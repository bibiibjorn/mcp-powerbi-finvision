"""client_gateway.adapters.openai_chat (VS Code Copilot): request masking and response rehydration,
driven by the sanitized wire fixtures. Synthetic names only; no network, sockets or processes."""

from __future__ import annotations

import base64
import json
import re
from typing import Any

import pytest

from client_gateway import images as _images
from client_gateway.adapters.base import MaskContext
from client_gateway.adapters.control import typed_command
from client_gateway.adapters.openai_chat import OpenAIChatAdapter
from client_gateway.adapters.streams import decode_stream
from client_gateway.masking import MaskingFailed
from client_gateway.memo import RemaskMemo
from tests._wire_support import (
    MARKER,
    NAME,
    NAME2,
    all_names,
    body_bytes,
    copilot_slots,
    from_wire,
    load_fixture,
    make_state,
    mask_stream,
    parse_chunks,
    resplit,
    same_shape,
    strings_of,
    stream_text,
    to_wire,
    token_of,
)

ADAPTER = OpenAIChatAdapter()
STREAMS = ["copilot_title_stream", "copilot_tool_call_stream", "copilot_tool_result_image"]
ENCODINGS = ["", "gzip", "br", "zstd", "deflate"]


@pytest.fixture()
def state(tmp_path):
    return make_state(tmp_path)


def mask(
    state, body: bytes, memo: RemaskMemo | None = None, **kw: Any
) -> tuple[bytes, MaskContext]:
    ctx = MaskContext(state, memo, marker=kw.pop("marker", MARKER), **kw)
    return ADAPTER.mask_request(body, ctx), ctx


def chat(messages: list[dict[str, Any]], **extra: Any) -> bytes:
    return json.dumps({"model": "m", "messages": messages, **extra}).encode()


def user(content: Any, **extra: Any) -> dict[str, Any]:
    return {"role": "user", "content": content, **extra}


# ---------------------------------------------------------------------------------- routing
def test_adapter_identity_and_chat_key():
    assert ADAPTER.name == "openai_chat"
    assert ADAPTER.chat_key(b"{}") is None and ADAPTER.url_chat_key("/chat/completions") is None


# ---------------------------------------------------------------------------------- request
def test_fixture_request_is_masked_everywhere_and_keeps_its_structure(state):
    fx = load_fixture("copilot_tool_result_image")
    original = fx["request"]["body"]
    out, ctx = mask(state, body_bytes(fx))
    masked = json.loads(out)
    assert not any(name in s for s in strings_of(masked) for name in all_names())
    assert ctx.marker_seen and MARKER not in out.decode()
    assert json.loads(masked["messages"][8]["content"])["_fv"] == "fvpbi"
    # structure: every key and every non-string value is the same, except the two image parts that
    # became text parts (remote attachment URLs cannot be read here)
    keep = [i for i, m in enumerate(original["messages"]) if not isinstance(m["content"], list)]
    for i in keep:
        assert same_shape(original["messages"][i], masked["messages"][i])
    assert [m["role"] for m in masked["messages"]] == [m["role"] for m in original["messages"]]
    for key in original:
        if key != "messages":
            assert masked[key] == original[key]  # settings and the tools list are untouched
    assert masked["messages"][4]["content"][0] == original["messages"][4]["content"][0]


def test_opaque_reasoning_ids_and_cache_control_are_untouched(state):
    fx = load_fixture("copilot_tool_result_image")
    original = fx["request"]["body"]["messages"]
    masked = json.loads(mask(state, body_bytes(fx))[0])["messages"]
    assert masked[7]["reasoning_opaque"] == original[7]["reasoning_opaque"]
    assert masked[7]["tool_calls"][0]["id"] == original[7]["tool_calls"][0]["id"]
    assert masked[8]["tool_call_id"] == original[8]["tool_call_id"]
    assert masked[1]["copilot_cache_control"] == {"type": "ephemeral"}
    assert masked[7]["tool_calls"][0]["function"]["name"] == "mcp_masking-lab_lab_customer_revenue"


def test_tool_call_arguments_are_parsed_masked_and_stay_valid_json(state):
    fx = load_fixture("copilot_tool_result_image")
    masked = json.loads(mask(state, body_bytes(fx))[0])["messages"]
    args = json.loads(masked[7]["tool_calls"][0]["function"]["arguments"])
    assert args == {"customer": token_of(state, NAME2)}
    assert masked[7]["reasoning_text"].count(token_of(state, NAME2)) == 2  # plain text field too


def test_tool_call_argument_keys_and_nested_values_are_masked(state):
    args = json.dumps({"filter": {NAME: [NAME2, 3]}, "n": 1})
    body = chat([{"role": "assistant", "content": None, "tool_calls": [
        {"id": "t", "type": "function", "function": {"name": "f", "arguments": args}}]}])  # fmt: skip
    masked = json.loads(mask(state, body)[0])["messages"][0]
    call = json.loads(masked["tool_calls"][0]["function"]["arguments"])
    assert call == {"filter": {token_of(state, NAME): [token_of(state, NAME2), 3]}, "n": 1}
    assert masked["content"] is None


