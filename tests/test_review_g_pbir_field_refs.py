"""Review G-09: report-side field references (PBIR report analyzer + whole-JSON walker).

The pre-review analyzer read visual filters from ``visual.filters`` (no PBIR
visual has it -- they are in the TOP-LEVEL ``filterConfig.filters``), used a
hard-coded projection-role list (Deneb ``dataset`` missed), ignored
``sortDefinition`` / ``HierarchyLevel`` / conditional-formatting objects /
bookmark states, and never resolved ``SourceRef.Source`` aliases. Every test
below fails on base ``07fd722``. Report content is synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.pbip.pbip_report_analyzer import PbirReportAnalyzer

GOLDEN = Path(__file__).parent / "fixtures" / "pbir_golden" / "GoldenCo"


def _col(table, prop, alias=None):
    src = {"Source": alias} if alias else {"Entity": table}
    return {"Column": {"Expression": {"SourceRef": src}, "Property": prop}}


def _meas(table, prop):
    return {"Measure": {"Expression": {"SourceRef": {"Entity": table}}, "Property": prop}}


_VISUAL = {
    "name": "vdeneb000000000001",
    "position": {"x": 0, "y": 0, "width": 100, "height": 100},
    "visual": {
        "visualType": "deneb7E15AEF80B9E4D4F8E12924291ECE89A",
        "query": {
            "queryState": {
                "dataset": {
                    "projections": [
                        {"field": _col("Dim", "Category"), "queryRef": "Dim.Category"},
                        {"field": _meas("M", "Deneb Measure"), "queryRef": "M.Deneb Measure"},
                    ]
                },
                "Rows": {
                    "projections": [
                        {
                            "field": {
                                "HierarchyLevel": {
                                    "Expression": {
                                        "Hierarchy": {
                                            "Expression": {"SourceRef": {"Entity": "Dim"}},
                                            "Hierarchy": "Calendar",
                                        }
                                    },
                                    "Level": "Year",
                                }
                            },
                            "queryRef": "Dim.Calendar.Year",
                        }
                    ]
                },
            },
            "sortDefinition": {
                "sort": [{"field": _col("Dim", "Sort Col"), "direction": "Ascending"}]
            },
        },
        "objects": {
            "dataPoint": [
                {
                    "properties": {
                        "fill": {
                            "solid": {
                                "color": {
                                    "expr": _meas("M", "CF Colour"),
                                }
                            }
                        }
                    }
                }
            ]
        },
    },
    "filterConfig": {
        "filters": [
            {
                "name": "f1",
                "field": _meas("M", "Show Gate"),
                "type": "Advanced",
                "filter": {
                    "Version": 2,
                    "From": [{"Name": "m", "Entity": "M", "Type": 0}],
                    "Where": [
                        {
                            "Condition": {
                                "Comparison": {
                                    "ComparisonKind": 0,
                                    "Left": {
                                        "Measure": {
                                            "Expression": {"SourceRef": {"Source": "m"}},
                                            "Property": "Show Gate",
                                        }
                                    },
                                    "Right": {"Literal": {"Value": "1L"}},
                                }
                            }
                        }
                    ],
                },
            },
            {
                "name": "f2",
                "field": _col("Dim", "Filter Col"),
                "type": "Categorical",
                "filter": {
                    "Version": 2,
                    "From": [{"Name": "d", "Entity": "Dim", "Type": 0}],
                    "Where": [
                        {
                            "Condition": {
                                "In": {
                                    "Expressions": [_col("Dim", "Filter Col", alias="d")],
                                    "Values": [[{"Literal": {"Value": "'A'"}}]],
                                }
                            }
                        }
                    ],
                },
            },
        ]
    },
}

_BOOKMARK = {
    "name": "bm01",
    "displayName": "Only A",
    "explorationState": {
        "version": "1.3",
        "activeSection": "p1",
        "sections": {
            "p1": {
                "filters": {
                    "byExpr": [
                        {
                            "name": "bf1",
                            "expression": _col("Dim", "Bookmark Col"),
                            "type": "Categorical",
                        }
                    ]
                }
            }
        },
    },
}


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


@pytest.fixture
def report(tmp_path):
    rpt = tmp_path / "Syn.Report"
    d = rpt / "definition"
    _write(d / "report.json", {"themeCollection": {}})
    _write(d / "pages" / "pages.json", {"pageOrder": ["p1"], "activePageName": "p1"})
    _write(d / "pages" / "p1" / "page.json", {"name": "p1", "displayName": "Page 1"})
    _write(d / "pages" / "p1" / "visuals" / "vdeneb000000000001" / "visual.json", _VISUAL)
    _write(d / "bookmarks" / "bm01.bookmark.json", _BOOKMARK)
    _write(d / "bookmarks" / "bookmarks.json", {"items": [{"name": "bm01"}]})
    an = PbirReportAnalyzer()
    data = an.analyze_report(str(rpt))
    return an, data


def _visual(data):
    return data["pages"][0]["visuals"][0]


def test_deneb_dataset_role_bindings_found(report):
    an, data = report
    refs = an.extract_all_field_references(data)
    assert "M[Deneb Measure]" in refs["measures"]
    assert "Dim[Category]" in refs["columns"]


def test_visual_level_filters_read_from_top_level_filter_config(report):
    _, data = report
    filters = _visual(data)["filters"]
    assert [f["field"]["name"] for f in filters] == ["Show Gate", "Filter Col"]
    assert filters[0]["field"]["type"] == "Measure"
    assert filters[0]["field"]["table"] == "M"


def test_filter_measure_counts_as_report_usage(report):
    an, data = report
    assert "M[Show Gate]" in an.extract_all_field_references(data)["measures"]


def test_sort_definition_and_cf_objects_count(report):
    an, data = report
    refs = an.extract_all_field_references(data)
    assert "Dim[Sort Col]" in refs["columns"]
    assert "M[CF Colour]" in refs["measures"]


def test_field_refs_carry_location_and_resolve_source_alias(report):
    _, data = report
    refs = _visual(data)["field_refs"]
    by = {(r["kind"], r["table"], r["name"], r["location"]) for r in refs}
    assert ("measure", "M", "Deneb Measure", "query:dataset") in by
    assert ("column", "Dim", "Sort Col", "sort") in by
    assert ("measure", "M", "CF Colour", "objects") in by
    # the Where clause uses SourceRef.Source = "d" -> resolved through From to Dim
    assert ("column", "Dim", "Filter Col", "filter") in by
    assert all(r["table"] for r in refs), refs


def test_hierarchy_level_binding(report):
    _, data = report
    lvl = [r for r in _visual(data)["field_refs"] if r["kind"] == "hierarchy_level"]
    assert lvl == [
        {
            "kind": "hierarchy_level",
            "table": "Dim",
            "name": "Calendar",
            "location": "query:Rows",
            "level": "Year",
        }
    ]


def test_bookmark_state_refs(report):
    _, data = report
    bm = data["bookmarks"][0]
    assert {(r["table"], r["name"]) for r in bm["field_refs"]} == {("Dim", "Bookmark Col")}
    assert all(r["location"] == "bookmark" for r in bm["field_refs"])


def test_walker_unit_selector_and_aggregation_wrapper():
    from core.pbip.pbir_field_refs import extract_field_refs

    obj = {
        "objects": {
            "values": [
                {"selector": {"metadata": "Sum(Dim.Total Return (%))"}},
                {"selector": {"metadata": "M.Plain"}},
            ]
        }
    }
    refs = extract_field_refs(obj)
    assert [(r.kind, r.table, r.name) for r in refs] == [
        ("selector", "Dim", "Total Return (%)"),
        ("selector", "M", "Plain"),
    ]


def test_golden_matrix_visual_filters_found():
    data = PbirReportAnalyzer().analyze_report(str(GOLDEN / "RPT-Sales.Report"))
    page = next(p for p in data["pages"] if p["id"] == "aa01overview00000001")
    matrix = next(v for v in page["visuals"] if v["id"] == "vm01matrixfilters001")
    names = {f["field"]["name"] for f in matrix["filters"]}
    assert "Show In Slot" in names
    refs = PbirReportAnalyzer().extract_all_field_references(data)
    assert "m Metrics[Show In Slot]" in refs["measures"]
