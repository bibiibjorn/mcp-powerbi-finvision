"""S-30: 04_Run_DAX batch mode (``queries``) - sequential, one connection, per-query result/error."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.utilities import progress_ticker as pt
from server.handlers import query_handler as qh


@pytest.fixture
def connected(monkeypatch):
    monkeypatch.setattr(qh.connection_state, "is_connected", lambda: True, raising=False)
    policy = MagicMock(name="agent_policy")
    monkeypatch.setattr(qh.connection_state, "agent_policy", policy, raising=False)
    monkeypatch.setattr(qh.connection_state, "query_executor", MagicMock(), raising=False)
    monkeypatch.setattr(
        qh.connection_state, "get_connection_identity", lambda: {"port": 1}, raising=False
    )
    monkeypatch.setattr(pt, "emit", lambda *a: None)
    return policy


def _ok(i):
    return {"success": True, "rows": [{"[v]": i}], "row_count": 1, "execution_time_ms": 3}


def test_batch_runs_each_query_in_order_with_shared_options(connected):
    calls = []

    def run(**kw):
        calls.append(kw)
        return _ok(len(calls))

    connected.safe_run_dax.side_effect = run
    out = qh.handle_run_dax(
        {
            "queries": ['EVALUATE ROW("a", 1)', {"id": "second", "query": 'EVALUATE ROW("a", 2)'}],
            "top_n": 7,
            "bypass_cache": True,
        }
    )
    assert out["success"] is True and out["mode"] == "batch"
    assert [r["id"] for r in out["results"]] == ["q1", "second"]
    assert out["executed"] == 2 and out["succeeded"] == 2 and out["failed"] == 0
    assert [c["query"] for c in calls] == ['EVALUATE ROW("a", 1)', 'EVALUATE ROW("a", 2)']
    assert all(c["max_rows"] == 7 and c["bypass_cache"] is True for c in calls)
    assert out["results"][0]["rows"] == [{"[v]": 1}]


def test_batch_reports_per_query_errors_and_continues(connected):
    connected.safe_run_dax.side_effect = [
        _ok(1),
        {"success": False, "error": "bad column", "error_type": "dax_error"},
        _ok(3),
    ]
    out = qh.handle_run_dax({"queries": ["EVALUATE a", "EVALUATE b", "EVALUATE c"]})
    assert out["success"] is False and out["error_type"] == "batch_partial_failure"
    assert out["executed"] == 3 and out["succeeded"] == 2 and out["failed"] == 1
    assert out["results"][1]["success"] is False and out["results"][1]["error"] == "bad column"
    assert out["results"][2]["success"] is True


def test_batch_stop_on_error_skips_the_rest(connected):
    connected.safe_run_dax.side_effect = [{"success": False, "error": "boom"}, _ok(2)]
    out = qh.handle_run_dax({"queries": ["EVALUATE a", "EVALUATE b"], "stop_on_error": True})
    assert out["executed"] == 1 and out["stopped_early"] is True
    assert out["not_run"] == ["q2"]
    assert connected.safe_run_dax.call_count == 1


def test_batch_cancel_between_queries(connected, monkeypatch):
    connected.safe_run_dax.side_effect = lambda **kw: _ok(1)
    state = {"n": 0}

    def cancelled():
        state["n"] += 1
        return state["n"] > 1  # first probe false, then cancelled

    monkeypatch.setattr(pt, "is_cancelled", cancelled)
    out = qh.handle_run_dax({"queries": ["EVALUATE a", "EVALUATE b", "EVALUATE c"]})
    assert out["cancelled"] is True and out["executed"] == 1 and out["not_run"] == ["q2", "q3"]


def test_batch_progress_per_query(connected, monkeypatch):
    seen = []
    monkeypatch.setattr(pt, "emit", lambda d, t, m: seen.append((d, t)))
    connected.safe_run_dax.side_effect = lambda **kw: _ok(1)
    qh.handle_run_dax({"queries": ["EVALUATE a", "EVALUATE b"]})
    assert seen == [(1, 2), (2, 2)]


@pytest.mark.parametrize(
    "bad,etype",
    [
        ({"queries": []}, "invalid_args"),
        ({"queries": "EVALUATE a"}, "invalid_args"),
        ({"queries": ["EVALUATE a"] * 21}, "invalid_args"),
        ({"queries": ["EVALUATE a", ""]}, "invalid_args"),
        ({"queries": ["EVALUATE a"], "query": "EVALUATE b"}, "invalid_args"),
        ({"queries": ["EVALUATE a"], "export_path": "x.csv"}, "invalid_args"),
        ({"queries": ["EVALUATE a"], "mode": "trace"}, "invalid_args"),
        ({"queries": [{"id": "x"}]}, "invalid_args"),
        (
            {"queries": [{"id": "d", "query": "EVALUATE a"}, {"id": "d", "query": "EVALUATE b"}]},
            "invalid_args",
        ),
    ],
)
def test_batch_input_validation(connected, bad, etype):
    out = qh.handle_run_dax(bad)
    assert out["success"] is False and out["error_type"] == etype
    connected.safe_run_dax.assert_not_called()


def test_batch_requires_a_connection(monkeypatch):
    monkeypatch.setattr(qh.connection_state, "is_connected", lambda: False, raising=False)
    out = qh.handle_run_dax({"queries": ["EVALUATE a"]})
    assert out["success"] is False and out["error_type"] == "not_connected"


def test_batch_validate_mode_is_allowed(connected, monkeypatch):
    qe = MagicMock()
    qe.validate_dax.return_value = {"success": True, "valid": True}
    monkeypatch.setattr(qh.connection_state, "query_executor", qe, raising=False)
    out = qh.handle_run_dax({"queries": ["EVALUATE a", "EVALUATE b"], "mode": "validate"})
    assert out["succeeded"] == 2 and qe.validate_dax.call_count == 2
