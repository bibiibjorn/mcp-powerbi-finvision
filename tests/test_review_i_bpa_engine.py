"""2026-09-22 deep review, slice I — BPA engine (I-01 … I-05, I-12, I-13, I-20, I-28).

Runs the REAL shipped rules files through the real analyzer over TMSL-shaped
(lowerCamel, defaults omitted) synthetic models.
"""

from __future__ import annotations

import copy
import glob
import json
import os
import threading
import time

import pytest

from core.analysis.bpa_analyzer import (
    RULESET_FILES,
    BPAAnalyzer,
    BPARule,
    BPASeverity,
    load_rules_file,
)
from core.analysis.bpa_expression import (
    BpaParseError,
    BpaUnsupported,
    Evaluator,
    compile_expression,
    compile_regex,
    regex_literals,
    translate_regex,
)
from core.analysis.bpa_model import ModelView
from tests.fixtures.bpa_synthetic import build_clean_model, table, with_changes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPREHENSIVE = os.path.join(ROOT, "config", "bpa_rules_comprehensive.json")
MICROSOFT = os.path.join(ROOT, "core", "bpa.json")
# bpa_rules_default.json (20 rules, a dead fallback) and config/bpa_rules/sample_custom_rule.json
# (auto-loaded on EVERY run) were deleted in the 2026-10-03 D-23 cleanup.
SHIPPED = [COMPREHENSIVE, MICROSOFT] + sorted(
    glob.glob(os.path.join(ROOT, "config", "bpa_rules", "*.json"))
)


@pytest.fixture(scope="module")
def live():
    """The analyzer exactly as connection_state builds it."""
    return BPAAnalyzer(COMPREHENSIVE)


@pytest.fixture(scope="module")
def clean_model():
    return build_clean_model(1500)


def _ids(result, rule_id):
    return sorted(
        (v.table_name + "." if v.table_name else "") + v.object_name
        for v in result.violations
        if v.rule_id == rule_id
    )


def _rule(expr, scope="Measure", rid="T"):
    return BPARule(
        id=rid,
        name=rid,
        category="Test",
        description="",
        severity=BPASeverity.WARNING,
        scope=[scope] if isinstance(scope, str) else scope,
        expression=expr,
    )


def _only(rule):
    a = BPAAnalyzer()
    a.rules = [rule]
    return a


