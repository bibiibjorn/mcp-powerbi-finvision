"""BR-04: ``17_Desktop_Bridge reload include_model=true``.

Never touches a Desktop or an MCP server. The node library helper is replaced by a fake
(``db.run_bridge_lib``) for the op-level tests; ``test_helper_*`` run the REAL
``tools/bridge_lib.mjs`` through real ``node`` against a FAKE bridge-CLI package (a
throwaway ``dist/index.js`` that speaks the same exports) to pin the envelope protocol.
Live Desktop behaviour is covered only by FACTS section E (2026-09-30 spike).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from core.autonomous import desktop_bridge as db
from core.autonomous import edit_ledger
from core.autonomous import model_change_classifier as mcc
from core.autonomous import reload_hints
from server.handlers import desktop_bridge_handler as h
from tests._fake_bridge_lib import install_fake_cli
from tests._tom_fakes import base, meas


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------
def _project(tmp_path: Path) -> dict:
    root = tmp_path / "Proj"
    rep = root / "Proj.Report"
    model = root / "Model.SemanticModel"
    (rep / "definition" / "pages" / "p1" / "visuals" / "v1").mkdir(parents=True)
    (model / "definition" / "tables").mkdir(parents=True)
    (root / "Proj.pbip").write_text(
        json.dumps({"artifacts": [{"report": {"path": "Proj.Report"}}]}), encoding="utf-8"
    )
    (rep / "definition.pbir").write_text(
        json.dumps({"datasetReference": {"byPath": {"path": "../Model.SemanticModel"}}}),
        encoding="utf-8",
    )
    (model / "definition.pbism").write_text("{}", encoding="utf-8")
    (model / "definition" / "model.tmdl").write_text("model Model\n", encoding="utf-8")
    (rep / "definition" / "pages" / "p1" / "page.json").write_text(
        json.dumps({"name": "p1", "displayName": "Overview", "width": 1280, "height": 720}),
        encoding="utf-8",
    )
    (rep / "definition" / "pages" / "p1" / "visuals" / "v1" / "visual.json").write_text(
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
        "pbip": root / "Proj.pbip",
        "report": rep,
        "model": model,
        "tmdl": model / "definition" / "tables" / "Sales.tmdl",
        "visual": rep / "definition" / "pages" / "p1" / "visuals" / "v1" / "visual.json",
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


class Rig:
    """Fake CLI + fake library helper + fake classifier seams; records every call."""

    def __init__(self, monkeypatch, instances):
        self.cli_calls = []
        self.lib_calls = []
        self.live = base()
        self.disk = base()
        self.live_after = None  # None -> the live model equals the disk after the reload
        self.lib_result = None
        self.reloaded = False
        self.live_reads = 0

        def fake_run(argv, **k):
            self.cli_calls.append(list(argv))
            if argv[0] == "status":
                return {"ok": True, "data": {"status": "ready", "instances": list(instances)}}
            if argv[0] == "reload":
                return {"ok": True, "data": {"status": "ok", "result": {"success": True}}}
            return {"ok": True, "data": {"status": "ok"}}

        def fake_lib(command, args, **k):
            self.lib_calls.append((command, list(args)))
            self.reloaded = True
            return self.lib_result or {
                "ok": True,
                "data": {
                    "status": "ok",
                    "pid": 7,
                    "reloadModelDefinition": True,
                    "result": {"success": True},
                    "applicationState": {"hasUnsavedChanges": False},
                },
            }

        def live_sig(pm=None):
            self.live_reads += 1
            model = self.live
            if self.reloaded:
                model = self.live_after if self.live_after is not None else self.disk
            return {"ok": True, "signature": mcc.extract_signature(model)}

        monkeypatch.setattr(db, "run_bridge", fake_run)
        monkeypatch.setattr(db, "run_bridge_lib", fake_lib)
        monkeypatch.setattr(db, "_optimize_output", lambda out, *a: None)
        monkeypatch.setattr(
            mcc,
            "_read_disk_signature",
            lambda d: {
                "ok": True,
                "signature": mcc.extract_signature(self.disk),
                "udf_gate": False,
            },
        )
        monkeypatch.setattr(mcc, "_read_live_signature", live_sig)
        monkeypatch.setattr(mcc, "VERIFY_INTERVAL_S", 0.0)

    @property
    def cli_ops(self):
        return [c[0] for c in self.cli_calls]


@pytest.fixture
def proj(tmp_path):
    return _project(tmp_path)


def _metadata_edit(rig):
    rig.disk.Model.Tables[0].Measures = [meas("Total", "2"), meas("New", "3")]


# ---------------------------------------------------------------------------
# the unsaved gate comes first and has no bypass beyond discard_unsaved / save_first
# ---------------------------------------------------------------------------
def test_gate_refuses_before_classifying_or_reloading(monkeypatch, proj):
    rig = Rig(monkeypatch, [_inst(proj["pbip"], dirty=True)])
    _metadata_edit(rig)
    out = db.op_reload(pid=7, include_model=True)
    assert out["success"] is False and out["error_type"] == "unsaved_changes"
    assert rig.lib_calls == [] and rig.live_reads == 0
    assert rig.cli_ops == ["status"]


def test_ledger_live_write_refuses_even_when_the_flag_is_false(monkeypatch, proj):
    rig = Rig(monkeypatch, [_inst(proj["pbip"], dirty=False)])
    edit_ledger.record_live_write("02_Model_Operations:measure/create", project=str(proj["pbip"]))
    out = db.op_reload(pid=7, include_model=True)
    assert out["error_type"] == "unsaved_changes"
    assert out["dirty_sources"]["ledger_live_writes"] == 1
    assert rig.lib_calls == []


def test_unknown_dirty_state_refuses(monkeypatch, proj):
    inst = _inst(proj["pbip"])
    inst["hasUnsavedChanges"] = None
    rig = Rig(monkeypatch, [inst])
    out = db.op_reload(pid=7, include_model=True)
    assert out["error_type"] == "dirty_state_unknown" and rig.lib_calls == []


def test_discard_unsaved_is_the_only_override_and_is_reported(monkeypatch, proj):
    rig = Rig(monkeypatch, [_inst(proj["pbip"], dirty=True)])
    _metadata_edit(rig)
    out = db.op_reload(pid=7, include_model=True, discard_unsaved=True)
    assert out["success"] is True and out["discarded_unsaved"]["reasons"]
    assert [c[0] for c in rig.lib_calls] == ["reload-model"]


# ---------------------------------------------------------------------------
# classifier refusals: nothing is reloaded
# ---------------------------------------------------------------------------
def test_unsafe_change_refuses_and_reloads_nothing(monkeypatch, proj):
    rig = Rig(monkeypatch, [_inst(proj["pbip"])])
    rig.disk.Model.Tables[0].Partitions[0].Source.Expression = "let Source = 2 in Source"
    out = db.op_reload(pid=7, include_model=True)
    assert out["success"] is False and out["error_type"] == "model_reload_unsafe_change"
    assert "WITHOUT saving" in out["guidance"] and "open" in out["guidance"]
    assert out["unsafe_changes"][0]["category"] == "partition"
    assert rig.lib_calls == [] and "reload" not in rig.cli_ops


def test_invalid_tmdl_refuses_with_the_parser_message(monkeypatch, proj):
    rig = Rig(monkeypatch, [_inst(proj["pbip"])])
    monkeypatch.setattr(
        mcc, "_read_disk_signature", lambda d: {"ok": False, "error": "line 3: orphan property"}
    )
    out = db.op_reload(pid=7, include_model=True)
    assert out["error_type"] == "tmdl_invalid" and "orphan property" in out["error"]
    assert rig.lib_calls == []


def test_classifier_that_cannot_run_fails_closed(monkeypatch, proj):
    rig = Rig(monkeypatch, [_inst(proj["pbip"])])
    monkeypatch.setattr(
        mcc, "_read_live_signature", lambda pm=None: {"ok": False, "reason": "not connected"}
    )
    out = db.op_reload(pid=7, include_model=True)
    assert out["error_type"] == "model_change_unclassified" and rig.lib_calls == []


def test_project_identity_is_checked_against_the_target_instance(monkeypatch, proj, tmp_path):
    """The live reader is handed a matcher for THE target instance's project."""
    rig = Rig(monkeypatch, [_inst(proj["pbip"])])
    seen = {}

    def live_sig(pm=None):
        seen["mine"] = pm(str(proj["pbip"]))
        seen["other"] = pm(str(tmp_path / "Other" / "Other.pbip"))
        return {"ok": True, "signature": mcc.extract_signature(rig.live)}

    monkeypatch.setattr(mcc, "_read_live_signature", live_sig)
    db.op_reload(pid=7, include_model=True)
    assert seen == {"mine": True, "other": False}


