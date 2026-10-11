"""2026-10-03 deep review, slice D (D-25 .. D-27): the static rules the canon coverage matrix listed
as MISSING. Every rule has a bad snippet that must fire and good snippets that must stay silent.
Severity is asserted where the calibration is the point (high = a runtime failure / silent wrong
result, info = a style / design hint).
"""

import pytest

from core.dax.analyzer.models import AnalysisContext, ModelSchema
from core.dax.analyzer.unified_analyzer import get_shared_analyzer

_STD = ModelSchema(
    measure_names={"sales", "maxdate", "cost", "margin", "revenue", "product sales"},
    column_names={"qty", "price", "amount", "country", "year", "date"},
    table_names={"sales", "product", "date", "customer"},
)


def run(dax, schema=_STD):
    return get_shared_analyzer().analyze(dax, AnalysisContext(schema=schema)).issues


def fired(dax, rule_id, schema=_STD):
    return [i for i in run(dax, schema) if i.rule_id == rule_id]


# (rule id, bad snippet, [good snippets], expected severity of the bad one)
CASES = [
    # --- D-25 runtime failures ------------------------------------------------------------------
    (
        "CORR_MEASURE_IN_FILTER",
        "CALCULATE([Sales], FILTER(ALL('Date'), 'Date'[Date] <= [MaxDate]))",
        [
            "CALCULATE([Sales], FILTER(ALL('Date'), 'Date'[Date] <= MAX('Date'[Date])))",
            "COUNTROWS(FILTER(ALL(Product), [Product Sales] > 100))",  # measure vs constant: ranking filter
            "VAR _m = [MaxDate] RETURN CALCULATE([Sales], FILTER(ALL('Date'), 'Date'[Date] <= _m))",
        ],
        "high",
    ),
    (
        "CORR_FILTER_ARG_NO_TRANSITION",
        "SUMX(Customers, CALCULATE([Sales], 'Date'[Year] = SELECTEDVALUE(Customers[BirthYear])))",
        [
            "SUMX(Customers, VAR y = Customers[BirthYear] RETURN CALCULATE([Sales], 'Date'[Year] = y))",
            "SUMX(Customers, CALCULATE([Sales], 'Date'[Year] = CALCULATE(SELECTEDVALUE(Customers[BirthYear]))))",
            "CALCULATE([Sales], 'Date'[Year] = SELECTEDVALUE('Date'[Year]))",  # no iterator over that table
        ],
        "high",
    ),
    (
        "CORR_SELECTEDVALUE_FP",
        "VAR _sel = SELECTEDVALUE(FieldParam) RETURN _sel",
        [
            'VAR _sel = SELECTEDVALUE(FieldParam[Parameter Fields], "Sales") RETURN _sel',
            "FUNCTION F = (c : ANYREF) => SELECTEDVALUE(c)",
        ],
        "high",
    ),
    (
        "CORR_REMOVEFILTERS_AS_TABLE",
        'CALCULATE([Sales], FILTER(REMOVEFILTERS(Products[Category]), Products[Category] = "Bikes"))',
        [
            'CALCULATE([Sales], REMOVEFILTERS(Products[Category]), Products[Category] = "Bikes")',
            "CALCULATE([Sales], REMOVEFILTERS())",
            'CALCULATE([Sales], FILTER(ALL(Products[Category]), Products[Category] = "Bikes"))',
        ],
        "high",
    ),
    (
        "CORR_CROSS_TABLE_PREDICATE",
        "CALCULATE([X], TableA[Col] <= TableB[Col])",
        [
            "VAR _b = TableB[Col] RETURN CALCULATE([X], TableA[Col] <= _b)",
            "CALCULATE([X], Sales[Date] <= MAX('Date'[Date]))",  # the other table sits inside MAX(...)
            "CALCULATE([X], TableA[Col] > 1, TableB[Col] < 5)",  # separate arguments
        ],
        "high",
    ),
    (
        "CORR_EXTENDED_COLUMN_IN_FILTER",
        'ADDCOLUMNS(vTable, "@Result", CALCULATE(SUM(Sales[Amount]), OtherTable[Col] = [@ExtendedCol]))',
        [
            'ADDCOLUMNS(vTable, "@Result", VAR _val = [@ExtendedCol] RETURN CALCULATE(SUM(Sales[Amount]), OtherTable[Col] = _val))',
        ],
        "high",
    ),
    (
        "CORR_VAR_CONDITIONAL_FILTER",
        'VAR _f = IF(Selection = 1, Product[Color] = "Red", Product[Color] = "Blue") RETURN CALCULATE([Sales], _f)',
        [
            'VAR _c = "Red" RETURN CALCULATE([Sales], Product[Color] = _c)',
            'CALCULATE([Sales], IF(Selection = 1, Product[Color] = "Red", Product[Color] = "Blue"))',
        ],
        "high",
    ),
    # --- D-26 ------------------------------------------------------------------------------------
    (
        "PY_ALLEXCEPT_VS_ALL_VALUES",
        "CALCULATE([Sales], ALLEXCEPT(Customer, Customer[Continent]))",
        ["CALCULATE([Sales], REMOVEFILTERS(Customer), VALUES(Customer[Continent]))"],
        "info",
    ),
    (
        "PY_NEED_KEEPFILTERS",
        'DIVIDE(CALCULATE([Sales], \'Product\'[Color] IN {"Blue","Red","White"}), [Sales])',
        [
            'DIVIDE(CALCULATE([Sales], KEEPFILTERS(\'Product\'[Color] IN {"Blue","Red","White"})), [Sales])',
            "CALCULATE([Sales], 'Product'[Color] IN {\"Blue\"})",  # one value: replacing is the point
            "CALCULATE([Sales], 'Product'[Color] = \"Red\")",
        ],
        "medium",  # ratio over the same measure
    ),
    (
        "PY_ITERATOR_NEEDS_KEEPFILTERS",
        "AVERAGEX(VALUES('Date'[Year-Month]), [Sales])",
        [
            "AVERAGEX(KEEPFILTERS(VALUES('Date'[Year-Month])), [Sales])",
            "AVERAGEX(VALUES('Date'[Year]), [Sales])",
        ],
        "info",
    ),
    (
        "MAINT_SPLIT_AND_PREDICATE",
        "CALCULATE([Sales], Sales[Qty] > 1 && Sales[Price] > 2)",
        ["CALCULATE([Sales], Sales[Qty] > 1, Sales[Price] > 2)"],
        "info",
    ),
    # --- D-27 ------------------------------------------------------------------------------------
    (
        "PY_NEVER_BLANK",
        "SUM(Sales[Amount]) + 0",
        [
            "SUM(Sales[Amount])",
            "VAR _ForceZero = NOT ISEMPTY('Sales') RETURN [Sales] + IF(_ForceZero, 0)",
            "IF(ISBLANK([Sales]), BLANK(), [Sales] * 1.21)",
            "[A] * 2 + [B]",
        ],
        "medium",
    ),
    (
        "PERF_EXPANDED_TABLE_ITERATOR",
        "SUMX(Customer, [Sales])",
        [
            "SUMX(VALUES(Customer[CustomerKey]), [Sales])",
            "SUMX(Sales, Sales[Qty] * Sales[Price])",
            "SUMX(Sales, [Qty] * 2)",  # [Qty] is a column per the schema
        ],
        "medium",
    ),
    (
        "PERF_PK_SUMMARIZECOLUMNS",
        "SUMMARIZECOLUMNS('Product'[ProductKey], \"Total\", [Sales])",
        ["SUMMARIZECOLUMNS('Product'[Category], \"Total\", [Sales])"],
        "low",
    ),
    (
        "PERF_SUMMARIZECOLUMNS_INTERNAL_FILTER",
        "SUMMARIZECOLUMNS('Table'[Column], TREATAS({\"Value\"}, 'Table'[FilterColumn]), \"@Calculation\", [Measure])",
        [
            "CALCULATETABLE(SUMMARIZECOLUMNS('Table'[Column], \"@Calculation\", [Measure]), TREATAS({\"Value\"}, 'Table'[FilterColumn]))",
            "SUMMARIZECOLUMNS('Table'[Column], NONVISUAL(TREATAS({\"v\"}, 'Table'[F])), \"x\", [Measure])",
            'SUMMARIZECOLUMNS(ROLLUPADDISSUBTOTAL(\'Table\'[Column], "Tot"), "x", [Measure])',
        ],
        "medium",
    ),
    (
        "MAINT_REPEATED_CALCULATE",
        "DIVIDE([Total Sales] - CALCULATE([Total Sales], ALL(Region)), CALCULATE([Total Sales], ALL(Region)))",
        [
            "DIVIDE([Total Sales] - CALCULATE([Total Sales], ALL(Region)), CALCULATE([Total Sales], ALL(Product)))",
            "VAR _t = CALCULATE([Total Sales], ALL(Region)) RETURN DIVIDE([Total Sales] - _t, _t)",
        ],
        "low",
    ),
    (
        "MAINT_EVALUATEANDLOG",
        'EVALUATEANDLOG([Sales], "x")',
        ["[Sales]"],
        "medium",
    ),
    (
        "PERF_CALENDARAUTO",
        "CALENDARAUTO()",
        ["CALENDAR(DATE(2020, 1, 1), DATE(2030, 12, 31))"],
        "medium",
    ),
]


