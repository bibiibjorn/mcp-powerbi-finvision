"""Regression tests for real VertiPaq cache clearing in the query-plan lane.

Historically `13_Query_Plan` "cleared" the cache by running `EVALUATE {1}` — a
throwaway constant query that clears nothing. Every `clear_cache=True` capture
and every `cold_cache=True` benchmark therefore produced warm-cache numbers
labelled cold, which silently invalidates any optimization A/B comparison.

These tests pin the two properties that matter:
  1. the ClearCache XMLA command is what actually gets issued, and
  2. when clearing fails, the caller is told (rather than being handed warm
     numbers that look cold).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.query_plan import capturer as cap_mod


@pytest.fixture
def fake_state(monkeypatch):
    state = MagicMock()
    state.is_connected.return_value = True
    executor = MagicMock()
    executor.validate_and_execute_dax.return_value = {
        "success": True,
        "execution_time_ms": 10,
        "row_count": 1,
    }
    executor.clear_vertipaq_cache.return_value = {"success": True, "cache_cleared": True}
    state.query_executor = executor
    state.dax_query_executor = executor

    import core.infrastructure.connection_state as cs_mod

    monkeypatch.setattr(cs_mod, "connection_state", state)
    return state


class TestBenchmarkColdCache:
    def test_cold_cache_issues_real_clearcache_not_evaluate_1(self, fake_state):
        """cold_cache=True must call clear_vertipaq_cache, never `EVALUATE {1}`."""
        executor = fake_state.query_executor

        cap_mod.benchmark('EVALUATE ROW("v", 1)', runs=3, cold_cache=True, warmup=0)

        assert executor.clear_vertipaq_cache.call_count == 3, "one real clear per measured run"

        # The old no-op pre-roll must be gone.
        issued = [c.args[0] for c in executor.validate_and_execute_dax.call_args_list if c.args]
        assert "EVALUATE {1}" not in issued, "the fake cache-clear query must not be used"

    def test_cold_cache_honoured_flag_true_on_success(self, fake_state):
        res = cap_mod.benchmark('EVALUATE ROW("v", 1)', runs=2, cold_cache=True, warmup=0)
        assert res["success"] is True
        assert res["cold_cache_honoured"] is True

    def test_cold_cache_honoured_false_when_clear_fails(self, fake_state):
        """A failed clear must be reported, not silently swallowed."""
        fake_state.query_executor.clear_vertipaq_cache.return_value = {
            "success": False,
            "cache_cleared": False,
            "error": "no connection",
        }

        res = cap_mod.benchmark('EVALUATE ROW("v", 1)', runs=2, cold_cache=True, warmup=0)

        assert res["success"] is True  # the runs still happened
        assert res["cold_cache_honoured"] is False, "warm numbers must not be labelled cold"

    def test_cold_cache_honoured_none_when_not_requested(self, fake_state):
        res = cap_mod.benchmark('EVALUATE ROW("v", 1)', runs=2, cold_cache=False, warmup=0)
        assert res["cold_cache_honoured"] is None
        fake_state.query_executor.clear_vertipaq_cache.assert_not_called()


class TestBenchmarkBaseline:
    def test_baseline_is_fastest_run_not_mean(self, fake_state):
        """The baseline protocol compares the FASTEST valid run, not the average."""
        times = [500.0, 100.0, 300.0]
        fake_state.query_executor.validate_and_execute_dax.side_effect = [
            {"success": True, "execution_time_ms": t, "row_count": 1} for t in times
        ]

        res = cap_mod.benchmark('EVALUATE ROW("v", 1)', runs=3, cold_cache=False, warmup=0)

        assert res["summary"]["baseline_ms"] == 100.0
        assert res["summary"]["min_ms"] == 100.0
        assert res["summary"]["mean_ms"] == pytest.approx(300.0)

    def test_wide_spread_emits_warning(self, fake_state):
        """>20% run-to-run spread means the baseline is not yet trustworthy."""
        times = [100.0, 400.0]
        fake_state.query_executor.validate_and_execute_dax.side_effect = [
            {"success": True, "execution_time_ms": t, "row_count": 1} for t in times
        ]

        res = cap_mod.benchmark('EVALUATE ROW("v", 1)', runs=2, cold_cache=False, warmup=0)

        assert res["summary"]["spread_pct"] == 300.0
        assert "warning" in res["summary"]

    def test_tight_spread_has_no_warning(self, fake_state):
        times = [100.0, 105.0]
        fake_state.query_executor.validate_and_execute_dax.side_effect = [
            {"success": True, "execution_time_ms": t, "row_count": 1} for t in times
        ]

        res = cap_mod.benchmark('EVALUATE ROW("v", 1)', runs=2, cold_cache=False, warmup=0)

        assert res["summary"]["spread_pct"] == 5.0
        assert "warning" not in res["summary"]


class TestCaptureFailsHonestly:
    def test_capture_errors_when_native_runner_unavailable(self, fake_state, monkeypatch):
        """A dead capture path must return success=False, not empty-success.

        The old code fell back to a NullTrace and returned success=True with
        empty plans — a false all-clear, which is worse than an error.
        """
        monkeypatch.setattr(
            cap_mod,
            "_native_capture",
            lambda dax_query, clear_cache: {"_error": "DaxExecutor.exe not found"},
        )

        cap = cap_mod.capture('EVALUATE ROW("v", 1)')

        assert cap["success"] is False
        assert cap["error_type"] == "trace_unavailable"
        assert "DaxExecutor.exe not found" in cap["error"]

    def test_capture_reports_cache_cleared(self, fake_state, monkeypatch):
        monkeypatch.setattr(
            cap_mod,
            "_native_capture",
            lambda dax_query, clear_cache: {
                "logical_plan": "AddColumns: RelLogOp",
                "physical_plan": "AddColumns: IterPhyOp #Records=16",
                "se_events": [{"query": "SELECT ...", "duration_ms": 3}],
                "cache_cleared": True,
                "total_ms": 12,
            },
        )

        cap = cap_mod.capture('EVALUATE ROW("v", 1)', clear_cache=True)

        assert cap["success"] is True
        assert cap["cache_cleared"] is True
        assert "IterPhyOp" in cap["physical_plan_raw"]
        assert cap["xmsql_queries"] == ["SELECT ..."]
