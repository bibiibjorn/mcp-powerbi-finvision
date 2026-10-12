"""
Shared helpers for debug handler sub-modules.

Extracted from debug_handler.py as part of P2-183 split. Holds:

- Module-level constants (freshness threshold, row/char caps, VQB TTL)
- VQB cache state (_vqb_cache, _vqb_cache_lock) + bust_vqb_cache()
- _get_visual_query_builder — TTL-cached builder factory
- _compact_* response helpers
- _build_trace_response — NativeTraceRunner result mapper
- _check_pbip_freshness — stale-file warning
- _discover_visuals — discovery fallback for visual/compare/drill

All public debug_handler imports (tests, autonomous loops) resolve
through debug_handler.py's re-export block.
"""

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config.config_manager import config
from core.infrastructure.connection_state import connection_state
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)


# --- Module-level constants ---
PBIP_FRESHNESS_THRESHOLD_MINUTES = config.get("debug.pbip_freshness_minutes", 5)
DRILL_DETAIL_LIMIT_MIN = 1
DRILL_DETAIL_LIMIT_MAX = 10000
MAX_MEASURES_SHOWN = config.get("debug.max_measures_shown", 3)
MAX_QUERY_RESULT_ROWS = config.get("debug.max_query_result_rows", 100)
FILTER_TRUNCATION_CHARS = config.get("debug.filter_truncation_chars", 50)
VARIABLE_TRUNCATION_CHARS = config.get("debug.variable_truncation_chars", 100)

# Module-level VQB cache — avoids rebuilding PBIP per call
# Key: normalised report path, Value: (builder, created_at, signature, checked_at)
_vqb_cache: Dict[str, tuple] = {}
_vqb_cache_lock = threading.Lock()
_VQB_CACHE_TTL = config.get("debug.vqb_cache_ttl_seconds", 300)
#: How often a cache hit re-checks the report definition's signature (F-21c).
_VQB_SIGNATURE_RECHECK_S = 2.0


def _compact_response(data: Dict[str, Any], compact: bool = True) -> Dict[str, Any]:
    """Optimize response for token usage. Delegates to middleware.compact_response."""
    if not compact:
        return data
    from server.middleware import compact_response

    return compact_response(data, compact=True, remove_empty=True, remove_nulls=True)


def _compact_visual_list(visuals: List[Dict], compact: bool = True) -> List[Dict]:
    """Return compact visual list for discovery responses."""
    if not compact:
        return visuals
    # Return only essential fields: id, friendly_name, type, measures
    return [
        {
            "id": v.get("id"),
            "name": v.get("friendly_name", v.get("type", "?")),
            "type": v.get("type_display", v.get("type", "")),
            "measures": v.get("measures", [])[:MAX_MEASURES_SHOWN],
        }
        for v in visuals
    ]


def _compact_page_list(pages: List[Dict], compact: bool = True) -> List[Dict]:
    """Return compact page list."""
    if not compact:
        return pages
    return [{"name": p.get("name")} for p in pages]


def _compact_filter_context(filter_breakdown: Dict, compact: bool = True) -> Dict:
    """Return compact filter context - just the DAX expressions."""
    if not compact:
        return filter_breakdown
    # Return only dax strings grouped by level
    result = {}
    for level, filters in filter_breakdown.items():
        if filters:
            dax_list = [f.get("dax") for f in filters if f.get("dax")]
            if dax_list:
                result[level] = dax_list
    return result


