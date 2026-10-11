"""Deep review 2026-10-03, slice N: write-journal property merge + project guard.

N-05 (replay treats a property removal as "already applied"), N-06 (undo clobbers newer edits to
other properties of the same object), N-07 (undo / replay / checkpoint_restore ran another
project's journal), N-22 (restore gates data-bearing removals), plus the pure merge helper.
Pure Python: synthetic image maps and ``tests/_journal_fakes.py``.
"""

from __future__ import annotations

import copy

import pytest

from core.operations import journal_images as images
from core.operations import journal_manager, journal_plan as plan_mod, write_journal
from core.operations.write_lane import commit
from server.handlers import model_operations_handler as h
from tests import _journal_fakes as jf


def _model(*measures, columns=()):
    return {
        "name": "M",
        "tables": [
            {
                "name": "T",
                "lineageTag": "t",
                "columns": [dict(c) for c in columns],
                "measures": [dict(m) for m in measures],
            }
        ],
    }


def _entry(eid, pre_model, post_model):
    pre, post = images.split_model(pre_model), images.split_model(post_model)
    return {"id": eid, "changes": images.diff_maps(pre, post)}


def _live(model):
    return images.split_model(model)


def _meas(name="M1", **kw):
    return {"name": name, "expression": "1", "lineageTag": "tag-M1", **kw}


def _col(name="C", **kw):
    return {"name": name, "dataType": "string", "sourceColumn": name, "lineageTag": "tc", **kw}


# ---------------------------------------------------------------------------
# the three-way merge helper
# ---------------------------------------------------------------------------
def test_three_way_applies_only_keys_that_differ_between_base_and_target():
    base = {"name": "M", "expression": "B"}
    target = {"name": "M", "expression": "A"}
    live = {"name": "M", "expression": "B", "formatString": "0"}
    res = images.three_way_merge(base, target, live)
    assert res["merged"] == {"name": "M", "expression": "A", "formatString": "0"}
    assert res["conflicts"] == [] and res["changed"] == ["expression"]


def test_three_way_a_removal_counts_and_deletes_the_key():
    res = images.three_way_merge({"name": "M"}, {"name": "M", "isHidden": True}, {"name": "M"})
    assert res["merged"]["isHidden"] is True and res["changed"] == ["isHidden"]
    res = images.three_way_merge({"name": "M", "isHidden": True}, {"name": "M"}, {"isHidden": True})
    assert "isHidden" not in res["merged"] and res["changed"] == ["isHidden"]


def test_three_way_key_changed_live_is_a_conflict_and_force_overrides_it():
    base = {"expression": "B"}
    target = {"expression": "A"}
    live = {"expression": "C"}
    res = images.three_way_merge(base, target, live)
    assert res["conflicts"] == ["expression"] and res["merged"] == live and res["changed"] == []
    res = images.three_way_merge(base, target, live, force=True)
    assert res["conflicts"] == [] and res["merged"] == {"expression": "A"}


def test_three_way_key_already_at_target_is_already_done():
    res = images.three_way_merge({"expression": "B"}, {"expression": "A"}, {"expression": "A"})
    assert res["changed"] == [] and res["conflicts"] == [] and res["already"] == ["expression"]


# ---------------------------------------------------------------------------
# N-05: replay of a property removal
# ---------------------------------------------------------------------------
def test_replay_unhide_column_is_applied_not_already_applied():
    pre = _model(columns=[_col("C", isHidden=True)])
    post = _model(columns=[_col("C")])
    plan = plan_mod.plan_replay([_entry("J1", pre, post)], _live(pre))
    assert plan["skipped"] == [] and plan["conflicts"] == []
    (op,) = plan["ops"]
    assert op["action"] == "update" and "isHidden" not in op["json"]
    assert op["current"]["isHidden"] is True


def test_replay_clear_display_folder_is_applied():
    pre = _model(_meas(displayFolder="F"))
    post = _model(_meas())
    plan = plan_mod.plan_replay([_entry("J1", pre, post)], _live(pre))
    (op,) = plan["ops"]
    assert "displayFolder" not in op["json"] and plan["skipped"] == []


def test_replay_clear_description_is_applied():
    pre = _model(_meas(description="d"))
    post = _model(_meas())
    plan = plan_mod.plan_replay([_entry("J1", pre, post)], _live(pre))
    (op,) = plan["ops"]
    assert "description" not in op["json"] and plan["skipped"] == []


