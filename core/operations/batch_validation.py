"""
Batch blast-radius validation — policy-independent runtime guard
for large deletes. Complements the schema-level maxItems cap and the
autonomous gate.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional

CONFIRM_THRESHOLD = 20  # deletes above this require confirm_destructive


def check_blast_radius(
    batch_operation: str,
    items: List[Dict[str, Any]],
    confirm_destructive: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Enforce the blast-radius confirmation threshold on destructive batch ops.

    Args:
        batch_operation: The verb — "create", "update", "delete", etc. Only "delete" is checked.
        items: The batch items. Inspected for size + per-item name/table fields.
        confirm_destructive: When True, bypass the check. From caller args or options dict.

    Returns:
        None  — batch is safe to proceed.
        dict  — blocking error with `error_type="confirmation_required"` and
                `would_affect={total, by_table, first_10_names}`.
    """
    if batch_operation != "delete":
        return None
    if len(items) <= CONFIRM_THRESHOLD:
        return None
    if confirm_destructive:
        return None

    by_table: Counter = Counter()
    names: List[str] = []
    for item in items:
        table = item.get("table_name") or item.get("table") or "(unknown)"
        by_table[table] += 1
        name = (
            item.get("name")
            or item.get("measure_name")
            or item.get("measure")
            or item.get("column_name")
            or item.get("column")
            or item.get("table_name")
            or item.get("table")
            or item.get("from_table")
            or item.get("relationship_name")
            or "(unnamed)"
        )
        if len(names) < 10:
            names.append(name)

    return {
        "success": False,
        "error_type": "confirmation_required",
        "error": (
            f"Deleting {len(items)} items requires confirm_destructive=true "
            f"(threshold: {CONFIRM_THRESHOLD})."
        ),
        "would_affect": {
            "total": len(items),
            "by_table": dict(by_table),
            "first_10_names": names,
        },
        "hint": "Re-send with confirm_destructive=true if this is intended.",
    }
