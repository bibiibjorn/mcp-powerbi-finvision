"""Tests for in-place field parameter field add/remove/reorder."""

from unittest.mock import MagicMock, patch

from core.operations.field_parameter_manager import FieldParameterManager


def _fp_table_with_expression(expr: str, table_name: str = "Picker"):
    table = MagicMock()
    table.Name = table_name
    partition = MagicMock()
    source = MagicMock()
    source.Expression = expr
    partition.Source = source
    table.Partitions = [partition]
    model = MagicMock()
    model.Tables = [table]
    return model, table, partition


@patch("core.operations.field_parameter_manager.AMO_AVAILABLE", True)
@patch.object(FieldParameterManager, "_get_server_db_model")
def test_add_field_appends_to_expression(mock_conn):
    expr = "{\n    (\"Revenue\", NAMEOF('Sales'[Revenue]), 0)\n}"
    model, _, partition = _fp_table_with_expression(expr)
    server = MagicMock()
    mock_conn.return_value = (server, MagicMock(), model)

    result = FieldParameterManager(connection=MagicMock()).add_field(
        table_name="Picker",
        field={"label": "Margin", "reference": "'Sales'[Margin]"},
    )

    assert result["success"] is True
    assert "Margin" in partition.Source.Expression
    assert "NAMEOF('Sales'[Margin])" in partition.Source.Expression
    assert ", 1)" in partition.Source.Expression
    model.SaveChanges.assert_called_once()


@patch("core.operations.field_parameter_manager.AMO_AVAILABLE", True)
@patch.object(FieldParameterManager, "_get_server_db_model")
def test_add_field_explicit_order(mock_conn):
    expr = "{\n    (\"A\", NAMEOF('T'[A]), 0)\n}"
    model, _, partition = _fp_table_with_expression(expr)
    mock_conn.return_value = (MagicMock(), MagicMock(), model)
    result = FieldParameterManager(connection=MagicMock()).add_field(
        table_name="Picker",
        field={"label": "B", "reference": "'T'[B]", "order": 5},
    )
    assert result["success"] is True
    assert ", 5)" in partition.Source.Expression


@patch("core.operations.field_parameter_manager.AMO_AVAILABLE", True)
@patch.object(FieldParameterManager, "_get_server_db_model")
def test_add_field_duplicate_label_rejected(mock_conn):
    expr = "{\n    (\"Revenue\", NAMEOF('Sales'[Revenue]), 0)\n}"
    model, _, _ = _fp_table_with_expression(expr)
    mock_conn.return_value = (MagicMock(), MagicMock(), model)
    result = FieldParameterManager(connection=MagicMock()).add_field(
        table_name="Picker",
        field={"label": "Revenue", "reference": "'Sales'[Other]"},
    )
    assert result["success"] is False
    assert "already exists" in result["error"].lower()


@patch("core.operations.field_parameter_manager.AMO_AVAILABLE", True)
@patch.object(FieldParameterManager, "_get_server_db_model")
def test_add_field_missing_label_or_reference(mock_conn):
    expr = "{\n    (\"Revenue\", NAMEOF('Sales'[Revenue]), 0)\n}"
    model, _, _ = _fp_table_with_expression(expr)
    mock_conn.return_value = (MagicMock(), MagicMock(), model)
    result = FieldParameterManager(connection=MagicMock()).add_field(
        table_name="Picker", field={"label": "X"}
    )
    assert result["success"] is False
    assert "label" in result["error"].lower() or "reference" in result["error"].lower()


@patch("core.operations.field_parameter_manager.AMO_AVAILABLE", True)
@patch.object(FieldParameterManager, "_get_server_db_model")
def test_remove_field_drops_row_and_renumbers(mock_conn):
    expr = (
        "{\n"
        "    (\"Revenue\", NAMEOF('Sales'[Revenue]), 0),\n"
        "    (\"Margin\", NAMEOF('Sales'[Margin]), 1),\n"
        "    (\"Units\", NAMEOF('Sales'[Units]), 2)\n"
        "}"
    )
    model, _, partition = _fp_table_with_expression(expr)
    mock_conn.return_value = (MagicMock(), MagicMock(), model)

    result = FieldParameterManager(connection=MagicMock()).remove_field(
        table_name="Picker", label="Margin"
    )

    assert result["success"] is True
    new_expr = partition.Source.Expression
    assert "Margin" not in new_expr
    assert ", 0)" in new_expr
    assert ", 1)" in new_expr
    assert ", 2)" not in new_expr
    assert result["field_count"] == 2


