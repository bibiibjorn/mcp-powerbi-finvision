"""DAX-bearing objects and schema facts from a TMSL model snapshot (review D-16 / D-05 / D-09).

``lint_model`` used to analyze only measure expressions (and UDFs). The same TMSL snapshot BPA
reads (``query_executor.get_tmsl_definition()``) carries every other expression the engine
evaluates, and the schema facts the rules need (storage mode, relationship table names, the
tables an RLS role secures, calculation-group / field-parameter tables):

- measure ``formatStringDefinition`` and KPI expressions;
- calculation items and their format-string expressions;
- calculated columns and calculated tables;
- RLS ``filterExpression``.

Pure functions over plain dicts - no connection, no CLR.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core.dax.analyzer.models import ModelSchema


@dataclass(frozen=True)
class DaxObject:
    """One DAX expression to lint."""

    kind: str  # format_string | calc_item | calc_item_format_string | calc_column | calc_table | rls | kpi
    table: str
    name: str
    expression: str


def _expr(value: Any) -> str:
    """TMSL expressions are a string or a list of lines."""
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(str(x) for x in value)
    if isinstance(value, dict):
        return _expr(value.get("expression"))
    return str(value)


def resolve_model(tmsl: Any) -> Optional[Dict[str, Any]]:
    """The ``model`` dict of a TMSL document (string / bytes / dict, with or without the
    ``createOrReplace`` wrapper)."""
    if isinstance(tmsl, (bytes, bytearray)):
        tmsl = tmsl.decode("utf-8", errors="replace")
    if isinstance(tmsl, str):
        try:
            tmsl = json.loads(tmsl)
        except ValueError:
            return None
    if not isinstance(tmsl, dict):
        return None
    if "tables" in tmsl:
        return tmsl
    for key in ("model",):
        if isinstance(tmsl.get(key), dict):
            return tmsl[key]
    inner = tmsl.get("createOrReplace") or tmsl.get("database")
    if isinstance(inner, dict):
        db = inner.get("database") if isinstance(inner.get("database"), dict) else inner
        if isinstance(db.get("model"), dict):
            return db["model"]
    return None


def extract_dax_objects(model: Dict[str, Any]) -> List[DaxObject]:
    """Every non-measure DAX expression of *model* (measure BODIES are linted separately)."""
    out: List[DaxObject] = []
    for t in model.get("tables", []) or []:
        tname = str(t.get("name", ""))
        for m in t.get("measures", []) or []:
            mname = str(m.get("name", ""))
            fsd = _expr(m.get("formatStringDefinition"))
            if fsd.strip():
                out.append(DaxObject("format_string", tname, mname, fsd))
            kpi = m.get("kpi")
            if isinstance(kpi, dict):
                for key in ("targetExpression", "statusExpression", "trendExpression"):
                    e = _expr(kpi.get(key))
                    if e.strip():
                        out.append(DaxObject("kpi", tname, f"{mname}.{key}", e))
        for c in t.get("columns", []) or []:
            if str(c.get("type", "")).lower() == "calculated":
                e = _expr(c.get("expression"))
                if e.strip():
                    out.append(DaxObject("calc_column", tname, str(c.get("name", "")), e))
        for p in t.get("partitions", []) or []:
            src = p.get("source")
            if isinstance(src, dict) and str(src.get("type", "")).lower() == "calculated":
                e = _expr(src.get("expression"))
                if e.strip():
                    out.append(DaxObject("calc_table", tname, tname, e))
        cg = t.get("calculationGroup")
        if isinstance(cg, dict):
            for item in cg.get("calculationItems", []) or []:
                iname = str(item.get("name", ""))
                e = _expr(item.get("expression"))
                if e.strip():
                    out.append(DaxObject("calc_item", tname, iname, e))
                f = _expr(item.get("formatStringDefinition"))
                if f.strip():
                    out.append(DaxObject("calc_item_format_string", tname, iname, f))
    for role in model.get("roles", []) or []:
        rname = str(role.get("name", ""))
        for tp in role.get("tablePermissions", []) or []:
            e = _expr(tp.get("filterExpression"))
            if e.strip():
                out.append(DaxObject("rls", str(tp.get("name", "")), rname, e))
    return out


def schema_from_tmsl(model: Dict[str, Any]) -> ModelSchema:
    """A ``ModelSchema`` carrying everything the TMSL snapshot knows."""
    tables: List[Dict[str, Any]] = []
    measures: List[Dict[str, Any]] = []
    columns: List[Dict[str, Any]] = []
    for t in model.get("tables", []) or []:
        tname = str(t.get("name", ""))
        parts = t.get("partitions", []) or []
        modes = {str(p.get("mode", "")).lower() for p in parts if p.get("mode")}
        mode = next(iter(modes)) if len(modes) == 1 else ""
        is_fp = any(
            isinstance(p.get("source"), dict)
            and str(p["source"].get("type", "")).lower() == "calculated"
            and "NAMEOF(" in _expr(p["source"].get("expression")).upper()
            for p in parts
        )
        tables.append(
            {
                "name": tname,
                "mode": mode,
                "is_calculation_group": isinstance(t.get("calculationGroup"), dict),
                "is_field_parameter": is_fp,
            }
        )
        for m in t.get("measures", []) or []:
            measures.append({"name": m.get("name", ""), "table": tname})
        for c in t.get("columns", []) or []:
            columns.append({"name": c.get("name", ""), "table": tname})
    roles: List[Dict[str, Any]] = []
    for r in model.get("roles", []) or []:
        roles.append(
            {
                "name": r.get("name", ""),
                "table_filters": {
                    str(tp.get("name", "")): _expr(tp.get("filterExpression"))
                    for tp in r.get("tablePermissions", []) or []
                    if _expr(tp.get("filterExpression")).strip()
                },
            }
        )
    return ModelSchema.from_model(
        {
            "tables": tables,
            "measures": measures,
            "columns": columns,
            "relationships": list(model.get("relationships", []) or []),
            "roles": roles,
        }
    )
