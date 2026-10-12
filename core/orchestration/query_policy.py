import logging
import math
import re
import statistics
import time
from typing import Any, Dict, List, Optional

from core.validation.error_handler import ErrorHandler

logger = logging.getLogger(__name__)

#: Upper bound on ``runs`` for mode=analyze (each run hits the engine).
MAX_ANALYZE_RUNS = 20
DEFAULT_ANALYZE_RUNS = 3

_LEADING_COMMENT_RE = re.compile(r"\A(?:\s+|//[^\n]*(?:\n|\Z)|--[^\n]*(?:\n|\Z)|/\*.*?\*/)", re.S)


def strip_leading_comments(query: str) -> str:
    """Drop leading whitespace and ``//`` / ``--`` / ``/* */`` comments (B-11)."""
    text = query or ""
    while True:
        m = _LEADING_COMMENT_RE.match(text)
        if not m or m.end() == 0:
            return text
        text = text[m.end() :]


def _is_complete_define_query(query: str) -> bool:
    """Check if query is a complete DEFINE+EVALUATE statement that must not be wrapped."""
    return strip_leading_comments(query).upper().startswith("DEFINE")


def _top_level_keywords(body: str) -> List[str]:
    """Upper-case keyword tokens at paren depth 0, outside strings/identifiers/comments."""
    tokens: List[str] = []
    depth = 0
    i = 0
    n = len(body)
    while i < n:
        ch = body[i]
        if ch == '"':
            j = i + 1
            while j < n:
                if body[j] == '"':
                    if j + 1 < n and body[j + 1] == '"':
                        j += 2
                        continue
                    break
                j += 1
            i = j + 1
            continue
        if ch == "'":
            j = body.find("'", i + 1)
            while j != -1 and j + 1 < n and body[j + 1] == "'":
                j = body.find("'", j + 2)
            i = n if j == -1 else j + 1
            continue
        if ch == "[":
            j = body.find("]", i + 1)
            while j != -1 and j + 1 < n and body[j + 1] == "]":
                j = body.find("]", j + 2)
            i = n if j == -1 else j + 1
            continue
        if body.startswith("//", i) or body.startswith("--", i):
            j = body.find("\n", i)
            i = n if j == -1 else j + 1
            continue
        if body.startswith("/*", i):
            j = body.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch.isalpha() or ch == "_":
            j = i
            while j < n and (body[j].isalnum() or body[j] in "._"):
                j += 1
            if depth == 0:
                tokens.append(body[i:j].upper())
            i = j
            continue
        i += 1
    return tokens


def _should_apply_topn(query: str) -> bool:
    """True only for a single, unordered ``EVALUATE <table expr>`` statement (B-11).

    A TOPN wrapper around everything after the first EVALUATE used to swallow
    ``ORDER BY`` / ``START AT`` clauses and further ``EVALUATE`` statements
    (-> DAX syntax error), and wrapped comment-prefixed queries whole. Such
    queries are now left untouched; the reader cap (``max_rows``) bounds rows.
    """
    head = strip_leading_comments(query)
    upper = head.upper()
    if not upper.startswith("EVALUATE") or _is_complete_define_query(head):
        return False
    body = head[len("EVALUATE") :]
    keywords = _top_level_keywords(body)
    if any(k in ("EVALUATE", "ORDER", "START", "DEFINE") for k in keywords):
        return False
    if keywords[:1] == ["TOPN"]:
        return False
    return bool(body.strip())


def _apply_topn_wrapper(query: str, limit: int) -> str:
    """Wrap a single bare EVALUATE statement with TOPN for row safety."""
    head = strip_leading_comments(query).strip()
    body = head[len("EVALUATE") :].strip()
    return f"EVALUATE TOPN({limit}, {body})"


def _stats(samples: List[float]) -> Dict[str, Any]:
    if not samples:
        return {}
    out = {
        "avg_ms": round(sum(samples) / len(samples), 2),
        "min_ms": round(min(samples), 2),
        "max_ms": round(max(samples), 2),
        "median_ms": round(statistics.median(samples), 2),
    }
    out["stddev_ms"] = round(statistics.stdev(samples), 2) if len(samples) > 1 else 0.0
    if out["avg_ms"] > 0 and len(samples) > 1:
        out["cv_pct"] = round(out["stddev_ms"] / out["avg_ms"] * 100, 1)
    return out


