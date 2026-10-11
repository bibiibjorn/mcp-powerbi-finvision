#!/usr/bin/env python3
"""Headless CLI over the tool dispatcher (F-11): ``python -m src.cli <tool> '<json-args>'``.

Runs any registered tool in-process through ``ToolDispatcher.dispatch`` (same security gates, argument
normalisation and envelope as the MCP server) without an MCP client, so CI, ``g01_prepush_gate`` and the
skills' scripts call the same engine instead of reimplementing it::

    python -m src.cli --list-tools
    python -m src.cli 07_PBIP_Operations '{"operation":"scan_broken_refs","pbip_path":"C:/repo/Report.pbip"}'
    python -m src.cli 09_Validate:test_list --file args.json
    python -m src.cli --connect-folder C:/repo/Model.pbip 02_Model_Operations '{"object_type":"measure","operation":"list"}'
    python -m src.cli --connect-xmla "My Workspace" "Sales Model" 09_Validate '{"operation":"test_run","suite_path":"tests/model.suite.json"}'

Exit codes: ``0`` ok, ``1`` the tool reported an error (``success: false``, or a result ``exit_code``
other than 0 such as a failed ``09_Validate test_run``), ``2`` usage error (bad JSON, unknown tool,
missing argument). stdout carries ONLY the result (JSON by default, ``--format text`` for a short
human rendering); logs and anything a library prints go to stderr. Secrets (tokens, passwords,
connection strings, the ``MCP_PBI_XMLA_TOKEN`` value) are redacted from the output.

File paths (``suite_path`` / ``output_path`` ...) go through the server's path guard: the offline project
folder, the repo's ``exports/`` / ``output/`` / ``logs/`` and any ``--root DIR`` (env
``FINVISION_ALLOWED_ROOTS``) are allowed.

Offline lane: ``--connect-folder`` connects the PBIP model through ``01_Connection connect_folder`` in
the same invocation (TOM over TMDL, no Desktop, no DAX engine: ``test_run`` needs Desktop or
``--connect-xmla``). Read-only XMLA target: ``--connect-xmla WORKSPACE MODEL``.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import re
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

EXIT_OK, EXIT_TOOL_ERROR, EXIT_USAGE = 0, 1, 2

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SECRET_KEY_RE = re.compile(
    r"(token|secret|password|passwd|pwd|api[_-]?key|authorization|bearer|connection[_-]?string|credential)",
    re.IGNORECASE,
)
_SECRET_ENV_VARS = ("MCP_PBI_XMLA_TOKEN",)
_REDACTED = "***"


# --------------------------------------------------------------------------- redaction
def _secret_values() -> List[str]:
    return [v for v in (os.environ.get(n) for n in _SECRET_ENV_VARS) if v and len(v) >= 6]


def redact(obj: Any, _secrets: Optional[List[str]] = None) -> Any:
    """Copy of *obj* with secret-looking keys masked and known secret values scrubbed from strings."""
    secrets = _secret_values() if _secrets is None else _secrets
    if isinstance(obj, dict):
        return {
            k: (
                _REDACTED
                if isinstance(k, str) and _SECRET_KEY_RE.search(k) and v not in (None, "", False)
                else redact(v, secrets)
            )
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [redact(v, secrets) for v in obj]
    if isinstance(obj, str):
        out = obj
        for s in secrets:
            out = out.replace(s, _REDACTED)
        return out
    return obj


# --------------------------------------------------------------------------- bootstrap
def bootstrap():
    """Registry + dispatcher the way ``pbixray_server_enhanced`` builds them, minus the MCP app,
    the stdio loop, the client gateway and the file logging. Returns ``(registry, dispatcher)``."""
    for p in (_ROOT, os.path.join(_ROOT, "src")):
        if p not in sys.path:
            sys.path.insert(0, p)
    from core.config.config_manager import config
    from core.config.tool_timeouts import ToolTimeoutManager
    from core.infrastructure.connection_manager import ConnectionManager
    from core.infrastructure.connection_state import connection_state
    from core.infrastructure.limits_manager import init_limits_manager
    from core.infrastructure.rate_limiter import RateLimiter
    from core.orchestration.agent_policy import AgentPolicy
    from server.dispatch import ToolDispatcher
    from server.handlers import register_all_handlers
    from server.registry import get_registry

    connection_state.set_connection_manager(ConnectionManager())
    registry = get_registry()
    register_all_handlers(registry)
    dispatcher = ToolDispatcher()
    connection_state.agent_policy = AgentPolicy(
        config,
        timeout_manager=ToolTimeoutManager(config.get("tool_timeouts", {})),
        rate_limiter=RateLimiter(config.get("rate_limiting", {})),
        limits_manager=init_limits_manager(config.get_all()),
    )
    return registry, dispatcher


# --------------------------------------------------------------------------- help / listing
def tool_summaries(registry) -> List[Dict[str, Any]]:
    """One entry per registered tool: name, operations (the schema's ``operation`` enum), first description line."""
    out = []
    for t in sorted(registry.get_all_tools(), key=lambda d: d.name):
        props = (
            (t.input_schema or {}).get("properties", {}) if isinstance(t.input_schema, dict) else {}
        )
        ops = (props.get("operation") or {}).get("enum") or []
        desc = (t.description or "").strip().splitlines()[0] if t.description else ""
        out.append({"tool": t.name, "operations": list(ops), "description": desc[:200]})
    return out


def render_tool_list(summaries: Sequence[Dict[str, Any]], fmt: str) -> str:
    if fmt == "json":
        return json.dumps(summaries, indent=2, ensure_ascii=False)
    lines = []
    for s in summaries:
        ops = ",".join(s["operations"]) if s["operations"] else "-"
        lines.append(f"{s['tool']}\n    ops: {ops}\n    {s['description']}")
    return "\n".join(lines)


def _epilog() -> str:
    """Help footer generated from the live registry, so the CLI help cannot drift from the tools."""
    try:
        with contextlib.redirect_stdout(sys.stderr):
            registry, _ = bootstrap()
        names = ", ".join(s["tool"] for s in tool_summaries(registry))
        return f"tools: {names}\n(use --list-tools for operations)"
    except Exception as exc:  # noqa: BLE001 - help must never crash
        return f"(tool list unavailable: {type(exc).__name__}: {exc})"


# --------------------------------------------------------------------------- args
class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # usage errors are exit 2, message on stderr
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")


def build_parser(with_epilog: bool = False) -> argparse.ArgumentParser:
    p = _Parser(
        prog="python -m src.cli",
        description="Run an MCP-PowerBi-Finvision tool headlessly through the tool dispatcher.",
        epilog=_epilog() if with_epilog else None,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "tool",
        nargs="?",
        help="tool name (e.g. 09_Validate) or tool:operation (09_Validate:test_run)",
    )
    p.add_argument("json_args", nargs="?", help="arguments as a JSON object (default {})")
    p.add_argument("--file", metavar="FILE", help="read the JSON arguments from FILE ('-' = stdin)")
    p.add_argument(
        "--list-tools", action="store_true", help="list tools and their operations, then exit"
    )
    p.add_argument(
        "--format", choices=("json", "text"), default="json", help="output format (default json)"
    )
    p.add_argument(
        "--connect-folder",
        metavar="PBIP",
        help="connect the offline PBIP model first (01_Connection connect_folder)",
    )
    p.add_argument(
        "--connect-xmla",
        nargs=2,
        metavar=("WORKSPACE", "MODEL"),
        help="connect a published model read-only first",
    )
    p.add_argument(
        "--root",
        action="append",
        metavar="DIR",
        default=[],
        help="extra directory the file-path guard may read/write (repeatable; e.g. the repo's tests/ folder)",
    )
    p.add_argument("--version", action="store_true", help="print the server version and exit")
    return p


def parse_arguments(ns: argparse.Namespace) -> Tuple[Optional[str], Dict[str, Any], Optional[str]]:
    """``(tool, arguments, usage_error)``."""
    tool = ns.tool
    if not tool:
        return None, {}, "a tool name is required (see --list-tools)"
    operation = None
    if ":" in tool:
        tool, operation = tool.split(":", 1)
    raw = None
    if ns.file is not None:
        if ns.json_args is not None:
            return None, {}, "pass the arguments either inline or with --file, not both"
        try:
            raw = (
                sys.stdin.read()
                if ns.file == "-"
                else open(ns.file, "r", encoding="utf-8-sig").read()
            )
        except OSError as e:
            return None, {}, f"cannot read --file: {e}"
    elif ns.json_args is not None:
        raw = ns.json_args
    args: Dict[str, Any] = {}
    if raw is not None and raw.strip():
        try:
            args = json.loads(raw)
        except ValueError as e:
            return None, {}, f"the arguments are not valid JSON: {e}"
        if not isinstance(args, dict):
            return None, {}, "the arguments must be a JSON object"
    if operation:
        if args.get("operation") not in (None, operation):
            return (
                None,
                {},
                f"operation conflict: '{operation}' in the tool name vs '{args['operation']}' in the JSON",
            )
        args["operation"] = operation
    return tool, args, None


# --------------------------------------------------------------------------- result handling
def exit_code_for(result: Any) -> int:
    """0 ok / 1 tool error. A result ``exit_code`` (``09_Validate test_run``: failed tests) wins over ``success``."""
    if not isinstance(result, dict):
        return EXIT_OK
    code = result.get("exit_code")
    if isinstance(code, int) and not isinstance(code, bool):
        return EXIT_OK if code == 0 else EXIT_TOOL_ERROR
    return EXIT_TOOL_ERROR if result.get("success") is False else EXIT_OK


def render_result(result: Any, fmt: str) -> str:
    result = redact(result)
    if fmt == "json":
        return json.dumps(result, indent=2, ensure_ascii=False, default=str)
    if not isinstance(result, dict):
        return str(result)
    lines: List[str] = []
    if result.get("success") is False or result.get("error"):
        lines.append(f"ERROR [{result.get('error_type', 'error')}]: {result.get('error')}")
        for key in ("suggestion", "hint"):
            if result.get(key):
                lines.append(f"  {key}: {result[key]}")
    for k, v in result.items():
        if k in ("error", "error_type", "suggestion", "hint"):
            continue
        if isinstance(v, (str, int, float, bool)) or v is None:
            lines.append(f"{k}: {v}")
        else:
            text = json.dumps(v, ensure_ascii=False, default=str)
            lines.append(f"{k}: {text if len(text) <= 300 else text[:297] + '...'}")
    return "\n".join(lines)


def _connect(dispatcher, ns: argparse.Namespace) -> Optional[Dict[str, Any]]:
    """Run the requested connection first; the failing result (or None when connected / nothing asked)."""
    if ns.connect_folder:
        res = dispatcher.dispatch(
            "01_Connection", {"operation": "connect_folder", "pbip_path": ns.connect_folder}
        )
    elif ns.connect_xmla:
        res = dispatcher.dispatch(
            "01_Connection",
            {
                "operation": "connect_xmla",
                "workspace": ns.connect_xmla[0],
                "model": ns.connect_xmla[1],
            },
        )
    else:
        return None
    return None if isinstance(res, dict) and res.get("success") is not False else res


# --------------------------------------------------------------------------- main
def _utf8_streams() -> None:
    """JSON is printed with ensure_ascii=False; a Windows cp1252 console must not crash on it."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):  # not a TextIOWrapper (captured / redirected)
            pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    _utf8_streams()
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser(with_epilog=any(a in ("-h", "--help") for a in argv))
    ns = parser.parse_args(argv)
    out = sys.stdout
    logging.basicConfig(
        level=logging.WARNING, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s"
    )

    if ns.version:
        sys.path.insert(0, os.path.join(_ROOT, "src"))
        from __version__ import __version__

        print(__version__, file=out)
        return EXIT_OK

    if not ns.list_tools:
        tool, args, usage_error = parse_arguments(ns)
        if usage_error:
            parser.print_usage(sys.stderr)
            print(f"python -m src.cli: error: {usage_error}", file=sys.stderr)
            return EXIT_USAGE

    if ns.root:
        existing = [
            r for r in (os.environ.get("FINVISION_ALLOWED_ROOTS") or "").split(os.pathsep) if r
        ]
        os.environ["FINVISION_ALLOWED_ROOTS"] = os.pathsep.join(
            existing + [os.path.abspath(r) for r in ns.root]
        )

    # Anything a library prints while it loads or runs must not pollute the result on stdout.
    with contextlib.redirect_stdout(sys.stderr):
        registry, dispatcher = bootstrap()

    if ns.list_tools:
        print(render_tool_list(tool_summaries(registry), ns.format), file=out)
        return EXIT_OK

    if not registry.has_tool(tool):
        names = sorted(t.name for t in registry.get_all_tools())
        print(
            f"python -m src.cli: error: unknown tool '{tool}'. Tools: {', '.join(names)}",
            file=sys.stderr,
        )
        return EXIT_USAGE

    with contextlib.redirect_stdout(sys.stderr):
        failed = _connect(dispatcher, ns)
        result = failed if failed is not None else dispatcher.dispatch(tool, args)
    print(render_result(result, ns.format), file=out)
    return exit_code_for(result)


if __name__ == "__main__":
    sys.exit(main())
