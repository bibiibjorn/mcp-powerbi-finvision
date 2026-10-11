"""
Aggregation Hit Rate Analyzer Module

Analyzes how effectively aggregation tables are being utilized in the report.
Provides hit rate analysis, miss reasons categorization, and opportunity ranking.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Any, Set, Tuple, TYPE_CHECKING
from enum import Enum
from collections import defaultdict

from .aggregation_detector import (
    AggregationTable,
    AggLevelMeasure,
    AggAwareMeasure,
)
from .filter_context_analyzer import FilterSourceType

# Use TYPE_CHECKING to avoid circular imports
if TYPE_CHECKING:
    from .aggregation_analyzer import (  # noqa: F401
        VisualAggregationAnalysis,
        PageAggregationSummary,
        ReportAggregationSummary,
    )

logger = logging.getLogger(__name__)


class MissReason(Enum):
    """Categories of why a visual misses an aggregation table."""

    DETAIL_COLUMN_FILTER = "detail_column_filter"  # Column in filter forces base table
    DIMENSION_NOT_IN_AGG = "dimension_not_in_agg"  # Dimension not included in agg table
    MEASURE_NOT_AGG_AWARE = "measure_not_agg_aware"  # Measure doesn't support aggregation
    CROSS_FILTER_PROPAGATION = "cross_filter_propagation"  # Cross-filter from another visual
    SLICER_SELECTION = "slicer_selection"  # Slicer forces detail level
    DRILLTHROUGH_FILTER = "drillthrough_filter"  # Drillthrough context
    NO_AGG_LEVEL_MEASURE = "no_agg_level_measure"  # No routing measure exists
    UNKNOWN = "unknown"


@dataclass
class MissDetails:
    """Details about why a visual missed using an aggregation table."""

    visual_id: str
    visual_title: Optional[str]
    page_name: str
    reason: MissReason
    reason_description: str
    triggering_columns: List[str]
    could_use_table: str  # Which agg table it could have used
    actual_table: str  # What table it actually uses


@dataclass
class ColumnMissImpact:
    """Impact analysis for a specific column causing misses."""

    column_ref: str  # Table[Column]
    table_name: str
    column_name: str
    miss_count: int
    affected_visuals: List[str]
    affected_pages: Set[str]
    source_types: Set[str]  # How the column enters context (slicer, filter, field)
    potential_improvement: float  # % hit rate improvement if added to agg


@dataclass
class TableHitRate:
    """Hit rate analysis for a specific aggregation table."""

    table_name: str
    level: int
    total_eligible_visuals: int  # Visuals that could potentially use this table
    actual_hits: int  # Visuals that actually use this table
    hit_rate: float  # Percentage (0-100)
    misses: List[MissDetails]
    miss_breakdown: Dict[MissReason, int]  # Reason -> count
    top_miss_columns: List[ColumnMissImpact]  # Columns causing most misses


@dataclass
class PageHitRate:
    """Hit rate analysis for a page."""

    page_id: str
    page_name: str
    total_visuals: int
    using_aggregation: int  # Visuals using any agg table
    using_base_table: int
    page_hit_rate: float
    primary_miss_reasons: List[Tuple[MissReason, int]]


@dataclass
class OpportunityRanking:
    """Ranked opportunity for improving aggregation hit rate."""

    rank: int
    opportunity_type: str  # "add_column_to_agg", "create_agg_aware_measure", etc.
    description: str
    affected_column: Optional[str]
    affected_table: Optional[str]
    visuals_impacted: int
    estimated_hit_rate_improvement: float  # Percentage points
    implementation_effort: str  # "low", "medium", "high"
    priority_score: float  # Higher = more impactful


@dataclass
class HitRateAnalysisResult:
    """Complete hit rate analysis result."""

    overall_hit_rate: float  # Percentage of visuals using aggregation
    total_visuals: int
    visuals_using_aggregation: int
    visuals_using_base: int
    table_hit_rates: List[TableHitRate]
    page_hit_rates: List[PageHitRate]
    all_misses: List[MissDetails]
    miss_summary: Dict[MissReason, int]
    top_miss_columns: List[ColumnMissImpact]
    opportunity_rankings: List[OpportunityRanking]
    potential_hit_rate: float  # If top opportunities were implemented


class AggregationHitRateAnalyzer:
    """Analyzes aggregation table hit rates and miss reasons."""

    def __init__(
        self,
        agg_tables: List[AggregationTable],
        agg_level_measures: List[AggLevelMeasure],
        agg_aware_measures: List[AggAwareMeasure],
        report_summary: ReportAggregationSummary,
    ):
        """
        Initialize the hit rate analyzer.

        Args:
            agg_tables: Detected aggregation tables
            agg_level_measures: Detected aggregation level measures
            agg_aware_measures: Detected aggregation-aware measures
            report_summary: Complete report analysis result
        """
        self.agg_tables = agg_tables
        self.agg_level_measures = agg_level_measures
        self.agg_aware_measures = agg_aware_measures
        self.report_summary = report_summary

        # Build lookup maps
        self._table_by_level: Dict[int, AggregationTable] = {t.level: t for t in agg_tables}
        self._agg_aware_names: Set[str] = {m.name for m in agg_aware_measures}

        # Get detail trigger columns from level measure
        self._detail_triggers: Set[str] = set()
        self._mid_level_triggers: Set[str] = set()
        if agg_level_measures:
            self._detail_triggers = set(agg_level_measures[0].detail_trigger_columns)
            self._mid_level_triggers = set(agg_level_measures[0].mid_level_trigger_columns)

        # Build grain column sets for each aggregation table
        self._table_grains: Dict[str, Set[str]] = {}
        for table in agg_tables:
            self._table_grains[table.name] = set(table.grain_columns)

    def analyze(self) -> HitRateAnalysisResult:
        """
        Perform complete hit rate analysis.

        Returns:
            HitRateAnalysisResult with all findings
        """
        logger.info("Starting aggregation hit rate analysis")

        # Collect all visual analyses
        all_visuals: List[VisualAggregationAnalysis] = []
        for page in self.report_summary.pages:
            all_visuals.extend(page.visuals)

        total_visuals = len(all_visuals)
        if total_visuals == 0:
            return self._empty_result()

        # Categorize visuals
        visuals_using_agg = [v for v in all_visuals if v.determined_agg_level > 1]
        visuals_using_base = [v for v in all_visuals if v.determined_agg_level == 1]

        # Analyze table-level hit rates
        table_hit_rates = self._analyze_table_hit_rates(all_visuals)

        # Analyze page-level hit rates
        page_hit_rates = self._analyze_page_hit_rates()

        # Collect all misses with details
        all_misses = self._collect_all_misses(visuals_using_base)

        # Summarize miss reasons
        miss_summary = self._summarize_miss_reasons(all_misses)

        # Analyze column miss impact
        top_miss_columns = self._analyze_column_miss_impact(all_misses)

        # Generate opportunity rankings
        opportunity_rankings = self._generate_opportunity_rankings(
            all_misses, top_miss_columns, total_visuals
        )

        # Calculate potential hit rate if top opportunities implemented
        potential_hit_rate = self._estimate_potential_hit_rate(
            len(visuals_using_agg), total_visuals, opportunity_rankings
        )

        overall_hit_rate = (
            (len(visuals_using_agg) / total_visuals * 100) if total_visuals > 0 else 0
        )

        return HitRateAnalysisResult(
            overall_hit_rate=overall_hit_rate,
            total_visuals=total_visuals,
            visuals_using_aggregation=len(visuals_using_agg),
            visuals_using_base=len(visuals_using_base),
            table_hit_rates=table_hit_rates,
            page_hit_rates=page_hit_rates,
            all_misses=all_misses,
            miss_summary=miss_summary,
            top_miss_columns=top_miss_columns,
            opportunity_rankings=opportunity_rankings,
            potential_hit_rate=potential_hit_rate,
        )

    def _empty_result(self) -> HitRateAnalysisResult:
        """Return empty result when no visuals to analyze."""
        return HitRateAnalysisResult(
            overall_hit_rate=0,
            total_visuals=0,
            visuals_using_aggregation=0,
            visuals_using_base=0,
            table_hit_rates=[],
            page_hit_rates=[],
            all_misses=[],
            miss_summary={},
            top_miss_columns=[],
            opportunity_rankings=[],
            potential_hit_rate=0,
        )

    def _analyze_table_hit_rates(
        self, all_visuals: List[VisualAggregationAnalysis]
    ) -> List[TableHitRate]:
        """Analyze hit rate for each aggregation table."""
        table_hit_rates: List[TableHitRate] = []

        for agg_table in self.agg_tables:
            # Count visuals using this table
            using_table = [v for v in all_visuals if v.determined_agg_table == agg_table.name]

            # Count visuals that could potentially use this table
            # (using base table but at a level that this table could serve)
            potentially_eligible = [
                v for v in all_visuals if v.determined_agg_level <= agg_table.level
            ]

            # Visuals that miss this table (use base when they could use this)
            misses = [v for v in all_visuals if v.determined_agg_level == 1 and agg_table.level > 1]

            # Analyze miss reasons
            miss_details: List[MissDetails] = []
            miss_breakdown: Dict[MissReason, int] = defaultdict(int)
            column_miss_counts: Dict[str, List[VisualAggregationAnalysis]] = defaultdict(list)

            for visual in misses:
                reason, description, triggering_cols = self._determine_miss_reason(
                    visual, agg_table
                )
                miss_details.append(
                    MissDetails(
                        visual_id=visual.visual_id,
                        visual_title=visual.visual_title,
                        page_name=visual.page_name,
                        reason=reason,
                        reason_description=description,
                        triggering_columns=triggering_cols,
                        could_use_table=agg_table.name,
                        actual_table="Base Table",
                    )
                )
                miss_breakdown[reason] += 1

                # Track which columns cause misses
                for col in triggering_cols:
                    column_miss_counts[col].append(visual)

            # Get top miss columns for this table
            top_columns: List[ColumnMissImpact] = []
            for col, visuals_affected in sorted(
                column_miss_counts.items(), key=lambda x: -len(x[1])
            )[:5]:
                table_name = col.split("[")[0].strip("'") if "[" in col else ""
                col_name = col.split("[")[-1].rstrip("]") if "[" in col else col

                top_columns.append(
                    ColumnMissImpact(
                        column_ref=col,
                        table_name=table_name,
                        column_name=col_name,
                        miss_count=len(visuals_affected),
                        affected_visuals=[v.visual_id for v in visuals_affected],
                        affected_pages={v.page_name for v in visuals_affected},
                        source_types=self._get_column_source_types(col, visuals_affected),
                        potential_improvement=(
                            len(visuals_affected) / len(all_visuals) * 100 if all_visuals else 0
                        ),
                    )
                )

            total_eligible = len(potentially_eligible)
            actual_hits = len(using_table)
            hit_rate = (actual_hits / total_eligible * 100) if total_eligible > 0 else 0

            table_hit_rates.append(
                TableHitRate(
                    table_name=agg_table.name,
                    level=agg_table.level,
                    total_eligible_visuals=total_eligible,
                    actual_hits=actual_hits,
                    hit_rate=hit_rate,
                    misses=miss_details,
                    miss_breakdown=dict(miss_breakdown),
                    top_miss_columns=top_columns,
                )
            )

        return table_hit_rates

    def _analyze_page_hit_rates(self) -> List[PageHitRate]:
        """Analyze hit rate for each page."""
        page_hit_rates: List[PageHitRate] = []

        for page in self.report_summary.pages:
            using_agg = sum(1 for v in page.visuals if v.determined_agg_level > 1)
            using_base = sum(1 for v in page.visuals if v.determined_agg_level == 1)
            total = page.visuals_analyzed

            hit_rate = (using_agg / total * 100) if total > 0 else 0

            # Determine primary miss reasons for this page
            miss_reasons: Dict[MissReason, int] = defaultdict(int)
            for visual in page.visuals:
                if visual.determined_agg_level == 1:
                    reason, _, _ = self._determine_miss_reason(visual, None)
                    miss_reasons[reason] += 1

            primary_reasons = sorted(miss_reasons.items(), key=lambda x: -x[1])[:3]

            page_hit_rates.append(
                PageHitRate(
                    page_id=page.page_id,
                    page_name=page.page_name,
                    total_visuals=total,
                    using_aggregation=using_agg,
                    using_base_table=using_base,
                    page_hit_rate=hit_rate,
                    primary_miss_reasons=primary_reasons,
                )
            )

        return page_hit_rates

    def _determine_miss_reason(
        self,
        visual: VisualAggregationAnalysis,
        target_table: Optional[AggregationTable],
    ) -> Tuple[MissReason, str, List[str]]:
        """Determine why a visual missed using aggregation."""
        triggering_cols: List[str] = []

        # Check for detail trigger columns
        if visual.filter_context.has_detail_triggers:
            triggering_cols = visual.filter_context.detail_trigger_columns
            return (
                MissReason.DETAIL_COLUMN_FILTER,
                f"Detail columns in filter context: {', '.join(triggering_cols[:3])}",
                triggering_cols,
            )

        # Check if columns in context are in the aggregation grain
        if target_table:
            grain_cols = self._table_grains.get(target_table.name, set())
            for col_ctx in visual.filter_context.all_columns:
                col_ref = f"{col_ctx.table}[{col_ctx.column}]"
                if col_ref not in grain_cols and col_ref not in self._detail_triggers:
                    triggering_cols.append(col_ref)

            if triggering_cols:
                return (
                    MissReason.DIMENSION_NOT_IN_AGG,
                    f"Columns not in aggregation grain: {', '.join(triggering_cols[:3])}",
                    triggering_cols,
                )

        # Check for non-aggregation-aware measures
        non_agg_measures = [m for m in visual.measures_used if m not in self._agg_aware_names]
        if non_agg_measures:
            return (
                MissReason.MEASURE_NOT_AGG_AWARE,
                f"Non-aggregation-aware measures: {', '.join(non_agg_measures[:3])}",
                non_agg_measures,
            )

        # Check for slicer-induced filtering
        slicer_cols = [
            col
            for col in visual.filter_context.all_columns
            if col.source_type == FilterSourceType.SLICER
        ]
        if slicer_cols:
            triggering_cols = [f"{c.table}[{c.column}]" for c in slicer_cols]
            return (
                MissReason.SLICER_SELECTION,
                f"Slicer selection on: {', '.join(triggering_cols[:3])}",
                triggering_cols,
            )

        # Check for cross-filter
        cross_filter_cols = [
            col
            for col in visual.filter_context.all_columns
            if col.source_type == FilterSourceType.CROSS_FILTER
        ]
        if cross_filter_cols:
            triggering_cols = [f"{c.table}[{c.column}]" for c in cross_filter_cols]
            return (
                MissReason.CROSS_FILTER_PROPAGATION,
                f"Cross-filter from: {', '.join(triggering_cols[:3])}",
                triggering_cols,
            )

        # Check if no aggregation level measure exists
        if not self.agg_level_measures:
            return (
                MissReason.NO_AGG_LEVEL_MEASURE,
                "No aggregation level routing measure detected in model",
                [],
            )

        return (
            MissReason.UNKNOWN,
            "Unable to determine specific miss reason",
            [],
        )

    def _get_column_source_types(
        self,
        column_ref: str,
        visuals: List[VisualAggregationAnalysis],
    ) -> Set[str]:
        """Get the source types for how a column enters filter context."""
        source_types: Set[str] = set()

        for visual in visuals:
            for col_ctx in visual.filter_context.all_columns:
                if f"{col_ctx.table}[{col_ctx.column}]" == column_ref:
                    source_types.add(col_ctx.source_type.value)

        return source_types

    def _collect_all_misses(
        self, visuals_using_base: List[VisualAggregationAnalysis]
    ) -> List[MissDetails]:
        """Collect all miss details from visuals using base table."""
        all_misses: List[MissDetails] = []

        # Find the highest level aggregation table to compare against
        if not self.agg_tables:
            return all_misses

        highest_agg = max(self.agg_tables, key=lambda t: t.level)

        for visual in visuals_using_base:
            reason, description, triggering_cols = self._determine_miss_reason(visual, highest_agg)
            all_misses.append(
                MissDetails(
                    visual_id=visual.visual_id,
                    visual_title=visual.visual_title,
                    page_name=visual.page_name,
                    reason=reason,
                    reason_description=description,
                    triggering_columns=triggering_cols,
                    could_use_table=highest_agg.name,
                    actual_table="Base Table",
                )
            )

        return all_misses

    def _summarize_miss_reasons(self, all_misses: List[MissDetails]) -> Dict[MissReason, int]:
        """Summarize miss reasons across all misses."""
        summary: Dict[MissReason, int] = defaultdict(int)
        for miss in all_misses:
            summary[miss.reason] += 1
        return dict(summary)

    def _analyze_column_miss_impact(self, all_misses: List[MissDetails]) -> List[ColumnMissImpact]:
        """Analyze which columns have the most miss impact."""
        column_impacts: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {
                "visuals": [],
                "pages": set(),
                "source_types": set(),
            }
        )

        for miss in all_misses:
            for col in miss.triggering_columns:
                column_impacts[col]["visuals"].append(miss.visual_id)
                column_impacts[col]["pages"].add(miss.page_name)

        # Convert to ColumnMissImpact objects
        total_visuals = self.report_summary.visuals_analyzed
        impact_list: List[ColumnMissImpact] = []

        for col, data in column_impacts.items():
            table_name = col.split("[")[0].strip("'") if "[" in col else ""
            col_name = col.split("[")[-1].rstrip("]") if "[" in col else col

            impact_list.append(
                ColumnMissImpact(
                    column_ref=col,
                    table_name=table_name,
                    column_name=col_name,
                    miss_count=len(data["visuals"]),
                    affected_visuals=data["visuals"],
                    affected_pages=data["pages"],
                    source_types=data["source_types"],
                    potential_improvement=(
                        len(data["visuals"]) / total_visuals * 100 if total_visuals > 0 else 0
                    ),
                )
            )

        # Sort by miss count descending
        impact_list.sort(key=lambda x: -x.miss_count)
        return impact_list[:15]  # Top 15

    def _generate_opportunity_rankings(
        self,
        all_misses: List[MissDetails],
        top_miss_columns: List[ColumnMissImpact],
        total_visuals: int,
    ) -> List[OpportunityRanking]:
        """Generate ranked opportunities for improving hit rate."""
        opportunities: List[OpportunityRanking] = []
        rank = 0

        # Opportunity 1: Add columns to aggregation tables
        for col_impact in top_miss_columns[:5]:
            if col_impact.miss_count >= 2:  # At least 2 visuals affected
                rank += 1
                effort = "medium" if col_impact.miss_count < 5 else "high"

                opportunities.append(
                    OpportunityRanking(
                        rank=rank,
                        opportunity_type="add_column_to_agg",
                        description=f"Add {col_impact.column_ref} to mid-level aggregation table",
                        affected_column=col_impact.column_ref,
                        affected_table=None,  # Would need to determine which agg table
                        visuals_impacted=col_impact.miss_count,
                        estimated_hit_rate_improvement=col_impact.potential_improvement,
                        implementation_effort=effort,
                        priority_score=col_impact.potential_improvement * 1.5,
                    )
                )

        # Opportunity 2: Create aggregation-aware versions of measures
        measure_miss_counts: Dict[str, int] = defaultdict(int)
        for miss in all_misses:
            if miss.reason == MissReason.MEASURE_NOT_AGG_AWARE:
                for measure in miss.triggering_columns:
                    measure_miss_counts[measure] += 1

        for measure, count in sorted(measure_miss_counts.items(), key=lambda x: -x[1])[:3]:
            if count >= 2:
                rank += 1
                opportunities.append(
                    OpportunityRanking(
                        rank=rank,
                        opportunity_type="create_agg_aware_measure",
                        description=f"Create aggregation-aware version of measure '{measure}'",
                        affected_column=None,
                        affected_table=measure,
                        visuals_impacted=count,
                        estimated_hit_rate_improvement=(
                            count / total_visuals * 100 if total_visuals > 0 else 0
                        ),
                        implementation_effort="medium",
                        priority_score=count * 2,
                    )
                )

        # Opportunity 3: Reconsider slicer selections
        slicer_miss_count = sum(
            1 for miss in all_misses if miss.reason == MissReason.SLICER_SELECTION
        )
        if slicer_miss_count >= 3:
            rank += 1
            opportunities.append(
                OpportunityRanking(
                    rank=rank,
                    opportunity_type="optimize_slicers",
                    description="Consider replacing high-cardinality slicers with aggregated dimensions",
                    affected_column=None,
                    affected_table=None,
                    visuals_impacted=slicer_miss_count,
                    estimated_hit_rate_improvement=(
                        slicer_miss_count / total_visuals * 50 if total_visuals > 0 else 0
                    ),
                    implementation_effort="low",
                    priority_score=slicer_miss_count * 1.2,
                )
            )

        # Sort by priority score
        opportunities.sort(key=lambda x: -x.priority_score)

        # Re-assign ranks after sorting
        for i, opp in enumerate(opportunities):
            opp.rank = i + 1

        return opportunities[:10]

    def _estimate_potential_hit_rate(
        self,
        current_hits: int,
        total_visuals: int,
        opportunities: List[OpportunityRanking],
    ) -> float:
        """Estimate potential hit rate if top opportunities were implemented."""
        if total_visuals == 0:
            return 0

        current_rate = current_hits / total_visuals * 100

        # Sum up potential improvements from top 5 opportunities (with diminishing returns)
        potential_improvement = 0
        for i, opp in enumerate(opportunities[:5]):
            # Apply diminishing returns factor
            factor = 0.7**i  # Each subsequent opportunity has less guaranteed impact
            potential_improvement += opp.estimated_hit_rate_improvement * factor

        # Cap at 100%
        return min(100, current_rate + potential_improvement)
