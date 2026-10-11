"""Tests for server.handlers.page_operations_handler — the dispatch shell.

07_Page_Operations is a thin dispatcher that routes ~24 ops to domain engines
(PageOperationsEngine in core/pbip/page_operations_engine.py, FilterEngine via
server/handlers/_internal/filter_helpers, slicer + authoring helpers). The
underlying engines have their own tests; this file pins the *dispatcher's*
contract:

  - pbip_path validation: missing → clean error BEFORE gating
  - operation validation: unknown op → error listing valid operations
  - read-only ops (list / list_filters / list_interactions) route to the engine
    and return its result WITHOUT requiring autonomous mode
  - write ops (create / add_filter / reorder / resize) route to the correct
    engine function with the expected args unpacked
  - reload_required hint propagation on a successful write op (disk writes)
  - missing required arg for a specific write op → clean error (no crash)

Gating is set to OFF via an autouse fixture so dispatch logic is exercised
cleanly; the dedicated gate test (STRICT) turns it back on locally.
conftest.py auto-resets the gate policy cache around every test.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from server.handlers import page_operations_handler as poh

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _gate_off(monkeypatch):
    """Default policy = OFF so the dispatcher logic runs without gate noise."""
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "OFF")
    from core.autonomous.gating import invalidate_policy_cache

    invalidate_policy_cache()


@pytest.fixture
def pbir_project(tmp_path):
    """Build a minimal on-disk PBIR project so file-reading ops (list) work and
    find_definition_folder() resolves to a real definition folder.

    Layout:
        <tmp>/Demo.Report/definition/pages/<pageId>/page.json
    Returns the .Report folder path (accepted by find_definition_folder).
    """
    report = tmp_path / "Demo.Report"
    pages = report / "definition" / "pages"
    page_folder = pages / "abc123"
    page_folder.mkdir(parents=True)
    (page_folder / "page.json").write_text(
        json.dumps({"displayName": "Overview", "width": 1280, "height": 720}),
        encoding="utf-8",
    )
    return str(report)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_missing_pbip_path_returns_clean_error():
    """No pbip_path → structured error, no crash, and it happens BEFORE gating."""
    result = poh.handle_page_operations({"operation": "list"})
    assert result["success"] is False
    assert "pbip_path is required" in result["error"]


def test_unknown_operation_returns_valid_operations(pbir_project):
    result = poh.handle_page_operations({"pbip_path": pbir_project, "operation": "frobnicate"})
    assert result["success"] is False
    assert "Unknown operation: 'frobnicate'" in result["error"]
    # The error lists the valid ops sorted.
    assert "create" in result["error"]
    assert "list_filters" in result["error"]


def test_operation_defaults_to_list(pbir_project):
    """Omitting `operation` defaults to 'list' (read-only) and reads the page."""
    result = poh.handle_page_operations({"pbip_path": pbir_project})
    assert result["success"] is True
    assert result["count"] == 1
    assert result["pages"][0]["display_name"] == "Overview"


# ---------------------------------------------------------------------------
# Read-only routing (no gate required)
# ---------------------------------------------------------------------------


def test_list_routes_to_filesystem_and_returns_pages(pbir_project):
    result = poh.handle_page_operations({"pbip_path": pbir_project, "operation": "list"})
    assert result["success"] is True
    assert result["count"] == 1
    page = result["pages"][0]
    assert page["page_id"] == "abc123"
    assert page["display_name"] == "Overview"
    assert page["width"] == 1280
    # Read-only op: no reload hint attached.
    assert "reload_required" not in result


def test_list_filters_is_read_only_and_skips_gate(pbir_project, monkeypatch):
    """list_filters routes to the filter engine and is NOT gated even under
    STRICT — it's in the read-only set."""
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")
    from core.autonomous.gating import invalidate_policy_cache

    invalidate_policy_cache()

    with patch(
        "server.handlers._internal.filter_helpers._op_list",
        return_value={"success": True, "filters": []},
    ) as mock_list:
        result = poh.handle_page_operations(
            {"pbip_path": pbir_project, "operation": "list_filters", "level": "page"}
        )
    assert result["success"] is True
    mock_list.assert_called_once()
    # Read-only op: no reload hint.
    assert "reload_required" not in result


def test_list_interactions_is_read_only(pbir_project):
    with patch(
        "server.handlers._internal.slicer_helpers._op_list_interactions",
        return_value={"success": True, "interactions": []},
    ) as mock_li:
        result = poh.handle_page_operations(
            {"pbip_path": pbir_project, "operation": "list_interactions", "page_name": "Overview"}
        )
    assert result["success"] is True
    mock_li.assert_called_once()
    assert "reload_required" not in result


# ---------------------------------------------------------------------------
# Write routing — engine called with unpacked args
# ---------------------------------------------------------------------------


def test_create_routes_to_authoring_create_page(pbir_project):
    """create delegates to authoring_handler._op_create_page(args, pbip_path)."""
    with patch(
        "server.handlers.authoring_handler._op_create_page",
        return_value={"success": True, "page_id": "newpage"},
    ) as mock_create:
        result = poh.handle_page_operations(
            {"pbip_path": pbir_project, "operation": "create", "page_name": "New Page"}
        )
    assert result["success"] is True
    mock_create.assert_called_once()
    # First positional arg = the full args dict; second = pbip_path.
    call_args, _ = mock_create.call_args
    assert call_args[0]["page_name"] == "New Page"
    assert call_args[1] == pbir_project


