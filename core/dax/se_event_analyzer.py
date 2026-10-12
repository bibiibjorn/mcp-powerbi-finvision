"""Deep analysis of Storage Engine trace events from visual trace."""

import logging
import re
import math
import statistics
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Callback pattern in xmSQL WITH clauses
_CALLBACK_RE = re.compile(
    r"(CallbackDataID|EncodeCallback|RoundValueCallback|"
    r"MinMaxColumnPositionCallback|Cond)\s*\(",
    re.IGNORECASE,
)

# xmSQL WITH clause pattern: WITH $Expr0 := ( ... )
_WITH_CLAUSE_RE = re.compile(
    r"\$(\w+)\s*:=\s*\(\s*(CallbackDataID|EncodeCallback|RoundValueCallback|"
    r"MinMaxColumnPositionCallback|Cond)\s*\(([^)]*)\)",
    re.IGNORECASE,
)

# JOIN type patterns
_INNER_JOIN_RE = re.compile(r"\bINNER\s+JOIN\b", re.IGNORECASE)
_LEFT_OUTER_JOIN_RE = re.compile(r"\bLEFT\s+OUTER\s+JOIN\b", re.IGNORECASE)
_REVERSE_BITMAP_JOIN_RE = re.compile(r"\bREVERSE\s+BITMAP\s+JOIN\b", re.IGNORECASE)
_REVERSE_HASH_JOIN_RE = re.compile(r"\bREVERSE\s+HASH\s+JOIN\b", re.IGNORECASE)

# Aggregation function pattern for fusion analysis
_AGG_FUNC_RE = re.compile(
    r"\b(SUM|COUNT|MIN|MAX|AVG|DCOUNT|STDEV|VAR)\s*\(",
    re.IGNORECASE,
)

# Table reference pattern in xmSQL: 'TableName'[Column]
_TABLE_REF_RE = re.compile(r"'([^']+)'\[", re.IGNORECASE)

# Alias pattern in xmSQL: AS [ColName] or AS ColName
_ALIAS_RE = re.compile(r"\bAS\s+\[?[^\],\s]+\]?", re.IGNORECASE)

# Caps for the with_clauses section of analyze() — a fan-out trace can carry
# hundreds of events, and this section is diagnostic detail, not a full dump.
MAX_WITH_CLAUSE_EVENTS = 10
MAX_WITH_CLAUSE_EXPRS = 10