# ---------------------------------------------------------------------------
# success path
# ---------------------------------------------------------------------------
def test_metadata_only_reload_applies_verifies_and_clears_the_tmdl_ledger(monkeypatch, proj):
    rig = Rig(monkeypatch, [_inst(proj["pbip"])])
    _metadata_edit(rig)
    edit_ledger.record_disk_write(str(proj["visual"]))
    edit_ledger.record_disk_write(str(proj["tmdl"]))
    assert {
        e["kind"]
        for e in edit_ledger.pending_disk_edits([str(proj["report"])], [str(proj["model"])])
    } == {"pbir", "tmdl"}

    out = db.op_reload(pid=7, include_model=True)
    assert out["success"] is True and out["include_model"] is True
    assert [c[0] for c in rig.lib_calls] == ["reload-model"]
    assert rig.lib_calls[0][1][:2] == ["--pid", "7"]
    assert "reload" not in rig.cli_ops  # the CLI's reloadModelDefinition:false reload never ran
    mr = out["model_reload"]
    assert mr["classification"] == "metadata_only" and mr["verification"] == "verified"
    assert sorted(mr["applied"]) == ["measure Sales[New] added", "measure Sales[Total] changed"]
    assert mr["mismatch"] == []
    assert any("Data pane" in n for n in mr["follow_up"])
    assert "tmdl_edits_not_applied" not in out
    assert edit_ledger.pending_disk_edits([str(proj["report"])], [str(proj["model"])]) == []


