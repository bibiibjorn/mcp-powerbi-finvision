"""Rule R2 at the front end (phase 3a.2, Task A): a chat that never used Power BI is passed through
byte-for-byte, never masked and never blocked, whatever another chat of the same app did a moment
ago, and an internal error in a flow that is not masked forwards it untouched.

Synthetic names only; no sockets, no processes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from mitmproxy import http
from mitmproxy.test import tflow

from client_gateway.frontend.addon import BLOCK_UNKNOWN_TEXT, META_KEY, FrontendAddon
from client_gateway.log import GatewayLog
from client_gateway.state import GatewayState
from tests._wire_support import MARKER, NAME, synthetic_clients
from tests.test_frontend_copilot_identity import ENV, chat_body, side_body, tool_result, wrapped

COPILOT = "https://api.individual.githubcopilot.com"
CHAT_URL = f"{COPILOT}/chat/completions"
RESPONSES_URL = f"{COPILOT}/responses"
JSON_HEADERS = {"content-type": "application/json"}
CID = "00000000-0000-4000-8000-000000000004"
OTHER_CID = "00000000-0000-4000-8000-000000000099"


@pytest.fixture()
def state(tmp_path):
    return make_state(tmp_path)


@pytest.fixture()
def addon(state):
    return FrontendAddon(state)


def make_state(home: Path) -> GatewayState:
    (home / "install.id").write_text("0123456789abcdef\n", encoding="ascii")
    state = GatewayState(home=home, default_mode="off", images_policy="withhold", ready_wait_s=0.05)
    for prefix, values in synthetic_clients().items():
        state.smap.add_values(prefix, values, "test")
    return state


def post(url: str, body: bytes, turn: str | None = None, method: str = "POST") -> Any:
    headers = dict(JSON_HEADERS)
    if turn:
        headers["x-interaction-id"] = turn
    req = http.Request.make(method, url, b"", headers)
    req.raw_content = body
    req.headers["content-length"] = str(len(body))
    return tflow.tflow(req=req)


def fingerprint(flow: Any) -> tuple[str, list[tuple[str, str]]]:
    return (
        hashlib.sha256(flow.request.raw_content or b"").hexdigest(),
        list(flow.request.headers.items(True)),
    )


def responses_body(*turns: str, tail: list[dict[str, Any]] | None = None) -> bytes:
    """A Copilot ``/responses`` body (``input`` items, a system item first)."""

    def item(role: str, text: str, kind: str = "input_text") -> dict[str, Any]:
        return {"role": role, "content": [{"type": kind, "text": text}]}

    items: list[dict[str, Any]] = [item("system", "sys"), item("user", ENV["content"])]
    for i, turn in enumerate(turns):
        items.append(item("user", wrapped(turn)["content"]))
        if i < len(turns) - 1:
            items.append(item("assistant", f"answer {i}", "output_text"))
    items += tail or []
    return json.dumps({"model": "gpt-5-mini", "input": items, "stream": True}).encode()


def make_pbi_chat(addon: FrontendAddon, name: str = "the customer chat") -> None:
    flow = post(CHAT_URL, chat_body(name, tail=[tool_result()]))
    addon.process_request(flow)
    assert flow.metadata[META_KEY].mode == "on"


# ---------------------------------------------------------------------------------- brand-new chats
@pytest.mark.parametrize("api", ["chat", "responses"])
def test_a_brand_new_chat_is_forwarded_byte_identical_right_after_a_pbi_chat(addon, state, api):
    make_pbi_chat(addon)
    build, url = (chat_body, CHAT_URL) if api == "chat" else (responses_body, RESPONSES_URL)
    for turns in (("hello",), ("hello", f"and {NAME}?"), (f"about {NAME}",)):
        flow = post(url, build(*turns))
        before = fingerprint(flow)
        addon.process_request(flow)
        assert flow.response is None, (api, turns)
        assert fingerprint(flow) == before
    stats = state.stats()
    assert stats["frontend_blocked"] == 0 and stats["frontend_masked"] == 1


def test_a_brand_new_chat_is_untouched_even_with_the_same_turn_id_of_no_pbi_chat(addon, state):
    make_pbi_chat(addon)
    body = chat_body("hello", f"about {NAME}")
    flow = post(CHAT_URL, body, turn="turn-unrelated")
    addon.process_request(flow)
    assert flow.response is None and flow.request.raw_content == body


def test_the_old_adapter_wide_window_is_gone(addon, state, monkeypatch):
    make_pbi_chat(addon)
    assert not hasattr(state, "frontend_pbi_recent") and not hasattr(state, "_frontend_recent")
    for minute in (0, 1, 14):
        monkeypatch.setattr(state, "clock", lambda m=minute: 1000.0 + m * 60)
        body = chat_body("fresh chat", f"about {NAME}")
        flow = post(CHAT_URL, body)
        addon.process_request(flow)
        assert flow.response is None and flow.request.raw_content == body


# ---------------------------------------------------------------------------------- unknown routes
def test_an_unknown_copilot_endpoint_of_another_chat_is_forwarded_untouched(addon, state):
    make_pbi_chat(addon)
    body = json.dumps({"input": [wrapped("other chat")], "who": NAME}).encode()
    flow = post(f"{COPILOT}/some/new/endpoint", body)
    addon.process_request(flow)
    assert flow.response is None and flow.request.raw_content == body
    assert state.stats()["frontend_blocked"] == 0


def test_an_unknown_copilot_endpoint_without_any_chat_is_forwarded_untouched(addon):
    body = json.dumps({"x": NAME}).encode()
    flow = post(f"{COPILOT}/telemetry", body)
    addon.process_request(flow)
    assert flow.response is None and flow.request.raw_content == body


def test_an_unknown_body_that_carries_the_marker_is_blocked(addon):
    flow = post(f"{COPILOT}/some/new/endpoint", json.dumps({"x": MARKER}).encode())
    addon.process_request(flow)
    assert flow.response.status_code == 400 and BLOCK_UNKNOWN_TEXT in flow.response.get_text()


def test_an_unknown_endpoint_of_a_masked_copilot_chat_is_blocked_by_its_content_key(addon, state):
    make_pbi_chat(addon, "the customer chat")
    body = json.dumps(
        {"messages": [wrapped("the customer chat"), wrapped(f"about {NAME}")]}
    ).encode()
    flow = post(f"{COPILOT}/some/new/endpoint", body)
    addon.process_request(flow)
    assert flow.response.status_code == 400 and BLOCK_UNKNOWN_TEXT in flow.response.get_text()
    assert NAME not in flow.response.get_text()
    # an input[] shape (Responses-like) of the same chat is recognised too
    body2 = json.dumps({"input": [wrapped("the customer chat")]}).encode()
    flow2 = post(f"{COPILOT}/another/endpoint", body2)
    addon.process_request(flow2)
    assert flow2.response.status_code == 400


def test_an_unknown_endpoint_of_a_masked_turn_is_blocked_by_its_turn_id(addon, state):
    flow = post(CHAT_URL, chat_body("the customer chat", tail=[tool_result()]), turn="turn-5")
    addon.process_request(flow)
    side = post(f"{COPILOT}/some/new/endpoint", json.dumps({"x": NAME}).encode(), turn="turn-5")
    addon.process_request(side)
    assert side.response.status_code == 400
    elsewhere = post(f"{COPILOT}/some/new/endpoint", json.dumps({"x": NAME}).encode(), turn="t-6")
    addon.process_request(elsewhere)
    assert elsewhere.response is None


def test_an_unknown_body_naming_a_masked_chatgpt_conversation_is_blocked(addon, state):
    state.frontend_mode(CID, True, "chatgpt_web")
    hit = post(
        "https://chatgpt.com/backend-api/new/thing", json.dumps({"conversation_id": CID}).encode()
    )
    addon.process_request(hit)
    assert hit.response.status_code == 400
    miss = post(
        "https://chatgpt.com/backend-api/new/thing",
        json.dumps({"conversation_id": OTHER_CID}).encode(),
    )
    addon.process_request(miss)
    assert miss.response is None
    plain = post("https://chatgpt.com/backend-api/new/thing", json.dumps({"x": NAME}).encode())
    addon.process_request(plain)
    assert plain.response is None


def test_a_get_without_a_body_is_never_blocked(addon, state):
    state.frontend_mode(CID, True, "chatgpt_web")
    flow = post("https://chatgpt.com/backend-api/new/thing", b"", method="GET")
    addon.process_request(flow)
    assert flow.response is None


# ---------------------------------------------------------------------------------- errors
def test_an_error_while_deciding_forwards_an_unmarked_request_untouched(addon, state, monkeypatch):
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("secret " + NAME)

    monkeypatch.setattr(state, "frontend_mode", boom)
    body = chat_body("a chat", f"about {NAME}")
    flow = post(CHAT_URL, body)
    before = fingerprint(flow)
    addon.safe_request(flow)
    assert flow.response is None and fingerprint(flow) == before
    stats = state.stats()
    assert stats["errors"] == 1 and stats["frontend_blocked"] == 0
    assert stats["frontend_passthrough"] == 1


def test_an_error_while_deciding_blocks_a_request_that_carries_the_marker(
    addon, state, monkeypatch
):
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("secret " + NAME)

    monkeypatch.setattr(state, "frontend_mode", boom)
    flow = post(CHAT_URL, chat_body("a chat", tail=[tool_result()]))
    addon.safe_request(flow)
    assert flow.response.status_code == 400 and "internal machine" not in flow.response.get_text()
    assert (
        NAME not in flow.response.get_text()
        and "did not leave this machine" in flow.response.get_text()
    )
    assert state.stats()["frontend_blocked"] == 1


def test_a_marker_in_a_gzip_body_is_found_when_deciding_failed(addon, state, monkeypatch):
    from client_gateway.adapters.streams import encode_bytes

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("x")

    monkeypatch.setattr(state, "frontend_mode", boom)
    flow = post(CHAT_URL, encode_bytes(chat_body("c", tail=[tool_result()]), "gzip"))
    flow.request.headers["content-encoding"] = "gzip"
    addon.safe_request(flow)
    assert flow.response.status_code == 400


def test_an_error_after_the_mode_is_known_never_blocks_a_passthrough_flow(
    addon, state, monkeypatch
):
    body = chat_body("a chat", f"about {NAME}")
    flow = post(CHAT_URL, body)
    before = fingerprint(flow)

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("secret " + NAME)

    monkeypatch.setattr(addon, "_mask", boom)
    monkeypatch.setattr(state, "bump", boom)  # even the counter of the passthrough path fails
    addon.safe_request(flow)
    assert flow.response is None and fingerprint(flow) == before


def test_an_error_after_the_mode_is_known_blocks_a_masked_flow(addon, state, monkeypatch):
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("secret " + NAME)

    monkeypatch.setattr(addon, "_mask", boom)
    flow = post(CHAT_URL, chat_body("a chat", tail=[tool_result()]))
    addon.safe_request(flow)
    assert flow.response.status_code == 400 and NAME not in flow.response.get_text()


def test_an_error_while_unmasking_blocks_that_flow(addon, state, monkeypatch):
    addon.process_request(post(CHAT_URL, chat_body("a chat", tail=[tool_result()])))
    flow = post(CHAT_URL, chat_body("a chat", "masking off"))

    def boom(plain: bytes) -> bytes:
        raise RuntimeError("secret")

    monkeypatch.setattr(addon, "_unmask_body", boom)
    addon.safe_request(flow)
    assert flow.response.status_code == 400


def test_a_side_call_error_with_no_marker_is_forwarded(addon, state, monkeypatch):
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("x")

    monkeypatch.setattr(state, "frontend_turn_chat", boom)
    body = side_body(f"about {NAME}")
    flow = post(CHAT_URL, body, turn="turn-1")
    addon.safe_request(flow)
    assert flow.response is None and flow.request.raw_content == body


# ---------------------------------------------------------------------------------- logging
def test_a_block_logs_its_route_label_and_never_a_chat_id(tmp_path):
    log = GatewayLog(tmp_path)
    state = make_state(tmp_path)
    addon = FrontendAddon(state, log)
    state.frontend_mode(CID, True, "chatgpt_web")
    flow = post(
        f"https://chatgpt.com/backend-api/conversation/{CID}", json.dumps({"t": 1}).encode()
    )
    addon.process_request(flow)
    assert flow.response.status_code == 400
    log.close()
    text = (tmp_path / "gateway.log").read_text(encoding="utf-8")
    events = [json.loads(line) for line in text.splitlines()]
    blocked = [e for e in events if e["event"] == "frontend_blocked"]
    assert len(blocked) == 1 and blocked[0]["route"] == "/backend-api/conversation/:id"
    assert CID not in text and NAME not in text and MARKER not in text
