"""Capture DAX query plans (logical/physical) + xmSQL via a per-call trace.

Returns a dict shaped for the parser; callers pass it to
core.query_plan.parser.parse_logical_plan / parse_xmsql.
"""

from __future__ import annotations

import hashlib
import itertools
import logging
import os
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVENT_CLASSES = (
    "DAXQueryPlan",
    "DAXVertiPaqSEQuery",
    "DAXEvaluationLog",
    "DirectQueryEnd",
    "AggregateTableRewriteQuery",
    "VertiPaqSEQueryCacheMatch",
    "VertiPaqSEQueryCacheRemoval",
)
_MAX_AGG_REWRITES = 50
_CAPTURE_COUNTER = itertools.count(1)


def _query_hash(dax_query: str, clear_cache: bool) -> str:
    return hashlib.sha256(f"{int(clear_cache)}|{dax_query}".encode("utf-8")).hexdigest()[:12]


def _capture_id(dax_query: str, clear_cache: bool) -> str:
    """UNIQUE per capture (F-08): ``<query hash>-<UTC time>-<counter>``.

    The old id was the hash alone, so capturing the same query before and after
    a model edit overwrote the "before" capture (and its exports/perf folder)
    and ``compare`` diffed a capture against itself. The hash is still in the
    id (and in ``query_hash``) so captures of one query group together.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{_query_hash(dax_query, clear_cache)}-{stamp}-{next(_CAPTURE_COUNTER):04d}"


def _native_capture(
    dax_query: str,
    clear_cache: bool,
    capture_eval_log: bool = False,
    capture_plan: bool = True,
) -> Dict[str, Any]:
    """Capture plans + timings via the native trace runner (in-process AMO lane first).

    This is the same lane 04_Run_DAX and 09_Debug_Operations use — real AMO trace
    subscription, real ClearCache XMLA, interval-union SE duration.
    Returns ``{"_error": ...}`` when no lane can run so the caller can report honestly.
    """
    try:
        from core.infrastructure.connection_state import connection_state
        from core.infrastructure.query_trace import NativeTraceRunner

        if not NativeTraceRunner.is_available():
            return {
                "_error": (
                    "No trace lane available: the in-process AMO trace could not load "
                    "(pythonnet / AMO assemblies) and the DaxExecutor.exe fallback is not "
                    "built. Check 01_Connection operation=status for the AMO build."
                )
            }
        conn_str = getattr(connection_state.connection_manager, "connection_string", None)
        if not conn_str:
            return {"_error": "No connection string available"}
        kwargs: Dict[str, Any] = {}
        if capture_eval_log:
            kwargs["capture_eval_log"] = True
        if not capture_plan:
            kwargs["capture_plan"] = False
        return NativeTraceRunner(conn_str).execute_with_trace(dax_query, clear_cache, **kwargs)
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("native trace capture failed: %s", e)
        return {"_error": str(e)}


# SSAS exposes AggregateTableRewriteQuery EventSubclass as either an integer
# (live CLR trace) or a human-readable string (Profiler / serialized JSON).
# Map both shapes to a stable {matched|nomatch|attempted} token.
# Subclass IDs verified against the Microsoft Trace Events documentation.
_AGG_SUBCLASS_INT_MAP = {
    1: "matched",  # MatchFound
    2: "nomatch",  # NoMatch
}


def _classify_agg_subclass(subclass: Any) -> str:
    """Return matched|nomatch|attempted for AggregateTableRewriteQuery subclass."""
    if isinstance(subclass, bool):
        # bool is a subclass of int; treat as attempted to avoid false positives.
        return "attempted"
    if isinstance(subclass, int):
        return _AGG_SUBCLASS_INT_MAP.get(subclass, "attempted")
    raw = str(subclass or "").strip()
    if raw.isdigit():
        return _AGG_SUBCLASS_INT_MAP.get(int(raw), "attempted")
    low = raw.lower()
    if "no" in low and "match" in low:
        return "nomatch"
    if "match" in low:
        return "matched"
    return "attempted"


def _map_agg_rewrites(native: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The native lane's AggregateTableRewriteQuery rows -> {status, agg, subclass} (F-24).

    pbi_perf_core keeps ``{text, start}`` and drops the subclass today, so the
    status is ``attempted`` unless a subclass is present (see the
    pbi-perf-core patch from the 2026-09-22 deep review).
    """
    out: List[Dict[str, Any]] = []
    for row in native.get("agg_rewrites") or []:
        if not isinstance(row, dict):
            continue
        subclass = row.get("subclass", row.get("status"))
        text = row.get("text") or row.get("agg") or row.get("query") or ""
        out.append(
            {
                "status": (
                    row["status"]
                    if row.get("status") in ("matched", "nomatch", "attempted")
                    else _classify_agg_subclass(subclass)
                ),
                "agg": str(text)[:500],
                "subclass": subclass,
            }
        )
        if len(out) >= _MAX_AGG_REWRITES:
            break
    return out


