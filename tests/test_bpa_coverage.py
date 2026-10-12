"""BPA rule-coverage reporting (audit Fix #4, 2026-05).

``_evaluate_expression_impl`` only understands a small grammar; unsupported
C#/TOM constructs fall through to ``return False`` — indistinguishable from
"evaluated, not violated". ~62% of the standard ruleset uses such constructs,
so a full run looks clean having actually evaluated ~38% of rules, with no
signal to the caller.

This pins that the analyzer now distinguishes "could not evaluate" from
"not violated" and exposes coverage (rules_total / rules_evaluated /
rules_unsupported) plus a run note.
"""

from __future__ import annotations

from core.analysis.bpa_analyzer import BPAAnalyzer, BPARule, BPASeverity


def _model() -> dict:
    return {
        "tables": [
            {
                "name": "Sales",
                "columns": [{"name": "Amount", "type": "DataColumn"}],
                "measures": [{"name": "Total", "expression": "SUM(Sales[Amount])"}],
            }
        ]
    }


def _analyzer_with(rule: BPARule) -> BPAAnalyzer:
    a = BPAAnalyzer()
    a.rules = [rule]
    return a


def test_unsupported_rule_reported_in_coverage():
    """A rule using an unsupported TOM construct is counted as unsupported,
    not silently evaluated to 'no violation'."""
    rule = BPARule(
        id="UNSUPPORTED_TOM_RULE",
        name="Uses unsupported chaining",
        category="Performance",
        description="x",
        severity=BPASeverity.WARNING,
        # TOM method chaining the hand-rolled evaluator cannot evaluate.
        scope=["Measure"],
        expression='UsedInRelationships.Any(FromColumn.DataType = "Int64")',
    )
    a = _analyzer_with(rule)
    a.analyze_model(_model())

    coverage = a.get_coverage()
    assert coverage["rules_total"] == 1
    assert "UNSUPPORTED_TOM_RULE" in coverage["unsupported_rule_ids"]
    assert coverage["rules_unsupported"] == 1
    assert coverage["rules_evaluated"] == 0
    # And a human-readable note must be surfaced.
    notes = " ".join(a.get_run_notes()).lower()
    assert "unsupported" in notes or "coverage" in notes


def test_supported_rule_counts_as_evaluated():
    """A rule the evaluator fully understands counts as evaluated, not unsupported."""
    rule = BPARule(
        id="SUPPORTED_REGEX_RULE",
        name="Measure name has lowercase",
        category="Naming",
        description="x",
        severity=BPASeverity.INFO,
        scope=["Measure"],
        expression=r'RegEx.IsMatch(Name, "[a-z]")',
    )
    a = _analyzer_with(rule)
    a.analyze_model(_model())

    coverage = a.get_coverage()
    assert coverage["rules_total"] == 1
    assert "SUPPORTED_REGEX_RULE" not in coverage["unsupported_rule_ids"]
    assert coverage["rules_evaluated"] == 1
    assert coverage["rules_unsupported"] == 0
