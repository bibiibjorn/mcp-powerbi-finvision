"""Lifecycle of the routed gateway (phase 3b.5): attach / watchdog / exit, the order in which routing is
enabled, the restore, the stale-rule cleanup, and the PAC / attach routes of the gateway. Fakes only (the
certificate store, the Internet Settings, the process table, the clock); an in-process gateway on an
ephemeral loopback port for the routes. Nothing here touches the real system (rule R8)."""

from __future__ import annotations

import threading
from typing import Any

import httpx
import pytest

from client_gateway import lifecycle as life_mod
from client_gateway.lifecycle import (
    ATTACH_GRACE_S,
    InactiveRouting,
    Lifecycle,
    RoutingControl,
    cleanup_stale_rule,
)
from client_gateway.log import GatewayLog
from client_gateway.routing.ca import CAStore
from client_gateway.routing.certstore import CAInstaller, MemoryCertStore
from client_gateway.routing.pac import RoutingRefused
from client_gateway.routing.winproxy import (
    REG_DWORD,
    REG_SZ,
    MemoryInternetSettings,
    ProxyError,
    RegValue,
)
from client_gateway.routing.wpad import MemoryWpadProbe
from client_gateway.server import LIFECYCLE_ROUTES, TOKEN_ROUTES, Gateway
from client_gateway.state import TOKEN_HEADER, GatewayState
from tests._routing_fakes import fake_crypto

GATEWAY_PORT = 18890
FRONTEND_PORT = 18891
OLD_PAC = 'function FindProxyForURL(url, host) { return "PROXY corp:3128"; }'


class FakeProbe:
    """A process table: ``identify`` gives a creation time to every known pid."""

    def __init__(self, *pids: int) -> None:
        self.table: dict[int, float] = {pid: 100.0 + pid for pid in pids}

    def identify(self, pid: int) -> float | None:
        return self.table.get(pid)

    def alive(self, pid: int, identity: float) -> bool:
        return self.table.get(pid) == identity

    def die(self, pid: int) -> None:
        self.table.pop(pid, None)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeFrontend:
    def __init__(self, running: bool = True, port: int = FRONTEND_PORT) -> None:
        self.running = running
        self.port = port


class Recorder(MemoryInternetSettings):
    """Records the order of the changes (and what the PAC provider and the backup looked like)."""

    def __init__(self, *args: Any, **kw: Any) -> None:
        super().__init__(*args, **kw)
        self.events: list[str] = []

    def apply_pac(self, url: str, disable_autodetect: bool = False) -> None:
        self.events.append("apply_pac")
        super().apply_pac(url, disable_autodetect)

    def restore(self, saved: Any) -> None:
        self.events.append("restore")
        super().restore(saved)

    def notify(self) -> None:
        self.events.append("notify")
        super().notify()


@pytest.fixture()
def state(tmp_path):
    return GatewayState(home=tmp_path, default_mode="off", images_policy="withhold")


def make_control(tmp_path, state, *, settings=None, trusted=True, frontend=None, **kw):
    certs = MemoryCertStore()
    settings = settings if settings is not None else Recorder()
    store = CAStore(tmp_path, crypto=fake_crypto())
    ca = store.ensure()
    if trusted:
        CAInstaller(tmp_path, certs).install(ca)
    control = RoutingControl(
        state,
        tmp_path,
        GATEWAY_PORT,
        frontend if frontend is not None else FakeFrontend(),
        certs=certs,
        settings=settings,
        crypto=fake_crypto(),
        **kw,
    )
    return control, settings, certs, ca


# ---------------------------------------------------------------------------------- attach / detach
def test_attach_registers_a_running_process_and_detach_removes_it(state):
    life = Lifecycle(state, probe=FakeProbe(100, 200))
    assert life.attach(100) == {"ok": True, "attached": 1}
    assert life.attach(200)["attached"] == 2 and life.attach(100)["attached"] == 2
    assert life.detach(100) == {"ok": True, "attached": 1}
    assert life.attached() == 1


