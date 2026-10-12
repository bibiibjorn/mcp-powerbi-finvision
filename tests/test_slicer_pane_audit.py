"""Tests for slicer pane-audit and bulk-rename ops."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def pbip_two_collisions_one_misleading(tmp_path: Path) -> Path:
    """Single page with:
    - two slicers titled "Sector" (collision)
    - one slicer titled "Sector" bound to a non-Sector dim (misleading + collider)
    - one untitled slicer bound to "Customer Name"
    """
    report = tmp_path / "R.Report"
    pages_dir = report / "definition" / "pages"
    page_dir = pages_dir / "p1.Page"
    visuals_dir = page_dir / "visuals"
    visuals_dir.mkdir(parents=True)
    (page_dir / "page.json").write_text(
        json.dumps({"displayName": "Settings", "name": "p1.Page"}), encoding="utf-8"
    )
    (pages_dir / "pages.json").write_text(
        json.dumps({"pageOrder": ["p1.Page"], "activePageName": "p1.Page"}),
        encoding="utf-8",
    )

    def write_slicer(vid: str, title: str | None, entity: str, prop: str, x: float, y: float):
        vd = visuals_dir / vid
        vd.mkdir()
        v: dict = {
            "visualType": "slicer",
            "query": {
                "queryState": {
                    "Values": {
                        "projections": [
                            {
                                "field": {
                                    "Column": {
                                        "Expression": {"SourceRef": {"Entity": entity}},
                                        "Property": prop,
                                    }
                                },
                            }
                        ]
                    }
                }
            },
            "objects": {},
        }
        if title is not None:
            v["visualContainerObjects"] = {
                "title": [{"properties": {"text": {"expr": {"Literal": {"Value": f"'{title}'"}}}}}]
            }
        (vd / "visual.json").write_text(
            json.dumps(
                {
                    "name": vid,
                    "position": {"x": x, "y": y, "width": 100, "height": 40, "z": 0},
                    "visual": v,
                }
            ),
            encoding="utf-8",
        )

    write_slicer("v1", "Sector", "d Asset", "Sector", x=10, y=10)
    write_slicer("v2", "Sector", "d Asset", "Sector", x=10, y=60)
    write_slicer("v3", "Sector", "d Entity", "Entity Name", x=10, y=110)
    write_slicer("v4", None, "d Customer", "Customer Name", x=10, y=160)
    return report


def test_audit_finds_collisions_and_misleading(pbip_two_collisions_one_misleading: Path):
    from server.handlers._internal.slicer_pane_audit import _op_audit_pane_collisions
    from core.utilities.pbip_utils import find_definition_folder

    definition = find_definition_folder(str(pbip_two_collisions_one_misleading))
    result = _op_audit_pane_collisions(
        {"pbip_path": str(pbip_two_collisions_one_misleading)}, definition
    )
    assert result["success"] is True
    assert result["total_collisions"] == 1
    collision = result["collisions"][0]
    assert collision["label"].lower() == "sector"
    assert {s["visual_name"] for s in collision["slicers"]} == {"v1", "v2", "v3"}
    misleading_ids = {m["visual_name"] for m in result["misleading"]}
    assert "v3" in misleading_ids
    assert "v4" not in misleading_ids


def test_audit_with_include_no_title_false(pbip_two_collisions_one_misleading: Path):
    from server.handlers._internal.slicer_pane_audit import _op_audit_pane_collisions
    from core.utilities.pbip_utils import find_definition_folder

    definition = find_definition_folder(str(pbip_two_collisions_one_misleading))
    result = _op_audit_pane_collisions(
        {"pbip_path": str(pbip_two_collisions_one_misleading), "include_no_title": False},
        definition,
    )
    for c in result["collisions"]:
        assert c["label"] != "(no title)"


def test_bulk_rename_rule_a_single_misleading(tmp_path: Path):
    """One page, one slicer titled 'Sector' bound to Entity Name → renamed."""
    from server.handlers._internal.slicer_pane_audit import _op_bulk_rename_pane_names
    from core.utilities.pbip_utils import find_definition_folder

    report = tmp_path / "R.Report"
    pages_dir = report / "definition" / "pages"
    page_dir = pages_dir / "p1.Page"
    visuals_dir = page_dir / "visuals"
    visuals_dir.mkdir(parents=True)
    (page_dir / "page.json").write_text(
        json.dumps({"displayName": "Settings", "name": "p1.Page"}), encoding="utf-8"
    )

    vd = visuals_dir / "v1"
    vd.mkdir()
    payload = {
        "name": "v1",
        "position": {"x": 0, "y": 0, "width": 100, "height": 40, "z": 0},
        "visual": {
            "visualType": "slicer",
            "query": {
                "queryState": {
                    "Values": {
                        "projections": [
                            {
                                "field": {
                                    "Column": {
                                        "Expression": {"SourceRef": {"Entity": "d Entity"}},
                                        "Property": "Entity Name",
                                    }
                                },
                            }
                        ]
                    }
                }
            },
            "objects": {},
            "visualContainerObjects": {
                "title": [{"properties": {"text": {"expr": {"Literal": {"Value": "'Sector'"}}}}}]
            },
        },
    }
    (vd / "visual.json").write_text(json.dumps(payload), encoding="utf-8")

    definition = find_definition_folder(str(report))
    result = _op_bulk_rename_pane_names({"pbip_path": str(report)}, definition)
    assert result["success"] is True
    assert result["applied"] == 1
    assert result["plan"][0]["new_title"] == "Entity Name"
    assert result["plan"][0]["rule"] == "rule_a_dim_only"
    assert result["plan"][0]["status"] == "changed"
    written = json.loads((vd / "visual.json").read_text("utf-8"))
    assert (
        written["visual"]["visualContainerObjects"]["title"][0]["properties"]["text"]["expr"][
            "Literal"
        ]["Value"]
        == "'Entity Name'"
    )


def _make_pbip_with_slicers(tmp_path: Path, slicers_spec: list) -> Path:
    """slicers_spec: list of {vid, title, entity, prop, x, y}."""
    report = tmp_path / "R.Report"
    pages_dir = report / "definition" / "pages"
    page_dir = pages_dir / "p1.Page"
    visuals_dir = page_dir / "visuals"
    visuals_dir.mkdir(parents=True)
    (page_dir / "page.json").write_text(
        json.dumps({"displayName": "P1", "name": "p1.Page"}), encoding="utf-8"
    )
    for spec in slicers_spec:
        vd = visuals_dir / spec["vid"]
        vd.mkdir()
        v: dict = {
            "visualType": "slicer",
            "query": {
                "queryState": {
                    "Values": {
                        "projections": [
                            {
                                "field": {
                                    "Column": {
                                        "Expression": {"SourceRef": {"Entity": spec["entity"]}},
                                        "Property": spec["prop"],
                                    }
                                },
                            }
                        ]
                    }
                }
            },
            "objects": {},
        }
        if spec["title"] is not None:
            v["visualContainerObjects"] = {
                "title": [
                    {"properties": {"text": {"expr": {"Literal": {"Value": f"'{spec['title']}'"}}}}}
                ]
            }
        (vd / "visual.json").write_text(
            json.dumps(
                {
                    "name": spec["vid"],
                    "position": {
                        "x": spec["x"],
                        "y": spec["y"],
                        "width": 100,
                        "height": 40,
                        "z": 0,
                    },
                    "visual": v,
                }
            ),
            encoding="utf-8",
        )
    return report


def test_bulk_rename_zero_match_both_renamed(tmp_path: Path):
    """Both slicers titled 'Sector'; neither dim is Sector → both renamed."""
    from server.handlers._internal.slicer_pane_audit import _op_bulk_rename_pane_names
    from core.utilities.pbip_utils import find_definition_folder

    report = _make_pbip_with_slicers(
        tmp_path,
        [
            {
                "vid": "v1",
                "title": "Sector",
                "entity": "d Asset",
                "prop": "Entity Name",
                "x": 0,
                "y": 0,
            },
            {
                "vid": "v2",
                "title": "Sector",
                "entity": "d Asset",
                "prop": "Entity Group",
                "x": 0,
                "y": 60,
            },
        ],
    )
    definition = find_definition_folder(str(report))
    result = _op_bulk_rename_pane_names({"pbip_path": str(report)}, definition)
    new_by_id = {p["visual_name"]: p["new_title"] for p in result["plan"]}
    assert new_by_id == {"v1": "Entity Name", "v2": "Entity Group"}


def test_bulk_rename_one_match_keeper_kept(tmp_path: Path):
    """Two 'Sector' slicers — one truly bound to Sector, one to Entity Name."""
    from server.handlers._internal.slicer_pane_audit import _op_bulk_rename_pane_names
    from core.utilities.pbip_utils import find_definition_folder

    report = _make_pbip_with_slicers(
        tmp_path,
        [
            {"vid": "v1", "title": "Sector", "entity": "d Asset", "prop": "Sector", "x": 0, "y": 0},
            {
                "vid": "v2",
                "title": "Sector",
                "entity": "d Asset",
                "prop": "Entity Name",
                "x": 0,
                "y": 60,
            },
        ],
    )
    definition = find_definition_folder(str(report))
    result = _op_bulk_rename_pane_names({"pbip_path": str(report)}, definition)
    plans = {p["visual_name"]: (p["new_title"], p["rule"]) for p in result["plan"]}
    assert "v1" not in plans
    assert plans["v2"] == ("Entity Name", "rule_a_keeper")


def test_bulk_rename_n_match_positional_suffix(tmp_path: Path):
    """Two 'Customer' slicers both bound to Customer dims → both keep 'Customer',
    positional fallback adds #1/#2."""
    from server.handlers._internal.slicer_pane_audit import _op_bulk_rename_pane_names
    from core.utilities.pbip_utils import find_definition_folder

    report = _make_pbip_with_slicers(
        tmp_path,
        [
            {
                "vid": "v1",
                "title": "Customer",
                "entity": "d Customer",
                "prop": "Customer Name",
                "x": 0,
                "y": 0,
            },
            {
                "vid": "v2",
                "title": "Customer",
                "entity": "d Customer",
                "prop": "Customer Type",
                "x": 100,
                "y": 0,
            },
        ],
    )
    definition = find_definition_folder(str(report))
    result = _op_bulk_rename_pane_names({"pbip_path": str(report)}, definition)
    titles = {p["visual_name"]: p["new_title"] for p in result["plan"]}
    assert titles == {"v1": "Customer #1", "v2": "Customer #2"}
    assert all(p["rule"] == "rule_b_positional" for p in result["plan"])


