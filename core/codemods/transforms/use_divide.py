"""use_divide — rewrite a safe binary ``a / b`` into ``DIVIDE(a, b)``.

DIVIDE() guards against divide-by-zero (returns BLANK instead of an error),
so converting raw ``/`` is a common best-practice fix. This transform is
deliberately CONSERVATIVE: a refactor must never emit semantically-wrong DAX.
It only rewrites when an expression is a SINGLE top-level binary division
(optionally wrapped in one pair of outer parens) — i.e. the division's
operands are unambiguously the whole left and right sides. Anything with
mixed-precedence operators at the top level (``a + b / c``) is left unchanged.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.codemods import dax_text
from core.codemods.transforms.base import Edit, PreviewResult, Transform, register
from core.dax.tokenizer import DaxLexer, TokenType


class UseDivide(Transform):
    name = "use_divide"
    description = (
        "Rewrite a safe binary 'a / b' expression to DIVIDE(a, b) "
        "(divide-by-zero safe). Conservative: only top-level single divisions "
        "with unambiguous operands are rewritten; mixed-precedence expressions "
        "are skipped with a warning."
    )
    args_schema = {
        "type": "object",
        "properties": {
            "scope": {"type": "array", "items": {"type": "string"}, "default": ["measures"]},
            **dax_text.SCOPE_SCHEMA,
        },
    }
    semantics_preserving = False  # '/' errors/Infinity become BLANK

    _lexer = DaxLexer()

    # Top-level tokens that make the operands of '/' ambiguous (E-10: &&, ||,
    # ==, IN, NOT were missing, so `[x] / [y] && [z]` became DIVIDE([x], [y] && [z])).
    _GUARD_OPS = ("+", "-", "*", "&", "=", "==", "<", ">", "<=", ">=", "<>", "&&", "||")
    _GUARD_KEYWORDS = ("IN", "NOT", "VAR", "RETURN")

    @classmethod
    def _rewrite(cls, expr: str) -> Optional[str]:
        """Return the DIVIDE(...) rewrite, or None if not a safe single division.

        Operands are sliced from CODE tokens only (E-10): a trailing
        ``// comment`` stays after the rewritten call instead of swallowing its
        closing paren, and a comment between an operand and the ``/`` makes
        the rewrite refuse.
        """
        from core.dax.tokenizer.lexer import TRIVIA_TYPES

        if not expr or not expr.strip():
            return None
        full = cls._lexer.tokenize(expr)
        code_idx = [i for i, t in enumerate(full) if t.type not in TRIVIA_TYPES]
        if not code_idx:
            return None
        tokens = [full[i] for i in code_idx]
        first, last = tokens[0], tokens[-1]
        prefix, suffix = expr[: first.start], expr[last.end :]

        # Strip a single fully-enclosing pair of parens: ( ... ).
        if tokens[0].type == TokenType.PAREN_OPEN and tokens[-1].type == TokenType.PAREN_CLOSE:
            depth = 0
            encloses = True
            for i, t in enumerate(tokens):
                if t.type == TokenType.PAREN_OPEN:
                    depth += 1
                elif t.type == TokenType.PAREN_CLOSE:
                    depth -= 1
                    if depth == 0 and i != len(tokens) - 1:
                        encloses = False
                        break
            if encloses:
                inner = cls._rewrite(expr[tokens[0].end : tokens[-1].start])
                if inner is None:
                    return None
                return prefix + inner.strip() + suffix

        depth = 0
        slash: List[int] = []  # index into tokens
        for k, t in enumerate(tokens):
            if t.type in (TokenType.PAREN_OPEN, TokenType.BRACE_OPEN):
                depth += 1
            elif t.type in (TokenType.PAREN_CLOSE, TokenType.BRACE_CLOSE):
                depth -= 1
            elif depth == 0 and t.type == TokenType.OPERATOR and t.value == "/":
                slash.append(k)
            elif depth == 0 and (
                (t.type == TokenType.OPERATOR and t.value in cls._GUARD_OPS)
                or (t.type == TokenType.KEYWORD and t.value.upper() in cls._GUARD_KEYWORDS)
            ):
                return None  # mixed precedence / VAR block — ambiguous, skip
        if len(slash) != 1:
            return None
        k = slash[0]
        if k == 0 or k == len(tokens) - 1:
            return None
        lhs_first, lhs_last = tokens[0], tokens[k - 1]
        rhs_first, rhs_last = tokens[k + 1], tokens[-1]
        # Anything but whitespace between an operand and the '/' is a comment.
        if (
            expr[lhs_last.end : tokens[k].start].strip()
            or expr[tokens[k].end : rhs_first.start].strip()
        ):
            return None
        # A constant non-zero denominator ([Sales] / 1000, / (60 * 60 * 24), / POWER(10, 6)) is the
        # canon's exception 1 and the analyzer does not flag it: DIVIDE would only add noise.
        from core.dax.analyzer.structure import const_expression

        const = const_expression(tokens[k + 1 :])
        if const is not None and const == const and const != 0.0 and abs(const) != float("inf"):
            return None
        lhs = expr[lhs_first.start : lhs_last.end]
        rhs = expr[rhs_first.start : rhs_last.end]
        # Both slices end on a code token, so a line comment inside an operand
        # always ends before the ', ' / ')' we append.
        return f"{prefix}DIVIDE({lhs}, {rhs}){suffix}"

    @staticmethod
    def _directquery_only(model: Any, expr: str) -> bool:
        """True when every table *expr* references is a known DirectQuery table.

        Duck-typed: a model table exposes ``storage_mode`` (``"directquery"`` / ``"import"`` /
        ...); a model without that attribute is treated as unknown (Import advice applies)."""
        from core.dax.analyzer.structure import ref_table

        modes = {}
        for t in getattr(model, "tables", []) or []:
            mode = str(getattr(t, "storage_mode", "") or "").strip().lower()
            if mode:
                modes[dax_text.norm_name(getattr(t, "name", ""))] = mode
        if not modes:
            return False
        code = dax_text.code_tokens(expr)
        referenced = {
            tb for k, tk in enumerate(code) if (tb := ref_table(tk, code[k - 1] if k else None))
        }
        return bool(referenced) and all(modes.get(tb) == "directquery" for tb in referenced)

    def preview(self, model: Any, args: Dict[str, Any]) -> PreviewResult:
        out = PreviewResult()
        scope = set(args.get("scope") or ["measures"])
        if "measures" not in scope:
            return out
        for tbl, m in dax_text.iter_scoped_measures(model, args):
            expr = m.expression or ""
            new_expr = self._rewrite(expr)
            if not new_expr or new_expr.strip() == expr.strip():
                continue
            if self._directquery_only(model, expr):
                # canon rule 4 exception 3 / AP-06: in DirectQuery IFERROR / '/' fold into the
                # SQL and DIVIDE does not - the rewrite would make the measure slower.
                out.skipped.append(
                    f"{dax_text.measure_target(tbl, m)}: directquery_only - DIVIDE does not "
                    "fold to SQL in DirectQuery"
                )
                continue
            target = dax_text.measure_target(tbl, m)
            out.edits.append(Edit(target=target, before=expr, after=new_expr.strip()))
            out.affected_measures.append(target)
        if out.edits:
            out.warnings.append(
                "use_divide is a SEMANTIC change, not a pure refactor: DIVIDE returns BLANK "
                "where '/' returned an error or +/-Infinity/NaN (division by zero / blank)."
            )
        return out

    def apply(self, model: Any, args: Dict[str, Any], preview: PreviewResult) -> Dict[str, Any]:
        return {"applied": [e.target for e in preview.edits]}


register(UseDivide())
