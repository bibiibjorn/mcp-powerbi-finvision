"""F-15 (2026-10-03 deep review): copy-pasted DAX detection (``core.dax.clone_detector``),
``05_DAX_Intelligence find_clones`` and the calc-group-by-structure candidate finder.
"""

from unittest.mock import MagicMock

import pytest

import server.handlers.dax_context_handler as dh
from core.calc_group_migration.detector import detect_structural
from core.dax.clone_detector import (
    analyse_measures,
    find_clones,
    normalise,
    suggest_function_name,
)
from core.dax.tokenizer import DaxLexer

MEASURES = {"Sales", "Cost", "Margin"}


def _m(name, expr, table="M", kind="measure"):
    return {"kind": kind, "table": table, "name": name, "expression": expr}


YTD = "CALCULATE([{b}], DATESYTD('Date'[Date]), 'Date'[Year] = 2024)"
WTD = (
    "VAR _x = SUMX(Sales, Sales[{c}] * Sales[Qty]) VAR _y = SUMX(Sales, Sales[{c}]) "
    "RETURN DIVIDE(_x, _y, 0) * 100"
)
LBL = (
    "IF(HASONEVALUE('Region'[Name]), \"{p}\" & SELECTEDVALUE('Region'[Name]) & \" / \" "
    '& FORMAT([{b}], "{f}"), "All")'
)


def _groups(objs, **kw):
    return find_clones(objs, measure_names=MEASURES, min_tokens=8, **kw)["groups"]


# --- normalisation -----------------------------------------------------------------------------


def test_normalise_abstracts_names_and_literals_but_keeps_structure():
    a = normalise("CALCULATE([Sales], 'Date'[Year] = 2024, Sales[Region] = \"EU\")")
    b = normalise("calculate([Cost], 'Date'[Year] = 2023, Sales[Region] = \"US\")")
    assert a.signature == b.signature
    c = normalise("CALCULATE([Sales], 'Date'[Year] = 2024)")
    assert a.signature != c.signature


def test_normalise_ignores_comments_whitespace_and_var_names():
    a = normalise("VAR _a = [Sales] // c\nRETURN _a + 1")
    b = normalise("VAR  _renamed =   [Cost]\n/* x */ RETURN _renamed + 2")
    assert a.signature == b.signature
    assert [u.value for u in a.units if u.kind == "var"] == ["_A", "_A"]


def test_unit_table_column_pair_is_one_unit():
    nz = normalise("SUM(Sales[Amount])")
    assert [u.kind for u in nz.units] == ["fn", "punct", "col", "punct"]
    assert nz.units[2].text == "Sales[Amount]"


# --- groups -----------------------------------------------------------------------------------


def test_structural_group_with_calc_group_proposal_when_only_the_base_measure_differs():
    objs = [_m(f"{b} YTD", YTD.format(b=b)) for b in ("Sales", "Cost", "Margin")]
    (g,) = _groups(objs)
    assert g["match"] == "structural" and g["scope"] == "measure" and g["size"] == 3
    assert g["recommended"] is True and g["extractable_by_transform"] is True
    (p,) = g["parameters"]
    assert p["kind"] == "measure" and p["mode"] == "expr" and p["type_hint"] == "MeasureRef"
    assert g["proposal"]["type"] == "calc_group_item"
    assert g["proposal"]["alternative"] == "udf"
    item = g["proposal"]["calc_group"]["item_expression"]
    assert "SELECTEDMEASURE" in item and "DATESYTD" in item
    assert [r["base_measure"] for r in g["proposal"]["calc_group"]["replaces"]] == [
        "[Sales]",
        "[Cost]",
        "[Margin]",
    ]
    udf = g["proposal"]["udf"]
    assert udf["signature"] == "(measureRef : MeasureRef)"
    assert udf["compatibility_level_required"] == 1702
    assert [c["call"] for c in udf["calls"]] == [
        f"{g['proposal']['suggested_function_name']} ( {b} )"
        for b in ("[Sales]", "[Cost]", "[Margin]")
    ]


def test_column_difference_is_a_columnref_param_in_every_position_it_occurs():
    objs = [_m(f"{c} Wtd", WTD.format(c=c)) for c in ("Amount", "Cost", "Price")]
    (g,) = _groups(objs, include_fragments=False)
    (p,) = g["parameters"]
    assert p["kind"] == "col" and p["type_hint"] == "ColumnRef" and p["mode"] == "expr"
    assert p["occurrences"] == 2  # both Sales[<col>] positions share one parameter
    assert g["proposal"]["type"] == "udf"
    body = g["proposal"]["udf"]["expression"]
    assert "SUMX ( Sales, columnRef * Sales[Qty] )" in body
    assert body.count("columnRef") == 3  # header + 2 uses


