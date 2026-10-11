"""Iterator optimization strategy — replace unnecessary iterators with aggregates."""

from typing import Dict, List, Optional, Set

from core.dax.tokenizer import DaxLexer, Token, TokenType
from core.dax.knowledge import DaxFunctionDatabase
from core.dax.analyzer.models import AnalysisIssue
from core.dax.optimizer.models import RewriteResult
from .base import RewriteStrategy

# Iterators that can be trivially replaced by aggregates over a single column.
# COUNTX is intentionally EXCLUDED: COUNTX(T, T[Col]) counts non-blank values of
# the column, whereas COUNTROWS(T) counts every row — not equivalent when the
# column has blanks. There is no zero-cost safe rewrite, so we do not offer one.
_REPLACEABLE: Dict[str, str] = {
    "SUMX": "SUM",
    "AVERAGEX": "AVERAGE",
    "MINX": "MIN",
    "MAXX": "MAX",
}

_STRATEGY_NAMES: Set[str] = {"unnecessary_iterator_to_agg", "sumx_to_sum"}


class IteratorOptimizationStrategy(RewriteStrategy):
    """Replace unnecessary iterators with their aggregate equivalents.

    SUMX(Table, Table[Col]) -> SUM(Table[Col])
    AVERAGEX(Table, Table[Col]) -> AVERAGE(Table[Col])
    MINX(Table, Table[Col]) -> MIN(Table[Col])
    MAXX(Table, Table[Col]) -> MAX(Table[Col])
    """

    strategy_name = "iterator_optimization"

    def can_apply(
        self,
        issue: AnalysisIssue,
        tokens: List[Token],
        function_db: DaxFunctionDatabase,
    ) -> bool:
        strategy = issue.rewrite_strategy or ""
        if strategy in _STRATEGY_NAMES:
            return True
        if "UNNECESSARY_ITERATOR" in (issue.rule_id or ""):
            return True
        return False

    def apply(
        self,
        dax: str,
        tokens: List[Token],
        issue: AnalysisIssue,
        function_db: DaxFunctionDatabase,
    ) -> Optional[RewriteResult]:
        """Rewrite the first TRIVIAL iterator only.

        Review E-03: arg 1 used to be ignored, so ``SUMX(FILTER(Sales, …),
        Sales[Amount])`` and ``SUMX(ALL(Sales), Sales[Amount])`` became
        ``SUM(Sales[Amount])`` (filter / ALL silently dropped). The shared guard
        ``is_trivial_iterator`` (core.dax.analyzer.rules.iterator_rules) accepts
        only a bare table token that owns the aggregated column.
        """
        from core.dax.analyzer.rules.iterator_rules import is_trivial_iterator

        lexer = DaxLexer()
        code = [
            t
            for t in tokens
            if t.type
            not in (
                TokenType.WHITESPACE,
                TokenType.NEWLINE,
                TokenType.COMMENT_LINE,
                TokenType.COMMENT_BLOCK,
            )
        ]
        for i, tok in enumerate(code):
            func_upper = tok.value.upper()
            if func_upper not in _REPLACEABLE:
                continue
            replacement_func = is_trivial_iterator(code, i)
            if replacement_func is None:
                continue
            args = lexer.extract_function_args(code, i)
            if len(args) != 2 or not args[1]:
                continue
            col_ref = dax[args[1][0].start : args[1][-1].end]

            original_fragment = self._reconstruct_call(tok.value, args, dax, code, i)
            rewritten_fragment = f"{replacement_func}({col_ref})"
            full_rewritten = self._replace_call_in_dax(dax, tok, code, i, args, rewritten_fragment)
            if full_rewritten is None or full_rewritten == dax:
                continue

            valid = self._validate_rewrite(dax, full_rewritten)
            return RewriteResult(
                strategy=self.strategy_name,
                rule_id=issue.rule_id,
                original_fragment=original_fragment,
                rewritten_fragment=rewritten_fragment,
                full_rewritten_dax=full_rewritten,
                explanation=(
                    f"{func_upper}(T, T[Col]) over the column's own table is exactly "
                    f"{replacement_func}(T[Col]) — same query plan, simpler to read."
                ),
                confidence="high",
                estimated_improvement="Readability only (SUM is syntax sugar for SUMX)",
                validation_passed=valid,
            )

        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_column_ref(expr_tokens: List[Token]) -> Optional[str]:
        """Extract an *unambiguous* column reference safe to wrap in SUM/AVG/etc.

        Returns the qualified column reference string (e.g. ``Sales[Amount]``)
        or None when the expression is not a single qualified column.

        A bare ``[X]`` is deliberately rejected: the tokenizer cannot
        distinguish a measure from a column, and rewriting ``SUMX(t, [Measure])``
        to ``SUM([Measure])`` is a runtime error. Only ``'Table'[Col]`` and
        ``Table[Col]`` forms (which can only be columns) are extracted.
        """
        meaningful = [t for t in expr_tokens if t.type not in (TokenType.WHITESPACE,)]
        if not meaningful:
            return None

        # Single QUALIFIED_REF: 'Sales'[Amount]
        if len(meaningful) == 1 and meaningful[0].type == TokenType.QUALIFIED_REF:
            return meaningful[0].value

        # TABLE_REF/IDENTIFIER + COLUMN_REF: Sales[Amount]
        if (
            len(meaningful) == 2
            and meaningful[0].type in (TokenType.TABLE_REF, TokenType.IDENTIFIER)
            and meaningful[1].type == TokenType.COLUMN_REF
        ):
            return meaningful[0].value + meaningful[1].value

        # Bare COLUMN_REF [X] — ambiguous measure/column. Do NOT rewrite.
        return None

    @staticmethod
    def _reconstruct_arg(arg_tokens: List[Token]) -> str:
        """Reconstruct source text from argument tokens."""
        return "".join(t.value for t in arg_tokens)

    @staticmethod
    def _reconstruct_call(
        func_name: str,
        args: List[List[Token]],
        dax: str,
        tokens: List[Token],
        func_idx: int,
    ) -> str:
        """Reconstruct the original function call text."""
        arg_strs = ["".join(t.value for t in arg) for arg in args]
        return f"{func_name}({', '.join(arg_strs)})"

    def _replace_call_in_dax(
        self,
        dax: str,
        func_token: Token,
        tokens: List[Token],
        func_idx: int,
        args: List[List[Token]],
        replacement: str,
    ) -> Optional[str]:
        """Replace the function call in the original DAX source."""
        # Find the span of the function call: from func_token.start to
        # the closing paren (end of last token + 1 for the close paren).
        call_start = func_token.start

        # Find closing paren by scanning tokens after function.
        lexer = DaxLexer()
        # Re-tokenize with full whitespace to get accurate positions.
        full_tokens = lexer.tokenize(dax)
        # Find the function token in full tokens by start position.
        func_full_idx = None
        for fi, ft in enumerate(full_tokens):
            if ft.start == call_start and ft.value.upper() == func_token.value.upper():
                func_full_idx = fi
                break
        if func_full_idx is None:
            return None

        # Find the opening paren.
        paren_idx = None
        for pi in range(func_full_idx + 1, len(full_tokens)):
            if full_tokens[pi].type == TokenType.PAREN_OPEN:
                paren_idx = pi
                break
        if paren_idx is None:
            return None

        # Find matching close paren.
        pmap = lexer.build_paren_map(full_tokens)
        close_idx = pmap.get(paren_idx)
        if close_idx is None:
            return None

        call_end = full_tokens[close_idx].end

        return dax[:call_start] + replacement + dax[call_end:]
