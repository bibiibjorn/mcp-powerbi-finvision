"""Tests of client_gateway.state, cache and log (synthetic data only)."""

from __future__ import annotations

import json
import threading

import pytest

from client_gateway.cache import BoundedCache
from client_gateway.log import GatewayLog, route_label
from client_gateway.state import (
    DEFAULT_SESSION,
    GatewayState,
    ModeDenied,
    clean_mode,
    ensure_token,
    read_token,
)

NAME = "Zorvex Holding"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def state(tmp_path):
    return GatewayState(home=tmp_path, default_mode="on")


def test_the_shipped_default_is_off(tmp_path, monkeypatch):
    monkeypatch.delenv("FINVISION_GATEWAY_DEFAULT_MODE", raising=False)
    assert GatewayState(home=tmp_path).default_mode == "off"


def test_default_mode_is_on_and_invalid_values_mean_on(tmp_path):
    assert GatewayState(home=tmp_path).default_mode == "on"  # conftest pins "on"
    assert GatewayState(home=tmp_path, default_mode="OFF").default_mode == "off"
    assert GatewayState(home=tmp_path, default_mode="banana").default_mode == "on"
    assert clean_mode(None) == "on" and clean_mode(" Off ") == "off"


def test_default_mode_off_gives_passthrough_sessions(tmp_path):
    st = GatewayState(home=tmp_path, default_mode="off")
    assert st.effective_mode("abc") == "off"


def test_token_file_is_created_once_and_reused(tmp_path):
    t1 = ensure_token(tmp_path)
    assert len(t1) >= 32 and read_token(tmp_path) == t1
    assert ensure_token(tmp_path) == t1
    assert GatewayState(home=tmp_path).token == t1
    assert read_token(tmp_path / "none") is None


def test_session_off_needs_the_token_but_on_does_not(state):
    with pytest.raises(ModeDenied):
        state.set_mode("s1", "off")
    with pytest.raises(ModeDenied):
        state.set_mode("s1", "off", "wrong-token")
    assert state.effective_mode("s1") == "on"
    assert state.set_mode("s1", "off", state.token)["mode"] == "off"
    assert state.effective_mode("s1") == "off"
    assert state.effective_mode("s2") == "on"  # other sessions unaffected
    assert state.set_mode("s1", "on")["mode"] == "on"  # off -> on: no token
    assert state.effective_mode("s1") == "on"


def test_bad_mode_value(state):
    with pytest.raises(ValueError):
        state.set_mode("s1", "maybe", state.token)


def test_global_switch_needs_the_token_both_ways_and_resets_session_switches(state):
    state.set_mode("s1", "off", state.token)
    with pytest.raises(ModeDenied):
        state.set_mode("", "on")
    with pytest.raises(ModeDenied):
        state.set_mode("", "off")
    state.set_mode("", "off", state.token)
    assert state.effective_mode("s1") == "off" and state.effective_mode("new") == "off"
    state.set_mode("s2", "on")  # a session can still opt back in
    assert state.effective_mode("s2") == "on"
    state.set_mode("", "on", state.token)
    assert state.effective_mode("s1") == "on" and state.effective_mode("s2") == "on"


def test_no_token_available_means_off_is_impossible(tmp_path, monkeypatch):
    import client_gateway.state as mod

    def fail(_home):
        raise OSError("read-only")

    monkeypatch.setattr(mod, "ensure_token", fail)
    st = GatewayState(home=tmp_path)
    assert st.token is None
    with pytest.raises(ModeDenied):
        st.set_mode("s", "off", "anything")
    assert not st.check_token(None) and not st.check_token("")


def test_empty_session_id_maps_to_default(state):
    assert state.session("").session_id == DEFAULT_SESSION
    assert state.session(None) is state.session("default")
    assert state.session("  ") is state.session("default")


def test_peek_mode_does_not_create_a_session(state):
    assert state.peek_mode("ghost") == "on"
    assert state.status()["sessions"]["count"] == 0


def test_sessions_have_their_own_memo(state):
    a, b = state.session("a"), state.session("b")
    a.memo.remember("m", "u")
    assert a.memo.lookup("u") == "m" and b.memo.lookup("u") is None


def test_idle_sessions_are_evicted_after_24_hours(tmp_path):
    clock = Clock()
    st = GatewayState(home=tmp_path, clock=clock, idle_ttl_s=24 * 3600.0)
    st.session("old").memo.remember("m", "u")
    st.set_mode("old", "off", st.token)
    clock.now += 23 * 3600
    st.session("fresh")
    assert st.status()["sessions"]["count"] == 2
    clock.now += 2 * 3600  # "old" idle for 25 h, "fresh" for 2 h
    st.session("fresh")
    assert st.status()["sessions"]["count"] == 1
    # evicted (memo gone) and re-created: the chat's own switch comes back from session_modes.json
    assert st.effective_mode("old") == "off"
    assert st.session("old").memo.lookup("m") is None


def test_status_has_counts_only(state):
    state.smap.add_values("CUS", [NAME, "Quillan Trust"], "src")
    state.session("11111111-2222-3333-4444-555555555555")
    state.bump("requests", 3)
    status = state.status()
    text = json.dumps(status)
    assert NAME not in text and "Quillan" not in text and state.token not in text
    assert status["dictionary"]["by_prefix"] == {"CUS": 2}
    assert status["stats"]["requests"] == 3
    assert status["sessions"]["items"][0]["id"] == "11111111"  # short id only
    assert status["provider"] == {"attached": False}


