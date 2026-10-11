"""Move multiple measures to different tables in dependency-aware order."""

from __future__ import annotations

from typing import Any, Dict, List

from core.codemods import dax_text
from core.codemods.transforms.base import Edit, PreviewResult, Transform, register


def _parse_ref(ref: str):
    """``'My Table'[M]`` / ``Table[M]`` -> ``(table, measure)`` (quotes and ``]]`` escapes
    handled by the shared scope parser), else ``(None, None)``."""
    table, name = dax_text._parse_scope_ref(ref or "")
    return (table, name) if table else (None, None)


def _resolve(model: Any, ref: str):
    """The measure named by *ref*; table and measure names compare case-insensitively
    (DAX names are case-insensitive)."""
    tname, mname = _parse_ref(ref)
    if not tname:
        return None
    for tbl in getattr(model, "tables", []) or []:
        if dax_text.norm_name(getattr(tbl, "name", "")) != dax_text.norm_name(tname):
            continue
        for m in getattr(tbl, "measures", []) or []:
            if dax_text.norm_name(getattr(m, "name", "")) == dax_text.norm_name(mname):
                return m
    return None


def _internal_refs(expression: str) -> List[str]:
    """Normalised names of every measure-like reference in *expression* (lone ``[M]`` and
    ``T[M]`` / ``'T'[M]``), token-based: strings and comments never count."""
    from core.dax.dax_reference_parser import scan_references

    names: List[str] = []
    for r in scan_references(expression or ""):
        if r.kind in ("bracket", "qualified"):
            names.append(dax_text.norm_name(r.name))
    return names


class MoveMeasureBatch(Transform):
    name = "move_measure_batch"
    description = (
        "Move N measures to different tables. Dependency-aware ordering: "
        "dependencies first. PREVIEW-ONLY: the emitted edits are move MARKERS "
        "(after='table=<Dst>', note='move'), not measure bodies, so "
        "15_DAX_Refactor apply cannot persist them — apply returns "
        "error_type='transform_not_persistable'. Perform the actual move via "
        "02_Model_Operations measure/move or 03_Batch_Operations measures/move."
    )
    args_schema = {
        "type": "object",
        "properties": {
            "moves": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "measure_ref": {"type": "string"},
                        "target_table": {"type": "string"},
                    },
                    "required": ["measure_ref", "target_table"],
                },
            },
        },
        "required": ["moves"],
    }
    persistable = False

    def preview(self, model: Any, args: Dict[str, Any]) -> PreviewResult:
        out = PreviewResult()
        moves = args.get("moves", [])
        ref_to_measure = {}
        for mv in moves:
            ref = mv.get("measure_ref")
            target_tbl = mv.get("target_table")
            m = _resolve(model, ref)
            if m is None:
                out.warnings.append(f"measure {ref} not found")
                continue
            ref_to_measure[ref] = (m, target_tbl)

        ordered: List[str] = []
        remaining = dict(ref_to_measure)
        max_iters = len(remaining) * 2 + 1
        i = 0
        while remaining and i < max_iters:
            i += 1
            for ref, (m, _t) in list(remaining.items()):
                deps = set(_internal_refs(m.expression or ""))
                still_dep = any(
                    other_ref != ref and dax_text.norm_name(_parse_ref(other_ref)[1] or "") in deps
                    for other_ref in remaining
                )
                if not still_dep:
                    ordered.append(ref)
                    remaining.pop(ref)
        ordered.extend(remaining)

        for ref in ordered:
            m, target_tbl = ref_to_measure[ref]
            out.edits.append(
                Edit(
                    target=f"measure:{ref}",
                    before=f"table={getattr(m, 'table_name', None) or _parse_ref(ref)[0]}",
                    after=f"table={target_tbl}",
                    note="move",
                )
            )
            out.affected_measures.append(f"measure:{ref}")
        return out

    def apply(self, model: Any, args: Dict[str, Any], preview: PreviewResult) -> Dict[str, Any]:
        return {"applied": [e.target for e in preview.edits]}


register(MoveMeasureBatch())
