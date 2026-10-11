"""Masking on demand: what the user's typed ``masking on`` / ``masking off`` do (owner rule
2026-10-08: the gateway runs only while a chat has masking on).

``masking_on(session, folder)``
    starts the gateway (waits until it answers), switches the chat's mode on, routes the chat's
    project folder (``folders.route_folder``: ``ANTHROPIC_BASE_URL`` in its
    ``.claude/settings.local.json``) and records the chat on that folder. The running Claude Code
    session re-reads that file and sends its next request through the gateway, masked.

``masking_off(session, folder)``
    switches masking off for every chat recorded on the folder (they share its routing), unroutes
    it (the direct address is written, never deleted: ``folders``) and leaves it to the gateway to
    stop by itself once nothing is routed to it any more (``reaper``). Other folders that still
    have masking on keep it running.

Both are called by the UserPromptSubmit hook (``hooks/client_masking_hook.py``) and by the CLI
(``python -m client_gateway masking on|off``); they never print and never raise.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from client_gateway import control_client, folders

START_WAIT_S = 90.0  # a large remembered dictionary takes a while to load


def project_folder(cwd: Any = None) -> Path:
    """The folder Claude Code reads ``.claude/settings.local.json`` from: the session's project
    root (``CLAUDE_PROJECT_DIR``) when ``cwd`` lies inside it, else ``cwd``, else the current
    directory."""
    here = (
        Path(os.path.abspath(str(cwd).strip()))
        if isinstance(cwd, (str, os.PathLike)) and str(cwd).strip()
        else Path.cwd()
    )
    root = os.environ.get("CLAUDE_PROJECT_DIR", "").strip()
    if root:
        project = Path(os.path.abspath(root))
        if folders._key(project) in {folders._key(p) for p in (here, *here.parents)}:
            return project
    return here


def _set_mode(session_id: str, mode: str) -> bool:
    headers: Dict[str, str] = {}
    token = control_client._token()
    if token:
        headers["X-Gateway-Token"] = token
    reply = control_client.request(
        "POST", "/__gateway/mode", {"session": session_id, "mode": mode}, headers
    )
    return reply is not None and reply[0] == 200 and reply[1].get("ok") is not False


def masking_on(
    session_id: Optional[str], folder: Any, wait_s: float = START_WAIT_S
) -> Dict[str, Any]:
    sid = (session_id or "").strip()
    if not sid:
        return {"ok": False, "error": "no_session"}
    target = project_folder(folder)
    started = control_client.start_gateway(wait_s=wait_s)
    if not started.get("running"):
        return {"ok": False, "error": "gateway_not_started", "detail": started.get("error")}
    if not _set_mode(sid, "on"):
        return {"ok": False, "error": "mode_not_set"}
    try:
        routed = folders.route_folder(target)
        folders.add_session(target, sid)
    except (folders.FolderError, OSError) as exc:
        _set_mode(sid, "off")
        return {"ok": False, "error": "route_failed", "detail": str(exc)}
    return {
        "ok": True,
        "folder": str(target),
        "started": bool(started.get("started")),
        "git": routed.get("git"),
        # a tracked settings.local.json would carry the route into a commit: the user must know
        "warning": (routed.get("git") or {}).get("warning"),
    }


def masking_off(session_id: Optional[str], folder: Any) -> Dict[str, Any]:
    sid = (session_id or "").strip()
    here = project_folder(folder)
    target = folders.routed_folder_of(here) or here
    sessions: list[str] = []
    for entry in folders._load_registry().values():
        if folders._key(Path(str(entry.get("path", "")))) == folders._key(target):
            sessions = [s for s in (entry.get("sessions") or []) if isinstance(s, str)]
    switched = 0
    for s in {*sessions, *([sid] if sid else [])}:
        switched += 1 if _set_mode(s, "off") else 0
    folders.drop_session(target, None)
    try:
        result = folders.unroute_folder(target)
    except (folders.FolderError, OSError) as exc:
        return {"ok": False, "error": "unroute_failed", "detail": str(exc), "folder": str(target)}
    others = [f["path"] for f in folders.list_folders() if f["active"]]
    return {
        "ok": True,
        "folder": str(target),
        "unrouted": bool(result.get("unrouted")),
        "chats_switched_off": switched,
        "other_folders_routed": others,
        "every_chat_routed": folders.routes_all(),
        "gateway_stops": not others and not folders.routes_all(),
    }


def chat_status(session_id: Optional[str], folder: Any) -> Dict[str, Any]:
    sid = (session_id or "").strip()
    running = control_client.health() is not None
    mode = control_client.session_mode(sid) if (running and sid) else None
    here = project_folder(folder)
    return {
        "gateway_running": running,
        "mode": mode or "off",
        "folder_routed": folders.routed_folder_of(here) is not None,
        "counts": _health_counts() if running else {},
    }


# what the user should see about THIS gateway run (counters since it started; zeros left out):
# degraded masking must be visible, not only in the log
HEALTH_COUNTERS = {
    "masked_requests": "masked_requests",
    "blocked": ("blocked", "residual_leak_blocked", "unchecked_attachment_blocked"),
    "ner_partial": "ner_partial",
    "system_values_passed": "system_values_passed",
}


def _health_counts() -> Dict[str, int]:
    reply = control_client.status()
    stats = reply.get("stats") if isinstance(reply, dict) else None
    if not isinstance(stats, dict):
        return {}
    out: Dict[str, int] = {}
    for name, keys in HEALTH_COUNTERS.items():
        total = sum(
            v for k in ((keys,) if isinstance(keys, str) else keys)
            if isinstance(v := stats.get(k, 0), int) and not isinstance(v, bool)
        )  # fmt: skip
        if total:
            out[name] = total
    return out
