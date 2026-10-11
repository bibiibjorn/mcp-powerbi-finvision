"""Semantic-equivalence guard for optimized DAX.

Closes the gap flagged by the 2026-05 DAX audit ("the optimizer has no
semantic-equivalence guard"): a performance win is only a win if the optimized
expression provably returns the SAME data. A single scalar under a single filter
context is NOT that proof — an optimization can break at a different grain, on
totals vs rows, or on blank/error handling.

This module is PURE: no I/O, no connection, no CLR. It compares two already-
executed DAX result sets (list-of-dict rows, as the query executor returns) and
emits a structured verdict. It never raises — malformed input yields a
not-equivalent verdict with a reason.

Design decisions:

- **Order-insensitive.** DAX guarantees no row order without ORDER BY, so two
  results are equivalent when they hold the same MULTISET of rows. Rows are
  sorted by a canonical serialization of their normalized values; the caller
  never has to supply a key.
- **Name-first column alignment.** Columns present on BOTH sides are matched
  by (alias-mapped) name; only the remaining columns are paired positionally,
  in their original order (the queries alias the value column differently —
  "Original" vs "Optimized"). An optional ``column_alias_map`` declares the
  intended mapping. A differing column *count* is a hard
  ``column_count_mismatch``.
- **Tight default tolerance (review D-06).** Numbers are equal when
  ``abs(a-b) <= max(abs_tol, rel_tol * max(|a|, |b|))`` with defaults
  ``rel_tol = 1e-9`` and ``abs_tol = 1e-6``: a currency difference of 0.09 %
  is a real difference. Pass ``tolerance=`` (legacy: used as both the relative
  and the absolute tolerance) or ``rel_tolerance=`` / ``abs_tolerance=`` to
  loosen it explicitly. Equal infinities and NaN/NaN are equal.
- **BLANK is not 0, "" is not BLANK, text is not a number.** ``BLANK()`` vs
  ``0`` (``blank_vs_zero``), ``""`` vs ``BLANK()`` (``blank_vs_empty_string`` —
  ``ISBLANK("")`` is FALSE in DAX) and ``"100"`` vs ``100`` (``type_mismatch`` —
  a FORMAT-style rewrite changes the type) are all differences.
- **Vacuous results fail closed.** Zero rows on both sides
  (``no_rows_compared``) and results whose every compared value cell is BLANK
  on both sides (``vacuous_all_blank`` — e.g. ``ROW("value", [M])`` on a model
  where the measure is BLANK) prove nothing and are NOT equivalent.
"""

from __future__ import annotations

import hashlib
import logging
import math
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_TOLERANCE",
    "DEFAULT_REL_TOLERANCE",
    "DEFAULT_ABS_TOLERANCE",
    "ROWWISE_ROW_LIMIT",
    "MAX_DIFFERENCES",
    "normalize_value",
    "compare_rows",
    "verdict",
]

#: Default relative / absolute numeric tolerances (review D-06: the old default
#: of 0.1 % relative certified 1,000,000 vs 1,000,900 as equal).
DEFAULT_REL_TOLERANCE = 1e-9
DEFAULT_ABS_TOLERANCE = 1e-6

#: Legacy single-value tolerance name, kept for importers; it is the absolute
#: floor used when no tolerance is passed.
DEFAULT_TOLERANCE = DEFAULT_ABS_TOLERANCE

#: Above this row count on either side we stop per-row diffing and compare
#: counts + a deterministic hash instead (token/time discipline).
ROWWISE_ROW_LIMIT = 50_000

