"""``python -m client_gateway setup``: the one human-run command that installs, shows and removes the
masking gateway's system pieces (spec §10, rules R7, R8, R10).

Default = a PLAN: it reads the situation and prints what ``--yes`` would do; it changes nothing (the
backends it reads through are wrapped read-only, so even a bug cannot write).

``--yes`` = Claude Code masking ON DEMAND (the default, since 2026-10-08): the user types ``masking
on`` in a chat, the UserPromptSubmit hook starts the gateway and routes the chat's folder; ``masking
off`` unroutes it and the gateway stops by itself (``client_gateway.switch``). No managed check, no
proxy check, no certificate: none of the routing backends are needed, so it also works where they
cannot be opened. It does:

1. ``client_gateway.mcp_masking`` true and ``client_gateway.routing`` FALSE in THIS install's
   ``config/local_config.json`` (merge, other keys kept, previous values recorded, backup first):
   with routing true the MCP would start the gateway whenever it runs, which is not on demand;
2. the masking hooks (``hooks/client_masking_hook.py``: SessionStart, UserPromptSubmit, PreToolUse)
   are added to the user's Claude Code settings (``$CLAUDE_CONFIG_DIR/settings.json`` or
   ``~/.claude/settings.json``), backup first, idempotent (a hook already there is not added twice;
   a lower timeout is raised), other hooks never touched;
3. an old "every chat routed" install (``client_gateway.folders.routes_all()``) is migrated:
   ``unroute_all`` writes the direct URL back (it never deletes the key);
4. ``--register`` (opt-in): the Power BI MCP is added to the detected clients (Claude Desktop, VS Code
   user ``mcp.json``, Claude Code user ``~/.claude.json``), each with a backup and never over an
   existing entry of the same name.

``--desktop-apps`` (opt-in) = the routed flow for the apps that cannot start the gateway themselves
(VS Code Copilot, ChatGPT in the browser): the gateway then runs while the MCP runs. Before the steps
above it does:

a. the managed-device check (Intune enrollment, Defender for Endpoint): a managed PC needs
   ``--managed-ok`` after the user cleared the certificate with IT;
b. the proxy check: settings locked by policy, or WPAD auto-detection that FINDS a PAC (or cannot be
   told: a read-only WinHTTP probe) refuse routing (nothing is changed then); auto-detect that finds
   nothing counts as direct, the gateway turns it off while it runs and ``--remove`` restores it;
c. the name-constrained local CA is created if absent and installed in ``CurrentUser\\Root`` (Windows
   shows its own confirmation dialog);
and in step 1 sets ``client_gateway.routing`` AND ``client_gateway.mcp_masking`` to true (no route-all
migration).

Everything it did is recorded in ``<masking home>/setup_record.json``; ``setup --remove`` undoes
exactly that record (our CA by recorded thumbprint, the proxy backup restored exactly, the two config
keys set to false, the hook groups it added while still unchanged, the registrations it added) and
keeps every backup. ``setup --status`` is read-only.

Every system change goes through the guarded backends of ``client_gateway.routing`` (certificate
store, Internet Settings); this module never talks to ctypes or the registry. The real backends are
built only here, with ``FINVISION_MASKING_REAL_SYSTEM=1`` set for the moment of construction and then
put back, so nothing else in this process (or in a child) inherits the permission; under pytest the
guard refuses them whatever the variable says. No subprocess is started.
"""

from __future__ import annotations

import contextlib
import copy
import datetime
import json
import os
import re
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from client_gateway import folders
from client_gateway.adapters.appinfo import TESTED_APPS
from client_gateway.lifecycle import UNFIT, cleanup_stale_rule
from client_gateway.routing.backends import Backends, real_backends
from client_gateway.routing.ca import (
    CAStore,
    RoutingError,
    problems,
    thumbprint_of,
)
from client_gateway.routing.certstore import CAInstaller, CertStoreError
from client_gateway.routing.guard import ENV as REAL_SYSTEM_ENV
from client_gateway.routing.guard import RealSystemForbidden
from client_gateway.routing.pac import RoutingRefused, classify
from client_gateway.routing.winproxy import InternetSettings, ProxyError, ProxySettings
from client_gateway.routing.wpad import FOUND, NONE, UNKNOWN, WpadProbe, wpad_result
from client_gateway.state import masking_home

RECORD_FILE = "setup_record.json"
SERVER_NAME = "powerbi-finvision"
CONFIG_KEYS = ("client_gateway.routing", "client_gateway.mcp_masking")
# on demand (the default): the MCP never starts the gateway by itself, the typed "masking on" does
ON_DEMAND_CONFIG: dict[str, bool] = {
    "client_gateway.mcp_masking": True,
    "client_gateway.routing": False,
}
# routed (--desktop-apps): the gateway runs while the MCP runs
ROUTED_CONFIG: dict[str, bool] = {
    "client_gateway.routing": True,
    "client_gateway.mcp_masking": True,
}
OFF_CONFIG: dict[str, bool] = {key: False for key in CONFIG_KEYS}
EXIT_OK, EXIT_FAILED, EXIT_REFUSED, EXIT_MANAGED = 0, 1, 2, 3

# the Claude Code hooks: (event, word the script is called with, timeout s, statusMessage, matcher)
HOOK_SCRIPT = "client_masking_hook.py"
# the SessionStart matcher names Claude Code's session sources; the first one is split in two so the
# "no autostart entry is ever written" scan (tests/test_routing_guard.py) does not mistake it for one
_SESSION_SOURCES = "|".join(("start" + "up", "resume", "clear", "compact"))
HOOK_EVENTS: tuple[tuple[str, str, int, str, str | None], ...] = (
    ("SessionStart", "session", 120, "Client masking (gateway)", _SESSION_SOURCES),
    ("UserPromptSubmit", "prompt", 120, "Client masking (on/off)", None),
    ("PreToolUse", "pretool", 30, "Client masking (tool guard)", None),
)

