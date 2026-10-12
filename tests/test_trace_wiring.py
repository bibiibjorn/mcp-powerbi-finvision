"""Tests for trace data that is captured in C# but was dropped before the caller.

Covers four wirings:

1. ``agg_rewrites`` — AggregateTableRewriteQuery events surfaced + classified.
2. ``timeline``    — full waterfall incl. synthetic FE rows and their offsets,
                     with ``se_events`` unchanged.
3. ``se_analysis`` — attached to 04_Run_DAX mode=trace, matching 09_Debug.
4. ``with_clauses``— parsed xmSQL WITH expressions in SeEventAnalyzer.analyze().

Every case is mocked at the subprocess / manager boundary — no AMO, CLR or
network is ever touched. The "old exe" cases assert graceful defaults when the
prebuilt DaxExecutor.exe returns JSON without the new keys.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from core.dax.se_event_analyzer import SeEventAnalyzer
from core.infrastructure.query_trace import NativeTraceRunner

# --- fixtures / builders -------------------------------------------------


def _se_event(line: int, duration: int = 10, query: str = "SELECT 1") -> dict:
    """One C#-shaped SE EventDetail with Timeline data."""
    return {
        "Line": line,
        "Class": "SE",
        "Subclass": "Scan",
        "Duration": duration,
        "CPU": duration * 2,
        "Par": 2.0,
        "Rows": 100,
        "KB": 8,
        "Query": query,
        "Timeline": {"StartOffset": float(line * 10), "EndOffset": float(line * 10 + duration)},
    }


def _fe_event(line: int, start: float, end: float) -> dict:
    """One C#-shaped synthetic FE gap row."""
    return {
        "Line": line,
        "Class": "FE",
        "Subclass": "Formula Engine",
        "Duration": end - start,
        "CPU": 0,
        "Par": 1.0,
        "Rows": 0,
        "KB": 0,
        "Query": "Formula Engine processing",
        "Timeline": {"StartOffset": start, "EndOffset": end},
    }


def _response(**overrides) -> dict:
    """A full C# DaxExecutor response with the new keys present."""
    base = {
        "Performance": {
            "Total": 100,
            "FE": 40,
            "SE": 60,
            "SE_CPU": 120,
            "SE_Par": 2.0,
            "SE_Queries": 2,
            "SE_Cache": 1,
        },
        "EventDetails": [
            _se_event(1, duration=20),
            _fe_event(2, 30.0, 75.0),
            _se_event(3, duration=25),
        ],
        "AggRewrites": [
            {"Status": "matched", "Text": "agg hit", "Subclass": "1"},
        ],
        "LogicalPlan": "logical",
        "PhysicalPlan": "physical",
        "CacheCleared": True,
    }
    base.update(overrides)
    return base


# --- (1) agg_rewrites ----------------------------------------------------


class TestAggRewrites:
    def test_agg_rewrites_surfaced(self):
        mapped = NativeTraceRunner._map_response(_response())
        assert mapped["agg_rewrites"] == [{"status": "matched", "text": "agg hit", "subclass": "1"}]

    @pytest.mark.parametrize(
        "subclass,expected",
        [
            (1, "matched"),  # integer form (live CLR trace)
            (2, "nomatch"),
            (7, "attempted"),
            ("1", "matched"),  # numeric-string form (serialized JSON)
            ("2", "nomatch"),
            ("99", "attempted"),
            ("MatchFound", "matched"),  # string form (Profiler)
            ("NoMatch", "nomatch"),
            ("MissingColumn", "attempted"),
            ("", "attempted"),
        ],
    )
    def test_subclass_classified_when_status_absent(self, subclass, expected):
        """A response without a C#-side Status is classified in Python."""
        resp = _response(AggRewrites=[{"Text": "t", "Subclass": subclass}])
        mapped = NativeTraceRunner._map_response(resp)
        assert mapped["agg_rewrites"][0]["status"] == expected
        assert mapped["agg_rewrites"][0]["subclass"] == subclass

    def test_capped_at_50(self):
        resp = _response(
            AggRewrites=[{"Status": "matched", "Text": "t", "Subclass": "1"} for _ in range(120)]
        )
        assert len(NativeTraceRunner._map_response(resp)["agg_rewrites"]) == 50

    def test_text_truncated(self):
        resp = _response(AggRewrites=[{"Status": "nomatch", "Text": "x" * 900, "Subclass": "2"}])
        assert len(NativeTraceRunner._map_response(resp)["agg_rewrites"][0]["text"]) == 500

    def test_non_list_agg_rewrites_ignored(self):
        resp = _response(AggRewrites="not-a-list")
        assert NativeTraceRunner._map_response(resp)["agg_rewrites"] == []

    def test_non_dict_entries_skipped(self):
        resp = _response(AggRewrites=["junk", {"Status": "matched", "Text": "", "Subclass": "1"}])
        mapped = NativeTraceRunner._map_response(resp)
        assert len(mapped["agg_rewrites"]) == 1


# --- (2) timeline --------------------------------------------------------


