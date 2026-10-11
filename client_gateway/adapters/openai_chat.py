"""Adapter for OpenAI Chat Completions as VS Code Copilot sends it
(``POST api.*.githubcopilot.com/chat/completions``, SSE reply).

Request (captured 2026-10-07): ``messages[]`` with ``role`` system / user / assistant / tool;
``content`` is a string or a list of parts (``text``, ``image_url``); assistant ``tool_calls[]``
with JSON-string ``function.arguments``; ``reasoning_text`` (plain) and ``reasoning_opaque``
(encrypted, never touched); ``copilot_cache_control``. Copilot resends the whole history on every
turn, so the masker leans on the remask memo (a reply the model wrote with pseudonyms is replayed
byte-identically).

What is masked: every string of user / assistant / tool messages (``system`` and ``developer``
text with the developer-text rule, ``whole=False``), text parts, ``reasoning_text``, the leaves
AND keys of tool-call arguments (parsed, masked, re-dumped; unchanged text stays byte-identical),
and image parts. Everything else is forwarded verbatim after a check-only leak pass (the top-level
settings; ``tools`` is the developer's tool list and is not read): a message-level ``role`` /
``tool_call_id`` / ``reasoning_opaque`` / ``copilot_cache_control`` and the whole ``tools`` list.

Images: ``data:`` URLs go through ``MaskContext.image_bytes``; a withheld or unreadable image, and
any image that is a remote URL (the capture shows pasted images as
``https://github.com/github-copilot/chat/attachments/<id>``: the bytes were uploaded in a separate
request this adapter never sees) becomes a text part ``[Client masking: image withheld - ...]``.

Response: SSE ``choices[].delta.content``, ``delta.reasoning_text`` and
``delta.tool_calls[].function.arguments`` (JSON context) are rehydrated split-safe; every other
event and field passes through byte-identical; ``[DONE]`` and a ``finish_reason`` flush held text.

The route, the chat identity, the request walk and the response plumbing are shared with the
Responses adapter (``adapters.copilot.CopilotAdapter``, ``adapters.common``); this file holds the
fields and events specific to Chat Completions.
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
)
from client_gateway.adapters.copilot import (
    DEV_ROLES,
    CopilotAdapter,
    mask_arguments,
    text_parts,
)
from client_gateway.adapters.streams import TailHold, frame_event, rebuild_event
from client_gateway.masking import MaskingFailed

PATH = "/chat/completions"
MESSAGE_PASS = frozenset({"role", "tool_call_id", "reasoning_opaque", "copilot_cache_control"})
PART_PASS = frozenset({"type", "copilot_cache_control", "cache_control"})
RESPONSE_SKIP = frozenset({"id", "created", "model", "finish_reason", "role", "type", "name"})

# ---------------------------------------------------------------------- response stream


class _ChatStream(HoldStream):
    """Rehydrates the SSE stream of one chat completion."""

    def __init__(self, ctx: MaskContext, content_encoding: str | None, reencode: bool) -> None:
        super().__init__(ctx, content_encoding, reencode)
        # (choice index, field, tool-call index) -> hold; field: content | reasoning_text | arguments
        self._holds: dict[tuple[int, str, int], TailHold] = {}
        self._envelope: dict[str, Any] = {}

    # -- events
    def _event(self, raw: bytes, delim: bytes) -> bytes:
        got = self._read(raw, delim)
        if isinstance(got, bytes):
            return got
        lines, payload = got
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if not isinstance(choices, list):
            return raw
        self.stats["recognised"] += 1
        for key in ("id", "created", "model"):
            if key in payload:
                self._envelope[key] = payload[key]
        pre = b""
        changed = False
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            raw_index = choice.get("index")
            index: int = raw_index if isinstance(raw_index, int) else 0
            raw_delta = choice.get("delta")
            delta: dict[str, Any] = raw_delta if isinstance(raw_delta, dict) else {}
            slots = self._slots(index, delta)
            finishing = bool(choice.get("finish_reason"))
            if slots or delta.get("tool_calls"):
                # text held for a field this event does not continue ends here; an event with no
                # streamed text at all (``content: ""``, a role-only delta) changes nothing
                pre += self._flush(index, delim, keep={s[0] for s in slots})
            for slot_key, container, field in slots:
                piece = container[field]
                hold = self._hold(slot_key)
                new = hold.feed(piece)
                if finishing:  # the last piece of the message: nothing may stay held
                    new += hold.flush()
                container[field] = new
                changed = changed or new != piece
            if finishing:
                pre += self._flush(index, delim)
        if not changed:
            return pre + raw
        self.stats["rehydrated_events"] += 1
        return pre + rebuild_event(lines, payload, self.eol)

    @staticmethod
    def _slots(index: int, delta: dict[str, Any]) -> list[tuple[tuple[int, str, int], Any, str]]:
        """The streamed strings of a delta: (hold key, the dict holding it, its key)."""
        out: list[tuple[tuple[int, str, int], Any, str]] = []
        for field in ("content", "reasoning_text"):
            if isinstance(delta.get(field), str) and delta[field]:
                out.append(((index, field, 0), delta, field))
        calls = delta.get("tool_calls")
        if isinstance(calls, list):
            for call in calls:
                fn = call.get("function") if isinstance(call, dict) else None
                if (
                    isinstance(fn, dict)
                    and isinstance(fn.get("arguments"), str)
                    and fn["arguments"]
                ):
                    tindex = call.get("index") if isinstance(call.get("index"), int) else 0
                    out.append(((index, "arguments", tindex), fn, "arguments"))
        return out

    def _hold(self, key: tuple[int, str, int]) -> TailHold:
        hold = self._holds.get(key)
        if hold is None:
            hold = self._holds[key] = TailHold(
                self._smap, "json" if key[1] == "arguments" else "plain"
            )
        return hold

    # -- flushing
    def _flush(
        self,
        choice: int | None,
        delim: bytes,
        keep: set[tuple[int, str, int]] | None = None,
    ) -> bytes:
        """Whole frames that carry the held tails of one choice (None: every choice) except the
        fields in ``keep``; those fields are finished and recorded in the remask memo."""
        out: list[bytes] = []
        for key in list(self._holds):
            if (choice is not None and key[0] != choice) or (keep is not None and key in keep):
                continue
            hold = self._holds.pop(key)
            if hold.held:
                out.append(self._tail_frame(key, hold.flush()) + delim)
                self.stats["flushed_tails"] += 1
            self._remember(key[1], hold)
        return b"".join(out)

    def _tail_frame(self, key: tuple[int, str, int], text: str) -> bytes:
        index, field, tindex = key
        if field == "arguments":
            delta: dict[str, Any] = {
                "tool_calls": [{"index": tindex, "function": {"arguments": text}}]
            }
        else:
            delta = {field: text}
        payload = dict(self._envelope, choices=[{"index": index, "delta": delta}])
        return frame_event(None, payload, self.eol)

    def _end(self, delim: bytes) -> bytes:
        return self._flush(None, delim)


# ---------------------------------------------------------------------- the adapter


class OpenAIChatAdapter(CopilotAdapter):
    name = "openai_chat"
    path = PATH
    items_key = "messages"
    response_skip = RESPONSE_SKIP
    stream = _ChatStream

    def _items(self, tree: Any) -> list[dict[str, Any]]:
        messages = tree.get("messages") if isinstance(tree, dict) else None
        if not isinstance(messages, list) or not all(isinstance(m, dict) for m in messages):
            raise MaskingFailed("unsupported_format")
        return messages

    def _handlers(self, ctx: MaskContext) -> dict[str, Any]:
        return {"messages": lambda value: [self._message(m, ctx) for m in value]}

    def _from_model(self, item: dict[str, Any]) -> bool:
        return item.get("role") == "assistant"

    def _strings(self, item: dict[str, Any]) -> list[str]:
        """The strings of a message that name recognition must read."""
        out = [s for s in text_parts(item.get("content")) if s]
        reasoning = item.get("reasoning_text")
        if isinstance(reasoning, str) and reasoning:
            out.append(reasoning)
        return out

    def _message(self, message: dict[str, Any], ctx: MaskContext) -> dict[str, Any]:
        whole = message.get("role") not in DEV_ROLES
        # reasoning_text, name, anything unknown: every string leaf is masked
        handlers = {
            "content": lambda v: mask_content(v, ctx, whole, lambda p: self._part(p, ctx, whole)),
            "tool_calls": lambda v: self._tool_calls(v, ctx),
        }
        return mask_fields(message, ctx, whole, MESSAGE_PASS, handlers)

    def _part(self, part: Any, ctx: MaskContext, whole: bool) -> Any:
        if isinstance(part, dict) and part.get("type") == "image_url":
            return mask_data_url_image(part, ctx, _images.withheld_block, retype=True)
        return ctx.mask_tree(part, skip=PART_PASS, whole=whole)

    def _tool_calls(self, calls: Any, ctx: MaskContext) -> Any:
        if not isinstance(calls, list):
            return ctx.mask_tree(calls)
        return [self._tool_call(call, ctx) for call in calls]

    def _tool_call(self, call: Any, ctx: MaskContext) -> Any:
        if not isinstance(call, dict):
            return ctx.mask_tree(call)
        out: dict[str, Any] = {}
        for key, value in call.items():
            if key in ("id", "type", "index"):
                out[key] = value
            elif key == "function" and isinstance(value, dict):
                out[key] = {k: self._function_field(k, v, ctx) for k, v in value.items()}
            else:
                out[key] = ctx.mask_tree(value)
        return out

    def _function_field(self, key: str, value: Any, ctx: MaskContext) -> Any:
        if key == "name":
            return value
        if key == "arguments" and isinstance(value, str):
            return mask_arguments(value, ctx)
        return ctx.mask_tree(value)