def test_calculated_change_adds_the_calculate_refresh_note(monkeypatch, proj):
    rig = Rig(monkeypatch, [_inst(proj["pbip"])])
    from tests._tom_fakes import col

    rig.disk.Model.Tables[0].Columns.append(col("Calc", "Calculated", "Double", expr="[Amount]*2"))
    out = db.op_reload(pid=7, include_model=True)
    assert out["success"] is True
    assert "refresh_type=calculate" in out["model_reload"]["follow_up"][0]


def test_mismatch_after_reload_keeps_the_tmdl_ledger_and_says_so(monkeypatch, proj):
    rig = Rig(monkeypatch, [_inst(proj["pbip"])])
    _metadata_edit(rig)
    stale = base()
    stale.Model.Tables[0].Measures = [meas("Total", "2")]  # 'New' never showed up
    rig.live_after = stale
    edit_ledger.record_disk_write(str(proj["tmdl"]))
    out = db.op_reload(pid=7, include_model=True)
    assert out["success"] is True
    assert out["model_reload"]["verification"] == "mismatch"
    assert out["model_reload"]["mismatch"] == ["measure Sales[New] added"]
    assert out["tmdl_edits_not_applied"] and "still differs from disk" in out["tmdl_warning"]
    left = edit_ledger.pending_disk_edits([str(proj["report"])], [str(proj["model"])])
    assert [e["kind"] for e in left] == ["tmdl"]


