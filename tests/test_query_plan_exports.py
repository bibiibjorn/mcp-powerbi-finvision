"""13_Query_Plan capture summary, file exports and the paging ops (7.15.0).

The capture response is a summary by design: the acceptance fixture's physical
plan is 10,044 rows / 12 MB, so inlining one would consume the session's whole
context budget. These tests pin that the summary stays a summary, that the DAX
Studio files land on disk, and that plan_rows is the way back to the rows.

Everything is mocked at the capturer boundary — no CLR, no Desktop.
"""

from __future__ import annotations

import json
import os

import pytest

from core.query_plan import capturer, exporter
from server.handlers import query_plan_handler

# A plan big enough that a summary and a dump are visibly different.
_PHYSICAL = "\n".join(
    ["AddColumns: IterPhyOp #Records=8"]
    + [f"\tCache: IterPhyOp #Records={i}" for i in range(1, 61)]
    + ["\tSpoolLookup: LookupPhyOp #Records=418"]
)
_LOGICAL = "AddColumns: RelLogOp\n\tScan_Vertipaq: RelLogOp\n\tSum_Vertipaq: ScaLogOp\n"


def _capture(capture_id="cap123", **overrides):
    cap = {
        "success": True,
        "capture_id": capture_id,
        "dax_query": 'EVALUATE ROW("a", 1)',
        "logical_plan_raw": _LOGICAL,
        "physical_plan_raw": _PHYSICAL,
        "xmsql_queries": ["SELECT SUM('Sales'[Amt])"],
        "se_fe_timings": {
            "total_ms": 1428,
            "fe_ms": 1328,
            "se_ms": 100,
            "fe_pct": 93.0,
            "se_pct": 7.0,
            "se_cpu_ms": 32,
            "se_queries": 189,
            "se_cache_hits": 110,
            "result_rows": 8,
        },
        "callback_count": 52,
        "agg_rewrites": [],
        "cache_cleared": True,
        "runner_lane": "in_process",
        "execution_metrics": {"peak_memory_kb": 223127, "result_rows": 8},
        "result_sets": [{"columns": ["[a]"], "row_count": 8}],
        "total_cpu_ms": 1297,
        "se_net_parallel_ms": 100,
        "dq_ms": 0,
        "parallel_se_detected": False,
        "daxstudio": {
            "server_timings": {"FileFormatVersion": 6, "TotalDuration": 1428},
            "query_plan": {"FileFormatVersion": 3, "PhysicalQueryPlanRows": []},
        },
    }
    cap.update(overrides)
    return cap


@pytest.fixture
def export_root(tmp_path, monkeypatch):
    """Redirect exports/perf/ into tmp so tests never touch the repo."""
    monkeypatch.setattr(exporter, "PERF_EXPORT_ROOT", str(tmp_path))
    return tmp_path


# --- capture summary -----------------------------------------------------


class TestCaptureSummary:
    def test_summary_carries_headline_metrics(self):
        summary = exporter.build_capture_summary(_capture())
        assert summary["headline"]["total_ms"] == 1428
        assert summary["headline"]["fe_pct"] == 93.0
        assert summary["headline"]["se_queries"] == 189

    def test_summary_never_inlines_plan_text(self):
        """The whole point of the file-backed design."""
        summary = exporter.build_capture_summary(_capture())
        blob = json.dumps(summary)
        assert "logical_plan_raw" not in summary
        assert "physical_plan_raw" not in summary
        assert len(blob) < len(_PHYSICAL)

    def test_operator_census_is_capped_at_twenty(self):
        summary = exporter.build_capture_summary(_capture())
        assert len(summary["operator_census"]) <= 20

    def test_top_records_capped_at_ten_and_sorted(self):
        summary = exporter.build_capture_summary(_capture())
        records = [r["records"] for r in summary["top_records"]]
        assert len(records) == 10
        assert records == sorted(records, reverse=True)
        assert records[0] == 418

    def test_plan_shape_reports_cache_consumers_vs_se_queries(self):
        """The gap between the two is the cache story the plan tells."""
        shape = exporter.build_capture_summary(_capture())["plan_shape"]
        # Only the 60 `Cache:` rows are datacache consumers; SpoolLookup is a
        # lookup operator and is deliberately not highlighted.
        assert shape["cache_consumers"] == 60
        assert shape["se_query_count"] == 189
        assert shape["callback_scans"] == 52

    def test_execution_metrics_and_result_sets_surface(self):
        summary = exporter.build_capture_summary(_capture())
        assert summary["execution_metrics"]["peak_memory_kb"] == 223127
        assert summary["result_sets"] == [{"columns": ["[a]"], "row_count": 8}]

    def test_empty_plan_is_explained_not_silent(self):
        """An empty plan used to be reported as a successful capture."""
        summary = exporter.build_capture_summary(
            _capture(logical_plan_raw="", physical_plan_raw="")
        )
        assert "note" in summary
        assert "exe fallback" in summary["note"]


# --- file exports --------------------------------------------------------


