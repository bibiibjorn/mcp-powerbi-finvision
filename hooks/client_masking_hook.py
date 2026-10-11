r"""Claude Code hooks for client masking (finvision MCP): standard library + optional psutil.

Usage (``~/.claude/settings.json``), ``python client_masking_hook.py <event>``:

  session  SessionStart hook: in a ROUTED chat (ANTHROPIC_BASE_URL on the local gateway, from the
           user settings or the project's .claude/settings.local.json) makes sure the gateway runs
           and tells the model and the user how masking stands in this chat. Elsewhere: nothing.
  prompt   UserPromptSubmit hook. In ANY chat the typed commands `masking on|off|status` (or
           `client mode ...`) are answered locally, the model never sees them (owner rule
           2026-10-08, masking on demand, ``client_gateway.switch``): `masking on` starts the
           gateway, switches this chat on and routes its project folder; `masking off` switches
           the folder's chats off and unroutes it, and the gateway then stops by itself. In a
           routed chat the gateway is also restarted when it is down (every prompt). The token
           that allows `off` is read here and by the CLI only.
  pretool  PreToolUse hook in a routed chat: blocks the claude.ai connectors (mcp__claude_ai_*,
           they bypass the gateway) and the sources the gateway's dictionary does not cover (Fabric
           data queries, connect_xmla) while the chat's mode is on, and always blocks the ACTIONS
           that would switch the gateway off, stop it, unroute the chat or read its token (never
           mere mentions in code work); the gateway masks every other tool result itself. That
           block is a SPEED BUMP against a careless or prompt-injected model, not a security
           boundary: the token is a file of the same OS user.

In a chat that is not routed every hook does nothing but answer the typed commands. Any error,
timeout or missing file means "nothing to do": exit 0, no output. These hooks can never stop the
chat. This file is run by path from any folder; only the typed `masking on|off|status` import the
repository (``client_gateway.switch``, lazily).
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

TIMEOUT_S = 3.0
FABRIC_DATA_TOOLS = (
    "06_run_query",
    "08_query_semantic_model",
    "30_livy_session",
    "37_run_kql_query",
    "54_query_log_analytics",
    "59_invoke_data_agent",
    "31_data_validation",
)
_MASKING_DIR_REF = re.compile(r"(?:appdata/local|localappdata)[%)}\"']*/+[\"']?finvisionmasking")
_HTTP_CLIENT = re.compile(
    r"\b(?:curl|wget|invoke-webrequest|invoke-restmethod|iwr|irm|urllib|requests|httpx|"
    r"http\.client|aiohttp|socket|fetch|webclient|ncat|nc|telnet)\b"
)


def _PORT_REF(port: str) -> "re.Pattern[str]":  # noqa: N802
    return re.compile(rf"(?:127\.0\.0\.1|localhost|\[?::1\]?)[\"'\s]*[:,]\s*{re.escape(port)}\b")


# ------------------------------------------------------------------------------ helpers
def _home() -> Path:
    override = os.environ.get("FINVISION_MASKING_HOME")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "FinvisionMasking"


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path)) if path else ""


# ------------------------------------------------------------------------------ gateway (routed)
# A "routed" session sends its API traffic through the local gateway (ANTHROPIC_BASE_URL in the
# project's .claude/settings.local.json). The gateway masks requests and rehydrates responses while
# a chat's mode is on. Masking is OFF by default (config client_gateway.default_mode) with a safety
# net that stops a request whose newest message holds client data; the user switches a chat on by
# typing "masking on". Control endpoints: /__gateway/health|status|mode|session (status, session and
# refresh need the control token, which this file reads from the masking folder).
_TOKEN_PATHS = ("/__gateway/status", "/__gateway/session", "/__gateway/refresh")
try:  # the one definition lives in client_gateway/defaults.py, read by path (no package import)
    _spec = importlib.util.spec_from_file_location(
        "_gateway_defaults",
        Path(__file__).resolve().parent.parent / "client_gateway" / "defaults.py",
    )
    _shared = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
    _spec.loader.exec_module(_shared)  # type: ignore[union-attr]
    GATEWAY_PORT = int(_shared.DEFAULT_PORT)
except Exception:  # noqa: BLE001 - the hook must work without the repo next to it
    GATEWAY_PORT = 8890
GATEWAY_TOKEN_HEADER = "X-Gateway-Token"
GATEWAY_TOKEN_FILE = "gateway.token"
START_WAIT_S = 8.0
MAX_WALK = 16
# "client mode off|on|status|dictionary", or the short form "masking off|on|status|dictionary"
# (dictionary = on, but name recognition that cannot finish is skipped instead of refusing)
_COMMAND = re.compile(
    r"^\s*/?(?:client\s+mode|masking)\s+(off|on|status|dictionary)\s*[.!?;:,]*\s*$",
    re.IGNORECASE,
)

# 00_Client_Mode gateway_start cannot help here: it needs the model, and every model call of a
# routed session goes to the (dead) gateway. The user starts it from a terminal.
START_COMMAND = "python -m client_gateway start"
NOT_RUNNING = (
    "Client gateway is not running and could not be restarted (nothing was sent). Start it in a "
    "terminal: cd {repo} && venv\\Scripts\\" + START_COMMAND
)
NOT_STARTED = (
    "Client gateway did not start: requests in this folder will fail. Start it in a terminal: "
    "cd {repo} && venv\\Scripts\\" + START_COMMAND + " . If it keeps failing, send every chat "
    "to Anthropic directly again: cd {repo} && venv\\Scripts\\python -m client_gateway unroute-all"
)
UNROUTED_REGISTERED = (
    "Client masking: this folder is registered as routed through the gateway, but its "
    ".claude/settings.local.json no longer sends traffic there. This session is NOT masked: it "
    "goes straight to Anthropic. Route the folder again (00_Client_Mode route_folder) or work "
    "elsewhere."
)
MODEL_CONTEXT_ON = (
    "Client masking is ON in this session: client identifiers reach you as pseudonyms such as "
    "CUS_48213, PCC_20311, ORG_10442. Use them verbatim; the local gateway restores real values "
    "for the user and for tool calls. Never ask for real values, never try to switch masking off."
)
USER_MSG_ON = "Client masking ON for this session (type: masking off for dev data)."
SWITCH_ON = (
    "Client masking ON for this chat. The masking gateway is running{started}: from your next "
    "message on, client names are replaced by codes before anything leaves this PC, and put back "
    "in the answers you read. Type masking off to stop it."
)
SWITCH_ON_FAILED = (
    "Client masking could NOT be switched on ({error}). Nothing was changed: this chat is NOT "
    "masked. Try again, or start the gateway in a terminal: cd {repo} && "
    "venv\\Scripts\\python -m client_gateway start"
)
SWITCH_OFF = (
    "Client masking OFF in this folder ({n} chat(s) switched off): client values now reach "
    "Anthropic as they are. {gateway} Type masking on to switch it back on."
)
SWITCH_OFF_STOPS = "The masking gateway stops by itself in about half a minute."
SWITCH_OFF_KEEPS = "The masking gateway keeps running for {n} other folder(s) with masking on."
SWITCH_OFF_EVERY_CHAT = (
    "The masking gateway keeps running: an older setup still sends EVERY chat through it. Run "
    "python -m client_gateway setup --yes in the repo to switch that off."
)
SWITCH_STATUS = "Client masking is {mode} for this chat. The masking gateway is {running}."
# counters of this gateway run, shown only when not zero (a degraded step must be visible)
SWITCH_STATUS_COUNTS = {
    "masked_requests": "{n} request(s) masked",
    "blocked": "{n} request(s) blocked because they could not be masked safely (nothing was sent)",
    "ner_partial": (
        "{n} message(s) went on before name recognition finished (names in the dictionary were "
        "masked; a brand-new name could have passed)"
    ),
    "system_values_passed": (
        "{n} client value(s) in Claude Code's identity lines, which are sent as they are"
    ),
}
SETTLE_S = 4.0  # after "masking on": the time Claude Code takes to re-read its settings
MASKING_PENDING = (
    "Client masking is being switched on and Claude Code has not picked it up yet: your message "
    "was NOT sent (it would have gone out unmasked). Send it again in a few seconds."
)
USER_MSG_OFF = (
    "Client masking off in this chat (the default): nothing is masked. A safety net stops a "
    "message that holds known client data. Type: masking on before working with client data."
)
USER_MSG_UNKNOWN = (
    "Client masking: the gateway did not report this chat's mode (treated as ON for connectors). "
    "Type: masking status."
)
WARNING_TEXTS = {
    "dictionary_empty": "the client dictionary is empty (no model read, nothing remembered): only "
    "names the recognizer finds and e-mail / IBAN / phone shapes are masked",
    "store_error": "the remembered-value store cannot be read: only the models open now are known",
}
REPLY_DICTIONARY = (
    "Client masking ON for this session, dictionary only: names that no model holds are masked "
    "only when the name recognizer is ready. Type masking on for the full check."
)
REPLY_ON = "Client masking ON for this session."
REPLY_OFF = (
    "Client masking OFF for this session: client values now reach Anthropic as they are. "
    "Type masking on to switch back."
)
ARTIFACT_TOOLS = frozenset({"Artifact", "ArtifactComments", "ArtifactData"})
CONNECTOR_REASON = (
    "Client masking is ON in this session: {tool} is a claude.ai connector that runs on "
    "Anthropic's side, outside the local gateway, and would carry real client values. Use a "
    "local tool instead. The user can type 'masking off' in the chat to allow connectors "
    "(for dev data only)."
)
GATEWAY_GUARD_REASON = (
    "Client gateway: this would switch the gateway off, stop it, unroute the folder or read its "
    "control token. Only the user can do that, by typing 'masking off' in the chat. Read its "
    "state with the 00_Client_Mode tool (operation gateway_status)."
)
UNCOVERED_REASON = (
    "Client masking is ON in this session: {what} reads data from a source the gateway's "
    "dictionary does not cover (it reads the models open in Power BI Desktop only), so its "
    "result would reach Anthropic unmasked. The user can type 'masking off' in the chat to "
    "allow it (for dev data only)."
)

# Bounded distances: a command is judged as one line (newlines collapsed), so an unbounded gap
# would join a mention of the package with a later "stop" anywhere in a heredoc or a doc text.
_GW_SWITCH = re.compile(
    r"client[_.\- ]?gateway\b[^\n;|&]{0,60}\bmode\b[^\n;|&]{0,40}\boff\b"
    r"|client[_.\- ]?gateway\b[^\n;|&]{0,40}\bstop\b",
    re.IGNORECASE,
)
_GW_KILL = re.compile(
    r"(?:taskkill|stop-process|pkill|killall|\bkill\b|wmic)[^\n]{0,120}client[_.\- ]?gateway"
    r"|client[_.\- ]?gateway[^\n]{0,120}(?:taskkill|stop-process|pkill|killall|\bkill\b)",
    re.IGNORECASE,
)
_GW_URL = re.compile(r"__gateway", re.IGNORECASE)
# ACTIONS on the gateway's internals, never mere mentions (reading, grepping, editing or testing the
# code stays allowed): RUNNING the hook with an event (forging the typed command), importing the
# CLI / control client (they read the token themselves), any CLI subcommand but status / start /
# help, and the masking folder's environment variable (it names the token's folder).
_GW_INTERNALS = re.compile(
    r"client_masking_hook(?:\.py)?[\"']?\s+(?:prompt|session|pretool|stop)\b"
    r"|(?<![\w])(?:from|import)\s+client_masking_hook\b"
    r"|(?<![\w])(?:from|import)\s+client_gateway\.(?:cli|control_client)\b"
    r"|(?<![\w])from\s+client_gateway\s+import\s+[^\n;]*\b(?:cli|control_client)\b"
    r"|-m\s+client_gateway\s+(?!(?:status|start|--help|-h)\b)\S"
    r"|-m\s+client_gateway\s+start\b[^\n;|&]*--replace"  # replacing the LIVE gateway: user only
    r"|[$%]\{?(?:env:)?finvision_masking_home",
    re.IGNORECASE,
)
# removing or blanking the routing key in a settings file (adding it is fine)
_ROUTING_REMOVAL = re.compile(
    r"\bsed\b[^\n]*-i|\.pop\s*\(|\bdel\s|\bunset\b|\bremove\b|\bdelete\b|remove-itemproperty"
    r"|['\"]anthropic_base_url['\"]\s*[:=]\s*(?:['\"]{2}|none|null)",
    re.IGNORECASE,
)
_CONTINUATION = re.compile(r"(?:\\|\^|`)[ \t]*\r?\n")
_ROUTING_KEY = re.compile(r"anthropic_base_url", re.IGNORECASE)
_SETTINGS_FILE = re.compile(r"(?:^|/)\.claude/settings(?:\.local)?\.json$", re.IGNORECASE)
_SETTINGS_MENTION = re.compile(r"settings(?:\.local)?\.json", re.IGNORECASE)
_FILE_REMOVE = re.compile(
    r"\b(?:rm|del|erase|remove-item|ri|unlink|mv|move|move-item|ren|rename-item)\b[^|;&\n]*"
    r"settings[^\s'\"|;&/\\]*\.json",
    re.IGNORECASE,
)
# ``git rm --cached`` only drops a file from the index: the working file (and its routing key) stays
_GIT_RM_CACHED = re.compile(r"\bgit\b[^|;&\n]*\brm\b[^|;&\n]*--cached\b", re.IGNORECASE)
_SETTINGS_PATH = re.compile(r"""[^\s'"<>|;&=()]*settings[^\s'"|;&/\\]*\.json""", re.IGNORECASE)
_QUOTED_SETTINGS_PATH = re.compile(
    r"""["']([^"'\n]*settings[^"'/\\\n]*\.json)["']""", re.IGNORECASE
)
_PATH_UNRESOLVED = re.compile(r"[$%*?\[\]{}`]")  # a variable or a glob: which file is not known
_CHANGES_DIR = re.compile(r"(?:^|[\s;&|(])(?:cd|chdir|pushd|set-location|sl)\s", re.IGNORECASE)
_GW_TOKEN = re.compile(r"gateway\.(?:t[o*?\[]|[*?\[])", re.IGNORECASE)  # token, or a glob of it
_FILE_KEYS = ("file_path", "path", "notebook_path", "pattern", "glob")


