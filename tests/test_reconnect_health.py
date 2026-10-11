"""H3: tests for the post-reconnect health gate in 01_Connection:reconnect.

handle_reconnect runs a trivial CATALOGS DMV probe after re-acquiring the
connection. Previously it always returned success:True regardless of the
probe outcome. These tests pin the contract that a failed probe yields
success:False / error_type:connection_unhealthy, while a healthy probe
still returns success:True with health_ok:True.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from server.handlers import connection_handler
from server.handlers.connection_handler import handle_reconnect


def _setup_state(monkeypatch: pytest.MonkeyPatch, *, probe_result):
    """Stub connection_state so handle_reconnect reaches the health block.

    cm.connect succeeds; detect_instances returns one fake instance;
    connection_state.probe_connection_health returns ``probe_result``.
    """
    fake_state = MagicMock()
    fake_state.get_pbip_info.return_value = {"file_full_path": "C:\\old\\Proj.pbip"}

    # B-03: the probe is a server round-trip that never touches the result cache.
    fake_state.probe_connection_health.return_value = probe_result

    fake_cm = MagicMock()
    # get_connection used to derive prev_port — keep it simple/no port.
    fake_cm.get_connection.return_value = None
    fake_cm.detect_instances.return_value = [
        {"pid": 100, "file_full_path": "C:\\old\\Proj.pbip"},
    ]
    fake_cm.connect_instance.return_value = {
        "success": True,
        "connection_string": "Data Source=localhost:5000;...",
    }
    fake_state.connection_manager = fake_cm

    monkeypatch.setattr(connection_handler, "connection_state", fake_state)
    return fake_state, fake_cm


def test_reconnect_failed_health_probe_reports_unhealthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_state(
        monkeypatch,
        probe_result={"success": False, "error": "probe failed"},
    )
    res = handle_reconnect({})
    assert res["success"] is False
    assert res["error_type"] == "connection_unhealthy"
    assert "probe failed" in res["health_error"]
    # Port fields are preserved so the caller can still see what happened.
    assert res["new_port"] == "5000"
    assert res["reconnected"] is True


def test_reconnect_healthy_probe_reports_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_state(monkeypatch, probe_result={"success": True})
    res = handle_reconnect({})
    assert res["success"] is True
    assert res["health_ok"] is True
    assert res["health_error"] is None
    assert res["new_port"] == "5000"