# ── I-01: rules files ────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", SHIPPED, ids=os.path.basename)
def test_every_regex_literal_in_every_shipped_rules_file_compiles(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    rules = data if isinstance(data, list) else data["rules"]
    checked = 0
    for r in rules:
        expr = r["Expression"] if isinstance(r["Expression"], str) else "\n".join(r["Expression"])
        try:
            ast = compile_expression(expr).ast
        except BpaParseError:
            continue  # parse errors are covered by the next test
        for pattern, flags in regex_literals(ast):
            compile_regex(pattern, flags)  # raises BpaUnsupported on a dead regex
            checked += 1
    assert checked > 0 or not rules


def test_shipped_rules_all_parse():
    unsupported = {}
    for path in SHIPPED:
        rules, report = load_rules_file(path)
        assert report["invalid"] == [], path
        unsupported.update({r.id: r.unsupported_reason for r in rules if r.unsupported_reason})
    # the only rule with C# ``x in y`` (not Dynamic LINQ) lived in the deleted bpa_rules_default.json
    assert set(unsupported) == set(), unsupported


def test_live_rules_have_no_double_escaped_regex():
    with open(COMPREHENSIVE, encoding="utf-8") as fh:
        for r in json.load(fh)["rules"]:
            assert "\\\\" not in r["Expression"], r["ID"]


def test_double_escaped_external_pattern_is_unescaped_not_dead():
    # A TE export that went through JSON escaping twice.
    rule = _rule('RegEx.IsMatch(Expression, "SUMX\\\\s*\\\\(\\\\s*FILTER", "(?i)")')
    model = {
        "tables": [
            {"name": "S", "measures": [{"name": "M", "expression": "SUMX ( FILTER ( S, 1 ), 1 )"}]}
        ]
    }
    res = _only(rule).run(model)
    assert [v.object_name for v in res.violations] == ["M"]


def test_dead_regex_is_reported_unsupported_not_silent():
    rule = _rule('RegEx.IsMatch(Expression, "(unclosed")')
    res = _only(rule).run(
        {"tables": [{"name": "S", "measures": [{"name": "M", "expression": "x"}]}]}
    )
    assert res.violations == []
    assert res.coverage["unsupported_rule_ids"] == ["T"]
    assert "regex" in res.coverage["unsupported_reasons"]["T"]


def test_dotnet_inline_option_mid_pattern_is_translated():
    # Python >= 3.11 rejects global flags mid-pattern; .NET allows them.
    rx = compile_regex(r"(?i)CALCULATE\s*\(\s*[^,]+,\s*(?i)FILTER\s*\(")
    assert rx.search("calculate([x], filter(t, 1))")
    assert translate_regex("a(?i)b")[1] & 2  # re.IGNORECASE


# ── I-02: grammar ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "expr,expected",
    [
        ('(Name == "Total") and (Name == "Total")', True),
        ('not (Name == "Total")', False),
        ('not IsHidden and (Name == "Total" or Name == "Other")', True),
        ('Name == "Total" and not RegEx.IsMatch(Expression, "DIVIDE")', True),
        ('RegEx.IsMatch(Expression, "SUM") and RegEx.IsMatch(Expression, "NOPE")', False),
        ('string.IsNullOrWhiteSpace(Description) and Name == "Total"', True),
        ("Expression.Length > 10", True),
        ('Convert.ToInt64(GetAnnotation("n")) + 1 == 43', True),
        ('Name.Substring(0,1).ToUpper() == "T"', True),
        ("(1 + 2) * 3 == 9 and 7 / 2 == 3", True),
    ],
)
def test_real_grammar(expr, expected):
    model = {
        "tables": [
            {
                "name": "Sales",
                "measures": [
                    {
                        "name": "Total",
                        "expression": "SUM ( Sales[Amount] )",
                        "annotations": [{"name": "n", "value": "42"}],
                    }
                ],
            }
        ]
    }
    view = ModelView(model)
    assert (
        Evaluator(view.model_node).evaluate(compile_expression(expr), view.all_measures[0])
        is expected
    )


def test_review_evidence_rules_now_give_te_verdicts(live):
    model = {
        "tables": [
            {
                "name": "Sales",
                "measures": [
                    {"name": "Margin %", "expression": "DIVIDE([A],[B])", "formatString": "0.0%"},
                    {
                        "name": "Ratio",
                        "expression": "DIVIDE([A],[B]) + [A] / [B]",
                        "formatString": "0",
                    },
                ],
            }
        ]
    }
    res = live.run(model)
    assert _ids(res, "FORMAT_PERCENTAGE_CONSISTENCY") == []
    # 2026-10-03 D-15: the rule now runs on the shared DAX analyzer (lexer), so a DIVIDE elsewhere
    # in the measure no longer masks a real unprotected `[A] / [B]`; the clean measure stays clean.
    assert _ids(res, "USE_DIVIDE_FUNCTION") == ["Sales.Ratio"]
    assert _ids(res, "ORGANIZE_DISPLAY_FOLDERS") == []  # table has 2 measures, not > 10


# ── I-03: no stale / collapsed cache ──────────────────────────────────────────


def test_same_analyzer_rerun_sees_the_edit(live):
    model = build_clean_model(20)
    assert _ids(live.run(model), "PROVIDE_FORMAT_STRING") == []
    edited = with_changes(model, lambda m: table(m, "Measures")["measures"][3].pop("formatString"))
    assert _ids(live.run(edited), "PROVIDE_FORMAT_STRING") == ["Measures.Measure 0003"]
    # and back again (no verdict inherited from the previous run)
    assert _ids(live.run(model), "PROVIDE_FORMAT_STRING") == []


def test_lambda_items_are_evaluated_individually():
    # Old engine: every .Any() item shared one cache key -> only the first was evaluated.
    rule = _rule("Columns.Any(IsHidden)", scope="Table")
    model = {
        "tables": [
            {"name": "A", "columns": [{"name": "x"}, {"name": "y", "isHidden": True}]},
            {"name": "B", "columns": [{"name": "x"}, {"name": "y"}]},
        ]
    }
    assert [v.object_name for v in _only(rule).run(model).violations] == ["A"]


