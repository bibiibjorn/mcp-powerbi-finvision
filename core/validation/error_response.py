"""Canonical error envelope (L-13).

Every failed tool result reaching the client has ONE shape::

    {"success": false, "error_type": "<snake_case>", "error": "<message>", ...}

``error_envelope`` builds it; ``normalize_error_envelope`` is applied once by the
dispatcher to every handler result so legacy spellings are folded into it:

* compaction-era aliases ``ok`` / ``err`` / ``err_type`` → ``success`` /
  ``error`` / ``error_type`` (only when the canonical key is absent);
* an error without ``error_type`` gets one inferred from the message
  (``invalid_operation``, ``not_connected``, ``missing_argument`` …) and only
  falls back to ``unspecified`` when nothing matches;
* a ``message``-only failure gets ``error`` = message;
* a multi-line ``error`` loses the appended .NET stack frames (``engine_message``).

Optional guidance keys keep their existing names (``suggestion``,
``retry_with``, ``valid_values`` …) — additive, never renamed.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from core.infrastructure.adomd_utils import engine_message

# Compaction-era alias → canonical key (top level only).
_ENVELOPE_ALIASES = (("ok", "success"), ("err", "error"), ("err_type", "error_type"))

# Ordered (needle, error_type) pairs for inferring a missing error_type from the
# message. First match wins; matching is case-insensitive substring.
_INFER_RULES = (
    ("unknown operation", "invalid_operation"),
    ("invalid operation", "invalid_operation"),
    ("unknown sub_operation", "invalid_operation"),
    ("unsupported operation", "invalid_operation"),
    ("unknown object_type", "invalid_operation"),
    ("not connected", "not_connected"),
    ("no active connection", "not_connected"),
    ("is required", "missing_argument"),
    ("are required", "missing_argument"),
    ("required parameter", "missing_argument"),
    ("missing required", "missing_argument"),
    ("not found", "not_found"),
    ("does not exist", "not_found"),
    ("already exists", "duplicate_name"),
    ("timed out", "timeout"),
    ("timeout", "timeout"),
    ("must be", "invalid_argument"),
    ("invalid", "invalid_argument"),
)


def error_envelope(error_type: str, error: str, **extra: Any) -> Dict[str, Any]:
    """Build the canonical failure envelope. ``extra`` keys are appended."""
    payload: Dict[str, Any] = {"success": False, "error_type": error_type, "error": error}
    for key, value in extra.items():
        if value is not None:
            payload[key] = value
    return payload


def infer_error_type(message: Any) -> str:
    """Best-effort error_type for a message that came without one."""
    text = str(message or "").lower()
    for needle, etype in _INFER_RULES:
        if needle in text:
            return etype
    return "unspecified"


def normalize_error_envelope(result: Any) -> Any:
    """Fold legacy envelope spellings into the canonical shape (in place).

    Only the top level is touched — nested payloads (rows, raw JSON) are never
    rewritten. Non-dict values pass through.
    """
    if not isinstance(result, dict):
        return result
    for alias, canonical in _ENVELOPE_ALIASES:
        if alias in result and canonical not in result:
            result[canonical] = result.pop(alias)
    if result.get("success") is False:
        if not result.get("error"):
            msg = result.get("message")
            if isinstance(msg, str) and msg:
                result["error"] = msg
        err = result.get("error")
        if isinstance(err, str) and "\n" in err:
            # Safety net: engine errors from any manager carry the .NET stack trace.
            result["error"] = engine_message(err)
        if not result.get("error_type"):
            result["error_type"] = infer_error_type(result.get("error"))
    return result


@dataclass
class ErrorResponse:
    """
    Structured error response.

    ``to_dict()`` returns the canonical envelope. ``compact=True`` keeps only
    ``success``/``error``/``error_type`` plus a single ``fix`` hint (the
    compaction-era ``ok``/``err``/``err_type`` spellings are retired).
    """

    success: bool = False
    error: str = ""
    error_type: str = "unexpected_error"
    suggestions: List[str] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    fix: Optional[str] = None  # Short actionable fix for compact mode

    def to_dict(self, compact: bool = False) -> dict:
        """Convert to dictionary. compact=True returns minimal keys (+ fix)."""
        if compact:
            result: Dict[str, Any] = {
                "success": False,
                "error": self.error,
                "error_type": self.error_type or "unexpected_error",
            }
            # Include fix hint if available, otherwise first suggestion
            if self.fix:
                result["fix"] = self.fix
            elif self.suggestions:
                result["fix"] = self.suggestions[0]
            return result
        else:
            # Full response - only include keys with truthy values
            data = asdict(self)
            # Remove fix from full response (it's for compact mode)
            data.pop("fix", None)
            out = {k: v for k, v in data.items() if (k == "success" or v)}
            return out


def quick_error(error: str, fix: str = None, error_type: str = "error") -> dict:
    """Create a compact canonical error dict (success=False, error, error_type, fix)."""
    result: Dict[str, Any] = {
        "success": False,
        "error": error,
        "error_type": (
            error_type if error_type and error_type != "error" else infer_error_type(error)
        ),
    }
    if fix:
        result["fix"] = fix
    return result
