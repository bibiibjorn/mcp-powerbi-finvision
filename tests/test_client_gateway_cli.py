"""Tests of client_gateway.cli: start / stop / status / mode with a real detached process."""

from __future__ import annotations

import json
import socket
import time

import pytest

from client_gateway import __version__, cli
from client_gateway.state import read_token


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    port = free_port()
    monkeypatch.setenv("FINVISION_MASKING_HOME", str(tmp_path))
    monkeypatch.setenv("FINVISION_GATEWAY_PORT", str(port))
    monkeypatch.setenv("FINVISION_GATEWAY_PROVIDER", "0")
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    yield port
    cli.stop(port)  # never leave a gateway behind


def test_resolve_port_precedence(monkeypatch):
    monkeypatch.delenv("FINVISION_GATEWAY_PORT", raising=False)
    monkeypatch.setattr(cli, "cfg", lambda key, default: 9123 if key.endswith("port") else default)
    assert cli.resolve_port(7000) == 7000
    assert cli.resolve_port(None) == 9123
    monkeypatch.setenv("FINVISION_GATEWAY_PORT", "9555")
    assert cli.resolve_port(None) == 9555
    assert cli.resolve_port(7000) == 7000
    monkeypatch.setenv("FINVISION_GATEWAY_PORT", "junk")
    assert cli.resolve_port(None) == 9123
    monkeypatch.setattr(cli, "cfg", lambda key, default: "nope")
    monkeypatch.delenv("FINVISION_GATEWAY_PORT")
    assert cli.resolve_port(None) == 8890


def test_status_and_stop_when_nothing_runs(env, capsys):
    assert cli.main(["status"]) == 1
    assert cli.main(["stop"]) == 0
    assert "not running" in capsys.readouterr().out


def test_serve_on_a_busy_port_returns_2(env):
    with socket.socket() as s:
        s.bind(("127.0.0.1", env))
        s.listen(1)
        assert cli.serve(env, provider=False) == 2


