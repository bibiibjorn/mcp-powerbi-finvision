"""Tests for core.pbip.authoring.pbir_add_visuals."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.pbip.authoring.pbir_add_visuals import (
    PbirAddVisualsError,
    _find_container_group,
    _patch_into_container,
    _preflight_roles,
    _resolve_page_dir,
    resolve_container,
)


@pytest.fixture
def mock_definition(tmp_path: Path) -> Path:
    """Build a minimal Report.Report/definition/pages/<page>/ fixture."""
    report = tmp_path / "Report.Report"
    definition = report / "definition"
    pages_dir = definition / "pages"
    page_dir = pages_dir / "abc123.Page"
    visuals_dir = page_dir / "visuals"
    visuals_dir.mkdir(parents=True)

    (page_dir / "page.json").write_text(
        json.dumps({"displayName": "Dashboard", "name": "abc123.Page"}),
        encoding="utf-8",
    )
    (pages_dir / "pages.json").write_text(
        json.dumps({"pageOrder": ["abc123.Page"], "activePageName": "abc123.Page"}),
        encoding="utf-8",
    )
    return definition


class TestPreflightRoles:
    def test_valid_roles_no_errors(self):
        visuals = [
            {"visual_type": "cardVisual", "fields": {"Data": "Sales.Revenue"}},
            {"visual_type": "lineChart", "fields": {"Category": "Date.Month", "Y": "Sales.Total"}},
        ]
        assert _preflight_roles(visuals) == []

    def test_invalid_role_flagged(self):
        visuals = [{"visual_type": "cardVisual", "fields": {"Values": "Sales.Revenue"}}]
        errs = _preflight_roles(visuals)
        assert len(errs) == 1
        assert "Values" in errs[0]

    def test_missing_visual_type_flagged(self):
        visuals = [{"fields": {"Data": "x"}}]
        errs = _preflight_roles(visuals)
        assert "missing 'visual_type'" in errs[0]

    def test_no_fields_is_ok(self):
        # Layout-only visuals (textbox, image) are valid with no bindings.
        visuals = [{"visual_type": "textbox"}]
        assert _preflight_roles(visuals) == []


class TestResolvePageDir:
    def test_by_folder_id(self, mock_definition: Path):
        page_dir = _resolve_page_dir(mock_definition, "abc123.Page")
        assert page_dir is not None
        assert page_dir.name == "abc123.Page"

    def test_by_display_name(self, mock_definition: Path):
        page_dir = _resolve_page_dir(mock_definition, "Dashboard")
        assert page_dir is not None
        assert page_dir.name == "abc123.Page"

    def test_not_found(self, mock_definition: Path):
        assert _resolve_page_dir(mock_definition, "NoSuchPage") is None

    def test_no_pages_dir(self, tmp_path: Path):
        assert _resolve_page_dir(tmp_path, "Anything") is None


class TestContainerHelpers:
    def test_find_container_by_display_name(self, mock_definition: Path):
        visuals_dir = mock_definition / "pages" / "abc123.Page" / "visuals"
        group_dir = visuals_dir / "group_xyz"
        group_dir.mkdir()
        (group_dir / "visual.json").write_text(
            json.dumps(
                {
                    "name": "group_xyz",
                    "visualGroup": {"displayName": "Visuals and graphs"},
                    "position": {"x": 15, "y": 161, "width": 1880, "height": 907},
                }
            ),
            encoding="utf-8",
        )
        assert _find_container_group(visuals_dir, "Visuals and graphs") == "group_xyz"

    def test_find_container_not_found(self, mock_definition: Path):
        visuals_dir = mock_definition / "pages" / "abc123.Page" / "visuals"
        assert _find_container_group(visuals_dir, "Missing") is None

    def test_patch_into_container_translates_coords(self, tmp_path: Path):
        vf = tmp_path / "new_visual"
        vf.mkdir()
        (vf / "visual.json").write_text(
            json.dumps(
                {
                    "name": "new_visual",
                    "position": {"x": 100, "y": 200, "width": 300, "height": 150},
                }
            ),
            encoding="utf-8",
        )
        _patch_into_container(vf, container_id="group_xyz", container_x=15, container_y=161)
        data = json.loads((vf / "visual.json").read_text(encoding="utf-8"))
        assert data["parentGroupName"] == "group_xyz"
        assert data["position"]["x"] == 85  # 100 - 15
        assert data["position"]["y"] == 39  # 200 - 161
        assert data["position"]["width"] == 300  # unchanged
        assert data["position"]["height"] == 150

    def test_patch_raises_when_visual_json_missing(self, tmp_path: Path):
        vf = tmp_path / "missing"
        vf.mkdir()
        with pytest.raises(PbirAddVisualsError):
            _patch_into_container(vf, "g", 0, 0)

    def test_resolve_container_returns_coords(self, mock_definition: Path):
        visuals_dir = mock_definition / "pages" / "abc123.Page" / "visuals"
        group_dir = visuals_dir / "g1"
        group_dir.mkdir()
        (group_dir / "visual.json").write_text(
            json.dumps(
                {
                    "visualGroup": {"displayName": "Grp"},
                    "position": {"x": 10, "y": 20, "width": 100, "height": 50},
                }
            ),
            encoding="utf-8",
        )
        cid, coords = resolve_container(mock_definition, "abc123.Page", "Grp")
        assert cid == "g1"
        # Page-absolute (top-level group: same as its own position) + raw local.
        assert {k: coords[k] for k in ("x", "y", "width", "height")} == {
            "x": 10,
            "y": 20,
            "width": 100,
            "height": 50,
        }
        assert coords["local"] == {"x": 10, "y": 20}