def capture(
    dax_query: str,
    clear_cache: bool = True,
    include_xmsql: bool = True,
    capture_eval_log: bool = False,
) -> Dict[str, Any]:
    """Capture a single DAX query's plans + timings."""
    from core.infrastructure.connection_state import connection_state

    if not connection_state.is_connected():
        return {
            "success": False,
            "error": "Not connected to Power BI Desktop",
            "error_type": "not_connected",
        }

    executor = getattr(connection_state, "query_executor", None) or getattr(
        connection_state, "dax_query_executor", None
    )
    if executor is None:
        return {
            "success": False,
            "error": "DAX executor not available",
            "error_type": "not_connected",
        }

    # NOTE: cache clearing is done by the native runner itself, via a real
    # ClearCache XMLA batch. Passed only when requested: eval-log capture is a
    # keyword-only extension and the test doubles for this hook are written
    # against the original two-argument shape.
    native = (
        _native_capture(dax_query, clear_cache, capture_eval_log=True)
        if capture_eval_log
        else _native_capture(dax_query, clear_cache)
    )
    if native.get("_error"):
        return {
            "success": False,
            "error": f"Plan capture failed: {native['_error']}",
            "error_type": native.get("_error_type") or "trace_unavailable",
        }

    logical_raw = native.get("logical_plan", "") or ""
    physical_raw = native.get("physical_plan", "") or ""
    xmsql_chunks: List[str] = []
    if include_xmsql:
        xmsql_chunks = [
            ev.get("query", "") for ev in native.get("se_events", []) if ev.get("query")
        ]

    timings: Dict[str, Any] = {
        k: native.get(k)
        for k in (
            "total_ms",
            "fe_ms",
            "se_ms",
            "se_cpu_ms",
            "se_parallelism",
            "se_queries",
            "se_cache_hits",
            "fe_pct",
            "se_pct",
            "se_net_parallel_ms",
            "total_cpu_ms",
            "dq_ms",
            "parallel_se_detected",
        )
    }
    timings["se_events"] = native.get("se_events", [])
    # `records_blowup` compares max #Records against the real result row count.
    # A visual's query often opens with a small title/label EVALUATE before the
    # matrix itself, so take the LARGEST result set -- otherwise a 1-row caption
    # makes every capture look like a blow-up.
    result_sets = native.get("result_sets") or []
    result_rows: Optional[int] = None
    if result_sets:
        result_rows = max((rs.get("row_count") or 0) for rs in result_sets)
        timings["result_rows"] = result_rows

    # F-24: DirectQuery + cache-removal counts come from the native timeline (the
    # old per-call hook was a stub that never yielded events).
    timeline = native.get("timeline") or []
    dq_query_count = sum(
        1 for ev in timeline if isinstance(ev, dict) and ev.get("class") == "DirectQueryEnd"
    )
    se_cache_removal_count = sum(
        1
        for ev in timeline
        if isinstance(ev, dict) and ev.get("class") == "VertiPaqSEQueryCacheRemoval"
    )

    # F-23: recompute the plan analysis with the LARGEST result set; the runner's
    # own analysis used the first set's row count.
    plan_analysis = native.get("plan_analysis") or {}
    try:
        from pbi_perf_core.query_plan import analyse_plan, parse_plan

        physical_parsed = native.get("_physical_plan_parsed") or parse_plan(
            physical_raw, "physical"
        )
        logical_parsed = native.get("_logical_plan_parsed") or parse_plan(logical_raw, "logical")
        plan_analysis = analyse_plan(
            physical_parsed,
            logical_parsed,
            result_rows=result_rows,
            se_query_count=native.get("se_queries"),
        )
    except Exception as e:  # noqa: BLE001 — keep the runner's analysis
        logger.debug("plan re-analysis failed: %s", e)

    cap_id = _capture_id(dax_query, clear_cache)
    # CallbackDataID detection: post-process xmSQL chunks for the substring.
    # CallbackDataID indicates formula-engine fallback inside SE — a known
    # perf antipattern (often from non-vectorisable expressions in iterators).
    callback_count = sum(1 for q in xmsql_chunks if "callbackdataid" in (q or "").lower())
    return {
        "success": True,
        "capture_id": cap_id,
        "query_hash": _query_hash(dax_query, clear_cache),
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # Kept so a later export_daxx can bundle the query it belongs to.
        "dax_query": dax_query,
        "logical_plan_raw": logical_raw,
        "physical_plan_raw": physical_raw,
        "xmsql_queries": xmsql_chunks,
        "evaluation_log": list(native.get("evaluation_log") or []),
        "eval_log_subscribed": native.get("eval_log_subscribed"),
        "se_fe_timings": timings,
        # Extended trace coverage
        "dq_query_count": dq_query_count,
        "dq_total_ms": int(native.get("dq_ms", 0) or 0),
        "se_cache_match_count": int(native.get("se_cache_hits", 0) or 0),
        "se_cache_removal_count": se_cache_removal_count,
        "agg_rewrites": _map_agg_rewrites(native),
        "callback_count": callback_count,
        # Whether the SE cache was genuinely dropped for this capture. When False
        # the numbers are warm-cache numbers — do not use them as a baseline.
        "cache_cleared": bool(native.get("cache_cleared")),
        # DAX Studio-parity artefacts (7.15.0). Empty on the exe fallback lane.
        "execution_metrics": native.get("execution_metrics") or {},
        "result_sets": result_sets,
        "daxstudio": native.get("daxstudio") or {},
        "plan_analysis": plan_analysis,
        "plan_rows": native.get("plan_rows") or {},
        "parallel_se_detected": bool(native.get("parallel_se_detected")),
        "se_net_parallel_ms": native.get("se_net_parallel_ms", 0),
        "total_cpu_ms": native.get("total_cpu_ms", 0),
        "dq_ms": native.get("dq_ms", 0),
        "runner_lane": native.get("runner_lane", "unknown"),
        "correlation": native.get("correlation") or {},
    }


