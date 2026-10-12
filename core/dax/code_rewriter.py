"""
DAX code rewriter facade.

``DaxCodeRewriter.rewrite_dax`` runs the optimizer rewrite engine
(``core.dax.optimizer``). Review E-21: the ~1,000-line regex "legacy fallback"
that ran when the engine raised was deleted — it was quadratic (355 s on a
28 KB measure) and carried unsafe "high confidence" rewrites
(``SUMX(FILTER(T, c), v)`` -> ``CALCULATE(SUM(v), c)`` replacing instead of
intersecting the filter, ``FILTER(VALUES(c), [M] > x)`` -> ``KEEPFILTERS(...)``,
measure refs hoisted out of iterators). If the engine fails the call now
returns ``success: false``.

``VariableOptimizationScanner`` is advisory only (09_Debug optimize reports).
"""

import logging
from typing import Dict, List, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Transformation:
    """Represents a code transformation"""

    transformation_type: str
    original_code: str
    transformed_code: str
    explanation: str
    estimated_improvement: str
    confidence: str  # "high", "medium", "low"


class DaxCodeRewriter:
    """Rewrite a DAX expression with the optimizer engine (exact rewrites only)."""

    def __init__(self):
        self.transformations: List[Transformation] = []

    def rewrite_dax(self, dax_expression: str) -> Dict[str, Any]:
        try:
            from core.dax.analyzer.unified_analyzer import get_shared_analyzer
            from core.dax.optimizer.rewrite_engine import DaxRewriteEngine
            from core.dax.knowledge import DaxFunctionDatabase

            analyzer = get_shared_analyzer()
            engine = DaxRewriteEngine(DaxFunctionDatabase.get(), analyzer=analyzer)
            analysis = analyzer.analyze(dax_expression)
            rewrites = engine.rewrite(dax_expression, analysis.tokens, analysis.issues)
        except Exception as e:  # noqa: BLE001
            logger.warning("DAX rewrite engine failed: %s", e, exc_info=True)
            return {
                "success": False,
                "error": f"rewrite engine failed: {e}",
                "error_type": "rewrite_failed",
                "original_code": dax_expression,
            }
        final_dax = engine.apply_rewrites(dax_expression, rewrites)
        has_changes = final_dax.strip() != dax_expression.strip()
        applied = [r for r in rewrites if r.validation_passed and not r.suggestion_only]
        return {
            "success": True,
            "has_changes": has_changes,
            "original_code": dax_expression,
            "rewritten_code": final_dax if has_changes else None,
            "transformations": [
                {
                    "type": r.strategy,
                    "original": r.original_fragment,
                    "transformed": r.rewritten_fragment,
                    "explanation": r.explanation,
                    "estimated_improvement": r.estimated_improvement,
                    "confidence": r.confidence,
                    "applied": r in applied,
                    "suggestion_only": r.suggestion_only,
                    "rejection_reason": r.rejection_reason,
                }
                for r in rewrites
            ],
            "transformation_count": len(applied),
            "suggestion_count": len(rewrites) - len(applied),
        }


