"""Review slice J (2026-09-22) — Desktop bridge safety + truthfulness.

J-01 reload refuses on unsaved / unknown state (incl. the live-TOM ledger),
J-02 pending-disk-edit ledger + save refusal + rewritten reload hints,
J-05 rejected reload = failure, J-06 no cmd.exe shim injection,
J-07 no flag-only "already saved", J-08 disk verification baseline/t0,
J-13 budget/cancel, J-14 composite lock, J-15 path-matched instance,
J-16 partial screenshot_all + structured CLI errors, J-17 UIA input safety,
J-21 settle / render-stable / case-insensitive pages, J-24 page-only crop,
J-30 atomic + tiered optimize, J-31 argument validation, J-32 open + dirty_sources.

Never runs the real powerbi-desktop CLI or a live Desktop: ``run_bridge`` /
pywinauto are faked; the J-06 test runs a FAKE CLI script through the real
``node`` when node is on PATH.
"""

from __future__ import annotations

import json
import os
import shutil
import time
import types
from pathlib import Path

import pytest

from core.autonomous import desktop_bridge as db
from core.autonomous import edit_ledger
from core.autonomous import reload_hints
from server.handlers import desktop_bridge_handler as h


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _project(tmp_path: Path, name: str = "Proj") -> dict:
    """Synthetic PBIP: <name>.pbip -> <name>.Report -> byPath ../Model.SemanticModel."""
    root = tmp_path / name
    rep = root / f"{name}.Report"
    model = root / "Model.SemanticModel"
    (rep / "definition" / "pages" / "p1" / "visuals" / "v1").mkdir(parents=True)
    (model / "definition" / "tables").mkdir(parents=True)
    (root / f"{name}.pbip").write_text(
        json.dumps({"artifacts": [{"report": {"path": f"{name}.Report"}}]}), encoding="utf-8"
    )
    (rep / "definition.pbir").write_text(
        json.dumps({"datasetReference": {"byPath": {"path": "../Model.SemanticModel"}}}),
        encoding="utf-8",
    )
    (model / "definition.pbism").write_text("{}", encoding="utf-8")
    (model / "definition" / "model.tmdl").write_text("model Model\n", encoding="utf-8")
    (model / "definition" / "tables" / "Sales.tmdl").write_text("table Sales\n", encoding="utf-8")
    (rep / "definition" / "pages" / "p1" / "page.json").write_text(
        json.dumps({"name": "p1", "displayName": "Overview", "width": 1280, "height": 720}),
        encoding="utf-8",
    )
    vj = rep / "definition" / "pages" / "p1" / "visuals" / "v1" / "visual.json"
    vj.write_text(
        json.dumps(
            {
                "name": "v1",
                "position": {"x": 10, "y": 20, "width": 300, "height": 120},
                "visual": {"visualType": "cardVisual"},
            }
        ),
        encoding="utf-8",
    )
    return {
        "root": root,
        "pbip": root / f"{name}.pbip",
        "report": rep,
        "model": model,
        "visual": vj,
    }


def _inst(pbip, pid=7, dirty=False, **extra):
    d = {
        "pid": pid,
        "bridgeStatus": "connected",
        "currentFilePath": str(pbip),
        "hasUnsavedChanges": dirty,
        "pages": [{"id": "p1", "displayName": "Overview"}],
    }
    d.update(extra)
    return d


def _fake_cli(monkeypatch, instances, *, reload_payload=None, calls=None, shot=None):
    calls = calls if calls is not None else []

    def fake_run(argv, **k):
        calls.append(list(argv))
        if argv[0] == "status":
            if "--pid" in argv:
                pid = int(argv[argv.index("--pid") + 1])
                sel = [i for i in instances if i.get("pid") == pid]
                return {"ok": True, "data": {"status": "ready", "instances": sel}}
            return {"ok": True, "data": {"status": "ready", "instances": list(instances)}}
        if argv[0] == "reload":
            return {
                "ok": True,
                "data": reload_payload or {"status": "ok", "pid": 7, "result": {"success": True}},
            }
        if argv[0] in ("screenshot", "screenshot-all") and shot is not None:
            return shot(argv)
        return {"ok": True, "data": {"status": "ok"}}

    monkeypatch.setattr(db, "run_bridge", fake_run)
    monkeypatch.setattr(db, "_optimize_output", lambda out, *a: None)
    return calls


# ---------------------------------------------------------------------------
# J-01 — reload refuses on unsaved / unknown work
# ---------------------------------------------------------------------------
def test_j01_live_tom_write_in_ledger_refuses_even_when_flag_false(monkeypatch, tmp_path):
    """The CLI emits hasUnsavedChanges `=== true`; a TOM write this server made
    must still block the reload (the 2026-07-03 incident: 8 measures lost)."""
    proj = _project(tmp_path)
    calls = _fake_cli(monkeypatch, [_inst(proj["pbip"], dirty=False)])
    edit_ledger.record_live_write("02_Model_Operations:measure/create", project=str(proj["pbip"]))
    out = db.op_reload(pid=7)
    assert out["success"] is False
    assert out["error_type"] == "unsaved_changes"
    assert out["dirty_sources"]["ledger_live_writes"] == 1
    assert "reload" not in [c[0] for c in calls]


