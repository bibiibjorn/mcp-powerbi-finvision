"""Regression: PBIR cascade rename includes per-visual mobile.json (P1).

Reports with mobile layouts store a mobile.json alongside each visual.json. It
can embed field/measure name references; _collect_target_files omitted it, so a
measure rename left mobile-layout references stale.
"""

import json
from pathlib import Path

from core.pbip.cascade_rename import CascadeRenameEngine


def _make_report(tmp_path: Path) -> Path:
    report = tmp_path / "MyReport.Report"
    (report / "definition").mkdir(parents=True)
    return report


def test_mobile_json_is_renamed(tmp_path):
    report = _make_report(tmp_path)
    vdir = report / "definition" / "pages" / "p1" / "visuals" / "v1"
    visual_json = vdir / "visual.json"
    mobile_json = vdir / "mobile.json"
    visual_json.parent.mkdir(parents=True, exist_ok=True)
    visual_json.write_text(
        json.dumps({"query": {"Select": [{"Measure": {"Property": "Old Measure"}}]}}),
        encoding="utf-8",
    )
    mobile_json.write_text(
        json.dumps({"query": {"Select": [{"Measure": {"Property": "Old Measure"}}]}}),
        encoding="utf-8",
    )

    engine = CascadeRenameEngine(str(report))
    result = engine.apply_renames({"Old Measure": "New Measure"})

    assert result["success"] is True
    mobile = json.loads(mobile_json.read_text(encoding="utf-8"))
    assert (
        mobile["query"]["Select"][0]["Measure"]["Property"] == "New Measure"
    ), "mobile.json reference was not cascaded"
