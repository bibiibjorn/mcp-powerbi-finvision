"""In-scope visual-type inventory."""

from typing import Set, Tuple

IN_SCOPE_TYPES: Tuple[str, ...] = (
    # Chart family (pbir 0.9.9: stackedColumnChart + stackedBarChart are axis-config
    # modes of columnChart/barChart, not separately addressable types)
    "columnChart",
    "barChart",
    "clusteredColumnChart",
    "clusteredBarChart",
    "hundredPercentStackedColumnChart",
    "hundredPercentStackedBarChart",
    "lineChart",
    "areaChart",
    "stackedAreaChart",
    "hundredPercentStackedAreaChart",
    "lineClusteredColumnComboChart",
    "lineStackedColumnComboChart",
    "scatterChart",
    "pieChart",
    "donutChart",
    "treemap",
    "waterfallChart",
    "funnel",
    "ribbonChart",
    "gauge",
    "kpi",
    # Tabular
    "card",
    "cardVisual",
    "multiRowCard",
    "pivotTable",
    "tableEx",
    # Geo
    "map",
    "filledMap",
    "azureMap",
    "shapeMap",
    # AI / advanced
    "decompositionTreeVisual",
    "qnaVisual",
    "keyDriversVisual",
    "aiNarratives",
    # Slicers
    "slicer",
    "advancedSlicerVisual",
    "listSlicer",
    "textSlicer",
    # Canvas / chrome
    "textbox",
    "shape",
    "image",
    "actionButton",
    "bookmarkNavigator",
)

POINTER_ONLY_TYPES: Set[str] = {
    "pythonVisual",
    "scriptVisual",
    "rdlVisual",
    "filter",
    "group",
    "scorecard",
    "bubbleChart",
}


def is_in_scope(visual_type: str) -> bool:
    return visual_type in IN_SCOPE_TYPES


def resolver_note_for_unknown(visual_type: str) -> str:
    return (
        f"Unknown visual type '{visual_type}' — not in IN_SCOPE_TYPES or "
        f"POINTER_ONLY_TYPES. Update tools/visual_catalog/types.py."
    )
