"""End-to-end test: 07_Visual_Operations dispatches the new slicer ops."""

from __future__ import annotations

import json
from pathlib import Path

from server.handlers.visual_operations_handler import handle_visual_operations


def _build_pbip(tmp_path: Path) -> str:
    report = tmp_path / "R.Report"
    pages_dir = report / "definition" / "pages"
    page_dir = pages_dir / "p1.Page"
    visuals_dir = page_dir / "visuals" / "v1"
    visuals_dir.mkdir(parents=True)
    (page_dir / "page.json").write_text(
        json.dumps({"displayName": "Settings", "name": "p1.Page"}), encoding="utf-8"
    )
    (pages_dir / "pages.json").write_text(
        json.dumps({"pageOrder": ["p1.Page"], "activePageName": "p1.Page"}),
        encoding="utf-8",
    )
    (visuals_dir / "visual.json").write_text(
        json.dumps(
            {
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
                                                "Expression": {"SourceRef": {"Entity": "d Asset"}},
                                                "Property": "Sector",
                                            }
                                        },
                                    }
                                ]
                            }
                        }
                    },
                    "objects": {},
                    "visualContainerObjects": {
                        "title": [
                            {
                                "properties": {
                                    "text": {"expr": {"Literal": {"Value": "'Some Title'"}}}
                                }
                            }
                        ]
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    return str(report)


def test_audit_pane_collisions_via_visual_operations(tmp_path: Path):
    pbip = _build_pbip(tmp_path)
    result = handle_visual_operations(
        {
            "operation": "audit_pane_collisions",
            "pbip_path": pbip,
        }
    )
    assert result["success"] is True
    # Audit is read-only, so no reload hint should be attached.
    assert result.get("reload_required") is None or result.get("reload_required") is False


def test_set_default_selection_via_visual_operations(tmp_path: Path):
    pbip = _build_pbip(tmp_path)
    result = handle_visual_operations(
        {
            "operation": "set_default_selection",
            "pbip_path": pbip,
            "page_name": "Settings",
            "visual_name": "v1",
            "values": ["Tech"],
            "value_type": "string",
        }
    )
    assert result["success"] is True
    # Write op → reload hint should be present (gate_and_wrap behavior).
    assert result.get("reload_required") is True
