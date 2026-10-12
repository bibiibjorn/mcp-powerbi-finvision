"""Heuristic rule engine — produces verdict + findings from a parsed plan tree."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from core.query_plan.parser import walk

#: `#Records` this far above the query's result row count is a blow-up.
_BLOWUP_HOT_RATIO = 100
_BLOWUP_WARN_RATIO = 10
#: An FE-side group-by spool of at least this many rows is worth reporting.
_FE_GROUPBY_HOT = 1_000_000
_FE_GROUPBY_WARN = 100_000
#: A CrossApply subtree this many times wider than its widest input is Cartesian.
_CARTESIAN_RATIO = 10
#: Ignore records growth below this absolute size — small spools are noise.
_GROWTH_MIN_RECORDS = 1000
_GROWTH_RATIO = 10


def _count_op(tree: Dict[str, Any], op_substring: str) -> int:
    return sum(1 for n in walk(tree) if op_substring.lower() in n["operator"].lower())


def _records(node: Dict[str, Any]) -> Optional[int]:
    """`#Records` for a node, tolerating nodes produced by the old parser."""
    rec = node.get("records")
    return rec if isinstance(rec, int) else None


def _subtree_max_records(node: Dict[str, Any]) -> Optional[int]:
    best: Optional[int] = None
    for n in walk(node):
        rec = _records(n)
        if rec is not None and (best is None or rec > best):
            best = rec
    return best


def _result_rows(timings: Dict[str, Any]) -> Optional[int]:
    """Result row count of the query, when the caller passed it through."""
    if not isinstance(timings, dict):
        return None
    for key in ("result_rows", "row_count", "result_row_count"):
        val = timings.get(key)
        if isinstance(val, bool):
            continue
        if isinstance(val, int) and val >= 0:
            return val
    return None


def _max_nesting(tree: Dict[str, Any]) -> int:
    """Deepest NESTING level (0-based operator levels), not the raw indent width.

    ``depth`` is the indentation in columns (a tab counts 4), so a two-level plan
    already had depth 8 and every plan was "hot" (F-09).
    """
    best = 0
    for n in walk(tree):
        level = n.get("nesting")
        if not isinstance(level, int):
            level = n.get("depth", 0)
        if level > best:
            best = level
    return best


# Kept for callers of the old name.
_max_depth = _max_nesting

#: Physical plans routinely nest 20-60 levels on healthy queries; only a very
#: deep tree is worth a mention, and never on its own a verdict.
_NESTING_INFO = 40


def _rule_callback_count(tree, xmsql, timings) -> List[Dict[str, Any]]:
    n = _count_op(tree, "CallbackDataID")
    if n > 20:
        return [
            {
                "rule": "callback_data_id_count",
                "severity": "hot",
                "value": n,
                "hint": "Many CallbackDataID hits — SE→FE transition cost is high. Inspect iterators using complex expressions on aggregations.",
            }
        ]
    if n > 5:
        return [
            {
                "rule": "callback_data_id_count",
                "severity": "warn",
                "value": n,
                "hint": "Several CallbackDataID hits — consider replacing iterators with bulk aggregations.",
            }
        ]
    return []


def _rule_nested_iterator(tree, xmsql, timings) -> List[Dict[str, Any]]:
    """INFO only (F-09): plan nesting is a shape, not a cost; #Records rules judge cost."""
    d = _max_nesting(tree)
    if d > _NESTING_INFO:
        return [
            {
                "rule": "nested_iterator_depth",
                "severity": "info",
                "value": d,
                "hint": f"Plan nests {d} operator levels; see records_* rules for the cost.",
            }
        ]
    return []


def _rule_cartesian(tree, xmsql, timings) -> List[Dict[str, Any]]:
    """An operator the engine itself labels Cartesian.

    A bare ``CrossApply`` is NOT flagged: it is in almost every physical plan.
    CrossApply that actually multiplies rows is caught by ``cartesian_expansion``
    from #Records (F-09).
    """
    if any("cartesian" in n["raw"].lower() for n in walk(tree)):
        return [
            {
                "rule": "cartesian_join_in_physical_plan",
                "severity": "hot",
                "hint": "Cartesian product in the plan — verify relationship coverage.",
            }
        ]
    return []


def _rule_distinct_count_xmsql(tree, xmsql, timings) -> List[Dict[str, Any]]:
    if xmsql and xmsql.get("has_distinctcount") and xmsql.get("select_count", 0) > 1:
        return [{"rule": "large_xmsql_with_distinct_count", "severity": "warn"}]
    return []


def _rule_redundant_calculate(tree, xmsql, timings) -> List[Dict[str, Any]]:
    out = []
    for n in walk(tree):
        if n["operator"] == "CALCULATE":
            kids_calc = [c for c in n["children"] if c["operator"] == "CALCULATE"]
            if kids_calc and not any("filter" in c["raw"].lower() for c in n["children"]):
                out.append({"rule": "redundant_calculate", "severity": "info"})
                break
    return out


