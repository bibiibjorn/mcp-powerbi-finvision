"""2026-09-22 review slice E — optimizer rewrites must be exact or refused.

E-03 SUMX(<modified table>) -> SUM, E-04 nested CALCULATE flattening, E-07 VAR
hoisting across context / invalid names / strings, E-08 last-rewrite-wins,
E-09 COUNTROWS(VALUES(table)) -> DISTINCTCOUNT(table).
"""

from __future__ import annotations

import pytest

from core.dax.optimizer.pipeline import OptimizationPipeline


@pytest.fixture(scope="module")
def pipe():
    return OptimizationPipeline()


def _final(pipe, dax):
    return pipe.optimize_expression(dax)


@pytest.mark.parametrize(
    "dax",
    [
        "SUMX(FILTER(Sales, Sales[Qty] > 1), Sales[Amount])",
        "SUMX(ALL(Sales), Sales[Amount])",
        "AVERAGEX(RELATEDTABLE(Sales), Sales[Amount])",
        "SUMX(Product, Sales[Amount])",
    ],
)
def test_e03_iterator_over_modified_table_is_never_rewritten(pipe, dax):
    r = _final(pipe, dax)
    assert r.final_dax is None
    assert not any(rw.strategy == "iterator_optimization" for rw in r.rewrites)


def test_e03_trivial_iterator_still_rewritten(pipe):
    assert _final(pipe, "SUMX(Sales, Sales[Amount])").final_dax == "SUM(Sales[Amount])"


@pytest.mark.parametrize(
    "dax",
    [
        "CALCULATE(CALCULATE(CALCULATE([Sales], 'D'[Year]=2024), 'P'[Color]=\"Red\"), 'P'[Color]=\"Blue\")",
        "CALCULATE(CALCULATE(CALCULATE([Sales], 'D'[Year]=2024), ALL('P')), 'P'[Color]=\"Red\")",
        "CALCULATE(CALCULATE(CALCULATE([S], T[a]=1), T[b]=2) * 2, T[c]=3)",
    ],
)
def test_e04_nested_calculate_is_suggestion_only(pipe, dax):
    r = _final(pipe, dax)
    assert r.final_dax is None
    calc = [rw for rw in r.rewrites if rw.strategy == "calculate_optimization"]
    assert calc and all(rw.suggestion_only and rw.full_rewritten_dax == dax for rw in calc)


def test_e07_no_hoisting_out_of_time_intelligence(pipe):
    r = _final(pipe, "IF([Sales] > 0, TOTALYTD([Sales], 'Date'[Date]) / [Sales])")
    assert r.final_dax is None


@pytest.mark.parametrize(
    "fn",
    [
        "FIRSTNONBLANK('D'[Date], [Sales])",
        "MYUDF([Sales])",
        "SUMMARIZECOLUMNS('D'[Y], \"s\", [Sales])",
    ],
)
def test_e07_no_hoisting_out_of_other_context_functions(pipe, fn):
    r = _final(pipe, f"[Sales] + [Sales] + COUNTROWS({fn})")
    assert r.final_dax is None


def test_e07_string_literal_untouched_and_name_sanitised(pipe):
    r = _final(pipe, 'IF([Sales] > 0, [Sales] & " of [Sales]", [Sales])')
    assert '" of [Sales]"' in r.final_dax
    r2 = _final(pipe, "IF([Margin %] > 0, [Margin %] * 2, [Margin %])")
    assert r2.final_dax.startswith("VAR _Margin = [Margin %]")


def test_e07_var_name_dedup():
    from core.dax.optimizer.strategies.variable_extraction import VariableExtractionStrategy as V

    assert V._generate_var_name("[Sales]", {"_SALES"}) == "_Sales2"
    assert V._generate_var_name("[%]") == "_Var"


def test_e08_rewrites_accumulate_to_a_fixpoint(pipe):
    r = _final(pipe, "SUMX(Sales, Sales[Amount]) + COUNTROWS(DISTINCT(Customer[Key]))")
    assert r.final_dax == "SUM(Sales[Amount]) + DISTINCTCOUNT(Customer[Key])"
    applied = [rw for rw in r.rewrites if not rw.suggestion_only]
    assert len(applied) == 2
    assert "2 rewrite(s) applied" in r.improvement_summary


def test_e08_suggestion_cannot_erase_a_real_rewrite(pipe):
    r = _final(pipe, "SUMX(Sales, Sales[Amount]) + COUNTROWS(VALUES(Customer[Key]))")
    assert r.final_dax == "SUM(Sales[Amount]) + COUNTROWS(VALUES(Customer[Key]))"
    assert any(rw.suggestion_only for rw in r.rewrites)


def test_e09_values_table_argument_not_rewritten_and_values_is_suggestion(pipe):
    assert _final(pipe, "COUNTROWS(VALUES(Sales))").final_dax is None
    r = _final(pipe, "COUNTROWS(VALUES(Customer[Key]))")
    assert r.final_dax is None
    assert any("referential" in rw.explanation for rw in r.rewrites)
    assert (
        _final(pipe, "COUNTROWS(DISTINCT(Customer[Key]))").final_dax
        == "DISTINCTCOUNT(Customer[Key])"
    )
