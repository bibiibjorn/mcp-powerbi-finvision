"""2026-09-22 deep review, slice D — the DAX rule catalogue.

D-05 SUMX(FILTER)->SUM justification, D-07 auto-fix mapping + blank
propagation, D-08 advice vs modern SQLBI guidance (+ golden corpus), D-09 one
canonical rule per pattern, D-11 one engine (shims, CB007-CB011), D-12 real
INFO.RELATIONSHIPS row shape, D-13 nesting depth, D-26 token-based
suppression, D-27 lint-disable comments.
"""

import json
from pathlib import Path

import pytest

from core.dax.analyzer.models import AnalysisContext, ModelSchema
from core.dax.analyzer.unified_analyzer import DaxUnifiedAnalyzer, get_shared_analyzer

RULES_DIR = Path(__file__).resolve().parent.parent / "core" / "dax" / "knowledge" / "rules"


def issues(dax, ctx=None):
    return get_shared_analyzer().analyze(dax, ctx).issues


def ids(dax, ctx=None):
    return [i.rule_id for i in issues(dax, ctx)]


# --- D-05: an iterator over FILTER / RELATEDTABLE / VALUES is NOT SUM(col) ---------------


@pytest.mark.parametrize(
    "dax",
    [
        "SUMX(FILTER(Sales, Sales[Qty] > 1), Sales[Amount])",
        "SUMX(RELATEDTABLE(Sales), Sales[Amount])",
        "SUMX(VALUES(Sales[Key]), Sales[Amount])",
        "VAR _t = FILTER(Sales, Sales[Qty] > 1) RETURN SUMX(_t, Sales[Amount])",
        "SUMX(Customer, Sales[Amount])",
    ],
)
def test_iterator_with_table_expression_is_not_unnecessary(dax):
    got = ids(dax)
    assert "PY_UNNECESSARY_ITERATOR" not in got
    assert "PERF_UNNECESSARY_SUMX" not in got


def test_trivial_iterator_is_info_maintainability_without_perf_claim():
    found = [
        i for i in issues("SUMX(Sales, Sales[Amount])") if i.rule_id == "PY_UNNECESSARY_ITERATOR"
    ]
    assert len(found) == 1
    assert found[0].severity == "info" and found[0].category == "maintainability"
    assert "overhead" not in found[0].description.lower()


def test_is_trivial_iterator_helper_for_rewriters():
    from core.dax.analyzer.rules import is_trivial_iterator
    from core.dax.dax_reference_parser import get_lexer

    toks = get_lexer().tokenize_code(
        "SUMX('Sales', 'Sales'[Amount]) + SUMX(FILTER(Sales, 1), Sales[A])"
    )
    first = 0
    second = next(i for i, t in enumerate(toks) if i > 0 and t.value.upper() == "SUMX")
    assert is_trivial_iterator(toks, first) == "SUM"
    assert is_trivial_iterator(toks, second) is None


# --- D-07: auto-fix map + CORR_BLANK_PROPAGATION shape ----------------------------------


def test_every_auto_fix_key_is_a_real_rule():
    from server.handlers.dax_context_handler import _AUTO_FIX_TRANSFORMS

    known = DaxUnifiedAnalyzer().rule_ids()
    missing = set(_AUTO_FIX_TRANSFORMS) - known
    assert not missing, f"auto-fix map references unknown rules: {missing}"
    assert _AUTO_FIX_TRANSFORMS.get("CORR_DIVISION_WITHOUT_DIVIDE") == "use_divide"
    assert "CORR_BLANK_PROPAGATION" not in _AUTO_FIX_TRANSFORMS


@pytest.mark.parametrize(
    "dax", ["1 - DIVIDE([a], [b])", "1 - [a] / [b]", "VAR r = 1 - [a]/[b] RETURN r"]
)
def test_blank_propagation_fires_on_literal_minus_ratio(dax):
    assert "CORR_BLANK_PROPAGATION" in ids(dax)


