"""Equivalence over a context grid (F-14): ``09_Validate equivalence``.

Two DAX expressions (or a measure and a candidate body) are evaluated side by side in a
deterministic grid of filter contexts and their results are compared cell by cell:

* the grand total,
* every grid column on its own (with its subtotal row) -- the page's slicer columns or
  ``grid_columns`` -- and the pairs among the first three columns,
* date grains (``year`` / ``quarter`` / ``month`` of ``date_column``),
* explicit scenario ``filters`` (each one a separate cell: a currency / scenario switch, a role ...).

Both sides are defined with ``DEFINE MEASURE`` on a host table inside the query only
(``__eqA`` / ``__eqB``): the model is never written. Grid cells come from ``SUMMARIZECOLUMNS``, so no
literal is ever built from a returned value. The grid is capped (``max_cells``) and the coverage is
reported; equal results on the sampled cells are NOT a formal proof of equivalence, and the result
says so (``proof: "sampled_grid"``).

``run_equivalence`` takes the query runner as an argument so it is unit-testable with a fake engine.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from core.infrastructure.adomd_utils import engine_message

DEFAULT_MAX_CELLS = 200
HARD_MAX_CELLS = 2000
MAX_DIFFS_REPORTED = 50
GRAINS = ("year", "quarter", "month")
DEFAULT_TOLERANCE = 1e-6
_MIN_GROUP_CELLS = 5

RunQuery = Callable[[str], Dict[str, Any]]


# --------------------------------------------------------------------------- query building
def _measure_expr(m: str) -> str:
    from core.dax.dax_names import bracket

    # measure_name is always a NAME (expressions go in expression_a): measure names may carry
    # '(' / '|' / '%' (R0201 'PL | % of Revenue (Managerial)' was sent unbracketed as DAX).
    m = (m or "").strip()
    if m.startswith("[") and m.endswith("]") and len(m) > 2:
        return m
    return bracket(m)


def _define(host: str, expr_a: Optional[str], expr_b: Optional[str]) -> str:
    from core.dax.dax_names import quote_table

    lines = ["DEFINE"]
    for name, expr in (("__eqA", expr_a), ("__eqB", expr_b)):
        if expr is not None:
            # a trailing newline keeps a // comment in the body from swallowing what follows
            lines.append(f"    MEASURE {quote_table(host)}[{name}] =\n        {expr.strip()}\n")
    return "\n".join(lines)


def _values(sides: Sequence[str]) -> str:
    parts = []
    if "a" in sides:
        parts.append('"a", [__eqA]')
    if "b" in sides:
        parts.append('"b", [__eqB]')
    return ", ".join(parts)


def _period_expr(grain: str, col: str) -> str:
    if grain == "year":
        return f"YEAR({col})"
    if grain == "quarter":
        return f"YEAR({col}) * 10 + ROUNDUP(MONTH({col}) / 3, 0)"
    return f"YEAR({col}) * 100 + MONTH({col})"


def build_groups(
    *,
    grid_columns: Sequence[str] = (),
    date_column: Optional[str] = None,
    grains: Sequence[str] = (),
    filters: Sequence[str] = (),
    max_cells: int = DEFAULT_MAX_CELLS,
) -> List[Dict[str, Any]]:
    """The grid as a list of groups ``{id, kind, dims, build(sides) -> DAX body, cap}`` (no host/define yet)."""
    max_cells = max(1, min(int(max_cells), HARD_MAX_CELLS))
    groups: List[Dict[str, Any]] = [
        {
            "id": "total",
            "kind": "total",
            "dims": [],
            "cap": 1,
            "body": lambda sides: f"EVALUATE ROW({_values(sides)})",
        }
    ]
    for i, f in enumerate(filters):
        groups.append(
            {
                "id": f"filter_{i + 1}",
                "kind": "filter",
                "dims": [],
                "label": f,
                "cap": 1,
                "body": (
                    lambda sides, f=f: "EVALUATE ROW("
                    + ", ".join(
                        f'"{s}", CALCULATE([__eq{s.upper()}], {f})'
                        for s in ("a", "b")
                        if s in sides
                    )
                    + ")"
                ),
            }
        )
    cols = [c for c in grid_columns if c]
    dim_groups: List[Dict[str, Any]] = []
    for c in cols:
        dim_groups.append({"id": f"col:{c}", "kind": "column", "dims": [c]})
    for i in range(min(len(cols), 3)):
        for j in range(i + 1, min(len(cols), 3)):
            dim_groups.append(
                {"id": f"pair:{cols[i]}|{cols[j]}", "kind": "pair", "dims": [cols[i], cols[j]]}
            )
    if date_column:
        for grain in grains or ():
            if grain in GRAINS:
                dim_groups.append(
                    {"id": f"grain:{grain}", "kind": "grain", "dims": [date_column], "grain": grain}
                )
    used = 1 + len(filters)
    per_group = max(_MIN_GROUP_CELLS, (max_cells - used) // len(dim_groups)) if dim_groups else 0
    for dg in dim_groups:
        dg["cap"] = per_group
        if dg["kind"] == "grain":
            dg["body"] = _grain_body(dg["grain"], str(date_column), per_group)
        else:
            dg["body"] = _summarize_body(dg["dims"], per_group)
        groups.append(dg)
    return groups


def _summarize_body(dims: Sequence[str], cap: int) -> Callable[[Sequence[str]], str]:
    def body(sides: Sequence[str]) -> str:
        rollup = (
            ", ".join(f'{d}, "__tot{i}"' for i, d in enumerate(dims))
            if len(dims) > 1
            else f'{dims[0]}, "__tot0"'
        )
        core = f"SUMMARIZECOLUMNS(ROLLUPADDISSUBTOTAL({rollup}), {_values(sides)})"
        return f"EVALUATE TOPN({cap}, {core}, {dims[0]}, 1)"

    return body


def _grain_body(grain: str, col: str, cap: int) -> Callable[[Sequence[str]], str]:
    def body(sides: Sequence[str]) -> str:
        period = _period_expr(grain, col)
        adds = ", ".join(
            f'"{s}", VAR __p = [__p] RETURN CALCULATE([__eq{s.upper()}], '
            f"FILTER(ALL({col}), {period} = __p))"
            for s in ("a", "b")
            if s in sides
        )
        periods = f'DISTINCT(SELECTCOLUMNS(ALL({col}), "__p", {period}))'
        return f"EVALUATE TOPN({cap}, ADDCOLUMNS({periods}, {adds}), [__p], 1)"

    return body


def compile_group(
    group: Dict[str, Any], host: str, expr_a: str, expr_b: str, sides: Sequence[str] = ("a", "b")
) -> str:
    return (
        _define(host, expr_a if "a" in sides else None, expr_b if "b" in sides else None)
        + "\n"
        + group["body"](sides)
    )


# --------------------------------------------------------------------------- result handling
def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _cell_key(row: Dict[str, Any], dims: Sequence[str], kind: str) -> Tuple[Any, ...]:
    """Dimension values of a result row (everything but a/b), keyed deterministically."""
    key = []
    for k, v in row.items():
        name = str(k).strip("[]").lower() if isinstance(k, str) else str(k)
        bare = name.rsplit("[", 1)[-1].rstrip("]")
        if bare in ("a", "b"):
            continue
        key.append((str(k), v))
    return tuple(sorted(key, key=lambda kv: kv[0]))


def _side(row: Dict[str, Any], which: str) -> Any:
    for k, v in row.items():
        if str(k).strip("[]").lower().rsplit("[", 1)[-1].rstrip("]") == which:
            return v
    return None


def values_equal(a: Any, b: Any, tolerance: float) -> bool:
    na, nb = _num(a), _num(b)
    if na is None and nb is None:
        return a == b
    if na is None or nb is None:
        # BLANK vs 0 is a real difference for a measure rewrite; keep it visible
        return False
    return abs(na - nb) <= max(tolerance, 1e-9 * max(abs(na), abs(nb)))


def _hash(items: Sequence[Tuple[Any, Any]]) -> str:
    canon = []
    for key, v in sorted(items, key=lambda kv: json.dumps(kv[0], default=str)):
        n = _num(v)
        canon.append([key, None if v is None else (round(n, 6) if n is not None else str(v))])
    return hashlib.sha256(
        json.dumps(canon, default=str, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]


# --------------------------------------------------------------------------- the run
def run_equivalence(
    run_query: RunQuery,
    *,
    expression_a: str,
    expression_b: str,
    host_table: str,
    grid_columns: Sequence[str] = (),
    date_column: Optional[str] = None,
    grains: Sequence[str] = (),
    filters: Sequence[str] = (),
    max_cells: int = DEFAULT_MAX_CELLS,
    tolerance: float = DEFAULT_TOLERANCE,
) -> Dict[str, Any]:
    """Evaluate A and B over the grid; never writes. See the module docstring."""
    if not (expression_a or "").strip() or not (expression_b or "").strip():
        return {
            "success": False,
            "error_type": "missing_argument",
            "error": "both expressions are required",
        }
    if not host_table:
        return {
            "success": False,
            "error_type": "missing_argument",
            "error": "host_table is required (any table of the model)",
        }
    groups = build_groups(
        grid_columns=grid_columns,
        date_column=date_column,
        grains=grains,
        filters=filters,
        max_cells=max_cells,
    )
    diffs: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    coverage: List[Dict[str, Any]] = []
    pairs_a: List[Tuple[Any, Any]] = []
    pairs_b: List[Tuple[Any, Any]] = []
    compared = 0
    max_abs = 0.0
    for g in groups:
        dax = compile_group(g, host_table, expression_a, expression_b)
        res = run_query(dax)
        cov: Dict[str, Any] = {
            "group": g["id"],
            "kind": g["kind"],
            "dims": g["dims"],
            "cap": g["cap"],
        }
        if res.get("success"):
            rows = res.get("rows") or []
            cov.update(
                {
                    "cells": len(rows),
                    "capped": len(rows) >= g["cap"] and g["kind"] not in ("total", "filter"),
                }
            )
            for r in rows:
                key = (g["id"],) + _cell_key(r, g["dims"], g["kind"])
                a, b = _side(r, "a"), _side(r, "b")
                pairs_a.append((key, a))
                pairs_b.append((key, b))
                compared += 1
                if not values_equal(a, b, tolerance):
                    na, nb = _num(a), _num(b)
                    if na is not None and nb is not None:
                        max_abs = max(max_abs, abs(na - nb))
                    diffs.append(
                        {
                            "group": g["id"],
                            "cell": dict((k, v) for k, v in key[1:]),
                            "a": a,
                            "b": b,
                            "difference": None if na is None or nb is None else na - nb,
                        }
                    )
        else:
            side_err = {}
            for side in ("a", "b"):
                r1 = run_query(compile_group(g, host_table, expression_a, expression_b, (side,)))
                if not r1.get("success"):
                    side_err[side] = engine_message(r1.get("error"))[:300]
            if not side_err:  # the pair failed but each side alone works: report the combined error
                side_err["both"] = engine_message(res.get("error"))[:300]
            cov.update({"cells": 0, "error": side_err})
            errors.append({"group": g["id"], "errors": side_err, "dax": dax[:1500]})
            if len(side_err) == 1 and "both" not in side_err:
                diffs.append(
                    {
                        "group": g["id"],
                        "cell": {},
                        "a": None,
                        "b": None,
                        "error_side": next(iter(side_err)),
                        "error": next(iter(side_err.values())),
                    }
                )
        coverage.append(cov)
    one_sided = [e for e in errors if len(e["errors"]) == 1 and "both" not in e["errors"]]
    if compared == 0 and not diffs:
        equivalent: Optional[bool] = None
    else:
        # One side failing where the other evaluates IS a difference (a rewrite that errors).
        equivalent = not diffs
    if errors and not one_sided and equivalent:
        equivalent = None  # some groups could not be evaluated on EITHER side: not a clean pass
    out: Dict[str, Any] = {
        "success": True,
        "equivalent": equivalent,
        "proof": "sampled_grid",
        "note": (
            "No value could be compared: one side failed to evaluate (see errors - a DAX error "
            "in that expression, or a measure name sent as an expression)."
            if compared == 0 and diffs
            else (
                "No cell could be compared: see errors."
                if compared == 0
                else (
                    "Equal results on the sampled cells; not a formal proof. Raise max_cells / add "
                    "grid columns to widen it."
                    if not diffs
                    else "Differences found on the sampled cells (differing_cells)."
                )
            )
        ),
        "cells_compared": compared,
        "cells_differing": len([d for d in diffs if "error_side" not in d]),
        "differing_cells": diffs[:MAX_DIFFS_REPORTED],
        "max_abs_difference": max_abs,
        "hash_a": _hash(pairs_a),
        "hash_b": _hash(pairs_b),
        "coverage": {
            "groups": coverage,
            "max_cells": min(int(max_cells), HARD_MAX_CELLS),
            "cells": compared,
        },
        "tolerance": tolerance,
        "model_written": False,
    }
    out["identical_hash"] = out["hash_a"] == out["hash_b"]
    if errors:
        out["errors"] = errors[:10]
    # A grid group that ran but returned no row (both sides BLANK on every member) proves nothing
    # about that grain: say so, so a pass on the grand total alone is not read as a grid pass.
    empty = [c["group"] for c in coverage if c.get("cells") == 0 and "error" not in c]
    if empty:
        out["empty_groups"] = empty
        if compared:
            out["note"] += (
                " Group(s) " + ", ".join(empty) + " returned no non-blank cell on either side, so "
                "only the other groups were compared."
            )
    return out


def resolve_expressions(args: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """``(expr_a, expr_b, error)`` from ``expression_a/expression_b`` or ``measure_name + candidate_expression``."""
    if args.get("expression_a") and args.get("expression_b"):
        return str(args["expression_a"]), str(args["expression_b"]), None
    if args.get("measure_name") and args.get("candidate_expression"):
        return _measure_expr(str(args["measure_name"])), str(args["candidate_expression"]), None
    return None, None, "give expression_a + expression_b, or measure_name + candidate_expression"
