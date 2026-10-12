"""client_gateway.adapters.openai_responses (VS Code Copilot, ``/responses``): request masking and
response rehydration, driven by the sanitized wire fixtures. Synthetic names only; no network,
sockets or processes."""

from __future__ import annotations

import base64
import json
import re
from typing import Any

import pytest

from client_gateway import images as _images
from client_gateway.adapters.base import MaskContext
from client_gateway.adapters.control import typed_command
from client_gateway.adapters.copilot import chat_key
from client_gateway.adapters.openai_chat import OpenAIChatAdapter
from client_gateway.adapters.openai_responses import OpenAIResponsesAdapter
from client_gateway.adapters.registry import adapter_for
from client_gateway.adapters.streams import decode_bytes, encode_bytes
from client_gateway.masking import MaskingFailed
from client_gateway.memo import RemaskMemo
from tests._wire_support import (
    MARKER,
    NAME,
    NAME2,
    NAME3,
    all_names,
    body_bytes,
    from_wire,
    load_fixture,
    make_state,
    parse_chunks,
    relabel_request,
    relabel_stream,
    resplit,
    responses_slots,
    strings_of,
    stream_text,
    to_wire,
    token_of,
)

ADAPTER = OpenAIResponsesAdapter()
FIXTURES = [
    "copilot_responses_first",
    "copilot_responses_tool_result",
    "copilot_responses_tool_call",
]
ENCODINGS = ["", "gzip", "br", "zstd", "deflate"]
HOST = "api.individual.githubcopilot.com"


@pytest.fixture()
def state(tmp_path):
    return make_state(tmp_path)


def mask(
    state, body: bytes, memo: RemaskMemo | None = None, **kw: Any
) -> tuple[bytes, MaskContext]:
    ctx = MaskContext(state, memo, marker=kw.pop("marker", MARKER), **kw)
    return ADAPTER.mask_request(body, ctx), ctx


def req(items: list[Any], **extra: Any) -> bytes:
    return json.dumps({"model": "m", "input": items, "stream": True, **extra}).encode()


def msg(role: str, text: str, kind: str | None = None) -> dict[str, Any]:
    kind = kind or ("output_text" if role == "assistant" else "input_text")
    return {"role": role, "content": [{"type": kind, "text": text}]}


def call(args: str, name: str = "f", call_id: str = "call_1") -> dict[str, Any]:
    return {"type": "function_call", "name": name, "arguments": args, "call_id": call_id}


def output(value: Any, call_id: str = "call_1") -> dict[str, Any]:
    return {"type": "function_call_output", "call_id": call_id, "output": value}


# ---------------------------------------------------------------------------------- routing
def test_adapter_identity_and_shared_chat_space():
    assert ADAPTER.name == "openai_responses" and ADAPTER.chat_space == "copilot"
    assert ADAPTER.uses_turn_ids is True and ADAPTER.url_chat_key("/responses") is None
    assert OpenAIChatAdapter.chat_space == ADAPTER.chat_space  # one chat, either API


@pytest.mark.parametrize(
    ("args", "ok"),
    [
        (("POST", HOST, "/responses", "application/json"), True),
        (("POST", "api.githubcopilot.com", "/responses?x=1", ""), True),
        (("POST", "API.business.githubcopilot.com:443", "/responses/", "application/json"), True),
        (("GET", HOST, "/responses", ""), False),
        (("POST", HOST, "/responses/compact", "application/json"), False),
        (("POST", HOST, "/responses", "text/plain"), False),
        (("POST", "evilgithubcopilot.com", "/responses", "application/json"), False),
        (("POST", "api.openai.com", "/responses", "application/json"), False),
    ],
)
def test_match_is_exact_about_host_path_and_type(args, ok):
    assert ADAPTER.match(*args) is ok
    found = adapter_for(*args)
    assert (found is not None and found.name == "openai_responses") is ok


def test_the_chat_key_is_the_same_on_both_apis_for_the_same_first_turn():
    chat = json.dumps(
        {"messages": [{"role": "user", "content": "<userRequest>\nhello\n</userRequest>"}]}
    )
    resp = req([msg("system", "s"), msg("user", "<environment_info>\nOS\n</environment_info>"),
                msg("user", "<context>\nd\n</context>\n<userRequest>\nhello\n</userRequest>")])  # fmt: skip
    assert chat_key(chat.encode()) == chat_key(resp) == ADAPTER.chat_key(resp)
    assert OpenAIChatAdapter().chat_key(chat.encode()) == ADAPTER.chat_key(resp)
    assert ADAPTER.chat_key(req([msg("user", "no wrapper")])) is None  # a side call: no key


