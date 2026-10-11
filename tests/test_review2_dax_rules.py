"""2026-10-03 deep review, slice D: rule precision against the canon (D-02 .. D-11, D-17, D-19, D-20,
D-21) plus the one-advice-engine fix (D-01).

The canon is ``~/.claude/skills/dax`` (dax-core-rules / dax-anti-patterns); the corpus fixture
(``test_review2_dax_corpus``) holds the broad good/bad sweep, this file pins each rule change.
"""

import json
import pathlib

import pytest

from core.dax.analyzer.models import AnalysisContext, ModelSchema
from core.dax.analyzer.unified_analyzer import get_shared_analyzer


def run(dax, schema=None):
    ctx = AnalysisContext(schema=schema) if schema is not None else None
    return get_shared_analyzer().analyze(dax, ctx).issues


def ids(dax, schema=None):
    return [i.rule_id for i in run(dax, schema)]


def sev(dax, rule_id, schema=None):
    return [i.severity for i in run(dax, schema) if i.rule_id == rule_id]


# ----------------------------------------------------------------------------------- D-01


def test_d01_anti_patterns_block_is_a_view_of_the_unified_findings():
    res = get_shared_analyzer().analyze("SUMX(Sales, IF(Sales[Qty] > 0, Sales[Amount], 0))")
    block = res.to_anti_patterns_format()
    assert block["patterns_detected"] >= 1
    assert "CB001" in block["pattern_matches"]
    assert block["articles"] == []  # the research "article" engine is gone
    # info findings are not "anti patterns"
    info_only = get_shared_analyzer().analyze("SUMX(Sales, Sales[Amount])")
    assert info_only.to_anti_patterns_format()["patterns_detected"] == 0


def test_d01_idiomatic_measures_draw_no_high_severity_advice():
    """The old regex block reported TOTALYTD / a 3-VAR measure / IF(..., BLANK(), ...) as 'high'."""
    for dax in (
        "TOTALYTD([Sales], 'Date'[Date])",
        "VAR _a = [A] VAR _b = [B] VAR _c = [C] RETURN _a + _b + _c",
        "IF([x] = 0, BLANK(), [y])",
    ):
        assert not [i for i in run(dax) if i.severity in ("high", "critical")], dax


def test_d01_context_debugger_report_uses_the_unified_engine():
    from core.dax.context_debugger import DaxContextDebugger

    out = DaxContextDebugger().generate_debug_report(
        "CALCULATE([Sales], FILTER(ALL('Date'), 'Date'[Date] <= [MaxDate]))"
    )
    text = out if isinstance(out, str) else json.dumps(out, default=str)
    assert "STATIC ANALYSIS (unified rule engine)" in text
    assert "CORR_MEASURE_IN_FILTER" in text
    assert "ANTI-PATTERN DETECTION & RESEARCH" not in text


def test_d01_improvements_have_no_research_detection():
    from core.dax import DaxContextAnalyzer, DaxContextDebugger

    dax = "CALCULATE([Sales], FILTER(ALL(Product), [Product Sales] > 10))"
    analysis = DaxContextAnalyzer().analyze_context_transitions(dax)
    out = DaxContextDebugger().generate_improved_dax(dax, analysis)
    assert not any(
        "Anti-pattern detected" in str(i.get("issue", "")) for i in out.get("improvements", [])
    )


# ----------------------------------------------------------------------------------- D-02


def test_d02_hoisting_never_leaves_a_conditional_branch():
    from core.dax.optimizer.pipeline import OptimizationPipeline

    pipe = OptimizationPipeline()
    guarded = "IF(HASONEVALUE(T[k]), [Heavy] * 2 + [Heavy] / 3 + [Heavy], BLANK())"
    out = pipe.optimize_expression(guarded)
    # no top-level VAR in front of the IF: the VAR lives INSIDE the branch
    assert not out.final_dax.lstrip().upper().startswith("VAR")
    assert "VAR _Heavy = [Heavy] RETURN" in out.final_dax
    assert out.final_dax.index("HASONEVALUE") < out.final_dax.index("VAR _Heavy")
    assert all(
        rw.confidence != "high" for rw in out.rewrites if rw.strategy == "variable_extraction"
    )