@pytest.mark.parametrize("bad", [0, -4, 2**31, "100", 1.5, None, True, [100]])
def test_attach_refuses_anything_that_is_not_a_process_id(state, bad):
    life = Lifecycle(state, probe=FakeProbe(1, 100))
    assert life.attach(bad) == {"ok": False, "error": "bad_pid"}
    assert life.attached() == 0
    assert life.detach(bad)["ok"] is False


def test_attach_refuses_a_process_that_does_not_exist(state):
    life = Lifecycle(state, probe=FakeProbe(100))
    assert life.attach(999) == {"ok": False, "error": "not_running"}


# ---------------------------------------------------------------------------------- the watchdog
def test_the_watchdog_exits_when_the_last_attached_process_is_gone(state):
    probe, clock, exits = FakeProbe(100, 200), FakeClock(), []
    control = InactiveRouting("test")
    life = Lifecycle(
        state, routing=control, on_exit=lambda: exits.append(1), probe=probe, clock=clock
    )
    life.attach(100)
    life.attach(200)
    assert life.tick() == "ok"
    probe.die(100)
    assert life.tick() == "ok" and exits == []  # one MCP is still there
    probe.die(200)
    assert life.tick() == "exit" and exits == [1] and life.exit_reason == "no_mcp"
    assert life.attached() == 0


def test_a_recycled_process_id_is_not_the_attached_process(state):
    probe = FakeProbe(100)
    life = Lifecycle(state, probe=probe, on_exit=lambda: None, clock=FakeClock())
    life.attach(100)
    probe.table[100] = 5.0  # the id is alive again, but it is another process
    assert life.tick() == "exit"


def test_a_gateway_nobody_attaches_to_exits_after_the_grace(state):
    clock, exits = FakeClock(), []
    life = Lifecycle(state, on_exit=lambda: exits.append(1), probe=FakeProbe(), clock=clock)
    assert life.tick() == "grace"  # the Claude Code start-up race: its MCP is not there yet
    clock.now += ATTACH_GRACE_S - 1
    assert life.tick() == "grace" and exits == []
    clock.now += 2
    assert life.tick() == "exit" and exits == [1] and life.exit_reason == "never_attached"


def test_the_grace_is_sixty_seconds_and_ends_with_the_first_attach(state):
    assert ATTACH_GRACE_S == 60.0
    probe, clock = FakeProbe(100), FakeClock()
    life = Lifecycle(state, on_exit=lambda: None, probe=probe, clock=clock)
    life.attach(100)
    clock.now += 5
    probe.die(100)
    assert life.tick() == "exit"  # attached once: no grace for a gateway whose MCP is gone


def test_shutdown_restores_the_rule_once_and_is_idempotent(tmp_path, state):
    control, settings, _certs, _ca = make_control(tmp_path, state)
    life = Lifecycle(state, routing=control, probe=FakeProbe())
    assert control.enable() == {"enabled": True, "reason": ""}
    life.shutdown()
    life.shutdown()
    assert settings.events.count("restore") == 1
    assert state.pac_provider is None and control.enabled is False


def test_the_watchdog_thread_enables_routing_then_exits_when_the_mcp_dies(tmp_path, state):
    control, settings, _certs, _ca = make_control(tmp_path, state)
    probe, done = FakeProbe(100), threading.Event()
    life = Lifecycle(
        state,
        routing=control,
        on_exit=done.set,
        probe=probe,
        interval_s=0.01,
        ready=lambda: True,
    )
    life.attach(100)
    life.start()
    deadline = threading.Event()
    for _ in range(300):
        if control.enabled:
            break
        deadline.wait(0.01)
    assert control.enabled and settings.values["AutoConfigURL"] is not None
    probe.die(100)
    assert done.wait(5), "the watchdog did not end the gateway"
    assert settings.events[-2:] == ["restore", "notify"] and control.enabled is False


