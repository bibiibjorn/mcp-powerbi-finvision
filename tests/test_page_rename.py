"""S-33: ``07_Page_Operations rename`` sets a page's DISPLAY name (the page id is unchanged)."""

from __future__ import annotations

import json

import pytest

from server.handlers import page_operations_handler as poh


@pytest.fixture(autouse=True)
def _gate_off(monkeypatch):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "OFF")
    from core.autonomous.gating import invalidate_policy_cache

    invalidate_policy_cache()


@pytest.fixture
def project(tmp_path):
    report = tmp_path / "Demo.Report"
    for pid, name in (("abc123", "Overview"), ("def456", "Detail")):
        folder = report / "definition" / "pages" / pid
        folder.mkdir(parents=True)
        (folder / "page.json").write_text(
            json.dumps({"name": pid, "displayName": name, "width": 1280, "height": 720}),
            encoding="utf-8",
        )
    return report


def _display(project, pid):
    path = project / "definition" / "pages" / pid / "page.json"
    return json.loads(path.read_text(encoding="utf-8"))["displayName"]


def _rename(project, **kw):
    return poh.handle_page_operations({"pbip_path": str(project), "operation": "rename", **kw})


def test_rename_changes_the_display_name_only(project):
    out = _rename(project, page_name="Overview", new_display_name="Executive Summary")
    assert out["success"] is True
    assert out["page_id"] == "abc123" and out["page_id_unchanged"] is True
    assert out["old_display_name"] == "Overview"
    assert out["new_display_name"] == "Executive Summary"
    assert out.get("reload_required") is True
    assert _display(project, "abc123") == "Executive Summary"
    assert (project / "definition" / "pages" / "abc123").is_dir()  # folder (= id) untouched


def test_rename_by_page_id(project):
    out = _rename(project, page_name="def456", new_display_name="Drill")
    assert out["success"] is True and _display(project, "def456") == "Drill"


def test_dry_run_writes_nothing_and_reports_the_planned_change(project):
    out = _rename(project, page_name="Overview", new_display_name="Preview", dry_run=True)
    assert out["success"] is True and out["dry_run"] is True
    assert out["files_modified"] == 0 and out["would_change_count"] == 1
    assert "reload_required" not in out
    assert _display(project, "abc123") == "Overview"


def test_duplicate_display_name_is_refused(project):
    out = _rename(project, page_name="Overview", new_display_name="Detail")
    assert out["success"] is False and out["error_type"] == "duplicate_name"
    assert _display(project, "abc123") == "Overview"


def test_unchanged_name_is_a_noop_success(project):
    out = _rename(project, page_name="Overview", new_display_name="Overview")
    assert out["success"] is True and out["unchanged"] is True


def test_missing_and_invalid_arguments(project):
    assert _rename(project, new_display_name="X")["error_type"] == "missing_argument"
    assert _rename(project, page_name="Overview")["error_type"] == "missing_argument"
    too_long = _rename(project, page_name="Overview", new_display_name="x" * 300)
    assert too_long["error_type"] == "invalid_argument"
    assert _rename(project, page_name="Nope", new_display_name="X")["success"] is False


def test_rename_is_registered_in_the_dispatch_table():
    assert "rename" in poh._PAGE_OPS