def _build_trace_response(trace_result: dict) -> dict:
    """Build standardized trace response from NativeTraceRunner result.

    Extracts perf metrics, builds summary string, and runs deep SE event
    analysis.  Used by both _execute_visual_query and _handle_run_dax to
    avoid duplicating the mapping logic.
    """
    perf = {
        "total_ms": trace_result.get("total_ms", 0),
        "fe_ms": trace_result.get("fe_ms", 0),
        "se_ms": trace_result.get("se_ms", 0),
        "se_cpu_ms": trace_result.get("se_cpu_ms", 0),
        "se_parallelism": trace_result.get("se_parallelism", 0.0),
        "se_queries": trace_result.get("se_queries", 0),
        "se_cache_hits": trace_result.get("se_cache_hits", 0),
        "fe_pct": trace_result.get("fe_pct", 0.0),
        "se_pct": trace_result.get("se_pct", 0.0),
        "se_net_parallel_ms": trace_result.get("se_net_parallel_ms", 0),
        "total_cpu_ms": trace_result.get("total_cpu_ms", 0),
        "dq_ms": trace_result.get("dq_ms", 0),
        "parallel_se_detected": trace_result.get("parallel_se_detected", False),
    }
    se_events_list = trace_result.get("se_events", [])

    # Deduplicate xmSQL query text: store unique queries in a lookup,
    # reference by index in each event to avoid ~70-85% size bloat.
    query_lookup: list = []
    query_to_idx: dict = {}
    compact_se_events: list = []
    for evt in se_events_list:
        query_text = evt.get("query", "")
        if query_text not in query_to_idx:
            query_to_idx[query_text] = len(query_lookup)
            query_lookup.append(query_text)
        compact_evt = {k: v for k, v in evt.items() if k != "query"}
        compact_evt["query_idx"] = query_to_idx[query_text]
        compact_se_events.append(compact_evt)

    response = {
        "runner": "native",
        "runner_lane": trace_result.get("runner_lane", "unknown"),
        "performance": perf,
        "se_queries_lookup": query_lookup,
        "se_events": compact_se_events,
        "_se_events_raw": se_events_list,  # Internal: full events for cache consumers
        "cache_cleared": trace_result.get("cache_cleared", False),
        # All EVALUATE statements, plus the engine's own execution metrics
        # (peak memory, FE/SE CPU split, thread-pool queue wait).
        "result_sets": trace_result.get("result_sets", []),
        "execution_metrics": trace_result.get("execution_metrics", {}),
        "summary": (
            f"Total: {perf['total_ms']}ms | "
            f"FE: {perf['fe_ms']}ms ({perf['fe_pct']}%) | "
            f"SE: {perf['se_ms']}ms ({perf['se_pct']}%) | "
            f"SE queries: {perf['se_queries']} | "
            f"SE cache: {perf['se_cache_hits']}"
        ),
    }
    # Deep SE event analysis (uses original list with inline query text)
    if se_events_list:
        try:
            from core.dax.se_event_analyzer import SeEventAnalyzer

            response["se_analysis"] = SeEventAnalyzer().analyze(se_events_list, perf)
        except Exception:
            pass
    return response


def cache_provenance(exec_result: Dict[str, Any]) -> Dict[str, Any]:
    """``{hit, age_s, bypassed}`` for an executor result (F-07 provenance).

    Every 09_* measurement path passes ``bypass_cache=True``; the block makes
    that visible so a caller can tell a fresh round-trip from a replayed one.
    """
    cache = exec_result.get("cache") if isinstance(exec_result, dict) else None
    if not isinstance(cache, dict):
        return {"hit": False, "age_s": None, "bypassed": None}
    return {
        "hit": bool(cache.get("hit", False)),
        "age_s": cache.get("age_s"),
        "bypassed": cache.get("bypassed"),
    }


def trace_unavailable_message() -> str:
    """Error text when neither trace lane can run (F-36: in-process lane first)."""
    return (
        "No trace lane available: the in-process AMO trace could not load (pythonnet / "
        "AMO assemblies) and the DaxExecutor.exe fallback is not built. Check "
        "01_Connection operation=status for the AMO build; pin one with MCP_PBI_AMO_DIR."
    )


def _definition_dir(pbip_folder: str) -> Optional[Path]:
    try:
        from core.utilities.pbip_utils import find_definition_folder

        found = find_definition_folder(pbip_folder)
    except Exception:  # noqa: BLE001
        found = None
    if found is not None:
        return Path(found)
    candidate = Path(pbip_folder) / "definition"
    return candidate if candidate.exists() else None


