"""
AUDIT OPERATION — Automated full-report visual audit + debug loop.

Extracted from debug_handler.py as part of P2-183 split.

Public entry points (imported back into debug_handler for dispatch and
back-compat):

- handle_audit
- handle_debug_loop
- _is_data_visual
- _audit_build_and_execute
- _audit_check_math
- _audit_single_page

The module-level constants _AUDIT_SKIP_TYPES and MAX_AUDIT_ROWS live here.
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List

from core.config.config_manager import config
from core.debug.visual_query_builder import is_grand_total_row
from core.infrastructure.connection_state import connection_state
from core.validation.error_handler import ErrorHandler
from server.handlers._debug.helpers import cache_provenance
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)


# Visual types that are never data visuals (skip in audit)
_AUDIT_SKIP_TYPES = {
    "shape",
    "image",
    "button",
    "bookmarknavigator",
    "textbox",
    "group",
    "actionButton",
    "basicShape",
}

MAX_AUDIT_ROWS = config.get("debug.audit_max_rows_per_visual", 20)


def _is_data_visual(visual: Dict) -> bool:
    """Check if a visual is a data visual worth auditing."""
    vtype = (visual.get("type") or "").lower().replace(" ", "")
    friendly = (visual.get("name") or "").lower()
    measures = visual.get("measures", [])

    # Skip non-data types
    if vtype in _AUDIT_SKIP_TYPES:
        return False
    if "visual group" in friendly or "Visual Group" in visual.get("type", ""):
        return False
    if "bookmark" in vtype.lower():
        return False

    # Must have at least one measure
    return len(measures) > 0


def _audit_build_and_execute(
    builder: Any, qe: Any, page_name: str, visual: Dict, max_rows: int
) -> Dict[str, Any]:
    """Build query for a visual and execute it. Returns audit result dict."""
    visual_id = visual.get("id")
    visual_name = visual.get("name", "")
    visual_type = visual.get("type", "")
    measures = visual.get("measures", [])

    result_entry = {
        "visual_id": visual_id,
        "visual_name": visual_name,
        "visual_type": visual_type,
        "measures": measures,
        "status": "pending",
    }

    try:
        # Load column types for accurate filter generation
        try:
            builder.load_column_types(qe)
        except Exception:
            pass

        # Build the visual query
        build_result = builder.build_visual_query(
            page_name=page_name,
            visual_id=visual_id,
            include_slicers=True,
        )

        if not build_result:
            result_entry["status"] = "skipped"
            result_entry["reason"] = "Could not build query"
            return result_entry

        query = build_result.dax_query
        result_entry["query"] = query
        result_entry["fidelity"] = getattr(build_result, "fidelity", "full")
        if getattr(build_result, "unsupported_filters", None):
            result_entry["unsupported_filters"] = build_result.unsupported_filters

        # Execute the query -- always a real round-trip: a post-change audit that
        # replays pre-change rows from the result cache is a false all-clear (F-07).
        exec_result = qe.validate_and_execute_dax(query, top_n=max_rows, bypass_cache=True)

        if exec_result.get("success"):
            rows = exec_result.get("rows", [])
            result_entry["status"] = "ok"
            result_entry["row_count"] = len(rows)
            result_entry["ms"] = exec_result.get("execution_time_ms")
            result_entry["cache"] = cache_provenance(exec_result)
            result_entry["data"] = rows

            if not rows:
                result_entry["status"] = "empty"
                result_entry["flag"] = "No rows returned"
            elif all(all(v is None for v in row.values()) for row in rows):
                result_entry["status"] = "all_null"
                result_entry["flag"] = "All values are NULL"

            # Run anomaly detection
            if rows and len(rows) > 1:
                try:
                    from core.debug.anomaly_detector import analyze_results

                    anomalies = analyze_results(rows)
                    if anomalies:
                        result_entry["anomalies"] = anomalies
                except Exception:
                    pass
        else:
            error_msg = exec_result.get("error", "Unknown error")

            # Retry: strip composite TREATAS (stale PBIP date filters)
            if "cannot convert" in error_msg.lower() or "composite" in error_msg.lower():
                try:
                    all_filters = build_result.filter_context.all_filters()
                    # Remove multi-column TREATAS (composite keys that cause type errors)
                    dropped = [
                        f
                        for f in all_filters
                        if f.composite_columns or "composite" in (f.condition_type or "").lower()
                    ]
                    simple_filters = [f for f in all_filters if f not in dropped]
                    if dropped:
                        retry_query = builder.rebuild_query(build_result, simple_filters)
                        retry_result = qe.validate_and_execute_dax(
                            retry_query, top_n=max_rows, bypass_cache=True
                        )
                        if retry_result.get("success"):
                            rows = retry_result.get("rows", [])
                            result_entry["status"] = "ok_retry"
                            result_entry["row_count"] = len(rows)
                            result_entry["ms"] = retry_result.get("execution_time_ms")
                            result_entry["data"] = rows
                            result_entry["retry_note"] = "Retried without composite TREATAS filters"
                            result_entry["fidelity"] = "partial"
                            result_entry["query"] = retry_query

                            if not rows:
                                result_entry["status"] = "empty_retry"
                                result_entry["flag"] = "No rows after retry"
                            return result_entry
                except Exception as retry_err:
                    logger.debug(f"Audit retry failed: {retry_err}")

            result_entry["status"] = "error"
            result_entry["error"] = error_msg[:500]

    except Exception as e:
        result_entry["status"] = "error"
        result_entry["error"] = str(e)[:500]

    return result_entry


def _audit_check_math(page_results: List[Dict]) -> List[Dict]:
    """Run cross-visual math checks on a page's results."""
    checks = []

    for vr in page_results:
        if vr.get("status") not in ("ok", "ok_retry"):
            continue
        rows = vr.get("data", [])
        if not rows:
            continue

        # Check %Share columns sum to ~1.0 (grand-total row excluded, bracketed or
        # bare IsGrandTotalRowTotal key -- ADOMD returns "[IsGrandTotalRowTotal]", F-20)
        for col_name in rows[0].keys():
            if "%share" in col_name.lower() or "share" in col_name.lower():
                try:
                    values = [
                        float(r.get(col_name, 0) or 0)
                        for r in rows
                        if r.get(col_name) is not None and not is_grand_total_row(r)
                    ]
                    if values:
                        total = sum(values)
                        if abs(total - 1.0) > 0.01 and abs(total) > 0.01:
                            checks.append(
                                {
                                    "check": "share_sum",
                                    "visual": vr.get("visual_name"),
                                    "column": col_name,
                                    "sum": round(total, 4),
                                    "expected": 1.0,
                                    "flag": f"Sum of {col_name} = {round(total, 4)}, expected ~1.0",
                                }
                            )
                except (ValueError, TypeError):
                    pass

        # Check for negative values in NAV-like columns
        for col_name in rows[0].keys():
            col_lower = col_name.lower()
            if "nav" in col_lower or "net_asset" in col_lower:
                try:
                    grand_total_rows = [r for r in rows if is_grand_total_row(r)]
                    if grand_total_rows:
                        val = float(grand_total_rows[0].get(col_name, 0) or 0)
                        if val < 0:
                            checks.append(
                                {
                                    "check": "negative_nav",
                                    "visual": vr.get("visual_name"),
                                    "column": col_name,
                                    "value": val,
                                    "flag": f"Grand total NAV is negative: {val}",
                                }
                            )
                except (ValueError, TypeError):
                    pass

    return checks