@pytest.mark.parametrize(
    "dax",
    [
        "VAR _py = CALCULATE([Sales], DATEADD('Date'[Date], -1, YEAR)) RETURN DIVIDE([Sales] - _py, _py)",
        "[Sales] -- ratio a/b\n+ 0",
        "1 - [a] + [b] / [c]",
        "[x] * 2 - DIVIDE([a], [b])",
    ],
)
def test_blank_propagation_silent_otherwise(dax):
    assert "CORR_BLANK_PROPAGATION" not in ids(dax)


# --- D-08: modern guidance ---------------------------------------------------------------


def test_divide_in_iterator_is_info_and_no_ping_pong():
    got = {i.rule_id: i for i in issues("SUMX(Sales, DIVIDE(Sales[Amount], Sales[Qty]))")}
    assert set(got) == {"CB007"}
    assert got["CB007"].severity == "info"
    assert "PY_DIVIDE_IN_ITERATOR" not in got


def test_constant_denominator_not_flagged():
    assert "CORR_DIVISION_WITHOUT_DIVIDE" not in ids("[Sales] / 1000")
    assert "CORR_DIVISION_WITHOUT_DIVIDE" in ids("[Sales] / [Units]")
    assert "CORR_DIVISION_WITHOUT_DIVIDE" in ids("[Sales] / 0")


def test_crossfilter_and_userelationship_emit_no_finding():
    """2026-10-03 review D-17: the info rules whose own fix text was "no change needed"
    (PERF_CROSSFILTER_IN_MEASURE / PERF_USERELATIONSHIP_IN_MEASURE) were deleted - they existed
    only to deduct a health point from the canon's recommended per-measure patterns."""
    for dax in [
        "CALCULATE(DISTINCTCOUNT(Customer[Id]), CROSSFILTER(Sales[CustId], Customer[Id], BOTH))",
        "CALCULATE([Sales], USERELATIONSHIP(Sales[ShipDate], 'Date'[Date]))",
    ]:
        got = ids(dax)
        assert "PERF_CROSSFILTER_IN_MEASURE" not in got
        assert "PERF_USERELATIONSHIP_IN_MEASURE" not in got


def test_filter_all_column_form_not_flagged_table_form_flagged():
    running_total = "CALCULATE([S], FILTER(ALL('Date'[Date]), 'Date'[Date] <= MAX('Date'[Date])))"
    got = ids(running_total)
    assert "PERF_FILTER_ALL" not in got
    assert "PY_KEEPFILTERS_OPPORTUNITY" not in got
    assert "MAINT_REPEATED_MEASURE" not in got
    assert "PERF_FILTER_ALL" in ids("CALCULATE([M], FILTER(ALL(Cust), [Age] > 30))")


def test_keepfilters_advice_only_for_filter_values_same_column():
    assert "PY_KEEPFILTERS_OPPORTUNITY" in ids(
        'CALCULATE([S], FILTER(VALUES(Product[Color]), Product[Color] = "Red"))'
    )
    assert "PY_KEEPFILTERS_OPPORTUNITY" not in ids(
        'CALCULATE([S], FILTER(ALL(Product[Color]), Product[Color] = "Red"))'
    )
    assert "PY_KEEPFILTERS_OPPORTUNITY" not in ids(
        "CALCULATE([S], FILTER(VALUES(Product[Color]), [Sales] > 10))"
    )


def test_sumx_filter_is_medium_single_finding():
    got = issues("SUMX(FILTER(Sales, Sales[Qty] > 1), Sales[Qty] * Sales[Price])")
    assert [(i.rule_id, i.severity) for i in got] == [("CB004", "medium")]


def test_filter_over_a_var_table_is_not_a_bare_table():
    got = ids("VAR _t = SUMMARIZE(Sales, Sales[Cust]) RETURN COUNTROWS(FILTER(_t, [Sales] > 100))")
    assert "CB004" not in got and "PERF_COUNTROWS_FILTER" not in got


def test_sparsity_preserving_isblank_not_flagged():
    assert "PERF_DENSE_BLANK_CONVERSION" not in ids("IF(ISBLANK([Sales]), BLANK(), [Sales] * 1.21)")
    assert "PERF_DENSE_BLANK_CONVERSION" in ids("IF(ISBLANK([Sales]), 0, [Sales])")


