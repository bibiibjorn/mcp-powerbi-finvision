"""Tie-out / additivity audit (F-09): ``09_Validate tieout`` and the ``tieout`` suite test type.

Three modes:

* ``visual`` (default) -- run a matrix / table visual's REAL query with its grand total and
  subtotal rows (``core.debug.visual_query_builder`` emits ``ROLLUPADDISSUBTOTAL`` with an
  ``IsGrandTotalRowTotal`` / ``IsDM<n>Total`` flag per row level) and check, level by level, that
  every parent row equals the sum of its children within a tolerance. Reports the FIRST level and
  measure that breaks, with a cause hint. Semi-additive / ratio measures break additivity
  legitimately: list them in ``non_additive`` (they are then skipped and reported).
* ``page_vs_page`` -- the same measure (or the same visual title) on two pages must show the same
  grand total.
* ``A_vs_B`` -- two measures that must agree (cash-flow EBITDA vs P&L EBITDA), at the grand total or
  per grain (``by`` columns).

The checking functions are pure (rows in, report out); the ``run_*`` functions are the glue to the
visual builder / query executor and are covered by fakes.
"""

from __future__ import annotations

import math
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

MODES = ("visual", "page_vs_page", "A_vs_B")
SUPPORTED_VISUALS = ("pivotTable", "matrix", "table", "tableEx")
DEFAULT_TOLERANCE = 0.01
DEFAULT_RELATIVE_TOLERANCE = 1e-9
DEFAULT_MAX_ROWS = 5000
MAX_BREAKS_REPORTED = 25
_TOPN_RE = re.compile(r"(TOPN\()\s*\d+(\s*,\s*__DS0Core)")
_FLAG_GRAND = "IsGrandTotalRowTotal"

RunQuery = Callable[[str], Dict[str, Any]]

# measure-expression patterns that make a measure non-additive by construction
_NON_ADDITIVE_HINTS: Tuple[Tuple[str, str], ...] = (
    (
        r"\bDIVIDE\s*\(|\)\s*/\s*[\[\(A-Za-z]",
        "ratio / quotient (a parent is not the sum of its children)",
    ),
    (r"\bDISTINCTCOUNT(NOBLANK)?\s*\(|\bAPPROXIMATEDISTINCTCOUNT\s*\(", "distinct count"),
    (r"\bAVERAGE(X|A)?\s*\(|\bMEDIAN(X)?\s*\(", "average / median"),
    (
        r"\b(LASTNONBLANK|FIRSTNONBLANK|CLOSINGBALANCE\w*|OPENINGBALANCE\w*|ENDOFMONTH|ENDOFYEAR)\b",
        "semi-additive (balance)",
    ),
    (r"\bMAXX?\s*\(|\bMINX?\s*\(", "min / max"),
)


# --------------------------------------------------------------------------- value helpers
def _norm(key: Any) -> str:
    """``'T'[C]`` / ``T[C]`` / ``[C]`` / ``C`` -> a comparable lower-case key."""
    return str(key).replace("'", "").strip().lower()


def _get(row: Dict[str, Any], ref: str) -> Any:
    """Cell of *ref* (``'T'[C]``, ``[Alias]``, ``Alias``) tolerating ADOMD's bracket/quote decoration."""
    if ref in row:
        return row[ref]
    want = _norm(ref)
    bare = want[1:-1] if want.startswith("[") and want.endswith("]") else want
    for k, v in row.items():
        if _norm(k) in (want, bare, f"[{bare}]"):
            return v
    if want.endswith("]") and "[" in want:  # 'T'[C] asked, [C] returned (or the reverse)
        col = want[want.rindex("[") :]
        for k, v in row.items():
            if _norm(k) == col or _norm(k).endswith(col):
                return v
    return None


def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _flag(row: Dict[str, Any], name: str) -> bool:
    v = _get(row, name)
    return str(v).lower() == "true"


def _within(parent: Optional[float], total: float, tol: float, rel: float) -> bool:
    p = parent or 0.0
    return abs(p - total) <= max(tol, rel * max(abs(p), abs(total)))


