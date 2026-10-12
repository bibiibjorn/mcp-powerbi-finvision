"""Phase 3a.2, Task E: why a masked Copilot reply was rehydrated (log: ``tokens=3``) and still showed
``CUS_61729`` ... in VS Code, and the proof that it is fixed.

Replayed: sample 778891, the post-tool reply of a claude model (three pseudonyms split over
``delta.content`` events, ``role`` on every delta, ``usage`` on the finish event), through the
``openai_chat`` rehydrator, through the front end's ``flow.response.stream`` and through the REAL
proxy (a local upstream that replays the recorded chunk boundaries, loopback only).

Result of the replay: **the stream conversion was correct** (no duplicate emission, no framing
problem, nothing after ``[DONE]``, the held tail leaves in front of the finish event, ``index`` and
``role`` kept). The defect was on the REQUEST side: the Power BI MCP layer had already pseudonymised
the tool result, the front end masked that result a second time (a pseudonym-shaped string in outgoing
text is a "token collision": a value of its own), the model echoed the SECOND pseudonym, and the
rehydrator turned it into the FIRST pseudonym (``tokens=3`` was true), not into the client name.
``tests/test_premasked_results.py`` pins the engine and adapter side; the tests below pin the visible
behaviour end to end. Synthetic names only; no AI host, no browser, no VS Code.
"""

from __future__ import annotations

import http.client as httpclient
import json
import re
from pathlib import Path
from typing import Any

import pytest
from mitmproxy import http
from mitmproxy.test import tflow

from client_gateway.adapters.openai_chat import OpenAIChatAdapter
from client_gateway.adapters.openai_responses import OpenAIResponsesAdapter
from client_gateway.adapters.base import MaskContext
from client_gateway.detectors import NER_ENV
from client_gateway.frontend import addon as addon_mod
from client_gateway.frontend.addon import FrontendAddon
from client_gateway.frontend.runner import FrontEnd
from tests._wire_support import (
    LAB_TOKENS,
    MARKER,
    NAME,
    NAME2,
    NAME3,
    body_bytes,
    copilot_slots,
    from_wire,
    load_fixture,
    make_state as wire_state,
    mask_stream,
    parse_chunks,
    relabel_request,
    relabel_stream,
    resplit,
    responses_slots,
    stream_text,
    to_wire,
    token_of,
)
from tests.test_frontend_addon import respond, stream_through
from tests.test_frontend_runner import LAZY, LOOPBACK, TIMEOUT, Upstream, make_state

FIXTURE = "copilot_post_tool_reply_stream"
LAB_PSEUDONYM = re.compile(r"CUS_\d{5}")


@pytest.fixture()
def state(tmp_path: Path):
    return wire_state(tmp_path)


def recorded(state: Any) -> tuple[list[str], dict[Any, str]]:
    chunks = load_fixture(FIXTURE)["response"]["chunks"]
    return relabel_stream(chunks, copilot_slots, state)


def displayed(text: str) -> str:
    """What a client shows: the ``delta.content`` of every event, joined."""
    return stream_text([text], copilot_slots)[(0, "content")]


# ---------------------------------------------------------------------------------- the recording
def test_the_recording_is_what_the_lab_session_saw(state):
    chunks = load_fixture(FIXTURE)["response"]["chunks"]
    events = [ev for chunk in parse_chunks(chunks) for ev in chunk]
    content = [e.payload for e in events if copilot_slots([e])]
    assert len(content) == 17 and all(
        c["choices"][0]["delta"]["role"] == "assistant" for c in content
    )
    text = "".join(c["choices"][0]["delta"]["content"] for c in content)
    assert len(LAB_PSEUDONYM.findall(text)) == 3
    for pseudonym in set(LAB_PSEUDONYM.findall(text)):  # every one is split over two events
        at = text.index(pseudonym)
        acc, cuts = 0, []
        for c in content:
            acc += len(c["choices"][0]["delta"]["content"])
            cuts.append(acc)
        assert any(at < cut < at + len(pseudonym) for cut in cuts), pseudonym
    finish = events[-2].payload
    assert (
        finish["choices"][0]["finish_reason"] == "stop"
        and finish["choices"][0]["delta"]["content"] is None
    )
    assert events[-1].lines == ["data: [DONE]"]


