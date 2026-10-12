"""Per-chat identity of Copilot (phase 3a.2, Task B): Copilot sends no chat id, so a chat is its first
user turn, and the side calls of a turn follow their chat through ``x-interaction-id``.

Covers ``adapters.copilot`` (the chat key), the turn map of ``GatewayState`` and the way the front
end uses both (sticky PBI flag, typed switches per chat, side calls, restart). Synthetic names only;
no sockets, no processes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mitmproxy import http
from mitmproxy.test import tflow

from client_gateway.adapters.base import Route
from client_gateway.adapters.copilot import chat_key, chat_key_of, human_text
from client_gateway.adapters.openai_chat import OpenAIChatAdapter
from client_gateway.frontend.addon import FrontendAddon
from client_gateway.state import TURN_TTL_S, GatewayState
from tests._wire_support import (
    MARKER,
    NAME,
    all_names,
    body_bytes,
    load_fixture,
    synthetic_clients,
)

COPILOT_URL = "https://api.individual.githubcopilot.com/chat/completions"
JSON_HEADERS = {"content-type": "application/json"}
ENV = {
    "role": "user",
    "content": "<environment_info>\nThe user's OS is: Windows\n</environment_info>",
}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def clock():
    return Clock()


@pytest.fixture()
def state(tmp_path, clock):
    return make_state(tmp_path, clock)


@pytest.fixture()
def addon(state):
    return FrontendAddon(state)


def make_state(home: Path, clock: Any = None) -> GatewayState:
    (home / "install.id").write_text("0123456789abcdef\n", encoding="ascii")
    state = GatewayState(
        home=home, default_mode="off", images_policy="withhold", ready_wait_s=0.05, clock=clock
    ) if clock else GatewayState(
        home=home, default_mode="off", images_policy="withhold", ready_wait_s=0.05
    )  # fmt: skip
    for prefix, values in synthetic_clients().items():
        state.smap.add_values(prefix, values, "test")
    assert state.marker == MARKER
    return state


def wrapped(text: str) -> dict[str, Any]:
    """A VS Code user message: context and reminder wrappers around the typed text."""
    return {
        "role": "user",
        "content": (
            "<context>\nThe current date is October 7, 2026.\n</context>\n"
            "<reminderInstructions>\nstatic advice\n</reminderInstructions>\n"
            f"<userRequest>\n{text}\n</userRequest>"
        ),
    }


def tool_result(marked: bool = True) -> dict[str, Any]:
    payload = {"rows": [{"c": NAME}]}
    if marked:
        payload["_fv"] = MARKER
    return {"role": "tool", "tool_call_id": "c1", "content": json.dumps(payload)}


def chat_body(*turns: str, tail: list[dict[str, Any]] | None = None) -> bytes:
    """A Copilot Chat Completions body of a chat whose typed turns are ``turns`` (the first one
    names the chat); ``tail`` is appended after the last turn (a tool round)."""
    messages: list[dict[str, Any]] = [{"role": "system", "content": "sys"}, ENV]
    for i, turn in enumerate(turns):
        messages.append(wrapped(turn))
        if i < len(turns) - 1:
            messages.append({"role": "assistant", "content": f"answer {i}"})
    messages += tail or []
    return json.dumps({"model": "m", "messages": messages, "stream": True}).encode()


def side_body(prompt: str) -> bytes:
    """A side call (title / summary): the app's own prompt, no ``<userRequest>`` block."""
    messages = [
        {"role": "system", "content": "You write pithy titles."},
        {"role": "user", "content": f"Please write a brief title for: {prompt}"},
    ]
    return json.dumps({"model": "gpt-4o-mini", "messages": messages, "stream": True}).encode()


def flow(body: bytes, turn: str | None = None, url: str = COPILOT_URL) -> Any:
    headers = dict(JSON_HEADERS)
    if turn:
        headers["x-interaction-id"] = turn
    req = http.Request.make("POST", url, b"", headers)
    req.raw_content = body
    req.headers["content-length"] = str(len(body))
    return tflow.tflow(req=req)


def untouched(f: Any, body: bytes) -> bool:
    return f.response is None and f.request.raw_content == body


def masked(f: Any) -> bool:
    return f.response is None and not any(n in f.request.raw_content.decode() for n in all_names())


# ---------------------------------------------------------------------------------- the key
def test_the_key_is_the_hash_of_the_first_typed_turn_and_never_the_text():
    body = chat_body("Tell me about the customers", "and the vendors?")
    key = chat_key(body)
    assert key == chat_key_of("Tell me about the customers")
    assert key is not None and key.startswith("c") and len(key) == 17
    assert "customers" not in key
    # later turns, tool rounds and the model's answers never change it
    assert chat_key(chat_body("Tell me about the customers")) == key
    assert chat_key(chat_body("Tell me about the customers", tail=[tool_result()])) == key
    assert chat_key(chat_body("Another question")) != key


