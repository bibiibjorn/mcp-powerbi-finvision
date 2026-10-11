"""``client_gateway.frontend.addon`` (phase 3a): the masking decisions of the TLS front end, driven
with mitmproxy's test flows and the sanitized wire fixtures. Synthetic names only; no sockets, no
processes, no network (the real-proxy checks are in ``test_frontend_runner.py``)."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from mitmproxy import http
from mitmproxy.test import tflow
from mitmproxy.websocket import WebSocketMessage
from wsproto.frame_protocol import Opcode

from client_gateway.adapters.base import MaskContext
from client_gateway.adapters.streams import decode_bytes, encode_bytes
from client_gateway.frontend.addon import (
    BLOCK_UNKNOWN_TEXT,
    META_KEY,
    FrontendAddon,
    StreamBridge,
    allow_hosts_pattern,
    is_routed_host,
)
from client_gateway.masking import MaskingFailed
from client_gateway.state import MODE_ON, MODE_PASSTHROUGH, GatewayState
from core.client_guard.marker import has_marker
from tests._wire_support import (
    MARKER,
    NAME,
    NAME2,
    all_names,
    body_bytes,
    chatgpt_slots,
    copilot_slots,
    from_wire,
    load_fixture,
    mask_stream,
    resplit,
    stream_text,
    synthetic_clients,
    to_wire,
    token_of,
)

CHATGPT = "chatgpt_web"
CID = "00000000-0000-4000-8000-000000000004"  # the conversation id of the recorded first turn
OTHER_CID = "00000000-0000-4000-8000-000000000099"
COPILOT_URL = "https://api.individual.githubcopilot.com/chat/completions"
CONVERSATION_URL = "https://chatgpt.com/backend-api/f/conversation"
JSON_HEADERS = {"content-type": "application/json"}


def make_state(home: Path, policy: str = "withhold") -> GatewayState:
    """Masking OFF by default (a chat is masked only by the marker or a typed switch); the install
    marker is the one the wire fixtures carry."""
    (home / "install.id").write_text("0123456789abcdef\n", encoding="ascii")
    state = GatewayState(home=home, default_mode="off", images_policy=policy, ready_wait_s=0.05)
    for prefix, values in synthetic_clients().items():
        state.smap.add_values(prefix, values, "test")
    assert state.marker == MARKER
    return state


@pytest.fixture()
def state(tmp_path):
    return make_state(tmp_path)


@pytest.fixture()
def addon(state):
    return FrontendAddon(state)


def flow_of(
    method: str,
    url: str,
    body: bytes = b"",
    headers: dict[str, str] | None = None,
    encoding: str | None = None,
) -> Any:
    req = http.Request.make(method, url, b"", dict(headers or {}))
    if body:
        raw = encode_bytes(body, encoding) if encoding else body
        if encoding:
            req.headers["content-encoding"] = encoding
        req.raw_content = raw
        req.headers["content-length"] = str(len(raw))
    return tflow.tflow(req=req)


def fixture_flow(name: str, encoding: str | None = None, body: bytes | None = None) -> Any:
    r = load_fixture(name)["request"]
    headers = {"content-type": r["content_type"]} if r["content_type"] else {}
    data = body if body is not None else (body_bytes(load_fixture(name)) if r.get("body") else b"")
    return flow_of(r["method"], f"https://{r['host']}{r['path']}", data, headers, encoding)


def sha(data: bytes | None) -> str:
    return hashlib.sha256(data or b"").hexdigest()


def request_json(flow: Any) -> Any:
    enc = flow.request.headers.get("content-encoding")
    return json.loads(decode_bytes(flow.request.raw_content, enc))


def chatgpt_turn(text: str, conversation_id: str | None = None, marker: bool = False) -> bytes:
    """The recorded first turn with another user text (and a conversation id for a follow-up)."""
    tree = load_fixture("chatgpt_conversation_turn1")["request"]["body"]
    user = [m for m in tree["messages"] if m["author"]["role"] == "user"][-1]
    user["content"]["parts"] = [text + (f" {MARKER}" if marker else "")]
    if conversation_id:
        tree["conversation_id"] = conversation_id
    return json.dumps(tree, ensure_ascii=False, separators=(",", ":")).encode()


def wrapped_user(text: str) -> dict[str, Any]:
    return {"role": "user", "content": f"<userRequest>\n{text}\n</userRequest>"}


def copilot_turn(text: str, *extra: dict[str, Any], first: str | None = None) -> bytes:
    """A Copilot request whose newest typed text is ``text``. The chat is identified by its FIRST
    user turn: ``first`` makes ``text`` a later turn of the chat that opened with ``first``."""
    messages = [wrapped_user(text), *extra]
    if first is not None:
        messages = [wrapped_user(first), {"role": "assistant", "content": "ok"}, *messages]
    return json.dumps({"model": "m", "messages": messages, "stream": True}).encode()


def marked_tool_result(text: str = "") -> dict[str, Any]:
    payload = json.dumps({"_fv": MARKER, "rows": text})
    return {"role": "tool", "tool_call_id": "call_1", "content": payload}


def tokens_of(state: GatewayState) -> dict[str, str]:
    return {NAME: token_of(state, NAME), NAME2: token_of(state, NAME2)}


def respond(flow: Any, status: int, headers: dict[str, str], body: bytes = b"") -> Any:
    flow.response = http.Response.make(status, b"", headers)
    if body:
        flow.response.raw_content = body
        flow.response.headers["content-length"] = str(len(body))
    return flow.response


def stream_through(flow: Any, wire: list[bytes]) -> list[bytes]:
    """What the app receives: every chunk through ``response.stream`` and the final ``b""``."""
    out = [flow.response.stream(chunk) for chunk in wire]
    out.append(flow.response.stream(b""))  # how mitmproxy signals the end of the body
    return [o for o in out if o]


# ---------------------------------------------------------------------------------- routing
def test_only_the_routed_ai_hosts_and_their_subdomains_are_routed():
    for host in (
        "chatgpt.com", "ab.chatgpt.com", "openai.com",
        "files.oaiusercontent.com", "api.individual.githubcopilot.com", "CHATGPT.COM.",
    ):  # fmt: skip
        assert is_routed_host(host), host
    for host in ("example.com", "notchatgpt.com", "chatgpt.com.evil.example", "github.com", ""):
        assert not is_routed_host(host), host
    # Claude Desktop has no adapter yet: its traffic is never decrypted (an unknown format in a
    # Power BI chat would be blocked and break the app); the MCP layer masks its Power BI results
    for host in ("claude.ai", "api.claude.ai"):
        assert not is_routed_host(host), host


def test_the_allow_hosts_regex_matches_exactly_the_routed_hosts():
    import re

    rx = re.compile(allow_hosts_pattern(), re.IGNORECASE)
    assert rx.search("chatgpt.com:443") and rx.search("api.individual.githubcopilot.com:443")
    assert rx.search("FILES.OAIUSERCONTENT.COM:443") and not rx.search("claude.ai:443")
    assert not rx.search("example.com:443") and not rx.search("evilchatgpt.com:443")
    assert not rx.search("chatgpt.com.evil.example:443") and not rx.search("127.0.0.1:443")


# ---------------------------------------------------------------------------------- passthrough
@pytest.mark.parametrize(
    "name",
    [
        "copilot_title_stream",  # holds a client name, no marker: R2, the chat is not a PBI chat
        "chatgpt_conversation_turn1",
        "chatgpt_conversation_image_turn",
        "chatgpt_prepare_followup",
        "chatgpt_files_metadata",
        "chatgpt_conversation_get",
    ],
)
def test_a_request_of_a_chat_that_is_not_a_pbi_chat_is_forwarded_byte_identical(addon, state, name):
    flow = fixture_flow(name)
    before = (sha(flow.request.raw_content), list(flow.request.headers.items(True)))
    addon.process_request(flow)
    assert flow.response is None
    assert (sha(flow.request.raw_content), list(flow.request.headers.items(True))) == before
    stats = state.stats()
    assert stats["frontend_requests"] == 1 and stats["frontend_passthrough"] == 1
    assert stats["frontend_masked"] == 0 and stats["frontend_blocked"] == 0


def test_a_host_that_is_not_routed_is_never_read(addon, state):
    flow = flow_of(
        "POST", "https://example.com/chat/completions", copilot_turn("hi", marked_tool_result())
    )
    before = sha(flow.request.raw_content)
    addon.process_request(flow)
    assert flow.response is None and sha(flow.request.raw_content) == before
    assert META_KEY not in flow.metadata and state.stats()["frontend_passthrough"] == 1


def test_an_unknown_request_without_a_masked_chat_is_untouched(addon):
    body = json.dumps({"event": "telemetry", "who": NAME}).encode()
    flow = flow_of("POST", "https://chatgpt.com/ces/v1/t", body, JSON_HEADERS)
    addon.process_request(flow)
    assert flow.response is None and flow.request.raw_content == body


# ---------------------------------------------------------------------------------- Copilot
def test_a_marked_copilot_request_is_masked_and_the_marker_is_stripped(addon, state):
    flow = fixture_flow("copilot_tool_result_image")
    assert has_marker(flow.request.raw_content, MARKER)
    addon.process_request(flow)
    assert flow.response is None
    raw = flow.request.raw_content
    assert not any(name in raw.decode() for name in all_names())
    assert MARKER not in raw.decode() and int(flow.request.headers["content-length"]) == len(raw)
    assert json.loads(raw)["messages"]  # still valid JSON
    assert state.stats()["frontend_masked"] == 1
    assert flow.metadata[META_KEY].mode == MODE_ON


def test_a_marked_gzip_request_keeps_its_content_encoding(addon):
    flow = fixture_flow("copilot_tool_result_image", encoding="gzip")
    addon.process_request(flow)
    assert flow.request.headers["content-encoding"] == "gzip"
    raw = flow.request.raw_content
    assert int(flow.request.headers["content-length"]) == len(raw)
    text = decode_bytes(raw, "gzip").decode()
    assert not any(name in text for name in all_names()) and MARKER not in text


def test_a_chunked_request_is_sent_with_a_length_once_it_is_masked(addon):
    flow = flow_of("POST", COPILOT_URL, copilot_turn(NAME, marked_tool_result()), JSON_HEADERS)
    flow.request.headers["transfer-encoding"] = "chunked"
    del flow.request.headers["content-length"]
    addon.process_request(flow)
    assert "transfer-encoding" not in flow.request.headers
    assert int(flow.request.headers["content-length"]) == len(flow.request.raw_content)


def test_a_copilot_request_that_cannot_be_masked_is_answered_locally(addon, state):
    flow = flow_of("POST", COPILOT_URL, b"not json " + MARKER.encode(), JSON_HEADERS)
    addon.process_request(flow)
    assert flow.response is not None and flow.response.status_code == 400
    text = flow.response.get_text()
    assert "bad_json" in text and "did not leave this machine" in text
    assert MARKER not in text and "not json" not in text
    assert flow.response.headers["x-should-retry"] == "false"
    assert state.stats()["frontend_blocked"] == 1 and META_KEY not in flow.metadata


def test_a_blocked_response_never_echoes_client_data(addon):
    flow = flow_of(
        "POST", COPILOT_URL, json.dumps({"messages": "x", "who": NAME, "_fv": MARKER}).encode(),
        JSON_HEADERS,
    )  # fmt: skip
    addon.process_request(flow)
    assert flow.response.status_code == 400
    assert not any(name in flow.response.get_text() for name in all_names())


def test_a_closed_dictionary_gate_blocks_with_the_not_ready_text(addon, state):
    state.set_ready(False, "dictionary_empty")
    flow = fixture_flow("copilot_tool_result_image")
    original = flow.request.raw_content
    addon.process_request(flow)
    assert flow.response.status_code == 400 and "dictionary" in flow.response.get_text()
    assert flow.request.raw_content == original  # the request was not touched
    assert state.stats()["not_ready"] == 1


def test_an_unexpected_error_in_a_masked_flow_fails_closed(addon, state, monkeypatch):
    def boom(flow: Any, plan: Any) -> None:
        raise RuntimeError("secret " + NAME)

    monkeypatch.setattr(addon, "_mask", boom)
    flow = fixture_flow("copilot_tool_result_image")  # marked: a masked flow
    addon.safe_request(flow)
    assert flow.response.status_code == 400 and NAME not in flow.response.get_text()
    assert state.stats()["errors"] == 1 and state.stats()["frontend_blocked"] == 1


def test_the_request_hook_masks_in_a_worker_and_done_stops_the_pool(addon):
    flow = fixture_flow("copilot_tool_result_image")

    async def run() -> None:
        await addon.request(flow)
        await addon.done()

    asyncio.run(run())
    assert flow.metadata[META_KEY].mode == MODE_ON
    assert not any(name in flow.request.raw_content.decode() for name in all_names())
    assert addon._pool is None


# ---------------------------------------------------------------------------------- typed commands
def test_typed_masking_on_and_off_in_a_chatgpt_chat(addon, state):
    on = flow_of("POST", CONVERSATION_URL, chatgpt_turn("masking on", CID), JSON_HEADERS)
    addon.process_request(on)
    assert state.frontend_chat_masked(CHATGPT, CID)
    follow = flow_of("POST", CONVERSATION_URL, chatgpt_turn(f"hi {NAME}", CID), JSON_HEADERS)
    addon.process_request(follow)
    assert NAME not in follow.request.raw_content.decode()  # masked: the chat was switched on
    untouched = flow_of(
        "POST", CONVERSATION_URL, chatgpt_turn(f"hi {NAME}", OTHER_CID), JSON_HEADERS
    )
    addon.process_request(untouched)
    assert NAME in untouched.request.raw_content.decode()  # another chat: not masked
    off = flow_of("POST", CONVERSATION_URL, chatgpt_turn("/masking OFF", CID), JSON_HEADERS)
    addon.process_request(off)
    after = flow_of("POST", CONVERSATION_URL, chatgpt_turn(f"hi {NAME}", CID), JSON_HEADERS)
    addon.process_request(after)
    assert NAME in after.request.raw_content.decode()  # switched back off, by the human's text


def test_a_phrase_inside_a_sentence_is_not_a_command(addon, state):
    for text in ("please type masking off now", "masking on please", "masking off\nthanks"):
        flow = flow_of("POST", CONVERSATION_URL, chatgpt_turn(text, CID), JSON_HEADERS)
        addon.process_request(flow)
    assert not state.frontend_chat_masked(CHATGPT, CID)
    assert state.frontend_mode(CID, False, CHATGPT) == MODE_PASSTHROUGH
    assert state.status()["sessions"]["count"] == 1  # only the chat that was asked about


def test_a_command_in_the_half_typed_draft_is_not_a_command(addon, state):
    fx = load_fixture("chatgpt_prepare_followup")["request"]["body"]
    fx["partial_query"]["content"]["parts"] = ["masking on"]
    flow = flow_of(
        "POST",
        "https://chatgpt.com/backend-api/f/conversation/prepare",
        json.dumps(fx).encode(),
        JSON_HEADERS,
    )
    addon.process_request(flow)
    assert not state.frontend_chat_masked(CHATGPT, fx["conversation_id"])


def test_typed_masking_off_unmasks_a_pbi_copilot_request_without_a_token(addon, state):
    token = token_of(state, NAME)
    flow = flow_of(
        "POST",
        COPILOT_URL,
        copilot_turn(
            "masking off",
            {"role": "assistant", "content": f"The customer is {token}."},
            marked_tool_result(),
            first="open the chat",
        ),
        JSON_HEADERS,
    )  # the wrapper is cut away: the whole typed message is the command
    addon.process_request(flow)
    text = flow.request.raw_content.decode()
    assert NAME in text and token not in text and MARKER not in text
    assert state.stats()["pbi_unmasked_requests"] == 1
    # the switch holds for the next request of that window
    nxt = flow_of(
        "POST",
        COPILOT_URL,
        copilot_turn(f"and {NAME}", marked_tool_result(), first="open the chat"),
        JSON_HEADERS,
    )
    addon.process_request(nxt)
    assert NAME in nxt.request.raw_content.decode()
    other_chat = flow_of(
        "POST", COPILOT_URL, copilot_turn(f"and {NAME}", marked_tool_result()), JSON_HEADERS
    )  # another chat: its own marker makes it a PBI chat of its own, masked
    addon.process_request(other_chat)
    assert NAME not in other_chat.request.raw_content.decode()


# ---------------------------------------------------------------------------------- first turns
def test_a_first_chatgpt_turn_is_bound_to_the_id_the_response_reports(addon, state):
    fx = load_fixture("chatgpt_conversation_turn1")
    flow = flow_of(
        "POST", CONVERSATION_URL, chatgpt_turn(f"about {NAME}", marker=True), JSON_HEADERS
    )
    addon.process_request(flow)
    assert (
        NAME not in flow.request.raw_content.decode()
        and MARKER not in flow.request.raw_content.decode()
    )
    assert not state.frontend_chat_masked(CHATGPT, CID)  # no id yet

    chunks = fx["response"]["chunks"]
    masked, expected = mask_stream(chunks, chatgpt_slots, tokens_of(state))
    wire = to_wire(masked, "gzip")
    respond(
        flow, 200, {"content-type": "text/event-stream; charset=utf-8", "content-encoding": "gzip",
                    "content-length": "1"},
    )  # fmt: skip
    addon.responseheaders(flow)
    assert "content-length" not in flow.response.headers and callable(flow.response.stream)
    out = stream_through(flow, resplit(b"".join(wire), [37, 311, 4000]))
    text = from_wire(out, "gzip")
    assert stream_text([text], chatgpt_slots) == expected  # rehydrated, split-safe
    assert not any(t in text for t in tokens_of(state).values())
    # the id of the response made the chat a PBI chat for good
    assert state.frontend_mode(CID, False, CHATGPT) == MODE_ON
    assert state.stats()["responses_rehydrated"] == 1


def test_a_first_turn_with_a_typed_command_binds_the_switch_to_the_new_chat(addon, state):
    fx = load_fixture("chatgpt_conversation_turn1")
    flow = flow_of("POST", CONVERSATION_URL, chatgpt_turn("masking on"), JSON_HEADERS)
    addon.process_request(flow)
    assert flow.metadata[META_KEY].mode == MODE_ON
    masked, _ = mask_stream(fx["response"]["chunks"], chatgpt_slots, tokens_of(state))
    respond(flow, 200, {"content-type": "text/event-stream", "content-encoding": "gzip"})
    addon.responseheaders(flow)
    stream_through(flow, to_wire(masked, "gzip"))
    assert state.frontend_chat_masked(CHATGPT, CID)
    follow = flow_of("POST", CONVERSATION_URL, chatgpt_turn(f"hi {NAME}", CID), JSON_HEADERS)
    addon.process_request(follow)
    assert NAME not in follow.request.raw_content.decode()


# ---------------------------------------------------------------------------------- streams
@pytest.mark.parametrize("encoding", ["", "gzip", "br", "zstd"])
def test_a_copilot_stream_is_rehydrated_split_safe_in_its_own_encoding(addon, state, encoding):
    fx = load_fixture("copilot_tool_call_stream")
    flow = flow_of("POST", COPILOT_URL, copilot_turn("hi", marked_tool_result()), JSON_HEADERS)
    addon.process_request(flow)
    masked, expected = mask_stream(fx["response"]["chunks"], copilot_slots, tokens_of(state))
    wire = b"".join(to_wire(masked, encoding))
    headers = {"content-type": "text/event-stream"}
    if encoding:
        headers["content-encoding"] = encoding
    respond(flow, 200, headers)
    addon.responseheaders(flow)
    cuts = [7, 100, len(wire) // 3, len(wire) // 2 + 1]
    out = stream_through(flow, resplit(wire, cuts))
    text = from_wire(out, encoding or "identity")
    assert stream_text([text], copilot_slots) == expected
    assert not any(t in text for t in tokens_of(state).values())
    assert flow.response.headers.get("content-encoding") == (encoding or None)


def test_the_held_tail_is_flushed_when_mitmproxy_ends_the_body(addon, state):
    flow = flow_of("POST", COPILOT_URL, copilot_turn("hi", marked_tool_result()), JSON_HEADERS)
    addon.process_request(flow)
    token = token_of(state, NAME)
    respond(flow, 200, {"content-type": "text/event-stream"})
    addon.responseheaders(flow)

    def frame(text: str) -> bytes:
        payload = {"choices": [{"index": 0, "delta": {"content": text}}]}
        return b"data: " + json.dumps(payload).encode() + b"\n\n"

    # the stream ends right after a complete token (no finish_reason, no [DONE]): the token could
    # still grow into a longer one, so it is held until the end of the body
    first = flow.response.stream(frame("Customer: "))
    second = flow.response.stream(frame(token))
    assert NAME.encode() not in first + second and token.encode() not in first + second
    end = flow.response.stream(b"")
    assert NAME.encode() in end and token.encode() not in end
    assert flow.response.stream(b"") == b""  # a second end signal adds nothing
    assert state.stats()["responses_rehydrated"] == 1


def test_a_stream_bridge_lets_masked_bytes_through_when_conversion_fails(state):
    class Broken:
        stats = {"tokens": 0, "errors": 0}

        def feed(self, chunk: bytes) -> bytes:
            raise RuntimeError("boom")

        def finish(self) -> bytes:
            raise RuntimeError("boom")

    bridge = StreamBridge(Broken(), MaskContext(state), state)
    assert bridge(b"data: x\n\n") == b"data: x\n\n"
    assert bridge(b"") == b""
    assert state.stats()["errors"] == 2


def test_a_stream_of_a_flow_that_is_not_masked_is_left_alone(addon, state):
    flow = fixture_flow("copilot_title_stream")  # no marker: passthrough
    addon.process_request(flow)
    respond(flow, 200, {"content-type": "text/event-stream"})
    flow.response.headers["content-length"] = "5"
    addon.responseheaders(flow)
    addon.response(flow)
    assert not flow.response.stream and flow.response.headers["content-length"] == "5"


# ---------------------------------------------------------------------------------- buffered replies
def test_a_stored_conversation_of_a_masked_chat_is_unmasked_in_its_encoding(addon, state):
    state.frontend_mode(CID, True, CHATGPT)  # the chat is a PBI chat
    token = token_of(state, NAME)
    flow = flow_of("GET", f"https://chatgpt.com/backend-api/conversations/{CID}")
    addon.process_request(flow)
    assert flow.metadata[META_KEY].mode == MODE_ON and not flow.request.raw_content
    stored = {
        "title": f"About {token}",
        "conversation_id": CID,
        "mapping": {"a": {"message": {"content": {"parts": [f"Hello {token}"]}}}},
    }
    body = encode_bytes(json.dumps(stored).encode(), "br")
    respond(flow, 200, {"content-type": "application/json", "content-encoding": "br"}, body)
    addon.responseheaders(flow)
    addon.response(flow)
    raw = flow.response.raw_content
    assert flow.response.headers["content-encoding"] == "br"
    assert int(flow.response.headers["content-length"]) == len(raw)
    tree = json.loads(decode_bytes(raw, "br"))
    assert tree["title"] == f"About {NAME}" and tree["conversation_id"] == CID
    assert tree["mapping"]["a"]["message"]["content"]["parts"] == [f"Hello {NAME}"]


# ---------------------------------------------------------------------------------- unknown bodies
def test_an_unknown_body_in_a_masked_chat_is_blocked_with_a_fixed_text(addon, state):
    state.frontend_mode(CID, True, CHATGPT)
    flow = flow_of(
        "PATCH",
        f"https://chatgpt.com/backend-api/conversation/{CID}",
        json.dumps({"title": f"About {NAME}"}).encode(),
        JSON_HEADERS,
    )
    addon.process_request(flow)
    assert flow.response.status_code == 400
    assert BLOCK_UNKNOWN_TEXT in flow.response.get_text() and NAME not in flow.response.get_text()
    assert state.stats()["frontend_blocked"] == 1


def test_an_unknown_body_that_carries_the_marker_is_blocked(addon):
    flow = flow_of(
        "POST", "https://chatgpt.com/backend-api/unknown",
        json.dumps({"x": MARKER}).encode(), JSON_HEADERS,
    )  # fmt: skip
    addon.process_request(flow)
    assert flow.response.status_code == 400


def test_an_unknown_body_in_a_chat_that_is_not_masked_is_untouched(addon):
    body = json.dumps({"title": f"About {NAME}"}).encode()
    flow = flow_of(
        "PATCH", f"https://chatgpt.com/backend-api/conversation/{OTHER_CID}", body, JSON_HEADERS
    )
    addon.process_request(flow)
    assert flow.response is None and flow.request.raw_content == body


def test_a_body_in_an_encoding_nobody_can_read_is_blocked_only_in_a_masked_chat(addon, state):
    state.frontend_mode(CID, True, CHATGPT)
    url = f"https://chatgpt.com/backend-api/conversation/{CID}"
    flow = flow_of("PATCH", url, b"\x00\x01", JSON_HEADERS)
    flow.request.headers["content-encoding"] = "x-weird"
    addon.process_request(flow)
    assert flow.response.status_code == 400
    plain = flow_of("PATCH", url.replace(CID, OTHER_CID), b"\x00\x01", JSON_HEADERS)
    plain.request.headers["content-encoding"] = "x-weird"
    addon.process_request(plain)
    assert plain.response is None


# ---------------------------------------------------------------------------------- uploads
PNG_BYTES = b"\x89PNG-bytes"


def upload_flow(data: bytes = PNG_BYTES, ctype: str = "image/png") -> Any:
    return flow_of(
        "PUT",
        "https://files-test.oaiusercontent.com/files/00000000-0000-4000-8000-000000000026/raw",
        data,
        {"content-type": ctype},
    )


def test_an_upload_under_the_global_mode_follows_the_image_policy(tmp_path):
    allow = make_state(_dir(tmp_path, "a"), "pass")
    addon = FrontendAddon(allow)
    allow.set_mode("", "on", allow.token)  # an upload names no chat: only the global mode masks it
    flow = upload_flow()
    addon.process_request(flow)
    assert flow.response is None and flow.request.raw_content == PNG_BYTES

    withhold = make_state(_dir(tmp_path, "b"), "withhold")
    strict = FrontendAddon(withhold)
    withhold.set_mode("", "on", withhold.token)
    blocked = upload_flow()
    strict.process_request(blocked)
    assert blocked.response.status_code == 400 and "image_withheld" in blocked.response.get_text()


def _dir(base: Path, name: str) -> Path:
    path = base / name
    path.mkdir()
    return path


def test_redacted_upload_bytes_are_sent_as_a_png_with_their_new_length(addon, state, monkeypatch):
    state.set_mode("", "on", state.token)
    monkeypatch.setattr(MaskContext, "image_bytes", lambda self, data: b"REDACTED-PNG")
    flow = upload_flow(b"jpeg-bytes", "image/jpeg")
    addon.process_request(flow)
    assert flow.request.raw_content == b"REDACTED-PNG"
    assert flow.request.headers["content-type"] == "image/png"
    assert flow.request.headers["content-length"] == str(len(b"REDACTED-PNG"))


def test_masking_failed_has_a_fixed_code_text(addon, state, monkeypatch):
    def refuse(self, body, ctx, route=None):
        raise MaskingFailed("residual_leak")

    from client_gateway.adapters.openai_chat import OpenAIChatAdapter

    monkeypatch.setattr(OpenAIChatAdapter, "mask_request", refuse)
    flow = flow_of("POST", COPILOT_URL, copilot_turn("hi", marked_tool_result()), JSON_HEADERS)
    addon.process_request(flow)
    assert "residual_leak" in flow.response.get_text()


# ---------------------------------------------------------------------------------- websockets
def ws_flow(
    addon: FrontendAddon, url: str = "https://chatgpt.com/ces/v1/ws", masked_chat: bool = False
) -> Any:
    if masked_chat:
        addon.state.frontend_mode(CID, True, CHATGPT)  # the chat the URL names is a PBI chat
    flow = flow_of("GET", url, b"", {"upgrade": "websocket"})
    addon.process_request(flow)
    flow.websocket = tflow.twebsocket(messages=False)
    return flow


def send(flow: Any, content: bytes, from_client: bool = True) -> WebSocketMessage:
    message = WebSocketMessage(Opcode.TEXT, from_client, content)
    flow.websocket.messages.append(message)
    return message


def test_an_outgoing_websocket_frame_with_a_client_value_is_dropped(addon, state):
    flow = ws_flow(addon, f"https://chatgpt.com/backend-api/conversation/{CID}", masked_chat=True)
    assert flow.metadata[META_KEY].ws_masked
    leak = send(flow, json.dumps({"q": f"about {NAME}"}).encode())
    escaped = send(
        flow, ('{"q":"about ' + "".join(f"\\u{ord(c):04x}" for c in NAME) + '"}').encode()
    )
    clean = send(flow, b'{"q":"hello"}')
    incoming = send(flow, f"you said {NAME}".encode(), from_client=False)
    for message in (leak, escaped, clean, incoming):
        addon.websocket_message(_with_last(flow, message))
    assert leak.dropped and escaped.dropped
    assert not clean.dropped and not incoming.dropped  # incoming frames are never touched
    assert state.stats()["frontend_ws_dropped"] == 2


def _with_last(flow: Any, message: WebSocketMessage) -> Any:
    """mitmproxy fires the hook with the message just received as ``messages[-1]``."""
    flow.websocket.messages.remove(message)
    flow.websocket.messages.append(message)
    return flow


def test_websocket_frames_are_untouched_when_the_socket_names_no_masked_chat(addon, state):
    state.frontend_mode(CID, True, CHATGPT)  # a PBI chat of the same app exists, but the socket
    flow = ws_flow(addon)  # names no chat: R2, it is never touched
    assert not flow.metadata[META_KEY].ws_masked
    leak = send(flow, f"about {NAME}".encode())
    addon.websocket_message(flow)
    assert not leak.dropped and state.stats()["frontend_ws_dropped"] == 0


def test_the_websocket_of_a_masked_chat_url_is_checked(addon, state):
    state.frontend_mode(CID, True, CHATGPT)
    flow = flow_of(
        "GET", f"https://chatgpt.com/backend-api/conversation/{CID}", b"", {"upgrade": "websocket"}
    )
    addon.process_request(flow)
    assert flow.metadata[META_KEY].ws_masked
