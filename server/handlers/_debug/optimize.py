"""
optimize operation — post-trace performance diagnosis + suggestions.

Extracted from debug_handler.py as part of P2-183 split.

Public entry points (re-exported via debug_handler.py):

- _handle_optimize          — primary dispatcher for operation=optimize
- handle_optimize_measure   — back-compat alias (older call sites)
- _diagnose_timing, _build_optimize_suggestions, _build_next_steps —
  helpers (kept for test access)
"""

import logging
import math
import re
from typing import Any, Dict, Optional

from core.infrastructure.connection_state import connection_state
from core.validation.error_response import error_envelope

_default_connection_state = connection_state
from core.validation.error_handler import ErrorHandler  # noqa: E402
from server.handlers._debug.helpers import _build_trace_response, _compact_response  # noqa: E402

logger = logging.getLogger(__name__)


def _diagnose_timing(perf: dict, se_analysis: Optional[dict] = None) -> dict:
    """Convert raw SE/FE metrics into a human-readable diagnosis.

    Args:
        perf: Performance dict with timing aggregates
        se_analysis: Optional SE event analysis (for fusion-aware datacache thresholds)
    """
    fe_pct = perf.get("fe_pct", 0)
    se_queries = perf.get("se_queries", 0)
    se_ms = perf.get("se_ms", 0)
    se_par = perf.get("se_parallelism", 0)
    notes = []

    if fe_pct > 80:
        profile = "FE-bound"
        notes.append(
            f"Formula Engine consumed {fe_pct}% of total time. "
            "The FE is single-threaded and uncached — this is the bottleneck. "
            "Focus on reducing iterators, context transitions, and pushing work to SE."
        )
    elif fe_pct > 50:
        profile = "FE-heavy"
        notes.append(
            f"Formula Engine consumed {fe_pct}% of time. "
            "DAX complexity is dominant. Consider simplifying filter arguments and reducing iterator scope."
        )
    elif fe_pct < 20:
        profile = "SE-bound"
        notes.append(
            f"Storage Engine consumed {100 - fe_pct}% of time. "
            "Check for large table scans, high-cardinality DISTINCTCOUNT, or missing aggregation tables."
        )
    else:
        profile = "balanced"
        notes.append(
            f"Good FE/SE balance ({fe_pct}% FE, {100 - fe_pct:.0f}% SE). "
            "Query performance is likely acceptable at this complexity level."
        )

    # Fusion-aware datacache threshold: tighten to 128 when many fusion breaks
    fusion_breaks = 0
    if se_analysis:
        fusion = se_analysis.get("fusion_opportunities", {})
        fusion_breaks = fusion.get("vertical_breaks", 0)
    datacache_limit = 128 if fusion_breaks > 5 else 256

    if se_queries > 256:
        notes.append(
            f"CRITICAL: SE query count ({se_queries}) exceeds the datacache limit of 256. "
            "Cache provides no benefit. Redesign required to reduce SE fan-out."
        )
    elif se_queries > datacache_limit:
        fusion_note = (
            f" (threshold tightened to {datacache_limit} due to {fusion_breaks} "
            f"vertical fusion breaks)"
            if datacache_limit < 256
            else ""
        )
        notes.append(
            f"High SE query count ({se_queries}){fusion_note}. "
            "Investigate iterator structure — repeated sub-expressions should be "
            "captured in VAR to avoid re-evaluation."
        )
    elif se_queries > 50:
        notes.append(
            f"High SE query count ({se_queries}). Investigate iterator structure — "
            "repeated sub-expressions should be captured in VAR to avoid re-evaluation."
        )

    if se_par < 1.0 and se_ms > 20:
        notes.append(f"SE parallelism ({se_par}x) is sequential — single-threaded SE execution.")
    elif 1.0 <= se_par < 2.0 and se_ms > 50:
        notes.append(
            f"SE parallelism ({se_par}x) is limited — not fully utilizing CPU cores. "
            "This can indicate FE serialization forcing sequential SE calls."
        )

    # SE CPU utilization analysis
    se_cpu = perf.get("se_cpu_ms", 0)
    if se_ms > 50 and se_cpu > 0:
        cpu_ratio = se_cpu / se_ms
        if cpu_ratio > 3.0:
            notes.append(
                f"SE CPU time ({se_cpu}ms) is {cpu_ratio:.1f}x wall time ({se_ms}ms) — "
                "heavy parallel CPU computation. Check for DISTINCTCOUNT or complex aggregations."
            )
        elif cpu_ratio < 0.3 and se_ms > 100:
            notes.append(
                f"SE CPU time ({se_cpu}ms) is only {cpu_ratio:.0%} of wall time ({se_ms}ms) — "
                "possible I/O wait or lock contention."
            )

    # Absolute time assessment
    total_ms = perf.get("total_ms", 0)
    if total_ms < 50:
        notes.append(f"Fast query ({total_ms}ms) — SE/FE ratios are less meaningful at this speed.")
    elif total_ms > 2000:
        notes.append(f"Slow query ({total_ms}ms) — significant optimization opportunity.")

    # SE/FE ratio benchmark (ideal ~20% FE / 80% SE)
    ideal_fe = 20
    deviation = abs(fe_pct - ideal_fe)
    if deviation > 40:
        notes.append(
            f"FE% ({fe_pct}%) is {deviation:.0f}% off ideal (20%). Major rebalancing needed."
        )
    elif deviation > 20:
        notes.append(f"FE% ({fe_pct}%) is {deviation:.0f}% off ideal (20%). Room for improvement.")

    # Datacache limit proximity
    if 200 < se_queries <= 256:
        notes.append(
            f"SE queries ({se_queries}) approaching datacache limit of 256. Performance cliff ahead."
        )

    return {"profile": profile, "notes": notes}


