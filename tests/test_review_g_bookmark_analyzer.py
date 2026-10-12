"""Review G-11: bookmark analyzer reference extraction (07_Bookmark_Operations analyze).

PBIR action buttons store the target as ``bookmark: {"expr": {"Literal":
{"Value": "'<id>'"}}}``; the old matcher only accepted strings / ``{"name"}``
and extracted nothing (R0105: 793 bookmark keys -> 0 refs -> all 341
bookmarks "orphaned"). Bookmark-navigator group membership from
``bookmarks.json`` was ignored. Every test below fails on base ``07fd722``.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from core.pbip.pbip_bookmark_analyzer import PbipBookmarkAnalyzer

GOLDEN = Path(__file__).parent / "fixtures" / "pbir_golden" / "GoldenCo"
VIS = "RPT-Sales.Report/definition/pages/aa01overview00000001/visuals"
A1, A2, A3 = "bm0000000000000000a1", "bm0000000000000000a2", "bm0000000000000000a3"


@pytest.fixture
def proj(tmp_path) -> Path:
    root = tmp_path / "GoldenCo"
    shutil.copytree(GOLDEN, root)
    return root


def _orphans(report_dir: Path):
    res = PbipBookmarkAnalyzer().analyze_bookmarks(str(report_dir))
    return sorted(b["id"] for b in res["bookmarks"] if b["is_orphaned"]), res


def _edit(path: Path, mutate) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def test_golden_literal_expr_targets_and_navigator_are_references():
    orphans, res = _orphans(GOLDEN / "RPT-Sales.Report")
    assert orphans == [A3]
    assert res["summary"]["orphaned_count"] == 1
    page = next(p for p in res["pages"] if p["id"] == "aa01overview00000001")
    btn = next(b for b in page["navigation_buttons"] if b["type"] == "actionButton")
    assert btn["bookmark_refs"] == [A1]  # quotes stripped from "'<id>'"
    nav = next(b for b in page["navigation_buttons"] if b["type"] == "bookmarkNavigator")
    assert nav["bookmark_groups"] == ["bg00000000000000grp1"]


def test_navigator_group_members_count_even_without_a_button(proj):
    # remove the action button's target: a1 is still shown by the navigator's group
    def drop_target(d):
        for link in d["visual"]["visualContainerObjects"]["visualLink"]:
            link["properties"].pop("bookmark", None)

    _edit(proj / VIS / "vb01actionbutton0001" / "visual.json", drop_target)
    orphans, res = _orphans(proj / "RPT-Sales.Report")
    assert res["bookmark_groups"]["bg00000000000000grp1"] == [A1, A2]
    assert orphans == [A3]


def test_navigator_without_group_shows_every_bookmark(proj):
    def ungroup(d):
        d["visual"]["objects"]["bookmarks"][0]["properties"].pop("bookmarkGroup")

    _edit(proj / VIS / "vb02bookmarknav00001" / "visual.json", ungroup)
    orphans, res = _orphans(proj / "RPT-Sales.Report")
    assert orphans == []
    page = next(p for p in res["pages"] if p["id"] == "aa01overview00000001")
    nav = next(b for b in page["navigation_buttons"] if b["type"] == "bookmarkNavigator")
    assert nav["shows_all_bookmarks"] is True


def test_legacy_string_target_still_accepted():
    refs = PbipBookmarkAnalyzer()._find_bookmark_references(
        {"visual": {"objects": {"x": [{"properties": {"bookmark": "bmLegacy"}}]}}}
    )
    assert refs == ["bmLegacy"]