def test_d02_switch_lanes_are_not_repeats_and_are_never_hoisted():
    from core.dax.optimizer.pipeline import OptimizationPipeline

    lanes = 'SWITCH(SELECTEDVALUE(T[k]), "a", [Heavy], "b", [Heavy] * 2, "c", [Heavy] * 3, 0)'
    assert "MAINT_REPEATED_MEASURE" not in ids(lanes)
    out = pipe_final(OptimizationPipeline(), lanes)
    assert out in (None, lanes)  # nothing to do: only one lane runs


def pipe_final(pipe, dax):
    return pipe.optimize_expression(dax).final_dax


def test_d02_condition_occurrence_makes_the_top_level_vap_neutral():
    from core.dax.optimizer.pipeline import OptimizationPipeline

    dax = "IF(ISBLANK([X]), BLANK(), [X] * 2 + [X] / 3)"  # [X] is evaluated unconditionally anyway
    out = OptimizationPipeline().optimize_expression(dax)
    assert out.final_dax.startswith("VAR _X = [X]")
    assert all(
        rw.confidence == "medium" for rw in out.rewrites if rw.strategy == "variable_extraction"
    )


def test_d02_repeated_measure_counts_inside_one_branch_only():
    assert "MAINT_REPEATED_MEASURE" in ids(
        "IF(HASONEVALUE(T[k]), [Heavy] * 2 + [Heavy] / 3 + [Heavy], BLANK())"
    )
    assert "MAINT_REPEATED_MEASURE" not in ids("IF(c, [H] + [H], IF(d, [H] * [H], [H]))")


def test_d02_variable_scanner_reports_branch_scoped_vars_and_no_invented_savings():
    from core.dax.code_rewriter import VariableOptimizationScanner

    res = VariableOptimizationScanner().scan_for_optimizations(
        "IF(HASONEVALUE(T[k]), [Heavy] * 2 + [Heavy] / 3 + [Heavy], BLANK())"
    )
    assert res["success"] and res["opportunities_found"] == 1
    assert "total_potential_savings_percent" not in res
    assert "estimated_savings_percent" not in res["opportunities"][0]
    assert "INSIDE" in res["opportunities"][0]["suggestion"]
    # SWITCH lanes: nothing to report
    lanes = VariableOptimizationScanner().scan_for_optimizations(
        'SWITCH(SELECTEDVALUE(T[k]), "a", [H] + [H], "b", [H] * [H], 0)'
    )
    assert lanes["opportunities_found"] == 0


def test_d02_scanner_ignores_text_inside_strings_and_comments():
    from core.dax.code_rewriter import VariableOptimizationScanner

    res = VariableOptimizationScanner().scan_for_optimizations(
        'CALCULATE(1) // CALCULATE(1) CALCULATE(1)\n& "CALCULATE( CALCULATE( FILTER( FILTER("'
    )
    assert res["opportunities_found"] == 0


# ----------------------------------------------------------------------------------- D-03


def test_d03_int_is_not_a_rounding_callback():
    assert "CB008" not in ids("SUMX(Sales, INT(Sales[Qty] > 0) * Sales[Amount])")
    assert "CB008" in ids("SUMX(Sales, ROUND(Sales[Amount] * Sales[Rate], 2))")


@pytest.mark.parametrize(
    "dax,rule",
    [
        (
            "SUMX(SUMMARIZE(Sales, Sales[Rate]), ROUND(CALCULATE(SUM(Sales[Amount])) * Sales[Rate], 2))",
            "CB008",
        ),
        ("SUMX(VALUES('Date'[Year]), IF([Sales] > 0, [Sales], 0))", "CB001"),
        ("SUMX(DISTINCT(Sales[Rate]), ROUND(Sales[Rate], 1))", "CB008"),
        ("VAR _t = SUMMARIZE(Sales, Sales[Rate]) RETURN SUMX(_t, ROUND(Sales[Rate], 1))", "CB008"),
    ],
)
def test_d03_iterating_a_reduced_grain_is_info_only(dax, rule):
    assert sev(dax, rule) == ["info"]