_REPO_ROOT = Path(__file__).resolve().parent.parent


def _gateway_port() -> int:
    """Env FINVISION_GATEWAY_PORT, else config client_gateway.port, else 8890."""
    raw = os.environ.get("FINVISION_GATEWAY_PORT", "").strip()
    if raw.isdigit() and 0 < int(raw) < 65536:
        return int(raw)
    port = GATEWAY_PORT
    for name in ("default_config.json", "local_config.json"):
        try:
            section = json.loads((_REPO_ROOT / "config" / name).read_text("utf-8-sig"))
            value = (section.get("client_gateway") or {}).get("port")
            if isinstance(value, int) and not isinstance(value, bool) and 0 < value < 65536:
                port = value
        except (OSError, ValueError, AttributeError):
            continue
    return port


def _is_gateway_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlparse(value.strip())
        return (
            parsed.scheme == "http"
            and (parsed.hostname or "") in ("127.0.0.1", "localhost", "::1")
            and parsed.port == _gateway_port()
        )
    except ValueError:
        return False


def routed_base(cwd: Any) -> Optional[str]:
    """The gateway base URL when this session is routed through it, else None.

    Routed = this process runs with ANTHROPIC_BASE_URL on the local gateway, or the project's
    ``.claude/settings.local.json`` (in cwd or the closest folder above it, up to the repository
    root) says so."""
    env_url = os.environ.get("ANTHROPIC_BASE_URL")
    if _is_gateway_url(env_url):
        return str(env_url).strip().rstrip("/")
    try:
        here = Path(os.path.abspath(str(cwd or os.getcwd())))
    except (OSError, ValueError):
        return None
    for folder in [here, *here.parents][:MAX_WALK]:
        try:
            data = json.loads((folder / ".claude" / "settings.local.json").read_text("utf-8-sig"))
            env = data.get("env") if isinstance(data, dict) else None
            url = env.get("ANTHROPIC_BASE_URL") if isinstance(env, dict) else None
            if _is_gateway_url(url):
                return str(url).strip().rstrip("/")
        except (OSError, ValueError):
            pass
        if (folder / ".git").exists():
            break
    return None


