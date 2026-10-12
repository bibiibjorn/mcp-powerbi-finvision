"""
Model Exporter for PBIXRay MCP Server
Exports models in TMSL, TMDL, and documentation formats
"""

import json
import logging
import os
from typing import Dict, Any, Optional
from datetime import datetime
from core.execution.dmv_helper import get_field_value
from core.utilities.type_conversions import safe_bool

logger = logging.getLogger(__name__)

# Try to load AMO
AMO_AVAILABLE = False
AMOServer = None

try:
    from core.infrastructure.dll_paths import load_amo_assemblies

    load_amo_assemblies()
    from Microsoft.AnalysisServices.Tabular import Server as AMOServer  # type: ignore  # noqa: F401

    AMO_AVAILABLE = True
    logger.info("AMO available for model export")

except Exception as e:
    logger.warning(f"AMO not available for export: {e}")


class ModelExporter:
    """Export Power BI models in various formats."""

    def __init__(self, connection):
        """Initialize with ADOMD connection."""
        self.connection = connection

    def export_tmsl(self, include_full_model: bool = False) -> Dict[str, Any]:
        """
        Export model as TMSL JSON.

        Args:
            include_full_model: If False, returns summary only. If True, includes full model (may be large).
        """
        if not AMO_AVAILABLE:
            return {"success": False, "error": "AMO not available for TMSL export"}

        # C-33: resolve the connected catalog (DBSCHEMA_CATALOGS) instead of
        # Databases[0], which picks the wrong model on multi-model instances.
        from core.operations.amo_helpers import get_server_db_model
        from core.infrastructure.connection_state import connection_state

        server, db, _model, err = get_server_db_model(connection_state)
        if err or db is None:
            return {"success": False, "error": err or "No database found"}
        try:

            # Get TMSL as JSON using JsonSerializer (with options if available)
            tmsl_json = None
            try:
                from Microsoft.AnalysisServices.Tabular import JsonSerializer, JsonSerializeOptions  # type: ignore

                options = JsonSerializeOptions()
                try:
                    options.IgnoreInferredObjects = False
                    options.IgnoreInferredProperties = False
                    options.IgnoreTimestamps = True
                except Exception:
                    pass
                tmsl_json = JsonSerializer.SerializeObject(db.Model, options)
            except Exception:
                from Microsoft.AnalysisServices.Tabular import JsonSerializer  # type: ignore

                tmsl_json = JsonSerializer.SerializeObject(db.Model)
            tmsl_data = json.loads(tmsl_json)

            # Calculate statistics
            stats = {
                "tables": len(tmsl_data.get("tables", [])),
                "relationships": len(tmsl_data.get("relationships", [])),
                "cultures": len(tmsl_data.get("cultures", [])),
                "roles": len(tmsl_data.get("roles", [])),
                "expressions": len(tmsl_data.get("expressions", [])),
            }

            # Count measures across all tables
            measure_count = 0
            column_count = 0
            for table in tmsl_data.get("tables", []):
                measure_count += len(table.get("measures", []))
                column_count += len(table.get("columns", []))

            stats["measures"] = measure_count
            stats["columns"] = column_count

            # Build result
            result = {
                "success": True,
                "format": "TMSL",
                "database_name": db.Name,
                "compatibility_level": db.CompatibilityLevel,
                "export_timestamp": datetime.now().isoformat(),
                "statistics": stats,
            }

            # Only include full model if requested
            if include_full_model:
                result["model"] = tmsl_data
                result["note"] = "Full model included - may be large"
            else:
                # Include lightweight summary
                result["summary"] = {
                    "table_names": [t.get("name") for t in tmsl_data.get("tables", [])],
                    "note": "Use include_full_model=true to get complete TMSL",
                }

            logger.info(f"Exported TMSL: {stats['tables']} tables, {stats['measures']} measures")
            return result

        except Exception as e:
            logger.error(f"TMSL export error: {e}")
            return {"success": False, "error": str(e)}
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def export_tmdl_structure(
        self, export_to_file: bool = True, output_path: str = None
    ) -> Dict[str, Any]:
        """
        Export model structure as TMDL-style hierarchy (includes all DAX expressions).

        Args:
            export_to_file: If True, writes TMDL to a JSON file and returns file path (recommended for large models).
                          If False, returns full TMDL structure in response (may be too large for MCP protocol).
            output_path: Optional custom output path. If None, uses exports/tmdl_exports/
        """
        if not AMO_AVAILABLE:
            return {"success": False, "error": "AMO not available for TMDL export"}

        # C-33: resolve the connected catalog (DBSCHEMA_CATALOGS) instead of
        # Databases[0], which picks the wrong model on multi-model instances.
        from core.operations.amo_helpers import get_server_db_model
        from core.infrastructure.connection_state import connection_state

        server, db, _model, err = get_server_db_model(connection_state)
        if err or db is None:
            return {"success": False, "error": err or "No database found"}
        try:
            model = db.Model

            # Build TMDL structure
            tmdl = {
                "model": {
                    "name": model.Name if hasattr(model, "Name") else db.Name,
                    "compatibility_level": db.CompatibilityLevel,
                    "default_mode": (
                        str(model.DefaultMode) if hasattr(model, "DefaultMode") else "Import"
                    ),
                },
                "tables": {},
                "relationships": [],
                "roles": [],
                "cultures": [],
            }

            # Export tables
            for table in model.Tables:
                table_data = {
                    "name": table.Name,
                    "is_hidden": table.IsHidden,
                    "columns": [],
                    "measures": [],
                    "hierarchies": [],
                }

                # Columns
                for col in table.Columns:
                    table_data["columns"].append(
                        {
                            "name": col.Name,
                            "data_type": str(col.DataType),
                            "is_hidden": col.IsHidden,
                            "source_column": (
                                col.SourceColumn if hasattr(col, "SourceColumn") else None
                            ),
                        }
                    )

                # Measures
                for measure in table.Measures:
                    measure_data = {
                        "name": measure.Name,
                        "expression": measure.Expression,  # Always include DAX expression
                        "display_folder": measure.DisplayFolder,
                        "format_string": (
                            measure.FormatString if hasattr(measure, "FormatString") else None
                        ),
                        "is_hidden": measure.IsHidden if hasattr(measure, "IsHidden") else False,
                    }
                    table_data["measures"].append(measure_data)

                # Hierarchies
                for hier in table.Hierarchies:
                    levels = []
                    for level in hier.Levels:
                        levels.append(
                            {
                                "name": level.Name,
                                "column": level.Column.Name if level.Column else None,
                            }
                        )
                    table_data["hierarchies"].append({"name": hier.Name, "levels": levels})

                tmdl["tables"][table.Name] = table_data

            # Export relationships
            for rel in model.Relationships:
                tmdl["relationships"].append(
                    {
                        "name": (
                            rel.Name
                            if hasattr(rel, "Name")
                            else f"{rel.FromTable.Name}_{rel.ToTable.Name}"
                        ),
                        "from_table": rel.FromTable.Name,
                        "from_column": rel.FromColumn.Name,
                        "to_table": rel.ToTable.Name,
                        "to_column": rel.ToColumn.Name,
                        "is_active": rel.IsActive,
                        "cross_filter_direction": str(rel.CrossFilteringBehavior),
                        "cardinality": str(rel.FromCardinality) + ":" + str(rel.ToCardinality),
                    }
                )

            # Export roles
            if hasattr(model, "Roles"):
                for role in model.Roles:
                    role_data = {"name": role.Name, "table_permissions": []}
                    for perm in role.TablePermissions:
                        role_data["table_permissions"].append(
                            {
                                "table": perm.Table.Name,
                                "filter_expression": (
                                    perm.FilterExpression
                                    if hasattr(perm, "FilterExpression")
                                    else None
                                ),
                            }
                        )
                    tmdl["roles"].append(role_data)

            # Calculate statistics
            statistics = {
                "tables": len(tmdl["tables"]),
                "relationships": len(tmdl["relationships"]),
                "roles": len(tmdl["roles"]),
                "total_measures": sum(len(t["measures"]) for t in tmdl["tables"].values()),
                "total_columns": sum(len(t["columns"]) for t in tmdl["tables"].values()),
            }

            # If export_to_file is True, write to file and return path
            if export_to_file:
                if output_path is None:
                    # Use default exports directory
                    export_dir = os.path.join(os.getcwd(), "exports", "tmdl_exports")
                    os.makedirs(export_dir, exist_ok=True)
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    model_name_safe = db.Name.replace(" ", "_").replace("/", "_")
                    output_path = os.path.join(
                        export_dir, f"{model_name_safe}_tmdl_{timestamp}.json"
                    )
                else:
                    os.makedirs(os.path.dirname(output_path), exist_ok=True)

                # Write full TMDL to file
                full_export = {
                    "format": "TMDL",
                    "database_name": db.Name,
                    "tmdl": tmdl,
                    "export_timestamp": datetime.now().isoformat(),
                    "statistics": statistics,
                }

                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(full_export, f, indent=2, ensure_ascii=False)

                logger.info(
                    f"Exported TMDL structure to file: {output_path} ({statistics['tables']} tables, {statistics['total_measures']} measures)"
                )

                # Calculate file size for user info
                file_size_bytes = os.path.getsize(output_path)
                file_size_mb = file_size_bytes / (1024 * 1024)

                return {
                    "success": True,
                    "format": "TMDL",
                    "database_name": db.Name,
                    "export_file": output_path,
                    "export_timestamp": datetime.now().isoformat(),
                    "statistics": statistics,
                    "file_size_mb": round(file_size_mb, 2),
                    "message": f'✅ TMDL structure exported to file ({statistics["tables"]} tables, {statistics["total_measures"]} measures, {round(file_size_mb, 1)} MB)',
                    "note": "To read the exported data, use the standard file reading capabilities to open: "
                    + output_path,
                }
            else:
                # Return full structure in response (may be too large!)
                result = {
                    "success": True,
                    "format": "TMDL",
                    "database_name": db.Name,
                    "tmdl": tmdl,
                    "export_timestamp": datetime.now().isoformat(),
                    "statistics": statistics,
                }

                logger.info(
                    f"Exported TMDL structure: {statistics['tables']} tables (in-memory, not file)"
                )
                return result

        except Exception as e:
            logger.error(f"TMDL export error: {e}")
            return {"success": False, "error": str(e)}
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def generate_documentation(self, query_executor) -> Dict[str, Any]:
        """Generate markdown documentation for the model."""
        try:
            doc_lines = []
            doc_lines.append("# Power BI Model Documentation")
            doc_lines.append(f"\n*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n")

            # Model overview
            tables_result = query_executor.execute_info_query("TABLES", top_n=5000)
            measures_result = query_executor.execute_info_query("MEASURES", top_n=5000)
            rels_result = query_executor.execute_info_query("RELATIONSHIPS", top_n=5000)

            if tables_result.get("success"):
                tables = tables_result["rows"]
                measures = measures_result.get("rows", [])
                rels = rels_result.get("rows", [])

                doc_lines.append("## Model Overview\n")
                doc_lines.append(f"- **Tables**: {len(tables)}")
                doc_lines.append(f"- **Measures**: {len(measures)}")
                doc_lines.append(f"- **Relationships**: {len(rels)}")
                doc_lines.append("")

                # Tables section
                doc_lines.append("## Tables\n")
                for table in tables:
                    table_name = table.get("Name", "Unknown")
                    is_hidden = table.get("IsHidden", False)
                    hidden_tag = " *(hidden)*" if is_hidden else ""

                    doc_lines.append(f"### {table_name}{hidden_tag}\n")

                    # Get columns for this table
                    cols_result = query_executor.execute_info_query(
                        "COLUMNS", table_name=table_name
                    )
                    if cols_result.get("success"):
                        doc_lines.append("**Columns:**")
                        for col in cols_result["rows"]:
                            col_name = col.get("Name")
                            col_type = col.get("DataType", "Unknown")
                            col_hidden = " *(hidden)*" if col.get("IsHidden") else ""
                            doc_lines.append(f"- `{col_name}` ({col_type}){col_hidden}")
                        doc_lines.append("")

                    # Get measures for this table
                    table_measures = [m for m in measures if m.get("Table") == table_name]
                    if table_measures:
                        doc_lines.append("**Measures:**")
                        for measure in table_measures[:10]:  # Limit to first 10
                            m_name = measure.get("Name")
                            m_hidden = " *(hidden)*" if measure.get("IsHidden") else ""
                            doc_lines.append(f"- `{m_name}`{m_hidden}")
                        if len(table_measures) > 10:
                            doc_lines.append(f"  *(... and {len(table_measures) - 10} more)*")
                        doc_lines.append("")

                # Relationships section
                if rels:
                    doc_lines.append("## Relationships\n")
                    doc_lines.append("| From | To | Active | Cardinality | Direction |")
                    doc_lines.append("|------|-----|--------|-------------|-----------|")
                    for rel in rels:
                        from_str = f"{rel.get('FromTable')}[{rel.get('FromColumn')}]"
                        to_str = f"{rel.get('ToTable')}[{rel.get('ToColumn')}]"
                        active = "Yes" if rel.get("IsActive") else "No"
                        cardinality = rel.get("Cardinality", "Unknown")
                        direction = rel.get("CrossFilterDirection", "Single")
                        doc_lines.append(
                            f"| {from_str} | {to_str} | {active} | {cardinality} | {direction} |"
                        )
                    doc_lines.append("")

                # Key measures section
                doc_lines.append("## Key Measures\n")
                visible_measures = [m for m in measures if not m.get("IsHidden")][:20]
                for measure in visible_measures:
                    m_name = measure.get("Name")
                    m_table = measure.get("Table")
                    m_expr = measure.get("Expression", "")

                    doc_lines.append(f"### {m_table}.{m_name}\n")
                    doc_lines.append("```dax")
                    doc_lines.append(m_expr[:300])  # Truncate long expressions
                    if len(m_expr) > 300:
                        doc_lines.append("...")
                    doc_lines.append("```\n")

            markdown = "\n".join(doc_lines)

            return {
                "success": True,
                "format": "markdown",
                "documentation": markdown,
                "line_count": len(doc_lines),
                "export_timestamp": datetime.now().isoformat(),
            }

        except Exception as e:
            logger.error(f"Documentation generation error: {e}")
            return {"success": False, "error": str(e)}

    def compare_models(self, tmsl_reference: Dict) -> Dict[str, Any]:
        """Compare current model with reference TMSL."""
        try:
            current = self.export_tmsl(include_full_model=True)
            if not current.get("success"):
                return current

            current_model = current["model"]
            ref_model = tmsl_reference.get("model", tmsl_reference)

            changes = {
                "tables_added": [],
                "tables_removed": [],
                "tables_modified": [],
                "relationships_added": [],
                "relationships_removed": [],
                "measures_added": [],
                "measures_removed": [],
            }

            # Compare tables
            current_tables = {t["name"]: t for t in current_model.get("tables", [])}
            ref_tables = {t["name"]: t for t in ref_model.get("tables", [])}

            for name in current_tables:
                if name not in ref_tables:
                    changes["tables_added"].append(name)

            for name in ref_tables:
                if name not in current_tables:
                    changes["tables_removed"].append(name)

            # Compare relationships
            current_rels = set()
            for r in current_model.get("relationships", []):
                rel_key = f"{r.get('fromTable')}.{r.get('fromColumn')}->{r.get('toTable')}.{r.get('toColumn')}"
                current_rels.add(rel_key)

            ref_rels = set()
            for r in ref_model.get("relationships", []):
                rel_key = f"{r.get('fromTable')}.{r.get('fromColumn')}->{r.get('toTable')}.{r.get('toColumn')}"
                ref_rels.add(rel_key)

            changes["relationships_added"] = list(current_rels - ref_rels)
            changes["relationships_removed"] = list(ref_rels - current_rels)

            # Summary
            total_changes = sum(len(v) for v in changes.values())

            return {
                "success": True,
                "changes": changes,
                "summary": {
                    "total_changes": total_changes,
                    "tables_changed": len(changes["tables_added"]) + len(changes["tables_removed"]),
                    "relationships_changed": len(changes["relationships_added"])
                    + len(changes["relationships_removed"]),
                },
            }

        except Exception as e:
            logger.error(f"Model comparison error: {e}")
            return {"success": False, "error": str(e)}

    def get_model_summary(self, query_executor) -> Dict[str, Any]:
        """
        Get a comprehensive but lightweight model summary.
        Useful for comparing large models without exporting full TMSL.
        """
        try:
            summary: Dict[str, Any] = {"success": True, "timestamp": datetime.now().isoformat()}

            # Initialize aggregations to build top-level counts and per-table info
            top_counts = {
                "tables": 0,
                "columns": 0,
                "measures": 0,
                "relationships": 0,
            }
            tables_by_name: Dict[str, Any] = {}

            # Helpers to read DMV rows with flexible key names (supports [Name] etc.)
            def _safe_get(mapping: Dict[int, Any], key: int | None, default: Any = None) -> Any:
                try:
                    if key is None:
                        return default
                    return mapping.get(int(key), default)
                except Exception:
                    return default

            # Get tables with row counts
            tables_result = query_executor.execute_info_query("TABLES", top_n=5000)
            tables = tables_result["rows"] if tables_result.get("success") else []
            # ID and name maps for joins
            tables_by_id: Dict[int, str] = {}

            def _get_id(row: Dict[str, Any], keys: list[str]) -> int | None:
                v = get_field_value(row, keys)
                try:
                    if v is None:
                        return None
                    return int(str(v))
                except Exception:
                    return None

            if tables:
                top_counts["tables"] = len(tables)

                # Normalize table names to avoid nulls in clients
                def _table_name(row: Dict[str, Any]) -> str:
                    v = get_field_value(row, ["Name", "Table", "TABLE_NAME", "TableName"])
                    if v not in (None, ""):
                        return str(v)
                    # As a last resort use ID if present
                    v = get_field_value(row, ["ID", "TableID"])
                    return str(v) if v not in (None, "") else "Unknown"

                table_list = [
                    {"name": _table_name(t), "hidden": t.get("IsHidden", False)} for t in tables
                ]
                summary["tables"] = {"count": len(tables), "list": table_list}
                for t in table_list:
                    tables_by_name[t["name"]] = {
                        "hidden": t.get("hidden", False),
                        "columns": 0,
                        "measures": 0,
                    }
                # Build tables_by_id for joins
                for tr in tables:
                    tid = _get_id(tr, ["ID", "TableID"])
                    nm = _table_name(tr)
                    if tid is not None and nm:
                        tables_by_id[tid] = nm

            # Get measures
            measures_result = query_executor.execute_info_query("MEASURES", top_n=5000)
            if measures_result.get("success"):
                measures = measures_result["rows"]
                top_counts["measures"] = len(measures)
                summary["measures"] = {"count": len(measures), "by_table": {}}
                for m in measures:
                    table = get_field_value(m, ["Table", "TableName"])
                    if not table:
                        # Try joining by TableID
                        mid = _get_id(m, ["TableID"])
                        if mid is not None and mid in tables_by_id:
                            table = tables_by_id[mid]
                    table = table or "Unknown"
                    if table not in summary["measures"]["by_table"]:
                        summary["measures"]["by_table"][table] = 0
                    summary["measures"]["by_table"][table] += 1
                # Populate per-table measures count
                for tbl, cnt in summary["measures"]["by_table"].items():
                    tables_by_name.setdefault(tbl, {"hidden": False, "columns": 0, "measures": 0})
                    tables_by_name[tbl]["measures"] = cnt

            # Get columns
            columns_result = query_executor.execute_info_query("COLUMNS", top_n=5000)
            columns_by_id: Dict[int, Dict[str, Any]] = {}
            if columns_result.get("success"):
                columns = columns_result["rows"]
                top_counts["columns"] = len(columns)
                summary["columns"] = {
                    "count": len(columns),
                    "calculated": len(
                        [
                            c
                            for c in columns
                            if str(get_field_value(c, ["Type"])).lower() == "calculated"
                        ]
                    ),
                    "by_table": {},
                }
                for c in columns:
                    table = get_field_value(c, ["Table", "TableName"])
                    if not table:
                        # Join via TableID
                        cid_tid = _get_id(c, ["TableID"])
                        if cid_tid is not None and cid_tid in tables_by_id:
                            table = tables_by_id[cid_tid]
                    table = table or "Unknown"
                    if table not in summary["columns"]["by_table"]:
                        summary["columns"]["by_table"][table] = 0
                    summary["columns"]["by_table"][table] += 1
                    # Build columns_by_id for relationship endpoint names
                    col_id = _get_id(c, ["ID", "ColumnID"])
                    if col_id is not None and col_id not in columns_by_id:
                        columns_by_id[col_id] = {
                            "name": str(get_field_value(c, ["Name"]) or ""),
                            "table": table,
                        }
                # Populate per-table columns count
                for tbl, cnt in summary["columns"]["by_table"].items():
                    tables_by_name.setdefault(tbl, {"hidden": False, "columns": 0, "measures": 0})
                    tables_by_name[tbl]["columns"] = cnt

            # Get relationships
            rels_result = query_executor.execute_info_query("RELATIONSHIPS", top_n=5000)
            if rels_result.get("success"):
                rels = rels_result["rows"]
                top_counts["relationships"] = len(rels)
                rel_list = []
                active_count = 0
                for r in rels:
                    is_active = safe_bool(get_field_value(r, ["IsActive"]))
                    if is_active:
                        active_count += 1
                    ftid = _get_id(r, ["FromTableID"])
                    fcid = _get_id(r, ["FromColumnID"])
                    ttid = _get_id(r, ["ToTableID"])
                    tcid = _get_id(r, ["ToColumnID"])
                    ft = _safe_get(tables_by_id, ftid, get_field_value(r, ["FromTable"]) or "?")
                    tt = _safe_get(tables_by_id, ttid, get_field_value(r, ["ToTable"]) or "?")
                    fc = (
                        (_safe_get(columns_by_id, fcid, {}) or {}).get("name")
                        or get_field_value(r, ["FromColumn"])
                        or "?"
                    )
                    tc = (
                        (_safe_get(columns_by_id, tcid, {}) or {}).get("name")
                        or get_field_value(r, ["ToColumn"])
                        or "?"
                    )
                    rel_list.append(f"{ft}[{fc}] -> {tt}[{tc}]")
                summary["relationships"] = {
                    "count": len(rels),
                    "active": active_count,
                    "inactive": max(0, len(rels) - active_count),
                    "list": rel_list,
                }

            # Attach top-level counts and a convenience map by table name
            summary["counts"] = top_counts
            summary["tables_by_name"] = tables_by_name

            # Heuristic purpose/capabilities summary so consumers know what the model likely does
            try:
                tbl_names = set(tables_by_name.keys())
                name_str = " ".join(tbl_names).lower()

                def present(substrs):
                    return any(s.lower() in name_str for s in substrs)

                domains = []
                signals = []

                # Time intelligence
                if any(k in tbl_names for k in ("d_Date", "d_Period")) or present(
                    ["time", "period", "calendar"]
                ):
                    domains.append("Period/Time")
                    signals.append("Time intelligence (Date/Period tables)")

                # Currency conversion
                if present(["currency", "fx"]) or any(
                    k in tbl_names
                    for k in ("d_Currency_From", "d_Currency_Report", "d_Currency_Rates")
                ):
                    domains.append("Currency/FX")
                    signals.append("Currency conversion present (currency tables)")

                # Scenario/versioning
                if present(["scenario", "version"]):
                    domains.append("Scenario/Version")
                    signals.append("Scenario/version switching")

                # Financial reporting / GL / P&L / Balance Sheet
                if (
                    present(["gl", "p&l", "pl", "balance", "bs", "cash flow", "cf", "finrep"])
                    or "f_FINREP" in tbl_names
                    or "d_GL Account" in tbl_names
                ):
                    domains.append("Financial Reporting")
                    signals.append("General Ledger / P&L / BS indicators detected")

                # Aging / AR / AP
                if present(["aging"]) or any(
                    k in tbl_names for k in ("f_Aging_Customer", "f_Aging_Vendor")
                ):
                    domains.append("Aging/AR/AP")
                    signals.append("Accounts receivable/payable aging")

                # RLS
                if present(["rls"]) or any(n.startswith("r_RLS_") for n in tbl_names):
                    domains.append("Row-Level Security")
                    signals.append("RLS artifacts detected (roles/tables)")

                # Customer/Vendor
                if any(k in tbl_names for k in ("d_Customer", "d_Vendor")) or present(
                    ["customer", "vendor", "supplier"]
                ):
                    domains.append("Customer/Vendor")

                # Organization (Cost/Profit/Company)
                if any(
                    k in tbl_names for k in ("d_Company", "d_CostCenter", "d_Profit Center")
                ) or present(["company", "cost center", "profit center"]):
                    domains.append("Company/Org")

                # Star schema hint
                star_hint = None
                if any(n.startswith("f_") for n in tbl_names) and any(
                    n.startswith("d_") for n in tbl_names
                ):
                    star_hint = "Star-schema oriented (facts linked to multiple dimensions)"
                    signals.append(star_hint)

                # Measure hub hint
                if "m_Measures" in tbl_names:
                    signals.append("Central measure table (m_Measures)")

                # Compose a short purpose text
                lead = []
                if "Financial Reporting" in domains:
                    lead.append("financial reporting")
                if "Currency/FX" in domains:
                    lead.append("currency conversion")
                if "Row-Level Security" in domains:
                    lead.append("row-level security")
                if "Period/Time" in domains:
                    lead.append("time intelligence")

                if lead:
                    purpose_text = f"Model geared towards {', '.join(lead[:-1]) + (' and ' if len(lead) > 1 else '') + lead[-1]}"
                else:
                    purpose_text = "General analytics model"

                if star_hint and star_hint not in signals:
                    signals.append(star_hint)

                # De-duplicate while preserving order
                seen = set()
                signals = [s for s in signals if not (s in seen or seen.add(s))]
                dom_seen = set()
                domains = [d for d in domains if not (d in dom_seen or dom_seen.add(d))]

                summary["purpose"] = {
                    "text": purpose_text,
                    "domains": domains,
                    "signals": signals,
                }
            except Exception:
                # Non-fatal: keep summary usable even if heuristics fail
                pass

            return summary

        except Exception as e:
            logger.error(f"Error getting model summary: {e}")
            return {"success": False, "error": str(e)}

    def export_tmdl(self, output_dir: Optional[str] = None) -> Dict[str, Any]:
        """
        Alias for export_tmdl_structure with export_to_file=True.
        Used by tool handlers for backward compatibility.

        Args:
            output_dir: Optional directory path for export

        Returns:
            Export result with file path
        """
        return self.export_tmdl_structure(export_to_file=True, output_path=output_dir)
