"""Copilot ``/responses`` through the front end (phase 3a.2, Task C): the adapter is wired, a chat keeps
one identity across both Copilot APIs, a brand-new ``/responses`` chat is never touched (R2), and a
masked ``/responses`` stream is rehydrated through ``flow.response.stream``. Synthetic names only;
no sockets, no processes."""

from __future__ import annotations

import json
from typing import Any

import pytest
from mitmproxy import http
from mitmproxy.test import tflow

from client_gateway.adapters.streams import decode_bytes
from client_gateway.frontend.addon import FrontendAddon
from tests._wire_support import (
    MARKER,
    NAME,
    NAME2,
    all_names,
    body_bytes,
    from_wire,
    load_fixture,
    relabel_request,
    relabel_stream,
    resplit,
    responses_slots,
    stream_text,
    to_wire,
    token_of,
)
from tests.test_frontend_addon import respond, stream_through
from tests.test_frontend_copilot_identity import (
    chat_body,
    make_state,
    tool_result,
)
from tests.test_frontend_r2 import CHAT_URL, RESPONSES_URL, post, responses_body

JSON_HEADERS = {"content-type": "application/json"}


@pytest.fixture()
def state(tmp_path):
    return make_state(tmp_path)


@pytest.fixture()
def addon(state):
    return FrontendAddon(state)


def fixture_post(name: str, state: Any) -> Any:
    fx = load_fixture(name)
    r = fx["request"]
    req = http.Request.make(
        r["method"], f"https://{r['host']}{r['path']}", b"", {"content-type": r["content_type"]}
    )
    body = relabel_request(body_bytes(fx), state)
    req.raw_content = body
    req.headers["content-length"] = str(len(body))
    return tflow.tflow(req=req)


def responses_tool_result() -> dict[str, Any]:
    return {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": json.dumps({"_fv": MARKER, "rows": [{"c": NAME}]}),
    }


# ---------------------------------------------------------------------------------- the adapter
@pytest.mark.parametrize("name", ["copilot_responses_tool_result", "copilot_responses_tool_call"])
def test_a_marked_responses_request_is_masked_and_loses_its_marker(addon, state, name):
    flow = fixture_post(name, state)
    addon.process_request(flow)
    assert flow.response is None and flow.metadata["fv_frontend"].mode == "on"
    raw = flow.request.raw_content.decode()
    assert not any(n in raw for n in all_names()) and MARKER not in raw
    assert int(flow.request.headers["content-length"]) == len(flow.request.raw_content)
    assert json.loads(raw)["input"]  # still valid JSON
    assert state.stats()["frontend_masked"] == 1 and state.stats()["residual_leak_blocked"] == 0


def test_a_gzip_responses_request_keeps_its_content_encoding(addon, state):
    from client_gateway.adapters.streams import encode_bytes

    flow = fixture_post("copilot_responses_tool_result", state)
    flow.request.raw_content = encode_bytes(flow.request.raw_content, "gzip")
    flow.request.headers["content-encoding"] = "gzip"
    addon.process_request(flow)
    text = decode_bytes(flow.request.raw_content, "gzip").decode()
    assert flow.request.headers["content-encoding"] == "gzip"
    assert not any(n in text for n in all_names()) and MARKER not in text


def test_an_unmarked_first_responses_turn_is_forwarded_byte_identical(addon, state):
    flow = fixture_post("copilot_responses_first", state)
    before = flow.request.raw_content
    addon.process_request(flow)
    assert flow.response is None and flow.request.raw_content == before
    assert any(n in before.decode() for n in all_names())  # it does name a client: R2 still holds
    assert state.stats()["frontend_masked"] == 0 and state.stats()["frontend_blocked"] == 0


def test_a_name_in_the_tools_list_of_a_responses_request_is_caught_by_the_scan(addon, state):
    tree = json.loads(
        relabel_request(body_bytes(load_fixture("copilot_responses_tool_result")), state)
    )
    tree["tools"].append({"type": "function", "name": "t", "description": f"about {NAME}"})
    flow = post(RESPONSES_URL, json.dumps(tree).encode())
    addon.process_request(flow)
    assert flow.response.status_code == 400 and "residual_leak" in flow.response.get_text()
    assert state.stats()["residual_leak_blocked"] == 1


def test_a_responses_body_of_another_shape_in_a_masked_chat_is_blocked_as_format_changed(
    addon, state
):
    body = json.dumps({"model": "m", "messages": [], "_fv": MARKER}).encode()
    flow = post(RESPONSES_URL, body)
    addon.process_request(flow)
    assert flow.response.status_code == 400 and "unsupported_format" in flow.response.get_text()
    assert state.stats()["format_changed"] == 1


def test_an_input_file_in_a_masked_responses_chat_is_blocked(addon, state):
    part = {"type": "input_file", "file_data": "AAAA", "filename": "x.pdf"}
    items = [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "<userRequest>\nread it\n</userRequest>"},
                part,
            ],
        },
        responses_tool_result(),
    ]
    flow = post(RESPONSES_URL, json.dumps({"model": "m", "input": items}).encode())
    addon.process_request(flow)
    assert flow.response.status_code == 400 and "unsupported_format" in flow.response.get_text()