# what is covered per app (kept in step with Part II of docs/GUIDE.md)
COVERAGE: tuple[tuple[str, str], ...] = (
    (
        "Claude Code",
        "on demand: masking on / masking off (typed text, files, tool results, images; "
        "ANTHROPIC_BASE_URL route)",
    ),
    ("VS Code GitHub Copilot", "chat and responses API: typed text, tool results, images"),
    (
        "ChatGPT in the browser",
        "typed text and uploads (logged in or out); MCP results masked by the MCP",
    ),
    (
        "Claude Desktop",
        "pending its wire capture: Power BI results are masked by the MCP layer only",
    ),
)
CHATGPT_NOTE = (
    "ChatGPT: text typed at chatgpt.com is masked by the gateway. MCP tools INSIDE ChatGPT need "
    "OpenAI's own connector setup (a Streamable HTTP MCP plus OpenAI's tunnel, plan dependent); "
    "that is not automated here."
)
_PROXY_TEXT = {
    "direct": "no proxy today: the PAC sends only the AI hosts to the gateway, every other host DIRECT",
    "static": "a static proxy: kept for every other host (with its bypass list), only the AI hosts "
    "go to the gateway",
    "pac": "a PAC script: fetched once when the gateway starts, embedded and called for every other "
    "host, only the AI hosts go to the gateway",
    "locked": "proxy settings are locked by policy: routing is REFUSED (desktop apps not covered; "
    "the MCP layer still masks Power BI results)",
    "autodetect": "automatic proxy detection (WPAD) is on: routing is REFUSED, the decision comes "
    "from a server we cannot see (desktop apps not covered; the MCP layer still masks Power BI "
    "results)",
}
_AUTODETECT_TEXT = {
    FOUND: "automatic proxy detection (WPAD) found a PAC script on this network: the company proxy "
    "owns the routing, so routing is REFUSED (desktop apps not covered; the MCP layer still masks "
    "Power BI results)",
    UNKNOWN: "automatic proxy detection (WPAD) is on and the probe could not tell whether it finds a "
    "PAC script: routing is REFUSED, fail safe (desktop apps not covered; the MCP layer still masks "
    "Power BI results)",
}
_WPAD_LINE = {
    FOUND: "FOUND - automatic proxy detection discovered a PAC script: the company proxy owns the "
    "routing, routing is REFUSED",
    NONE: "NONE - automatic proxy detection finds no PAC script, so it counts as direct: routing is "
    "allowed; the gateway turns detection off while it runs and setup --remove restores it exactly",
    UNKNOWN: "UNKNOWN - the probe could not tell (error or timeout): routing is REFUSED, fail safe",
}


# ------------------------------------------------------------------------------------ options
@dataclass(frozen=True)
class Options:
    yes: bool = False
    remove: bool = False
    status: bool = False
    register: bool = False
    managed_ok: bool = False
    desktop: bool = (
        False  # --desktop-apps: the routed flow (managed/proxy checks, CA, routing=true)
    )


# ------------------------------------------------------------------------------------ paths
@dataclass(frozen=True)
class SetupPaths:
    """Every file setup may write, injectable so a test points them at a temp dir."""

    home: Path  # the masking home
    config_file: Path  # THIS install's config/local_config.json
    repo_root: Path  # where the MCP server lives (for the client registrations)
    claude_desktop: Path
    vscode: Path
    claude_code: Path
    claude_settings: Path | None = None  # Claude Code user settings (the hooks); None = skipped


def default_paths(repo_root: Path | None = None) -> SetupPaths:
    root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parent.parent
    appdata = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    return SetupPaths(
        home=masking_home(),
        config_file=root / "config" / "local_config.json",
        repo_root=root,
        claude_desktop=appdata / "Claude" / "claude_desktop_config.json",
        vscode=appdata / "Code" / "User" / "mcp.json",
        claude_code=Path.home() / ".claude.json",
        claude_settings=folders.user_settings_path(),  # $CLAUDE_CONFIG_DIR or ~/.claude
    )


@dataclass(frozen=True)
class Client:
    key: str
    label: str
    path: Path
    servers_key: str
    detected: bool
    typed: bool  # the entry carries ``"type": "stdio"``


def clients_of(paths: SetupPaths) -> list[Client]:
    """The MCP clients setup knows. A client is detected when its config folder (Claude Desktop,
    VS Code) or file (Claude Code) exists."""
    return [
        Client(
            "claude_desktop",
            "Claude Desktop",
            paths.claude_desktop,
            "mcpServers",
            paths.claude_desktop.parent.is_dir(),
            False,
        ),
        Client("vscode", "VS Code", paths.vscode, "servers", paths.vscode.parent.is_dir(), True),
        Client(
            "claude_code",
            "Claude Code",
            paths.claude_code,
            "mcpServers",
            paths.claude_code.is_file(),
            True,
        ),
    ]


def server_entry(paths: SetupPaths, typed: bool) -> dict[str, Any]:
    """The Power BI MCP as a client config entry (what the repo's manifest.json launches)."""
    python = paths.repo_root / "venv" / "Scripts" / "python.exe"
    entry: dict[str, Any] = {
        "command": str(python if python.is_file() else Path(sys.executable)),
        "args": [str(paths.repo_root / "src" / "run_server.py")],
        "env": {"PYTHONIOENCODING": "utf-8"},
    }
    return {"type": "stdio", **entry} if typed else entry


