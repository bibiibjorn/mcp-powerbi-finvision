"""
Aggregation Recommender Module

Automatically generates aggregation table recommendations based on report usage patterns.
Can generate TMDL code for new aggregation tables and DAX measures.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Set, Tuple, TYPE_CHECKING
from collections import defaultdict

from .aggregation_detector import AggregationTable, AggLevelMeasure, AggAwareMeasure

# Use TYPE_CHECKING to avoid circular imports
if TYPE_CHECKING:
    from .aggregation_analyzer import (  # noqa: F401
        VisualAggregationAnalysis,
        PageAggregationSummary,
        ReportAggregationSummary,
    )

logger = logging.getLogger(__name__)


@dataclass
class RecommendedGrainColumn:
    """A recommended grain column for an aggregation table."""

    table_name: str
    column_name: str
    full_ref: str  # Table[Column]
    usage_count: int  # How many visuals use this column
    source_types: Set[str]  # How it's used (filter, slicer, axis, etc.)
    cardinality_estimate: str  # "low", "medium", "high"
    time_dimension: bool
    priority: int  # 1=high, 2=medium, 3=low


@dataclass
class RecommendedAggregation:
    """A recommended aggregation for the table."""

    source_table: str
    source_column: str
    aggregation_function: str  # SUM, COUNT, AVERAGE, etc.
    result_column_name: str
    used_in_measures: List[str]
    usage_count: int
    priority: int


@dataclass
class RecommendedAggregationTable:
    """Complete recommendation for a new aggregation table."""

    name: str
    level: int
    level_description: str
    grain_columns: List[RecommendedGrainColumn]
    aggregations: List[RecommendedAggregation]
    estimated_row_count: int
    estimated_compression_ratio: float
    visuals_that_would_benefit: int
    hit_rate_improvement: float
    priority_score: float
    tmdl_code: Optional[str] = None
    dax_expression: Optional[str] = None
    implementation_notes: List[str] = field(default_factory=list)


@dataclass
class RecommendedMeasure:
    """Recommendation for an aggregation-aware measure."""

    measure_name: str
    original_measure_name: str
    table_name: str
    dax_code: str
    level_switches: Dict[int, str]  # level -> column/expression used
    implementation_complexity: str  # "low", "medium", "high"
    notes: List[str]


@dataclass
class RecommendedLevelMeasure:
    """Recommendation for an aggregation level measure."""

    measure_name: str
    table_name: str
    dax_code: str
    detail_triggers: List[str]
    mid_level_triggers: List[str]
    notes: List[str]


@dataclass
class AggregationRecommendationResult:
    """Complete recommendation result."""

    # Existing infrastructure assessment
    has_existing_aggregations: bool
    existing_coverage_score: float

    # Recommended tables
    recommended_tables: List[RecommendedAggregationTable]

    # Recommended measures
    recommended_level_measure: Optional[RecommendedLevelMeasure]
    recommended_agg_aware_measures: List[RecommendedMeasure]

    # Coverage map
    dimension_coverage: Dict[str, List[str]]  # dimension -> [columns covered]
    measure_coverage: Dict[str, bool]  # measure -> is_covered

    # Impact summary
    total_improvement_potential: float  # Hit rate improvement
    estimated_query_reduction: float  # Row reduction percentage
    implementation_effort: str  # "low", "medium", "high"

    # Priority action items
    priority_actions: List[str]


class AggregationRecommender:
    """Generates aggregation recommendations based on report analysis."""

    # Default cardinality estimates for common dimension patterns
    CARDINALITY_PATTERNS = {
        "year": ("low", 10),
        "quarter": ("low", 40),
        "month": ("low", 120),
        "week": ("medium", 520),
        "day": ("high", 3650),
        "date": ("high", 3650),
        "category": ("low", 50),
        "subcategory": ("medium", 200),
        "product": ("high", 5000),
        "customer": ("high", 10000),
        "region": ("low", 20),
        "country": ("low", 200),
        "store": ("medium", 500),
        "channel": ("low", 10),
        "segment": ("low", 20),
    }

    def __init__(
        self,
        agg_tables: List[AggregationTable],
        agg_level_measures: List[AggLevelMeasure],
        agg_aware_measures: List[AggAwareMeasure],
        report_summary: ReportAggregationSummary,
        model_data: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize the recommender.

        Args:
            agg_tables: Existing aggregation tables
            agg_level_measures: Existing level measures
            agg_aware_measures: Existing aggregation-aware measures
            report_summary: Complete report analysis
            model_data: Full model data for additional context
        """
        self.agg_tables = agg_tables
        self.agg_level_measures = agg_level_measures
        self.agg_aware_measures = agg_aware_measures
        self.report_summary = report_summary
        self.model_data = model_data or {}

        # Build lookups
        self._existing_agg_names = {t.name for t in agg_tables}
        self._agg_aware_names = {m.name for m in agg_aware_measures}

        # Analyze report usage patterns
        self._column_usage = self._analyze_column_usage()
        self._measure_usage = self._analyze_measure_usage()

    def recommend(self) -> AggregationRecommendationResult:
        """
        Generate complete aggregation recommendations.

        Returns:
            AggregationRecommendationResult with all recommendations
        """
        logger.info("Generating aggregation recommendations")

        # Assess existing coverage
        has_existing = len(self.agg_tables) > 0
        existing_coverage = self._calculate_existing_coverage()

        # Generate table recommendations
        recommended_tables = self._recommend_aggregation_tables()

        # Generate level measure recommendation (if none exists)
        level_measure = None
        if not self.agg_level_measures:
            level_measure = self._recommend_level_measure()

        # Generate measure recommendations
        agg_aware_measures = self._recommend_agg_aware_measures()

        # Build coverage maps
        dimension_coverage = self._build_dimension_coverage()
        measure_coverage = self._build_measure_coverage()

        # Calculate impact
        total_improvement = sum(t.hit_rate_improvement for t in recommended_tables[:3])
        query_reduction = sum(
            (1 - t.estimated_compression_ratio) * t.hit_rate_improvement / 100
            for t in recommended_tables[:3]
        )

        # Determine implementation effort
        effort = self._determine_implementation_effort(
            recommended_tables, level_measure, agg_aware_measures
        )

        # Generate priority actions
        priority_actions = self._generate_priority_actions(
            recommended_tables, level_measure, agg_aware_measures
        )

        return AggregationRecommendationResult(
            has_existing_aggregations=has_existing,
            existing_coverage_score=existing_coverage,
            recommended_tables=recommended_tables,
            recommended_level_measure=level_measure,
            recommended_agg_aware_measures=agg_aware_measures,
            dimension_coverage=dimension_coverage,
            measure_coverage=measure_coverage,
            total_improvement_potential=total_improvement,
            estimated_query_reduction=query_reduction * 100,
            implementation_effort=effort,
            priority_actions=priority_actions,
        )

    def _analyze_column_usage(self) -> Dict[str, Dict[str, Any]]:
        """Analyze how columns are used across the report."""
        usage: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {
                "count": 0,
                "source_types": set(),
                "pages": set(),
                "visuals": [],
                "triggers_detail": False,
            }
        )

        for page in self.report_summary.pages:
            for visual in page.visuals:
                for col in visual.filter_context.all_columns:
                    col_ref = f"{col.table}[{col.column}]"
                    usage[col_ref]["count"] += 1
                    usage[col_ref]["source_types"].add(col.source_type.value)
                    usage[col_ref]["pages"].add(page.page_name)
                    usage[col_ref]["visuals"].append(visual.visual_id)
                    if col.triggers_detail:
                        usage[col_ref]["triggers_detail"] = True

        return dict(usage)

    def _analyze_measure_usage(self) -> Dict[str, int]:
        """Analyze how measures are used across the report."""
        usage: Dict[str, int] = defaultdict(int)

        for page in self.report_summary.pages:
            for visual in page.visuals:
                for measure in visual.measures_used:
                    usage[measure] += 1

        return dict(usage)

    def _calculate_existing_coverage(self) -> float:
        """Calculate current aggregation coverage score."""
        if not self.report_summary.visuals_analyzed:
            return 0

        using_agg = sum(
            1
            for page in self.report_summary.pages
            for visual in page.visuals
            if visual.determined_agg_level > 1
        )

        return using_agg / self.report_summary.visuals_analyzed * 100

    def _recommend_aggregation_tables(self) -> List[RecommendedAggregationTable]:
        """Generate recommendations for new aggregation tables."""
        recommendations: List[RecommendedAggregationTable] = []

        # Identify frequently used column combinations
        column_combos = self._identify_column_combinations()

        # Generate recommendations for different aggregation levels

        # Level 3: High-level aggregation (Year + Category)
        high_level = self._recommend_high_level_table(column_combos)
        if high_level:
            recommendations.append(high_level)

        # Level 2: Mid-level aggregation (Month + Subcategory)
        mid_level = self._recommend_mid_level_table(column_combos)
        if mid_level:
            recommendations.append(mid_level)

        # Sort by priority score
        recommendations.sort(key=lambda x: -x.priority_score)

        return recommendations

    def _identify_column_combinations(self) -> List[Tuple[Set[str], int]]:
        """Identify frequently occurring column combinations."""
        combo_counts: Dict[frozenset, int] = defaultdict(int)

        for page in self.report_summary.pages:
            for visual in page.visuals:
                # Get non-detail columns used in this visual
                cols = set()
                for col in visual.filter_context.all_columns:
                    col_ref = f"{col.table}[{col.column}]"
                    if not col.triggers_detail:
                        cols.add(col_ref)

                if cols:
                    combo_counts[frozenset(cols)] += 1

        # Convert to list and sort by count
        combos = [(set(k), v) for k, v in combo_counts.items()]
        combos.sort(key=lambda x: -x[1])

        return combos[:10]  # Top 10 combinations

    def _recommend_high_level_table(
        self, column_combos: List[Tuple[Set[str], int]]
    ) -> Optional[RecommendedAggregationTable]:
        """Recommend a high-level aggregation table."""
        # Find time dimensions at year/quarter level
        time_cols = self._find_time_columns(["year", "quarter", "fiscal"])

        # Find categorical dimensions
        category_cols = self._find_categorical_columns(["category", "segment", "region", "channel"])

        if not time_cols and not category_cols:
            return None

        # Build grain columns
        grain_columns: List[RecommendedGrainColumn] = []

        for col_ref in time_cols[:1]:  # Take best time column
            table, column = self._parse_column_ref(col_ref)
            usage = self._column_usage.get(col_ref, {})
            grain_columns.append(
                RecommendedGrainColumn(
                    table_name=table,
                    column_name=column,
                    full_ref=col_ref,
                    usage_count=usage.get("count", 0),
                    source_types=usage.get("source_types", set()),
                    cardinality_estimate="low",
                    time_dimension=True,
                    priority=1,
                )
            )

        for col_ref in category_cols[:2]:  # Take up to 2 category columns
            table, column = self._parse_column_ref(col_ref)
            usage = self._column_usage.get(col_ref, {})
            grain_columns.append(
                RecommendedGrainColumn(
                    table_name=table,
                    column_name=column,
                    full_ref=col_ref,
                    usage_count=usage.get("count", 0),
                    source_types=usage.get("source_types", set()),
                    cardinality_estimate="low",
                    time_dimension=False,
                    priority=2,
                )
            )

        if not grain_columns:
            return None

        # Build aggregations
        aggregations = self._recommend_aggregations()

        # Calculate estimates
        estimated_rows = self._estimate_row_count(grain_columns)
        compression_ratio = estimated_rows / 10_000_000  # Assume 10M base rows

        # Calculate improvement potential
        visuals_benefiting = self._count_visuals_benefiting(grain_columns, level=3)
        hit_rate_improvement = (
            visuals_benefiting / max(1, self.report_summary.visuals_analyzed) * 100
        )

        # Generate TMDL and DAX
        tmdl_code = self._generate_tmdl(grain_columns, aggregations, "Agg_HighLevel")
        dax_expression = self._generate_dax_expression(grain_columns, aggregations)

        return RecommendedAggregationTable(
            name="Agg_HighLevel",
            level=3,
            level_description="Yearly/Quarterly aggregation by Category",
            grain_columns=grain_columns,
            aggregations=aggregations,
            estimated_row_count=estimated_rows,
            estimated_compression_ratio=compression_ratio,
            visuals_that_would_benefit=visuals_benefiting,
            hit_rate_improvement=hit_rate_improvement,
            priority_score=hit_rate_improvement * (1 - compression_ratio),
            tmdl_code=tmdl_code,
            dax_expression=dax_expression,
            implementation_notes=[
                "Creates a high-level aggregation for strategic dashboards",
                f"Estimated {estimated_rows:,} rows ({compression_ratio:.2%} of base)",
                f"Would improve hit rate by {hit_rate_improvement:.1f}%",
            ],
        )

    def _recommend_mid_level_table(
        self, column_combos: List[Tuple[Set[str], int]]
    ) -> Optional[RecommendedAggregationTable]:
        """Recommend a mid-level aggregation table."""
        # Find time dimensions at month level
        time_cols = self._find_time_columns(["month", "period", "week"])

        # Find categorical dimensions at subcategory level
        category_cols = self._find_categorical_columns(
            ["subcategory", "product", "store", "territory"]
        )

        if not time_cols:
            return None

        grain_columns: List[RecommendedGrainColumn] = []

        for col_ref in time_cols[:1]:
            table, column = self._parse_column_ref(col_ref)
            usage = self._column_usage.get(col_ref, {})
            grain_columns.append(
                RecommendedGrainColumn(
                    table_name=table,
                    column_name=column,
                    full_ref=col_ref,
                    usage_count=usage.get("count", 0),
                    source_types=usage.get("source_types", set()),
                    cardinality_estimate="low",
                    time_dimension=True,
                    priority=1,
                )
            )

        for col_ref in category_cols[:2]:
            table, column = self._parse_column_ref(col_ref)
            usage = self._column_usage.get(col_ref, {})
            cardinality = self._estimate_cardinality(column)
            grain_columns.append(
                RecommendedGrainColumn(
                    table_name=table,
                    column_name=column,
                    full_ref=col_ref,
                    usage_count=usage.get("count", 0),
                    source_types=usage.get("source_types", set()),
                    cardinality_estimate=cardinality,
                    time_dimension=False,
                    priority=2,
                )
            )

        if len(grain_columns) < 2:
            return None

        aggregations = self._recommend_aggregations()
        estimated_rows = self._estimate_row_count(grain_columns)
        compression_ratio = estimated_rows / 10_000_000

        visuals_benefiting = self._count_visuals_benefiting(grain_columns, level=2)
        hit_rate_improvement = (
            visuals_benefiting / max(1, self.report_summary.visuals_analyzed) * 100
        )

        tmdl_code = self._generate_tmdl(grain_columns, aggregations, "Agg_MidLevel")
        dax_expression = self._generate_dax_expression(grain_columns, aggregations)

        return RecommendedAggregationTable(
            name="Agg_MidLevel",
            level=2,
            level_description="Monthly aggregation by Subcategory",
            grain_columns=grain_columns,
            aggregations=aggregations,
            estimated_row_count=estimated_rows,
            estimated_compression_ratio=compression_ratio,
            visuals_that_would_benefit=visuals_benefiting,
            hit_rate_improvement=hit_rate_improvement,
            priority_score=hit_rate_improvement * (1 - compression_ratio) * 0.8,
            tmdl_code=tmdl_code,
            dax_expression=dax_expression,
            implementation_notes=[
                "Creates a mid-level aggregation for operational dashboards",
                f"Estimated {estimated_rows:,} rows ({compression_ratio:.2%} of base)",
                f"Would improve hit rate by {hit_rate_improvement:.1f}%",
            ],
        )

    def _find_time_columns(self, patterns: List[str]) -> List[str]:
        """Find time-related columns matching patterns."""
        matches: List[Tuple[str, int]] = []

        for col_ref, usage in self._column_usage.items():
            col_lower = col_ref.lower()
            for pattern in patterns:
                if pattern in col_lower:
                    matches.append((col_ref, usage["count"]))
                    break

        # Sort by usage count
        matches.sort(key=lambda x: -x[1])
        return [m[0] for m in matches]

    def _find_categorical_columns(self, patterns: List[str]) -> List[str]:
        """Find categorical columns matching patterns."""
        matches: List[Tuple[str, int]] = []

        for col_ref, usage in self._column_usage.items():
            col_lower = col_ref.lower()
            for pattern in patterns:
                if pattern in col_lower:
                    matches.append((col_ref, usage["count"]))
                    break

        matches.sort(key=lambda x: -x[1])
        return [m[0] for m in matches]

    def _parse_column_ref(self, col_ref: str) -> Tuple[str, str]:
        """Parse Table[Column] into (table, column)."""
        if "[" in col_ref:
            parts = col_ref.split("[")
            table = parts[0].strip("'")
            column = parts[1].rstrip("]")
            return table, column
        return "", col_ref

    def _estimate_cardinality(self, column_name: str) -> str:
        """Estimate cardinality level for a column."""
        col_lower = column_name.lower()
        for pattern, (card, _) in self.CARDINALITY_PATTERNS.items():
            if pattern in col_lower:
                return card
        return "medium"

    def _recommend_aggregations(self) -> List[RecommendedAggregation]:
        """Recommend aggregations (SUM, COUNT, etc.) for the table."""
        aggregations: List[RecommendedAggregation] = []

        # Find most used measures and their likely aggregation patterns
        for measure_name, count in sorted(self._measure_usage.items(), key=lambda x: -x[1])[:10]:
            # Determine aggregation function from measure name
            measure_lower = measure_name.lower()

            if any(p in measure_lower for p in ["count", "qty", "quantity", "units"]):
                func = "SUM"  # Pre-aggregated counts are summed
            elif any(p in measure_lower for p in ["avg", "average"]):
                func = "AVERAGE"
            elif any(p in measure_lower for p in ["min", "first"]):
                func = "MIN"
            elif any(p in measure_lower for p in ["max", "last"]):
                func = "MAX"
            else:
                func = "SUM"

            aggregations.append(
                RecommendedAggregation(
                    source_table="Fact",  # Would need model analysis to determine
                    source_column=measure_name,
                    aggregation_function=func,
                    result_column_name=f"Agg_{measure_name}",
                    used_in_measures=[measure_name],
                    usage_count=count,
                    priority=1 if count > 5 else 2,
                )
            )

        return aggregations[:5]  # Top 5 aggregations

    def _estimate_row_count(self, grain_columns: List[RecommendedGrainColumn]) -> int:
        """Estimate row count for an aggregation table."""
        estimated = 1

        for col in grain_columns:
            col_lower = col.column_name.lower()
            for pattern, (_, card) in self.CARDINALITY_PATTERNS.items():
                if pattern in col_lower:
                    estimated *= card
                    break
            else:
                # Default estimate
                if col.cardinality_estimate == "low":
                    estimated *= 50
                elif col.cardinality_estimate == "medium":
                    estimated *= 500
                else:
                    estimated *= 5000

        return min(estimated, 1_000_000)  # Cap at 1M

    def _count_visuals_benefiting(
        self, grain_columns: List[RecommendedGrainColumn], level: int
    ) -> int:
        """Count visuals that would benefit from this aggregation."""
        grain_refs = {col.full_ref for col in grain_columns}
        count = 0

        for page in self.report_summary.pages:
            for visual in page.visuals:
                if visual.determined_agg_level < level:
                    # Check if all columns in context are covered by grain
                    visual_cols = {
                        f"{c.table}[{c.column}]"
                        for c in visual.filter_context.all_columns
                        if not c.triggers_detail
                    }
                    if visual_cols.issubset(grain_refs) or not visual_cols:
                        count += 1

        return count

    def _generate_tmdl(
        self,
        grain_columns: List[RecommendedGrainColumn],
        aggregations: List[RecommendedAggregation],
        table_name: str,
    ) -> str:
        """Generate TMDL code for the aggregation table."""
        lines = [
            f"table '{table_name}'",
            "    isHidden",
            "    lineageTag: // Auto-generate a GUID",
            "",
            "    partition Aggregation = calculated",
            "        mode: import",
            "        source =",
        ]

        # Build SUMMARIZECOLUMNS expression
        grain_refs = [f"'{col.table_name}'[{col.column_name}]" for col in grain_columns]
        agg_exprs = [
            f"\"{agg.result_column_name}\", {agg.aggregation_function}('Fact'[{agg.source_column}])"
            for agg in aggregations
        ]

        expr = f"SUMMARIZECOLUMNS(\n                {','.join(grain_refs)}"
        if agg_exprs:
            expr += f",\n                {','.join(agg_exprs)}"
        expr += "\n            )"

        lines.append(f"            {expr}")
        lines.append("")

        # Add column definitions
        for col in grain_columns:
            lines.extend(
                [
                    f"    column '{col.column_name}'",
                    "        lineageTag: // Auto-generate",
                    f"        sourceColumn: {col.column_name}",
                    "        summarizeBy: none",
                    "",
                ]
            )

        for agg in aggregations:
            lines.extend(
                [
                    f"    column '{agg.result_column_name}'",
                    "        lineageTag: // Auto-generate",
                    f"        sourceColumn: {agg.result_column_name}",
                    "        summarizeBy: sum",
                    "",
                ]
            )

        return "\n".join(lines)

    def _generate_dax_expression(
        self,
        grain_columns: List[RecommendedGrainColumn],
        aggregations: List[RecommendedAggregation],
    ) -> str:
        """Generate DAX expression for the calculated table."""
        grain_refs = [f"'{col.table_name}'[{col.column_name}]" for col in grain_columns]
        agg_exprs = [
            f"    \"{agg.result_column_name}\", {agg.aggregation_function}('Fact'[{agg.source_column}])"
            for agg in aggregations
        ]

        lines = ["SUMMARIZECOLUMNS("]
        lines.append(f"    {', '.join(grain_refs)},")
        lines.extend([f"{expr}," for expr in agg_exprs[:-1]])
        if agg_exprs:
            lines.append(f"{agg_exprs[-1]}")
        lines.append(")")

        return "\n".join(lines)

    def _recommend_level_measure(self) -> Optional[RecommendedLevelMeasure]:
        """Recommend an aggregation level measure."""
        # Find detail trigger columns
        detail_triggers: List[str] = []
        mid_level_triggers: List[str] = []

        for col_ref, usage in self._column_usage.items():
            if usage.get("triggers_detail"):
                detail_triggers.append(col_ref)
            elif usage.get("count", 0) > 3:
                col_lower = col_ref.lower()
                if any(p in col_lower for p in ["month", "quarter", "category", "subcategory"]):
                    mid_level_triggers.append(col_ref)

        if not detail_triggers:
            # Infer from common patterns
            for col_ref in self._column_usage.keys():
                col_lower = col_ref.lower()
                if any(p in col_lower for p in ["date", "day", "customer", "product", "order"]):
                    detail_triggers.append(col_ref)

        # Generate DAX
        dax_lines = ["VAR _DetailFilter ="]

        if detail_triggers:
            conditions = [f"    ISFILTERED({col})" for col in detail_triggers[:5]]
            dax_lines.append(" ||\n".join(conditions))
        else:
            dax_lines.append("    FALSE")

        dax_lines.extend(
            [
                "",
                "VAR _MidLevelFilter =",
            ]
        )

        if mid_level_triggers:
            conditions = [f"    ISFILTERED({col})" for col in mid_level_triggers[:5]]
            dax_lines.append(" ||\n".join(conditions))
        else:
            dax_lines.append("    FALSE")

        dax_lines.extend(
            [
                "",
                "RETURN",
                "SWITCH(",
                "    TRUE(),",
                "    _DetailFilter, 1,",
                "    _MidLevelFilter, 2,",
                "    3",
                ")",
            ]
        )

        return RecommendedLevelMeasure(
            measure_name="_AggregationLevel",
            table_name="Measures",  # Or first table with measures
            dax_code="\n".join(dax_lines),
            detail_triggers=detail_triggers[:5],
            mid_level_triggers=mid_level_triggers[:5],
            notes=[
                "This measure determines which aggregation level to use",
                "Level 1 = Base table (detail), Level 2 = Mid-level, Level 3 = High-level",
                "Adjust ISFILTERED columns based on your model structure",
            ],
        )

    def _recommend_agg_aware_measures(self) -> List[RecommendedMeasure]:
        """Recommend aggregation-aware measure implementations."""
        recommendations: List[RecommendedMeasure] = []

        # Find measures not currently aggregation-aware
        for measure_name, count in sorted(self._measure_usage.items(), key=lambda x: -x[1])[:5]:
            if measure_name not in self._agg_aware_names:
                dax = self._generate_agg_aware_measure_dax(measure_name)
                recommendations.append(
                    RecommendedMeasure(
                        measure_name=f"{measure_name}_Agg",
                        original_measure_name=measure_name,
                        table_name="Measures",
                        dax_code=dax,
                        level_switches={
                            1: f"[{measure_name}]",
                            2: "SUM(Agg_MidLevel[...])",
                            3: "SUM(Agg_HighLevel[...])",
                        },
                        implementation_complexity="medium",
                        notes=[
                            f"Aggregation-aware version of [{measure_name}]",
                            "Update column references to match your aggregation tables",
                        ],
                    )
                )

        return recommendations

    def _generate_agg_aware_measure_dax(self, measure_name: str) -> str:
        """Generate DAX for an aggregation-aware measure."""
        return f"""VAR _Level = [_AggregationLevel]

RETURN
SWITCH(
    _Level,
    1, [{measure_name}],
    2, SUM('Agg_MidLevel'[Agg_{measure_name}]),
    3, SUM('Agg_HighLevel'[Agg_{measure_name}]),
    [{measure_name}]  -- Default fallback
)"""

    def _build_dimension_coverage(self) -> Dict[str, List[str]]:
        """Build dimension coverage map."""
        coverage: Dict[str, List[str]] = defaultdict(list)

        for col_ref in self._column_usage.keys():
            table, column = self._parse_column_ref(col_ref)
            if table:
                coverage[table].append(column)

        return dict(coverage)

    def _build_measure_coverage(self) -> Dict[str, bool]:
        """Build measure coverage map."""
        return {measure: measure in self._agg_aware_names for measure in self._measure_usage.keys()}

    def _determine_implementation_effort(
        self,
        tables: List[RecommendedAggregationTable],
        level_measure: Optional[RecommendedLevelMeasure],
        measures: List[RecommendedMeasure],
    ) -> str:
        """Determine overall implementation effort."""
        score = 0

        score += len(tables) * 3  # Each table is moderate effort
        if level_measure:
            score += 2  # Level measure is moderate
        score += len(measures) * 1  # Each measure is low effort

        if score <= 5:
            return "low"
        elif score <= 12:
            return "medium"
        else:
            return "high"

    def _generate_priority_actions(
        self,
        tables: List[RecommendedAggregationTable],
        level_measure: Optional[RecommendedLevelMeasure],
        measures: List[RecommendedMeasure],
    ) -> List[str]:
        """Generate priority action items."""
        actions: List[str] = []

        if level_measure:
            actions.append(
                "[1] Create aggregation level measure '_AggregationLevel' to route queries"
            )

        if tables:
            best_table = tables[0]
            actions.append(
                f"[2] Create {best_table.name} aggregation table "
                f"(est. {best_table.hit_rate_improvement:.1f}% hit rate improvement)"
            )

        if measures and level_measure:
            actions.append(
                f"[3] Convert top {len(measures)} measures to aggregation-aware versions"
            )

        if not actions:
            actions.append("[INFO] Current aggregation setup appears adequate")

        return actions
