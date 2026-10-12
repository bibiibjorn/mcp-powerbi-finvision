"""
Aggregation Quality Analyzer Module

Analyzes the quality of aggregation table designs and DAX patterns for
aggregation-aware measures. Provides scoring and recommendations for improvements.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Set, Tuple
from enum import Enum

from .aggregation_detector import AggregationTable, AggLevelMeasure, AggAwareMeasure

logger = logging.getLogger(__name__)


class QualitySeverity(Enum):
    """Severity levels for quality issues."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


@dataclass
class QualityIssue:
    """Represents a quality issue found in the analysis."""

    category: str  # "grain", "coverage", "relationship", "dax_pattern", "data_type"
    severity: QualitySeverity
    title: str
    description: str
    affected_item: str  # Table or measure name
    recommendation: str
    impact_score: int = 0  # Impact on overall quality (0-10)


@dataclass
class DAXPatternAnalysis:
    """Analysis of DAX patterns in a measure."""

    measure_name: str
    measure_table: str
    has_switch_pattern: bool = False
    has_cached_level_var: bool = False
    uses_isfiltered: bool = False
    uses_iscrossfiltered: bool = False
    has_fallback_handling: bool = False
    has_error_handling: bool = False
    references_agg_level_measure: bool = False
    potential_circular_reference: bool = False
    pattern_quality_score: int = 0  # 0-100
    issues: List[QualityIssue] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)


@dataclass
class AggregationTableQuality:
    """Quality assessment for a single aggregation table."""

    table_name: str
    level: int
    overall_score: int  # 0-100
    grain_score: int  # 0-100 - grain appropriateness
    coverage_score: int  # 0-100 - measure coverage
    relationship_score: int  # 0-100 - relationship quality
    data_type_score: int  # 0-100 - data type optimization
    grain_analysis: Dict[str, Any]
    measure_coverage: Dict[str, Any]
    relationship_analysis: Dict[str, Any]
    issues: List[QualityIssue] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)


@dataclass
class MeasureQuality:
    """Quality assessment for aggregation-aware measures."""

    total_measures: int
    measures_with_issues: int
    average_quality_score: float
    dax_pattern_analyses: List[DAXPatternAnalysis]
    common_issues: List[Tuple[str, int]]  # (issue_type, count)
    recommendations: List[str]


@dataclass
class AggregationQualityResult:
    """Complete quality analysis result."""

    overall_quality_score: int  # 0-100
    table_quality: List[AggregationTableQuality]
    measure_quality: MeasureQuality
    all_issues: List[QualityIssue]
    priority_recommendations: List[str]
    quality_grade: str  # A, B, C, D, F


