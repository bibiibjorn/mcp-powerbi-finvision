"""2026-09-22 review (MCP-PowerBi-Finvision slice F) - pbi-perf-core fixes.

F-11 ``.daxx`` is an OPC package; F-24 AggregateTableRewriteQuery keeps its
subclass; F-25 plan operator = ``Name: Class``; F-29 batch attribution with
Begin rows; F-30 query-window clamp, QueryEnd / ExecutionMetrics choice, skewed
rows; F-31 Desktop's flat Performance Analyzer export; F-35 direct-children
record inheritance.  Parity cases replay the DAX Studio reference capture.
"""

from __future__ import annotations

import datetime as _dt
import json
import zipfile

import pytest

from pbi_perf_core.daxstudio_export import write_daxx
from pbi_perf_core.perf_analyzer import parse_performance_export
from pbi_perf_core.query_plan import parse_plan
from pbi_perf_core.server_timings import TraceEvent, process_results

from test_server_timings import (
    EXPECTED_FE,
    EXPECTED_SE,
    EXPECTED_TOTAL,
    _events_from_export,
)


def _headline(result):
    h = result.headline()
    return h["total_ms"], h["fe_ms"], h["se_ms"]


# -- F-11 --------------------------------------------------------------------


def test_f11_daxx_declares_its_parts_first(tmp_path):
    target = tmp_path / "c.daxx"
    write_daxx(str(target), query_text="EVALUATE 1", server_timings={"a": 1})
    with zipfile.ZipFile(target) as bundle:
        names = bundle.namelist()
        types = bundle.read("[Content_Types].xml").decode("utf-8")
    assert names[0] == "[Content_Types].xml"
    assert 'Extension="dax"' in types and 'Extension="json"' in types


# -- F-24 --------------------------------------------------------------------


def test_f24_aggregate_rewrite_keeps_its_subclass():
    t0 = _dt.datetime(2026, 1, 1, 12)
    events = [
        TraceEvent(event_class="QueryBegin", start_time=t0),
        TraceEvent(
            event_class="AggregateTableRewriteQuery",
            event_subclass="MatchFound",
            text_data="agg",
            start_time=t0,
        ),
        TraceEvent(
            event_class="QueryEnd",
            start_time=t0,
            end_time=t0 + _dt.timedelta(milliseconds=10),
            duration_ms=10,
        ),
    ]
    rewrites = process_results(events).aggregate_rewrites
    assert rewrites == [{"text": "agg", "start": t0, "subclass": "MatchFound"}]


# -- F-25 --------------------------------------------------------------------


def test_f25_logical_operator_drops_the_column_lists():
    plan = parse_plan(
        "ScalarVarProxy: ScaLogOp DependOnCols(24, 25)('s_Lines'[Key], 's_Lines'[Order])\n"
        "\tSum_Vertipaq: ScaLogOp DependOnCols(24)('s_Lines'[Key]) Currency DominantValue=BLANK\n"
        "\t'd Unit'[Name]: ScaLogOp DependOnCols(3)('d Unit'[Name]) String DominantValue=NONE\n",
        "logical",
    )
    assert [r.operator for r in plan.rows] == [
        "ScalarVarProxy: ScaLogOp",
        "Sum_Vertipaq: ScaLogOp",
        "'d Unit'[Name]: ScaLogOp",
    ]


def test_f25_real_logical_plan_has_few_distinct_operators(query_plan_export):
    rows = query_plan_export["LogicalQueryPlanRows"]
    text = "\n".join("\t" * r["Level"] + r["Operation"] for r in rows)
    census = parse_plan(text, "logical").operator_census()
    assert len(census) < 200, len(census)  # was 714 with the column lists kept
    assert not [op for op, _ in census if "DependOnCols" in op]


# -- F-29 --------------------------------------------------------------------


def _with_batch_begins(events):
    out = list(events)
    batches = [
        e
        for e in events
        if e.class_name == "VertiPaqSEQueryEnd" and e.event_subclass == "BatchVertiPaqScan"
    ]
    for batch in batches:
        idx = out.index(batch)
        j = idx - 1
        while (
            j > 0
            and out[j].class_name == "VertiPaqSEQueryEnd"
            and out[j].event_subclass in ("VertiPaqScan", "VertiPaqScanInternal")
        ):
            j -= 1
        out.insert(
            j + 1,
            TraceEvent(
                event_class="VertiPaqSEQueryBegin",
                event_subclass="BatchVertiPaqScan",
                start_time=batch.start_time,
                activity_id=batch.activity_id,
            ),
        )
    return out


def test_f29_batch_begin_rows_do_not_change_the_parity_numbers(server_timings_export):
    base = _events_from_export(server_timings_export)
    assert _headline(process_results(_with_batch_begins(base))) == (
        EXPECTED_TOTAL,
        EXPECTED_FE,
        EXPECTED_SE,
    )


# -- F-30 --------------------------------------------------------------------


def test_f30_scan_after_query_end_is_ignored(server_timings_export):
    base = _events_from_export(server_timings_export)
    end = _dt.datetime.fromisoformat(server_timings_export["QueryEndDateTime"])
    stray = TraceEvent(
        event_class="VertiPaqSEQueryEnd",
        event_subclass="VertiPaqScan",
        start_time=end + _dt.timedelta(seconds=2),
        end_time=end + _dt.timedelta(seconds=2, milliseconds=5),
        duration_ms=5,
        cpu_time_ms=5,
    )
    result = process_results(base + [stray])
    assert _headline(result) == (EXPECTED_TOTAL, EXPECTED_FE, EXPECTED_SE)


