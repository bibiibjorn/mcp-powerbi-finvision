"""Extract a subexpression into a new measure and rebind its sites.

2026-09-22 review E-10/E-11: the old transform ran ``str.replace`` (strings and
comments included), rebound sites inside iterators (``SUMX(Sales, [Line])`` over
naked columns) and CALCULATE filter arguments, and never checked ``new_name`` for
collisions — an existing measure was silently OVERWRITTEN and the journal's
``before=""`` made the loss unrecoverable. Now:

- the new name must not exist as a measure (model-scoped) or a column anywhere,
  and ``target_table`` must exist — otherwise nothing is emitted;
- the expression must be a valid measure body (no naked column reference);
- occurrences are matched on the token stream (case-insensitive names, never in
  strings/comments) and only where rebinding is exact: not in a row context or a
  CALCULATE filter argument, and only as a complete operand (a non-atomic
  expression such as ``a + b`` is only rebound where it is a whole argument /
  VAR value / RETURN value, never inside ``a + b * c``);
- the create edit carries ``note="create"`` so rollback DELETES it (K-04).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.codemods import dax_text
from core.codemods.transforms.base import Edit, PreviewResult, Transform, register
from core.dax.tokenizer import Token, TokenType

_NAME_TYPES = (TokenType.FUNCTION, TokenType.KEYWORD, TokenType.IDENTIFIER)


def _key(t: Token) -> Tuple[str, str]:
    from core.dax.dax_names import unbracket, unquote_table

    if t.type in _NAME_TYPES:
        return ("W", t.value.upper())
    if t.type == TokenType.COLUMN_REF:
        return ("C", dax_text.norm_name(unbracket(t.value)))
    if t.type == TokenType.TABLE_REF:
        return ("W", unquote_table(t.value).upper())
    if t.type == TokenType.QUALIFIED_REF:
        from core.dax.dax_names import split_qualified

        tb, c = split_qualified(t.value)
        return ("Q", dax_text.norm_name(tb) + "\x00" + dax_text.norm_name(c))
    return (t.type.name, t.value)


def _keys(code: Sequence[Token]) -> List[Tuple[str, str]]:
    """Canonical keys; ``Sales[Amount]`` (IDENTIFIER+COLUMN_REF) == ``'Sales'[Amount]``."""
    out: List[Tuple[str, str]] = []
    i = 0
    from core.dax.dax_names import unbracket

    while i < len(code):
        t = code[i]
        if (
            t.type == TokenType.IDENTIFIER
            and i + 1 < len(code)
            and code[i + 1].type == TokenType.COLUMN_REF
            and code[i + 1].start == t.end
        ):
            out.append(
                (
                    "Q",
                    dax_text.norm_name(t.value)
                    + "\x00"
                    + dax_text.norm_name(unbracket(code[i + 1].value)),
                )
            )
            out.append(("_", ""))  # placeholder keeps index alignment with code
            i += 2
            continue
        out.append(_key(t))
        i += 1
    return out


def _is_atomic(code: Sequence[Token]) -> bool:
    """True when the expression has no top-level binary operator (a single call,
    reference, literal or parenthesised group) — safe to rebind anywhere."""
    depth = 0
    for t in code:
        if t.type in (TokenType.PAREN_OPEN, TokenType.BRACE_OPEN):
            depth += 1
        elif t.type in (TokenType.PAREN_CLOSE, TokenType.BRACE_CLOSE):
            depth -= 1
        elif depth == 0 and (
            t.type == TokenType.OPERATOR
            or (t.type == TokenType.KEYWORD and t.value.upper() in ("IN", "NOT", "VAR", "RETURN"))
        ):
            return False
    return True


def _whole_operand(code: Sequence[Token], i: int, j: int) -> bool:
    """True when tokens ``code[i:j]`` form a complete operand in their context."""
    prev = code[i - 1] if i > 0 else None
    nxt = code[j] if j < len(code) else None
    before_ok = (
        prev is None
        or prev.type in (TokenType.PAREN_OPEN, TokenType.COMMA)
        or (prev.type == TokenType.KEYWORD and prev.value.upper() == "RETURN")
        or (
            # VAR x = <operand>
            prev.type == TokenType.OPERATOR
            and prev.value == "="
            and i >= 3
            and code[i - 3].type == TokenType.KEYWORD
            and code[i - 3].value.upper() == "VAR"
        )
    )
    after_ok = (
        nxt is None
        or nxt.type in (TokenType.PAREN_CLOSE, TokenType.COMMA)
        or (nxt.type == TokenType.KEYWORD and nxt.value.upper() in ("VAR", "RETURN"))
    )
    return before_ok and after_ok


def _naked_column(expression: str, measure_names: Dict[str, str]) -> Optional[str]:
    """A column reference outside any aggregation/iterator makes an invalid measure."""
    from core.dax.dax_reference_parser import scan_references

    code = dax_text.code_tokens(expression)
    stacks = dax_text.frame_stacks(code)
    by_start = {t.start: k for k, t in enumerate(code)}
    for ref in scan_references(expression, code=code):
        if ref.kind == "table":
            continue
        if ref.kind == "bracket" and dax_text.norm_name(ref.name) in measure_names:
            continue
        if ref.kind == "qualified" and dax_text.norm_name(ref.name) in measure_names:
            continue
        k = by_start.get(ref.start)
        frames = stacks[k] if k is not None else ()
        # MIN/MAX are scalar-safe for hoisting but AGGREGATE a column argument.
        if any(fn in ("MIN", "MAX") for fn, _a in frames):
            continue
        if dax_text.is_hoistable(frames):
            return expression[ref.start : ref.end]
    return None


class ExtractMeasure(Transform):
    name = "extract_measure"
    description = (
        "Lift a subexpression into a new measure and replace its occurrences with "
        "[NewName]. Refuses a name that already exists (measure or column), a missing "
        "target_table and a body with naked columns; rebinds only where the result is "
        "exact (not in a row context / CALCULATE filter argument, complete operands only)."
    )
    args_schema = {
        "type": "object",
        "properties": {
            "expression": {"type": "string"},
            "new_name": {"type": "string"},
            "target_table": {"type": "string"},
            "replace_in_measures": {"type": "array", "items": {"type": "string"}},
            "auto_detect": {"type": "boolean", "default": False},
            **dax_text.SCOPE_SCHEMA,
        },
        "required": ["expression", "new_name", "target_table"],
    }

    def preview(self, model: Any, args: Dict[str, Any]) -> PreviewResult:
        out = PreviewResult()
        expression = (args.get("expression") or "").strip()
        new_name = (args.get("new_name") or "").strip()
        target_table = (args.get("target_table") or "").strip()
        if not expression or not new_name or not target_table:
            out.warnings.append("expression, new_name, target_table are required")
            return out

        tables = {
            dax_text.norm_name(getattr(t, "name", "")): getattr(t, "name", "")
            for t in getattr(model, "tables", []) or []
        }
        if dax_text.norm_name(target_table) not in tables:
            out.warnings.append(f"target_table {target_table!r} not found — nothing extracted")
            return out
        target_table = tables[dax_text.norm_name(target_table)]

        # E-11: measure names are model-scoped and may not clash with columns either.
        measure_names = dax_text.all_measure_names(model)
        if dax_text.norm_name(new_name) in measure_names:
            out.warnings.append(
                f"a measure named {new_name!r} already exists in table "
                f"{measure_names[dax_text.norm_name(new_name)]!r} — refusing to overwrite it"
            )
            return out
        for t in getattr(model, "tables", []) or []:
            for c in getattr(t, "columns", []) or []:
                if dax_text.norm_name(getattr(c, "name", "")) == dax_text.norm_name(new_name):
                    out.warnings.append(
                        f"{new_name!r} clashes with column {getattr(t, 'name', '')}"
                        f"[{getattr(c, 'name', '')}] — choose another name"
                    )
                    return out

        naked = _naked_column(expression, measure_names)
        if naked is not None:
            out.warnings.append(
                f"expression is not a valid measure body: column {naked} is not inside an "
                "aggregation (extracting it from a row context would change its meaning)"
            )
            return out

        pat = dax_text.code_tokens(expression)
        pat_keys = [k for k in _keys(pat) if k != ("_", "")]
        atomic = _is_atomic(pat)
        from core.dax.dax_names import bracket

        new_ref = bracket(new_name)

        out.edits.append(
            Edit(
                target=f"measure:{target_table}[{new_name}]",
                before="",
                after=expression,
                note="create",
            )
        )
        # Calculation items apply to MEASURE REFERENCES, so [New] is transformed by every
        # calculation group while the inline expression it replaces was not (D-21; inline_measure
        # carries the same warning).
        out.warnings.append(
            "Extraction is exact only without calculation groups: calculation items apply to "
            "measure references, so the new measure reference is transformed by them while the "
            "inline expression it replaces was not."
        )

        replace_in = {dax_text.norm_name(r) for r in (args.get("replace_in_measures") or [])}
        auto = bool(args.get("auto_detect", False))
        for tbl, m in dax_text.iter_scoped_measures(model, args):
            ref = f"{getattr(tbl, 'name', '')}[{getattr(m, 'name', '')}]"
            if not (auto or dax_text.norm_name(ref) in replace_in):
                continue
            expr = m.expression or ""
            code = dax_text.code_tokens(expr)
            keys = _keys(code)
            stacks = dax_text.frame_stacks(code)
            edits = []
            skipped_sites = 0
            i = 0
            while i < len(code):
                # match pat_keys against keys starting at i, skipping placeholders
                j, p = i, 0
                while p < len(pat_keys) and j < len(code):
                    if keys[j] == ("_", ""):
                        j += 1
                        continue
                    if keys[j] != pat_keys[p]:
                        break
                    j += 1
                    p += 1
                while j < len(code) and keys[j] == ("_", ""):
                    j += 1
                if p == len(pat_keys) and keys[i] != ("_", ""):
                    safe = not dax_text.has_row_context(stacks[i]) and (
                        atomic or _whole_operand(code, i, j)
                    )
                    if safe:
                        edits.append((code[i].start, code[j - 1].end, new_ref))
                    else:
                        skipped_sites += 1
                    i = j
                    continue
                i += 1
            if skipped_sites:
                out.warnings.append(
                    f"measure:{ref}: {skipped_sites} occurrence(s) left in place (row context, "
                    "CALCULATE filter argument or partial operand — rebinding would change "
                    "the result)"
                )
            if not edits:
                continue
            new_expr = dax_text.splice(expr, edits)
            out.edits.append(Edit(target=f"measure:{ref}", before=expr, after=new_expr))
            out.affected_measures.append(f"measure:{ref}")
        return out

    def apply(self, model: Any, args: Dict[str, Any], preview: PreviewResult) -> Dict[str, Any]:
        return {"applied": [e.target for e in preview.edits]}


register(ExtractMeasure())
