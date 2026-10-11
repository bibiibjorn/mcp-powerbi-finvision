"""Dispatch + gating tests for 07_Report_Operations (report_operations_handler).

Focus: the CATASTROPHIC `restore` op is gated (blocked without
confirm_destructive), and dispatch routes to the right op. Gating goes through
gate_and_wrap → require_autonomous_mode; engines are imported lazily inside each
op, so we route via monkeypatching _OPS (routing) or set the gate policy for the
gate test. No disk, no CLR.
"""

from __future__ import annotations

import pytest

import server.handlers.report_operations_handler as mod
from core.autonomous import gating


@pytest.fixture(autouse=True)
def _reset_policy():
    gating.invalidate_policy_cache()
    yield
    gating.invalidate_policy_cache()


def test_missing_pbip_path_is_pure_error():
    out = mod.handle_report_operations({"operation": "info"})
    assert out["success"] is False
    assert "pbip_path is required" in out["error"]


def test_restore_blocked_without_confirm_under_strict(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")
    gating.invalidate_policy_cache()
    # Replace the op so, if the gate ever fails to block, we don't hit disk.
    monkeypatch.setitem(mod._OPS, "restore", lambda a: {"success": True, "restored": True})

    out = mod.handle_report_operations(
        {
            "operation": "restore",
            "pbip_path": str(tmp_path),
            "backup_path": str(tmp_path / "bk.zip"),
        }
    )
    assert out["success"] is False
    assert out["error_type"] == "confirm_destructive_required"
    assert out["blocked_op"] == "07_Report_Operations:restore"
    assert out["severity"] == "catastrophic"


def test_restore_passes_with_confirm(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")
    gating.invalidate_policy_cache()
    seen = {}

    def _fake_restore(a):
        seen["called"] = True
        return {"success": True, "restored": True}

    monkeypatch.setitem(mod._OPS, "restore", _fake_restore)

    out = mod.handle_report_operations(
        {
            "operation": "restore",
            "pbip_path": str(tmp_path),
            "backup_path": str(tmp_path / "bk.zip"),
            "confirm_destructive": True,
        }
    )
    assert out["success"] is True
    assert seen.get("called") is True


def test_read_only_info_routes_without_gate(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")
    gating.invalidate_policy_cache()
    monkeypatch.setitem(mod._OPS, "info", lambda a: {"success": True, "pages": []})
    out = mod.handle_report_operations({"operation": "info", "pbip_path": str(tmp_path)})
    # info is read-only, so STRICT does not block it.
    assert out["success"] is True


def test_restore_is_the_only_catastrophic_op():
    assert mod._CATASTROPHIC_REPORT_OPS == {"restore"}
