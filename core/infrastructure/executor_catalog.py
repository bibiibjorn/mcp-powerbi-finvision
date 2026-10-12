"""``OptimizedQueryExecutor`` methods split out of ``core.infrastructure.query_executor`` (C-17): pure move, no behaviour change.

``OptimizedQueryExecutor`` inherits :class:`ExecutorCatalogMixin`, so every ``self.<method>`` call and every import of
``core.infrastructure.query_executor.OptimizedQueryExecutor`` keeps working.
"""

import logging
from typing import Any, Dict, List, Optional
from core.config.config_manager import config
from core.infrastructure.adomd_utils import engine_message
from core.infrastructure.limits_manager import get_limits

logger = logging.getLogger("core.infrastructure.query_executor")


class ExecutorCatalogMixin:
    """Methods of :class:`OptimizedQueryExecutor` (executor_catalog.py)."""

    def _get_info_columns(self, function_name: str) -> List[str]:
        """Real INFO.* column names used for projected (exclude_columns) queries.

        B-17: INFO.COLUMNS has ExplicitName/InferredName/ExplicitDataType (no
        Name/DataType) and INFO.RELATIONSHIPS carries IDs + From/ToCardinality
        (no FromTable/Cardinality). Name-ish aliases are added client-side by
        ``execute_info_query``.
        """
        column_map = {
            "MEASURES": [
                "ID",
                "Name",
                "TableID",
                "DataType",
                "IsHidden",
                "DisplayFolder",
                "Expression",
                "FormatString",
                "Description",
            ],
            "TABLES": ["ID", "Name", "IsHidden", "ModifiedTime", "DataCategory", "Description"],
            "COLUMNS": [
                "ID",
                "TableID",
                "ExplicitName",
                "InferredName",
                "ExplicitDataType",
                "InferredDataType",
                "IsHidden",
                "IsKey",
                "Type",
                "Expression",
                "DisplayFolder",
            ],
            "RELATIONSHIPS": [
                "ID",
                "Name",
                "FromTableID",
                "FromColumnID",
                "ToTableID",
                "ToColumnID",
                "IsActive",
                "CrossFilteringBehavior",
                "FromCardinality",
                "ToCardinality",
            ],
        }
        return column_map.get(function_name, [])

    def execute_info_query(
        self,
        function_name: str,
        filter_expr: Optional[str] = None,
        exclude_columns: Optional[List[str]] = None,
        table_name: Optional[str] = None,
        top_n: Optional[int] = None,
        bypass_cache: bool = False,
    ) -> Dict[str, Any]:
        """
        Execute INFO.* DAX query with optional filtering.
        ``bypass_cache=True`` forces a round-trip (plan hashes / staleness checks,
        review E-02 — cross-WP edit by WP6).
        Automatically converts TableID to Table for MEASURES and COLUMNS.
        Handles table_name parameter by looking up the numeric TableID.

        Args:
            function_name: INFO function name (TABLES, COLUMNS, MEASURES, RELATIONSHIPS)
            filter_expr: Optional DAX filter expression (uses TableID as integer)
            exclude_columns: Optional list of columns to exclude
            table_name: Optional table name to filter by (will be converted to numeric TableID)

        Returns:
            Query result dictionary with Table column instead of TableID
        """
        try:
            # If table_name is provided, look up the numeric TableID and build filter
            if table_name:
                table_id = self._get_table_id_from_name(table_name)
                if table_id is None:
                    return {
                        "success": False,
                        "error": f'Table "{table_name}" not found',
                        "error_type": "table_not_found",
                        "suggestions": [
                            "Verify the table name: 02_Model_Operations object_type=table operation=list",
                            "Check case-sensitive spelling",
                        ],
                    }
                # Build or append to filter expression using numeric TableID
                table_filter = f"[TableID] = {table_id}"
                if filter_expr:
                    filter_expr = f"({filter_expr}) && ({table_filter})"
                else:
                    filter_expr = table_filter

            # Apply default limit to prevent unlimited returns

            if top_n is None:
                # Use centralized limits manager if available
                try:
                    limits = get_limits()
                    top_n = limits.query.default_info_limit
                except RuntimeError:
                    # Fallback to config if limits_manager not initialized yet
                    # Use high default to ensure all measures/columns are fetched
                    top_n = config.get("query.default_info_limit", 10000)

            # Prefer plain INFO.* for broad compatibility; optionally attempt selective projection
            inner = f"INFO.{function_name}()"
            # Apply TOPN limit to prevent token overflow
            if filter_expr:
                inner_limited = f"TOPN({top_n}, FILTER({inner}, {filter_expr}))"
            else:
                inner_limited = f"TOPN({top_n}, {inner})"
            query = f"EVALUATE {inner_limited}"
            # If caller asked to exclude heavy columns (e.g., Expression), try a projected SELECTCOLUMNS,
            # but fall back to plain INFO.* if projection fails on this Desktop build.
            if exclude_columns:
                cols = self._get_info_columns(function_name)
                try:
                    selected = [f'"{col}", [{col}]' for col in cols if col not in exclude_columns]
                    inner_proj = f"SELECTCOLUMNS({inner}, {', '.join(selected)})"
                    query_proj = (
                        f"EVALUATE FILTER({inner_proj}, {filter_expr})"
                        if filter_expr
                        else f"EVALUATE {inner_proj}"
                    )
                    res_proj = self.validate_and_execute_dax(
                        query_proj, 0, bypass_cache=bypass_cache
                    )
                    if res_proj.get("success"):
                        result = res_proj
                    else:
                        # Fallback to plain query
                        result = self.validate_and_execute_dax(query, 0, bypass_cache=bypass_cache)
                except Exception:
                    result = self.validate_and_execute_dax(query, 0, bypass_cache=bypass_cache)
            else:
                result = self.validate_and_execute_dax(query, 0, bypass_cache=bypass_cache)
            # After here, 'result' holds execution

            # Normalize keys and convert TableID to Table for better usability
            if result.get("success"):
                rows = result.get("rows", []) or []
                for row in rows:
                    # Normalize bracketed aliases if any
                    for k in list(row.keys()):
                        if k.startswith("[") and k.endswith("]"):
                            row[k[1:-1]] = row.pop(k)
                    if function_name in ["MEASURES", "COLUMNS"]:
                        if "Table" not in row and "TableID" in row:
                            name = self._get_table_name_from_id(row.get("TableID"))
                            row["Table"] = name or (
                                str(row.get("TableID")) if row.get("TableID") is not None else ""
                            )
                        if function_name == "COLUMNS":
                            # J-03(a)/K-08: INFO.COLUMNS has no Name/DataType;
                            # add the aliases every consumer expects.
                            if row.get("Name") is None:
                                alias = row.get("ExplicitName") or row.get("InferredName")
                                if alias is not None:
                                    row["Name"] = alias
                            if row.get("DataType") is None:
                                dtype = row.get("ExplicitDataType")
                                if dtype is None:
                                    dtype = row.get("InferredDataType")
                                if dtype is not None:
                                    row["DataType"] = dtype
                    elif function_name == "RELATIONSHIPS":
                        # Convert FromTableID/ToTableID to FromTable/ToTable for relationships
                        if "FromTable" not in row and "FromTableID" in row:
                            from_table_id = row.get("FromTableID")
                            if from_table_id is not None:
                                from_name = self._get_table_name_from_id(from_table_id)
                                row["FromTable"] = from_name or str(from_table_id)
                        if "ToTable" not in row and "ToTableID" in row:
                            to_table_id = row.get("ToTableID")
                            if to_table_id is not None:
                                to_name = self._get_table_name_from_id(to_table_id)
                                row["ToTable"] = to_name or str(to_table_id)
                        # Convert FromColumnID/ToColumnID to FromColumn/ToColumn
                        if "FromColumn" not in row and "FromColumnID" in row:
                            from_column_id = row.get("FromColumnID")
                            if from_column_id is not None:
                                from_col_name = self._get_column_name_from_id(from_column_id)
                                row["FromColumn"] = from_col_name or str(from_column_id)
                        if "ToColumn" not in row and "ToColumnID" in row:
                            to_column_id = row.get("ToColumnID")
                            if to_column_id is not None:
                                to_col_name = self._get_column_name_from_id(to_column_id)
                                row["ToColumn"] = to_col_name or str(to_column_id)

            return result
        except Exception as e:
            logger.error(f"Error executing INFO query: {e}")
            return {
                "success": False,
                "error": engine_message(e),
                "error_type": "query_execution_error",
            }

    def get_measure_details_with_fallback(
        self, table_name: str, measure_name: str
    ) -> Dict[str, Any]:
        """Fetch measure details scoped to a table with robust fallback.

        Strategy:
        1) Attempt server-side filter on table and name via INFO.MEASURES().
        2) If empty or failed, fetch all measures and filter client-side by
           normalized table name and exact measure name.
        """
        try:
            # Primary: table-scoped query with name filter
            primary = self.execute_info_query(
                "MEASURES",
                filter_expr=f'[Name] = "{self._escape_dax_string(measure_name)}"',
                table_name=table_name,
                exclude_columns=None,
            )
            if primary.get("success") and (primary.get("rows") or []):
                return primary

            # Fallback: fetch all and filter client-side
            all_meas = self.execute_info_query("MEASURES")
            if not all_meas.get("success"):
                return primary

            t_norm = str(table_name or "").strip().lower()
            m_norm = str(measure_name or "").strip().lower()

            # Build id->name map once (execute_info_query(TABLES) already normalizes keys)
            id_to_name: Dict[str, str] = {}
            try:
                tbls = self.execute_info_query("TABLES")
                if tbls.get("success"):
                    for t in tbls.get("rows", []) or []:
                        tid = t.get("[ID]") or t.get("ID") or t.get("[TableID]") or t.get("TableID")
                        nm = t.get("Name") or t.get("[Name]")
                        if tid is not None and nm:
                            id_to_name[str(tid)] = str(nm).strip().lower()
            except Exception:
                pass

            def _row_table_name(r: Dict[str, Any]) -> Optional[str]:
                rt = r.get("Table") or r.get("[Table]")
                if isinstance(rt, str) and rt:
                    return rt.strip().lower()
                tid = r.get("TableID") or r.get("[TableID]")
                if tid is not None and str(tid) in id_to_name:
                    return id_to_name[str(tid)]
                return None

            def _row_measure_name(r: Dict[str, Any]) -> Optional[str]:
                rn = r.get("Name") or r.get("[Name]")
                return str(rn).strip().lower() if rn is not None else None

            rows = [
                r
                for r in (all_meas.get("rows") or [])
                if _row_table_name(r) == t_norm and _row_measure_name(r) == m_norm
            ]
            return {"success": True, "rows": rows, "row_count": len(rows), "client_filtered": True}
        except Exception as e:
            return {"success": False, "error": engine_message(e)}

    def search_measures_dax(
        self, search_text: str, search_in_expression: bool = True, search_in_name: bool = True
    ) -> Dict:
        """
        Search for text in DAX measures.

        Args:
            search_text: Text to search for
            search_in_expression: Whether to search in measure expressions
            search_in_name: Whether to search in measure names

        Returns:
            Search results dictionary
        """
        try:
            escaped_text = self._escape_dax_string(search_text)
            conditions = []

            if search_in_expression:
                conditions.append(f'SEARCH("{escaped_text}", [Expression], 1, 0) > 0')
            if search_in_name:
                conditions.append(f'SEARCH("{escaped_text}", [Name], 1, 0) > 0')

            filter_expr = " || ".join(conditions) if conditions else "TRUE()"
            query = f"EVALUATE FILTER(INFO.MEASURES(), {filter_expr})"

            result = self.validate_and_execute_dax(query)
            # Map TableID -> Table name for usability
            if result.get("success"):
                rows = result.get("rows", [])
                for row in rows:
                    if "TableID" in row and "Table" not in row:
                        name = self._get_table_name_from_id(row.get("TableID"))
                        row["Table"] = name or str(row.get("TableID"))
            return result
        except Exception as e:
            logger.error(f"Error searching measures: {e}")
            return {"success": False, "error": engine_message(e)}

    def search_objects_dax(self, pattern: str, object_types: List[str]) -> Dict:
        """
        Search for objects by name.

        ``pattern`` is a plain substring matched case-insensitively with DAX
        ``SEARCH()``; ``*`` and ``?`` are stripped (it is NOT a regex).

        B-17: INFO.COLUMNS has ``ExplicitName``/``InferredName`` and
        ``ExplicitDataType`` (no ``Name``/``DataType``) — the old column query
        always failed and was silently skipped, so ``types=[columns]`` never
        returned anything. Per-type failures are now reported in ``errors``.
        """
        try:
            search_text = pattern.replace("*", "").replace("?", "")
            escaped_text = self._escape_dax_string(search_text)
            results_list = []
            errors: List[Dict[str, Any]] = []

            if "tables" in object_types:
                query = f"""
                EVALUATE SELECTCOLUMNS(
                    FILTER(INFO.TABLES(), SEARCH("{escaped_text}", [Name], 1, 0) > 0),
                    "type", "table",
                    "Name", [Name],
                    "IsHidden", [IsHidden],
                    "ModifiedTime", [ModifiedTime]
                )
                """
                r = self.validate_and_execute_dax(query)
                if r.get("success"):
                    results_list.extend(r["rows"])
                else:
                    errors.append({"type": "tables", "error": r.get("error")})

            if "columns" in object_types:
                query = f"""
                EVALUATE SELECTCOLUMNS(
                    FILTER(
                        INFO.COLUMNS(),
                        [Type] <> 3
                            && SEARCH("{escaped_text}",
                                IF(ISBLANK([ExplicitName]), [InferredName], [ExplicitName]),
                                1, 0) > 0
                    ),
                    "type", "column",
                    "Name", IF(ISBLANK([ExplicitName]), [InferredName], [ExplicitName]),
                    "TableID", [TableID],
                    "DataType", [ExplicitDataType],
                    "IsHidden", [IsHidden]
                )
                """
                r = self.validate_and_execute_dax(query)
                if r.get("success"):
                    for row in r.get("rows", []):
                        if "TableID" in row:
                            name = self._get_table_name_from_id(row.get("TableID"))
                            row["Table"] = name or str(row.get("TableID"))
                    results_list.extend(r["rows"])
                else:
                    errors.append({"type": "columns", "error": r.get("error")})

            if "measures" in object_types:
                query = f"""
                EVALUATE SELECTCOLUMNS(
                    FILTER(INFO.MEASURES(), SEARCH("{escaped_text}", [Name], 1, 0) > 0),
                    "type", "measure",
                    "Name", [Name],
                    "TableID", [TableID],
                    "DataType", [DataType],
                    "IsHidden", [IsHidden],
                    "DisplayFolder", [DisplayFolder]
                )
                """
                r = self.validate_and_execute_dax(query)
                if r.get("success"):
                    for row in r.get("rows", []):
                        if "TableID" in row:
                            name = self._get_table_name_from_id(row.get("TableID"))
                            row["Table"] = name or str(row.get("TableID"))
                    results_list.extend(r["rows"])
                else:
                    errors.append({"type": "measures", "error": r.get("error")})

            # Return in a shape compatible with server pagination helpers
            out: Dict[str, Any] = {
                "success": True,
                "rows": results_list,
                "results": results_list,
                "row_count": len(results_list),
                "count": len(results_list),
                "match_mode": "substring (DAX SEARCH, case-insensitive; * and ? stripped)",
            }
            if errors:
                out["errors"] = errors
                out["partial"] = True
            return out
        except Exception as e:
            logger.error(f"Error searching objects: {e}")
            return {
                "success": False,
                "error": engine_message(e),
                "error_type": "query_execution_error",
            }

    def describe_table(self, table_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get comprehensive table description with columns, measures, and relationships.

        B-17: the "consolidated" single-UNION query referenced INFO.* columns
        that do not exist ([TableName], [RowsCount], [ColumnCardinality],
        [FromTable]) and so ALWAYS failed, costing a wasted round-trip plus a
        logged error before falling back. The ID-based sequential path is now
        the default; ``use_consolidated=true`` opts into the rewritten
        consolidated query (Needs live check before making it the default).
        """
        use_consolidated = bool(args.get("use_consolidated", False))

        if use_consolidated:
            return self._describe_table_consolidated(table_name, args)
        else:
            return self._describe_table_sequential(table_name, args)

    def _describe_table_consolidated(self, table_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Consolidated single-query describe (one UNION, results split by QueryType).

        Every branch projects the same ten text columns (values are coerced to
        text with ``& ""``) so UNION's column-count/type rules hold; filters use
        the numeric TableID resolved client-side.
        """
        try:
            columns_page_size = int(args.get("columns_page_size", 50))
            measures_page_size = int(args.get("measures_page_size", 50))
            relationships_page_size = int(args.get("relationships_page_size", 50))

            table_id = self._get_table_id_from_name(table_name)
            if table_id is None:
                return self._describe_table_sequential(table_name, args)
            tid = int(table_id)

            consolidated_query = f"""
            EVALUATE
            UNION(
                SELECTCOLUMNS(
                    FILTER(INFO.TABLES(), [ID] = {tid}),
                    "QueryType", 1,
                    "Name", [Name] & "",
                    "IsHidden", [IsHidden] & "",
                    "Description", [Description] & "",
                    "DataType", "",
                    "Type", "",
                    "_Attr1", [ModifiedTime] & "",
                    "_Attr2", "",
                    "_Attr3", "",
                    "_Attr4", ""
                ),
                TOPN({columns_page_size},
                    SELECTCOLUMNS(
                        FILTER(INFO.COLUMNS(), [TableID] = {tid} && [Type] <> 3),
                        "QueryType", 2,
                        "Name", IF(ISBLANK([ExplicitName]), [InferredName], [ExplicitName]) & "",
                        "IsHidden", [IsHidden] & "",
                        "Description", [Description] & "",
                        "DataType", [ExplicitDataType] & "",
                        "Type", [Type] & "",
                        "_Attr1", "",
                        "_Attr2", "",
                        "_Attr3", "",
                        "_Attr4", ""
                    )
                ),
                TOPN({measures_page_size},
                    SELECTCOLUMNS(
                        FILTER(INFO.MEASURES(), [TableID] = {tid}),
                        "QueryType", 3,
                        "Name", [Name] & "",
                        "IsHidden", [IsHidden] & "",
                        "Description", [Description] & "",
                        "DataType", [DataType] & "",
                        "Type", "",
                        "_Attr1", [DisplayFolder] & "",
                        "_Attr2", "",
                        "_Attr3", "",
                        "_Attr4", ""
                    )
                ),
                SELECTCOLUMNS(
                    FILTER(INFO.RELATIONSHIPS(), [FromTableID] = {tid} || [ToTableID] = {tid}),
                    "QueryType", 4,
                    "Name", [Name] & "",
                    "IsHidden", "",
                    "Description", "",
                    "DataType", "",
                    "Type", "",
                    "_Attr1", [FromTableID] & "",
                    "_Attr2", [FromColumnID] & "",
                    "_Attr3", [ToTableID] & "",
                    "_Attr4", [ToColumnID] & "|" & [IsActive] & "|" & [CrossFilteringBehavior]
                )
            )
            """

            query_result = self.validate_and_execute_dax(consolidated_query)

            if not query_result.get("success"):
                logger.info(
                    f"Consolidated describe failed for {table_name}, falling back to sequential"
                )
                return self._describe_table_sequential(table_name, args)

            result = {
                "success": True,
                "table": table_name,
                "columns": [],
                "measures": [],
                "relationships": [],
                "_performance": {"method": "consolidated", "queries": 1},
            }

            def _clean(row: Dict[str, Any]) -> Dict[str, Any]:
                return {
                    (k[1:-1] if k.startswith("[") and k.endswith("]") else k): v
                    for k, v in row.items()
                }

            for raw in query_result.get("rows", []):
                row = _clean(raw)
                query_type = row.get("QueryType")
                try:
                    query_type = int(query_type) if query_type is not None else None
                except (TypeError, ValueError):
                    query_type = None
                if query_type == 1:
                    result["table_info"] = {
                        "Name": row.get("Name"),
                        "IsHidden": row.get("IsHidden"),
                        "Description": row.get("Description"),
                        "ModifiedTime": row.get("_Attr1"),
                    }
                elif query_type == 2:
                    result["columns"].append(
                        {
                            "Name": row.get("Name"),
                            "IsHidden": row.get("IsHidden"),
                            "Description": row.get("Description"),
                            "DataType": row.get("DataType"),
                            "Type": row.get("Type"),
                        }
                    )
                elif query_type == 3:
                    result["measures"].append(
                        {
                            "Name": row.get("Name"),
                            "IsHidden": row.get("IsHidden"),
                            "Description": row.get("Description"),
                            "DataType": row.get("DataType"),
                            "DisplayFolder": row.get("_Attr1"),
                        }
                    )
                elif query_type == 4:
                    extra = str(row.get("_Attr4") or "").split("|")

                    def _as_int(v: Any) -> Any:
                        try:
                            return int(v)
                        except (TypeError, ValueError):
                            return v

                    from_tid, to_tid = _as_int(row.get("_Attr1")), _as_int(row.get("_Attr3"))
                    from_cid = _as_int(row.get("_Attr2"))
                    to_cid = _as_int(extra[0] if extra else None)
                    result["relationships"].append(
                        {
                            "Name": row.get("Name"),
                            "FromTable": self._get_table_name_from_id(from_tid) or from_tid,
                            "FromColumn": self._get_column_name_from_id(from_cid) or from_cid,
                            "ToTable": self._get_table_name_from_id(to_tid) or to_tid,
                            "ToColumn": self._get_column_name_from_id(to_cid) or to_cid,
                            "IsActive": extra[1] if len(extra) > 1 else None,
                            "CrossFilteringBehavior": extra[2] if len(extra) > 2 else None,
                        }
                    )

            result["relationships"] = result["relationships"][:relationships_page_size]
            result["columns_count"] = len(result["columns"])
            result["measures_count"] = len(result["measures"])
            result["relationships_count"] = len(result["relationships"])
            return result

        except Exception as e:
            logger.info(f"Consolidated table describe for {table_name} failed: {e}")
            return self._describe_table_sequential(table_name, args)

    def _describe_table_sequential(self, table_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        LEGACY: Sequential query approach (kept for fallback compatibility)
        Makes 4 separate queries - slower but more compatible.
        """
        try:
            result = {
                "success": True,
                "table": table_name,
                "columns": [],
                "measures": [],
                "relationships": [],
                "_performance": {"method": "sequential", "queries": 4},
            }

            # Get table basic info
            escaped_table = table_name.replace('"', '""')
            table_query = f'EVALUATE FILTER(INFO.TABLES(), [Name] = "{escaped_table}")'
            table_result = self.validate_and_execute_dax(table_query)
            if table_result.get("success") and table_result.get("rows"):
                result["table_info"] = table_result["rows"][0]

            # Get columns
            columns_page_size = args.get("columns_page_size", 50)
            columns_result = self.execute_info_query(
                "COLUMNS", table_name=table_name, top_n=columns_page_size
            )
            if columns_result.get("success"):
                result["columns"] = columns_result.get("rows", [])
                result["columns_count"] = len(result["columns"])

            # Get measures
            measures_page_size = args.get("measures_page_size", 50)
            measures_result = self.execute_info_query(
                "MEASURES", table_name=table_name, top_n=measures_page_size
            )
            if measures_result.get("success"):
                result["measures"] = measures_result.get("rows", [])
                result["measures_count"] = len(result["measures"])

            # Get relationships (both from and to this table)
            relationships_page_size = args.get("relationships_page_size", 50)
            relationships_result = self.execute_info_query("RELATIONSHIPS")
            if relationships_result.get("success"):
                all_rels = relationships_result.get("rows", [])
                # Filter for relationships involving this table
                table_rels = [
                    rel
                    for rel in all_rels
                    if rel.get("FromTable") == table_name
                    or rel.get("ToTable") == table_name
                    or rel.get("[FromTable]") == table_name
                    or rel.get("[ToTable]") == table_name
                ]
                result["relationships"] = table_rels[:relationships_page_size]
                result["relationships_count"] = len(result["relationships"])

            return result

        except Exception as e:
            logger.error(f"Error describing table {table_name}: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Error describing table: {engine_message(e)}",
                "table": table_name,
            }

    def _is_table_expression(self, query: str) -> bool:
        """Check if query is a table expression."""
        table_keywords = [
            "SELECTCOLUMNS",
            "ADDCOLUMNS",
            "SUMMARIZE",
            "FILTER",
            "VALUES",
            "ALL",
            "INFO.",
            "TOPN",
            "SAMPLE",
            "SUMMARIZECOLUMNS",
        ]
        return any(kw in query.upper() for kw in table_keywords)

    def list_udfs(self) -> Dict[str, Any]:
        """User-defined DAX functions via DMV (the AMO object model cannot see them).

        Verified read-only on Desktop CL 1702 (2026-09-22): ``$SYSTEM.TMSCHEMA_FUNCTIONS``
        and ``INFO.USERDEFINEDFUNCTIONS()`` both return every UDF while AMO
        ``Model.Functions.Count`` is 0 with both 19.101 and 19.84 — so this is
        the read path for function ops. Returns ``{success, rows: [{ID, Name,
        Description, Expression, IsHidden, State, ErrorMessage, ModifiedTime,
        ...}], row_count, source}``; keys are unbracketed.
        """
        dmv = self.execute_dmv_query("SELECT * FROM $SYSTEM.TMSCHEMA_FUNCTIONS")
        if dmv.get("success"):
            rows = dmv.get("rows") or []
            return {
                "success": True,
                "rows": rows,
                "row_count": len(rows),
                "source": "TMSCHEMA_FUNCTIONS",
            }
        res = self.validate_and_execute_dax("EVALUATE INFO.USERDEFINEDFUNCTIONS()", 0)
        if not res.get("success"):
            return {
                "success": False,
                "error": res.get("error") or dmv.get("error"),
                "error_type": res.get("error_type", "query_execution_error"),
                "dmv_error": dmv.get("error"),
            }
        rows = [
            {
                (k[1:-1] if isinstance(k, str) and k.startswith("[") and k.endswith("]") else k): v
                for k, v in row.items()
            }
            for row in res.get("rows") or []
        ]
        return {
            "success": True,
            "rows": rows,
            "row_count": len(rows),
            "source": "INFO.USERDEFINEDFUNCTIONS",
        }

    def list_measures(self) -> Dict[str, Any] | list[dict]:
        res = self.execute_info_query("MEASURES")
        return res.get("rows", []) if isinstance(res, dict) else []

    def list_columns(self, table: Optional[str] = None) -> Dict[str, Any] | list[dict]:
        res = (
            self.execute_info_query("COLUMNS", table_name=table)
            if table
            else self.execute_info_query("COLUMNS")
        )
        return res.get("rows", []) if isinstance(res, dict) else []

    def list_tables(self) -> Dict[str, Any] | list[dict]:
        res = self.execute_info_query("TABLES")
        return res.get("rows", []) if isinstance(res, dict) else []

    def list_relationships(self) -> Dict[str, Any] | list[dict]:
        res = self.execute_info_query("RELATIONSHIPS")
        return res.get("rows", []) if isinstance(res, dict) else []
