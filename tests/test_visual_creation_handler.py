"""Tests for server.handlers.visual_creation_handler — the canonical
create/delete/template source of truth.

Both 07_Visual_Operations and 11_PBIP_Authoring delegate create_visual /
delete_visual here, so this file pins the handler's own contract:

  - op_create_visual: validates page + visual_type, writes a visual.json
    under the resolved page folder (via the real pbir_executor.write_visual
    against a tmp_path fixture), returns {success, visual_id, page_id, path}.
  - op_create_visual_group: writes a visualGroup container.
  - op_delete_visual: validates page_id + visual_id, delegates to
    CloneEngine.delete_visual (removes files on disk, strips dangling refs),
    tags result with operation="delete_visual". delete_children removes the
    subtree.
  - op_list_templates / op_get_template: return the template catalog / a
    specific template; unknown / missing visual_type → clean error.

Deterministic + fast: no live PBI. Persistence is exercised through a real
minimal PBIR `definition/` tree under tmp_path. The lower-level cross-ref
cleanup + clone semantics already have dedicated coverage in
test_clone_engine.py — here we test THIS handler's wiring.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.handlers import visual_creation_handler as vch

# ---------------------------------------------------------------------------
# Fixture — minimal PBIR definition/ tree
# ---------------------------------------------------------------------------


def _write(p: Path, content) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(content, ensure_ascii=True, indent=2), encoding="utf-8")


def _load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


@pytest.fixture
def definition_path(tmp_path: Path) -> Path:
    """A 1-page report with one group + two member visuals.

    layout:
        definition/
          pages.json
          pages/page001/page.json
          pages/page001/visuals/{v-group, v-child1, v-child2}/visual.json
    """
    defn = tmp_path / "MyReport.Report" / "definition"
    pages_dir = defn / "pages"

    # H-03 / H-08 / H-41: real PBIR locations — pages/pages.json and
    # page.json ``visualInteractions`` (the old fixture used the invented
    # definition/pages.json + ``interactions`` shapes, so it proved nothing).
    _write(pages_dir / "pages.json", {"pageOrder": ["page001"]})
    _write(
        pages_dir / "page001" / "page.json",
        {
            "name": "page001",
            "displayName": "First Page",
            "visualInteractions": [
                {"source": "v-child1", "target": "v-child2", "type": "NoFilter"},
            ],
        },
    )
    _write(
        pages_dir / "page001" / "visuals" / "v-group" / "visual.json",
        {"name": "v-group", "visual": {"visualType": "visualGroup"}},
    )
    _write(
        pages_dir / "page001" / "visuals" / "v-child1" / "visual.json",
        {"name": "v-child1", "parentGroupName": "v-group", "visual": {"visualType": "cardVisual"}},
    )
    _write(
        pages_dir / "page001" / "visuals" / "v-child2" / "visual.json",
        {"name": "v-child2", "parentGroupName": "v-group", "visual": {"visualType": "cardVisual"}},
    )
    return defn


# ---------------------------------------------------------------------------
# op_create_visual
# ---------------------------------------------------------------------------


def test_create_visual_writes_visual_json(definition_path):
    result = vch.op_create_visual(
        {
            "page_id": "page001",
            "visual_type": "columnChart",
            "title": "My Chart",
            "position": {"x": 10, "y": 20, "width": 300, "height": 200},
        },
        definition_path,
    )
    assert result["success"] is True
    assert result["operation"] == "create_visual"
    assert result["visual_type"] == "columnChart"
    assert result["page_id"] == "page001"
    vid = result["visual_id"]
    assert vid

    # A visual.json was actually written under the page's visuals folder.
    vj = definition_path / "pages" / "page001" / "visuals" / vid / "visual.json"
    assert vj.exists()
    data = _load(vj)
    assert data["name"] == vid


def test_create_visual_coerces_string_position(definition_path):
    """Numeric position passed as strings must be coerced, not written literal."""
    result = vch.op_create_visual(
        {
            "page_id": "page001",
            "visual_type": "columnChart",
            "position": {"x": "100", "y": "50", "width": "300", "height": "200"},
        },
        definition_path,
    )
    assert result["success"] is True
    vj = definition_path / "pages" / "page001" / "visuals" / result["visual_id"] / "visual.json"
    pos = _load(vj)["position"]
    assert pos["x"] == 100
    assert isinstance(pos["x"], (int, float)) and not isinstance(pos["x"], str)


def test_create_visual_missing_visual_type_errors(definition_path):
    result = vch.op_create_visual({"page_id": "page001"}, definition_path)
    assert result["success"] is False
    assert "visual_type is required" in result["error"]


def test_create_visual_missing_page_errors(definition_path):
    result = vch.op_create_visual({"visual_type": "columnChart"}, definition_path)
    assert result["success"] is False
    assert "page_id or page_name is required" in result["error"]


def test_create_visual_unknown_page_errors(definition_path):
    result = vch.op_create_visual(
        {"page_id": "nope999", "visual_type": "columnChart"}, definition_path
    )
    assert result["success"] is False
    assert "Page not found" in result["error"]


def test_create_visual_accepts_page_name_alias(definition_path):
    """page_name is an accepted alias for page_id (resolves by display name)."""
    result = vch.op_create_visual(
        {"page_name": "First Page", "visual_type": "columnChart"}, definition_path
    )
    assert result["success"] is True
    assert result["page_id"] == "page001"


def test_create_visual_invalid_position_value_errors(definition_path):
    """A non-numeric position value surfaces a descriptive ValueError-derived error."""
    result = vch.op_create_visual(
        {
            "page_id": "page001",
            "visual_type": "columnChart",
            "position": {"x": "abc"},
        },
        definition_path,
    )
    assert result["success"] is False
    assert "position.x" in result["error"]


# ---------------------------------------------------------------------------
# op_create_visual_group
# ---------------------------------------------------------------------------


def test_create_visual_group_writes_container(definition_path):
    result = vch.op_create_visual_group(
        {
            "page_id": "page001",
            "group_name": "My Group",
            "position": {"x": 0, "y": 0, "width": 400, "height": 300},
        },
        definition_path,
    )
    assert result["success"] is True
    assert result["operation"] == "create_visual_group"
    assert result["group_name"] == "My Group"
    vid = result["visual_id"]
    vj = definition_path / "pages" / "page001" / "visuals" / vid / "visual.json"
    assert vj.exists()


def test_create_visual_group_missing_page_errors(definition_path):
    result = vch.op_create_visual_group({"group_name": "G"}, definition_path)
    assert result["success"] is False
    assert "page_id or page_name is required" in result["error"]


# ---------------------------------------------------------------------------
# op_delete_visual
# ---------------------------------------------------------------------------


def test_delete_visual_removes_files(definition_path):
    vj = definition_path / "pages" / "page001" / "visuals" / "v-child1"
    assert vj.exists()

    result = vch.op_delete_visual(
        {"page_id": "page001", "visual_id": "v-child1", "delete_children": False},
        definition_path,
    )
    assert result["success"] is True
    assert result["operation"] == "delete_visual"
    assert "v-child1" in result["deleted_visuals"]
    assert not vj.exists()


def test_delete_visual_group_with_children_removes_subtree(definition_path):
    visuals = definition_path / "pages" / "page001" / "visuals"
    result = vch.op_delete_visual(
        {"page_id": "page001", "visual_id": "v-group", "delete_children": True},
        definition_path,
    )
    assert result["success"] is True
    deleted = set(result["deleted_visuals"])
    assert {"v-group", "v-child1", "v-child2"} <= deleted
    assert not (visuals / "v-group").exists()
    assert not (visuals / "v-child1").exists()
    assert not (visuals / "v-child2").exists()


def test_delete_visual_strips_dangling_interactions(definition_path):
    """Deleting a visual referenced by an interaction prunes that interaction."""
    page_json = definition_path / "pages" / "page001" / "page.json"
    assert len(_load(page_json)["visualInteractions"]) == 1

    result = vch.op_delete_visual(
        {"page_id": "page001", "visual_id": "v-child1", "delete_children": False},
        definition_path,
    )
    assert result["success"] is True
    # The single interaction referenced v-child1 → must be gone now.
    assert _load(page_json).get("visualInteractions", []) == []


def test_delete_visual_missing_page_errors(definition_path):
    result = vch.op_delete_visual({"visual_id": "v-child1"}, definition_path)
    assert result["success"] is False
    assert "page_id or page_name is required" in result["error"]


def test_delete_visual_missing_visual_id_errors(definition_path):
    result = vch.op_delete_visual({"page_id": "page001"}, definition_path)
    assert result["success"] is False
    # H-25 added the visual_ids (multi-delete) form to the message.
    assert "visual_id (or visual_ids / visual_name) is required" in result["error"]


def test_delete_nonexistent_visual_errors(definition_path):
    result = vch.op_delete_visual(
        {"page_id": "page001", "visual_id": "does-not-exist"}, definition_path
    )
    assert result["success"] is False
    assert "Visual not found" in result["error"]


# ---------------------------------------------------------------------------
# op_list_templates / op_get_template
# ---------------------------------------------------------------------------


def test_list_templates_returns_catalog():
    result = vch.op_list_templates({}, None)
    assert result["success"] is True
    assert result["operation"] == "list_templates"
    assert result["count"] == len(result["templates"])
    assert result["count"] > 0
    # Catalog entries describe a visual type.
    types = {t.get("type") for t in result["templates"]}
    assert "columnChart" in types


def test_get_template_returns_specific_template():
    result = vch.op_get_template({"visual_type": "columnChart"}, None)
    assert result["success"] is True
    assert result["operation"] == "get_template"
    assert result["visual_type"] == "columnChart"
    # A built template is a complete visual.json skeleton.
    assert "name" in result["template"]


def test_get_template_missing_visual_type_errors():
    result = vch.op_get_template({}, None)
    assert result["success"] is False
    assert "visual_type is required" in result["error"]


def test_get_template_unknown_type_errors():
    result = vch.op_get_template({"visual_type": "notARealVisual"}, None)
    assert result["success"] is False
    assert "Unknown visual type" in result["error"]
    # Error lists available types to help the caller recover.
    assert "columnChart" in result["error"]
