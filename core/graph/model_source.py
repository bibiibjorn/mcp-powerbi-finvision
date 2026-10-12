"""Model side of the dependency graph — every DAX-bearing object of the live model.

Review J-03 (b/c): the graph used to see measure expressions only (and, until
WP1 fixed the facade, no columns at all). ``load_live_snapshot`` reads, via the
``INFO.*`` DAX functions (one query each, cache bypassed):

* tables (+ calculated-table / field-parameter partitions, calc groups,
  default detail-rows expressions),
* columns (type, calculated expression, sort-by column),
* measures (expression, dynamic format-string definition, detail rows),
* calculation items (expression + format-string definition),
* roles + table permissions (RLS filter expressions),
* relationships (by column id), hierarchies + levels,
* DAX user-defined functions (``OptimizedQueryExecutor.list_udfs`` — AMO cannot
  see them, LIVE-CHECKS.md).

A failed optional query is recorded in ``warnings`` / ``missing`` rather than
silently producing a smaller graph; a failed CORE query (tables / columns /
measures) sets ``complete = False`` so consumers can fail closed.

``snapshot_from_facade`` adapts the duck-typed ``live_model_facade`` shape
(``model.tables[].columns/.measures``) — used by tests and older callers.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_CORE_VIEWS = ("TABLES", "COLUMNS", "MEASURES")
_OPTIONAL_VIEWS = (
    "RELATIONSHIPS",
    "PARTITIONS",
    "CALCULATIONGROUPS",
    "CALCULATIONITEMS",
    "ROLES",
    "TABLEPERMISSIONS",
    "HIERARCHIES",
    "LEVELS",
    "FORMATSTRINGDEFINITIONS",
    "DETAILROWSDEFINITIONS",
)
# INFO.COLUMNS [Type]: 1 Data, 2 Calculated, 3 RowNumber, 4 CalculatedTableColumn.
_COLUMN_TYPES = {1: "data", 2: "calculated", 3: "row_number", 4: "calculated_table_column"}
# INFO.PARTITIONS [Type]: 2 = Calculated (DAX), 7 = CalculationGroup.
_PARTITION_CALCULATED = 2


@dataclass
class ModelSnapshot:
    """Plain-data view of the model objects the graph builder needs."""

    tables: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    columns: List[Dict[str, Any]] = field(default_factory=list)
    measures: List[Dict[str, Any]] = field(default_factory=list)
    calc_items: List[Dict[str, Any]] = field(default_factory=list)
    roles: List[Dict[str, Any]] = field(default_factory=list)
    relationships: List[Dict[str, Any]] = field(default_factory=list)
    hierarchies: List[Dict[str, Any]] = field(default_factory=list)
    functions: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    complete: bool = True
    source: str = "live"

    def etag(self) -> str:
        """Content hash of every name + expression + link the graph is built from."""
        parts: List[str] = []
        for t, info in sorted(self.tables.items()):
            parts.append(f"T|{t}|{info.get('expression') or ''}|{info.get('detail_rows') or ''}")
        for c in self.columns:
            parts.append(
                f"C|{c['table']}|{c['name']}|{c.get('type')}|{c.get('expression') or ''}|"
                f"{c.get('sort_by') or ''}"
            )
        for m in self.measures:
            parts.append(
                f"M|{m['table']}|{m['name']}|{m.get('expression') or ''}|"
                f"{m.get('format_string_expr') or ''}|{m.get('detail_rows_expr') or ''}"
            )
        for ci in self.calc_items:
            parts.append(
                f"I|{ci['table']}|{ci['name']}|{ci.get('expression') or ''}|"
                f"{ci.get('format_string_expr') or ''}"
            )
        for r in self.roles:
            perms = ";".join(
                f"{p['table']}={p.get('filter') or ''}" for p in r.get("permissions", [])
            )
            parts.append(f"R|{r['name']}|{perms}")
        for rel in self.relationships:
            parts.append(
                f"L|{rel['from_table']}|{rel['from_column']}|{rel['to_table']}|"
                f"{rel['to_column']}|{rel.get('active')}"
            )
        for h in self.hierarchies:
            parts.append(f"H|{h['table']}|{h['name']}|{','.join(h.get('levels', []))}")
        for f in self.functions:
            parts.append(f"F|{f['name']}|{f.get('expression') or ''}")
        parts.sort()
        return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------
def _norm_row(row: Any) -> Dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    out: Dict[str, Any] = {}
    for k, v in row.items():
        key = str(k)
        if key.startswith("[") and key.endswith("]"):
            key = key[1:-1]
        out[key.casefold()] = v
    return out


def _g(row: Dict[str, Any], *names: str, default: Any = None) -> Any:
    for n in names:
        v = row.get(n.casefold())
        if v is not None and v != "":
            return v
    return default


def _int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _key(v: Any) -> Optional[str]:
    """IDs arrive as int / float / str depending on the path — compare as text."""
    if v is None:
        return None
    iv = _int(v)
    return str(iv) if iv is not None else str(v)


def _run_info(qe: Any, view: str) -> Optional[List[Dict[str, Any]]]:
    """``EVALUATE INFO.<view>()`` rows (normalised keys), or None on failure."""
    query = f"EVALUATE INFO.{view}()"
    try:
        try:
            res = qe.validate_and_execute_dax(query, 0, bypass_cache=True, max_rows=1_000_000)
        except TypeError:
            res = qe.validate_and_execute_dax(query, 0)
    except Exception as e:  # noqa: BLE001 - CLR / connection edge
        logger.debug("INFO.%s failed: %s", view, e)
        return None
    if not isinstance(res, dict) or not res.get("success"):
        return None
    return [_norm_row(r) for r in (res.get("rows") or [])]


# ---------------------------------------------------------------------------
# Live snapshot
# ---------------------------------------------------------------------------
def load_live_snapshot(
    qe: Any, *, runner: Optional[Callable[[Any, str], Optional[List[Dict[str, Any]]]]] = None
) -> ModelSnapshot:
    """Read every DAX-bearing model object through ``INFO.*`` (see module doc)."""
    run = runner or _run_info
    snap = ModelSnapshot()
    rows: Dict[str, Optional[List[Dict[str, Any]]]] = {}
    for view in _CORE_VIEWS + _OPTIONAL_VIEWS:
        rows[view] = run(qe, view)
        if rows[view] is None:
            snap.missing.append(view)
            if view in _CORE_VIEWS:
                snap.complete = False
                snap.warnings.append(f"INFO.{view}() failed — the model side is incomplete")
            else:
                snap.warnings.append(f"INFO.{view}() unavailable — {view.lower()} not modelled")

    table_by_id: Dict[str, str] = {}
    for r in rows["TABLES"] or []:
        name = _g(r, "Name")
        if not name:
            continue
        name = str(name)
        table_by_id[_key(_g(r, "ID")) or name] = name
        snap.tables[name] = {
            "expression": None,
            "calculated": False,
            "field_parameter": False,
            "calc_group": _g(r, "CalculationGroupID") is not None,
            "detail_rows_id": _key(_g(r, "DefaultDetailRowsDefinitionID")),
            "detail_rows": None,
        }

    fsd_by_id: Dict[str, str] = {}
    for r in rows["FORMATSTRINGDEFINITIONS"] or []:
        if _g(r, "Expression"):
            fsd_by_id[_key(_g(r, "ID")) or ""] = str(_g(r, "Expression"))
    drd_by_id: Dict[str, str] = {}
    for r in rows["DETAILROWSDEFINITIONS"] or []:
        if _g(r, "Expression"):
            drd_by_id[_key(_g(r, "ID")) or ""] = str(_g(r, "Expression"))
    for info in snap.tables.values():
        did = info.pop("detail_rows_id", None)
        if did and did in drd_by_id:
            info["detail_rows"] = drd_by_id[did]

    for r in rows["PARTITIONS"] or []:
        tname = table_by_id.get(_key(_g(r, "TableID")) or "")
        if not tname or _int(_g(r, "Type")) != _PARTITION_CALCULATED:
            continue
        expr = _g(r, "QueryDefinition", "Expression")
        if expr:
            info = snap.tables[tname]
            info["calculated"] = True
            info["expression"] = str(expr)
            info["field_parameter"] = "NAMEOF" in str(expr).upper()

    col_by_id: Dict[str, Dict[str, Any]] = {}
    for r in rows["COLUMNS"] or []:
        ctype = _int(_g(r, "Type"))
        if ctype == 3:  # RowNumber — internal, never referenced
            continue
        name = _g(r, "ExplicitName", "Name", "InferredName")
        tname = table_by_id.get(_key(_g(r, "TableID")) or "") or _g(r, "Table", "TableName")
        if not name or not tname:
            continue
        col = {
            "table": str(tname),
            "name": str(name),
            "type": _COLUMN_TYPES.get(ctype or 1, "data"),
            "expression": str(_g(r, "Expression")) if _g(r, "Expression") else None,
            "sort_by_id": _key(_g(r, "SortByColumnID")),
            "sort_by": None,
        }
        snap.columns.append(col)
        cid = _key(_g(r, "ID"))
        if cid:
            col_by_id[cid] = col
        if str(tname) not in snap.tables:
            snap.tables[str(tname)] = {
                "expression": None,
                "calculated": False,
                "field_parameter": False,
                "calc_group": False,
                "detail_rows": None,
            }
    for col in snap.columns:
        sid = col.pop("sort_by_id", None)
        if sid and sid in col_by_id:
            col["sort_by"] = col_by_id[sid]["name"]

    for r in rows["MEASURES"] or []:
        name = _g(r, "Name")
        tname = table_by_id.get(_key(_g(r, "TableID")) or "") or _g(r, "Table", "TableName")
        if not name or not tname:
            continue
        snap.measures.append(
            {
                "table": str(tname),
                "name": str(name),
                "expression": str(_g(r, "Expression", default="") or ""),
                "format_string_expr": fsd_by_id.get(_key(_g(r, "FormatStringDefinitionID")) or ""),
                "detail_rows_expr": drd_by_id.get(_key(_g(r, "DetailRowsDefinitionID")) or ""),
            }
        )

    group_table: Dict[str, str] = {}
    for r in rows["CALCULATIONGROUPS"] or []:
        tname = table_by_id.get(_key(_g(r, "TableID")) or "")
        if tname:
            group_table[_key(_g(r, "ID")) or ""] = tname
            snap.tables.setdefault(tname, {})["calc_group"] = True
    for r in rows["CALCULATIONITEMS"] or []:
        tname = group_table.get(_key(_g(r, "CalculationGroupID")) or "")
        name = _g(r, "Name")
        if not tname or not name:
            continue
        snap.calc_items.append(
            {
                "table": tname,
                "name": str(name),
                "expression": str(_g(r, "Expression", default="") or ""),
                "format_string_expr": fsd_by_id.get(_key(_g(r, "FormatStringDefinitionID")) or ""),
            }
        )

    role_by_id: Dict[str, Dict[str, Any]] = {}
    for r in rows["ROLES"] or []:
        name = _g(r, "Name")
        if name:
            role = {"name": str(name), "permissions": []}
            snap.roles.append(role)
            role_by_id[_key(_g(r, "ID")) or str(name)] = role
    for r in rows["TABLEPERMISSIONS"] or []:
        role = role_by_id.get(_key(_g(r, "RoleID")) or "")
        tname = table_by_id.get(_key(_g(r, "TableID")) or "")
        if role is None or not tname:
            continue
        role["permissions"].append(
            {"table": tname, "filter": str(_g(r, "FilterExpression", default="") or "")}
        )

    for r in rows["RELATIONSHIPS"] or []:
        fc = col_by_id.get(_key(_g(r, "FromColumnID")) or "")
        tc = col_by_id.get(_key(_g(r, "ToColumnID")) or "")
        if fc is None or tc is None:
            continue
        active = _g(r, "IsActive", default=True)
        snap.relationships.append(
            {
                "from_table": fc["table"],
                "from_column": fc["name"],
                "to_table": tc["table"],
                "to_column": tc["name"],
                "active": str(active).strip().lower() not in ("false", "0"),
                # INFO.RELATIONSHIPS ints (1 one / 2 many; 1 single / 2 both): kept for the
                # filter-propagation analysis (core/pbip/interaction_audit.py), not in the etag.
                "from_card": _g(r, "FromCardinality"),
                "to_card": _g(r, "ToCardinality"),
                "cross_filter": _g(r, "CrossFilteringBehavior"),
            }
        )

    hier_by_id: Dict[str, Dict[str, Any]] = {}
    for r in rows["HIERARCHIES"] or []:
        tname = table_by_id.get(_key(_g(r, "TableID")) or "")
        name = _g(r, "Name")
        if tname and name:
            h = {"table": tname, "name": str(name), "levels": [], "level_names": {}}
            snap.hierarchies.append(h)
            hier_by_id[_key(_g(r, "ID")) or ""] = h
    for r in sorted(rows["LEVELS"] or [], key=lambda x: _int(_g(x, "Ordinal")) or 0):
        h = hier_by_id.get(_key(_g(r, "HierarchyID")) or "")
        col = col_by_id.get(_key(_g(r, "ColumnID")) or "")
        if h is None or col is None:
            continue
        h["levels"].append(col["name"])
        if _g(r, "Name"):
            h["level_names"][str(_g(r, "Name"))] = col["name"]

    udfs = _load_udfs(qe)
    if udfs is None:
        snap.missing.append("USERDEFINEDFUNCTIONS")
        snap.warnings.append("user-defined functions unavailable — UDF calls not modelled")
    else:
        snap.functions = udfs
    return snap


def _load_udfs(qe: Any) -> Optional[List[Dict[str, Any]]]:
    fn = getattr(qe, "list_udfs", None)
    rows: Optional[List[Dict[str, Any]]] = None
    if callable(fn):
        try:
            res = fn()
            if isinstance(res, dict) and res.get("success"):
                rows = [_norm_row(r) for r in res.get("rows") or []]
        except Exception as e:  # noqa: BLE001
            logger.debug("list_udfs failed: %s", e)
    if rows is None:
        rows = _run_info(qe, "USERDEFINEDFUNCTIONS")
    if rows is None:
        return None
    out = []
    for r in rows:
        name = _g(r, "Name")
        if name:
            out.append(
                {"name": str(name), "expression": str(_g(r, "Expression", default="") or "")}
            )
    return out


# ---------------------------------------------------------------------------
# Facade adapter (duck-typed live_model_facade / TmdlModel shapes)
# ---------------------------------------------------------------------------
def snapshot_from_facade(model: Any) -> ModelSnapshot:
    """Adapt ``model.tables[].columns/.measures`` (+ optional extras) to a snapshot."""
    snap = ModelSnapshot(source="facade")
    for tbl in getattr(model, "tables", []) or []:
        tname = getattr(tbl, "name", None)
        if not tname:
            continue
        expr = getattr(tbl, "expression", None)
        snap.tables[str(tname)] = {
            "expression": expr,
            "calculated": bool(expr),
            "field_parameter": bool(expr) and "NAMEOF" in str(expr).upper(),
            "calc_group": bool(getattr(tbl, "calculation_items", None)),
            "detail_rows": None,
        }
        for col in getattr(tbl, "columns", []) or []:
            cname = getattr(col, "name", None)
            if not cname:
                continue
            cexpr = getattr(col, "expression", None)
            snap.columns.append(
                {
                    "table": str(tname),
                    "name": str(cname),
                    "type": "calculated" if cexpr else "data",
                    "expression": cexpr,
                    "sort_by": getattr(col, "sort_by_column", None),
                }
            )
        for m in getattr(tbl, "measures", []) or []:
            mname = getattr(m, "name", None)
            if not mname:
                continue
            snap.measures.append(
                {
                    "table": str(tname),
                    "name": str(mname),
                    "expression": getattr(m, "expression", "") or "",
                    "format_string_expr": getattr(m, "format_string_expression", None),
                    "detail_rows_expr": None,
                }
            )
        for ci in getattr(tbl, "calculation_items", []) or []:
            if getattr(ci, "name", None):
                snap.calc_items.append(
                    {
                        "table": str(tname),
                        "name": str(ci.name),
                        "expression": getattr(ci, "expression", "") or "",
                        "format_string_expr": getattr(ci, "format_string_expression", None),
                    }
                )
    for rel in getattr(model, "relationships", []) or []:
        ft, fc = getattr(rel, "from_table", ""), getattr(rel, "from_column", "")
        tt, tc = getattr(rel, "to_table", ""), getattr(rel, "to_column", "")
        if ft and fc and tt and tc:
            snap.relationships.append(
                {
                    "from_table": ft,
                    "from_column": fc,
                    "to_table": tt,
                    "to_column": tc,
                    "active": bool(getattr(rel, "is_active", True)),
                    "from_card": getattr(rel, "from_cardinality", None),
                    "to_card": getattr(rel, "to_cardinality", None),
                    "cross_filter": getattr(rel, "cross_filtering_behavior", None),
                }
            )
    for f in getattr(model, "functions", []) or []:
        if getattr(f, "name", None):
            snap.functions.append(
                {"name": f.name, "expression": getattr(f, "expression", "") or ""}
            )
    for r in getattr(model, "roles", []) or []:
        if getattr(r, "name", None):
            perms = [
                {"table": getattr(p, "table", ""), "filter": getattr(p, "filter", "") or ""}
                for p in getattr(r, "permissions", []) or []
            ]
            snap.roles.append({"name": r.name, "permissions": perms})
    return snap


def snapshot_from_model_data(model_data: Dict[str, Any]) -> ModelSnapshot:
    """Adapt the OFFLINE parsed TMDL model (``pbip_cache`` ``model_data`` dict) to a snapshot.

    Lets the graph / interaction audit run on a closed PBIP with no Desktop: tables (calculated
    partition source = table expression, field parameters via ``NAMEOF``), columns, measures
    (+ dynamic format-string definitions), calculation items, relationships (+ cardinality and
    cross-filter direction), roles, hierarchies and user-defined functions.
    """
    snap = ModelSnapshot(source="tmdl")
    for t in (model_data or {}).get("tables") or []:
        tname = t.get("name")
        if not tname:
            continue
        expr = None
        for part in t.get("partitions") or []:
            if str(part.get("type") or "").lower() == "calculated" and part.get("source"):
                expr = str(part["source"])
                break
        items = t.get("calculation_items") or []
        snap.tables[str(tname)] = {
            "expression": expr,
            "calculated": bool(expr),
            "field_parameter": bool(expr) and "NAMEOF" in expr.upper(),
            "calc_group": bool(t.get("is_calculation_group") or items),
            "detail_rows": None,
        }
        for c in t.get("columns") or []:
            if not c.get("name"):
                continue
            cexpr = c.get("expression") if c.get("is_calculated") or c.get("expression") else None
            snap.columns.append(
                {
                    "table": str(tname),
                    "name": str(c["name"]),
                    "type": "calculated" if cexpr else "data",
                    "expression": cexpr,
                    "sort_by": c.get("sort_by_column"),
                }
            )
        for m in t.get("measures") or []:
            if m.get("name"):
                snap.measures.append(
                    {
                        "table": str(tname),
                        "name": str(m["name"]),
                        "expression": str(m.get("expression") or ""),
                        "format_string_expr": m.get("format_string_definition"),
                        "detail_rows_expr": None,
                    }
                )
        for ci in items:
            if ci.get("name"):
                snap.calc_items.append(
                    {
                        "table": str(tname),
                        "name": str(ci["name"]),
                        "expression": str(ci.get("expression") or ""),
                        "format_string_expr": ci.get("format_string_definition"),
                    }
                )
        for h in t.get("hierarchies") or []:
            if not h.get("name"):
                continue
            levels = [lv for lv in (h.get("levels") or []) if isinstance(lv, dict)]
            snap.hierarchies.append(
                {
                    "table": str(tname),
                    "name": str(h["name"]),
                    "levels": [str(lv.get("column") or lv.get("name")) for lv in levels],
                    "level_names": {
                        str(lv.get("name")): str(lv.get("column") or lv.get("name"))
                        for lv in levels
                    },
                }
            )
    for r in (model_data or {}).get("relationships") or []:
        ft, fc = r.get("from_table"), r.get("from_column_name")
        tt, tc = r.get("to_table"), r.get("to_column_name")
        if ft and fc and tt and tc:
            snap.relationships.append(
                {
                    "from_table": ft,
                    "from_column": fc,
                    "to_table": tt,
                    "to_column": tc,
                    "active": r.get("is_active") is not False,
                    "from_card": r.get("from_cardinality"),
                    "to_card": r.get("to_cardinality"),
                    "cross_filter": r.get("cross_filtering_behavior"),
                }
            )
    for role in (model_data or {}).get("roles") or []:
        if role.get("name"):
            snap.roles.append(
                {
                    "name": str(role["name"]),
                    "permissions": [
                        {"table": p.get("table", ""), "filter": p.get("filter_expression") or ""}
                        for p in role.get("table_permissions") or []
                    ],
                }
            )
    for f in (model_data or {}).get("functions") or []:
        if f.get("name"):
            snap.functions.append(
                {"name": str(f["name"]), "expression": str(f.get("expression") or "")}
            )
    return snap


def as_snapshot(model: Any) -> Optional[ModelSnapshot]:
    if model is None:
        return None
    if isinstance(model, ModelSnapshot):
        return model
    return snapshot_from_facade(model)