# ---------------------------------------------------------------------------
# Capture store (F-34: bounded by BYTES, raw plans spilled to disk)
# ---------------------------------------------------------------------------

_CAPTURE_STORE: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
_CAPTURE_LOCK = threading.Lock()
_MAX_CAPTURES = 32
#: Keys too large to keep for 32 captures (raw plans reach 12 MB each).
_SPILLABLE_KEYS = ("physical_plan_raw", "logical_plan_raw")
_DROP_ON_SPILL = ("daxstudio",)
_PARSED_CACHE: "OrderedDict[tuple, Any]" = OrderedDict()
_PARSED_CACHE_MAX = 4


def remember(capture: Dict[str, Any], spill_dir: Optional[str] = None) -> None:
    """Store a capture for later parse/analyze/compare calls.

    T0-4 thread safety: the dispatcher's thread pool can run two
    13_Query_Plan:capture calls concurrently. Without the lock the eviction
    `pop(next(iter(_CAPTURE_STORE)))` races against insertion in another
    thread and raises ``RuntimeError: dictionary changed size during
    iteration``. A simple Lock around mutation is sufficient because the
    critical section is tiny.

    F-34: with ``spill_dir`` (the capture's exports folder) the raw plan texts
    are written there and only their paths are kept in memory; the DAX Studio
    payload (already on disk as ServerTimings.json / QueryPlan.json) is dropped.
    """
    cid = capture.get("capture_id")
    if not cid:
        return
    stored = capture
    if spill_dir:
        stored = dict(capture)
        spilled: Dict[str, str] = {}
        try:
            os.makedirs(spill_dir, exist_ok=True)
            for key in _SPILLABLE_KEYS:
                text = stored.get(key)
                if not text:
                    continue
                path = os.path.join(spill_dir, f"{key.replace('_raw', '')}.txt")
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(text)
                spilled[key] = path
                stored[key] = None
            for key in _DROP_ON_SPILL:
                stored.pop(key, None)
            stored["_spilled"] = spilled
        except OSError as e:
            logger.warning("capture spill failed for %s: %s", cid, e)
            stored = capture
    with _CAPTURE_LOCK:
        _CAPTURE_STORE[cid] = stored
        _CAPTURE_STORE.move_to_end(cid)
        while len(_CAPTURE_STORE) > _MAX_CAPTURES:
            _CAPTURE_STORE.popitem(last=False)


