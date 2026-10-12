"""Pattern replacement strategy — COUNTROWS(DISTINCT/VALUES(col)) -> DISTINCTCOUNT(col).

Review E-09: the regex version accepted a TABLE argument and emitted the invalid
``DISTINCTCOUNT(Sales)``, ran over strings and comments, and treated
``COUNTROWS(VALUES(col))`` as equivalent although VALUES includes the blank row
added for referential-integrity violations while DISTINCTCOUNT does not.

Now token-based: only a single qualified column argument qualifies;
``COUNTROWS(DISTINCT(col))`` is rewritten (``DISTINCTCOUNT`` is defined as
exactly that); ``COUNTROWS(VALUES(col))`` is a SUGGESTION stating the RI
condition.
"""

from typing import List, Optional

from core.dax.tokenizer import DaxLexer, Token, TokenType
from core.dax.knowledge import DaxFunctionDatabase
from core.dax.analyzer.models import AnalysisIssue
from core.dax.optimizer.models import RewriteResult
from .base import RewriteStrategy

_HANDLED_STRATEGIES = {
    "countrows_values_to_distinctcount",
    "countrows_distinct_to_distinctcount",
}
_TRIVIA = (TokenType.WHITESPACE, TokenType.NEWLINE, TokenType.COMMENT_LINE, TokenType.COMMENT_BLOCK)


def _single_column(arg: List[Token]) -> bool:
    sig = [t for t in arg if t.type not in _TRIVIA]
    if len(sig) == 1 and sig[0].type == TokenType.QUALIFIED_REF:
        return True
    return (
        len(sig) == 2
        and sig[0].type == TokenType.IDENTIFIER
        and sig[1].type == TokenType.COLUMN_REF
        and sig[1].start == sig[0].end
    )


class PatternReplacementStrategy(RewriteStrategy):
    """COUNTROWS(DISTINCT(col)) -> DISTINCTCOUNT(col); VALUES variant = suggestion."""

    strategy_name = "pattern_replacement"

    def can_apply(
        self,
        issue: AnalysisIssue,
        tokens: List[Token],
        function_db: DaxFunctionDatabase,
    ) -> bool:
        return (issue.rewrite_strategy or "") in _HANDLED_STRATEGIES

    def apply(
        self,
        dax: str,
        tokens: List[Token],
        issue: AnalysisIssue,
        function_db: DaxFunctionDatabase,
    ) -> Optional[RewriteResult]:
        lexer = DaxLexer(function_names=function_db.get_function_names())
        code = [t for t in lexer.tokenize(dax) if t.type not in _TRIVIA]
        pmap = lexer.build_paren_map(code)
        for i, t in enumerate(code):
            if t.value.upper() != "COUNTROWS" or i + 3 >= len(code):
                continue
            if code[i + 1].type != TokenType.PAREN_OPEN:
                continue
            inner = code[i + 2]
            kind = inner.value.upper()
            if kind not in ("DISTINCT", "VALUES") or code[i + 3].type != TokenType.PAREN_OPEN:
                continue
            inner_close = pmap.get(i + 3)
            outer_close = pmap.get(i + 1)
            if inner_close is None or outer_close is None or outer_close != inner_close + 1:
                continue  # COUNTROWS has more than the single VALUES/DISTINCT argument
            args = lexer.extract_function_args(code, i + 2)
            if len(args) != 1 or not _single_column(args[0]):
                continue  # a table argument (DISTINCTCOUNT(Sales) is invalid)
            col = dax[args[0][0].start : args[0][-1].end]
            original = dax[t.start : code[outer_close].end]
            rewritten = f"DISTINCTCOUNT({col})"
            if kind == "VALUES":
                return RewriteResult(
                    strategy=self.strategy_name,
                    rule_id=issue.rule_id,
                    original_fragment=original,
                    rewritten_fragment=rewritten,
                    full_rewritten_dax=dax,
                    explanation=(
                        f"{original} equals {rewritten} only when the relationship to the "
                        "column's table has no referential-integrity violations: VALUES "
                        "counts the blank row added for unmatched keys, DISTINCTCOUNT does "
                        "not. COUNTROWS(DISTINCT(col)) is always exactly DISTINCTCOUNT(col)."
                    ),
                    confidence="medium",
                    estimated_improvement="Engine-optimized distinct count",
                    validation_passed=True,
                    suggestion_only=True,
                )
            full = dax[: t.start] + rewritten + dax[code[outer_close].end :]
            return RewriteResult(
                strategy=self.strategy_name,
                rule_id=issue.rule_id,
                original_fragment=original,
                rewritten_fragment=rewritten,
                full_rewritten_dax=full,
                explanation="DISTINCTCOUNT(col) is defined as COUNTROWS(DISTINCT(col)).",
                confidence="high",
                estimated_improvement="Engine-optimized function replacement",
                validation_passed=self._validate_rewrite(dax, full),
            )
        return None
