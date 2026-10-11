"""Tests for MeasureCRUDManager.set_detail_rows_expression.

Mirrors test_measure_format_string_expression: targets DetailRowsDefinition
on a measure (CL 1400+ feature, used by DETAILROWS() / Excel drill).
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.operations import measure_crud_manager as mod
from core.operations.measure_crud_manager import MeasureCRUDManager


def _build_model_with_measure(measure_name: str, table_name: str, drd=None):
    measure = SimpleNamespace(Name=measure_name, DetailRowsDefinition=drd)
    table = SimpleNamespace(Name=table_name, Measures=[measure])
    model = MagicMock()
    model.Tables = [table]
    return model, table, measure


@pytest.fixture
def tom_module(monkeypatch):
    fake_tom = MagicMock()

    class _DRD:
        def __init__(self):
            self.Expression = None

    fake_tom.DetailRowsDefinition = _DRD
    monkeypatch.setitem(sys.modules, "Microsoft.AnalysisServices.Tabular", fake_tom)
    yield fake_tom


def test_set_drd_creates_definition(tom_module):
    model, _t, measure = _build_model_with_measure("M1", "T1", drd=None)
    server = MagicMock()
    mgr = MeasureCRUDManager(connection=MagicMock())

    with patch.object(mod, "AMO_AVAILABLE", True), patch.object(
        mgr, "_get_server_db_model", return_value=(server, MagicMock(), model)
    ):
        result = mgr.set_detail_rows_expression(
            "T1", "M1", "SELECTCOLUMNS('Sales', \"Order\", 'Sales'[Order])"
        )

    assert result["success"] is True
    assert result["cleared"] is False
    assert result["action"] == "set"
    assert result["table"] == "T1"
    assert result["measure"] == "M1"
    assert measure.DetailRowsDefinition is not None
    assert "SELECTCOLUMNS" in measure.DetailRowsDefinition.Expression
    model.SaveChanges.assert_called_once()


def test_set_drd_clears_when_none(tom_module):
    existing = SimpleNamespace(Expression="OLD")
    model, _t, measure = _build_model_with_measure("M1", "T1", drd=existing)
    server = MagicMock()
    mgr = MeasureCRUDManager(connection=MagicMock())

    with patch.object(mod, "AMO_AVAILABLE", True), patch.object(
        mgr, "_get_server_db_model", return_value=(server, MagicMock(), model)
    ):
        result = mgr.set_detail_rows_expression("T1", "M1", None)

    assert result["success"] is True
    assert result["cleared"] is True
    assert result["action"] == "cleared"
    assert measure.DetailRowsDefinition is None
    model.SaveChanges.assert_called_once()


def test_set_drd_clears_when_empty_string(tom_module):
    existing = SimpleNamespace(Expression="OLD")
    model, _t, measure = _build_model_with_measure("M1", "T1", drd=existing)
    server = MagicMock()
    mgr = MeasureCRUDManager(connection=MagicMock())

    with patch.object(mod, "AMO_AVAILABLE", True), patch.object(
        mgr, "_get_server_db_model", return_value=(server, MagicMock(), model)
    ):
        result = mgr.set_detail_rows_expression("T1", "M1", "")

    assert result["success"] is True
    assert result["cleared"] is True
    assert measure.DetailRowsDefinition is None


def test_set_drd_measure_not_found(tom_module):
    model, _t, _m = _build_model_with_measure("Other", "T1")
    server = MagicMock()
    mgr = MeasureCRUDManager(connection=MagicMock())

    with patch.object(mod, "AMO_AVAILABLE", True), patch.object(
        mgr, "_get_server_db_model", return_value=(server, MagicMock(), model)
    ):
        result = mgr.set_detail_rows_expression("T1", "Missing", "EVALUATE 'X'")

    assert result["success"] is False
    assert result["error_type"] == "measure_not_found"
    model.SaveChanges.assert_not_called()


def test_set_drd_table_not_found(tom_module):
    model, _t, _m = _build_model_with_measure("M1", "T1")
    server = MagicMock()
    mgr = MeasureCRUDManager(connection=MagicMock())

    with patch.object(mod, "AMO_AVAILABLE", True), patch.object(
        mgr, "_get_server_db_model", return_value=(server, MagicMock(), model)
    ):
        result = mgr.set_detail_rows_expression("MissingT", "M1", "EVALUATE 'X'")

    assert result["success"] is False
    assert result["error_type"] == "table_not_found"
    model.SaveChanges.assert_not_called()


def test_set_drd_amo_unavailable():
    mgr = MeasureCRUDManager(connection=MagicMock())
    with patch.object(mod, "AMO_AVAILABLE", False):
        result = mgr.set_detail_rows_expression("T1", "M1", "EVALUATE 'X'")
    assert result["success"] is False
    assert result["error_type"] == "amo_unavailable"


def test_set_drd_invalid_identifier(tom_module):
    mgr = MeasureCRUDManager(connection=MagicMock())
    result = mgr.set_detail_rows_expression("", "M1", "EVALUATE 'X'")
    assert result["success"] is False
    assert result["error_type"] == "invalid_parameters"
