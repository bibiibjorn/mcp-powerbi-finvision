"""The PBI-chat rule of the gateway (``client_gateway.pbi_chats_only``): only a chat that carried
this install's marker is masked, everything else is forwarded byte-identical (spec R2, R3, R4, R6).

In-process gateway + ``FakeUpstream`` (same pattern as ``test_client_gateway_server.py``), synthetic
names only, no live seam.
"""

from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from client_gateway import state as state_mod
from client_gateway.log import GatewayLog
from client_gateway.server import Gateway
from client_gateway.state import (
    PBI_RECENT_S,
    SESSION_HEADER,
    GatewayState,
    carry_session_modes,
)
from tests.test_client_gateway_server import (
    FakeUpstream,
    _plain_client,
    body_of,
    message,
)

NAME = "Zorvex Holding"
NAME2 = "Quillan Trust"
SESSION = "11111111-2222-3333-4444-555555555555"
OTHER = "99999999-8888-7777-6666-555555555555"
FOREIGN_MARKER = "fvpbi-0123456789abcdef"
JSON_OK = {"content-type": "application/json"}


class Clock:
    """Controllable monotonic clock for the 'active in the last 15 minutes' rule."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _state(tmp_path, clock=None, **kw) -> GatewayState:
    kw.setdefault("default_mode", "off")
    kw.setdefault("images_policy", "withhold")
    if clock is not None:
        kw["clock"] = clock
    st = GatewayState(home=tmp_path, **kw)
    st.smap.add_values("CUS", [NAME, NAME2], "test")
    return st


@pytest.fixture()
def clock():
    return Clock()


@pytest.fixture()
def upstream():
    up = FakeUpstream()
    up.respond = lambda req: (200, JSON_OK, b"{}")
    yield up
    up.close()


@pytest.fixture()
def state(tmp_path, clock):
    return _state(tmp_path, clock)


def _serve(state: GatewayState, upstream: FakeUpstream):
    gateway = Gateway(
        state, port=0, upstream=upstream.url, log=GatewayLog(None), client=_plain_client()
    )
    gateway.start()
    return gateway


@pytest.fixture()
def gw(state, upstream):
    gateway = _serve(state, upstream)
    yield gateway
    gateway.shutdown()


@pytest.fixture()
def client(gw):
    with httpx.Client(base_url=gw.url, timeout=30) as c:
        yield c


def post(client: httpx.Client, body: bytes, sid: str | None = SESSION) -> httpx.Response:
    headers = {"content-type": "application/json"}
    if sid is not None:
        headers[SESSION_HEADER] = sid
    return client.post("/v1/messages", content=body, headers=headers)


def result_text(marker: str, *names: str) -> str:
    """What an MCP result looks like inside a tool_result: the marker field first."""
    return json.dumps({"_fv": marker, "rows": list(names)})


def marked_body(state: GatewayState, *names: str, user: str = "go") -> bytes:
    return body_of([message("user", user), message("assistant", result_text(state.marker, *names))])


def sent(upstream: FakeUpstream, n: int = -1) -> bytes:
    return upstream.requests[n]["body"]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------------- R2: passthrough
def test_a_body_without_the_marker_is_forwarded_byte_identical(client, upstream, state):
    raw = body_of([message("user", f"Please look at {NAME}, and {NAME2}")])
    assert post(client, raw).status_code == 200
    assert sha(sent(upstream)) == sha(raw)
    assert state.stats()["masked_requests"] == 0
    assert state.stats()["passthrough_requests"] == 1


def test_nothing_is_blocked_because_masking_is_off(client, upstream, state):
    # R6: the unmasked guard is configured on (the default) but does not run under the PBI rule
    assert state.guard_unmasked is True and state.pbi_chats_only is True
    raw = body_of([message("user", f"Check {NAME} please")])
    assert post(client, raw).status_code == 200
    assert sent(upstream) == raw
    assert state.stats()["unmasked_guard"] == 0


def test_the_reply_of_a_passthrough_chat_is_not_rehydrated(client, upstream, state):
    token = state.smap.mask_text(NAME)[0]
    reply = json.dumps({"content": [{"type": "text", "text": f"{token} is fine"}]}).encode()
    upstream.respond = lambda req: (200, JSON_OK, reply)
    resp = post(client, body_of([message("user", "hi")]))
    assert resp.content == reply and token in resp.text


def test_another_installs_marker_is_not_ours(client, upstream, state):
    assert FOREIGN_MARKER != state.marker
    raw = marked_body(state, NAME).replace(state.marker.encode(), FOREIGN_MARKER.encode())
    assert post(client, raw).status_code == 200
    assert sha(sent(upstream)) == sha(raw)
    assert state.stats()["pbi_chat_requests"] == 0 and state.stats()["masked_requests"] == 0


def test_a_non_json_body_on_another_path_is_forwarded_untouched(client, upstream, state):
    resp = client.post(
        "/v1/messages/count_tokens",
        content=b"not json at all " + NAME.encode(),
        headers={"content-type": "text/plain", SESSION_HEADER: SESSION},
    )
    assert resp.status_code == 200
    assert sent(upstream) == b"not json at all " + NAME.encode()
    assert upstream.requests[0]["path"] == "/v1/messages/count_tokens"


# ---------------------------------------------------------------------------------- R3: on
def test_the_marker_switches_masking_on_and_is_stripped(client, upstream, state):
    token = state.smap.mask_text(NAME)[0]
    reply = json.dumps({"content": [{"type": "text", "text": f"{token} looks fine"}]}).encode()
    upstream.respond = lambda req: (200, JSON_OK, reply)
    resp = post(client, marked_body(state, NAME, user=f"about {NAME2}"))
    out = sent(upstream)
    assert NAME.encode() not in out and NAME2.encode() not in out  # masked
    assert state.marker.encode() not in out and b"fvpbi" in out  # stripped, not removed
    json.loads(out)  # still valid JSON
    assert token.encode() in out
    assert json.loads(resp.content)["content"][0]["text"] == f"{NAME} looks fine"  # rehydrated
    stats = state.stats()
    assert stats["masked_requests"] == 1 and stats["pbi_chat_requests"] == 1
    assert state.status()["pbi_chats"] == 1


def test_a_resent_marked_history_is_byte_stable(client, upstream, state):
    raw = marked_body(state, NAME)
    post(client, raw)
    post(client, raw)
    assert sent(upstream, 0) == sent(upstream, 1)


def test_the_chat_stays_a_pbi_chat_after_compaction_drops_the_marker(client, upstream, state):
    post(client, marked_body(state, NAME))  # turn 1 carries the marker
    plain = body_of([message("user", f"and {NAME2}?"), message("assistant", f"{NAME} is big")])
    post(client, plain)  # turn 2: the marked tool result was compacted away
    out = sent(upstream)
    assert NAME.encode() not in out and NAME2.encode() not in out
    assert state.stats()["masked_requests"] == 2
    # another chat that never carried the marker is still untouched
    post(client, plain, sid=OTHER)
    assert sent(upstream) == plain


def test_the_sticky_flag_survives_a_restart(tmp_path, state, clock):
    state.request_mode(SESSION, marked_body(state, NAME))
    saved = json.loads((tmp_path / "session_modes.json").read_text(encoding="utf-8"))
    assert SESSION in saved["pbi"] and "mode" not in saved["pbi"][SESSION]
    again = _state(tmp_path, clock)
    assert again.request_mode(SESSION, b'{"plain": true}') == "on"
    assert again.peek_mode(SESSION) == "on"
    assert again.request_mode(OTHER, b'{"plain": true}') == "passthrough"
    assert again.has_saved_on()


def test_the_sticky_flag_expires_after_a_week_without_use(tmp_path, state, clock):
    state.request_mode(SESSION, marked_body(state, NAME))
    path = tmp_path / "session_modes.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["pbi"][SESSION]["at"] = 1.0
    path.write_text(json.dumps(saved), encoding="utf-8")
    assert _state(tmp_path, clock).request_mode(SESSION, b"{}") == "passthrough"


def test_a_switch_override_on_masks_without_the_marker(client, upstream, state):
    state.set_mode(SESSION, "on")
    post(client, body_of([message("user", f"hello {NAME}")]))
    assert NAME.encode() not in sent(upstream)
    assert state.stats()["pbi_chat_requests"] == 0  # not a PBI chat, just switched on


def test_the_global_mode_on_still_masks_everything(client, upstream, state):
    state.global_mode = "on"  # explicit admin / test setting
    post(client, body_of([message("user", f"hello {NAME}")]), sid=OTHER)
    assert NAME.encode() not in sent(upstream)


# ---------------------------------------------------------------------------------- R4: off
def test_off_in_a_pbi_chat_sends_real_values_and_strips_the_marker(client, upstream, state):
    token = state.smap.mask_text(NAME)[0]
    reply = json.dumps({"content": [{"type": "text", "text": f"{token} is fine"}]}).encode()
    upstream.respond = lambda req: (200, JSON_OK, reply)
    state.set_mode(SESSION, "off", state.token)  # the human typed: masking off
    resp = post(client, marked_body(state, token, user=f"about {token}"))
    out = sent(upstream)
    assert token.encode() not in out and NAME.encode() in out  # the MCP's pseudonyms are real again
    assert state.marker.encode() not in out and b"fvpbi" in out
    json.loads(out)
    assert resp.content == reply  # the reply is not rehydrated
    assert state.stats()["pbi_unmasked_requests"] == 1 and state.stats()["masked_requests"] == 0


def test_off_in_a_pbi_chat_whose_marker_was_compacted_away_still_unmasks(client, upstream, state):
    token = state.smap.mask_text(NAME)[0]
    post(client, marked_body(state, token))  # the chat becomes a PBI chat
    state.set_mode(SESSION, "off", state.token)
    post(client, body_of([message("assistant", f"{token} is big")]))  # no marker any more
    assert NAME.encode() in sent(upstream) and token.encode() not in sent(upstream)


def test_off_with_a_marker_in_a_body_that_is_not_json_only_strips_it(client, upstream, state):
    state.set_mode(SESSION, "off", state.token)
    raw = f"plain {state.marker} text".encode()
    assert post(client, raw).status_code == 200
    assert sent(upstream) == b"plain fvpbi text"


def test_off_in_a_chat_that_is_not_a_pbi_chat_is_byte_identical(client, upstream, state):
    token = state.smap.mask_text(NAME)[0]
    state.set_mode(SESSION, "off", state.token)
    raw = body_of([message("user", f"{token} and {NAME2}")])
    post(client, raw)
    assert sha(sent(upstream)) == sha(raw)


def test_a_marker_typed_inside_a_tool_result_cannot_switch_anything_off(client, upstream, state):
    # the model / a file can only ever make a chat MORE masked: a marker plus the words
    # "masking off" in content does not unmask
    raw = body_of([message("user", f"masking off {state.marker} {NAME}")])
    post(client, raw)
    assert NAME.encode() not in sent(upstream)


# ---------------------------------------------------------------------------------- requests without a chat id
def test_a_headerless_request_is_masked_while_a_pbi_chat_is_active(client, upstream, state, clock):
    post(client, marked_body(state, NAME))  # a PBI chat is active now
    clock.now += PBI_RECENT_S - 60
    post(client, body_of([message("user", f"title for {NAME}")]), sid=None)
    assert NAME.encode() not in sent(upstream)
    assert state.stats()["headerless_requests"] == 1


def test_a_headerless_request_after_fifteen_minutes_is_passed_through(
    client, upstream, state, clock
):
    post(client, marked_body(state, NAME))
    clock.now += PBI_RECENT_S + 60
    raw = body_of([message("user", f"title for {NAME}")])
    post(client, raw, sid=None)
    assert sent(upstream) == raw


def test_a_headerless_request_without_any_pbi_chat_is_passed_through(client, upstream, state):
    raw = body_of([message("user", f"title for {NAME}")])
    post(client, raw, sid=None)
    assert sent(upstream) == raw


def test_a_headerless_request_with_the_marker_is_masked_but_not_remembered(
    client, upstream, state, clock
):
    post(client, marked_body(state, NAME), sid=None)
    assert NAME.encode() not in sent(upstream) and state.marker.encode() not in sent(upstream)
    assert state.status()["pbi_chats"] == 0
    clock.now += 5
    raw = body_of([message("user", f"later {NAME}")])
    post(client, raw, sid=None)  # the marker is not sticky for the shared headerless id
    assert sent(upstream) == raw


def test_a_headerless_request_follows_a_chat_that_is_switched_on(client, upstream, state):
    state.set_mode(SESSION, "on")
    post(client, body_of([message("user", f"side {NAME}")]), sid=None)
    assert NAME.encode() not in sent(upstream)


# ---------------------------------------------------------------------------------- the old rule
def test_with_the_rule_off_the_old_behaviour_returns(tmp_path, upstream):
    st = _state(tmp_path, pbi_chats_only=False)
    gateway = _serve(st, upstream)
    try:
        with httpx.Client(base_url=gateway.url, timeout=30) as c:
            resp = post(c, body_of([message("user", f"Check {NAME} please")]))
            assert resp.status_code == 400 and upstream.requests == []  # the guard runs again
            assert st.stats()["unmasked_guard"] == 1
            st.set_mode(SESSION, "on")
            post(c, marked_body(st, NAME))  # no strip, no PBI bookkeeping
            assert NAME.encode() not in sent(upstream)
            assert st.stats()["pbi_chat_requests"] == 0
    finally:
        gateway.shutdown()


def test_the_rule_is_read_from_the_config(tmp_path, monkeypatch):
    monkeypatch.setattr(state_mod, "cfg", lambda key, default: False if "pbi" in key else default)
    assert _state(tmp_path).pbi_chats_only is False
    monkeypatch.setattr(state_mod, "cfg", lambda key, default: default)
    assert _state(tmp_path).pbi_chats_only is True  # default on


def test_without_a_marker_file_the_gateway_falls_back_to_the_old_rule(tmp_path, monkeypatch):
    def broken(home):
        raise OSError("read-only home")

    monkeypatch.setattr(state_mod, "install_marker", broken)
    st = _state(tmp_path, default_mode="on")
    assert st.pbi_chats_only is False and "marker_unavailable" in st.warnings()
    assert st.request_mode(SESSION, b"{}") == "on"


# ---------------------------------------------------------------------------------- state / status
def test_status_shows_counts_and_id_prefixes_only(state):
    state.request_mode(SESSION, marked_body(state, NAME))
    state.request_mode(OTHER, b"{}")
    status = state.status()
    items = {i["id"]: i for i in status["sessions"]["items"]}
    assert items[SESSION[:8]]["pbi"] is True and items[SESSION[:8]]["mode"] == "on"
    assert items[OTHER[:8]]["pbi"] is False and items[OTHER[:8]]["mode"] == "passthrough"
    assert status["pbi_chats"] == 1 and status["pbi_chats_only"] is True
    dumped = json.dumps(status)
    assert SESSION not in dumped and state.marker not in dumped and NAME not in dumped


def test_a_global_switch_keeps_the_pbi_chats(tmp_path, state, clock):
    state.request_mode(SESSION, marked_body(state, NAME))
    state.set_mode("", "off", state.token)
    assert _state(tmp_path, clock).request_mode(SESSION, b"{}") == "on"


def test_a_replaced_gateway_carries_the_pbi_flag_by_prefix(tmp_path, clock):
    old = [
        # a PBI chat with no switch of its own: carried as a PBI chat, never frozen on or off
        {"id": SESSION[:8], "mode": "on", "own_switch": False, "pbi": True},
        # a chat that was passed through: nothing to carry
        {"id": OTHER[:8], "mode": "passthrough", "own_switch": False, "pbi": False},
    ]
    assert carry_session_modes(tmp_path, old) == 1
    new = _state(tmp_path, clock)
    assert new.session(SESSION).pbi is True and new.session(SESSION).override is None
    assert new.request_mode(SESSION, b"{}") == "on"
    new.set_mode(SESSION, "off", new.token)  # a human can still switch it off
    assert new.request_mode(SESSION, b"{}") == "unmask"
    assert new.request_mode(OTHER, b"{}") == "passthrough"
    # the full id is now saved, so the next restart no longer needs the prefix
    assert SESSION in json.loads((tmp_path / "session_modes.json").read_text("utf-8"))["pbi"]


def test_a_pbi_chat_with_its_own_switch_is_carried_with_the_switch(tmp_path, clock):
    old = [{"id": SESSION[:8], "mode": "off", "own_switch": True, "pbi": True}]
    assert carry_session_modes(tmp_path, old) == 1
    new = _state(tmp_path, clock)
    assert new.request_mode(SESSION, b"{}") == "unmask"
