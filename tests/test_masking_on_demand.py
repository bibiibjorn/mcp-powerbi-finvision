"""Masking on demand (owner rule 2026-10-08): the typed "masking on" starts the gateway and routes
the chat's folder, "masking off" unroutes it, and the gateway stops by itself once nothing is
routed to it (``client_gateway.switch``, ``client_gateway.reaper``). A fake gateway stands in for
the real one; nothing here touches the developer's settings or the live gateway."""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from client_gateway import folders, reaper, switch
from tests.test_client_gateway_hook import TOKEN, FakeGateway

SID = "chat-1"


@pytest.fixture()
def gw(tmp_path, monkeypatch):
    gateway = FakeGateway().start()
    home = tmp_path / "home"
    home.mkdir()
    (home / "gateway.token").write_text(TOKEN, encoding="utf-8")
    monkeypatch.setenv("FINVISION_MASKING_HOME", str(home))
    monkeypatch.setenv("FINVISION_GATEWAY_PORT", str(gateway.port))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    claude = tmp_path / "claude-config"
    claude.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    gateway.default = "off"
    yield gateway
    gateway.stop()


def _env(folder: Path) -> dict:
    data = json.loads((folder / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    return data.get("env") or {}


def _project(tmp_path: Path, name: str = "proj") -> Path:
    folder = tmp_path / name
    folder.mkdir()
    return folder


# ------------------------------------------------------------------------------ switch
def test_masking_on_switches_the_chat_on_and_routes_its_folder(tmp_path, gw):
    proj = _project(tmp_path)
    out = switch.masking_on(SID, proj, wait_s=1)
    assert out["ok"] is True and out["folder"] == str(proj)
    assert gw.modes[SID] == "on"
    assert _env(proj)["ANTHROPIC_BASE_URL"] == f"http://127.0.0.1:{gw.port}"
    assert folders.anything_routed() is True


def test_masking_on_without_a_running_gateway_changes_nothing(tmp_path, gw, monkeypatch):
    from client_gateway import control_client

    proj = _project(tmp_path)
    monkeypatch.setattr(
        control_client, "start_gateway", lambda wait_s=0: {"running": False, "error": "x"}
    )
    out = switch.masking_on(SID, proj, wait_s=0)
    assert out == {"ok": False, "error": "gateway_not_started", "detail": "x"}
    assert not (proj / ".claude" / "settings.local.json").exists()  # never routed to a dead port
    assert SID not in gw.modes


def test_masking_on_warns_when_the_settings_file_is_tracked_by_git(tmp_path, gw, monkeypatch):
    import subprocess

    empty = tmp_path / "empty.gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    proj = _project(tmp_path)
    (proj / ".claude").mkdir()
    (proj / ".claude" / "settings.local.json").write_text("{}", encoding="utf-8")

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(proj), "-c", "user.name=t", "-c", "user.email=t@t", *args],
            check=True,
            capture_output=True,
        )

    git("init", "-q")
    git("add", "-f", ".claude/settings.local.json")
    git("commit", "-q", "-m", "x")
    out = switch.masking_on(SID, proj, wait_s=1)
    assert out["ok"] is True and "tracked by git" in out["warning"]


def test_masking_on_needs_a_session(tmp_path, gw):
    assert switch.masking_on("", _project(tmp_path))["error"] == "no_session"


def test_masking_off_switches_every_chat_of_the_folder_off_and_sends_it_direct(tmp_path, gw):
    proj = _project(tmp_path)
    switch.masking_on(SID, proj, wait_s=1)
    switch.masking_on("chat-2", proj, wait_s=1)
    out = switch.masking_off(SID, proj)
    assert out["ok"] is True and out["chats_switched_off"] == 2
    assert gw.modes == {SID: "off", "chat-2": "off"}
    assert _env(proj)["ANTHROPIC_BASE_URL"] == folders.DIRECT_URL  # written, never deleted
    assert out["gateway_stops"] is True and folders.anything_routed() is False


def test_masking_off_keeps_the_gateway_for_another_folder(tmp_path, gw):
    a, b = _project(tmp_path, "a"), _project(tmp_path, "b")
    switch.masking_on(SID, a, wait_s=1)
    switch.masking_on("chat-2", b, wait_s=1)
    out = switch.masking_off(SID, a)
    assert out["gateway_stops"] is False and out["other_folders_routed"] == [str(b)]
    assert gw.modes["chat-2"] == "on"


def test_masking_off_from_a_subfolder_finds_the_routed_project(tmp_path, gw):
    proj = _project(tmp_path)
    sub = proj / "src"
    sub.mkdir()
    switch.masking_on(SID, proj, wait_s=1)
    out = switch.masking_off(SID, sub)
    assert out["folder"] == str(proj) and out["unrouted"] is True