def test_stable_pseudonyms_across_states_sharing_a_home(tmp_path):
    a = GatewayState(home=tmp_path)
    b = GatewayState(home=tmp_path)
    a.smap.add_values("CUS", [NAME], "x")
    b.smap.add_values("CUS", ["Other One", NAME], "y")
    assert a.smap.mask_text(NAME)[0] == b.smap.mask_text(NAME)[0] != NAME


def test_readiness_gate(state):
    assert state.ready
    state.set_ready(False)
    assert not state.ready and not state.wait_ready(0.01)
    threading.Timer(0.05, state.set_ready, args=(True,)).start()
    assert state.wait_ready(2)


def test_refresh_without_provider(state):
    assert state.request_refresh() is False


def test_stats_are_thread_safe(state):
    def work():
        for _ in range(1000):
            state.bump("requests")

    threads = [threading.Thread(target=work) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert state.stats()["requests"] == 8000


def test_image_policy_validation(tmp_path):
    assert GatewayState(home=tmp_path, images_policy="WITHHOLD").images_policy == "withhold"
    assert GatewayState(home=tmp_path, images_policy="weird").images_policy == "redact"


# ---------------------------------------------------------------------------------- cache / log
def test_bounded_cache_evicts_oldest_by_cost():
    c = BoundedCache(10)
    c.put("a", 1, 4)
    c.put("b", 2, 4)
    c.put("c", 3, 4)  # 12 > 10: "a" goes
    assert c.get("a") is None and c.get("b") == 2 and c.get("c") == 3 and c.cost == 8
    c.put("huge", 4, 11)  # never stored
    assert c.get("huge") is None
    c.put("b", 5, 2)
    assert c.get("b") == 5 and c.cost == 6
    c.clear()
    assert len(c) == 0 and c.cost == 0


def test_route_label_hides_ids():
    assert route_label("/v1/messages/batches/msgbatch_01ABC/results?x=1") == (
        "/v1/messages/batches/:id/results"
    )
    assert route_label("/v1/messages") == "/v1/messages"


def test_log_holds_counts_and_safe_labels_only(tmp_path):
    log = GatewayLog(tmp_path)
    log.event(
        "request",
        route="/v1/messages",
        error_type="Bad Type with spaces",
        n=3,
        ok=True,
        secret=NAME,
        ratio=1.5,
    )  # type: ignore[arg-type]
    log.close()
    line = (tmp_path / "gateway.log").read_text(encoding="utf-8").strip()
    rec = json.loads(line)
    assert rec["event"] == "request" and rec["n"] == 3 and rec["ok"] is True
    assert rec["error_type"] == "?" and rec["route"] == "/v1/messages"
    assert "secret" not in rec and "ratio" not in rec and NAME not in line


def test_log_without_home_and_after_close_never_raises(tmp_path):
    GatewayLog(None).event("x", n=1)
    log = GatewayLog(tmp_path)
    log.close()
    log.event("after_close", n=1)


# ------------------------------------------------------------------ switches survive a restart
SID_A = "3f2a9c1e-0000-4000-8000-00000000000a"
SID_B = "77c1d0b2-0000-4000-8000-00000000000b"


def test_a_session_switch_survives_a_restart(tmp_path):
    first = GatewayState(home=tmp_path, default_mode="off")
    first.set_mode(SID_A, "on")
    again = GatewayState(home=tmp_path, default_mode="off")
    assert again.peek_mode(SID_A) == "on"
    assert again.effective_mode(SID_A) == "on"
    assert again.effective_mode(SID_B) == "off"


def test_modes_of_a_replaced_gateway_are_carried_by_prefix(tmp_path):
    from client_gateway.state import carry_session_modes

    # status of the old gateway: chat A was masked (its default), chat B switched off on purpose
    old = [
        {"id": SID_A[:8], "mode": "on", "own_switch": False},
        {"id": SID_B[:8], "mode": "off", "own_switch": True},
    ]
    assert carry_session_modes(tmp_path, old) == 2
    new = GatewayState(home=tmp_path, default_mode="off")
    assert new.effective_mode(SID_A) == "on"  # a masked chat stays masked after the update
    assert new.effective_mode(SID_B) == "off"
    assert new.session(SID_A).override == "on"


def test_a_global_switch_clears_the_saved_session_switches(tmp_path):
    state = GatewayState(home=tmp_path, default_mode="off")
    state.set_mode(SID_A, "on")
    state.set_mode("", "off", token=state.token)
    assert GatewayState(home=tmp_path, default_mode="off").peek_mode(SID_A) == "off"


def test_old_saved_switches_expire(tmp_path):
    import json as _json

    (tmp_path / "session_modes.json").write_text(
        _json.dumps({"sessions": {SID_A: {"mode": "on", "at": 1.0}}, "prefixes": {}}),
        encoding="utf-8",
    )
    assert GatewayState(home=tmp_path, default_mode="off").peek_mode(SID_A) == "off"
