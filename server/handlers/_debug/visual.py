"""
Visual + compare + drill + set_pbip_path + status handlers.

Extracted from debug_handler.py as part of P2-183 split.

Public entry points (re-exported via debug_handler.py):

- handle_debug_visual
- handle_compare_measures
- handle_drill_to_detail
- handle_set_pbip_path
- handle_get_debug_status
- _get_recommendations (internal, status helper)
"""

import logging
from typing import Any, Dict, List, Optional

from core.infrastructure.connection_state import connection_state
from core.validation.error_handler import ErrorHandler
from server.progress import emit_progress
from server.handlers._debug.context import (
    _apply_manual_filters,
    _build_filter_context,
    _execute_visual_query,
)
from server.handlers._debug.helpers import (
    DRILL_DETAIL_LIMIT_MAX,
    DRILL_DETAIL_LIMIT_MIN,
    MAX_QUERY_RESULT_ROWS,
    _check_pbip_freshness,
    _compact_visual_list,
    _discover_visuals,
    _get_visual_query_builder,
    cache_provenance,
)
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)


def handle_debug_visual(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Debug a visual: filter context + query execution.

    Combines PBIP analysis (for filter context) with live model
    (for execution). Self-sufficient: lists pages/visuals when
    not found.
    """
    try:
        compact = args.get("compact", True)
        execute_query = args.get("execute_query", True)
        include_slicers = args.get("include_slicers", True)
        trace = args.get("trace", False)
        clear_cache = args.get("clear_cache", True)

        # Get builder — needed for all operations
        emit_progress(1, 5, "Discovering visual...")
        builder, error = _get_visual_query_builder()
        if error:
            return error_envelope("operation_failed", error)

        # Step 1: Discovery — return early if page/visual missing
        discovery = _discover_visuals(args, builder, compact)
        if discovery is not None:
            return discovery

        # Load column types for accurate type detection
        if connection_state.is_connected():
            qe = connection_state.query_executor
            if qe:
                types_loaded = builder.load_column_types(qe)
                if types_loaded > 0:
                    logger.debug(f"Loaded {types_loaded} column types")

        # Build visual query
        page_name = args.get("page_name")
        result = builder.build_visual_query(
            page_name=page_name,
            visual_id=args.get("visual_id"),
            visual_name=args.get("visual_name"),
            measure_name=args.get("measure_name"),
            include_slicers=include_slicers,
            measures=args.get("measures"),
        )

        if not result:
            visuals = builder.list_visuals(page_name)
            non_slicer = [v for v in visuals if not v.get("is_slicer")]
            return error_envelope(
                "not_found",
                "Visual not found: "
                f"id='{args.get('visual_id')}', "
                f"name='{args.get('visual_name')}'",
                visuals=_compact_visual_list(non_slicer, compact),
            )

        # Step 2: Build filter context and base response
        emit_progress(2, 5, "Building filters...")
        pbip_freshness = _check_pbip_freshness(connection_state.get_pbip_folder_path())
        ctx = _build_filter_context(
            builder, result, page_name, compact, include_slicers, pbip_freshness
        )
        response = ctx["response"]

        # Step 3: Apply manual filter overrides
        emit_progress(3, 5, "Applying filter overrides...")
        query_to_execute = _apply_manual_filters(
            args, result, builder, response, ctx["data_filters"], result.dax_query
        )

        # F-39(1)(2): Desktop's own query (Performance Analyzer) as ground truth.
        query_to_execute = _apply_reference_query(
            args, result, response, query_to_execute, builder=builder, page_name=page_name
        )

        # Step 4: Execute query + advanced analysis
        emit_progress(4, 5, "Executing DAX query...")
        _execute_visual_query(
            query_to_execute,
            response,
            builder,
            result,
            ctx["all_filters"],
            ctx["data_filters"],
            ctx["field_param_filters"],
            compact,
            ctx["slicers_without_selection"],
            execute_query,
            trace=trace,
            clear_cache=clear_cache,
            op_args=args,
        )

        # Hint Claude toward the optimization op with pre-filled args
        # when the trace shows a formula-engine-bound bottleneck.
        measure_name = args.get("measure_name")
        table_name = args.get("table_name")
        trace_block = response.get("se_fe_trace") or {}
        perf_block = trace_block.get("performance") or {}
        fe_pct = perf_block.get("fe_pct")
        if fe_pct is not None and isinstance(fe_pct, (int, float)) and fe_pct > 50 and measure_name:
            response["next_action"] = {
                "tool": "05_DAX_Intelligence",
                "operation": "optimize",
                "args": {
                    "measure_name": measure_name,
                    "table_name": table_name,
                },
                "why": (
                    f"fe_pct={fe_pct:.1f}% — formula engine bound, " "optimization recommended"
                ),
            }

        emit_progress(5, 5, "Complete")
        return response

    except Exception as e:
        logger.error(f"Error in debug_visual: {e}", exc_info=True)
        return ErrorHandler.handle_unexpected_error("debug_visual", e)


def _reference_from_trace(
    args: Dict[str, Any],
    result: Any,
    builder: Any,
    page_name: Optional[str],
    response: Dict[str, Any],
) -> Optional[str]:
    """F-02: the live trace's best-matching captured query for this visual (``from_trace=true``).

    Reads the running ``13_Query_Plan trace_start`` buffer (the trace keeps running); records what was
    used under ``trace_capture``. ``None`` (with ``reconstruction_check.error``) when nothing matches
    well enough: a guess is never substituted for the real query.
    """
    from core.debug.trace_visual_match import capture_for_visual
    from core.infrastructure import live_trace

    visual_id = result.visual_info.visual_id
    fetched = live_trace.fetch(keep_text=True)
    if fetched.get("success") is False:
        response["reconstruction_check"] = {
            "error": f"from_trace needs a running live trace: {fetched.get('error')} "
            "(13_Query_Plan trace_start, interact with the report, then retry)"
        }
        return None
    page = page_name or getattr(result.visual_info, "page_name", None) or ""
    found = capture_for_visual(fetched, builder, page_name=page, visual_id=visual_id)
    for q in fetched.get("queries") or []:
        q.pop("_text", None)
    if not found["found"]:
        response["reconstruction_check"] = {
            "error": f"from_trace: {found['reason']}",
            "candidates": found["candidates"],
        }
        return None
    response["trace_capture"] = {
        k: found[k] for k in ("seq", "request_id", "total_ms", "match", "captures_for_visual")
    }
    return found["query"]


def _apply_reference_query(
    args: Dict[str, Any],
    result: Any,
    response: Dict[str, Any],
    query_to_execute: str,
    builder: Any = None,
    page_name: Optional[str] = None,
) -> str:
    """Diff the reconstruction against Desktop's query and (by default) run Desktop's.

    ``reference_query`` is the DAX copied from Performance Analyzer ("Copy
    query"); ``pa_export_path`` is a PowerBIPerformanceData.json export, from
    which the visual's QueryText is read by visual id. The reconstruction check
    lists every divergence (group-by, filter columns, TREATAS values, NOTs).
    With ``use_reference_query`` (default true, ignored when manual ``filters``
    are given) the EXACT Desktop query is executed instead of the reconstruction.
    """
    reference = args.get("reference_query")
    from_trace = False
    if not reference and args.get("from_trace") in (True, "true", "True", 1):
        reference = _reference_from_trace(args, result, builder, page_name, response)
        if not reference:
            return query_to_execute
        from_trace = True
    if not reference and args.get("pa_export_path"):
        from core.debug.query_shape_diff import reference_query_from_export

        reference = reference_query_from_export(
            str(args["pa_export_path"]), result.visual_info.visual_id
        )
        if not reference:
            response["reconstruction_check"] = {
                "error": (
                    f"visual {result.visual_info.visual_id!r} has no QueryText in "
                    f"{args['pa_export_path']}"
                )
            }
            return query_to_execute
    if not reference:
        return query_to_execute
    from core.debug.query_shape_diff import diff_queries

    check = diff_queries(result.dax_query, reference)
    response["reconstruction_check"] = check
    if not check.get("matches"):
        response["fidelity"] = "partial"
    if args.get("use_reference_query", True) and not args.get("filters"):
        response["executed_query_source"] = "reference"
        response["reference_query"] = reference
        if from_trace:
            # The executed query IS what Desktop sent (captured from the live trace): the
            # reconstruction's divergences stay in reconstruction_check, but the run is faithful.
            response["fidelity"] = "captured"
            response["executed_query_source"] = "trace"
        return reference
    response["executed_query_source"] = "reconstruction"
    return query_to_execute


def _assemble_compare_filters(
    args: Dict[str, Any], qe, skipped: Optional[List[Dict[str, Any]]] = None
) -> List[str]:
    """Collect the DAX filter arguments for a compare run.

    Pulls the visual's DATA filters (when page + visual identify one) as valid
    CALCULATE arguments (exclude lists stay exclude lists, F-02) and appends any
    manual `filters`. Measure (value) filters cannot be CALCULATE arguments;
    they are appended to *skipped*. Extracted so the scalar query and the
    table-grain equivalence query share ONE filter-context assembly.
    """
    from core.debug.filter_to_dax import calculate_filter_args

    page_name = args.get("page_name")
    visual_id = args.get("visual_id")
    visual_name = args.get("visual_name")
    include_slicers = args.get("include_slicers", True)

    filter_dax_parts: List[str] = []

    if page_name and (visual_id or visual_name):
        # Get filter context from visual
        builder, error = _get_visual_query_builder()
        if not error and builder:
            # Load column types for accurate filter generation (shared executor)
            builder.load_column_types(connection_state.query_executor or qe)
            visual_info, filter_context = builder.get_visual_filter_context(
                page_name, visual_id, visual_name, include_slicers
            )
            filter_dax_parts, not_applied = calculate_filter_args(filter_context.all_filters())
            if skipped is not None:
                skipped.extend(not_applied)
                skipped.extend(filter_context.unsupported)

    # Add manual filters
    filter_dax_parts.extend(args.get("filters", []) or [])
    return filter_dax_parts


def _timed_runs(
    qe,
    queries: Dict[str, str],
    *,
    runs: int,
    warmup: bool,
    trace: bool,
) -> Dict[str, Dict[str, Any]]:
    """Run each query ``runs`` times INTERLEAVED (A B, B A, A B ...) - F-22.

    The first query no longer pays the cold storage-engine cost alone while the
    second reuses its datacaches: an optional warm-up runs both once, the order
    alternates every round so neither side is always first, and every run
    bypasses the result cache. With ``trace`` each run is a cold (ClearCache)
    traced run whose engine total is reported instead of the client wall time.
    Returns ``{name: {"result": first successful result, "times": [...],
    "errors": [...], "cache": provenance}}``.
    """
    from server.request_context import raise_if_cancelled

    out: Dict[str, Dict[str, Any]] = {
        name: {"result": None, "times": [], "errors": [], "cache": None} for name in queries
    }
    runner = None
    if trace:
        from core.infrastructure.query_trace import NativeTraceRunner

        conn_str = getattr(connection_state.connection_manager, "connection_string", None)
        if conn_str and NativeTraceRunner.is_available():
            runner = NativeTraceRunner(conn_str)

    names = list(queries)

    def _one(name: str, record: bool) -> None:
        query = queries[name]
        res = qe.validate_and_execute_dax(query, top_n=10, bypass_cache=True)
        slot = out[name]
        if slot["result"] is None or (res.get("success") and not slot["result"].get("success")):
            slot["result"] = res
            slot["cache"] = cache_provenance(res)
        if not record:
            return
        if not res.get("success"):
            slot["errors"].append(res.get("error"))
            return
        if runner is not None:
            traced = runner.execute_with_trace(query, True, capture_plan=False)
            if "_error" in traced:
                slot["errors"].append(traced["_error"])
                return
            slot["times"].append(float(traced.get("total_ms", 0) or 0))
        else:
            ms = res.get("execution_time_ms")
            if isinstance(ms, (int, float)):
                slot["times"].append(float(ms))

    if warmup:
        for name in names:
            raise_if_cancelled()
            _one(name, record=False)
    for i in range(max(1, runs)):
        order = names if i % 2 == 0 else list(reversed(names))
        for name in order:
            raise_if_cancelled()
            _one(name, record=True)
    return out


def _build_grain_query(expression: str, filter_clause: str, grain: str) -> str:
    """SUMMARIZECOLUMNS query evaluating `expression` at `grain`.

    Both sides alias the value column "value" by construction, so the
    equivalence comparison aligns positionally without guessing.
    """
    if filter_clause:
        return (
            f"EVALUATE SUMMARIZECOLUMNS({grain}, "
            f'"value", CALCULATE({expression}, {filter_clause}))'
        )
    return f'EVALUATE SUMMARIZECOLUMNS({grain}, "value", {expression})'


def _run_equivalence_check(
    qe,
    *,
    original_measure: str,
    optimized_expression: str,
    filter_clause: str,
    grain: Optional[str],
    tolerance: float,
    scalar_rows: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Execute (or reuse) both result sets and return an equivalence verdict.

    With a `grain`, both sides are re-evaluated with SUMMARIZECOLUMNS so the
    comparison covers many rows rather than one total. Without a grain, the
    already-executed scalar rows go through the SAME compare_rows path so the
    verdict shape is uniform (`grain: "scalar"`).

    Fails CLOSED: a query error yields `{"equivalent": False,
    "reason": "query_failed"}` — a failed query is never treated as equivalent.
    """
    from core.dax.semantic_equivalence import compare_rows, verdict

    alias_map = {"value": "value", "Original": "value", "Optimized": "value"}

    if not grain:
        # Fail closed: no usable scalar rows means at least one query failed.
        if not scalar_rows or len(scalar_rows) != 2:
            v = verdict(equivalent=False, reason="query_failed", tolerance_used=tolerance)
        else:
            v = compare_rows(
                scalar_rows[0],
                scalar_rows[1],
                tolerance=tolerance,
                column_alias_map=alias_map,
            )
        v["grain"] = "scalar"
        return v

    query_a = _build_grain_query(original_measure, filter_clause, grain)
    query_b = _build_grain_query(optimized_expression, filter_clause, grain)

    result_a = qe.validate_and_execute_dax(query_a, top_n=None, bypass_cache=True)
    result_b = qe.validate_and_execute_dax(query_b, top_n=None, bypass_cache=True)

    if not result_a.get("success") or not result_b.get("success"):
        v = verdict(equivalent=False, reason="query_failed", tolerance_used=tolerance)
        v["grain"] = grain
        v["original_query"] = query_a
        v["optimized_query"] = query_b
        v["original_error"] = result_a.get("error")
        v["optimized_error"] = result_b.get("error")
        return v

    v = compare_rows(
        result_a.get("rows") or [],
        result_b.get("rows") or [],
        tolerance=tolerance,
        column_alias_map=alias_map,
    )
    v["grain"] = grain
    v["original_query"] = query_a
    v["optimized_query"] = query_b
    return v


def handle_compare_measures(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compare original measure vs optimized version with same filter context.

    Can use filter context from a specific visual or manually specified.

    Pass `equivalence_check=true` (optionally with `equivalence_grain`) to
    validate that the optimized expression returns the SAME data before
    trusting the performance delta — the result carries
    `comparison.performance_claim_valid`.
    """
    if str(args.get("mode") or "").lower() == "live_swap":
        # F-08: A/B in the real measure chain (checkpoint -> write -> measure -> restore -> verify).
        from server.handlers._debug.live_swap import handle_live_swap

        return handle_live_swap(args)
    try:
        original_measure = args.get("original_measure")
        optimized_expression = args.get("optimized_expression")
        page_name = args.get("page_name")
        visual_id = args.get("visual_id")
        visual_name = args.get("visual_name")
        equivalence_check = bool(args.get("equivalence_check", False))
        equivalence_grain = args.get("equivalence_grain")

        if not original_measure:
            return error_envelope("missing_argument", "original_measure is required")

        if not optimized_expression:
            return error_envelope("missing_argument", "optimized_expression is required")

        if not connection_state.is_connected():
            return error_envelope("not_connected", "Not connected to Power BI model")

        from core.infrastructure.impersonated_executor import get_executor_for_args

        qe = get_executor_for_args(args)
        if not qe:
            return ErrorHandler.handle_manager_unavailable("query_executor")

        # Build filter context (shared by the scalar and the grain queries)
        not_applied: List[Dict[str, Any]] = []
        filter_dax_parts = _assemble_compare_filters(args, qe, not_applied)

        # Ensure measure has brackets (F-37: ']' escaped)
        if not original_measure.startswith("[") and not original_measure.startswith("'"):
            from core.dax.dax_names import bracket

            original_measure = bracket(original_measure)

        # Build queries
        filter_clause = ", ".join(filter_dax_parts) if filter_dax_parts else ""

        if filter_clause:
            original_query = (
                f'EVALUATE ROW("Original", CALCULATE({original_measure}, {filter_clause}))'
            )
            optimized_query = (
                f'EVALUATE ROW("Optimized", CALCULATE({optimized_expression}, {filter_clause}))'
            )
        else:
            original_query = f'EVALUATE ROW("Original", {original_measure})'
            optimized_query = f'EVALUATE ROW("Optimized", {optimized_expression})'

        # Execute both queries: warm-up + interleaved runs, cache bypassed (F-22).
        try:
            runs = max(1, min(int(args.get("runs", 3) or 3), 20))
        except (TypeError, ValueError):
            runs = 3
        timed = _timed_runs(
            qe,
            {"original": original_query, "optimized": optimized_query},
            runs=runs,
            warmup=bool(args.get("warmup", True)),
            trace=bool(args.get("trace", False)),
        )
        original_result = timed["original"]["result"] or {"success": False}
        optimized_result = timed["optimized"]["result"] or {"success": False}

        # Extract values
        original_value = None
        optimized_value = None
        # Fastest run per side = the least noise-polluted observation.
        orig_times = timed["original"]["times"]
        opt_times = timed["optimized"]["times"]
        original_time = min(orig_times) if orig_times else 0
        optimized_time = min(opt_times) if opt_times else 0

        if original_result.get("success") and original_result.get("rows"):
            row = original_result["rows"][0]
            original_value = row.get("Original", row.get("[Original]"))

        if optimized_result.get("success") and optimized_result.get("rows"):
            row = optimized_result["rows"][0]
            optimized_value = row.get("Optimized", row.get("[Optimized]"))

        # Compare
        values_match = False
        difference = None

        if original_value is not None and optimized_value is not None:
            try:
                orig_num = float(original_value)
                opt_num = float(optimized_value)
                difference = opt_num - orig_num
                threshold = args.get("tolerance", 0.001)
                values_match = abs(difference) < threshold
            except (ValueError, TypeError):
                values_match = str(original_value) == str(optimized_value)
                difference = "N/A (non-numeric)"

        # Performance comparison
        perf_improvement_ms = original_time - optimized_time
        perf_improvement_pct = (
            (perf_improvement_ms / original_time * 100) if original_time > 0 else 0
        )

        comparison: Dict[str, Any] = {
            "values_match": values_match,
            "difference": difference,
            "performance_improvement_ms": perf_improvement_ms,
            "performance_improvement_pct": round(perf_improvement_pct, 1),
            "timing": {
                "method": "traced_cold" if args.get("trace") else "wall_clock_warm",
                "order": "interleaved",
                "runs_per_side": runs,
                "warmup": bool(args.get("warmup", True)),
                "kept": "fastest",
                "original_runs_ms": orig_times,
                "optimized_runs_ms": opt_times,
            },
        }

        # --- semantic-equivalence guard (opt-in) -------------------------
        # A single scalar under a single filter context is NOT proof of
        # equivalence; with equivalence_check the results are compared as a
        # multiset of rows (at `equivalence_grain` when given) and the perf
        # claim is only valid when they match.
        if equivalence_check:
            tolerance = args.get("tolerance", 0.001)
            scalar_rows = None
            if original_result.get("success") and optimized_result.get("success"):
                scalar_rows = [
                    original_result.get("rows") or [],
                    optimized_result.get("rows") or [],
                ]
            equivalence = _run_equivalence_check(
                qe,
                original_measure=original_measure,
                optimized_expression=optimized_expression,
                filter_clause=filter_clause,
                grain=equivalence_grain,
                tolerance=tolerance,
                scalar_rows=scalar_rows,
            )
            comparison["equivalence"] = equivalence
            claim_valid = bool(equivalence.get("equivalent"))
        else:
            claim_valid = bool(values_match)

        comparison["performance_claim_valid"] = claim_valid
        if not claim_valid:
            comparison["performance_warning"] = (
                "Timings are NOT comparable: the optimized expression does not "
                "return the same results as the original"
                + (
                    f" (equivalence check: {comparison['equivalence'].get('reason')})"
                    if equivalence_check
                    else " (scalar values differ)"
                )
                + ". Fix the expression before claiming a performance win."
            )

        return {
            "success": True,
            "original": {
                "measure": original_measure,
                "query": original_query,
                "value": original_value,
                "execution_time_ms": original_time,
                "success": original_result.get("success", False),
                "error": original_result.get("error"),
                "cache": timed["original"]["cache"],
            },
            "optimized": {
                "expression": optimized_expression,
                "query": optimized_query,
                "value": optimized_value,
                "execution_time_ms": optimized_time,
                "success": optimized_result.get("success", False),
                "error": optimized_result.get("error"),
                "cache": timed["optimized"]["cache"],
            },
            "comparison": comparison,
            "filter_context": {
                "source": f"visual:{visual_id or visual_name}" if page_name else "manual",
                "filters_applied": len(filter_dax_parts),
                "filter_dax": filter_dax_parts,
                "fidelity": "partial" if not_applied else "full",
                "not_applied": not_applied,
            },
        }

    except Exception as e:
        logger.error(f"Error in compare_measures: {e}", exc_info=True)
        return ErrorHandler.handle_unexpected_error("compare_measures", e)


def handle_drill_to_detail(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Show the underlying rows that make up an aggregated value.

    Uses the visual's filter context to query the fact table.
    """
    try:
        page_name = args.get("page_name")
        visual_id = args.get("visual_id")
        visual_name = args.get("visual_name")
        fact_table = args.get("fact_table")
        limit = args.get("limit", MAX_QUERY_RESULT_ROWS)
        limit = max(DRILL_DETAIL_LIMIT_MIN, min(DRILL_DETAIL_LIMIT_MAX, limit))
        include_slicers = args.get("include_slicers", True)

        if not connection_state.is_connected():
            return error_envelope("not_connected", "Not connected to Power BI model")

        from core.infrastructure.impersonated_executor import get_executor_for_args

        qe = get_executor_for_args(args)
        if not qe:
            return ErrorHandler.handle_manager_unavailable("query_executor")

        builder, error = _get_visual_query_builder()
        if error:
            return error_envelope("operation_failed", error)

        # Load column types for accurate filter generation (shared executor, F-38)
        builder.load_column_types(connection_state.query_executor or qe)

        # Build detail query
        query = builder.build_detail_rows_query(
            page_name=page_name or "",
            visual_id=visual_id,
            visual_name=visual_name,
            fact_table=fact_table,
            limit=limit,
            include_slicers=include_slicers,
        )

        if not query:
            return error_envelope(
                "invalid_argument",
                "Could not build detail query. Specify fact_table if visual cannot be found.",
            )

        # Execute
        result = qe.validate_and_execute_dax(query, top_n=limit, bypass_cache=True)

        if not result.get("success"):
            return error_envelope("operation_failed", result.get("error"), query_attempted=query)

        return {
            "success": True,
            "query": query,
            "row_count": result.get("row_count", len(result.get("rows", []))),
            "rows": result.get("rows", []),
            "columns": result.get("columns", []),
            "execution_time_ms": result.get("execution_time_ms"),
            "cache": cache_provenance(result),
        }

    except Exception as e:
        logger.error(f"Error in drill_to_detail: {e}", exc_info=True)
        return ErrorHandler.handle_unexpected_error("drill_to_detail", e)


def handle_set_pbip_path(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Manually set the PBIP folder path for visual debugging.

    Use this if auto-detection didn't work or you want to analyze a different project.
    """
    try:
        pbip_path = args.get("pbip_path")

        if not pbip_path:
            return error_envelope("missing_argument", "pbip_path is required")

        import os

        if not os.path.exists(pbip_path):
            return error_envelope("not_found", f"Path does not exist: {pbip_path}")

        # Smart resolution: try to resolve to a valid .Report folder
        from core.utilities.pbip_utils import resolve_pbip_report_path

        resolved_path = resolve_pbip_report_path(pbip_path)

        if resolved_path:
            effective_path = resolved_path
        else:
            # Direct validation (original behavior)
            definition_path = os.path.join(pbip_path, "definition")
            report_json = os.path.join(pbip_path, "report.json")
            if not os.path.exists(definition_path) and not os.path.exists(report_json):
                return error_envelope(
                    "operation_failed",
                    "Path does not appear to be a valid PBIP folder. "
                    "Expected: .pbip file, .Report folder, or directory "
                    "containing .pbip files. Got: " + pbip_path,
                )
            effective_path = pbip_path

        # Set the resolved path
        result = connection_state.set_pbip_info(
            pbip_folder_path=effective_path,
            file_full_path=pbip_path,
            file_type="pbip",
            source="manual",
        )

        response = {
            "success": True,
            "pbip_info": result,
            "message": f"PBIP path set to: {effective_path}",
        }
        if effective_path != pbip_path:
            response["resolved_from"] = pbip_path
            response["resolved_to"] = effective_path
        return response

    except Exception as e:
        logger.error(f"Error in 09_Debug_Operations set_path: {e}", exc_info=True)
        return ErrorHandler.handle_unexpected_error("09_Debug_Operations:set_path", e)


def handle_get_debug_status(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Get the current debug capabilities status.

    Shows whether PBIP and model connection are available.
    """
    try:
        compact = args.get("compact", True)
        pbip_info = connection_state.get_pbip_info()
        is_connected = connection_state.is_connected()
        pbip_available = pbip_info.get("pbip_available", False)

        builder = None
        pages = []
        if pbip_available:
            builder, _ = _get_visual_query_builder()
            if builder:
                pages = builder.list_pages()

        if compact:
            return {
                "success": True,
                "connected": is_connected,
                "pbip": pbip_available,
                "ready": is_connected and pbip_available,
                "pages": [p.get("name") for p in pages],
            }
        else:
            return {
                "success": True,
                "connection": {
                    "is_connected": is_connected,
                    "info": connection_state._connection_info,
                },
                "pbip": pbip_info,
                "capabilities": {
                    "analyze_filters": pbip_available,
                    "execute_queries": is_connected,
                    "debug_visuals": pbip_available and is_connected,
                    "compare_measures": is_connected,
                },
                "pages": pages,
                "recommendations": _get_recommendations(pbip_info, is_connected),
            }

    except Exception as e:
        logger.error(f"Error in get_debug_status: {e}", exc_info=True)
        return ErrorHandler.handle_unexpected_error("get_debug_status", e)


def _get_recommendations(pbip_info: Dict, is_connected: bool) -> List[str]:
    """Generate recommendations based on current status."""
    recommendations = []

    if not is_connected:
        recommendations.append(
            "Connect to Power BI Desktop with 01_Connection operation=connect for query execution"
        )

    if not pbip_info.get("pbip_available"):
        if pbip_info.get("file_type") == "pbix":
            recommendations.append(
                "You have a .pbix file open. Convert to PBIP (Save As > Power BI Project) for visual debugging"
            )
        else:
            recommendations.append(
                "Use 09_Debug_Operations operation=set_path to specify your PBIP folder path"
            )

    if is_connected and pbip_info.get("pbip_available"):
        recommendations.append(
            "Full debugging available! Use 09_Debug_Operations operation=visual on any visual"
        )

    return recommendations
