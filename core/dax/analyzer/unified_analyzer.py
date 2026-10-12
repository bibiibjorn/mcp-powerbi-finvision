"""Unified DAX Analyzer — single entry point for all static analysis.

The one live DAX rule engine (2026-09-22 review D-11): JSON rules
(``core/dax/knowledge/rules/*.json``) + Python structural rules, evaluated on
the ``DaxLexer`` token stream. ``DaxRulesEngine``, ``CallbackDetector`` and
``DaxBestPracticesAnalyzer`` are format shims over ``get_shared_analyzer()``.

Post-processing, in order:
1. exact duplicates (same rule, location, line) are dropped;
2. ``supersedes`` — when a canonical JSON rule fired, the findings of the rule
   ids it lists are dropped (one finding per anti-pattern, review D-09);
3. suppressions — ``// lint-disable RULE_A, RULE_B`` (or ``-- lint-disable``)
   comments in the expression, ``lint-disable-all``, and
   ``AnalysisContext.suppressed_rules`` remove findings; the ids are reported
   in ``UnifiedAnalysisResult.suppressed`` (review D-27);
4. structural downgrades (IFERROR around XIRR/XNPV, CALCULATETABLE(FILTER())
   on an extension column) are decided from lexer tokens, never from raw
   substrings (review D-26).
"""

import logging
import re
import threading
from typing import List, Optional, Set, Tuple

from core.dax.tokenizer import DaxLexer, Token, TokenType
from core.dax.knowledge import DaxFunctionDatabase
from core.dax.analyzer.models import (
    AnalysisContext,
    AnalysisIssue,
    UnifiedAnalysisResult,
)
from core.dax.analyzer.rule_engine import JsonRuleEngine
from core.dax.analyzer.rules import load_python_rules

logger = logging.getLogger(__name__)

_LINT_DISABLE_RE = re.compile(r"lint-disable(-all)?\b[:\s]*([A-Za-z0-9_,\s]*)", re.IGNORECASE)

_SHARED_LOCK = threading.Lock()
_SHARED: Optional["DaxUnifiedAnalyzer"] = None


def get_shared_analyzer() -> "DaxUnifiedAnalyzer":
    """Process-wide analyzer instance (rules are loaded once).

    The analyzer holds no per-call state, so one instance is safe to share
    across threads. Use this instead of ``DaxUnifiedAnalyzer()`` in hot paths —
    each construction re-reads the four rule JSON files.
    """
    global _SHARED
    if _SHARED is None:
        with _SHARED_LOCK:
            if _SHARED is None:
                _SHARED = DaxUnifiedAnalyzer()
    return _SHARED


def lint_disabled_rules(tokens: List[Token]) -> Tuple[Set[str], bool]:
    """Rule ids disabled by ``lint-disable`` comments, and whether ``-all`` was used."""
    ids: Set[str] = set()
    disable_all = False
    for t in tokens:
        if t.type not in (TokenType.COMMENT_LINE, TokenType.COMMENT_BLOCK):
            continue
        for m in _LINT_DISABLE_RE.finditer(t.value):
            if m.group(1):
                disable_all = True
                continue
            for part in re.split(r"[\s,]+", m.group(2) or ""):
                if part:
                    ids.add(part.upper())
    return ids, disable_all


