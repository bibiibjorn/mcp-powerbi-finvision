"""
Aggregation Analyzer Module

Main analysis engine that combines detection and filter context analysis
to provide comprehensive aggregation usage insights.

Integrates:
- Quality Analysis: Design quality & DAX pattern analysis
- Hit Rate Analysis: Aggregation hit rate & miss reasons
- Slicer Impact Analysis: Slicer effects on aggregation levels
- Cross-Filter Analysis: Cross-filter/cross-highlight impact
- Recommendations: Automatic aggregation table recommendations
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any

from .aggregation_detector import (
    AggregationTableDetector,
    AggregationTable,
    AggLevelMeasure,
    AggAwareMeasure,
)
from .filter_context_analyzer import (
    FilterContextAnalyzer,
    FilterContext,
    ColumnContext,
    SlicerInfo,
    extract_slicers_from_page,
    extract_page_filters,
)

# Import new analyzers
from .aggregation_quality_analyzer import AggregationQualityAnalyzer, AggregationQualityResult
from .aggregation_hit_rate_analyzer import AggregationHitRateAnalyzer, HitRateAnalysisResult
from .slicer_impact_analyzer import SlicerImpactAnalyzer, SlicerImpactResult
from .cross_filter_analyzer import CrossFilterAnalyzer, CrossFilterAnalysisResult
from .aggregation_recommender import AggregationRecommender, AggregationRecommendationResult

logger = logging.getLogger(__name__)

# visualType values that never query the model for a measure value (compared
# lower-case). 2026-09-22 (I-23): the old list held "actionButton", which never
# matched ``visual_type.lower()``, and missed the new slicer types, basicShape
# and navigators - they were scored as "Base Table" misses.
NON_DATA_VISUAL_TYPES = frozenset(
    {
        "slicer",
        "advancedslicervisual",
        "listslicer",
        "textslicer",
        "textbox",
        "image",
        "shape",
        "basicshape",
        "actionbutton",
        "pagenavigator",
        "bookmarknavigator",
    }
)


def is_data_visual(visual_data: Dict[str, Any]) -> bool:
    """True for a visual that renders model data (not a group container, slicer,
    shape, text box, image, button or navigator)."""
    if "visualGroup" in visual_data or not isinstance(visual_data.get("visual"), dict):
        return False
    visual_type = str(visual_data["visual"].get("visualType", "") or "")
    return bool(visual_type) and visual_type.lower() not in NON_DATA_VISUAL_TYPES


@dataclass
class VisualAggregationAnalysis:
    """Analysis result for a single visual."""

    visual_id: str
    visual_type: str
    visual_title: Optional[str]
    page_id: str
    page_name: str
    measures_used: List[str]
    agg_aware_measures_used: List[str]
    columns_in_context: List[ColumnContext]
    filter_context: FilterContext
    determined_agg_level: int
    determined_agg_level_name: str
    determined_agg_table: Optional[str]
    reasoning: str
    optimization_notes: List[str] = field(default_factory=list)


@dataclass
class PageAggregationSummary:
    """Aggregation summary for a page."""

    page_id: str
    page_name: str
    total_visuals: int
    visuals_analyzed: int
    agg_table_breakdown: Dict[str, int]  # table_name -> count
    agg_level_breakdown: Dict[int, int]  # level -> count
    agg_table_percentages: Dict[str, float]
    visuals: List[VisualAggregationAnalysis]
    slicers: List[SlicerInfo]
    optimization_opportunities: List[str]


@dataclass
class ReportAggregationSummary:
    """Overall report aggregation summary."""

    total_pages: int
    total_visuals: int
    visuals_analyzed: int
    agg_table_breakdown: Dict[str, int]
    agg_level_breakdown: Dict[int, int]
    agg_table_percentages: Dict[str, float]
    optimization_score: float  # 0-100
    pages: List[PageAggregationSummary]
    recommendations: List[str]


@dataclass
class AggregationAnalysisResult:
    """Complete aggregation analysis result."""

    # Model info
    model_name: str
    model_path: str

    # Detected infrastructure
    aggregation_tables: List[AggregationTable]
    base_fact_tables: List[str]
    agg_level_measures: List[AggLevelMeasure]
    agg_aware_measures: List[AggAwareMeasure]

    # Report analysis
    report_summary: Optional[ReportAggregationSummary]

    # Enhanced analysis results (new)
    quality_analysis: Optional[AggregationQualityResult] = None
    hit_rate_analysis: Optional[HitRateAnalysisResult] = None
    slicer_impact_analysis: Optional[SlicerImpactResult] = None
    cross_filter_analysis: Optional[CrossFilterAnalysisResult] = None
    recommendations: Optional[AggregationRecommendationResult] = None

    # Metadata
    analysis_timestamp: str = ""
    has_report: bool = False
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class AggregationAnalyzer:
    """Comprehensive aggregation analysis for PBIP models."""

    def __init__(self, pbip_path: str):
        """
        Initialize analyzer with PBIP path.

        Args:
            pbip_path: Path to PBIP project folder, .SemanticModel folder, or parent
        """
        self.pbip_path = Path(pbip_path)
        self.model_path: Optional[Path] = None
        self.report_path: Optional[Path] = None
        self.model_data: Optional[Dict] = None
        self.report_data: Optional[Dict] = None

        # Analysis components
        self.detector: Optional[AggregationTableDetector] = None
        self.context_analyzer: Optional[FilterContextAnalyzer] = None

        # New enhanced analyzers
        self.quality_analyzer: Optional[AggregationQualityAnalyzer] = None
        self.hit_rate_analyzer: Optional[AggregationHitRateAnalyzer] = None
        self.slicer_impact_analyzer: Optional[SlicerImpactAnalyzer] = None
        self.cross_filter_analyzer: Optional[CrossFilterAnalyzer] = None
        self.recommender: Optional[AggregationRecommender] = None

        # Detection results
        self.agg_tables: List[AggregationTable] = []
        self.base_tables: List[str] = []
        self.agg_level_measures: List[AggLevelMeasure] = []
        self.agg_aware_measures: List[AggAwareMeasure] = []

        self._resolve_paths()

    def _resolve_paths(self) -> None:
        """Resolve model and report paths from PBIP path."""
        path = self.pbip_path

        # Check if path is directly to SemanticModel
        if path.name.endswith(".SemanticModel"):
            self.model_path = path
            # Look for sibling Report folder
            report_name = path.name.replace(".SemanticModel", ".Report")
            potential_report = path.parent / report_name
            if potential_report.exists():
                self.report_path = potential_report
        else:
            # Look for SemanticModel folder in path or children
            for item in path.iterdir():
                if item.is_dir():
                    if item.name.endswith(".SemanticModel"):
                        self.model_path = item
                    elif item.name.endswith(".Report"):
                        self.report_path = item

            # If not found, check if we're in the PBIP root with subfolders
            if not self.model_path:
                for item in path.iterdir():
                    if item.is_dir():
                        for sub in item.iterdir():
                            if sub.name.endswith(".SemanticModel"):
                                self.model_path = sub
                            elif sub.name.endswith(".Report"):
                                self.report_path = sub

    def analyze(
        self,
        include_report: bool = True,
        include_quality_analysis: bool = True,
        include_hit_rate_analysis: bool = True,
        include_slicer_impact: bool = True,
        include_cross_filter: bool = True,
        include_recommendations: bool = True,
        page_filter: Optional[str] = None,
    ) -> AggregationAnalysisResult:
        """
        Perform full aggregation analysis.

        Args:
            include_report: Whether to analyze the report visuals
            include_quality_analysis: Whether to run quality & DAX pattern analysis
            include_hit_rate_analysis: Whether to analyze hit rates and miss reasons
            include_slicer_impact: Whether to analyze slicer impact on aggregation
            include_cross_filter: Whether to analyze cross-filter/cross-highlight impact
            include_recommendations: Whether to generate aggregation recommendations
            page_filter: Case-insensitive substring of the page display name; only
                matching pages are analysed, so every total/score covers just them

        Returns:
            Complete AggregationAnalysisResult
        """
        import datetime

        timestamp = datetime.datetime.now().isoformat()

        errors = []
        warnings = []

        # Load model data
        if not self.model_path:
            raise ValueError(f"No SemanticModel found in {self.pbip_path}")

        logger.info(f"Loading model from {self.model_path}")
        self.model_data = self._load_model_data()

        if not self.model_data:
            raise ValueError(f"Failed to load model from {self.model_path}")

        # Initialize detector and run detection
        self.detector = AggregationTableDetector(self.model_data)
        detection_results = self.detector.detect_all()

        self.agg_tables = detection_results["aggregation_tables"]
        self.base_tables = detection_results["base_fact_tables"]
        self.agg_level_measures = detection_results["agg_level_measures"]
        self.agg_aware_measures = detection_results["agg_aware_measures"]

        # Initialize context analyzer with detected rules
        primary_level_measure = self.agg_level_measures[0] if self.agg_level_measures else None
        self.context_analyzer = FilterContextAnalyzer(
            agg_level_measure=primary_level_measure,
            agg_tables=self.agg_tables,
        )

        # Analyze report if available and requested
        report_summary = None
        has_report = False

        if include_report and self.report_path:
            logger.info(f"Loading report from {self.report_path}")
            try:
                self.report_data = self._load_report_data()
                has_report = True
                report_summary = self._analyze_report(page_filter=page_filter)
            except Exception as e:
                logger.warning(f"Failed to analyze report: {e}")
                warnings.append(f"Report analysis failed: {e}")

        # Get model name
        model_name = (
            self.model_path.name.replace(".SemanticModel", "") if self.model_path else "Unknown"
        )

        # Run enhanced analyses
        quality_result = None
        hit_rate_result = None
        slicer_impact_result = None
        cross_filter_result = None
        recommendation_result = None

        # Quality Analysis
        if include_quality_analysis and self.agg_tables:
            logger.info("Running aggregation quality analysis...")
            try:
                quality_result = self._run_quality_analysis()
            except Exception as e:
                logger.warning(f"Quality analysis failed: {e}")
                warnings.append(f"Quality analysis failed: {e}")

        # Hit Rate Analysis
        if include_hit_rate_analysis and report_summary:
            logger.info("Running hit rate analysis...")
            try:
                hit_rate_result = self._run_hit_rate_analysis(report_summary)
            except Exception as e:
                logger.warning(f"Hit rate analysis failed: {e}")
                warnings.append(f"Hit rate analysis failed: {e}")

        # Slicer Impact Analysis
        if include_slicer_impact and report_summary:
            logger.info("Running slicer impact analysis...")
            try:
                slicer_impact_result = self._run_slicer_impact_analysis(report_summary)
            except Exception as e:
                logger.warning(f"Slicer impact analysis failed: {e}")
                warnings.append(f"Slicer impact analysis failed: {e}")

        # Cross-Filter Analysis
        if include_cross_filter and report_summary:
            logger.info("Running cross-filter analysis...")
            try:
                cross_filter_result = self._run_cross_filter_analysis(report_summary)
            except Exception as e:
                logger.warning(f"Cross-filter analysis failed: {e}")
                warnings.append(f"Cross-filter analysis failed: {e}")

        # Recommendations
        if include_recommendations:
            logger.info("Generating aggregation recommendations...")
            try:
                recommendation_result = self._run_recommendations(report_summary)
            except Exception as e:
                logger.warning(f"Recommendations generation failed: {e}")
                warnings.append(f"Recommendations generation failed: {e}")

        return AggregationAnalysisResult(
            model_name=model_name,
            model_path=str(self.model_path),
            aggregation_tables=self.agg_tables,
            base_fact_tables=self.base_tables,
            agg_level_measures=self.agg_level_measures,
            agg_aware_measures=self.agg_aware_measures,
            report_summary=report_summary,
            quality_analysis=quality_result,
            hit_rate_analysis=hit_rate_result,
            slicer_impact_analysis=slicer_impact_result,
            cross_filter_analysis=cross_filter_result,
            recommendations=recommendation_result,
            analysis_timestamp=timestamp,
            has_report=has_report,
            errors=errors,
            warnings=warnings,
        )

    def _load_model_data(self) -> Dict[str, Any]:
        """Load and parse model data with the canonical TMDL parser."""
        from core.tmdl.unified_parser import UnifiedTmdlParser

        return UnifiedTmdlParser(str(self.model_path)).parse_full_model().to_dict()

    def _load_report_data(self) -> Dict[str, Any]:
        """Load and parse report data."""
        if not self.report_path:
            return {}

        report_data = {
            "pages": [],
            "report_filters": [],
        }

        definition_path = self.report_path / "definition"
        if not definition_path.exists():
            return report_data

        # Load pages
        pages_path = definition_path / "pages"
        if pages_path.exists():
            # Get page order from pages.json
            pages_json = pages_path / "pages.json"
            page_order = []
            if pages_json.exists():
                with open(pages_json, "r", encoding="utf-8") as f:
                    pages_config = json.load(f)
                    page_order = pages_config.get("pageOrder", [])

            # Load each page in report order: pageOrder first, then any page
            # folder it does not list (by name). I-23: pageOrder was ignored.
            order_index = {name: i for i, name in enumerate(page_order)}
            page_folders = sorted(
                (f for f in pages_path.iterdir() if f.is_dir()),
                key=lambda f: (order_index.get(f.name, len(order_index)), f.name),
            )
            for page_folder in page_folders:
                page_data = self._load_page_data(page_folder)
                if page_data:
                    report_data["pages"].append(page_data)

        # Load report-level filters
        report_json = definition_path / "report.json"
        if report_json.exists():
            with open(report_json, "r", encoding="utf-8") as f:
                report_config = json.load(f)
                filter_config = report_config.get("filterConfig", {})
                report_data["report_filters"] = filter_config.get("filters", [])

        return report_data

    def _load_page_data(self, page_folder: Path) -> Optional[Dict[str, Any]]:
        """Load data for a single page."""
        page_json = page_folder / "page.json"
        if not page_json.exists():
            return None

        with open(page_json, "r", encoding="utf-8") as f:
            page_config = json.load(f)

        page_data = {
            "id": page_folder.name,
            "name": page_config.get("displayName", page_folder.name),
            "type": page_config.get("type", ""),
            "visuals": [],
            "filters": [],
        }

        # Load visuals
        visuals_path = page_folder / "visuals"
        if visuals_path.exists():
            for visual_folder in sorted(visuals_path.iterdir()):
                if visual_folder.is_dir():
                    visual_json = visual_folder / "visual.json"
                    if visual_json.exists():
                        with open(visual_json, "r", encoding="utf-8") as f:
                            visual_data = json.load(f)
                            page_data["visuals"].append(visual_data)

        # Extract page filters
        page_data["filters"] = extract_page_filters(page_config)

        return page_data

    def _analyze_report(self, page_filter: Optional[str] = None) -> ReportAggregationSummary:
        """Analyze the report's pages (only those matching ``page_filter``)."""
        if not self.report_data:
            return ReportAggregationSummary(
                total_pages=0,
                total_visuals=0,
                visuals_analyzed=0,
                agg_table_breakdown={},
                agg_level_breakdown={},
                agg_table_percentages={},
                optimization_score=0,
                pages=[],
                recommendations=[],
            )

        pages = self.report_data.get("pages", [])
        if page_filter:
            needle = str(page_filter).lower()
            pages = [p for p in pages if needle in str(p.get("name", "")).lower()]
        report_filters = self.report_data.get("report_filters", [])

        page_summaries = []
        total_visuals = 0
        visuals_analyzed = 0
        overall_breakdown: Dict[str, int] = {}
        overall_level_breakdown: Dict[int, int] = {}

        for page_data in pages:
            page_summary = self._analyze_page(page_data, report_filters)
            page_summaries.append(page_summary)

            total_visuals += page_summary.total_visuals
            visuals_analyzed += page_summary.visuals_analyzed

            # Aggregate breakdowns
            for table, count in page_summary.agg_table_breakdown.items():
                overall_breakdown[table] = overall_breakdown.get(table, 0) + count

            for level, count in page_summary.agg_level_breakdown.items():
                overall_level_breakdown[level] = overall_level_breakdown.get(level, 0) + count

        # Calculate percentages
        percentages = {}
        if visuals_analyzed > 0:
            for table, count in overall_breakdown.items():
                percentages[table] = (count / visuals_analyzed) * 100

        # Calculate optimization score
        # Score is based on how many visuals use aggregation tables vs base table
        optimization_score = self._calculate_optimization_score(
            overall_level_breakdown, visuals_analyzed
        )

        # Generate recommendations
        recommendations = self._generate_recommendations(
            page_summaries, overall_breakdown, overall_level_breakdown
        )

        return ReportAggregationSummary(
            total_pages=len(pages),
            total_visuals=total_visuals,
            visuals_analyzed=visuals_analyzed,
            agg_table_breakdown=overall_breakdown,
            agg_level_breakdown=overall_level_breakdown,
            agg_table_percentages=percentages,
            optimization_score=optimization_score,
            pages=page_summaries,
            recommendations=recommendations,
        )

    def _analyze_page(self, page_data: Dict, report_filters: List[Dict]) -> PageAggregationSummary:
        """Analyze aggregation for a single page."""
        page_id = page_data.get("id", "")
        page_name = page_data.get("name", page_id)
        visuals = page_data.get("visuals", [])
        page_filters = page_data.get("filters", [])

        # Extract slicers from page
        slicers = extract_slicers_from_page({"visuals": visuals}, page_id)

        visual_analyses = []
        agg_breakdown: Dict[str, int] = {}
        level_breakdown: Dict[int, int] = {}

        for visual_data in visuals:
            # Skip group containers and non-data visuals
            if not is_data_visual(visual_data):
                continue

            analysis = self._analyze_visual(
                visual_data,
                page_id,
                page_name,
                page_filters,
                report_filters,
                slicers,
            )

            if analysis:
                visual_analyses.append(analysis)

                # Update breakdown
                table_name = analysis.determined_agg_table or "Base Table"
                agg_breakdown[table_name] = agg_breakdown.get(table_name, 0) + 1
                level_breakdown[analysis.determined_agg_level] = (
                    level_breakdown.get(analysis.determined_agg_level, 0) + 1
                )

        # Calculate percentages
        percentages = {}
        if visual_analyses:
            for table, count in agg_breakdown.items():
                percentages[table] = (count / len(visual_analyses)) * 100

        # Identify optimization opportunities
        opportunities = self._identify_page_opportunities(visual_analyses)

        return PageAggregationSummary(
            page_id=page_id,
            page_name=page_name,
            total_visuals=len(visuals),
            visuals_analyzed=len(visual_analyses),
            agg_table_breakdown=agg_breakdown,
            agg_level_breakdown=level_breakdown,
            agg_table_percentages=percentages,
            visuals=visual_analyses,
            slicers=slicers,
            optimization_opportunities=opportunities,
        )

    def _analyze_visual(
        self,
        visual_data: Dict,
        page_id: str,
        page_name: str,
        page_filters: List[Dict],
        report_filters: List[Dict],
        slicers: List[SlicerInfo],
    ) -> Optional[VisualAggregationAnalysis]:
        """Analyze aggregation for a single visual."""
        if not self.context_analyzer:
            return None

        visual_id = visual_data.get("name", "")
        visual_info = visual_data.get("visual", {})
        visual_type = visual_info.get("visualType", "unknown")

        # Get visual title
        visual_title = self._get_visual_title(visual_data)

        # Analyze filter context
        filter_context = self.context_analyzer.analyze_visual_context(
            visual_data,
            page_filters=page_filters,
            report_filters=report_filters,
            slicers=slicers,
            page_id=page_id,
        )

        # Determine aggregation level
        level, level_name, reasoning = self.context_analyzer.determine_aggregation_level(
            filter_context
        )

        # Get aggregation table for this level
        agg_table = self.context_analyzer.get_aggregation_table_for_level(level)

        # Extract measures used
        measures_used = self._extract_measures_used(visual_data)

        # Identify which are aggregation-aware
        agg_aware_used = [
            m for m in measures_used if any(am.name == m for am in self.agg_aware_measures)
        ]

        # Generate optimization notes
        optimization_notes = self._generate_visual_optimization_notes(
            filter_context, level, measures_used, agg_aware_used
        )

        return VisualAggregationAnalysis(
            visual_id=visual_id,
            visual_type=visual_type,
            visual_title=visual_title,
            page_id=page_id,
            page_name=page_name,
            measures_used=measures_used,
            agg_aware_measures_used=agg_aware_used,
            columns_in_context=filter_context.all_columns,
            filter_context=filter_context,
            determined_agg_level=level,
            determined_agg_level_name=level_name,
            determined_agg_table=agg_table,
            reasoning=reasoning,
            optimization_notes=optimization_notes,
        )

    def _get_visual_title(self, visual_data: Dict) -> Optional[str]:
        """Extract visual title from visual configuration."""
        visual_objects = visual_data.get("visual", {}).get("visualContainerObjects", {})
        title_config = visual_objects.get("title", [])

        if title_config:
            props = title_config[0].get("properties", {})
            text_prop = props.get("text", {})
            expr = text_prop.get("expr", {})
            literal = expr.get("Literal", {})
            value = literal.get("Value", "")

            if value:
                # Remove surrounding quotes
                return value.strip("'\"")

        return None

    def _extract_measures_used(self, visual_data: Dict) -> List[str]:
        """Extract measure names used in a visual."""
        measures = []

        visual = visual_data.get("visual", {})
        query = visual.get("query", {})
        query_state = query.get("queryState", {})

        for well_name, well_data in query_state.items():
            projections = well_data.get("projections", [])
            for proj in projections:
                field_info = proj.get("field", {})
                measure_info = field_info.get("Measure", {})
                if measure_info:
                    prop = measure_info.get("Property", "")
                    if prop:
                        measures.append(prop)

        return measures

    def _generate_visual_optimization_notes(
        self,
        filter_context: FilterContext,
        level: int,
        measures_used: List[str],
        agg_aware_used: List[str],
    ) -> List[str]:
        """Generate optimization notes for a visual."""
        notes = []

        # Check if using base table when aggregation might be possible
        if level == 1:
            if filter_context.detail_trigger_columns:
                trigger_col = filter_context.detail_trigger_columns[0]
                notes.append(
                    f"Using base table due to {trigger_col}. "
                    f"Consider adding this dimension to an aggregation table if frequently needed."
                )

        # Check for non-aggregation-aware measures
        non_agg_measures = [m for m in measures_used if m not in agg_aware_used]
        if non_agg_measures and level > 1:
            notes.append(
                f"Measures {non_agg_measures} are not aggregation-aware. "
                f"They may not benefit from aggregation tables."
            )

        return notes

    def _identify_page_opportunities(
        self, visual_analyses: List[VisualAggregationAnalysis]
    ) -> List[str]:
        """Identify optimization opportunities for a page."""
        opportunities = []

        # Count visuals at each level
        level_counts = {}
        for va in visual_analyses:
            level_counts[va.determined_agg_level] = level_counts.get(va.determined_agg_level, 0) + 1

        # If many visuals use base table, suggest review
        base_count = level_counts.get(1, 0)
        if base_count > len(visual_analyses) * 0.5 and len(visual_analyses) > 2:
            opportunities.append(
                f"{base_count} of {len(visual_analyses)} visuals require base table. "
                f"Consider reviewing filter context or adding aggregation tables."
            )

        # Identify common detail triggers
        detail_triggers = {}
        for va in visual_analyses:
            if va.determined_agg_level == 1:
                for col in va.filter_context.detail_trigger_columns:
                    detail_triggers[col] = detail_triggers.get(col, 0) + 1

        # Suggest adding frequent triggers to aggregation
        for col, count in detail_triggers.items():
            if count >= 2:
                opportunities.append(
                    f"Column {col} triggers base table in {count} visuals. "
                    f"Consider adding to mid-level aggregation."
                )

        return opportunities

    def _calculate_optimization_score(
        self, level_breakdown: Dict[int, int], total_visuals: int
    ) -> float:
        """Calculate optimization score (0-100)."""
        if total_visuals == 0:
            return 0.0

        # Score based on aggregation usage
        # Level 1 (base) = 0 points, Level 2 = 50 points, Level 3+ = 100 points
        total_points = 0
        for level, count in level_breakdown.items():
            if level == 1:
                total_points += 0 * count
            elif level == 2:
                total_points += 50 * count
            else:
                total_points += 100 * count

        max_points = 100 * total_visuals
        return (total_points / max_points) * 100 if max_points > 0 else 0

    def _generate_recommendations(
        self,
        page_summaries: List[PageAggregationSummary],
        overall_breakdown: Dict[str, int],
        level_breakdown: Dict[int, int],
    ) -> List[str]:
        """Generate overall recommendations."""
        recommendations = []

        total_visuals = sum(level_breakdown.values())
        if total_visuals == 0:
            return ["No visuals analyzed. Ensure report has data visuals."]

        base_count = level_breakdown.get(1, 0)
        base_percentage = (base_count / total_visuals) * 100

        # High base table usage
        if base_percentage > 50:
            recommendations.append(
                f"{base_percentage:.0f}% of visuals use the base table. "
                f"Consider adding more aggregation tables or adjusting report design."
            )

        # Good aggregation usage
        if base_percentage < 25:
            recommendations.append(
                "Good aggregation coverage! Most visuals benefit from aggregation tables."
            )

        # Check for pages with all base table usage
        for page in page_summaries:
            if page.visuals_analyzed > 0:
                page_base = page.agg_level_breakdown.get(1, 0)
                if page_base == page.visuals_analyzed and page.visuals_analyzed > 2:
                    recommendations.append(
                        f"Page '{page.page_name}' uses only base table. "
                        f"Review dimension requirements."
                    )

        # Check if no aggregation level measure detected
        if not self.agg_level_measures:
            recommendations.append(
                "No aggregation level measure detected. "
                "Consider implementing an _AggregationLevel measure for dynamic routing."
            )

        return recommendations

    # =========================================================================
    # Enhanced Analysis Methods
    # =========================================================================

    def _run_quality_analysis(self) -> Optional[AggregationQualityResult]:
        """Run aggregation quality and DAX pattern analysis."""
        if not self.model_data:
            return None

        self.quality_analyzer = AggregationQualityAnalyzer(
            model_data=self.model_data,
            agg_tables=self.agg_tables,
            agg_level_measures=self.agg_level_measures,
            agg_aware_measures=self.agg_aware_measures,
        )

        return self.quality_analyzer.analyze()

    def _run_hit_rate_analysis(
        self, report_summary: ReportAggregationSummary
    ) -> Optional[HitRateAnalysisResult]:
        """Run hit rate and miss reason analysis."""
        self.hit_rate_analyzer = AggregationHitRateAnalyzer(
            agg_tables=self.agg_tables,
            agg_level_measures=self.agg_level_measures,
            agg_aware_measures=self.agg_aware_measures,
            report_summary=report_summary,
        )

        return self.hit_rate_analyzer.analyze()

    def _run_slicer_impact_analysis(
        self, report_summary: ReportAggregationSummary
    ) -> Optional[SlicerImpactResult]:
        """Run slicer impact analysis."""
        self.slicer_impact_analyzer = SlicerImpactAnalyzer(
            agg_tables=self.agg_tables,
            agg_level_measures=self.agg_level_measures,
            report_summary=report_summary,
        )

        return self.slicer_impact_analyzer.analyze()

    def _run_cross_filter_analysis(
        self, report_summary: ReportAggregationSummary
    ) -> Optional[CrossFilterAnalysisResult]:
        """Run cross-filter/cross-highlight analysis."""
        if not self.model_data:
            return None

        # Extract relationships from model_data
        relationships = self.model_data.get("relationships", [])

        self.cross_filter_analyzer = CrossFilterAnalyzer(
            agg_tables=self.agg_tables,
            agg_level_measures=self.agg_level_measures,
            report_summary=report_summary,
            relationships=relationships,
        )

        return self.cross_filter_analyzer.analyze()

    def _run_recommendations(
        self, report_summary: Optional[ReportAggregationSummary]
    ) -> Optional[AggregationRecommendationResult]:
        """Generate automatic aggregation recommendations."""
        if not self.model_data or not report_summary:
            return None

        self.recommender = AggregationRecommender(
            agg_tables=self.agg_tables,
            agg_level_measures=self.agg_level_measures,
            agg_aware_measures=self.agg_aware_measures,
            report_summary=report_summary,
            model_data=self.model_data,
        )

        return self.recommender.recommend()
