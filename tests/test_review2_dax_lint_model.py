"""2026-10-03 deep review, slice D: lint_model covers every DAX-bearing object (D-16), carries the
TMSL schema facts the storage-mode / topology rules need (D-05, D-09), and only calls a finding
auto-fixable when the transform would change the measure (D-04). Also the TMSL object extractor
and the schema builder."""

import json
from unittest.mock import MagicMock

import pytest

import server.handlers.dax_context_handler as dh
from core.dax.analyzer.tmsl_objects import extract_dax_objects, resolve_model, schema_from_tmsl

TMSL = {
    "name": "M",
    "tables": [
        {
            "name": "Sales",
            "partitions": [{"name": "Sales", "mode": "directQuery", "source": {"type": "m"}}],
            "columns": [
                {"name": "Amount", "type": "data"},
                {
                    "name": "Margin",
                    "type": "calculated",
                    "expression": ["RELATED(Product[Price])", " - Sales[Amount]"],
                },
            ],
            "measures": [
                {
                    "name": "Ratio",
                    "expression": "SUM(Sales[Amount]) / SUM(Sales[Qty])",
                    "formatStringDefinition": {"expression": 'IF([Ratio] > 1, "0.0", "0.00")'},
                    "kpi": {"targetExpression": "100"},
                }
            ],
        },
        {
            "name": "Time Intelligence",
            "calculationGroup": {
                "precedence": 10,
                "calculationItems": [
                    {
                        "name": "YTD",
                        "expression": "CALCULATE(SELECTEDMEASURE(), DATESYTD('Date'[Date]))",
                        "formatStringDefinition": {
                            "expression": 'IF(SELECTEDMEASURE() > 1, "0", "0.0")'
                        },
                    }
                ],
            },
            "partitions": [{"name": "TI", "source": {"type": "calculationGroup"}}],
        },
        {
            "name": "Dates",
            "partitions": [
                {
                    "name": "Dates",
                    "mode": "import",
                    "source": {"type": "calculated", "expression": "CALENDARAUTO()"},
                }
            ],
        },
        {
            "name": "FieldParam",
            "partitions": [
                {
                    "name": "FieldParam",
                    "source": {
                        "type": "calculated",
                        "expression": "{ (\"Sales\", NAMEOF('Sales'[Ratio]), 0) }",
                    },
                }
            ],
        },
    ],
    "relationships": [
        {
            "fromTable": "Sales",
            "fromColumn": "k",
            "toTable": "Dates",
            "toColumn": "k",
            "crossFilteringBehavior": "bothDirections",
        }
    ],
    "roles": [
        {
            "name": "Reader",
            "tablePermissions": [{"name": "Sales", "filterExpression": '[Region] = "EU"'}],
        }
    ],
}


def test_tmsl_extractor_lists_every_non_measure_expression():
    kinds = sorted((o.kind, o.table, o.name) for o in extract_dax_objects(TMSL))
    assert kinds == [
        ("calc_column", "Sales", "Margin"),
        ("calc_item", "Time Intelligence", "YTD"),
        ("calc_item_format_string", "Time Intelligence", "YTD"),
        ("calc_table", "Dates", "Dates"),
        ("calc_table", "FieldParam", "FieldParam"),
        ("format_string", "Sales", "Ratio"),
        ("kpi", "Sales", "Ratio.targetExpression"),
        ("rls", "Sales", "Reader"),
    ]
    margin = next(o for o in extract_dax_objects(TMSL) if o.name == "Margin")
    assert margin.expression == "RELATED(Product[Price])\n - Sales[Amount]"  # list-of-lines form


def test_tmsl_schema_carries_storage_mode_topology_and_calc_group_facts():
    schema = schema_from_tmsl(TMSL)
    assert schema.storage_mode("Sales") == "directquery"
    assert schema.storage_mode("Dates") == "import"
    assert schema.is_directquery_only(["Sales"]) and not schema.is_directquery_only(
        ["Sales", "Dates"]
    )
    assert "time intelligence" in schema.calculation_group_tables
    assert "fieldparam" in schema.field_parameter_tables
    assert schema.secured_tables == {"sales"}
    assert schema.cross_filtered_tables() == {"sales", "dates"}


def test_resolve_model_accepts_the_wrappers():
    assert resolve_model(json.dumps(TMSL))["name"] == "M"
    assert resolve_model({"model": TMSL})["name"] == "M"
    assert resolve_model("not json") is None


