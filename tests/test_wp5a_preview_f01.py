"""F-01: one uniform ``preview=true`` (alias ``dry_run``) on every live-TOM write (02 + 03).

The preview runs the REAL manager code with ``write_lane.commit`` suppressed (the image is captured
instead of ``SaveChanges``), diffs the before / after TMSL with ``journal_images`` and rolls the
in-memory edits back. Nothing is saved, the model generation does not move. The CLR seams
(``tmsl_of`` / ``committed_tmsl``) are replaced by an in-memory model; TOM is never touched.
"""

from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from core.autonomous.gating import invalidate_policy_cache
from core.operations import write_lane, write_preview
from server import op_classification as oc
from server.handlers import batch_operations_handler as boh
from server.handlers import model_operations_handler as moh

BASE: Dict[str, Any] = {
    "tables": [
        {
            "name": "Sales",
            "lineageTag": "t-sales",
            "columns": [{"name": "Amount", "dataType": "double", "lineageTag": "c-amount"}],
            "measures": [
                {"name": "Total", "expression": "SUM(Sales[Amount])", "lineageTag": "m-total"},
                {"name": "Count", "expression": "COUNTROWS(Sales)", "lineageTag": "m-count"},
            ],
        }
    ]
}


class FakeModel:
    """One client connection: a pending TMSL dict; SaveChanges must NEVER be called in a preview."""

    def __init__(self, live: "Live"):
        self.live = live
        self.tmsl = copy.deepcopy(live.tmsl)
        self.saved = 0
        self.undone = 0
        self.Database = None

    def SaveChanges(self):  # noqa: N802 - TOM name
        self.saved += 1
        self.live.saves += 1
        self.live.tmsl = copy.deepcopy(self.tmsl)

    def UndoLocalChanges(self):  # noqa: N802
        self.undone += 1
        self.tmsl = copy.deepcopy(self.live.tmsl)


class Live:
    def __init__(self):
        self.tmsl = copy.deepcopy(BASE)
        self.saves = 0


@pytest.fixture
def live(monkeypatch):
    lv = Live()
    models: List[FakeModel] = []
    lv.models = models

    def connect() -> FakeModel:
        m = FakeModel(lv)
        models.append(m)
        return m

    lv.connect = connect
    monkeypatch.setattr(write_preview, "tmsl_of", lambda model: copy.deepcopy(model.tmsl))
    monkeypatch.setattr(write_preview, "committed_tmsl", lambda model: copy.deepcopy(lv.tmsl))
    monkeypatch.setattr(write_preview, "_generation", lambda: 7)
    monkeypatch.setattr(write_preview, "_offline_before", lambda: None)
    # the journal / health / pacing never run for a suppressed commit: count the lookups (an apply
    # does consult the journal, a preview must not)
    lv.journal_lookups = 0

    def journal():
        lv.journal_lookups += 1
        return None

    monkeypatch.setattr(write_lane, "_journal", journal)
    return lv


@pytest.fixture(autouse=True)
def _gate_strict(monkeypatch):
    """STRICT: a gated write without confirm_destructive is blocked; a preview must still pass."""
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")
    invalidate_policy_cache()
    yield
    invalidate_policy_cache()


def _measure_update(live_, *, new_expr="SUM(Sales[Amount]) * 2", delete=False):
    """A stand-in manager: mutate the pending model, then ``write_lane.commit`` like every manager."""

    def run(args):
        model = live_.connect()
        tbl = model.tmsl["tables"][0]
        if delete:
            tbl["measures"] = [m for m in tbl["measures"] if m["name"] != args["measure_name"]]
        else:
            for m in tbl["measures"]:
                if m["name"] == args["measure_name"]:
                    m["expression"] = new_expr
        info = write_lane.commit(model, label="measure/update")
        return {"success": True, "action": "updated", **info}

    return run


def _install(monkeypatch, live_, runner):
    spies = {}
    for key in list(moh._handlers):
        m = MagicMock(name=key)
        m.execute.side_effect = runner
        spies[key] = m
        monkeypatch.setitem(moh._handlers, key, m)
    return spies


