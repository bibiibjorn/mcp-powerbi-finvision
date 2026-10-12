"""Client side of the gateway's control endpoints, for the MCP server (standard library only).

The gateway (``python -m client_gateway``) serves ``/__gateway/health|status|mode|session|refresh``
and, for the MCP's masking layer, ``mask|unmask|mask_image`` (the one masking authority) on
127.0.0.1. This module is what the finvision MCP uses to talk to it and to start / stop it:

* every call is defensive: an unreachable gateway is ``None``, never an exception;
* ``off`` is never sent from here: only the user's typed ``client mode off`` (the UserPromptSubmit
  hook, which alone reads ``<home>/gateway.token``) switches a session's masking off;
* nothing here prints (the MCP server speaks its protocol on stdout).

The hook ``hooks/client_masking_hook.py`` is self-contained and carries its own small copy of the
few calls it needs; keep both in step.
"""

from __future__ import annotations

import atexit
import base64
import json
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

from client_gateway.defaults import (
    DEFAULT_PORT,
    FAIL_MODE_DEGRADE,
    PORT_ENV,
    normalise_fail_mode,
)

logger = logging.getLogger(__name__)

BASE_URL_ENV = "ANTHROPIC_BASE_URL"
AUTOSTART_ENV = "FINVISION_GATEWAY_AUTOSTART"
_LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1", "[::1]")
_REPO_ROOT = Path(__file__).resolve().parent.parent


def home() -> Path:
    """The masking home (``%LOCALAPPDATA%/FinvisionMasking``, env ``FINVISION_MASKING_HOME``)."""
    override = os.environ.get("FINVISION_MASKING_HOME")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "FinvisionMasking"


def repo_root() -> Path:
    return _REPO_ROOT


def gateway_port() -> int:
    """Env ``FINVISION_GATEWAY_PORT``, else config ``client_gateway.port``, else 8890 (the order
    the gateway's own command line uses)."""
    raw = os.environ.get(PORT_ENV, "").strip()
    if raw.isdigit() and 0 < int(raw) < 65536:
        return int(raw)
    try:
        from core.config.config_manager import config

        value = int(config.get("client_gateway.port", DEFAULT_PORT))
        return value if 0 < value < 65536 else DEFAULT_PORT
    except Exception:  # noqa: BLE001 - config not loadable: the default
        return DEFAULT_PORT


def gateway_url() -> str:
    return f"http://127.0.0.1:{gateway_port()}"


def is_gateway_url(value: Any) -> bool:
    """True for a loopback http URL on the gateway port (the value ``route_folder`` writes)."""
    if not isinstance(value, str):
        return False
    try:
        parsed = urlparse(value.strip())
        return (
            parsed.scheme == "http"
            and (parsed.hostname or "") in [h.strip("[]") for h in _LOCAL_HOSTS]
            and parsed.port == gateway_port()
        )
    except ValueError:
        return False


def env_routed(environ: Optional[Dict[str, str]] = None) -> bool:
    """True when this process runs with ``ANTHROPIC_BASE_URL`` pointing at the local gateway, i.e.
    the Claude Code session that started it is routed through the gateway."""
    env = os.environ if environ is None else environ
    return is_gateway_url(env.get(BASE_URL_ENV))


def current_session_id() -> str:
    return os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()


# ------------------------------------------------------------------------------ HTTP
_TOKEN_PATHS = (
    "/__gateway/status",
    "/__gateway/session",
    "/__gateway/refresh",
    "/__gateway/mask",  # the masking authority (also matches /__gateway/mask_image)
    "/__gateway/unmask",
    "/__gateway/attach",  # the MCP registers its process with the routed gateway's lifecycle
    "/__gateway/detach",
)


def _token() -> Optional[str]:
    """The control token (``<home>/gateway.token``), for the endpoints that ask for it."""
    try:
        text = (home() / "gateway.token").read_text(encoding="ascii").strip()
    except OSError:
        return None
    return text or None


# Why a call failed (``mask_texts_ex`` and friends): fixed codes, never a value.
REASON_UNREACHABLE = "unreachable"  # nothing answered (not running, refused, reset)
REASON_TIMEOUT = "timeout"  # it answered too slowly
REASON_TOKEN = "token_required"  # 403: the control token is missing or stale (rerun setup)
REASON_NOT_READY = "not_ready"  # 503 not_ready (fail mode closed; the dictionary is not read)
REASON_MASK_FAILED = "mask_failed"  # 503 anything else: the gateway could not mask / unmask
REASON_BAD_REPLY = "bad_reply"  # a reply that is not the documented one
_local = threading.local()  # the transport failure of this thread's last ``request``


