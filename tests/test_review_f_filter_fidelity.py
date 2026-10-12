"""2026-09-22 review, slice F: visual -> DAX reconstruction fidelity.

Pins F-02, F-03, F-04, F-05, F-06 (builder side), F-12, F-15, F-18, F-19, F-27,
F-37 (name escaping) and F-39(3)/(4) against the synthetic golden PBIP
(``tests/fixtures/pbir_golden``) and small synthetic reports whose shapes mirror
the R0105 / R0201 corpus (fake names only). No live model: the builder runs
offline, and the few model lookups use fakes.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List

import pytest

from core.debug.filter_to_dax import (
    FilterExpression,
    FilterToDaxConverter,
    calculate_filter_args,
)
from core.debug.visual_query_builder import (
    VisualQueryBuilder,
    is_grand_total_row,
    row_value,
)

GOLDEN = Path(__file__).parent / "fixtures" / "pbir_golden" / "GoldenCo"
SALES = GOLDEN / "RPT-Sales.Report"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _col(entity: str, prop: str, source: str = None) -> Dict[str, Any]:
    ref = {"Source": source} if source else {"Entity": entity}
    return {"Column": {"Expression": {"SourceRef": ref}, "Property": prop}}


def _measure(entity: str, prop: str, source: str = None) -> Dict[str, Any]:
    ref = {"Source": source} if source else {"Entity": entity}
    return {"Measure": {"Expression": {"SourceRef": ref}, "Property": prop}}


def _lit(value: str) -> Dict[str, Any]:
    return {"Literal": {"Value": value}}


def _cat_filter(name, entity, prop, values, *, inverted=False, not_wrap=False):
    cond = {"In": {"Expressions": [_col(entity, prop, "t")], "Values": [[_lit(v)] for v in values]}}
    if not_wrap:
        cond = {"Not": {"Expression": cond}}
    f = {
        "name": name,
        "field": _col(entity, prop),
        "type": "Categorical",
        "filter": {
            "Version": 2,
            "From": [{"Name": "t", "Entity": entity, "Type": 0}],
            "Where": [{"Condition": cond}],
        },
    }
    if inverted:
        f["objects"] = {
            "general": [{"properties": {"isInvertedSelectionMode": {"expr": _lit("true")}}}]
        }
    return f


def _write(root: Path, rel: str, obj: Any) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def _report(tmp_path: Path, visuals: Dict[str, Dict], page_filters=None, interactions=None):
    root = tmp_path / "Synth.Report"
    d = "definition"
    _write(root, f"{d}/report.json", {"filterConfig": {"filters": []}})
    _write(root, f"{d}/pages/pages.json", {"pageOrder": ["p1"], "activePageName": "p1"})
    page = {"name": "p1", "displayName": "P1", "filterConfig": {"filters": page_filters or []}}
    if interactions:
        page["visualInteractions"] = interactions
    _write(root, f"{d}/pages/p1/page.json", page)
    for vid, body in visuals.items():
        _write(root, f"{d}/pages/p1/visuals/{vid}/visual.json", {"name": vid, **body})
    return VisualQueryBuilder(str(root))


def _table_visual(columns, measures, filters=None, vtype="tableEx", role="Values"):
    projections = [{"field": _col(t, c)} for t, c in columns] + [
        {"field": _measure(t, m)} for t, m in measures
    ]
    body = {
        "visual": {
            "visualType": vtype,
            "query": {"queryState": {role: {"projections": projections}}},
        }
    }
    if filters:
        body["filterConfig"] = {"filters": filters}
    return body


def _slicer(entity, prop, selection_cond, *, fc_filters=None, data_inverted=False):
    objects = {
        "general": [
            {
                "properties": {
                    "filter": {
                        "filter": {
                            "Version": 2,
                            "From": [{"Name": "s", "Entity": entity, "Type": 0}],
                            "Where": [{"Condition": selection_cond}],
                        }
                    }
                }
            }
        ]
    }
    if data_inverted:
        objects["data"] = [{"properties": {"isInvertedSelectionMode": {"expr": _lit("true")}}}]
    body = {
        "visual": {
            "visualType": "advancedSlicerVisual",
            "query": {"queryState": {"Values": {"projections": [{"field": _col(entity, prop)}]}}},
            "objects": objects,
        }
    }
    if fc_filters is not None:
        body["filterConfig"] = {"filters": fc_filters}
    return body


def _in(entity, prop, values):
    return {"In": {"Expressions": [_col(entity, prop, "s")], "Values": [[_lit(v)] for v in values]}}


# ---------------------------------------------------------------------------
# F-03: visual-level filters live in the TOP-LEVEL filterConfig
# ---------------------------------------------------------------------------


def test_f03_visual_filters_read_from_top_level_filter_config():
    b = VisualQueryBuilder(str(SALES))
    r = b.build_visual_query("Overview", visual_id="vm01matrixfilters001")
    names = {f.original.get("name") for f in r.filter_context.visual_filters}
    assert {"vf04amountrange00001", "vf05region0000000001", "vf06notblank00000001"} <= names
    assert "'Sales'[Amount] >= 100" in r.dax_query
    assert "'Product Line'[Region]" in r.dax_query


# ---------------------------------------------------------------------------
# F-02 / F-04: every condition type keeps its meaning in the MAIN query
# ---------------------------------------------------------------------------


def test_f02_not_in_filter_is_not_emitted_as_treatas_include(tmp_path):
    notin = _cat_filter("vf", "d Channel", "Channel", ["'Web'"], not_wrap=True)
    b = _report(
        tmp_path, {"v1": _table_visual([("d Customer", "Name")], [("m", "Sales")], [notin])}
    )
    q = b.build_visual_query("P1", visual_id="v1").dax_query
    assert 'TREATAS({"Web"}' not in q
    assert (
        "FILTER(KEEPFILTERS(VALUES('d Channel'[Channel])), NOT('d Channel'[Channel] IN {\"Web\"}))"
        in q
    )


@pytest.mark.parametrize(
    "kind,op",
    [(0, "="), (1, ">"), (2, ">="), (3, "<"), (4, "<="), ("GreaterThan", ">")],
)
def test_f04d_int_and_named_comparison_kinds(kind, op):
    conv = FilterToDaxConverter()
    f = {
        "field": _col("d Product", "Price"),
        "type": "Advanced",
        "filter": {
            "From": [{"Name": "t", "Entity": "d Product"}],
            "Where": [
                {
                    "Condition": {
                        "Comparison": {
                            "ComparisonKind": kind,
                            "Left": _col("d Product", "Price", "t"),
                            "Right": _lit("100L"),
                        }
                    }
                }
            ],
        },
    }
    e = conv.convert_filter(f, "visual")
    assert e.dax == f"'d Product'[Price] {op} 100"
    assert e.filter_table_dax.startswith("FILTER(KEEPFILTERS(VALUES('d Product'[Price]))")


def test_f04_range_and_not_blank_from_golden_matrix():
    conv = FilterToDaxConverter()
    data = json.loads(
        (
            SALES / "definition/pages/aa01overview00000001/visuals/vm01matrixfilters001/visual.json"
        ).read_text(encoding="utf-8")
    )
    by_name = {f["name"]: f for f in data["filterConfig"]["filters"]}
    rng = conv.convert_filter(by_name["vf04amountrange00001"], "visual", data)
    assert rng.dax == "('Sales'[Amount] >= 100 && 'Sales'[Amount] <= 5000)"
    blank = conv.convert_filter(by_name["vf06notblank00000001"], "visual", data)
    assert blank.dax == "NOT(ISBLANK('Owner''s Book'[Owner]))"
    assert blank.condition_type == "IsNotBlank"


def test_f04c_topn_and_relative_date_are_not_dropped():
    b = VisualQueryBuilder(str(SALES))
    donut = b.build_visual_query("Details", visual_id="vd01donuttopn0000001")
    assert donut.filter_context.visual_filters[0].condition_type == "TopN"
    assert "TOPN(5, SUMMARIZECOLUMNS('Product Line'[Category]" in donut.dax_query
    assert "[__TopNValue], DESC" in donut.dax_query
    line = b.build_visual_query("Details", visual_id="vl01linereldate00001")
    rel = line.filter_context.visual_filters[0]
    assert rel.condition_type == "RelativeDate"
    assert "NOW()" in rel.dax and "EDATE(" in rel.dax
    assert "'Sales'[Order Date] <" in line.dax_query


def test_f39_3_unsupported_filter_is_reported_not_silently_dropped(tmp_path):
    weird = {
        "name": "vfweird",
        "field": _col("d Product", "Color"),
        "type": "Advanced",
        "filter": {
            "From": [{"Name": "t", "Entity": "d Product"}],
            "Where": [{"Condition": {"Exists": {"Table": {"SourceRef": {"Source": "t"}}}}}],
        },
    }
    b = _report(
        tmp_path, {"v1": _table_visual([("d Product", "Color")], [("m", "Sales")], [weird])}
    )
    r = b.build_visual_query("P1", visual_id="v1")
    assert r.fidelity == "partial"
    assert r.unsupported_filters and r.unsupported_filters[0]["name"] == "vfweird"


# ---------------------------------------------------------------------------
# F-04a: inverted selection -- visual filters and slicer selections
# ---------------------------------------------------------------------------


def test_f04a_filter_level_inverted_flag_turns_in_values_into_exclude_list():
    conv = FilterToDaxConverter()
    f = _cat_filter("vf", "d Region", "Region", ["'EU'"], inverted=True)
    e = conv.convert_filter(f, "visual")
    assert e.polarity == "exclude"
    assert e.dax == "NOT('d Region'[Region] IN {\"EU\"})"


def test_f04a_inverted_filter_without_condition_filters_nothing():
    conv = FilterToDaxConverter()
    data = json.loads(
        (
            SALES / "definition/pages/ab02details000000002/visuals/vt01tableinverted001/visual.json"
        ).read_text(encoding="utf-8")
    )
    expr, issue = conv.convert_filter_detailed(data["filterConfig"]["filters"][0], "visual", data)
    assert expr is None and issue["inactive"] is True


def test_f04a_r0201_field_parameter_slicer_is_an_exclude_list(tmp_path):
    """R0201 shape: selection In {one field} + a filterConfig entry on the slicer's
    table with isInvertedSelectionMode and NO own condition -> EXCLUDE that field."""
    fc_entry = {
        "name": "fcslot",
        "field": _col("sf Slot (Filters)", "sf Slot"),
        "type": "Categorical",
        "objects": {
            "general": [{"properties": {"isInvertedSelectionMode": {"expr": _lit("true")}}}]
        },
    }
    slicer = _slicer(
        "sf Slot (Filters)",
        "sf Slot Fields",
        _in("sf Slot (Filters)", "sf Slot Fields", ["'''d_Unit''[Unit Name]'"]),
        fc_filters=[fc_entry],
    )
    b = _report(
        tmp_path,
        {"s1": slicer, "v1": _table_visual([("d Unit", "Name")], [("m", "Sales")])},
    )
    r = b.build_visual_query("P1", visual_id="v1")
    sf = [f for f in r.filter_context.slicer_filters if f.table == "sf Slot (Filters)"][0]
    assert sf.polarity == "exclude"
    assert sf.dax.startswith("NOT(")


def test_f04a_slicer_filter_config_with_own_condition_does_not_invert_selection(tmp_path):
    """R0201 time-calc shape: the filterConfig entry hides options (own Not(In)) --
    the SELECTION In {'Last 2 Years'} stays an include list."""
    fc_entry = _cat_filter(
        "fchide", "s TimeSel", "Selection", ["'MTD'", "'QTD'"], inverted=True, not_wrap=True
    )
    slicer = _slicer(
        "s TimeSel",
        "Selection",
        _in("s TimeSel", "Selection", ["'Last 2 Years'"]),
        fc_filters=[fc_entry],
    )
    b = _report(
        tmp_path, {"s1": slicer, "v1": _table_visual([("d Unit", "Name")], [("m", "Sales")])}
    )
    r = b.build_visual_query("P1", visual_id="v1")
    sf = r.filter_context.slicer_filters[0]
    assert sf.polarity == "include"
    assert 'TREATAS({"Last 2 Years"}' in r.dax_query
    assert '"MTD"' not in r.dax_query  # the slicer's own visual filter never leaks


def test_f04a_golden_not_in_selection_and_visual_level_flag():
    b = VisualQueryBuilder(str(SALES))
    # vs02 = data-level inverted flag + Not(In APAC): exclude, no double negation.
    card = b.build_visual_query("Overview", visual_id="vc01cardvisual000001")
    region = [f for f in card.filter_context.slicer_filters if f.column == "Region"][0]
    assert region.polarity == "exclude"
    assert region.dax == "NOT('Product Line'[Region] IN {\"APAC\"})"
    assert "NOT(NOT(" not in card.dax_query


# ---------------------------------------------------------------------------
# F-05: same-column filters from different levels INTERSECT
# ---------------------------------------------------------------------------


def test_f05_page_filter_and_slicer_on_same_column_intersect(tmp_path):
    page = [_cat_filter("pf", "d Date", "Year", ["2024L", "2025L"])]
    slicer = _slicer("d Date", "Year", _in("d Date", "Year", ["2025L"]))
    b = _report(
        tmp_path,
        {"s1": slicer, "v1": _table_visual([("d Unit", "Name")], [("m", "Sales")])},
        page_filters=page,
    )
    q = b.build_visual_query("P1", visual_id="v1").dax_query
    assert "TREATAS({2025}, 'd Date'[Year])" in q
    assert "{2024, 2025}" not in q


def test_f05_disjoint_same_column_filters_stay_separate_arguments():
    a = FilterExpression(
        dax="x",
        source="page",
        table="T",
        column="C",
        condition_type="In",
        values=[1],
        literals=["1"],
    )
    c = FilterExpression(
        dax="y",
        source="slicer",
        table="T",
        column="C",
        condition_type="In",
        values=[2],
        literals=["2"],
    )
    merged = VisualQueryBuilder._merge_same_column_filters([a, c])
    assert merged == [a, c]  # engine intersects -> empty, exactly like the visual


# ---------------------------------------------------------------------------
# F-06 (builder): manual predicates are applied verbatim
# ---------------------------------------------------------------------------


def test_f06_manual_filter_is_keepfilters_not_empty_treatas():
    b = VisualQueryBuilder(str(SALES))
    r = b.build_visual_query("Overview", visual_id="vm01matrixfilters001")
    q = b.rebuild_query(r, [b.converter.manual_filter("'Sales'[Year] = 2023")])
    assert "TREATAS({BLANK()}, ''[])" not in q
    assert "KEEPFILTERS('Sales'[Year] = 2023)" in q
    # keeps the visual's rollup and its per-type row limit
    assert "ROLLUPADDISSUBTOTAL(" in q
    assert "TOPN(501," in q


# ---------------------------------------------------------------------------
# measure (value) filters
# ---------------------------------------------------------------------------


def test_measure_visibility_gate_filters_the_grouped_rows():
    b = VisualQueryBuilder(str(SALES))
    q = b.build_visual_query("Overview", visual_id="vm01matrixfilters001").dax_query
    assert "VAR __ValueFilterDM0 =" in q
    assert "\"__VF0\", 'm Metrics'[Show In Slot])), [__VF0] = 1)" in q
    assert "__ValueFilterDM0,\n" in q  # passed to the core SUMMARIZECOLUMNS


def test_f27_calculate_args_exclude_measure_filters_and_placeholders():
    b = VisualQueryBuilder(str(SALES))
    r = b.build_visual_query("Overview", visual_id="vm01matrixfilters001")
    args, skipped = calculate_filter_args(r.filter_context.all_filters())
    assert all("--" not in a for a in args)
    assert all("Show In Slot" not in a for a in args)
    assert any(s["type"] == "Measure" for s in skipped)


def test_f27_composite_slicer_dax_is_valid_treatas_not_a_comment():
    conv = FilterToDaxConverter()
    e = conv.convert_slicer_selection(
        {
            "entity": "sf Row Drill",
            "property": "sf Row Drill Fields",
            "selected_values": ["'a'"],
            "extra_columns": ["'sf Row Drill'[sf Row Drill Order]"],
            "composite_values": [["'a'", "1L"]],
        }
    )
    assert not e.dax.startswith("--")
    assert e.dax.startswith('TREATAS({("a", 1)}')


# ---------------------------------------------------------------------------
# F-12: every query role is scanned
# ---------------------------------------------------------------------------


def test_f12_card_visual_data_role_measures_are_found():
    b = VisualQueryBuilder(str(SALES))
    r = b.build_visual_query("Overview", visual_id="vc01cardvisual000001")
    assert r is not None
    assert r.visual_info.measures == ["[Total Amount]"]
    assert 'ROW(\n    "Total_Amount"' in r.dax_query
    assert b._is_data_visual("cardVisual", {"visual": {"visualType": "cardVisual"}})


def test_f12_combo_y2_and_deneb_dataset_roles(tmp_path):
    combo = {
        "visual": {
            "visualType": "lineClusteredColumnComboChart",
            "query": {
                "queryState": {
                    "Category": {"projections": [{"field": _col("d Date", "Month")}]},
                    "Y": {"projections": [{"field": _measure("m", "Bars")}]},
                    "Y2": {"projections": [{"field": _measure("m", "Line")}]},
                }
            },
        }
    }
    deneb = {
        "visual": {
            "visualType": "deneb7E15AEF80B9E4D4F8E12924291ECE89A",
            "query": {
                "queryState": {
                    "dataset": {
                        "projections": [
                            {"field": _col("d Date", "Month")},
                            {"field": _measure("m", "Spark")},
                        ]
                    }
                }
            },
        }
    }
    b = _report(tmp_path, {"c1": combo, "d1": deneb})
    assert b.build_visual_query("P1", visual_id="c1").visual_info.measures == ["[Bars]", "[Line]"]
    assert b.build_visual_query("P1", visual_id="d1").visual_info.measures == ["[Spark]"]


def test_implicit_aggregation_measure_is_queried(tmp_path):
    agg = {"Aggregation": {"Expression": _col("Sales", "Amount"), "Function": 0}}
    body = {
        "visual": {
            "visualType": "barChart",
            "query": {
                "queryState": {
                    "Category": {"projections": [{"field": _col("d Unit", "Name")}]},
                    "Y": {"projections": [{"field": agg}]},
                }
            },
        }
    }
    b = _report(tmp_path, {"b1": body})
    q = b.build_visual_query("P1", visual_id="b1").dax_query
    assert "\"Sum_of_Amount\", SUM('Sales'[Amount])" in q


# ---------------------------------------------------------------------------
# F-19: grand totals, subtotal levels, sortDefinition
# ---------------------------------------------------------------------------


def test_f19_table_with_two_columns_gets_one_grand_total(tmp_path):
    b = _report(
        tmp_path,
        {
            "t1": _table_visual(
                [("d Customer", "Name"), ("d Product", "Category")], [("m", "Sales")]
            )
        },
    )
    q = b.build_visual_query("P1", visual_id="t1").dax_query
    assert (
        "ROLLUPADDISSUBTOTAL(ROLLUPGROUP('d Customer'[Name], 'd Product'[Category]), \"IsGrandTotalRowTotal\")"
        in q
    )


def test_f19_matrix_row_levels_get_their_own_subtotal_flags(tmp_path):
    body = _table_visual(
        [("d Region", "Region"), ("d Unit", "Name")],
        [("m", "Sales")],
        vtype="pivotTable",
        role="Rows",
    )
    b = _report(tmp_path, {"m1": body})
    q = b.build_visual_query("P1", visual_id="m1").dax_query
    assert (
        "ROLLUPADDISSUBTOTAL('d Region'[Region], \"IsGrandTotalRowTotal\", 'd Unit'[Name], \"IsDM1Total\")"
        in q
    )
    assert (
        "ORDER BY [IsGrandTotalRowTotal] DESC, 'd Region'[Region], [IsDM1Total] DESC, 'd Unit'[Name]"
        in q
    )


def test_f19_sort_definition_orders_the_window():
    b = VisualQueryBuilder(str(SALES))
    q = b.build_visual_query("Overview", visual_id="vm01matrixfilters001").dax_query
    assert "TOPN(501, __DS0Core, [IsGrandTotalRowTotal], 0, [Total_Amount], 0" in q
    assert "ORDER BY [IsGrandTotalRowTotal] DESC, [Total_Amount] DESC" in q


# ---------------------------------------------------------------------------
# F-15: the measure value is read from the measure column / a total query
# ---------------------------------------------------------------------------


def test_f15_total_query_evaluates_the_measure_over_what_the_visual_shows():
    b = VisualQueryBuilder(str(SALES))
    r = b.build_visual_query("Overview", visual_id="vm01matrixfilters001")
    q, alias = b.build_total_query(r)
    assert alias == "Value"
    assert "ROW(\"Value\", CALCULATE('Sales'[Total Amount]," in q
    assert "__ValueFilterDM0)" in q  # value filter kept, over the visual's grouping


def test_row_value_and_grand_total_helpers_accept_bracketed_keys():
    row = {"[Product Line[Category]]": None, "[Total_Amount]": 12.5, "[IsGrandTotalRowTotal]": True}
    assert row_value(row, "Total_Amount") == 12.5
    assert is_grand_total_row(row) is True
    assert is_grand_total_row({"IsGrandTotalRowTotal": False}) is False


# ---------------------------------------------------------------------------
# F-39(4): interactions set to NoFilter
# ---------------------------------------------------------------------------


def test_f39_4_slicer_with_nofilter_interaction_is_not_applied():
    b = VisualQueryBuilder(str(SALES))
    r = b.build_visual_query("Overview", visual_id="vm01matrixfilters001")
    assert all(
        f.column != "Region" or f.source != "slicer" for f in r.filter_context.slicer_filters
    )
    assert any("NoFilter" in i["reason"] for i in r.filter_context.inactive)


# ---------------------------------------------------------------------------
# F-18: sort-by columns (live DMV lane + offline TMDL lane via definition.pbir)
# ---------------------------------------------------------------------------


class _InfoQE:
    def __init__(self, rows):
        self.rows = rows
        self.calls: List[str] = []

    def execute_info_query(self, fn, **_kw):
        self.calls.append(fn)
        if fn == "COLUMNS":
            return {"success": True, "rows": self.rows}
        return {"success": False}


def test_f18_sort_by_from_info_columns_and_cached(tmp_path):
    rows = [
        {"ID": 1, "Table": "d Date", "ExplicitName": "Month", "SortByColumnID": 2},
        {"ID": 2, "Table": "d Date", "ExplicitName": "Month Nr", "SortByColumnID": None},
    ]
    b = _report(
        tmp_path,
        {
            "b1": _table_visual(
                [("d Date", "Month")], [("m", "Sales")], vtype="barChart", role="Category"
            )
        },
    )
    qe = _InfoQE(rows)
    b._query_executor = qe
    r = b.build_visual_query("P1", visual_id="b1", expand_measures=False)
    assert "'d Date'[Month Nr],\n        'd Date'[Month]" in r.dax_query
    b.build_visual_query("P1", visual_id="b1", expand_measures=False)
    assert qe.calls.count("COLUMNS") == 1


def test_f18_offline_tmdl_lane_finds_the_bound_model(tmp_path):
    root = tmp_path / "GoldenCo"
    shutil.copytree(GOLDEN, root)
    body = _table_visual(
        [("Time Calc", "Name")], [("Sales", "Total Amount")], vtype="barChart", role="Category"
    )
    vis = (
        root / "RPT-Sales.Report/definition/pages/aa01overview00000001/visuals/vz01timecalc00000001"
    )
    vis.mkdir(parents=True)
    (vis / "visual.json").write_text(
        json.dumps({"name": "vz01timecalc00000001", **body}), encoding="utf-8"
    )
    b = VisualQueryBuilder(str(root / "RPT-Sales.Report"))
    b._query_executor = None
    r = b.build_visual_query("Overview", visual_id="vz01timecalc00000001")
    assert "'Time Calc'[Ordinal],\n        'Time Calc'[Name]" in r.dax_query


# ---------------------------------------------------------------------------
# F-37: names are escaped
# ---------------------------------------------------------------------------


def test_f37_table_and_column_names_are_escaped(tmp_path):
    f = _cat_filter("vf", "O'Neil Book", "Col]x", ["'a'"])
    b = _report(
        tmp_path, {"v1": _table_visual([("O'Neil Book", "Name")], [("m", "Net]Value")], [f])}
    )
    q = b.build_visual_query("P1", visual_id="v1").dax_query
    assert "TREATAS({\"a\"}, 'O''Neil Book'[Col]]x])" in q
    assert "'O''Neil Book'[Name]" in q
    assert "[Net]]Value]" in q


def test_f37_equality_filter_uses_the_column_type():
    conv = FilterToDaxConverter()
    conv.set_column_type("d Date", "Year", "Int64")
    e = conv.equality_filter("d Date", "Year", "2024")
    assert e.dax == "'d Date'[Year] = 2024"
    s = conv.equality_filter("d Unit", "Name", 'say "hi"')
    assert s.dax == '\'d Unit\'[Name] = "say ""hi"""'
