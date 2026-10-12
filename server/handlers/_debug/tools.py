"""
09_Validate / 09_Profile / 09_Document handlers.

Extracted from debug_handler.py as part of P2-183 split. Each handler
is a thin dispatcher into core.debug.debug_operations.DebugOperations.

Public entry points (imported back into debug_handler for registration
and back-compat):

- handle_validate
- handle_profile
- handle_document
- _get_debug_operations
"""

import json
import logging
from typing import Any, Dict, Optional

from core.infrastructure.connection_state import connection_state
from core.validation.error_handler import ErrorHandler
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)


def _get_debug_operations():
    """Get DebugOperations instance with builder and query executor."""
    # Imported lazily to avoid a circular import at module load — helpers
    # sit in the parent debug_handler module.
    from server.handlers._debug.helpers import _get_visual_query_builder

    builder, error = _get_visual_query_builder()
    if error:
        return None, error

    qe = connection_state.query_executor if connection_state.is_connected() else None

    if qe:
        builder.load_column_types(qe)

    try:
        from core.debug.debug_operations import DebugOperations

        return DebugOperations(builder, qe), None
    except Exception as e:
        return None, f"Error initializing DebugOperations: {e}"


def handle_validate(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Consolidated validation operations for visual debugging.

    Operations:
    - cross_visual: Compare same measure across multiple visuals
    - expected_value: Assert visual returns expected value
    - filter_permutation: Test visual with different slicer combinations
    - tieout: parent == sum(children) of a matrix/table visual, page_vs_page, A_vs_B (F-09)
    - test_generate: generate suite tests from a page / report / roles / measures (F-10)
    - equivalence: two DAX expressions over a deterministic context grid (F-14)
    """
    try:
        operation = args.get("operation", "cross_visual")

        # Baseline ops talk to the executor directly (no visual layer needed).
        if operation == "capture_baseline":
            return _capture_baseline(args)
        if operation == "compare_baseline":
            return _compare_baseline(args)
        if operation in _SUITE_OPS:
            return _handle_suite(operation, args)
        if operation == "tieout":
            return _handle_tieout(args)
        if operation == "test_generate":
            return _handle_test_generate(args)
        if operation == "equivalence":
            return _handle_equivalence(args)

        ops, error = _get_debug_operations()
        if error:
            return error_envelope("operation_failed", error)

        if operation == "cross_visual":
            measure_name = args.get("measure_name")
            if not measure_name:
                return error_envelope(
                    "missing_argument",
                    "measure_name is required for cross_visual validation",
                )
            return ops.cross_visual_validation(
                measure_name=measure_name,
                page_names=args.get("pages") or args.get("page_names"),
                tolerance=args.get("tolerance", 0.001),
            )

        elif operation == "expected_value":
            page_name = args.get("page_name")
            if not page_name:
                return error_envelope(
                    "missing_argument",
                    "page_name is required for expected_value test",
                )
            return ops.expected_value_test(
                page_name=page_name,
                visual_id=args.get("visual_id"),
                visual_name=args.get("visual_name"),
                expected_value=args.get("expected_value"),
                filters=args.get("filters"),
                tolerance=args.get("tolerance", 0.001),
            )

        elif operation == "filter_permutation":
            page_name = args.get("page_name")
            if not page_name:
                return error_envelope(
                    "missing_argument",
                    "page_name is required for filter_permutation test",
                )
            return ops.filter_permutation_test(
                page_name=page_name,
                visual_id=args.get("visual_id"),
                visual_name=args.get("visual_name"),
                max_permutations=args.get("max_permutations", 20),
            )

        else:
            return error_envelope(
                "invalid_operation",
                f"Unknown operation: {operation}",
                available_operations=[
                    "cross_visual",
                    "expected_value",
                    "filter_permutation",
                    "capture_baseline",
                    "compare_baseline",
                    *_SUITE_OPS,
                    "tieout",
                    "test_generate",
                    "equivalence",
                ],
            )

    except Exception as e:
        logger.error(f"Error in validate: {e}", exc_info=True)
        return ErrorHandler.handle_unexpected_error("validate", e)


# ---------------------------------------------------------------------------
# Persisted test suites (TEST-01): core/validation/test_suites.py
# ---------------------------------------------------------------------------

_SUITE_OPS = ("test_put", "test_remove", "test_list", "test_run", "test_export")


def _suite_query(query: str) -> Dict[str, Any]:
    qe = connection_state.query_executor
    if qe is None:
        return {
            "success": False,
            "error": "Not connected (no query engine)",
            "error_type": "not_connected",
        }
    return qe.validate_and_execute_dax(query, bypass_cache=True, max_rows=10000)


def _suite_query_as_role(query: str, roles, upn) -> Dict[str, Any]:
    from core.infrastructure.role_impersonation import run_as_role

    return run_as_role(dax_query=query, roles=list(roles), effective_user_name=upn, top_n=10000)


def _no_engine() -> Dict[str, Any]:
    """Not connected - or on the offline lane, which has TOM but no DAX engine (F-11)."""
    try:
        from core.infrastructure import offline_model

        if offline_model.is_active():
            return {
                "success": False,
                "error": "The offline folder lane has no DAX engine: test_run / test_export need Desktop or "
                "connect_xmla (test_list / test_put work offline).",
                "error_type": "no_dax_engine",
            }
    except Exception:  # noqa: BLE001
        pass
    return {"success": False, "error": "Not connected", "error_type": "not_connected"}


def _suite_rls_audit(test: Dict[str, Any]) -> Dict[str, Any]:
    from core.validation.rls_audit import audit_from_connection

    return audit_from_connection(
        {
            "roles": test.get("roles"),
            "as_user": test.get("as_user"),
            "probe_identities": test.get("probe_identities"),
            "custom_data": test.get("custom_data"),
        }
    )


def _validate_builder():
    """``(builder, error)`` - the PBIP visual query builder with column types loaded."""
    from server.handlers._debug.helpers import _get_visual_query_builder

    builder, error = _get_visual_query_builder()
    if error or builder is None:
        return None, error or "PBIP path not set (09_Debug_Operations set_path)"
    qe = connection_state.query_executor if connection_state.is_connected() else None
    if qe:
        builder.load_column_types(qe)
    return builder, None


def _suite_tieout(test: Dict[str, Any]) -> Dict[str, Any]:
    from core.validation.tieout import run_tieout

    builder = None
    if test.get("mode") != "A_vs_B" and not test.get("measure_a"):
        builder, error = _validate_builder()
        if error:
            return {"success": False, "error": str(error), "error_type": "pbip_path_required"}
    return run_tieout(test, builder=builder, run_query=_suite_query)


def _handle_suite(operation: str, args: Dict[str, Any]) -> Dict[str, Any]:
    from core.validation import test_suites as ts

    path = args.get("suite_path")
    if not path:
        return error_envelope(
            "missing_argument", "suite_path is required (a .json suite file; created by test_put)"
        )
    # N-19: suite / export files go through the repo path guard like every other file-writing tool.
    from core.validation.path_guard import guard_or_error

    safe_suite, guard_err = guard_or_error(str(path), "suite_path")
    if guard_err:
        return guard_err
    path = str(safe_suite)
    try:
        if operation == "test_put":
            test = args.get("test")
            if not isinstance(test, dict):
                return error_envelope(
                    "missing_argument", "test (object: id, type, ...) is required"
                )
            return ts.put_test(path, test, replace=args.get("replace", True) is not False)
        if operation == "test_remove":
            if not args.get("test_id"):
                return error_envelope("missing_argument", "test_id is required")
            return ts.remove_test(path, str(args["test_id"]))
        if operation == "test_list":
            return ts.list_tests(path)
        # test_run / test_export need a live engine
        if not connection_state.is_connected() or connection_state.query_executor is None:
            return _no_engine()
        summary = ts.run_suite(
            path,
            query=_suite_query,
            query_as_role=_suite_query_as_role,
            compare_baseline=_compare_baseline,
            rls_audit=_suite_rls_audit,
            tieout=_suite_tieout,
            ids=args.get("ids"),
            tags=args.get("tags"),
            record=args.get("record", True) is not False,
        )
        if operation == "test_export" or args.get("output_path"):
            out = args.get("output_path")
            if not out:
                return error_envelope("missing_argument", "output_path is required for test_export")
            safe_out, guard_err = guard_or_error(str(out), "output_path")
            if guard_err:
                summary["export"] = guard_err
                return summary
            out = str(safe_out)
            summary["export"] = ts.export_results(
                summary, out, args.get("output_format") or "junit"
            )
        return summary
    except ts.SuiteNotFound as e:
        return error_envelope("not_found", str(e))
    except ts.SuiteError as e:
        return error_envelope("invalid_parameters", str(e))


# ---------------------------------------------------------------------------
# tieout (F-09) / test_generate (F-10) / equivalence (F-14): read-only engines in core/validation
# ---------------------------------------------------------------------------


def _handle_tieout(args: Dict[str, Any]) -> Dict[str, Any]:
    from core.validation.tieout import run_tieout

    if not connection_state.is_connected() or connection_state.query_executor is None:
        return _no_engine()
    builder = None
    if args.get("mode") != "A_vs_B" and not args.get("measure_a"):
        builder, error = _validate_builder()
        if error:
            return error_envelope("operation_failed", str(error))
    return run_tieout(args, builder=builder, run_query=_suite_query)


def _model_role_names() -> list:
    rls_mgr = getattr(connection_state, "rls_manager", None)
    if rls_mgr is None:
        return []
    listed = rls_mgr.list_roles()
    if not listed.get("success", True):
        return []
    return [r.get("name") for r in (listed.get("roles") or []) if r.get("name")]


def _handle_test_generate(args: Dict[str, Any]) -> Dict[str, Any]:
    from core.validation import test_generator as tg
    from core.validation import test_suites as ts
    from core.validation.path_guard import guard_or_error

    if not connection_state.is_connected() or connection_state.query_executor is None:
        return _no_engine()
    source = args.get("source")
    if source not in tg.SOURCES:
        return error_envelope("missing_argument", f"source is required: one of {list(tg.SOURCES)}")
    write = args.get("write") is True
    path = args.get("suite_path")
    if write and not path:
        return error_envelope("missing_argument", "suite_path is required with write=true")
    if path:
        safe, guard_err = guard_or_error(str(path), "suite_path")
        if guard_err:
            return guard_err
        path = str(safe)
    builder = None
    if source in ("page", "report"):
        builder, error = _validate_builder()
        if error:
            return error_envelope("operation_failed", str(error))
    roles = list(args.get("roles") or [])
    if source in ("roles", "report") and not roles:
        roles = _model_role_names()
    try:
        gen = tg.generate(
            source,
            builder=builder,
            run_query=_suite_query,
            page_name=args.get("page_name"),
            measures=list(args.get("measures") or []),
            roles=roles,
            as_user=args.get("as_user"),
            tolerance=float(args.get("tolerance", 0.001)),
            snapshot_rows=int(args.get("snapshot_rows") or tg.DEFAULT_SNAPSHOT_ROWS),
            include_tieout=args.get("include_tieout", True) is not False,
            include_perf=args.get("include_perf") is True,
            runs=int(args.get("runs") or 3),
            headroom=float(args.get("headroom") or 1.5),
            heavy_ms=float(args.get("heavy_ms") or 500),
            max_visuals=int(args.get("max_visuals") or tg.DEFAULT_MAX_VISUALS),
        )
        if not gen.get("success"):
            return gen
        suite_tests = ts.load_suite(path)["tests"] if path else []
        diff = tg.diff_against_suite(gen["tests"], suite_tests)
        out: Dict[str, Any] = {
            "success": True,
            "source": source,
            "preview": not write,
            "suite_path": path,
            "generated": len(gen["tests"]),
            "diff": diff,
            "skipped": gen["skipped"],
            "notes": gen["notes"],
            "provenance_note": "provenance=snapshot tests assert today's values (bugs included); "
            "invariant / audit tests assert a property.",
        }
        if not write:
            out["tests"] = gen["tests"]
            out["write_hint"] = (
                "re-call with write=true and suite_path to persist "
                "(accept_changes=true overwrites changed expectations)"
            )
            return out
        to_write, held = tg.select_for_write(
            gen["tests"], diff, accept_changes=args.get("accept_changes") is True
        )
        written = ts.put_tests(path, to_write)
        out.update(
            {
                "written": {k: written[k] for k in ("added", "replaced", "test_count")},
                "held_for_review": held,
                "test_ids": [t["id"] for t in gen["tests"]],
            }
        )
        return out
    except ts.SuiteError as e:
        return error_envelope("invalid_parameters", str(e))


def _resolve_host_table() -> Optional[str]:
    """Any table of the model can host the query-scoped ``DEFINE MEASURE`` (never written)."""
    try:
        from core.dax.info_view import info_view

        res = info_view("tables", top_n=500)
        rows = (res.get("rows") or []) if isinstance(res, dict) else []
        visible = [r for r in rows if not r.get("IsHidden") and r.get("Name")]
        for r in visible or [r for r in rows if r.get("Name")]:
            return str(r["Name"])
    except Exception:  # noqa: BLE001
        pass
    return None


def _handle_equivalence(args: Dict[str, Any]) -> Dict[str, Any]:
    from core.dax.dax_names import column_ref
    from core.validation import equivalence_grid as eg

    if not connection_state.is_connected() or connection_state.query_executor is None:
        return _no_engine()
    expr_a, expr_b, err = eg.resolve_expressions(args)
    if err:
        return error_envelope("missing_argument", err)
    grid_columns = list(args.get("grid_columns") or [])
    if not grid_columns and args.get("page_name"):
        builder, error = _validate_builder()
        if error:
            return error_envelope("operation_failed", str(error))
        grid_columns = [
            column_ref(s.table, s.column) for s in (builder.list_slicers(args["page_name"]) or [])
        ]
    host = args.get("host_table") or _resolve_host_table()
    if not host:
        return error_envelope(
            "missing_argument", "host_table is required (any table name; the model is not written)"
        )
    return eg.run_equivalence(
        _suite_query,
        expression_a=expr_a,
        expression_b=expr_b,
        host_table=str(host),
        grid_columns=grid_columns,
        date_column=args.get("date_column"),
        grains=list(args.get("grains") or []),
        filters=list(args.get("filters") or []),
        max_cells=int(args.get("max_cells") or eg.DEFAULT_MAX_CELLS),
        tolerance=float(args.get("tolerance", eg.DEFAULT_TOLERANCE)),
    )


# ---------------------------------------------------------------------------
# Baseline capture / compare (Phase 5): regression guard around model edits.
# ---------------------------------------------------------------------------


def _measure_query_ref(measure_name: str) -> str:
    """``Total Sales`` -> ``[Total Sales]``; ``Sales[Total]`` / ``'Sales'[Total]`` kept
    table-qualified; ``]`` escaped (F-37)."""
    from core.dax.dax_names import bracket, column_ref, split_qualified, unbracket

    name = (measure_name or "").strip()
    if name.startswith("[") and name.endswith("]"):
        return bracket(unbracket(name))
    if "[" in name and name.endswith("]"):
        table, col = split_qualified(name)
        if table and col:
            return column_ref(table, col)
    return bracket(name)


def _eval_measure_scalar(measure_name: str, cache_info: Optional[Dict[str, Any]] = None):
    """Evaluate a measure to a scalar via ROW(). Returns (ok, value_or_error).

    F-01: always a real round-trip (``bypass_cache=True``) -- the regression
    guard must never compare a baseline against a replay of itself. The
    executor's cache provenance is written to *cache_info* when given.
    """
    qe = connection_state.query_executor
    if not qe:
        return False, "query_executor unavailable"
    query = f'EVALUATE ROW("v", {_measure_query_ref(measure_name)})'
    res = qe.validate_and_execute_dax(query, bypass_cache=True)
    if cache_info is not None and isinstance(res, dict):
        cache_info[measure_name] = res.get("cache")
    if not res.get("success"):
        return False, res.get("error", "query failed")
    rows = res.get("rows") or []
    if not rows:
        return True, None
    first = rows[0]
    # The single cell — tolerate either the aliased key or the only value.
    if "v" in first:
        return True, first["v"]
    if "[v]" in first:
        return True, first["[v]"]
    return True, next(iter(first.values()), None)


def _capture_baseline(args: Dict[str, Any]) -> Dict[str, Any]:
    """Snapshot the scalar value of each measure to a JSON baseline file."""
    if not connection_state.is_connected():
        return {"success": False, "error": "Not connected", "error_type": "not_connected"}

    measures = args.get("measures") or []
    baseline_path = args.get("baseline_path")
    if not measures:
        return error_envelope("missing_argument", "measures (list) is required")
    if not baseline_path:
        return error_envelope("missing_argument", "baseline_path is required")

    results: Dict[str, Any] = {}
    errors: Dict[str, str] = {}
    cache_info: Dict[str, Any] = {}
    for m in measures:
        ok, val = _eval_measure_scalar(m, cache_info)
        if ok:
            results[m] = val
        else:
            errors[m] = val

    baseline = {
        "version": 2,
        "measures": list(measures),
        "results": results,
        "errors": errors,
        "captured_at": _utc_now(),
        "model_generation": getattr(connection_state, "model_generation", None),
    }
    try:
        import os

        parent = os.path.dirname(os.path.abspath(baseline_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(baseline_path, "w", encoding="utf-8") as fh:
            json.dump(baseline, fh, separators=(",", ":"), default=str)
    except OSError as e:
        return {
            "success": False,
            "error": f"Failed to write baseline: {e}",
            "error_type": "write_error",
        }

    return {
        "success": True,
        "captured": len(results),
        "baseline_path": baseline_path,
        "errors": errors or None,
        "cache_bypassed": True,
    }


def _compare_baseline(args: Dict[str, Any]) -> Dict[str, Any]:
    """Re-run the baseline's measures and diff against the stored values."""
    if not connection_state.is_connected():
        return {"success": False, "error": "Not connected", "error_type": "not_connected"}

    baseline_path = args.get("baseline_path")
    tolerance = float(args.get("tolerance", 0.001))
    if not baseline_path:
        return error_envelope("missing_argument", "baseline_path is required")

    try:
        with open(baseline_path, "r", encoding="utf-8") as fh:
            baseline = json.load(fh)
    except (OSError, ValueError) as e:
        return {
            "success": False,
            "error": f"Failed to read baseline: {e}",
            "error_type": "read_error",
        }

    stored = baseline.get("results", {}) or {}
    stored_errors = baseline.get("errors", {}) or {}
    # F-37: iterate the baseline's MEASURES, not just the ones that evaluated --
    # a measure that errored at capture must still be reported.
    measures = list(baseline.get("measures") or []) or list(stored)
    for m in stored:
        if m not in measures:
            measures.append(m)
    differences = []
    unchanged = 0
    cache_info: Dict[str, Any] = {}
    for measure in measures:
        ok, new_val = _eval_measure_scalar(measure, cache_info)
        if measure not in stored:
            entry = {
                "measure": measure,
                "baseline_error": stored_errors.get(measure, "not captured"),
            }
            if ok:
                entry["current"] = new_val
                entry["note"] = "errored at capture, evaluates now"
            else:
                entry["error"] = new_val
            differences.append(entry)
            continue
        old_val = stored[measure]
        if not ok:
            differences.append({"measure": measure, "baseline": old_val, "error": new_val})
            continue
        if _values_differ(old_val, new_val, tolerance):
            differences.append({"measure": measure, "baseline": old_val, "current": new_val})
        else:
            unchanged += 1

    cache_hits = [m for m, c in cache_info.items() if isinstance(c, dict) and c.get("hit")]
    return {
        "success": True,
        "changed": len(differences),
        "unchanged": unchanged,
        "tolerance": tolerance,
        "differences": differences,
        "baseline_captured_at": baseline.get("captured_at"),
        "cache_bypassed": True,
        # Must stay empty: every value above is a fresh engine round-trip.
        "cache_hits": cache_hits,
    }


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _values_differ(old, new, tolerance: float) -> bool:
    """Numeric comparison within tolerance; exact for non-numeric (F-37).

    BLANK vs a number differs; NaN vs a number differs; NaN vs NaN is equal.
    """
    import math

    if old is None or new is None:
        return (old is None) != (new is None)
    if isinstance(old, bool) or isinstance(new, bool):
        return old != new
    try:
        a, b = float(old), float(new)
    except (TypeError, ValueError):
        return old != new
    if math.isnan(a) or math.isnan(b):
        return not (math.isnan(a) and math.isnan(b))
    return abs(a - b) > tolerance