def _build_optimize_suggestions(
    bpa_result: dict,
    rewriter_result: dict,
    callback_info: dict,
    static_callbacks: dict,
    perf: dict,
    vertipaq_result: Optional[dict] = None,
) -> list:
    """
    Merge runtime CallbackDataID findings, static callback detections, BPA issues,
    and rewriter suggestions into a single prioritized optimization list.

    Priority scoring includes cardinality-weighted boost: same anti-pattern on a
    high-cardinality table ranks above the same issue on a small table.

    Priority order:
      0. Runtime CallbackDataID in SE trace (confirmed, always critical)
      1. Static callback detections from DAX analysis
      2. Timing-correlated high/critical BPA issues
      3. Non-timing-correlated high/critical BPA issues
      4. Timing-correlated medium BPA issues
      5. High-confidence rewriter suggestions
      6. Non-timing-correlated medium/low BPA issues
      7. Medium-confidence rewriter suggestions
    """
    suggestions = []
    fe_pct = perf.get("fe_pct", 0) if perf else 0

    # Compute cardinality boost from VertiPaq analysis (0-5 range)
    max_cardinality = 0
    if vertipaq_result and vertipaq_result.get("success"):
        col_analysis = vertipaq_result.get("column_analysis", {})
        for col_data in col_analysis.values():
            if isinstance(col_data, dict):
                card = col_data.get("cardinality", 0)
                if card > max_cardinality:
                    max_cardinality = card
    cardinality_boost = (
        min(5, max(0, math.log10(max_cardinality) - 2)) if max_cardinality > 100 else 0
    )

    # --- Source 1: Runtime CallbackDataID in SE trace ---
    if callback_info.get("detected"):
        for cb in callback_info.get("queries", []):
            suggestions.append(
                {
                    "priority": 0,
                    "severity": "critical",
                    "category": "callback_elimination",
                    "title": "CallbackDataID detected in Storage Engine query (runtime)",
                    "description": (
                        "A CallbackDataID forces the Formula Engine (single-threaded, uncached) "
                        "to be invoked for every row during SE scan. "
                        "This can increase query time by 100x–260x compared to a pure SE query. "
                        f"Triggered in SE event line {cb.get('line')} ({cb.get('duration_ms')}ms). "
                        "Common causes: ROUND/TRUNC/INT/CEILING/FLOOR inside SUMX, "
                        "DIVIDE() anywhere, IF() inside iterators, text functions in row context."
                    ),
                    "code_before": cb.get("snippet", ""),
                    "code_after": (
                        "-- Pre-group by distinct values to minimize callback invocations:\n"
                        "SUMX(\n"
                        "    SUMMARIZE(FactTable, FactTable[ColumnWithLowCardinality]),\n"
                        "    CALCULATE(SUM(FactTable[Qty])) * callback_function(FactTable[ColumnWithLowCardinality])\n"
                        ")"
                    ),
                    "estimated_improvement": "10x–260x reduction in SE CPU time",
                    "confidence": "high",
                    "timing_correlated": True,
                }
            )

    # --- Source 2: Static callback detections from CallbackDetector ---
    for cb_det in static_callbacks.get("callback_detections") or []:
        sev = cb_det.get("severity", "high")
        suggestions.append(
            {
                "priority": 1,
                "severity": sev,
                "category": "callback_elimination",
                "title": f"Callback risk [{cb_det.get('rule_id')}]: {cb_det.get('description', '')[:80]}",
                "description": cb_det.get("description", ""),
                "code_before": cb_det.get("match_text", ""),
                "code_after": cb_det.get("fix_suggestion", ""),
                "estimated_improvement": "Eliminates per-row FE invocation from SE scan",
                "confidence": "high",
                "timing_correlated": fe_pct > 50,
            }
        )

    # --- FE-bound timing context — which BPA categories to boost ---
    fe_correlated_categories = set()
    if fe_pct > 50:
        fe_correlated_categories = {"performance", "anti_pattern"}

    # --- Source 3: BPA issues ---
    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

    for issue in bpa_result.get("issues") or []:
        sev = issue.get("severity", "low")
        cat = issue.get("category", "")
        is_timing_correlated = cat in fe_correlated_categories or sev in ("critical", "high")

        sev_rank = severity_rank.get(sev, 3)
        priority_base = sev_rank * 10
        if is_timing_correlated:
            priority_base -= 3
        # Cardinality boost: high-cardinality tables get higher priority
        priority_base -= int(cardinality_boost)
        # FE boost: FE-bound queries with performance issues get extra priority
        fe_boost = 0
        if fe_pct > 50 and cat == "performance":
            fe_boost = min(5, int((fe_pct - 50) / 10))
            priority_base -= fe_boost

        suggestions.append(
            {
                "priority": max(2, 10 + priority_base),  # Never displace callbacks (0-1)
                "severity": sev,
                "category": cat,
                "title": issue.get("title", ""),
                "description": issue.get("description", ""),
                "code_before": issue.get("code_example_before"),
                "code_after": issue.get("code_example_after"),
                "estimated_improvement": issue.get("estimated_improvement"),
                "confidence": "high",
                "timing_correlated": is_timing_correlated,
            }
        )

    # --- Source 4: Code rewriter transformations ---
    confidence_rank = {"high": 0, "medium": 1, "low": 2}
    for t in rewriter_result.get("transformations") or []:
        conf = t.get("confidence", "low")
        if conf == "low":
            continue
        suggestions.append(
            {
                "priority": 50 + confidence_rank.get(conf, 2),
                "severity": "medium" if conf == "high" else "low",
                "category": "code_rewrite",
                "title": f"Code rewrite: {t.get('type', 'optimization')}",
                "description": t.get("explanation", ""),
                "code_before": t.get("original", ""),
                "code_after": t.get("transformed", ""),
                "estimated_improvement": t.get("estimated_improvement"),
                "confidence": conf,
                "timing_correlated": False,
            }
        )

    suggestions.sort(key=lambda x: x["priority"])

    # Deduplicate: if two suggestions have very similar titles, keep higher priority
    _DEDUP_STRIP_RE = re.compile(r"\[cb\d+\]:\s*|callback risk\s*", re.IGNORECASE)
    _DEDUP_CLEAN_RE = re.compile(r"[^a-z0-9 ]")
    _DEDUP_NOISE = {"code", "rewrite", "detected", "found", "risk", "pattern"}
    seen_sigs: set = set()
    deduped: list = []
    for s in suggestions:
        title_key = _DEDUP_STRIP_RE.sub("", s.get("title", "")).lower().strip()
        title_key = _DEDUP_CLEAN_RE.sub("", title_key)
        words = [w for w in title_key.split() if len(w) > 3 and w not in _DEDUP_NOISE]
        sig = " ".join(sorted(words[:5]))
        if sig and sig in seen_sigs:
            continue
        if sig:
            seen_sigs.add(sig)
        deduped.append(s)
    suggestions = deduped

    for s in suggestions:
        s.pop("priority", None)
    return suggestions


