"""
Model-change classifier for ``17_Desktop_Bridge reload include_model=true`` (BR-04).

A bridge model reload (``file.reload/v1`` with ``reloadModelDefinition:true``)
re-applies the TMDL definition from disk to the RUNNING model. Live spike on
Desktop 2.157.1354 (2026-09-30, ``docs/research/2026-09-30-pbi-agentic-sept/
FACTS.md`` section E):

* metadata-only edits (measures, calculated columns, relationships, formats...)
  apply in place in ~2 s and the loaded import data is RETAINED;
* a changed partition / M source EMPTIES the table and the follow-up refresh
  left the Desktop host permanently "not ready" (the instance had to be
  killed). Those changes need close-without-saving + reopen.

So before the reload the pending on-disk definition is compared with the live
model and refused unless every difference is metadata-only:

ALLOWED ("metadata_only"): measures, calculated columns (add / change /
remove), calculated tables' DAX (and new / removed calculated or calculation-
group tables), relationships, descriptions, format strings, display folders,
hidden flags, hierarchies, roles, perspectives, cultures, user-defined
functions, and property changes of import columns.

REFUSED (``model_reload_unsafe_change``): any change to partitions (source /
mode / query / ANY other partition property), named expressions / M parameters,
data sources, table storage mode, refresh policies, the model default mode or
compatibility level, a new or removed import (storage-backed) table, and a new /
removed / retyped import (``sourceColumn``) column.

Fail CLOSED on unknown properties (N-08): the objects whose changes matter for the
loaded data (model, data sources, named expressions, tables, partitions, import
columns) are compared by EVERY property TOM serializes (``JsonSerializer`` TMSL for
a real object, the public attributes for a duck-typed fake), not by a fixed list. A
property that is neither one the classifier reviews (``_*_HANDLED``), nor a known
metadata-only one (``_*_SAFE``), nor runtime bookkeeping (``_IGNORED_PROPS``) is
UNSAFE when it differs, so a TOM property added in a later release (or one nobody
thought of: ``EncodingHint``, ``IsNullable``, ``DataView``,
``DefaultPowerBIDataSourceVersion``, the string / full-text indexing behaviours)
can never be silently treated as reload-safe. Partition source / named-expression M
text is compared exactly (line endings and outer whitespace normalised only).

Design: :func:`extract_signature` turns a TOM ``Database`` (live model or the
deserialized on-disk folder) into a plain, JSON-able dict; :func:`diff_signatures`
is a PURE function over two of them (unit-testable without a CLR);
:func:`classify_for_reload` and :func:`verify_after_reload` are the two
orchestrators the bridge calls. Fails CLOSED everywhere: an unreadable disk
folder is ``tmdl_invalid``, an unreadable / unidentified live model is
``model_change_unclassified``.

UDF gate: TOM's client library refuses ``function`` objects below compatibility
level 1000000 although Desktop accepts them at 1702, so the disk model is read
without functions (``udf_gate`` in the result) and functions are left out of
the comparison on BOTH sides. UDF changes are allowed anyway.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

ERR_UNSAFE = "model_reload_unsafe_change"
ERR_INVALID = "tmdl_invalid"
ERR_UNCLASSIFIED = "model_change_unclassified"

UNSAFE_GUIDANCE = (
    "Close Power BI Desktop WITHOUT saving (ask the user; never close it unprompted) and "
    "reopen the project with 17_Desktop_Bridge operation=open. A model reload that changes "
    "partitions / M sources / data sources / import tables or columns empties the table and "
    "can leave the Desktop host permanently not ready."
)

# Partition source kinds that hold no imported data (DAX-only objects).
_META_SOURCES = frozenset({"Calculated", "CalculationGroup", "None", ""})

# Cap on the change list returned to the caller (counts are always complete).
MAX_CHANGES_REPORTED = 100

# Post-reload verification: Desktop applies the definition synchronously, but the
# TOM view can lag a moment.
VERIFY_ATTEMPTS = 3
VERIFY_INTERVAL_S = 1.5


# ---------------------------------------------------------------------------
# Signature extraction (duck-typed over TOM objects)
# ---------------------------------------------------------------------------
def _g(obj: Any, name: str, default: Any = None) -> Any:
    """``getattr`` that also swallows CLR property exceptions."""
    if obj is None:
        return default
    try:
        val = getattr(obj, name)
    except Exception:  # noqa: BLE001 - missing attribute / CLR getter throws
        return default
    return default if val is None else val


def _iter(coll: Any) -> List[Any]:
    if coll is None:
        return []
    try:
        return list(coll)
    except Exception:  # noqa: BLE001
        return []


def _txt(value: Any) -> str:
    """Whitespace-collapsed text (line endings / indentation are not a change)."""
    if value is None:
        return ""
    return " ".join(str(value).split())


def _nl(value: str) -> str:
    """Exact text for M / source comparison: only line endings and outer whitespace
    are normalised, so a double space inside a string literal IS a change."""
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def _enum(value: Any) -> str:
    if value is None:
        return ""
    return str(value).rsplit(".", 1)[-1]


def _name_of(obj: Any) -> str:
    return str(_g(obj, "Name", "") or "")


def _mode(value: Any, default_mode: str) -> str:
    m = _enum(value)
    if m in ("", "Default", "None"):
        return default_mode
    return m


# --- generic property reading (N-08) -------------------------------------------------
# Runtime bookkeeping that differs between a live model and its on-disk definition
# without being a definition change (never compared).
_IGNORED_PROPS = frozenset(
    {
        "lineagetag",
        "sourcelineagetag",
        "changedproperties",
        "annotations",
        "extendedproperties",
        "modifiedtime",
        "structuremodifiedtime",
        "refreshedtime",
        "lastprocessed",
        "lastupdate",
        "lastschemaupdate",
        "state",
        "errormessage",
    }
)
# Child collections / back references: compared by their own signature sections.
_CHILD_KEYS = frozenset(
    {
        "tables",
        "columns",
        "measures",
        "partitions",
        "hierarchies",
        "relationships",
        "roles",
        "perspectives",
        "cultures",
        "expressions",
        "datasources",
        "functions",
        "calendars",
        "querygroups",
        "calculationgroup",
        "calculationitems",
        "levels",
        "tablepermissions",
        "members",
        "objecttranslations",
        "perspectivetables",
        "model",
        "table",
        "parent",
        "database",
    }
)
# Properties this module reads structurally (so the generic comparison skips them) ...
_MODEL_HANDLED = frozenset({"name", "defaultmode"})
_TABLE_HANDLED = frozenset({"name", "description", "ishidden", "datacategory", "refreshpolicy"})
_PARTITION_HANDLED = frozenset({"name", "mode", "source", "sourcetype"})
_COLUMN_HANDLED = frozenset(
    {
        "name",
        "type",
        "datatype",
        "sourcecolumn",
        "expression",
        "formatstring",
        "displayfolder",
        "description",
        "ishidden",
        "sortbycolumn",
        "summarizeby",
        "datacategory",
    }
)
# ... and properties known to be metadata-only (a change is reported as SAFE).
_MODEL_SAFE = frozenset({"description", "discourageimplicitmeasures", "defaultmeasure"})
_TABLE_SAFE = frozenset({"showasvariationsonly", "isprivate"})
_COLUMN_SAFE = frozenset({"isavailableinmdx", "isdefaultlabel", "isdefaultimage", "displayordinal"})

_TMSL_OPTIONS: Any = None


def _tmsl_dict(obj: Any) -> Optional[Dict[str, Any]]:
    """TMSL properties (no children, no timestamps) of a CLR TOM object; None for any other
    object. Raises when a TOM object cannot be serialized: fail closed, never guess."""
    global _TMSL_OPTIONS
    if not (getattr(type(obj), "__module__", "") or "").startswith("Microsoft.AnalysisServices"):
        return None
    import json

    from Microsoft.AnalysisServices.Tabular import JsonSerializer, SerializeOptions  # type: ignore

    if _TMSL_OPTIONS is None:
        opts = SerializeOptions()
        opts.IgnoreChildren = True
        opts.IgnoreTimestamps = True
        _TMSL_OPTIONS = opts
    return json.loads(JsonSerializer.SerializeObject(obj, _TMSL_OPTIONS))


def _norm_value(value: Any) -> Any:
    """JSON-able, comparison-stable form of a property value (None / "" are 'unset')."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _nl(value)
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for k, v in value.items():
            key = str(k).lower()
            if key in _IGNORED_PROPS or key in _CHILD_KEYS or str(k).startswith("_"):
                continue
            if key in ("expression", "query") and isinstance(v, (list, tuple)):
                v = "\n".join(str(x) for x in v)  # TMSL splits multi-line text into an array
            nv = _norm_value(v)
            if nv is not None and nv != "":
                out[key] = nv
        return out
    if isinstance(value, (list, tuple)):
        return [_norm_value(v) for v in value]
    if hasattr(value, "__dict__"):
        return _norm_value(vars(value))
    return _enum(value)