# ---------------------------------------------------------------------------
# the engine
# ---------------------------------------------------------------------------
def test_preview_never_saves_and_reports_before_after(live, monkeypatch):
    spies = _install(monkeypatch, live, _measure_update(live))
    out = moh.handle_model_operations(
        {
            "object_type": "measure",
            "operation": "update",
            "table_name": "Sales",
            "measure_name": "Total",
            "expression": "SUM(Sales[Amount]) * 2",
            "preview": True,
        }
    )
    assert out["success"] is True and out["preview"] is True and out["applied"] is False
    assert out["would_succeed"] is True and out["committed"] is False
    (change,) = out["would_change"]
    assert change["ref"] == "measure:Sales[Total]" and change["change"] == "modified"
    assert change["before"]["expression"] == "SUM(Sales[Amount])"
    assert change["after"]["expression"] == "SUM(Sales[Amount]) * 2"
    assert out["errors"] == [] and out["breaks"] == []
    assert out["plan_hash"] and out["model_generation"] == 7
    assert out["validation"]["dax_expressions"].startswith("not_validated")
    # nothing was saved, and the preview flags never reached the manager
    assert live.saves == 0 and all(m.saved == 0 for m in live.models)
    assert live.journal_lookups == 0
    sent = spies["measure"].execute.call_args[0][0]
    assert "preview" not in sent and "dry_run" not in sent
    # the in-memory edit was rolled back
    assert all(m.undone >= 1 for m in live.models)
    assert out["rolled_back"] is True


def test_the_gate_is_skipped_by_a_preview_but_not_by_the_apply(live, monkeypatch):
    _install(monkeypatch, live, _measure_update(live, delete=True))
    args = {
        "object_type": "measure",
        "operation": "delete",
        "table_name": "Sales",
        "measure_name": "Count",
    }
    blocked = moh.handle_model_operations(dict(args))  # STRICT, no confirm -> gate
    assert blocked["success"] is False and "confirm" in blocked["error"].lower()
    assert live.saves == 0
    out = moh.handle_model_operations({**args, "dry_run": True})  # alias
    assert out["success"] is True and out["preview"] is True
    assert [c["change"] for c in out["would_change"]] == ["removed"]


def test_a_removal_reports_the_dependents_that_would_break(live, monkeypatch):
    _install(monkeypatch, live, _measure_update(live, delete=True))
    from core.autonomous import evidence

    seen = {}

    def fake(refs, pbip_path=None):
        seen["refs"] = refs
        return {
            "proven": True,
            "blast_radius": {
                "impacts": {
                    refs[0]: {"breaks": ["measure:Sales[Margin]", "visual:R/p/v"], "safe": False}
                }
            },
        }

    monkeypatch.setattr(evidence, "compute_delete_evidence", fake)
    out = moh.handle_model_operations(
        {
            "object_type": "measure",
            "operation": "delete",
            "table_name": "Sales",
            "measure_name": "Total",
            "preview": True,
        }
    )
    assert seen["refs"] == ["measure:Sales[Total]"]
    assert out["breaks"] == [
        {
            "object": "measure:Sales[Total]",
            "breaks": ["measure:Sales[Margin]", "visual:R/p/v"],
            "unknown_nodes": [],
        }
    ]
    assert out["breaks_status"] == "checked"


def test_breaks_are_unavailable_not_silently_empty_without_a_graph(live, monkeypatch):
    _install(monkeypatch, live, _measure_update(live, delete=True))
    from core.autonomous import evidence

    monkeypatch.setattr(evidence, "compute_delete_evidence", lambda refs, pbip_path=None: None)
    out = moh.handle_model_operations(
        {
            "object_type": "measure",
            "operation": "delete",
            "table_name": "Sales",
            "measure_name": "Total",
            "preview": True,
        }
    )
    assert out["breaks"] == [] and out["breaks_status"] == "unavailable"
    assert out["removed"] == ["measure:Sales[Total]"]


def test_the_managers_own_refusal_is_reported_as_errors(live, monkeypatch):
    def failing(args):
        live.connect()
        return {
            "success": False,
            "error_type": "name_conflict",
            "error": "Measure 'X' already exists",
        }

    _install(monkeypatch, live, failing)
    out = moh.handle_model_operations(
        {
            "object_type": "measure",
            "operation": "create",
            "table_name": "Sales",
            "measure_name": "X",
            "expression": "1",
            "preview": True,
        }
    )
    assert out["success"] is False and out["would_succeed"] is False
    assert out["error_type"] == "name_conflict"
    assert out["errors"][0]["error_type"] == "name_conflict"
    assert out["would_change"] == [] and "plan_hash" not in out