def _audit_single_page(builder, qe, page_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Audit a single page: discover visuals, execute queries, check results.
    Returns full inline results for that page (no file save, no stripping).
    """
    from server.handlers._debug.helpers import _compact_visual_list

    max_rows = args.get("max_rows", MAX_AUDIT_ROWS)
    include_data = args.get("include_data", False)
    skip_types = set(args.get("skip_types", []))
    skip_types.update(_AUDIT_SKIP_TYPES)

    start_time = time.time()
    logger.info(f"Auditing page: {page_name}")

    # List visuals on this page
    try:
        visuals = builder.list_visuals(page_name)
    except Exception as e:
        return error_envelope(
            "operation_failed",
            str(e)[:200],
            page=page_name,
            status="error",
        )

    # Filter to data visuals
    data_visuals = [
        _compact_visual_list([v], compact=True)[0]
        for v in visuals
        if _is_data_visual(_compact_visual_list([v], compact=True)[0])
    ]

    if not data_visuals:
        return {
            "success": True,
            "page": page_name,
            "status": "no_data_visuals",
            "total_visuals": len(visuals),
            "data_visuals": 0,
        }

    # Audit each visual
    tallies = {"ok": 0, "error": 0, "empty": 0, "skipped": 0}
    flags = []
    visual_results = []

    from server.request_context import raise_if_cancelled

    for visual in data_visuals:
        raise_if_cancelled()
        vr = _audit_build_and_execute(builder, qe, page_name, visual, max_rows)

        if not include_data and "data" in vr:
            del vr["data"]

        visual_results.append(vr)

        status = vr.get("status", "error")
        if status in ("ok", "ok_retry"):
            tallies["ok"] += 1
        elif status in ("empty", "empty_retry", "all_null"):
            tallies["empty"] += 1
        elif status == "skipped":
            tallies["skipped"] += 1
        else:
            tallies["error"] += 1
            flags.append(
                {
                    "visual": vr.get("visual_name"),
                    "type": "query_error",
                    "detail": vr.get("error", "")[:200],
                }
            )

        if vr.get("anomalies"):
            flags.append(
                {
                    "visual": vr.get("visual_name"),
                    "type": "anomaly",
                    "detail": vr["anomalies"],
                }
            )

        if vr.get("flag"):
            flags.append(
                {
                    "visual": vr.get("visual_name"),
                    "type": vr.get("status"),
                    "detail": vr["flag"],
                }
            )

    # Cross-visual math checks
    math_checks = _audit_check_math(visual_results)
    if math_checks:
        flags.extend(math_checks)

    elapsed = round(time.time() - start_time, 1)

    return {
        "success": True,
        "page": page_name,
        "status": "audited",
        "visuals_total": len(data_visuals),
        "visuals_ok": tallies["ok"],
        "visuals_error": tallies["error"],
        "visuals_empty": tallies["empty"],
        "visuals_skipped": tallies["skipped"],
        "flags": flags,
        "visuals": visual_results,
        "elapsed_seconds": elapsed,
    }


def handle_audit(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Audit operation: automated visual audit.

    Three modes:
    1. **page_name** (single string): Audit ONE page, return full visual details
       inline. Designed for page-by-page iteration to avoid context overload.
    2. **pages** (array): Audit multiple pages, save full report to file,
       return compact summary inline.
    3. **Neither**: Discovery mode — return list of all pages with visual counts
       so the caller can iterate with page_name.

    Parameters:
        page_name: single page to audit (returns full inline results)
        pages: list of page names to audit (batch mode, saves to file)
        max_rows: max rows per visual query (default: 20)
        skip_types: visual types to skip (extends defaults)
        include_data: include raw row data in output (default: false, saves tokens)
    """
    try:
        from server.handlers._debug.helpers import _get_visual_query_builder, _compact_visual_list

        builder, error = _get_visual_query_builder()
        if error:
            return error_envelope("operation_failed", error)

        if not connection_state.is_connected():
            return error_envelope("not_connected", "Not connected to Power BI Desktop")

        qe = connection_state.query_executor
        if not qe:
            return error_envelope("manager_unavailable", "Query executor not available")

        # --- Mode 1: Single page audit (page_name) ---
        single_page = args.get("page_name")
        if single_page:
            # Validate page exists
            all_pages = builder.list_pages()
            page_match = [p for p in all_pages if p["name"].lower() == single_page.lower()]
            if not page_match:
                return error_envelope(
                    "not_found",
                    f"Page '{single_page}' not found",
                    available_pages=[p["name"] for p in all_pages],
                )
            return _audit_single_page(builder, qe, page_match[0]["name"], args)

        # --- Mode 3: Discovery mode (no page_name, no pages) ---
        target_pages = args.get("pages", [])
        all_pages = builder.list_pages()

        if not target_pages:
            # Return page inventory with visual counts for caller to iterate
            skip_types = set(args.get("skip_types", []))
            skip_types.update(_AUDIT_SKIP_TYPES)
            page_inventory = []
            for page_info in all_pages:
                pname = page_info["name"]
                try:
                    visuals = builder.list_visuals(pname)
                    data_visuals = [
                        v
                        for v in visuals
                        if _is_data_visual(_compact_visual_list([v], compact=True)[0])
                    ]
                    page_inventory.append(
                        {
                            "page": pname,
                            "total_visuals": len(visuals),
                            "data_visuals": len(data_visuals),
                        }
                    )
                except Exception as e:
                    page_inventory.append(
                        {
                            "page": pname,
                            "error": str(e)[:200],
                        }
                    )
            return {
                "success": True,
                "mode": "discovery",
                "total_pages": len(all_pages),
                "pages": page_inventory,
                "hint": "Use page_name='<name>' to audit one page at a time with full inline results.",
            }

        # --- Mode 2: Batch audit (pages array) ---
        target_lower = {p.lower() for p in target_pages}
        pages_to_audit = [p for p in all_pages if p["name"].lower() in target_lower]

        if not pages_to_audit:
            return error_envelope(
                "not_found",
                "No pages found to audit",
                available_pages=[p["name"] for p in all_pages],
            )

        # Run audit
        audit_report = {
            "success": True,
            "pages_audited": 0,
            "visuals_audited": 0,
            "visuals_ok": 0,
            "visuals_error": 0,
            "visuals_empty": 0,
            "visuals_skipped": 0,
            "flags": [],
            "page_results": {},
        }

        start_time = time.time()

        for page_info in pages_to_audit:
            page_name = page_info["name"]
            page_result = _audit_single_page(builder, qe, page_name, args)

            if page_result.get("status") == "audited":
                audit_report["visuals_audited"] += page_result.get("visuals_total", 0)
                audit_report["visuals_ok"] += page_result.get("visuals_ok", 0)
                audit_report["visuals_error"] += page_result.get("visuals_error", 0)
                audit_report["visuals_empty"] += page_result.get("visuals_empty", 0)
                audit_report["visuals_skipped"] += page_result.get("visuals_skipped", 0)
                for flag in page_result.get("flags", []):
                    flag["page"] = page_name
                    audit_report["flags"].append(flag)

            audit_report["page_results"][page_name] = page_result
            audit_report["pages_audited"] += 1

        elapsed = round(time.time() - start_time, 1)
        audit_report["elapsed_seconds"] = elapsed
        audit_report["summary"] = (
            f"Audited {audit_report['pages_audited']} pages, "
            f"{audit_report['visuals_audited']} visuals in {elapsed}s. "
            f"OK: {audit_report['visuals_ok']}, "
            f"Empty: {audit_report['visuals_empty']}, "
            f"Errors: {audit_report['visuals_error']}, "
            f"Skipped: {audit_report['visuals_skipped']}, "
            f"Flags: {len(audit_report['flags'])}"
        )

        # Save full report to file (likely too large for inline response)
        try:
            output_dir = Path(__file__).parent.parent.parent.parent / "output"
            output_dir.mkdir(exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            output_file = output_dir / f"audit_report_{timestamp}.json"
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(audit_report, f, indent=2, default=str)
            audit_report["output_file"] = str(output_file)
        except Exception as fe:
            logger.warning(f"Could not save audit report to file: {fe}")

        # Return compact summary inline — strip page_results for token savings
        inline_response = {k: v for k, v in audit_report.items() if k != "page_results"}
        inline_response["page_summary"] = {
            page: {
                "status": pr.get("status"),
                "visuals_total": pr.get("visuals_total", 0),
                "ok": pr.get("visuals_ok", 0),
                "errors": pr.get("visuals_error", 0),
                "empty": pr.get("visuals_empty", 0),
            }
            for page, pr in audit_report.get("page_results", {}).items()
        }

        return inline_response

    except Exception as e:
        logger.error(f"Error in handle_audit: {e}", exc_info=True)
        return ErrorHandler.handle_unexpected_error("audit", e)


def handle_debug_loop(args: Dict[str, Any]) -> Dict[str, Any]:
    """Run the closed-loop debug cycle: read filters -> run DAX -> detect
    anomaly -> propose fix -> apply -> 17_Desktop_Bridge reload + screenshot
    (in-place, no close/reopen) -> re-debug, until resolved or max_iterations.

    fix_strategy='live_tom' passes the destructive-op gate inside the loop
    (review J-09); allow_llm_fix additionally needs confirm_destructive=true
    under WARN / STRICT. There is no separate mode to enter.
    """

    def _flag(key: str, default: bool) -> bool:
        # J-09: bool("false") is True — coerce string booleans explicitly.
        value = args.get(key, default)
        if value is None:
            return default
        if isinstance(value, str):
            return value.strip().lower() not in ("false", "0", "no", "off", "")
        return bool(value)

    pbip_path = args.get("pbip_path") or connection_state.get_pbip_folder_path()
    # L-11: canonical page_name / visual_id / measure_name (legacy page / visual /
    # measure are folded in by the dispatcher; direct callers still work).
    page = args.get("page_name") or args.get("page")
    visual = args.get("visual_id") or args.get("visual")
    if not pbip_path or not page or not visual:
        return {
            "success": False,
            "error": "pbip_path, page_name and visual_id are all required for debug_loop",
            "error_type": "missing_args",
        }

    try:
        from core.autonomous.loops.debug_loop import DebugLoopConfig, run_debug_loop
    except Exception as e:  # noqa: BLE001
        return error_envelope("manager_unavailable", f"debug_loop module unavailable: {e}")

    # Boundary-safe coercion — bare int()/float() raise ValueError on
    # hostile input (e.g. max_iterations="abc"), bubbling out as a 500.
    try:
        max_iters_raw = args.get("max_iterations", 5)
        max_iters = int(float(max_iters_raw)) if max_iters_raw is not None else 5
    except (TypeError, ValueError):
        return {
            "success": False,
            "error": f"max_iterations must be numeric, got {max_iters_raw!r}",
            "error_type": "invalid_max_iterations",
        }
    try:
        wait_timeout_raw = args.get("wait_timeout_seconds", 180.0)
        wait_timeout = float(wait_timeout_raw) if wait_timeout_raw is not None else 180.0
    except (TypeError, ValueError):
        return {
            "success": False,
            "error": f"wait_timeout_seconds must be numeric, got {wait_timeout_raw!r}",
            "error_type": "invalid_wait_timeout_seconds",
        }

    cfg = DebugLoopConfig(
        pbip_path=pbip_path,
        page=str(page),
        visual=str(visual),
        measure=args.get("measure_name") or args.get("measure"),
        expected=args.get("expected"),
        max_iterations=max_iters,
        take_screenshot=_flag("take_screenshot", True),
        trace=_flag("trace", True),
        fix_strategy=str(args.get("fix_strategy", "ask_user")),
        allow_llm_fix=_flag("allow_llm_fix", False),
        confirm_destructive=args.get("confirm_destructive") is True,
        screenshot_dir=args.get("screenshot_dir"),
        wait_timeout_seconds=wait_timeout,
    )

    try:
        return run_debug_loop(cfg, connection_state=connection_state)
    except Exception as e:  # noqa: BLE001
        logger.error("debug_loop crashed: %s", e, exc_info=True)
        return error_envelope("operation_failed", f"debug_loop crashed: {e}")
