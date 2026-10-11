"""
00_Client_Mode: the local client-masking gateway for Claude Code.

The gateway (``client_gateway``) sits between Claude Code and Anthropic for ROUTED chats: it masks
every client value before a request leaves the machine (the user's prompt, tool results, files,
screenshots) and restores the real values in the answer, so the user and the tools work with real
names while Anthropic only sees pseudonyms such as CUS_48213. Masking on demand (owner rule
2026-10-08, ``client_gateway.switch``): masking is off by default and the gateway runs ONLY while
a chat has masking on. The
USER's typed `masking on` starts it and routes the chat's project folder; `masking off` unroutes
the folder and the gateway stops by itself; `masking status` (or `client mode ...`) is typed by
the user too. This tool can switch a chat ON (the same as the typed command), never OFF. While a
chat is off, a safety net stops a request whose NEWEST message holds known client data (a
dictionary value, a name the recognizer finds, a screenshot with a hit) with a hint to type
`masking on`; it does NOT look at earlier messages or the system prompt, and masks nothing. This
server itself does not mask: its results travel inside the request and the gateway masks them.

  status          is this chat routed, the gateway running, this chat's mode, dictionary COUNTS,
                  routed folders and whether every chat is routed. Never a value. (read)
  on              switch this chat's masking on, the same as the typed `masking on`: starts the
                  gateway and routes this chat's project folder. (write)
  off             refused (user_action_required): the user types `masking off` in the chat. (write)
  refresh         re-read the open Power BI models' client values now. (write)
  gateway_status  alias of status. (read)
  gateway_start   start the gateway (detached, waits up to 8 s). (write)
  gateway_stop    stop the gateway; routed chats then fail until it runs again. Refused in a routed
                  chat (user_action_required). (write)
  route_folder    route a folder (arg folder_path): ANTHROPIC_BASE_URL in its
                  .claude/settings.local.json, kept out of git. Routing only says where the traffic
                  goes, not what the data is. (write)
  unroute_folder  undo it (arg folder_path). Refused in a routed chat (user_action_required).
                  (write)
  route_all       route EVERY chat (the user's Claude Code settings). Remote Control is then
                  unavailable in every chat (a Claude Code rule for gateways). (write)
  unroute_all     undo route_all. Refused in a routed chat (user_action_required). (write)
  setup           the command the USER runs in a terminal to install the routed masking gateway (a
                  certificate goes into the user's store, so this tool never runs it) plus the
                  steps and what is already known (CA, config flags, client configs). (read)
  selftest        replay every recorded wire fixture of every named app (Claude Code, Copilot,
                  ChatGPT) through the current adapters, offline, one line per app (R9). Runs in a
                  windowless child process with its own temp masking home: this server's
                  environment is never touched. 60 s limit. (read)
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List

from client_gateway import control_client as gw
from client_gateway import folders as gw_folders
from client_gateway.routing.guard import ENV as REAL_SYSTEM_ENV
from core.validation.error_response import error_envelope
from server.registry import ToolDefinition
from server.tool_schemas import TOOL_SCHEMAS

_VALID_OPS = (
    "status",
    "on",
    "off",
    "refresh",
    "gateway_status",
    "gateway_start",
    "gateway_stop",
    "route_folder",
    "unroute_folder",
    "route_all",
    "unroute_all",
    "setup",
    "selftest",
)
_USER_OFF = "type: masking off in the chat"
_USER_TERMINAL = (
    "run it yourself in a terminal (python -m client_gateway stop) or from a chat that is not "
    "routed through the gateway"
)
_ROUTING_NOTE = (
    "Routing only decides where Claude Code traffic goes; it says nothing about the data. Chats "
    "started afterwards run through the gateway (restart Claude Code); masking is a per-chat mode, "
    "off by default (a safety net stops a message with known client data): the user types "
    "`masking on` / `masking off` / `masking status`. Routing also "
    "switches off the Claude Code channels that ignore the base URL (WebFetch domain pre-check, "
    "/feedback, the survey, telemetry)."
)


# ------------------------------------------------------------------------------ status
def _gateway_section() -> Dict[str, Any]:
    """Gateway facts: running, version, this chat's mode, counts. Never a value."""
    routed = gw.env_routed()
    info = gw.health()
    out: Dict[str, Any] = {
        "url": gw.gateway_url(),
        "routed_session": routed,
        "running": info is not None,
    }
    if info is not None:
        if isinstance(info.get("version"), str):
            out["version"] = info["version"]
        full = gw.status()
        counts = gw.counts_summary(full)
        if counts:
            out["dictionary"] = counts
        if isinstance(full, dict):
            if isinstance(full.get("default_mode"), str):
                out["default_mode"] = full["default_mode"]  # off = masked only after "masking on"
            if full.get("warnings"):
                out["warnings"] = full["warnings"]  # e.g. dictionary_empty, store_error:...
            apps = _apps(full.get("frontend_apps"))
            if apps:
                out["apps"] = apps  # observed app labels per adapter (labels only)
        sid = gw.current_session_id()
        if routed and sid:
            out["session_mode"] = gw.session_mode(sid, timeout=3.0) or "unknown (treated as on)"
    out["routed_folders"] = [f["path"] for f in gw_folders.list_folders()]
    out["every_chat_routed"] = gw_folders.routes_all()
    if routed and info is None:
        out["note"] = "this chat is routed but the gateway is not running: requests fail"
    elif not routed:
        out["note"] = "this chat is not routed through the gateway: nothing masks it"
    return out


