"""The adapter contract and the masking context a front end hands to an adapter.

``MaskContext`` is the ONE door to the masking engine for the wire adapters: it wraps the
gateway's ``GatewayState`` and a per-chat ``RemaskMemo`` and reuses the request masker of
``client_gateway.masking`` (memo, string cache, residual-leak check, name recognition) and the
screenshot redactor of ``client_gateway.images``. No engine logic is duplicated here.

``Route`` is what the front end knows about a message (method, host, path, content type); an
adapter that serves several endpoints reads its route from there.

Failure model (fixed, never holds input text): everything that cannot be masked safely raises
``MaskingFailed(code)``; rehydration never raises (the masked text goes through).
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from client_gateway import images as _images
from client_gateway.adapters.streams import StreamRehydrator
from client_gateway.masking import TRANSIENT_WITHHELD, MaskingFailed, _Masker
from client_gateway.memo import RemaskMemo
from client_gateway.reminders import recognition_texts
from client_gateway.state import GatewayState
from core.client_guard.marker import has_marker, strip_marker

MAX_DEPTH = 60


@dataclass(frozen=True)
class Route:
    """The request line of a message as the front end saw it."""

    method: str
    host: str
    path: str
    content_type: str = ""
    turn: str = ""  # the app's per-turn id when it sends one (Copilot ``x-interaction-id``)
    referer: str = ""  # the ``Referer`` header (ChatGPT names the chat in it: ``/c/<id>``)


def chat_space_of(adapter: Any) -> str:
    """The namespace of an adapter's chats (``Adapter.chat_space``, default its name). Adapters that
    serve the same app over several wire formats share one space, so a chat keeps its mode when
    the app switches API between two turns."""
    return str(getattr(adapter, "chat_space", None) or adapter.name)


def norm_host(host: str) -> str:
    """Lower-case host without port and trailing dot."""
    value = (host or "").strip().lower()
    if value.startswith("["):  # an IPv6 literal: leave as is
        return value
    return value.split(":", 1)[0].rstrip(".")


def norm_path(path: str) -> str:
    """The path without query string, fragment and a trailing slash (``/`` stays)."""
    value = (path or "").split("?", 1)[0].split("#", 1)[0]
    return value.rstrip("/") or "/"


def base_type(content_type: str) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


class ImageWithheld(Exception):
    """An image cannot be made safe. ``reason`` is one of the fixed ``images.W_*`` strings."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Adapter(Protocol):
    """One wire format. Instances are stateless and shared; per-request state lives in the
    ``MaskContext`` and in the rehydrator an adapter returns.

    Optional attributes (read with ``getattr``): ``draft_text(body, route)`` (the half-typed text of
    a request that carries a draft, ChatGPT ``prepare``), ``replace_command_turns(body, route,
    new_text)`` (an app that resends its history: every earlier typed command turn is replaced),
    ``chat_space`` (the namespace of the adapter's
    chats, default ``name``; see ``chat_space_of``) and ``uses_turn_ids`` (the app sends a per-turn
    id, ``Route.turn``, that the side calls of a turn share with the main call)."""

    name: str

    def match(self, method: str, host: str, path: str, content_type: str = "") -> bool:
        """Is this request (or its response) one this adapter understands?"""

    def mask_request(self, body: bytes, ctx: "MaskContext", route: Route | None = None) -> bytes:
        """The request body as it may leave this machine. ``MaskingFailed("unsupported_format")``
        when the body is not the expected shape."""

    def chat_key(self, body: bytes, route: Route | None = None) -> str | None:
        """The chat id the body carries, or None."""

    def url_chat_key(self, path: str) -> str | None:
        """The chat id the URL path carries, or None."""

    def newest_user_text(self, body: bytes, route: Route | None = None) -> str | None:
        """Only the text the human typed in the newest user turn (wrappers stripped), or None."""

    def replace_newest_user_text(self, body: bytes, route: Route | None, new_text: str) -> bytes:
        """The body with ONLY the text of the newest user turn (where ``newest_user_text`` reads it)
        replaced by ``new_text``; everything else stays. The same bytes when there is nothing to
        replace. Used to keep a typed ``masking on`` / ``off`` from reaching the AI."""

    def rehydrator(
        self,
        content_type: str,
        content_encoding: str | None,
        ctx: "MaskContext",
        route: Route | None = None,
        reencode: bool = True,
    ) -> StreamRehydrator:
        """The converter of one streamed response."""

    def unmask_response(
        self,
        body: bytes,
        content_type: str,
        content_encoding: str | None,
        ctx: "MaskContext",
        route: Route | None = None,
        reencode: bool = True,
    ) -> bytes:
        """A buffered response body with real values restored (same encoding unless ``reencode``
        is False, then plain bytes)."""