def test_unreadable_model_after_reload_is_reported_not_verified(monkeypatch, proj):
    rig = Rig(monkeypatch, [_inst(proj["pbip"])])
    _metadata_edit(rig)
    calls = {"n": 0}

    def flaky(pm=None):
        calls["n"] += 1
        if rig.reloaded:
            return {"ok": False, "reason": "connection dropped"}
        return {"ok": True, "signature": mcc.extract_signature(rig.live)}

    monkeypatch.setattr(mcc, "_read_live_signature", flaky)
    out = db.op_reload(pid=7, include_model=True)
    assert out["success"] is True and out["model_reload"]["verification"] == "unavailable"
    assert "verify with 04_Run_DAX" in out["model_reload"]["verification_note"]


def test_plain_reload_is_unchanged_and_never_uses_the_library_helper(monkeypatch, proj):
    rig = Rig(monkeypatch, [_inst(proj["pbip"])])
    out = db.op_reload(pid=7)
    assert out["success"] is True and "include_model" not in out
    assert rig.lib_calls == [] and "reload" in rig.cli_ops


# ---------------------------------------------------------------------------
# errors from Desktop: parse the envelope, never the exit code
# ---------------------------------------------------------------------------
def _lib_error(code, message, **extra):
    err = {"message": message, "code": code, "retryable": False, "details": {}, **extra}
    return {
        "ok": False,
        "error_type": "cli_error",
        "error": "bridge library helper `reload-model` reported an error",
        "data": {"status": "error", "error": err},
    }


def test_desktop_tmdl_parse_error_is_model_reload_failed(monkeypatch, proj):
    rig = Rig(monkeypatch, [_inst(proj["pbip"])])
    _metadata_edit(rig)
    rig.lib_result = _lib_error(
        "BRIDGE_ERROR",
        "Line 12: unexpected property 'foo'",
        jsonRpcCode=-32505,
        bridgeCode="RENDER_FAILED",
        retryable=True,
    )
    out = db.op_reload(pid=7, include_model=True)
    assert out["success"] is False and out["error_type"] == "model_reload_failed"
    assert "unexpected property 'foo'" in out["error"] and "atomic" in out["error"]
    assert out["cli_jsonrpc_code"] == -32505 and out["include_model"] is True
    # the tmdl ledger is untouched (nothing was applied)
    edit_ledger.record_disk_write(str(proj["tmdl"]))
    assert edit_ledger.pending_disk_edits([], [str(proj["model"])])


def test_reload_rejected_by_desktop_is_reload_failed(monkeypatch, proj):
    rig = Rig(monkeypatch, [_inst(proj["pbip"])])
    rig.lib_result = _lib_error(
        "RELOAD_REJECTED",
        "Power BI Desktop rejected the model reload.",
        details={"result": {"success": False, "error": "bad"}},
    )
    out = db.op_reload(pid=7, include_model=True)
    assert out["error_type"] == "reload_failed"


def test_method_not_available_is_its_own_error(monkeypatch, proj):
    rig = Rig(monkeypatch, [_inst(proj["pbip"])])
    rig.lib_result = _lib_error("METHOD_NOT_AVAILABLE", "Desktop does not expose file.reload/v1")
    out = db.op_reload(pid=7, include_model=True)
    assert out["error_type"] == "bridge_method_unavailable"


def test_helper_unavailable_error_passes_through(monkeypatch, proj):
    rig = Rig(monkeypatch, [_inst(proj["pbip"])])
    rig.lib_result = {
        "ok": False,
        "error_type": "bridge_lib_unavailable",
        "error": "needs >= 1.0.0",
    }
    out = db.op_reload(pid=7, include_model=True)
    assert out["error_type"] == "bridge_lib_unavailable" and out["include_model"] is True


