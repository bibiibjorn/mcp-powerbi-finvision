"""
Filter Context Analyzer Module

Analyzes filter context for Power BI visuals to determine which aggregation
level would be used based on the columns in context.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Set, Tuple
from enum import Enum

from .aggregation_detector import AggLevelMeasure, AggregationTable

logger = logging.getLogger(__name__)

# Lower-case visualType values of slicers (I-23: + listSlicer / textSlicer).
SLICER_VISUAL_TYPES = {"slicer", "advancedslicervisual", "listslicer", "textslicer"}


class FilterSourceType(Enum):
    """Types of filter sources in Power BI."""

    VISUAL_FIELD = "visual_field"  # Column used in visual (Category, Values, etc.)
    VISUAL_FILTER = "visual_filter"  # Filter applied at visual level
    PAGE_FILTER = "page_filter"  # Filter applied at page level
    REPORT_FILTER = "report_filter"  # Filter applied at report level
    SLICER = "slicer"  # Slicer on the page
    DRILLTHROUGH = "drillthrough"  # Drillthrough filter
    CROSS_FILTER = "cross_filter"  # Cross-filtering from another visual


@dataclass
class ColumnContext:
    """Represents a column in the filter context."""

    table: str
    column: str
    source_type: FilterSourceType
    source_description: str
    triggers_detail: bool = False  # True if this column forces base table
    triggers_mid_level: bool = False  # True if this column allows mid-level
    filter_values: Optional[List[Any]] = None  # Specific values if filtered


@dataclass
class FilterSource:
    """Represents a source of filtering."""

    source_type: FilterSourceType
    source_id: Optional[str]  # Visual ID, slicer ID, etc.
    source_name: Optional[str]  # Display name
    columns: List[ColumnContext]


@dataclass
class SlicerInfo:
    """Information about a slicer."""

    slicer_id: str
    page_id: str
    entity: str  # Table name
    column: str
    slicer_type: str  # Single, Multi, Range, etc.
    sync_group: Optional[str] = None
    affects_pages: List[str] = field(default_factory=list)


@dataclass
class FilterContext:
    """Complete filter context for a visual."""

    visual_id: str
    page_id: str
    all_columns: List[ColumnContext]
    filter_sources: List[FilterSource]
    has_detail_triggers: bool = False
    has_mid_level_triggers: bool = False
    detail_trigger_columns: List[str] = field(default_factory=list)
    mid_level_trigger_columns: List[str] = field(default_factory=list)


class FilterContextAnalyzer:
    """Analyzes filter context to determine aggregation level."""

    def __init__(
        self,
        agg_level_measure: Optional[AggLevelMeasure] = None,
        agg_tables: Optional[List[AggregationTable]] = None,
    ):
        """
        Initialize the analyzer with aggregation rules.

        Args:
            agg_level_measure: Parsed aggregation level measure with trigger rules
            agg_tables: List of aggregation tables for context
        """
        self.agg_level_measure = agg_level_measure
        self.agg_tables = agg_tables or []

        # Build quick lookup sets for trigger columns
        self.detail_triggers: Set[str] = set()
        self.mid_level_triggers: Set[str] = set()

        if agg_level_measure:
            self.detail_triggers = set(agg_level_measure.detail_trigger_columns)
            self.mid_level_triggers = set(agg_level_measure.mid_level_trigger_columns)

    def analyze_visual_context(
        self,
        visual_data: Dict[str, Any],
        page_filters: Optional[List[Dict]] = None,
        report_filters: Optional[List[Dict]] = None,
        slicers: Optional[List[SlicerInfo]] = None,
        page_id: str = "",
    ) -> FilterContext:
        """
        Analyze a visual's complete filter context.

        Args:
            visual_data: Parsed visual JSON data
            page_filters: Filters applied at page level
            report_filters: Filters applied at report level
            slicers: Slicers on the page
            page_id: ID of the page containing this visual

        Returns:
            FilterContext with all columns and their impact on aggregation
        """
        visual_id = visual_data.get("name", "")
        all_columns: List[ColumnContext] = []
        filter_sources: List[FilterSource] = []

        # 1. Extract columns from visual fields (Category, Values, Legend, etc.)
        visual_fields = self._extract_visual_fields(visual_data)
        if visual_fields:
            filter_sources.append(
                FilterSource(
                    source_type=FilterSourceType.VISUAL_FIELD,
                    source_id=visual_id,
                    source_name="Visual Fields",
                    columns=visual_fields,
                )
            )
            all_columns.extend(visual_fields)

        # 2. Extract visual-level filters
        visual_filters = self._extract_visual_filters(visual_data)
        if visual_filters:
            filter_sources.append(
                FilterSource(
                    source_type=FilterSourceType.VISUAL_FILTER,
                    source_id=visual_id,
                    source_name="Visual Filters",
                    columns=visual_filters,
                )
            )
            all_columns.extend(visual_filters)

        # 3. Add page-level filters
        if page_filters:
            page_filter_cols = self._parse_filters(page_filters, FilterSourceType.PAGE_FILTER)
            if page_filter_cols:
                filter_sources.append(
                    FilterSource(
                        source_type=FilterSourceType.PAGE_FILTER,
                        source_id=page_id,
                        source_name="Page Filters",
                        columns=page_filter_cols,
                    )
                )
                all_columns.extend(page_filter_cols)

        # 4. Add report-level filters
        if report_filters:
            report_filter_cols = self._parse_filters(report_filters, FilterSourceType.REPORT_FILTER)
            if report_filter_cols:
                filter_sources.append(
                    FilterSource(
                        source_type=FilterSourceType.REPORT_FILTER,
                        source_id="report",
                        source_name="Report Filters",
                        columns=report_filter_cols,
                    )
                )
                all_columns.extend(report_filter_cols)

        # 5. Add slicer impacts
        if slicers:
            slicer_cols = self._process_slicers(slicers, page_id)
            if slicer_cols:
                filter_sources.append(
                    FilterSource(
                        source_type=FilterSourceType.SLICER,
                        source_id="slicers",
                        source_name="Page Slicers",
                        columns=slicer_cols,
                    )
                )
                all_columns.extend(slicer_cols)

        # Analyze which columns trigger which levels
        detail_trigger_cols = []
        mid_level_trigger_cols = []
        has_detail = False
        has_mid_level = False

        for col in all_columns:
            col_ref = f"{col.table}[{col.column}]"

            if self._is_detail_trigger(col_ref):
                col.triggers_detail = True
                has_detail = True
                detail_trigger_cols.append(col_ref)
            elif self._is_mid_level_trigger(col_ref):
                col.triggers_mid_level = True
                has_mid_level = True
                mid_level_trigger_cols.append(col_ref)

        return FilterContext(
            visual_id=visual_id,
            page_id=page_id,
            all_columns=all_columns,
            filter_sources=filter_sources,
            has_detail_triggers=has_detail,
            has_mid_level_triggers=has_mid_level,
            detail_trigger_columns=detail_trigger_cols,
            mid_level_trigger_columns=mid_level_trigger_cols,
        )

    def _extract_visual_fields(self, visual_data: Dict) -> List[ColumnContext]:
        """Extract columns used in visual field wells (Category, Values, etc.)."""
        columns = []

        visual = visual_data.get("visual", {})
        query = visual.get("query", {})
        query_state = query.get("queryState", {})

        # Common field well names in Power BI
        field_wells = [
            "Category",
            "Series",
            "Values",
            "Y",
            "X",
            "Rows",
            "Columns",
            "Legend",
            "Details",
            "Tooltips",
            "Size",
            "Gradient",
            "Axis",
            "Location",
            "Latitude",
            "Longitude",
            "Group",
            "SmallMultiples",
        ]

        for well_name in field_wells:
            well_data = query_state.get(well_name, {})
            projections = well_data.get("projections", [])

            for proj in projections:
                field_info = proj.get("field", {})

                # Handle Column type
                column_info = field_info.get("Column", {})
                if column_info:
                    entity = self._get_entity_name(column_info)
                    prop = column_info.get("Property", "")
                    if entity and prop:
                        columns.append(
                            ColumnContext(
                                table=entity,
                                column=prop,
                                source_type=FilterSourceType.VISUAL_FIELD,
                                source_description=f"{well_name} field: {entity}[{prop}]",
                            )
                        )

                # Handle Measure type (for identifying which measures are used)
                measure_info = field_info.get("Measure", {})
                if measure_info:
                    entity = self._get_entity_name(measure_info)
                    prop = measure_info.get("Property", "")
                    # Measures don't directly add to filter context for ISFILTERED
                    # but we track them for completeness

        return columns

    def _get_entity_name(self, field_info: Dict) -> str:
        """Extract entity (table) name from field info."""
        expr = field_info.get("Expression", {})
        source_ref = expr.get("SourceRef", {})
        return source_ref.get("Entity", "")

    def _extract_visual_filters(self, visual_data: Dict) -> List[ColumnContext]:
        """Extract columns from visual-level filters."""
        columns = []

        filter_config = visual_data.get("filterConfig", {})
        filters = filter_config.get("filters", [])

        for filt in filters:
            field_info = filt.get("field", {})

            # Handle Column filter
            column_info = field_info.get("Column", {})
            if column_info:
                entity = self._get_entity_name(column_info)
                prop = column_info.get("Property", "")
                if entity and prop:
                    columns.append(
                        ColumnContext(
                            table=entity,
                            column=prop,
                            source_type=FilterSourceType.VISUAL_FILTER,
                            source_description=f"Visual filter: {entity}[{prop}]",
                        )
                    )

            # Measure filters are deliberately not recorded: they don't make any
            # column ISFILTERED, so they cannot change the aggregation level
            # (I-23: the half-wired ``measure_info`` lookup was removed).

        return columns

    def _parse_filters(
        self, filters: List[Dict], source_type: FilterSourceType
    ) -> List[ColumnContext]:
        """Parse a list of filter definitions."""
        columns = []

        for filt in filters:
            # Handle different filter structures
            field_info = filt.get("field", {})
            if not field_info:
                # Try alternate structure
                field_info = filt

            column_info = field_info.get("Column", {})
            if column_info:
                entity = self._get_entity_name(column_info)
                prop = column_info.get("Property", "")
                if entity and prop:
                    columns.append(
                        ColumnContext(
                            table=entity,
                            column=prop,
                            source_type=source_type,
                            source_description=f"{source_type.value}: {entity}[{prop}]",
                        )
                    )

        return columns

    def _process_slicers(self, slicers: List[SlicerInfo], page_id: str) -> List[ColumnContext]:
        """Process slicers that affect this page."""
        columns = []

        for slicer in slicers:
            # Check if slicer affects this page
            affects_page = slicer.page_id == page_id or page_id in slicer.affects_pages

            if affects_page:
                columns.append(
                    ColumnContext(
                        table=slicer.entity,
                        column=slicer.column,
                        source_type=FilterSourceType.SLICER,
                        source_description=f"Slicer: {slicer.entity}[{slicer.column}]",
                    )
                )

        return columns

    def _is_detail_trigger(self, column_ref: str) -> bool:
        """Check if column triggers detail (base table) level."""
        # Normalize column reference
        normalized = self._normalize_column_ref(column_ref)
        return normalized in self.detail_triggers or column_ref in self.detail_triggers

    def _is_mid_level_trigger(self, column_ref: str) -> bool:
        """Check if column triggers mid-level aggregation."""
        normalized = self._normalize_column_ref(column_ref)
        return normalized in self.mid_level_triggers or column_ref in self.mid_level_triggers

    def _normalize_column_ref(self, column_ref: str) -> str:
        """Normalize column reference for comparison."""
        # Handle variations like 'Table'[Column] vs Table[Column]
        return column_ref.replace("'", "")

    def determine_aggregation_level(self, filter_context: FilterContext) -> Tuple[int, str, str]:
        """
        Determine which aggregation level applies based on filter context.

        Args:
            filter_context: Analyzed filter context

        Returns:
            Tuple of (level_number, level_name, reasoning)
        """
        # Check detail triggers first (highest priority - forces base table)
        if filter_context.has_detail_triggers:
            trigger_cols = ", ".join(filter_context.detail_trigger_columns[:3])
            if len(filter_context.detail_trigger_columns) > 3:
                trigger_cols += f" (+{len(filter_context.detail_trigger_columns) - 3} more)"

            return (1, "Base Table (Detail)", f"Detail-level columns in context: {trigger_cols}")

        # Check mid-level triggers
        if filter_context.has_mid_level_triggers:
            trigger_cols = ", ".join(filter_context.mid_level_trigger_columns[:3])
            if len(filter_context.mid_level_trigger_columns) > 3:
                trigger_cols += f" (+{len(filter_context.mid_level_trigger_columns) - 3} more)"

            return (2, "Mid-Level Aggregation", f"Mid-level columns in context: {trigger_cols}")

        # Default to highest aggregation
        return (3, "High-Level Aggregation", "No dimension filtering requires detail data")

    def get_aggregation_table_for_level(self, level: int) -> Optional[str]:
        """Get the aggregation table name for a given level."""
        if level == 1:
            return None  # Base table (not an aggregation table)

        for agg_table in self.agg_tables:
            if agg_table.level == level:
                return agg_table.name

        return None


def extract_slicers_from_page(page_data: Dict, page_id: str) -> List[SlicerInfo]:
    """
    Extract slicer information from page visual data.

    Args:
        page_data: Page data containing visuals
        page_id: ID of the page

    Returns:
        List of SlicerInfo objects
    """
    slicers = []

    visuals = page_data.get("visuals", [])
    for visual in visuals:
        visual_info = visual.get("visual", {})
        visual_type = visual_info.get("visualType", "")

        # Check if this is a slicer (standard or advanced/chiclet)
        if visual_type.lower() in SLICER_VISUAL_TYPES:
            slicer_id = visual.get("name", "")

            # Extract the field being sliced
            query = visual_info.get("query", {})
            query_state = query.get("queryState", {})

            # Slicers typically use "Values" or "Field" well
            for well_name in ["Values", "Field", "Category"]:
                well_data = query_state.get(well_name, {})
                projections = well_data.get("projections", [])

                for proj in projections:
                    field_info = proj.get("field", {})
                    column_info = field_info.get("Column", {})

                    if column_info:
                        expr = column_info.get("Expression", {})
                        source_ref = expr.get("SourceRef", {})
                        entity = source_ref.get("Entity", "")
                        prop = column_info.get("Property", "")

                        if entity and prop:
                            slicers.append(
                                SlicerInfo(
                                    slicer_id=slicer_id,
                                    page_id=page_id,
                                    entity=entity,
                                    column=prop,
                                    slicer_type="Unknown",  # Would need more parsing
                                )
                            )

    return slicers


def extract_page_filters(page_data: Dict) -> List[Dict]:
    """Extract page-level filters from page configuration."""
    filters = []

    filter_config = page_data.get("filterConfig", {})
    page_filters = filter_config.get("filters", [])

    for filt in page_filters:
        filters.append(filt)

    return filters
