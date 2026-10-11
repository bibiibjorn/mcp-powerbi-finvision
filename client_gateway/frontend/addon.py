"""The mitmproxy addon of the TLS front end: AI-app traffic in, masking decisions out.

One masking authority: this addon runs in the gateway process and shares its ``GatewayState``, the
wire adapters (``client_gateway.adapters``) and the engine. It decides per flow, in this order:

1. A host that is not routed (``ROUTED_SUFFIXES``), or a request no adapter understands and that is
   not covered by rule 5, passes through UNTOUCHED (rule R2). The request body is never modified
   unless the chat is a masked one. R2 is the top priority: no time window and no adapter-wide state
   ever makes a request of another chat masked or blocked (a chat that never used Power BI is never
   touched, whatever another chat of the same app did a moment ago).
2. Chat identity: ``adapter.chat_key`` / ``adapter.url_chat_key``, namespaced ``"<space>:<key>"``
   (``state.frontend_chat_id``; the space is ``adapters.base.chat_space_of``). Copilot has no chat
   id: its key is the hash of the chat's first user turn (``adapters.copilot``), and the side calls
   of a turn (no first turn of their own) follow their chat through ``x-interaction-id``
   (``state.frontend_note_turn`` / ``frontend_turn_chat``). The marker (``has_marker``) says the chat
   is a Power BI chat; a typed command (``adapters.control.typed_command`` over
   ``newest_user_text``, the WHOLE message only) switches the chat on or off (rule R4) and is
   handled HERE: that user turn is rewritten to ``NEUTRAL_TEXT`` before anything is forwarded, so the
   AI never learns about the command (a ChatGPT ``prepare`` draft that is exactly the command is
   rewritten the same way, without switching anything). An app that resends its history (Copilot)
   has every EARLIER typed command rewritten as well; only the newest turn can switch. The chat key
   is always read from the original body, so it never changes when the first turn was a command. A first
   ChatGPT turn has no id yet: its marker / typed switch ride along on the flow and are bound to the
   id the response reports (``ctx.on_chat_id``).
3. ``state.frontend_mode`` gives ``on`` / ``off`` / ``passthrough`` / ``unmask``. It is decided in its
   own guarded step: when deciding fails, a request that carries this install's marker is blocked
   and every other request is forwarded untouched.
4. ``on``: the body is decoded per ``content-encoding``, masked by the adapter, re-encoded, and
   ``content-length`` is fixed; the response is rehydrated (SSE: ``flow.response.stream`` with the
   adapter's rehydrator, flushed when mitmproxy calls the stream callable with ``b""`` at the end of
   the body; buffered JSON: ``unmask_response``). ``unmask`` (a human switched a PBI chat off):
   pseudonyms in the request become real values and the marker is stripped. Anything the engine
   cannot mask safely answers the app with a short fixed text and nothing is sent upstream. After
   the adapter the WHOLE outgoing body is scanned for known client values in every wire form
   (``leakscan``, rule R9): a hit blocks the request (``residual_leak``). An error after the mode
   is known blocks a masked flow only (``on`` / ``unmask``); a passthrough flow is never blocked.
5. An unknown POST / PUT / PATCH body on a routed host is blocked with a fixed text (spec §11) only
   when THIS flow belongs to a masked chat: the body carries the marker, the URL or the body names a
   registered masked chat id, or its Copilot chat key (first user turn, or the turn id) is a masked
   chat. Any other unknown body is forwarded untouched.
6. WebSocket frames are never changed; on a socket whose URL names a masked chat (or with the
   global mode on) an OUTGOING frame that holds a known identity value is dropped and counted.

Blocking work (masking, the readiness wait) runs in a worker pool: the ``request`` hook is async and
awaits it, so one slow chat never stalls the other flows. Logs and counters hold counts and fixed
labels only, never request or response text and never a full chat id.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from client_gateway.adapters.appinfo import app_labels
from client_gateway.adapters.base import (
    Adapter,
    MaskContext,
    Route,
    base_type,
    chat_space_of,
    norm_host,
)
from client_gateway.adapters.control import NEUTRAL_TEXT, typed_command
from client_gateway.adapters.registry import adapter_for, adapters
from client_gateway.adapters.streams import decode_bytes, encode_bytes
from client_gateway.leakscan import json_strings, residual_hits
from client_gateway.log import GatewayLog
from client_gateway.masking import MaskingFailed, dumps
from client_gateway.memo import RemaskMemo
from client_gateway.server import (
    MASK_FAILED_TEXT,
    NER_DEGRADED_TEXT,
    NER_HISTORY_TEXT,
    NER_INCOMPLETE_TEXT,
    _code_id,
    _not_ready_message,
)
from client_gateway.state import (
    MODE_OFF,
    MODE_ON,
    MODE_PASSTHROUGH,
    MODE_UNMASK,
    GatewayState,
    frontend_chat_id,
)
from client_gateway.routing.ca import INTERCEPTED_DOMAINS
from core.client_guard.marker import has_marker, strip_marker

META_KEY = "fv_frontend"
# the AI hosts the front end intercepts (a host and every subdomain of it); every other CONNECT is
# tunnelled without being decrypted. ``claude.ai`` (Claude Desktop) is NOT routed until it has an
# adapter: an unknown format in a Power BI chat is blocked, so routing it now would break Claude
# Desktop; its Power BI results stay masked by the MCP layer. The CA still permits it (no rotation
# needed when its adapter lands).
ROUTED_SUFFIXES = INTERCEPTED_DOMAINS
BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})
WORKERS = 8
_BINARY_TYPES = ("image/", "audio/", "video/", "application/octet-stream")
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
MAX_BODY_IDS = 64  # ids read from an unknown body when looking for a masked chat
# what ``decide`` settled for a flow
_PASS = "pass"  # not ours (host, handshake, unknown body of no masked chat): forwarded untouched
_BLOCK = "block"  # an unknown body of a masked chat
_FLOW = "flow"  # an adapter read it: ``info.mode`` says what happens

BLOCK_UNKNOWN_TEXT = (
    "Client gateway: this kind of request is not supported while masking is on in this chat, so it "
    "was NOT sent. Type: masking off (to send it unmasked, for dev or test data). Your data did not "
    "leave this machine."
)
BLOCK_FORMAT_TEXT = (
    "Client gateway: this app sent a request in a format masking cannot be applied to "
    "(unsupported_format), so it was NOT sent. The app may have been updated: update the Power BI "
    "MCP, or type: masking off (to send it unmasked, for dev or test data). Your data did not "
    "leave this machine."
)
BLOCK_ATTACHMENT_TEXT = (
    "Client gateway: an image or file was attached before masking could check it, so this message "
    "was NOT sent. Remove the attachment and attach it again (or type: masking off, for dev or test "
    "data). Your data did not leave this machine."
)
BLOCK_INTERNAL_TEXT = (
    "Client gateway: request not sent because of an internal error. Your data did not leave this "
    "machine."
)


def allow_hosts_pattern(suffixes: tuple[str, ...] = ROUTED_SUFFIXES) -> str:
    """The mitmproxy ``allow_hosts`` regex: the routed hosts and their subdomains, any port. It is
    matched (re.search, case-insensitive) against ``host:port`` strings."""
    names = "|".join(s.replace(".", r"\.") for s in suffixes)
    return rf"^(.+\.)?({names}):\d+$"


def is_routed_host(host: str, suffixes: tuple[str, ...] = ROUTED_SUFFIXES) -> bool:
    h = norm_host(host)
    return any(h == s or h.endswith("." + s) for s in suffixes)


_ENC_IDS = {"": 0, "identity": 0, "gzip": 1, "br": 2, "zstd": 3, "deflate": 4}


@dataclass(eq=False)
class FlowInfo:
    """What the request hook learned about a flow, read again by the response hooks."""

    mode: str
    adapter: Adapter | None = None
    route: Route | None = None
    ctx: MaskContext | None = None
    ws_masked: bool = False
    space: str = ""  # the chat space and chat key of the flow (a key the request names or implies)
    key: str | None = None


@dataclass(eq=False)
class _Plan:
    """What ``FrontendAddon.decide`` settled for one request (``apply`` carries it out)."""

    kind: str
    info: FlowInfo | None = None
    key: str | None = None
    space: str = ""
    plain: bytes = b""
    raw: bytes = b""
    enc: str | None = None
    marker_hit: bool = False
    pending_mode: str | None = None
    rewritten: bool = False  # ``plain`` is the body with a typed command swapped for NEUTRAL_TEXT
    text: str = ""
    code: str = ""

    @property
    def masked(self) -> bool:
        """A flow whose failure must block (a masked chat); never a passthrough flow."""
        if self.kind == _BLOCK:
            return True
        return bool(self.info is not None and self.info.mode in (MODE_ON, MODE_UNMASK))


class StreamBridge:
    """``flow.response.stream`` for a masked chat: mitmproxy calls it with every body chunk and with
    ``b""`` once at the end of the body (``HttpStream.state_stream_response_body``), where the
    rehydrator's held tail is flushed. A conversion error lets the masked bytes through (a pseudonym
    on screen, never a leak)."""

    def __init__(
        self,
        rehydrator: Any,
        ctx: MaskContext,
        state: GatewayState,
        log: Any = None,
        route: str | None = None,
    ) -> None:
        self._r = rehydrator
        self._ctx = ctx
        self._state = state
        self._log = log  # counts and the route label only, never content
        self._route = route
        self._chunks = 0
        self._done = False

    def __call__(self, chunk: bytes) -> bytes:
        if self._done:
            return chunk
        if chunk:
            self._chunks += 1
            try:
                out: bytes = self._r.feed(chunk)
                return out
            except Exception:  # noqa: BLE001 - never break the stream
                self._state.bump("errors")
                return chunk
        self._done = True
        try:
            tail: bytes = self._r.finish()
        except Exception:  # noqa: BLE001
            self._state.bump("errors")
            tail = b""
        stats = getattr(self._r, "stats", {})
        state = self._state
        state.bump("responses_rehydrated")
        if int(stats.get("events", 0)) > 0 and int(stats.get("recognised", 0)) == 0:
            state.bump("rehydration_unrecognised")  # R9: a reply shape the rehydrator never saw
        state.bump("tokens_rehydrated", int(stats.get("tokens", 0)))
        state.bump("errors", int(stats.get("errors", 0)))
        if self._log is not None:
            self._log.event(
                "frontend_stream_done",
                route=self._route,
                chunks=self._chunks,
                tokens=int(stats.get("tokens", 0)),
                errors=int(stats.get("errors", 0)),
                events=int(stats.get("events", 0)),
                recognised=int(stats.get("recognised", 0)),
            )
        try:
            self._ctx.finish()
        except Exception:  # noqa: BLE001
            pass
        return tail


class FrontendAddon:
    def __init__(
        self,
        state: GatewayState,
        log: GatewayLog | None = None,
        routed: tuple[str, ...] = ROUTED_SUFFIXES,
        upstream_proxy: Callable[[str, int, str], Any] | None = None,
    ) -> None:
        self.state = state
        self.log = log
        self.routed = routed  # tests route a loopback host; the product always uses the default
        # per-connection upstream proxy of the routed hosts (``routing.upstream.UpstreamProxy.via``):
        # the user's previous static proxy, so the company route is kept (R7)
        self.upstream_proxy = upstream_proxy
        self._pool: ThreadPoolExecutor | None = None
        self._pool_lock = threading.Lock()

    # ------------------------------------------------------------------ mitmproxy hooks
    async def request(self, flow: Any) -> None:
        await asyncio.get_running_loop().run_in_executor(self._executor(), self.safe_request, flow)

    def responseheaders(self, flow: Any) -> None:
        info = _info(flow)
        resp = flow.response
        if info is None or info.mode != MODE_ON or info.adapter is None or resp is None:
            return
        if info.ctx is None:
            return
        ctype = resp.headers.get("content-type", "")
        enc = resp.headers.get("content-encoding")
        if self.log is not None:  # labels and counts only: which masked replies are streams
            self.log.event(
                "frontend_response",
                route=flow.request.path,
                status=int(resp.status_code),
                sse=base_type(ctype) == "text/event-stream",
                json="json" in ctype.lower(),
                enc=_ENC_IDS.get((enc or "").strip().lower(), 9),
            )
        if base_type(ctype) != "text/event-stream":
            return  # buffered: the ``response`` hook converts a JSON body
        rehydrator = info.adapter.rehydrator(ctype, enc, info.ctx, info.route, reencode=True)
        resp.headers.pop("content-length", None)  # a stream: the length is not known up front
        resp.stream = StreamBridge(rehydrator, info.ctx, self.state, self.log, flow.request.path)

    def response(self, flow: Any) -> None:
        info = _info(flow)
        resp = flow.response
        if info is not None and resp is not None:
            self._note_upload(flow, info, resp)
        if info is None or info.mode != MODE_ON or info.adapter is None or resp is None:
            return
        if info.ctx is None or resp.stream:
            return
        ctype = resp.headers.get("content-type", "")
        raw = resp.raw_content
        if "json" not in ctype.lower() or not raw:
            return
        enc = resp.headers.get("content-encoding")
        out = info.adapter.unmask_response(raw, ctype, enc, info.ctx, info.route, reencode=True)
        if out != raw:
            self._set_raw(resp, out)
            self.state.bump("responses_rehydrated")

    def _note_upload(self, flow: Any, info: FlowInfo, resp: Any) -> None:
        """The reply of an upload registration (``POST /files``) that was attributed to a chat:
        remember ``file id -> chat`` (memory, 15 minutes) so the raw bytes that follow can be
        redacted when that chat is masked. Never breaks the response."""
        adapter = info.adapter
        reader = getattr(adapter, "response_file_ids", None)
        if reader is None or not info.key or info.route is None or info.route.method != "POST":
            return
        try:
            raw = resp.raw_content or b""
            if not raw or int(resp.status_code) >= 300:
                return
            body = decode_bytes(raw, resp.headers.get("content-encoding"))
            ids = tuple(reader(body))
            if ids:
                self.state.frontend_note_file(info.space, info.key, ids)
        except Exception:  # noqa: BLE001 - an unreadable reply is just an upload nobody remembers
            self._count("errors")

    def websocket_message(self, flow: Any) -> None:
        info = _info(flow)
        ws = getattr(flow, "websocket", None)
        if info is None or not info.ws_masked or ws is None or not ws.messages:
            return
        message = ws.messages[-1]
        if not message.from_client or message.dropped:
            return
        try:
            hits = self._frame_hits(message.content)
        except Exception:  # noqa: BLE001 - a frame that cannot be checked is not forwarded
            hits = 1
        if hits:
            message.drop()
            self.state.bump("frontend_ws_dropped")
            self._log("frontend_ws_dropped", hits=int(hits))

    async def done(self) -> None:
        with self._pool_lock:
            pool, self._pool = self._pool, None
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------------ request
    def _chain_upstream(self, flow: Any) -> None:
        """Send this connection to a routed host through the previous static proxy (when there is
        one and the host is not on its bypass list). Never breaks the request."""
        chooser = self.upstream_proxy
        if chooser is None:
            return
        try:
            req = flow.request
            if not is_routed_host(req.host, self.routed):
                return
            via = chooser(norm_host(req.host), int(req.port), str(req.scheme))
            if via is not None:
                flow.server_conn.via = via
        except Exception:  # noqa: BLE001 - the connection then goes direct, as without chaining
            self._count("errors")

    def safe_request(self, flow: Any) -> None:
        """``process_request`` with R2 first: an unexpected error never blocks a chat that is not
        masked. The mode is decided in its own guarded step; when that fails, only a request that
        carries this install's marker is blocked (it is a Power BI request whose mode is unknown) and
        everything else is forwarded untouched. After the mode is known, an error blocks a masked
        flow (``on`` / ``unmask``: nothing is sent upstream) and never a passthrough flow."""
        self._chain_upstream(flow)
        try:
            plan = self.decide(flow)
        except Exception:  # noqa: BLE001
            self._count("errors")
            self._decide_failed(flow)
            return
        try:
            self.apply(flow, plan)
        except Exception:  # noqa: BLE001
            self._count("errors")
            if plan.masked:
                self._block(flow, 400, BLOCK_INTERNAL_TEXT, "internal")

    def process_request(self, flow: Any) -> None:
        """Decide and apply, errors propagate (``safe_request`` is the guarded hook body)."""
        self.apply(flow, self.decide(flow))

    def decide(self, flow: Any) -> "_Plan":
        """Read the request and settle what happens to it. Changes nothing but the shared state
        (counters, a typed switch, the turn map) and ``flow.metadata``."""
        state = self.state
        req = flow.request
        state.bump("frontend_requests")
        host = norm_host(req.host)
        if not is_routed_host(host, self.routed):
            return _Plan(kind=_PASS)
        method = req.method.upper()
        path = req.path
        ctype = req.headers.get("content-type", "")
        turn = (req.headers.get("x-interaction-id", "") or "")[:128]
        referer = (req.headers.get("referer", "") or "")[:512]
        route = Route(method, host, path, ctype, turn, referer)
        enc = req.headers.get("content-encoding")
        raw = req.raw_content or b""
        readable = True
        plain = raw
        if raw:
            try:
                plain = decode_bytes(raw, enc)
            except Exception:  # noqa: BLE001 - an encoding this machine cannot read
                readable, plain = False, b""
        if req.headers.get("upgrade", "").lower() == "websocket":
            self._note_websocket(flow, route)  # a handshake has no body: frames are checked later
            return _Plan(kind=_PASS)
        marker = state.marker if state.pbi_chats_only else ""
        marker_hit = bool(marker) and readable and has_marker(plain, marker)
        adapter = adapter_for(method, host, path, ctype) if readable else None
        if adapter is None:
            if (
                method in BODY_METHODS
                and raw
                and self._unknown_belongs_to_masked_chat(route, plain, readable, marker_hit)
            ):
                return _Plan(kind=_BLOCK, text=BLOCK_UNKNOWN_TEXT, code="unsupported_body")
            return _Plan(kind=_PASS)

        space = chat_space_of(adapter)
        own = adapter.chat_key(plain, route) or adapter.url_chat_key(path)
        if not own:  # a raw upload names its chat through the file the registration attributed
            for file_id in _file_ids_in_url(adapter, path):
                own = state.frontend_file_chat(space, file_id)
                if own:
                    break
        key = own
        mapped = False
        if getattr(adapter, "uses_turn_ids", False) and turn:
            if own:
                state.frontend_note_turn(space, turn, own)
            else:  # a side call: it follows the chat of the main call of its turn
                key = state.frontend_turn_chat(space, turn)
                mapped = key is not None
        state.frontend_note_app(space, app_labels(space, req.headers))
        command = typed_command(adapter.newest_user_text(plain, route)) if plain else None
        draft = False
        if plain and not command:  # a half-typed draft that is exactly a command: kept off the wire
            draft_text = getattr(adapter, "draft_text", None)
            draft = bool(draft_text and typed_command(draft_text(plain, route)))
        rewritten = False
        if command or draft:
            neutral = adapter.replace_newest_user_text(plain, route, NEUTRAL_TEXT)
            if neutral == plain:  # not replaceable: fail closed, the command must not leave
                state.bump("errors")
                return _Plan(kind=_BLOCK, text=BLOCK_INTERNAL_TEXT, code="internal")
            plain, rewritten = neutral, True
        history = getattr(adapter, "replace_command_turns", None)
        if history is not None and plain:  # a command typed earlier is resent with the history
            older = history(plain, route, NEUTRAL_TEXT)
            if older != plain:
                plain, rewritten = older, True
        pending_mode: str | None = None
        if command:
            if key:
                state.frontend_set_mode(space, key, command)
            else:
                pending_mode = command  # a first turn: bound to the id the response reports
        mode = state.frontend_mode(key, marker_hit, space, pending_mode)
        info = FlowInfo(mode, adapter, route, space=space, key=key)
        flow.metadata[META_KEY] = info
        if self.log is not None:  # labels and counts only
            self.log.event(
                "frontend_request",
                route=path,
                masked=mode == MODE_ON,
                marker=marker_hit,
                keyed=bool(key),
                mapped=mapped,
            )
        if mapped:
            state.bump("frontend_turn_mapped")
        return _Plan(
            kind=_FLOW,
            info=info,
            key=key,
            space=space,
            plain=plain,
            raw=raw,
            enc=enc,
            marker_hit=marker_hit,
            pending_mode=pending_mode,
            rewritten=rewritten,
        )

    def apply(self, flow: Any, plan: "_Plan") -> None:
        """Carry out a decision: forward untouched, block with a fixed text, unmask or mask."""
        state = self.state
        if plan.kind == _BLOCK:
            self._block(flow, 400, plan.text, plan.code)
            return
        info = plan.info
        if plan.kind == _PASS or info is None or info.mode in (MODE_OFF, MODE_PASSTHROUGH):
            if plan.rewritten:  # forwarded as it is, except that the typed command is gone
                self._set_body(flow.request, plan.plain, plan.enc, plan.raw)
            state.bump("frontend_passthrough")
            return
        if info.mode == MODE_UNMASK:
            self._set_body(flow.request, self._unmask_body(plan.plain), plan.enc, plan.raw)
            state.bump("pbi_unmasked_requests")
            return
        self._mask(flow, plan)

    def _decide_failed(self, flow: Any) -> None:
        """The mode could not be decided. Marker in the raw body (plain or decoded): a Power BI
        request of unknown mode, blocked. Otherwise R2: forwarded untouched."""
        state = self.state
        marked = False
        try:
            marker = state.marker
            req = flow.request
            raw = req.raw_content or b""
            if marker and raw:
                marked = has_marker(raw, marker) or has_marker(
                    decode_bytes(raw, req.headers.get("content-encoding")), marker
                )
        except Exception:  # noqa: BLE001 - unreadable: it cannot be shown to hold the marker
            marked = False
        if marked:
            self._block(flow, 400, BLOCK_INTERNAL_TEXT, "internal")
            return
        self._count("frontend_passthrough")
        self._log("frontend_decide_failed")

    def _mask(self, flow: Any, plan: "_Plan") -> None:
        state = self.state
        info = plan.info
        assert info is not None
        adapter = info.adapter
        assert adapter is not None
        key, space = plan.key, plan.space
        req = flow.request
        sess = state.session(frontend_chat_id(space, key)) if key else None
        if sess is not None:
            memo, ner_degrade = sess.memo, sess.ner_degrade
        else:
            # no chat yet: a fresh memo, handed over when its id is known; a first turn that was
            # refused once before goes on with the dictionary (never refused forever)
            first = self._first_turn(space, create=False)
            memo, ner_degrade = RemaskMemo(), bool(first and first.ner_degrade)
        on_id: Callable[[str], None] | None = None
        if not key:

            def bind(chat_id: str) -> None:
                state.frontend_bind_chat(
                    space, chat_id, pbi=plan.marker_hit, mode=plan.pending_mode, memo=memo
                )

            on_id = bind

        ctx = MaskContext(state, memo, ner_degrade=ner_degrade, on_chat_id=on_id)
        info.ctx = ctx
        state.verify()  # a model opened since the last cycle closes the gate right here
        if not state.ready and not state.wait_ready():
            counter = sess or self._first_turn(space, create=True)
            if counter.ready_blocks >= 1:
                # refused once already: never a locked chat; mask with the dictionary as it is
                state.bump("not_ready_bypassed")
            else:
                counter.ready_blocks += 1
                state.bump("not_ready")
                code = str(state.not_ready_info().get("reason") or "dictionary_not_ready")
                self._block(flow, 400, _not_ready_message(state.not_ready_info()), code)
                return
        refs = _attachment_refs(adapter, plan.plain, info.route)
        if refs and state.frontend_files_unchecked(space, refs):
            state.bump("unchecked_attachment_blocked")  # fail closed: nobody looked at that file
            self._block(flow, 400, BLOCK_ATTACHMENT_TEXT, "attachment_unchecked")
            return
        try:
            out = adapter.mask_request(plan.plain, ctx, info.route)
            if state.pbi_chats_only:
                out = strip_marker(out, state.marker)  # the marker never leaves the machine
            self._scan(out, req.headers.get("content-type", ""))
        except MaskingFailed as exc:
            if exc.code == "residual_leak":
                state.bump("residual_leak_blocked")
            elif exc.code == "unsupported_format":
                state.bump("format_changed")
            text = BLOCK_FORMAT_TEXT if exc.code == "unsupported_format" else None
            # a first turn has no chat id yet: its refusals are counted per app, so a retried first
            # message is never refused forever either
            counter = sess or self._first_turn(space, create=True)
            if exc.code == "ner_incomplete" and counter.ner_refused():
                state.bump("ner_auto_degraded")  # never a locked chat: dictionary-only from now on
                text = NER_DEGRADED_TEXT.format(ner_state=self._ner_state())
            self._block(flow, 400, text or self._failed_text(exc.code, memo), exc.code)
            return
        except Exception:  # noqa: BLE001 - fail closed on ANY error
            self._block(flow, 400, self._failed_text("internal", memo), "internal")
            return
        if sess is not None:
            sess.ner_ok()
            if state.ready:
                sess.ready_blocks = 0
        else:  # a first turn went through: the next first turn starts strict again
            done = self._first_turn(space, create=False)
            if done is not None:
                done.ner_ok()
                done.ner_degrade = False
        self._set_body(req, out, plan.enc, plan.raw)
        if out != plan.plain and base_type(req.headers.get("content-type", "")).startswith(
            "image/"
        ):
            req.headers["content-type"] = "image/png"  # redacted bytes are a PNG
        uploaded = _file_ids_in_url(adapter, req.path)
        if uploaded and req.method.upper() == "PUT":
            state.frontend_file_checked(space, uploaded)  # redacted or found clean, while masked
        state.bump("frontend_masked")

    def _scan(self, body: bytes, ctype: str) -> None:
        """R9: nothing the adapter did not understand may carry a known client value out. Binary
        uploads are not text (their images went through the redactor)."""
        if base_type(ctype).startswith(_BINARY_TYPES):
            return
        if residual_hits(self.state.smap, body):
            raise MaskingFailed("residual_leak")

    def _unknown_belongs_to_masked_chat(
        self, route: Route, plain: bytes, readable: bool, marker_hit: bool
    ) -> bool:
        """Does THIS flow (no adapter reads it) belong to a masked chat? Only that makes an unknown
        body a reason to block (spec §11); nothing about OTHER chats does (R2)."""
        if marker_hit or self._masked_url(route.path):
            return True
        state = self.state
        for adapter in adapters():
            if not getattr(adapter, "uses_turn_ids", False):
                continue
            space = chat_space_of(adapter)
            if state.frontend_chat_masked(space, state.frontend_turn_chat(space, route.turn)):
                return True
            if (
                readable
                and plain
                and state.frontend_chat_masked(space, adapter.chat_key(plain, route))
            ):
                return True
        if readable and plain:
            for chat_id in _body_ids(plain):
                if any(state.frontend_chat_masked(chat_space_of(a), chat_id) for a in adapters()):
                    return True
        return False

    def _masked_url(self, path: str) -> bool:
        """Does the URL name a chat that is masked (any adapter's chat id in the path)?"""
        state = self.state
        return any(
            state.frontend_chat_masked(chat_space_of(a), a.url_chat_key(path)) for a in adapters()
        )

    def _note_websocket(self, flow: Any, route: Route) -> None:
        """A WebSocket handshake is forwarded as it is. Its frames are checked only when the URL names
        a masked chat or masking is on globally: a socket that cannot be attributed to a masked chat
        is never touched (R2)."""
        state = self.state
        masked = self._masked_url(route.path) or state.global_mode == "on"
        flow.metadata[META_KEY] = FlowInfo(MODE_PASSTHROUGH, ws_masked=masked)
        state.bump("frontend_passthrough")

    # ------------------------------------------------------------------ helpers
    def _executor(self) -> ThreadPoolExecutor:
        with self._pool_lock:
            if self._pool is None:
                self._pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="fv-mask")
            return self._pool

    def _unmask_body(self, plain: bytes) -> bytes:
        """A PBI chat a human switched off (R4): pseudonyms in the history become real values
        again and the marker is removed. A body that is not JSON, or cannot be converted, only
        loses the marker."""
        state = self.state
        try:
            out = dumps(state.smap.unmask_tree(json.loads(plain)))
        except ValueError:
            return strip_marker(plain, state.marker)
        except Exception:  # noqa: BLE001 - the tokens stay visible; nothing real was exposed
            state.bump("errors")
            return strip_marker(plain, state.marker)
        return strip_marker(out, state.marker)

    @staticmethod
    def _set_raw(message: Any, data: bytes) -> None:
        message.raw_content = data
        if "transfer-encoding" not in message.headers:
            message.headers["content-length"] = str(len(data))

    def _set_body(self, req: Any, out: bytes, enc: str | None, raw: bytes) -> None:
        """Put the converted body on the request in its original ``content-encoding``."""
        if not raw and not out:
            return
        data = encode_bytes(out, enc)
        req.raw_content = data
        req.headers.pop("transfer-encoding", None)  # the body is whole: a length, not chunks
        req.headers["content-length"] = str(len(data))

    def _first_turn(self, space: str, create: bool) -> Any:
        """The refusal counter of an app's first turns (no chat id yet); created only once a
        first turn was actually refused, so no chat is invented otherwise."""
        sid = frontend_chat_id(space, "_first_turn")
        if create:
            return self.state.session(sid)
        return self.state.peek_session(sid)

    def _ner_state(self) -> str:
        ner = getattr(self.state.detectors, "ner", None)
        return str(getattr(ner, "state", "not available")).replace("not_started", "not started")

    def _failed_text(self, code: str, memo: RemaskMemo) -> str:
        if code == "ner_incomplete":
            template = NER_INCOMPLETE_TEXT if memo.history_scanned else NER_HISTORY_TEXT
            return template.format(ner_state=self._ner_state())
        return MASK_FAILED_TEXT.format(code=code)

    def _block(self, flow: Any, status: int, message: str, code: str) -> None:
        """Answer the app locally with a fixed text; nothing is sent upstream and nothing of the
        request is echoed."""
        from mitmproxy import http  # lazy: importing client_gateway stays light

        body = json.dumps(
            {
                "error": {
                    "message": message,
                    "type": "invalid_request_error",
                    "code": "client_gateway_blocked",
                },
                "detail": message,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        flow.response = http.Response.make(
            status,
            body,
            {
                "content-type": "application/json",
                "x-should-retry": "false",
                "cache-control": "no-store",
            },
        )
        flow.metadata.pop(META_KEY, None)
        self.state.bump("frontend_blocked")
        self._log("frontend_blocked", code_id=_code_id(code), route=flow.request.path)

    def _frame_hits(self, content: bytes) -> int:
        """Identity values in an outgoing WebSocket frame (the text, and the strings of a JSON
        frame, so an escaped name is seen too)."""
        smap = self.state.smap
        text = content.decode("utf-8", "replace")
        hits = sum(smap.identity_hits(text).values())
        if not hits and text[:1] in "{[":
            try:
                strings = json_strings(json.loads(text))
            except (ValueError, RecursionError):
                strings = []
            hits = sum(smap.identity_hits("\n".join(strings)).values()) if strings else 0
        return hits

    def _count(self, name: str) -> None:
        """A counter on an error path: a failing counter must never turn into a failing request."""
        try:
            self.state.bump(name)
        except Exception:  # noqa: BLE001
            pass

    def _log(self, name: str, *, hits: int = 0, code_id: int = 0, route: str | None = None) -> None:
        if self.log is not None:
            self.log.event(name, route=route, hits=hits, code_id=code_id)


def _file_ids_in_url(adapter: Any, path: str) -> tuple[str, ...]:
    reader = getattr(adapter, "file_ids_in_url", None)
    return tuple(reader(path)) if reader is not None else ()


def _attachment_refs(adapter: Any, plain: bytes, route: Route | None) -> tuple[str, ...]:
    reader = getattr(adapter, "attachment_refs", None)
    return tuple(reader(plain, route)) if reader is not None and plain else ()


def _info(flow: Any) -> FlowInfo | None:
    info = flow.metadata.get(META_KEY)
    return info if isinstance(info, FlowInfo) else None


def _body_ids(plain: bytes) -> list[str]:
    """The distinct UUIDs a body carries (at most ``MAX_BODY_IDS``): ChatGPT names its chat in the
    body of a request no adapter reads."""
    text = plain.decode("utf-8", "replace")
    seen: dict[str, None] = {}
    for match in _UUID.finditer(text):
        seen.setdefault(match.group(0).lower(), None)
        if len(seen) >= MAX_BODY_IDS:
            break
    return list(seen)
