"""Tests for template picker."""

from pathlib import Path

from tools.visual_catalog.inventory import VisualInstance
from tools.visual_catalog import picker as pk


def _inst(vid: str, containers: list[str], cf: bool = False, roles: int = 1) -> VisualInstance:
    return VisualInstance(
        page_id="p",
        page_display_name="P",
        visual_id=vid,
        visual_type="cardVisual",
        position={"x": 0, "y": 0, "width": 100, "height": 100},
        parent_group_id=None,
        configured_containers=sorted(containers),
        cf_present=cf,
        role_bindings={"Data": roles},
        visual_json_path=Path(f"/fake/{vid}.json"),
    )


def test_always_includes_minimal():
    instances = [
        _inst("a", ["background", "title", "border", "dataLabels"]),
        _inst("b", ["background"]),
        _inst("c", ["background", "title"]),
    ]
    picked_ids = [p.visual_id for p in pk.pick_templates(instances, cap=4)]
    assert "b" in picked_ids


def test_includes_cf_instance():
    instances = [
        _inst("a", ["background"]),
        _inst("b", ["background", "dataLabels"], cf=True),
    ]
    picked_ids = [p.visual_id for p in pk.pick_templates(instances, cap=4)]
    assert "b" in picked_ids


def test_respects_cap():
    instances = [_inst(f"v{i}", [f"c{i}", f"d{i}", f"e{i}"]) for i in range(20)]
    assert len(pk.pick_templates(instances, cap=4)) == 4


def test_empty_input_returns_empty():
    assert pk.pick_templates([], cap=4) == []


def test_force_include_override():
    instances = [_inst(f"v{i}", ["background", "title", "border", "dataLabels"]) for i in range(10)]
    overrides = {"include": [{"page": "P", "visual_id": "v7"}]}
    picked_ids = {p.visual_id for p in pk.pick_templates(instances, cap=4, overrides=overrides)}
    assert "v7" in picked_ids


def test_force_exclude_override():
    instances = [_inst("a", ["background"]), _inst("b", ["background", "dataLabels"])]
    overrides = {"exclude": [{"page": "P", "visual_id": "a"}]}
    picked_ids = {p.visual_id for p in pk.pick_templates(instances, cap=4, overrides=overrides)}
    assert "a" not in picked_ids