def test_bulk_rename_cross_group_disambiguation(tmp_path: Path):
    """Page has 'Sector' bound to Entity Name AND an existing 'Entity Name'
    slicer. Renaming the first to 'Entity Name' creates a new collision.
    Disambiguator should add #1/#2 across both."""
    from server.handlers._internal.slicer_pane_audit import _op_bulk_rename_pane_names
    from core.utilities.pbip_utils import find_definition_folder

    report = _make_pbip_with_slicers(
        tmp_path,
        [
            {
                "vid": "v1",
                "title": "Sector",
                "entity": "d Entity",
                "prop": "Entity Name",
                "x": 0,
                "y": 0,
            },
            {
                "vid": "v2",
                "title": "Entity Name",
                "entity": "d Entity",
                "prop": "Entity Name",
                "x": 100,
                "y": 0,
            },
        ],
    )
    definition = find_definition_folder(str(report))
    result = _op_bulk_rename_pane_names({"pbip_path": str(report)}, definition)
    titles = {p["visual_name"]: p["new_title"] for p in result["plan"]}
    rules = {p["visual_name"]: p["rule"] for p in result["plan"]}
    assert set(titles.values()) == {"Entity Name #1", "Entity Name #2"}
    assert rules["v2"] == "cross_group_disambiguated"


