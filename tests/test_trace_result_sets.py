"""result_sets + execution_metrics on 04_Run_DAX and 09_Debug, and the traced
benchmark (7.15.0).

Before 7.15.0 a query with several EVALUATE statements reported only the first
result set, and the engine's own ExecutionMetrics (peak memory, FE/SE CPU split,
thread-pool wait) were never parsed at all. Mocked at the runner boundary.
"""

from __future__ import annotations

import pytest

from core.query_plan import capturer
from server.handlers import query_handler
from server.handlers._debug.helpers import _build_trace_response


def _trace_result(**overrides):
    """A NativeTraceRunner response in its 7.15.0 shape."""
    result = {
        "total_ms": 1428,
        "fe_ms": 1328,
        "se_ms": 100,
        "se_cpu_ms": 32,
        "se_parallelism": 0.3,
        "se_queries": 189,
        "se_cache_hits": 110,
        "fe_pct": 93.0,
        "se_pct": 7.0,
        "se_events": [{"line": 1, "duration_ms": 60, "query": "SELECT 1"}],
        "timeline": [],
        "agg_rewrites": [],
        "logical_plan": "",
        "physical_plan": "",
        "cache_cleared": True,
        "result_sets": [
            {"columns": ["[a]"], "row_count": 8},
            {"columns": ["[b]"], "row_count": 3},
        ],
        "execution_metrics": {"peak_memory_kb": 223127, "result_rows": 8},
        "se_net_parallel_ms": 100,
        "total_cpu_ms": 1297,
        "dq_ms": 0,
        "parallel_se_detected": False,
        "runner_lane": "in_process",
    }
    result.update(overrides)
    return result


# --- 09_Debug_Operations run_dax ----------------------------------------


class TestDebugTraceResponse:
    def test_all_result_sets_surface(self):
        response = _build_trace_response(_trace_result())
        assert [rs["row_count"] for rs in response["result_sets"]] == [8, 3]

    def test_execution_metrics_surface(self):
        response = _build_trace_response(_trace_result())
        assert response["execution_metrics"]["peak_memory_kb"] == 223127

    def test_new_headline_metrics_reach_performance(self):
        perf = _build_trace_response(_trace_result())["performance"]
        assert perf["total_cpu_ms"] == 1297
        assert perf["se_net_parallel_ms"] == 100
        assert perf["dq_ms"] == 0
        assert perf["parallel_se_detected"] is False

    def test_legacy_performance_keys_unchanged(self):
        """Existing consumers read these by name."""
        perf = _build_trace_response(_trace_result())["performance"]
        for key in (
            "total_ms",
            "fe_ms",
            "se_ms",
            "se_cpu_ms",
            "se_parallelism",
            "se_queries",
            "se_cache_hits",
            "fe_pct",
            "se_pct",
        ):
            assert key in perf, key

    def test_exe_lane_result_defaults_are_empty_not_missing(self):
        """The fallback lane has no metrics; callers must not KeyError."""
        response = _build_trace_response({"total_ms": 100})
        assert response["result_sets"] == []
        assert response["execution_metrics"] == {}

    def test_xmsql_dedup_still_applies(self):
        result = _trace_result(
            se_events=[
                {"line": 1, "duration_ms": 5, "query": "SELECT 1"},
                {"line": 2, "duration_ms": 6, "query": "SELECT 1"},
            ]
        )
        response = _build_trace_response(result)
        assert len(response["se_queries_lookup"]) == 1
        assert [e["query_idx"] for e in response["se_events"]] == [0, 0]


# --- 04_Run_DAX mode=trace ----------------------------------------------


@pytest.fixture
def traced_run_dax(monkeypatch):
    """Wire 04_Run_DAX's trace path onto a canned runner result."""

    class FakeRunner:
        def __init__(self, conn_str):
            pass

        @staticmethod
        def is_available():
            return True

        def execute_with_trace(self, query, clear_cache=True, **kw):
            return _trace_result()

    monkeypatch.setattr("core.infrastructure.query_trace.NativeTraceRunner", FakeRunner)
    monkeypatch.setattr(query_handler.connection_state, "is_connected", lambda: True)
    monkeypatch.setattr(
        query_handler.connection_state,
        "connection_manager",
        type("CM", (), {"connection_string": "Data Source=127.0.0.1:1234"})(),
        raising=False,
    )
    monkeypatch.setattr(
        query_handler.connection_state,
        "store_trace_result",
        lambda data: None,
        raising=False,
    )


