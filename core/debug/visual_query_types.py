"""Leaf definitions split out of ``core.debug.visual_query_builder`` (C-17).

Pure move, no behaviour change: ``core.debug.visual_query_builder`` re-exports every name below.
"""

from typing import Any, Dict, List, Optional, Tuple
from .filter_to_dax import FilterExpression
from core.dax.dax_names import bracket, column_ref
from dataclasses import dataclass, field
import re

# Visual types that are slicers (standard slicer + advanced/chiclet slicers)
SLICER_VISUAL_TYPES = {"slicer", "advancedSlicerVisual"}

# Visual types that are UI/layout elements, not data-bearing visuals
UI_VISUAL_TYPES = {
    "shape",
    "basicShape",
    "image",
    "textbox",
    "button",
    "actionButton",
    "bookmarkNavigator",
    "pageNavigator",
    "navigatorButton",
    "visualGroup",
    "group",
    "slicer",
    "advancedSlicerVisual",
    "multiRowCard",  # Multi-row cards are typically label/context displays
}

# Visual types that display actual data (analytical visuals)
DATA_VISUAL_TYPES = {
    "pivotTable",
    "matrix",
    "table",
    "tableEx",
    "barChart",
    "clusteredBarChart",
    "stackedBarChart",
    "hundredPercentStackedBarChart",
    "columnChart",
    "clusteredColumnChart",
    "stackedColumnChart",
    "hundredPercentStackedColumnChart",
    "lineChart",
    "areaChart",
    "stackedAreaChart",
    "lineStackedColumnComboChart",
    "lineClusteredColumnComboChart",
    "pieChart",
    "donutChart",
    "treemap",
    "funnel",
    "scatterChart",
    "bubbleChart",
    "map",
    "filledMap",
    "azureMap",
    "shapeMap",
    "gauge",
    "kpi",
    "card",
    # F-12: the new card visual binds its fields under the ``Data`` role.
    "cardVisual",
    # Audit T111: multiRowCard intentionally NOT here — it lives in
    # UI_VISUAL_TYPES (label/context display). Including it in both sets
    # made is_data_visual order-dependent.
    "waterfallChart",
    "ribbonChart",
    "decompositionTreeVisual",
    "keyInfluencers",
    "qnaVisual",
    "scriptVisual",
    "pythonVisual",
    "rScript",
}

# Visual types that include grand total rows (need ROLLUPADDISSUBTOTAL in SUMMARIZECOLUMNS)
SUBTOTAL_VISUAL_TYPES = {"pivotTable", "matrix", "table", "tableEx"}

# Default TOPN limits per visual type (top.count + 1 to match PBI behavior)
VISUAL_TYPE_TOPN = {
    # Charts: top.count=1000 -> TOPN(1001)
    "barChart": 1001,
    "clusteredBarChart": 1001,
    "stackedBarChart": 1001,
    "hundredPercentStackedBarChart": 1001,
    "columnChart": 1001,
    "clusteredColumnChart": 1001,
    "stackedColumnChart": 1001,
    "hundredPercentStackedColumnChart": 1001,
    "lineChart": 1001,
    "areaChart": 1001,
    "stackedAreaChart": 1001,
    "lineStackedColumnComboChart": 1001,
    "lineClusteredColumnComboChart": 1001,
    "waterfallChart": 1001,
    "ribbonChart": 1001,
    "pieChart": 1001,
    "donutChart": 1001,
    # Tables/Matrix: top.count=500 -> TOPN(501)
    "pivotTable": 501,
    "matrix": 501,
    "table": 501,
    "tableEx": 501,
    "treemap": 501,
    "funnel": 501,
    # Maps: top.count=3500 -> TOPN(3501)
    "map": 3501,
    "filledMap": 3501,
    "azureMap": 3501,
    "shapeMap": 3501,
    # Scatter: top.count=10000 -> TOPN(10001)
    "scatterChart": 10001,
    "bubbleChart": 10001,
}

DEFAULT_TOPN = 1001


def _sanitize_alias(name: str) -> str:
    """Sanitize a measure/column name into a PBI-compatible alias.

    Power BI replaces all non-alphanumeric characters with underscores.
    E.g. 'WF1-Base' -> 'WF1_Base', 'Net Asset Value' -> 'Net_Asset_Value'
    """
    return re.sub(r"[^a-zA-Z0-9]", "_", name)


def _walk_for_measure_refs(obj, measures: set) -> None:
    """Recursively walk JSON to find Measure references (data labels, titles, etc.)."""
    if isinstance(obj, dict):
        if "Measure" in obj and isinstance(obj["Measure"], dict):
            ref = obj["Measure"]
            entity = (ref.get("Expression") or {}).get("SourceRef", {}).get("Entity", "")
            prop = ref.get("Property", "")
            if entity and prop:
                measures.add((entity, prop))
        for value in obj.values():
            _walk_for_measure_refs(value, measures)
    elif isinstance(obj, list):
        for item in obj:
            _walk_for_measure_refs(item, measures)