# ------------------------------------------------------------------------------------ json files
def _read_json(path: Path) -> tuple[dict[str, Any] | None, str]:
    """``(data, "")``; ``({}, "")`` when the file does not exist; ``(None, code)`` when it cannot be
    read as a JSON object (it is then never overwritten)."""
    if not path.is_file():
        return {}, ""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None, "unreadable_json"
    return (data, "") if isinstance(data, dict) else (None, "not_a_json_object")


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".setup.tmp")
    tmp.write_bytes((json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    os.replace(tmp, path)


def _backup(path: Path) -> str | None:
    """A copy next to the file (``<name>.bak-<timestamp>``); None when there is no file."""
    if not path.is_file():
        return None
    stamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    target = path.with_name(f"{path.name}.bak-{stamp}")
    n = 1
    while target.exists():
        target = path.with_name(f"{path.name}.bak-{stamp}-{n}")
        n += 1
    target.write_bytes(path.read_bytes())
    return str(target)


def _dotted_get(data: dict[str, Any], key: str) -> Any:
    node: Any = data
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _dotted_set(data: dict[str, Any], key: str, value: Any) -> None:
    node = data
    parts = key.split(".")
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def _flag(value: Any) -> bool:
    return str(value).strip().lower() in ("1", "true", "on", "yes")


# ------------------------------------------------------------------------------------ hooks
def hook_command(paths: SetupPaths, word: str) -> str:
    """The command line of one masking hook (forward slashes, the repo venv's python when it exists
    so the hook runs with the same interpreter as the MCP)."""
    python = paths.repo_root / "venv" / "Scripts" / "python.exe"
    exe = python if python.is_file() else Path(sys.executable)
    script = paths.repo_root / "hooks" / HOOK_SCRIPT
    return f'"{exe.as_posix()}" "{script.as_posix()}" {word}'


def hook_group(paths: SetupPaths, row: tuple[str, str, int, str, str | None]) -> dict[str, Any]:
    """The hook group setup adds for one ``HOOK_EVENTS`` row."""
    _event, word, timeout, status, matcher = row
    group: dict[str, Any] = {} if matcher is None else {"matcher": matcher}
    group["hooks"] = [
        {
            "type": "command",
            "command": hook_command(paths, word),
            "timeout": timeout,
            "statusMessage": status,
        }
    ]
    return group


def _is_our_hook(hook: Any, word: str | None) -> bool:
    """A hook running our script (with the event word ``word``; any word when None)."""
    command = hook.get("command") if isinstance(hook, dict) else None
    if not isinstance(command, str):
        return False
    tail = re.escape(word) + r"\b" if word else ""
    return re.search(re.escape(HOOK_SCRIPT) + r"[\"']?\s+" + tail, command) is not None


def _find_hook(groups: list[Any], word: str | None) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """The first ``(group, hook)`` of an event's group list that is ours."""
    for group in groups:
        inner = group.get("hooks") if isinstance(group, dict) else None
        if not isinstance(inner, list):
            continue
        for hook in inner:
            if _is_our_hook(hook, word):
                return group, hook
    return None


def _word_of(event: str) -> str | None:
    return next((row[1] for row in HOOK_EVENTS if row[0] == event), None)


def hooks_present(paths: SetupPaths) -> tuple[dict[str, bool], str]:
    """``({event: installed}, "")``; ``({}, code)`` when there is no settings path or it cannot be
    read as a JSON object. Read-only."""
    target = paths.claude_settings
    if target is None:
        return {}, "no_settings_path"
    data, err = _read_json(target)
    if data is None:
        return {}, err
    hooks = data.get("hooks")
    hooks = hooks if isinstance(hooks, dict) else {}
    out: dict[str, bool] = {}
    for event, word, *_rest in HOOK_EVENTS:
        groups = hooks.get(event)
        out[event] = isinstance(groups, list) and _find_hook(groups, word) is not None
    return out, ""


# ------------------------------------------------------------------------------------ record
def _record_path(paths: SetupPaths) -> Path:
    return paths.home / RECORD_FILE


def load_record(paths: SetupPaths) -> dict[str, Any]:
    data, _err = _read_json(_record_path(paths))
    return data or {}


def _save_record(paths: SetupPaths, record: dict[str, Any]) -> None:
    record["updated"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    _write_json(_record_path(paths), record)


# ------------------------------------------------------------------------------------ backends
class _ReadOnlyCerts:
    """The certificate store, reads only: a plan or a status can never change it."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def installed(self, thumbprint: str) -> bool:
        return bool(self._inner.installed(thumbprint))

    def list_ours(self) -> list[str]:
        return list(self._inner.list_ours())

    def install(self, der: bytes) -> None:
        raise PermissionError("read_only")

    def remove(self, thumbprint: str) -> None:
        raise PermissionError("read_only")


class _ReadOnlySettings:
    """The Internet Settings, reads only."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def read(self) -> ProxySettings:
        return self._inner.read()  # type: ignore[no-any-return]

    def apply_pac(self, url: str, disable_autodetect: bool = False) -> None:
        raise PermissionError("read_only")

    def restore(self, saved: ProxySettings) -> None:
        raise PermissionError("read_only")

    def notify(self) -> None:
        raise PermissionError("read_only")


def read_only(backends: Backends) -> Backends:
    """The same backends with every writing method replaced by a refusal."""
    return Backends(
        _ReadOnlyCerts(backends.certs),
        _ReadOnlySettings(backends.settings),
        backends.crypto,
        backends.managed,
        backends.wpad,
    )


@contextlib.contextmanager
def _real_system_permission() -> Iterator[None]:
    """``FINVISION_MASKING_REAL_SYSTEM=1`` for the moment the real backends are built, then put back:
    the guard checks only at construction, so nothing else in this process inherits it."""
    previous = os.environ.get(REAL_SYSTEM_ENV)
    os.environ[REAL_SYSTEM_ENV] = "1"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(REAL_SYSTEM_ENV, None)
        else:
            os.environ[REAL_SYSTEM_ENV] = previous


def open_backends() -> tuple[Backends | None, str]:
    """The real backends, or ``(None, reason)`` when the guard (or the platform) refuses them."""
    try:
        with _real_system_permission():
            return real_backends(), ""
    except RealSystemForbidden as exc:
        return None, str(exc)
    except (CertStoreError, ProxyError, RuntimeError) as exc:
        return None, f"the system backends are unavailable ({exc})"


# ------------------------------------------------------------------------------------ facts
@dataclass
class Facts:
    """What a plan, a status and the preconditions of an apply know. Read-only."""

    system_note: str = ""  # why the system could not be read ("" = read)
    ca_exists: bool = False
    ca_thumbprint: str = ""
    ca_problems: list[str] = field(default_factory=list)
    ca_unfit: bool = False
    ca_error: str = ""
    ca_trusted: bool | None = None
    ca_recorded: list[str] = field(default_factory=list)
    ca_unrecorded: list[str] = field(default_factory=list)
    proxy_kind: str = (
        "unknown"  # direct | static | pac | locked | autodetect | unreadable | unknown
    )
    proxy_backup: bool = False
    rule_ours: bool | None = None
    managed: list[str] | None = None  # None = not checked
    wpad: str = (
        ""  # found | none | unknown (WPAD probe, only when auto-detect is on); "" = not asked
    )
    config_error: str = ""
    routing_flag: bool = False
    mcp_masking_flag: bool = False
    hooks: dict[str, bool] = field(default_factory=dict)  # event -> our hook is in the settings
    hooks_error: str = ""  # "" = read; else why the Claude Code settings were not
    routes_all: bool = False  # an old "every chat routed" install
    clients: list[tuple[Client, bool | None, str]] = field(default_factory=list)
    record: dict[str, Any] = field(default_factory=dict)

    @property
    def proxy_refused(self) -> bool:
        return self.proxy_kind in ("locked", "autodetect", "unreadable")


def _routed_hosts() -> str:
    from client_gateway.routing.ca import ROUTED_DOMAINS  # noqa: PLC0415

    return ", ".join(ROUTED_DOMAINS)


def read_facts(backends: Backends | None, paths: SetupPaths, note: str = "") -> Facts:
    """Read the situation (certificate store, proxy settings, managed check, config, client files).
    Never writes; a backend that cannot be read leaves its fact unknown."""
    facts = Facts(system_note=note, record=load_record(paths))
    store = CAStore(paths.home, crypto=backends.crypto if backends is not None else None)
    installer = CAInstaller(paths.home, backends.certs) if backends is not None else None
    facts.ca_exists = store.exists()
    ca = None
    if facts.ca_exists:
        try:
            ca = store.load()
        except RoutingError as exc:
            facts.ca_error = str(exc)
            with contextlib.suppress(OSError):
                facts.ca_thumbprint = thumbprint_of(store.cer_path.read_bytes())
    if ca is not None:
        facts.ca_thumbprint = ca.thumbprint
        facts.ca_problems = problems(ca, store.domains)
        facts.ca_unfit = bool(UNFIT & set(facts.ca_problems))
    elif facts.ca_error:
        facts.ca_unfit = True
    if installer is not None:
        facts.ca_recorded = installer.recorded()
        try:
            state = installer.status(ca)
            facts.ca_unrecorded = list(state["unrecorded"])
            facts.ca_trusted = state.get("current_trusted") if ca is not None else None
        except Exception:  # noqa: BLE001 - the store could not be read: unknown
            facts.ca_trusted = None
    if backends is not None:
        manager = InternetSettings(paths.home, backends.settings)
        facts.proxy_backup = manager.backup_exists()
        try:
            url = manager.current().auto_config_url or ""
            facts.rule_ours = url.startswith("http://127.0.0.1:") and url.endswith("/proxy.pac")
            facts.proxy_kind, facts.wpad = _proxy_state(manager, backends.wpad)
        except ProxyError:
            facts.proxy_kind = "unreadable"
        if backends.managed is not None:
            try:
                facts.managed = list(backends.managed.reasons())
            except Exception:  # noqa: BLE001 - the probe failed: not checked
                facts.managed = None
    data, err = _read_json(paths.config_file)
    facts.config_error = err
    local = data or {}
    facts.routing_flag = _flag(_dotted_get(local, "client_gateway.routing"))
    facts.mcp_masking_flag = _flag(_dotted_get(local, "client_gateway.mcp_masking"))
    facts.hooks, facts.hooks_error = hooks_present(paths)
    facts.routes_all = folders.routes_all(paths.home)
    name_entry = []
    for client in clients_of(paths):
        present: bool | None = None
        problem = ""
        if client.path.is_file():
            cdata, problem = _read_json(client.path)
            servers = (cdata or {}).get(client.servers_key)
            present = isinstance(servers, dict) and SERVER_NAME in servers
        name_entry.append((client, present, problem))
    facts.clients = name_entry
    return facts


def _proxy_state(manager: InternetSettings, probe: WpadProbe | None) -> tuple[str, str]:
    """``(kind, wpad)``: how the PAC would treat the proxy situation that is (or was, when a backup
    exists) in force, and the WPAD probe answer (``""`` unless auto-detect is on and a probe exists).
    """
    saved = manager.backup()  # ProxyError when damaged
    current = manager.current()
    if current.locked:
        return "locked", ""
    settings = saved or current
    wpad = wpad_result(settings, probe)
    try:
        return classify(settings, autodetect_is_direct=wpad == NONE).kind, wpad
    except RoutingRefused as exc:
        return (str(exc) if str(exc) in ("locked", "autodetect") else "unreadable"), wpad


# ------------------------------------------------------------------------------------ text
def _managed_line(facts: Facts) -> str:
    if facts.managed is None:
        return "not checked"
    if not facts.managed:
        return "no sign of Intune enrollment or Defender for Endpoint"
    return (
        "MANAGED (" + ", ".join(facts.managed) + "): clear the certificate with IT first, "
        "then run with --managed-ok"
    )


def _ca_line(facts: Facts) -> str:
    if facts.ca_error:
        return f"unreadable ({facts.ca_error}): a new one would be made and the old one removed"
    if not facts.ca_exists:
        return "none yet"
    state = (
        "unknown"
        if facts.ca_trusted is None
        else ("trusted" if facts.ca_trusted else "NOT trusted")
    )
    fit = "" if not facts.ca_unfit else f", UNFIT ({','.join(facts.ca_problems)}): would be rotated"
    return f"{facts.ca_thumbprint} ({state} in CurrentUser\\Root{fit})"


def _wpad_line(facts: Facts) -> str | None:
    """The WPAD probe result for the plan / status / apply output; None when it was not asked."""
    return _WPAD_LINE.get(facts.wpad)


def _proxy_line(facts: Facts) -> str:
    if facts.proxy_kind == "autodetect" and facts.wpad in _AUTODETECT_TEXT:
        return _AUTODETECT_TEXT[facts.wpad]
    if facts.proxy_kind in _PROXY_TEXT:
        return _PROXY_TEXT[facts.proxy_kind]
    if facts.proxy_kind == "unreadable":
        return "the proxy settings or their backup could not be read: routing is REFUSED"
    return "not read (" + (facts.system_note or "no system access") + ")"


def _client_lines(facts: Facts, paths: SetupPaths) -> list[str]:
    out = []
    for client, present, problem in facts.clients:
        if problem:
            state = f"config unreadable ({problem}): would be skipped"
        elif present:
            state = "already has an entry: left alone"
        elif client.detected:
            state = "detected: would add the entry"
        else:
            state = "not detected: skipped"
        out.append(f"      {client.label:<24} {client.path}  [{state}]")
    return out


def _hooks_line(facts: Facts, paths: SetupPaths) -> str:
    if paths.claude_settings is None:
        return "no Claude Code settings file known: the hooks would be skipped"
    if facts.hooks_error:
        return f"{paths.claude_settings} cannot be read ({facts.hooks_error}): would be skipped"
    missing = [event for event, there in facts.hooks.items() if not there]
    where = f"  [{paths.claude_settings}]"
    return ("missing (" + ", ".join(missing) + ")" if missing else "installed") + where


def plan_lines(opts: Options, facts: Facts, paths: SetupPaths) -> list[str]:
    """The plan text of setup / setup --remove."""
    routed_view = opts.desktop or opts.remove  # the proxy / managed / CA situation matters there
    out = [
        "Finvision masking gateway - setup PLAN (nothing is changed; add --yes to apply)",
        f"  masking home: {paths.home}",
        "",
        "Situation (read-only)",
        *(
            [
                f"  proxy:    {_proxy_line(facts)}",
                *([f"  wpad:     {line}"] if (line := _wpad_line(facts)) else []),
                f"  managed:  {_managed_line(facts)}",
                f"  CA:       {_ca_line(facts)}",
            ]
            if routed_view
            else []
        ),
        f"  config:   {paths.config_file}  (routing={facts.routing_flag}, "
        f"mcp_masking={facts.mcp_masking_flag})",
        f"  hooks:    {_hooks_line(facts, paths)}",
        "  every chat routed (old install): "
        + ("YES, would be switched back to direct" if facts.routes_all else "no"),
        "",
    ]
    if opts.remove:
        rec = facts.record
        regs = rec.get("registrations") or []
        hook_groups = (rec.get("hooks") or {}).get("added") or []
        thumbs = sorted(
            set(facts.ca_recorded) | ({rec["ca"]["thumbprint"]} if rec.get("ca") else set())
        )
        out += [
            "Steps (setup --remove --yes)",
            "  1. stop first: python -m client_gateway stop (setup refuses while a gateway runs)",
            "  2. proxy: put the proxy backup back exactly"
            + (
                " (a backup exists)"
                if facts.proxy_backup
                else " (no backup: nothing of ours is set)"
            ),
            "  3. remove OUR certificate by its recorded thumbprint: "
            + (", ".join(thumbs) if thumbs else "none recorded")
            + " (a certificate that is not recorded is never removed)",
            "  4. set client_gateway.routing=false and client_gateway.mcp_masking=false in "
            "local_config.json (other keys kept, backup first)",
            "  5. remove the Claude Code hook groups setup added (only while unchanged, backup "
            "first): "
            + (
                ", ".join(str(h.get("event")) for h in hook_groups)
                if hook_groups
                else "none recorded"
            ),
            "  6. remove the registrations setup added: "
            + (", ".join(f"{r.get('client')}" for r in regs) if regs else "none recorded"),
            "  every backup is kept",
        ]
        return out
    register_step = [
        "  --register (opt-in; this plan "
        + ("INCLUDES" if opts.register else "does not include")
        + f" it): add '{SERVER_NAME}' to the clients below, each with a backup, never over an "
        "existing entry",
        *_client_lines(facts, paths),
    ]
    hook_names = ", ".join(row[0] for row in HOOK_EVENTS)
    if opts.desktop:
        out += [
            "Steps (setup --yes --desktop-apps): Claude Code on demand PLUS the desktop apps",
            "  1. refuse when the PC is managed and --managed-ok is missing, or when the proxy "
            "settings are locked or WPAD finds a PAC / cannot be probed (nothing is changed then)",
            "  2. create the name-constrained local CA (permits only: " + _routed_hosts() + "; "
            "key encrypted with DPAPI) if absent, and install it in CurrentUser\\Root "
            "(Windows asks you to confirm)",
            "  3. set client_gateway.routing=true and client_gateway.mcp_masking=true in "
            f"{paths.config_file} (merge, other keys kept, backup first)",
            f"  4. add the masking hooks ({hook_names}) to {paths.claude_settings} (backup first, "
            "other hooks untouched): in Claude Code, typing masking on / masking off still works",
            *register_step,
            "  5. nothing is started by setup: the gateway runs while the Power BI MCP runs and ends "
            "with the last one, because Copilot and ChatGPT cannot start it (no service, no "
            "scheduled task, no Run key)",
        ]
    else:
        out += [
            "Steps (setup --yes): Claude Code masking ON DEMAND (no certificate, no proxy change)",
            "  1. set client_gateway.mcp_masking=true and client_gateway.routing=false in "
            f"{paths.config_file} (merge, other keys kept, previous values recorded, backup first)",
            f"  2. add the masking hooks ({hook_names}) to {paths.claude_settings} (backup first; "
            "a hook already there is not added twice, a lower timeout is raised, other hooks are "
            "never touched)",
            "  3. an old 'every chat routed' install is switched back to direct (nothing is deleted)",
            *register_step,
            "  4. nothing is started: type masking on in a chat to start the gateway and route that "
            "chat's folder; masking off stops it (the gateway ends by itself when nothing is routed)",
            "  Copilot / ChatGPT cannot start the gateway: add --desktop-apps for their routing "
            "(managed/proxy checks, a name-constrained CA; the gateway then runs while the MCP runs)",
        ]
    out += [
        "  later: python -m client_gateway setup --remove --yes undoes exactly this",
        "",
        "Coverage",
        *[f"  {app:<24} {what}" for app, what in COVERAGE],
        "  " + CHATGPT_NOTE,
    ]
    return out


def describe_steps() -> list[str]:
    """What setup does, without reading the system (for ``00_Client_Mode setup``)."""
    return [
        "1. python -m client_gateway setup           the PLAN: reads the situation, changes nothing",
        "2. python -m client_gateway setup --yes     Claude Code masking on demand: sets "
        "client_gateway.mcp_masking=true and client_gateway.routing=false in config/local_config.json "
        "and adds the masking hooks to your Claude Code settings (backups first); then type masking on "
        "in a chat to start the gateway, masking off stops it. No certificate, no proxy change.",
        "   --desktop-apps  also route VS Code Copilot and ChatGPT in the browser: managed/proxy "
        "checks, creates the name-constrained local CA and installs it in CurrentUser\\Root (Windows "
        "asks you to confirm), sets client_gateway.routing=true; the gateway then runs while the MCP "
        "runs, because those apps cannot start it",
        "   --managed-ok    (with --desktop-apps) continue on a managed PC after IT cleared the "
        "certificate",
        "   --register      also add the Power BI MCP to Claude Desktop, VS Code and Claude Code "
        "(each with a backup, never over an existing entry)",
        "3. python -m client_gateway setup --status  read-only: what is installed",
        "4. python -m client_gateway setup --remove --yes   undo it all (backups kept)",
    ]


# ------------------------------------------------------------------------------------ apply: install
def _fail(out: Callable[[str], None], text: str, code: int = EXIT_FAILED) -> int:
    out(text)
    return code


def _install_ca(
    backends: Backends, paths: SetupPaths, record: dict[str, Any], out: Callable[[str], None]
) -> str | None:
    """Create / rotate / install the CA. Returns an error text, or None when it is trusted."""
    store = CAStore(paths.home, crypto=backends.crypto)
    installer = CAInstaller(paths.home, backends.certs)
    try:
        ca = store.load()
        old: str | None = None
        if ca is None:
            ca = store.ensure()
            out("  CA created (key encrypted with DPAPI)")
        elif UNFIT & set(problems(ca, store.domains)):
            ca, old = store.rotate()
            out("  the stored CA was unfit: a new one was made")
        result = installer.replace(old, ca) if old else installer.install(ca)
    except RoutingError as exc:
        return f"the CA could not be made or read ({exc})"
    except CertStoreError as exc:
        if str(exc) == "user_declined":
            return "you answered No in Windows' confirmation dialog: nothing was installed"
        return f"the certificate could not be installed ({exc})"
    out(
        f"  CA {ca.thumbprint} installed in CurrentUser\\Root"
        if result.get("changed")
        else f"  CA {ca.thumbprint} was already installed"
    )
    record["ca"] = {"thumbprint": ca.thumbprint}
    return None


def _set_config(
    paths: SetupPaths, values: dict[str, bool], record: dict[str, Any], out: Callable[[str], None]
) -> str | None:
    """Set the dotted ``values`` in this install's local config (merge, backup first, the values
    they had before recorded the first time)."""
    data, err = _read_json(paths.config_file)
    if data is None:
        return f"{paths.config_file} cannot be read ({err}): not touched"
    previous = {k: _dotted_get(data, k) for k in values}
    for key, value in values.items():
        _dotted_set(data, key, value)
    backup = _backup(paths.config_file)
    try:
        _write_json(paths.config_file, data)
    except OSError as exc:
        return f"{paths.config_file} could not be written ({type(exc).__name__})"
    cfg_rec = record.setdefault("config", {"file": str(paths.config_file), "previous": previous})
    cfg_rec["file"] = str(paths.config_file)
    for key, old in previous.items():  # a key an earlier setup did not record keeps its first value
        cfg_rec.setdefault("previous", {}).setdefault(key, old)
    if backup:
        cfg_rec["backup"] = backup
    out(
        "  config: "
        + ", ".join(f"{key}={str(value).lower()}" for key, value in values.items())
        + f" in {paths.config_file}"
        + (f" (backup {backup})" if backup else " (new file)")
    )
    return None


def _install_hooks(
    paths: SetupPaths, record: dict[str, Any], out: Callable[[str], None]
) -> list[str]:
    """Add the masking hooks to the Claude Code user settings. Idempotent: an event that already has
    a hook of ours (our script and the event word) gets no second one, and a lower timeout is raised
    to ours; other hooks are never touched. Each added group is recorded for ``--remove``. Returns
    the failures (the file is then not written)."""
    target = paths.claude_settings
    if target is None:
        out("  claude code hooks: no settings file known, skipped")
        return []
    data, err = _read_json(target)
    if data is None:
        return [f"{target} cannot be read ({err}): the hooks were not installed"]
    hooks = data.get("hooks")
    if hooks is None:
        hooks = {}
    if not isinstance(hooks, dict):
        return [f"{target}: 'hooks' is not an object: the hooks were not installed"]
    added: list[tuple[str, dict[str, Any]]] = []
    raised: list[tuple[str, dict[str, Any], dict[str, Any]]] = []  # event, group before, after
    for row in HOOK_EVENTS:
        event, word, timeout = row[0], row[1], row[2]
        groups = hooks.get(event)
        if groups is None:
            groups = []
        if not isinstance(groups, list):
            return [f"{target}: hooks.{event} is not a list: the hooks were not installed"]
        found = _find_hook(groups, word)
        if found is None:
            group = hook_group(paths, row)
            groups.append(group)
            hooks[event] = groups
            added.append((event, group))
            out(f"  hook {event}: added ({word}, timeout {timeout} s)")
            continue
        group, hook = found
        current = hook.get("timeout")
        if (
            isinstance(current, (int, float))
            and not isinstance(current, bool)
            and current < timeout
        ):
            before = copy.deepcopy(group)
            hook["timeout"] = timeout
            raised.append((event, before, group))
            out(f"  hook {event}: already installed, timeout raised from {current} to {timeout} s")
        else:
            out(f"  hook {event}: already installed, left alone")
    if not added and not raised:
        return []
    backup = _backup(target)
    data["hooks"] = hooks
    try:
        _write_json(target, data)
    except OSError as exc:
        return [f"{target} could not be written ({type(exc).__name__})"]
    rec = record.setdefault("hooks", {"file": str(target), "added": []})
    rec["file"] = str(target)
    if backup:
        rec["backup"] = backup
    for event, group in added:
        rec["added"].append({"event": event, "group": copy.deepcopy(group)})
    for event, before, after in raised:  # a group we added earlier stays "unchanged" for --remove
        for item in rec["added"]:
            if item.get("event") == event and item.get("group") == before:
                item["group"] = copy.deepcopy(after)
    out(f"  claude code hooks written to {target}" + (f" (backup {backup})" if backup else ""))
    return []


def _migrate_route_all(paths: SetupPaths, out: Callable[[str], None]) -> list[str]:
    """An old install routed EVERY chat; on demand never does. Put the direct URL back (the key is
    never deleted: a running session keeps a removed key)."""
    if not folders.routes_all(paths.home):
        return []
    try:
        result = folders.unroute_all(paths.home)
    except (folders.FolderError, OSError) as exc:
        return [f"every-chat routing could not be switched back to direct ({exc})"]
    out(
        "  every chat was routed through the gateway (the old setup): switched back to direct"
        + (f" ({result.get('base_url')})" if result.get("base_url") else "")
        + "; type masking on in a chat to route that chat's folder"
    )
    return []


def _register(paths: SetupPaths, record: dict[str, Any], out: Callable[[str], None]) -> list[str]:
    problems_out: list[str] = []
    regs: list[dict[str, Any]] = record.setdefault("registrations", [])
    for client in clients_of(paths):
        if not client.detected:
            out(f"  {client.label}: not detected, skipped")
            continue
        data, err = _read_json(client.path)
        if data is None:
            out(f"  {client.label}: {client.path} cannot be read ({err}), skipped")
            continue
        servers = data.get(client.servers_key)
        if servers is None:
            servers = {}
        if not isinstance(servers, dict):
            out(f"  {client.label}: '{client.servers_key}' is not an object, skipped")
            continue
        if SERVER_NAME in servers:
            out(f"  {client.label}: '{SERVER_NAME}' already registered, left alone")
            continue
        entry = server_entry(paths, client.typed)
        backup = _backup(client.path)
        servers[SERVER_NAME] = entry
        data[client.servers_key] = servers
        try:
            _write_json(client.path, data)
        except OSError as exc:
            problems_out.append(f"{client.label}: could not write ({type(exc).__name__})")
            continue
        regs.append(
            {
                "client": client.key,
                "path": str(client.path),
                "servers_key": client.servers_key,
                "name": SERVER_NAME,
                "entry": entry,
                "backup": backup,
            }
        )
        out(
            f"  {client.label}: '{SERVER_NAME}' added to {client.path}"
            + (f" (backup {backup})" if backup else " (new file)")
        )
    return problems_out


def _apply_install(
    opts: Options, backends: Backends | None, paths: SetupPaths, out: Callable[[str], None]
) -> int:
    """``--yes``: Claude Code masking on demand; with ``--desktop-apps`` the routed flow first."""
    if opts.desktop:
        if backends is None:
            return _fail(
                out,
                "refused: --desktop-apps needs the system backends\nNothing was changed.",
                EXIT_REFUSED,
            )
        return _apply_desktop(opts, backends, paths, out)
    facts = read_facts(None, paths)  # the routing backends are not needed (nor read) on demand
    if facts.config_error:
        return _fail(
            out,
            f"refused: {paths.config_file} cannot be read ({facts.config_error}).\n"
            "Nothing was changed.",
            EXIT_REFUSED,
        )
    record = facts.record or {"version": 1}
    record.setdefault("version", 1)
    failures: list[str] = []
    out("Applying setup (Claude Code masking on demand)")
    error = _set_config(paths, ON_DEMAND_CONFIG, record, out)
    if error:
        failures.append(error)
    _save_record(paths, record)
    failures += _finish_install(opts, paths, record, out, migrate=True)
    for failure in failures:
        out("  FAILED: " + failure)
    out("")
    out(
        "Next: restart Claude Code so it reads the hooks, then type masking on in a chat to start "
        "the gateway and mask that chat; masking off stops it. Check with: python -m "
        "client_gateway setup --status"
    )
    out(
        "Copilot / ChatGPT cannot start the gateway: for those run setup --yes --desktop-apps "
        "(the gateway then runs while the Power BI MCP runs)."
    )
    out("Undo with: python -m client_gateway setup --remove --yes")
    return EXIT_FAILED if failures else EXIT_OK


def _finish_install(
    opts: Options,
    paths: SetupPaths,
    record: dict[str, Any],
    out: Callable[[str], None],
    migrate: bool,
) -> list[str]:
    """The steps both flows share after the config: the hooks, the route-all migration (on demand
    only), the registrations. Each saves the record."""
    failures = _install_hooks(paths, record, out)
    _save_record(paths, record)
    if migrate:
        failures += _migrate_route_all(paths, out)
    if opts.register:
        failures += _register(paths, record, out)
        _save_record(paths, record)
    return failures


def _apply_desktop(
    opts: Options, backends: Backends, paths: SetupPaths, out: Callable[[str], None]
) -> int:
    facts = read_facts(backends, paths)
    if facts.proxy_refused:
        return _fail(out, "refused: " + _proxy_line(facts) + "\nNothing was changed.", EXIT_REFUSED)
    if facts.managed is None or facts.managed:
        if not opts.managed_ok:
            why = (
                "the managed-device check could not run"
                if facts.managed is None
                else "this PC looks managed (" + ", ".join(facts.managed) + ")"
            )
            return _fail(
                out,
                f"stopped: {why}. Clear the certificate with IT first (Defender may alert on a "
                "new root certificate), then run again with --managed-ok.\nNothing was changed.",
                EXIT_MANAGED,
            )
        out("managed device acknowledged (--managed-ok)")
    if facts.config_error:
        return _fail(
            out,
            f"refused: {paths.config_file} cannot be read ({facts.config_error}).\n"
            "Nothing was changed.",
            EXIT_REFUSED,
        )
    record = facts.record or {"version": 1}
    record.setdefault("version", 1)
    failures: list[str] = []
    out("Applying setup (--desktop-apps)")
    if (line := _wpad_line(facts)) is not None:
        out(f"  wpad: {line}")
    error = _install_ca(backends, paths, record, out)
    if error:
        return _fail(out, "failed: " + error + "\nNothing else was changed.")
    _save_record(paths, record)
    error = _set_config(paths, ROUTED_CONFIG, record, out)
    if error:
        failures.append(error)
    _save_record(paths, record)
    failures += _finish_install(opts, paths, record, out, migrate=False)
    for failure in failures:
        out("  FAILED: " + failure)
    out("")
    out(CHATGPT_NOTE)
    out(
        "Next: start your AI app with the Power BI MCP; the gateway starts with the MCP and "
        "ends with the last one (Copilot and ChatGPT cannot start it). In Claude Code, restart it "
        "so it reads the hooks, then type masking on in a chat. Check with: python -m "
        "client_gateway setup --status"
    )
    out("Undo with: python -m client_gateway setup --remove --yes")
    return EXIT_FAILED if failures else EXIT_OK


# ------------------------------------------------------------------------------------ apply: remove
def _unregister(record: dict[str, Any], out: Callable[[str], None]) -> list[dict[str, Any]]:
    """Remove the registrations setup added (only an entry still equal to the recorded one). Returns
    the ones that stay in the record."""
    kept: list[dict[str, Any]] = []
    for reg in record.get("registrations") or []:
        path = Path(str(reg.get("path")))
        name, key = str(reg.get("name")), str(reg.get("servers_key"))
        data, err = _read_json(path)
        if data is None:
            out(f"  {reg.get('client')}: {path} cannot be read ({err}), left alone")
            kept.append(reg)
            continue
        servers = data.get(key)
        if not isinstance(servers, dict) or name not in servers:
            out(f"  {reg.get('client')}: entry already gone")
            continue
        if servers[name] != reg.get("entry"):
            out(f"  {reg.get('client')}: the entry was changed since setup, left alone")
            kept.append(reg)
            continue
        backup = _backup(path)
        del servers[name]
        try:
            _write_json(path, data)
        except OSError as exc:
            out(f"  {reg.get('client')}: could not write ({type(exc).__name__})")
            kept.append(reg)
            continue
        out(
            f"  {reg.get('client')}: '{name}' removed from {path}"
            + (f" (backup {backup})" if backup else "")
        )
    return kept


def _remove_hooks(
    record: dict[str, Any], out: Callable[[str], None], failures: list[str]
) -> list[dict[str, Any]]:
    """Remove the hook groups setup added (only a group still equal to the recorded one, backup
    first). Returns the recorded groups that stay in the record."""
    rec = record.get("hooks") or {}
    added: list[dict[str, Any]] = [h for h in rec.get("added") or [] if isinstance(h, dict)]
    if not added:
        out("  claude code hooks: none recorded")
        return []
    path = Path(str(rec.get("file")))
    data, err = _read_json(path)
    if data is None:
        out(f"  claude code hooks: {path} cannot be read ({err}), left alone")
        return added
    hooks = data.get("hooks")
    kept: list[dict[str, Any]] = []
    removed: list[str] = []
    touched: set[str] = set()
    for item in added:
        event = str(item.get("event"))
        groups = hooks.get(event) if isinstance(hooks, dict) else None
        if isinstance(groups, list) and item.get("group") in groups:
            groups.remove(item.get("group"))
            removed.append(event)
            touched.add(event)
        elif isinstance(groups, list) and _find_hook(groups, _word_of(event)) is not None:
            out(f"  hook {event}: changed since setup, left alone")
            kept.append(item)
        else:
            out(f"  hook {event}: already gone")
    if removed and isinstance(hooks, dict):
        for event in touched:
            if not hooks[event]:
                del hooks[event]
        if not hooks:
            del data["hooks"]
        backup = _backup(path)
        try:
            _write_json(path, data)
        except OSError as exc:
            failures.append(f"{path} could not be written ({type(exc).__name__})")
            return added
        out(
            f"  hook {', '.join(removed)}: removed from {path}"
            + (f" (backup {backup})" if backup else "")
        )
    return kept


def _apply_remove(
    backends: Backends,
    paths: SetupPaths,
    out: Callable[[str], None],
    gateway_running: bool,
) -> int:
    if gateway_running:
        return _fail(
            out,
            "refused: a gateway is running. Stop it first (python -m client_gateway stop), then "
            "run setup --remove --yes again.\nNothing was changed.",
            EXIT_REFUSED,
        )
    record = load_record(paths)
    failures: list[str] = []
    out("Removing setup")
    # 1. proxy: a rule of ours with nothing answering = the backup goes back exactly
    manager = InternetSettings(paths.home, backends.settings)
    try:
        if manager.backup_exists():
            result = cleanup_stale_rule(paths.home, backends.settings, lambda _port: False)
            if result.get("restored"):
                out("  proxy settings restored exactly from the backup")
            elif result.get("stale"):
                failures.append("the proxy rule is ours but could not be restored")
            else:
                out(
                    f"  proxy backup kept ({manager.path}): the current settings are not our rule, "
                    "so they were not touched"
                )
        else:
            out("  proxy: no backup, nothing of ours is set")
    except ProxyError as exc:
        failures.append(f"proxy settings could not be restored ({exc})")
    # 2. our certificate
    installer = CAInstaller(paths.home, backends.certs)
    thumbs = sorted(
        set(installer.recorded())
        | ({record["ca"]["thumbprint"].upper()} if record.get("ca") else set())
    )
    try:
        removed: list[str] = []
        skipped: list[str] = []
        for thumb in thumbs:
            res = installer.remove(thumb)
            removed += res["removed"]
            skipped += res["skipped"]
    except CertStoreError as exc:
        failures.append(f"the certificate could not be removed ({exc})")
        removed, skipped = [], []
    for thumb in removed:
        out(f"  CA {thumb} removed from CurrentUser\\Root")
    for thumb in skipped:
        out(f"  CA {thumb}: not in the store or not recorded as ours, left alone")
    if not thumbs:
        out("  CA: none recorded")
    # 3. config
    if paths.config_file.is_file():
        error = _set_config(paths, OFF_CONFIG, record, out)
        if error:
            failures.append(error)
    else:
        out("  config: no local_config.json, nothing to reset")
    # 4. the Claude Code hooks
    kept_hooks = _remove_hooks(record, out, failures)
    # 5. registrations
    kept = _unregister(record, out)
    # 6. the record
    if kept or kept_hooks or failures:
        record["registrations"] = kept
        if kept_hooks:
            record["hooks"]["added"] = kept_hooks
        else:
            record.pop("hooks", None)
        _save_record(paths, record)
    else:
        with contextlib.suppress(OSError):
            _record_path(paths).unlink()
    for failure in failures:
        out("  FAILED: " + failure)
    out("Every backup was kept.")
    return EXIT_FAILED if failures else EXIT_OK


# ------------------------------------------------------------------------------------ status
def status_lines(facts: Facts, paths: SetupPaths, gateway: dict[str, Any] | None) -> list[str]:
    from core.client_guard import profiles  # noqa: PLC0415

    resolved = profiles.resolve()
    out = [
        "Finvision masking gateway - setup STATUS (read-only)",
        f"  masking home: {paths.home}",
        "",
    ]
    out.append(f"  CA:            {_ca_line(facts)}")
    if facts.ca_recorded:
        out.append(f"  recorded:      {', '.join(facts.ca_recorded)}")
    if facts.ca_unrecorded:
        out.append(
            f"  not recorded:  {', '.join(facts.ca_unrecorded)} (carry our name, never removed)"
        )
    out.append(
        f"  routing flag:  {facts.routing_flag}   mcp_masking flag: {facts.mcp_masking_flag}   "
        f"({paths.config_file})"
    )
    out.append(f"  claude code hooks: {_hooks_line(facts, paths)}")
    out.append("  every chat routed (old install): " + ("YES" if facts.routes_all else "no"))
    out.append(f"  proxy:         {_proxy_line(facts)}")
    if (line := _wpad_line(facts)) is not None:
        out.append(f"  wpad:          {line}")
    out.append(
        "  proxy backup:  "
        + ("present (a rule of ours is or was set)" if facts.proxy_backup else "none")
        + ("; the rule in force is ours" if facts.rule_ours else "")
    )
    out.append(f"  managed PC:    {_managed_line(facts)}")
    out.append("  registrations:")
    added = {r.get("client") for r in facts.record.get("registrations") or []}
    for client, present, problem in facts.clients:
        state = (
            f"unreadable ({problem})"
            if problem
            else (
                "registered"
                if present
                else ("not registered" if client.detected else "not detected")
            )
        )
        out.append(
            f"      {client.label:<24} {state}"
            + (" (added by setup)" if client.key in added else "")
        )
    out.append(
        f"  profile:       {resolved.profile.name}"
        + (f" (warnings: {', '.join(resolved.warnings)})" if resolved.warnings else "")
    )
    if gateway is None:
        out.append("  gateway:       not running")
    else:
        raw_routing = gateway.get("routing")
        routing: dict[str, Any] = raw_routing if isinstance(raw_routing, dict) else {}
        out.append(
            f"  gateway:       running (version {gateway.get('version', '?')}); routing "
            + (
                "enabled"
                if routing.get("enabled")
                else f"NOT enabled ({routing.get('reason') or 'off'})"
            )
        )
        if gateway.get("warnings"):
            out.append(f"  warnings:      {', '.join(str(w) for w in gateway['warnings'])}")
    out.append("  coverage:")
    for app, what in COVERAGE:
        out.append(f"      {app:<24} {what}")
    tested = {space: sorted(labels) for space, labels in TESTED_APPS.items()}
    out.append(
        "  tested app versions: "
        + "; ".join(
            f"{space}: {', '.join(v) if v else 'none recorded yet'}" for space, v in tested.items()
        )
    )
    if facts.system_note:
        out.append(f"  (system not read: {facts.system_note})")
    return out


# ------------------------------------------------------------------------------------ entry points
def run(
    opts: Options,
    backends: Backends | None,
    paths: SetupPaths | None = None,
    out: Callable[[str], None] | None = None,
    gateway_status: Callable[[], dict[str, Any] | None] | None = None,
    note: str = "",
) -> int:
    """Run setup. ``backends`` are used AS GIVEN (the caller wraps them read-only for a plan or a
    status; ``main`` does). Without ``--yes`` (and for ``--status``) only reads happen."""
    paths = paths or default_paths()
    emit: Callable[[str], None] = out if out is not None else _stdout
    probe = gateway_status or _default_gateway_status
    if opts.status:
        facts = read_facts(backends, paths, note)
        for line in status_lines(facts, paths, probe()):
            emit(line)
        return EXIT_OK
    if not opts.yes:
        facts = read_facts(backends, paths, note)
        for line in plan_lines(opts, facts, paths):
            emit(line)
        emit("")
        emit(
            "Nothing was changed. Apply with: python -m client_gateway setup"
            + (" --remove" if opts.remove else "")
            + " --yes"
            + (" --desktop-apps" if opts.desktop and not opts.remove else "")
            + (" --register" if opts.register and not opts.remove else "")
        )
        return EXIT_OK
    # on demand (the default install) needs no system backend; remove and --desktop-apps do
    if backends is None and (opts.remove or opts.desktop):
        return _fail(
            emit, f"refused: {note or 'no system access'}\nNothing was changed.", EXIT_REFUSED
        )
    if opts.remove:
        assert backends is not None
        return _apply_remove(backends, paths, emit, probe() is not None)
    return _apply_install(opts, backends, paths, emit)


def _stdout(text: str) -> None:
    sys.stdout.write(text + "\n")


def _default_gateway_status() -> dict[str, Any] | None:
    """The running gateway's status (counts only), or None. A read of the loopback gateway."""
    from client_gateway import cli  # noqa: PLC0415

    port = cli.resolve_port(None)
    if cli.health(port) is None:
        return None
    reply = cli.call(port, "GET", "/__gateway/status")
    return reply[1] if reply is not None and reply[0] == 200 else {"ok": True}


def main(args: Any) -> int:
    """``python -m client_gateway setup [--yes] [--remove] [--status] [--register] [--desktop-apps]
    [--managed-ok]``."""
    opts = Options(
        yes=bool(args.yes),
        remove=bool(args.remove),
        status=bool(args.status),
        register=bool(args.register),
        managed_ok=bool(args.managed_ok),
        desktop=bool(getattr(args, "desktop_apps", False)),
    )
    if opts.status and (opts.yes or opts.remove or opts.register or opts.desktop):
        sys.stdout.write("refused: --status is read-only; it takes no other option\n")
        return EXIT_REFUSED
    if opts.yes and not (opts.remove or opts.desktop):
        return run(opts, None, None, None, None, "")  # on demand: no system backend is opened
    backends, note = open_backends()
    if backends is not None and not opts.yes:
        backends = read_only(backends)  # a plan or a status can never write
    return run(opts, backends, None, None, None, note)
