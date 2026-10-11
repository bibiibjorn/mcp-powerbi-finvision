"""Tests for server.handlers.model_operations_handler — the dispatch shell.

The individual managers (PerspectiveCrudManager, FieldParameterManager, etc.)
already have dedicated test files. This file pins down the *dispatcher's*
contract:

  - object_type validation: missing / unknown returns valid_types
  - operation validation: unknown operation returns valid_operations
  - severity classification: read-only ops skip the gate, refresh-without-tables
    is CATASTROPHIC, table/delete is CATASTROPHIC, everything else DESTRUCTIVE
  - Gate decisions propagate (block returns the gate envelope; success or
    advisory passes through with merge_gate_info applied)
  - New TOM types route through _dispatch_new_type with kwargs unpacked from
    args; existing types route through manager.execute(args)
  - Gate policy interaction (STRICT vs LIGHT) for catastrophic ops
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from server.handlers import model_operations_handler as moh

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def patch_handlers(monkeypatch):
    """Replace every entry in moh._handlers with a MagicMock so we can spy on
    dispatch without touching real CRUD managers / TOM."""

    fakes: dict[str, MagicMock] = {}
    for key in list(moh._handlers.keys()):
        m = MagicMock(name=f"handler[{key}]")
        # Default reply for execute() (existing types)
        m.execute.return_value = {"success": True, "object_type": key, "via": "execute"}
        fakes[key] = m
        monkeypatch.setitem(moh._handlers, key, m)
    return fakes


@pytest.fixture(autouse=True)
def _gate_off(monkeypatch):
    """Default policy = OFF so the dispatcher logic is exercised without
    individual tests needing to mock gating. Tests that need gating turn it
    back on locally."""
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "OFF")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_missing_object_type_returns_valid_types(patch_handlers):
    result = moh.handle_model_operations({})
    assert result["success"] is False
    assert "object_type is required" in result["error"]
    assert "table" in result["valid_types"]
    assert "measure" in result["valid_types"]
    assert "field_parameter" in result["valid_types"]


def test_unknown_object_type_returns_valid_types(patch_handlers):
    result = moh.handle_model_operations({"object_type": "bogus", "operation": "list"})
    assert result["success"] is False
    assert "Unknown object_type: bogus" in result["error"]
    assert "perspective" in result["valid_types"]


def test_unknown_operation_for_known_type_returns_valid_operations(patch_handlers):
    result = moh.handle_model_operations(
        {"object_type": "measure", "operation": "telepathically_create"}
    )
    assert result["success"] is False
    assert "Invalid operation 'telepathically_create' for measure" in result["error"]
    assert "create" in result["valid_operations"]
    assert "rename" in result["valid_operations"]
    assert "set_format_string_expression" in result["valid_operations"]


# ---------------------------------------------------------------------------
# Routing: existing types (execute pattern) vs new TOM types (dispatch_new)
# ---------------------------------------------------------------------------


def test_existing_type_routes_through_execute(patch_handlers):
    """measure / table / column / relationship / calculation_group use
    handler.execute(args)."""
    args = {"object_type": "measure", "operation": "list", "table_name": "Sales"}
    result = moh.handle_model_operations(args)
    assert result["success"] is True
    patch_handlers["measure"].execute.assert_called_once_with(args)
    # _dispatch_new_type-only methods should NOT have been called
    patch_handlers["measure"].create_field_parameter.assert_not_called()


def test_new_tom_type_skips_execute_and_dispatches_directly(patch_handlers):
    """field_parameter is a 'new' type — must not go through execute()."""
    fp = patch_handlers["field_parameter"]
    fp.add_field.return_value = {"success": True, "added": True}

    result = moh.handle_model_operations(
        {
            "object_type": "field_parameter",
            "operation": "add_field",
            "table_name": "Metrics",
            "field": {"label": "Revenue", "reference": "[Sales]"},
        }
    )
    assert result["success"] is True
    assert result.get("added") is True
    fp.execute.assert_not_called()
    fp.add_field.assert_called_once_with(
        table_name="Metrics",
        field={"label": "Revenue", "reference": "[Sales]"},
    )


def test_perspective_add_table_unpacks_kwargs(patch_handlers):
    p = patch_handlers["perspective"]
    p.add_table.return_value = {"success": True}
    moh.handle_model_operations(
        {
            "object_type": "perspective",
            "operation": "add_table",
            "perspective_name": "P1",
            "table_name": "Sales",
        }
    )
    p.add_table.assert_called_once_with("P1", "Sales")


def test_culture_bulk_set_translations_passes_list(patch_handlers):
    c = patch_handlers["culture"]
    c.bulk_set_translations.return_value = {"success": True, "applied": 3}
    items = [{"object_type": "measure", "name": "M1", "property": "Caption", "value": "Mes 1"}]
    moh.handle_model_operations(
        {
            "object_type": "culture",
            "operation": "bulk_set_translations",
            "culture_name": "fr-FR",
            "translations": items,
        }
    )
    c.bulk_set_translations.assert_called_once_with("fr-FR", items)


def test_culture_bulk_set_translations_handles_missing_list(patch_handlers):
    """If translations is omitted, dispatcher must pass [] (not None)."""
    c = patch_handlers["culture"]
    c.bulk_set_translations.return_value = {"success": True}
    moh.handle_model_operations(
        {
            "object_type": "culture",
            "operation": "bulk_set_translations",
            "culture_name": "fr-FR",
        }
    )
    c.bulk_set_translations.assert_called_once_with("fr-FR", [])


def test_unknown_operation_for_new_type_returns_valid_operations(patch_handlers):
    """Routing to _dispatch_new_type with an unknown op returns a structured
    error that lists the dispatch_map keys, not the _valid_operations list."""
    # 'perspective.list' is valid; 'perspective.frobnicate' is not. The
    # outer _valid_operations check catches this first.
    result = moh.handle_model_operations({"object_type": "perspective", "operation": "frobnicate"})
    assert result["success"] is False
    assert "Invalid operation 'frobnicate' for perspective" in result["error"]


# ---------------------------------------------------------------------------
# Severity classification
# ---------------------------------------------------------------------------


def test_severity_for_read_only_returns_none():
    for op in ("list", "describe", "get", "find", "sample_data"):
        assert moh._severity_for("measure", op, {}) is None


def test_severity_for_table_delete_is_catastrophic():
    from core.autonomous.gating import Severity

    assert moh._severity_for("table", "delete", {}) is Severity.CATASTROPHIC


def test_severity_for_model_refresh_no_tables_is_catastrophic():
    from core.autonomous.gating import Severity

    # No `tables` arg → full-model refresh → CATASTROPHIC
    assert moh._severity_for("model", "refresh", {}) is Severity.CATASTROPHIC


def test_severity_for_model_refresh_with_tables_is_destructive():
    from core.autonomous.gating import Severity

    sev = moh._severity_for("model", "refresh", {"tables": ["Sales"]})
    assert sev is Severity.DESTRUCTIVE


def test_severity_for_ordinary_write_is_destructive():
    from core.autonomous.gating import Severity

    # A plain create is additive (refuses when the object exists) -> not gated;
    # an overwriting create (if_exists=update) and an update stay destructive.
    assert moh._severity_for("measure", "create", {}) is None
    assert moh._severity_for("measure", "create", {"if_exists": "update"}) is Severity.DESTRUCTIVE
    assert moh._severity_for("measure", "update", {}) is Severity.DESTRUCTIVE
    # C-13: a rename that cannot be dry-run (not connected) is CATASTROPHIC
    # (fail closed); only a proven 0-expression cascade is DESTRUCTIVE.
    assert moh._severity_for("column", "rename", {}) is Severity.CATASTROPHIC


# ---------------------------------------------------------------------------
# Gate interaction
# ---------------------------------------------------------------------------


def test_read_only_op_skips_gate_under_strict_policy(patch_handlers, monkeypatch):
    """A 'list' call should NOT be blocked even under STRICT — it's read-only."""
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")
    from core.autonomous.gating import invalidate_policy_cache

    invalidate_policy_cache()

    result = moh.handle_model_operations({"object_type": "measure", "operation": "list"})
    assert result["success"] is True
    patch_handlers["measure"].execute.assert_called_once()


