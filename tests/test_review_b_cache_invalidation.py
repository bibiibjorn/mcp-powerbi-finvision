"""2026-09-22 review, slice B — result-cache correctness (B-02, B-03, B-04, B-24, B-28, B-29, L-03).

The old cache key was ``(query text, top_n)`` with a 600 s TTL and no
invalidation: a model switch, a TOM write or a Desktop-side edit kept serving
the previous rows; ``ensure`` probed through the cache; ``mode=analyze`` and
``warm_cache`` never reached the engine twice.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.fake_adomd import FakeServer, instance_for, make_state

CS_A = "Data Source=localhost:50001"
CS_B = "Data Source=localhost:50002"
Q = 'EVALUATE ROW("total", [Total])'


def _version_rows(stamp):
    return [{"CUBE_NAME": "Model", "LAST_SCHEMA_UPDATE": stamp, "LAST_DATA_UPDATE": stamp}]


@pytest.fixture
def two_models(monkeypatch):
    a, b = FakeServer("A"), FakeServer("B")
    a.respond("[Total]", [{"[total]": "licensee_A_total"}])
    b.respond("[Total]", [{"[total]": "licensee_B_total"}])
    for srv in (a, b):
        srv.respond("MDSCHEMA_CUBES", _version_rows("2026-09-22T10:00:00"))
        srv.respond("TMSCHEMA_MODEL", [{"ModifiedTime": "t0", "StructureModifiedTime": "t0"}])
    state, cm, opened = make_state(monkeypatch, {CS_A: a, CS_B: b}, connect_to=CS_A)
    return state, cm, a, b


def _count(server, needle):
    return sum(1 for e in server.executions if needle in e)


def test_second_identical_query_is_a_cache_hit_with_honest_timing(two_models):
    state, _cm, a, _b = two_models
    first = state.query_executor.validate_and_execute_dax(Q)
    second = state.query_executor.validate_and_execute_dax(Q)
    assert first["cache"]["hit"] is False
    assert second["cache"]["hit"] is True
    assert second["cache"]["age_s"] is not None
    assert second["execution_time_ms"] == 0
    assert "cached_execution_time_ms" in second
    assert "__cached_at__" not in second and "__cells__" not in second
    assert _count(a, "[Total]") == 1


def test_model_switch_never_serves_the_previous_models_rows(two_models):
    """B-02(a): connect to another licensee, same query -> must hit the new model."""
    state, cm, a, b = two_models
    assert state.query_executor.validate_and_execute_dax(Q)["rows"][0]["[total]"] == (
        "licensee_A_total"
    )
    assert cm.connect_instance(instance_for(CS_B))["success"]
    res = state.query_executor.validate_and_execute_dax(Q)
    assert res["rows"][0]["[total]"] == "licensee_B_total"
    assert res["cache"]["hit"] is False
    assert _count(b, "[Total]") == 1


def test_bump_model_generation_invalidates(two_models):
    """B-02(b)/(c), L-03: after a write the verify query must re-execute."""
    state, _cm, a, _b = two_models
    state.query_executor.validate_and_execute_dax(Q)
    gen = state.bump_model_generation("measure update")
    assert gen == state.model_generation
    res = state.query_executor.validate_and_execute_dax(Q)
    assert res["cache"]["hit"] is False
    assert _count(a, "[Total]") == 2


def test_external_desktop_change_detected_by_version_probe(two_models):
    """B-02(d): a Desktop-side edit/refresh (no MCP write) invalidates too."""
    state, _cm, a, _b = two_models
    state._model_version_probe_interval = 0.0
    state.query_executor.validate_and_execute_dax(Q)
    assert state.query_executor.validate_and_execute_dax(Q)["cache"]["hit"] is True
    a.respond("MDSCHEMA_CUBES", _version_rows("2026-09-22T10:05:00"))  # user refreshed
    res = state.query_executor.validate_and_execute_dax(Q)
    assert res["cache"]["hit"] is False
    assert _count(a, "[Total]") == 2


def test_unverifiable_model_version_disables_caching(two_models):
    state, _cm, a, _b = two_models
    a.respond("MDSCHEMA_CUBES", RuntimeError("no such DMV"))
    a.respond("TMSCHEMA_MODEL", RuntimeError("no such DMV"))
    state._model_version_probe_interval = 0.0
    state.query_executor.validate_and_execute_dax(Q)
    res = state.query_executor.validate_and_execute_dax(Q)
    assert res["cache"]["hit"] is False
    assert _count(a, "[Total]") == 2


def test_bypass_cache_always_hits_the_server(two_models):
    state, _cm, a, _b = two_models
    state.query_executor.validate_and_execute_dax(Q)
    res = state.query_executor.validate_and_execute_dax(Q, bypass_cache=True)
    assert res["cache"]["hit"] is False and res["cache"]["bypassed"] is True
    assert _count(a, "[Total]") == 2


def test_health_probe_never_served_from_cache(two_models):
    """B-03: a dead connection must fail the ensure probe even after a cached success."""
    state, _cm, a, _b = two_models
    assert state.probe_connection_health()["success"] is True
    a.respond("DBSCHEMA_CATALOGS", RuntimeError("A connection cannot be made"))
    assert state.probe_connection_health()["success"] is False


def test_cached_rows_are_copies(two_models):
    """B-29: in-place normalisation by one caller must not corrupt the cache."""
    state, _cm, _a, _b = two_models
    first = state.query_executor.validate_and_execute_dax(Q)
    first["rows"][0].pop("[total]")
    second = state.query_executor.validate_and_execute_dax(Q)
    assert second["cache"]["hit"] is True
    assert second["rows"][0]["[total]"] == "licensee_A_total"


def test_large_results_are_not_cached(two_models):
    """B-28: no unbounded memory — results over the per-entry cap are not memoised."""
    from core.infrastructure.query_executor import CACHE_MAX_ROWS_PER_ENTRY

    state, _cm, a, _b = two_models
    a.respond("BigTable", [{"[i]": i} for i in range(CACHE_MAX_ROWS_PER_ENTRY + 5)])
    r1 = state.query_executor.validate_and_execute_dax("EVALUATE BigTable", max_rows=5000)
    r2 = state.query_executor.validate_and_execute_dax("EVALUATE BigTable", max_rows=5000)
    assert r1["cache"]["stored"] is False
    assert r2["cache"]["hit"] is False


def test_analyze_mode_is_a_real_n_run_benchmark(two_models):
    """B-04: mode=analyze runs N times against the engine, cache bypassed."""
    from core.orchestration.query_policy import QueryPolicy

    state, _cm, a, _b = two_models
    policy = QueryPolicy(MagicMock(get=lambda k, d=None: d))
    out = policy.safe_run_dax(state, Q, mode="analyze", runs=4)
    assert out["success"] is True
    bench = out["benchmark"]
    assert bench["runs"] == 4 and len(bench["run_ms"]) == 4
    assert "first_run_ms" in bench  # untimed warm-up reported separately
    assert _count(a, "[Total]") == 5
    out2 = policy.safe_run_dax(state, Q, mode="analyze", runs=2)
    assert _count(a, "[Total]") == 8, "a second benchmark must not be served from cache"
    assert out2["benchmark"]["wall_clock"]["avg_ms"] >= 0


def test_analyze_cold_clears_vertipaq_before_each_run(two_models):
    from core.orchestration.query_policy import QueryPolicy

    state, _cm, a, _b = two_models
    policy = QueryPolicy(MagicMock(get=lambda k, d=None: d))
    out = policy.safe_run_dax(state, Q, mode="analyze", runs=3, cold=True)
    assert out["benchmark"]["cold"] is True
    assert out["benchmark"]["cache_cleared"] is True
    assert _count(a, "<ClearCache>") == 3


def test_warm_cache_reaches_the_engine_every_time(two_models, monkeypatch):
    """B-04/B-22: warm_cache bypasses the result cache and escapes ']'."""
    import server.handlers.query_handler as qh

    state, _cm, a, _b = two_models
    monkeypatch.setattr(qh, "connection_state", state)
    qh._handle_warm_cache({"measures": ["Total Sales"], "columns": ["'T'[C]"]})
    qh._handle_warm_cache({"measures": ["Total Sales"], "columns": ["'T'[C]"]})
    measure_runs = [e for e in a.executions if "[Total Sales]" in e]
    assert len(measure_runs) == 2
    assert any("COUNTROWS(DISTINCT('T'[C]))" in e for e in a.executions)
    # B-22: ']' is escaped as ']]'; qualified refs pass through; [x] is not re-wrapped.
    # (DaxValidator's own ']]' handling is slice D / WP3.)
    assert qh._measure_ref("Sales [EUR]") == "[Sales [EUR]]]"
    assert qh._measure_ref("[Margin]") == "[Margin]"
    assert qh._measure_ref("'Fact Sales'[Margin]") == "'Fact Sales'[Margin]"
    assert qh._measure_ref("Sales[Margin]") == "Sales[Margin]"


def test_trace_result_ring_is_addressable_by_trace_id(two_models):
    """L-20: stored traces get a trace_id; ambiguity is reported."""
    state, _cm, _a, _b = two_models
    t1 = state.store_trace_result({"query": "EVALUATE A", "performance": {"total_ms": 1}})
    t2 = state.store_trace_result({"query": "EVALUATE B", "performance": {"total_ms": 2}})
    assert t1 != t2
    assert state.get_trace_result(t1)["query"] == "EVALUATE A"
    assert state.get_cached_trace_result()["query"] == "EVALUATE B"  # legacy "last"
    assert state.get_cached_trace_result(trace_id=t1)["query"] == "EVALUATE A"
    info = state.get_last_trace_info()
    assert info["trace_id"] == t2 and info["ambiguous"] is True