def _build_next_steps(
    optimizations: list,
    perf: dict,
    callback_info: dict,
    static_callbacks: dict,
    context_result: Optional[dict] = None,
    vertipaq_result: Optional[dict] = None,
    se_analysis: Optional[dict] = None,
) -> list:
    """Generate 2–5 actionable next steps based on findings."""
    steps = []
    fe_pct = perf.get("fe_pct", 0) if perf else 0
    se_queries = perf.get("se_queries", 0) if perf else 0
    has_rewrites = any(o.get("category") == "code_rewrite" for o in optimizations)
    has_static_callbacks = bool(static_callbacks.get("summary", {}).get("total", 0))

    # SE event deep analysis guidance (highest priority if confirmed at runtime)
    if se_analysis:
        if se_analysis.get("callbacks", {}).get("detected"):
            cb = se_analysis["callbacks"]
            steps.append(
                f"Runtime CallbackDataID confirmed ({cb['total_count']} occurrences in SE events). "
                "This is the #1 priority — eliminate callbacks via SUMMARIZE pre-grouping."
            )
        fusion = se_analysis.get("fusion_opportunities", {})
        if fusion.get("vertical_breaks", 0) > 2:
            steps.append(
                f"Vertical fusion broken ({fusion['vertical_breaks']} SE queries could be fused). "
                "Check for IF/SWITCH returning different measures — restructure to CALCULATE pattern."
            )

    if callback_info.get("detected") and not (
        se_analysis and se_analysis.get("callbacks", {}).get("detected")
    ):
        steps.append(
            "PRIORITY: Eliminate CallbackDataID — open DAX Studio Server Timings, "
            "locate the SE query containing CallbackDataID, identify the triggering "
            "function (ROUND/IF/DIVIDE inside iterator), and restructure using SUMMARIZE "
            "to pre-group by distinct values."
        )
    elif has_static_callbacks and not (
        se_analysis and se_analysis.get("callbacks", {}).get("detected")
    ):
        steps.append(
            "Callback risk detected in DAX expression. "
            "Pass se_events from the visual trace to optimize for deep runtime confirmation."
        )

    if fe_pct > 60:
        steps.append(
            "FE-bound query: focus on pushing filter predicates to SE. "
            "Replace FILTER(table, condition) with Boolean expression CALCULATE arguments. "
            "Use KEEPFILTERS instead of FILTER(VALUES(...), ...)."
        )

    if se_queries > 50:
        steps.append(
            f"High SE query count ({se_queries}): introduce VAR to capture repeated "
            "sub-expressions (e.g., prior-year CALCULATE blocks used in both numerator "
            "and denominator of DIVIDE)."
        )

    # Context transition guidance
    if context_result and context_result.get("max_nesting_level", 0) > 3:
        steps.append(
            f"Deep CALCULATE nesting detected (depth {context_result['max_nesting_level']}). "
            "Flatten nested CALCULATE into single CALCULATE with multiple filter arguments."
        )

    # VertiPaq cardinality guidance
    if vertipaq_result and vertipaq_result.get("high_cardinality_columns"):
        high_card = vertipaq_result["high_cardinality_columns"]
        col_names = ", ".join(
            c if isinstance(c, str) else c.get("column", str(c)) for c in high_card[:3]
        )
        steps.append(
            f"High-cardinality columns in DAX: {col_names}. "
            "If these appear in iterators, reduce scope using SUMMARIZE or filter to subset."
        )

    if has_rewrites:
        steps.append(
            "A recommended_rewrite is available. Test it using "
            "operation=compare with original_measure and optimized_expression "
            "to benchmark the improvement."
        )

    if not steps:
        steps.append(
            "No critical issues detected. This measure has good SE/FE balance. "
            "Monitor performance if data volume increases significantly."
        )

    return steps


