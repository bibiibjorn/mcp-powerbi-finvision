"""Verify 05_DAX_Intelligence accepts measure_name as alternative to expression."""

from unittest.mock import MagicMock, patch


def test_optimize_accepts_measure_name_only():
    """When only measure_name is passed, handler resolves the DAX internally."""
    from server.handlers.debug_handler import _handle_optimize

    args = {
        "operation": "optimize",
        "measure_name": "Total Sales",
        "table_name": "m_Measures",
        "total_ms": 1500,
        "fe_ms": 1000,
        "se_ms": 500,
        "fe_pct": 66.0,
        "se_queries": 20,
    }

    with patch("server.handlers.debug_handler.connection_state") as mock_cs, patch(
        "server.handlers.debug_handler._resolve_measure_expression"
    ) as mock_resolve:
        mock_cs.is_connected.return_value = True
        mock_cs.query_executor = MagicMock()
        mock_resolve.return_value = {
            "success": True,
            "expression": "SUM(Sales[Amount])",
            "measure_details": {"table_name": "m_Measures"},
            "expression_source": "live",
        }
        result = _handle_optimize(args)

    mock_resolve.assert_called_once()
    assert result is not None


def test_analyze_accepts_measure_name_only():
    """analyze op with only measure_name resolves expression and proceeds."""
    from server.handlers.debug_handler import _handle_analyze

    args = {"operation": "analyze", "measure_name": "Sales YTD", "table_name": "m"}

    with patch("server.handlers.debug_handler.connection_state") as mock_cs, patch(
        "server.handlers.debug_handler._resolve_measure_expression"
    ) as mock_resolve:
        mock_cs.is_connected.return_value = True
        mock_cs.query_executor = MagicMock()
        mock_resolve.return_value = {
            "success": True,
            "expression": "TOTALYTD(...)",
            "measure_details": {"table_name": "m"},
            "expression_source": "live",
        }
        _handle_analyze(args)
    mock_resolve.assert_called_once()


def test_analyze_errors_when_neither_expression_nor_measure_name():
    from server.handlers.debug_handler import _handle_analyze

    result = _handle_analyze({"operation": "analyze"})
    assert result["success"] is False
    assert "measure_name" in result["error"] or "expression" in result["error"]


def test_analyze_with_both_uses_expression_not_measure_name():
    """When both expression and measure_name are passed, expression wins (no resolve call)."""
    from server.handlers.debug_handler import _handle_analyze

    args = {
        "operation": "analyze",
        "expression": "SUM(Sales[Amount])",
        "measure_name": "Should Be Ignored",
    }

    with patch("server.handlers.debug_handler._resolve_measure_expression") as mock_resolve:
        result = _handle_analyze(args)

    mock_resolve.assert_not_called()
    assert result is not None