def test_a_manager_exception_is_a_would_be_failure_not_a_crash(live, monkeypatch):
    def boom(args):
        live.connect()
        raise RuntimeError("engine said no")

    _install(monkeypatch, live, boom)
    out = moh.handle_model_operations(
        {
            "object_type": "measure",
            "operation": "update",
            "table_name": "Sales",
            "measure_name": "Total",
            "expression": "x",
            "preview": True,
        }
    )
    assert out["success"] is False and out["error_type"] == "preview_failed"
    assert "engine said no" in out["errors"][0]["error"]


def test_upsert_preview_follows_the_create_to_update_retry(live, monkeypatch):
    calls = []

    def runner(args):
        calls.append(args["operation"])
        model = live.connect()
        if args["operation"] == "create":
            return {"success": False, "error": "Measure 'Total' already exists in table 'Sales'"}
        model.tmsl["tables"][0]["measures"][0]["expression"] = "1"
        return {"success": True, **write_lane.commit(model, label="measure/update")}

    _install(monkeypatch, live, runner)
    out = moh.handle_model_operations(
        {
            "object_type": "measure",
            "operation": "upsert",
            "table_name": "Sales",
            "measure_name": "Total",
            "expression": "1",
            "preview": True,
        }
    )
    assert calls == ["create", "update"]
    assert out["success"] is True and out["result"]["upserted"] is True
    assert out["would_change"][0]["change"] == "modified"


# ---------------------------------------------------------------------------
# refresh = a plan
# ---------------------------------------------------------------------------
def test_refresh_preview_is_a_plan_and_never_reaches_the_manager(live, monkeypatch):
    spies = _install(monkeypatch, live, _measure_update(live))
    monkeypatch.setattr(
        write_preview,
        "_model_tables",
        lambda: [{"name": "Sales", "partitions": ["Sales"]}, {"name": "Date", "partitions": ["D"]}],
    )
    out = moh.handle_model_operations(
        {"object_type": "model", "operation": "refresh", "preview": True}
    )
    assert out["success"] is True and out["would_refresh"]["scope"] == "model"
    assert out["would_refresh"]["table_count"] == 2 and out["plan_hash"]
    spies["model"].refresh.assert_not_called()
    out = moh.handle_model_operations(
        {"object_type": "table", "operation": "refresh", "table_name": "Nope", "preview": True}
    )
    assert out["success"] is False and out["error_type"] == "table_not_found"


# ---------------------------------------------------------------------------
# plan_hash staleness
# ---------------------------------------------------------------------------
def test_apply_with_plan_hash_runs_and_a_moved_model_refuses(live, monkeypatch):
    spies = _install(monkeypatch, live, _measure_update(live))
    args = {
        "object_type": "measure",
        "operation": "update",
        "table_name": "Sales",
        "measure_name": "Total",
        "expression": "2",
        "confirm_destructive": True,
    }
    preview = moh.handle_model_operations({**args, "preview": True})
    h = preview["plan_hash"]
    ok = moh.handle_model_operations({**args, "plan_hash": h})
    assert ok["success"] is True and ok["plan_hash_verified"] is True
    sent = spies["measure"].execute.call_args[0][0]
    assert "plan_hash" not in sent
    # the model moved (any write bumps the generation) -> stale
    monkeypatch.setattr(write_preview, "_generation", lambda: 8)
    stale = moh.handle_model_operations({**args, "plan_hash": h})
    assert stale["success"] is False and stale["error_type"] == "stale_preview"
    assert stale["executed"] is False and stale["current_plan_hash"] != h
    # different arguments are stale too
    monkeypatch.setattr(write_preview, "_generation", lambda: 7)
    other = moh.handle_model_operations({**args, "expression": "3", "plan_hash": h})
    assert other["error_type"] == "stale_preview"


def test_plan_hash_ignores_the_flags_that_do_not_change_the_call():
    a = {"object_type": "measure", "operation": "update", "measure_name": "X", "preview": True}
    b = {
        "object_type": "measure",
        "operation": "update",
        "measure_name": "X",
        "confirm_destructive": True,
        "request_id": "r",
        "plan_hash": "zzz",
    }
    assert write_preview.plan_hash("02_Model_Operations", a, 3) == write_preview.plan_hash(
        "02_Model_Operations", b, 3
    )
    assert write_preview.plan_hash("02_Model_Operations", a, 3) != write_preview.plan_hash(
        "02_Model_Operations", a, 4
    )
    assert write_preview.plan_hash("02_Model_Operations", a, 3) != write_preview.plan_hash(
        "03_Batch_Operations", a, 3
    )


