"""Tests for VertiPaq DMV size reporter."""

from core.dax.vertipaq_reporter import _verdict_for, build_report

# ---- _verdict_for ---------------------------------------------------------


def test_verdict_hot_when_total_over_50mb():
    v = _verdict_for(total_mb=80.0, cardinality=1000, rle_efficiency=0.9)
    assert v["verdict"] == "hot"
    assert "50" in v["verdict_reason"]


def test_verdict_hot_when_over_10mb_and_bad_rle():
    v = _verdict_for(total_mb=15.0, cardinality=1000, rle_efficiency=0.1)
    assert v["verdict"] == "hot"
    assert "RLE" in v["verdict_reason"]


def test_verdict_warn_when_over_10mb_with_good_rle():
    v = _verdict_for(total_mb=15.0, cardinality=1000, rle_efficiency=0.9)
    assert v["verdict"] == "warn"


def test_verdict_warn_when_high_cardinality():
    v = _verdict_for(total_mb=1.0, cardinality=2_000_000, rle_efficiency=0.9)
    assert v["verdict"] == "warn"
    assert "cardinality" in v["verdict_reason".lower()] or "Cardinality" in v["verdict_reason"]


def test_verdict_info_for_small_columns():
    v = _verdict_for(total_mb=0.5, cardinality=100, rle_efficiency=0.95)
    assert v["verdict"] == "info"


# ---- build_report --------------------------------------------------------
# 2026-09-22 review E-17/E-19: build_report is a projection of the shared
# extractor (core.dax.vpa_extractor), so the fake answers ITS DMVs (the old
# fixtures used COLUMN_CARDINALITY / COLUMN_HIERARCHIES_SIZE / a DICTIONARIES
# rowset — fields the real DMVs do not have).

MB = 1024 * 1024


class _FakeQE:
    """Routes vpa_extractor's DMV queries to canned rows."""

    def __init__(self, columns, segments, tables=None):
        self.columns = columns
        self.segments = segments
        self.tables = tables if tables is not None else sorted({c["TABLE_NAME"] for c in columns})

    def execute_dmv_query(self, query):
        if "DISCOVER_STORAGE_TABLES" in query and "H$" not in query:
            return {
                "success": True,
                "rows": [
                    {"TABLE_NAME": t, "TABLE_ID": t, "ROWS_IN_TABLE": 1000} for t in self.tables
                ],
            }
        if "DISCOVER_STORAGE_TABLE_COLUMNS " in query or query.endswith(
            "DISCOVER_STORAGE_TABLE_COLUMNS WHERE COLUMN_TYPE = 'BASIC_DATA'"
        ):
            return {"success": True, "rows": self.columns}
        if "DISCOVER_STORAGE_TABLE_COLUMN_SEGMENTS" in query and "'$'" in query:
            return {"success": True, "rows": self.segments}
        return {"success": True, "rows": []}


def _col(table, cid, name, enc=2, dict_size=0, dtype="Int64"):
    return {
        "TABLE_NAME": table,
        "COLUMN_ID": cid,
        "COLUMN_NAME": name,
        "Data Type": dtype,
        "DICTIONARY_SIZE_BYTES": dict_size,
        "COLUMN_ENCODING_INT": enc,
    }


def _seg(table, cid, size):
    return {"TABLE_NAME": table, "COLUMN_NAME": cid, "USED_SIZE": size, "BITS_COUNT": 8}


def test_build_report_returns_top_n_sorted():
    qe = _FakeQE(
        [
            _col("Sales", "Amount (1)", "Amount", enc=1, dict_size=5 * MB, dtype="Double"),
            _col("Sales", "Qty (2)", "Qty"),
        ],
        [_seg("Sales", "Amount (1)", 100 * MB), _seg("Sales", "Qty (2)", 1 * MB)],
    )
    res = build_report(qe, top_n=10)
    assert res["success"] is True
    assert res["columns"][0]["column"] == "Amount"
    assert res["columns"][0]["total_size_mb"] > res["columns"][1]["total_size_mb"]
    assert res["columns"][0]["dict_size_mb"] == 5.0  # dictionary counted (was always 0)
    assert res["columns"][0]["encoding"] == "HASH"  # code 1 = HASH (LIVE-CHECKS)
    assert res["columns"][0]["verdict"] == "hot"  # 105 MB total


def test_build_report_truncates():
    qe = _FakeQE(
        [_col("T", f"col{i} ({i})", f"col{i}") for i in range(20)],
        [_seg("T", f"col{i} ({i})", (20 - i) * 1024) for i in range(20)],
    )
    res = build_report(qe, top_n=5)
    assert res["success"] is True
    assert res["truncated"] is True
    assert len(res["columns"]) == 5
    assert res["column_count_total"] == 20


def test_build_report_includes_table_rollup():
    qe = _FakeQE(
        [
            _col("Sales", "A (1)", "A"),
            _col("Sales", "B (2)", "B"),
            _col("Date", "Year (3)", "Year"),
        ],
        [
            _seg("Sales", "A (1)", 1 * MB),
            _seg("Sales", "B (2)", 2 * MB),
            _seg("Date", "Year (3)", 100 * 1024),
        ],
    )
    res = build_report(qe, top_n=10, include_table_rollup=True)
    assert res["success"] is True
    rollup_by_table = {r["table"]: r for r in res["table_rollup"]}
    assert rollup_by_table["Sales"]["column_count"] == 2
    assert rollup_by_table["Date"]["column_count"] == 1
    assert res["table_rollup"][0]["table"] == "Sales"


def test_build_report_dmv_unavailable():
    qe = _FakeQE([], [], tables=[])
    res = build_report(qe)
    assert res["success"] is False
    assert res["error_type"] == "dmv_unavailable"


def test_build_report_skip_verdict():
    qe = _FakeQE([_col("T", "A (1)", "A")], [_seg("T", "A (1)", 1024)])
    res = build_report(qe, include_verdict=False)
    assert res["success"] is True
    assert "verdict" not in res["columns"][0]
    assert "stats" not in res
