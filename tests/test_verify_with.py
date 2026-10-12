"""``verify_with`` on ``timeout_outcome_unknown`` / ``write_in_progress``.

Bug (spike 2026-09-30): a timed-out ``02_Model_Operations model/refresh`` suggested
``{"operation": "list", "object_type": "model"}`` - an invalid op (``model`` supports only
refresh / get_properties / set_value_filter_behavior). Every suggestion must now name an
operation the target tool accepts, and it must be a READ.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

import server.dispatch as dispatch_mod
from server.dispatch import ToolDispatcher
from server.handlers import register_all_handlers
from server.handlers.model_operations_handler import _valid_operations
from server.op_classification import READ, classify, tool_levels
from server.registry import HandlerRegistry
from tests._mcp_harness import make_dispatcher, tool


@pytest.fixture(scope="module")
def registry():
    reg = HandlerRegistry()
    register_all_handlers(reg)
    return reg


def _schema_ops(registry, tool_name):
    td = registry._handlers.get(tool_name)
    props = td.input_schema.get("properties", {}) if td else {}
    return list((props.get("operation") or {}).get("enum") or [])


def _check_valid(registry, verify):
    """The suggestion names a registered tool and an op that tool accepts; returns the call."""
    assert set(verify) >= {"tool", "args"}
    tool_name, args = verify["tool"], verify["args"]
    assert registry.has_tool(tool_name), tool_name
    if tool_name == "02_Model_Operations":
        ot, op = args.get("object_type"), args.get("operation")
        assert ot in _valid_operations, (ot, args)
        assert op in _valid_operations[ot], f"{op!r} is not a valid {ot} operation: {args}"
    elif tool_name == "04_Run_DAX":
        assert args["query"].lstrip().upper().startswith("EVALUATE")
    else:
        ops = _schema_ops(registry, tool_name)
        if ops:
            assert args.get("operation") in ops, f"{tool_name}: {args}"
    return verify


def _write_capable_tools(registry):
    return sorted(td.name for td in registry.get_all_tools() if tool_levels(td.name) - {READ})


def test_model_refresh_no_longer_suggests_an_invalid_list_op(registry):
    v = ToolDispatcher._verify_with(
        "02_Model_Operations", {"object_type": "model", "operation": "refresh"}
    )
    assert v["tool"] == "04_Run_DAX" and v["args"]["query"] == "EVALUATE INFO.PARTITIONS()"
    assert "get_properties" in v["why"]
    assert "operation" not in v["args"]
    # the old suggestion was {"operation": "list", "object_type": "model"}
    assert "list" not in _valid_operations["model"]
    _check_valid(registry, v)


@pytest.mark.parametrize("object_type", sorted(_valid_operations))
def test_every_model_object_type_gets_a_valid_read(registry, object_type):
    v = ToolDispatcher._verify_with(
        "02_Model_Operations", {"object_type": object_type, "operation": "create"}
    )
    _check_valid(registry, v)
    assert classify(v["tool"], v["args"]) == READ
    if "list" in _valid_operations[object_type]:
        assert v["args"] == {"object_type": object_type, "operation": "list"}


def test_model_call_without_or_with_an_unknown_object_type_still_gets_a_valid_read(registry):
    for args in ({}, {"operation": "create"}, {"object_type": "nonsense"}, {"object_type": None}):
        v = _check_valid(registry, ToolDispatcher._verify_with("02_Model_Operations", args))
        assert classify(v["tool"], v["args"]) == READ


def test_table_scope_is_carried_over(registry):
    v = ToolDispatcher._verify_with(
        "02_Model_Operations", {"object_type": "column", "table_name": "Sales"}
    )
    assert v["args"] == {"operation": "list", "object_type": "column", "table_name": "Sales"}


@pytest.mark.parametrize(
    "batch_type,expected",
    [
        ("measures", "measure"),
        ("columns", "column"),
        ("tables", "table"),
        ("changeset", "table"),
        (None, "table"),
    ],
)
def test_batch_operations_verify_through_the_model_list(registry, batch_type, expected):
    args = {"batch_operation": "create"}
    if batch_type:
        args["object_type"] = batch_type
    v = _check_valid(registry, ToolDispatcher._verify_with("03_Batch_Operations", args))
    assert v == {
        "tool": "02_Model_Operations",
        "args": {"operation": "list", "object_type": expected},
    }


def test_pbip_authoring_verifies_through_the_page_list(registry):
    v = _check_valid(
        registry, ToolDispatcher._verify_with("11_PBIP_Authoring", {"pbip_path": "C:/x/R.pbip"})
    )
    assert v == {
        "tool": "07_Page_Operations",
        "args": {"operation": "list", "pbip_path": "C:/x/R.pbip"},
    }


def test_every_write_capable_tool_gets_a_valid_read_only_suggestion(registry):
    """The regression guard: whatever a new tool adds, its verify_with must be valid + a read."""
    tools = _write_capable_tools(registry)
    assert "02_Model_Operations" in tools and "17_Desktop_Bridge" in tools
    for name in tools:
        variants = [{}, {"operation": "create"}, {"pbip_path": "C:/x/R.pbip"}]
        if name == "02_Model_Operations":
            variants += [{"object_type": ot} for ot in _valid_operations]
        if name == "03_Batch_Operations":
            variants += [
                {"object_type": ot} for ot in ("measures", "tables", "columns", "changeset")
            ]
        for args in variants:
            v = _check_valid(registry, ToolDispatcher._verify_with(name, args))
            assert classify(v["tool"], v["args"]) == READ, (name, args, v)


def test_static_verify_table_only_names_valid_ops(registry):
    """Every entry of the static table names an op in the tool's own enum (or none)."""
    for name, args in dispatch_mod._VERIFY_WITH.items():
        ops = _schema_ops(registry, name)
        if "operation" in args and ops:
            assert args["operation"] in ops, name


def test_the_timeout_envelope_of_a_model_refresh_carries_the_valid_read(monkeypatch):
    release = threading.Event()

    def refresh(args):
        release.wait(5)
        return {"success": True}

    d = make_dispatcher(
        tool("02_Model_Operations", refresh, read_only=False, destructive=True, category="model")
    )
    monkeypatch.setattr(d, "_get_timeout", lambda t: 0.3)
    try:
        out = asyncio.run(
            d.dispatch_async(
                "02_Model_Operations", {"object_type": "model", "operation": "refresh"}
            )
        )
    finally:
        release.set()
    time.sleep(0.2)
    assert out["error_type"] == "timeout_outcome_unknown"
    assert out["verify_with"]["tool"] == "04_Run_DAX"
    assert out["verify_with"]["args"]["query"] == "EVALUATE INFO.PARTITIONS()"
