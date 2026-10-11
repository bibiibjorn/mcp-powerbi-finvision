"""DAX best-practice analysis — format shim over the unified analyzer.

Feeds 05_DAX_Intelligence (analyze mode) and 09_Debug_Operations:optimize via
``analysis_pipeline.run_best_practices``. 2026-09-22 review D-11/D-24: the
~1,000 lines of regex ``_check_*`` methods below an unconditional ``return``
were unreachable and have been deleted; the unified analyzer is the only
engine and its failure is returned as a structured error.
"""

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def _has_var_and_return_tokens(dax: str) -> Tuple[bool, bool]:
    """Return (has_VAR_keyword, has_RETURN_keyword) using the DAX tokenizer.

    Substring search is never used: identifiers like [VARIANCE], string
    literals like "VAR field" and comments must not count.
    """
    from core.dax.tokenizer import DaxLexer, TokenType

    has_var = False
    has_return = False
    for tok in DaxLexer().tokenize_code(dax or ""):
        if tok.type == TokenType.KEYWORD:
            upper = tok.value.upper()
            if upper == "VAR":
                has_var = True
            elif upper == "RETURN":
                has_return = True
        if has_var and has_return:
            break
    return has_var, has_return


class DaxBestPracticesAnalyzer:
    """DAX best-practice analysis (unified-analyzer shim)."""

    def analyze(
        self,
        dax_expression: str,
        context_analysis: Optional[Dict[str, Any]] = None,
        vertipaq_analysis: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run the unified analyzer and return the legacy best-practices shape.

        Args:
            dax_expression: DAX code to analyze
            context_analysis: accepted for backward compatibility (unused)
            vertipaq_analysis: Optional VertiPaq metrics (tier-2 context)

        Returns:
            ``{success, total_issues, critical_issues, high_issues, medium_issues,
            issues, summary, articles_referenced, overall_score, complexity_level}``
            or a ``success: False`` error envelope.
        """
        try:
            from core.dax.analyzer.unified_analyzer import get_shared_analyzer
            from core.dax.analyzer.models import AnalysisContext

            ctx = AnalysisContext(vertipaq_data=vertipaq_analysis)
            result = get_shared_analyzer().analyze(dax_expression, ctx)
            return result.to_best_practices_format()
        except Exception as e:  # noqa: BLE001 - surfaced as a structured error
            logger.error("DaxUnifiedAnalyzer failed: %s", e, exc_info=True)
            return {
                "success": False,
                "error": f"DaxUnifiedAnalyzer failed: {e}",
                "error_type": "analyzer_failed",
                "issues": [],
                "metrics": {},
                "recommendations": [],
            }
