"""T1-H: tests for theme apply/export/import/clear_visual_overrides."""

from __future__ import annotations

import json
from pathlib import Path


from core.pbip import theme_engine as eng


def _make_report(tmp_path: Path, theme_data: dict | None = None) -> Path:
    """Build a minimal .Report/ tree with a theme registered."""
    report = tmp_path / "MyReport.Report"
    defn = report / "definition"
    defn.mkdir(parents=True)
    (defn / "report.json").write_text(
        json.dumps(
            {
                "themeCollection": {
                    "customTheme": {"name": "Theme.json", "type": "RegisteredResources"},
                },
                "publicCustomVisuals": [],
            }
        ),
        encoding="utf-8",
    )
    rr = report / "StaticResources" / "RegisteredResources"
    rr.mkdir(parents=True)
    (rr / "Theme.json").write_text(
        json.dumps(
            theme_data
            or {
                "name": "Original",
                "dataColors": ["#111", "#222"],
                "textClasses": {},
            }
        ),
        encoding="utf-8",
    )
    return report


def test_export_theme(tmp_path: Path) -> None:
    report = _make_report(tmp_path)
    out = tmp_path / "exported.json"
    res = eng.export_theme(report, out)
    assert res["success"] is True
    assert res["theme_name"] == "Original"
    assert out.exists()
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["name"] == "Original"


def test_apply_theme_overwrites_in_place(tmp_path: Path) -> None:
    report = _make_report(tmp_path)
    new_theme = {"name": "Brand2026", "dataColors": ["#abc"]}
    res = eng.apply_theme(report, new_theme)
    assert res["success"] is True
    on_disk = json.loads(
        (report / "StaticResources" / "RegisteredResources" / "Theme.json").read_text(
            encoding="utf-8"
        )
    )
    assert on_disk["name"] == "Brand2026"
    assert on_disk["dataColors"] == ["#abc"]


def test_apply_theme_rejects_non_dict(tmp_path: Path) -> None:
    report = _make_report(tmp_path)
    res = eng.apply_theme(report, "not a dict")  # type: ignore[arg-type]
    assert res["success"] is False


def test_import_theme_registers_and_repoints_report_json(tmp_path: Path) -> None:
    report = _make_report(tmp_path)
    src = tmp_path / "Imported.json"
    src.write_text(json.dumps({"name": "Imported", "dataColors": ["#001"]}), encoding="utf-8")
    res = eng.import_theme(report, src)
    assert res["success"] is True
    assert res["report_json_updated"] is True
    # New file landed under RegisteredResources.
    dest = report / "StaticResources" / "RegisteredResources" / "Imported.json"
    assert dest.exists()
    # report.json points at it.
    report_json = json.loads((report / "definition" / "report.json").read_text(encoding="utf-8"))
    assert report_json["themeCollection"]["customTheme"]["name"] == "Imported.json"


def test_clear_visual_overrides_removes_objects(tmp_path: Path) -> None:
    report = _make_report(tmp_path)
    page = report / "definition" / "pages" / "p001.Page"
    visuals_dir = page / "visuals"
    visuals_dir.mkdir(parents=True)
    v = visuals_dir / "v0001"
    v.mkdir()
    (v / "visual.json").write_text(
        json.dumps(
            {
                "visual": {
                    "visualType": "cardVisual",
                    "objects": {
                        "value": [{"properties": {"fontSize": "14D"}}],
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    res = eng.clear_visual_overrides(report, scope="all", keep_cf=False)
    assert res["success"] is True
    assert "v0001" in res["cleared"]
    saved = json.loads((v / "visual.json").read_text(encoding="utf-8"))
    assert saved["visual"]["objects"] == {}


def test_clear_visual_overrides_rejects_unknown_scope(tmp_path: Path) -> None:
    report = _make_report(tmp_path)
    (report / "definition" / "pages").mkdir(parents=True, exist_ok=True)
    res = eng.clear_visual_overrides(report, scope="bogus")
    assert res["success"] is False
    assert res["error_type"] == "invalid_args"
    # H-02: page / visual scopes are supported now; an unknown page is a
    # page_not_found error (not a silent report-wide clear).
    res = eng.clear_visual_overrides(report, scope="page:home")
    assert res["success"] is False
    assert res["error_type"] == "page_not_found"
