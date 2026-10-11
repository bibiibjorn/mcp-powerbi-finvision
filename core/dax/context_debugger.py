"""
DAX Context Debugger - Step-by-step context tracking (Layer 2 of 3)

See context_analyzer.py for the three-layer split description (P2-36).
This file is Layer 2: step-through evaluator. Consumes DaxContextAnalyzer.

Provides:
- Step-through evaluation with context tracking
- Context inspection at breakpoints
- Performance profiling per step
- Optimization suggestions
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Any

from .context_analyzer import DaxContextAnalyzer, ContextFlowExplanation

logger = logging.getLogger(__name__)


@dataclass
class EvaluationStep:
    """Represents a single evaluation step"""

    step_number: int
    code_fragment: str
    filter_context: Dict[str, List[Any]]
    row_context: Optional[Dict[str, Any]]
    intermediate_result: Optional[Any]
    explanation: str
    execution_time_ms: Optional[float] = None


@dataclass
class ContextExplanation:
    """Explanation of context at a specific position"""

    position: int
    line: int
    column: int
    filter_context: Dict[str, List[str]]
    row_context: Optional[Dict[str, Any]]
    in_transition: bool
    transition_type: Optional[str]
    explanation: str


@dataclass
class Optimization:
    """Optimization suggestion"""

    severity: str  # "info", "warning", "critical"
    category: str  # "performance", "readability", "correctness"
    message: str
    suggestion: str
    code_example: Optional[str] = None


class DaxContextDebugger:
    """
    Step-by-step DAX debugger with context tracking

    Features:
    - Step through DAX evaluation
    - Track filter and row context at each step
    - Performance profiling
    - Optimization suggestions
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize debugger

        Args:
            config: Optional configuration dict
        """
        self.config = config or {}
        self.max_steps = self.config.get("max_steps", 100)
        self.analyzer = DaxContextAnalyzer(config)

    def step_through(
        self,
        dax_expression: str,
        breakpoints: Optional[List[int]] = None,
    ) -> List[EvaluationStep]:
        """
        Step through DAX evaluation with context tracking

        Args:
            dax_expression: DAX expression to debug
            breakpoints: Optional character positions. When given, only the step
                ACTIVE at each position is returned (the context transition with
                the greatest location <= the breakpoint). Review E-23: the
                argument used to be ignored; the unused ``sample_data`` argument
                was removed (nothing is evaluated — ``intermediate_result`` is
                always None).

        Returns:
            List of evaluation steps
        """
        try:
            steps: List[EvaluationStep] = []

            # First, analyze context transitions
            analysis = self.analyzer.analyze_context_transitions(dax_expression)

            # Create steps from transitions
            for i, transition in enumerate(analysis.transitions):
                step = EvaluationStep(
                    step_number=i + 1,
                    code_fragment=self._extract_code_fragment(dax_expression, transition.location),
                    filter_context=self._infer_filter_context(transition),
                    row_context=self._infer_row_context(transition),
                    intermediate_result=None,  # Would require actual execution
                    explanation=transition.explanation,
                    execution_time_ms=None,
                )

                steps.append(step)

                # Check if we've reached max steps
                if len(steps) >= self.max_steps:
                    logger.warning(f"Reached max steps limit: {self.max_steps}")
                    break

            if breakpoints:
                locations = [t.location for t in analysis.transitions[: len(steps)]]
                keep = set()
                for bp in breakpoints:
                    try:
                        bp_i = int(bp)
                    except (TypeError, ValueError):
                        continue
                    active = [i for i, loc in enumerate(locations) if loc <= bp_i]
                    if active:
                        keep.add(max(active, key=lambda i: locations[i]))
                steps = [st for i, st in enumerate(steps) if i in keep]

            logger.info(f"Generated {len(steps)} evaluation steps")

            return steps

        except Exception as e:
            logger.error(f"Error in step-through debugging: {e}", exc_info=True)
            return []

    def suggest_optimizations(
        self,
        context_analysis: ContextFlowExplanation,
    ) -> List[Optimization]:
        """
        Suggest optimizations based on context analysis

        Args:
            context_analysis: Context analysis result

        Returns:
            List of optimization suggestions
        """
        optimizations: List[Optimization] = []

        try:
            # Check for excessive CALCULATE nesting
            if context_analysis.max_nesting_level > 3:
                optimizations.append(
                    Optimization(
                        severity="warning",
                        category="performance",
                        message=f"Excessive CALCULATE nesting ({context_analysis.max_nesting_level} levels)",
                        suggestion="Consider using variables (VAR) to store intermediate calculations and reduce nesting depth.",
                        code_example="""// Instead of:
CALCULATE(
    CALCULATE(
        CALCULATE([Measure], Filter1),
        Filter2
    ),
    Filter3
)

// Use:
VAR Step1 = CALCULATE([Measure], Filter1)
VAR Step2 = CALCULATE(Step1, Filter2)
RETURN CALCULATE(Step2, Filter3)""",
                    )
                )

            # Check for iterator + measure combinations
            iterator_transitions = [
                t for t in context_analysis.transitions if t.type.value == "iterator"
            ]

            if len(iterator_transitions) > 3:
                optimizations.append(
                    Optimization(
                        severity="warning",
                        category="performance",
                        message=f"Multiple iterators with measure references ({len(iterator_transitions)} detected)",
                        suggestion="Each measure reference in an iterator causes context transition per row. Consider pre-calculating values using variables or switching to iterators that work with columns.",
                        code_example="""// Instead of:
SUMX(Sales, [Total Sales] * [Tax Rate])

// Use:
SUMX(Sales, Sales[Amount] * Sales[TaxRate])

// Or use variables:
VAR TotalSales = [Total Sales]
VAR TaxRate = [Tax Rate]
RETURN SUMX(Sales, TotalSales * TaxRate)""",
                    )
                )

            # Check complexity score
            if context_analysis.complexity_score > 70:
                optimizations.append(
                    Optimization(
                        severity="critical",
                        category="readability",
                        message=f"High complexity score ({context_analysis.complexity_score}/100)",
                        suggestion="Consider breaking this measure into multiple simpler measures for better maintainability and performance.",
                        code_example=None,
                    )
                )

            # Check for implicit measure references
            implicit_refs = [
                t for t in context_analysis.transitions if t.type.value == "implicit_measure"
            ]

            if len(implicit_refs) > 10:
                optimizations.append(
                    Optimization(
                        severity="info",
                        category="performance",
                        message=f"Many implicit measure references ({len(implicit_refs)})",
                        suggestion="While implicit CALCULATE is convenient, excessive use can impact performance. Consider whether all references need to be measures.",
                        code_example=None,
                    )
                )

            # Add general best practices
            if context_analysis.transitions:
                optimizations.append(
                    Optimization(
                        severity="info",
                        category="correctness",
                        message="Context transition detected",
                        suggestion="Ensure that context transitions are intentional. Use DAX Studio or Tabular Editor to verify query plans and performance.",
                        code_example=None,
                    )
                )

            logger.info(f"Generated {len(optimizations)} optimization suggestions")

        except Exception as e:
            logger.error(f"Error generating optimizations: {e}", exc_info=True)

        return optimizations

    def generate_improved_dax(
        self,
        dax_expression: str,
        context_analysis: ContextFlowExplanation,
        anti_patterns: Optional[Dict[str, Any]] = None,
        vertipaq_analysis: Optional[Dict[str, Any]] = None,
        connection_state=None,  # Kept for API compatibility but not used
    ) -> Dict[str, Any]:
        """
        Generate DAX improvement SUGGESTIONS for the AI to use.

        The code rewriter provides suggestions and a draft optimized code,
        but the AI has the final say and should write its own optimized measure
        based on ALL the analysis (context transitions, anti-patterns, VertiPaq, etc.).

        Args:
            dax_expression: Original DAX expression
            context_analysis: Context analysis result
            anti_patterns: Deprecated and ignored (2026-10-03 review D-01): the regex research
                engine behind it was removed; findings come from the unified analyzer only
            vertipaq_analysis: Optional VertiPaq column analysis results
            connection_state: Not used (kept for API compatibility)

        Returns:
            Dictionary with improvement suggestions and draft code for AI to review
        """
        improvements = []
        rewriter_suggestion = None  # Draft from code rewriter - AI should review/improve
        transformation_details = []

        try:
            # STEP 1: Get code rewriter suggestions (draft for AI to review)
            from core.dax.code_rewriter import DaxCodeRewriter

            rewriter = DaxCodeRewriter()
            rewrite_result = rewriter.rewrite_dax(dax_expression)

            if rewrite_result.get("success") and rewrite_result.get("has_changes"):
                rewriter_suggestion = rewrite_result["rewritten_code"]

                # Add rewriter transformations as suggestions for AI
                for trans in rewrite_result.get("transformations", []):
                    improvements.append(
                        {
                            "issue": f"Code transformation: {trans['type'].replace('_', ' ').title()}",
                            "severity": "medium",
                            "original_code": (
                                trans["original"][:100] + "..."
                                if len(trans["original"]) > 100
                                else trans["original"]
                            ),
                            "improved_code": (
                                trans["transformed"][:100] + "..."
                                if len(trans["transformed"]) > 100
                                else trans["transformed"]
                            ),
                            "explanation": trans["explanation"],
                            "specific_suggestion": f"Estimated improvement: {trans['estimated_improvement']}",
                            "auto_transform_available": True,
                        }
                    )
                    transformation_details.append(trans["explanation"])

                logger.info(
                    f"Code rewriter suggested {rewrite_result['transformation_count']} transformations"
                )

            # STEP 2: Detect pattern-based improvement opportunities
            # These are detected but not automatically transformed (requires DAX parser)
            # The AI tool will use these recommendations to generate optimized code

            # Detection 1: Iterator with measure references
            iterator_transitions = [
                t
                for t in context_analysis.transitions
                if t.type.value == "iterator" and t.function in ["SUMX", "AVERAGEX", "COUNTX"]
            ]

            if iterator_transitions:
                improvement = {
                    "issue": "Iterator with measure references causing row-by-row context transitions",
                    "severity": "medium",
                    "original_pattern": "SUMX(Table, [Measure])",
                    "improved_pattern": "SUMX(Table, Table[Column])",
                    "explanation": "Replace measure references inside iterators with direct column references to avoid context transitions in each iteration.",
                    "specific_suggestion": f"Found {len(iterator_transitions)} iterator(s) with potential measure references. Review each iterator to see if measure can be replaced with column reference.",
                    "auto_transform_available": False,
                }
                improvements.append(improvement)

            # Detection 2: Excessive CALCULATE nesting
            if context_analysis.max_nesting_level > 2:
                nested_calcs = [
                    t
                    for t in context_analysis.transitions
                    if t.type.value == "explicit_calculate" and t.nested_level > 1
                ]

                if nested_calcs:
                    improvement = {
                        "issue": f"Excessive CALCULATE nesting (depth: {context_analysis.max_nesting_level})",
                        "severity": "high",
                        "original_pattern": "CALCULATE(CALCULATE([Measure], Filter1), Filter2)",
                        "improved_pattern": (
                            "-- Equivalent ONLY when the inner CALCULATE is the whole first argument,\n"
                            "-- no level uses ALL/REMOVEFILTERS/KEEPFILTERS/USERELATIONSHIP and\n"
                            "-- Filter1/Filter2 are on DIFFERENT columns:\n"
                            "CALCULATE([Measure], Filter1, Filter2)\n"
                            "-- Otherwise keep the nesting: an inner filter OVERRIDES an outer filter on\n"
                            "-- the same column, while filters of one CALCULATE intersect.\n"
                            "-- Never CALCULATE(<VAR>, Filter2): a VAR is a constant, the filter is ignored."
                        ),
                        "explanation": (
                            "Deep CALCULATE nesting is hard to read. Merging levels is a semantic "
                            "change unless the conditions above hold — verify with 09_Validate."
                        ),
                        "specific_suggestion": (
                            "Merge levels only where the filters are on disjoint columns and no "
                            "modifier is involved; otherwise document why the nesting is needed."
                        ),
                        "auto_transform_available": False,
                    }
                    improvements.append(improvement)

            # Detection 4: Multiple implicit measure references
            implicit_refs = [
                t for t in context_analysis.transitions if t.type.value == "implicit_measure"
            ]

            if len(implicit_refs) > 5:
                improvement = {
                    "issue": f"Multiple implicit measure references ({len(implicit_refs)} detected)",
                    "severity": "medium",
                    "explanation": (
                        "Each measure reference creates an implicit CALCULATE wrapper. A measure "
                        "used several times in the SAME filter context (not inside an iterator, "
                        "CALCULATE filter or time-intelligence call) can be cached in a VAR."
                    ),
                    "original_pattern": "[Measure1] + [Measure2] + [Measure3]",
                    "improved_pattern": """VAR M1 = [Measure1]