class SeEventAnalyzer:
    """Deep analysis of Storage Engine trace events from visual trace."""

    def analyze(self, se_events: list, perf: dict) -> dict:
        """Main entry point. Returns structured analysis dict.

        Args:
            se_events: List of SE event dicts from NativeTraceRunner
            perf: Performance dict with timing aggregates
        """
        if not se_events:
            return {"note": "No SE events provided"}

        callbacks = self._analyze_callbacks(se_events)
        datacache = self._analyze_datacache(se_events)
        timing_dist = self._analyze_timing_distribution(se_events, perf)
        fusion = self._detect_fusion_opportunities(se_events)
        joins = self._analyze_join_types(se_events)
        row_ratio = self._analyze_row_ratio(se_events)
        with_clauses = self._collect_with_clauses(se_events)

        return {
            "callbacks": callbacks,
            "datacache": datacache,
            "timing_distribution": timing_dist,
            "fusion_opportunities": fusion,
            "join_analysis": joins,
            "row_ratio_analysis": row_ratio,
            "with_clauses": with_clauses,
        }

    def _collect_with_clauses(self, se_events: list) -> list:
        """Parsed xmSQL WITH-clause expressions per event.

        Only events that actually declare a WITH expression are emitted, so
        this is empty for the common callback-free trace. Capped to keep the
        response small.
        """
        collected: List[Dict[str, Any]] = []
        for idx, evt in enumerate(se_events):
            if len(collected) >= MAX_WITH_CLAUSE_EVENTS:
                break
            query = evt.get("query", "") or evt.get("xmSQL", "") or ""
            if not query:
                continue
            exprs = self._parse_xmsql_with_clause(query)
            if not exprs:
                continue
            collected.append(
                {
                    "line": evt.get("line", idx + 1),
                    "expressions": exprs[:MAX_WITH_CLAUSE_EXPRS],
                }
            )
        return collected

    def _analyze_callbacks(self, se_events: list) -> dict:
        """Parse xmSQL WITH clauses for all callback types.

        Returns dict with detected flag, counts by type, and slowest callback query.
        """
        by_type: Dict[str, list] = {}
        total_count = 0
        slowest_cb_query: Optional[dict] = None
        slowest_cb_duration = 0.0

        for idx, evt in enumerate(se_events):
            query = evt.get("query", "") or evt.get("xmSQL", "") or ""
            duration = evt.get("duration_ms", 0) or evt.get("duration", 0) or 0
            line = evt.get("line", idx + 1)

            for m in _CALLBACK_RE.finditer(query):
                cb_type = m.group(1)
                total_count += 1
                snippet = query[max(0, m.start() - 30) : m.end() + 50].strip()

                entry = {"line": line, "duration_ms": duration, "snippet": snippet}
                by_type.setdefault(cb_type, []).append(entry)

                if duration > slowest_cb_duration:
                    slowest_cb_duration = duration
                    slowest_cb_query = {
                        "line": line,
                        "duration_ms": duration,
                        "query": query[:200],
                    }

        return {
            "detected": total_count > 0,
            "total_count": total_count,
            "by_type": {k: v[:5] for k, v in by_type.items()},  # Limit per type
            "slowest_callback_query": slowest_cb_query,
        }

    def _analyze_datacache(self, se_events: list) -> dict:
        """Sum rows and KB across all SE events. Flag large queries."""
        total_rows = 0
        total_kb = 0
        large_queries: list = []
        materialization_warnings: list = []

        for idx, evt in enumerate(se_events):
            rows = evt.get("rows", 0) or 0
            kb = evt.get("kb", 0) or evt.get("size_kb", 0) or 0
            line = evt.get("line", idx + 1)
            total_rows += rows
            total_kb += kb

            if rows > 100_000:
                large_queries.append(
                    {
                        "line": line,
                        "rows": rows,
                        "kb": kb,
                        "query": (evt.get("query", "") or "")[:150],
                    }
                )
                if rows > 1_000_000:
                    materialization_warnings.append(
                        f"SE query at line {line} materialized {rows:,} rows — "
                        f"consider reducing table scope or adding filters."
                    )

            if kb > 10_240:  # >10MB
                materialization_warnings.append(
                    f"SE query at line {line} consumed {kb:,} KB — " f"large datacache allocation."
                )

        # Sort by rows descending, take top 3
        large_queries.sort(key=lambda q: q["rows"], reverse=True)

        return {
            "total_rows": total_rows,
            "total_kb": total_kb,
            "largest_queries": large_queries[:3],
            "materialization_warnings": materialization_warnings[:5],
        }

    def _analyze_timing_distribution(self, se_events: list, perf: dict) -> dict:
        """Compute avg/median/max/p95 of SE query durations. Identify outliers."""
        durations = []
        for evt in se_events:
            d = evt.get("duration_ms", 0) or evt.get("duration", 0) or 0
            durations.append(d)

        if not durations:
            return {"avg_ms": 0, "max_ms": 0, "p95_ms": 0, "outlier_queries": []}

        avg_ms = statistics.mean(durations)
        max_ms = max(durations)
        median_ms = statistics.median(durations)

        # p95, nearest-rank (review E-24: int(0.95n) returned the max for n <= 20)
        sorted_d = sorted(durations)
        p95_idx = max(0, math.ceil(0.95 * len(sorted_d)) - 1)
        p95_ms = sorted_d[min(p95_idx, len(sorted_d) - 1)]

        # Outliers: >3x average (only if avg is meaningful)
        outlier_queries = []
        threshold = max(avg_ms * 3, 5)  # At least 5ms to avoid noise
        for idx, evt in enumerate(se_events):
            d = evt.get("duration_ms", 0) or evt.get("duration", 0) or 0
            if d > threshold:
                outlier_queries.append(
                    {
                        "line": evt.get("line", idx + 1),
                        "duration_ms": d,
                        "query": (evt.get("query", "") or "")[:150],
                    }
                )

        outlier_queries.sort(key=lambda q: q["duration_ms"], reverse=True)

        # Cache hit rate
        se_queries = perf.get("se_queries", 0) or len(se_events)
        se_cache_hits = perf.get("se_cache_hits", 0) or 0
        cache_hit_rate = (se_cache_hits / se_queries) if se_queries > 0 else 0.0

        # Parallelism assessment
        se_par = perf.get("se_parallelism", 0) or 0
        if se_par < 1.0:
            parallelism_assessment = "sequential"
        elif se_par < 2.0:
            parallelism_assessment = "limited"
        else:
            parallelism_assessment = "good"

        return {
            "avg_ms": round(avg_ms, 2),
            "median_ms": round(median_ms, 2),
            "max_ms": round(max_ms, 2),
            "p95_ms": round(p95_ms, 2),
            "outlier_queries": outlier_queries[:5],
            "cache_hit_rate": round(cache_hit_rate, 3),
            "parallelism_assessment": parallelism_assessment,
        }

    @staticmethod
    def _normalize_xmsql(query: str) -> str:
        """Normalize xmSQL query for fusion signature comparison.

        Applies: case normalization, whitespace collapse, alias stripping,
        then aggregation function normalization. This catches fusion groups
        that differ only by case, whitespace, or alias naming.
        """
        normalized = query.upper()
        normalized = re.sub(r"\s+", " ", normalized).strip()
        normalized = _ALIAS_RE.sub("", normalized)
        normalized = _AGG_FUNC_RE.sub("AGG(", normalized)
        return normalized

    def _detect_fusion_opportunities(self, se_events: list) -> dict:
        """Detect vertical and horizontal fusion breaks.

        Vertical: Multiple SE queries with same filter context but different aggregations,
                  referencing the same set of tables.
        Horizontal: SE queries differing only in one column predicate value.
        """
        signatures: Dict[tuple, list] = {}
        predicate_groups: Dict[str, list] = {}

        for idx, evt in enumerate(se_events):
            query = evt.get("query", "") or evt.get("xmSQL", "") or ""
            if not query:
                continue

            # Vertical fusion: normalize xmSQL then include table references
            normalized_q = self._normalize_xmsql(query)
            tables_in_query = frozenset(t.upper() for t in _TABLE_REF_RE.findall(query.strip()))
            signature = (normalized_q, tables_in_query)
            signatures.setdefault(signature, []).append(idx)

            # Horizontal fusion: normalize column predicate values
            # Replace literal values (numbers, strings) with placeholder
            normalized = re.sub(r"'[^']*'", "'?'", query)
            normalized = re.sub(r"\b\d+(\.\d+)?\b", "?", normalized)
            predicate_groups.setdefault(normalized, []).append(idx)

        vertical_breaks = sum(1 for v in signatures.values() if len(v) > 1)
        horizontal_breaks = sum(1 for v in predicate_groups.values() if len(v) > 1)

        # Estimate saveable queries (conservative)
        estimated_saveable = sum(len(v) - 1 for v in signatures.values() if len(v) > 1)

        notes = []
        if vertical_breaks > 0:
            notes.append(
                f"{vertical_breaks} groups of SE queries share the same filter context "
                f"but use different aggregations — vertical fusion is broken. "
                f"This often happens with IF/SWITCH returning different measures."
            )
        if horizontal_breaks > 0:
            notes.append(
                f"{horizontal_breaks} groups of SE queries differ only in predicate "
                f"values — horizontal fusion is broken. "
                f"Consider restructuring to allow VertiPaq to batch these."
            )

        return {
            "vertical_breaks": vertical_breaks,
            "horizontal_breaks": horizontal_breaks,
            "estimated_saveable_queries": estimated_saveable,
            "notes": notes,
        }

    def _analyze_join_types(self, se_events: list) -> dict:
        """Scan SE events for JOIN patterns including expensive reverse joins."""
        inner_joins = 0
        left_outer_joins = 0
        reverse_bitmap_joins = 0
        reverse_hash_joins = 0
        cartesian_warnings: list = []
        reverse_join_warnings: list = []

        for idx, evt in enumerate(se_events):
            query = evt.get("query", "") or evt.get("xmSQL", "") or ""
            rows = evt.get("rows", 0) or 0
            line = evt.get("line", idx + 1)

            inner_count = len(_INNER_JOIN_RE.findall(query))
            outer_count = len(_LEFT_OUTER_JOIN_RE.findall(query))
            rev_bitmap_count = len(_REVERSE_BITMAP_JOIN_RE.findall(query))
            rev_hash_count = len(_REVERSE_HASH_JOIN_RE.findall(query))
            inner_joins += inner_count
            left_outer_joins += outer_count
            reverse_bitmap_joins += rev_bitmap_count
            reverse_hash_joins += rev_hash_count

            if inner_count > 0 and rows > 10_000:
                cartesian_warnings.append(
                    f"INNER JOIN at line {line} produced {rows:,} rows — "
                    f"may indicate Cartesian product from nested iterators or CROSSJOIN."
                )

            if rev_bitmap_count > 0:
                reverse_join_warnings.append(
                    f"REVERSE BITMAP JOIN at line {line} ({rows:,} rows) — "
                    f"expensive join reversal triggered when many-side exceeds 131K rows "
                    f"or unique values exceed 16K. Consider reducing cardinality."
                )
            if rev_hash_count > 0:
                reverse_join_warnings.append(
                    f"REVERSE HASH JOIN at line {line} ({rows:,} rows) — "
                    f"expensive hash-based join reversal. Indicates very high cardinality "
                    f"relationship traversal."
                )

        return {
            "inner_joins": inner_joins,
            "left_outer_joins": left_outer_joins,
            "reverse_bitmap_joins": reverse_bitmap_joins,
            "reverse_hash_joins": reverse_hash_joins,
            "cartesian_warnings": cartesian_warnings[:5],
            "reverse_join_warnings": reverse_join_warnings[:5],
        }

    def _parse_xmsql_with_clause(self, query: str) -> list:
        """Parse WITH $Expr := (CallbackType(...)) from xmSQL query text.

        Returns list of dicts with expr_name, callback_type, arguments.
        """
        results = []
        for m in _WITH_CLAUSE_RE.finditer(query):
            results.append(
                {
                    "expr_name": m.group(1),
                    "callback_type": m.group(2),
                    "arguments": m.group(3).strip(),
                }
            )
        return results

    def _analyze_row_ratio(self, se_events: list) -> dict:
        """Compute duration_ms / rows ratio to identify per-row FE overhead.

        Uses scaled thresholds: small scans have higher acceptable overhead,
        large scans flag even small per-row cost since it compounds.
        """
        queries_with_high_ratio = 0
        worst_ratio = 0.0
        worst_query_line = 0

        for idx, evt in enumerate(se_events):
            duration = evt.get("duration_ms", 0) or evt.get("duration", 0) or 0
            rows = evt.get("rows", 0) or 0
            line = evt.get("line", idx + 1)

            if rows < 1:
                continue

            ratio = duration / rows
            # Scale threshold with row count: small scans have normal overhead,
            # large scans should flag even minor per-row cost
            if rows < 1_000:
                threshold = 0.05
            elif rows < 100_000:
                threshold = 0.01
            else:
                threshold = 0.005
            if ratio > threshold:
                queries_with_high_ratio += 1
                if ratio > worst_ratio:
                    worst_ratio = ratio
                    worst_query_line = line

        return {
            "queries_with_high_ratio": queries_with_high_ratio,
            "worst_ratio": round(worst_ratio, 4),
            "worst_query_line": worst_query_line,
        }

    @staticmethod
    def compare_cache_impact(cold_events: list, warm_events: list) -> dict:
        """Compare cold vs warm cache SE events to confirm cache effectiveness.

        If warm ≈ cold timing, the SE datacache is busted (e.g. due to
        CallbackDataID or excessive query fan-out).

        Args:
            cold_events: SE events from a cold-cache trace run
            warm_events: SE events from a subsequent warm-cache trace run

        Returns:
            Dict with cold/warm totals, speedup ratio, cache effectiveness flag,
            and diagnostic text.
        """

        def _sum_duration(events: list) -> float:
            return sum(evt.get("duration_ms", 0) or evt.get("duration", 0) or 0 for evt in events)

        cold_total = _sum_duration(cold_events)
        warm_total = _sum_duration(warm_events)

        # E-24: a perfect warm run (every SE query a cache hit, 0 ms) used to be
        # reported as "no warm cache timing data" / ineffective.
        fully_cached = bool(warm_events) and warm_total == 0 and cold_total > 0
        if fully_cached:
            speedup = float("inf")
        else:
            speedup = (cold_total / warm_total) if warm_total > 0 else 0.0
        cache_effective = speedup > 1.5

        if fully_cached:
            diagnosis = (
                "Warm run took 0 ms of SE time — every storage-engine query was served "
                "from the datacache (fully cached)."
            )
        elif cache_effective:
            if speedup > 5:
                diagnosis = (
                    f"Excellent cache effectiveness ({speedup:.1f}x speedup). "
                    f"Datacache is working well."
                )
            else:
                diagnosis = (
                    f"Cache is effective ({speedup:.1f}x speedup). "
                    f"Warm cache provides meaningful benefit."
                )
        else:
            if warm_total > 0:
                diagnosis = (
                    f"Cache is INEFFECTIVE ({speedup:.1f}x). "
                    f"Warm cache ({warm_total:.0f}ms) is not significantly faster "
                    f"than cold cache ({cold_total:.0f}ms). "
                    f"Likely caused by CallbackDataID or excessive SE query fan-out "
                    f"exceeding the 256-query datacache limit."
                )
            else:
                diagnosis = "No warm cache timing data available for comparison."

        return {
            "cold_total_ms": round(cold_total, 1),
            "warm_total_ms": round(warm_total, 1),
            "speedup_ratio": None if fully_cached else round(speedup, 2),
            "fully_cached": fully_cached,
            "cache_effective": cache_effective,
            "diagnosis": diagnosis,
        }
