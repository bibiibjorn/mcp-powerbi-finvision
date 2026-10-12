"""client_gateway.sse / client_gateway.memo: rehydrating one streamed /v1/messages response.

Synthetic values only. The rehydrator is pure (``feed(bytes) -> bytes``), so no network here.
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any

import pytest

from client_gateway.memo import RemaskMemo
from client_gateway.sse import SSERehydrator
from core.client_guard.engine import SessionMap

FAMILY = "Zorbax Holding"
PERSON = "Marie Quillon"
QUOTED = 'Jan "JJ" Smith\\Sons'  # a double quote and a backslash in one value
APOS = "O'Brien Trust"
UNI = "Café Délice"


def make_map() -> SessionMap:
    smap = SessionMap()
    smap.add_values("CUS", [FAMILY, PERSON, QUOTED, UNI], "test")
    smap.add_values("PER", [APOS], "test")
    return smap


def tok(smap: SessionMap, value: str) -> str:
    return smap.mask_text(value)[0]


def frame(name: str, data: dict[str, Any], eol: str = "\n") -> bytes:
    body = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    return f"event: {name}{eol}data: {body}{eol}{eol}".encode()


def start(index: int, block: dict[str, Any], eol: str = "\n") -> bytes:
    return frame(
        "content_block_start",
        {"type": "content_block_start", "index": index, "content_block": block},
        eol,
    )


def delta(index: int, kind: str, key: str, value: str, eol: str = "\n") -> bytes:
    return frame(
        "content_block_delta",
        {"type": "content_block_delta", "index": index, "delta": {"type": kind, key: value}},
        eol,
    )


def text_delta(index: int, value: str, eol: str = "\n") -> bytes:
    return delta(index, "text_delta", "text", value, eol)


def json_delta(index: int, value: str, eol: str = "\n") -> bytes:
    return delta(index, "input_json_delta", "partial_json", value, eol)


def stop(index: int, eol: str = "\n") -> bytes:
    return frame("content_block_stop", {"type": "content_block_stop", "index": index}, eol)


def message_start(eol: str = "\n") -> bytes:
    msg = {"id": "msg_1", "type": "message", "role": "assistant", "content": []}
    return frame("message_start", {"type": "message_start", "message": msg}, eol)


def message_end(eol: str = "\n") -> bytes:
    return frame(
        "message_delta",
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {}},
        eol,
    ) + frame("message_stop", {"type": "message_stop"}, eol)


def text_start(index: int, eol: str = "\n") -> bytes:
    return start(index, {"type": "text", "text": ""}, eol)


def run(rehydrator: SSERehydrator, chunks: list[bytes]) -> bytes:
    return b"".join(rehydrator.feed(c) for c in chunks) + rehydrator.finish()


def new(smap: SessionMap | None = None) -> tuple[SSERehydrator, SessionMap, RemaskMemo]:
    smap = smap or make_map()
    memo = RemaskMemo()
    return SSERehydrator(smap, memo), smap, memo


def parse(stream: bytes) -> list[dict[str, Any]]:
    """Events of an output stream: ``{"raw": bytes, "data": dict | None}``."""
    events: list[dict[str, Any]] = []
    for raw in re.split(rb"\r?\n\r?\n", stream):
        if not raw:
            continue
        data = None
        for line in raw.decode().splitlines():
            if line.startswith("data:"):
                data = json.loads(line[5:].lstrip(" "))
        events.append({"raw": raw, "data": data})
    return events


def assembled(stream: bytes) -> dict[int, str]:
    """Concatenated text / partial_json per block index."""
    out: dict[int, str] = {}
    for ev in parse(stream):
        d = ev["data"]
        if d and d.get("type") == "content_block_delta":
            dl = d["delta"]
            piece = dl.get("text", dl.get("partial_json", ""))
            out[d["index"]] = out.get(d["index"], "") + piece
    return out


def text_stream(smap: SessionMap, eol: str = "\n") -> tuple[bytes, str]:
    """A scripted text response: bytes on the wire and the text it must become."""
    fam, per = tok(smap, FAMILY), tok(smap, PERSON)
    pieces = [
        "Hello, ",
        fam[:2],
        fam[2:6],
        fam[6:] + " and ",
        per[:5],
        per[5:] + ", it is fine ",
        fam,
        ".",
    ]
    wire = message_start(eol) + text_start(0, eol)
    wire += b"".join(text_delta(0, p, eol) for p in pieces) + stop(0, eol) + message_end(eol)
    return wire, f"Hello, {FAMILY} and {PERSON}, it is fine {FAMILY}."


# --------------------------------------------------------------------------------------------
# Text rehydration and chunk splits
# --------------------------------------------------------------------------------------------


def test_text_rehydrated_whole_chunk() -> None:
    rh, smap, _ = new()
    wire, expected = text_stream(smap)
    out = run(rh, [wire])
    assert assembled(out)[0] == expected
    assert tok(smap, FAMILY) not in out.decode()
    assert rh.stats["errors"] == 0


def test_every_byte_split_gives_identical_output() -> None:
    rh, smap, _ = new()
    wire, expected = text_stream(smap)
    reference = run(new(smap)[0], [wire])
    assert assembled(reference)[0] == expected
    for cut in range(len(wire) + 1):
        out = run(new(smap)[0], [wire[:cut], wire[cut:]])
        assert out == reference, f"split at {cut}"


def test_byte_by_byte_feed() -> None:
    _, smap, _ = new()
    wire, expected = text_stream(smap)
    reference = run(new(smap)[0], [wire])
    out = run(new(smap)[0], [wire[i : i + 1] for i in range(len(wire))])  # noqa: E203
    assert out == reference
    assert assembled(out)[0] == expected


def test_token_across_three_deltas() -> None:
    rh, smap, _ = new()
    fam = tok(smap, FAMILY)
    wire = (
        text_start(0)
        + text_delta(0, "see " + fam[:1])
        + text_delta(0, fam[1:5])
        + text_delta(0, fam[5:] + " now")
        + stop(0)
    )
    out = run(rh, [wire])
    assert assembled(out)[0] == f"see {FAMILY} now"
    assert len(parse(out)) == len(parse(wire))  # no tail left: nothing was added


def test_token_split_with_case_suffix() -> None:
    rh, smap, _ = new()
    upper = tok(smap, FAMILY.upper())
    assert upper.endswith("U")
    wire = text_start(0) + text_delta(0, "x " + upper[:-1]) + text_delta(0, "U!") + stop(0)
    out = run(rh, [wire])
    assert assembled(out)[0] == f"x {FAMILY.upper()}!"


def test_tail_just_prefix_and_digit() -> None:
    rh, smap, _ = new()
    fam = tok(smap, FAMILY)  # CUS_0001
    head, rest = fam[:5], fam[5:]  # "CUS_0" + "001"
    wire = text_start(0) + text_delta(0, "a " + head) + text_delta(0, rest + " b") + stop(0)
    assert assembled(run(rh, [wire]))[0] == f"a {FAMILY} b"


def test_non_ascii_value_is_emitted_raw() -> None:
    rh, smap, _ = new()
    wire = text_start(0) + text_delta(0, f"at {tok(smap, UNI)} today") + stop(0)
    out = run(rh, [wire])
    assert assembled(out)[0] == f"at {UNI} today"
    assert UNI.encode() in out  # not \u-escaped


def test_model_invented_token_left_alone() -> None:
    rh, smap, _ = new()
    wire = text_start(0) + text_delta(0, "ghost CUS_9999 and ABC_0042.") + stop(0)
    out = run(rh, [wire])
    assert assembled(out)[0] == "ghost CUS_9999 and ABC_0042."
    assert out == wire  # nothing changed -> byte-identical


def test_held_tail_flushed_before_stop_is_the_only_added_event() -> None:
    rh, smap, _ = new()
    wire = (
        message_start() + text_start(0) + text_delta(0, "ends with CUS_") + stop(0) + message_end()
    )
    out = run(rh, [wire])
    src, dst = parse(wire), parse(out)
    assert len(dst) == len(src) + 1
    # order: start, delta, EXTRA delta, stop, ...
    kinds = [e["data"]["type"] for e in dst]
    assert kinds[:5] == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_delta",
        "content_block_stop",
    ]
    assert dst[3]["data"]["delta"] == {"type": "text_delta", "text": "CUS_"}
    assert assembled(out)[0] == "ends with CUS_"
    # untouched frames are byte-identical and in order
    keep = [e["raw"] for e in dst if e is not dst[2] and e is not dst[3]]
    assert keep == [e["raw"] for e in src if e is not src[2]]
    assert rh.stats["flushed_tails"] == 1


def test_capital_word_at_delta_end_is_held_then_released() -> None:
    rh, smap, _ = new()
    wire = text_start(0) + text_delta(0, "this is OK") + text_delta(0, " now") + stop(0)
    out = run(rh, [wire])
    assert assembled(out)[0] == "this is OK now"
    assert len(parse(out)) == len(parse(wire))


def test_message_end_flushes_held_tail_of_unclosed_block() -> None:
    rh, smap, _ = new()
    wire = text_start(0) + text_delta(0, "tail AB") + message_end()
    out = run(rh, [wire])
    types = [e["data"]["type"] for e in parse(out)]
    assert types[-3:] == ["content_block_delta", "message_delta", "message_stop"]
    assert assembled(out)[0] == "tail AB"


def test_start_event_text_is_rehydrated() -> None:
    rh, smap, _ = new()
    fam = tok(smap, FAMILY)
    wire = start(0, {"type": "text", "text": f"{fam} says"}) + stop(0)
    out = run(rh, [wire])
    assert parse(out)[0]["data"]["content_block"]["text"] == f"{FAMILY} says"


# --------------------------------------------------------------------------------------------
# tool_use / input_json_delta
# --------------------------------------------------------------------------------------------


def tool_stream(smap: SessionMap, eol: str = "\n") -> tuple[bytes, str]:
    q = tok(smap, QUOTED)
    full = '{"customer": "' + q + '", "note": "for ' + tok(smap, FAMILY) + '", "n": 3}'
    cuts = [0, 5, 17, 18, 19, 23, 30, 33, 36, len(full)]
    pieces = [full[a:b] for a, b in zip(cuts, cuts[1:]) if full[a:b]]
    assert "".join(pieces) == full
    wire = (
        message_start(eol)
        + start(0, {"type": "tool_use", "id": "t1", "name": "lookup", "input": {}}, eol)
        + b"".join(json_delta(0, p, eol) for p in pieces)
        + stop(0, eol)
        + message_end(eol)
    )
    return wire, full


def test_tool_use_json_with_quote_and_backslash() -> None:
    rh, smap, _ = new()
    wire, _ = tool_stream(smap)
    out = run(rh, [wire])
    parsed = json.loads(assembled(out)[0])
    assert parsed == {"customer": QUOTED, "note": f"for {FAMILY}", "n": 3}
    assert tok(smap, QUOTED) not in out.decode()


def test_tool_use_every_byte_split() -> None:
    _, smap, _ = new()
    wire, _ = tool_stream(smap)
    reference = run(new(smap)[0], [wire])
    assert json.loads(assembled(reference)[0])["customer"] == QUOTED
    for cut in range(len(wire) + 1):
        assert run(new(smap)[0], [wire[:cut], wire[cut:]]) == reference, f"split at {cut}"


def test_tool_use_memo_json_and_leaves() -> None:
    rh, smap, memo = new()
    wire, masked_json = tool_stream(smap)
    out = run(rh, [wire])
    rehydrated = assembled(out)[0]
    assert memo.lookup(rehydrated) == masked_json
    # string leaves of the parsed input: unmasked -> masked
    assert memo.lookup(QUOTED) == tok(smap, QUOTED)
    assert memo.lookup(f"for {FAMILY}") == f"for {tok(smap, FAMILY)}"
    assert memo.lookup("3") is None


def test_tool_use_memo_does_not_double_count_engine_stats() -> None:
    rh, smap, _ = new()
    wire, _ = tool_stream(smap)
    run(rh, [wire])
    hits = smap.stats["unmasked_hits"]
    assert hits == 2  # the two tokens, counted once while streaming


def test_tool_use_dax_quote_doubling_across_deltas() -> None:
    rh, smap, _ = new()
    smap2 = SessionMap()
    smap2.add_values("CUS", ['O"Brien Trust'], "test")
    t = smap2.mask_text('O"Brien Trust')[0]
    expr = 'CALCULATE([Sales], d[Name] = \\"' + t + '\\")'
    full = '{"expr":"' + expr + '"}'
    cut = full.index(t) + 3
    rh = SSERehydrator(smap2, RemaskMemo())
    wire = (
        start(0, {"type": "tool_use", "id": "t", "name": "dax", "input": {}})
        + json_delta(0, full[:cut])
        + json_delta(0, full[cut:])
        + stop(0)
    )
    out = run(rh, [wire])
    parsed = json.loads(assembled(out)[0])
    assert parsed["expr"] == 'CALCULATE([Sales], d[Name] = "O""Brien Trust")'


def test_tool_name_is_plain_rehydrated() -> None:
    rh, smap, _ = new()
    t = tok(smap, QUOTED)
    wire = start(0, {"type": "tool_use", "id": "t", "name": f"mcp__{t}", "input": {}}) + stop(0)
    out = run(rh, [wire])
    assert parse(out)[0]["data"]["content_block"]["name"] == f"mcp__{QUOTED}"


def test_tool_use_held_tail_flush_uses_input_json_delta() -> None:
    rh, smap, _ = new()
    wire = (
        start(0, {"type": "tool_use", "id": "t", "name": "x", "input": {}})
        + json_delta(0, '{"a":"ABC_')
        + stop(0)
    )
    out = run(rh, [wire])
    events = parse(out)
    assert events[2]["data"]["delta"] == {"type": "input_json_delta", "partial_json": "ABC_"}
    assert assembled(out)[0] == '{"a":"ABC_'


# --------------------------------------------------------------------------------------------
# Memo for text blocks
# --------------------------------------------------------------------------------------------


def test_text_block_recorded_in_memo() -> None:
    rh, smap, memo = new()
    wire, expected = text_stream(smap)
    run(rh, [wire])
    fam, per = tok(smap, FAMILY), tok(smap, PERSON)
    assert memo.lookup(expected) == f"Hello, {fam} and {per}, it is fine {fam}."


def test_plain_block_without_tokens_not_recorded() -> None:
    rh, _, memo = new()
    run(rh, [text_start(0) + text_delta(0, "nothing here") + stop(0)])
    assert len(memo) == 0


# --------------------------------------------------------------------------------------------
# Passthrough: thinking, ping, unknown, error, comments
# --------------------------------------------------------------------------------------------


def test_thinking_signature_ping_unknown_are_byte_identical() -> None:
    rh, smap, _ = new()
    fam = tok(smap, FAMILY)  # a token in thinking is NOT rehydrated
    sig = "EtQBCkYIBxgCKkDq" + "x" * 60
    wire = (
        message_start()
        + frame("ping", {"type": "ping"})
        + start(0, {"type": "thinking", "thinking": ""})
        + delta(0, "thinking_delta", "thinking", f"about {fam} CUS_")
        + delta(0, "signature_delta", "signature", sig)
        + stop(0)
        + b": keep-alive comment\n\n"
        + frame("brand_new_event", {"type": "brand_new_event", "x": fam})
        + frame("error", {"type": "error", "error": {"type": "overloaded_error", "message": fam}})
        + text_start(1)
        + text_delta(1, "ok")
        + stop(1)
        + message_end()
    )
    out = run(rh, [wire])
    assert out == wire


def test_thinking_between_text_deltas_does_not_disturb_hold_back() -> None:
    rh, smap, _ = new()
    fam = tok(smap, FAMILY)
    wire = (
        text_start(0)
        + text_delta(0, "a " + fam[:4])
        + delta(1, "thinking_delta", "thinking", "hmm")
        + text_delta(0, fam[4:] + "!")
        + stop(0)
    )
    out = run(rh, [wire])
    assert assembled(out)[0] == f"a {FAMILY}!"
    thinking = [
        e
        for e in parse(out)
        if e["data"]["type"] == "content_block_delta"
        and e["data"]["delta"]["type"] == "thinking_delta"
    ]
    assert thinking[0]["raw"] == parse(wire)[2]["raw"]


def test_interleaved_blocks_keep_separate_state() -> None:
    rh, smap, _ = new()
    fam, per = tok(smap, FAMILY), tok(smap, PERSON)
    wire = (
        text_start(0)
        + text_start(1)
        + text_delta(0, "A " + fam[:3])
        + text_delta(1, "B " + per[:3])
        + text_delta(0, fam[3:])
        + text_delta(1, per[3:])
        + stop(0)
        + stop(1)
    )
    out = run(rh, [wire])
    assert assembled(out) == {0: f"A {FAMILY}", 1: f"B {PERSON}"}


def test_garbage_frames_pass_through() -> None:
    rh, _, _ = new()
    wire = b"event: weird\ndata: {not json\n\n" + b"data: [1,2]\n\n" + b"just text\n\n"
    assert run(rh, [wire]) == wire


def test_invalid_utf8_frame_passes_through() -> None:
    rh, _, _ = new()
    wire = b"event: x\ndata: \xff\xfe\n\n"
    assert run(rh, [wire]) == wire


# --------------------------------------------------------------------------------------------
# Framing
# --------------------------------------------------------------------------------------------


def test_crlf_framing() -> None:
    rh, smap, _ = new()
    wire, expected = text_stream(smap, eol="\r\n")
    out = run(rh, [wire])
    assert assembled(out)[0] == expected
    assert b"\r\n\r\n" in out and b"\n\n" not in out.replace(b"\r\n\r\n", b"")
    reference = out
    for cut in range(len(wire) + 1):
        assert run(new(smap)[0], [wire[:cut], wire[cut:]]) == reference, f"split at {cut}"


def test_crlf_flush_event_uses_crlf() -> None:
    rh, smap, _ = new()
    wire = text_start(0, "\r\n") + text_delta(0, "tail CUS_", "\r\n") + stop(0, "\r\n")
    out = run(rh, [wire])
    flushed = parse(out)[2]["raw"]
    assert b"\r\n" in flushed and b"\n" not in flushed.replace(b"\r\n", b"")


def test_data_line_without_space_after_colon() -> None:
    rh, smap, _ = new()
    fam = tok(smap, FAMILY)
    body = json.dumps(
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": fam}},
        separators=(",", ":"),
    )
    wire = text_start(0) + f"event: content_block_delta\ndata:{body}\n\n".encode() + stop(0)
    assert assembled(run(rh, [wire]))[0] == FAMILY


def test_unterminated_trailing_bytes_are_returned_by_finish() -> None:
    rh, smap, _ = new()
    partial = b'event: content_block_delta\ndata: {"type":'
    out = rh.feed(text_start(0) + partial)
    assert out == text_start(0)
    assert rh.finish() == partial


def test_finish_flushes_held_tail_of_truncated_stream() -> None:
    rh, smap, _ = new()
    out = rh.feed(text_start(0) + text_delta(0, "cut CUS_"))
    out += rh.finish()
    assert assembled(out)[0] == "cut CUS_"
    assert parse(out)[-1]["data"]["delta"]["text"] == "CUS_"


def test_empty_stream() -> None:
    rh, _, _ = new()
    assert rh.feed(b"") == b""
    assert rh.finish() == b""
    assert rh.finish() == b""  # idempotent


def test_empty_dictionary_is_identity() -> None:
    rh = SSERehydrator(SessionMap(), RemaskMemo())
    wire = message_start() + text_start(0) + text_delta(0, "plain CUS_0001 text") + stop(0)
    assert run(rh, [wire]) == wire


def test_event_order_and_count_preserved() -> None:
    rh, smap, _ = new()
    wire, _ = text_stream(smap)
    out = run(rh, [wire])
    assert [e["data"]["type"] for e in parse(out)] == [e["data"]["type"] for e in parse(wire)]


# --------------------------------------------------------------------------------------------
# Failure handling
# --------------------------------------------------------------------------------------------


def test_rehydration_failure_passes_masked_text_and_counts() -> None:
    rh, smap, memo = new()
    fam = tok(smap, FAMILY)

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("synthetic")

    smap.unmask_text = boom  # type: ignore[method-assign]
    wire = text_start(0) + text_delta(0, f"x {fam} y") + text_delta(0, " z") + stop(0)
    out = run(rh, [wire])
    assert out == wire  # masked text unchanged, nothing dropped
    assert rh.stats["errors"] >= 1
    assert len(memo) == 0  # a failed block is not recorded


def test_memo_failure_does_not_break_stream() -> None:
    smap = make_map()

    class Bad:
        def remember(self, masked: str, unmasked: str) -> None:
            raise RuntimeError("memo down")

    rh = SSERehydrator(smap, Bad())
    wire, expected = text_stream(smap)
    out = run(rh, [wire])
    assert assembled(out)[0] == expected
    assert rh.stats["errors"] >= 1


def test_feed_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    rh, smap, _ = new()
    wire, _ = text_stream(smap)

    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("synthetic")

    monkeypatch.setattr(rh, "_event", boom)
    out = rh.feed(wire)
    assert out == wire  # frames forwarded untouched
    assert rh.stats["errors"] > 0


# --------------------------------------------------------------------------------------------
# Performance
# --------------------------------------------------------------------------------------------


def test_ten_thousand_deltas_under_one_second() -> None:
    rh, smap, _ = new()
    fam = tok(smap, FAMILY)
    pieces = []
    for i in range(10_000):
        pieces.append(f"word{i} " if i % 10 else f"{fam[:3]}")
        if i % 10 == 0:
            pieces.append(fam[3:] + " ")
    wire = text_start(0) + b"".join(text_delta(0, p) for p in pieces) + stop(0)
    chunks = [wire[i : i + 4096] for i in range(0, len(wire), 4096)]  # noqa: E203
    t0 = time.perf_counter()
    out = b"".join(rh.feed(c) for c in chunks) + rh.finish()
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0, f"{elapsed:.2f}s"
    assert assembled(out)[0].count(FAMILY) == 1000


# --------------------------------------------------------------------------------------------
# RemaskMemo
# --------------------------------------------------------------------------------------------


def test_memo_remember_lookup() -> None:
    m = RemaskMemo()
    m.remember("CUS_0001 x", "Zorbax x")
    assert m.lookup("Zorbax x") == "CUS_0001 x"
    assert m.lookup("other") is None
    m.remember("same", "same")  # unchanged strings are not stored
    m.remember("", "x")
    m.remember("x", "")
    assert len(m) == 1


def test_memo_is_bounded_and_evicts_oldest() -> None:
    m = RemaskMemo(max_entries=3)
    for i in range(5):
        m.remember(f"m{i}", f"u{i}")
    assert len(m) == 3
    assert m.lookup("u0") is None and m.lookup("u1") is None
    assert m.lookup("u4") == "m4"


def test_memo_default_bound_is_50k() -> None:
    m = RemaskMemo()
    for i in range(50_100):
        m.remember(f"m{i}", f"u{i}")
    assert len(m) == 50_000


def test_memo_refresh_moves_entry_to_newest() -> None:
    m = RemaskMemo(max_entries=2)
    m.remember("a", "A1")
    m.remember("b", "B1")
    m.remember("a2", "A1")  # refresh A1
    m.remember("c", "C1")  # evicts B1 (oldest), not A1
    assert m.lookup("A1") == "a2" and m.lookup("B1") is None


def test_memo_thread_safe() -> None:
    m = RemaskMemo(max_entries=1000)

    def work(k: int) -> None:
        for i in range(2000):
            m.remember(f"m{k}-{i}", f"u{k}-{i}")
            m.lookup(f"u{k}-{i}")

    threads = [threading.Thread(target=work, args=(k,)) for k in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(m) == 1000


# --------------------------------------------------------------------------------------------
# Five-digit stable pseudonyms (SessionMap(token_fn=...)), when the engine supports them
# --------------------------------------------------------------------------------------------


@pytest.mark.skipif(
    "token_fn" not in SessionMap.__init__.__code__.co_varnames,
    reason="engine without stable token_fn",
)
def test_five_digit_stable_tokens_every_byte_split() -> None:
    smap = SessionMap(token_fn=lambda p, k: 10_000 + (hash(k) % 90_000))  # type: ignore[call-arg]
    smap.add_values("CUS", [FAMILY, PERSON], "test")
    fam, per = tok(smap, FAMILY), tok(smap, PERSON)
    assert re.fullmatch(r"CUS_\d{5}", fam)
    wire = text_start(0) + text_delta(0, f"a {fam} b {per}") + text_delta(0, f" {fam}U.") + stop(0)
    reference = run(SSERehydrator(smap, RemaskMemo()), [wire])
    assert assembled(reference)[0] == f"a {FAMILY} b {PERSON} {FAMILY}U."
    for cut in range(len(wire) + 1):
        out = run(SSERehydrator(smap, RemaskMemo()), [wire[:cut], wire[cut:]])
        assert out == reference, f"split at {cut}"
