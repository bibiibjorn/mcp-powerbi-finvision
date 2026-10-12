"""Live-engine tests for the JSON rule ``match.check`` subkey.

Audit finding C1/C2 (2026-05): ``rule_engine._eval_function_usage`` ignored the
``match.check`` field, so ``function_usage`` rules that declare a ``check``
(``PERF_UNNECESSARY_SUMX``: simple_column_expression, ``CORR_SUMMARIZE_INLINE``:
more_than_two_args, ``PERF_SELECTEDVALUE_VS_VALUES``: scalar_context) fired on
EVERY occurrence of their function — telling users to break correct DAX
(e.g. ``SUMX(T, T[a]*T[b])`` -> "use SUM", impossible).

These tests exercise the LIVE path (``DaxUnifiedAnalyzer``), not the dead
regex engines. Each rule must fire on the genuine anti-pattern and stay silent
on the legitimate shape.
"""

from __future__ import annotations

from core.dax.analyzer.unified_analyzer import DaxUnifiedAnalyzer


def _rule_ids(dax: str) -> set:
    result = DaxUnifiedAnalyzer().analyze(dax)
    return {i.rule_id for i in result.issues}


# --- single-column iterator: only a bare single-column SUMX is "unnecessary" ---
# 2026-09-22 review D-09: PERF_UNNECESSARY_SUMX was a duplicate of
# PY_UNNECESSARY_ITERATOR (canonical). Both ids are asserted so neither returns.
_ITER_IDS = {"PERF_UNNECESSARY_SUMX", "PY_UNNECESSARY_ITERATOR"}


def test_sumx_with_row_level_multiplication_not_flagged_unnecessary():
    """SUMX(T, T[a]*T[b]) is the canonical CORRECT iterator — must NOT flag."""
    ids = _rule_ids("SUMX ( Sales, Sales[Qty] * Sales[Price] )")
    assert not (ids & _ITER_IDS)


def test_sumx_with_related_not_flagged_unnecessary():
    ids = _rule_ids("SUMX ( Sales, Sales[Qty] * RELATED ( Product[Price] ) )")
    assert not (ids & _ITER_IDS)


def test_sumx_over_single_column_is_flagged_unnecessary():
    """The genuine anti-pattern SUMX(T, T[Col]) MUST still be flagged."""
    ids = _rule_ids("SUMX ( Sales, Sales[Amount] )")
    assert "PY_UNNECESSARY_ITERATOR" in ids
    assert "PERF_UNNECESSARY_SUMX" not in ids  # one canonical finding


# --- CORR_SUMMARIZE_INLINE: only SUMMARIZE with an inline "name", expr ---


def test_summarize_pure_groupby_not_flagged_inline():
    """SUMMARIZE used purely for grouping (no inline expr) is correct."""
    ids = _rule_ids("SUMMARIZE ( Sales, Sales[A], Sales[B], Sales[C] )")
    assert "CORR_SUMMARIZE_INLINE" not in ids


def test_summarize_with_inline_expression_is_flagged():
    """SUMMARIZE(T, T[Col], "Name", <expr>) is the deprecated form — flag it."""
    ids = _rule_ids('SUMMARIZE ( Sales, Sales[Cat], "Total", SUM ( Sales[Amount] ) )')
    assert "CORR_SUMMARIZE_INLINE" in ids


# --- PERF_SELECTEDVALUE_VS_VALUES: only VALUES in a scalar context ---


def test_values_as_table_argument_not_flagged():
    """VALUES used as a table (the normal case) must NOT suggest SELECTEDVALUE."""
    ids = _rule_ids("SUMX ( VALUES ( 'Date'[Year] ), [Sales] )")
    assert "PERF_SELECTEDVALUE_VS_VALUES" not in ids
