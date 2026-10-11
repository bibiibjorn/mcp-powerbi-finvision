"""core.pbip.pbir_model -- the shared PBIR/PBIP shape accessors (2026-09-22 review).

Every assertion runs against the synthetic golden fixture
``tests/fixtures/pbir_golden/GoldenCo`` (see its README), whose shapes mirror
the real R0105 / R0201 / S01 / S02 projects. Finding IDs: G-06, H-13 (pointer
chain), H-03 (pages.json), F-03/G-09/H-09/H-22 (filterConfig), F-04/H-10
(inverted polarity), H-08 (visualInteractions), H-06 (bookmark children),
G-11 (bookmark targets), H-04/H-15/H-11 (literals + ComparisonKind), H-30
(lazy visual iteration).
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from core.pbip import pbir_model as pm

GOLDEN = Path(__file__).parent / "fixtures" / "pbir_golden" / "GoldenCo"
SALES = GOLDEN / "RPT-Sales.Report"
DEFN = SALES / "definition"
P1 = "aa01overview00000001"
P2 = "ab02details000000002"


def _visual(page: str, vid: str) -> dict:
    return json.loads(
        (DEFN / "pages" / page / "visuals" / vid / "visual.json").read_text(encoding="utf-8")
    )


def _names(entries, key="report_dir"):
    return sorted(Path(e[key]).name for e in entries)


# ------------------------------------------------------------ pointer chain


def test_report_pbip_resolves_through_bypath_to_shared_model():
    res = pm.resolve_pbip(GOLDEN / "RPT-Sales.pbip")
    assert res["target"] == "report"
    assert _names(res["reports"]) == ["RPT-Sales.Report"]
    rep = res["reports"][0]
    assert rep["dataset_reference"] == "byPath"
    assert rep["model_path"] == "../MDL-Core.SemanticModel"
    assert Path(rep["model_dir"]).name == "MDL-Core.SemanticModel"
    assert [m["name"] for m in res["models"]] == ["MDL-Core"]
    assert res["models"][0]["format"] == "tmdl"
    assert Path(res["pbip_file"]).name == "RPT-Sales.pbip"


def test_model_only_pbip_naming_missing_report_falls_back_to_sibling_model():
    res = pm.resolve_pbip(GOLDEN / "MDL-Core.pbip")
    assert res["target"] == "model"
    assert [m["name"] for m in res["models"]] == ["MDL-Core"]
    # every sibling report bound to the model -- never an arbitrary one (H-13)
    assert _names(res["reports"]) == ["RPT-Finance.Report", "RPT-Sales.Report"]
    assert any("MDL-Core.Report" in w for w in res["warnings"])


def test_pbip_whose_artifact_has_a_different_stem_is_a_model_project(tmp_path):
    """S02-SemanticModel.pbip -> artifact R0200-SemanticModel.Report (exists)."""
    root = tmp_path / "GoldenCo"
    shutil.copytree(GOLDEN, root)
    (root / "MDL-Core.pbip").write_text(
        json.dumps({"version": "1.0", "artifacts": [{"report": {"path": "RPT-Finance.Report"}}]}),
        encoding="utf-8",
    )
    res = pm.resolve_pbip(root / "MDL-Core.pbip")
    assert res["target"] == "model"
    assert _names(res["reports"]) == ["RPT-Finance.Report", "RPT-Sales.Report"]


@pytest.mark.parametrize(
    "rel,target",
    [
        ("RPT-Sales.Report", "report"),
        ("RPT-Sales.Report/definition", "report"),
        ("RPT-Sales.Report/definition/pages/aa01overview00000001", "report"),
        ("MDL-Core.SemanticModel", "model"),
        ("MDL-Core.SemanticModel/definition", "model"),
    ],
)
def test_any_artifact_path_form_resolves(rel, target):
    res = pm.resolve_pbip(GOLDEN / rel)
    assert res["target"] == target
    assert [m["name"] for m in res["models"]] == ["MDL-Core"]


def test_folder_with_several_reports_lists_all_and_flags_byconnection():
    res = pm.resolve_pbip(GOLDEN)
    assert res["target"] == "folder"
    assert _names(res["reports"]) == [
        "RPT-Finance.Report",
        "RPT-Remote.Report",
        "RPT-Sales.Report",
    ]
    remote = next(r for r in res["reports"] if r["name"] == "RPT-Remote")
    assert remote["dataset_reference"] == "byConnection" and remote["model_dir"] is None
    assert [m["name"] for m in res["models"]] == ["MDL-Core"]
    assert _names([{"d": d} for d in res["models"][0]["report_dirs"]], "d") == [
        "RPT-Finance.Report",
        "RPT-Sales.Report",
    ]


def test_find_reports_bound_to_model():
    bound = pm.find_reports_bound_to_model(GOLDEN / "MDL-Core.SemanticModel")
    assert sorted(Path(b).name for b in bound) == ["RPT-Finance.Report", "RPT-Sales.Report"]


def test_stale_definition_pbir_inside_semanticmodel_is_not_a_report(tmp_path):
    """S01's .SemanticModel folder carries a stale definition.pbir + definition/pages."""
    root = tmp_path / "GoldenCo"
    shutil.copytree(GOLDEN, root)
    model = root / "MDL-Core.SemanticModel"
    (model / "definition.pbir").write_text(
        json.dumps({"datasetReference": {"byPath": {"path": "../Gone.SemanticModel"}}}),
        encoding="utf-8",
    )
    (model / "definition" / "pages").mkdir()
    reports, models = pm.discover_artifacts(root)
    assert [p.name for p in models] == ["MDL-Core.SemanticModel"]
    assert "MDL-Core.SemanticModel" not in [p.name for p in reports]