@pytest.mark.parametrize("rule_id,bad,good,severity", CASES, ids=[c[0] for c in CASES])
def test_bad_snippet_fires_with_the_calibrated_severity(rule_id, bad, good, severity):
    hits = fired(bad, rule_id)
    assert hits, f"{rule_id} did not fire on {bad!r}: {[i.rule_id for i in run(bad)]}"
    assert hits[0].severity == severity
    assert hits[0].fix_suggestion and hits[0].description


@pytest.mark.parametrize("rule_id,bad,good,severity", CASES, ids=[c[0] for c in CASES])
def test_good_snippets_stay_silent(rule_id, bad, good, severity):
    for dax in good:
        assert not fired(dax, rule_id), f"{rule_id} fired on the good form {dax!r}"


def test_new_rule_messages_cite_the_canon():
    cited = 0
    for rule_id, bad, _g, _s in CASES:
        if rule_id in ("MAINT_SPLIT_AND_PREDICATE",):
            continue
        text = " ".join(f"{i.description} {i.title}" for i in fired(bad, rule_id))
        if "canon" in text.lower() or "AP-" in text or "MS " in text or "DAX Optimizer" in text:
            cited += 1
    assert cited >= 14


def test_calc_group_override_needs_the_schema():
    dax = "CALCULATE([Sales], 'Time Intelligence'[Item] = \"YTD\")"
    assert not fired(dax, "PY_CALC_ITEM_OVERRIDE")  # no schema: silent
    schema = ModelSchema.from_model(
        {"tables": [{"name": "Time Intelligence", "is_calculation_group": True}, {"name": "Sales"}]}
    )
    assert fired(dax, "PY_CALC_ITEM_OVERRIDE", schema)
    assert not fired("CALCULATE([Sales], 'Date'[Year] = 2024)", "PY_CALC_ITEM_OVERRIDE", schema)


