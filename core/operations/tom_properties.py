"""TOM property reflection: ``02_Model_Operations object_type=any`` (F-04).

``list_properties`` / ``get_property`` / ``set_property`` close the long tail of TOM properties no
dedicated op exposes (``IsAvailableInMDX``, ``SummarizeBy``, ``DataCategory``, ``SortByColumn``,
``Annotations[...]``, ``ExtendedProperties[...]`` ...) with introspection, so a caller can discover
them instead of hand-writing a TOM script.

Safety model
------------
* Reading is open: every scalar property of the resolved object is listed (with its type and value).
* Writing is an ALLOW-LIST per object kind (``ALLOWED``). Anything else is refused with a reason
  (``denied_property``): identity / lineage, structural, runtime state, or "use the dedicated op"
  (rename, expression updates, ...). ``Annotations[name]`` and ``ExtendedProperties[name]`` are the
  two pseudo-properties every object that has them accepts.
* Values are coerced to the CLR type: booleans (``true``/``false``/``1``/``0``), integers, enum member
  names (case-insensitive, the valid names are listed on a miss) and column / measure references.
* Compatibility level: the TOM assemblies enforce it when a property is assigned
  (``CompatibilityViolationException``); that is reported as ``compatibility_level_too_low`` with the
  required level, and nothing is saved. ``MIN_CL`` also pre-checks the levels measured against the
  loaded TOM so the caller is told before the model is touched.
* Every write is ONE ``write_lane.commit`` (so it is journaled, paced and undoable); ``dry_run``
  validates and reports ``previous`` -> ``value`` without writing. ``HIGH_IMPACT`` properties (query
  semantics / security) are classified CATASTROPHIC by the handler's gate.
"""

from __future__ import annotations

import datetime as _dt
import logging
import re
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from core.infrastructure.adomd_utils import _clr_type_name, clr_to_py
from core.operations.write_lane import commit as _lane_commit
from core.operations.write_lane import safe_undo

logger = logging.getLogger(__name__)

__all__ = ["TomPropertyManager", "ALLOWED", "HIGH_IMPACT", "is_high_impact", "parse_ref"]

#: Settable properties per object kind (canonical TOM names).
ALLOWED: Dict[str, Tuple[str, ...]] = {
    "model": (
        "Description",
        "DiscourageImplicitMeasures",
        "DiscourageReportMeasures",
        "ValueFilterBehavior",
        "SelectionExpressionBehavior",
        "DefaultMeasure",
    ),
    "table": (
        "Description",
        "IsHidden",
        "DataCategory",
        "ShowAsVariationsOnly",
        "IsPrivate",
        "ExcludeFromModelRefresh",
    ),
    "column": (
        "Description",
        "IsHidden",
        "DisplayFolder",
        "FormatString",
        "DataCategory",
        "SummarizeBy",
        "IsAvailableInMDX",
        "SortByColumn",
        "Alignment",
        "TableDetailPosition",
        "IsDefaultLabel",
        "IsDefaultImage",
    ),
    "measure": ("Description", "IsHidden", "DisplayFolder", "FormatString", "DataCategory"),
    "relationship": (
        "RelyOnReferentialIntegrity",
        "JoinOnDateBehavior",
        "CrossFilteringBehavior",
        "SecurityFilteringBehavior",
    ),
    "hierarchy": ("Description", "IsHidden", "DisplayFolder"),
    "partition": ("Description",),
    "role": ("Description",),
    "named_expression": ("Description",),
    "calculation_group": ("Description",),
    "perspective": (),
    "culture": (),
}

#: Properties that change query semantics or security: the handler gates them CATASTROPHIC.
HIGH_IMPACT = {
    ("model", "ValueFilterBehavior"),
    ("model", "SelectionExpressionBehavior"),
    ("relationship", "CrossFilteringBehavior"),
    ("relationship", "SecurityFilteringBehavior"),
    ("relationship", "JoinOnDateBehavior"),
}

#: Minimum compatibility level, measured against the loaded TOM (it enforces them on assignment).
MIN_CL: Dict[Tuple[str, str], int] = {
    ("model", "ValueFilterBehavior"): 1606,
    ("model", "SelectionExpressionBehavior"): 1609,
}

#: Properties whose value is a reference to another object (resolved by name).
_REF_PROPS = {"SortByColumn": "column", "DefaultMeasure": "measure"}

