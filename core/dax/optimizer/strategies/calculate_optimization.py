"""CALCULATE optimization strategy — flatten nested CALCULATE, suggest filter rewrites."""

import re
from typing import List, Optional

from core.dax.tokenizer import DaxLexer, Token, TokenType
from core.dax.knowledge import DaxFunctionDatabase
from core.dax.analyzer.models import AnalysisIssue
from core.dax.optimizer.models import RewriteResult
from .base import RewriteStrategy

# Rewrite strategy names this handler covers.
_NESTED_CALCULATE_STRATEGIES = {"flatten_calculate"}
_FILTER_TABLE_STRATEGIES = {"filter_to_column_predicate"}
_DIRECT_FILTER_STRATEGIES = {"calculatetable_filter_to_direct"}


class CalculateOptimizationStrategy(RewriteStrategy):
    """Optimize CALCULATE patterns — flatten nesting and suggest filter rewrites."""

    strategy_name = "calculate_optimization"

    def can_apply(
        self,
        issue: AnalysisIssue,
        tokens: List[Token],
        function_db: DaxFunctionDatabase,
    ) -> bool:
        strategy = issue.rewrite_strategy or ""
        if strategy in _NESTED_CALCULATE_STRATEGIES:
            return True
        if strategy in _FILTER_TABLE_STRATEGIES:
            return True
        if strategy in _DIRECT_FILTER_STRATEGIES:
            return True
        if "NESTED_CALCULATE" in (issue.rule_id or ""):
            return True
        return False

    def apply(
        self,
        dax: str,
        tokens: List[Token],
        issue: AnalysisIssue,
        function_db: DaxFunctionDatabase,
    ) -> Optional[RewriteResult]:
        strategy = issue.rewrite_strategy or ""

        if strategy in _NESTED_CALCULATE_STRATEGIES or "NESTED_CALCULATE" in (issue.rule_id or ""):
            return self._flatten_nested_calculate(dax, tokens, issue)

        if strategy in _FILTER_TABLE_STRATEGIES:
            return self._suggest_filter_rewrite(dax, issue)

        if strategy in _DIRECT_FILTER_STRATEGIES:
            return self._suggest_direct_filter(dax, issue)

        return None

    # ------------------------------------------------------------------
    # CALCULATETABLE(FILTER(<table>, <column predicate>), ...)
    # ------------------------------------------------------------------

    def _suggest_direct_filter(self, dax: str, issue: AnalysisIssue) -> Optional[RewriteResult]:
        """SUGGESTION: ``CALCULATETABLE(FILTER(T, T[c] > 1), f)`` -> ``CALCULATETABLE(T,
        KEEPFILTERS(T[c] > 1), f)``.

        KEEPFILTERS is what makes the boolean form equivalent: a bare Boolean filter REPLACES an
        existing filter on the column while FILTER over the table intersects with it. The
        predicate must read columns of that one table only (the rule's own check), so it is
        written verbatim. Never applied automatically (suggestion only)."""
        from core.dax.analyzer.unified_analyzer import get_shared_analyzer
        from core.dax.analyzer.rule_engine import _declared_names, _meaningful

        engine = get_shared_analyzer()._json_engine
        lexer = DaxLexer(function_names=engine._db.get_function_names())
        code = lexer.tokenize_code(dax)
        declared = _declared_names(code)
        for i, tok in enumerate(code):
            if tok.value.upper() != "CALCULATETABLE" or tok.type not in (
                TokenType.FUNCTION,
                TokenType.KEYWORD,
            ):
                continue
            outer = lexer.extract_function_args(code, i)
            if not outer or not outer[0]:
                continue
            first = _meaningful(outer[0])
            if not first or first[0].value.upper() != "FILTER":
                continue
            fidx = next((k for k, t in enumerate(code) if t is first[0]), None)
            if fidx is None:
                continue
            fargs = lexer.extract_function_args(code, fidx)
            if len(fargs) != 2:
                continue
            table = engine._bare_model_table(fargs[0], declared)
            if table is None or not engine._is_column_only_predicate(fargs[1], table):
                continue
            tbl_text = dax[fargs[0][0].start : fargs[0][-1].end]
            pred_text = dax[fargs[1][0].start : fargs[1][-1].end]
            rest = ", ".join(dax[a[0].start : a[-1].end] for a in outer[1:] if a)
            fragment = f"CALCULATETABLE({tbl_text}, KEEPFILTERS({pred_text})" + (
                f", {rest})" if rest else ")"
            )
            return RewriteResult(
                strategy=self.strategy_name,
                rule_id=issue.rule_id,
                original_fragment=dax[
                    first[0].start : fargs[-1][-1].end + 1
                ],  # the FILTER(...) call
                rewritten_fragment=fragment,
                full_rewritten_dax=dax,
                explanation=(
                    "CALCULATETABLE takes the Boolean predicate directly. Wrap it in KEEPFILTERS "
                    "to keep FILTER's intersect semantics: a bare Boolean filter would replace an "
                    "existing filter on the same column. Suggestion only - verify with "
                    "09_Validate."
                ),
                confidence="medium",
                estimated_improvement="Column predicate pushed to the storage engine",
                validation_passed=True,
                suggestion_only=True,
            )
        return None

    # ------------------------------------------------------------------
    # Nested CALCULATE flattening
    # ------------------------------------------------------------------

    def _flatten_nested_calculate(
        self,
        dax: str,
        tokens: List[Token],
        issue: AnalysisIssue,
    ) -> Optional[RewriteResult]:
        """SUGGESTION ONLY (review E-04).

        ``CALCULATE(CALCULATE(E, f1), f2)`` is NOT ``CALCULATE(E, f1, f2)`` in
        general: an inner filter overrides an outer filter on the same column
        (filters of one CALCULATE intersect), modifiers (ALL / REMOVEFILTERS /
        USERELATIONSHIP / KEEPFILTERS) are applied before the other filters of
        the same CALCULATE, and inner filter arguments are evaluated in the
        outer-modified context. The old auto-rewrite changed results (Red ->
        BLANK, all colours -> Red only) and emitted invalid DAX when the inner
        CALCULATE was not the whole first argument. It is only equivalent when
        the inner CALCULATE IS the whole first argument, neither level uses a
        modifier and the filter columns are disjoint — which is not provable
        statically, so no rewrite is produced.
        """
        if not re.search(r"\bCALCULATE\s*\(\s*CALCULATE\s*\(", dax, re.IGNORECASE):
            return None
        return RewriteResult(
            strategy=self.strategy_name,
            rule_id=issue.rule_id,
            original_fragment=dax.strip(),
            rewritten_fragment="(suggestion only — no rewrite)",
            full_rewritten_dax=dax,
            explanation=(
                "Nested CALCULATE can be merged into one CALCULATE ONLY when the inner "
                "CALCULATE is the entire first argument, no level uses ALL/REMOVEFILTERS/"
                "ALLEXCEPT/USERELATIONSHIP/KEEPFILTERS/CROSSFILTER, and the two levels filter "
                "disjoint columns. Otherwise the inner filters override the outer ones and "
                "merging changes the result. Verify with 09_Validate before changing it."
            ),
            confidence="low",
            estimated_improvement="Usually negligible (the engine merges contexts itself)",
            validation_passed=True,
            suggestion_only=True,
        )

    # ------------------------------------------------------------------
    # Filter-table-to-column suggestion
    # ------------------------------------------------------------------

    def _suggest_filter_rewrite(
        self,
        dax: str,
        issue: AnalysisIssue,
    ) -> Optional[RewriteResult]:
        """Suggest replacing FILTER(Table, condition) with a column predicate.

        This is hard to auto-rewrite safely (the condition may be complex),
        so we provide the suggestion with medium confidence.
        """
        return RewriteResult(
            strategy=self.strategy_name,
            rule_id=issue.rule_id,
            original_fragment=dax.strip(),
            rewritten_fragment="(see explanation)",
            full_rewritten_dax=dax,  # no actual change — suggestion only
            explanation=(
                "FILTER(Table, condition) inside CALCULATE forces full table "
                "materialization. Replace with a direct column predicate "
                "(e.g., Table[Col] = value) when the condition is a simple "
                "comparison on a single column."
            ),
            confidence="medium",
            estimated_improvement="Can be large on big tables (SE vs FE filtering)",
            validation_passed=True,  # no change to validate
            suggestion_only=True,
        )
