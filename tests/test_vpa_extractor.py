"""Tests for core.dax.vpa_extractor (VertiPaq Analyzer parity, WP-C).

Everything runs off a fake query_executor whose DMV rows are fixtures, so no
live Power BI Desktop is required. Covers:

- DMV extraction wiring into pbi_perf_core.vpa (sizes, encoding, relationships)
- DAX statistics batching (COUNTROWS / DISTINCTCOUNT / RI probes) and batch sizes
- include_stats=False skipping every DAX stat query
- the 04_Query_Operations op: arg handling, view selection, not-connected
- export to .json and .csv
"""

from __future__ import annotations

import csv
import json
import re
from unittest.mock import MagicMock

import pytest

from core.dax import vpa_extractor as vpa

# ---------------------------------------------------------------------------
# fixtures: a synthetic 2-table model (Sales fact + Date dim, one relationship)
# ---------------------------------------------------------------------------

MB = 1024 * 1024

STORAGE_TABLES = [
    {
        "TABLE_NAME": "Sales",
        "TABLE_ID": "Sales",
        "ROWS_IN_TABLE": 2_000_000,
        "RI_VIOLATION_COUNT": 0,
    },
    {"TABLE_NAME": "Date", "TABLE_ID": "Date", "ROWS_IN_TABLE": 3_650, "RI_VIOLATION_COUNT": 7},
]

STORAGE_COLUMNS = [
    {
        "TABLE_NAME": "Sales",
        "COLUMN_ID": "c1",
        "COLUMN_NAME": "Amount",
        "Data Type": "Double",
        "DICTIONARY_SIZE_BYTES": 30 * MB,
        "COLUMN_ENCODING_INT": 1,  # HASH on a numeric — a finding
    },
    {
        "TABLE_NAME": "Sales",
        "COLUMN_ID": "c2",
        "COLUMN_NAME": "DateKey",
        "Data Type": "Int64",
        "DICTIONARY_SIZE_BYTES": 0,
        "COLUMN_ENCODING_INT": 2,  # VALUE
    },
    {
        "TABLE_NAME": "Date",
        "COLUMN_ID": "c3",
        "COLUMN_NAME": "Date",
        "Data Type": "DateTime",
        "DICTIONARY_SIZE_BYTES": 1024,
        "COLUMN_ENCODING_INT": 2,
    },
]

SEGMENTS = [
    {
        "TABLE_NAME": "Sales",
        "PARTITION_NAME": "Sales-P1",
        "COLUMN_NAME": "Amount",
        "SEGMENT_NUMBER": 0,
        "SEGMENT_ROWS": 2_000_000,
        "USED_SIZE": 60 * MB,
        "COMPRESSION_TYPE": "V",
        "BITS_COUNT": 21,
    },
    {
        "TABLE_NAME": "Sales",
        "PARTITION_NAME": "Sales-P1",
        "COLUMN_NAME": "DateKey",
        "SEGMENT_NUMBER": 0,
        "SEGMENT_ROWS": 2_000_000,
        "USED_SIZE": 4 * MB,
        "COMPRESSION_TYPE": "R",
        "BITS_COUNT": 12,
    },
    {
        "TABLE_NAME": "Date",
        "PARTITION_NAME": "Date-P1",
        "COLUMN_NAME": "Date",
        "SEGMENT_NUMBER": 0,
        "SEGMENT_ROWS": 3_650,
        "USED_SIZE": 8 * 1024,
        "COMPRESSION_TYPE": "V",
        "BITS_COUNT": 12,
    },
]

COLUMN_HIERARCHIES = [
    {"TABLE_NAME": "Sales", "STRUCTURE_NAME": "Amount", "USED_SIZE": 2 * MB},
    {"TABLE_NAME": "Date", "STRUCTURE_NAME": "Date", "USED_SIZE": 4 * 1024},
]

USER_HIERARCHIES = [{"TABLE_NAME": "Date", "STRUCTURE_NAME": "Calendar", "USED_SIZE": 16 * 1024}]

RELATIONSHIP_SIZES = [
    {"TABLE_NAME": "Sales (37)", "RELATIONSHIP_ID": "R$Sales (37)", "USED_SIZE": 3 * MB}
]

RELATIONSHIPS = [
    {
        "RelationshipID": "37",
        "FromTableID": "1",
        "FromColumnID": "c2",
        "ToTableID": "2",
        "ToColumnID": "c3",
        "IsActive": True,
        "CrossFilteringBehavior": 1,
    }
]