def test_arguments_without_a_client_value_stay_byte_identical(state):
    args = '{"customer":   "nobody",  "n": 1}'
    body = chat([{"role": "assistant", "content": "", "tool_calls": [
        {"id": "t", "type": "function", "function": {"name": "f", "arguments": args}}]}])  # fmt: skip
    masked = json.loads(mask(state, body)[0])["messages"][0]
    assert masked["tool_calls"][0]["function"]["arguments"] == args


def test_unparseable_arguments_are_masked_as_text(state):
    body = chat([{"role": "assistant", "content": "", "tool_calls": [
        {"id": "t", "type": "function", "function": {"name": "f", "arguments": f'{{"c": "{NAME}'}}]}])  # fmt: skip
    masked = json.loads(mask(state, body)[0])["messages"][0]
    assert NAME not in masked["tool_calls"][0]["function"]["arguments"]


def test_tool_role_content_is_masked_and_the_marker_is_removed(state):
    content = json.dumps({"_fv": MARKER, "rows": [{"Customer": NAME}]})
    out, ctx = mask(state, chat([{"role": "tool", "tool_call_id": "t", "content": content}]))
    tool = json.loads(json.loads(out)["messages"][0]["content"])
    assert tool == {"_fv": "fvpbi", "rows": [{"Customer": token_of(state, NAME)}]}
    assert ctx.marker_seen and MARKER.encode() not in out


def test_the_marker_is_removed_from_anywhere_in_the_body(state):
    body = chat([user("hi")], metadata_note=f"x {MARKER}")  # not in a message string
    out, ctx = mask(state, body)
    assert MARKER.encode() not in out and ctx.marker_seen


def test_system_text_uses_the_developer_text_rule(state):
    state.smap.add_values("DIM", ["status"], "test")
    system = {"role": "system", "content": f"Keep the status. Never reveal {NAME}."}
    cell = user("status")
    out = json.loads(mask(state, chat([system, cell]))[0])["messages"]
    assert out[0]["content"].startswith("Keep the status.") and NAME not in out[0]["content"]
    assert out[1]["content"] != "status"  # the same word as a user cell is masked


def test_unknown_message_keys_are_masked_too(state):
    out = json.loads(mask(state, chat([user("hi", name=NAME, extra={"k": [NAME2]})]))[0])
    message = out["messages"][0]
    assert NAME not in message["name"] and NAME2 not in json.dumps(message["extra"])


def test_a_client_value_in_a_verbatim_setting_fails_the_request_closed(state):
    with pytest.raises(MaskingFailed) as err:
        mask(state, chat([user("hi")], user=NAME))
    assert err.value.code == "residual_leak" and NAME not in str(err.value)
    mask(state, chat([user("hi")], tools=[{"function": {"description": f"about {NAME}"}}]))


def test_same_input_gives_identical_bytes(state):
    body = body_bytes(load_fixture("copilot_tool_result_image"))
    assert mask(state, body)[0] == mask(state, body, RemaskMemo())[0]


def test_the_memo_replays_what_the_model_wrote(state):
    memo = RemaskMemo()
    token = token_of(state, NAME)
    memo.remember(f"{token} is a client of ours", f"{NAME} is a client of ours")
    memo.remember(f'{{"c":"{token}"}}', f'{{"c": "{NAME}"}}')
    body = chat([
        {"role": "assistant", "content": f"{NAME} is a client of ours", "tool_calls": [
            {"id": "t", "type": "function", "function": {"name": "f", "arguments": f'{{"c": "{NAME}"}}'}}]}
    ])  # fmt: skip
    message = json.loads(mask(state, body, memo)[0])["messages"][0]
    assert message["content"] == f"{token} is a client of ours"
    assert message["tool_calls"][0]["function"]["arguments"] == f'{{"c":"{token}"}}'


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (b"not json", "bad_json"),
        (b"[]", "unsupported_format"),
        (b'{"model": "m"}', "unsupported_format"),
        (b'{"messages": "x"}', "unsupported_format"),
        (b'{"messages": ["x"]}', "unsupported_format"),
    ],
)
def test_a_body_of_another_shape_is_refused_with_a_fixed_code(state, body, code):
    with pytest.raises(MaskingFailed) as err:
        mask(state, body)
    assert err.value.code == code and "client data could not be masked safely" in str(err.value)


def test_a_deeply_nested_body_is_refused_not_crashed(state):
    deep: Any = "x"
    for _ in range(200):
        deep = [deep]
    with pytest.raises(MaskingFailed) as err:
        mask(state, chat([user("hi", extra=deep)]))
    assert err.value.code == "too_deep"


# ---------------------------------------------------------------------------------- images
PNG = b"\x89PNG-fake-image-bytes"


def data_url(raw: bytes, mime: str = "image/jpeg") -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


def image_message(url: str) -> bytes:
    part = {
        "type": "image_url",
        "image_url": {"url": url, "detail": "high", "media_type": "image/jpeg"},
    }
    return chat([user([{"type": "text", "text": "look"}, part])])