@pytest.mark.parametrize(
    "dax,rule,expected",
    [
        ("SUMX(Sales, IF(Sales[Qty] > 0, Sales[Amount], 0))", "CB001", "high"),
        ("SUMX(FILTER(Sales, Sales[Qty] > 1), IF(Sales[Qty] > 5, 1, 0))", "CB001", "high"),
        ("SUMX(Sales, ROUND(Sales[Amount], 2))", "CB008", "medium"),
        ('SUMX(RELATEDTABLE(Sales), SEARCH("x", Sales[Name], 1, 0))', "CB009", "medium"),
    ],
)
def test_d03_fact_grain_keeps_the_full_severity(dax, rule, expected):
    assert sev(dax, rule) == [expected]


def test_d03_containsstring_in_an_iterator_is_a_string_callback():
    assert "CB009" in ids('SUMX(Sales, IF(CONTAINSSTRING(Sales[Name], "x"), 1, 0))')


# ----------------------------------------------------------------------------------- D-04 / D-05


@pytest.mark.parametrize(
    "dax",
    [
        "[Seconds] / (60 * 60 * 24)",
        "[Sales] / -1000",
        "[a] / POWER(10, 6)",
        "[Sales] / 1000",
        "CALCULATE(SUMX(Sales, Sales[Amount] / Sales[Qty]), KEEPFILTERS(Sales[Qty] <> 0))",
        "CALCULATE(SUMX(Sales, Sales[Amount] / Sales[Qty]), Sales[Qty] > 0)",
        "SUMX(FILTER(Sales, Sales[Qty] <> 0), Sales[Amount] / Sales[Qty])",
    ],
)
def test_d04_constant_and_prefiltered_denominators_are_not_flagged(dax):
    assert "CORR_DIVISION_WITHOUT_DIVIDE" not in [
        i.rule_id for i in run(dax) if i.severity != "info"
    ]


@pytest.mark.parametrize(
    "dax",
    [
        "[Revenue] / [Units]",
        "[Sales] / 0",
        "[a] / ([b] - [c])",
        "SUMX(Sales, Sales[Amount] / Sales[Qty])",
    ],
)
def test_d04_real_unprotected_division_is_still_found(dax):
    assert "CORR_DIVISION_WITHOUT_DIVIDE" in ids(dax)


def test_d04_division_inside_an_iterator_is_advice_not_a_defect():
    assert sev("SUMX(Sales, Sales[Amount] / Sales[Qty])", "CORR_DIVISION_WITHOUT_DIVIDE") == [
        "info"
    ]
    assert sev("[Revenue] / [Units]", "CORR_DIVISION_WITHOUT_DIVIDE") == ["medium"]


def test_d05_directquery_inverts_the_iferror_and_divide_advice():
    dq = ModelSchema.from_model(
        {
            "tables": [
                {"name": "Sales", "mode": "directQuery"},
                {"name": "Product", "mode": "import"},
            ]
        }
    )
    dax = "IFERROR(Sales[Amount] / Sales[Qty], 0)"
    out = {i.rule_id: i for i in run(dax, dq)}
    assert out["CORR_IFERROR_USAGE"].severity == "info"
    assert out["CORR_DIVISION_WITHOUT_DIVIDE"].severity == "info"
    assert "DirectQuery" in out["CORR_IFERROR_USAGE"].description
    # a table that is NOT DirectQuery keeps the Import advice
    mixed = "IFERROR(Sales[Amount] / Product[Cost], 0)"
    assert sev(mixed, "CORR_IFERROR_USAGE", dq) == ["medium"]
    # unknown storage mode: advice stays, worded conditionally
    unknown = {i.rule_id: i for i in run(dax)}
    assert unknown["CORR_IFERROR_USAGE"].severity == "medium"
    assert "DirectQuery" in unknown["CORR_IFERROR_USAGE"].description


