"""Data collection for Power BI model documentation.

2026-09-22 review:

- I-10: BPA really runs (it used to require the TMSL as a *string* while the
  exporter returns a dict, so the Word doc always fell back to two naming
  heuristics). The same TMSL snapshot feeds the new Calculation groups /
  Security / Power Query / Functions sections (I-30 idea 7).
- I-16: INFO.* fields are mapped through ``info_normalize`` — data types are
  names (not the column-kind int), calculated columns are labelled, columns and
  measures are sorted by their real name, relationship cardinality and
  cross-filter direction are filled, and the title uses the Desktop file name
  instead of the database GUID.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.analysis import info_normalize as infn
from core.comparison import model_narrative
from core.dax.dax_reference_parser import parse_dax_references

from .utils import (
    build_reference_index,
    format_edge,
    now_iso,
    pick,
    to_bool,
)


def _summarize_best_practices(
    bpa_payload: Optional[Dict[str, Any]], light_payload: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Summarize best practice analysis results."""
    result: Dict[str, Any] = {
        "source": None,
        "total_issues": 0,
        "by_severity": {},
        "violations": [],
        "notes": [],
    }
    if bpa_payload is not None:
        result["source"] = "bpa"
        result["violations"] = bpa_payload.get("violations", [])
        result["total_issues"] = len(result["violations"])
        counts: Dict[str, int] = {}
        for item in result["violations"]:
            severity = str(item.get("severity") or "INFO").title()
            counts[severity] = counts.get(severity, 0) + 1
        result["by_severity"] = counts
        result["notes"].extend(bpa_payload.get("notes") or [])
        if bpa_payload.get("coverage"):
            result["coverage"] = bpa_payload["coverage"]
    elif light_payload and light_payload.get("issues"):
        result["source"] = "lightweight"
        result["violations"] = light_payload.get("issues", [])
        result["total_issues"] = len(light_payload.get("issues", []))
        counts_light: Dict[str, int] = {}
        for item in light_payload.get("issues", []):
            severity = str(item.get("severity") or "info").title()
            counts_light[severity] = counts_light.get(severity, 0) + 1
        result["by_severity"] = counts_light
    return result


def fetch_tmsl_model(connection_state, exporter=None) -> Optional[Dict[str, Any]]:
    """The model's TMSL as a dict (query_executor first — it targets the
    connected database — then the exporter). ``None`` when AMO is unavailable."""
    from core.analysis.bpa_model import resolve_tmsl_model

    qe = getattr(connection_state, "query_executor", None)
    if qe is not None and hasattr(qe, "get_tmsl_definition"):
        try:
            res = qe.get_tmsl_definition()
            if isinstance(res, dict) and res.get("success"):
                model = resolve_tmsl_model(res.get("tmsl"))
                if model is not None:
                    return model
        except Exception:  # noqa: BLE001 — fall through to the exporter
            pass
    if exporter is not None:
        try:
            res = exporter.export_tmsl(include_full_model=True)
            if isinstance(res, dict) and res.get("success"):
                return resolve_tmsl_model(res.get("model") or res.get("tmsl"))
        except Exception:  # noqa: BLE001
            return None
    return None