def _gw_request(
    base: str,
    method: str,
    path: str,
    body: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = TIMEOUT_S,
) -> Optional[Tuple[int, Dict[str, Any]]]:
    """(status, json) from the gateway, or None when it cannot be reached."""
    import urllib.error  # noqa: PLC0415 - kept off the startup path of every tool call
    import urllib.request  # noqa: PLC0415

    data = json.dumps(body).encode("utf-8") if body is not None else None
    hdrs = {"Content-Type": "application/json"} if data is not None else {}
    if path.startswith(_TOKEN_PATHS):
        token = _read_token()
        if token:
            hdrs[GATEWAY_TOKEN_HEADER] = token
    hdrs.update(headers or {})
    req = urllib.request.Request(base + path, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - loopback
            raw, code = resp.read(), resp.status
    except urllib.error.HTTPError as exc:
        try:
            raw, code = exc.read(), exc.code
        except Exception:  # noqa: BLE001
            raw, code = b"", exc.code
    except Exception:  # noqa: BLE001 - refused / timeout / reset
        return None
    try:
        parsed = json.loads(raw or b"{}")
    except ValueError:
        parsed = {}
    return code, parsed if isinstance(parsed, dict) else {}


def _find_mode(data: Any) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    mode = data.get("mode")
    if isinstance(mode, str) and mode.lower() in ("on", "off"):
        return mode.lower()
    return _find_mode(data.get("session"))


def _session_mode(base: str, sid: Any, timeout: float = 1.0) -> Optional[str]:
    """'on' / 'off' for the session, None when the gateway cannot say."""
    if not isinstance(sid, str) or not sid:
        return None
    reply = _gw_request(
        base, "GET", "/__gateway/session?id=" + quote(sid, safe=""), timeout=timeout
    )
    return _find_mode(reply[1]) if reply is not None and reply[0] == 200 else None


def _healthy(base: str) -> bool:
    reply = _gw_request(base, "GET", "/__gateway/health", timeout=1.0)
    return reply is not None and reply[0] == 200


def _port_open(base: str) -> bool:
    """A busy gateway may miss a short HTTP health timeout while still owning the port."""
    import socket  # noqa: PLC0415

    try:
        parsed = urlparse(base)
        with socket.create_connection(
            (parsed.hostname or "127.0.0.1", parsed.port or GATEWAY_PORT), timeout=0.5
        ):
            return True
    except (OSError, ValueError):
        return False


def _spawn_gateway() -> None:
    """``<repo>/venv/Scripts/pythonw.exe -m client_gateway start``, detached and hidden."""
    import subprocess  # noqa: PLC0415

    exe = _REPO_ROOT / "venv" / "Scripts" / "pythonw.exe"
    if not exe.is_file():
        sibling = Path(sys.executable).with_name("pythonw.exe")
        exe = sibling if sibling.is_file() else Path(sys.executable)
    flags = 0
    if os.name == "nt":
        flags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    subprocess.Popen(  # noqa: S603 - fixed argv
        [str(exe), "-m", "client_gateway", "start"],
        cwd=str(_REPO_ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=True,
        start_new_session=os.name != "nt",
    )


def _ensure_gateway(base: str, wait_s: Optional[float] = None) -> bool:
    if _healthy(base):
        return True
    if not _port_open(base):
        try:
            _spawn_gateway()
        except Exception:  # noqa: BLE001
            return False
    deadline = time.monotonic() + (START_WAIT_S if wait_s is None else wait_s)
    while time.monotonic() < deadline:
        if _healthy(base):
            return True
        time.sleep(0.25)
    return _healthy(base)


def _read_token() -> Optional[str]:
    try:
        text = (_home() / GATEWAY_TOKEN_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def _counts_text(status: Any) -> str:
    """'Dictionary: 120 values (CUS 80, ORG 40).' from a status reply. Counts only."""
    if not isinstance(status, dict):
        return ""
    box = next(
        (
            status[k]
            for k in ("dictionary", "dictionary_by_prefix", "counts")
            if isinstance(status.get(k), dict)
        ),
        None,
    )
    if box is None:
        return ""
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
    detail = ", ".join(f"{k} {v}" for k, v in sorted(parts.items()))
    return f"Dictionary: {total} values" + (f" ({detail})" if detail else "") + "."


def _emit(obj: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj))


def _block(reason: str) -> int:
    _emit({"decision": "block", "reason": reason})
    return 0


def _registered_but_unrouted(cwd: Any) -> bool:
    """True when ``cwd`` is inside a folder the gateway registry lists as routed, although no
    settings file routes it any more (someone removed the key: the session would be unmasked and
    nothing else says so)."""
    try:
        here = _norm(str(cwd or os.getcwd()))
        data = json.loads((_home() / "gateway_folders.json").read_text("utf-8-sig"))
        folders = data.get("folders") if isinstance(data, dict) else None
        for entry in (folders or {}).values():
            path = entry.get("path") if isinstance(entry, dict) else None
            if not isinstance(path, str) or not path:
                continue
            root = _norm(path).rstrip("\\/")
            if here == root or here.startswith(root + os.sep):
                return True
    except (OSError, ValueError, AttributeError):
        pass
    return False


def on_session(payload: Dict[str, Any]) -> int:
    """SessionStart: routed folder -> gateway running + the session's masking context."""
    base = routed_base(payload.get("cwd"))
    if base is None:
        if _registered_but_unrouted(payload.get("cwd")):
            _emit({"systemMessage": UNROUTED_REGISTERED})
        return 0
    if not _ensure_gateway(base):
        _emit({"systemMessage": NOT_STARTED.format(repo=_REPO_ROOT)})
        return 0
    mode = _session_mode(base, payload.get("session_id"), timeout=TIMEOUT_S)
    if mode == "off":
        _emit({"systemMessage": USER_MSG_OFF + _warnings_text(base)})
    else:
        _emit(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": MODEL_CONTEXT_ON,
                },
                "systemMessage": (USER_MSG_ON if mode == "on" else USER_MSG_UNKNOWN)
                + _warnings_text(base),
            }
        )
    return 0


