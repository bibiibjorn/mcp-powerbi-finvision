"""T1-G: tests for bookmark get / update / group operations."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.pbip import bookmark_engine as eng


@pytest.fixture
def defn(tmp_path: Path) -> Path:
    """Create a minimal definition/ folder with two bookmarks."""
    defn = tmp_path / "Report.Report" / "definition"
    bdir = defn / "bookmarks"
    bdir.mkdir(parents=True)
    for bid, name in [("aaaaa", "Bookmark A"), ("bbbbb", "Bookmark B")]:
        (bdir / f"{bid}.bookmark.json").write_text(
            json.dumps(
                {
                    "$schema": "x",
                    "name": bid,
                    "displayName": name,
                    "options": {"captureData": True, "captureDisplay": True},
                    "explorationState": {"version": "1.0", "sections": {}},
                }
            ),
            encoding="utf-8",
        )
    return defn


def test_get_bookmark_returns_full_data(defn: Path) -> None:
    res = eng.get_bookmark(defn, "aaaaa")
    assert res["success"] is True
    assert res["bookmark_id"] == "aaaaa"
    assert res["data"]["displayName"] == "Bookmark A"


def test_get_bookmark_not_found(defn: Path) -> None:
    res = eng.get_bookmark(defn, "nope")
    assert res["success"] is False


def test_update_bookmark_renames_and_toggles_options(defn: Path) -> None:
    res = eng.update_bookmark(
        defn,
        "aaaaa",
        display_name="Bookmark A2",
        capture_data=False,
    )
    assert res["success"] is True
    assert set(res["changed"]) == {"displayName", "captureData"}
    # Confirm on disk.
    after = json.loads((defn / "bookmarks" / "aaaaa.bookmark.json").read_text(encoding="utf-8"))
    assert after["displayName"] == "Bookmark A2"
    # H-06 / real PBIR: "don't capture data" is options.suppressData = true
    # (the invented captureData key is migrated away).
    assert after["options"]["suppressData"] is True
    assert "captureData" not in after["options"]


def test_update_bookmark_no_op_when_nothing_changes(defn: Path) -> None:
    res = eng.update_bookmark(defn, "aaaaa")  # no kwargs
    assert res["success"] is True
    assert res["changed"] == []


def test_create_group(defn: Path) -> None:
    res = eng.create_bookmark_group(defn, "My Group", bookmark_ids=["aaaaa", "bbbbb"])
    assert res["success"] is True
    gid = res["group_id"]
    assert res["children"] == ["aaaaa", "bbbbb"]
    idx_path = defn / "bookmarks" / "bookmarks.json"
    assert idx_path.exists()
    idx = json.loads(idx_path.read_text(encoding="utf-8"))
    group = next(i for i in idx["items"] if i.get("name") == gid)
    assert group["displayName"] == "My Group"
    # H-06: real bookmarks.json group children are plain string ids.
    assert group["children"] == ["aaaaa", "bbbbb"]


def test_create_group_rejects_missing_bookmarks(defn: Path) -> None:
    res = eng.create_bookmark_group(defn, "X", bookmark_ids=["aaaaa", "nope"])
    assert res["success"] is False
    assert res["error_type"] == "bookmarks_not_found"
    assert res["missing"] == ["nope"]


def test_update_group_reorder_and_rename(defn: Path) -> None:
    created = eng.create_bookmark_group(defn, "G1", bookmark_ids=["aaaaa", "bbbbb"])
    gid = created["group_id"]
    res = eng.update_bookmark_group(defn, gid, display_name="G2", bookmark_ids=["bbbbb", "aaaaa"])
    assert res["success"] is True
    assert set(res["changed"]) == {"displayName", "children"}
    idx = json.loads((defn / "bookmarks" / "bookmarks.json").read_text(encoding="utf-8"))
    group = next(i for i in idx["items"] if i.get("name") == gid)
    assert group["displayName"] == "G2"
    assert group["children"] == ["bbbbb", "aaaaa"]


def test_delete_group_preserves_children_by_default(defn: Path) -> None:
    created = eng.create_bookmark_group(defn, "G1", bookmark_ids=["aaaaa", "bbbbb"])
    gid = created["group_id"]
    res = eng.delete_bookmark_group(defn, gid)
    assert res["success"] is True
    # Bookmark files still exist.
    assert (defn / "bookmarks" / "aaaaa.bookmark.json").exists()
    assert (defn / "bookmarks" / "bbbbb.bookmark.json").exists()
    # Index no longer has the group; children re-promoted to top level.
    idx = json.loads((defn / "bookmarks" / "bookmarks.json").read_text(encoding="utf-8"))
    assert not any(i.get("name") == gid for i in idx["items"])
    top_ids = {i.get("name") for i in idx["items"] if "children" not in i}
    assert {"aaaaa", "bbbbb"}.issubset(top_ids)


def test_delete_group_with_delete_children_removes_files(defn: Path) -> None:
    created = eng.create_bookmark_group(defn, "G1", bookmark_ids=["aaaaa"])
    gid = created["group_id"]
    res = eng.delete_bookmark_group(defn, gid, delete_children=True)
    assert res["success"] is True
    assert res["deleted_children"] == ["aaaaa"]
    assert not (defn / "bookmarks" / "aaaaa.bookmark.json").exists()
