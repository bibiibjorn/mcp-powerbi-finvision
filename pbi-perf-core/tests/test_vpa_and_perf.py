"""VertiPaq Analyzer metrics and Performance Analyzer semantics."""

from __future__ import annotations

import pytest

from pbi_perf_core.perf_analyzer import (
    DEFAULT_QUERY_SLOTS,
    VisualTiming,
    parse_performance_export,
    simulate_page,
    summarise_rows,
)
from pbi_perf_core.vpa import analyse_model, build_model_metrics

# ---------------------------------------------------------------------------
# A small synthetic model: one fact, two dimensions.
# ---------------------------------------------------------------------------

STORAGE_TABLES = [
    {"TABLE_ID": "f_Sales", "ROWS_COUNT": 1_000_000, "RIVIOLATION_COUNT": 0},
    {"TABLE_ID": "d_Product", "ROWS_COUNT": 5_000, "RIVIOLATION_COUNT": 2},
    {"TABLE_ID": "d_Date", "ROWS_COUNT": 3_650, "RIVIOLATION_COUNT": 0},
]

STORAGE_COLUMNS = [
    {
        "TABLE_ID": "f_Sales",
        "ATTRIBUTE_NAME": "Amount",
        "COLUMN_TYPE": "BASIC_DATA",
        "DICTIONARY_SIZE": 2_000_000,
        "COLUMN_ENCODING": 1,
        "DATATYPE": "Double",
    },
    {
        "TABLE_ID": "f_Sales",
        "ATTRIBUTE_NAME": "ProductKey",
        "COLUMN_TYPE": "BASIC_DATA",
        "DICTIONARY_SIZE": 1_000,
        "COLUMN_ENCODING": 2,
        "DATATYPE": "Int64",
    },
    {
        "TABLE_ID": "d_Product",
        "ATTRIBUTE_NAME": "ProductKey",
        "COLUMN_TYPE": "BASIC_DATA",
        "DICTIONARY_SIZE": 500,
        "COLUMN_ENCODING": 2,
        "DATATYPE": "Int64",
    },
    # Filtered out: not BASIC_DATA.
    {
        "TABLE_ID": "d_Product",
        "ATTRIBUTE_NAME": "RowNumber",
        "COLUMN_TYPE": "ROWNUMBER",
        "DICTIONARY_SIZE": 0,
    },
]

SEGMENTS = [
    {"TABLE_ID": "f_Sales", "COLUMN_ID": "Amount", "USED_SIZE": 8_000_000, "BITS_COUNT": 20},
    {"TABLE_ID": "f_Sales", "COLUMN_ID": "Amount", "USED_SIZE": 4_000_000, "BITS_COUNT": 20},
    {"TABLE_ID": "f_Sales", "COLUMN_ID": "ProductKey", "USED_SIZE": 500_000},
    {"TABLE_ID": "d_Product", "COLUMN_ID": "ProductKey", "USED_SIZE": 20_000},
    # Hierarchy / relationship segments carry a prefix on the table name.
    {"TABLE_ID": "H$f_Sales", "COLUMN_ID": "ProductKey", "USED_SIZE": 100_000},
    {"TABLE_ID": "R$f_Sales", "COLUMN_ID": "rel", "USED_SIZE": 50_000},
    {"TABLE_ID": "U$d_Product", "COLUMN_ID": "hier", "USED_SIZE": 10_000},
]

RELATIONSHIPS = [
    {
        "Name": "Sales_Product",
        "FromTable": "f_Sales",
        "FromColumn": "ProductKey",
        "ToTable": "d_Product",
        "ToColumn": "ProductKey",
        "IsActive": True,
        "MissingKeys": 3,
    }
]

DAX_STATS = {
    "f_Sales": 1_000_000,
    "d_Product": 5_000,
    "f_Sales[Amount]": 750_000,
    "f_Sales[ProductKey]": 5_000,
    "d_Product[ProductKey]": 5_000,
}


@pytest.fixture(scope="module")
def model():
    return build_model_metrics(
        storage_tables=STORAGE_TABLES,
        storage_columns=STORAGE_COLUMNS,
        segments=SEGMENTS,
        relationships=RELATIONSHIPS,
        partitions=[{"TableName": "f_Sales"}, {"TableName": "f_Sales"}],
        dax_stats=DAX_STATS,
    )


