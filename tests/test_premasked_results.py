"""An MCP result the masking authority already pseudonymised is not masked a second time (phase 3a.2,
Task E: the root cause of the Copilot display bug of the lab session).

The engine treats a string shaped like one of OUR pseudonyms in outgoing text as a real data code that
merely looks like one (a "token collision") and gives it a pseudonym of its own: right for text that
was never masked, wrong for a tool result the Power BI MCP already masked (it carries the PBI
marker). The model then saw a SECOND pseudonym that unmasks to the FIRST one (``CUS_61729``), not to
the client name: the rehydrator counted its tokens, the app still showed ``CUS_61729``, and the next
turn masked that visible pseudonym as yet another new value. Synthetic names only.
"""

from __future__ import annotations

import json
import re

import pytest

from client_gateway.adapters.base import MaskContext
from client_gateway.adapters.openai_chat import OpenAIChatAdapter
from client_gateway.adapters.openai_responses import OpenAIResponsesAdapter
from client_gateway.memo import RemaskMemo
from tests._wire_support import (
    MARKER,
    NAME,
    NAME2,
    all_names,
    body_bytes,
    load_fixture,
    make_state,
    relabel_request,
    strings_of,
    token_of,
)


@pytest.fixture()
def state(tmp_path):
    return make_state(tmp_path)


def result_text(state, *names: str) -> str:
    rows = [{"Customer": token_of(state, n), "Revenue": 100 + i} for i, n in enumerate(names)]
    return json.dumps({"_fv": MARKER, "success": True, "rows": rows})


# ---------------------------------------------------------------------------------- the engine
def test_by_default_a_pseudonym_shaped_string_in_outgoing_text_is_a_value_of_its_own(state):
    token = token_of(state, NAME)
    out, n = state.smap.mask_text(f"code {token}")
    assert n == 1 and token not in out and out != f"code {token}"
    unmasked = state.smap.unmask_text(out)[0]
    assert unmasked == f"code {token}"  # the second pseudonym unmasks to the FIRST pseudonym
    assert state.smap.stats.get("token_collisions", 0) == 1


def test_premasked_text_keeps_its_pseudonyms_and_still_masks_everything_else(state):
    token = token_of(state, NAME)
    before = dict(state.smap.stats)
    out, n = state.smap.mask_text(f"{token} next to {NAME2}", premasked=True)
    assert out.startswith(f"{token} next to ") and NAME2 not in out and n == 1
    assert state.smap.stats.get("token_collisions", 0) == before.get("token_collisions", 0)
    assert state.smap.unmask_text(out)[0] == f"{NAME} next to {NAME2}"  # both come back as names
    assert state.smap.mask_text(f"just {token}", premasked=True) == (f"just {token}", 0)


def test_a_premasked_string_is_still_checked_for_shapes_and_values(state):
    out, _ = state.smap.mask_text(f"{token_of(state, NAME)} mail a.b@example.org", premasked=True)
    assert "example.org" not in out


# ---------------------------------------------------------------------------------- the context
def test_a_string_with_the_marker_is_premasked_and_one_without_is_not(state):
    token = token_of(state, NAME)
    ctx = MaskContext(state, marker=MARKER)
    marked = ctx.text(f'{{"_fv":"{MARKER}","c":"{token}"}}')
    assert json.loads(marked) == {"_fv": "fvpbi", "c": token} and ctx.marker_seen
    plain = ctx.text(f'{{"c":"{token}"}}')  # no marker: text nobody masked before
    assert token not in plain
    assert MaskContext(state, marker=MARKER).text(f"{NAME} {MARKER}") == f"{token} fvpbi"


def test_the_flag_does_not_leak_to_the_next_string_and_the_cache_keeps_both_apart(state):
    token = token_of(state, NAME)
    ctx = MaskContext(state, marker=MARKER)
    same = f'{{"c":"{token}"}}'
    first = ctx.text(f'{{"_fv":"{MARKER}","c":"{token}"}}')
    second = ctx.text(same)
    third = ctx.text(f'{{"_fv":"{MARKER}","c":"{token}"}}')
    assert token in first and token not in second and first == third
    assert ctx._masker._premasked is False


def test_the_same_marked_result_is_masked_to_identical_bytes_every_turn(state):
    text = result_text(state, NAME, NAME2)
    outs = {MaskContext(state, RemaskMemo(), marker=MARKER).text(text) for _ in range(3)}
    assert len(outs) == 1


# ---------------------------------------------------------------------------------- the adapters
def chat_body(state) -> bytes:
    messages = [
        {"role": "user", "content": f"<userRequest>\nWhat about {NAME}?\n</userRequest>"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "t", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": result_text(state, NAME, NAME2)},
    ]  # fmt: skip
    return json.dumps({"model": "m", "messages": messages}).encode()


def responses_body(state) -> bytes:
    items = [
        {"role": "user", "content": [{"type": "input_text", "text": f"What about {NAME}?"}]},
        {"type": "function_call", "name": "t", "arguments": "{}", "call_id": "c1"},
        {
            "type": "function_call_output",
            "call_id": "c1",
            "output": result_text(state, NAME, NAME2),
        },
    ]
    return json.dumps({"model": "m", "input": items}).encode()


@pytest.mark.parametrize(
    ("adapter", "build", "key"),
    [
        (OpenAIChatAdapter(), chat_body, "messages"),
        (OpenAIResponsesAdapter(), responses_body, "input"),
    ],
)
def test_a_mcp_result_keeps_the_pseudonyms_the_authority_issued(state, adapter, build, key):
    ctx = MaskContext(state, marker=MARKER)
    out = json.loads(adapter.mask_request(build(state), ctx))[key]
    last = out[-1]
    text = last["content"] if "content" in last else last["output"]
    rows = json.loads(text)["rows"]
    assert [r["Customer"] for r in rows] == [token_of(state, NAME), token_of(state, NAME2)]
    assert NAME in json.dumps(out[0]) or token_of(state, NAME) in json.dumps(out[0])
    assert not any(n in s for s in strings_of(out) for n in all_names())


@pytest.mark.parametrize(
    ("adapter", "build"),
    [(OpenAIChatAdapter(), chat_body), (OpenAIResponsesAdapter(), responses_body)],
)
def test_every_pseudonym_the_model_sees_unmasks_to_a_client_name(state, adapter, build):
    """The invariant the lab session broke: whatever pseudonym leaves in the request, the model may
    echo it, and the rehydrator must turn it into the client name (never into another pseudonym)."""
    out = adapter.mask_request(build(state), MaskContext(state, marker=MARKER))
    seen = json.loads(out)
    back = state.smap.unmask_tree(seen)
    text = json.dumps(back, ensure_ascii=False)
    assert NAME in text and NAME2 in text
    assert not re.search(r"CUS_\d{5}", text), "a pseudonym unmasked to another pseudonym"


def test_the_recorded_lab_tool_result_is_not_masked_twice(state):
    for name, adapter in (
        ("copilot_responses_tool_result", OpenAIResponsesAdapter()),
        ("copilot_tool_result_image", OpenAIChatAdapter()),
    ):
        fx = load_fixture(name)
        body = relabel_request(body_bytes(fx), state)
        out = adapter.mask_request(body, MaskContext(state, marker=MARKER))
        back = json.dumps(state.smap.unmask_tree(json.loads(out)), ensure_ascii=False)
        assert not re.search(r"CUS_\d{5}", back), name
