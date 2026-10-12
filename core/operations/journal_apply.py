"""Apply journal plan ops to a TOM model in memory (JR-01) - the only CLR-touching planner half.

:func:`apply_ops` mutates a connected ``Model`` (add / remove / update objects from their TMSL JSON
images) and does NOT save: the caller commits once through ``write_lane.commit`` so the whole
undo / replay / restore is one atomic SaveChanges, and calls ``model.UndoLocalChanges()`` when this
raises.

Updates are applied IN PLACE (property by property, through .NET reflection) for the kinds that
carry data or that other objects point at (model, table, column, measure, partition, calculation
item, relationship, named expression): removing and re-adding a column would drop its data.
Roles, perspectives, cultures, hierarchies, data sources, functions and query groups hold no data
and are REPLACED (remove + add from the full JSON).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

__all__ = ["JournalApplyError", "apply_ops", "deserialize_image"]

_TOP_COLLECTION = {
    "relationship": "Relationships",
    "role": "Roles",
    "perspective": "Perspectives",
    "culture": "Cultures",
    "expression": "Expressions",
    "datasource": "DataSources",
    "function": "Functions",
    "querygroup": "QueryGroups",
}
_TABLE_COLLECTION = {
    "column": "Columns",
    "measure": "Measures",
    "partition": "Partitions",
    "hierarchy": "Hierarchies",
}
_CLASS = {
    "table": "Table",
    "column": "Column",
    "measure": "Measure",
    "partition": "Partition",
    "hierarchy": "Hierarchy",
    "calcitem": "CalculationItem",
    "relationship": "SingleColumnRelationship",
    "role": "ModelRole",
    "perspective": "Perspective",
    "culture": "Culture",
    "expression": "NamedExpression",
    "datasource": "DataSource",
    "function": "Function",
    "querygroup": "QueryGroup",
}
#: Updates of these kinds are done as remove + add from the full image.
_REPLACE_KINDS = frozenset(
    {"role", "perspective", "culture", "hierarchy", "datasource", "function", "querygroup"}
)
#: JSON keys never written property-by-property.
_SKIP_KEYS = frozenset({"type", "changedProperties", "extendedProperties", "refreshPolicy"})
_PARTITION_SOURCE = {
    "m": "MPartitionSource",
    "calculated": "CalculatedPartitionSource",
    "query": "QueryPartitionSource",
    "entity": "EntityPartitionSource",
}


class JournalApplyError(RuntimeError):
    """A plan op could not be applied to the model (nothing has been saved)."""


def _tom():
    import Microsoft.AnalysisServices.Tabular as tabular  # type: ignore

    return tabular


def _sys():
    import System  # type: ignore

    return System


def deserialize_image(kind: str, body: Dict[str, Any]) -> Any:
    """TMSL JSON -> a detached TOM object of *kind* (via ``JsonSerializer``)."""
    import clr  # type: ignore

    tabular = _tom()
    cls = getattr(tabular, _CLASS[kind])
    return tabular.JsonSerializer.DeserializeObject(clr.GetClrType(cls), json.dumps(body))


# ---------------------------------------------------------------------------
# locating objects
# ---------------------------------------------------------------------------
def _collection(model: Any, kind: str, table: Optional[str]) -> Any:
    if kind == "table":
        return model.Tables
    if kind in _TOP_COLLECTION:
        return getattr(model, _TOP_COLLECTION[kind])
    tbl = model.Tables.Find(table)
    if tbl is None:
        raise JournalApplyError(f"table {table!r} not found (needed for {kind})")
    if kind == "calcitem":
        cg = getattr(tbl, "CalculationGroup", None)
        if cg is None:
            raise JournalApplyError(f"table {table!r} is not a calculation group")
        return cg.CalculationItems
    return getattr(tbl, _TABLE_COLLECTION[kind])


def _find(model: Any, kind: str, table: Optional[str], name: str) -> Any:
    return _collection(model, kind, table).Find(name)


# ---------------------------------------------------------------------------
# reflection property writer
# ---------------------------------------------------------------------------
def _flags():
    sys_ = _sys()
    bf = sys_.Reflection.BindingFlags
    return bf.IgnoreCase | bf.Public | bf.Instance


def _default_of(obj: Any, prop: Any) -> Any:
    """The property's value on a freshly constructed object of the same type (its default)."""
    sys_ = _sys()
    try:
        fresh = sys_.Activator.CreateInstance(obj.GetType())
        return prop.GetValue(fresh)
    except Exception:  # noqa: BLE001 - no default constructor: fall back to type defaults
        ptype = prop.PropertyType
        if ptype.IsValueType and sys_.Nullable.GetUnderlyingType(ptype) is None:
            return sys_.Activator.CreateInstance(ptype)
        return None