class FakeRedactor:
    def __init__(self, mode: str = "redact") -> None:
        self.mode = mode
        self.calls = 0

    def __call__(self, data: bytes, smap: Any, engines: Any = None) -> _images.ImageResult:
        self.calls += 1
        if self.mode == "withhold":
            raise _images._Withhold(_images.W_RESIDUAL)
        if self.mode == "clean":
            return _images.ImageResult(None, None, 0, {})
        return _images.ImageResult(b"PAINTED:" + data, None, 1, {})


def image_state(tmp_path, monkeypatch, mode: str):
    monkeypatch.setattr(_images, "redact_image_bytes", FakeRedactor(mode))
    return make_state(tmp_path, "redact")


def test_a_data_url_image_is_redacted_and_comes_back_as_png(tmp_path, monkeypatch):
    st = image_state(tmp_path, monkeypatch, "redact")
    out = json.loads(mask(st, image_message(data_url(PNG)))[0])
    part = out["messages"][0]["content"][1]
    assert part["type"] == "image_url" and part["image_url"]["detail"] == "high"
    assert part["image_url"]["media_type"] == "image/png"
    head, _, payload = part["image_url"]["url"].partition(",")
    assert head == "data:image/png;base64" and base64.b64decode(payload) == b"PAINTED:" + PNG
    assert out["messages"][0]["content"][0] == {"type": "text", "text": "look"}


def test_a_clean_image_is_forwarded_unchanged(tmp_path, monkeypatch):
    st = image_state(tmp_path, monkeypatch, "clean")
    url = data_url(PNG)
    part = json.loads(mask(st, image_message(url))[0])["messages"][0]["content"][1]
    assert part["image_url"]["url"] == url and part["image_url"]["media_type"] == "image/jpeg"


@pytest.mark.parametrize(
    ("mode", "url", "reason"),
    [
        ("withhold", data_url(PNG), _images.W_RESIDUAL),
        ("redact", "https://github.com/github-copilot/chat/attachments/x", _images.W_URL),
        ("redact", "data:image/png;base64,!!!not base64!!!", _images.W_DECODE),
        ("redact", "data:image/png,plain", _images.W_DECODE),
        ("redact", "data:image/png;base64,", _images.W_DECODE),
    ],
)
def test_an_image_that_cannot_be_made_safe_becomes_a_text_part(
    tmp_path, monkeypatch, mode, url, reason
):
    st = image_state(tmp_path, monkeypatch, mode)
    part = json.loads(mask(st, image_message(url))[0])["messages"][0]["content"][1]
    assert part == {"type": "text", "text": f"[Client masking: image withheld - {reason}]"}


def test_the_withhold_policy_withholds_every_inline_image(state):
    part = json.loads(mask(state, image_message(data_url(PNG)))[0])["messages"][0]["content"][1]
    assert part["type"] == "text" and "withheld" in part["text"]


def test_a_bare_string_image_url_is_handled(tmp_path, monkeypatch):
    st = image_state(tmp_path, monkeypatch, "redact")
    body = chat([user([{"type": "image_url", "image_url": data_url(PNG)}])])
    part = json.loads(mask(st, body)[0])["messages"][0]["content"][0]
    assert part["image_url"].startswith("data:image/png;base64,")


# ---------------------------------------------------------------------------------- recognition
class Recognizer:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.history: list[list[str]] = []

    def find_many(self, texts: list[str], complete: bool = False, budget_s=None, wait=True):
        # strict calls (the new text) are recorded in ``calls``; best-effort history in ``history``
        (self.calls if complete else self.history).append(list(texts))
        return []

    def find(self, text: str):
        raise AssertionError("batched recognition expected")


def recognizer_state(tmp_path):
    from client_gateway.state import GatewayState
    from core.client_guard.engine import SessionMap

    rec = Recognizer()
    smap = SessionMap(token_fn=lambda prefix, key: 60_000, recognizers=[rec])
    return GatewayState(home=tmp_path, default_mode="on", images_policy="withhold", smap=smap), rec


def test_recognition_reads_the_whole_history_once_then_only_the_new_round(tmp_path, monkeypatch):
    import client_gateway.masking as masking_mod

    monkeypatch.setattr(masking_mod, "_background", lambda fn: fn())  # deterministic
    st, rec = recognizer_state(tmp_path)
    memo = RemaskMemo()
    turn1 = [
        {"role": "system", "content": "system text"},
        user("first <reminderInstructions>static tool advice</reminderInstructions> typed"),
        {"role": "assistant", "content": "answer one", "reasoning_text": "thought one"},
        user("second question"),
    ]
    mask(st, chat(turn1), memo)
    # strict only for the new round; the earlier conversation is read best effort (never blocks)
    assert rec.calls == [["second question"]] and memo.history_scanned
    seen = sorted(t for call in rec.history for t in call)
    assert sorted(set(seen)) == sorted(["first   typed", "answer one", "thought one"])
    turn2 = [*turn1, {"role": "assistant", "content": "answer two"},
             {"role": "tool", "tool_call_id": "t", "content": "tool result"}, user("third")]  # fmt: skip
    mask(st, chat(turn2), memo)
    assert rec.calls[1] == ["tool result", "third"]


