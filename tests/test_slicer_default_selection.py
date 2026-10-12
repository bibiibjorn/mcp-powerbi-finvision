"""Tests for clear_default_selection / set_default_selection ops."""

from __future__ import annotations

import json
from pathlib import Path


def _slicer_with_filter(period: str = "FEB 25") -> dict:
    return {
        "name": "v1",
        "position": {"x": 0, "y": 0, "width": 100, "height": 40, "z": 0},
        "visual": {
            "visualType": "slicer",
            "query": {
                "queryState": {
                    "Values": {
                        "projections": [
                            {
                                "field": {
                                    "Column": {
                                        "Expression": {"SourceRef": {"Entity": "d Period"}},
                                        "Property": "Period Name",
                                    }
                                },
                            }
                        ]
                    }
                }
            },
            "objects": {
                "general": [
                    {
                        "properties": {
                            "filter": {
                                "filter": {
                                    "From": [{"Name": "d", "Entity": "d Period"}],
                                    "Where": [
                                        {
                                            "Condition": {
                                                "In": {
                                                    "Expressions": [
                                                        {
                                                            "Column": {
                                                                "Expression": {
                                                                    "SourceRef": {"Source": "d"}
                                                                },
                                                                "Property": "Period Name",
                                                            }
                                                        }
                                                    ],
                                                    "Values": [
                                                        [{"Literal": {"Value": f"'{period}'"}}]
                                                    ],
                                                }
                                            }
                                        }
                                    ],
                                }
                            },
                            "selfFilterEnabled": {"expr": {"Literal": {"Value": "true"}}},
                        }
                    }
                ],
            },
            "visualContainerObjects": {
                "title": [
                    {
                        "properties": {
                            "text": {"expr": {"Literal": {"Value": "'Custom from Period'"}}}
                        }
                    }
                ]
            },
        },
    }


def _make_pbip(tmp_path: Path, slicer_payload: dict) -> Path:
    report = tmp_path / "R.Report"
    pages_dir = report / "definition" / "pages"
    page_dir = pages_dir / "p1.Page"
    visuals_dir = page_dir / "visuals" / slicer_payload["name"]
    visuals_dir.mkdir(parents=True)
    (page_dir / "page.json").write_text(
        json.dumps({"displayName": "Settings", "name": "p1.Page"}), encoding="utf-8"
    )
    (visuals_dir / "visual.json").write_text(json.dumps(slicer_payload), encoding="utf-8")
    return report


def test_clear_default_selection_removes_filter_keeps_siblings(tmp_path: Path):
    from server.handlers._internal.slicer_default_selection import _op_clear_default_selection
    from core.utilities.pbip_utils import find_definition_folder

    report = _make_pbip(tmp_path, _slicer_with_filter())
    definition = find_definition_folder(str(report))
    visuals_dir = definition / "pages" / "p1.Page" / "visuals" / "v1"

    result = _op_clear_default_selection(
        {"pbip_path": str(report), "page_name": "Settings", "visual_name": "v1"},
        definition,
    )
    assert result["success"] is True

    parsed = json.loads((visuals_dir / "visual.json").read_text("utf-8"))
    props = parsed["visual"]["objects"]["general"][0]["properties"]
    assert "filter" not in props
    assert "selfFilterEnabled" in props


def test_clear_default_selection_ambiguous_match_fails(tmp_path: Path):
    """Multiple slicers match identifier criteria; require visual_name."""
    from server.handlers._internal.slicer_default_selection import _op_clear_default_selection
    from core.utilities.pbip_utils import find_definition_folder

    report = tmp_path / "R.Report"
    pages_dir = report / "definition" / "pages"
    page_dir = pages_dir / "p1.Page"
    (page_dir / "page.json").parent.mkdir(parents=True)
    (page_dir / "page.json").write_text(
        json.dumps({"displayName": "Settings", "name": "p1.Page"}), encoding="utf-8"
    )
    for vid in ("v1", "v2"):
        s = _slicer_with_filter()
        s["name"] = vid
        vd = page_dir / "visuals" / vid
        vd.mkdir(parents=True)
        (vd / "visual.json").write_text(json.dumps(s), encoding="utf-8")
    definition = find_definition_folder(str(report))

    result = _op_clear_default_selection(
        {"pbip_path": str(report), "page_name": "Settings", "display_name": "Custom from Period"},
        definition,
    )
    assert result["success"] is False
    assert "ambiguous" in result["error"].lower()
    assert len(result["candidates"]) == 2


def _slicer_no_filter() -> dict:
    return {
        "name": "v1",
        "position": {"x": 0, "y": 0, "width": 100, "height": 40, "z": 0},
        "visual": {
            "visualType": "slicer",
            "query": {
                "queryState": {
                    "Values": {
                        "projections": [
                            {
                                "field": {
                                    "Column": {
                                        "Expression": {"SourceRef": {"Entity": "d Period"}},
                                        "Property": "Period Name",
                                    }
                                },
                            }
                        ]
                    }
                }
            },
            "objects": {"general": [{"properties": {}}]},
        },
    }