def _warnings_text(base: str) -> str:
    """' Warning: ...' for a gateway that reports a weak dictionary (empty, or a store error), else
    ''. Masking can be ON and still hold nothing but the name recognizer and the shape detectors."""
    reply = _gw_request(base, "GET", "/__gateway/status", timeout=TIMEOUT_S)
    codes = reply[1].get("warnings") if reply is not None and reply[0] == 200 else None
    if not isinstance(codes, list):
        return ""
    texts = [
        WARNING_TEXTS[str(c).split(":", 1)[0]]
        for c in codes
        if str(c).split(":", 1)[0] in WARNING_TEXTS
    ]
    return (" Warning: " + "; ".join(texts) + ".") if texts else ""


def _pending_guard(payload: Dict[str, Any], base: str) -> int:
    """A chat that switched masking ON whose Claude Code process has not yet picked up the new
    ``ANTHROPIC_BASE_URL`` (it re-reads its settings a few seconds after the change): its request
    would still go straight to Anthropic, unmasked. The hook runs with the process's environment,
    so it sees that and holds the message (measured 2026-10-08: a message sent 3 s after
    "masking on" still went direct)."""
    if _is_gateway_url(os.environ.get("ANTHROPIC_BASE_URL")):
        return 0  # this process already talks to the gateway
    sid = payload.get("session_id")
    if not isinstance(sid, str) or not sid:
        return 0
    if _session_mode(base, sid, timeout=TIMEOUT_S) != "on":
        return 0  # not a masked chat: it may go direct
    return _block(MASKING_PENDING)


