"""03_Batch_Operations apply_changeset dispatch + gating (Phase 5).

preview routes straight through (read-only, no gate); apply gates DESTRUCTIVE
(CATASTROPHIC when >10 deletes) before delegating.
"""

import pytest
from unittest.mock import MagicMock

import server.handlers.batch_operations_handler as boh


@pytest.fixture(autouse=True)
def _gate_off(monkeypatch):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "OFF")


def test_preview_routes_to_apply_changeset(monkeypatch):
    spy = MagicMock(return_value={"success": True, "preview": True})
    monkeypatch.setattr(boh._batch_ops_handler, "apply_changeset", spy)

    res = boh.handle_batch_operations(
        {"object_type": "changeset", "preview": True, "changeset": {"tables": {"create": [{}]}}}
    )
    assert res["preview"] is True
    spy.assert_called_once()


def test_apply_routes_and_gates(monkeypatch):
    spy = MagicMock(return_value={"success": True, "applied": 1})
    monkeypatch.setattr(boh._batch_ops_handler, "apply_changeset", spy)

    res = boh.handle_batch_operations(
        {"changeset": {"tables": {"create": [{"table": "T"}]}}, "plan_hash": "H"}
    )
    assert res["success"] is True
    spy.assert_called_once()


def test_apply_with_many_deletes_blocks_under_strict(monkeypatch):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")
    spy = MagicMock(return_value={"success": True})
    monkeypatch.setattr(boh._batch_ops_handler, "apply_changeset", spy)

    doc = {"tables": {"delete": [{"table": f"T{i}"} for i in range(11)]}}
    res = boh.handle_batch_operations({"changeset": doc, "plan_hash": "H"})
    # CATASTROPHIC (>10 deletes) blocked without confirm_destructive.
    assert res.get("success") is False
    spy.assert_not_called()
