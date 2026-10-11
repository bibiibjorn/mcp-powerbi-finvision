"""Streaming plumbing of the wire adapters.

* ``decode_stream`` / ``encode_stream`` / ``decode_bytes`` / ``encode_bytes``: incremental
  gzip / deflate / br / zstd codecs (``identity`` is a passthrough). Encoders flush after every
  ``feed`` (gzip and deflate sync flush, brotli flush, zstd block flush), so a re-encoded stream
  delivers each event as soon as it was converted, like the upstream did.
* ``SSEFramer`` / ``parse_event`` / ``rebuild_event``: SSE frames over arbitrary chunk splits.
* ``TailHold``: split-safe rehydration of ONE text field. A trailing run that could still grow into
  a pseudonym (``CUS_4``, a lone ``NV``) is held back until the next piece of the same field; what
  is still held when the field ends is flushed as ONE extra piece (the same ``_TAIL`` rule as
  ``client_gateway.sse``).
* ``SSEPipeline``: the base of the stream rehydrators: decode, frame, convert each event, encode.
  A rehydration failure never breaks the stream: the masked text goes through for that event and
  ``stats["errors"]`` counts it (the user sees a pseudonym, never a leak). A body that cannot be
  decoded is forwarded unchanged from then on.

Nothing here logs or raises with input text.
"""

from __future__ import annotations

import json
import zlib
from typing import Any, Protocol

from client_gateway.sse import _FRAME_END, _LINE_BREAK, _TAIL, _TAIL_WINDOW
from core.client_guard.engine import SessionMap

SUPPORTED_ENCODINGS = ("identity", "gzip", "deflate", "br", "zstd")


class UnsupportedEncoding(ValueError):
    """A ``content-encoding`` this module cannot decode (fixed message)."""


def normalize_encoding(encoding: str | None) -> str:
    """Canonical name of a ``content-encoding`` header value; raises ``UnsupportedEncoding``."""
    value = (encoding or "").strip().lower()
    if value in ("", "identity"):
        return "identity"
    if value in ("gzip", "x-gzip"):
        return "gzip"
    if value in ("deflate", "br", "zstd"):
        return value
    raise UnsupportedEncoding("unsupported content-encoding")


class Codec(Protocol):
    def feed(self, data: bytes) -> bytes: ...

    def finish(self) -> bytes: ...


# ---------------------------------------------------------------------- decoders


class _Identity:
    def feed(self, data: bytes) -> bytes:
        return data

    def finish(self) -> bytes:
        return b""


class _ZlibDecoder:
    def __init__(self, wbits: int) -> None:
        self._d = zlib.decompressobj(wbits)

    def feed(self, data: bytes) -> bytes:
        return self._d.decompress(data)

    def finish(self) -> bytes:
        return self._d.flush()


class _DeflateDecoder:
    """HTTP ``deflate`` is the zlib format; some servers send a raw deflate stream instead."""

    def __init__(self) -> None:
        self._d: Any = None
        self._pre = b""

    def feed(self, data: bytes) -> bytes:
        if self._d is None:
            self._pre += data
            if len(self._pre) < 2:
                return b""
            data, self._pre = self._pre, b""
            self._d = zlib.decompressobj(zlib.MAX_WBITS)
            try:
                return self._d.decompress(data)
            except zlib.error:
                self._d = zlib.decompressobj(-zlib.MAX_WBITS)
        return self._d.decompress(data)  # type: ignore[no-any-return]

    def finish(self) -> bytes:
        return self._d.flush() if self._d is not None else b""  # type: ignore[no-any-return]


class _BrotliDecoder:
    def __init__(self) -> None:
        import brotli

        self._d = brotli.Decompressor()

    def feed(self, data: bytes) -> bytes:
        return self._d.process(data)  # type: ignore[no-any-return]

    def finish(self) -> bytes:
        return b""


class _ZstdDecoder:
    def __init__(self) -> None:
        import zstandard

        try:
            self._d = zstandard.ZstdDecompressor().decompressobj(read_across_frames=True)
        except TypeError:  # an older build without the keyword
            self._d = zstandard.ZstdDecompressor().decompressobj()

    def feed(self, data: bytes) -> bytes:
        return self._d.decompress(data)  # type: ignore[no-any-return]

    def finish(self) -> bytes:
        return b""


# ---------------------------------------------------------------------- encoders


class _ZlibEncoder:
    def __init__(self, wbits: int) -> None:
        self._c = zlib.compressobj(6, zlib.DEFLATED, wbits)

    def feed(self, data: bytes) -> bytes:
        if not data:
            return b""
        return self._c.compress(data) + self._c.flush(zlib.Z_SYNC_FLUSH)

    def finish(self) -> bytes:
        return self._c.flush(zlib.Z_FINISH)


class _BrotliEncoder:
    def __init__(self) -> None:
        import brotli

        self._c = brotli.Compressor(quality=5)

    def feed(self, data: bytes) -> bytes:
        if not data:
            return b""
        return self._c.process(data) + self._c.flush()  # type: ignore[no-any-return]

    def finish(self) -> bytes:
        return self._c.finish()  # type: ignore[no-any-return]


