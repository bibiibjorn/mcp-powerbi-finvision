"""Tests for markdown rendering."""

from pathlib import Path

from tools.visual_catalog.inventory import VisualInstance
from tools.visual_catalog.schema import (
    ContainerSurface,
    PropertySpec,
    RoleSpec,
    RolesSurface,
)
from tools.visual_catalog import render as rn


def _roles():
    return RolesSurface(
        visual_type="cardVisual",
        roles={"Data": RoleSpec("Data", True, True, ["Column", "Measure"])},
    )


def _surface():
    return [
        ContainerSurface(
            visual_type="cardVisual",
            container="value",
            properties=[
                PropertySpec("show", "boolean", "Show"),
                PropertySpec("color", "object", "Background fill color"),
            ],
        )
    ]


def _inst():
    return VisualInstance(
        page_id="p1",
        page_display_name="KPI Overview",
        visual_id="v001",
        visual_type="cardVisual",
        position={"x": 0, "y": 0, "width": 200, "height": 100},
        parent_group_id=None,
        configured_containers=["background"],
        cf_present=False,
        role_bindings={"Data": 1},
        visual_json_path=Path("/fake/v001.json"),
    )


def test_per_type_anchors_in_order():
    md = rn.render_type_file(
        visual_type="cardVisual",
        roles=_roles(),
        containers=_surface(),
        picked_templates=[],
        minimal_template_json="{}",
    )
    anchors = [
        "## Overview",
        "## Roles",
        "## Containers",
        "## Properties",
        "## Conditional formatting",
        "## Templates",
        "## Gotchas",
        "## Related",
    ]
    last = -1
    for a in anchors:
        pos = md.find(a)
        assert pos > last, f"anchor {a} out of order"
        last = pos


def test_manual_fences_present():
    md = rn.render_type_file(
        visual_type="cardVisual",
        roles=_roles(),
        containers=_surface(),
        picked_templates=[],
        minimal_template_json="{}",
    )
    for slug in ("overview", "gotchas"):
        assert f"<!-- MANUAL: {slug} -->" in md
    assert "<!-- /MANUAL -->" in md


def test_property_table_6_column_schema():
    md = rn.render_type_file(
        visual_type="cardVisual",
        roles=_roles(),
        containers=_surface(),
        picked_templates=[],
        minimal_template_json="{}",
    )
    assert "| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |" in md


def test_template_block_screenshot_missing():
    md = rn.render_type_file(
        visual_type="cardVisual",
        roles=_roles(),
        containers=_surface(),
        picked_templates=[(_inst(), '{"visual":{"visualType":"cardVisual"}}', False)],
        minimal_template_json="{}",
    )
    assert "⚠️ screenshot unavailable" in md


def test_template_block_screenshot_present():
    md = rn.render_type_file(
        visual_type="cardVisual",
        roles=_roles(),
        containers=_surface(),
        picked_templates=[(_inst(), '{"visual":{"visualType":"cardVisual"}}', True)],
        minimal_template_json="{}",
    )
    assert "./_extracts/KPI Overview__v001.png" in md


def test_index_lists_all_in_scope_types():
    md = rn.render_index(usage_counts={"cardVisual": 12, "columnChart": 5}, roles_by_type={})
    assert "cardVisual" in md and "columnChart" in md
    assert "12" in md and "5" in md


def test_index_shows_zero_for_unused_types():
    md = rn.render_index(usage_counts={}, roles_by_type={})
    assert "cardVisual" in md
    assert "| 0 |" in md


def test_shared_container_has_stable_heading():
    md = rn.render_shared_container("background", properties=[])
    assert md.startswith("# Shared container: background")
    assert "<!-- MANUAL:" in md


def test_shared_convention_has_heading():
    md = rn.render_shared_convention("conditional-formatting")
    assert "conditional-formatting" in md.lower() or "Conditional formatting" in md
    assert "<!-- MANUAL:" in md