def on_prompt_routed(payload: Dict[str, Any], base: str) -> int:
    """UserPromptSubmit in a routed session: `client mode off|on|status` is answered here and
    never reaches the model. Any other prompt passes (the gateway masks it)."""
    # Self-healing: a gateway that died (crash, kill, replaced by another start) is restarted by
    # the next prompt instead of leaving the session with refused connections.
    if not _ensure_gateway(base):
        return _block(NOT_STARTED.format(repo=_REPO_ROOT))
    prompt = payload.get("prompt")
    match = _COMMAND.match(prompt) if isinstance(prompt, str) else None
    if match is None:
        return _pending_guard(payload, base)
    command = match.group(1).lower()
    sid = payload.get("session_id")
    if not isinstance(sid, str) or not sid:
        return _block(
            "Client masking: this message carries no session id, so nothing was "
            "changed (nothing was sent)."
        )
    if not _healthy(base):
        return _block(NOT_RUNNING.format(repo=_REPO_ROOT))
    if command == "status":
        mode = _session_mode(base, sid, timeout=TIMEOUT_S)
        counts = (
            _counts_text(_gw_request(base, "GET", "/__gateway/status")[1]) if _healthy(base) else ""
        )
        head = (
            "Client masking is %s for this session." % mode.upper()
            if mode
            else "Client masking: the gateway did not report this session's mode (treated as ON)."
        )
        if mode == "off":
            head += " (the default: a safety net stops a message holding known client data)"
        return _block((head + " " + counts + _warnings_text(base)).strip())
    headers: Dict[str, str] = {}
    if command in ("off", "dictionary"):  # both weaken the masking: the user's token only
        token = _read_token()
        if token is None:
            return _block(
                "Client masking was NOT %s: the gateway token is missing. "
                "Start the gateway first (%s)."
                % ("switched off" if command == "off" else "changed", START_COMMAND)
            )
        headers[GATEWAY_TOKEN_HEADER] = token
    body: Dict[str, Any] = {"session": sid, "mode": "on" if command == "dictionary" else command}
    if command == "dictionary":
        body["ner"] = "degrade"
    reply = _gw_request(base, "POST", "/__gateway/mode", body, headers)
    if reply is None:
        return _block(NOT_RUNNING.format(repo=_REPO_ROOT))
    code, data = reply
    if code in (401, 403):
        return _block(
            "Client masking was NOT changed: the gateway did not accept the token "
            "(it may have been restarted). Try again."
        )
    if code != 200 or data.get("ok") is False:
        return _block("Client masking was NOT changed: the gateway answered %s." % code)
    seen = _session_mode(base, sid, timeout=TIMEOUT_S)
    want = "on" if command == "dictionary" else command
    if seen is not None and seen != want:
        return _block(
            "Client masking was NOT changed: the gateway still reports %s." % seen.upper()
        )
    if command == "dictionary":
        return _block(REPLY_DICTIONARY)
    return _block(REPLY_OFF if command == "off" else REPLY_ON)