def test_j01_ledger_entry_for_other_project_does_not_block(monkeypatch, tmp_path):
    a = _project(tmp_path, "A")
    b = _project(tmp_path, "B")
    _fake_cli(monkeypatch, [_inst(a["pbip"])])
    edit_ledger.record_live_write("02_Model_Operations:measure/create", project=str(b["pbip"]))
    assert db.op_reload(pid=7)["success"] is True


def test_j01_unknown_project_ledger_entry_fails_closed(monkeypatch, tmp_path):
    proj = _project(tmp_path)
    _fake_cli(monkeypatch, [_inst(proj["pbip"])])
    monkeypatch.setattr(edit_ledger, "_connected_project", lambda: None)
    edit_ledger.record_live_write("03_Batch_Operations:measure/create")
    out = db.op_reload(pid=7)
    assert out["error_type"] == "unsaved_changes"


def test_j01_manual_save_detected_on_disk_clears_ledger(monkeypatch, tmp_path):
    """A user Ctrl+S rewrites the model TMDL after the TOM write -> flushed."""
    proj = _project(tmp_path)
    _fake_cli(monkeypatch, [_inst(proj["pbip"])])
    edit_ledger.record_live_write("02_Model_Operations:measure/create", project=str(proj["pbip"]))
    # Backdate the ledger entry, then "save": rewrite a model file now.
    with edit_ledger._LOCK:
        edit_ledger._TOM[-1]["t"] = time.time() - 30
    (proj["model"] / "definition" / "tables" / "Sales.tmdl").write_text(
        "table Sales\n\tmeasure X = 1\n", encoding="utf-8"
    )
    out = db.op_reload(pid=7)
    assert out["success"] is True
    assert edit_ledger.pending_tom_writes([], [str(proj["model"])]) == []


def test_j01_discard_unsaved_clears_tom_ledger(monkeypatch, tmp_path):
    proj = _project(tmp_path)
    _fake_cli(monkeypatch, [_inst(proj["pbip"], dirty=True)])
    edit_ledger.record_live_write("x:y", project=str(proj["pbip"]))
    out = db.op_reload(pid=7, discard_unsaved=True)
    assert out["success"] is True
    assert out["discarded_unsaved"]["reasons"]
    assert edit_ledger.pending_tom_writes([str(proj["report"])], [str(proj["model"])]) == []


def test_j01_debug_loop_reload_goes_through_refusal(monkeypatch, tmp_path):
    from core.autonomous.loops import debug_loop

    proj = _project(tmp_path)
    _fake_cli(monkeypatch, [_inst(proj["pbip"], dirty=True)])
    paths = types.SimpleNamespace(pbip_file=proj["pbip"])
    cfg = debug_loop.DebugLoopConfig(pbip_path=str(proj["pbip"]), page="p1", visual="v1")
    res = debug_loop._reload(paths, cfg)
    assert res["success"] is False
    assert res["step"]["error_type"] == "unsaved_changes"


def test_j01_status_reports_dirty_sources(monkeypatch, tmp_path):
    proj = _project(tmp_path)
    _fake_cli(monkeypatch, [_inst(proj["pbip"])])
    edit_ledger.record_live_write("x:y", project=str(proj["pbip"]))
    out = h.handle_desktop_bridge({"operation": "status"})
    ds = out["instances"][0]["dirty_sources"]
    assert ds["verdict"] == "dirty"
    assert ds["intent_flag"] is False and ds["ledger_live_writes"] == 1


# ---------------------------------------------------------------------------
# J-02 — pending disk edits: save refuses, reload clears PBIR, TMDL survives
# ---------------------------------------------------------------------------
def test_j02_pbir_write_through_pbip_utils_feeds_the_ledger(tmp_path):
    from core.utilities.pbip_utils import save_json_file

    proj = _project(tmp_path)
    save_json_file(proj["visual"], {"name": "v1", "visual": {"visualType": "card"}})
    pend = edit_ledger.pending_disk_edits([str(proj["report"])], [])
    assert len(pend) == 1 and pend[0]["kind"] == "pbir"


def test_j02_save_refuses_with_pending_disk_edits(monkeypatch, tmp_path):
    from core.utilities.pbip_utils import save_json_file

    proj = _project(tmp_path)
    _fake_cli(monkeypatch, [_inst(proj["pbip"], dirty=True)])
    save_json_file(proj["visual"], {"name": "v1"})
    import core.autonomous.ui_automation as uia

    monkeypatch.setattr(uia, "PbiDesktopUIAutomation", lambda **k: pytest.fail("must not save"))
    out = db.op_save(pid=7)
    assert out["success"] is False
    assert out["error_type"] == "conflict_pending_disk_and_memory"
    assert out["memory_state"] == "dirty"
    # save_first on the reload path refuses the same way (the 2026-08-12 incident).
    out2 = db.op_reload(pid=7, save_first=True)
    assert out2["error_type"] == "conflict_pending_disk_and_memory"


