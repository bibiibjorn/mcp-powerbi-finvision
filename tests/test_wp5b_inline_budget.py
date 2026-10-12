"""F-34: soft inline budget below the 450K spill (server/response_pipeline + response_format)."""

from __future__ import annotations

import json

import pytest

import server.response_pipeline as rp
from core.validation import response_format as rf

BUDGET = 20_000


def _rows(n, pad=200):
    return [{"i": i, "pad": "x" * pad} for i in range(n)]


def _render(result, **kw):
    return rp.render_result("04_Run_DAX", result, soft_budget_chars=BUDGET, **kw)


def test_over_budget_list_returns_first_page_with_next_token():
    result = {"success": True, "rows": _rows(500), "row_count": 500, "execution_time_ms": 4}
    out = _render(result)
    body = json.loads(out.text)
    assert out.spilled_path is None and len(out.text) <= BUDGET
    assert body["truncated_inline"] is True and body["has_more"] is True
    shown = body["inline_truncation"]["shown"]
    assert 1 <= shown < 500 and len(body["rows"]) == shown
    assert body["next_token"] == f"rows:{shown}" and body["paginated_key"] == "rows"
    assert body["inline_truncation"]["total"] == 500
    # scalars and the untouched items survive; nothing is clipped
    assert body["row_count"] == 500 and body["rows"][0] == result["rows"][0]
    assert all(len(r["pad"]) == 200 for r in body["rows"])


def test_the_next_token_works_with_the_dispatchers_existing_pagination():
    from core.validation.pagination_helpers import paginate

    result = {"success": True, "rows": _rows(500)}
    body = json.loads(_render(result).text)
    shown = body["inline_truncation"]["shown"]
    page2 = paginate(
        {"success": True, "rows": _rows(500)},
        page_size=shown,
        next_token=body["next_token"],
        list_keys=["rows"],
    )
    assert page2["rows"][0]["i"] == shown  # continues exactly where the cut stopped


def test_under_budget_is_verbatim():
    result = {"success": True, "rows": _rows(5)}
    out = _render(result)
    assert json.loads(out.text) == result


def test_budget_is_off_by_default_for_direct_callers():
    result = {"success": True, "rows": _rows(500)}
    out = rp.render_result("04_Run_DAX", result)  # no soft_budget_chars
    assert "truncated_inline" not in json.loads(out.text)


def test_dax_text_is_never_clipped():
    dax = "VAR x = 1 RETURN " + "SUM(T[a]) + " * 300  # ~3.9k chars per item
    result = {
        "success": True,
        "measures": [{"name": f"m{i}", "expression": dax} for i in range(40)],
    }
    body = json.loads(_render(result).text)
    assert body["truncated_inline"] is True
    assert all(m["expression"] == dax for m in body["measures"])


@pytest.mark.parametrize(
    "result",
    [
        {"success": False, "error": "boom", "rows": _rows(500)},  # errors untouched
        {
            "success": True,
            "rows": _rows(500),
            "paginated_key": "rows",
            "has_more": False,
        },  # explicit page_size
        {
            "success": True,
            "rows": _rows(500),
            "next_token": "rows:5",
            "has_more": True,
        },  # handler paginates
        {"success": True, "text": "x" * 40_000},  # no list
        {"success": True, "rows": [{"pad": "x" * 40_000}]},  # one huge item: nothing to cut
    ],
)
def test_not_applicable_leaves_the_result_alone(result):
    out = _render(result)
    assert json.loads(out.text) == result


def test_opt_out_marker_skips_the_budget_and_is_not_serialized():
    result = {"success": True, "rows": _rows(500), rf.INLINE_BUDGET_OFF_KEY: True}
    body = json.loads(_render(result).text)
    assert "truncated_inline" not in body and len(body["rows"]) == 500
    assert rf.INLINE_BUDGET_OFF_KEY not in body


def test_hard_cap_still_spills_when_the_cut_result_is_still_too_big(tmp_path):
    result = {"success": True, "rows": [{"pad": "x" * 30_000}], "blob": "y" * 5_000}
    out = rp.render_result(
        "04_Run_DAX",
        result,
        inline_max_chars=10_000,
        spill_directory=tmp_path,
        soft_budget_chars=BUDGET,
    )
    assert out.spilled_path is not None


def test_effective_budget_never_exceeds_the_tool_cap():
    result = {"success": True, "rows": _rows(500)}
    out = rp.render_result("04_Run_DAX", result, inline_max_chars=8_000, soft_budget_chars=BUDGET)
    assert len(out.text) <= 8_000 and json.loads(out.text)["truncated_inline"] is True


# --- helpers ----------------------------------------------------------------------------------


def test_main_list_key_matches_the_dispatchers_choice():
    from server.dispatch import ToolDispatcher, _PAGINABLE_KEYS

    assert tuple(_PAGINABLE_KEYS) == rf.PAGINABLE_KEYS
    cases = [
        {"rows": [1], "measures": [1, 2]},
        {"zeta": [1], "alpha": [1, 2, 3], "_private": list(range(9))},
        {"success": True},
    ]
    for case in cases:
        got = ToolDispatcher._pagination_keys(case, None)
        assert rf.main_list_key(case) == (got[0] if got else None)


def test_default_response_format_only_for_list_heavy_ops_without_a_choice():
    d = rf.default_response_format
    assert d("04_Query_Operations", {"operation": "search_objects"}) == "concise"
    assert d("16_Dependency_Graph", {"operation": "find_unused"}) == "concise"
    assert d("02_Model_Operations", {"operation": "list", "include": "expression"}) == "concise"
    assert d("02_Model_Operations", {"operation": "list"}) is None  # plain list stays full
    assert d("04_Run_DAX", {"query": "EVALUATE x"}) is None
    # any explicit choice wins
    assert (
        d("04_Query_Operations", {"operation": "search_objects", "response_format": "detailed"})
        is None
    )
    assert d("04_Query_Operations", {"operation": "search_objects", "page_size": 10}) is None
    assert d("04_Query_Operations", {"operation": "search_objects", "full": True}) is None


def test_inline_budget_opt_out_rules():
    o = rf.inline_budget_opted_out
    assert o({"page_size": 50}) is True
    assert o({"response_format": "detailed", "full": True}) is True
    assert o({"response_format": "detailed"}) is False
    assert o({"full": True}) is False  # full alone is not enough: say detailed
    assert o({"response_format": "concise", "full": True}) is False
    assert o({}) is False


def test_soft_inline_budget_reads_config_and_falls_back(monkeypatch):
    from core.config import config_manager

    monkeypatch.setattr(
        config_manager.config,
        "get",
        lambda k, d=None: 12345 if k == "response.inline_soft_chars" else d,
    )
    assert rf.soft_inline_budget() == 12345
    monkeypatch.setattr(config_manager.config, "get", lambda k, d=None: d)
    assert rf.soft_inline_budget() == rf.DEFAULT_INLINE_SOFT_CHARS
    monkeypatch.setattr(config_manager.config, "get", lambda k, d=None: 0)
    assert rf.soft_inline_budget() == 0