PARTITIONS = [
    {
        "PARTITION_ID": "p1",
        "TABLE_ID": "1",
        "PARTITION_NAME": "Sales-P1",
        "Mode": "Import",
        "State": "Ready",
    },
    {
        "PARTITION_ID": "p2",
        "TABLE_ID": "2",
        "PARTITION_NAME": "Date-P1",
        "Mode": "Import",
        "State": "Ready",
    },
]

TMSCHEMA_TABLES = [{"ID": "1", "Name": "Sales"}, {"ID": "2", "Name": "Date"}]

TMSCHEMA_COLUMNS = [
    {"ID": "c1", "TableID": "1", "ExplicitName": "Amount"},
    {"ID": "c2", "TableID": "1", "ExplicitName": "DateKey"},
    {"ID": "c3", "TableID": "2", "ExplicitName": "Date"},
]

TMSCHEMA_MEASURES = [{"ID": "m1", "Name": "Total Sales"}, {"ID": "m2", "Name": "Sales LY"}]

# H$ attribute-hierarchy rows: the cheap cardinality fallback (ROWS_COUNT - 3).
DMV_CARDINALITY = [
    {"TABLE_NAME": "Sales", "COLUMN_HIERARCHY_ID": "H$Amount (1)", "COLUMN_CARDINALITY": 900_000},
    {"TABLE_NAME": "Date", "COLUMN_HIERARCHY_ID": "H$Date (3)", "COLUMN_CARDINALITY": 3_650},
]


class FakeQE:
    """Query executor stub: routes DMVs by substring, DAX by ROW() shape.

    Records every query so tests can assert on batching and on the absence of
    DAX traffic when include_stats=False.
    """

    def __init__(self, dax_results=None, dmv_overrides=None):
        self.dmv_queries: list[str] = []
        self.dax_queries: list[str] = []
        self._dax_results = dax_results or {}
        self._dmv_overrides = dmv_overrides or {}

    def execute_dmv_query(self, query: str):
        self.dmv_queries.append(query)
        for marker, rows in self._dmv_overrides.items():
            if marker in query:
                return {"success": True, "rows": rows}
        if "DISCOVER_STORAGE_TABLES" in query and "H$" in query:
            return {"success": True, "rows": DMV_CARDINALITY}
        if "DISCOVER_STORAGE_TABLES" in query:
            return {"success": True, "rows": STORAGE_TABLES}
        if "DISCOVER_STORAGE_TABLE_COLUMNS" in query:
            return {"success": True, "rows": STORAGE_COLUMNS}
        if "DISCOVER_STORAGE_TABLE_COLUMN_SEGMENTS" in query:
            if "'H$'" in query:
                return {"success": True, "rows": COLUMN_HIERARCHIES}
            if "'U$'" in query:
                return {"success": True, "rows": USER_HIERARCHIES}
            if "'R$'" in query:
                return {"success": True, "rows": RELATIONSHIP_SIZES}
            return {"success": True, "rows": SEGMENTS}
        if "TMSCHEMA_RELATIONSHIPS" in query:
            return {"success": True, "rows": RELATIONSHIPS}
        if "TMSCHEMA_PARTITIONS" in query:
            return {"success": True, "rows": PARTITIONS}
        if "TMSCHEMA_TABLES" in query:
            return {"success": True, "rows": TMSCHEMA_TABLES}
        if "TMSCHEMA_COLUMNS" in query:
            return {"success": True, "rows": TMSCHEMA_COLUMNS}
        if "TMSCHEMA_MEASURES" in query:
            return {"success": True, "rows": TMSCHEMA_MEASURES}
        return {"success": True, "rows": []}

    def validate_and_execute_dax(self, query: str, top_n=0):
        self.dax_queries.append(query)
        # Route on the ROW() output column, not on the aggregate function: the
        # RI probe contains DISTINCTCOUNT *and* COUNTROWS, so matching on those
        # would mis-route it to the table/column stat rows.
        for marker in ("VpaRelationshipId", "VpaColumn", "VpaTable"):
            if marker in query and marker in self._dax_results:
                return {"success": True, "rows": self._dax_results[marker]}
        for marker, rows in self._dax_results.items():
            if marker in query:
                return {"success": True, "rows": rows}
        return {"success": True, "rows": []}


