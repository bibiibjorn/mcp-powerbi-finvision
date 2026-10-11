"""Regression tests for build_visual_query variable scoping + profiler error surfacing.

Root cause covered here: `scalar_format_string_measures` was initialized INSIDE the
`if expand_measures and self._query_executor:` block but consumed unconditionally at
the `_build_visual_dax_query(...)` call. Any offline/no-executor path therefore raised
`UnboundLocalError`, and `profile_page` swallowed it into a silent `None` -> the visual
vanished from the profile output ("only found 1 visual").
"""

import json
from pathlib import Path

import pytest

from core.debug.visual_query_builder import VisualQueryBuilder


def _write_report(root: Path) -> Path:
    """Minimal PBIR report with one matrix bound to measures + columns."""
    report = root / "R.Report"
    page_dir = report / "definition" / "pages" / "pg1"
    vis_dir = page_dir / "visuals" / "matrix1"
    vis_dir.mkdir(parents=True)

    (report / "definition" / "pages" / "pages.json").write_text(
        json.dumps({"pageOrder": ["pg1"], "activePageName": "pg1"}), encoding="utf-8"
    )
    (page_dir / "page.json").write_text(
        json.dumps({"name": "pg1", "displayName": "Matrix", "width": 1280, "height": 720}),
        encoding="utf-8",
    )

    visual = {
        "name": "matrix1",
        "position": {"x": 0, "y": 0, "width": 600, "height": 400},
        "visual": {
            "visualType": "pivotTable",
            "query": {
                "queryState": {
                    "Rows": {
                        "projections": [
                            {
                                "field": {
                                    "Column": {
                                        "Expression": {"SourceRef": {"Entity": "s_ReportLines"}},
                                        "Property": "Line Name",
                                    }
                                },
                                "queryRef": "s_ReportLines.Line Name",
                                "active": True,
                            }
                        ]
                    },
                    "Values": {
                        "projections": [
                            {
                                "field": {
                                    "Measure": {
                                        "Expression": {"SourceRef": {"Entity": "m_Measures"}},
                                        "Property": "PL | TC0 Reported",
                                    }
                                },
                                "queryRef": "m_Measures.PL | TC0 Reported",
                            }
                        ]
                    },
                }
            },
        },
    }
    (vis_dir / "visual.json").write_text(json.dumps(visual), encoding="utf-8")
    return report


@pytest.fixture()
def builder(tmp_path):
    report = _write_report(tmp_path)
    b = VisualQueryBuilder(str(report))
    # No live model: this is the offline path that used to raise UnboundLocalError.
    b._query_executor = None
    return b


def test_build_visual_query_without_executor_does_not_raise(builder):
    """Offline (no query executor) must still produce a query, not UnboundLocalError."""
    result = builder.build_visual_query(page_name="Matrix", visual_id="matrix1")

    assert result is not None, "matrix silently dropped instead of returning a query"
    assert result.dax_query
    assert "PL | TC0 Reported" in result.dax_query


def test_build_visual_query_expand_measures_false_does_not_raise(builder):
    """expand_measures=False skips the same block and must not raise either."""
    result = builder.build_visual_query(
        page_name="Matrix", visual_id="matrix1", expand_measures=False
    )

    assert result is not None
    assert result.dax_query
