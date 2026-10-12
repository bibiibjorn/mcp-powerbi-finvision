"""F-33 (2026-10-03 deep review): ``05_DAX_Intelligence explain`` - structured explanation.

Structure only (no prose): call tree with argument roles, filter modifiers in order, context
transitions, iterators, VARs with their uses, calculation-group / RLS notes, dependencies.
"""

from unittest.mock import MagicMock

import pytest

import server.handlers.dax_context_handler as dh
from core.dax.analyzer.models import ModelSchema
from core.dax.explain import explain_expression

SCHEMA = ModelSchema(
    measure_names={"sales", "cost", "margin"},
    table_names={"sales", "date", "product"},
    udf_signatures={"MYFN": ["expr", "val"]},
)

MEASURE = """VAR _ly = CALCULATE([Sales], SAMEPERIODLASTYEAR('Date'[Date]))
VAR _cost = [Cost]
VAR _unused = 1
RETURN IF(_ly > 0,
    SUMX(VALUES('Product'[Category]),
        CALCULATE([Margin], KEEPFILTERS('Product'[Color] = "Red"), REMOVEFILTERS('Date'),
            USERELATIONSHIP(Sales[ShipDate], 'Date'[Date])) * _cost),
    BLANK())"""


@pytest.fixture(scope="module")
def ex():
    return explain_expression(
        MEASURE,
        schema=SCHEMA,
        calc_groups=[{"table": "Time Intelligence", "items": ["YTD"]}],
        roles=[{"name": "R1", "tables": ["Sales"]}],
        measure="Margin LY",
        table="M",
    )


def _walk(nodes):
    for n in nodes:
        yield n
        for a in n["args"]:
            yield from _walk(a.get("calls", []))


def test_call_tree_has_function_kind_and_argument_roles(ex):
    fns = {n["function"]: n for n in _walk(ex["call_tree"])}
    assert fns["CALCULATE"]["kind"] == "context_transition"
    assert fns["SUMX"]["kind"] == "iterator" and fns["IF"]["kind"] == "conditional"
    sumx = fns["SUMX"]
    assert [a["role"] for a in sumx["args"]] == ["table (row source)", "expression (per row)"]
    iff = fns["IF"]
    assert [a["role"] for a in iff["args"]] == ["condition", "then", "else"]
    cal = [n for n in _walk(ex["call_tree"]) if n["function"] == "CALCULATE"][1]
    assert [a["role"] for a in cal["args"]] == [
        "expression",
        "filter modifier",
        "filter modifier",
        "filter modifier",
    ]
    # each argument lists its OWN references (nested calls report theirs)
    assert cal["args"][0]["refs"] == [{"ref": "[Margin]", "kind": "measure"}]
    assert "refs" not in cal["args"][2]  # REMOVEFILTERS('Date'): the table is a TABLE_REF


def test_filter_modifiers_come_in_source_order_and_are_classified(ex):
    mods = ex["filter_modifiers"]
    assert [m["order"] for m in mods] == [1, 2, 3, 4]
    assert [m["function"] for m in mods] == [
        "SAMEPERIODLASTYEAR",
        "KEEPFILTERS",
        "REMOVEFILTERS",
        "USERELATIONSHIP",
    ]
    assert "intersection" in mods[1]["effect"]
    assert mods[3]["columns"] == ["Sales[ShipDate]", "'Date'[Date]"]
    assert all(m["in_function"] == "CALCULATE" for m in mods)


def test_boolean_and_filter_table_arguments():
    r = explain_expression(
        "CALCULATE([Sales], 'Date'[Year] = 2024, FILTER(ALL('Product'), [Margin] > 0), "
        "TREATAS({1}, Sales[Key]), ALLEXCEPT(Sales, Sales[Key]))",
        schema=SCHEMA,
    )
    mods = r["filter_modifiers"]
    assert mods[0]["function"] is None and "boolean filter" in mods[0]["effect"]
    assert mods[0]["columns"] == ["'Date'[Year]"]
    assert mods[1]["function"] == "FILTER" and "ALL" in mods[1]["effect"]
    assert mods[2]["function"] == "TREATAS" and mods[3]["function"] == "ALLEXCEPT"


def test_context_transitions_distinguish_row_context_from_the_top_level(ex):
    tr = ex["context_transitions"]
    explicit = [t for t in tr if t["kind"] == "explicit_calculate"]
    assert [t["in_row_context"] for t in explicit] == [False, True]
    assert explicit[1]["row_context_from"] == "SUMX"
    implicit = [t for t in tr if t["kind"] == "implicit_measure_reference"]
    assert [t["ref"] for t in implicit] == ["[Margin]"]  # [Sales]/[Cost] are not in a row context
    assert ex["summary"]["context_transitions"] == 2


