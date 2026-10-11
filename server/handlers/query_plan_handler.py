"""13_Query_Plan handler — capture/parse/analyze/compare DAX query plans."""

from __future__ import annotations

import logging
from typing import Any, Dict

from core.query_plan import capturer, exporter, heuristics, parser
from server.registry import ToolDefinition

logger = logging.getLogger(__name__)

_VALID_OPS = {
    "capture",
    "parse",
    "analyze",
    "compare",
    "list_rules",
    "evaluate_and_log",
    "benchmark",
    "plan_rows",
    "export_daxx",
    "trace_start",
    "trace_fetch",
    "trace_stop",
}


def _err(msg: str, et: str = "invalid_args") -> Dict[str, Any]:
    return {"success": False, "error": msg, "error_type": et}


def _query(args: Dict[str, Any]) -> Any:
    """The DAX text: canonical ``query`` (L-11), legacy ``dax_query``."""
    return args.get("query") or args.get("dax_query")


def _time_budget_s() -> float:
    """13_Query_Plan's tool timeout minus a margin (F-14): loops stop before it."""
    try:
        from core.config.config_manager import config
        from core.config.tool_timeouts import ToolTimeoutManager

        mgr = ToolTimeoutManager(config.get("tool_timeouts", {}) or {})
        timeout = float(mgr.get_timeout("13_Query_Plan", default=120))
    except Exception:  # noqa: BLE001
        timeout = 120.0
    return max(10.0, timeout - 15.0)