def test_the_wrappers_and_whitespace_do_not_change_the_key():
    a = chat_body("Tell   me\nabout it")
    b = chat_body(" Tell me about it ")
    assert chat_key(a) == chat_key(b)
    ctx_only = {"role": "user", "content": "<context>\nx\n</context>"}
    messages = [ENV, ctx_only, wrapped("first question")]
    assert chat_key(json.dumps({"messages": messages}).encode()) == chat_key_of("first question")


def test_a_body_without_a_wrapped_turn_has_no_key():
    assert chat_key(side_body("anything")) is None
    assert chat_key(b"{}") is None and chat_key(b"[]") is None and chat_key(b"not json") is None
    assert (
        chat_key(
            json.dumps(
                {"messages": [{"role": "user", "content": "<userRequest>\n\n</userRequest>"}]}
            ).encode()
        )
        is None
    )
    assert chat_key(json.dumps({"messages": "x"}).encode()) is None
    assert (
        chat_key(
            json.dumps(
                {"messages": [{"role": "assistant", "content": wrapped("x")["content"]}]}
            ).encode()
        )
        is None
    )


def test_the_fixture_requests_have_the_key_of_their_first_turn():
    fx = load_fixture("copilot_tool_result_image")
    key = chat_key(body_bytes(fx))
    assert key is not None
    assert human_text(wrapped("masking off")["content"]) == "masking off"


def test_the_adapter_exposes_the_key_and_shares_its_space_with_the_responses_chats():
    adapter = OpenAIChatAdapter()
    assert adapter.chat_key(chat_body("hello")) == chat_key_of("hello")
    assert adapter.url_chat_key("/chat/completions") is None
    assert adapter.chat_space == "copilot" and adapter.uses_turn_ids is True
    assert Route("POST", "h", "/p").turn == ""


# ---------------------------------------------------------------------------------- the front end
def test_a_marked_chat_stays_masked_after_its_marker_was_compacted_away(addon, state):
    first = flow(chat_body("open the customer chat", tail=[tool_result()]))
    addon.process_request(first)
    assert masked(first) and MARKER not in first.request.raw_content.decode()
    later_body = chat_body("open the customer chat", f"and what about {NAME}?")
    later = flow(later_body)  # same first turn, no marker in the history any more
    addon.process_request(later)
    assert NAME not in later.request.raw_content.decode()  # sticky PBI flag of THIS chat


def test_another_chat_is_untouched_even_right_after_a_chat_became_a_pbi_chat(addon, state):
    addon.process_request(flow(chat_body("open the customer chat", tail=[tool_result()])))
    body = chat_body("a brand new chat", f"mentions {NAME}")
    other = flow(body)
    addon.process_request(other)
    assert untouched(other, body)
    stats = state.stats()
    assert stats["frontend_blocked"] == 0 and stats["frontend_masked"] == 1


def test_the_pbi_flag_of_a_copilot_chat_survives_a_restart(tmp_path, clock):
    first_state = make_state(tmp_path, clock)
    FrontendAddon(first_state).process_request(flow(chat_body("the chat", tail=[tool_result()])))
    again = FrontendAddon(make_state(tmp_path, clock))
    body = chat_body("the chat", f"about {NAME}")
    f = flow(body)
    again.process_request(f)
    assert NAME not in f.request.raw_content.decode()


def test_a_typed_off_in_one_copilot_chat_does_not_touch_another(addon, state):
    addon.process_request(flow(chat_body("chat a", tail=[tool_result()])))
    addon.process_request(flow(chat_body("chat b", tail=[tool_result()])))
    off = flow(chat_body("chat a", "masking off"))
    addon.process_request(off)
    a_next = flow(chat_body("chat a", "masking off", f"{NAME} again"))
    addon.process_request(a_next)
    assert NAME in a_next.request.raw_content.decode()  # chat a was switched off by the human
    b_next = flow(chat_body("chat b", f"about {NAME}"))
    addon.process_request(b_next)
    assert NAME not in b_next.request.raw_content.decode()  # chat b is still masked


def test_a_typed_on_masks_one_copilot_chat_only(addon, state):
    on = flow(chat_body("some chat", "masking on"))
    addon.process_request(on)
    follow = flow(chat_body("some chat", "masking on", f"about {NAME}"))
    addon.process_request(follow)
    assert NAME not in follow.request.raw_content.decode()
    body = chat_body("some other chat", f"about {NAME}")
    other = flow(body)
    addon.process_request(other)
    assert untouched(other, body)