def test_if_eager_is_the_remedy_not_a_finding():
    assert ids("SUMX(Sales, IF.EAGER(Sales[Qty] > 0, Sales[Amount], 0))") == []
    assert "CB001" in ids("SUMX(Sales, IF(Sales[Qty] > 0, Sales[Amount], 0))")


@pytest.mark.parametrize(
    "dax",
    [
        "STDEVX.S(Sales, IF(Sales[Qty] > 0, Sales[Amount]))",
        "PERCENTILEX.INC(Sales, IF(Sales[Qty] > 0, Sales[Amount]), 0.9)",
    ],
)
def test_dotted_iterators_are_recognised(dax):
    assert "CB001" in ids(dax)


def test_udf_val_parameter_in_boolean_filter_is_fine():
    fine = "FUNCTION HighSales = (threshold: NUMERIC VAL) => CALCULATE([Sales], Sales[Amount] > threshold)"
    assert "PY_UDF_EAGER_EVAL_RISK" not in ids(fine)
    bad = "FUNCTION F2 = (flt: NUMERIC VAL) => CALCULATE([Sales], flt)"
    assert "PY_UDF_EAGER_EVAL_RISK" in ids(bad)


def test_udf_leading_passing_mode_parameter_name():
    got = [
        i
        for i in issues("FUNCTION F = (VAL sales, EXPR r: NUMERIC) => sales * r")
        if i.rule_id == "PY_UDF_UNTYPED_PARAMETER"
    ]
    assert got and got[0].match_text == "sales"


def test_fixed_filter_detected_quoted_and_unquoted():
    assert "CORR_FIXED_FILTER_IN_CALCULATE" in ids("CALCULATE([Sales], 'Date'[Year] = 2024)")
    assert "CORR_FIXED_FILTER_IN_CALCULATE" in ids("CALCULATE([Sales], Date[Year] = 2024)")


def test_repeated_measure_counts_one_filter_context_only():
    switch = (
        "SWITCH(TRUE(), [Sel] = 1, CALCULATE([Sales], 'P'[C] = \"A\"), "
        "[Sel] = 2, CALCULATE([Sales], 'P'[C] = \"B\"), [Sales])"
    )
    assert "MAINT_REPEATED_MEASURE" not in ids(switch)
    ti = (
        "CALCULATE([Sales], DATESYTD('Date'[Date])) - CALCULATE([Sales], SAMEPERIODLASTYEAR('Date'[Date])) "
        "+ CALCULATE([Sales], PREVIOUSYEAR('Date'[Date]))"
    )
    assert "MAINT_REPEATED_MEASURE" not in ids(ti)
    assert "MAINT_REPEATED_MEASURE" in ids("IF([S] > 0, [S] * 2, [S] / 2)")


def test_repeated_measure_skips_schema_columns():
    schema = ModelSchema(column_names={"qty"})
    assert "MAINT_REPEATED_MEASURE" not in ids(
        "SUMX(Sales, [Qty] * [Qty] + [Qty])", AnalysisContext(schema=schema)
    )


