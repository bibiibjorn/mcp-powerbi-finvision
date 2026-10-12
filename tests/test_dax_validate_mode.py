"""PV-01: ``04_Run_DAX mode=validate`` - engine parse/bind without executing.

Offline: the rewrite (``core.dax.dax_validate``) is pure; the executor's ADOMD seam
(``_run_command``) and the handler's ``connection_state`` are faked. What only a live
Desktop can prove (that the engine really binds an unused-branch VAR and reports an unknown
column inside it) is listed in the tool reference (Part III of docs/GUIDE.md) and is NOT asserted here.
"""

from __future__ import annotations

import types

import pytest

import core.infrastructure.query_executor as qx
from core.dax.dax_validate import (
    _line_col,
    build_bind_only_query,
    remap_engine_positions,
)
from core.infrastructure.query_executor import OptimizedQueryExecutor
from server.handlers import query_handler as qh

# ---------------------------------------------------------------------------
# the rewrite
# ---------------------------------------------------------------------------


def test_plain_evaluate_becomes_an_unused_branch_var():
    plan = build_bind_only_query("EVALUATE 'Sales'")
    assert plan.ok and plan.statements == 1 and plan.dropped == []
    assert plan.query.count("EVALUATE") == 1  # only the trivial ROW statement runs
    assert plan.query.startswith("DEFINE\n")
    assert "VAR __mcp_validate_1 = 'Sales'" in plan.query
    assert plan.query.rstrip().endswith(
        'EVALUATE ROW("__mcp_validate", IF(FALSE(), COUNTROWS(__mcp_validate_1), 0))'
    )


def test_caller_define_block_is_kept_and_order_by_is_reported_dropped():
    q = (
        "DEFINE\n"
        "    MEASURE 'Sales'[Margin] = [Sales] - [Cost]\n"
        "EVALUATE\n"
        "SUMMARIZECOLUMNS('Date'[Year], \"m\", [Margin])\n"
        "ORDER BY 'Date'[Year] DESC\n"
    )
    plan = build_bind_only_query(q)
    assert plan.ok
    assert plan.query.count("DEFINE") == 1
    assert "MEASURE 'Sales'[Margin] = [Sales] - [Cost]" in plan.query
    assert "SUMMARIZECOLUMNS('Date'[Year]" in plan.query
    assert "ORDER BY" not in plan.query
    assert plan.dropped and "ORDER BY" in plan.dropped[0]


def test_several_evaluates_each_get_their_own_var():
    plan = build_bind_only_query(
        "EVALUATE 'A'\nEVALUATE 'B' ORDER BY 'B'[x]\nEVALUATE ROW(\"x\", 1)"
    )
    assert plan.ok and plan.statements == 3
    for n in (1, 2, 3):
        assert f"VAR __mcp_validate_{n} =" in plan.query
    assert (
        "COUNTROWS(__mcp_validate_1) + COUNTROWS(__mcp_validate_2) + COUNTROWS(__mcp_validate_3)"
        in plan.query
    )
    assert len(plan.dropped) == 1


def test_keywords_in_comments_strings_and_brackets_are_not_statements():
    q = '// EVALUATE fake\nEVALUATE ROW("a", "EVALUATE ORDER BY", [EVALUATE], \'Order\'[By])'
    plan = build_bind_only_query(q)
    assert plan.ok and plan.statements == 1 and plan.dropped == []
    assert "// EVALUATE fake" in plan.query  # the comment prefix is kept verbatim


def test_evaluate_inside_parentheses_is_not_top_level():
    plan = build_bind_only_query("EVALUATE TOPN(5, 'T', 'T'[c], DESC)")
    assert plan.ok and plan.statements == 1


@pytest.mark.parametrize(
    "query",
    ["", "   ", "SELECT * FROM $SYSTEM.TMSCHEMA_TABLES", "DEFINE MEASURE 'T'[m] = 1", "'Sales'"],
)
def test_queries_without_a_top_level_evaluate_are_refused(query):
    plan = build_bind_only_query(query)
    assert not plan.ok and plan.error