def _fake_qe_with_stats():
    # Keyed by the ROW() output column FakeQE routes on (see validate_and_execute_dax).
    return FakeQE(
        dax_results={
            "VpaTable": [
                {"VpaTable": "Sales", "VpaCardinality": 2_000_000},
                {"VpaTable": "Date", "VpaCardinality": 3_650},
            ],
            "VpaColumn": [
                {"VpaTable": "Sales", "VpaColumn": "Amount", "VpaCardinality": 1_500_000},
                {"VpaTable": "Sales", "VpaColumn": "DateKey", "VpaCardinality": 3_600},
                {"VpaTable": "Date", "VpaColumn": "Date", "VpaCardinality": 3_650},
            ],
            "VpaRelationshipId": [
                {"VpaRelationshipId": "37", "MissingKeys": 4, "InvalidRows": 120},
            ],
        }
    )


# ---------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------


def test_extract_builds_model_with_sizes_from_dmvs():
    result = vpa.extract_model_metrics(FakeQE(), include_stats=False)
    model = result["model"]

    assert {t.table_name for t in model.tables} == {"Sales", "Date"}
    sales = next(t for t in model.tables if t.table_name == "Sales")
    amount = next(c for c in sales.columns if c.column_name == "Amount")

    # TotalSize = DataSize + DictionarySize + HierarchiesSize (Dax.Model.Extractor)
    assert amount.data_size == 60 * MB
    assert amount.dictionary_size == 30 * MB
    assert amount.hierarchies_size == 2 * MB
    assert amount.total_size == 92 * MB
    assert amount.encoding == "HASH"

    # TableSize = ColumnsTotalSize + UserHierarchiesSize + RelationshipsSize
    assert sales.relationships_size == 3 * MB
    date = next(t for t in model.tables if t.table_name == "Date")
    assert date.user_hierarchies_size == 16 * 1024
    assert date.ri_violation_count == 7


def test_extract_resolves_relationship_ids_to_names():
    result = vpa.extract_model_metrics(FakeQE(), include_stats=False)
    rel = result["model"].relationships[0]

    assert rel.from_table == "Sales"
    assert rel.from_column == "DateKey"
    assert rel.to_table == "Date"
    assert rel.to_column == "Date"
    assert rel.cross_filter == "OneDirection"
    # Size parsed out of the "R$Sales (37)" TABLE_ID suffix
    assert rel.size == 3 * MB


def test_extract_uses_dmv_cardinality_when_stats_skipped():
    result = vpa.extract_model_metrics(FakeQE(), include_stats=False)
    sales = next(t for t in result["model"].tables if t.table_name == "Sales")
    amount = next(c for c in sales.columns if c.column_name == "Amount")
    assert amount.cardinality == 900_000  # H$ ROWS_COUNT - 3


def test_extract_counts_measures_and_partitions():
    result = vpa.extract_model_metrics(FakeQE(), include_stats=False)
    assert result["measure_count"] == 2
    assert [p["TableName"] for p in result["partitions"]] == ["Sales", "Date"]
    sales = next(t for t in result["model"].tables if t.table_name == "Sales")
    assert sales.partitions_count == 1


# ---------------------------------------------------------------------------
# DAX statistics + batching
# ---------------------------------------------------------------------------


def test_include_stats_false_runs_no_dax_queries():
    qe = _fake_qe_with_stats()
    result = vpa.extract_model_metrics(qe, include_stats=False)

    assert qe.dax_queries == []
    assert result["stats_included"] is False
    assert result["stats_queries"] == 0
    assert qe.dmv_queries, "DMVs must still run"


def test_include_stats_true_overrides_cardinality_from_dax():
    qe = _fake_qe_with_stats()
    result = vpa.extract_model_metrics(qe, include_stats=True)

    sales = next(t for t in result["model"].tables if t.table_name == "Sales")
    amount = next(c for c in sales.columns if c.column_name == "Amount")
    # DAX DISTINCTCOUNT (1.5M) wins over the H$ fallback (900k)
    assert amount.cardinality == 1_500_000
    assert sales.rows_count == 2_000_000
    assert result["stats_included"] is True
    assert result["stats_queries"] == 3  # 1 table batch + 1 column batch + 1 rel batch


def test_relationship_ri_probe_populates_missing_keys():
    qe = _fake_qe_with_stats()
    result = vpa.extract_model_metrics(qe, include_stats=True)
    rel = result["model"].relationships[0]
    assert rel.missing_keys == 4
    assert rel.invalid_rows == 120