def test_d05_storage_mode_normalisation():
    from core.dax.analyzer.models import _norm_storage_mode

    assert _norm_storage_mode("DirectQuery") == "directquery"
    assert _norm_storage_mode(1) == "directquery"
    assert _norm_storage_mode("1") == "directquery"
    assert _norm_storage_mode(0) == "import"
    # TOM ModeType: 0 Import, 1 DirectQuery, 2 Default, 3 Push, 4 Dual, 5 DirectLake
    assert _norm_storage_mode(3) == "import"  # Push partitions hold imported data
    assert _norm_storage_mode(4) == "dual"
    assert _norm_storage_mode(5) == "directlake"
    assert _norm_storage_mode(2) == ""  # TOM ModeType.Default resolves per model
    assert _norm_storage_mode(None) == ""


def test_d05_use_divide_refuses_directquery_only_measures():
    from types import SimpleNamespace as NS

    from core.codemods.transforms.use_divide import UseDivide

    def model(mode):
        m = NS(name="Ratio", expression="Sales[Amount] / Sales[Qty]", table_name="Sales")
        return NS(tables=[NS(name="Sales", storage_mode=mode, columns=[], measures=[m])])

    assert UseDivide().preview(model("directquery"), {}).edits == []
    assert UseDivide().preview(model("directquery"), {}).skipped
    assert len(UseDivide().preview(model("import"), {}).edits) == 1
    assert len(UseDivide().preview(model(""), {}).edits) == 1


# ----------------------------------------------------------------------------------- D-06


def test_d06_vars_shared_with_the_condition_are_not_defeating_short_circuit():
    margin = (
        "VAR _Sales = CALCULATE([Sales], 'Date'[Year] = 2024)\n"
        "VAR _Cost = CALCULATE([Cost], 'Date'[Year] = 2024)\n"
        "RETURN IF(_Sales = 0, BLANK(), DIVIDE(_Sales - _Cost, _Sales))"
    )
    assert "PY_VAR_DEFEATING_SHORTCIRCUIT" not in ids(margin)


def test_d06_exclusive_lane_vars_are_flagged_at_medium():
    dax = (
        "VAR _A = CALCULATE([Sales], FilterA)\nVAR _B = CALCULATE([Sales LY], FilterB)\n"
        'RETURN SWITCH(Selection, "A", _A, "B", _B, BLANK())'
    )
    assert sev(dax, "PY_VAR_DEFEATING_SHORTCIRCUIT") == ["medium"]


def test_d06_a_var_used_by_a_later_var_is_not_confined():
    dax = (
        "VAR _A = CALCULATE([Sales], FilterA)\nVAR _B = CALCULATE([Sales LY], FilterB)\n"
        "VAR _Both = _A + _B\nRETURN IF(Selection = 1, _A, _B)"
    )
    assert "PY_VAR_DEFEATING_SHORTCIRCUIT" not in ids(dax)


# ----------------------------------------------------------------------------------- D-07


def test_d07_calculatetable_filter_only_flagged_for_a_simple_column_predicate():
    measure_pred = (
        "CALCULATETABLE(FILTER(ALL('Product'[Name]), [Sales] > 100), 'Date'[Year] = 2024)"
    )
    assert "PERF_CALCULATETABLE_FILTER" not in ids(measure_pred)
    simple = "CALCULATETABLE(FILTER(Sales, Sales[Qty] > 1), 'Date'[Year] = 2024)"
    assert sev(simple, "PERF_CALCULATETABLE_FILTER") == ["medium"]


# ----------------------------------------------------------------------------------- D-08


def test_d08_all_table_is_flagged_only_as_an_unreadded_calculate_modifier():
    assert "PY_ALL_TABLE_VS_COLUMN" in ids("CALCULATE([Sales], ALL(Product))")
    # SAFE form: the sibling VALUES re-adds the visible values of the same table
    assert "PY_ALL_TABLE_VS_COLUMN" not in ids(
        "CALCULATE([Sales], ALL(Customer), VALUES(Customer[Continent]))"
    )
    # window function / RANKX table arguments are table expressions
    assert "PY_ALL_TABLE_VS_COLUMN" not in ids(
        "INDEX(-1, ALL('Status'), ORDERBY('Status'[Date], ASC), PARTITIONBY('Status'[EntityKey]))"
    )
    assert "PY_ALL_TABLE_VS_COLUMN" not in ids("RANKX(ALL(Product), [Sales])")


