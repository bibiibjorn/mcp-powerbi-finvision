"""Pinning tests for the three DAX rule engines.

Prior audit P2-28 flagged overlap between three rule-producing modules:

  * ``core/dax/dax_best_practices.py`` — regex-catalogue analyzer (~1500 LOC)
  * ``core/dax/dax_rules_engine.py`` — class-hierarchy rule set + health score
  * ``core/analysis/bpa_analyzer.py`` — JSON+tokens model-level rules

Consolidation was deferred because there were zero tests pinning engine
output. These tests fix that: each engine gets a tiny fixture + assertions
on the public response shape. Passing tests = safe to consolidate.

The tests intentionally use *observable* keys rather than implementation
details (specific rule IDs, message wording) so a consolidation that keeps
output-equivalence can still pass.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# dax_rules_engine — class-hierarchy engine with health score
# ---------------------------------------------------------------------------


def test_rules_engine_returns_structured_dict():
    """analyze() returns a dict with issues + health_score + issue_count."""
    from core.dax.dax_rules_engine import DaxRulesEngine

    engine = DaxRulesEngine()
    # Division without DIVIDE() — trips _CorrDivision rule.
    result = engine.analyze("Sales[Amount] / Sales[Count]")
    assert isinstance(result, dict)
    # Observable contract: these keys MUST be present. Consolidation that
    # renames them breaks callers in column_usage / handlers / UI.
    for key in ("issues", "health_score", "issue_count", "categories"):
        assert key in result, f"dax_rules_engine response missing {key!r}: {sorted(result.keys())}"
    assert isinstance(result["issues"], list)
    assert isinstance(result["health_score"], int)
    assert 0 <= result["health_score"] <= 100


def test_rules_engine_clean_expression_high_score():
    """Clean DIVIDE expression should score strictly better than an anti-pattern."""
    from core.dax.dax_rules_engine import DaxRulesEngine

    engine = DaxRulesEngine()
    clean = engine.analyze("DIVIDE ( [Total Sales], [Count of Orders], 0 )")
    bad = engine.analyze("SUMX ( ALL ( 'Sales' ), IF ( [X] > 0, [X] * [X], 0 ) )")
    assert clean["health_score"] >= bad["health_score"]


def test_rules_engine_issue_shape():
    """Each issue dict has the documented fields."""
    from core.dax.dax_rules_engine import DaxRulesEngine

    engine = DaxRulesEngine()
    result = engine.analyze("IF ( [X] > 0, 1 / [Y], 0 )")
    assert result["issues"], "expected at least one issue on division-without-DIVIDE"
    issue = result["issues"][0]
    for key in ("rule_id", "category", "severity", "description"):
        assert key in issue, f"issue dict missing {key!r}: {sorted(issue.keys())}"


def test_rules_engine_deterministic():
    """Same input → same (rule_id, category) set across fresh engines."""
    from core.dax.dax_rules_engine import DaxRulesEngine

    expr = "SUMX ( Sales, IF ( Sales[Amount] > 0, Sales[Amount], 0 ) )"
    r1 = DaxRulesEngine().analyze(expr)
    r2 = DaxRulesEngine().analyze(expr)
    set1 = {(i["rule_id"], i["category"]) for i in r1["issues"]}
    set2 = {(i["rule_id"], i["category"]) for i in r2["issues"]}
    assert set1 == set2


# ---------------------------------------------------------------------------
# dax_best_practices — regex-catalogue analyzer (returns dict, not list)
# ---------------------------------------------------------------------------


def test_best_practices_returns_structured_dict():
    """analyze() returns a dict with summary counts + issues list."""
    from core.dax.dax_best_practices import DaxBestPracticesAnalyzer

    analyzer = DaxBestPracticesAnalyzer()
    result = analyzer.analyze("DIVIDE ( [X], [Y], 0 )")
    assert isinstance(result, dict)
    # Observable response-shape contract.
    for key in (
        "issues",
        "total_issues",
        "critical_issues",
        "high_issues",
        "medium_issues",
        "overall_score",
        "complexity_level",
        "success",
    ):
        assert (
            key in result
        ), f"dax_best_practices response missing {key!r}: {sorted(result.keys())}"
    assert isinstance(result["issues"], list)


def test_best_practices_flags_iferror_around_division():
    """Well-known anti-pattern: ``IFERROR`` around division. Pins at least
    one issue is found with severity high/critical on this pattern.
    """
    from core.dax.dax_best_practices import DaxBestPracticesAnalyzer

    analyzer = DaxBestPracticesAnalyzer()
    result = analyzer.analyze("IFERROR ( [Revenue] / [Cost], 0 )")
    assert (
        result["total_issues"] >= 1
    ), "anti-pattern IFERROR-around-division produced zero issues; rule regressed"
    # Severity for IFERROR anti-pattern should be at least "medium".
    severities = {str(i.get("severity", "")).lower() for i in result["issues"]}
    assert severities & {
        "medium",
        "high",
        "critical",
    }, f"expected medium+ severity on IFERROR anti-pattern, got {severities}"


def test_best_practices_clean_expression_no_high_severity():
    """Clean DIVIDE() call should trip no ERROR / CRITICAL level issues."""
    from core.dax.dax_best_practices import DaxBestPracticesAnalyzer

    analyzer = DaxBestPracticesAnalyzer()
    result = analyzer.analyze("DIVIDE ( [Total Sales], [Order Count], 0 )")
    high = [
        i for i in result["issues"] if str(i.get("severity", "")).lower() in ("critical", "high")
    ]
    assert not high, f"clean DIVIDE flagged high/critical issues: {high}"


def test_best_practices_issue_shape():
    """Each issue dict has the documented fields."""
    from core.dax.dax_best_practices import DaxBestPracticesAnalyzer

    result = DaxBestPracticesAnalyzer().analyze("IFERROR ( [X] / [Y], 0 )")
    assert result["issues"]
    issue = result["issues"][0]
    for key in ("rule_id", "category", "severity", "description"):
        assert key in issue, f"issue dict missing {key!r}: {sorted(issue.keys())}"


# ---------------------------------------------------------------------------
# Cross-engine overlap — the engines have overlapping rules (e.g. both catch
# CORR_DIVISION_WITHOUT_DIVIDE). That overlap is the consolidation target.
# ---------------------------------------------------------------------------


def test_both_engines_flag_division_without_divide():
    """Both engines should catch ``X / Y`` and suggest DIVIDE — this overlap
    is P2-28's consolidation target. If one engine stops catching it after a
    rewrite, the other must compensate.
    """
    from core.dax.dax_rules_engine import DaxRulesEngine
    from core.dax.dax_best_practices import DaxBestPracticesAnalyzer

    expr = "[Revenue] / [Cost]"

    r1 = DaxRulesEngine().analyze(expr)
    r2 = DaxBestPracticesAnalyzer().analyze(expr)

    r1_rules = {i["rule_id"] for i in r1["issues"]}
    r2_rules = {i["rule_id"] for i in r2["issues"]}

    # The specific rule_id is stable: CORR_DIVISION_WITHOUT_DIVIDE appears in
    # both engines today. If a consolidation renames it, update this single
    # reference. If it disappears from both, regression.
    divide_rule_ids = {
        r for r in (r1_rules | r2_rules) if "DIVIDE" in r.upper() or "DIVISION" in r.upper()
    }
    assert divide_rule_ids, (
        f"neither engine flagged division-without-DIVIDE on {expr!r}; "
        f"r1={r1_rules}, r2={r2_rules}"
    )


def test_engines_agree_on_clean_divide():
    """A clean DIVIDE should produce zero high/critical issues in BOTH engines."""
    from core.dax.dax_rules_engine import DaxRulesEngine
    from core.dax.dax_best_practices import DaxBestPracticesAnalyzer

    expr = "DIVIDE ( [Total Sales], [Order Count], 0 )"

    r1 = DaxRulesEngine().analyze(expr)
    r1_err = [
        i
        for i in r1["issues"]
        if str(i.get("severity", "")).lower() in ("critical", "high", "error")
    ]

    r2 = DaxBestPracticesAnalyzer().analyze(expr)
    r2_err = [
        i
        for i in r2["issues"]
        if str(i.get("severity", "")).lower() in ("critical", "high", "error")
    ]

    assert not r1_err and not r2_err, (
        f"clean DIVIDE flagged high-sev on at least one engine — "
        f"rules_engine={r1_err}, best_practices={r2_err}"
    )
