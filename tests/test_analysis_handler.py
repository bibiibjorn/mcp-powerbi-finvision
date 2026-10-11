"""Dispatch-shell tests for 06_Analysis_Operations (analysis_handler).

No live AMO/CLR: connection_state is faked at the handler-module boundary; the
analysis orchestrator is a MagicMock. Covers the pure unknown-operation branch,
the not-connected / manager-unavailable gates, and correct routing of
simple/full/compare + the simple-mode sub-dispatch.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock


import server.handlers.analysis_handler as mod


def _fake_conn(orch=None, *, connected=True, agent_policy=True):
    ns = SimpleNamespace()
    ns.is_connected = lambda: connected
    if agent_policy:
        ns.agent_policy = SimpleNamespace(analysis_orch=orch or MagicMock())
    else:
        ns.agent_policy = None
    ns.query_executor = MagicMock()
    ns.bpa_analyzer = None
    return ns


def test_unknown_operation_is_pure_error():
    # No connection touched before this branch.
    out = mod.handle_analysis_operations({"operation": "bogus"})
    assert out["success"] is False
    assert "Unknown operation" in out["error"]


def test_not_connected_gate(monkeypatch):
    monkeypatch.setattr(mod, "connection_state", _fake_conn(connected=False))
    out = mod.handle_analysis_operations({"operation": "simple", "mode": "tables"})
    assert out["success"] is False
    assert out["error_type"] == "not_connected"


def test_manager_unavailable_when_no_agent_policy(monkeypatch):
    monkeypatch.setattr(mod, "connection_state", _fake_conn(agent_policy=False))
    out = mod.handle_analysis_operations({"operation": "simple", "mode": "tables"})
    assert out["success"] is False
    assert out["error_type"] == "manager_unavailable"


def test_simple_mode_tables_routes_to_orch(monkeypatch):
    orch = MagicMock()
    orch.list_tables_simple.return_value = {"success": True, "tables": ["A", "B"]}
    monkeypatch.setattr(mod, "connection_state", _fake_conn(orch))
    out = mod.handle_analysis_operations({"operation": "simple", "mode": "tables"})
    assert out["success"] is True
    orch.list_tables_simple.assert_called_once()


def test_simple_mode_measure_requires_table_and_name(monkeypatch):
    monkeypatch.setattr(mod, "connection_state", _fake_conn())
    out = mod.handle_analysis_operations({"operation": "simple", "mode": "measure"})
    assert out["success"] is False
    assert "requires both table and measure_name" in out["error"]


def test_simple_unknown_mode(monkeypatch):
    monkeypatch.setattr(mod, "connection_state", _fake_conn())
    out = mod.handle_analysis_operations({"operation": "simple", "mode": "nonsense"})
    assert out["success"] is False
    assert "Unknown mode" in out["error"]


def test_full_routes_to_comprehensive(monkeypatch):
    orch = MagicMock()
    orch.comprehensive_analysis.return_value = {"success": True, "issues": []}
    monkeypatch.setattr(mod, "connection_state", _fake_conn(orch))
    out = mod.handle_analysis_operations({"operation": "full", "scope": "best_practices"})
    assert out["success"] is True
    orch.comprehensive_analysis.assert_called_once()


def test_compare_routes_to_comparison_helper(monkeypatch):
    import server.handlers._internal.comparison_helpers as ch

    monkeypatch.setattr(
        ch, "handle_compare_pbi_models", lambda a: {"success": True, "compared": True}
    )
    out = mod.handle_analysis_operations({"operation": "compare"})
    assert out["success"] is True
    assert out["compared"] is True
