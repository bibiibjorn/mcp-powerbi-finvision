"""
Cross-Filter Analyzer Module

Analyzes how cross-filtering and cross-highlighting between visuals
affects aggregation level usage. Identifies problematic visual interactions
and recommends interaction optimizations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Any, Set, Tuple, TYPE_CHECKING
from enum import Enum
from collections import defaultdict

from .aggregation_detector import AggregationTable, AggLevelMeasure
from .filter_context_analyzer import FilterSourceType

# Use TYPE_CHECKING to avoid circular imports
if TYPE_CHECKING:
    from .aggregation_analyzer import (
        VisualAggregationAnalysis,
        PageAggregationSummary,
        ReportAggregationSummary,
    )

logger = logging.getLogger(__name__)


class InteractionType(Enum):
    """Types of visual interactions."""

    CROSS_FILTER = "cross_filter"  # Filters other visuals
    CROSS_HIGHLIGHT = "cross_highlight"  # Highlights without filtering
    NONE = "none"  # No interaction
    DRILLTHROUGH = "drillthrough"


class InteractionImpact(Enum):
    """Impact level of cross-filter on aggregation."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class VisualInteraction:
    """Represents an interaction between two visuals."""

    source_visual_id: str
    source_visual_title: Optional[str]
    source_visual_type: str
    target_visual_id: str
    target_visual_title: Optional[str]
    target_visual_type: str
    page_id: str
    page_name: str
    interaction_type: InteractionType
    propagated_columns: List[str]  # Columns that would be filtered
    impact_on_target: InteractionImpact
    source_agg_level: int
    target_agg_level_before: int  # Without interaction
    target_agg_level_after: int  # With interaction
    causes_level_drop: bool


@dataclass
class VisualInteractionProfile:
    """Profile of all interactions for a single visual."""

    visual_id: str
    visual_title: Optional[str]
    visual_type: str
    page_id: str
    page_name: str

    # Outgoing interactions (this visual filters others)
    outgoing_interactions: int
    critical_outgoing: int  # Count that cause level drops
    visuals_it_affects: List[str]

    # Incoming interactions (other visuals filter this one)
    incoming_interactions: int
    critical_incoming: int
    visuals_affecting_it: List[str]

    # Impact summary
    is_problematic_source: bool
    is_problematic_target: bool
    net_aggregation_impact: int  # Negative = causes drops, Positive = neutral


@dataclass
class RelationshipPath:
    """A relationship path that propagates filters."""

    from_table: str
    to_table: str
    is_bidirectional: bool
    cardinality: str  # "one-to-many", "many-to-one", "one-to-one", "many-to-many"
    propagates_filter: bool
    can_cause_agg_drop: bool


@dataclass
class PageInteractionMatrix:
    """Interaction matrix for a single page."""

    page_id: str
    page_name: str
    visual_count: int
    total_interactions: int
    critical_interactions: int
    interaction_density: float  # Interactions / possible pairs
    problematic_visuals: List[str]
    interaction_map: Dict[str, Dict[str, InteractionImpact]]  # source -> target -> impact
    recommendations: List[str]


@dataclass
class CrossFilterAnalysisResult:
    """Complete cross-filter analysis result."""

    total_visual_pairs: int
    total_interactions_analyzed: int
    critical_interactions: int
    interactions_causing_level_drops: int

    visual_interactions: List[VisualInteraction]
    visual_profiles: List[VisualInteractionProfile]
    page_matrices: List[PageInteractionMatrix]
    relationship_paths: List[RelationshipPath]

    # Overall assessment
    overall_risk_level: str  # "low", "medium", "high", "critical"
    estimated_hit_rate_impact: float  # Percentage points lost due to cross-filtering

    # Recommendations
    disable_interaction_recommendations: List[Tuple[str, str, str]]  # (source, target, reason)
    relationship_recommendations: List[str]
    priority_recommendations: List[str]