def _obj_props(obj: Any) -> Dict[str, Any]:
    """Every property of *obj* (lower-cased keys), minus children / runtime bookkeeping."""
    if obj is None:
        return {}
    raw = _tmsl_dict(obj)
    if raw is None:
        raw = vars(obj) if hasattr(obj, "__dict__") else {}
    out = _norm_value(raw)
    return out if isinstance(out, dict) else {}


def _split_props(
    props: Dict[str, Any], handled: Iterable[str], safe: Iterable[str] = ()
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """``(unreviewed, metadata_only)``: properties neither handled elsewhere nor listed safe
    are UNREVIEWED (a change is unsafe)."""
    handled, safe = frozenset(handled), frozenset(safe)
    unreviewed = {k: v for k, v in props.items() if k not in handled and k not in safe}
    meta = {k: v for k, v in props.items() if k in safe}
    return unreviewed, meta


def _flat(d: Dict[str, Any], collapse: bool, prefix: str = "") -> List[str]:
    bits: List[str] = []
    for k in sorted(d):
        v = d[k]
        if isinstance(v, dict):
            bits.extend(_flat(v, collapse, f"{prefix}{k}."))
        elif v not in (None, ""):
            bits.append(f"{prefix}{k}={_txt(v) if collapse else v}")
    return bits


def _partition_sig(part: Any, default_mode: str) -> Dict[str, Any]:
    props = _obj_props(part)
    source_type = _enum(_g(part, "SourceType"))
    src = props.get("source")
    # Every source property counts (M text exactly; DAX of calculated tables whitespace-insensitive).
    bits = _flat(src, collapse=source_type in _META_SOURCES) if isinstance(src, dict) else []
    unreviewed, _meta = _split_props(props, _PARTITION_HANDLED)
    return {
        "source": source_type,
        "mode": _mode(_g(part, "Mode"), default_mode),
        "definition": "|".join(bits),
        "props": unreviewed,
    }


def _column_sig(col: Any) -> Dict[str, Any]:
    unreviewed, meta = _split_props(_obj_props(col), _COLUMN_HANDLED, _COLUMN_SAFE)
    return {
        "props": unreviewed,
        "meta_props": meta,
        "type": _enum(_g(col, "Type")),
        "data_type": _enum(_g(col, "DataType")),
        "source_column": _txt(_g(col, "SourceColumn")),
        "expression": _txt(_g(col, "Expression")),
        "format_string": _txt(_g(col, "FormatString")),
        "display_folder": _txt(_g(col, "DisplayFolder")),
        "description": _txt(_g(col, "Description")),
        "hidden": bool(_g(col, "IsHidden", False)),
        "sort_by": _name_of(_g(col, "SortByColumn")),
        "summarize_by": _enum(_g(col, "SummarizeBy")),
        "data_category": _txt(_g(col, "DataCategory")),
    }


def _measure_sig(m: Any) -> Dict[str, Any]:
    return {
        "expression": _txt(_g(m, "Expression")),
        "format_string": _txt(_g(m, "FormatString")),
        "format_string_expression": _txt(_g(_g(m, "FormatStringDefinition"), "Expression")),
        "display_folder": _txt(_g(m, "DisplayFolder")),
        "description": _txt(_g(m, "Description")),
        "hidden": bool(_g(m, "IsHidden", False)),
    }


def _hierarchy_sig(h: Any) -> Dict[str, Any]:
    levels = [f"{_name_of(lv)}:{_name_of(_g(lv, 'Column'))}" for lv in _iter(_g(h, "Levels"))]
    return {
        "levels": levels,
        "display_folder": _txt(_g(h, "DisplayFolder")),
        "description": _txt(_g(h, "Description")),
        "hidden": bool(_g(h, "IsHidden", False)),
    }


_REFRESH_POLICY_ATTRS = (
    "PolicyType",
    "RollingWindowGranularity",
    "RollingWindowPeriods",
    "IncrementalGranularity",
    "IncrementalPeriods",
    "IncrementalPeriodsOffset",
    "SourceExpression",
    "Mode",
)


def _calc_items_sig(t: Any) -> Dict[str, Dict[str, Any]]:
    cg = _g(t, "CalculationGroup")
    return {
        _name_of(ci): {
            "expression": _txt(_g(ci, "Expression")),
            "format_string_expression": _txt(_g(_g(ci, "FormatStringDefinition"), "Expression")),
            "ordinal": _g(ci, "Ordinal"),
            "description": _txt(_g(ci, "Description")),
        }
        for ci in _iter(_g(cg, "CalculationItems"))
    }


def _table_sig(t: Any, default_mode: str) -> Dict[str, Any]:
    partitions = {_name_of(p): _partition_sig(p, default_mode) for p in _iter(_g(t, "Partitions"))}
    storage = any(p["source"] not in _META_SOURCES for p in partitions.values())
    rp = _g(t, "RefreshPolicy")
    refresh_policy = {a: _txt(_g(rp, a)) for a in _REFRESH_POLICY_ATTRS} if rp is not None else None
    unreviewed, meta = _split_props(_obj_props(t), _TABLE_HANDLED, _TABLE_SAFE)
    return {
        "storage": storage,
        "props": unreviewed,
        "meta_props": meta,
        "calc_items": _calc_items_sig(t),
        "description": _txt(_g(t, "Description")),
        "hidden": bool(_g(t, "IsHidden", False)),
        "data_category": _txt(_g(t, "DataCategory")),
        "partitions": partitions,
        "refresh_policy": refresh_policy,
        # The hidden system RowNumber column exists in the live model only.
        "columns": {
            _name_of(c): _column_sig(c)
            for c in _iter(_g(t, "Columns"))
            if _enum(_g(c, "Type")) != "RowNumber"
        },
        "measures": {_name_of(m): _measure_sig(m) for m in _iter(_g(t, "Measures"))},
        "hierarchies": {_name_of(h): _hierarchy_sig(h) for h in _iter(_g(t, "Hierarchies"))},
    }


def _relationships_sig(model: Any) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for r in _iter(_g(model, "Relationships")):
        fc, tc = _g(r, "FromColumn"), _g(r, "ToColumn")
        ft, tt = _name_of(_g(fc, "Table")), _name_of(_g(tc, "Table"))
        key = f"{ft}[{_name_of(fc)}] -> {tt}[{_name_of(tc)}]"
        n = 2
        while key in out:
            key = f"{key}#{n}"
            n += 1
        out[key] = {
            "active": bool(_g(r, "IsActive", True)),
            "cross_filter": _enum(_g(r, "CrossFilteringBehavior")),
            "from_cardinality": _enum(_g(r, "FromCardinality")),
            "to_cardinality": _enum(_g(r, "ToCardinality")),
            "security_filter": _enum(_g(r, "SecurityFilteringBehavior")),
        }
    return out


def _roles_sig(model: Any) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for role in _iter(_g(model, "Roles")):
        filters = {}
        for tp in _iter(_g(role, "TablePermissions")):
            tname = _name_of(_g(tp, "Table")) or _name_of(tp)
            filters[tname] = _txt(_g(tp, "FilterExpression"))
        out[_name_of(role)] = {
            "permission": _enum(_g(role, "ModelPermission")),
            "filters": filters,
            "members": sorted(
                str(_g(m, "MemberName") or _name_of(m)) for m in _iter(_g(role, "Members"))
            ),
        }
    return out


def extract_signature(database: Any) -> Dict[str, Any]:
    """Plain-dict signature of a TOM ``Database`` (live or deserialized from disk).

    Only reads attributes; never mutates ``database``. Names are case-preserved.
    """
    model = _g(database, "Model")
    default_mode = _enum(_g(model, "DefaultMode"))
    if default_mode in ("", "Default", "None"):
        default_mode = "Import"
    cl = _g(database, "CompatibilityLevel")
    try:
        cl_val: Optional[int] = int(cl) if cl is not None else None
    except (TypeError, ValueError):
        cl_val = None
    model_props, model_meta = _split_props(_obj_props(model), _MODEL_HANDLED, _MODEL_SAFE)
    return {
        "compatibility_level": cl_val,
        "default_mode": default_mode,
        "model_props": model_props,
        "model_meta": model_meta,
        # Every property of a data source / named expression counts (N-08).
        "data_sources": {_name_of(ds): _obj_props(ds) for ds in _iter(_g(model, "DataSources"))},
        "expressions": {_name_of(e): _obj_props(e) for e in _iter(_g(model, "Expressions"))},
        "tables": {_name_of(t): _table_sig(t, default_mode) for t in _iter(_g(model, "Tables"))},
        "relationships": _relationships_sig(model),
        "roles": _roles_sig(model),
        "perspectives": {
            _name_of(p): sorted(_name_of(pt) for pt in _iter(_g(p, "PerspectiveTables")))
            for p in _iter(_g(model, "Perspectives"))
        },
        "cultures": {
            _name_of(c): len(_iter(_g(c, "ObjectTranslations")))
            for c in _iter(_g(model, "Cultures"))
        },
        "functions": {
            _name_of(f): _txt(_g(f, "Expression")) for f in _iter(_g(model, "Functions"))
        },
    }


# ---------------------------------------------------------------------------
# Pure diff + verdict
# ---------------------------------------------------------------------------
def _chg(
    category: str,
    obj: str,
    change: str,
    safe: bool,
    reason: str,
    fields: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "category": category,
        "object": obj,
        "change": change,
        "safe": safe,
        "reason": reason,
    }
    if fields:
        out["fields"] = sorted(fields)
    return out


def _diff_props(a: Dict[str, Any], b: Dict[str, Any]) -> List[str]:
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))


