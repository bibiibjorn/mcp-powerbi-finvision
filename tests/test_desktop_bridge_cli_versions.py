"""Desktop Bridge CLI version compatibility (0.1.x and >= 1.0.0).

Bridge CLI 0.2.0 moved the CLI from ``dist/index.js`` to ``dist/cli.js`` (index.js
became a silent library entry) and 1.0.0 reports a rejected reload as a
``RELOAD_REJECTED`` error envelope with a non-zero exit. These tests pin:

* the resolver reads the entry from the installed package (``package.json`` bin,
  shim text, ``dist/cli.js``) and never runs the library ``dist/index.js`` of a
  CLI >= 0.2.0; the cache follows an in-session upgrade (package.json mtime);
* exit 0 with empty stdout is an error (``cli_no_output``), not "no instances";
* the 1.0.0 error codes map to the same error types the 0.x shapes produced;
* ``pagesError`` on a status instance is tolerated and surfaced.

Fully offline: fake npm prefixes on tmp_path, ``subprocess.run`` / ``run_bridge``
faked. Never runs npm, the real CLI or a Desktop.
"""

from __future__ import annotations

import json
import os
import types
from pathlib import Path

import pytest

from core.autonomous import desktop_bridge as db

# What npm writes into powerbi-desktop.cmd for the 0.1.1 package (last line).
_SHIM_TMPL = (
    "@ECHO off\r\nGOTO start\r\n:start\r\nSETLOCAL\r\n"
    'endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  '
    '"%dp0%\\node_modules\\@microsoft\\powerbi-desktop-bridge-cli\\dist\\{entry}" %*\r\n'
)
_PLAIN_SHIM = "@echo off\r\necho ARGS=[%*]\r\n"  # names no script


def _prefix(
    tmp_path: Path,
    *,
    version=None,
    bin_value=None,
    files=(),
    shim=_PLAIN_SHIM,
    with_package_dir=True,
):
    """Fake global npm prefix. Returns (prefix, package dir)."""
    prefix = tmp_path / "npm"
    prefix.mkdir()
    (prefix / "powerbi-desktop.cmd").write_text(shim, encoding="utf-8")
    pkg = prefix / "node_modules" / "@microsoft" / "powerbi-desktop-bridge-cli"
    if with_package_dir:
        pkg.mkdir(parents=True)
    if version is not None:
        doc = {"name": db.CLI_PACKAGE, "version": version}
        if bin_value is not None:
            doc["bin"] = bin_value
        (pkg / "package.json").write_text(json.dumps(doc), encoding="utf-8")
    for rel in files:
        f = pkg / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("// stub", encoding="utf-8")
    return prefix, pkg


@pytest.fixture
def use_prefix(monkeypatch):
    def _use(prefix: Path):
        monkeypatch.delenv("MCP_PBI_BRIDGE_CLI_JS", raising=False)
        monkeypatch.setattr(
            db.shutil,
            "which",
            lambda n: (
                str(prefix / "powerbi-desktop.cmd") if n == db.CLI_NAME else "C:/fake/node.exe"
            ),
        )
        db.reset_cli_cache()
        return db._resolve_cli()

    return _use


def _tail(cli: dict) -> str:
    return cli["script"].replace("\\", "/").rsplit("dist/", 1)[-1]


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------
def test_resolve_cli_uses_package_json_bin(tmp_path, use_prefix):
    """1.0.0: bin -> dist/cli.js wins even though the (stale) shim text names index.js."""
    prefix, _ = _prefix(
        tmp_path,
        version="1.0.0",
        bin_value={"powerbi-desktop": "dist/cli.js"},
        files=["dist/index.js", "dist/cli.js"],
        shim=_SHIM_TMPL.format(entry="index.js"),
    )
    cli = use_prefix(prefix)
    assert cli["mode"] == "node"
    assert _tail(cli) == "cli.js"
    assert cli["version"] == "1.0.0"
    assert cli["resolved_via"] == "package_bin"


@pytest.mark.parametrize(
    "bin_value", ["dist/index.js", {"powerbi-desktop": "dist/index.js"}], ids=["str", "dict"]
)
def test_resolve_cli_0x_layout_still_works(tmp_path, use_prefix, bin_value):
    prefix, _ = _prefix(
        tmp_path,
        version="0.1.1",
        bin_value=bin_value,
        files=["dist/index.js"],
        shim=_SHIM_TMPL.format(entry="index.js"),
    )
    cli = use_prefix(prefix)
    assert cli["mode"] == "node" and _tail(cli) == "index.js"
    assert cli["version"] == "0.1.1" and cli["resolved_via"] == "package_bin"