def _apps(raw: Any) -> Dict[str, Any]:
    """The front end's observed app labels per adapter (``vscode/1``, ``chrome/141``): labels, the
    number of requests that carried each, and which labels no wire fixture was recorded with.
    Nothing else of a header is ever kept, so nothing else can be shown."""
    out: Dict[str, Any] = {}
    if not isinstance(raw, dict):
        return out
    for adapter, info in raw.items():
        if not isinstance(info, dict):
            continue
        seen = info.get("seen")
        untested = info.get("untested")
        out[str(adapter)] = {
            "seen": (
                {str(k): int(v) for k, v in seen.items() if isinstance(v, int)}
                if isinstance(seen, dict)
                else {}
            ),
            "untested": [str(u) for u in untested] if isinstance(untested, list) else [],
        }
    return out


def _status(args: Dict[str, Any]) -> Dict[str, Any]:
    return {"success": True, **_gateway_section()}


def _unreachable() -> Dict[str, Any]:
    return error_envelope(
        "gateway_unreachable",
        "The client gateway is not running.",
        suggestion="00_Client_Mode gateway_start",
    )


def _on(args: Dict[str, Any]) -> Dict[str, Any]:
    """The same as the user's typed ``masking on`` (``client_gateway.switch.masking_on``)."""
    from client_gateway import switch  # noqa: PLC0415

    sid = gw.current_session_id()
    project = os.environ.get("CLAUDE_PROJECT_DIR", "").strip()
    if not sid or not project:
        return error_envelope(
            "no_session",
            "This server does not know which Claude Code chat or project it serves "
            "(CLAUDE_CODE_SESSION_ID / CLAUDE_PROJECT_DIR are not set).",
            user_action="type: masking on",
            suggestion="Ask the user to type `masking on` in the chat.",
        )
    result = switch.masking_on(sid, project)
    if not result.get("ok"):
        return error_envelope(
            "masking_on_failed",
            "Masking could not be switched on (" + str(result.get("error")) + ").",
            suggestion="Ask the user to type `masking on` in the chat.",
        )
    return {
        "success": True,
        "masking": "on",
        "folder": result.get("folder"),
        "gateway_started": bool(result.get("started")),
        "note": "Masking is on for this chat from its next message on (Claude Code re-reads its "
        "settings within a few seconds). The user types `masking off` to stop it; the gateway "
        "then stops by itself.",
    }


def _refuse(what: str, how: str) -> Dict[str, Any]:
    """Operations that weaken the masking are the user's, never the AI's."""
    return error_envelope(
        "user_action_required",
        f"{what} can only be done by the user while this chat runs through the gateway.",
        user_action=how,
        suggestion="Ask the user to " + how + ". Never try another way.",
    )


def _off(args: Dict[str, Any]) -> Dict[str, Any]:
    return _refuse("Switching masking off", _USER_OFF)


def _refresh(args: Dict[str, Any]) -> Dict[str, Any]:
    reply = gw.refresh()
    if reply is None:
        return _unreachable()
    code, data = reply
    if code != 200 or data.get("ok") is False:
        return error_envelope("gateway_error", f"Gateway refresh failed (HTTP {code}).")
    counts = gw.counts_summary(gw.status()) or gw.counts_summary(data)
    return {"success": True, "dictionary": counts}


# ------------------------------------------------------------------------------ gateway ops
def _gateway_start(args: Dict[str, Any]) -> Dict[str, Any]:
    result = gw.start_gateway()
    if not result.get("running"):
        return error_envelope(
            "gateway_start_failed",
            "The client gateway did not start (" + str(result.get("error") or "no health") + ").",
            suggestion="Run `python -m client_gateway serve` in the repo to see why.",
        )
    return {
        "success": True,
        "running": True,
        "started": bool(result.get("started")),
        "url": gw.gateway_url(),
    }


