"""Dispatch + gating tests for 07_Theme_Operations (theme_operations_handler).

Same architecture as report_operations: thin handler → gate_and_wrap → _OPS
dispatch, engines imported lazily. We route via _OPS monkeypatch and exercise
the CATASTROPHIC gate on set_colors. No disk, no CLR.
"""

from __future__ import annotations

import pytest

import server.handlers.theme_operations_handler as mod
from core.autonomous import gating


@pytest.fixture(autouse=True)
def _reset_policy():
    gating.invalidate_policy_cache()
    yield
    gating.invalidate_policy_cache()


def test_missing_pbip_path_is_pure_error():
    out = mod.handle_theme_operations({"operation": "get_theme"})
    assert out["success"] is False
    assert "pbip_path is required" in out["error"]


def test_catastrophic_set_colors_blocked_under_strict(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")
    gating.invalidate_policy_cache()
    monkeypatch.setitem(mod._OPS, "set_colors", lambda a: {"success": True})

    out = mod.handle_theme_operations(
        {"operation": "set_colors", "pbip_path": str(tmp_path), "colors": {"a": "#fff"}}
    )
    assert out["success"] is False
    assert out["error_type"] == "confirm_destructive_required"
    assert out["severity"] == "catastrophic"


def test_set_colors_passes_with_confirm(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")
    gating.invalidate_policy_cache()
    seen = {}

    def _fake(a):
        seen["ok"] = True
        return {"success": True}

    monkeypatch.setitem(mod._OPS, "set_colors", _fake)

    out = mod.handle_theme_operations(
        {
            "operation": "set_colors",
            "pbip_path": str(tmp_path),
            "colors": {"a": "#fff"},
            "confirm_destructive": True,
        }
    )
    assert out["success"] is True
    assert seen.get("ok") is True


def test_read_only_get_theme_routes(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")
    gating.invalidate_policy_cache()
    monkeypatch.setitem(mod._OPS, "get_theme", lambda a: {"success": True, "theme": {}})
    out = mod.handle_theme_operations({"operation": "get_theme", "pbip_path": str(tmp_path)})
    assert out["success"] is True


def test_catastrophic_op_set():
    assert mod._CATASTROPHIC_THEME_OPS == {
        "set_colors",
        "apply_theme",
        "import_theme",
        "clear_visual_overrides",
    }
