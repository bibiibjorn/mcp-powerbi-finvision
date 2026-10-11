"""TMSL -> ModelDiffer input (2026-09-22 review I-07).

The live compare used ``ModelExporter.export_tmdl_structure`` whose keys never
matched what :class:`ModelDiffer` compares (``cardinality: "Many:One"`` vs
``from_cardinality``; ``filter_expression`` vs ``expression``) and which omitted
calculated-column DAX, partitions/M, calculation groups, descriptions,
annotations, perspectives, KPIs, UDFs and role members — so ``06 compare``
reported "0 changes" when bidirectional filtering was switched on or an RLS
filter was rewritten.

This module turns the full ``JsonSerializer.SerializeObject(Model)`` TMSL (the
same snapshot BPA uses) into the differ's canonical snake_case shape, filling
the TOM defaults TMSL omits (``fromCardinality`` -> ``Many``,
``crossFilteringBehavior`` -> ``OneDirection`` …) so an absent key and its
default compare equal.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.analysis.info_normalize import (
    CARDINALITY,
    CROSS_FILTER,
    SECURITY_FILTER,
    enum_name,
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(str(v) for v in value)
    return str(value)


def _pascal(value: Any, default: str) -> str:
    text = _text(value).strip()
    if not text:
        return default
    return text[0].upper() + text[1:]


def _annotations(obj: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for a in obj.get("annotations") or []:
        if isinstance(a, dict) and a.get("name"):
            out.append({"name": str(a["name"]), "value": _text(a.get("value"))})
    return out


def _column(c: Dict[str, Any]) -> Dict[str, Any]:
    ctype = str(c.get("type") or "data").lower()
    return {
        "name": _text(c.get("name")),
        "kind": {"calculated": "Calculated", "calculatedtablecolumn": "CalculatedTableColumn"}.get(
            ctype, "Data"
        ),
        "data_type": _pascal(c.get("dataType"), "Unknown"),
        "is_calculated": ctype == "calculated",
        "expression": _text(c.get("expression")) or None,
        "source_column": c.get("sourceColumn"),
        "description": _text(c.get("description")) or None,
        "display_folder": c.get("displayFolder") or None,
        "format_string": c.get("formatString") or None,
        "data_category": c.get("dataCategory") or None,
        "summarize_by": _pascal(c.get("summarizeBy"), "Default"),
        "sort_by_column": c.get("sortByColumn") or None,
        "is_key": bool(c.get("isKey", False)),
        "is_hidden": bool(c.get("isHidden", False)),
        "lineage_tag": c.get("lineageTag"),
        "annotations": _annotations(c),
    }


def _measure(m: Dict[str, Any]) -> Dict[str, Any]:
    fsd = m.get("formatStringDefinition")
    kpi = m.get("kpi") if isinstance(m.get("kpi"), dict) else None
    return {
        "name": _text(m.get("name")),
        "expression": _text(m.get("expression")),
        "format_string": m.get("formatString") or None,
        "format_string_definition": _text(fsd.get("expression")) if isinstance(fsd, dict) else None,
        "display_folder": m.get("displayFolder") or None,
        "description": _text(m.get("description")) or None,
        "is_hidden": bool(m.get("isHidden", False)),
        "data_category": m.get("dataCategory") or None,
        "lineage_tag": m.get("lineageTag"),
        "annotations": _annotations(m),
        "kpi": (
            {
                k: _text(kpi.get(k))
                for k in ("targetExpression", "statusExpression", "trendExpression")
                if kpi.get(k) is not None
            }
            if kpi
            else None
        ),
    }


def _partition(p: Dict[str, Any]) -> Dict[str, Any]:
    src = p.get("source") if isinstance(p.get("source"), dict) else {}
    text = src.get("expression")
    if text is None:
        text = src.get("query")
    return {
        "name": _text(p.get("name")),
        "mode": _pascal(p.get("mode"), "Default"),
        "type": _pascal(src.get("type"), "Query"),
        "source": _text(text),
        "data_source": src.get("dataSource"),
    }


def _table(t: Dict[str, Any]) -> Dict[str, Any]:
    cg = t.get("calculationGroup") if isinstance(t.get("calculationGroup"), dict) else None
    parts = [p for p in t.get("partitions") or [] if isinstance(p, dict)]
    first_type = str(((parts[0].get("source") or {}) if parts else {}).get("type") or "").lower()
    return {
        "name": _text(t.get("name")),
        "description": _text(t.get("description")) or None,
        "is_hidden": bool(t.get("isHidden", False)),
        "data_category": t.get("dataCategory") or None,
        "lineage_tag": t.get("lineageTag"),
        "is_calculation_group": cg is not None,
        "is_calculated_table": first_type == "calculated",
        "calculation_group_precedence": int(cg.get("precedence") or 0) if cg else None,
        "calculation_items": [
            {
                "name": _text(ci.get("name")),
                "expression": _text(ci.get("expression")),
                "ordinal": ci.get("ordinal"),
                "format_string_definition": (
                    _text((ci.get("formatStringDefinition") or {}).get("expression"))
                    if isinstance(ci.get("formatStringDefinition"), dict)
                    else None
                ),
                "description": _text(ci.get("description")) or None,
                "annotations": _annotations(ci),
            }
            for ci in (cg or {}).get("calculationItems") or []
            if isinstance(ci, dict)
        ],
        "columns": [
            _column(c)
            for c in t.get("columns") or []
            if isinstance(c, dict) and str(c.get("type") or "").lower() != "rownumber"
        ],
        "measures": [_measure(m) for m in t.get("measures") or [] if isinstance(m, dict)],
        "hierarchies": [
            {
                "name": _text(h.get("name")),
                "levels": [
                    {"name": _text(lv.get("name")), "column": lv.get("column")}
                    for lv in h.get("levels") or []
                    if isinstance(lv, dict)
                ],
            }
            for h in t.get("hierarchies") or []
            if isinstance(h, dict)
        ],
        "partitions": [_partition(p) for p in parts],
        "annotations": _annotations(t),
    }


def _relationship(r: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": _text(r.get("name")),
        "from_table": _text(r.get("fromTable")),
        "from_column": _text(r.get("fromColumn")),
        "to_table": _text(r.get("toTable")),
        "to_column": _text(r.get("toColumn")),
        "from_cardinality": enum_name(CARDINALITY, r.get("fromCardinality"), "Many"),
        "to_cardinality": enum_name(CARDINALITY, r.get("toCardinality"), "One"),
        "cross_filtering_behavior": enum_name(
            CROSS_FILTER, r.get("crossFilteringBehavior"), "OneDirection"
        ),
        "security_filtering_behavior": enum_name(
            SECURITY_FILTER, r.get("securityFilteringBehavior"), "OneDirection"
        ),
        "is_active": r.get("isActive", True) is not False,
        "rely_on_referential_integrity": bool(r.get("relyOnReferentialIntegrity", False)),
        "annotations": _annotations(r),
    }


def _role(r: Dict[str, Any]) -> Dict[str, Any]:
    perms = []
    for p in r.get("tablePermissions") or []:
        if not isinstance(p, dict):
            continue
        expr = _text(p.get("filterExpression"))
        perms.append(
            {
                "table": _text(p.get("name")),
                "expression": expr,
                "filter_expression": expr,
                "metadata_permission": _pascal(p.get("metadataPermission"), "Default"),
                "column_permissions": sorted(
                    f"{_text(cp.get('name'))}:{_pascal(cp.get('metadataPermission'), 'Default')}"
                    for cp in p.get("columnPermissions") or []
                    if isinstance(cp, dict)
                ),
            }
        )
    return {
        "name": _text(r.get("name")),
        "description": _text(r.get("description")) or None,
        "model_permission": _pascal(r.get("modelPermission"), "Read"),
        "members": sorted(
            _text(m.get("memberName")) for m in r.get("members") or [] if isinstance(m, dict)
        ),
        "table_permissions": perms,
        "annotations": _annotations(r),
    }


def _perspective(p: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": _text(p.get("name")),
        "tables": [
            {
                "name": _text(pt.get("name")),
                "columns": [
                    _text(c.get("name")) for c in pt.get("columns") or [] if isinstance(c, dict)
                ],
                "measures": [
                    _text(m.get("name")) for m in pt.get("measures") or [] if isinstance(m, dict)
                ],
                "hierarchies": [
                    _text(h.get("name")) for h in pt.get("hierarchies") or [] if isinstance(h, dict)
                ],
            }
            for pt in p.get("tables") or []
            if isinstance(pt, dict)
        ],
    }


_MODEL_PROPERTIES = (
    ("culture", "culture", None),
    ("default_mode", "defaultMode", "Import"),
    ("discourage_implicit_measures", "discourageImplicitMeasures", False),
    ("value_filter_behavior", "valueFilterBehavior", "Automatic"),
    ("default_power_bi_data_source_version", "defaultPowerBIDataSourceVersion", None),
    ("source_query_culture", "sourceQueryCulture", None),
)


def normalize_tmsl(
    model: Dict[str, Any],
    *,
    name: Optional[str] = None,
    compatibility_level: Optional[int] = None,
) -> Dict[str, Any]:
    """Canonical differ input from a TMSL model object."""
    props: Dict[str, Any] = {}
    for key, tmsl_key, default in _MODEL_PROPERTIES:
        val = model.get(tmsl_key, default)
        props[key] = _pascal(val, str(default)) if isinstance(val, str) else val
    return {
        "model": {
            "name": name or _text(model.get("name")) or "Model",
            "compatibility_level": compatibility_level,
            "properties": props,
        },
        "tables": [_table(t) for t in model.get("tables") or [] if isinstance(t, dict)],
        "relationships": [
            _relationship(r) for r in model.get("relationships") or [] if isinstance(r, dict)
        ],
        "roles": [_role(r) for r in model.get("roles") or [] if isinstance(r, dict)],
        "perspectives": [
            _perspective(p) for p in model.get("perspectives") or [] if isinstance(p, dict)
        ],
        "cultures": [
            _text(c.get("name")) for c in model.get("cultures") or [] if isinstance(c, dict)
        ],
        "expressions": [
            {
                "name": _text(e.get("name")),
                "kind": _pascal(e.get("kind"), "M"),
                "expression": _text(e.get("expression")),
                "description": _text(e.get("description")) or None,
            }
            for e in model.get("expressions") or []
            if isinstance(e, dict)
        ],
        "functions": [
            {
                "name": _text(f.get("name")),
                "expression": _text(f.get("expression")),
                "description": _text(f.get("description")) or None,
                "is_hidden": bool(f.get("isHidden", False)),
            }
            for f in model.get("functions") or []
            if isinstance(f, dict)
        ],
    }