VAR M2 = [Measure2]
VAR M3 = [Measure3]
RETURN M1 + M2 + M3""",
                    "specific_suggestion": "Store measure results in variables when the same measure is referenced multiple times or when measures are used in complex expressions.",
                    "auto_transform_available": False,
                }
                improvements.append(improvement)

            # Determine if we have improvements
            has_improvements = len(improvements) > 0

            # Validate rewriter draft — discard if it contains obvious errors
            validated_draft = None
            if rewriter_suggestion:
                draft = rewriter_suggestion.strip()
                # Check for circular references: VAR _@col = [@col] inside same ADDCOLUMNS
                # Check for obviously broken patterns
                import re as _re

                has_circular_ref = bool(_re.search(r"VAR\s+_@\w+\s*=\s*\[@\w+\]", draft))
                if has_circular_ref:
                    logger.warning(
                        "Rewriter draft contains circular extended column references — discarding"
                    )
                else:
                    validated_draft = draft

            return {
                "has_improvements": has_improvements,
                "improvements_count": len(improvements),
                "improvements": improvements,
                "original_code": dax_expression.strip(),
                "rewriter_draft": validated_draft,
                "summary": self._generate_improvement_summary(improvements),
                "transformation_details": transformation_details,
            }

        except Exception as e:
            logger.error(f"Error generating improved DAX: {e}", exc_info=True)
            return {
                "has_improvements": False,
                "code_actually_transformed": False,
                "improvements_count": 0,
                "improvements": [],
                "error": str(e),
            }

    def _generate_var_based_code(self, dax_expression: str, nested_calcs: List) -> str:
        """
        Generate improved code using variables instead of nested CALCULATE

        This is a simplified version - full implementation would need DAX parsing
        """
        # Simple heuristic-based transformation
        # In production, this would use proper DAX parsing
        if "CALCULATE" not in dax_expression.upper():
            return dax_expression

        # Add comment suggesting variable-based approach
        suggestion = f"""// SUGGESTED IMPROVEMENT: Use variables to reduce nesting
// Original code had {len(nested_calcs)} nested CALCULATE statement(s)

VAR Result =
    // Break down nested CALCULATE into sequential steps
    // Step 1: Apply first filter context
    // Step 2: Apply additional filters
    // Step 3: Final calculation
{dax_expression}

RETURN Result

