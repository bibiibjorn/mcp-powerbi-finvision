"""DAX Rewrite Engine — generates optimized DAX from analysis issues.

Review E-08: every strategy used to run on the ORIGINAL text and
``apply_rewrites`` kept only the LAST result, so earlier rewrites were silently
lost while the summary said "N optimization(s) applied"; suggestion-only
results (``full_rewritten_dax == dax``) could erase a real rewrite; the PERF_*
(JSON) and PY_* twins fired twice for the same pattern. Now the engine applies
ONE rewrite at a time and re-analyses the result until nothing changes
(fixpoint, bounded): every returned applied rewrite's ``full_rewritten_dax`` is
cumulative, suggestions are returned separately flagged ``suggestion_only``,
duplicates are removed by (strategy, fragment), and a rewrite that fails the
structural gate is returned with ``validation_passed=False`` and never chained.
"""

import logging
import re
from typing import Any, List, Optional, Set, Tuple

from core.dax.tokenizer import DaxLexer, Token
from core.dax.knowledge import DaxFunctionDatabase
from core.dax.analyzer.models import AnalysisIssue
from core.dax.optimizer.models import RewriteResult
from core.dax.optimizer.strategies import load_strategies

logger = logging.getLogger(__name__)

MAX_PASSES = 8


class DaxRewriteEngine:
    """Generates optimized DAX rewrites based on analysis issues."""

    def __init__(
        self, function_db: Optional[DaxFunctionDatabase] = None, analyzer: Any = None
    ) -> None:
        self._function_db = function_db or DaxFunctionDatabase.get()
        self._lexer = DaxLexer(function_names=self._function_db.get_function_names())
        self._strategies = load_strategies()
        self._analyzer = analyzer

    def _reanalyze(self, dax: str) -> Tuple[List[Token], List[AnalysisIssue]]:
        analyzer = self._analyzer
        if analyzer is None:
            from core.dax.analyzer.unified_analyzer import get_shared_analyzer

            analyzer = get_shared_analyzer()
        res = analyzer.analyze(dax)
        return list(getattr(res, "tokens", []) or []), list(getattr(res, "issues", []) or [])

    def _first_rewrite(
        self,
        dax: str,
        tokens: List[Token],
        issues: List[AnalysisIssue],
        seen: Set[Tuple[str, str]],
        side: List[RewriteResult],
    ) -> Optional[RewriteResult]:
        for issue in issues:
            if not issue.rewrite_strategy:
                continue
            for strategy in self._strategies:
                if not strategy.can_apply(issue, tokens, self._function_db):
                    continue
                try:
                    result = strategy.apply(dax, tokens, issue, self._function_db)
                except Exception as e:  # noqa: BLE001
                    logger.warning("Strategy %s failed: %s", strategy.strategy_name, e)
                    continue
                if not result:
                    continue
                key = (result.strategy, result.original_fragment)
                if key in seen:
                    break
                seen.add(key)
                if result.suggestion_only or result.full_rewritten_dax == dax:
                    result.suggestion_only = True
                    result.full_rewritten_dax = dax
                    side.append(result)
                    break
                if not result.validation_passed:
                    from core.codemods.dax_text import sanity_check

                    result.rejection_reason = (
                        sanity_check(result.full_rewritten_dax, dax) or "structural check failed"
                    )
                    side.append(result)
                    break
                return result
        return None

    def rewrite(
        self,
        dax: str,
        tokens: List[Token],
        issues: List[AnalysisIssue],
    ) -> List[RewriteResult]:
        """Applied rewrites (cumulative, in order) followed by suggestions /
        rejected results. The first pass uses the caller's *tokens*/*issues*."""
        applied: List[RewriteResult] = []
        side: List[RewriteResult] = []
        seen: Set[Tuple[str, str]] = set()
        current = dax
        cur_tokens, cur_issues = tokens, issues
        for _pass in range(MAX_PASSES):
            result = self._first_rewrite(current, cur_tokens, cur_issues, seen, side)
            if result is None:
                break
            applied.append(result)
            current = result.full_rewritten_dax
            try:
                cur_tokens, cur_issues = self._reanalyze(current)
            except Exception as e:  # noqa: BLE001
                logger.warning("re-analysis after rewrite failed: %s", e)
                break
        return applied + side

    def apply_rewrites(self, dax: str, rewrites: List[RewriteResult]) -> str:
        """Final DAX = the last APPLIED rewrite (applied rewrites are cumulative)."""
        current = dax
        for rw in rewrites:
            if rw.validation_passed and not rw.suggestion_only and rw.full_rewritten_dax:
                current = rw.full_rewritten_dax
        return current

    @staticmethod
    def generate_variable_name(expression: str) -> str:
        """``[Sales Amount]`` -> ``_SalesAmount`` (only ``[A-Za-z0-9_]`` kept)."""
        clean = expression.strip("[] '\"")
        if "." in clean:
            clean = clean.split(".")[-1]
        words = re.split(r"[^A-Za-z0-9]+", clean)
        camel = "".join(w[:1].upper() + w[1:] for w in words if w)
        return f"_{camel}" if camel else "_Var"