def test_j02_reload_clears_pbir_but_not_tmdl_pending(monkeypatch, tmp_path):
    proj = _project(tmp_path)
    _fake_cli(monkeypatch, [_inst(proj["pbip"])])
    edit_ledger.record_disk_write(str(proj["visual"]))
    edit_ledger.record_disk_write(str(proj["model"] / "definition" / "tables" / "Sales.tmdl"))
    out = db.op_reload(pid=7)
    assert out["success"] is True
    assert out["tmdl_edits_not_applied"] and "open" in out["tmdl_warning"]
    left = edit_ledger.pending_disk_edits([str(proj["report"])], [str(proj["model"])])
    assert [e["kind"] for e in left] == ["tmdl"]


def test_j02_dispatcher_hook_classifies_writes(tmp_path):
    proj = _project(tmp_path)
    edit_ledger.note_dispatch_write(
        "02_TMDL_Operations:bulk_rename", {"tmdl_path": str(proj["model"])}
    )
    edit_ledger.note_dispatch_write("02_Model_Operations:measure/create", {})
    edit_ledger.note_dispatch_write("17_Desktop_Bridge:reload", {})
    disk = edit_ledger.pending_disk_edits([], [str(proj["model"])])
    assert disk and disk[0]["kind"] == "tmdl"
    snap = edit_ledger.snapshot()
    assert [e["label"] for e in snap["tom_writes"]] == ["02_Model_Operations:measure/create"]


def test_j02_dispatch_calls_the_ledger_after_a_successful_write(monkeypatch):
    import server.dispatch as dispatch

    seen = []
    monkeypatch.setattr(edit_ledger, "note_dispatch_write", lambda label, args: seen.append(label))
    dispatch._note_edit_ledger("02_Model_Operations:measure/create", {})
    assert seen == ["02_Model_Operations:measure/create"]


def test_j02_reload_hint_never_advises_save_after_a_disk_edit():
    resp = reload_hints.attach_reload_hint({"success": True}, "07_Visual_Operations", "add_field")
    assert "save_first=true" not in resp["reload_hint"]
    assert "never save first" in resp["reload_hint"]
    assert "ONCE after the whole batch" in resp["reload_hint"]
    assert resp["save_forbidden_until_reload"] is True


def test_j02_tmdl_hint_says_reload_cannot_apply_it():
    resp = reload_hints.attach_reload_hint({"success": True}, "02_TMDL_Operations", "bulk_rename")
    assert resp["reload_applies"] is False and resp["reopen_required"] is True
    assert resp["reload_op_chain"][-1] == "17_Desktop_Bridge:open"
    assert "reloadModelDefinition:false" in resp["reload_hint"]


# ---------------------------------------------------------------------------
# J-05 — a reload Desktop rejected is a failure
# ---------------------------------------------------------------------------
def test_j05_bridge_result_false_is_reload_failed_and_no_screenshot(monkeypatch, tmp_path):
    proj = _project(tmp_path)
    calls = _fake_cli(
        monkeypatch,
        [_inst(proj["pbip"])],
        reload_payload={"status": "ok", "pid": 7, "result": {"success": False, "error": "bad"}},
    )
    out = db.op_reload(pid=7)
    assert out["success"] is False and out["error_type"] == "reload_failed"
    assert out["result"]["error"] == "bad"
    out2 = db.op_reload_and_screenshot(page="Overview", output=str(tmp_path / "o.png"), pid=7)
    assert out2["error_type"] == "reload_failed"
    assert "screenshot" not in [c[0] for c in calls]


# ---------------------------------------------------------------------------
# J-06 — the npm .cmd shim is never executed with cmd metacharacters
# ---------------------------------------------------------------------------
# (bridge CLI version, the dist/ script its package.json `bin` names): 0.1.1 ships the
# CLI at dist/index.js; from 0.2.0 it is dist/cli.js and dist/index.js is the silent library.
_CLI_LAYOUTS = [("0.1.1", "index.js"), ("1.0.0", "cli.js")]


def _fake_prefix(tmp_path: Path, with_script: bool, version: str = "0.1.1") -> Path:
    prefix = tmp_path / "npm"
    prefix.mkdir()
    (prefix / "powerbi-desktop.cmd").write_text("@echo off\r\necho ARGS=[%*]\r\n", encoding="utf-8")
    if with_script:
        entry = dict(_CLI_LAYOUTS)[version]
        pkg = prefix / "node_modules" / "@microsoft" / "powerbi-desktop-bridge-cli"
        d = pkg / "dist"
        d.mkdir(parents=True)
        (pkg / "package.json").write_text(
            json.dumps(
                {"name": db.CLI_PACKAGE, "version": version, "bin": {db.CLI_NAME: f"dist/{entry}"}}
            ),
            encoding="utf-8",
        )
        (d / entry).write_text(
            "process.stdout.write(JSON.stringify({argv: process.argv.slice(2)}));",
            encoding="utf-8",
        )
        if entry != "index.js":
            (d / "index.js").write_text("// library entry: prints nothing", encoding="utf-8")
    return prefix