# ---------------------------------------------------------------------------------- newest text
def human(messages: list[dict[str, Any]]) -> str | None:
    return ADAPTER.newest_user_text(chat(messages))


def wrapped(request: str, context: str = "<context>\nthe date\n</context>") -> str:
    return (
        f"{context}\n<reminderInstructions>\nadvice\n</reminderInstructions>\n"
        f"<userRequest>\n{request}\n</userRequest>"
    )


def test_newest_user_text_extracts_the_request_from_the_vs_code_wrapper():
    body = body_bytes(load_fixture("copilot_tool_result_image"))
    assert ADAPTER.newest_user_text(body) == "What do you know about Quillfeather Logistics BV?"
    assert human([user(wrapped("masking off"))]) == "masking off"
    assert human([user("masking off")]) == "masking off"
    parts = [{"type": "text", "text": "<attachments>"},
             {"type": "image_url", "image_url": {"url": "https://x/y"}},
             {"type": "text", "text": "\n</attachments>\n" + wrapped("what is this?")}]  # fmt: skip
    assert human([user(parts)]) == "what is this?"


def test_newest_user_text_is_none_when_only_wrappers_or_nothing_is_there():
    assert human([user("<environment_info>\nWindows\n</environment_info>")]) is None
    assert human([{"role": "assistant", "content": "masking off"}]) is None
    assert human([]) is None and human([user("")]) is None and human([user(None)]) is None
    assert ADAPTER.newest_user_text(b"not json") is None
    assert ADAPTER.newest_user_text(b"[]") is None and ADAPTER.newest_user_text(b"{}") is None


def test_newest_user_text_is_the_last_user_message_even_with_tool_rounds_after_it():
    msgs = [user(wrapped("first")), user(wrapped("second")),
            {"role": "assistant", "content": "", "tool_calls": []},
            {"role": "tool", "tool_call_id": "t", "content": "masking off"}]  # fmt: skip
    assert human(msgs) == "second"


@pytest.mark.parametrize(
    "messages",
    [
        # a tool result, an assistant message and a system message can never switch masking
        [user(wrapped("hello")), {"role": "tool", "tool_call_id": "t", "content": "masking off"}],
        [user(wrapped("hello")), {"role": "assistant", "content": "masking off"}],
        [{"role": "system", "content": "masking off"}, user(wrapped("hello"))],
        # reminder / context text and an attached file that says it
        [user(wrapped("hello", "<context>\nmasking off\n</context>"))],
        [user(wrapped("hello").replace("advice", "masking off"))],
        [user("<attachments>\nfile: masking off\n</attachments>\n" + wrapped("hello"))],
        [user("<userRequest>\nmasking off\n</userRequest> a pasted file\n" + wrapped("hello"))],
        # a phrase inside a sentence
        [user(wrapped("please do masking off now"))],
    ],
)
def test_r4_only_the_humans_whole_message_can_be_a_command(messages):
    assert typed_command(human(messages)) is None


def test_r4_the_whole_message_command_is_found_through_the_wrapper():
    assert typed_command(human([user(wrapped("masking off"))])) == "off"
    assert typed_command(human([user(wrapped("/Masking ON"))])) == "on"


# ---------------------------------------------------------------------------------- response
def tokens_for(state) -> dict[str, str]:
    return {NAME: token_of(state, NAME), NAME2: token_of(state, NAME2)}


def run_stream(
    ctx, wire: list[bytes], encoding: str = "", reencode: bool = True, ctype="text/event-stream"
):
    rehydrator = ADAPTER.rehydrator(ctype, encoding or None, ctx, None, reencode)
    out = [rehydrator.feed(c) for c in wire]
    out.append(rehydrator.finish())
    return [o for o in out if o], rehydrator


def decoded(out: list[bytes], rehydrator: Any) -> str:
    return from_wire(out, rehydrator.encoding_out)


def assert_valid_events(text: str) -> None:
    for chunk in parse_chunks([text]):
        for ev in chunk:
            if ev.data_at is not None and ev.lines[ev.data_at].strip() != "data: [DONE]":
                assert ev.payload is not None, "an event is not valid JSON any more"


@pytest.mark.parametrize("name", STREAMS)
def test_recorded_chunk_boundaries_are_rehydrated_to_the_unmasked_text(state, name):
    chunks = load_fixture(name)["response"]["chunks"]
    masked, expected = mask_stream(chunks, copilot_slots, tokens_for(state))
    fields = stream_text(masked, copilot_slots)  # the tokens are split over events: join per field
    assert expected and any(
        t in text for text in fields.values() for t in tokens_for(state).values()
    )
    assert not any(n in text for text in fields.values() for n in (NAME, NAME2))
    ctx = MaskContext(state, marker=None)
    out, rehydrator = run_stream(ctx, [c.encode() for c in masked])
    text = decoded(out, rehydrator)
    assert not any(t in text for t in tokens_for(state).values())
    assert stream_text([text], copilot_slots) == expected
    assert_valid_events(text)
    assert rehydrator.stats["errors"] == 0


