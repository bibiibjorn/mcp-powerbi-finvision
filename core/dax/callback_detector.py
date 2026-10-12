"""
CallbackDataID pattern detection for DAX measures — unified-analyzer shim.

Detects DAX patterns that typically cause CallbackDataID (row-by-row Formula
Engine evaluation inside a storage-engine scan). The rules live in
``core/dax/knowledge/rules/callback.json`` and run in the unified analyzer:

  CB001 IF/SWITCH in iterator expression      CB007 DIVIDE in iterator (info)
  CB002 IFERROR/ISERROR in iterator           CB008 rounding functions in iterator
  CB003 nested iterators                      CB009 string functions in iterator
  CB004 FILTER over a whole model table       CB010 SELECTEDVALUE/HASONEVALUE in iterator
  CB005 FORMAT (text result / FE callback)    CB011 time intelligence in iterator
  CB006 LASTDATE in a scalar comparison

2026-09-22 review D-11: CB007-CB011 had silently disappeared when this module
started delegating to the unified analyzer (they only existed in the legacy
regex bodies, which ran solely when the analyzer failed). They are now JSON
rules; the ~800 lines of legacy regex rules were deleted.

Usage:
    detector = CallbackDetector()
    detections = detector.detect(dax_expression)
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


@dataclass
class CallbackDetection:
    """A single CallbackDataID detection result."""

    rule_id: str  # "CB001", "CB002", etc.
    severity: str  # "critical", "high", "medium", "low", "info"
    description: str  # What was found
    fix_suggestion: str  # How to fix it
    line: int  # Line in the expression
    match_text: str  # The matched code fragment


class CallbackDetector:
    """Detect DAX patterns that cause CallbackDataID (unified-analyzer shim)."""

    @staticmethod
    def _cb_issues(dax_expression: str) -> List[Any]:
        from core.dax.analyzer.unified_analyzer import get_shared_analyzer

        result = get_shared_analyzer().analyze(dax_expression)
        return [i for i in result.issues if i.rule_id.startswith("CB")]

    def detect(self, dax_expression: str) -> List[CallbackDetection]:
        """Analyze DAX expression for CallbackDataID patterns.

        Raises whatever the unified analyzer raises — there is no fallback
        engine any more (review D-11).

        Returns:
            List of CallbackDetection results sorted by line, then rule id
        """
        detections = [
            CallbackDetection(
                rule_id=issue.rule_id,
                severity=issue.severity,
                description=issue.description,
                fix_suggestion=issue.fix_suggestion,
                line=issue.line,
                match_text=issue.match_text or "",
            )
            for issue in self._cb_issues(dax_expression)
        ]
        detections.sort(key=lambda d: (d.line, d.rule_id))
        return detections

    def detect_dict(self, dax_expression: str) -> Dict[str, Any]:
        """Analyze and return ``{callback_detections, summary}`` for handlers.

        On an analyzer failure returns ``success: False`` with ``error`` and no
        detections.
        """
        try:
            detections = self.detect(dax_expression)
        except Exception as e:  # noqa: BLE001 - surfaced as a structured error
            logger.error("DaxUnifiedAnalyzer failed: %s", e, exc_info=True)
            return {
                "success": False,
                "error": f"DaxUnifiedAnalyzer failed: {e}",
                "error_type": "analyzer_failed",
                "callback_detections": [],
                "summary": {"total": 0, "critical": 0, "high": 0, "medium": 0},
            }

        return {
            "success": True,
            "callback_detections": [
                {
                    "rule_id": d.rule_id,
                    "severity": d.severity,
                    "description": d.description,
                    "fix_suggestion": d.fix_suggestion,
                    "line": d.line,
                    "match_text": d.match_text,
                }
                for d in detections
            ],
            "summary": {
                "total": len(detections),
                "critical": sum(1 for d in detections if d.severity == "critical"),
                "high": sum(1 for d in detections if d.severity == "high"),
                "medium": sum(1 for d in detections if d.severity == "medium"),
            },
        }