def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    return isinstance(exc, urllib.error.URLError) and isinstance(
        exc.reason, (TimeoutError, socket.timeout)
    )


def _transport_reason() -> str:
    """Why this thread's last ``request`` returned None (``unreachable`` when unknown)."""
    return str(getattr(_local, "transport", "") or REASON_UNREACHABLE)


def request(
    method: str,
    path: str,
    body: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 3.0,
    base: Optional[str] = None,
) -> Optional[Tuple[int, Dict[str, Any]]]:
    """``(status, json body)`` from the gateway, or None when it cannot be reached."""
    _local.transport = ""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    hdrs = {"Content-Type": "application/json"} if data is not None else {}
    if path.startswith(_TOKEN_PATHS):  # status / session / refresh / authority need the token
        token = _token()
        if token:
            hdrs["X-Gateway-Token"] = token
    hdrs.update(headers or {})
    req = urllib.request.Request(
        (base or gateway_url()) + path, data=data, method=method, headers=hdrs
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - loopback
            raw, code = resp.read(), resp.status
    except urllib.error.HTTPError as exc:
        try:
            raw, code = exc.read(), exc.code
        except Exception:  # noqa: BLE001
            raw, code = b"", exc.code
    except Exception as exc:  # noqa: BLE001 - refused, timeout, reset: not reachable
        _local.transport = REASON_TIMEOUT if _is_timeout(exc) else REASON_UNREACHABLE
        return None
    try:
        parsed = json.loads(raw or b"{}")
    except ValueError:
        parsed = {}
    return code, parsed if isinstance(parsed, dict) else {}


def health(timeout: float = 1.0) -> Optional[Dict[str, Any]]:
    """The health reply when the gateway answers, else None."""
    reply = request("GET", "/__gateway/health", timeout=timeout)
    if reply is None or reply[0] != 200:
        return None
    return reply[1] or {"ok": True}


def status(timeout: float = 3.0) -> Optional[Dict[str, Any]]:
    reply = request("GET", "/__gateway/status", timeout=timeout)
    return reply[1] if reply is not None and reply[0] == 200 else None


def _find_mode(data: Any) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    mode = data.get("mode")
    if isinstance(mode, str) and mode.lower() in ("on", "off"):
        return mode.lower()
    return _find_mode(data.get("session"))


def session_mode(session_id: str, timeout: float = 1.0) -> Optional[str]:
    """'on' / 'off' for the session, or None when the gateway cannot say."""
    if not session_id:
        return None
    reply = request("GET", "/__gateway/session?id=" + quote(session_id, safe=""), timeout=timeout)
    if reply is None or reply[0] != 200:
        return None
    return _find_mode(reply[1])


def switch_on(session_id: str, timeout: float = 3.0) -> Optional[Tuple[int, Dict[str, Any]]]:
    """Switch the session's masking ON. (There is deliberately no 'off' here.)"""
    return request(
        "POST", "/__gateway/mode", {"session": session_id, "mode": "on"}, timeout=timeout
    )


def refresh(timeout: float = 120.0) -> Optional[Tuple[int, Dict[str, Any]]]:
    return request("POST", "/__gateway/refresh", {}, timeout=timeout)


# ------------------------------------------------------------------------------ masking authority
# Fail mode ``closed``: the gateway waits up to ``client_gateway.authority_wait_s`` (30 s) for its
# dictionary before it answers 503, so the client waits longer than that. Fail mode ``degrade``:
# it waits only ``authority_degrade_wait_s`` and answers 200 with a ``degraded`` note. Each helper
# returns None when the gateway is unreachable, answers anything but a complete 200, or when
# anything at all goes wrong; the ``*_ex`` variants also say why (``REASON_*``). The caller decides:
# retry, mask in-process (degrade) or fail closed (no data is ever returned unmasked by guessing).
MASK_TIMEOUT_S = 60.0
# a Power BI MCP with routing on re-attaches this often, so routing comes back after a gateway
# restart (a reboot, a user restart, a hook start) without restarting the MCP
ATTACH_HEARTBEAT_S = 30.0
UNMASK_TIMEOUT_S = 15.0
MASK_IMAGE_TIMEOUT_S = 90.0


def _texts_reply(reply: Optional[Tuple[int, Dict[str, Any]]], expected: int) -> Optional[List[str]]:
    """The ``texts`` of a 200 reply: ``expected`` strings, or None (never a partial list)."""
    if reply is None or reply[0] != 200 or reply[1].get("ok") is not True:
        return None
    texts = reply[1].get("texts")
    if not isinstance(texts, list) or len(texts) != expected:
        return None
    if not all(isinstance(t, str) for t in texts):
        return None
    return texts


_LABEL = re.compile(r"[a-z0-9_]{1,48}")


def _degraded_extra(body: Dict[str, Any]) -> Dict[str, Any]:
    """The ``degraded`` note of a 200 reply (fail mode degrade): ``{}`` when the gateway did not
    flag one, else ``degraded`` / ``degraded_reason`` / ``degraded_classes`` reduced to fixed
    labels (anything else is dropped)."""
    if body.get("degraded") is not True:
        return {}
    reason = body.get("degraded_reason")
    extra: Dict[str, Any] = {
        "degraded": True,
        "degraded_reason": (
            reason
            if isinstance(reason, str) and _LABEL.fullmatch(reason)
            else "dictionary_not_ready"
        ),
    }
    classes = body.get("degraded_classes")
    if isinstance(classes, list):
        labels = [c for c in classes if isinstance(c, str) and _LABEL.fullmatch(c)]
        if labels:
            extra["degraded_classes"] = labels[:16]
    return extra


def _failure_reason(reply: Optional[Tuple[int, Dict[str, Any]]]) -> str:
    """The fixed code for a reply that is not a usable 200 (``None`` = the transport failed)."""
    if reply is None:
        return _transport_reason()
    status, body = reply
    if status == 403:
        return REASON_TOKEN
    if status == 503:
        return REASON_NOT_READY if body.get("error") == "not_ready" else REASON_MASK_FAILED
    return REASON_BAD_REPLY


def _texts_ex(
    reply: Optional[Tuple[int, Dict[str, Any]]], expected: int
) -> Tuple[Optional[List[str]], str, Dict[str, Any]]:
    texts = _texts_reply(reply, expected)
    if texts is None or reply is None:
        reason = _failure_reason(reply) if reply is None or reply[0] != 200 else REASON_BAD_REPLY
        return None, reason, {}
    return texts, "", _degraded_extra(reply[1])


def mask_texts_ex(
    texts: List[str], whole: bool = True, timeout: float = MASK_TIMEOUT_S
) -> Tuple[Optional[List[str]], str, Dict[str, Any]]:
    """``(texts, reason, extra)``: the masked texts and ``""`` (``extra`` carries the gateway's
    ``degraded`` note when its dictionary was not ready), or ``None`` and one of the ``REASON_*``
    codes. Never raises."""
    try:
        if not texts:
            return [], "", {}
        _local.transport = ""
        reply = request(
            "POST",
            "/__gateway/mask",
            {"texts": list(texts), "whole": bool(whole)},
            timeout=timeout,
        )
        return _texts_ex(reply, len(texts))
    except Exception:  # noqa: BLE001 - the caller decides (retry, degrade, fail closed)
        return None, REASON_BAD_REPLY, {}


def unmask_texts_ex(
    texts: List[str], timeout: float = UNMASK_TIMEOUT_S
) -> Tuple[Optional[List[str]], str, Dict[str, Any]]:
    """``unmask_texts`` with the reason of a failure (see ``mask_texts_ex``)."""
    try:
        if not texts:
            return [], "", {}
        _local.transport = ""
        reply = request("POST", "/__gateway/unmask", {"texts": list(texts)}, timeout=timeout)
        return _texts_ex(reply, len(texts))
    except Exception:  # noqa: BLE001
        return None, REASON_BAD_REPLY, {}


def mask_image_ex(
    png_bytes: bytes, timeout: float = MASK_IMAGE_TIMEOUT_S
) -> Tuple[Optional[Tuple[bytes, str]], str, Dict[str, Any]]:
    """``mask_image`` with the reason of a failure and the ``degraded`` note (see
    ``mask_texts_ex``)."""
    try:
        if not isinstance(png_bytes, (bytes, bytearray)) or not png_bytes:
            return None, REASON_BAD_REPLY, {}
        _local.transport = ""
        reply = request(
            "POST",
            "/__gateway/mask_image",
            {"png_b64": base64.b64encode(bytes(png_bytes)).decode("ascii")},
            timeout=timeout,
        )
        if reply is None or reply[0] != 200:
            return None, _failure_reason(reply), {}
        action, data = reply[1].get("action"), reply[1].get("png_b64")
        if (
            reply[1].get("ok") is not True
            or action not in ("clean", "redacted")
            or not isinstance(data, str)
            or not data
        ):
            return None, REASON_BAD_REPLY, {}
        return (base64.b64decode(data, validate=True), str(action)), "", _degraded_extra(reply[1])
    except Exception:  # noqa: BLE001 - includes a reply that is not valid base64
        return None, REASON_BAD_REPLY, {}


def mask_texts(
    texts: List[str], whole: bool = True, timeout: float = MASK_TIMEOUT_S
) -> Optional[List[str]]:
    """``texts`` masked by the gateway's one authority, or None (unreachable / refused / failed)."""
    return mask_texts_ex(texts, whole, timeout)[0]


def unmask_texts(texts: List[str], timeout: float = UNMASK_TIMEOUT_S) -> Optional[List[str]]:
    """``texts`` with the gateway's pseudonyms replaced by the real values (unknown ones stay), or
    None (unreachable / refused / failed)."""
    return unmask_texts_ex(texts, timeout)[0]


def mask_image(
    png_bytes: bytes, timeout: float = MASK_IMAGE_TIMEOUT_S
) -> Optional[Tuple[bytes, str]]:
    """``(image bytes, action)`` with ``action`` ``"clean"`` (unchanged) or ``"redacted"``; None
    when the gateway cannot make the image safe (the caller withholds it)."""
    return mask_image_ex(png_bytes, timeout)[0]


def fail_mode() -> str:
    """Config ``client_gateway.fail_mode``: ``"degrade"`` (default: never refuse a call, mask with
    what is known and report it) or ``"closed"`` (the strict behaviour). Anything else gives
    ``"degrade"``."""
    try:
        from core.config.config_manager import config

        return normalise_fail_mode(config.get("client_gateway.fail_mode", FAIL_MODE_DEGRADE))
    except Exception:  # noqa: BLE001 - config not loadable: the default
        return FAIL_MODE_DEGRADE


# ------------------------------------------------------------------------------ process
def _windows_flags(detached: bool) -> int:
    if os.name != "nt":
        return 0
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if detached:
        flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return flags


def gateway_python(windowless: bool = True) -> str:
    """The repo venv interpreter (``pythonw.exe`` when windowless), else this interpreter."""
    name = "pythonw.exe" if windowless else "python.exe"
    candidate = _REPO_ROOT / "venv" / "Scripts" / name
    if candidate.is_file():
        return str(candidate)
    sibling = Path(sys.executable).with_name(name)
    return str(sibling) if sibling.is_file() else sys.executable


def routing_wanted() -> bool:
    """Config ``client_gateway.routing`` (default off): the owner approved routing the desktop apps
    through the gateway. While off the MCP never attaches to a lifecycle and starts nothing."""
    try:
        from core.config.config_manager import config

        value = config.get("client_gateway.routing", False)
    except Exception:  # noqa: BLE001 - config not loadable: off
        return False
    return str(value).strip().lower() in ("1", "true", "on", "yes")


def child_env() -> Dict[str, str]:
    """The environment of a gateway started from here: this one, plus (routing on only) the
    permission to change the user's certificate store and proxy settings (``routing.guard``)."""
    env = dict(os.environ)
    if routing_wanted():
        env["FINVISION_MASKING_REAL_SYSTEM"] = "1"
    return env


def spawn_gateway() -> None:
    """Start ``python -m client_gateway start`` detached and hidden (no wait)."""
    subprocess.Popen(  # noqa: S603 - fixed argv
        [gateway_python(True), "-m", "client_gateway", "start"],
        cwd=str(_REPO_ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_windows_flags(True),
        close_fds=True,
        start_new_session=os.name != "nt",
        env=child_env(),
    )


def attach_pid(pid: Optional[int] = None) -> bool:
    """Register this process (a Power BI MCP) with the routed gateway's lifecycle. False when the
    gateway is not reachable, runs without routing (409) or refuses; never raises."""
    reply = request("POST", "/__gateway/attach", {"pid": pid if pid is not None else os.getpid()})
    return reply is not None and reply[0] == 200


def detach_pid(pid: Optional[int] = None) -> bool:
    reply = request("POST", "/__gateway/detach", {"pid": pid if pid is not None else os.getpid()})
    return reply is not None and reply[0] == 200


def start_gateway(wait_s: float = 8.0) -> Dict[str, Any]:
    """Make sure the gateway runs: health check, else start it detached and wait for health."""
    info = health()
    if info is not None:
        return {"running": True, "started": False, "health": info}
    try:
        spawn_gateway()
    except Exception as exc:  # noqa: BLE001
        return {"running": False, "started": False, "error": type(exc).__name__}
    deadline = time.monotonic() + max(0.0, wait_s)
    while True:
        info = health()
        if info is not None:
            return {"running": True, "started": True, "health": info}
        if time.monotonic() >= deadline:
            return {"running": False, "started": True, "error": "no_health_answer"}
        time.sleep(0.25)


def stop_gateway(timeout_s: float = 20.0) -> Dict[str, Any]:
    """Run ``python -m client_gateway stop`` and report whether the gateway is gone."""
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv
            [gateway_python(False), "-m", "client_gateway", "stop"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            creationflags=_windows_flags(False),
            stdin=subprocess.DEVNULL,
        )
        code = proc.returncode
    except Exception as exc:  # noqa: BLE001
        return {"stopped": False, "error": type(exc).__name__}
    deadline = time.monotonic() + 3.0
    while health() is not None and time.monotonic() < deadline:
        time.sleep(0.2)
    return {"stopped": health() is None, "exit_code": code}


def _skip_autostart() -> bool:
    return os.environ.get(AUTOSTART_ENV, "1").strip() == "0" or "pytest" in sys.modules


def ensure_gateway_background() -> Optional[threading.Thread]:
    """Server start: ONLY when routing of the desktop apps is on (config ``client_gateway.routing``,
    opt-in), make sure the gateway runs, in a daemon thread, and attach this process to its
    lifecycle (it exits when no attached process is left, rule R1). For Claude Code the gateway is
    started by the user's typed ``masking on`` alone and stops when nothing is routed to it
    (owner rule 2026-10-08, ``client_gateway.switch``): the MCP starts nothing. Never blocks,
    never raises. Skipped under pytest and with ``FINVISION_GATEWAY_AUTOSTART=0``."""
    try:
        if _skip_autostart():
            return None
        routing = routing_wanted()
        if not routing:
            return None

        def _run() -> None:
            try:
                result = start_gateway()
                if not result.get("running"):
                    logger.warning("client gateway did not start (%s)", result.get("error"))
                elif routing and attach_pid():
                    atexit.register(detach_pid)
            except Exception as exc:  # noqa: BLE001
                logger.warning("client gateway start failed (%s)", type(exc).__name__)
            # with routing on, keep the attachment alive: a gateway that restarted (a reboot, a
            # user restart, a hook start) has an empty attach list and would never route again
            while routing:
                time.sleep(ATTACH_HEARTBEAT_S)
                try:
                    if health() is None:
                        start_gateway()
                    attach_pid()
                except Exception:  # noqa: BLE001 - a heartbeat never stops the MCP
                    pass

        thread = threading.Thread(target=_run, name="client-gateway-start", daemon=True)
        thread.start()
        return thread
    except Exception as exc:  # noqa: BLE001
        logger.warning("client gateway autostart skipped (%s)", type(exc).__name__)
        return None


def counts_summary(status_reply: Any) -> Dict[str, Any]:
    """Dictionary COUNTS (never values) out of a status reply: ``{"total": n, "by_prefix": {...}}``,
    or an empty dict when the reply holds none."""
    if not isinstance(status_reply, dict):
        return {}
    box = next(
        (
            status_reply[k]
            for k in ("dictionary", "dictionary_by_prefix", "counts")
            if isinstance(status_reply.get(k), dict)
        ),
        None,
    )
    if box is None:
        return {}
    parts: Dict[str, int] = {}
    for key, value in box.items():
        if key in ("total", "values"):
            continue
        if isinstance(value, int) and not isinstance(value, bool):
            parts[key] = parts.get(key, 0) + value
        elif isinstance(value, dict):
            for k2, v2 in value.items():
                if isinstance(v2, int) and not isinstance(v2, bool):
                    parts[k2] = parts.get(k2, 0) + v2
    total = box.get("total", box.get("values"))  # the gateway's status says "values"
    if not isinstance(total, int) or isinstance(total, bool):
        total = sum(parts.values())
    return {"total": total, "by_prefix": dict(sorted(parts.items()))}
