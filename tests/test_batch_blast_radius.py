from core.operations.batch_validation import check_blast_radius


def test_small_delete_passes():
    result = check_blast_radius("delete", [{"name": f"m{i}"} for i in range(5)])
    assert result is None


def test_medium_delete_requires_confirm():
    items = [{"name": f"m{i}"} for i in range(25)]
    result = check_blast_radius("delete", items)
    assert result is not None
    assert result["success"] is False
    assert result["error_type"] == "confirmation_required"
    assert result["would_affect"]["total"] == 25


def test_medium_delete_passes_with_confirm():
    items = [{"name": f"m{i}"} for i in range(25)]
    result = check_blast_radius("delete", items, confirm_destructive=True)
    assert result is None


def test_non_delete_passes_regardless():
    items = [{"name": f"m{i}"} for i in range(100)]
    result = check_blast_radius("update", items)
    assert result is None


def test_would_affect_groups_by_table():
    items = [
        {"name": "a", "table_name": "T1"},
        {"name": "b", "table_name": "T1"},
        {"name": "c", "table_name": "T2"},
    ] * 10  # 30 items
    result = check_blast_radius("delete", items)
    assert result["would_affect"]["by_table"] == {"T1": 20, "T2": 10}
    assert len(result["would_affect"]["first_10_names"]) == 10


def test_handler_blocks_mass_delete_at_root():
    """25-item measures delete via handler → blocked with confirmation_required."""
    from server.handlers.batch_operations_handler import handle_batch_operations
    from unittest.mock import patch

    args = {
        "operation": "measures",
        "batch_operation": "delete",
        "items": [{"name": f"m{i}"} for i in range(25)],
    }
    with patch(
        "core.infrastructure.connection_state.connection_state.is_connected", return_value=True
    ):
        result = handle_batch_operations(args)
    assert result["success"] is False
    assert result["error_type"] == "confirmation_required"


def test_handler_passes_mass_delete_with_confirm_in_options():
    """confirm_destructive inside options bypasses the threshold."""
    from server.handlers.batch_operations_handler import handle_batch_operations
    from unittest.mock import patch

    args = {
        "operation": "measures",
        "batch_operation": "delete",
        "items": [{"name": f"m{i}"} for i in range(25)],
        "options": {"confirm_destructive": True, "dry_run": True},
    }
    with patch(
        "core.infrastructure.connection_state.connection_state.is_connected", return_value=True
    ):
        result = handle_batch_operations(args)
    # dry_run is True so we should reach the dry_run branch, not the blast radius guard
    assert result.get("error_type") != "confirmation_required"


def test_handler_rejects_tables_batch_over_30():
    """31 tables → items_exceeds_cap."""
    from server.handlers.batch_operations_handler import handle_batch_operations

    args = {
        "operation": "tables",
        "batch_operation": "create",
        "items": [{"name": f"T{i}"} for i in range(31)],
    }
    result = handle_batch_operations(args)
    assert result["success"] is False
    assert result["error_type"] == "items_exceeds_cap"
    assert "30" in result["error"]


def test_handler_rejects_relationships_batch_over_100():
    """101 relationships → items_exceeds_cap."""
    from server.handlers.batch_operations_handler import handle_batch_operations

    args = {
        "operation": "relationships",
        "batch_operation": "create",
        "items": [{"name": f"R{i}"} for i in range(101)],
    }
    result = handle_batch_operations(args)
    assert result["success"] is False
    assert result["error_type"] == "items_exceeds_cap"
    assert "100" in result["error"]
