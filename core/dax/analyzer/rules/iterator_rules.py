"""Iterator-related structural analysis rules.

2026-09-22 review D-09: the Python duplicates of JSON rules were removed —
IF/SWITCH in iterator (canonical CB001), DIVIDE in iterator (CB007), RELATED in
iterator (PERF_RELATED_IN_SUMX), nested iterators (CB003). One rule per pattern.
"""

from typing import List, Optional

from core.dax.tokenizer import Token, TokenType
from core.dax.analyzer.models import AnalysisIssue
from core.dax.analyzer.rule_engine import is_trivial_iterator_args, _declared_names
from .base import PythonRule

# Iterators that can be trivially replaced by a single-column aggregate.
# COUNTX is excluded: COUNTX(T, T[Col]) counts non-blank column values while
# COUNTROWS(T) counts all rows — not equivalent, so there is no safe rewrite.
_REPLACEABLE_ITERATORS = {
    "SUMX": "SUM",
    "AVERAGEX": "AVERAGE",
    "MINX": "MIN",
    "MAXX": "MAX",
}


def is_trivial_iterator(tokens: List[Token], func_index: int) -> Optional[str]:
    """Return the equivalent aggregate (``"SUM"`` …) when the iterator call at
    *func_index* is exactly ``AGG(Table[Col])``, else None.

    Equivalent ONLY when the table argument is the bare model table owning the
    column (review D-05): ``SUMX(FILTER(Sales, …), Sales[Amount])`` keeps its
    filter and is NOT ``SUM(Sales[Amount])``. Rewriters (``core.dax.optimizer``)
    must use this guard before replacing an iterator.
    """
    if func_index < 0 or func_index >= len(tokens):
        return None
    tok = tokens[func_index]
    replacement = _REPLACEABLE_ITERATORS.get(tok.value.upper())
    if replacement is None or tok.type not in (TokenType.FUNCTION, TokenType.IDENTIFIER):
        return None
    from core.dax.tokenizer import DaxLexer

    args = DaxLexer().extract_function_args(tokens, func_index)
    if is_trivial_iterator_args(args, _declared_names(tokens)):
        return replacement
    return None


class UnnecessaryIteratorRule(PythonRule):
    """Detect SUMX/AVERAGEX/MINX/MAXX(Table, Table[Col]) — plain aggregation.

    Informational: ``SUM(col)`` is syntax sugar for ``SUMX(table, col)`` and
    produces the same query plan, so there is no performance gain — the finding
    is about readability only (review D-05).
    """

    rule_id = "PY_UNNECESSARY_ITERATOR"
    category = "maintainability"
    severity = "info"
    title = "Iterator over a single column (same as the plain aggregate)"
    rewrite_strategy = "unnecessary_iterator_to_agg"

    def evaluate(self, tokens, function_db, context=None) -> List[AnalysisIssue]:
        issues: List[AnalysisIssue] = []
        hits = self._find_functions(tokens, set(_REPLACEABLE_ITERATORS.keys()))
        for idx in hits:
            replacement = is_trivial_iterator(tokens, idx)
            if replacement is None:
                continue
            func_name = tokens[idx].value.upper()
            issues.append(
                self._make_issue(
                    f"{func_name}(Table, Table[Col]) over the column's own table is exactly "
                    f"{replacement}(Table[Col]) (same query plan) — the iterator form only "
                    f"adds noise.",
                    f"Replace with {replacement}(Table[Col]) for readability.",
                    rewrite_strategy=self.rewrite_strategy,
                    line=tokens[idx].line,
                )
            )
        return issues

    @staticmethod
    def _is_simple_column_ref(expr_tokens: List[Token]) -> bool:
        """True if *expr_tokens* is exactly one qualified column reference.

        A bare ``[X]`` is intentionally NOT treated as a simple column: it is
        ambiguous between a measure and a column (``SUM([Measure])`` is a runtime
        error). Extended columns (``[@col]``) are not physical columns either.
        """
        meaningful = [t for t in expr_tokens if t.type not in (TokenType.WHITESPACE,)]
        if not meaningful:
            return False
        for t in meaningful:
            if t.type == TokenType.COLUMN_REF and t.value.startswith("[@"):
                return False
            if t.type == TokenType.QUALIFIED_REF and "[@" in t.value:
                return False
        if len(meaningful) == 1 and meaningful[0].type == TokenType.QUALIFIED_REF:
            return True
        if (
            len(meaningful) == 2
            and meaningful[0].type == TokenType.IDENTIFIER
            and meaningful[1].type == TokenType.COLUMN_REF
        ):
            return True
        return False
