"""Tests for MeasureCRUDManager.set_format_string_expression.

Covers the dynamic format string expression workflow added for
02_Model_Operations / measure / set_format_string_expression.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.operations import measure_crud_manager as mod
from core.operations.measure_crud_manager import MeasureCRUDManager


def _build_model_with_measure(measure_name: str, table_name: str, fsd=None):
    """Build a minimal mock TOM model exposing one table with one measure."""
    measure = SimpleNamespace(Name=measure_name, FormatStringDefinition=fsd)
    table = SimpleNamespace(Name=table_name, Measures=[measure])
    model = MagicMock()
    model.Tables = [table]
    return model, table, measure


@pytest.fixture
def tom_module(monkeypatch):
    """Install a fake `Microsoft.AnalysisServices.Tabular` module with a
    FormatStringDefinition class so the lazy `import` inside
    set_format_string_expression resolves without real AMO."""
    fake_tom = MagicMock()

    class _FSD:
        def __init__(self):
            self.Expression = None

    fake_tom.FormatStringDefinition = _FSD
    monkeypatch.setitem(sys.modules, "Microsoft.AnalysisServices.Tabular", fake_tom)
    yield fake_tom


def test_set_format_string_expression_creates_definition(tom_module):
    """Non-empty expression: instantiate FSD, set Expression, save, return set."""
    model, _table, measure = _build_model_with_measure("M1", "T1", fsd=None)
    server = MagicMock()
    db = MagicMock()

    mgr = MeasureCRUDManager(connection=MagicMock())

    with patch.object(mod, "AMO_AVAILABLE", True), patch.object(
        mgr, "_get_server_db_model", return_value=(server, db, model)
    ):
        result = mgr.set_format_string_expression("T1", "M1", '"#,##0.00"')

    assert result["success"] is True
    assert result["cleared"] is False
    assert result["action"] == "set"
    assert result["table"] == "T1"
    assert result["measure"] == "M1"
    # FSD assigned with the expression
    assert measure.FormatStringDefinition is not None
    assert measure.FormatStringDefinition.Expression == '"#,##0.00"'
    model.SaveChanges.assert_called_once()


def test_set_format_string_expression_clears_when_empty(tom_module):
    """expression=None should clear FormatStringDefinition (set to None)."""
    existing_fsd = SimpleNamespace(Expression='"existing"')
    model, _table, measure = _build_model_with_measure("M1", "T1", fsd=existing_fsd)
    server = MagicMock()
    db = MagicMock()

    mgr = MeasureCRUDManager(connection=MagicMock())

    with patch.object(mod, "AMO_AVAILABLE", True), patch.object(
        mgr, "_get_server_db_model", return_value=(server, db, model)
    ):
        result = mgr.set_format_string_expression("T1", "M1", None)

    assert result["success"] is True
    assert result["cleared"] is True
    assert result["action"] == "cleared"
    assert measure.FormatStringDefinition is None
    model.SaveChanges.assert_called_once()


def test_set_format_string_expression_clears_when_empty_string(tom_module):
    """Empty string also clears, matching the spec's 'None or empty' branch."""
    existing_fsd = SimpleNamespace(Expression='"existing"')
    model, _table, measure = _build_model_with_measure("M1", "T1", fsd=existing_fsd)
    server = MagicMock()
    db = MagicMock()

    mgr = MeasureCRUDManager(connection=MagicMock())

    with patch.object(mod, "AMO_AVAILABLE", True), patch.object(
        mgr, "_get_server_db_model", return_value=(server, db, model)
    ):
        result = mgr.set_format_string_expression("T1", "M1", "")

    assert result["success"] is True
    assert result["cleared"] is True
    assert measure.FormatStringDefinition is None


def test_set_format_string_expression_measure_not_found(tom_module):
    """Table found, measure missing -> success=False, error mentions 'not found'."""
    model, _table, _measure = _build_model_with_measure("Other", "T1", fsd=None)
    server = MagicMock()
    db = MagicMock()

    mgr = MeasureCRUDManager(connection=MagicMock())

    with patch.object(mod, "AMO_AVAILABLE", True), patch.object(
        mgr, "_get_server_db_model", return_value=(server, db, model)
    ):
        result = mgr.set_format_string_expression("T1", "Missing", '"#,##0"')

    assert result["success"] is False
    assert "not found" in result["error"]
    assert result["error_type"] == "measure_not_found"
    model.SaveChanges.assert_not_called()


def test_set_format_string_expression_table_not_found(tom_module):
    """Table missing -> success=False, error mentions table not found."""
    model, _table, _measure = _build_model_with_measure("M1", "T1", fsd=None)
    server = MagicMock()
    db = MagicMock()

    mgr = MeasureCRUDManager(connection=MagicMock())

    with patch.object(mod, "AMO_AVAILABLE", True), patch.object(
        mgr, "_get_server_db_model", return_value=(server, db, model)
    ):
        result = mgr.set_format_string_expression("MissingTable", "M1", '"#,##0"')

    assert result["success"] is False
    assert "not found" in result["error"]
    assert result["error_type"] == "table_not_found"
    model.SaveChanges.assert_not_called()


def test_set_format_string_expression_amo_unavailable():
    """When AMO is not loaded, return a clean error instead of crashing."""
    mgr = MeasureCRUDManager(connection=MagicMock())
    with patch.object(mod, "AMO_AVAILABLE", False):
        result = mgr.set_format_string_expression("T1", "M1", '"#,##0"')
    assert result["success"] is False
    assert "AMO" in result["error"]
    assert result["error_type"] == "amo_unavailable"


def test_set_format_string_expression_invalid_identifier(tom_module):
    """Empty/invalid identifiers should be rejected before AMO connection."""
    mgr = MeasureCRUDManager(connection=MagicMock())
    result = mgr.set_format_string_expression("", "Sales", '"$0.00"')
    assert result["success"] is False
    assert result["error_type"] == "invalid_parameters"
