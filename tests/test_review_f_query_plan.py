"""2026-09-22 review, slice F: 13_Query_Plan capture / export / benchmark fixes.

Pins F-08 (unique capture ids), F-09 (verdict not "hot" for every plan), F-10
(DAXEvaluationLog subscribed at trace start), F-11 (.daxx is an OPC package),
F-14 (batched traces stop at the caller's budget), F-22 (cold-cache honesty +
interleaved A/B), F-23 (plan analysis on the largest result set; two distinct
cache counts) and F-34 (capture store spills raw plans to disk).

No live engine: the native trace lane is replaced by fakes at the
``_native_capture`` / ``TraceSession`` seams; the code under test (id
generation, subscription order, zip layout, loop control, re-analysis) is real.
"""

from __future__ import annotations

import itertools
import types
import zipfile
from unittest.mock import MagicMock

import pytest

from core.query_plan import capturer as cap_mod
from core.query_plan import exporter
from core.query_plan import heuristics, parser
from server.handlers import query_plan_handler as qph


@pytest.fixture
def fake_state(monkeypatch):
    state = MagicMock()
    state.is_connected.return_value = True
    executor = MagicMock()
    executor.validate_and_execute_dax.return_value = {"success": True, "ms": 5}
    state.query_executor = executor
    state.dax_query_executor = executor
    import core.infrastructure.connection_state as cs_mod

    monkeypatch.setattr(cs_mod, "connection_state", state)
    return state


