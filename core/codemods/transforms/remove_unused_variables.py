"""remove_unused_variables — drop VAR declarations that are never referenced.

2026-09-22 review E-10: the old token scan ignored VAR/RETURN nesting. It
emitted ``RETURN 5`` for ``VAR x = 1 RETURN 5`` (does not parse), cut through a
nested ``CALCULATE(VAR inner = 1 RETURN inner)`` and gave up after 50 passes.
Now every VAR block is parsed with its own paren depth:

- a block is ``VAR n1 = e1 ... VAR nk = ek RETURN r`` at one depth; a
  declaration's value ends at the next ``VAR``/``RETURN`` at that depth;
- a VAR is unused when its name (IDENTIFIER token, case-insensitive; string
  literals and comments never count) does not occur after its declaration
  anywhere up to the end of its block — conservative for shadowed names;
- when every declaration of a block is removed, the ``RETURN`` keyword goes too;
- all unused declarations are removed per pass and passes repeat until nothing
  changes (bounded by the number of VARs, no arbitrary cap);
- a declaration whose value starts with a bare ``VAR`` (nested block without
  parens) makes the measure be skipped — its extent is ambiguous.

2026-10-03 review D-12: only the declaration's own span is removed - from the ``VAR`` keyword to
the end of its last code token (plus a same-line trailing comment / the line break when the
declaration owned its line). Comment lines between declarations (section headers, "do not
simplify" rationale) survive, and no whole-measure blank-line squeeze runs, so blank lines inside
a multi-line string literal are untouched.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from core.codemods import dax_text
from core.codemods.transforms.base import Edit, PreviewResult, Transform, register
from core.dax.tokenizer import TokenType

_Decl = Tuple[int, int, str]  # (VAR token index, end token index exclusive, NAME upper)


class _Ambiguous(Exception):
    pass


class RemoveUnusedVariables(Transform):
    name = "remove_unused_variables"
    description = (
        "Remove VAR declarations whose names are never referenced after their "
        "definition (VAR/RETURN nesting aware; string-literal and comment occurrences "
        "are not uses). Removes the RETURN keyword when a block loses all its VARs."
    )
    args_schema = {
        "type": "object",
        "properties": {
            "scope": {"type": "array", "items": {"type": "string"}, "default": ["measures"]},
            **dax_text.SCOPE_SCHEMA,
        },
    }

    @staticmethod
    def _depths(toks) -> List[int]:
        out, d = [], 0
        for t in toks:
            if t.type in (TokenType.PAREN_CLOSE, TokenType.BRACE_CLOSE):
                d -= 1
            out.append(d)
            if t.type in (TokenType.PAREN_OPEN, TokenType.BRACE_OPEN):
                d += 1
        return out

    @classmethod
    def _blocks(cls, toks) -> List[Tuple[List[_Decl], int, int]]:
        """Every VAR block: (declarations, RETURN token index, block end index)."""
        depths = cls._depths(toks)
        n = len(toks)

        def is_kw(i: int, *words: str) -> bool:
            t = toks[i]
            return t.type == TokenType.KEYWORD and t.value.upper() in words

        blocks = []
        consumed = set()
        for i in range(n):
            if i in consumed or not is_kw(i, "VAR"):
                continue
            d = depths[i]
            decls: List[_Decl] = []
            j = i
            ret_idx = -1
            while j < n and is_kw(j, "VAR") and depths[j] == d:
                if j + 3 >= n or toks[j + 2].value != "=":
                    raise _Ambiguous("malformed VAR declaration")
                if is_kw(j + 3, "VAR"):
                    raise _Ambiguous("VAR value starts with a nested VAR block")
                name = toks[j + 1].value.upper()
                k = j + 3
                while k < n and not (depths[k] == d and is_kw(k, "VAR", "RETURN")):
                    if depths[k] < d:
                        raise _Ambiguous("VAR block without RETURN")
                    k += 1
                if k >= n:
                    raise _Ambiguous("VAR block without RETURN")
                decls.append((j, k, name))
                consumed.add(j)
                if is_kw(k, "RETURN"):
                    ret_idx = k
                    break
                j = k
            if ret_idx < 0 or ret_idx + 1 >= n:
                raise _Ambiguous("VAR block without RETURN expression")
            # The RETURN expression ends at the close paren that leaves depth d
            # or at a depth-d comma (argument separator of the enclosing call).
            e = ret_idx + 1
            while e < n and depths[e] >= d:
                if depths[e] == d and toks[e].type in (
                    TokenType.COMMA,
                    TokenType.PAREN_CLOSE,
                    TokenType.BRACE_CLOSE,
                ):
                    break
                e += 1
            blocks.append((decls, ret_idx, e))
        return blocks

    @staticmethod
    def _line_start(expr: str, pos: int) -> int:
        return max(expr.rfind("\n", 0, pos), expr.rfind("\r", 0, pos)) + 1

    @classmethod
    def _eat_to_eol(cls, expr: str, start: int, end: int) -> Tuple[int, int]:
        """Extend ``[start, end)`` over trailing spaces, a same-line ``//`` / ``--`` comment and,
        when the span began its line, the line break (and the indentation before it)."""
        n = len(expr)
        j = end
        while j < n and expr[j] in " \t":
            j += 1
        ls = cls._line_start(expr, start)
        owns_line = not expr[ls:start].strip()
        if j < n and expr.startswith(("//", "--"), j):
            while j < n and expr[j] not in "\r\n":
                j += 1
        elif j < n and expr[j] not in "\r\n":
            return start, j  # more code follows on the same line: drop only the gap
        if owns_line:
            if expr.startswith("\r\n", j):
                j += 2
            elif j < n and expr[j] in "\r\n":
                j += 1
            start = ls
            # the removed line sat after a blank line and is followed by one: do not leave a
            # double blank behind
            prev_blank = ls > 0 and not expr[expr.rfind("\n", 0, ls - 1) + 1 : ls].strip()
            if prev_blank:
                k = j
                while k < n and expr[k] in " \t":
                    k += 1
                if k < n and expr[k] in "\r\n":
                    j = k + (2 if expr.startswith("\r\n", k) else 1)
        return start, j

    @classmethod
    def _remove_pass(cls, expr: str) -> Optional[str]:
        toks = dax_text.code_tokens(expr)
        if not any(t.type == TokenType.KEYWORD and t.value.upper() == "VAR" for t in toks):
            return None
        edits: List[Tuple[int, int, str]] = []
        for decls, ret_idx, end in cls._blocks(toks):
            unused = []
            for di, (_start, stop, name) in enumerate(decls):
                used = any(
                    toks[k].type == TokenType.IDENTIFIER and toks[k].value.upper() == name
                    for k in range(stop, end)
                )
                if not used:
                    unused.append(di)
            if not unused:
                continue
            for di in unused:
                start, stop, _ = decls[di]
                a, b = cls._eat_to_eol(expr, toks[start].start, toks[stop - 1].end)
                edits.append((a, b, ""))
            if len(unused) == len(decls):
                # The whole block goes: VAR ... RETURN <r>  ->  <r> (comments in between stay)
                ret = toks[ret_idx]
                a, b = cls._eat_to_eol(expr, ret.start, ret.end)
                edits.append((a, b, ""))
        if not edits:
            return None
        # A nested block inside a removed declaration overlaps it; keep the outer.
        edits.sort(key=lambda e: (e[0], -e[1]))
        kept: List[Tuple[int, int, str]] = []
        for e in edits:
            if kept and e[0] < kept[-1][1]:
                continue
            kept.append(e)
        new_expr = dax_text.splice(expr, kept)
        return new_expr.strip()

    @classmethod
    def _remove_one_unused(cls, expr: str) -> Optional[str]:
        """One removal pass (legacy name kept for callers/tests); None = no change."""
        try:
            return cls._remove_pass(expr)
        except _Ambiguous:
            return None

    @classmethod
    def _rewrite(cls, expr: str) -> Optional[str]:
        cur = expr
        changed = False
        try:
            # Each pass removes at least one declaration, so this terminates.
            while True:
                nxt = cls._remove_pass(cur)
                if nxt is None or nxt == cur:
                    break
                cur = nxt
                changed = True
        except _Ambiguous:
            return None
        return cur if changed else None

    def preview(self, model: Any, args: Dict[str, Any]) -> PreviewResult:
        out = PreviewResult()
        scope = set(args.get("scope") or ["measures"])
        if "measures" not in scope:
            return out
        for tbl, m in dax_text.iter_scoped_measures(model, args):
            expr = m.expression or ""
            try:
                new_expr = self._rewrite(expr)
            except Exception:  # noqa: BLE001 - never emit a half-parsed rewrite
                new_expr = None
            if not new_expr or new_expr == expr:
                continue
            target = dax_text.measure_target(tbl, m)
            out.edits.append(Edit(target=target, before=expr, after=new_expr))
            out.affected_measures.append(target)
        return out

    def apply(self, model: Any, args: Dict[str, Any], preview: PreviewResult) -> Dict[str, Any]:
        return {"applied": [e.target for e in preview.edits]}


register(RemoveUnusedVariables())
