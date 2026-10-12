"""2026-09-22 deep review, slice I — 07_PBIP_Operations aggregation_analysis (I-23, I-28 #6).

Runs the real analyzer / report builder / handler over a tiny synthetic PBIP
assembled from the golden fixture (``tests/fixtures/pbir_golden/GoldenCo``):
its Overview page mixes data visuals with an action button, a bookmark
navigator, a visual group, a text box and two slicer types.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import core.documentation.utils as doc_utils
from core.aggregation import AggregationAnalyzer, AggregationReportBuilder
from core.aggregation.aggregation_analyzer import is_data_visual
from core.aggregation.aggregation_detector import (
    AggregatedColumn,
    AggregationTable,
    AggregationTableDetector,
)
from core.aggregation.aggregation_quality_analyzer import AggregationQualityAnalyzer
from server.handlers._internal.aggregation_helpers import handle_aggregation_analysis

GOLDEN = Path(__file__).parent / "fixtures" / "pbir_golden" / "GoldenCo"
# Report order deliberately differs from folder-name order.
PAGE_ORDER = ["ac03tooltip000000003", "ab02details000000002", "aa01overview00000001"]


@pytest.fixture
def pbip(tmp_path):
    root = tmp_path / "Proj"
    root.mkdir()
    shutil.copytree(GOLDEN / "MDL-Core.SemanticModel", root / "Model.SemanticModel")
    shutil.copytree(GOLDEN / "RPT-Sales.Report", root / "Model.Report")
    pages_json = root / "Model.Report" / "definition" / "pages" / "pages.json"
    cfg = json.loads(pages_json.read_text(encoding="utf-8"))
    cfg["pageOrder"] = PAGE_ORDER
    pages_json.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return root


def _pages(result):
    return [(p.page_id, p.visuals_analyzed) for p in result.report_summary.pages]


def test_non_data_visuals_are_not_scored(pbip):
    result = AggregationAnalyzer(str(pbip / "Model.SemanticModel")).analyze()
    by_page = dict(_pages(result))
    # Overview: cardVisual, card, pivotTable only (not actionButton,
    # bookmarkNavigator, the visual group, textbox, slicer, advancedSlicerVisual).
    assert by_page["aa01overview00000001"] == 3
    overview = next(p for p in result.report_summary.pages if p.page_id.startswith("aa01"))
    assert sorted(v.visual_type for v in overview.visuals) == ["card", "cardVisual", "pivotTable"]
    assert by_page["ab02details000000002"] == 3
    assert by_page["ac03tooltip000000003"] == 0
    assert result.report_summary.visuals_analyzed == 6


@pytest.mark.parametrize(
    "vtype, expected",
    [
        ("actionButton", False),
        ("listSlicer", False),
        ("textSlicer", False),
        ("basicShape", False),
        ("advancedSlicerVisual", False),
        ("pageNavigator", False),
        ("tableEx", True),
        ("", False),
    ],
)
def test_is_data_visual(vtype, expected):
    assert is_data_visual({"visual": {"visualType": vtype}}) is expected


def test_visual_group_is_not_a_data_visual():
    assert is_data_visual({"name": "g", "visualGroup": {"displayName": "G"}}) is False


def test_pages_follow_page_order(pbip):
    result = AggregationAnalyzer(str(pbip / "Model.SemanticModel")).analyze()
    assert [pid for pid, _ in _pages(result)] == PAGE_ORDER


def test_page_filter_applies_before_scoring(pbip):
    result = AggregationAnalyzer(str(pbip / "Model.SemanticModel")).analyze(page_filter="details")
    rs = result.report_summary
    assert [p.page_name for p in rs.pages] == ["Details"]
    assert rs.total_pages == 1
    assert rs.visuals_analyzed == 3
    assert rs.total_visuals == 3


# ------------------------------------------------------------------ handler


def test_handler_json_without_output_path_writes_nothing(pbip, tmp_path, monkeypatch):
    monkeypatch.setattr(doc_utils, "REPO_ROOT", str(tmp_path / "fake_repo"))
    res = handle_aggregation_analysis(
        {"pbip_path": str(pbip / "Model.SemanticModel"), "output_format": "json"}
    )
    assert res["success"] is True, res
    assert "html_report_path" not in res
    assert res["data"]["model_name"] == "Model"
    assert not (pbip / "exports").exists()
    assert not (tmp_path / "fake_repo").exists()


def test_handler_json_with_output_path_writes_only_json(pbip, tmp_path):
    out = pbip / "out" / "agg.json"
    res = handle_aggregation_analysis(
        {
            "pbip_path": str(pbip),  # project root: output_path may sit inside it
            "output_format": "json",
            "output_path": str(out),
        }
    )
    assert res["success"] is True, res
    assert json.loads(out.read_text(encoding="utf-8"))["model_name"] == "Model"
    assert [p.name for p in out.parent.iterdir()] == ["agg.json"]
    assert not (tmp_path / "exports").exists()  # old code: <pbip parent>/exports


def test_handler_html_default_goes_to_repo_exports(pbip, tmp_path, monkeypatch):
    fake_repo = tmp_path / "fake_repo"
    monkeypatch.setattr(doc_utils, "REPO_ROOT", str(fake_repo))
    res = handle_aggregation_analysis(
        {"pbip_path": str(pbip / "Model.SemanticModel"), "output_format": "html"}
    )
    assert res["success"] is True, res
    saved = Path(res["output_path"])
    assert saved.parent == fake_repo / "exports" / "aggregation_analysis"
    assert saved.exists()
    assert not (pbip / "exports").exists()


def test_handler_page_filter_scores_only_matching_pages(pbip):
    res = handle_aggregation_analysis(
        {
            "pbip_path": str(pbip / "Model.SemanticModel"),
            "output_format": "json",
            "page_filter": "Overview",
        }
    )
    rs = res["data"]["report_summary"]
    assert rs["total_pages"] == 1
    assert rs["visuals_analyzed"] == 3


# ------------------------------------------------- half-wired computations


def _agg_table(name="Agg Sales", level=3):
    return AggregationTable(
        name=name,
        level=level,
        level_name="High",
        is_hidden=True,
        source_expression=None,
        grain_columns=["Month"],
        aggregated_columns=[
            AggregatedColumn(name="Sales Amount", source_table="Sales", source_column="Amount")
        ],
        related_dimensions=[],
    )


def test_agg_aware_detection_uses_lexed_table_references():
    model = {
        "tables": [
            {"name": "Agg Sales", "columns": [], "measures": []},
            {
                "name": "Sales",
                "columns": [],
                "measures": [
                    {
                        "name": "Spaced",
                        "expression": "SWITCH ( TRUE (), 1, SUM ( 'Agg Sales'[Sales Amount] ), 0 )",
                    },
                    {
                        "name": "StringOnly",
                        "expression": 'SWITCH ( TRUE (), 1, "Agg Sales", 0 )',
                    },
                    {
                        "name": "LongerName",
                        "expression": "SWITCH ( TRUE (), 1, SUM ( 'Agg Sales Old'[X] ), 0 )",
                    },
                ],
            },
        ]
    }
    det = AggregationTableDetector(model)
    found = {m.name: m for m in det.detect_aggregation_aware_measures([_agg_table()], [])}
    assert set(found) == {"Spaced"}
    assert found["Spaced"].table_switches == {3: "Agg Sales"}
    assert found["Spaced"].column_switches == {3: "Agg Sales[Sales Amount]"}


def test_quality_reports_aggregated_source_columns():
    qa = AggregationQualityAnalyzer([_agg_table()], [], [])
    _score, analysis, _issues = qa._analyze_measure_coverage(_agg_table())
    assert analysis["aggregated_source_columns"] == ["Amount"]


def test_html_overview_shows_level_measures_and_page_ids(pbip):
    result = AggregationAnalyzer(str(pbip / "Model.SemanticModel")).analyze()
    html_text = AggregationReportBuilder(result).build_html_report()
    assert "Agg Level Measures" in html_text
    assert len(result.hit_rate_analysis.page_hit_rates) == 3
    assert 'id="page-hit-0"' in html_text
