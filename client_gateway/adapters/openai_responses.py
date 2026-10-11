"""Adapter for the OpenAI Responses API as VS Code Copilot sends it
(``POST api.*.githubcopilot.com/responses``, SSE reply). Copilot picks the API per chat ("Auto"):
the same chat can use ``/chat/completions`` (``openai_chat``) one turn and ``/responses`` the next.

Request (captured 2026-10-07): ``model``, ``input[]``, ``stream``, ``tools``, ``max_output_tokens``,
``store``, ``text``, ``truncation``, ``reasoning`` (config), ``include``, optional ``instructions``.
``input[]`` items:

* ``{role: system|user|assistant, content: [{type: input_text|output_text, text}]}`` (a message;
  assistant ones also carry ``id`` / ``status`` / ``type: "message"``);
* ``{type: reasoning, id, summary: [{type: summary_text, text}], encrypted_content}``;
* ``{type: function_call, name, arguments: "<json text>", call_id}``;
* ``{type: function_call_output, call_id, output: "<string>"}`` (the MCP result, with the marker).

What is masked: every text part, ``reasoning.summary[].text``, the leaves AND keys of
``function_call.arguments`` (parsed, masked, re-dumped; unchanged text stays byte-identical),
``function_call_output.output`` (a string, or a list of parts) and image parts. ``system`` /
``developer`` messages and ``instructions`` use the developer-text rule (``whole=False``). Ids,
``call_id``, ``name``, ``encrypted_content`` (opaque), ``tools`` (the developer's list, not read) and
the config keys are forwarded verbatim; the config keys pass a check-only leak pass. A body of another
shape, and a part that cannot be inspected (``input_file``, ``input_audio``), fail closed
(``unsupported_format``): in a masked chat the front end blocks it (rule R9).

Response: SSE ``event: <name>`` / ``data: {"type": "response.<name>", ...}``. The streamed strings
(``response.output_text.delta``, ``response.refusal.delta``, ``response.reasoning_summary_text.delta``,
``response.reasoning_text.delta``, ``response.function_call_arguments.delta``) are rehydrated
split-safe per field; an event that carries a whole object or a whole text (``item``, ``part``,
``text``, ``arguments``, ``response``) is unmasked as a tree; everything else passes through
byte-identical. The text held back at a field end is emitted as ONE extra delta event in front of
the event that ends the field.

The route, the chat identity, the request walk and the response plumbing are shared with the Chat
Completions adapter (``adapters.copilot.CopilotAdapter``, ``adapters.common``); this file holds the
items and events specific to the Responses API.
"""

from __future__ import annotations

from typing import Any

from client_gateway import images as _images
from client_gateway.adapters.base import MaskContext
from client_gateway.adapters.common import (
    HoldStream,
    mask_content,
    mask_data_url_image,
    mask_fields,
    mask_str,
)
from client_gateway.adapters.copilot import (
    DEV_ROLES,
    CopilotAdapter,
    mask_arguments,
    text_parts,
)
from client_gateway.adapters.streams import TailHold, frame_event, rebuild_event
from client_gateway.masking import MaskingFailed

PATH = "/responses"
# item keys forwarded verbatim (ids and structure; ``encrypted_content`` is an opaque blob)
ITEM_PASS = frozenset(
    {"type", "role", "id", "call_id", "status", "name", "encrypted_content", "phase", "object"}
)
PART_PASS = frozenset({"type", "cache_control", "copilot_cache_control"})
UNINSPECTABLE_PARTS = frozenset({"input_file", "input_audio"})
# values that are ids or structure, never text: not touched when a response is unmasked
RESPONSE_SKIP = frozenset(
    {
        "id", "item_id", "call_id", "type", "status", "role", "model", "object", "name",
        "encrypted_content", "obfuscation", "service_tier", "safety_identifier",
        "previous_response_id", "prompt_cache_retention", "prompt_cache_key", "tool_choice",
        "truncation", "effort", "code", "finish_reason",
    }
)  # fmt: skip

# streamed text events: type -> (field, kind of the hold, key of the second index)
_DELTAS = {
    "response.output_text.delta": ("text", "plain", "content_index"),
    "response.refusal.delta": ("refusal", "plain", "content_index"),
    "response.reasoning_summary_text.delta": ("summary", "plain", "summary_index"),
    "response.reasoning_text.delta": ("reasoning", "plain", "content_index"),
    "response.function_call_arguments.delta": ("arguments", "json", ""),
    "response.custom_tool_call_input.delta": ("custom", "plain", ""),
}
_ENDS_AFTER = ("response.created", "response.in_progress")  # events that end no field

# ---------------------------------------------------------------------- response stream

Slot = tuple[str, int, int]  # (field, output_index, second index)