@pytest.mark.parametrize("name", STREAMS)
def test_events_without_text_pass_through_byte_identical(state, name):
    chunks = load_fixture(name)["response"]["chunks"]
    masked, _ = mask_stream(chunks, copilot_slots, tokens_for(state))
    out, rehydrator = run_stream(MaskContext(state, marker=None), [c.encode() for c in masked])
    original = [f for f in "".join(masked).split("\n\n") if f]
    converted = [f for f in decoded(out, rehydrator).split("\n\n") if f]
    events = [ev for chunk in parse_chunks(masked) for ev in chunk]
    assert len(events) == len(original)
    assert len(converted) == len(original) + rehydrator.stats["flushed_tails"]  # only tails added
    later = iter(converted)  # every event without a streamed text field survives, in order
    for frame, ev in zip(original, events):
        if not copilot_slots([ev]):
            assert any(frame == other for other in later)
    assert original[-1] == converted[-1] == "data: [DONE]"


def _token_cuts(wire: bytes, tokens: list[str]) -> list[int]:
    cuts = [0]
    for token in tokens:
        at = wire.find(token.encode())
        while at >= 0:
            cuts += list(range(at - 1, at + len(token) + 2))
            at = wire.find(token.encode(), at + 1)
    return cuts


@pytest.mark.parametrize("name", STREAMS)
@pytest.mark.parametrize("encoding", ENCODINGS)
def test_wire_splits_around_every_pseudonym_give_the_same_output(state, name, encoding):
    chunks = load_fixture(name)["response"]["chunks"]
    masked, expected = mask_stream(chunks, copilot_slots, tokens_for(state))
    wire = b"".join(to_wire(masked, encoding))
    ctx = MaskContext(state, marker=None)
    reference, r0 = run_stream(ctx, [wire], encoding)
    ref_text = decoded(reference, r0)
    assert stream_text([ref_text], copilot_slots) == expected
    for cut in (
        _token_cuts(wire, list(tokens_for(state).values()))
        if not encoding
        else range(1, len(wire), 7)
    ):
        out, r = run_stream(ctx, resplit(wire, [cut]), encoding)
        assert decoded(out, r) == ref_text, (encoding, cut)
    out, r = run_stream(ctx, [wire[i : i + 1] for i in range(len(wire))], encoding)
    assert decoded(out, r) == ref_text  # one byte at a time


def copilot_stream(field: tuple[Any, ...], pieces: list[str]) -> list[str]:
    """A stream of events that stream ``pieces`` into one field, then finish."""

    def event(delta: dict[str, Any], finish: str | None = None) -> str:
        choice: dict[str, Any] = {"index": 0, "delta": delta}
        if finish:
            choice["finish_reason"] = finish
        return "data: " + json.dumps({"choices": [choice], "id": "msg_fake", "model": "m"}) + "\n\n"

    kind = field[1]
    out = []
    for piece in pieces:
        if kind == "arguments":
            out.append(event({"tool_calls": [{"function": {"arguments": piece}, "index": 1}]}))
        else:
            out.append(event({kind: piece, "role": "assistant"}))
    out.append(event({"content": None}, "stop"))
    out.append("data: [DONE]\n\n")
    return out


@pytest.mark.parametrize("name", STREAMS)
def test_event_splits_inside_and_around_every_pseudonym(state, name):
    """The recorded text of each field, cut into two events at every offset around a pseudonym,
    and cut into one character per event."""
    chunks = load_fixture(name)["response"]["chunks"]
    masked, expected = mask_stream(chunks, copilot_slots, tokens_for(state))
    masked_text = stream_text(masked, copilot_slots)
    ctx = MaskContext(state, marker=None)
    checked = 0
    for fid, text in masked_text.items():
        if not text:
            continue
        want = expected[fid]
        cuts = {0, len(text)}
        for token in tokens_for(state).values():
            at = text.find(token)
            while at >= 0:
                cuts.update(range(max(0, at - 1), min(len(text), at + len(token) + 1) + 1))
                at = text.find(token, at + 1)
        for cut in sorted(cuts):
            stream = copilot_stream(fid, [text[:cut], text[cut:]])
            out, r = run_stream(ctx, [c.encode() for c in stream])
            got = stream_text([decoded(out, r)], copilot_slots)
            assert got[fid] == want, (fid, cut)
            checked += 1
        stream = copilot_stream(fid, list(text))
        out, r = run_stream(ctx, [c.encode() for c in stream])
        assert stream_text([decoded(out, r)], copilot_slots)[fid] == want
    assert checked > 10


@pytest.mark.parametrize("encoding", ["gzip", "br", "zstd", "deflate"])
def test_output_is_re_encoded_in_the_same_encoding_by_default(state, encoding):
    chunks = load_fixture("copilot_tool_call_stream")["response"]["chunks"]
    masked, expected = mask_stream(chunks, copilot_slots, tokens_for(state))
    ctx = MaskContext(state, marker=None)
    out, r = run_stream(ctx, to_wire(masked, encoding), encoding)
    assert r.encoding_out == encoding
    assert stream_text([from_wire(out, encoding)], copilot_slots) == expected
    # every fed chunk of the upstream produced output a client could already decode
    rehydrator = ADAPTER.rehydrator("text/event-stream", encoding, ctx)
    dec = decode_stream(encoding)
    wire = to_wire(masked, encoding)
    first = rehydrator.feed(wire[0])
    assert dec.feed(first).startswith(b"data: ")


