"""Tests for the three new heuristic rules:
- callback_in_xmsql
- dq_query_in_plan
- agg_rewrite_no_match

These rules consume the extended xmsql_summary fields populated from the
new trace event coverage.
"""

from __future__ import annotations

from core.query_plan import heuristics

_EMPTY_TREE = {"operator": "ROOT", "raw": "", "children": [], "depth": 0}
_EMPTY_TIMINGS = {}


def _findings_by_rule(verdict_dict):
    return {f["rule"]: f for f in verdict_dict["findings"]}


def test_callback_in_xmsql_warn(monkeypatch):
    xmsql = {"callback_count": 3}
    out = heuristics.analyze(_EMPTY_TREE, xmsql, _EMPTY_TIMINGS)
    by_rule = _findings_by_rule(out)
    assert "callback_in_xmsql" in by_rule
    assert by_rule["callback_in_xmsql"]["severity"] == "warn"
    assert by_rule["callback_in_xmsql"]["value"] == 3


def test_callback_in_xmsql_hot():
    xmsql = {"callback_count": 9}
    out = heuristics.analyze(_EMPTY_TREE, xmsql, _EMPTY_TIMINGS)
    by_rule = _findings_by_rule(out)
    assert by_rule["callback_in_xmsql"]["severity"] == "hot"
    # Verdict promotes to hot at top level
    assert out["verdict"] == "hot"


def test_callback_in_xmsql_zero_no_finding():
    out = heuristics.analyze(_EMPTY_TREE, {"callback_count": 0}, _EMPTY_TIMINGS)
    assert "callback_in_xmsql" not in _findings_by_rule(out)


def test_dq_query_in_plan_info():
    xmsql = {"dq_query_count": 2, "dq_total_ms": 50}
    out = heuristics.analyze(_EMPTY_TREE, xmsql, _EMPTY_TIMINGS)
    by_rule = _findings_by_rule(out)
    assert by_rule["dq_query_in_plan"]["severity"] == "info"
    assert by_rule["dq_query_in_plan"]["value"] == 2
    assert "50ms" in by_rule["dq_query_in_plan"]["hint"]


def test_dq_query_in_plan_zero_no_finding():
    out = heuristics.analyze(_EMPTY_TREE, {"dq_query_count": 0}, _EMPTY_TIMINGS)
    assert "dq_query_in_plan" not in _findings_by_rule(out)


def test_agg_rewrite_no_match():
    xmsql = {"agg_rewrite_nomatch_count": 1}
    out = heuristics.analyze(_EMPTY_TREE, xmsql, _EMPTY_TIMINGS)
    by_rule = _findings_by_rule(out)
    assert by_rule["agg_rewrite_no_match"]["severity"] == "info"


def test_agg_rewrite_no_match_zero():
    out = heuristics.analyze(_EMPTY_TREE, {"agg_rewrite_nomatch_count": 0}, _EMPTY_TIMINGS)
    assert "agg_rewrite_no_match" not in _findings_by_rule(out)


def test_list_rules_includes_new_ones():
    rules = {r["id"] for r in heuristics.list_rules()}
    assert "callback_in_xmsql" in rules
    assert "dq_query_in_plan" in rules
    assert "agg_rewrite_no_match" in rules