def _perf_project(args: Dict[str, Any]) -> str:
    """History project name: explicit ``project``, else the PBIP folder's name."""
    from core.debug.perf_history import project_key

    explicit = args.get("project")
    if explicit:
        return project_key(str(explicit))
    try:
        return project_key(connection_state.get_pbip_folder_path())
    except Exception:  # noqa: BLE001
        return project_key(None)


def _record_profile(result: Dict[str, Any], args: Dict[str, Any]) -> None:
    """F-18: ``record=true`` appends the page profile to ``output/perf/<project>.jsonl``."""
    from core.debug import perf_history

    try:
        from core.config.config_manager import config

        read_only = bool(config.get("security.read_only_mode", False))
    except Exception:  # noqa: BLE001
        read_only = False
    if read_only:
        result["recorded"] = {
            "success": False,
            "error_type": "read_only_mode",
            "error": "record=true writes output/perf/<project>.jsonl, which security.read_only_mode forbids.",
        }
        return
    pbip = None
    try:
        pbip = connection_state.get_pbip_folder_path()
    except Exception:  # noqa: BLE001
        pass
    result["recorded"] = perf_history.record_profile(
        result,
        project=_perf_project(args),
        pbip_path=pbip,
        model_generation=getattr(connection_state, "model_generation", None),
        label=str(args["label"]) if args.get("label") else None,
    )