def run_bpa(connection_state, model: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """BPA over *model* (dict TMSL) -> ``{violations, notes, coverage}`` or None."""
    bpa = getattr(connection_state, "bpa_analyzer", None)
    if not bpa or model is None or not hasattr(bpa, "run"):
        return None
    try:
        result = bpa.run(model, {"max_seconds": 60, "per_rule_max_ms": 3000})
    except Exception:  # noqa: BLE001 — the doc must still render
        return None
    return {
        "violations": [v.to_dict() for v in result.violations],
        "notes": [str(n) for n in result.notes],
        "coverage": result.coverage,
    }


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(str(v) for v in value)
    return str(value)


def model_extras(model: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculation groups, roles, Power Query sources, UDFs and field parameters."""
    out: Dict[str, Any] = {
        "calculation_groups": [],
        "roles": [],
        "power_query": [],
        "functions": [],
        "field_parameters": [],
    }
    if not model:
        return out
    fp_tables = {t["name"] for t in infn.tables_from_tmsl(model) if t.get("isFieldParameter")}
    out["field_parameters"] = sorted(fp_tables)
    for t in model.get("tables") or []:
        if not isinstance(t, dict):
            continue
        cg = t.get("calculationGroup")
        if isinstance(cg, dict):
            out["calculation_groups"].append(
                {
                    "name": _text(t.get("name")),
                    "precedence": int(cg.get("precedence") or 0),
                    "description": _text(t.get("description")),
                    "items": [
                        {
                            "name": _text(ci.get("name")),
                            "ordinal": ci.get("ordinal"),
                            "expression": _text(ci.get("expression")),
                            "format_string_expression": (
                                _text((ci.get("formatStringDefinition") or {}).get("expression"))
                                if isinstance(ci.get("formatStringDefinition"), dict)
                                else ""
                            ),
                        }
                        for ci in cg.get("calculationItems") or []
                        if isinstance(ci, dict)
                    ],
                }
            )
        for p in t.get("partitions") or []:
            src = p.get("source") if isinstance(p, dict) else None
            if isinstance(src, dict) and str(src.get("type") or "").lower() == "m":
                out["power_query"].append(
                    {
                        "table": _text(t.get("name")),
                        "partition": _text(p.get("name")),
                        "mode": _text(p.get("mode") or "import"),
                        "expression": _text(src.get("expression")),
                    }
                )
    for e in model.get("expressions") or []:
        if isinstance(e, dict) and str(e.get("kind") or "m").lower() == "m":
            out["power_query"].append(
                {
                    "table": "(shared expression)",
                    "partition": _text(e.get("name")),
                    "mode": "",
                    "expression": _text(e.get("expression")),
                }
            )
    for r in model.get("roles") or []:
        if not isinstance(r, dict):
            continue
        out["roles"].append(
            {
                "name": _text(r.get("name")),
                "model_permission": _text(r.get("modelPermission") or "read"),
                "members": len(r.get("members") or []),
                "table_permissions": [
                    {
                        "table": _text(tp.get("name")),
                        "filter_expression": _text(tp.get("filterExpression")),
                        "metadata_permission": _text(tp.get("metadataPermission") or ""),
                    }
                    for tp in r.get("tablePermissions") or []
                    if isinstance(tp, dict)
                ],
            }
        )
    for f in model.get("functions") or []:
        if isinstance(f, dict):
            out["functions"].append(
                {
                    "name": _text(f.get("name")),
                    "description": _text(f.get("description")),
                    "expression": _text(f.get("expression")),
                }
            )
    return out


def _display_name(connection_state, qe, summary: Dict[str, Any]) -> str:
    """Desktop file name > summary name > database name (a GUID on Desktop)."""
    try:
        cm = getattr(connection_state, "connection_manager", None)
        info = cm.get_instance_info() if cm else None
        if isinstance(info, dict):
            name = info.get("display_name") or info.get("database")
            if name and "Power BI Desktop (Port" not in str(name):
                return str(name)
    except Exception:  # noqa: BLE001
        pass
    try:
        name = qe._get_database_name()
    except Exception:  # noqa: BLE001
        name = None
    return str(summary.get("database_name") or name or "PowerBI_Model")


def collect_model_documentation(
    connection_state,
    *,
    include_hidden: bool = True,
    dependency_depth: int = 1,
    lightweight_best_practices: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Collect comprehensive documentation for a Power BI model.

    Returns a context dict: success, generated_at, database_name, summary,
    tables, measures, relationships, dependency_edges, best_practices, notes,
    narrative, calculation_groups, roles, power_query, functions,
    field_parameters.
    """
    if not connection_state or not connection_state.is_connected():
        return {"success": False, "error": "Power BI Desktop is not connected"}

    qe = getattr(connection_state, "query_executor", None)
    exporter = getattr(connection_state, "model_exporter", None)
    if not qe:
        return {"success": False, "error": "query_executor manager unavailable"}

    notes: List[str] = []
    summary: Dict[str, Any] = {}
    if exporter:
        try:
            summary = exporter.get_model_summary(qe)
        except Exception as exc:
            notes.append(f"Model summary unavailable: {exc}")
            summary = {"success": False}
    else:
        notes.append("Model exporter unavailable; summary limited")
        summary = {"success": False}

    db_name = _display_name(connection_state, qe, summary)

    tables_res = qe.execute_info_query("TABLES", top_n=5000)
    columns_res = qe.execute_info_query("COLUMNS", top_n=20000)
    measures_res = qe.execute_info_query("MEASURES", top_n=20000)
    relationships_res = qe.execute_info_query("RELATIONSHIPS", top_n=5000)

    tables_rows = tables_res.get("rows", []) if tables_res.get("success") else []
    columns_rows = columns_res.get("rows", []) if columns_res.get("success") else []
    measures_rows = measures_res.get("rows", []) if measures_res.get("success") else []
    relationships_rows = (
        relationships_res.get("rows", []) if relationships_res.get("success") else []
    )
    columns_rows = [c for c in columns_rows if infn.column_kind(c) != "RowNumber"]

    if not include_hidden:
        tables_rows = [r for r in tables_rows if not to_bool(pick(r, "IsHidden"))]
        columns_rows = [r for r in columns_rows if not to_bool(pick(r, "IsHidden"))]
        measures_rows = [r for r in measures_rows if not to_bool(pick(r, "IsHidden"))]

    columns_by_table: Dict[str, List[Dict[str, Any]]] = {}
    for col in columns_rows:
        table = str(pick(col, "Table", "TableName", default=""))
        columns_by_table.setdefault(table, []).append(col)

    measures_by_table: Dict[str, List[Dict[str, Any]]] = {}
    for meas in measures_rows:
        table = str(pick(meas, "Table", "TableName", default=""))
        measures_by_table.setdefault(table, []).append(meas)

    reference_index = build_reference_index(measures_rows, columns_rows)

    tables: List[Dict[str, Any]] = []
    for table in tables_rows:
        t_name = str(pick(table, "Name", "TableName", default="Unknown"))
        table_entry = {
            "name": t_name,
            "description": pick(table, "Description", default=""),
            "hidden": to_bool(pick(table, "IsHidden")),
            "row_count": pick(table, "RowCount", default=None),
            "columns": [],
            "measures": [],
        }

        for col in sorted(
            columns_by_table.get(t_name, []), key=lambda r: infn.column_name(r).lower()
        ):
            col_name = infn.column_name(col).strip() or "Unnamed Column"
            table_entry["columns"].append(
                {
                    "name": col_name,
                    "data_type": infn.column_data_type(col),
                    "description": pick(col, "Description", default=""),
                    "hidden": to_bool(pick(col, "IsHidden")),
                    "summarize_by": pick(col, "SummarizeBy", default=None),
                    "type": infn.column_kind(col),
                }
            )

        for meas in sorted(
            measures_by_table.get(t_name, []),
            key=lambda r: str(pick(r, "Name", default="")).lower(),
        ):
            expr = pick(meas, "Expression", default="") or ""
            refs = (
                parse_dax_references(expr, reference_index)
                if expr
                else {"tables": [], "columns": [], "measures": []}
            )
            deps_cols = [f"{tbl}[{col}]" for tbl, col in refs.get("columns", [])]
            deps_meas = [f"{tbl}.{name}" if tbl else name for tbl, name in refs.get("measures", [])]
            table_entry["measures"].append(
                {
                    "name": str(pick(meas, "Name", default="")) or "Unnamed Measure",
                    "description": pick(meas, "Description", default=""),
                    "expression": expr,
                    "format_string": pick(meas, "FormatString", default=""),
                    "display_folder": pick(meas, "DisplayFolder", default=""),
                    "hidden": to_bool(pick(meas, "IsHidden")),
                    "dependencies": {
                        "columns": deps_cols,
                        "measures": deps_meas,
                        "tables": refs.get("tables", []),
                    },
                }
            )

        tables.append(table_entry)

    all_measures: List[Dict[str, Any]] = []
    dependency_edges: List[Dict[str, Any]] = []
    for table in tables:
        for meas in table["measures"]:
            source = f"{table['name']}.{meas['name']}"
            dependency_edges.append(
                format_edge(source, meas["dependencies"]["measures"], "measure")
            )
            dependency_edges.append(format_edge(source, meas["dependencies"]["columns"], "column"))
            all_measures.append(
                {
                    "table": table["name"],
                    "name": meas["name"],
                    "description": meas["description"],
                    "expression": meas["expression"],
                    "dependencies": meas["dependencies"],
                    "hidden": meas["hidden"],
                }
            )

    relationships: List[Dict[str, Any]] = []
    for rel in relationships_rows:
        r = infn.normalize_relationship(rel)
        relationships.append(
            {
                "from_table": r["fromTable"],
                "from_column": r["fromColumn"],
                "to_table": r["toTable"],
                "to_column": r["toColumn"],
                "is_active": r["isActive"],
                "cardinality": f"{r['fromCardinality']}:{r['toCardinality']}",
                "direction": r["crossFilteringBehavior"],
            }
        )

    try:
        narrative = model_narrative.generate_narrative(
            summary, {"relationships": {"count": len(relationships)}}
        )
    except Exception:
        narrative = {"success": False}

    tmsl_model = fetch_tmsl_model(connection_state, exporter)
    if tmsl_model is None:
        notes.append(
            "TMSL unavailable (AMO): BPA and the calc-group/RLS/Power Query sections are skipped."
        )
    bpa_payload = run_bpa(connection_state, tmsl_model)
    best_practices = _summarize_best_practices(bpa_payload, lightweight_best_practices)
    extras = model_extras(tmsl_model)

    context = {
        "success": True,
        "generated_at": now_iso(),
        "database_name": db_name,
        "summary": summary,
        "tables": tables,
        "measures": all_measures,
        "relationships": relationships,
        "dependency_edges": dependency_edges,
        "best_practices": best_practices,
        "notes": notes,
        "narrative": narrative,
        **extras,
    }
    return context
