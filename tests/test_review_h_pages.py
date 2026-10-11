"""Deep review 2026-09-22, slice H — page authoring on real PBIR shapes.

H-03  ``definition/pages/pages.json`` is the page registry (create / clone /
      delete / reorder keep pageOrder / activePageName / landingPageName and
      bookmarks consistent; no stray ``definition/pages.json``).
H-04  page.json string enums (``FitToWidth``, ``HiddenInViewMode``,
      ``type: Tooltip``), drillthrough ``pageBinding`` + ``howCreated``.
H-07  clone_report keeps page / visual ids (navigation stays valid) and
      re-bases ``definition.pbir`` byPath.
H-08  cloned pages remap ``visualInteractions`` to the new visual ids.
H-34  a failing clone leaves no staging folder / half page behind.
H-38  page list reports real visibility / type.

Everything runs through the tool handlers on a copy of the golden fixture.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / "fixtures" / "pbir_golden" / "GoldenCo"
OVERVIEW = "aa01overview00000001"
DETAILS = "ab02details000000002"
TOOLTIP = "ac03tooltip000000003"


@pytest.fixture
def report(tmp_path) -> Path:
    root = tmp_path / "GoldenCo"
    shutil.copytree(GOLDEN, root)
    return root / "RPT-Sales.Report"


def _defn(report: Path) -> Path:
    return report / "definition"


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _pages_meta(report: Path) -> dict:
    return _read(_defn(report) / "pages" / "pages.json")


def _page(report: Path, pid: str) -> dict:
    return _read(_defn(report) / "pages" / pid / "page.json")


def _page_op(report: Path, **args):
    from server.handlers.page_operations_handler import handle_page_operations

    return handle_page_operations({"pbip_path": str(report), "confirm_destructive": True, **args})


def _authoring(report: Path, **args):
    from server.handlers.authoring_handler import handle_pbip_authoring

    return handle_pbip_authoring({"pbip_path": str(report), "confirm_destructive": True, **args})


# ---------------------------------------------------------------------------
# H-03 / H-08
# ---------------------------------------------------------------------------


def test_h03_create_page_registers_in_pages_pages_json(report):
    res = _authoring(report, operation="create_page", page_name="New One", insert_after=OVERVIEW)
    assert res["success"] is True, res
    new_id = res.get("page_id") or res.get("page_name")
    meta = _pages_meta(report)
    assert new_id in meta["pageOrder"]
    assert meta["pageOrder"].index(new_id) == 1
    assert not (_defn(report) / "pages.json").exists()
    page = _page(report, new_id)
    assert page["displayOption"] == "FitToPage"  # H-04: string enum, not 1


def test_h03_h08_clone_page_registers_and_remaps_interactions(report):
    res = _authoring(report, operation="clone_page", source_page=OVERVIEW, new_display_name="Copy")
    assert res["success"] is True, res
    new_id = res["page_id"]
    assert new_id in _pages_meta(report)["pageOrder"]
    assert not (_defn(report) / "pages.json").exists()
    clone = _page(report, new_id)
    new_visuals = {p.name for p in (_defn(report) / "pages" / new_id / "visuals").iterdir()}
    inter = clone["visualInteractions"]
    assert len(inter) == 3
    for entry in inter:
        assert entry["source"] in new_visuals and entry["target"] in new_visuals
    # the child keeps pointing at its (renamed) group
    child_id = res["id_mapping"]["vg02groupchild000001"]
    child = _read(_defn(report) / "pages" / new_id / "visuals" / child_id / "visual.json")
    assert child["parentGroupName"] == res["id_mapping"]["vg01group00000000001"]
    # the source page is untouched
    assert _page(report, OVERVIEW)["visualInteractions"][0]["source"] == "vs01slicerinverted01"
    # no staging folder left behind
    assert not [p for p in report.iterdir() if p.name.startswith(".mcp_stage")]


def test_h03_reorder_on_a_clean_report(report):
    res = _page_op(report, operation="reorder", page_order=[TOOLTIP, OVERVIEW, DETAILS])
    assert res["success"] is True, res
    assert _pages_meta(report)["pageOrder"] == [TOOLTIP, OVERVIEW, DETAILS]


def test_h03_delete_page_repoints_registry_and_scrubs_bookmarks(report):
    res = _page_op(report, operation="delete", page_name=OVERVIEW)
    assert res["success"] is True, res
    meta = _pages_meta(report)
    assert OVERVIEW not in meta["pageOrder"]
    assert meta["activePageName"] == DETAILS
    assert meta["landingPageName"] == DETAILS
    assert not (_defn(report) / "pages" / OVERVIEW).exists()
    items = _read(_defn(report) / "bookmarks" / "bookmarks.json")["items"]
    remaining = {c for i in items for c in (i.get("children") or [])} | {
        i["name"] for i in items if not i.get("children")
    }
    # a1 / a2 captured the deleted page; a3 (Details) survives
    assert "bm0000000000000000a3" in remaining
    assert "bm0000000000000000a1" not in remaining
    for child in (c for i in items for c in (i.get("children") or [])):
        assert isinstance(child, str)


# ---------------------------------------------------------------------------
# H-04 / H-38
# ---------------------------------------------------------------------------


def test_h04_display_visibility_tooltip_are_string_enums(report):
    assert _page_op(
        report, operation="set_display", page_name="Overview", display_option="FitToWidth"
    )["success"]
    assert _page(report, OVERVIEW)["displayOption"] == "FitToWidth"

    assert _page_op(report, operation="hide", page_name="Overview")["success"]
    assert _page(report, OVERVIEW)["visibility"] == "HiddenInViewMode"
    assert _page_op(report, operation="show", page_name="Overview")["success"]
    assert "visibility" not in _page(report, OVERVIEW)

    res = _page_op(report, operation="set_tooltip", page_name="Overview")
    assert res["success"], res
    page = _page(report, OVERVIEW)
    assert page["type"] == "Tooltip"
    assert "pageType" not in page


def test_h04_set_drillthrough_writes_page_binding(report):
    res = _page_op(
        report,
        operation="set_drillthrough",
        page_name="Overview",
        table="Product Line",
        field="Region",
    )
    assert res["success"], res
    page = _page(report, OVERVIEW)
    assert page["type"] == "Drillthrough"
    drill = [f for f in page["filterConfig"]["filters"] if f.get("howCreated") == "Drillthrough"]
    assert len(drill) == 1
    assert drill[0]["type"] == "Categorical"
    assert drill[0]["field"]["Column"]["Property"] == "Region"
    binding = page["pageBinding"]
    assert binding["type"] == "Drillthrough"
    assert binding["parameters"][0]["boundFilter"] == drill[0]["name"]


def test_h04_background_and_wallpaper_use_objects(report):
    assert _page_op(report, operation="set_background", page_name="Overview", color="#112233")[
        "success"
    ]
    assert _page_op(report, operation="set_wallpaper", page_name="Overview", color="#445566")[
        "success"
    ]
    page = _page(report, OVERVIEW)
    assert "background" in page["objects"] and "outspace" in page["objects"]
    assert "background" not in page  # never a top-level key
    assert "#112233" in json.dumps(page["objects"]["background"])


def test_h38_list_reports_real_visibility_and_type(report):
    res = _page_op(report, operation="list", summary_only=False)
    pages = {p["page_id"]: p for p in res["pages"]}
    assert pages[DETAILS]["is_hidden"] is True
    assert pages[TOOLTIP]["is_hidden"] is True
    assert pages[OVERVIEW]["is_hidden"] is False
    assert pages[TOOLTIP]["page_type"] in ("Tooltip", "tooltip")
    assert pages[DETAILS]["page_binding"]["type"] == "Drillthrough"
    assert pages[OVERVIEW]["interaction_count"] == 3


# ---------------------------------------------------------------------------
# H-07 / H-34
# ---------------------------------------------------------------------------


def test_h07_clone_report_keeps_ids_and_rebases_model(report):
    target = report.parent / "copies" / "RPT-Copy.Report"
    res = _authoring(report, operation="clone_report", target_path=str(target))
    assert res["success"] is True, res
    tdef = target / "definition"
    meta = _read(tdef / "pages" / "pages.json")
    pages = {p.name for p in (tdef / "pages").iterdir() if p.is_dir()}
    assert set(meta["pageOrder"]) <= pages
    assert meta["activePageName"] in pages
    btn = _read(tdef / "pages" / OVERVIEW / "visuals" / "vb01actionbutton0001" / "visual.json")
    bm = btn["visual"]["visualContainerObjects"]["visualLink"][0]["properties"]["bookmark"]
    assert (tdef / "bookmarks" / f"{bm['expr']['Literal']['Value'][1:-1]}.bookmark.json").exists()
    inter = _read(tdef / "pages" / OVERVIEW / "page.json")["visualInteractions"]
    vids = {p.name for p in (tdef / "pages" / OVERVIEW / "visuals").iterdir()}
    assert all(i["source"] in vids and i["target"] in vids for i in inter)
    by_path = _read(target / "definition.pbir")["datasetReference"]["byPath"]["path"]
    assert (target / by_path).resolve() == (report.parent / "MDL-Core.SemanticModel").resolve()
    assert not [p for p in target.parent.iterdir() if p.name.startswith(".mcp_clone")]


def test_h34_failed_clone_page_leaves_nothing(report, monkeypatch):
    from core.pbip.authoring import safe_io

    real_move = safe_io.move_path
    calls = {"n": 0}

    def flaky_move(src, dst):
        calls["n"] += 1
        if calls["n"] == 3:
            raise safe_io.WriteError("disk full", dst)
        return real_move(src, dst)

    monkeypatch.setattr(safe_io, "move_path", flaky_move)
    before_pages = sorted(p.name for p in (_defn(report) / "pages").iterdir())
    before_meta = (_defn(report) / "pages" / "pages.json").read_bytes()
    res = _authoring(report, operation="clone_page", source_page=OVERVIEW)
    assert res["success"] is False
    assert sorted(p.name for p in (_defn(report) / "pages").iterdir()) == before_pages
    assert (_defn(report) / "pages" / "pages.json").read_bytes() == before_meta
    assert not [p for p in report.iterdir() if p.name.startswith(".mcp_stage")]


# ---------------------------------------------------------------------------
# H-28
# ---------------------------------------------------------------------------


def test_h28_interaction_uses_real_type_names(report):
    res = _page_op(
        report,
        operation="set_interaction",
        page_name="Overview",
        source_visual="vs01slicerinverted01",
        target_visual="vt01tableinverted001",
        interaction_type="Highlight",
    )
    # target lives on another page -> not found on Overview
    assert res["success"] is False
    res = _page_op(
        report,
        operation="set_interaction",
        page_name="Overview",
        source_visual="vs01slicerinverted01",
        target_visual="Total Amount",
        interaction_type="Highlight",
    )
    assert res["success"], res
    inter = _page(report, OVERVIEW)["visualInteractions"]
    hit = [
        i
        for i in inter
        if i["source"] == "vs01slicerinverted01" and i["target"] == "vc01cardvisual000001"
    ]
    assert hit == [
        {
            "source": "vs01slicerinverted01",
            "target": "vc01cardvisual000001",
            "type": "HighlightFilter",
        }
    ]


def test_h28_ambiguous_page_prefix_is_refused(report):
    assert _authoring(
        report, operation="clone_page", source_page=OVERVIEW, new_display_name="Overview 2"
    )["success"]
    res = _page_op(
        report,
        operation="set_interaction",
        page_name="Overv",
        source_visual="vs01slicerinverted01",
        target_visual="vc01cardvisual000001",
        interaction_type="NoFilter",
    )
    assert res["success"] is False
    assert (
        res.get("error_type") in ("ambiguous_page", "page_not_found") or "ambiguous" in res["error"]
    )


def test_h28_ambiguous_visual_title_is_refused(report):
    vroot = _defn(report) / "pages" / OVERVIEW / "visuals"
    card = _read(vroot / "vc01cardvisual000001" / "visual.json")
    card["name"] = "vc03dupetitle0000001"
    (vroot / "vc03dupetitle0000001").mkdir()
    (vroot / "vc03dupetitle0000001" / "visual.json").write_text(json.dumps(card), encoding="utf-8")
    before = (_defn(report) / "pages" / OVERVIEW / "page.json").read_bytes()
    res = _page_op(
        report,
        operation="set_interaction",
        page_name="Overview",
        source_visual="vs01slicerinverted01",
        target_visual="Total Amount",
        interaction_type="NoFilter",
    )
    assert res["success"] is False
    assert (_defn(report) / "pages" / OVERVIEW / "page.json").read_bytes() == before
