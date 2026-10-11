"""
Debug Operations

Consolidated operations for visual debugging, validation, profiling,
documentation, and advanced analysis.
"""

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from core.infrastructure.adomd_utils import engine_message
from core.utilities.progress_ticker import Ticker

# C-17: leaf definitions live in core.debug.debug_types (re-exported here).
from core.debug.debug_types import (  # noqa: F401
    LineageInfo,
    ProfileResult,
    ValidationResult,
    _first_value,
)
from core.debug.analysis_ops import DebugAnalysisMixin
from core.debug.document_ops import DebugDocumentMixin

try:
    from pbi_perf_core.perf_analyzer import DEFAULT_QUERY_SLOTS
except ImportError:  # sibling package: its absence must not take every 09_* op down (C-21)
    DEFAULT_QUERY_SLOTS = 6  # keep equal to pbi_perf_core.perf_analyzer.DEFAULT_QUERY_SLOTS

logger = logging.getLogger(__name__)


def _cache_info(exec_result: Dict[str, Any]) -> Dict[str, Any]:
    """Executor cache provenance (F-07): ``{hit, age_s}``."""
    cache = exec_result.get("cache") if isinstance(exec_result, dict) else None
    if not isinstance(cache, dict):
        return {"hit": False, "age_s": None}
    return {"hit": bool(cache.get("hit")), "age_s": cache.get("age_s")}


def _cancelled() -> bool:
    try:
        from server.request_context import is_cancelled

        return bool(is_cancelled())
    except Exception:  # pragma: no cover - server layer absent in some tests
        return False


def _tool_budget_s(tool_name: str, default: float = 180.0) -> float:
    """Wall-clock budget for a long 09_* loop: the tool timeout minus a margin (F-14)."""
    try:
        from core.config.config_manager import config
        from core.config.tool_timeouts import ToolTimeoutManager

        mgr = ToolTimeoutManager(config.get("tool_timeouts", {}) or {})
        timeout = float(mgr.get_timeout(tool_name, default=int(default)))
    except Exception:  # noqa: BLE001
        timeout = default
    return max(10.0, timeout - 15.0)


def _as_int(value: Any) -> Optional[int]:
    """Coerce a metric to int; None when it is missing or not numeric."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _traced_row_count(trace_result: Dict[str, Any], metrics: Dict[str, Any]) -> Optional[int]:
    """Rows the visual's query returned.

    ExecutionMetrics is authoritative when the engine emitted it; the result-set
    row counts are the fallback for the exe lane, which has no event 136.
    """
    rows = _as_int(metrics.get("result_rows"))
    if rows is not None:
        return rows
    result_sets = trace_result.get("result_sets") or []
    if not isinstance(result_sets, list) or not result_sets:
        return None
    return sum(int(s.get("row_count", 0) or 0) for s in result_sets if isinstance(s, dict))


def _simulate_layout(timings: List[Any], k_slots: int, layout_order: List[str]) -> Dict[str, Any]:
    """K-slot queue in PAGE LAYOUT order (tabOrder, then top-to-bottom) - F-33.

    ``pbi_perf_core.simulate_page`` schedules longest-first (LPT), which is an
    optimistic lower bound: a real page dispatches its visuals roughly in layout
    order, so a slow visual late in the order waits behind fast ones. Mutates the
    VisualTiming objects like simulate_page does.
    """
    k = max(int(k_slots), 1)
    rank = {vid: i for i, vid in enumerate(layout_order)}
    ordered = sorted(timings, key=lambda t: rank.get(t.visual_id, len(rank)))

    def _run(slots_n: int, items: List[Any], mutate: bool) -> float:
        slots = [0.0] * slots_n
        end_max = 0.0
        for t in items:
            index = min(range(slots_n), key=lambda i: slots[i])
            start = slots[index]
            end = start + t.dax_ms
            slots[index] = end
            if mutate:
                t.start_ms = start
                t.end_ms = end + t.display_ms
                t.other_ms = start
                t.total_ms = t.dax_ms + t.display_ms + t.other_ms
                t.estimated = True
            end_max = max(end_max, end + (t.display_ms if mutate else 0.0))
        return end_max

    est_page_ms = _run(k, ordered, True)
    serial_ms = sum(t.dax_ms for t in ordered)
    critical_path_ms = max((t.dax_ms + t.display_ms for t in ordered), default=0.0)
    total_wait = sum(t.other_ms for t in ordered)
    what_if: Dict[str, Any] = {
        f"k={alt}": round(_run(alt, ordered, False), 1)
        for alt in sorted({1, 2, 4, 6, 8, max(1, k * 2)})
    }
    if ordered:
        slowest = max(ordered, key=lambda t: t.dax_ms)
        rest = [t for t in ordered if t is not slowest]
        without = _run(k, rest, False) if rest else 0.0
        what_if["without_slowest_visual"] = {
            "visual": slowest.visual,
            "est_page_ms": round(without, 1),
            "saving_ms": round(est_page_ms - without, 1),
        }
    return {
        "k_slots": k,
        "schedule": "layout",
        "est_page_ms": round(est_page_ms, 1),
        "serial_ms": round(serial_ms, 1),
        "critical_path_ms": round(critical_path_ms, 1),
        "total_wait_ms": round(total_wait, 1),
        "wait_pct": round(100.0 * total_wait / max(sum(t.total_ms for t in ordered), 1), 1),
        "what_if": what_if,
        "note": (
            f"Wait ('Other') is ESTIMATED with a {k}-slot scheduler in page-layout order "
            "(tabOrder, then position); Power BI does not publish its concurrency limit and "
            "the per-visual DAX times are cold and isolated. Read Desktop's Performance "
            "Analyzer pane (perf_analyzer_import) for measured values."
        ),
    }


def _parse_iso_ms(value: Any) -> Optional[float]:
    """ISO-8601 timestamp -> epoch milliseconds (numbers pass through)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        from datetime import datetime as _dt

        return _dt.fromisoformat(text).timestamp() * 1000.0
    except ValueError:
        return None


