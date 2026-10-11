"""
Slicer Impact Analyzer Module

Analyzes how slicers affect aggregation level usage across the report.
Provides impact analysis per slicer, sync group effects, and what-if scenarios.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Any, Set, Tuple, TYPE_CHECKING
from enum import Enum

from .aggregation_detector import AggregationTable, AggLevelMeasure
from .filter_context_analyzer import SlicerInfo

# Use TYPE_CHECKING to avoid circular imports
if TYPE_CHECKING:
    from .aggregation_analyzer import (  # noqa: F401
        VisualAggregationAnalysis,
        PageAggregationSummary,
        ReportAggregationSummary,
    )

logger = logging.getLogger(__name__)


class SlicerType(Enum):
    """Types of slicer interactions."""

    DROPDOWN = "dropdown"
    LIST = "list"
    RANGE = "range"
    DATE_RANGE = "date_range"
    HIERARCHY = "hierarchy"
    RELATIVE_DATE = "relative_date"
    UNKNOWN = "unknown"


class AggregationImpact(Enum):
    """Impact level of a slicer on aggregation."""

    NONE = "none"  # No impact on aggregation
    POSITIVE = "positive"  # Can enable higher-level aggregation
    NEUTRAL = "neutral"  # Matches aggregation grain
    NEGATIVE = "negative"  # Forces lower aggregation level
    CRITICAL = "critical"  # Forces base table usage


@dataclass
class SlicerSelectionScenario:
    """What-if scenario for a slicer selection."""

    selection_type: str  # "any", "single", "multiple", "range"
    aggregation_level: int  # Level that would be used
    aggregation_table: Optional[str]
    affected_visuals_count: int
    impact: AggregationImpact
    description: str


@dataclass
class SlicerAggregationImpact:
    """Detailed impact analysis for a single slicer."""

    slicer_id: str
    page_id: str
    page_name: str
    entity: str  # Table name
    column: str
    full_column_ref: str  # Table[Column]
    slicer_type: SlicerType
    sync_group: Optional[str]
    synced_pages: List[str]

    # Impact analysis
    impact_level: AggregationImpact
    triggers_detail_level: bool
    triggers_mid_level: bool
    is_in_aggregation_grain: bool

    # Affected visuals
    affected_visuals_on_page: int
    affected_visuals_total: int  # Including synced pages
    affected_visual_ids: List[str]

    # What-if scenarios
    selection_scenarios: List[SlicerSelectionScenario]

    # Recommendations
    recommendations: List[str]


@dataclass
class SyncGroupAnalysis:
    """Analysis of a slicer sync group."""

    sync_group_id: str
    slicers: List[SlicerAggregationImpact]
    affected_pages: List[str]
    total_affected_visuals: int
    worst_case_level: int
    combined_impact: AggregationImpact
    recommendations: List[str]


@dataclass
class PageSlicerSummary:
    """Summary of slicer impacts on a single page."""

    page_id: str
    page_name: str
    slicers_on_page: int
    synced_slicers: int
    worst_case_aggregation_level: int
    best_case_aggregation_level: int
    visuals_potentially_affected: int
    critical_slicers: List[str]  # Slicers that force base table


@dataclass
class SlicerImpactResult:
    """Complete slicer impact analysis result."""

    total_slicers: int
    slicers_forcing_base: int
    slicers_neutral: int
    slicers_positive: int

    slicer_impacts: List[SlicerAggregationImpact]
    sync_group_analyses: List[SyncGroupAnalysis]
    page_summaries: List[PageSlicerSummary]

    # Global impact assessment
    report_wide_impact: str  # "low", "medium", "high", "critical"
    worst_case_hit_rate: float  # If all slicers select detail values
    best_case_hit_rate: float  # If all slicers allow aggregation

    # Top recommendations
    priority_recommendations: List[str]


class SlicerImpactAnalyzer:
    """Analyzes slicer impact on aggregation levels."""

    def __init__(
        self,
        agg_tables: List[AggregationTable],
        agg_level_measures: List[AggLevelMeasure],
        report_summary: ReportAggregationSummary,
        slicers: Optional[List[SlicerInfo]] = None,
    ):
        """
        Initialize the slicer impact analyzer.

        Args:
            agg_tables: Detected aggregation tables
            agg_level_measures: Detected aggregation level measures
            report_summary: Complete report analysis result
            slicers: Extracted slicer information (optional, will extract from pages if not provided)
        """
        self.agg_tables = agg_tables
        self.agg_level_measures = agg_level_measures
        self.report_summary = report_summary

        # Extract slicers from pages if not provided
        if slicers:
            self.slicers = slicers
        else:
            self.slicers = self._extract_all_slicers()

        # Build lookup maps
        self._detail_triggers: Set[str] = set()
        self._mid_level_triggers: Set[str] = set()
        if agg_level_measures:
            self._detail_triggers = set(agg_level_measures[0].detail_trigger_columns)
            self._mid_level_triggers = set(agg_level_measures[0].mid_level_trigger_columns)

        # Build grain column sets
        self._agg_grains: Dict[int, Set[str]] = {}
        for table in agg_tables:
            self._agg_grains[table.level] = set(table.grain_columns)

        # Build page visual map
        self._page_visuals: Dict[str, List[VisualAggregationAnalysis]] = {}
        for page in report_summary.pages:
            self._page_visuals[page.page_id] = page.visuals

    def _extract_all_slicers(self) -> List[SlicerInfo]:
        """Extract all slicers from report pages."""
        all_slicers: List[SlicerInfo] = []
        for page in self.report_summary.pages:
            all_slicers.extend(page.slicers)
        return all_slicers

    def analyze(self) -> SlicerImpactResult:
        """
        Perform complete slicer impact analysis.

        Returns:
            SlicerImpactResult with all findings
        """
        logger.info("Starting slicer impact analysis")

        if not self.slicers:
            return self._empty_result()

        # Analyze each slicer
        slicer_impacts = [self._analyze_slicer(slicer) for slicer in self.slicers]

        # Analyze sync groups
        sync_group_analyses = self._analyze_sync_groups(slicer_impacts)

        # Generate page summaries
        page_summaries = self._generate_page_summaries(slicer_impacts)

        # Calculate global metrics
        slicers_forcing_base = sum(
            1 for s in slicer_impacts if s.impact_level == AggregationImpact.CRITICAL
        )
        slicers_neutral = sum(
            1
            for s in slicer_impacts
            if s.impact_level in [AggregationImpact.NEUTRAL, AggregationImpact.NONE]
        )
        slicers_positive = sum(
            1 for s in slicer_impacts if s.impact_level == AggregationImpact.POSITIVE
        )

        # Calculate worst/best case hit rates
        worst_case, best_case = self._calculate_scenario_hit_rates(slicer_impacts)

        # Determine report-wide impact
        report_wide_impact = self._determine_report_impact(slicer_impacts)

        # Generate priority recommendations
        priority_recommendations = self._generate_recommendations(
            slicer_impacts, sync_group_analyses
        )

        return SlicerImpactResult(
            total_slicers=len(self.slicers),
            slicers_forcing_base=slicers_forcing_base,
            slicers_neutral=slicers_neutral,
            slicers_positive=slicers_positive,
            slicer_impacts=slicer_impacts,
            sync_group_analyses=sync_group_analyses,
            page_summaries=page_summaries,
            report_wide_impact=report_wide_impact,
            worst_case_hit_rate=worst_case,
            best_case_hit_rate=best_case,
            priority_recommendations=priority_recommendations,
        )

    def _empty_result(self) -> SlicerImpactResult:
        """Return empty result when no slicers exist."""
        return SlicerImpactResult(
            total_slicers=0,
            slicers_forcing_base=0,
            slicers_neutral=0,
            slicers_positive=0,
            slicer_impacts=[],
            sync_group_analyses=[],
            page_summaries=[],
            report_wide_impact="none",
            worst_case_hit_rate=self._current_hit_rate(),
            best_case_hit_rate=self._current_hit_rate(),
            priority_recommendations=["No slicers detected in report"],
        )

    def _current_hit_rate(self) -> float:
        """Calculate current hit rate from report summary."""
        total = self.report_summary.visuals_analyzed
        using_agg = total - self.report_summary.agg_level_breakdown.get(1, 0)
        return (using_agg / total * 100) if total > 0 else 0

    def _analyze_slicer(self, slicer: SlicerInfo) -> SlicerAggregationImpact:
        """Analyze a single slicer's impact on aggregation."""
        full_column_ref = f"{slicer.entity}[{slicer.column}]"

        # Determine impact level
        triggers_detail = full_column_ref in self._detail_triggers or self._is_detail_trigger(
            full_column_ref
        )
        triggers_mid = full_column_ref in self._mid_level_triggers

        # Check if column is in any aggregation grain
        is_in_grain = any(full_column_ref in grains for grains in self._agg_grains.values())

        # Determine impact level
        if triggers_detail:
            impact_level = AggregationImpact.CRITICAL
        elif triggers_mid:
            impact_level = AggregationImpact.NEUTRAL
        elif is_in_grain:
            impact_level = AggregationImpact.POSITIVE
        else:
            # Column not in any trigger list - might still affect things
            impact_level = self._infer_impact_level(slicer)

        # Find affected visuals
        affected_visuals = self._find_affected_visuals(slicer)

        # Determine slicer type
        slicer_type = self._determine_slicer_type(slicer)

        # Get synced pages
        synced_pages = slicer.affects_pages if slicer.affects_pages else [slicer.page_id]

        # Calculate affected totals
        affected_on_page = len([v for v in affected_visuals if v.page_id == slicer.page_id])
        affected_total = len(affected_visuals)

        # Generate what-if scenarios
        scenarios = self._generate_selection_scenarios(
            slicer, full_column_ref, triggers_detail, triggers_mid, affected_total
        )

        # Generate recommendations
        recommendations = self._generate_slicer_recommendations(
            slicer, impact_level, triggers_detail, is_in_grain
        )

        # Get page name
        page_name = slicer.page_id
        for page in self.report_summary.pages:
            if page.page_id == slicer.page_id:
                page_name = page.page_name
                break

        return SlicerAggregationImpact(
            slicer_id=slicer.slicer_id,
            page_id=slicer.page_id,
            page_name=page_name,
            entity=slicer.entity,
            column=slicer.column,
            full_column_ref=full_column_ref,
            slicer_type=slicer_type,
            sync_group=slicer.sync_group,
            synced_pages=synced_pages,
            impact_level=impact_level,
            triggers_detail_level=triggers_detail,
            triggers_mid_level=triggers_mid,
            is_in_aggregation_grain=is_in_grain,
            affected_visuals_on_page=affected_on_page,
            affected_visuals_total=affected_total,
            affected_visual_ids=[v.visual_id for v in affected_visuals],
            selection_scenarios=scenarios,
            recommendations=recommendations,
        )

    def _is_detail_trigger(self, column_ref: str) -> bool:
        """
        Check if column is likely a detail-level trigger based on patterns.

        Used when explicit trigger list is not available.
        """
        col_lower = column_ref.lower()

        # High-cardinality patterns that typically force detail
        detail_patterns = [
            "customerid",
            "customer_id",
            "customerkey",
            "productid",
            "product_id",
            "productkey",
            "sku",
            "transactionid",
            "transaction_id",
            "orderid",
            "order_id",
            "invoiceid",
            "invoice_id",
            "lineid",
            "line_id",
            "employeeid",
            "employee_id",
            "employeekey",
            "date",
            "datetime",
            "timestamp",
        ]

        for pattern in detail_patterns:
            if pattern in col_lower:
                return True

        return False

    def _infer_impact_level(self, slicer: SlicerInfo) -> AggregationImpact:
        """Infer impact level when not explicitly in trigger lists."""
        col_lower = slicer.column.lower()

        # Patterns that suggest aggregation-friendly
        if any(
            p in col_lower for p in ["year", "quarter", "month", "category", "region", "segment"]
        ):
            return AggregationImpact.POSITIVE

        # Patterns that suggest potential issues
        if any(p in col_lower for p in ["name", "id", "code", "key"]):
            return AggregationImpact.NEGATIVE

        return AggregationImpact.NEUTRAL

    def _find_affected_visuals(self, slicer: SlicerInfo) -> List[VisualAggregationAnalysis]:
        """Find visuals affected by a slicer."""
        affected: List[VisualAggregationAnalysis] = []

        # Get pages affected by this slicer
        affected_pages = set(slicer.affects_pages) if slicer.affects_pages else {slicer.page_id}
        affected_pages.add(slicer.page_id)

        for page_id in affected_pages:
            visuals = self._page_visuals.get(page_id, [])
            for visual in visuals:
                # All visuals on the page are potentially affected by the slicer
                affected.append(visual)

        return affected

    def _determine_slicer_type(self, slicer: SlicerInfo) -> SlicerType:
        """Determine the type of slicer."""
        slicer_type_str = slicer.slicer_type.lower() if slicer.slicer_type else ""
        col_lower = slicer.column.lower()

        if "date" in col_lower:
            if "range" in slicer_type_str:
                return SlicerType.DATE_RANGE
            if "relative" in slicer_type_str:
                return SlicerType.RELATIVE_DATE
            return SlicerType.DATE_RANGE  # Default for date columns

        if "range" in slicer_type_str or "slider" in slicer_type_str:
            return SlicerType.RANGE

        if "dropdown" in slicer_type_str:
            return SlicerType.DROPDOWN

        if "list" in slicer_type_str:
            return SlicerType.LIST

        if "hierarchy" in slicer_type_str or "tree" in slicer_type_str:
            return SlicerType.HIERARCHY

        return SlicerType.UNKNOWN

    def _generate_selection_scenarios(
        self,
        slicer: SlicerInfo,
        column_ref: str,
        triggers_detail: bool,
        triggers_mid: bool,
        affected_visuals: int,
    ) -> List[SlicerSelectionScenario]:
        """Generate what-if scenarios for different slicer selections."""
        scenarios: List[SlicerSelectionScenario] = []
        col_lower = slicer.column.lower()

        # Scenario: No selection / Select All
        scenarios.append(
            SlicerSelectionScenario(
                selection_type="all",
                aggregation_level=3 if self.agg_tables else 1,
                aggregation_table=self.agg_tables[0].name if self.agg_tables else None,
                affected_visuals_count=affected_visuals,
                impact=AggregationImpact.POSITIVE,
                description="With 'Select All' or no selection, highest aggregation level can be used",
            )
        )

        # Scenario: Single value selection
        if triggers_detail:
            scenarios.append(
                SlicerSelectionScenario(
                    selection_type="single",
                    aggregation_level=1,
                    aggregation_table=None,
                    affected_visuals_count=affected_visuals,
                    impact=AggregationImpact.CRITICAL,
                    description=f"Selecting any value from {column_ref} forces base table for {affected_visuals} visuals",
                )
            )
        elif triggers_mid:
            scenarios.append(
                SlicerSelectionScenario(
                    selection_type="single",
                    aggregation_level=2,
                    aggregation_table=self._get_table_for_level(2),
                    affected_visuals_count=affected_visuals,
                    impact=AggregationImpact.NEUTRAL,
                    description="Single selection allows mid-level aggregation",
                )
            )
        else:
            # Check if it's a time dimension with different granularities
            if "year" in col_lower:
                scenarios.append(
                    SlicerSelectionScenario(
                        selection_type="single",
                        aggregation_level=3,
                        aggregation_table=self._get_table_for_level(3),
                        affected_visuals_count=affected_visuals,
                        impact=AggregationImpact.POSITIVE,
                        description="Year selection maintains high-level aggregation",
                    )
                )
            elif "month" in col_lower:
                scenarios.append(
                    SlicerSelectionScenario(
                        selection_type="single",
                        aggregation_level=2,
                        aggregation_table=self._get_table_for_level(2),
                        affected_visuals_count=affected_visuals,
                        impact=AggregationImpact.NEUTRAL,
                        description="Month selection allows mid-level aggregation",
                    )
                )
            elif "day" in col_lower or "date" in col_lower:
                scenarios.append(
                    SlicerSelectionScenario(
                        selection_type="single",
                        aggregation_level=1,
                        aggregation_table=None,
                        affected_visuals_count=affected_visuals,
                        impact=AggregationImpact.CRITICAL,
                        description="Day/date selection forces base table usage",
                    )
                )

        return scenarios

    def _get_table_for_level(self, level: int) -> Optional[str]:
        """Get aggregation table name for a given level."""
        for table in self.agg_tables:
            if table.level == level:
                return table.name
        return None

    def _generate_slicer_recommendations(
        self,
        slicer: SlicerInfo,
        impact_level: AggregationImpact,
        triggers_detail: bool,
        is_in_grain: bool,
    ) -> List[str]:
        """Generate recommendations for a slicer."""
        recommendations: List[str] = []
        col_ref = f"{slicer.entity}[{slicer.column}]"

        if impact_level == AggregationImpact.CRITICAL:
            if "date" in slicer.column.lower():
                recommendations.append(
                    f"Consider using a date hierarchy slicer instead of {col_ref} "
                    f"to allow users to select at year/quarter/month level"
                )
            else:
                recommendations.append(
                    f"Slicer on {col_ref} forces base table for all affected visuals. "
                    f"Consider replacing with a higher-level grouping column"
                )

        if impact_level == AggregationImpact.NEGATIVE and not is_in_grain:
            recommendations.append(
                f"Add {col_ref} to aggregation table grain if this slicer is frequently used"
            )

        if slicer.sync_group:
            recommendations.append(
                "This slicer is synced across pages - impact affects all synced pages"
            )

        return recommendations

    def _analyze_sync_groups(
        self, slicer_impacts: List[SlicerAggregationImpact]
    ) -> List[SyncGroupAnalysis]:
        """Analyze slicer sync groups."""
        sync_groups: Dict[str, List[SlicerAggregationImpact]] = {}

        for impact in slicer_impacts:
            if impact.sync_group:
                if impact.sync_group not in sync_groups:
                    sync_groups[impact.sync_group] = []
                sync_groups[impact.sync_group].append(impact)

        analyses: List[SyncGroupAnalysis] = []

        for group_id, slicers in sync_groups.items():
            affected_pages = set()
            total_visuals = 0
            worst_level = 3

            for slicer in slicers:
                affected_pages.update(slicer.synced_pages)
                total_visuals += slicer.affected_visuals_total
                if slicer.triggers_detail_level:
                    worst_level = 1
                elif slicer.triggers_mid_level and worst_level > 2:
                    worst_level = 2

            # Determine combined impact
            if worst_level == 1:
                combined_impact = AggregationImpact.CRITICAL
            elif worst_level == 2:
                combined_impact = AggregationImpact.NEUTRAL
            else:
                combined_impact = AggregationImpact.POSITIVE

            recommendations = []
            if combined_impact == AggregationImpact.CRITICAL:
                recommendations.append(
                    f"Sync group '{group_id}' contains slicers that force base table usage. "
                    f"Consider separating high-impact slicers or adding alternative views"
                )

            analyses.append(
                SyncGroupAnalysis(
                    sync_group_id=group_id,
                    slicers=slicers,
                    affected_pages=list(affected_pages),
                    total_affected_visuals=total_visuals,
                    worst_case_level=worst_level,
                    combined_impact=combined_impact,
                    recommendations=recommendations,
                )
            )

        return analyses

    def _generate_page_summaries(
        self, slicer_impacts: List[SlicerAggregationImpact]
    ) -> List[PageSlicerSummary]:
        """Generate slicer impact summaries per page."""
        page_data: Dict[str, Dict[str, Any]] = {}

        for page in self.report_summary.pages:
            page_data[page.page_id] = {
                "page_name": page.page_name,
                "slicers_on_page": 0,
                "synced_slicers": 0,
                "worst_level": 3,
                "best_level": 1,
                "affected_visuals": page.visuals_analyzed,
                "critical_slicers": [],
            }

        for impact in slicer_impacts:
            page_id = impact.page_id
            if page_id in page_data:
                page_data[page_id]["slicers_on_page"] += 1
                if impact.sync_group:
                    page_data[page_id]["synced_slicers"] += 1
                if impact.triggers_detail_level:
                    page_data[page_id]["worst_level"] = 1
                    page_data[page_id]["critical_slicers"].append(impact.full_column_ref)
                elif impact.triggers_mid_level and page_data[page_id]["worst_level"] > 2:
                    page_data[page_id]["worst_level"] = 2

        summaries: List[PageSlicerSummary] = []
        for page_id, data in page_data.items():
            summaries.append(
                PageSlicerSummary(
                    page_id=page_id,
                    page_name=data["page_name"],
                    slicers_on_page=data["slicers_on_page"],
                    synced_slicers=data["synced_slicers"],
                    worst_case_aggregation_level=data["worst_level"],
                    best_case_aggregation_level=3 if self.agg_tables else 1,
                    visuals_potentially_affected=data["affected_visuals"],
                    critical_slicers=data["critical_slicers"],
                )
            )

        return summaries

    def _calculate_scenario_hit_rates(
        self, slicer_impacts: List[SlicerAggregationImpact]
    ) -> Tuple[float, float]:
        """Calculate worst and best case hit rates."""
        total_visuals = self.report_summary.visuals_analyzed
        if total_visuals == 0:
            return 0, 0

        # Worst case: all critical slicers are selecting detail values
        critical_affected = sum(
            impact.affected_visuals_total
            for impact in slicer_impacts
            if impact.impact_level == AggregationImpact.CRITICAL
        )
        # Cap at total visuals
        critical_affected = min(critical_affected, total_visuals)
        worst_case = (total_visuals - critical_affected) / total_visuals * 100

        # Best case: no slicers are filtering (all visuals can use aggregation)
        best_case = 100.0 if self.agg_tables else 0

        return worst_case, best_case

    def _determine_report_impact(self, slicer_impacts: List[SlicerAggregationImpact]) -> str:
        """Determine overall report-wide impact level."""
        critical_count = sum(
            1 for s in slicer_impacts if s.impact_level == AggregationImpact.CRITICAL
        )
        negative_count = sum(
            1 for s in slicer_impacts if s.impact_level == AggregationImpact.NEGATIVE
        )

        total = len(slicer_impacts)
        if total == 0:
            return "none"

        critical_ratio = critical_count / total

        if critical_ratio > 0.5:
            return "critical"
        elif critical_ratio > 0.25 or (critical_count + negative_count) > total * 0.5:
            return "high"
        elif critical_count > 0:
            return "medium"
        else:
            return "low"

    def _generate_recommendations(
        self,
        slicer_impacts: List[SlicerAggregationImpact],
        sync_analyses: List[SyncGroupAnalysis],
    ) -> List[str]:
        """Generate priority recommendations."""
        recommendations: List[str] = []

        # Critical slicers
        critical_slicers = [
            s for s in slicer_impacts if s.impact_level == AggregationImpact.CRITICAL
        ]
        if critical_slicers:
            cols = [s.full_column_ref for s in critical_slicers[:3]]
            recommendations.append(
                f"[CRITICAL] {len(critical_slicers)} slicer(s) force base table usage: {', '.join(cols)}. "
                f"Consider replacing with aggregated dimension slicers"
            )

        # Sync group issues
        problematic_groups = [
            g for g in sync_analyses if g.combined_impact == AggregationImpact.CRITICAL
        ]
        if problematic_groups:
            recommendations.append(
                f"[HIGH] {len(problematic_groups)} sync group(s) contain critical slicers affecting multiple pages"
            )

        # Date hierarchy recommendation
        date_detail_slicers = [
            s for s in slicer_impacts if s.triggers_detail_level and "date" in s.column.lower()
        ]
        if date_detail_slicers:
            recommendations.append(
                "[MEDIUM] Consider using date hierarchy slicers (Year > Quarter > Month) "
                "instead of individual date slicers to allow aggregation at higher levels"
            )

        # Positive reinforcement
        positive_slicers = [
            s for s in slicer_impacts if s.impact_level == AggregationImpact.POSITIVE
        ]
        if positive_slicers and len(positive_slicers) == len(slicer_impacts):
            recommendations.append(
                "[INFO] All slicers are aggregation-friendly! "
                "Consider documenting this best practice for future report development"
            )

        return recommendations[:10]