def recall(capture_id: str) -> Optional[Dict[str, Any]]:
    """Look up a capture by id. Read under the same lock for memory consistency."""
    with _CAPTURE_LOCK:
        return _CAPTURE_STORE.get(capture_id)


def plan_text(capture: Dict[str, Any], key: str) -> str:
    """Raw plan text of a capture, in memory or spilled to disk (F-34)."""
    text = capture.get(key)
    if text:
        return text
    path = (capture.get("_spilled") or {}).get(key)
    if not path:
        return ""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError as e:
        logger.warning("spilled plan %s unreadable: %s", path, e)
        return ""


def parsed_plan(capture: Dict[str, Any], kind: str):
    """``pbi_perf_core`` QueryPlan for a capture, parsed once and cached (F-34)."""
    from pbi_perf_core.query_plan import parse_plan

    text = plan_text(capture, f"{kind}_plan_raw")
    key = (capture.get("capture_id"), kind, len(text), hash(text))
    with _CAPTURE_LOCK:
        hit = _PARSED_CACHE.get(key)
        if hit is not None:
            _PARSED_CACHE.move_to_end(key)
            return hit
    parsed = parse_plan(text, kind)
    if key[0]:
        with _CAPTURE_LOCK:
            _PARSED_CACHE[key] = parsed
            while len(_PARSED_CACHE) > _PARSED_CACHE_MAX:
                _PARSED_CACHE.popitem(last=False)
    return parsed


def _stats(values: List[float]) -> Dict[str, Any]:
    """avg/stddev/min/max/median for one run group.

    ``stddev_ms`` is the POPULATION standard deviation (n in the denominator);
    ``stddev_kind`` says so.
    """
    if not values:
        return {}
    ordered = sorted(values)
    n = len(ordered)
    mean = sum(ordered) / n
    median = ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2
    return {
        "runs": n,
        "avg_ms": round(mean, 1),
        "stddev_ms": round((sum((x - mean) ** 2 for x in ordered) / n) ** 0.5, 1),
        "stddev_kind": "population",
        "min_ms": ordered[0],
        "max_ms": ordered[-1],
        "median_ms": median,
    }


def _baseline_summary(values: List[float]) -> Dict[str, Any]:
    """Fastest run + spread, the same protocol as the wall-clock lane (F-22)."""
    if not values:
        return {}
    ordered = sorted(values)
    fastest = ordered[0]
    spread = round((ordered[-1] - fastest) / fastest * 100, 1) if fastest else 0.0
    out: Dict[str, Any] = {"baseline_ms": fastest, "spread_pct": spread}
    if spread > 20:
        out["warning"] = (
            f"Run-to-run spread is {spread}% (>20%). Add more runs before trusting this "
            "as a baseline."
        )
    return out


def _progress(done: int, total: int, message: str) -> None:
    """F-26: per-run progress notification (no-op without a progress token / server layer)."""
    try:
        from server.progress import emit_progress

        emit_progress(done, total, message)
    except Exception:  # pragma: no cover - server layer absent
        pass


def _should_stop(deadline: Optional[float]) -> bool:
    try:
        from server.request_context import is_cancelled

        if is_cancelled():
            return True
    except Exception:  # pragma: no cover - server layer absent
        pass
    return deadline is not None and time.monotonic() > deadline