# ---------------------------------------------------------------------------
# reload_and_screenshot + handler plumbing
# ---------------------------------------------------------------------------
def test_reload_and_screenshot_passes_include_model_and_skips_the_shot_on_refusal(
    monkeypatch, proj, tmp_path
):
    rig = Rig(monkeypatch, [_inst(proj["pbip"])])
    rig.disk.Model.Tables[0].Partitions[0].Source.Expression = "changed"
    out = db.op_reload_and_screenshot(
        page="Overview", output=str(tmp_path / "o.png"), pid=7, include_model=True
    )
    assert out["error_type"] == "model_reload_unsafe_change"
    assert "screenshot" not in rig.cli_ops


def test_reload_and_screenshot_with_include_model_reloads_then_shoots(monkeypatch, proj, tmp_path):
    rig = Rig(monkeypatch, [_inst(proj["pbip"])])
    _metadata_edit(rig)
    out = db.op_reload_and_screenshot(
        page="Overview", output=str(tmp_path / "o.png"), pid=7, include_model=True, settle_ms=0
    )
    assert out["success"] is True and out["reload"]["include_model"] is True
    assert rig.cli_ops.index("screenshot") > 0 and [c[0] for c in rig.lib_calls] == ["reload-model"]


@pytest.mark.parametrize(
    "value,expected", [(True, True), ("true", True), ("false", False), (None, False)]
)
def test_handler_passes_include_model(monkeypatch, value, expected):
    seen = {}
    monkeypatch.setattr(db, "op_reload", lambda **k: seen.update(k) or {"success": True})
    args = {"operation": "reload", "pid": 7}
    if value is not None:
        args["include_model"] = value
    h.handle_desktop_bridge(args)
    assert seen["include_model"] is expected
    seen2 = {}
    monkeypatch.setattr(
        db, "op_reload_and_screenshot", lambda **k: seen2.update(k) or {"success": True}
    )
    h.handle_desktop_bridge(
        {**args, "operation": "reload_and_screenshot", "page": "P", "output": "o.png"}
    )
    assert seen2["include_model"] is expected


def test_schema_declares_include_model_and_defers_it_from_tools_list():
    from server.registry import HandlerRegistry
    from server.tool_surface import deferred_args

    reg = HandlerRegistry()
    h.register_desktop_bridge_handler(reg)
    td = reg._handlers["17_Desktop_Bridge"]
    assert td.input_schema["properties"]["include_model"]["type"] == "boolean"
    assert "include_model" in deferred_args("17_Desktop_Bridge")
    assert "include_model=true" in td.description


# ---------------------------------------------------------------------------
# hints: a plain reload / a TMDL edit point at include_model when it applies
# ---------------------------------------------------------------------------
def test_plain_reload_with_pending_tmdl_recommends_include_model_only_when_metadata_only(
    monkeypatch, proj
):
    rig = Rig(monkeypatch, [_inst(proj["pbip"])])
    _metadata_edit(rig)
    edit_ledger.record_disk_write(str(proj["tmdl"]))
    out = db.op_reload(pid=7)
    assert out["tmdl_edits_not_applied"]
    assert out["model_reload_hint"]["classification"] == "metadata_only"
    assert "re-run with include_model=true" in out["tmdl_warning"]

    edit_ledger.reset()
    rig.disk.Model.Tables[0].Partitions[0].Source.Expression = "changed"
    edit_ledger.record_disk_write(str(proj["tmdl"]))
    out = db.op_reload(pid=7)
    assert out["model_reload_hint"]["error_type"] == "model_reload_unsafe_change"
    assert "re-run with include_model=true" not in out["tmdl_warning"]
    assert "operation=open" in out["tmdl_warning"]


