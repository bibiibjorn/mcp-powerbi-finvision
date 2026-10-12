"""DAX Studio-format exports and the capture summary for 13_Query_Plan.

Plans routinely run to 10k+ rows / 12 MB. Inlining one into a tool response
would blow the context budget for the rest of the session, so a capture writes
the full artefacts to disk and returns a summary; the rows themselves are read
back a page at a time via the ``plan_rows`` op.
"""

from __future__ import annotations

import logging
import os
import shutil
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: Repo-root/exports/perf/<capture_id>/ — alongside the other export lanes.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(_THIS_DIR))
PERF_EXPORT_ROOT = os.path.join(PROJECT_ROOT, "exports", "perf")

#: A summary that names the worst rows is actionable; one that names all of them
#: is a plan dump by another route.
_CENSUS_LIMIT = 20
_TOP_RECORDS_LIMIT = 10


def export_dir(capture_id: str) -> str:
    return os.path.join(PERF_EXPORT_ROOT, capture_id)


def write_exports(capture: Dict[str, Any], *, dax_query: str) -> Dict[str, Any]:
    """Write ServerTimings.json, QueryPlan.json and capture.daxx.

    Returns the paths written plus any error, never raising: a capture whose
    numbers are good but whose disk write failed is still worth returning.
    """
    capture_id = capture.get("capture_id") or "unknown"
    daxstudio = capture.get("daxstudio") or {}
    server_timings = daxstudio.get("server_timings") or {}
    query_plan = daxstudio.get("query_plan") or {}
    if not daxstudio and capture.get("_spilled"):
        # A slim (spilled) capture: the DAX Studio payloads are already on disk.
        server_timings = _read_json(os.path.join(export_dir(capture_id), "ServerTimings.json"))
        query_plan = _read_json(os.path.join(export_dir(capture_id), "QueryPlan.json"))

    if not server_timings and not query_plan:
        return {
            "written": False,
            "reason": (
                "No DAX Studio payload: the capture ran on the DaxExecutor.exe "
                "fallback lane, which emits neither plans nor ExecutionMetrics."
            ),
        }

    target = export_dir(capture_id)
    try:
        os.makedirs(target, exist_ok=True)
        paths: Dict[str, str] = {}
        if server_timings:
            paths["server_timings"] = _write_json(
                os.path.join(target, "ServerTimings.json"), server_timings
            )
        if query_plan:
            paths["query_plan"] = _write_json(os.path.join(target, "QueryPlan.json"), query_plan)
        paths["daxx"] = write_daxx_package(
            os.path.join(target, "capture.daxx"),
            query_text=dax_query,
            server_timings=server_timings or None,
            query_plan=query_plan or None,
        )
        pruned = prune_perf_exports(keep=capture_id)
        return {"written": True, "dir": target, **paths, **({"pruned": pruned} if pruned else {})}
    except OSError as e:
        logger.warning("perf export failed for %s: %s", capture_id, e)
        return {"written": False, "reason": str(e)}


def _dir_size(path: str) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                continue
    return total


