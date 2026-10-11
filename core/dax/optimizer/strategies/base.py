"""Base class for rewrite strategies."""

from abc import ABC, abstractmethod
from typing import List, Optional

from core.dax.tokenizer import Token
from core.dax.knowledge import DaxFunctionDatabase
from core.dax.analyzer.models import AnalysisIssue
from core.dax.optimizer.models import RewriteResult


class RewriteStrategy(ABC):
    """Abstract base for DAX rewrite strategies.

    Each strategy handles a set of issue types and produces
    a ``RewriteResult`` with the transformed DAX.
    """

    strategy_name: str

    @abstractmethod
    def can_apply(
        self,
        issue: AnalysisIssue,
        tokens: List[Token],
        function_db: DaxFunctionDatabase,
    ) -> bool:
        """Check if this strategy can fix the given issue."""

    @abstractmethod
    def apply(
        self,
        dax: str,
        tokens: List[Token],
        issue: AnalysisIssue,
        function_db: DaxFunctionDatabase,
    ) -> Optional[RewriteResult]:
        """Apply the rewrite. Return None if it cannot be applied."""

    def _validate_rewrite(self, original: str, rewritten: str) -> bool:
        """Structural gate on the re-lexed rewrite (balanced/nested parens, no
        swallowed tokens, no new malformed operator sequences) — the same gate
        15_DAX_Refactor uses (``core.codemods.dax_text.sanity_check``)."""
        from core.codemods.dax_text import sanity_check

        try:
            return sanity_check(rewritten, original) is None
        except Exception:
            return False