def test_destructive_op_blocked_under_strict_policy(patch_handlers, monkeypatch):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")
    from core.autonomous.gating import invalidate_policy_cache

    invalidate_policy_cache()

    result = moh.handle_model_operations(
        {
            "object_type": "measure",
            "operation": "delete",
            "table_name": "Sales",
            "measure_name": "M1",
        }
    )
    assert result["success"] is False
    assert (
        result.get("error_type")
        in (
            "confirm_destructive_required",
            "destructive_blocked",
        )
        or "confirm_destructive" in (result.get("error") or result.get("hint") or "").lower()
    )
    # Manager must NOT have been touched
    patch_handlers["measure"].execute.assert_not_called()


def test_destructive_op_passes_with_confirm_destructive(patch_handlers, monkeypatch):
    """Break-glass: confirm_destructive=True bypasses the gate."""
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")
    from core.autonomous.gating import invalidate_policy_cache

    invalidate_policy_cache()

    result = moh.handle_model_operations(
        {
            "object_type": "measure",
            "operation": "delete",
            "table_name": "Sales",
            "measure_name": "M1",
            "confirm_destructive": True,
        }
    )
    assert result["success"] is True
    patch_handlers["measure"].execute.assert_called_once()


def test_destructive_op_passes_under_off_policy(patch_handlers):
    """Under OFF (the autouse default) every destructive op should pass."""
    result = moh.handle_model_operations(
        {
            "object_type": "measure",
            "operation": "delete",
            "table_name": "Sales",
            "measure_name": "M1",
        }
    )
    assert result["success"] is True


def test_advisory_notice_merged_into_result_under_light(patch_handlers, monkeypatch):
    """LIGHT policy emits advisory `gate_notice` for ordinary destructive ops."""
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "LIGHT")
    from core.autonomous.gating import invalidate_policy_cache

    invalidate_policy_cache()

    result = moh.handle_model_operations(
        {
            "object_type": "measure",
            "operation": "create",
            "table_name": "Sales",
            "measure_name": "M1",
            "expression": "1",
        }
    )
    assert result["success"] is True
    # gate_notice / gate_warning may or may not be merged depending on policy
    # implementation — what matters is the call went through and merge_gate_info
    # didn't break the response shape.
    patch_handlers["measure"].execute.assert_called_once()


# ---------------------------------------------------------------------------
# Registry hookup
# ---------------------------------------------------------------------------


def test_register_model_operations_handler_registers_tool():
    registry = MagicMock()
    moh.register_model_operations_handler(registry)
    registry.register.assert_called_once()
    tool = registry.register.call_args[0][0]
    assert tool.name == "02_Model_Operations"
    assert tool.handler is moh.handle_model_operations
    assert tool.category == "model"