#: Cap on reported differences (token discipline).
MAX_DIFFERENCES = 20


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def normalize_value(v: Any) -> Any:
    """Normalize one cell value into a comparable canonical form.

    Type-aware so DAX semantics survive the round trip:

    - ``None`` (BLANK) stays ``None`` — BLANK is NOT 0 and NOT ``""``.
    - ``bool`` stays ``bool`` — distinct from 0/1.
    - numerics (int / float / Decimal) become ``float``.
    - strings stay strings, unmodified — ``"100"`` is text, not the number 100,
      and ``""`` is an empty string, not BLANK (review D-06).
    - datetimes/dates/times become ISO strings.
    - anything else becomes its ``str``.

    Never raises.
    """
    try:
        if v is None:
            return None
        # bool BEFORE int — bool is an int subclass and must stay distinct.
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, Decimal):
            return float(v)
        if isinstance(v, (datetime, date, time)):
            return v.isoformat()
        if isinstance(v, bytes):
            try:
                return v.decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                return str(v)
        if isinstance(v, str):
            return v
        return str(v)
    except Exception:  # noqa: BLE001 - normalization must never raise
        return str(v)


def _type_tag(v: Any) -> str:
    """Coarse type tag used for canonical sorting (mixed types must be sortable)."""
    if v is None:
        return "0none"
    if isinstance(v, bool):
        return "1bool"
    if isinstance(v, float):
        return "2num"
    return "3str"


def _canonical_row_key(normalized: Sequence[Tuple[str, Any]]) -> str:
    """Deterministic, type-stable serialization of a normalized row."""
    parts = []
    for col, val in normalized:
        parts.append(f"{col}\x1f{_type_tag(val)}\x1f{val!r}")
    return "\x1e".join(parts)


def _normalize_row(
    row: Any, column_alias_map: Optional[Dict[str, str]]
) -> Optional[List[Tuple[str, Any]]]:
    """Normalize one row dict → ``[(canonical_column, normalized_value), ...]``
    in the row's original column order (alignment happens in ``_align``).
    Returns ``None`` when the row isn't a mapping.
    """
    if not isinstance(row, dict):
        return None
    items: List[Tuple[str, Any]] = []
    for raw_col, raw_val in row.items():
        col = str(raw_col)
        # Strip the DAX bracket wrapper the ADOMD layer sometimes keeps.
        stripped = col[1:-1] if col.startswith("[") and col.endswith("]") else col
        canonical = None
        if column_alias_map:
            canonical = column_alias_map.get(col) or column_alias_map.get(stripped)
        items.append((canonical or stripped, normalize_value(raw_val)))
    return items


def _align(
    norm_a: List[List[Tuple[str, Any]]], norm_b: List[List[Tuple[str, Any]]]
) -> Tuple[List[List[Tuple[str, Any]]], List[List[Tuple[str, Any]]]]:
    """Reorder both sides so shared column names line up by NAME (sorted) and
    only the leftover columns pair positionally, in their original order.

    Sorting every column by name (the old behaviour) interleaved a shared column
    such as ``Ordering`` between ``Optimized`` and ``Original`` and produced a
    false mismatch (review D-24)."""
    cols_a = [c for c, _ in norm_a[0]] if norm_a else []
    cols_b = [c for c, _ in norm_b[0]] if norm_b else []
    shared = sorted(set(cols_a) & set(cols_b))
    order_a = shared + [c for c in cols_a if c not in shared]
    order_b = shared + [c for c in cols_b if c not in shared]

    def reorder(rows: List[List[Tuple[str, Any]]], order: List[str]) -> List[List[Tuple[str, Any]]]:
        out = []
        for row in rows:
            first: Dict[str, Any] = {}
            dupes: List[Tuple[str, Any]] = []
            for c, v in row:
                if c in first:
                    dupes.append((c, v))
                else:
                    first[c] = v
            ordered = [(c, first[c]) for c in order if c in first]
            ordered += [(c, first[c]) for c in first if c not in order]
            out.append(ordered + dupes)
        return out

    return reorder(norm_a, order_a), reorder(norm_b, order_b)


