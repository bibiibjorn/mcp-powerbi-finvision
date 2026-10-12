"""
Filter context + DAX query execution helpers for debug handler.

Extracted from debug_handler.py as part of P2-183 split. Holds the three
large helpers shared by handle_debug_visual and _handle_run_dax:

- _build_filter_context — classify slicer/page/report filters, build base response
- _apply_manual_filters — handle skip_auto_filters + manual filter overrides
- _execute_visual_query — run trace + query with retry + anomaly/agg/rel analysis

Module constants (MAX_MEASURES_SHOWN, MAX_QUERY_RESULT_ROWS, VARIABLE_TRUNCATION_CHARS)
come from helpers.py.
"""

import logging
from typing import Any, Dict, List, Optional

from core.infrastructure.connection_state import connection_state
from server.handlers._debug.helpers import (
    MAX_MEASURES_SHOWN,
    MAX_QUERY_RESULT_ROWS,
    VARIABLE_TRUNCATION_CHARS,
    _build_trace_response,
    _compact_filter_context,
    cache_provenance,
    trace_unavailable_message,
)

logger = logging.getLogger(__name__)


def _build_filter_context(
    builder: Any,
    result: Any,
    page_name: str,
    compact: bool,
    include_slicers: bool,
    pbip_freshness: Optional[Dict],
) -> Dict[str, Any]:
    """Classify filters and assemble the base response dict.

    Returns a dict with keys: response, all_filters, data_filters,
    field_param_filters, ui_control_filters,
    slicers_without_selection.
    """
    from core.debug.filter_to_dax import FilterClassification

    # Check for slicers with no selection
    slicers_without_selection = []
    all_slicers = builder.list_slicers(page_name)
    for slicer in all_slicers:
        if not slicer.selected_values:
            slicers_without_selection.append(
                {"field": slicer.field_reference, "table": slicer.table, "column": slicer.column}
            )

    # Classify filters
    all_filters = result.filter_context.all_filters()

    # Reliable detection methods for field parameters
    RELIABLE_FP_METHODS = {"nameof_pattern", "switch_pattern", "system_flags"}

    # Enhance classification with semantic analysis
    if connection_state.is_connected():
        try:
            classifier = builder._init_semantic_classifier()
            if classifier:
                for f in all_filters:
                    if not f.table:
                        continue
                    sc = classifier.classify(f.table, f.column)
                    if sc.classification == "field_parameter":
                        if sc.detection_method in RELIABLE_FP_METHODS and sc.confidence > 0.80:
                            f.classification = FilterClassification.FIELD_PARAMETER
                    elif sc.classification == "ui_control" and sc.confidence > 0.80:
                        f.classification = FilterClassification.UI_CONTROL
        except (AttributeError, ValueError, TypeError) as se:
            logger.debug(f"Semantic classification skipped: {se}")

    data_filters = [
        f for f in all_filters if getattr(f, "classification", "data") == FilterClassification.DATA
    ]
    field_param_filters = [
        f
        for f in all_filters
        if getattr(f, "classification", "data") == FilterClassification.FIELD_PARAMETER
    ]
    ui_control_filters = [
        f
        for f in all_filters
        if getattr(f, "classification", "data") == FilterClassification.UI_CONTROL
    ]
    filters_with_nulls = [f for f in all_filters if getattr(f, "has_null_values", False)]

    fidelity = getattr(result, "fidelity", "full")
    unsupported = list(getattr(result, "unsupported_filters", []) or [])

    # Build base response
    response = {
        "success": True,
        "visual": {
            "id": result.visual_info.visual_id,
            "name": result.visual_info.visual_name,
            "type": result.visual_info.visual_type,
            "page": result.visual_info.page_name,
            "measures": result.visual_info.measures,
            "columns": result.visual_info.columns,
        },
        "filters": _compact_filter_context(result.filter_breakdown, compact),
        "filter_counts": (
            {
                "total": len(all_filters),
                "applied": len(data_filters),
                "excluded": (len(field_param_filters) + len(ui_control_filters)),
            }
            if compact
            else {
                "report": len(result.filter_context.report_filters),
                "page": len(result.filter_context.page_filters),
                "visual": len(result.filter_context.visual_filters),
                "slicer": len(result.filter_context.slicer_filters),
                "total": len(all_filters),
                "data_applied": len(data_filters),
                "field_params_excluded": len(field_param_filters),
                "ui_controls_excluded": len(ui_control_filters),
                "with_nulls": len(filters_with_nulls),
            }
        ),
        "query": result.dax_query,
        "measure": result.measure_name,
        # F-39(3): every silently-dropped filter/role now shows up here.
        "fidelity": fidelity,
    }
    if unsupported:
        response["unsupported_filters"] = unsupported
        response["fidelity_warning"] = (
            f"{len(unsupported)} filter(s) could not be reproduced in DAX; the query does "
            "NOT show exactly what the visual shows. Pass them manually via `filters`."
        )
    exclude_filters = [
        f"{f.table}[{f.column}]" for f in all_filters if getattr(f, "polarity", "") == "exclude"
    ]
    if exclude_filters:
        response["exclude_filters"] = exclude_filters

    # Add PBIP freshness information if applicable
    if pbip_freshness:
        if pbip_freshness.get("stale"):
            response["pbip_warning"] = pbip_freshness
        elif not compact:
            response["pbip_state"] = pbip_freshness

    # Verbose-only fields
    if not compact:
        response["pbip_path"] = connection_state.pbip_path
        response["title"] = result.visual_info.title
        inactive = list(getattr(result.filter_context, "inactive", []) or [])
        if inactive:
            response["inactive_filters"] = inactive
        notes = list(getattr(result, "fidelity_notes", []) or [])
        if notes:
            response["fidelity_notes"] = notes

    if not compact and (field_param_filters or ui_control_filters):
        excluded = []
        for f in field_param_filters:
            excluded.append({"table": f.table, "column": f.column, "type": "field_param"})
        for f in ui_control_filters:
            excluded.append({"table": f.table, "column": f.column, "type": "ui_control"})
        response["excluded_filters"] = excluded

    if not compact and include_slicers and result.filter_context.slicer_filters:
        response["slicer_details"] = [
            {
                "field": f"{sf.table}[{sf.column}]",
                "dax": sf.dax,
                "values": sf.values[:5],
                "polarity": getattr(sf, "polarity", "include"),
            }
            for sf in result.filter_context.slicer_filters
        ]

    if not compact and result.measure_definitions:
        response["measure_definitions"] = [
            {
                "name": m.name,
                "expression": (
                    m.expression[:800] + "... [truncated]"
                    if len(m.expression) > 800
                    else m.expression
                ),
            }
            for m in result.measure_definitions
        ]

    if not compact and result.expanded_query:
        response["expanded_query"] = result.expanded_query

    # Always surface detected format string companion measures so the caller
    # knows they are included as IGNORE() args in the trace query.
    fs_measures = getattr(result, "format_string_measures", [])
    if fs_measures:
        response["format_string_measures"] = fs_measures

    return {
        "response": response,
        "all_filters": all_filters,
        "data_filters": data_filters,
        "field_param_filters": field_param_filters,
        "ui_control_filters": ui_control_filters,
        "slicers_without_selection": slicers_without_selection,
    }


