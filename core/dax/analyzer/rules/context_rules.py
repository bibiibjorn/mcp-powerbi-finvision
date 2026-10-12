"""Context transition and variable analysis rules.

2026-09-22 review D-09/D-24: the unregistered duplicates UnusedVarRule
(MAINT_UNUSED_VAR), BlankPropagationRule (CORR_BLANK_PROPAGATION, whose
adjacency logic now lives in the JSON engine) and MeasureRefWithoutVarRule
(MAINT_REPEATED_MEASURE) were deleted.

2026-10-03 review D-06: PY_VAR_DEFEATING_SHORTCIRCUIT only fires when the
expensive VARs are each consumed by ONE non-condition branch (canon rule 3 /
AP-10: VARs shared by the condition and a branch are needed anyway).
"""

from typing import List, Optional, Set, Tuple

from core.dax.tokenizer import Token, TokenType
from core.dax.analyzer.models import AnalysisIssue
from core.dax.analyzer.structure import arg_slices
from .base import PythonRule

# Functions considered expensive enough to defeat short-circuit benefit.
_EXPENSIVE_FUNCS: Set[str] = {
    "CALCULATE",
    "CALCULATETABLE",
    "SUMX",
    "AVERAGEX",
    "COUNTX",
    "MAXX",
    "MINX",
    "PRODUCTX",
    "RANKX",
    "CONCATENATEX",
    "FILTER",
    "ADDCOLUMNS",
    "GENERATE",
}


def _is_kw(tok: Token, word: str) -> bool:
    return tok.type == TokenType.KEYWORD and tok.value.upper() == word


def top_level_vars(tokens: List[Token]) -> Tuple[List[Tuple[str, int, int, int]], Optional[int]]:
    """Top-level ``VAR name = body`` declarations before the first depth-0 RETURN.

    Returns ``([(name, name_idx, body_start, body_end)], return_idx)`` with ``body_end``
    exclusive; ``return_idx`` is None when there is no depth-0 RETURN."""
    depth = 0
    vars_: List[Tuple[str, int, int, int]] = []
    return_idx: Optional[int] = None
    starts: List[Tuple[str, int, int]] = []
    for i, t in enumerate(tokens):
        if t.type in (TokenType.PAREN_OPEN, TokenType.BRACE_OPEN):
            depth += 1
        elif t.type in (TokenType.PAREN_CLOSE, TokenType.BRACE_CLOSE):
            depth -= 1
        elif depth == 0 and _is_kw(t, "RETURN"):
            return_idx = i
            break
        elif depth == 0 and _is_kw(t, "VAR") and i + 2 < len(tokens):
            name_tok = tokens[i + 1]
            eq = tokens[i + 2]
            if name_tok.type == TokenType.IDENTIFIER and eq.value == "=":
                starts.append((name_tok.value, i + 1, i + 3))
    if return_idx is None:
        return [], None
    for k, (name, name_idx, body_start) in enumerate(starts):
        body_end = starts[k + 1][1] - 1 if k + 1 < len(starts) else return_idx
        vars_.append((name, name_idx, body_start, body_end))
    return vars_, return_idx


class VarDefeatingShortCircuitRule(PythonRule):
    """Detect expensive VARs evaluated before IF/SWITCH that each serve ONE branch."""

    rule_id = "PY_VAR_DEFEATING_SHORTCIRCUIT"
    category = "performance"
    severity = "medium"
    title = "Expensive VARs before IF/SWITCH defeat short-circuit"

    def evaluate(self, tokens, function_db, context=None) -> List[AnalysisIssue]:
        issues: List[AnalysisIssue] = []
        vars_, return_idx = top_level_vars(tokens)
        if return_idx is None or not vars_:
            return issues

        cond_idx = return_idx + 1
        if cond_idx >= len(tokens):
            return issues
        cond = tokens[cond_idx]
        cname = cond.value.upper()
        if cond.type not in (TokenType.KEYWORD, TokenType.FUNCTION) or cname not in (
            "IF",
            "SWITCH",
        ):
            return issues
        slices = arg_slices(tokens, cond_idx)
        if len(slices) < 2:
            return issues
        n = len(slices)
        if cname == "IF":
            branch_args = {i for i in range(1, n)}
        else:  # SWITCH(expr, v1, r1, v2, r2, ..., default)
            branch_args = {i for i in range(2, n, 2)}
            if n % 2 == 0:
                branch_args.add(n - 1)  # default result

        confined: List[str] = []
        for name, name_idx, b_start, b_end in vars_:
            if not self._tokens_contain_function(tokens[b_start:b_end], _EXPENSIVE_FUNCS):
                continue
            upper = name.upper()
            arg_hits: Set[int] = set()
            elsewhere = False
            for i, t in enumerate(tokens):
                if t.type != TokenType.IDENTIFIER or t.value.upper() != upper or i == name_idx:
                    continue
                for ai, (a, b) in enumerate(slices):
                    if a <= i < b:
                        arg_hits.add(ai)
                        break
                else:
                    if i > name_idx:
                        elsewhere = True  # used by a later VAR / outside the conditional
            if not elsewhere and len(arg_hits) == 1 and next(iter(arg_hits)) in branch_args:
                confined.append(name)

        if len(confined) >= 2:
            listed = ", ".join(confined)
            issues.append(
                self._make_issue(
                    f"{len(confined)} expensive VARs ({listed}) are each used by only ONE branch "
                    f"of the IF/SWITCH but are evaluated eagerly before it — all branches compute "
                    f"even when only one is taken, defeating short-circuit evaluation (canon "
                    f"rule 3 / AP-10). VARs the condition also needs are correctly hoisted and "
                    f"not counted.",
                    "Move each expensive computation into the branch that uses it (inline the "
                    "expression or define the VAR inside the branch: "
                    '`"A", VAR _v = CALCULATE(...) RETURN _v`). A data-dependent dispatch over '
                    "many expensive lanes still prefetches every lane's SE work — measure it, "
                    "and split per lane if it stays slow.",
                    line=tokens[cond_idx].line,
                )
            )
        return issues
