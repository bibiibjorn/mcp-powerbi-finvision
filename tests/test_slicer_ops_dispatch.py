"""Tests for _extract_slicer_info (title text + position from visual.json).

The legacy ``handle_slicer_operations`` dispatcher (and its tests) was removed
as dead code (deep review C-03); the ops are reached through the visual /
page handlers.
"""

from __future__ import annotations

import json
from pathlib import Path

from server.handlers._internal.slicer_helpers import _extract_slicer_info

# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _slicer_payload(title: str | None, x: float, y: float) -> dict:
    visual: dict = {
        "visualType": "slicer",
        "query": {
            "queryState": {
                "Values": {
                    "projections": [
                        {
                            "field": {
                                "Column": {
                                    "Expression": {"SourceRef": {"Entity": "d Period"}},
                                    "Property": "Period Name",
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
        visual["visualContainerObjects"] = {
            "title": [{"properties": {"text": {"expr": {"Literal": {"Value": f"'{title}'"}}}}}]
        }
    return {
        "name": "vid",
        "position": {"x": x, "y": y, "width": 200, "height": 60, "z": 0},
        "visual": visual,
    }


# ---------------------------------------------------------------------------
# _extract_slicer_info: title + position extraction
# ---------------------------------------------------------------------------


def test_extract_includes_title_and_position(tmp_path: Path):
    f = tmp_path / "visual.json"
    f.write_text(json.dumps(_slicer_payload("My Title", 100, 200)), encoding="utf-8")
    info = _extract_slicer_info(json.loads(f.read_text("utf-8")), f)
    assert info is not None
    assert info["title"] == "My Title"
    assert info["position"] == {"x": 100, "y": 200, "width": 200, "height": 60, "z": 0}


def test_extract_no_title_block_returns_none_title(tmp_path: Path):
    f = tmp_path / "visual.json"
    f.write_text(json.dumps(_slicer_payload(None, 0, 0)), encoding="utf-8")
    info = _extract_slicer_info(json.loads(f.read_text("utf-8")), f)
    assert info is not None
    assert info["title"] is None
