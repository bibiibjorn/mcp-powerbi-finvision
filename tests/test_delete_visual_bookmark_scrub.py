"""Regression: deleting a visual must scrub its id from every bookmark.

delete_visual cleaned visualInteractions / parentGroupName but left dangling
`visualContainers` / `visualContainerGroups` references in bookmark JSON — the
_remap_bookmarks rewrite existed but was only wired into clone_report. A
bookmark that still references a deleted visual can misapply state or trip PBI
on open. delete_visual now scrubs those refs.
"""

import json
from pathlib import Path

from core.pbip.authoring.clone_engine import CloneEngine


def _make_report(tmp_path: Path):
    definition = tmp_path / "Report.Report" / "definition"
    page = definition / "pages" / "p1" / "visuals"
    (page / "v_keep").mkdir(parents=True)
    (page / "v_del").mkdir(parents=True)
    (definition / "pages" / "p1" / "page.json").write_text('{"name":"p1"}', encoding="utf-8")
    (page / "v_keep" / "visual.json").write_text('{"name":"v_keep"}', encoding="utf-8")
    (page / "v_del" / "visual.json").write_text('{"name":"v_del"}', encoding="utf-8")

    bmarks = definition / "bookmarks"
    bmarks.mkdir(parents=True)
    bm = {
        "name": "bm1",
        "explorationState": {
            "activeSection": "p1",
            "sections": {
                "p1": {
                    "visualContainers": {
                        "v_keep": {"singleVisual": {}},
                        "v_del": {"singleVisual": {}},
                    }
                }
            },
        },
    }
    (bmarks / "bm1.bookmark.json").write_text(json.dumps(bm), encoding="utf-8")
    return definition


def test_delete_visual_scrubs_bookmark_reference(tmp_path):
    definition = _make_report(tmp_path)
    engine = CloneEngine()
    res = engine.delete_visual(definition, "p1", "v_del")
    assert res["success"] is True

    bm = json.loads((definition / "bookmarks" / "bm1.bookmark.json").read_text(encoding="utf-8"))
    containers = bm["explorationState"]["sections"]["p1"]["visualContainers"]
    assert "v_del" not in containers, "deleted visual still referenced in bookmark"
    assert "v_keep" in containers, "sibling visual reference wrongly removed"