def _convert(value: Any, ptype: Any) -> Any:
    sys_ = _sys()
    inner = sys_.Nullable.GetUnderlyingType(ptype)
    target = inner if inner is not None else ptype
    if target.IsEnum:
        return sys_.Enum.Parse(target, str(value), True)
    if target == sys_.String:
        return str(value)
    if target == sys_.Boolean:
        return bool(value)
    return sys_.Convert.ChangeType(value, target)


def _apply_annotations(obj: Any, wanted: List[Dict[str, Any]]) -> None:
    coll = getattr(obj, "Annotations", None)
    if coll is None:
        return
    tabular = _tom()
    want = {a.get("name"): a.get("value") for a in wanted or []}
    for existing in [a for a in coll]:
        if existing.Name not in want:
            coll.Remove(existing)
    for name, value in want.items():
        cur = coll.Find(name)
        if cur is None:
            ann = tabular.Annotation()
            ann.Name = name
            ann.Value = value
            coll.Add(ann)
        elif cur.Value != value:
            cur.Value = value


def _new_partition_source(spec: Dict[str, Any]) -> Any:
    cls_name = _PARTITION_SOURCE.get(str(spec.get("type") or "m").lower())
    if cls_name is None:
        raise JournalApplyError(f"unsupported partition source type {spec.get('type')!r}")
    return getattr(_tom(), cls_name)()


def _apply_props(
    obj: Any,
    target: Dict[str, Any],
    current: Dict[str, Any],
    warnings: List[str],
    where: str,
    *,
    skip: frozenset = frozenset(),
    table: Optional[str] = None,
    model: Any = None,
) -> None:
    """Write *target* onto *obj*; keys present in *current* but absent from *target* are reset to
    the type default (the serializer omits default values)."""
    otype = obj.GetType()
    flags = _flags()
    # resets first: clearing a nested object (kpi) after another one (formatStringDefinition) was
    # assigned makes TOM's serializer reject the measure
    keys = [k for k in (current or {}) if k not in target and k not in _SKIP_KEYS and k not in skip]
    keys += [k for k in target if k not in _SKIP_KEYS and k not in skip]
    for key in keys:
        if key == "annotations":
            _apply_annotations(obj, target.get(key) or [])
            continue
        prop = otype.GetProperty(key, flags)
        if prop is None or not prop.CanWrite:
            warnings.append(f"{where}: property {key!r} not settable in place (left as is)")
            continue
        try:
            if key not in target:
                value = _default_of(obj, prop)
            else:
                value = _property_value(
                    obj,
                    prop,
                    key,
                    target[key],
                    (current or {}).get(key),
                    warnings,
                    where,
                    table,
                    model,
                )
                if value is _SKIPPED:
                    continue
            setattr(obj, prop.Name, value)
        except JournalApplyError:
            raise
        except Exception as exc:  # noqa: BLE001 - CLR interop
            raise JournalApplyError(f"{where}: cannot set {key!r}: {exc}") from exc


_SKIPPED = object()


