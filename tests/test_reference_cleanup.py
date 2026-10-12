import json
from core.pbip.authoring.reference_cleanup import (
    strip_dangling_interactions,
    strip_dangling_visual_refs,
)


def test_strip_dangling_interactions_removes_dead_targets():
    # H-08: real page.json key is visualInteractions.
    page_json = {
        "visualInteractions": [
            {"source": "aaa", "target": "bbb", "type": 2},
            {"source": "aaa", "target": "zzz", "type": 2},
        ]
    }
    result = strip_dangling_interactions(page_json, live_ids={"aaa", "bbb"})
    assert len(result["visualInteractions"]) == 1
    assert result["visualInteractions"][0]["target"] == "bbb"


def test_strip_dangling_interactions_migrates_legacy_key():
    page_json = {"interactions": [{"source": "aaa", "target": "zzz", "type": "NoFilter"}]}
    result = strip_dangling_interactions(page_json, live_ids={"aaa"})
    assert "interactions" not in result
    assert "visualInteractions" not in result  # nothing left


def test_strip_dangling_interactions_removes_dead_sources():
    page_json = {
        "visualInteractions": [
            {"source": "zzz", "target": "bbb", "type": 2},
        ]
    }
    result = strip_dangling_interactions(page_json, live_ids={"bbb"})
    assert result.get("visualInteractions", []) == []


def test_strip_dangling_interactions_no_key():
    page_json = {"displayName": "test"}
    result = strip_dangling_interactions(page_json, live_ids={"aaa"})
    assert "interactions" not in result


def test_strip_dangling_visual_refs_clears_parent_group(tmp_path):
    visuals_dir = tmp_path / "visuals"
    visuals_dir.mkdir()
    v1 = visuals_dir / "aaa"
    v1.mkdir()
    (v1 / "visual.json").write_text(
        json.dumps({"name": "aaa", "parentGroupName": "zzz"}), encoding="utf-8"
    )
    strip_dangling_visual_refs(visuals_dir, live_ids={"aaa"})
    data = json.loads((v1 / "visual.json").read_text(encoding="utf-8"))
    assert "parentGroupName" not in data


def test_strip_dangling_visual_refs_keeps_valid_parent(tmp_path):
    visuals_dir = tmp_path / "visuals"
    visuals_dir.mkdir()
    v1 = visuals_dir / "aaa"
    v1.mkdir()
    (v1 / "visual.json").write_text(
        json.dumps({"name": "aaa", "parentGroupName": "grp1"}), encoding="utf-8"
    )
    strip_dangling_visual_refs(visuals_dir, live_ids={"aaa", "grp1"})
    data = json.loads((v1 / "visual.json").read_text(encoding="utf-8"))
    assert data["parentGroupName"] == "grp1"
