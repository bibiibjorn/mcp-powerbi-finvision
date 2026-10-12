"""2026-09-22 deep review, slice D — parse_dax_references on the lexer (D-02).

Its output feeds get_unused_columns_pbip, 05 dependencies/impact and the
destructive-op evidence gate: a missed reference makes a used column look
unused (and deletable); a phantom one inflates blast radii.
"""

import time

from core.dax.dax_reference_parser import (
    DaxReferenceIndex,
    parse_dax_references,
    scan_references,
)


def refs(expr, idx=None, **kw):
    return parse_dax_references(expr, idx, **kw)


def test_references_after_a_url_in_a_string_are_kept():
    """SVG measures carry xmlns='http://…' — the old // comment stripper ate the rest of the line."""
    svg = (
        "\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg'>\" & [Bar Width] & "
        "SELECTEDVALUE('Dim Client'[Colour]) & [Label]"
    )
    r = refs(svg)
    assert ("Dim Client", "Colour") in r["columns"]
    assert {m for _, m in r["measures"]} == {"Bar Width", "Label"}


def test_escaped_apostrophe_table():
    r = refs("SUM('O''Brien'[X])")
    assert r["tables"] == ["O'Brien"] and r["columns"] == [("O'Brien", "X")]


def test_escaped_bracket_column():
    r = refs("SUM(T[Col ]]name])")
    assert r["columns"] == [("T", "Col ]name")] and r["measures"] == []


def test_comment_markers_inside_strings():
    r = refs('"/*" & [A] & "*/" & [B]')
    assert {m for _, m in r["measures"]} == {"A", "B"}


def test_dash_dash_comment_is_ignored():
    r = refs("SUM(T[a]) -- uses [Ghost]\n+ [Real]")
    assert r["measures"] == [("", "Real")]


def test_format_string_is_not_a_reference():
    idx = DaxReferenceIndex(
        measure_rows=[{"Table": "m", "Name": "Sales"}, {"Table": "m", "Name": "Red"}]
    )
    r = refs('FORMAT([Sales], "0;[Red]-0")', idx)
    assert r["measures"] == [("m", "Sales")]
    assert "Red" not in r["identifiers"]


def test_treatas_table_constructor():
    r = refs('CALCULATE([S], TREATAS({"a","b"}, T[c]))')
    assert r["tables"] == ["T"] and r["columns"] == [("T", "c")]


def test_whitespace_between_table_and_column():
    r = refs("SUM('Sales' [Amount])")
    assert r["columns"] == [("Sales", "Amount")] and r["measures"] == []


def test_quoted_table_arguments_anywhere():
    assert refs("SUMX('Sales', [Qty])")["tables"] == ["Sales"]
    assert refs("COUNTROWS(CROSSJOIN('A', 'B'))")["tables"] == ["A", "B"]


def test_unquoted_qualified_column_does_not_mark_same_named_columns_elsewhere():
    idx = DaxReferenceIndex(
        measure_rows=[{"Table": "m", "Name": "Sales"}],
        column_rows=[
            {"Table": "Sales", "Name": "Date"},
            {"Table": "Budget", "Name": "Date"},
            {"Table": "Calendar", "Name": "Date"},
        ],
    )
    r = refs("CALCULATE([Sales], Sales[Date] = TODAY())", idx)
    assert r["columns"] == [("Sales", "Date")]


def test_lone_known_column_still_marks_all_candidates():
    """Row-context calc columns reference [Col] unqualified — stay conservative."""
    idx = DaxReferenceIndex(column_rows=[{"Table": "A", "Name": "K"}, {"Table": "B", "Name": "K"}])
    assert set(refs("[K] * 2", idx)["columns"]) == {("A", "K"), ("B", "K")}


def test_qualified_measure_reference_classified_with_index():
    idx = DaxReferenceIndex(measure_rows=[{"Table": "m", "Name": "Margin %"}])
    r = refs("'m'[Margin %] + m[margin %]", idx)
    assert r["measures"] == [("m", "Margin %"), ("m", "margin %")]
    assert r["columns"] == []


def test_bare_table_names_by_position_and_by_index():
    r = refs("COUNTROWS(Sales) + DATEADD('Date'[Date], -1, YEAR)")
    assert r["tables"] == ["Date", "Sales"]
    idx = DaxReferenceIndex(column_rows=[{"Table": "Budget", "Name": "x"}])
    assert "Budget" in refs("IF(ISEMPTY(Budget), 0, 1) + [m]", idx)["tables"]


def test_var_table_is_not_a_model_table():
    r = refs("VAR _t = FILTER(Sales, Sales[Qty] > 1) RETURN COUNTROWS(_t)")
    assert r["tables"] == ["Sales"]


def test_extension_columns_are_not_phantom_measures():
    r = refs('SUMX(ADDCOLUMNS(VALUES(T[k]), "Tmp", [M]), [Tmp])')
    assert r["measures"] == [("", "M")]


def test_enhanced_relationship_functions():
    r = refs(
        "CALCULATE([S], USERELATIONSHIP(Sales[Ship], 'Date'[Date]), "
        "CROSSFILTER(Sales[C], Cust[C], BOTH), TREATAS(VALUES(A[x]), B[y], C[z])) "
        "+ SUMX(RELATEDTABLE('O''S'), RELATED(P[Price]))",
        enhanced=True,
    )
    fns = [x["function"] for x in r["relationship_refs"]]
    assert fns.count("TREATAS") == 2
    assert "USERELATIONSHIP" in fns and "CROSSFILTER" in fns and "RELATED" in fns
    assert r["uses_inactive_relationships"] is True
    rt = [x for x in r["relationship_refs"] if x["function"] == "RELATEDTABLE"][0]
    assert rt["from_table"] == "O'S"
    cf = [x for x in r["relationship_refs"] if x["function"] == "CROSSFILTER"][0]
    assert cf["direction"] == "BOTH"


def test_enhanced_variables_and_filter_columns():
    expr = (
        "VAR TotalSales = [S] RETURN CALCULATE(TotalSales, Sales[Region] = \"EU\", 'P'[C] IN {1})"
    )
    r = refs(expr, enhanced=True)
    assert [v["name"] for v in r["variables"]] == ["TotalSales"]
    assert r["variables"][0]["position"] == expr.index("TotalSales")
    assert set(r["filter_columns"]) == {("Sales", "Region"), ("P", "C")}


def test_scan_references_spans_point_into_source():
    expr = "SUM('T'[a]) + T[b] + [m]"
    for ref in scan_references(expr):
        if ref.kind == "qualified":
            assert expr[ref.name_start : ref.name_end] in ("[a]", "[b]")
        if ref.kind == "bracket":
            assert expr[ref.name_start : ref.name_end] == "[m]"


def test_parse_is_fast_enough_for_large_models():
    exprs = [
        f"CALCULATE([M{i}], 'Date'[Year] = 2024, KEEPFILTERS(Product[Color] = \"Red\")) "
        f"+ DIVIDE([Sales], [Units]) // note {i}"
        for i in range(1500)
    ]
    start = time.perf_counter()
    for e in exprs:
        parse_dax_references(e)
    assert time.perf_counter() - start < 5.0