# ---------------------------------------------------------------------------------- one chat, two APIs
def test_a_chat_keeps_its_identity_when_copilot_switches_api(addon, state):
    first = post(CHAT_URL, chat_body("open the customer chat", tail=[tool_result()]))
    addon.process_request(first)
    assert first.metadata["fv_frontend"].mode == "on"
    # the next turn goes to /responses; the marker was compacted away
    later = post(RESPONSES_URL, responses_body("open the customer chat", f"about {NAME}?"))
    addon.process_request(later)
    assert later.response is None and later.metadata["fv_frontend"].mode == "on"
    assert NAME not in later.request.raw_content.decode()
    # and a brand-new chat on either API is untouched
    for url, build in ((RESPONSES_URL, responses_body), (CHAT_URL, chat_body)):
        body = build("a brand new chat", f"about {NAME}?")
        other = post(url, body)
        addon.process_request(other)
        assert other.response is None and other.request.raw_content == body


def test_the_other_direction_works_too(addon, state):
    marked = post(RESPONSES_URL, responses_body("the chat", tail=[
        {"type": "function_call_output", "call_id": "c", "output": json.dumps({"_fv": MARKER, "x": 1})}
    ]))  # fmt: skip
    addon.process_request(marked)
    later = post(CHAT_URL, chat_body("the chat", f"about {NAME}?"))
    addon.process_request(later)
    assert (
        later.metadata["fv_frontend"].mode == "on"
        and NAME not in later.request.raw_content.decode()
    )


def test_typed_switches_follow_the_chat_across_apis(addon, state):
    addon.process_request(post(CHAT_URL, chat_body("the chat", tail=[tool_result()])))
    addon.process_request(post(RESPONSES_URL, responses_body("the chat", "masking off")))
    body = chat_body("the chat", "masking off", f"{NAME} again")
    after = post(CHAT_URL, body)
    addon.process_request(after)
    assert NAME in after.request.raw_content.decode()  # switched off on /responses, off on both


def test_a_side_call_of_a_responses_turn_follows_its_chat(addon, state):
    addon.process_request(
        post(RESPONSES_URL, responses_body("the chat", tail=[responses_tool_result()]), turn="t-1")
    )
    from tests.test_frontend_copilot_identity import side_body

    side = post(
        CHAT_URL, side_body(f"about {NAME}"), turn="t-1"
    )  # the title call uses chat/completions
    addon.process_request(side)
    assert NAME not in side.request.raw_content.decode()
    assert state.stats()["frontend_turn_mapped"] == 1


# ---------------------------------------------------------------------------------- the stream
@pytest.mark.parametrize("encoding", ["", "gzip", "br"])
def test_a_responses_stream_is_rehydrated_split_safe_in_its_own_encoding(addon, state, encoding):
    flow = fixture_post("copilot_responses_tool_result", state)
    addon.process_request(flow)
    chunks = load_fixture("copilot_responses_tool_result")["response"]["chunks"]
    masked, expected = relabel_stream(chunks, responses_slots, state)
    wire = b"".join(to_wire(masked, encoding))
    headers = {"content-type": "text/event-stream"}
    if encoding:
        headers["content-encoding"] = encoding
    respond(flow, 200, headers)
    addon.responseheaders(flow)
    assert callable(flow.response.stream)
    out = stream_through(flow, resplit(wire, [7, 100, len(wire) // 3, len(wire) // 2 + 1]))
    text = from_wire(out, encoding or "identity")
    assert stream_text([text], responses_slots) == expected
    tokens = [token_of(state, n) for n in (NAME, NAME2)]
    assert not any(t in text for t in tokens)
    assert flow.response.headers.get("content-encoding") == (encoding or None)
    stats = state.stats()
    assert stats["responses_rehydrated"] == 1 and stats["tokens_rehydrated"] > 0
    assert (
        stats["rehydration_unrecognised"] == 0
        and "rehydration_unrecognised" not in state.status()["warnings"]
    )


def test_a_responses_reply_of_an_unknown_shape_is_flagged(addon, state):
    flow = fixture_post("copilot_responses_tool_result", state)
    addon.process_request(flow)
    respond(flow, 200, {"content-type": "text/event-stream"})
    addon.responseheaders(flow)
    flow.response.stream(b'data: {"outputs":[{"text":"x"}]}\n\n')
    flow.response.stream(b"")
    assert state.stats()["rehydration_unrecognised"] == 1


def test_a_buffered_responses_reply_is_unmasked(addon, state):
    flow = fixture_post("copilot_responses_tool_result", state)
    addon.process_request(flow)
    token = token_of(state, NAME)
    message = {"type": "message", "role": "assistant", "id": "m"}
    message["content"] = [{"type": "output_text", "text": f"About {token}."}]
    body = json.dumps({"id": "r1", "output": [message]}).encode()
    respond(flow, 200, {"content-type": "application/json"}, body)
    addon.responseheaders(flow)
    addon.response(flow)
    tree = json.loads(flow.response.raw_content)
    assert tree["output"][0]["content"][0]["text"] == f"About {NAME}."
