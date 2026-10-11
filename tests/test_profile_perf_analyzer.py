"""Tests for the Performance Analyzer lanes on 09_Profile (WP-D).

Every trace goes through a fake NativeTraceRunner, so no live Power BI Desktop
is required. Covers:

- profile_page(trace=True): warm-up discarded, FASTEST measured run kept
- one trace session for the whole page (batched), not one per visual
- the K-slot simulation wiring and that its wait is labelled an estimate
- skipped[] for build failures and for queries that fail under trace
- perf_analyzer_import: parsing, "Other" as the residual, visual-id resolution
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.debug.debug_operations import DebugOperations

VISUALS = [
    {"id": "matrix1", "type": "pivotTable", "friendly_name": "P&L matrix", "is_slicer": False},
    {"id": "card1", "type": "card", "friendly_name": "Period card", "is_slicer": False},
    {"id": "slicer1", "type": "slicer", "friendly_name": "Entity", "is_slicer": True},
]


class FakeRunner:
    """Records every batch call and replays scripted trace results.

    ``script`` maps a query string to the list of per-pass results it should
    return; pass 0 is the warm-up.
    """

    def __init__(self, script: dict):
        self.script = script
        self.batch_calls: list[list[str]] = []

    def execute_batch_with_trace(self, queries, clear_cache=True, **kwargs):
        # F-35: every pass now runs in ONE batch (one trace session); the n-th
        # occurrence of a query in the batch is its n-th pass.
        self.batch_calls.append(list(queries))
        seen: dict = {}
        out = []
        for q in queries:
            k = seen.get(q, 0)
            seen[q] = k + 1
            out.append(self.script[q][k])
        return out


def _trace_result(total_ms, se_ms=100, se_queries=5, cache_hits=2, callbacks=0, peak_kb=1024):
    return {
        "total_ms": total_ms,
        "fe_ms": total_ms - se_ms,
        "se_ms": se_ms,
        "se_queries": se_queries,
        "se_cache_hits": cache_hits,
        "se_events": [{"has_callback": True} for _ in range(callbacks)],
        "execution_metrics": {"peak_memory_kb": peak_kb, "result_rows": 8},
        "result_sets": [{"row_count": 8}],
    }


def _make_ops(builder, runner=None, runner_error=None):
    ops = DebugOperations.__new__(DebugOperations)
    ops.builder = builder
    ops.qe = MagicMock()
    ops.logger = MagicMock()
    ops._get_trace_runner = lambda: (runner, runner_error)
    return ops


def _builder_for(queries: dict, visuals=None):
    builder = MagicMock()
    builder.list_visuals.return_value = list(visuals if visuals is not None else VISUALS)

    def _build(page_name, visual_id, include_slicers):
        query = queries.get(visual_id)
        if query is None:
            return None
        return SimpleNamespace(
            dax_query=query,
            filter_context=SimpleNamespace(all_filters=lambda: []),
        )

    builder.build_visual_query.side_effect = _build
    return builder


Q_MATRIX = 'EVALUATE ROW("matrix", 1)'
Q_CARD = 'EVALUATE ROW("card", 1)'


# ---------------------------------------------------------------------------
# baseline protocol
# ---------------------------------------------------------------------------


class TestBaselineProtocol:
    def test_warmup_is_discarded_and_fastest_run_is_kept(self):
        # Warm-up is by far the slowest; if it leaked in, dax_ms would be 5000.
        script = {
            Q_MATRIX: [_trace_result(5000), _trace_result(900), _trace_result(700)],
            Q_CARD: [_trace_result(400), _trace_result(120), _trace_result(150)],
        }
        runner = FakeRunner(script)
        ops = _make_ops(_builder_for({"matrix1": Q_MATRIX, "card1": Q_CARD}), runner)

        result = ops.profile_page("Page1", iterations=2, trace=True)

        by_name = {r["visual"]: r for r in result["results"]}
        assert by_name["P&L matrix"]["dax_ms"] == 700
        assert by_name["Period card"]["dax_ms"] == 120

    def test_baseline_block_states_the_protocol(self):
        script = {Q_MATRIX: [_trace_result(300), _trace_result(200), _trace_result(210)]}
        ops = _make_ops(_builder_for({"matrix1": Q_MATRIX}), FakeRunner(script))

        result = ops.profile_page("Page1", iterations=2, trace=True)

        assert result["baseline"] == {
            "warmup_runs_discarded": 1,
            "measured_runs_per_visual": 2,
            "kept": "fastest",
            "clear_cache": True,
        }
        assert result["results"][0]["runs_measured"] == 2
        assert result["results"][0]["min_ms"] == 200
        assert result["results"][0]["max_ms"] == 210

    def test_one_batch_per_pass_not_one_trace_per_visual(self):
        script = {
            Q_MATRIX: [_trace_result(300)] * 3,
            Q_CARD: [_trace_result(100)] * 3,
        }
        runner = FakeRunner(script)
        ops = _make_ops(_builder_for({"matrix1": Q_MATRIX, "card1": Q_CARD}), runner)

        ops.profile_page("Page1", iterations=2, trace=True)

        # F-35: 1 warm-up + 2 measured passes in ONE batch (one trace session),
        # pass by pass, each pass carrying BOTH visuals' queries.
        assert len(runner.batch_calls) == 1
        assert runner.batch_calls[0] == [Q_MATRIX, Q_CARD] * 3

    def test_iterations_zero_still_measures_one_run(self):
        script = {Q_MATRIX: [_trace_result(500), _trace_result(250)]}
        ops = _make_ops(_builder_for({"matrix1": Q_MATRIX}), FakeRunner(script))

        result = ops.profile_page("Page1", iterations=0, trace=True)

        assert result["baseline"]["measured_runs_per_visual"] == 1
        assert result["results"][0]["dax_ms"] == 250


# ---------------------------------------------------------------------------
# the simulation
# ---------------------------------------------------------------------------


class TestWaitSimulation:
    def test_single_slot_serialises_and_produces_wait(self):
        script = {
            Q_MATRIX: [_trace_result(1000)] * 2,
            Q_CARD: [_trace_result(400)] * 2,
        }
        ops = _make_ops(_builder_for({"matrix1": Q_MATRIX, "card1": Q_CARD}), FakeRunner(script))

        result = ops.profile_page("Page1", iterations=1, trace=True, simulate_concurrency=1)

        by_name = {r["visual"]: r for r in result["results"]}
        # Longest first on the one slot: the matrix waits for nothing, the card
        # waits the matrix out. That wait IS the "Other" estimate.
        assert by_name["P&L matrix"]["other_wait_est_ms"] == 0
        assert by_name["Period card"]["other_wait_est_ms"] == 1000
        assert by_name["Period card"]["total_est_ms"] == 1400
        assert result["page_estimate"]["est_page_ms"] == 1400

    def test_enough_slots_means_no_wait(self):
        script = {
            Q_MATRIX: [_trace_result(1000)] * 2,
            Q_CARD: [_trace_result(400)] * 2,
        }
        ops = _make_ops(_builder_for({"matrix1": Q_MATRIX, "card1": Q_CARD}), FakeRunner(script))

        result = ops.profile_page("Page1", iterations=1, trace=True, simulate_concurrency=6)

        assert all(r["other_wait_est_ms"] == 0 for r in result["results"])
        assert result["page_estimate"]["est_page_ms"] == 1000
        assert result["page_estimate"]["critical_path_ms"] == 1000

    def test_wait_is_labelled_an_estimate(self):
        script = {Q_MATRIX: [_trace_result(300)] * 2}
        ops = _make_ops(_builder_for({"matrix1": Q_MATRIX}), FakeRunner(script))

        result = ops.profile_page("Page1", iterations=1, trace=True)

        assert "ESTIMATED" in result["estimate_note"]
        assert "does not publish" in result["estimate_note"]
        # The column name itself carries the caveat.
        assert "other_wait_est_ms" in result["results"][0]

    def test_what_if_offers_alternative_slot_counts(self):
        script = {
            Q_MATRIX: [_trace_result(1000)] * 2,
            Q_CARD: [_trace_result(400)] * 2,
        }
        ops = _make_ops(_builder_for({"matrix1": Q_MATRIX, "card1": Q_CARD}), FakeRunner(script))

        result = ops.profile_page("Page1", iterations=1, trace=True)

        what_if = result["page_estimate"]["what_if"]
        assert what_if["k=1"] == 1400  # fully serial
        assert what_if["k=2"] == 1000  # bounded by the slowest query
        assert what_if["without_slowest_visual"]["visual"] == "P&L matrix"


class TestTracedMetrics:
    def test_se_split_callbacks_and_peak_memory_are_surfaced(self):
        script = {
            Q_MATRIX: [
                _trace_result(
                    1000, se_ms=250, se_queries=189, cache_hits=110, callbacks=3, peak_kb=223127
                )
            ]
            * 2
        }
        ops = _make_ops(_builder_for({"matrix1": Q_MATRIX}), FakeRunner(script))

        row = ops.profile_page("Page1", iterations=1, trace=True)["results"][0]

        assert row["se_ms"] == 250
        assert row["fe_ms"] == 750
        assert row["se_pct"] == 25.0
        assert row["se_queries"] == 189
        assert row["cache_hits"] == 110
        assert row["callbacks"] == 3
        assert row["peak_mem_kb"] == 223127
        assert row["row_count"] == 8

    def test_callback_visuals_are_called_out(self):
        script = {Q_MATRIX: [_trace_result(1000, callbacks=2)] * 2}
        ops = _make_ops(_builder_for({"matrix1": Q_MATRIX}), FakeRunner(script))

        result = ops.profile_page("Page1", iterations=1, trace=True)

        assert any("callback" in r.lower() for r in result["recommendations"])

    def test_table_is_sorted_by_total(self):
        script = {
            Q_MATRIX: [_trace_result(200)] * 2,
            Q_CARD: [_trace_result(900)] * 2,
        }
        ops = _make_ops(_builder_for({"matrix1": Q_MATRIX, "card1": Q_CARD}), FakeRunner(script))

        result = ops.profile_page("Page1", iterations=1, trace=True, simulate_concurrency=1)

        totals = [r["total_est_ms"] for r in result["results"]]
        assert totals == sorted(totals, reverse=True)


# ---------------------------------------------------------------------------
# skipped[]
# ---------------------------------------------------------------------------


class TestSkipped:
    def test_unbuildable_visual_is_skipped_with_a_reason(self):
        script = {Q_CARD: [_trace_result(100)] * 2}
        # matrix1 has no query -> build returns None
        ops = _make_ops(_builder_for({"card1": Q_CARD}), FakeRunner(script))

        result = ops.profile_page("Page1", iterations=1, trace=True)

        assert result["visuals_profiled"] == 1
        assert result["visuals_skipped"] == 1
        assert result["skipped"][0]["visual_id"] == "matrix1"
        assert result["skipped"][0]["reason"] == "query_not_built"

    def test_query_failing_under_trace_is_skipped_with_the_engine_error(self):
        script = {
            Q_MATRIX: [{"_error": "The column 'X' could not be found"}] * 2,
            Q_CARD: [_trace_result(100)] * 2,
        }
        ops = _make_ops(_builder_for({"matrix1": Q_MATRIX, "card1": Q_CARD}), FakeRunner(script))

        result = ops.profile_page("Page1", iterations=1, trace=True)

        assert result["visuals_profiled"] == 1
        skipped = result["skipped"][0]
        assert skipped["reason"] == "query_failed"
        assert "could not be found" in skipped["detail"]

    def test_build_exception_is_reported(self):
        builder = MagicMock()
        builder.list_visuals.return_value = [VISUALS[0]]
        builder.build_visual_query.side_effect = UnboundLocalError("boom")
        ops = _make_ops(builder, FakeRunner({}))

        result = ops.profile_page("Page1", iterations=1, trace=True)

        assert result["visuals_profiled"] == 0
        assert result["skipped"][0]["reason"] == "exception"
        assert "UnboundLocalError" in result["skipped"][0]["detail"]

    def test_no_trace_lane_is_an_error_not_a_silent_wall_clock_fallback(self):
        ops = _make_ops(
            _builder_for({"matrix1": Q_MATRIX}), None, runner_error="No trace lane available"
        )

        result = ops.profile_page("Page1", iterations=1, trace=True)

        assert result["success"] is False
        assert "No trace lane" in result["error"]

    def test_per_visual_limit_caps_the_page(self):
        script = {Q_MATRIX: [_trace_result(300)] * 2}
        runner = FakeRunner(script)
        ops = _make_ops(_builder_for({"matrix1": Q_MATRIX, "card1": Q_CARD}), runner)

        result = ops.profile_page("Page1", iterations=1, trace=True, per_visual_limit=1)

        assert result["data_visuals_found"] == 1
        assert runner.batch_calls[0] == [Q_MATRIX, Q_MATRIX]  # warm-up + 1 measured


def test_trace_false_keeps_the_wall_clock_shape():
    """The default lane must stay exactly as it was — callers depend on it."""
    builder = _builder_for({"matrix1": Q_MATRIX, "card1": Q_CARD})
    ops = DebugOperations.__new__(DebugOperations)
    ops.builder = builder
    ops.logger = MagicMock()
    ops.qe = MagicMock()
    ops.qe.validate_and_execute_dax.return_value = {
        "success": True,
        "execution_time_ms": 12.0,
        "rows": [{"a": 1}],
    }

    result = ops.profile_page("Page1", iterations=1)

    assert result["visuals_profiled"] == 2
    assert "mode" not in result
    assert "page_estimate" not in result
    assert result["results"][0]["avg_time_ms"] == 12.0


# ---------------------------------------------------------------------------
# perf_analyzer_import
# ---------------------------------------------------------------------------


PERF_EXPORT = {
    "version": "1.2",
    "events": [
        {
            "name": "Visual Container Lifecycle",
            "start": 0,
            "end": 1311,
            "metrics": {
                "visualTitle": "P&L matrix",
                "visualId": "matrix1",
                "visualType": "pivotTable",
            },
            "children": [
                {
                    "name": "Query",
                    "start": 10,
                    "end": 1080,
                    "children": [
                        {"name": "Query Generation", "start": 10, "end": 15},
                        {"name": "Query Pending", "start": 15, "end": 27},
                        {
                            "name": "Execute Semantic Query",
                            "start": 27,
                            "end": 1080,
                            "children": [
                                {
                                    "name": "Execute DAX Query",
                                    "start": 27,
                                    "end": 1080,
                                    "metrics": {"QueryText": Q_MATRIX, "RowCount": 8},
                                }
                            ],
                        },
                    ],
                },
                {"name": "Render", "start": 1080, "end": 1111},
            ],
        },
        {
            "name": "Visual Container Lifecycle",
            "start": 0,
            "end": 300,
            "metrics": {"visualId": "card1", "visualType": "card"},
            "children": [
                {
                    "name": "Query",
                    "start": 5,
                    "end": 200,
                    "children": [
                        {
                            "name": "Execute DAX Query",
                            "start": 10,
                            "end": 200,
                            "metrics": {"QueryText": Q_CARD, "RowCount": 1},
                        }
                    ],
                },
                {"name": "Render", "start": 200, "end": 250},
            ],
        },
    ],
}


@pytest.fixture
def export_file(tmp_path):
    path = tmp_path / "PowerBIPerformanceData.json"
    path.write_text(json.dumps(PERF_EXPORT), encoding="utf-8")
    return str(path)


class TestPerfAnalyzerImport:
    def test_parses_every_visual(self, export_file):
        ops = _make_ops(MagicMock())

        result = ops.perf_analyzer_import(export_file)

        assert result["success"] is True
        assert result["visuals"] == 2
        assert {r["visual"] for r in result["results"]} == {"P&L matrix", "card1"}

    def test_other_is_the_measured_residual(self, export_file):
        ops = _make_ops(MagicMock())

        rows = {r["visual"]: r for r in ops.perf_analyzer_import(export_file)["results"]}

        # 1311 lifecycle - 1053 DAX - 31 render = 227 of pure waiting.
        assert rows["P&L matrix"]["dax_ms"] == 1053
        assert rows["P&L matrix"]["display_ms"] == 31
        assert rows["P&L matrix"]["other_ms"] == 227

    def test_query_pending_is_reported_separately(self, export_file):
        ops = _make_ops(MagicMock())

        rows = {r["visual"]: r for r in ops.perf_analyzer_import(export_file)["results"]}

        assert rows["P&L matrix"]["query_pending_ms"] == 12

    def test_note_says_measured_not_estimated(self, export_file):
        ops = _make_ops(MagicMock())

        result = ops.perf_analyzer_import(export_file)

        assert "MEASURED" in result["note"]
        assert not any(r.get("estimated") for r in result["results"])

    def test_missing_file_is_an_error(self, tmp_path):
        ops = _make_ops(MagicMock())

        result = ops.perf_analyzer_import(str(tmp_path / "nope.json"))

        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_invalid_json_is_an_error(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        ops = _make_ops(MagicMock())

        result = ops.perf_analyzer_import(str(path))

        assert result["success"] is False
        assert "JSON" in result["error"]

    def test_wrong_file_shape_names_the_right_export(self, tmp_path):
        path = tmp_path / "other.json"
        path.write_text(json.dumps({"events": [{"name": "Something Else"}]}), encoding="utf-8")
        ops = _make_ops(MagicMock())

        result = ops.perf_analyzer_import(str(path))

        assert result["success"] is False
        assert "Performance Analyzer" in result["error"]

    def test_trace_attaches_se_fe_per_visual(self, export_file):
        script = {
            Q_MATRIX: [_trace_result(1053, se_ms=300, se_queries=189)],
            Q_CARD: [_trace_result(190, se_ms=40, se_queries=3)],
        }
        runner = FakeRunner(script)
        ops = _make_ops(MagicMock(), runner)

        result = ops.perf_analyzer_import(export_file, trace=True)

        assert result["traced"] == 2
        rows = {r["visual"]: r for r in result["results"]}
        assert rows["P&L matrix"]["se_ms"] == 300
        assert rows["P&L matrix"]["se_queries"] == 189
        # One batch, both queries — not a trace session per visual.
        assert runner.batch_calls == [[Q_MATRIX, Q_CARD]]

    def test_trace_without_a_lane_reports_the_reason(self, export_file):
        ops = _make_ops(MagicMock(), None, runner_error="No trace lane available")

        result = ops.perf_analyzer_import(export_file, trace=True)

        assert result["success"] is True  # the parse still succeeded
        assert result["traced"] == 0
        assert "No trace lane" in result["trace_errors"][0]["error"]


class TestVisualResolution:
    def test_ids_resolve_to_pbir_names(self, export_file, monkeypatch):
        builder = MagicMock()
        builder.list_pages.return_value = [{"name": "Summary"}]
        builder.list_visuals.return_value = [
            {"id": "card1", "friendly_name": "Period card", "type": "card"},
        ]
        monkeypatch.setattr(
            "core.debug.visual_query_builder.VisualQueryBuilder",
            lambda path: builder,
        )
        ops = _make_ops(MagicMock())

        result = ops.perf_analyzer_import(export_file, pbip_path="C:/fake.pbip")

        assert result["visual_resolution"]["resolved"] == 1
        assert result["visual_resolution"]["unresolved"] == 1
        rows = {r["visual"] for r in result["results"]}
        assert "Period card" in rows

    def test_exported_title_wins_over_pbir_name(self, export_file, monkeypatch):
        # The pane title is what the user actually saw; PBIR only fills the gaps.
        builder = MagicMock()
        builder.list_pages.return_value = [{"name": "Summary"}]
        builder.list_visuals.return_value = [
            {"id": "matrix1", "friendly_name": "Renamed in PBIR", "type": "pivotTable"},
        ]
        monkeypatch.setattr(
            "core.debug.visual_query_builder.VisualQueryBuilder",
            lambda path: builder,
        )
        ops = _make_ops(MagicMock())

        result = ops.perf_analyzer_import(export_file, pbip_path="C:/fake.pbip")

        assert {r["visual"] for r in result["results"]} >= {"P&L matrix"}

    def test_unreadable_pbip_does_not_kill_the_parse(self, export_file, monkeypatch):
        def _boom(path):
            raise FileNotFoundError("no such PBIP")

        monkeypatch.setattr("core.debug.visual_query_builder.VisualQueryBuilder", _boom)
        ops = _make_ops(MagicMock())

        result = ops.perf_analyzer_import(export_file, pbip_path="C:/missing.pbip")

        assert result["success"] is True
        assert result["visuals"] == 2
        assert "error" in result["visual_resolution"]


class TestHandlerWiring:
    def test_perf_analyzer_import_requires_a_file_path(self):
        from server.handlers._debug.tools import handle_profile

        result = handle_profile({"operation": "perf_analyzer_import"})

        assert result["success"] is False
        assert "file_path" in result["error"]

    def test_schema_advertises_the_new_op_and_args(self):
        from server.tool_schemas import TOOL_SCHEMAS

        schema = TOOL_SCHEMAS["profile_operations"]
        assert "perf_analyzer_import" in schema["properties"]["operation"]["enum"]
        for arg in (
            "trace",
            "clear_cache",
            "simulate_concurrency",
            "per_visual_limit",
            "file_path",
            "pbip_path",
        ):
            assert arg in schema["properties"]