def _rule_callback_in_xmsql(tree, xmsql, timings) -> List[Dict[str, Any]]:
    """CallbackDataID detected in xmSQL text (post-processed substring match).

    Complements `callback_data_id_count` which walks the parsed tree —
    `callback_in_xmsql` works even when the parser doesn't recognize the
    operator, since it scans the raw xmSQL chunks captured from
    `DAXVertiPaqSEQuery` events.
    """
    n = int(xmsql.get("callback_count", 0)) if xmsql else 0
    if n > 5:
        return [
            {
                "rule": "callback_in_xmsql",
                "severity": "hot",
                "value": n,
                "hint": "CallbackDataID present in many SE queries — formula engine fallback.",
            }
        ]
    if n > 0:
        return [
            {
                "rule": "callback_in_xmsql",
                "severity": "warn",
                "value": n,
                "hint": "CallbackDataID detected — SE→FE transition. Inspect iterators with non-vectorisable expressions.",
            }
        ]
    return []


def _rule_dq_query_in_plan(tree, xmsql, timings) -> List[Dict[str, Any]]:
    """Informational: DirectQueryEnd events present (model has a DQ source)."""
    n = int(xmsql.get("dq_query_count", 0)) if xmsql else 0
    if n > 0:
        ms = int(xmsql.get("dq_total_ms", 0)) if xmsql else 0
        return [
            {
                "rule": "dq_query_in_plan",
                "severity": "info",
                "value": n,
                "hint": f"{n} DirectQueryEnd events ({ms}ms) — model contains a DirectQuery source.",
            }
        ]
    return []


def _rule_agg_rewrite_no_match(tree, xmsql, timings) -> List[Dict[str, Any]]:
    """Informational: aggregation rewrite was attempted but didn't match.

    Signals that an aggregation table exists for a related query but the
    engine couldn't route this query to it — usually a sign the agg-table
    grain is wrong, or the query references a column not in the agg.
    """
    n = int(xmsql.get("agg_rewrite_nomatch_count", 0)) if xmsql else 0
    if n > 0:
        return [
            {
                "rule": "agg_rewrite_no_match",
                "severity": "info",
                "value": n,
                "hint": f"{n} aggregation rewrite attempts produced no match — review agg table grain.",
            }
        ]
    return []


def _rule_records_blowup(tree, xmsql, timings) -> List[Dict[str, Any]]:
    """THE headline diagnostic: max #Records vs the rows the query returns.

    Ferrari's `#Records` method — a query returning 2,191 rows whose plan
    contains `#Records=67373061` materialized ~30,000x more data than it
    needed. Silent when the result row count is unknown (the ratio is
    meaningless without it) or when the plan carries no `#Records`.
    """
    rows = _result_rows(timings)
    if rows is None or rows <= 0:
        return []
    peak_node: Dict[str, Any] = {}
    peak: Optional[int] = None
    for n in walk(tree):
        rec = _records(n)
        if rec is not None and (peak is None or rec > peak):
            peak, peak_node = rec, n
    if peak is None:
        return []
    ratio = peak / float(rows)
    if ratio <= _BLOWUP_WARN_RATIO:
        return []
    severity = "hot" if ratio > _BLOWUP_HOT_RATIO else "warn"
    op = peak_node.get("operator", "?")
    return [
        {
            "rule": "records_blowup",
            "severity": severity,
            "value": peak,
            "result_rows": rows,
            "ratio": round(ratio, 1),
            "operator": op,
            "hint": (
                f"Plan materializes {peak:,} records for a {rows:,}-row result "
                f"({round(ratio, 1)}x) — peak at {op}. Walk down from that operator to "
                "find the expansion and push the filter into the storage engine "
                "(CALCULATE filter arguments) so it applies before materialization."
            ),
        }
    ]


def _rule_records_growth(tree, xmsql, timings) -> List[Dict[str, Any]]:
    """`#Records` should SHRINK moving up the plan; growth = a materialization.

    Reports the child/parent pair where the row count grew — that boundary is
    the materialization point you are paying for.
    """
    worst: Optional[Dict[str, Any]] = None
    for parent in walk(tree):
        p_rec = _records(parent)
        if p_rec is None:
            continue
        for child in parent.get("children", []):
            c_rec = _records(child)
            if c_rec is None or c_rec < _GROWTH_MIN_RECORDS:
                continue
            if c_rec <= p_rec * _GROWTH_RATIO:
                continue
            growth = c_rec - p_rec
            if worst is None or growth > worst["growth"]:
                worst = {
                    "growth": growth,
                    "operator": child.get("operator", "?"),
                    "records": c_rec,
                    "parent_operator": parent.get("operator", "?"),
                    "parent_records": p_rec,
                }
    if worst is None:
        return []
    return [
        {
            "rule": "records_growth",
            "severity": "warn",
            "value": worst["records"],
            "operator": worst["operator"],
            "parent_operator": worst["parent_operator"],
            "parent_records": worst["parent_records"],
            "hint": (
                f"{worst['operator']} produces {worst['records']:,} records but its parent "
                f"{worst['parent_operator']} keeps only {worst['parent_records']:,} — the "
                "materialization happens here and is then thrown away. Filter earlier."
            ),
        }
    ]