def _iter_projections(visual: Dict[str, Any]):
    """Yield ``(role, projection)`` for EVERY queryState role (F-12)."""
    query_state = ((visual or {}).get("query") or {}).get("queryState") or {}
    if not isinstance(query_state, dict):
        return
    for role, section in query_state.items():
        if not isinstance(section, dict):
            continue
        for proj in section.get("projections") or []:
            if isinstance(proj, dict):
                yield role, proj


def _aggregation_expression(agg: Dict[str, Any]) -> Optional[str]:
    """``{"Aggregation": {"Expression": {"Column": ...}, "Function": 0}}`` -> ``SUM('T'[C])``."""
    from .filter_to_dax import _AGGREGATION_FUNCTIONS

    fn = _AGGREGATION_FUNCTIONS.get((agg or {}).get("Function"))
    col = ((agg or {}).get("Expression") or {}).get("Column") or {}
    entity = (col.get("Expression") or {}).get("SourceRef", {}).get("Entity", "")
    prop = col.get("Property", "")
    if not fn or not entity or not prop:
        return None
    return f"{fn}({column_ref(entity, prop)})"


_AGGREGATION_LABELS = {
    0: "Sum",
    1: "Average",
    2: "Count (Distinct)",
    3: "Min",
    4: "Max",
    5: "Count",
    6: "Median",
    7: "Standard deviation",
    8: "Variance",
}


def _aggregation_label(agg: Dict[str, Any]) -> Optional[str]:
    """Human label of an implicit measure, e.g. ``Sum of Amount``."""
    col = ((agg or {}).get("Expression") or {}).get("Column") or {}
    prop = col.get("Property", "")
    fn = _AGGREGATION_LABELS.get((agg or {}).get("Function"))
    if not prop or not fn:
        return None
    return f"{fn} of {prop}"


@dataclass
class VisualInfo:
    """Information about a visual from PBIP."""

    visual_id: str
    visual_type: str
    visual_name: Optional[str]
    page_name: str
    page_id: str
    title: Optional[str]
    measures: List[str] = field(default_factory=list)
    columns: List[str] = field(default_factory=list)
    filters: List[Dict] = field(default_factory=list)
    sort_columns: List[str] = field(default_factory=list)
    object_measures: List[Tuple[str, str]] = field(default_factory=list)
    scalar_measures: List[Tuple[str, str]] = field(default_factory=list)
    #: 'T'[C] refs bound to the matrix ``Rows`` role (subtotal levels, F-19).
    row_columns: List[str] = field(default_factory=list)
    #: Implicit measures (Aggregation projections) -> their DAX alias.
    measure_aliases: Dict[str, str] = field(default_factory=dict)
    #: Measure property -> entity (table) from the visual's own projections.
    measure_entities: Dict[str, str] = field(default_factory=dict)
    #: ``visual.query.sortDefinition.sort`` entries (F-19).
    sort_definition: List[Dict[str, Any]] = field(default_factory=list)
    #: False when the matrix turns row subtotals off (objects.subTotals).
    row_subtotals: Optional[bool] = None
    #: queryState roles found on the visual, with projection counts (F-12).
    roles: Dict[str, int] = field(default_factory=dict)
    #: The visual.json the info was parsed from (polarity lookups).
    visual_json: Dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class SlicerState:
    """Current slicer state from PBIP."""

    slicer_id: str
    page_name: str
    table: str
    column: str
    field_reference: str
    selected_values: List[Any]
    selection_mode: str
    is_inverted: bool
    affects_all_pages: bool = False
    # Multi-column composite TREATAS support (sf Row Drill, sf Slicer 1, etc.)
    extra_columns: List[str] = field(default_factory=list)  # Additional 'Table'[Col] refs
    composite_values: Optional[List[List[Any]]] = None  # Value tuples, one per selected row


