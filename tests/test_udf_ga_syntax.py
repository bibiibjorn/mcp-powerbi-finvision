"""DAX UDF GA (CL 1702) — verbatim round-trip + the PY_UDF_* lint rules.

GA UDF syntax carries four things our CRUD must not touch:
  - typed parameters (scalar subtypes, TABLE, reference types),
  - parameter passing modes VAL (eager) / EXPR (lazy),
  - optional parameters with a default expression,
  - ``///`` doc comments (they feed IntelliSense — stripping them is data loss).

TOM is mocked at the manager boundary; nothing here touches AMO/CLR.
"""

import pytest

from core.dax.analyzer.unified_analyzer import DaxUnifiedAnalyzer
from core.operations.function_crud_manager import FunctionCrudManager

# The canonical GA-syntax body used for the round-trip assertions. Kept as one
# exact string so any normalization (whitespace, comment stripping, case) shows
# up as an equality failure.
GA_UDF_EXPRESSION = """\
/// Applies a margin to a sales amount.
/// <param name="sales">The sales amount (eagerly evaluated).</param>
/// <param name="rate">Optional margin rate; defaults to 0.1.</param>
/// <param name="scope">A lazily evaluated filter expression.</param>
FUNCTION Sales.AddMargin =
    (sales: NUMERIC VAL, rate: NUMERIC VAL = 0.1, scope: ANYREF EXPR) =>
    VAR Base = sales * (1 + rate)
    RETURN
        CALCULATE(Base, scope)"""


# ---- Fakes ---------------------------------------------------------------
# FG-11: UDF reads come from the DMV and writes are TMSL; the fake stands in for
# that server (rows + a createOrReplace interpreter).


class _FakeUdfServer:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.commands = []

    def udf_rows(self):
        return [dict(r) for r in self.rows], None

    def execute(self, command):
        self.commands.append(command)
        body = command["createOrReplace"]["function"]
        self.rows = [r for r in self.rows if r["Name"] != body["name"]]
        self.rows.append(
            {
                "Name": body["name"],
                "Expression": body["expression"],
                "Description": body.get("description", ""),
                "LineageTag": body.get("lineageTag"),
            }
        )
        return {"success": True}


@pytest.fixture
def udf_server(monkeypatch):
    srv = _FakeUdfServer()
    mgr = FunctionCrudManager()
    monkeypatch.setattr(mgr, "_udf_rows", srv.udf_rows)
    monkeypatch.setattr(mgr, "_execute_tmsl", srv.execute)
    return srv, mgr


# ---- Verbatim round-trip -------------------------------------------------


def test_create_function_stores_ga_syntax_verbatim(udf_server):
    """create -> the TMSL body and the read-back must be byte-identical."""
    srv, mgr = udf_server
    res = mgr.create_function(
        "Sales.AddMargin", expression=GA_UDF_EXPRESSION, description="Margin helper"
    )
    assert res["success"] is True
    assert res["verified"] is True
    assert srv.commands[-1]["createOrReplace"]["function"]["expression"] == GA_UDF_EXPRESSION
    assert res["function"]["expression"] == GA_UDF_EXPRESSION
    assert len(srv.commands) == 1


def test_round_trip_preserves_every_ga_construct(udf_server):
    """Explicitly assert each GA construct survives (not just string equality)."""
    _srv, mgr = udf_server
    mgr.create_function("Sales.AddMargin", expression=GA_UDF_EXPRESSION)

    got = mgr.get_function("Sales.AddMargin")["function"]["expression"]

    # /// doc comments retained (all three param lines + the summary)
    assert got.count("///") == 4
    assert '<param name="sales">' in got
    # typed parameters
    assert "sales: NUMERIC VAL" in got
    assert "scope: ANYREF EXPR" in got
    # passing modes
    assert " VAL" in got and " EXPR" in got
    # optional parameter with default expression
    assert "rate: NUMERIC VAL = 0.1" in got
    # no leading/trailing whitespace mangling
    assert got == GA_UDF_EXPRESSION