def _gateway_stop(args: Dict[str, Any]) -> Dict[str, Any]:
    if gw.env_routed():
        return _refuse("Stopping the gateway", _USER_TERMINAL)
    result = gw.stop_gateway()
    if not result.get("stopped"):
        return error_envelope(
            "gateway_stop_failed", "The client gateway did not stop.", detail=result
        )
    return {
        "success": True,
        **result,
        "note": "Routed chats fail until the gateway runs again (gateway_start).",
    }


def _folder_op(fn: Callable[..., Dict[str, Any]], *fn_args: Any) -> Dict[str, Any]:
    try:
        result = fn(*fn_args)
    except gw_folders.FolderError as exc:
        return error_envelope("invalid_argument", str(exc))
    except OSError as exc:
        return error_envelope(
            "folder_error", f"could not write the settings ({type(exc).__name__})"
        )
    return {"success": True, **result}


def _folder_arg(args: Dict[str, Any]) -> Any:
    folder = args.get("folder_path")
    return folder if isinstance(folder, str) and folder.strip() else None


def _route_folder(args: Dict[str, Any]) -> Dict[str, Any]:
    folder = _folder_arg(args)
    if folder is None:
        return error_envelope("missing_argument", "folder_path is required for this operation.")
    out = _folder_op(gw_folders.route_folder, folder)
    if out.get("success"):
        out["note"] = _ROUTING_NOTE
    return out


def _unroute_folder(args: Dict[str, Any]) -> Dict[str, Any]:
    if gw.env_routed():
        return _refuse(
            "Taking a folder out of the gateway", "run it yourself from a chat that is not routed"
        )
    folder = _folder_arg(args)
    if folder is None:
        return error_envelope("missing_argument", "folder_path is required for this operation.")
    out = _folder_op(gw_folders.unroute_folder, folder)
    if out.get("success"):
        out["note"] = "New chats in that folder go to Anthropic directly again."
    return out


def _route_all(args: Dict[str, Any]) -> Dict[str, Any]:
    out = _folder_op(gw_folders.route_all)
    if out.get("success"):
        out["note"] = _ROUTING_NOTE + (
            " Every chat now depends on the gateway (it is started by the session hook and the "
            "MCP server, and restarted by the next message when it died). Remote Control is "
            "unavailable in routed chats (Claude Code rule)."
        )
        started = gw.start_gateway()
        out["gateway_running"] = bool(started.get("running"))
    return out


def _unroute_all(args: Dict[str, Any]) -> Dict[str, Any]:
    if gw.env_routed():
        return _refuse(
            "Taking every chat out of the gateway",
            "run it yourself from a chat that is not routed, or remove "
            "ANTHROPIC_BASE_URL from ~/.claude/settings.json",
        )
    out = _folder_op(gw_folders.unroute_all)
    if out.get("success"):
        out["note"] = "New chats go to Anthropic directly again (routed folders stay routed)."
    return out


# ------------------------------------------------------------------------------ setup / selftest
SELFTEST_TIMEOUT_S = 60
_SELFTEST_LOCK = threading.Lock()  # one self-test child at a time


def _setup(args: Dict[str, Any]) -> Dict[str, Any]:
    """The command for the USER to run (a certificate install must be a human action) and the plan
    as far as it is known without touching the system. Never runs setup."""
    from client_gateway import setup_cmd  # noqa: PLC0415

    base = f'"{gw.gateway_python(False)}" -m client_gateway setup'
    out: Dict[str, Any] = {
        "success": True,
        "run_by": "the user, in a terminal (setup changes the Claude Code settings and, with "
        "--desktop-apps, installs a certificate: never run by the AI)",
        "cwd": str(gw.repo_root()),
        "command": base,
        "then": {
            "apply": base + " --yes",
            "register_clients": base + " --yes --register",
            "desktop_apps": base + " --yes --desktop-apps",
            "managed_pc": base + " --yes --desktop-apps --managed-ok",
            "status": base + " --status",
            "undo": base + " --remove --yes",
        },
        "steps": setup_cmd.describe_steps(),
        "note": "The bare command is a plan and changes nothing; nothing is applied without --yes. "
        "--yes installs masking on demand for Claude Code (the hooks that answer 'masking on' / "
        "'masking off', and the config); --desktop-apps adds Copilot / ChatGPT routing (a local "
        "certificate and a proxy rule, managed-PC and proxy checks first).",
    }
    try:
        paths = setup_cmd.default_paths()
        facts = setup_cmd.read_facts(None, paths, "this tool never reads the system settings")
        out["plan"] = setup_cmd.plan_lines(setup_cmd.Options(register=True), facts, paths)
    except Exception as exc:  # noqa: BLE001 - the steps and the command are enough
        out["plan_unavailable"] = type(exc).__name__
    return out


