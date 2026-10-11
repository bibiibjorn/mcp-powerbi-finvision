"""Tests for server.handlers.query_handler — the dispatch shell.

Backs MCP tools 04_Run_DAX (handle_run_dax) and 04_Query_Operations
(handle_query_operations).

The probe internals (run_probe / list_probes) already have a dedicated test
file (tests/test_dmv_probes.py). This file pins the *dispatcher's* contract:

  - 04_Run_DAX: not-connected envelope, missing/empty query error, mode routing
    (trace mode delegates to the native trace runner; normal mode delegates to
    agent_policy.safe_run_dax).
  - 04_Query_Operations: list_dmv_probes works WITHOUT a live connection
    (gated *before* is_connected); every other op returns the structured
    not_connected envelope when disconnected; unknown op lists valid operations;
    dmv_probe missing-probe and routing.

All boundaries are mocked — no live ADOMD/AMO/network.

Mock strategy: the handler imports the module-level singleton
`connection_state` at module top (`from ...connection_state import
connection_state`). We patch attributes on that same object via
`qh.connection_state` so the handler sees our fakes.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from server.handlers import query_handler as qh

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _gate_off(monkeypatch):
    """Query ops are read-only, but keep the gate off for determinism."""
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "OFF")


@pytest.fixture
def disconnected(monkeypatch):
    """connection_state.is_connected() -> False."""
    monkeypatch.setattr(qh.connection_state, "is_connected", lambda: False, raising=False)


@pytest.fixture
def connected(monkeypatch):
    """connection_state.is_connected() -> True, with mockable executor + policy."""
    monkeypatch.setattr(qh.connection_state, "is_connected", lambda: True, raising=False)
    qe = MagicMock(name="query_executor")
    policy = MagicMock(name="agent_policy")
    monkeypatch.setattr(qh.connection_state, "query_executor", qe, raising=False)
    monkeypatch.setattr(qh.connection_state, "agent_policy", policy, raising=False)
    return {"qe": qe, "policy": policy}


# ---------------------------------------------------------------------------
# 04_Run_DAX — handle_run_dax
# ---------------------------------------------------------------------------


def test_run_dax_not_connected_returns_structured_envelope(disconnected):
    result = qh.handle_run_dax({"query": "EVALUATE Sales"})
    assert result["success"] is False
    assert result["error_type"] == "not_connected"


def test_run_dax_missing_query_returns_clean_error(connected):
    result = qh.handle_run_dax({})
    assert result["success"] is False
    assert "query parameter required" in result["error"]
    # Must not have attempted to run anything
    connected["policy"].safe_run_dax.assert_not_called()


def test_run_dax_empty_query_returns_clean_error(connected):
    # NOTE: the handler validates only truthiness of `query` — it does NOT
    # enforce that the query starts with EVALUATE (that lives downstream in
    # the executor). An empty string is falsy -> caught here.
    result = qh.handle_run_dax({"query": ""})
    assert result["success"] is False
    assert "query parameter required" in result["error"]


def test_run_dax_normal_mode_routes_to_agent_policy(connected):
    connected["policy"].safe_run_dax.return_value = {"success": True, "rows": [{"v": 1}]}
    result = qh.handle_run_dax({"query": "EVALUATE Sales", "top_n": 25})
    assert result["success"] is True
    connected["policy"].safe_run_dax.assert_called_once()
    kwargs = connected["policy"].safe_run_dax.call_args.kwargs
    assert kwargs["query"] == "EVALUATE Sales"
    assert kwargs["mode"] == "auto"
    assert kwargs["max_rows"] == 25


def test_run_dax_does_not_validate_evaluate_prefix(connected):
    # A non-EVALUATE query is passed straight through to the executor; the
    # dispatch shell does not reject it. Pins current behavior.
    connected["policy"].safe_run_dax.return_value = {"success": True, "rows": []}
    qh.handle_run_dax({"query": "DEFINE MEASURE x = 1 EVALUATE {1}"})
    invoked = connected["policy"].safe_run_dax.call_args.kwargs["query"]
    assert invoked.startswith("DEFINE")


def test_run_dax_trace_mode_routes_to_trace_handler(connected, monkeypatch):
    sentinel = {"success": True, "summary": "traced"}
    spy = MagicMock(return_value=sentinel)
    monkeypatch.setattr(qh, "handle_run_dax_trace", spy)
    result = qh.handle_run_dax({"query": "EVALUATE Sales", "mode": "trace"})
    assert result is sentinel
    spy.assert_called_once()
    # Normal-mode path must not have been taken
    connected["policy"].safe_run_dax.assert_not_called()


def test_run_dax_trace_mode_not_connected_short_circuits(disconnected):
    # handle_run_dax checks is_connected() before dispatching to trace.
    result = qh.handle_run_dax({"query": "EVALUATE Sales", "mode": "trace"})
    assert result["success"] is False
    assert result["error_type"] == "not_connected"


# ---------------------------------------------------------------------------
# 04_Query_Operations — handle_query_operations
# ---------------------------------------------------------------------------


def test_list_dmv_probes_works_without_connection(disconnected):
    # Pinned: list_dmv_probes is gated BEFORE the is_connected() check.
    result = qh.handle_query_operations({"operation": "list_dmv_probes"})
    assert result["success"] is True
    assert isinstance(result["probes"], list)
    assert len(result["probes"]) > 0
    names = {p["name"] for p in result["probes"]}
    assert "sessions" in names


def test_unknown_operation_returns_valid_operations(connected):
    result = qh.handle_query_operations({"operation": "frobnicate"})
    assert result["success"] is False
    assert "Unknown operation: frobnicate" in result["error"]
    # Error message enumerates the valid operations
    for valid in ("data_sources", "m_expressions", "search_objects", "list_dmv_probes"):
        assert valid in result["error"]


def test_disconnected_op_returns_structured_not_connected(disconnected):
    # Any non-list_dmv_probes op short-circuits with not_connected (no exception).
    result = qh.handle_query_operations({"operation": "data_sources"})
    assert result["success"] is False
    assert result["error_type"] == "not_connected"


def test_dmv_probe_missing_probe_arg_returns_error(connected):
    result = qh.handle_query_operations({"operation": "dmv_probe"})
    assert result["success"] is False
    assert "probe is required" in result["error"]


def test_data_sources_routes_to_query_executor(connected):
    connected["qe"].execute_info_query.return_value = {"success": True, "rows": []}
    result = qh.handle_query_operations({"operation": "data_sources"})
    assert result["success"] is True
    connected["qe"].execute_info_query.assert_called_once_with("DATASOURCES")


def test_m_expressions_routes_to_query_executor_with_partitions(connected):
    connected["qe"].execute_info_query.return_value = {
        "success": True,
        "rows": [{"e": 1}, {"e": 2}, {"e": 3}],
    }
    result = qh.handle_query_operations({"operation": "m_expressions", "limit": 2})
    assert result["success"] is True
    # B-25: the limit is pushed into the INFO query's TOPN (not fetch-10k-then-slice);
    # the limit is already met by partitions, so INFO.EXPRESSIONS is not queried.
    connected["qe"].execute_info_query.assert_called_once_with("PARTITIONS", top_n=2)
    assert len(result["rows"]) == 2


def test_impersonate_run_missing_dax_query_returns_invalid_args(connected):
    result = qh.handle_query_operations({"operation": "impersonate_run", "roles": ["R1"]})
    assert result["success"] is False
    assert result["error_type"] == "invalid_args"
    # L-11: the canonical name is `query`; the error names both spellings.
    assert "query (legacy: dax_query) is required" in result["error"]


def test_default_operation_is_data_sources(connected):
    # No operation arg -> defaults to data_sources (pins the .get default).
    connected["qe"].execute_info_query.return_value = {"success": True, "rows": []}
    qh.handle_query_operations({})
    connected["qe"].execute_info_query.assert_called_once_with("DATASOURCES")


# ---------------------------------------------------------------------------
# Registry hookup
# ---------------------------------------------------------------------------


def test_register_query_handlers_registers_both_tools():
    registry = MagicMock()
    qh.register_query_handlers(registry)
    registered = [call.args[0] for call in registry.register.call_args_list]
    names = {t.name for t in registered}
    assert names == {"04_Run_DAX", "04_Query_Operations"}
    by_name = {t.name: t for t in registered}
    assert by_name["04_Run_DAX"].handler is qh.handle_run_dax
    assert by_name["04_Query_Operations"].handler is qh.handle_query_operations
    assert by_name["04_Run_DAX"].category == "query"