# ---------------------------------------------------------------------------------- request
@pytest.mark.parametrize("name", FIXTURES)
def test_fixture_request_is_masked_everywhere_and_keeps_its_structure(state, name):
    fx = load_fixture(name)
    original = fx["request"]["body"]
    out, ctx = mask(state, relabel_request(body_bytes(fx), state))
    masked = json.loads(out)
    assert not any(n in s for s in strings_of(masked) for n in all_names())
    assert MARKER not in out.decode()
    assert [i.get("role") or i.get("type") for i in masked["input"]] == [
        i.get("role") or i.get("type") for i in original["input"]
    ]
    for want, got in zip(original["input"], masked["input"]):
        assert list(want) == list(got)  # the same keys in the same order
    for key in original:
        if key != "input":
            assert masked[key] == original[key]  # settings and the tools list are untouched
    assert ctx.marker_seen == (name != "copilot_responses_first")


def test_the_marked_tool_result_is_masked_and_loses_its_marker(state):
    fx = load_fixture("copilot_responses_tool_result")
    out, ctx = mask(state, relabel_request(body_bytes(fx), state))
    results = [i for i in json.loads(out)["input"] if i.get("type") == "function_call_output"]
    assert ctx.marker_seen and MARKER.encode() not in out
    rows = json.loads(results[-1]["output"])
    assert rows["_fv"] == "fvpbi" and rows["rows"][0]["Customer"] == token_of(state, NAME)


def test_ids_opaque_blobs_and_call_ids_are_untouched(state):
    fx = load_fixture("copilot_responses_tool_result")
    original = fx["request"]["body"]["input"]
    masked = json.loads(mask(state, relabel_request(body_bytes(fx), state))[0])["input"]
    for want, got in zip(original, masked):
        for key in ("id", "call_id", "encrypted_content", "name", "status", "type", "role"):
            assert got.get(key) == want.get(key), key
    assert any(i.get("encrypted_content") == original[8]["encrypted_content"] for i in masked)


def test_function_call_arguments_are_parsed_masked_and_stay_valid_json(state):
    args = json.dumps({"customer": NAME, "n": 1})
    item = json.loads(mask(state, req([call(args)]))[0])["input"][0]
    assert json.loads(item["arguments"]) == {"customer": token_of(state, NAME), "n": 1}
    assert item["name"] == "f" and item["call_id"] == "call_1"


def test_argument_keys_and_nested_values_are_masked_and_clean_text_is_byte_identical(state):
    args = json.dumps({"filter": {NAME: [NAME2, 3]}})
    got = json.loads(json.loads(mask(state, req([call(args)]))[0])["input"][0]["arguments"])
    assert got == {"filter": {token_of(state, NAME): [token_of(state, NAME2), 3]}}
    clean = '{"customer":   "nobody",  "n": 1}'
    assert json.loads(mask(state, req([call(clean)]))[0])["input"][0]["arguments"] == clean


def test_unparseable_arguments_are_masked_as_text(state):
    item = json.loads(mask(state, req([call(f'{{"c": "{NAME}')]))[0])["input"][0]
    assert NAME not in item["arguments"]


def test_a_tool_output_as_a_string_or_a_list_of_parts_is_masked(state):
    text = json.dumps({"_fv": MARKER, "rows": [{"Customer": NAME}]})
    parts = [{"type": "output_text", "text": f"see {NAME2}"}]
    out, ctx = mask(state, req([output(text), output(parts, "call_2")]))
    items = json.loads(out)["input"]
    assert json.loads(items[0]["output"]) == {
        "_fv": "fvpbi",
        "rows": [{"Customer": token_of(state, NAME)}],
    }
    assert items[1]["output"] == [{"type": "output_text", "text": f"see {token_of(state, NAME2)}"}]
    assert ctx.marker_seen and MARKER.encode() not in out


def test_reasoning_summaries_are_masked_and_the_blob_is_not(state):
    item = {"type": "reasoning", "id": "rs_1", "encrypted_content": "BLOB==",
            "summary": [{"type": "summary_text", "text": f"Thinking about {NAME}"}]}  # fmt: skip
    got = json.loads(mask(state, req([item]))[0])["input"][0]
    assert got["summary"][0]["text"] == f"Thinking about {token_of(state, NAME)}"
    assert got["encrypted_content"] == "BLOB==" and got["id"] == "rs_1"


def test_system_and_instructions_use_the_developer_text_rule(state):
    state.smap.add_values("DIM", ["status"], "test")
    body = req([msg("system", f"Keep the status. Never reveal {NAME}."), msg("user", "status")],
               instructions=f"Keep the status for {NAME2}.")  # fmt: skip
    out = json.loads(mask(state, body)[0])
    assert out["input"][0]["content"][0]["text"].startswith("Keep the status.")
    assert NAME not in json.dumps(out) and NAME2 not in json.dumps(out)
    assert out["instructions"].startswith("Keep the status for ")
    assert out["input"][1]["content"][0]["text"] != "status"  # the same word as a user cell


def test_unknown_item_keys_are_masked_too(state):
    out = json.loads(mask(state, req([dict(msg("user", "hi"), extra={"k": [NAME]})]))[0])
    assert NAME not in json.dumps(out["input"][0]["extra"])


