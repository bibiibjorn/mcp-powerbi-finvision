"""Tests for find_page_folder + resolve_pbip_paths in pbip_utils."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.utilities.pbip_utils import (
    PbipPaths,
    find_page_folder,
    resolve_pbip_paths,
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Build a realistic PBIP project skeleton:

    proj/
      MyReport.pbip
      MyReport.Report/
        definition/
          report.json
          pages/
            pages.json
            abc123/page.json    (display: Dashboard)
            def456/page.json    (display: Sales Detail)
            ghi789/page.json    (display: Sales Summary)
      MyReport.SemanticModel/
        definition.tmdl
    """
    proj = tmp_path / "proj"
    proj.mkdir()

    pbip_file = proj / "MyReport.pbip"
    pbip_file.write_text("{}", encoding="utf-8")

    report = proj / "MyReport.Report"
    definition = report / "definition"
    pages = definition / "pages"
    pages.mkdir(parents=True)

    (definition / "report.json").write_text("{}", encoding="utf-8")
    (pages / "pages.json").write_text(
        json.dumps({"pageOrder": ["abc123", "def456", "ghi789"]}),
        encoding="utf-8",
    )

    for folder, name in [
        ("abc123", "Dashboard"),
        ("def456", "Sales Detail"),
        ("ghi789", "Sales Summary"),
    ]:
        page_dir = pages / folder
        page_dir.mkdir()
        (page_dir / "page.json").write_text(
            json.dumps({"displayName": name, "name": folder}), encoding="utf-8"
        )

    sm = proj / "MyReport.SemanticModel"
    sm.mkdir()
    (sm / "definition.tmdl").write_text("model { }", encoding="utf-8")

    return proj


# ---------------------------------------------------------------------------
# find_page_folder
# ---------------------------------------------------------------------------


def test_find_by_exact_folder_id(project: Path):
    definition = project / "MyReport.Report" / "definition"
    folder = find_page_folder(definition, "abc123")
    assert folder is not None and folder.name == "abc123"


def test_find_by_exact_display_name(project: Path):
    definition = project / "MyReport.Report" / "definition"
    folder = find_page_folder(definition, "Dashboard")
    assert folder is not None and folder.name == "abc123"


def test_find_by_display_name_case_insensitive(project: Path):
    definition = project / "MyReport.Report" / "definition"
    folder = find_page_folder(definition, "dashboard")
    assert folder is not None and folder.name == "abc123"


def test_find_unique_startswith(project: Path):
    """When only one display name starts with the prefix, return it."""
    definition = project / "MyReport.Report" / "definition"
    # Only "Dashboard" starts with "Dash"
    folder = find_page_folder(definition, "Dash")
    assert folder is not None and folder.name == "abc123"


def test_find_ambiguous_startswith_returns_none(project: Path):
    """Multiple pages starting with the prefix => None (no silent wrong match)."""
    definition = project / "MyReport.Report" / "definition"
    # "Sales Detail" AND "Sales Summary" both start with "Sales"
    assert find_page_folder(definition, "Sales") is None


def test_find_no_substring_match(project: Path):
    """Substring fallback was the audit's R01-matches-R0101 bug — must NOT match."""
    definition = project / "MyReport.Report" / "definition"
    # "Detail" appears IN "Sales Detail" but is not a startswith
    assert find_page_folder(definition, "Detail") is None


def test_find_strips_dot_page_suffix(project: Path):
    """The pbir CLI uses '<id>.Page' as the page arg; resolver tolerates it."""
    definition = project / "MyReport.Report" / "definition"
    folder = find_page_folder(definition, "abc123.Page")
    assert folder is not None and folder.name == "abc123"


def test_find_unknown_returns_none(project: Path):
    definition = project / "MyReport.Report" / "definition"
    assert find_page_folder(definition, "NoSuchPage") is None


def test_find_no_pages_dir(tmp_path: Path):
    assert find_page_folder(tmp_path, "anything") is None


def test_find_disable_startswith(project: Path):
    """Caller can opt out of the unique-startswith fallback."""
    definition = project / "MyReport.Report" / "definition"
    assert find_page_folder(definition, "Dash", allow_unique_startswith=False) is None


# ---------------------------------------------------------------------------
# resolve_pbip_paths
# ---------------------------------------------------------------------------


def test_resolve_from_pbip_file(project: Path):
    paths = resolve_pbip_paths(str(project / "MyReport.pbip"))
    assert isinstance(paths, PbipPaths)
    assert paths.pbip_file is not None and paths.pbip_file.name == "MyReport.pbip"
    assert paths.report_folder is not None and paths.report_folder.name.endswith(".Report")
    assert paths.definition_path.name == "definition"
    assert paths.pages_folder.name == "pages"
    assert paths.semantic_model_folder is not None and paths.semantic_model_folder.name.endswith(
        ".SemanticModel"
    )


def test_resolve_from_report_folder(project: Path):
    paths = resolve_pbip_paths(str(project / "MyReport.Report"))
    assert paths.definition_path.exists()
    assert paths.report_folder is not None
    assert paths.semantic_model_folder is not None


def test_resolve_from_definition_folder(project: Path):
    paths = resolve_pbip_paths(str(project / "MyReport.Report" / "definition"))
    assert paths.definition_path.exists()
    # We can still walk back up to the report folder.
    assert paths.report_folder is not None


def test_resolve_from_project_root(project: Path):
    paths = resolve_pbip_paths(str(project))
    assert paths.definition_path.exists()


def test_resolve_invalid_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        resolve_pbip_paths(str(tmp_path))
