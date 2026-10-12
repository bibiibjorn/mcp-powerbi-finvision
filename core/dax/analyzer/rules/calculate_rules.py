"""CALCULATE-related structural analysis rules.

2026-09-22 review D-09: NestedCalculateRule (duplicate of PERF_NESTED_CALCULATE)
and FilterTableNotColumnRule (duplicate of CB004, with an over-claimed
"117x slower" critical) were removed.
"""

from typing import List, Set

from core.dax.tokenizer import Token, TokenType
from core.dax.analyzer.models import AnalysisIssue
from core.dax.analyzer.rule_engine import _declared_names
from core.dax.analyzer.structure import arg_slices, call_frames, ref_table
from .base import PythonRule

_CALCULATE_FUNCS: Set[str] = {"CALCULATE", "CALCULATETABLE"}

_TRIVIA = (TokenType.WHITESPACE, TokenType.NEWLINE, TokenType.COMMENT_LINE, TokenType.COMMENT_BLOCK)


def _sig(tokens: List[Token]) -> List[Token]:
    return [t for t in tokens if t.type not in _TRIVIA]


def _column_key(tokens: List[Token]) -> str:
    """Normalized ``table|column`` when *tokens* is one qualified column ref."""
    from core.dax.dax_names import split_qualified

    sig = _sig(tokens)
    if len(sig) == 1 and sig[0].type == TokenType.QUALIFIED_REF:
        t, c = split_qualified(sig[0].value)
    elif (
        len(sig) == 2
        and sig[0].type == TokenType.IDENTIFIER
        and sig[1].type == TokenType.COLUMN_REF
    ):
        t, c = sig[0].value, sig[1].value[1:-1].replace("]]", "]")
    else:
        return ""
    return f"{' '.join(t.lower().split())}|{' '.join(c.lower().split())}"


class KeepfiltersOpportunityRule(PythonRule):
    """FILTER(VALUES(T[c]), <predicate on T[c]>) — the KEEPFILTERS shorthand.

    ``CALCULATE(x, FILTER(VALUES(T[c]), T[c] = 1))`` is equivalent to
    ``CALCULATE(x, KEEPFILTERS(T[c] = 1))``. ``FILTER(ALL(col), …)`` is NOT —
    it ignores the existing filter on the column (review D-08) — so it is not
    flagged.
    """

    rule_id = "PY_KEEPFILTERS_OPPORTUNITY"
    category = "maintainability"
    severity = "low"
    title = "FILTER(VALUES(col), col predicate) can be KEEPFILTERS(predicate)"

    def evaluate(self, tokens, function_db, context=None) -> List[AnalysisIssue]:
        issues: List[AnalysisIssue] = []
        for idx in self._find_functions(tokens, {"FILTER"}):
            args = self._get_function_args(tokens, idx)
            if len(args) != 2:
                continue
            table_sig = _sig(args[0])
            if not table_sig or table_sig[0].type != TokenType.FUNCTION:
                continue
            if table_sig[0].value.upper() != "VALUES":
                continue
            inner = self._get_function_args(table_sig, 0)
            if len(inner) != 1:
                continue
            key = _column_key(inner[0])
            if not key:
                continue
            # Every column the predicate reads must be that same column.
            pred = _sig(args[1])
            refs = []
            for k, t in enumerate(pred):
                if t.type == TokenType.QUALIFIED_REF:
                    refs.append(_column_key([t]))
                elif t.type == TokenType.COLUMN_REF:
                    if k > 0 and pred[k - 1].type == TokenType.IDENTIFIER:
                        refs.append(_column_key([pred[k - 1], t]))
                    else:
                        refs.append("")  # lone [x] (measure) — not a column predicate
            if not refs or any(r != key for r in refs):
                continue
            issues.append(
                self._make_issue(
                    "FILTER(VALUES(col), predicate on col) keeps the existing filter on "
                    "the column and applies the predicate — exactly what KEEPFILTERS does.",
                    "Use KEEPFILTERS(col predicate) as the CALCULATE filter argument; the "
                    "semantics are identical and the intent is clearer.",
                    line=tokens[idx].line,
                )
            )
        return issues


