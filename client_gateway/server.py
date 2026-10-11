"""The gateway HTTP server: Claude Code <-> this process <-> https://api.anthropic.com.

Claude Code is pointed at ``http://127.0.0.1:<port>`` (``ANTHROPIC_BASE_URL``). Per request:

* ``HEAD /api/hello`` -> 200 (reachability probe, answered locally);
* ``/__gateway/*`` -> control endpoints (loopback ``Host`` only, see ``_control``);
* everything else is forwarded to the upstream (``/v1/messages``, ``/v1/messages/count_tokens`` and
  any other path): the body is MASKED first (``masking.mask_request``) while the session's mode is
  ``on``, and the response is REHYDRATED on the way back (SSE through ``SSERehydrator``, JSON
  through ``unmask_tree`` with memo recording). Mode ``off`` forwards the request bytes unchanged.

PBI-chat rule (``client_gateway.pbi_chats_only``, default on; ``GatewayState.request_mode``): a
request is masked only when it belongs to a Power BI chat (its body carries this install's marker,
or the chat did before); every other request is forwarded byte-identical, never blocked and never
rehydrated. A PBI chat a human switched off gets its pseudonyms turned back into real values; the
marker is stripped from everything that leaves.

Fail closed, per request: a request that cannot be masked safely is answered with HTTP 400 and an
Anthropic-format error (``x-should-retry: false``) and nothing is sent upstream; the next request is
handled on its own. An unreachable upstream is a 502 Anthropic-format error.

All request headers are forwarded verbatim (``anthropic-beta``, ``anthropic-version``,
``authorization``, ``x-api-key``, ...) except hop-by-hop headers, ``host``, ``content-length``,
``expect`` and ``accept-encoding`` (``identity`` is sent instead, so streamed bodies reach this
process uncompressed). Upstream status, error bodies and headers come back unchanged except
``content-length`` / ``transfer-encoding`` / ``content-encoding`` / ``connection``.

Nothing here logs or returns a value: logs hold counts and fixed labels, status holds counts.
"""

from __future__ import annotations

import base64
import binascii
import json
import socket
import socketserver
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from client_gateway import __version__
from client_gateway.defaults import DEFAULT_PORT
from client_gateway.images import redact_image_bytes
from client_gateway.log import GatewayLog
from client_gateway.masking import (
    MaskingFailed,
    client_values_in_last_message,
    dumps,
    mask_request,
    mask_strings,
)
from client_gateway.provider import Provider
from client_gateway.sse import SSERehydrator
from client_gateway.state import (
    MODE_PASSTHROUGH,
    MODE_UNMASK,
    SESSION_HEADER,
    TOKEN_HEADER,
    GatewayState,
    ModeDenied,
    SessionState,
    cfg,
)
from core.client_guard.marker import strip_marker

DEFAULT_UPSTREAM = "https://api.anthropic.com"
MAX_BODY_BYTES = 64 * 1024 * 1024  # the API itself accepts 32 MB
MAX_CONTROL_BYTES = 1024 * 1024
# a query result or a screenshot, for the authority routes only
MAX_AUTHORITY_BYTES = 64 * 1024 * 1024
DRAIN_MAX_BYTES = 1024 * 1024  # a refused body up to this size is read off the connection
AUTHORITY_WAIT_S = 30.0
REFRESH_WAIT_S = 100.0
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
# the masking authority: the MCP masks its results and unmasks its arguments through these
AUTHORITY_ROUTES = frozenset(
    {
        ("POST", "/__gateway/mask"),
        ("POST", "/__gateway/unmask"),
        ("POST", "/__gateway/mask_image"),
    }
)
# the Power BI MCP registers its process with the gateway's lifecycle (routing on): token only
LIFECYCLE_ROUTES = frozenset({("POST", "/__gateway/attach"), ("POST", "/__gateway/detach")})
PAC_PATH = "/proxy.pac"  # the PAC file of the routing (no token: the apps fetch it themselves)
TOKEN_ROUTES = (
    frozenset(
        {
            ("GET", "/__gateway/status"),
            ("GET", "/__gateway/session"),
            ("POST", "/__gateway/refresh"),
        }
    )
    | AUTHORITY_ROUTES
    | LIFECYCLE_ROUTES
)

_HOP_BY_HOP = frozenset(
    {
        "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
        "proxy-connection", "te", "trailer", "trailers", "transfer-encoding", "upgrade",
    }
)  # fmt: skip
_DROP_REQUEST = _HOP_BY_HOP | {"host", "content-length", "accept-encoding", "expect"}
_DROP_RESPONSE = _HOP_BY_HOP | {"content-length", "content-encoding"}
_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})