def _engine_duration_ms(native: Dict[str, Any]) -> Optional[float]:
    """``execution_metrics.duration_ms`` of a traced run (the engine's own duration), or None."""
    metrics = native.get("execution_metrics")
    value = metrics.get("duration_ms") if isinstance(metrics, dict) else None
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _run_row(native: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "duration_ms": _engine_duration_ms(native),
        "total_ms": native.get("total_ms", 0),
        "fe_ms": native.get("fe_ms", 0),
        "se_ms": native.get("se_ms", 0),
        "se_cpu_ms": native.get("se_cpu_ms", 0),
        "se_queries": native.get("se_queries", 0),
        "se_cache_hits": native.get("se_cache_hits", 0),
        "cache_cleared": native.get("cache_cleared", False),
    }


def benchmark_traced(
    dax_query: str,
    cold_runs: int = 3,
    warm_runs: int = 3,
    warmup: int = 1,
    time_budget_s: Optional[float] = None,
) -> Dict[str, Any]:
    """Traced benchmark: N cold runs then M warm runs, with per-run SE/FE.

    Cold and warm are reported separately rather than pooled — they measure
    different things (scan cost vs cache-hit latency) and averaging across the
    two produces a number that describes neither. ``cold_cache_honoured`` is
    False when any "cold" run could not actually clear the SE cache (F-22).
    Runs stop at the time budget / on cancellation (F-14) and say so.
    """
    from core.infrastructure.connection_state import connection_state

    if not connection_state.is_connected():
        return {
            "success": False,
            "error": "Not connected to Power BI Desktop",
            "error_type": "not_connected",
        }

    cold_runs = max(0, min(int(cold_runs), 20))
    warm_runs = max(0, min(int(warm_runs), 20))
    warmup = max(0, min(int(warmup), 5))
    if cold_runs + warm_runs == 0:
        return {
            "success": False,
            "error": "cold_runs + warm_runs must be at least 1",
            "error_type": "invalid_args",
        }
    deadline = time.monotonic() + time_budget_s if time_budget_s else None

    for _ in range(warmup):
        if _should_stop(deadline):
            break
        _native_capture(dax_query, False)

    groups: Dict[str, List[Dict[str, Any]]] = {"cold": [], "warm": []}
    errors: List[str] = []
    not_run = 0
    total_runs = cold_runs + warm_runs
    done_runs = 0
    for phase, count in (("cold", cold_runs), ("warm", warm_runs)):
        for n in range(count):
            if _should_stop(deadline):
                not_run += 1
                continue
            _progress(done_runs, total_runs, f"{phase} run {n + 1} of {count}")
            done_runs += 1
            native = _native_capture(dax_query, phase == "cold")
            if native.get("_error"):
                errors.append(native["_error"])
                continue
            groups[phase].append(_run_row(native))

    if not groups["cold"] and not groups["warm"]:
        return {
            "success": False,
            "error": "no successful traced runs",
            "error_type": "trace_unavailable",
            "errors": errors,
        }

    def _group(phase: str) -> Dict[str, Any]:
        rows = groups[phase]
        totals = [r["total_ms"] for r in rows]
        # S-31: the engine's own execution_metrics.duration_ms is the PRIMARY number (the
        # same one 04_Run_DAX mode=trace / 09_Debug report); total_ms (trace window) is secondary.
        durations = [r["duration_ms"] for r in rows if r.get("duration_ms") is not None]
        use_engine = bool(rows) and len(durations) == len(rows)
        primary = durations if use_engine else totals
        out = {
            "primary_metric": "execution_metrics.duration_ms" if use_engine else "total_ms",
            "primary_ms": _stats(primary),
            "runs": rows,
            "total_ms": _stats(totals),
            "se_ms": _stats([r["se_ms"] for r in rows]),
            "fe_ms": _stats([r["fe_ms"] for r in rows]),
        }
        if use_engine:
            out["duration_ms"] = _stats(durations)
        if rows:
            out["summary"] = _baseline_summary(primary)
        return out

    result = {
        "success": True,
        "cold": _group("cold"),
        "warm": _group("warm"),
        "warmup_runs": warmup,
        # None when there were no cold runs; False = at least one "cold" run was warm.
        "cold_cache_honoured": (
            all(bool(r["cache_cleared"]) for r in groups["cold"]) if groups["cold"] else None
        ),
        "errors": errors,
    }
    if not_run:
        result["runs_not_executed"] = not_run
        result["warning"] = (
            f"{not_run} run(s) were not executed: the time budget ran out or the call "
            "was cancelled. Lower cold_runs/warm_runs."
        )
    return result