@pytest.mark.parametrize("version,entry", _CLI_LAYOUTS)
def test_j06_shim_resolved_to_node_and_script(monkeypatch, tmp_path, version, entry):
    prefix = _fake_prefix(tmp_path, with_script=True, version=version)
    monkeypatch.setattr(
        db.shutil,
        "which",
        lambda n: str(prefix / "powerbi-desktop.cmd") if n == db.CLI_NAME else "C:/fake/node.exe",
    )
    db.reset_cli_cache()
    cli = db._resolve_cli()
    assert cli["mode"] == "node"
    assert cli["argv0"][1].endswith(os.path.join("dist", entry))
    assert cli["version"] == version
    seen = {}

    def fake_run(cmd, **k):
        seen["cmd"], seen["shell"] = cmd, k.get("shell")
        return types.SimpleNamespace(stdout="{}", stderr="", returncode=0)

    monkeypatch.setattr(db.subprocess, "run", fake_run)
    db.run_bridge(["screenshot", "p", "--output", 'C:\\t\\a" & echo X & "b.png'])
    assert seen["cmd"][0] == "C:/fake/node.exe" and seen["shell"] is False
    assert seen["cmd"][-1] == 'C:\\t\\a" & echo X & "b.png'


def test_j06_shim_fallback_refuses_cmd_metacharacters(monkeypatch, tmp_path):
    prefix = _fake_prefix(tmp_path, with_script=False)
    monkeypatch.setattr(
        db.shutil,
        "which",
        lambda n: str(prefix / "powerbi-desktop.cmd") if n == db.CLI_NAME else None,
    )
    db.reset_cli_cache()
    ran = []
    monkeypatch.setattr(db.subprocess, "run", lambda *a, **k: ran.append(a))
    for bad in ['C:\\t\\a" & echo INJECTED & "b.png', "C:\\t\\%PATH:~0,12%.png", "a|b", "a^b"]:
        out = db.run_bridge(["screenshot", "p", "--output", bad])
        assert out["ok"] is False and out["error_type"] == "unsafe_argument"
    assert ran == []


@pytest.mark.skipif(shutil.which("node") is None or os.name != "nt", reason="needs node on Windows")
@pytest.mark.parametrize("version,entry", _CLI_LAYOUTS)
def test_j06_real_node_receives_the_argument_verbatim(monkeypatch, tmp_path, version, entry):
    """End to end through a real process: the hostile path arrives as ONE argv
    entry and no second command runs (probe_cmd_injection ran `echo INJECTED`)."""
    prefix = _fake_prefix(tmp_path, with_script=True, version=version)
    real_which = shutil.which
    monkeypatch.setattr(
        db.shutil,
        "which",
        lambda n: str(prefix / "powerbi-desktop.cmd") if n == db.CLI_NAME else real_which(n),
    )
    db.reset_cli_cache()
    hostile = 'C:\\tmp\\a" & echo INJECTED-VIA-OUTPUT & "b %PATH:~0,12%.png'
    out = db.run_bridge(["screenshot", "page1", "--output", hostile], timeout=30)
    assert out["ok"] is True, out
    assert out["data"]["argv"] == ["screenshot", "page1", "--output", hostile]


# ---------------------------------------------------------------------------
# J-07 — save never short-circuits on the intent flag alone
# ---------------------------------------------------------------------------
class _RecordingUIA:
    calls = []

    def __init__(self, save_timeout=30.0):
        pass

    def save(self, pid, pbip_path=None, required_tree=None):
        _RecordingUIA.calls.append((pid, pbip_path, required_tree))
        return {"success": True, "verified": "disk", "mechanism": "invoked"}


def test_j07_flag_false_but_ledger_dirty_really_saves(monkeypatch, tmp_path):
    import core.autonomous.ui_automation as uia

    proj = _project(tmp_path)
    _fake_cli(monkeypatch, [_inst(proj["pbip"], dirty=False)])
    edit_ledger.record_live_write("x:y", project=str(proj["pbip"]))
    _RecordingUIA.calls = []
    monkeypatch.setattr(uia, "PbiDesktopUIAutomation", _RecordingUIA)
    out = db.op_save(pid=7)
    assert out["success"] is True and out["saved"] is True
    assert "already_saved" not in out
    assert _RecordingUIA.calls == [(7, str(proj["pbip"]), "model")]
    # A disk-verified save flushes the ledger.
    assert edit_ledger.pending_tom_writes([str(proj["report"])], [str(proj["model"])]) == []


def test_j07_clean_fast_path_reports_already_saved_not_saved(monkeypatch, tmp_path):
    proj = _project(tmp_path)
    _fake_cli(monkeypatch, [_inst(proj["pbip"], dirty=False)])
    out = db.op_save(pid=7)
    assert out["already_saved"] is True and out["saved"] is False
    r = db.op_reload(pid=7, save_first=True)
    assert r["success"] is True
    assert r["saved"] is False and r["already_saved"] is True


