"""2026-09-22 deep review, slice I — M practices (I-11 partitions, I-22 tokenisation)."""

from __future__ import annotations

import json

from core.analysis.m_practices import (
    detect_m_issues,
    iter_m_sources,
    m_code_and_strings,
    scan_m_practices,
    strip_m_comments,
)


def _rules(expr):
    return sorted({i["rule"] for i in detect_m_issues("Q", expr)})


# ── I-22 ─────────────────────────────────────────────────────────────────────


def test_sharepoint_url_is_not_an_absolute_path():
    expr = 'let s = Excel.Workbook(Web.Contents("https://contoso.sharepoint.com/x.xlsx", [RelativePath="a"])) in s'
    assert "M010" not in _rules(expr) and "M003" not in _rules(expr)


def test_unc_and_drive_paths_are_absolute():
    assert "M010" in _rules(r'let s = Csv.Document(File.Contents("\\srv\share\a.csv")) in s')
    assert "M003" in _rules(r'let s = File.Contents("C:\temp\a.txt") in s')


def test_selectrows_after_addcolumn_but_outside_its_span_is_not_m008():
    expr = (
        'let a = Table.AddColumn(S, "x", each [k] * 2),'
        " b = Table.SelectRows(a, each [x] > 1) in b"
    )
    assert "M008" not in _rules(expr)


def test_selectrows_inside_addcolumn_is_m008():
    expr = 'let a = Table.AddColumn(S, "v", each Table.RowCount(Table.SelectRows(O, each [k] = 1))) in a'
    assert "M008" in _rules(expr)


def test_comments_do_not_count():
    assert _rules("let x = 1 // Table.Buffer(Source)\nin x") == []
    assert _rules("let x = /* Table.Sort(S, {}) */ 1 in x") == []


def test_calls_inside_string_literals_do_not_count():
    assert _rules('let x = "Table.Buffer(" in x') == []


def test_column_named_password_is_not_a_secret():
    expr = 'let x = Table.SelectColumns(S, {"password", "user"}) in x'
    assert "M007" not in _rules(expr)


def test_connection_string_password_is_a_secret():
    expr = 'let x = Odbc.DataSource("dsn=db;uid=u;pwd=Hunter2") in x'
    assert "M007" in _rules(expr)


def test_second_selectrows_after_merge_is_m006():
    expr = (
        "let f = Table.SelectRows(A, each [a] > 1),"
        ' m = Table.NestedJoin(f, "k", B, "k", "nb"),'
        " g = Table.SelectRows(m, each [b] > 1) in g"
    )
    assert "M006" in _rules(expr)


def test_lexer_keeps_length_and_decodes_strings():
    text = 'a = "x""y" // c\n b'
    assert len(strip_m_comments(text)) == len(text)
    code, strings = m_code_and_strings(text)
    assert strings == ['x"y'] and "c" not in code.split("\n")[0].split("//")[-1]


# ── I-11: partitions are scanned ─────────────────────────────────────────────


def _tmsl():
    return {
        "expressions": [
            {"name": "Param", "kind": "m", "expression": '"x" meta [IsParameterQuery=true]'}
        ],
        "tables": [
            {
                "name": "Sales",
                "partitions": [
                    {
                        "name": "Sales-1",
                        "source": {
                            "type": "m",
                            "expression": [
                                "let",
                                '  S = Table.Buffer(Sql.Database("s", "d"))',
                                "in S",
                            ],
                        },
                    }
                ],
            },
            {
                "name": "Calc",
                "partitions": [
                    {"name": "c", "source": {"type": "calculated", "expression": 'ROW("a", 1)'}}
                ],
            },
        ],
    }


def test_iter_m_sources_includes_partitions_and_expressions():
    kinds = [(s["kind"], s["name"]) for s in iter_m_sources(_tmsl())]
    assert ("expression", "Param") in kinds and ("partition", "Sales / Sales-1") in kinds
    assert all("Calc" not in n for _, n in kinds)


def test_scan_uses_the_given_tmsl_and_reports_total_issues():
    res = scan_m_practices(None, tmsl=json.dumps(_tmsl()))
    assert res["success"] and res["partitions_scanned"] == 1
    assert res["total_issues"] == res["count"] == 1
    assert res["issues"][0]["rule"] == "M001" and res["issues"][0]["table"] == "Sales"


def test_scan_never_sends_the_invalid_dax_dmv_query():
    class Exec:
        def __init__(self):
            self.dax = []

        def validate_and_execute_dax(self, q, *a, **k):
            self.dax.append(q)
            return {"success": False}

        def get_tmsl_definition(self):
            return {"success": True, "tmsl": json.dumps(_tmsl())}

    ex = Exec()
    res = scan_m_practices(ex)
    assert res["success"] and ex.dax == []


def test_scan_falls_back_to_tom_and_says_partitions_were_not_covered():
    class Exec:
        def get_tmsl_definition(self):
            return {"success": False, "error": "no AMO"}

        def enumerate_m_expressions_tom(self, cap):
            return {
                "success": True,
                "rows": [{"Name": "E", "Expression": "Table.Sort(S, {})", "Kind": "M"}],
            }

    res = scan_m_practices(Exec())
    assert res["success"] and res["source"] == "tom_expressions" and "partition" in res["note"]