# ---------------------------------------------------------------------------
# a preview is never a real write (the invariant behind classifying it READ)
# ---------------------------------------------------------------------------
def _write_ops():
    for object_type, ops in moh._valid_operations.items():
        for op in ops:
            if op in moh._READ_ONLY_OPS:
                continue
            yield object_type, op


def test_no_write_op_executes_for_real_under_preview(live, monkeypatch):
    """Every non-read (object_type, operation) with preview=true goes to run_preview, the refresh
    plan or an existing dry-run (preview/dry_run True): the real execute is never reached."""
    reached: List[Dict[str, Any]] = []

    def original(args):
        reached.append(dict(args))
        return {"success": True, "via": "existing_dry_run"}

    monkeypatch.setattr(moh, "_original_handle_model_operations", original)
    monkeypatch.setattr(
        write_preview,
        "run_preview",
        lambda tool, label, args, fn: {"success": True, "via": "run_preview"},
    )
    monkeypatch.setattr(
        write_preview,
        "refresh_plan",
        lambda tool, label, args: {"success": True, "via": "refresh_plan"},
    )
    base = {
        "table_name": "Sales",
        "measure_name": "Total",
        "column_name": "Amount",
        "new_name": "N",
        "group_name": "G",
        "role_name": "R",
        "perspective_name": "P",
    }
    seen = set()
    for object_type, op in _write_ops():
        out = moh.handle_model_operations(
            {**base, "object_type": object_type, "operation": op, "preview": True}
        )
        seen.add((object_type, op, out.get("via")))
        assert out.get("via") in {"run_preview", "refresh_plan", "existing_dry_run"}, (
            object_type,
            op,
            out,
        )
    for call in reached:
        assert call.get("preview") is True or call.get("dry_run") is True, call
    # the three refresh ops are plans; renames reuse their cascade preview
    assert ("model", "refresh", "refresh_plan") in seen and (
        "table",
        "refresh",
        "refresh_plan",
    ) in seen
    assert ("measure", "rename", "existing_dry_run") in seen
    assert ("measure", "safe_delete", "run_preview") in seen  # composite -> the raw delete preview
    assert ("any", "set_property", "existing_dry_run") in seen


def test_read_ops_ignore_preview(live, monkeypatch):
    _install(monkeypatch, live, lambda args: {"success": True, "rows": []})
    out = moh.handle_model_operations(
        {"object_type": "measure", "operation": "list", "table_name": "Sales", "preview": True}
    )
    assert out == {"success": True, "rows": []}


def test_classification_a_preview_is_a_read_but_runs_exclusively():
    for object_type, op in _write_ops():
        args = {"object_type": object_type, "operation": op, "preview": True}
        assert oc.classify("02_Model_Operations", args) == "read", (object_type, op)
        assert oc.is_model_preview("02_Model_Operations", args), (object_type, op)
        real = {"object_type": object_type, "operation": op}
        assert oc.classify("02_Model_Operations", real) != "read" or op in oc._MODEL_READ_OPS
    # reads stay shared reads; a bare apply stays a write
    assert not oc.is_model_preview(
        "02_Model_Operations", {"object_type": "measure", "operation": "list", "preview": True}
    )
    assert not oc.is_model_preview(
        "02_Model_Operations", {"object_type": "measure", "operation": "update"}
    )
    assert (
        oc.classify(
            "03_Batch_Operations",
            {"object_type": "measures", "batch_operation": "delete", "preview": True},
        )
        == "read"
    )
    assert (
        oc.classify(
            "03_Batch_Operations",
            {"object_type": "measures", "batch_operation": "delete", "dry_run": True},
        )
        == "read"
    )
    assert oc.is_model_preview(
        "03_Batch_Operations",
        {"object_type": "measures", "batch_operation": "update", "preview": True},
    )
    assert (
        oc.classify("03_Batch_Operations", {"object_type": "measures", "batch_operation": "delete"})
        == "destructive"
    )