def test_d08_running_total_form_gets_one_info_finding_not_two():
    dax = "CALCULATE([Sales], FILTER(ALL('Date'), 'Date'[Date] <= MAX('Date'[Date])))"
    got = run(dax)
    assert [(i.rule_id, i.severity) for i in got] == [("PERF_FILTER_ALL", "info")]
    # a measure predicate keeps the AP-11 severity
    assert sev("FILTER(ALL(Product), [Product Sales] > 10000)", "PERF_FILTER_ALL") == ["medium"]


def test_d08_all_as_modifier_has_an_info_removefilters_hint():
    assert sev(
        "CALCULATE([Sales], ALL(Customer), VALUES(Customer[Continent]))",
        "PY_ALL_PREFER_REMOVEFILTERS",
    ) == ["info"]
    assert "PY_ALL_PREFER_REMOVEFILTERS" not in ids("CALCULATE([Sales], REMOVEFILTERS(Customer))")
    assert "PY_ALL_PREFER_REMOVEFILTERS" not in ids("RANKX(ALL(Product), [Sales])")


# ----------------------------------------------------------------------------------- D-09


def _schema_with_bidirectional_bridge(**extra):
    return ModelSchema(
        measure_names={"sales"},
        table_names={"sales", "product", "bridge", "users", "date"},
        relationships=[
            {
                "IsActive": True,
                "FromTable": "Bridge",
                "ToTable": "Users",
                "FromCardinality": 2,
                "ToCardinality": 1,
                "CrossFilteringBehavior": 2,
            },
            {
                "IsActive": True,
                "FromTable": "Sales",
                "ToTable": "Product",
                "FromCardinality": 2,
                "ToCardinality": 1,
                "CrossFilteringBehavior": 1,
            },
        ],
        **extra,
    )


def test_d09_all_is_only_flagged_for_tables_near_the_limited_relationship():
    schema = _schema_with_bidirectional_bridge()
    # a column never matters, a far-away table neither
    assert "PY_ALL_VS_ALLCROSSFILTERED" not in ids("CALCULATE([Sales], ALL('Date'[Year]))", schema)
    assert "PY_ALL_VS_ALLCROSSFILTERED" not in ids(
        "DIVIDE([Sales], CALCULATE([Sales], REMOVEFILTERS(Product)))", schema
    )
    assert "PY_ALL_VS_ALLCROSSFILTERED" in ids("CALCULATE([Sales], ALL(Users))", schema)
    assert "PY_ALL_VS_ALLCROSSFILTERED" in ids("CALCULATE([Sales], ALL(Bridge))", schema)


def test_d09_rls_rule_needs_a_secured_endpoint_and_is_capped_at_medium():
    cf = "CALCULATE([Sales], CROSSFILTER(Sales[k], Product[k], BOTH))"
    unsecured = _schema_with_bidirectional_bridge(roles=[{"name": "RLS"}], secured_tables={"users"})
    assert "PY_RLS_BIDIRECTIONAL_INTERACTION" not in ids(cf, unsecured)
    secured = _schema_with_bidirectional_bridge(roles=[{"name": "RLS"}], secured_tables={"product"})
    assert sev(cf, "PY_RLS_BIDIRECTIONAL_INTERACTION", secured) == ["medium"]
    # secured tables unknown: still medium, never high
    unknown = _schema_with_bidirectional_bridge(roles=[{"name": "RLS"}])
    assert sev(cf, "PY_RLS_BIDIRECTIONAL_INTERACTION", unknown) == ["medium"]


def test_d09_schema_topology_helpers():
    schema = _schema_with_bidirectional_bridge()
    assert schema.named_edges_available()
    assert schema.cross_filtered_tables() >= {"bridge", "users"}
    assert "product" not in schema.cross_filtered_tables(hops=1)
    assert "sales" in schema.one_side_tables() or "product" in schema.one_side_tables()