def test_literals_are_val_scalars_and_a_measure_in_a_calculate_stays_lazy():
    objs = [
        _m("Lbl 1", LBL.format(p="Region: ", b="Sales", f="#,0")),
        _m("Lbl 2", LBL.format(p="Country: ", b="Cost", f="0.0")),
    ]
    (g,) = _groups(objs, include_fragments=False)
    by_kind = {p["kind"]: p for p in g["parameters"] if p["kind"] != "str"}
    strs = [p for p in g["parameters"] if p["kind"] == "str"]
    assert [p["mode"] for p in strs] == ["val", "val"]
    assert all(p["type_hint"] == "Scalar String Val" for p in strs)
    # the measure sits in the ELSE-less THEN branch of an IF: a conditional frame -> lazy MeasureRef
    assert by_kind["measure"]["mode"] == "expr"
    assert g["recommended"] is False  # only 2 copies


def test_measure_in_the_callers_own_context_is_a_val_param():
    objs = [
        _m("A pct", "DIVIDE([Sales], [Cost], 0) * 100 + 0.5 + [Margin] - 1 + 2 + 3 + 4"),
        _m("B pct", "DIVIDE([Cost], [Cost], 0) * 100 + 0.5 + [Margin] - 1 + 2 + 3 + 4"),
    ]
    (g,) = _groups(objs, include_fragments=False)
    (p,) = g["parameters"]
    assert p["mode"] == "val" and p["type_hint"] == "AnyVal Val"


def test_exact_duplicates_have_no_udf_proposal():
    objs = [_m("A", YTD.format(b="Sales")), _m("B", YTD.format(b="Sales").lower())]
    (g,) = _groups(objs)
    assert g["match"] == "exact_duplicate" and g["parameters"] == []
    assert g["proposal"]["type"] is None and g["extractable_by_transform"] is False


def test_near_clones_report_differences_and_no_proposal():
    objs = [
        _m("Sales YTD", YTD.format(b="Sales")),
        _m("Cost YTD", YTD.format(b="Cost") + "".replace("", "")),
        _m(
            "Near",
            "CALCULATE([Sales], DATESYTD('Date'[Date]), 'Date'[Year] = 2024, "
            "KEEPFILTERS('Product'[Color] = \"Red\"))",
        ),
    ]
    groups = _groups(objs, min_similarity=0.7)
    near = [g for g in groups if g["match"] == "near"]
    # the two exact structural members group; "Near" differs by an extra argument
    struct = [g for g in groups if g["match"] == "structural"]
    assert len(struct) == 1 and struct[0]["size"] == 2
    assert near == []  # a lone object is never a near group
    only_near = _groups([objs[0], objs[2]], min_similarity=0.7, include_fragments=False)
    (g,) = only_near
    assert g["match"] == "near" and 0.7 <= g["similarity"] < 1.0
    assert g["proposal"]["type"] is None and g["extractable_by_transform"] is False
    assert g["differences"]


def test_fragment_clones_are_found_across_different_measures_and_flag_row_context():
    frag = "SUMX(Sales, Sales[{c}] * Sales[Qty] * (1 - Sales[Discount]) + Sales[Tax] * 2)"
    objs = [
        _m("Net A", f"DIVIDE({frag.format(c='Amount')}, [Cost], 0)"),
        _m("Net B", f"IF(ISBLANK([Sales]), BLANK(), {frag.format(c='Cost')}) + 1"),
    ]
    groups = find_clones(objs, measure_names=MEASURES, min_tokens=8)["groups"]
    frags = [g for g in groups if g["scope"] == "fragment"]
    assert frags and frags[0]["extractable_by_transform"] is False
    assert frags[0]["parameters"][0]["kind"] == "col"
    assert any("Fragment clone" in n for n in frags[0]["proposal"]["notes"])


def test_calc_items_and_columns_can_be_scanned_but_are_not_extractable():
    objs = [
        _m("YTD", YTD.format(b="Sales"), kind="calc_item"),
        _m("YTD2", YTD.format(b="Cost"), kind="calc_item"),
    ]
    (g,) = _groups(objs, include_fragments=False)
    assert g["extractable_by_transform"] is False
    assert any("non-measure" in n for n in g["proposal"]["notes"])


def test_short_bodies_are_ignored_and_results_are_capped():
    res = find_clones([_m("a", "[Sales]"), _m("b", "[Cost]")], measure_names=MEASURES)
    assert res["groups"] == [] and res["objects_scanned"] == 0
    many = [_m(f"{b} YTD", YTD.format(b=b)) for b in MEASURES]
    capped = find_clones(many, measure_names=MEASURES, min_tokens=8, max_groups=0)
    assert capped["groups"] == [] and capped["omitted_groups"] == 1


def test_parameter_names_never_collide_with_measures_or_vars():
    objs = [
        _m(f"{b} X", f"VAR measureRef = 1 RETURN CALCULATE([{b}], 'Date'[Year] = measureRef + 2)")
        for b in MEASURES
    ]
    (g,) = _groups(objs, include_fragments=False)
    assert g["parameters"][0]["name"] != "measureRef"


def test_suggested_function_name():
    ms = [
        type("M", (), {"name": "Sales YTD"})(),
        type("M", (), {"name": "Cost YTD"})(),
    ]
    assert suggest_function_name(ms) == "YTD"


# --- analyse_measures (extract_udf input) ---------------------------------------------------------


