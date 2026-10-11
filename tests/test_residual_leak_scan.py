"""Rule R9 at the front end (phase 3a.2, Task D): after the adapter masked a request, the WHOLE outgoing
body is scanned for known client values in every wire form, and a hit blocks the request. Also the
counters of an app that changed its format (``format_changed``) and of a reply nothing could be
rehydrated in (``rehydration_unrecognised``). Synthetic names only; no sockets, no processes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote, quote_plus

import pytest
from mitmproxy import http
from mitmproxy.test import tflow

from client_gateway.adapters.openai_chat import OpenAIChatAdapter
from client_gateway.frontend.addon import BLOCK_FORMAT_TEXT, FrontendAddon
from client_gateway.leakscan import json_strings, residual_hits
from tests._wire_support import MARKER, NAME, NAME2, all_names, token_of
from tests.test_frontend_addon import respond
from tests.test_frontend_copilot_identity import chat_body, make_state, tool_result
from tests.test_frontend_r2 import CHAT_URL, post


@pytest.fixture()
def state(tmp_path: Path):
    return make_state(tmp_path)


@pytest.fixture()
def addon(state):
    return FrontendAddon(state)


# ---------------------------------------------------------------------------------- the scan
def test_a_name_in_plain_text_json_escapes_and_url_encoding_is_found(state):
    smap = state.smap
    assert residual_hits(smap, f"about {NAME} today".encode()) >= 1
    assert residual_hits(smap, json.dumps({"k": f"about {NAME}"}).encode()) >= 1
    escaped = json.dumps({"k": f"about {NAME}"}, ensure_ascii=True).replace(" ", "\\u0020")
    assert NAME not in escaped and residual_hits(smap, escaped.encode()) >= 1
    assert residual_hits(smap, ("a=" + quote_plus(NAME) + "&b=1").encode()) >= 1
    assert residual_hits(smap, ("/p?q=" + quote(NAME)).encode()) >= 1
    assert residual_hits(smap, json.dumps({"url": "/p?q=" + quote(NAME)}).encode()) >= 1


def test_a_name_split_over_json_keys_and_nested_values_is_found(state):
    assert residual_hits(state.smap, json.dumps({NAME: 1}).encode()) >= 1
    assert residual_hits(state.smap, json.dumps({"a": [{"b": [f"x {NAME2}"]}]}).encode()) >= 1


def test_a_clean_body_and_the_pseudonyms_are_not_hits(state):
    assert residual_hits(state.smap, b"") == 0
    assert (
        residual_hits(state.smap, b'{"model":"m","messages":[{"role":"user","content":"hi"}]}') == 0
    )
    token = token_of(state, NAME)
    assert residual_hits(state.smap, f"about {token}".encode()) == 0
    assert residual_hits(state.smap, json.dumps({"k": f"about {token}"}).encode()) == 0
    assert residual_hits(state.smap, bytes(range(256)) * 40) == 0  # binary: nothing, no crash
    assert residual_hits(state.smap, b"[" * 100_000) == 0  # a hostile depth never raises


def test_the_scan_returns_a_count_never_a_value(state):
    hits = residual_hits(state.smap, f"{NAME} and {NAME2}".encode())
    assert isinstance(hits, int) and hits >= 2
    assert json_strings({"a": ["b", {"c": "d"}]}) == ["a", "b", "c", "d"]
    deep: Any = "x"
    for _ in range(200):
        deep = [deep]
    assert json_strings(deep) == []


# ---------------------------------------------------------------------------------- the front end
def body_with_tools(description: str, **top: Any) -> bytes:
    tree = json.loads(chat_body("a chat", tail=[tool_result()]))
    tree["tools"] = [{"type": "function", "function": {"name": "t", "description": description}}]
    tree.update(top)
    return json.dumps(tree, ensure_ascii=False).encode()


def test_a_name_the_adapter_forwards_verbatim_is_caught_by_the_whole_body_scan(addon, state):
    # ``tools`` is the developer's list: the adapter does not read it. The scan does.
    flow = post(CHAT_URL, body_with_tools(f"look up {NAME}"))
    addon.process_request(flow)
    assert flow.response.status_code == 400
    text = flow.response.get_text()
    assert "residual_leak" in text and "did not leave this machine" in text
    assert NAME not in text and MARKER not in text
    stats = state.stats()
    assert stats["residual_leak_blocked"] == 1 and stats["frontend_blocked"] == 1
    assert stats["frontend_masked"] == 0


def test_an_escaped_name_the_adapter_forwards_verbatim_is_caught_too(addon, state):
    escaped = json.dumps(json.loads(body_with_tools(f"look up {NAME}")), ensure_ascii=True)
    escaped = escaped.replace("Zorblax Holdings", "Zorblax\\u0020Holdings")
    flow = post(CHAT_URL, escaped.encode())
    addon.process_request(flow)
    assert flow.response.status_code == 400 and state.stats()["residual_leak_blocked"] == 1


def test_a_request_the_scan_passes_is_sent_masked_and_counted_as_masked(addon, state):
    flow = post(CHAT_URL, body_with_tools("look up a customer"))
    addon.process_request(flow)
    assert flow.response is None
    assert not any(n in flow.request.raw_content.decode() for n in all_names())
    assert state.stats()["frontend_masked"] == 1 and state.stats()["residual_leak_blocked"] == 0


def test_an_adapter_that_misses_a_field_cannot_send_the_name_out(addon, state, monkeypatch):
    def careless(self, body, ctx, route=None):  # an adapter of an old app version
        ctx.scan_marker(body)
        return ctx.strip_marker_bytes(body)

    monkeypatch.setattr(OpenAIChatAdapter, "mask_request", careless)
    flow = post(CHAT_URL, chat_body("a chat", f"about {NAME}", tail=[tool_result()]))
    addon.process_request(flow)
    assert flow.response.status_code == 400 and state.stats()["residual_leak_blocked"] == 1
    assert (
        flow.request.raw_content is not None
    )  # the app gets the fixed text, nothing goes upstream


def test_a_binary_upload_is_not_scanned_as_text(addon, state):
    state.set_mode("", "on", state.token)
    assert addon._scan(NAME.encode(), "image/png") is None  # images went through the redactor
    with pytest.raises(Exception):
        addon._scan(NAME.encode(), "application/json")


def test_the_unmask_mode_is_not_scanned(addon, state):
    # a human switched the chat off: real values go out, by design
    addon.process_request(post(CHAT_URL, chat_body("a chat", tail=[tool_result()])))
    addon.process_request(post(CHAT_URL, chat_body("a chat", "masking off")))
    token = token_of(state, NAME)
    flow = post(CHAT_URL, chat_body("a chat", "masking off", f"{token} please"))
    addon.process_request(flow)
    assert flow.response is None and NAME in flow.request.raw_content.decode()
    assert state.stats()["residual_leak_blocked"] == 0


# ---------------------------------------------------------------------------------- format drift
def test_a_masked_request_of_a_shape_the_adapter_does_not_know_is_blocked_as_format_changed(
    addon, state
):
    body = json.dumps({"model": "m", "newer_field": {"x": 1}, "_fv": MARKER}).encode()
    flow = post(CHAT_URL, body)
    addon.process_request(flow)
    assert flow.response.status_code == 400
    text = flow.response.get_text()
    assert BLOCK_FORMAT_TEXT in text and "unsupported_format" in text
    assert state.stats()["format_changed"] == 1 and state.stats()["frontend_blocked"] == 1


def test_a_request_of_an_unknown_shape_in_a_chat_that_is_not_masked_is_untouched(addon, state):
    body = json.dumps({"model": "m", "newer_field": {"x": NAME}}).encode()
    flow = post(CHAT_URL, body)
    addon.process_request(flow)
    assert flow.response is None and flow.request.raw_content == body
    assert state.stats()["format_changed"] == 0


# ---------------------------------------------------------------------------------- replies
def masked_flow(addon: FrontendAddon) -> Any:
    flow = post(CHAT_URL, chat_body("a chat", tail=[tool_result()]))
    addon.process_request(flow)
    respond(flow, 200, {"content-type": "text/event-stream"})
    addon.responseheaders(flow)
    assert callable(flow.response.stream)
    return flow


def test_a_reply_with_no_known_event_shape_is_counted_and_warned(addon, state):
    flow = masked_flow(addon)
    out = flow.response.stream(b'data: {"newer":{"delta":"x"}}\n\ndata: {"other":1}\n\n')
    out += flow.response.stream(b"")
    assert out.startswith(b'data: {"newer"')  # nothing is dropped: the masked bytes go through
    assert state.stats()["rehydration_unrecognised"] == 1
    assert "rehydration_unrecognised" in state.status()["warnings"]


def test_a_reply_of_a_known_shape_is_not_flagged(addon, state):
    flow = masked_flow(addon)
    frame = b'data: {"choices":[{"index":0,"delta":{"content":"hi"}}]}\n\n'
    flow.response.stream(frame)
    flow.response.stream(b"data: [DONE]\n\n")
    flow.response.stream(b"")
    assert state.stats()["rehydration_unrecognised"] == 0
    assert "rehydration_unrecognised" not in state.status()["warnings"]


def test_an_empty_reply_is_not_flagged(addon, state):
    flow = masked_flow(addon)
    flow.response.stream(b"")
    assert state.stats()["rehydration_unrecognised"] == 0


def test_the_http_helper_builds_what_the_tests_expect():
    req = http.Request.make("POST", CHAT_URL, b"", {})
    assert tflow.tflow(req=req).request.host.endswith("githubcopilot.com")


# ---------------------------------------------------------------------------------- JSON escapes
def test_a_known_pseudonym_after_a_json_newline_is_not_a_leak(state):
    """In raw JSON ``\nCUS_12345`` the ``n`` glues to the pseudonym; the matcher must not then read a
    pseudonym that once collided with a value as a client value."""
    token = token_of(state, NAME)
    state.smap.mask_text(f"code {token}")  # a collision entry for the pseudonym's own text exists
    body = json.dumps({"k": f"line one\n{token}\n{token} again"}).encode()
    assert b"\\n" + token.encode() in body  # in the raw JSON text: backslash, n, pseudonym
    assert residual_hits(state.smap, body) == 0


def test_a_name_after_a_json_newline_or_tab_is_still_found(state):
    for sep in ("\n", "\t", "\r\n"):
        body = json.dumps({"k": f"line one{sep}{NAME}"}).encode()
        assert residual_hits(state.smap, body) >= 1, repr(sep)


def test_numbers_and_other_scalars_of_a_json_body_are_read_without_error(state):
    state.smap.add_values("CUS", ["4711 Holding NV"], "test")
    assert residual_hits(state.smap, json.dumps({"id": 1, "n": "4711 Holding NV"}).encode()) >= 1
    assert residual_hits(state.smap, json.dumps({"id": 4711, "n": [1.5, True, None]}).encode()) == 0