UNMASKED_GUARD_TEXT = (
    "Client gateway: this message looks like it holds client data ({n} x {kinds}; IMG = a "
    "screenshot) and masking is off in this chat, so it was NOT sent. Type: masking on (to work "
    "with client data), or masking off (to send it unmasked, for dev or test data). Your data did "
    "not leave this machine."
)
MASK_FAILED_TEXT = (
    "Client gateway: request not sent because client data could not be masked safely ({code}). "
    "Your data did not leave this machine."
)
NER_INCOMPLETE_TEXT = (
    "Client gateway: request not sent, the name recognition could not check all new text in "
    "time (it is {ner_state}, or the message is very long). Nothing was sent unchecked. Retry in "
    "a moment (what was already checked is kept) or split a very long paste. If it keeps failing, "
    "type 'masking dictionary' to mask with the client dictionary only (names no model holds "
    "then reach Anthropic as typed), or 'masking off' to send without masking. Your data did not "
    "leave this machine."
)
NER_HISTORY_TEXT = (
    "Client gateway: this chat is being checked for client names for the first time since masking "
    "was switched on or the gateway started (the whole earlier conversation is read once; it is "
    "{ner_state}). Nothing was sent. Send the message again: what was already checked is kept and "
    "the rest continues. Your data did not leave this machine."
)
# the owner's rule (2026-10-07): the gateway may refuse ONE message, never lock a chat
NER_DEGRADED_TEXT = (
    "Client gateway: this message was not sent - the name recognition could not check it in time "
    "(it is {ner_state}). Your data did not leave this machine. This chat now masks with the "
    "client dictionary only, so your next message goes through (every known client value is still "
    "masked; a name no model or dictionary holds is sent as typed). Type 'masking off' to send "
    "without masking, or 'masking on' to use name recognition again."
)
NOT_READY_TEXT = (
    "Client gateway: request not sent, the client dictionary is not ready ({code}). {hint} "
    "Your data did not leave this machine."
)
NOT_READY_HINTS = {
    "dictionary_empty": (
        "No client model has been read yet (none is open in Power BI Desktop), so there is "
        "nothing to mask with. Open the client model in Desktop and retry in a minute, or type "
        "'masking off' to send without masking."
    ),
    "dictionary_incomplete": (
        "{not_ready} of {total} open model(s) could not be read completely ({classes}). Close or "
        "fix that model and retry (the dictionary is re-read when you retry), or type "
        "'masking off' to send without masking."
    ),
}
NOT_READY_FALLBACK = (
    "The dictionary is being read. Retry in a moment, or type 'masking off' to send without "
    "masking."
)
UPSTREAM_DOWN_TEXT = (
    "Client gateway: could not reach the Anthropic API ({code}). " "Nothing was lost; try again."
)


_RETRYABLE = (
    httpx.ReadError,
    httpx.WriteError,
    httpx.RemoteProtocolError,
    httpx.ConnectError,
    httpx.PoolTimeout,
)


def _error_body(kind: str, message: str) -> bytes:
    return json.dumps(
        {"type": "error", "error": {"type": kind, "message": message}}, separators=(",", ":")
    ).encode("utf-8")


def host_is_local(value: str | None) -> bool:
    """True when a ``Host`` header names this machine (stops DNS rebinding of the control API)."""
    if not value:
        return False
    host = value.strip()
    if host.startswith("["):
        host = host[1 : host.find("]")]  # noqa: E203
    elif host.count(":") == 1:
        host = host.split(":", 1)[0]
    return host.lower() in LOCAL_HOSTS


def authority_wait_s() -> float:
    """How long an authority call waits for the dictionary (``client_gateway.authority_wait_s``,
    default 30 s; 0 = do not wait). A value that is not a non-negative number gives the default."""
    try:
        value = float(cfg("client_gateway.authority_wait_s", AUTHORITY_WAIT_S))
    except (TypeError, ValueError):
        return AUTHORITY_WAIT_S
    return value if value >= 0 else AUTHORITY_WAIT_S


class _Server(ThreadingHTTPServer):
    """Exclusive bind (a second process must not share the port) and no reverse DNS at bind."""

    allow_reuse_address = False
    daemon_threads = True
    request_queue_size = 128
    gateway: Gateway

    def server_bind(self) -> None:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            self.socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        socketserver.TCPServer.server_bind(self)  # HTTPServer.server_bind does getfqdn(): slow
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = port


class _TooLarge(Exception):
    pass