def test_equality_index_gives_same_verdicts_as_plain_evaluation():
    names = [f"M{i % 30}" for i in range(100)]  # 70 duplicates across tables
    model = {
        "tables": [
            {"name": f"T{t}", "measures": [{"name": n, "expression": "1"} for n in names[t::4]]}
            for t in range(4)
        ]
    }
    rule = _rule("Model.AllMeasures.Where(Name == current.Name).Count() > 1")
    fast = _only(rule).run(model)
    view = ModelView(model)
    ev = Evaluator(view.model_node)
    ev._equality_split = lambda ast: None  # disable the fast path
    compiled = compile_expression(rule.expression)
    slow = sorted(
        f"{m.table.bpa_name}.{m.bpa_name}" for m in view.all_measures if ev.evaluate(compiled, m)
    )
    assert sorted(f"{v.table_name}.{v.object_name}" for v in fast.violations) == slow
    assert fast.cache_stats["cache_hits"] > 0


def test_index_cache_is_bounded():
    ev = Evaluator(None, index_cache_max=3)
    for i in range(10):
        ev._equality_index(list(range(i)), ("ident", "Name"))
    assert ev.cache_size() <= 3


# ── I-04: TMSL enum casing ────────────────────────────────────────────────────


def test_enum_properties_compare_case_insensitively(live):
    model = {
        "tables": [
            {
                "name": "Fact",
                "columns": [
                    {"name": "Key", "dataType": "int64"},
                    {"name": "Amount", "dataType": "double", "sourceColumn": "Amount"},
                ],
            },
            {"name": "Dim", "columns": [{"name": "Key", "dataType": "int64"}]},
            {"name": "Bridge", "columns": [{"name": "Key", "dataType": "int64"}]},
        ],
        "relationships": [
            {
                "name": "r1",
                "fromTable": "Fact",
                "fromColumn": "Key",
                "toTable": "Dim",
                "toColumn": "Key",
                "crossFilteringBehavior": "bothDirections",
            },
            {
                "name": "r2",
                "fromTable": "Fact",
                "fromColumn": "Key",
                "toTable": "Bridge",
                "toColumn": "Key",
                "toCardinality": "many",
            },
        ],
    }
    res = live.run(model)
    assert _ids(res, "AVOID_BIDIRECTIONAL_RELATIONSHIPS") == ["Fact[Key] -> Dim[Key]"]
    assert _ids(res, "AVOID_MANY_TO_MANY") == ["Fact[Key] -> Bridge[Key]"]
    assert _ids(res, "AVOID_FLOAT_FOR_FINANCIALS") == ["Fact.Amount"]


@pytest.mark.parametrize(
    "expr",
    [
        'DataType == "Int64"',
        "DataType == DataType.Int64",
        'DataType.ToString() == "Int64"',
        'SummarizeBy == "Default"',
    ],
)
def test_enum_literal_forms(expr):
    view = ModelView({"tables": [{"name": "T", "columns": [{"name": "c", "dataType": "int64"}]}]})
    assert (
        Evaluator(view.model_node).evaluate(compile_expression(expr), view.all_columns[0]) is True
    )


# ── I-05: scopes ──────────────────────────────────────────────────────────────


def _scope_model():
    return {
        "tables": [
            {
                "name": "Sales",
                "columns": [
                    {"name": "Amount", "dataType": "decimal", "sourceColumn": "Amount"},
                    {
                        "name": "CustName",
                        "type": "calculated",
                        "dataType": "string",
                        "expression": "RELATED ( Customer[Name] )",
                    },
                    {
                        "name": "RowNumber-1",
                        "type": "rowNumber",
                        "dataType": "int64",
                        "isHidden": True,
                    },
                ],
                "partitions": [
                    {"name": "Sales", "source": {"type": "m", "expression": "let x = 1 in x"}}
                ],
            },
            {"name": "Empty", "columns": [{"name": "c"}], "partitions": []},
            {"name": "Orphan", "columns": [{"name": "c"}]},
            {
                "name": "Time Intel",
                "calculationGroup": {
                    "calculationItems": [
                        {"name": "CY", "expression": "SELECTEDMEASURE()", "ordinal": 0}
                    ]
                },
                "columns": [{"name": "Name", "dataType": "string"}],
                "partitions": [{"name": "p", "source": {"type": "calculationGroup"}}],
            },
            {
                "name": "Param",
                "columns": [
                    {"name": "Value", "type": "calculatedTableColumn", "dataType": "int64"}
                ],
                "partitions": [
                    {
                        "name": "Param",
                        "source": {"type": "calculated", "expression": "GENERATESERIES(1, 10)"},
                    }
                ],
            },
        ]
    }


