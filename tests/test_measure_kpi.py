"""Tests for MeasureCRUDManager set_kpi / clear_kpi / get_kpi.

Mock TOM KPI class with the 9 properties the manager touches.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.operations import measure_crud_manager as mod
from core.operations.measure_crud_manager import MeasureCRUDManager


class _FakeKPI:
    """Stand-in for Microsoft.AnalysisServices.Tabular.KPI."""

    _SLOTS = (
        "TargetExpression",
        "StatusExpression",
        "TrendExpression",
        "StatusGraphic",
        "TrendGraphic",
        "TargetDescription",
        "StatusDescription",
        "TrendDescription",
        "TargetFormatString",
    )

    def __init__(self):
        for s in self._SLOTS:
            setattr(self, s, None)


def _build_model_with_measure(measure_name: str, table_name: str, kpi=None):
    measure = SimpleNamespace(Name=measure_name, KPI=kpi)
    table = SimpleNamespace(Name=table_name, Measures=[measure])
    model = MagicMock()
    model.Tables = [table]
    return model, table, measure


@pytest.fixture
def tom_module(monkeypatch):
    fake_tom = MagicMock()
    fake_tom.KPI = _FakeKPI
    monkeypatch.setitem(sys.modules, "Microsoft.AnalysisServices.Tabular", fake_tom)
    yield fake_tom


# ---------------- set_kpi ----------------


def test_set_kpi_requires_at_least_one_expression(tom_module):
    """No expression args at all → invalid_parameters; no AMO connection."""
    mgr = MeasureCRUDManager(connection=MagicMock())
    with patch.object(mod, "AMO_AVAILABLE", True):
        result = mgr.set_kpi("T1", "M1")
    assert result["success"] is False
    assert result["error_type"] == "invalid_parameters"
    assert "at least one" in result["error"]


def test_set_kpi_creates_fresh_kpi(tom_module):
    model, _t, measure = _build_model_with_measure("M1", "T1", kpi=None)
    server = MagicMock()
    mgr = MeasureCRUDManager(connection=MagicMock())

    with patch.object(mod, "AMO_AVAILABLE", True), patch.object(
        mgr, "_get_server_db_model", return_value=(server, MagicMock(), model)
    ):
        result = mgr.set_kpi(
            "T1",
            "M1",
            target_expression="[Sales Target]",
            status_expression="IF([Sales] >= [Sales Target], 1, -1)",
        )

    assert result["success"] is True
    assert result["action"] == "created"
    assert measure.KPI is not None
    assert measure.KPI.TargetExpression == "[Sales Target]"
    assert "IF" in measure.KPI.StatusExpression
    # Auto-default status graphic kicks in when status_expression is set
    assert measure.KPI.StatusGraphic == "Three Symbols UnCircled Colored"
    # Trend not provided, no graphic default applied for trend
    assert measure.KPI.TrendGraphic is None
    model.SaveChanges.assert_called_once()


def test_set_kpi_updates_existing_partial(tom_module):
    """Partial update: only provided fields touched, others preserved."""
    existing = _FakeKPI()
    existing.TargetExpression = "[Old Target]"
    existing.StatusExpression = "[Old Status]"
    existing.StatusGraphic = "Five Bars Colored"

    model, _t, measure = _build_model_with_measure("M1", "T1", kpi=existing)
    server = MagicMock()
    mgr = MeasureCRUDManager(connection=MagicMock())

    with patch.object(mod, "AMO_AVAILABLE", True), patch.object(
        mgr, "_get_server_db_model", return_value=(server, MagicMock(), model)
    ):
        result = mgr.set_kpi(
            "T1",
            "M1",
            target_expression="[New Target]",  # only this changes
        )

    assert result["success"] is True
    assert result["action"] == "updated"
    assert measure.KPI is existing  # same object updated, not replaced
    assert measure.KPI.TargetExpression == "[New Target]"
    # Untouched fields preserved
    assert measure.KPI.StatusExpression == "[Old Status]"
    assert measure.KPI.StatusGraphic == "Five Bars Colored"
    assert "TargetExpression" in result["properties_set"]
    assert "StatusExpression" not in result["properties_set"]


def test_set_kpi_trend_only_auto_defaults_trend_graphic(tom_module):
    """trend_expression supplied alone on a fresh KPI → TrendGraphic auto-set.

    AMO rejects SaveChanges when an expression is set without its graphic;
    the manager defaults TrendGraphic to "Three Arrows Colored" to match
    the analogous status_expression auto-default.
    """
    model, _t, measure = _build_model_with_measure("M1", "T1", kpi=None)
    server = MagicMock()
    mgr = MeasureCRUDManager(connection=MagicMock())

    with patch.object(mod, "AMO_AVAILABLE", True), patch.object(
        mgr, "_get_server_db_model", return_value=(server, MagicMock(), model)
    ):
        result = mgr.set_kpi(
            "T1",
            "M1",
            trend_expression="VAR _t = [Sales] - [Sales LY] RETURN IF(_t>0, 1, -1)",
        )

    assert result["success"] is True
    assert measure.KPI is not None
    assert measure.KPI.TrendExpression is not None
    assert measure.KPI.TrendGraphic == "Three Arrows Colored"
    # No status expression supplied → no status graphic auto-default
    assert measure.KPI.StatusGraphic is None
    model.SaveChanges.assert_called_once()


def test_set_kpi_explicit_graphic_overrides_default(tom_module):
    """User-supplied graphic is honoured even when the expression is set."""
    model, _t, measure = _build_model_with_measure("M1", "T1", kpi=None)
    server = MagicMock()
    mgr = MeasureCRUDManager(connection=MagicMock())

    with patch.object(mod, "AMO_AVAILABLE", True), patch.object(
        mgr, "_get_server_db_model", return_value=(server, MagicMock(), model)
    ):
        mgr.set_kpi(
            "T1",
            "M1",
            status_expression="[Status]",
            status_graphic="Five Bars Colored",  # user override
        )

    assert measure.KPI.StatusGraphic == "Five Bars Colored"


def test_set_kpi_measure_not_found(tom_module):
    model, _t, _m = _build_model_with_measure("Other", "T1")
    server = MagicMock()
    mgr = MeasureCRUDManager(connection=MagicMock())

    with patch.object(mod, "AMO_AVAILABLE", True), patch.object(
        mgr, "_get_server_db_model", return_value=(server, MagicMock(), model)
    ):
        result = mgr.set_kpi("T1", "Missing", target_expression="[X]")

    assert result["success"] is False
    assert result["error_type"] == "measure_not_found"
    model.SaveChanges.assert_not_called()


def test_set_kpi_amo_unavailable():
    mgr = MeasureCRUDManager(connection=MagicMock())
    with patch.object(mod, "AMO_AVAILABLE", False):
        result = mgr.set_kpi("T1", "M1", target_expression="[X]")
    assert result["success"] is False
    assert result["error_type"] == "amo_unavailable"


# ---------------- clear_kpi ----------------


def test_clear_kpi_when_present(tom_module):
    existing = _FakeKPI()
    existing.TargetExpression = "[Target]"
    model, _t, measure = _build_model_with_measure("M1", "T1", kpi=existing)
    server = MagicMock()
    mgr = MeasureCRUDManager(connection=MagicMock())

    with patch.object(mod, "AMO_AVAILABLE", True), patch.object(
        mgr, "_get_server_db_model", return_value=(server, MagicMock(), model)
    ):
        result = mgr.clear_kpi("T1", "M1")

    assert result["success"] is True
    assert result["action"] == "cleared"
    assert result["was_present"] is True
    assert measure.KPI is None
    model.SaveChanges.assert_called_once()


def test_clear_kpi_when_absent_idempotent(tom_module):
    model, _t, measure = _build_model_with_measure("M1", "T1", kpi=None)
    server = MagicMock()
    mgr = MeasureCRUDManager(connection=MagicMock())

    with patch.object(mod, "AMO_AVAILABLE", True), patch.object(
        mgr, "_get_server_db_model", return_value=(server, MagicMock(), model)
    ):
        result = mgr.clear_kpi("T1", "M1")

    assert result["success"] is True
    assert result["was_present"] is False
    assert measure.KPI is None


# ---------------- get_kpi ----------------


def test_get_kpi_present(tom_module):
    existing = _FakeKPI()
    existing.TargetExpression = "[Target]"
    existing.StatusExpression = "[Status]"
    existing.TrendExpression = "[Trend]"
    existing.StatusGraphic = "Three Flags Colored"

    model, _t, _m = _build_model_with_measure("M1", "T1", kpi=existing)
    server = MagicMock()
    mgr = MeasureCRUDManager(connection=MagicMock())

    with patch.object(mod, "AMO_AVAILABLE", True), patch.object(
        mgr, "_get_server_db_model", return_value=(server, MagicMock(), model)
    ):
        result = mgr.get_kpi("T1", "M1")

    assert result["success"] is True
    assert result["present"] is True
    kpi = result["kpi"]
    assert kpi["target_expression"] == "[Target]"
    assert kpi["status_expression"] == "[Status]"
    assert kpi["trend_expression"] == "[Trend]"
    assert kpi["status_graphic"] == "Three Flags Colored"
    # Read-only — no save
    model.SaveChanges.assert_not_called()


def test_get_kpi_absent(tom_module):
    model, _t, _m = _build_model_with_measure("M1", "T1", kpi=None)
    server = MagicMock()
    mgr = MeasureCRUDManager(connection=MagicMock())

    with patch.object(mod, "AMO_AVAILABLE", True), patch.object(
        mgr, "_get_server_db_model", return_value=(server, MagicMock(), model)
    ):
        result = mgr.get_kpi("T1", "M1")

    assert result["success"] is True
    assert result["present"] is False
    assert "kpi" not in result
    model.SaveChanges.assert_not_called()
