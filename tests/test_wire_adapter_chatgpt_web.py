"""client_gateway.adapters.chatgpt_web (ChatGPT in the browser, logged in): request masking, upload
redaction, stream and stored-conversation rehydration, driven by the sanitized wire fixtures.
Synthetic names only; no network, sockets or processes."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from client_gateway import images as _images
from client_gateway.adapters.base import MaskContext, Route
from client_gateway.adapters.chatgpt_web import ChatGPTStream, ChatGPTWebAdapter
from client_gateway.adapters.control import typed_command
from client_gateway.adapters.streams import PassthroughRehydrator, decode_bytes, encode_bytes
from client_gateway.masking import MaskingFailed
from tests._wire_support import (
    MARKER,
    NAME,
    NAME2,
    all_names,
    body_bytes,
    chatgpt_slots,
    from_wire,
    load_fixture,
    make_state,
    mask_stream,
    parse_chunks,
    resplit,
    same_shape,
    stream_text,
    to_wire,
    token_of,
)

ADAPTER = ChatGPTWebAdapter()
STREAMS = ["chatgpt_conversation_turn1", "chatgpt_conversation_image_turn"]
REQUESTS = [
    "chatgpt_conversation_turn1",
    "chatgpt_conversation_image_turn",
    "chatgpt_prepare_first",
    "chatgpt_prepare_followup",
    "chatgpt_files_metadata",
    "chatgpt_process_upload_stream",
]
ENCODINGS = ["", "gzip", "br", "zstd"]
CONVERSATION = Route("POST", "chatgpt.com", "/backend-api/f/conversation", "application/json")


@pytest.fixture()
def state(tmp_path):
    return make_state(tmp_path)


def route_of(fixture: dict[str, Any]) -> Route:
    r = fixture["request"]
    return Route(r["method"], r["host"], r["path"], r["content_type"])


def mask(state, body: bytes, route: Route | None = None, **kw: Any) -> tuple[bytes, MaskContext]:
    ctx = MaskContext(state, marker=kw.pop("marker", MARKER), **kw)
    return ADAPTER.mask_request(body, ctx, route), ctx


def conv(messages: list[dict[str, Any]], **extra: Any) -> bytes:
    return json.dumps({"action": "next", "messages": messages, **extra}).encode()


def msg(*parts: Any, role: str = "user", **extra: Any) -> dict[str, Any]:
    content = {"content_type": "text", "parts": list(parts)}
    return {"author": {"role": role}, "content": content, **extra}


# ---------------------------------------------------------------------------------- requests
@pytest.mark.parametrize("name", REQUESTS)
def test_fixture_requests_are_masked_and_keep_their_structure(state, name):
    fx = load_fixture(name)
    out, _ = mask(state, body_bytes(fx), route_of(fx))
    text = out.decode()
    assert not any(n in text for n in all_names()) and "zorblax" not in text.lower()
    assert same_shape(fx["request"]["body"], json.loads(out))  # keys and non-string values


def test_first_turn_text_and_followup_with_an_image_part(state):
    fx = load_fixture("chatgpt_conversation_image_turn")
    original = fx["request"]["body"]
    masked = json.loads(mask(state, body_bytes(fx), route_of(fx))[0])
    parts = masked["messages"][0]["content"]["parts"]
    assert parts[0] == original["messages"][0]["content"]["parts"][0]  # the asset pointer dict
    assert parts[1] == "What does this screenshot say?"
    attachment = masked["messages"][0]["metadata"]["attachments"][0]
    assert attachment["name"] != original["messages"][0]["metadata"]["attachments"][0]["name"]
    before = original["messages"][0]["metadata"]["attachments"][0]
    assert attachment["size"] == 13685 and attachment["id"] == before["id"]
    assert masked["conversation_id"] == original["conversation_id"]

    first = load_fixture("chatgpt_conversation_turn1")
    out = json.loads(mask(state, body_bytes(first), route_of(first))[0])
    assert out["messages"][0]["content"]["parts"][0].startswith(
        f"Our customer {token_of(state, NAME)} had revenue"
    )
    assert "conversation_id" not in out


def test_prepare_masks_the_half_typed_message_object(state):
    fx = load_fixture("chatgpt_prepare_followup")
    masked = json.loads(mask(state, body_bytes(fx), route_of(fx))[0])
    query = masked["partial_query"]
    assert query["author"] == {"role": "user"} and query["content"]["content_type"] == "text"
    assert masked["attachment_mime_types"] == ["image/png"]
    body = json.dumps({"partial_query": f"half {NAME}", "client_prepare_state": "sent"}).encode()
    out = json.loads(
        mask(
            state,
            body,
            Route("POST", "chatgpt.com", "/backend-api/f/conversation/prepare", "application/json"),
        )[0]
    )
    assert NAME not in out["partial_query"]  # a plain string draft is masked too


def test_file_names_are_masked_and_ids_are_not(state):
    for name in ("chatgpt_files_metadata", "chatgpt_process_upload_stream"):
        fx = load_fixture(name)
        out = json.loads(mask(state, body_bytes(fx), route_of(fx))[0])
        assert out["file_name"] != fx["request"]["body"]["file_name"]
        assert out["file_name"].endswith("_customer.png")
        assert {k: v for k, v in out.items() if k != "file_name"} == {
            k: v for k, v in fx["request"]["body"].items() if k != "file_name"
        }


def test_the_marker_is_removed_and_seen(state):
    body = conv([msg(json.dumps({"_fv": MARKER, "c": NAME}))])
    out, ctx = mask(state, body, CONVERSATION)
    assert ctx.marker_seen and MARKER.encode() not in out and NAME.encode() not in out


def test_other_content_fields_and_unknown_dict_parts(state):
    message = {
        "author": {"role": "user"},
        "content": {"content_type": "code", "text": f"x = '{NAME}'", "language": "python"},
    }
    out = json.loads(mask(state, conv([message]), CONVERSATION)[0])
    content = out["messages"][0]["content"]
    assert NAME not in content["text"] and content["content_type"] == "code"
    assert content["language"] == "python" and NAME not in json.dumps(out)
    pointer = {
        "asset_pointer": "sediment://file_x",
        "content_type": "image_asset_pointer",
        "width": 1,
    }
    out = json.loads(mask(state, conv([msg(pointer, f"hi {NAME}")]), CONVERSATION)[0])
    assert out["messages"][0]["content"]["parts"][0] == pointer


def test_a_client_value_in_a_field_that_is_forwarded_as_it_is_fails_closed(state):
    for extra in ({"timezone": NAME}, {"client_contextual_info": {"x": [NAME2]}}):
        with pytest.raises(MaskingFailed) as err:
            mask(state, conv([msg("hi")], **extra), CONVERSATION)
        assert err.value.code == "residual_leak" and NAME not in str(err.value)
    with pytest.raises(MaskingFailed):
        mask(state, conv([msg("hi", metadata={"x": NAME})]), CONVERSATION)
    with pytest.raises(MaskingFailed):
        mask(state, conv([msg({"asset_pointer": NAME})]), CONVERSATION)


def test_a_body_that_is_not_the_expected_shape_is_refused(state):
    with pytest.raises(MaskingFailed) as err:
        mask(state, b"not json", CONVERSATION)
    assert err.value.code == "bad_json"
    for body in (b"[]", b'{"messages": "x"}', b'{"messages": [1]}', b'{"unrelated": 1}'):
        with pytest.raises(MaskingFailed) as err:
            mask(state, body, CONVERSATION if b"messages" in body else None)
        assert err.value.code == "unsupported_format"
    with pytest.raises(MaskingFailed):  # no route and no recognisable shape
        mask(state, b'{"unrelated": 1}')


def test_without_a_route_the_kind_is_taken_from_the_shape_of_the_body(state):
    fx = load_fixture("chatgpt_conversation_turn1")
    assert NAME.encode() not in mask(state, body_bytes(fx))[0]
    fx = load_fixture("chatgpt_prepare_first")
    assert NAME.encode() not in mask(state, body_bytes(fx))[0]
    fx = load_fixture("chatgpt_files_metadata")
    assert json.loads(mask(state, body_bytes(fx))[0])["file_name"].endswith("_customer.png")


def test_get_routes_carry_no_body_and_are_never_rewritten(state):
    for path in (
        "/backend-api/conversations",
        f"/backend-api/conversations/{'0' * 8}-0000-4000-8000-000000000001",
    ):
        route = Route("GET", "chatgpt.com", path)
        assert mask(state, b"", route)[0] == b""
        with pytest.raises(MaskingFailed):
            mask(state, b"{}", route)


# ---------------------------------------------------------------------------------- keys
def test_chat_key_and_url_chat_key():
    turn1, image = load_fixture("chatgpt_conversation_turn1"), load_fixture(
        "chatgpt_conversation_image_turn"
    )
    assert ADAPTER.chat_key(body_bytes(turn1)) is None
    key = ADAPTER.chat_key(body_bytes(image))
    assert key == image["request"]["body"]["conversation_id"]
    assert ADAPTER.chat_key(body_bytes(load_fixture("chatgpt_prepare_followup"))) == key
    assert ADAPTER.chat_key(body_bytes(load_fixture("chatgpt_process_upload_stream"))) == key
    assert ADAPTER.chat_key(b"not json") is None and ADAPTER.chat_key(b"[]") is None
    assert ADAPTER.chat_key(b'{"conversation_id": ""}') is None
    get = load_fixture("chatgpt_conversation_get")["request"]["path"]
    assert ADAPTER.url_chat_key(get) == get.rsplit("/", 1)[1]
    assert (
        ADAPTER.url_chat_key("/backend-api/conversation/" + get.rsplit("/", 1)[1] + "/") is not None
    )
    assert ADAPTER.url_chat_key("/backend-api/conversations") is None
    assert ADAPTER.url_chat_key("/backend-api/f/conversation") is None


# ---------------------------------------------------------------------------------- uploads
PNG = b"\x89PNG-fake-bytes"
PUT = Route("PUT", "files-test.oaiusercontent.com", "/files/x/raw", "image/png")


class FakeRedactor:
    def __init__(self, mode: str) -> None:
        self.mode = mode

    def __call__(self, data: bytes, smap: Any, engines: Any = None) -> _images.ImageResult:
        if self.mode == "withhold":
            raise _images._Withhold(_images.W_RESIDUAL)
        if self.mode == "clean":
            return _images.ImageResult(None, None, 0, {})
        return _images.ImageResult(b"PAINTED:" + data, None, 1, {})


def upload_state(tmp_path, monkeypatch, mode: str):
    monkeypatch.setattr(_images, "redact_image_bytes", FakeRedactor(mode))
    return make_state(tmp_path, "redact")


def test_an_uploaded_image_is_redacted_or_passed_when_clean(tmp_path, monkeypatch):
    assert mask(upload_state(tmp_path, monkeypatch, "redact"), PNG, PUT)[0] == b"PAINTED:" + PNG
    assert mask(upload_state(tmp_path, monkeypatch, "clean"), PNG, PUT)[0] == PNG


def test_a_withheld_image_fails_the_upload_instead_of_leaking(tmp_path, monkeypatch):
    for st in (upload_state(tmp_path, monkeypatch, "withhold"), make_state(tmp_path, "withhold")):
        with pytest.raises(MaskingFailed) as err:
            mask(st, PNG, PUT)
        assert err.value.code == "image_withheld"


def test_an_upload_that_is_not_an_image_cannot_be_read_and_is_refused(state):
    for route in (
        Route("PUT", PUT.host, PUT.path, "application/pdf"),
        Route("PUT", PUT.host, PUT.path, "text/csv"),
        Route("PUT", PUT.host, PUT.path, ""),
        None,
    ):
        with pytest.raises(MaskingFailed) as err:
            mask(state, PNG, route)
        assert err.value.code == "unsupported_format"
    with pytest.raises(MaskingFailed):
        mask(state, b"", PUT)


def test_the_put_fixture_is_an_image_upload_route():
    fx = load_fixture("chatgpt_upload_put")
    assert ADAPTER.match(
        fx["request"]["method"], fx["request"]["host"], fx["request"]["path"], "image/png"
    )
    assert fx["request"]["content_length"] == 13685 and fx["request"]["content_type"] == "image/png"


# ---------------------------------------------------------------------------------- newest text
def newest(messages: list[dict[str, Any]], route: Route | None = CONVERSATION) -> str | None:
    return ADAPTER.newest_user_text(conv(messages), route)


def test_newest_user_text_is_the_string_parts_of_the_last_user_message():
    fx = load_fixture("chatgpt_conversation_image_turn")
    assert (
        ADAPTER.newest_user_text(body_bytes(fx), route_of(fx)) == "What does this screenshot say?"
    )
    assert newest([msg("first"), msg("second part", "and third")]) == "second part\nand third"
    assert newest([msg("hello"), msg("ignored", role="assistant")]) == "hello"
    assert newest([msg("sys", role="system")]) is None and newest([]) is None
    assert newest([msg({"asset_pointer": "x"})]) is None and newest([msg("")]) is None
    assert ADAPTER.newest_user_text(b"not json") is None and ADAPTER.newest_user_text(b"[]") is None


def test_the_half_typed_draft_is_never_text_a_command_can_come_from():
    draft = json.dumps({"partial_query": msg("masking off")}).encode()
    route = Route("POST", "chatgpt.com", "/backend-api/f/conversation/prepare", "application/json")
    assert (
        ADAPTER.newest_user_text(draft, route) is None and ADAPTER.newest_user_text(draft) is None
    )
    assert typed_command(newest([msg("masking off")])) == "off"
    assert typed_command(newest([msg("masking on")])) == "on"


@pytest.mark.parametrize(
    "messages",
    [
        [msg("hello"), msg("masking off", role="assistant")],
        [msg("hello"), msg("masking off", role="tool")],
        [msg("masking off", role="system"), msg("hello")],
        [msg({"text": "masking off", "content_type": "x"}, "hello")],
        [msg("please set masking off now")],
        [msg("masking off", "and more")],
    ],
)
def test_r4_only_the_humans_whole_message_can_be_a_command(messages):
    assert typed_command(newest(messages)) is None


# ---------------------------------------------------------------------------------- response
def tokens_for(state) -> dict[str, str]:
    return {NAME: token_of(state, NAME), NAME2: token_of(state, NAME2)}


def masked_stream(state, name: str) -> tuple[list[str], dict[Any, str], str]:
    """Recorded stream with every name replaced by its token (slot text re-split, every other
    event replaced in place), the text each field must give back, and the recorded encoding."""
    fx = load_fixture(name)
    tokens = tokens_for(state)
    chunks, expected = mask_stream(fx["response"]["chunks"], chatgpt_slots, tokens)
    for real, token in tokens.items():
        chunks = [c.replace(real, token) for c in chunks]  # echoes: whole messages, input_message
    return chunks, expected, fx["response"]["content_encoding"]


def run(
    ctx, wire: list[bytes], encoding: str = "", route: Route | None = None, reencode: bool = True
):
    rehydrator = ADAPTER.rehydrator(
        "text/event-stream; charset=utf-8", encoding or None, ctx, route, reencode
    )
    out = [rehydrator.feed(c) for c in wire]
    out.append(rehydrator.finish())
    return [o for o in out if o], rehydrator


def text_of(out: list[bytes], rehydrator: Any) -> str:
    return from_wire(out, rehydrator.encoding_out)


def valid_events(text: str) -> list[Any]:
    events = []
    for chunk in parse_chunks([text]):
        for ev in chunk:
            if ev.data_at is not None and ev.lines[ev.data_at].strip() != "data: [DONE]":
                assert ev.payload is not None, "an event is not valid JSON any more"
            events.append(ev)
    return events


@pytest.mark.parametrize("name", STREAMS)
def test_the_recorded_gzip_stream_is_rehydrated_event_by_event(state, name):
    chunks, expected, encoding = masked_stream(state, name)
    assert encoding == "gzip"
    fields = stream_text(chunks, chatgpt_slots)
    assert any(t in text for text in fields.values() for t in tokens_for(state).values())
    ctx = MaskContext(state, marker=None)
    out, r = run(ctx, to_wire(chunks, "gzip"), "gzip", route=CONVERSATION)
    text = text_of(out, r)
    assert r.encoding_out == "gzip"
    assert stream_text([text], chatgpt_slots) == expected
    valid_events(text)
    assert not any(t in text for t in tokens_for(state).values()) and r.stats["errors"] == 0
    assert NAME in text  # the echo of the user's own message shows the real name again


def test_echoed_messages_and_titles_are_unmasked_but_ids_are_not(state):
    token = token_of(state, NAME)
    uid = "00000000-0000-4000-8000-000000000009"
    events = [
        ("delta", {"v": {"message": {"id": token, "author": {"role": "user"},
                                     "content": {"content_type": "text", "parts": [f"About {token}"]}},
                         "conversation_id": uid}, "p": "", "o": "add"}),
        (None, {"type": "input_message", "input_message": {"id": token, "content": {"parts": [f"Hi {token}"]}}}),
        (None, {"type": "title_generation", "title": f"Notes on {token}", "conversation_id": uid}),
        (None, {"type": "resume_conversation_token", "token": token, "conversation_id": uid}),
    ]  # fmt: skip
    wire = "".join(
        (f"event: {n}\n" if n else "") + f"data: {json.dumps(e)}\n\n" for n, e in events
    ).encode()
    seen: list[str] = []
    ctx = MaskContext(state, marker=None, on_chat_id=seen.append)
    out, r = run(ctx, [wire])
    got = [e.payload for e in valid_events(text_of(out, r))]
    assert got[0]["v"]["message"]["content"]["parts"] == [f"About {NAME}"]
    assert got[0]["v"]["message"]["id"] == token  # an id is not text
    assert got[1]["input_message"]["content"]["parts"] == [f"Hi {NAME}"]
    assert got[2]["title"] == f"Notes on {NAME}" and got[3]["token"] == token
    assert seen == [uid]  # reported once


def test_the_first_turn_reports_its_conversation_id_once(state):
    chunks, _, _ = masked_stream(state, "chatgpt_conversation_turn1")
    seen: list[str] = []
    ctx = MaskContext(state, marker=None, on_chat_id=seen.append)
    run(ctx, to_wire(chunks, "gzip"), "gzip", route=CONVERSATION)
    assert seen == ["00000000-0000-4000-8000-000000000004"]


@pytest.mark.parametrize("name", STREAMS)
def test_events_without_streamed_text_pass_through_byte_identical(state, name):
    chunks, _, _ = masked_stream(state, name)
    out, r = run(MaskContext(state, marker=None), [c.encode() for c in chunks])
    original = [f for f in "".join(chunks).split("\n\n") if f]
    converted = [f for f in text_of(out, r).split("\n\n") if f]
    events = [ev for chunk in parse_chunks(chunks) for ev in chunk]
    assert len(events) == len(original) and len(converted) >= len(original)  # nothing dropped
    later = iter(converted)  # every event that streams no text survives unchanged, in order
    for frame, ev in zip(original, events):
        if not chatgpt_slots([ev]) and not any(t in frame for t in tokens_for(state).values()):
            assert any(frame == other for other in later)
    assert converted[-1] == original[-1] == "data: [DONE]"


def _cuts_around_tokens(wire: bytes, tokens: list[str]) -> list[int]:
    cuts = [0]
    for token in tokens:
        at = wire.find(token.encode())
        while at >= 0:
            cuts += list(range(at - 1, at + len(token) + 2))
            at = wire.find(token.encode(), at + 1)
    return cuts


@pytest.mark.parametrize("name", STREAMS)
@pytest.mark.parametrize("encoding", ENCODINGS)
def test_wire_splits_give_the_same_output_in_every_encoding(state, name, encoding):
    chunks, expected, _ = masked_stream(state, name)
    wire = b"".join(to_wire(chunks, encoding))
    ctx = MaskContext(state, marker=None)
    reference, r0 = run(ctx, [wire], encoding, route=CONVERSATION)
    ref_text = text_of(reference, r0)
    assert stream_text([ref_text], chatgpt_slots) == expected
    cuts = (
        _cuts_around_tokens(wire, list(tokens_for(state).values()))
        if not encoding
        else range(1, len(wire), 11)
    )
    for cut in cuts:
        out, r = run(ctx, resplit(wire, [cut]), encoding, route=CONVERSATION)
        assert text_of(out, r) == ref_text, (encoding, cut)
    out, r = run(ctx, [wire[i : i + 1] for i in range(len(wire))], encoding, route=CONVERSATION)
    assert text_of(out, r) == ref_text  # one byte at a time


def chatgpt_stream(pieces: list[str], tail: str = "patch") -> list[str]:
    """Events that stream ``pieces`` into one text path, finishing the way the capture does."""

    def ev(payload: Any, name: str | None = "delta") -> str:
        return (f"event: {name}\n" if name else "") + f"data: {json.dumps(payload)}\n\n"

    path = "/message/content/parts/0"
    out = [ev("v1", "delta_encoding"), ev({"p": "", "o": "add", "v": {"message": {"id": "m"}}})]
    out.append(ev({"p": path, "o": "append", "v": pieces[0]}))
    out += [ev({"v": p}) for p in pieces[1:-1]]
    last = pieces[-1] if len(pieces) > 1 else ""
    if tail == "patch":
        ops = [
            {"p": path, "o": "append", "v": last},
            {"p": "/message/status", "o": "replace", "v": "done"},
        ]
        out.append(ev({"p": "", "o": "patch", "v": ops}))
    else:
        out.append(ev({"v": last}))
        out.append(ev({"p": "/message/status", "o": "replace", "v": "done"}))
    out.append(ev({"type": "message_marker", "event": "last", "marker": "last_token"}, None))
    out.append(ev({"type": "message_stream_complete"}, None))
    out.append("data: [DONE]\n\n")
    return out


@pytest.mark.parametrize("tail", ["patch", "events"])
def test_every_split_of_a_pseudonym_across_events_gives_the_unmasked_text(state, tail):
    token = token_of(state, NAME)
    text = f"Customer: {token}, again {token}\n\nand NV"
    expected = state.smap.unmask_text(text)[0]
    ctx = MaskContext(state, marker=None)
    for cut in range(len(text) + 1):
        stream = chatgpt_stream([text[:cut], text[cut:]], tail)
        out, r = run(ctx, [c.encode() for c in stream])
        got = stream_text([text_of(out, r)], chatgpt_slots)
        assert got == {"/message/content/parts/0": expected}, (tail, cut)
    stream = chatgpt_stream(list(text) + [""], tail)  # one character per event
    out, r = run(ctx, [c.encode() for c in stream])
    assert stream_text([text_of(out, r)], chatgpt_slots) == {"/message/content/parts/0": expected}
    valid_events(text_of(out, r))


def test_a_held_tail_is_flushed_before_the_status_change_and_before_the_end(state):
    token = token_of(state, NAME)
    ctx = MaskContext(state, marker=None)
    # the tail "NV" is held when the patch changes the status: it must come out BEFORE that op
    stream = chatgpt_stream([f"Hello {token} and NV", ""], "patch")
    out, r = run(ctx, [c.encode() for c in stream])
    patches = [e.payload for e in valid_events(text_of(out, r)) if isinstance(e.payload, dict)]
    ops = [p for p in patches if p.get("o") == "patch"][0]["v"]
    kinds = [(op["p"], op["o"]) for op in ops]
    assert kinds == [
        ("/message/content/parts/0", "append"),
        ("/message/content/parts/0", "append"),  # the flushed tail, inserted by the adapter
        ("/message/status", "replace"),
    ]
    assert ops[1]["v"] == "NV" and r.stats["flushed_tails"] == 1
    # a stream with nothing after the text: the tail leaves with message_stream_complete
    events = [
        'event: delta\ndata: {"p":"/message/content/parts/0","o":"append","v":"Hi NV"}\n\n',
        'data: {"type":"message_stream_complete"}\n\n',
        "data: [DONE]\n\n",
    ]
    out, r = run(ctx, [c.encode() for c in events])
    frames = [f for f in text_of(out, r).split("\n\n") if f]
    assert len(frames) == 4 and '"v":"NV"' in frames[1] and "message_stream_complete" in frames[2]
    # and a stream cut off without any end event: the tail leaves at the end of the body
    out, r = run(ctx, [events[0].encode()])
    assert stream_text([text_of(out, r)], chatgpt_slots) == {"/message/content/parts/0": "Hi NV"}


def test_a_new_path_or_a_non_text_operation_ends_the_held_text(state):
    token = token_of(state, NAME)
    first = {"p": "/message/content/parts/0", "o": "append", "v": f"See {token[:6]}"}
    other = {"p": "/message/content/parts/1", "o": "append", "v": "second"}
    wire = "".join(f"event: delta\ndata: {json.dumps(e)}\n\n" for e in (first, other)).encode()
    out, r = run(MaskContext(state, marker=None), [wire])
    got = stream_text([text_of(out, r)], chatgpt_slots)
    assert got == {
        "/message/content/parts/0": f"See {token[:6]}",
        "/message/content/parts/1": "second",
    }
    assert r.stats["flushed_tails"] == 1


def test_a_bare_text_continues_the_last_append_path_not_a_replace(state):
    token = token_of(state, NAME)
    events = [
        {"p": "/message/content/parts/0", "o": "append", "v": f"A {token[:5]}"},
        {"p": "/message/status", "o": "replace", "v": "in_progress"},
        {"v": token[5:] + " B"},
    ]
    wire = "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode()
    out, r = run(MaskContext(state, marker=None), [wire])
    text = text_of(out, r)
    got = stream_text([text], chatgpt_slots)
    assert got["/message/content/parts/0"].startswith("A ") and "/message/status" not in got
    assert "in_progress" in text


def test_odd_events_pass_through_unchanged(state):
    wire = (
        b'event: delta_encoding\ndata: "v1"\n\n'
        b": keep-alive\n\n"
        b"data: not json\n\n"
        b"data: [1,2]\n\n"
        b'data: {"type":"message_marker","marker":"user_visible_token","event":"first"}\n\n'
        b'data: {"type":"server_ste_metadata","metadata":{"a":1}}\n\n'
        b"\xff\xfe\n\n"
    )
    out, r = run(MaskContext(state, marker=None), [wire])
    assert b"".join(out) == wire and r.stats["errors"] == 0


def test_a_conversion_failure_never_breaks_the_stream(state):
    token = token_of(state, NAME)
    wire = f'data: {{"p":"/m","o":"append","v":"hi {token} bye"}}\n\n'.encode()
    ctx = MaskContext(state, marker=None)

    class Boom:
        stats: dict[str, int] = {}

        def unmask_text(self, *args: Any, **kwargs: Any):
            raise RuntimeError("boom")

    ctx.state = type("S", (), {"smap": Boom(), "bump": lambda *a, **k: None})()  # type: ignore[assignment]
    out, r = run(ctx, [wire])
    assert f"hi {token} bye" in text_of(out, r) and r.stats["errors"] >= 1


@pytest.mark.parametrize("encoding", ["gzip", "br", "zstd"])
def test_reencode_false_gives_plain_bytes_and_says_so(state, encoding):
    chunks, expected, _ = masked_stream(state, "chatgpt_conversation_turn1")
    out, r = run(
        MaskContext(state, marker=None), to_wire(chunks, encoding), encoding, CONVERSATION, False
    )
    assert r.encoding_out == "identity"
    assert stream_text([b"".join(out).decode()], chatgpt_slots) == expected


def test_each_upstream_chunk_gives_output_a_client_can_decode_at_once(state):
    chunks, _, _ = masked_stream(state, "chatgpt_conversation_turn1")
    wire = to_wire(chunks, "gzip")
    r = ADAPTER.rehydrator(
        "text/event-stream", "gzip", MaskContext(state, marker=None), CONVERSATION
    )
    from client_gateway.adapters.streams import decode_stream

    dec = decode_stream("gzip")
    first = dec.feed(r.feed(wire[0]))
    assert first.startswith(b"event: delta_encoding")  # no waiting for the end of the stream


def test_responses_that_are_not_conversation_event_streams_are_passed_through(state):
    ctx = MaskContext(state, marker=None)
    ndjson = Route(
        "POST", "chatgpt.com", "/backend-api/files/process_upload_stream", "application/json"
    )
    r = ADAPTER.rehydrator("text/event-stream; charset=utf-8", None, ctx, ndjson)
    line = b'{"file_id":"f","event":"file.processing.started"}\n'
    assert isinstance(r, PassthroughRehydrator) and r.feed(line) == line and r.finish() == b""
    assert isinstance(ADAPTER.rehydrator("application/json", None, ctx), PassthroughRehydrator)
    unsupported = ADAPTER.rehydrator("text/event-stream", "compress", ctx, CONVERSATION)
    assert unsupported.feed(b"abc") == b"abc" and unsupported.stats["errors"] == 1
    broken = ADAPTER.rehydrator("text/event-stream", "gzip", ctx, CONVERSATION)
    assert (
        broken.feed(b"definitely not gzip") == b"definitely not gzip"
        and broken.stats["errors"] == 1
    )
    assert isinstance(
        ADAPTER.rehydrator("text/event-stream", None, ctx, CONVERSATION), ChatGPTStream
    )


# ---------------------------------------------------------------------------------- stored conversation
def tokenised(state, obj: Any) -> Any:
    text = json.dumps(obj, ensure_ascii=False)
    for real, token in tokens_for(state).items():
        text = text.replace(real, token)
    return json.loads(text)


@pytest.mark.parametrize("encoding", ["br", "gzip", "zstd", ""])
def test_the_stored_conversation_is_unmasked_in_its_own_encoding(state, encoding):
    fx = load_fixture("chatgpt_conversation_get")
    original = fx["response"]["body"]
    assert fx["response"]["content_encoding"] == "br" and NAME in json.dumps(original)
    masked = tokenised(state, original)
    assert not any(n in json.dumps(masked) for n in all_names())
    body = encode_bytes(json.dumps(masked).encode(), encoding)
    route = Route("GET", "chatgpt.com", fx["request"]["path"])
    out = ADAPTER.unmask_response(
        body, "application/json", encoding or None, MaskContext(state, marker=None), route
    )
    assert json.loads(decode_bytes(out, encoding)) == original  # ids, times, structure: identical
    plain = ADAPTER.unmask_response(
        body, "application/json", encoding or None, MaskContext(state, marker=None), route, False
    )
    assert json.loads(plain) == original


def test_the_conversation_list_titles_are_unmasked_and_ids_are_kept(state):
    token = token_of(state, NAME)
    items = {
        "items": [
            {
                "id": "00000000-0000-4000-8000-000000000004",
                "title": f"About {token}",
                "update_time": "t",
            }
        ],
        "total": 1,
    }
    out = ADAPTER.unmask_response(
        json.dumps(items).encode(), "application/json", None, MaskContext(state, marker=None)
    )
    got = json.loads(out)
    assert (
        got["items"][0]["title"] == f"About {NAME}"
        and got["items"][0]["id"] == items["items"][0]["id"]
    )


def test_a_response_that_cannot_be_unmasked_is_returned_as_it_came(state):
    ctx = MaskContext(state, marker=None)
    assert ADAPTER.unmask_response(b"{broken", "application/json", None, ctx) == b"{broken"
    assert ADAPTER.unmask_response(b"x", "text/html", None, ctx) == b"x"
    assert ADAPTER.unmask_response(b"", "application/json", None, ctx) == b""
    assert ADAPTER.unmask_response(b"garbage", "application/json", "gzip", ctx) == b"garbage"


# ---------------------------------------------------------------------------------- recognition
def test_recognition_reads_the_message_texts_and_the_draft(tmp_path):
    from client_gateway.state import GatewayState
    from core.client_guard.engine import SessionMap

    class Recognizer:
        calls: list[list[str]] = []

        def find_many(self, texts: list[str], complete: bool = False):
            self.calls.append(list(texts))
            return []

        def find(self, text: str):
            raise AssertionError("batched recognition expected")

    rec = Recognizer()
    smap = SessionMap(token_fn=lambda prefix, key: 60_000, recognizers=[rec])
    st = GatewayState(home=tmp_path, default_mode="on", images_policy="withhold", smap=smap)
    fx = load_fixture("chatgpt_conversation_image_turn")
    mask(st, body_bytes(fx), route_of(fx))
    assert rec.calls == [["What does this screenshot say?"]]  # strings only, not the asset pointer
    fx = load_fixture("chatgpt_prepare_first")
    mask(st, body_bytes(fx), route_of(fx))
    assert len(rec.calls) == 2 and rec.calls[1][0].startswith("Our customer")  # the draft too


def test_an_empty_append_does_not_end_the_held_text(state):
    token = token_of(state, NAME)
    pieces = [token[:2], "", token[2:5], "", token[5:], " ok", ""]
    out, r = run(MaskContext(state, marker=None), [c.encode() for c in chatgpt_stream(pieces)])
    assert stream_text([text_of(out, r)], chatgpt_slots) == {
        "/message/content/parts/0": f"{NAME} ok"
    }


# ---------------------------------------------------------------------------------- command rewrite
NEUTRAL = "Reply with only: OK"
PREPARE = Route("POST", "chatgpt.com", "/backend-api/f/conversation/prepare", "application/json")


@pytest.mark.parametrize("command", ["masking on", "masking off", " /Masking Off "])
def test_a_typed_command_is_replaced_by_the_neutral_text_and_nothing_else_changes(command):
    fx = load_fixture("chatgpt_conversation_turn1")
    tree = fx["request"]["body"]
    user_msg = [m for m in tree["messages"] if m["author"]["role"] == "user"][-1]
    user_msg["content"]["parts"] = [command]
    body = json.dumps(tree).encode()
    out = ADAPTER.replace_newest_user_text(body, route_of(fx), NEUTRAL)
    new = json.loads(out)
    assert ADAPTER.newest_user_text(out, route_of(fx)) == NEUTRAL
    assert not re.search(r"masking +(on|off)", out.decode(), re.I)
    new_user = [m for m in new["messages"] if m["author"]["role"] == "user"][-1]
    new_user["content"]["parts"] = [command]
    assert new == tree


def test_the_rewrite_keeps_asset_pointers_and_drops_the_other_text_parts():
    pointer = {"asset_pointer": "sediment://file_x", "content_type": "image_asset_pointer"}
    body = conv([msg("earlier"), msg(pointer, "masking", "on")])
    new = json.loads(ADAPTER.replace_newest_user_text(body, CONVERSATION, NEUTRAL))
    assert new["messages"][0]["content"]["parts"] == ["earlier"]
    assert new["messages"][1]["content"]["parts"] == [pointer, NEUTRAL]


def test_nothing_replaceable_leaves_the_bytes_alone():
    for body in (
        conv([msg("masking on", role="assistant")]),
        conv([msg({"asset_pointer": "x"})]),
        conv([]),
        b"not json",
        b"[]",
    ):
        assert ADAPTER.replace_newest_user_text(body, CONVERSATION, NEUTRAL) == body


def test_the_half_typed_draft_is_found_and_replaced_for_both_draft_shapes():
    fx = load_fixture("chatgpt_prepare_followup")
    tree = fx["request"]["body"]
    tree["partial_query"]["content"]["parts"] = ["  Masking ON "]
    body = json.dumps(tree).encode()
    assert ADAPTER.draft_text(body, route_of(fx)) == "Masking ON"
    assert typed_command(ADAPTER.draft_text(body, route_of(fx))) == "on"
    out = ADAPTER.replace_newest_user_text(body, route_of(fx), NEUTRAL)
    new = json.loads(out)
    assert new["partial_query"]["content"]["parts"] == [NEUTRAL]
    new["partial_query"]["content"]["parts"] = tree["partial_query"]["content"]["parts"]
    assert new == tree and not re.search(r"masking +(on|off)", out.decode(), re.I)
    plain = json.dumps({"partial_query": "masking off", "conversation_id": "c"}).encode()
    assert ADAPTER.draft_text(plain, PREPARE) == "masking off"
    assert json.loads(ADAPTER.replace_newest_user_text(plain, PREPARE, NEUTRAL)) == {
        "partial_query": NEUTRAL,
        "conversation_id": "c",
    }
    # a draft is not a send: it never answers ``newest_user_text`` and a send has no draft text
    assert ADAPTER.newest_user_text(body, route_of(fx)) is None
    assert ADAPTER.draft_text(conv([msg("masking on")]), CONVERSATION) is None
    assert ADAPTER.draft_text(b"not json") is None
