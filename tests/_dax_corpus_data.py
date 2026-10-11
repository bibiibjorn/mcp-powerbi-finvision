# -*- coding: utf-8 -*-
"""Corpus for the 2026-10-03 deep review (slice D): 108 canon-good / canon-bad DAX snippets.

Each entry: dict(id, ref, kind, dax, fire, missing, ok, schema, note, why)
  kind    : 'good' (canon-clean: no finding above info may fire except those in `ok`)
            'bad'  (canon anti-pattern: every rule id in `fire` must fire)
  fire    : live rule ids that MUST fire (true positives)
  missing : canon rule/AP that static analysis cannot detect (documented limit; `why` says why)
  ok      : rule ids tolerated on this snippet (info-level noise / defensible)
  schema  : name of a synthetic ModelSchema in SCHEMAS ('std', 'm2m', ...) passed as context

Source: SP/probes/D/corpus.py of the review; permanent regression fixture for
tests/test_review2_dax_corpus.py.
"""

from core.dax.analyzer.models import ModelSchema

_MEASURES = {
    m.lower()
    for m in [
        "Sales",
        "Cost",
        "Revenue",
        "Units",
        "Total Sales",
        "Sales LY",
        "Sales PY",
        "Margin",
        "Balance",
        "Label",
        "MaxDate",
        "Sales A",
        "Sales B",
        "Product Sales",
        "Measure",
        "Men's Sales",
        "Sales Amount",
    ]
}
_COLUMNS = {c.lower() for c in ["amount", "qty", "price", "country", "key", "name", "year", "date"]}
_TABLES = {"sales", "product", "date", "customer", "geography", "budget"}
_TABLE_COLUMNS = {
    "sales": {"amount", "qty", "price", "cost", "key"},
    "date": {"date", "year"},
    "budget": {"year", "amount"},
    "geography": {"country"},
}

SCHEMAS = {
    "std": ModelSchema(
        measure_names=_MEASURES,
        column_names=_COLUMNS,
        table_names=_TABLES,
        relationships=[
            {
                "IsActive": True,
                "FromCardinality": 2,
                "ToCardinality": 1,
                "CrossFilteringBehavior": 1,
            }
        ],
        roles=[],
        table_columns=_TABLE_COLUMNS,
    ),
    "m2m": ModelSchema(
        measure_names=_MEASURES,
        column_names=_COLUMNS,
        table_names=_TABLES,
        relationships=[
            {
                "IsActive": True,
                "FromCardinality": 2,
                "ToCardinality": 2,
                "CrossFilteringBehavior": 2,
            }
        ],
        roles=[],
    ),
    # model that has a UDF whose first parameter is an `expr` (AP-48)
    "udf": ModelSchema(
        measure_names=_MEASURES,
        column_names=_COLUMNS,
        table_names=_TABLES | {"formulas"},
        relationships=[],
        roles=[],
        udf_signatures={"AMOUNTFOR": ["expr"]},
    ),
    # DirectQuery-only model (D-05)
    "dq": ModelSchema(
        measure_names=_MEASURES,
        column_names=_COLUMNS,
        table_names=_TABLES,
        relationships=[],
        roles=[],
        table_storage_modes={t: "directquery" for t in _TABLES},
    ),
}

C = []


def add(id, ref, kind, dax, fire=(), missing=(), ok=(), schema="std", note="", why=""):
    C.append(
        dict(
            id=id,
            ref=ref,
            kind=kind,
            dax=dax,
            fire=list(fire),
            missing=list(missing),
            ok=list(ok),
            schema=schema,
            note=note,
            why=why,
        )
    )


