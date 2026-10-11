"""Swap one table for another across measure expressions.

2026-09-22 review E-10: the regex version only rewrote ``Old[col]``, so
``SUMX(Sales, Sales[Amount])`` became the half-swapped ``SUMX(Sales, Sales2[Amount])``;
it rewrote qualified MEASURE refs (``Sales[Total Sales]`` -> ``Sales2[...]``, an
error), matched case-sensitively and claimed to cover calc columns / RLS. Now it
is lexer-driven (``scan_references``): every table reference (``Old``, ``'Old'``,
``Old[c]``, ``'Old'[c]``, case-insensitive, never inside strings/comments) is
swapped, ``column_map`` renames the column part, and a qualified reference whose
name is a MEASURE of the old table is left alone (reported as a warning).
Scope: measures only.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from core.codemods import dax_text
from core.codemods.transforms.base import Edit, PreviewResult, Transform, register
from core.dax.dax_names import bracket


class SwapTableReference(Transform):
    name = "swap_table_reference"
    description = (
        "Rewrite every reference to from_table (bare/quoted table refs and Old[Col]) to "
        "to_table across MEASURE expressions (column_map renames columns). Qualified "
        "measure refs Old[Measure] are left alone."
    )
    args_schema = {
        "type": "object",
        "properties": {
            "from_table": {"type": "string"},
            "to_table": {"type": "string"},
            "column_map": {"type": "object", "additionalProperties": {"type": "string"}},
            **dax_text.SCOPE_SCHEMA,
        },
        "required": ["from_table", "to_table"],
    }
    semantics_preserving = False  # points the measures at a different table

    def preview(self, model: Any, args: Dict[str, Any]) -> PreviewResult:
        from core.dax.dax_reference_parser import scan_references

        out = PreviewResult()
        old, new = args.get("from_table"), args.get("to_table")
        if not old or not new:
            out.warnings.append("from_table and to_table are required")
            return out
        col_map = {dax_text.norm_name(k): v for k, v in (args.get("column_map") or {}).items()}
        old_n = dax_text.norm_name(old)

        # A bare identifier stays bare (existing callers rely on it); anything
        # else is quoted ('Fact Sales').
        new_ref = (
            new
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", new or "")
            else ("'" + new.replace("'", "''") + "'")
        )
        # Measures hosted by the old table: Old[M] is a measure ref, not a column.
        old_measures = set()
        for tbl in getattr(model, "tables", []) or []:
            if dax_text.norm_name(getattr(tbl, "name", "")) == old_n:
                old_measures = {
                    dax_text.norm_name(getattr(m, "name", ""))
                    for m in getattr(tbl, "measures", []) or []
                }
        known = {
            dax_text.norm_name(getattr(t, "name", "")) for t in getattr(model, "tables", []) or []
        } | {old_n}

        for tbl, m in dax_text.iter_scoped_measures(model, args):
            expr = m.expression or ""
            edits: List[Tuple[int, int, str]] = []
            kept_measure_refs = 0
            for ref in scan_references(expr, known_tables=known):
                if dax_text.norm_name(ref.table) != old_n:
                    continue
                if ref.kind == "table":
                    edits.append((ref.start, ref.end, new_ref))
                elif ref.kind == "qualified":
                    if dax_text.norm_name(ref.name) in old_measures:
                        kept_measure_refs += 1
                        continue
                    mapped = col_map.get(dax_text.norm_name(ref.name))
                    edits.append(
                        (ref.start, ref.end, new_ref + bracket(mapped if mapped else ref.name))
                    )
            target = dax_text.measure_target(tbl, m)
            if kept_measure_refs:
                out.warnings.append(
                    f"{target}: {kept_measure_refs} qualified measure reference(s) "
                    f"{old}[<measure>] left unchanged (measures do not move with a table swap)"
                )
            if not edits:
                continue
            new_expr = dax_text.splice(expr, edits)
            if new_expr == expr:
                continue
            out.edits.append(Edit(target=target, before=expr, after=new_expr))
            out.affected_measures.append(target)
        out.warnings.append(
            "swap_table_reference rewrites MEASURES only — calculated columns, calculation "
            "items, RLS filters and report visuals still reference the old table."
        )
        return out

    def apply(self, model: Any, args: Dict[str, Any], preview: PreviewResult) -> Dict[str, Any]:
        return {"applied": [e.target for e in preview.edits]}


register(SwapTableReference())
