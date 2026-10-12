"""Tests for Power Query (M) best-practice detection (Phase 3.7).

The per-expression detection is extracted into the pure helper detect_m_issues()
so rules can be unit-tested without a query executor. scan_m_practices() keeps
its DMV/TOM fetch and delegates to the helper.
"""

from core.analysis.m_practices import detect_m_issues, scan_m_practices


def _rules(name, expr):
    return {i["rule"] for i in detect_m_issues(name, expr)}


# ── Existing rules M001-M004 still fire (lock current behavior) ──────────────


def test_m001_table_buffer():
    assert "M001" in _rules("Q", "let x = Table.Buffer(Source) in x")


def test_m002_web_contents_without_options():
    assert "M002" in _rules("Q", 'let x = Web.Contents("https://api.example.com") in x')


def test_m002_not_fired_when_relativepath_present():
    expr = 'let x = Web.Contents("https://api.example.com", [RelativePath="/v1"]) in x'
    assert "M002" not in _rules("Q", expr)


def test_m003_file_contents_absolute_path():
    assert "M003" in _rules("Q", r'let x = File.Contents("C:\data\in.txt") in x')


def test_m004_is_retired():
    # 2026-10-03 D-18: M004 fired on every query that filters; the useful case (a filter AFTER a
    # merge) is M006
    assert "M004" not in _rules("Q", "let x = Table.SelectRows(Source, each [a] > 1) in x")


# ── New rules M005-M012 ─────────────────────────────────────────────────────


def test_m005_volatile_function_breaks_folding():
    assert "M005" in _rules("Q", 'let x = Table.AddColumn(S, "now", each DateTime.LocalNow()) in x')
    assert "M005" in _rules("Q", "let x = Number.Random() in x")


def test_m005_not_fired_on_plain_query():
    assert "M005" not in _rules("Q", "let x = Table.SelectRows(S, each [a] > 1) in x")


def test_m006_filter_after_merge():
    expr = (
        'let m = Table.NestedJoin(A, "k", B, "k", "nb"),'
        " f = Table.SelectRows(m, each [a] > 1) in f"
    )
    assert "M006" in _rules("Q", expr)


def test_m006_not_fired_when_filter_before_merge():
    expr = (
        "let f = Table.SelectRows(A, each [a] > 1),"
        ' m = Table.NestedJoin(f, "k", B, "k", "nb") in m'
    )
    assert "M006" not in _rules("Q", expr)


def test_m007_hardcoded_secret():
    assert "M007" in _rules("Q", 'let x = Web.Contents("h", [Query=[apikey="SECRET123"]]) in x')
    assert "M007" in _rules("Q", 'let x = Sql.Database("s","d",[Password="p@ss"]) in x')


def test_m008_nested_lookup_in_addcolumn():
    expr = 'let x = Table.AddColumn(S, "v", each Table.SelectRows(Other, each [k]=[k])) in x'
    assert "M008" in _rules("Q", expr)


def test_m009_table_sort_breaks_folding():
    assert "M009" in _rules("Q", 'let x = Table.Sort(S, {{"a", Order.Ascending}}) in x')


def test_m010_csv_excel_absolute_path():
    assert "M010" in _rules("Q", r'let x = Csv.Document(File.Contents("C:\a.csv")) in x')
    assert "M010" in _rules("Q", r'let x = Excel.Workbook(File.Contents("D:/b.xlsx")) in x')


def test_m011_native_query_without_folding_hint():
    assert "M011" in _rules("Q", 'let x = Value.NativeQuery(S, "select 1") in x')


def test_m011_not_fired_with_folding_hint():
    expr = 'let x = Value.NativeQuery(S, "select 1", null, [EnableFolding=true]) in x'
    assert "M011" not in _rules("Q", expr)


def test_m012_table_combine_info():
    # 2026-10-03 D-18: a single Table.Combine is normal; sprawl = more than one
    assert "M012" not in _rules("Q", "let x = Table.Combine({A, B, C}) in x")
    assert "M012" in _rules("Q", "let a = Table.Combine({A, B}), x = Table.Combine({a, C}) in x")


# ── scan_m_practices still delegates correctly ──────────────────────────────


class _FakeExecutor:
    """2026-09-22 (I-11): the scan reads M from the TMSL (partitions + shared
    expressions). The old fake answered the invalid DAX DMV query the scanner
    used to send first; that path is gone."""

    def get_tmsl_definition(self):
        import json

        return {
            "success": True,
            "tmsl": json.dumps(
                {
                    "expressions": [
                        {"name": "Q1", "kind": "m", "expression": "let x = Table.Sort(S, {}) in x"},
                        {"name": "DaxQ", "kind": "dax", "expression": "ignored"},
                    ],
                    "tables": [],
                }
            ),
        }


def test_scan_delegates_to_detector_and_skips_non_m():
    result = scan_m_practices(_FakeExecutor())
    assert result["success"] is True
    rules = {i["rule"] for i in result["issues"]}
    assert "M009" in rules
    # The DAX-kind row must be skipped.
    assert all(i["name"] != "DaxQ" for i in result["issues"])