def test_iterators_report_row_source_and_per_row_measures(ex):
    (it,) = ex["iterators"]
    assert it["function"] == "SUMX" and it["row_source"] == "VALUES('Product'[Category])"
    assert it["measures_evaluated_per_row"] == ["[Margin]"]


def test_variables_track_uses_unused_and_eager_evaluation(ex):
    v = {x["name"]: x for x in ex["variables"]}
    assert v["_ly"]["used_in"][0]["conditional"] is False and not v["_ly"]["unused"]
    assert v["_unused"]["unused"] is True
    assert v["_cost"]["used_in"][0]["conditional"] is True
    assert "eager_evaluation_note" in v["_cost"] and "eager_evaluation_note" not in v["_ly"]
    assert v["_ly"]["depends_on_refs"] == ["[Sales]", "'Date'[Date]"]
    assert v["_ly"]["type"] == "scalar"
    assert ex["summary"]["unused_variables"] == 1


def test_var_used_by_another_var_is_not_flagged_eager():
    r = explain_expression(
        "VAR _a = [Sales] VAR _b = _a * 2 RETURN IF([Cost] > 0, _b, 0)", schema=SCHEMA
    )
    a = {x["name"]: x for x in r["variables"]}["_a"]
    assert a["used_in"][0]["used_by"] == "VAR _b" and "eager_evaluation_note" not in a


def test_calculation_group_and_rls_notes(ex):
    cg = ex["calculation_groups"]
    assert cg["model_calculation_groups"][0]["table"] == "Time Intelligence"
    assert "MEASURE REFERENCES" in cg["model_note"]
    assert cg["selectedmeasure_family_used"] == []
    rls = ex["rls"]
    assert rls["roles_defined"] is True
    assert rls["secured_tables_read_directly"] == ["sales"]
    assert rls["roles_filtering_those_tables"] == {"sales": ["R1"]}


def test_selectedmeasure_body_is_flagged_as_a_calc_item():
    r = explain_expression("CALCULATE(SELECTEDMEASURE(), DATESYTD('Date'[Date]))", schema=SCHEMA)
    assert (
        r["calculation_groups"]["selectedmeasure_family_used"][0]["function"] == "SELECTEDMEASURE"
    )
    assert "calculation item" in r["calculation_groups"]["note"]
    assert r["rls"] == {"roles_defined": False}


def test_dependencies_and_udf_calls(ex):
    deps = ex["dependencies"]
    assert deps["measures"] == ["[Sales]", "[Cost]", "[Margin]"]
    assert "'Product'[Color]" in deps["columns"] and deps["tables"] == ["Date", "Product", "Sales"]
    r = explain_expression("MyFn([Sales], 2) + SUM(Sales[Amount])", schema=SCHEMA)
    assert r["dependencies"]["udf_calls"] == [
        {"function": "MyFn", "parameter_modes": ["expr", "val"]}
    ]


def test_summary_counts(ex):
    s = ex["summary"]
    assert s["calls"] == 10 and s["variables"] == 3 and s["iterators"] == 1
    assert s["filter_modifiers"] == 4 and s["explicit_calculates"] == 2
    assert s["max_call_depth"] >= 4 and s["measure_dependencies"] == 3


def test_expression_without_schema_and_empty_input_do_not_fail():
    r = explain_expression("[Sales] / [Cost]")
    assert r["call_tree"] == [] and r["dependencies"]["measures"] == []
    assert {x["kind"] for x in r["dependencies"].get("columns", [])} == set()
    assert explain_expression("")["summary"]["calls"] == 0


# --- handler ------------------------------------------------------------------------------------------


def _connect(monkeypatch, rows, tmsl=None):
    cs = MagicMock()
    cs.is_connected.return_value = True
    qe = MagicMock()
    qe.enumerate_measures_tom.return_value = {"success": True, "rows": rows, "row_count": len(rows)}
    if tmsl is not None:
        qe.get_tmsl_definition.return_value = {"success": True, "tmsl": tmsl}
    cs.query_executor = qe
    monkeypatch.setattr(dh, "connection_state", cs)
    return qe


ROWS = [
    {"Name": "Margin LY", "Table": "M", "Expression": MEASURE},
    {"Name": "Sales", "Table": "F", "Expression": "SUM(F[Amount])"},
    {"Name": "Cost", "Table": "F", "Expression": "SUM(F[Cost])"},
    {"Name": "Margin", "Table": "F", "Expression": "[Sales] - [Cost]"},
]