# Golden corpus: idiomatic SQLBI / DAX Patterns measures (synthetic names) that
# must produce ZERO high/critical findings.
GOLDEN = [
    "SUM(Sales[Amount])",
    "DIVIDE([Sales], [Units])",
    "DIVIDE([Sales] - [Sales PY], [Sales PY])",
    "CALCULATE([Sales], 'Date'[Year] = 2024)",
    'CALCULATE([Sales], KEEPFILTERS(Product[Color] = "Red"))',
    "CALCULATE([Sales], DATEADD('Date'[Date], -1, YEAR))",
    "CALCULATE([Sales], SAMEPERIODLASTYEAR('Date'[Date]))",
    "CALCULATE([Sales], DATESYTD('Date'[Date]))",
    "TOTALYTD([Sales], 'Date'[Date])",
    "CALCULATE([Sales], FILTER(ALL('Date'[Date]), 'Date'[Date] <= MAX('Date'[Date])))",
    "CALCULATE([Sales], REMOVEFILTERS(Product))",
    "DIVIDE([Sales], CALCULATE([Sales], REMOVEFILTERS(Product[Category])))",
    "DIVIDE([Sales], CALCULATE([Sales], ALLSELECTED()))",
    "SUMX(Sales, Sales[Qty] * Sales[Price])",
    "SUMX(Sales, Sales[Qty] * RELATED(Product[Price]))",
    "SUMX(VALUES(Customer[Id]), [Sales])",
    "AVERAGEX(VALUES('Date'[Month]), [Sales])",
    "COUNTROWS(Sales)",
    "DISTINCTCOUNT(Sales[CustomerId])",
    "CALCULATE(DISTINCTCOUNT(Customer[Id]), CROSSFILTER(Sales[CustId], Customer[Id], BOTH))",
    "CALCULATE([Sales], USERELATIONSHIP(Sales[ShipDate], 'Date'[Date]))",
    "IF(ISBLANK([Sales]), BLANK(), [Sales] * 1.21)",
    "VAR _s = [Sales] VAR _c = [Cost] RETURN DIVIDE(_s - _c, _s)",
    "COALESCE([Sales], 0)",
    'SELECTEDVALUE(Product[Category], "All")',
    "IF(HASONEVALUE(Product[Category]), [Sales])",
    "RANKX(ALL(Product[Name]), [Sales])",
    "CALCULATE([Sales], TREATAS(VALUES(Budget[Product]), Product[Name]))",
    "CALCULATE([Sales], LASTNONBLANK('Date'[Date], [Sales]))",
    "MAXX(VALUES('Date'[Date]), [Sales])",
    'SWITCH(SELECTEDVALUE(Metric[Name]), "Sales", [Sales], "Cost", [Cost], BLANK())',
    "CALCULATE([Sales], 'Date'[Date] >= DATE(2024, 1, 1))",
    "[Sales] / 1000",
    'CONCATENATEX(VALUES(Product[Color]), Product[Color], ", ")',
    "VAR _max = MAX('Date'[Date]) RETURN CALCULATE([Sales], 'Date'[Date] <= _max)",
    "SUMX(SUMMARIZE(Sales, Sales[Price]), CALCULATE(SUM(Sales[Qty])) * Sales[Price])",
    'FORMAT([Sales], "#,0") // lint-disable CB005',
    "IFERROR(XIRR(Flows, Flows[Amount], Flows[Date]), BLANK())",
    "SUMX(Sales, IF.EAGER(Sales[Qty] > 0, Sales[Amount], 0))",
    "CALCULATE([Sales], ALLEXCEPT(Sales, Sales[Region]))",
]


@pytest.mark.parametrize("dax", GOLDEN)
def test_golden_corpus_has_no_high_or_critical_findings(dax):
    bad = [(i.rule_id, i.severity) for i in issues(dax) if i.severity in ("high", "critical")]
    assert not bad, f"{dax!r} -> {bad}"


# --- D-09: one canonical finding per pattern ------------------------------------------------

PAIRS = [
    ("SUMX(Sales, IF(Sales[Qty] > 0, Sales[Amount], 0))", {"CB001", "PY_IF_IN_ITERATOR"}),
    ('FORMAT([Sales], "#,0")', {"CB005", "PERF_FORMAT_USAGE"}),
    ("SUMX(Sales, Sales[Amount])", {"PERF_UNNECESSARY_SUMX", "PY_UNNECESSARY_ITERATOR"}),
    (
        "SUMX(Sales, Sales[Qty] * RELATED('Product'[Price]))",
        {"PERF_RELATED_IN_SUMX", "PY_RELATED_IN_ITERATOR"},
    ),
    (
        "COUNTROWS(INTERSECT(VALUES(A[k]), VALUES(B[k])))",
        {"CORR_INTERSECT_VS_TREATAS", "PY_INTERSECT_VS_TREATAS"},
    ),
    ("IF([S] > 0, [S] * 2, [S] / 2)", {"MAINT_REPEATED_MEASURE", "PY_MEASURE_REF_WITHOUT_VAR"}),
    (
        "CALCULATE(CALCULATE(CALCULATE([S], T[a] = 1), T[b] = 2), T[c] = 3)",
        {"PERF_NESTED_CALCULATE", "PY_NESTED_CALCULATE"},
    ),
    ("SUMX(Customer, SUMX(RELATEDTABLE(Sales), Sales[Amount]))", {"CB003", "PY_NESTED_ITERATOR"}),
    (
        'ADDCOLUMNS(SUMMARIZE(Sales, Sales[C]), "x", [S])',
        {"PERF_ADDCOLUMNS_SUMMARIZE", "PY_ADDCOLUMNS_SUMMARIZE"},
    ),
    (
        "CALCULATE([S], FILTER(Sales, Sales[Y] = 1))",
        {"CB004", "PY_FILTER_TABLE_NOT_COLUMN", "PERF_SUMX_FILTER", "PERF_COUNTROWS_FILTER"},
    ),
    ("SUMX(Sales, IFERROR(Sales[a] / Sales[b], 0))", {"CB002", "CORR_IFERROR_USAGE"}),
    ("SUMX(Sales, DIVIDE(Sales[a], Sales[b]))", {"CB007", "PY_DIVIDE_IN_ITERATOR"}),
]


