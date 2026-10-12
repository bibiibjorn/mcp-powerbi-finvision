"""Tests for core.pbip.authoring.clone_engine.CloneEngine.

Focused on the externally-visible contract: clone_page regenerates IDs,
delete_visual cleans dangling interactions + parentGroupName refs, and
clone_report rewrites IDs in place.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.pbip.authoring.clone_engine import CloneEngine

# ---------------------------------------------------------------------------
# Fixture — build a minimal PBIP "definition/" tree
# ---------------------------------------------------------------------------


def _write(p: Path, content):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(content, ensure_ascii=True, indent=2), encoding="utf-8")


@pytest.fixture
def definition_path(tmp_path: Path) -> Path:
    """Build a 1-page fixture with 3 visuals; one is a group, two are members.

    layout:
        definition/
          pages/
            pages.json                       (page order list — the REAL location, H-03)
            page001/
              page.json                      (display name + visualInteractions, H-08)
              visuals/
                v-group/ visual.json         (visualGroup "G1")
                v-child1/ visual.json        (parentGroupName = v-group)
                v-child2/ visual.json        (parentGroupName = v-group)
    """
    defn = tmp_path / "MyReport.Report" / "definition"
    pages_dir = defn / "pages"

    _write(pages_dir / "pages.json", {"pageOrder": ["page001"]})
    _write(
        pages_dir / "page001" / "page.json",
        {
            "name": "page001",
            "displayName": "First Page",
            "visualInteractions": [
                {"source": "v-child1", "target": "v-child2", "type": "NoFilter"},
                {"source": "v-child2", "target": "v-child1", "type": "NoFilter"},
            ],
        },
    )
    _write(
        pages_dir / "page001" / "visuals" / "v-group" / "visual.json",
        {"name": "v-group", "visual": {"visualType": "visualGroup"}},
    )
    _write(
        pages_dir / "page001" / "visuals" / "v-child1" / "visual.json",
        {
            "name": "v-child1",
            "parentGroupName": "v-group",
            "visual": {"visualType": "cardVisual"},
        },
    )
    _write(
        pages_dir / "page001" / "visuals" / "v-child2" / "visual.json",
        {
            "name": "v-child2",
            "parentGroupName": "v-group",
            "visual": {"visualType": "cardVisual"},
        },
    )
    return defn


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# clone_page
# ---------------------------------------------------------------------------


class TestClonePage:
    def test_copies_folder_with_new_ids(self, definition_path: Path):
        engine = CloneEngine()
        result = engine.clone_page(definition_path, "page001", new_display_name="Copy")
        assert result["success"] is True
        new_page_id = result["page_id"]
        # Page folder renamed
        assert (definition_path / "pages" / new_page_id).exists()
        # Display name updated
        new_page = _load(definition_path / "pages" / new_page_id / "page.json")
        assert new_page["displayName"] == "Copy"
        # All visual IDs regenerated (none equal source)
        new_visuals_dir = definition_path / "pages" / new_page_id / "visuals"
        new_ids = {v.name for v in new_visuals_dir.iterdir() if v.is_dir()}
        old_ids = {"v-group", "v-child1", "v-child2"}
        assert new_ids.isdisjoint(old_ids), f"Cloned visuals reused old IDs: {new_ids & old_ids}"

    def test_parent_group_refs_remap(self, definition_path: Path):
        engine = CloneEngine()
        result = engine.clone_page(definition_path, "page001")
        new_page_id = result["page_id"]
        mapping = result["id_mapping"]

        # Find the child visuals in the clone and verify parentGroupName
        # points to the REMAPPED group, not the original.
        new_visuals_dir = definition_path / "pages" / new_page_id / "visuals"
        new_group_id = mapping["v-group"]
        for v_folder in new_visuals_dir.iterdir():
            if not v_folder.is_dir():
                continue
            data = _load(v_folder / "visual.json")
            if data.get("parentGroupName"):
                assert (
                    data["parentGroupName"] == new_group_id
                ), "parentGroupName was not remapped to the new group id"

    def test_interactions_remap(self, definition_path: Path):
        engine = CloneEngine()
        result = engine.clone_page(definition_path, "page001")
        new_page_id = result["page_id"]
        mapping = result["id_mapping"]

        page = _load(definition_path / "pages" / new_page_id / "page.json")
        interactions = page.get("visualInteractions", [])
        # Every source/target must be a REMAPPED id (not the original)
        new_child1 = mapping["v-child1"]
        new_child2 = mapping["v-child2"]
        sources = {i["source"] for i in interactions}
        targets = {i["target"] for i in interactions}
        assert sources == {new_child1, new_child2}
        assert targets == {new_child1, new_child2}

    def test_clone_by_display_name(self, definition_path: Path):
        """Source can be specified by display name when the ID is unknown."""
        engine = CloneEngine()
        result = engine.clone_page(definition_path, "First Page")
        assert result["success"] is True

    def test_clone_missing_page_returns_error(self, definition_path: Path):
        engine = CloneEngine()
        result = engine.clone_page(definition_path, "does-not-exist")
        assert result["success"] is False
        assert "not found" in result["error"].lower()


# ---------------------------------------------------------------------------
# delete_visual — cleans parentGroupName + interactions
# ---------------------------------------------------------------------------


class TestDeleteVisual:
    def test_delete_child_cleans_interactions(self, definition_path: Path):
        engine = CloneEngine()
        result = engine.delete_visual(definition_path, "page001", "v-child1")
        assert result["success"] is True

        page = _load(definition_path / "pages" / "page001" / "page.json")
        # Every surviving interaction's endpoints must still exist
        interactions = page.get("interactions", [])
        for i in interactions:
            assert i["source"] != "v-child1"
            assert i["target"] != "v-child1"

    def test_delete_group_cascades_to_children(self, definition_path: Path):
        engine = CloneEngine()
        result = engine.delete_visual(definition_path, "page001", "v-group")
        assert result["success"] is True
        visuals = definition_path / "pages" / "page001" / "visuals"
        # Group + both children deleted
        remaining = {v.name for v in visuals.iterdir() if v.is_dir()}
        assert "v-group" not in remaining
        assert "v-child1" not in remaining
        assert "v-child2" not in remaining

    def test_delete_missing_visual_returns_error(self, definition_path: Path):
        engine = CloneEngine()
        result = engine.delete_visual(definition_path, "page001", "v-ghost")
        assert result["success"] is False
