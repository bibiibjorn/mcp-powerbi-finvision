"""Regression: CascadeRenameEngine must write PBIR JSON atomically.

Cascade-rename touches the largest fan-out of files of any writer (every
visual.json / page.json / bookmark / filters.json in the report). A crash,
AV-lock, or full disk mid-write must NOT leave a truncated file that PBI
Desktop refuses to open. Previously all 4 write sites used raw
``Path.write_text()``; they now route through the atomic temp+fsync+os.replace
helper (``save_json_minimal``) used by every other PBIR writer.
"""

import json
from pathlib import Path


from core.pbip.cascade_rename import CascadeRenameEngine


def _make_report(tmp_path: Path) -> Path:
    report = tmp_path / "MyReport.Report"
    (report / "definition").mkdir(parents=True)
    return report


def _write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def test_rename_leaves_no_tmp_file(tmp_path):
    report = _make_report(tmp_path)
    vj = report / "definition" / "pages" / "p1" / "visuals" / "v1" / "visual.json"
    _write_json(vj, {"query": {"Select": [{"Measure": {"Property": "Old"}}]}})

    result = CascadeRenameEngine(str(report)).apply_renames({"Old": "New"})
    assert result["success"] is True

    # Atomic writer cleans up its sibling .tmp on success.
    tmp_siblings = list(vj.parent.glob("*.tmp"))
    assert tmp_siblings == [], f"leftover temp files: {tmp_siblings}"
    assert (
        json.loads(vj.read_text(encoding="utf-8"))["query"]["Select"][0]["Measure"]["Property"]
        == "New"
    )


def test_crash_mid_write_preserves_original(tmp_path, monkeypatch):
    """If os.replace fails mid-write, the ORIGINAL file must be intact
    (not truncated) — the whole point of the atomic pattern."""
    report = _make_report(tmp_path)
    vj = report / "definition" / "pages" / "p1" / "visuals" / "v1" / "visual.json"
    original_obj = {"query": {"Select": [{"Measure": {"Property": "Old Measure"}}]}}
    _write_json(vj, original_obj)
    original_bytes = vj.read_bytes()

    import core.utilities.pbip_utils as pu

    def boom(src, dst):
        raise OSError("simulated crash during atomic replace")

    monkeypatch.setattr(pu.os, "replace", boom)

    # The rename attempt hits the failing replace; it must not corrupt vj.
    CascadeRenameEngine(str(report)).apply_renames({"Old Measure": "New Measure"})

    # Original content survives byte-for-byte; no partial/truncated write.
    assert vj.read_bytes() == original_bytes
    assert json.loads(vj.read_text(encoding="utf-8")) == original_obj


def test_typed_rename_is_atomic(tmp_path, monkeypatch):
    """apply_renames_typed (the second code path, lines ~312/329) is also atomic."""
    report = _make_report(tmp_path)
    vj = report / "definition" / "pages" / "p1" / "visuals" / "v1" / "visual.json"
    original_obj = {"Measure": {"Property": "Old Measure"}}
    _write_json(vj, original_obj)
    original_bytes = vj.read_bytes()

    import core.utilities.pbip_utils as pu

    monkeypatch.setattr(pu.os, "replace", lambda s, d: (_ for _ in ()).throw(OSError("crash")))

    CascadeRenameEngine(str(report)).apply_renames_typed(
        {"measure": {"Old Measure": "New Measure"}}
    )
    assert vj.read_bytes() == original_bytes


def test_locked_file_recorded_as_error_not_modified(tmp_path, monkeypatch):
    """A write failure (locked visual.json) must be recorded as an error with
    success=False and the file MUST NOT appear in files_modified/changed_files.
    Previously bookkeeping ran before the write, so a locked file was reported
    as successfully renamed."""
    report = _make_report(tmp_path)
    vj = report / "definition" / "pages" / "p1" / "visuals" / "v1" / "visual.json"
    _write_json(vj, {"query": {"Select": [{"Measure": {"Property": "Old"}}]}})

    import core.utilities.pbip_utils as pu

    monkeypatch.setattr(
        pu.os, "replace", lambda s, d: (_ for _ in ()).throw(OSError("locked by PBI Desktop"))
    )

    result = CascadeRenameEngine(str(report)).apply_renames({"Old": "New"})

    assert result["success"] is False
    assert result["files_modified"] == 0
    assert result["changed_files"] == []
    assert result["error_count"] >= 1
    assert any("visual.json" in e for e in result["errors"])


def test_typed_locked_file_recorded_as_error_not_modified(tmp_path, monkeypatch):
    """Same failure honesty for the typed code path."""
    report = _make_report(tmp_path)
    vj = report / "definition" / "pages" / "p1" / "visuals" / "v1" / "visual.json"
    _write_json(vj, {"Measure": {"Property": "Old Measure"}})

    import core.utilities.pbip_utils as pu

    monkeypatch.setattr(pu.os, "replace", lambda s, d: (_ for _ in ()).throw(OSError("locked")))

    result = CascadeRenameEngine(str(report)).apply_renames_typed(
        {"measure": {"Old Measure": "New Measure"}}
    )
    assert result["success"] is False
    assert result["files_modified"] == 0
    assert result["changed_files"] == []
    assert result["error_count"] >= 1


def test_dry_run_no_write_still_counts_and_succeeds(tmp_path):
    """dry_run performs no write, so it must still report the would-be
    modifications and success (no write means no failure to distinguish)."""
    report = _make_report(tmp_path)
    vj = report / "definition" / "pages" / "p1" / "visuals" / "v1" / "visual.json"
    _write_json(vj, {"query": {"Select": [{"Measure": {"Property": "Old"}}]}})
    original_bytes = vj.read_bytes()

    result = CascadeRenameEngine(str(report)).apply_renames({"Old": "New"}, dry_run=True)

    assert result["success"] is True
    assert result["files_modified"] == 1
    # File on disk is untouched by a dry run.
    assert vj.read_bytes() == original_bytes


def test_successful_write_counts_file_as_modified(tmp_path):
    """Sanity: when the write succeeds the file IS counted (regression guard so
    the moved bookkeeping didn't drop the happy path)."""
    report = _make_report(tmp_path)
    vj = report / "definition" / "pages" / "p1" / "visuals" / "v1" / "visual.json"
    _write_json(vj, {"query": {"Select": [{"Measure": {"Property": "Old"}}]}})

    result = CascadeRenameEngine(str(report)).apply_renames({"Old": "New"})

    assert result["success"] is True
    assert result["files_modified"] == 1
    assert len(result["changed_files"]) == 1
    assert (
        json.loads(vj.read_text(encoding="utf-8"))["query"]["Select"][0]["Measure"]["Property"]
        == "New"
    )
