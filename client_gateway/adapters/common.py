"""What the named-app adapters share (phase 3c). Each adapter keeps only what is specific to its app:
its route, its chat id, the newest typed text and the fields that hold the reply text.

* ``guarded_mask``: the fail-closed envelope of a request mask (build, dump, strip the marker, map
  every unexpected error to a fixed ``MaskingFailed`` code, add the counters).
* ``walk_dict`` / ``mask_fields`` / ``mask_str`` / ``mask_content``: the field-by-field walk of a
  JSON object. A field with a handler is masked by it; every other field is forwarded verbatim after
  the check-only leak pass (``walk_dict``) or masked as a tree (``mask_fields``).
* ``rewrite_body`` / ``replace_part_texts``: the edit of ONE user turn of a request body (a typed
  command is replaced by a neutral text before the body is masked or forwarded).
* ``mask_data_url_image``: an ``image_url`` part (data URL) through ``MaskContext.image_bytes``.
* ``sse_rehydrator`` / ``unmask_json_response`` / ``HoldStream``: the response side (stream or
  buffered body) with split-safe text holds and the remask memo.

No engine logic lives here: everything goes through ``MaskContext`` (``adapters.base``). Nothing
here logs or raises with input text.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import Any

from client_gateway import images as _images
from client_gateway.adapters.base import (
    ImageWithheld,
    MaskContext,
    b64decode_image,
    base_type,
)
from client_gateway.adapters.streams import (
    PassthroughRehydrator,
    SSEPipeline,
    StreamRehydrator,
    TailHold,
    UnsupportedEncoding,
    decode_bytes,
    encode_bytes,
    parse_event,
)
from client_gateway.masking import MaskingFailed, dumps

VERBATIM: Any = object()  # a handler's answer: "forward this field as it is (after the leak check)"


# ---------------------------------------------------------------------- request side


def guarded_mask(ctx: MaskContext, build: Callable[[], Any]) -> bytes:
    """``build()`` is the masked JSON tree; the result is its bytes without the install's marker.
    Fails closed: ``MaskingFailed`` passes, anything else becomes ``too_deep`` / ``internal``."""
    try:
        data = ctx.strip_marker_bytes(dumps(build()))
    except MaskingFailed:
        raise
    except RecursionError:
        raise MaskingFailed("too_deep") from None
    except Exception:  # noqa: BLE001 - fail closed; nothing from the failure is surfaced
        raise MaskingFailed("internal") from None
    ctx.finish()
    return data


def walk_dict(
    mapping: dict[str, Any],
    ctx: MaskContext,
    handlers: dict[str, Callable[[Any], Any]],
    verbatim_skip: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """``mapping`` with the handled keys masked. A key without a handler (or whose handler answers
    ``VERBATIM``) is forwarded as it is after a check-only leak pass, unless it is in
    ``verbatim_skip`` (developer text that is not read)."""
    out: dict[str, Any] = {}
    for key, value in mapping.items():
        handler = handlers.get(key)
        result = handler(value) if handler is not None else VERBATIM
        if result is VERBATIM:
            if key not in verbatim_skip:
                ctx.check_verbatim(value)
            out[key] = value
        else:
            out[key] = result
    return out


def mask_fields(
    mapping: dict[str, Any],
    ctx: MaskContext,
    whole: bool,
    passthrough: frozenset[str],
    handlers: dict[str, Callable[[Any], Any]],
) -> dict[str, Any]:
    """``mapping`` with ``passthrough`` keys kept, handled keys masked by their handler and every
    other field masked as a tree (``whole=False``: the developer-text rule)."""
    out: dict[str, Any] = {}
    for key, value in mapping.items():
        if key in passthrough:
            out[key] = value
        elif key in handlers:
            out[key] = handlers[key](value)
        else:
            out[key] = ctx.mask_tree(value, whole=whole)
    return out


def mask_str(ctx: MaskContext, whole: bool = True) -> Callable[[Any], Any]:
    """A ``walk_dict`` handler: a string value is masked, anything else is forwarded verbatim."""
    return lambda value: ctx.text(value, whole) if isinstance(value, str) else VERBATIM


def mask_content(content: Any, ctx: MaskContext, whole: bool, part: Callable[[Any], Any]) -> Any:
    """A message content: a string, a list of parts (each through ``part``) or any other tree."""
    if isinstance(content, str):
        return ctx.text(content, whole)
    if isinstance(content, list):
        return [part(p) for p in content]
    return ctx.mask_tree(content, whole=whole)


def mask_data_url_image(
    part: dict[str, Any],
    ctx: MaskContext,
    withheld: Callable[[str], Any],
    retype: bool = False,
) -> Any:
    """An ``image_url`` part: a ``data:`` URL goes through ``MaskContext.image_bytes`` (the same part
    when nothing is readable in the image, redacted PNG bytes otherwise). A remote URL, an
    undecodable payload and a withheld image become ``withheld(reason)``. ``retype``: a
    ``media_type`` next to the URL is set to ``image/png`` when the bytes changed."""
    spec = part.get("image_url")
    url = spec.get("url") if isinstance(spec, dict) else spec
    if not isinstance(url, str) or not url.startswith("data:"):
        return withheld(_images.W_URL)  # a remote image or a file id: its bytes are not here
    head, _, payload = url.partition(",")
    raw = b64decode_image(payload) if head.lower().endswith(";base64") else None
    if not raw:
        return withheld(_images.W_DECODE)
    try:
        out = ctx.image_bytes(raw)
    except ImageWithheld as exc:
        return withheld(exc.reason)
    if out == raw:
        return part
    new_url = "data:image/png;base64," + base64.b64encode(out).decode("ascii")
    new_spec: Any
    if isinstance(spec, dict):
        new_spec = dict(spec, url=new_url)
        if retype and "media_type" in new_spec:
            new_spec["media_type"] = "image/png"
    else:
        new_spec = new_url
    return dict(part, image_url=new_spec)


# ---------------------------------------------------------------------- one user turn


def rewrite_body(body: bytes, edit: Callable[[Any], bool]) -> bytes:
    """``body`` after ``edit(tree)`` changed its JSON tree in place (``edit`` answers whether it
    did). The same bytes when the body is not JSON or nothing was changed."""
    try:
        tree = json.loads(body)
    except (ValueError, RecursionError):
        return body
    try:
        if not edit(tree):
            return body
        return dumps(tree)
    except (RecursionError, TypeError, AttributeError, KeyError):
        return body


def replace_part_texts(parts: list[Any], new_text: str) -> list[Any]:
    """``parts`` (strings, or dicts with a ``text``) with the first non-empty text part set to
    ``new_text`` and every other non-empty text part dropped. Parts that are not text (images, asset
    pointers) and empty strings stay where they are."""
    out: list[Any] = []
    placed = False
    for part in parts:
        if isinstance(part, str):
            has_text = bool(part)
        else:
            has_text = (
                isinstance(part, dict) and isinstance(part.get("text"), str) and bool(part["text"])
            )
        if not has_text:
            out.append(part)
        elif not placed:
            placed = True
            out.append(new_text if isinstance(part, str) else dict(part, text=new_text))
    return out


# ---------------------------------------------------------------------- response side


def sse_rehydrator(
    stream: Callable[[MaskContext, str | None, bool], StreamRehydrator],
    content_type: str,
    content_encoding: str | None,
    ctx: MaskContext,
    reencode: bool,
) -> StreamRehydrator:
    """The converter of one streamed response: ``stream(...)`` for an event stream, a passthrough for
    a buffered body (``unmask_json_response``) and for an encoding that cannot be decoded."""
    if base_type(content_type) != "text/event-stream":
        return PassthroughRehydrator()
    try:
        return stream(ctx, content_encoding, reencode)
    except (UnsupportedEncoding, ImportError):
        return PassthroughRehydrator(errors=1)


def unmask_json_response(
    body: bytes,
    content_type: str,
    content_encoding: str | None,
    ctx: MaskContext,
    reencode: bool,
    skip: frozenset[str],
    json_keys: frozenset[str] = frozenset(),
) -> bytes:
    """A buffered JSON response with real values restored (same encoding unless ``reencode`` is
    False). Anything that goes wrong returns the masked body: a pseudonym on screen, never a leak.
    """
    if "json" not in content_type.lower() or not body:
        return body
    try:
        tree = json.loads(decode_bytes(body, content_encoding))
        out = dumps(ctx.unmask_obj(tree, skip, json_keys))
        ctx.finish()
        return encode_bytes(out, content_encoding) if reencode else out
    except Exception:  # noqa: BLE001 - the masked body goes through
        return body


class HoldStream(SSEPipeline):
    """The base of the SSE rehydrators that hold back the tail of streamed text (``TailHold``)."""

    def __init__(self, ctx: MaskContext, content_encoding: str | None, reencode: bool) -> None:
        super().__init__(content_encoding, reencode)
        self._ctx = ctx
        self._smap = ctx.state.smap
        self._memo = ctx.memo

    def _read(self, raw: bytes, delim: bytes) -> tuple[list[str], Any] | bytes:
        """``(lines, payload)`` of a frame that carries JSON, else the bytes to forward: a frame
        without ``data``, with other text, and ``[DONE]`` (which flushes every held tail first)."""
        lines, text = parse_event(raw)
        if text is None:
            return raw
        if text.strip() == "[DONE]":
            self.stats["recognised"] += 1
            return self._end(delim) + raw
        try:
            return lines, json.loads(text)
        except ValueError:
            return raw

    def _remember(self, field: str, hold: TailHold) -> None:
        """Record masked -> unmasked pairs: the next request resends the unmasked text. A field
        named ``arguments`` is tool-call JSON: one memo entry per changed string leaf as well."""
        self.stats["tokens"] += hold.tokens
        self.stats["errors"] += hold.errors
        if hold.failed:
            return
        try:
            self._memo.remember(hold.done, hold.unmasked)
            if field != "arguments":
                return
            tree = json.loads(hold.done or "{}")
            saved = dict(self._smap.stats)
            try:  # one memo entry per changed string leaf
                self._smap.unmask_tree(tree, on_string=self._memo.remember)
            finally:
                self._smap.stats.update(saved)
        except Exception:  # noqa: BLE001 - the memo is an optimisation, never a failure
            self.stats["errors"] += 1