def test_analyse_measures_refuses_different_structures():
    res = analyse_measures(
        [_m("A", YTD.format(b="Sales")), _m("B", "CALCULATE([Cost], DATESMTD('Date'[Date]))")],
        measure_names=MEASURES,
    )
    assert res["ok"] is False and res["differences"]


def test_analyse_measures_honours_parameter_names():
    objs = [_m(f"{b} YTD", YTD.format(b=b)) for b in ("Sales", "Cost")]
    res = analyse_measures(objs, measure_names=MEASURES, parameter_names=["baseMeasure"])
    assert res["ok"] and res["analysis"]["params"][0].name == "baseMeasure"


# --- calc-group candidates by structure --------------------------------------------------------------


def test_detect_structural_finds_a_calc_item_for_a_family_of_ytd_measures():
    objs = [_m(f"{b} YTD", YTD.format(b=b)) for b in ("Sales", "Cost", "Margin")]
    res = detect_structural(objs, MEASURES, min_tokens=8)
    (cand,) = res["candidates"]
    assert cand["pattern_name"] == "structural"
    assert cand["base_measures"] == ["Cost", "Margin", "Sales"]
    assert cand["estimated_replacement_count"] == 3
    assert cand["item_expression"].startswith("CALCULATE ( SELECTEDMEASURE ()")


# --- handler: find_clones ---------------------------------------------------------------------------


def _connect(monkeypatch, rows, tmsl=None, cl=None, fns=None):
    cs = MagicMock()
    cs.is_connected.return_value = True
    qe = MagicMock()
    qe.enumerate_measures_tom.return_value = {"success": True, "rows": rows, "row_count": len(rows)}
    if tmsl is not None:
        qe.get_tmsl_definition.return_value = {"success": True, "tmsl": tmsl}
    if cl is not None:
        qe.validate_and_execute_dax.return_value = {
            "success": True,
            "rows": [{"[CompatibilityLevel]": cl}],
        }
    if fns is not None:
        qe.list_udfs.return_value = {"success": True, "rows": [{"[Name]": n} for n in fns]}
    cs.query_executor = qe
    monkeypatch.setattr(dh, "connection_state", cs)
    return qe


def _rows():
    return [
        {"Name": f"{b} YTD", "Table": "M", "Expression": YTD.format(b=b)}
        for b in ("Sales", "Cost", "Margin")
    ] + [
        {"Name": b, "Table": "F", "Expression": f"SUM(F[{b}])"} for b in ("Sales", "Cost", "Margin")
    ]


def test_find_clones_handler_reports_groups_and_the_udf_cl_gate(monkeypatch):
    _connect(monkeypatch, _rows(), cl=1601, fns=[])
    res = dh.handle_dax_intelligence({"operation": "find_clones", "min_tokens": 8})
    assert res["success"] is True
    (g,) = res["groups"]
    assert g["size"] == 3 and g["proposal"]["type"] == "calc_group_item"
    assert res["udf"]["compatibility_level"] == 1601 and res["udf"]["supported"] is False
    assert "upgrade" in res["udf"]["note"]


def test_find_clones_handler_includes_calc_items_from_tmsl(monkeypatch):
    tmsl = {
        "model": {
            "tables": [
                {
                    "name": "TI",
                    "calculationGroup": {
                        "calculationItems": [
                            {"name": "A", "expression": YTD.format(b="Sales")},
                            {"name": "B", "expression": YTD.format(b="Cost")},
                        ]
                    },
                }
            ]
        }
    }
    _connect(monkeypatch, [], tmsl=tmsl, cl=1702, fns=["f"])
    res = dh.handle_dax_intelligence({"operation": "find_clones", "min_tokens": 8})
    assert res["success"] is True and res["other_objects_scanned"] == 2
    (g,) = [x for x in res["groups"] if x["scope"] == "measure"]
    assert {m["kind"] for m in g["members"]} == {"calc_item"}
    assert res["udf"]["supported"] is True and res["udf"]["existing_functions"] == 1


def test_find_clones_handler_bad_numeric_arg_is_an_error(monkeypatch):
    _connect(monkeypatch, _rows())
    res = dh.handle_dax_intelligence({"operation": "find_clones", "min_tokens": "abc"})
    assert res["success"] is False and res["error_type"] == "invalid_argument"


def test_find_clones_handler_not_connected(monkeypatch):
    cs = MagicMock()
    cs.is_connected.return_value = False
    monkeypatch.setattr(dh, "connection_state", cs)
    assert dh.handle_dax_intelligence({"operation": "find_clones"})["success"] is False


@pytest.mark.parametrize("op", ["find_clones", "explain"])
def test_new_ops_are_dispatched_by_the_handler(op):
    # the dispatcher keys on the literal; an unknown op would fall through to the analyze path
    import inspect

    assert f'operation == "{op}"' in inspect.getsource(dh)


def test_lexer_is_unchanged_by_the_detector():
    # normalise() must not mutate shared lexer state
    before = DaxLexer().tokenize("SUM(x[a])")
    normalise("SUM(x[a])")
    assert DaxLexer().tokenize("SUM(x[a])") == before