def handle_profile(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Consolidated profiling operations for performance analysis.

    Operations:
    - page: Profile all visuals on a page, rank by execution time (``record=true`` also appends
      the timings to the perf history, F-18)
    - filter_matrix: Test measure performance with different filter combinations
    - regress: compare the latest recorded run with ``baseline`` (label | git sha | date); refuses
      a verdict inside the noise band (F-18)
    """
    try:
        from server.handlers._debug.helpers import _get_visual_query_builder

        operation = args.get("operation", "page")
        page_name = args.get("page_name")

        # regress reads the recorded history only: no page, no connection.
        if operation == "regress":
            from core.debug import perf_history

            return perf_history.regress(
                _perf_project(args),
                str(args.get("baseline") or ""),
                candidate=args.get("candidate"),
                page_name=page_name,
            )

        # perf_analyzer_import reads an exported file, not a page — it must be
        # dispatched before the page_name guard below.
        if operation == "perf_analyzer_import":
            file_path = args.get("file_path")
            if not file_path:
                return error_envelope(
                    "missing_argument",
                    "file_path required: the PowerBIPerformanceData.json exported "
                    "from Power BI Desktop's Performance Analyzer pane",
                )
            ops, error = _get_debug_operations()
            if error:
                return error_envelope("operation_failed", error)
            return ops.perf_analyzer_import(
                file_path=file_path,
                trace=args.get("trace", False),
                pbip_path=args.get("pbip_path"),
            )

        if not page_name:
            builder, error = _get_visual_query_builder()
            if error:
                return error_envelope("operation_failed", error)
            pages = builder.list_pages()
            return error_envelope(
                "missing_argument",
                "page_name required",
                pages=[p.get("name") for p in pages],
            )

        ops, error = _get_debug_operations()
        if error:
            return error_envelope("operation_failed", error)

        if operation == "page":
            from pbi_perf_core.perf_analyzer import DEFAULT_QUERY_SLOTS

            profiled = ops.profile_page(
                page_name=page_name,
                iterations=args.get("iterations", 3),
                include_slicers=args.get("include_slicers", True),
                trace=args.get("trace", False),
                clear_cache=args.get("clear_cache", True),
                simulate_concurrency=args.get("simulate_concurrency", DEFAULT_QUERY_SLOTS),
                per_visual_limit=args.get("per_visual_limit"),
                schedule=str(args.get("schedule", "lpt") or "lpt"),
                warm_pass=bool(args.get("warm_pass", False)),
            )
            if (
                isinstance(profiled, dict)
                and profiled.get("success")
                and args.get("record") in (True, "true", "True", 1)
                and not profiled.get("cancelled")
            ):
                _record_profile(profiled, args)
            return profiled

        elif operation == "filter_matrix":
            return ops.filter_performance_matrix(
                page_name=page_name,
                visual_id=args.get("visual_id"),
                visual_name=args.get("visual_name"),
                filter_columns=args.get("filter_columns"),
                max_combinations=args.get("max_combinations", 15),
                include_slicers=args.get("include_slicers", True),
                iterations=args.get("iterations", 1),
            )

        elif operation == "decompose":
            if not page_name:
                return error_envelope("missing_argument", "page_name is required for decompose")
            return ops.decompose_value(
                page_name=page_name,
                visual_id=args.get("visual_id"),
                visual_name=args.get("visual_name"),
                dimension=args.get("dimension"),
                top_n=args.get("top_n", 10),
            )

        elif operation == "contribution":
            if not page_name:
                return error_envelope(
                    "missing_argument",
                    "page_name is required for contribution analysis",
                )
            return ops.contribution_analysis(
                page_name=page_name,
                visual_id=args.get("visual_id"),
                visual_name=args.get("visual_name"),
                dimension=args.get("dimension"),
                top_n=args.get("top_n", 10),
            )

        elif operation == "trend":
            if not page_name:
                return error_envelope(
                    "missing_argument",
                    "page_name is required for trend analysis",
                )
            return ops.trend_analysis(
                page_name=page_name,
                visual_id=args.get("visual_id"),
                visual_name=args.get("visual_name"),
                date_column=args.get("date_column"),
                granularity=args.get("granularity", "month"),
            )

        elif operation == "root_cause":
            if not page_name:
                return error_envelope(
                    "missing_argument",
                    "page_name is required for root_cause analysis",
                )
            return ops.root_cause_analysis(
                page_name=page_name,
                visual_id=args.get("visual_id"),
                visual_name=args.get("visual_name"),
                baseline_filters=args.get("baseline_filters"),
                comparison_filters=args.get("comparison_filters"),
                dimensions=args.get("dimensions"),
                top_n=args.get("top_n", 5),
            )

        elif operation == "export":
            return ops.export_debug_report(
                page_name=page_name,
                visual_id=args.get("visual_id"),
                visual_name=args.get("visual_name"),
                format=args.get("output_format") or args.get("format") or "markdown",
            )

        else:
            return error_envelope(
                "invalid_operation",
                f"Unknown operation: {operation}",
                available_operations=[
                    "page",
                    "filter_matrix",
                    "decompose",
                    "contribution",
                    "trend",
                    "root_cause",
                    "export",
                    "regress",
                ],
            )

    except Exception as e:
        logger.error(f"Error in profile: {e}", exc_info=True)
        return ErrorHandler.handle_unexpected_error("profile", e)


def _maybe_narrate(result: Dict[str, Any], operation: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Optionally attach a client-LLM-generated narrative to ``result``.

    Opt-in via ``narrate: true`` in args. Uses MCP sampling (server asks the
    client to call its LLM). Silently no-ops if sampling isn't available
    (client didn't declare the capability, or we're outside a request context).
    """
    if not args.get("narrate") or not isinstance(result, dict):
        return result
    if result.get("success") is False:
        return result
    try:
        import json as _json
        from server.sampling import sample_text

        system_prompt = (
            "You are a senior BI analyst writing concise, stakeholder-friendly "
            "summaries of Power BI report structure. Prefer bullet points, avoid "
            "technical jargon, keep output under 200 words."
        )
        payload = _json.dumps(
            {k: v for k, v in result.items() if not k.startswith("_")},
            default=str,
        )
        prompt = (
            f"Summarize this Power BI `{operation}` documentation output for a "
            f"non-technical stakeholder:\n\n```json\n{payload[:8000]}\n```\n\n"
            "Return a concise narrative summary — what the page/report does, "
            "what key measures are involved, any obvious risks or dependencies."
        )
        narrative = sample_text(prompt, system_prompt=system_prompt, max_tokens=800)
        if narrative:
            result["narrative"] = narrative
    except Exception as e:  # noqa: BLE001
        logger.debug("narrate hook failed: %s", e)
    return result


def handle_document(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Consolidated documentation operations.

    Operations:
    - page: Document all visuals on a page (data visuals only by default)
    - report: Document entire report
    - measure_lineage: Show which visuals use which measures
    - filter_lineage: Show which filters affect which visuals

    Pass ``narrate: true`` to request an LLM-generated narrative summary via
    MCP sampling (server asks the client to call its LLM). Silently falls back
    to the structured output alone if the client doesn't support sampling.
    """
    try:
        from server.handlers._debug.helpers import _get_visual_query_builder

        operation = args.get("operation", "page")
        lightweight = args.get("lightweight", True)
        include_ui_elements = args.get("include_ui_elements", False)

        ops, error = _get_debug_operations()
        if error:
            return error_envelope("operation_failed", error)

        if operation == "page":
            page_name = args.get("page_name")
            # P0-55 fix: previously if `builder` was None (not connected) the
            # `if not page_name` branch silently fell through to
            # `ops.document_page(None, ...)` which crashes. Always return the
            # "page_name required" error before attempting any document_page
            # call; fall back to a page listing only when the builder is
            # available.
            if not page_name:
                response = error_envelope(
                    "missing_argument",
                    "page_name required",
                )
                builder, _ = _get_visual_query_builder()
                if builder:
                    try:
                        pages = builder.list_pages()
                        response["pages"] = [p.get("name") for p in pages]
                    except Exception as e:  # noqa: BLE001 — listing is best-effort
                        logger.debug(f"Could not list pages for error response: {e}")
                return response
            result = ops.document_page(
                page_name, lightweight=lightweight, include_ui_elements=include_ui_elements
            )
            return _maybe_narrate(result, "page", args)

        elif operation == "report":
            result = ops.document_report(lightweight=lightweight)
            return _maybe_narrate(result, "report", args)

        elif operation == "measure_lineage":
            result = ops.measure_lineage(args.get("measure_name"))
            return _maybe_narrate(result, "measure_lineage", args)

        elif operation == "filter_lineage":
            result = ops.filter_lineage(args.get("page_name"))
            return _maybe_narrate(result, "filter_lineage", args)

        else:
            return error_envelope(
                "invalid_operation",
                f"Unknown operation: {operation}",
                available_operations=["page", "report", "measure_lineage", "filter_lineage"],
            )

    except Exception as e:
        logger.error(f"Error in document: {e}", exc_info=True)
        return ErrorHandler.handle_unexpected_error("document", e)
