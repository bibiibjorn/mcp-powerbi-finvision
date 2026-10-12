"""2026-10-03 deep review, slice D (BPA): D-15 DAX rules on the shared analyzer (DaxLint), D-18
calibration, D-23 cleanup + supersedes, D-28 modern-model rules, P-18 equality-index residual."""

import json
import os

import pytest

from core.analysis.bpa_analyzer import (
    RULE_SUPERSEDES,
    RULESET_FILES,
    BPAAnalyzer,
    superseded_rule_ids,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def live():
    return BPAAnalyzer(RULESET_FILES["default"])


def _model(measures, **extra):
    m = {
        "name": "M",
        "tables": [
            {
                "name": "Sales",
                "description": "Sales fact.",
                "columns": [
                    {
                        "name": "Amount",
                        "dataType": "decimal",
                        "sourceColumn": "Amount",
                        "description": "d",
                    }
                ],
                "measures": [
                    {"name": n, "expression": e, "formatString": "0", "description": "d"}
                    for n, e in measures
                ],
                "partitions": [
                    {"name": "Sales", "source": {"type": "m", "expression": "let x = 1 in x"}}
                ],
            }
        ],
    }
    m.update(extra)
    return m


def _hits(res, rule_id):
    return sorted(v.object_name for v in res.violations if v.rule_id == rule_id)


# ------------------------------------------------------------------------------------- D-15


def test_d15_lastdate_semi_additive_pattern_is_not_flagged_and_the_rule_is_gone(live):
    ids = {r.id for r in live.rules}
    assert "AVOID_LASTDATE_FIRSTDATE_FILTERS" not in ids
    res = live.run(_model([("Closing", "CALCULATE([Balance], LASTDATE('Date'[Date]))")]))
    assert not [v for v in res.violations if "LASTDATE" in v.rule_id]


@pytest.mark.parametrize(
    "name,expr,flagged",
    [
        ("Bracket name with slash", "[Margin / Unit] * 2", False),
        ("Constant denominator", "[Men's Sales] / 1000", False),
        ("Real division no spaces", "SUM(Sales[a])/SUM(Sales[b])", True),
        ("DIVIDE elsewhere does not mask", "DIVIDE([a],[b]) + [a] / [b]", True),
        (
            "Prefiltered iterator division",
            "CALCULATE(SUMX(Sales, Sales[Amount] / Sales[Qty]), KEEPFILTERS(Sales[Qty] <> 0))",
            False,
        ),
    ],
)
def test_d15_use_divide_function_is_decided_by_the_dax_analyzer(live, name, expr, flagged):
    res = live.run(_model([(name, expr)]))
    assert (
        name
        in [
            v.object_name.split(".")[-1]
            for v in res.violations
            if v.rule_id == "USE_DIVIDE_FUNCTION"
        ]
    ) is flagged


def test_d15_iferror_rule_ignores_names_and_xirr(live):
    res = live.run(
        _model(
            [
                ("Column name", "[ISERROR_Count] + 1"),
                ("XIRR", "IFERROR(XIRR(Flows, Flows[Amount], Flows[Date]), BLANK())"),
                ("Real", "IFERROR([a] / [b], 0)"),
            ]
        )
    )
    flagged = [
        v.object_name.split(".")[-1] for v in res.violations if v.rule_id == "AVOID_IFERROR_ISERROR"
    ]
    assert flagged == ["Real"]


def test_d15_iterator_rules_follow_the_remedy_not_the_regex(live):
    res = live.run(
        _model(
            [
                ("Measure iteration", "SUMX(Customer, [Sales])"),
                ("Remedy", "SUMX(VALUES(Customer[CustomerKey]), CALCULATE([Sales]))"),
                ("Plain", "SUMX(Sales, Sales[Amount])"),
            ]
        )
    )
    unnecessary = [
        v.object_name.split(".")[-1]
        for v in res.violations
        if v.rule_id == "AVOID_UNNECESSARY_ITERATORS"
    ]
    assert unnecessary == ["Plain"]
    expanded = [
        v.object_name.split(".")[-1]
        for v in res.violations
        if v.rule_id == "NO_CALCULATE_OR_CALCULATETABLE"
    ]
    assert expanded == ["Measure iteration"]


def test_d15_excessive_calculate_counts_calls_not_names(live):
    names = ", ".join(f"[Calculated {c}]" for c in "ABCDE")
    res = live.run(
        _model(
            [("Names", f"{names}"), ("Calls", "CALCULATE(CALCULATE(CALCULATE(CALCULATE([x]))))")]
        )
    )
    assert [
        v.object_name.split(".")[-1]
        for v in res.violations
        if v.rule_id == "AVOID_EXCESSIVE_CALCULATE"
    ] == ["Calls"]


def test_d15_approximate_distinct_count_is_reported_once(live):
    res = live.run(_model([("Approx", "APPROXIMATEDISTINCTCOUNT(Sales[Key])")]))
    approx = [v for v in res.violations if "APPROXIMAT" in v.rule_id]
    assert len(approx) == 1 and approx[0].rule_id == "APPROXIMATEDISTINCTCOUNT_IN_MEASURE"


def test_d15_daxlint_functions_in_the_expression_language():
    from core.analysis.bpa_expression import Evaluator, compile_expression
    from core.analysis.bpa_model import ModelView

    view = ModelView(_model([("A", 'SUM(Sales[x]) / 2 + FORMAT([y], "0")')]))
    ev = Evaluator(view.model_node)
    node = view.all_measures[0]

    def check(expr):
        return ev.evaluate(compile_expression(expr), node)

    assert check('DaxLint.HasFunction(Expression, "FORMAT")') is True
    assert check('DaxLint.HasFunction(Expression, "TODAY")') is False
    assert check('DaxLint.CountFunction(Expression, "SUM") == 1') is True
    assert check('DaxLint.Fires(Expression, "low", "CB005")') is True
    assert check('DaxLint.Fires(Expression, "info", "NOPE")') is False


# ------------------------------------------------------------------------------------- D-18 / D-23


def test_d18_severity_rubric_error_is_not_used_for_heuristics():
    data = json.load(open(RULESET_FILES["default"], encoding="utf-8"))["rules"]
    sev = {r["ID"]: r["Severity"] for r in data}
    for rid in (
        "NO_CALCULATE_OR_CALCULATETABLE",
        "AVOID_NESTED_FILTERS",
        "AVOID_CALCULATED_COLUMNS_AGGREGATIONS",
        "MARK_DATE_TABLE",
        "CHECK_DATE_TABLE_REQUIREMENTS",
    ):
        assert sev[rid] <= 2, rid
    # the wide-table builtins: > 30 columns is info, > 50 a warning; ERROR is for measured defects
    live = BPAAnalyzer(RULESET_FILES["default"])
    by = {r.id: int(r.severity) for r in live.rules}
    assert by["PERF_TABLE_TOO_MANY_COLUMNS"] == 1 and by["PERF_WIDE_TABLE_WARNING"] == 2


def test_d18_date_table_rule_is_calendar_aware_and_name_tolerant(live):
    def table(name, calendars=None):
        t = {
            "name": name,
            "columns": [{"name": "Date", "dataType": "dateTime", "sourceColumn": "Date"}],
            "partitions": [{"name": name, "source": {"type": "m", "expression": "x"}}],
        }
        if calendars:
            t["calendars"] = calendars
        return t

    res = live.run({"name": "M", "tables": [table("Dates")]})
    assert _hits(res, "MARK_DATE_TABLE") == ["Dates"]  # 'Dates' / 'DateDim' were missed by \bdate\b
    res = live.run({"name": "M", "tables": [table("Date", calendars=[{"name": "Gregorian"}])]})
    assert _hits(res, "MARK_DATE_TABLE") == []  # a Calendar object needs no marked classic table


def test_d18_star_schema_validator_is_calendar_aware_and_name_tolerant():
    from core.analysis.star_schema_validator import StarSchemaValidator

    v = StarSchemaValidator()
    names = v.validate({"tables": [{"name": "DateDim"}, {"name": "S"}], "relationships": []})
    assert not [x for x in names if x.finding_id == "MISSING_DATE_TABLE"]
    plural = v.validate({"tables": [{"name": "Dates"}, {"name": "S"}], "relationships": []})
    assert not [x for x in plural if x.finding_id == "MISSING_DATE_TABLE"]
    cal = v.validate(
        {"tables": [{"name": "Zeit", "calendars": [{"name": "c"}]}], "relationships": []}
    )
    assert not [x for x in cal if x.finding_id == "MISSING_DATE_TABLE"]
    none = v.validate({"tables": [{"name": "Orders"}], "relationships": []})
    assert [x for x in none if x.finding_id == "MISSING_DATE_TABLE"]


def test_d18_value_filter_behavior_automatic_is_a_heads_up_not_a_defect():
    from core.analysis.model_settings_validator import ModelSettingsValidator

    def sev(behavior):
        out = ModelSettingsValidator().validate(
            {"compatibility_level": 1702, "value_filter_behavior": behavior}
        )
        return [f.severity for f in out if f.finding_id == "VALUE_FILTER_BEHAVIOR_NOT_INDEPENDENT"]

    assert sev("Automatic") == ["low"]
    assert sev("Coalesced") == ["medium"]


def test_d18_m_practices_noise_is_reduced():
    from core.analysis.m_practices import detect_m_issues

    def rules(m):
        return {i["rule"] for i in detect_m_issues("q", m)}

    assert "M004" not in rules("let a = Table.SelectRows(Source, each [x] > 1) in a")
    assert "M012" not in rules("let a = Table.Combine({A, B}) in a")
    assert "M012" in rules("let a = Table.Combine({A, B}), b = Table.Combine({a, C}) in b")
    text = [
        i["description"]
        for i in detect_m_issues("q", 'let a = Table.Sort(S, {{"x", Order.Ascending}}) in a')
        if i["rule"] == "M009"
    ][0]
    assert "folds to ORDER BY" in text


def test_d23_dead_rulesets_and_sample_rule_are_gone():
    assert not os.path.exists(os.path.join(ROOT, "config", "bpa_rules_default.json"))
    assert not os.path.exists(os.path.join(ROOT, "config", "bpa_rules", "sample_custom_rule.json"))
    assert "CUSTOM_001" not in {r.id for r in BPAAnalyzer(RULESET_FILES["default"]).rules}


def test_d23_misnamed_and_duplicate_rules(live):
    ids = {r.id for r in live.rules}
    assert "USE_FORMAT_FUNCTION" not in ids and "AVOID_FORMAT_FUNCTION" in ids
    assert "USE_VARIABLE_STORAGE" not in ids and "USE_VAR_FOR_COMPLEX_EXPRESSIONS" in ids


def test_d23_ruleset_all_supersedes_the_microsoft_twins():
    ms_ids = {r.id for r in BPAAnalyzer(RULESET_FILES["microsoft"]).rules}
    local = {r.id for r in BPAAnalyzer(RULESET_FILES["default"]).rules}
    # every twin the map names really exists in the Microsoft set, and its local rule exists
    for loc, twins in RULE_SUPERSEDES.items():
        assert loc in local, loc
        for t in twins:
            assert t in ms_ids, t
    dropped = superseded_rule_ids(local)
    assert {
        "USE_THE_DIVIDE_FUNCTION_FOR_DIVISION",
        "AVOID_USING_THE_IFERROR_FUNCTION",
        "DATE/CALENDAR_TABLES_SHOULD_BE_MARKED_AS_A_DATE_TABLE",
    } <= dropped
    assert superseded_rule_ids([]) == set()


# ------------------------------------------------------------------------------------- D-28


def _modern(model):
    res = BPAAnalyzer(RULESET_FILES["default"]).run(model)
    out = {}
    for v in res.violations:
        out.setdefault(v.rule_id, []).append((v.object_type, v.object_name, v.details))
    return out, res


def test_d28_udf_hygiene():
    model = {
        "name": "M",
        "tables": [{"name": "Secret Table", "columns": [], "partitions": []}],
        "roles": [
            {"name": "R", "tablePermissions": [{"name": "Secret Table", "filterExpression": "1=1"}]}
        ],
        "functions": [
            {"name": "Reuse", "description": "d", "expression": "(x : ANYVAL EXPR) => x + x"},
            {
                "name": "Good",
                "description": "d",
                "expression": "(x : ANYVAL EXPR) => VAR _v = x RETURN _v + _v",
            },
            {"name": "NoDoc", "expression": "(x : INT64 VAL) => x"},
            {"name": "Secret Table Sum", "description": "d", "expression": "(x : INT64 VAL) => x"},
        ],
    }
    out, _ = _modern(model)
    assert [h[1] for h in out["UDF_MISSING_DESCRIPTION"]] == ["NoDoc"]
    assert "Reuse" in [h[1] for h in out["UDF_EXPR_PARAMETER_REUSED"]]
    assert [h[1] for h in out["UDF_NAME_LEAKS_SECURED_OBJECT"]] == ["Secret Table Sum"]


def test_d28_calendar_and_week_time_intelligence():
    t = {
        "name": "Date",
        "columns": [],
        "partitions": [],
        "calendars": [
            {"name": "Gregorian", "calendarColumnGroups": []},
            {
                "name": "Fiscal",
                "calendarColumnGroups": [{"timeUnit": "year", "primaryColumn": "FY"}],
            },
        ],
    }
    m = {
        "name": "Sales",
        "columns": [],
        "partitions": [],
        "measures": [{"name": "WTD", "expression": "TOTALWTD([Sales], 'Gregorian')"}],
    }
    out, _ = _modern({"name": "M", "tables": [t, m]})
    assert [h[1] for h in out["CALENDAR_NO_TIME_UNIT_MAPPING"]] == ["Gregorian"]
    assert "WEEK_TI_WITHOUT_CALENDAR" not in out  # a calendar exists
    out2, _ = _modern(
        {"name": "M", "tables": [{"name": "Date", "columns": [], "partitions": []}, m]}
    )
    assert [h[1] for h in out2["WEEK_TI_WITHOUT_CALENDAR"]] == ["WTD"]


def test_d28_calculation_groups():
    def cg(name, prec, items, **kw):
        return {
            "name": name,
            "columns": [],
            "partitions": [],
            "calculationGroup": {"precedence": prec, "calculationItems": items, **kw},
        }

    items = [
        {"name": "A", "expression": "SELECTEDMEASURE()"},
        {
            "name": "B",
            "expression": "SELECTEDMEASURE()",
            "formatStringDefinition": {"expression": '"0"'},
        },
    ]
    model = {
        "name": "M",
        "tables": [
            cg("TI", 10, items),
            cg(
                "Currency",
                10,
                items,
                noSelectionExpression={"expression": "SELECTEDMEASURE()"},
                multipleOrEmptySelectionExpression={"expression": "SELECTEDMEASURE()"},
            ),
            cg("Solo", 20, items[:1]),
        ],
    }
    out, _ = _modern(model)
    assert sorted(h[1] for h in out["CALCGROUP_DUPLICATE_PRECEDENCE"]) == ["Currency", "TI"]
    assert [h[1] for h in out["CALCGROUP_NO_SELECTION_EXPRESSIONS"]] == ["TI"]  # Solo has < 2 items
    assert {h[1] for h in out["CALCITEM_NO_FORMAT_STRING"]} == {"A"}


def test_d28_field_parameter_integrity():
    sales = {
        "name": "Sales",
        "columns": [{"name": "Amount"}],
        "measures": [{"name": "Visible"}, {"name": "Hidden", "isHidden": True}],
        "partitions": [],
    }
    fp = {
        "name": "FP",
        "columns": [],
        "partitions": [
            {
                "name": "FP",
                "source": {
                    "type": "calculated",
                    "expression": "{ (\"a\", NAMEOF('Sales'[Visible]), 0), (\"b\", NAMEOF('Sales'[Hidden]), 1), "
                    '("c", NAMEOF(\'Sales\'[Gone]), 2), ("d", NAMEOF([Amount]), 3) }',
                },
            }
        ],
    }
    out, _ = _modern({"name": "M", "tables": [sales, fp]})
    assert "Gone" in out["FIELD_PARAMETER_MISSING_FIELD"][0][2]
    assert len(out["FIELD_PARAMETER_MISSING_FIELD"]) == 1
    assert "Hidden" in out["FIELD_PARAMETER_HIDDEN_FIELD"][0][2]


def test_d28_ai_readiness_only_for_visible_objects():
    model = {
        "name": "M",
        "tables": [
            {
                "name": "Visible",
                "columns": [
                    {"name": "A"},
                    {"name": "B", "description": "ok"},
                    {"name": "H", "isHidden": True},
                ],
                "partitions": [],
            },
            {"name": "Hidden", "isHidden": True, "columns": [{"name": "X"}], "partitions": []},
        ],
    }
    out, _ = _modern(model)
    assert [h[1] for h in out["AI_TABLE_NO_DESCRIPTION"]] == ["Visible"]
    assert [h[1] for h in out["AI_COLUMN_NO_DESCRIPTION"]] == ["A"]
    assert "AI_NO_SYNONYMS" in out
    with_syn = {
        **model,
        "cultures": [{"name": "en-US", "linguisticMetadata": {"Entities": {"a": {"Terms": []}}}}],
    }
    # "Terms" key present but empty is still metadata the author maintains
    assert "AI_NO_SYNONYMS" not in _modern(with_syn)[0]


def test_d28_modern_rules_are_part_of_the_coverage_and_never_crash_a_run():
    res = BPAAnalyzer(RULESET_FILES["default"]).run({"name": "M", "tables": []})
    assert res.coverage["rules_unsupported"] == 0
    from core.analysis.bpa_modern_rules import MODERN_RULES

    assert res.coverage["rules_evaluated"] >= len(MODERN_RULES)
    assert len(MODERN_RULES) == 13


# ------------------------------------------------------------------------------------- P-18


def test_p18_conjunctive_equality_uses_the_index_and_the_residual_only():
    from core.analysis.bpa_expression import Evaluator, compile_expression
    from core.analysis.bpa_model import ModelView

    tables = [
        {"name": f"T{t}", "columns": [{"name": f"Col {c}"} for c in range(20)], "partitions": []}
        for t in range(30)
    ]
    view = ModelView({"name": "M", "tables": tables})
    ev = Evaluator(view.model_node)
    expr = compile_expression(
        "Model.AllColumns.Where(Name == current.Name and Table.Name != current.Table.Name).Count() == 29"
    )
    node = view.all_columns[0]
    assert ev.evaluate(expr, node) is True
    assert ev.index_hits + ev.index_misses > 0  # the equality index was used
    # the same predicate without the fast path agrees
    ev2 = Evaluator(view.model_node)
    ev2._equality_split = lambda ast: None
    assert ev2.evaluate(expr, node) is True


def test_p18_equality_split_returns_the_residual_conjuncts():
    from core.analysis.bpa_expression import Evaluator, compile_expression

    ast = compile_expression(
        "Name == current.Name and Table.Name != current.Table.Name and IsHidden"
    ).ast
    # drill to the predicate: the whole expression is itself the predicate shape for _equality_split
    key, other, residual = Evaluator._equality_split(ast)
    assert residual is not None and residual[0] == "and"
    only_eq = compile_expression("Name == current.Name").ast
    assert Evaluator._equality_split(only_eq)[2] is None
