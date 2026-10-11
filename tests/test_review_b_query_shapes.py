"""2026-09-22 review, slice B — query shapes, RLS args, metadata names (B-11, B-13, B-14,
B-16, B-17, B-18, B-21, B-23, B-25, B-26, J-03a, K-08, L-03).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.orchestration.query_policy import (
    _apply_topn_wrapper,
    _should_apply_topn,
    strip_leading_comments,
)

# --- B-11: TOPN wrapper ----------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "EVALUATE 'Sales' ORDER BY 'Sales'[Date] DESC",
        "EVALUATE 'A'\nEVALUATE 'B'",
        "EVALUATE 'Sales' ORDER BY [x] START AT 5",
        "// top customers\nEVALUATE TOPN(5,'C')",
        "DEFINE VAR x = 1 EVALUATE {x}",
        "/* c */ DEFINE MEASURE T[m] = 1 EVALUATE {[m]}",
    ],
)
def test_topn_wrapper_leaves_multi_statement_ordered_and_define_queries_alone(query):
    assert _should_apply_topn(query) is False


def test_order_by_inside_a_string_or_parens_does_not_block_wrapping():
    q = "EVALUATE FILTER('T', [Label] = \"ORDER BY\")"
    assert _should_apply_topn(q) is True
    assert _apply_topn_wrapper(q, 10).startswith("EVALUATE TOPN(10, FILTER(")


def test_comment_prefixed_single_statement_is_wrapped_after_the_comment():
    q = "-- preview\nEVALUATE 'Sales'"
    assert _should_apply_topn(q) is True
    assert _apply_topn_wrapper(q, 100) == "EVALUATE TOPN(100, 'Sales')"


def test_strip_leading_comments():
    assert strip_leading_comments("  // a\n/* b */ -- c\nEVALUATE x") == "EVALUATE x"


def test_executor_prepare_ignores_leading_comment(monkeypatch):
    from core.infrastructure.query_executor import OptimizedQueryExecutor

    qe = OptimizedQueryExecutor.__new__(OptimizedQueryExecutor)
    q = "// note\nEVALUATE TOPN(5, 'C')"
    assert qe._prepare_dax_query(q, 100) == q
    assert qe._prepare_dax_query("SELECT * FROM $SYSTEM.X", 0) == "SELECT * FROM $SYSTEM.X"


# --- B-13 / B-14: RLS args ---------------------------------------------------


def test_run_dax_roles_are_honoured_not_silently_ignored(monkeypatch):
    import server.handlers.query_handler as qh
    from core.infrastructure import role_impersonation as ri

    seen = {}
    monkeypatch.setattr(qh.connection_state, "is_connected", lambda: True)

    def fake_run_as_role(**kw):
        seen.update(kw)
        return {"success": True, "rows": [{"v": 1}], "impersonated": {"roles": kw["roles"]}}

    monkeypatch.setattr(ri, "run_as_role", fake_run_as_role)
    res = qh.handle_run_dax({"query": "EVALUATE T", "roles": ["EU"], "custom_data": "c1"})
    assert res["success"] is True
    assert seen["roles"] == ["EU"] and seen["custom_data"] == "c1"
    assert res["cache"]["hit"] is False


def test_run_dax_roles_with_trace_is_rejected_loudly(monkeypatch):
    import server.handlers.query_handler as qh

    monkeypatch.setattr(qh.connection_state, "is_connected", lambda: True)
    res = qh.handle_run_dax({"query": "EVALUATE T", "roles": ["EU"], "mode": "trace"})
    assert res["success"] is False and res["error_type"] == "unsupported_arg"


def test_effective_user_without_roles_is_rejected(monkeypatch):
    import server.handlers.query_handler as qh

    monkeypatch.setattr(qh.connection_state, "is_connected", lambda: True)
    res = qh.handle_run_dax({"query": "EVALUATE T", "effective_user_name": "a@b.com"})
    assert res["success"] is False and res["error_type"] == "invalid_args"


def test_connection_string_injection_is_quoted_or_rejected():
    from core.infrastructure.role_impersonation import (
        ImpersonationArgError,
        _build_impersonation_conn_str,
    )

    base = "Data Source=localhost:50001"
    s = _build_impersonation_conn_str(base, ["Sales;Data Source=localhost:50002"])
    assert s == base + ';Roles="Sales;Data Source=localhost:50002"'
    with pytest.raises(ImpersonationArgError):
        _build_impersonation_conn_str(base, ["EU, Sales"])
    with pytest.raises(ImpersonationArgError):
        _build_impersonation_conn_str(base, ["R"], "a@b.com;Roles=Admin")
    s = _build_impersonation_conn_str(base, ["R"], custom_data='x"y')
    assert s.endswith(';CustomData="x""y"')
    assert _build_impersonation_conn_str(base, ["A", "B"]).endswith("Roles=A,B")


def test_impersonation_failures_are_classified(monkeypatch):
    from core.infrastructure import role_impersonation as ri
    from core.infrastructure.connection_state import connection_state

    class _Conn:
        def __init__(self, s):
            pass

        def Open(self):
            raise RuntimeError("A connection cannot be made")

        def Close(self):
            pass

    class _Mgr:
        connection_string = "Data Source=localhost:1"

    monkeypatch.setattr(ri, "_load_adomd", lambda: (_Conn, None))
    monkeypatch.setattr(connection_state, "is_connected", lambda: True)
    monkeypatch.setattr(connection_state, "connection_manager", _Mgr(), raising=False)
    res = ri.run_as_role("EVALUATE {1}", roles=["R"])
    assert res["error_type"] == "connection_error"
    res = ri.run_as_role("EVALUATE {1}", roles=["R,S"])
    assert res["error_type"] == "invalid_args"


# --- B-16: relationship cardinality -----------------------------------------


def test_relationship_check_reads_from_to_cardinality():
    from core.performance.performance_optimizer import PerformanceOptimizer

    ex = MagicMock()

    def info(fn, **kw):
        if fn == "TABLES":
            return {"success": True, "rows": [{"ID": 1, "Name": "Fact"}, {"ID": 2, "Name": "Dim"}]}
        if fn == "COLUMNS":
            return {
                "success": True,
                "rows": [{"ID": 10, "ExplicitName": "Key"}, {"ID": 20, "ExplicitName": "Key"}],
            }
        # INFO.RELATIONSHIPS shape: IDs + From/ToCardinality, NO Cardinality column.
        return {
            "success": True,
            "rows": [
                {
                    "FromTableID": 1,
                    "FromColumnID": 10,
                    "ToTableID": 2,
                    "ToColumnID": 20,
                    "FromCardinality": 2,
                    "ToCardinality": 1,
                }
            ],
        }

    ex.execute_info_query.side_effect = info
    ex.validate_and_execute_dax.return_value = {
        "success": True,
        "rows": [{"[TotalRows]": 10, "[UniqueValues]": 8}],
    }
    out = PerformanceOptimizer(ex).analyze_relationship_cardinality()
    assert out["success"] is True
    assert out["issues_found"] == 1, "duplicate keys on the One side must be reported"
    assert out["issues"][0]["configured_cardinality"] == "Many-to-One"
    query = ex.validate_and_execute_dax.call_args.args[0]
    assert "'Dim'[Key]" in query
    assert ex.validate_and_execute_dax.call_args.kwargs.get("bypass_cache") is True


# --- B-17 / B-18: INFO.* names + string escaping ------------------------------


class _RecordingExecutor:
    """Real OptimizedQueryExecutor query builders over a recorded fake runner."""

    def __init__(self, responses):
        from core.infrastructure.query_executor import OptimizedQueryExecutor

        qe = OptimizedQueryExecutor.__new__(OptimizedQueryExecutor)
        qe._table_id_by_name = {"Sales": 7}
        qe._table_name_by_id = {7: "Sales"}
        qe._column_name_by_id = {}
        qe._connection_state = None
        self.queries = []

        def fake(query, top_n=0, bypass_cache=False, **kw):
            self.queries.append(query)
            for needle, resp in responses.items():
                if needle in query:
                    return resp
            return {"success": True, "rows": []}

        qe.validate_and_execute_dax = fake
        self.qe = qe


def test_search_objects_columns_uses_explicit_name_and_reports_failures():
    rec = _RecordingExecutor(
        {
            "INFO.COLUMNS": {
                "success": True,
                "rows": [{"type": "column", "Name": "Amount", "TableID": 7}],
            },
            "INFO.MEASURES": {"success": False, "error": "boom"},
        }
    )
    out = rec.qe.search_objects_dax("Amou*", ["columns", "measures"])
    col_query = next(q for q in rec.queries if "INFO.COLUMNS" in q)
    assert "[ExplicitName]" in col_query and "[DataType]" not in col_query.split("INFO.COLUMNS")[0]
    assert out["rows"][0]["Table"] == "Sales"
    assert out["partial"] is True and out["errors"][0]["type"] == "measures"


def test_string_literal_escaping_only_doubles_double_quotes():
    rec = _RecordingExecutor({})
    assert rec.qe._escape_dax_string('Client\'s "Value"') == 'Client\'s ""Value""'
    rec.qe.search_measures_dax("Client's")
    assert 'SEARCH("Client\'s"' in rec.queries[-1]


def test_info_columns_rows_get_name_and_datatype_aliases():
    rec = _RecordingExecutor(
        {
            "INFO.COLUMNS": {
                "success": True,
                "rows": [
                    {"[ID]": 1, "[TableID]": 7, "[ExplicitName]": "Amount", "[ExplicitDataType]": 8}
                ],
            }
        }
    )
    out = rec.qe.execute_info_query("COLUMNS")
    row = out["rows"][0]
    assert row["Name"] == "Amount" and row["DataType"] == 8 and row["Table"] == "Sales"


def test_describe_table_defaults_to_the_sequential_id_based_path():
    rec = _RecordingExecutor({})
    out = rec.qe.describe_table("Sales", {})
    assert out["_performance"]["method"] == "sequential"
    assert not any("[TableName]" in q for q in rec.queries)


# --- B-21 --------------------------------------------------------------------


# --- B-23 / A-22 / K-06: rate limits keyed by registered names ---------------


def test_legacy_rate_limit_keys_apply_to_registered_tool_names():
    from core.infrastructure.rate_limiter import RateLimiter

    rl = RateLimiter(
        {
            "global_calls_per_second": 100,
            "global_burst": 100,
            "tool_limits": {"run_dax": 1},
            "tool_bursts": {"run_dax": 3},
        }
    )
    allowed = sum(1 for _ in range(10) if rl.allow_request("04_Run_DAX"))
    assert allowed == 3


def test_rate_limit_profile_supplies_defaults():
    from core.infrastructure.rate_limiter import RATE_LIMIT_PROFILES, RateLimiter

    rl = RateLimiter({"profile": "conservative"})
    assert rl.global_rate == RATE_LIMIT_PROFILES["conservative"]["global_calls_per_second"]
    assert "04_Run_DAX" in rl.tool_limits


def test_default_config_rate_limit_keys_are_registered_tool_names():
    import json
    import os

    from core.infrastructure.dll_paths import PROJECT_ROOT
    from core.config.tool_timeouts import ToolTimeoutManager

    with open(os.path.join(PROJECT_ROOT, "config", "default_config.json"), encoding="utf-8") as fh:
        section = json.load(fh)["rate_limiting"]
    registered = set(ToolTimeoutManager._REGISTERED_TOOL_TIMEOUTS)
    for key in list(section["tool_limits"]) + list(section["tool_bursts"]):
        assert key in registered, key
    assert section["global_burst"] >= 25, "an agent's 25-call fan-out must not be throttled"


# --- B-25: data sources / m expressions -------------------------------------


def test_data_sources_fall_back_to_tom_when_info_is_empty(monkeypatch):
    import server.handlers.query_handler as qh

    qe = MagicMock()
    qe.execute_info_query.return_value = {"success": True, "rows": []}
    qe.list_data_sources_tom.return_value = {"success": True, "rows": [{"Name": "SQL"}]}
    monkeypatch.setattr(qh.connection_state, "is_connected", lambda: True)
    monkeypatch.setattr(qh.connection_state, "query_executor", qe, raising=False)
    out = qh.handle_get_data_sources({})
    assert out["rows"] == [{"Name": "SQL"}] and out["fallback_from"] == "INFO.DATASOURCES"


def test_m_expressions_include_shared_expressions(monkeypatch):
    import server.handlers.query_handler as qh

    qe = MagicMock()
    qe.execute_info_query.side_effect = lambda fn, **kw: {
        "success": True,
        "rows": [{"Name": "p1"}] if fn == "PARTITIONS" else [{"Name": "Param"}],
    }
    monkeypatch.setattr(qh.connection_state, "is_connected", lambda: True)
    monkeypatch.setattr(qh.connection_state, "query_executor", qe, raising=False)
    out = qh.handle_get_m_expressions({})
    assert [r["kind"] for r in out["rows"]] == ["partition", "shared_expression"]


# --- J-03(a) / K-08(b) -------------------------------------------------------


def test_live_model_facade_sees_columns(monkeypatch):
    from core.infrastructure import live_model_facade as lmf
    from core.infrastructure.connection_state import connection_state

    qe = MagicMock()

    def info(fn):
        if fn == "TABLES":
            return {"success": True, "rows": [{"ID": 7, "Name": "Sales"}]}
        if fn == "COLUMNS":
            return {
                "success": True,
                "rows": [
                    {"ExplicitName": "Amount", "TableID": 7, "Table": "7", "Type": 1},
                    {"ExplicitName": "RowNumber-2662979B", "TableID": 7, "Type": 3},
                ],
            }
        return {"success": True, "rows": []}

    qe.execute_info_query.side_effect = info
    monkeypatch.setattr(connection_state, "is_connected", lambda: True)
    monkeypatch.setattr(connection_state, "query_executor", qe, raising=False)
    model = lmf.build_live_model()
    sales = next(t for t in model.tables if t.name == "Sales")
    assert [c.name for c in sales.columns] == ["Amount"]


def test_filter_to_dax_loads_column_types_from_info_columns_shape():
    from core.debug.filter_to_dax import FilterToDaxConverter

    conv = FilterToDaxConverter()
    qe = MagicMock()
    qe.execute_info_query.return_value = {
        "success": True,
        "rows": [
            {"Table": "Sales", "ExplicitName": "Amount", "ExplicitDataType": 10, "Type": 1},
            {"Table": "Sales", "ExplicitName": "Name", "ExplicitDataType": 2, "Type": 1},
            {"Table": "Sales", "ExplicitName": "RowNumber-X", "ExplicitDataType": 6, "Type": 3},
        ],
    }
    assert conv.load_column_types_from_model(qe) == 2
    assert conv.get_column_type("Sales", "Amount") == "decimal"
    assert conv.get_column_type("Sales", "Name") == "string"


# --- B-26 / K-13 / B-31: dead surfaces removed --------------------------------


def test_dead_orchestration_surface_is_gone():
    from core.orchestration.agent_policy import AgentPolicy
    from core.orchestration.query_orchestrator import QueryOrchestrator

    for name in (
        "optimize_variants",
        "decide_and_run",
        "execute_intent",
        "plan_query",
        "auto_analyze_or_preview",
    ):
        assert not hasattr(QueryOrchestrator, name), name
        assert not hasattr(AgentPolicy, name), name
    with pytest.raises(ImportError):
        import core.orchestration.cache_orchestrator  # noqa: F401
    with pytest.raises(ImportError):
        import core.infrastructure.cache_manager  # noqa: F401


def test_agent_policy_lazy_properties_are_singletons_under_threads():
    import threading

    from core.orchestration.agent_policy import AgentPolicy

    ap = AgentPolicy(MagicMock(get=lambda k, d=None: d))
    seen = []
    threads = [threading.Thread(target=lambda: seen.append(ap.query_orch)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len({id(x) for x in seen}) == 1


# --- UDF read path via DMV (interface for WP5) --------------------------------


def test_list_udfs_reads_tmschema_functions_with_info_fallback(monkeypatch):
    from tests.fake_adomd import FakeServer, make_state

    server = FakeServer()
    server.respond("TMSCHEMA_FUNCTIONS", [{"ID": 1, "Name": "fnTax", "Expression": "(x) => x"}])
    state, _cm, _ = make_state(monkeypatch, {"Data Source=localhost:50009": server})
    out = state.query_executor.list_udfs()
    assert out["success"] is True and out["source"] == "TMSCHEMA_FUNCTIONS"
    assert out["rows"][0]["Name"] == "fnTax"

    server.respond("TMSCHEMA_FUNCTIONS", RuntimeError("not on this build"))
    server.respond("INFO.USERDEFINEDFUNCTIONS", [{"[Name]": "fnTax"}])
    out = state.query_executor.list_udfs()
    assert out["source"] == "INFO.USERDEFINEDFUNCTIONS" and out["rows"] == [{"Name": "fnTax"}]