class TestTimeline:
    def test_timeline_includes_fe_rows_with_offsets(self):
        mapped = NativeTraceRunner._map_response(_response())
        timeline = mapped["timeline"]

        assert [row["class"] for row in timeline] == ["SE", "FE", "SE"]

        fe = timeline[1]
        assert fe["subclass"] == "Formula Engine"
        assert fe["start_offset_ms"] == 30.0
        assert fe["end_offset_ms"] == 75.0
        assert fe["duration_ms"] == 45.0
        assert fe["rows"] == 0

    def test_timeline_row_shape(self):
        row = NativeTraceRunner._map_response(_response())["timeline"][0]
        assert set(row) == {
            "line",
            "class",
            "subclass",
            "duration_ms",
            "cpu_ms",
            "rows",
            "kb",
            "start_offset_ms",
            "end_offset_ms",
        }

    def test_se_events_unchanged_when_timeline_added(self):
        """se_events stays SE-only, renumbered 1..N, with its original keys."""
        mapped = NativeTraceRunner._map_response(_response())
        se = mapped["se_events"]

        assert len(se) == 2  # the FE row is excluded
        assert [e["line"] for e in se] == [1, 2]  # own numbering, not C# Line
        assert set(se[0]) == {
            "line",
            "duration_ms",
            "cpu_ms",
            "parallelism",
            "rows",
            "kb",
            "has_callback",
            "query",
        }
        assert se[0]["duration_ms"] == 20
        assert se[1]["duration_ms"] == 25

    def test_has_callback_still_detected(self):
        resp = _response(
            EventDetails=[_se_event(1, query="WITH $Expr0 := ( CallbackDataID ( x ) )")]
        )
        assert NativeTraceRunner._map_response(resp)["se_events"][0]["has_callback"] is True

    def test_empty_events_gives_empty_timeline(self):
        mapped = NativeTraceRunner._map_response(_response(EventDetails=[]))
        assert mapped["timeline"] == []
        assert mapped["se_events"] == []


# --- old-exe graceful defaults -------------------------------------------


class TestOldExeCompatibility:
    """The prebuilt March exe emits no AggRewrites and (for FE rows) no Timeline."""

    def _old_response(self) -> dict:
        return {
            "Performance": {"Total": 50, "FE": 20, "SE": 30},
            "EventDetails": [
                # No Timeline key at all — the pre-timeline event shape.
                {
                    "Line": 1,
                    "Class": "SE",
                    "Subclass": "Scan",
                    "Duration": 30,
                    "CPU": 60,
                    "Par": 2.0,
                    "Rows": 5,
                    "KB": 1,
                    "Query": "SELECT 1",
                },
                {"Line": 2, "Class": "FE", "Subclass": "Formula Engine", "Duration": 20},
            ],
            "CacheCleared": False,
        }

    def test_missing_aggrewrites_defaults_to_empty_list(self):
        mapped = NativeTraceRunner._map_response(self._old_response())
        assert mapped["agg_rewrites"] == []

    def test_missing_timeline_defaults_offsets_to_none(self):
        mapped = NativeTraceRunner._map_response(self._old_response())
        assert len(mapped["timeline"]) == 2
        for row in mapped["timeline"]:
            assert row["start_offset_ms"] is None
            assert row["end_offset_ms"] is None

    def test_missing_fields_default_without_keyerror(self):
        """A bare FE row with no CPU/Rows/KB still maps."""
        mapped = NativeTraceRunner._map_response(self._old_response())
        fe = mapped["timeline"][1]
        assert fe["class"] == "FE"
        assert fe["cpu_ms"] == 0
        assert fe["rows"] == 0
        assert fe["kb"] == 0

    def test_null_timeline_value_tolerated(self):
        resp = _response(EventDetails=[dict(_se_event(1), Timeline=None)])
        row = NativeTraceRunner._map_response(resp)["timeline"][0]
        assert row["start_offset_ms"] is None

    def test_existing_keys_preserved(self):
        """Backwards compatibility: every pre-existing key is still returned."""
        mapped = NativeTraceRunner._map_response(self._old_response())
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
            "se_events",
            "logical_plan",
            "physical_plan",
            "cache_cleared",
        ):
            assert key in mapped

    def test_end_to_end_through_subprocess_boundary(self, monkeypatch):
        """Old-exe JSON on stdout still maps cleanly — no KeyError anywhere."""
        proc = MagicMock()
        proc.stdout = json.dumps(self._old_response())
        proc.stderr = ""
        monkeypatch.setattr("core.infrastructure.query_trace.subprocess.run", lambda *a, **k: proc)

        runner = NativeTraceRunner.__new__(NativeTraceRunner)
        runner._connection_string = "localhost:1234"
        runner._exe_path = r"C:\fake\DaxExecutor.exe"

        result = runner.execute_with_trace('EVALUATE ROW("a", 1)')
        assert "_error" not in result
        assert result["agg_rewrites"] == []
        assert len(result["timeline"]) == 2


# --- (3) se_analysis on 04_Run_DAX mode=trace ----------------------------