def test_empty_evaluate_body_is_refused():
    plan = build_bind_only_query("EVALUATE\nORDER BY [x]")
    assert not plan.ok and "no expression" in plan.error


def test_engine_positions_are_translated_back_to_the_callers_text():
    original = "DEFINE\n    MEASURE 'T'[m] = 1\nEVALUATE\n  FOO('T'[x])\n"
    plan = build_bind_only_query(original)
    off = plan.query.index("FOO(")
    line, col = _line_col(plan.query, off)
    engine = f"Query ({line}, {col}) Function 'FOO' does not exist."
    mapped = remap_engine_positions(engine, plan)
    o_line, o_col = _line_col(original, original.index("FOO("))
    assert mapped == f"Query ({o_line}, {o_col}) Function 'FOO' does not exist."


def test_positions_in_the_define_prefix_are_identity():
    original = "DEFINE\n    MEASURE 'T'[m] = BAR(1)\nEVALUATE 'T'"
    plan = build_bind_only_query(original)
    off = plan.query.index("BAR(")
    line, col = _line_col(plan.query, off)
    assert remap_engine_positions(f"Query ({line}, {col}) x", plan) == f"Query ({line}, {col}) x"
    assert (line, col) == _line_col(original, original.index("BAR("))


def test_messages_without_a_position_are_returned_unchanged():
    plan = build_bind_only_query("EVALUATE 'T'")
    assert remap_engine_positions("The syntax is incorrect.", plan) == "The syntax is incorrect."


# ---------------------------------------------------------------------------
# the executor
# ---------------------------------------------------------------------------


class AdomdErrorResponseException(Exception):
    """Named like the real CLR exception: the executor keys on the class name."""


@pytest.fixture
def qe(monkeypatch):
    monkeypatch.setattr(qx, "ADOMD_AVAILABLE", True)
    monkeypatch.setattr(OptimizedQueryExecutor, "connection", property(lambda self: object()))
    ex = OptimizedQueryExecutor.__new__(OptimizedQueryExecutor)
    ex.calls = []
    return ex


def _runner(qe, exc=None):
    def run(text, **kw):
        qe.calls.append((text, kw))
        if exc is not None:
            raise exc
        return [], [], 1.0, False, False

    qe._run_command = run


def test_valid_query_runs_only_the_cheap_wrapper(qe):
    _runner(qe)
    out = qe.validate_dax("EVALUATE TOPN(1000000, 'Sales')")
    assert out["success"] is True and out["valid"] is True and out["errors"] == []
    assert out["statements"] == 1 and out["strategy"] == "define_var_dead_branch"
    assert "elapsed_ms" in out
    text, kw = qe.calls[0]
    assert "IF(FALSE()" in text and text.count("EVALUATE") == 1
    assert kw["max_rows"] == 1 and kw["timeout_s"] == 10.0 and kw["purpose"] == "dax_validate"


def test_engine_error_is_a_normal_invalid_result_with_engine_text(qe):
    _runner(qe, AdomdErrorResponseException("Query (1, 1) Failed to resolve name 'Nope'."))
    out = qe.validate_dax("EVALUATE 'Nope'")
    assert out["success"] is True and out["valid"] is False
    assert "Failed to resolve name 'Nope'" in out["errors"][0]


def test_error_positions_point_into_the_callers_query(qe):
    original = "DEFINE\n  MEASURE 'T'[m] = 1\nEVALUATE BAD('T')\n"
    plan = build_bind_only_query(original)
    line, col = _line_col(plan.query, plan.query.index("BAD("))
    assert (line, col) != _line_col(original, original.index("BAD("))
    _runner(qe, AdomdErrorResponseException(f"Query ({line}, {col}) Function 'BAD' does not exist"))
    out = qe.validate_dax(original)
    o_line, o_col = _line_col(original, original.index("BAD("))
    assert out["errors"] == [f"Query ({o_line}, {o_col}) Function 'BAD' does not exist"]
    assert out["raw_errors"] == [f"Query ({line}, {col}) Function 'BAD' does not exist"]