def test_f30_ping_query_end_first_does_not_become_the_total(server_timings_export):
    base = _events_from_export(server_timings_export)
    end = _dt.datetime.fromisoformat(server_timings_export["QueryEndDateTime"])
    ping = TraceEvent(
        event_class="QueryEnd",
        event_subclass="DmxQuery",
        start_time=end,
        end_time=end + _dt.timedelta(milliseconds=30),
        duration_ms=30,
    )
    assert process_results([ping] + base).total_duration == EXPECTED_TOTAL


def test_f30_execution_metrics_closest_to_the_total_wins(server_timings_export):
    base = [
        e for e in _events_from_export(server_timings_export) if e.class_name != "ExecutionMetrics"
    ]
    small = TraceEvent(event_class="ExecutionMetrics", text_data=json.dumps({"durationMs": 2}))
    big = TraceEvent(event_class="ExecutionMetrics", text_data=json.dumps({"durationMs": 1428}))
    for e in base:
        e.activity_id = None
    result = process_results([small] + base + [big])
    assert result.execution_metrics["durationMs"] == 1428


def test_f30_scan_with_end_before_start_uses_its_duration():
    t0 = _dt.datetime(2026, 1, 1, 12)

    def ms(n):
        return t0 + _dt.timedelta(milliseconds=n)

    events = [
        TraceEvent(event_class="QueryBegin", start_time=t0),
        TraceEvent(
            event_class="VertiPaqSEQueryEnd",
            event_subclass="VertiPaqScan",
            start_time=ms(10),
            end_time=ms(50),
            duration_ms=40,
            cpu_time_ms=40,
        ),
        # Skewed row: EndTime before StartTime. Its real window is [30, 70].
        TraceEvent(
            event_class="VertiPaqSEQueryEnd",
            event_subclass="VertiPaqScan",
            start_time=ms(30),
            end_time=ms(5),
            duration_ms=40,
            cpu_time_ms=40,
        ),
        TraceEvent(event_class="QueryEnd", start_time=t0, end_time=ms(100), duration_ms=100),
    ]
    # SE covers [10, 70] -> 60 ms; FE is the remaining 40 ms.
    assert _headline(process_results(events)) == (100, 40, 60)


# -- F-31 --------------------------------------------------------------------


def test_f31_flat_desktop_export_is_parsed():
    export = {
        "version": "1.1.0",
        "events": [
            {
                "id": "1",
                "name": "Visual Container Lifecycle",
                "start": "2026-09-01T10:00:00.000Z",
                "metrics": {"visualId": "v1", "visualTitle": "Sales matrix"},
            },
            {
                "id": "2",
                "parentId": "1",
                "name": "Execute DAX Query",
                "start": "2026-09-01T10:00:00.100Z",
                "end": "2026-09-01T10:00:00.600Z",
                "metrics": {"QueryText": "EVALUATE 1", "RowCount": 3},
            },
            {
                "id": "3",
                "parentId": "1",
                "name": "Render",
                "start": "2026-09-01T10:00:00.600Z",
                "end": "2026-09-01T10:00:00.700Z",
            },
            {"id": "1", "name": "Visual Container Lifecycle", "end": "2026-09-01T10:00:01.000Z"},
        ],
    }
    (timing,) = parse_performance_export(export)
    assert timing.visual_id == "v1"
    assert timing.total_ms == pytest.approx(1000.0)
    assert timing.dax_ms == pytest.approx(500.0)
    assert timing.display_ms == pytest.approx(100.0)
    assert timing.other_ms == pytest.approx(400.0)
    assert timing.query_text == "EVALUATE 1" and timing.row_count == 3


# -- F-35 --------------------------------------------------------------------


def test_f35_records_inherited_from_direct_children_only():
    plan = parse_plan(
        "A: IterPhyOp\n"
        "\tB: IterPhyOp #Records=10\n"
        "\tC: IterPhyOp\n"
        "\t\tD: IterPhyOp #Records=500\n"
        "\t\t\tE: IterPhyOp #Records=9999\n"
        "F: IterPhyOp\n",
        "physical",
    )
    records = {r.operation.split(":")[0]: r.records for r in plan.rows}
    assert records == {"A": 500, "B": 10, "C": 500, "D": 500, "E": 9999, "F": None}


def _subtree_scan_oracle(rows):
    """The pre-F-35 algorithm (scan each row's whole subtree) as the oracle."""
    by_number = {r.row_number: r for r in rows}
    out = {r.row_number: r.records for r in rows}
    for row in reversed(rows):
        if out[row.row_number] is not None:
            continue
        best = None
        for candidate in range(row.row_number + 1, row.next_sibling_row_number + 1):
            child = by_number.get(candidate)
            if child is None or child.level != row.level + 1:
                continue
            value = out[child.row_number]
            if value is not None:
                best = value if best is None else max(best, value)
        out[row.row_number] = best
    return [out[r.row_number] for r in rows]


def test_f35_reference_plan_inherits_like_before(query_plan_export):
    rows = query_plan_export["PhysicalQueryPlanRows"]
    text = "\n".join("\t" * r["Level"] + r["Operation"] for r in rows)
    plan = parse_plan(text, "physical")
    raw = parse_plan(text, "logical")  # same rows, no inheritance applied
    assert [r.records for r in plan.rows] == _subtree_scan_oracle(raw.rows)