# ---------------------------------------------------------------------------
# J-08 — disk verification: baseline BEFORE the trigger, own writes ignored,
# required tree honoured
# ---------------------------------------------------------------------------
class _Win:
    def __init__(self, on_invoke):
        self._on_invoke = on_invoke

    def window_text(self):
        return "Proj"

    def is_visible(self):
        return True

    def is_enabled(self):
        return True

    def rectangle(self):
        return types.SimpleNamespace(left=0, top=0, right=100, bottom=100)

    def set_focus(self):
        pass

    def is_active(self):
        return True


def _uia_with(monkeypatch, on_invoke, save_timeout=3.2):
    import core.autonomous.ui_automation as ua

    win = _Win(on_invoke)

    class _App:
        def __init__(self, backend=None):
            pass

        def connect(self, process=None, timeout=None):
            return self

        def windows(self):
            return [win]

    monkeypatch.setattr(ua, "_try_import_pywinauto", lambda: _App)
    auto = ua.PbiDesktopUIAutomation(save_timeout=save_timeout)

    def invoke(window, allow_click=True):
        on_invoke()
        return True, "invoked Button {'auto_id': 'SaveButton'}"

    monkeypatch.setattr(auto, "invoke_save_control", invoke)
    return auto


def test_j08_u1_fast_save_before_first_poll_is_verified(monkeypatch, tmp_path):
    proj = _project(tmp_path)
    tm = proj["model"] / "definition" / "tables" / "Sales.tmdl"
    auto = _uia_with(
        monkeypatch, lambda: tm.write_text("table Sales\n\tmeasure M = 1\n", encoding="utf-8")
    )
    res = auto.save(7, pbip_path=str(proj["pbip"]), required_tree="model")
    assert res["success"] is True and res["verified"] == "disk"


def test_j08_u2_own_write_during_save_is_not_a_receipt(monkeypatch, tmp_path):
    proj = _project(tmp_path)

    def our_write():
        from core.utilities.pbip_utils import save_json_file

        save_json_file(proj["visual"], {"name": "v1", "x": 1})  # recorded as OUR write

    auto = _uia_with(monkeypatch, our_write, save_timeout=2.6)
    res = auto.save(7, pbip_path=str(proj["pbip"]))
    assert res["success"] is False and res["error_type"] == "save_timeout"
    assert res["verification_mode"] == "disk"
    assert "title still shows" not in res["error"]


def test_j08_report_only_change_does_not_verify_a_model_save(monkeypatch, tmp_path):
    proj = _project(tmp_path)
    pj = proj["report"] / "definition" / "pages" / "p1" / "page.json"
    auto = _uia_with(
        monkeypatch,
        lambda: pj.write_text('{"name":"p1","x":2}', encoding="utf-8"),
        save_timeout=2.6,
    )
    res = auto.save(7, pbip_path=str(proj["pbip"]), required_tree="model")
    assert res["success"] is False
    assert ".SemanticModel" in res["error"]


def test_j08_project_folder_with_two_reports_is_not_watched_whole(tmp_path):
    from core.autonomous.ui_automation import PbiDesktopUIAutomation

    a = _project(tmp_path / "shared", "A")
    _project(tmp_path / "shared", "B")
    trees = PbiDesktopUIAutomation._resolve_pbip_trees(str(tmp_path / "shared"))
    assert str(tmp_path / "shared") not in trees
    single = PbiDesktopUIAutomation._resolve_pbip_trees(str(a["root"]))
    assert os.path.normcase(str(a["report"])) in [os.path.normcase(t) for t in single]


# ---------------------------------------------------------------------------
# J-13 — no reload after the budget is spent / the call was cancelled
# ---------------------------------------------------------------------------
def test_j13_budget_exhausted_never_reloads(monkeypatch, tmp_path):
    proj = _project(tmp_path)
    calls = _fake_cli(monkeypatch, [_inst(proj["pbip"])])
    out = db.op_reload(pid=7, deadline=time.monotonic() + 1.0)
    assert out["error_type"] == "budget_exhausted"
    assert calls == []


def test_j13_cancelled_call_never_reloads(monkeypatch, tmp_path):
    proj = _project(tmp_path)
    calls = _fake_cli(monkeypatch, [_inst(proj["pbip"])])
    monkeypatch.setattr(db, "_cancelled", lambda: True)
    assert db.op_reload(pid=7)["error_type"] == "cancelled"
    assert calls == []


def test_j13_wait_seconds_capped_by_budget(monkeypatch, tmp_path):
    proj = _project(tmp_path)
    calls = _fake_cli(monkeypatch, [_inst(proj["pbip"])])
    db.op_reload(pid=7, deadline=time.monotonic() + 30.0)
    reload_argv = [c for c in calls if c[0] == "reload"][0]
    assert int(reload_argv[reload_argv.index("--wait-seconds") + 1]) <= 25