def benchmark_ab(
    dax_query_a: str,
    dax_query_b: str,
    runs: int = 5,
    warmup: int = 1,
    time_budget_s: Optional[float] = None,
) -> Dict[str, Any]:
    """Interleaved A/B benchmark: A, B, B, A, A, B ... each run cold + traced (F-22, F-39(5)).

    Alternating the order cancels the drift (thermal, background work) that
    makes "A first, then B" systematically favour one side; the SE cache is
    cleared before every run so neither side reuses the other's datacaches.
    """
    from core.infrastructure.connection_state import connection_state

    if not connection_state.is_connected():
        return {
            "success": False,
            "error": "Not connected to Power BI Desktop",
            "error_type": "not_connected",
        }
    runs = max(1, min(int(runs), 20))
    warmup = max(0, min(int(warmup), 5))
    deadline = time.monotonic() + time_budget_s if time_budget_s else None
    queries = {"a": dax_query_a, "b": dax_query_b}

    for _ in range(warmup):
        for side in ("a", "b"):
            if _should_stop(deadline):
                break
            _native_capture(queries[side], False)

    rows: Dict[str, List[Dict[str, Any]]] = {"a": [], "b": []}
    order: List[str] = []
    errors: List[str] = []
    not_run = 0
    for i in range(runs):
        _progress(i, runs, f"A/B pair {i + 1} of {runs}")
        pair = ("a", "b") if i % 2 == 0 else ("b", "a")
        for side in pair:
            if _should_stop(deadline):
                not_run += 1
                continue
            native = _native_capture(queries[side], True)
            order.append(side)
            if native.get("_error"):
                errors.append(f"{side}: {native['_error']}")
                continue
            rows[side].append(_run_row(native))

    if not rows["a"] or not rows["b"]:
        return {
            "success": False,
            "error": "each side needs at least one successful traced run",
            "error_type": "trace_unavailable",
            "errors": errors,
        }
    a_totals = [r["total_ms"] for r in rows["a"]]
    b_totals = [r["total_ms"] for r in rows["b"]]
    a_best, b_best = min(a_totals), min(b_totals)
    all_rows = rows["a"] + rows["b"]
    result: Dict[str, Any] = {
        "success": True,
        "mode": "ab_interleaved",
        "order": order,
        "a": {"runs": rows["a"], "total_ms": _stats(a_totals), **_baseline_summary(a_totals)},
        "b": {"runs": rows["b"], "total_ms": _stats(b_totals), **_baseline_summary(b_totals)},
        "delta_ms": round(b_best - a_best, 1),
        "delta_pct": round((b_best - a_best) / a_best * 100, 1) if a_best else None,
        "faster": "b" if b_best < a_best else ("a" if a_best < b_best else "tie"),
        "cold_cache_honoured": all(bool(r["cache_cleared"]) for r in all_rows),
        "warmup_runs": warmup,
        "errors": errors,
        "note": "Compared on the FASTEST run of each side (baseline protocol).",
    }
    if not_run:
        result["runs_not_executed"] = not_run
    return result


