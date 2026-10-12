"""Wiring of the routing into ``serve`` and into the MCP's start (phase 3b.5): off by default, never builds a
real backend under test, and with fakes enables the rule only after the front end and the gateway are up
and takes it out again on shutdown. The gateway, the front end, the certificate store and the Internet
Settings are fakes; no real process, no system setting (rule R8)."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest

from client_gateway import cli, control_client
from client_gateway import server as server_mod
from client_gateway.frontend import runner as runner_mod
from client_gateway.routing import backends as backends_mod
from client_gateway.routing.backends import Backends
from client_gateway.routing.ca import CAStore
from client_gateway.routing.certstore import CAInstaller, MemoryCertStore
from client_gateway.routing.guard import ENV as REAL_SYSTEM_ENV
from client_gateway.routing.guard import RealSystemForbidden
from client_gateway.routing.winproxy import REG_DWORD, REG_SZ, MemoryInternetSettings, RegValue
from client_gateway.state import masking_home
from tests._routing_fakes import fake_crypto

PORT, FPORT = 18990, 18991
PAC_URL = f"http://127.0.0.1:{PORT}/proxy.pac"


class FakeGateway:
    instances: list["FakeGateway"] = []
    release = threading.Event()

    def __init__(self, state: Any, *, port: int, on_shutdown: Any = None, **kw: Any) -> None:
        self.state, self.port, self.on_shutdown = state, port, on_shutdown
        self.lifecycle: Any = None
        FakeGateway.instances.append(self)

    def serve_forever(self) -> None:
        FakeGateway.release.wait(10)
        assert self.on_shutdown is not None
        self.on_shutdown()  # what Gateway._cleanup does when it stops

    def shutdown(self) -> None:
        FakeGateway.release.set()


class FakeFrontEnd:
    made: list["FakeFrontEnd"] = []
    fail: str | None = None

    def __init__(self, state: Any, port: int, **kw: Any) -> None:
        self.port, self.kw, self.events, self.running = port, kw, [], False
        FakeFrontEnd.made.append(self)

    def start(self) -> None:
        if FakeFrontEnd.fail:
            raise runner_mod.FrontEndError(FakeFrontEnd.fail)
        self.running = True
        self.events.append("start")

    def stop(self) -> None:
        self.running = False
        self.events.append("stop")


class Recorder(MemoryInternetSettings):
    def __init__(self, *a: Any, **k: Any) -> None:
        super().__init__(*a, **k)
        self.events: list[str] = []

    def apply_pac(self, url: str, disable_autodetect: bool = False) -> None:
        self.events.append("apply_pac")
        super().apply_pac(url, disable_autodetect)

    def restore(self, saved: Any) -> None:
        self.events.append("restore")
        super().restore(saved)


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("a real subprocess was started")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(cli, "_lower_priority", lambda: None)
    FakeGateway.instances.clear()
    FakeGateway.release = threading.Event()
    FakeFrontEnd.made.clear()
    FakeFrontEnd.fail = None
    monkeypatch.setattr(server_mod, "Gateway", FakeGateway)
    monkeypatch.setattr(runner_mod, "FrontEnd", FakeFrontEnd)
    monkeypatch.setattr(cli, "health", lambda port, timeout=1.0: {"ok": True})
    yield
    FakeGateway.release.set()


def config(monkeypatch, **values: Any) -> None:
    real = cli.cfg

    def fake(key: str, default: Any = None) -> Any:
        return values.get(key.rsplit(".", 1)[-1], real(key, default))

    monkeypatch.setattr(cli, "cfg", fake)


def prepared(home: Path, *, trust: bool = True) -> Backends:
    """A CA made in ``home`` (a fake for DPAPI) and, when ``trust``, installed in a fake store."""
    certs = MemoryCertStore()
    ca = CAStore(home, crypto=fake_crypto()).ensure()
    if trust:
        CAInstaller(home, certs).install(ca)
    return Backends(
        certs, Recorder.with_values(enable=1, server="proxy.corp:8080", detect=0), fake_crypto()
    )


def serve_in_thread(**kw: Any) -> tuple[threading.Thread, dict[str, Any]]:
    result: dict[str, Any] = {}

    def run() -> None:
        result["code"] = cli.serve(PORT, provider=False, **kw)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, result


def wait_for(predicate: Any, seconds: float = 10.0) -> bool:
    stop = threading.Event()
    for _ in range(int(seconds / 0.01)):
        if predicate():
            return True
        stop.wait(0.01)
    return False


# ---------------------------------------------------------------------------------- off by default
def test_routing_is_off_by_default_and_the_default_config_says_so():
    import json

    shipped = json.loads(
        (Path(__file__).resolve().parent.parent / "config" / "default_config.json").read_text(
            encoding="utf-8"
        )
    )
    assert shipped["client_gateway"]["routing"] is False
    assert cli.routing_wanted() is False and control_client.routing_wanted() is False


def test_with_routing_off_no_backend_front_end_or_lifecycle_exists(monkeypatch):
    def forbidden() -> None:
        raise AssertionError("a backend was built with routing off")

    monkeypatch.setattr(backends_mod, "real_backends", forbidden)
    FakeGateway.release.set()
    assert cli.serve(PORT, provider=False) == 0
    (gw,) = FakeGateway.instances
    assert gw.lifecycle is None and FakeFrontEnd.made == []
    assert gw.state.routing_status is None and gw.state.pac_provider is None
    assert gw.state.status()["routing"] == {"wanted": False}


# ---------------------------------------------------------------------------------- refused
def test_routing_on_under_test_cannot_build_a_real_backend_and_says_so():
    FakeGateway.release.set()
    assert cli.serve(PORT, provider=False, routing=True) == 0  # no backends given: the real ones
    (gw,) = FakeGateway.instances
    assert FakeFrontEnd.made == [] and gw.lifecycle is not None  # the watchdog (R1) still runs
    routing = gw.state.status()["routing"]
    assert routing["wanted"] is True and routing["enabled"] is False
    assert routing["reason"] == "real_system_forbidden"
    assert "routing_inactive:real_system_forbidden" in gw.state.status()["warnings"]
    # the guard refused: a plain RealSystemForbidden, never a half-built backend
    with pytest.raises(RealSystemForbidden):
        backends_mod.real_backends()


def test_the_front_end_port_must_differ_from_the_gateway_port(monkeypatch, tmp_path):
    config(monkeypatch, frontend_port=PORT)
    FakeGateway.release.set()
    assert cli.serve(PORT, provider=False, routing=True, backends=prepared(masking_home())) == 0
    assert FakeFrontEnd.made == []


@pytest.mark.parametrize("what", ["no_ca", "untrusted"])
def test_a_ca_that_is_missing_or_not_trusted_starts_no_front_end_and_enables_nothing(what):
    home = masking_home()
    if what == "no_ca":
        backends = Backends(MemoryCertStore(), Recorder.with_values(), fake_crypto())
    else:
        backends = prepared(home, trust=False)
    FakeGateway.release.set()
    assert cli.serve(PORT, provider=False, routing=True, backends=backends) == 0
    (gw,) = FakeGateway.instances
    assert FakeFrontEnd.made == [] and backends.settings.writes == []
    reason = {"no_ca": "ca_missing", "untrusted": "ca_not_trusted"}[what]
    assert gw.state.status()["routing"]["reason"] == reason
    assert f"routing_inactive:{reason}" in gw.state.status()["warnings"]


def test_a_front_end_that_cannot_start_leaves_the_gateway_up_and_the_rule_unset():
    FakeFrontEnd.fail = "port_in_use"
    backends = prepared(masking_home())
    FakeGateway.release.set()
    assert cli.serve(PORT, provider=False, routing=True, backends=backends) == 0
    (gw,) = FakeGateway.instances
    assert backends.settings.writes == []
    assert gw.state.status()["routing"]["reason"] == "frontend_port_in_use"


# ---------------------------------------------------------------------------------- routing on
def test_routing_is_enabled_after_the_front_end_and_removed_at_shutdown():
    home = masking_home()
    backends = prepared(home)
    before = dict(backends.settings.values)
    thread, result = serve_in_thread(routing=True, backends=backends)
    try:
        assert wait_for(lambda: backends.settings.values["AutoConfigURL"] is not None)
        (front,) = FakeFrontEnd.made
        (gw,) = FakeGateway.instances
        assert front.port == 8891 and front.events == ["start"]  # the configured front-end port
        assert backends.settings.values["AutoConfigURL"] == RegValue(REG_SZ, PAC_URL)
        assert "PROXY 127.0.0.1:8891" in gw.state.pac_provider()
        assert gw.lifecycle is not None and gw.state.routing_status()["enabled"] is True
        assert gw.state.routing_status()["attached"] == 0
        # the front end got the in-memory CA, the trust bundle (public certificates) and the chooser
        assert (
            front.kw["trust_bundle"].name == "upstream-ca.pem"
            and front.kw["trust_bundle"].is_file()
        )
        assert front.kw["upstream_proxy"]("chatgpt.com", 443, "https") == (
            "http",
            ("proxy.corp", 8080),
        )
        store = front.kw["cert_store"]()
        assert store.default_ca.to_cryptography() == CAStore(home, crypto=fake_crypto()).load().cert
        assert not (home / "frontend").exists()  # no lab CA, no plain key
    finally:
        FakeGateway.release.set()
        thread.join(10)
    assert result["code"] == 0
    assert backends.settings.values == before  # restored exactly
    assert backends.settings.events[-1] == "restore" and front.events == ["start", "stop"]
    assert gw.state.pac_provider is None and not (home / "routing" / "proxy_backup.json").exists()


def test_the_wpad_probe_of_the_backends_reaches_the_routing_control():
    from client_gateway.routing.wpad import MemoryWpadProbe

    home = masking_home()
    base = prepared(home)
    settings = Recorder.with_values(detect=1)  # Windows' default: "Automatically detect settings"
    before = dict(settings.values)
    probe = MemoryWpadProbe("none")
    backends = Backends(base.certs, settings, fake_crypto(), None, probe)
    thread, result = serve_in_thread(routing=True, backends=backends)
    try:
        assert wait_for(lambda: settings.values["AutoConfigURL"] is not None)
        (gw,) = FakeGateway.instances
        assert settings.values["AutoDetect"] == RegValue(REG_DWORD, 0)
        assert gw.state.routing_status()["enabled"] is True and probe.calls == 1
    finally:
        FakeGateway.release.set()
        thread.join(10)
    assert result["code"] == 0 and settings.values == before  # AutoDetect=1 restored exactly


def test_a_crash_leftover_is_cleaned_before_anything_else(monkeypatch):
    home = masking_home()
    backends = prepared(home)
    settings = backends.settings
    original = dict(settings.values)
    from client_gateway.routing.winproxy import InternetSettings

    InternetSettings(home, settings).enable(PAC_URL)  # a previous gateway set the rule, then died
    assert settings.values["AutoConfigURL"] is not None
    asked: list[int] = []

    def nobody(port: int, timeout: float = 1.0) -> None:
        asked.append(port)
        return None

    monkeypatch.setattr(cli, "health", nobody)
    FakeFrontEnd.fail = "port_in_use"  # so the new gateway does not route again
    FakeGateway.release.set()
    assert cli.serve(PORT, provider=False, routing=True, backends=backends) == 0
    assert asked and asked[0] == PORT  # the rule's own port was probed first
    assert settings.values == original  # and the user's settings are back


# ---------------------------------------------------------------------------------- the processes
class Recorded(Exception):
    pass


def recording_popen(monkeypatch):
    calls: list[dict[str, Any]] = []

    class P:
        def __init__(self, args: Any, **kw: Any) -> None:
            calls.append({"args": args, **kw})

    monkeypatch.setattr(subprocess, "Popen", P)
    return calls


def test_a_gateway_started_with_routing_off_gets_no_permission_to_touch_the_system(monkeypatch):
    calls = recording_popen(monkeypatch)
    cli.spawn(PORT)
    control_client.spawn_gateway()
    assert len(calls) == 2
    for call in calls:
        assert REAL_SYSTEM_ENV not in call["env"]


def test_with_routing_on_only_the_child_gets_the_permission(monkeypatch):
    calls = recording_popen(monkeypatch)
    config(monkeypatch, routing=True)
    monkeypatch.setattr(control_client, "routing_wanted", lambda: True)
    cli.spawn(PORT)
    control_client.spawn_gateway()
    assert all(call["env"][REAL_SYSTEM_ENV] == "1" for call in calls) and len(calls) == 2
    import os

    assert REAL_SYSTEM_ENV not in os.environ  # this process (a test) never got it


def test_the_mcp_attaches_only_when_routing_is_on(monkeypatch):
    import atexit

    monkeypatch.setattr(control_client, "_skip_autostart", lambda: False)
    monkeypatch.setattr(control_client, "env_routed", lambda: False)
    monkeypatch.setattr(
        control_client, "start_gateway", lambda *a, **k: {"running": True, "started": True}
    )
    attached: list[Any] = []
    registered: list[Any] = []
    monkeypatch.setattr(control_client, "attach_pid", lambda pid=None: attached.append(pid) or True)
    monkeypatch.setattr(atexit, "register", lambda fn, *a: registered.append(fn))
    import client_gateway.folders as folders_mod

    monkeypatch.setattr(folders_mod, "routes_all", lambda: False)
    monkeypatch.setattr(folders_mod, "list_folders", lambda: [])
    monkeypatch.setattr(control_client, "routing_wanted", lambda: False)
    assert control_client.ensure_gateway_background() is None and attached == []  # nothing new
    monkeypatch.setattr(control_client, "routing_wanted", lambda: True)
    thread = control_client.ensure_gateway_background()
    assert thread is not None
    thread.join(5)
    assert attached == [None] and registered == [control_client.detach_pid]


def test_an_mcp_whose_gateway_has_no_routing_attaches_to_nothing_and_registers_nothing(monkeypatch):
    import atexit

    monkeypatch.setattr(control_client, "_skip_autostart", lambda: False)
    monkeypatch.setattr(control_client, "routing_wanted", lambda: True)
    monkeypatch.setattr(
        control_client, "start_gateway", lambda *a, **k: {"running": True, "started": False}
    )
    monkeypatch.setattr(control_client, "attach_pid", lambda pid=None: False)  # 409: routing off
    registered: list[Any] = []
    monkeypatch.setattr(atexit, "register", lambda fn, *a: registered.append(fn))
    thread = control_client.ensure_gateway_background()
    assert thread is not None
    thread.join(5)
    assert registered == []


def test_attach_and_detach_post_this_process_id(monkeypatch):
    import os

    sent: list[tuple[str, str, Any]] = []

    def fake_request(method: str, path: str, body: Any = None, **kw: Any):
        sent.append((method, path, body))
        return 200, {"ok": True}

    monkeypatch.setattr(control_client, "request", fake_request)
    assert control_client.attach_pid() and control_client.detach_pid(7)
    assert sent == [
        ("POST", "/__gateway/attach", {"pid": os.getpid()}),
        ("POST", "/__gateway/detach", {"pid": 7}),
    ]
    monkeypatch.setattr(control_client, "request", lambda *a, **k: (409, {"ok": False}))
    assert control_client.attach_pid() is False
    monkeypatch.setattr(control_client, "request", lambda *a, **k: None)
    assert control_client.attach_pid() is False  # an unreachable gateway is False, never an error


def test_the_attach_routes_send_the_control_token():
    assert any(p.startswith("/__gateway/attach") for p in control_client._TOKEN_PATHS)
    assert any(p.startswith("/__gateway/detach") for p in control_client._TOKEN_PATHS)