def _coerce_rows(value: Any) -> Any:
    """Best-effort int for a caller-supplied result row count (None when unusable)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        rows = int(value)
    except (TypeError, ValueError):
        return None
    return rows if rows >= 0 else None


def _merge_joins(parsed: list) -> Dict[str, int]:
    """Sum the per-query xmSQL join-kind counts into one histogram."""
    merged: Dict[str, int] = {}
    for item in parsed:
        for kind, n in (item.get("joins") or {}).items():
            merged[kind] = merged.get(kind, 0) + n
    return merged


def _visual_builder(args: Dict[str, Any]):
    """``(builder, error)`` for ``match=visuals``: ``pbip_path`` if given, else the folder
    ``09_Debug_Operations set_path`` (or the open project) established."""
    if args.get("pbip_path"):
        try:
            from core.debug.visual_query_builder import VisualQueryBuilder

            return VisualQueryBuilder(str(args["pbip_path"])), None
        except Exception as exc:  # noqa: BLE001
            return (
                None,
                f"cannot read the PBIP at {args['pbip_path']!r}: {type(exc).__name__}: {exc}",
            )
    from server.handlers._debug.helpers import _get_visual_query_builder

    builder, error = _get_visual_query_builder()
    return builder, error


def _match_visuals(result: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    """F-02: ``match=visuals`` - tag each captured query with the PBIR visual that sent it."""
    from core.debug.trace_visual_match import annotate_trace_result

    if result.get("success") is False:
        return result
    builder, error = _visual_builder(args)
    if error or builder is None:
        for q in result.get("queries") or []:
            q.pop("_text", None)
        result["visual_matches"] = {
            "error_type": "pbip_path_required",
            "error": error or "PBIP path not set (09_Debug_Operations set_path or pass pbip_path)",
        }
        return result
    try:
        from core.infrastructure.connection_state import connection_state

        if connection_state.is_connected() and connection_state.query_executor:
            builder.load_column_types(connection_state.query_executor)
    except Exception:  # noqa: BLE001 - column types only sharpen the reconstruction
        pass
    return annotate_trace_result(result, builder, page_name=args.get("page_name"))


def _handle_live_trace(op: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """TRC-01: a long-lived trace while visuals are driven (core/infrastructure/live_trace)."""
    from core.infrastructure import live_trace

    fetch_kwargs = {
        "since": int(args.get("since") or 0),
        "include_text": bool(args.get("include_text")),
        "slowest_first": bool(args.get("slowest_first")),
    }
    match = str(args.get("match") or "").lower()
    if match and match != "visuals":
        return _err(f"match must be 'visuals', got {match!r}", "invalid_args")
    if match:
        fetch_kwargs["keep_text"] = True  # full DAX for the matcher; stripped before returning
    if op == "trace_fetch":
        result = live_trace.fetch(**fetch_kwargs)
        return _match_visuals(result, args) if match else result
    if op == "trace_stop":
        result = live_trace.stop(**fetch_kwargs)
        return _match_visuals(result, args) if match else result
    from core.infrastructure.connection_state import connection_state
    from core.infrastructure import xmla_target

    cm = connection_state.connection_manager
    conn_str = getattr(cm, "connection_string", None) if cm else None
    if not conn_str:
        return _err("Not connected", "not_connected")
    if xmla_target.is_active():
        # N-11: a remote (XMLA) trace is filtered server-side to our own session, so it
        # can never see the report's visual queries; refuse instead of returning an
        # empty "success".
        return {
            "success": False,
            "error_type": "trace_unsupported_on_service",
            "error": (
                "trace_start cannot capture a report's queries on a published (XMLA) model: "
                "the service trace is filtered to this server's own session."
            ),
            "suggestion": "Open the PBIP in Desktop (01_Connection ensure) to trace report interactions.",
        }
    opts = xmla_target.trace_options()

    def factory(cs):
        from core.infrastructure.trace_session import TraceSession

        return TraceSession(cs, **opts)

    return live_trace.start(
        conn_str,
        max_duration_s=args.get("max_duration_s"),
        max_events=args.get("max_events"),
        label=args.get("label"),
        session_factory=factory,
    )


def handle_query_plan(args: Dict[str, Any]) -> Dict[str, Any]:
    op = args.get("operation")
    if op not in _VALID_OPS:
        return {
            "success": False,
            "error": f"Unknown operation {op!r}",
            "valid_operations": sorted(_VALID_OPS),
            "error_type": "invalid_args",
        }

    if op == "list_rules":
        return {"success": True, "rules": heuristics.list_rules()}

    if op in ("trace_start", "trace_fetch", "trace_stop"):
        return _handle_live_trace(op, args)

    if op == "capture":
        dax = _query(args)
        if not dax:
            return _err("query is required")
        cap = capturer.capture(
            dax_query=dax,
            clear_cache=bool(args.get("clear_cache", True)),
            include_xmsql=bool(args.get("include_xmsql", True)),
        )
        if not cap.get("success"):
            return cap
        exports = exporter.write_exports(cap, dax_query=dax)
        summary = exporter.build_capture_summary(cap, exports)
        # F-34: keep only a slim capture in memory; raw plans live in the export dir.
        capturer.remember(cap, spill_dir=exports.get("dir") if exports.get("written") else None)
        return {
            "success": True,
            **summary,
            # Extended trace coverage
            "dq_query_count": cap.get("dq_query_count", 0),
            "dq_total_ms": cap.get("dq_total_ms", 0),
            "se_cache_match_count": cap.get("se_cache_match_count", 0),
            "se_cache_removal_count": cap.get("se_cache_removal_count", 0),
            "next_steps": (
                "Plan rows are file-backed, not inlined. Use operation='plan_rows' "
                "(capture_id, plan, offset, limit, min_records) to page them, or open "
                "capture.daxx in DAX Studio."
            ),
        }

    if op == "plan_rows":
        cap = capturer.recall(args.get("capture_id")) if args.get("capture_id") else None
        if cap is None:
            return _err("capture_id not found; run capture first")
        return exporter.page_plan_rows(
            cap,
            plan=str(args.get("plan", "physical")),
            offset=int(args.get("offset", 0) or 0),
            limit=int(args.get("limit", 100) or 100),
            min_records=(int(args["min_records"]) if args.get("min_records") is not None else None),
        )

    if op == "export_daxx":
        cap = capturer.recall(args.get("capture_id")) if args.get("capture_id") else None
        if cap is None:
            return _err("capture_id not found; run capture first")
        exports = exporter.write_exports(cap, dax_query=cap.get("dax_query", ""))
        if not exports.get("written"):
            return {
                "success": False,
                "error": exports.get("reason", "export failed"),
                "error_type": "export_failed",
            }
        return {"success": True, "exports": exports}

    if op == "parse":
        cap_id = args.get("capture_id")
        cap = capturer.recall(cap_id) if cap_id else None
        if cap is None:
            return _err("capture_id not found; run capture first")
        logical = parser.parse_logical_plan(capturer.plan_text(cap, "logical_plan_raw"))
        physical = parser.parse_logical_plan(capturer.plan_text(cap, "physical_plan_raw"))
        xmsql = [parser.parse_xmsql(q) for q in cap.get("xmsql_queries", [])]
        # The physical plan is the one that carries #Records; fall back to the
        # logical tree so the summary is still populated for a logical-only
        # capture (e.g. DirectQuery, which emits no physical plan).
        summary = parser.plan_summary(physical)
        if summary.get("node_count", 0) == 0:
            summary = parser.plan_summary(logical)
        return {
            "success": True,
            "logical_tree": logical,
            "physical_tree": physical,
            "xmsql_parsed": xmsql,
            "summary": summary,
        }

    if op == "analyze":
        if args.get("capture_id"):
            cap = capturer.recall(args["capture_id"])
            if cap is None:
                return _err("capture_id not found")
        elif _query(args):
            cap = capturer.capture(
                dax_query=_query(args),
                clear_cache=bool(args.get("clear_cache", True)),
                include_xmsql=bool(args.get("include_xmsql", True)),
            )
            if not cap.get("success"):
                return cap
            capturer.remember(cap)
        else:
            return _err("query or capture_id is required")
        tree = parser.parse_logical_plan(
            capturer.plan_text(cap, "logical_plan_raw")
            + "\n"
            + capturer.plan_text(cap, "physical_plan_raw")
        )
        queries = cap.get("xmsql_queries", []) or []
        parsed_xmsql = [parser.parse_xmsql(q) for q in queries]
        xmsql_summary = {
            "has_distinctcount": any("DISTINCTCOUNT" in (q or "").upper() for q in queries),
            "select_count": sum(1 for _ in queries),
            # Extended trace counters consumed by new heuristic rules
            "callback_count": cap.get("callback_count", 0),
            "dq_query_count": cap.get("dq_query_count", 0),
            "dq_total_ms": cap.get("dq_total_ms", 0),
            "agg_rewrite_nomatch_count": sum(
                1 for r in cap.get("agg_rewrites", []) if r.get("status") == "nomatch"
            ),
            # Structural xmSQL signals, folded across every SE query in the capture
            "has_define_table": any(p["has_define_table"] for p in parsed_xmsql),
            "has_inindex": any(p["has_inindex"] for p in parsed_xmsql),
            "has_large_in_list": any(p["has_large_in_list"] for p in parsed_xmsql),
            "in_list_max_size": max(
                (p["in_list_max_size"] for p in parsed_xmsql if p["in_list_max_size"] is not None),
                default=None,
            ),
            "callback_forms": sorted({c for p in parsed_xmsql for c in p["callbacks"]}),
            "joins": _merge_joins(parsed_xmsql),
        }
        timings = dict(cap.get("se_fe_timings", {}) or {})
        # `records_blowup` needs the query's result row count. The trace runner
        # does not report it, so the caller passes it in; a capture that already
        # carries one wins nothing over an explicit argument.
        rows = _coerce_rows(args.get("result_rows"))
        if rows is None:
            rows = _coerce_rows(timings.get("result_rows"))
        if rows is not None:
            timings["result_rows"] = rows
        verdict = heuristics.analyze(tree, xmsql_summary, timings)
        verdict["success"] = True
        verdict["capture_id"] = cap.get("capture_id")
        verdict["plan_summary"] = parser.plan_summary(tree)
        verdict["result_rows"] = rows
        if rows is None:
            verdict["note"] = (
                "result_rows not supplied — the `records_blowup` rule (max #Records vs "
                "result row count) was skipped. Pass result_rows=<row count from "
                "04_Run_DAX> to enable it."
            )
        return verdict

    if op == "evaluate_and_log":
        # L-12: a thin alias of 04_Run_DAX mode=evaluate_and_log (same code path).
        from server.handlers.query_handler import execute_dax

        if not _query(args):
            return _err("query is required")
        return execute_dax(
            {
                "query": _query(args),
                "mode": "evaluate_and_log",
                "clear_cache": args.get("clear_cache", True),
            },
            source="13_Query_Plan:evaluate_and_log",
        )

    if op == "benchmark":
        dax = _query(args)
        if not dax:
            return _err("query is required")
        # cold_runs/warm_runs drive the traced lane (per-run SE/FE); the legacy
        # runs/cold_cache pair still selects the wall-clock lane so existing
        # callers keep the behaviour they were written against.
        budget = _time_budget_s()
        if args.get("compare_dax_query"):
            # F-22 / F-39(5): interleaved A/B, cold + traced every run.
            return capturer.benchmark_ab(
                dax_query_a=dax,
                dax_query_b=str(args["compare_dax_query"]),
                runs=int(args.get("runs", 5) or 5),
                warmup=int(args.get("warmup", 1)),
                time_budget_s=budget,
            )
        if args.get("cold_runs") is not None or args.get("warm_runs") is not None:
            return capturer.benchmark_traced(
                dax_query=dax,
                cold_runs=int(args.get("cold_runs", 3) or 0),
                warm_runs=int(args.get("warm_runs", 3) or 0),
                warmup=int(args.get("warmup", 1)),
                time_budget_s=budget,
            )
        return capturer.benchmark(
            dax_query=dax,
            runs=int(args.get("runs", 5)),
            cold_cache=bool(args.get("cold_cache", True)),
            warmup=int(args.get("warmup", 1)),
            time_budget_s=budget,
        )

    if op == "compare":
        a, b = args.get("capture_id_a"), args.get("capture_id_b")
        if not a or not b:
            return _err("capture_id_a and capture_id_b are required")
        if a == b:
            # F-08: a capture diffed against itself always says "no change".
            return _err("capture_id_a and capture_id_b are the same capture")
        ca, cb = capturer.recall(a), capturer.recall(b)
        if ca is None or cb is None:
            return _err("one or both capture_ids not found")
        ta = ca.get("se_fe_timings", {}) or {}
        tb = cb.get("se_fe_timings", {}) or {}
        delta = {
            k: tb.get(k, 0) - ta.get(k, 0)
            for k in set(ta) | set(tb)
            if isinstance(ta.get(k, 0), (int, float)) and isinstance(tb.get(k, 0), (int, float))
        }
        from pbi_perf_core.query_plan import compare_plans

        # A timing delta says the rewrite got faster; the structural diff says
        # what the engine actually stopped doing, which is what generalises.
        physical_diff = compare_plans(
            capturer.parsed_plan(ca, "physical"), capturer.parsed_plan(cb, "physical")
        )
        logical_diff = compare_plans(
            capturer.parsed_plan(ca, "logical"), capturer.parsed_plan(cb, "logical")
        )
        return {
            "success": True,
            "same_query": bool(
                ca.get("query_hash") and ca.get("query_hash") == cb.get("query_hash")
            ),
            "cache_cleared": {"a": ca.get("cache_cleared"), "b": cb.get("cache_cleared")},
            "timing_delta": delta,
            "physical_plan_diff": physical_diff,
            "logical_plan_diff": logical_diff,
        }

    return _err(f"Unhandled op {op!r}")


def register_query_plan_handler(registry):
    registry.register(
        ToolDefinition(
            name="13_Query_Plan",
            description=(
                "DAX QUERY PLANS - explain WHY a query is slow: capture (summary + DAX Studio "
                "exports ServerTimings.json / QueryPlan.json / capture.daxx in "
                "exports/perf/<capture_id>/), plan_rows (page a plan: plan, offset, limit, "
                "min_records), export_daxx, parse, analyze (verdict ok|warn|hot), compare "
                "(structural plan diff + timing delta), list_rules, evaluate_and_log (= "
                "04_Run_DAX mode=evaluate_and_log), benchmark (cold_runs / warm_runs; "
                "compare_dax_query = interleaved A/B), trace_start/fetch/stop (live trace while "
                "visuals run). The numbers: 04_Run_DAX; page timings: 09_Profile."
            ),
            guide_notes=(
                "DAX QUERY PLAN capture and analysis - logical plan, physical "
                "plan (operators, classes, #Records), xmSQL, storage-engine "
                "scans, CallbackDataID, spools, aggregation rewrites - with a "
                "heuristic verdict (ok|warn|hot). Ops: capture (SUMMARY + writes "
                "DAX Studio ServerTimings.json/QueryPlan.json/capture.daxx to "
                "exports/perf/<capture_id>/; plans are never inlined - they reach "
                "10k+ rows), plan_rows (page the plan: plan, offset, limit, "
                "min_records), export_daxx (re-write the exports), parse, analyze, "
                "compare (structural plan diff + timing delta; capture ids are unique per "
                "run, so before/after captures of one query never overwrite each other), "
                "list_rules, trace_start / trace_fetch / trace_stop (a long-lived "
                "trace while the report is driven: every query with SE/FE timings; "
                "auto-stops after max_duration_s, default 300; blocks other traces "
                "until stopped; since / include_text / slowest_first), "
                "evaluate_and_log (EVALUATEANDLOG trace events for in-place VAR "
                "inspection), benchmark (cold_runs then warm_runs with per-run "
                "server timings and avg/stddev/min/max/median; legacy runs/"
                "cold_cache does the wall-clock percentile summary; compare_dax_query "
                "runs an interleaved A/B). "
                "parse/analyze/compare/plan_rows take the capture_id from a prior "
                "capture, not raw DAX. Use this to explain WHY a query is slow at "
                "the engine level; for the numbers themselves use 04_Run_DAX "
                "(mode=trace), for per-visual page timings use 09_Profile."
            ),
            handler=handle_query_plan,
            input_schema={
                "type": "object",
                "properties": {
                    "operation": {"type": "string", "enum": sorted(_VALID_OPS)},
                    "query": {"type": "string", "description": "DAX query text"},
                    "capture_id": {"type": "string"},
                    "max_duration_s": {
                        "type": "integer",
                        "description": "trace_start: auto-stop after N s (300, max 3600)",
                    },
                    "max_events": {
                        "type": "integer",
                        "description": "trace_start: raw event cap (200000)",
                    },
                    "since": {
                        "type": "integer",
                        "description": "trace_fetch / trace_stop: first query seq to return",
                    },
                    "include_text": {
                        "type": "boolean",
                        "description": "trace_fetch / trace_stop: full query text",
                    },
                    "slowest_first": {
                        "type": "boolean",
                        "description": "trace_fetch / trace_stop: sort by total_ms desc",
                    },
                    "match": {
                        "type": "string",
                        "enum": ["visuals"],
                        "description": "trace_fetch / trace_stop: tag each query with the PBIR visual that sent it",
                        "x-doc": (
                            "trace_fetch / trace_stop match=visuals: maps every captured query to a "
                            "visual of the PBIP (09_Debug_Operations set_path or pbip_path) by the "
                            "engine's ApplicationContext VisualId when the event carries one, else by "
                            "query shape (group-by columns, measures, filter tables vs each visual's "
                            "reconstructed query). Rows gain visual_match {match{visual_id, page, "
                            "confidence, method}, ambiguous, candidates}; the result gains "
                            "visual_matches {by_visual: per-visual count / total_ms}. A tie between "
                            "different visuals is never auto-picked."
                        ),
                    },
                    "pbip_path": {
                        "type": "string",
                        "description": "match=visuals: PBIP to match against (default: the set_path project)",
                    },
                    "page_name": {
                        "type": "string",
                        "description": "match=visuals: only index this page's visuals",
                    },
                    "label": {"type": "string", "description": "trace_start: free label"},
                    "capture_id_a": {"type": "string"},
                    "capture_id_b": {"type": "string"},
                    "clear_cache": {"type": "boolean", "default": True},
                    "include_xmsql": {"type": "boolean", "default": True},
                    "result_rows": {
                        "type": "integer",
                        "description": (
                            "analyze: the query's result row count (enables the "
                            "records_blowup rule)."
                        ),
                        "x-doc": (
                            "analyze: row count the DAX query actually returns. Enables the "
                            "`records_blowup` rule (max #Records vs result rows — the headline "
                            "materialization diagnostic). Skipped when omitted."
                        ),
                    },
                    "plan": {
                        "type": "string",
                        "enum": ["physical", "logical"],
                        "default": "physical",
                        "description": "plan_rows: which plan to page",
                    },
                    "offset": {
                        "type": "integer",
                        "default": 0,
                        "description": "plan_rows: first row index to return",
                    },
                    "limit": {
                        "type": "integer",
                        "default": 100,
                        "description": "plan_rows: rows per page (clamped 1..500)",
                    },
                    "min_records": {
                        "type": "integer",
                        "description": "plan_rows: only rows with #Records >= this (finds the hotspot).",
                        "x-doc": (
                            "plan_rows: only return rows whose #Records is at least this. "
                            "The fastest way to find the materialization hotspot in a 10k-row plan."
                        ),
                    },
                    "cold_runs": {
                        "type": "integer",
                        "default": 3,
                        "description": (
                            "benchmark: traced runs with the SE cache cleared (0..20). "
                            "Presence of cold_runs/warm_runs selects the traced lane."
                        ),
                    },
                    "warm_runs": {
                        "type": "integer",
                        "default": 3,
                        "description": "benchmark: traced runs against a warm cache (0..20)",
                    },
                    "runs": {
                        "type": "integer",
                        "default": 5,
                        "description": "benchmark: number of measured runs (clamped 1..50)",
                    },
                    "cold_cache": {
                        "type": "boolean",
                        "default": True,
                        "description": "benchmark: clear VertiPaq cache before each run",
                    },
                    "warmup": {
                        "type": "integer",
                        "default": 1,
                        "description": "benchmark: warmup runs before measurement (0..5)",
                    },
                    "compare_dax_query": {
                        "type": "string",
                        "description": (
                            "benchmark: query B for an interleaved, cold, traced A/B (runs "
                            "per side)."
                        ),
                        "x-doc": (
                            "benchmark: a second query B; runs an INTERLEAVED A/B benchmark "
                            "(A,B,B,A,...), SE cache cleared and traced every run, compared "
                            "on the fastest run of each side. Uses `runs` per side (1..20)."
                        ),
                    },
                },
                "required": ["operation"],
            },
            category="debug",
            sort_order=1300,
            # WP2 (A-10/L-08): analysis-only. benchmark/capture may evict the
            # engine cache and export writes artifact files -> not idempotent.
            annotations={
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": False,
                "openWorldHint": True,
            },
        )
    )