class TestModelMetrics:
    def test_tables_discovered(self, model):
        assert {t.table_name for t in model.tables} == {
            "f_Sales",
            "d_Product",
            "d_Date",
        }

    def test_non_basic_columns_excluded(self, model):
        product = next(t for t in model.tables if t.table_name == "d_Product")
        assert [c.column_name for c in product.columns] == ["ProductKey"]

    def test_column_total_size_formula(self, model):
        amount = next(c for c in model.all_columns() if c.column_name == "Amount")
        # data (8M + 4M segments) + dictionary (2M) + hierarchies (0)
        assert amount.data_size == 12_000_000
        assert amount.total_size == 14_000_000

    def test_hierarchy_size_attributed_to_column(self, model):
        key = next(
            c
            for c in model.all_columns()
            if c.table_name == "f_Sales" and c.column_name == "ProductKey"
        )
        assert key.hierarchies_size == 100_000

    def test_relationship_and_user_hierarchy_sizes(self, model):
        sales = next(t for t in model.tables if t.table_name == "f_Sales")
        product = next(t for t in model.tables if t.table_name == "d_Product")
        assert sales.relationships_size == 50_000
        assert product.user_hierarchies_size == 10_000

    def test_table_size_formula(self, model):
        sales = next(t for t in model.tables if t.table_name == "f_Sales")
        assert sales.table_size == sales.columns_total_size + sales.relationships_size

    def test_segment_and_partition_counts(self, model):
        sales = next(t for t in model.tables if t.table_name == "f_Sales")
        assert sales.segments_count == 3
        assert sales.partitions_count == 2

    def test_encoding_names(self, model):
        amount = next(c for c in model.all_columns() if c.column_name == "Amount")
        assert amount.encoding == "HASH"

    def test_cardinality_from_dax_stats(self, model):
        amount = next(c for c in model.all_columns() if c.column_name == "Amount")
        assert amount.cardinality == 750_000

    def test_relationship_cardinalities_resolved(self, model):
        rel = model.relationships[0]
        assert rel.from_cardinality == 5_000
        assert rel.to_cardinality == 5_000
        assert rel.one_to_many_ratio == 1.0

    def test_summary_shape(self, model):
        summary = model.summary()
        assert summary["tables"] == 3
        assert summary["model_size_bytes"] == model.model_size
        assert summary["largest_columns"]


class TestModelFindings:
    def test_dominant_column_flagged(self, model):
        codes = {f["code"] for f in analyse_model(model)["findings"]}
        assert "DOMINANT_COLUMN" in codes

    def test_hash_encoded_numeric_flagged(self, model):
        findings = analyse_model(model)["findings"]
        assert any(
            f["code"] == "HASH_ENCODED_NUMERIC" and "Amount" in f["object"] for f in findings
        )

    def test_ri_violation_flagged(self, model):
        findings = analyse_model(model)["findings"]
        assert any(f["code"] == "RI_VIOLATION" and f["object"] == "d_Product" for f in findings)

    def test_missing_keys_flagged(self, model):
        codes = {f["code"] for f in analyse_model(model)["findings"]}
        assert "MISSING_KEYS" in codes

    def test_findings_sorted_by_severity(self, model):
        severities = [f["severity"] for f in analyse_model(model)["findings"]]
        order = {"high": 0, "medium": 1, "low": 2, "info": 3}
        assert severities == sorted(severities, key=lambda s: order[s])

    def test_empty_model_is_safe(self):
        out = analyse_model(build_model_metrics())
        assert out["findings"] == []
        assert out["summary"]["tables"] == 0


# ---------------------------------------------------------------------------
# Performance Analyzer
# ---------------------------------------------------------------------------

PERF_EXPORT = {
    "version": "1.0.0",
    "events": [
        {
            "name": "Visual Container Lifecycle",
            "start": 0,
            "end": 1311,
            "metrics": {
                "visualTitle": "P&L matrix",
                "visualId": "abc123",
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
                                    "metrics": {
                                        "QueryText": 'EVALUATE ROW("x", 1)',
                                        "RowCount": 8,
                                    },
                                }
                            ],
                        },
                    ],
                },
                {
                    "name": "Render",
                    "start": 1080,
                    "end": 1111,
                    "children": [{"name": "Data View Transform", "start": 1080, "end": 1090}],
                },
            ],
        }
    ],
}


