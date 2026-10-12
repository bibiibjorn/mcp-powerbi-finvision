"""A typed ``masking on`` / ``masking off`` is handled at the front end and never reaches the AI
(live lab, ChatGPT: the AI read the forwarded command as an instruction and answered ``[REDACTED]``):
the mode switches as before, and that user turn is rewritten to the neutral ``Reply with only: OK``
before the body is masked or forwarded. Covers ChatGPT (send + half-typed ``prepare`` draft) and both
Copilot APIs, in every mode a command can meet. Synthetic names only; no sockets, no processes.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import pytest

from client_gateway.adapters.control import NEUTRAL_TEXT
from client_gateway.adapters.streams import decode_bytes
from client_gateway.frontend.addon import FrontendAddon
from client_gateway.state import MODE_ON
from tests._wire_support import MARKER, NAME, load_fixture
from tests.test_frontend_addon import (
    CHATGPT,
    CID,
    CONVERSATION_URL,
    JSON_HEADERS,
    chatgpt_turn,
    flow_of,
    make_state,
)
from tests.test_frontend_copilot_identity import chat_body, tool_result
from tests.test_frontend_r2 import CHAT_URL, RESPONSES_URL, post, responses_body

PREPARE_URL = "https://chatgpt.com/backend-api/f/conversation/prepare"
COMMAND = re.compile(r"masking +(on|off)", re.IGNORECASE)


@pytest.fixture()
def state(tmp_path):
    return make_state(tmp_path)


@pytest.fixture()
def addon(state):
    return FrontendAddon(state)


def sent(flow: Any) -> Any:
    """The JSON body the app would send upstream (decoded)."""
    enc = flow.request.headers.get("content-encoding")
    return json.loads(decode_bytes(flow.request.raw_content, enc))


def wire_text(flow: Any) -> str:
    return decode_bytes(
        flow.request.raw_content, flow.request.headers.get("content-encoding")
    ).decode("utf-8")


def newest_chatgpt(body: Any) -> list[Any]:
    return [m for m in body["messages"] if m["author"]["role"] == "user"][-1]["content"]["parts"]


def sha(data: bytes | None) -> str:
    return hashlib.sha256(data or b"").hexdigest()


# ---------------------------------------------------------------------------------- ChatGPT
@pytest.mark.parametrize("command", ["masking on", "/Masking ON", "  masking on\n"])
def test_a_typed_masking_on_switches_the_chat_and_the_ai_gets_a_neutral_request(
    addon, state, command
):
    original = chatgpt_turn(command, CID)
    flow = flow_of("POST", CONVERSATION_URL, original, JSON_HEADERS)
    addon.process_request(flow)
    assert state.frontend_chat_masked(CHATGPT, CID)  # the switch still happens
    assert flow.metadata["fv_frontend"].mode == MODE_ON
    body = sent(flow)
    assert newest_chatgpt(body) == [NEUTRAL_TEXT] and not COMMAND.search(wire_text(flow))
    # nothing else of the body changed, and the length header follows the new body
    expected = json.loads(original)
    [m for m in expected["messages"] if m["author"]["role"] == "user"][-1]["content"]["parts"] = [
        NEUTRAL_TEXT
    ]
    assert body == expected
    assert flow.request.headers["content-length"] == str(len(flow.request.raw_content))


@pytest.mark.parametrize("encoding", [None, "gzip", "br"])
def test_a_typed_masking_off_in_a_chat_that_is_not_masked_is_rewritten_in_its_encoding(
    addon, state, encoding
):
    flow = flow_of(
        "POST", CONVERSATION_URL, chatgpt_turn("masking off", CID), JSON_HEADERS, encoding
    )
    addon.process_request(flow)
    assert flow.request.headers.get("content-encoding") == encoding
    assert newest_chatgpt(sent(flow)) == [NEUTRAL_TEXT] and not COMMAND.search(wire_text(flow))
    assert not state.frontend_chat_masked(CHATGPT, CID)
    assert state.frontend_mode(CID, False, CHATGPT) == "off"  # the switch is plain off


def test_a_typed_masking_off_in_a_pbi_chat_unmasks_and_hides_the_command(addon, state):
    marked = flow_of("POST", CONVERSATION_URL, chatgpt_turn("hi", CID, marker=True), JSON_HEADERS)
    addon.process_request(marked)
    assert state.frontend_mode(CID, False, CHATGPT) == MODE_ON
    off = flow_of("POST", CONVERSATION_URL, chatgpt_turn("masking off", CID), JSON_HEADERS)
    addon.process_request(off)
    assert state.frontend_mode(CID, False, CHATGPT) == "unmask"
    assert newest_chatgpt(sent(off)) == [NEUTRAL_TEXT] and not COMMAND.search(wire_text(off))
    assert state.stats()["pbi_unmasked_requests"] == 1


def test_a_first_turn_command_is_rewritten_too(addon, state):
    flow = flow_of("POST", CONVERSATION_URL, chatgpt_turn("masking on"), JSON_HEADERS)
    addon.process_request(flow)
    assert flow.metadata["fv_frontend"].mode == MODE_ON  # bound to the id the response reports
    assert newest_chatgpt(sent(flow)) == [NEUTRAL_TEXT] and not COMMAND.search(wire_text(flow))


@pytest.mark.parametrize(
    "text", ["please type masking off now", "masking on please", "masking off\nthanks"]
)
def test_a_phrase_inside_a_sentence_is_forwarded_untouched(addon, state, text):
    body = chatgpt_turn(text, CID)
    flow = flow_of("POST", CONVERSATION_URL, body, JSON_HEADERS)
    addon.process_request(flow)
    assert flow.request.raw_content == body and sha(flow.request.raw_content) == sha(body)
    assert not state.frontend_chat_masked(CHATGPT, CID)


def test_an_exact_command_draft_is_rewritten_without_switching_anything(addon, state):
    fx = load_fixture("chatgpt_prepare_followup")["request"]["body"]
    cid = fx["conversation_id"]
    for draft in ("masking on", "  Masking OFF "):
        fx["partial_query"]["content"]["parts"] = [draft]
        flow = flow_of("POST", PREPARE_URL, json.dumps(fx).encode(), JSON_HEADERS)
        addon.process_request(flow)
        body = sent(flow)
        assert body["partial_query"]["content"]["parts"] == [NEUTRAL_TEXT]
        assert not COMMAND.search(wire_text(flow))
        fx["partial_query"]["content"]["parts"] = [draft]
        body["partial_query"]["content"]["parts"] = [draft]
        assert body == fx  # nothing else of the draft request changed
        assert not state.frontend_chat_masked(CHATGPT, cid)  # a draft never switches a chat
        assert state.frontend_mode(cid, False, CHATGPT) == "passthrough"


def test_a_draft_that_is_only_part_of_a_command_or_a_sentence_is_untouched(addon, state):
    fx = load_fixture("chatgpt_prepare_followup")["request"]["body"]
    for draft in ("masking", "masking o", "masking on please", "say masking off"):
        fx["partial_query"]["content"]["parts"] = [draft]
        body = json.dumps(fx).encode()
        flow = flow_of("POST", PREPARE_URL, body, JSON_HEADERS)
        addon.process_request(flow)
        assert flow.request.raw_content == body


def test_a_command_draft_in_a_masked_chat_is_rewritten_and_then_masked(addon, state):
    fx = load_fixture("chatgpt_prepare_followup")["request"]["body"]
    state.frontend_set_mode(CHATGPT, fx["conversation_id"], "on")
    fx["partial_query"]["content"]["parts"] = ["masking off"]
    flow = flow_of("POST", PREPARE_URL, json.dumps(fx).encode(), JSON_HEADERS)
    addon.process_request(flow)
    assert flow.metadata["fv_frontend"].mode == MODE_ON
    assert sent(flow)["partial_query"]["content"]["parts"] == [NEUTRAL_TEXT]
    assert state.frontend_chat_masked(CHATGPT, fx["conversation_id"])  # a draft switched nothing


# ---------------------------------------------------------------------------------- Copilot
def test_a_typed_copilot_masking_on_switches_the_chat_and_is_replaced(addon, state):
    flow = post(CHAT_URL, chat_body("open the chat", "masking on"))
    addon.process_request(flow)
    body = sent(flow)
    last = body["messages"][-1]["content"]
    assert "<userRequest>\n" + NEUTRAL_TEXT + "\n</userRequest>" in last
    assert last.startswith("<context>") and "<reminderInstructions>" in last  # wrapper kept
    assert not COMMAND.search(last)
    assert flow.metadata["fv_frontend"].mode == MODE_ON
    follow = post(CHAT_URL, chat_body("open the chat", f"about {NAME}"))
    addon.process_request(follow)
    assert NAME not in follow.request.raw_content.decode()  # the chat is masked from now on


def test_a_typed_copilot_masking_off_in_a_pbi_chat_unmasks_and_hides_the_command(addon, state):
    flow = post(CHAT_URL, chat_body("open the chat", "masking off", tail=[tool_result()]))
    addon.process_request(flow)
    text = flow.request.raw_content.decode()
    last_user = [m for m in sent(flow)["messages"] if m["role"] == "user"][-1]["content"]
    assert NEUTRAL_TEXT in last_user and not COMMAND.search(last_user)
    assert NAME in text and MARKER not in text  # unmasked, as before
    assert state.stats()["pbi_unmasked_requests"] == 1


def test_a_typed_copilot_command_outside_a_pbi_chat_is_rewritten_and_the_rest_untouched(
    addon, state
):
    body = chat_body("open the chat", "masking off")
    flow = post(CHAT_URL, body)
    addon.process_request(flow)
    new = sent(flow)
    old = json.loads(body)
    assert new["messages"][:-1] == old["messages"][:-1]
    assert NEUTRAL_TEXT in new["messages"][-1]["content"]
    assert not COMMAND.search(new["messages"][-1]["content"])
    assert flow.request.headers["content-length"] == str(len(flow.request.raw_content))
    plain = post(CHAT_URL, chat_body("open the chat", "please say masking off"))
    plain_body = plain.request.raw_content
    addon.process_request(plain)
    assert plain.request.raw_content == plain_body  # a phrase inside a sentence: untouched


def test_a_typed_responses_command_is_replaced_in_the_newest_user_item(addon, state):
    flow = post(RESPONSES_URL, responses_body("open the chat", "masking on"))
    addon.process_request(flow)
    items = sent(flow)["input"]
    text = items[-1]["content"][0]["text"]
    assert "<userRequest>\n" + NEUTRAL_TEXT + "\n</userRequest>" in text
    assert not COMMAND.search(text)
    assert flow.metadata["fv_frontend"].mode == MODE_ON
    off = post(RESPONSES_URL, responses_body("open the chat", "masking off"))
    addon.process_request(off)
    assert NEUTRAL_TEXT in sent(off)["input"][-1]["content"][0]["text"]
    assert not COMMAND.search(wire_text(off))


# ---------------------------------------------------------------------------------- fail closed
def test_a_command_that_cannot_be_replaced_is_not_forwarded(addon, state, monkeypatch):
    from client_gateway.adapters import chatgpt_web

    monkeypatch.setattr(
        chatgpt_web.ChatGPTWebAdapter,
        "replace_newest_user_text",
        lambda self, body, route, new_text: body,
    )
    flow = flow_of("POST", CONVERSATION_URL, chatgpt_turn("masking on", CID), JSON_HEADERS)
    addon.process_request(flow)
    assert flow.response is not None and flow.response.status_code == 400
    assert not state.frontend_chat_masked(CHATGPT, CID)  # nothing was switched either


# ---------------------------------------------------------------------------------- Copilot history
def user_turns(body: Any) -> list[str]:
    return [m["content"] for m in body["messages"] if m["role"] == "user"]


def test_a_three_turn_history_with_a_command_in_turn_one_leaves_no_command_on_the_wire(
    addon, state
):
    first = post(CHAT_URL, chat_body("masking on"))
    addon.process_request(first)
    assert first.metadata["fv_frontend"].mode == MODE_ON
    original = chat_body("masking on", "second question", f"third about {NAME}")
    flow = post(CHAT_URL, original)
    addon.process_request(flow)
    body = sent(flow)
    assert not COMMAND.search(json.dumps(body))
    turns = user_turns(body)
    assert NEUTRAL_TEXT in turns[1] and "second question" in turns[2]  # [0] is the environment
    assert NAME not in flow.request.raw_content.decode()  # and the chat stays masked
    old = json.loads(original)
    assert [m for m in body["messages"] if m["role"] != "user"] == [
        m for m in old["messages"] if m["role"] != "user"
    ]


def test_the_chat_key_is_the_same_on_every_turn_of_a_chat_that_opened_with_a_command(addon, state):
    from client_gateway.adapters.copilot import chat_key

    bodies = [
        chat_body("masking on"),
        chat_body("masking on", "second question"),
        chat_body("masking on", "second question", "third question"),
    ]
    keys = []
    for body in bodies:
        flow = post(CHAT_URL, body)
        addon.process_request(flow)
        keys.append(flow.metadata["fv_frontend"].key)
        assert keys[-1] == chat_key(body)  # read from the ORIGINAL body, before any rewrite
        assert flow.metadata["fv_frontend"].mode == MODE_ON
    assert keys[0] == keys[1] == keys[2] and keys[0] is not None
    assert state.frontend_chat_masked("copilot", keys[0])


def test_an_older_command_only_rewrites_and_never_switches(addon, state):
    on = post(CHAT_URL, chat_body("open the chat", "masking on"))
    addon.process_request(on)
    key = on.metadata["fv_frontend"].key
    # a later request whose history holds an older ``masking off`` (the app resent it): no switch
    flow = post(CHAT_URL, chat_body("open the chat", "masking on", "masking off", "and now?"))
    addon.process_request(flow)
    assert state.frontend_chat_masked("copilot", key)
    assert flow.metadata["fv_frontend"].mode == MODE_ON
    assert not COMMAND.search(json.dumps(sent(flow)))


def test_an_older_command_is_rewritten_in_a_chat_that_is_not_masked_too(addon, state):
    body = chat_body("open the chat", "masking off", "plain follow up")
    flow = post(CHAT_URL, body)
    addon.process_request(flow)
    new = sent(flow)
    assert not COMMAND.search(json.dumps(new))
    assert "plain follow up" in user_turns(new)[-1]
    sentence = chat_body("open the chat", "please say masking off sometime", "next")
    again = post(CHAT_URL, sentence)
    addon.process_request(again)
    assert again.request.raw_content == sentence  # only an exact command is rewritten


def test_a_responses_history_is_rewritten_too(addon, state):
    first = post(RESPONSES_URL, responses_body("masking on"))
    addon.process_request(first)
    flow = post(RESPONSES_URL, responses_body("masking on", "second", "third"))
    addon.process_request(flow)
    assert not COMMAND.search(wire_text(flow))
    assert first.metadata["fv_frontend"].key == flow.metadata["fv_frontend"].key
    assert flow.metadata["fv_frontend"].mode == MODE_ON