def test_replay_already_applied_when_the_removal_is_already_live():
    pre = _model(_meas(displayFolder="F"))
    post = _model(_meas())
    plan = plan_mod.plan_replay([_entry("J1", pre, post)], _live(post))
    assert plan["ops"] == []
    assert plan["skipped"][0]["reason"] == "already_applied"


def test_replay_conflicts_per_key_when_live_changed_the_same_property():
    pre = _model(_meas(expression="1"))
    post = _model(_meas(expression="2"))
    live = _model(_meas(expression="3"))
    plan = plan_mod.plan_replay([_entry("J1", pre, post)], _live(live))
    assert plan["ops"] == []
    (conf,) = plan["conflicts"]
    assert conf["keys"] == ["expression"]


def test_replay_keeps_unrelated_live_properties():
    pre = _model(_meas(expression="1"))
    post = _model(_meas(expression="2"))
    live = _model(_meas(expression="1", description="user note"))
    plan = plan_mod.plan_replay([_entry("J1", pre, post)], _live(live))
    (op,) = plan["ops"]
    assert op["json"]["expression"] == "2" and op["json"]["description"] == "user note"


# ---------------------------------------------------------------------------
# N-06: undo of an older entry keeps newer edits to other properties
# ---------------------------------------------------------------------------
def test_undo_older_entry_leaves_a_later_edit_of_another_property_untouched():
    a = _model(_meas(expression="A"))
    b = _model(_meas(expression="B"))
    c = _model(_meas(expression="B", formatString="0.00"))
    e1 = _entry("J1", a, b)
    plan = plan_mod.plan_undo([e1], _live(c))
    assert plan["conflicts"] == []
    (op,) = plan["ops"]
    assert op["json"]["expression"] == "A" and op["json"]["formatString"] == "0.00"
    assert op["json"]["lineageTag"] == "tag-M1"


def test_undo_older_entry_conflicts_when_a_later_edit_changed_the_same_property():
    a = _model(_meas(expression="A"))
    b = _model(_meas(expression="B"))
    c = _model(_meas(expression="C"))
    plan = plan_mod.plan_undo([_entry("J1", a, b)], _live(c))
    assert plan["ops"] == []
    (conf,) = plan["conflicts"]
    assert conf["keys"] == ["expression"] and conf["entry"] == "J1"


def test_undo_force_overrides_only_the_conflicting_key():
    a = _model(_meas(expression="A"))
    b = _model(_meas(expression="B"))
    c = _model(_meas(expression="C", description="keep"))
    plan = plan_mod.plan_undo([_entry("J1", a, b)], _live(c), force=True)
    (op,) = plan["ops"]
    assert plan["conflicts"] == []
    assert op["json"]["expression"] == "A" and op["json"]["description"] == "keep"


def test_undo_of_a_property_removal_restores_the_key():
    pre = _model(_meas(displayFolder="F"))
    post = _model(_meas())
    plan = plan_mod.plan_undo([_entry("J1", pre, post)], _live(post))
    (op,) = plan["ops"]
    assert op["json"]["displayFolder"] == "F"


def test_undo_already_reverted_is_skipped():
    pre = _model(_meas(expression="A"))
    post = _model(_meas(expression="B"))
    plan = plan_mod.plan_undo([_entry("J1", pre, post)], _live(pre))
    assert plan["ops"] == [] and plan["skipped"][0]["reason"] == "already_matches"


def test_undo_rename_still_reverts_the_name():
    pre = _model(_meas("Old"))
    post = _model(_meas("New"))
    e = _entry("J1", pre, post)
    assert e["changes"][0]["change"] == "renamed"
    plan = plan_mod.plan_undo([e], _live(post))
    (op,) = plan["ops"]
    assert op["rename_from"] == "New" and op["json"]["name"] == "Old"


def test_replay_rename_then_already_applied():
    pre = _model(_meas("Old"))
    post = _model(_meas("New"))
    e = _entry("J1", pre, post)
    assert plan_mod.plan_replay([e], _live(post))["skipped"][0]["reason"] == "already_applied"
    (op,) = plan_mod.plan_replay([e], _live(pre))["ops"]
    assert op["json"]["name"] == "New" and op["rename_from"] == "Old"


# ---------------------------------------------------------------------------
# N-22: restore gates data-bearing removals
# ---------------------------------------------------------------------------
def test_restore_of_a_data_column_or_partition_removal_needs_confirmation():
    target = _model()
    live = _model(columns=[_col("C")])
    plan = plan_mod.plan_restore(_live(live), _live(target))
    assert plan_mod.needs_removal_confirmation(plan) == ["column:T[C]"]


