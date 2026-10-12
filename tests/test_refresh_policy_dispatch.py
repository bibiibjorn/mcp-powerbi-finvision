"""02_Model_Operations table refresh_policy dispatch + severity (Phase 5).

Confirms the three ops are routed to the table handler, exposed in
valid_operations, and that get_refresh_policy is read-only-gated while
set/clear are destructive.
"""

import pytest
from unittest.mock import MagicMock

import server.handlers.model_operations_handler as moh
from server.handlers.model_operations_handler import _severity_for, _READ_ONLY_OPS
from core.autonomous.gating import Severity


@pytest.fixture(autouse=True)
def _gate_off(monkeypatch):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "OFF")


def test_refresh_policy_ops_in_valid_operations():
    ops = moh._valid_operations["table"]
    assert "get_refresh_policy" in ops
    assert "set_refresh_policy" in ops
    assert "clear_refresh_policy" in ops


def test_get_refresh_policy_is_read_only():
    assert "get_refresh_policy" in _READ_ONLY_OPS
    assert _severity_for("table", "get_refresh_policy", {}) is None


def test_set_clear_refresh_policy_are_destructive():
    assert _severity_for("table", "set_refresh_policy", {}) == Severity.DESTRUCTIVE
    assert _severity_for("table", "clear_refresh_policy", {}) == Severity.DESTRUCTIVE


def test_set_refresh_policy_routes_to_table_handler(monkeypatch):
    fake = MagicMock()
    fake.execute.return_value = {"success": True, "via": "execute"}
    monkeypatch.setitem(moh._handlers, "table", fake)

    args = {
        "object_type": "table",
        "operation": "set_refresh_policy",
        "table_name": "Sales",
        "rolling_window_periods": 5,
    }
    result = moh.handle_model_operations(args)
    assert result["success"] is True
    fake.execute.assert_called_once_with(args)