def test_order_by_is_reported_as_not_validated(qe):
    _runner(qe)
    out = qe.validate_dax("EVALUATE 'T' ORDER BY 'T'[x]")
    assert out["valid"] is True and "ORDER BY" in out["not_validated"][0]


def test_timeout_is_unknown_not_valid(qe):
    _runner(qe, AdomdErrorResponseException("The timeout period expired before completion."))
    out = qe.validate_dax("EVALUATE 'Heavy'")
    assert out["success"] is False and out["error_type"] == "timeout"
    assert "unknown" in out["hint"]
    assert "valid" not in out


def test_non_engine_failure_is_never_reported_as_invalid_dax(qe):
    _runner(qe, RuntimeError("boom"))
    out = qe.validate_dax("EVALUATE 'T'")
    assert out["success"] is False and out["error_type"] == "validate_failed"
    assert "valid" not in out


def test_query_without_evaluate_never_reaches_the_engine(qe):
    _runner(qe)
    out = qe.validate_dax("SELECT * FROM $SYSTEM.DISCOVER_SESSIONS")
    assert out["success"] is False and out["error_type"] == "invalid_args"
    assert qe.calls == []


def test_no_connection_is_reported(monkeypatch):
    monkeypatch.setattr(qx, "ADOMD_AVAILABLE", True)
    monkeypatch.setattr(OptimizedQueryExecutor, "connection", property(lambda self: None))
    ex = OptimizedQueryExecutor.__new__(OptimizedQueryExecutor)
    out = ex.validate_dax("EVALUATE 'T'")
    assert out["success"] is False and out["error_type"] == "connection_not_open"


def test_adomd_missing_is_reported(monkeypatch):
    monkeypatch.setattr(qx, "ADOMD_AVAILABLE", False)
    ex = OptimizedQueryExecutor.__new__(OptimizedQueryExecutor)
    assert ex.validate_dax("EVALUATE 'T'")["error_type"] == "adomd_not_available"


def test_validate_never_touches_the_result_cache(qe):
    _runner(qe)

    def boom(*a, **k):  # pragma: no cover - must not be called
        raise AssertionError("validate must not use the executing/caching path")

    qe.validate_and_execute_dax = boom
    qe._cache_set = boom
    assert qe.validate_dax("EVALUATE 'T'")["valid"] is True


# ---------------------------------------------------------------------------
# the handler / schema
# ---------------------------------------------------------------------------


class _State:
    def __init__(self, executor, connected=True):
        self.query_executor = executor
        self.agent_policy = types.SimpleNamespace(
            safe_run_dax=lambda **k: (_ for _ in ()).throw(AssertionError("must not execute"))
        )
        self._connected = connected

    def is_connected(self):
        return self._connected

    def get_connection_identity(self):
        return {"database": "M"}


def test_mode_validate_dispatches_to_the_executor_and_never_executes(monkeypatch):
    seen = {}

    def validate(q):
        seen["q"] = q
        return {"success": True, "valid": True, "errors": [], "elapsed_ms": 3.0}

    monkeypatch.setattr(
        qh, "connection_state", _State(types.SimpleNamespace(validate_dax=validate))
    )
    out = qh.handle_run_dax({"query": "EVALUATE 'Sales'", "mode": "validate", "roles": ["R"]})
    assert out["valid"] is True and out["mode"] == "validate"
    assert out["connection"] == {"database": "M"}
    assert seen["q"] == "EVALUATE 'Sales'"


def test_mode_validate_when_not_connected(monkeypatch):
    monkeypatch.setattr(qh, "connection_state", _State(None, connected=False))
    out = qh.handle_run_dax({"query": "EVALUATE 'Sales'", "mode": "validate"})
    assert out["success"] is False and out["error_type"] == "not_connected"


def test_validate_is_in_the_advertised_mode_enum():
    from server.handlers import register_all_handlers
    from server.registry import HandlerRegistry

    reg = HandlerRegistry()
    register_all_handlers(reg)
    enum = reg.get_emitted_schema("04_Run_DAX")["properties"]["mode"]["enum"]
    assert "validate" in enum and "validate" in qh.RUN_DAX_MODES
