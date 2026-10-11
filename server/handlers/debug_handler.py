"""
Debug Handler — thin dispatch + registration shell.

MCP tools for visual debugging, filter analysis, measure comparison,
validation, profiling, documentation, and the audit loop. Combines PBIP
analysis with live model query execution.

Implementation lives in server/handlers/_debug/:

- helpers.py   — VQB cache, compact/trace helpers, discover_visuals
- context.py   — _build_filter_context, _apply_manual_filters, _execute_visual_query
- visual.py    — handle_debug_visual, compare, drill, set_path, status
- analyze.py   — _handle_analyze + measure-centric helpers
- variables.py — debug_variable / step_variables / run_dax
- optimize.py  — _handle_optimize + diagnose_timing + suggestions
- audit.py     — handle_audit + handle_debug_loop
- tools.py     — handle_validate / handle_profile / handle_document

This file holds:

- All public re-exports so external importers (tests, autonomous loops,
  autonomous_handler) keep working without path changes.
- handle_debug_operations — dispatcher that maps `operation` strings to
  the sub-module handlers.
- register_debug_handlers — MCP registration of the 4 tool definitions
  (09_Debug_Operations, 09_Validate, 09_Profile, 09_Document).
"""

import logging
from typing import Any, Dict

# re-exported: tests patch `debug_handler.connection_state`
from core.infrastructure.connection_state import connection_state  # noqa: F401
from server.registry import ToolDefinition
from server.tool_schemas import TOOL_SCHEMAS

# --- Re-exports from sub-modules -----------------------------------------
# Everything below is what external code expects to import from
# `server.handlers.debug_handler`.  Keep these paths stable.

from server.handlers._debug.helpers import (  # noqa: F401, E402
    DRILL_DETAIL_LIMIT_MAX,
    DRILL_DETAIL_LIMIT_MIN,
    FILTER_TRUNCATION_CHARS,
    MAX_MEASURES_SHOWN,
    MAX_QUERY_RESULT_ROWS,
    PBIP_FRESHNESS_THRESHOLD_MINUTES,
    VARIABLE_TRUNCATION_CHARS,
    _build_trace_response,
    _check_pbip_freshness,
    _compact_filter_context,
    _compact_page_list,
    _compact_response,
    _compact_visual_list,
    _discover_visuals,
    _get_visual_query_builder,
    _VQB_CACHE_TTL,
    _vqb_cache,
    _vqb_cache_lock,
    bust_vqb_cache,
)
from server.handlers._debug.context import (  # noqa: F401, E402
    _apply_manual_filters,
    _build_filter_context,
    _execute_visual_query,
)
from server.handlers._debug.visual import (  # noqa: F401, E402
    _get_recommendations,
    handle_compare_measures,
    handle_debug_visual,
    handle_drill_to_detail,
    handle_get_debug_status,
    handle_set_pbip_path,
)
from server.handlers._debug.analyze import (  # noqa: F401, E402
    _analyze_measure_dax,
    _build_analyze_response,
    _execute_measure_with_context,
    _get_visual_filter_context_for_measure,
    _handle_analyze,
    _resolve_measure_expression,
)
from server.handlers._debug.variables import (  # noqa: F401, E402
    _build_var_chain_query,
    _classify_var_type,
    _extract_variables_full,
    _handle_debug_variable,
    _handle_run_dax,
    _handle_step_variables,
    _trace_measures_individually,
)
from server.handlers._debug.optimize import (  # noqa: F401, E402
    _build_next_steps,
    _build_optimize_suggestions,
    _diagnose_timing,
    _handle_optimize,
    handle_optimize_measure,
)
from server.handlers._debug.audit import (  # noqa: F401, E402
    MAX_AUDIT_ROWS,
    _AUDIT_SKIP_TYPES,
    _audit_build_and_execute,
    _audit_check_math,
    _audit_single_page,
    _is_data_visual,
    handle_audit,
    handle_debug_loop,
)
from server.handlers._debug.tools import (  # noqa: F401, E402
    _get_debug_operations,
    handle_document,
    handle_profile,
    handle_validate,
)

