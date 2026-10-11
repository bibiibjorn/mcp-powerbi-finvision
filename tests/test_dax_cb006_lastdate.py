"""CB006 LASTDATE correctness (Phase 0 correctness tail).

CB006 used to fire on EVERY LASTDATE and recommend replacing it with MAX. That
is wrong: LASTDATE is the preferred semi-additive table filter
(CALCULATE([Balance], LASTDATE('Date'[Date]))) — MAX is scalar and not
equivalent there. The rule should only fire when LASTDATE sits in a scalar
COMPARISON context, where MAX is genuinely the better choice.

Exercises the LIVE path (DaxUnifiedAnalyzer / JsonRuleEngine).
"""

from __future__ import annotations

from core.dax.analyzer.unified_analyzer import DaxUnifiedAnalyzer


def _rule_ids(dax: str) -> set:
    result = DaxUnifiedAnalyzer().analyze(dax)
    return {i.rule_id for i in result.issues}


def test_lastdate_as_calculate_filter_not_flagged():
    # Canonical semi-additive pattern — LASTDATE is correct here.
    ids = _rule_ids("CALCULATE ( [Balance], LASTDATE ( 'Date'[Date] ) )")
    assert "CB006" not in ids


def test_lastdate_in_scalar_comparison_is_flagged():
    # In a comparison, MAX('Date'[Date]) is the simpler/scalar choice.
    ids = _rule_ids("IF ( 'Date'[Date] = LASTDATE ( 'Date'[Date] ) , 1 , 0 )")
    assert "CB006" in ids