# --------------------------------------------------------------------------- level derivation
def derive_levels(
    build_kwargs: Dict[str, Any], visual_type: str
) -> Tuple[List[List[str]], List[str], List[str]]:
    """``(row_levels, flags, fixed_cols)`` the builder used for the subtotal structure.

    Mirrors ``VisualQueryBuilder._build_visual_dax_query_ex``: a matrix with several row levels and
    subtotals on emits one flag per level (``IsGrandTotalRowTotal``, ``IsDM1Total`` ...); a table
    (or a one-level matrix) emits a single grand-total flag over every grouping column. Columns of
    the matrix ``Columns`` role are plain group-by columns (``fixed_cols``).
    """
    grouping = list(build_kwargs.get("grouping_columns") or [])
    levels = [list(lv) for lv in (build_kwargs.get("grouping_levels") or [[c] for c in grouping])]
    known = {c for lvl in levels for c in lvl}
    levels += [[c] for c in grouping if c not in known]
    rows_set = set(build_kwargs.get("row_columns") or [])
    is_matrix = visual_type in ("pivotTable", "matrix")
    row_levels = (
        [lv for lv in levels if any(c in rows_set for c in lv)]
        if (is_matrix and rows_set)
        else list(levels)
    )
    other = [lv for lv in levels if lv not in row_levels]
    fixed = [c for lv in other for c in lv]
    per_level = is_matrix and build_kwargs.get("row_subtotals") is not False and len(row_levels) > 1
    if per_level:
        flags = [_FLAG_GRAND if i == 0 else f"IsDM{i}Total" for i in range(len(row_levels))]
        return row_levels, flags, fixed
    flat = [c for lv in row_levels for c in lv]
    return ([flat] if flat else []), ([_FLAG_GRAND] if flat else []), fixed


def _depth(row: Dict[str, Any], flags: Sequence[str]) -> int:
    """0 = grand total ... len(flags) = detail row (the first TRUE flag is the rolled-up level)."""
    for i, f in enumerate(flags):
        if _flag(row, f):
            return i
    return len(flags)


def hint_for_expression(expression: Optional[str]) -> Optional[str]:
    if not expression:
        return None
    for pat, text in _NON_ADDITIVE_HINTS:
        if re.search(pat, expression, re.IGNORECASE):
            return text
    return None


