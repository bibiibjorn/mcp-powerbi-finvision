"""``AnalysisOrchestrator`` methods split out of ``core.orchestration.analysis_orchestrator`` (C-17): pure move, no behaviour change.

``AnalysisOrchestrator`` inherits :class:`OrchestratorSimpleMixin`, so every ``self.<method>`` call and every import of
``core.orchestration.analysis_orchestrator.AnalysisOrchestrator`` keeps working.
"""

import logging
from typing import Any, Dict, List
from core.analysis import info_normalize as infn
from core.validation.error_handler import requires_connection
import time

logger = logging.getLogger("core.orchestration.analysis_orchestrator")


class OrchestratorSimpleMixin:
    """Methods of :class:`AnalysisOrchestrator` (orchestrator_simple.py)."""

    @staticmethod
    def _table_stats(executor) -> Dict[str, Dict[str, int]]:
        """Per-table column / measure / partition counts (INFO.TABLES has none)."""
        stats: Dict[str, Dict[str, int]] = {}
        for key, fn, kwargs in (
            ("column_count", "COLUMNS", {"exclude_columns": ["Expression"], "top_n": 50000}),
            ("measure_count", "MEASURES", {"exclude_columns": ["Expression"], "top_n": 50000}),
            ("partition_count", "PARTITIONS", {"top_n": 50000}),
        ):
            try:
                res = executor.execute_info_query(fn, **kwargs)
            except Exception:  # noqa: BLE001
                continue
            if not res.get("success"):
                continue
            rows = res.get("rows", []) or []
            if fn == "PARTITIONS":
                # INFO.PARTITIONS carries TableID only
                for r in rows:
                    tid = infn.pick(r, "TableID")
                    name = None
                    if tid is not None and hasattr(executor, "_get_table_name_from_id"):
                        try:
                            name = executor._get_table_name_from_id(tid)
                        except Exception:  # noqa: BLE001
                            name = None
                    t = str(name or infn.pick(r, "Table", default=tid) or "")
                    stats.setdefault(t, {})[key] = stats.get(t, {}).get(key, 0) + 1
                continue
            counts = infn.counts_by_table(rows, skip_row_number=(fn == "COLUMNS"))
            for t, n in counts.items():
                stats.setdefault(t, {})[key] = n
        return stats

    @requires_connection
    def list_tables_simple(self, connection_state) -> Dict[str, Any]:
        """Table list with column / measure / partition counts and hidden flag."""
        start_time = time.time()
        executor = connection_state.query_executor
        stats = self._table_stats(executor)

        def post(data, rows):
            for item, row in zip(data, rows):
                s = stats.get(item["name"], {})
                item["column_count"] = s.get("column_count", 0)
                item["partition_count"] = s.get("partition_count", 0)
                mc = s.get("measure_count", 0)
                if mc > 0:
                    item["measure_count"] = mc
                item["is_hidden"] = infn.to_bool(infn.pick(row, "IsHidden", default=False))
            return data

        result = self._query_and_map(
            connection_state,
            "TABLES",
            field_map={"name": ("Name", "")},
            response_key="tables",
            query_kwargs={"top_n": 10000},
            post_process=post,
        )
        if result.get("success"):
            result["analysis_type"] = "table_list"
            result["execution_time_seconds"] = round(time.time() - start_time, 2)
            result["table_count"] = len(result["data"])
        return result

    @requires_connection
    def simple_model_analysis(self, connection_state) -> Dict[str, Any]:
        """
        Fast model statistics overview.

        Counts come from INFO.* row counts (measures/columns fetched WITHOUT
        expressions — I-29); per-table counts from the grouped rows (I-15).

        Returns: dict with success/data/message keys.
        """
        from core.validation.error_handler import ErrorHandler

        start_time = time.time()
        executor = connection_state.query_executor

        if not executor:
            return ErrorHandler.handle_manager_unavailable("query_executor")

        try:
            model_info = executor.execute_info_query("MODEL", top_n=10)
            tables = executor.execute_info_query("TABLES", top_n=10000)
            relationships = executor.execute_info_query("RELATIONSHIPS", top_n=5000)
            roles = executor.execute_info_query("ROLES", top_n=1000)
            calc_groups = executor.execute_info_query("CALCULATION_GROUPS", top_n=1000)
            data_sources = executor.execute_info_query("DATA_SOURCES")
            cultures = executor.execute_info_query("CULTURES")
            perspectives = executor.execute_info_query("PERSPECTIVES")
            stats = self._table_stats(executor)

            model_data = (
                model_info.get("rows", [{}])[0]
                if model_info.get("success") and model_info.get("rows")
                else {}
            )
            compat_level = self._compatibility_level(executor)
            model = {
                "name": model_data.get("Name", "Model"),
                "database": model_data.get("Database", ""),
                "compatibility_level": compat_level,
            }

            if compat_level >= 1601:
                model["compatibility_version"] = "Power BI 2025"
            elif compat_level >= 1600:
                model["compatibility_version"] = "Power BI 2021+"
            elif compat_level >= 1500:
                model["compatibility_version"] = "SQL Server 2019 / Power BI"
            elif compat_level >= 1400:
                model["compatibility_version"] = "SQL Server 2017 / Power BI"
            elif compat_level >= 1200:
                model["compatibility_version"] = "SQL Server 2016 / Power BI"
            else:
                model["compatibility_version"] = "Unknown"

            counts = {
                "tables": len(tables.get("rows", [])),
                "columns": sum(s.get("column_count", 0) for s in stats.values()),
                "measures": sum(s.get("measure_count", 0) for s in stats.values()),
                "relationships": len(relationships.get("rows", [])),
                "partitions": sum(s.get("partition_count", 0) for s in stats.values()),
                "roles": len(roles.get("rows", [])),
                "data_sources": (
                    len(data_sources.get("rows", [])) if data_sources.get("success") else 0
                ),
                "cultures": len(cultures.get("rows", [])) if cultures.get("success") else 1,
                "perspectives": (
                    len(perspectives.get("rows", [])) if perspectives.get("success") else 0
                ),
                "calculation_groups": len(calc_groups.get("rows", [])),
            }

            tables_detailed = []
            for table in tables.get("rows", []):
                table_name = table.get("Name", "")
                s = stats.get(table_name, {})
                table_info = {
                    "name": table_name,
                    "type": self._infer_table_type(table_name),
                    "column_count": s.get("column_count", 0),
                    "partition_count": s.get("partition_count", 0),
                    "is_hidden": infn.to_bool(table.get("IsHidden", False)),
                }
                if s.get("measure_count", 0) > 0:
                    table_info["measure_count"] = s["measure_count"]
                tables_detailed.append(table_info)

            measure_tables = [
                t["name"]
                for t in tables_detailed
                if t.get("measure_count", 0) > 0 and t["column_count"] < 5
            ]
            largest_tables = sorted(tables_detailed, key=lambda t: t["column_count"], reverse=True)[
                :5
            ]
            largest_tables = [
                {"name": t["name"], "column_count": t["column_count"]} for t in largest_tables
            ]
            table_types: Dict[str, int] = {}
            for t in tables_detailed:
                table_types[t["type"]] = table_types.get(t["type"], 0) + 1

            summary = {
                "total_objects": (
                    counts["tables"]
                    + counts["columns"]
                    + counts["measures"]
                    + counts["relationships"]
                ),
                "measure_tables": measure_tables,
                "largest_tables": largest_tables,
                "table_types": table_types,
            }

            return {
                "success": True,
                "analysis_type": "simple_stats",
                "execution_time_seconds": round(time.time() - start_time, 2),
                "model": model,
                "counts": counts,
                "tables": tables_detailed,
                "summary": summary,
            }

        except Exception as e:
            logger.error(f"Simple model analysis failed: {e}")
            return {"success": False, "error": f"Simple model analysis failed: {str(e)}"}

    @requires_connection
    def list_measures_simple(
        self, connection_state, table_name: str = None, max_results: int = None
    ) -> Dict[str, Any]:
        """List measures with optional table filter."""
        result = self._query_and_map(
            connection_state,
            "MEASURES",
            field_map={
                "name": ("Name", ""),
                "table": ("Table", ""),
                "displayFolder": ("DisplayFolder", ""),
            },
            response_key="measures",
            query_kwargs={"table_name": table_name, "exclude_columns": ["Expression"]},
            message_fn=(
                (lambda d: f"Found {len(d)} measures in table '{table_name}'")
                if table_name
                else None
            ),
        )
        if not result.get("success"):
            return result
        total_count = len(result["data"])
        if max_results and total_count > max_results:
            result["data"] = result["data"][:max_results]
            result["message"] = (
                f"Found {len(result['data'])} measures in table '{table_name}'"
                if table_name
                else f"Found {len(result['data'])} measures"
            )
            result["warnings"] = [
                f"Results truncated: Showing {len(result['data'])} of {total_count} measures "
                f"(limited by MaxResults={max_results})"
            ]
        if table_name:
            result["tableName"] = table_name
        return result

    @requires_connection
    def get_measure_simple(
        self, connection_state, table_name: str, measure_name: str
    ) -> Dict[str, Any]:
        """
        Get detailed measure information including DAX expression.

        Args:
            connection_state: Current connection state
            table_name: Table containing the measure
            measure_name: Measure name to retrieve

        Returns: dict with success/data/message keys.
        """
        from core.validation.error_handler import ErrorHandler

        executor = connection_state.query_executor
        if not executor:
            return ErrorHandler.handle_manager_unavailable("query_executor")

        if not table_name or not measure_name:
            return {"success": False, "error": "table_name and measure_name are required"}

        try:
            # Use the existing get_measure_details_with_fallback
            result = executor.get_measure_details_with_fallback(table_name, measure_name)

            if not result.get("success"):
                return result

            measure_data = result.get("measure", {})

            # Build response matching Microsoft MCP format
            return {
                "success": True,
                "message": f"Measure '{measure_name}' retrieved successfully",
                "operation": "Get",
                "measureName": measure_name,
                "tableName": table_name,
                "data": measure_data,
            }

        except Exception as e:
            logger.error(f"Get measure failed: {e}")
            return {"success": False, "error": f"Get measure failed: {str(e)}"}

    @requires_connection
    def list_relationships_simple(
        self, connection_state, active_only: bool = False
    ) -> Dict[str, Any]:
        """List all relationships with full metadata (enums as names)."""

        def post(data, rows):
            out = []
            for row in rows:
                out.append(infn.normalize_relationship(row))
            return out

        return self._query_and_map(
            connection_state,
            "RELATIONSHIPS",
            field_map={"name": ("Name", "")},
            response_key="relationships",
            filter_fn=(lambda r: infn.to_bool(r.get("IsActive"))) if active_only else None,
            post_process=post,
            operation="LIST",
        )

    @requires_connection
    def list_calculation_groups_simple(self, connection_state) -> Dict[str, Any]:
        """
        List all calculation groups with their items.

        Uses the CalculationGroupManager for robust calculation group retrieval.

        Args:
            connection_state: Current connection state

        Returns: dict with success/data/message keys.
        """
        from core.validation.error_handler import ErrorHandler

        calc_group_manager = connection_state.calc_group_manager
        if not calc_group_manager:
            return ErrorHandler.handle_manager_unavailable("calc_group_manager")

        try:
            # Use the dedicated CalculationGroupManager which has robust DAX + AMO fallback
            result = calc_group_manager.list_calculation_groups()

            if not result.get("success"):
                # If listing failed, return empty list with success=True (model may have no calc groups)
                return {
                    "success": True,
                    "message": "Found 0 calculation groups",
                    "operation": "ListGroups",
                    "data": [],
                    "note": result.get("error", "Unable to query calculation groups"),
                }

            # Convert from CalculationGroupManager format to Microsoft MCP format
            calc_groups = result.get("calculation_groups", [])
            mcp_groups = []

            for cg in calc_groups:
                # Convert items to MCP format (simplified - just ordinal and name)
                calc_items = []
                for item in cg.get("items", []):
                    calc_items.append(
                        {"ordinal": item.get("ordinal", 0), "name": item.get("name", "")}
                    )

                # Sort by ordinal
                calc_items.sort(key=lambda x: x["ordinal"])

                mcp_groups.append(
                    {"name": cg.get("name", cg.get("table", "")), "calculationItems": calc_items}
                )

            # Build response matching Microsoft MCP format
            return {
                "success": True,
                "message": f"Found {len(mcp_groups)} calculation groups",
                "operation": "ListGroups",
                "data": mcp_groups,
            }

        except Exception as e:
            logger.error(f"List calculation groups failed: {e}")
            # Return empty list rather than error (model may have no calc groups)
            return {
                "success": True,
                "message": "Found 0 calculation groups",
                "operation": "ListGroups",
                "data": [],
                "note": f"Calculation group query failed: {str(e)}",
            }

    @requires_connection
    def list_columns_simple(
        self, connection_state, table_name: str = None, max_results: int = None
    ) -> Dict[str, Any]:
        """
        List columns across tables or for a specific table.

        Names come from ``ExplicitName`` and data types from the
        ``ExplicitDataType`` enum (I-15); ``type`` is the column kind
        (``Data`` / ``Calculated`` / ``CalculatedTableColumn``); row-number
        columns are skipped.

        Returns: dict with success/data/message keys.
        """
        from core.validation.error_handler import ErrorHandler

        executor = connection_state.query_executor
        if not executor:
            return ErrorHandler.handle_manager_unavailable("query_executor")

        try:
            result = executor.execute_info_query(
                "COLUMNS", table_name=table_name, top_n=50000, exclude_columns=["Expression"]
            )
            if not result.get("success"):
                return result

            rows = [r for r in result.get("rows", []) if infn.column_kind(r) != "RowNumber"]
            total_count = len(rows)
            if total_count == 0:
                return {
                    "success": True,
                    "operation": "List",
                    "message": "No columns found",
                    "data": [],
                }

            tables_dict: Dict[str, List[Dict[str, Any]]] = {}
            for row in rows:
                tbl = str(infn.pick(row, "Table", "TableName", default="Unknown"))
                col_name = infn.column_name(row) or f"Column_{len(tables_dict.get(tbl, [])) + 1}"
                tables_dict.setdefault(tbl, []).append(
                    {
                        "name": col_name,
                        "table": tbl,
                        "dataType": infn.column_data_type(row),
                        "type": infn.column_kind(row),
                        "isHidden": infn.to_bool(infn.pick(row, "IsHidden", default=False)),
                    }
                )

            columns_by_table = []
            column_count = 0
            for tbl_name, cols in sorted(tables_dict.items()):
                cols_to_add = []
                for col in cols:
                    if max_results and column_count >= max_results:
                        break
                    cols_to_add.append(col)
                    column_count += 1
                columns_by_table.append({"tableName": tbl_name, "columns": cols_to_add})
                if max_results and column_count >= max_results:
                    break

            returned_count = sum(len(t["columns"]) for t in columns_by_table)
            truncated = max_results and total_count > max_results
            response: Dict[str, Any] = {"success": True, "operation": "List"}
            if table_name:
                response["message"] = f"Found {returned_count} columns in table '{table_name}'"
            else:
                response["message"] = (
                    f"Found {returned_count} columns across {len(columns_by_table)} tables"
                )
            response["data"] = columns_by_table
            if truncated:
                response["warnings"] = [
                    f"Results truncated: Showing {returned_count} of {total_count} columns"
                ]
            return response

        except Exception as e:
            logger.error(f"List columns failed: {e}")
            return {"success": False, "error": f"List columns failed: {str(e)}"}

    @requires_connection
    def list_roles_simple(self, connection_state) -> Dict[str, Any]:
        """
        List security roles with their table permissions (INFO.TABLEPERMISSIONS;
        the old ``TMSCHEMA_MODEL_ROLE_TABLE_PERMISSIONS()`` is not a DAX function).

        Returns: dict with success/data/message keys.
        """
        from core.validation.error_handler import ErrorHandler

        executor = connection_state.query_executor
        if not executor:
            return ErrorHandler.handle_manager_unavailable("query_executor")

        try:
            result = executor.execute_info_query("ROLES")
            if not result.get("success"):
                return result
            rows = result.get("rows", [])
            role_by_id = {str(infn.pick(r, "ID")): infn.pick(r, "Name", default="") for r in rows}

            perms_by_role: Dict[str, List[Dict[str, Any]]] = {}
            perms_note = None
            try:
                perms = executor.execute_info_query("TABLEPERMISSIONS")
                if perms.get("success"):
                    for p in perms.get("rows", []) or []:
                        role = role_by_id.get(str(infn.pick(p, "RoleID")), "")
                        tid = infn.pick(p, "TableID")
                        tname = infn.pick(p, "Table")
                        if (
                            tname is None
                            and tid is not None
                            and hasattr(executor, "_get_table_name_from_id")
                        ):
                            tname = executor._get_table_name_from_id(tid)
                        perms_by_role.setdefault(role, []).append(
                            {
                                "table": str(tname if tname is not None else tid),
                                "has_filter": bool(
                                    str(infn.pick(p, "FilterExpression", default="") or "").strip()
                                ),
                                "metadata_permission": infn.enum_name(
                                    infn.METADATA_PERMISSION,
                                    infn.pick(p, "MetadataPermission"),
                                    "Default",
                                ),
                            }
                        )
                else:
                    perms_note = perms.get("error") or "INFO.TABLEPERMISSIONS unavailable"
            except Exception as perm_error:  # noqa: BLE001
                perms_note = str(perm_error)

            roles = []
            for row in rows:
                role_name = infn.pick(row, "Name", default="")
                role_perms = perms_by_role.get(role_name, [])
                filtered = [p["table"] for p in role_perms if p["has_filter"]]
                roles.append(
                    {
                        "name": role_name,
                        "modelPermission": infn.enum_name(
                            infn.MODEL_PERMISSION, infn.pick(row, "ModelPermission"), "Read"
                        ),
                        "tablePermissionCount": len(filtered),
                        "filteredTables": filtered,
                        "tablePermissions": role_perms,
                    }
                )

            out = {
                "success": True,
                "message": f"Found {len(roles)} roles",
                "operation": "List",
                "data": roles,
            }
            if perms_note:
                out["warnings"] = [f"Table permissions not read: {perms_note}"]
            return out

        except Exception as e:
            logger.error(f"List roles failed: {e}")
            return {"success": False, "error": f"List roles failed: {str(e)}"}

    @requires_connection
    def list_databases_simple(self, connection_state) -> Dict[str, Any]:
        """The connected database with its compatibility level and display name."""
        executor = connection_state.query_executor
        compat = self._compatibility_level(executor)
        inst: Dict[str, Any] = {}
        try:
            cm = getattr(connection_state, "connection_manager", None)
            inst = (cm.get_instance_info() if cm else None) or {}
        except Exception:  # noqa: BLE001
            inst = {}

        def post(data, rows):
            for item, row in zip(data, rows):
                item["id"] = row.get("Database", row.get("ID", ""))
                item["name"] = inst.get("display_name") or row.get("Name", "") or item["id"]
                item["model_name"] = row.get("Name", "")
                item["modelType"] = "Tabular"
                item["compatibilityLevel"] = compat
                for out_key, row_key in (
                    ("createdTimestamp", "CreatedTimestamp"),
                    ("lastUpdate", "ModifiedTime"),
                    ("lastSchemaUpdate", "StructureModifiedTime"),
                    ("culture", "Culture"),
                ):
                    if row.get(row_key):
                        item[out_key] = row.get(row_key)
            return data

        return self._query_and_map(
            connection_state,
            "MODEL",
            field_map={},
            response_key="databases on server",
            post_process=post,
        )