def normalise_performance_export(data: Any) -> Any:
    """Turn Desktop's FLAT ``PowerBIPerformanceData.json`` into the nested shape (F-31).

    Desktop (and DAX Studio's importer) use a flat ``events[]`` list linked by
    ``id`` / ``parentId`` with ISO-timestamp ``start`` / ``end``; an event can be
    written twice (once at start, once at end). ``pbi_perf_core`` expects nested
    ``children`` with numeric times, so a real export parsed to DAX = 0 for every
    visual. Already-nested exports are returned unchanged.
    """
    if isinstance(data, dict):
        events = data.get("events") or data.get("Events")
    else:
        events = data
    if not isinstance(events, list) or not events:
        return data
    if not any(isinstance(e, dict) and ("parentId" in e or "id" in e) for e in events):
        return data
    if any(isinstance(e, dict) and isinstance(e.get("children"), list) for e in events):
        return data

    nodes: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    anonymous: List[Dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        eid = event.get("id")
        if eid is None:
            node = dict(event)
            node["children"] = []
            anonymous.append(node)
            continue
        key = str(eid)
        node = nodes.get(key)
        if node is None:
            node = {"children": []}
            nodes[key] = node
            order.append(key)
        for k, v in event.items():
            if k == "metrics" and isinstance(v, dict) and isinstance(node.get("metrics"), dict):
                node["metrics"] = {**node["metrics"], **v}
            elif v is not None and (k not in node or node[k] in (None, "")):
                node[k] = v
    for node in list(nodes.values()) + anonymous:
        for key in ("start", "end"):
            ms = _parse_iso_ms(node.get(key))
            if ms is not None:
                node[key] = ms
    roots: List[Dict[str, Any]] = []
    for key in order:
        node = nodes[key]
        parent = node.get("parentId")
        if parent is not None and str(parent) in nodes and str(parent) != key:
            nodes[str(parent)]["children"].append(node)
        else:
            roots.append(node)
    roots.extend(anonymous)
    if isinstance(data, dict):
        out = dict(data)
        out["events"] = roots
        return out
    return roots


class DebugOperations(DebugDocumentMixin, DebugAnalysisMixin):
    """
    Consolidated debug operations for validation, profiling, documentation, and analysis.

    Designed to minimize tool count by grouping related operations.
    """

    def __init__(self, visual_query_builder, query_executor=None):
        """query_executor: optional QueryExecutor for live model queries."""
        self.builder = visual_query_builder
        self.qe = query_executor
        self.logger = logging.getLogger(__name__)

    # ========== VALIDATION OPERATIONS ==========

    def cross_visual_validation(
        self, measure_name: str, page_names: Optional[List[str]] = None, tolerance: float = 0.001
    ) -> Dict[str, Any]:
        """
        Compare the same measure across multiple visuals to find inconsistencies.
        """
        if not self.qe:
            return {"success": False, "error": "Not connected to Power BI model"}

        results: List[ValidationResult] = []
        fidelity: Dict[str, str] = {}
        pages = page_names or [p["name"] for p in self.builder.list_pages()]

        clean_measure = measure_name.strip("[]")

        for page_name in pages:
            visuals = self.builder.list_visuals(page_name)

            for visual in visuals:
                if visual.get("is_slicer"):
                    continue

                # Check if visual uses this measure
                visual_measures = visual.get("measures", [])
                if clean_measure not in [m.strip("[]") for m in visual_measures]:
                    continue

                # Get filter context and execute
                try:
                    query_result = self.builder.build_visual_query(
                        page_name=page_name, visual_id=visual["id"], measure_name=measure_name
                    )

                    if query_result and query_result.dax_query:
                        # F-15: the measure over everything the visual shows, read
                        # from ITS column -- not the first column of the visual
                        # query (a group-by column, or BLANK on a total row).
                        total_query, alias = self.builder.build_total_query(
                            query_result, measure_name
                        )
                        exec_result = self.qe.validate_and_execute_dax(
                            total_query, top_n=1, bypass_cache=True
                        )

                        value = None
                        if exec_result.get("success"):
                            value = _first_value(exec_result, alias)
                        fidelity[visual["id"]] = getattr(query_result, "fidelity", "full")

                        results.append(
                            ValidationResult(
                                visual_id=visual["id"],
                                visual_name=visual.get("friendly_name", visual["id"]),
                                page_name=page_name,
                                measure_name=clean_measure,
                                value=value,
                                execution_time_ms=exec_result.get("execution_time_ms", 0),
                                success=exec_result.get("success", False),
                                error=exec_result.get("error"),
                            )
                        )

                except Exception as e:
                    results.append(
                        ValidationResult(
                            visual_id=visual["id"],
                            visual_name=visual.get("friendly_name", visual["id"]),
                            page_name=page_name,
                            measure_name=clean_measure,
                            value=None,
                            execution_time_ms=0,
                            success=False,
                            error=engine_message(e),
                        )
                    )

        # Analyze for discrepancies
        discrepancies = self._find_discrepancies(results, tolerance)

        return {
            "success": True,
            "measure": clean_measure,
            "visuals_checked": len(results),
            "results": [
                {
                    "page": r.page_name,
                    "visual": r.visual_name,
                    "value": r.value,
                    "time_ms": r.execution_time_ms,
                    "success": r.success,
                    "error": r.error,
                }
                for r in results
            ],
            "discrepancies": discrepancies,
            "has_discrepancies": len(discrepancies) > 0,
            "failed": [
                {"page": r.page_name, "visual": r.visual_name, "error": r.error}
                for r in results
                if not r.success or r.value is None
            ],
            "partial_fidelity_visuals": [k for k, v in fidelity.items() if v != "full"],
            "value_source": "total query per visual (visual filter context, cache bypassed)",
        }

    def _find_discrepancies(
        self, results: List[ValidationResult], tolerance: float
    ) -> List[Dict[str, Any]]:
        """Find value discrepancies across validation results."""
        discrepancies = []

        # Group successful results by value
        successful = [r for r in results if r.success and r.value is not None]
        if len(successful) < 2:
            return discrepancies

        # Use first value as baseline
        baseline = successful[0]
        baseline_value = baseline.value

        for result in successful[1:]:
            try:
                if isinstance(baseline_value, (int, float)) and isinstance(
                    result.value, (int, float)
                ):
                    diff = abs(float(result.value) - float(baseline_value))
                    if diff > tolerance:
                        discrepancies.append(
                            {
                                "type": "value_mismatch",
                                "baseline": {
                                    "page": baseline.page_name,
                                    "visual": baseline.visual_name,
                                    "value": baseline_value,
                                },
                                "different": {
                                    "page": result.page_name,
                                    "visual": result.visual_name,
                                    "value": result.value,
                                },
                                "difference": diff,
                            }
                        )
                elif str(baseline_value) != str(result.value):
                    discrepancies.append(
                        {
                            "type": "value_mismatch",
                            "baseline": {
                                "page": baseline.page_name,
                                "visual": baseline.visual_name,
                                "value": baseline_value,
                            },
                            "different": {
                                "page": result.page_name,
                                "visual": result.visual_name,
                                "value": result.value,
                            },
                        }
                    )
            except (ValueError, TypeError):
                continue

        return discrepancies

    def expected_value_test(
        self,
        page_name: str,
        visual_id: Optional[str] = None,
        visual_name: Optional[str] = None,
        expected_value: Any = None,
        filters: Optional[List[str]] = None,
        tolerance: float = 0.001,
    ) -> Dict[str, Any]:
        """
        Test that a visual returns an expected value with given filters.
        """
        if not self.qe:
            return {"success": False, "error": "Not connected to Power BI model"}

        query_result = self.builder.build_visual_query(
            page_name=page_name, visual_id=visual_id, visual_name=visual_name
        )

        if not query_result:
            return {"success": False, "error": "Visual not found"}

        # Manual filters are kept verbatim as KEEPFILTERS(...) arguments (F-06);
        # the value is the measure over what the visual shows (F-15).
        all_filters = query_result.filter_context.all_filters()
        manual = [f for f in (filters or []) if isinstance(f, str) and f.strip()]
        all_filters = all_filters + [self.builder.converter.manual_filter(f) for f in manual]
        query, alias = self.builder.build_total_query(query_result, filters=all_filters)

        exec_result = self.qe.validate_and_execute_dax(query, top_n=10, bypass_cache=True)

        if not exec_result.get("success"):
            return {
                "success": False,
                "error": exec_result.get("error"),
                "test_passed": False,
                "query": query,
            }

        # Extract actual value
        actual_value = _first_value(exec_result, alias)

        # Compare values
        test_passed = False
        difference = None

        if expected_value is not None and actual_value is not None:
            try:
                exp_num = float(expected_value)
                act_num = float(actual_value)
                difference = act_num - exp_num
                test_passed = abs(difference) <= tolerance
            except (ValueError, TypeError):
                test_passed = str(expected_value) == str(actual_value)
                difference = "N/A (non-numeric)"
        elif expected_value is None and actual_value is None:
            test_passed = True

        return {
            "success": True,
            "test_passed": test_passed,
            "visual": {
                "id": query_result.visual_info.visual_id,
                "name": query_result.visual_info.visual_name,
                "page": page_name,
            },
            "expected_value": expected_value,
            "actual_value": actual_value,
            "difference": difference,
            "tolerance": tolerance,
            "execution_time_ms": exec_result.get("execution_time_ms"),
            "cache": _cache_info(exec_result),
            "query": query,
            "filters_applied": len(all_filters),
            "fidelity": getattr(query_result, "fidelity", "full"),
            "unsupported_filters": getattr(query_result, "unsupported_filters", []),
        }

    def filter_permutation_test(
        self,
        page_name: str,
        visual_id: Optional[str] = None,
        visual_name: Optional[str] = None,
        max_permutations: int = 20,
    ) -> Dict[str, Any]:
        """
        Test a visual against different slicer value combinations (max_permutations caps the count).
        """
        if not self.qe:
            return {"success": False, "error": "Not connected to Power BI model"}

        # Get slicers on the page
        slicers = self.builder.list_slicers(page_name)
        if not slicers:
            return {"success": True, "message": "No slicers found on page", "results": []}

        from core.dax.dax_names import column_ref

        # Get unique values for each slicer (limited)
        slicer_values = {}
        slicer_cols: Dict[str, Tuple[str, str]] = {}
        for slicer in slicers[:3]:  # Limit to 3 slicers to avoid explosion
            table = slicer.table
            column = slicer.column
            ref = column_ref(table, column)

            # Query distinct values
            try:
                query = f"EVALUATE TOPN(5, DISTINCT({ref}))"
                result = self.qe.validate_and_execute_dax(query, top_n=5, bypass_cache=True)
                if result.get("success") and result.get("rows"):
                    values = [
                        list(r.values())[0]
                        for r in result["rows"]
                        if list(r.values())[0] is not None
                    ]
                    slicer_values[ref] = values[:5]
                    slicer_cols[ref] = (table, column)
            except Exception:
                continue

        if not slicer_values:
            return {"success": True, "message": "Could not get slicer values", "results": []}

        # Generate permutations
        permutations = self._generate_filter_permutations(slicer_values, max_permutations)

        # Test each permutation
        results = []
        query_result = self.builder.build_visual_query(
            page_name=page_name, visual_id=visual_id, visual_name=visual_name
        )

        if not query_result:
            return {"success": False, "error": "Visual not found"}

        base_filters = query_result.filter_context.all_filters()
        converter = self.builder.converter

        for i, perm in enumerate(permutations[:max_permutations]):
            if _cancelled():
                break
            try:
                # The permuted value REPLACES the slicer's own selection on that
                # column; every other filter of the visual stays applied (F-06).
                # Literals follow the column's model type (F-37: 2024, not "2024").
                permuted = {slicer_cols[c] for c in perm if c in slicer_cols}
                kept = [
                    f
                    for f in base_filters
                    if not (f.source == "slicer" and (f.table, f.column) in permuted)
                ]
                test_filters = [
                    converter.equality_filter(*slicer_cols[c], value)
                    for c, value in perm.items()
                    if c in slicer_cols
                ]
                query, alias = self.builder.build_total_query(
                    query_result, filters=kept + test_filters
                )
                exec_result = self.qe.validate_and_execute_dax(query, top_n=1, bypass_cache=True)

                value = _first_value(exec_result, alias) if exec_result.get("success") else None

                results.append(
                    {
                        "permutation": i + 1,
                        "filters": perm,
                        "filter_dax": [f.dax for f in test_filters],
                        "value": value,
                        "is_null": value is None,
                        "is_error": not exec_result.get("success"),
                        "error": exec_result.get("error"),
                        "time_ms": exec_result.get("execution_time_ms", 0),
                    }
                )

            except Exception as e:
                results.append(
                    {
                        "permutation": i + 1,
                        "filters": perm,
                        "value": None,
                        "is_null": True,
                        "is_error": True,
                        "error": engine_message(e),
                        "time_ms": 0,
                    }
                )

        # Analyze results
        null_count = sum(1 for r in results if r["is_null"])
        error_count = sum(1 for r in results if r["is_error"])

        return {
            "success": True,
            "visual": {
                "id": query_result.visual_info.visual_id,
                "name": query_result.visual_info.visual_name,
                "page": page_name,
            },
            "permutations_tested": len(results),
            "null_results": null_count,
            "error_results": error_count,
            "results": results,
            "issues": [
                f"{null_count} filter combinations returned NULL" if null_count > 0 else None,
                f"{error_count} filter combinations caused errors" if error_count > 0 else None,
            ],
        }

    def _generate_filter_permutations(
        self, slicer_values: Dict[str, List[Any]], max_count: int
    ) -> List[Dict[str, Any]]:
        """Generate filter value permutations."""
        if not slicer_values:
            return []

        keys = list(slicer_values.keys())
        values_list = [slicer_values[k] for k in keys]

        # Simple cartesian product with limit
        permutations = []

        def generate(current: Dict, depth: int):
            if len(permutations) >= max_count:
                return
            if depth == len(keys):
                permutations.append(current.copy())
                return

            key = keys[depth]
            for val in values_list[depth]:
                current[key] = val
                generate(current, depth + 1)

        generate({}, 0)
        return permutations

    # ========== PROFILING OPERATIONS ==========

    def profile_page(
        self,
        page_name: str,
        iterations: int = 3,
        include_slicers: bool = True,
        parallel: bool = False,
        max_workers: int = 4,
        trace: bool = False,
        clear_cache: bool = True,
        simulate_concurrency: int = DEFAULT_QUERY_SLOTS,
        per_visual_limit: Optional[int] = None,
        schedule: str = "lpt",
        warm_pass: bool = False,
        time_budget_s: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Profile all visuals on a page to identify slow visuals.

        parallel=False (default) because validate_and_execute_dax is not verified ADOMD-thread-safe;
        opt in only when each worker has its own connection.

        Every run bypasses the query-result cache (F-07): run 2..N used to be cache
        hits that replayed run 1's timing, so avg == min == max and "High variance"
        could never fire.

        trace=True routes every visual query through NativeTraceRunner (SE/FE split,
        SE query count, cache hits, peak memory) and then models the page as a
        K-slot queue to estimate Performance Analyzer's "Other" — the wait a visual
        accumulates before it gets a query slot, which is what dominates a page with
        many visuals. That wait is an ESTIMATE: Power BI publishes no concurrency
        limit, so it is labelled as such everywhere it appears.
        """
        if not self.qe:
            return {"success": False, "error": "Not connected to Power BI model"}

        visuals = self.builder.list_visuals(page_name)
        if not visuals:
            return {"success": False, "error": f'No visuals found on page "{page_name}"'}

        # Filter to data visuals only (exclude slicers)
        data_visuals = [v for v in visuals if not v.get("is_slicer")]

        if per_visual_limit is not None and per_visual_limit > 0:
            data_visuals = data_visuals[:per_visual_limit]

        if not data_visuals:
            return {
                "success": True,
                "page": page_name,
                "visuals_profiled": 0,
                "message": "No data visuals to profile (page contains only slicers/UI elements)",
            }

        budget_s = time_budget_s if time_budget_s else _tool_budget_s("09_Profile")
        deadline = time.monotonic() + budget_s

        if trace:
            return self._profile_page_traced(
                page_name=page_name,
                data_visuals=data_visuals,
                iterations=iterations,
                include_slicers=include_slicers,
                clear_cache=clear_cache,
                simulate_concurrency=simulate_concurrency,
                schedule=schedule,
                warm_pass=warm_pass,
                deadline=deadline,
                budget_s=budget_s,
            )

        results: List[ProfileResult] = []
        # Visuals that could not be profiled, with the reason. Without this a build or
        # execution failure silently vanished from the output and the page looked like it
        # simply had fewer visuals.
        skipped: List[Dict[str, str]] = []
        cache_hits = 0

        def _skip(visual: Dict, reason: str, detail: str = "") -> None:
            entry = {
                "visual_id": visual.get("id", "unknown"),
                "visual_name": visual.get("friendly_name", visual.get("id", "unknown")),
                "visual_type": visual.get("type", "unknown"),
                "reason": reason,
            }
            if detail:
                entry["detail"] = detail[:500]
            skipped.append(entry)

        def _profile_single_visual(visual: Dict) -> Optional[ProfileResult]:
            """Profile a single visual (thread-safe)."""
            nonlocal cache_hits
            try:
                query_result = self.builder.build_visual_query(
                    page_name=page_name, visual_id=visual["id"], include_slicers=include_slicers
                )

                if not query_result or not query_result.dax_query:
                    _skip(
                        visual,
                        "query_not_built",
                        "No DAX query could be built for this visual (no measures bound, "
                        "or its fields could not be resolved).",
                    )
                    return None

                times = []
                row_count = 0
                last_error = ""

                for _ in range(iterations):
                    if _cancelled() or time.monotonic() > deadline:
                        break
                    exec_result = self.qe.validate_and_execute_dax(
                        query_result.dax_query, top_n=100, bypass_cache=True
                    )
                    if _cache_info(exec_result)["hit"]:
                        cache_hits += 1
                    if exec_result.get("success"):
                        times.append(exec_result.get("execution_time_ms", 0))
                        row_count = max(row_count, len(exec_result.get("rows", [])))
                    else:
                        last_error = str(exec_result.get("error", "unknown DAX error"))

                if not times:
                    _skip(
                        visual,
                        "query_failed" if last_error else "time_budget_exhausted",
                        last_error,
                    )
                    return None

                avg_time = sum(times) / len(times)

                # Identify issues
                issues = []
                if avg_time > 2000:
                    issues.append(f"Slow query ({avg_time:.0f}ms > 2000ms)")
                if row_count > 1000:
                    issues.append(f"Large result set ({row_count} rows)")
                if max(times) > min(times) * 2 and len(times) > 1:
                    issues.append(f"High variance ({min(times):.0f}-{max(times):.0f}ms)")
                if getattr(query_result, "fidelity", "full") != "full":
                    issues.append(
                        "Query does not reproduce every visual filter (fidelity: partial)"
                    )

                return ProfileResult(
                    visual_id=visual["id"],
                    visual_name=visual.get("friendly_name", visual["id"]),
                    visual_type=visual.get("type", "unknown"),
                    page_name=page_name,
                    measures=visual.get("measures", []),
                    avg_time_ms=avg_time,
                    min_time_ms=min(times),
                    max_time_ms=max(times),
                    row_count=row_count,
                    filter_count=len(query_result.filter_context.all_filters()),
                    issues=issues,
                    runs_ms=[float(t) for t in times],
                )
            except Exception as e:
                self.logger.warning(
                    f"Error profiling visual {visual['id']}: {type(e).__name__}: {e}",
                    exc_info=True,
                )
                _skip(visual, "exception", f"{type(e).__name__}: {e}")
                return None

        # Execute profiling
        execution_mode = "sequential"
        ticker = Ticker(len(data_visuals), "visual")  # F-26: per-visual progress
        cancelled_seen = False

        if parallel and len(data_visuals) > 1:
            from concurrent.futures import ThreadPoolExecutor, as_completed

            execution_mode = "parallel"

            with ThreadPoolExecutor(max_workers=min(max_workers, len(data_visuals))) as executor:
                futures = {executor.submit(_profile_single_visual, v): v for v in data_visuals}

                for future in as_completed(futures):
                    try:
                        result = future.result()
                        if result:
                            results.append(result)
                    except Exception as e:
                        visual = futures[future]
                        self.logger.warning(f"Future failed for visual {visual.get('id')}: {e}")
                    ticker.tick()
            cancelled_seen = _cancelled()
        else:
            # Sequential execution
            for visual in data_visuals:
                label = str(visual.get("friendly_name", visual.get("id", "")))
                if _cancelled():
                    cancelled_seen = True
                if cancelled_seen or time.monotonic() > deadline:
                    if cancelled_seen:
                        _skip(visual, "cancelled", "the call was cancelled")
                    else:
                        _skip(
                            visual,
                            "time_budget_exhausted",
                            f"stopped after {budget_s:.0f}s so the call returns before its timeout",
                        )
                    ticker.tick(label)
                    continue
                result = _profile_single_visual(visual)
                if result:
                    results.append(result)
                ticker.tick(label)
        cancelled_seen = cancelled_seen or _cancelled()

        # Sort by avg time descending
        results.sort(key=lambda x: x.avg_time_ms, reverse=True)

        total_time = sum(r.avg_time_ms for r in results)

        # Generate recommendations
        recommendations = []
        slow_visuals = [r for r in results if r.avg_time_ms > 1000]

        if slow_visuals:
            recommendations.append(
                f"Optimize {len(slow_visuals)} slow visual(s): "
                + ", ".join(r.visual_name for r in slow_visuals[:3])
            )

        if total_time > 5000:
            recommendations.append(f"Page total load time ({total_time:.0f}ms) exceeds 5s target")

        large_results = [r for r in results if r.row_count > 500]
        if large_results:
            recommendations.append(
                f"{len(large_results)} visual(s) return large result sets - consider aggregation"
            )

        if skipped:
            recommendations.append(
                f'{len(skipped)} visual(s) could not be profiled - see "skipped" for reasons'
            )

        return {
            "success": True,
            **({"cancelled": True, "partial": True} if cancelled_seen else {}),
            "page": page_name,
            "visuals_profiled": len(results),
            "visuals_skipped": len(skipped),
            "skipped": skipped,
            "data_visuals_found": len(data_visuals),
            "total_time_ms": round(total_time, 1),
            "avg_time_per_visual_ms": round(total_time / len(results), 1) if results else 0,
            "execution_mode": execution_mode,
            "cache": {"bypassed": True, "hits": cache_hits},
            "timing_note": (
                "Client wall-clock per run (warm storage-engine cache after the first run; "
                "the query-result cache is bypassed). Use trace=true for SE/FE splits."
            ),
            "results": [
                {
                    "visual_id": r.visual_id,
                    "visual_name": r.visual_name,
                    "visual_type": r.visual_type,
                    "measures": r.measures,
                    "avg_time_ms": round(r.avg_time_ms, 1),
                    "min_time_ms": round(r.min_time_ms, 1),
                    "max_time_ms": round(r.max_time_ms, 1),
                    "row_count": r.row_count,
                    "filter_count": r.filter_count,
                    "issues": r.issues,
                    "runs_ms": [round(t, 1) for t in r.runs_ms],
                }
                for r in results
            ],
            "recommendations": recommendations,
        }

    # ---- Performance Analyzer parity (WP-D) --------------------------------

    def _get_trace_runner(self):
        """Build a NativeTraceRunner for the live connection. (runner, error)."""
        from core.infrastructure.connection_state import connection_state
        from core.infrastructure.query_trace import NativeTraceRunner

        conn_str = getattr(connection_state.connection_manager, "connection_string", None)
        if not conn_str:
            return None, "No connection string available for tracing"
        if not NativeTraceRunner.is_available():
            return None, (
                "No trace lane available: pythonnet/AMO could not be loaded and "
                "DaxExecutor.exe is not built."
            )
        return NativeTraceRunner(conn_str), None

    #: Rough cost of one traced run on Desktop: event delivery + our quiet window.
    _TRACED_RUN_OVERHEAD_S = 2.8

    def _profile_page_traced(
        self,
        page_name: str,
        data_visuals: List[Dict],
        iterations: int,
        include_slicers: bool,
        clear_cache: bool,
        simulate_concurrency: int,
        schedule: str = "lpt",
        warm_pass: bool = False,
        deadline: Optional[float] = None,
        budget_s: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Performance-Analyzer-shaped page profile: traced DAX + estimated wait.

        Baseline protocol (Microsoft's, and the only honest one): one warm-up run
        that is DISCARDED — the first execution pays query-plan compilation and
        first-touch column paging that no later run repeats — then ``iterations``
        measured runs, of which the FASTEST is kept. The fastest run is the one
        least polluted by background work on the machine; an average of a noisy
        set measures the noise.

        F-35: ALL passes run on ONE trace session (one batch). F-14: the batch
        stops at ``deadline`` (tool timeout minus a margin) or when the MCP call
        is cancelled, so a timed-out profile never keeps clearing the cache and
        querying in the background; skipped runs are reported.
        """
        from pbi_perf_core.perf_analyzer import VisualTiming, simulate_page

        runner, runner_error = self._get_trace_runner()
        if runner_error:
            return {"success": False, "error": runner_error}

        skipped: List[Dict[str, str]] = []

        def _skip(visual: Dict, reason: str, detail: str = "") -> None:
            entry = {
                "visual_id": visual.get("id", "unknown"),
                "visual_name": visual.get("friendly_name", visual.get("id", "unknown")),
                "visual_type": visual.get("type", "unknown"),
                "reason": reason,
            }
            if detail:
                entry["detail"] = detail[:500]
            skipped.append(entry)

        # Build every query first, so one trace session can run them back to back.
        buildable: List[Tuple[Dict, str]] = []
        for visual in data_visuals:
            try:
                query_result = self.builder.build_visual_query(
                    page_name=page_name,
                    visual_id=visual["id"],
                    include_slicers=include_slicers,
                )
            except Exception as e:
                self.logger.warning(
                    f"Error building query for visual {visual.get('id')}: {type(e).__name__}: {e}",
                    exc_info=True,
                )
                _skip(visual, "exception", f"{type(e).__name__}: {e}")
                continue
            if not query_result or not query_result.dax_query:
                _skip(
                    visual,
                    "query_not_built",
                    "No DAX query could be built for this visual (no measures bound, "
                    "or its fields could not be resolved).",
                )
                continue
            buildable.append((visual, query_result.dax_query))

        if not buildable:
            return {
                "success": True,
                "page": page_name,
                "mode": "trace",
                "visuals_profiled": 0,
                "visuals_skipped": len(skipped),
                "skipped": skipped,
                "data_visuals_found": len(data_visuals),
                "message": "No visual on this page produced a DAX query.",
            }

        runs = max(int(iterations), 1)
        queries = [q for _, q in buildable]
        passes = runs + 1  # pass 0 is the warm-up and is never recorded
        estimated_s = round(len(queries) * passes * self._TRACED_RUN_OVERHEAD_S, 1)
        flat: List[str] = [q for _ in range(passes) for q in queries]

        cancelled_seen = False

        def _should_stop() -> bool:
            nonlocal cancelled_seen
            if _cancelled():
                cancelled_seen = True
            return cancelled_seen or (deadline is not None and time.monotonic() > deadline)

        # F-26: the batch polls should_stop() once before every traced run, so each poll is
        # "run k of N is about to start".
        run_ticker = Ticker(len(flat), "traced run")

        def _batch_should_stop() -> bool:
            stop = _should_stop()
            if not stop:
                run_ticker.tick()
            return stop

        batch = runner.execute_batch_with_trace(
            flat,
            clear_cache=clear_cache,
            capture_plan=False,
            should_stop=_batch_should_stop,
        )
        n = len(queries)
        per_visual_runs: List[List[Dict[str, Any]]] = [[] for _ in buildable]
        stopped_runs = 0
        for index, result in enumerate(batch or []):
            pass_index, visual_index = divmod(index, n)
            if (result or {}).get("_error_type") == "cancelled":
                stopped_runs += 1
                continue
            if pass_index == 0:
                continue
            if visual_index < len(per_visual_runs):
                per_visual_runs[visual_index].append(result or {})

        warm_totals: Dict[int, float] = {}
        if warm_pass and not _should_stop():
            # F-33: one page pass with NO ClearCache between visuals -- the shared
            # SE caches a real page load benefits from.
            warm = runner.execute_batch_with_trace(
                queries, clear_cache=False, capture_plan=False, should_stop=_should_stop
            )
            for i, r in enumerate(warm or []):
                if r and "_error" not in r:
                    warm_totals[i] = float(r.get("total_ms", 0) or 0)

        timings: List[VisualTiming] = []
        rows: List[Dict[str, Any]] = []
        for i, ((visual, query), results) in enumerate(zip(buildable, per_visual_runs)):
            valid = [r for r in results if r and "_error" not in r]
            if not valid:
                errors = [str(r.get("_error")) for r in results if r and "_error" in r]
                reason = "query_failed" if errors else "time_budget_exhausted"
                _skip(visual, reason, errors[0] if errors else "no measured run completed in time")
                continue

            best = min(valid, key=lambda r: r.get("total_ms", 0) or 0)
            metrics = best.get("execution_metrics") or {}
            name = visual.get("friendly_name", visual.get("id", "unknown"))

            timing = VisualTiming(
                visual=name,
                visual_id=visual.get("id", ""),
                visual_type=visual.get("type", "unknown"),
                dax_ms=float(best.get("total_ms", 0) or 0),
                se_ms=float(best.get("se_ms", 0) or 0),
                fe_ms=float(best.get("fe_ms", 0) or 0),
                se_queries=best.get("se_queries"),
                cache_hits=best.get("se_cache_hits"),
                peak_memory_kb=_as_int(metrics.get("peak_memory_kb")),
                row_count=_traced_row_count(best, metrics),
                query_text=query,
            )
            timings.append(timing)
            rows.append(
                {
                    "visual_id": timing.visual_id,
                    "callbacks": sum(1 for e in best.get("se_events", []) if e.get("has_callback")),
                    "runs_measured": len(valid),
                    "runs_ms": [round(float(r.get("total_ms", 0) or 0), 1) for r in valid],
                    "measures": list(visual.get("measures") or []),
                    "runs_failed": len(results) - len(valid),
                    "cache_cleared_all": all(bool(r.get("cache_cleared")) for r in valid),
                    "min_ms": round(min(float(r.get("total_ms", 0) or 0) for r in valid), 1),
                    "max_ms": round(max(float(r.get("total_ms", 0) or 0) for r in valid), 1),
                    "warm_page_pass_ms": warm_totals.get(i),
                }
            )

        if not timings:
            return {
                "success": True,
                "page": page_name,
                "mode": "trace",
                "visuals_profiled": 0,
                "visuals_skipped": len(skipped),
                "skipped": skipped,
                "data_visuals_found": len(data_visuals),
                "estimated_runtime_s": estimated_s,
                "message": 'Every visual query failed under trace - see "skipped".',
            }

        layout_order = [
            v.get("id", "")
            for v in sorted(
                (v for v, _ in buildable),
                key=lambda v: (
                    (v.get("position") or {}).get("tabOrder", 10**9),
                    (v.get("position") or {}).get("y", 0),
                    (v.get("position") or {}).get("x", 0),
                ),
            )
        ]
        if schedule == "layout":
            simulation = _simulate_layout(timings, simulate_concurrency, layout_order)
        else:
            simulation = simulate_page(timings, k_slots=simulate_concurrency)
            simulation["note"] = (
                simulation.get("note", "") + " The longest-first (LPT) schedule is an "
                'optimistic LOWER BOUND on page time; schedule="layout" orders by the '
                "page layout like a real page load, and the per-visual DAX times are cold "
                "and isolated (no SE cache shared across visuals) - pass warm_pass=true for "
                "a back-to-back page pass."
            )
            simulation["schedule"] = "lpt"
        extras = {r["visual_id"]: r for r in rows}

        # simulate_page mutates the VisualTiming objects in place with the queue
        # estimate, so the table can be built straight off them.
        table = []
        for timing in sorted(timings, key=lambda t: -t.total_ms):
            extra = extras.get(timing.visual_id, {})
            entry = {
                "visual": timing.visual,
                "visual_id": timing.visual_id,
                "type": timing.visual_type,
                "dax_ms": round(timing.dax_ms, 1),
                "other_wait_est_ms": round(timing.other_ms, 1),
                "total_est_ms": round(timing.total_ms, 1),
                "se_ms": round(timing.se_ms or 0, 1),
                "fe_ms": round(timing.fe_ms or 0, 1),
                "se_pct": (
                    round(100.0 * (timing.se_ms or 0) / timing.dax_ms, 1) if timing.dax_ms else 0.0
                ),
                "se_queries": timing.se_queries,
                "cache_hits": timing.cache_hits,
                "callbacks": extra.get("callbacks", 0),
                "peak_mem_kb": timing.peak_memory_kb,
                "row_count": timing.row_count,
                "runs_measured": extra.get("runs_measured", 0),
                "runs_ms": extra.get("runs_ms", []),
                "measures": extra.get("measures", []),
                "min_ms": extra.get("min_ms"),
                "max_ms": extra.get("max_ms"),
            }
            if extra.get("warm_page_pass_ms") is not None:
                entry["warm_page_pass_ms"] = extra["warm_page_pass_ms"]
            table.append(entry)

        recommendations = self._traced_page_recommendations(table, simulation, skipped)
        if stopped_runs:
            recommendations.append(
                f"{stopped_runs} traced run(s) were not executed: the {budget_s or 0:.0f}s "
                "time budget ran out (or the call was cancelled). Use per_visual_limit or "
                "fewer iterations for a complete profile."
            )

        return {
            "success": True,
            **({"cancelled": True, "partial": True} if cancelled_seen else {}),
            "page": page_name,
            "mode": "trace",
            "visuals_profiled": len(table),
            "visuals_skipped": len(skipped),
            "skipped": skipped,
            "data_visuals_found": len(data_visuals),
            "baseline": {
                "warmup_runs_discarded": 1,
                "measured_runs_per_visual": runs,
                "kept": "fastest",
                "clear_cache": clear_cache,
            },
            "runtime": {
                "estimated_s": estimated_s,
                "budget_s": round(budget_s, 1) if budget_s else None,
                "runs_not_executed": stopped_runs,
                "trace_sessions": 1 + (1 if warm_pass else 0),
            },
            "results": table,
            "page_estimate": {
                "k_slots": simulation["k_slots"],
                "schedule": simulation.get("schedule", schedule),
                "est_page_ms": simulation["est_page_ms"],
                "critical_path_ms": simulation["critical_path_ms"],
                "serial_ms": simulation["serial_ms"],
                "total_wait_ms": simulation["total_wait_ms"],
                "wait_pct": simulation["wait_pct"],
                "what_if": simulation.get("what_if", {}),
            },
            "estimate_note": simulation["note"],
            "recommendations": recommendations,
        }

    @staticmethod
    def _traced_page_recommendations(
        table: List[Dict[str, Any]],
        simulation: Dict[str, Any],
        skipped: List[Dict[str, str]],
    ) -> List[str]:
        """Turn the traced table + simulation into actionable lines."""
        recommendations: List[str] = []

        slow = [r for r in table if r["dax_ms"] > 1000]
        if slow:
            recommendations.append(
                f"Optimize {len(slow)} slow visual(s): " + ", ".join(r["visual"] for r in slow[:3])
            )

        callback_visuals = [r for r in table if r.get("callbacks")]
        if callback_visuals:
            recommendations.append(
                f"{len(callback_visuals)} visual(s) emit SE callbacks (CallbackDataID etc.) - "
                "these scans are serialised through the FE and never cached"
            )

        if simulation.get("wait_pct", 0) > 40:
            recommendations.append(
                f"Estimated wait is {simulation['wait_pct']}% of page time at "
                f"k={simulation['k_slots']} slots - the page is queue-bound, so "
                "reducing the NUMBER of query-bearing visuals helps more than tuning one query"
            )

        without = simulation.get("what_if", {}).get("without_slowest_visual")
        if isinstance(without, dict) and without.get("saving_ms", 0) > 0:
            recommendations.append(
                f"Removing '{without['visual']}' would cut an estimated "
                f"{without['saving_ms']}ms off page load"
            )

        if skipped:
            recommendations.append(
                f'{len(skipped)} visual(s) could not be profiled - see "skipped" for reasons'
            )
        return recommendations

    def perf_analyzer_import(
        self,
        file_path: str,
        trace: bool = False,
        pbip_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Parse Desktop's exported ``PowerBIPerformanceData.json``.

        This is the only lane with a MEASURED "Other" — Power BI computes it as the
        residual of the visual container lifecycle and never serialises it, so the
        parser rebuilds it the same way. Unlike ``profile_page(trace=True)`` nothing
        here is estimated.
        """
        import json
        from pathlib import Path

        from pbi_perf_core.perf_analyzer import summarise_rows

        path = Path(file_path)
        if not path.exists():
            return {"success": False, "error": f"File not found: {file_path}"}

        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            return {"success": False, "error": f"Not valid JSON: {e}"}
        except OSError as e:
            return {"success": False, "error": f"Could not read {file_path}: {e}"}

        from pbi_perf_core.perf_analyzer import parse_performance_export

        # F-31: Desktop writes a flat id/parentId event list with ISO timestamps.
        timings = parse_performance_export(normalise_performance_export(data))
        if not timings:
            return {
                "success": False,
                "error": (
                    'No "Visual Container Lifecycle" events found. Export the file from '
                    "Power BI Desktop's Performance Analyzer pane (Optimize ribbon -> Export)."
                ),
            }

        resolution = self._resolve_visual_names(timings, pbip_path)

        trace_errors: List[Dict[str, str]] = []
        if trace:
            trace_errors = self._attach_traced_splits(timings)

        summary = summarise_rows(timings)
        response: Dict[str, Any] = {
            "success": True,
            "source": str(path),
            "mode": "import",
            "visuals": len(timings),
            "results": summary["visuals"],
            "totals": summary["totals"],
            "slowest": summary["slowest"],
            "note": (
                '"Other" here is MEASURED (lifecycle residual: waiting for a query slot, '
                "query preparation, background work), not estimated."
            ),
        }
        if resolution:
            response["visual_resolution"] = resolution
        if trace:
            response["traced"] = sum(1 for t in timings if t.se_ms is not None)
            if trace_errors:
                response["trace_errors"] = trace_errors
        return response

    def _resolve_visual_names(self, timings: List[Any], pbip_path: Optional[str]) -> Dict[str, Any]:
        """Map exported visual ids onto their PBIR names/titles.

        The export carries a `visualId` and often no title at all, which makes the
        table unreadable; the PBIR definition is where the human name lives.
        """
        if not pbip_path:
            return {}

        try:
            from core.debug.visual_query_builder import VisualQueryBuilder

            builder = VisualQueryBuilder(pbip_path)
            pages = builder.list_pages()
        except Exception as e:
            return {"error": f"Could not read PBIR at {pbip_path}: {type(e).__name__}: {e}"}

        index: Dict[str, Dict[str, str]] = {}
        for page in pages:
            page_name = page.get("name") or page.get("display_name") or ""
            try:
                visuals = builder.list_visuals(page_name)
            except Exception:
                continue
            for visual in visuals:
                visual_id = str(visual.get("id") or "")
                if visual_id:
                    index[visual_id] = {
                        "name": visual.get("friendly_name", visual_id),
                        "type": visual.get("type", ""),
                        "page": page_name,
                    }

        resolved = 0
        for timing in timings:
            match = index.get(timing.visual_id)
            if not match:
                continue
            resolved += 1
            # The export's own title wins when it has one - it is what the user sees.
            if not timing.visual or timing.visual == timing.visual_id:
                timing.visual = match["name"]
            if not timing.visual_type:
                timing.visual_type = match["type"]

        return {
            "pbip_path": pbip_path,
            "visuals_in_report": len(index),
            "resolved": resolved,
            "unresolved": len(timings) - resolved,
        }

    def _attach_traced_splits(self, timings: List[Any]) -> List[Dict[str, str]]:
        """Re-run each exported QueryText under a trace to add the SE/FE split.

        The export records how long the query took but not where the time went;
        replaying it is the only way to get storage-engine vs formula-engine.
        """
        runner, runner_error = self._get_trace_runner()
        if runner_error:
            return [{"visual": "(all)", "error": runner_error}]

        with_query = [t for t in timings if t.query_text]
        if not with_query:
            return [{"visual": "(all)", "error": "No QueryText in the export to trace"}]

        results = runner.execute_batch_with_trace(
            [t.query_text for t in with_query],
            clear_cache=False,
            capture_plan=False,
        )

        errors: List[Dict[str, str]] = []
        for timing, result in zip(with_query, results, strict=True):
            if not result or "_error" in result:
                errors.append(
                    {
                        "visual": timing.visual,
                        "error": str((result or {}).get("_error", "trace returned no result")),
                    }
                )
                continue
            timing.se_ms = float(result.get("se_ms", 0) or 0)
            timing.fe_ms = float(result.get("fe_ms", 0) or 0)
            timing.se_queries = result.get("se_queries")
            timing.cache_hits = result.get("se_cache_hits")
            metrics = result.get("execution_metrics") or {}
            timing.peak_memory_kb = _as_int(metrics.get("peak_memory_kb"))
        return errors

    def filter_performance_matrix(
        self,
        page_name: str,
        visual_id: Optional[str] = None,
        visual_name: Optional[str] = None,
        filter_columns: Optional[List[str]] = None,
        max_combinations: int = 15,
        include_slicers: bool = True,
        iterations: int = 1,
    ) -> Dict[str, Any]:
        """
        Test measure performance with different filter combinations.

        Each combination runs ``iterations`` times (result cache bypassed) and
        the FASTEST run is kept, like the page profiler's baseline protocol.
        """
        if not self.qe:
            return {"success": False, "error": "Not connected to Power BI model"}

        query_result = self.builder.build_visual_query(
            page_name=page_name,
            visual_id=visual_id,
            visual_name=visual_name,
            include_slicers=include_slicers,
        )
        try:
            iterations = max(1, min(int(iterations or 1), 10))
        except (TypeError, ValueError):
            iterations = 1

        def _fastest(query: str) -> Dict[str, Any]:
            best: Dict[str, Any] = {}
            for _ in range(iterations):
                if _cancelled():
                    break
                res = self.qe.validate_and_execute_dax(query, top_n=1, bypass_cache=True)
                if not res.get("success"):
                    return res
                if not best or (res.get("execution_time_ms") or 0) < (
                    best.get("execution_time_ms") or 0
                ):
                    best = res
            return best or {"success": False, "error": "cancelled"}

        if not query_result:
            return {"success": False, "error": "Visual not found"}

        from core.dax.dax_names import column_ref, split_qualified

        # Get filter columns from slicers if not specified
        if not filter_columns:
            slicers = self.builder.list_slicers(page_name)
            filter_columns = [column_ref(s.table, s.column) for s in slicers[:3]]

        if not filter_columns:
            return {"success": True, "message": "No filter columns to test", "matrix": []}

        # Get sample values for each filter column
        filter_values = {}
        for col in filter_columns:
            try:
                query = f"EVALUATE TOPN(5, DISTINCT({col}))"
                result = self.qe.validate_and_execute_dax(query, top_n=5, bypass_cache=True)
                if result.get("success") and result.get("rows"):
                    values = [list(r.values())[0] for r in result["rows"]]
                    filter_values[col] = [v for v in values if v is not None][:5]
            except Exception:
                continue

        base_filters = query_result.filter_context.all_filters()
        converter = self.builder.converter

        matrix_results = []
        # F-26: progress per tested filter value (+ the baseline); a cancel returns what ran.
        planned = 1 + sum(min(len(v), 3) for v in filter_values.values())
        ticker = Ticker(min(planned, max_combinations), "filter combination")
        cancelled_seen = False

        # Test baseline (the visual's own query; result cache bypassed, F-07)
        baseline_result = _fastest(query_result.dax_query)
        baseline_time = baseline_result.get("execution_time_ms", 0)

        matrix_results.append(
            {
                "filters": "baseline (current)",
                "time_ms": baseline_time,
                "relative": 1.0,
                "status": "baseline",
            }
        )
        ticker.tick("baseline")

        # Test each filter value
        for col, values in filter_values.items():
            for val in values[:3]:  # Limit per column
                if len(matrix_results) >= max_combinations:
                    break
                if _cancelled():
                    cancelled_seen = True
                    break

                filter_dax = f"{col} = {val!r}"
                try:
                    table, column = split_qualified(col)
                    # Typed literal (F-37); the value REPLACES any filter the visual
                    # already has on that column, everything else stays (F-06).
                    test_filter = converter.equality_filter(table, column, val)
                    filter_dax = test_filter.dax
                    kept = [f for f in base_filters if (f.table, f.column) != (table, column)]
                    test_query = self.builder.rebuild_query(query_result, kept + [test_filter])

                    result = _fastest(test_query)
                    time_ms = result.get("execution_time_ms", 0)
                    if not result.get("success"):
                        raise RuntimeError(result.get("error") or "query failed")

                    relative = time_ms / baseline_time if baseline_time > 0 else 1.0

                    status = "normal"
                    if relative > 2.0:
                        status = "slow"
                    elif relative < 0.5:
                        status = "fast"

                    matrix_results.append(
                        {
                            "filters": filter_dax,
                            "time_ms": time_ms,
                            "relative": round(relative, 2),
                            "status": status,
                        }
                    )

                except Exception as e:
                    matrix_results.append(
                        {
                            "filters": filter_dax,
                            "time_ms": 0,
                            "relative": 0,
                            "status": "error",
                            "error": engine_message(e),
                        }
                    )
                ticker.tick(filter_dax)
            if cancelled_seen:
                break

        # Find problematic filters
        slow_filters = [r for r in matrix_results if r["status"] == "slow"]

        return {
            "success": True,
            **({"cancelled": True, "partial": True} if cancelled_seen else {}),
            "visual": {
                "id": query_result.visual_info.visual_id,
                "name": query_result.visual_info.visual_name,
                "page": page_name,
            },
            "baseline_time_ms": baseline_time,
            "combinations_tested": len(matrix_results),
            "matrix": matrix_results,
            "slow_filters": [r["filters"] for r in slow_filters],
            "recommendation": (
                f"{len(slow_filters)} filter(s) cause >2x slowdown"
                if slow_filters
                else "No problematic filters found"
            ),
        }

    # ========== DOCUMENTATION OPERATIONS ==========

    # ========== ADVANCED ANALYSIS OPERATIONS ==========
