"""Uniform `response_format: concise|detailed` control.

`detailed` is today's shape (the default); `concise` shrinks verbose fields for
an agent that is chaining/scanning. Applied at the dispatch layer, implemented
as a pure function in core/validation/response_format.py.
"""

import copy

import pytest

from core.validation.response_format import (
    CONCISE_LIST_LIMIT,
    CONCISE_STRING_LIMIT,
    apply_concise,
    normalize_response_format,
)
from server.dispatch import ToolDispatcher
from server.registry import HandlerRegistry, ToolDefinition

# --------------------------------------------------------------------------
# pure function
# --------------------------------------------------------------------------


def _verbose_result():
    return {
        "success": True,
        "measures": [{"name": f"M{i}"} for i in range(40)],
        "dax": "EVALUATE " + "x" * 900,
        "raw_stdout": "noise",
        "raw_stderr": "noise",
        "formatted_output": "pretty",
        "recommendations": ["a", "b"],
        "suggestions": ["c"],
        "notes": ["d"],
        "diagram": "graph TD",
        "mermaid": "graph TD",
        "html": "<html/>",
        "description": "z" * 800,
        "expression": "CALCULATE(" + "y" * 900 + ")",
        "keep": 42,
    }


def test_concise_drops_verbose_keys():
    out = apply_concise(_verbose_result())
    for key in (
        "raw_stdout",
        "raw_stderr",
        "formatted_output",
        "recommendations",
        "suggestions",
        "notes",
        "diagram",
        "mermaid",
        "html",
    ):
        assert key not in out
    assert out["keep"] == 42
    assert out["_response_format"] == "concise"


def test_concise_caps_lists_and_records_counts():
    out = apply_concise(_verbose_result())
    assert len(out["measures"]) == CONCISE_LIST_LIMIT
    assert out["_concise_capped"]["measures"] == {
        "shown": CONCISE_LIST_LIMIT,
        "total": 40,
    }


def test_concise_truncates_long_strings_but_not_expression():
    src = _verbose_result()
    out = apply_concise(src)
    assert len(out["description"]) == CONCISE_STRING_LIMIT + 1
    assert out["description"].endswith("…")
    # DAX text is load-bearing for chaining — never clipped.
    assert out["expression"] == src["expression"]


def test_concise_keeps_dict_that_would_otherwise_empty():
    # A result whose ONLY content is a droppable key keeps it.
    out = apply_concise({"recommendations": ["only", "content"]})
    assert out["recommendations"] == ["only", "content"]


def test_concise_recurses_into_nested_structures():
    src = {
        "success": True,
        "pages": [
            {"name": "P1", "notes": ["x"], "visuals": [{"id": i} for i in range(30)]},
        ],
    }
    out = apply_concise(src)
    page = out["pages"][0]
    assert "notes" not in page
    assert len(page["visuals"]) == CONCISE_LIST_LIMIT


def test_concise_does_not_mutate_input():
    src = _verbose_result()
    snapshot = copy.deepcopy(src)
    apply_concise(src)
    assert src == snapshot


def test_concise_depth_guard_does_not_blow_stack():
    node: dict = {"leaf": True}
    for _ in range(100):
        node = {"child": node}
    node["success"] = True
    out = apply_concise(node)  # must not raise RecursionError
    assert out["_response_format"] == "concise"


def test_concise_returns_non_dict_unchanged():
    assert apply_concise("nope") == "nope"  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value,expected",
    [
        ("concise", "concise"),
        ("  CONCISE ", "concise"),
        ("Detailed", "detailed"),
        ("verbose", None),
        (None, None),
        (5, None),
    ],
)
def test_normalize_response_format(value, expected):
    assert normalize_response_format(value) == expected


# --------------------------------------------------------------------------
# dispatch layer
# --------------------------------------------------------------------------


def _dispatcher(monkeypatch, payload):
    reg = HandlerRegistry()
    reg.register(
        ToolDefinition(
            name="99_Format_Test",
            description="test tool",
            handler=lambda args: copy.deepcopy(payload),
            input_schema={"type": "object", "properties": {}},
            category="query",
            sort_order=999,
            annotations={"readOnlyHint": True},
        )
    )
    import server.dispatch as d

    monkeypatch.setattr(d, "get_registry", lambda: reg)
    return ToolDispatcher()


def test_absent_response_format_is_a_no_op(monkeypatch):
    payload = _verbose_result()
    disp = _dispatcher(monkeypatch, payload)
    res = disp.dispatch("99_Format_Test", {})
    assert res == payload


def test_detailed_preserves_shape(monkeypatch):
    payload = _verbose_result()
    disp = _dispatcher(monkeypatch, payload)
    res = disp.dispatch("99_Format_Test", {"response_format": "detailed"})
    assert res.pop("_response_format") == "detailed"
    assert res == payload


def test_invalid_response_format_is_a_no_op_but_visible(monkeypatch):
    payload = _verbose_result()
    disp = _dispatcher(monkeypatch, payload)
    res = disp.dispatch("99_Format_Test", {"response_format": "tiny"})
    assert res.pop("_response_format_ignored") == "tiny"
    assert res == payload


def test_concise_shrinks_result_at_dispatch(monkeypatch):
    disp = _dispatcher(monkeypatch, _verbose_result())
    res = disp.dispatch("99_Format_Test", {"response_format": "concise"})
    assert res["_response_format"] == "concise"
    assert "recommendations" not in res
    assert len(res["measures"]) == CONCISE_LIST_LIMIT


def test_concise_leaves_error_result_intact(monkeypatch):
    payload = {
        "success": False,
        "error": "boom",
        "error_type": "not_connected",
        "recommendations": ["reconnect"],
        "notes": ["x"],
    }
    disp = _dispatcher(monkeypatch, payload)
    res = disp.dispatch("99_Format_Test", {"response_format": "concise"})
    assert res["success"] is False
    assert res["recommendations"] == ["reconnect"]
    assert res["notes"] == ["x"]
    assert "_response_format" not in res


def test_concise_composes_with_page_size_pagination(monkeypatch):
    payload = {"success": True, "measures": [{"n": i} for i in range(50)], "notes": ["x"]}
    disp = _dispatcher(monkeypatch, payload)
    res = disp.dispatch("99_Format_Test", {"page_size": 10, "response_format": "concise"})
    # Pagination still ran first (10 items, continuation token emitted)...
    assert len(res["measures"]) == 10
    assert res["has_more"] is True
    assert res["next_token"]
    # ...and concise still shrank the result.
    assert res["_response_format"] == "concise"
    assert "notes" not in res


def test_concise_keeps_dax_text_whole():
    """L-19 / A-19 (wave 3): ``dax`` is load-bearing (09_Debug's filter-context
    summary carries each slicer filter's DAX under it) - never dropped, never clipped."""
    out = apply_concise(_verbose_result())
    assert out["dax"] == "EVALUATE " + "x" * 900
    nested = apply_concise(
        {"success": True, "filters": [{"table": "Date", "dax": "'Date'[Year] = 2026"}]}
    )
    assert nested["filters"][0]["dax"] == "'Date'[Year] = 2026"