@pytest.fixture
def conn(monkeypatch):
    cs = MagicMock()
    cs.is_connected.return_value = True
    qe = MagicMock()
    qe.enumerate_measures_tom.return_value = {
        "success": True,
        "rows": [
            {
                "Name": "Ratio",
                "Table": "Sales",
                "Expression": "SUM(Sales[Amount]) / SUM(Sales[Qty])",
            },
            {
                "Name": "InIter",
                "Table": "Sales",
                "Expression": "SUMX(Sales, Sales[Amount] / Sales[Qty])",
            },
        ],
    }
    qe.validate_and_execute_dax.return_value = {"success": False, "error": "no udf"}
    qe.execute_info_query.return_value = {"success": True, "rows": []}
    qe.get_tmsl_definition.return_value = {"success": True, "tmsl": json.dumps(TMSL)}
    cs.query_executor = qe
    monkeypatch.setattr(dh, "connection_state", cs)
    return cs, qe


def test_lint_model_lints_every_object_kind_with_object_kind_in_the_output(conn):
    res = dh.handle_dax_intelligence({"operation": "lint_model"})
    assert res["success"] and res["tmsl_available"] is True
    assert res["other_objects_analyzed"] == {
        "calc_column": 1,
        "calc_item": 1,
        "calc_item_format_string": 1,
        "calc_table": 2,
        "format_string": 1,
        "kpi": 1,
        "rls": 1,
    }
    by = {(f.get("object_kind"), f["rule_id"]) for f in res["findings"]}
    assert ("calc_item_format_string", "PERF_FORMAT_STRING_DOUBLE_EVAL") in by
    assert ("format_string", "PERF_FORMAT_STRING_DOUBLE_EVAL") in by
    assert ("calc_column", "PERF_RELATED_IN_CALC_COLUMN") in by
    assert ("calc_table", "PERF_CALENDARAUTO") in by


def test_lint_model_uses_the_tmsl_storage_mode_for_the_iferror_divide_advice(conn):
    _cs, qe = conn
    qe.enumerate_measures_tom.return_value = {
        "success": True,
        "rows": [
            {
                "Name": "Safe",
                "Table": "Sales",
                "Expression": "IFERROR(Sales[Amount] / Sales[Qty], 0)",
            }
        ],
    }
    res = dh.handle_dax_intelligence({"operation": "lint_model", "tables": ["Sales"]})
    rows = {f["rule_id"]: f for f in res["findings"] if f["measure"] == "Safe"}
    assert rows["CORR_IFERROR_USAGE"]["severity"] == "info"  # Sales is DirectQuery-only
    assert rows["CORR_DIVISION_WITHOUT_DIVIDE"]["auto_fixable"] is False  # info: not offered


def test_lint_model_auto_fixable_means_the_transform_would_edit(conn):
    res = dh.handle_dax_intelligence({"operation": "lint_model", "tables": ["Sales"]})
    div = {
        f["measure"]: f
        for f in res["findings"]
        if f["rule_id"] == "CORR_DIVISION_WITHOUT_DIVIDE" and not f.get("object_kind")
    }
    # the DQ schema downgrades both; the point: an in-iterator division is never offered as a fix
    assert div["InIter"]["auto_fixable"] is False
    assert res["auto_fixable_count"] == sum(1 for f in res["findings"] if f["auto_fixable"])


def test_lint_model_without_tmsl_still_lints_measures(conn):
    _cs, qe = conn
    qe.get_tmsl_definition.return_value = {"success": False, "error": "AMO/TOM not available"}
    res = dh.handle_dax_intelligence({"operation": "lint_model"})
    assert res["success"] and res["tmsl_available"] is False
    assert res["other_objects_analyzed"] == {}
    assert res["measures_analyzed"] == 2


def test_lint_model_passes_udf_signatures_to_the_schema(conn):
    _cs, qe = conn
    qe.enumerate_measures_tom.return_value = {
        "success": True,
        "rows": [
            {
                "Name": "Use",
                "Table": "Sales",
                "Expression": "SUMX(_Set, 'F'[m] * AmountFor({ 'F'[e] }))",
            }
        ],
    }
    qe.validate_and_execute_dax.return_value = {
        "success": True,
        "rows": [
            {
                "[Name]": "AmountFor",
                "[Expression]": "(x : ANYVAL EXPR) => CALCULATE(SUM(Sales[Amount]), Sales[E] = x)",
            }
        ],
    }
    res = dh.handle_dax_intelligence({"operation": "lint_model"})
    assert "CORR_UDF_EXPR_BARE_COLUMN" in {f["rule_id"] for f in res["findings"]}