class TestRunDaxTrace:
    def test_result_sets_and_metrics_returned(self, traced_run_dax):
        res = query_handler.handle_run_dax_trace({"query": "EVALUATE A EVALUATE B"})
        assert res["success"] is True
        assert [rs["row_count"] for rs in res["result_sets"]] == [8, 3]
        assert res["execution_metrics"]["peak_memory_kb"] == 223127

    def test_summary_string_preserved(self, traced_run_dax):
        res = query_handler.handle_run_dax_trace({"query": "EVALUATE A"})
        assert "Total: 1428ms" in res["summary"]

    def test_runner_lane_reported(self, traced_run_dax):
        res = query_handler.handle_run_dax_trace({"query": "EVALUATE A"})
        assert res["runner_lane"] == "in_process"


# --- traced benchmark ----------------------------------------------------


class TestBenchmarkTraced:
    @pytest.fixture(autouse=True)
    def _connected(self, monkeypatch):
        import core.infrastructure.connection_state as cs

        monkeypatch.setattr(cs.connection_state, "is_connected", lambda: True)

    def test_cold_and_warm_are_reported_separately(self, monkeypatch):
        """Pooling them would average two different measurements."""
        calls = []

        def fake_native(query, clear_cache, capture_eval_log=False):
            calls.append(clear_cache)
            return _trace_result(total_ms=1400 if clear_cache else 200)

        monkeypatch.setattr(capturer, "_native_capture", fake_native)
        res = capturer.benchmark_traced("EVALUATE {1}", cold_runs=2, warm_runs=3, warmup=0)

        assert res["success"] is True
        assert res["cold"]["total_ms"]["runs"] == 2
        assert res["warm"]["total_ms"]["runs"] == 3
        assert res["cold"]["total_ms"]["avg_ms"] == 1400
        assert res["warm"]["total_ms"]["avg_ms"] == 200
        assert calls == [True, True, False, False, False]

    def test_stats_include_the_documented_five(self, monkeypatch):
        values = iter([100, 200, 300])
        monkeypatch.setattr(
            capturer,
            "_native_capture",
            lambda q, c, capture_eval_log=False: _trace_result(total_ms=next(values)),
        )
        res = capturer.benchmark_traced("EVALUATE {1}", cold_runs=3, warm_runs=0, warmup=0)
        stats = res["cold"]["total_ms"]
        assert stats["avg_ms"] == 200
        assert stats["min_ms"] == 100
        assert stats["max_ms"] == 300
        assert stats["median_ms"] == 200
        assert stats["stddev_ms"] == pytest.approx(81.6, abs=0.1)

    def test_per_run_server_timings_are_kept(self, monkeypatch):
        monkeypatch.setattr(
            capturer,
            "_native_capture",
            lambda q, c, capture_eval_log=False: _trace_result(),
        )
        res = capturer.benchmark_traced("EVALUATE {1}", cold_runs=2, warm_runs=0, warmup=0)
        assert res["cold"]["runs"][0]["se_queries"] == 189
        assert res["cold"]["runs"][0]["fe_ms"] == 1328

    def test_warmup_runs_are_not_measured(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            capturer,
            "_native_capture",
            lambda q, c, capture_eval_log=False: calls.append(c) or _trace_result(),
        )
        res = capturer.benchmark_traced("EVALUATE {1}", cold_runs=1, warm_runs=0, warmup=2)
        assert len(calls) == 3
        assert res["cold"]["total_ms"]["runs"] == 1

    def test_failed_runs_reported_not_swallowed(self, monkeypatch):
        monkeypatch.setattr(
            capturer,
            "_native_capture",
            lambda q, c, capture_eval_log=False: {"_error": "trace failed"},
        )
        res = capturer.benchmark_traced("EVALUATE {1}", cold_runs=1, warm_runs=1, warmup=0)
        assert res["success"] is False
        assert "trace failed" in res["errors"]

    def test_zero_runs_rejected(self):
        res = capturer.benchmark_traced("EVALUATE {1}", cold_runs=0, warm_runs=0)
        assert res["success"] is False
        assert res["error_type"] == "invalid_args"