def _definition_signature(definition_dir: Optional[Path]) -> tuple:
    """``(newest file mtime_ns, file count)`` of the REPORT definition folder only.

    P-06: one shared walker -- ``server.pbip_cache.signature_for_trees`` (parallel
    ``os.scandir``, no per-file ``open``). ``fresh=True``: no memo is trusted
    here, so a Desktop save or an external edit is seen on the very next
    recheck (F-21c); the recheck itself is rate-limited by the caller.
    """
    if definition_dir is None or not Path(definition_dir).exists():
        return (0, 0)
    from server.pbip_cache import pbip_cache

    sigs = pbip_cache.signature_for_trees([str(definition_dir)], fresh=True)
    if not sigs or sigs[0][0] < 0:
        return (0, 0)
    files, _dirs, _total, max_file_mtime, _max_dir_mtime = sigs[0]
    return (max_file_mtime, files)


def _check_pbip_freshness(
    pbip_folder: str, threshold_minutes: int = PBIP_FRESHNESS_THRESHOLD_MINUTES
) -> Optional[Dict[str, Any]]:
    """Describe the saved state the debugger reads (F-21a).

    The old heuristic warned whenever the newest PBIP file was older than 5
    minutes -- nearly always, so the warning was noise -- and pointed at the
    (then broken) ``filters`` override. What actually goes stale is (1) a
    cached builder older than the files, which the VQB cache now detects and
    rebuilds on its own, and (2) Desktop selections that were never saved.
    The latter cannot be seen from disk, so the result is informational
    (``stale: False``) and names the check that CAN see it
    (``17_Desktop_Bridge status`` -> ``hasUnsavedChanges``).
    """
    if not pbip_folder or not os.path.exists(pbip_folder):
        return None

    newest_ns, count = _definition_signature(_definition_dir(pbip_folder))
    if not newest_ns:
        return None
    age_minutes = (time.time() - newest_ns / 1e9) / 60
    return {
        "stale": False,
        "definition_files": count,
        "last_saved_minutes_ago": round(age_minutes, 1),
        "note": (
            "Slicer/filter state is read from the last SAVED report definition. "
            "Unsaved Desktop selections are invisible here -- check "
            "17_Desktop_Bridge operation=status (hasUnsavedChanges), or pass the "
            "current values via `filters` (with skip_auto_filters to replace them)."
        ),
        "threshold_minutes": threshold_minutes,
    }


def _normalize_cache_key(path: Optional[str]) -> Optional[str]:
    """Resolve any PBIP path form (.pbip / .Report / folder) to the cache key (F-21c)."""
    if not path:
        return None
    try:
        from core.utilities.pbip_utils import resolve_pbip_report_path

        resolved = resolve_pbip_report_path(path)
    except Exception:  # noqa: BLE001
        resolved = None
    key = resolved or path
    try:
        return os.path.normcase(os.path.abspath(key))
    except (TypeError, ValueError):
        return key


def bust_vqb_cache(pbip_path: Optional[str] = None) -> int:
    """Drop cached VisualQueryBuilder entries.

    Called by the lifecycle's reopen path so disk edits between iterations
    of the debug loop are reflected in the next filter-context read.
    ``pbip_path`` may be a ``.pbip`` file, a ``.Report`` folder or any path
    inside the report -- it is normalised to the same key the cache uses
    (the old substring match against a ``.pbip`` path dropped nothing).
    Returns the number of entries dropped.
    """
    with _vqb_cache_lock:
        if pbip_path is None:
            n = len(_vqb_cache)
            _vqb_cache.clear()
            return n
        key = _normalize_cache_key(pbip_path)
        try:
            raw = os.path.normcase(os.path.abspath(pbip_path))
        except (TypeError, ValueError):
            raw = ""
        targets = [
            k for k in _vqb_cache if k == key or (raw and (raw == k or raw.startswith(k + os.sep)))
        ]
        for k in targets:
            _vqb_cache.pop(k, None)
        return len(targets)