def test_tmdl_write_hint_offers_the_classified_model_reload():
    resp = reload_hints.attach_reload_hint({"success": True}, "02_TMDL_Operations", "bulk_rename")
    assert resp["include_model_reload"] is True
    assert resp["reload_op_chain"][0].endswith("include_model=true (metadata-only TMDL edits)")
    assert resp["reload_op_chain"][-1] == "17_Desktop_Bridge:open"
    assert (
        "include_model=true" in resp["reload_hint"]
        and "model_reload_unsafe_change" in resp["reload_hint"]
    )
    # a PBIR edit hint is unchanged
    pb = reload_hints.attach_reload_hint({"success": True}, "07_Visual_Operations", "add_field")
    assert "include_model_reload" not in pb


# ---------------------------------------------------------------------------
# _exec_bridge_lib: parse the envelope, ignore the exit code
# ---------------------------------------------------------------------------
class _Proc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


@pytest.fixture
def lib_target(monkeypatch):
    monkeypatch.setattr(
        db,
        "_bridge_lib_target",
        lambda: {"node": "node", "package_dir": "/pkg", "version": "1.0.0"},
    )


def test_exec_bridge_lib_error_envelope_with_exit_zero_is_a_failure(monkeypatch, lib_target):
    env = {"status": "error", "error": {"message": "RENDER_FAILED line 4", "code": "BRIDGE_ERROR"}}
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(json.dumps(env), returncode=0))
    res = db._exec_bridge_lib("reload-model", ["--pid", "7"], timeout=5)
    assert res["ok"] is False and res["data"] == env


def test_exec_bridge_lib_ok_envelope_with_nonzero_exit_is_still_ok(monkeypatch, lib_target):
    env = {"status": "ok", "result": {"success": True}}
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(json.dumps(env), returncode=1))
    assert db._exec_bridge_lib("reload-model", [], timeout=5) == {"ok": True, "data": env}


def test_exec_bridge_lib_empty_or_bad_output(monkeypatch, lib_target):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc("", "boom", 1))
    assert db._exec_bridge_lib("x", [], timeout=5)["error_type"] == "cli_no_output"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc("not json"))
    assert db._exec_bridge_lib("x", [], timeout=5)["error_type"] == "bad_output"


def test_exec_bridge_lib_runs_node_directly_without_a_shell(monkeypatch, lib_target):
    seen = {}

    def fake_run(argv, **k):
        seen.update(argv=argv, **k)
        return _Proc('{"status":"ok"}')

    monkeypatch.setattr(subprocess, "run", fake_run)
    db._exec_bridge_lib("reload-model", ["--pid", "7"], timeout=5)
    assert seen["shell"] is False and seen["argv"][0] == "node"
    assert seen["argv"][1].endswith("bridge_lib.mjs") and seen["argv"][2] == "reload-model"
    assert seen["argv"][3:5] == ["--package-dir", "/pkg"]


def test_bridge_lib_target_requires_cli_1_0_0_and_node_mode(monkeypatch):
    def cli(version, mode="node"):
        return {
            "argv0": ["node", "/p/dist/cli.js"] if mode == "node" else ["x.cmd"],
            "mode": mode,
            "script": "/p/dist/cli.js" if mode == "node" else None,
            "version": version,
        }

    monkeypatch.setattr(db, "_resolve_cli", lambda: cli("0.1.1"))
    assert db._bridge_lib_target()["error_type"] == "bridge_lib_unavailable"
    monkeypatch.setattr(db, "_resolve_cli", lambda: cli(None))
    assert db._bridge_lib_target()["error_type"] == "bridge_lib_unavailable"
    monkeypatch.setattr(db, "_resolve_cli", lambda: cli("1.0.0", mode="shim"))
    assert db._bridge_lib_target()["error_type"] == "bridge_lib_unavailable"
    monkeypatch.setattr(
        db, "_resolve_cli", lambda: {"error_type": "cli_not_installed", "error": "x"}
    )
    assert db._bridge_lib_target()["error_type"] == "cli_not_installed"