def benchmark(
    dax_query: str,
    runs: int = 5,
    cold_cache: bool = True,
    warmup: int = 1,
    time_budget_s: Optional[float] = None,
) -> Dict[str, Any]:
    """Run a DAX query N times and return a percentile summary.

    ``cold_cache=True`` clears the VertiPaq cache (real ClearCache XMLA) before
    EVERY measured run; ``cold_cache=False`` measures warm runs only. The two are
    not alternated -- use ``cold_runs``/``warm_runs`` for a separated cold+warm
    report or ``compare_dax_query`` for an interleaved A/B benchmark.
    """
    from core.infrastructure.connection_state import connection_state

    if not connection_state.is_connected():
        return {
            "success": False,
            "error": "Not connected to Power BI Desktop",
            "error_type": "not_connected",
        }
    executor = getattr(connection_state, "query_executor", None)
    if executor is None:
        return {
            "success": False,
            "error": "DAX executor not available",
            "error_type": "not_connected",
        }

    runs = max(1, min(int(runs), 50))  # clamp 1..50
    warmup = max(0, min(int(warmup), 5))
    deadline = time.monotonic() + time_budget_s if time_budget_s else None

    timings_ms: List[float] = []
    row_counts: List[int] = []
    errors: List[str] = []
    not_run = 0

    # Warmup runs (not measured)
    for _ in range(warmup):
        if _should_stop(deadline):
            break
        try:
            executor.validate_and_execute_dax(dax_query, 0, bypass_cache=True)
        except Exception:
            pass

    cold_cache_honoured = True
    for i in range(runs):
        if _should_stop(deadline):
            not_run += 1
            continue
        _progress(i, runs, f"run {i + 1} of {runs}")
        if cold_cache:
            # Real ClearCache XMLA. The previous `EVALUATE {1}` pre-roll cleared
            # nothing, so `cold_cache=True` produced warm numbers labelled cold.
            try:
                if not executor.clear_vertipaq_cache().get("cache_cleared"):
                    cold_cache_honoured = False
            except Exception:
                cold_cache_honoured = False
        try:
            res = executor.validate_and_execute_dax(dax_query, 0, bypass_cache=True)
            if isinstance(res, dict):
                if not res.get("success", True):
                    errors.append(str(res.get("error", "unknown")))
                    continue
                # The executor returns `execution_time_ms`; some paths also
                # set `ms`. Accept either.
                ms = res.get("execution_time_ms")
                if ms is None:
                    ms = res.get("ms")
                if isinstance(ms, (int, float)):
                    timings_ms.append(float(ms))
                rc = res.get("row_count")
                if isinstance(rc, int):
                    row_counts.append(rc)
        except Exception as e:
            errors.append(str(e))

    if not timings_ms:
        return {
            "success": False,
            "error": "no successful runs",
            "error_type": "trace_unavailable",
            "errors": errors,
        }

    sorted_ms = sorted(timings_ms)
    n = len(sorted_ms)

    def _pct(p: float) -> float:
        if n == 1:
            return sorted_ms[0]
        idx = max(0, min(n - 1, int(round((p / 100.0) * (n - 1)))))
        return sorted_ms[idx]

    summary = {
        "runs": n,
        "min_ms": sorted_ms[0],
        "max_ms": sorted_ms[-1],
        "mean_ms": sum(sorted_ms) / n,
        "median_ms": _pct(50),
        "p90_ms": _pct(90),
        "p95_ms": _pct(95),
        "stdev_ms": (sum((x - sum(sorted_ms) / n) ** 2 for x in sorted_ms) / n) ** 0.5,
        "stdev_kind": "population",
        # The baseline protocol says: compare the FASTEST valid run, not the mean.
        # The mean is dragged around by background noise; the fastest run is the
        # closest observation to the query's true cost.
        "baseline_ms": sorted_ms[0],
        "spread_pct": (
            round((sorted_ms[-1] - sorted_ms[0]) / sorted_ms[0] * 100, 1) if sorted_ms[0] else 0
        ),
    }
    # >20% spread means the runs disagree — the baseline is not yet trustworthy.
    if summary["spread_pct"] > 20:
        summary["warning"] = (
            f"Run-to-run spread is {summary['spread_pct']}% (>20%). Add more runs "
            "before trusting this as a baseline."
        )
    out = {
        "success": True,
        "summary": summary,
        "timings_ms": timings_ms,
        "row_counts": row_counts,
        # False when cold_cache was requested but ClearCache did not succeed —
        # the timings are then warm-cache timings.
        "cold_cache_honoured": cold_cache_honoured if cold_cache else None,
        "errors": errors,
        "cold_cache": cold_cache,
        "warmup_runs": warmup,
    }
    if not_run:
        out["runs_not_executed"] = not_run
    return out