# ---------------------------------------------------------------------------
# J-14 — the composite holds the per-PID lock across reload + screenshot
# ---------------------------------------------------------------------------
def test_j14_composite_holds_pid_lock_between_steps(monkeypatch, tmp_path):
    held = []
    lock = db._pid_lock(7)

    def fake_reload(**k):
        held.append(lock._is_owned())
        return {"success": True, "pid": 7}

    def fake_shot(**k):
        held.append(lock._is_owned())
        return {"success": True, "output": k["output"]}

    monkeypatch.setattr(db, "op_reload", fake_reload)
    monkeypatch.setattr(db, "op_screenshot", fake_shot)
    db.op_reload_and_screenshot(page="P", output=str(tmp_path / "o.png"), pid=7)
    assert held == [True, True]


# ---------------------------------------------------------------------------
# J-15 — pbip_path must match the instance's open project
# ---------------------------------------------------------------------------
def test_j15_single_instance_other_project_is_mismatch(tmp_path):
    a = _project(tmp_path, "A")
    b = _project(tmp_path, "B")
    with pytest.raises(db.BridgePathMismatch):
        db.select_instance({"instances": [_inst(a["pbip"])]}, pid=None, pbip_path=str(b["pbip"]))


@pytest.mark.parametrize("which", ["folder", "report", "model", "case", "slashes"])
def test_j15_folder_report_model_and_case_variants_match(tmp_path, which):
    a = _project(tmp_path, "A")
    b = _project(tmp_path / "other", "B")
    insts = {"instances": [_inst(a["pbip"], pid=1), _inst(b["pbip"], pid=2)]}
    path = {
        "folder": str(a["root"]),
        "report": str(a["report"]),
        "model": str(a["model"]),
        "case": str(a["pbip"]).upper(),
        "slashes": str(a["pbip"]).replace("\\", "/"),
    }[which]
    assert db.select_instance(insts, pid=None, pbip_path=path)["pid"] == 1


def test_j15_two_reports_sharing_a_model_do_not_match_each_other(tmp_path):
    root = tmp_path / "repo"
    a = _project(root, "A")
    # B: second report in the SAME folder bound to A's model.
    brep = root / "A" / "B.Report"
    brep.mkdir()
    (brep / "definition.pbir").write_text(
        json.dumps({"datasetReference": {"byPath": {"path": "../Model.SemanticModel"}}}),
        encoding="utf-8",
    )
    bpbip = root / "A" / "B.pbip"
    bpbip.write_text(
        json.dumps({"artifacts": [{"report": {"path": "B.Report"}}]}), encoding="utf-8"
    )
    with pytest.raises(db.BridgePathMismatch):
        db.select_instance({"instances": [_inst(a["pbip"])]}, pid=None, pbip_path=str(bpbip))


# ---------------------------------------------------------------------------
# J-16 — partial screenshot_all is success; CLI error codes survive
# ---------------------------------------------------------------------------
def test_j16_partial_screenshot_all_is_success_and_optimizes_captured(monkeypatch, tmp_path):
    proj = _project(tmp_path)
    shots = [{"pageId": f"p{i}", "outputPath": str(tmp_path / f"p{i}.png")} for i in range(3)]
    partial = {
        "ok": False,
        "error_type": "cli_error",
        "error": "exited 1",
        "data": {"status": "partial", "screenshots": shots, "failures": [{"pageId": "z"}]},
        "returncode": 1,
    }
    _fake_cli(monkeypatch, [_inst(proj["pbip"])], shot=lambda argv: partial)
    optimized = []
    monkeypatch.setattr(db, "_optimize_output", lambda out, *a: optimized.append(out) or {"x": 1})
    out = db.op_screenshot_all(output_dir=str(tmp_path), pid=7)
    assert out["success"] is True and out["partial"] is True
    assert out["failures"] == [{"pageId": "z"}]
    assert len(optimized) == 3


def test_j16_cli_error_code_is_passed_through(monkeypatch, tmp_path):
    proj = _project(tmp_path)
    err = {
        "ok": False,
        "error_type": "cli_error",
        "error": "exited 1",
        "data": {"status": "error", "error": {"code": "REPORT_DIR_REQUIRED", "message": "no"}},
        "raw_stderr": "stack",
        "returncode": 1,
    }
    _fake_cli(monkeypatch, [_inst(proj["pbip"])], shot=lambda argv: err)
    out = db.op_screenshot_all(output_dir=str(tmp_path), pid=7)
    assert out["success"] is False
    assert out["cli_error_code"] == "REPORT_DIR_REQUIRED"
    assert out["raw_stderr"] == "stack"


# ---------------------------------------------------------------------------
# J-17 — UIA input safety
# ---------------------------------------------------------------------------
def test_j17_no_foreground_path_never_click_inputs(monkeypatch):
    from core.autonomous.ui_automation import PbiDesktopUIAutomation

    clicked = []

    class _Ctrl:
        def exists(self, timeout=0):
            return True

        def invoke(self):
            raise RuntimeError("no InvokePattern")

        def click_input(self):
            clicked.append(1)

    class _W:
        def child_window(self, **kw):
            return _Ctrl()

    ok, _ = PbiDesktopUIAutomation().invoke_save_control(_W(), allow_click=False)
    assert clicked == []
    assert ok is False