def test_restore_removing_many_measures_needs_confirmation():
    live = _model(*[_meas(f"M{i}") for i in range(12)])
    plan = plan_mod.plan_restore(_live(live), _live(_model()))
    assert len(plan_mod.needs_removal_confirmation(plan)) >= 1


def test_restore_removing_one_measure_does_not_need_confirmation():
    plan = plan_mod.plan_restore(_live(_model(_meas())), _live(_model()))
    assert plan_mod.needs_removal_confirmation(plan) == []


# ---------------------------------------------------------------------------
# N-07: another project's journal is never applied
# ---------------------------------------------------------------------------
OTHER = "C:/Repo/Other/Other.pbip"


@pytest.fixture
def live(monkeypatch, tmp_path):
    write_journal.configure(enabled=True, directory=tmp_path / "journal")
    lv = jf.LiveModel()
    jf.install(monkeypatch, lv)
    return lv


def _op(operation, **kw):
    return h.handle_model_operations({"object_type": "model", "operation": operation, **kw})


def _foreign_journal(live, monkeypatch):
    """A journal written while project OTHER was connected, then the server connects to PROJECT."""
    monkeypatch.setattr(write_journal, "_connected_project", lambda: OTHER)
    tom = live.connect()
    tom.measure("Sales", "Total")["expression"] = "FOREIGN"
    with write_journal.call_scope("02_Model_Operations", "02_Model_Operations:measure/update", {}):
        commit(tom, label="measure/update")
    foreign_key = write_journal.list_journals()[0]["key"]
    live.tmsl = copy.deepcopy(jf.BASE_TMSL)  # the connected model is another one
    monkeypatch.setattr(write_journal, "_connected_project", lambda: jf.PROJECT)
    return foreign_key


@pytest.mark.parametrize("operation", ["undo", "replay"])
def test_undo_and_replay_refuse_a_fallback_journal_of_another_project(live, monkeypatch, operation):
    key = _foreign_journal(live, monkeypatch)
    saves = live.saves
    out = _op(operation)
    assert out["success"] is False and out["error_type"] == "journal_project_mismatch"
    assert key in out["error"] or out.get("journal") == key
    assert live.saves == saves
    assert jf.measure_expr(live, "Sales", "Total") == "SUM(Sales[Amount])"


def test_preview_is_refused_too(live, monkeypatch):
    _foreign_journal(live, monkeypatch)
    assert _op("replay", preview=True)["error_type"] == "journal_project_mismatch"


def test_history_still_shows_the_fallback_with_the_note(live, monkeypatch):
    key = _foreign_journal(live, monkeypatch)
    out = _op("history")
    assert out["success"] is True and out["journal"] == key and "note" in out


def test_explicit_journal_argument_overrides_the_guard(live, monkeypatch):
    key = _foreign_journal(live, monkeypatch)
    out = _op("replay", journal=key, preview=True)
    assert out["success"] is True and out["preview"] is True


def test_checkpoint_of_another_project_is_refused(live, monkeypatch):
    monkeypatch.setattr(write_journal, "_connected_project", lambda: OTHER)
    assert _op("checkpoint_create", checkpoint="ck")["success"] is True
    monkeypatch.setattr(write_journal, "_connected_project", lambda: jf.PROJECT)
    out = _op("checkpoint_restore", checkpoint="ck")
    assert out["success"] is False and out["error_type"] == "journal_project_mismatch"
    assert _op("checkpoint_restore", checkpoint="ck", preview=True)["error_type"] == (
        "journal_project_mismatch"
    )


def test_checkpoint_of_the_connected_project_still_restores(live):
    assert _op("checkpoint_create", checkpoint="ck")["success"] is True
    out = _op("checkpoint_restore", checkpoint="ck", preview=True)
    assert out["success"] is True


def test_journal_argument_cannot_escape_the_journal_directory(live, tmp_path):
    outside = tmp_path / "outside_x"
    outside.mkdir()
    (outside / "secret.jsonl").write_text("{}\n", encoding="utf-8")
    for bad in ("../outside_x/secret", "..\\outside_x\\secret", "/etc/passwd", "a/b", "."):
        out = _op("history", journal=bad)
        assert out["success"] is False and out["error_type"] == "invalid_parameters", bad


def test_manager_view_marks_fallback(live, monkeypatch):
    _foreign_journal(live, monkeypatch)
    view, err = journal_manager.WriteJournalManager()._view({})
    assert err is None and view["fallback"] is True
    view, _ = journal_manager.WriteJournalManager()._view({"journal": view["key"]})
    assert not view.get("fallback")
