"""DAX static analysis with health scoring — format shim over the unified analyzer.

2026-09-22 review D-11: this module used to carry its own 10-rule regex engine
(PERF001-CORR002) that silently took over whenever the unified analyzer raised,
with different rule ids and severities. It is now a thin adapter: the unified
analyzer (``core.dax.analyzer``) is the only engine, and a failure there is a
visible ``success: False`` result instead of a silent engine switch.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict

logger = logging.getLogger(__name__)


@dataclass
class DaxIssue:
    """A single static analysis issue (legacy shape, kept for importers).

    NOTE: shares the name `DaxIssue` with the unrelated dataclass in
    ``core.dax.dax_best_practices`` (different fields). Never import both
    in the same module — use `.to_dict()` at the boundary so consumers see
    the unified shape from `analyzer/models.AnalysisIssue`.
    """

    rule_id: str
    category: str
    severity: str
    description: str
    fix_suggestion: str
    line: int = 0
    match_text: str = ""


class DaxRulesEngine:
    """DAX static analysis with health scoring (unified-analyzer shim).

    Usage:
        engine = DaxRulesEngine()
        result = engine.analyze(dax_expression)
    """

    def analyze(self, dax_expression: str) -> Dict[str, Any]:
        """Analyze DAX and return ``{health_score, issues, issue_count, categories}``.

        On an analyzer failure returns ``success: False`` with ``error`` and an
        empty issue list (never a different engine's verdict).
        """
        try:
            from core.dax.analyzer.unified_analyzer import get_shared_analyzer

            result = get_shared_analyzer().analyze(dax_expression)
            out = result.to_rules_engine_format()
            out["success"] = True
            return out
        except Exception as e:  # noqa: BLE001 - surfaced as a structured error
            logger.error("DaxUnifiedAnalyzer failed: %s", e, exc_info=True)
            return {
                "success": False,
                "error": f"DaxUnifiedAnalyzer failed: {e}",
                "error_type": "analyzer_failed",
                "health_score": None,
                "issues": [],
                "issue_count": 0,
                "categories": {},
            }
