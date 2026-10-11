"""2026-09-22 deep review, slice D — 05_DAX_Intelligence handler.

D-04 measure resolution, D-07 auto-fix map, D-10 HTML opt-in, D-14 validation
wrapper, D-19 expand_measure escaping/deps, D-22 reference-index staleness,
D-23 lint_model output, D-25 schema/annotations.
"""

from unittest.mock import MagicMock

import pytest

import server.handlers.dax_context_handler as dh

ROWS = [
    {
        "Name": "Total Sales Amount",
        "Table": "m",
        "Expression": "SUMX(Sales, Sales[Qty] * Sales[Price])",
    },
    {"Name": "Margin %", "Table": "m", "Expression": "DIVIDE([Margin], [Total Sales Amount])"},
    {
        "Name": "Sales YTD",
        "Table": "m",
        "Expression": "TOTALYTD([Total Sales Amount], 'Date'[Date])",
    },
    {
        "Name": 'Target "Q"',
        "Table": "m",
        "Expression": "\"http://x\" & [Sales ]]x] & 'm'[Margin %]",
    },
    {"Name": "Sales ]x", "Table": "m", "Expression": "1"},
    {"Name": "Margin", "Table": "m", "Expression": "2 // trailing"},
    {"Name": "NAV TWR (%) Axis (cumul)", "Table": "m", "Expression": "3"},
]


@pytest.fixture
def conn(monkeypatch):
    cs = MagicMock()
    cs.is_connected.return_value = True
    qe = MagicMock()
    qe.enumerate_measures_tom.return_value = {"success": True, "rows": list(ROWS)}
    qe.validate_and_execute_dax.return_value = {"success": True, "rows": []}
    qe.execute_info_query.return_value = {"success": True, "rows": []}
    cs.query_executor = qe
    monkeypatch.setattr(dh, "connection_state", cs)
    return cs, qe


# --- D-04 ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dax",
    ["SUM(Sales[Amount])", "COUNTROWS(Sales)", "[Sales] * 1.21", "SELECTEDVALUE('Date'[Year])"],
)
def test_one_line_dax_is_analysed_verbatim(conn, dax):
    r = dh._resolve_measure_input({"expression": dax})
    assert r.get("error") is None
    assert r["measure"] is None and r["expression"] == dax


@pytest.mark.parametrize(
    "inp,name",
    [
        ("margin %", "Margin %"),
        ("[Margin %]", "Margin %"),
        ("NAV TWR (%) Axis (cumul)", "NAV TWR (%) Axis (cumul)"),
    ],
)
def test_exact_name_resolves(conn, inp, name):
    r = dh._resolve_measure_input({"expression": inp})
    assert r["measure"] == name


def test_near_miss_name_is_suggested_not_substituted(conn):
    r = dh._resolve_measure_input({"expression": "Total Sales"})
    err = r["error"]
    assert err["success"] is False and err["error_type"] == "measure_not_found"
    assert "'m'[Total Sales Amount]" in err["suggestions"]


def test_explicit_measure_name_must_match_exactly(conn):
    assert dh._resolve_measure_input({"measure_name": "sales ytd"})["measure"] == "Sales YTD"
    assert (
        dh._resolve_measure_input({"measure_name": "Sales Y"})["error"]["error_type"]
        == "measure_not_found"
    )


def test_handler_reports_resolved_measure(conn, monkeypatch):
    res = dh.handle_dax_intelligence(
        {"measure_name": "Sales YTD", "analysis_mode": "debug", "output_format": "steps"}
    )
    assert res["success"] is True
    assert res["measure_info"]["name"] == "Sales YTD"


def test_measures_come_from_the_executor_not_a_new_amo_connection(conn):
    _cs, qe = conn
    dh._resolve_measure_input({"expression": "Margin"})
    qe.enumerate_measures_tom.assert_called()
    assert not hasattr(dh, "AMOServer")


# --- D-14 ------------------------------------------------------------------------------


def test_trailing_comment_cannot_swallow_the_wrapper():
    q = dh._build_validation_query("SUM(Sales[Amount]) // total")
    assert q.endswith("// total\n))")


def test_define_and_evaluate_queries_pass_through():
    for q in ['DEFINE MEASURE T[x]=1 EVALUATE ROW("a",[x])', '// c\nEVALUATE ROW("a", 1)']:
        assert dh._build_validation_query(q) == q