def test_routing_goes_dormant_and_comes_back_the_watchdog_never_stops(tmp_path, state):
    # owner rule 2026-10-07: the gateway stays up (Claude Code talks to the AI through it); only
    # the desktop-app routing ends while no MCP is attached, and returns on the next attach
    control, settings, _certs, _ca = make_control(tmp_path, state)
    probe, clock, exits = FakeProbe(100, 200), FakeClock(), []
    life = Lifecycle(
        state, routing=control, on_exit=lambda: exits.append(1), probe=probe, clock=clock
    )
    life.attach(100)
    assert control.enable() == {"enabled": True, "reason": ""}
    probe.die(100)
    assert life.tick() == "exit" and exits == [1] and control.enabled is False
    assert life.tick() == "idle" and exits == [1]  # once: no repeated restores
    assert not life._stop.is_set()  # the watchdog (and the gateway) keep running
    assert life.attach(200) == {"ok": True, "attached": 1}
    assert control.enabled is True and life.exit_reason == ""  # routing is back


def test_routing_is_enabled_only_once_the_gateway_answers(tmp_path, state):
    control, settings, _certs, _ca = make_control(tmp_path, state)
    answering = threading.Event()
    life = Lifecycle(
        state, routing=control, probe=FakeProbe(100), interval_s=0.01, ready=answering.is_set
    )
    life.attach(100)
    life.start()
    threading.Event().wait(0.15)
    assert not control.enabled and settings.writes == []  # nothing is routed to a dead PAC URL
    answering.set()
    for _ in range(300):
        if control.enabled:
            break
        threading.Event().wait(0.01)
    assert control.enabled
    life.shutdown()


# ---------------------------------------------------------------------------------- enabling routing
def test_routing_is_enabled_in_the_order_the_spec_demands(tmp_path, state):
    control, settings, _certs, ca = make_control(
        tmp_path, state, settings=Recorder.with_values(enable=1, server="proxy.corp:8080", detect=0)
    )
    seen: dict[str, Any] = {}
    original = settings.apply_pac

    def spy(url: str, disable_autodetect: bool = False) -> None:
        seen["backup"] = control.manager.backup_exists()  # before the first change
        seen["pac"] = state.pac_provider is not None  # the PAC is published before the rule
        seen["frontend"] = control.frontend.running  # and the front end listens
        original(url, disable_autodetect)

    settings.apply_pac = spy
    assert control.enable() == {"enabled": True, "reason": ""}
    assert seen == {"backup": True, "pac": True, "frontend": True}
    assert settings.values["AutoConfigURL"].data == "http://127.0.0.1:18890/proxy.pac"
    pac = state.pac_provider()
    assert "PROXY 127.0.0.1:18891; DIRECT" in pac  # the front end's port, not the gateway's
    assert "PROXY proxy.corp:8080" in pac  # the previous static proxy answers everything else
    assert control.upstream.mode == "static_proxy"
    assert control.upstream.via("chatgpt.com") == ("http", ("proxy.corp", 8080))
    assert control.enable() == {"enabled": True, "reason": ""}  # twice is fine
    assert control.status()["pac_url"] == "http://127.0.0.1:18890/proxy.pac"
    assert ca.thumbprint  # (the CA the checks above relied on exists)


def test_the_pac_follows_the_masked_flag_of_the_state(tmp_path, state):
    control, _settings, _certs, _ca = make_control(tmp_path, state)
    control.enable()
    assert "; DIRECT" in state.pac_provider().split("}")[0]
    state.set_mode("", "on", state.token)  # a chat (here: all) is masked now
    assert state.any_masked() and "; DIRECT" not in state.pac_provider().split("}")[0]


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        ("no_frontend", "frontend_not_running"),
        ("untrusted", "ca_not_trusted"),
        ("no_ca", "ca_missing"),
        ("old_domains", "ca_needs_rotation"),
        ("locked", "refused_locked"),
        ("autodetect", "refused_autodetect"),
        ("pac_fetch", "refused_pac_unfetchable"),
        ("broken_key", "ca_unreadable"),
    ],
)
def test_every_refusal_has_a_fixed_reason_and_leaves_the_settings_alone(
    tmp_path, state, mutate, reason
):
    kw: dict[str, Any] = {}
    settings = Recorder.with_values(enable=0, detect=0)
    if mutate == "locked":
        settings = Recorder.with_values(locked=("proxy_settings_per_machine",))
    if mutate == "autodetect":
        settings = Recorder.with_values(detect=1)
    if mutate == "pac_fetch":
        settings = Recorder.with_values(pac="http://corp/p.pac", detect=0)

        def boom(url: str) -> str:
            raise RoutingRefused("pac_unfetchable")

        kw["fetch"] = boom
    control, settings, certs, ca = make_control(
        tmp_path,
        state,
        settings=settings,
        trusted=mutate != "untrusted",
        frontend=FakeFrontend(running=mutate != "no_frontend"),
        **kw,
    )
    if mutate == "no_ca":
        control.ca_store.key_path.unlink()
    if mutate == "broken_key":
        control.ca_store.key_path.write_bytes(b"garbage")
    if mutate == "old_domains":
        control.ca_store.domains = ("example.com",)
    before = dict(settings.values)
    result = control.enable()
    assert result == {"enabled": False, "reason": reason}
    assert control.status()["enabled"] is False and control.status()["reason"] == reason
    assert settings.values == before and settings.writes == [] and settings.notified == 0
    assert not control.manager.backup_exists() and state.pac_provider is None


