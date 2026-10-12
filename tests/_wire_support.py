"""Helpers of the wire-adapter tests (not a test module). Synthetic names only.

* ``load_fixture`` / ``make_state`` / ``token_of``: the sanitized wire fixtures and a gateway state
  that knows the synthetic clients of ``tests/fixtures/masking/synthetic_clients.txt``.
* The recorded streams are UNMASKED (a capture session without masking). ``mask_stream`` turns one
  into the stream a masked chat would see: every synthetic name becomes its pseudonym, and the text
  is spread over the same events again, the recorded event boundaries mapped through the
  replacement, so a boundary inside a name lands inside its pseudonym (the split-token case).
* ``copilot_slots`` / ``responses_slots`` / ``chatgpt_slots`` find the streamed text fields of an event
  list, the way each protocol defines them; ``stream_text`` joins them per field.
* The fixtures recorded in the lab session of 2026-10-07 hold the lab gateway's pseudonyms
  (``LAB_TOKENS``), which a test state cannot reproduce (its HMAC key is not here): ``relabel_stream`` /
  ``relabel_request`` swap them for the pseudonyms of the test state, keeping every recorded event
  boundary (a boundary inside a pseudonym stays inside the new pseudonym).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from client_gateway.adapters.streams import decode_stream, encode_stream
from client_gateway.detectors import parse_client_list
from client_gateway.state import GatewayState

FIXTURES = Path(__file__).parent / "fixtures" / "masking"
WIRE = FIXTURES / "wire"
MARKER = "fvpbi-0123456789abcdef"
NAME = "Zorblax Holdings NV"
NAME2 = "Quillfeather Logistics BV"
NAME3 = "Brambleton Fakeworks SA"
# the pseudonyms of the lab session -> the synthetic client each one stands for
LAB_TOKENS = {"CUS_61729": NAME, "CUS_64247": NAME2, "CUS_43438": NAME3}

Slot = tuple[Any, Any, str]  # (field id, dict holding the string, its key)


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((WIRE / f"{name}.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def body_bytes(fixture: dict[str, Any]) -> bytes:
    """The recorded request body as the wire carries it (compact JSON)."""
    return json.dumps(
        fixture["request"]["body"], ensure_ascii=False, separators=(",", ":")
    ).encode()


def synthetic_clients() -> dict[str, list[str]]:
    lines = [
        ln.strip()
        for ln in (FIXTURES / "synthetic_clients.txt").read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    return parse_client_list(lines)


def make_state(tmp_path: Path, policy: str = "withhold") -> GatewayState:
    state = GatewayState(home=tmp_path, default_mode="on", images_policy=policy)
    for prefix, values in synthetic_clients().items():
        state.smap.add_values(prefix, values, "test")
    return state


def token_of(state: GatewayState, value: str) -> str:
    return state.smap.mask_text(value)[0]


def all_names() -> list[str]:
    return [v for values in synthetic_clients().values() for v in values]


def strings_of(obj: Any) -> list[str]:
    """Every string leaf and dict key of a JSON tree."""
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, list):
        return [s for v in obj for s in strings_of(v)]
    if isinstance(obj, dict):
        return [s for k, v in obj.items() for s in strings_of(k) + strings_of(v)]
    return []


def same_shape(a: Any, b: Any) -> bool:
    """Same keys and list lengths everywhere, equal non-string values (strings may differ)."""
    if isinstance(a, dict) and isinstance(b, dict):
        return list(a) == list(b) and all(same_shape(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same_shape(x, y) for x, y in zip(a, b))
    if isinstance(a, str) and isinstance(b, str):
        return True
    return type(a) is type(b) and a == b


# ---------------------------------------------------------------------- SSE events of a fixture


@dataclass
class Ev:
    lines: list[str]
    data_at: int | None  # index of the ``data:`` line in ``lines``
    payload: Any  # parsed JSON of the data line, or None


def parse_chunks(chunks: list[str]) -> list[list[Ev]]:
    out: list[list[Ev]] = []
    for chunk in chunks:
        events = []
        for frame in chunk.split("\n\n"):
            if not frame:
                continue
            lines = frame.split("\n")
            at = next((i for i, ln in enumerate(lines) if ln.startswith("data:")), None)
            payload = None
            if at is not None:
                try:
                    payload = json.loads(lines[at][5:])
                except ValueError:
                    payload = None
            events.append(Ev(lines, at, payload))
        out.append(events)
    return out


def render_event(ev: Ev) -> str:
    lines = list(ev.lines)
    if ev.data_at is not None and ev.payload is not None:
        lines[ev.data_at] = "data: " + json.dumps(
            ev.payload, ensure_ascii=False, separators=(",", ":")
        )
    return "\n".join(lines) + "\n\n"


def render_chunks(chunks: list[list[Ev]]) -> list[str]:
    return ["".join(render_event(ev) for ev in events) for events in chunks]


def copilot_slots(events: list[Ev]) -> list[Slot]:
    out: list[Slot] = []
    for ev in events:
        if not isinstance(ev.payload, dict):
            continue
        for choice in ev.payload.get("choices") or []:
            index = choice.get("index", 0)
            delta = choice.get("delta") or {}
            for field in ("content", "reasoning_text"):
                if isinstance(delta.get(field), str):
                    out.append(((index, field), delta, field))
            for call in delta.get("tool_calls") or []:
                fn = call.get("function") or {}
                if isinstance(fn.get("arguments"), str):
                    out.append(((index, "arguments", call.get("index", 0)), fn, "arguments"))
    return out


RESPONSES_DELTAS = {  # event type -> (field, key of the second index)
    "response.output_text.delta": ("text", "content_index"),
    "response.refusal.delta": ("refusal", "content_index"),
    "response.reasoning_summary_text.delta": ("summary", "summary_index"),
    "response.reasoning_text.delta": ("reasoning", "content_index"),
    "response.function_call_arguments.delta": ("arguments", ""),
}


def responses_slots(events: list[Ev]) -> list[Slot]:
    out: list[Slot] = []
    for ev in events:
        payload = ev.payload
        if not isinstance(payload, dict) or not isinstance(payload.get("delta"), str):
            continue
        spec = RESPONSES_DELTAS.get(payload.get("type"))
        if spec is not None:
            field, second = spec
            sub = payload.get(second, 0) if second else 0
            out.append(((field, payload.get("output_index", 0), sub), payload, "delta"))
    return out


def chatgpt_slots(events: list[Ev]) -> list[Slot]:
    out: list[Slot] = []
    path: list[Any] = [None]

    def visit(op: dict[str, Any]) -> None:
        value, kind, at = op.get("v"), op.get("o"), op.get("p") or None
        if kind == "patch" and isinstance(value, list):
            for sub in value:
                if isinstance(sub, dict):
                    visit(sub)
        elif isinstance(value, str) and (kind == "append" or (kind is None and at is None)):
            path[0] = at or path[0]
            out.append((path[0], op, "v"))
        elif kind == "append":
            path[0] = at or path[0]

    for ev in events:
        if isinstance(ev.payload, dict) and ev.payload.get("type") is None and "v" in ev.payload:
            visit(ev.payload)
    return out


def stream_text(chunks: list[str], slots_fn: Callable[[list[Ev]], list[Slot]]) -> dict[Any, str]:
    """The text of every streamed field of a stream (what a client would display), per field."""
    events = [ev for chunk in parse_chunks(chunks) for ev in chunk]
    out: dict[Any, str] = {}
    for fid, container, key in slots_fn(events):
        out[fid] = out.get(fid, "") + container[key]
    return out


def replace_with_spans(
    full: str, tokens: dict[str, str]
) -> tuple[str, list[tuple[int, int, int, int]]]:
    """``full`` with each name replaced by its token; spans are (old start, old end, new start,
    new end) of every replacement."""
    out: list[str] = []
    spans: list[tuple[int, int, int, int]] = []
    pos = 0
    new_len = 0
    hits = sorted((i, name) for name in tokens for i in _find_all(full, name))
    for start, name in hits:
        if start < pos:
            continue
        out.append(full[pos:start])
        new_len += start - pos
        out.append(tokens[name])
        spans.append((start, start + len(name), new_len, new_len + len(tokens[name])))
        new_len += len(tokens[name])
        pos = start + len(name)
    out.append(full[pos:])
    return "".join(out), spans


def _find_all(text: str, needle: str) -> list[int]:
    out, at = [], text.find(needle)
    while at >= 0:
        out.append(at)
        at = text.find(needle, at + 1)
    return out


def map_offset(offset: int, spans: list[tuple[int, int, int, int]]) -> int:
    """Where an offset of the original text lands in the replaced text. An offset inside a name
    lands inside its token."""
    shift = 0
    for old_s, old_e, new_s, new_e in spans:
        if offset <= old_s:
            break
        if offset < old_e:
            inside = (offset - old_s) * (new_e - new_s) // (old_e - old_s)
            return new_s + min(max(inside, 1), new_e - new_s - 1)
        shift = new_e - old_e
    return offset + shift


def mask_stream(
    chunks: list[str],
    slots_fn: Callable[[list[Ev]], list[Slot]],
    tokens: dict[str, str],
) -> tuple[list[str], dict[Any, str]]:
    """The masked version of a recorded stream and the unmasked text it must give back per field."""
    parsed = parse_chunks(chunks)
    events = [ev for chunk in parsed for ev in chunk]
    groups: dict[Any, list[tuple[Any, str]]] = {}
    for fid, container, key in slots_fn(events):
        groups.setdefault(fid, []).append((container, key))
    expected: dict[Any, str] = {}
    for fid, slots in groups.items():
        pieces = [container[key] for container, key in slots]
        full = "".join(pieces)
        masked, spans = replace_with_spans(full, tokens)
        cuts = [0]
        for piece in pieces:
            cuts.append(cuts[-1] + len(piece))
        mapped = [map_offset(c, spans) for c in cuts]
        mapped[-1] = len(masked)
        for (container, key), start, end in zip(slots, mapped, mapped[1:]):
            container[key] = masked[start:end]
        expected[fid] = full
    return render_chunks(parsed), expected


# ---------------------------------------------------------------------- wire encoding


def to_wire(text_chunks: list[str], encoding: str) -> list[bytes]:
    """The chunks as the upstream would send them: one encoder over the whole stream."""
    enc = encode_stream(encoding)
    out = [enc.feed(c.encode("utf-8")) for c in text_chunks]
    out.append(enc.finish())
    return [o for o in out if o]


def from_wire(chunks: list[bytes], encoding: str) -> str:
    dec = decode_stream(encoding)
    data = b"".join(dec.feed(c) for c in chunks) + dec.finish()
    return data.decode("utf-8")


def resplit(data: bytes, cuts: list[int]) -> list[bytes]:
    """``data`` cut at the given offsets (sorted, deduplicated, inside the data)."""
    points = sorted({c for c in cuts if 0 < c < len(data)})
    return [data[a:b] for a, b in zip([0, *points], [*points, len(data)])]


# ---------------------------------------------------------------------- lab pseudonyms


def lab_tokens_of(state: GatewayState) -> dict[str, str]:
    """The lab pseudonym -> the pseudonym THIS state gives the same synthetic client."""
    return {lab: token_of(state, name) for lab, name in LAB_TOKENS.items()}


def _swap(text: str, mapping: dict[str, str]) -> str:
    for old, new in mapping.items():
        text = text.replace(old, new)
    return text


def relabel_request(body: bytes, state: GatewayState) -> bytes:
    """A recorded request body with the lab pseudonyms swapped for the state's own."""
    return _swap(body.decode("utf-8"), lab_tokens_of(state)).encode("utf-8")


def relabel_stream(
    chunks: list[str], slots_fn: Callable[[list[Ev]], list[Slot]], state: GatewayState
) -> tuple[list[str], dict[Any, str]]:
    """A recorded stream (lab pseudonyms) as ``state`` would see it, every recorded boundary kept,
    and the text each streamed field must give back once rehydrated (the synthetic names)."""
    ours = lab_tokens_of(state)
    masked, expected = mask_stream(chunks, slots_fn, ours)
    masked = [_swap(chunk, ours) for chunk in masked]  # the whole-object events carry them too
    return masked, {fid: _swap(text, LAB_TOKENS) for fid, text in expected.items()}