def _diff_named(
    out: List[Dict[str, Any]],
    category: str,
    live: Dict[str, Any],
    disk: Dict[str, Any],
    *,
    safe: bool,
    what: str,
) -> None:
    """Added / removed / changed for a flat name -> value map with one safety verdict."""
    for name in sorted(set(live) | set(disk)):
        if name not in live:
            out.append(_chg(category, name, "added", safe, f"{what} added on disk"))
        elif name not in disk:
            out.append(_chg(category, name, "removed", safe, f"{what} removed on disk"))
        elif live[name] != disk[name]:
            fields = (
                _diff_props(live[name], disk[name])
                if isinstance(live[name], dict) and isinstance(disk[name], dict)
                else None
            )
            out.append(_chg(category, name, "changed", safe, f"{what} changed on disk", fields))


def _diff_table(
    out: List[Dict[str, Any]], name: str, lt: Dict[str, Any], dt: Dict[str, Any]
) -> None:
    if lt["storage"] != dt["storage"]:
        out.append(
            _chg(
                "table",
                name,
                "changed",
                False,
                "table switches between calculated / calculation-group and storage-backed",
                ["storage"],
            )
        )
    calc_only = (not lt["storage"]) and (not dt["storage"])

    # partitions
    lp_all, dp_all = lt["partitions"], dt["partitions"]
    for pn in sorted(set(lp_all) | set(dp_all)):
        obj = f"{name}/partition:{pn}"
        lp, dp = lp_all.get(pn), dp_all.get(pn)
        if lp is None or dp is None:
            side = dp if lp is None else lp
            safe = calc_only and side["source"] in _META_SOURCES
            out.append(
                _chg(
                    "partition",
                    obj,
                    "added" if lp is None else "removed",
                    safe,
                    (
                        "calculated-table partition (DAX only)"
                        if safe
                        else "partition added/removed: a metadata reload cannot load or drop imported data"
                    ),
                )
            )
            continue
        fields = _diff_props(lp, dp)
        if not fields:
            continue
        both_calc = lp["source"] == dp["source"] and lp["source"] in (
            "Calculated",
            "CalculationGroup",
        )
        if both_calc and set(fields) <= {"definition"}:
            out.append(
                _chg("partition", obj, "changed", True, "calculated table DAX changed", fields)
            )
        else:
            out.append(
                _chg(
                    "partition",
                    obj,
                    "changed",
                    False,
                    "partition source / mode / query changed: data must be reprocessed "
                    "(fields: " + ", ".join(fields) + ")",
                    fields,
                )
            )

    if lt["refresh_policy"] != dt["refresh_policy"]:
        out.append(
            _chg("refresh_policy", name, "changed", False, "incremental refresh policy changed")
        )

    # columns
    lc_all, dc_all = lt["columns"], dt["columns"]
    for cn in sorted(set(lc_all) | set(dc_all)):
        obj = f"{name}[{cn}]"
        lc, dc = lc_all.get(cn), dc_all.get(cn)
        if lc is None or dc is None:
            col = dc if lc is None else lc
            imported = col["type"] == "Data"
            out.append(
                _chg(
                    "column",
                    obj,
                    "added" if lc is None else "removed",
                    not imported,
                    (
                        "import (sourceColumn) column added/removed: needs a data refresh / reopen"
                        if imported
                        else f"{col['type'] or 'calculated'} column (DAX only)"
                    ),
                )
            )
            continue
        fields = _diff_props(lc, dc)
        if not fields:
            continue
        if "Data" in (lc["type"], dc["type"]):
            # N-08: `props` = any column property the classifier does not review
            # (EncodingHint, IsNullable, indexing behaviours, ...) is unsafe on an import column.
            structural = sorted({"type", "data_type", "source_column", "props"} & set(fields))
            if structural:
                out.append(
                    _chg(
                        "column",
                        obj,
                        "changed",
                        False,
                        "import column type / source changed (fields: "
                        + ", ".join(structural)
                        + ")",
                        fields,
                    )
                )
                continue
        out.append(_chg("column", obj, "changed", True, "column metadata / DAX changed", fields))

    # measures / hierarchies: metadata only
    for kind, key in (("measure", "measures"), ("hierarchy", "hierarchies")):
        lm, dm = lt[key], dt[key]
        for mn in sorted(set(lm) | set(dm)):
            obj = f"{name}[{mn}]" if kind == "measure" else f"{name}/hierarchy:{mn}"
            if mn not in lm:
                out.append(_chg(kind, obj, "added", True, f"{kind} added on disk"))
            elif mn not in dm:
                out.append(_chg(kind, obj, "removed", True, f"{kind} removed on disk"))
            elif lm[mn] != dm[mn]:
                out.append(
                    _chg(
                        kind,
                        obj,
                        "changed",
                        True,
                        f"{kind} changed on disk",
                        _diff_props(lm[mn], dm[mn]),
                    )
                )

    tprops = [k for k in ("description", "hidden", "data_category") if lt[k] != dt[k]]
    if lt.get("meta_props", {}) != dt.get("meta_props", {}):
        tprops.append("meta_props")
    if tprops:
        out.append(
            _chg("table_properties", name, "changed", True, "table metadata changed", tprops)
        )
    # N-08: any other table property is unreviewed -> unsafe (fail closed)
    if lt.get("props", {}) != dt.get("props", {}):
        out.append(
            _chg(
                "table_properties",
                name,
                "changed",
                False,
                "unreviewed table property changed (fields: "
                + ", ".join(_diff_props(lt.get("props", {}), dt.get("props", {})))
                + ")",
                ["props"],
            )
        )

    # calculation-group items: DAX metadata, applied in place
    li, di = lt.get("calc_items", {}), dt.get("calc_items", {})
    for cn in sorted(set(li) | set(di)):
        obj = f"{name}/calculation_item:{cn}"
        if cn not in li:
            out.append(
                _chg("calculation_item", obj, "added", True, "calculation item added on disk")
            )
        elif cn not in di:
            out.append(
                _chg("calculation_item", obj, "removed", True, "calculation item removed on disk")
            )
        elif li[cn] != di[cn]:
            out.append(
                _chg(
                    "calculation_item",
                    obj,
                    "changed",
                    True,
                    "calculation item changed on disk",
                    _diff_props(li[cn], di[cn]),
                )
            )