class _ZstdEncoder:
    def __init__(self) -> None:
        import zstandard

        self._z = zstandard
        self._c = zstandard.ZstdCompressor(level=3).compressobj()

    def feed(self, data: bytes) -> bytes:
        if not data:
            return b""
        return self._c.compress(data) + self._c.flush(self._z.COMPRESSOBJ_FLUSH_BLOCK)  # type: ignore[no-any-return]

    def finish(self) -> bytes:
        return self._c.flush(self._z.COMPRESSOBJ_FLUSH_FINISH)  # type: ignore[no-any-return]


def decode_stream(encoding: str | None) -> Codec:
    """Incremental decoder for a ``content-encoding``: ``feed(chunk) -> bytes``, ``finish()``."""
    name = normalize_encoding(encoding)
    if name == "gzip":
        return _ZlibDecoder(16 + zlib.MAX_WBITS)  # a stream: no end marker is required
    if name == "deflate":
        return _DeflateDecoder()
    if name == "br":
        return _BrotliDecoder()
    if name == "zstd":
        return _ZstdDecoder()
    return _Identity()


def encode_stream(encoding: str | None) -> Codec:
    """Incremental encoder for a ``content-encoding``; every ``feed`` is flushed."""
    name = normalize_encoding(encoding)
    if name == "gzip":
        return _ZlibEncoder(16 + zlib.MAX_WBITS)
    if name == "deflate":
        return _ZlibEncoder(zlib.MAX_WBITS)
    if name == "br":
        return _BrotliEncoder()
    if name == "zstd":
        return _ZstdEncoder()
    return _Identity()


def decode_bytes(data: bytes, encoding: str | None) -> bytes:
    codec = decode_stream(encoding)
    return codec.feed(data) + codec.finish()


def encode_bytes(data: bytes, encoding: str | None) -> bytes:
    codec = encode_stream(encoding)
    return codec.feed(data) + codec.finish()


# ---------------------------------------------------------------------- SSE framing


class SSEFramer:
    """Splits a byte stream into complete SSE frames (blank-line terminated, LF or CRLF)."""

    def __init__(self) -> None:
        self._buf = b""
        self.eol = "\n"

    def feed(self, data: bytes) -> list[tuple[bytes, bytes]]:
        """``[(frame, delimiter), ...]`` for every frame completed by ``data``."""
        buf = self._buf + data if self._buf else data
        frames: list[tuple[bytes, bytes]] = []
        pos = 0
        for m in _FRAME_END.finditer(buf):
            delim = m.group(0)
            if b"\r\n" in delim:
                self.eol = "\r\n"
            elif delim.startswith(b"\n"):
                self.eol = "\n"
            frames.append((buf[pos : m.start()], delim))  # noqa: E203
            pos = m.end()
        self._buf = buf[pos:]
        return frames

    def rest(self) -> bytes:
        """The unterminated bytes after the last frame (and forget them)."""
        out, self._buf = self._buf, b""
        return out