def test_set_default_selection_string_explicit(tmp_path: Path):
    from server.handlers._internal.slicer_default_selection import _op_set_default_selection
    from core.utilities.pbip_utils import find_definition_folder

    report = _make_pbip(tmp_path, _slicer_no_filter())
    definition = find_definition_folder(str(report))

    result = _op_set_default_selection(
        {
            "pbip_path": str(report),
            "page_name": "Settings",
            "visual_name": "v1",
            "values": ["FEB 25"],
            "value_type": "string",
        },
        definition,
    )
    assert result["success"] is True

    parsed = json.loads(
        (definition / "pages" / "p1.Page" / "visuals" / "v1" / "visual.json").read_text("utf-8")
    )
    where = parsed["visual"]["objects"]["general"][0]["properties"]["filter"]["filter"]["Where"]
    values = where[0]["Condition"]["In"]["Values"]
    assert values == [[{"Literal": {"Value": "'FEB 25'"}}]]


def test_set_default_selection_int_explicit(tmp_path: Path):
    from server.handlers._internal.slicer_default_selection import _op_set_default_selection
    from core.utilities.pbip_utils import find_definition_folder

    report = _make_pbip(tmp_path, _slicer_no_filter())
    definition = find_definition_folder(str(report))

    result = _op_set_default_selection(
        {
            "pbip_path": str(report),
            "page_name": "Settings",
            "visual_name": "v1",
            "values": [25],
            "value_type": "int",
        },
        definition,
    )
    assert result["success"] is True

    parsed = json.loads(
        (definition / "pages" / "p1.Page" / "visuals" / "v1" / "visual.json").read_text("utf-8")
    )
    where = parsed["visual"]["objects"]["general"][0]["properties"]["filter"]["filter"]["Where"]
    values = where[0]["Condition"]["In"]["Values"]
    assert values == [[{"Literal": {"Value": "25L"}}]]


def test_set_default_selection_multi_values_string(tmp_path: Path):
    from server.handlers._internal.slicer_default_selection import _op_set_default_selection
    from core.utilities.pbip_utils import find_definition_folder

    report = _make_pbip(tmp_path, _slicer_no_filter())
    definition = find_definition_folder(str(report))

    result = _op_set_default_selection(
        {
            "pbip_path": str(report),
            "page_name": "Settings",
            "visual_name": "v1",
            "values": ["FEB 25", "MAR 25"],
            "value_type": "string",
        },
        definition,
    )
    assert result["success"] is True

    parsed = json.loads(
        (definition / "pages" / "p1.Page" / "visuals" / "v1" / "visual.json").read_text("utf-8")
    )
    where = parsed["visual"]["objects"]["general"][0]["properties"]["filter"]["filter"]["Where"]
    values = where[0]["Condition"]["In"]["Values"]
    assert values == [
        [{"Literal": {"Value": "'FEB 25'"}}],
        [{"Literal": {"Value": "'MAR 25'"}}],
    ]


def test_set_default_selection_auto_detect_via_tmdl_int(tmp_path: Path):
    """When live DAX probe unavailable, fall back to TMDL parsing."""
    from server.handlers._internal.slicer_default_selection import _op_set_default_selection
    from core.utilities.pbip_utils import find_definition_folder

    report = _make_pbip(tmp_path, _slicer_no_filter())
    sm = report.parent / "R.SemanticModel" / "definition" / "tables"
    sm.mkdir(parents=True)
    (sm / "d Period.tmdl").write_text(
        "table 'd Period'\n\n"
        "\tcolumn 'Period Name'\n"
        "\t\tdataType: int64\n"
        "\t\tsourceColumn: 'Period Name'\n",
        encoding="utf-8",
    )

    definition = find_definition_folder(str(report))
    result = _op_set_default_selection(
        {
            "pbip_path": str(report),
            "page_name": "Settings",
            "visual_name": "v1",
            "values": [25],
        },
        definition,
    )
    assert result["success"] is True
    assert result["value_type"] == "int"


def test_set_default_selection_auto_detect_string_default_warns(tmp_path: Path):
    """No DAX probe, no TMDL → defaults to string and emits a warning."""
    from server.handlers._internal.slicer_default_selection import _op_set_default_selection
    from core.utilities.pbip_utils import find_definition_folder

    report = _make_pbip(tmp_path, _slicer_no_filter())
    definition = find_definition_folder(str(report))

    result = _op_set_default_selection(
        {
            "pbip_path": str(report),
            "page_name": "Settings",
            "visual_name": "v1",
            "values": ["FEB 25"],
        },
        definition,
    )
    assert result["success"] is True
    assert result["value_type"] == "string"
    assert "warning" in result
    assert "string" in result["warning"].lower()
