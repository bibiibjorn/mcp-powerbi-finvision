"""Lexer-driven DAX rename helpers — never touch strings or comments.

Replacement for the regex rename cascades (``rename_cascade.py``, TMDL
``bulk_rename``, the PBIR bridge). Built on ``scan_references`` so every rule of
``core.dax.dax_reference_parser`` applies: escapes are understood (``'O''Brien'``,
``[a]]b]``), string literals / comments / format strings are left alone, and
names are matched case-insensitively (DAX names are case-insensitive) while
every byte outside a rewritten reference is preserved.

All functions return ``(new_expression, replacement_count)``; the input is
returned unchanged (count 0) when there is nothing to do or the input is not a
string.

Signatures::

    rename_table_refs(expr, old, new) -> (str, int)
    rename_column_refs(expr, table, old, new, *, include_unqualified=False) -> (str, int)
    rename_measure_refs(expr, old, new, *, home_table=None) -> (str, int)
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from core.dax.dax_names import bracket, quote_table
from core.dax.dax_reference_parser import scan_references

__all__ = ["rename_table_refs", "rename_column_refs", "rename_measure_refs"]

Edit = Tuple[int, int, str]


def _norm(name: str) -> str:
    return " ".join((name or "").lower().split())


#: P-13: skip the lexer for an expression that cannot contain the old name. Tests
#: switch this off to prove the pre-filter never changes a result.
_PREFILTER_ENABLED = True
_NEEDLE_SPLIT = re.compile(r"[\s'\]\[]+")


def _absent(expr: str, name: str) -> bool:
    """True when *expr* cannot reference *name* (so scanning it is pointless).

    A reference compares by ``_norm`` (lower-cased, whitespace collapsed); in the
    source the name may carry quote / bracket escapes (``'O''Brien'``,
    ``[a]]b]``) and arbitrary whitespace. The needle is therefore the longest
    run of the normalised name without whitespace, quotes or brackets -- it must
    appear verbatim (case-insensitively) in any expression that references the
    name. Non-ASCII needles are never filtered (``str.lower`` is not
    context-free for e.g. the Greek final sigma).
    """
    if not _PREFILTER_ENABLED:
        return False
    norm = _norm(name)
    if not norm or not norm.isascii():
        return False
    needle = max(_NEEDLE_SPLIT.split(norm), key=len)
    return bool(needle) and needle not in expr.lower()


def _apply(expr: str, edits: List[Edit]) -> Tuple[str, int]:
    if not edits:
        return expr, 0
    edits = sorted(set(edits), key=lambda e: e[0])
    out: List[str] = []
    cursor = 0
    count = 0
    for start, end, text in edits:
        if start < cursor:  # overlapping edit (cannot happen with scan spans) — skip
            continue
        out.append(expr[cursor:start])
        out.append(text)
        cursor = end
        count += 1
    out.append(expr[cursor:])
    return "".join(out), count


def rename_table_refs(expr: str, old: str, new: str) -> Tuple[str, int]:
    """Rename every reference to table *old* as ``quote_table(new)``.

    Covers ``'Old'[c]``, ``Old[c]``, ``'Old'`` and bare ``Old`` in table
    positions (``ALL(Old)``, ``COUNTROWS(Old)``, ``RELATEDTABLE(Old)``,
    ``CALCULATE([m], Old)``, ``EVALUATE Old`` …). A bare identifier equal to
    *old* is always treated as the table (it cannot be a VAR — DAX forbids a VAR
    named like a table — and declared VAR/UDF-parameter names are excluded).
    """
    if not isinstance(expr, str) or not expr or not old or old == new:
        return expr, 0
    if _absent(expr, old):
        return expr, 0
    target = _norm(old)
    replacement = quote_table(new)
    edits: List[Edit] = []
    for ref in scan_references(expr, known_tables={target}):
        if ref.kind in ("qualified", "table") and _norm(ref.table) == target:
            edits.append((ref.table_start, ref.table_end, replacement))
    return _apply(expr, edits)


def rename_column_refs(
    expr: str,
    table: str,
    old: str,
    new: str,
    *,
    include_unqualified: bool = False,
) -> Tuple[str, int]:
    """Rename column *old* of *table* to *new*.

    Only ``'Table'[old]`` / ``Table[old]`` references of THAT table are rewritten
    (``MySales[Amount]`` is not a match for table ``Sales``). A lone ``[old]`` is
    rewritten only with ``include_unqualified=True`` — pass it for expressions
    evaluated in the row context of *table* (its calculated columns, RLS filters
    on it), never for measures, where ``[old]`` is a measure reference.
    """
    if not isinstance(expr, str) or not expr or not old or old == new:
        return expr, 0
    if _absent(expr, old):
        return expr, 0
    t_norm = _norm(table)
    c_norm = _norm(old)
    replacement = bracket(new)
    edits: List[Edit] = []
    for ref in scan_references(expr):
        if _norm(ref.name) != c_norm:
            continue
        if ref.kind == "qualified" and _norm(ref.table) == t_norm:
            edits.append((ref.name_start, ref.name_end, replacement))
        elif ref.kind == "bracket" and include_unqualified:
            edits.append((ref.name_start, ref.name_end, replacement))
    return _apply(expr, edits)


def rename_measure_refs(
    expr: str,
    old: str,
    new: str,
    *,
    home_table: Optional[str] = None,
) -> Tuple[str, int]:
    """Rename measure *old* to *new*.

    Rewrites every lone ``[old]``; with *home_table* (the table that hosts the
    measure) also ``'Home'[old]`` / ``Home[old]``. References qualified by any
    other table are columns and are left alone.
    """
    if not isinstance(expr, str) or not expr or not old or old == new:
        return expr, 0
    if _absent(expr, old):
        return expr, 0
    m_norm = _norm(old)
    h_norm = _norm(home_table) if home_table else None
    replacement = bracket(new)
    edits: List[Edit] = []
    for ref in scan_references(expr):
        if _norm(ref.name) != m_norm:
            continue
        if ref.kind == "bracket":
            edits.append((ref.name_start, ref.name_end, replacement))
        elif ref.kind == "qualified" and h_norm is not None and _norm(ref.table) == h_norm:
            edits.append((ref.name_start, ref.name_end, replacement))
    return _apply(expr, edits)