def test_update_function_stores_ga_syntax_verbatim(udf_server):
    srv, mgr = udf_server
    srv.rows.append({"Name": "Sales.AddMargin", "Expression": "(x) => x", "LineageTag": "lt"})

    res = mgr.update_function("Sales.AddMargin", expression=GA_UDF_EXPRESSION)

    assert res["success"] is True
    assert srv.commands[-1]["createOrReplace"]["function"]["expression"] == GA_UDF_EXPRESSION
    assert res["function"]["expression"] == GA_UDF_EXPRESSION


def test_list_functions_returns_expression_verbatim(udf_server):
    srv, mgr = udf_server
    srv.rows.append({"Name": "Sales.AddMargin", "Expression": GA_UDF_EXPRESSION})

    res = mgr.list_functions(include=["expression"])
    assert res["functions"][0]["expression"] == GA_UDF_EXPRESSION


# ---- Lint rules ----------------------------------------------------------


def _udf_rule_ids(expression: str):
    result = DaxUnifiedAnalyzer().analyze(expression)
    return [i.rule_id for i in result.issues if i.rule_id.startswith("PY_UDF")]


def test_rules_silent_on_well_formed_ga_udf():
    assert _udf_rule_ids(GA_UDF_EXPRESSION) == []


def test_untyped_parameter_rule_fires():
    bad = "FUNCTION Margin = (sales, rate: NUMERIC VAL) => sales * rate"
    assert "PY_UDF_UNTYPED_PARAMETER" in _udf_rule_ids(bad)


def test_untyped_parameter_rule_names_the_offending_parameters():
    bad = "FUNCTION Margin = (sales, rate: NUMERIC VAL, factor) => sales * rate * factor"
    issues = [
        i
        for i in DaxUnifiedAnalyzer().analyze(bad).issues
        if i.rule_id == "PY_UDF_UNTYPED_PARAMETER"
    ]
    assert len(issues) == 1
    assert issues[0].match_text == "sales, factor"
    assert issues[0].location == "Margin"
    assert issues[0].severity == "low"  # 2026-10-03 D-11: AnyVal is the documented default (style)


def test_untyped_parameter_rule_handles_namespaced_udf_name():
    """A namespaced name tokenizes as IDENTIFIER DOT IDENTIFIER — must still match."""
    bad = "FUNCTION Sales.AddMargin = (sales, r: INT64 VAL) => sales * r"
    issues = [
        i
        for i in DaxUnifiedAnalyzer().analyze(bad).issues
        if i.rule_id == "PY_UDF_UNTYPED_PARAMETER"
    ]
    assert len(issues) == 1
    assert issues[0].location == "Sales.AddMargin"


def test_untyped_parameter_rule_silent_when_all_typed():
    good = "FUNCTION F = (t: TABLE, e: ANYREF EXPR, n: INT64 VAL) => COUNTROWS(t)"
    assert "PY_UDF_UNTYPED_PARAMETER" not in _udf_rule_ids(good)


def test_eager_eval_rule_fires_on_val_used_as_calculate_filter():
    bad = "FUNCTION F = (m: NUMERIC VAL, flt: NUMERIC VAL) => CALCULATE(m, flt)"
    assert "PY_UDF_EAGER_EVAL_RISK" in _udf_rule_ids(bad)


def test_eager_eval_rule_fires_for_calculatetable():
    bad = "FUNCTION F = (t: TABLE, flt: NUMERIC VAL) => CALCULATETABLE(t, flt)"
    assert "PY_UDF_EAGER_EVAL_RISK" in _udf_rule_ids(bad)


def test_eager_eval_rule_silent_when_filter_is_expr():
    good = "FUNCTION F = (m: NUMERIC VAL, flt: ANYREF EXPR) => CALCULATE(m, flt)"
    assert "PY_UDF_EAGER_EVAL_RISK" not in _udf_rule_ids(good)


