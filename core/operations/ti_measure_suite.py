"""Time-intelligence measure-suite generation.

We can already scaffold the two *structural* halves of a time-intelligence
setup — a date table (``table/generate_calendar``) and calculation groups
(``calculation_group/scaffold``) — but not the measures themselves, so the
repetitive part is still hand-written: for every base measure, a YTD/QTD/MTD/WTD
variant, a prior-year variant, a delta and a delta-%. That is the third leg.

This module is **pure**: it emits ``{name, expression, ...}`` dicts and never
touches TOM. Callers hand the result to ``03_Batch_Operations`` (measures /
create) or ``02_Model_Operations`` (measure/create), which own persistence,
transactions and gating. Keeping generation pure means the DAX shapes are unit
testable without a live model, and a preview costs nothing.

Week-grain variants emit ``TOTALWTD``/week functions, which require a
calendar-based time-intelligence model (CL 1701+) or an explicit week column —
callers get a ``requires`` marker rather than a silent runtime failure.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence

from core.dax.dax_names import quote_table

# --- Variant catalogue ------------------------------------------------------
# Each entry: suffix, a DAX template, and what the template presumes.
#   {m}    -> bracketed base measure reference, e.g. [Sales Amount]
#   {date} -> fully-qualified date column, e.g. 'Date'[Date]
#   {tbl}  -> quoted date table, e.g. 'Date'
_VARIANTS: Dict[str, Dict[str, Any]] = {
    "YTD": {
        "suffix": "YTD",
        "template": "TOTALYTD({m}, {date})",
        "kind": "to_date",
    },
    "QTD": {
        "suffix": "QTD",
        "template": "TOTALQTD({m}, {date})",
        "kind": "to_date",
    },
    "MTD": {
        "suffix": "MTD",
        "template": "TOTALMTD({m}, {date})",
        "kind": "to_date",
    },
    # Week grain: shipped Sept 2025 alongside calendar-based TI.
    "WTD": {
        "suffix": "WTD",
        "template": "TOTALWTD({m}, {date})",
        "kind": "to_date",
        "requires": "calendar-based time intelligence (CL 1701+) or an explicit week column",
    },
    "PY": {
        "suffix": "PY",
        "template": "CALCULATE({m}, SAMEPERIODLASTYEAR({date}))",
        "kind": "prior",
    },
    "PY_YTD": {
        "suffix": "PY YTD",
        "template": "CALCULATE(TOTALYTD({m}, {date}), SAMEPERIODLASTYEAR({date}))",
        "kind": "prior",
    },
    "PM": {
        "suffix": "PM",
        "template": "CALCULATE({m}, DATEADD({date}, -1, MONTH))",
        "kind": "prior",
    },
}

#: Delta variants are derived: they reference the generated sibling, not the base.
_DELTA_VARIANTS: Dict[str, Dict[str, Any]] = {
    "DELTA": {
        "suffix": "vs PY",
        "template": "{m} - {other}",
        "against": "PY",
        "kind": "delta",
    },
    "DELTA_PCT": {
        "suffix": "vs PY %",
        # DIVIDE guards the blank/zero denominator — never bare division.
        "template": "DIVIDE({m} - {other}, {other})",
        "against": "PY",
        "kind": "delta_pct",
        "format_string": "0.0%",
    },
}

ALL_VARIANTS: List[str] = list(_VARIANTS) + list(_DELTA_VARIANTS)

#: Sensible default suite: to-date grains plus a prior-year comparison set.
DEFAULT_VARIANTS: List[str] = ["YTD", "QTD", "MTD", "PY", "DELTA", "DELTA_PCT"]


class TiSuiteError(ValueError):
    """Raised for an unusable request (unknown variant, malformed reference)."""


def _bracket(measure: str) -> str:
    """Return a bracketed measure reference: ``Sales`` -> ``[Sales]``."""
    m = (measure or "").strip()
    if not m:
        raise TiSuiteError("base measure name is empty")
    if m.startswith("[") and m.endswith("]"):
        return m
    if "[" in m or "]" in m:
        raise TiSuiteError(f"malformed measure reference: {measure!r}")
    return f"[{m}]"


def _unbracket(measure: str) -> str:
    m = (measure or "").strip()
    if m.startswith("[") and m.endswith("]"):
        return m[1:-1]
    return m


def _quote_table(table: str) -> str:
    t = (table or "").strip().strip("'")
    if not t:
        raise TiSuiteError("date table name is empty")
    return quote_table(t)


def parse_date_column(date_column: str) -> str:
    """Normalize a date-column reference to ``'Table'[Column]``.

    Accepts ``Date[Date]``, ``'Date'[Date]``, or ``Date.Date``. A bare column
    name is rejected: TI functions need the table qualifier, and guessing it is
    how you silently build measures against the wrong table.
    """
    raw = (date_column or "").strip()
    if not raw:
        raise TiSuiteError("date_column is required (e.g. \"'Date'[Date]\")")
    # Quoted table form: '...'[Col]. The table body may contain '' (a DAX-escaped
    # single quote), so match quoted and unquoted separately rather than with one
    # pattern that forbids quotes outright.
    m = re.match(r"^'((?:[^']|'')+)'\s*\[\s*([^\[\]]+?)\s*\]$", raw)
    if m:
        # Collapse the escape before re-quoting so _quote_table doesn't double it.
        return f"{_quote_table(m.group(1).replace(chr(39) * 2, chr(39)))}[{m.group(2).strip()}]"
    m = re.match(r"^([^'\[\]]+?)\s*\[\s*([^\[\]]+?)\s*\]$", raw)
    if m:
        return f"{_quote_table(m.group(1))}[{m.group(2).strip()}]"
    if "." in raw and "[" not in raw:
        table, _, col = raw.partition(".")
        if table.strip() and col.strip():
            return f"{_quote_table(table)}[{col.strip()}]"
    raise TiSuiteError(
        f"date_column {date_column!r} must be a qualified reference like \"'Date'[Date]\""
    )


def _date_table_of(date_ref: str) -> str:
    return date_ref.split("[", 1)[0]


def generate_suite(
    base_measures: Sequence[str],
    *,
    date_column: str,
    variants: Optional[Iterable[str]] = None,
    target_table: Optional[str] = None,
    display_folder: Optional[str] = None,
    name_pattern: str = "{base} {suffix}",
    format_string: Optional[str] = None,
) -> Dict[str, Any]:
    """Build TI measure definitions for every (base measure x variant) pair.

    Args:
        base_measures: base measure names (bare or bracketed).
        date_column: qualified date column, e.g. ``'Date'[Date]``.
        variants: keys from :data:`ALL_VARIANTS`; defaults to
            :data:`DEFAULT_VARIANTS`.
        target_table: table to create the measures on. Defaults to per-measure
            resolution by the caller (emitted as ``None`` so the persistence
            layer can keep each variant with its base measure).
        display_folder: optional display folder for every generated measure.
        name_pattern: ``{base}`` and ``{suffix}`` placeholders.
        format_string: applied to all generated measures unless the variant
            declares its own (percentages do).

    Returns:
        ``{measures: [...], count, variants_used, requires, warnings}`` — a
        plan, not a mutation. ``measures`` entries are ready to pass to
        ``03_Batch_Operations`` (object_type=measures, operation_kind=create).

    Raises:
        TiSuiteError: unknown variant, bad date column, or empty input.
    """
    if not base_measures:
        raise TiSuiteError("base_measures is empty")
    if "{base}" not in name_pattern or "{suffix}" not in name_pattern:
        raise TiSuiteError("name_pattern must contain both {base} and {suffix}")

    date_ref = parse_date_column(date_column)
    date_tbl = _date_table_of(date_ref)

    requested = list(variants) if variants is not None else list(DEFAULT_VARIANTS)
    if not requested:
        raise TiSuiteError("variants resolved to an empty selection")
    unknown = [v for v in requested if v not in _VARIANTS and v not in _DELTA_VARIANTS]
    if unknown:
        raise TiSuiteError(
            f"unknown variant(s): {', '.join(sorted(unknown))}. "
            f"Known: {', '.join(ALL_VARIANTS)}"
        )

    measures: List[Dict[str, Any]] = []
    warnings: List[str] = []
    requires: Dict[str, str] = {}
    seen: set[str] = set()

    for base in base_measures:
        base_name = _unbracket(base)
        base_ref = _bracket(base)

        # Pass 1: direct variants.
        generated_names: Dict[str, str] = {}
        for key in requested:
            spec = _VARIANTS.get(key)
            if spec is None:
                continue
            name = name_pattern.format(base=base_name, suffix=spec["suffix"])
            expr = spec["template"].format(m=base_ref, date=date_ref, tbl=date_tbl)
            generated_names[key] = name
            if spec.get("requires"):
                requires[key] = spec["requires"]
            # C-14: `measure_name` is what the batch create reads; `name` kept
            # for callers of the preview plan.
            entry: Dict[str, Any] = {
                "name": name,
                "measure_name": name,
                "expression": expr,
                "base_measure": base_name,
                "variant": key,
                "kind": spec["kind"],
            }
            if target_table:
                entry["table_name"] = target_table
            if display_folder:
                entry["display_folder"] = display_folder
            fmt = spec.get("format_string") or format_string
            if fmt:
                entry["format_string"] = fmt
            if name in seen:
                warnings.append(f"duplicate generated name skipped: {name!r}")
                continue
            seen.add(name)
            measures.append(entry)

        # Pass 2: delta variants, which reference a generated sibling.
        for key in requested:
            spec = _DELTA_VARIANTS.get(key)
            if spec is None:
                continue
            against = spec["against"]
            other = generated_names.get(against)
            if other is None:
                # The comparison sibling wasn't requested — emitting a
                # reference to a measure we aren't creating would produce
                # invalid DAX, so skip loudly instead.
                warnings.append(
                    f"{key} skipped for {base_name!r}: requires variant "
                    f"{against!r} in the same request"
                )
                continue
            name = name_pattern.format(base=base_name, suffix=spec["suffix"])
            expr = spec["template"].format(m=_bracket(base_name), other=_bracket(other))
            entry = {
                "name": name,
                "measure_name": name,
                "expression": expr,
                "base_measure": base_name,
                "variant": key,
                "kind": spec["kind"],
                "depends_on": other,
            }
            if target_table:
                entry["table_name"] = target_table
            if display_folder:
                entry["display_folder"] = display_folder
            fmt = spec.get("format_string") or format_string
            if fmt:
                entry["format_string"] = fmt
            if name in seen:
                warnings.append(f"duplicate generated name skipped: {name!r}")
                continue
            seen.add(name)
            measures.append(entry)

    result: Dict[str, Any] = {
        "measures": measures,
        "count": len(measures),
        "variants_used": requested,
        "date_column": date_ref,
    }
    if requires:
        result["requires"] = requires
    if warnings:
        result["warnings"] = warnings
    return result


__all__ = [
    "ALL_VARIANTS",
    "DEFAULT_VARIANTS",
    "TiSuiteError",
    "generate_suite",
    "parse_date_column",
]