def test_a_client_value_in_a_verbatim_setting_fails_the_request_closed(state):
    with pytest.raises(MaskingFailed) as err:
        mask(state, req([msg("user", "hi")], user=NAME))
    assert err.value.code == "residual_leak" and NAME not in str(err.value)
    mask(state, req([msg("user", "hi")], tools=[{"name": "t", "description": f"about {NAME}"}]))


def test_the_marker_is_removed_from_anywhere_in_the_body(state):
    out, ctx = mask(state, req([msg("user", "hi")], metadata={"note": f"x {MARKER}"}))
    assert MARKER.encode() not in out and ctx.marker_seen


def test_same_input_gives_identical_bytes(state):
    body = relabel_request(body_bytes(load_fixture("copilot_responses_tool_call")), state)
    assert mask(state, body)[0] == mask(state, body, RemaskMemo())[0]


def test_the_memo_replays_what_the_model_wrote(state):
    memo = RemaskMemo()
    token = token_of(state, NAME)
    memo.remember(f"{token} is a client of ours", f"{NAME} is a client of ours")
    memo.remember(f'{{"c":"{token}"}}', f'{{"c": "{NAME}"}}')
    body = req([msg("assistant", f"{NAME} is a client of ours"), call(f'{{"c": "{NAME}"}}')])
    items = json.loads(mask(state, body, memo)[0])["input"]
    assert items[0]["content"][0]["text"] == f"{token} is a client of ours"
    assert items[1]["arguments"] == f'{{"c":"{token}"}}'


def test_a_bare_string_input_is_masked_as_text(state):
    out = json.loads(mask(state, json.dumps({"model": "m", "input": f"about {NAME}"}).encode())[0])
    assert NAME not in out["input"]


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (b"not json", "bad_json"),
        (b"[]", "unsupported_format"),
        (b'{"model": "m"}', "unsupported_format"),
        (b'{"input": 5}', "unsupported_format"),
        (b'{"input": ["x"]}', "unsupported_format"),
        (b'{"input": [[]]}', "unsupported_format"),
        (b'{"messages": []}', "unsupported_format"),
    ],
)
def test_a_body_of_another_shape_is_refused_with_a_fixed_code(state, body, code):
    with pytest.raises(MaskingFailed) as err:
        mask(state, body)
    assert err.value.code == code and "client data could not be masked safely" in str(err.value)


def test_a_part_that_cannot_be_inspected_fails_closed(state):
    for kind in ("input_file", "input_audio"):
        part = {"type": kind, "file_data": "AAAA", "filename": "x.pdf"}
        with pytest.raises(MaskingFailed) as err:
            mask(state, req([{"role": "user", "content": [part]}]))
        assert err.value.code == "unsupported_format"


def test_a_deeply_nested_body_is_refused_not_crashed(state):
    deep: Any = "x"
    for _ in range(200):
        deep = [deep]
    with pytest.raises(MaskingFailed) as err:
        mask(state, req([dict(msg("user", "hi"), extra=deep)]))
    assert err.value.code == "too_deep"


# ---------------------------------------------------------------------------------- images
PNG = b"\x89PNG-fake-image-bytes"


def data_url(raw: bytes, mime: str = "image/jpeg") -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


def image_body(image: Any) -> bytes:
    part = {"type": "input_image", "image_url": image, "detail": "high"}
    return req([{"role": "user", "content": [{"type": "input_text", "text": "look"}, part]}])


class FakeRedactor:
    def __init__(self, mode: str) -> None:
        self.mode = mode

    def __call__(self, data: bytes, smap: Any, engines: Any = None) -> _images.ImageResult:
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
    part = json.loads(mask(st, image_body(data_url(PNG)))[0])["input"][0]["content"][1]
    assert part["type"] == "input_image" and part["detail"] == "high"
    head, _, payload = part["image_url"].partition(",")
    assert head == "data:image/png;base64" and base64.b64decode(payload) == b"PAINTED:" + PNG


def test_an_image_url_object_is_handled_and_a_clean_image_is_unchanged(tmp_path, monkeypatch):
    st = image_state(tmp_path, monkeypatch, "clean")
    url = data_url(PNG)
    part = json.loads(mask(st, image_body({"url": url, "detail": "low"}))[0])["input"][0][
        "content"
    ][1]
    assert part["image_url"] == {"url": url, "detail": "low"}


@pytest.mark.parametrize(
    ("mode", "image", "reason"),
    [
        ("withhold", data_url(PNG), _images.W_RESIDUAL),
        ("redact", "https://example.test/attachments/x", _images.W_URL),
        ("redact", None, _images.W_URL),  # a file id instead of a URL
        ("redact", "data:image/png;base64,!!!not base64!!!", _images.W_DECODE),
        ("redact", "data:image/png,plain", _images.W_DECODE),
    ],
)
def test_an_image_that_cannot_be_made_safe_becomes_an_input_text_part(
    tmp_path, monkeypatch, mode, image, reason
):
    st = image_state(tmp_path, monkeypatch, mode)
    part = json.loads(mask(st, image_body(image))[0])["input"][0]["content"][1]
    assert part == {"type": "input_text", "text": f"[Client masking: image withheld - {reason}]"}


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


