"""H1/H2: batch table/column/relationship ops are single-connection and atomic.

The batch object-type ops (tables/columns/relationships) must acquire the AMO
model ONCE, apply every item via the manager's _apply_* helpers, then call
SaveChanges ONCE -- or UndoLocalChanges if any item fails under use_transaction.
This eliminates per-item reconnect (H2) and makes the documented
use_transaction=True atomicity contract real (H1).

NOTE: the production class is BatchOperationsHandler (no constructor args); it is
the holder of _batch_tables/_batch_columns/_batch_relationships. We patch
get_server_db_model + connection_state in core.operations.batch_operations.
"""

from unittest.mock import MagicMock, patch


@patch("core.operations.batch_operations.get_server_db_model")
@patch("core.operations.batch_operations.connection_state")
def test_batch_tables_single_connection_and_one_save(mock_state, mock_gsdm):
    from core.operations.batch_operations import BatchOperationsHandler

    mock_state.is_connected.return_value = True
    crud = MagicMock()
    crud._apply_create_table.return_value = {"success": True, "table": "T"}
    mock_state.table_crud_manager = crud
    server, db, model = MagicMock(), MagicMock(), MagicMock()
    mock_gsdm.return_value = (server, db, model, None)

    mgr = BatchOperationsHandler()
    res = mgr._batch_tables(
        {
            "batch_operation": "create",
            "items": [{"name": "A"}, {"name": "B"}, {"name": "C"}],
            "options": {"use_transaction": True},
        }
    )

    assert res["success"] is True
    mock_gsdm.assert_called_once()
    assert model.SaveChanges.call_count == 1
    server.Disconnect.assert_called_once()
    assert crud._apply_create_table.call_count == 3


@patch("core.operations.batch_operations.get_server_db_model")
@patch("core.operations.batch_operations.connection_state")
def test_batch_tables_rolls_back_on_failure(mock_state, mock_gsdm):
    from core.operations.batch_operations import BatchOperationsHandler

    mock_state.is_connected.return_value = True
    crud = MagicMock()
    crud._apply_create_table.side_effect = [
        {"success": True, "table": "A"},
        {"success": False, "error": "boom", "error_type": "creation_error"},
        {"success": True, "table": "C"},
    ]
    mock_state.table_crud_manager = crud
    server, db, model = MagicMock(), MagicMock(), MagicMock()
    mock_gsdm.return_value = (server, db, model, None)

    mgr = BatchOperationsHandler()
    res = mgr._batch_tables(
        {
            "batch_operation": "create",
            "items": [{"name": "A"}, {"name": "B"}, {"name": "C"}],
            "options": {"use_transaction": True},
        }
    )

    assert res["success"] is False
    assert res["rolled_back"] is True
    model.UndoLocalChanges.assert_called_once()
    model.SaveChanges.assert_not_called()
    server.Disconnect.assert_called_once()


@patch("core.operations.batch_operations.get_server_db_model")
@patch("core.operations.batch_operations.connection_state")
def test_batch_columns_single_save(mock_state, mock_gsdm):
    from core.operations.batch_operations import BatchOperationsHandler

    mock_state.is_connected.return_value = True
    crud = MagicMock()
    crud._apply_delete_column.return_value = {"success": True}
    mock_state.column_crud_manager = crud
    server, db, model = MagicMock(), MagicMock(), MagicMock()
    mock_gsdm.return_value = (server, db, model, None)
    mgr = BatchOperationsHandler()
    res = mgr._batch_columns(
        {
            "batch_operation": "delete",
            "items": [
                {"table_name": "T", "column_name": "a"},
                {"table_name": "T", "column_name": "b"},
            ],
            "options": {"use_transaction": True},
        }
    )
    assert res["success"] is True
    assert model.SaveChanges.call_count == 1
    assert crud._apply_delete_column.call_count == 2


@patch("core.operations.batch_operations.get_server_db_model")
@patch("core.operations.batch_operations.connection_state")
def test_batch_relationships_single_save(mock_state, mock_gsdm):
    from core.operations.batch_operations import BatchOperationsHandler

    mock_state.is_connected.return_value = True
    crud = MagicMock()
    crud._apply_delete_relationship.return_value = {"success": True}
    mock_state.relationship_crud_manager = crud
    server, db, model = MagicMock(), MagicMock(), MagicMock()
    mock_gsdm.return_value = (server, db, model, None)
    mgr = BatchOperationsHandler()
    res = mgr._batch_relationships(
        {
            "batch_operation": "delete",
            "items": [{"relationship_name": "r1"}, {"relationship_name": "r2"}],
            "options": {"use_transaction": True},
        }
    )
    assert res["success"] is True
    assert model.SaveChanges.call_count == 1
    assert crud._apply_delete_relationship.call_count == 2


@patch("core.operations.batch_operations.get_server_db_model")
@patch("core.operations.batch_operations.connection_state")
def test_batch_conn_error_early_return(mock_state, mock_gsdm):
    from core.operations.batch_operations import BatchOperationsHandler

    mock_state.is_connected.return_value = True
    mock_state.table_crud_manager = MagicMock()
    mock_gsdm.return_value = (None, None, None, "Not connected")
    res = BatchOperationsHandler()._batch_tables(
        {
            "batch_operation": "create",
            "items": [{"name": "A"}],
            "options": {},
        }
    )
    assert res["success"] is False
    assert res["error_type"] == "connection_error"


@patch("core.operations.batch_operations.get_server_db_model")
@patch("core.operations.batch_operations.connection_state")
def test_batch_non_transactional_partial_commit(mock_state, mock_gsdm):
    from core.operations.batch_operations import BatchOperationsHandler

    mock_state.is_connected.return_value = True
    crud = MagicMock()
    crud._apply_create_table.side_effect = [
        {"success": True},
        {"success": False, "error": "x"},
        {"success": True},
    ]
    mock_state.table_crud_manager = crud
    server, db, model = MagicMock(), MagicMock(), MagicMock()
    mock_gsdm.return_value = (server, db, model, None)
    res = BatchOperationsHandler()._batch_tables(
        {
            "batch_operation": "create",
            "items": [{"name": "A"}, {"name": "B"}, {"name": "C"}],
            "options": {"use_transaction": False, "continue_on_error": True},
        }
    )
    assert model.SaveChanges.call_count == 1  # successful items committed
    model.UndoLocalChanges.assert_not_called()
    assert res["succeeded"] == 2 and res["failed"] == 1


@patch("core.operations.batch_operations.get_server_db_model")
@patch("core.operations.batch_operations.connection_state")
def test_batch_apply_exception_triggers_rollback(mock_state, mock_gsdm):
    from core.operations.batch_operations import BatchOperationsHandler

    mock_state.is_connected.return_value = True
    crud = MagicMock()
    crud._apply_create_table.side_effect = [{"success": True}, RuntimeError("boom")]
    mock_state.table_crud_manager = crud
    server, db, model = MagicMock(), MagicMock(), MagicMock()
    mock_gsdm.return_value = (server, db, model, None)
    res = BatchOperationsHandler()._batch_tables(
        {
            "batch_operation": "create",
            "items": [{"name": "A"}, {"name": "B"}],
            "options": {"use_transaction": True},
        }
    )
    assert res["success"] is False
    model.UndoLocalChanges.assert_called_once()
    server.Disconnect.assert_called_once()