def test_read_dataset_reference_missing_target_and_no_pbir(tmp_path):
    rep = tmp_path / "X.Report"
    rep.mkdir()
    assert pm.read_dataset_reference(rep)["kind"] == "none"
    (tmp_path / "X.SemanticModel").mkdir()
    assert pm.read_dataset_reference(rep)["kind"] == "sibling"
    (rep / "definition.pbir").write_text(
        json.dumps({"datasetReference": {"byPath": {"path": "../Nope.SemanticModel"}}}),
        encoding="utf-8",
    )
    ref = pm.read_dataset_reference(rep)
    assert ref["kind"] == "byPath" and ref["model_dir"] is None and "not found" in ref["note"]


# ------------------------------------------------------------ report paths


def test_pages_json_lives_under_pages():
    p = pm.pages_json_path(DEFN)
    assert p == DEFN / "pages" / "pages.json" and p.is_file()
    assert not (DEFN / "pages.json").exists()
    order = json.loads(p.read_text(encoding="utf-8"))["pageOrder"]
    assert order[0] == P1


def test_iter_visuals_loads_only_the_requested_page(monkeypatch):
    loaded = []
    real = pm._load_json_quiet

    def spy(path):
        loaded.append(Path(path))
        return real(path)

    monkeypatch.setattr(pm, "_load_json_quiet", spy)
    recs = list(pm.iter_visuals(DEFN, page="Details"))  # display name
    assert sorted(r.visual_dir.name for r in recs) == [
        "vd01donuttopn0000001",
        "vl01linereldate00001",
        "vt01tableinverted001",
    ]
    assert all(P2 in str(p) for p in loaded), "visuals of other pages were read"

    loaded.clear()
    only = list(pm.iter_visuals(DEFN, page=P1, visual_id="vm01matrixfilters001"))
    assert [r.data["name"] for r in only] == ["vm01matrixfilters001"]
    assert len(loaded) == 1

    loaded.clear()
    gen = pm.iter_visuals(DEFN, page=P1)
    next(gen)
    assert len(loaded) == 1, "iter_visuals must be lazy"

    with pytest.raises(LookupError):
        list(pm.iter_visuals(DEFN, page="No Such Page"))


# ------------------------------------------------------------ visual filters


def test_visual_filters_come_from_top_level_filterconfig():
    v = _visual(P1, "vm01matrixfilters001")
    assert "filters" not in v["visual"]
    names = [f["name"] for f in pm.get_visual_filters(v)]
    assert names == [
        "vf03slotgate00000001",
        "vf04amountrange00001",
        "vf05region0000000001",
        "vf06notblank00000001",
        "vf07fieldonly0000001",
    ]


def test_set_visual_filters_writes_filterconfig_and_drops_legacy_key():
    v = _visual(P1, "vm01matrixfilters001")
    v["visual"]["filters"] = [{"name": "legacy1"}]
    assert [f["name"] for f in pm.get_visual_filters(v)][-1] == "legacy1"
    kept = pm.get_visual_filters(v)[:1]
    pm.set_visual_filters(v, kept)
    assert v["filterConfig"]["filters"] == kept
    assert v["filterConfig"]["filterSortOrder"] == "Custom"  # other keys kept
    assert "filters" not in v["visual"]
    pm.set_visual_filters(v, [])
    assert v["filterConfig"] == {"filterSortOrder": "Custom"}
    card = _visual(P1, "vc01cardvisual000001")
    pm.set_visual_filters(card, [])
    assert "filterConfig" not in card


