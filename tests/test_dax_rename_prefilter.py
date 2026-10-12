"""WP-P wave 2 -- the rename scan skips expressions that cannot contain the old name (P-13).

The pre-filter must be INVISIBLE: every result equals the unfiltered lexer scan.
"""

from __future__ import annotations

import random
import time


from core.dax import dax_rename as dr

NAMES = [
    "Sales",
    "sales",
    "Net Sales",
    "Net  Sales",  # double space in the source, collapsed by _norm
    "O'Brien",
    "a]b",
    "Amount",
    "[x",
    "Total (EUR)",
    "Café",
    "ΣΑ",  # Greek: lower() is context dependent -> never filtered
    "_",
    "'",
]

FRAGMENTS = [
    "SUM(Sales[Amount])",
    "SUM('Sales'[Amount])",
    "CALCULATE([Net Sales], ALL('Net Sales'))",
    "CALCULATE([Net  Sales], 'Net  Sales'[Amount])",
    "COUNTROWS('O''Brien')",
    "'O''Brien'[a]]b]",
    "[a]]b] + [Total (EUR)]",
    "VAR Sales = 1 RETURN Sales",
    '"Sales in a string" & [x]',
    "// Sales in a comment\n[Amount]",
    "SUM('Café'[Amount])",
    "[ΣΑ] + 'ΣΑ'[c]",
    "DIVIDE([A], [B])",
    "EVALUATE Sales",
    "'T'['] + _[_]",
]


def _exprs(rng, n=400):
    out = []
    for _ in range(n):
        out.append(" + ".join(rng.sample(FRAGMENTS, rng.randint(1, 4))))
    out.append("")
    return out


def _all_results():
    rng = random.Random(11)
    res = []
    for expr in _exprs(rng):
        for old in NAMES:
            res.append(dr.rename_table_refs(expr, old, "Renamed"))
            res.append(dr.rename_column_refs(expr, "Sales", old, "Renamed"))
            res.append(
                dr.rename_column_refs(expr, "Sales", old, "Renamed", include_unqualified=True)
            )
            res.append(dr.rename_measure_refs(expr, old, "Renamed"))
            res.append(dr.rename_measure_refs(expr, old, "Renamed", home_table="Sales"))
    return res


def test_prefilter_never_changes_a_result(monkeypatch):
    with_filter = _all_results()
    monkeypatch.setattr(dr, "_PREFILTER_ENABLED", False)
    without = _all_results()
    assert with_filter == without
    assert any(count for _, count in with_filter)  # the fuzz really renamed things


def test_absent_names_skip_the_lexer(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("scan_references must not run for an absent name")

    monkeypatch.setattr(dr, "scan_references", boom)
    expr = "SUM(Orders[Amount]) + [Margin]"
    assert dr.rename_table_refs(expr, "Sales", "X") == (expr, 0)
    assert dr.rename_column_refs(expr, "Orders", "Quantity", "Qty") == (expr, 0)
    assert dr.rename_measure_refs(expr, "Revenue", "Income") == (expr, 0)


def test_present_names_still_scan_and_rename():
    expr = "CALCULATE([Net  Sales], 'Net Sales'[Amount])"  # spacing differs from the name
    new, n = dr.rename_measure_refs(expr, "net sales", "Net Revenue")
    assert n == 1 and "[Net Revenue]" in new


def test_escaped_and_non_ascii_names_are_not_missed():
    assert dr.rename_table_refs("COUNTROWS('O''Brien')", "O'Brien", "Orders")[1] == 1
    assert dr.rename_column_refs("'T'[a]]b]", "T", "a]b", "ab")[1] == 1
    assert dr.rename_table_refs("COUNTROWS('ΣΑ')", "ΣΑ", "X")[1] == 1


def test_pass_over_2000_measures_is_much_cheaper_with_the_filter(monkeypatch):
    measures = [
        f"CALCULATE(SUM(Fact{i % 40}[Amount{i}]), KEEPFILTERS(Dim{i % 20}[Key]))" * 2
        for i in range(2000)
    ]

    def run():
        t = time.perf_counter()
        for m in measures:
            dr.rename_table_refs(m, "NoSuchTable", "X")
        return time.perf_counter() - t

    fast = run()
    monkeypatch.setattr(dr, "_PREFILTER_ENABLED", False)
    slow = run()
    assert fast < slow / 5, (fast, slow)