def _property_value(obj, prop, key, raw, cur_raw, warnings, where, table, model):
    if raw is None:
        return _default_of(obj, prop)
    ptype = prop.PropertyType
    if isinstance(raw, dict):
        current = prop.GetValue(obj)
        nested_current = cur_raw if isinstance(cur_raw, dict) else {}
        if key == "source":  # partition source (polymorphic)
            want_type = _PARTITION_SOURCE.get(str(raw.get("type") or "m").lower())
            fresh = current is None or current.GetType().Name != want_type
            nested = _new_partition_source(raw) if fresh else current
            _apply_props(
                nested, raw, nested_current, warnings, f"{where}.{key}", skip=frozenset({"type"})
            )
        else:
            if ptype.IsAbstract:
                warnings.append(f"{where}: property {key!r} is polymorphic (left as is)")
                return _SKIPPED
            fresh = current is None
            nested = _sys().Activator.CreateInstance(ptype) if fresh else current
            _apply_props(nested, raw, nested_current, warnings, f"{where}.{key}")
        # An attached child is edited in place; re-assigning it to its own parent corrupts TOM's
        # object graph (FormatStringDefinitionID errors), so only a NEW object is set.
        return nested if fresh else _SKIPPED
    if isinstance(raw, list):
        warnings.append(f"{where}: list property {key!r} not settable in place (left as is)")
        return _SKIPPED
    if key.lower() == "sortbycolumn":  # a name in JSON, a Column object in TOM
        tbl = model.Tables.Find(table) if (model is not None and table) else None
        col = tbl.Columns.Find(str(raw)) if tbl is not None else None
        if col is None:
            warnings.append(f"{where}: sortByColumn {raw!r} not found (left as is)")
            return _SKIPPED
        return col
    return _convert(raw, ptype)


# ---------------------------------------------------------------------------
# op execution
# ---------------------------------------------------------------------------
def _table_shell_target(body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        k: v
        for k, v in body.items()
        if k not in ("columns", "measures", "partitions", "hierarchies")
    }


def _update_in_place(model: Any, op: Dict[str, Any], warnings: List[str]) -> None:
    kind, table = op["kind"], op.get("table")
    name = op.get("locate_name") or op["name"]  # its name in the live model right now
    obj = model if kind == "model" else _find(model, kind, table, name)
    if obj is None:
        raise JournalApplyError(f"{op['ref']}: object not found in the live model")
    target = dict(op["json"] or {})
    current = dict(op.get("current") or {})
    if kind == "table":
        cg_target = target.pop("calculationGroup", None)
        cg_current = current.pop("calculationGroup", None)
        _apply_props(obj, target, current, warnings, op["ref"], model=model)
        cg = getattr(obj, "CalculationGroup", None)
        if cg is not None and isinstance(cg_target, dict):
            _apply_props(cg, cg_target, cg_current or {}, warnings, op["ref"] + ".calculationGroup")
        return
    if kind == "column":
        want = target.get("type")
        have = current.get("type")
        if want != have:
            raise JournalApplyError(
                f"{op['ref']}: column type changed ({have!r} -> {want!r}); cannot be restored in place"
            )
    _apply_props(obj, target, current, warnings, op["ref"], table=table, model=model)


def _replace(model: Any, op: Dict[str, Any]) -> None:
    coll = _collection(model, op["kind"], op.get("table"))
    existing = coll.Find(op.get("locate_name") or op["name"])
    if existing is not None:
        coll.Remove(existing)
    coll.Add(deserialize_image(op["kind"], op["json"]))


def _relationship_endpoints(body: Dict[str, Any]):
    return tuple(body.get(k) for k in ("fromTable", "fromColumn", "toTable", "toColumn"))


def apply_ops(model: Any, ops: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Apply *ops* (already ordered) to *model* in memory. Returns ``{"applied", "warnings"}``."""
    warnings: List[str] = []
    applied = 0
    for op in ops:
        action, kind = op["action"], op["kind"]
        try:
            if action == "remove":
                coll = _collection(model, kind, op.get("table"))
                obj = coll.Find(op["name"])
                if obj is None:
                    warnings.append(f"{op['ref']}: already absent")
                    continue
                coll.Remove(obj)
            elif action == "add":
                _collection(model, kind, op.get("table")).Add(deserialize_image(kind, op["json"]))
            elif action == "update":
                if kind in _REPLACE_KINDS:
                    _replace(model, op)
                elif kind == "relationship" and _relationship_endpoints(
                    op["json"] or {}
                ) != _relationship_endpoints(op.get("current") or {}):
                    _replace(model, op)
                else:
                    _update_in_place(model, op, warnings)
            else:
                raise JournalApplyError(f"unknown plan action {action!r}")
        except JournalApplyError:
            raise
        except Exception as exc:  # noqa: BLE001 - CLR interop
            raise JournalApplyError(f"{op['ref']}: {action} failed: {exc}") from exc
        applied += 1
    return {"applied": applied, "warnings": warnings}