def _on_pbir_written(path: str) -> None:
    """pbip_utils write listener: drop the builder of the report that changed."""
    try:
        bust_vqb_cache(path)
    except Exception:  # noqa: BLE001 — a cache hook must never fail a write
        pass


try:
    from core.utilities.pbip_utils import register_write_listener as _register_write_listener

    _register_write_listener(_on_pbir_written)
except Exception:  # pragma: no cover - optional hook
    pass


def _get_visual_query_builder():
    """Get VisualQueryBuilder instance with a freshness-checked cache.

    Caches the builder per report for ``_VQB_CACHE_TTL`` seconds, but only
    while the report definition is unchanged: every hit re-checks the
    definition folder's signature (newest mtime + file count, at most every
    ``_VQB_SIGNATURE_RECHECK_S``), so a Desktop save, a 07_*/11_* edit or a
    debug_loop fix is picked up on the very next call (F-21c). PBIR writes
    through ``pbip_utils`` also bust the entry immediately via a write listener.
    """

    pbip_folder = connection_state.get_pbip_folder_path()
    if not pbip_folder:
        return None, (
            "PBIP folder not available. Either open a .pbip "
            "project in Power BI Desktop, or call 09_Debug_Operations "
            "operation=set_path (pbip_path=...) to specify the path manually."
        )

    key = _normalize_cache_key(pbip_folder) or pbip_folder
    now = time.time()
    with _vqb_cache_lock:
        cached = _vqb_cache.get(key)
        if cached is not None:
            builder, ts, signature, checked_at = cached
            if (now - ts) < _VQB_CACHE_TTL:
                if now - checked_at < _VQB_SIGNATURE_RECHECK_S:
                    return builder, None
                current = _definition_signature(getattr(builder, "definition_path", None))
                if current == signature:
                    _vqb_cache[key] = (builder, ts, signature, now)
                    return builder, None
            _vqb_cache.pop(key, None)

        # Path changed — clear stale entries for other paths
        if _vqb_cache and key not in _vqb_cache:
            _vqb_cache.clear()

    try:
        from core.debug.visual_query_builder import VisualQueryBuilder

        builder = VisualQueryBuilder(pbip_folder)
        signature = _definition_signature(getattr(builder, "definition_path", None))
        with _vqb_cache_lock:
            _vqb_cache[key] = (builder, now, signature, now)
        return builder, None
    except Exception as e:
        return None, f"Error initializing VisualQueryBuilder: {e}"


def _discover_visuals(
    args: Dict[str, Any], builder: Any, compact: bool
) -> Optional[Dict[str, Any]]:
    """Handle discovery when page/visual not fully specified.

    Returns a discovery response dict if page or visual is missing,
    or None if both are specified and valid.
    """
    page_name = args.get("page_name")
    visual_id = args.get("visual_id")
    visual_name = args.get("visual_name")

    # If page_name not provided, list available pages
    if not page_name:
        pages = builder.list_pages()
        return error_envelope(
            "missing_argument",
            "page_name required",
            pages=_compact_page_list(pages, compact),
        )

    # Check if page exists
    pages = builder.list_pages()
    page_exists = any(p["name"].lower() == page_name.lower() for p in pages)
    if not page_exists:
        return error_envelope(
            "not_found",
            f"Page '{page_name}' not found",
            pages=_compact_page_list(pages, compact),
        )

    # If visual_id/visual_name not provided, list visuals on page
    if not visual_id and not visual_name:
        visuals = builder.list_visuals(page_name)
        non_slicer = [v for v in visuals if not v.get("is_slicer")]
        slicer = [v for v in visuals if v.get("is_slicer")]
        return error_envelope(
            "missing_argument",
            "visual_id or visual_name required",
            page=page_name,
            visuals=_compact_visual_list(non_slicer, compact),
            slicers=(
                [
                    {
                        "id": v.get("id"),
                        "field": (v.get("columns", ["?"])[0] if v.get("columns") else "?"),
                    }
                    for v in slicer
                ]
                if slicer
                else None
            ),
        )

    return None  # All specified — proceed with main logic
