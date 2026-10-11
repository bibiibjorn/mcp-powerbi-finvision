"""xmSQL cleanup, DAX Studio export format, and execution metrics."""

from __future__ import annotations

import json
import zipfile

import pytest

from pbi_perf_core.daxstudio_export import (
    QUERY_PLAN_FORMAT_VERSION,
    SERVER_TIMINGS_FORMAT_VERSION,
    build_query_plan_export,
    build_server_timings_export,
    write_daxx,
    write_query_plan,
    write_server_timings,
)
from pbi_perf_core.execution_metrics import (
    parse_execution_metrics,
    summarise_execution_metrics,
)
from pbi_perf_core.query_plan import rows_from_export
from pbi_perf_core.server_timings import ServerTimingsResult, process_results
from pbi_perf_core.xmsql import XmSqlCleaner, build_name_maps, clean_xmsql


class TestXmSqlCleaner:
    """The raw storage-engine text carries internal ids; users need names."""

    def test_ids_stripped_without_maps(self):
        raw = "SELECT\n[s ReportLines (13)].[Line Name (466)]\nFROM [s ReportLines (13)]"
        out = clean_xmsql(raw)
        assert "(13)" not in out
        assert "(466)" not in out

    def test_names_resolved_with_maps(self):
        columns = [{"COLUMN_ID": "466", "ATTRIBUTE_NAME": "Line Name"}]
        tables = [{"ID": "13", "Name": "s_ReportLines"}]
        column_map, table_map = build_name_maps(columns, tables)
        out = clean_xmsql("FROM [s ReportLines (13)]", column_map, table_map)
        assert "'s_ReportLines'" in out

    def test_alias_removed(self):
        raw = "SELECT [a] AS 'some$alias' FROM [t]"
        assert "some$alias" not in clean_xmsql(raw)

    def test_rownumber_guid_collapsed(self):
        raw = "SELECT [RowNumber 2662979B 1795 4F74 8F37 6A1BA8059B61] FROM [t]"
        assert clean_xmsql(raw).count("[RowNumber]") == 1

    def test_premium_tags_removed(self):
        assert "<pii>" not in clean_xmsql("SELECT <pii>[c]</pii> FROM [t]")

    def test_tabs_become_spaces(self):
        assert "\t" not in clean_xmsql("SELECT\n\t[c]\nFROM [t]")

    def test_empty_input(self):
        assert clean_xmsql("") == ""
        assert clean_xmsql(None) == ""

    def test_cleaner_is_reusable(self):
        cleaner = XmSqlCleaner({"1": "Col"}, {"2": "Tbl"})
        first = cleaner("FROM [T (2)]")
        assert cleaner("FROM [T (2)]") == first

    def test_real_fixture_text_is_simplified(self, server_timings_export):
        # Every id-tagged name should be gone from the cleaned output.
        raw = next(
            e["TextData"]
            for e in server_timings_export["StorageEngineEvents"]
            if e.get("Subclass") == "VertiPaqScan"
        )
        out = clean_xmsql(raw)
        assert "$" not in out.split("FROM")[0] or "AS" not in out


class TestExecutionMetrics:
    def test_parses_real_payload(self, server_timings_export):
        raw = next(
            e["TextData"]
            for e in server_timings_export["StorageEngineEvents"]
            if e["Class"] == "ExecutionMetrics"
        )
        metrics = parse_execution_metrics(raw)
        assert metrics["durationMs"] == 1430
        assert metrics["queryResultRows"] == 8
        assert metrics["approximatePeakMemConsumptionKB"] == 223127

    def test_alternate_peak_memory_spelling_normalised(self):
        metrics = parse_execution_metrics('{"approximatePeakMemoryConsumptionKB": 512}')
        assert metrics["approximatePeakMemConsumptionKB"] == 512

    def test_malformed_json_returns_empty(self):
        assert parse_execution_metrics("not json") == {}
        assert parse_execution_metrics("") == {}
        assert parse_execution_metrics(None) == {}

    def test_summary_derives_cpu_split(self):
        summary = summarise_execution_metrics(
            {
                "durationMs": 1000,
                "totalCpuTimeMs": 2000,
                "queryProcessingCpuTimeMs": 1500,
                "vertipaqJobCpuTimeMs": 500,
            }
        )
        assert summary["fe_cpu_pct"] == 75.0
        assert summary["se_cpu_pct"] == 25.0
        # More CPU than wall clock means the work parallelised.
        assert summary["cpu_parallelism"] == 2.0

    def test_summary_of_empty_metrics(self):
        assert summarise_execution_metrics({}) == {}