@pytest.mark.parametrize("dax,family", PAIRS)
def test_exactly_one_finding_per_pattern(dax, family):
    got = [r for r in ids(dax) if r in family]
    assert len(got) == 1, f"{dax!r}: expected one of {family}, got {got}"


def test_superseded_ids_never_fire_when_canonical_fires():
    sup = {}
    for f in RULES_DIR.glob("*.json"):
        for r in json.loads(f.read_text(encoding="utf-8")):
            if r.get("supersedes"):
                sup[r["rule_id"]] = set(r["supersedes"])
    assert "CB004" in sup and "PY_FILTER_TABLE_NOT_COLUMN" in sup["CB004"]
    for dax, _family in PAIRS:
        got = set(ids(dax))
        for canonical, targets in sup.items():
            if canonical in got:
                assert not (got & targets), (dax, canonical, got & targets)


# --- D-11: one engine; CB007-CB011 restored; shims surface failures ------------------------


@pytest.mark.parametrize(
    "dax,rid",
    [
        ("SUMX(Sales, DIVIDE(Sales[a], Sales[b]))", "CB007"),
        ("SUMX(Sales, Sales[Qty] * ROUND(Sales[Price], 2))", "CB008"),
        ("SUMX(Sales, LEN(Sales[Code]))", "CB009"),
        ("SUMX(Sales, Sales[a] * SELECTEDVALUE(P[x]))", "CB010"),
        ("SUMX(Customer, CALCULATE([S], DATEADD('Date'[Date], -1, YEAR)))", "CB011"),
    ],
)
def test_callback_rules_cb007_to_cb011_restored(dax, rid):
    from core.dax.callback_detector import CallbackDetector

    out = CallbackDetector().detect_dict(dax)
    assert rid in {d["rule_id"] for d in out["callback_detections"]}


def test_shims_report_analyzer_failure_instead_of_switching_engine(monkeypatch):
    import core.dax.analyzer.unified_analyzer as ua
    from core.dax.callback_detector import CallbackDetector
    from core.dax.dax_best_practices import DaxBestPracticesAnalyzer
    from core.dax.dax_rules_engine import DaxRulesEngine

    class Boom:
        def analyze(self, *a, **k):
            raise RuntimeError("boom")

    monkeypatch.setattr(ua, "get_shared_analyzer", lambda: Boom())
    r1 = DaxRulesEngine().analyze("[a] / [b]")
    r2 = CallbackDetector().detect_dict("SUMX(T, IF(T[a], 1))")
    r3 = DaxBestPracticesAnalyzer().analyze("[a] / [b]")
    for r in (r1, r2, r3):
        assert r["success"] is False and r["error_type"] == "analyzer_failed"
    assert r1["issues"] == [] and r2["callback_detections"] == []


def test_research_regex_engine_is_gone():
    """2026-10-03 review D-01: one advice engine. The regex "research article" engine
    (core/research) and DaxContextAnalyzer.detect_dax_anti_patterns were removed."""
    import importlib

    from core.dax.context_analyzer import DaxContextAnalyzer

    assert not hasattr(DaxContextAnalyzer, "detect_dax_anti_patterns")
    with pytest.raises(ImportError):
        importlib.import_module("core.research.dax_research")


# --- D-12: real INFO.RELATIONSHIPS() row shape ---------------------------------------------------