# ----------------------------------------------------------------- GOOD (canon-clean)
add("G01", "core rule 1 GOOD", "good", 'CALCULATE([Sales], Geography[Country] = "US")')
add("G02", "rule 5 GOOD", "good", "DIVIDE([Revenue] - [Cost], [Revenue])")
add(
    "G03",
    "rule 3 VAR",
    "good",
    "VAR _Sales = SUM(Sales[Amount])\nVAR _Cost = SUM(Sales[Cost])\nRETURN DIVIDE(_Sales - _Cost, _Sales)",
)
add("G04", "rule 2 GOOD", "good", "SUMX(VALUES(Customer[CustomerKey]), [Sales])")
add("G05", "rule 6 GOOD", "good", "SUMX(Sales, Sales[Qty] * Sales[Price])")
add(
    "G06",
    "rule 9 GOOD",
    "good",
    'DIVIDE(CALCULATE([Sales], KEEPFILTERS(\'Product\'[Color] IN {"Blue","Red","White"})), [Sales])',
)
add(
    "G07",
    "rule 10 GOOD (ALL + VALUES)",
    "good",
    "DIVIDE([Sales], CALCULATE([Sales], ALL(Customer), VALUES(Customer[Continent])))",
)
add(
    "G08",
    "AP-17 SAFE (REMOVEFILTERS)",
    "good",
    "DIVIDE([Sales], CALCULATE([Sales], REMOVEFILTERS(Customer), VALUES(Customer[Continent])))",
)
add(
    "G09",
    "rule 14 GOOD running total",
    "good",
    "CALCULATE([Sales], FILTER(ALL('Date'), 'Date'[Date] <= MAX('Date'[Date])))",
)
add("G10", "rule 12 GOOD", "good", "CALCULATE([Total Sales], DATESYTD('Date'[Date]))")
add(
    "G11",
    "AP-08 GOOD B (pre-filtered /)",
    "good",
    "CALCULATE(SUMX(Sales, Sales[Amount] / Sales[Qty]), KEEPFILTERS(Sales[Qty] <> 0))",
)
add("G12", "AP-02 GOOD A (INT boolean)", "good", "SUMX(Sales, INT(Sales[Qty] > 0) * Sales[Amount])")
add("G13", "DAX007 preferred", "good", "CALCULATE(COUNTROWS(Sales), Sales[Amount] > 1000)")
add(
    "G14",
    "AP-32 RIGHT",
    "good",
    'VAR _sel = SELECTEDVALUE(FieldParam[Parameter Fields], "Sales")\n'
    'RETURN SWITCH(_sel, "Sales", [Sales], "Margin", [Margin], BLANK())',
)
add(
    "G15",
    "AP-20 GOOD (TREATAS)",
    "good",
    "CALCULATE([Sales], TREATAS(VALUES(Budget[Year]), 'Date'[Year]))",
)
add("G16", "semi-additive LASTDATE", "good", "CALCULATE([Balance], LASTDATE('Date'[Date]))")
add(
    "G17",
    "rule 7 query form",
    "good",
    "SUMMARIZECOLUMNS('Product'[Category], \"Total\", [Total Sales])",
)
add(
    "G18",
    "rule 7 measure form (ADDCOLUMNS SUMMARIZE)",
    "good",
    "ADDCOLUMNS(SUMMARIZE(Sales, 'Product'[Category]), \"Total\", CALCULATE(SUM(Sales[Amount])))",
    ok=["PERF_ADDCOLUMNS_SUMMARIZE"],
    note="canon rule 7 recommends this inside measures; AP-19 flags it",
)
add(
    "G19",
    "ERROR() pattern",
    "good",
    "IF(HASONEVALUE('Region'[Name]), CALCULATE([Sales], 'Region'[Name] = SELECTEDVALUE('Region'[Name])), ERROR(\"Select exactly one region.\"))",
)
add("G20", "calc-group item", "good", "CALCULATE(SELECTEDMEASURE(), DATESYTD('Date'[Date]))")
add("G21", "calc-group fmt", "good", "SELECTEDMEASUREFORMATSTRING()")
add("G22", "dynamic fmt", "good", "SELECTEDVALUE('Currency'[Format], \"#,0.00\")")
add("G23", "UDF typed", "good", "FUNCTION AddTax = (amount : NUMERIC) =>\n    amount * 1.1")
add(
    "G24",
    "UDF expr/ColumnRef (MS doc)",
    "good",
    "FUNCTION PriorYearValue = (expression : Scalar Numeric Expr, dateColumn : ColumnRef) =>\n"
    "    CALCULATE(expression, SAMEPERIODLASTYEAR(dateColumn))",
)
add(
    "G25",
    "DEFINE FUNCTION query",
    "good",
    "DEFINE\n    /// Description\n    FUNCTION f = (x : ANYVAL) => x + 1\nEVALUATE { f(1) }",
)
add("G26", "visual calc RUNNINGSUM", "good", "RUNNINGSUM([Sales Amount], ROWS, , HIGHESTPARENT)")
add(
    "G27",
    "visual calc COLLAPSEALL",
    "good",
    "DIVIDE([Sales Amount], COLLAPSEALL([Sales Amount], ROWS))",
)
add("G28", "visual calc MOVINGAVERAGE", "good", "MOVINGAVERAGE([Sales Amount], 3)")
add(
    "G29",
    "TEXTCONTAINS",
    "good",
    "CALCULATE([Sales], TEXTCONTAINS('Product'[Name], \"bike\", PHRASEMATCHING))",
)
add("G30", "APPROXIMATEDISTINCTCOUNT", "good", "APPROXIMATEDISTINCTCOUNT(Sales[CustomerKey])")
add(
    "G31",
    "comment look-alike",
    "good",
    "SUM(Sales[Amount]) // IFERROR(x, 0) SUMX(FILTER(Sales, 1), 1) / 0\n-- DIVIDE( [a] / [b] ) FORMAT(1)\n/* VAR x = 1 */",
)
add(
    "G32",
    "string look-alike",
    "good",
    '"IFERROR(SUMX(FILTER(Sales, 1), 1) / 0) // FORMAT(1)" & [Label]',
)
add(
    "G33",
    "quoted table apostrophe",
    "good",
    "SUMX('O''Brien Sales', 'O''Brien Sales'[Qty] * 'O''Brien Sales'[Price])",
)
add("G34", "apostrophe in measure name", "good", "[Men's Sales] / 1000")
add(
    "G35",
    "FILTER over VAR table",
    "good",
    "VAR _t = SUMMARIZE(Sales, Sales[Key])\nRETURN COUNTROWS(FILTER(_t, [Sales] > 100))",
)
add(
    "G36",
    "SWITCH TRUE w/ default",
    "good",
    'SWITCH(TRUE(),\n  [Sales] > 100, "A",\n  [Sales] > 50, "B",\n  "C")',
    ok=["MAINT_REPEATED_MEASURE"],
)
add(
    "G37",
    "CROSSFILTER per measure",
    "good",
    "CALCULATE([Sales], CROSSFILTER(Sales[CustKey], Customer[Id], BOTH))",
)
add(
    "G38",
    "USERELATIONSHIP",
    "good",
    "CALCULATE([Sales], USERELATIONSHIP(Sales[ShipDate], 'Date'[Date]))",
)
add(
    "G39",
    "WINDOW INDEX",
    "good",
    "INDEX(-1, ALL('Status'), ORDERBY('Status'[Date], ASC), PARTITIONBY('Status'[EntityKey]))",
)
add(
    "G40",
    "CALCULATETABLE preferred (DAX001)",
    "good",
    "CALCULATETABLE('Sales', 'Sales'[Region] = \"West\", 'Sales'[Amount] > 1000)",
)
add(
    "G41",
    "KEEPFILTERS predicate (DAX001)",
    "good",
    "CALCULATE(SUM('Sales'[Amount]), KEEPFILTERS('Product'[Category] = \"Electronics\"))",
)
add("G42", "sparsity-preserving IF", "good", "IF(ISBLANK([Sales]), BLANK(), [Sales] * 1.21)")
add(
    "G43",
    "nested CALCULATE x2 (fine)",
    "good",
    "CALCULATE(CALCULATE([Sales], 'Date'[Year] = 2024), Product[Color] = \"Red\")",
)
add(
    "G44",
    "VAR in branch (rule 3 GOOD)",
    "good",
    "IF([Sales] > 0, VAR _c = [Cost] RETURN [Sales] - _c)",
)
add(
    "G45",
    "VAR used by IF condition + branch",
    "good",
    "VAR _Sales = CALCULATE([Sales], 'Date'[Year] = 2024)\n"
    "VAR _Cost = CALCULATE([Cost], 'Date'[Year] = 2024)\n"
    "RETURN IF(_Sales = 0, BLANK(), DIVIDE(_Sales - _Cost, _Sales))",
    note="both VARs are needed by the condition/branch - hoisting is correct",
)
add(
    "G46",
    "DAX Patterns running total KEEPFILTERS",
    "good",
    "VAR _max = MAX('Date'[Date])\nRETURN CALCULATE([Sales], KEEPFILTERS(FILTER(ALL('Date'[Date]), 'Date'[Date] <= _max)))",
)
add(
    "G47",
    "UDF VAL table param as filter",
    "good",
    "FUNCTION SalesFor = (dates : TABLE VAL) =>\n    CALCULATE([Sales], dates)",
    note="eager table value is a valid CALCULATE filter",
)
add(
    "G48",
    "UDF untyped parameters",
    "good",
    "FUNCTION Twice = (x) => x * 2",
    ok=["PY_UDF_UNTYPED_PARAMETER"],
    note="AnyVal is the documented default; typing is a style recommendation",
)
add(
    "G49",
    "% of parent, REMOVEFILTERS+VALUES",
    "good",
    "DIVIDE([Sales], CALCULATE([Sales], REMOVEFILTERS('Product'), VALUES('Product'[Category])))",
)
add(
    "G50",
    "constant literal predicate in measure",
    "good",
    "CALCULATE([Sales], 'Currency'[Code] = \"EUR\")",
    note="a fixed-currency measure is a legitimate design",
)

