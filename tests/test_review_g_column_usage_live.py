"""Review G-19: live ``05_Column_Usage_Mapping get_unused_columns`` (ColumnUsageAnalyzer).

Pre-review the analyzer read ``Name`` from INFO.COLUMNS() rows -- the DMV only
exposes ``ExplicitName`` / ``InferredName`` -- so no column was known and every
column that only a calculated column or a UDF used was "unused"; calc columns
and UDF bodies were never scanned; the cached result never expired after a
live model edit; and the result did not say it is model-only (no report usage).
Every test below fails on base ``07fd722``. Fake query executor, no live calls.
"""

from __future__ import annotations

import pytest

import core.analysis.column_usage_analyzer as cua
from core.analysis.column_usage_analyzer import ColumnUsageAnalyzer


class _FakeQE:
    """INFO.* rows in the live DMV shape (ExplicitName, no Name)."""

    def __init__(self):
        self.info_calls = 0

    def execute_info_query(self, kind, *a, **k):
        self.info_calls += 1
        if kind == "MEASURES":
            return {
                "success": True,
                "rows": [
                    {"Table": "Sales", "Name": "Total", "Expression": "SUM(Sales[Amount])"},
                ],
            }
        if kind == "COLUMNS":
            return {
                "success": True,
                "rows": [
                    {"Table": "Sales", "ExplicitName": "Amount", "ID": 1},
                    {"Table": "Sales", "ExplicitName": "Base", "ID": 2},
                    {
                        "Table": "Sales",
                        "ExplicitName": "Calc",
                        "ID": 3,
                        "Expression": "Sales[Base] * 2",
                    },
                    {"Table": "Sales", "ExplicitName": "Udf Col", "ID": 4},
                    {"Table": "Sales", "InferredName": "Orphan", "ID": 5},
                ],
            }
        if kind == "RELATIONSHIPS":
            return {"success": True, "rows": []}
        return {"success": False, "error": "n/a"}

    def validate_and_execute_dax(self, *a, **k):
        return {"success": False, "error": "no partitions in the fake"}

    def list_udfs(self):
        return {"success": True, "rows": [{"Name": "F", "Expression": "() => SUM(Sales[Udf Col])"}]}


@pytest.fixture
def gen(monkeypatch):
    state = {"g": 1}
    monkeypatch.setattr(cua, "_model_generation", lambda: state["g"], raising=False)
    return state


def _unused(res):
    return sorted(c["column"] for c in res["unused_columns"])


def test_explicit_name_rows_are_indexed(gen):
    res = ColumnUsageAnalyzer(_FakeQE()).get_unused_columns()
    assert res["summary"]["total_columns_analyzed"] == 5
    assert {c["column"] for c in res["used_by_measures"]} >= {"Amount"}


def test_calc_column_and_udf_references_count(gen):
    res = ColumnUsageAnalyzer(_FakeQE()).get_unused_columns()
    # Base is used by the calc column, Udf Col by the UDF body; Calc + Orphan unused
    assert _unused(res) == ["Calc", "Orphan"]


def test_result_is_labelled_model_only(gen):
    res = ColumnUsageAnalyzer(_FakeQE()).get_unused_columns()
    assert res["scope"] == "model-only"
    assert "get_unused_columns_pbip" in res["scope_note"]


def test_cache_expires_on_model_generation_bump(gen):
    qe = _FakeQE()
    an = ColumnUsageAnalyzer(qe)
    an.get_unused_columns()
    n = qe.info_calls
    an.get_unused_columns()
    assert qe.info_calls == n  # same generation -> cached
    gen["g"] = 2
    an.get_unused_columns()
    assert qe.info_calls > n  # live edit bumped the generation -> rebuilt


def test_table_expansion_only_for_table_only_references(gen):
    """A bare table ref (``REMOVEFILTERS('Dim')``) keeps every Dim column; the
    table of a qualified ``Sales[Amount]`` ref must NOT mark all Sales columns."""

    class QE(_FakeQE):
        def execute_info_query(self, kind, *a, **k):
            res = super().execute_info_query(kind, *a, **k)
            if kind == "MEASURES":
                res["rows"].append(
                    {
                        "Table": "Sales",
                        "Name": "All Dim",
                        "Expression": "CALCULATE([Total], REMOVEFILTERS('Dim'))",
                    }
                )
            if kind == "COLUMNS":
                res["rows"].append({"Table": "Dim", "ExplicitName": "Key", "ID": 9})
            return res

    res = ColumnUsageAnalyzer(QE()).get_unused_columns()
    assert "Key" in {c["column"] for c in res["used_by_measures"] if c["table"] == "Dim"}
    assert _unused(res) == ["Calc", "Orphan"]