# ---------------------------------------------------------------------------------- the rehydrator
def test_the_recorded_reply_is_rehydrated_to_the_client_names_at_the_recorded_boundaries(state):
    masked, expected = recorded(state)
    ctx = MaskContext(state, marker=None)
    rehydrator = OpenAIChatAdapter().rehydrator("text/event-stream", None, ctx, None)
    out = [rehydrator.feed(c.encode()) for c in masked] + [rehydrator.finish()]
    text = b"".join(out).decode()
    assert displayed(text) == expected[(0, "content")]
    assert all(n in displayed(text) for n in (NAME, NAME2, NAME3))
    assert not any(token_of(state, n) in text for n in (NAME, NAME2, NAME3))
    assert not LAB_PSEUDONYM.search(text)
    assert rehydrator.stats["tokens"] == 3 and rehydrator.stats["errors"] == 0


def test_the_stream_shape_survives_the_conversion(state):
    masked, _ = recorded(state)
    rehydrator = OpenAIChatAdapter().rehydrator(
        "text/event-stream", None, MaskContext(state, marker=None), None
    )
    out = b"".join([rehydrator.feed(c.encode()) for c in masked] + [rehydrator.finish()]).decode()
    frames = [f for f in out.split("\n\n") if f]
    assert frames[-1] == "data: [DONE]" and out.endswith("\n\n")
    after_done = out.split("data: [DONE]", 1)[1]
    assert after_done == "\n\n"  # nothing after [DONE]
    events = [json.loads(f[6:]) for f in frames[:-1]]
    finish = [i for i, e in enumerate(events) if e["choices"][0].get("finish_reason")]
    assert finish == [len(events) - 1]  # the finish event is the last event, once
    assert events[-1]["usage"]["completion_tokens"] == 63
    for event in events:
        assert event["choices"][0]["index"] == 0 and {"id", "created", "model"} <= set(event)
    # the recorded events keep their role; only a flushed tail (no role) can be added to them
    with_role = [e for e in events if e["choices"][0]["delta"].get("role") == "assistant"]
    tails = [e for e in events[:-1] if "role" not in e["choices"][0]["delta"]]
    assert len(with_role) == 17 and len(tails) == rehydrator.stats["flushed_tails"] == 0


def test_no_text_is_emitted_twice(state):
    masked, expected = recorded(state)
    rehydrator = OpenAIChatAdapter().rehydrator(
        "text/event-stream", None, MaskContext(state, marker=None), None
    )
    out = b"".join([rehydrator.feed(c.encode()) for c in masked] + [rehydrator.finish()]).decode()
    pieces = [
        e["choices"][0]["delta"]["content"]
        for chunk in parse_chunks([out])
        for ev in chunk
        if (e := ev.payload)
        and e.get("choices")
        and isinstance(e["choices"][0]["delta"].get("content"), str)
    ]
    assert "".join(pieces) == expected[(0, "content")]
    assert len("".join(pieces)) == len(expected[(0, "content")])


