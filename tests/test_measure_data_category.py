"""Verify `data_category` flows from MeasureOperationsHandler create/update
arguments all the way onto the TOM measure object.

C-12 moved measure create/update off ``dax_injector.upsert_measure`` (an
upsert) onto ``MeasureCRUDManager.create_measure`` / ``update_measure`` built
on ``_apply_upsert_measure``; these tests drive the real manager against a
PascalCase fake model so the property write itself is asserted.
"""

from __future__ import annotations

import core.operations.measure_crud_manager as mcm
from core.operations.measure_crud_manager import MeasureCRUDManager
from core.operations.measure_operations import MeasureOperationsHandler


class _Measure:
    def __init__(self):
        self.Name = ""
        self.Expression = ""
        self.DataCategory = None
        self.FormatStringDefinition = None


class _Coll(list):
    def Add(self, item):
        self.append(item)


class _Table:
    def __init__(self, name, measures=()):
        self.Name = name
        self.Measures = _Coll(measures)
        self.Columns = _Coll()


class _Model:
    def __init__(self, tables):
        self.Tables = _Coll(tables)
        self.saves = 0

    def SaveChanges(self):
        self.saves += 1


class _Server:
    def Disconnect(self):
        pass


def _wire(monkeypatch, model):
    crud = MeasureCRUDManager.__new__(MeasureCRUDManager)
    monkeypatch.setattr(crud, "_get_server_db_model", lambda: (_Server(), None, model))
    monkeypatch.setattr(mcm, "AMO_AVAILABLE", True)
    monkeypatch.setattr(mcm, "Measure", _Measure)
    import core.operations.measure_operations as mo

    monkeypatch.setattr(mo, "get_manager_or_error", lambda name: crud)
    return crud


def test_create_pipes_data_category_to_tom(monkeypatch):
    model = _Model([_Table("Sales")])
    _wire(monkeypatch, model)
    result = MeasureOperationsHandler().execute(
        {
            "operation": "create",
            "table_name": "Sales",
            "measure_name": "Sales URL",
            "expression": '"https://example.com"',
            "data_category": "WebUrl",
        }
    )
    assert result["success"] is True
    assert result["action"] == "created"
    m = model.Tables[0].Measures[0]
    assert (m.Name, m.DataCategory) == ("Sales URL", "WebUrl")
    assert model.saves == 1


def test_update_pipes_data_category_and_never_creates(monkeypatch):
    existing = _Measure()
    existing.Name, existing.Expression = "Image", "1"
    model = _Model([_Table("Sales", [existing])])
    _wire(monkeypatch, model)
    result = MeasureOperationsHandler().execute(
        {
            "operation": "update",
            "table_name": "Sales",
            "measure_name": "Image",
            "expression": '"https://cdn.example.com/x.png"',
            "data_category": "ImageUrl",
        }
    )
    assert result["success"] is True
    assert existing.DataCategory == "ImageUrl"
    # P2-29 / C-11 contract: update of a missing measure never creates it
    missing = MeasureOperationsHandler().execute(
        {"operation": "update", "table_name": "Sales", "measure_name": "Typo", "expression": "2"}
    )
    assert missing["success"] is False
    assert missing["error_type"] == "measure_not_found"
    assert len(model.Tables[0].Measures) == 1


def test_create_without_data_category_leaves_it_unset(monkeypatch):
    model = _Model([_Table("Sales")])
    _wire(monkeypatch, model)
    MeasureOperationsHandler().execute(
        {"operation": "create", "table_name": "Sales", "measure_name": "Plain", "expression": "1"}
    )
    assert model.Tables[0].Measures[0].DataCategory is None
