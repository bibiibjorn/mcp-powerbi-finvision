"""Dispatch tests for 05_DAX_Intelligence:list_udfs (audit C1)."""

from unittest.mock import MagicMock, patch


@patch("server.handlers.dax_context_handler.connection_state")
def test_list_udfs_returns_rows_on_success(mock_state):
    from server.handlers.dax_context_handler import _handle_list_udfs

    qx = MagicMock()
    qx.validate_and_execute_dax.return_value = {
        "success": True,
        "rows": [{"Name": "MyUdf"}],
        "row_count": 1,
        "columns": ["Name"],
    }
    mock_state.query_executor = qx

    result = _handle_list_udfs({})

    assert result["success"] is True
    assert result["udfs"] == [{"Name": "MyUdf"}]
    assert result["count"] == 1
    qx.validate_and_execute_dax.assert_called_once()


@patch("server.handlers.dax_context_handler.connection_state")
def test_list_udfs_infra_failure_is_not_silent_success(mock_state):
    from server.handlers.dax_context_handler import _handle_list_udfs

    qx = MagicMock()
    qx.validate_and_execute_dax.return_value = {
        "success": False,
        "error": "connection lost",
        "error_type": "connection_not_open",
    }
    mock_state.query_executor = qx

    result = _handle_list_udfs({})
    assert result["success"] is False
    assert "error" in result


@patch("server.handlers.dax_context_handler.connection_state")
def test_list_udfs_unknown_function_reports_unsupported(mock_state):
    from server.handlers.dax_context_handler import _handle_list_udfs

    qx = MagicMock()
    qx.validate_and_execute_dax.return_value = {
        "success": False,
        "error": "The function 'INFO.USERDEFINEDFUNCTIONS' has not been recognized",
        "error_type": "dax_error",
    }
    mock_state.query_executor = qx

    result = _handle_list_udfs({})
    assert result["success"] is True
    assert result["udfs"] == []
    assert result["count"] == 0
    assert "note" in result


@patch("server.handlers.dax_context_handler.connection_state")
def test_list_udfs_not_connected(mock_state):
    from server.handlers.dax_context_handler import _handle_list_udfs

    mock_state.query_executor = None
    result = _handle_list_udfs({})
    assert result["success"] is False
