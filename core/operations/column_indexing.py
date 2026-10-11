"""String / full-text indexing behaviour of a column (Sep 2026 model feature).

``Column.StringIndexingBehavior`` (compatibility level 1707) speeds up CONTAINSSTRING / SEARCH
and text slicers; ``Column.FullTextIndexingBehavior`` (level 1708) is required by the new DAX
``TEXTCONTAINS`` / ``TEXTSIMILARITY``. Both are properties of a STRING column and only take
effect on a persisted (Import / Dual) column.

Guard rails, in this order (each refusal changes nothing):

1. the value is one of the documented ones (string: auto|full|explicit|off;
   full-text: full|explicit|off - there is no ``auto``);
2. the column is a String column;
3. the loaded AMO/TOM exposes the property (``feature_unsupported_by_client`` names the
   needed client build - the bundled 19.117 has it);
4. the database compatibility level is high enough. This code NEVER raises the level: a level
   upgrade is irreversible and is a separate, explicit ``model set_properties`` call.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

STRING_INDEXING_VALUES = ("auto", "full", "explicit", "off")
FULL_TEXT_INDEXING_VALUES = ("full", "explicit", "off")
STRING_INDEXING_MIN_CL = 1707
FULL_TEXT_INDEXING_MIN_CL = 1708
#: Oldest AMO/TOM client build known to expose both properties.
MIN_CLIENT_BUILD = "Microsoft.AnalysisServices.Tabular 19.117"


def _refusal(message: str, error_type: str, **extra: Any) -> Dict[str, Any]:
    return {"success": False, "error": message, "error_type": error_type, **extra}


def _database_level(model: Any) -> Optional[int]:
    try:
        db = getattr(model, "Database", None)
        level = getattr(db, "CompatibilityLevel", None)
        return int(level) if level is not None else None
    except (TypeError, ValueError):
        return None


def _enum_member(name: str) -> Any:
    """``IndexingBehavior.<Name>`` from the loaded TOM, or None when the enum is absent."""
    try:
        import Microsoft.AnalysisServices.Tabular as TOM  # type: ignore

        enum_type = getattr(TOM, "IndexingBehavior", None)
        return getattr(enum_type, name, None) if enum_type is not None else None
    except Exception:  # noqa: BLE001
        return None


def apply_indexing_behavior(
    model: Any,
    column: Any,
    *,
    string_indexing_behavior: Optional[str] = None,
    full_text_indexing_behavior: Optional[str] = None,
) -> Tuple[List[str], Optional[Dict[str, Any]]]:
    """Set the requested behaviours on ``column``. Returns ``(updates, error)``.

    Does not save. ``updates`` are ``"string_indexing_behavior=full"`` style tokens.
    """
    requests = []  # (arg name, TOM property, canonical value, allowed, min CL)
    if string_indexing_behavior is not None:
        requests.append(
            (
                "string_indexing_behavior",
                "StringIndexingBehavior",
                string_indexing_behavior,
                STRING_INDEXING_VALUES,
                STRING_INDEXING_MIN_CL,
            )
        )
    if full_text_indexing_behavior is not None:
        requests.append(
            (
                "full_text_indexing_behavior",
                "FullTextIndexingBehavior",
                full_text_indexing_behavior,
                FULL_TEXT_INDEXING_VALUES,
                FULL_TEXT_INDEXING_MIN_CL,
            )
        )
    if not requests:
        return [], None

    resolved = []
    for arg, prop, raw, allowed, min_cl in requests:
        value = str(raw).strip().lower()
        if value not in allowed:
            return [], _refusal(
                f"Invalid {arg} {raw!r}. Valid: {', '.join(allowed)}",
                "invalid_parameters",
                valid_values=list(allowed),
            )
        resolved.append((arg, prop, value, min_cl))

    dtype = str(getattr(column, "DataType", "")).split(".")[-1].lower()
    if dtype != "string":
        return [], _refusal(
            f"Indexing behaviour applies to String columns only; "
            f"'{getattr(column, 'Name', '?')}' is {dtype or 'unknown'}.",
            "invalid_column_type",
        )

    level = _database_level(model)
    updates: List[str] = []
    pending = []
    for arg, prop, value, min_cl in resolved:
        if not hasattr(column, prop):
            return [], _refusal(
                f"The loaded AMO/TOM client does not expose Column.{prop}; it needs "
                f"{MIN_CLIENT_BUILD} or newer (set MCP_PBI_AMO_DIR to a newer build).",
                "feature_unsupported_by_client",
                required_client=MIN_CLIENT_BUILD,
            )
        if level is not None and level < min_cl:
            return [], _refusal(
                f"{arg} needs compatibility level {min_cl}+; this model is at {level}. "
                "Nothing was changed and the level was NOT raised (an upgrade is irreversible).",
                "compatibility_level_too_low",
                compatibility_level=level,
                required_compatibility_level=min_cl,
                suggestion=(
                    f"02_Model_Operations object_type=model operation=set_properties "
                    f"compatibility_level={min_cl} allow_compatibility_upgrade=true "
                    "confirm_destructive=true - then retry."
                ),
            )
        member = _enum_member(value.capitalize())
        if member is None:
            return [], _refusal(
                f"The loaded TOM has no IndexingBehavior.{value.capitalize()}; it needs "
                f"{MIN_CLIENT_BUILD} or newer.",
                "feature_unsupported_by_client",
                required_client=MIN_CLIENT_BUILD,
            )
        pending.append((arg, prop, value, member))

    for arg, prop, value, member in pending:
        setattr(column, prop, member)
        updates.append(f"{arg}={value}")
    return updates, None
