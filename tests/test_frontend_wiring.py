"""Wiring of the TLS front end (phase 3a): ``serve --frontend-port`` and ``lab.py start
--frontend-port``. The front end is OFF unless a port is given. Nothing here starts a listener or a
process: the front end, the gateway and the lab spawn are replaced."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from client_gateway import cli
from client_gateway import server as server_mod
from client_gateway.frontend import runner as runner_mod
from tools.masking_lab import lab


@pytest.fixture(autouse=True)
def _no_real_spawn(monkeypatch):
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("a real subprocess was started")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(cli, "_lower_priority", lambda: None)


# ---------------------------------------------------------------------------------- serve
class FakeGateway:
    instances: list["FakeGateway"] = []

    def __init__(self, state: Any, *, port: int, on_shutdown: Any = None, **kw: Any) -> None:
        self.state, self.port, self.on_shutdown = state, port, on_shutdown
        FakeGateway.instances.append(self)

    def serve_forever(self) -> None:
        assert self.on_shutdown is not None
        self.on_shutdown()  # what Gateway._cleanup does when it stops

    def shutdown(self) -> None:  # pragma: no cover
        pass


class FakeFrontEnd:
    made: list["FakeFrontEnd"] = []
    fail: str | None = None

    def __init__(self, state: Any, port: int, **kw: Any) -> None:
        self.port, self.events = port, []
        FakeFrontEnd.made.append(self)

    def start(self) -> None:
        if FakeFrontEnd.fail:
            raise runner_mod.FrontEndError(FakeFrontEnd.fail)
        self.events.append("start")

    def stop(self) -> None:
        self.events.append("stop")


@pytest.fixture()
def fakes(monkeypatch):
    FakeGateway.instances.clear()
    FakeFrontEnd.made.clear()
    FakeFrontEnd.fail = None
    monkeypatch.setattr(server_mod, "Gateway", FakeGateway)
    monkeypatch.setattr(runner_mod, "FrontEnd", FakeFrontEnd)
    return FakeFrontEnd


def test_without_a_frontend_port_no_front_end_exists(fakes):
    assert cli.serve(18990, provider=False) == 0
    assert fakes.made == [] and len(FakeGateway.instances) == 1


def test_a_frontend_port_starts_the_front_end_before_the_gateway_and_stops_it_with_it(fakes):
    assert cli.serve(18990, provider=False, frontend_port=18991) == 0
    (front,) = fakes.made
    assert front.port == 18991 and front.events == ["start", "stop"]


def test_the_front_end_port_must_differ_from_the_gateway_port(fakes, capsys):
    assert cli.serve(18990, provider=False, frontend_port=18990) == 2
    assert fakes.made == [] and FakeGateway.instances == []
    assert "must differ" in capsys.readouterr().out


@pytest.mark.parametrize("code", ["port_in_use", "mitmproxy_unavailable", "start_failed"])
def test_a_front_end_that_cannot_start_refuses_the_whole_start(fakes, capsys, code):
    fakes.fail = code
    assert cli.serve(18990, provider=False, frontend_port=18991) == 2
    assert FakeGateway.instances == []  # the gateway never came up half-protected
    assert code in capsys.readouterr().out


def test_the_cli_parses_frontend_port_only_for_serve(monkeypatch):
    seen: dict[str, Any] = {}

    def fake_serve(*args: Any) -> int:
        seen["args"] = args
        return 0

    monkeypatch.setattr(cli, "serve", fake_serve)
    assert cli.main(["serve", "--port", "18990", "--frontend-port", "18991"]) == 0
    assert seen["args"][3] == 18991
    assert cli.main(["serve", "--port", "18990"]) == 0 and seen["args"][3] is None
    with pytest.raises(SystemExit):
        cli.main(["start", "--frontend-port", "18991"])


# ---------------------------------------------------------------------------------- lab
@pytest.fixture()
def lab_fakes(monkeypatch, tmp_path):
    calls: dict[str, Any] = {"spawn": [], "up": False, "status": {}}
    base = tmp_path / "localappdata"
    base.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(base))

    def spawn(argv: list[str], env: dict[str, str], cwd: Path, log_path: Path) -> int:
        calls["spawn"].append(argv)
        calls["up"] = True
        return 4242

    def http(port: int, method: str, path: str, token: str | None = None, timeout: float = 3.0):
        if not calls["up"]:
            return None
        if path == "/__gateway/health":
            return 200, {"ok": True, "port": port, "version": "test"}
        if path == "/__gateway/status":
            return 200, {"ok": True, **calls["status"]}
        return 404, {}

    monkeypatch.setattr(lab, "spawn_child", spawn)
    monkeypatch.setattr(lab, "http_call", http)
    monkeypatch.setattr(lab.time, "sleep", lambda _s: None)
    monkeypatch.setattr(lab, "configured_port", lambda: 8890)
    return calls


def test_lab_start_passes_the_frontend_port_through_and_records_it(lab_fakes, tmp_path):
    target = tmp_path / "lab"
    assert lab.main(["start", "--dir", str(target), "--frontend-port", "18899"]) == 0
    argv = lab_fakes["spawn"][0]
    assert argv[argv.index("--frontend-port") + 1] == "18899"
    assert "--no-provider" in argv  # R8 still holds
    assert json.loads((target / lab.LAB_FILE).read_text())["frontend_port"] == 18899


def test_lab_start_without_a_frontend_port_has_no_front_end(lab_fakes, tmp_path):
    assert lab.main(["start", "--dir", str(tmp_path / "lab")]) == 0
    assert "--frontend-port" not in lab_fakes["spawn"][0]
    assert "frontend_port" not in json.loads((tmp_path / "lab" / lab.LAB_FILE).read_text())


@pytest.mark.parametrize(
    "frontend_port, why",
    [
        (8890, "production gateway port"),
        (18890, "must differ from the lab gateway port"),
        (0, "not a valid TCP port"),
        (70000, "not a valid TCP port"),
    ],
)
def test_lab_refuses_these_front_end_ports(lab_fakes, tmp_path, capsys, frontend_port, why):
    target = tmp_path / "lab"
    code = lab.main(["start", "--dir", str(target), "--frontend-port", str(frontend_port)])
    assert code == 2 and why in capsys.readouterr().err
    assert lab_fakes["spawn"] == [] and not target.exists()  # nothing was created


def test_lab_refuses_the_configured_production_port_as_front_end_port(
    lab_fakes, tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(lab, "configured_port", lambda: 9123)
    code = lab.main(["start", "--dir", str(tmp_path / "lab"), "--frontend-port", "9123"])
    assert code == 2 and "configured production port" in capsys.readouterr().err


def test_lab_status_prints_the_spki_of_the_lab_ca(lab_fakes, tmp_path, capsys):
    target = tmp_path / "lab"
    assert lab.main(["start", "--dir", str(target), "--frontend-port", "18899"]) == 0
    capsys.readouterr()
    lab_fakes["status"] = {
        "frontend": {"enabled": True, "port": 18899, "running": True, "spki_sha256_b64": "AbC="}
    }
    assert lab.main(["status", "--dir", str(target)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["frontend"] == {"port": 18899, "spki_sha256_b64": "AbC=", "running": True}


def test_lab_status_without_a_front_end_has_no_front_end_block(lab_fakes, tmp_path, capsys):
    target = tmp_path / "lab"
    assert lab.main(["start", "--dir", str(target)]) == 0
    capsys.readouterr()
    lab_fakes["status"] = {"frontend": {"enabled": False}}
    assert lab.main(["status", "--dir", str(target)]) == 0
    assert "frontend" not in json.loads(capsys.readouterr().out)
