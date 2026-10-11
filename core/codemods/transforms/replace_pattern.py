"""Regex-based replace across measure expressions (v1 — AST-based is v2)."""

from __future__ import annotations

import re
from typing import Any, Dict

from core.codemods import dax_text
from core.codemods.transforms.base import Edit, PreviewResult, Transform, register


class ReplacePattern(Transform):
    name = "replace_pattern"
    description = (
        "Regex find-replace over DAX expressions. Use named groups for "
        "capture-and-rebuild patterns. v1 is regex-based; AST-based is v2."
    )
    args_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "replacement": {"type": "string"},
            "scope": {"type": "array", "items": {"type": "string"}, "default": ["measures"]},
            **dax_text.SCOPE_SCHEMA,
        },
        "required": ["pattern", "replacement"],
    }
    # A user regex can change meaning on purpose; the structural sanity gate
    # still refuses results that no longer parse.
    semantics_preserving = False

    def preview(self, model: Any, args: Dict[str, Any]) -> PreviewResult:
        out = PreviewResult()
        try:
            rx = re.compile(args.get("pattern", ""))
        except re.error as e:
            out.warnings.append(f"invalid regex: {e}")
            return out
        replacement = args.get("replacement", "")
        scope = set(args.get("scope") or ["measures"])
        if "measures" in scope:
            for tbl, m in dax_text.iter_scoped_measures(model, args):
                expr = m.expression or ""
                if not rx.search(expr):
                    continue
                try:
                    new_expr = rx.sub(replacement, expr)
                except (re.error, IndexError) as e:
                    out.warnings.append(f"invalid replacement: {e}")
                    return out
                if new_expr == expr:
                    continue
                target = dax_text.measure_target(tbl, m)
                out.edits.append(Edit(target=target, before=expr, after=new_expr))
                out.affected_measures.append(target)
        return out

    def apply(self, model: Any, args: Dict[str, Any], preview: PreviewResult) -> Dict[str, Any]:
        return {"applied": [e.target for e in preview.edits]}


register(ReplacePattern())
