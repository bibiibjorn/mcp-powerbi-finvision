"""Parity tests against a real DAX Studio Server Timings export.

The export's own ``StorageEngineEvents`` are converted back into raw trace
events and fed through :func:`process_results`.  Reproducing DAX Studio's
headline numbers from its own inputs is the strongest available proof that the
algorithm matches - and the numbers are not reachable by accident: naively
summing the event durations gives 81 ms and 64 ms CPU where DAX Studio reports
100 ms and 32 ms.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from pbi_perf_core.server_timings import (
    TraceEvent,
    _compute_formula_engine_ms,
    process_results,
)

# Headline values of the reference capture (see conftest docstring).
EXPECTED_TOTAL = 1428
EXPECTED_FE = 1328
EXPECTED_SE = 100
EXPECTED_SE_NET = 100
EXPECTED_SE_CPU = 32
EXPECTED_SE_QUERIES = 189
EXPECTED_CACHE = 110


def _events_from_export(export: dict) -> list[TraceEvent]:
    """Rebuild raw trace events from exported rows, in arrival order."""
    events: list[TraceEvent] = []

    query_start = _dt.datetime.fromisoformat(export["QueryStartDateTime"])
    query_end = _dt.datetime.fromisoformat(export["QueryEndDateTime"])

    events.append(
        TraceEvent(
            event_class="QueryBegin",
            text_data=export.get("CommandText", ""),
            start_time=query_start,
            activity_id=export.get("ActivityID"),
        )
    )

    for row in sorted(export["StorageEngineEvents"], key=lambda r: r["RowNumber"]):
        start = row.get("StartTime")
        end = row.get("EndTime")
        events.append(
            TraceEvent(
                event_class=row["Class"],
                event_subclass=row.get("Subclass"),
                text_data=row.get("TextData", ""),
                start_time=_dt.datetime.fromisoformat(start) if start else None,
                end_time=_dt.datetime.fromisoformat(end) if end else None,
                current_time=_dt.datetime.fromisoformat(start) if start else None,
                duration_ms=row.get("Duration", 0) or 0,
                cpu_time_ms=row.get("CpuTime", 0) or 0,
                activity_id=export.get("ActivityID"),
            )
        )

    events.append(
        TraceEvent(
            event_class="QueryEnd",
            text_data=export.get("CommandText", ""),
            start_time=query_start,
            end_time=query_end,
            duration_ms=export["TotalDuration"],
            cpu_time_ms=export["TotalCpuDuration"],
            activity_id=export.get("ActivityID"),
        )
    )
    return events


@pytest.fixture(scope="module")
def processed(server_timings_export):
    return process_results(_events_from_export(server_timings_export))


class TestHeadlineParity:
    """The numbers a user reads off the Server Timings header."""

    def test_storage_engine_query_count(self, processed):
        assert processed.storage_engine_query_count == EXPECTED_SE_QUERIES

    def test_cache_matches(self, processed):
        assert processed.vertipaq_cache_matches == EXPECTED_CACHE

    def test_storage_engine_cpu(self, processed):
        # Requires ignoring VertiPaqScanInternal rows (they restate the CPU of
        # the scan they accompany) and using batch rows' CPU as reported.
        # A naive sum yields 64.
        assert processed.storage_engine_cpu == EXPECTED_SE_CPU

    def test_storage_engine_net_parallel(self, processed):
        assert processed.storage_engine_net_parallel_duration == EXPECTED_SE_NET

    def test_total_duration(self, processed):
        assert processed.total_duration == EXPECTED_TOTAL

    def test_formula_engine_duration(self, processed):
        # FE dominates this query at 93%; it is the reason the visual is slow.
        assert processed.formula_engine_duration == EXPECTED_FE

    def test_storage_engine_duration_reconciled(self, processed):
        assert processed.storage_engine_duration == EXPECTED_SE

    def test_percentages(self, processed):
        assert processed.fe_pct == pytest.approx(93.0, abs=0.1)
        assert processed.se_pct == pytest.approx(7.0, abs=0.1)
        assert processed.cache_pct == pytest.approx(58.2, abs=0.1)


class TestEventClassification:
    """Every row must land in the right bucket."""

    def test_row_count(self, processed, server_timings_export):
        assert len(processed.events) == len(server_timings_export["StorageEngineEvents"])

    def test_internal_events_excluded_from_duration(self, processed):
        internal = [e for e in processed.events if e.is_internal_event]
        assert len(internal) == 70
        # Internal scans contribute CPU but never duration.
        assert sum(e.cpu_time for e in internal) == 32

    def test_batch_events(self, processed):
        batches = [e for e in processed.events if e.is_batch_event]
        assert len(batches) == 9

    def test_batch_children(self, processed):
        children = [e for e in processed.events if e.internal_batch_event]
        assert len(children) == 27

    def test_cache_rows_have_no_duration(self, processed):
        cache_rows = [e for e in processed.events if e.class_name == "VertiPaqSEQueryCacheMatch"]
        assert len(cache_rows) == EXPECTED_CACHE
        assert all(e.duration == 0 for e in cache_rows)

    def test_callback_highlighting(self, processed):
        # DAX Studio highlights 104 rows; callbacks are the FE-per-row penalty.
        assert processed.callback_count == 104


class TestDerivedColumns:
    def test_estimated_size_parsed(self, processed):
        with_rows = [e for e in processed.events if e.estimated_rows is not None]
        assert len(with_rows) == 180
        assert all(e.estimated_kbytes and e.estimated_kbytes >= 1 for e in with_rows)

    def test_timeline_offsets(self, processed):
        offsets = [e.start_offset_ms for e in processed.events if e.start_time]
        assert offsets and min(offsets) >= 0
        assert processed.timeline_total_duration >= processed.total_duration

    def test_execution_metrics_extracted(self, processed):
        metrics = processed.execution_metrics
        assert metrics["queryResultRows"] == 8
        assert metrics["approximatePeakMemConsumptionKB"] == 223127
        assert metrics["vertipaqJobCpuTimeMs"] == 31
        assert metrics["queryProcessingCpuTimeMs"] == 1266

    def test_headline_dict(self, processed):
        headline = processed.headline()
        assert headline["se_queries"] == EXPECTED_SE_QUERIES
        assert headline["fe_ms"] == EXPECTED_FE
        assert headline["se_cache_hits"] == EXPECTED_CACHE


class TestSweepLine:
    """The formula-engine calculation in isolation."""

    def test_no_storage_engine_means_all_formula_engine(self):
        start = _dt.datetime(2026, 1, 1, 0, 0, 0)
        end = start + _dt.timedelta(milliseconds=500)
        assert _compute_formula_engine_ms(start, end, []) == 500

    def test_single_scan_is_subtracted(self):
        start = _dt.datetime(2026, 1, 1, 0, 0, 0)
        end = start + _dt.timedelta(milliseconds=1000)
        scan = (
            start + _dt.timedelta(milliseconds=200),
            start + _dt.timedelta(milliseconds=700),
        )
        assert _compute_formula_engine_ms(start, end, [scan]) == 500

    def test_overlapping_scans_count_once(self):
        start = _dt.datetime(2026, 1, 1, 0, 0, 0)
        end = start + _dt.timedelta(milliseconds=1000)
        scans = [
            (start + _dt.timedelta(milliseconds=100), start + _dt.timedelta(milliseconds=600)),
            (start + _dt.timedelta(milliseconds=200), start + _dt.timedelta(milliseconds=500)),
        ]
        # Parallel scans occupy one wall-clock window, not two.
        assert _compute_formula_engine_ms(start, end, scans) == 500


class TestEdgeCases:
    def test_empty_event_list(self):
        result = process_results([])
        assert result.total_duration == 0
        assert result.events == []
        assert result.headline()["se_queries"] == 0

    def test_query_without_storage_engine(self):
        start = _dt.datetime(2026, 1, 1, 0, 0, 0)
        end = start + _dt.timedelta(milliseconds=42)
        result = process_results(
            [
                TraceEvent(
                    event_class="QueryEnd",
                    start_time=start,
                    end_time=end,
                    duration_ms=42,
                    cpu_time_ms=40,
                )
            ]
        )
        assert result.total_duration == 42
        assert result.formula_engine_duration == 42
        assert result.storage_engine_duration == 0

    def test_end_time_repaired_from_duration(self):
        start = _dt.datetime(2026, 1, 1, 0, 0, 0)
        result = process_results(
            [
                TraceEvent(
                    event_class="QueryEnd",
                    start_time=start,
                    end_time=start,
                    duration_ms=100,
                    cpu_time_ms=90,
                ),
                TraceEvent(
                    event_class="VertiPaqSEQueryEnd",
                    event_subclass="VertiPaqScan",
                    start_time=start,
                    end_time=start,
                    duration_ms=10,
                    cpu_time_ms=20,
                ),
            ]
        )
        assert result.storage_engine_query_count == 1
        assert result.total_duration == 100

    def test_direct_query_counted(self):
        start = _dt.datetime(2026, 1, 1, 0, 0, 0)
        result = process_results(
            [
                TraceEvent(
                    event_class="QueryEnd",
                    start_time=start,
                    end_time=start + _dt.timedelta(milliseconds=300),
                    duration_ms=300,
                ),
                TraceEvent(
                    event_class="DirectQueryEnd",
                    start_time=start + _dt.timedelta(milliseconds=50),
                    end_time=start + _dt.timedelta(milliseconds=150),
                    duration_ms=100,
                ),
            ]
        )
        assert result.total_direct_query_duration == 100
        assert result.storage_engine_query_count == 1

    def test_dict_input_accepted(self):
        result = process_results(
            [
                {
                    "event_class": "QueryEnd",
                    "start_time": "2026-01-01T00:00:00",
                    "end_time": "2026-01-01T00:00:00.250000",
                    "duration_ms": 250,
                    "cpu_time_ms": 200,
                }
            ]
        )
        assert result.total_duration == 250