# ---------------------------------------------------------------------------
# 03 batch
# ---------------------------------------------------------------------------
def test_batch_preview_runs_the_real_batch_without_saving(live, monkeypatch):
    seen = {}

    def execute(args):
        seen.update(args)
        model = live.connect()
        for it in args["items"]:
            for m in model.tmsl["tables"][0]["measures"]:
                if m["name"] == it["measure_name"]:
                    m["displayFolder"] = it["display_folder"]
        return {
            "success": True,
            "committed": True,
            **write_lane.commit(model, label="batch/update", in_loop=True),
        }

    monkeypatch.setattr(boh._batch_ops_handler, "execute", execute)
    args = {
        "object_type": "measures",
        "batch_operation": "update",
        "items": [
            {"table_name": "Sales", "measure_name": "Total", "display_folder": "KPI"},
            {"table_name": "Sales", "measure_name": "Count", "display_folder": "KPI"},
        ],
    }
    out = boh.handle_batch_operations({**args, "preview": True})
    assert out["success"] is True and out["preview"] is True and out["committed"] is False
    assert sorted(c["ref"] for c in out["would_change"]) == [
        "measure:Sales[Count]",
        "measure:Sales[Total]",
    ]
    assert out["plan_hash"] and live.saves == 0
    assert "preview" not in seen
    # the apply: same args + plan_hash passes while the model generation holds, then goes stale
    ok = boh.handle_batch_operations(
        {**args, "plan_hash": out["plan_hash"], "confirm_destructive": True}
    )
    assert ok["success"] is True and ok["plan_hash_verified"] is True
    monkeypatch.setattr(write_preview, "_generation", lambda: 99)
    stale = boh.handle_batch_operations({**args, "plan_hash": out["plan_hash"]})
    assert stale["error_type"] == "stale_preview"


def test_batch_preview_keeps_the_per_type_cap_and_skips_the_gate(live, monkeypatch):
    monkeypatch.setattr(
        boh._batch_ops_handler, "execute", lambda a: pytest.fail("real execute ran")
    )
    monkeypatch.setattr(
        write_preview, "run_preview", lambda *a, **k: {"success": True, "via": "run_preview"}
    )
    items = [{"table_name": f"t{i}"} for i in range(31)]
    over = boh.handle_batch_operations(
        {"object_type": "tables", "batch_operation": "delete", "items": items, "preview": True}
    )
    assert over["error_type"] == "items_exceeds_cap"
    ok = boh.handle_batch_operations(
        {"object_type": "tables", "batch_operation": "delete", "items": items[:2], "dry_run": True}
    )
    assert ok["via"] == "run_preview"  # a CATASTROPHIC delete is not gated when only previewed


def test_changeset_and_patch_keep_their_own_preview(monkeypatch):
    seen = []
    monkeypatch.setattr(
        boh._batch_ops_handler,
        "apply_changeset",
        lambda a: seen.append(a) or {"success": True, "plan_hash": "cs"},
    )
    out = boh.handle_batch_operations(
        {
            "object_type": "changeset",
            "preview": True,
            "changeset": {"tables": {"create": [{"name": "T"}]}},
        }
    )
    assert out["plan_hash"] == "cs" and seen


# ---------------------------------------------------------------------------
# lane: commit suppressed on the offline lane too
# ---------------------------------------------------------------------------
def test_commit_in_a_preview_scope_skips_savechanges_journal_and_offline_persist(monkeypatch):
    from core.infrastructure import offline_model

    monkeypatch.setattr(offline_model, "persist", lambda label: pytest.fail("offline persist ran"))
    monkeypatch.setattr(write_lane, "_journal", lambda: pytest.fail("journal consulted"))
    model = SimpleNamespace(
        SaveChanges=lambda: pytest.fail("SaveChanges ran"), tmsl=copy.deepcopy(BASE)
    )
    cap = write_lane.PreviewCapture(
        serialize=lambda m: copy.deepcopy(m.tmsl), committed=lambda m: copy.deepcopy(BASE)
    )
    saves_before = write_lane.get_write_lane().saves
    with write_lane.preview_scope(cap):
        assert write_lane.preview_active()
        model.tmsl["tables"][0]["measures"].append({"name": "New", "expression": "1"})
        info = write_lane.commit(model, label="measure/create")
        info2 = write_lane.get_write_lane().commit(model, label="measure/create", in_loop=True)
    assert not write_lane.preview_active()
    assert info == {"preview_commit_suppressed": True} == info2
    assert cap.commits == 2 and write_lane.get_write_lane().saves == saves_before
    assert cap.after is not None and "measure:Sales[New]" in cap.after