@pytest.mark.parametrize(
    "row,expected",
    [
        (
            {
                "IsActive": True,
                "FromCardinality": 2,
                "ToCardinality": 2,
                "CrossFilteringBehavior": 1,
            },
            True,
        ),
        (
            {
                "[IsActive]": True,
                "[FromCardinality]": 2,
                "[ToCardinality]": 1,
                "[CrossFilteringBehavior]": 2,
            },
            True,
        ),
        (
            {
                "IsActive": True,
                "FromCardinality": 2,
                "ToCardinality": 1,
                "CrossFilteringBehavior": 1,
            },
            False,
        ),
        (
            {
                "IsActive": False,
                "FromCardinality": 2,
                "ToCardinality": 2,
                "CrossFilteringBehavior": 2,
            },
            False,
        ),
        (
            {
                "isActive": True,
                "fromCardinality": "Many",
                "toCardinality": "One",
                "crossFilteringBehavior": "BothDirections",
            },
            True,
        ),
    ],
)
def test_cross_filtered_paths_from_pascalcase_numeric_rows(row, expected):
    assert ModelSchema(relationships=[row]).has_cross_filtered_paths() is expected
    assert ModelSchema(relationships=[row]).has_limited_relationships() is expected


def test_allcrossfiltered_rule_fires_on_live_row_shape():
    schema = ModelSchema(
        relationships=[
            {
                "IsActive": True,
                "FromCardinality": 2,
                "ToCardinality": 2,
                "CrossFilteringBehavior": 2,
            }
        ]
    )
    got = ids("DIVIDE([Sales], CALCULATE([Sales], ALL(Sales)))", AnalysisContext(schema=schema))
    assert "PY_ALL_VS_ALLCROSSFILTERED" in got


# --- D-13: nesting depth ------------------------------------------------------------------------


def test_single_calculate_deep_in_expression_is_not_nested_calculate():
    assert "PERF_NESTED_CALCULATE" not in ids("DIVIDE(SUMX(Customer, CALCULATE([Sales])), 1)")


def test_real_calculate_nesting_detected_even_when_other_functions_are_deeper():
    dax = "CALCULATE(SUMX(T, ROUND(ABS(1), 0)) + CALCULATE(CALCULATE([S], T[a] = 1), T[b] = 2), T[c] = 3)"
    assert ids(dax).count("PERF_NESTED_CALCULATE") == 1


def test_deep_nesting_reports_once():
    dax = "IF(IF(IF(IF(IF(IF(IF(IF(1,1,1),1,1),1,1),1,1),1,1),1,1),1,1),1,1)"
    assert ids(dax).count("MAINT_DEEP_NESTING") == 1


# --- D-26 / D-27: suppressions -------------------------------------------------------------------


def test_iferror_xirr_downgrade_uses_tokens_not_substrings():
    real = [
        i
        for i in issues("IFERROR(XIRR(T, T[v], T[d]), BLANK())")
        if i.rule_id == "CORR_IFERROR_USAGE"
    ]
    assert real and real[0].severity == "info"
    fake = [
        i for i in issues("IFERROR(1, 0) // XIRR in a comment") if i.rule_id == "CORR_IFERROR_USAGE"
    ]
    assert fake and fake[0].severity != "info"


def test_extended_column_downgrade_ignores_strings():
    res = issues('CALCULATETABLE(FILTER(T, T[a] = "[@x]"))')
    f = [i for i in res if i.rule_id == "PERF_CALCULATETABLE_FILTER"]
    assert not f or f[0].severity != "info"


def test_lint_disable_comment_suppresses_and_reports():
    res = get_shared_analyzer().analyze('FORMAT([Sales], "0") // lint-disable CB005')
    assert [i.rule_id for i in res.issues] == [] and res.suppressed == ["CB005"]
    res = get_shared_analyzer().analyze('FORMAT([Sales], "0") /* lint-disable-all */')
    assert res.issues == [] and res.suppressed == ["CB005"]


def test_context_suppressed_rules():
    ctx = AnalysisContext(suppressed_rules={"cb005"})
    res = get_shared_analyzer().analyze('FORMAT([Sales], "0")', ctx)
    assert res.issues == [] and res.suppressed == ["CB005"]