# ----------------------------------------------------------------------------------- D-10


@pytest.mark.parametrize(
    "dax",
    [
        'CALCULATE([Sales], Geography[Country] = "US")',
        "CALCULATE(COUNTROWS(Sales), Sales[Amount] > 1000)",
        "CALCULATE([Sales], 'Currency'[Code] = \"EUR\")",
    ],
)
def test_d10_literal_predicates_on_non_date_columns_are_not_flagged(dax):
    assert "CORR_FIXED_FILTER_IN_CALCULATE" not in ids(dax)


@pytest.mark.parametrize(
    "dax",
    [
        "CALCULATE([Sales], 'Date'[Year] = 2024)",
        "CALCULATE([Sales], Date[Year] = 2024)",
        "CALCULATE([Sales], 'Date'[Date] >= DATE(2024, 1, 1))",
        "CALCULATE([Sales], 'Date'[Date] >= dt\"2024-01-01\")",
    ],
)
def test_d10_hardcoded_dates_are_still_noted_at_info(dax):
    assert sev(dax, "CORR_FIXED_FILTER_IN_CALCULATE") == ["info"]


# ----------------------------------------------------------------------------------- D-11


def test_d11_table_val_parameter_as_filter_is_valid_and_untyped_is_low():
    assert "PY_UDF_EAGER_EVAL_RISK" not in ids(
        "FUNCTION SalesFor = (dates : TABLE VAL) =>\n    CALCULATE([Sales], dates)"
    )
    assert sev("FUNCTION Twice = (x) => x * 2", "PY_UDF_UNTYPED_PARAMETER") == ["low"]
    assert sev(
        "FUNCTION F = (x : INT64 VAL) => CALCULATE([Sales], x)", "PY_UDF_EAGER_EVAL_RISK"
    ) == ["medium"]


# ----------------------------------------------------------------------------------- D-17


def test_d17_severity_and_scope_calibration():
    assert "PERF_NATURALLEFTOUTERJOIN" not in ids("NATURALINNERJOIN(_q, _u)")
    assert "PERF_NATURALLEFTOUTERJOIN" in ids("NATURALLEFTOUTERJOIN(A, B)")
    # table argument / HASONEVALUE idiom: no DISTINCTCOUNT advice
    assert "PERF_COUNTROWS_VALUES_VS_DISTINCTCOUNT" not in ids("COUNTROWS(VALUES(Sales))")
    assert "PERF_COUNTROWS_VALUES_VS_DISTINCTCOUNT" not in ids(
        "COUNTROWS(VALUES(Customer[Id])) = 1"
    )
    assert "PERF_COUNTROWS_VALUES_VS_DISTINCTCOUNT" not in ids(
        "COUNTROWS(FILTER(VALUES(Customer[Id]), [Sales] > 1))"
    )
    assert sev("COUNTROWS(VALUES(Customer[Id]))", "PERF_COUNTROWS_VALUES_VS_DISTINCTCOUNT") == [
        "low"
    ]
    assert sev(
        "SUMX(FILTER(Sales, Sales[d] <= EARLIER(Sales[d])), Sales[a])", "PERF_EARLIER_DEPRECATED"
    ) == ["medium"]
    assert sev(
        "CALCULATE([Balance], LASTNONBLANK('Date'[Date], [Balance]))", "PY_SEMI_ADDITIVE_RANKING"
    ) == ["low"]
    for rid in ("PERF_CROSSFILTER_IN_MEASURE", "PERF_USERELATIONSHIP_IN_MEASURE"):
        assert rid not in get_shared_analyzer().rule_ids()


def test_nondescriptive_var_underscore_prefix_is_an_explicit_private_name():
    assert "MAINT_NONDESCRIPTIVE_VAR" not in ids(
        "IF([Sales] > 0, VAR _c = [Cost] RETURN [Sales] - _c)"
    )
    assert "MAINT_NONDESCRIPTIVE_VAR" in ids("VAR a = 1 RETURN a")
    assert "MAINT_NONDESCRIPTIVE_VAR" in ids("VAR x1 = 1 RETURN x1")


