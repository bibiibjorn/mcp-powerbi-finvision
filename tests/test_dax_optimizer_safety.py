"""Optimizer semantic-safety tests (audit Fix #3, 2026-05).

Two confirmed non-equivalent rewrites the optimizer previously emitted:

1. COUNTX(T, T[Col]) -> COUNTROWS(T): COUNTX counts NON-BLANK values of the
   column; COUNTROWS counts every row. Not equivalent when the column has
   blanks. The optimizer must not emit this rewrite.

2. variable_extraction hoisting a [Measure] used INSIDE an iterator/CALCULATE
   out to a top-level VAR breaks context transition (AP-10): SUMX(Sales,
   [Margin]) computes [Margin] per-row (implicit CALCULATE); a top-level
   VAR _Margin = [Margin] evaluates it once. The optimizer must not rewrite
   when any occurrence sits in a context-transition scope.
"""

from __future__ import annotations

from core.dax.optimizer.pipeline import OptimizationPipeline


def _final(dax: str):
    return OptimizationPipeline().optimize_expression(dax)


# --- COUNTX must not be rewritten to COUNTROWS -----------------------------


def test_countx_not_rewritten_to_countrows():
    result = _final("COUNTX ( Sales, Sales[Quantity] )")
    final = result.final_dax or ""
    assert "COUNTROWS" not in final.upper(), f"COUNTX wrongly rewritten to COUNTROWS: {final!r}"
    # No applied rewrite should turn COUNTX into COUNTROWS.
    for rw in result.rewrites or []:
        if rw.validation_passed:
            assert "COUNTROWS" not in (rw.full_rewritten_dax or "").upper()


# --- variable_extraction must not hoist a measure out of an iterator -------


def test_measure_inside_sumx_not_hoisted_to_var():
    # [Margin] appears inside SUMX (context transition) AND twice outside.
    dax = "SUMX ( Sales, [Margin] ) + [Margin] + [Margin]"
    result = _final(dax)
    final = result.final_dax
    if final is not None:
        # The critical invariant: SUMX must not now reference a hoisted VAR —
        # the [Margin] inside SUMX must stay a measure ref (context transition).
        compact = final.replace(" ", "")
        assert (
            "SUMX(Sales,_Margin)" not in compact
        ), f"measure hoisted out of SUMX — breaks context transition: {final!r}"


def test_repeated_measure_outside_iterator_still_extractable():
    """A measure repeated only in plain scalar context is still safe to extract."""
    dax = "[Margin] + [Margin] + [Margin]"
    result = _final(dax)
    # This is safe; either it extracts to a VAR or leaves it — but it must
    # never produce broken DAX. We only assert it does not crash and, if a
    # VAR was introduced, the body references it.
    assert result.success
    final = result.final_dax
    if final and "VAR" in final.upper():
        assert "_Margin" in final
