"""BR-03 (light calibration) and the ``open`` pid finding.

BR-03: Desktop 2.157.627.0 reported ``hasUnsavedChanges=true`` on an untouched PBIP and the
flag did not clear after a save. ``dirty_state_source`` says which signal decided a verdict; a
save VERIFIED ON DISK that leaves the flag true marks the pid ``flag_stuck``. The gate is never
bypassed automatically (the caller gets a suggestion and ``discard_unsaved`` stays explicit).

open: the 1.0.0 CLI returned another, already-connected instance's pid before the new window
was ready (live spike 2026-09-30). The pid is only trusted after status shows a NEW connected
instance whose ``currentFilePath`` is the requested file.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.autonomous import desktop_bridge as db
from core.autonomous import edit_ledger
from server.handlers import desktop_bridge_handler as h


def _project(tmp_path: Path, name="Proj") -> dict:
    root = tmp_path / name
    rep = root / f"{name}.Report"
    model = root / "Model.SemanticModel"
    (rep / "definition" / "pages" / "p1").mkdir(parents=True)
    (model / "definition").mkdir(parents=True)
    (root / f"{name}.pbip").write_text(
        json.dumps({"artifacts": [{"report": {"path": f"{name}.Report"}}]}), encoding="utf-8"
    )
    (rep / "definition.pbir").write_text(
        json.dumps({"datasetReference": {"byPath": {"path": "../Model.SemanticModel"}}}),
        encoding="utf-8",
    )
    (model / "definition.pbism").write_text("{}", encoding="utf-8")
    (model / "definition" / "model.tmdl").write_text("model Model\n", encoding="utf-8")
    return {"pbip": root / f"{name}.pbip", "report": rep, "model": model}


def _inst(pbip, pid=7, flag=False, status="connected"):
    return {
        "pid": pid,
        "bridgeStatus": status,
        "currentFilePath": str(pbip),
        "hasUnsavedChanges": flag,
        "pages": [{"id": "p1", "displayName": "Overview"}],
    }


class _Cli:
    """Fake bridge CLI whose instance list can change between calls."""

    def __init__(self, monkeypatch, instances):
        self.instances = instances
        self.calls = []
        monkeypatch.setattr(db, "run_bridge", self._run)
        monkeypatch.setattr(db, "_optimize_output", lambda out, *a: None)
        monkeypatch.setattr(db, "_SAVE_VERIFY_INTERVAL", 0.0)
        monkeypatch.setattr(db, "_OPEN_POLL_INTERVAL_S", 0.01)

    def _run(self, argv, **k):
        self.calls.append(list(argv))
        if argv[0] == "status":
            inst = self.instances() if callable(self.instances) else list(self.instances)
            if "--pid" in argv:
                pid = int(argv[argv.index("--pid") + 1])
                inst = [i for i in inst if i["pid"] == pid]
            return {"ok": True, "data": {"status": "ready", "instances": inst}}
        if argv[0] == "reload":
            return {"ok": True, "data": {"status": "ok", "result": {"success": True}}}
        return {"ok": True, "data": {"status": "ok"}}


def _uia(monkeypatch, result):
    import core.autonomous.ui_automation as uia

    class Fake:
        def __init__(self, **k):
            pass

        def save(self, pid, pbip_path=None, required_tree=None):
            return dict(result)

    monkeypatch.setattr(uia, "PbiDesktopUIAutomation", Fake)


# ---------------------------------------------------------------------------
# dirty_state_source
# ---------------------------------------------------------------------------
def test_dirty_state_source_names_the_deciding_signal(tmp_path):
    p = _project(tmp_path)
    assert db.assess_dirty(_inst(p["pbip"], flag=True))["dirty_state_source"] == "bridge_flag"
    assert (
        db.assess_dirty(_inst(p["pbip"], flag=False))["dirty_state_source"] == "bridge_flag+ledger"
    )
    edit_ledger.record_live_write("02_Model_Operations:measure/create", project=str(p["pbip"]))
    assert db.assess_dirty(_inst(p["pbip"], flag=False))["dirty_state_source"] == "ledger"
    assert (
        db.assess_dirty(_inst(p["pbip"], flag=True))["dirty_state_source"] == "bridge_flag+ledger"
    )
    unknown = db.assess_dirty(_inst(p["pbip"], flag=None))
    assert unknown["verdict"] == "unknown" and unknown["dirty_state_source"] == "unavailable"
    lost = db.assess_dirty(_inst(p["pbip"], status="not_connected"))
    assert lost["dirty_state_source"] == "unavailable"


def test_status_and_reload_results_carry_dirty_state_source(monkeypatch, tmp_path):
    p = _project(tmp_path)
    _Cli(monkeypatch, [_inst(p["pbip"])])
    st = h.handle_desktop_bridge({"operation": "status"})
    inst = st["instances"][0]
    assert inst["dirty_state_source"] == "bridge_flag+ledger"
    assert inst["dirty_sources"]["dirty_state_source"] == inst["dirty_state_source"]
    assert db.op_reload(pid=7)["dirty_state_source"] == "bridge_flag+ledger"


def test_refusal_carries_dirty_state_source(monkeypatch, tmp_path):
    p = _project(tmp_path)
    _Cli(monkeypatch, [_inst(p["pbip"], flag=True)])
    out = db.op_reload(pid=7)
    assert out["error_type"] == "unsaved_changes" and out["dirty_state_source"] == "bridge_flag"


# ---------------------------------------------------------------------------
# flag_stuck
# ---------------------------------------------------------------------------
def test_disk_verified_save_with_a_flag_that_stays_true_records_flag_stuck(monkeypatch, tmp_path):
    p = _project(tmp_path)
    _Cli(monkeypatch, [_inst(p["pbip"], flag=True)])  # the flag never clears
    _uia(monkeypatch, {"success": True, "verified": "disk"})
    out = db.op_save(pid=7)
    assert out["success"] is True and out["saved"] is True and out["verified_clean"] is True
    assert out["flag_stuck"] is True and out["has_unsaved_changes_after_save"] is True
    assert out["dirty_state_source"] == "ledger" and "discard_unsaved=true" in out["suggestion"]
    assert db.flag_stuck_info(7) is not None


def test_a_stuck_flag_still_gates_the_reload_and_the_suggestion_is_surfaced(monkeypatch, tmp_path):
    p = _project(tmp_path)
    cli = _Cli(monkeypatch, [_inst(p["pbip"], flag=True)])
    db.note_flag_stuck(7, file=str(p["pbip"]))
    out = db.op_reload(pid=7)
    assert out["success"] is False and out["error_type"] == "unsaved_changes"  # no auto-bypass
    assert out["flag_stuck"] is True and "discard_unsaved=true" in out["suggestion"]
    assert out["dirty_sources"]["flag_stuck"] is True
    assert "reload" not in [c[0] for c in cli.calls]
    # the explicit override still works, and only that
    assert db.op_reload(pid=7, discard_unsaved=True)["success"] is True


def test_flag_stuck_is_per_pid(monkeypatch, tmp_path):
    a, b = _project(tmp_path, "A"), _project(tmp_path, "B")
    db.note_flag_stuck(7)
    assert db.assess_dirty(_inst(a["pbip"], pid=7, flag=True))["flag_stuck"] is True
    other = db.assess_dirty(_inst(b["pbip"], pid=8, flag=True))
    assert other["flag_stuck"] is False and "suggestion" not in other


def test_stuck_marker_is_dropped_once_the_flag_reports_false(tmp_path):
    p = _project(tmp_path)
    db.note_flag_stuck(7)
    assert db.assess_dirty(_inst(p["pbip"], flag=False))["flag_stuck"] is False
    assert db.flag_stuck_info(7) is None


def test_disk_verified_save_that_clears_the_flag_is_not_stuck(monkeypatch, tmp_path):
    p = _project(tmp_path)
    state = {"flag": True}
    _Cli(monkeypatch, lambda: [_inst(p["pbip"], flag=state["flag"])])
    _uia(monkeypatch, {"success": True, "verified": "disk"})
    db.note_flag_stuck(7)  # an old marker must be cleared by a save that works
    # the save clears the flag as soon as it lands
    import core.autonomous.ui_automation as uia

    real = uia.PbiDesktopUIAutomation

    class Clearing(real):
        def save(self, pid, pbip_path=None, required_tree=None):
            state["flag"] = False
            return super().save(pid, pbip_path=pbip_path, required_tree=required_tree)

    monkeypatch.setattr(uia, "PbiDesktopUIAutomation", Clearing)
    out = db.op_save(pid=7)
    assert out["success"] is True and "flag_stuck" not in out
    assert out["has_unsaved_changes_after_save"] is False
    assert out["dirty_state_source"] == "bridge_flag+ledger"


def test_unreadable_flag_after_a_disk_verified_save_is_not_recorded_as_stuck(monkeypatch, tmp_path):
    p = _project(tmp_path)
    inst = _inst(p["pbip"], flag=True)
    calls = {"n": 0}

    def instances():
        calls["n"] += 1
        # first read (the save pre-flight) is dirty; afterwards the flag cannot be read
        return [inst] if calls["n"] == 1 else [{**inst, "hasUnsavedChanges": None}]

    _Cli(monkeypatch, instances)
    _uia(monkeypatch, {"success": True, "verified": "disk"})
    out = db.op_save(pid=7)
    assert out["success"] is True and out["has_unsaved_changes_after_save"] is None
    assert "flag_stuck" not in out and db.flag_stuck_info(7) is None


# ---------------------------------------------------------------------------
# open: never return another instance's pid
# ---------------------------------------------------------------------------
def test_open_returns_the_pid_of_the_instance_that_has_the_file_open(monkeypatch, tmp_path):
    target = _project(tmp_path, "Target")
    users = _project(tmp_path, "Users")
    polls = {"n": 0}

    def instances():
        polls["n"] += 1
        found = [_inst(users["pbip"], pid=1)]  # the user's other, already-connected instance
        if any(c[0] == "open" for c in cli.calls) and polls["n"] >= 3:
            found.append(_inst(target["pbip"], pid=9))
        return found

    cli = _Cli(monkeypatch, instances)
    orig = cli._run

    def run(argv, **k):
        if argv[0] == "open":
            cli.calls.append(list(argv))
            # the 1.0.0 CLI reported the user's OTHER instance
            return {
                "ok": True,
                "data": {"status": "launched", "bridgeStatus": "connected", "pid": 1},
            }
        return orig(argv, **k)

    monkeypatch.setattr(db, "run_bridge", run)
    edit_ledger.record_disk_write(str(target["model"] / "definition" / "model.tmdl"))
    out = h.handle_desktop_bridge({"operation": "open", "pbip_path": str(target["pbip"])})
    assert out["success"] is True and out["verified"] is True
    assert out["pid"] == 9 and out["cli_reported_pid"] == 1
    assert "not the instance that has" in out["warning"]
    assert out["current_file_path"] == str(target["pbip"])
    assert edit_ledger.pending_disk_edits([], [str(target["model"])]) == []


def test_open_without_a_matching_instance_returns_no_pid_and_keeps_the_ledger(
    monkeypatch, tmp_path
):
    target = _project(tmp_path, "Target")
    users = _project(tmp_path, "Users")
    cli = _Cli(monkeypatch, [_inst(users["pbip"], pid=1)])
    orig = cli._run

    def run(argv, **k):
        if argv[0] == "open":
            return {
                "ok": True,
                "data": {"status": "launched", "bridgeStatus": "connected", "pid": 1},
            }
        return orig(argv, **k)

    monkeypatch.setattr(db, "run_bridge", run)
    edit_ledger.record_disk_write(str(target["model"] / "definition" / "model.tmdl"))
    out = db.op_open(pbip_path=str(target["pbip"]), timeout_seconds=1)
    assert out["success"] is True and out["pid"] is None and out["verified"] is False
    assert out["cli_reported_pid"] == 1 and "no connected instance" in out["warning"]
    assert out["bridge_status"] == "unverified"
    assert edit_ledger.pending_disk_edits([], [str(target["model"])])  # not cleared


def test_open_ignores_a_not_yet_connected_instance_with_the_right_file(monkeypatch, tmp_path):
    target = _project(tmp_path, "Target")
    seq = {"opened": False}

    def instances():
        if not seq["opened"]:
            return []
        n = seq.setdefault("n", 0)
        seq["n"] = n + 1
        return [_inst(target["pbip"], pid=9, status="not_connected" if n < 2 else "connected")]

    cli = _Cli(monkeypatch, instances)
    orig = cli._run

    def run(argv, **k):
        if argv[0] == "open":
            seq["opened"] = True
            return {
                "ok": True,
                "data": {"status": "launched", "bridgeStatus": "connected", "pid": 9},
            }
        return orig(argv, **k)

    monkeypatch.setattr(db, "run_bridge", run)
    out = db.op_open(pbip_path=str(target["pbip"]), timeout_seconds=30)
    assert out["verified"] is True and out["pid"] == 9 and "cli_reported_pid" not in out


def test_open_with_allow_duplicate_picks_the_new_instance_not_the_old_one(monkeypatch, tmp_path):
    target = _project(tmp_path, "Target")
    seq = {"opened": False}

    def instances():
        found = [_inst(target["pbip"], pid=5)]  # already open (allow_duplicate)
        if seq["opened"]:
            found.append(_inst(target["pbip"], pid=9))
        return found

    cli = _Cli(monkeypatch, instances)
    orig = cli._run

    def run(argv, **k):
        if argv[0] == "open":
            seq["opened"] = True
            return {
                "ok": True,
                "data": {"status": "launched", "bridgeStatus": "connected", "pid": 5},
            }
        return orig(argv, **k)

    monkeypatch.setattr(db, "run_bridge", run)
    out = db.op_open(pbip_path=str(target["pbip"]), allow_duplicate=True, timeout_seconds=5)
    assert out["verified"] is True and out["pid"] == 9 and out["cli_reported_pid"] == 5


def test_open_with_allow_duplicate_and_no_new_instance_is_unverified(monkeypatch, tmp_path):
    target = _project(tmp_path, "Target")
    cli = _Cli(monkeypatch, [_inst(target["pbip"], pid=5)])
    orig = cli._run

    def run(argv, **k):
        if argv[0] == "open":
            return {
                "ok": True,
                "data": {"status": "launched", "bridgeStatus": "connected", "pid": 5},
            }
        return orig(argv, **k)

    monkeypatch.setattr(db, "run_bridge", run)
    out = db.op_open(pbip_path=str(target["pbip"]), allow_duplicate=True, timeout_seconds=1)
    assert out["pid"] is None and out["verified"] is False


def test_open_still_refuses_when_already_open(monkeypatch, tmp_path):
    target = _project(tmp_path, "Target")
    _Cli(monkeypatch, [_inst(target["pbip"], pid=5)])
    out = db.op_open(pbip_path=str(target["pbip"]))
    assert out["error_type"] == "already_open"