class TestExports:
    def test_writes_the_three_daxstudio_artefacts(self, export_root):
        result = exporter.write_exports(_capture(), dax_query="EVALUATE {1}")
        assert result["written"] is True
        target = export_root / "cap123"
        assert (target / "ServerTimings.json").exists()
        assert (target / "QueryPlan.json").exists()
        assert (target / "capture.daxx").exists()

    def test_daxx_is_a_zip_daxstudio_can_open(self, export_root):
        import zipfile

        exporter.write_exports(_capture(), dax_query="EVALUATE {1}")
        daxx = export_root / "cap123" / "capture.daxx"
        with zipfile.ZipFile(daxx) as bundle:
            names = set(bundle.namelist())
        assert {"Query.dax", "ServerTimings.json", "QueryPlan.json"} <= names

    def test_server_timings_keeps_the_documented_format_version(self, export_root):
        exporter.write_exports(_capture(), dax_query="EVALUATE {1}")
        payload = json.loads(
            (export_root / "cap123" / "ServerTimings.json").read_text(encoding="utf-8")
        )
        assert payload["FileFormatVersion"] == 6

    def test_exe_lane_capture_reports_why_nothing_was_written(self, export_root):
        result = exporter.write_exports(_capture(daxstudio={}), dax_query="EVALUATE {1}")
        assert result["written"] is False
        assert "fallback lane" in result["reason"]

    def test_write_failure_is_reported_not_raised(self, monkeypatch, export_root):
        """A capture with good numbers is still worth returning."""

        def boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr(os, "makedirs", boom)
        result = exporter.write_exports(_capture(), dax_query="EVALUATE {1}")
        assert result["written"] is False
        assert "disk full" in result["reason"]


# --- plan_rows paging ----------------------------------------------------


class TestPlanRows:
    def test_pages_physical_rows(self):
        page = exporter.page_plan_rows(_capture(), plan="physical", offset=0, limit=10)
        assert page["success"] is True
        assert page["total_rows"] == 62
        assert page["returned"] == 10
        assert page["has_more"] is True

    def test_offset_advances_the_window(self):
        first = exporter.page_plan_rows(_capture(), limit=5)
        second = exporter.page_plan_rows(_capture(), offset=5, limit=5)
        assert first["rows"][0]["row_number"] != second["rows"][0]["row_number"]

    def test_last_page_reports_no_more(self):
        page = exporter.page_plan_rows(_capture(), offset=60, limit=10)
        assert page["has_more"] is False

    def test_min_records_finds_the_hotspot(self):
        """The fastest route to the materialization row in a 10k-row plan."""
        page = exporter.page_plan_rows(_capture(), min_records=400)
        assert page["matched_rows"] == 1
        assert page["rows"][0]["records"] == 418

    def test_logical_plan_can_be_paged(self):
        page = exporter.page_plan_rows(_capture(), plan="logical")
        assert page["total_rows"] == 3

    def test_limit_is_clamped(self):
        page = exporter.page_plan_rows(_capture(), limit=99999)
        assert page["limit"] == 500

    def test_invalid_plan_rejected(self):
        page = exporter.page_plan_rows(_capture(), plan="sideways")
        assert page["success"] is False
        assert page["error_type"] == "invalid_args"

    def test_rows_carry_the_tree_shape(self):
        row = exporter.page_plan_rows(_capture(), limit=2)["rows"][1]
        assert row["level"] == 1
        assert row["highlight_row"] is True  # `Cache:` is a datacache consumer


# --- handler dispatch ----------------------------------------------------


@pytest.fixture
def stub_capture(monkeypatch, export_root):
    """capture() returns a canned result; nothing touches a connection."""
    cap = _capture()
    monkeypatch.setattr(capturer, "capture", lambda **kw: cap)
    monkeypatch.setattr(capturer, "recall", lambda cid: cap if cid == "cap123" else None)
    monkeypatch.setattr(capturer, "remember", lambda c, **_kw: None)
    return cap


class TestHandlerOps:
    def test_new_ops_are_registered(self):
        assert {"plan_rows", "export_daxx"} <= query_plan_handler._VALID_OPS

    def test_capture_returns_summary_with_export_paths(self, stub_capture):
        res = query_plan_handler.handle_query_plan(
            {"operation": "capture", "dax_query": "EVALUATE {1}"}
        )
        assert res["success"] is True
        assert res["exports"]["written"] is True
        assert "operator_census" in res
        assert "logical_plan_raw" not in res

    def test_plan_rows_op_pages(self, stub_capture):
        res = query_plan_handler.handle_query_plan(
            {"operation": "plan_rows", "capture_id": "cap123", "limit": 3}
        )
        assert res["success"] is True and res["returned"] == 3

    def test_plan_rows_requires_a_known_capture(self, stub_capture):
        res = query_plan_handler.handle_query_plan({"operation": "plan_rows", "capture_id": "nope"})
        assert res["success"] is False

    def test_export_daxx_op_writes_files(self, stub_capture, export_root):
        res = query_plan_handler.handle_query_plan(
            {"operation": "export_daxx", "capture_id": "cap123"}
        )
        assert res["success"] is True
        assert (export_root / "cap123" / "capture.daxx").exists()

    def test_compare_returns_structural_plan_diff(self, monkeypatch, export_root):
        """A timing delta says it got faster; the diff says what changed."""
        before = _capture("a")
        after = _capture("b", physical_plan_raw="AddColumns: IterPhyOp #Records=8")
        monkeypatch.setattr(capturer, "recall", lambda cid: before if cid == "a" else after)
        res = query_plan_handler.handle_query_plan(
            {"operation": "compare", "capture_id_a": "a", "capture_id_b": "b"}
        )
        assert res["success"] is True
        assert res["physical_plan_diff"]["rows"]["delta"] == -61
        assert res["physical_plan_diff"]["max_records"]["delta"] == -410
        assert "logical_plan_diff" in res