// NOTE: This is a template. Manually refactor nested CALCULATE statements
// into separate VAR statements for each calculation step."""

        return suggestion

    def _generate_improvement_summary(self, improvements: List[Dict[str, Any]]) -> str:
        """Generate human-readable summary of improvements"""
        if not improvements:
            return "No significant improvements identified. Your DAX code follows best practices."

        high_severity = sum(1 for i in improvements if i.get("severity") == "high")
        medium_severity = sum(1 for i in improvements if i.get("severity") == "medium")
        low_severity = sum(1 for i in improvements if i.get("severity") == "low")
        auto_available = sum(1 for i in improvements if i.get("auto_transform_available"))

        summary_parts = [f"Found {len(improvements)} optimization opportunity(ies):"]

        if high_severity > 0:
            summary_parts.append(
                f"  • {high_severity} high-priority issue(s) - should be addressed"
            )
        if medium_severity > 0:
            summary_parts.append(
                f"  • {medium_severity} medium-priority improvement(s) - recommended"
            )
        if low_severity > 0:
            summary_parts.append(f"  • {low_severity} low-priority enhancement(s) - optional")

        if auto_available > 0:
            summary_parts.append(
                f"\n{auto_available} optimization(s) have draft code from the rewriter."
            )
            summary_parts.append(
                "The AI will review all analysis and write the final optimized measure."
            )

        return "\n".join(summary_parts)

    def _extract_code_fragment(self, dax: str, position: int) -> str:
        """Extract code fragment around position"""
        start = max(0, position - 30)
        end = min(len(dax), position + 30)

        fragment = dax[start:end]

        # Add markers
        marker_pos = min(30, position - start)
        return fragment[:marker_pos] + " ▶ " + fragment[marker_pos:]

    def _infer_filter_context(self, transition) -> Dict[str, List[Any]]:
        """Infer filter context (simplified - would need runtime data)"""
        # This is a placeholder - actual implementation would need
        # to track filter modifications through CALCULATE arguments
        return {}

    def _infer_row_context(self, transition) -> Optional[Dict[str, Any]]:
        """Infer row context (simplified - would need runtime data)"""
        if transition.type.value == "iterator":
            return {"type": "iterator", "function": transition.function}
        return None

    def generate_debug_report(
        self,
        dax_expression: str,
        include_profiling: bool = True,
        include_optimization: bool = True,
        connection_state=None,
    ) -> str:
        """
        Generate comprehensive debug report with all enhancements

        Args:
            dax_expression: DAX expression to analyze
            include_profiling: Include performance profiling
            include_optimization: Include optimization suggestions
            connection_state: Optional connection state for advanced analysis

        Returns:
            Formatted debug report
        """
        lines = []

        lines.append("=" * 70)
        lines.append("DAX INTELLIGENCE ENHANCED ANALYSIS REPORT")
        lines.append("=" * 70)
        lines.append("")

        # Context analysis
        analysis = self.analyzer.analyze_context_transitions(dax_expression)

        lines.append("CONTEXT ANALYSIS")
        lines.append("-" * 70)
        lines.append(analysis.summary)
        lines.append("")
        lines.append(f"Complexity Score: {analysis.complexity_score}/100")
        lines.append(f"Max Nesting Level: {analysis.max_nesting_level}")
        lines.append("")

        # Transitions
        if analysis.transitions:
            lines.append("CONTEXT TRANSITIONS")
            lines.append("=" * 70)
            lines.append("")

            for i, t in enumerate(analysis.transitions, 1):
                # Impact icon
                impact_icon = {"low": "🟢", "medium": "🟡", "high": "🔴", "critical": "⛔"}.get(
                    t.performance_impact.value, "⚪"
                )

                # Type icon
                type_icon = {
                    "iterator": "🔄",
                    "implicit_measure": "📊",
                    "filter_context": "🔍",
                    "row_context": "📍",
                }.get(t.type.value, "•")

                lines.append(f"┌─ Transition #{i} ─────────────────────────────────────────────")
                lines.append(f"│ {type_icon} {t.function} (Line {t.line}, Col {t.column})")
                lines.append("│")
                lines.append(f"│ Type: {t.type.value}")
                lines.append(f"│ Impact: {impact_icon} {t.performance_impact.value.upper()}")
                lines.append("│")
                lines.append(f"│ {t.explanation}")
                lines.append(f"└{'─' * 65}")
                lines.append("")

        # Warnings
        if analysis.warnings:
            lines.append("PERFORMANCE WARNINGS")
            lines.append("-" * 70)

            for w in analysis.warnings:
                lines.append(f"⚠️  {w.message}")
                lines.append(f"   💡 {w.suggestion}")
                lines.append("")

        # Static analysis - the ONE rule engine (unified analyzer). The former regex "research
        # article" block contradicted these rules (2026-10-03 review D-01).
        lines.append("STATIC ANALYSIS (unified rule engine)")
        lines.append("=" * 70)

        unified = None
        try:
            from core.dax.analyzer.unified_analyzer import get_shared_analyzer

            unified = get_shared_analyzer().analyze(dax_expression)
        except Exception as e:  # never break the report on a rule failure
            lines.append(f"⚠️  Static analysis could not be completed: {e}")
            lines.append("")

        if unified is not None:
            findings = [i for i in unified.issues if i.severity != "info"]
            if findings:
                lines.append(
                    f"Detected {len(findings)} finding(s) (health score {unified.health_score}/100)"
                )
                lines.append("")
                for issue in findings:
                    lines.append(f"⚠️  [{issue.severity.upper()}] {issue.title}")
                    lines.append(f"   Rule: {issue.rule_id}")
                    if issue.location:
                        lines.append(f"   Where: {issue.location}")
                    if issue.fix_suggestion:
                        lines.append(f"   Fix: {issue.fix_suggestion}")
                    lines.append("")
            else:
                lines.append("✅ No findings above info level.")
                lines.append("")

        # Generate specific improvements with new DAX code
        improvements = None
        vertipaq_analysis_result = None

        # Get VertiPaq analysis if connection available
        if connection_state:
            try:
                from core.dax.vertipaq_analyzer import VertiPaqAnalyzer

                vertipaq = VertiPaqAnalyzer(connection_state)
                vertipaq_analysis_result = vertipaq.analyze_dax_columns(dax_expression)
            except Exception:
                pass

        if include_optimization:
            improvements = self.generate_improved_dax(
                dax_expression=dax_expression,
                context_analysis=analysis,
                vertipaq_analysis=vertipaq_analysis_result,
            )

            if improvements.get("has_improvements"):
                lines.append("SPECIFIC IMPROVEMENTS & NEW DAX CODE")
                lines.append("=" * 70)
                lines.append("")
                lines.append(improvements["summary"])
                lines.append("")

                for i, improvement in enumerate(improvements["improvements"], 1):
                    severity_icon = (
                        "🔴"
                        if improvement.get("severity") == "high"
                        else "🟡" if improvement.get("severity") == "medium" else "🔵"
                    )
                    lines.append(f"{severity_icon} IMPROVEMENT {i}: {improvement['issue']}")
                    lines.append("-" * 70)
                    lines.append(f"Explanation: {improvement['explanation']}")
                    lines.append("")

                    if improvement.get("specific_suggestion"):
                        lines.append(f"💡 Specific Action: {improvement['specific_suggestion']}")
                        lines.append("")

                    # Show original pattern with DAX formatting
                    if improvement.get("original_pattern"):
                        lines.append("❌ ORIGINAL PATTERN:")
                        lines.append("")
                        lines.append("```dax")
                        lines.append(improvement["original_pattern"])
                        lines.append("```")
                        lines.append("")

                    # Show improved pattern with DAX formatting
                    if improvement.get("improved_pattern"):
                        lines.append("✅ IMPROVED PATTERN:")
                        lines.append("")
                        lines.append("```dax")
                        lines.append(improvement["improved_pattern"])
                        lines.append("```")
                        lines.append("")

                    # Show full improved code if available with DAX formatting
                    if improvement.get("improved_code"):
                        lines.append("✅ SUGGESTED REFACTORED CODE:")
                        lines.append("")
                        lines.append("```dax")
                        lines.append(improvement["improved_code"])
                        lines.append("```")
                        lines.append("")

                    lines.append("")

        # Traditional Optimizations (generic patterns)
        if include_optimization:
            optimizations = self.suggest_optimizations(analysis)

            if optimizations:
                lines.append("GENERAL OPTIMIZATION PATTERNS")
                lines.append("-" * 70)

                for opt in optimizations:
                    icon = (
                        "🔴"
                        if opt.severity == "critical"
                        else "🟡" if opt.severity == "warning" else "🔵"
                    )
                    lines.append(f"{icon} [{opt.category.upper()}] {opt.message}")
                    lines.append(f"   💡 {opt.suggestion}")

                    if opt.code_example:
                        lines.append("   Example:")
                        for line in opt.code_example.split("\n"):
                            lines.append(f"      {line}")

                    lines.append("")

        # ===== NEW ENHANCEMENTS =====

        # VertiPaq Column Analysis
        if connection_state:
            try:
                from core.dax.vertipaq_analyzer import VertiPaqAnalyzer

                vertipaq = VertiPaqAnalyzer(connection_state)
                column_analysis = vertipaq.analyze_dax_columns(dax_expression)

                if (
                    column_analysis.get("success")
                    and column_analysis.get("columns_analyzed", 0) > 0
                ):
                    lines.append("VERTIPAQ COLUMN ANALYSIS")
                    lines.append("=" * 70)

                    # Show columns referenced with their metrics
                    col_details = column_analysis.get("column_analysis", {})
                    lines.append(f"Columns Referenced: {column_analysis['columns_analyzed']}")
                    lines.append("")

                    # Track if any columns were calculated vs DMV
                    has_calculated = False

                    # Show all columns with their cardinality
                    for col_ref, col_data in col_details.items():
                        if isinstance(col_data, dict) and "cardinality" in col_data:
                            cardinality = col_data.get("cardinality", 0)
                            encoding = col_data.get("encoding", "unknown")

                            if encoding == "calculated":
                                lines.append(
                                    f"   • {col_ref} - Cardinality: {cardinality:,} (calculated)"
                                )
                                has_calculated = True
                            else:
                                lines.append(f"   • {col_ref} - Cardinality: {cardinality:,}")
                        else:
                            # Column without metrics
                            lines.append(f"   • {col_ref} - No metrics available")

                    lines.append("")

                    # Show totals
                    total_cardinality = column_analysis.get("total_cardinality", 0)
                    total_size_mb = column_analysis.get("total_size_mb", 0)

                    lines.append(f"Total Cardinality: {total_cardinality:,}")

                    # Note about size calculation
                    if has_calculated:
                        lines.append(
                            f"Total Size: {total_size_mb:.2f} MB (estimated for calculated columns)"
                        )
                    else:
                        lines.append(f"Total Size: {total_size_mb:.2f} MB")

                    lines.append("")

                    if column_analysis.get("high_cardinality_columns"):
                        lines.append("⚠️  HIGH-CARDINALITY COLUMNS:")
                        for col in column_analysis["high_cardinality_columns"]:
                            col_data = column_analysis["column_analysis"].get(col, {})
                            lines.append(f"   • {col}")
                            lines.append(f"     Cardinality: {col_data.get('cardinality', 0):,}")
                            lines.append(f"     Size: {col_data.get('size_mb', 0):.2f} MB")
                            lines.append(
                                f"     Impact: {col_data.get('performance_impact', 'unknown').upper()}"
                            )
                            lines.append(f"     💡 {col_data.get('recommendation', '')}")
                            lines.append("")

                    if column_analysis.get("optimizations"):
                        lines.append("DATA TYPE OPTIMIZATIONS:")
                        for opt in column_analysis["optimizations"]:
                            lines.append(f"   {opt['severity'].upper()}: {opt['issue']}")
                            lines.append(f"   💡 {opt['recommendation']}")
                            lines.append("")

                    lines.append("")

            except Exception as e:
                logger.warning(f"VertiPaq analysis failed: {e}", exc_info=True)
                lines.append("VERTIPAQ COLUMN ANALYSIS")
                lines.append("=" * 70)
                lines.append(f"⚠️  VertiPaq analysis could not be completed: {str(e)}")
                lines.append("")

        # Call Tree Visualization
        try:
            from core.dax.call_tree_builder import CallTreeBuilder

            call_tree_builder = CallTreeBuilder()
            if connection_state:
                try:
                    from core.dax.vertipaq_analyzer import VertiPaqAnalyzer

                    vertipaq = VertiPaqAnalyzer(connection_state)
                    call_tree_builder.vertipaq_analyzer = vertipaq
                except Exception:
                    pass

            call_tree = call_tree_builder.build_call_tree(dax_expression)

            lines.append("CALL TREE HIERARCHY")
            lines.append("=" * 70)
            lines.append("")
            lines.append("```")
            tree_viz = call_tree_builder.visualize_tree(call_tree)
            lines.append(tree_viz)
            lines.append("```")
            lines.append("")

            # Show iteration estimates
            def count_iterations(node):
                total = node.estimated_iterations or 0
                for child in node.children:
                    total += count_iterations(child)
                return total

            total_iterations = count_iterations(call_tree)
            if total_iterations > 0:
                lines.append(f"📊 ESTIMATED ITERATIONS: {total_iterations:,}")
                if total_iterations >= 1_000_000:
                    lines.append("   🔴 CRITICAL: Over 1 million iterations!")
                    lines.append("   This will cause severe performance issues.")
                elif total_iterations >= 100_000:
                    lines.append("   ⚠️  WARNING: Over 100,000 iterations")
                    lines.append("   Consider optimization.")
                lines.append("")

        except Exception as e:
            logger.warning(f"Call tree analysis failed: {e}", exc_info=True)
            lines.append("CALL TREE HIERARCHY")
            lines.append("=" * 70)
            lines.append(f"⚠️  Call tree analysis could not be completed: {str(e)}")
            lines.append("   💡 This usually means measure dependencies couldn't be resolved.")
            lines.append("")

        # Calculation Group Analysis
        if connection_state:
            try:
                from core.dax.calculation_group_analyzer import CalculationGroupAnalyzer

                calc_group_analyzer = CalculationGroupAnalyzer(connection_state)
                calc_group_analysis = calc_group_analyzer.analyze_dax_with_calc_groups(
                    dax_expression
                )

                if (
                    calc_group_analysis.get("success")
                    and calc_group_analysis.get("group_count", 0) > 0
                ):
                    lines.append("CALCULATION GROUP ANALYSIS")
                    lines.append("=" * 70)
                    lines.append(
                        f"Calculation groups detected: {calc_group_analysis['group_count']}"
                    )
                    lines.append(f"Groups: {', '.join(calc_group_analysis['groups_detected'])}")
                    lines.append(
                        f"Performance impact: {calc_group_analysis['performance_impact'].upper()}"
                    )
                    lines.append("")

                    if calc_group_analysis.get("precedence_conflicts"):
                        lines.append("⚠️  PRECEDENCE CONFLICTS:")
                        for conflict in calc_group_analysis["precedence_conflicts"]:
                            lines.append(
                                f"   {conflict['severity'].upper()}: {conflict['warning']}"
                            )
                            group_list = ", ".join(
                                [
                                    g["name"] + " (prec: " + str(g["precedence"]) + ")"
                                    for g in conflict["groups"]
                                ]
                            )
                            lines.append(f"   Groups: {group_list}")
                            lines.append("")

                    if calc_group_analysis.get("issues"):
                        lines.append("CALCULATION GROUP ISSUES:")
                        for issue in calc_group_analysis["issues"]:
                            lines.append(f"   {issue['severity'].upper()}: {issue['message']}")
                            lines.append(f"   💡 {issue['recommendation']}")
                            lines.append("")

                    if calc_group_analysis.get("recommendations"):
                        lines.append("RECOMMENDATIONS:")
                        for rec in calc_group_analysis["recommendations"]:
                            lines.append(f"   • {rec}")
                        lines.append("")

            except Exception as e:
                logger.warning(f"Calculation group analysis failed: {e}", exc_info=True)
                lines.append("CALCULATION GROUP ANALYSIS")
                lines.append("=" * 70)
                lines.append(f"⚠️  Calculation group analysis could not be completed: {str(e)}")
                lines.append("")

        # SUMMARIZE Pattern Detection
        summarize_analysis = self.analyzer.detect_summarize_patterns(dax_expression)
        if summarize_analysis.get("has_summarize"):
            lines.append("SUMMARIZE vs SUMMARIZECOLUMNS")
            lines.append("=" * 70)
            lines.append(f"⚠️  {summarize_analysis['recommendation']}")
            lines.append("")
            lines.append("Example conversion:")
            lines.append(summarize_analysis["example_conversion"])
            lines.append("")

        # Variable Optimization Scanner
        try:
            from core.dax.code_rewriter import VariableOptimizationScanner

            var_scanner = VariableOptimizationScanner()
            var_analysis = var_scanner.scan_for_optimizations(dax_expression)

            if var_analysis.get("success") and var_analysis.get("opportunities_found", 0) > 0:
                lines.append("VARIABLE OPTIMIZATION OPPORTUNITIES")
                lines.append("=" * 70)
                lines.append(var_analysis["recommendation"])
                lines.append("")

                for opp in var_analysis["opportunities"][:5]:  # Show top 5
                    lines.append(f"   {opp['priority'].upper()}: {opp['type']}")
                    lines.append(f"   {opp['suggestion']}")
                    if opp.get("example_code"):
                        lines.append(f"   Example:\n   {opp['example_code']}")
                    lines.append("")

        except Exception as e:
            logger.warning(f"Variable optimization scan failed: {e}", exc_info=True)
            lines.append("VARIABLE OPTIMIZATION OPPORTUNITIES")
            lines.append("=" * 70)
            lines.append(f"⚠️  Variable optimization scan could not be completed: {str(e)}")
            lines.append("")

        # Code Rewriting Suggestions
        if include_optimization:
            try:
                from core.dax.code_rewriter import DaxCodeRewriter

                rewriter = DaxCodeRewriter()
                rewrite_result = rewriter.rewrite_dax(dax_expression)

                if rewrite_result.get("has_changes"):
                    lines.append("CODE REWRITING SUGGESTIONS")
                    lines.append("=" * 70)
                    lines.append(
                        f"Transformations applied: {rewrite_result['transformation_count']}"
                    )
                    lines.append("")

                    for trans in rewrite_result["transformations"]:
                        lines.append(f"   {trans['type'].upper()}:")
                        lines.append(f"   {trans['explanation']}")
                        lines.append(f"   Estimated improvement: {trans['estimated_improvement']}")
                        lines.append(f"   Confidence: {trans['confidence']}")
                        lines.append("")

                    if rewrite_result.get("rewritten_code"):
                        lines.append("   REWRITTEN CODE:")
                        for line in rewrite_result["rewritten_code"].split("\n")[:20]:
                            lines.append(f"   {line}")
                        lines.append("")

            except Exception as e:
                logger.warning(f"Code rewriting failed: {e}", exc_info=True)
                lines.append("CODE REWRITING SUGGESTIONS")
                lines.append("=" * 70)
                lines.append(f"⚠️  Code rewriting could not be completed: {str(e)}")
                lines.append("")

        # Visual Context Flow Diagram
        try:
            from core.dax.visual_flow import VisualFlowDiagramGenerator

            flow_gen = VisualFlowDiagramGenerator()
            ascii_diagram = flow_gen.generate_ascii_diagram(analysis, dax_expression)

            lines.append("")
            lines.append("```")
            lines.append(ascii_diagram)
            lines.append("```")
            lines.append("")

        except Exception as e:
            logger.warning(f"Visual flow diagram failed: {e}", exc_info=True)
            lines.append("")
            lines.append("VISUAL CONTEXT FLOW DIAGRAM")
            lines.append("=" * 70)
            lines.append(f"⚠️  Visual flow diagram could not be generated: {str(e)}")
            lines.append("")

        # ===== END NEW ENHANCEMENTS =====

        # ===== FINAL OPTIMIZED MEASURE - THE COMPLETE RESULT =====
        lines.append("")
        lines.append("=" * 70)
        lines.append("🎯 FINAL OPTIMIZED MEASURE - READY TO USE")
        lines.append("=" * 70)
        lines.append("")

        # Check if we have any improvements at all
        if improvements and improvements.get("has_improvements"):
            # Calculate estimated improvement
            high_priority = sum(
                1 for i in improvements.get("improvements", []) if i.get("severity") == "high"
            )
            medium_priority = sum(
                1 for i in improvements.get("improvements", []) if i.get("severity") == "medium"
            )
            transformation_count = improvements.get("improvements_count", 0)

            # Case 1: We have automatically transformed code
            if improvements.get("suggested_code"):
                lines.append("✅ OPTIMIZED DAX CODE (AUTOMATIC TRANSFORMATION):")
                lines.append("")

                # Show the complete optimized code with syntax highlighting
                optimized_code = improvements["suggested_code"]
                lines.append("```dax")
                lines.append(optimized_code)
                lines.append("```")
                lines.append("")

                # Show what was optimized
                lines.append("📋 APPLIED OPTIMIZATIONS:")
                lines.append("")
                lines.append(f"   Total transformations applied: {transformation_count}")
                lines.append("")

                # List all transformations
                if improvements.get("transformation_details"):
                    for idx, detail in enumerate(improvements["transformation_details"], 1):
                        lines.append(f"   {idx}. {detail}")
                    lines.append("")

                # Show key metrics
                lines.append("📊 OPTIMIZATION SUMMARY:")
                lines.append("")

                if high_priority > 0:
                    lines.append(f"   • {high_priority} high-priority optimization(s) applied")
                if medium_priority > 0:
                    lines.append(f"   • {medium_priority} medium-priority optimization(s) applied")

                # Add performance estimate
                estimated_gain = min(high_priority * 20 + medium_priority * 10, 80)
                if estimated_gain > 0:
                    lines.append(f"   • Estimated performance gain: {estimated_gain}%")

                lines.append("")

                # Show comparison with proper DAX formatting
                lines.append("📈 BEFORE vs AFTER COMPARISON:")
                lines.append("")
                lines.append("ORIGINAL CODE:")
                lines.append("")
                lines.append("```dax")
                lines.append(dax_expression.strip())
                lines.append("```")
                lines.append("")

                lines.append("OPTIMIZED CODE:")
                lines.append("")
                lines.append("```dax")
                lines.append(optimized_code.strip())
                lines.append("```")
                lines.append("")

                lines.append("💡 NEXT STEPS:")
                lines.append("")
                lines.append("   1. Copy the optimized code above")
                lines.append("   2. Test it in your Power BI model")
                lines.append("   3. Verify the results match the original")
                lines.append("   4. Use DAX Studio to compare performance")
                lines.append("   5. Update your measure with the optimized version")
                lines.append("")

            # Case 2: We have improvements but no automatic transformation
            else:
                lines.append("⚠️  OPTIMIZATION OPPORTUNITIES DETECTED")
                lines.append("-" * 70)
                lines.append("")
                lines.append(
                    f"Found {transformation_count} improvement(s) that require manual optimization:"
                )
                lines.append("")

                # Show key metrics
                lines.append("📊 IMPROVEMENT BREAKDOWN:")
                lines.append("")
                if high_priority > 0:
                    lines.append(
                        f"   🔴 {high_priority} high-priority issue(s) - SHOULD BE ADDRESSED"
                    )
                if medium_priority > 0:
                    lines.append(
                        f"   🟡 {medium_priority} medium-priority improvement(s) - RECOMMENDED"
                    )

                # Add performance estimate
                estimated_gain = min(high_priority * 20 + medium_priority * 10, 80)
                if estimated_gain > 0:
                    lines.append(f"   📈 Estimated performance gain: up to {estimated_gain}%")

                lines.append("")
                lines.append(
                    "⚠️  IMPORTANT: Review the 'SPECIFIC IMPROVEMENTS & NEW DAX CODE' section above"
                )
                lines.append("    for detailed recommendations and code patterns to apply.")
                lines.append("")

                lines.append("CURRENT CODE:")
                lines.append("")
                lines.append("```dax")
                lines.append(dax_expression.strip())
                lines.append("```")
                lines.append("")

                lines.append("💡 NEXT STEPS:")
                lines.append("")
                lines.append("   1. Review the improvement recommendations above")
                lines.append("   2. Apply the suggested patterns to your DAX code")
                lines.append("   3. Test each change incrementally")
                lines.append("   4. Use DAX Studio to measure performance improvements")
                lines.append("   5. Consider using AI assistance to generate optimized code")
                lines.append("")

        else:
            # No improvements - code is already optimal
            lines.append("✅ YOUR CODE IS ALREADY WELL-OPTIMIZED!")
            lines.append("-" * 70)
            lines.append("")
            lines.append("No significant optimizations were identified.")
            lines.append("Your DAX code follows best practices.")
            lines.append("")
            lines.append("CURRENT CODE:")
            lines.append("")
            lines.append("```dax")
            lines.append(dax_expression.strip())
            lines.append("```")
            lines.append("")
            lines.append("💡 RECOMMENDATIONS:")
            lines.append("")
            lines.append("   • Monitor query performance in DAX Studio")
            lines.append("   • Check VertiPaq Analyzer for column-level optimizations")
            lines.append("   • Review data model relationships and cardinality")
            lines.append("   • Consider adding measures to display folders for organization")
            lines.append("")

        lines.append("=" * 70)
        lines.append("END OF ENHANCED ANALYSIS REPORT")
        lines.append("=" * 70)

        return "\n".join(lines)