def test_schema_builder_resolves_relationship_names_and_storage_mode():
    from core.dax.analyzer.schema_builder import build_model_schema

    views = {
        "TABLES": [{"ID": 1, "Name": "Sales"}, {"ID": 2, "Name": "Users"}],
        "PARTITIONS": [{"TableID": 1, "Mode": 1}],
        "RELATIONSHIPS": [
            {
                "FromTableID": 1,
                "ToTableID": 2,
                "FromCardinality": 2,
                "ToCardinality": 1,
                "CrossFilteringBehavior": 2,
            }
        ],
        "MEASURES": [],
        "COLUMNS": [],
        "ROLES": [],
    }
    qe = MagicMock()
    qe.execute_info_query.side_effect = lambda view, *a, **k: {
        "success": True,
        "rows": views.get(view, []),
    }
    schema = build_model_schema(qe)
    assert schema.storage_mode("Sales") == "directquery"
    assert schema.cross_filtered_tables() == {"sales", "users"}


# ---- 09-22 ledger D-27: field-parameter refs come from the lexer, not a regex -----------------


def test_field_parameter_refs_ignore_string_literals_and_comments():
    from core.analysis.column_usage_analyzer import ColumnUsageAnalyzer

    source = (
        "{ (\"'Ghost'[In String]\", NAMEOF('Sales'[Amount]), 0) }  // 'Ghost'[In Comment]\n"
        ", SELECTEDVALUE('Lookup'[Label])"
    )

    class _QE:
        def execute_info_query(self, kind, *a, **k):
            if kind == "MEASURES":
                return {"success": True, "rows": []}
            if kind == "COLUMNS":
                return {
                    "success": True,
                    "rows": [
                        {"Table": "FP", "ExplicitName": "Fields", "ID": 1},
                        {"Table": "Sales", "ExplicitName": "Amount", "ID": 2},
                        {"Table": "Lookup", "ExplicitName": "Label", "ID": 3},
                    ],
                }
            return {"success": True, "rows": []}

        def validate_and_execute_dax(self, *a, **k):
            return {"success": True, "rows": [{"Table": "FP", "Source": source}]}

        def list_udfs(self):
            return {"success": False}

    res = ColumnUsageAnalyzer(_QE()).build_complete_mapping(force_refresh=True)
    refs = {k.lower() for k in res.field_parameter_ref_columns}
    assert any("sales" in k and "amount" in k for k in refs)
    assert any("lookup" in k and "label" in k for k in refs)
    assert not any("ghost" in k for k in refs)


# ---- 09-22 ledger D-23: lint-disable measure annotation ----------------------------------------


def test_measure_annotation_lint_disable_suppresses_the_listed_rules(conn):
    _cs, qe = conn
    tmsl = json.loads(json.dumps(TMSL))
    tmsl["tables"][0]["measures"][0]["annotations"] = [
        {"name": "lint-disable", "value": "CORR_DIVISION_WITHOUT_DIVIDE, PERF_NEVER_BLANK"}
    ]
    qe.get_tmsl_definition.return_value = {"success": True, "tmsl": json.dumps(tmsl)}
    res = dh.handle_dax_intelligence({"operation": "lint_model", "tables": ["Sales"]})
    ratio_rules = {
        f["rule_id"]
        for f in res["findings"]
        if f["measure"] == "Ratio" and not f.get("object_kind")
    }
    assert "CORR_DIVISION_WITHOUT_DIVIDE" not in ratio_rules
    entry = next(
        m for m in res["per_measure"] if m["measure"] == "Ratio" and "object_kind" not in m
    )
    assert "CORR_DIVISION_WITHOUT_DIVIDE" in entry["suppressed"]


# ---- D-01: the analyze / all handler paths carry ONE advice engine --------------------------------


@pytest.mark.parametrize("mode", ["analyze", "all", "report"])
def test_d01_handler_anti_patterns_block_comes_from_the_unified_analyzer(conn, mode):
    _cs, qe = conn
    qe.validate_and_execute_dax.return_value = {"success": True, "rows": []}
    res = dh.handle_dax_intelligence(
        {
            "expression": "SUMX(Sales, IF(Sales[Qty] > 0, Sales[Amount], 0))",
            "analysis_mode": mode,
        }
    )
    assert res["success"] is True, res
    if mode == "report":
        return  # report mode returns text; it must simply not crash without the research engine
    block = res["anti_patterns"]
    assert "CB001" in block["pattern_matches"]  # a rule id, not a regex "article" id
    assert not any(
        k in block["pattern_matches"] for k in ("sumx_filter", "filter_all", "divide_zero_check")
    )
    for rec in block.get("recommendations", []):
        assert "5-10x" not in rec and "HIGH IMPACT" not in rec