class VariableOptimizationScanner:
    """
    Scanner for variable optimization opportunities

    Detects:
    - Repeated measure calculations
    - Repeated column references
    - Repeated function calls
    - Opportunities to cache intermediate results
    """

    def __init__(self):
        """Initialize scanner"""
        pass

    def scan_for_optimizations(self, dax: str) -> Dict[str, Any]:
        """
        Scan DAX for variable optimization opportunities (token-based: strings and comments
        never count). 2026-10-03 review D-21: the invented ``estimated_savings_percent`` /
        ``total_potential_savings_percent`` numbers were removed - nothing here is measured.

        Args:
            dax: DAX expression to scan

        Returns:
            Dictionary with optimization opportunities
        """
        try:
            opportunities = []
            opportunities.extend(self._scan_repeated_measures(dax))
            opportunities.extend(self._scan_repeated_expressions(dax))
            opportunities.extend(self._scan_cacheable_functions(dax))

            return {
                "success": True,
                "opportunities_found": len(opportunities),
                "opportunities": opportunities,
                "recommendation": self._generate_recommendation(opportunities),
            }

        except Exception as e:
            logger.error(f"Error scanning for optimizations: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def _scan_repeated_measures(self, dax: str) -> List[Dict[str, Any]]:
        """Scan for repeated measure references.

        A reference inside an iterator / CALCULATE / time-intelligence call is evaluated in
        another context, so caching it in a top-level VAR would change the result (review
        E-07/E-23); a reference that only occurs inside ONE conditional branch gets its VAR
        inside that branch, and references spread over several branches / SWITCH lanes are not
        reported (a top-level VAR defeats short-circuit evaluation, review D-02).
        """
        from core.dax.optimizer.strategies.variable_extraction import _bare_ref_sites

        opportunities = []
        for _name, occ in _bare_ref_sites(dax).items():
            if len(occ) < 2 or not all(o.hoistable for o in occ):
                continue
            in_branch = not any(o.unconditional for o in occ)
            if in_branch and len({o.branch for o in occ}) != 1:
                continue
            measure = dax[occ[0].start + 1 : occ[0].end - 1]
            count = len(occ)
            where = (
                "declare the VAR INSIDE that conditional branch (a top-level VAR would defeat "
                "short-circuit evaluation)"
                if in_branch
                else "a VAR at the top evaluates it once"
            )
            opportunities.append(
                {
                    "type": "repeated_measure",
                    "measure": measure,
                    "occurrences": count,
                    "priority": "high" if count >= 3 else "medium",
                    "suggestion": f"[{measure}] is referenced {count} times in one filter context: {where}",
                    "example_code": (
                        f"IF(<condition>, VAR _v = [{measure}] RETURN _v * ... + _v * ...)"
                        if in_branch
                        else f"VAR _v = [{measure}]\nRETURN _v * ... + _v * ..."
                    ),
                }
            )
        return opportunities

    def _scan_repeated_expressions(self, dax: str) -> List[Dict[str, Any]]:
        """Scan for a repeated ``T[a] * T[b]`` / ``T[a] / T[b]`` column expression."""
        from core.dax.dax_reference_parser import get_lexer
        from core.dax.tokenizer import TokenType

        code = get_lexer().tokenize_code(dax)
        counts: Dict[str, int] = {}
        for k in range(len(code) - 2):
            a, op, b = code[k], code[k + 1], code[k + 2]
            if (
                a.type == TokenType.QUALIFIED_REF
                and op.type == TokenType.OPERATOR
                and op.value in ("*", "/")
                and b.type == TokenType.QUALIFIED_REF
            ):
                key = f"{a.value} {op.value} {b.value}"
                counts[key] = counts.get(key, 0) + 1

        return [
            {
                "type": "repeated_expression",
                "expression": expr,
                "occurrences": count,
                "priority": "medium",
                "suggestion": f"Extract repeated expression '{expr}' into a variable",
                "example_code": f"VAR _result = {expr}\nRETURN _result * ... + _result * ...",
            }
            for expr, count in counts.items()
            if count >= 2
        ]

    def _scan_cacheable_functions(self, dax: str) -> List[Dict[str, Any]]:
        """Report IDENTICAL calls of an expensive function written 2+ times in one filter
        context (token-sequence equality, not a name count)."""
        from core.dax.analyzer.structure import call_frames, matching_close
        from core.dax.dax_reference_parser import get_lexer
        from core.dax.tokenizer import TokenType

        expensive = {
            "CALCULATE",
            "CALCULATETABLE",
            "FILTER",
            "ALL",
            "ALLEXCEPT",
            "SUMMARIZE",
            "SUMMARIZECOLUMNS",
            "CROSSJOIN",
        }
        code = get_lexer().tokenize_code(dax)
        frames = call_frames(code)
        seen: Dict[tuple, int] = {}
        for k, t in enumerate(code):
            if t.type != TokenType.FUNCTION or t.value.upper() not in expensive:
                continue
            if k + 1 >= len(code) or code[k + 1].type != TokenType.PAREN_OPEN:
                continue
            close = matching_close(code, k + 1)
            body = " ".join(x.value.lower() for x in code[k : close + 1])
            ctx = tuple(f.open_idx for f in frames[k])
            seen[(t.value.upper(), body, ctx)] = seen.get((t.value.upper(), body, ctx), 0) + 1

        return [
            {
                "type": "repeated_function_call",
                "function": func,
                "occurrences": count,
                "priority": "high",
                "suggestion": f"The same {func}(...) call is written {count} times: cache it in a VAR",
                "example_code": f"VAR _result = {func}(...)\nRETURN ... _result ...",
            }
            for (func, _body, _ctx), count in seen.items()
            if count >= 2
        ]

    def _generate_recommendation(self, opportunities: List[Dict[str, Any]]) -> str:
        """Generate overall recommendation"""
        if not opportunities:
            return "No significant variable optimization opportunities found. Code looks well-optimized!"

        high_priority = [o for o in opportunities if o.get("priority") == "high"]
        medium_priority = [o for o in opportunities if o.get("priority") == "medium"]

        parts = [f"Found {len(opportunities)} optimization opportunity(ies):"]

        if high_priority:
            parts.append(
                f"  • {len(high_priority)} high-priority optimization(s) - address these first"
            )

        if medium_priority:
            parts.append(f"  • {len(medium_priority)} medium-priority optimization(s)")

        parts.append(
            "\nRecommendation: Use variables (VAR) to cache repeated calculations. "
            "This improves both performance and readability."
        )

        return "\n".join(parts)