def test_recognition_reads_the_whole_history_once_then_only_the_new_round(tmp_path, monkeypatch):
    import client_gateway.masking as masking_mod

    monkeypatch.setattr(masking_mod, "_background", lambda fn: fn())  # deterministic
    from client_gateway.state import GatewayState
    from core.client_guard.engine import SessionMap

    rec = Recognizer()
    smap = SessionMap(token_fn=lambda prefix, key: 60_000, recognizers=[rec])
    st = GatewayState(home=tmp_path, default_mode="on", images_policy="withhold", smap=smap)
    memo = RemaskMemo()
    summary = {
        "type": "reasoning",
        "id": "r",
        "summary": [{"type": "summary_text", "text": "thought one"}],
    }
    turn1 = [
        msg("system", "system text"),
        msg("user", "first <reminderInstructions>static advice</reminderInstructions> typed"),
        summary,
        msg("assistant", "answer one"),
        msg("user", "second question"),
    ]
    mask(st, req(turn1), memo)
    # strict only for the new round; the earlier conversation is read best effort (never blocks)
    assert rec.calls == [["second question"]] and memo.history_scanned
    seen = sorted(t for call in rec.history for t in call)
    assert sorted(set(seen)) == sorted(["first   typed", "thought one", "answer one"])
    turn2 = [*turn1, call("{}"), output("tool result"), msg("user", "third")]
    mask(st, req(turn2), memo)
    assert rec.calls[1] == ["tool result", "third"]  # after the model's last call


# ---------------------------------------------------------------------------------- newest text
def wrapped(request: str, context: str = "<context>\nthe date\n</context>") -> str:
    return (
        f"{context}\n<reminderInstructions>\nadvice\n</reminderInstructions>\n"
        f"<userRequest>\n{request}\n</userRequest>"
    )


def human(items: list[Any]) -> str | None:
    return ADAPTER.newest_user_text(req(items))


def test_newest_user_text_extracts_the_request_from_the_vs_code_wrapper():
    fx = load_fixture("copilot_responses_first")
    assert ADAPTER.newest_user_text(body_bytes(fx)).startswith("Repeat this name once")
    assert human([msg("user", wrapped("masking off"))]) == "masking off"
    assert human([msg("user", "masking off")]) == "masking off"
    parts = [{"type": "input_text", "text": "<attachments>"},
             {"type": "input_text", "text": "\n</attachments>\n" + wrapped("what is this?")}]  # fmt: skip
    assert human([{"role": "user", "content": parts}]) == "what is this?"


def test_newest_user_text_is_none_when_only_wrappers_or_nothing_is_there():
    assert human([msg("user", "<environment_info>\nWindows\n</environment_info>")]) is None
    assert human([msg("assistant", "masking off")]) is None
    assert human([]) is None and human([msg("user", "")]) is None
    assert ADAPTER.newest_user_text(b"not json") is None
    assert ADAPTER.newest_user_text(b"[]") is None and ADAPTER.newest_user_text(b"{}") is None
    assert ADAPTER.newest_user_text(json.dumps({"input": "masking off"}).encode()) is None


@pytest.mark.parametrize(
    "items",
    [
        # a tool result, an assistant message, a call and a system message can never switch masking
        [msg("user", wrapped("hello")), output("masking off")],
        [msg("user", wrapped("hello")), msg("assistant", "masking off")],
        [msg("user", wrapped("hello")), call("masking off")],
        [msg("system", "masking off"), msg("user", wrapped("hello"))],
        # reminder / context text and an attached file that says it
        [msg("user", wrapped("hello", "<context>\nmasking off\n</context>"))],
        [msg("user", "<attachments>\nfile: masking off\n</attachments>\n" + wrapped("hello"))],
        [msg("user", wrapped("please do masking off now"))],
    ],
)
def test_r4_only_the_humans_whole_message_can_be_a_command(items):
    assert typed_command(human(items)) is None


def test_r4_the_whole_message_command_is_found_through_the_wrapper():
    assert typed_command(human([msg("user", wrapped("masking off"))])) == "off"
    assert typed_command(human([msg("user", wrapped("/Masking ON"))])) == "on"


# ---------------------------------------------------------------------------------- response
def run_stream(
    ctx, wire: list[bytes], encoding: str = "", reencode: bool = True, ctype="text/event-stream"
):
    rehydrator = ADAPTER.rehydrator(ctype, encoding or None, ctx, None, reencode)
    out = [rehydrator.feed(c) for c in wire]
    out.append(rehydrator.finish())
    return [o for o in out if o], rehydrator


