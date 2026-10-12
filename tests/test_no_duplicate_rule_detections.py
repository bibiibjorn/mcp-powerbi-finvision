"""Phase 2: duplicate Python rules that mirror live JSON rules are removed.

Several Python rules detected the exact same anti-pattern as an already-live
JSON rule, so the analyzer emitted two findings (different rule_ids) for one
issue — noise to the user. The JSON rule is kept as canonical; the Python
duplicate is removed. This test pins that each anti-pattern yields exactly one
detection from the relevant family.
"""

from core.dax.analyzer.unified_analyzer import DaxUnifiedAnalyzer


def _ids(dax):
    return [i.rule_id for i in DaxUnifiedAnalyzer().analyze(dax).issues]


def test_unused_var_single_detection():
    ids = _ids("VAR x = 1 RETURN 2")
    assert "PY_UNUSED_VAR" not in ids, "PY_UNUSED_VAR duplicate should be removed"
    assert "MAINT_UNUSED_VAR" in ids, "canonical JSON unused-var rule must remain"


def test_bare_table_filter_single_detection():
    ids = _ids("SUMX(FILTER(Sales, Sales[Y]=1), Sales[A])")
    assert "PY_FILTER_BARE_TABLE" not in ids, "PY_FILTER_BARE_TABLE duplicate should be removed"
    assert "CB004" in ids, "canonical JSON bare-table rule (CB004) must remain"


def test_filter_all_single_detection():
    ids = _ids("CALCULATE([M], FILTER(ALL(Cust), [Age]>30))")
    assert (
        "PY_FILTER_ALL_MATERIALIZATION" not in ids
    ), "PY_FILTER_ALL_MATERIALIZATION duplicate should be removed"
    assert "PERF_FILTER_ALL" in ids, "canonical JSON FILTER(ALL()) rule must remain"


def test_blank_propagation_single_detection():
    ids = _ids("1 - DIVIDE([a],[b])")
    assert "PY_BLANK_PROPAGATION" not in ids, "PY_BLANK_PROPAGATION duplicate should be removed"
    assert "CORR_BLANK_PROPAGATION" in ids, "canonical JSON blank-propagation rule must remain"
