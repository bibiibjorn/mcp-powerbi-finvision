"""L-13 (remainder) / A-19: one branchable error envelope from every tool.

Wave 2 normalized the envelope at the dispatcher (``normalize_error_envelope``
infers a type from the message). What was left: ~340 ad-hoc
``{"success": False, "error": ...}`` dicts in ``server/handlers`` without an
``error_type``, several of which the inference could not type (``unspecified``:
"Could not find definition folder", "PBIP folder not available", "file_path
required: ..."), so a client could not branch on them.

Every handler failure now goes through ``error_envelope(<type>, <message>)``.
This file fuzzes every registered tool through the real dispatcher - bogus op,
no args, every enum op with no args - with no Desktop connection and no PBIP
path, and asserts a real type on every failure. It also pins the source-level
invariant (no untyped failure literal left in server/handlers).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from server.dispatch import ToolDispatcher
from server.handlers import register_all_handlers
from server.registry import get_registry

ROOT = Path(__file__).resolve().parent.parent
UNTYPED = (None, "", "unspecified", "error")


@pytest.fixture(scope="module")
def dispatcher():
    reg = get_registry()
    if reg.total_tools() == 0:
        register_all_handlers(reg)
    return ToolDispatcher()


#: Operations that exist only to talk to a running Desktop (instance detection,
#: ADOMD connect, the powerbi-desktop bridge CLI), or to start / stop the local
#: client gateway process. They are never swept: this test must not reach a real
#: Power BI Desktop or spawn / kill a process. The tripwires below fail the
#: test if anything tries to anyway.
LIVE_ONLY_OPS = {
    "01_Connection": None,  # None = every op, including the default (detect)
    "17_Desktop_Bridge": None,
    "06_Analysis_Operations": {"compare"},  # auto-detects two running instances
    # spawn / stop the gateway process; selftest spawns its windowless child process
    "00_Client_Mode": {"gateway_start", "gateway_stop", "selftest"},
}


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """No live Desktop, no PBIP folder, and tripwires on every live seam."""
    import subprocess

    import core.autonomous.desktop_bridge as bridge
    from core.infrastructure import connection_manager as cm
    from core.infrastructure.connection_state import connection_state

    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "OFF")
    monkeypatch.setattr(connection_state, "is_connected", lambda: False, raising=False)
    monkeypatch.setattr(connection_state, "get_pbip_folder_path", lambda: None, raising=False)
    live: list = []

    def _trip(name, result):
        def _f(*a, **k):
            live.append(name)
            return result

        return _f

    monkeypatch.setattr(
        bridge, "run_bridge", _trip("bridge", {"ok": False, "error_type": "x", "error": "x"})
    )
    monkeypatch.setattr(cm.PowerBIDesktopDetector, "find_powerbi_instances", _trip("detect", []))
    for meth in ("connect", "connect_to_port", "connect_instance", "detect_instances"):
        monkeypatch.setattr(cm.ConnectionManager, meth, _trip(meth, {"success": False}))

    def _no_spawn(*a, **k):
        live.append(f"subprocess {a[:1]}")
        raise OSError("subprocess blocked by test")

    monkeypatch.setattr(subprocess, "run", _no_spawn)
    monkeypatch.setattr(subprocess, "Popen", _no_spawn)
    yield
    assert not live, f"a live Desktop seam was called: {live}"


def _calls(reg, tool):
    props = reg.get_full_schema(tool)["properties"]
    live = LIVE_ONLY_OPS.get(tool, set())
    calls = [] if live is None else [{}]
    for sel in ("operation", "object_type", "mode"):
        if sel in props:
            calls.append({sel: "bogus_op_zz", "pbip_path": "C:/nope/none.pbip"})
            break
    if live is None:
        return calls
    for op in (props.get("operation") or {}).get("enum") or []:
        if op not in live:
            calls.append({"operation": op})
    return calls


def _tool_names():
    reg = get_registry()
    if reg.total_tools() == 0:
        register_all_handlers(reg)
    return sorted(t.name for t in reg.get_all_tools())


@pytest.mark.parametrize("tool", _tool_names())
def test_every_failure_has_a_branchable_error_type(dispatcher, tool):
    untyped = []
    for args in _calls(dispatcher.registry, tool):
        out = dispatcher.dispatch(tool, dict(args))
        if isinstance(out, dict) and out.get("success") is False:
            if out.get("error_type") in UNTYPED:
                untyped.append((args, str(out.get("error"))[:100]))
            assert isinstance(out.get("error"), str) and out["error"], (tool, args, out)
    assert not untyped, untyped


def test_no_untyped_failure_literal_left_in_handlers():
    """Source invariant: a ``{"success": False, ...}`` literal in server/handlers
    either carries ``error_type`` or is built with ``error_envelope``."""
    hits = []
    for path in (ROOT / "server" / "handlers").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if not isinstance(n, ast.Dict) or None in n.keys:
                continue
            keys = [k.value for k in n.keys if isinstance(k, ast.Constant)]
            if "success" not in keys or "error" not in keys or "error_type" in keys:
                continue
            v = n.values[keys.index("success")]
            if isinstance(v, ast.Constant) and v.value is False:
                hits.append(f"{path.relative_to(ROOT)}:{n.lineno}")
    assert not hits, hits


def test_definition_resolver_failure_is_typed():
    from core.utilities.pbip_utils import resolve_definition_path

    res = resolve_definition_path("C:/nope/none.pbip")
    assert res["error_type"] == "not_found" and "definition folder" in res["error"]


# ---------------------------------------------------------------------------
# A-19: guidance names real tools / ops
# ---------------------------------------------------------------------------

#: v6-era tool names that no longer exist (the registry has 28 NN_* tools).
_REMOVED_TOOL_NAMES = (
    "detect_powerbi_desktop",
    "connect_to_powerbi",
    "run_dax_query",
    "preview_table_data",
    "12_Autonomous_Workflow",
    "07_PBIP_Dependency_Analysis",
    "SVG_Visual_Operations",
    "set_pbip_path",
    "list_tables",
    "describe_table",
    "analyze_model_bpa",
    "export_tmdl",
    "get_recent_logs",
)


def _prose_literals(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(n, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docs.add(id(body[0].value))
    for n in ast.walk(tree):
        if (
            isinstance(n, ast.Constant)
            and isinstance(n.value, str)
            and id(n) not in docs
            and " " in n.value  # prose / hints, not identifier-like keys
        ):
            yield n.lineno, n.value


def test_no_hint_or_message_names_a_removed_tool():
    """Wave 2 scanned the protocol layer only; every string the server can emit
    (server/, core/, src/) is scanned now. "Verify ... with list_tables" and
    "set_pbip_path" survived in core/ and the debug handler."""
    import re

    pat = re.compile(
        r"(?<![A-Za-z0-9_.])("
        + "|".join(map(re.escape, _REMOVED_TOOL_NAMES))
        + r")(?![A-Za-z0-9_])"
    )
    hits = []
    for base in ("server", "core", "src"):
        for path in (ROOT / base).rglob("*.py"):
            for lineno, text in _prose_literals(path):
                m = pat.search(text)
                # "describe_table method ..." names a Python method, not a tool.
                if m and "method" not in text:
                    hits.append(f"{path.relative_to(ROOT)}:{lineno} {m.group(1)}")
    assert not hits, hits


def test_not_connected_and_manager_unavailable_point_at_01_connection():
    from core.validation.error_handler import ErrorHandler

    for env in (
        ErrorHandler.handle_not_connected(),
        ErrorHandler.handle_manager_unavailable("query_executor"),
    ):
        text = repr(env)
        assert "01_Connection" in text, env
        for name in ("detect_powerbi_desktop", "connect_to_powerbi"):
            assert name not in text