class AggregationQualityAnalyzer:
    """Analyzes the quality of aggregation implementations."""

    # Known high-cardinality column patterns that should NOT be in aggregations
    HIGH_CARDINALITY_PATTERNS = [
        r"(?i)(id|key|code)$",
        r"(?i)^(transaction|order|invoice|sku|product).*id",
        r"(?i)(guid|uuid|hash)",
        r"(?i)(timestamp|datetime)",
        r"(?i)customer.*name",
        r"(?i)employee.*name",
    ]

    # Ideal grain column patterns for different aggregation levels
    IDEAL_GRAIN_PATTERNS = {
        3: [  # High-level aggregation - time aggregates
            r"(?i)(year|quarter|fiscal.*year)",
            r"(?i)(category|segment|region|channel)",
        ],
        2: [  # Mid-level aggregation - monthly/weekly
            r"(?i)(month|week|period)",
            r"(?i)(category|subcategory|product.*category)",
            r"(?i)(region|territory|store)",
        ],
    }

    # DAX best practice patterns for aggregation-aware measures
    BEST_PRACTICE_PATTERNS = {
        "cached_level_var": r"VAR\s+_\w*[Ll]evel\w*\s*=",
        "switch_true": r"SWITCH\s*\(\s*TRUE\s*\(\s*\)",
        "switch_level": r"SWITCH\s*\(\s*\[_\w*[Ll]evel",
        "isfiltered": r"ISFILTERED\s*\(",
        "iscrossfiltered": r"ISCROSSFILTERED\s*\(",
        "isinscope": r"ISINSCOPE\s*\(",
        "error_handling": r"(IFERROR|IF\s*\(\s*ISERROR)",
        "blank_handling": r"(IF\s*\(\s*ISBLANK|COALESCE)",
    }

    def __init__(
        self,
        agg_tables: List[AggregationTable],
        agg_level_measures: List[AggLevelMeasure],
        agg_aware_measures: List[AggAwareMeasure],
        model_data: Optional[Dict[str, Any]] = None,
        report_measures_used: Optional[Set[str]] = None,
    ):
        """
        Initialize the quality analyzer.

        Args:
            agg_tables: Detected aggregation tables
            agg_level_measures: Detected aggregation level measures
            agg_aware_measures: Detected aggregation-aware measures
            model_data: Full model data for additional context
            report_measures_used: Set of measures actually used in reports
        """
        self.agg_tables = agg_tables
        self.agg_level_measures = agg_level_measures
        self.agg_aware_measures = agg_aware_measures
        self.model_data = model_data or {}
        self.report_measures_used = report_measures_used or set()

        # Build lookup maps
        self._measure_map: Dict[str, AggAwareMeasure] = {m.name: m for m in agg_aware_measures}
        self._table_map: Dict[str, AggregationTable] = {t.name: t for t in agg_tables}

    def analyze(self) -> AggregationQualityResult:
        """
        Perform complete quality analysis.

        Returns:
            AggregationQualityResult with all findings
        """
        logger.info("Starting aggregation quality analysis")

        # Analyze each aggregation table
        table_quality = [self._analyze_table_quality(table) for table in self.agg_tables]

        # Analyze measure quality
        measure_quality = self._analyze_measure_quality()

        # Collect all issues
        all_issues: List[QualityIssue] = []
        for tq in table_quality:
            all_issues.extend(tq.issues)
        all_issues.extend(
            [
                issue
                for analysis in measure_quality.dax_pattern_analyses
                for issue in analysis.issues
            ]
        )

        # Sort issues by severity
        severity_order = {
            QualitySeverity.CRITICAL: 0,
            QualitySeverity.HIGH: 1,
            QualitySeverity.MEDIUM: 2,
            QualitySeverity.LOW: 3,
            QualitySeverity.INFO: 4,
        }
        all_issues.sort(key=lambda x: severity_order[x.severity])

        # Calculate overall score
        table_scores = [tq.overall_score for tq in table_quality] if table_quality else [50]
        measure_score = measure_quality.average_quality_score

        # Weight: 60% table quality, 40% measure quality
        overall_score = int((sum(table_scores) / len(table_scores)) * 0.6 + measure_score * 0.4)

        # Generate priority recommendations
        priority_recommendations = self._generate_priority_recommendations(
            table_quality, measure_quality, all_issues
        )

        # Calculate grade
        quality_grade = self._calculate_grade(overall_score)

        return AggregationQualityResult(
            overall_quality_score=overall_score,
            table_quality=table_quality,
            measure_quality=measure_quality,
            all_issues=all_issues,
            priority_recommendations=priority_recommendations,
            quality_grade=quality_grade,
        )

    def _analyze_table_quality(self, table: AggregationTable) -> AggregationTableQuality:
        """Analyze quality of a single aggregation table."""
        issues: List[QualityIssue] = []
        recommendations: List[str] = []

        # 1. Grain Analysis
        grain_score, grain_analysis, grain_issues = self._analyze_grain(table)
        issues.extend(grain_issues)

        # 2. Measure Coverage Analysis
        coverage_score, coverage_analysis, coverage_issues = self._analyze_measure_coverage(table)
        issues.extend(coverage_issues)

        # 3. Relationship Analysis
        relationship_score, relationship_analysis, rel_issues = self._analyze_relationships(table)
        issues.extend(rel_issues)

        # 4. Data Type Analysis
        data_type_score, data_type_issues = self._analyze_data_types(table)
        issues.extend(data_type_issues)

        # Calculate overall score (weighted average)
        overall_score = int(
            grain_score * 0.30
            + coverage_score * 0.35
            + relationship_score * 0.20
            + data_type_score * 0.15
        )

        # Generate recommendations based on issues
        if grain_score < 70:
            recommendations.append(
                f"Review grain columns for {table.name} - consider removing high-cardinality columns"
            )
        if coverage_score < 50:
            recommendations.append(
                f"Add more aggregated measures to {table.name} to improve coverage"
            )
        if relationship_score < 70:
            recommendations.append(
                f"Verify relationships for {table.name} connect to dimension tables properly"
            )

        return AggregationTableQuality(
            table_name=table.name,
            level=table.level,
            overall_score=overall_score,
            grain_score=grain_score,
            coverage_score=coverage_score,
            relationship_score=relationship_score,
            data_type_score=data_type_score,
            grain_analysis=grain_analysis,
            measure_coverage=coverage_analysis,
            relationship_analysis=relationship_analysis,
            issues=issues,
            recommendations=recommendations,
        )

    def _analyze_grain(
        self, table: AggregationTable
    ) -> Tuple[int, Dict[str, Any], List[QualityIssue]]:
        """Analyze the grain of an aggregation table."""
        issues: List[QualityIssue] = []
        score = 100

        grain_columns = table.grain_columns
        analysis = {
            "grain_columns": grain_columns,
            "column_count": len(grain_columns),
            "high_cardinality_columns": [],
            "ideal_grain_match": False,
            "grain_appropriateness": "unknown",
        }

        if not grain_columns:
            analysis["grain_appropriateness"] = "unknown"
            return 70, analysis, issues  # Neutral if we can't determine

        # Check for high-cardinality columns in grain
        for col in grain_columns:
            col_name = col.split("[")[-1].rstrip("]") if "[" in col else col
            for pattern in self.HIGH_CARDINALITY_PATTERNS:
                if re.search(pattern, col_name):
                    analysis["high_cardinality_columns"].append(col)
                    score -= 20
                    issues.append(
                        QualityIssue(
                            category="grain",
                            severity=QualitySeverity.HIGH,
                            title="High-cardinality column in grain",
                            description=f"Column '{col}' appears to be high-cardinality and may reduce aggregation effectiveness",
                            affected_item=table.name,
                            recommendation=f"Consider removing '{col}' from the aggregation grain or replacing with a lower-cardinality grouping",
                            impact_score=8,
                        )
                    )
                    break

        # Check if grain matches ideal patterns for the level
        ideal_patterns = self.IDEAL_GRAIN_PATTERNS.get(table.level, [])
        matching_patterns = 0
        for col in grain_columns:
            col_name = col.split("[")[-1].rstrip("]") if "[" in col else col
            for pattern in ideal_patterns:
                if re.search(pattern, col_name):
                    matching_patterns += 1
                    break

        if ideal_patterns:
            analysis["ideal_grain_match"] = matching_patterns > 0
            if matching_patterns == 0:
                score -= 15
                analysis["grain_appropriateness"] = "suboptimal"
            elif matching_patterns >= len(grain_columns) / 2:
                analysis["grain_appropriateness"] = "optimal"
            else:
                analysis["grain_appropriateness"] = "acceptable"

        # Check grain column count appropriateness
        if table.level == 3:  # High-level should have fewer grain columns
            if len(grain_columns) > 3:
                score -= 10
                issues.append(
                    QualityIssue(
                        category="grain",
                        severity=QualitySeverity.MEDIUM,
                        title="Too many grain columns for high-level aggregation",
                        description=f"High-level aggregation has {len(grain_columns)} grain columns; consider reducing for better compression",
                        affected_item=table.name,
                        recommendation="High-level aggregations typically work best with 2-3 grain columns",
                        impact_score=5,
                    )
                )
        elif table.level == 2:  # Mid-level
            if len(grain_columns) > 5:
                score -= 10
                issues.append(
                    QualityIssue(
                        category="grain",
                        severity=QualitySeverity.LOW,
                        title="Many grain columns in mid-level aggregation",
                        description=f"Mid-level aggregation has {len(grain_columns)} grain columns",
                        affected_item=table.name,
                        recommendation="Consider if all grain columns are necessary",
                        impact_score=3,
                    )
                )

        return max(0, min(100, score)), analysis, issues

    def _analyze_measure_coverage(
        self, table: AggregationTable
    ) -> Tuple[int, Dict[str, Any], List[QualityIssue]]:
        """Analyze how well the aggregation covers report measures."""
        issues: List[QualityIssue] = []

        # Get aggregated columns in this table
        agg_column_names = {c.name for c in table.aggregated_columns}
        agg_source_columns = {c.source_column for c in table.aggregated_columns if c.source_column}

        # Find measures that reference this aggregation table
        measures_using_table = [
            m for m in self.agg_aware_measures if table.name in m.table_switches.values()
        ]

        # Check coverage of report measures
        report_measures_covered = 0
        report_measures_missing = []

        for measure_name in self.report_measures_used:
            # Check if measure is aggregation-aware and uses this table
            if measure_name in self._measure_map:
                measure = self._measure_map[measure_name]
                if table.name in measure.table_switches.values():
                    report_measures_covered += 1
                else:
                    report_measures_missing.append(measure_name)

        total_report_measures = len(self.report_measures_used)

        analysis = {
            "aggregated_columns": list(agg_column_names),
            "aggregated_column_count": len(agg_column_names),
            # Base-table columns the aggregation summarises (I-23: was computed, never reported).
            "aggregated_source_columns": sorted(agg_source_columns),
            "measures_using_table": len(measures_using_table),
            "report_measures_covered": report_measures_covered,
            "report_measures_missing": report_measures_missing[:10],  # Top 10
            "coverage_percentage": (
                report_measures_covered / total_report_measures * 100
                if total_report_measures > 0
                else 0
            ),
        }

        # Calculate score
        if total_report_measures > 0:
            score = int(analysis["coverage_percentage"])
        else:
            # If we don't know report measures, base on aggregated column count
            score = min(100, len(agg_column_names) * 20)

        # Add issues for missing coverage
        if analysis["coverage_percentage"] < 50 and total_report_measures > 5:
            issues.append(
                QualityIssue(
                    category="coverage",
                    severity=QualitySeverity.MEDIUM,
                    title="Low measure coverage",
                    description=f"Only {analysis['coverage_percentage']:.0f}% of report measures are covered by {table.name}",
                    affected_item=table.name,
                    recommendation="Consider adding more aggregated measures to improve coverage",
                    impact_score=6,
                )
            )

        # Check for commonly needed aggregations that might be missing
        common_agg_patterns = ["Amount", "Quantity", "Count", "Revenue", "Cost", "Profit", "Sales"]
        missing_common = []
        for pattern in common_agg_patterns:
            if not any(pattern.lower() in c.lower() for c in agg_column_names):
                # Check if it exists in the base fact table
                if any(pattern.lower() in m.name.lower() for m in self.agg_aware_measures):
                    missing_common.append(pattern)

        if missing_common:
            analysis["potentially_missing_aggregations"] = missing_common

        return max(0, min(100, score)), analysis, issues

    def _analyze_relationships(
        self, table: AggregationTable
    ) -> Tuple[int, Dict[str, Any], List[QualityIssue]]:
        """Analyze relationship quality for an aggregation table."""
        issues: List[QualityIssue] = []
        score = 100

        related_dims = table.related_dimensions
        grain_tables = set()

        # Extract table names from grain columns
        for col in table.grain_columns:
            if "[" in col:
                table_name = col.split("[")[0].strip("'")
                grain_tables.add(table_name)

        analysis = {
            "related_dimensions": related_dims,
            "grain_source_tables": list(grain_tables),
            "has_dimension_relationships": len(related_dims) > 0,
            "relationship_issues": [],
        }

        # Check if grain tables have relationships
        for grain_table in grain_tables:
            if grain_table not in related_dims and grain_table != table.name:
                analysis["relationship_issues"].append(
                    f"Grain column from {grain_table} but no relationship found"
                )
                score -= 15
                issues.append(
                    QualityIssue(
                        category="relationship",
                        severity=QualitySeverity.MEDIUM,
                        title="Missing relationship to grain source",
                        description=f"Aggregation uses grain from {grain_table} but no relationship is defined",
                        affected_item=table.name,
                        recommendation=f"Ensure proper relationship exists between {table.name} and {grain_table}",
                        impact_score=5,
                    )
                )

        # Check for dimension relationship count
        if len(related_dims) == 0:
            score -= 20
            issues.append(
                QualityIssue(
                    category="relationship",
                    severity=QualitySeverity.HIGH,
                    title="No dimension relationships",
                    description=f"Aggregation table {table.name} has no relationships to dimension tables",
                    affected_item=table.name,
                    recommendation="Create relationships to dimension tables for proper filter propagation",
                    impact_score=7,
                )
            )

        return max(0, min(100, score)), analysis, issues

    def _analyze_data_types(self, table: AggregationTable) -> Tuple[int, List[QualityIssue]]:
        """Analyze data type optimization for aggregation table."""
        issues: List[QualityIssue] = []
        score = 100

        # This would require access to column data types from model_data
        # For now, we'll do pattern-based analysis

        for col in table.aggregated_columns:
            col_name = col.name

            # Check for potentially suboptimal patterns
            if col.aggregation_function == "COUNT" and "Amount" in col_name:
                issues.append(
                    QualityIssue(
                        category="data_type",
                        severity=QualitySeverity.LOW,
                        title="Possible aggregation function mismatch",
                        description=f"Column '{col_name}' uses COUNT but name suggests SUM might be appropriate",
                        affected_item=table.name,
                        recommendation="Verify the aggregation function matches the intended calculation",
                        impact_score=2,
                    )
                )
                score -= 5

        return max(0, min(100, score)), issues

    def _analyze_measure_quality(self) -> MeasureQuality:
        """Analyze the quality of aggregation-aware measures."""
        dax_analyses: List[DAXPatternAnalysis] = []
        issue_counts: Dict[str, int] = {}

        for measure in self.agg_aware_measures:
            analysis = self._analyze_dax_pattern(measure)
            dax_analyses.append(analysis)

            for issue in analysis.issues:
                issue_counts[issue.title] = issue_counts.get(issue.title, 0) + 1

        # Calculate average score
        scores = [a.pattern_quality_score for a in dax_analyses]
        avg_score = sum(scores) / len(scores) if scores else 50

        # Count measures with issues
        measures_with_issues = sum(1 for a in dax_analyses if a.issues)

        # Get common issues
        common_issues = sorted(issue_counts.items(), key=lambda x: -x[1])[:5]

        # Generate recommendations
        recommendations = []
        if any(not a.has_cached_level_var for a in dax_analyses):
            recommendations.append(
                "Cache the aggregation level in a variable (VAR _Level = [_AggregationLevel]) "
                "to avoid recalculating it multiple times"
            )
        if any(a.uses_isfiltered and not a.uses_iscrossfiltered for a in dax_analyses):
            recommendations.append(
                "Consider using ISCROSSFILTERED in addition to ISFILTERED for more accurate "
                "cross-filter detection"
            )
        if any(not a.has_fallback_handling for a in dax_analyses):
            recommendations.append(
                "Add fallback handling for unexpected aggregation levels to prevent errors"
            )

        return MeasureQuality(
            total_measures=len(self.agg_aware_measures),
            measures_with_issues=measures_with_issues,
            average_quality_score=avg_score,
            dax_pattern_analyses=dax_analyses,
            common_issues=common_issues,
            recommendations=recommendations,
        )

    def _analyze_dax_pattern(self, measure: AggAwareMeasure) -> DAXPatternAnalysis:
        """Analyze DAX pattern quality for a single measure."""
        expression = measure.expression
        issues: List[QualityIssue] = []
        recommendations: List[str] = []
        score = 100

        # Check for best practice patterns
        has_cached_level = bool(
            re.search(self.BEST_PRACTICE_PATTERNS["cached_level_var"], expression)
        )
        has_switch_true = bool(re.search(self.BEST_PRACTICE_PATTERNS["switch_true"], expression))
        has_switch_level = bool(re.search(self.BEST_PRACTICE_PATTERNS["switch_level"], expression))
        uses_isfiltered = bool(re.search(self.BEST_PRACTICE_PATTERNS["isfiltered"], expression))
        uses_iscrossfiltered = bool(
            re.search(self.BEST_PRACTICE_PATTERNS["iscrossfiltered"], expression)
        )
        has_error_handling = bool(
            re.search(self.BEST_PRACTICE_PATTERNS["error_handling"], expression)
        )
        has_blank_handling = bool(
            re.search(self.BEST_PRACTICE_PATTERNS["blank_handling"], expression)
        )

        # Check for SWITCH pattern (either TRUE or level variable)
        has_switch_pattern = has_switch_true or has_switch_level

        # Check for level measure reference
        references_level_measure = measure.uses_agg_level_measure is not None

        # Check for potential circular references
        potential_circular = self._check_circular_reference(measure)

        # Determine fallback handling
        # Look for a default case in SWITCH or handling for unexpected levels
        has_fallback = (
            bool(re.search(r"SWITCH\s*\([^)]+,\s*\d+\s*,\s*[^,]+\s*\)", expression))
            or has_blank_handling
        )

        # Score deductions based on missing best practices
        if not has_cached_level and references_level_measure:
            score -= 10
            issues.append(
                QualityIssue(
                    category="dax_pattern",
                    severity=QualitySeverity.LOW,
                    title="Level measure not cached",
                    description="Aggregation level measure is used but not cached in a VAR",
                    affected_item=measure.name,
                    recommendation="Cache the level in a variable: VAR _Level = [_AggregationLevel]",
                    impact_score=3,
                )
            )

        if not has_switch_pattern and references_level_measure:
            score -= 15
            issues.append(
                QualityIssue(
                    category="dax_pattern",
                    severity=QualitySeverity.MEDIUM,
                    title="Missing SWITCH pattern",
                    description="Measure references aggregation level but doesn't use SWITCH pattern",
                    affected_item=measure.name,
                    recommendation="Use SWITCH(TRUE(), ...) or SWITCH([_AggLevel], ...) for level routing",
                    impact_score=5,
                )
            )

        if uses_isfiltered and not uses_iscrossfiltered:
            score -= 5
            recommendations.append("Consider ISCROSSFILTERED for cross-visual filter detection")

        if not has_fallback and has_switch_pattern:
            score -= 10
            issues.append(
                QualityIssue(
                    category="dax_pattern",
                    severity=QualitySeverity.MEDIUM,
                    title="No fallback handling",
                    description="SWITCH pattern has no default/fallback for unexpected levels",
                    affected_item=measure.name,
                    recommendation="Add a default case to handle unexpected aggregation levels",
                    impact_score=4,
                )
            )

        if potential_circular:
            score -= 25
            issues.append(
                QualityIssue(
                    category="dax_pattern",
                    severity=QualitySeverity.CRITICAL,
                    title="Potential circular reference",
                    description="Measure may have a circular reference with the level measure",
                    affected_item=measure.name,
                    recommendation="Review measure dependencies to break the circular reference",
                    impact_score=10,
                )
            )

        return DAXPatternAnalysis(
            measure_name=measure.name,
            measure_table=measure.table,
            has_switch_pattern=has_switch_pattern,
            has_cached_level_var=has_cached_level,
            uses_isfiltered=uses_isfiltered,
            uses_iscrossfiltered=uses_iscrossfiltered,
            has_fallback_handling=has_fallback,
            has_error_handling=has_error_handling,
            references_agg_level_measure=references_level_measure,
            potential_circular_reference=potential_circular,
            pattern_quality_score=max(0, min(100, score)),
            issues=issues,
            recommendations=recommendations,
        )

    def _check_circular_reference(self, measure: AggAwareMeasure) -> bool:
        """Check if measure might have circular reference with level measure."""
        if not self.agg_level_measures:
            return False

        level_measure = self.agg_level_measures[0]

        # Check if level measure references this measure
        if f"[{measure.name}]" in level_measure.expression:
            return True

        # Check if this measure is in the dependencies of the level measure
        # This is a simplified check - full analysis would require expression parsing

        return False

    def _generate_priority_recommendations(
        self,
        table_quality: List[AggregationTableQuality],
        measure_quality: MeasureQuality,
        all_issues: List[QualityIssue],
    ) -> List[str]:
        """Generate prioritized recommendations based on analysis."""
        recommendations: List[str] = []

        # Critical issues first
        critical_issues = [i for i in all_issues if i.severity == QualitySeverity.CRITICAL]
        for issue in critical_issues[:3]:
            recommendations.append(f"[CRITICAL] {issue.recommendation}")

        # High impact issues
        high_issues = [i for i in all_issues if i.severity == QualitySeverity.HIGH]
        for issue in high_issues[:3]:
            recommendations.append(f"[HIGH] {issue.recommendation}")

        # Table-specific recommendations
        for tq in table_quality:
            if tq.overall_score < 60:
                recommendations.append(
                    f"Review {tq.table_name} - overall quality score is {tq.overall_score}/100"
                )

        # Measure quality recommendations
        if measure_quality.average_quality_score < 60:
            recommendations.append(
                "Review aggregation-aware measure DAX patterns - "
                f"average quality is {measure_quality.average_quality_score:.0f}/100"
            )

        # Add measure-specific recommendations
        recommendations.extend(measure_quality.recommendations[:2])

        return recommendations[:10]  # Top 10 recommendations

    def _calculate_grade(self, score: int) -> str:
        """Calculate letter grade from score."""
        if score >= 90:
            return "A"
        elif score >= 80:
            return "B"
        elif score >= 70:
            return "C"
        elif score >= 60:
            return "D"
        else:
            return "F"