class MaskContext:
    """Engine access for the masking of ONE request (and the rehydration of its response).

    ``marker`` is the install's PBI marker (``fvpbi-<id>``; default: the state's): it is removed
    from every string before masking and from the final bytes, and ``marker_seen`` records that the
    request carried it. ``on_chat_id`` is called (once per distinct id) with a chat id an adapter
    found in a response stream, so the gateway can register a first-turn chat.
    """

    def __init__(
        self,
        state: GatewayState,
        memo: RemaskMemo | None = None,
        *,
        marker: str | None = None,
        ner_degrade: bool = False,
        on_chat_id: Callable[[str], None] | None = None,
    ) -> None:
        self.state = state
        self.memo = memo if memo is not None else RemaskMemo()
        self.marker = marker if marker is not None else (getattr(state, "marker", "") or None)
        self.marker_seen = False
        self.on_chat_id = on_chat_id
        self.ner_degrade = ner_degrade
        self._masker = _Masker(state, self.memo, ner_degrade)
        self._chat_id: str | None = None
        self._unmasked_tokens = 0

    # ------------------------------------------------------------------ marker
    def scan_marker(self, data: bytes) -> bool:
        """True (and remembered in ``marker_seen``) when ``data`` holds the marker."""
        if self.marker and has_marker(data, self.marker):
            self.marker_seen = True
        return self.marker_seen

    def strip_marker_bytes(self, data: bytes) -> bytes:
        return strip_marker(data, self.marker) if self.marker else data

    # ------------------------------------------------------------------ text
    def text(self, s: str, whole: bool = True) -> str:
        """Mask one string: marker out, memo, string cache, engine, residual-leak check.
        ``whole=False`` is the developer-text rule (a lone word is not a client cell). A string
        that carried the PBI marker is an MCP result the masking authority already pseudonymised:
        the pseudonyms in it are kept (masking them again would give the model a second pseudonym
        that unmasks to the first one, not to the client name)."""
        if not s:
            return s
        premasked = False
        if self.marker and self.marker in s:
            self.marker_seen = True
            s = strip_marker(s, self.marker)
            premasked = True  # an MCP result: its pseudonyms were issued by the authority already
        masker = self._masker
        before, before_pre = masker._whole, masker._premasked
        masker._whole, masker._premasked = whole, premasked
        try:
            return masker.text(s)
        finally:
            masker._whole, masker._premasked = before, before_pre

    def replay(self, s: str) -> str | None:
        """The exact masked text the model saw for ``s`` (memo), when it is still leak free."""
        hit = self.memo.lookup(s) if s else None
        if hit is not None and self.state.smap.count_leaks(hit) == 0:
            self._masker.memo_hits += 1
            return hit
        return None

    def mask_tree(
        self,
        obj: Any,
        *,
        skip: frozenset[str] = frozenset(),
        keys: bool = False,
        whole: bool = True,
        _depth: int = 0,
    ) -> Any:
        """``obj`` with every string leaf masked. Values under a key in ``skip`` are kept
        verbatim; ``keys=True`` masks dict keys too (arbitrary user data, e.g. a tool input)."""
        if _depth > MAX_DEPTH:
            raise MaskingFailed("too_deep")
        if isinstance(obj, str):
            return self.text(obj, whole)
        if isinstance(obj, list):
            return [
                self.mask_tree(v, skip=skip, keys=keys, whole=whole, _depth=_depth + 1) for v in obj
            ]
        if isinstance(obj, dict):
            out: dict[Any, Any] = {}
            for key, value in obj.items():
                new_key = self.text(key, whole) if keys and isinstance(key, str) else key
                if isinstance(key, str) and key in skip:
                    out[new_key] = value
                else:
                    out[new_key] = self.mask_tree(
                        value, skip=skip, keys=keys, whole=whole, _depth=_depth + 1
                    )
            return out
        return obj

    def check_verbatim(self, obj: Any) -> None:
        """Check-only pass over text that is forwarded as it is (API settings, ids): an identity
        value or a shape in it fails the request closed instead of leaving the machine."""
        self._masker._leak_check(obj)

    def recognize(
        self, texts: list[str], history: bool = False, older: list[str] | None = None
    ) -> None:
        """Run name recognition over the NEW text of a request, before anything is masked
        (within a time budget, then the request goes on: ``masking.recognize_new_text``). ``older``: the earlier conversation of a
        chat masked for the first time; recognized best effort, it never blocks (see
        ``masking.recognize_history``). ``history=True`` marks the history scanned."""
        marked = self._masker._carries_marker  # an MCP result: masked by the authority already
        pieces = [p for s in texts if s and not marked(s) for p in recognition_texts(s)]
        if history:
            from client_gateway.masking import recognize_history  # noqa: PLC0415 - import cycle

            recognize_history(
                self.state, [p for s in (older or []) if s for p in recognition_texts(s)]
            )
            self.memo.history_scanned = True
        from client_gateway.masking import recognize_new_text  # noqa: PLC0415 - import cycle

        recognize_new_text(self.state, pieces)
        self._masker._pre_recognized = True

    # ------------------------------------------------------------------ images
    def image_bytes(self, data: bytes) -> bytes:
        """The image as it may leave this machine (the same bytes when nothing is readable in
        it, redacted PNG bytes otherwise). Raises ``ImageWithheld`` when it cannot be made safe."""
        state = self.state
        policy = state.images_policy
        masker = self._masker
        if policy == "pass":
            masker.images["clean"] += 1
            return data
        if policy == "withhold":
            masker.images["withheld"] += 1
            raise ImageWithheld(_images.W_POLICY)
        smap = state.smap
        key = (hashlib.sha256(data).digest(), policy, "bytes")
        version = smap.revision
        cached = state.image_cache.get(key)
        if cached is not None and (
            cached[0] == version
            or (
                len(cached) > 3
                and cached[3] is not None
                and not smap.changed_since(cached[3], cached[0], normalised=True, loose=True)
            )
        ):
            return self._image_outcome(cached[1], cached[2], data)
        reason: str | None = None
        out: bytes | None = None
        outcome = "clean"
        ocr_norm = None
        try:
            result = _images.redact_image_bytes(data, smap)
        except _images._Withhold as exc:
            reason = exc.reason
        except Exception:  # noqa: BLE001 - fail closed; nothing from the failure is surfaced
            reason = _images.W_OCR_FAILED
        else:
            ocr_norm = result.stats.get("ocr_norm")
            if result.data is not None:
                out, outcome = result.data, "redacted"
        if reason is not None:
            outcome = "withheld"
        if outcome != "withheld" or reason not in TRANSIENT_WITHHELD:
            payload: Any = reason if reason is not None else out
            state.image_cache.put(key, (version, payload, outcome, ocr_norm), len(data) + 1024)
        return self._image_outcome(reason if reason is not None else out, outcome, data)

    def _image_outcome(self, payload: Any, outcome: str, original: bytes) -> bytes:
        self._masker.images[outcome] += 1
        if outcome == "withheld":
            raise ImageWithheld(str(payload))
        return original if payload is None else bytes(payload)

    # ------------------------------------------------------------------ rehydration
    def unmask(self, s: str, context: str = "plain", prefix: str = "") -> str:
        """``s`` with this session's tokens replaced by the real values; never raises (on an
        engine error the masked text goes through)."""
        if not s:
            return s
        try:
            out, n = self.state.smap.unmask_text(s, context, prefix=prefix)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001 - a pseudonym on screen, never a leak
            return s
        self._unmasked_tokens += n
        return out

    def unmask_obj(
        self,
        obj: Any,
        skip: frozenset[str] = frozenset(),
        json_keys: frozenset[str] = frozenset(),
        _key: str | None = None,
        _depth: int = 0,
    ) -> Any:
        """``obj`` with every string leaf unmasked. Values under a key in ``skip`` (ids, tokens)
        are kept; strings under a key in ``json_keys`` are JSON text (real values are escaped)."""
        if _depth > MAX_DEPTH:
            return obj
        if isinstance(obj, str):
            if _key in skip:
                return obj
            return self.unmask(obj, "json" if _key in json_keys else "plain")
        if isinstance(obj, list):
            return [self.unmask_obj(v, skip, json_keys, _key, _depth + 1) for v in obj]
        if isinstance(obj, dict):
            return {k: self.unmask_obj(v, skip, json_keys, k, _depth + 1) for k, v in obj.items()}
        return obj

    def report_chat_id(self, chat_id: Any) -> None:
        """An adapter found a chat id in a response; tell the gateway once per distinct id."""
        if not isinstance(chat_id, str) or not chat_id or chat_id == self._chat_id:
            return
        self._chat_id = chat_id
        if self.on_chat_id is not None:
            try:
                self.on_chat_id(chat_id)
            except Exception:  # noqa: BLE001 - registering a chat never breaks the stream
                pass

    # ------------------------------------------------------------------ counters
    def finish(self) -> None:
        """Add this context's counters to the gateway status (call once per request/response)."""
        masker = self._masker
        state = self.state
        state.bump("masked_strings", masker.masked)
        state.bump("memo_hits", masker.memo_hits)
        for outcome in ("redacted", "withheld", "clean"):
            state.bump(f"images_{outcome}", masker.images[outcome])
            masker.images[outcome] = 0
        state.bump("tokens_rehydrated", self._unmasked_tokens)
        masker.masked = masker.memo_hits = self._unmasked_tokens = 0


def load_json(body: bytes) -> Any:
    """Parse a JSON request body; ``MaskingFailed("bad_json")`` when it is not JSON."""
    try:
        return json.loads(body)
    except (ValueError, RecursionError):
        raise MaskingFailed("bad_json") from None


def b64decode_image(payload: str) -> bytes | None:
    """Bytes of a base64 payload, or None when it is not valid base64."""
    try:
        return base64.b64decode(payload, validate=False)
    except (ValueError, TypeError):
        return None  # binascii.Error is a ValueError
