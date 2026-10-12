"""F-16: ``11_PBIP_Authoring clone_visual`` (core/pbip/authoring/visual_clone.py).

A single visual copied with fresh ids, group + coordinate handling, its filterConfig and
(inherit_interactions) every visualInteractions entry in both directions -- the lack of
which made 11 cloned visuals go blank (feedback_cloned_visuals_lose_interactions).
Synthetic copies of the golden fixture only.
"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from core.pbip import pbir_model
from core.pbip.authoring.visual_clone import clone_visual, remap_ids_in_copy
from server.handlers.authoring_handler import AUTHORING_SCHEMA, handle_pbip_authoring

GOLDEN = Path(__file__).parent / "fixtures" / "pbir_golden" / "GoldenCo"
OVERVIEW = "aa01overview00000001"
DETAILS = "ab02details000000002"
SLICER = "vs01slicerinverted01"
SLICER2 = "vs02slicervisinv0002"
MATRIX = "vm01matrixfilters001"
CARD = "vc01cardvisual000001"
CARD2 = "vc02cardlegacy000001"
GROUP = "vg01group00000000001"
CHILD = "vg02groupchild000001"


@pytest.fixture(autouse=True)
def _gate_off(monkeypatch):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "OFF")
    from core.autonomous.gating import invalidate_policy_cache

    invalidate_policy_cache()


@pytest.fixture()
def defn(tmp_path):
    dst = tmp_path / "GoldenCo"
    shutil.copytree(GOLDEN, dst)
    return dst / "RPT-Sales.Report" / "definition"


def _vjson(defn: Path, page: str, vid: str) -> dict:
    return json.loads(
        (defn / "pages" / page / "visuals" / vid / "visual.json").read_text(encoding="utf-8")
    )


def _page(defn: Path, page: str) -> dict:
    return json.loads((defn / "pages" / page / "page.json").read_text(encoding="utf-8"))


def _tree_hash(root: Path):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# -- ids / filters ----------------------------------------------------------


def test_same_page_clone_gets_fresh_ids_filters_and_offset(defn):
    src = _vjson(defn, OVERVIEW, MATRIX)
    res = clone_visual(defn, OVERVIEW, MATRIX)
    assert res["success"], res
    new = res["new_visual_id"]
    assert new != MATRIX and len(new) == 20
    clone = _vjson(defn, OVERVIEW, new)
    assert clone["name"] == new
    assert (clone["position"]["x"], clone["position"]["y"]) == (40, 120)  # +20/+20 default
    assert clone["position"]["z"] > src["position"]["z"]
    # filterConfig copied, filter names regenerated
    src_names = [f["name"] for f in src["filterConfig"]["filters"]]
    new_names = [f["name"] for f in clone["filterConfig"]["filters"]]
    assert len(new_names) == len(src_names) and not set(new_names) & set(src_names)
    assert res["filter_names_regenerated"] == len(src_names)
    # everything else identical (query bindings untouched)
    assert clone["visual"]["query"] == src["visual"]["query"]
    # source untouched
    assert _vjson(defn, OVERVIEW, MATRIX) == src


def test_ids_inside_base64_blobs_of_the_copy_are_remapped(defn):
    vpath = defn / "pages" / OVERVIEW / "visuals" / CARD / "visual.json"
    data = json.loads(vpath.read_text(encoding="utf-8"))
    blob = base64.b64encode(json.dumps({"selector": CARD, "other": "keep"}).encode()).decode()
    data["visual"]["objects"]["blobHolder"] = [{"properties": {"state": blob}}]
    vpath.write_text(json.dumps(data, indent=2), encoding="utf-8")
    res = clone_visual(defn, OVERVIEW, CARD)
    assert res["success"] and res["blob_refs_remapped"] == 1
    clone = _vjson(defn, OVERVIEW, res["new_visual_id"])
    decoded = json.loads(
        base64.b64decode(clone["visual"]["objects"]["blobHolder"][0]["properties"]["state"])
    )
    assert decoded == {"selector": res["new_visual_id"], "other": "keep"}
    # the SOURCE blob and every other visual are untouched
    assert (
        CARD
        in base64.b64decode(
            _vjson(defn, OVERVIEW, CARD)["visual"]["objects"]["blobHolder"][0]["properties"][
                "state"
            ]
        ).decode()
    )


def test_remap_ignores_binary_and_non_base64_strings():
    mapping = {"abcdef0123456789abcd": "11111111111111111111"}
    img = base64.b64encode(bytes(range(256))).decode()
    doc = {"img": img, "text": "see abcdef0123456789abcd here", "name": "abcdef0123456789abcd"}
    new, exact, blobs = remap_ids_in_copy(doc, mapping)
    assert new["img"] == img and new["text"] == doc["text"]  # substring text is not an id
    assert new["name"] == "11111111111111111111" and exact == 1 and blobs == 0


# -- interactions -----------------------------------------------------------


def test_inherit_interactions_both_directions(defn):
    res = clone_visual(defn, OVERVIEW, MATRIX, inherit_interactions=True)
    new = res["new_visual_id"]
    inter = pbir_model.get_page_interactions(_page(defn, OVERVIEW))
    assert {"source": SLICER2, "target": new, "type": "NoFilter"} in inter  # into the clone
    assert {"source": new, "target": CARD2, "type": "HighlightFilter"} in inter  # out of the clone
    assert res["interactions_added"] == 2 and res["interactions_in_source"] == 2
    # originals kept
    assert {"source": SLICER2, "target": MATRIX, "type": "NoFilter"} in inter


def test_without_inherit_the_clone_follows_no_rule_and_says_so(defn):
    before = pbir_model.get_page_interactions(_page(defn, OVERVIEW))
    res = clone_visual(defn, OVERVIEW, MATRIX)
    assert pbir_model.get_page_interactions(_page(defn, OVERVIEW)) == before
    assert res["interactions_not_inherited"] == 2
    assert any("inherit_interactions=true" in n for n in res["notes"])


def test_slicer_clone_keeps_its_nofilter_targets(defn):
    res = clone_visual(defn, OVERVIEW, SLICER, inherit_interactions=True)
    inter = pbir_model.get_page_interactions(_page(defn, OVERVIEW))
    assert {"source": res["new_visual_id"], "target": CARD, "type": "NoFilter"} in inter


def test_cross_page_interactions_need_a_map(defn):
    res = clone_visual(defn, OVERVIEW, SLICER, target_page=DETAILS, inherit_interactions=True)
    assert res["success"] and res["interactions_added"] == 0
    assert any("interaction_map" in n for n in res["notes"])
    assert not pbir_model.get_page_interactions(_page(defn, DETAILS)) or all(
        i["source"] != res["new_visual_id"]
        for i in pbir_model.get_page_interactions(_page(defn, DETAILS))
    )
    # with a map the rule follows onto the target page's visual
    res2 = clone_visual(
        defn,
        OVERVIEW,
        SLICER,
        target_page=DETAILS,
        inherit_interactions=True,
        interaction_map={CARD: "vt01tableinverted001"},
    )
    assert res2["interactions_added"] == 1
    assert {
        "source": res2["new_visual_id"],
        "target": "vt01tableinverted001",
        "type": "NoFilter",
    } in pbir_model.get_page_interactions(_page(defn, DETAILS))


# -- groups / coordinates ---------------------------------------------------


def test_group_clone_remaps_children_and_keeps_local_coordinates(defn):
    res = clone_visual(defn, OVERVIEW, GROUP)
    assert res["success"] and res["visual_count"] == 2
    new_group, new_child = res["id_mapping"][GROUP], res["id_mapping"][CHILD]
    g = _vjson(defn, OVERVIEW, new_group)
    c = _vjson(defn, OVERVIEW, new_child)
    assert "parentGroupName" not in g and (g["position"]["x"], g["position"]["y"]) == (860, 420)
    assert c["parentGroupName"] == new_group
    assert (c["position"]["x"], c["position"]["y"]) == (10, 5)  # group-local: untouched
    assert (
        _vjson(defn, OVERVIEW, CHILD)["parentGroupName"] == GROUP
    )  # source child still in source group


def test_child_clone_same_page_stays_in_its_group(defn):
    res = clone_visual(defn, OVERVIEW, CHILD)
    c = _vjson(defn, OVERVIEW, res["new_visual_id"])
    assert res["group_mode"] == "keep" and c["parentGroupName"] == GROUP
    assert (c["position"]["x"], c["position"]["y"]) == (30, 25)  # local 10,5 + 20,20


def test_child_clone_top_level_converts_to_page_absolute(defn):
    res = clone_visual(defn, OVERVIEW, CHILD, target_page=DETAILS)
    c = _vjson(defn, DETAILS, res["new_visual_id"])
    assert res["group_mode"] == "top_level" and "parentGroupName" not in c
    assert (c["position"]["x"], c["position"]["y"]) == (850, 405)  # 840+10, 400+5, offset 0
    # explicit position override in the page frame
    res2 = clone_visual(
        defn, OVERVIEW, CHILD, target_page=DETAILS, position={"x": 100, "y": 50, "width": 60}
    )
    c2 = _vjson(defn, DETAILS, res2["new_visual_id"])
    assert (c2["position"]["x"], c2["position"]["y"], c2["position"]["width"]) == (100, 50, 60)


def test_clone_group_mode_clones_the_ancestor_chain(defn):
    res = clone_visual(defn, OVERVIEW, CHILD, target_page=DETAILS, group_mode="clone_group")
    assert res["success"] and res["group_mode"] == "clone_group"
    new_child = res["new_visual_id"]
    new_group = res["id_mapping"][GROUP]
    c = _vjson(defn, DETAILS, new_child)
    g = _vjson(defn, DETAILS, new_group)
    assert c["parentGroupName"] == new_group and (c["position"]["x"], c["position"]["y"]) == (10, 5)
    assert g["visualGroup"] == _vjson(defn, OVERVIEW, GROUP)["visualGroup"]
    assert (g["position"]["x"], g["position"]["y"]) == (840, 400)
    # the source group's OTHER children were not copied
    kids = [
        v
        for v in (defn / "pages" / DETAILS / "visuals").iterdir()
        if _vjson(defn, DETAILS, v.name).get("parentGroupName") == new_group
    ]
    assert len(kids) == 1


def test_reparent_under_an_existing_group_converts_coordinates(defn):
    # put a top-level card inside the target page's... same-page group (page-absolute -> group-local)
    res = clone_visual(defn, OVERVIEW, CARD, parent_group=GROUP, offset={"x": 0, "y": 0})
    assert res["success"] and res["group_mode"] == "parent_group"
    card_pos = _vjson(defn, OVERVIEW, CARD)["position"]
    c = _vjson(defn, OVERVIEW, res["new_visual_id"])
    assert c["parentGroupName"] == GROUP
    assert (c["position"]["x"], c["position"]["y"]) == (card_pos["x"] - 840, card_pos["y"] - 400)
    # the absolute position is unchanged
    assert res["position"]["page_absolute"] == {"x": card_pos["x"], "y": card_pos["y"]}


def test_bad_group_args(defn):
    assert (
        clone_visual(defn, OVERVIEW, CARD, parent_group="nope")["error_type"]
        == "parent_group_not_found"
    )
    assert clone_visual(defn, OVERVIEW, GROUP, parent_group=CHILD)["success"] is False
    assert clone_visual(defn, OVERVIEW, CARD, group_mode="bad")["error_type"] == "invalid_args"


# -- bookmarks --------------------------------------------------------------


def test_copy_bookmark_refs(defn):
    bm = defn / "bookmarks" / "bm0000000000000000a1.bookmark.json"
    data = json.loads(bm.read_text(encoding="utf-8"))
    data["explorationState"]["sections"][OVERVIEW]["visualContainers"][MATRIX] = {
        "singleVisual": {"visualType": "pivotTable", "objects": {}}
    }
    bm.write_text(json.dumps(data, indent=2), encoding="utf-8")
    res = clone_visual(defn, OVERVIEW, MATRIX, copy_bookmark_refs=True)
    new = res["new_visual_id"]
    out = json.loads(bm.read_text(encoding="utf-8"))
    assert new in out["explorationState"]["sections"][OVERVIEW]["visualContainers"]
    assert (
        new in out["options"]["targetVisualNames"] and MATRIX in out["options"]["targetVisualNames"]
    )
    assert res["bookmarks_updated"] >= 1
    # without the flag nothing in bookmarks changes
    res2 = clone_visual(defn, OVERVIEW, CARD)
    assert res2["bookmarks_updated"] == 0


# -- errors / dry run / handler --------------------------------------------


def test_unknown_page_and_visual(defn):
    assert clone_visual(defn, "nope", CARD)["success"] is False
    assert clone_visual(defn, OVERVIEW, "nope")["error_type"] == "visual_not_found"
    assert clone_visual(defn, OVERVIEW, CARD, target_page="nope")["success"] is False


def _call(defn: Path, **kw):
    return handle_pbip_authoring({"operation": "clone_visual", "pbip_path": str(defn.parent), **kw})


def test_handler_dry_run_writes_nothing(defn):
    before = _tree_hash(defn)
    res = _call(
        defn,
        source_page=OVERVIEW,
        visual_id=MATRIX,
        inherit_interactions=True,
        dry_run=True,
        copy_bookmark_refs=True,
    )
    assert res["success"] and res["dry_run"] is True and res["files_modified"] == 0
    assert res["would_change_count"] >= 2
    assert any(c["action"] in ("create", "write") for c in res["would_change"])
    assert "reload_required" not in res
    assert _tree_hash(defn) == before


def test_handler_real_run_carries_the_reload_hint(defn):
    res = _call(defn, source_page=OVERVIEW, visual_id=CARD, inherit_interactions=True)
    assert res["success"] and res["operation"] == "clone_visual"
    assert res["reload_required"] is True and res["files_modified"] >= 1
    assert (defn / "pages" / OVERVIEW / "visuals" / res["new_visual_id"] / "visual.json").is_file()


def test_handler_validates_args(defn):
    assert _call(defn, source_page=OVERVIEW)["success"] is False  # no visual
    assert _call(defn, source_page=OVERVIEW, visual_id=CARD, offset="x")["success"] is False
    res = _call(defn, source_page=OVERVIEW, visual_id=CARD, offset={"x": "abc"})
    assert res["success"] is False


def test_schema_declares_clone_visual_and_its_args():
    assert "clone_visual" in AUTHORING_SCHEMA["properties"]["operation"]["enum"]
    for key in (
        "target_page",
        "offset",
        "group_mode",
        "inherit_interactions",
        "interaction_map",
        "copy_bookmark_refs",
        "position_frame",
        "parent_group",
    ):
        assert key in AUTHORING_SCHEMA["properties"], key


def test_clone_then_preflight_is_clean(defn):
    from core.pbip.preflight import run_preflight

    res = clone_visual(defn, OVERVIEW, MATRIX, inherit_interactions=True)
    assert res["success"]
    pre = run_preflight(str(defn.parent), tom_check="off", report_author=False)
    assert pre["commit_ok"], [f for f in pre["findings"] if f["severity"] == "error"]


def test_group_children_z_order_moves_with_the_cloned_group(defn):
    src_group_z = _vjson(defn, OVERVIEW, GROUP)["position"]["z"]
    src_child_z = _vjson(defn, OVERVIEW, CHILD)["position"]["z"]
    res = clone_visual(defn, OVERVIEW, GROUP)
    g = _vjson(defn, OVERVIEW, res["id_mapping"][GROUP])["position"]["z"]
    c = _vjson(defn, OVERVIEW, res["id_mapping"][CHILD])["position"]["z"]
    assert g > src_group_z
    assert c - g == src_child_z - src_group_z  # relative layering inside the group is kept