def test_relationship_probe_uses_userelationship_shape():
    qe = _fake_qe_with_stats()
    vpa.extract_model_metrics(qe, include_stats=True)
    probe = next(q for q in qe.dax_queries if "USERELATIONSHIP" in q)

    assert "CALCULATETABLE(" in probe
    assert "\"MissingKeys\", DISTINCTCOUNT('Sales'[DateKey])" in probe
    assert "\"InvalidRows\", COUNTROWS('Sales')" in probe
    assert "ISBLANK('Date'[Date])" in probe
    assert "USERELATIONSHIP('Sales'[DateKey], 'Date'[Date])" in probe


def test_table_stats_batch_at_50_per_query():
    qe = FakeQE()
    tables = [f"T{i}" for i in range(120)]
    vpa.collect_table_stats(qe, tables)

    assert len(qe.dax_queries) == 3  # 50 + 50 + 20
    assert qe.dax_queries[0].count("COUNTROWS(") == 50
    assert qe.dax_queries[-1].count("COUNTROWS(") == 20


def test_column_stats_batch_at_50_per_query():
    qe = FakeQE()
    columns = [("Sales", f"c{i}") for i in range(75)]
    vpa.collect_column_stats(qe, columns)

    assert len(qe.dax_queries) == 2
    assert qe.dax_queries[0].count("DISTINCTCOUNT(") == 50
    assert qe.dax_queries[1].count("DISTINCTCOUNT(") == 25


def test_column_stats_uses_allnoblankrow_for_group_by_columns():
    qe = FakeQE()
    vpa.collect_column_stats(qe, [("FP", "Measure")], group_by_columns={("FP", "Measure")})
    assert "COUNTROWS(ALLNOBLANKROW('FP'[Measure]))" in qe.dax_queries[0]
    assert "DISTINCTCOUNT" not in qe.dax_queries[0]


def test_relationship_stats_batch_at_10_per_query():
    qe = FakeQE()
    rels = [
        {
            "id": str(i),
            "from_table": "Sales",
            "from_column": "DateKey",
            "to_table": "Date",
            "to_column": "Date",
        }
        for i in range(25)
    ]
    vpa.collect_relationship_stats(qe, rels)

    assert len(qe.dax_queries) == 3  # 10 + 10 + 5
    assert qe.dax_queries[0].count("USERELATIONSHIP(") == 10
    assert qe.dax_queries[-1].count("USERELATIONSHIP(") == 5


def test_column_stats_retries_singly_when_a_batch_fails():
    """One unqueryable column must not blank out the rest of its batch."""

    class FailingBatchQE(FakeQE):
        def validate_and_execute_dax(self, query, top_n=0):
            self.dax_queries.append(query)
            if query.count("DISTINCTCOUNT(") > 1:
                return {"success": False, "error": "cannot be determined"}
            table = re.search(r"\"VpaTable\", \"([^\"]+)\"", query).group(1)
            column = re.search(r"\"VpaColumn\", \"([^\"]+)\"", query).group(1)
            return {
                "success": True,
                "rows": [{"VpaTable": table, "VpaColumn": column, "VpaCardinality": 42}],
            }

    qe = FailingBatchQE()
    stats = vpa.collect_column_stats(qe, [("Sales", "A"), ("Sales", "B")])

    assert stats == {"Sales[A]": 42, "Sales[B]": 42}


def test_dax_stat_exception_degrades_to_empty():
    qe = MagicMock()
    qe.validate_and_execute_dax.side_effect = RuntimeError("engine busy")
    assert vpa.collect_table_stats(qe, ["Sales"]) == {}


# ---------------------------------------------------------------------------
# findings
# ---------------------------------------------------------------------------


def test_findings_cover_the_documented_thresholds():
    result = vpa.extract_model_metrics(_fake_qe_with_stats(), include_stats=True)
    findings = vpa.build_findings(result["model"], top_n=15, core_count=8)
    codes = {f["code"] for f in findings}

    assert "DOMINANT_COLUMN" in codes  # largest columns by size
    assert "HASH_ENCODED_NUMERIC" in codes  # Sales[Amount] is HASH on a Double
    assert "RI_VIOLATION" in codes  # Date has 7 violations
    assert "HIGH_CARDINALITY_NUMERIC" in codes  # Amount 1.5M distinct
    assert "SEGMENTS_BELOW_CORES" in codes  # 2M rows in 1 segment on 8 cores

    for finding in findings:
        assert finding["severity"] in ("high", "medium", "low", "info")
        assert finding["object"], "every finding names an object"
        assert len(finding["detail"]) > 20, "detail must be actionable prose"

    severities = [f["severity"] for f in findings]
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    assert severities == sorted(severities, key=lambda s: order[s])