def test_the_project_root_wins_over_a_subfolder_cwd(tmp_path, monkeypatch):
    proj = _project(tmp_path)
    (proj / "src").mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
    assert switch.project_folder(proj / "src") == proj
    other = _project(tmp_path, "other")
    assert switch.project_folder(other) == other  # a cwd outside the project: the cwd


def test_status_reports_the_chat_and_the_gateway(tmp_path, gw):
    proj = _project(tmp_path)
    assert switch.chat_status(SID, proj) == {
        "gateway_running": True,
        "mode": "off",
        "folder_routed": False,
        "counts": {},
    }
    switch.masking_on(SID, proj, wait_s=1)
    assert switch.chat_status(SID, proj)["mode"] == "on"


def test_status_shows_degraded_masking_of_this_gateway_run(tmp_path, gw):
    # best practice: a fail-open step (name recognition too slow) must be visible to the user
    gw.stats.update(
        masked_requests=7, blocked=1, residual_leak_blocked=1, ner_partial=2, requests=9
    )
    counts = switch.chat_status(SID, _project(tmp_path))["counts"]
    assert counts == {"masked_requests": 7, "blocked": 2, "ner_partial": 2}


# ------------------------------------------------------------------------------ reaper
class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _gateway(clock: _Clock) -> SimpleNamespace:
    stopped: list[int] = []
    log = SimpleNamespace(event=lambda *a, **k: None)
    return SimpleNamespace(
        in_flight=0,
        last_request=clock.now,
        log=log,
        stopped=stopped,
        shutdown=lambda: stopped.append(1),
    )


def test_the_gateway_stops_once_nothing_is_routed_and_it_is_idle():
    clock = _Clock()
    gw = _gateway(clock)
    used = {"value": True}
    r = reaper.Reaper(gw, lambda: used["value"], grace_s=20, clock=clock)
    clock.now += 60
    assert r.tick() is False  # a folder is still routed
    used["value"] = False
    assert r.tick() is False  # unused from now on
    clock.now += 10
    assert r.tick() is False  # not for long enough
    clock.now += 11
    assert r.tick() is True and gw.stopped == [1]


def test_a_running_or_recent_request_keeps_it_alive():
    clock = _Clock()
    gw = _gateway(clock)
    r = reaper.Reaper(gw, lambda: False, grace_s=20, clock=clock)
    gw.in_flight = 1
    for _ in range(5):
        clock.now += 30
        assert r.tick() is False
    gw.in_flight = 0
    gw.last_request = clock.now
    clock.now += 5
    assert r.tick() is False  # a session that has not yet followed its settings change
    clock.now += 30
    r.tick()
    clock.now += 21
    assert r.tick() is True


def test_an_error_in_the_check_keeps_it_running():
    clock = _Clock()
    gw = _gateway(clock)
    r = reaper.Reaper(gw, lambda: 1 / 0, grace_s=1, clock=clock)
    for _ in range(5):
        clock.now += 10
        assert r.tick() is False


def test_the_reaper_can_be_switched_off(monkeypatch):
    monkeypatch.setenv(reaper.KEEP_ENV, "1")
    assert reaper.wanted(lambda key, default: True) is False
    monkeypatch.delenv(reaper.KEEP_ENV)
    assert reaper.wanted(lambda key, default: default) is True
    assert reaper.wanted(lambda key, default: False) is False


def test_a_real_gateway_server_is_stopped_by_the_reaper(tmp_path):
    from client_gateway.server import Gateway
    from client_gateway.state import GatewayState

    state = GatewayState(home=tmp_path, default_mode="off", images_policy="withhold")
    gateway = Gateway(state, port=0, upstream="http://127.0.0.1:9")
    gateway.start()
    r = reaper.Reaper(gateway, lambda: False, grace_s=0.2, every_s=0.05)
    gateway.last_request = time.monotonic() - 10
    r.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not gateway._closed.is_set():
        time.sleep(0.05)
    assert gateway._closed.is_set()


def test_the_terminal_command_does_what_the_typed_one_does(tmp_path, gw, capsys):
    from client_gateway import cli

    proj = _project(tmp_path)
    assert cli.main(["masking", "on", "--folder", str(proj), "--session", SID]) == 0
    assert gw.modes[SID] == "on"
    assert cli.main(["masking", "status", "--folder", str(proj), "--session", SID]) == 0
    assert cli.main(["masking", "off", "--folder", str(proj)]) == 0  # no session needed
    assert gw.modes[SID] == "off"
    assert _env(proj)["ANTHROPIC_BASE_URL"] == folders.DIRECT_URL
    assert '"gateway_stops": true' in capsys.readouterr().out
