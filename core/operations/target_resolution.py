"""Resolve an op's target identifiers ONCE (review C-19, C-43).

The destructive gate's evidence and the executor used to read the target from
different argument precedences (evidence preferred ``name``; delete read
``measure_name``/``measure``), so ``{name: "Unused", measure_name: "Critical"}``
proved one object safe and deleted another. Every entry point now resolves the
target here and hands the SAME canonical args to both sides; conflicting
aliases are rejected instead of silently picking one.

Canonical keys:

* table ops ............ ``table_name``
* measure ops .......... ``table_name`` + ``measure_name``
* column ops ........... ``table_name`` + ``column_name``

Batch / changeset items use the same canonical keys per object type.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "AmbiguousTarget",
    "canonicalize_args",
    "canonicalize_changeset",
    "canonicalize_items",
    "resolve_target",
]

# object_type -> (canonical table key, table aliases, canonical name key, name aliases)
_SPECS: Dict[str, Tuple[str, Tuple[str, ...], Optional[str], Tuple[str, ...]]] = {
    "table": ("table_name", ("table_name", "table", "name"), None, ()),
    "measure": (
        "table_name",
        ("table_name", "table", "source_table"),
        "measure_name",
        ("measure_name", "measure", "name"),
    ),
    "column": (
        "table_name",
        ("table_name", "table"),
        "column_name",
        ("column_name", "column", "name"),
    ),
}

# batch object_type (plural) -> singular spec key
_PLURAL = {"tables": "table", "measures": "measure", "columns": "column"}


class AmbiguousTarget(ValueError):
    """Two aliases of the same identifier carry different values."""

    def __init__(self, field: str, values: Dict[str, Any]):
        self.field = field
        self.values = values
        super().__init__(
            f"Ambiguous {field}: aliases disagree ({', '.join(f'{k}={v!r}' for k, v in values.items())}). "
            "Pass exactly one of them."
        )

    def to_error(self) -> Dict[str, Any]:
        return {
            "success": False,
            "error_type": "ambiguous_target",
            "error": str(self),
            "conflicting_args": self.values,
        }


def _pick(src: Dict[str, Any], keys: Sequence[str], field: str) -> Optional[Any]:
    present = {k: src.get(k) for k in keys if src.get(k) not in (None, "")}
    distinct = {str(v) for v in present.values()}
    if len(distinct) > 1:
        raise AmbiguousTarget(field, present)
    return next(iter(present.values()), None)


def resolve_target(object_type: str, args: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """``{"table": ..., "name": ...}`` for table/measure/column ops.

    Raises :class:`AmbiguousTarget` on conflicting aliases. Other object types
    return ``{}`` (they have a single, unaliased identifier set).
    """
    spec = _SPECS.get(object_type)
    if spec is None:
        return {}
    _tkey, t_aliases, nkey, n_aliases = spec
    table = _pick(args, t_aliases, "table name")
    name = _pick(args, n_aliases, f"{object_type} name") if nkey else table
    return {"table": table, "name": name}


def canonicalize_args(object_type: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Copy of *args* with the resolved identifiers under the canonical keys
    and the aliases removed, so no later reader can pick a different one.

    Raises :class:`AmbiguousTarget` on conflicting aliases.
    """
    spec = _SPECS.get(object_type)
    if spec is None:
        return dict(args)
    tkey, t_aliases, nkey, n_aliases = spec
    resolved = resolve_target(object_type, args)
    out = {k: v for k, v in args.items() if k not in set(t_aliases) | set(n_aliases)}
    if object_type == "measure" and args.get("source_table") not in (None, ""):
        # measure/move reads source_table as the source; keep it canonical too.
        out["source_table"] = resolved["table"]
    if resolved.get("table") is not None:
        out[tkey] = resolved["table"]
    if nkey and resolved.get("name") is not None:
        out[nkey] = resolved["name"]
    return out


def canonicalize_items(object_type: str, items: List[Any]) -> List[Any]:
    """Canonicalize every dict item of a batch (``tables``/``measures``/``columns``).

    Non-dict items and other object types pass through unchanged. Raises
    :class:`AmbiguousTarget` (with the item index in ``field``) on a conflict.
    """
    singular = _PLURAL.get(object_type)
    if singular is None:
        return list(items or [])
    out: List[Any] = []
    for idx, item in enumerate(items or []):
        if not isinstance(item, dict):
            out.append(item)
            continue
        try:
            out.append(canonicalize_args(singular, item))
        except AmbiguousTarget as e:
            raise AmbiguousTarget(f"items[{idx}] {e.field}", e.values) from None
    return out


def canonicalize_changeset(doc: Any) -> Any:
    """Canonicalize the tables/columns item lists of a changeset document."""
    if not isinstance(doc, dict):
        return doc
    out: Dict[str, Any] = {}
    for object_type, ops in doc.items():
        if object_type not in _PLURAL or not isinstance(ops, dict):
            out[object_type] = ops
            continue
        new_ops: Dict[str, Any] = {}
        for op_kind, items in ops.items():
            if isinstance(items, list):
                try:
                    new_ops[op_kind] = canonicalize_items(object_type, items)
                except AmbiguousTarget as e:
                    raise AmbiguousTarget(f"{object_type}.{op_kind} {e.field}", e.values) from None
            else:
                new_ops[op_kind] = items
        out[object_type] = new_ops
    return out
