"""Deep review 2026-09-22, slice H-06 — bookmarks on the real bookmarks.json shape.

Real ``bookmarks/bookmarks.json`` group ``children`` are plain STRINGS (R0201:
163, R0105: 334, zero objects). The old engine wrote ``[{"name": id}]``,
crashed with ``'str' object has no attribute 'get'`` on delete and unlinked
the file BEFORE updating the index (file gone, id still referenced).
Capture options are Desktop's ``suppress*`` keys; affected visuals are
``options.targetVisualNames``.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / "fixtures" / "pbir_golden" / "GoldenCo"
GROUP = "bg00000000000000grp1"
A1, A2, A3 = "bm0000000000000000a1", "bm0000000000000000a2", "bm0000000000000000a3"


@pytest.fixture
def report(tmp_path) -> Path:
    root = tmp_path / "GoldenCo"
    shutil.copytree(GOLDEN, root)
    return root / "RPT-Sales.Report"


def _bm_dir(report: Path) -> Path:
    return report / "definition" / "bookmarks"


def _index(report: Path) -> dict:
    return json.loads((_bm_dir(report) / "bookmarks.json").read_text(encoding="utf-8-sig"))


def _op(report: Path, **args):
    from server.handlers.bookmark_operations_handler import handle_bookmark_operations

    return handle_bookmark_operations(
        {"pbip_path": str(report), "confirm_destructive": True, **args}
    )


def _all_children(index: dict):
    return [c for i in index["items"] for c in (i.get("children") or [])]


def test_h06_delete_grouped_bookmark_updates_index_and_file(report):
    res = _op(report, operation="delete", bookmark_id=A1)
    assert res["success"], res
    idx = _index(report)
    group = next(i for i in idx["items"] if i["name"] == GROUP)
    assert group["children"] == [A2]
    assert all(isinstance(c, str) for c in _all_children(idx))
    assert not (_bm_dir(report) / f"{A1}.bookmark.json").exists()


def test_h06_delete_by_display_name(report):
    res = _op(report, operation="delete", bookmark_id="Details state")
    assert res["success"], res
    assert all(i.get("name") != A3 for i in _index(report)["items"])


def test_h06_create_group_writes_string_children(report):
    res = _op(report, operation="create_group", display_name="Pair", bookmark_ids=[A3])
    assert res["success"], res
    idx = _index(report)
    group = next(i for i in idx["items"] if i.get("displayName") == "Pair")
    assert group["children"] == [A3]
    # a bookmark belongs to one group only: the loose a3 item is gone
    assert [i for i in idx["items"] if i.get("name") == A3] == []


def test_h06_update_and_delete_group(report):
    res = _op(report, operation="update_group", group_id=GROUP, bookmark_ids=[A2, A3])
    assert res["success"], res
    group = next(i for i in _index(report)["items"] if i["name"] == GROUP)
    assert group["children"] == [A2, A3]
    res = _op(report, operation="delete_group", group_id=GROUP)
    assert res["success"], res
    idx = _index(report)
    assert all(i.get("name") != GROUP for i in idx["items"])
    # children survive as top-level items (delete_children defaults to false)
    names = {i.get("name") for i in idx["items"]}
    assert {A2, A3} <= names
    assert (_bm_dir(report) / f"{A2}.bookmark.json").exists()


def test_h06_set_capture_uses_suppress_keys(report):
    res = _op(report, operation="set_capture", bookmark_id=A1, capture_data=False)
    assert res["success"], res
    bm = json.loads((_bm_dir(report) / f"{A1}.bookmark.json").read_text(encoding="utf-8-sig"))
    assert bm["options"]["suppressData"] is True
    assert "captureData" not in bm["options"]


def test_h06_set_affected_visuals_writes_target_visual_names(report):
    res = _op(
        report,
        operation="set_affected_visuals",
        bookmark_id=A1,
        visual_ids=["vc01cardvisual000001"],
    )
    assert res["success"], res
    bm = json.loads((_bm_dir(report) / f"{A1}.bookmark.json").read_text(encoding="utf-8-sig"))
    assert bm["options"]["targetVisualNames"] == ["vc01cardvisual000001"]
    assert bm["options"]["applyOnlyToTargetVisuals"] is True


def test_h06_create_bookmark_registers_as_string_item(report):
    res = _op(report, operation="create", display_name="New view", page_name="Overview")
    assert res["success"], res
    new_id = res.get("bookmark_id") or res.get("name")
    idx = _index(report)
    assert {"name": new_id} in idx["items"] or any(i.get("name") == new_id for i in idx["items"])
    bm = json.loads((_bm_dir(report) / f"{new_id}.bookmark.json").read_text(encoding="utf-8-sig"))
    assert bm["explorationState"]["activeSection"] == "aa01overview00000001"
    assert bm["$schema"].endswith("/bookmark/2.1.0/schema.json")  # H-36: sibling schema
