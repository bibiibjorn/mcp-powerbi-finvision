"""Regression: create_bookmark registers in bookmarks.json index (P1).

PBIR uses bookmarks/bookmarks.json as an ordering index. create_bookmark wrote
the per-bookmark .bookmark.json but never added the bookmark to the index, so
Power BI Desktop could show it unordered or omit it. delete_bookmark must
likewise drop it from the index.
"""

import json
from pathlib import Path

from core.pbip.bookmark_engine import (
    create_bookmark,
    delete_bookmark,
    _bookmarks_index_file,
)


def _index_items(definition_path: Path):
    idx = _bookmarks_index_file(definition_path)
    if not idx.exists():
        return []
    return json.loads(idx.read_text(encoding="utf-8")).get("items", [])


def test_create_bookmark_appends_to_index(tmp_path):
    definition = tmp_path / "Report" / "definition"
    definition.mkdir(parents=True)

    result = create_bookmark(definition, display_name="My Bookmark")
    assert result["success"] is True
    bid = result["bookmark_id"]

    names = [it.get("name") for it in _index_items(definition)]
    assert bid in names, f"bookmark {bid} not registered in index; items={names}"


def test_delete_bookmark_removes_from_index(tmp_path):
    definition = tmp_path / "Report" / "definition"
    definition.mkdir(parents=True)

    bid = create_bookmark(definition, display_name="Temp")["bookmark_id"]
    assert bid in [it.get("name") for it in _index_items(definition)]

    delete_bookmark(definition, bid)
    assert bid not in [it.get("name") for it in _index_items(definition)]
