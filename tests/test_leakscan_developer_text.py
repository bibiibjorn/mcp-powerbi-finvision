"""The leak scan reads verbatim developer text with the developer-text rule (phase 3b.0, item 2).

``tools`` / ``functions`` / ``instructions`` (descriptions, parameter schemas, the app's own
instructions) are scanned with ``identity_hits(whole=False)``: a value that is only a whole-string
cell (a lone ordinary word) is not a hit there, exactly as the masker treats developer text. Content
fields keep the strict scan. Synthetic names only.
"""

from __future__ import annotations

import json

import pytest

from client_gateway.leakscan import DEVELOPER_KEYS, residual_hits
from tests._wire_support import NAME, make_state

LONE = "Total"  # an ordinary word the dictionary holds as a whole-cell value


@pytest.fixture()
def smap(tmp_path):
    state = make_state(tmp_path)
    state.smap.add_values("CUS", [LONE], "test")
    return state.smap


def hits(smap, tree) -> int:
    return residual_hits(smap, json.dumps(tree).encode())


def test_the_engine_has_a_developer_text_switch_on_identity_hits(smap):
    assert smap.identity_hits(LONE) == {"CUS": 1}  # strict: the whole cell is the name
    assert smap.identity_hits(LONE, whole=False) == {}  # developer text: a lone word is a word
    assert smap.identity_hits(NAME, whole=False) == {"CUS": 1}  # a real name is a name everywhere


def test_the_developer_fields_are_the_schema_and_instruction_fields():
    assert {"tools", "functions", "instructions"} <= DEVELOPER_KEYS


@pytest.mark.parametrize("field", sorted(DEVELOPER_KEYS))
def test_a_lone_ordinary_word_in_developer_text_is_not_a_hit(smap, field):
    assert hits(smap, {field: LONE}) == 0
    assert hits(smap, {field: [{"function": {"name": "t", "description": LONE}}]}) == 0
    assert hits(smap, {field: {"parameters": {"properties": {LONE: {"type": "string"}}}}}) == 0


@pytest.mark.parametrize("field", sorted(DEVELOPER_KEYS))
def test_a_client_name_in_developer_text_is_still_a_hit(smap, field):
    assert hits(smap, {field: f"look up {NAME}"}) >= 1
    assert hits(smap, {field: [{"function": {"name": "t", "description": NAME}}]}) >= 1
    assert hits(smap, {field: {"properties": {"customer": {"description": f"e.g. {NAME}"}}}}) >= 1


def test_an_escaped_name_in_developer_text_is_still_a_hit(smap):
    raw = '{"instructions":"look up Zorblax' + chr(92) + 'u0020Holdings NV"}'
    assert NAME not in raw and residual_hits(smap, raw.encode()) >= 1


def test_content_fields_keep_the_strict_scan(smap):
    assert hits(smap, [LONE]) == 1  # a whole-cell value as the whole content
    assert hits(smap, {"messages": [{"role": "user", "content": f"about {NAME}"}]}) >= 1
    assert hits(smap, {"tools": [], "messages": [{"content": f"about {NAME}"}]}) >= 1
    # developer text next to content: the content hit is found, the developer word is not one
    assert hits(smap, {"tools": [{"description": LONE}], "input": f"about {NAME}"}) >= 1
    assert hits(smap, {"tools": [{"description": LONE}], "input": "hello"}) == 0


def test_a_body_without_developer_fields_scans_as_before(smap):
    assert hits(smap, {"messages": [{"content": "hi"}]}) == 0
    assert hits(smap, {"a": [{"b": [f"x {NAME}"]}]}) >= 1
    assert residual_hits(smap, f"plain {NAME}".encode()) >= 1