def gateway_blocked(
    tool: str, tool_input: Dict[str, Any], base: str, sid: Any, cwd: Any = None
) -> Optional[str]:
    """The reason text when a tool call must be blocked in a routed session, else None."""
    if tool.startswith("mcp__claude_ai_") or tool in ARTIFACT_TOOLS:
        # claude.ai connectors and artifacts run on Anthropic's side: what they publish does not
        # pass the gateway, and in a masked chat it holds the REAL values the gateway restored
        if _session_mode(base, sid, timeout=1.0) != "off":  # unreachable gateway = on
            return CONNECTOR_REASON.format(tool=tool)
        return None
    short = tool.split("__")[-1].lower()
    if tool.startswith("mcp__") and short == "00_client_mode":
        # the AI may read and switch ON; it may not stop the gateway or take the folder out of it
        op = str(tool_input.get("operation") or "").strip().lower()
        refused = ("gateway_stop", "unroute_folder", "unroute_all")
        return GATEWAY_GUARD_REASON if op in refused else None
    if tool.startswith("mcp__"):
        what = _uncovered_source(tool, short, tool_input)
        if what and _session_mode(base, sid, timeout=1.0) != "off":
            return UNCOVERED_REASON.format(what=what)
        return None
    if tool in ("Bash", "PowerShell"):
        cmd = tool_input.get("command")
        if not isinstance(cmd, str):
            return None
        # a line continuation splits a keyword over two lines: judge the command as one line
        cmd = re.sub(r"\s+", " ", _CONTINUATION.sub("", cmd))
        if _GW_SWITCH.search(cmd) or _GW_KILL.search(cmd) or _GW_TOKEN.search(cmd):
            return GATEWAY_GUARD_REASON
        if _GW_INTERNALS.search(cmd) or _MASKING_DIR_REF.search(cmd.lower().replace("\\", "/")):
            return GATEWAY_GUARD_REASON
        low = cmd.lower().replace("\\", "/")
        if _removes_routing_file(cmd, cwd) or (
            _ROUTING_KEY.search(cmd)
            and _SETTINGS_MENTION.search(cmd)
            and _ROUTING_REMOVAL.search(cmd)
        ):
            return GATEWAY_GUARD_REASON  # takes the folder out of the gateway
        home = _norm(str(_home())).lower().replace("\\", "/")
        if home and (home in low or home.replace("c:/", "/c/") in low):
            return GATEWAY_GUARD_REASON
        if _GW_URL.search(cmd) and _HTTP_CLIENT.search(low):
            return GATEWAY_GUARD_REASON
        port = str(_gateway_port())
        if _PORT_REF(port).search(low) and _HTTP_CLIENT.search(low):
            return GATEWAY_GUARD_REASON
        return None
    if tool in ("Read", "Grep", "Glob", "NotebookEdit", "Edit", "MultiEdit", "Write"):
        home = _norm(str(_home()))
        for key in _FILE_KEYS:
            value = tool_input.get(key)
            if not isinstance(value, str) or not value:
                continue
            if _GW_TOKEN.search(value):
                return GATEWAY_GUARD_REASON
            if tool in ("Grep", "Glob") and key in ("path", "pattern", "glob"):
                if "finvisionmasking" in value.lower().replace("\\", "/"):
                    return GATEWAY_GUARD_REASON  # enumerates the masking folder by name
            if tool in ("Grep", "Glob") and key == "path":  # enumerates the folder holding it
                norm = _norm(value)
                if norm == home or norm.startswith(home.rstrip("\\/") + os.sep):
                    return GATEWAY_GUARD_REASON
        if tool in ("Edit", "MultiEdit", "Write", "NotebookEdit") and _edits_routing(
            tool, tool_input
        ):
            return GATEWAY_GUARD_REASON
    return None