class TestPerformanceExportParsing:
    def test_visual_identified(self):
        rows = parse_performance_export(PERF_EXPORT)
        assert len(rows) == 1
        assert rows[0].visual == "P&L matrix"
        assert rows[0].visual_type == "pivotTable"

    def test_dax_and_display_durations(self):
        row = parse_performance_export(PERF_EXPORT)[0]
        assert row.dax_ms == 1053
        assert row.display_ms == 31

    def test_other_is_the_residual(self):
        """'Other' is never serialised - it is lifecycle minus the rest."""
        row = parse_performance_export(PERF_EXPORT)[0]
        # 1311 total - 1053 DAX - 31 render = 227 of waiting.
        assert row.other_ms == pytest.approx(227)

    def test_query_pending_captured(self):
        row = parse_performance_export(PERF_EXPORT)[0]
        assert row.query_pending_ms == 12

    def test_query_text_and_rows(self):
        row = parse_performance_export(PERF_EXPORT)[0]
        assert row.row_count == 8
        assert "EVALUATE" in row.query_text

    def test_bare_event_list_accepted(self):
        rows = parse_performance_export(PERF_EXPORT["events"])
        assert len(rows) == 1

    def test_empty_input(self):
        assert parse_performance_export({}) == []
        assert parse_performance_export([]) == []


class TestPageSimulation:
    """Estimating 'Other' before a pane is available to read."""

    @staticmethod
    def _visuals(*durations):
        return [VisualTiming(visual=f"v{i}", dax_ms=d) for i, d in enumerate(durations)]

    def test_single_slot_serialises_everything(self):
        out = simulate_page(self._visuals(100, 200, 300), k_slots=1)
        assert out["est_page_ms"] == 600

    def test_enough_slots_means_no_waiting(self):
        out = simulate_page(self._visuals(100, 200, 300), k_slots=10)
        assert out["est_page_ms"] == 300
        assert out["total_wait_ms"] == 0

    def test_wait_appears_when_slots_are_scarce(self):
        out = simulate_page(self._visuals(100, 100, 100, 100), k_slots=2)
        # Two rounds of 100 ms: two visuals wait 100 ms each.
        assert out["est_page_ms"] == 200
        assert out["total_wait_ms"] == 200

    def test_longest_query_scheduled_first(self):
        # The heaviest query claims a slot first, so it never waits; the rows
        # themselves are reported worst-total-first, which is a different order.
        out = simulate_page(self._visuals(50, 500, 50), k_slots=1)
        longest = next(v for v in out["visuals"] if v["visual"] == "v1")
        assert longest["other_ms"] == 0

    def test_what_if_covers_alternate_slot_counts(self):
        out = simulate_page(self._visuals(100, 100, 100), k_slots=2)
        assert "k=1" in out["what_if"]
        assert out["what_if"]["k=1"] == 300

    def test_what_if_drop_slowest(self):
        out = simulate_page(self._visuals(1000, 10, 10), k_slots=1)
        dropped = out["what_if"]["without_slowest_visual"]
        assert dropped["visual"] == "v0"
        assert dropped["saving_ms"] == 1000

    def test_result_is_labelled_an_estimate(self):
        out = simulate_page(self._visuals(100), k_slots=DEFAULT_QUERY_SLOTS)
        assert "ESTIMATED" in out["note"]
        assert out["visuals"][0]["estimated"] is True

    def test_dict_input_accepted(self):
        out = simulate_page([{"visual": "a", "dax_ms": 100}], k_slots=1)
        assert out["visuals"][0]["visual"] == "a"

    def test_empty_page(self):
        out = simulate_page([], k_slots=4)
        assert out["est_page_ms"] == 0
        assert out["visuals"] == []


class TestSummariseRows:
    def test_totals_and_percentages(self):
        rows = parse_performance_export(PERF_EXPORT)
        out = summarise_rows(rows)
        assert out["totals"]["visuals"] == 1
        assert out["totals"]["dax_ms"] == 1053
        assert out["totals"]["other_pct"] > 0

    def test_wall_clock_from_start_and_end(self):
        out = summarise_rows(parse_performance_export(PERF_EXPORT))
        assert out["totals"]["wall_clock_ms"] == 1311

    def test_empty(self):
        assert summarise_rows([])["visuals"] == []
