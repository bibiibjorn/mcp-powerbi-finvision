"""
Aggregation Analysis Module

Provides comprehensive analysis of manual aggregation tables in Power BI models,
including detection, filter context analysis, and optimization recommendations.

Enhanced Features:
- Quality Analysis: Design quality & DAX pattern analysis
- Hit Rate Analysis: Aggregation hit rate & miss reasons
- Slicer Impact Analysis: Slicer effects on aggregation levels
- Cross-Filter Analysis: Cross-filter/cross-highlight impact
- Recommendations: Automatic aggregation table recommendations
"""

from .aggregation_detector import (
    AggregationTableDetector,
    AggregationTable,
    AggLevelMeasure,
    AggAwareMeasure,
    AggregatedColumn,
)

from .filter_context_analyzer import (
    FilterContextAnalyzer,
    FilterContext,
    ColumnContext,
    FilterSource,
    SlicerInfo,
)

from .aggregation_analyzer import (
    AggregationAnalyzer,
    AggregationAnalysisResult,
    VisualAggregationAnalysis,
    PageAggregationSummary,
    ReportAggregationSummary,
)

from .aggregation_report_builder import (
    AggregationReportBuilder,
)

# New enhanced analyzers
from .aggregation_quality_analyzer import (
    AggregationQualityAnalyzer,
    AggregationQualityResult,
    AggregationTableQuality,
    MeasureQuality,
    QualityIssue,
    DAXPatternAnalysis,
)

from .aggregation_hit_rate_analyzer import (
    AggregationHitRateAnalyzer,
    HitRateAnalysisResult,
    TableHitRate,
    PageHitRate,
    MissReason,
    MissDetails,
    ColumnMissImpact,
    OpportunityRanking,
)

from .slicer_impact_analyzer import (
    SlicerImpactAnalyzer,
    SlicerImpactResult,
    SlicerAggregationImpact,
    SyncGroupAnalysis,
    PageSlicerSummary,
    SlicerType,
    AggregationImpact,
)

from .cross_filter_analyzer import (
    CrossFilterAnalyzer,
    CrossFilterAnalysisResult,
    VisualInteractionProfile,
    PageInteractionMatrix,
    VisualInteraction,
    InteractionType,
    InteractionImpact,
    RelationshipPath,
)

from .aggregation_recommender import (
    AggregationRecommender,
    AggregationRecommendationResult,
    RecommendedAggregationTable,
    RecommendedGrainColumn,
    RecommendedAggregation,
    RecommendedMeasure,
    RecommendedLevelMeasure,
)

__all__ = [
    # Detector
    "AggregationTableDetector",
    "AggregationTable",
    "AggLevelMeasure",
    "AggAwareMeasure",
    "AggregatedColumn",
    # Filter Context
    "FilterContextAnalyzer",
    "FilterContext",
    "ColumnContext",
    "FilterSource",
    "SlicerInfo",
    # Analyzer
    "AggregationAnalyzer",
    "AggregationAnalysisResult",
    "VisualAggregationAnalysis",
    "PageAggregationSummary",
    "ReportAggregationSummary",
    # Report Builder
    "AggregationReportBuilder",
    # Quality Analyzer
    "AggregationQualityAnalyzer",
    "AggregationQualityResult",
    "AggregationTableQuality",
    "MeasureQuality",
    "QualityIssue",
    "DAXPatternAnalysis",
    # Hit Rate Analyzer
    "AggregationHitRateAnalyzer",
    "HitRateAnalysisResult",
    "TableHitRate",
    "PageHitRate",
    "MissReason",
    "MissDetails",
    "ColumnMissImpact",
    "OpportunityRanking",
    # Slicer Impact Analyzer
    "SlicerImpactAnalyzer",
    "SlicerImpactResult",
    "SlicerAggregationImpact",
    "SyncGroupAnalysis",
    "PageSlicerSummary",
    "SlicerType",
    "AggregationImpact",
    # Cross-Filter Analyzer
    "CrossFilterAnalyzer",
    "CrossFilterAnalysisResult",
    "VisualInteractionProfile",
    "PageInteractionMatrix",
    "VisualInteraction",
    "InteractionType",
    "InteractionImpact",
    "RelationshipPath",
    # Recommender
    "AggregationRecommender",
    "AggregationRecommendationResult",
    "RecommendedAggregationTable",
    "RecommendedGrainColumn",
    "RecommendedAggregation",
    "RecommendedMeasure",
    "RecommendedLevelMeasure",
]
