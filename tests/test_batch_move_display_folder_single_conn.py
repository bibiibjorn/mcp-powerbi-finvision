"""Regression: batch move_display_folder uses ONE AMO connection (P1 perf).

Previously each item called dax_injector.upsert_measure, opening a fresh AMO
connection per item. The batch now routes through _run_atomic_batch with a
no-connect _apply_set_display_folder primitive: one connect, one SaveChanges.
"""

from unittest.mock import MagicMock

import core.operations.batch_operations as bo
from core.operations.batch_operations import BatchOperationsHandler


class _Measure:
    def __init__(self, name):
        self.Name = name
        self.DisplayFolder = ""


class _Table:
    def __init__(self, name, measures):
        self.Name = name
        self.Measures = measures


class _Model:
    def __init__(self, tables):
        self.Tables = tables
        self.save_calls = 0

    def SaveChanges(self):
        self.save_calls += 1

    def UndoLocalChanges(self):  # pragma: no cover
        pass


def test_move_display_folder_single_connection(monkeypatch):
    measures = [_Measure("M1"), _Measure("M2"), _Measure("M3")]
    model = _Model([_Table("Sales", measures)])
    server = MagicMock()

    connect_calls = {"n": 0}

    def fake_get(cs):
        connect_calls["n"] += 1
        return (server, MagicMock(), model, None)

    monkeypatch.setattr(bo, "get_server_db_model", fake_get)

    # Provide a real measure_crud_manager with the apply helper.
    from core.operations.measure_crud_manager import MeasureCRUDManager

    crud = MeasureCRUDManager.__new__(MeasureCRUDManager)
    cs = MagicMock()
    cs.measure_crud_manager = crud
    monkeypatch.setattr(bo, "connection_state", cs)

    handler = BatchOperationsHandler()
    args = {
        "batch_operation": "move_display_folder",
        "items": [
            {"table": "Sales", "measure": "M1", "display_folder": "F"},
            {"table": "Sales", "measure": "M2", "display_folder": "F"},
            {"table": "Sales", "measure": "M3", "display_folder": "F"},
        ],
        "options": {"use_transaction": True},
    }
    result = handler._batch_measures(args)

    assert result["success"] is True, result
    assert connect_calls["n"] == 1, f"expected ONE AMO connect, got {connect_calls['n']}"
    assert model.save_calls == 1, f"expected ONE SaveChanges, got {model.save_calls}"
    assert all(m.DisplayFolder == "F" for m in measures)
