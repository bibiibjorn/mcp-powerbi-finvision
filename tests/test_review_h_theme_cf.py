"""Deep review 2026-09-22, slice H — theme + conditional formatting writers.

H-02  clear_visual_overrides removes only plain formatting overrides: slicer
      selections, textbox text, Deneb specs, slicer mode, selector-scoped
      entries and (keep_cf) conditional formatting survive; scope=page: works.
H-20  add_cf writes the real CF shapes (FillRule.linearGradient2/3 with
      Literal colours, Conditional.Cases) at the requested selector;
      unverified rule types are refused.
H-21  theme visualStyles cards are ARRAYS of plain values: set_formatting
      accepts the array form, push_visual unwraps literals.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / "fixtures" / "pbir_golden" / "GoldenCo"
OVERVIEW = "aa01overview00000001"
DETAILS = "ab02details000000002"

CF_VALUE = {
    "solid": {
        "color": {
            "expr": {
                "FillRule": {
                    "Input": {
                        "Measure": {
                            "Expression": {"SourceRef": {"Entity": "Sales"}},
                            "Property": "Total Amount",
                        }
                    },
                    "FillRule": {
                        "linearGradient2": {
                            "min": {"color": {"Literal": {"Value": "'#FFFFFF'"}}},
                            "max": {"color": {"Literal": {"Value": "'#118DFF'"}}},
                        }
                    },
                }
            }
        }
    }
}


@pytest.fixture
def report(tmp_path) -> Path:
    root = tmp_path / "GoldenCo"
    shutil.copytree(GOLDEN, root)
    rep = root / "RPT-Sales.Report"
    # a custom theme (the golden report only has the base theme)
    theme_dir = rep / "StaticResources" / "RegisteredResources"
    theme_dir.mkdir(parents=True)
    (theme_dir / "Golden.json").write_text(
        json.dumps(
            {
                "name": "Golden",
                "visualStyles": {"cardVisual": {"*": {"background": [{"transparency": 0}]}}},
            }
        ),
        encoding="utf-8",
    )
    rj = rep / "definition" / "report.json"
    data = json.loads(rj.read_text(encoding="utf-8"))
    data["themeCollection"]["customTheme"] = {
        "name": "Golden.json",
        "reportVersionAtImport": {"visual": "1.8.83", "report": "2.0.83", "page": "1.3.83"},
        "type": "RegisteredResources",
    }
    rj.write_text(json.dumps(data, indent=2), encoding="utf-8")
    # a CF override + a selector-scoped entry on the matrix
    vj = _vj(rep, "vm01matrixfilters001")
    m = json.loads(vj.read_text(encoding="utf-8"))
    m["visual"]["objects"]["values"] = [
        {"properties": {"backColor": CF_VALUE, "fontSize": {"expr": {"Literal": {"Value": "9D"}}}}},
        {
            "properties": {
                "fontColor": {"solid": {"color": {"expr": {"Literal": {"Value": "'#FF0000'"}}}}}
            },
            "selector": {"metadata": "Sales.Total Amount"},
        },
    ]
    vj.write_text(json.dumps(m, indent=2), encoding="utf-8")
    return rep


def _vj(report: Path, vid: str, page: str = OVERVIEW) -> Path:
    return report / "definition" / "pages" / page / "visuals" / vid / "visual.json"


def _read(p: Path):
    return json.loads(p.read_text(encoding="utf-8-sig"))


def _theme_op(report: Path, **args):
    from server.handlers.theme_operations_handler import handle_theme_operations

    return handle_theme_operations({"pbip_path": str(report), "confirm_destructive": True, **args})


def _theme(report: Path) -> dict:
    return _read(report / "StaticResources" / "RegisteredResources" / "Golden.json")


# ---------------------------------------------------------------------------
# H-02
# ---------------------------------------------------------------------------


def test_h02_clear_overrides_keeps_content_cf_and_selectors(report):
    slicer_before = _read(_vj(report, "vs01slicerinverted01"))["visual"]["objects"]
    text_before = _read(_vj(report, "vg02groupchild000001"))["visual"]["objects"]
    res = _theme_op(report, operation="clear_visual_overrides", keep_cf=True)
    assert res["success"], res
    assert _read(_vj(report, "vs01slicerinverted01"))["visual"]["objects"] == slicer_before
    assert _read(_vj(report, "vg02groupchild000001"))["visual"]["objects"] == text_before
    m = _read(_vj(report, "vm01matrixfilters001"))["visual"]["objects"]
    assert "columnHeaders" not in m  # plain override removed
    kept = m["values"]
    assert kept[0]["properties"] == {"backColor": CF_VALUE}  # CF kept, plain fontSize removed
    assert kept[1]["selector"] == {"metadata": "Sales.Total Amount"}
    assert res["kept"]["conditional_formatting"] >= 1
    assert res["kept"]["selector_scoped"] >= 1


def test_h02_keep_cf_false_drops_cf(report):
    _theme_op(report, operation="clear_visual_overrides", keep_cf=False)
    m = _read(_vj(report, "vm01matrixfilters001"))["visual"]["objects"]
    assert all("backColor" not in e.get("properties", {}) for e in m.get("values", []))


def test_h02_scope_page_only(report):
    donut = _vj(report, "vd01donuttopn0000001", DETAILS)
    d = _read(donut)
    d["visual"]["objects"] = {
        "legend": [{"properties": {"show": {"expr": {"Literal": {"Value": "true"}}}}}]
    }
    donut.write_text(json.dumps(d, indent=2), encoding="utf-8")
    res = _theme_op(report, operation="clear_visual_overrides", scope=f"page:{DETAILS}")
    assert res["success"], res
    assert "legend" not in _read(donut)["visual"]["objects"]
    assert "columnHeaders" in _read(_vj(report, "vm01matrixfilters001"))["visual"]["objects"]


def test_h02_dry_run_touches_nothing(report):
    before = _vj(report, "vm01matrixfilters001").read_bytes()
    res = _theme_op(report, operation="clear_visual_overrides", dry_run=True)
    assert res["dry_run"] is True and res["files_modified"] == 0
    assert _vj(report, "vm01matrixfilters001").read_bytes() == before


# ---------------------------------------------------------------------------
# H-20
# ---------------------------------------------------------------------------


def test_h20_color_scale_real_shape_at_selector(report):
    res = _theme_op(
        report,
        operation="add_cf",
        page_name="Overview",
        visual_name="vm01matrixfilters001",
        container="values",
        property_name="fontColor",
        rule_type="color_scale",
        selector_metadata="m Metrics.Net Δ Value",
        cf_config={
            "table": "Sales",
            "field": "Total Amount",
            "min_color": "#000000",
            "max_color": "#00FF00",
        },
    )
    assert res["success"], res
    entries = _read(_vj(report, "vm01matrixfilters001"))["visual"]["objects"]["values"]
    entry = next(
        e for e in entries if (e.get("selector") or {}).get("metadata") == "m Metrics.Net Δ Value"
    )
    rule = entry["properties"]["fontColor"]["solid"]["color"]["expr"]["FillRule"]
    assert rule["Input"]["Measure"]["Property"] == "Total Amount"  # measure from the model
    grad = rule["FillRule"]["linearGradient2"]
    assert grad["min"]["color"] == {"Literal": {"Value": "'#000000'"}}
    assert grad["max"]["color"] == {"Literal": {"Value": "'#00FF00'"}}
    # the other selector's entry is untouched
    other = next(
        e for e in entries if (e.get("selector") or {}).get("metadata") == "Sales.Total Amount"
    )
    assert "fontColor" in other["properties"] and "FillRule" not in json.dumps(other)


def test_h20_rules_write_conditional_cases(report):
    res = _theme_op(
        report,
        operation="add_cf",
        page_name="Overview",
        visual_name="vm01matrixfilters001",
        container="values",
        property_name="backColor",
        rule_type="rules",
        selector_metadata="Sales.Total Amount",
        cf_config={
            "table": "Sales",
            "field": "Total Amount",
            "rules": [{"operator": "GreaterThan", "value": 100, "color": "#00AA00"}],
        },
    )
    assert res["success"], res
    entries = _read(_vj(report, "vm01matrixfilters001"))["visual"]["objects"]["values"]
    entry = next(
        e
        for e in entries
        if (e.get("selector") or {}).get("metadata") == "Sales.Total Amount"
        and "backColor" in e["properties"]
    )
    # real CF selectors carry dataViewWildcard + metadata
    assert entry["selector"]["data"] == [{"dataViewWildcard": {"matchingOption": 0}}]
    cases = entry["properties"]["backColor"]["solid"]["color"]["expr"]["Conditional"]["Cases"]
    assert cases[0]["Condition"]["Comparison"]["ComparisonKind"] == 1
    assert cases[0]["Value"] == {"Literal": {"Value": "'#00AA00'"}}
    assert "rules" not in json.dumps(entry)


def test_h20_unverified_rule_types_refused(report):
    before = _vj(report, "vm01matrixfilters001").read_bytes()
    from core.pbip.conditional_formatting_engine import add_rule

    res = add_rule(
        definition_path=report / "definition",
        page_name="Overview",
        visual_name="vm01matrixfilters001",
        container="values",
        property_name="x",
        rule_type="data_bars",
        config={},
    )
    assert res["success"] is False
    assert _vj(report, "vm01matrixfilters001").read_bytes() == before


def test_h20_list_cf_reads_the_real_shape(report):
    res = _theme_op(
        report, operation="list_cf", page_name="Overview", visual_name="vm01matrixfilters001"
    )
    assert "backColor" in json.dumps(res)


# ---------------------------------------------------------------------------
# H-21
# ---------------------------------------------------------------------------


def test_h21_set_formatting_array_form(report):
    res = _theme_op(
        report,
        operation="set_formatting",
        visual_type_target="cardVisual",
        formatting={"value": [{"fontSize": 14}], "background": {"show": True}},
    )
    assert res["success"], res
    style = _theme(report)["visualStyles"]["cardVisual"]["*"]
    assert style["value"] == [{"fontSize": 14}]
    assert style["background"] == [{"transparency": 0, "show": True}]


def test_h21_push_visual_unwraps_literals_into_arrays(report):
    res = _theme_op(
        report, operation="push_visual", page_name="Overview", visual_name="vc01cardvisual000001"
    )
    assert res["success"], res
    style = _theme(report)["visualStyles"]["cardVisual"]["*"]
    assert style["value"] == [{"fontSize": 14, "bold": False}]
    assert style["layout"][0]["calloutSize"] == 8
    assert isinstance(style["title"], list) and style["title"][0]["show"] is True
    assert "expr" not in json.dumps(style)