class DaxUnifiedAnalyzer:
    """Single entry point for all DAX static analysis.

    Combines JSON rules and Python rules, evaluated against
    tokenized DAX using the function knowledge base.
    """

    def __init__(self):
        self._function_db = DaxFunctionDatabase.get()
        self._lexer = DaxLexer(function_names=self._function_db.get_function_names())
        self._json_engine = JsonRuleEngine()
        self._python_rules = load_python_rules()
        self._supersedes = self._json_engine.supersedes_map()

    def rule_ids(self) -> Set[str]:
        """Every rule id this analyzer can emit (JSON + Python)."""
        return self._json_engine.rule_ids() | {r.rule_id for r in self._python_rules}

    def analyze(
        self,
        dax_expression: str,
        context: Optional[AnalysisContext] = None,
    ) -> UnifiedAnalysisResult:
        """Run ALL rules against tokenized DAX at highest available tier."""
        dax_expression = dax_expression or ""
        full_tokens = self._lexer.tokenize(dax_expression)
        tokens = [
            t
            for t in full_tokens
            if t.type
            not in (
                TokenType.COMMENT_LINE,
                TokenType.COMMENT_BLOCK,
                TokenType.WHITESPACE,
                TokenType.NEWLINE,
            )
        ]

        issues: List[AnalysisIssue] = []

        # JSON rules
        try:
            json_issues = self._json_engine.evaluate(tokens, dax_expression, context)
            issues.extend(json_issues)
        except Exception as e:
            logger.warning("JSON rule engine error: %s", e)

        # Python rules
        for rule in self._python_rules:
            try:
                rule_issues = rule.evaluate(tokens, self._function_db, context)
                issues.extend(rule_issues)
            except Exception as e:
                logger.warning("Python rule %s error: %s", rule.rule_id, e)

        # 1. exact duplicates
        seen = set()
        unique_issues = []
        for issue in issues:
            key = (issue.rule_id, issue.location or "", issue.line)
            if key not in seen:
                seen.add(key)
                unique_issues.append(issue)

        # 2. supersedes (one canonical finding per pattern)
        fired = {i.rule_id for i in unique_issues}
        superseded: Set[str] = set()
        for canonical, targets in self._supersedes.items():
            if canonical in fired:
                superseded |= targets
        if superseded:
            unique_issues = [i for i in unique_issues if i.rule_id not in superseded]

        # 3. suppressions
        disabled, disable_all = lint_disabled_rules(full_tokens)
        if context is not None and context.suppressed_rules:
            disabled |= {str(r).upper() for r in context.suppressed_rules}
        suppressed: List[str] = []
        if disable_all or disabled:
            kept = []
            for issue in unique_issues:
                if disable_all or issue.rule_id.upper() in disabled:
                    suppressed.append(issue.rule_id)
                else:
                    kept.append(issue)
            unique_issues = kept

        # 4. structural downgrades
        unique_issues = self._suppress_false_positives(unique_issues, tokens, context)

        tier = 1
        if context and context.trace_data:
            tier = 3
        elif context and context.vertipaq_data:
            tier = 2

        result = UnifiedAnalysisResult.from_issues(unique_issues, tier_used=tier, tokens=tokens)
        result.suppressed = sorted(set(suppressed))
        return result

    # Import-mode advice that inverts on DirectQuery (canon rule 4 exception 3 / AP-06):
    # IFERROR folds into the SQL, DIVIDE does not.
    _DQ_INVERTED_RULES = frozenset(
        {
            "CORR_IFERROR_USAGE",
            "CORR_ISERROR_USAGE",
            "CB002",
            "CORR_DIVISION_WITHOUT_DIVIDE",
            "PY_NEVER_BLANK",
        }
    )

    @staticmethod
    def _referenced_tables(tokens) -> Set[str]:
        """Normalised names of the model tables a token list references."""
        from core.dax.analyzer.structure import ref_table

        out: Set[str] = set()
        for k, t in enumerate(tokens):
            tb = ref_table(t, tokens[k - 1] if k else None)
            if tb:
                out.add(tb)
        return out

    def _suppress_false_positives(self, issues: list, tokens, context=None) -> list:
        """Downgrade issues that are structurally necessary (decided from tokens).

        ``tokens`` may be the code-token list (preferred) or, for backward
        compatibility, the raw DAX string.
        """
        if isinstance(tokens, str):
            tokens = self._lexer.tokenize_code(tokens)
        iferror_calls = [
            i
            for i, t in enumerate(tokens)
            if t.type == TokenType.FUNCTION and t.value.upper() in ("IFERROR", "ISERROR")
        ]
        all_wrap_irr = bool(iferror_calls) and all(
            any(
                t.type == TokenType.FUNCTION and t.value.upper() in ("XIRR", "XNPV")
                for arg in self._lexer.extract_function_args(tokens, i)[:1]
                for t in arg
            )
            for i in iferror_calls
        )
        has_extended_cols = any(
            t.type == TokenType.COLUMN_REF and t.value.startswith("[@") for t in tokens
        )

        schema = getattr(context, "schema", None)
        dq_only = (
            schema is not None
            and bool(getattr(schema, "table_storage_modes", None))
            and schema.is_directquery_only(self._referenced_tables(tokens))
        )

        result = []
        for issue in issues:
            # DirectQuery inverts the IFERROR/DIVIDE advice: IFERROR folds to SQL, DIVIDE does not
            if dq_only and issue.rule_id in self._DQ_INVERTED_RULES:
                issue.severity = "info"
                issue.description += (
                    " [DirectQuery tables only: IFERROR folds into the generated SQL and DIVIDE "
                    "does not, so the Import-mode advice does not apply here.]"
                )
                issue.rewrite_strategy = None
                result.append(issue)
                continue

            # IFERROR + XIRR: no alternative exists (XIRR raises on no solution)
            if (
                issue.rule_id in ("CORR_IFERROR_USAGE", "CORR_ISERROR_USAGE", "CB002")
                and all_wrap_irr
            ):
                issue.severity = "info"
                issue.description += " [Suppressed: IFERROR on XIRR/XNPV is unavoidable]"
                result.append(issue)
                continue

            # CALCULATETABLE(FILTER()) on extended columns: can't use boolean filter
            if issue.rule_id == "PERF_CALCULATETABLE_FILTER" and has_extended_cols:
                issue.severity = "info"
                issue.description += " [Suppressed: FILTER on extended column [@col] required]"
                result.append(issue)
                continue

            result.append(issue)
        return result

    def analyze_batch(
        self,
        measures: List[Tuple[str, str]],
        context: Optional[AnalysisContext] = None,
    ) -> List[UnifiedAnalysisResult]:
        """Analyze multiple measures. Each tuple is (name, dax_expression)."""
        results = []
        for name, dax in measures:
            if context is None:
                ctx = AnalysisContext()
            else:
                # Fresh context per measure, carrying the caller's enrichment
                # data forward without mutating the caller's object (P0-3).
                ctx = AnalysisContext(
                    vertipaq_data=context.vertipaq_data,
                    table_row_counts=context.table_row_counts,
                    model_relationships=context.model_relationships,
                    calculation_groups=context.calculation_groups,
                    trace_data=context.trace_data,
                    table_name=context.table_name,
                    schema=context.schema,
                    suppressed_rules=context.suppressed_rules,
                    object_kind=context.object_kind,
                )
            ctx.measure_name = name
            results.append(self.analyze(dax, ctx))
        return results
