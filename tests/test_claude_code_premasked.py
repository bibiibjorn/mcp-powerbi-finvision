"""The Claude Code path does not mask an MCP result twice (phase 3b.0, item 1).

A tool result that carries the install's PBI marker was pseudonymised by the masking authority
already (only when ``client_gateway.mcp_masking`` is on). ``masking.mask_request`` treats such a
string like the wire adapters do: the pseudonyms in it stay, everything else is still masked.
Synthetic names only.
"""

from __future__ import annotations

import json

import pytest

from client_gateway.masking import mask_request
from client_gateway.memo import RemaskMemo
from tests._wire_support import NAME, NAME2, make_state, token_of


@pytest.fixture()
def state(tmp_path):
    return make_state(tmp_path)


def body(*results: str) -> bytes:
    blocks = [
        {"type": "tool_result", "tool_use_id": f"t{i}", "content": [{"type": "text", "text": r}]}
        for i, r in enumerate(results)
    ]
    messages = [{"role": "user", "content": [{"type": "text", "text": "hello"}] + blocks}]
    return json.dumps({"model": "m", "max_tokens": 5, "messages": messages}).encode()


def result_texts(out: bytes) -> list[str]:
    msg = json.loads(out)["messages"][0]["content"]
    return [b["content"][0]["text"] for b in msg if b.get("type") == "tool_result"]


def test_a_marked_result_keeps_the_pseudonyms_the_authority_issued(state):
    assert state.marker
    token = token_of(state, NAME)
    marked = json.dumps({"_fv": state.marker, "rows": [{"Customer": token}]})
    out = mask_request(body(marked), "application/json", state, RemaskMemo())
    (text,) = result_texts(out)
    assert json.loads(text)["rows"] == [{"Customer": token}]
    assert state.smap.stats.get("token_collisions", 0) == 0
    assert state.smap.unmask_text(text)[0].count(NAME) == 1  # the name, not another pseudonym


def test_an_unmarked_pseudonym_shaped_string_is_still_a_value_of_its_own(state):
    token = token_of(state, NAME)
    plain = json.dumps({"rows": [{"Customer": token}]})  # no marker: text nobody masked
    out = mask_request(body(plain), "application/json", state, RemaskMemo())
    (text,) = result_texts(out)
    assert token not in text
    assert state.smap.stats.get("token_collisions", 0) == 1


def test_a_marked_result_still_masks_every_real_value_in_it(state):
    token = token_of(state, NAME)
    marked = json.dumps({"_fv": state.marker, "a": token, "b": f"leftover {NAME2}"})
    out = mask_request(body(marked), "application/json", state, RemaskMemo())
    (text,) = result_texts(out)
    assert NAME2 not in text and token in text


def test_the_flag_does_not_leak_to_the_next_string_of_the_request(state):
    token = token_of(state, NAME)
    marked = json.dumps({"_fv": state.marker, "c": token})
    plain = json.dumps({"c": token})
    first, second = result_texts(
        mask_request(body(marked, plain), "application/json", state, RemaskMemo())
    )
    assert token in first and token not in second


def test_without_a_marker_in_the_state_nothing_is_premasked(state):
    state.marker = ""
    token = token_of(state, NAME)
    out = mask_request(
        body(json.dumps({"_fv": "fvpbi-x", "c": token})), "application/json", state, RemaskMemo()
    )
    (text,) = result_texts(out)
    assert token not in text