def test_bulk_rename_round_trip_already_correct(tmp_path: Path):
    """Run twice — second run reports already_correct for everything."""
    from server.handlers._internal.slicer_pane_audit import _op_bulk_rename_pane_names
    from core.utilities.pbip_utils import find_definition_folder

    report = _make_pbip_with_slicers(
        tmp_path,
        [
            {
                "vid": "v1",
                "title": "Sector",
                "entity": "d Entity",
                "prop": "Entity Name",
                "x": 0,
                "y": 0,
            },
        ],
    )
    definition = find_definition_folder(str(report))
    first = _op_bulk_rename_pane_names({"pbip_path": str(report)}, definition)
    assert first["applied"] == 1

    second = _op_bulk_rename_pane_names({"pbip_path": str(report)}, definition)
    assert second["total_planned"] == 0
    assert second["applied"] == 0
    assert second["errors"] == 0


def test_bulk_rename_byte_fidelity_unrelated_files(tmp_path: Path):
    """Files not in the rename plan must be byte-identical after the run."""
    from server.handlers._internal.slicer_pane_audit import _op_bulk_rename_pane_names
    from core.utilities.pbip_utils import find_definition_folder

    report = _make_pbip_with_slicers(
        tmp_path,
        [
            {
                "vid": "v1",
                "title": "Sector",
                "entity": "d Entity",
                "prop": "Entity Name",
                "x": 0,
                "y": 0,
            },
            {
                "vid": "v2",
                "title": "Customer Name",
                "entity": "d Customer",
                "prop": "Customer Name",
                "x": 0,
                "y": 60,
            },
            {"vid": "v3", "title": None, "entity": "d Date", "prop": "Period", "x": 0, "y": 120},
        ],
    )
    definition = find_definition_folder(str(report))
    visuals_dir = definition / "pages" / "p1.Page" / "visuals"

    before_v2 = (visuals_dir / "v2" / "visual.json").read_bytes()
    before_v3 = (visuals_dir / "v3" / "visual.json").read_bytes()

    _op_bulk_rename_pane_names({"pbip_path": str(report)}, definition)

    assert (visuals_dir / "v2" / "visual.json").read_bytes() == before_v2
    assert (visuals_dir / "v3" / "visual.json").read_bytes() == before_v3