def _values_equal(
    a: Any, b: Any, tolerance: float, rel_tolerance: Optional[float] = None
) -> Tuple[bool, Optional[str]]:
    """Compare two NORMALIZED values. Returns ``(equal, reason_if_not)``.

    ``tolerance`` is the absolute floor; ``rel_tolerance`` the relative part
    (defaults to ``tolerance`` — the legacy single-tolerance semantics).
    """
    rel = tolerance if rel_tolerance is None else rel_tolerance
    if a is None and b is None:
        return True, None
    # BLANK vs 0 / BLANK vs "" — real DAX semantic differences, called out explicitly.
    if a is None or b is None:
        other = b if a is None else a
        if isinstance(other, float) and other == 0.0:
            return False, "blank_vs_zero"
        if isinstance(other, str) and other == "":
            return False, "blank_vs_empty_string"
        return False, "blank_mismatch"
    if isinstance(a, bool) or isinstance(b, bool):
        # bool only equals bool (never 0/1) — type_mismatch otherwise.
        if isinstance(a, bool) and isinstance(b, bool):
            return (a == b), None if a == b else "value_mismatch"
        return False, "type_mismatch"
    if isinstance(a, float) and isinstance(b, float):
        if a == b:  # includes equal infinities
            return True, None
        if math.isnan(a) and math.isnan(b):
            return True, None
        if math.isinf(a) or math.isinf(b) or math.isnan(a) or math.isnan(b):
            return False, "numeric_difference"
        allowed = max(tolerance, rel * max(abs(a), abs(b)))
        if abs(a - b) <= allowed:
            return True, None
        return False, "numeric_difference"
    if isinstance(a, float) != isinstance(b, float):
        return False, "type_mismatch"
    return (a == b), None if a == b else "value_mismatch"