@patch("core.operations.field_parameter_manager.AMO_AVAILABLE", True)
@patch.object(FieldParameterManager, "_get_server_db_model")
def test_remove_field_unknown_label(mock_conn):
    expr = "{\n    (\"Revenue\", NAMEOF('Sales'[Revenue]), 0)\n}"
    model, _, _ = _fp_table_with_expression(expr)
    mock_conn.return_value = (MagicMock(), MagicMock(), model)
    result = FieldParameterManager(connection=MagicMock()).remove_field(
        table_name="Picker", label="DoesNotExist"
    )
    assert result["success"] is False
    assert "doesnotexist" in result["error"].lower()


@patch("core.operations.field_parameter_manager.AMO_AVAILABLE", True)
@patch.object(FieldParameterManager, "_get_server_db_model")
def test_reorder_fields_uses_supplied_order(mock_conn):
    expr = (
        "{\n"
        "    (\"A\", NAMEOF('T'[A]), 0),\n"
        "    (\"B\", NAMEOF('T'[B]), 1),\n"
        "    (\"C\", NAMEOF('T'[C]), 2)\n"
        "}"
    )
    model, _, partition = _fp_table_with_expression(expr)
    mock_conn.return_value = (MagicMock(), MagicMock(), model)
    result = FieldParameterManager(connection=MagicMock()).reorder_fields(
        table_name="Picker", labels_in_order=["C", "A", "B"]
    )
    assert result["success"] is True
    new_expr = partition.Source.Expression
    lines = [ln.strip() for ln in new_expr.splitlines() if ln.strip().startswith("(")]
    assert lines[0].startswith('("C"')
    assert ", 0)" in lines[0]
    assert lines[1].startswith('("A"')
    assert ", 1)" in lines[1]
    assert lines[2].startswith('("B"')
    assert ", 2)" in lines[2]


@patch("core.operations.field_parameter_manager.AMO_AVAILABLE", True)
@patch.object(FieldParameterManager, "_get_server_db_model")
def test_reorder_fields_rejects_missing_or_extra(mock_conn):
    expr = "{\n    (\"A\", NAMEOF('T'[A]), 0),\n    (\"B\", NAMEOF('T'[B]), 1)\n}"
    model, _, _ = _fp_table_with_expression(expr)
    mock_conn.return_value = (MagicMock(), MagicMock(), model)
    # Missing "B" from supplied → "B" is in model not in supplied → "extra" (model has extras)
    r1 = FieldParameterManager(connection=MagicMock()).reorder_fields(
        table_name="Picker", labels_in_order=["A"]
    )
    assert r1["success"] is False
    assert "B" in r1.get("extra", [])
    # Extra "C" supplied → "C" is in supplied not in model → "missing" (from model)
    r2 = FieldParameterManager(connection=MagicMock()).reorder_fields(
        table_name="Picker", labels_in_order=["A", "B", "C"]
    )
    assert r2["success"] is False
    assert "C" in r2.get("missing", [])


@patch("core.operations.field_parameter_manager.AMO_AVAILABLE", True)
@patch.object(FieldParameterManager, "_get_server_db_model")
def test_table_not_found(mock_conn):
    """If the named table is not in the model, all three ops return success=False."""
    model = MagicMock()
    model.Tables = []  # empty
    mock_conn.return_value = (MagicMock(), MagicMock(), model)
    mgr = FieldParameterManager(connection=MagicMock())
    for fn, args in [
        (mgr.add_field, dict(table_name="Picker", field={"label": "X", "reference": "'T'[X]"})),
        (mgr.remove_field, dict(table_name="Picker", label="X")),
        (mgr.reorder_fields, dict(table_name="Picker", labels_in_order=[])),
    ]:
        res = fn(**args)
        assert res["success"] is False


def test_amo_unavailable_returns_clean_error():
    """Without AMO, methods short-circuit to a clean error."""
    with patch("core.operations.field_parameter_manager.AMO_AVAILABLE", False):
        result = FieldParameterManager(connection=MagicMock()).add_field(
            table_name="Picker", field={"label": "X", "reference": "'T'[X]"}
        )
        assert result["success"] is False
        assert result.get("error_type") == "amo_unavailable"
