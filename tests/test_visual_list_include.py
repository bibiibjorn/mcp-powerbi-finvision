"""T1-E: tests for include=[...] enrichment on 07_Visual_Operations:list.

The selector merges optional fields into the condensed summary so the agent
doesn't have to read per-visual JSON via get_raw_property to copy formatting.
"""

from __future__ import annotations

import json
from pathlib import Path


from server.handlers.visual_layout_handler import _op_list


def _make_report(tmp_path: Path) -> Path:
    """Build a minimal Report.Report/definition with one card visual."""
    defn = tmp_path / "Report.Report" / "definition"
    page = defn / "pages" / "p001.Page"
    visuals = page / "visuals"
    visuals.mkdir(parents=True)
    # page.json (needed by _find_visuals page resolution)
    (page / "page.json").write_text(
        json.dumps({"name": "p001", "displayName": "Page 1"}), encoding="utf-8"
    )
    v = visuals / "v0001"
    v.mkdir()
    (v / "visual.json").write_text(
        json.dumps(
            {
                "name": "v0001",
                "position": {"x": 0, "y": 0, "width": 200, "height": 100, "z": 0},
                "visual": {
                    "visualType": "cardVisual",
                    "objects": {"value": [{"properties": {"fontSize": "14D"}}]},
                    "visualContainerObjects": {
                        "title": [{"properties": {"text": {"expr": {"Literal": {"Value": "'X'"}}}}}]
                    },
                    "query": {
                        "queryState": {"Data": {"projections": [{"field": "Sales[Revenue]"}]}}
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    return defn


def test_list_default_summary_excludes_objects(tmp_path: Path) -> None:
    defn = _make_report(tmp_path)
    res = _op_list({}, defn)
    assert res["success"] is True
    assert res["count"] == 1
    v = res["visuals"][0]
    assert "objects" not in v
    assert "visualContainerObjects" not in v


def test_list_include_objects(tmp_path: Path) -> None:
    defn = _make_report(tmp_path)
    res = _op_list({"include": ["objects"]}, defn)
    assert res["success"] is True
    v = res["visuals"][0]
    assert "objects" in v
    assert v["objects"]["value"][0]["properties"]["fontSize"] == "14D"


def test_list_include_vco_lowercase_alias(tmp_path: Path) -> None:
    defn = _make_report(tmp_path)
    res = _op_list({"include": ["vCO"]}, defn)
    v = res["visuals"][0]
    assert "visualContainerObjects" in v
    assert (
        v["visualContainerObjects"]["title"][0]["properties"]["text"]["expr"]["Literal"]["Value"]
        == "'X'"
    )


def test_list_include_fields(tmp_path: Path) -> None:
    defn = _make_report(tmp_path)
    res = _op_list({"include": ["fields"]}, defn)
    v = res["visuals"][0]
    assert "fields" in v
    assert "Data" in v["fields"]


def test_list_include_all(tmp_path: Path) -> None:
    defn = _make_report(tmp_path)
    res = _op_list({"include": ["all"]}, defn)
    v = res["visuals"][0]
    assert "objects" in v
    assert "visualContainerObjects" in v
    assert "fields" in v
    assert "parentGroupName" in v