# ---------------------------------------------------------------------------------- WPAD probe
def test_wpad_none_routes_turns_auto_detect_off_and_disable_restores_it_exactly(tmp_path, state):
    settings = Recorder.with_values(enable=0, detect=1)
    before = dict(settings.values)
    probe = MemoryWpadProbe("none")
    control, settings, _certs, _ca = make_control(tmp_path, state, settings=settings, wpad=probe)
    assert control.enable() == {"enabled": True, "reason": ""}
    assert probe.calls == 1
    assert settings.values["AutoDetect"] == RegValue(REG_DWORD, 0)  # detection is off while we run
    assert settings.values["AutoConfigURL"] == RegValue(REG_SZ, control.pac_url)
    assert control.status()["wpad"] == "none"
    assert control.disable() == {"restored": True, "was_enabled": True}
    assert settings.values == before  # AutoDetect=1 is back, exactly


def test_wpad_none_with_a_static_proxy_keeps_it_for_other_hosts(tmp_path, state):
    settings = Recorder.with_values(enable=1, server="corp:8080", detect=1)
    control, settings, _c, _ca = make_control(
        tmp_path, state, settings=settings, wpad=MemoryWpadProbe("none")
    )
    assert control.enable()["enabled"] is True
    assert control.upstream.mode == "static_proxy"  # the company proxy stays the way out


@pytest.mark.parametrize(
    ("answer", "reason"),
    [("found", "refused_autodetect"), ("unknown", "refused_autodetect_unknown")],
)
def test_wpad_found_or_unknown_refuses_and_leaves_the_settings_alone(
    tmp_path, state, answer, reason
):
    settings = Recorder.with_values(detect=1)
    control, settings, _c, _ca = make_control(
        tmp_path, state, settings=settings, wpad=MemoryWpadProbe(answer)
    )
    before = dict(settings.values)
    assert control.enable() == {"enabled": False, "reason": reason}
    assert settings.values == before and settings.writes == [] and settings.notified == 0
    assert not control.manager.backup_exists() and state.pac_provider is None
    assert control.status()["wpad"] == answer


def test_a_probe_that_raises_is_unknown_and_refuses(tmp_path, state):
    class Boom:
        def probe(self) -> str:
            raise OSError("x")

    control, settings, _c, _ca = make_control(
        tmp_path, state, settings=Recorder.with_values(detect=1), wpad=Boom()
    )
    assert control.enable() == {"enabled": False, "reason": "refused_autodetect_unknown"}


def test_the_probe_is_not_asked_when_auto_detect_is_off(tmp_path, state):
    probe = MemoryWpadProbe("found")
    control, _s, _c, _ca = make_control(
        tmp_path, state, settings=Recorder.with_values(enable=0, detect=0), wpad=probe
    )
    assert control.enable()["enabled"] is True
    assert probe.calls == 0 and "wpad" not in control.status()


def test_disable_restores_exactly_and_is_idempotent(tmp_path, state):
    settings = Recorder.with_values(enable=1, server="p:1", override="<local>", detect=0)
    before = dict(settings.values)
    control, settings, _certs, _ca = make_control(tmp_path, state, settings=settings)
    control.enable()
    assert control.disable() == {"restored": True, "was_enabled": True}
    assert settings.values == before and state.pac_provider is None
    assert control.disable() == {"restored": False, "was_enabled": False}
    assert settings.events.count("restore") == 1