def _rule_fe_side_grouping(tree, xmsql, timings) -> List[Dict[str, Any]]:
    """AggregationSpool<GroupBy> = the FE grouping rows VertiPaq should group."""
    out: List[Dict[str, Any]] = []
    peak: Optional[int] = None
    for n in walk(tree):
        if (n.get("spool_kind") or "").lower() != "groupby":
            continue
        rec = _records(n)
        if rec is not None and (peak is None or rec > peak):
            peak = rec
    if peak is None or peak < _FE_GROUPBY_WARN:
        return out
    severity = "hot" if peak >= _FE_GROUPBY_HOT else "warn"
    out.append(
        {
            "rule": "fe_side_grouping",
            "severity": severity,
            "value": peak,
            "hint": (
                f"AggregationSpool<GroupBy> materializes {peak:,} rows — the formula engine "
                "is doing a group-by the storage engine should do. Rewrite so the grouping "
                "columns come from a SUMMARIZECOLUMNS/GROUPBY the SE can push down."
            ),
        }
    )
    return out


def _rule_cartesian_expansion(tree, xmsql, timings) -> List[Dict[str, Any]]:
    """CrossApply whose subtree #Records dwarfs its own inputs = FE Cartesian."""
    worst: Optional[Dict[str, Any]] = None
    for n in walk(tree):
        if (n.get("operator_base") or n.get("operator", "")).lower() != "crossapply":
            continue
        subtree_peak = _subtree_max_records(n)
        if subtree_peak is None:
            continue
        input_peaks = [
            v for v in (_subtree_max_records(c) for c in n.get("children", [])) if v is not None
        ]
        widest_input = max(input_peaks) if input_peaks else None
        if widest_input is None or widest_input <= 0:
            continue
        if subtree_peak <= widest_input * _CARTESIAN_RATIO:
            continue
        if worst is None or subtree_peak > worst["value"]:
            worst = {
                "value": subtree_peak,
                "widest_input": widest_input,
                "ratio": round(subtree_peak / float(widest_input), 1),
            }
    if worst is None:
        return []
    return [
        {
            "rule": "cartesian_expansion",
            "severity": "hot",
            "value": worst["value"],
            "widest_input_records": worst["widest_input"],
            "ratio": worst["ratio"],
            "hint": (
                f"CrossApply expands {worst['widest_input']:,} input rows to "
                f"{worst['value']:,} ({worst['ratio']}x) — a Cartesian product built in the "
                "formula engine. Move both conditions into CALCULATE filter arguments so "
                "VertiPaq evaluates the pair."
            ),
        }
    ]


_X_ITERATORS = {"sumx", "minx", "maxx", "averagex", "countax", "countrows"}
_VERTIPAQ_AGGS = {
    "sum_vertipaq",
    "min_vertipaq",
    "max_vertipaq",
    "count_vertipaq",
    "average_vertipaq",
    "distinctcount_vertipaq",
}


def _rule_logical_iterator_not_pushed(tree, xmsql, timings) -> List[Dict[str, Any]]:
    """`SumX: ScaLogOp` where `Sum_Vertipaq: ScaLogOp` was expected.

    The plan-level fingerprint of "an iterator that didn't need to be an
    iterator": the engine plans FE-side iteration instead of pushing the
    aggregation to the storage engine. Only fires when NO `*_Vertipaq`
    aggregation appears anywhere in the plan — a plan that mixes both is doing
    at least some of the work in the SE and is not clear-cut.
    """
    iterators = sorted(
        {
            str(n.get("operator", ""))
            for n in walk(tree)
            if str(n.get("operator_base") or n.get("operator", "")).lower() in _X_ITERATORS
        }
    )
    if not iterators:
        return []
    has_vertipaq_agg = any(
        str(n.get("operator_base") or n.get("operator", "")).lower() in _VERTIPAQ_AGGS
        for n in walk(tree)
    )
    if has_vertipaq_agg:
        return []
    return [
        {
            "rule": "logical_iterator_not_pushed",
            "severity": "warn",
            "value": iterators,
            "hint": (
                f"Logical plan uses {', '.join(iterators)} with no *_Vertipaq aggregation — "
                "the engine plans to iterate in the formula engine instead of pushing the "
                "aggregation to VertiPaq. Simplify the iterator expression (or aggregate a "
                "plain column) so the storage engine can do it."
            ),
        }
    ]


