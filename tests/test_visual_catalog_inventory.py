"""Tests for R0101 inventory walker + harvest."""

import json
from pathlib import Path

import pytest

from tools.visual_catalog import inventory as inv

FIXTURE = Path(__file__).parent / "fixtures" / "visual_catalog" / "report_mini"


def test_walk_collects_all_visuals():
    report = inv.walk_report(FIXTURE)
    assert len(report.visuals) == 2
    assert {v.visual_type for v in report.visuals} == {"cardVisual", "columnChart"}


def test_instance_has_page_and_id():
    report = inv.walk_report(FIXTURE)
    card = next(v for v in report.visuals if v.visual_type == "cardVisual")
    assert card.page_display_name == "KPI Overview"
    assert card.page_id == "aaa111"
    assert card.visual_id == "v001"


def test_group_by_type():
    grouped = inv.group_by_type(inv.walk_report(FIXTURE))
    assert "cardVisual" in grouped and "columnChart" in grouped
    assert len(grouped["cardVisual"]) == 1


def test_configured_containers_detected():
    report = inv.walk_report(FIXTURE)
    card = next(v for v in report.visuals if v.visual_type == "cardVisual")
    assert "background" in card.configured_containers


def test_missing_report_raises():
    with pytest.raises(FileNotFoundError):
        inv.walk_report(Path("/does/not/exist.Report"))


# ---------------------------------------------------------------------------
# visualGroup containers + isHidden (needed for page-absolute crop bounds)
# ---------------------------------------------------------------------------
def _write_page(tmp_path, page_id="p1"):
    page = tmp_path / "definition" / "pages" / page_id
    (page / "visuals").mkdir(parents=True)
    (page / "page.json").write_text(
        json.dumps({"name": page_id, "displayName": "P", "width": 1280, "height": 720}),
        encoding="utf-8",
    )
    return tmp_path


def _add_visual(
    root,
    vid,
    *,
    x=0,
    y=0,
    w=100,
    h=50,
    parent=None,
    hidden=False,
    group=False,
    vtype="cardVisual",
    page_id="p1",
):
    d = root / "definition" / "pages" / page_id / "visuals" / vid
    d.mkdir(parents=True)
    data = {"name": vid, "position": {"x": x, "y": y, "z": 0, "width": w, "height": h}}
    if group:
        data["visualGroup"] = {"displayName": vid, "groupMode": "ScaleMode"}
    else:
        data["visual"] = {"visualType": vtype}
    if parent:
        data["parentGroupName"] = parent
    if hidden:
        data["isHidden"] = True
    (d / "visual.json").write_text(json.dumps(data), encoding="utf-8")


def test_walk_collects_group_containers(tmp_path):
    root = _write_page(tmp_path)
    _add_visual(root, "g1", x=100, y=300, w=400, h=200, group=True)
    _add_visual(root, "c1", x=10, y=10, parent="g1")
    report = inv.walk_report(root)
    # Groups are indexed separately — NOT in .visuals (harvest/group_by_type unchanged).
    assert [v.visual_id for v in report.visuals] == ["c1"]
    groups = {g.group_id: g for g in report.groups}
    assert groups["g1"].position["x"] == 100.0
    assert groups["g1"].position["y"] == 300.0
    assert groups["g1"].page_id == "p1"
    assert groups["g1"].parent_group_id is None
    assert groups["g1"].is_hidden is False


def test_walk_marks_hidden(tmp_path):
    root = _write_page(tmp_path)
    _add_visual(root, "gh", x=0, y=0, group=True, hidden=True)
    _add_visual(root, "v1", x=5, y=5, hidden=True)
    _add_visual(root, "v2", x=5, y=80)
    report = inv.walk_report(root)
    by_id = {v.visual_id: v for v in report.visuals}
    assert by_id["v1"].is_hidden is True
    assert by_id["v2"].is_hidden is False
    assert next(g for g in report.groups if g.group_id == "gh").is_hidden is True


def test_harvest_r0101_writes_extracts_and_metadata(tmp_path):
    warnings = inv.harvest_r0101(
        report_root=FIXTURE,
        extracts_root=tmp_path,
        report_sha="test-sha",
    )
    assert warnings == []
    jsons = sorted(p.name for p in tmp_path.glob("*.json") if p.name != "_metadata.json")
    assert len(jsons) == 2
    assert any("KPI Overview__v001.json" == n for n in jsons)

    meta = json.loads((tmp_path / "_metadata.json").read_text(encoding="utf-8"))
    assert meta["report_sha"] == "test-sha"
    assert meta["visual_count"] == 2


def test_harvest_r0101_overwrites_mutated_extracts(tmp_path):
    inv.harvest_r0101(report_root=FIXTURE, extracts_root=tmp_path, report_sha="v1")
    victim = next(tmp_path.glob("KPI Overview__v001.json"))
    victim.write_text("{}", encoding="utf-8")
    inv.harvest_r0101(report_root=FIXTURE, extracts_root=tmp_path, report_sha="v2")
    restored = json.loads(victim.read_text(encoding="utf-8"))
    assert restored["visual"]["visualType"] == "cardVisual"