def test_a_failing_restore_is_reported_and_keeps_the_backup(tmp_path, state):
    control, settings, _certs, _ca = make_control(tmp_path, state)
    control.enable()
    settings.fail_restore = True
    out = control.disable()
    assert out["restored"] is False and out["error"] == "backend_failed"
    assert (
        control.manager.backup_exists() and control.status()["reason"] == "restore_backend_failed"
    )


def test_inactive_routing_touches_nothing_and_says_why():
    inactive = InactiveRouting("real_system_forbidden")
    assert inactive.enable() == {"enabled": False, "reason": "real_system_forbidden"}
    assert inactive.disable() == {"restored": False, "was_enabled": False}
    assert inactive.status() == {
        "wanted": True,
        "enabled": False,
        "reason": "real_system_forbidden",
    }


# ---------------------------------------------------------------------------------- stale rule
OUR_PAC = "http://127.0.0.1:18890/proxy.pac"


def stale_setup(tmp_path, state, pac_after=OUR_PAC):
    control, settings, _certs, _ca = make_control(
        tmp_path, state, settings=Recorder.with_values(enable=1, server="p:1", detect=0)
    )
    control.enable()
    if pac_after != OUR_PAC:
        settings.values["AutoConfigURL"] = RegValue(REG_SZ, pac_after)
    return settings


def test_a_rule_of_ours_that_no_gateway_answers_is_a_leftover_and_is_restored(tmp_path, state):
    settings = stale_setup(tmp_path, state)
    asked = []

    def nobody(port: int) -> bool:
        asked.append(port)
        return False

    out = cleanup_stale_rule(tmp_path, settings, nobody)
    assert out == {"stale": True, "restored": True} and asked == [18890]
    assert settings.values["ProxyServer"] == RegValue(REG_SZ, "p:1")
    assert settings.values["AutoConfigURL"] is None  # it did not exist before routing
    assert settings.values["ProxyEnable"] == RegValue(REG_DWORD, 1)


def test_a_rule_whose_gateway_answers_is_never_touched(tmp_path, state):
    settings = stale_setup(tmp_path, state)
    before = dict(settings.values)
    assert cleanup_stale_rule(tmp_path, settings, lambda p: True) == {
        "stale": False,
        "answering": True,
    }
    assert settings.values == before


@pytest.mark.parametrize(
    "url", ["http://corp.example/proxy.pac", "http://127.0.0.1:8890/other.pac", "", "file:///x.pac"]
)
def test_a_rule_that_is_not_ours_is_never_touched(tmp_path, state, url):
    settings = stale_setup(tmp_path, state, pac_after=url)
    before = dict(settings.values)
    assert cleanup_stale_rule(tmp_path, settings, lambda p: False) == {"stale": False}
    assert settings.values == before


def test_a_stale_rule_without_a_backup_is_reported_not_guessed(tmp_path):
    settings = Recorder.with_values(pac=OUR_PAC)
    out = cleanup_stale_rule(tmp_path, settings, lambda p: False)
    assert out == {"stale": True, "restored": False, "reason": "no_backup"}
    assert settings.writes == []


def test_a_failing_cleanup_is_reported(tmp_path, state):
    settings = stale_setup(tmp_path, state)
    settings.fail_restore = True
    out = cleanup_stale_rule(tmp_path, settings, lambda p: False)
    assert out["stale"] is True and out["restored"] is False and out["reason"] == "backend_failed"


# ---------------------------------------------------------------------------------- the gateway routes
@pytest.fixture()
def gateway(tmp_path):
    st = GatewayState(home=tmp_path, default_mode="off", images_policy="withhold")
    client = httpx.Client(http2=False, verify=False, timeout=httpx.Timeout(10.0, read=None))
    gw = Gateway(st, port=0, upstream="http://127.0.0.1:9", log=GatewayLog(None), client=client)
    gw.start()
    yield gw
    gw.shutdown()