class _ResponsesStream(HoldStream):
    """Rehydrates the SSE stream of one ``/responses`` call."""

    def __init__(self, ctx: MaskContext, content_encoding: str | None, reencode: bool) -> None:
        super().__init__(ctx, content_encoding, reencode)
        self._holds: dict[Slot, TailHold] = {}
        self._tmpl: dict[Slot, dict[str, Any]] = {}  # the last delta event of a field, minus text
        self._names: dict[Slot, str] = {}  # the event name of that field's delta

    # -- events
    def _event(self, raw: bytes, delim: bytes) -> bytes:
        got = self._read(raw, delim)
        if isinstance(got, bytes):
            return got
        lines, payload = got
        kind = payload.get("type") if isinstance(payload, dict) else None
        if not isinstance(kind, str) or not kind.startswith("response."):
            return raw
        self.stats["recognised"] += 1
        delta = _DELTAS.get(kind)
        if delta is not None and isinstance(payload.get("delta"), str):
            return self._delta(raw, lines, payload, kind, delta)
        # every other event: the holds of the fields it ends are flushed in front of it, then the
        # whole object or text it carries is unmasked as a tree
        pre = b""
        if kind not in _ENDS_AFTER and not kind.endswith(".added"):
            index = payload.get("output_index")
            pre = self._flush(index if isinstance(index, int) else None, delim)
        converted = self._ctx.unmask_obj(payload, RESPONSE_SKIP, frozenset({"arguments"}))
        if converted == payload:
            return pre + raw
        self.stats["rehydrated_events"] += 1
        return pre + rebuild_event(lines, converted, self.eol)

    def _delta(
        self, raw: bytes, lines: list[str], payload: dict[str, Any], kind: str, spec: Any
    ) -> bytes:
        field, context, second = spec
        slot = self._slot(field, payload, second)
        hold = self._holds.get(slot)
        if hold is None:
            hold = self._holds[slot] = TailHold(self._smap, context)
        piece = payload["delta"]
        new = hold.feed(piece)
        self._tmpl[slot] = {k: v for k, v in payload.items() if k not in ("delta", "obfuscation")}
        self._names[slot] = _event_name(lines, kind)
        if new == piece:
            return raw
        self.stats["rehydrated_events"] += 1
        return rebuild_event(lines, dict(payload, delta=new), self.eol)

    @staticmethod
    def _slot(field: str, payload: dict[str, Any], second: str) -> Slot:
        out = payload.get("output_index")
        sub = payload.get(second) if second else 0
        return (field, out if isinstance(out, int) else 0, sub if isinstance(sub, int) else 0)

    # -- flushing
    def _flush(self, output_index: int | None, delim: bytes) -> bytes:
        """Whole frames that carry the held tails of one output item (None: every item); the
        fields are finished and recorded in the remask memo."""
        out: list[bytes] = []
        for slot in list(self._holds):
            if output_index is not None and slot[1] != output_index:
                continue
            hold = self._holds.pop(slot)
            if hold.held:
                out.append(self._tail_frame(slot, hold.flush()) + delim)
                self.stats["flushed_tails"] += 1
            self._remember(slot[0], hold)
            self._tmpl.pop(slot, None)
            self._names.pop(slot, None)
        return b"".join(out)

    def _tail_frame(self, slot: Slot, text: str) -> bytes:
        payload = dict(self._tmpl.get(slot, {"type": "response.output_text.delta"}), delta=text)
        name = self._names.get(slot) or str(payload.get("type"))
        return frame_event(name, payload, self.eol)

    def _end(self, delim: bytes) -> bytes:
        return self._flush(None, delim)


def _event_name(lines: list[str], kind: str) -> str:
    """The ``event:`` name of a frame (else its ``type``)."""
    for line in lines:
        if line.startswith("event:"):
            return line[6:].strip() or kind
    return kind


# ---------------------------------------------------------------------- the adapter


def _withheld(reason: str) -> dict[str, Any]:
    """The text part that replaces an image that cannot be made safe (an ``input_text`` part)."""
    return {"type": "input_text", "text": _images.withheld_block(reason)["text"]}


class OpenAIResponsesAdapter(CopilotAdapter):
    name = "openai_responses"
    path = PATH
    items_key = "input"
    response_skip = RESPONSE_SKIP
    stream = _ResponsesStream

    def _items(self, tree: Any) -> Any:
        if not isinstance(tree, dict):
            raise MaskingFailed("unsupported_format")
        items = tree.get("input")
        if not isinstance(items, str) and (
            not isinstance(items, list) or not all(isinstance(i, dict) for i in items)
        ):
            raise MaskingFailed("unsupported_format")
        return items

    def _handlers(self, ctx: MaskContext) -> dict[str, Any]:
        return {
            "input": lambda v: (
                ctx.text(v) if isinstance(v, str) else [self._item(i, ctx) for i in v]
            ),
            "instructions": mask_str(ctx, whole=False),
        }

    def _from_model(self, item: dict[str, Any]) -> bool:
        """Is this input item something the model wrote (an assistant message, a call, a reasoning)?"""
        return item.get("role") == "assistant" or item.get("type") in ("function_call", "reasoning")

    def _strings(self, item: dict[str, Any]) -> list[str]:
        """The strings of an input item that name recognition must read."""
        return [
            s for key in ("content", "output", "summary") for s in text_parts(item.get(key)) if s
        ]

    def _item(self, item: dict[str, Any], ctx: MaskContext) -> dict[str, Any]:
        whole = item.get("role") not in DEV_ROLES
        handlers = {
            "content": lambda v: self._parts(v, ctx, whole),
            "summary": lambda v: self._parts(v, ctx, True),
            "output": lambda v: self._parts(v, ctx, True),
            "arguments": lambda v: (
                mask_arguments(v, ctx) if isinstance(v, str) else ctx.mask_tree(v, whole=whole)
            ),
        }
        return mask_fields(item, ctx, whole, ITEM_PASS, handlers)  # anything unknown: masked

    def _parts(self, content: Any, ctx: MaskContext, whole: bool) -> Any:
        return mask_content(content, ctx, whole, lambda p: self._part(p, ctx, whole))

    def _part(self, part: Any, ctx: MaskContext, whole: bool) -> Any:
        if isinstance(part, dict):
            kind = part.get("type")
            if kind in UNINSPECTABLE_PARTS:
                raise MaskingFailed("unsupported_format")  # content that cannot be read here
            if kind == "input_image":
                return mask_data_url_image(part, ctx, _withheld)
        return ctx.mask_tree(part, skip=PART_PASS, whole=whole)
