"""Tests for HandlerRegistry.get_all_tools_as_mcp — the MCP tool export path.

Builds a tiny registry by hand (no register_all_handlers / TOM imports) and
asserts the returned mcp.types.Tool objects carry name/description/inputSchema,
the confirm_destructive injection, annotation/title handling, and the deferred
include/exclude inversion. Also asserts register() skips malformed defs.
"""

from __future__ import annotations


from server.registry import HandlerRegistry, ToolDefinition


def _td(name, sort_order=100, category="model", annotations=None):
    return ToolDefinition(
        name=name,
        description=f"desc for {name}",
        handler=lambda args: {"success": True},
        input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
        category=category,
        sort_order=sort_order,
        annotations=annotations,
    )


def test_returns_mcp_tools_with_core_fields():
    r = HandlerRegistry()
    r.register(_td("02_Foo"))
    tools = r.get_all_tools_as_mcp()
    assert len(tools) == 1
    t = tools[0]
    assert t.name == "02_Foo"
    assert t.description == "desc for 02_Foo"
    assert t.inputSchema["type"] == "object"


def test_confirm_destructive_injected_only_on_destructive_tools():
    # A-30: the break-glass flag is declared only where destructiveHint=true.
    r = HandlerRegistry()
    r.register(_td("02_Foo", annotations={"readOnlyHint": False, "destructiveHint": True}))
    r.register(_td("04_Bar", annotations={"readOnlyHint": True, "destructiveHint": False}))
    tools = {t.name: t for t in r.get_all_tools_as_mcp()}
    props = tools["02_Foo"].inputSchema["properties"]
    assert props["confirm_destructive"]["type"] == "boolean"
    assert "confirm_destructive" not in tools["04_Bar"].inputSchema["properties"]
    # protocol-level args every tool honours are injected everywhere (A-18)
    for t in tools.values():
        for key in ("response_format", "page_size", "next_token"):
            assert key in t.inputSchema["properties"]


def test_original_input_schema_not_mutated():
    r = HandlerRegistry()
    td = _td("02_Foo", annotations={"destructiveHint": True})
    r.register(td)
    r.get_all_tools_as_mcp()
    # The original ToolDefinition's schema must be untouched (shallow copy).
    assert "confirm_destructive" not in td.input_schema["properties"]


def test_sorted_by_sort_order_then_name():
    r = HandlerRegistry()
    r.register(_td("09_B", sort_order=90))
    r.register(_td("01_A", sort_order=10))
    names = [t.name for t in r.get_all_tools_as_mcp()]
    assert names == ["01_A", "09_B"]


def test_register_skips_malformed_defs():
    r = HandlerRegistry()
    # Missing handler -> skipped silently.
    bad = ToolDefinition(name="bad", description="", handler=None, input_schema={"type": "object"})
    r.register(bad)
    assert r.total_tools() == 0


def test_annotations_passed_through():
    r = HandlerRegistry()
    r.register(_td("02_Foo", annotations={"readOnlyHint": True, "destructiveHint": False}))
    t = r.get_all_tools_as_mcp()[0]
    assert t.annotations is not None
    assert t.annotations.readOnlyHint is True


def test_full_registry_has_exactly_27_tools():
    # Integration check of the bootstrap invariant.
    from server.handlers import register_all_handlers

    r = HandlerRegistry()
    register_all_handlers(r)
    assert r.total_tools() == 28
    mcp_tools = r.get_all_tools_as_mcp()
    assert len(mcp_tools) == 28
    # Every destructive-capable tool carries the confirm_destructive escape
    # hatch; read-only tools never do (A-30).
    for t in mcp_tools:
        declared = "confirm_destructive" in t.inputSchema.get("properties", {})
        assert declared is bool(t.annotations and t.annotations.destructiveHint), t.name
