"""Tests for the live-rename -> PBIR cascade bridge."""

import json
from pathlib import Path


from core.operations.rename_cascade_pbir_bridge import apply_pbir_cascade


def _make_report(tmp_path: Path, visual_payload: dict) -> Path:
    root = tmp_path / "demo.Report"
    pages = root / "definition" / "pages"
    page_dir = pages / "page-1"
    (page_dir / "visuals" / "v1").mkdir(parents=True)
    (page_dir / "page.json").write_text(
        json.dumps({"name": "page-1", "displayName": "P1"}), encoding="utf-8"
    )
    (page_dir / "visuals" / "v1" / "visual.json").write_text(
        json.dumps(visual_payload), encoding="utf-8"
    )
    return root


def test_measure_rename_cascade_rewrites_visual(tmp_path):
    payload = {
        "visual": {
            "query": {
                "queryState": {
                    "Y": {"projections": [{"field": {"Measure": {"Property": "Sales"}}}]}
                }
            }
        }
    }
    report = _make_report(tmp_path, payload)
    result = apply_pbir_cascade(
        pbip_path=str(report.parent),
        kind="measure",
        old_name="Sales",
        new_name="Revenue",
    )
    assert result["success"] is True
    assert result["replacements"] >= 1
    new = json.loads(
        (report / "definition" / "pages" / "page-1" / "visuals" / "v1" / "visual.json").read_text(
            encoding="utf-8"
        )
    )
    text = json.dumps(new)
    assert "Revenue" in text
    assert '"Sales"' not in text


def test_column_rename_does_not_touch_measure_with_same_name(tmp_path):
    payload = {
        "visual": {
            "query": {
                "queryState": {
                    "Y": {
                        "projections": [
                            {"field": {"Measure": {"Property": "Foo"}}},
                            {"field": {"Column": {"Property": "Foo"}}},
                        ]
                    }
                }
            }
        }
    }
    report = _make_report(tmp_path, payload)
    apply_pbir_cascade(
        pbip_path=str(report.parent),
        kind="column",
        old_name="Foo",
        new_name="Bar",
    )
    text = (
        report / "definition" / "pages" / "page-1" / "visuals" / "v1" / "visual.json"
    ).read_text(encoding="utf-8")
    obj = json.loads(text)
    projections = obj["visual"]["query"]["queryState"]["Y"]["projections"]
    measure_proj = next(p for p in projections if "Measure" in p["field"])
    column_proj = next(p for p in projections if "Column" in p["field"])
    assert measure_proj["field"]["Measure"]["Property"] == "Foo"
    assert column_proj["field"]["Column"]["Property"] == "Bar"


def test_measure_bracket_form_in_dax_expression_only(tmp_path):
    """Bracket form `[Sales]` is renamed inside DAX text (a visual calculation's
    ``Expression``), but NOT inside arbitrary display strings such as a caption
    literal (H-12: the old engine rewrote any string containing ``[Sales]``)."""
    payload = {
        "visual": {
            "query": {
                "queryState": {
                    "Y": {
                        "projections": [
                            {
                                "field": {
                                    "NativeVisualCalculation": {
                                        "Language": "dax",
                                        "Expression": "RUNNINGSUM([Sales])",
                                        "Name": "Running",
                                    }
                                },
                                "queryRef": "Running",
                            }
                        ]
                    }
                }
            },
            "objects": {
                "general": [
                    {
                        "properties": {
                            "caption": {"expr": {"Literal": {"Value": "'Total: [Sales]'"}}}
                        }
                    }
                ]
            },
        }
    }
    report = _make_report(tmp_path, payload)
    apply_pbir_cascade(
        pbip_path=str(report.parent),
        kind="measure",
        old_name="Sales",
        new_name="Revenue",
    )
    data = json.loads(
        (report / "definition" / "pages" / "page-1" / "visuals" / "v1" / "visual.json").read_text(
            encoding="utf-8"
        )
    )
    calc = data["visual"]["query"]["queryState"]["Y"]["projections"][0]["field"]
    assert calc["NativeVisualCalculation"]["Expression"] == "RUNNINGSUM([Revenue])"
    caption = data["visual"]["objects"]["general"][0]["properties"]["caption"]
    assert caption["expr"]["Literal"]["Value"] == "'Total: [Sales]'"


def test_pbip_path_missing_returns_clean_error(tmp_path):
    result = apply_pbir_cascade(
        pbip_path=str(tmp_path / "nope"),
        kind="measure",
        old_name="A",
        new_name="B",
    )
    assert result["success"] is False
    assert "no .report" in result["error"].lower() or "not found" in result["error"].lower()


def test_unsupported_kind_rejected(tmp_path):
    report = _make_report(tmp_path, {"visual": {}})
    result = apply_pbir_cascade(
        pbip_path=str(report.parent),
        kind="bogus",
        old_name="A",
        new_name="B",
    )
    assert result["success"] is False
    assert "kind" in result["error"].lower() or "unsupported" in result["error"].lower()


def test_dry_run_does_not_write(tmp_path):
    payload = {
        "visual": {
            "query": {
                "queryState": {
                    "Y": {"projections": [{"field": {"Measure": {"Property": "Sales"}}}]}
                }
            }
        }
    }
    report = _make_report(tmp_path, payload)
    visual_path = report / "definition" / "pages" / "page-1" / "visuals" / "v1" / "visual.json"
    before = visual_path.read_text(encoding="utf-8")
    result = apply_pbir_cascade(
        pbip_path=str(report.parent),
        kind="measure",
        old_name="Sales",
        new_name="Revenue",
        dry_run=True,
    )
    assert result["success"] is True
    assert result["replacements"] >= 1
    after = visual_path.read_text(encoding="utf-8")
    assert before == after
