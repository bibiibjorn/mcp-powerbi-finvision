"""
Shared DAX Analysis Pipeline Helpers

Common analysis steps used by both dax_context_handler (05_DAX_Intelligence)
and debug_handler (09_Debug_Operations.optimize). Eliminates duplication of
try/except + instantiation patterns for context, VertiPaq, and best practices.
"""

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def run_context_analysis(expression: str) -> Tuple[Optional[Any], Optional[Dict[str, Any]]]:
    """Run context transition analysis on a DAX expression.

    Returns (raw_result, dict_form). raw_result has .transitions, .complexity_score etc.
    Returns (None, None) on failure.
    """
    try:
        from core.dax.context_analyzer import DaxContextAnalyzer

        result = DaxContextAnalyzer().analyze_context_transitions(expression)
        dict_form = {
            "complexity_score": result.complexity_score,
            "max_nesting_level": result.max_nesting_level,
            "transition_count": len(result.transitions),
            "transitions": [
                {
                    "type": t.type.value,
                    "location": t.location,
                    "function": t.function,
                    "nesting": t.nested_level,
                }
                for t in result.transitions[:10]
            ],
            "warnings": [{"severity": w.severity, "message": w.message} for w in result.warnings],
        }
        return result, dict_form
    except Exception as e:
        logger.warning(f"Context analysis failed: {e}")
        return None, None


def run_vertipaq_analysis(expression: str, conn_state: Any) -> Optional[Dict[str, Any]]:
    """Run VertiPaq column analysis on a DAX expression.

    Returns analysis dict or None on failure.
    """
    try:
        from core.dax.vertipaq_analyzer import VertiPaqAnalyzer

        result = VertiPaqAnalyzer(conn_state).analyze_dax_columns(expression)
        if not result.get("success"):
            logger.warning(f"VertiPaq analysis failed: {result.get('error', 'Unknown error')}")
        return result
    except Exception as e:
        logger.warning(f"VertiPaq analysis not available: {e}")
        return None


def run_best_practices(
    expression: str,
    context_analysis: Optional[Dict[str, Any]] = None,
    vertipaq_analysis: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Run DAX best practices analysis.

    Returns analysis dict or None on failure.
    """
    try:
        from core.dax.dax_best_practices import DaxBestPracticesAnalyzer

        result = DaxBestPracticesAnalyzer().analyze(
            dax_expression=expression,
            context_analysis=context_analysis,
            vertipaq_analysis=vertipaq_analysis,
        )
        logger.info(f"Best practices analysis: {result.get('total_issues', 0)} issues found")
        return result
    except Exception as e:
        logger.warning(f"Best practices analysis not available: {e}")
        return None


def run_call_tree(expression: str, conn_state: Any = None) -> Optional[Dict[str, Any]]:
    """Build and visualize a DAX call tree.

    Returns dict with visualization, total_iterations, performance_warning, or None on failure.
    """
    try:
        from core.dax.call_tree_builder import CallTreeBuilder

        builder = CallTreeBuilder()

        if conn_state:
            try:
                from core.dax.vertipaq_analyzer import VertiPaqAnalyzer

                builder.vertipaq_analyzer = VertiPaqAnalyzer(conn_state)
            except Exception:
                pass

        call_tree = builder.build_call_tree(expression)
        tree_viz = builder.visualize_tree(call_tree)

        def count_iterations(node):
            total = node.estimated_iterations or 0
            for child in node.children:
                total += count_iterations(child)
            return total

        total_iterations = count_iterations(call_tree)

        return {
            "visualization": tree_viz,
            "total_iterations": total_iterations,
            "performance_warning": (
                "CRITICAL: Over 1 million iterations - severe performance impact!"
                if total_iterations >= 1_000_000
                else (
                    "WARNING: Over 100,000 iterations - consider optimization"
                    if total_iterations >= 100_000
                    else None
                )
            ),
            "formatting_note": "Display this visualization in a ```text code block to preserve the tree structure and box-drawing characters. The visualization includes its own integrated legend.",
        }
    except Exception as e:
        logger.warning(f"Call tree analysis failed: {e}")
        return {"error": f"Call tree could not be generated: {str(e)}"}


def run_optimization_pipeline(
    expression: str,
    connection_state: Any = None,
    dry_run: bool = True,
) -> Optional[Dict[str, Any]]:
    """Run the full DAX optimization pipeline.

    Returns optimization result dict or None on failure.
    """
    try:
        from core.dax.optimizer.pipeline import OptimizationPipeline

        pipeline = OptimizationPipeline(connection_state=connection_state)
        result = pipeline.optimize_expression(expression)
        return {
            "success": result.success,
            "original_dax": result.original_dax,
            "final_dax": result.final_dax,
            "has_changes": result.final_dax is not None,
            "rewrite_count": len(result.rewrites),
            "analysis_score": result.analysis.health_score if result.analysis else None,
            "analysis_issues": result.analysis.total_issues if result.analysis else 0,
            "improvement_summary": result.improvement_summary,
        }
    except Exception as e:
        logger.warning(f"Optimization pipeline failed: {e}")
        return None