# ---------------------------------------------------------------------------------- side calls
def test_a_side_call_of_a_pbi_turn_follows_its_chat_through_the_turn_id(addon, state):
    main = flow(chat_body("open the chat", tail=[tool_result()]), turn="turn-1")
    addon.process_request(main)
    side = flow(side_body(f"what we learned about {NAME}"), turn="turn-1")
    addon.process_request(side)
    assert masked(side)
    assert state.stats()["frontend_turn_mapped"] == 1


def test_a_side_call_with_another_turn_id_is_untouched(addon, state):
    addon.process_request(flow(chat_body("open the chat", tail=[tool_result()]), turn="turn-1"))
    body = side_body(f"about {NAME}")
    other = flow(body, turn="turn-2")
    addon.process_request(other)
    assert untouched(other, body)
    no_id = flow(body)
    addon.process_request(no_id)
    assert untouched(no_id, body)


def test_a_side_call_that_comes_before_its_main_call_is_untouched_the_documented_residual(
    addon, state
):
    # the intent / title call of a turn is sent first: nothing maps its turn id yet. It holds only
    # the text typed in THAT turn, in a chat that has not used Power BI yet.
    body = side_body(f"about {NAME}")
    early = flow(body, turn="turn-9")
    addon.process_request(early)
    assert untouched(early, body)
    addon.process_request(flow(chat_body("a chat", tail=[tool_result()]), turn="turn-9"))
    later = flow(side_body(f"about {NAME}"), turn="turn-9")  # now the turn is mapped
    addon.process_request(later)
    assert masked(later)


def test_a_side_call_follows_a_chat_that_was_switched_off(addon, state):
    addon.process_request(flow(chat_body("the chat", tail=[tool_result()])))
    addon.process_request(flow(chat_body("the chat", "masking off"), turn="turn-3"))
    body = side_body(f"about {NAME}")
    side = flow(body, turn="turn-3")
    addon.process_request(side)
    assert NAME in side.request.raw_content.decode()  # off: the human's choice, nothing masked


def test_a_turn_id_stops_mapping_after_its_ttl(tmp_path, clock):
    state = make_state(tmp_path, clock)
    addon = FrontendAddon(state)
    addon.process_request(flow(chat_body("the chat", tail=[tool_result()]), turn="turn-1"))
    clock.now += TURN_TTL_S + 1
    body = side_body(f"about {NAME}")
    f = flow(body, turn="turn-1")
    addon.process_request(f)
    assert untouched(f, body)


def test_the_main_call_of_every_turn_refreshes_the_map(addon, state):
    addon.process_request(flow(chat_body("the chat", tail=[tool_result()]), turn="turn-1"))
    addon.process_request(flow(chat_body("the chat", "second question"), turn="turn-2"))
    side = flow(side_body(f"about {NAME}"), turn="turn-2")
    addon.process_request(side)
    assert masked(side)


def test_a_marked_side_call_with_no_known_chat_is_masked_by_its_own_marker(addon, state):
    body = json.dumps({
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Summarize " + json.dumps(
            {"_fv": MARKER, "rows": [NAME]})}],
    }).encode()  # fmt: skip
    f = flow(body, turn="turn-7")
    addon.process_request(f)
    assert masked(f) and MARKER not in f.request.raw_content.decode()
    assert state.status()["sessions"]["count"] == 0  # no chat was invented for it


# ---------------------------------------------------------------------------------- limits
def test_known_limit_a_compacted_chat_whose_first_turn_is_gone_is_a_new_chat(addon, state):
    """Copilot may replace the first user turn by a summary: the key changes. The chat is then
    masked only while a marked tool result is still in its history (documented limit)."""
    addon.process_request(flow(chat_body("the long chat", tail=[tool_result()])))
    turns = [ENV, wrapped("summary of the earlier chat"), wrapped(f"and now {NAME}")]
    summary = json.dumps({"model": "m", "messages": turns}).encode()
    f = flow(summary)
    addon.process_request(f)
    assert untouched(f, summary)
    marked = flow(json.dumps({"model": "m", "messages": [*turns, tool_result()]}).encode())
    addon.process_request(marked)
    assert masked(marked)  # a marked history is still masked, under the summary's key


def test_known_limit_two_chats_that_open_with_the_same_text_share_a_key(addon, state):
    addon.process_request(flow(chat_body("hi", tail=[tool_result()])))
    twin = flow(chat_body("hi", f"about {NAME}"))
    addon.process_request(twin)
    assert NAME not in twin.request.raw_content.decode()  # the same first turn: the same chat