def test_offline_lane_preview_rereads_the_folder_and_never_persists(monkeypatch):
    from core.infrastructure import offline_model

    state = {"dirty": False, "rolled": 0}
    db = SimpleNamespace(Model=SimpleNamespace(tmsl=copy.deepcopy(BASE)))
    monkeypatch.setattr(offline_model, "is_active", lambda: True)
    monkeypatch.setattr(offline_model, "database", lambda: db)
    monkeypatch.setattr(
        offline_model, "info", lambda: {"pending_unpersisted_changes": state["dirty"]}
    )
    monkeypatch.setattr(offline_model, "persist", lambda label: pytest.fail("offline persist ran"))

    def rollback():
        state["rolled"] += 1
        db.Model.tmsl = copy.deepcopy(BASE)
        state["dirty"] = False

    monkeypatch.setattr(offline_model, "rollback", rollback)
    monkeypatch.setattr(write_preview, "tmsl_of", lambda model: copy.deepcopy(model.tmsl))
    monkeypatch.setattr(write_preview, "_generation", lambda: 1)

    def fn():
        state["dirty"] = True
        db.Model.tmsl["tables"][0]["measures"].append({"name": "Offline", "expression": "1"})
        write_lane.commit(db.Model, label="measure/create")
        return {"success": True}

    out = write_preview.run_preview(
        "02_Model_Operations", "02_Model_Operations:measure/create", {"a": 1}, fn
    )
    assert out["success"] is True
    assert [(c["ref"], c["change"]) for c in out["would_change"]] == [
        ("measure:Sales[Offline]", "added")
    ]
    assert state["rolled"] == 1 and out["rolled_back"] is True
    assert out["breaks_status"] == "nothing_removed"
    assert db.Model.tmsl == BASE  # the folder-backed model was re-read, the edit is gone


def test_offline_removal_reports_breaks_unavailable(monkeypatch):
    from core.infrastructure import offline_model

    monkeypatch.setattr(offline_model, "is_active", lambda: True)
    monkeypatch.setattr(offline_model, "info", lambda: {"pending_unpersisted_changes": False})
    monkeypatch.setattr(offline_model, "database", lambda: None)
    monkeypatch.setattr(offline_model, "rollback", lambda: None)
    out = write_preview._breaks(
        [
            {
                "ref": "measure:Sales[Total]",
                "kind": "measure",
                "table": "Sales",
                "name": "Total",
                "change": "removed",
            }
        ],
        {},
    )
    assert out["breaks_status"] == "unavailable_offline" and out["breaks"] == []


def test_removing_a_table_does_not_list_its_own_children():
    changes = [
        {"kind": "table", "name": "Sales", "table": None, "change": "removed"},
        {"kind": "column", "name": "Amount", "table": "Sales", "change": "removed"},
        {"kind": "measure", "name": "Total", "table": "Sales", "change": "removed"},
        {"kind": "measure", "name": "Keep", "table": "Other", "change": "removed"},
    ]
    assert write_preview._removed_refs(changes) == ["table:Sales", "measure:Other[Keep]"]


# ---------------------------------------------------------------------------
# dispatcher: a preview is a READ that still runs exclusively
# ---------------------------------------------------------------------------
def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_dispatcher_runs_a_preview_in_read_only_mode_without_bumping_the_generation(monkeypatch):
    from server import dispatch as dispatch_mod
    from server.dispatch import ToolDispatcher
    from server.registry import HandlerRegistry, ToolDefinition

    bumps, notes = [], []
    monkeypatch.setattr(dispatch_mod, "_bump_model_generation", lambda label: bumps.append(label))
    monkeypatch.setattr(dispatch_mod, "_note_edit_ledger", lambda label, args: notes.append(label))
    scopes = []
    real_scope = dispatch_mod._offline_scope

    def scope(*, write):
        scopes.append(write)
        return real_scope(write=write)

    monkeypatch.setattr(dispatch_mod, "_offline_scope", scope)
    reg = HandlerRegistry()
    reg.register(
        ToolDefinition(
            name="02_Model_Operations",
            description="t",
            handler=lambda a: {"success": True, "n": 1},
            input_schema={
                "type": "object",
                "properties": {"object_type": {}, "operation": {}, "preview": {}, "table_name": {}},
            },
            category="model",
        )
    )
    d = ToolDispatcher()
    d.registry = reg
    d._read_only = True
    preview = {"object_type": "table", "operation": "delete", "table_name": "T", "preview": True}
    out = d.dispatch("02_Model_Operations", preview)
    assert out["success"] is True and bumps == [] and notes == []
    assert scopes == [True]  # exclusive (write scope) although it is a read
    blocked = d.dispatch(
        "02_Model_Operations", {k: v for k, v in preview.items() if k != "preview"}
    )
    assert blocked["error_type"] == "read_only_mode"