TMSL = {
    "model": {
        "tables": [
            {"name": "Sales", "measures": [], "columns": []},
            {
                "name": "TI",
                "calculationGroup": {
                    "precedence": 5,
                    "calculationItems": [{"name": "YTD", "expression": "SELECTEDMEASURE()"}],
                },
            },
        ],
        "roles": [
            {"name": "EU", "tablePermissions": [{"name": "Sales", "filterExpression": "1=1"}]}
        ],
    }
}


def test_explain_handler_for_a_model_measure_uses_tmsl_roles_and_calc_groups(monkeypatch):
    _connect(monkeypatch, ROWS, tmsl=TMSL)
    res = dh.handle_dax_intelligence({"operation": "explain", "measure_name": "Margin LY"})
    assert res["success"] is True and res["source"] == "model measure"
    assert res["measure"] == {"name": "Margin LY", "table": "M"}
    assert res["calculation_groups"]["model_calculation_groups"] == [
        {"table": "TI", "precedence": 5, "items": ["YTD"]}
    ]
    assert res["rls"]["roles"] == ["EU"]
    assert res["dependencies"]["measures"] == ["[Sales]", "[Cost]", "[Margin]"]


def test_explain_handler_accepts_a_dax_expression(monkeypatch):
    _connect(monkeypatch, ROWS)
    res = dh.handle_dax_intelligence(
        {"operation": "explain", "expression": "CALCULATE([Sales], 'Date'[Year] = 2024)"}
    )
    assert res["success"] is True and res["source"] == "expression"
    assert res["filter_modifiers"][0]["columns"] == ["'Date'[Year]"]


def test_explain_handler_unknown_measure_suggests(monkeypatch):
    _connect(monkeypatch, ROWS)
    res = dh.handle_dax_intelligence({"operation": "explain", "measure_name": "Margin LX"})
    assert res["success"] is False and res["error_type"] == "measure_not_found"


def test_explain_handler_requires_a_target(monkeypatch):
    _connect(monkeypatch, ROWS)
    res = dh.handle_dax_intelligence({"operation": "explain"})
    assert res["success"] is False and res["error_type"] == "missing_argument"


def test_explain_include_values_calls_step_variables_and_merges_values(monkeypatch):
    _connect(monkeypatch, ROWS)
    calls = []

    def fake_step(args):
        calls.append(args)
        return {
            "success": True,
            "evaluation_context": "each VAR evaluated with no filters (model total)",
            "steps": [
                {"var_name": "_ly", "type": "scalar", "value": 42.0},
                {"var_name": "_cost", "type": "scalar", "value": 7.0},
            ],
            "slowest_variable": {"name": "_ly", "ms": 3.0},
        }

    import server.handlers._debug.variables as var_mod

    monkeypatch.setattr(var_mod, "_handle_step_variables", fake_step)
    res = dh.handle_dax_intelligence(
        {
            "operation": "explain",
            "measure_name": "Margin LY",
            "include_values": True,
            "filters": ["'Date'[Year] = 2025"],
            "max_rows": 3,
        }
    )
    assert calls == [
        {
            "measure_name": "Margin LY",
            "table_name": "M",
            "filters": ["'Date'[Year] = 2025"],
            "max_rows": 3,
        }
    ]
    v = {x["name"]: x for x in res["variables"]}
    assert v["_ly"]["value"] == {"value": 42.0} and v["_cost"]["value"] == {"value": 7.0}
    assert "value" not in v["_unused"]
    assert res["variable_values"]["slowest_variable"]["name"] == "_ly"


def test_explain_include_values_failure_is_reported_not_raised(monkeypatch):
    _connect(monkeypatch, ROWS)
    import server.handlers._debug.variables as var_mod

    monkeypatch.setattr(
        var_mod, "_handle_step_variables", lambda a: {"success": False, "error": "no visual"}
    )
    res = dh.handle_dax_intelligence(
        {"operation": "explain", "measure_name": "Margin LY", "include_values": True}
    )
    assert res["success"] is True and res["variable_values_error"] == "no visual"


def test_explain_include_values_needs_a_model_measure_with_vars(monkeypatch):
    _connect(monkeypatch, ROWS)
    res = dh.handle_dax_intelligence(
        {"operation": "explain", "expression": "[Sales] + 1", "include_values": True}
    )
    assert "model measure" in res["variable_values_error"]
    res = dh.handle_dax_intelligence(
        {"operation": "explain", "measure_name": "Margin", "include_values": True}
    )
    assert res["variable_values_error"] == "the measure declares no VAR"
