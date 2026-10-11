"""T1-A: tests for the additive 01_Connection:ensure op.

ensure is the idempotent connect: it skips work when already connected and
healthy, and falls through to picking the best instance (PID > file_path >
prev_file > model_index) when not.

2026-09-22 (B-03/B-12): the health probe is connection_state.probe_connection_health
(a server round-trip, never the result cache) and the chosen instance dict is
connected with cm.connect_instance(instance) — no second detection.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from server.handlers import connection_handler
from server.handlers.connection_handler import handle_ensure


def _setup_state(monkeypatch: pytest.MonkeyPatch, *, connected: bool, probe_ok: bool):
    """Stub connection_state for the duration of one test."""
    fake_state = MagicMock()
    fake_state.is_connected.return_value = connected
    fake_state.get_pbip_info.return_value = {"file_full_path": "C:\\old\\Proj.pbip"}

    fake_state.probe_connection_health.return_value = {"success": probe_ok}

    fake_cm = MagicMock()
    fake_state.connection_manager = fake_cm
    monkeypatch.setattr(connection_handler, "connection_state", fake_state)
    return fake_state, fake_cm


def test_ensure_reuses_healthy_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_state, fake_cm = _setup_state(monkeypatch, connected=True, probe_ok=True)
    res = handle_ensure({})
    assert res["success"] is True
    assert res["reused"] is True
    # No detect/connect call when reusing.
    fake_cm.detect_instances.assert_not_called()
    fake_cm.connect_instance.assert_not_called()


def test_ensure_reconnects_when_probe_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_state, fake_cm = _setup_state(monkeypatch, connected=True, probe_ok=False)
    fake_cm.detect_instances.return_value = [
        {"pid": 100, "file_full_path": "C:\\new\\A.pbip"},
        {"pid": 200, "file_full_path": "C:\\new\\B.pbip"},
    ]
    fake_cm.connect_instance.return_value = {"success": True, "connection_string": "localhost:5555"}
    res = handle_ensure({"file_path": "C:\\new\\B.pbip"})
    assert res["success"] is True
    assert res["reused"] is False
    fake_cm.connect_instance.assert_called_once_with(fake_cm.detect_instances.return_value[1])


def test_ensure_picks_prefer_pid_over_file_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_state, fake_cm = _setup_state(monkeypatch, connected=False, probe_ok=False)
    fake_cm.detect_instances.return_value = [
        {"pid": 100, "file_full_path": "C:\\X.pbip"},
        {"pid": 200, "file_full_path": "C:\\Y.pbip"},
    ]
    fake_cm.connect_instance.return_value = {"success": True}
    handle_ensure({"prefer_pid": 200, "file_path": "C:\\X.pbip"})
    fake_cm.connect_instance.assert_called_once_with(fake_cm.detect_instances.return_value[1])


def test_ensure_uses_prev_file_when_no_explicit_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_state, fake_cm = _setup_state(monkeypatch, connected=False, probe_ok=False)
    fake_state.get_pbip_info.return_value = {"file_full_path": "C:\\Sticky.pbip"}
    fake_cm.detect_instances.return_value = [
        {"pid": 100, "file_full_path": "C:\\Other.pbip"},
        {"pid": 200, "file_full_path": "C:\\Sticky.pbip"},
    ]
    fake_cm.connect_instance.return_value = {"success": True}
    handle_ensure({})
    fake_cm.connect_instance.assert_called_once_with(fake_cm.detect_instances.return_value[1])


def test_ensure_falls_back_to_model_index(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_state, fake_cm = _setup_state(monkeypatch, connected=False, probe_ok=False)
    fake_state.get_pbip_info.return_value = None  # no prev file
    fake_cm.detect_instances.return_value = [
        {"pid": 100, "file_full_path": "C:\\A.pbip"},
        {"pid": 200, "file_full_path": "C:\\B.pbip"},
    ]
    fake_cm.connect_instance.return_value = {"success": True}
    handle_ensure({"model_index": 1})
    fake_cm.connect_instance.assert_called_once_with(fake_cm.detect_instances.return_value[1])


def test_ensure_no_instances(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_state, fake_cm = _setup_state(monkeypatch, connected=False, probe_ok=False)
    fake_cm.detect_instances.return_value = []
    res = handle_ensure({})
    assert res["success"] is False
    assert res["error_type"] == "no_instances"


def test_ensure_invalid_model_index(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_state, fake_cm = _setup_state(monkeypatch, connected=False, probe_ok=False)
    fake_state.get_pbip_info.return_value = None
    fake_cm.detect_instances.return_value = [{"pid": 100, "file_full_path": "C:\\A.pbip"}]
    res = handle_ensure({"model_index": 5})
    assert res["success"] is False
    assert res["error_type"] == "invalid_model_index"


def test_ensure_switches_when_prefer_pid_names_another_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live 2026-09-23: ensure prefer_pid=<other> returned reused=True on the OLD instance."""
    fake_state, fake_cm = _setup_state(monkeypatch, connected=True, probe_ok=True)
    fake_state.get_connection_identity.return_value = {"port": 62357}
    fake_state.is_connected.side_effect = [True, False]
    fake_cm.detect_instances.return_value = [
        {"pid": 40840, "port": 54952, "file_full_path": "C:\\sandbox\\A.pbip"},
        {"pid": 46764, "port": 62357, "file_full_path": "C:\real\\B.pbip"},
    ]
    fake_cm.connect_instance.return_value = {"success": True}
    res = handle_ensure({"prefer_pid": 40840})
    fake_state.cleanup.assert_called_once()
    fake_cm.connect_instance.assert_called_once_with(fake_cm.detect_instances.return_value[0])
    assert res.get("reused") is False


def test_ensure_reuses_when_prefer_pid_is_the_current_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_state, fake_cm = _setup_state(monkeypatch, connected=True, probe_ok=True)
    fake_state.get_connection_identity.return_value = {"port": 62357}
    fake_cm.detect_instances.return_value = [
        {"pid": 40840, "port": 54952},
        {"pid": 46764, "port": 62357},
    ]
    res = handle_ensure({"prefer_pid": 46764})
    assert res["reused"] is True
    fake_state.cleanup.assert_not_called()
    fake_cm.connect_instance.assert_not_called()