# ----------------------------------------------------------------------------------- D-19 / D-20


def test_d19_canon_functions_are_known_to_the_function_db():
    from core.dax.knowledge import DaxFunctionDatabase

    db = DaxFunctionDatabase.get()
    for name in (
        "USERPRINCIPALNAME",
        "USEROBJECTID",
        "CUSTOMDATA",
        "ORDERBY",
        "PARTITIONBY",
        "MATCHBY",
        "CONTAINSSTRING",
        "CONTAINSSTRINGEXACT",
        "ISSUBTOTAL",
        "FIRSTNONBLANKVALUE",
        "LASTNONBLANKVALUE",
        "TEXTCONTAINS",
        "TEXTSIMILARITY",
        "APPROXIMATEDISTINCTCOUNT",
        "PREVIOUS",
        "NEXT",
        "FIRST",
        "LAST",
        "COLLAPSEALL",
        "EXPANDALL",
        "ISATLEVEL",
        "RANGE",
        "EXTERNALMEASURE",
        "COUNTAX",
        "INFO.MEASURES",
        "INFO.TABLES",
        "INFO.MODEL",
        "INFO.CALENDARS",
        "INFO.USERDEFINEDFUNCTIONS",
    ):
        assert db.is_function(name), name
    assert db.lookup("CONTAINSSTRING").callback_risk == "high"


_CANON = pathlib.Path.home() / ".claude" / "skills" / "dax"
_ALLOW = {
    # prose / acronyms / keywords that are followed by "(" in the canon text but are not DAX functions
    "UDF",
    "DAX",
    "TOM",
    "VAR",
    "TE3",
    "TE2",
    "OLS",
    "KQL",
    "VALID",
    "SKU",
    "RLS",
    "STRUCTURE",
    "EQUIVALENCE",
    "BAD",
    "GOOD",
    "CPU",
    "RLE",
    "TABLETRAITS",
    "TMDL",
    "OPTIMIZED",
    "YTD",
    "SLOWER",
    "BPA",
    "NONBLANK",
    "DQV",
    "DOM",
    "JSON",
    "REPL",
    "PS7",
    "XMLA",
    "FILTERS_X",
}


@pytest.mark.skipif(not _CANON.is_dir(), reason="the dax skill canon is not installed")
def test_d19_every_function_named_in_the_canon_is_known_or_allow_listed():
    import re

    from core.dax.knowledge import DaxFunctionDatabase

    db = DaxFunctionDatabase.get()
    unknown = set()
    for f in list(_CANON.rglob("*.md")):
        text = f.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(
            r"(?<![A-Za-z0-9_.\['\"])([A-Z][A-Z0-9]{2,}(?:\.[A-Z0-9]+)*)\s?\(", text
        ):
            if not db.is_function(m.group(1)) and m.group(1) not in _ALLOW:
                unknown.add(m.group(1))
    # INFO.* introspection names the canon mentions are catalogued as a family
    unknown = {u for u in unknown if not u.startswith("INFO.")}
    assert unknown == set(), sorted(unknown)


def test_d20_calendar_argument_is_a_calendar_not_a_table():
    from core.dax.dax_reference_parser import parse_dax_references, scan_references

    refs = scan_references("TOTALYTD([Sales], 'Fiscal Calendar')")
    assert [(r.kind, r.table) for r in refs if r.kind != "bracket"] == [
        ("calendar", "Fiscal Calendar")
    ]
    assert (
        "Fiscal Calendar"
        not in parse_dax_references("TOTALYTD([Sales], 'Fiscal Calendar')")["tables"]
    )
    # a table in a real table position is still a table
    assert [(r.kind, r.table) for r in scan_references("COUNTROWS('Fiscal Calendar')")] == [
        ("table", "Fiscal Calendar")
    ]
    # classic date-column form is unchanged
    assert [r.kind for r in scan_references("TOTALYTD([Sales], 'Date'[Date])")] == [
        "bracket",
        "qualified",
    ]