@dataclass
class FilterContext:
    """Complete filter context for a visual."""

    report_filters: List[FilterExpression] = field(default_factory=list)
    page_filters: List[FilterExpression] = field(default_factory=list)
    visual_filters: List[FilterExpression] = field(default_factory=list)
    slicer_filters: List[FilterExpression] = field(default_factory=list)
    #: Filters that could not be expressed in DAX (F-04 / F-39(3)): the query
    #: does NOT reproduce the visual while this list is non-empty.
    unsupported: List[Dict[str, Any]] = field(default_factory=list)
    #: Entries that filter nothing (no condition, inverted "select all",
    #: drillthrough placeholder) -- informational.
    inactive: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def fidelity(self) -> str:
        """``"full"`` when every filter was reproduced, else ``"partial"``."""
        return "partial" if self.unsupported else "full"

    def all_filters(self) -> List[FilterExpression]:
        """Get all filters combined."""
        return self.report_filters + self.page_filters + self.visual_filters + self.slicer_filters

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""

        def filter_to_dict(f):
            """Convert a FilterExpression to a dict with all relevant fields."""
            return {
                "dax": f.dax,
                "source": f.source,
                "table": f.table,
                "column": f.column,
                "values": f.values,
                "condition_type": f.condition_type,
                "is_field_parameter": f.is_field_parameter,
                "classification": getattr(f, "classification", "data"),
                "has_null_values": getattr(f, "has_null_values", False),
                "polarity": getattr(f, "polarity", "include"),
            }

        return {
            "report_filters": [filter_to_dict(f) for f in self.report_filters],
            "page_filters": [filter_to_dict(f) for f in self.page_filters],
            "visual_filters": [filter_to_dict(f) for f in self.visual_filters],
            "slicer_filters": [filter_to_dict(f) for f in self.slicer_filters],
            "unsupported": list(self.unsupported),
            "fidelity": self.fidelity,
        }

    def data_filters_only(self) -> List:
        """Get only data filters (excluding field parameters and UI controls)."""
        from .filter_to_dax import FilterClassification

        all_f = self.all_filters()
        return [
            f for f in all_f if getattr(f, "classification", "data") == FilterClassification.DATA
        ]

    def field_parameter_filters(self) -> List:
        """Get only field parameter filters."""
        from .filter_to_dax import FilterClassification

        all_f = self.all_filters()
        return [
            f
            for f in all_f
            if getattr(f, "classification", "data") == FilterClassification.FIELD_PARAMETER
        ]

    def ui_control_filters(self) -> List:
        """Get only UI control filters."""
        from .filter_to_dax import FilterClassification

        all_f = self.all_filters()
        return [
            f
            for f in all_f
            if getattr(f, "classification", "data") == FilterClassification.UI_CONTROL
        ]


@dataclass
class MeasureDefinition:
    """Measure definition from the model."""

    name: str
    expression: str
    table: Optional[str] = None
    format_string: Optional[str] = None


@dataclass
class VisualQueryResult:
    """Result of building a visual query."""

    visual_info: VisualInfo
    filter_context: FilterContext
    dax_query: str
    measure_name: str
    measure_expression: Optional[str] = None
    measure_definitions: List[MeasureDefinition] = field(default_factory=list)
    expanded_query: Optional[str] = None  # Query with measure expressions inline
    filter_breakdown: Dict[str, Any] = field(default_factory=dict)
    format_string_measures: List[str] = field(
        default_factory=list
    )  # IGNORE() refs for dynamic format strings
    #: "full" | "partial" -- partial when a filter/role could not be reproduced.
    fidelity: str = "full"
    unsupported_filters: List[Dict[str, Any]] = field(default_factory=list)
    fidelity_notes: List[str] = field(default_factory=list)
    #: DAX column alias of every measure in the main EVALUATE (for value reads, F-15).
    measure_aliases: Dict[str, str] = field(default_factory=dict)
    #: Arguments the query was built with (``VisualQueryBuilder.rebuild_query``).
    build_kwargs: Dict[str, Any] = field(default_factory=dict, repr=False)

    def value_column(self, measure: Optional[str] = None) -> Optional[str]:
        """Result-row key of *measure* (default: the target measure), F-15.

        ADOMD returns SUMMARIZECOLUMNS aliases bracketed (``[Total_Sales]``);
        callers should look the value up with :func:`row_value`.
        """
        ref = measure or self.measure_name
        if not ref:
            return None
        alias = self.measure_aliases.get(ref)
        if alias is None and not ref.startswith("["):
            alias = self.measure_aliases.get(bracket(ref))
        return alias or _sanitize_alias(ref.strip("[]"))


def row_value(row: Dict[str, Any], alias: Optional[str]) -> Any:
    """Read column *alias* from a result row, bracketed or not (``[X]`` / ``X`` / ``'T'[X]``)."""
    if not row or not alias:
        return None
    for key in (alias, f"[{alias}]"):
        if key in row:
            return row[key]
    suffix = f"[{alias}]"
    for key, value in row.items():
        if isinstance(key, str) and key.endswith(suffix):
            return value
    return None


def is_grand_total_row(row: Dict[str, Any]) -> bool:
    """True for the ROLLUPADDISSUBTOTAL grand-total row (bracketed or bare key, F-20)."""
    for key in ("IsGrandTotalRowTotal", "[IsGrandTotalRowTotal]"):
        if key in row:
            return str(row[key]).lower() == "true"
    return False