# ---------------------------------------------------------------------------
# the REAL helper through real node, against a fake bridge-CLI package
# ---------------------------------------------------------------------------
@pytest.fixture
def fake_cli_pkg(tmp_path, monkeypatch):
    return install_fake_cli(tmp_path, monkeypatch)


def _log(fake):
    if not fake["log"].exists():
        return []
    return [json.loads(x) for x in fake["log"].read_text(encoding="utf-8").splitlines()]


def test_helper_reload_model_sends_reload_model_definition_true(fake_cli_pkg):
    res = db._exec_bridge_lib("reload-model", ["--pid", "7", "--wait-ms", "0"], timeout=30)
    assert res["ok"] is True, res
    data = res["data"]
    assert data["status"] == "ok" and data["reloadModelDefinition"] is True
    assert data["applicationState"] == {"currentFilePath": "x.pbip", "hasUnsavedChanges": False}
    sent = [e for e in _log(fake_cli_pkg) if "method" in e]
    assert sent == [{"method": "file.reload/v1", "args": {"reloadModelDefinition": True}}]
    assert any(e.get("dispose") for e in _log(fake_cli_pkg))


def test_helper_reports_a_desktop_error_as_an_envelope_with_exit_zero(fake_cli_pkg, monkeypatch):
    monkeypatch.setenv("FAKE_LIB_MODE", "parse_error")
    target = db._bridge_lib_target()
    raw = subprocess.run(
        [
            target["node"],
            str(db.BRIDGE_LIB_HELPER),
            "reload-model",
            "--package-dir",
            target["package_dir"],
            "--pid",
            "7",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert raw.returncode == 0  # the exit code carries nothing; the envelope does
    env = json.loads(raw.stdout.strip().splitlines()[-1])
    assert env["status"] == "error"
    assert env["error"]["jsonRpcCode"] == -32505 and env["error"]["bridgeCode"] == "RENDER_FAILED"
    res = db._exec_bridge_lib("reload-model", ["--pid", "7"], timeout=30)
    assert res["ok"] is False and res["data"]["error"]["message"].startswith("Line 12")


def test_helper_maps_success_false_to_reload_rejected(fake_cli_pkg, monkeypatch):
    monkeypatch.setenv("FAKE_LIB_MODE", "reject")
    res = db._exec_bridge_lib("reload-model", ["--pid", "7"], timeout=30)
    assert res["ok"] is False and res["data"]["error"]["code"] == "RELOAD_REJECTED"
    out = db._cli_err(res, pid=7)
    assert out["error_type"] == "reload_failed"


def test_helper_requires_file_reload_in_the_manifest(fake_cli_pkg, monkeypatch):
    monkeypatch.setenv("FAKE_LIB_METHODS", "application.state.get/v1")
    res = db._exec_bridge_lib("reload-model", ["--pid", "7"], timeout=30)
    assert res["ok"] is False and res["data"]["error"]["code"] == "METHOD_NOT_AVAILABLE"
    assert db._cli_err(res)["error_type"] == "bridge_method_unavailable"
    assert not [e for e in _log(fake_cli_pkg) if "method" in e]  # nothing was sent


# Runs the real node helper, but only with arguments it rejects: nothing reaches a Desktop.
@pytest.mark.live_seam
def test_helper_rejects_unknown_commands_and_missing_package(fake_cli_pkg):
    target = db._bridge_lib_target()
    raw = subprocess.run(
        [target["node"], str(db.BRIDGE_LIB_HELPER), "nope", "--package-dir", target["package_dir"]],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert json.loads(raw.stdout.strip())["status"] == "error"
    raw = subprocess.run(
        [
            target["node"],
            str(db.BRIDGE_LIB_HELPER),
            "reload-model",
            "--package-dir",
            "/no/such/dir",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert json.loads(raw.stdout.strip())["status"] == "error"
