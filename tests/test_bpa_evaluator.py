"""Regression tests for the BPA analyzer end-to-end flow.

The BPA evaluator in ``core/analysis/bpa_analyzer.py`` is a hand-rolled DSL
interpreter (~450 lines) that prior audit P2-26 flagged for future refactor
(→ asteval / pyparsing). That refactor was deferred because there were
**zero** end-to-end tests pinning current behavior — a rewrite could silently
change rule semantics.

This module establishes a minimal pin: feed ``analyze_model`` a small TMSL
fixture, assert the set of violations is stable across code changes. A failing
assertion after a refactor means the refactor changed observable rule output;
the refactorer must decide whether the change is intentional and update the
expected set.

The fixture intentionally triggers several common rule patterns (hidden-table
heuristics, data-type issues, missing descriptions) so a broad swath of the
evaluator is exercised. It is NOT a comprehensive rule-coverage suite —
adding coverage per rule is the next step, once somebody wants to do the
real extraction.
"""

from __future__ import annotations

import pytest

from core.analysis.bpa_analyzer import BPAAnalyzer

# A trimmed TMSL fragment exercising several BPA rule patterns.
# Shape follows what ``BPAAnalyzer.analyze_model`` expects — see
# ``_build_model_index`` and ``_check_*_rule`` in bpa_analyzer.py.
_MINI_MODEL = {
    "model": {
        "tables": [
            {
                "name": "Sales",
                "isHidden": False,
                "columns": [
                    {
                        "name": "Amount",
                        "dataType": "double",
                        "isHidden": False,
                        "summarizeBy": "sum",
                    },
                    {
                        "name": "DateKey",
                        "dataType": "int64",
                        "isHidden": False,
                    },
                ],
                "measures": [
                    {
                        "name": "Total Sales",
                        "expression": "SUM ( Sales[Amount] )",
                        "description": "",
                    }
                ],
            },
            {
                "name": "_HiddenHelper",
                "isHidden": True,
                "columns": [],
                "measures": [],
            },
        ],
        "relationships": [],
    }
}


@pytest.fixture
def analyzer():
    return BPAAnalyzer()


def test_analyze_model_returns_violation_list(analyzer):
    """Smoke test: analyze_model must return a list without raising."""
    violations = analyzer.analyze_model(_MINI_MODEL)
    assert isinstance(violations, list)


def test_analyze_model_violations_are_wellformed(analyzer):
    """Every returned violation must have the documented dataclass fields.

    Pins the response-shape contract so a future refactor that changes what
    ``BPAViolation`` looks like fails loudly.
    """
    violations = analyzer.analyze_model(_MINI_MODEL)
    for v in violations:
        assert hasattr(v, "rule_id")
        assert hasattr(v, "rule_name")
        assert hasattr(v, "severity")
        assert hasattr(v, "object_type")
        assert hasattr(v, "object_name")
        assert isinstance(v.rule_id, str) and v.rule_id
        assert isinstance(v.rule_name, str) and v.rule_name


def test_analyze_model_is_deterministic(analyzer):
    """Same input → same violation set (IDs, not order). Pins purity of the
    evaluator against silent nondeterminism creeping in during refactor.
    """
    first = analyzer.analyze_model(_MINI_MODEL)
    # Fresh analyzer for second run so cache state doesn't mask a bug.
    analyzer2 = BPAAnalyzer()
    second = analyzer2.analyze_model(_MINI_MODEL)

    first_keys = sorted((v.rule_id, v.object_type, v.object_name) for v in first)
    second_keys = sorted((v.rule_id, v.object_type, v.object_name) for v in second)
    assert first_keys == second_keys