def test_slicer_selection_filter_accessor():
    v = _visual(P1, "vs01slicerinverted01")
    sel = pm.get_slicer_selection_filter(v)
    assert sel is not None and "Where" in sel
    assert pm.get_slicer_selection_filter(_visual(P1, "vc01cardvisual000001")) is None


# ------------------------------------------------------------ polarity


def test_filter_level_inverted_flag_is_exclude():
    v = _visual(P1, "vs01slicerinverted01")
    f = pm.get_visual_filters(v)[0]
    assert pm.is_inverted_selection(f, v) is True
    assert pm.filter_polarity(f, v) == "exclude"
    values = pm.get_filter_in_values(f)
    assert [row[0]["Literal"]["Value"] for row in values] == ["'Legacy'", "'Internal'"]


def test_visual_level_inverted_flag_applies_to_categorical_filters():
    v = _visual(P1, "vs02slicervisinv0002")
    f = pm.get_visual_filters(v)[0]
    assert "objects" not in f  # no filter-level flag
    assert pm.filter_polarity(f, v) == "exclude"
    assert pm.filter_polarity(f) == "include"  # without the visual there is no flag
    sel = pm.get_slicer_selection_filter(v)
    assert pm.filter_polarity(sel, v) == "exclude"


def test_inverted_with_no_where_means_exclude_nothing():
    v = _visual(P2, "vt01tableinverted001")
    f = pm.get_visual_filters(v)[0]
    assert pm.filter_polarity(f, v) == "exclude"
    assert pm.get_filter_in_values(f) is None


def test_positive_filters_are_include_and_advanced_never_inherits_visual_flag():
    v = _visual(P1, "vm01matrixfilters001")
    by = {f["name"]: f for f in pm.get_visual_filters(v)}
    assert pm.filter_polarity(by["vf05region0000000001"], v) == "include"
    assert pm.filter_polarity(by["vf03slotgate00000001"], v) == "include"
    slicer = _visual(P1, "vs02slicervisinv0002")
    # an Advanced filter placed on a visual with a visual-level flag stays include
    assert pm.is_inverted_selection(by["vf03slotgate00000001"], slicer) is False
    # Not(Comparison) is "not equal / not blank", not an exclude list
    assert pm.filter_polarity(by["vf06notblank00000001"], v) == "include"


def test_topn_filter_has_no_values_list():
    v = _visual(P2, "vd01donuttopn0000001")
    f = pm.get_visual_filters(v)[0]
    assert f["type"] == "TopN"
    assert pm.get_filter_in_values(f) is None
    assert pm.filter_polarity(f, v) == "include"


# ------------------------------------------------------------ interactions


def test_page_interactions_use_visualinteractions():
    page = json.loads((DEFN / "pages" / P1 / "page.json").read_text(encoding="utf-8"))
    assert "interactions" not in page
    items = pm.get_page_interactions(page)
    assert len(items) == 3 and items[0]["type"] == "NoFilter"
    page["interactions"] = [{"source": "x", "target": "y", "type": "NoFilter"}]
    assert len(pm.get_page_interactions(page)) == 4
    pm.set_page_interactions(page, items[:1])
    assert page["visualInteractions"] == items[:1] and "interactions" not in page
    pm.set_page_interactions(page, [])
    assert "visualInteractions" not in page


# ------------------------------------------------------------ bookmarks


def test_bookmark_group_children_are_strings():
    bj = json.loads((DEFN / "bookmarks" / "bookmarks.json").read_text(encoding="utf-8"))
    group = bj["items"][0]
    assert all(isinstance(c, str) for c in group["children"])
    assert pm.bookmark_group_children(group) == [
        "bm0000000000000000a1",
        "bm0000000000000000a2",
    ]
    legacy = {"name": "g", "children": [{"name": "a"}, "b"]}
    assert pm.bookmark_group_children(legacy) == ["a", "b"]
    pm.set_bookmark_group_children(legacy, pm.bookmark_group_children(legacy))
    assert legacy["children"] == ["a", "b"]
    idx = pm.bookmark_membership(bj)
    assert idx["groups"] == {"bg00000000000000grp1": group["children"]}
    assert idx["parent"]["bm0000000000000000a1"] == "bg00000000000000grp1"
    assert idx["parent"]["bm0000000000000000a3"] is None


