"""Tests for CascadeRenameEngine (core/pbip/cascade_rename.py)."""

import json
from pathlib import Path


from core.pbip.cascade_rename import CascadeRenameEngine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_report(tmp_path: Path) -> Path:
    """Create a minimal .Report folder structure and return its path."""
    report = tmp_path / "MyReport.Report"
    (report / "definition").mkdir(parents=True)
    return report


def _write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_cascade_rename_visual_json(tmp_path):
    """Property value in visual.json is renamed."""
    report = _make_report(tmp_path)
    visual_json = report / "definition" / "pages" / "p1" / "visuals" / "v1" / "visual.json"
    original = {"query": {"Select": [{"Measure": {"Property": "Old Measure"}}]}}
    _write_json(visual_json, original)

    engine = CascadeRenameEngine(str(report))
    result = engine.apply_renames({"Old Measure": "New Measure"})

    assert result["success"] is True
    assert result["replacements"] > 0
    assert result["files_modified"] == 1

    data = _read_json(visual_json)
    assert data["query"]["Select"][0]["Measure"]["Property"] == "New Measure"


def test_cascade_rename_bracket_form(tmp_path):
    """Bracket-wrapped form [Old Measure] is replaced, [Other Measure] is not."""
    report = _make_report(tmp_path)
    visual_json = report / "definition" / "pages" / "p1" / "visuals" / "v1" / "visual.json"
    original = {"expression": "[Old Measure] / [Other Measure]"}
    _write_json(visual_json, original)

    engine = CascadeRenameEngine(str(report))
    result = engine.apply_renames({"Old Measure": "New Measure"})

    assert result["success"] is True
    data = _read_json(visual_json)
    assert data["expression"] == "[New Measure] / [Other Measure]"


