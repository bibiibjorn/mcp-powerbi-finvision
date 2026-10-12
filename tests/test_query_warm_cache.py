"""04_Query_Operations warm_cache (Phase 5 quick win).

Issue column/measure-touching queries (cache NOT cleared) to populate the
VertiPaq cache before a warm-run benchmark, removing a manual pre-step in
perf sessions.
"""

from unittest.mock import MagicMock

import server.handlers.query_handler as qh


def _patch_connected(monkeypatch):
    cs = MagicMock()
    cs.is_connected.return_value = True
    qe = MagicMock()
    qe.validate_and_execute_dax.return_value = {"success": True, "rows": [{"v": 1}]}
    cs.query_executor = qe
    monkeypatch.setattr(qh, "connection_state", cs)
    return qe


def test_warm_cache_runs_one_query_per_measure(monkeypatch):
    qe = _patch_connected(monkeypatch)
    res = qh.handle_query_operations({"operation": "warm_cache", "measures": ["Sales", "Cost"]})
    assert res["success"] is True
    assert res["warmed"] == 2
    # Two measures -> two evaluate calls; cache must NOT be cleared.
    assert qe.validate_and_execute_dax.call_count == 2


def test_warm_cache_requires_targets(monkeypatch):
    _patch_connected(monkeypatch)
    res = qh.handle_query_operations({"operation": "warm_cache"})
    assert res["success"] is False


def test_warm_cache_not_connected(monkeypatch):
    cs = MagicMock()
    cs.is_connected.return_value = False
    monkeypatch.setattr(qh, "connection_state", cs)
    res = qh.handle_query_operations({"operation": "warm_cache", "measures": ["X"]})
    assert res["success"] is False
    assert res["error_type"] == "not_connected"
