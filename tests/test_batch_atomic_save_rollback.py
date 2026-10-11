"""Regression: _run_atomic_batch must UndoLocalChanges when SaveChanges throws (P0-2).

When every item dispatches successfully but model.SaveChanges() itself raises
(e.g. AMO validation failure), the partial in-memory writes must be rolled
back via UndoLocalChanges() rather than left in an undefined state.
"""

from unittest.mock import MagicMock

import pytest

import core.operations.batch_operations as bo
from core.operations.batch_operations import BatchOperationsHandler


class _FakeModel:
    def __init__(self):
        self.undo_calls = 0
        self.save_calls = 0

    def SaveChanges(self):
        self.save_calls += 1
        raise RuntimeError("AMO validation failed")

    def UndoLocalChanges(self):
        self.undo_calls += 1


def test_save_failure_triggers_rollback(monkeypatch):
    model = _FakeModel()
    server = MagicMock()

    monkeypatch.setattr(
        bo,
        "get_server_db_model",
        lambda cs: (server, MagicMock(), model, None),
    )

    handler = BatchOperationsHandler()

    # dispatch_fn succeeds for every item, so `errors` stays empty and the
    # single SaveChanges() path runs — which is where the throw happens.
    def dispatch_fn(_model, _item):
        return {"success": True}

    with pytest.raises(RuntimeError):
        handler._run_atomic_batch(
            items=[{"name": "M1"}],
            options={"use_transaction": True, "_operation": "create"},
            dispatch_fn=dispatch_fn,
        )

    assert model.save_calls == 1
    assert model.undo_calls == 1, "SaveChanges failure must trigger UndoLocalChanges"
    server.Disconnect.assert_called_once()


def test_item_failure_still_rolls_back(monkeypatch):
    """Pre-existing behaviour must be preserved: an item-level failure under a
    transaction still rolls back and does NOT call SaveChanges."""
    model = _FakeModel()
    # Make SaveChanges a no-op spy here so we can assert it is never reached.
    model.SaveChanges = MagicMock()
    server = MagicMock()
    monkeypatch.setattr(
        bo,
        "get_server_db_model",
        lambda cs: (server, MagicMock(), model, None),
    )
    handler = BatchOperationsHandler()

    def dispatch_fn(_model, _item):
        return {"success": False, "error": "bad item"}

    handler._run_atomic_batch(
        items=[{"name": "M1"}],
        options={"use_transaction": True, "_operation": "create"},
        dispatch_fn=dispatch_fn,
    )
    assert model.undo_calls == 1
    model.SaveChanges.assert_not_called()