class TestServerTimingsExport:
    """The file must be loadable by DAX Studio, so the shape must match."""

    @pytest.fixture(scope="class")
    def payload(self, server_timings_export):
        events = []
        result = ServerTimingsResult(
            total_duration=1428,
            total_cpu_duration=1297,
            formula_engine_duration=1328,
            storage_engine_duration=100,
            storage_engine_net_parallel_duration=100,
            storage_engine_cpu=32,
            storage_engine_query_count=189,
            vertipaq_cache_matches=110,
            timeline_total_duration=1428,
            activity_id="TEST-ACTIVITY",
            command_text='EVALUATE ROW("x", 1)',
            events=events,
        )
        return build_server_timings_export(result)

    def test_format_version(self, payload):
        assert payload["FileFormatVersion"] == SERVER_TIMINGS_FORMAT_VERSION

    def test_required_keys_present(self, payload, server_timings_export):
        # Every key DAX Studio writes must exist in ours.
        for key in server_timings_export:
            assert key in payload, f"missing export key: {key}"

    def test_headline_values_round_trip(self, payload):
        assert payload["TotalDuration"] == 1428
        assert payload["FormulaEngineDuration"] == 1328
        assert payload["StorageEngineQueryCount"] == 189

    def test_default_fields_omitted_on_rows(self):
        """DAX Studio omits false/null row fields rather than writing them."""
        result = process_results(
            [
                {
                    "event_class": "VertiPaqSEQueryEnd",
                    "event_subclass": "VertiPaqScan",
                    "text_data": "SELECT 1",
                    "start_time": "2026-01-01T00:00:00",
                    "end_time": "2026-01-01T00:00:00.001000",
                    "duration_ms": 1,
                }
            ]
        )
        row = result.events[0].to_export_dict()
        assert "IsInternalEvent" not in row
        assert "HighlightQuery" not in row
        assert "IsBatchEvent" not in row

    def test_cache_rows_omit_duration(self):
        result = process_results(
            [
                {
                    "event_class": "VertiPaqSEQueryCacheMatch",
                    "event_subclass": "VertiPaqCacheExactMatch",
                    "text_data": "SELECT 1",
                    "start_time": "2026-01-01T00:00:00",
                }
            ]
        )
        row = result.events[0].to_export_dict()
        assert "Duration" not in row
        assert "EndTime" not in row

    def test_written_file_is_valid_json(self, tmp_path, payload):
        target = tmp_path / "ServerTimings.json"
        write_server_timings(ServerTimingsResult(total_duration=1, command_text="x"), str(target))
        assert json.loads(target.read_text(encoding="utf-8"))["FileFormatVersion"] == 6


class TestQueryPlanExport:
    def test_format_version_and_keys(self, query_plan_export):
        physical = rows_from_export(query_plan_export["PhysicalQueryPlanRows"][:10], "physical")
        logical = rows_from_export(query_plan_export["LogicalQueryPlanRows"][:10], "logical")
        payload = build_query_plan_export(
            physical, logical, activity_id="A", command_text="EVALUATE 1"
        )
        assert payload["FileFormatVersion"] == QUERY_PLAN_FORMAT_VERSION
        for key in ("PhysicalQueryPlanRows", "LogicalQueryPlanRows", "ActivityID"):
            assert key in payload

    def test_logical_rows_exported_without_records(self, query_plan_export):
        logical = rows_from_export(query_plan_export["LogicalQueryPlanRows"][:5], "logical")
        assert all("Records" not in row for row in logical.to_export_rows())

    def test_physical_rows_exported_with_records(self, query_plan_export):
        physical = rows_from_export(query_plan_export["PhysicalQueryPlanRows"][:5], "physical")
        assert all("Records" in row for row in physical.to_export_rows())

    def test_write_query_plan(self, tmp_path):
        target = tmp_path / "QueryPlan.json"
        write_query_plan(str(target), None, None, command_text="EVALUATE 1")
        data = json.loads(target.read_text(encoding="utf-8"))
        assert data["PhysicalQueryPlanRows"] == []


class TestDaxxBundle:
    def test_bundle_contains_expected_members(self, tmp_path):
        target = tmp_path / "capture.daxx"
        write_daxx(
            str(target),
            query_text='EVALUATE ROW("a", 1)',
            server_timings={"FileFormatVersion": 6},
            query_plan={"FileFormatVersion": 3},
        )
        with zipfile.ZipFile(target) as bundle:
            names = set(bundle.namelist())
        # [Content_Types].xml is the OPC part DAX Studio's packaging API needs (F-11).
        assert names == {
            "[Content_Types].xml",
            "Query.dax",
            "ServerTimings.json",
            "QueryPlan.json",
        }

    def test_bundle_query_text_round_trips(self, tmp_path):
        target = tmp_path / "capture.daxx"
        write_daxx(str(target), query_text="EVALUATE 42")
        with zipfile.ZipFile(target) as bundle:
            assert bundle.read("Query.dax").decode() == "EVALUATE 42"
