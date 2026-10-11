"""``00_Client_Mode`` ops ``setup`` and ``selftest`` (phase 4b.2).

``setup`` only RETURNS the command a human runs in a terminal (a certificate install must be a human
action) and the plan as far as it is known without touching the system; it never runs setup, never
builds a real backend. ``selftest`` replays the recorded wire fixtures offline in a windowless CHILD
process (its own temp masking home in the child's environment; this process's environment is never
modified).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from client_gateway import setup_cmd
from client_gateway.routing.guard import ENV as REAL_SYSTEM_ENV
from server.handlers import client_mode_handler as h
from server.op_classification import classify
from server.tool_schemas import TOOL_SCHEMAS


@pytest.fixture()
def paths(tmp_path: Path, monkeypatch) -> setup_cmd.SetupPaths:
    (tmp_path / "appdata" / "Claude").mkdir(parents=True)
    p = setup_cmd.SetupPaths(
        home=tmp_path / "home",
        config_file=tmp_path / "repo" / "config" / "local_config.json",
        repo_root=tmp_path / "repo",
        claude_desktop=tmp_path / "appdata" / "Claude" / "claude_desktop_config.json",
        vscode=tmp_path / "appdata" / "Code" / "User" / "mcp.json",
        claude_code=tmp_path / "user" / ".claude.json",
    )
    monkeypatch.setattr(setup_cmd, "default_paths", lambda repo_root=None: p)
    # folders.* reads these: the plan must never look at the real ~/.claude or masking home
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("FINVISION_MASKING_HOME", str(tmp_path / "home"))
    return p


def test_the_new_ops_are_in_the_schema_and_are_reads():
    enum = TOOL_SCHEMAS["client_mode"]["properties"]["operation"]["enum"]
    assert {"setup", "selftest"} <= set(enum) and tuple(enum) == h._VALID_OPS
    assert classify("00_Client_Mode", {"operation": "setup"}) == "read"
    assert classify("00_Client_Mode", {"operation": "selftest"}) == "read"


def test_setup_returns_the_command_for_the_user_and_runs_nothing(paths, monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("setup ran, or a backend was built, from the tool")

    for name in ("run", "main", "open_backends", "real_backends", "_apply_install"):
        monkeypatch.setattr(setup_cmd, name, forbidden)
    before = sorted(str(p) for p in tmp_path.rglob("*"))
    out = h.handle_client_mode({"operation": "setup"})
    assert out["success"] is True
    assert out["command"].endswith("-m client_gateway setup")
    assert "never run by the AI" in out["run_by"] and out["cwd"]
    assert out["then"]["apply"] == out["command"] + " --yes"
    assert out["then"]["undo"].endswith("setup --remove --yes")
    assert out["then"]["status"].endswith("setup --status")
    assert any("CurrentUser\\Root" in line for line in out["steps"])
    assert out["plan"][0].startswith("Finvision masking gateway - setup PLAN")
    assert any("masking on" in line for line in out["plan"])  # the default plan: on demand
    assert any("hooks:" in line for line in out["plan"])  # the Claude Code settings are read only
    assert sorted(str(p) for p in tmp_path.rglob("*")) == before  # and writes nothing
    assert REAL_SYSTEM_ENV not in os.environ


def test_setup_survives_a_plan_that_cannot_be_built(paths, monkeypatch):
    monkeypatch.setattr(
        setup_cmd, "read_facts", lambda *a, **k: (_ for _ in ()).throw(OSError("denied"))
    )
    out = h.handle_client_mode({"operation": "setup"})
    assert out["success"] is True and out["plan_unavailable"] == "OSError"
    assert out["steps"] and out["command"]


@pytest.mark.selftest
def test_selftest_replays_every_named_app_and_passes_on_the_shipped_code():
    before = dict(os.environ)
    out = h.handle_client_mode({"operation": "selftest"})
    assert out["success"] is True and out["passed"] is True and out["exit_code"] == 0, out
    assert [a["app"] for a in out["apps"]] == ["claude-code", "copilot", "chatgpt"]
    assert all(a["verdict"] == "PASS" and a["problems"] == [] for a in out["apps"])
    assert out["unrouted"] == []
    assert len(out["lines"]) == 3
    assert dict(os.environ) == before  # the child had its own environment, this one is unchanged


class FakeRun:
    """Replaces ``subprocess.run`` inside the handler: records the call, starts nothing."""

    def __init__(self, stdout: bytes = b"", returncode: int = 0, raises: Exception | None = None):
        self.stdout, self.returncode, self.raises = stdout, returncode, raises
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    def __call__(self, args: Any, **kw: Any) -> subprocess.CompletedProcess:
        self.calls.append((args, kw))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(args, self.returncode, self.stdout, None)


def fake_child(monkeypatch, **kw: Any) -> FakeRun:
    run = FakeRun(**kw)
    monkeypatch.setattr(h.subprocess, "run", run)
    return run


def test_selftest_summary_names_the_failed_fixture_and_never_a_value(monkeypatch):
    lines = [
        "claude-code  PASS  masked out 1/1  rehydrated back 1/1  leak scan 1/1",
        "copilot      FAIL  masked out 0/2  rehydrated back 1/1  leak scan 2/2",
        "  - copilot_title_stream: a client name is left in the masked request",
        "chatgpt      PASS  masked out 3/3  rehydrated back 2/2  leak scan 3/3",
        "unrouted fixture: new_thing (no named app serves its route)",
    ]
    fake_child(monkeypatch, stdout=("\n".join(lines) + "\n").encode(), returncode=1)
    out = h.handle_client_mode({"operation": "selftest"})
    assert out["success"] is True and out["passed"] is False and out["exit_code"] == 1
    copilot = out["apps"][1]
    assert copilot["app"] == "copilot" and copilot["verdict"] == "FAIL"
    assert copilot["problems"] == [
        "copilot_title_stream: a client name is left in the masked request"
    ]
    assert out["unrouted"] == ["unrouted fixture: new_thing (no named app serves its route)"]


def test_selftest_child_is_windowless_isolated_and_the_parent_environment_is_unchanged(
    monkeypatch,
):
    monkeypatch.setenv(REAL_SYSTEM_ENV, "1")  # a parent that holds the system permission ...
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:1")  # ... and is routed
    monkeypatch.setenv("FINVISION_MASKING_HOME", "PARENT_HOME_SENTINEL")
    monkeypatch.delenv("FINVISION_GATEWAY_NER", raising=False)
    before = dict(os.environ)
    run = fake_child(
        monkeypatch,
        stdout=b"claude-code  PASS  masked out 1/1  rehydrated back 1/1  leak scan 1/1\n",
    )
    homes: list[Path] = []
    original = h._selftest_env

    def spy(tmp: Path) -> dict[str, str]:
        homes.append(tmp)
        assert tmp.is_dir()
        return original(tmp)

    monkeypatch.setattr(h, "_selftest_env", spy)
    out = h.handle_client_mode({"operation": "selftest"})
    assert out["success"] is True and out["passed"] is True
    ((argv, kw),) = run.calls
    assert argv[1:] == ["-m", "tools.masking_lab.selftest"]
    assert kw["cwd"] == str(h.gw.repo_root())
    assert kw["stdin"] is subprocess.DEVNULL and not kw.get("shell")
    assert kw["timeout"] == h.SELFTEST_TIMEOUT_S == 60
    if os.name == "nt":
        assert kw["creationflags"] & subprocess.CREATE_NO_WINDOW
    env = kw["env"]
    assert env["FINVISION_MASKING_HOME"] == str(homes[0] / "home") != "PARENT_HOME_SENTINEL"
    assert env["FINVISION_GATEWAY_NER"] == "0" and env["CLAUDE_CONFIG_DIR"].startswith(
        str(homes[0])
    )
    assert REAL_SYSTEM_ENV not in env and "ANTHROPIC_BASE_URL" not in env
    assert dict(os.environ) == before  # THIS process's environment was never patched
    assert not homes[0].exists()  # the temp home is gone again


def test_selftest_that_crashes_is_an_error_envelope(monkeypatch):
    fake_child(monkeypatch, raises=ValueError("secret value"))
    out = h.handle_client_mode({"operation": "selftest"})
    assert out["error_type"] == "selftest_failed" and "ValueError" in out["error"]
    assert "secret value" not in str(out)


def test_selftest_timeout_is_a_clear_error_envelope(monkeypatch):
    fake_child(monkeypatch, raises=subprocess.TimeoutExpired(["x"], 60))
    out = h.handle_client_mode({"operation": "selftest"})
    assert out["success"] is False and out["error_type"] == "selftest_timeout"
    assert "60 s" in out["error"] and "stopped" in out["error"]


def test_selftest_child_that_prints_nothing_is_an_error_envelope(monkeypatch):
    fake_child(monkeypatch, stdout=b"", returncode=3)
    out = h.handle_client_mode({"operation": "selftest"})
    assert out["error_type"] == "selftest_failed" and "exit code 3" in out["error"]


def test_selftest_does_not_import_the_selftest_module_in_this_process():
    import inspect

    source = inspect.getsource(h._selftest)
    assert "run_selftest" not in source and "mock.patch" not in source