def test_calculated_column_scope_fires(live):
    res = live.run(_scope_model())
    assert _ids(res, "USE_RELATED_CORRECTLY") == ["Sales.CustName"]


def test_datacolumn_rules_skip_calculated_and_rownumber_columns():
    rule = _rule("true", scope="DataColumn")
    names = [v.object_name for v in _only(rule).run(_scope_model()).violations]
    assert "CustName" not in names and not any(n.startswith("RowNumber") for n in names)
    assert "Amount" in names


def test_calc_group_and_calculated_tables_are_not_in_table_scope():
    rule = _rule("true", scope="Table")
    names = [v.object_name for v in _only(rule).run(_scope_model()).violations]
    assert names == ["Sales", "Empty", "Orphan"]


def test_empty_partitions_table_does_not_abort_the_rule():
    rule = _rule("not UsedInRelationships.Any()", scope="Table")
    names = [v.object_name for v in _only(rule).run(_scope_model()).violations]
    assert names == ["Sales", "Empty", "Orphan"]


@pytest.mark.parametrize(
    "scope,expr,expected",
    [
        ("CalculationItem", 'RegEx.IsMatch(Expression, "SELECTEDMEASURE")', ["CY"]),
        ("CalculatedTable", "true", ["Param"]),
        ("CalculatedTableColumn", "true", ["Value"]),
        ("CalculationGroup", "CalculationItems.Count() == 1", ["Time Intel"]),
        ("Partition", 'SourceType == "M"', ["Sales"]),
    ],
)
def test_other_te_scopes_are_evaluated(scope, expr, expected):
    res = _only(_rule(expr, scope=scope)).run(_scope_model())
    assert [v.object_name for v in res.violations] == expected
    assert res.coverage["rules_evaluated"] == 1


def test_unknown_scope_is_unsupported_not_evaluated():
    res = _only(_rule("true", scope="Sprocket")).run(_scope_model())
    assert res.coverage["unsupported_rule_ids"] == ["T"]
    assert res.coverage["rules_evaluated"] == 0


# ── I-12: signal quality ──────────────────────────────────────────────────────


def test_clean_1500_measure_model_has_no_violations(live, clean_model):
    started = time.perf_counter()
    res = live.run(clean_model, {"max_seconds": 60, "per_rule_max_ms": 2000})
    elapsed = time.perf_counter() - started
    assert res.coverage["rules_unsupported"] == 0
    assert res.coverage["rules_truncated"] == []
    assert [(v.rule_id, v.object_name) for v in res.violations] == []
    assert elapsed < 15  # the old engine: 10,216 findings; this one runs in well under a second


def _textbook_model():
    m = build_clean_model(10)
    meas = table(m, "Measures")["measures"]

    def add(name, expr):
        meas.append(
            {
                "name": name,
                "expression": expr,
                "formatString": "#,0",
                "description": "x",
                "displayFolder": "X",
            }
        )

    add("Bad SUMX FILTER", "SUMX ( FILTER ( 'Sales', 'Sales'[Amount] > 0 ), 'Sales'[Amount] )")
    add("Bad nested FILTER", "CALCULATE ( [Measure 0000], FILTER ( FILTER ( 'Sales', 1 ), 1 ) )")
    add("Bad division", "[Measure 0000] / [Measure 0003]")
    add("Bad TODAY", "CALCULATE ( [Measure 0000], 'Date'[Date] <= TODAY () )")
    add("Bad IFERROR", "IFERROR ( [Measure 0000], 0 )")
    add("Bad COUNTROWS FILTER ALL", "COUNTROWS ( FILTER ( ALL ( 'Sales' ), 1 ) )")
    add("Commented", "// SUMX(FILTER(x, 1), 1) and a / b\nSUM ( 'Sales'[Amount] )")
    add("In a string", 'IF ( 1 = 1, "FILTER(FILTER(x / y", BLANK () )')
    add("Hidden unused", "1")
    meas[-1]["isHidden"] = True
    sales = table(m, "Sales")
    sales["columns"].append(
        {
            "name": "Label",
            "type": "calculated",
            "dataType": "string",
            "expression": "RELATED ( 'Product'[Brand] )",
            "isHidden": True,
            "isAvailableInMdx": False,
            "description": "x",
        }
    )
    m["relationships"][0]["crossFilteringBehavior"] = "bothDirections"
    m["relationships"].append(
        {
            "name": "Inactive",
            "fromTable": "Sales",
            "fromColumn": "Store Key",
            "toTable": "Store",
            "toColumn": "Store Key",
            "isActive": False,
        }
    )
    m["relationships"].append(
        {
            "name": "MM",
            "fromTable": "Budget",
            "fromColumn": "Product Key",
            "toTable": "Customer",
            "toColumn": "Customer Key",
            "toCardinality": "many",
        }
    )
    return m