def _native(**over):
    base = {
        "logical_plan": "AddColumns: RelLogOp",
        "physical_plan": "AddColumns: IterPhyOp #Records=16",
        "se_events": [],
        "cache_cleared": True,
        "total_ms": 12,
        "timeline": [],
        "agg_rewrites": [],
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# F-08: capture ids are unique per run; compare refuses a == b
# ---------------------------------------------------------------------------


def test_f08_same_query_captured_twice_gets_two_ids(fake_state, monkeypatch):
    monkeypatch.setattr(cap_mod, "_native_capture", lambda dax_query, clear_cache: _native())
    a = cap_mod.capture('EVALUATE ROW("v", [Sales])')
    b = cap_mod.capture('EVALUATE ROW("v", [Sales])')
    assert a["capture_id"] != b["capture_id"], "before/after captures must not collide"
    assert a["query_hash"] == b["query_hash"]
    cap_mod.remember(a)
    cap_mod.remember(b)
    assert cap_mod.recall(a["capture_id"]) is not cap_mod.recall(b["capture_id"])


def test_f08_compare_rejects_the_same_capture_twice():
    res = qph.handle_query_plan(
        {"operation": "compare", "capture_id_a": "abc", "capture_id_b": "abc"}
    )
    assert res["success"] is False
    assert "same capture" in res["error"]


def test_f08_compare_reports_same_query(fake_state, monkeypatch):
    monkeypatch.setattr(cap_mod, "_native_capture", lambda dax_query, clear_cache: _native())
    a = cap_mod.capture("EVALUATE {1}")
    b = cap_mod.capture("EVALUATE {1}")
    cap_mod.remember(a)
    cap_mod.remember(b)
    res = qph.handle_query_plan(
        {"operation": "compare", "capture_id_a": a["capture_id"], "capture_id_b": b["capture_id"]}
    )
    assert res["success"] is True
    assert res["same_query"] is True


# ---------------------------------------------------------------------------
# F-09: a small healthy plan is not "hot"
# ---------------------------------------------------------------------------

_TINY_PLAN = (
    "AddColumns: IterPhyOp LogOp=AddColumns IterCols(0)('Product'[Color]) #Records=16\n"
    "\tSpool_Iterator<SpoolIterator>: IterPhyOp LogOp=Sum_Vertipaq IterCols(0)('Product'[Color]) #Records=16\n"
    "\t\tAggregationSpool<Cache>: SpoolPhyOp #Records=16\n"
    "\t\t\tVertipaqResult: IterPhyOp #FieldCols=1 #ValueCols=1\n"
)


def test_f09_four_line_se_only_plan_is_ok():
    tree = parser.parse_logical_plan(_TINY_PLAN)
    verdict = heuristics.analyze(tree, {}, {"result_rows": 16})
    assert verdict["verdict"] == "ok", verdict["findings"]


def test_f09_bare_crossapply_is_not_hot():
    plan = (
        "CrossApply: IterPhyOp LogOp=Sum_Vertipaq #Records=10\n"
        "\tSpool_Iterator<SpoolIterator>: IterPhyOp #Records=10\n"
        "\t\tVertipaqResult: IterPhyOp #FieldCols=1 #ValueCols=1\n"
    )
    verdict = heuristics.analyze(parser.parse_logical_plan(plan), {}, {"result_rows": 10})
    assert not [f for f in verdict["findings"] if f["severity"] == "hot"], verdict["findings"]


# ---------------------------------------------------------------------------
# F-10: event 135 is subscribed when the trace STARTS
# ---------------------------------------------------------------------------


def test_f10_eval_log_flag_is_set_before_the_trace_starts(monkeypatch):
    import core.infrastructure.trace_session as ts
    from core.infrastructure.query_trace import NativeTraceRunner

    monkeypatch.setattr(ts, "_clr_available", lambda: True)
    monkeypatch.setattr(ts, "_load_trace_assemblies", lambda: True)
    seen = {}

    class _Session(ts.TraceSession):
        def _open_connection(self):
            pass

        def _cached_supported_events(self):
            return {}

        def _discover_supported_events(self):
            return {}

        def _start_trace(self):
            seen["flag_at_trace_start"] = self._capture_eval_log
            self._trace = object()
            self._trace_started = True

        def close(self):
            self._trace = None
            if getattr(self, "_holds_trace_lock", False):
                ts._TRACE_LOCK.release()
                self._holds_trace_lock = False

        def clear_cache(self):
            return False

        def _drain_events(self):
            return []

        def _execute_query(self, q, keep_rows=0):
            return [{"columns": [], "row_count": 0}]

        def _settle(self, ms, query=None):
            return []

        def _aggregate(self, *a, **k):
            pass

    monkeypatch.setattr(ts, "TraceSession", _Session)
    runner = NativeTraceRunner("Data Source=localhost:1")
    try:
        runner._execute_in_process(
            'EVALUATE EVALUATEANDLOG({1}, "x")',
            False,
            capture_plan=False,
            capture_eval_log=True,
            settle_ms=0,
            timeout_s=5,
        )
    except Exception:  # noqa: BLE001 - the mapping of an empty result is not under test
        pass
    assert seen.get("flag_at_trace_start") is True, seen


def test_f10_evaluate_and_log_warns_when_not_subscribed(monkeypatch):
    monkeypatch.setattr(
        qph.capturer,
        "capture",
        lambda **kw: {
            "success": True,
            "capture_id": "c1",
            "evaluation_log": [],
            "eval_log_subscribed": False,
            "se_fe_timings": {},
        },
    )
    res = qph.handle_query_plan({"operation": "evaluate_and_log", "dax_query": "EVALUATE {1}"})
    assert res["success"] is True
    assert res["eval_log_subscribed"] is False
    assert "not subscribed" in res["warning"]


# ---------------------------------------------------------------------------
# F-11: capture.daxx is an OPC package
# ---------------------------------------------------------------------------


def test_f11_daxx_has_content_types_part(tmp_path, monkeypatch):
    monkeypatch.setattr(exporter, "PERF_EXPORT_ROOT", str(tmp_path))
    cap = {
        "capture_id": "cid-daxx",
        "daxstudio": {
            "server_timings": {"TotalDuration": 10},
            "query_plan": {"PhysicalQueryPlanRows": []},
        },
    }
    out = exporter.write_exports(cap, dax_query="EVALUATE {1}")
    assert out["written"] is True
    with zipfile.ZipFile(out["daxx"]) as z:
        names = z.namelist()
        assert names[0] == "[Content_Types].xml"
        ct = z.read("[Content_Types].xml").decode("utf-8")
        query = z.read("Query.dax").decode("utf-8-sig")
    assert 'Extension="dax"' in ct and 'Extension="json"' in ct
    assert {"Query.dax", "ServerTimings.json", "QueryPlan.json"} <= set(names)
    assert query == "EVALUATE {1}"


# ---------------------------------------------------------------------------
# F-14: a batch stops at the caller's budget (no background runs)
# ---------------------------------------------------------------------------


def test_f14_batch_trace_stops_when_should_stop_fires(monkeypatch):
    from core.infrastructure.query_trace import NativeTraceRunner

    runner = NativeTraceRunner("Data Source=localhost:1")
    ran = []
    monkeypatch.setattr(runner, "in_process_available", lambda: False)
    monkeypatch.setattr(runner, "_execute_via_exe", lambda q, cc: ran.append(q) or {"total_ms": 1})
    calls = {"n": 0}

    def stop():
        calls["n"] += 1
        return calls["n"] > 1

    out = runner.execute_batch_with_trace(["Q1", "Q2", "Q3"], True, should_stop=stop)
    assert ran == ["Q1"]
    assert len(out) == 3
    assert [o.get("_error_type") for o in out[1:]] == ["cancelled", "cancelled"]


def test_f14_benchmark_traced_stops_at_the_time_budget(fake_state, monkeypatch):
    runs = []
    monkeypatch.setattr(
        cap_mod,
        "_native_capture",
        lambda dax_query, clear_cache: runs.append(clear_cache) or _native(total_ms=5),
    )
    # A clock private to the capturer: the budget is set at t=0, every later
    # check is at t=100 (> 10 s budget). The global time module is untouched.
    clock = itertools.chain([0.0], itertools.repeat(100.0))
    monkeypatch.setattr(cap_mod, "time", types.SimpleNamespace(monotonic=lambda: next(clock)))
    res = cap_mod.benchmark_traced(
        "EVALUATE {1}", cold_runs=3, warm_runs=3, warmup=0, time_budget_s=10
    )
    assert runs == []
    assert res["success"] is False  # nothing ran -> no successful runs, honestly


# ---------------------------------------------------------------------------
# F-22: cold-cache honesty + interleaved A/B
# ---------------------------------------------------------------------------


def test_f22_benchmark_traced_flags_a_cold_run_that_was_warm(fake_state, monkeypatch):
    flags = iter([True, False, True])
    monkeypatch.setattr(
        cap_mod,
        "_native_capture",
        lambda dax_query, clear_cache: _native(
            total_ms=10, cache_cleared=next(flags) if clear_cache else False
        ),
    )
    res = cap_mod.benchmark_traced("EVALUATE {1}", cold_runs=3, warm_runs=1, warmup=0)
    assert res["success"] is True
    assert res["cold_cache_honoured"] is False
    assert res["cold"]["total_ms"]["stddev_kind"] == "population"
    assert res["cold"]["summary"]["baseline_ms"] == 10


def test_f22_ab_benchmark_interleaves_and_clears_every_run(fake_state, monkeypatch):
    seen = []

    def fake(dax_query, clear_cache):
        seen.append((dax_query, clear_cache))
        return _native(total_ms=20 if dax_query == "A" else 10)

    monkeypatch.setattr(cap_mod, "_native_capture", fake)
    res = cap_mod.benchmark_ab("A", "B", runs=3, warmup=0)
    assert res["order"] == ["a", "b", "b", "a", "a", "b"]
    assert all(cc for _, cc in seen)
    assert res["faster"] == "b"
    assert res["delta_ms"] == -10
    assert res["cold_cache_honoured"] is True


def test_f22_handler_routes_compare_dax_query_to_ab(monkeypatch):
    got = {}
    monkeypatch.setattr(
        qph.capturer, "benchmark_ab", lambda **kw: got.update(kw) or {"success": True}
    )
    res = qph.handle_query_plan(
        {"operation": "benchmark", "dax_query": "A", "compare_dax_query": "B", "runs": 4}
    )
    assert res["success"] is True
    assert got["dax_query_a"] == "A" and got["dax_query_b"] == "B" and got["runs"] == 4
    assert got["time_budget_s"] > 0


# ---------------------------------------------------------------------------
# F-23: re-analysis on the largest result set; two distinct cache counts
# ---------------------------------------------------------------------------


def test_f23_plan_analysis_uses_the_largest_result_set(fake_state, monkeypatch):
    monkeypatch.setattr(
        cap_mod,
        "_native_capture",
        lambda dax_query, clear_cache: _native(
            physical_plan="AddColumns: IterPhyOp #Records=5000",
            result_sets=[{"row_count": 1}, {"row_count": 5000}],
            # the runner's own analysis used the 1-row caption set
            plan_analysis={"materialisation_ratio": 5000.0},
        ),
    )
    cap = cap_mod.capture("EVALUATE {1}")
    assert cap["se_fe_timings"]["result_rows"] == 5000
    assert cap["plan_analysis"]["materialisation_ratio"] == 1.0


def test_f23_summary_separates_cache_consumers_from_highlights():
    cap = {
        "capture_id": None,
        "physical_plan_raw": (
            "AddColumns: IterPhyOp #Records=3\n"
            "\tCache: IterPhyOp #FieldCols=1 #ValueCols=0\n"
            "\t\tVertipaqResult: IterPhyOp #FieldCols=1 #ValueCols=1\n"
        ),
        "logical_plan_raw": "",
        "se_fe_timings": {},
    }
    summary = exporter.build_capture_summary(cap, {})
    shape = summary["plan_shape"]
    assert shape["cache_consumers"] == 1
    assert shape["highlighted_rows"] == 2


# ---------------------------------------------------------------------------
# F-34: raw plans spill to disk; the store keeps a slim capture
# ---------------------------------------------------------------------------


def test_f34_remember_spills_raw_plans(tmp_path):
    cap = {
        "capture_id": "cid-spill",
        "physical_plan_raw": "AddColumns: IterPhyOp #Records=7",
        "logical_plan_raw": "AddColumns: RelLogOp",
        "daxstudio": {"server_timings": {"big": "x" * 1000}},
    }
    cap_mod.remember(cap, spill_dir=str(tmp_path / "cid-spill"))
    stored = cap_mod.recall("cid-spill")
    assert stored["physical_plan_raw"] is None and stored["logical_plan_raw"] is None
    assert "daxstudio" not in stored
    assert cap_mod.plan_text(stored, "physical_plan_raw") == "AddColumns: IterPhyOp #Records=7"
    first = cap_mod.parsed_plan(stored, "physical")
    assert cap_mod.parsed_plan(stored, "physical") is first  # parsed once, cached
    assert first.max_records == 7