def prune_perf_exports(
    root: Optional[str] = None,
    *,
    max_captures: Optional[int] = None,
    max_mb: Optional[float] = None,
    keep: Optional[str] = None,
) -> List[str]:
    """Retention for ``exports/perf`` (C-11 / K-07): keep the newest ``max_captures`` capture
    folders, then drop the oldest until the total is within ``max_mb``.

    Defaults come from ``performance.perf_export_max_captures`` (30) and
    ``performance.perf_export_max_mb`` (500); ``<= 0`` disables that limit. The ``keep`` capture
    (the one just written) is never removed, even when it alone exceeds the size cap. Returns the
    removed folder names; never raises.
    """
    removed: List[str] = []
    try:
        root = root if root is not None else PERF_EXPORT_ROOT
        if max_captures is None or max_mb is None:
            from core.config.config_manager import config

            if max_captures is None:
                max_captures = int(config.get("performance.perf_export_max_captures", 30) or 0)
            if max_mb is None:
                max_mb = float(config.get("performance.perf_export_max_mb", 500) or 0)
        entries = []
        with os.scandir(root) as it:
            for e in it:
                if e.is_dir(follow_symlinks=False):
                    try:
                        entries.append((e.stat().st_mtime, e.name, e.path))
                    except OSError:
                        continue
        entries.sort(reverse=True)  # newest first
        sizes = {path: _dir_size(path) for _m, _n, path in entries} if max_mb > 0 else {}
        total = sum(sizes.values())
        limit = int(max_mb * 1024 * 1024) if max_mb > 0 else 0
        # Oldest first: entries are newest-first, so walk them in reverse. Count overflow is the
        # tail; a size overflow trims further into the kept range.
        for idx in range(len(entries) - 1, -1, -1):
            _mtime, name, path = entries[idx]
            if name == keep:
                continue
            over_count = max_captures > 0 and idx >= max_captures
            over_size = limit > 0 and total > limit
            if not (over_count or over_size):
                break
            try:
                shutil.rmtree(path)
            except OSError as e:
                logger.warning("perf export prune failed for %s: %s", path, e)
                continue
            total -= sizes.get(path, 0)
            removed.append(name)
    except Exception as e:  # noqa: BLE001 - retention must never fail a capture
        logger.debug("perf export retention skipped: %s", e)
    return removed


#: [Content_Types].xml of an OPC package (System.IO.Packaging). Without it
#: ``Package.Open`` sees ZERO parts, so DAX Studio opened our .daxx as an empty
#: document (F-11). One Default per extension we write.
_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="dax" ContentType="text/plain" />'
    '<Default Extension="json" ContentType="application/json" />'
    "</Types>"
)


def write_daxx_package(
    path: str,
    *,
    query_text: str,
    server_timings: Optional[Dict[str, Any]] = None,
    query_plan: Optional[Dict[str, Any]] = None,
    model_info: Optional[Dict[str, Any]] = None,
) -> str:
    """Write a ``.daxx`` as a proper OPC package DAX Studio's packaging API opens.

    Same parts as ``pbi_perf_core.daxstudio_export.write_daxx`` (``Query.dax``,
    ``ServerTimings.json``, ``QueryPlan.json``, ``ModelInfo.json``) plus the
    mandatory ``[Content_Types].xml`` part, written first. Parts are stored
    UTF-8 (``Query.dax`` with a BOM, as DAX Studio writes it). The temp file is
    swapped in atomically so a reader never sees a half-written package.
    """
    import json
    import zipfile

    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    parts = [("Query.dax", "\ufeff" + (query_text or ""))]
    for name, payload in (
        ("ServerTimings.json", server_timings),
        ("QueryPlan.json", query_plan),
        ("ModelInfo.json", model_info),
    ):
        if payload is not None:
            parts.append((name, json.dumps(payload, indent=2, ensure_ascii=False, default=str)))
    tmp = f"{path}.{os.getpid()}.tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("[Content_Types].xml", _CONTENT_TYPES)
        for name, text in parts:
            bundle.writestr(name, text.encode("utf-8"))
    os.replace(tmp, path)
    return path


def _read_json(path: str) -> Dict[str, Any]:
    import json

    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: str, payload: Dict[str, Any]) -> str:
    import json

    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False, default=str)
    return path


