"""Tests for calc-group post-create mutations (set_precedence)."""

from unittest.mock import MagicMock, patch

from core.operations.calculation_group_manager import CalculationGroupManager


def _model_with_cg(group_name: str = "Time"):
    cg = MagicMock()
    cg.Precedence = 0
    table = MagicMock()
    table.Name = group_name
    table.CalculationGroup = cg
    model = MagicMock()
    model.Tables = [table]
    db = MagicMock()
    db.Model = model
    return model, db, cg


@patch.object(CalculationGroupManager, "_connect_amo_server_db")
def test_set_precedence_writes_value(mock_conn):
    model, db, cg = _model_with_cg()
    server = MagicMock()
    Tabular = MagicMock()
    mock_conn.return_value = (server, db, Tabular)

    mgr = CalculationGroupManager(connection=MagicMock())
    result = mgr.set_precedence(group_name="Time", precedence=10)

    assert result["success"] is True
    assert cg.Precedence == 10
    model.SaveChanges.assert_called_once()
    server.Disconnect.assert_called_once()


@patch.object(CalculationGroupManager, "_connect_amo_server_db")
def test_set_precedence_unknown_group(mock_conn):
    model, db, _ = _model_with_cg(group_name="Time")
    server = MagicMock()
    mock_conn.return_value = (server, db, MagicMock())

    mgr = CalculationGroupManager(connection=MagicMock())
    result = mgr.set_precedence(group_name="DoesNotExist", precedence=5)
    assert result["success"] is False
    assert "not found" in result["error"].lower()
    model.SaveChanges.assert_not_called()
    server.Disconnect.assert_called_once()


@patch.object(CalculationGroupManager, "_connect_amo_server_db")
def test_set_precedence_no_amo(mock_conn):
    """If _connect_amo_server_db returns (None, None, None), surface 'AMO not available'."""
    mock_conn.return_value = (None, None, None)
    mgr = CalculationGroupManager(connection=MagicMock())
    result = mgr.set_precedence(group_name="Time", precedence=10)
    assert result["success"] is False
    assert "amo" in result["error"].lower()


@patch.object(CalculationGroupManager, "_connect_amo_server_db")
def test_set_precedence_coerces_to_int(mock_conn):
    """precedence='15' (str) should still work via int(precedence)."""
    model, db, cg = _model_with_cg()
    mock_conn.return_value = (MagicMock(), db, MagicMock())
    mgr = CalculationGroupManager(connection=MagicMock())
    result = mgr.set_precedence(group_name="Time", precedence="15")
    assert result["success"] is True
    assert cg.Precedence == 15
