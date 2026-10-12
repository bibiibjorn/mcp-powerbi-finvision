"""State, control-surface and resource findings of the 2026-10-03 deep review
(G-10, 11, 12, 19, 20, 22, 25, 26). Temp homes only; never the live gateway."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from client_gateway import cli, control_client, server, state as state_mod
from client_gateway.memo import RemaskMemo
from client_gateway.state import DEFAULT_SESSION, GatewayState, ensure_token
from core.client_guard.tokens import load_or_create_key

ROOT = Path(__file__).resolve().parent.parent


def make(tmp_path, mode: str = "off") -> GatewayState:
    return GatewayState(home=tmp_path, default_mode=mode, images_policy="withhold")


# ------------------------------------------------------------------------------ G-10
class TestModeTtlFromUse:
    def test_a_chat_in_use_keeps_its_switch_past_seven_days(self, tmp_path, monkeypatch) -> None:
        now = [1_000_000.0]
        monkeypatch.setattr(state_mod.time, "time", lambda: now[0])
        st = make(tmp_path)
        st.set_mode("chat-1", "on")
        for _day in range(10):  # used once a day for ten days
            now[0] += 24 * 3600
            st.session("chat-1")
        restarted = make(tmp_path)  # a gateway restart 10 days after the switch
        assert restarted.effective_mode("chat-1") == "on"

    def test_an_unused_chat_still_expires(self, tmp_path, monkeypatch) -> None:
        now = [1_000_000.0]
        monkeypatch.setattr(state_mod.time, "time", lambda: now[0])
        st = make(tmp_path)
        st.set_mode("chat-2", "on")
        now[0] += 8 * 24 * 3600
        restarted = make(tmp_path)
        assert restarted.effective_mode("chat-2") == "off"
        assert restarted.modes_expired == 1
        assert restarted.status()["modes_expired_at_start"] == 1

    def test_the_renewal_is_throttled(self, tmp_path, monkeypatch) -> None:
        writes: list[int] = []
        real = state_mod._write_modes
        monkeypatch.setattr(state_mod, "_write_modes", lambda *a: (writes.append(1), real(*a)))
        st = make(tmp_path)
        st.set_mode("chat-3", "on")
        before = len(writes)
        for _ in range(50):
            st.session("chat-3")
        assert len(writes) == before


# ------------------------------------------------------------------------------ G-19
class TestHeaderlessRequests:
    def test_a_request_without_a_session_follows_the_strictest_chat(self, tmp_path) -> None:
        st = make(tmp_path)
        assert st.effective_mode("") == "off"
        st.set_mode("chat-1", "on")
        assert st.effective_mode("") == "on" and st.effective_mode(None) == "on"
        assert st.effective_mode("chat-9") == "off"  # another named chat is not affected
        assert st.any_masked()

    def test_a_chat_with_its_own_off_is_not_overridden(self, tmp_path) -> None:
        st = make(tmp_path)
        st.set_mode("chat-1", "off", st.token)
        st.set_mode("chat-2", "on")
        assert st.effective_mode("chat-1") == "off"

    def test_counter_exists(self, tmp_path) -> None:
        assert "headerless_requests" in make(tmp_path).stats()
        assert DEFAULT_SESSION == "default"


# ------------------------------------------------------------------------------ G-20
class TestStatusWarnings:
    def test_empty_dictionary_and_store_error_are_reported(self, tmp_path) -> None:
        st = make(tmp_path, "on")
        assert "dictionary_empty" in st.status()["warnings"]
        st.smap.add_values("CUS", ["Zorvex Holding"], "t")
        assert "dictionary_empty" not in st.status()["warnings"]

        class Store:
            error = "InvalidTag"

        class Det:
            store = Store()

            def status(self):
                return {}

        st.detectors = Det()
        assert "store_error:InvalidTag" in st.warnings()


# ------------------------------------------------------------------------------ G-22
class TestControlSurface:
    def test_status_session_and_refresh_ask_for_the_token(self) -> None:
        assert ("GET", "/__gateway/status") in server.TOKEN_ROUTES
        assert ("GET", "/__gateway/session") in server.TOKEN_ROUTES
        assert ("POST", "/__gateway/refresh") in server.TOKEN_ROUTES
        assert ("GET", "/__gateway/health") not in server.TOKEN_ROUTES

    @pytest.mark.parametrize(
        "url,ok",
        [
            ("https://api.anthropic.com", True),
            ("http://127.0.0.1:9000", True),
            ("http://localhost:9000", True),
            ("http://evil.example", False),
            ("ftp://x", False),
            ("http://api.anthropic.com", False),
        ],
    )
    def test_upstream_must_be_https_or_loopback(self, url: str, ok: bool) -> None:
        assert server.upstream_allowed(url) is ok

    def test_a_plain_http_upstream_is_refused_by_the_gateway(self, tmp_path) -> None:
        with pytest.raises(ValueError):
            server.Gateway(make(tmp_path), port=0, upstream="http://api.example.com")

    def test_the_clients_send_the_token(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("FINVISION_MASKING_HOME", str(tmp_path))
        token = ensure_token(tmp_path)
        assert control_client._token() == token
        seen: list[dict] = []

        class Resp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"{}"

        def fake_open(req, timeout=0):
            seen.append(dict(req.header_items()))
            return Resp()

        monkeypatch.setattr(control_client.urllib.request, "urlopen", fake_open)
        control_client.request("GET", "/__gateway/status", base="http://127.0.0.1:1")
        control_client.request("GET", "/__gateway/health", base="http://127.0.0.1:1")
        assert seen[0].get("X-gateway-token") == token and "X-gateway-token" not in seen[1]


# ------------------------------------------------------------------------------ G-25
class TestResourceBounds:
    def test_the_memo_is_bounded_by_characters_not_only_entries(self) -> None:
        memo = RemaskMemo(max_entries=1000, max_chars=100)
        for i in range(10):
            memo.remember("m" * 30 + str(i), "u" * 30 + str(i))
        assert len(memo) <= 2
        assert memo.lookup("u" * 30 + "9") == "m" * 30 + "9"  # the newest is kept
        memo.remember("x" * 500, "y" * 500)  # bigger than the budget: not kept
        assert memo.lookup("y" * 500) is None

    def test_the_memo_char_count_stays_consistent(self) -> None:
        memo = RemaskMemo(max_entries=3, max_chars=10_000)
        for i in range(10):
            memo.remember(f"masked{i}", f"plain{i}")
            memo.remember(f"masked{i}", f"plain{i}")  # same entry again
        assert len(memo) == 3 and memo._chars == sum(len(k) + len(v) for k, v in memo._map.items())

    def test_a_request_body_is_capped_near_the_api_limit(self) -> None:
        assert server.MAX_BODY_BYTES <= 64 * 1024 * 1024


# ------------------------------------------------------------------------------ G-26
class TestFirstCreationRaces:
    def test_two_starters_agree_on_one_token(self, tmp_path) -> None:
        results: list[str] = []
        barrier = threading.Barrier(8)

        def go() -> None:
            barrier.wait()
            results.append(ensure_token(tmp_path))

        threads = [threading.Thread(target=go) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert len(results) == 8 and len(set(results)) == 1
        assert (tmp_path / "gateway.token").read_text("ascii").strip() == results[0]

    def test_a_broken_token_file_is_replaced(self, tmp_path) -> None:
        (tmp_path / "gateway.token").write_text("short", encoding="ascii")
        token = ensure_token(tmp_path)
        assert len(token) >= 32 and ensure_token(tmp_path) == token

    def test_two_starters_agree_on_one_order_key(self, tmp_path) -> None:
        keys: list[bytes] = []
        errors: list[BaseException] = []
        barrier = threading.Barrier(8)

        def go() -> None:
            barrier.wait()
            try:
                keys.append(load_or_create_key(tmp_path))
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=go) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert not errors and len(set(keys)) == 1 and len(keys[0]) == 32
        assert not list(tmp_path.glob("order.key.*.tmp"))


# ------------------------------------------------------------------------------ G-12
def test_serve_loads_no_model_when_masking_is_off_by_default(tmp_path, monkeypatch) -> None:
    from client_gateway import detectors

    monkeypatch.setenv("FINVISION_MASKING_HOME", str(tmp_path))
    monkeypatch.setenv("FINVISION_GATEWAY_PROVIDER", "0")
    monkeypatch.setenv("FINVISION_GATEWAY_DEFAULT_MODE", "off")  # conftest sets "on" for the suite
    started: list[int] = []
    monkeypatch.setattr(detectors.Detectors, "masking_on", lambda self: started.append(1))

    class Boom:
        def __init__(self, *a, **k) -> None:
            raise OSError("bind")

    monkeypatch.setattr(server, "Gateway", Boom)
    assert cli.serve(0, upstream="http://127.0.0.1:1", provider=False) == 2
    assert started == []  # default_mode off: the worker waits for a chat that switches on
    # ... unless a chat that was masked before the restart is remembered (its switch survives)
    GatewayState(home=tmp_path, default_mode="off").set_mode("chat-1", "on")
    before = len(started)  # (the switch itself starts the worker in that earlier process)
    assert cli.serve(0, upstream="http://127.0.0.1:1", provider=False) == 2
    assert len(started) == before + 1


# ------------------------------------------------------------------------------ G-11
class TestOffByDefaultTexts:
    FILES = [
        "client_gateway/folders.py",
        "hooks/client_masking_hook.py",
        "server/handlers/client_mode_handler.py",
    ]

    @pytest.mark.parametrize("rel", FILES)
    def test_no_text_calls_masking_default_on(self, rel: str) -> None:
        text = (ROOT / rel).read_text(encoding="utf-8").lower()
        for phrase in ("default on", "it is the default", "(default on)", "default: on"):
            assert phrase not in text, (rel, phrase)

    def test_the_shipped_default_is_off(self) -> None:
        cfg = json.loads((ROOT / "config" / "default_config.json").read_text("utf-8"))
        assert cfg["client_gateway"]["default_mode"] == "off"

    def test_the_tool_note_says_what_the_guard_covers(self) -> None:
        from server.handlers import client_mode_handler as h

        assert "off by default" in h.__doc__ and "safety net" in h.__doc__
        assert "off by default" in h._ROUTING_NOTE