def _handle_optimize(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    DAX analysis + optimization suggestions for a single measure.

    Accepts a measure name, resolves its expression, detects anti-patterns
    (static callback detection, context transitions, VertiPaq cardinality,
    best practices, code rewrites), and returns prioritized optimization
    suggestions with before/after code examples.

    REQUIRES SE/FE timing data from a prior visual trace run
    (operation=visual, trace=true). Pass fe_pct, se_queries, total_ms,
    fe_ms, se_ms. Optionally pass se_events for deep SE analysis.
    """
    try:
        measure_name = (args.get("measure_name") or "").strip()
        table_name = args.get("table_name")
        compact = args.get("compact", True)

        if not measure_name:
            return error_envelope(
                "missing_argument",
                "measure_name is required for optimize operation",
                hint='Example: {"operation": "optimize", "measure_name": "Net Asset Value"}',
            )

        # Look `connection_state` and `_resolve_measure_expression` up through
        # the parent debug_handler module so that unittest.mock patches targeting
        # `server.handlers.debug_handler.<name>` take effect here.
        from server.handlers import debug_handler as _dh

        if not _dh.connection_state.is_connected():
            return error_envelope(
                "not_connected",
                "Not connected to Power BI model. Use 01_Connection operation=connect first.",
            )

        qe = _dh.connection_state.query_executor
        if not qe:
            return ErrorHandler.handle_manager_unavailable("query_executor")

        # ── Step 1: Resolve measure expression ───────────────────────────────
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
        measure_details = resolved["measure_details"]
        resolved_table = measure_details.get("table_name") or table_name or "m Measure"
        expression_source = resolved.get("expression_source", "unknown")

        # ── Step 2: Build perf dict from timing input params or cached trace ──
        perf: dict = {}
        timing_provided = False
        timing_source = "explicit_params"
        timing_keys = [
            "total_ms",
            "fe_ms",
            "se_ms",
            "se_cpu_ms",
            "fe_pct",
            "se_pct",
            "se_queries",
            "se_cache_hits",
            "se_parallelism",
        ]
        for key in timing_keys:
            val = args.get(key)
            if val is not None:
                perf[key] = val
                timing_provided = True

        se_events_input = args.get("se_events", [])
        trace_info: Dict[str, Any] = {}
        traced_query: Optional[str] = None

        # L-20 / F-26: never borrow "whatever was traced last in the process".
        # Timing comes from explicit args, from an explicit trace_id, or from the
        # most recent trace ONLY when it provably timed this measure and nothing
        # else was traced meanwhile; otherwise ROW([measure]) is traced now.
        if not timing_provided:
            selected, trace_info, refusal = _select_trace(args, measure_name, _dh.connection_state)
            if refusal is not None:
                return refusal
            if selected is not None:
                for key in timing_keys:
                    val = (selected.get("performance") or {}).get(key)
                    if val is not None:
                        perf[key] = val
                        timing_provided = True
                if timing_provided:
                    timing_source = trace_info.get("source", "cached_trace")
                    traced_query = selected.get("query")
                    # Use _se_events_raw (full query text) for analyzer compatibility
                    if not se_events_input:
                        se_events_input = selected.get("_se_events_raw") or selected.get(
                            "se_events", []
                        )

        if not timing_provided:
            fresh = _trace_measure_now(
                measure_name, bool(args.get("clear_cache", True)), _dh.connection_state
            )
            if fresh.get("performance"):
                perf.update({k: v for k, v in fresh["performance"].items() if k in timing_keys})
                timing_provided = True
                timing_source = "fresh_trace_row"
                traced_query = fresh.get("query")
                trace_info = {
                    "source": "fresh_trace_row",
                    "trace_id": fresh.get("trace_id"),
                    "query": traced_query,
                    "note": (
                        "No trace of this measure was available, so ROW([measure]) was "
                        "traced now (cold, no visual filter context). Pass trace_id from a "
                        "09_Debug visual trace=true run to diagnose it in the visual's context."
                    ),
                }
                if not se_events_input:
                    se_events_input = fresh.get("_se_events_raw") or []

        if not timing_provided:
            return {
                "success": False,
                "error": "SE/FE timing data is required. Run operation=visual with trace=true first, "
                "then pass its trace_id (or fe_pct, se_queries, total_ms, fe_ms, se_ms) to optimize.",
                "hint": 'Example: {"operation": "optimize", "measure_name": "Total Sales", '
                '"trace_id": "trace_ab12cd34ef56"}',
                **({"trace_selection": trace_info} if trace_info else {}),
            }

        # ── Step 3: Static callback detection (DAX expression analysis) ───────
        try:
            from core.dax.callback_detector import CallbackDetector

            static_callbacks = CallbackDetector().detect_dict(expression)
        except Exception as ce:
            logger.warning(f"Static callback detection failed: {ce}")
            static_callbacks = {"callback_detections": [], "summary": {"total": 0}}

        # ── Step 4-5: Context + VertiPaq analysis (shared pipeline) ─────────
        from core.dax.analysis_pipeline import (
            run_context_analysis,
            run_vertipaq_analysis,
            run_best_practices,
        )

        _, context_result = run_context_analysis(expression)
        vertipaq_result = run_vertipaq_analysis(expression, connection_state)

        # ── Step 6: DAX best practices analysis (shared pipeline) ─────────
        bpa_result = run_best_practices(expression, context_result, vertipaq_result) or {
            "total_issues": 0,
            "critical_issues": 0,
            "issues": [],
        }

        # ── Step 7: Code rewriter ─────────────────────────────────────────────
        from core.dax.code_rewriter import DaxCodeRewriter

        rewriter_result = DaxCodeRewriter().rewrite_dax(expression)

        # ── Step 8: SE event deep analysis ───────────────────────────────────
        se_analysis = None
        if se_events_input:
            try:
                from core.dax.se_event_analyzer import SeEventAnalyzer

                se_analysis = SeEventAnalyzer().analyze(se_events_input, perf)
            except Exception as se_err:
                logger.warning(f"SE event analysis failed: {se_err}")

        # ── Step 9: Timing diagnosis (after SE analysis for fusion awareness) ─
        timing_diagnosis = _diagnose_timing(perf, se_analysis=se_analysis)

        # ── Step 9b: Optional cold/warm cache comparison ─────────────────────
        cache_comparison_result = None
        if args.get("cache_comparison") and se_events_input:
            try:
                from core.dax.dax_names import bracket
                from core.dax.se_event_analyzer import SeEventAnalyzer as _SeAnalyzer
                from core.infrastructure.query_trace import NativeTraceRunner

                conn_str = (
                    connection_state.connection_manager.connection_string
                    if connection_state.connection_manager
                    else None
                )
                if conn_str and NativeTraceRunner.is_available():
                    # F-26: re-run the SAME query warm -- the cold events came from it.
                    warm_query = traced_query or f'EVALUATE ROW("Result", {bracket(measure_name)})'
                    runner = NativeTraceRunner(conn_str)
                    warm_result = runner.execute_with_trace(
                        warm_query, clear_cache=False, capture_plan=False
                    )
                    if "_error" not in warm_result:
                        warm_events = warm_result.get("se_events", [])
                        cache_comparison_result = _SeAnalyzer.compare_cache_impact(
                            se_events_input, warm_events
                        )
                        cache_comparison_result["warm_query_is_traced_query"] = bool(traced_query)
            except Exception as cc_err:
                logger.warning(f"Cache comparison failed: {cc_err}")

        # ── Step 10: Synthesize suggestions ──────────────────────────────────
        # Extract runtime callback info from SE analysis (if available)
        callback_info: dict = {}
        if se_analysis and se_analysis.get("callbacks", {}).get("detected"):
            cb_data = se_analysis["callbacks"]
            callback_info = {
                "detected": True,
                "count": cb_data.get("total_count", 0),
                "queries": [
                    {
                        "line": q.get("line"),
                        "duration_ms": q.get("duration_ms"),
                        "snippet": q.get("snippet", ""),
                    }
                    for cb_type_list in cb_data.get("by_type", {}).values()
                    for q in cb_type_list
                ],
            }
        optimizations = _build_optimize_suggestions(
            bpa_result,
            rewriter_result,
            callback_info,
            static_callbacks,
            perf,
            vertipaq_result=vertipaq_result,
        )

        # ── Step 11: Next steps ──────────────────────────────────────────────
        next_steps = _build_next_steps(
            optimizations,
            perf,
            callback_info,
            static_callbacks,
            context_result=context_result,
            vertipaq_result=vertipaq_result,
            se_analysis=se_analysis,
        )

        # ── Step 12: Build tl;dr ─────────────────────────────────────────────
        tl_dr_parts = []
        if static_callbacks.get("summary", {}).get("critical", 0):
            tl_dr_parts.append(
                f"{static_callbacks['summary']['critical']} critical callback patterns"
            )
        if bpa_result.get("critical_issues", 0):
            tl_dr_parts.append(f"{bpa_result['critical_issues']} critical DAX anti-patterns")
        fe_pct_val = perf.get("fe_pct", 0)
        if fe_pct_val > 60:
            tl_dr_parts.append(f"FE-bound at {fe_pct_val}%")
        if tl_dr_parts:
            tl_dr = f"Key issues: {'; '.join(tl_dr_parts)}. See optimizations for fixes."
        else:
            tl_dr = "No critical issues. Measure has acceptable performance characteristics."

        # ── Step 13: Build response ──────────────────────────────────────────
        response: Dict[str, Any] = {
            "success": True,
            "tl_dr": tl_dr,
            "measure": {
                "name": measure_name,
                "table": resolved_table,
                "expression": expression,
                "source": expression_source,
            },
            "timing": perf,
            "timing_source": timing_source,
            **({"trace_selection": trace_info} if trace_info else {}),
            "timing_diagnosis": timing_diagnosis,
            "dax_analysis": {
                "score": bpa_result.get("overall_score"),
                "complexity": bpa_result.get("complexity_level"),
                "total_issues": bpa_result.get("total_issues", 0),
                "critical": bpa_result.get("critical_issues", 0),
                "high": bpa_result.get("high_issues", 0),
                "medium": bpa_result.get("medium_issues", 0),
                "low": sum(
                    1 for i in bpa_result.get("issues", []) if i.get("severity") in ("low", "info")
                ),
            },
            "callback_analysis": {
                "static_total": static_callbacks.get("summary", {}).get("total", 0),
                "static_critical": static_callbacks.get("summary", {}).get("critical", 0),
            },
            "optimizations": optimizations,
            "next_steps": next_steps,
        }

        if context_result:
            response["context_analysis"] = context_result
        if vertipaq_result and vertipaq_result.get("success"):
            response["vertipaq_analysis"] = vertipaq_result
        if se_analysis:
            response["se_analysis"] = se_analysis
        if cache_comparison_result:
            response["cache_comparison"] = cache_comparison_result

        if rewriter_result.get("has_changes"):
            response["recommended_rewrite"] = rewriter_result.get("rewritten_code")

        # ── Compact mode: trim verbose sections ──────────────────────────────
        if compact:
            if len(optimizations) > 5:
                response["optimizations"] = optimizations[:5]
            if context_result and "context_analysis" in response:
                response["context_analysis"] = {
                    "complexity_score": context_result.get("complexity_score"),
                    "max_nesting_level": context_result.get("max_nesting_level"),
                    "transition_count": context_result.get("transition_count"),
                }
            if se_analysis and "se_analysis" in response:
                response["se_analysis"] = {
                    k: v
                    for k, v in se_analysis.items()
                    if v and v != {} and v != [] and v != {"detected": False, "total_count": 0}
                }

        # ── Per-measure timing (opt-in, slow) ────────────────────────────────
        if args.get("per_measure_trace"):
            try:
                from server.handlers._debug.variables import (
                    _measure_refs_in,
                    _trace_measures_individually,
                )

                # F-17: lexer-driven refs, confirmed against the model's measure list
                # so column refs ('T'[Amount]) are never traced as measures.
                known = _model_measure_names(qe)
                measure_refs = _measure_refs_in(expression, known)
                # Deduplicate and add the main measure
                all_measures = list(dict.fromkeys([measure_name] + measure_refs))
                # Limit to avoid excessive tracing
                all_measures = all_measures[:10]

                per_measure = _trace_measures_individually(
                    all_measures, clear_cache=args.get("clear_cache", True)
                )
                if per_measure:
                    response["per_measure_timing"] = per_measure
                    response["per_measure_caveat"] = (
                        "Individual traces lack the visual combined filter context. "
                        "Timings are indicative, not exact replicas of visual performance."
                    )
            except Exception as pm_err:
                logger.warning(f"Per-measure tracing failed: {pm_err}")

        return _compact_response(response, compact)

    except Exception as e:
        logger.error(f"Error in handle_optimize_measure: {e}", exc_info=True)
        return ErrorHandler.handle_unexpected_error("optimize_measure", e)


def _model_measure_names(qe) -> set:
    """Every measure name in the model (empty set when unavailable)."""
    try:
        res = qe.execute_info_query("MEASURES", exclude_columns=["Expression"])
    except Exception:  # noqa: BLE001
        return set()
    if not isinstance(res, dict) or not res.get("success"):
        return set()
    return {str(r.get("Name")) for r in res.get("rows") or [] if r.get("Name")}


def _select_trace(
    args: Dict[str, Any], measure_name: str, cs: Any = None
) -> "tuple[Optional[Dict[str, Any]], Dict[str, Any], Optional[Dict[str, Any]]]":
    """Pick the stored trace optimize may use (L-20). Returns ``(trace, info, refusal)``.

    * ``trace_id`` given -> that trace or a ``trace_not_found`` refusal.
    * otherwise the last trace, but only when (a) no OTHER query was traced in the
      window (a sibling agent's parallel trace makes "last" ambiguous) and (b) it
      timed a query that evaluates ``measure_name``. Anything else -> ``(None, info,
      None)`` so the caller traces the measure itself.
    """
    connection_state = cs if cs is not None else _default_connection_state
    trace_id = args.get("trace_id")
    if trace_id:
        trace = connection_state.get_trace_result(str(trace_id))
        if trace is None:
            return (
                None,
                {},
                {
                    "success": False,
                    "error": f"trace_id {trace_id!r} not found (traces are kept ~20 deep)",
                    "error_type": "trace_not_found",
                    "recent_traces": connection_state.get_last_trace_info().get("recent", []),
                },
            )
        return (
            trace,
            {
                "source": "trace_id",
                "trace_id": str(trace_id),
                "query": (trace.get("query") or "")[:300],
            },
            None,
        )

    info = connection_state.get_last_trace_info()
    if not info.get("available"):
        return None, {"source": "none", "note": "no trace in the last 5 minutes"}, None
    trace = connection_state.get_trace_result(info["trace_id"], max_age_seconds=300)
    selection: Dict[str, Any] = {
        "source": "cached_trace",
        "trace_id": info.get("trace_id"),
        "query": (info.get("query") or "")[:300],
        "age_s": info.get("age_s"),
    }
    if trace is None:
        return None, {**selection, "rejected": "expired"}, None
    if info.get("ambiguous"):
        return (
            None,
            {
                **selection,
                "rejected": "ambiguous",
                "recent": info.get("recent", []),
                "note": "several different queries were traced recently; pass trace_id",
            },
            None,
        )
    if info.get("stale_model"):
        return None, {**selection, "rejected": "model changed since the trace"}, None
    names = [str(n).lower() for n in (trace.get("measure_names") or [])]
    query = str(trace.get("query") or "")
    wanted = measure_name.strip("[]").lower()
    matches = wanted in names or (f"[{wanted}]" in query.lower() and not names)
    if not matches:
        return (
            None,
            {
                **selection,
                "rejected": "the last trace did not evaluate this measure",
                "traced_measures": trace.get("measure_names") or [],
            },
            None,
        )
    return trace, selection, None


def _trace_measure_now(measure_name: str, clear_cache: bool, cs: Any = None) -> Dict[str, Any]:
    """Trace ``EVALUATE ROW("Result", [measure])`` now (F-26 fallback)."""
    from core.dax.dax_names import bracket

    connection_state = cs if cs is not None else _default_connection_state

    try:
        from core.infrastructure.query_trace import NativeTraceRunner

        conn_str = (
            connection_state.connection_manager.connection_string
            if connection_state.connection_manager
            else None
        )
        if not conn_str or not NativeTraceRunner.is_available():
            return {}
        query = f'EVALUATE ROW("Result", {bracket(measure_name.strip("[]"))})'
        raw = NativeTraceRunner(conn_str).execute_with_trace(query, clear_cache, capture_plan=False)
        if "_error" in raw:
            return {"error": raw["_error"]}
        response = _build_trace_response(raw)
        response["query"] = query
        response["measure_names"] = [measure_name.strip("[]")]
        response["source"] = "09_Debug_Operations:optimize"
        response["trace_id"] = connection_state.store_trace_result(response)
        return response
    except Exception as e:  # noqa: BLE001
        logger.warning(f"optimize fresh trace failed: {e}")
        return {"error": str(e)}


# Back-compat alias (older call sites used handle_optimize_measure)
handle_optimize_measure = _handle_optimize