def test_leading_comment_does_not_break_table_detection():
    assert dh._build_validation_query("// lead\nFILTER(Sales, Sales[a] > 1)").startswith(
        "EVALUATE TOPN(0, "
    )


def test_scalar_validation_does_not_compute_the_measure():
    q = dh._build_validation_query("CALCULATE(SUM(T[a]), FILTER(T, T[b] = 1))")
    assert q.startswith('EVALUATE ROW("Result", IF(FALSE(), ')


def test_invalid_dax_is_a_failure(conn):
    _cs, qe = conn
    qe.validate_and_execute_dax.return_value = {"success": False, "error": "Syntax error near X"}
    res = dh.handle_dax_intelligence({"expression": "SUM(T[a]) +", "analysis_mode": "analyze"})
    assert res["success"] is False and res["error_type"] == "invalid_dax"


def test_operation_analyze_is_accepted(conn):
    res = dh.handle_dax_intelligence(
        {
            "operation": "analyze",
            "expression": "SUM(T[a])",
            "analysis_mode": "debug",
            "skip_validation": True,
            "output_format": "steps",
        }
    )
    assert res["success"] is True and res["mode"] == "debug"


# --- D-19 / D-27: expand_measure ----------------------------------------------------------


def test_expand_measure_escapes_names_and_finds_all_deps(conn):
    res = dh._handle_expand_measure({"expression": 'Target "Q"'})
    assert res["success"] is True
    assert set(res["dependencies"]) == {"Sales ]x", "Margin %", "Margin", "Total Sales Amount"}
    q = res["query"]
    assert "MEASURE 'm'[Sales ]]x] = 1" in q
    assert 'ROW("Target ""Q""", \'m\'[Target "Q"])' in q
    # dependencies are defined before dependants
    assert q.index("'m'[Margin] =") < q.index("'m'[Margin %] =") < q.index("'m'[Target \"Q\"] =")
    # a trailing // comment in a body cannot swallow the next definition
    assert "2 // trailing\n" in q


def test_expand_measure_defines_called_udfs(conn):
    _cs, qe = conn
    rows = list(ROWS) + [{"Name": "UsesUdf", "Table": "m", "Expression": "AddTax([Margin])"}]
    qe.enumerate_measures_tom.return_value = {"success": True, "rows": rows}
    qe.validate_and_execute_dax.return_value = {
        "success": True,
        "rows": [{"[Name]": "AddTax", "[Expression]": "(x: NUMERIC) => x * 1.21"}],
    }
    res = dh._handle_expand_measure({"measure_name": "UsesUdf"})
    assert res["functions"] == ["AddTax"]
    assert "FUNCTION AddTax = (x: NUMERIC) => x * 1.21" in res["query"]


def test_expand_measure_inline_mode(conn):
    res = dh._handle_expand_measure({"measure_name": "Margin %", "inline": True})
    assert res["mode"] == "inline"
    assert "CALCULATE(\n2 // trailing\n)" in res["inlined_expression"]
    assert "[Margin]" not in res["inlined_expression"]


# --- D-07 / D-23: lint_model ----------------------------------------------------------------


def test_lint_model_filters_summary_and_sort(conn):
    _cs, qe = conn
    qe.enumerate_measures_tom.return_value = {
        "success": True,
        "rows": [
            {
                "Name": "A",
                "Table": "F",
                "Expression": "SUMX(Sales, IF(Sales[Qty] > 0, Sales[Amount], 0))",
            },
            {"Name": "B", "Table": "G", "Expression": "[x] / [y]"},
            {"Name": "C", "Table": "F", "Expression": 'FORMAT([S], "0") // lint-disable CB005'},
        ],
    }
    qe.validate_and_execute_dax.return_value = {"success": False, "error": "not recognized"}
    res = dh.handle_dax_intelligence({"operation": "lint_model"})
    assert res["success"] is True
    assert res["findings"][0]["severity"] == "high"
    assert "health_score" not in res["findings"][0]
    pm = {m["measure"]: m for m in res["per_measure"]}
    assert pm["C"]["suppressed"] == ["CB005"]
    b = next(f for f in res["findings"] if f["measure"] == "B")
    assert (
        b["rule_id"] == "CORR_DIVISION_WITHOUT_DIVIDE"
        and b["auto_fixable"]
        and b["fix_transform"] == "use_divide"
    )

    only_high = dh.handle_dax_intelligence({"operation": "lint_model", "min_severity": "high"})
    assert {f["severity"] for f in only_high["findings"]} <= {"high", "critical"}
    only_g = dh.handle_dax_intelligence({"operation": "lint_model", "tables": ["G"]})
    assert {f["measure"] for f in only_g["findings"]} == {"B"}
    capped = dh.handle_dax_intelligence({"operation": "lint_model", "max_findings": 1})
    assert (
        capped["returned_findings"] == 1
        and capped["omitted_findings"] == capped["total_findings"] - 1
    )
    sup = dh.handle_dax_intelligence({"operation": "lint_model", "suppress_rules": ["CB001"]})
    assert "CB001" not in {f["rule_id"] for f in sup["findings"]}