def _rows_hash(sorted_keys: Sequence[str]) -> str:
    h = hashlib.sha256()
    for key in sorted_keys:
        h.update(key.encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Verdict construction
# ---------------------------------------------------------------------------


def verdict(
    *,
    equivalent: bool,
    reason: str,
    row_count_a: int = 0,
    row_count_b: int = 0,
    differences: Optional[List[Dict[str, Any]]] = None,
    compared_rows: int = 0,
    tolerance_used: float = DEFAULT_TOLERANCE,
    method: str = "rowwise",
    **extra: Any,
) -> Dict[str, Any]:
    """Build the canonical verdict envelope.

    Exposed so callers (handlers, the debug loop) can emit a uniform
    not-equivalent verdict — e.g. ``verdict(equivalent=False,
    reason="query_failed")`` — without hand-rolling the shape.
    """
    diffs = list(differences or [])
    truncated = len(diffs) > MAX_DIFFERENCES
    if truncated:
        diffs = diffs[:MAX_DIFFERENCES]
    out: Dict[str, Any] = {
        "equivalent": bool(equivalent),
        "row_count_a": int(row_count_a),
        "row_count_b": int(row_count_b),
        "differences": diffs,
        "differences_truncated": truncated,
        "reason": reason,
        "compared_rows": int(compared_rows),
        "tolerance_used": float(tolerance_used),
        "method": method,
    }
    out.update(extra)
    return out


# ---------------------------------------------------------------------------
# Public comparison entry point
# ---------------------------------------------------------------------------


def compare_rows(
    rows_a: Any,
    rows_b: Any,
    *,
    tolerance: Optional[float] = None,
    key_columns: Optional[Sequence[str]] = None,
    column_alias_map: Optional[Dict[str, str]] = None,
    rel_tolerance: Optional[float] = None,
    abs_tolerance: Optional[float] = None,
) -> Dict[str, Any]:
    """Compare two DAX result sets for semantic equivalence.

    Args:
        rows_a: rows of the ORIGINAL result (``list[dict]``).
        rows_b: rows of the OPTIMIZED result (``list[dict]``).
        tolerance: LEGACY single tolerance used as both the relative and the
            absolute tolerance. ``None`` (default) = the tight defaults
            ``rel 1e-9`` / ``abs 1e-6``. Invalid values fall back to the defaults.
        rel_tolerance / abs_tolerance: explicit relative / absolute tolerances
            (override ``tolerance``).
        key_columns: OPTIONAL hint. Comparison is order-insensitive without it;
            when given, the listed columns are used first in the canonical sort
            key so reported ``row_index`` values line up with the caller's
            notion of identity. Never required.
        column_alias_map: OPTIONAL mapping of raw result column name →
            canonical name, for declaring the known alias difference between
            the two queries (e.g. ``{"Original": "value", "Optimized":
            "value"}``).

    Returns:
        The verdict dict documented in ``verdict()``. Never raises.
    """

    def _valid(x: Any) -> Optional[float]:
        try:
            f = float(x)
        except (TypeError, ValueError):
            return None
        return f if f >= 0 and f == f else None

    legacy = _valid(tolerance) if tolerance is not None else None
    tol = _valid(abs_tolerance) if abs_tolerance is not None else None
    rel = _valid(rel_tolerance) if rel_tolerance is not None else None
    if tol is None:
        tol = legacy if legacy is not None else DEFAULT_ABS_TOLERANCE
    if rel is None:
        rel = legacy if legacy is not None else DEFAULT_REL_TOLERANCE

    # --- input validation (fail closed) ---------------------------------
    if not isinstance(rows_a, list) or not isinstance(rows_b, list):
        return verdict(
            equivalent=False,
            reason="invalid_input",
            row_count_a=len(rows_a) if isinstance(rows_a, list) else 0,
            row_count_b=len(rows_b) if isinstance(rows_b, list) else 0,
            tolerance_used=tol,
        )

    count_a, count_b = len(rows_a), len(rows_b)

    try:
        norm_a = [_normalize_row(r, column_alias_map) for r in rows_a]
        norm_b = [_normalize_row(r, column_alias_map) for r in rows_b]
    except Exception as e:  # noqa: BLE001 - never raise out of the guard
        logger.debug("semantic_equivalence: normalization failed: %s", e)
        return verdict(
            equivalent=False,
            reason="invalid_input",
            row_count_a=count_a,
            row_count_b=count_b,
            tolerance_used=tol,
        )

    if any(r is None for r in norm_a) or any(r is None for r in norm_b):
        return verdict(
            equivalent=False,
            reason="invalid_input",
            row_count_a=count_a,
            row_count_b=count_b,
            tolerance_used=tol,
        )

    if count_a != count_b:
        return verdict(
            equivalent=False,
            reason="row_count_mismatch",
            row_count_a=count_a,
            row_count_b=count_b,
            tolerance_used=tol,
        )

    if count_a == 0:
        # BOTH sides returned nothing. Two empty sets are trivially equal, but
        # that is a VACUOUS result, not evidence: an unloaded model, an
        # over-restrictive filter context, or a grain column with no matching
        # rows all produce it, and every rewrite — including a wrong one —
        # "passes". Reporting equivalent=True here let a `* 1.5` rewrite be
        # certified against a model whose measures were all BLANK (caught live
        # 2026-08-22 on R0201). Fail closed: the caller must widen the grain or
        # load data before a perf claim can rest on this.
        return verdict(
            equivalent=False,
            reason="no_rows_compared",
            row_count_a=0,
            row_count_b=0,
            compared_rows=0,
            tolerance_used=tol,
            note=(
                "Both queries returned zero rows, so equivalence could not be "
                "demonstrated. Check that the model has data and that the grain "
                "column yields rows under the current filter context."
            ),
        )

    # Column-count mismatch is a hard structural failure.
    widths_a = {len(r) for r in norm_a}  # type: ignore[arg-type]
    widths_b = {len(r) for r in norm_b}  # type: ignore[arg-type]
    if widths_a != widths_b or len(widths_a) != 1:
        return verdict(
            equivalent=False,
            reason="column_count_mismatch",
            row_count_a=count_a,
            row_count_b=count_b,
            tolerance_used=tol,
            columns_a=sorted(widths_a),
            columns_b=sorted(widths_b),
        )

    norm_a, norm_b = _align(norm_a, norm_b)  # type: ignore[arg-type]

    # --- vacuous-truth guard: every compared VALUE cell BLANK on both sides --
    value_cols: Optional[set] = None
    if column_alias_map:
        value_cols = set(column_alias_map.values())
    elif key_columns:
        keys = {str(c) for c in key_columns}
        value_cols = {c for row in norm_a for c, _ in row if c not in keys}  # type: ignore[union-attr]

    def _all_blank(rows: List[List[Tuple[str, Any]]]) -> bool:
        cells = [v for row in rows for c, v in row if value_cols is None or c in value_cols]
        return bool(cells) and all(v is None for v in cells)

    if _all_blank(norm_a) and _all_blank(norm_b):  # type: ignore[arg-type]
        return verdict(
            equivalent=False,
            reason="vacuous_all_blank",
            row_count_a=count_a,
            row_count_b=count_b,
            compared_rows=count_a,
            tolerance_used=tol,
            rel_tolerance_used=rel,
            note=(
                "Every compared value is BLANK on both sides, so equivalence could not "
                "be demonstrated (e.g. the measure is BLANK in this filter context or "
                "the model has no data). Evaluate over a grain/filter where the "
                "original returns values."
            ),
        )

    def sort_key(normalized: List[Tuple[str, Any]]) -> str:
        if key_columns:
            wanted = {str(c) for c in key_columns}
            lead = [kv for kv in normalized if kv[0] in wanted]
            rest = [kv for kv in normalized if kv[0] not in wanted]
            return _canonical_row_key(lead + rest)
        return _canonical_row_key(normalized)

    keys_a = sorted(sort_key(r) for r in norm_a)  # type: ignore[arg-type]
    keys_b = sorted(sort_key(r) for r in norm_b)  # type: ignore[arg-type]

    # --- scale guard: hash instead of per-row diffs ----------------------
    if count_a > ROWWISE_ROW_LIMIT or count_b > ROWWISE_ROW_LIMIT:
        hash_a, hash_b = _rows_hash(keys_a), _rows_hash(keys_b)
        equal = hash_a == hash_b
        return verdict(
            equivalent=equal,
            reason="equivalent" if equal else "hash_mismatch",
            row_count_a=count_a,
            row_count_b=count_b,
            compared_rows=count_a,
            tolerance_used=tol,
            method="hash",
            hash_a=hash_a,
            hash_b=hash_b,
            note=(
                "Row counts exceed the row-wise limit; compared row-multiset "
                "hashes instead of per-row diffs. Numeric tolerance is NOT "
                "applied in hash mode."
            ),
        )

    # --- row-wise comparison on the canonically sorted multisets ---------
    sorted_a = sorted(norm_a, key=sort_key)  # type: ignore[arg-type]
    sorted_b = sorted(norm_b, key=sort_key)  # type: ignore[arg-type]

    differences: List[Dict[str, Any]] = []
    for idx, (ra, rb) in enumerate(zip(sorted_a, sorted_b)):
        for (col_a, val_a), (col_b, val_b) in zip(ra, rb):  # type: ignore[union-attr]
            equal, why = _values_equal(val_a, val_b, tol, rel)
            if equal:
                continue
            differences.append(
                {
                    "row_index": idx,
                    "column": col_a if col_a == col_b else f"{col_a}/{col_b}",
                    "a": val_a,
                    "b": val_b,
                    "reason": why or "value_mismatch",
                }
            )
            if len(differences) > MAX_DIFFERENCES:
                # Keep collecting one past the cap so verdict() flags truncation,
                # then stop — no reason to walk a million rows.
                break
        if len(differences) > MAX_DIFFERENCES:
            break

    if not differences:
        return verdict(
            equivalent=True,
            reason="equivalent",
            row_count_a=count_a,
            row_count_b=count_b,
            compared_rows=count_a,
            tolerance_used=tol,
            rel_tolerance_used=rel,
        )

    blank_vs_zero = any(d["reason"] == "blank_vs_zero" for d in differences)
    return verdict(
        equivalent=False,
        reason="blank_vs_zero" if blank_vs_zero else "value_mismatch",
        row_count_a=count_a,
        row_count_b=count_b,
        differences=differences,
        compared_rows=count_a,
        tolerance_used=tol,
        rel_tolerance_used=rel,
    )
