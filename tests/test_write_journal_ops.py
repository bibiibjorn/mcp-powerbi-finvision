"""JR-01: ``02_Model_Operations object_type=model`` history / diff / undo / replay / checkpoints.

The whole record -> undo -> replay -> checkpoint flow runs against ``tests/_journal_fakes.py``
(pure Python; the real TOM applier has its own file). Op classification + schema wiring included.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from core.operations import journal_manager, write_journal
from core.operations.write_lane import commit
from server.handlers import model_operations_handler as h
from server.op_classification import DESTRUCTIVE, READ, WRITE, classify, classify_known
from tests import _journal_fakes as jf


@pytest.fixture
def live(monkeypatch, tmp_path):
    write_journal.configure(enabled=True, directory=tmp_path / "journal")
    lv = jf.LiveModel()
    jf.install(monkeypatch, lv)
    return lv


def op(operation, **kw):
    return h.handle_model_operations({"object_type": "model", "operation": operation, **kw})


def write(live, mutate, label="measure/update", tool_label=None):
    tom = live.connect()
    mutate(tom)
    with write_journal.call_scope(
        "02_Model_Operations", tool_label or f"02_Model_Operations:{label}", {"label": label}
    ):
        return commit(tom, label=label)


def set_total(expr):
    return lambda tom: tom.measure("Sales", "Total").__setitem__("expression", expr)


ORIGINAL_TOTAL = "SUM(Sales[Amount])"


# ---------------------------------------------------------------------------
# op classification / registration
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "operation, args, expected",
    [
        ("history", {}, READ),
        ("diff", {}, READ),
        ("checkpoint_create", {}, READ),
        ("undo", {}, DESTRUCTIVE),
        ("undo", {"preview": True}, READ),
        ("checkpoint_restore", {}, DESTRUCTIVE),
        ("checkpoint_restore", {"preview": True}, READ),
        ("replay", {}, WRITE),
        ("replay", {"preview": True}, READ),
    ],
)
def test_new_ops_are_classified_explicitly(operation, args, expected):
    call = {"object_type": "model", "operation": operation, **args}
    assert classify_known("02_Model_Operations", call) == expected
    assert classify("02_Model_Operations", call) == expected


def test_new_ops_are_registered_on_the_model_object_type():
    assert set(journal_manager.JOURNAL_OPERATIONS) <= set(
        h._valid_operations["model"]
    )  # noqa: SLF001
    from server.tool_surface import deferred_args

    assert {
        "entry_id",
        "entry_ids",
        "count",
        "since",
        "unflushed_only",
        "journal",
        "checkpoint",
    } <= deferred_args("02_Model_Operations")


def test_undo_preview_is_not_gated_but_apply_is_destructive(live):
    assert h._severity_for("model", "undo", {"preview": True}) is None  # noqa: SLF001
    assert h._severity_for("model", "undo", {}) is not None  # noqa: SLF001
    assert h._severity_for("model", "history", {}) is None  # noqa: SLF001


# ---------------------------------------------------------------------------
# history / diff
# ---------------------------------------------------------------------------
def test_history_lists_newest_first_with_filters(live):
    write(live, set_total("A"))
    write(
        live,
        lambda t: t.table("Sales")["measures"].append({"name": "Extra", "expression": "1"}),
        "measure/create",
    )
    write(live, set_total("B"))
    out = op("history")
    assert out["success"] is True and out["total_entries"] == 3
    assert [e["id"] for e in out["entries"]] == ["J000003", "J000002", "J000001"]
    assert out["journal_unflushed"] == 3 and out["entries"][0]["operation"] == "measure/update"
    assert [e["id"] for e in op("history", object="extra")["entries"]] == ["J000002"]
    assert (
        len(op("history", limit=2)["entries"]) == 2 and op("history", limit=2)["truncated"] is True
    )
    assert op("history", since="1h")["matching"] == 3
    assert op("history", since="2000-01-01T00:00:00Z")["matching"] == 3
    assert op("history", since="bogus")["error_type"] == "invalid_parameters"
    write_journal.mark_flushed(jf.REPORTS, jf.MODELS)
    assert op("history", unflushed_only=True)["entries"] == []
    write(live, set_total("C"))
    assert [e["id"] for e in op("history", unflushed_only=True)["entries"]] == ["J000004"]


def test_history_is_available_without_a_live_connection(live, monkeypatch):
    write(live, set_total("A"))
    monkeypatch.setattr(write_journal, "_connected_project", lambda: None)  # not connected
    out = op("history")
    assert out["success"] is True and out["note"] and out["entries"][0]["id"] == "J000001"


def test_diff_shows_pre_vs_post_readably(live):
    write(live, set_total("SUM(Sales[Amount])\n * 2"))
    out = op("diff", entry_id="J000001")
    assert out["success"] is True
    (ch,) = out["changes"]
    assert ch["ref"] == "measure:Sales[Total]" and ch["change"] == "modified"
    assert ch["properties"]["expression"]["pre"] == ORIGINAL_TOTAL
    assert "diff" in ch["properties"]["expression"] and ch["properties"]["expression"]["diff"]
    assert op("diff")["error_type"] == "missing_argument"
    assert op("diff", entry_id="J999999")["error_type"] == "not_found"


# ---------------------------------------------------------------------------
# undo
# ---------------------------------------------------------------------------
def test_undo_restores_the_exact_preimage_in_one_save(live):
    write(
        live,
        lambda t: t.measure("Sales", "Total").update(
            expression="X", formatString="0", displayFolder="Z"
        ),
    )
    saves = live.saves
    original = copy.deepcopy(jf.BASE_TMSL["tables"][0]["measures"][0])
    out = op("undo")
    assert out["success"] is True and out["applied"] == 1
    assert live.saves == saves + 1, "ONE SaveChanges"
    restored = next(m for m in live.tmsl["tables"][0]["measures"] if m["name"] == "Total")
    assert restored == original, "DAX, format string and folder are identical to the pre-image"
    # the undo is itself a journal entry that names what it reverted
    entry = write_journal.read_journal(write_journal.resolve_key(jf.PROJECT, "db1"))["entries"][-1]
    assert entry["id"] == out["journal_entry"] and entry["meta"]["op"] == "undo"
    assert entry["meta"]["reverts"] == ["J000001"]


def test_undo_last_n_reverts_in_reverse_order(live):
    write(live, set_total("A"))
    write(live, set_total("B"))
    write(live, set_total("C"))
    out = op("undo", count=2)
    assert out["success"] is True
    assert jf.measure_expr(live, "Sales", "Total") == "A"
    # the entries just reverted are skipped next time: undo goes one further back
    assert op("undo")["success"] is True
    assert jf.measure_expr(live, "Sales", "Total") == ORIGINAL_TOTAL
    assert op("undo")["error_type"] == "nothing_to_undo"


def test_undo_reverts_added_and_removed_objects(live):
    def mutate(t):
        t.table("Sales")["measures"] = [
            m for m in t.table("Sales")["measures"] if m["name"] != "Count"
        ]
        t.table("Sales")["measures"].append({"name": "Fresh", "expression": "1"})

    write(live, mutate, "batch")
    out = op("undo")
    assert out["success"] is True
    assert jf.measure_names(live) == ["Count", "Total"]
    assert jf.measure_expr(live, "Sales", "Count") == "COUNTROWS(Sales)"


def test_undo_preview_changes_nothing(live):
    write(live, set_total("A"))
    saves = live.saves
    out = op("undo", preview=True)
    assert out["success"] is True and out["preview"] is True and out["can_apply"] is True
    assert (
        out["changes"][0]["ref"] == "measure:Sales[Total]"
        and out["changes"][0]["action"] == "update"
    )
    assert live.saves == saves and jf.measure_expr(live, "Sales", "Total") == "A"


def test_undo_refuses_when_the_object_changed_after_the_entry(live):
    write(live, set_total("A"))
    write(live, set_total("B"))
    saves = live.saves
    out = op("undo", entry_id="J000001")
    assert out["success"] is False and out["error_type"] == "journal_conflict"
    assert out["conflicts"][0]["ref"] == "measure:Sales[Total]"
    assert live.saves == saves and jf.measure_expr(live, "Sales", "Total") == "B", "nothing changed"
    prev = op("undo", entry_id="J000001", preview=True)
    assert prev["success"] is True and prev["can_apply"] is False and prev["conflict_count"] == 1


def test_undo_force_needs_confirm_destructive(live):
    write(live, set_total("A"))
    write(live, set_total("B"))
    refused = op("undo", entry_id="J000001", force=True)
    assert refused["error_type"] == "confirmation_required"
    assert jf.measure_expr(live, "Sales", "Total") == "B"
    ok = op("undo", entry_id="J000001", force=True, confirm_destructive=True)
    assert ok["success"] is True and jf.measure_expr(live, "Sales", "Total") == ORIGINAL_TOTAL


def test_undo_of_a_created_table_needs_confirmation(live):
    def mutate(t):
        t.tmsl["tables"].append(
            {
                "name": "Fresh",
                "lineageTag": "tag-fresh",
                "columns": [{"name": "Id", "dataType": "int64", "sourceColumn": "Id"}],
                "measures": [{"name": "M", "expression": "1"}],
                "partitions": [{"name": "Fresh", "source": {"type": "m", "expression": "x"}}],
            }
        )

    write(live, mutate, "table/create")
    refused = op("undo")
    assert refused["error_type"] == "confirmation_required" and "table:Fresh" in refused["error"]
    assert [t["name"] for t in live.tmsl["tables"]] == ["Sales", "Fresh"]
    ok = op("undo", confirm_destructive=True)
    assert ok["success"] is True and [t["name"] for t in live.tmsl["tables"]] == ["Sales"]


def test_undo_of_a_deleted_table_recreates_it_and_flags_a_refresh(live):
    tables = copy.deepcopy(jf.BASE_TMSL["tables"][0])
    write(live, lambda t: t.tmsl["tables"].clear(), "table/delete")
    prev = op("undo", preview=True)
    assert "table:Sales" in prev["needs_refresh"]
    out = op("undo")
    assert out["success"] is True
    assert jf.canon(live.tmsl["tables"][0]) == jf.canon(tables)


def test_undo_reverses_a_rename_in_place(live):
    def mutate(t):
        next(c for c in t.table("Sales")["columns"] if c["name"] == "Amount")["name"] = "Amount2"

    write(live, mutate, "column/rename")
    assert op("undo")["success"] is True
    assert sorted(c["name"] for c in live.tmsl["tables"][0]["columns"]) == ["Amount", "Qty"]


def test_undo_refuses_a_degraded_entry(live, monkeypatch):
    monkeypatch.setattr(
        write_journal, "fetch_committed", lambda m: (_ for _ in ()).throw(RuntimeError("x"))
    )
    write(live, set_total("A"))
    out = op("undo", entry_id="J000001")
    assert out["error_type"] == "not_undoable"
    assert op("undo")["error_type"] == "nothing_to_undo"


def test_undo_apply_failure_rolls_back_without_saving(live, monkeypatch):
    from core.operations import journal_apply

    write(live, set_total("A"))
    saves = live.saves

    def boom(model, ops):
        raise journal_apply.JournalApplyError("measure:Sales[Total]: cannot set 'x'")

    monkeypatch.setattr(journal_apply, "apply_ops", boom)
    out = op("undo")
    assert out["error_type"] == "journal_apply_failed" and "Nothing was saved" in out["error"]
    assert live.saves == saves


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------
def _discard(live):
    """A reload that re-read disk: the live model is back to the last saved state."""
    live.tmsl = copy.deepcopy(jf.BASE_TMSL)
    live.stamp += 1


def test_replay_reapplies_unflushed_writes_after_a_discarded_reload(live):
    write(live, set_total("A"))
    write(
        live,
        lambda t: t.table("Sales")["measures"].append({"name": "Extra", "expression": "1"}),
        "measure/create",
    )
    write(live, set_total("B"))
    after_writes = copy.deepcopy(live.tmsl)
    _discard(live)
    assert jf.measure_expr(live, "Sales", "Total") == ORIGINAL_TOTAL

    prev = op("replay", preview=True)
    assert prev["success"] is True and prev["preview"] is True and prev["change_count"] == 3
    assert jf.measure_expr(live, "Sales", "Total") == ORIGINAL_TOTAL, "preview writes nothing"

    saves = live.saves
    out = op("replay")
    assert out["success"] is True and out["applied"] == 3 and live.saves == saves + 1
    assert jf.canon(live.tmsl) == jf.canon(after_writes), "the discarded edits are back, exactly"
    # replaying again finds everything already applied
    again = op("replay")
    assert again["applied"] == 0 and again["skipped_count"] >= 2


def test_replay_skips_entries_already_matching_live_and_flushed_ones(live):
    write(live, set_total("A"))
    write_journal.mark_flushed(jf.REPORTS, jf.MODELS)  # A reached disk
    write(live, set_total("B"))
    _discard(live)
    live.tmsl["tables"][0]["measures"][0]["expression"] = "A"  # disk has A (the flushed write)
    out = op("replay")
    assert out["success"] is True and out["applied"] == 1
    assert jf.measure_expr(live, "Sales", "Total") == "B"
    assert op("replay", entry_ids=["J000001"])["message"].startswith("Nothing to replay")


def test_replay_reports_conflicts_and_applies_the_rest(live):
    write(live, set_total("A"))
    write(
        live,
        lambda t: t.table("Sales")["measures"].append({"name": "Extra", "expression": "1"}),
        "measure/create",
    )
    _discard(live)
    live.tmsl["tables"][0]["measures"][0]["expression"] = "edited elsewhere"
    out = op("replay")
    assert out["success"] is True and out["applied"] == 1
    assert out["conflict_count"] == 1 and out["conflicts"][0]["ref"] == "measure:Sales[Total]"
    assert jf.measure_expr(live, "Sales", "Total") == "edited elsewhere", "conflict left alone"
    assert "Extra" in jf.measure_names(live)
    forced = op("replay", force=True, confirm_destructive=True)
    assert forced["success"] is True and jf.measure_expr(live, "Sales", "Total") == "A"


def test_replay_with_nothing_unflushed(live):
    assert op("replay")["message"].startswith("Nothing to replay")


def test_replay_is_journaled_and_not_picked_by_undo_count(live):
    write(live, set_total("A"))
    _discard(live)
    out = op("replay")
    entry = write_journal.read_journal(write_journal.resolve_key(jf.PROJECT, "db1"))["entries"][-1]
    assert (
        entry["meta"] == {"op": "replay", "replays": ["J000001"]}
        and entry["id"] == out["journal_entry"]
    )
    # "undo the last write" still means the original edit, not the replay bookkeeping
    assert op("undo")["success"] is True
    assert jf.measure_expr(live, "Sales", "Total") == ORIGINAL_TOTAL


# ---------------------------------------------------------------------------
# checkpoints
# ---------------------------------------------------------------------------
def test_checkpoint_round_trip(live, tmp_path):
    created = op("checkpoint_create", checkpoint="before refactor")
    assert created["success"] is True and created["objects"]["measure"] == 2
    path = Path(created["path"])
    assert path == tmp_path / "journal" / "checkpoints" / "before refactor.json" and path.exists()
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["model"]["tables"][0]["name"] == "Sales" and doc["name"] == "before refactor"

    def wreck(t):
        t.measure("Sales", "Total")["expression"] = "WRECKED"
        t.table("Sales")["measures"].append({"name": "Junk", "expression": "1"})
        t.table("Sales")["measures"] = [
            m for m in t.table("Sales")["measures"] if m["name"] != "Count"
        ]
        t.tmsl["culture"] = "nl-BE"

    write(live, wreck, "batch")
    assert op("history")["checkpoints"] == ["before refactor"]

    prev = op("checkpoint_restore", checkpoint="before refactor", preview=True)
    assert prev["success"] is True and prev["preview"] is True
    assert prev["counts"] == {"update": 2, "add": 1, "remove": 1}
    assert jf.measure_expr(live, "Sales", "Total") == "WRECKED"

    saves = live.saves
    out = op("checkpoint_restore", checkpoint="before refactor")
    assert out["success"] is True and live.saves == saves + 1, "ONE SaveChanges"
    assert jf.canon(live.tmsl) == jf.canon(jf.BASE_TMSL)
    entry = write_journal.read_journal(write_journal.resolve_key(jf.PROJECT, "db1"))["entries"][-1]
    assert entry["meta"]["op"] == "checkpoint_restore"
    # restoring an identical model is a no-op
    assert op("checkpoint_restore", checkpoint="before refactor")["applied"] == 0


def test_checkpoint_restore_of_an_added_table_needs_confirmation(live):
    op("checkpoint_create", checkpoint="base")
    write(
        live,
        lambda t: t.tmsl["tables"].append(
            {
                "name": "Later",
                "lineageTag": "tag-later",
                "columns": [{"name": "Id", "dataType": "int64"}],
            }
        ),
        "table/create",
    )
    refused = op("checkpoint_restore", checkpoint="base")
    assert refused["error_type"] == "confirmation_required"
    assert op("checkpoint_restore", checkpoint="base", confirm_destructive=True)["success"] is True
    assert [t["name"] for t in live.tmsl["tables"]] == ["Sales"]


def test_checkpoint_name_rules_and_overwrite(live):
    assert op("checkpoint_create")["error_type"] == "invalid_parameters"
    assert op("checkpoint_create", checkpoint="../evil")["error_type"] == "invalid_parameters"
    assert op("checkpoint_create", checkpoint="one")["success"] is True
    assert op("checkpoint_create", checkpoint="one")["error_type"] == "already_exists"
    assert op("checkpoint_create", checkpoint="one", force=True)["success"] is True
    assert op("checkpoint_restore", checkpoint="missing")["error_type"] == "not_found"


def test_checkpoint_never_deletes_udfs_it_did_not_capture(live, monkeypatch):
    """A checkpoint made while the UDF read failed must not wipe the model's UDFs on restore."""
    op("checkpoint_create", checkpoint="nofn")  # _function_images -> None in the fake
    fn = {
        "kind": "function",
        "table": None,
        "name": "F",
        "json": {"name": "F", "expression": "() => 1"},
    }
    monkeypatch.setattr(
        journal_manager.WriteJournalManager,
        "_function_images",
        staticmethod(lambda: {"function:F": fn}),
    )
    out = op("checkpoint_restore", checkpoint="nofn", preview=True)
    assert out["success"] is True and out["change_count"] == 0