def test_findings_report_missing_keys_and_low_ratio():
    result = vpa.extract_model_metrics(_fake_qe_with_stats(), include_stats=True)
    findings = vpa.build_findings(result["model"], core_count=8)
    by_code = {f["code"]: f for f in findings}

    assert "MISSING_KEYS" in by_code
    assert "4" in by_code["MISSING_KEYS"]["detail"]
    # to_cardinality 3650 / from_cardinality 3600 -> ratio ~1.01, no LOW ratio flag
    assert "LOW_ONE_TO_MANY_RATIO" not in by_code


def test_findings_flag_wide_tables():
    columns = [
        {
            "TABLE_NAME": "Wide",
            "COLUMN_ID": f"c{i}",
            "COLUMN_NAME": f"Col{i}",
            "Data Type": "String",
            "DICTIONARY_SIZE_BYTES": 1024,
            "COLUMN_ENCODING_INT": 1,
        }
        for i in range(60)
    ]
    # Override only the columns DMV; the tables DMV keeps the standard fixture.
    qe = FakeQE(dmv_overrides={"COLUMN_TYPE = 'BASIC_DATA'": columns})
    result = vpa.extract_model_metrics(qe, include_stats=False)
    findings = vpa.build_findings(result["model"], core_count=8)

    wide = [f for f in findings if f["code"] == "WIDE_TABLE"]
    assert wide and wide[0]["object"] == "Wide"


# ---------------------------------------------------------------------------
# the op
# ---------------------------------------------------------------------------


@pytest.fixture
def connected(monkeypatch):
    """Install a connected connection_state whose executor is our fake."""
    import core.infrastructure.connection_state as cs_mod

    qe = _fake_qe_with_stats()
    state = MagicMock()
    state.is_connected.return_value = True
    state.query_executor = qe
    monkeypatch.setattr(cs_mod, "connection_state", state)
    return qe


def test_op_default_view_is_summary(connected):
    result = vpa.vertipaq_analyzer()

    assert result["success"] is True
    assert result["view"] == "summary"
    assert "summary" in result and "findings" in result
    # summary-only: the heavy per-object views are not inlined
    assert "columns" not in result
    assert "tables" not in result
    assert result["summary"]["measures"] == 2
    assert result["summary"]["model_size_bytes"] > 0


@pytest.mark.parametrize(
    "view,key",
    [
        ("tables", "tables"),
        ("columns", "columns"),
        ("relationships", "relationships"),
        ("partitions", "partitions"),
        ("segments", "segments"),
    ],
)
def test_op_view_selection(connected, view, key):
    result = vpa.vertipaq_analyzer(view=view)
    assert result["success"] is True
    assert result["view"] == view
    assert isinstance(result[key], list) and result[key]


def test_op_view_all_returns_every_section(connected):
    result = vpa.vertipaq_analyzer(view="all")
    for key in ("tables", "columns", "relationships", "partitions", "segments"):
        assert key in result, key


def test_op_top_n_truncates(connected):
    result = vpa.vertipaq_analyzer(view="columns", top_n=1)
    assert len(result["columns"]) == 1
    # top_n sorts by size desc — Sales[Amount] is the biggest column
    assert result["columns"][0]["column"] == "Amount"


def test_op_include_stats_false_skips_dax(connected):
    result = vpa.vertipaq_analyzer(include_stats=False)
    assert connected.dax_queries == []
    assert result["stats_included"] is False


def test_op_rejects_unknown_view(connected):
    result = vpa.vertipaq_analyzer(view="nonsense")
    assert result["success"] is False
    assert result["error_type"] == "invalid_args"


def test_op_not_connected(monkeypatch):
    import core.infrastructure.connection_state as cs_mod

    state = MagicMock()
    state.is_connected.return_value = False
    monkeypatch.setattr(cs_mod, "connection_state", state)

    result = vpa.vertipaq_analyzer()
    assert result["success"] is False
    assert result["error_type"] == "not_connected"