class CrossFilterAnalyzer:
    """Analyzes cross-filter impact on aggregation levels."""

    def __init__(
        self,
        agg_tables: List[AggregationTable],
        agg_level_measures: List[AggLevelMeasure],
        report_summary: ReportAggregationSummary,
        relationships: Optional[List[Dict[str, Any]]] = None,
    ):
        """
        Initialize the cross-filter analyzer.

        Args:
            agg_tables: Detected aggregation tables
            agg_level_measures: Detected aggregation level measures
            report_summary: Complete report analysis result
            relationships: Model relationships for path analysis
        """
        self.agg_tables = agg_tables
        self.agg_level_measures = agg_level_measures
        self.report_summary = report_summary
        self.relationships = relationships or []

        # Build lookup maps
        self._detail_triggers: Set[str] = set()
        self._mid_level_triggers: Set[str] = set()
        if agg_level_measures:
            self._detail_triggers = set(agg_level_measures[0].detail_trigger_columns)
            self._mid_level_triggers = set(agg_level_measures[0].mid_level_trigger_columns)

        # Build visual lookup
        self._visuals: Dict[str, VisualAggregationAnalysis] = {}
        for page in report_summary.pages:
            for visual in page.visuals:
                self._visuals[visual.visual_id] = visual

        # Analyze relationship paths
        self._relationship_paths = self._analyze_relationship_paths()

    def analyze(self) -> CrossFilterAnalysisResult:
        """
        Perform complete cross-filter analysis.

        Returns:
            CrossFilterAnalysisResult with all findings
        """
        logger.info("Starting cross-filter analysis")

        # Analyze interactions on each page
        all_interactions: List[VisualInteraction] = []
        page_matrices: List[PageInteractionMatrix] = []

        for page in self.report_summary.pages:
            interactions, matrix = self._analyze_page_interactions(page)
            all_interactions.extend(interactions)
            page_matrices.append(matrix)

        # Build visual profiles
        visual_profiles = self._build_visual_profiles(all_interactions)

        # Calculate statistics
        total_pairs = sum(len(p.visuals) * (len(p.visuals) - 1) for p in self.report_summary.pages)
        critical_count = sum(
            1 for i in all_interactions if i.impact_on_target == InteractionImpact.CRITICAL
        )
        level_drop_count = sum(1 for i in all_interactions if i.causes_level_drop)

        # Determine overall risk
        overall_risk = self._determine_overall_risk(
            critical_count, level_drop_count, len(all_interactions)
        )

        # Calculate hit rate impact
        hit_rate_impact = self._calculate_hit_rate_impact(all_interactions)

        # Generate recommendations
        disable_recommendations = self._generate_disable_recommendations(all_interactions)
        relationship_recommendations = self._generate_relationship_recommendations()
        priority_recommendations = self._generate_priority_recommendations(
            all_interactions, visual_profiles, page_matrices
        )

        return CrossFilterAnalysisResult(
            total_visual_pairs=total_pairs,
            total_interactions_analyzed=len(all_interactions),
            critical_interactions=critical_count,
            interactions_causing_level_drops=level_drop_count,
            visual_interactions=all_interactions,
            visual_profiles=visual_profiles,
            page_matrices=page_matrices,
            relationship_paths=self._relationship_paths,
            overall_risk_level=overall_risk,
            estimated_hit_rate_impact=hit_rate_impact,
            disable_interaction_recommendations=disable_recommendations,
            relationship_recommendations=relationship_recommendations,
            priority_recommendations=priority_recommendations,
        )

    def _analyze_relationship_paths(self) -> List[RelationshipPath]:
        """Analyze relationship paths that could propagate filters."""
        paths: List[RelationshipPath] = []

        for rel in self.relationships:
            from_col = rel.get("from_column", "")
            to_col = rel.get("to_column", "")
            is_active = rel.get("is_active", True)
            cross_filter = rel.get("cross_filter_direction", "")
            cardinality = rel.get("cardinality", "")

            if not from_col or not to_col:
                continue

            # Parse table names
            from_table = from_col.split(".")[0] if "." in from_col else from_col.split("[")[0]
            to_table = to_col.split(".")[0] if "." in to_col else to_col.split("[")[0]

            is_bidir = cross_filter.lower() in ["both", "bidirectional"]

            # Determine if this relationship can cause aggregation drops
            can_cause_drop = False
            # Bidirectional relationships are more likely to propagate filters unexpectedly
            if is_bidir:
                can_cause_drop = True
            # Many-to-many relationships often cause issues
            if "many" in cardinality.lower() and "many" in cardinality.lower():
                can_cause_drop = True

            paths.append(
                RelationshipPath(
                    from_table=from_table,
                    to_table=to_table,
                    is_bidirectional=is_bidir,
                    cardinality=cardinality,
                    propagates_filter=is_active,
                    can_cause_agg_drop=can_cause_drop,
                )
            )

        return paths

    def _analyze_page_interactions(
        self, page: PageAggregationSummary
    ) -> Tuple[List[VisualInteraction], PageInteractionMatrix]:
        """Analyze all visual interactions on a page."""
        interactions: List[VisualInteraction] = []
        interaction_map: Dict[str, Dict[str, InteractionImpact]] = defaultdict(dict)
        problematic_visuals: Set[str] = set()

        visuals = page.visuals

        # For each pair of visuals, analyze potential cross-filter impact
        for source_visual in visuals:
            for target_visual in visuals:
                if source_visual.visual_id == target_visual.visual_id:
                    continue

                interaction = self._analyze_visual_pair(
                    source_visual, target_visual, page.page_name
                )

                if interaction:
                    interactions.append(interaction)
                    interaction_map[source_visual.visual_id][
                        target_visual.visual_id
                    ] = interaction.impact_on_target

                    if interaction.impact_on_target == InteractionImpact.CRITICAL:
                        problematic_visuals.add(source_visual.visual_id)

        # Calculate interaction density
        possible_pairs = len(visuals) * (len(visuals) - 1) if len(visuals) > 1 else 1
        density = len(interactions) / possible_pairs if possible_pairs > 0 else 0

        critical_count = sum(
            1 for i in interactions if i.impact_on_target == InteractionImpact.CRITICAL
        )

        # Generate page recommendations
        recommendations = []
        if critical_count > 0:
            recommendations.append(
                f"Page has {critical_count} critical cross-filter interactions. "
                f"Consider disabling interactions for problematic visuals"
            )
        if density > 0.7:
            recommendations.append(
                f"High interaction density ({density:.0%}). "
                f"Consider using interaction controls to limit filter propagation"
            )

        matrix = PageInteractionMatrix(
            page_id=page.page_id,
            page_name=page.page_name,
            visual_count=len(visuals),
            total_interactions=len(interactions),
            critical_interactions=critical_count,
            interaction_density=density,
            problematic_visuals=list(problematic_visuals),
            interaction_map=dict(interaction_map),
            recommendations=recommendations,
        )

        return interactions, matrix

    def _analyze_visual_pair(
        self,
        source: VisualAggregationAnalysis,
        target: VisualAggregationAnalysis,
        page_name: str,
    ) -> Optional[VisualInteraction]:
        """Analyze interaction between two visuals."""
        # Determine what columns the source visual would propagate
        propagated_columns: List[str] = []

        for col_ctx in source.filter_context.all_columns:
            if col_ctx.source_type == FilterSourceType.VISUAL_FIELD:
                col_ref = f"{col_ctx.table}[{col_ctx.column}]"
                propagated_columns.append(col_ref)

        if not propagated_columns:
            return None  # No columns to propagate

        # Determine impact on target
        would_trigger_detail = any(col in self._detail_triggers for col in propagated_columns)
        would_trigger_mid = any(col in self._mid_level_triggers for col in propagated_columns)

        # Calculate level change
        target_level_before = target.determined_agg_level
        if would_trigger_detail:
            target_level_after = 1
        elif would_trigger_mid and target_level_before > 2:
            target_level_after = 2
        else:
            target_level_after = target_level_before

        causes_drop = target_level_after < target_level_before

        # Determine impact level
        if would_trigger_detail and target_level_before > 1:
            impact = InteractionImpact.CRITICAL
        elif causes_drop:
            impact = InteractionImpact.HIGH
        elif would_trigger_mid:
            impact = InteractionImpact.MEDIUM
        else:
            impact = InteractionImpact.LOW

        return VisualInteraction(
            source_visual_id=source.visual_id,
            source_visual_title=source.visual_title,
            source_visual_type=source.visual_type,
            target_visual_id=target.visual_id,
            target_visual_title=target.visual_title,
            target_visual_type=target.visual_type,
            page_id=source.page_id,
            page_name=page_name,
            interaction_type=InteractionType.CROSS_FILTER,
            propagated_columns=propagated_columns,
            impact_on_target=impact,
            source_agg_level=source.determined_agg_level,
            target_agg_level_before=target_level_before,
            target_agg_level_after=target_level_after,
            causes_level_drop=causes_drop,
        )

    def _build_visual_profiles(
        self, interactions: List[VisualInteraction]
    ) -> List[VisualInteractionProfile]:
        """Build interaction profiles for each visual."""
        profile_data: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {
                "outgoing": [],
                "incoming": [],
                "title": None,
                "type": None,
                "page_id": None,
                "page_name": None,
            }
        )

        for interaction in interactions:
            source_id = interaction.source_visual_id
            target_id = interaction.target_visual_id

            profile_data[source_id]["outgoing"].append(interaction)
            profile_data[source_id]["title"] = interaction.source_visual_title
            profile_data[source_id]["type"] = interaction.source_visual_type
            profile_data[source_id]["page_id"] = interaction.page_id
            profile_data[source_id]["page_name"] = interaction.page_name

            profile_data[target_id]["incoming"].append(interaction)
            profile_data[target_id]["title"] = interaction.target_visual_title
            profile_data[target_id]["type"] = interaction.target_visual_type
            profile_data[target_id]["page_id"] = interaction.page_id
            profile_data[target_id]["page_name"] = interaction.page_name

        profiles: List[VisualInteractionProfile] = []

        for visual_id, data in profile_data.items():
            outgoing = data["outgoing"]
            incoming = data["incoming"]

            critical_outgoing = sum(
                1 for i in outgoing if i.impact_on_target == InteractionImpact.CRITICAL
            )
            critical_incoming = sum(
                1 for i in incoming if i.impact_on_target == InteractionImpact.CRITICAL
            )

            is_problematic_source = critical_outgoing > 0
            is_problematic_target = critical_incoming > 0

            net_impact = -critical_outgoing - critical_incoming

            profiles.append(
                VisualInteractionProfile(
                    visual_id=visual_id,
                    visual_title=data["title"],
                    visual_type=data["type"],
                    page_id=data["page_id"],
                    page_name=data["page_name"],
                    outgoing_interactions=len(outgoing),
                    critical_outgoing=critical_outgoing,
                    visuals_it_affects=[i.target_visual_id for i in outgoing],
                    incoming_interactions=len(incoming),
                    critical_incoming=critical_incoming,
                    visuals_affecting_it=[i.source_visual_id for i in incoming],
                    is_problematic_source=is_problematic_source,
                    is_problematic_target=is_problematic_target,
                    net_aggregation_impact=net_impact,
                )
            )

        # Sort by impact (most problematic first)
        profiles.sort(key=lambda p: p.net_aggregation_impact)

        return profiles

    def _determine_overall_risk(self, critical: int, level_drops: int, total: int) -> str:
        """Determine overall cross-filter risk level."""
        if total == 0:
            return "low"

        critical_ratio = critical / total
        drop_ratio = level_drops / total

        if critical_ratio > 0.3 or critical > 10:
            return "critical"
        elif critical_ratio > 0.15 or critical > 5:
            return "high"
        elif drop_ratio > 0.3 or level_drops > 10:
            return "medium"
        else:
            return "low"

    def _calculate_hit_rate_impact(self, interactions: List[VisualInteraction]) -> float:
        """Calculate estimated hit rate impact from cross-filtering."""
        if not interactions:
            return 0

        total_visuals = self.report_summary.visuals_analyzed
        if total_visuals == 0:
            return 0

        # Count unique visuals affected by level drops
        affected_visuals: Set[str] = set()
        for interaction in interactions:
            if interaction.causes_level_drop:
                affected_visuals.add(interaction.target_visual_id)

        # Estimate impact as percentage of visuals affected
        # Weight by severity of the drop
        weighted_impact = 0
        for interaction in interactions:
            if interaction.causes_level_drop:
                level_drop = (
                    interaction.target_agg_level_before - interaction.target_agg_level_after
                )
                weighted_impact += level_drop * 0.5  # 0.5% per level dropped

        return min(weighted_impact, 50)  # Cap at 50%

    def _generate_disable_recommendations(
        self, interactions: List[VisualInteraction]
    ) -> List[Tuple[str, str, str]]:
        """Generate recommendations for disabling specific interactions."""
        recommendations: List[Tuple[str, str, str]] = []

        for interaction in interactions:
            if interaction.impact_on_target == InteractionImpact.CRITICAL:
                source_name = interaction.source_visual_title or interaction.source_visual_id[:8]
                target_name = interaction.target_visual_title or interaction.target_visual_id[:8]
                reason = (
                    f"Cross-filter propagates {', '.join(interaction.propagated_columns[:2])} "
                    f"causing {target_name} to drop from Level {interaction.target_agg_level_before} "
                    f"to Level {interaction.target_agg_level_after}"
                )
                recommendations.append((source_name, target_name, reason))

        return recommendations[:10]  # Top 10

    def _generate_relationship_recommendations(self) -> List[str]:
        """Generate recommendations based on relationship analysis."""
        recommendations: List[str] = []

        bidir_count = sum(1 for p in self._relationship_paths if p.is_bidirectional)
        if bidir_count > 0:
            recommendations.append(
                f"{bidir_count} bidirectional relationships detected. "
                f"These can cause unexpected filter propagation affecting aggregation levels"
            )

        problematic_paths = [p for p in self._relationship_paths if p.can_cause_agg_drop]
        if problematic_paths:
            recommendations.append(
                f"{len(problematic_paths)} relationship paths may cause aggregation level drops. "
                f"Review cross-filter direction settings"
            )

        return recommendations

    def _generate_priority_recommendations(
        self,
        interactions: List[VisualInteraction],
        profiles: List[VisualInteractionProfile],
        matrices: List[PageInteractionMatrix],
    ) -> List[str]:
        """Generate priority recommendations."""
        recommendations: List[str] = []

        # Most problematic source visuals
        problematic_sources = [p for p in profiles if p.is_problematic_source]
        if problematic_sources:
            worst = problematic_sources[0]
            recommendations.append(
                f"[HIGH] Visual '{worst.visual_title or worst.visual_id[:8]}' causes "
                f"{worst.critical_outgoing} critical aggregation level drops. "
                f"Consider disabling its cross-filter behavior"
            )

        # Pages with high interaction density
        high_density_pages = [m for m in matrices if m.interaction_density > 0.5]
        if high_density_pages:
            recommendations.append(
                f"[MEDIUM] {len(high_density_pages)} page(s) have high interaction density. "
                f"Use 'Edit interactions' to selectively disable filter propagation"
            )

        # Critical interactions summary
        critical_count = sum(
            1 for i in interactions if i.impact_on_target == InteractionImpact.CRITICAL
        )
        if critical_count > 0:
            recommendations.append(
                f"[HIGH] {critical_count} visual interactions cause critical aggregation level drops. "
                f"Review and disable problematic interactions in Power BI Desktop"
            )

        # If no issues
        if not recommendations:
            recommendations.append(
                "[INFO] Cross-filter interactions have minimal impact on aggregation levels"
            )

        return recommendations[:10]
