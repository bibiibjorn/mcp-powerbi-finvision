"""Live-engine tests: a bare ``[X]`` ref must not be rewritten to ``SUM([X])``.

Audit finding (2026-05): the tokenizer labels every ``[X]`` as COLUMN_REF
(``tokens.py`` comment: "Column or Measure"). ``UnnecessaryIteratorRule`` and
the optimizer's ``iterator_optimization`` strategy treated a single bare
``[X]`` as a replaceable column, so:

    SUMX(VALUES(Dim[Key]), [Sales])  ->  SUM([Sales])

That is a hard runtime error (SUM needs a physical column) and the recommended
AP-05 pattern (iterate the key, context-transition the measure). A bare ``[X]``
must NOT be flagged/rewritten; only an unambiguous qualified column may be.
"""

from __future__ import annotations

from core.dax.analyzer.unified_analyzer import DaxUnifiedAnalyzer


def _rule_ids(dax: str) -> set:
    result = DaxUnifiedAnalyzer().analyze(dax)
    return {i.rule_id for i in result.issues}


def test_bare_ref_in_sumx_not_flagged_unnecessary_iterator():
    """SUMX(VALUES(Key), [Measure]) — bare [X] is ambiguous; must NOT flag."""
    ids = _rule_ids("SUMX ( VALUES ( 'Date'[Year] ), [Sales] )")
    assert "PY_UNNECESSARY_ITERATOR" not in ids
    assert "PERF_UNNECESSARY_SUMX" not in ids


def test_bare_ref_direct_table_in_sumx_not_flagged():
    """Even SUMX(Sales, [Margin]) — [Margin] is more often a measure; don't rewrite."""
    ids = _rule_ids("SUMX ( Sales, [Margin] )")
    assert "PY_UNNECESSARY_ITERATOR" not in ids


def test_qualified_column_in_sumx_still_flagged():
    """The unambiguous case SUMX(Sales, Sales[Amount]) MUST still be flagged."""
    ids = _rule_ids("SUMX ( Sales, Sales[Amount] )")
    assert "PY_UNNECESSARY_ITERATOR" in ids


def test_optimizer_does_not_rewrite_bare_measure_to_sum():
    """The end-to-end optimizer pipeline must not emit SUM([X]) for a bare ref."""
    from core.dax.optimizer.pipeline import OptimizationPipeline

    result = OptimizationPipeline().optimize_expression("SUMX ( VALUES ( 'Date'[Year] ), [Sales] )")
    final = (result.final_dax or "").replace(" ", "")
    assert "SUM([Sales])" not in final
    # Also assert at the strategy level.
    for rw in result.rewrites or []:
        frag = (rw.rewritten_fragment or "").replace(" ", "")
        assert "SUM([Sales])" not in frag


def test_optimizer_still_rewrites_qualified_column():
    """The legitimate SUMX(Sales, Sales[Amount]) -> SUM(Sales[Amount]) still works."""
    from core.dax.optimizer.pipeline import OptimizationPipeline

    result = OptimizationPipeline().optimize_expression("SUMX ( Sales, Sales[Amount] )")
    final = (result.final_dax or "").replace(" ", "")
    assert "SUM(Sales[Amount])" in final
