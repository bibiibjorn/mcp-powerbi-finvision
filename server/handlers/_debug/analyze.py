"""
analyze operation — measure-centric analysis.

Extracted from debug_handler.py as part of P2-183 split.

Public entry points (re-exported via debug_handler.py):

- _handle_analyze  — primary dispatcher for operation=analyze
- _resolve_measure_expression, _analyze_measure_dax,
  _get_visual_filter_context_for_measure, _execute_measure_with_context,
  _build_analyze_response — helpers (kept for test access)
"""

import logging
from typing import Any, Dict, List, Optional

from core.validation.error_handler import ErrorHandler
from server.handlers._debug.helpers import (
    VARIABLE_TRUNCATION_CHARS,
    MAX_MEASURES_SHOWN,
    _get_visual_query_builder,
    cache_provenance,
)
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)


def _resolve_measure_expression(
    measure_name: str, table_name: Optional[str], qe: Any
) -> Dict[str, Any]:
    """Find measure expression from model via DMV, TMDL, or fallback.

    Returns dict with keys: success, measure_details, expression,
    expression_source. On failure, returns success=False with error.
    """
    clean_name = measure_name.strip("[]")
    measure_details: Dict[str, Any] = {"success": False}
    expression_source = None

    # Try 1: DMV (most reliable for live model)
    info_result = qe.execute_info_query("MEASURES")
    if info_result.get("success") and info_result.get("rows"):
        for row in info_result["rows"]:
            name = row.get("Name", row.get("[Name]", ""))
            if name.lower() == clean_name.lower():
                expression = row.get("Expression", row.get("[Expression]", ""))
                table_id = row.get("TableID", row.get("[TableID]"))
                fmt = row.get("FormatString", row.get("[FormatString]", ""))

                found_table = table_name
                if table_id and not found_table:
                    tables_result = qe.execute_info_query("TABLES")
                    if tables_result.get("success") and tables_result.get("rows"):
                        for trow in tables_result["rows"]:
                            tid = trow.get("ID", trow.get("[ID]", ""))
                            if str(tid) == str(table_id):
                                found_table = trow.get("Name", trow.get("[Name]", ""))
                                break

                measure_details = {
                    "success": True,
                    "measure_name": name,
                    "expression": expression,
                    "table_name": found_table,
                    "table_id": table_id,
                    "format_string": fmt,
                }
                expression_source = "DMV"
                break

    # Try 2: TMDL files
    if not measure_details.get("success") or not measure_details.get("expression"):
        builder, error = _get_visual_query_builder()
        if not error and builder:
            builder.load_column_types(qe)
            tmdl_result = builder.get_measure_expression(clean_name)
            if tmdl_result and tmdl_result.expression:
                measure_details = {
                    "success": True,
                    "measure_name": tmdl_result.name,
                    "expression": tmdl_result.expression,
                    "table_name": (tmdl_result.table or table_name),
                    "format_string": tmdl_result.format_string,
                }
                expression_source = "TMDL"

    # Try 3: QueryExecutor fallback
    if not measure_details.get("success"):
        measure_details = qe.get_measure_details_with_fallback(table_name, measure_name)
        if measure_details.get("success"):
            expression_source = "QueryExecutor fallback"

    if not measure_details.get("success"):
        return error_envelope(
            "not_found",
            f"Could not find measure '{measure_name}'. " "Specify table_name to narrow the search.",
            hint="Use measure_operations with operation=list " "to see available measures",
        )

    expression = measure_details.get("expression", measure_details.get("Expression", ""))
    if not expression:
        return error_envelope(
            "not_found",
            f"Measure '{measure_name}' found but has no "
            "expression (may be a calculated column or "
            "external measure)",
        )

    return {
        "success": True,
        "measure_details": measure_details,
        "expression": expression,
        "expression_source": expression_source,
    }


def _analyze_measure_dax(
    expression: str,
) -> Dict[str, Any]:
    """Run DAX best practices analysis on an expression.

    Returns the analysis result dict.
    """
    from core.dax.analysis_pipeline import run_best_practices

    result = run_best_practices(expression)
    if result is None:
        return error_envelope(
            "unavailable",
            "Best practices analysis unavailable",
            issues=[],
            total_issues=0,
        )
    return result


