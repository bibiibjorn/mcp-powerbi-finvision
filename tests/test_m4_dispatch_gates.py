"""M4 parity pack - the dispatch shell of ``02_Model_Operations``: routing of every new
(object_type, operation), the gate severity of each, the read/write classification and the
tools/list surface (deferred args, enums, size).

Managers are replaced by MagicMocks at the ``_handlers`` boundary (as in
test_model_operations_handler.py); TOM is never touched.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from core.autonomous.gating import Severity, invalidate_policy_cache
from server import op_classification as oc
from server.handlers import model_operations_handler as moh

NEW_OPS = [
    ("table", "mark_as_date_table"),
    ("relationship", "rename"),
    ("calculation_group", "update"),
    ("calculation_group", "rename"),
    ("hierarchy", "add_level"),
    ("hierarchy", "remove_level"),
    ("hierarchy", "rename_level"),
    ("model", "set_properties"),
    ("model", "refresh_status"),
    ("model", "refresh_history"),
    ("role", "get_effective_permissions"),
    ("query_group", "list"),
    ("query_group", "create"),
    ("query_group", "update"),
    ("query_group", "delete"),
    ("query_group", "assign"),
    ("query_group", "unassign"),
]


@pytest.fixture
def spies(monkeypatch):
    fakes = {}
    for key in list(moh._handlers):
        m = MagicMock(name=key)
        m.execute.return_value = {"success": True, "via": "execute"}
        fakes[key] = m
        monkeypatch.setitem(moh._handlers, key, m)
    return fakes


@pytest.fixture(autouse=True)
def _gate_off(monkeypatch):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "OFF")
    invalidate_policy_cache()
    yield
    invalidate_policy_cache()


def call(**args):
    return moh.handle_model_operations(args)


# --- validity + classification ------------------------------------------------------------


@pytest.mark.parametrize("object_type,operation", NEW_OPS)
def test_every_new_op_is_advertised_and_classified(object_type, operation):
    assert operation in moh._valid_operations[object_type]
    assert object_type in moh._handlers
    level = oc.classify("02_Model_Operations", {"object_type": object_type, "operation": operation})
    assert level in ("read", "write", "destructive")


@pytest.mark.parametrize(
    "object_type,operation,level",
    [
        ("model", "refresh_status", "read"),
        ("model", "refresh_history", "read"),
        ("role", "get_effective_permissions", "read"),
        ("query_group", "list", "read"),
        ("table", "mark_as_date_table", "write"),
        ("hierarchy", "add_level", "write"),
        ("hierarchy", "rename_level", "write"),
        ("hierarchy", "remove_level", "destructive"),
        ("model", "set_properties", "write"),
        ("query_group", "create", "write"),
        ("query_group", "update", "write"),
        ("query_group", "assign", "write"),
        ("query_group", "unassign", "write"),
        ("query_group", "delete", "destructive"),
        ("calculation_group", "update", "write"),
        ("calculation_group", "rename", "destructive"),
        ("relationship", "rename", "destructive"),
    ],
)
def test_classification_levels(object_type, operation, level):
    assert (
        oc.classify("02_Model_Operations", {"object_type": object_type, "operation": operation})
        == level
    )


def test_a_compatibility_upgrade_is_classified_destructive():
    args = {"object_type": "model", "operation": "set_properties", "compatibility_level": 1707}
    assert oc.classify("02_Model_Operations", args) == "destructive"


def test_rename_previews_are_reads():
    for object_type in ("calculation_group", "relationship"):
        args = {"object_type": object_type, "operation": "rename", "preview": True}
        assert oc.classify("02_Model_Operations", args) == "read"


# --- routing ----------------------------------------------------------------------------------


def test_model_ops_route_with_their_arguments(spies):
    call(object_type="model", operation="refresh_status", table_name="Sales")
    spies["model"].refresh_status.assert_called_once_with(table_name="Sales")
    call(object_type="model", operation="refresh_history", since="1d", limit=5)
    spies["model"].refresh_history.assert_called_once_with(since="1d", limit=5)


def test_set_properties_routes_to_the_properties_manager(monkeypatch, spies):
    props = MagicMock()
    props.set_properties.return_value = {"success": True}
    monkeypatch.setattr(moh, "_model_properties", props)
    call(
        object_type="model",
        operation="set_properties",
        description="d",
        culture="en-US",
        discourage_implicit_measures=True,
        compatibility_level=1707,
        allow_compatibility_upgrade=True,
        confirm_destructive=True,
    )
    props.set_properties.assert_called_once_with(
        description="d",
        culture="en-US",
        discourage_implicit_measures=True,
        compatibility_level=1707,
        allow_compatibility_upgrade=True,
    )


def test_hierarchy_level_ops_route(spies):
    call(
        object_type="hierarchy",
        operation="add_level",
        table_name="T",
        hierarchy_name="H",
        level_name="L",
        column_name="C",
        ordinal=1,
    )
    spies["hierarchy"].add_level.assert_called_once_with(
        "T", "H", level_name="L", column_name="C", ordinal=1
    )
    call(
        object_type="hierarchy",
        operation="remove_level",
        table_name="T",
        hierarchy_name="H",
        level_name="L",
    )
    spies["hierarchy"].remove_level.assert_called_once_with("T", "H", level_name="L")
    call(
        object_type="hierarchy",
        operation="rename_level",
        table_name="T",
        hierarchy_name="H",
        level_name="L",
        new_name="N",
    )
    spies["hierarchy"].rename_level.assert_called_once_with("T", "H", level_name="L", new_name="N")


def test_role_effective_permissions_routes(spies):
    call(
        object_type="role",
        operation="get_effective_permissions",
        role_name="EU",
        table_name="Sales",
    )
    spies["role"].get_effective_permissions.assert_called_once_with(
        role_name="EU", table_name="Sales"
    )


def test_query_group_ops_route(spies):
    qg = spies["query_group"]
    call(object_type="query_group", operation="list")
    qg.list_query_groups.assert_called_once_with()
    call(object_type="query_group", operation="create", query_group="A\\B", description="d")
    qg.create_query_group.assert_called_once_with("A\\B", description="d")
    call(
        object_type="query_group",
        operation="update",
        query_group="A\\B",
        new_name="C",
        description="e",
    )
    qg.update_query_group.assert_called_once_with("A\\B", description="e", new_name="C")
    call(object_type="query_group", operation="delete", query_group="C", force=True)
    qg.delete_query_group.assert_called_once_with("C", force=True)
    call(object_type="query_group", operation="assign", query_group="C", expression_name="X")
    qg.assign.assert_called_with("C", expression_name="X", table_name=None, partition_name=None)
    call(object_type="query_group", operation="unassign", table_name="T", partition_name="P")
    qg.assign.assert_called_with(
        None, expression_name=None, table_name="T", partition_name="P", unassign=True
    )


def test_table_calc_group_and_relationship_ops_go_through_execute(spies):
    for object_type, operation in [
        ("table", "mark_as_date_table"),
        ("calculation_group", "update"),
        ("calculation_group", "rename"),
        ("relationship", "rename"),
    ]:
        out = call(object_type=object_type, operation=operation)
        assert out["via"] == "execute"
        assert spies[object_type].execute.call_args[0][0]["operation"] == operation


def test_unknown_query_group_operation_lists_the_valid_ones(spies):
    out = call(object_type="query_group", operation="merge")
    assert out["error_type"] == "invalid_operation"
    assert out["valid_operations"] == ["list", "create", "update", "delete", "assign", "unassign"]


# --- gate severity ---------------------------------------------------------------------------


def test_compatibility_upgrade_is_catastrophic_other_properties_are_not():
    sev = moh._severity_for
    assert sev("model", "set_properties", {"compatibility_level": 1707}) is Severity.CATASTROPHIC
    assert sev("model", "set_properties", {"description": "x"}) is Severity.DESTRUCTIVE


def test_calculation_group_rename_is_catastrophic_unless_previewed():
    sev = moh._severity_for
    assert sev("calculation_group", "rename", {}) is Severity.CATASTROPHIC
    assert sev("calculation_group", "rename", {"preview": True}) is None
    assert sev("relationship", "rename", {}) is Severity.DESTRUCTIVE


def test_indexes_refresh_is_only_destructive_and_reads_are_ungated():
    sev = moh._severity_for
    assert sev("model", "refresh", {"refresh_type": "indexes"}) is Severity.DESTRUCTIVE
    assert sev("model", "refresh", {"refresh_type": "full"}) is Severity.CATASTROPHIC
    for object_type, op in [
        ("model", "refresh_status"),
        ("model", "refresh_history"),
        ("role", "get_effective_permissions"),
        ("query_group", "list"),
    ]:
        assert sev(object_type, op, {}) is None


def test_hierarchy_add_level_is_additive_so_it_carries_no_advisory():
    assert moh._severity_for("hierarchy", "add_level", {}) is None


def test_light_policy_blocks_a_compatibility_upgrade_without_confirmation(monkeypatch, spies):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "LIGHT")
    invalidate_policy_cache()
    props = MagicMock()
    props.set_properties.return_value = {"success": True}
    monkeypatch.setattr(moh, "_model_properties", props)
    blocked = call(
        object_type="model",
        operation="set_properties",
        compatibility_level=1707,
        allow_compatibility_upgrade=True,
    )
    assert blocked["success"] is False
    props.set_properties.assert_not_called()
    ok = call(
        object_type="model",
        operation="set_properties",
        compatibility_level=1707,
        allow_compatibility_upgrade=True,
        confirm_destructive=True,
    )
    assert ok["success"] is True
    props.set_properties.assert_called_once()


def test_calculation_group_rename_needs_confirmation_under_light(monkeypatch, spies):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "LIGHT")
    invalidate_policy_cache()
    blocked = call(
        object_type="calculation_group", operation="rename", group_name="T", new_name="U"
    )
    assert blocked["success"] is False
    spies["calculation_group"].execute.assert_not_called()
    preview = call(
        object_type="calculation_group",
        operation="rename",
        group_name="T",
        new_name="U",
        preview=True,
    )
    assert preview["success"] is True


# --- tools/list surface --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def registry():
    from server.handlers import register_all_handlers
    from server.registry import HandlerRegistry

    reg = HandlerRegistry()
    register_all_handlers(reg)
    return reg


NEW_ARGS = [
    "culture",
    "discourage_implicit_measures",
    "compatibility_level",
    "allow_compatibility_upgrade",
    "level_name",
    "query_group",
    "string_indexing_behavior",
    "full_text_indexing_behavior",
]


def test_new_args_are_declared_but_deferred_out_of_tools_list(registry):
    declared = registry.declared_properties("02_Model_Operations")
    emitted = registry.get_emitted_schema("02_Model_Operations")["properties"]
    for name in NEW_ARGS:
        assert name in declared, name
        assert name not in emitted, name


def test_object_type_and_refresh_type_enums_are_extended(registry):
    props = registry.get_emitted_schema("02_Model_Operations")["properties"]
    assert "query_group" in props["object_type"]["enum"]
    assert "indexes" in props["refresh_type"]["enum"]
    full = registry.get_full_schema("02_Model_Operations")["properties"]
    assert full["string_indexing_behavior"]["enum"] == ["auto", "full", "explicit", "off"]
    assert full["full_text_indexing_behavior"]["enum"] == ["full", "explicit", "off"]


def test_tools_list_stays_inside_the_budget(registry):
    payload = [
        t.model_dump(by_alias=True, exclude_none=True, mode="json")
        for t in registry.get_all_tools_as_mcp()
    ]
    # +800 for the 28th tool (00_Client_Mode, Client Guard): same exception as the registry budget
    # test (tests/test_review_a_registry.py). Never raise it for anything else.
    assert len(json.dumps(payload)) < 76_300
