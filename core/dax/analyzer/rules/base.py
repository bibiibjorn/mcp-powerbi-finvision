"""Base class for Python DAX analysis rules."""

from abc import ABC, abstractmethod
from typing import List, Set

from core.dax.tokenizer import DaxLexer, Token, TokenType
from core.dax.knowledge import DaxFunctionDatabase
from core.dax.analyzer.models import AnalysisIssue


class PythonRule(ABC):
    """Base class for complex structural analysis rules.

    Subclasses define ``rule_id``, ``category``, ``severity``, ``title``
    as class attributes and implement ``evaluate()`` which receives a
    token stream produced by ``DaxLexer.tokenize_code()``.
    """

    rule_id: str
    category: str
    severity: str
    title: str
    requires_tier: int = 1  # 1=static, 2=vertipaq, 3=trace

    @abstractmethod
    def evaluate(
        self,
        tokens: List[Token],
        function_db: DaxFunctionDatabase,
        context=None,
    ) -> List[AnalysisIssue]:
        """Evaluate this rule against tokenized DAX."""

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _find_functions(self, tokens: List[Token], names: Set[str]) -> List[int]:
        """Find indices of FUNCTION tokens matching *names* (upper-case)."""
        return [
            i
            for i, t in enumerate(tokens)
            if t.type == TokenType.FUNCTION and t.value.upper() in names
        ]

    def _get_function_args(self, tokens: List[Token], func_idx: int) -> List[List[Token]]:
        """Extract function arguments using ``DaxLexer.extract_function_args``."""
        lexer = DaxLexer()
        return lexer.extract_function_args(tokens, func_idx)

    def _tokens_contain_function(self, tokens: List[Token], names: Set[str]) -> bool:
        """Return True if any token in *tokens* is a FUNCTION in *names*."""
        upper_names = {n.upper() for n in names}
        return any(t.type == TokenType.FUNCTION and t.value.upper() in upper_names for t in tokens)

    def _make_issue(self, description: str, fix: str, **kwargs) -> AnalysisIssue:
        """Create an ``AnalysisIssue`` with this rule's metadata."""
        return AnalysisIssue(
            rule_id=self.rule_id,
            category=self.category,
            severity=self.severity,
            title=self.title,
            description=description,
            fix_suggestion=fix,
            source="static",
            **kwargs,
        )
