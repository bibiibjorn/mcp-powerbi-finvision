"""Shared state of the gateway process (memory only, thread-safe).

* ONE machine-wide ``SessionMap`` (stable pseudonyms through ``stable_token_fn``) shared by every
  Claude Code session: the same value has the same pseudonym everywhere, and the provider adds
  to it (add-only).
* One ``RemaskMemo`` per Claude session (``x-claude-code-session-id`` request header, else
  ``default``), evicted after 24 h idle.
* Per-session MODE ``on`` | ``off`` (``off`` = byte-for-byte passthrough for that session only) and
  a global mode. A session without its own switch follows the global mode, which starts at
  ``client_gateway.default_mode`` (default ``off``; anything else than ``off`` means ``on``). The
  control token in ``<home>/gateway.token`` is required for every switch to ``off`` and for every
  global switch; a session can be switched ``on`` without it (the AI may do that, never ``off``).
* Counters (never values), the readiness of the dictionary and two bounded caches (masked
  strings, redacted images) that keep the bytes sent upstream identical from turn to turn.
* PBI-chat rule (``client_gateway.pbi_chats_only``, default on): only a chat that carried this
  install's marker (``core.client_guard.marker``) is masked automatically; any other chat is
  forwarded byte-identical (``request_mode`` -> ``passthrough``). A marked chat stays a PBI chat
  (sticky, persisted next to the switches: compaction can drop the marked tool results).
* The per-session switches survive a restart: ``<home>/session_modes.json`` (session ids and
  modes only, 7 days). A ``start --replace`` also carries the modes the old gateway applied
  (``carry_session_modes``, by the 8-character id prefix its status shows), so a chat that was
  masked stays masked after the update: a restart never turns masking off for a chat.
* The TLS front end (``client_gateway.frontend``) uses the same rule through ``frontend_mode`` with
  session ids ``"<adapter>:<chat key>"`` (``frontend_chat_id``), which share the persistence and the
  7-day expiry above but never a carried 8-character prefix. A switch TYPED by the human in a
  front-end chat (``frontend_set_mode``) needs no control token, also for ``off``: it is the
  human's own message, read from the user's own app traffic, and acts on that one chat only.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from client_gateway import __version__
from client_gateway.cache import BoundedCache
from client_gateway.memo import RemaskMemo
from core.client_guard.engine import SessionMap
from core.client_guard.marker import has_marker
from core.client_guard.marker import marker as install_marker

SESSION_HEADER = "x-claude-code-session-id"
DEFAULT_SESSION = "default"
TOKEN_FILE = "gateway.token"
TOKEN_HEADER = "X-Gateway-Token"
MODES = ("on", "off")
IDLE_TTL_S = 24 * 3600.0
EVICT_EVERY_S = 60.0
MASK_CACHE_CHARS = 48_000_000
IMAGE_CACHE_BYTES = 96_000_000
READY_WAIT_S = 60.0  # a masked request waits this long for the dictionary (a restart reads every open model again)
IMAGE_POLICIES = ("redact", "withhold", "pass")

DEFAULT_MODE_ENV = "FINVISION_GATEWAY_DEFAULT_MODE"
MODES_FILE = "session_modes.json"
MODES_TTL_S = 7 * 24 * 3600.0
MODES_TOUCH_S = 6 * 3600.0  # a chat that is still in use renews its saved switch this often
PBI_RECENT_S = (
    15 * 60.0
)  # a Claude Code request without a session id follows a PBI chat this recent
TURN_TTL_S = (
    15 * 60.0
)  # a front-end turn id (Copilot ``x-interaction-id``) maps to its chat this long
TURN_MAX = 4096  # at most this many turn ids are remembered (memory only)
FILE_TTL_S = 15 * 60.0  # an upload (file id -> chat, checked or not) is remembered this long
FILE_MAX = 2048  # at most this many uploads are remembered (memory only)
_MODE_KINDS = ("sessions", "prefixes")  # entries that carry a mode
_PBI_KINDS = ("pbi", "pbi_prefixes")  # entries of chats that carried the marker (``at`` only)
# what ``request_mode`` decides for one request
MODE_ON = "on"
MODE_OFF = "off"  # forwarded byte-identical (a human switched masking off, or the legacy rule)
MODE_PASSTHROUGH = "passthrough"  # not a Power BI chat: forwarded byte-identical
MODE_UNMASK = "unmask"  # a human switched a PBI chat off: real values, marker stripped


def _read_modes(home: Path) -> dict[str, dict[str, dict[str, Any]]]:
    """``{"sessions": {id: {mode, at}}, "prefixes": {id8: {mode, at}}, "pbi": {id: {at}},
    "pbi_prefixes": {id8: {at}}}``, old entries dropped (``at`` is the last USE of the chat,
    renewed while it is in use: see ``GatewayState.session``)."""
    return _read_modes_counted(home)[0]


def _read_modes_counted(
    home: Path,
) -> tuple[dict[str, dict[str, dict[str, Any]]], int]:
    """``_read_modes`` plus the number of entries dropped as expired."""
    try:
        raw = json.loads((home / MODES_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    now = time.time()
    expired = 0
    out: dict[str, dict[str, dict[str, Any]]] = {kind: {} for kind in _MODE_KINDS + _PBI_KINDS}
    for kind in out:
        has_mode = kind in _MODE_KINDS
        items = raw.get(kind) if isinstance(raw, dict) else None
        for key, entry in items.items() if isinstance(items, dict) else []:
            if (
                isinstance(entry, dict)
                and (not has_mode or entry.get("mode") in MODES)
                and now - float(entry.get("at") or 0) <= MODES_TTL_S
            ):
                kept: dict[str, Any] = {"at": float(entry["at"])}
                out[kind][str(key)] = {"mode": entry["mode"], **kept} if has_mode else kept
            else:
                expired += 1
    return out, expired


def _write_modes(home: Path, modes: dict[str, Any]) -> None:
    try:
        home.mkdir(parents=True, exist_ok=True)
        tmp = home / (MODES_FILE + ".tmp")
        tmp.write_text(json.dumps(modes), encoding="utf-8")
        os.replace(tmp, home / MODES_FILE)
    except OSError:
        pass  # the switch still holds in memory


def carry_session_modes(home: Path, sessions: list[dict[str, Any]]) -> int:
    """Before a replaced gateway stops: keep the mode it APPLIED to each of its chats (``id`` is
    the 8-character prefix of its status) and which chats are PBI chats (``pbi``). A PBI chat
    that follows the rule, with no switch of its own, keeps no mode: a human can still switch it
    off and it is never frozen on. Returns the number of chats carried."""
    modes = _read_modes(home)
    now = time.time()
    n = 0
    for item in sessions:
        if not isinstance(item, dict):
            continue
        prefix = str(item.get("id") or "")[:8]
        if len(prefix) != 8:
            continue
        mode = item.get("mode")
        pbi = item.get("pbi") is True
        carried = False
        if pbi:
            modes["pbi_prefixes"][prefix] = {"at": now}
            carried = True
        if mode in MODES and not (pbi and item.get("own_switch") is False):
            modes["prefixes"][prefix] = {"mode": mode, "at": now}
            carried = True
        n += carried
    _write_modes(home, modes)
    return n


STAT_NAMES = (
    "requests",
    "masked_requests",
    "passthrough_requests",
    "masked_strings",
    "memo_hits",
    "images_redacted",
    "images_withheld",
    "images_clean",
    "thinking_dropped",
    "blocked",
    "not_ready",
    "upstream_errors",
    "upstream_retries",
    "unmasked_guard",
    "pbi_chat_requests",
    "pbi_unmasked_requests",
    "headerless_requests",
    "ner_degraded",
    "ner_auto_degraded",
    "not_ready_bypassed",
    "history_scans",
    "history_scans_capped",
    "table_values_learned",
    "responses_rehydrated",
    "tokens_rehydrated",
    "authority_mask_calls",
    "authority_unmask_calls",
    "authority_failures",
    "frontend_requests",
    "frontend_masked",
    "frontend_passthrough",
    "frontend_blocked",
    "frontend_ws_dropped",
    "errors",
    # front end, R9 (append only)
    "residual_leak_blocked",  # a masked request still held a known client value: blocked
    "format_changed",  # a masked request had a shape the adapter does not know: blocked
    "rehydration_unrecognised",  # a masked reply in which the rehydrator knew no event shape
    "frontend_turn_mapped",  # a side call that followed its chat through the turn id
    # front end, uploads (phase 3b.0; append only)
    "unchecked_attachment_blocked",  # a masked chat referenced an image nobody redacted / checked
    "frontend_files_noted",  # an upload attributed to a chat through its same-origin Referer
    # routing (phase 3b.5; append only)
    "pac_served",  # GET /proxy.pac answered
    "mcp_attached",  # a Power BI MCP process registered itself
)


def frontend_chat_id(adapter_name: str, chat_key: str | None) -> str:
    """The session id of a front-end chat: ``"<adapter>:<chat key>"`` (a request without a chat key
    belongs to the adapter's headerless chat ``"<adapter>:"``). The adapter name keeps these ids
    apart from the Claude Code session ids (UUIDs) and from each other."""
    return f"{adapter_name}:{chat_key or ''}"


class ModeDenied(Exception):
    """A mode switch that needs the control token was tried without it (fixed message)."""

    def __init__(self) -> None:
        super().__init__("control token required")


# ------------------------------------------------------------------------------------ helpers
def cfg(key: str, default: Any) -> Any:
    """Config value (dot path) with a fallback; a config that cannot be loaded gives ``default``."""
    try:
        from core.config.config_manager import config

        return config.get(key, default)
    except Exception:  # noqa: BLE001 - never block the gateway on the config
        return default


def masking_home() -> Path:
    from core.client_guard.home import home_dir

    return home_dir()


def ensure_token(home: Path) -> str:
    """The per-install secret that authorises switching masking OFF (``<home>/gateway.token``).

    Only the user's own prompt hook reads it (when the USER types ``client mode off``).
    """
    path = home / TOKEN_FILE
    home.mkdir(parents=True, exist_ok=True)
    for _ in range(40):
        token = read_token(home)
        if token:
            return token
        # created EXCLUSIVELY: two gateways (or a gateway and the CLI) starting together must end
        # up with the one token that is in the file, not each keep its own copy
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            time.sleep(0.05)  # somebody is writing it right now: read it again
            continue
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            fh.write(secrets.token_urlsafe(32))
    # an unusable file (empty / too short and not being written): replace it atomically
    tmp = path.with_name(TOKEN_FILE + ".tmp")
    tmp.write_text(secrets.token_urlsafe(32), encoding="ascii")
    os.replace(tmp, path)
    token = read_token(home)
    if token is None:
        raise OSError("control token could not be created")
    return token


def read_token(home: Path) -> str | None:
    """The token if the file exists (for the CLI / hooks); never creates it."""
    try:
        token = (home / TOKEN_FILE).read_text(encoding="ascii").strip()
    except OSError:
        return None
    return token if len(token) >= 32 else None


# a new pseudonym key (new codes for every client) after this many days, at a gateway start;
# config client_guard.key_rotation_days, 0 = never (core.client_guard.tokens)
KEY_ROTATION_DAYS = 90


def _token_source(home: Path) -> tuple[Callable[[str, str], int], bool]:
    """``(token_fn, ephemeral)``: the stable keyed function, or (when the key file cannot be used)
    the per-process one, which makes pseudonyms differ after a restart (a ``warnings()`` code)."""
    from core.client_guard.tokens import ephemeral_token_fn, stable_token_fn

    try:
        days = cfg("client_guard.key_rotation_days", KEY_ROTATION_DAYS)
        days = days if isinstance(days, (int, float)) and not isinstance(days, bool) else 0
        return stable_token_fn(home, rotate_after_days=float(days)), False
    except Exception:  # noqa: BLE001 - never block masking on the key file
        return ephemeral_token_fn(), True


def clean_mode(value: Any, default: str = "on") -> str:
    mode = str(value).strip().lower() if value is not None else ""
    return mode if mode in MODES else default


# ------------------------------------------------------------------------------------ sessions
@dataclass(eq=False)
class _FileNote:
    """One upload as the front end saw it (ids only, never content)."""

    chat: str | None
    at: float
    checked: bool = False


NER_REFUSALS_BEFORE_DEGRADE = 1  # one refused message, then dictionary-only: never a locked chat


@dataclass(eq=False)
class SessionState:
    session_id: str
    memo: RemaskMemo = field(default_factory=RemaskMemo)
    override: str | None = None  # 'on' | 'off' set by the user; None follows the global mode
    ner_degrade: bool = False  # the user chose dictionary-only masking (name recognition skipped)
    last_seen: float = 0.0
    requests: int = 0
    pbi: bool = False  # this chat carried the install's marker once (sticky, persisted)
    ner_blocks: int = 0  # name-recognition refusals in a row (owner rule: a chat never locks)
    ready_blocks: int = 0  # dictionary-not-ready refusals in a row (same rule)

    def ner_refused(self) -> bool:
        """Count a name-recognition refusal. True when this one already switched the chat to
        dictionary-only masking (the second refusal in a row): the owner's rule is that the gateway
        may refuse ONE message, never lock a chat, so from then on name recognition that cannot
        finish is skipped and every known client value is still masked by the dictionary."""
        self.ner_blocks += 1
        if self.ner_blocks >= NER_REFUSALS_BEFORE_DEGRADE and not self.ner_degrade:
            self.ner_degrade = True
            return True
        return False

    def ner_ok(self) -> None:
        self.ner_blocks = 0


class GatewayState:
    def __init__(
        self,
        home: Path | None = None,
        default_mode: str | None = None,
        images_policy: str | None = None,
        smap: SessionMap | None = None,
        clock: Callable[[], float] = time.monotonic,
        idle_ttl_s: float = IDLE_TTL_S,
        ready_wait_s: float | None = None,
        pbi_chats_only: bool | None = None,
    ) -> None:
        self.home = Path(home) if home is not None else masking_home()
        self.clock = clock
        self.idle_ttl_s = idle_ttl_s
        self.default_mode = clean_mode(
            default_mode
            if default_mode is not None
            else os.environ.get(DEFAULT_MODE_ENV) or cfg("client_gateway.default_mode", "off")
        )
        policy = images_policy if images_policy is not None else cfg("client_gateway.images", "")
        policy = str(policy or "redact").strip().lower()
        self.images_policy = policy if policy in IMAGE_POLICIES else "redact"
        wait = ready_wait_s if ready_wait_s is not None else cfg("client_gateway.ready_wait_s", 0)
        try:
            self.ready_wait_s = float(wait) if float(wait) > 0 else READY_WAIT_S
        except (TypeError, ValueError):
            self.ready_wait_s = READY_WAIT_S
        self.detectors: Any = None
        self.token_key_ephemeral = False
        if smap is None:
            from client_gateway.detectors import Detectors  # noqa: PLC0415 - imports this module

            self.detectors = Detectors(self.home)
            token_fn, self.token_key_ephemeral = _token_source(self.home)
            smap = self.detectors.session_map(token_fn)
        self.smap = smap
        try:
            self.token: str | None = ensure_token(self.home)
        except OSError:
            self.token = None  # switching OFF then stays impossible: fail closed
        self.global_mode = self.default_mode
        # stop a request with client names in a chat where nobody switched masking on or off
        self.guard_unmasked = str(
            cfg("client_gateway.guard_unmasked", True)
        ).strip().lower() not in ("0", "false", "off", "no")
        # the safety net also looks for unknown names (NER worker) and in screenshots (OCR)
        self.guard_ner = str(cfg("client_gateway.guard_ner", True)).strip().lower() not in (
            "0",
            "false",
            "off",
            "no",
        )
        flag = (
            pbi_chats_only
            if pbi_chats_only is not None
            else cfg("client_gateway.pbi_chats_only", True)
        )
        self.pbi_chats_only = str(flag).strip().lower() not in ("0", "false", "off", "no")
        self.marker = ""
        self.marker_error = False
        if self.pbi_chats_only:
            try:
                self.marker = install_marker(self.home)
            except OSError:
                # no marker, so no PBI chat can be told apart: the legacy rule (every chat follows
                # the mode, masking when on) is the safe fallback, shown as a warning
                self.pbi_chats_only = False
                self.marker_error = True
        self.started = time.time()
        self._lock = threading.RLock()
        self._sessions: dict[str, SessionState] = {}
        # switches from before a restart (and how many aged out: shown in the status)
        self._modes, self.modes_expired = _read_modes_counted(self.home)
        self._last_evict = clock()
        self._stats = dict.fromkeys(STAT_NAMES, 0)
        self.mask_cache = BoundedCache(MASK_CACHE_CHARS)
        self.image_cache = BoundedCache(IMAGE_CACHE_BYTES)
        self._ready = threading.Event()
        self._ready.set()  # no provider attached: the dictionary is whatever the map holds
        self._not_ready: dict[str, Any] = {}
        self.provider_status: Callable[[], dict[str, Any]] | None = None
        self.refresh_hook: Callable[[float], bool] | None = None
        self.verify_hook: Callable[[], None] | None = None
        self.notice_hook: Callable[[], None] | None = None
        self.frontend_status: Callable[[], dict[str, Any]] | None = None
        # routing (phase 3b.5): the PAC text while routing is on (``GET /proxy.pac``), and the
        # lifecycle / routing view for the status (fixed labels and counts only)
        self.pac_provider: Callable[[], str] | None = None
        self.routing_status: Callable[[], dict[str, Any]] | None = None
        # (chat space, turn id) -> (chat key, clock()): the side calls of a Copilot turn carry the
        # turn id of the main call, which names the chat (memory only, never persisted)
        self._frontend_turns: dict[tuple[str, str], tuple[str, float]] = {}
        # (chat space, file id or upload id) -> the upload's note, shared by all ids of one upload
        # (memory only, ``FILE_TTL_S``): which chat it belongs to and whether the gateway redacted
        # or checked its bytes while that chat was masked
        self._frontend_files: dict[tuple[str, str], _FileNote] = {}
        # adapter name -> {app label: requests} (labels only: app and version, never a header)
        self._frontend_apps: dict[str, dict[str, int]] = {}
        ner = getattr(self.detectors, "ner", None)
        if ner is not None:
            ner.keep_alive = self.any_masked  # the model stays loaded while a masked chat exists

    # ------------------------------------------------------------------ sessions
    def session(self, session_id: str | None) -> SessionState:
        sid = (session_id or "").strip() or DEFAULT_SESSION
        now = self.clock()
        with self._lock:
            if now - self._last_evict >= EVICT_EVERY_S:
                self._last_evict = now
                for key in [
                    k for k, s in self._sessions.items() if now - s.last_seen > self.idle_ttl_s
                ]:
                    del self._sessions[key]
            state = self._sessions.get(sid)
            if state is None:
                state = self._sessions[sid] = SessionState(
                    sid, override=self._saved_mode(sid), pbi=self._saved_pbi(sid)
                )
            state.last_seen = now
            if state.override is not None:
                self._touch_mode(sid, state.override)
            if state.pbi:
                self._touch_pbi(sid)
            return state

    def _touch_mode(self, sid: str, mode: str) -> None:
        """Renew the saved switch of a chat that is in use (at most every ``MODES_TOUCH_S``): the
        7-day expiry counts from the last use, not from the switch (a restart after a week must not
        silently turn masking off for a chat that was used a minute ago). Caller holds the lock."""
        wall = time.time()
        entry = self._modes["sessions"].get(sid)
        if entry is not None and wall - entry["at"] < MODES_TOUCH_S:
            return
        self._modes["sessions"][sid] = {"mode": mode, "at": wall}
        _write_modes(self.home, self._modes)

    def _touch_pbi(self, sid: str) -> None:
        """Remember (or renew) that a chat is a PBI chat; same 7-day, renewed-on-use expiry as the
        switches. Caller holds the lock."""
        wall = time.time()
        entry = self._modes["pbi"].get(sid)
        if entry is not None and wall - entry["at"] < MODES_TOUCH_S:
            return
        self._modes["pbi"][sid] = {"at": wall}
        _write_modes(self.home, self._modes)

    def _saved_pbi(self, sid: str) -> bool:
        """True when this chat carried the marker before (its own id, else a carried prefix)."""
        if sid == DEFAULT_SESSION:
            return False
        if (
            ":" in sid
        ):  # a front-end chat: no 8-character prefix of its own (it is the adapter name)
            return sid in self._modes["pbi"]
        return sid in self._modes["pbi"] or sid[:8] in self._modes["pbi_prefixes"]

    def has_saved_on(self) -> bool:
        """True when a chat that was masked before a restart is remembered (its switch survives
        the restart, so the name recognizer must be loaded for its first request)."""
        with self._lock:
            if self.pbi_chats_only and (self._modes["pbi"] or self._modes["pbi_prefixes"]):
                return True
            return any(
                entry["mode"] == "on"
                for kind in _MODE_KINDS
                for entry in self._modes[kind].values()
            )

    def any_masked(self) -> bool:
        """True while masking is on for any chat (globally, by a chat's own switch, or because it
        is a PBI chat)."""
        with self._lock:
            return (
                self.global_mode == "on"
                or any(s.override == "on" for s in self._sessions.values())
                or (self.pbi_chats_only and any(s.pbi for s in self._sessions.values()))
            )

    def pbi_active_within(self, seconds: float) -> bool:
        """True when a PBI chat sent a request in the last ``seconds``."""
        now = self.clock()
        with self._lock:
            return any(s.pbi and now - s.last_seen <= seconds for s in self._sessions.values())

    def _saved_mode(self, sid: str) -> str | None:
        """The switch this chat had before a restart (its own id, else a carried prefix)."""
        entry = self._modes["sessions"].get(sid)
        if entry is None and ":" not in sid:  # (a front-end chat has no carried prefix)
            entry = self._modes["prefixes"].get(sid[:8])
        return entry["mode"] if entry else None

    def active_within(self, seconds: float) -> bool:
        """True when any chat sent a request in the last ``seconds`` (the provider's load gate)."""
        now = self.clock()
        with self._lock:
            return any(now - s.last_seen <= seconds for s in self._sessions.values())

    def effective_mode(self, session_id: str | None) -> str:
        state = self.session(session_id)
        with self._lock:
            if state.override is None and state.session_id == DEFAULT_SESSION:
                # a request without the session header (a side query, an older client): it cannot
                # name its chat, so it follows the strictest chat that exists
                if self.global_mode != "on" and any(
                    s.override == "on" for s in self._sessions.values()
                ):
                    return "on"
            return state.override or self.global_mode

    def request_mode(self, session_id: str | None, body: bytes) -> str:
        """What to do with one request: ``on`` (mask, rehydrate the reply), ``off`` / ``passthrough``
        (forward byte-identical), ``unmask`` (a PBI chat a human switched off: real values, marker
        stripped, reply not rehydrated).

        With ``pbi_chats_only`` off this is the legacy rule (``effective_mode``). Otherwise: a body
        that carries the marker makes the chat a PBI chat for good; a chat's own ``off`` / ``on``
        wins; a PBI chat is masked; the global mode ``on`` masks everything; anything else is
        passed through. A request without a chat id (a side query) is masked when its body carries
        the marker, when any chat's own switch is ``on`` or when a PBI chat was active in the last
        15 minutes."""
        if not self.pbi_chats_only:
            return self.effective_mode(session_id)
        state = self.session(session_id)
        hit = has_marker(body, self.marker)
        headerless = state.session_id == DEFAULT_SESSION
        newly = False
        with self._lock:
            if hit and not headerless:
                newly = not state.pbi
                state.pbi = True
                self._touch_pbi(state.session_id)
            pbi = state.pbi or hit
            if pbi:
                self.bump("pbi_chat_requests")
            if state.override == "off":
                mode = MODE_UNMASK if pbi else MODE_OFF
            elif state.override == "on" or state.pbi or self.global_mode == "on":
                mode = MODE_ON
            elif headerless and (
                hit
                or any(s.override == "on" for s in self._sessions.values())
                or self.pbi_active_within(PBI_RECENT_S)
            ):
                mode = MODE_ON
            else:
                mode = MODE_PASSTHROUGH
        if newly and mode == MODE_ON and self.detectors is not None:
            self.detectors.masking_on()  # the name recognizer starts before the first masked request
        return mode

    def peek_session(self, session_id: str) -> SessionState | None:
        """An existing session WITHOUT creating or touching it (None when there is none)."""
        with self._lock:
            return self._sessions.get(session_id)

    def peek_mode(self, session_id: str) -> str:
        """Mode of a session WITHOUT creating or touching it."""
        with self._lock:
            state = self._sessions.get(session_id)
            override = state.override if state else self._saved_mode(session_id)
            pbi = state.pbi if state else self._saved_pbi(session_id)
            if override is None and pbi and self.pbi_chats_only:
                return "on"
            return override or self.global_mode

    def check_token(self, supplied: str | None) -> bool:
        if not self.token or not supplied:
            return False
        return hmac.compare_digest(str(supplied).encode("utf-8"), self.token.encode("utf-8"))

    def set_mode(
        self,
        session_id: str | None,
        mode: str,
        token: str | None = None,
        ner_degrade: bool = False,
    ) -> dict[str, Any]:
        """Switch a session (``session_id`` set) or every session (empty) to ``mode``.

        ``off``, every global switch and ``ner_degrade`` (``on`` with name recognition skipped
        when it cannot finish: dictionary-only masking, the user's explicit choice) need the
        control token (``ModeDenied``); a session can be switched ``on`` without it. Raises
        ``ValueError`` for an unknown mode.
        """
        mode = str(mode).strip().lower()
        if mode not in MODES:
            raise ValueError("bad_mode")
        sid = (session_id or "").strip()
        if (not sid or mode == "off" or ner_degrade) and not self.check_token(token):
            raise ModeDenied()
        if ner_degrade and (mode != "on" or not sid):
            raise ValueError("bad_mode")
        with self._lock:
            if not sid:
                self.global_mode = mode
                for state in self._sessions.values():
                    state.override = None
                # the chats' own switches are cleared; which chats are PBI chats is not a switch
                self._modes = {
                    **{kind: {} for kind in _MODE_KINDS},
                    **{kind: self._modes[kind] for kind in _PBI_KINDS},
                }
                _write_modes(self.home, self._modes)
                return {"scope": "global", "mode": mode}
        return self._apply_session_mode(sid, mode, ner_degrade)

    def _apply_session_mode(self, sid: str, mode: str, ner_degrade: bool = False) -> dict[str, Any]:
        """Set one session's own switch (the token check, if any, is the caller's)."""
        with self._lock:
            state = self.session(sid)
            state.override = mode
            state.ner_degrade = bool(ner_degrade) and mode == "on"
            if mode == "on":
                state.memo.history_scanned = False  # what was typed while off was never recognized
            self._modes["sessions"][sid] = {"mode": mode, "at": time.time()}
            _write_modes(self.home, self._modes)
            result = {
                "scope": "session",
                "session": sid,
                "mode": mode,
                "ner": "degraded" if state.ner_degrade else "strict",
            }
        if mode == "on":
            if self.detectors is not None:
                self.detectors.masking_on()  # NER worker starts before the first masked request
            self.verify()  # a model opened since the last cycle is read now, not on the request
        return result

    # ------------------------------------------------------------------ front end (phase 3a)
    def frontend_mode(
        self, chat_key: str | None, marker_hit: bool, adapter_name: str, typed: str | None = None
    ) -> str:
        """What to do with one request the TLS front end read: ``on`` / ``off`` / ``passthrough`` /
        ``unmask`` (the meanings of ``request_mode``). ``chat_key`` is the RAW chat id of the wire
        (None when the request names none); the session id is ``frontend_chat_id(adapter, key)``.
        ``typed`` (``on`` / ``off``) is a command the human typed in THIS request when the chat has
        no id yet (a first ChatGPT turn): it acts like the chat's own switch for this request.

        Same rules as ``request_mode``, with the chat key in place of the Claude session header:
        a marked request makes its chat a PBI chat for good (sticky, persisted); the chat's own
        ``off`` / ``on`` wins (an ``off`` PBI chat is ``unmask``); a PBI chat is masked; the global
        ``on`` masks everything; anything else is passed through (rule R2).

        A request WITHOUT a chat key belongs to no chat: it is masked only when it carries the
        marker itself (or the human typed a switch in it), never because some other chat of the
        same app was a PBI chat a moment ago (no time window, no adapter-wide state): a chat that
        never used Power BI must never be masked or blocked."""
        sid = frontend_chat_id(adapter_name, chat_key)
        keyed = bool(chat_key)
        if not self.pbi_chats_only:
            own = typed if (typed in MODES and not keyed) else None
            if keyed:
                own = self.session(sid).override
            return MODE_ON if (own or self.global_mode) == "on" else MODE_OFF
        newly = False
        with self._lock:
            if keyed:
                state = self.session(sid)
                if marker_hit:
                    newly = not state.pbi
                    state.pbi = True
                    self._touch_pbi(sid)
                pbi = state.pbi
                own = state.override
            else:
                pbi = marker_hit
                newly = marker_hit
                own = typed if typed in MODES else None
            if pbi:
                self.bump("pbi_chat_requests")
            if own == "off":
                mode = MODE_UNMASK if pbi else MODE_OFF
            elif own == "on" or pbi or self.global_mode == "on":
                mode = MODE_ON
            else:
                mode = MODE_PASSTHROUGH
        if newly and mode == MODE_ON and self.detectors is not None:
            self.detectors.masking_on()
        return mode

    def frontend_set_mode(
        self, adapter_name: str, chat_key: str | None, mode: str
    ) -> dict[str, Any]:
        """The human TYPED ``masking on`` / ``masking off`` in a front-end chat (rule R4: only a
        whole-message typed command from the user's own app traffic reaches this). Unlike
        ``set_mode`` it needs no control token, also for ``off``: the text is the human's own message
        read from the user's own app traffic, which the AI or a tool result cannot forge, and it
        switches this ONE chat only (never a global switch). Every other ``off`` path keeps the
        token. Raises ``ValueError`` for an unknown mode."""
        mode = str(mode).strip().lower()
        if mode not in MODES:
            raise ValueError("bad_mode")
        return self._apply_session_mode(frontend_chat_id(adapter_name, chat_key), mode)

    def frontend_bind_chat(
        self,
        adapter_name: str,
        chat_key: str,
        *,
        pbi: bool = False,
        mode: str | None = None,
        memo: RemaskMemo | None = None,
    ) -> None:
        """A first-turn chat got its id (from the response): carry over what was learned from its
        request, the marker (``pbi``), the typed switch (``mode``) and the remask memo."""
        if not chat_key:
            return
        sid = frontend_chat_id(adapter_name, chat_key)
        state = self.session(sid)
        with self._lock:
            if memo is not None:
                state.memo = memo
            if pbi and not state.pbi:
                state.pbi = True
                self._touch_pbi(sid)
        if mode in MODES:
            self._apply_session_mode(sid, str(mode))

    def frontend_chat_masked(self, adapter_name: str, chat_key: str | None) -> bool:
        """Read-only: is this front-end chat masked (its own ``on``, a PBI chat, or the global
        ``on``), unless a human switched it off? Never creates a session."""
        if not chat_key:
            return False
        sid = frontend_chat_id(adapter_name, chat_key)
        with self._lock:
            session = self._sessions.get(sid)
            own = session.override if session else self._saved_mode(sid)
            pbi = (session.pbi if session else self._saved_pbi(sid)) and self.pbi_chats_only
            if own == "off":
                return False
            return own == "on" or pbi or self.global_mode == "on"

    def frontend_note_turn(self, space: str, turn_id: str, chat_key: str) -> None:
        """A main call named its chat: remember ``turn id -> chat key`` for ``TURN_TTL_S`` so the
        side calls of the same turn (intent, title, summary: they carry the turn id but no first
        user turn of their own) follow that chat. Memory only."""
        if not (turn_id and chat_key):
            return
        with self._lock:
            now = self.clock()
            turns = self._frontend_turns
            turns[(space, turn_id)] = (chat_key, now)
            if len(turns) > TURN_MAX:
                for key in [k for k, (_c, at) in turns.items() if now - at > TURN_TTL_S]:
                    del turns[key]
                while len(turns) > TURN_MAX:  # still full: the oldest entry goes
                    del turns[next(iter(turns))]

    def frontend_turn_chat(self, space: str, turn_id: str) -> str | None:
        """The chat key a turn id was registered for (within ``TURN_TTL_S``), else None."""
        if not turn_id:
            return None
        with self._lock:
            entry = self._frontend_turns.get((space, turn_id))
            if entry is None:
                return None
            chat_key, at = entry
            if self.clock() - at > TURN_TTL_S:
                del self._frontend_turns[(space, turn_id)]
                return None
            return chat_key

    def frontend_note_file(
        self, space: str, chat_key: str | None, file_ids: tuple[str, ...]
    ) -> None:
        """An upload (``POST /files``) was attributed to a chat (``chat_key``: through its same-origin
        Referer): remember ``file id -> chat`` for ``FILE_TTL_S``. Every id of one upload (the file id
        and the id its upload URL carries) shares one note. Memory only, never persisted."""
        ids = tuple(i for i in file_ids if i)
        if not ids:
            return
        with self._lock:
            now = self.clock()
            note = _FileNote(chat_key or None, now)
            files = self._frontend_files
            for fid in ids:
                files[(space, fid)] = note
            if len(files) > FILE_MAX:
                for key in [k for k, n in files.items() if now - n.at > FILE_TTL_S]:
                    del files[key]
                while len(files) > FILE_MAX:  # still full: the oldest entry goes
                    del files[next(iter(files))]
        if chat_key:
            self.bump("frontend_files_noted")

    def _file_note(self, space: str, file_id: str) -> "_FileNote | None":
        """The live note of an upload id (within ``FILE_TTL_S``), else None. Caller holds the lock."""
        note = self._frontend_files.get((space, file_id))
        if note is None:
            return None
        if self.clock() - note.at > FILE_TTL_S:
            del self._frontend_files[(space, file_id)]
            return None
        return note

    def frontend_file_chat(self, space: str, file_id: str) -> str | None:
        """The chat key an upload id was attributed to (within ``FILE_TTL_S``), else None."""
        if not file_id:
            return None
        with self._lock:
            note = self._file_note(space, file_id)
            return note.chat if note is not None else None

    def frontend_file_checked(self, space: str, file_ids: tuple[str, ...]) -> None:
        """The gateway redacted or checked the bytes of this upload while its chat was masked."""
        with self._lock:
            for fid in file_ids:
                note = self._file_note(space, fid)
                if note is not None:
                    note.checked = True

    def frontend_files_unchecked(self, space: str, file_ids: tuple[str, ...]) -> int:
        """How many of these file ids (an attachment a masked chat sends) the gateway did NOT redact
        or check: a file it never saw (an upload from before the gateway started, a first-turn upload
        of a new chat that cannot be attributed, an upload older than ``FILE_TTL_S``) counts too."""
        with self._lock:
            missing = 0
            for fid in dict.fromkeys(file_ids):
                note = self._file_note(space, fid)
                if note is None or not note.checked:
                    missing += 1
            return missing

    def frontend_note_app(self, adapter_name: str, labels: tuple[str, ...]) -> None:
        """Count the app labels (``name/version``, see ``adapters.appinfo``) one request carried."""
        if not labels:
            return
        with self._lock:
            seen = self._frontend_apps.setdefault(adapter_name, {})
            for label in labels:
                if label in seen or len(seen) < 32:  # bounded: a hostile header cannot grow it
                    seen[label] = seen.get(label, 0) + 1

    def frontend_apps(self) -> dict[str, dict[str, Any]]:
        """``{adapter: {"seen": {label: n}, "untested": [labels]}}`` for the status (labels only)."""
        from client_gateway.adapters.appinfo import is_tested

        with self._lock:
            return {
                name: {
                    "seen": dict(sorted(labels.items())),
                    "untested": sorted(label for label in labels if not is_tested(name, label)),
                }
                for name, labels in sorted(self._frontend_apps.items())
            }

    # ------------------------------------------------------------------ counters
    def bump(self, name: str, n: int = 1) -> None:
        if n:
            with self._lock:
                self._stats[name] = self._stats.get(name, 0) + n

    def stats(self) -> dict[str, int]:
        with self._lock:
            return dict(self._stats)

    # ------------------------------------------------------------------ dictionary readiness
    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    def set_ready(
        self, ready: bool, reason: str = "", detail: dict[str, Any] | None = None
    ) -> None:
        """Open or close the gate. ``reason`` is a fixed code and ``detail`` holds counts and fixed
        labels only (never a value); they tell the user WHY requests are held."""
        if ready:
            self._not_ready = {}
            self._ready.set()
        else:
            self._not_ready = {"reason": reason or "dictionary_not_ready", **(detail or {})}
            self._ready.clear()

    def not_ready_info(self) -> dict[str, Any]:
        """Why the gate is closed (empty while it is open)."""
        return dict(self._not_ready) if not self._ready.is_set() else {}

    def verify(self) -> None:
        """Per masked request: let the provider re-check, cheaply, that no new model appeared
        since its last poll (closes the gate until that model is read). Never raises."""
        hook = self.verify_hook
        if hook is not None:
            try:
                hook()
            except Exception:  # noqa: BLE001 - the poll thread still covers it
                pass

    def notice(self) -> None:
        """A chat sent a request: the provider may run a cycle in the background when its last
        one is old. Never blocks, never raises."""
        hook = self.notice_hook
        if hook is not None:
            try:
                hook()
            except Exception:  # noqa: BLE001
                pass

    def wait_ready(self, timeout: float | None = None) -> bool:
        return self._ready.wait(self.ready_wait_s if timeout is None else timeout)

    def request_refresh(self, wait_s: float = 0.0) -> bool:
        """Ask the provider to re-detect now; waits up to ``wait_s`` for that cycle to finish.
        False when no provider is attached or the wait ran out."""
        hook = self.refresh_hook
        return False if hook is None else bool(hook(wait_s))

    # ------------------------------------------------------------------ view (counts only)
    def _provider_view(self) -> dict[str, Any]:
        hook = self.provider_status
        if hook is None:
            return {"attached": False}
        try:
            return dict(hook())
        except Exception:  # noqa: BLE001
            return {"attached": True, "error": True}

    def _routing_view(self) -> dict[str, Any]:
        hook = self.routing_status
        if hook is None:
            return {"wanted": False}
        try:
            return dict(hook())
        except Exception:  # noqa: BLE001
            return {"wanted": True, "error": True}

    def _frontend_view(self) -> dict[str, Any]:
        hook = self.frontend_status
        if hook is None:
            return {"enabled": False}
        try:
            return dict(hook())
        except Exception:  # noqa: BLE001
            return {"enabled": True, "error": True}

    def dictionary_counts(self) -> dict[str, int]:
        """``{prefix: n}`` over all sources."""
        out: dict[str, int] = {}
        for by_prefix in self.smap.counts().values():
            for prefix, n in by_prefix.items():
                out[prefix] = out.get(prefix, 0) + n
        return dict(sorted(out.items()))

    def status(self) -> dict[str, Any]:
        now = self.clock()
        with self._lock:
            items = [
                {
                    "id": s.session_id[:8],
                    "mode": self._applied_mode(s),
                    "own_switch": s.override is not None,
                    "pbi": s.pbi,
                    "ner_degraded": s.ner_degrade,
                    "requests": s.requests,
                    "idle_s": int(now - s.last_seen),
                }
                for s in self._sessions.values()
            ]
        return {
            "ok": True,
            "version": __version__,
            "pid": os.getpid(),
            "uptime_s": int(time.time() - self.started),
            "mode": self.global_mode,
            "default_mode": self.default_mode,
            "pbi_chats_only": self.pbi_chats_only,
            "pbi_chats": len(self._modes["pbi"]),
            "images_policy": self.images_policy,
            "ready": self.ready,
            "not_ready": self.not_ready_info(),
            "sessions": {"count": len(items), "items": items},
            "dictionary": {
                "values": self.smap.stats.get("values", 0),
                "by_prefix": self.dictionary_counts(),
            },
            "provider": self._provider_view(),
            "frontend": self._frontend_view(),
            "routing": self._routing_view(),
            "frontend_apps": self.frontend_apps(),
            "detectors": self.detectors.status() if self.detectors is not None else {},
            "masking_stats": {
                k: self.smap.stats.get(k, 0)
                for k in ("masked_hits", "shape_hits", "fuzzy_hits", "recognized", "case_other")
            },
            "stats": self.stats(),
            "caches": {"strings": len(self.mask_cache), "images": len(self.image_cache)},
            "modes_expired_at_start": self.modes_expired,
            "warnings": self.warnings(),
        }

    def _applied_mode(self, s: SessionState) -> str:
        """The mode a chat is under: its own switch, else ``on`` for a PBI chat or the global
        ``on``, else the global mode (``off``), or ``passthrough`` under the PBI-chat rule (not a
        switch: a restart must not freeze it as ``off``). Caller holds the lock."""
        if s.override is not None:
            return s.override
        if self.pbi_chats_only:
            return MODE_ON if (s.pbi or self.global_mode == "on") else MODE_PASSTHROUGH
        return self.global_mode

    def warnings(self) -> list[str]:
        """Fixed codes for states in which masking is on but weaker than it looks: an empty
        dictionary (no model read, no remembered values: only the name recognizer and the shape
        detectors work), a remembered-value store that could not be read or written, a pseudonym
        key that fell back to the per-process one, and the detector profile's own codes (unknown
        profile or shape pack, name recognition unavailable or failed to load)."""
        out: list[str] = []
        if self.marker_error:
            out.append("marker_unavailable")
        if not self.smap.stats.get("values", 0):
            out.append("dictionary_empty")
        store = getattr(self.detectors, "store", None)
        if store is not None and store.error:
            out.append("store_error:" + str(store.error))
        if self.token_key_ephemeral:
            out.append("token_key_ephemeral")
        detector_warnings = getattr(self.detectors, "warnings", None)
        if callable(detector_warnings):
            out.extend(detector_warnings())
        # R9: the front end met something it was not verified against (labels only)
        if self.stats().get("rehydration_unrecognised", 0):
            out.append("rehydration_unrecognised")
        for name, info in self.frontend_apps().items():
            if info["untested"]:
                out.append(f"untested_app_version:{name}")
        # routing was asked for but is not active: the desktop apps are not covered (the MCP layer
        # still masks Power BI results)
        routing = self._routing_view()
        if routing.get("wanted") and not routing.get("enabled"):
            out.append("routing_inactive:" + str(routing.get("reason") or "unknown"))
        return out