def _get_visual_filter_context_for_measure(
    page_name: str,
    visual_id: Optional[str],
    visual_name: Optional[str],
    include_slicers: bool,
    qe: Any,
) -> tuple:
    """Get filter context from a visual/page for measure analysis.

    Returns (filter_context_info, filter_dax_parts) tuple.
    filter_context_info is None if no visual context available.
    """
    filter_context_info = None
    filter_dax_parts: List[str] = []

    if not page_name or not (visual_id or visual_name):
        return filter_context_info, filter_dax_parts

    builder, error = _get_visual_query_builder()
    if error or not builder:
        return filter_context_info, filter_dax_parts

    from core.debug.filter_to_dax import calculate_filter_args

    if qe is not None:
        builder.load_column_types(qe)
    visual_info, filter_context = builder.get_visual_filter_context(
        page_name, visual_id, visual_name, include_slicers
    )
    # F-27: DATA filters only, as valid CALCULATE arguments. Field-parameter and
    # UI-control filters (which the visual query also leaves out of the data)
    # and the old "-- composite TREATAS" placeholder comment -- which swallowed
    # the closing parens -- never reach the CALCULATE.
    filter_dax_parts, not_applied = calculate_filter_args(filter_context.all_filters())
    not_applied = list(not_applied) + list(filter_context.unsupported)
    filter_context_info = {
        "visual": {
            "id": (visual_info.visual_id if visual_info else None),
            "name": (visual_info.visual_name if visual_info else None),
            "page": page_name,
        },
        "filters_applied": len(filter_dax_parts),
        "filter_summary": {
            "report_filters": len(filter_context.report_filters),
            "page_filters": len(filter_context.page_filters),
            "visual_filters": len(filter_context.visual_filters),
            "slicer_filters": len(filter_context.slicer_filters),
        },
        "filter_dax": filter_dax_parts,
        "fidelity": "partial" if not_applied else "full",
    }
    if not_applied:
        filter_context_info["not_applied"] = not_applied

    return filter_context_info, filter_dax_parts


def _execute_measure_with_context(
    measure_name: str, filter_dax_parts: List[str], qe: Any
) -> Optional[Dict[str, Any]]:
    """Execute a measure with the given filter context.

    Returns execution result dict, or None if not executed.
    """
    from core.dax.dax_names import bracket, unbracket

    measure_ref = bracket(unbracket(measure_name))
    filter_clause = ", ".join(filter_dax_parts) if filter_dax_parts else ""

    if filter_clause:
        query = f'EVALUATE ROW("Value", ' f"CALCULATE({measure_ref}, {filter_clause}))"
    else:
        query = f'EVALUATE ROW("Value", {measure_ref})'

    try:
        exec_result = qe.validate_and_execute_dax(query, top_n=10, bypass_cache=True)
        if exec_result.get("success") and exec_result.get("rows"):
            row = exec_result["rows"][0]
            value = row.get("Value", row.get("[Value]"))
            return {
                "success": True,
                "value": value,
                "execution_time_ms": exec_result.get("execution_time_ms"),
                "query": query,
                "cache": cache_provenance(exec_result),
            }
        else:
            return error_envelope("operation_failed", exec_result.get("error"), query=query)
    except Exception as e:
        return error_envelope("operation_failed", str(e), query=query)


def _build_analyze_response(
    measure_name: str,
    table_name: Optional[str],
    expression: str,
    measure_details: Dict[str, Any],
    expression_source: Optional[str],
    analysis_result: Dict[str, Any],
    fix_suggestions: List[Dict],
    execution_result: Optional[Dict[str, Any]],
    filter_context_info: Optional[Dict[str, Any]],
    compact: bool,
) -> Dict[str, Any]:
    """Build the compact or verbose response for analyze_measure."""
    expr_limit = 3 * VARIABLE_TRUNCATION_CHARS
    if compact:
        response: Dict[str, Any] = {
            "success": True,
            "measure": measure_name,
            "expression": (
                expression[:expr_limit] + "..." if len(expression) > expr_limit else expression
            ),
            "issues": analysis_result.get("total_issues", 0),
            "score": analysis_result.get("overall_score"),
            "fixes": [
                {"issue": s["issue"], "severity": s["severity"]}
                for s in fix_suggestions[:MAX_MEASURES_SHOWN]
            ],
        }
        if execution_result and execution_result.get("success"):
            response["value"] = execution_result.get("value")
            response["ms"] = execution_result.get("execution_time_ms")
        elif execution_result:
            response["exec_error"] = execution_result.get("error")
    else:
        response = {
            "success": True,
            "measure": {
                "name": measure_name,
                "table": table_name or measure_details.get("table_name", "Unknown"),
                "expression": expression,
                "format_string": measure_details.get("format_string"),
                "source": expression_source,
            },
            "analysis": {
                "total_issues": analysis_result.get("total_issues", 0),
                "critical": analysis_result.get("critical_issues", 0),
                "high": analysis_result.get("high_issues", 0),
                "score": analysis_result.get("overall_score"),
                "complexity": analysis_result.get("complexity_level"),
                "summary": analysis_result.get("summary"),
            },
            "fix_suggestions": fix_suggestions,
        }
        if filter_context_info:
            response["filter_context"] = filter_context_info
        if execution_result:
            response["execution"] = execution_result

    return response


