"""Declarative changeset engine for 03_Batch_Operations:apply_changeset.

A changeset is a single grouped-by-object_type document describing creates,
updates, and deletes across multiple object types in one atomic apply:

    {
      "tables":        {"create": [...], "update": [...], "delete": [...]},
      "columns":       {"create": [...], ...},
      "relationships": {...}
    }

This module is PURE (no AMO/TOM): it validates the document and flattens it
into a dependency-safe operation sequence. The handler executes the sequence
against a live model under a single transaction and gates the apply on a
plan_hash computed at preview time.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

# Supported object types and their op kinds.
#
# NOTE: measures are intentionally EXCLUDED. The atomic changeset applies all
# ops against a single shared TOM model in one transaction, which requires
# no-connect ``_apply_*`` primitives. tables/columns/relationships have them;
# measure CRUD goes through the DAXInjector/bulk path that opens its own
# connection per op and cannot join the shared transaction. Callers needing
# measure batches use ``03_Batch_Operations object_type=measures``.
OBJECT_TYPES = ("tables", "columns", "relationships")
OP_KINDS = ("create", "update", "delete")

# Dependency-safe CREATE order: a thing must exist before things that reference
# it. Tables hold columns; relationships reference columns across tables.
_CREATE_ORDER = ["tables", "columns", "relationships"]
# DELETE runs in reverse-dependency order: drop referrers before referents.
_DELETE_ORDER = list(reversed(_CREATE_ORDER))


def validate_changeset(doc: Any) -> List[str]:
    """Return a list of human-readable validation errors ([] when valid)."""
    errors: List[str] = []
    if not isinstance(doc, dict):
        return ["changeset must be an object keyed by object_type"]

    for object_type, ops in doc.items():
        if object_type not in OBJECT_TYPES:
            errors.append(
                f"unknown object_type {object_type!r} " f"(valid: {', '.join(OBJECT_TYPES)})"
            )
            continue
        if not isinstance(ops, dict):
            errors.append(f"{object_type!r} value must be an object of op_kind -> list")
            continue
        for op_kind, items in ops.items():
            if op_kind not in OP_KINDS:
                errors.append(
                    f"unknown op kind {op_kind!r} for {object_type!r} "
                    f"(valid: {', '.join(OP_KINDS)})"
                )
                continue
            if not isinstance(items, list):
                errors.append(f"{object_type}.{op_kind} must be a list")

    return errors


def order_operations(doc: Dict[str, Any]) -> List[Tuple[str, str, Dict[str, Any]]]:
    """Flatten a (validated) changeset into a dependency-safe op sequence.

    Order: all creates (tables -> columns -> relationships), then all updates
    (same forward order), then all deletes (reverse-dependency: relationships
    -> columns -> tables). Returns a list of (object_type, op_kind, item)
    tuples.
    """
    sequence: List[Tuple[str, str, Dict[str, Any]]] = []

    def _emit(op_kind: str, type_order: List[str]) -> None:
        for object_type in type_order:
            ops = doc.get(object_type)
            if not isinstance(ops, dict):
                continue
            for item in ops.get(op_kind, []) or []:
                sequence.append((object_type, op_kind, item))

    _emit("create", _CREATE_ORDER)
    _emit("update", _CREATE_ORDER)
    _emit("delete", _DELETE_ORDER)
    return sequence


def count_operations(doc: Dict[str, Any]) -> Dict[str, int]:
    """Return per-op-kind totals across all object types (for preview summary)."""
    totals = {"create": 0, "update": 0, "delete": 0}
    for object_type in OBJECT_TYPES:
        ops = doc.get(object_type)
        if not isinstance(ops, dict):
            continue
        for op_kind in OP_KINDS:
            items = ops.get(op_kind) or []
            if isinstance(items, list):
                totals[op_kind] += len(items)
    return totals
