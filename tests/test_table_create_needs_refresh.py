"""S-13: creating a calculated table says it is EMPTY until refreshed (and how to refresh it)."""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock


class _FakeTables(list):
    def Add(self, t):
        self.append(t)


class _Obj:
    def __init__(self):
        self.Partitions = _FakeTables()


def _install_tom(monkeypatch):
    tom = types.ModuleType("Microsoft.AnalysisServices.Tabular")
    tom.Partition = type("Partition", (), {})
    tom.CalculatedPartitionSource = type("CalculatedPartitionSource", (), {})
    tom.MPartitionSource = type("MPartitionSource", (), {})
    tom.PartitionSourceType = type("PartitionSourceType", (), {})
    ms, asv = types.ModuleType("Microsoft"), types.ModuleType("Microsoft.AnalysisServices")
    ms.AnalysisServices, asv.Tabular = asv, tom
    monkeypatch.setitem(sys.modules, "Microsoft", ms)
    monkeypatch.setitem(sys.modules, "Microsoft.AnalysisServices", asv)
    monkeypatch.setitem(sys.modules, "Microsoft.AnalysisServices.Tabular", tom)


def _mgr(monkeypatch):
    from core.operations import table_crud_manager as tcm

    _install_tom(monkeypatch)
    monkeypatch.setattr(tcm, "Table", _Obj)
    model = MagicMock()
    model.Tables = _FakeTables()
    return tcm.TableCRUDManager(connection=MagicMock()), model


def test_calculated_table_create_reports_needs_refresh(monkeypatch):
    mgr, model = _mgr(monkeypatch)
    res = mgr._apply_create_table(model, table_name="Calc", expression='ROW("a", 1)')
    assert res["success"] is True and res["needs_refresh"] is True
    assert res["refresh_with"] == {
        "tool": "02_Model_Operations",
        "args": {
            "object_type": "model",
            "operation": "refresh",
            "tables": ["Calc"],
            "refresh_type": "calculate",
        },
    }
    assert "EMPTY" in res["refresh_note"]


def test_import_table_create_has_no_needs_refresh(monkeypatch):
    mgr, model = _mgr(monkeypatch)
    res = mgr._apply_create_table(
        model, table_name="Imp", m_expression='let Source = #table({"a"}, {{1}}) in Source'
    )
    assert res["success"] is True
    assert "needs_refresh" not in res