# --------------------------------------------------------------------------- the check
def check_hierarchy(
    rows: Sequence[Dict[str, Any]],
    *,
    levels: Sequence[Sequence[str]],
    flags: Sequence[str],
    measures: Dict[str, str],
    fixed_cols: Sequence[str] = (),
    tolerance: float = DEFAULT_TOLERANCE,
    relative_tolerance: float = DEFAULT_RELATIVE_TOLERANCE,
    non_additive: Sequence[str] = (),
    expressions: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Parent == sum(children) at every subtotal level, for every additive measure.

    ``measures`` maps a measure name to its column alias in *rows*. Returns
    ``{ties, breaks, first_break, levels_checked, levels_skipped, per_measure, ...}``.
    """
    skip = {_norm(m).strip("[]") for m in non_additive}
    additive = {m: a for m, a in measures.items() if _norm(m).strip("[]") not in skip}
    skipped = [m for m in measures if m not in additive]
    # A measure that returns text (SVG data URI, label, colour code) has nothing to add up: every
    # value the query returned for it is non-numeric. A column with no value at all stays checked.
    skipped_non_numeric: List[str] = []
    for m, alias in list(additive.items()):
        values = [v for v in (_get(r, alias) for r in rows) if v is not None]
        if values and all(_num(v) is None for v in values):
            skipped_non_numeric.append(m)
            del additive[m]
    n_levels = len(flags)
    keyed: List[Tuple[int, Dict[str, Any]]] = [(_depth(r, flags), r) for r in rows]

    def level_key(row: Dict[str, Any], depth: int) -> Tuple[Any, ...]:
        cols = [c for lvl in levels[:depth] for c in lvl] + list(fixed_cols)
        return tuple(_get(row, c) for c in cols)

    by_depth: Dict[int, List[Dict[str, Any]]] = {}
    for d, r in keyed:
        by_depth.setdefault(d, []).append(r)

    breaks: List[Dict[str, Any]] = []
    levels_checked: List[Dict[str, Any]] = []
    levels_skipped: List[Dict[str, Any]] = []
    per_measure: Dict[str, Dict[str, Any]] = {
        m: {
            "parents_checked": 0,
            "parents_without_children": 0,
            "breaks": 0,
            "first_break_level": None,
        }
        for m in additive
    }
    for d in range(n_levels):
        parents, children = by_depth.get(d, []), by_depth.get(d + 1, [])
        name = " > ".join(c for c in (levels[d - 1] if d > 0 else ["<grand total>"]))
        if not parents:
            levels_skipped.append(
                {"level": d, "name": name, "reason": "no subtotal row returned for this level"}
            )
            continue
        if not children:
            levels_skipped.append({"level": d, "name": name, "reason": "no child rows returned"})
            continue
        groups: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = {}
        for c in children:
            groups.setdefault(level_key(c, d), []).append(c)
        levels_checked.append(
            {"level": d, "name": name, "parents": len(parents), "children": len(children)}
        )
        for p in parents:
            kids = groups.get(level_key(p, d), [])
            for m, alias in additive.items():
                pv = _num(_get(p, alias))
                total = sum(_num(_get(k, alias)) or 0.0 for k in kids)
                pm = per_measure[m]
                if not kids:
                    # A member with no child row (all children BLANK, or a formula line such as
                    # EBIT that the next level does not break down) has nothing to add up: it is
                    # not a break. R0201 live test: 60 of 72 reported breaks were such rows.
                    pm["parents_without_children"] += 1
                    continue
                pm["parents_checked"] += 1
                if _within(pv, total, tolerance, relative_tolerance):
                    continue
                pm["breaks"] += 1
                if pm["first_break_level"] is None:
                    pm["first_break_level"] = d
                breaks.append(
                    {
                        "level": d,
                        "level_name": name,
                        "measure": m,
                        "parent_key": dict(
                            zip(
                                [c for lvl in levels[:d] for c in lvl] + list(fixed_cols),
                                level_key(p, d),
                            )
                        ),
                        "parent_value": pv,
                        "children_sum": total,
                        "difference": (pv or 0.0) - total,
                        "children": len(kids),
                    }
                )
    breaks.sort(key=lambda b: (b["level"], list(additive).index(b["measure"])))
    hinted: List[str] = []
    for m, pm in per_measure.items():
        if pm["breaks"]:
            expr_hint = hint_for_expression((expressions or {}).get(m))
            if expr_hint:
                hinted.append(m)
            pattern = (
                "every parent breaks"
                if pm["breaks"] >= pm["parents_checked"]
                else "some parents break"
            )
            pm["pattern"] = pattern
            pm["cause_hint"] = (
                f"non-additive by construction: {expr_hint}; add it to non_additive if intended"
                if expr_hint
                else (
                    "systematic: the measure is probably non-additive (ratio/distinct/semi-additive) or its "
                    "total branch differs"
                    if pattern == "every parent breaks"
                    else "isolated: a wrong rollup, a currency/granularity fan-out or a measure that "
                    "ignores the subtotal context for these members"
                )
            )
    result: Dict[str, Any] = {
        "ties": not breaks,
        "levels_checked": levels_checked,
        "levels_skipped": levels_skipped,
        "measures_checked": list(additive),
        "skipped_non_additive": skipped,
        "skipped_non_numeric": skipped_non_numeric,
        "per_measure": per_measure,
        "breaks": breaks[:MAX_BREAKS_REPORTED],
        "break_count": len(breaks),
        "first_break": breaks[0] if breaks else None,
        "rows": len(rows),
        "tolerance": tolerance,
    }
    if breaks and hinted:
        # Every break may come from ratio / distinct / average measures whose expression already
        # says they cannot add up (R0201: only the two % measures broke). Say so, without
        # turning `ties` green: the hint is lexical, the caller decides via non_additive.
        result["breaks_only_in_non_additive_by_expression"] = all(
            b["measure"] in hinted for b in breaks
        )
        result["non_additive_by_expression"] = hinted
    if not levels_checked:
        result["ties"] = None
        result["inconclusive"] = (
            "no parent/child level could be checked ("
            + (
                "the visual has no subtotal levels"
                if not n_levels
                else "no subtotal rows were returned"
            )
            + ")"
        )
    elif not additive or not sum(pm["parents_checked"] for pm in per_measure.values()):
        # Nothing was actually compared: every measure was text / declared non-additive, or every
        # parent had no child row. "No breaks" would then read as a pass.
        result["ties"] = None
        result["inconclusive"] = (
            "no additive measure left to check ("
            f"{len(skipped_non_numeric)} text, {len(skipped)} declared non-additive, "
            f"{len(additive)} remaining)"
            if not additive
            else "every parent had no child rows, so no sum could be compared"
        )
    return result


# --------------------------------------------------------------------------- value comparison
def compare_values(
    a: Any, b: Any, tolerance: float, relative_tolerance: float = DEFAULT_RELATIVE_TOLERANCE
) -> bool:
    na, nb = _num(a), _num(b)
    if na is None and nb is None:
        return a == b or (a is None and b is None)
    return (
        _within(na, nb or 0.0, tolerance, relative_tolerance)
        if (na is not None or nb is not None)
        else a == b
    )


def compare_pairs(
    pairs: Sequence[Dict[str, Any]],
    tolerance: float = DEFAULT_TOLERANCE,
    relative_tolerance: float = DEFAULT_RELATIVE_TOLERANCE,
) -> Dict[str, Any]:
    """Each pair ``{label, a, b, ...}`` must agree; returns ``{ties, differences, compared}``."""
    diffs = []
    for p in pairs:
        if not compare_values(p.get("a"), p.get("b"), tolerance, relative_tolerance):
            na, nb = _num(p.get("a")), _num(p.get("b"))
            diffs.append({**p, "difference": None if na is None or nb is None else na - nb})
    return {
        "ties": not diffs,
        "compared": len(pairs),
        "differences": diffs[:MAX_BREAKS_REPORTED],
        "difference_count": len(diffs),
        "tolerance": tolerance,
    }


# --------------------------------------------------------------------------- glue: DAX helpers
def _measure_expr(m: str) -> str:
    """``Name`` -> ``[Name]``; bracketed / qualified refs and expressions are kept."""
    from core.dax.dax_names import bracket

    m = (m or "").strip()
    if m.startswith(("[", "'")) or "(" in m or "[" in m:
        return m
    return bracket(m)


def lift_row_limit(query: str, max_rows: int) -> Tuple[str, Optional[int]]:
    """Raise the visual's per-type ``TOPN(501, __DS0Core ...)`` window so totals and details survive.

    Returns ``(query, original_limit_or_None)``."""
    m = _TOPN_RE.search(query)
    if not m:
        return query, None
    original = int(re.findall(r"\d+", m.group(0))[0])
    return (
        _TOPN_RE.sub(lambda mm: f"{mm.group(1)}{max_rows}{mm.group(2)}", query, count=1),
        original,
    )


# --------------------------------------------------------------------------- glue: visual
def run_visual_tieout(
    builder: Any,
    run_query: RunQuery,
    *,
    page_name: str,
    visual_id: Optional[str] = None,
    visual_name: Optional[str] = None,
    measures: Optional[Sequence[str]] = None,
    tolerance: float = DEFAULT_TOLERANCE,
    relative_tolerance: float = DEFAULT_RELATIVE_TOLERANCE,
    non_additive: Sequence[str] = (),
    max_rows: int = DEFAULT_MAX_ROWS,
) -> Dict[str, Any]:
    vq = builder.build_visual_query(
        page_name, visual_id, visual_name, measures=list(measures) if measures else None
    )
    if vq is None:
        return {
            "success": False,
            "error_type": "not_found",
            "error": "visual not found or it has no measure",
        }
    vtype = vq.visual_info.visual_type
    if vtype not in SUPPORTED_VISUALS:
        return {
            "success": False,
            "error_type": "unsupported_visual",
            "error": f"tieout needs a matrix / table visual with subtotals, this is '{vtype}'",
        }
    levels, flags, fixed = derive_levels(vq.build_kwargs or {}, vtype)
    query, original_limit = lift_row_limit(vq.dax_query, max_rows)
    res = run_query(query)
    if not res.get("success"):
        return {
            "success": False,
            "error_type": "query_failed",
            "error": str(res.get("error"))[:400],
            "dax_query": query,
        }
    rows = res.get("rows") or []
    names = list(measures) if measures else list(vq.visual_info.measures or [])
    aliases: Dict[str, str] = {}
    expressions: Dict[str, str] = {}
    for m in names:
        ref = m if m.startswith("[") else f"[{m}]"
        aliases[m] = vq.value_column(ref) or m
    for d in getattr(vq, "measure_definitions", None) or []:
        expressions[getattr(d, "name", "")] = getattr(d, "expression", "")
        expressions[f"[{getattr(d, 'name', '')}]"] = getattr(d, "expression", "")
    report = check_hierarchy(
        rows,
        levels=levels,
        flags=flags,
        measures=aliases,
        fixed_cols=fixed,
        tolerance=tolerance,
        relative_tolerance=relative_tolerance,
        non_additive=non_additive,
        expressions=expressions,
    )
    truncated = len(rows) >= max_rows or bool(res.get("truncated"))
    out: Dict[str, Any] = {
        "success": True,
        "mode": "visual",
        "page": page_name,
        "visual_type": vtype,
        "fidelity": getattr(vq, "fidelity", "full"),
        "fidelity_notes": list(getattr(vq, "fidelity_notes", []) or []),
        "row_limit": {"visual_default": original_limit, "used": max_rows},
        "levels": [list(lv) for lv in levels],
        "flags": list(flags),
        **report,
    }
    if truncated:
        out["truncated"] = True
        cap = f"the result hit the {max_rows}-row cap; raise max_rows"
        if report["ties"] is None:
            # already inconclusive: keep the specific reason and add the cap to it
            out["inconclusive"] = f"{report.get('inconclusive', 'inconclusive')}; {cap}"
        else:
            out["ties"] = None if report["ties"] else False
            out["inconclusive"] = cap
    return out


# --------------------------------------------------------------------------- glue: A vs B
def run_ab_tieout(
    run_query: RunQuery,
    *,
    measure_a: str,
    measure_b: str,
    by: Sequence[str] = (),
    filters: Sequence[str] = (),
    tolerance: float = DEFAULT_TOLERANCE,
    relative_tolerance: float = DEFAULT_RELATIVE_TOLERANCE,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> Dict[str, Any]:
    a, b = _measure_expr(measure_a), _measure_expr(measure_b)
    if by:
        core = f'SUMMARIZECOLUMNS({", ".join(by)}, "a", {a}, "b", {b})'
        body = f"CALCULATETABLE({core}, {', '.join(filters)})" if filters else core
        query = f"EVALUATE TOPN({int(max_rows)}, {body}, {by[0]})"
    elif filters:
        f = ", ".join(filters)
        query = f'EVALUATE ROW("a", CALCULATE({a}, {f}), "b", CALCULATE({b}, {f}))'
    else:
        query = f'EVALUATE ROW("a", {a}, "b", {b})'
    res = run_query(query)
    if not res.get("success"):
        return {
            "success": False,
            "error_type": "query_failed",
            "error": str(res.get("error"))[:400],
            "dax_query": query,
        }
    pairs = []
    for r in res.get("rows") or []:
        label = {c: _get(r, c) for c in by} if by else {}
        pairs.append({"label": label or "grand total", "a": _get(r, "a"), "b": _get(r, "b")})
    out = {
        "success": True,
        "mode": "A_vs_B",
        "measure_a": measure_a,
        "measure_b": measure_b,
        "grain": list(by) or "grand total",
        "dax_query": query,
        **compare_pairs(pairs, tolerance, relative_tolerance),
    }
    if by and len(pairs) >= max_rows:
        out["truncated"] = True
    if not pairs:
        out["ties"] = None
        out["inconclusive"] = "the query returned no row"
    return out


# --------------------------------------------------------------------------- glue: page vs page
def _page_values(
    builder: Any, run_query: RunQuery, page: str, measure: str
) -> List[Dict[str, Any]]:
    out = []
    clean = measure.strip("[]")
    for v in builder.list_visuals(page):
        if v.get("is_slicer") or not v.get("is_data_visual", True):
            continue
        if clean not in [m.strip("[]") for m in v.get("measures", [])]:
            continue
        vq = builder.build_visual_query(page, v["id"], measure_name=measure)
        if vq is None:
            continue
        query, alias = builder.build_total_query(vq, measure)
        res = run_query(query)
        value = None
        if res.get("success") and res.get("rows"):
            value = _get(res["rows"][0], alias)
        out.append(
            {
                "visual_id": v["id"],
                "title": v.get("title") or v.get("friendly_name"),
                "value": value,
                "fidelity": getattr(vq, "fidelity", "full"),
                "error": None if res.get("success") else str(res.get("error"))[:200],
            }
        )
    return out


def run_page_vs_page(
    builder: Any,
    run_query: RunQuery,
    *,
    page_a: str,
    page_b: str,
    measure: str,
    tolerance: float = DEFAULT_TOLERANCE,
    relative_tolerance: float = DEFAULT_RELATIVE_TOLERANCE,
) -> Dict[str, Any]:
    side_a = _page_values(builder, run_query, page_a, measure)
    side_b = _page_values(builder, run_query, page_b, measure)
    if not side_a or not side_b:
        return {
            "success": False,
            "error_type": "not_found",
            "error": f"measure {measure} is not on a data visual of "
            + ("both pages" if not side_a and not side_b else (page_a if not side_a else page_b)),
        }
    pairs: List[Dict[str, Any]] = []
    unmatched: List[Dict[str, Any]] = []
    if len(side_a) == 1 and len(side_b) == 1:
        pairs.append(
            {
                "label": measure,
                "a": side_a[0]["value"],
                "b": side_b[0]["value"],
                "visual_a": side_a[0]["visual_id"],
                "visual_b": side_b[0]["visual_id"],
            }
        )
    else:
        by_title = {str(v["title"]).strip().lower(): v for v in side_b if v.get("title")}
        for va in side_a:
            vb = by_title.get(str(va.get("title") or "").strip().lower())
            if vb is None:
                unmatched.append({"page": page_a, **va})
                continue
            pairs.append(
                {
                    "label": va["title"],
                    "a": va["value"],
                    "b": vb["value"],
                    "visual_a": va["visual_id"],
                    "visual_b": vb["visual_id"],
                }
            )
        matched_b = {p["visual_b"] for p in pairs}
        unmatched += [{"page": page_b, **v} for v in side_b if v["visual_id"] not in matched_b]
    out = {
        "success": True,
        "mode": "page_vs_page",
        "page_a": page_a,
        "page_b": page_b,
        "measure": measure,
        **compare_pairs(pairs, tolerance, relative_tolerance),
    }
    if unmatched:
        out["unmatched_visuals"] = unmatched[:MAX_BREAKS_REPORTED]
    if not pairs:
        out["ties"] = None
        out["inconclusive"] = (
            "several visuals use the measure and none share a title: pass visual ids"
        )
    errors = [v for v in side_a + side_b if v.get("error")]
    if errors:
        # an inconclusive (None) verdict stays None and keeps its reason; True degrades to None
        if out["ties"] is not None:
            out["ties"] = None if out["ties"] else False
        if out["ties"] is None:
            out.setdefault("inconclusive", f"{len(errors)} visual value(s) failed to evaluate")
        out["errors"] = errors[:5]
    return out


# --------------------------------------------------------------------------- dispatcher
def run_tieout(
    args: Dict[str, Any],
    *,
    builder: Any,
    run_query: RunQuery,
) -> Dict[str, Any]:
    """Entry shared by ``09_Validate tieout`` and the ``tieout`` suite test (args or the test dict)."""
    mode = str(args.get("mode") or ("A_vs_B" if args.get("measure_a") else "visual"))
    if mode not in MODES:
        return {
            "success": False,
            "error_type": "invalid_parameters",
            "error": f"mode must be one of {list(MODES)}",
        }
    tol = float(args.get("tolerance", DEFAULT_TOLERANCE))
    rel = float(args.get("relative_tolerance", DEFAULT_RELATIVE_TOLERANCE))
    try:
        if mode == "A_vs_B":
            if not (args.get("measure_a") and args.get("measure_b")):
                return {
                    "success": False,
                    "error_type": "missing_argument",
                    "error": "A_vs_B needs measure_a and measure_b",
                }
            return run_ab_tieout(
                run_query,
                measure_a=str(args["measure_a"]),
                measure_b=str(args["measure_b"]),
                by=list(args.get("by") or []),
                filters=list(args.get("filters") or []),
                tolerance=tol,
                relative_tolerance=rel,
            )
        if builder is None:
            return {
                "success": False,
                "error_type": "pbip_path_required",
                "error": "visual / page_vs_page tieout needs the PBIP path (09_Debug_Operations set_path)",
            }
        if mode == "page_vs_page":
            measure = args.get("measure") or args.get("measure_name")
            if not (args.get("page_a") and args.get("page_b") and measure):
                return {
                    "success": False,
                    "error_type": "missing_argument",
                    "error": "page_vs_page needs page_a, page_b and measure_name",
                }
            return run_page_vs_page(
                builder,
                run_query,
                page_a=str(args["page_a"]),
                page_b=str(args["page_b"]),
                measure=str(measure),
                tolerance=tol,
                relative_tolerance=rel,
            )
        if not args.get("page_name"):
            return {
                "success": False,
                "error_type": "missing_argument",
                "error": "visual tieout needs page_name and visual_id/visual_name",
            }
        return run_visual_tieout(
            builder,
            run_query,
            page_name=str(args["page_name"]),
            visual_id=args.get("visual_id"),
            visual_name=args.get("visual_name"),
            measures=args.get("measures"),
            tolerance=tol,
            relative_tolerance=rel,
            non_additive=list(args.get("non_additive") or []),
            max_rows=int(args.get("max_rows") or DEFAULT_MAX_ROWS),
        )
    except Exception as exc:  # noqa: BLE001 - one broken visual must not crash a suite run
        return {
            "success": False,
            "error_type": "tieout_failed",
            "error": f"{type(exc).__name__}: {exc}",
        }


def tieout_passes(result: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Suite verdict: only a definite tie passes; inconclusive and failed runs do not."""
    if not result.get("success"):
        return False, [f"tieout failed: {str(result.get('error'))[:300]}"]
    if result.get("ties") is True:
        return True, []
    if result.get("ties") is None:
        return False, [f"tieout inconclusive: {result.get('inconclusive', 'unknown')}"]
    msgs = []
    fb = result.get("first_break")
    if fb:
        msgs.append(
            f"{result.get('break_count')} break(s); first at level {fb['level']} ({fb['level_name']}), "
            f"measure {fb['measure']}: parent {fb['parent_value']} vs children sum {fb['children_sum']}"
        )
    for d in (result.get("differences") or [])[:3]:
        msgs.append(f"{d.get('label')}: {d.get('a')} != {d.get('b')}")
    return False, msgs or ["values do not tie"]