def test_start_is_idempotent_status_mode_and_stop(env, tmp_path, capsys):
    port = env
    assert cli.main(["start"]) == 0
    info = cli.health(port)
    assert info is not None and info["version"] == __version__
    pid = info["pid"]
    lock = json.loads((tmp_path / "gateway.lock").read_text())
    assert lock["pid"] == pid and lock["port"] == port and lock["version"] == __version__
    # second start: same process, no new one
    capsys.readouterr()
    assert cli.main(["start"]) == 0
    assert "already running" in capsys.readouterr().out
    assert cli.health(port)["pid"] == pid
    # status prints counts
    assert cli.main(["status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["version"] == __version__ and status["mode"] == "on"  # conftest pins on
    # mode: off for a session needs (and gets) the token from the home
    assert read_token(tmp_path) is not None
    assert cli.main(["mode", "off", "--session", "sess-1"]) == 0
    reply = cli.call(port, "GET", "/__gateway/session?id=sess-1")
    assert reply is not None and reply[1]["mode"] == "off"
    assert cli.main(["mode", "on", "--session", "sess-1"]) == 0
    assert cli.call(port, "GET", "/__gateway/session?id=sess-1")[1]["mode"] == "on"
    # stop
    assert cli.main(["stop"]) == 0
    assert cli.health(port) is None
    deadline = time.time() + 5
    while (tmp_path / "gateway.lock").exists() and time.time() < deadline:
        time.sleep(0.1)
    assert not (tmp_path / "gateway.lock").exists()


def test_a_running_gateway_of_another_version_is_left_alone_without_replace(
    env, monkeypatch, capsys
):
    """2026-10-02: replacing the live gateway took every routed chat down. A plain start never
    touches a running gateway; only the user's ``start --replace`` does."""
    port = env
    assert cli.start(port) == 0
    old_pid = cli.health(port)["pid"]
    monkeypatch.setattr(cli, "__version__", "0.0.0-newer-than-running")
    capsys.readouterr()
    assert cli.start(port) == 0
    assert "Not replacing it" in capsys.readouterr().out
    assert cli.health(port)["pid"] == old_pid
    assert cli.main(["start", "--replace"]) == 0
    new = cli.health(port)
    assert new is not None and new["pid"] != old_pid


def test_mode_without_token_file_cannot_switch_off(env, tmp_path):
    port = env
    assert cli.start(port) == 0
    token = (tmp_path / "gateway.token").read_text(encoding="ascii").strip()
    (tmp_path / "gateway.token").unlink()  # the CLI cannot read it any more
    assert cli.mode(port, "off", "sess-x") == 1
    reply = cli.call(port, "GET", "/__gateway/session?id=sess-x", headers={cli.TOKEN_HEADER: token})
    assert reply[1]["mode"] == "on"


def test_start_on_another_port_stops_the_first_gateway(env):
    first = env
    second = free_port()
    assert cli.start(first) == 0
    assert cli.start(second) == 0  # without --replace: the running one is left alone
    assert cli.health(first) is not None and cli.health(second) is None
    assert cli.start(second, replace=True) == 0
    try:
        assert cli.health(second) is not None
        assert cli.health(first) is None  # one gateway per machine
    finally:
        cli.stop(second)


def test_global_off_needs_an_explicit_all(env, tmp_path, capsys):
    port = env
    assert cli.start(port) == 0
    capsys.readouterr()
    # no session id anywhere (the environment variable is unset): refused, nothing switched
    assert cli.mode(port, "off", None) == 2
    assert cli.mode(port, "off", "") == 2
    assert cli.main(["mode", "off"]) == 2
    assert "EVERY session" in capsys.readouterr().out
    assert cli.call(port, "GET", "/__gateway/session?id=")[1]["mode"] == "on"
    assert cli.call(port, "GET", "/__gateway/status")[1]["mode"] == "on"
    # explicit: --all
    assert cli.main(["mode", "off", "--all"]) == 0
    assert cli.call(port, "GET", "/__gateway/status")[1]["mode"] == "off"
    assert cli.main(["mode", "on", "--all"]) == 0
    assert cli.call(port, "GET", "/__gateway/status")[1]["mode"] == "on"


# 2026-10-07: ``start --replace`` hung on a gateway that stopped answering while it shut down
# (health timed out, the process still held the port); the user had to Ctrl+C a traceback.
@pytest.fixture()
def hung_gateway(env, tmp_path):
    import subprocess
    import sys

    # a process that looks like a gateway (its command line names client_gateway) and never answers
    proc = subprocess.Popen(  # noqa: S603 - fixed argv
        [sys.executable, "-c", "import time; time.sleep(120)", "client_gateway", "serve"],
        stdin=subprocess.DEVNULL,
    )
    (tmp_path / cli.LOCK_FILE).write_text(
        json.dumps({"pid": proc.pid, "port": env, "version": __version__}), encoding="ascii"
    )
    yield env, proc
    proc.kill()
    proc.wait(5)


def test_stop_ends_a_gateway_process_that_no_longer_answers(hung_gateway, tmp_path, monkeypatch):
    port, proc = hung_gateway
    monkeypatch.setattr(cli, "KILL_WAIT_S", 5.0)
    assert cli.health(port) is None
    assert cli.stop(port, wait_s=0.5) == 0
    assert proc.wait(5) is not None  # the process is really gone
    assert not (tmp_path / cli.LOCK_FILE).exists()


def test_start_without_replace_never_ends_a_gateway_that_does_not_answer(
    hung_gateway, capsys, monkeypatch
):
    port, proc = hung_gateway
    monkeypatch.setattr(cli, "STARTING_GRACE_S", 0.0)  # an old, hung gateway
    assert cli.start(port) == 1
    assert proc.poll() is None  # left alone
    assert "start --replace" in capsys.readouterr().out


def test_a_pid_that_is_not_a_gateway_is_never_waited_on_or_ended():
    import os

    assert cli._is_gateway_process(os.getpid()) is False
    assert cli._is_gateway_process(None) is False
    assert cli._is_gateway_process(-1) is False


def test_start_and_stop_survive_ctrl_c(monkeypatch, capsys):
    def _interrupted(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "stop", _interrupted)
    assert cli.main(["stop", "--port", "1"]) == 130
    assert "interrupted" in capsys.readouterr().out


def test_a_gateway_that_is_still_starting_is_waited_for_never_ended(hung_gateway, monkeypatch):
    # 2026-10-07: a large dictionary kept a new gateway busy for over 10 s; the next
    # start --replace took it for a hung one and ended it, so the gateway never came up
    port, proc = hung_gateway
    waited = []
    monkeypatch.setattr(
        cli, "_wait_until_answers", lambda p, s, pid=None: waited.append(pid) or {"version": "x"}
    )
    assert cli.start(port, replace=True) == 0
    assert waited == [proc.pid]
    assert proc.poll() is None  # not ended
