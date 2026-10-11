"""T1-C: tests for 07_Visual_Operations:get_roles_for_type.

Verifies the op returns the canonical role list from VISUAL_ROLES and, when
a same-type visual exists in the report on disk, an example visual id and
its used role names.
"""

from __future__ import annotations

import json
from pathlib import Path


from server.handlers.visual_layout_handler import _op_get_roles_for_type


def _make_definition(tmp_path: Path) -> Path:
    """Build a minimal Report.Report/definition tree with one cardVisual."""
    defn = tmp_path / "Report.Report" / "definition"
    page = defn / "pages" / "abcd1234.Page"
    visuals = page / "visuals"
    visuals.mkdir(parents=True)
    v = visuals / "v0001"
    v.mkdir()
    (v / "visual.json").write_text(
        json.dumps(
            {
                "visual": {
                    "visualType": "cardVisual",
                    "query": {
                        "queryState": {
                            "Data": {"projections": []},
                        }
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    return defn


def test_get_roles_known_type(tmp_path: Path) -> None:
    defn = _make_definition(tmp_path)
    res = _op_get_roles_for_type({"visual_type": "cardVisual"}, defn)
    assert res["success"] is True
    assert res["known_in_registry"] is True
    assert "Data" in res["canonical_roles"]
    assert res["example_visual_id"] == "v0001"
    assert res["example_used_roles"] == ["Data"]


def test_get_roles_unknown_type_returns_known_list(tmp_path: Path) -> None:
    defn = _make_definition(tmp_path)
    res = _op_get_roles_for_type({"visual_type": "notRealVisual"}, defn)
    assert res["success"] is True
    assert res["known_in_registry"] is False
    assert res["canonical_roles"] is None
    # Caller can self-correct.
    assert "cardVisual" in res["known_types"]


def test_get_roles_requires_visual_type(tmp_path: Path) -> None:
    defn = _make_definition(tmp_path)
    res = _op_get_roles_for_type({}, defn)
    assert res["success"] is False
    assert res["error_type"] == "invalid_args"


def test_get_roles_no_example_when_no_visuals_of_type(tmp_path: Path) -> None:
    defn = _make_definition(tmp_path)  # only cardVisual exists
    res = _op_get_roles_for_type({"visual_type": "lineChart"}, defn)
    assert res["success"] is True
    assert res["known_in_registry"] is True
    assert "Category" in res["canonical_roles"]
    assert "example_visual_id" not in res