@pytest.mark.parametrize("encoding", ["gzip", "br", "zstd"])
def test_reencode_false_gives_plain_bytes_and_says_so(state, encoding):
    chunks = load_fixture("copilot_tool_call_stream")["response"]["chunks"]
    masked, expected = mask_stream(chunks, copilot_slots, tokens_for(state))
    out, r = run_stream(MaskContext(state, marker=None), to_wire(masked, encoding), encoding, False)
    assert r.encoding_out == "identity"
    assert stream_text([b"".join(out).decode()], copilot_slots) == expected


def test_the_pair_is_recorded_in_the_memo_for_the_next_request(state):
    chunks = load_fixture("copilot_tool_call_stream")["response"]["chunks"]
    masked, expected = mask_stream(chunks, copilot_slots, tokens_for(state))
    memo = RemaskMemo()
    ctx = MaskContext(state, memo, marker=None)
    run_stream(ctx, [c.encode() for c in masked])
    reasoning = expected[(0, "reasoning_text")]
    assert NAME2 in reasoning and token_of(state, NAME2) in memo.lookup(reasoning)
    args = expected[(0, "arguments", 1)]
    assert memo.lookup(args) == '{"customer": "' + token_of(state, NAME2) + '"}'
    # and the request that resends them replays the same masked bytes
    body = chat([{"role": "assistant", "content": "", "reasoning_text": reasoning, "tool_calls": [
        {"id": "t", "type": "function", "function": {"name": "f", "arguments": args}}]}])  # fmt: skip
    message = json.loads(mask(state, body, memo)[0])["messages"][0]
    assert message["tool_calls"][0]["function"]["arguments"] == memo.lookup(args)
    assert message["reasoning_text"] == memo.lookup(reasoning)


def test_a_json_escaped_value_keeps_the_arguments_valid_json(state):
    state.smap.add_values("CUS", ['Quote "Q" Holding'], "test")
    token = token_of(state, 'Quote "Q" Holding')
    pieces = ['{"customer": "', token[:5], token[5:], '"}']
    out, r = run_stream(
        MaskContext(state, marker=None),
        [c.encode() for c in copilot_stream((0, "arguments", 1), pieces)],
    )
    text = stream_text([decoded(out, r)], copilot_slots)[(0, "arguments", 1)]
    assert json.loads(text) == {"customer": 'Quote "Q" Holding'}


def test_the_last_piece_and_finish_reason_in_one_event_keep_their_order(state):
    token = token_of(state, NAME)
    events = [
        {"choices": [{"index": 0, "delta": {"content": f"Client {token[:6]}"}}]},
        {"choices": [{"index": 0, "delta": {"content": token[6:]}, "finish_reason": "stop"}]},
    ]
    wire = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
    out, r = run_stream(MaskContext(state, marker=None), [wire.encode()])
    frames = decoded(out, r).split("\n\n")
    texts = [
        json.loads(f[6:])["choices"][0]["delta"].get("content")
        for f in frames
        if f.startswith("data: {")
    ]
    assert "".join(t for t in texts if t) == f"Client {NAME}"
    assert json.loads(frames[1][6:])["choices"][0]["finish_reason"] == "stop"
    assert frames[-2] == "data: [DONE]"  # nothing is emitted after the finish event except [DONE]
    assert r.stats["flushed_tails"] == 0