# ----------------------------------------------------------------- BAD (canon anti-patterns)
add(
    "B01",
    "AP-01",
    "bad",
    'CALCULATE([Sales], FILTER(Geography, Geography[Country] = "US"))',
    fire=["CB004"],
)
add("B02", "AP-02", "bad", "SUMX(Sales, IF(Sales[Qty] > 0, Sales[Amount], 0))", fire=["CB001"])
add(
    "B03",
    "AP-03",
    "bad",
    "SUMX(Customer, SUMX(RELATEDTABLE(Sales), Sales[Amount]))",
    fire=["CB003"],
)
add("B04", "AP-04", "bad", "SUMX(Sales, ROUND(Sales[Amount] * Sales[Rate], 2))", fire=["CB008"])
add("B05", "AP-05", "bad", "SUMX(Customer, [Sales])", fire=["PERF_EXPANDED_TABLE_ITERATOR"])
add(
    "B06",
    "AP-06",
    "bad",
    "IFERROR([Revenue] / [Units], 0)",
    fire=["CORR_IFERROR_USAGE", "CORR_DIVISION_WITHOUT_DIVIDE"],
)
add("B07", "AP-07", "bad", 'FORMAT([Sales], "#,##0.00")', fire=["CB005"])
add("B08", "AP-08", "bad", "SUMX(Sales, DIVIDE(Sales[Amount], Sales[Qty]))", fire=["CB007"])
add(
    "B09",
    "AP-09",
    "bad",
    "SUMX(Sales, Sales[Qty] * RELATED(Product[Price]))",
    fire=["PERF_RELATED_IN_SUMX"],
)
add(
    "B10",
    "AP-10",
    "bad",
    'VAR _A = CALCULATE([Sales], FilterA)\nVAR _B = CALCULATE([Sales LY], FilterB)\nRETURN SWITCH(Selection, "A", _A, "B", _B)',
    fire=["PY_VAR_DEFEATING_SHORTCIRCUIT", "CORR_SWITCH_NO_DEFAULT"],
)
add(
    "B11", "AP-11", "bad", "FILTER(ALL(Product), [Product Sales] > 10000)", fire=["PERF_FILTER_ALL"]
)
add(
    "B12",
    "AP-17",
    "bad",
    "CALCULATE([Sales], ALLEXCEPT(Customer, Customer[Continent]))",
    fire=["PY_ALLEXCEPT_VS_ALL_VALUES"],
)
add("B13", "AP-26", "bad", "1 - DIVIDE([Cost], [Revenue])", fire=["CORR_BLANK_PROPAGATION"])
add("B14", "AP-27", "bad", "[Revenue] / [Units]", fire=["CORR_DIVISION_WITHOUT_DIVIDE"])
add(
    "B15",
    "AP-28",
    "bad",
    'SWITCH([Type], "A", [Sales A], "B", [Sales B])',
    fire=["CORR_SWITCH_NO_DEFAULT"],
)
add(
    "B16",
    "AP-29",
    "bad",
    'SUMMARIZE(Sales, Sales[Category], "Total", SUM(Sales[Amount]))',
    fire=["CORR_SUMMARIZE_INLINE"],
)
add(
    "B17",
    "AP-30",
    "bad",
    "CALCULATE([Sales], FILTER(ALL('Date'), 'Date'[Date] <= [MaxDate]))",
    fire=["PERF_FILTER_ALL", "CORR_MEASURE_IN_FILTER"],
)
add(
    "B18",
    "AP-31",
    "bad",
    "SUMX(Customers, CALCULATE([Sales], 'Date'[Year] = SELECTEDVALUE(Customers[BirthYear])))",
    fire=["CORR_FILTER_ARG_NO_TRANSITION"],
)
add(
    "B19",
    "AP-32",
    "bad",
    "VAR _sel = SELECTEDVALUE(FieldParam)\nRETURN _sel",
    fire=["CORR_SELECTEDVALUE_FP"],
)
add(
    "B20",
    "AP-33",
    "bad",
    'CALCULATE([Sales], FILTER(REMOVEFILTERS(Products[Category]), Products[Category] = "Bikes"))',
    fire=["CORR_REMOVEFILTERS_AS_TABLE"],
)
add(
    "B21",
    "AP-34",
    "bad",
    "CALCULATE([X], TableA[Col] <= TableB[Col])",
    fire=["CORR_CROSS_TABLE_PREDICATE"],
)
add(
    "B22",
    "AP-35",
    "bad",
    'ADDCOLUMNS(vTable, "@Result", CALCULATE(SUM(Sales[Amount]), OtherTable[Col] = [@ExtendedCol]))',
    fire=["CORR_EXTENDED_COLUMN_IN_FILTER"],
)
add(
    "B23",
    "AP-36",
    "bad",
    "SUMX(FILTER(Sales, Sales[Date] <= EARLIER(Sales[Date])), Sales[Amount])",
    fire=["PERF_EARLIER_DEPRECATED"],
    ok=["CB004"],
)
add(
    "B24",
    "AP-37",
    "bad",
    "DIVIDE([Total Sales] - CALCULATE([Total Sales], ALL(Region)), CALCULATE([Total Sales], ALL(Region)))",
    ok=["PY_ALL_TABLE_VS_COLUMN"],
    fire=["MAINT_REPEATED_CALCULATE"],
)
add(
    "B25",
    "AP-38",
    "bad",
    "VAR a = SUM(Sales[Amount])\nVAR b = SUM(Budget[Amount])\nRETURN DIVIDE(a - b, b)",
    fire=["MAINT_NONDESCRIPTIVE_VAR"],
)
add(
    "B26",
    "AP-39",
    "bad",
    "VAR _x = SUM(Sales[Amount])\nVAR _y = 1\nRETURN _x",
    fire=["MAINT_UNUSED_VAR"],
)
add(
    "B27",
    "AP-21",
    "bad",
    "LOOKUPVALUE(Product[Price], Product[ID], Sales[ProductID])",
    fire=["PERF_LOOKUPVALUE_USAGE"],
)
add(
    "B28",
    "AP-25",
    "bad",
    'DIVIDE(CALCULATE([Sales], \'Product\'[Color] IN {"Blue","Red","White"}), [Sales])',
    fire=["PY_NEED_KEEPFILTERS"],
)
add(
    "B29",
    "AP-24",
    "bad",
    "AVERAGEX(VALUES('Date'[Year-Month]), [Sales])",
    fire=["PY_ITERATOR_NEEDS_KEEPFILTERS"],
)
add(
    "B30",
    "AP-20",
    "bad",
    "CALCULATE([Sales], INTERSECT(VALUES(Budget[Year]), VALUES('Date'[Year])))",
    fire=["CORR_INTERSECT_VS_TREATAS"],
)
add("B31", "AP-49", "bad", "SUM(Sales[Amount]) + 0", fire=["PY_NEVER_BLANK"])
add(
    "B32",
    "AP-49/QRY004",
    "bad",
    "IF(ISBLANK([Sales]), 0, [Sales])",
    fire=["PERF_DENSE_BLANK_CONVERSION"],
)
add("B33", "AP-01", "bad", "COUNTROWS(FILTER(Sales, Sales[Qty] > 10))", fire=["CB004"])
add("B34", "AP-12/engine", "bad", 'SUMX(Sales, SEARCH("x", Sales[Name], 1, 0))', fire=["CB009"])
add(
    "B35",
    "AP-22",
    "bad",
    "SUMMARIZECOLUMNS('Product'[ProductKey], \"Total\", [Sales])",
    fire=["PERF_PK_SUMMARIZECOLUMNS"],
)
add(
    "B36",
    "AP-23",
    "bad",
    "SUMMARIZECOLUMNS('Table'[Column], TREATAS({\"Value\"}, 'Table'[FilterColumn]), \"@Calculation\", [Measure])",
    fire=["PERF_SUMMARIZECOLUMNS_INTERNAL_FILTER"],
)
add("B37", "MS guideline", "bad", "EVALUATEANDLOG([Sales])", fire=["MAINT_EVALUATEANDLOG"])
add(
    "B38",
    "AP-18",
    "bad",
    "DIVIDE([Sales], CALCULATE([Sales], ALL(Sales)))",
    fire=["PY_ALL_VS_ALLCROSSFILTERED"],
    schema="m2m",
)
add("B39", "AP-16", "bad", "CALCULATE([Sales], ALL(Product))", fire=["PY_ALL_TABLE_VS_COLUMN"])
add(
    "B40",
    "deep nesting",
    "bad",
    "IF(a,IF(b,IF(c,IF(d,IF(e,IF(f,IF(g,1)))))))",
    fire=["MAINT_DEEP_NESTING"],
)
add(
    "B41",
    "IFERROR+XIRR unavoidable",
    "good",
    "IFERROR(XIRR(Flows, Flows[Amount], Flows[Date]), BLANK())",
    ok=["CORR_IFERROR_USAGE"],
    note="downgraded to info by design",
)
add(
    "B42",
    "semi-additive LASTNONBLANK",
    "bad",
    "CALCULATE([Balance], LASTNONBLANK('Date'[Date], [Balance]))",
    fire=["PY_SEMI_ADDITIVE_RANKING"],
)
add(
    "B43",
    "AP-48",
    "bad",
    "FUNCTION AmountFor = (x : ANYVAL EXPR) => CALCULATE(SUM(Sales[Amount]), Sales[Element] = x)\n",
    missing=["AP-48 UDF expr param"],
    why="the definition alone is not wrong: the failure happens at the call site (B44), which needs the UDF signature",
)
add(
    "B44",
    "AP-48 call site",
    "bad",
    "SUMX(_Set, 'Formulas'[Multiplier] * AmountFor({ 'Formulas'[Element] }))",
    fire=["CORR_UDF_EXPR_BARE_COLUMN"],
    schema="udf",
)
add(
    "B45",
    "engine: CONTAINSSTRING in iterator",
    "bad",
    'SUMX(Sales, IF(CONTAINSSTRING(Sales[Name], "x"), 1, 0))',
    fire=["CB001", "CB009"],
)
add(
    "B46",
    "SUMX over VALUES with CALCULATE",
    "good",
    "SUMX(VALUES('Date'[Year]), CALCULATE([Sales], DATESYTD('Date'[Date])))",
    ok=["CB011"],
    note="canon-sanctioned grain-reduction rule 2; CB011 is info-grade at best",
)
add(
    "B47",
    "rule 4 DQ",
    "good",
    "IFERROR(Sales[Amount] / Sales[Qty], 0)",
    schema="dq",
    note="DirectQuery inverts the advice: IFERROR folds to SQL, DIVIDE does not (storage-mode aware, D-05)",
)
add(
    "B48",
    "COUNTROWS(VALUES())",
    "bad",
    "COUNTROWS(VALUES(Customer[Id]))",
    fire=["PERF_COUNTROWS_VALUES_VS_DISTINCTCOUNT"],
    note="MS DAX011: DISTINCTCOUNT vs distinct-iteration is test-dependent",
)
add(
    "B49",
    "HASONEVALUE pattern",
    "good",
    "COUNTROWS(VALUES(Customer[Id])) = 1",
    ok=["PERF_COUNTROWS_VALUES_VS_DISTINCTCOUNT"],
)
add(
    "B50",
    "DAX Patterns: CALCULATETABLE(FILTER(measure))",
    "good",
    "CALCULATETABLE(FILTER(ALL('Product'[Name]), [Sales] > 100), 'Date'[Year] = 2024)",
    note="FILTER with a measure predicate cannot be expressed as a boolean filter",
)