def test_udf_header_detection_ignores_comments():
    assert dh._starts_with_function_header("/// doc\nFUNCTION f = (x) => x") is True
    assert dh._starts_with_function_header("(x) => x // FUNCTION") is False


# --- D-25: schema + annotations ----------------------------------------------------------------


def test_schema_accepts_analyze_and_new_args():
    from server.tool_schemas import TOOL_SCHEMAS

    sch = TOOL_SCHEMAS["dax_intelligence"]["properties"]
    assert "analyze" in sch["operation"]["enum"]
    for key in (
        "measure_name",
        "html",
        "auto_open",
        "min_severity",
        "rule_ids",
        "tables",
        "max_findings",
        "suppress_rules",
        "inline",
    ):
        assert key in sch


def test_tool_is_read_only_and_not_destructive():
    # Integration decision (wave-1 merge): 05 never writes the model; its file outputs
    # follow the 04_Run_DAX export_path convention, so readOnlyHint stays True and
    # agrees with server.op_classification (all 05 ops = read).
    registry = MagicMock()
    dh.register_dax_handlers(registry)
    tool = registry.register.call_args[0][0]
    assert tool.annotations["readOnlyHint"] is True
    assert tool.annotations["destructiveHint"] is False


# --- D-10: HTML diagram / browser are opt-in -------------------------------------------------


def test_dependencies_html_and_browser_are_opt_in(monkeypatch):
    import server.handlers._internal.dependencies_helpers as helpers

    cs = MagicMock()
    cs.is_connected.return_value = True
    da = MagicMock()
    da.analyze_dependencies.return_value = {
        "success": True,
        "referenced_measures": [("m", "B")],
        "referenced_columns": [],
        "referenced_tables": [],
    }
    da.find_measure_usage.return_value = {"success": True, "used_by": []}
    da.generate_impact_mermaid.return_value = {"success": True, "mermaid": "graph LR; A-->B"}
    cs.dependency_analyzer = da
    monkeypatch.setattr(helpers, "connection_state", cs)
    html = MagicMock(return_value="C:/x.html")
    sidebar = MagicMock(return_value={})
    monkeypatch.setattr(helpers, "generate_dependency_html", html)
    monkeypatch.setattr(helpers, "_load_sidebar_data", sidebar)

    res = helpers.handle_analyze_measure_dependencies({"table": "m", "measure": "A"})
    assert res["success"] is True
    html.assert_not_called()
    sidebar.assert_not_called()

    helpers.handle_analyze_measure_dependencies({"table": "m", "measure": "A", "html": True})
    assert html.call_args.kwargs["auto_open"] is False


# --- D-22: reference index goes stale after live edits -------------------------------------------


def test_reference_index_rebuilds_after_ttl(monkeypatch):
    from core.model import dependency_analyzer as da_mod

    qe = MagicMock()
    qe.execute_info_query.side_effect = lambda view: {
        "success": True,
        "rows": [{"Table": "m", "Name": "New"}] if view == "MEASURES" else [],
    }
    analyzer = da_mod.DependencyAnalyzer(qe)
    analyzer._ref_index_ttl = 0.0
    first = analyzer._parse_dax_cached("X", "[New]")
    assert first["measures"] == [("m", "New")]
    qe.execute_info_query.side_effect = lambda view: {
        "success": True,
        "rows": [{"Table": "k", "Name": "New"}] if view == "MEASURES" else [],
    }
    second = analyzer._parse_dax_cached("X", "[New]")
    assert second["measures"] == [("k", "New")]