def test_resolve_cli_0x_without_package_json_uses_shim_text_then_index(tmp_path, use_prefix):
    prefix, _ = _prefix(tmp_path, files=["dist/index.js"], shim=_SHIM_TMPL.format(entry="index.js"))
    cli = use_prefix(prefix)
    assert _tail(cli) == "index.js" and cli["resolved_via"] == "shim_text"
    assert cli["version"] is None


def test_resolve_cli_0x_index_fallback_needs_old_version_or_no_package_json(tmp_path, use_prefix):
    # Shim names nothing; package.json says 0.1.1 without a usable bin -> index.js is fine.
    prefix, _ = _prefix(tmp_path, version="0.1.1", files=["dist/index.js"])
    cli = use_prefix(prefix)
    assert _tail(cli) == "index.js" and cli["resolved_via"] == "dist_index_legacy"


def test_resolve_cli_falls_back_to_dist_cli(tmp_path, use_prefix):
    prefix, _ = _prefix(
        tmp_path,
        version="1.0.0",
        files=["dist/index.js", "dist/cli.js"],
        shim=_SHIM_TMPL.format(entry="index.js"),
    )
    cli = use_prefix(prefix)
    assert _tail(cli) == "cli.js" and cli["resolved_via"] == "dist_cli"


@pytest.mark.parametrize(
    "shim", [_PLAIN_SHIM, _SHIM_TMPL.format(entry="index.js")], ids=["no-target", "stale-target"]
)
def test_resolve_cli_never_picks_library_index_for_v1(tmp_path, use_prefix, shim):
    prefix, _ = _prefix(tmp_path, version="1.0.0", files=["dist/index.js"], shim=shim)
    cli = use_prefix(prefix)
    assert cli["mode"] == "shim"
    assert cli["script"] is None
    assert cli["version"] == "1.0.0"
    assert cli["argv0"] == [str(prefix / "powerbi-desktop.cmd")]


def test_resolve_cli_ignores_bin_outside_the_package(tmp_path, use_prefix):
    prefix, _ = _prefix(
        tmp_path, version="1.0.0", bin_value="../../../evil.js", files=["dist/cli.js"]
    )
    (prefix / "evil.js").write_text("// evil", encoding="utf-8")
    cli = use_prefix(prefix)
    assert _tail(cli) == "cli.js" and cli["resolved_via"] == "dist_cli"


def test_shim_target_regex_parses_cmd_and_sh_style_shims():
    m = db._SHIM_TARGET_RE.search(_SHIM_TMPL.format(entry="cli.js"))
    assert m and m.group(1) == "dist\\cli.js"
    ps1 = '& "$basedir/node_modules/@microsoft/powerbi-desktop-bridge-cli/dist/index.js" $args'
    m = db._SHIM_TARGET_RE.search(ps1)
    assert m and m.group(1) == "dist/index.js"
    assert db._SHIM_TARGET_RE.search(_PLAIN_SHIM) is None


def test_resolve_cli_parses_cmd_shim_target(tmp_path, use_prefix):
    """No package.json at all: the script the shim runs is used (custom entry name)."""
    prefix, _ = _prefix(tmp_path, files=["dist/cli.js"], shim=_SHIM_TMPL.format(entry="cli.js"))
    cli = use_prefix(prefix)
    assert _tail(cli) == "cli.js" and cli["resolved_via"] == "shim_text"