def test_a_tail_held_when_another_field_starts_is_flushed_first(state):
    token = token_of(state, NAME)
    events = [
        {"choices": [{"index": 0, "delta": {"content": f"See {token[:5]}"}}]},
        {
            "choices": [
                {"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"name": "f"}}]}}
            ]
        },
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
    ]
    wire = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
    out, r = run_stream(MaskContext(state, marker=None), [wire.encode()])
    frames = [f for f in decoded(out, r).split("\n\n") if f]
    assert len(frames) == 5 and r.stats["flushed_tails"] == 1
    assert json.loads(frames[1][6:])["choices"][0]["delta"] == {"content": token[:5]}
    assert "tool_calls" in frames[2]  # the tool-call event follows the flushed tail


def test_two_choices_are_held_apart(state):
    t1, t2 = token_of(state, NAME), token_of(state, NAME2)

    def ev(index: int, text: str) -> str:
        return (
            "data: "
            + json.dumps({"choices": [{"index": index, "delta": {"content": text}}]})
            + "\n\n"
        )

    wire = (
        ev(0, t1[:4])
        + ev(1, t2[:6])
        + ev(0, t1[4:] + " one")
        + ev(1, t2[6:] + " two")
        + "data: [DONE]\n\n"
    )
    out, r = run_stream(MaskContext(state, marker=None), [wire.encode()])
    got: dict[int, str] = {0: "", 1: ""}
    for chunk in parse_chunks([decoded(out, r)]):
        for e in chunk:
            for choice in (e.payload or {}).get("choices", []):
                got[choice["index"]] += choice["delta"].get("content", "")
    assert got == {0: f"{NAME} one", 1: f"{NAME2} two"}


def test_a_stream_that_ends_without_done_flushes_the_held_tail(state):
    token = token_of(state, NAME)
    wire = (
        "data: "
        + json.dumps({"choices": [{"index": 0, "delta": {"content": f"x {token}"}}]})
        + "\n\n"
    )
    out, r = run_stream(MaskContext(state, marker=None), [wire.encode()])
    assert stream_text([decoded(out, r)], copilot_slots)[(0, "content")] == f"x {NAME}"
    unterminated = wire.rstrip("\n").encode()  # the last frame has no closing blank line
    out, r = run_stream(MaskContext(state, marker=None), [unterminated])
    assert stream_text([decoded(out, r)], copilot_slots)[(0, "content")] == f"x {NAME}"


def test_a_conversion_failure_never_breaks_the_stream(state):
    token = token_of(state, NAME)
    chunks = [
        f"data: {json.dumps({'choices': [{'index': 0, 'delta': {'content': f'hi {token} bye'}}]})}\n\n"
    ]
    ctx = MaskContext(state, marker=None)

    class Boom:
        stats: dict[str, int] = {}

        def unmask_text(self, *args: Any, **kwargs: Any):
            raise RuntimeError("boom")

    ctx.state = type("S", (), {"smap": Boom(), "bump": lambda *a, **k: None})()  # type: ignore[assignment]
    out, r = run_stream(ctx, [c.encode() for c in chunks])
    assert f"hi {token} bye" in decoded(out, r)  # a pseudonym on screen, never a leak
    assert r.stats["errors"] >= 1


def test_odd_events_pass_through_unchanged(state):
    wire = (
        b": keep-alive\n\n"
        b"data: not json\n\n"
        b'data: {"choices": "x"}\n\n'
        b'data: {"no_choices": 1}\n\n'
        b"event: other\ndata: [1,2]\n\n"
        b"\xff\xfe\n\n"
    )
    out, r = run_stream(MaskContext(state, marker=None), [wire])
    assert b"".join(out) == wire and r.stats["errors"] == 0


def test_crlf_streams_are_kept_in_crlf(state):
    token = token_of(state, NAME)
    wire = (
        "data: "
        + json.dumps({"choices": [{"index": 0, "delta": {"content": f"x {token} y"}}]})
        + "\r\n\r\n"
    ).encode()
    out, r = run_stream(MaskContext(state, marker=None), [wire])
    text = b"".join(out).decode()
    assert text.endswith("\r\n\r\n") and NAME in text and "\n\n" not in text.replace("\r\n\r\n", "")


def test_an_unsupported_encoding_is_forwarded_untouched_and_counted(state):
    r = ADAPTER.rehydrator("text/event-stream", "compress", MaskContext(state, marker=None))
    assert r.feed(b"abc") == b"abc" and r.finish() == b"" and r.stats["errors"] == 1


def test_undecodable_bytes_are_forwarded_and_the_stream_goes_on(state):
    r = ADAPTER.rehydrator("text/event-stream", "gzip", MaskContext(state, marker=None))
    assert r.feed(b"this is not gzip data at all") == b"this is not gzip data at all"
    assert r.feed(b"more") == b"more" and r.finish() == b"" and r.stats["errors"] == 1


def test_a_response_that_is_not_an_event_stream_is_passed_through(state):
    r = ADAPTER.rehydrator("application/json", None, MaskContext(state, marker=None))
    assert r.feed(b'{"a": 1}') == b'{"a": 1}' and r.finish() == b""


# ---------------------------------------------------------------------------------- buffered
def test_a_buffered_completion_is_unmasked_with_json_context_for_arguments(state):
    state.smap.add_values("CUS", ['Quote "Q" Holding'], "test")
    t1, t2 = token_of(state, NAME), token_of(state, 'Quote "Q" Holding')
    body = json.dumps({
        "id": t1, "model": "m",
        "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": f"About {t1}.",
            "tool_calls": [{"id": "t", "type": "function", "function": {
                "name": "f", "arguments": '{"customer": "' + t2 + '"}'}}]}}],
    }).encode()  # fmt: skip
    for encoding in ("", "gzip", "br"):
        from client_gateway.adapters.streams import decode_bytes, encode_bytes

        wire = encode_bytes(body, encoding)
        out = ADAPTER.unmask_response(
            wire, "application/json", encoding or None, MaskContext(state, marker=None)
        )
        message = json.loads(decode_bytes(out, encoding))
        assert message["id"] == t1  # an id is not text
        choice = message["choices"][0]
        assert choice["message"]["content"] == f"About {NAME}."
        args = json.loads(choice["message"]["tool_calls"][0]["function"]["arguments"])
        assert args == {"customer": 'Quote "Q" Holding'}
    garbage = ADAPTER.unmask_response(
        b"{broken", "application/json", None, MaskContext(state, marker=None)
    )
    assert garbage == b"{broken"
    assert (
        ADAPTER.unmask_response(b"x", "text/plain", None, MaskContext(state, marker=None)) == b"x"
    )