def build_capture_summary(
    capture: Dict[str, Any],
    exports: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """The response body for ``capture``: headline + shape, never the plan text."""
    from pbi_perf_core.query_plan import analyse_plan

    from core.query_plan.capturer import parsed_plan

    physical = parsed_plan(capture, "physical")
    logical = parsed_plan(capture, "logical")
    timings = capture.get("se_fe_timings") or {}
    se_queries = timings.get("se_queries")

    analysis = capture.get("plan_analysis") or analyse_plan(
        physical,
        logical,
        result_rows=timings.get("result_rows"),
        se_query_count=se_queries,
        top_n=_CENSUS_LIMIT,
    )

    # F-23: two DIFFERENT counts, two names. ``highlighted_rows`` is DAX Studio's
    # highlight (datacache producers AND consumers: Cache:, VertipaqResult:,
    # DirectQueryResult, DataPostFilter:); ``cache_consumers`` counts ``Cache:``
    # operators only -- the same number analyse_plan reports.
    highlighted_rows = physical.highlight_count
    cache_consumers = analysis.get("cache_consumers")
    if cache_consumers is None:
        cache_consumers = sum(n for op, n in physical.operator_census() if op.startswith("Cache:"))

    summary: Dict[str, Any] = {
        "capture_id": capture.get("capture_id"),
        "query_hash": capture.get("query_hash"),
        "captured_at": capture.get("captured_at"),
        "runner_lane": capture.get("runner_lane", "unknown"),
        "cache_cleared": capture.get("cache_cleared", False),
        "headline": {
            "total_ms": timings.get("total_ms", 0),
            "fe_ms": timings.get("fe_ms", 0),
            "se_ms": timings.get("se_ms", 0),
            "fe_pct": timings.get("fe_pct", 0),
            "se_pct": timings.get("se_pct", 0),
            "se_cpu_ms": timings.get("se_cpu_ms", 0),
            "total_cpu_ms": capture.get("total_cpu_ms", 0),
            "se_net_parallel_ms": capture.get("se_net_parallel_ms", 0),
            "se_queries": se_queries or 0,
            "se_cache_hits": timings.get("se_cache_hits", 0),
            "dq_ms": capture.get("dq_ms", 0),
            "parallel_se_detected": capture.get("parallel_se_detected", False),
            "result_rows": timings.get("result_rows"),
        },
        "execution_metrics": capture.get("execution_metrics") or {},
        "result_sets": capture.get("result_sets") or [],
        "plan_shape": {
            "physical_rows": len(physical),
            "logical_rows": len(logical),
            "max_records": physical.max_records,
            "cache_consumers": cache_consumers,
            "highlighted_rows": highlighted_rows,
            "se_query_count": se_queries or 0,
            "callback_scans": capture.get("callback_count", 0),
        },
        "operator_census": [
            {"operator": op, "count": n} for op, n in physical.operator_census(_CENSUS_LIMIT)
        ],
        "top_records": [
            {
                "row_number": row.row_number,
                "level": row.level,
                "records": row.records,
                "operation": row.operation[:200],
            }
            for row in physical.top_records(_TOP_RECORDS_LIMIT)
        ],
        "findings": analysis.get("findings", []),
        "agg_rewrites": capture.get("agg_rewrites", []),
    }
    if exports is not None:
        summary["exports"] = exports
    if not len(physical) and not len(logical):
        summary["note"] = (
            "No query plan was captured. On the exe fallback lane this is "
            "expected — DaxExecutor.exe predates plan capture and cannot be "
            "rebuilt without a .NET SDK."
        )
    return summary


def page_plan_rows(
    capture: Dict[str, Any],
    *,
    plan: str = "physical",
    offset: int = 0,
    limit: int = 100,
    min_records: Optional[int] = None,
) -> Dict[str, Any]:
    """Read one window of plan rows back out of a capture (parsed once, F-34)."""
    from core.query_plan.capturer import parsed_plan

    if plan not in ("physical", "logical"):
        return {
            "success": False,
            "error": "plan must be 'physical' or 'logical'",
            "error_type": "invalid_args",
        }

    parsed = parsed_plan(capture, plan)

    rows = parsed.rows
    if min_records is not None:
        rows = [r for r in rows if (r.records or 0) >= min_records]

    offset = max(0, int(offset))
    limit = max(1, min(int(limit), 500))
    window = rows[offset : offset + limit]

    return {
        "success": True,
        "capture_id": capture.get("capture_id"),
        "plan": plan,
        "total_rows": len(parsed),
        "matched_rows": len(rows),
        "offset": offset,
        "limit": limit,
        "returned": len(window),
        "has_more": offset + len(window) < len(rows),
        "rows": [
            {
                "row_number": r.row_number,
                "level": r.level,
                "records": r.records,
                "operation": r.operation,
                "next_sibling_row_number": r.next_sibling_row_number,
                "highlight_row": r.highlight_row,
            }
            for r in window
        ],
    }