class _BadBody(Exception):
    pass


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 900

    server: _Server

    # ------------------------------------------------------------------ plumbing
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        pass

    def log_error(self, format: str, *args: Any) -> None:  # noqa: A002
        pass

    def handle(self) -> None:
        try:
            super().handle()
        except (OSError, ValueError):  # reset, broken pipe, timeout: the connection is gone
            self.close_connection = True

    @property
    def gw(self) -> Gateway:
        return self.server.gateway

    def do_GET(self) -> None:
        self._dispatch()

    do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = do_GET

    def _dispatch(self) -> None:
        self._started = False
        try:
            path = urlsplit(self.path).path
            if path.startswith("/__gateway/"):
                self._control(path)
            elif path == "/api/hello" and self.command in ("HEAD", "GET"):
                self._json(200, {"ok": True} if self.command == "GET" else None)
            elif path == PAC_PATH and self.command in ("HEAD", "GET"):
                self._pac()
            else:
                gw = self.gw
                with gw.flight_lock:
                    gw.in_flight += 1
                    gw.last_request = time.monotonic()
                try:
                    self._proxy()
                finally:
                    with gw.flight_lock:
                        gw.in_flight -= 1
                        gw.last_request = time.monotonic()
        except OSError:
            self.close_connection = True
        except Exception as exc:  # noqa: BLE001 - one request must never take the server down
            self.gw.state.bump("errors")
            self.gw.log.event("handler_error", error_type=type(exc).__name__)
            self.close_connection = True
            if not self._started:
                try:
                    self._send_error(500, "api_error", "Client gateway: internal error.")
                except OSError:
                    pass

    def _pac(self) -> None:
        """The routing's PAC file while routing is on (404 otherwise)."""
        state = self.gw.state
        provider = state.pac_provider
        if not host_is_local(self.headers.get("Host")) or provider is None:
            self._json(404, {"ok": False, "error": "not_found"})
            return
        try:
            body = provider().encode("utf-8")
        except Exception:  # noqa: BLE001 - never a stack trace to an app
            state.bump("errors")
            self._json(500, {"ok": False, "error": "internal"})
            return
        state.bump("pac_served")
        headers = [
            ("Content-Type", "application/x-ns-proxy-autoconfig"),
            ("Cache-Control", "no-cache, no-store, must-revalidate"),
            ("Pragma", "no-cache"),
            ("Content-Length", str(len(body))),
        ]
        self._head(200, headers)
        if self.command != "HEAD":
            self.wfile.write(body)

    # ------------------------------------------------------------------ writing
    def _head(self, status: int, headers: list[tuple[str, str]], reason: str | None = None) -> None:
        self._started = True
        self.send_response_only(status, reason)
        for key, value in headers:
            self.send_header(key, value)
        self.end_headers()

    def _json(self, status: int, payload: dict[str, Any] | None) -> None:
        body = b"" if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        headers = [("Content-Type", "application/json"), ("Content-Length", str(len(body)))]
        self._head(status, headers)
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _send_error(
        self, status: int, kind: str, message: str, extra: list[tuple[str, str]] | None = None
    ) -> None:
        body = _error_body(kind, message)
        headers = [("Content-Type", "application/json"), ("Content-Length", str(len(body)))]
        self._head(status, headers + (extra or []))
        if self.command != "HEAD":
            self.wfile.write(body)

    # ------------------------------------------------------------------ reading
    def _read_body(self) -> bytes:
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            try:
                return self._read_chunked(MAX_BODY_BYTES)
            except ValueError:
                raise _BadBody from None
        raw = self.headers.get("Content-Length")
        if not raw:
            return b""
        try:
            length = int(raw)
        except ValueError:
            raise _BadBody from None
        if length < 0 or length > MAX_BODY_BYTES:
            raise _TooLarge
        return self.rfile.read(length) if length else b""

    def _read_chunked(self, limit: int) -> bytes:
        parts: list[bytes] = []
        total = 0
        while True:
            line = self.rfile.readline(1024)
            size = int(line.split(b";", 1)[0].strip() or b"0", 16)
            if size == 0:
                while self.rfile.readline(1024) not in (b"\r\n", b"\n", b""):
                    pass  # trailers
                return b"".join(parts)
            total += size
            if total > limit:
                raise _TooLarge
            parts.append(self.rfile.read(size))
            self.rfile.readline(8)  # the CRLF after the chunk

    # ------------------------------------------------------------------ control
    def _control(self, path: str) -> None:
        gw = self.gw
        state = gw.state
        if not host_is_local(self.headers.get("Host")):
            self._json(403, {"ok": False, "error": "forbidden_host"})
            return
        split = urlsplit(self.path)
        post = self.command == "POST"
        payload: dict[str, Any] = {}
        route = (self.command, path)
        authority = route in AUTHORITY_ROUTES
        if authority and not state.check_token(self.headers.get(TOKEN_HEADER)):
            # the body (a query result, a screenshot: up to 64 MiB) is not read for a caller
            # without the token
            self._skip_body()
            self._json(403, {"ok": False, "error": "token_required"})
            return
        if post:
            if "json" not in self.headers.get("Content-Type", "").lower():
                self._skip_body()
                self._json(415, {"ok": False, "error": "json_required"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self.close_connection = True
                self._json(400, {"ok": False, "error": "bad_request"})
                return
            if length < 0 or length > (MAX_AUTHORITY_BYTES if authority else MAX_CONTROL_BYTES):
                self._skip_body()
                self._json(400, {"ok": False, "error": "bad_request"})
                return
            try:
                data = json.loads(self.rfile.read(length) or b"{}") if length else {}
            except ValueError:
                self._json(400, {"ok": False, "error": "bad_request"})
                return
            if not isinstance(data, dict):
                self._json(400, {"ok": False, "error": "bad_request"})
                return
            payload = data
        if route in TOKEN_ROUTES and not state.check_token(self.headers.get(TOKEN_HEADER)):
            # status (counts, session id prefixes), a session's mode and a refresh (a heavy
            # Desktop re-detection) are for the user's own tools, which read the control token;
            # any other local process only gets /health
            self._json(403, {"ok": False, "error": "token_required"})
            return
        if route == ("GET", "/__gateway/health"):
            self._json(
                200,
                {
                    "ok": True,
                    "version": __version__,
                    "pid": gw.pid,
                    "port": gw.port,
                    "mode": state.global_mode,
                    "ready": state.ready,
                },
            )
        elif route == ("GET", "/__gateway/status"):
            self._json(200, {**state.status(), "port": gw.port, "upstream": gw.upstream_host})
        elif route == ("GET", "/__gateway/session"):
            ids = parse_qs(split.query).get("id", [""])
            sid = ids[0].strip()
            if sid:
                self._json(200, {"ok": True, "session": sid, "mode": state.peek_mode(sid)})
            else:
                self._json(200, {"ok": True, "session": "", "mode": state.global_mode})
        elif route == ("POST", "/__gateway/mode"):
            self._set_mode(payload)
        elif route == ("POST", "/__gateway/refresh"):
            waited = payload.get("wait", True) is not False
            started = state.request_refresh(REFRESH_WAIT_S if waited else 0.0)
            self._json(200, {"ok": True, "provider": started, "ready": state.ready})
        elif route in LIFECYCLE_ROUTES:
            self._lifecycle(path, payload)
        elif route == ("POST", "/__gateway/shutdown"):
            if not state.check_token(self.headers.get(TOKEN_HEADER)):
                self._json(403, {"ok": False, "error": "token_required"})
                return
            self._json(200, {"ok": True})
            self.close_connection = True
            threading.Thread(target=gw.shutdown, name="gateway-shutdown", daemon=True).start()
        elif authority:
            self._authority(path, payload)
        else:
            self._json(404, {"ok": False, "error": "not_found"})

    def _lifecycle(self, path: str, payload: dict[str, Any]) -> None:
        """A Power BI MCP process registers itself (attach) or leaves (detach). A gateway without a
        lifecycle (routing off) answers 409: nothing watches processes there."""
        life = self.gw.lifecycle
        if life is None:
            self._json(409, {"ok": False, "error": "routing_off"})
            return
        pid = payload.get("pid")
        if path.endswith("/attach"):
            result = life.attach(pid)
            if result.get("ok"):
                self.gw.state.bump("mcp_attached")
        else:
            result = life.detach(pid)
        self._json(200 if result.get("ok") else 400, result)

    def _skip_body(self) -> None:
        """A request that is answered without reading its body: read a small one off the
        connection (so a keep-alive connection stays usable), close the connection for a large
        or unframed one."""
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            self.close_connection = True
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.close_connection = True
            return
        if length <= 0:
            return
        if length > DRAIN_MAX_BYTES:
            self.close_connection = True
            return
        self.rfile.read(length)

    # ------------------------------------------------------------------ masking authority
    def _authority(self, path: str, payload: dict[str, Any]) -> None:
        """``mask`` / ``unmask`` / ``mask_image`` for the MCP (token already checked). The shared
        ``SessionMap`` is the one place pseudonyms are made, so the MCP and the conversation agree.
        Fails closed: a 503 carries no text and no partial list, only a fixed error label."""
        state = self.gw.state
        name = path.rsplit("/", 1)[-1]
        if name == "mask_image":
            png_b64 = payload.get("png_b64")
            if not isinstance(png_b64, str) or not png_b64:
                self._json(400, {"ok": False, "error": "bad_request"})
                return
            try:
                data = base64.b64decode(png_b64, validate=True)
            except (binascii.Error, ValueError):
                self._json(400, {"ok": False, "error": "bad_request"})
                return
            state.bump("authority_mask_calls")
            self._mask_image(png_b64, data)
            return
        texts = payload.get("texts")
        if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
            self._json(400, {"ok": False, "error": "bad_request"})
            return
        if name == "unmask":
            self._unmask(texts)
            return
        whole = payload.get("whole", True)
        if not isinstance(whole, bool):
            self._json(400, {"ok": False, "error": "bad_request"})
            return
        state.bump("authority_mask_calls")
        if not self._authority_ready(path):
            return
        try:
            masked, count = mask_strings(texts, state, whole)
        except Exception:  # noqa: BLE001 - fail closed on ANY error, nothing partial is returned
            self._authority_failed(path, "mask_failed")
            return
        self._json(200, {"ok": True, "texts": masked, "masked": count})

    def _unmask(self, texts: list[str]) -> None:
        state = self.gw.state
        state.bump("authority_unmask_calls")  # no readiness wait: unknown tokens just stay
        try:
            pairs = [state.smap.unmask_text(t) for t in texts]
        except Exception:  # noqa: BLE001
            self._authority_failed("/__gateway/unmask", "unmask_failed")
            return
        self._json(
            200,
            {"ok": True, "texts": [p[0] for p in pairs], "unmasked": sum(p[1] for p in pairs)},
        )

    def _mask_image(self, png_b64: str, data: bytes) -> None:
        path = "/__gateway/mask_image"
        state = self.gw.state
        if not self._authority_ready(path):
            return
        policy = state.images_policy
        if policy == "pass":  # the user's explicit choice (client_gateway.images)
            self._json(200, {"ok": True, "png_b64": png_b64, "action": "clean"})
            return
        if policy == "withhold":
            self._authority_failed(path, "mask_failed", reason="policy")
            return
        try:
            result = redact_image_bytes(data, state.smap)
        except Exception as exc:  # noqa: BLE001 - OCR unavailable or failed: the caller withholds
            reason = getattr(exc, "reason", None)
            extra = {"reason": reason} if isinstance(reason, str) else {}
            self._authority_failed(path, "mask_failed", **extra)
            return
        if result.data is None:
            self._json(200, {"ok": True, "png_b64": png_b64, "action": "clean"})
            return
        self._json(
            200,
            {
                "ok": True,
                "png_b64": base64.b64encode(result.data).decode("ascii"),
                "action": "redacted",
            },
        )

    def _authority_ready(self, path: str) -> bool:
        """Wait (bounded) for the dictionary like a masked request does; False = 503 sent."""
        state = self.gw.state
        state.verify()
        if state.ready or state.wait_ready(authority_wait_s()):
            return True
        self._authority_failed(path, "not_ready")
        return False

    def _authority_failed(self, path: str, error: str, **extra: str) -> None:
        self.gw.state.bump("authority_failures")
        self.gw.log.event("authority_failed", route=path, error_type=error)
        self._json(503, {"ok": False, "error": error, **extra})

    def _set_mode(self, payload: dict[str, Any]) -> None:
        session = payload.get("session", "")
        mode = payload.get("mode")
        if not isinstance(session, str) or len(session) > 200 or not isinstance(mode, str):
            self._json(400, {"ok": False, "error": "bad_request"})
            return
        degrade = payload.get("ner") == "degrade"  # dictionary-only masking (needs the token)
        try:
            result = self.gw.state.set_mode(
                session, mode, self.headers.get(TOKEN_HEADER), ner_degrade=degrade
            )
        except ModeDenied:
            self._json(403, {"ok": False, "error": "token_required"})
        except ValueError:
            self._json(400, {"ok": False, "error": "bad_mode"})
        else:
            self.gw.log.event("mode_switch", on=result["mode"] == "on", glob=not session)
            self._json(200, {"ok": True, **result})

    # ------------------------------------------------------------------ proxy
    def _proxy(self) -> None:
        gw = self.gw
        state = gw.state
        started = time.perf_counter()
        sid = self.headers.get(SESSION_HEADER) or ""
        sess = state.session(sid)
        sess.requests += 1
        state.bump("requests")
        if not sid.strip():
            state.bump("headerless_requests")
        try:
            body = self._read_body()
        except (_TooLarge, _BadBody) as exc:
            self.close_connection = True
            if isinstance(exc, _TooLarge):
                self._send_error(413, "request_too_large", "Client gateway: body too large.")
            else:
                self._send_error(400, "invalid_request_error", "Client gateway: bad body.")
            return
        mode = state.request_mode(sid, body)
        if (
            mode == "off"
            and sess.override is None
            and state.guard_unmasked
            and not state.pbi_chats_only  # R6: under the PBI-chat rule nothing is ever blocked
        ):
            state.notice()  # keeps the known values current for the safety net (background)
            # masking is off only because nobody switched it on: client names in the newest
            # message stop this one request with a hint; "masking off" (explicit) lets it pass
            found = client_values_in_last_message(body, self.headers.get("Content-Type", ""), state)
            if found:
                state.bump("unmasked_guard")
                gw.log.event("unmasked_guard", values=sum(found.values()))
                self._send_error(
                    400,
                    "invalid_request_error",
                    UNMASKED_GUARD_TEXT.format(
                        n=sum(found.values()), kinds=", ".join(sorted(found))
                    ),
                    [("x-should-retry", "false")],
                )
                return
        if mode in ("off", MODE_PASSTHROUGH):
            out = body
            state.bump("passthrough_requests")
        elif mode == MODE_UNMASK:
            out = self._unmasked_body(body)
            state.bump("pbi_unmasked_requests")
        else:
            code = None
            message = ""
            out = b""
            state.verify()  # a model opened since the last cycle closes the gate right here
            if not state.ready and not state.wait_ready():
                if sess.ready_blocks >= 1:
                    # refused once already: never a locked chat. Mask with what the dictionary
                    # holds now (the remembered values of earlier runs included)
                    state.bump("not_ready_bypassed")
                else:
                    sess.ready_blocks += 1
                    code = state.not_ready_info().get("reason") or "dictionary_not_ready"
                    message = _not_ready_message(state.not_ready_info())
                    state.bump("not_ready")
            if code is None:
                try:
                    out = mask_request(
                        body,
                        self.headers.get("Content-Type", ""),
                        state,
                        sess.memo,
                        ner_degrade=sess.ner_degrade,
                    )
                except MaskingFailed as exc:
                    code = exc.code
                except Exception:  # noqa: BLE001 - fail closed on ANY error
                    code = "internal"
            if code is not None:
                state.bump("blocked")
                gw.log.event("blocked", route=self.path, code_id=_code_id(code))
                if code == "ner_incomplete":
                    ner = getattr(state.detectors, "ner", None)
                    ner_state = str(getattr(ner, "state", "not available")).replace(
                        "not_started", "not started"
                    )
                    template = (
                        NER_INCOMPLETE_TEXT if sess.memo.history_scanned else NER_HISTORY_TEXT
                    )
                    if sess.ner_refused():  # never a locked chat: dictionary-only from now on
                        state.bump("ner_auto_degraded")
                        template = NER_DEGRADED_TEXT
                    message = message or template.format(ner_state=ner_state)
                message = message or MASK_FAILED_TEXT.format(code=code)
                retry = [("x-should-retry", "false")]
                self._send_error(400, "invalid_request_error", message, retry)
                return
            sess.ner_ok()
            if state.ready:
                sess.ready_blocks = 0
            if state.pbi_chats_only:
                # the marker only tells the gateway which chat this is: it never leaves the machine
                out = strip_marker(out, state.marker)
            state.bump("masked_requests")
        masked_ms = int((time.perf_counter() - started) * 1000)
        status = self._forward(out, mode == "on", sess)
        gw.log.event(
            "request",
            route=self.path,
            masked=mode == "on",
            status=status,
            bytes_in=len(body),
            bytes_out=len(out),
            mask_ms=masked_ms,
            total_ms=int((time.perf_counter() - started) * 1000),
        )

    def _unmasked_body(self, body: bytes) -> bytes:
        """A PBI chat a human switched off (R4): the MCP still masks its results, so the pseudonyms
        in the history become real values again (the user asked for unmasked), and the marker is
        removed. A body that is not JSON, or that cannot be converted, only loses the marker."""
        state = self.gw.state
        try:
            out = dumps(state.smap.unmask_tree(json.loads(body)))
        except ValueError:  # not JSON
            return strip_marker(body, state.marker)
        except Exception:  # noqa: BLE001 - the tokens stay visible; nothing real was exposed
            state.bump("errors")
            return strip_marker(body, state.marker)
        return strip_marker(out, state.marker)

    def _forward(self, body: bytes, rehydrate: bool, sess: SessionState) -> int:
        gw = self.gw
        headers = [(k, v) for k, v in self.headers.items() if k.lower() not in _DROP_REQUEST]
        headers.append(("accept-encoding", "identity"))
        content = body if (body or self.command in _BODY_METHODS) else None
        try:
            response = self._send_upstream(headers, content)
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            gw.state.bump("upstream_errors")
            gw.log.event("upstream_error", error_type=type(exc).__name__)
            self._send_error(502, "api_error", UPSTREAM_DOWN_TEXT.format(code=type(exc).__name__))
            return 502
        try:
            ctype = response.headers.get("content-type", "").lower()
            if "text/event-stream" in ctype and self.command != "HEAD":
                self._stream(response, rehydrate, sess)
            else:
                self._buffered(response, rehydrate, sess, ctype)
        finally:
            response.close()
        return response.status_code

    def _send_upstream(self, headers: list[tuple[str, str]], content: Any) -> httpx.Response:
        """Send upstream; ONE immediate retry when the connection failed before any response
        (a pooled keep-alive connection the far end had already closed: ``ReadError`` within a
        second, about 1 in 40 requests). Nothing was answered, so the retry cannot duplicate a
        response the client saw."""
        gw = self.gw
        for attempt in (1, 2):
            request = gw.client.build_request(
                self.command, gw.upstream + self.path, headers=headers, content=content
            )
            try:
                return gw.client.send(request, stream=True)
            except _RETRYABLE as exc:
                if attempt == 2:
                    raise
                gw.state.bump("upstream_retries")
                gw.log.event("upstream_retry", error_type=type(exc).__name__)
        raise RuntimeError("unreachable")  # pragma: no cover

    def _response_headers(self, response: httpx.Response) -> list[tuple[str, str]]:
        items = response.headers.multi_items()
        return [(k, v) for k, v in items if k.lower() not in _DROP_RESPONSE]

    def _buffered(
        self, response: httpx.Response, rehydrate: bool, sess: SessionState, ctype: str
    ) -> None:
        gw = self.gw
        try:
            data = response.read()
        except httpx.HTTPError as exc:
            gw.state.bump("upstream_errors")
            gw.log.event("upstream_error", error_type=type(exc).__name__)
            if not self._started:
                self._send_error(
                    502, "api_error", UPSTREAM_DOWN_TEXT.format(code=type(exc).__name__)
                )
            return
        if rehydrate and "json" in ctype and data and self.command != "HEAD":
            data = self._rehydrate_json(data, sess, response.status_code < 400)
        headers = self._response_headers(response)
        status = response.status_code
        no_body = self.command == "HEAD" or status in (204, 304) or status < 200
        if self.command == "HEAD":
            if response.headers.get("content-length"):
                headers.append(("Content-Length", response.headers["content-length"]))
        elif not no_body:
            headers.append(("Content-Length", str(len(data))))
        self._head(status, headers, response.reason_phrase)
        if data and not no_body:
            self.wfile.write(data)

    def _rehydrate_json(self, data: bytes, sess: SessionState, ok: bool) -> bytes:
        state = self.gw.state
        hits = 0

        def record(masked: str, unmasked: str) -> None:
            nonlocal hits
            hits += 1
            if ok:
                sess.memo.remember(masked, unmasked)

        try:
            tree = json.loads(data)
            out = dumps(state.smap.unmask_tree(tree, on_string=record))
        except Exception:  # noqa: BLE001 - tokens stay visible; nothing real was exposed
            state.bump("errors")
            return data
        state.bump("responses_rehydrated")
        state.bump("tokens_rehydrated", hits)
        return out

    def _stream(self, response: httpx.Response, rehydrate: bool, sess: SessionState) -> None:
        gw = self.gw
        chunked = self.request_version != "HTTP/1.0"
        rehydrator = SSERehydrator(gw.state.smap, sess.memo) if rehydrate else None
        headers = self._response_headers(response)
        if chunked:
            headers.append(("Transfer-Encoding", "chunked"))
        else:
            headers.append(("Connection", "close"))
            self.close_connection = True
        self._head(response.status_code, headers, response.reason_phrase)

        def emit(data: bytes) -> None:
            if data:
                self.wfile.write(b"%x\r\n%b\r\n" % (len(data), data) if chunked else data)

        try:
            for chunk in response.iter_bytes():
                emit(rehydrator.feed(chunk) if rehydrator else chunk)
        except httpx.HTTPError as exc:
            gw.state.bump("upstream_errors")
            gw.log.event("upstream_error", error_type=type(exc).__name__)
            frame = b"event: error\ndata: " + _error_body(
                "api_error", UPSTREAM_DOWN_TEXT.format(code=type(exc).__name__)
            )
            emit(frame + b"\n\n")
        if rehydrator is not None:
            emit(rehydrator.finish())
            gw.state.bump("responses_rehydrated")
            gw.state.bump("tokens_rehydrated", rehydrator.stats["tokens"])
            gw.state.bump("errors", rehydrator.stats["errors"])
        if chunked:
            self.wfile.write(b"0\r\n\r\n")


def _not_ready_message(info: dict[str, Any]) -> str:
    """The user-facing text for a closed gate: counts and fixed labels only."""
    code = str(info.get("reason") or "dictionary_not_ready")
    template = NOT_READY_HINTS.get(code)
    try:
        hint = (
            template.format(
                total=info.get("total", 0),
                not_ready=info.get("not_ready", 0),
                classes=", ".join(str(c) for c in info.get("classes") or ["unknown"]),
            )
            if template
            else NOT_READY_FALLBACK
        )
    except (KeyError, IndexError, ValueError):
        hint = NOT_READY_FALLBACK
    return NOT_READY_TEXT.format(code=code, hint=hint)


def _code_id(code: str) -> int:
    """Stable small integer for a masking code (the log holds numbers only)."""
    from client_gateway.masking import CODES

    return CODES.index(code) if code in CODES else -1


def upstream_allowed(url: str) -> bool:
    """The upstream must be https (every request carries the API key and, masked or not, the
    conversation); plain http only to this machine (tests, a local proxy)."""
    parts = urlsplit(url)
    return parts.scheme == "https" or (
        parts.scheme == "http" and (parts.hostname or "") in LOCAL_HOSTS
    )


class Gateway:
    """The server plus everything it owns (state, upstream client, optional provider)."""

    def __init__(
        self,
        state: GatewayState,
        *,
        host: str = "127.0.0.1",
        port: int = DEFAULT_PORT,
        upstream: str = DEFAULT_UPSTREAM,
        log: GatewayLog | None = None,
        provider: Provider | None = None,
        client: httpx.Client | None = None,
        on_shutdown: Callable[[], None] | None = None,
        lifecycle: Any = None,
    ) -> None:
        import os

        if not upstream_allowed(upstream):
            raise ValueError("upstream_must_be_https")
        self.state = state
        self.log = log or GatewayLog(state.home)
        self.provider = provider
        self.upstream = upstream.rstrip("/")
        self.upstream_host = urlsplit(self.upstream).hostname or ""
        self.client = client or httpx.Client(
            http2=False,
            timeout=httpx.Timeout(connect=10.0, read=None, write=120.0, pool=60.0),
            # idle connections are dropped after 4 s, before the far end closes them
            limits=httpx.Limits(
                max_connections=200, max_keepalive_connections=20, keepalive_expiry=4.0
            ),
            follow_redirects=False,
        )
        self.pid = os.getpid()
        # requests being proxied now, and when the last one started or ended (``reaper``)
        self.flight_lock = threading.Lock()
        self.in_flight = 0
        self.last_request = time.monotonic()
        self.lifecycle = lifecycle  # ``client_gateway.lifecycle.Lifecycle`` when routing is on
        self._on_shutdown = on_shutdown
        self._http = _Server((host, port), _Handler)
        self._http.gateway = self
        self._thread: threading.Thread | None = None
        self._closed = threading.Event()
        self._serving = threading.Event()

    @property
    def port(self) -> int:
        return int(self._http.server_address[1])

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        """Serve in a daemon thread (tests, embedding)."""
        self._thread = threading.Thread(target=self.serve_forever, name="gateway", daemon=True)
        self._thread.start()
        self._serving.wait(5)

    def serve_forever(self) -> None:
        if self.provider is not None:
            self.provider.start()
        self.log.event("gateway_start", pid=self.pid, port=self.port)
        self._serving.set()
        try:
            self._http.serve_forever(poll_interval=0.05)
        finally:
            self._cleanup()

    def shutdown(self) -> None:
        """Stop serving and release everything. Safe to call twice and from a handler thread."""
        if self._serving.is_set():
            self._http.shutdown()
        else:
            self._cleanup()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=10)

    def _cleanup(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        try:
            self._http.server_close()
        except OSError:
            pass
        if self.provider is not None:
            self.provider.stop()
        try:
            self.client.close()
        except Exception:  # noqa: BLE001
            pass
        self.log.event("gateway_stop", pid=self.pid)
        self.log.close()
        if self._on_shutdown is not None:
            try:
                self._on_shutdown()
            except Exception:  # noqa: BLE001
                pass