def _may_hold_routing_key(token: str, cwd: Any, command: str) -> bool:
    """Could the settings file named by ``token`` be one that holds ANTHROPIC_BASE_URL? True when it
    does (read from disk, relative paths against the session's folder), and also when that cannot
    be told: a variable or glob in the path, a read error, or a relative path that does not exist
    after the command changed directory. Only a file that is there and holds no routing key, or one
    that is not there at all, is safe to remove."""
    if _PATH_UNRESOLVED.search(token):
        return True
    path = os.path.expanduser(token.strip())
    match = re.match(r"^/([a-zA-Z])/(.*)$", path.replace("\\", "/"))
    if match:  # an MSYS path (/c/Users/...) on Windows
        path = match.group(1).upper() + ":/" + match.group(2)
    absolute = os.path.isabs(path) or bool(re.match(r"^[a-zA-Z]:[\\/]", path))
    if not absolute:
        path = os.path.join(str(cwd or os.getcwd()), path)
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            return bool(_ROUTING_KEY.search(fh.read()))
    except FileNotFoundError:
        return (not absolute) and bool(_CHANGES_DIR.search(command))
    except OSError:
        return True


def _removes_routing_file(command: str, cwd: Any) -> bool:
    """Does the command delete or move a settings file that holds the routing key (the file that
    sends this folder, or every chat, through the gateway)? ``git rm --cached`` and the removal of
    a settings file that holds no routing key (or is not there) are not that."""
    for found in _FILE_REMOVE.finditer(command):
        segment = found.group(0)
        start = max(command.rfind(sep, 0, found.start()) for sep in ";|&\n") + 1
        if _GIT_RM_CACHED.search(command[start : found.end()]):
            continue  # the index only: the working file stays where it is
        paths = _SETTINGS_PATH.findall(segment) + _QUOTED_SETTINGS_PATH.findall(segment)
        if any(_may_hold_routing_key(p, cwd, command) for p in paths):
            return True
    return False


def _uncovered_source(tool: str, short: str, tool_input: Dict[str, Any]) -> str:
    """What a tool call reads that the gateway dictionary does NOT cover, else ''. The dictionary
    comes from the models open in Power BI Desktop (and the gateway restores nothing it does not
    know), so a published model over XMLA and the Fabric data lanes would reach Anthropic as
    they are."""
    if tool.startswith("mcp__ms-fabric-mcp__") and short in FABRIC_DATA_TOOLS:
        return "a Fabric data query"
    if short == "01_connection":
        if str(tool_input.get("operation") or "").strip().lower() == "connect_xmla":
            return "a connection to a published model (connect_xmla)"
    return ""