def test_cli_cache_invalidates_on_package_json_mtime(tmp_path, use_prefix):
    """An in-session `npm i -g` upgrade is picked up without reset_cli_cache()."""
    prefix, pkg = _prefix(
        tmp_path,
        version="0.1.1",
        bin_value="dist/index.js",
        files=["dist/index.js"],
        shim=_SHIM_TMPL.format(entry="index.js"),
    )
    before = use_prefix(prefix)
    assert _tail(before) == "index.js" and before["version"] == "0.1.1"
    assert db._resolve_cli() is before  # cached while nothing changed

    (pkg / "dist" / "cli.js").write_text("// cli", encoding="utf-8")
    pj = pkg / "package.json"
    pj.write_text(
        json.dumps(
            {"name": db.CLI_PACKAGE, "version": "1.0.0", "bin": {"powerbi-desktop": "dist/cli.js"}}
        ),
        encoding="utf-8",
    )
    st = pj.stat()
    os.utime(pj, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    after = db._resolve_cli()  # no reset_cli_cache()
    assert _tail(after) == "cli.js" and after["version"] == "1.0.0"


def test_reset_cli_cache_still_forces_re_resolution(tmp_path, use_prefix):
    prefix, _ = _prefix(tmp_path, version="1.0.0", files=["dist/cli.js"])
    first = use_prefix(prefix)
    db.reset_cli_cache()
    again = db._resolve_cli()
    assert again == first and again is not first


def test_env_override_wins_and_reports_the_package_version(tmp_path, monkeypatch, use_prefix):
    prefix, pkg = _prefix(
        tmp_path, version="1.0.0", bin_value="dist/cli.js", files=["dist/cli.js", "dist/other.js"]
    )
    use_prefix(prefix)
    monkeypatch.setenv("MCP_PBI_BRIDGE_CLI_JS", str(pkg / "dist" / "other.js"))
    db.reset_cli_cache()
    cli = db._resolve_cli()
    assert _tail(cli) == "other.js"
    assert cli["resolved_via"] == "env_override" and cli["version"] == "1.0.0"


# ---------------------------------------------------------------------------
# run_bridge / manifest / status: exit 0 + empty stdout is an error
# ---------------------------------------------------------------------------
_FAKE_LIB_CLI = {
    "argv0": ["node", "C:/npm/pkg/dist/index.js"],
    "mode": "node",
    "script": "C:/npm/pkg/dist/index.js",
    "version": "1.0.0",
    "resolved_via": "shim_text",
}


def _silent_run(monkeypatch, *, stdout="", returncode=0):
    monkeypatch.setattr(db, "_resolve_cli", lambda: dict(_FAKE_LIB_CLI))
    monkeypatch.setattr(
        db.subprocess,
        "run",
        lambda *a, **k: types.SimpleNamespace(stdout=stdout, stderr="", returncode=returncode),
    )


@pytest.mark.parametrize("stdout", ["", "  \n"])
def test_run_bridge_empty_stdout_exit0_is_error(monkeypatch, stdout):
    _silent_run(monkeypatch, stdout=stdout)
    out = db.run_bridge(["status"])
    assert out["ok"] is False and out["error_type"] == "cli_no_output"
    assert "C:/npm/pkg/dist/index.js" in out["hint"] and "library" in out["hint"]
    assert out["bridge_cli_script"] == "C:/npm/pkg/dist/index.js"
    assert out["bridge_cli_version"] == "1.0.0"


def test_run_bridge_empty_stdout_nonzero_exit_stays_cli_error(monkeypatch):
    _silent_run(monkeypatch, stdout="", returncode=2)
    assert db.run_bridge(["status"])["error_type"] == "cli_error"


def test_run_bridge_json_payload_still_ok(monkeypatch):
    _silent_run(monkeypatch, stdout="{}")
    assert db.run_bridge(["status"]) == {"ok": True, "data": {}}


def test_op_manifest_explicit_pid_empty_output_fails(monkeypatch):
    """Regression: the pid fast path used to return success with empty data."""
    _silent_run(monkeypatch)
    out = db.op_manifest(pid=4242)
    assert out["success"] is False and out["error_type"] == "cli_no_output"
    assert out["pid"] == 4242
    assert out["bridge_cli_script"] == "C:/npm/pkg/dist/index.js"
    assert db.bridge_supports("file.reload/v1", pid=4242) is None


def test_op_status_empty_output_is_not_reported_as_not_connected(monkeypatch):
    _silent_run(monkeypatch)
    out = db.op_status(wait_seconds=0)
    assert out["success"] is False and out["error_type"] == "cli_no_output"


# ---------------------------------------------------------------------------
# op_status / op_manifest expose the resolved CLI
# ---------------------------------------------------------------------------
def _cli_info(monkeypatch, version, script="C:/npm/pkg/dist/cli.js"):
    monkeypatch.setattr(
        db,
        "_resolve_cli",
        lambda: {"argv0": ["node", script], "mode": "node", "script": script, "version": version},
    )


def test_status_and_manifest_report_cli_version_and_script(monkeypatch):
    _cli_info(monkeypatch, "1.0.0")
    monkeypatch.setattr(
        db,
        "run_bridge",
        lambda argv, **k: {
            "ok": True,
            "data": (
                {"status": "ready", "instances": []}
                if argv[0] == "status"
                else {
                    "status": "ok",
                    "pid": 9,
                    "manifest": {"methods": [{"name": "file.reload/v1"}]},
                }
            ),
        },
    )
    st = db.op_status(wait_seconds=0)
    assert st["bridge_cli_version"] == "1.0.0"
    assert st["bridge_cli_script"] == "C:/npm/pkg/dist/cli.js"
    assert "warnings" not in st
    mf = db.op_manifest(pid=9)
    assert mf["bridge_cli_version"] == "1.0.0" and mf["methods"] == ["file.reload/v1"]
    assert "warnings" not in mf


def test_newer_major_than_tested_adds_a_warning(monkeypatch):
    _cli_info(monkeypatch, "2.0.0")
    monkeypatch.setattr(
        db,
        "run_bridge",
        lambda argv, **k: {"ok": True, "data": {"status": "ready", "instances": []}},
    )
    st = db.op_status(wait_seconds=0)
    assert st["success"] is True
    assert any("2.0.0" in w and "newer" in w for w in st["warnings"])


# ---------------------------------------------------------------------------
# Error-code mapping
# ---------------------------------------------------------------------------
def _envelope(code, message="m", *, details=None, **extra):
    err = {"code": code, "message": message, "details": details or {}, **extra}
    return {
        "ok": False,
        "error_type": "cli_error",
        "error": "`powerbi-desktop x` exited 1",
        "data": {"status": "error", "error": err},
        "returncode": 1,
    }


def test_cli_err_maps_connection_lost_with_retry_hints():
    out = db._cli_err(_envelope("CONNECTION_LOST", retryable=True, retryAfterMs=500), pid=3)
    assert out["error_type"] == "bridge_connection_lost"
    assert out["cli_error_code"] == "CONNECTION_LOST"
    assert out["retryable"] is True and out["retryAfterMs"] == 500
    assert out["pid"] == 3


def test_cli_err_maps_ambiguous_desktop_instance():
    out = db._cli_err(_envelope("AMBIGUOUS_DESKTOP_INSTANCE"))
    assert out["error_type"] == "ambiguous_instance"


@pytest.mark.parametrize(
    "code",
    [
        "AMBIGUOUS_OWNING_PBIP",
        "OWNING_PBIP_NOT_FOUND",
        "OWNING_PBIP_UNREADABLE",
        "REPORT_PARENT_DIR_UNREADABLE",
    ],
)
def test_cli_err_passes_other_codes_through(code):
    out = db._cli_err(_envelope(code, "detail text"))
    assert out["error_type"] == "cli_error"
    assert out["cli_error_code"] == code and out["cli_error_message"] == "detail text"


# ---------------------------------------------------------------------------
# Reload: 0.x result.success=false vs 1.0.0 RELOAD_REJECTED
# ---------------------------------------------------------------------------
def _pbip(tmp_path: Path) -> Path:
    root = tmp_path / "Proj"
    (root / "Proj.Report" / "definition").mkdir(parents=True)
    (root / "Model.SemanticModel" / "definition").mkdir(parents=True)
    pbip = root / "Proj.pbip"
    pbip.write_text(json.dumps({"artifacts": [{"report": {"path": "Proj.Report"}}]}), "utf-8")
    (root / "Proj.Report" / "definition.pbir").write_text(
        json.dumps({"datasetReference": {"byPath": {"path": "../Model.SemanticModel"}}}), "utf-8"
    )
    return pbip


def _inst(pbip: Path, **extra):
    d = {
        "pid": 7,
        "bridgeStatus": "connected",
        "currentFilePath": str(pbip),
        "hasUnsavedChanges": False,
        "pages": [{"id": "p1", "displayName": "Overview"}],
    }
    d.update(extra)
    return d


def _fake_reload_cli(monkeypatch, pbip, reload_res, calls):
    def fake_run(argv, **k):
        calls.append(list(argv))
        if argv[0] == "status":
            return {"ok": True, "data": {"status": "ready", "instances": [_inst(pbip)]}}
        if argv[0] == "reload":
            return reload_res
        return {"ok": True, "data": {"status": "ok"}}

    monkeypatch.setattr(db, "run_bridge", fake_run)
    monkeypatch.setattr(db, "_optimize_output", lambda out, *a: None)


_LEGACY_REJECTED = {
    "ok": True,
    "data": {"status": "ok", "pid": 7, "result": {"success": False, "error": "bad"}},
}
_V1_REJECTED = _envelope(
    "RELOAD_REJECTED",
    "Power BI Desktop rejected the report reload.",
    details={
        "pid": 7,
        "reportDir": "C:/x/Proj.Report",
        "result": {"success": False, "error": "bad"},
    },
)


def test_reload_rejected_v1_envelope(monkeypatch, tmp_path):
    pbip = _pbip(tmp_path)
    calls = []
    _fake_reload_cli(monkeypatch, pbip, _V1_REJECTED, calls)
    out = db.op_reload(pid=7)
    assert out["success"] is False
    assert out["error_type"] == "reload_failed"
    assert out["cli_error_code"] == "RELOAD_REJECTED"
    assert out["result"] == {"success": False, "error": "bad"}
    assert "PREVIOUS state" in out["error"] and "bad" in out["error"]
    assert out["pid"] == 7

    out2 = db.op_reload_and_screenshot(page="Overview", output=str(tmp_path / "o.png"), pid=7)
    assert out2["error_type"] == "reload_failed"
    assert "screenshot" not in [c[0] for c in calls]


def test_reload_rejected_legacy_shape_and_v1_give_the_same_guidance(monkeypatch, tmp_path):
    pbip = _pbip(tmp_path)
    _fake_reload_cli(monkeypatch, pbip, _LEGACY_REJECTED, [])
    legacy = db.op_reload(pid=7)
    _fake_reload_cli(monkeypatch, pbip, _V1_REJECTED, [])
    v1 = db.op_reload(pid=7)
    assert legacy["error_type"] == v1["error_type"] == "reload_failed"
    assert legacy["error"] == v1["error"]
    assert legacy["result"] == v1["result"]
    assert "cli_error_code" not in legacy


def test_reload_rejected_v1_without_result_still_explains(monkeypatch, tmp_path):
    pbip = _pbip(tmp_path)
    bare = _envelope("RELOAD_REJECTED", "Power BI Desktop rejected the report reload.")
    _fake_reload_cli(monkeypatch, pbip, bare, [])
    out = db.op_reload(pid=7)
    assert out["error_type"] == "reload_failed"
    assert "rejected the report reload" in out["error"] and "PREVIOUS state" in out["error"]
    assert "result" not in out


def test_debug_loop_reload_receives_the_specific_message(monkeypatch, tmp_path):
    from core.autonomous.loops import debug_loop

    pbip = _pbip(tmp_path)
    _fake_reload_cli(monkeypatch, pbip, _V1_REJECTED, [])
    paths = types.SimpleNamespace(pbip_file=pbip)
    cfg = types.SimpleNamespace(pbip_path=str(pbip), wait_timeout_seconds=30)
    out = debug_loop._reload(paths, cfg)
    assert out["success"] is False
    assert "Desktop rejected the reload" in out["error"] and "PREVIOUS state" in out["error"]


# ---------------------------------------------------------------------------
# pagesError (1.0.0, additive on a connected instance)
# ---------------------------------------------------------------------------
_PAGES_ERROR = {
    "code": "PAGES_JSON_NOT_FOUND",
    "message": "pages.json is missing",
    "retryable": False,
}


def _status_with_pages_error(monkeypatch, tmp_path):
    inst = _inst(_pbip(tmp_path), pagesError=_PAGES_ERROR)
    del inst["pages"]
    monkeypatch.setattr(
        db,
        "run_bridge",
        lambda argv, **k: {"ok": True, "data": {"status": "ready", "instances": [inst]}},
    )
    return inst


def test_status_tolerates_pagesError_field(monkeypatch, tmp_path):
    _status_with_pages_error(monkeypatch, tmp_path)
    out = db.op_status(wait_seconds=0)
    assert out["success"] is True
    assert out["instances"][0]["pagesError"] == _PAGES_ERROR
    assert any("pid 7" in w and "pages.json is missing" in w for w in out["warnings"])


@pytest.mark.parametrize("pages", [None, []])
def test_resolve_page_with_pagesError_reports_page_not_found_with_the_cause(
    monkeypatch, tmp_path, pages
):
    inst = _status_with_pages_error(monkeypatch, tmp_path)
    inst["pages"] = pages
    out = db.op_resolve_page(page="Overview", pid=7)
    assert out["success"] is False and out["error_type"] == "page_not_found"
    assert out["available"] == []
    assert out["pages_error"] == _PAGES_ERROR
