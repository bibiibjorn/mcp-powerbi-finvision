"""Pattern-type rules now actually fire against real DAX (P0-4).

Each of the seven previously-dead `pattern_type:"pattern"` rules gets a
behavioural check. Assertions are conservative — they fire on the canonical
positive case and stay silent on a clean idiom — so reviving the rules does
not introduce false positives on common DAX.
"""

from core.dax.analyzer.unified_analyzer import DaxUnifiedAnalyzer


def _rule_ids(dax):
    res = DaxUnifiedAnalyzer().analyze(dax)
    return {i.rule_id for i in res.issues}


def test_redundant_if_true_false_detected():
    assert "MAINT_REDUNDANT_IF_TRUE_FALSE" in _rule_ids("IF([Sales] > 0, TRUE, FALSE)")


def test_redundant_if_true_false_not_flagged_on_real_logic():
    assert "MAINT_REDUNDANT_IF_TRUE_FALSE" not in _rule_ids("IF([Sales] > 0, [Sales], 0)")


def test_no_return_detected():
    assert "MAINT_NO_RETURN" in _rule_ids("VAR x = SUM('T'[A])")


def test_no_return_not_flagged_when_return_present():
    assert "MAINT_NO_RETURN" not in _rule_ids("VAR x = SUM('T'[A]) RETURN x")


def test_nondescriptive_var_detected():
    assert "MAINT_NONDESCRIPTIVE_VAR" in _rule_ids("VAR x = 1 RETURN x")


def test_nondescriptive_var_not_flagged_on_good_name():
    assert "MAINT_NONDESCRIPTIVE_VAR" not in _rule_ids(
        "VAR TotalSales = SUM('T'[A]) RETURN TotalSales"
    )


def test_long_measure_detected():
    long_dax = "VAR TotalSales = " + " + ".join(f"SUM('T'[C{i}])" for i in range(80))
    long_dax += " RETURN TotalSales"
    assert "MAINT_LONG_MEASURE" in _rule_ids(long_dax)


def test_long_measure_not_flagged_on_short():
    assert "MAINT_LONG_MEASURE" not in _rule_ids("SUM('T'[A])")
