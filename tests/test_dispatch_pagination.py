"""Uniform pagination at the dispatch layer (Phase 5 polish).

When a caller passes page_size (and optionally next_token), the dispatcher
paginates the result's common list keys via core pagination_helpers — opt-in,
so callers that don't paginate see unchanged behavior. This replaces relying
on the blunt 100-item middleware truncation for large list ops.
"""

from server.dispatch import ToolDispatcher
from server.registry import HandlerRegistry, ToolDefinition


def _registry_with_list_tool(rows):
    reg = HandlerRegistry()
    reg.register(
        ToolDefinition(
            name="99_List_Test",
            description="test list tool",
            handler=lambda args: {"success": True, "measures": list(rows)},
            input_schema={"type": "object", "properties": {}},
            category="query",
            sort_order=999,
            annotations={"readOnlyHint": True},
        )
    )
    return reg


def _dispatcher(monkeypatch, rows):
    reg = _registry_with_list_tool(rows)
    import server.dispatch as d

    monkeypatch.setattr(d, "get_registry", lambda: reg)
    return ToolDispatcher()


def test_no_page_size_returns_all_rows(monkeypatch):
    disp = _dispatcher(monkeypatch, [{"n": i} for i in range(50)])
    res = disp.dispatch("99_List_Test", {})
    assert len(res["measures"]) == 50
    assert "next_token" not in res


def test_page_size_truncates_and_emits_token(monkeypatch):
    disp = _dispatcher(monkeypatch, [{"n": i} for i in range(50)])
    res = disp.dispatch("99_List_Test", {"page_size": 20})
    assert len(res["measures"]) == 20
    assert res["has_more"] is True
    assert res["next_token"]


def test_next_token_continues(monkeypatch):
    disp = _dispatcher(monkeypatch, [{"n": i} for i in range(50)])
    page1 = disp.dispatch("99_List_Test", {"page_size": 20})
    token = page1["next_token"]
    page2 = disp.dispatch("99_List_Test", {"page_size": 20, "next_token": token})
    # Page 2 starts where page 1 left off.
    assert page2["measures"][0]["n"] == 20
    assert len(page2["measures"]) == 20


def test_error_result_not_paginated(monkeypatch):
    reg = HandlerRegistry()
    reg.register(
        ToolDefinition(
            name="99_Err",
            description="err",
            handler=lambda args: {"success": False, "error": "boom", "measures": [1, 2, 3]},
            input_schema={"type": "object", "properties": {}},
            category="query",
            sort_order=999,
            annotations={"readOnlyHint": True},
        )
    )
    import server.dispatch as d

    monkeypatch.setattr(d, "get_registry", lambda: reg)
    disp = ToolDispatcher()
    res = disp.dispatch("99_Err", {"page_size": 1})
    # Pagination is a no-op on failure responses.
    assert res["success"] is False
    assert res["measures"] == [1, 2, 3]