def _modifier_position(tokens, frames, idx):
    """When the call at *idx* is a whole CALCULATE/CALCULATETABLE filter argument (arg >= 1),
    return that call's frame; else None. ``ALL`` as a FILTER / window / RANKX table argument is a
    table expression, not a filter modifier."""
    stack = frames[idx]
    if not stack:
        return None
    parent = stack[-1]
    if parent.name not in _CALCULATE_FUNCS or parent.arg < 1:
        return None
    slices = arg_slices(tokens, parent.name_idx)
    if parent.arg >= len(slices) or slices[parent.arg][0] != idx:
        return None
    return parent


class AllTableVsColumnRule(PythonRule):
    """ALL(Table) as a CALCULATE filter modifier that nothing re-adds.

    2026-10-03 review D-08: only the modifier use is flagged (``CALCULATE(x, ALL(Table))``);
    ``ALL(Table)`` as a table expression (FILTER / window / RANKX argument) is a different thing,
    and the canon's SAFE form ``CALCULATE(x, ALL(Customer), VALUES(Customer[Continent]))`` re-adds
    the visible values of the same table in a sibling argument, so it is not flagged either."""

    rule_id = "PY_ALL_TABLE_VS_COLUMN"
    category = "performance"
    severity = "medium"
    title = "ALL(Table) removes all filters from entire table"

    def evaluate(self, tokens, function_db, context=None) -> List[AnalysisIssue]:
        issues: List[AnalysisIssue] = []
        declared = None
        frames = None
        for idx in self._find_functions(tokens, {"ALL"}):
            args = self._get_function_args(tokens, idx)
            if len(args) != 1:
                continue  # ALL with multiple args = column list, fine
            meaningful = _sig(args[0])
            if len(meaningful) != 1:
                continue
            t = meaningful[0]
            if t.type == TokenType.IDENTIFIER:
                if declared is None:
                    declared = _declared_names(tokens)
                if t.value.upper() in declared:
                    continue  # ALL(_tableVar) — not a model table
            elif t.type != TokenType.TABLE_REF:
                continue
            if frames is None:
                frames = call_frames(tokens)
            parent = _modifier_position(tokens, frames, idx)
            if parent is None:
                continue
            table = ref_table(t) or " ".join(t.value.lower().split())
            if self._readded(tokens, parent, idx, table):
                continue
            issues.append(
                self._make_issue(
                    f"ALL({t.value}) removes filters from every column "
                    f"in the table. This may remove more filters than intended.",
                    "If you only need to clear specific columns, use "
                    "ALL(Table[Col1], Table[Col2]) to be explicit (or REMOVEFILTERS(Table[Col]) "
                    "as the CALCULATE modifier).",
                    line=tokens[idx].line,
                )
            )
        return issues

    @staticmethod
    def _readded(tokens, parent, all_idx: int, table: str) -> bool:
        """A sibling filter argument references a column of the same table (VALUES / DISTINCT /
        ALLSELECTED / a predicate): the author reset the table on purpose and re-applied a filter.
        """
        for ai, (a, b) in enumerate(arg_slices(tokens, parent.name_idx)):
            if ai == 0 or a <= all_idx < b:
                continue
            for k in range(a, b):
                prev = tokens[k - 1] if k > a else None
                if ref_table(tokens[k], prev) == table:
                    return True
        return False


class AllPreferRemovefiltersRule(PythonRule):
    """ALL(...) used as a CALCULATE filter modifier: REMOVEFILTERS says what it does.

    Info only (canon rule 11: ``ALL`` and ``REMOVEFILTERS`` are identical inside CALCULATE,
    ``REMOVEFILTERS`` reads better; Microsoft's DAX guidance prefers it as a modifier)."""

    rule_id = "PY_ALL_PREFER_REMOVEFILTERS"
    category = "maintainability"
    severity = "info"
    title = "ALL as a CALCULATE modifier - REMOVEFILTERS states the intent"

    def evaluate(self, tokens, function_db, context=None) -> List[AnalysisIssue]:
        issues: List[AnalysisIssue] = []
        frames = None
        for idx in self._find_functions(tokens, {"ALL"}):
            if not self._get_function_args(tokens, idx):
                continue
            if frames is None:
                frames = call_frames(tokens)
            if _modifier_position(tokens, frames, idx) is None:
                continue
            issues.append(
                self._make_issue(
                    "ALL used as a CALCULATE filter argument only removes filters; "
                    "REMOVEFILTERS is identical there and makes the intent explicit "
                    "(ALL stays right as a table expression, e.g. FILTER(ALL(col), ...)).",
                    "Replace ALL(...) with REMOVEFILTERS(...) in CALCULATE filter position.",
                    line=tokens[idx].line,
                )
            )
        return issues