# ----------------------------------------------------------------------------------- D-21 / E-08


def test_d21_extract_measure_warns_about_calculation_groups():
    from types import SimpleNamespace as NS

    from core.codemods.transforms.extract_measure import ExtractMeasure

    model = NS(
        tables=[
            NS(
                name="Sales",
                columns=[],
                measures=[NS(name="M1", expression="SUM(Sales[Amount]) * 2", table_name="Sales")],
            )
        ]
    )
    out = ExtractMeasure().preview(
        model,
        {
            "expression": "SUM(Sales[Amount])",
            "new_name": "Amt",
            "target_table": "Sales",
            "auto_detect": True,
        },
    )
    assert any("calculation group" in w.lower() for w in out.warnings)


def test_d21_move_measure_batch_reads_quoted_refs_case_insensitively():
    from types import SimpleNamespace as NS

    from core.codemods.transforms.move_measure_batch import MoveMeasureBatch

    a = NS(name="Base", expression="SUM(T[x])", table_name="My Table")
    b = NS(name="Derived", expression="[base] * 2", table_name="My Table")
    model = NS(tables=[NS(name="My Table", columns=[], measures=[b, a])])
    out = MoveMeasureBatch().preview(
        model,
        {
            "moves": [
                {"measure_ref": "'My Table'[Derived]", "target_table": "Dst"},
                {"measure_ref": "'my table'[BASE]", "target_table": "Dst"},
            ]
        },
    )
    assert not out.warnings, out.warnings
    assert [e.target for e in out.edits] == [
        "measure:'my table'[BASE]",
        "measure:'My Table'[Derived]",
    ]


def test_e08_every_advertised_rewrite_strategy_has_an_implementation():
    from core.dax.analyzer.models import AnalysisIssue
    from core.dax.knowledge import DaxFunctionDatabase
    from core.dax.optimizer.strategies import load_strategies

    analyzer = get_shared_analyzer()
    names = set()
    rules_dir = (
        pathlib.Path(__file__).resolve().parent.parent / "core" / "dax" / "knowledge" / "rules"
    )
    for f in rules_dir.glob("*.json"):
        names |= {
            r["rewrite_strategy"]
            for r in json.loads(f.read_text(encoding="utf-8"))
            if r.get("rewrite_strategy")
        }
    names |= {
        r.rewrite_strategy for r in analyzer._python_rules if getattr(r, "rewrite_strategy", None)
    }
    assert names, "no strategy names at all?"
    strategies = load_strategies()
    db = DaxFunctionDatabase.get()
    for name in sorted(names):
        issue = AnalysisIssue(
            "X", "performance", "low", "t", "d", "f", "static", rewrite_strategy=name
        )
        assert any(
            s.can_apply(issue, [], db) for s in strategies
        ), f"{name}: no strategy implements it"


def test_e08_calculatetable_filter_suggestion_keeps_intersect_semantics():
    from core.dax.optimizer.pipeline import OptimizationPipeline

    res = OptimizationPipeline().optimize_expression(
        "CALCULATETABLE(FILTER(Sales, Sales[Qty] > 1), 'Date'[Year] = 2024)"
    )
    sugg = [r for r in res.rewrites if r.suggestion_only and "KEEPFILTERS" in r.rewritten_fragment]
    assert sugg and "KEEPFILTERS(Sales[Qty] > 1)" in sugg[0].rewritten_fragment
    assert res.final_dax in (
        None,
        "CALCULATETABLE(FILTER(Sales, Sales[Qty] > 1), 'Date'[Year] = 2024)",
    )  # never auto-applied


def test_d29_health_score_caps_the_deduction_per_rule():
    res = get_shared_analyzer().analyze("\n+ ".join(["CALCULATE([X], 'Date'[Year] = 2024)"] * 6))
    info = [i for i in res.issues if i.rule_id == "CORR_FIXED_FILTER_IN_CALCULATE"]
    assert len(info) >= 4
    # six info findings of one rule cost 3 points (cap), plus MAINT_REPEATED_CALCULATE (low, 2)
    assert res.health_score >= 100 - 3 - 2