def _measure_field(entity: str, prop: str) -> dict:
    return {"Measure": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def test_cascade_rename_bookmarks(tmp_path):
    """Measure FIELD references inside bookmark JSON are updated; display text is not.

    H-12: the old engine renamed any string equal to the old name (bookmark
    ``displayName`` / ``name`` bled); only structural refs are renamed now.
    """
    report = _make_report(tmp_path)
    bk_json = report / "definition" / "bookmarks" / "bk1.bookmark.json"
    original = {
        "name": "bk1",
        "displayName": "Old Measure",
        "explorationState": {
            "filters": {
                "byExpr": [
                    {"name": "f1", "expression": _measure_field("m Measures", "Old Measure")}
                ]
            }
        },
    }
    _write_json(bk_json, original)

    engine = CascadeRenameEngine(str(report))
    result = engine.apply_renames({"Old Measure": "New Measure"})

    assert result["success"] is True
    assert result["files_modified"] == 1
    data = _read_json(bk_json)
    expr = data["explorationState"]["filters"]["byExpr"][0]["expression"]
    assert expr["Measure"]["Property"] == "New Measure"
    assert data["displayName"] == "Old Measure"  # display text is never renamed


def test_cascade_rename_filters_json(tmp_path):
    """Report-level filter FIELD + DAX ``expression`` are renamed; the filter id is not."""
    report = _make_report(tmp_path)
    report_json = report / "definition" / "report.json"
    original = {
        "filterConfig": {
            "filters": [
                {
                    "name": "Old Measure",  # a filter id, not a model reference
                    "field": _measure_field("m Measures", "Old Measure"),
                    "expression": "[Old Measure] > 0",
                }
            ]
        }
    }
    _write_json(report_json, original)

    engine = CascadeRenameEngine(str(report))
    result = engine.apply_renames({"Old Measure": "New Measure"})

    assert result["success"] is True
    f = _read_json(report_json)["filterConfig"]["filters"][0]
    assert f["field"]["Measure"]["Property"] == "New Measure"
    assert "[New Measure]" in f["expression"]
    assert f["name"] == "Old Measure"


def test_cascade_rename_dry_run(tmp_path):
    """dry_run=True returns replacements>0 but does NOT modify files on disk."""
    report = _make_report(tmp_path)
    visual_json = report / "definition" / "pages" / "p1" / "visuals" / "v1" / "visual.json"
    original = {
        "name": "v1",
        "visual": {
            "query": {
                "queryState": {
                    "Y": {"projections": [{"field": _measure_field("m", "Old Measure")}]}
                }
            }
        },
    }
    _write_json(visual_json, original)
    original_text = visual_json.read_text(encoding="utf-8")

    engine = CascadeRenameEngine(str(report))
    result = engine.apply_renames({"Old Measure": "New Measure"}, dry_run=True)

    assert result["success"] is True
    assert result["replacements"] > 0
    assert result["dry_run"] is True
    # File must not have changed
    assert visual_json.read_text(encoding="utf-8") == original_text


def test_cascade_rename_missing_definition(tmp_path):
    """When definition/ doesn't exist, success=False with a friendly error."""
    report = tmp_path / "NoReport.Report"
    report.mkdir()
    # No definition/ folder

    engine = CascadeRenameEngine(str(report))
    result = engine.apply_renames({"Old Measure": "New Measure"})

    assert result["success"] is False
    assert result["errors"]
    assert "Definition directory not found" in result["errors"][0]


def test_cascade_rename_no_files_is_success(tmp_path):
    """Empty target list counts as success (nothing to do) — audit T72."""
    report = _make_report(tmp_path)
    # Make pages/ but no visual files
    (report / "definition" / "pages").mkdir()

    engine = CascadeRenameEngine(str(report))
    result = engine.apply_renames({"Old": "New"})

    assert result["success"] is True
    assert result["error_count"] == 0
    assert result["files_attempted"] == 0


def test_cascade_rename_writes_ascii_escaped_unicode(tmp_path):
    """Audit T73: unicode measure names must be escaped on disk so the
    pbir CLI's Latin-1 shell pipe does not corrupt them."""
    report = _make_report(tmp_path)
    visual_json = report / "definition" / "pages" / "p1" / "visuals" / "v1" / "visual.json"
    _write_json(visual_json, {"expression": "[Net Asset Value Δ (Simulation)]"})

    engine = CascadeRenameEngine(str(report))
    result = engine.apply_renames({"Net Asset Value Δ (Simulation)": "NAV Δ Sim 1"})

    assert result["success"] is True
    raw = visual_json.read_text(encoding="utf-8")
    # The Δ char must be ASCII-escaped (\u0394) on disk.
    assert "\\u0394" in raw
    # And no raw Δ byte sequence remains.
    assert "Δ" not in raw


def test_cascade_rename_no_double_count(tmp_path):
    """Audit T75: one structural field ref + one DAX bracket ref = exactly 2
    replacements, and a plain string equal to the old name is NOT counted
    (nor renamed — H-12: display text never bleeds)."""
    report = _make_report(tmp_path)
    bookmarks = report / "definition" / "bookmarks" / "bk1.bookmark.json"
    _write_json(
        bookmarks,
        {
            "displayName": "Old Measure",
            "field": _measure_field("m", "Old Measure"),
            "expression": "[Old Measure]",
        },
    )

    engine = CascadeRenameEngine(str(report))
    result = engine.apply_renames({"Old Measure": "New Measure"})

    assert result["success"] is True
    written = _read_json(bookmarks)
    assert written == {
        "displayName": "Old Measure",
        "field": _measure_field("m", "New Measure"),
        "expression": "[New Measure]",
    }
    assert result["replacements"] == 2


def test_cascade_rename_invalid_json_fallback(tmp_path):
    """Files with invalid JSON still have bracket-form renames applied via raw fallback."""
    report = _make_report(tmp_path)
    visual_json = report / "definition" / "pages" / "p1" / "visuals" / "v1" / "visual.json"
    visual_json.parent.mkdir(parents=True, exist_ok=True)
    # Write deliberately broken JSON that still contains the measure reference
    visual_json.write_text(
        '{ "expression": "[Old Measure]" broken json ]]]',
        encoding="utf-8",
    )

    engine = CascadeRenameEngine(str(report))
    result = engine.apply_renames({"Old Measure": "New Measure"})

    assert result["success"] is True
    assert result["replacements"] > 0
    content = visual_json.read_text(encoding="utf-8")
    assert "[New Measure]" in content
    assert "[Old Measure]" not in content