def _manual_filter_columns(manual_filters: List[str]) -> set:
    """``(table, column)`` pairs (lower-cased) referenced by manual DAX filters."""
    from core.dax.dax_reference_parser import scan_references

    cols = set()
    for mf in manual_filters:
        try:
            for ref in scan_references(mf):
                if ref.kind == "qualified" and ref.table and ref.name:
                    cols.add((ref.table.lower(), ref.name.lower()))
        except Exception:  # noqa: BLE001 — a malformed filter just overrides nothing
            continue
    return cols


def _apply_manual_filters(
    args: Dict[str, Any],
    result: Any,
    builder: Any,
    response: Dict[str, Any],
    data_filters: List,
    query_to_execute: str,
) -> str:
    """Handle manual filter overrides and skip_auto_filters logic.

    F-06: every manual predicate is kept VERBATIM and applied as a
    ``KEEPFILTERS(<dax>)`` CALCULATETABLE argument around the visual's core
    query (never re-parsed into a ``TREATAS({BLANK()}, ''[])``). The rebuilt
    query keeps the visual's rollup, row limit, IGNORE() companions and sort
    order (``VisualQueryBuilder.rebuild_query``).

    Mutates response dict in-place. Returns the (possibly updated)
    query_to_execute string.
    """
    manual_filters = [f for f in (args.get("filters") or []) if isinstance(f, str) and f.strip()]
    skip_auto_filters = args.get("skip_auto_filters", False)
    converter = builder.converter
    manual_objs = [converter.manual_filter(f) for f in manual_filters]

    if skip_auto_filters:
        query_to_execute = builder.rebuild_query(result, manual_objs)
        response["generated_query"] = query_to_execute
        response["auto_filters_skipped"] = True
        if manual_filters:
            response["manual_filters_applied"] = manual_filters
        else:
            response["note"] = (
                "Auto-detected filters skipped. Provide "
                "filters parameter for manual DAX filters."
            )

    elif manual_filters:
        manual_filter_columns = _manual_filter_columns(manual_filters)

        # Keep every auto filter (data AND field-parameter, like the visual query)
        # except those on a column a manual filter overrides.
        kept = []
        skipped = []
        for f in result.filter_context.all_filters():
            if (
                f.table
                and f.column
                and (f.table.lower(), f.column.lower()) in manual_filter_columns
            ):
                skipped.append(f)
                continue
            kept.append(f)

        query_to_execute = builder.rebuild_query(result, kept + manual_objs)

        response["generated_query"] = query_to_execute
        response["query_with_manual_filters"] = query_to_execute
        response["manual_filters_applied"] = manual_filters

        if skipped:
            response["auto_filters_overridden"] = [
                {
                    "table": f.table,
                    "column": f.column,
                    "original_dax": f.dax,
                    "reason": ("Overridden by manual filter " "on same column"),
                }
                for f in skipped
            ]

    return query_to_execute