@pytest.mark.parametrize("framing", ["lf", "crlf"])
@pytest.mark.parametrize("encoding", ["", "gzip", "br", "zstd"])
def test_every_wire_split_of_the_recorded_reply_displays_the_names(state, framing, encoding):
    masked, expected = recorded(state)
    if framing == "crlf":
        masked = [c.replace("\n", "\r\n") for c in masked]
    wire = b"".join(to_wire(masked, encoding))
    reference = None
    cuts = [[], [1], [5], [len(wire) // 2], list(range(1, len(wire), 7))]
    for cut in cuts:
        rehydrator = OpenAIChatAdapter().rehydrator(
            "text/event-stream", encoding or None, MaskContext(state, marker=None), None
        )
        out = [rehydrator.feed(c) for c in resplit(wire, cut)] + [rehydrator.finish()]
        text = from_wire([o for o in out if o], rehydrator.encoding_out)
        assert displayed(text.replace("\r\n", "\n")) == expected[(0, "content")], (framing, cut[:3])
        if reference is None:
            reference = text
        assert text == reference  # the same bytes whatever the splits
        assert not LAB_PSEUDONYM.search(text)


# ---------------------------------------------------------------------------------- the front end
def post(url: str, body: bytes) -> Any:
    req = http.Request.make("POST", url, b"", {"content-type": "application/json"})
    req.raw_content = body
    req.headers["content-length"] = str(len(body))
    return tflow.tflow(req=req)


def marked_request(state: Any) -> bytes:
    """The request of the turn that produced the recorded reply: the tool result is the one the
    Power BI MCP pseudonymised (it carries the marker)."""
    tree = {
        "model": "claude-haiku-4.5",
        "stream": True,
        "messages": [
            {"role": "user", "content": "<userRequest>\nShow me the customers\n</userRequest>"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "t1", "type": "function", "function": {"name": "lab", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "t1", "content": json.dumps({
                "_fv": MARKER, "rows": [
                    {"Customer": token_of(state, NAME), "Revenue": 1200},
                    {"Customer": token_of(state, NAME2), "Revenue": 800},
                    {"Customer": token_of(state, NAME3), "Revenue": 450}]})},
        ],
    }  # fmt: skip
    return json.dumps(tree).encode()


def test_through_the_front_end_the_names_are_displayed_and_the_request_is_not_masked_twice(
    tmp_path,
):
    state = make_state(tmp_path)
    addon = FrontendAddon(state)
    flow = post("https://api.individual.githubcopilot.com/chat/completions", marked_request(state))
    addon.process_request(flow)
    sent = flow.request.raw_content.decode()
    # the model sees exactly the pseudonyms the MCP issued: a reply that echoes one can be restored
    for n in (NAME, NAME2, NAME3):
        assert token_of(state, n) in sent
    assert len(set(re.findall(r"CUS_\d{5}", sent))) == 3
    assert state.smap.unmask_tree(json.loads(sent))["messages"][2]["content"].count("Zorblax") == 1

    masked, expected = recorded(state)
    wire = b"".join(c.encode() for c in masked)
    respond(flow, 200, {"content-type": "text/event-stream"})
    addon.responseheaders(flow)
    out = stream_through(flow, [c.encode() for c in masked])  # the recorded chunk boundaries
    text = b"".join(out).decode()
    assert displayed(text) == expected[(0, "content")] and not LAB_PSEUDONYM.search(text)
    assert (
        state.stats()["tokens_rehydrated"] == 3 and state.stats()["rehydration_unrecognised"] == 0
    )
    assert len(wire) > 0


def test_the_next_turn_resends_the_names_and_gets_the_same_pseudonyms(tmp_path):
    """The lab session's second symptom: the visible pseudonym was masked as a NEW value."""
    state = make_state(tmp_path)
    addon = FrontendAddon(state)
    first = post("https://api.individual.githubcopilot.com/chat/completions", marked_request(state))
    addon.process_request(first)
    masked, expected = recorded(state)
    respond(first, 200, {"content-type": "text/event-stream"})
    addon.responseheaders(first)
    stream_through(first, [c.encode() for c in masked])
    reply = expected[(0, "content")]  # what VS Code now shows and stores
    assert NAME in reply
    body = json.loads(marked_request(state))
    body["messages"] += [
        {"role": "assistant", "content": reply},
        {"role": "user", "content": "<userRequest>\nand again?\n</userRequest>"},
    ]
    second = post(
        "https://api.individual.githubcopilot.com/chat/completions", json.dumps(body).encode()
    )
    addon.process_request(second)
    sent = json.loads(second.request.raw_content)["messages"]
    assert sent[2]["content"] == json.loads(first.request.raw_content)["messages"][2]["content"]
    assert sent[3]["content"] == first_reply_as_sent(state, masked)  # the memo replays it
    assert len(set(re.findall(r"CUS_\d{5}", json.dumps(sent)))) == 3  # no fourth pseudonym


def first_reply_as_sent(state: Any, masked: list[str]) -> str:
    """The reply text exactly as the model wrote it (pseudonyms), joined from the masked stream."""
    return stream_text(masked, copilot_slots)[(0, "content")]


# ---------------------------------------------------------------------------------- the real proxy
@pytest.fixture(scope="module")
def live(tmp_path_factory):
    home = tmp_path_factory.mktemp("replay_home")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(NER_ENV.split("_", 1)[0] + "_MASKING_HOME", str(home))  # the isolated home
        mp.setenv(NER_ENV, "0")  # no name-recognition worker in a test
        state = make_state(home)
    front = FrontEnd(state, 0, options=LAZY)
    front.addon.routed = (*addon_mod.ROUTED_SUFFIXES, LOOPBACK)
    front.start()
    yield front, state
    front.stop()


@pytest.fixture()
def front_state(live):
    front, state = live
    state._frontend_turns.clear()
    state._sessions.clear()
    yield front, state


@pytest.fixture()
def upstream():
    server = Upstream()
    yield server
    server.close()


@pytest.fixture()
def routes(monkeypatch):
    chat, responses = OpenAIChatAdapter(), OpenAIResponsesAdapter()
    monkeypatch.setattr(
        addon_mod,
        "adapter_for",
        lambda method, host, path, ctype="": {
            "/chat/completions": chat,
            "/responses": responses,
        }.get(path),
    )


def post_via(front: FrontEnd, upstream: Upstream, path: str, body: bytes) -> tuple[int, bytes]:
    conn = httpclient.HTTPConnection(LOOPBACK, front.port, timeout=TIMEOUT)
    try:
        conn.request(
            "POST",
            f"http://{LOOPBACK}:{upstream.port}{path}",
            body,
            {"Host": f"{LOOPBACK}:{upstream.port}", "Content-Type": "application/json"},
        )
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


class EchoReply:
    """The model of the lab session: it echoes the pseudonyms IT RECEIVED. The recorded stream is
    replayed with the pseudonyms found in the request the upstream got, in their order of
    appearance, every recorded chunk boundary kept."""

    def __init__(self, upstream: Upstream) -> None:
        self.upstream = upstream

    def __iter__(self):
        sent = self.upstream.received[-1].decode()
        received = list(dict.fromkeys(re.findall(r"CUS_\d{5}", sent)))
        chunks = load_fixture(FIXTURE)["response"]["chunks"]
        masked, _ = mask_stream(chunks, copilot_slots, dict(zip(LAB_TOKENS, received)))
        return iter([c.encode() for c in masked])


def test_through_the_real_proxy_the_recorded_reply_shows_the_client_names(
    front_state, upstream, routes
):
    front, state = front_state
    _, expected = recorded(state)
    upstream.reply = EchoReply(upstream)  # type: ignore[assignment]
    status, body = post_via(front, upstream, "/chat/completions", marked_request(state))
    assert status == 200
    sent = upstream.received[0].decode()
    assert MARKER not in sent and not any(n in sent for n in (NAME, NAME2, NAME3))
    # the model got exactly the pseudonyms the MCP issued: a second masking pass would have
    # given it new ones that unmask to the first ones
    assert [token_of(state, n) in sent for n in (NAME, NAME2, NAME3)] == [True, True, True]
    text = body.decode()
    assert displayed(text) == expected[(0, "content")] and not LAB_PSEUDONYM.search(text)
    assert state.stats()["tokens_rehydrated"] >= 3


def test_through_the_real_proxy_a_responses_reply_shows_the_client_names(
    front_state, upstream, routes
):
    front, state = front_state
    chunks = load_fixture("copilot_responses_tool_result")["response"]["chunks"]
    masked, expected = relabel_stream(chunks, responses_slots, state)
    upstream.reply = [c.encode() for c in masked]
    request = relabel_request(body_bytes(load_fixture("copilot_responses_tool_result")), state)
    status, body = post_via(front, upstream, "/responses", request)
    assert status == 200
    sent = upstream.received[0].decode()
    assert MARKER not in sent and not any(n in sent for n in (NAME, NAME2, NAME3))
    text = body.decode()
    assert stream_text([text], responses_slots) == expected
    assert not LAB_PSEUDONYM.search(text)


# ---------------------------------------------------------------------------------- R2, real proxy
def chat_turns(url_path: str, *turns: str) -> bytes:
    from tests.test_frontend_copilot_identity import chat_body
    from tests.test_frontend_r2 import responses_body

    return (responses_body if url_path == "/responses" else chat_body)(*turns)


@pytest.mark.parametrize("path", ["/chat/completions", "/responses"])
def test_through_the_real_proxy_a_new_chat_is_byte_identical_right_after_a_pbi_chat(
    front_state, upstream, routes, path
):
    front, state = front_state
    upstream.reply = [b'data: {"choices":[{"index":0,"delta":{"content":"hi"}}]}\n\n']
    status, _ = post_via(front, upstream, "/chat/completions", marked_request(state))
    assert status == 200 and MARKER.encode() not in upstream.received[0]  # the PBI chat is masked
    request = chat_turns(path, "a brand new chat", f"mentions {NAME}")
    reply = [
        b'data: {"choices":[{"index":0,"delta":{"content":"about ' + NAME.encode() + b'"}}]}\n\n'
    ]
    upstream.reply = reply
    before = state.stats()
    status, body = post_via(front, upstream, path, request)
    assert status == 200 and upstream.received[-1] == request  # not one byte changed
    assert body == b"".join(reply)  # and the answer comes back untouched
    after = state.stats()
    assert after["frontend_blocked"] == before["frontend_blocked"]
    assert after["frontend_masked"] == before["frontend_masked"]