class QueryPolicy:
    def __init__(self, config: Any):
        self.config = config

    def _get_preview_limit(self, max_rows: Optional[int]) -> int:
        if isinstance(max_rows, int) and max_rows > 0:
            return max_rows
        return (
            self.config.get("query.max_rows_preview", 1000)
            or self.config.get("performance.default_top_n", 1000)
            or 1000
        )

    def _run_benchmark(
        self,
        query_executor: Any,
        query: str,
        runs: Optional[int],
        cold: bool,
        lim: int,
    ) -> Dict[str, Any]:
        """B-04: a real N-run wall-clock benchmark that always hits the server.

        * ``cold=False`` (default): one untimed warm-up run, then ``runs`` timed
          warm runs. The warm-up's time is reported as ``first_run_ms``.
        * ``cold=True``: the VertiPaq cache is cleared (XMLA ClearCache) before
          EVERY timed run; ``cache_cleared`` says whether that worked.

        Client wall-clock timings only — for FE/SE server timings per run use
        ``13_Query_Plan benchmark`` (cold_runs/warm_runs).
        """
        n = DEFAULT_ANALYZE_RUNS if runs is None else int(runs)
        n = max(1, min(MAX_ANALYZE_RUNS, n))
        timings: List[float] = []
        engine_ms: List[float] = []
        last: Dict[str, Any] = {}
        first_run_ms: Optional[float] = None
        cache_cleared: List[bool] = []

        def _one() -> Dict[str, Any]:
            start = time.perf_counter()
            res = query_executor.validate_and_execute_dax(query, lim, bypass_cache=True)
            wall = (time.perf_counter() - start) * 1000
            if isinstance(res, dict):
                res = dict(res)
                res["_wall_ms"] = wall
            return res if isinstance(res, dict) else {"success": False, "error": str(res)}

        if not cold:
            warm = _one()
            if not warm.get("success"):
                warm.setdefault("decision", "analyze")
                return warm
            first_run_ms = round(warm["_wall_ms"], 2)

        for _ in range(n):
            if cold:
                clear = getattr(query_executor, "clear_vertipaq_cache", None)
                ok = False
                if callable(clear):
                    try:
                        cleared = clear()
                        ok = bool(isinstance(cleared, dict) and cleared.get("cache_cleared"))
                    except Exception:  # noqa: BLE001
                        ok = False
                cache_cleared.append(ok)
            res = _one()
            if not res.get("success"):
                res.setdefault("decision", "analyze")
                res["runs_completed"] = len(timings)
                return res
            timings.append(res["_wall_ms"])
            em = res.get("execution_time_ms")
            if isinstance(em, (int, float)) and not math.isnan(em):
                engine_ms.append(float(em))
            last = res

        out = {k: v for k, v in last.items() if k != "_wall_ms"}
        out["decision"] = "analyze"
        out["reason"] = f"{n} timed {'cold' if cold else 'warm'} run(s), cache bypassed" + (
            "" if cold else " after 1 untimed warm-up"
        )
        benchmark: Dict[str, Any] = {
            "runs": n,
            "cold": bool(cold),
            "run_ms": [round(t, 2) for t in timings],
            "wall_clock": _stats(timings),
            "reader_ms": _stats(engine_ms),
            "timing_source": "client wall-clock (use 13_Query_Plan benchmark for FE/SE)",
        }
        if first_run_ms is not None:
            benchmark["first_run_ms"] = first_run_ms
        if cold:
            benchmark["cache_cleared"] = all(cache_cleared) if cache_cleared else False
        out["benchmark"] = benchmark
        out["execution_time_ms"] = benchmark["wall_clock"].get(
            "avg_ms", out.get("execution_time_ms")
        )
        return out

    def safe_run_dax(
        self,
        connection_state,
        query: str,
        mode: str = "auto",
        runs: Optional[int] = None,
        max_rows: Optional[int] = None,
        verbose: bool = False,
        bypass_cache: bool = False,
        include_event_counts: bool = False,
        cold: bool = False,
    ) -> Dict[str, Any]:
        if not connection_state.is_connected():
            return ErrorHandler.handle_not_connected()
        query_executor = connection_state.query_executor
        if not query_executor:
            return ErrorHandler.handle_manager_unavailable("query_executor")
        # P-07: static validation runs ONCE per query text (the executor memoises
        # it). The full analysis (complexity / patterns / suggestions, ~10 ms @ 9 KB)
        # is built only when it is shown (verbose) or needed as the error details.
        analysis: Optional[Dict[str, Any]] = None
        try:
            needs_analysis = bool(verbose or query_executor.syntax_errors(query))
        except Exception:  # noqa: BLE001 -- analyze_dax_query reports it as before
            needs_analysis = True
        if needs_analysis:
            analysis = query_executor.analyze_dax_query(query)
            if not analysis.get("success"):
                return analysis
            if analysis.get("syntax_errors"):
                return {
                    "success": False,
                    "error": "Query validation failed",
                    "error_type": "syntax_validation_error",
                    "details": analysis,
                }
        lim = self._get_preview_limit(max_rows)
        try:
            limits = connection_state.get_safety_limits()
            max_rows_cap = int(limits.get("max_rows_per_call", 10000))
            if isinstance(lim, int) and lim > 0:
                lim = min(lim, max_rows_cap)
        except Exception:
            pass
        effective_mode = (mode or "auto").lower()
        notes: List[str] = []

        if effective_mode == "analyze":
            return self._run_benchmark(query_executor, query, runs, bool(cold), lim)

        # Preview path: TOPN only for a single unordered EVALUATE; the reader cap
        # (top_n) bounds every other shape without rewriting the DAX.
        if _should_apply_topn(query):
            try:
                query = _apply_topn_wrapper(query, lim)
                notes.append(f"Applied TOPN({lim}) to EVALUATE query for safety")
            except Exception:
                pass
        exec_result = query_executor.validate_and_execute_dax(query, lim, bypass_cache=bypass_cache)
        if verbose and notes:
            exec_result.setdefault("notes", []).extend(notes)
        exec_result.setdefault("decision", "preview")
        exec_result.setdefault(
            "reason", "Fast preview; rows capped at top_n (TOPN only for a single bare EVALUATE)"
        )
        if verbose and analysis is not None:
            exec_result.setdefault("analysis", analysis)
        return exec_result