def test_j17_sendinput_skipped_when_foreground_lost(monkeypatch):
    from core.autonomous.ui_automation import PbiDesktopUIAutomation

    uia = PbiDesktopUIAutomation()
    sent = []
    monkeypatch.setattr(uia, "invoke_save_control", lambda w: (False, "none"))
    monkeypatch.setattr(uia, "_foreground_is", lambda w: False)
    monkeypatch.setattr(uia, "_send_input_ctrl_s", staticmethod(lambda: sent.append(1) or True))
    monkeypatch.setattr(uia, "_resolve_hwnd", lambda w: None)
    assert uia._send_save_keystroke(object()) is False
    assert sent == []


def test_j17_minimized_window_is_restored_and_used():
    from core.autonomous.ui_automation import PbiDesktopUIAutomation

    class _Min:
        restored = False

        def is_visible(self):
            return self.restored

        def is_minimized(self):
            return not self.restored

        def restore(self):
            self.restored = True

        def is_enabled(self):
            return True

        def window_text(self):
            return "Proj"

        def rectangle(self):
            return types.SimpleNamespace(left=0, top=0, right=10, bottom=10)

    w = _Min()
    app = types.SimpleNamespace(windows=lambda: [w])
    assert PbiDesktopUIAutomation()._main_windows(app) == [w]
    assert w.restored is True


# ---------------------------------------------------------------------------
# J-21 — settle / render-stable / case-insensitive + ambiguous pages
# ---------------------------------------------------------------------------
def test_j21_settle_ms_sleeps_before_capture(monkeypatch, tmp_path):
    proj = _project(tmp_path)
    _fake_cli(monkeypatch, [_inst(proj["pbip"])])
    sleeps = []
    monkeypatch.setattr(db.time, "sleep", lambda s: sleeps.append(s))
    out = db.op_screenshot(page="Overview", output=str(tmp_path / "o.png"), pid=7, settle_ms=2500)
    assert out["success"] is True and sleeps == [2.5] and out["settle_ms"] == 2500


def test_j21_render_stable_recaptures_until_identical(monkeypatch, tmp_path):
    proj = _project(tmp_path)
    out_png = tmp_path / "o.png"
    frames = iter([b"a", b"b", b"b"])

    def shot(argv):
        out_png.write_bytes(next(frames))
        return {"ok": True, "data": {"status": "ok"}}

    calls = _fake_cli(monkeypatch, [_inst(proj["pbip"])], shot=shot)
    monkeypatch.setattr(db.time, "sleep", lambda s: None)
    out = db.op_screenshot(page="Overview", output=str(out_png), pid=7, render_stable=True)
    assert out["render_stable"] == {"captures": 3, "stable": True}
    assert [c[0] for c in calls].count("screenshot") == 3


def test_j21_page_match_is_case_insensitive_and_flags_duplicates(tmp_path):
    inst = {
        "pages": [
            {"id": "a", "displayName": "Sales"},
            {"id": "b", "displayName": "Dup"},
            {"id": "c", "displayName": "Dup"},
        ]
    }
    assert db.resolve_page(inst, "sales")["page_id"] == "a"
    amb = db.resolve_page(inst, "Dup")
    assert amb["page_id"] is None and amb["ambiguous"] == ["b", "c"]


def test_j21_reload_and_screenshot_defaults_to_a_render_settle(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        db, "op_reload_and_screenshot", lambda **k: seen.update(k) or {"success": True}
    )
    h.handle_desktop_bridge(
        {"operation": "reload_and_screenshot", "pid": 7, "page": "P", "output": "o.png"}
    )
    assert seen["settle_ms"] == db.DEFAULT_RELOAD_SETTLE_MS


