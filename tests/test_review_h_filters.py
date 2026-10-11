"""Deep review 2026-09-22, slice H — filters and slicer selections.

H-09  visual filters live in the TOP-LEVEL ``filterConfig.filters`` (never
      ``visual.filters``): list / add / clear find and write them there.
H-10  inverted-selection polarity (hard rule): list surfaces it, set refuses
      to edit an exclude list without polarity / confirm_widen, never writes
      ``Values: []``; slicer default selection honours the mode.
H-11  ComparisonKind 0..4 (Equal = 0), NotEqual = Not(Comparison 0),
      apostrophes doubled, Int64 columns get ``L`` literals, TopN real shape,
      RelativeDate refused.
A-07  ``top_n`` / ``top_direction`` are honoured.
H-17  configure_slicer needs page + selector, previews multi-matches and
      dumps the prior selection.
H-22  measure_usage counts measures used only as visual-level filters.
H-27  slicer literal type comes from the model bound through definition.pbir.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / "fixtures" / "pbir_golden" / "GoldenCo"
OVERVIEW = "aa01overview00000001"
DETAILS = "ab02details000000002"


@pytest.fixture
def report(tmp_path) -> Path:
    root = tmp_path / "GoldenCo"
    shutil.copytree(GOLDEN, root)
    return root / "RPT-Sales.Report"


def _vj(report: Path, page: str, vid: str) -> Path:
    return report / "definition" / "pages" / page / "visuals" / vid / "visual.json"


def _read(p: Path):
    return json.loads(p.read_text(encoding="utf-8-sig"))


def _page_op(report: Path, **args):
    from server.handlers.page_operations_handler import handle_page_operations

    return handle_page_operations({"pbip_path": str(report), "confirm_destructive": True, **args})


def _visual_op(report: Path, **args):
    from server.handlers.visual_operations_handler import handle_visual_operations

    return handle_visual_operations({"pbip_path": str(report), **args})


def _where(filt: dict) -> dict:
    return filt["filter"]["Where"][0]["Condition"]


# ---------------------------------------------------------------------------
# H-09
# ---------------------------------------------------------------------------


def test_h09_list_reads_top_level_filter_config(report):
    res = _page_op(
        report,
        operation="list_filters",
        level="visual",
        page_name="Overview",
        visual_name="vm01matrixfilters001",
    )
    names = [f["name"] for f in res["visual_filters"]]
    assert names == [
        "vf03slotgate00000001",
        "vf04amountrange00001",
        "vf05region0000000001",
        "vf06notblank00000001",
        "vf07fieldonly0000001",
    ]
    region = res["visual_filters"][2]
    assert region["values"] == ["EU", "O'Brien Isles"]


def test_h09_add_and_clear_visual_filter_use_filter_config(report):
    vj = _vj(report, DETAILS, "vt01tableinverted001")
    res = _page_op(
        report,
        operation="add_filter",
        level="visual",
        page_name="Details",
        visual_name="vt01tableinverted001",
        table="Sales",
        field="Amount",
        filter_type="Advanced",
        operator="GreaterThan",
        values=[100],
    )
    assert res["success"], res
    data = _read(vj)
    assert "filters" not in data["visual"]
    added = [f for f in data["filterConfig"]["filters"] if f["name"] == res["filter_id"]]
    assert len(added) == 1
    res = _page_op(
        report,
        operation="clear_filters",
        level="visual",
        page_name="Details",
        visual_name="vt01tableinverted001",
        filter_names=[res["filter_id"]],
    )
    assert res["cleared_count"] == 1
    data = _read(vj)
    assert [f["name"] for f in data["filterConfig"]["filters"]] == ["vf08ownerinv00000001"]
    assert "filters" not in data["visual"]


# ---------------------------------------------------------------------------
# H-10
# ---------------------------------------------------------------------------


def test_h10_list_surfaces_polarity(report):
    res = _page_op(
        report,
        operation="list_filters",
        level="visual",
        page_name="Overview",
        visual_name="vs01slicerinverted01",
    )
    f = res["visual_filters"][0]
    assert f["polarity"] == "exclude" and f["is_inverted"] is True
    assert f["values"] == ["Legacy", "Internal"]
    res = _page_op(
        report,
        operation="list_filters",
        level="visual",
        page_name="Details",
        visual_name="vt01tableinverted001",
    )
    f = res["visual_filters"][0]
    assert f["polarity"] == "exclude" and f["has_condition"] is False
    assert "excludes nothing" in f["note"]


def test_h10_empty_values_never_written(report):
    page = report / "definition" / "pages" / OVERVIEW / "page.json"
    before = page.read_bytes()
    res = _page_op(report, operation="set_filter", filter_name="pf01category00000001", values=[])
    assert res["success"] is False and res["error_type"] == "empty_in_list"
    assert page.read_bytes() == before


def test_h10_exclude_list_needs_polarity(report):
    vj = _vj(report, DETAILS, "vt01tableinverted001")
    before = vj.read_bytes()
    res = _page_op(
        report, operation="set_filter", filter_name="vf08ownerinv00000001", values=["Ann"]
    )
    assert res["success"] is False
    assert res["error_type"] == "polarity_confirmation_required"
    assert vj.read_bytes() == before
    res = _page_op(
        report,
        operation="set_filter",
        filter_name="vf08ownerinv00000001",
        values=["Ann"],
        polarity="exclude",
    )
    assert res["success"] and res["polarity_applied"] == "exclude"
    f = _read(vj)["filterConfig"]["filters"][0]
    cond = _where(f)
    assert list(cond) == ["Not"]
    assert cond["Not"]["Expression"]["In"]["Values"] == [[{"Literal": {"Value": "'Ann'"}}]]
    assert (
        f["objects"]["general"][0]["properties"]["isInvertedSelectionMode"]["expr"]["Literal"][
            "Value"
        ]
        == "true"
    )


def test_h10_include_switch_drops_inverted_flag(report):
    vj = _vj(report, DETAILS, "vt01tableinverted001")
    res = _page_op(
        report,
        operation="set_filter",
        filter_name="vf08ownerinv00000001",
        values=["Ann"],
        polarity="include",
    )
    assert res["success"], res
    f = _read(vj)["filterConfig"]["filters"][0]
    assert "In" in _where(f)
    assert "objects" not in f
    from core.pbip import pbir_model

    assert pbir_model.filter_polarity(f, _read(vj)) == "include"


def test_h10_slicer_filter_is_routed_to_the_slicer_op(report):
    """The slicer keeps its selection in general.filter + filterConfig: set_filter
    must not desynchronise them."""
    vj = _vj(report, OVERVIEW, "vs01slicerinverted01")
    before = vj.read_bytes()
    res = _page_op(
        report,
        operation="set_filter",
        filter_name="vf01invcategory00001",
        values=["Legacy"],
        polarity="exclude",
    )
    assert res["error_type"] == "use_set_default_selection"
    assert vj.read_bytes() == before


def test_h10_set_default_selection_on_inverted_slicer(report):
    vj = _vj(report, OVERVIEW, "vs01slicerinverted01")
    res = _visual_op(
        report,
        operation="set_default_selection",
        page_name="Overview",
        visual_name="vs01slicerinverted01",
        values=["Legacy"],
    )
    assert res["success"] is False and res["error_type"] == "polarity_required"
    res = _visual_op(
        report,
        operation="set_default_selection",
        page_name="Overview",
        visual_name="vs01slicerinverted01",
        values=["Legacy"],
        polarity="exclude",
    )
    assert res["success"], res
    data = _read(vj)
    sel = data["visual"]["objects"]["general"][0]["properties"]["filter"]["filter"]
    assert "Not" in sel["Where"][0]["Condition"]
    fc = data["filterConfig"]["filters"][0]
    assert fc["filter"] == sel  # both places agree
    assert (
        fc["objects"]["general"][0]["properties"]["isInvertedSelectionMode"]["expr"]["Literal"][
            "Value"
        ]
        == "true"
    )


def test_h10_set_default_selection_empty_refused(report):
    res = _visual_op(
        report,
        operation="set_default_selection",
        page_name="Overview",
        visual_name="vs01slicerinverted01",
        values=[],
    )
    assert res["error_type"] == "empty_in_list"


# ---------------------------------------------------------------------------
# H-11 / A-07
# ---------------------------------------------------------------------------


def _add_page_filter(report, **kw):
    res = _page_op(report, operation="add_filter", level="page", page_name="Details", **kw)
    assert res["success"], res
    page = _read(report / "definition" / "pages" / DETAILS / "page.json")
    return next(f for f in page["filterConfig"]["filters"] if f["name"] == res["filter_id"])


@pytest.mark.parametrize(
    "operator,kind",
    [
        ("Equal", 0),
        ("GreaterThan", 1),
        ("GreaterThanOrEqual", 2),
        ("LessThan", 3),
        ("LessThanOrEqual", 4),
    ],
)
def test_h11_comparison_kinds(report, operator, kind):
    f = _add_page_filter(
        report,
        table="Sales",
        field="Amount",
        filter_type="Advanced",
        operator=operator,
        values=[100],
    )
    cmp_ = _where(f)["Comparison"]
    assert cmp_["ComparisonKind"] == kind
    assert cmp_["Right"]["Literal"]["Value"] == "100D"  # Sales[Amount] is double


def test_h11_not_equal_is_not_comparison_equal(report):
    f = _add_page_filter(
        report,
        table="Sales",
        field="Amount",
        filter_type="Advanced",
        operator="NotEqual",
        values=[5],
    )
    inner = _where(f)["Not"]["Expression"]["Comparison"]
    assert inner["ComparisonKind"] == 0


def test_h11_literals_escape_and_follow_column_type(report):
    f = _add_page_filter(
        report, table="Owner's Book", field="Owner", filter_type="Categorical", values=["O'Neil"]
    )
    assert _where(f)["In"]["Values"] == [[{"Literal": {"Value": "'O''Neil'"}}]]
    f = _add_page_filter(
        report, table="Owner's Book", field="Owner Key", filter_type="Categorical", values=[2024]
    )
    assert _where(f)["In"]["Values"] == [[{"Literal": {"Value": "2024L"}}]]


def test_h11_a07_topn_real_shape_and_args(report):
    f = _add_page_filter(
        report,
        table="Product Line",
        field="Category",
        filter_type="TopN",
        top_n=3,
        top_direction="Bottom",
        by_table="Sales",
        by_field="Total Amount",
    )
    assert f["type"] == "TopN"
    sub = f["filter"]["From"][0]
    assert sub["Type"] == 2
    query = sub["Expression"]["Subquery"]["Query"]
    assert query["Top"] == 3
    assert query["OrderBy"][0]["Direction"] == 1  # Bottom = ascending
    assert query["OrderBy"][0]["Expression"]["Measure"]["Property"] == "Total Amount"
    assert "Table" in _where(f)["In"]
    top = _add_page_filter(
        report,
        table="Product Line",
        field="Category",
        filter_type="TopN",
        top_n=5,
        by_table="Sales",
        by_field="Total Amount",
    )
    assert (
        top["filter"]["From"][0]["Expression"]["Subquery"]["Query"]["OrderBy"][0]["Direction"] == 2
    )


def test_h11_relative_date_refused(report):
    res = _page_op(
        report,
        operation="add_filter",
        level="page",
        page_name="Details",
        table="Sales",
        field="Order Date",
        filter_type="RelativeDate",
        values=[3],
    )
    assert res["success"] is False
    assert "RelativeDate" in res["error"]


# ---------------------------------------------------------------------------
# H-17
# ---------------------------------------------------------------------------


def test_h17_configure_slicer_requires_page_and_selector(report):
    res = _visual_op(report, operation="configure_slicer")
    assert res["error_type"] == "target_required"
    res = _visual_op(report, operation="configure_slicer", entity="Product Line")
    assert res["error_type"] == "target_required"


def test_h17_multi_match_previews_and_dumps_prior_state(report):
    vj1 = _vj(report, OVERVIEW, "vs01slicerinverted01")
    vj2 = _vj(report, OVERVIEW, "vs02slicervisinv0002")
    b1, b2 = vj1.read_bytes(), vj2.read_bytes()
    res = _visual_op(
        report, operation="configure_slicer", page_name="Overview", entity="Product Line"
    )
    assert res["success"] and res["dry_run"] is True
    assert res["matched"] == 2
    assert vj1.read_bytes() == b1 and vj2.read_bytes() == b2
    dumps = {c["visual_name"]: c["prior_state"] for c in res["changes"]}
    assert dumps["vs01slicerinverted01"]["general_filter"]["Where"]
    assert (
        dumps["vs01slicerinverted01"]["filterConfig_filters"][0]["name"] == "vf01invcategory00001"
    )


# ---------------------------------------------------------------------------
# H-22 / H-27
# ---------------------------------------------------------------------------


def test_h22_measure_usage_counts_visual_filter_measures(report):
    from server.handlers.report_operations_handler import handle_report_operations

    res = handle_report_operations(
        {"operation": "measure_usage", "pbip_path": str(report), "output_format": "json"}
    )
    assert res.get("success") is not False, res
    overview = next(p for p in res["pages"] if p["page"] == "Overview")["measures"]
    assert "m Metrics[Show In Slot]" in overview  # ONLY the matrix's visibility-gate filter
    assert "Sales[Amount Dynamic]" in overview  # field-only visual filter


def test_h27_slicer_value_type_from_bound_model(report):
    from server.handlers._internal.slicer_default_selection import _detect_via_tmdl

    defn = report / "definition"
    assert _detect_via_tmdl(defn, "Owner's Book", "Owner Key") == "int"
    assert _detect_via_tmdl(defn, "Product Line", "Category") == "string"