def decoded(out: list[bytes], rehydrator: Any) -> str:
    return from_wire(out, rehydrator.encoding_out)


def masked_stream(state, name: str):
    chunks = load_fixture(name)["response"]["chunks"]
    return relabel_stream(chunks, responses_slots, state)


def assert_valid_events(text: str) -> None:
    for chunk in parse_chunks([text]):
        for ev in chunk:
            if ev.data_at is not None:
                assert ev.payload is not None, "an event is not valid JSON any more"
                assert ev.lines[0].startswith("event: ")


@pytest.mark.parametrize("name", FIXTURES)
def test_recorded_events_are_rehydrated_to_the_unmasked_text(state, name):
    masked, expected = masked_stream(state, name)
    tokens = list(token_of(state, n) for n in (NAME, NAME2, NAME3))
    fields = stream_text(masked, responses_slots)
    assert expected and (name == "copilot_responses_first" or any(
        t in text for text in fields.values() for t in tokens
    ))  # fmt: skip
    ctx = MaskContext(state, marker=None)
    out, rehydrator = run_stream(ctx, [c.encode() for c in masked])
    text = decoded(out, rehydrator)
    assert not any(t in text for t in tokens)  # every pseudonym is a name again, in every event
    assert stream_text([text], responses_slots) == expected
    assert_valid_events(text)
    assert rehydrator.stats["errors"] == 0 and rehydrator.stats["recognised"] > 5


def test_the_whole_object_events_are_unmasked_too(state):
    masked, expected = masked_stream(state, "copilot_responses_tool_result")
    out, r = run_stream(MaskContext(state, marker=None), [c.encode() for c in masked])
    text = decoded(out, r)
    done = [
        ev.payload
        for chunk in parse_chunks([text])
        for ev in chunk
        if (ev.payload or {}).get("type") == "response.output_item.done"
    ]
    assert any(NAME in json.dumps(p, ensure_ascii=False) for p in done)
    completed = [ev.payload for chunk in parse_chunks([text]) for ev in chunk
                 if (ev.payload or {}).get("type") == "response.completed"]  # fmt: skip
    assert completed and NAME2 in json.dumps(completed[0], ensure_ascii=False)
    assert r.stats["rehydrated_events"] > 10


@pytest.mark.parametrize("name", FIXTURES)
def test_events_without_text_pass_through_byte_identical(state, name):
    masked, _ = masked_stream(state, name)
    out, rehydrator = run_stream(MaskContext(state, marker=None), [c.encode() for c in masked])
    original = [f for f in "".join(masked).split("\n\n") if f]
    converted = [f for f in decoded(out, rehydrator).split("\n\n") if f]
    assert len(converted) == len(original) + rehydrator.stats["flushed_tails"]
    token_set = [token_of(state, n) for n in (NAME, NAME2, NAME3)]
    events = [ev for chunk in parse_chunks(masked) for ev in chunk]
    assert len(events) == len(original)
    later = iter(converted)  # an event with no streamed field and no pseudonym survives, in order
    for frame, ev in zip(original, events):
        if not responses_slots([ev]) and not any(t in frame for t in token_set):
            assert any(frame == other for other in later)


def _token_cuts(wire: bytes, tokens: list[str]) -> list[int]:
    cuts = [0]
    for token in tokens:
        at = wire.find(token.encode())
        while at >= 0:
            cuts += list(range(at - 1, at + len(token) + 2))
            at = wire.find(token.encode(), at + 1)
    return cuts


@pytest.mark.parametrize("name", ["copilot_responses_tool_result", "copilot_responses_tool_call"])
@pytest.mark.parametrize("encoding", ENCODINGS)
def test_wire_splits_around_every_pseudonym_give_the_same_output(state, name, encoding):
    masked, expected = masked_stream(state, name)
    wire = b"".join(to_wire(masked, encoding))
    ctx = MaskContext(state, marker=None)
    reference, r0 = run_stream(ctx, [wire], encoding)
    ref_text = decoded(reference, r0)
    assert stream_text([ref_text], responses_slots) == expected
    tokens = [token_of(state, n) for n in (NAME, NAME2, NAME3)]
    for cut in _token_cuts(wire, tokens) if not encoding else range(1, len(wire), 97):
        out, r = run_stream(ctx, resplit(wire, [cut]), encoding)
        assert decoded(out, r) == ref_text, (encoding, cut)
    out, r = run_stream(ctx, [wire[i : i + 1] for i in range(len(wire))], encoding)
    assert decoded(out, r) == ref_text  # one byte at a time