def _handle_analyze(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Analyze a measure's DAX and suggest fixes/optimizations.

    Accepts either `expression` (raw DAX) or `measure_name` (resolved live).
    Analyzes for anti-patterns and optionally evaluates with filter context
    from a visual.
    """
    try:
        table_name = args.get("table_name")
        measure_name = args.get("measure_name")
        expression = args.get("expression")
        compact = args.get("compact", True)

        if not expression and not measure_name:
            return error_envelope(
                "missing_argument",
                "Either 'expression' or 'measure_name' is required",
                hint='Example: {"operation": "analyze", "measure_name": "Total Sales"}',
            )

        measure_details: Dict[str, Any] = {}
        expression_source = "explicit"
        qe = None

        # Resolve measure_name -> DAX expression when expression is absent.
        # Look `connection_state` and `_resolve_measure_expression` up through
        # the parent debug_handler module so that unittest.mock patches targeting
        # `server.handlers.debug_handler.<name>` take effect here.
        from server.handlers import debug_handler as _dh

        if not expression and measure_name:
            if not _dh.connection_state.is_connected():
                return error_envelope(
                    "not_connected",
                    "measure_name given but not connected to PBI Desktop",
                    hint="Call 01_Connection operation=connect first, "
                    "or pass expression directly.",
                )
            qe = _dh.connection_state.query_executor
            if not qe:
                return ErrorHandler.handle_manager_unavailable("query_executor")
            resolved = _dh._resolve_measure_expression(measure_name, table_name, qe)
            if not resolved.get("success"):
                return resolved
            expression = resolved["expression"]
            if not expression:
                return error_envelope(
                    "invalid_argument",
                    f"Measure {measure_name!r} resolved to an empty expression",
                    hint="Check the measure is defined. Pass the expression explicitly if the live lookup is failing.",
                )
            measure_details = resolved.get("measure_details", {}) or {}
            expression_source = resolved.get("expression_source", "live")
        elif _dh.connection_state.is_connected():
            qe = _dh.connection_state.query_executor

        # Step 2: Analyze the DAX expression
        analysis_result = _analyze_measure_dax(expression)

        # Step 3: Get filter context from visual if specified
        filter_context_info, filter_dax_parts = _get_visual_filter_context_for_measure(
            page_name=args.get("page_name"),
            visual_id=args.get("visual_id"),
            visual_name=args.get("visual_name"),
            include_slicers=args.get("include_slicers", True),
            qe=qe,
        )

        # Step 4: Execute measure with filter context.
        # Audit T98: skip when measure_name is None (expression-only path).
        # Previously _execute_measure_with_context did .strip("[]") on None
        # and the AttributeError got swallowed by the outer except.
        execution_result = None
        if args.get("execute_measure", True) and measure_name:
            execution_result = _execute_measure_with_context(measure_name, filter_dax_parts, qe)

        # Step 5: Generate fix suggestions from analysis issues
        fix_suggestions = []
        for issue in (analysis_result.get("issues") or [])[:5]:
            suggestion: Dict[str, Any] = {
                "issue": issue.get("title"),
                "severity": issue.get("severity"),
                "description": issue.get("description"),
                "category": issue.get("category"),
            }
            if issue.get("code_example_before") and issue.get("code_example_after"):
                suggestion["example_fix"] = {
                    "before": issue.get("code_example_before"),
                    "after": issue.get("code_example_after"),
                }
            if issue.get("estimated_improvement"):
                suggestion["estimated_improvement"] = issue.get("estimated_improvement")
            fix_suggestions.append(suggestion)

        # Step 6: Build response
        return _build_analyze_response(
            measure_name,
            table_name,
            expression,
            measure_details,
            expression_source,
            analysis_result,
            fix_suggestions,
            execution_result,
            filter_context_info,
            compact,
        )

    except Exception as e:
        logger.error(f"Error in analyze_measure: {e}", exc_info=True)
        return ErrorHandler.handle_unexpected_error("analyze_measure", e)