def diff_signatures(
    live: Dict[str, Any], disk: Dict[str, Any], *, skip_functions: bool = False
) -> List[Dict[str, Any]]:
    """Every difference between the live model and the on-disk definition.

    Each entry: ``{category, object, change: added|removed|changed, safe, reason,
    fields?}``. ``safe`` is the reload-safety verdict (see module docstring).
    ``added`` = present on DISK only (what the reload would introduce),
    ``removed`` = present in the live model only.
    """
    out: List[Dict[str, Any]] = []
    for key, label in (
        ("compatibility_level", "model compatibility level"),
        ("default_mode", "model default storage mode"),
    ):
        if live.get(key) != disk.get(key):
            out.append(_chg("model", key, "changed", False, f"{label} differs from disk", [key]))
    for key, safe, what in (
        ("model_props", False, "unreviewed model property"),
        ("model_meta", True, "model metadata"),
    ):
        lp, dp = live.get(key, {}), disk.get(key, {})
        if lp != dp:
            out.append(
                _chg(
                    "model",
                    "properties" if not safe else "metadata",
                    "changed",
                    safe,
                    f"{what} changed (fields: {', '.join(_diff_props(lp, dp))})",
                    _diff_props(lp, dp),
                )
            )
    _diff_named(
        out,
        "data_source",
        live["data_sources"],
        disk["data_sources"],
        safe=False,
        what="data source",
    )
    _diff_named(
        out,
        "named_expression",
        live["expressions"],
        disk["expressions"],
        safe=False,
        what="named expression / M parameter",
    )

    lts, dts = live["tables"], disk["tables"]
    for name in sorted(set(lts) | set(dts)):
        lt, dt = lts.get(name), dts.get(name)
        if lt is None:
            safe = not dt["storage"]
            out.append(
                _chg(
                    "table",
                    name,
                    "added",
                    safe,
                    (
                        "new calculated / calculation-group table (DAX only)"
                        if safe
                        else "new import (storage-backed) table: needs partitions loaded and a data refresh"
                    ),
                )
            )
        elif dt is None:
            safe = not lt["storage"]
            out.append(
                _chg(
                    "table",
                    name,
                    "removed",
                    safe,
                    (
                        "calculated / calculation-group table removed (DAX only)"
                        if safe
                        else "import (storage-backed) table removed: its loaded data would be dropped"
                    ),
                )
            )
        else:
            _diff_table(out, name, lt, dt)

    _diff_named(
        out,
        "relationship",
        live["relationships"],
        disk["relationships"],
        safe=True,
        what="relationship",
    )
    _diff_named(out, "role", live["roles"], disk["roles"], safe=True, what="role")
    _diff_named(
        out,
        "perspective",
        live["perspectives"],
        disk["perspectives"],
        safe=True,
        what="perspective",
    )
    _diff_named(out, "culture", live["cultures"], disk["cultures"], safe=True, what="culture")
    if not skip_functions:
        _diff_named(
            out, "function", live["functions"], disk["functions"], safe=True, what="function (UDF)"
        )
    return out


