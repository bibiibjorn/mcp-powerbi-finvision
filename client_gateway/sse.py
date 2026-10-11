"""Pure SSE rehydrator for ONE Anthropic ``/v1/messages`` streaming response.

``SSERehydrator(smap, memo).feed(chunk) -> bytes`` turns the masked stream the API sends (tokens
such as ``CUS_48213``) into the stream Claude Code should see (real values), chunk by chunk.
``finish()`` flushes. No I/O, no logging, no real value ever leaves this module except in the
returned bytes.

Rules:

* Frames are parsed across arbitrary chunk splits (``\\n\\n`` and ``\\r\\n\\r\\n``); a frame is only
  processed once complete, so a UTF-8 character or a JSON document is never cut.
* ``text_delta`` and ``input_json_delta`` are rehydrated. Real values inside ``partial_json`` are
  JSON-escaped (and DAX-quote-doubled when the token sits inside a DAX literal) by the engine.
* A per-content-block hold-back keeps an incomplete token tail (``CUS_4``) until the next delta;
  what is still held at ``content_block_stop`` is flushed as ONE extra ``content_block_delta``
  right before the stop event. That is the only event this module ever adds.
* ``thinking_delta``, ``signature_delta``, ``ping``, ``message_*``, ``error`` and every unknown
  event pass through byte-identical and immediately. Events are never dropped, duplicated or
  reordered.
* When a text or tool_use block ends, the masked text -> rehydrated text pairs are recorded in the
  remask memo (full text; for tool_use also the full JSON and every string leaf of the input).
* A rehydration failure never breaks the stream: the masked text goes through unchanged for that
  delta and ``stats["errors"]`` counts it. ``feed`` and ``finish`` never raise.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from core.client_guard.engine import SessionMap

# Longest text that could still grow into a token: 1-5 capitals, then optionally "_", up to six
# digits and a case suffix (U / L). Longest possible tail = 5 + 1 + 6 + 1 = 13 characters.
_TAIL = re.compile(r"[A-Z]{1,5}(?:_\d{0,6}[UL]?)?$|[A-Za-z]{2,5}\\{0,2}_\d{0,6}[ULul]?$")
_TAIL_WINDOW = 13

_FRAME_END = re.compile(rb"\r?\n\r?\n")
_LINE_BREAK = re.compile(r"\r?\n")

_TEXT = "text"
_JSON = "json"

_DELTA_KEYS = {"text_delta": "text", "input_json_delta": "partial_json"}


class _Memo(Protocol):
    def remember(self, masked: str, unmasked: str) -> None: ...


@dataclass
class _Block:
    kind: str | None  # "text" | "json" (tool_use style) | None (passthrough)
    done: str = ""  # masked text already converted and emitted (DAX-literal context)
    held: str = ""  # incomplete token tail not emitted yet (masked)
    emitted: list[str] = field(default_factory=list)  # pieces Claude Code received
    failed: bool = False  # a rehydration error happened in this block: no memo entry


def _new_stats() -> dict[str, int]:
    return {"events": 0, "rehydrated_events": 0, "tokens": 0, "flushed_tails": 0, "errors": 0}


class SSERehydrator:
    """Stateful transformer for one streaming response."""

    def __init__(self, smap: SessionMap, memo: _Memo) -> None:
        self._smap = smap
        self._memo = memo
        self._buf = b""
        self._blocks: dict[int, _Block] = {}
        self._eol = "\n"
        self.stats: dict[str, int] = _new_stats()

    # ------------------------------------------------------------------ framing
    def feed(self, chunk: bytes) -> bytes:
        """Transform one chunk of the response body; returns the bytes to forward."""
        try:
            return self._feed(chunk)
        except Exception:  # noqa: BLE001 - the stream must survive any bug here
            self.stats["errors"] += 1
            buf, self._buf = self._buf, b""
            return buf + chunk if chunk else buf

    def _feed(self, chunk: bytes) -> bytes:
        if not chunk:
            return b""
        buf = self._buf + chunk if self._buf else chunk
        out: list[bytes] = []
        pos = 0
        for m in _FRAME_END.finditer(buf):
            raw = buf[pos : m.start()]  # noqa: E203
            delim = m.group(0)
            pos = m.end()
            if b"\r\n" in delim:
                self._eol = "\r\n"
            elif delim.startswith(b"\n"):
                self._eol = "\n"
            self.stats["events"] += 1
            try:
                out.append(self._event(raw, delim))
            except Exception:  # noqa: BLE001 - fall back to the untouched frame
                self.stats["errors"] += 1
                out.append(raw)
            out.append(delim)
        self._buf = buf[pos:]
        return b"".join(out)

    def finish(self) -> bytes:
        """End of response: held tails first (as complete events), then any unterminated bytes."""
        out: list[bytes] = []
        try:
            for index in list(self._blocks):
                blk = self._blocks.get(index)
                if blk is not None and blk.held:
                    out.append(self._flush_event(index, blk) + self._eol.encode() * 2)
        except Exception:  # noqa: BLE001
            self.stats["errors"] += 1
        self._blocks.clear()
        out.append(self._buf)
        self._buf = b""
        return b"".join(out)

    # ------------------------------------------------------------------ events
    def _event(self, raw: bytes, delim: bytes) -> bytes:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw
        lines = _LINE_BREAK.split(text)
        data_lines = [
            ln[6:] if ln.startswith("data: ") else ln[5:] for ln in lines if ln.startswith("data:")
        ]
        if not data_lines:
            return raw  # comment / keep-alive / unknown field only
        try:
            payload = json.loads("\n".join(data_lines))
        except ValueError:
            return raw
        if not isinstance(payload, dict):
            return raw
        kind = payload.get("type")
        if kind == "content_block_delta":
            changed = self._on_delta(payload)
        elif kind == "content_block_start":
            changed = self._on_start(payload)
        elif kind == "content_block_stop":
            return self._on_stop(payload, raw, delim)
        elif kind in ("message_delta", "message_stop"):
            return self._flush_all(delim) + raw
        else:
            return raw
        if not changed:
            return raw
        self.stats["rehydrated_events"] += 1
        return self._rebuild(lines, payload)

    def _rebuild(self, lines: list[str], payload: dict[str, Any]) -> bytes:
        data = "data: " + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        out: list[str] = []
        placed = False
        for ln in lines:
            if ln.startswith("data:"):
                if not placed:
                    out.append(data)
                    placed = True
            else:
                out.append(ln)
        return self._eol.join(out).encode("utf-8")

    # ------------------------------------------------------------------ conversion
    def _unmask(self, blk: _Block, text: str) -> str:
        """Rehydrate ``text`` (already cut at a safe boundary); on failure return it unchanged."""
        if not text:
            return text
        ctx = "json" if blk.kind == _JSON else "plain"
        try:
            out, n = self._smap.unmask_text(text, ctx, prefix=blk.done)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001 - masked text goes through, the stream continues
            self.stats["errors"] += 1
            blk.failed = True
            return text
        self.stats["tokens"] += n
        return out

    def _convert(self, blk: _Block, piece: str) -> str:
        """Rehydrate ``piece`` of a block, holding back an incomplete token tail."""
        full = blk.held + piece
        m = _TAIL.search(full, max(0, len(full) - _TAIL_WINDOW))
        cut = m.start() if m else len(full)
        ready, blk.held = full[:cut], full[cut:]
        out = self._unmask(blk, ready)
        blk.done += ready
        blk.emitted.append(out)
        return out

    def _flush_event(self, index: int, blk: _Block) -> bytes:
        """Synthesised delta carrying the held-back tail (the only event ever added)."""
        held, blk.held = blk.held, ""
        out = self._unmask(blk, held)
        blk.done += held
        blk.emitted.append(out)
        self.stats["flushed_tails"] += 1
        key = "partial_json" if blk.kind == _JSON else "text"
        payload = {
            "type": "content_block_delta",
            "index": index,
            "delta": {
                "type": "input_json_delta" if blk.kind == _JSON else "text_delta",
                key: out,
            },
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return self._eol.join(["event: content_block_delta", f"data: {body}"]).encode("utf-8")

    def _flush_all(self, delim: bytes) -> bytes:
        """Flush every held tail before an event that ends the message."""
        out: list[bytes] = []
        for index, blk in list(self._blocks.items()):
            if blk.held:
                out.append(self._flush_event(index, blk) + delim)
        self._blocks.clear()
        return b"".join(out)

    # ------------------------------------------------------------------ block events
    def _on_start(self, payload: dict[str, Any]) -> bool:
        block = payload.get("content_block")
        index = payload.get("index")
        if not isinstance(block, dict) or not isinstance(index, int):
            return False
        btype = block.get("type")
        if btype == _TEXT:
            blk = self._blocks[index] = _Block(_TEXT)
            text = block.get("text")
            if isinstance(text, str) and text:
                new = self._convert(blk, text)
                block["text"] = new
                return new != text
            return False
        if isinstance(btype, str) and btype.endswith("tool_use"):
            blk = self._blocks[index] = _Block(_JSON)
            name = block.get("name")
            if isinstance(name, str) and "_" in name:
                new_name = self._unmask(_Block(_TEXT), name)  # a decoded string, not JSON text
                block["name"] = new_name
                return new_name != name
            return False
        self._blocks[index] = _Block(None)  # thinking and everything else: untouched bytes
        return False

    def _on_delta(self, payload: dict[str, Any]) -> bool:
        index = payload.get("index")
        delta = payload.get("delta")
        if not isinstance(delta, dict) or not isinstance(index, int):
            return False
        key = _DELTA_KEYS.get(delta.get("type", ""))
        if key is None:
            return False  # thinking / signature / citations / unknown: untouched bytes
        piece = delta.get(key)
        if not isinstance(piece, str):
            return False
        want = _JSON if key == "partial_json" else _TEXT
        blk = self._blocks.get(index)
        if blk is None or blk.kind != want:
            blk = self._blocks[index] = _Block(want)
        out = self._convert(blk, piece)
        delta[key] = out
        return out != piece

    def _on_stop(self, payload: dict[str, Any], raw: bytes, delim: bytes) -> bytes:
        index = payload.get("index")
        blk = self._blocks.pop(index, None) if isinstance(index, int) else None
        if blk is None or not isinstance(index, int) or blk.kind is None:
            return raw
        pre = b""
        if blk.held:
            pre = self._flush_event(index, blk) + delim
        try:
            self._remember(blk)
        except Exception:  # noqa: BLE001 - the memo is an optimisation, never a failure
            self.stats["errors"] += 1
        return pre + raw

    def _remember(self, blk: _Block) -> None:
        if blk.failed:
            return
        masked = blk.done
        unmasked = "".join(blk.emitted)
        self._memo.remember(masked, unmasked)
        if blk.kind != _JSON:
            return
        try:
            tree = json.loads(masked or "{}")
        except ValueError:
            return
        # Fills the memo with one entry per changed string leaf; the engine counters were already
        # advanced while streaming, so they are restored.
        saved = dict(self._smap.stats)
        try:
            self._smap.unmask_tree(tree, on_string=self._memo.remember)
        finally:
            self._smap.stats.update(saved)
