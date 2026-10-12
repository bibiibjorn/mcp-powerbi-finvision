"""WP-FT: the 09_Validate / 04_Query_Operations dispatch shells for tieout, test_generate,
equivalence and rls_audit (engines are tested in their own files)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import server.handlers._debug.tools as tools
from server.op_classification import classify


def _cs(connected=True, executor=None):
    cs = MagicMock()
    cs.is_connected.return_value = connected
    cs.query_executor = executor
    return cs


def _executor(fn):
    qe = MagicMock()
    qe.validate_and_execute_dax.side_effect = lambda q, *a, **k: fn(q)
    return qe


@pytest.fixture
def allow_tmp(monkeypatch, tmp_path):
    from core.validation import path_guard

    monkeypatch.setattr(path_guard, "_resolved_root_list", lambda **_k: [tmp_path.resolve()])
    return tmp_path


# ---------------------------------------------------------------- classification
@pytest.mark.parametrize(
    "tool,args,level",
    [
        ("09_Validate", {"operation": "tieout"}, "read"),
        ("09_Validate", {"operation": "equivalence"}, "read"),
        ("09_Validate", {"operation": "test_generate", "source": "page"}, "read"),
        ("09_Validate", {"operation": "test_generate", "write": False}, "read"),
        ("09_Validate", {"operation": "test_generate", "write": True}, "write"),
        ("09_Validate", {"operation": "test_put"}, "write"),
        ("04_Query_Operations", {"operation": "rls_audit"}, "read"),
    ],
)
def test_op_classification(tool, args, level):
    assert classify(tool, args) == level


def test_read_only_mode_blocks_only_the_write_form(monkeypatch):
    from server.dispatch import ToolDispatcher

    d = ToolDispatcher()
    monkeypatch.setattr(d, "_read_only", True)
    blocked = d._security_gate(
        "09_Validate", {"operation": "test_generate", "write": True}, "write", "x"
    )
    assert blocked is not None and blocked["error_type"] == "read_only_mode"


# ---------------------------------------------------------------- tieout
def test_tieout_a_vs_b_through_the_handler(monkeypatch):
    qe = _executor(lambda q: {"success": True, "rows": [{"[a]": 1.0, "[b]": 1.0}]})
    monkeypatch.setattr(tools, "connection_state", _cs(executor=qe))
    res = tools.handle_validate(
        {"operation": "tieout", "mode": "A_vs_B", "measure_a": "A", "measure_b": "B"}
    )
    assert res["success"] and res["ties"] is True and res["mode"] == "A_vs_B"


def test_tieout_without_engine_is_a_clean_error(monkeypatch):
    monkeypatch.setattr(tools, "connection_state", _cs(connected=False))
    res = tools.handle_validate({"operation": "tieout", "page_name": "P", "visual_id": "v"})
    assert res["success"] is False and res["error_type"] == "not_connected"


def test_tieout_visual_without_pbip_path_reports_it(monkeypatch):
    qe = _executor(lambda q: {"success": True, "rows": []})
    monkeypatch.setattr(tools, "connection_state", _cs(executor=qe))
    monkeypatch.setattr(tools, "_validate_builder", lambda: (None, "PBIP folder not available"))
    res = tools.handle_validate({"operation": "tieout", "page_name": "P", "visual_id": "v"})
    assert res["success"] is False and "PBIP" in res["error"]


def test_offline_lane_names_the_missing_engine(monkeypatch):
    from core.infrastructure import offline_model

    monkeypatch.setattr(tools, "connection_state", _cs(connected=False))
    monkeypatch.setattr(offline_model, "is_active", lambda: True)
    res = tools.handle_validate(
        {"operation": "tieout", "mode": "A_vs_B", "measure_a": "A", "measure_b": "B"}
    )
    assert res["error_type"] == "no_dax_engine"


# ---------------------------------------------------------------- test_generate
class _Builder:
    def list_pages(self):
        return [{"id": "pg1", "name": "Overview"}]

    def list_visuals(self, page):
        return [
            {
                "id": "vc",
                "type": "card",
                "title": "KPI",
                "measures": ["Revenue"],
                "is_data_visual": True,
            }
        ]

    def list_slicers(self, page):
        return []

    def build_visual_query(self, page, vid=None, **k):
        return SimpleNamespace(
            dax_query='EVALUATE ROW("vc", 1)',
            fidelity="full",
            fidelity_notes=[],
            filter_breakdown={},
        )


def _gen_engine(q):
    if q.startswith('EVALUATE ROW("v"'):
        return {"success": True, "rows": [{"[v]": 42.0}]}
    return {
        "success": True,
        "rows": [{"[Revenue]": 42.0}],
        "columns": ["[Revenue]"],
        "row_count": 1,
    }


def test_test_generate_previews_then_writes_then_is_idempotent(monkeypatch, allow_tmp):
    monkeypatch.setattr(tools, "connection_state", _cs(executor=_executor(_gen_engine)))
    monkeypatch.setattr(tools, "_validate_builder", lambda: (_Builder(), None))
    path = str(allow_tmp / "gen.suite.json")
    base = {
        "operation": "test_generate",
        "source": "page",
        "page_name": "Overview",
        "suite_path": path,
    }

    prev = tools.handle_validate(base)
    assert (
        prev["success"] and prev["preview"] is True and not (allow_tmp / "gen.suite.json").exists()
    )
    assert {t["id"] for t in prev["tests"]} == {"snap.pg1.vc", "kpi.Revenue"}
    assert prev["diff"]["added"] and "write_hint" in prev

    wrote = tools.handle_validate({**base, "write": True})
    assert wrote["success"] and wrote["preview"] is False and len(wrote["written"]["added"]) == 2
    suite = json.loads((allow_tmp / "gen.suite.json").read_text(encoding="utf-8"))
    assert {t["id"] for t in suite["tests"]} == {"snap.pg1.vc", "kpi.Revenue"}
    assert all(t["generated"] and t["provenance"] == "snapshot" for t in suite["tests"])

    again = tools.handle_validate({**base, "write": True})
    assert again["written"]["added"] == [] and again["written"]["test_count"] == 2
    assert again["diff"]["unchanged"] and not again["diff"]["changed"]


def test_test_generate_validation(monkeypatch, allow_tmp):
    monkeypatch.setattr(tools, "connection_state", _cs(executor=_executor(_gen_engine)))
    assert tools.handle_validate({"operation": "test_generate"})["error_type"] == "missing_argument"
    res = tools.handle_validate({"operation": "test_generate", "source": "measures", "write": True})
    assert res["error_type"] == "missing_argument" and "suite_path" in res["error"]
    assert not list(allow_tmp.iterdir())


def test_test_generate_roles_source_reads_the_model_roles(monkeypatch):
    cs = _cs(executor=_executor(_gen_engine))
    cs.rls_manager.list_roles.return_value = {
        "success": True,
        "roles": [{"name": "A"}, {"name": "B"}],
    }
    monkeypatch.setattr(tools, "connection_state", cs)
    res = tools.handle_validate(
        {"operation": "test_generate", "source": "roles", "as_user": "u@x.com"}
    )
    assert [t["id"] for t in res["tests"]] == ["rls.A", "rls.B"]


# ---------------------------------------------------------------- equivalence
def test_equivalence_handler_never_writes_and_needs_expressions(monkeypatch):
    seen = []

    def engine(q):
        seen.append(q)
        return {"success": True, "rows": [{"[a]": 5.0, "[b]": 5.0}]}

    monkeypatch.setattr(tools, "connection_state", _cs(executor=_executor(engine)))
    res = tools.handle_validate(
        {
            "operation": "equivalence",
            "expression_a": "SUM(T[x])",
            "expression_b": "SUMX(T, T[x])",
            "host_table": "T",
        }
    )
    assert res["success"] and res["equivalent"] is True and res["model_written"] is False
    assert all(q.lstrip().startswith("DEFINE") for q in seen)
    bad = tools.handle_validate({"operation": "equivalence", "expression_a": "1"})
    assert bad["error_type"] == "missing_argument"


def test_equivalence_resolves_a_host_table_when_none_given(monkeypatch):
    monkeypatch.setattr(
        tools,
        "connection_state",
        _cs(executor=_executor(lambda q: {"success": True, "rows": [{"[a]": 1, "[b]": 1}]})),
    )
    monkeypatch.setattr(tools, "_resolve_host_table", lambda: "Sales")
    res = tools.handle_validate(
        {"operation": "equivalence", "expression_a": "1", "expression_b": "1"}
    )
    assert res["success"] is True
    monkeypatch.setattr(tools, "_resolve_host_table", lambda: None)
    assert (
        tools.handle_validate(
            {"operation": "equivalence", "expression_a": "1", "expression_b": "1"}
        )["error_type"]
        == "missing_argument"
    )


# ---------------------------------------------------------------- suite runner wiring
def test_test_run_executes_generated_style_tests_through_the_engines(monkeypatch, allow_tmp):
    from core.validation import test_suites as ts

    path = str(allow_tmp / "s.json")
    ts.put_test(
        path, {"id": "ab", "type": "tieout", "mode": "A_vs_B", "measure_a": "A", "measure_b": "B"}
    )
    monkeypatch.setattr(
        tools,
        "connection_state",
        _cs(executor=_executor(lambda q: {"success": True, "rows": [{"[a]": 1, "[b]": 2}]})),
    )
    res = tools.handle_validate({"operation": "test_run", "suite_path": path, "record": False})
    assert res["exit_code"] == 1 and res["results"][0]["status"] == "fail"


# ---------------------------------------------------------------- 04_Query_Operations rls_audit
def test_query_handler_dispatches_rls_audit(monkeypatch):
    import server.handlers.query_handler as qh
    from core.validation import rls_audit

    monkeypatch.setattr(
        rls_audit, "audit_from_connection", lambda args: {"success": True, "echo": args}
    )
    monkeypatch.setattr(qh.connection_state, "is_connected", lambda: True, raising=False)
    res = qh._dispatch_query_operation(
        "rls_audit", {"operation": "rls_audit", "as_user": "u@x.com"}
    )
    assert res["success"] and res["echo"]["as_user"] == "u@x.com"


def test_rls_audit_registered_in_schema_and_surface():
    from server.tool_schemas import TOOL_SCHEMAS
    from server.tool_surface import deferred_args

    assert "rls_audit" in TOOL_SCHEMAS["query_operations"]["properties"]["operation"]["enum"]
    assert {"tieout", "test_generate", "equivalence"} <= set(
        TOOL_SCHEMAS["validate_operations"]["properties"]["operation"]["enum"]
    )
    assert "probe_identities" in TOOL_SCHEMAS["query_operations"]["properties"]
    assert "probe_identities" in deferred_args("04_Query_Operations")
    assert {"source", "write", "expression_a", "grid_columns"} <= deferred_args("09_Validate")