_DENY_REASONS = {
    "name": "rename it with the type's rename op (the rename cascades every DAX reference)",
    "expression": "use the type's update op (it validates the expression)",
    "lineagetag": "lineage tags are identity metadata",
    "sourcelineagetag": "lineage tags are identity metadata",
    "iskey": "structural: key columns are set with column update is_key",
    "isunique": "structural (engine-computed)",
    "isnullable": "structural: use column update is_nullable",
    "datatype": "structural: changing a data type needs a refresh; use column update",
    "type": "structural",
    "sourcecolumn": "structural: binds the column to its source",
    "modifiedtime": "read-only runtime state",
    "structuremodifiedtime": "read-only runtime state",
    "refreshedtime": "read-only runtime state",
    "state": "read-only runtime state",
    "errormessage": "read-only runtime state",
    "systemmanaged": "read-only engine flag",
    "isactive": "use relationship activate / deactivate",
    "culture": "use model set_properties",
}

_REF_RE = re.compile(r"^(?P<table>.+?)\[(?P<name>.+)\]$")
_PSEUDO_RE = re.compile(r"^(?P<coll>annotations?|extended_?properties?)\[(?P<name>.+)\]$", re.I)
_KIND_ALIASES = {"expression": "named_expression", "calc_group": "calculation_group"}
_KINDS = tuple(ALLOWED)
_MIN_CL_RE = re.compile(r"minimal compat\w* level of (\d+)", re.IGNORECASE)