def _edits_routing(tool: str, tool_input: Dict[str, Any]) -> bool:
    """True when a file edit would remove or change ANTHROPIC_BASE_URL in a Claude settings file
    (the file that routes this folder through the gateway)."""
    path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not isinstance(path, str) or not _SETTINGS_FILE.search(path.replace("\\", "/")):
        return False
    pieces = [tool_input.get(k) for k in ("old_string", "new_string", "content")]
    for edit in tool_input.get("edits") or []:
        if isinstance(edit, dict):
            pieces += [edit.get("old_string"), edit.get("new_string")]
    blob = " ".join(p for p in pieces if isinstance(p, str))
    if _ROUTING_KEY.search(blob):
        return True
    if tool == "Write":  # a full rewrite that drops the key that is there now
        try:
            return bool(_ROUTING_KEY.search(Path(path).read_text("utf-8-sig")))
        except OSError:
            return False
    return False


def on_pretool_routed(payload: Dict[str, Any], base: str) -> int:
    tool_input = payload.get("tool_input")
    reason = gateway_blocked(
        str(payload.get("tool_name") or ""),
        tool_input if isinstance(tool_input, dict) else {},
        base,
        payload.get("session_id"),
        payload.get("cwd"),
    )
    if reason is None:
        return 0
    sys.stderr.write(reason)
    return 2


# ------------------------------------------------------------------------------ main
ROUTED_HANDLERS = {"prompt": on_prompt_routed, "pretool": on_pretool_routed}


def _switch_module() -> Any:
    """``client_gateway.switch`` of this repository (imported only for a typed command)."""
    root = str(_REPO_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    from client_gateway import switch  # noqa: PLC0415

    return switch


def on_command(payload: Dict[str, Any], command: str) -> int:
    """`masking on|off|status` in ANY chat: masking on demand (``client_gateway.switch``)."""
    sid = payload.get("session_id")
    cwd = payload.get("cwd")
    if not isinstance(sid, str) or not sid:
        return _block(
            "Client masking: this message carries no session id, so nothing was "
            "changed (nothing was sent)."
        )
    switch = _switch_module()
    if command == "on":
        result = switch.masking_on(sid, cwd)
        if not result.get("ok"):
            return _block(
                SWITCH_ON_FAILED.format(error=result.get("error", "unknown"), repo=_REPO_ROOT)
            )
        # Claude Code re-reads the settings a few seconds after the change: give it that time
        # here, so the next message normally finds the chat switched (_pending_guard covers the
        # rest)
        time.sleep(SETTLE_S)
        text = SWITCH_ON.format(started=" (started now)" if result.get("started") else "")
        if result.get("warning"):
            text += " Warning: " + str(result["warning"])
        return _block(text)
    if command == "off":
        result = switch.masking_off(sid, cwd)
        others = result.get("other_folders_routed") or []
        if result.get("gateway_stops"):
            gateway = SWITCH_OFF_STOPS
        elif result.get("every_chat_routed"):
            gateway = SWITCH_OFF_EVERY_CHAT
        else:
            gateway = SWITCH_OFF_KEEPS.format(n=max(1, len(others)))
        return _block(SWITCH_OFF.format(n=result.get("chats_switched_off", 0), gateway=gateway))
    info = switch.chat_status(sid, cwd)
    text = SWITCH_STATUS.format(
        mode=str(info.get("mode", "off")).upper(),
        running="running" if info.get("gateway_running") else "not running",
    )
    counts = info.get("counts") if isinstance(info.get("counts"), dict) else {}
    lines = [
        template.format(n=counts[name])
        for name, template in SWITCH_STATUS_COUNTS.items()
        if isinstance(counts.get(name), int) and counts[name] > 0
    ]
    if lines:
        text += " Since the gateway started: " + "; ".join(lines) + "."
    return _block(text)


def main(argv: List[str]) -> int:
    try:
        event = argv[1] if len(argv) > 1 else ""
        if event != "session" and event not in ROUTED_HANDLERS:
            return 0  # "stop" and unknown events: nothing to do (older settings may still call it)
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            return 0
        if event == "session":
            return on_session(payload)
        if event == "prompt":
            prompt = payload.get("prompt")
            match = _COMMAND.match(prompt) if isinstance(prompt, str) else None
            if match is not None and match.group(1).lower() in ("on", "off", "status"):
                try:
                    return on_command(payload, match.group(1).lower())
                except Exception as exc:  # noqa: BLE001 - never let the command reach the model
                    return _block(
                        SWITCH_ON_FAILED.format(error=type(exc).__name__, repo=_REPO_ROOT)
                        if match.group(1).lower() == "on"
                        else "Client masking: the command failed (%s); nothing was changed."
                        % type(exc).__name__
                    )
        base = routed_base(payload.get("cwd"))
        if base is None:
            return 0  # a chat that is not routed through the gateway: nothing to do
        return ROUTED_HANDLERS[event](payload, base)
    except Exception:  # noqa: BLE001 - a hook must never break the chat
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