def _execute_visual_query(
    query_to_execute: str,
    response: Dict[str, Any],
    builder: Any,
    result: Any,
    all_filters: List,
    data_filters: List,
    field_param_filters: List,
    compact: bool,
    slicers_without_selection: List,
    execute_query: bool,
    trace: bool = False,
    clear_cache: bool = True,
    op_args: Optional[Dict[str, Any]] = None,
) -> None:
    """Execute DAX query with retry, add results to response.

    Also performs relationship analysis, aggregation matching,
    slicer warnings, and anomaly detection. Mutates response
    in-place.

    When trace=True, runs SE/FE timing analysis via NativeTraceRunner
    before the normal query execution (cold cache first, warm rows second).
    The row query always bypasses the result cache (F-07): a debugger that
    replays a pre-edit result is worse than no debugger.
    """
    from core.dax.dax_names import column_ref

    # Add warning if slicers have no selection
    if slicers_without_selection:
        if compact:
            response["empty_slicers"] = len(slicers_without_selection)
        else:
            response["warnings"] = [
                {
                    "type": "empty_slicers",
                    "count": len(slicers_without_selection),
                    "slicers": slicers_without_selection,
                }
            ]

    # Advanced analysis: relationships and aggregation
    if connection_state.is_connected():
        qe = connection_state.query_executor
        if qe:
            measure_tables = list(
                set(
                    getattr(m, "table", "")
                    for m in (result.measure_definitions or [])
                    if getattr(m, "table", "")
                )
            )
            filter_tables = list(set(f.table for f in all_filters if f.table))
            grouping_tables = list(
                set(
                    col.split("[")[0].strip("'\"")
                    for col in (result.visual_info.columns or [])
                    if "[" in col
                )
            )

            # Relationship analysis
            try:
                resolver = builder._init_relationship_resolver()
                if resolver:
                    hints = resolver.analyze_query_tables(
                        measure_tables, filter_tables, grouping_tables
                    )
                    if hints:
                        response["relationship_hints"] = [
                            {
                                "type": h.type,
                                "tables": (f"{h.from_table} -> " f"{h.to_table}"),
                                "suggestion": (h.dax_modifier if h.dax_modifier else h.reason),
                                "severity": h.severity,
                            }
                            for h in hints[:MAX_MEASURES_SHOWN]
                        ]
            except (AttributeError, ValueError, TypeError) as re_err:
                logger.debug(f"Relationship analysis skipped: {re_err}")

            # Aggregation matching
            try:
                agg_matcher = builder._init_aggregation_matcher()
                if agg_matcher:
                    grp_cols = result.visual_info.columns or []
                    flt_cols = [
                        column_ref(f.table, f.column) for f in data_filters if f.table and f.column
                    ]
                    agg_match = agg_matcher.find_matching_aggregation(grp_cols, flt_cols)
                    if agg_match:
                        response["aggregation_info"] = {
                            "available": True,
                            "table": agg_match.agg_table,
                            "confidence": (agg_match.match_confidence),
                            "recommendation": (agg_match.recommendation),
                        }
            except (AttributeError, ValueError, TypeError) as ae:
                logger.debug(f"Aggregation analysis skipped: {ae}")

    # SE/FE trace — runs first (cold cache) so timing is accurate.
    # If execute_query is also True it runs after on warm cache (fast row fetch).
    conn_status = {"connected": False}
    if trace:
        conn_status = connection_state.verify_connection(auto_reconnect=True)
        if conn_status["connected"]:
            try:
                from core.infrastructure.query_trace import NativeTraceRunner

                conn_str = connection_state.connection_manager.connection_string
                if not conn_str:
                    response["se_fe_trace"] = {"error": "No connection string available"}
                elif not NativeTraceRunner.is_available():
                    response["se_fe_trace"] = {"error": trace_unavailable_message()}
                else:
                    runner = NativeTraceRunner(conn_str)
                    trace_result = runner.execute_with_trace(query_to_execute, clear_cache)
                    if "_error" in trace_result:
                        response["se_fe_trace"] = {
                            "error": trace_result["_error"],
                            "error_type": trace_result.get("_error_type"),
                        }
                    else:
                        trace_response = _build_trace_response(trace_result)
                        # L-20: the stored trace knows WHICH query/measures it timed,
                        # so optimize can refuse to diagnose a different measure.
                        trace_response["query"] = query_to_execute
                        trace_response["measure_names"] = [
                            m.strip("[]") for m in (result.visual_info.measures or [])
                        ] or [str(result.measure_name).strip("[]")]
                        trace_response["source"] = (
                            f"09_Debug_Operations:visual {result.visual_info.page_name}/"
                            f"{result.visual_info.visual_id}"
                        )
                        trace_id = connection_state.store_trace_result(trace_response)
                        # Strip internal keys before sending to user
                        response["se_fe_trace"] = {
                            k: v
                            for k, v in trace_response.items()
                            if not k.startswith("_") and k not in ("query",)
                        }
                        if trace_id:
                            response["se_fe_trace"]["trace_id"] = trace_id
            except Exception as te:
                logger.error(f"SE/FE trace failed: {te}", exc_info=True)
                response["se_fe_trace"] = {"error": str(te)}
        else:
            error_msg = "not_connected"
            if conn_status.get("error"):
                error_msg += f" ({conn_status['error']})"
            response["se_fe_trace"] = {"error": error_msg}

    # Execute query if requested and connected
    if execute_query:
        if not conn_status.get("connected"):
            conn_status = connection_state.verify_connection(auto_reconnect=True)
    if execute_query and conn_status.get("connected"):
        try:
            from core.infrastructure.impersonated_executor import get_executor_for_args

            qe = get_executor_for_args(op_args or {})
            if qe:
                exec_result = qe.validate_and_execute_dax(
                    query_to_execute, top_n=MAX_QUERY_RESULT_ROWS, bypass_cache=True
                )
                if exec_result.get("impersonated"):
                    response["impersonated"] = exec_result["impersonated"]

                # Smart retry on composite key errors
                if not exec_result.get("success"):
                    error_msg = (exec_result.get("error") or "").lower()
                    retry_patterns = [
                        "composite",
                        "multiple columns",
                        "ambiguous",
                        "cannot determine",
                    ]
                    if any(p in error_msg for p in retry_patterns) and field_param_filters:
                        logger.info(
                            "Composite key error, retrying "
                            "without "
                            f"{len(field_param_filters)}"
                            " field param filters"
                        )
                        fp_ids = {id(f) for f in field_param_filters}
                        reduced = [f for f in all_filters if id(f) not in fp_ids]
                        reduced_query = builder.rebuild_query(result, reduced)
                        retry_result = qe.validate_and_execute_dax(
                            reduced_query, top_n=MAX_QUERY_RESULT_ROWS, bypass_cache=True
                        )
                        if retry_result.get("success"):
                            exec_result = retry_result
                            response["retry_info"] = {
                                "retried": True,
                                "original_error": (error_msg[:VARIABLE_TRUNCATION_CHARS]),
                                "excluded": [
                                    column_ref(f.table, f.column)
                                    for f in field_param_filters
                                    if f.table and f.column
                                ],
                                "retry_query": reduced_query,
                                "note": (
                                    "Results may differ from "
                                    "visual due to excluded "
                                    "field parameters"
                                ),
                            }
                            response["fidelity"] = "partial"

                if exec_result.get("success"):
                    rows = exec_result.get("rows", [])
                    response["result"] = {
                        "rows": rows,
                        "count": len(rows),
                        "ms": exec_result.get("execution_time_ms"),
                        "cache": cache_provenance(exec_result),
                    }
                    if exec_result.get("truncated"):
                        response["result"]["truncated"] = True
                    if not rows:
                        response["result"]["note"] = "no_rows"
                    elif all(all(v is None for v in row.values()) for row in rows):
                        response["result"]["note"] = "all_null"

                    # Anomaly detection
                    if rows and len(rows) > 1:
                        try:
                            from core.debug.anomaly_detector import (
                                analyze_results,
                            )

                            report = analyze_results(rows)
                            if report:
                                response["anomalies"] = report
                        except (ImportError, KeyError, ValueError, TypeError) as ae:
                            logger.debug("Anomaly detection skipped: " f"{ae}")
                else:
                    response["result"] = {
                        "error": exec_result.get("error"),
                        "error_type": exec_result.get("error_type"),
                    }
        except Exception as e:
            response["result"] = {"error": str(e)}
    elif execute_query:
        error_msg = "not_connected"
        if conn_status.get("error"):
            error_msg += f" ({conn_status['error']})"
        response["result"] = {"error": error_msg}

    # Surface connection loss prominently at top level
    if (trace or execute_query) and not connection_state.is_connected():
        response["connection_warning"] = (
            "Connection to Power BI model was lost. "
            "Trace and execution results may be missing. "
            "Use 01_Connection operation=ensure to reconnect."
        )
        if trace and response.get("se_fe_trace", {}).get("error"):
            response["success"] = False