def test_op_reports_dmv_unavailable(monkeypatch):
    import core.infrastructure.connection_state as cs_mod

    empty = MagicMock()
    empty.execute_dmv_query.return_value = {"success": True, "rows": []}
    empty.validate_and_execute_dax.return_value = {"success": True, "rows": []}
    state = MagicMock()
    state.is_connected.return_value = True
    state.query_executor = empty
    monkeypatch.setattr(cs_mod, "connection_state", state)

    result = vpa.vertipaq_analyzer(include_stats=False)
    assert result["success"] is False
    assert result["error_type"] == "dmv_unavailable"


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def test_export_json_writes_full_payload(connected, tmp_path):
    target = tmp_path / "vpa.json"
    result = vpa.vertipaq_analyzer(view="all", export_path=str(target))

    assert result["export"]["format"] == "json"
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["summary"]["model_size_bytes"] == result["summary"]["model_size_bytes"]
    assert written["columns"] and written["findings"]


def test_export_csv_writes_the_columns_view(connected, tmp_path):
    target = tmp_path / "vpa.csv"
    result = vpa.vertipaq_analyzer(view="columns", export_path=str(target))

    assert result["export"]["format"] == "csv"
    with target.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == result["export"]["rows_written"] == 3
    assert {"table", "column", "total_size", "encoding"} <= set(rows[0])


def test_export_rejects_unsupported_extension(connected, tmp_path):
    result = vpa.vertipaq_analyzer(export_path=str(tmp_path / "vpa.txt"))
    assert result["success"] is False
    assert result["error_type"] == "invalid_args"


def test_export_creates_missing_parent_directory(connected, tmp_path):
    target = tmp_path / "nested" / "deep" / "vpa.json"
    result = vpa.vertipaq_analyzer(export_path=str(target))
    assert result["export"]["success"] is True
    assert target.is_file()


# ---------------------------------------------------------------------------
# handler dispatch
# ---------------------------------------------------------------------------


def test_handler_dispatches_vertipaq_analyzer(monkeypatch):
    import server.handlers.query_handler as qh

    state = MagicMock()
    state.is_connected.return_value = True
    monkeypatch.setattr(qh, "connection_state", state)

    captured = {}

    def fake_op(**kwargs):
        captured.update(kwargs)
        return {"success": True}

    monkeypatch.setattr(vpa, "vertipaq_analyzer", fake_op)

    result = qh.handle_query_operations(
        {
            "operation": "vertipaq_analyzer",
            "include_stats": False,
            "top_n": 10,
            "view": "columns",
            "export_path": "out.json",
        }
    )

    assert result["success"] is True
    assert captured == {
        "include_stats": False,
        "top_n": 10,
        "view": "columns",
        "export_path": "out.json",
    }


def test_handler_defaults_for_vertipaq_analyzer(monkeypatch):
    import server.handlers.query_handler as qh

    state = MagicMock()
    state.is_connected.return_value = True
    monkeypatch.setattr(qh, "connection_state", state)

    captured = {}
    monkeypatch.setattr(
        vpa, "vertipaq_analyzer", lambda **kw: captured.update(kw) or {"success": True}
    )

    qh.handle_query_operations({"operation": "vertipaq_analyzer"})

    assert captured["include_stats"] is True
    assert captured["top_n"] == 50
    assert captured["view"] == "summary"
    assert captured["export_path"] is None


def test_existing_vertipaq_report_op_still_dispatches(monkeypatch):
    """WP-C must not repurpose the pre-existing size reporter."""
    import server.handlers.query_handler as qh

    state = MagicMock()
    state.is_connected.return_value = True
    monkeypatch.setattr(qh, "connection_state", state)

    import core.dax.vertipaq_reporter as reporter

    captured = {}
    monkeypatch.setattr(
        reporter,
        "vertipaq_report",
        lambda **kw: captured.update(kw) or {"success": True, "columns": []},
    )

    result = qh.handle_query_operations({"operation": "vertipaq_report", "top_n": 5})

    assert result["success"] is True
    assert captured["top_n"] == 5


def test_vertipaq_analyzer_is_in_the_tool_schema():
    from server.tool_schemas import TOOL_SCHEMAS

    props = TOOL_SCHEMAS["query_operations"]["properties"]
    assert "vertipaq_analyzer" in props["operation"]["enum"]
    assert "vertipaq_report" in props["operation"]["enum"]  # kept intact
    assert "include_stats" in props
    assert "export_path" in props
    assert set(vpa.VALID_VIEWS) <= set(props["view"]["enum"])