class TestRunDaxTraceSeAnalysis:
    def _run(self, monkeypatch, trace_result: dict) -> dict:
        import server.handlers.query_handler as qh

        state = MagicMock()
        state.is_connected.return_value = True
        state.connection_manager.connection_string = "localhost:1234"
        monkeypatch.setattr(qh, "connection_state", state)

        runner = MagicMock()
        runner.execute_with_trace.return_value = trace_result
        fake_cls = MagicMock(return_value=runner)
        fake_cls.is_available.return_value = True
        monkeypatch.setattr("core.infrastructure.query_trace.NativeTraceRunner", fake_cls)

        return qh.handle_run_dax_trace({"query": "EVALUATE Sales"})

    def test_se_analysis_present(self, monkeypatch):
        result = self._run(
            monkeypatch,
            {
                "total_ms": 100,
                "fe_ms": 40,
                "se_ms": 60,
                "se_events": [
                    {
                        "line": 1,
                        "duration_ms": 60,
                        "cpu_ms": 120,
                        "rows": 1000,
                        "kb": 8,
                        "query": "WITH $Expr0 := ( CallbackDataID ( 1 ) ) SELECT x",
                    }
                ],
            },
        )
        assert result["success"] is True
        assert "se_analysis" in result
        analysis = result["se_analysis"]
        assert analysis["callbacks"]["detected"] is True
        # Same section set as the 09_Debug_Operations surface.
        for section in (
            "callbacks",
            "datacache",
            "timing_distribution",
            "fusion_opportunities",
            "join_analysis",
            "row_ratio_analysis",
        ):
            assert section in analysis

    def test_se_analysis_absent_when_no_events(self, monkeypatch):
        result = self._run(monkeypatch, {"total_ms": 5, "se_events": []})
        assert result["success"] is True
        assert "se_analysis" not in result

    def test_analyzer_failure_never_breaks_response(self, monkeypatch):
        import core.dax.se_event_analyzer as sea

        def _boom(*a, **k):
            raise RuntimeError("analyzer exploded")

        monkeypatch.setattr(sea.SeEventAnalyzer, "analyze", _boom)
        result = self._run(
            monkeypatch,
            {"total_ms": 10, "se_events": [{"line": 1, "duration_ms": 10, "query": "x"}]},
        )
        assert result["success"] is True
        assert "se_analysis" not in result
        assert "performance" in result

    def test_debug_helper_surface_matches(self):
        """09_Debug_Operations keeps returning se_analysis with the same key."""
        from server.handlers._debug.helpers import _build_trace_response

        response = _build_trace_response(
            {
                "total_ms": 10,
                "se_events": [{"line": 1, "duration_ms": 10, "rows": 1, "query": "SELECT 1"}],
            }
        )
        assert "se_analysis" in response


# --- (4) with_clauses in analyze() ---------------------------------------


class TestWithClauses:
    def test_with_clauses_present(self):
        events = [
            {
                "line": 1,
                "duration_ms": 10,
                "rows": 5,
                "query": "WITH $Expr0 := ( CallbackDataID ( PFDATAID ) ) SELECT a",
            }
        ]
        analysis = SeEventAnalyzer().analyze(events, {"total_ms": 10})
        assert "with_clauses" in analysis
        assert analysis["with_clauses"] == [
            {
                "line": 1,
                "expressions": [
                    {
                        "expr_name": "Expr0",
                        "callback_type": "CallbackDataID",
                        "arguments": "PFDATAID",
                    }
                ],
            }
        ]

    def test_events_without_with_clause_omitted(self):
        events = [
            {"line": 1, "duration_ms": 5, "query": "SELECT SUM(a) FROM t"},
            {"line": 2, "duration_ms": 5, "query": "WITH $Expr1 := ( Cond ( b ) ) SELECT b"},
        ]
        analysis = SeEventAnalyzer().analyze(events, {"total_ms": 10})
        assert [c["line"] for c in analysis["with_clauses"]] == [2]

    def test_with_clauses_empty_for_plain_trace(self):
        analysis = SeEventAnalyzer().analyze(
            [{"line": 1, "duration_ms": 5, "query": "SELECT a FROM t"}], {"total_ms": 5}
        )
        assert analysis["with_clauses"] == []

    def test_with_clauses_capped(self):
        events = [
            {
                "line": i,
                "duration_ms": 1,
                "query": "WITH $Expr0 := ( Cond ( x ) ) SELECT x",
            }
            for i in range(1, 40)
        ]
        analysis = SeEventAnalyzer().analyze(events, {"total_ms": 40})
        assert len(analysis["with_clauses"]) == 10

    def test_existing_analyze_sections_unchanged(self):
        analysis = SeEventAnalyzer().analyze(
            [{"line": 1, "duration_ms": 5, "rows": 10, "kb": 1, "query": "SELECT a"}],
            {"total_ms": 5},
        )
        for section in (
            "callbacks",
            "datacache",
            "timing_distribution",
            "fusion_opportunities",
            "join_analysis",
            "row_ratio_analysis",
        ):
            assert section in analysis

    def test_no_events_short_circuit_unchanged(self):
        assert SeEventAnalyzer().analyze([], {}) == {"note": "No SE events provided"}