def test_add_filter_routes_to_filter_engine(pbir_project):
    """add_filter delegates to filter_helpers._op_add(args)."""
    with patch(
        "server.handlers._internal.filter_helpers._op_add",
        return_value={"success": True, "filter_name": "f1"},
    ) as mock_add:
        result = poh.handle_page_operations(
            {
                "pbip_path": pbir_project,
                "operation": "add_filter",
                "page_name": "Overview",
                "table": "Sales",
                "field": "Region",
                "filter_type": "Categorical",
            }
        )
    assert result["success"] is True
    mock_add.assert_called_once()
    passed_args = mock_add.call_args[0][0]
    assert passed_args["table"] == "Sales"
    assert passed_args["field"] == "Region"


def test_reorder_routes_to_engine_with_kwargs(pbir_project):
    """reorder unpacks definition_path + page_order kwargs into reorder_pages."""
    with patch(
        "core.pbip.page_operations_engine.reorder_pages",
        return_value={"success": True, "reordered": True},
    ) as mock_reorder:
        result = poh.handle_page_operations(
            {
                "pbip_path": pbir_project,
                "operation": "reorder",
                "page_order": ["abc123", "def456"],
            }
        )
    assert result["success"] is True
    mock_reorder.assert_called_once()
    kwargs = mock_reorder.call_args.kwargs
    assert kwargs["page_order"] == ["abc123", "def456"]
    # definition_path resolved from the project folder.
    assert kwargs["definition_path"].name == "definition"


def test_resize_routes_to_engine_with_kwargs(pbir_project):
    with patch(
        "core.pbip.page_operations_engine.resize_page",
        return_value={"success": True},
    ) as mock_resize:
        result = poh.handle_page_operations(
            {
                "pbip_path": pbir_project,
                "operation": "resize",
                "page_name": "abc123",
                "width": 1600,
                "height": 900,
            }
        )
    assert result["success"] is True
    kwargs = mock_resize.call_args.kwargs
    assert kwargs["page_name"] == "abc123"
    assert kwargs["width"] == 1600
    assert kwargs["height"] == 900


# ---------------------------------------------------------------------------
# Reload-hint propagation on successful write
# ---------------------------------------------------------------------------


def test_successful_write_carries_reload_hint(pbir_project):
    """create is a disk-write op in OPS_REQUIRING_RELOAD — a successful dispatch
    must carry the reload_required hint added by gate_and_wrap."""
    with patch(
        "server.handlers.authoring_handler._op_create_page",
        return_value={"success": True, "page_id": "newpage"},
    ):
        result = poh.handle_page_operations(
            {"pbip_path": pbir_project, "operation": "create", "page_name": "New Page"}
        )
    assert result["success"] is True
    assert result.get("reload_required") is True


def test_failed_write_does_not_carry_reload_hint(pbir_project):
    """A failed dispatch must NOT attach reload_required (op never touched disk)."""
    with patch(
        "server.handlers.authoring_handler._op_create_page",
        return_value={"success": False, "error": "boom"},
    ):
        result = poh.handle_page_operations(
            {"pbip_path": pbir_project, "operation": "create", "page_name": "New Page"}
        )
    assert result["success"] is False
    assert "reload_required" not in result


# ---------------------------------------------------------------------------
# Missing required arg for a specific write op → clean error
# ---------------------------------------------------------------------------


def test_resize_missing_page_name_returns_clean_error(pbir_project):
    """resize without page_name/page_id returns a structured error, not a crash."""
    result = poh.handle_page_operations(
        {"pbip_path": pbir_project, "operation": "resize", "width": 1600}
    )
    assert result["success"] is False
    assert "page_name or page_id is required" in result["error"]
    # Error-shaped → no reload hint.
    assert "reload_required" not in result


def test_reorder_missing_page_order_returns_clean_error(pbir_project):
    result = poh.handle_page_operations({"pbip_path": pbir_project, "operation": "reorder"})
    assert result["success"] is False
    assert "page_order is required" in result["error"]


def test_set_display_missing_display_option_returns_clean_error(pbir_project):
    result = poh.handle_page_operations(
        {"pbip_path": pbir_project, "operation": "set_display", "page_name": "abc123"}
    )
    assert result["success"] is False
    assert "display_option is required" in result["error"]


# ---------------------------------------------------------------------------
# Gate interaction
# ---------------------------------------------------------------------------


def test_destructive_op_blocked_under_strict_policy(pbir_project, monkeypatch):
    """A write op (delete) must be blocked under STRICT when autonomous mode is
    inactive — and the underlying engine must NOT be called."""
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")
    from core.autonomous.gating import invalidate_policy_cache

    invalidate_policy_cache()

    with patch(
        "server.handlers.authoring_handler._op_delete_page",
        return_value={"success": True},
    ) as mock_delete:
        result = poh.handle_page_operations(
            {"pbip_path": pbir_project, "operation": "delete", "page_name": "abc123"}
        )
    assert result["success"] is False
    mock_delete.assert_not_called()


def test_register_page_operations_handler_registers_tool():
    from unittest.mock import MagicMock

    registry = MagicMock()
    poh.register_page_operations_handler(registry)
    registry.register.assert_called_once()
    tool = registry.register.call_args[0][0]
    assert tool.name == "07_Page_Operations"
    assert tool.handler is poh.handle_page_operations
    assert tool.category == "pbip"