@pytest.mark.parametrize(
    "rule_id,expected",
    [
        ("AVOID_SUMX_FILTER", ["Measures.Bad SUMX FILTER"]),
        ("AVOID_NESTED_FILTERS", ["Measures.Bad nested FILTER"]),
        ("USE_DIVIDE_FUNCTION", ["Measures.Bad division"]),
        ("AVOID_NOW_TODAY_IN_MEASURES", ["Measures.Bad TODAY"]),
        ("AVOID_IFERROR_ISERROR", ["Measures.Bad IFERROR"]),
        ("AVOID_COUNTROWS_FILTER_ALL", ["Measures.Bad COUNTROWS FILTER ALL"]),
        ("USE_RELATED_CORRECTLY", ["Sales.Label"]),
        ("REMOVE_UNUSED_MEASURES", ["Measures.Hidden unused"]),
        ("REMOVE_UNUSED_COLUMNS", ["Sales.Label"]),
        ("AVOID_BIDIRECTIONAL_RELATIONSHIPS", ["Sales[Product Key] -> Product[Product Key]"]),
        ("AVOID_MANY_TO_MANY", ["Budget[Product Key] -> Customer[Customer Key]"]),
        ("NO_LEGACY_RELATIONSHIPS", ["Sales[Store Key] -> Store[Store Key]"]),
    ],
)
def test_each_textbook_violation_fires_its_rule(live, rule_id, expected):
    assert _ids(live.run(_textbook_model()), rule_id) == expected


def test_comments_and_string_literals_do_not_trigger_rules(live):
    res = live.run(_textbook_model())
    hit = {v.object_name for v in res.violations}
    assert "Commented" not in hit and "In a string" not in hit


def test_userelationship_activates_the_inactive_relationship(live):
    m = _textbook_model()
    table(m, "Measures")["measures"].append(
        {
            "name": "Via store",
            "expression": "CALCULATE ( [Measure 0000], USERELATIONSHIP ( 'Sales'[Store Key], 'Store'[Store Key] ) )",
            "formatString": "#,0",
            "description": "x",
            "displayFolder": "X",
        }
    )
    assert _ids(live.run(m), "NO_LEGACY_RELATIONSHIPS") == []


def test_microsoft_ruleset_fully_evaluates():
    a = BPAAnalyzer(MICROSOFT)
    res = a.run(_textbook_model(), {"max_seconds": 60})
    assert res.coverage["rules_unsupported"] == 0, res.coverage["unsupported_reasons"]
    assert "USE_THE_DIVIDE_FUNCTION_FOR_DIVISION" in {v.rule_id for v in res.violations}
    assert RULESET_FILES["microsoft"] == MICROSOFT


# ── I-13: coverage ────────────────────────────────────────────────────────────


def test_fast_mode_emits_coverage_and_does_not_accumulate():
    a = BPAAnalyzer()
    a.rules = [_rule('Foo.Bar == "x"', rid="U1"), _rule("true", rid="S1")]
    a.run({"tables": [{"name": "T", "measures": [{"name": "m", "expression": "1"}]}]}, {})
    cov = a.get_coverage()
    assert cov["rules_evaluated"] == 1 and cov["unsupported_rule_ids"] == ["U1"]
    assert any("coverage" in n.lower() for n in a.get_run_notes())
    a.rules = [_rule("true", rid="S2")]
    a.run({"tables": [{"name": "T", "measures": [{"name": "m", "expression": "1"}]}]}, {})
    assert a.get_coverage()["rules_total"] == 1
    assert a.get_coverage()["rules_unsupported"] == 0


