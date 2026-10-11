"""Tests for the destructive-op gate (core.autonomous.gating).

Autonomous mode was removed in v7.8: the gate now decides purely on policy +
confirm_destructive. There is no longer an _is_mode_active() escape hatch.
"""

from core.autonomous.gating import (
    Severity,
    require_autonomous_mode,
    autonomous_gated,
)


def test_policy_off_passes_catastrophic(monkeypatch):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "OFF")
    result = require_autonomous_mode("test:delete", {}, Severity.CATASTROPHIC)
    assert result is None


def test_policy_light_blocks_catastrophic(monkeypatch):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "LIGHT")
    result = require_autonomous_mode("test:delete", {}, Severity.CATASTROPHIC)
    assert result is not None
    assert result["success"] is False
    assert result["error_type"] == "confirm_destructive_required"
    assert result["severity"] == "catastrophic"
    assert "confirm_destructive" in result["hint"]
    assert "enter_mode" not in result["hint"]


def test_policy_light_passes_destructive_with_notice(monkeypatch):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "LIGHT")
    result = require_autonomous_mode("test:update", {}, Severity.DESTRUCTIVE)
    # Advisory: returns a dict with gate_notice but NOT success=False
    assert result is not None
    assert "gate_notice" in result
    assert result.get("success") is not False


def test_confirm_destructive_bypasses_light(monkeypatch):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "LIGHT")
    result = require_autonomous_mode(
        "test:delete", {"confirm_destructive": True}, Severity.CATASTROPHIC
    )
    assert result is None


def test_confirm_destructive_bypasses_strict(monkeypatch):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")
    result = require_autonomous_mode(
        "test:update", {"confirm_destructive": True}, Severity.DESTRUCTIVE
    )
    assert result is None


def test_policy_strict_blocks_destructive(monkeypatch):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")
    result = require_autonomous_mode("test:update", {}, Severity.DESTRUCTIVE)
    assert result["success"] is False
    assert result["error_type"] == "confirm_destructive_required"


def test_policy_warn_never_blocks(monkeypatch):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "WARN")
    cat = require_autonomous_mode("t:d", {}, Severity.CATASTROPHIC)
    dest = require_autonomous_mode("t:d", {}, Severity.DESTRUCTIVE)
    assert "gate_warning" in cat
    assert "gate_warning" in dest


def test_decorator_blocks_handler(monkeypatch):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")

    @autonomous_gated("test_tool:delete", Severity.DESTRUCTIVE)
    def handler(args):
        return {"success": True, "side_effect": "mutation"}

    result = handler({})
    assert result["success"] is False
    assert result["error_type"] == "confirm_destructive_required"


def test_decorator_passes_handler_with_confirm(monkeypatch):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")

    @autonomous_gated("test_tool:delete", Severity.DESTRUCTIVE)
    def handler(args):
        return {"success": True}

    assert handler({"confirm_destructive": True})["success"] is True


def test_decorator_merges_warning_on_warn_policy(monkeypatch):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "WARN")

    @autonomous_gated("test_tool:delete", Severity.CATASTROPHIC)
    def handler(args):
        return {"success": True}

    result = handler({})
    assert result["success"] is True
    assert "gate_warning" in result


def test_batch_delete_over_10_blocks_under_light(monkeypatch):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "LIGHT")
    from server.handlers.batch_operations_handler import handle_batch_operations

    args = {
        "operation": "measures",
        "batch_operation": "delete",
        "items": [{"name": f"m{i}"} for i in range(15)],
    }
    result = handle_batch_operations(args)
    assert result["success"] is False
    assert result["error_type"] in ("confirm_destructive_required", "confirmation_required")