# ----------------------------------------------------------------- canon REMEDIES (must not trigger the rule they cure)
add(
    "R01",
    "AP-04 GOOD (iterate distinct rate)",
    "good",
    "SUMX(SUMMARIZE(Sales, Sales[Rate]), ROUND(CALCULATE(SUM(Sales[Amount])) * Sales[Rate], 2))",
)
add(
    "R02",
    "engine-internals Fast3",
    "good",
    "SUMX(SUMMARIZE(Sales, Sales[NetPrice]), CALCULATE(SUM(Sales[Quantity])) * ROUND(Sales[NetPrice], 1))",
)
add(
    "R03",
    "MS DAX015 preferred",
    "good",
    "SUMX(VALUES('Customer'[DiscountRate]), CALCULATE(SUM('Sales'[Amount])) * 'Customer'[DiscountRate])",
)
add(
    "R04",
    "IF over a small distinct set",
    "good",
    "SUMX(VALUES('Date'[Year]), IF([Sales] > 0, [Sales], 0))",
)
add(
    "R05",
    "MS DAX021 preferred (NATURALINNERJOIN)",
    "good",
    "VAR _Joined = NATURALINNERJOIN(_Qualifying, _UnfilteredAgg)\nRETURN SUMX(_Joined, [@Agg2])",
    ok=["MAINT_NONDESCRIPTIVE_VAR"],
)
add(
    "R06",
    "MS DAX018 preferred",
    "good",
    "SUMX('Fact', 'Fact'[BaseAmount] * (RELATED('Items'[Discount]) / RELATED('Items'[LocationAdjustment])))",
    ok=["PERF_RELATED_IN_SUMX"],
)
add(
    "R07",
    "MS DAX017 boolean multiplier",
    "good",
    "SUMX(KEEPFILTERS(ALL('Product'[Category])), CALCULATE(SUM('Sales'[Amount])) * ('Product'[Category] = \"Bikes\"))",
)
add(
    "R08",
    "MS QRY004 conditional zero",
    "good",
    "VAR _ForceZero = NOT ISEMPTY('Sales')\nRETURN [Sales Amount] + IF(_ForceZero, 0)",
)