def post(gw: Gateway, route: str, body: Any, token: bool = True):
    headers = {"content-type": "application/json"}
    if token:
        headers[TOKEN_HEADER] = gw.state.token or ""
    return httpx.post(f"{gw.url}/__gateway/{route}", json=body, headers=headers, timeout=10)


def test_the_lifecycle_routes_are_token_routes():
    assert LIFECYCLE_ROUTES == {("POST", "/__gateway/attach"), ("POST", "/__gateway/detach")}
    assert LIFECYCLE_ROUTES <= TOKEN_ROUTES


def test_attach_needs_the_token_and_a_routed_gateway(gateway):
    assert post(gateway, "attach", {"pid": 1}, token=False).status_code == 403
    off = post(gateway, "attach", {"pid": 1})
    assert off.status_code == 409 and off.json() == {"ok": False, "error": "routing_off"}
    assert post(gateway, "detach", {"pid": 1}).status_code == 409


def test_attach_and_detach_over_http(gateway):
    probe = FakeProbe(4242)
    gateway.lifecycle = Lifecycle(gateway.state, probe=probe)
    ok = post(gateway, "attach", {"pid": 4242})
    assert ok.status_code == 200 and ok.json() == {"ok": True, "attached": 1}
    assert gateway.state.stats()["mcp_attached"] == 1
    assert post(gateway, "attach", {"pid": 99}).status_code == 400  # no such process
    assert post(gateway, "attach", {"pid": "x"}).json() == {"ok": False, "error": "bad_pid"}
    assert post(gateway, "detach", {"pid": 4242}).json() == {"ok": True, "attached": 0}


def test_the_pac_is_served_only_while_routing_is_on(gateway):
    url = f"{gateway.url}/proxy.pac"
    assert httpx.get(url, timeout=10).status_code == 404  # routing off: no PAC
    gateway.state.pac_provider = lambda: "function FindProxyForURL(u, h) { return 'DIRECT'; }\n"
    reply = httpx.get(url, timeout=10)
    assert reply.status_code == 200
    assert reply.headers["content-type"] == "application/x-ns-proxy-autoconfig"
    assert (
        "no-cache" in reply.headers["cache-control"]
        and "no-store" in reply.headers["cache-control"]
    )
    assert reply.text.startswith("function FindProxyForURL")
    head = httpx.head(url, timeout=10)
    assert head.status_code == 200 and head.content == b""
    assert int(head.headers["content-length"]) == len(reply.content)
    assert gateway.state.stats()["pac_served"] == 2


def test_the_pac_is_refused_for_a_foreign_host_header(gateway):
    gateway.state.pac_provider = lambda: "x"
    reply = httpx.get(f"{gateway.url}/proxy.pac", headers={"host": "evil.example"}, timeout=10)
    assert reply.status_code == 404


def test_a_pac_provider_that_fails_is_a_fixed_500(gateway):
    def boom() -> str:
        raise RuntimeError("secret detail")

    gateway.state.pac_provider = boom
    reply = httpx.get(f"{gateway.url}/proxy.pac", timeout=10)
    assert reply.status_code == 500 and "secret" not in reply.text
    assert gateway.state.stats()["errors"] == 1


def test_the_status_carries_the_routing_view_and_a_warning_while_it_is_inactive(gateway, state):
    status = gateway.state.status()
    assert status["routing"] == {"wanted": False} and not any(
        w.startswith("routing_") for w in status["warnings"]
    )
    gateway.state.routing_status = lambda: {
        "wanted": True,
        "enabled": False,
        "reason": "ca_missing",
    }
    status = gateway.state.status()
    assert status["routing"]["reason"] == "ca_missing"
    assert "routing_inactive:ca_missing" in status["warnings"]
    gateway.state.routing_status = lambda: {"wanted": True, "enabled": True, "reason": ""}
    assert not any(w.startswith("routing_") for w in gateway.state.status()["warnings"])
    gateway.state.routing_status = lambda: 1 / 0
    assert gateway.state.status()["routing"] == {"wanted": True, "error": True}


def test_proxy_errors_are_the_fixed_codes_of_the_module():
    assert str(ProxyError("locked")) == "locked"
    assert life_mod.WATCHDOG_S == 2.0 and life_mod.PAC_PATH == "/proxy.pac"