def test_eager_eval_rule_silent_on_val_as_calculate_first_arg():
    """A pre-computed scalar as the CALCULATE expression is legitimate."""
    ok = "FUNCTION F = (m: NUMERIC VAL) => CALCULATE(m, ALL(Dim))"
    assert "PY_UDF_EAGER_EVAL_RISK" not in _udf_rule_ids(ok)


def test_eager_eval_rule_silent_on_val_inside_iterator():
    """An eager scalar inside SUMX is the normal, correct choice — not a finding."""
    ok = "FUNCTION F = (r: NUMERIC VAL, t: TABLE) => SUMX(t, r * 2)"
    assert "PY_UDF_EAGER_EVAL_RISK" not in _udf_rule_ids(ok)


@pytest.mark.parametrize(
    "measure_dax",
    [
        "CALCULATE(SUM(Sales[Amount]), ALL(Dim))",
        "SUMX(Sales, Sales[Qty] * Sales[Price])",
        "DIVIDE([Total], [Count])",
        "VAR x = 1 RETURN x",
    ],
)
def test_udf_rules_inert_on_ordinary_measures(measure_dax):
    """The rules must never fire on a plain measure body."""
    assert _udf_rule_ids(measure_dax) == []


def test_rules_are_registered():
    from core.dax.analyzer.rules import load_python_rules

    ids = {r.rule_id for r in load_python_rules()}
    assert "PY_UDF_UNTYPED_PARAMETER" in ids
    assert "PY_UDF_EAGER_EVAL_RISK" in ids


# ---- lint_model exercises the UDF rules ---------------------------------


def _patch_connection(monkeypatch, udf_result):
    """Patch dax_context_handler.connection_state (the established lint_model pattern)."""
    from unittest.mock import MagicMock

    import server.handlers.dax_context_handler as dch

    cs = MagicMock()
    cs.is_connected.return_value = True
    qe = MagicMock()
    qe.enumerate_measures_tom.return_value = {
        "success": True,
        "rows": [{"Name": "M1", "Table": "T", "Expression": "SUM(T[A])"}],
        "row_count": 1,
    }
    qe.validate_and_execute_dax.return_value = udf_result
    cs.query_executor = qe
    monkeypatch.setattr(dch, "connection_state", cs)
    return dch, qe


def test_lint_model_includes_udf_findings(monkeypatch):
    """05_DAX_Intelligence lint_model must surface PY_UDF_* findings."""
    dch, qe = _patch_connection(
        monkeypatch,
        {
            "success": True,
            "rows": [
                {
                    "[Name]": "Sales.AddMargin",
                    "[Expression]": (
                        "FUNCTION Sales.AddMargin = (sales, r: INT64 VAL) => sales * r"
                    ),
                }
            ],
        },
    )

    res = dch._handle_lint_model({})
    assert "USERDEFINEDFUNCTIONS" in qe.validate_and_execute_dax.call_args[0][0]
    assert res["success"] is True
    assert res["functions_analyzed"] == 1
    assert res["by_rule"].get("PY_UDF_UNTYPED_PARAMETER") == 1
    udf_finding = next(f for f in res["findings"] if f["rule_id"] == "PY_UDF_UNTYPED_PARAMETER")
    assert udf_finding["measure"] == "Sales.AddMargin"
    assert udf_finding["object_kind"] == "function"


def test_lint_model_tolerates_no_udf_support(monkeypatch):
    """A model/DAX version without UDFs must not break measure linting."""
    dch, _ = _patch_connection(
        monkeypatch,
        {"success": False, "error": "INFO.USERDEFINEDFUNCTIONS has not been recognized"},
    )

    res = dch._handle_lint_model({})
    assert res["success"] is True
    assert res["measures_analyzed"] == 1
    assert res["functions_analyzed"] == 0