logger = logging.getLogger(__name__)


def handle_debug_operations(args: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch debug operations."""
    # Require explicit operation — the schema marks it required, so falling
    # back to a silent "visual" default would mask validation gaps when a
    # lenient client sends no operation at all.
    operation = args.get("operation")

    dispatch = {
        "visual": handle_debug_visual,
        "compare": handle_compare_measures,
        "drill": handle_drill_to_detail,
        "analyze": _handle_analyze,
        "debug_variable": _handle_debug_variable,
        "step_variables": _handle_step_variables,
        "run_dax": _handle_run_dax,
        "optimize": handle_optimize_measure,
        "audit": handle_audit,
        "set_path": handle_set_pbip_path,
        "status": handle_get_debug_status,
        "debug_loop": handle_debug_loop,
    }

    if not operation:
        return {
            "success": False,
            "error": (
                "operation is required for 09_Debug_Operations. "
                f"Valid: {', '.join(dispatch.keys())}."
            ),
            "error_type": "missing_operation",
        }

    handler = dispatch.get(operation)
    if handler:
        return handler(args)

    return {
        "success": False,
        "error": f"Unknown operation: {operation}. Valid: {', '.join(dispatch.keys())}",
        "error_type": "unknown_operation",
    }


def register_debug_handlers(registry):
    """Register debug handlers with the tool registry."""
    tools = [
        ToolDefinition(
            name="09_Debug_Operations",
            description=(
                "Debug a Power BI VISUAL - why is it blank / wrong / slow - from the PBIP "
                "layout (filters, slicers, bindings) plus live execution. PREFER this over "
                "07_Report_Operations info (static only). Ops: set_path (once; persists), "
                "status, visual (trace=true = SE/FE timings; measures[] override), compare "
                "(equivalence_check=true proves the same data; mode=live_swap = A/B in the "
                "real measure chain, restores + verifies), drill, analyze, debug_variable, "
                "step_variables, run_dax, optimize (trace_id from a trace), audit (no args = "
                "page list; page_name = one page; pages[] = batch), debug_loop. roles=[...] "
                "impersonates RLS."
            ),
            guide_notes="Debug a Power BI VISUAL — why is it blank/wrong/slow — by combining the PBIP page layout (filters, slicers, field bindings) with live model execution. ALWAYS PREFER THIS over 07_Report_Operations:info for debugging: that one returns static report structure only and cannot run the visual's query. Ops: visual, compare, drill, analyze, debug_variable, step_variables, run_dax, optimize, audit, debug_loop, set_path (set the PBIP path ONCE; it persists), status. visual: use trace=true for SE/FE timing, measures[] to override. compare: pass equivalence_check=true (with optional equivalence_grain) to prove the optimized expression returns the SAME data at a grain before trusting the perf delta — the result's comparison.performance_claim_valid is false when it doesn't. compare mode=live_swap (F-08) is the A/B on the visual's REAL measure chain: it checkpoints the live model (journal checkpoint_create), writes the candidate body (original_measure + optimized_expression [+ table_name], or patch=[{table_name?, measure_name, expression}]) into the live model, runs the visual's own query (page_name + visual_id/visual_name, or reference_query) cold `runs` times (default 3, max 10) interleaved with the baseline (A B | B A ...; one model write per side change, each journaled + paced), compares the rows at the visual's grain, then ALWAYS restores (checkpoint_restore preview first; direct write-back as fallback) and proves it: restore_verified (a failed proof is the restore_not_verified error naming the checkpoint). Verdict: faster | slower | noise | values_differ | inconclusive, with delta_ms / delta_pct / min_detectable_effect_pct (the larger of both groups' spread and 5%) on the fastest cold run of each side. dry_run=true plans without writing. Gated as a DESTRUCTIVE write (confirm_destructive under STRICT); needs a live Desktop model (refused on XMLA / offline-folder targets); it does not refuse on hasUnsavedChanges - the checkpoint and the restore proof protect other unsaved edits. optimize: requires timing from prior trace (fe_pct, se_queries, total_ms, fe_ms, se_ms; optional: se_events). audit: three modes — (1) no args: discovery, returns page list with visual counts; (2) page_name='X': audit ONE page, full inline results; (3) pages=['X','Y']: batch audit, compact summary + file. Use mode 2 to iterate page-by-page. RLS impersonation: pass roles=['RoleName', ...] (and optional effective_user_name on Service/SSAS) to visual/compare/drill/run_dax to see amounts as that role would see them.",
            handler=handle_debug_operations,
            input_schema=TOOL_SCHEMAS["debug_operations"],
            category="debug",
            sort_order=90,
            # P1-132 fix: previously advertised readOnlyHint=True but the
            # tool includes debug_loop (close+reopen + TMDL/live_tom fixes)
            # and set_path (mutates connection_state). Declare destructive
            # write surface honestly.
            annotations={
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
                "openWorldHint": True,
            },
        ),
        ToolDefinition(
            name="09_Validate",
            description=(
                "Assert correctness: cross_visual, expected_value (absolute tolerance), "
                "filter_permutation, capture_baseline / compare_baseline (regression guard "
                "around model edits), tieout (parent == sum of children; page_vs_page; "
                "A_vs_B), equivalence (two DAX expressions over a context grid), "
                "test_put/test_run/test_list/test_remove/test_export (CI suites in "
                "suite_path), test_generate (write suites from page/report/roles/measures). "
                "Only test_put/test_remove/test_generate write=true edit the suite JSON. "
                "Why a visual is wrong: 09_Debug_Operations; timings: 09_Profile."
            ),
            guide_notes=(
                "Validate / assert measure correctness against expected values "
                "and guard against regressions with baselines and tolerance. "
                "Ops: cross_visual (same measure consistent across visuals/"
                "pages), expected_value (measure == expected_value within "
                "tolerance), filter_permutation (test every filter combination), "
                "capture_baseline (snapshot measures[] scalars to baseline_path), "
                "compare_baseline (re-run + diff vs that baseline — the "
                "regression guard to wrap around model edits or 15_DAX_Refactor). "
                "Persisted test suites: test_put (add/replace one test in the JSON "
                "suite_path), test_remove, test_list, test_run (ids / tags filters; "
                "returns passed/failed/errors/skipped + exit_code 0/1 for CI, no_tests_ran + exit 1 "
                "when nothing ran; stores the last run in <suite>.last-run.json), test_export (output_path, output_format junit|json). Test "
                "types: measure_assertion (measure + filters == expected within "
                "tolerance), dax_assertion (query + expect: scalar / row_count / "
                "min_rows / max_rows / columns / rows / error), rls_validation (same "
                "under role), regression_snapshot (baseline_path), performance_budget "
                "(median of runs <= max_ms), metadata_compliance (no_measure_errors, "
                "measures_exist, measure_name_regex, descriptions_required), rls_audit "
                "(roles, as_user, fail_on, allow: the 04_Query_Operations rls_audit engine), "
                "tieout (mode, page_name + visual_id, non_additive, tolerance). "
                "tieout: runs a matrix/table visual's REAL query with its subtotal rows (row "
                "limit raised to max_rows) and checks parent == sum(children) at every level; "
                "reports the FIRST breaking level + measure + cause hint; non_additive "
                "measures are skipped; mode=page_vs_page (page_a, page_b, measure_name) / "
                "mode=A_vs_B (measure_a, measure_b, by, filters). test_generate: "
                "source=page (page_name) | report | roles | measures (measures[]) builds "
                "per-visual dax_assertion snapshots (fidelity recorded), tieouts, slicer-base "
                "checks, KPI measure_assertions, rls_audit per role, optional "
                "performance_budget (include_perf, headroom, heavy_ms); ids are stable so "
                "re-generating updates; PREVIEW + diff by default, write=true persists to "
                "suite_path, a changed expectation or a hand-written test with the same id "
                "is held back unless accept_changes=true; provenance=snapshot asserts "
                "today's numbers, bugs included. equivalence: expression_a/expression_b (or "
                "measure_name + candidate_expression) evaluated with a query-scoped DEFINE "
                "MEASURE on host_table over total + grid_columns (default the page's slicer "
                "columns) + date grains (date_column, grains) + explicit filters, capped at "
                "max_cells; a sampled grid, not a formal proof; the model is never written. "
                "tolerance is absolute, not a percentage. suite_path / output_path must sit under an "
                "allowed root (PBIP folder, exports/, output/, logs/). The model is never "
                "written: this ASSERTS "
                "values, it does not fix them — to inspect why a visual is wrong "
                "use 09_Debug_Operations, for timings use 09_Profile."
            ),
            handler=handle_validate,
            input_schema=TOOL_SCHEMAS["validate_operations"],
            category="debug",
            sort_order=92,
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
            },
        ),
        ToolDefinition(
            name="09_Profile",
            description=(
                "Profile page / visual PERFORMANCE and decompose measures. Ops: page (time "
                "every visual; trace=true = Performance-Analyzer-shaped table with an "
                "ESTIMATED 'Other' wait), filter_matrix, decompose, contribution, trend, "
                "root_cause, export, perf_analyzer_import (a Desktop "
                "PowerBIPerformanceData.json). Needs 09_Debug_Operations set_path first. Plan "
                "operators: 13_Query_Plan; one visual: 09_Debug_Operations visual trace=true."
            ),
            guide_notes=(
                "Profile page / visual PERFORMANCE (slow page, slow visual, "
                "which visual is expensive) and decompose a measure "
                "analytically. Ops: page (time every visual on a page, "
                "iterations=3 by default), filter_matrix (one visual across a "
                "filter matrix), decompose (split a measure by a dimension), "
                "contribution (contribution analysis), trend (by date_column + "
                "granularity), root_cause (baseline_filters vs "
                "comparison_filters), export (markdown|json), "
                "perf_analyzer_import (parse a PowerBIPerformanceData.json "
                "exported from Desktop's Performance Analyzer pane -> per-visual "
                "DAX / DirectQuery / Display / Other / Parameters). "
                "page with trace=true gives the Performance-Analyzer-shaped table: "
                "traced DAX ms per visual plus an ESTIMATED 'Other' wait from a "
                "K-slot queue model (simulate_concurrency, default 6 - Power BI "
                "publishes no concurrency limit). Needs a PBIP path "
                "set once via 09_Debug_Operations:set_path. For the plan operators "
                "and xmSQL behind one query use 13_Query_Plan, for a single "
                "visual's DAX + filter context use 09_Debug_Operations:visual "
                "with trace=true."
            ),
            handler=handle_profile,
            input_schema=TOOL_SCHEMAS["profile_operations"],
            category="debug",
            sort_order=93,
            annotations={
                # page record=true appends to the perf history file (op_classification: WRITE)
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
                "openWorldHint": True,
            },
        ),
        ToolDefinition(
            name="09_Document",
            description="Per-page / per-measure documentation from the LIVE model: page, report, measure_lineage, filter_lineage. Data visuals only by default. For full Word output use 08_Documentation_Word; for offline HTML from PBIP files use 07_PBIP_Operations operation=analyze, output_format=html.",
            handler=handle_document,
            input_schema=TOOL_SCHEMAS["document_operations"],
            category="debug",
            sort_order=94,
            annotations={
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        ),
    ]

    for tool in tools:
        registry.register(tool)

    logger.info(f"Registered {len(tools)} debug handlers")
