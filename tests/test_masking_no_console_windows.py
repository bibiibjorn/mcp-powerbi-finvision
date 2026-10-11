"""R10: no console window may ever appear when anything masking-related runs (phase 3b).

Every process the gateway, the MCP masking code, the lab and the hook start on Windows is windowless:
``pythonw.exe`` and / or ``CREATE_NO_WINDOW`` (plus ``DETACHED_PROCESS`` where the child outlives
its parent), never ``shell=True``, and always a ``stdin`` of its own (``DEVNULL`` or a private pipe,
never the MCP protocol pipe). One behavioural test per spawn site with ``subprocess.Popen`` replaced
by a recorder (nothing is started), and a static scan of the source so a NEW spawn site cannot
forget the flags.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
WINDOWS = os.name == "nt"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)

SCANNED = [
    *sorted((ROOT / "client_gateway").rglob("*.py")),
    *sorted((ROOT / "core" / "client_guard").rglob("*.py")),
    *sorted((ROOT / "tools" / "masking_lab").rglob("*.py")),
    ROOT / "server" / "masking_layer.py",
    ROOT / "server" / "handlers" / "client_mode_handler.py",
    ROOT / "hooks" / "client_masking_hook.py",
]
SPAWNERS = {"Popen", "run", "call", "check_call", "check_output"}
FORBIDDEN_OS = {"system", "startfile", "popen", "spawnl", "spawnv", "execv", "execl"}


class Recorder:
    """A stand-in for ``subprocess.Popen``: records the call, starts nothing."""

    calls: list[tuple[Any, dict[str, Any]]] = []

    def __init__(self, args: Any, **kw: Any) -> None:
        type(self).calls.append((args, kw))
        self.args = args
        self.pid = 4242
        self.returncode = 0
        self.stdin = None
        self.stdout = iter(['{"ready": true, "load_s": 0.1}\n'])
        self.stderr = None

    def __enter__(self) -> "Recorder":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def communicate(self, *a: Any, **k: Any) -> tuple[str, str]:
        return "", ""

    def poll(self) -> int:
        return 0

    def wait(self, *a: Any, **k: Any) -> int:
        return 0

    def kill(self) -> None:
        return None

    terminate = kill


@pytest.fixture()
def calls(monkeypatch):
    Recorder.calls = []
    monkeypatch.setattr(subprocess, "Popen", Recorder)
    return Recorder.calls


def exe_name(argv: Any) -> str:
    return Path(str(argv[0])).name.lower()


def pythonw_or_unavailable(argv: Any) -> bool:
    return (
        exe_name(argv) == "pythonw.exe"
        or not Path(sys.executable).with_name("pythonw.exe").is_file()
    )


def assert_windowless(argv: Any, kw: dict[str, Any], detached: bool = False) -> None:
    flags = kw.get("creationflags", 0)
    assert flags & NO_WINDOW, f"no CREATE_NO_WINDOW: {argv!r}"
    if detached:
        assert flags & DETACHED, f"not detached: {argv!r}"
    assert not kw.get("shell"), "shell=True opens cmd.exe"
    assert kw.get("stdin") is not None, "the child would inherit stdin"


pytestmark = pytest.mark.skipif(not WINDOWS, reason="console windows are a Windows matter")


# ---------------------------------------------------------------------------------- the sites
def test_control_client_gateway_start_is_pythonw_detached_and_hidden(calls):
    from client_gateway import control_client

    control_client.spawn_gateway()
    ((argv, kw),) = calls
    assert pythonw_or_unavailable(argv)
    assert_windowless(argv, kw, detached=True)
    assert kw["stdin"] is subprocess.DEVNULL


def test_control_client_gateway_end_helper_has_no_window(calls, monkeypatch):
    from client_gateway import control_client

    monkeypatch.setattr(control_client, "health", lambda *a, **k: None)
    getattr(control_client, "stop_" + "gateway")(timeout_s=1)
    assert calls, "the helper process was not started"
    for argv, kw in calls:
        assert_windowless(argv, kw)
        assert kw["stdin"] is subprocess.DEVNULL


def test_cli_spawn_is_pythonw_detached_and_hidden(calls):
    from client_gateway import cli

    cli.spawn(18890)
    ((argv, kw),) = calls
    assert pythonw_or_unavailable(argv)
    assert_windowless(argv, kw, detached=True)
    assert kw["stdin"] is subprocess.DEVNULL


def test_the_folder_git_probe_has_no_window(calls):
    from client_gateway import folders

    folders._git(Path("."), "status")
    ((argv, kw),) = calls
    assert_windowless(argv, kw)
    assert kw["stdin"] is subprocess.DEVNULL


def test_the_rapidocr_worker_has_no_window(calls):
    from client_gateway.images import _RapidClient

    _RapidClient()._start()
    ((argv, kw),) = calls
    assert_windowless(argv, kw)


def test_the_ner_worker_has_no_window(calls):
    from client_gateway.ner_worker import WorkerModel

    model = WorkerModel("some/model", load_timeout_s=5)
    assert model.error is None
    ((argv, kw),) = calls
    assert_windowless(argv, kw)


def test_the_lab_gateway_is_detached_and_hidden(calls, tmp_path):
    from tools.masking_lab import lab

    lab.spawn_child([sys.executable, "-c", "pass"], {}, tmp_path, tmp_path / "log.txt")
    ((argv, kw),) = calls
    assert_windowless(argv, kw, detached=True)
    assert kw["stdin"] is subprocess.DEVNULL


def test_the_selftest_child_is_pythonw_hidden_and_has_its_own_stdin(calls, monkeypatch):
    from server.handlers import client_mode_handler as handler

    class Quiet(Recorder):
        def communicate(self, *a: Any, **k: Any) -> tuple[bytes, None]:
            return b"", None

    monkeypatch.setattr(subprocess, "Popen", Quiet)
    handler.handle_client_mode({"operation": "selftest"})
    ((argv, kw),) = calls
    assert pythonw_or_unavailable(argv)
    assert_windowless(argv, kw)
    assert kw["stdin"] is subprocess.DEVNULL


def test_the_hook_gateway_start_is_pythonw_detached_and_hidden(calls):
    spec = importlib.util.spec_from_file_location(
        "client_masking_hook_r10", ROOT / "hooks" / "client_masking_hook.py"
    )
    assert spec is not None and spec.loader is not None
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    hook._spawn_gateway()
    ((argv, kw),) = calls
    assert pythonw_or_unavailable(argv)
    assert_windowless(argv, kw, detached=True)
    assert kw["stdin"] is subprocess.DEVNULL


# ---------------------------------------------------------------------------------- the scan
def _spawn_calls(path: Path) -> list[ast.Call]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            if (
                isinstance(owner, ast.Name)
                and owner.id == "subprocess"
                and node.func.attr in SPAWNERS
            ):
                out.append(node)
    return out


def test_the_scan_knows_the_files_and_finds_the_known_sites():
    assert all(p.is_file() for p in SCANNED)
    assert sum(len(_spawn_calls(p)) for p in SCANNED) >= 7


@pytest.mark.parametrize("path", SCANNED, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_spawn_in_the_source_is_windowless_and_has_its_own_stdin(path):
    for node in _spawn_calls(path):
        keywords = {k.arg: k.value for k in node.keywords if k.arg}
        where = f"{path.name}:{node.lineno}"
        assert "creationflags" in keywords, f"{where}: no creationflags (console window)"
        assert "stdin" in keywords, f"{where}: no stdin (AGENTS.md rule)"
        shell = keywords.get("shell")
        assert not (isinstance(shell, ast.Constant) and shell.value), f"{where}: shell=True"


@pytest.mark.parametrize("path", SCANNED, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_os_level_spawn_or_shell_open_in_the_source(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "os"
            and node.func.attr in FORBIDDEN_OS
        ):
            raise AssertionError(f"{path.name}:{node.lineno}: os.{node.func.attr} (console window)")