# ---------------------------------------------------------------------------
# J-24 — per_visual reads only the captured page and decodes the PNG once
# ---------------------------------------------------------------------------
def test_j24_per_visual_does_not_walk_the_whole_report(monkeypatch, tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image

    import tools.visual_catalog.inventory as inv

    proj = _project(tmp_path)
    monkeypatch.setattr(inv, "walk_report", lambda root: pytest.fail("whole-report walk"))
    png = tmp_path / "page.png"
    Image.new("RGB", (1280, 720), color=(1, 2, 3)).save(png)
    opens = []
    real_open = Image.open
    monkeypatch.setattr(Image, "open", lambda *a, **k: opens.append(1) or real_open(*a, **k))
    res = db._crop_page_visuals(
        full_page_png=str(png),
        report_root=str(proj["report"]),
        page_id="p1",
        crops_dir=str(tmp_path / "crops"),
        optimize=False,
    )
    assert [c["visual_id"] for c in res["crops"]] == ["v1"]
    assert len(opens) == 1


# ---------------------------------------------------------------------------
# J-30 — optimize is atomic and honours the vision tier
# ---------------------------------------------------------------------------
def test_j30_standard_tier_and_atomic_replace(monkeypatch, tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image

    from core.autonomous import image_optimize as io

    png = tmp_path / "big.png"
    Image.new("RGB", (3000, 1700)).save(png)
    replaced = []
    real_replace = os.replace
    monkeypatch.setattr(
        io.os, "replace", lambda a, b: replaced.append((a, b)) or real_replace(a, b)
    )
    stats = io.optimize_png(png, vision_tier="standard")
    assert max(stats["final_size"]) <= io.STANDARD_MAX_EDGE
    assert stats["visual_tokens"] <= io.STANDARD_MAX_TOKENS
    assert replaced and Path(replaced[0][1]) == png
    assert not list(tmp_path.glob("*.tmp"))
    with pytest.raises(ValueError):
        io.tier_limits("ultra")


# ---------------------------------------------------------------------------
# J-31 — argument validation / forwarding
# ---------------------------------------------------------------------------
def test_j31_invalid_pid_is_an_error_not_ignored(monkeypatch):
    monkeypatch.setattr(db, "op_reload", lambda **k: pytest.fail("must not reload"))
    for bad in ("abc", -1, 1.5, True):
        out = h.handle_desktop_bridge({"operation": "reload", "pid": bad})
        assert out["success"] is False and out["error_type"] == "invalid_argument"


def test_j31_null_wait_seconds_does_not_crash(monkeypatch):
    seen = {}
    monkeypatch.setattr(db, "op_reload", lambda **k: seen.update(k) or {"success": True})
    out = h.handle_desktop_bridge({"operation": "reload", "pid": 7, "wait_seconds": None})
    assert out["success"] is True and seen["wait_seconds"] is None


def test_j31_wait_seconds_is_forwarded_to_the_cli(monkeypatch, tmp_path):
    proj = _project(tmp_path)
    calls = _fake_cli(monkeypatch, [_inst(proj["pbip"])])
    db.op_reload(pid=7, wait_seconds=12)
    db.op_screenshot(page="Overview", output=str(tmp_path / "o.png"), pid=7, wait_seconds=12)
    for verb in ("reload", "screenshot"):
        argv = [c for c in calls if c[0] == verb][0]
        assert argv[argv.index("--wait-seconds") + 1] == "12"


def test_j31_schema_declares_page_aliases():
    """L-11 (wave 3): the schema declares the canonical page_name only; the
    legacy page / page_id spellings are folded in by server/arg_aliases.py."""
    from server.arg_aliases import normalize_tool_args
    from server.registry import HandlerRegistry

    reg = HandlerRegistry()
    h.register_desktop_bridge_handler(reg)
    props = reg._handlers["17_Desktop_Bridge"].input_schema["properties"]
    for legacy in ("page", "page_id"):
        assert legacy not in props
        args, notes = normalize_tool_args("17_Desktop_Bridge", {legacy: "Overview"})
        assert args == {"page_name": "Overview"} and legacy in notes["deprecated_args"]
    for key in (
        "page_name",
        "discard_unsaved",
        "overwrite_disk_edits",
        "settle_ms",
        "render_stable",
        "vision_tier",
    ):
        assert key in props
    assert "open" in props["operation"]["enum"]


# ---------------------------------------------------------------------------
# J-32 — open (reopen lane) + classification
# ---------------------------------------------------------------------------
def test_j32_open_runs_cli_open_and_clears_ledger(monkeypatch, tmp_path):
    proj = _project(tmp_path)
    edit_ledger.record_disk_write(str(proj["model"] / "definition" / "tables" / "Sales.tmdl"))
    seen = []

    def fake_run(argv, **k):
        seen.append(list(argv))
        if argv[0] == "status":
            # BR-08: the pid is only trusted once an instance with this file open exists.
            opened = any(a[0] == "open" for a in seen)
            return {
                "ok": True,
                "data": {"instances": [_inst(proj["pbip"], pid=9)] if opened else []},
            }
        return {"ok": True, "data": {"status": "launched", "bridgeStatus": "connected", "pid": 9}}

    monkeypatch.setattr(db, "run_bridge", fake_run)
    monkeypatch.setattr(db, "_OPEN_POLL_INTERVAL_S", 0.0)
    out = h.handle_desktop_bridge({"operation": "open", "pbip_path": str(proj["pbip"])})
    assert out["success"] is True and out["pid"] == 9 and out["verified"] is True
    open_call = next(c for c in seen if c[0] == "open")
    assert open_call[1] == str(proj["pbip"].resolve())
    assert edit_ledger.pending_disk_edits([], [str(proj["model"])]) == []


def test_j32_open_refuses_when_already_open(monkeypatch, tmp_path):
    proj = _project(tmp_path)
    _fake_cli(monkeypatch, [_inst(proj["pbip"])])
    out = db.op_open(pbip_path=str(proj["pbip"]))
    assert out["success"] is False and out["error_type"] == "already_open"


def test_j32_open_is_classified():
    from server.op_classification import classify

    assert classify("17_Desktop_Bridge", {"operation": "open"}) == "write"