def responses_stream(field: tuple[Any, ...], pieces: list[str]) -> list[str]:
    """Events that stream ``pieces`` into one field, then end it the way the API does."""
    kind, out_idx, sub = field

    def ev(name: str, payload: dict[str, Any]) -> str:
        return f"event: {name}\ndata: " + json.dumps({"type": name, **payload}) + "\n\n"

    base = {"item_id": "item_1", "output_index": out_idx}
    if kind == "arguments":
        delta_name, done_name, final = (
            "response.function_call_arguments.delta",
            "response.function_call_arguments.done",
            "arguments",
        )
    elif kind == "summary":
        delta_name, done_name, final = (
            "response.reasoning_summary_text.delta",
            "response.reasoning_summary_text.done",
            "text",
        )
        base["summary_index"] = sub
    else:
        delta_name, done_name, final = (
            "response.output_text.delta",
            "response.output_text.done",
            "text",
        )
        base["content_index"] = sub
    out = [ev("response.created", {"response": {"id": "r", "output": []}})]
    for i, piece in enumerate(pieces):
        out.append(ev(delta_name, dict(base, delta=piece, sequence_number=i + 2)))
    out.append(
        ev(done_name, dict(base, **{final: "".join(pieces)}, sequence_number=len(pieces) + 2))
    )
    out.append(ev("response.completed", {"response": {"id": "r", "output": []}}))
    return out


@pytest.mark.parametrize("field", [("text", 0, 0), ("summary", 0, 0), ("arguments", 2, 0)])
def test_event_splits_inside_and_around_every_pseudonym(state, field):
    token = token_of(state, NAME)
    text = f"Client {token} and {token_of(state, NAME2)}: ok"
    if field[0] == "arguments":
        text = json.dumps({"customer": token, "other": token_of(state, NAME2)})
    want = (
        text.replace(token, NAME).replace(token_of(state, NAME2), NAME2)
        if field[0] != "arguments"
        else json.dumps({"customer": NAME, "other": NAME2})
    )
    fid = (field[0], field[1], field[2])
    ctx = MaskContext(state, marker=None)
    cuts = {0, len(text)}
    for t in (token, token_of(state, NAME2)):
        at = text.find(t)
        cuts.update(range(max(0, at - 1), min(len(text), at + len(t) + 1) + 1))
    for cut in sorted(cuts):
        stream = responses_stream(field, [text[:cut], text[cut:]])
        out, r = run_stream(ctx, [c.encode() for c in stream])
        got = stream_text([decoded(out, r)], responses_slots)
        assert got[fid] == want, (field, cut)
    stream = responses_stream(field, list(text))  # one character per event
    out, r = run_stream(ctx, [c.encode() for c in stream])
    assert stream_text([decoded(out, r)], responses_slots)[fid] == want


def test_the_tail_is_flushed_in_front_of_the_event_that_ends_the_field(state):
    token = token_of(state, NAME)
    stream = responses_stream(("text", 0, 0), [f"See {token[:5]}"])  # the token never completes
    out, r = run_stream(MaskContext(state, marker=None), [c.encode() for c in stream])
    frames = [f for f in decoded(out, r).split("\n\n") if f]
    names = [f.split("\n", 1)[0] for f in frames]
    assert names == [
        "event: response.created",
        "event: response.output_text.delta",
        "event: response.output_text.delta",  # the flushed tail, in front of ...
        "event: response.output_text.done",
        "event: response.completed",
    ]
    tail = json.loads(frames[2].split("data: ", 1)[1])
    assert tail["delta"] == token[:5] and tail["item_id"] == "item_1" and tail["output_index"] == 0
    assert tail["type"] == "response.output_text.delta" and "obfuscation" not in tail
    assert r.stats["flushed_tails"] == 1


def test_a_stream_that_ends_without_completed_flushes_the_held_tail(state):
    token = token_of(state, NAME)
    delta = {"type": "response.output_text.delta", "item_id": "i", "output_index": 0,
             "content_index": 0, "delta": f"x {token}"}  # fmt: skip
    wire = f"event: response.output_text.delta\ndata: {json.dumps(delta)}\n\n"
    for data in (wire.encode(), wire.rstrip("\n").encode()):  # also an unterminated last frame
        out, r = run_stream(MaskContext(state, marker=None), [data])
        assert stream_text([decoded(out, r)], responses_slots)[("text", 0, 0)] == f"x {NAME}"


def test_a_json_escaped_value_keeps_the_arguments_valid_json(state):
    state.smap.add_values("CUS", ['Quote "Q" Holding'], "test")
    token = token_of(state, 'Quote "Q" Holding')
    pieces = ['{"customer": "', token[:5], token[5:], '"}']
    stream = responses_stream(("arguments", 1, 0), pieces)
    out, r = run_stream(MaskContext(state, marker=None), [c.encode() for c in stream])
    text = decoded(out, r)
    got = stream_text([text], responses_slots)[("arguments", 1, 0)]
    assert json.loads(got) == {"customer": 'Quote "Q" Holding'}
    done = [ev.payload for c in parse_chunks([text]) for ev in c
            if (ev.payload or {}).get("type") == "response.function_call_arguments.done"]  # fmt: skip
    assert json.loads(done[0]["arguments"]) == {"customer": 'Quote "Q" Holding'}  # json context


