"""Inline a measure — replace every reference to it with its expression body.

2026-09-22 review E-05/E-06: a measure reference ``[M]`` is evaluated as
``CALCULATE(<body of M>)`` — it carries an implicit context transition. The old
transform substituted ``(<body>)``, so ``SUMX(Customer, [Total Sales])`` became a
grand total repeated per row; a trailing ``// comment`` in the body swallowed the
closing paren; refs were matched by a case-sensitive regex that skipped ``T[M]``
and ``]]`` names. Now (lexer-driven, ``core.codemods.dax_text``):

- a site in a row context / CALCULATE filter argument gets ``CALCULATE(\\n<body>\\n)``,
  a site under only scalar-safe functions gets ``(\\n<body>\\n)`` — the body is on
  its own lines so a trailing line comment can never swallow the close paren;
- ``[M]`` and ``Home[M]`` / ``'Home'[M]`` are matched case-insensitively, never in
  strings or comments;
- a caller whose VAR names collide with VARs of the inlined body is skipped;
- ``delete_after`` now defaults to **false**; the transaction deletes only after
  every rewrite committed and a full-model rescan finds no remaining reference.
"""

from __future__ import annotations

import re
from typing import Any, Dict

from core.codemods import dax_text
from core.codemods.transforms.base import Edit, PreviewResult, Transform, register


def parse_measure_ref(ref: str):
    """``T[Name]`` / ``'T'[Name]`` -> ``(T, Name)`` (escapes undone) or ``(None, None)``."""
    m = re.match(r"^\s*(?P<t>'(?:[^']|'')*'|[^\[']+?)\s*\[(?P<m>.+)\]\s*$", ref or "")
    if not m:
        return None, None
    t = m.group("t")
    if t.startswith("'"):
        t = t[1:-1].replace("''", "'")
    return t.strip(), m.group("m").replace("]]", "]")


def find_measure(model: Any, table: str, name: str):
    for tbl in getattr(model, "tables", []) or []:
        if dax_text.norm_name(getattr(tbl, "name", "")) != dax_text.norm_name(table):
            continue
        for m in getattr(tbl, "measures", []) or []:
            if dax_text.norm_name(getattr(m, "name", "")) == dax_text.norm_name(name):
                return tbl, m
    return None, None


class InlineMeasure(Transform):
    name = "inline_measure"
    description = (
        "Replace every reference to a measure ([M] and Home[M], case-insensitive) with its "
        "body. Sites in a row context / CALCULATE filter argument get CALCULATE(<body>) to "
        "keep the implicit context transition. delete_after (default false) removes the "
        "measure only when every rewrite committed and no reference remains anywhere "
        "(measures, calc columns/items, RLS, format strings, report visuals)."
    )
    args_schema = {
        "type": "object",
        "properties": {
            "measure_ref": {"type": "string", "description": "T[Name]"},
            "delete_after": {"type": "boolean", "default": False},
            **dax_text.SCOPE_SCHEMA,
        },
        "required": ["measure_ref"],
    }

    @staticmethod
    def _parse_ref(ref: str):
        return parse_measure_ref(ref)

    def preview(self, model: Any, args: Dict[str, Any]) -> PreviewResult:
        out = PreviewResult()
        tname, mname = parse_measure_ref(args.get("measure_ref", ""))
        if not tname or not mname:
            out.warnings.append("measure_ref must be in form T[Name]")
            return out

        home_tbl, target = find_measure(model, tname, mname)
        if target is None:
            out.warnings.append(f"measure {tname}[{mname}] not found")
            return out
        home = getattr(home_tbl, "name", tname)
        mname = getattr(target, "name", mname)

        body = (target.expression or "").strip()
        if not body:
            out.warnings.append(f"measure {home}[{mname}] has an empty expression")
            return out
        body_vars = set(dax_text.declared_var_names(body))
        names = dax_text.all_measure_names(model)
        from core.dax.dax_reference_parser import scan_references

        if any(
            r.kind == "bracket" and dax_text.norm_name(r.name) in names
            for r in scan_references(body)
        ):
            out.warnings.append(
                "target has nested measure refs — they stay measure references after inlining"
            )
        out.warnings.append(
            "Inlining is exact only without calculation groups: calculation items apply to "
            "measure references, not to inlined expressions. The target's format string "
            "(static or dynamic) is not carried over to the callers."
        )

        for tbl, m in dax_text.iter_scoped_measures(model, args):
            if m is target:
                continue
            expr = m.expression or ""
            sites = dax_text.measure_ref_sites(expr, mname, home)
            if not sites:
                continue
            target_id = dax_text.measure_target(tbl, m)
            clash = body_vars & set(dax_text.declared_var_names(expr))
            if clash:
                out.warnings.append(
                    f"{target_id} skipped: VAR name(s) {sorted(clash)} also declared in the "
                    "inlined body (shadowing is not allowed / not verified)"
                )
                out.skipped.append(target_id)
                continue
            edits = []
            for s in sites:
                wrapped = (
                    f"CALCULATE(\n{body}\n)"
                    if dax_text.has_row_context(s.frames)
                    else f"(\n{body}\n)"
                )
                edits.append((s.start, s.end, wrapped))
            new_expr = dax_text.splice(expr, edits)
            out.edits.append(Edit(target=target_id, before=expr, after=new_expr))
            out.affected_measures.append(target_id)
        if args.get("delete_after", False):
            out.warnings.append(
                f"delete_after=true: {home}[{mname}] is deleted only if every rewrite commits "
                "and a full-model rescan finds no remaining reference (report visuals are "
                "checked when a PBIP path is known; otherwise the delete is skipped)"
            )
        return out

    def apply(self, model: Any, args: Dict[str, Any], preview: PreviewResult) -> Dict[str, Any]:
        """Declare WHAT changes: the applied edits + (when delete_after) the target
        to delete. The transaction layer persists, guards and journals it."""
        result: Dict[str, Any] = {"applied": [e.target for e in preview.edits]}
        if args.get("delete_after", False) and preview.edits:
            tname, mname = parse_measure_ref(args.get("measure_ref", ""))
            home_tbl, target = find_measure(model, tname or "", mname or "")
            if target is not None:
                result["delete"] = {
                    "table": getattr(home_tbl, "name", tname),
                    "measure": getattr(target, "name", mname),
                    "expression": getattr(target, "expression", None),
                }
        return result


register(InlineMeasure())