def test_unsupported_rules_contribute_no_violations():
    a = BPAAnalyzer()
    a.rules = [_rule('true and Sprocket.Widget == "x"', rid="U")]
    res = a.run({"tables": [{"name": "T", "measures": [{"name": "m", "expression": "1"}]}]})
    assert res.violations == [] and res.coverage["unsupported_rule_ids"] == ["U"]


# ── I-20: pure runs, per-call external rules ──────────────────────────────────


def test_concurrent_runs_do_not_interfere(live):
    small = {"tables": [{"name": "T", "measures": [{"name": "m", "expression": "1"}]}]}
    big = build_clean_model(300)
    big_tbl = table(big, "Measures")
    for m in big_tbl["measures"]:
        m.pop("formatString")
    results = {}

    def go(key, model):
        results[key] = live.run(model, {"max_seconds": 60})

    threads = [
        threading.Thread(target=go, args=(k, m)) for k, m in (("big", big), ("small", small))
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(_ids(results["big"], "PROVIDE_FORMAT_STRING")) == len(big_tbl["measures"])
    assert _ids(results["small"], "PROVIDE_FORMAT_STRING") == ["T.m"]


def test_external_rules_are_per_call_and_reported(tmp_path, live):
    path = tmp_path / "te.json"
    path.write_text(
        json.dumps(
            [
                {
                    "ID": "EXT_1",
                    "Name": "n",
                    "Category": "c",
                    "Severity": 2,
                    "Scope": "Measure",
                    "Expression": "true",
                },
                {
                    "ID": "AVOID_SUMX_FILTER",
                    "Name": "dup",
                    "Category": "c",
                    "Severity": 2,
                    "Scope": "Measure",
                    "Expression": "true",
                },
                {
                    "ID": "BAD_SEV",
                    "Name": "n",
                    "Category": "c",
                    "Severity": 4,
                    "Scope": "Measure",
                    "Expression": "true",
                },
                {
                    "ID": "UNSUP",
                    "Name": "n",
                    "Category": "c",
                    "Severity": 1,
                    "Scope": "Measure",
                    "Expression": "Sprocket.Count() > 1",
                },
            ]
        ),
        encoding="utf-8",
    )
    before = len(live.rules)
    rules, report = load_rules_file(str(path), {r.id for r in live.rules})
    assert report["added"] == 2
    assert report["skipped_duplicate_ids"] == ["AVOID_SUMX_FILTER"]
    assert report["invalid"][0]["id"] == "BAD_SEV"
    res = live.run(
        {"tables": [{"name": "T", "measures": [{"name": "m", "expression": "1"}]}]},
        extra_rules=rules,
    )
    assert "EXT_1" in {v.rule_id for v in res.violations}
    assert "UNSUP" in res.coverage["unsupported_rule_ids"]
    assert len(live.rules) == before  # nothing persisted on the shared analyzer
    assert "EXT_1" not in {v.rule_id for v in live.run({"tables": []}).violations}


def test_violation_to_dict_uses_severity_names(live):
    res = live.run(_textbook_model())
    d = res.violations[0].to_dict()
    assert d["severity"] in ("INFO", "WARNING", "ERROR") and isinstance(d["severity_level"], int)


def test_rule_expression_as_json_array_is_joined(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "ID": "A",
                        "Name": "n",
                        "Severity": 1,
                        "Scope": "Measure",
                        "Expression": ["IsHidden", "or", "true"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    rules, _ = load_rules_file(str(path))
    assert rules[0].compiled is not None


def test_multiline_tmsl_expression_arrays_are_joined(live):
    m = build_clean_model(3)
    table(m, "Measures")["measures"].append(
        {
            "name": "Arr",
            "expression": ["SUMX (", "  FILTER ( 'Sales', 1 ),", "  1 )"],
            "formatString": "#",
            "description": "x",
            "displayFolder": "X",
        }
    )
    assert _ids(live.run(m), "AVOID_SUMX_FILTER") == ["Measures.Arr"]


def test_copy_of_model_not_mutated_by_run(live, clean_model):
    snapshot = copy.deepcopy(clean_model)
    live.run(clean_model, {"max_tables": 3})
    assert clean_model == snapshot


def test_unsupported_error_type_is_public():
    with pytest.raises(BpaUnsupported):
        compile_regex("(")