def test_the_pair_is_recorded_in_the_memo_for_the_next_request(state):
    masked, expected = masked_stream(state, "copilot_responses_tool_result")
    memo = RemaskMemo()
    run_stream(MaskContext(state, memo, marker=None), [c.encode() for c in masked])
    reply = expected[("text", 0, 0)]
    assert NAME in reply
    replay = memo.lookup(reply)
    assert replay is not None and not any(n in replay for n in all_names())
    # the request that resends the unmasked reply replays the same masked bytes
    item = json.loads(mask(state, req([msg("assistant", reply)]), memo)[0])["input"][0]
    assert item["content"][0]["text"] == replay


def test_the_arguments_of_a_call_are_recorded_per_leaf_in_the_memo(state):
    token = token_of(state, NAME)
    pieces = ['{"customer": "', token[:5], token[5:], '"}']
    stream = responses_stream(("arguments", 1, 0), pieces)
    memo = RemaskMemo()
    run_stream(MaskContext(state, memo, marker=None), [c.encode() for c in stream])
    args = '{"customer": "' + NAME + '"}'
    assert memo.lookup(args) == '{"customer": "' + token + '"}'
    assert memo.lookup(NAME) == token  # one entry per changed string leaf
    item = json.loads(mask(state, req([call(args)]), memo)[0])["input"][0]
    assert item["arguments"] == '{"customer": "' + token + '"}'


def test_a_conversion_failure_never_breaks_the_stream(state):
    token = token_of(state, NAME)
    delta = {"type": "response.output_text.delta", "item_id": "i", "output_index": 0,
             "content_index": 0, "delta": f"hi {token} bye"}  # fmt: skip
    chunks = [f"event: response.output_text.delta\ndata: {json.dumps(delta)}\n\n"]
    ctx = MaskContext(state, marker=None)

    class Boom:
        stats: dict[str, int] = {}

        def unmask_text(self, *args: Any, **kwargs: Any):
            raise RuntimeError("boom")

    ctx.state = type("S", (), {"smap": Boom(), "bump": lambda *a, **k: None})()  # type: ignore[assignment]
    out, r = run_stream(ctx, [c.encode() for c in chunks])
    assert f"hi {token} bye" in decoded(out, r)  # a pseudonym on screen, never a leak
    assert r.stats["errors"] >= 1


def test_odd_events_pass_through_unchanged_and_are_not_recognised(state):
    wire = (
        b": keep-alive\n\n"
        b"data: not json\n\n"
        b'data: {"choices": "x"}\n\n'
        b'data: {"type": 5}\n\n'
        b'event: other\ndata: {"type": "other.thing", "delta": "x"}\n\n'
        b"event: other\ndata: [1,2]\n\n"
        b"\xff\xfe\n\n"
    )
    out, r = run_stream(MaskContext(state, marker=None), [wire])
    assert b"".join(out) == wire and r.stats["errors"] == 0
    assert r.stats["events"] == 7 and r.stats["recognised"] == 0  # R9: nothing was understood


def test_an_unknown_response_event_is_recognised_and_passed_on(state):
    token = token_of(state, NAME)
    wire = f'event: response.brand_new\ndata: {{"type":"response.brand_new","delta":"{token}"}}\n\n'
    out, r = run_stream(MaskContext(state, marker=None), [wire.encode()])
    assert r.stats["recognised"] == 1
    assert NAME in decoded(out, r)  # an unknown event is still unmasked as a tree


def test_crlf_streams_are_kept_in_crlf(state):
    token = token_of(state, NAME)
    delta = {"type": "response.output_text.delta", "item_id": "i", "output_index": 0,
             "content_index": 0, "delta": f"x {token} y"}  # fmt: skip
    wire = f"event: response.output_text.delta\r\ndata: {json.dumps(delta)}\r\n\r\n".encode()
    out, r = run_stream(MaskContext(state, marker=None), [wire])
    text = b"".join(out).decode()
    assert text.endswith("\r\n\r\n") and NAME in text and "\n\n" not in text.replace("\r\n\r\n", "")


@pytest.mark.parametrize("encoding", ["gzip", "br", "zstd", "deflate"])
def test_output_is_re_encoded_in_the_same_encoding_by_default(state, encoding):
    masked, expected = masked_stream(state, "copilot_responses_tool_result")
    ctx = MaskContext(state, marker=None)
    out, r = run_stream(ctx, to_wire(masked, encoding), encoding)
    assert r.encoding_out == encoding
    assert stream_text([from_wire(out, encoding)], responses_slots) == expected


@pytest.mark.parametrize("encoding", ["gzip", "br", "zstd"])
def test_reencode_false_gives_plain_bytes_and_says_so(state, encoding):
    masked, expected = masked_stream(state, "copilot_responses_tool_result")
    out, r = run_stream(MaskContext(state, marker=None), to_wire(masked, encoding), encoding, False)
    assert r.encoding_out == "identity"
    assert stream_text([b"".join(out).decode()], responses_slots) == expected