def test_bookmark_references_from_buttons_and_navigators():
    btn = pm.get_bookmark_references(_visual(P1, "vb01actionbutton0001"))
    assert btn["bookmarks"] == ["bm0000000000000000a1"]
    nav = pm.get_bookmark_references(_visual(P1, "vb02bookmarknav00001"))
    assert nav["bookmark_groups"] == ["bg00000000000000grp1"]
    assert nav["selected_bookmarks"] == ["bm0000000000000000a2"]
    page_nav = {
        "visual": {
            "visualContainerObjects": {
                "visualLink": [
                    {
                        "properties": {
                            "type": {"expr": {"Literal": {"Value": "'PageNavigation'"}}},
                            "bookmark": {"expr": {"Literal": {"Value": "'stale'"}}},
                        }
                    }
                ]
            }
        }
    }
    assert pm.get_bookmark_references(page_nav)["bookmarks"] == []


# ------------------------------------------------------------ literals


@pytest.mark.parametrize(
    "value,kind,expected",
    [
        ("O'Neil", "string", "'O''Neil'"),
        ("Revenue YTD", "auto", "'Revenue YTD'"),  # never bare (H-15)
        ("Segoe UI", "auto", "'Segoe UI'"),
        (12, "double", "12D"),
        (14, "auto", "14D"),
        (12.5, "double", "12.5D"),
        (1e-05, "double", "0.00001D"),  # no exponent (H-15)
        (2024, "int64", "2024L"),  # Int64 columns (H-11c)
        (2024.0, "int64", "2024L"),
        (Decimal("1.50"), "decimal", "1.5M"),
        (True, "auto", "true"),
        (False, "bool", "false"),
        (None, "auto", "null"),
        (dt.date(2022, 12, 31), "auto", "datetime'2022-12-31T00:00:00'"),
    ],
)
def test_encode_literal(value, kind, expected):
    assert pm.encode_literal(value, kind) == expected


def test_encode_literal_rejects_bad_input():
    with pytest.raises(ValueError):
        pm.encode_literal(1.5, "int64")
    with pytest.raises(ValueError):
        pm.encode_literal(float("nan"), "double")
    with pytest.raises(ValueError):
        pm.encode_literal(1, "nonsense")


@pytest.mark.parametrize(
    "text,value,kind",
    [
        ("'O''Neil'", "O'Neil", "string"),
        ("'''Segoe UI'', wf_segoe-ui_normal'", "'Segoe UI', wf_segoe-ui_normal", "string"),
        ("44D", 44.0, "double"),
        ("0L", 0, "int64"),
        ("1.5M", Decimal("1.5"), "decimal"),
        ("true", True, "bool"),
        ("null", None, "null"),
        ("datetime'2022-12-31T00:00:00'", dt.datetime(2022, 12, 31), "datetime"),
        ("weird", "weird", "unknown"),
    ],
)
def test_decode_literal_and_round_trip(text, value, kind):
    assert pm.decode_literal(text) == (value, kind)
    if kind != "unknown":
        assert pm.encode_literal(value, kind) == text.replace("1.5M", "1.5M")


def test_every_literal_in_the_golden_report_round_trips():
    count = 0

    def walk(o):
        nonlocal count
        if isinstance(o, dict):
            lit = o.get("Literal")
            if isinstance(lit, dict) and isinstance(lit.get("Value"), str):
                value, kind = pm.decode_literal(lit["Value"])
                assert kind != "unknown", lit["Value"]
                assert pm.encode_literal(value, kind) == lit["Value"]
                count += 1
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    for f in DEFN.rglob("*.json"):
        walk(json.loads(f.read_text(encoding="utf-8")))
    assert count > 30


def test_comparison_kind_map_matches_real_data():
    assert pm.ADVANCED_FILTER_COMPARISON == {
        "Equal": 0,
        "GreaterThan": 1,
        "GreaterThanOrEqual": 2,
        "LessThan": 3,
        "LessThanOrEqual": 4,
    }
    assert "NotEqual" not in pm.ADVANCED_FILTER_COMPARISON
    v = _visual(P1, "vm01matrixfilters001")
    by = {f["name"]: f for f in pm.get_visual_filters(v)}
    cond = by["vf04amountrange00001"]["filter"]["Where"][0]["Condition"]["And"]
    kinds = (
        cond["Left"]["Comparison"]["ComparisonKind"],
        cond["Right"]["Comparison"]["ComparisonKind"],
    )
    assert tuple(pm.ADVANCED_FILTER_COMPARISON_NAMES[k] for k in kinds) == (
        "GreaterThanOrEqual",
        "LessThanOrEqual",
    )