def test_udf_expr_parameter_needs_the_signature():
    dax = "SUMX(_Set, 'Formulas'[Multiplier] * AmountFor({ 'Formulas'[Element] }))"
    assert not fired(dax, "CORR_UDF_EXPR_BARE_COLUMN")
    schema = ModelSchema(udf_signatures={"AMOUNTFOR": ["expr"]})
    assert fired(dax, "CORR_UDF_EXPR_BARE_COLUMN", schema)
    fixed = "SUMX(_Set, VAR _e = 'Formulas'[Element] RETURN AmountFor({ _e }))"
    assert not fired(fixed, "CORR_UDF_EXPR_BARE_COLUMN", schema)
    # a val parameter is fine
    assert not fired(
        dax, "CORR_UDF_EXPR_BARE_COLUMN", ModelSchema(udf_signatures={"AMOUNTFOR": ["val"]})
    )


def test_udf_signature_helper_reads_passing_modes():
    from core.dax.analyzer.rules.udf_rules import udf_signature

    assert udf_signature("FUNCTION AmountFor = (x : ANYVAL EXPR, y : INT64 VAL, z) => x") == (
        "AmountFor",
        ["expr", "val", ""],
    )
    assert udf_signature("SUM(Sales[Amount])") is None


def test_qualification_rules_need_a_schema_that_knows_the_owning_table():
    schema = ModelSchema.from_model(
        {
            "tables": [{"name": "Sales"}],
            "measures": [{"name": "Total", "table": "Sales"}],
            "columns": [{"name": "Amount", "table": "Sales"}],
        }
    )
    assert fired("Sales[Total] * 2", "MAINT_QUALIFIED_MEASURE", schema)
    assert not fired("[Total] * 2", "MAINT_QUALIFIED_MEASURE", schema)
    assert not fired("SUM(Sales[Amount])", "MAINT_QUALIFIED_MEASURE", schema)
    assert fired("SUMX(Sales, [Amount])", "MAINT_UNQUALIFIED_COLUMN", schema)
    assert not fired("SUMX(Sales, Sales[Amount])", "MAINT_UNQUALIFIED_COLUMN", schema)
    assert not fired("Sales[Total]", "MAINT_QUALIFIED_MEASURE")  # no schema -> silent