def test_an_event_without_text_does_not_end_the_held_field(state):
    """Providers interleave ``content: ""`` and role-only deltas: a pseudonym split around one of
    them is still one pseudonym."""
    token = token_of(state, NAME)
    pieces = [token[:2], "", token[2:5], "", token[5:], " ok"]
    wire = copilot_stream((0, "content"), pieces)
    wire.insert(2, 'data: {"choices":[{"index":0,"delta":{"role":"assistant"}}]}\n\n')
    out, r = run_stream(MaskContext(state, marker=None), [c.encode() for c in wire])
    assert stream_text([decoded(out, r)], copilot_slots)[(0, "content")] == f"{NAME} ok"
    assert r.stats["flushed_tails"] == 0


# ---------------------------------------------------------------------------------- command rewrite
NEUTRAL = "Reply with only: OK"


def swapped(command: str) -> tuple[Any, bytes]:
    """The recorded request with a typed command as its newest turn, and the body after the rewrite."""
    tree = load_fixture("copilot_tool_result_image")["request"]["body"]
    text = tree["messages"][6]["content"]
    ask = "What do you know about Quillfeather Logistics BV?"
    assert ask in text
    tree["messages"][6]["content"] = text.replace(ask, command)
    body = json.dumps(tree).encode()
    return tree, ADAPTER.replace_newest_user_text(body, None, NEUTRAL)


@pytest.mark.parametrize("command", ["masking on", "masking off", "/Masking ON"])
def test_a_typed_command_is_replaced_by_the_neutral_text_and_the_wrapper_stays(command):
    tree, out = swapped(command)
    new = json.loads(out)
    turn = new["messages"][6]["content"]
    assert "<userRequest>\n" + NEUTRAL + "\n</userRequest>" in turn
    assert "masking" not in turn.lower() and turn.startswith("<context>")
    assert not re.search(r"masking +(on|off)", out.decode(), re.I)
    assert ADAPTER.newest_user_text(out) == NEUTRAL
    new["messages"][6]["content"] = tree["messages"][6]["content"]
    assert new == tree  # nothing else changed


def test_the_rewrite_replaces_the_text_part_that_holds_the_request_only():
    parts = [{"type": "text", "text": "<attachments>\nfile\n</attachments>\n"},
             {"type": "image_url", "image_url": {"url": "https://x/y"}},
             {"type": "text", "text": wrapped("masking off")}]  # fmt: skip
    body = chat([user("first ask"), user(parts)])
    new = json.loads(ADAPTER.replace_newest_user_text(body, None, NEUTRAL))
    assert new["messages"][0]["content"] == "first ask"
    got = new["messages"][1]["content"]
    assert got[0] == parts[0] and got[1] == parts[1]
    assert got[2]["text"] == wrapped(NEUTRAL) and got[2]["type"] == "text"
    plain = [user([{"type": "text", "text": "masking"}, {"type": "text", "text": " on"}])]
    out = ADAPTER.replace_newest_user_text(chat(plain), None, NEUTRAL)
    assert json.loads(out)["messages"][0]["content"] == [{"type": "text", "text": NEUTRAL}]


def test_an_unwrapped_command_and_only_the_newest_user_turn_are_replaced():
    body = chat([user("masking on"), {"role": "assistant", "content": "ok"}, user("masking off")])
    new = json.loads(ADAPTER.replace_newest_user_text(body, None, NEUTRAL))
    assert [m["content"] for m in new["messages"]] == ["masking on", "ok", NEUTRAL]


def test_nothing_replaceable_leaves_the_bytes_alone():
    # whether a text is a command is decided by the front end (R4: the whole message only)
    for body in (
        chat([user("<environment_info>\nWindows\n</environment_info>")]),
        chat([{"role": "assistant", "content": "masking on"}]),
        chat([]),
        b"not json",
        b"[]",
    ):
        assert ADAPTER.replace_newest_user_text(body, None, NEUTRAL) == body


def test_every_older_typed_command_in_the_resent_history_is_replaced_too():
    msgs = [user(wrapped("masking on")), {"role": "assistant", "content": "OK"},
            user(wrapped("tell me about the model")),
            user(wrapped("please say masking on to switch")),
            user("/Masking OFF"),
            user([{"type": "text", "text": wrapped("masking off")}]),
            user(wrapped("last question"))]  # fmt: skip
    body = chat(msgs)
    out = ADAPTER.replace_command_turns(body, None, NEUTRAL)
    new = json.loads(out)["messages"]
    assert new[0]["content"] == wrapped(NEUTRAL) and new[4]["content"] == NEUTRAL
    assert new[5]["content"] == [{"type": "text", "text": wrapped(NEUTRAL)}]
    # a sentence that holds the phrase, other turns and the assistant stay as they were
    for i in (1, 2, 3, 6):
        assert new[i] == msgs[i]
    assert "masking on\n" not in out.decode() and "masking off\n" not in out.decode().lower()
    assert ADAPTER.replace_command_turns(chat(msgs[1:4]), None, NEUTRAL) == chat(msgs[1:4])
    for other in (b"not json", b"[]", chat([])):
        assert ADAPTER.replace_command_turns(other, None, NEUTRAL) == other