def _rule_large_semijoin(tree, xmsql, timings) -> List[Dict[str, Any]]:
    """xmSQL DEFINE TABLE + ININDEX with a big IN list = a costly semi-join."""
    if not xmsql or not xmsql.get("has_define_table") or not xmsql.get("has_inindex"):
        return []
    size = xmsql.get("in_list_max_size")
    if not isinstance(size, int) or not xmsql.get("has_large_in_list"):
        return []
    return [
        {
            "rule": "large_semijoin",
            "severity": "warn",
            "value": size,
            "hint": (
                f"xmSQL semi-join (DEFINE TABLE + ININDEX) carries a {size}-value IN list — "
                "the formula engine pushed a large materialized filter set into the storage "
                "engine. Reduce the filter cardinality or filter on a related key column."
            ),
        }
    ]


_RULES: Dict[str, Callable[..., List[Dict[str, Any]]]] = {
    "callback_data_id_count": _rule_callback_count,
    "nested_iterator_depth": _rule_nested_iterator,
    "cartesian_join_in_physical_plan": _rule_cartesian,
    "large_xmsql_with_distinct_count": _rule_distinct_count_xmsql,
    "redundant_calculate": _rule_redundant_calculate,
    "callback_in_xmsql": _rule_callback_in_xmsql,
    "dq_query_in_plan": _rule_dq_query_in_plan,
    "agg_rewrite_no_match": _rule_agg_rewrite_no_match,
    # Structure-aware rules (consume #Records / operator classes from the parser)
    "records_blowup": _rule_records_blowup,
    "records_growth": _rule_records_growth,
    "fe_side_grouping": _rule_fe_side_grouping,
    "cartesian_expansion": _rule_cartesian_expansion,
    "logical_iterator_not_pushed": _rule_logical_iterator_not_pushed,
    "large_semijoin": _rule_large_semijoin,
}

RULES = list(_RULES.keys())

_DESCRIPTIONS = {
    "callback_data_id_count": "Count of CallbackDataID nodes (SE→FE transitions).",
    "nested_iterator_depth": (
        "Maximum operator NESTING level (info only; never drives the verdict)."
    ),
    "cartesian_join_in_physical_plan": (
        "An operator the engine labels Cartesian (a plain CrossApply is not flagged; see "
        "cartesian_expansion)."
    ),
    "large_xmsql_with_distinct_count": "Multiple SELECTs combined with DISTINCTCOUNT.",
    "redundant_calculate": "Nested CALCULATE without filter args.",
    "callback_in_xmsql": "CallbackDataID substring detected in xmSQL trace text.",
    "dq_query_in_plan": "Number of DirectQueryEnd events in the trace.",
    "agg_rewrite_no_match": "Aggregation rewrites attempted but produced no match.",
    "records_blowup": (
        "Max #Records in the physical plan vs the query's result row count — the headline "
        "materialization diagnostic (hot >100x, warn >10x). Needs result_rows in timings."
    ),
    "records_growth": (
        "A child operator whose #Records exceeds its parent's — the materialization point "
        "(#Records should shrink moving up the plan)."
    ),
    "fe_side_grouping": (
        "AggregationSpool<GroupBy> with a large #Records — the formula engine is grouping "
        "rows the storage engine should have grouped."
    ),
    "cartesian_expansion": (
        "CrossApply whose subtree #Records greatly exceeds its widest input — an FE-side "
        "Cartesian product."
    ),
    "logical_iterator_not_pushed": (
        "Logical plan contains SumX/MinX/CountRows with no matching *_Vertipaq aggregation — "
        "FE iteration instead of an SE-pushed aggregation."
    ),
    "large_semijoin": (
        "xmSQL DEFINE TABLE + ININDEX semi-join carrying a large IN (...) value list."
    ),
}


def list_rules() -> List[Dict[str, str]]:
    return [{"id": rid, "description": _DESCRIPTIONS.get(rid, "")} for rid in _RULES]


def _verdict(findings: List[Dict[str, Any]]) -> str:
    if any(f.get("severity") == "hot" for f in findings):
        return "hot"
    if any(f.get("severity") == "warn" for f in findings):
        return "warn"
    return "ok"


def analyze(tree: Dict[str, Any], xmsql: Dict[str, Any], timings: Dict[str, Any]) -> Dict[str, Any]:
    findings: List[Dict[str, Any]] = []
    for fn in _RULES.values():
        findings.extend(fn(tree, xmsql, timings))
    return {
        "verdict": _verdict(findings),
        "findings": findings,
        "summary": {"rule_count_run": len(_RULES), "finding_count": len(findings)},
    }