def test_format_string_and_calc_column_rules_follow_the_object_kind():
    analyzer = get_shared_analyzer()
    fsd = "SELECTEDMEASURE()"
    assert not [
        i for i in analyzer.analyze(fsd).issues if i.rule_id == "PERF_FORMAT_STRING_DOUBLE_EVAL"
    ]
    ctx = AnalysisContext(object_kind="calc_item_format_string")
    assert [
        i
        for i in analyzer.analyze('IF(SELECTEDMEASURE() > 1000, "0.0,K", "0")', ctx).issues
        if i.rule_id == "PERF_FORMAT_STRING_DOUBLE_EVAL"
    ]
    assert not [
        i
        for i in analyzer.analyze("SELECTEDMEASUREFORMATSTRING()", ctx).issues
        if i.rule_id == "PERF_FORMAT_STRING_DOUBLE_EVAL"
    ]
    cc = AnalysisContext(object_kind="calc_column")
    assert [
        i
        for i in analyzer.analyze("RELATED(Product[Price])", cc).issues
        if i.rule_id == "PERF_RELATED_IN_CALC_COLUMN"
    ]
    assert not [
        i
        for i in analyzer.analyze("RELATED(Product[Price])").issues
        if i.rule_id == "PERF_RELATED_IN_CALC_COLUMN"
    ]


def test_field_parameter_schema_flag_names_the_table_in_the_message():
    schema = ModelSchema.from_model(
        {"tables": [{"name": "FieldParam", "is_field_parameter": True}]}
    )
    hit = fired("SELECTEDVALUE(FieldParam)", "CORR_SELECTEDVALUE_FP", schema)[0]
    assert "field parameter" in hit.description


def test_allexcept_golden_corpus_policy_info_for_a_modifier_medium_inside_filter():
    assert (
        fired("CALCULATE([S], ALLEXCEPT(Sales, Sales[Region]))", "PY_ALLEXCEPT_VS_ALL_VALUES")[
            0
        ].severity
        == "info"
    )
    assert (
        fired(
            "CALCULATE([S], FILTER(ALLEXCEPT(Customer, Customer[Continent]), Customer[x] > 1))",
            "PY_ALLEXCEPT_VS_ALL_VALUES",
        )[0].severity
        == "medium"
    )