def _selftest_summary(code: int, lines: List[str]) -> Dict[str, Any]:
    apps: List[Dict[str, Any]] = []
    unrouted: List[str] = []
    for line in lines:
        if line.startswith("  - ") and apps:
            apps[-1]["problems"].append(line[4:])
        elif line.startswith("unrouted fixture"):
            unrouted.append(line)
        elif line.strip() and not line.startswith(" "):
            parts = line.split()
            apps.append(
                {
                    "app": parts[0],
                    "verdict": parts[1] if len(parts) > 1 else "?",
                    "line": line,
                    "problems": [],
                }
            )
    return {
        "success": True,
        "passed": code == 0,
        "exit_code": code,
        "apps": apps,
        "unrouted": unrouted,
        "lines": list(lines),
    }


def _selftest_env(tmp: Path) -> Dict[str, str]:
    """The CHILD's environment: this one minus the routing and real-system variables, plus a temp
    masking home, no NER model and a temp Claude config dir. This process's ``os.environ`` is never
    modified."""
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_BASE_URL", REAL_SYSTEM_ENV)}
    env.update(
        {
            "FINVISION_MASKING_HOME": str(tmp / "home"),
            "FINVISION_GATEWAY_NER": "0",
            "CLAUDE_CONFIG_DIR": str(tmp / "claude"),
            "PYTHONIOENCODING": "utf-8",
        }
    )
    return env


def _selftest(args: Dict[str, Any]) -> Dict[str, Any]:
    root = gw.repo_root()
    if (
        not (root / "tools" / "masking_lab" / "selftest.py").is_file()
        or not (root / "tests" / "_wire_support.py").is_file()
    ):
        return error_envelope(
            "selftest_unavailable",
            "The self-test needs the repo's tools/masking_lab and tests folders (not found).",
        )
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        with _SELFTEST_LOCK, tempfile.TemporaryDirectory(prefix="masking-selftest-") as tmp:
            done = subprocess.run(  # noqa: S603 - fixed argv, windowless (R10), own stdin
                [gw.gateway_python(True), "-m", "tools.masking_lab.selftest"],
                cwd=str(root),
                env=_selftest_env(Path(tmp)),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
                timeout=SELFTEST_TIMEOUT_S,
            )
    except subprocess.TimeoutExpired:
        return error_envelope(
            "selftest_timeout",
            f"The self-test did not finish within {SELFTEST_TIMEOUT_S} s and was stopped.",
        )
    except Exception as exc:  # noqa: BLE001 - reported by type, never a value
        return error_envelope(
            "selftest_failed", f"The self-test could not run ({type(exc).__name__})."
        )
    lines = done.stdout.decode("utf-8", "replace").splitlines()
    summary = _selftest_summary(done.returncode, lines)
    if not summary["apps"]:
        return error_envelope(
            "selftest_failed",
            f"The self-test produced no result (exit code {done.returncode}).",
        )
    return summary


_OPS = {
    "status": _status,
    "on": _on,
    "off": _off,
    "refresh": _refresh,
    "gateway_status": _status,
    "gateway_start": _gateway_start,
    "gateway_stop": _gateway_stop,
    "route_folder": _route_folder,
    "unroute_folder": _unroute_folder,
    "route_all": _route_all,
    "unroute_all": _unroute_all,
    "setup": _setup,
    "selftest": _selftest,
}


def handle_client_mode(args: Dict[str, Any]) -> Dict[str, Any]:
    op = (args or {}).get("operation") or "status"
    handler = _OPS.get(op)
    if handler is None:
        return error_envelope(
            "invalid_operation",
            f"operation must be one of {list(_VALID_OPS)}",
            valid_values=list(_VALID_OPS),
        )
    return handler(args or {})


def register_client_mode_handler(registry: Any) -> None:
    registry.register(
        ToolDefinition(
            name="00_Client_Mode",
            description=(
                "Client-masking gateway for Claude Code (client names -> pseudonyms before they "
                "leave the machine, real values back in the answer; per-chat mode, off by "
                "default, switched by the user typing 'masking on/off'): status, on, off, "
                "refresh, gateway_start/stop, route_folder, unroute_folder, route_all, "
                "unroute_all, setup, selftest."
            ),
            guide_notes=__doc__,
            handler=handle_client_mode,
            input_schema=TOOL_SCHEMAS["client_mode"],
            category="core",
            sort_order=5,
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        )
    )


__all__ = ["handle_client_mode", "register_client_mode_handler"]
