"""Turn copy-pasted measure bodies into one DAX UDF and rewrite the measures as calls (F-15).

Input: ``measures`` (2+ ``'Table[Name]'`` refs whose bodies share ONE token structure - the groups
``05_DAX_Intelligence find_clones`` reports as ``structural`` / ``extractable_by_transform``) and
the new ``function_name``. The differing literals / references become the parameters
(``core.dax.clone_detector``: literals are ``Scalar ... Val``, column / table references the typed
reference, a differing measure ``MeasureRef`` unless every use is in the caller's own context).

Edits: ``function:<Name>`` (note ``create``: the transaction creates the function BEFORE it
validates and writes the measures, and deletes it again on any failure / rollback) + one
``measure:T[M]`` rewrite per measure: the body becomes ``Name ( args )`` (a leading comment block
is kept).

Refuses (preview ``success: false``, apply writes nothing):
- compatibility level below 1702 or unknown (``compatibility_level_too_low`` /
  ``compatibility_level_unknown``) - UDFs need CL 1702, and this op NEVER raises the level;
- a function that already exists, a name that is not a valid function name or collides with a
  built-in function;
- measures that do not share one structure, or identical bodies (nothing to parameterise).

Verification is the standard one for a semantics-preserving refactor: the rewritten measures are
evaluated at the grand total before and after (``15_DAX_Refactor apply`` rolls the whole plan back
on a difference). Per-slice equivalence is NOT proven - sweep the real grains before relying on it.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from core.codemods import dax_text
from core.codemods.transforms.base import Edit, PreviewResult, Transform, register
from core.dax import clone_detector

_FN_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


def _refuse(out: PreviewResult, error_type: str, error: str, **extra: Any) -> PreviewResult:
    out.refusal = {"error_type": error_type, "error": error, **extra}
    out.warnings.append(error)
    return out


class ExtractUdf(Transform):
    name = "extract_udf"
    description = (
        "Create a DAX UDF from 2+ measures with the same body structure (the differing literals / "
        "column / table / measure references become parameters, val or expr per the canon) and "
        "rewrite those measures as calls. Needs compatibility level >= 1702 (never raised "
        "here); the function is created first and removed again on any failure; the rewrite is "
        "value-verified at the grand total and rolled back on a difference."
    )
    args_schema = {
        "type": "object",
        "properties": {
            "measures": {
                "type": "array",
                "items": {"type": "string"},
                "description": "2+ measures ('Table[Name]') to fold into the function.",
            },
            "function_name": {"type": "string"},
            "parameter_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional names for the parameters, in order of first use.",
            },
            "description": {"type": "string", "description": "Function description."},
        },
        "required": ["measures", "function_name"],
    }
    persistable = True
    semantics_preserving = True

    def preview(self, model: Any, args: Dict[str, Any]) -> PreviewResult:
        out = PreviewResult()
        fn = str(args.get("function_name") or "").strip()
        refs = [str(r) for r in (args.get("measures") or [])]
        if not fn or len(refs) < 2:
            out.warnings.append("function_name and at least two measures are required")
            return out
        if not _FN_NAME.match(fn):
            return _refuse(
                out,
                "invalid_function_name",
                f"{fn!r} is not a valid function name "
                "(letters, digits, underscore; '.' separates namespaces)",
            )
        from core.dax.knowledge.function_db import DaxFunctionDatabase

        if DaxFunctionDatabase.get().is_function(fn):
            return _refuse(
                out,
                "invalid_function_name",
                f"{fn!r} is a built-in DAX function name - choose another",
            )

        cl = getattr(model, "compatibility_level", None)
        if cl is None:
            return _refuse(
                out,
                "compatibility_level_unknown",
                "the model's compatibility level could not be read - UDFs need "
                f"{clone_detector.MIN_UDF_COMPATIBILITY_LEVEL}; nothing extracted "
                "(this op never raises the level)",
                required_compatibility_level=clone_detector.MIN_UDF_COMPATIBILITY_LEVEL,
            )
        if int(cl) < clone_detector.MIN_UDF_COMPATIBILITY_LEVEL:
            return _refuse(
                out,
                "compatibility_level_too_low",
                f"the model is at compatibility level {int(cl)}; DAX UDFs need "
                f"{clone_detector.MIN_UDF_COMPATIBILITY_LEVEL}. A level upgrade is irreversible "
                "and must be a separate, explicitly approved change - nothing extracted.",
                compatibility_level=int(cl),
                required_compatibility_level=clone_detector.MIN_UDF_COMPATIBILITY_LEVEL,
            )
        existing = {str(n).lower() for n in (getattr(model, "udf_names", None) or ())}
        if fn.lower() in existing:
            return _refuse(
                out,
                "already_exists",
                f"a function named {fn!r} already exists - refusing to overwrite it",
            )

        picked: List[Dict[str, Any]] = []
        missing: List[str] = []
        for ref in refs:
            hits = list(dax_text.iter_scoped_measures(model, {"measures": [ref]}))
            if len(hits) != 1:
                missing.append(ref)
                continue
            tbl, m = hits[0]
            picked.append(
                {
                    "table": getattr(tbl, "name", ""),
                    "name": getattr(m, "name", ""),
                    "expression": getattr(m, "expression", "") or "",
                }
            )
        if missing:
            return _refuse(
                out,
                "measure_not_found",
                f"measure(s) not found (or ambiguous - qualify as 'Table[Name]'): {missing}",
            )

        names = set(dax_text.all_measure_names(model))
        analysis = clone_detector.analyse_measures(
            picked,
            measure_names=names,
            parameter_names=args.get("parameter_names") or None,
        )
        if not analysis["ok"]:
            out.warnings.append(analysis["reason"])
            if analysis.get("differences"):
                out.warnings.append(f"differences: {analysis['differences']}")
            return out
        info = analysis["analysis"]
        if info["match"] == "exact_duplicate":
            out.warnings.append(
                "the bodies are identical - there is nothing to parameterise; keep one measure and "
                "reference it from the others instead"
            )
            return out
        members = analysis["members"]
        params = info["params"]
        if args.get("parameter_names") and len(args["parameter_names"]) != len(params):
            out.warnings.append(
                f"parameter_names has {len(args['parameter_names'])} name(s) but the bodies differ "
                f"in {len(params)} place(s): {[p.name for p in params]} were used for the rest"
            )

        udf = clone_detector.build_udf(members, params, fn)
        out.edits.append(
            Edit(target=f"function:{fn}", before="", after=udf["expression"], note="create")
        )
        calls = {c["member"]: c["call"] for c in udf["calls"]}
        for m in members:
            expr = m.norm.expression
            first, last = m.units[0], m.units[-1]
            prefix = expr[: first.start]
            suffix = expr[last.end :]
            keep_prefix = prefix if prefix.strip() else ""
            keep_suffix = suffix if suffix.strip() else ""
            after = f"{keep_prefix}{calls[m.ref]}{keep_suffix}".strip()
            inner_comments = _inner_comments(expr, first.start, last.end)
            if inner_comments:
                out.warnings.append(
                    f"measure:{m.ref}: {inner_comments} comment(s) inside the body are not carried "
                    "to the call (they stay in the journal's before text)"
                )
            out.edits.append(Edit(target=f"measure:{m.ref}", before=expr, after=after))
            out.affected_measures.append(f"measure:{m.ref}")
        for prm in params:
            if prm.mode == "expr" and len(prm.positions) > 1:
                out.warnings.append(
                    f"parameter {prm.name} ({prm.type_hint}) is lazy and used {len(prm.positions)} "
                    "times in the body: it is evaluated at each use, like the original copies"
                )
        out.warnings += [
            "A UDF body gets no automatic context transition: call the function only where the "
            "measure body already ran (a measure reference), not from an iterator row context.",
            "Verification at apply is the grand total only; sweep the real grains (compare / "
            "equivalence over the visuals' filter contexts) before relying on the rewrite.",
        ]
        return out

    def apply(self, model: Any, args: Dict[str, Any], preview: PreviewResult) -> Dict[str, Any]:
        fn = next(
            (
                e.target[len("function:") :]
                for e in preview.edits
                if e.target.startswith("function:")
            ),
            None,
        )
        return {
            "applied": [e.target for e in preview.edits],
            "function": {"name": fn, "description": (args.get("description") or None)},
        }


def _inner_comments(expr: str, lo: int, hi: int) -> int:
    from core.dax.tokenizer import TokenType

    lexer = dax_text.get_lexer()
    return sum(
        1
        for t in lexer.tokenize(expr)
        if t.type in (TokenType.COMMENT_LINE, TokenType.COMMENT_BLOCK) and lo <= t.start < hi
    )


register(ExtractUdf())