def parse_event(raw: bytes) -> tuple[list[str], str | None]:
    """``(lines, data text)`` of one frame; the data text is None without ``data:`` lines or when
    the frame is not UTF-8."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return [], None
    lines = _LINE_BREAK.split(text)
    data = [ln[6:] if ln.startswith("data: ") else ln[5:] for ln in lines if ln.startswith("data:")]
    return lines, ("\n".join(data) if data else None)


def rebuild_event(lines: list[str], payload: Any, eol: str) -> bytes:
    """The frame with its ``data:`` lines replaced by one line holding ``payload`` as JSON (the
    other lines, such as ``event: delta``, kept in place)."""
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
    return eol.join(out).encode("utf-8")


def frame_event(name: str | None, payload: Any, eol: str) -> bytes:
    """A new SSE frame (without the closing blank line) carrying ``payload`` as JSON."""
    data = "data: " + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return (f"event: {name}{eol}{data}" if name else data).encode("utf-8")


# ---------------------------------------------------------------------- split-safe hold-back


class TailHold:
    """Rehydrates the pieces of ONE streamed text field, holding back an incomplete token tail.

    ``feed(piece)`` returns the text to emit now; ``flush()`` converts and returns what is held
    (call it when the field ends, or before something else is emitted). ``masked`` is the full
    masked text seen so far and ``unmasked`` the text emitted so far: together they are what the
    remask memo records for the next request.
    """

    def __init__(self, smap: SessionMap, context: str = "plain") -> None:
        self._smap = smap
        self.context = context
        self.done = ""  # masked text converted and emitted (DAX-literal context for the engine)
        self.held = ""  # incomplete token tail not emitted yet (masked)
        self.emitted: list[str] = []
        self.failed = False  # a conversion error happened: the memo must not learn this pair
        self.errors = 0
        self.tokens = 0

    @property
    def masked(self) -> str:
        return self.done + self.held

    @property
    def unmasked(self) -> str:
        return "".join(self.emitted)

    def _unmask(self, text: str) -> str:
        if not text:
            return text
        try:
            out, n = self._smap.unmask_text(text, self.context, prefix=self.done)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001 - the masked text goes through, the stream continues
            self.failed = True
            self.errors += 1
            return text
        self.tokens += n
        return out

    def feed(self, piece: str) -> str:
        full = self.held + piece
        m = _TAIL.search(full, max(0, len(full) - _TAIL_WINDOW))
        cut = m.start() if m else len(full)
        ready, self.held = full[:cut], full[cut:]
        out = self._unmask(ready)
        self.done += ready
        self.emitted.append(out)
        return out

    def flush(self) -> str:
        held, self.held = self.held, ""
        out = self._unmask(held)
        self.done += held
        self.emitted.append(out)
        return out


# ---------------------------------------------------------------------- stream rehydrators


class StreamRehydrator(Protocol):
    """One streamed response: ``feed`` wire chunks, ``finish`` at the end; ``stats`` counts."""

    stats: dict[str, int]

    def feed(self, chunk: bytes) -> bytes: ...

    def finish(self) -> bytes: ...


def new_stats() -> dict[str, int]:
    return {
        "events": 0,
        "rehydrated_events": 0,
        "tokens": 0,
        "flushed_tails": 0,
        "errors": 0,
        "recognised": 0,  # events of a shape the rehydrator knows (none in a whole reply: R9)
    }


class PassthroughRehydrator:
    """Forwards every byte unchanged (a response nothing is converted in)."""

    def __init__(self, errors: int = 0) -> None:
        self.stats = new_stats()
        self.stats["errors"] = errors
        self.encoding_out = "identity"

    def feed(self, chunk: bytes) -> bytes:
        return chunk

    def finish(self) -> bytes:
        return b""


class SSEPipeline:
    """Base of the SSE rehydrators: decode, frame, convert each event, encode again.

    ``reencode=True`` (default): the converted stream goes out in the SAME ``content-encoding`` it
    came in with, so the front end changes no header except ``content-length`` (a streamed body is
    chunked anyway). ``reencode=False``: the output is plain bytes; the front end must then drop
    the ``content-encoding`` header (and any ``content-length``) before the first byte is sent.
    ``encoding_out`` tells which one applies.

    Subclasses implement ``_event(raw, delim) -> bytes`` (the frame to forward, WITHOUT its closing
    delimiter, optionally preceded by whole extra frames that end with ``delim``) and
    ``_end(delim) -> bytes`` (frames that flush held text at the end of the stream).
    """

    def __init__(self, content_encoding: str | None, reencode: bool = True) -> None:
        self.stats = new_stats()
        self._in = decode_stream(content_encoding)
        encoding = normalize_encoding(content_encoding)
        self.encoding_out = encoding if reencode else "identity"
        self._out = encode_stream(content_encoding) if reencode else _Identity()
        self._framer = SSEFramer()
        self._broken = False

    # -- hooks
    def _event(self, raw: bytes, delim: bytes) -> bytes:  # pragma: no cover - abstract
        return raw

    def _end(self, delim: bytes) -> bytes:  # pragma: no cover - abstract
        return b""

    @property
    def eol(self) -> str:
        return self._framer.eol

    # -- wire
    def _convert(self, data: bytes) -> bytes:
        out: list[bytes] = []
        for raw, delim in self._framer.feed(data):
            self.stats["events"] += 1
            try:
                out.append(self._event(raw, delim))
            except Exception:  # noqa: BLE001 - fall back to the untouched frame
                self.stats["errors"] += 1
                out.append(raw)
            out.append(delim)
        return b"".join(out)

    def feed(self, chunk: bytes) -> bytes:
        if self._broken or not chunk:
            return chunk if self._broken else b""
        try:
            data = self._in.feed(chunk)
        except Exception:  # noqa: BLE001 - undecodable upstream bytes: forward them unchanged
            self._broken = True
            self.stats["errors"] += 1
            return chunk
        return self._out.feed(self._convert(data))

    def finish(self) -> bytes:
        if self._broken:
            return b""
        out = b""
        try:
            data = self._in.finish()
            if data:
                out += self._convert(data)
            rest = self._framer.rest()
            delim = (self.eol * 2).encode()
            end = b""
            if rest.strip():
                self.stats["events"] += 1
                try:
                    out += self._event(rest, delim)  # unterminated last frame: no delimiter added
                except Exception:  # noqa: BLE001
                    self.stats["errors"] += 1
                    out += rest
                end = self._end(delim)
                if end:
                    out += delim  # close the unterminated frame before the extra ones
            else:
                out += rest
                end = self._end(delim)
            out += end
        except Exception:  # noqa: BLE001 - never raise at the end of a response
            self.stats["errors"] += 1
        return self._out.feed(out) + self._out.finish()
