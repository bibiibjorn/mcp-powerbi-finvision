"""03_Batch_Operations:apply_changeset preview + apply (Phase 5).

preview: validate + compute plan_hash + summarize the ordered op sequence.
apply: re-validate, require a matching plan_hash (reject stale), then apply the
dependency-ordered ops against the model under one transaction (SaveChanges
once; UndoLocalChanges on any failure).
"""

from unittest.mock import MagicMock, patch

import pytest

import core.operations.batch_operations as bo
from core.operations.batch_operations import BatchOperationsHandler


@pytest.fixture(autouse=True)
def _connected(monkeypatch):
    """apply_changeset guards on connection_state.is_connected() — stub it True."""
    monkeypatch.setattr(bo.connection_state, "is_connected", lambda: True)


def _doc():
    return {
        "columns": {
            "create": [{"table": "Sales", "name": "Margin", "expression": "[Rev]-[Cost]"}],
            "delete": [{"table": "Sales", "name": "Old"}],
        },
        "tables": {"create": [{"table": "New"}]},
    }


# --- preview -----------------------------------------------------------------


def test_preview_returns_plan_hash_and_op_counts(monkeypatch):
    handler = BatchOperationsHandler()
    model = MagicMock()
    server = MagicMock()
    monkeypatch.setattr(bo, "get_server_db_model", lambda cs: (server, MagicMock(), model, None))
    monkeypatch.setattr(bo, "compute_plan_hash", lambda m: "HASH123")

    res = handler.apply_changeset({"changeset": _doc(), "preview": True})
    assert res["success"] is True
    assert res["preview"] is True
    assert res["plan_hash"] == "HASH123"
    assert res["operations"]["create"] == 2  # 1 column + 1 table
    assert res["operations"]["delete"] == 1
    server.Disconnect.assert_called_once()


def test_preview_rejects_invalid_changeset(monkeypatch):
    handler = BatchOperationsHandler()
    res = handler.apply_changeset({"changeset": {"widgets": {"create": [{}]}}, "preview": True})
    assert res["success"] is False
    assert res["error_type"] == "invalid_changeset"


# --- apply -------------------------------------------------------------------


def test_apply_requires_matching_plan_hash(monkeypatch):
    handler = BatchOperationsHandler()
    model = MagicMock()
    server = MagicMock()
    monkeypatch.setattr(bo, "get_server_db_model", lambda cs: (server, MagicMock(), model, None))
    monkeypatch.setattr(bo, "compute_plan_hash", lambda m: "CURRENT")

    res = handler.apply_changeset({"changeset": _doc(), "plan_hash": "STALE"})
    assert res["success"] is False
    assert res["error_type"] == "stale_plan"
    model.SaveChanges.assert_not_called()


def test_apply_runs_ops_in_order_and_saves_once(monkeypatch):
    handler = BatchOperationsHandler()
    model = MagicMock()
    server = MagicMock()
    monkeypatch.setattr(bo, "get_server_db_model", lambda cs: (server, MagicMock(), model, None))
    monkeypatch.setattr(bo, "compute_plan_hash", lambda m: "MATCH")

    calls = []

    def _spy(op_label):
        def _fn(model, item):
            calls.append(op_label)
            return {"success": True}

        return _fn

    # Patch the changeset dispatch to record order without touching managers.
    with patch.object(
        handler,
        "_changeset_apply_one",
        side_effect=lambda model, ot, op, item: calls.append(f"{ot}:{op}") or {"success": True},
    ):
        res = handler.apply_changeset({"changeset": _doc(), "plan_hash": "MATCH"})

    assert res["success"] is True
    assert res["applied"] == 3
    # Dependency-safe: tables create -> columns create -> columns delete
    assert calls == ["tables:create", "columns:create", "columns:delete"]
    model.SaveChanges.assert_called_once()


def test_apply_rolls_back_on_failure(monkeypatch):
    handler = BatchOperationsHandler()
    model = MagicMock()
    server = MagicMock()
    monkeypatch.setattr(bo, "get_server_db_model", lambda cs: (server, MagicMock(), model, None))
    monkeypatch.setattr(bo, "compute_plan_hash", lambda m: "MATCH")

    def _one(model, ot, op, item):
        if ot == "columns" and op == "create":
            return {"success": False, "error": "bad column"}
        return {"success": True}

    with patch.object(handler, "_changeset_apply_one", side_effect=_one):
        res = handler.apply_changeset({"changeset": _doc(), "plan_hash": "MATCH"})

    assert res["success"] is False
    model.UndoLocalChanges.assert_called_once()
    model.SaveChanges.assert_not_called()