def _norm(name: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _as_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
    return None


def is_high_impact(kind: str, prop: str) -> bool:
    canon = _canonical(kind, prop)
    return (kind, canon) in HIGH_IMPACT if canon else False


def _canonical(kind: str, prop: str) -> Optional[str]:
    key = _norm(prop)
    for name in ALLOWED.get(kind, ()):
        if _norm(name) == key:
            return name
    return None


def _unquote(table: str) -> str:
    t = table.strip()
    if len(t) >= 2 and t[0] == "'" and t[-1] == "'":
        return t[1:-1].replace("''", "'")
    return t


def parse_ref(ref: Any) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """``(kind, table, name, error)`` from ``Table[Obj]`` / ``kind:Name`` / ``kind:Table[Obj]``."""
    text = str(ref or "").strip()
    if not text:
        return (
            None,
            None,
            None,
            "ref is required (for example Sales[Amount], table:Sales, relationship:<name>, model)",
        )
    kind: Optional[str] = None
    rest = text
    if ":" in text and not text.startswith("'"):
        head, tail = text.split(":", 1)
        h = _KIND_ALIASES.get(head.strip().lower(), head.strip().lower())
        if h in _KINDS:
            kind, rest = h, tail.strip()
    if kind is None and text.lower() == "model":
        return "model", None, None, None
    if kind == "model":
        return "model", None, None, None
    m = _REF_RE.match(rest)
    if m:
        table, name = _unquote(m.group("table")), m.group("name")
        if kind is None:
            return "column_or_measure", table, name, None
        if kind in ("column", "measure", "hierarchy", "partition"):
            return kind, table, name, None
        return None, None, None, f"{kind} refs take a plain name, not Table[Name]: {text!r}"
    if kind is None:
        return "table", None, _unquote(rest), None
    if kind in ("column", "measure", "hierarchy", "partition"):
        return None, None, None, f"{kind} refs need Table[Name]: {text!r}"
    return kind, None, rest, None


class _Prop(NamedTuple):
    name: str
    kind: str  # bool | string | int | float | enum | ref | datetime | other
    can_write: bool
    enum_type: Any = None
    enum_names: Optional[List[str]] = None


def _kind_from_clr(ptype: Any) -> str:
    name = str(getattr(ptype, "Name", ""))
    if getattr(ptype, "IsEnum", False):
        return "enum"
    return {
        "String": "string",
        "Boolean": "bool",
        "Int32": "int",
        "Int64": "int",
        "Double": "float",
        "DateTime": "datetime",
    }.get(name, "other")


def _clr_props(obj: Any) -> Optional[List[_Prop]]:
    get_type = getattr(obj, "GetType", None)
    if get_type is None:
        return None
    try:
        import System  # type: ignore

        out: List[_Prop] = []
        for p in get_type().GetProperties():
            if p.GetIndexParameters().Length:
                continue
            kind = _kind_from_clr(p.PropertyType)
            if p.Name in _REF_PROPS:
                kind = "ref"
            enum_type = enum_names = None
            if kind == "enum":
                enum_type = p.PropertyType
                enum_names = [str(n) for n in System.Enum.GetNames(p.PropertyType)]
            out.append(_Prop(p.Name, kind, bool(p.CanWrite), enum_type, enum_names))
        return out
    except Exception as exc:  # noqa: BLE001 - CLR interop
        logger.debug("CLR reflection failed: %s", exc)
        return None


def _py_props(obj: Any) -> List[_Prop]:
    """Fallback for plain-Python stand-ins (tests): infer kinds from the attribute values.

    A stand-in may carry ``__tom_kinds__ = {"SummarizeBy": ("enum", ["Sum", "Count"])}`` for
    properties whose current value cannot reveal the kind (None, enum).
    """
    hints = getattr(obj, "__tom_kinds__", {}) or {}
    out: List[_Prop] = []
    for name, value in vars(obj).items():
        if name.startswith("_") or name in ("Annotations", "ExtendedProperties"):
            continue
        hint = hints.get(name)
        if isinstance(hint, tuple) and hint and hint[0] == "enum":
            out.append(_Prop(name, "enum", True, None, list(hint[1])))
        elif name in _REF_PROPS:
            out.append(_Prop(name, "ref", True))
        elif isinstance(value, bool):
            out.append(_Prop(name, "bool", True))
        elif isinstance(value, int):
            out.append(_Prop(name, "int", True))
        elif isinstance(value, str) or value is None:
            out.append(_Prop(name, hint or "string", True))
    return out


def reflect(obj: Any) -> List[_Prop]:
    props = _clr_props(obj)
    return props if props is not None else _py_props(obj)


def _display(value: Any) -> Any:
    """JSON-safe rendering of a TOM property value (enum -> member name, object -> its name)."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, _dt.date) or _clr_type_name(value).endswith(
        ("DateTime", "DateTimeOffset")
    ):
        # ISO 8601, never the process culture's rendering ('21/09/2026 14:12:12')
        return clr_to_py(value)
    name = getattr(value, "Name", None)
    if name is not None:
        return str(name)
    return str(value)


def _deny_reason(kind: str, prop: _Prop) -> str:
    return _DENY_REASONS.get(_norm(prop.name), f"not on the settable allow-list for {kind}")


class TomPropertyManager:
    """``object_type=any`` operations. ``_open`` is the seam tests replace."""

    def __init__(self):
        self._connection_state = None

    @property
    def connection_state(self):
        if self._connection_state is None:
            from core.infrastructure.connection_state import connection_state

            self._connection_state = connection_state
        return self._connection_state

    # ---- plumbing ------------------------------------------------------------
    def _open(self):
        from core.operations.amo_helpers import get_server_db_model

        return get_server_db_model(self.connection_state)

    @staticmethod
    def _close(server: Any) -> None:
        try:
            if server is not None:
                server.Disconnect()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _find(coll: Any, name: str) -> Any:
        finder = getattr(coll, "Find", None)
        if finder is not None:
            return finder(name)
        return next((o for o in coll if getattr(o, "Name", None) == name), None)

    def resolve(self, model: Any, ref: Any) -> Tuple[Any, Optional[str], Optional[Dict[str, Any]]]:
        """``(object, kind, error)`` for ``ref``."""
        kind, table_name, name, err = parse_ref(ref)
        if err:
            return None, None, _fail("invalid_parameters", err)
        try:
            if kind == "model":
                return model, "model", None
            if kind == "table":
                obj = self._find(model.Tables, name)
                return self._found(obj, "table", ref)
            if kind in ("column_or_measure", "column", "measure", "hierarchy", "partition"):
                table = self._find(model.Tables, table_name)
                if table is None:
                    return (
                        None,
                        None,
                        _fail("not_found", f"table {table_name!r} not found", ref=str(ref)),
                    )
                lookups = {
                    "column": [("column", "Columns")],
                    "measure": [("measure", "Measures")],
                    "hierarchy": [("hierarchy", "Hierarchies")],
                    "partition": [("partition", "Partitions")],
                    "column_or_measure": [("column", "Columns"), ("measure", "Measures")],
                }[kind]
                for k, attr in lookups:
                    obj = self._find(getattr(table, attr), name)
                    if obj is not None:
                        return obj, k, None
                return (
                    None,
                    None,
                    _fail("not_found", f"{kind.replace('_', ' ')} {ref!r} not found", ref=str(ref)),
                )
            colls = {
                "relationship": "Relationships",
                "role": "Roles",
                "perspective": "Perspectives",
                "culture": "Cultures",
                "named_expression": "Expressions",
            }
            if kind in colls:
                return self._found(self._find(getattr(model, colls[kind]), name), kind, ref)
            if kind == "calculation_group":
                table = self._find(model.Tables, name)
                cg = getattr(table, "CalculationGroup", None) if table is not None else None
                return self._found(cg, "calculation_group", ref)
        except AttributeError as exc:
            return (
                None,
                None,
                _fail(
                    "feature_unsupported_by_client",
                    f"the loaded TOM does not expose what {ref!r} needs: {exc}",
                ),
            )
        return None, None, _fail("invalid_parameters", f"unsupported ref {ref!r}")

    @staticmethod
    def _found(obj: Any, kind: str, ref: Any):
        if obj is None:
            return None, None, _fail("not_found", f"{kind} {ref!r} not found", ref=str(ref))
        return obj, kind, None

    @staticmethod
    def _resolve_ref_value(
        model: Any, obj: Any, prop: str, value: Any
    ) -> Tuple[Any, Optional[Dict[str, Any]]]:
        """Resolve a column / measure reference by name (None / "" clears it)."""
        if value is None or str(value).strip() == "":
            return None, None
        text = str(value).strip()
        want = _REF_PROPS[prop]
        if want == "column":
            table = getattr(obj, "Table", None)
            m = _REF_RE.match(text)
            if m:  # Table[Col] form: must be the same table
                if _unquote(m.group("table")) != getattr(table, "Name", None):
                    return None, _fail(
                        "invalid_parameters",
                        f"{prop} must be a column of the same table, got {value!r}",
                    )
                text = m.group("name")
            col = TomPropertyManager._find(table.Columns, text) if table is not None else None
            if col is None:
                return None, _fail("not_found", f"column {text!r} not found on the object's table")
            return col, None
        m = _REF_RE.match(text)
        if not m:
            return None, _fail("invalid_parameters", f"{prop} needs Table[Measure], got {value!r}")
        tbl = TomPropertyManager._find(model.Tables, _unquote(m.group("table")))
        meas = TomPropertyManager._find(tbl.Measures, m.group("name")) if tbl is not None else None
        if meas is None:
            return None, _fail("not_found", f"measure {value!r} not found")
        return meas, None

    @staticmethod
    def _coerce(prop: _Prop, value: Any) -> Tuple[Any, Optional[Dict[str, Any]]]:
        if prop.kind == "bool":
            flag = _as_bool(value)
            if flag is None:
                return None, _fail(
                    "invalid_parameters", f"{prop.name} needs true/false, got {value!r}"
                )
            return flag, None
        if prop.kind == "int":
            try:
                if isinstance(value, bool):
                    raise ValueError
                return int(value), None
            except (TypeError, ValueError):
                return None, _fail(
                    "invalid_parameters", f"{prop.name} needs an integer, got {value!r}"
                )
        if prop.kind == "float":
            try:
                return float(value), None
            except (TypeError, ValueError):
                return None, _fail(
                    "invalid_parameters", f"{prop.name} needs a number, got {value!r}"
                )
        if prop.kind == "enum":
            names = prop.enum_names or []
            hit = next((n for n in names if _norm(n) == _norm(value)), None)
            if hit is None:
                return None, _fail(
                    "invalid_parameters",
                    f"{prop.name} must be one of {names}, got {value!r}",
                    valid_values=names,
                )
            if prop.enum_type is not None:
                import System  # type: ignore

                return System.Enum.Parse(prop.enum_type, hit, True), None
            return hit, None
        if prop.kind == "string":
            return ("" if value is None else str(value)), None
        return None, _fail(
            "invalid_parameters", f"{prop.name} ({prop.kind}) cannot be set through set_property"
        )

    # ---- ops -----------------------------------------------------------------
    def list_properties(self, ref: Any, include_values: bool = True) -> Dict[str, Any]:
        server, _db, model, err = self._open()
        if model is None:
            return _fail("connection_error", err or "Not connected")
        try:
            obj, kind, rerr = self.resolve(model, ref)
            if rerr:
                return rerr
            allowed = {_norm(n) for n in ALLOWED.get(kind, ())}
            props = []
            for p in sorted(reflect(obj), key=lambda x: x.name):
                if p.kind in ("other",):
                    continue
                settable = p.can_write and _norm(p.name) in allowed
                row: Dict[str, Any] = {
                    "name": p.name,
                    "type": p.kind if p.kind != "enum" else "enum",
                    "settable": bool(settable),
                }
                if p.kind == "enum" and p.enum_names:
                    row["values"] = p.enum_names
                if include_values:
                    try:
                        row["value"] = _display(getattr(obj, p.name, None))
                    except (
                        Exception
                    ) as exc:  # noqa: BLE001 - some getters throw on a detached object
                        row["value_error"] = str(exc)[:80]
                if not settable:
                    row["reason"] = "read-only" if not p.can_write else _deny_reason(kind, p)
                else:
                    cl = MIN_CL.get((kind, p.name))
                    if cl:
                        row["min_compatibility_level"] = cl
                    if (kind, p.name) in HIGH_IMPACT:
                        row["high_impact"] = True
                props.append(row)
            out: Dict[str, Any] = {
                "success": True,
                "ref": str(ref),
                "object_kind": kind,
                "properties": props,
                "property_count": len(props),
                "pseudo_properties": [
                    "Annotations[<name>]" if hasattr(obj, "Annotations") else None,
                    "ExtendedProperties[<name>]" if hasattr(obj, "ExtendedProperties") else None,
                ],
            }
            out["pseudo_properties"] = [x for x in out["pseudo_properties"] if x]
            if hasattr(obj, "Annotations"):
                out["annotations"] = [
                    {"name": str(a.Name), "value": _display(getattr(a, "Value", None))}
                    for a in obj.Annotations
                ]
            if hasattr(obj, "ExtendedProperties"):
                out["extended_properties"] = [
                    {"name": str(e.Name), "value": _display(getattr(e, "Value", None))}
                    for e in obj.ExtendedProperties
                ]
            return out
        finally:
            self._close(server)

    def get_property(self, ref: Any, property_name: Any) -> Dict[str, Any]:
        if not property_name:
            return _fail("invalid_parameters", "property_name is required")
        server, _db, model, err = self._open()
        if model is None:
            return _fail("connection_error", err or "Not connected")
        try:
            obj, kind, rerr = self.resolve(model, ref)
            if rerr:
                return rerr
            pm = _PSEUDO_RE.match(str(property_name).strip())
            if pm:
                coll_name = (
                    "Annotations"
                    if pm.group("coll").lower().startswith("annot")
                    else "ExtendedProperties"
                )
                coll = getattr(obj, coll_name, None)
                if coll is None:
                    return _fail("not_found", f"{kind} has no {coll_name}")
                item = self._find(coll, pm.group("name"))
                if item is None:
                    return _fail("not_found", f"{coll_name}[{pm.group('name')}] not found on {ref}")
                return {
                    "success": True,
                    "ref": str(ref),
                    "property": f"{coll_name}[{pm.group('name')}]",
                    "value": _display(getattr(item, "Value", None)),
                }
            match = next((p for p in reflect(obj) if _norm(p.name) == _norm(property_name)), None)
            if match is None:
                return _fail(
                    "not_found",
                    f"{kind} has no readable property {property_name!r} (use operation=list_properties)",
                )
            return {
                "success": True,
                "ref": str(ref),
                "object_kind": kind,
                "property": match.name,
                "type": match.kind,
                "value": _display(getattr(obj, match.name, None)),
                "settable": bool(match.can_write and _canonical(kind, match.name)),
            }
        finally:
            self._close(server)

    def set_property(
        self, ref: Any, property_name: Any, value: Any, dry_run: bool = False
    ) -> Dict[str, Any]:
        if not property_name:
            return _fail("invalid_parameters", "property_name is required")
        server, db, model, err = self._open()
        if model is None:
            return _fail("connection_error", err or "Not connected")
        try:
            obj, kind, rerr = self.resolve(model, ref)
            if rerr:
                return rerr
            pm = _PSEUDO_RE.match(str(property_name).strip())
            if pm:
                return self._set_pseudo(model, obj, kind, ref, pm, value, dry_run)
            canon = _canonical(kind, property_name)
            props = {p.name: p for p in reflect(obj)}
            if canon is None:
                known = props.get(next((n for n in props if _norm(n) == _norm(property_name)), ""))
                return _fail(
                    "denied_property",
                    f"{property_name!r} is not settable on a {kind}: "
                    + (
                        _deny_reason(kind, known)
                        if known
                        else "no such property (see list_properties)"
                    ),
                    object_kind=kind,
                    settable_properties=list(ALLOWED.get(kind, ())),
                )
            prop = props.get(canon)
            if prop is None or not prop.can_write:
                return _fail(
                    "feature_unsupported_by_client",
                    f"the loaded TOM does not expose a writable {kind}.{canon}",
                    object_kind=kind,
                )
            level = getattr(db, "CompatibilityLevel", None)
            minimum = MIN_CL.get((kind, canon))
            if minimum and level is not None and int(level) < minimum:
                return _fail(
                    "compatibility_level_too_low",
                    f"{kind}.{canon} needs compatibility level {minimum}+; this model is at {int(level)}.",
                    required_compatibility_level=minimum,
                    compatibility_level=int(level),
                )
            if canon in _REF_PROPS:
                new, cerr = self._resolve_ref_value(model, obj, canon, value)
            else:
                new, cerr = self._coerce(prop, value)
            if cerr:
                return cerr
            previous = _display(getattr(obj, canon, None))
            shown = _display(new)
            base = {
                "ref": str(ref),
                "object_kind": kind,
                "property": canon,
                "previous": previous,
                "value": shown,
                "high_impact": (kind, canon) in HIGH_IMPACT or None,
            }
            base = {k: v for k, v in base.items() if v is not None or k in ("previous", "value")}
            if previous == shown:
                return {"success": True, "action": "noop", "message": "already set", **base}
            if dry_run:
                return {"success": True, "dry_run": True, "action": "would_set", **base}
            return self._write(model, obj, canon, new, base, label=f"{kind}/set_property")
        finally:
            self._close(server)

    # ---- writes --------------------------------------------------------------
    def _write(self, model: Any, obj: Any, canon: str, new: Any, base: Dict[str, Any], label: str):
        try:
            setattr(obj, canon, new)
            info = _lane_commit(model, label=label)
        except Exception as exc:  # noqa: BLE001 - CLR interop
            try:
                safe_undo(model)
            except Exception:  # noqa: BLE001
                pass
            return _tom_error(exc)
        return {"success": True, "action": "updated", **base, **(info or {})}

    def _set_pseudo(self, model, obj, kind, ref, pm, value, dry_run):
        is_annotation = pm.group("coll").lower().startswith("annot")
        coll_name = "Annotations" if is_annotation else "ExtendedProperties"
        pname = pm.group("name")
        coll = getattr(obj, coll_name, None)
        if coll is None:
            return _fail("not_found", f"{kind} has no {coll_name}")
        text = "" if value is None else str(value)
        existing = self._find(coll, pname)
        previous = _display(getattr(existing, "Value", None)) if existing is not None else None
        base = {
            "ref": str(ref),
            "object_kind": kind,
            "property": f"{coll_name}[{pname}]",
            "previous": previous,
            "value": text,
        }
        if existing is not None and previous == text:
            return {"success": True, "action": "noop", "message": "already set", **base}
        if dry_run:
            return {"success": True, "dry_run": True, "action": "would_set", **base}
        try:
            if existing is not None:
                existing.Value = text
            else:
                import Microsoft.AnalysisServices.Tabular as TOM  # type: ignore

                item = TOM.Annotation() if is_annotation else TOM.StringExtendedProperty()
                item.Name = pname
                item.Value = text
                coll.Add(item)
            info = _lane_commit(model, label=f"{kind}/set_property")
        except Exception as exc:  # noqa: BLE001 - CLR interop
            try:
                safe_undo(model)
            except Exception:  # noqa: BLE001
                pass
            return _tom_error(exc)
        return {"success": True, "action": "updated", **base, **(info or {})}


def _fail(error_type: str, message: str, **extra: Any) -> Dict[str, Any]:
    return {"success": False, "error": message, "error_type": error_type, **extra}


def _tom_error(exc: Exception) -> Dict[str, Any]:
    if "CompatibilityViolation" in type(exc).__name__ or "compatability level" in str(exc).lower():
        m = _MIN_CL_RE.search(str(exc))
        return _fail(
            "compatibility_level_too_low",
            str(exc).splitlines()[0],
            required_compatibility_level=int(m.group(1)) if m else None,
        )
    try:
        from core.autonomous.clr_errors import format_clr_error

        return format_clr_error(exc, error_type="tom_error")
    except Exception:  # noqa: BLE001
        return _fail("tom_error", str(exc))