def test_an_unsupported_encoding_is_forwarded_untouched_and_counted(state):
    r = ADAPTER.rehydrator("text/event-stream", "compress", MaskContext(state, marker=None))
    assert r.feed(b"abc") == b"abc" and r.finish() == b"" and r.stats["errors"] == 1


def test_a_response_that_is_not_an_event_stream_is_passed_through(state):
    r = ADAPTER.rehydrator("application/json", None, MaskContext(state, marker=None))
    assert r.feed(b'{"a": 1}') == b'{"a": 1}' and r.finish() == b""


# ---------------------------------------------------------------------------------- buffered
def test_a_buffered_response_is_unmasked_with_json_context_for_arguments(state):
    state.smap.add_values("CUS", ['Quote "Q" Holding'], "test")
    t1, t2 = token_of(state, NAME), token_of(state, 'Quote "Q" Holding')
    body = json.dumps({
        "id": t1, "object": "response", "model": "m",
        "output": [
            {"type": "message", "id": "m1", "role": "assistant",
             "content": [{"type": "output_text", "text": f"About {t1}."}]},
            {"type": "function_call", "id": "f1", "call_id": "c1", "name": "f",
             "arguments": '{"customer": "' + t2 + '"}'},
        ],
    }).encode()  # fmt: skip
    for encoding in ("", "gzip", "br"):
        wire = encode_bytes(body, encoding)
        out = ADAPTER.unmask_response(
            wire, "application/json", encoding or None, MaskContext(state, marker=None)
        )
        message = json.loads(decode_bytes(out, encoding))
        assert message["id"] == t1  # an id is not text
        assert message["output"][0]["content"][0]["text"] == f"About {NAME}."
        assert json.loads(message["output"][1]["arguments"]) == {"customer": 'Quote "Q" Holding'}
    garbage = ADAPTER.unmask_response(
        b"{broken", "application/json", None, MaskContext(state, marker=None)
    )
    assert garbage == b"{broken"
    assert (
        ADAPTER.unmask_response(b"x", "text/plain", None, MaskContext(state, marker=None)) == b"x"
    )


# ---------------------------------------------------------------------------------- command rewrite
NEUTRAL = "Reply with only: OK"


@pytest.mark.parametrize("command", ["masking on", "masking off"])
def test_a_typed_command_is_replaced_by_the_neutral_text_and_the_wrapper_stays(command):
    tree = load_fixture("copilot_responses_first")["request"]["body"]
    part = tree["input"][2]["content"][0]
    ask = "Repeat this name once, then spell it with dashes: Zorblax Holdings NV"
    assert ask in part["text"]
    part["text"] = part["text"].replace(ask, command)
    body = json.dumps(tree).encode()
    out = ADAPTER.replace_newest_user_text(body, None, NEUTRAL)
    new = json.loads(out)
    text = new["input"][2]["content"][0]["text"]
    assert "<userRequest>\n" + NEUTRAL + "\n</userRequest>" in text and "masking" not in text
    assert not re.search(r"masking +(on|off)", out.decode(), re.I)
    assert ADAPTER.newest_user_text(out) == NEUTRAL
    new["input"][2]["content"][0]["text"] = part["text"]
    assert new == tree  # nothing else changed


def test_only_the_newest_user_item_is_replaced_and_other_shapes_are_unchanged():
    items = [msg("user", "masking on"), msg("assistant", "ok"), msg("user", "masking off")]
    new = json.loads(ADAPTER.replace_newest_user_text(req(items), None, NEUTRAL))
    assert [i["content"][0]["text"] for i in new["input"]] == ["masking on", "ok", NEUTRAL]
    for body in (
        req([msg("user", "<environment_info>\nWindows\n</environment_info>")]),
        req([msg("assistant", "masking on")]),
        req([]),
        json.dumps({"input": "masking on"}).encode(),
        b"not json",
    ):
        assert ADAPTER.replace_newest_user_text(body, None, NEUTRAL) == body


def test_every_older_typed_command_in_the_resent_history_is_replaced_too():
    items = [msg("user", wrapped("masking on")), msg("assistant", "OK"),
             msg("user", wrapped("a sentence with masking off in it")),
             msg("user", "masking off"), msg("user", wrapped("last question"))]  # fmt: skip
    body = req(items)
    out = ADAPTER.replace_command_turns(body, None, NEUTRAL)
    new = json.loads(out)["input"]
    assert new[0]["content"][0]["text"] == wrapped(NEUTRAL)
    assert new[3]["content"][0]["text"] == NEUTRAL
    for i in (1, 2, 4):
        assert new[i] == items[i]
    assert ADAPTER.replace_command_turns(req(items[1:3]), None, NEUTRAL) == req(items[1:3])
    plain = json.dumps({"input": "masking on"}).encode()
    assert ADAPTER.replace_command_turns(plain, None, NEUTRAL) == plain