def classify(changes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Verdict + counts for a change list."""
    unsafe = [c for c in changes if not c["safe"]]
    counts: Dict[str, int] = {}
    for c in changes:
        counts[c["category"]] = counts.get(c["category"], 0) + 1
    return {
        "verdict": "unsafe" if unsafe else ("metadata_only" if changes else "unchanged"),
        "change_count": len(changes),
        "unsafe_count": len(unsafe),
        "counts_by_category": counts,
        "calculated_objects_changed": any(_touches_calculated(c) for c in changes),
    }


def _touches_calculated(c: Dict[str, Any]) -> bool:
    """Does this SAFE change add / alter DAX that only a Calculate refresh evaluates?"""
    if not c["safe"]:
        return False
    if c["category"] == "partition":
        return True  # calculated-table DAX
    if c["category"] == "table":
        return c["change"] in ("added", "removed")
    if c["category"] == "column":
        return "expression" in (c.get("fields") or []) or "calculated" in c["reason"].lower()
    return False


def _capped(changes: List[Dict[str, Any]], *, unsafe_first: bool = True) -> Dict[str, Any]:
    ordered = sorted(changes, key=lambda c: c["safe"]) if unsafe_first else list(changes)
    out: Dict[str, Any] = {"changes": ordered[:MAX_CHANGES_REPORTED]}
    if len(ordered) > MAX_CHANGES_REPORTED:
        out["changes_truncated"] = {"shown": MAX_CHANGES_REPORTED, "total": len(ordered)}
    return out


# ---------------------------------------------------------------------------
# I/O seams (tests patch these two)
# ---------------------------------------------------------------------------
def _read_disk_signature(definition_dir: str) -> Dict[str, Any]:
    """``{ok, signature, udf_gate, error, reason}`` for the on-disk TMDL folder."""
    from core.tmdl.tom_check import load_database_from_folder

    res = load_database_from_folder(definition_dir)
    if not res.get("ran"):
        return {"ok": False, "unavailable": True, "reason": res.get("reason")}
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error")}
    try:
        signature = extract_signature(res["database"])
    except Exception as e:  # noqa: BLE001 - CLR interop; never guess a verdict
        return {
            "ok": False,
            "unavailable": True,
            "reason": f"disk model signature failed: {type(e).__name__}: {e}",
        }
    return {"ok": True, "signature": signature, "udf_gate": bool(res.get("udf_gate"))}


def _read_live_signature(project_matches: Optional[Callable[[str], bool]]) -> Dict[str, Any]:
    """``{ok, signature, reason}`` for the model this server is connected to.

    ``project_matches(path)`` tells whether *path* is the project of the Desktop
    instance being reloaded. The connected model MUST be that project's — comparing
    another model's partitions would clear a reload that is not safe.
    """
    try:
        from core.infrastructure.connection_state import connection_state
        from core.operations.amo_helpers import get_server_db_model, safe_disconnect
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"connection layer unavailable: {e}"}
    try:
        connected = connection_state.is_connected()
    except Exception:  # noqa: BLE001
        connected = False
    if not connected:
        return {
            "ok": False,
            "reason": "this server is not connected to a model (01_Connection operation=ensure)",
        }
    if project_matches is not None:
        info = connection_state.get_pbip_info() or {}
        candidates = [p for p in (info.get("file_full_path"), info.get("pbip_folder_path")) if p]
        if not candidates:
            return {
                "ok": False,
                "reason": "cannot verify that the connected model is the reload target "
                "(no PBIP path recorded for the connection; reconnect with 01_Connection)",
            }
        if not any(project_matches(p) for p in candidates):
            return {
                "ok": False,
                "reason": "the model this server is connected to is a different project than the "
                f"Desktop instance being reloaded ({candidates[0]}); reconnect to the target "
                "instance (01_Connection with the right model_index) first",
            }
    server, db, _model, err = get_server_db_model(connection_state)
    if db is None:
        return {"ok": False, "reason": f"live model unreadable: {err}"}
    try:
        return {"ok": True, "signature": extract_signature(db)}
    except Exception as e:  # noqa: BLE001 - CLR interop
        return {"ok": False, "reason": f"live model signature failed: {type(e).__name__}: {e}"}
    finally:
        safe_disconnect(server)


def find_model_definition(pbip_or_report_path: str) -> Tuple[Optional[str], Optional[str]]:
    """``(definition_dir, problem)`` of the single local TMDL model behind a project path."""
    try:
        from core.pbip.pbir_model import resolve_pbip

        res = resolve_pbip(pbip_or_report_path)
    except Exception as e:  # noqa: BLE001
        return None, f"could not resolve the project: {e}"
    models = res.get("models") or []
    if not models:
        return None, "the report is not bound to a local .SemanticModel folder (byConnection?)"
    if len(models) > 1:
        return None, f"{len(models)} semantic models resolve from this project; cannot pick one"
    m = models[0]
    if m.get("format") != "tmdl":
        return None, f"the semantic model is stored as {m.get('format')!r}, not TMDL (definition/)"
    ddir = m.get("definition_dir")
    if not ddir or not Path(ddir).is_dir():
        return None, "the semantic model has no definition/ folder"
    return str(ddir), None


# ---------------------------------------------------------------------------
# Orchestrators
# ---------------------------------------------------------------------------
def classify_for_reload(
    project_path: str,
    *,
    project_matches: Optional[Callable[[str], bool]] = None,
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """Classify the pending disk definition of *project_path* against the live model.

    Returns ``(result, context)``. ``result["success"]`` is False with
    ``error_type`` ``tmdl_invalid`` / ``model_change_unclassified`` /
    ``model_reload_unsafe_change`` when the reload must not run (``context`` is
    None); otherwise it carries ``classification`` (``metadata_only`` |
    ``unchanged``) and the (capped) change list. ``context`` =
    ``{disk_signature, changes, udf_gate}`` — the FULL change list — is what
    :func:`verify_after_reload` needs.
    """
    ddir, problem = find_model_definition(project_path)
    if ddir is None:
        return (
            {
                "success": False,
                "error_type": ERR_UNCLASSIFIED,
                "error": f"Cannot classify the pending model change: {problem}. The model "
                "reload was NOT attempted (fail closed).",
                "guidance": UNSAFE_GUIDANCE,
            },
            None,
        )
    disk = _read_disk_signature(ddir)
    if not disk.get("ok"):
        if disk.get("unavailable"):
            return (
                {
                    "success": False,
                    "error_type": ERR_UNCLASSIFIED,
                    "error": "Cannot classify the pending model change: the TOM assemblies are "
                    f"not available to read the on-disk TMDL ({disk.get('reason')}). The model "
                    "reload was NOT attempted (fail closed).",
                },
                None,
            )
        return (
            {
                "success": False,
                "error_type": ERR_INVALID,
                "error": "The on-disk TMDL does not deserialize with the TOM parser, so Desktop "
                f"would reject it too: {disk.get('error')}. The model reload was NOT attempted.",
                "parser_message": disk.get("error"),
                "definition_dir": ddir,
                "hint": "Fix the TMDL (python ~/.claude/skills/pbip/scripts/validate_tmdl_blocks.py "
                "<definition dir> or 07_PBIP_Operations validate_model) and retry.",
            },
            None,
        )
    live = _read_live_signature(project_matches)
    if not live.get("ok"):
        return (
            {
                "success": False,
                "error_type": ERR_UNCLASSIFIED,
                "error": "Cannot classify the pending model change: "
                f"{live.get('reason')}. The model reload was NOT attempted (fail closed).",
            },
            None,
        )
    udf_gate = bool(disk.get("udf_gate"))
    changes = diff_signatures(live["signature"], disk["signature"], skip_functions=udf_gate)
    verdict = classify(changes)
    base: Dict[str, Any] = {
        "definition_dir": ddir,
        "udf_gate": udf_gate,
        **verdict,
        **_capped(changes),
    }
    if udf_gate:
        base["udf_gate_note"] = (
            "The TOM client library cannot read function (UDF) objects at this compatibility "
            "level, so functions were left out of the comparison; UDF changes are allowed."
        )
    if verdict["verdict"] == "unsafe":
        unsafe = [c for c in changes if not c["safe"]]
        return (
            {
                "success": False,
                "error_type": ERR_UNSAFE,
                "error": f"Refusing the model reload: {len(unsafe)} pending change(s) are not "
                "metadata-only ("
                + "; ".join(f"{c['category']} {c['object']} {c['change']}" for c in unsafe[:5])
                + ("; ..." if len(unsafe) > 5 else "")
                + "). "
                + UNSAFE_GUIDANCE,
                "guidance": UNSAFE_GUIDANCE,
                "classification": verdict["verdict"],
                "unsafe_changes": unsafe[:MAX_CHANGES_REPORTED],
                **base,
            },
            None,
        )
    return (
        {"success": True, "classification": verdict["verdict"], **base},
        {"disk_signature": disk["signature"], "changes": changes, "udf_gate": udf_gate},
    )


def verify_after_reload(
    disk_signature: Dict[str, Any],
    changes: List[Dict[str, Any]],
    *,
    project_matches: Optional[Callable[[str], bool]] = None,
    skip_functions: bool = False,
    attempts: int = VERIFY_ATTEMPTS,
    interval_s: float = VERIFY_INTERVAL_S,
) -> Dict[str, Any]:
    """Re-read the live model and report which classified changes now match the disk.

    Returns ``{status: verified|mismatch|unavailable, applied: [...], mismatch:
    [...], reason?}``. ``applied`` / ``mismatch`` are ``"<category> <object>
    <change>"`` strings. ``unavailable`` = the live model could not be re-read
    (never reported as verified). Verify by DAX / TOM — the Data pane in Desktop
    can stay stale after a model reload.
    """
    last: Dict[str, Any] = {}
    remaining: List[Dict[str, Any]] = []
    for attempt in range(max(1, attempts)):
        last = _read_live_signature(project_matches)
        if last.get("ok"):
            remaining = diff_signatures(
                last["signature"], disk_signature, skip_functions=skip_functions
            )
            if not remaining:
                break
        if attempt < attempts - 1:
            time.sleep(interval_s)
    if not last.get("ok"):
        return {
            "status": "unavailable",
            "applied": [],
            "mismatch": [],
            "reason": last.get("reason"),
        }

    def _label(c: Dict[str, Any]) -> str:
        return f"{c['category']} {c['object']} {c['change']}"

    left = {(c["category"], c["object"]) for c in remaining}
    applied = [_label(c) for c in changes if (c["category"], c["object"]) not in left]
    expected = {(c["category"], c["object"]) for c in changes}
    mismatch = [_label(c) for c in remaining]
    # Anything still different that the pre-reload classification did not list is
    # reported too (it is in `mismatch`), flagged so the caller can tell.
    unexpected = [_label(c) for c in remaining if (c["category"], c["object"]) not in expected]
    out: Dict[str, Any] = {
        "status": "mismatch" if mismatch else "verified",
        "applied": applied[:MAX_CHANGES_REPORTED],
        "mismatch": mismatch[:MAX_CHANGES_REPORTED],
    }
    if len(applied) > MAX_CHANGES_REPORTED:
        out["applied_truncated"] = {"shown": MAX_CHANGES_REPORTED, "total": len(applied)}
    if unexpected:
        out["unexpected_mismatch"] = unexpected[:MAX_CHANGES_REPORTED]
    return out


__all__ = [
    "ERR_UNSAFE",
    "ERR_INVALID",
    "ERR_UNCLASSIFIED",
    "UNSAFE_GUIDANCE",
    "extract_signature",
    "diff_signatures",
    "classify",
    "classify_for_reload",
    "verify_after_reload",
    "find_model_definition",
]
