"""
Column Usage Analyzer

Provides complete mapping between columns and measures:
1. Column → Measures: What measures use a specific Table[Column]
2. Measure → Columns: What columns a measure references

This enables answering questions like:
- "Which measures use columns from table X?"
- "What columns does measure Y depend on?"
- "List all measures that use any column from these tables"
"""

import logging
from typing import Dict, List, Set, Tuple, Optional, Any
from dataclasses import dataclass, field
from collections import defaultdict

from core.dax.dax_reference_parser import DaxReferenceIndex, parse_dax_references, scan_references

logger = logging.getLogger(__name__)


def _table_only_refs(expression: str, ref_index: DaxReferenceIndex) -> List[str]:
    """Tables referenced ON THEIR OWN (``REMOVEFILTERS('T')``, ``COUNTROWS(T)``).

    ``parse_dax_references()['tables']`` also lists the table of every qualified
    ``T[C]`` reference; expanding those to all columns marked every column of any
    measure-referenced table as used (WP8 / G-19 false negative).
    """
    if not expression:
        return []
    known = ref_index.normalized_tables() if ref_index.table_names else None
    try:
        refs = scan_references(expression, known_tables=known)
    except Exception:  # noqa: BLE001 - never fail the mapping on one body
        return []
    return sorted({r.table for r in refs if r.kind == "table" and r.table})


@dataclass
class ColumnUsageResult:
    """Result structure for column usage analysis"""

    # Column to measures mapping: "Table[Column]" -> list of measures that use it
    column_to_measures: Dict[str, List[Dict[str, str]]] = field(default_factory=dict)

    # Measure to columns mapping: "Table[Measure]" -> list of columns it uses
    measure_to_columns: Dict[str, List[Dict[str, str]]] = field(default_factory=dict)

    # Columns used in relationships (key columns)
    relationship_columns: Set[str] = field(default_factory=set)

    # Columns used as SortByColumn targets
    sort_by_columns: Set[str] = field(default_factory=set)

    # Columns in field parameter tables (internal columns like Fields, Order, display)
    field_parameter_columns: Set[str] = field(default_factory=set)

    # Columns referenced by field parameter NAMEOF expressions
    field_parameter_ref_columns: Set[str] = field(default_factory=set)

    # Columns referenced in RLS filter expressions
    rls_columns: Set[str] = field(default_factory=set)

    # Statistics
    total_columns_analyzed: int = 0
    total_measures_analyzed: int = 0
    columns_with_usage: int = 0
    columns_without_usage: int = 0
    columns_used_by_relationships: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            "column_to_measures": self.column_to_measures,
            "measure_to_columns": self.measure_to_columns,
            "relationship_columns": list(self.relationship_columns),
            "sort_by_columns": list(self.sort_by_columns),
            "field_parameter_columns": list(self.field_parameter_columns),
            "field_parameter_ref_columns": list(self.field_parameter_ref_columns),
            "rls_columns": list(self.rls_columns),
            "statistics": {
                "total_columns_analyzed": self.total_columns_analyzed,
                "total_measures_analyzed": self.total_measures_analyzed,
                "columns_with_usage": self.columns_with_usage,
                "columns_without_usage": self.columns_without_usage,
                "columns_used_by_relationships": self.columns_used_by_relationships,
            },
        }


def _normalize_column_key(table: str, column: str) -> str:
    """
    Normalize a column key for case-insensitive matching.

    Strips whitespace and lowercases both table and column names to ensure
    consistent matching between DMV results and DAX-parsed references.
    """
    return f"{table.strip().lower()}[{column.strip().lower()}]"


def _column_row_name(row: Dict[str, Any]) -> str:
    """Column name of an INFO.COLUMNS() row.

    Review G-19: the DMV exposes ``ExplicitName`` (and ``InferredName``) — the
    repo's own query executor notes that — not ``Name``; reading only ``Name``
    left every column out of the index. Accept all shapes.
    """
    for key in (
        "Name",
        "[Name]",
        "ExplicitName",
        "[ExplicitName]",
        "InferredName",
        "[InferredName]",
    ):
        v = row.get(key)
        if v:
            return str(v)
    return ""


def _with_column_names(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for r in rows:
        if not (r.get("Name") or r.get("[Name]")):
            name = _column_row_name(r)
            if name:
                r = dict(r)
                r["Name"] = name
        out.append(r)
    return out


def _make_display_key(table: str, column: str) -> str:
    """
    Create a display-friendly column key (preserves original case).
    """
    return f"{table.strip()}[{column.strip()}]"


class ColumnUsageAnalyzer:
    """
    Analyzes column usage across measures in a Power BI model.

    Provides bidirectional mapping:
    - Column → Measures: Find all measures that reference a column
    - Measure → Columns: Find all columns referenced by a measure
    """

    def __init__(self, query_executor):
        """
        Initialize the analyzer with a query executor.

        Args:
            query_executor: Query executor for DMV queries
        """
        self.query_executor = query_executor
        self._ref_index: Optional[DaxReferenceIndex] = None
        self._cached_result: Optional[ColumnUsageResult] = None
        self._cache_valid = False
        self._cached_include_dax = False

    def _ensure_reference_index(self) -> DaxReferenceIndex:
        """Lazily build the reference index for DAX parsing"""
        if self._ref_index is not None:
            return self._ref_index

        measures_result = self.query_executor.execute_info_query("MEASURES")
        columns_result = self.query_executor.execute_info_query("COLUMNS")

        measure_rows = measures_result.get("rows", []) if measures_result.get("success") else []
        column_rows = _with_column_names(
            columns_result.get("rows", []) if columns_result.get("success") else []
        )

        self._ref_index = DaxReferenceIndex(measure_rows, column_rows)
        return self._ref_index

    def invalidate_cache(self) -> None:
        """Invalidate the cached analysis results"""
        self._cached_result = None
        self._cache_valid = False
        self._cached_include_dax = False
        self._ref_index = None
        logger.debug("Column usage cache invalidated")

    def build_complete_mapping(
        self, force_refresh: bool = False, include_dax: bool = False
    ) -> ColumnUsageResult:
        """
        Build complete bidirectional mapping between columns and measures.

        Args:
            force_refresh: If True, rebuild even if cache exists
            include_dax: If True, include DAX expressions for each measure (default: False for size optimization)

        Returns:
            ColumnUsageResult with complete mappings
        """
        # Return cached result if valid — and still for the SAME model generation
        # (G-19: the cache never expired after live TOM edits). If include_dax
        # is requested but cache was built without DAX, force refresh.
        gen = _model_generation()
        if self._cache_valid and gen != getattr(self, "_cached_generation", gen):
            self.invalidate_cache()
        if self._cache_valid and self._cached_result and not force_refresh:
            if include_dax and not self._cached_include_dax:
                logger.debug("Cache hit but include_dax requested; rebuilding with DAX")
            else:
                logger.debug("Returning cached column usage mapping")
                return self._cached_result

        logger.info("Building complete column-measure mapping...")

        # Get all measures
        measures_result = self.query_executor.execute_info_query("MEASURES")
        if not measures_result.get("success"):
            logger.error(f"Failed to get measures: {measures_result.get('error')}")
            return ColumnUsageResult()

        # Get all columns
        columns_result = self.query_executor.execute_info_query("COLUMNS")
        if not columns_result.get("success"):
            logger.error(f"Failed to get columns: {columns_result.get('error')}")
            return ColumnUsageResult()

        all_measures = measures_result.get("rows", [])
        all_columns = _with_column_names(columns_result.get("rows", []))

        # Get relationships to mark key columns as "used"
        relationships_result = self.query_executor.execute_info_query("RELATIONSHIPS")
        all_relationships = (
            relationships_result.get("rows", []) if relationships_result.get("success") else []
        )

        # Build reference index
        ref_index = self._ensure_reference_index()

        # Initialize result
        result = ColumnUsageResult()
        result.total_measures_analyzed = len(all_measures)
        result.total_columns_analyzed = len(all_columns)

        # Track relationship columns (normalized for matching, display for output)
        relationship_normalized: Set[str] = set()
        for rel in all_relationships:
            from_table = rel.get("FromTable", "") or rel.get("[FromTable]", "")
            from_col = rel.get("FromColumn", "") or rel.get("[FromColumn]", "")
            to_table = rel.get("ToTable", "") or rel.get("[ToTable]", "")
            to_col = rel.get("ToColumn", "") or rel.get("[ToColumn]", "")

            if from_table and from_col:
                display_key = _make_display_key(from_table, from_col)
                result.relationship_columns.add(display_key)
                relationship_normalized.add(_normalize_column_key(from_table, from_col))
            if to_table and to_col:
                display_key = _make_display_key(to_table, to_col)
                result.relationship_columns.add(display_key)
                relationship_normalized.add(_normalize_column_key(to_table, to_col))

        result.columns_used_by_relationships = len(result.relationship_columns)

        # Track SortByColumn targets — columns used as sort-by for other columns
        # Build column ID -> (table, name) mapping for resolving SortByColumnID
        column_id_to_info: Dict[Any, Tuple[str, str]] = {}
        for col in all_columns:
            col_id = col.get("ID") or col.get("[ID]")
            col_table = col.get("Table", "") or col.get("[Table]", "")
            col_name = col.get("Name", "") or col.get("[Name]", "")
            if col_id is not None and col_table and col_name:
                column_id_to_info[col_id] = (col_table, col_name)

        for col in all_columns:
            sort_by_id = col.get("SortByColumnID") or col.get("[SortByColumnID]")
            if sort_by_id is not None and sort_by_id in column_id_to_info:
                sort_table, sort_name = column_id_to_info[sort_by_id]
                display_key = _make_display_key(sort_table, sort_name)
                result.sort_by_columns.add(display_key)

        # Track field parameter tables and their referenced columns via NAMEOF in partition sources
        try:
            partition_query = """
            EVALUATE
            SELECTCOLUMNS(
                INFO.PARTITIONS(),
                "Table", [TableName],
                "Source", [QueryDefinition]
            )
            """
            partition_result = self.query_executor.validate_and_execute_dax(
                partition_query, top_n=500
            )
            if partition_result.get("success"):
                # Lexer-based (2026-10-03 review D-27 of the 09-22 ledger): a regex over the raw
                # partition text also matched refs inside string literals and comments and
                # mishandled ``''`` / ``]]`` escapes.
                from core.dax.dax_reference_parser import scan_references

                for row in partition_result.get("rows", []):
                    table_name = row.get("Table", "")
                    source = row.get("Source", "")
                    if source and table_name and "NAMEOF" in source:
                        # Mark all columns of this field parameter table as used
                        for c in all_columns:
                            ct = c.get("Table", "") or c.get("[Table]", "")
                            cn = c.get("Name", "") or c.get("[Name]", "")
                            if ct == table_name and cn:
                                result.field_parameter_columns.add(_make_display_key(ct, cn))
                        # Columns referenced via NAMEOF(), plus any other qualified column
                        # reference in the expression (SELECTEDVALUE / CALCULATE lookups), except
                        # self-references (columns of the field parameter table itself).
                        for ref in scan_references(source):
                            if ref.kind != "qualified" or not ref.table:
                                continue
                            ref_table_s = ref.table.strip()
                            if ref.function != "NAMEOF" and ref_table_s == table_name:
                                continue
                            result.field_parameter_ref_columns.add(
                                _make_display_key(ref_table_s, ref.name.strip())
                            )
        except Exception as e:
            logger.debug(f"Could not fetch partition data for field parameter detection: {e}")

        # Track RLS filter expression columns
        try:
            from core.infrastructure.connection_state import connection_state

            rls_manager = connection_state.rls_manager
            if rls_manager:
                roles_result = rls_manager.list_roles()
                if roles_result.get("success"):
                    for role in roles_result.get("roles", []):
                        for perm in role.get("table_permissions", []):
                            filter_expr = perm.get("filter_expression", "")
                            perm_table = perm.get("table", "")
                            if filter_expr and perm_table:
                                # Parse DAX to find column references
                                refs = parse_dax_references(filter_expr, ref_index)
                                for ref_table, ref_column in refs.get("columns", []):
                                    if ref_table and ref_column:
                                        result.rls_columns.add(
                                            _make_display_key(ref_table, ref_column)
                                        )
                                    elif not ref_table and ref_column:
                                        # Unqualified ref — on the permission's table
                                        result.rls_columns.add(
                                            _make_display_key(perm_table, ref_column)
                                        )
                                # Also mark all columns in tables with RLS as functionally required
                                for c in all_columns:
                                    ct = c.get("Table", "") or c.get("[Table]", "")
                                    cn = c.get("Name", "") or c.get("[Name]", "")
                                    if ct == perm_table and cn:
                                        result.rls_columns.add(_make_display_key(ct, cn))
        except Exception as e:
            logger.debug(f"Could not fetch RLS data: {e}")

        # Initialize column_to_measures with all columns (even unused ones)
        # Use normalized keys for matching, but store under display keys
        all_column_keys: Set[str] = set()
        normalized_to_display: Dict[str, str] = {}  # Maps normalized key -> display key

        for col in all_columns:
            col_table = col.get("Table", "") or col.get("[Table]", "")
            col_name = col.get("Name", "") or col.get("[Name]", "")
            if col_table and col_name:
                display_key = _make_display_key(col_table, col_name)
                norm_key = _normalize_column_key(col_table, col_name)
                all_column_keys.add(display_key)
                normalized_to_display[norm_key] = display_key
                result.column_to_measures[display_key] = []

        # Build a lookup of table name (lowercase) -> list of (norm_key, display_key) for all columns
        # This is used when a DAX function references an entire table (e.g., REMOVEFILTERS('d Period'))
        table_to_column_keys: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
        for col in all_columns:
            col_table = col.get("Table", "") or col.get("[Table]", "")
            col_name = col.get("Name", "") or col.get("[Name]", "")
            if col_table and col_name:
                norm_key = _normalize_column_key(col_table, col_name)
                display_key = _make_display_key(col_table, col_name)
                table_to_column_keys[col_table.strip().lower()].append((norm_key, display_key))

        # Review G-19: calculated columns and UDF bodies reference columns too
        # (they used to be ignored). Calc columns come from INFO.COLUMNS
        # Expression; UDFs from the functions DMV. Their references are
        # recorded as referrers like measures ("calc column:" / "udf:").
        extra_carriers: List[Dict[str, Any]] = []
        for col in all_columns:
            expr = col.get("Expression") or col.get("[Expression]")
            if expr:
                extra_carriers.append(
                    {
                        "Table": col.get("Table", "") or col.get("[Table]", ""),
                        "Name": f"calc column: {_column_row_name(col)}",
                        "Expression": expr,
                    }
                )
        try:
            udfs = (
                self.query_executor.list_udfs() if hasattr(self.query_executor, "list_udfs") else {}
            )
            for u in (udfs or {}).get("rows", []) or []:
                if u.get("Expression"):
                    extra_carriers.append(
                        {
                            "Table": "(functions)",
                            "Name": f"udf: {u.get('Name')}",
                            "Expression": u.get("Expression"),
                        }
                    )
        except Exception as e:  # noqa: BLE001 - optional source
            logger.debug(f"UDF list unavailable: {e}")

        # Build measure_to_columns mapping
        # Also populate column_to_measures as we go
        for m in list(all_measures) + extra_carriers:
            m_table = m.get("Table", "") or m.get("[Table]", "")
            m_name = m.get("Name", "") or m.get("[Name]", "")
            m_expression = m.get("Expression", "") or m.get("[Expression]", "")
            m_folder = m.get("DisplayFolder", "") or m.get("[DisplayFolder]", "") or ""

            if not m_table or not m_name:
                continue

            measure_key = _make_display_key(m_table, m_name)

            # Parse DAX to find column references
            refs = parse_dax_references(m_expression, ref_index)
            referenced_columns = refs.get("columns", [])

            # Also get table-only references (e.g., REMOVEFILTERS('d Period'))
            # and expand them to all columns of those tables
            referenced_tables = _table_only_refs(m_expression, ref_index)
            table_only_columns = []
            for ref_table in referenced_tables:
                ref_table_lower = ref_table.strip().lower()
                if ref_table_lower in table_to_column_keys:
                    for _, col_display_key in table_to_column_keys[ref_table_lower]:
                        # Parse display_key back to table, column
                        if "[" in col_display_key:
                            ct = col_display_key.split("[")[0]
                            cn = col_display_key.split("[")[1].rstrip("]")
                            table_only_columns.append((ct, cn))

            # Combine explicit column refs and table-level refs (deduplicate)
            all_referenced = list(referenced_columns)
            existing_norm = {_normalize_column_key(t, c) for t, c in referenced_columns}
            for ct, cn in table_only_columns:
                nk = _normalize_column_key(ct, cn)
                if nk not in existing_norm:
                    all_referenced.append((ct, cn))
                    existing_norm.add(nk)

            # Store measure -> columns mapping
            result.measure_to_columns[measure_key] = [
                {"table": col_table.strip(), "column": col_name.strip()}
                for col_table, col_name in all_referenced
            ]

            # Update column -> measures mapping
            measure_info = {"table": m_table, "measure": m_name, "display_folder": m_folder}

            # Include DAX expression if requested
            if include_dax and m_expression:
                measure_info["dax"] = m_expression

            for col_table, col_name in all_referenced:
                # Use normalized key for lookup, store under display key
                norm_key = _normalize_column_key(col_table, col_name)
                if norm_key in normalized_to_display:
                    # Found matching column from DMV - use its display key
                    display_key = normalized_to_display[norm_key]
                    result.column_to_measures[display_key].append(measure_info)
                else:
                    # Column not in COLUMNS DMV (e.g., calculated columns or parsing artifacts)
                    # Store under a display key derived from the parsed reference
                    display_key = _make_display_key(col_table, col_name)
                    if display_key in result.column_to_measures:
                        result.column_to_measures[display_key].append(measure_info)
                    else:
                        result.column_to_measures[display_key] = [measure_info]
                    # Also add to normalized mapping for future lookups
                    normalized_to_display[norm_key] = display_key

        # Calculate statistics - a column is "used" if referenced by measures, relationships,
        # sort-by, field parameters, or RLS
        # O(C) inverse lookup (the nested scan was O(C^2), G-19)
        display_to_normalized = {disp: norm for norm, disp in normalized_to_display.items()}
        used_by_measures_normalized = {
            display_to_normalized[k]
            for k, v in result.column_to_measures.items()
            if v and k in display_to_normalized
        }
        # Build normalized sets for new categories
        sort_by_norm = {
            _normalize_column_key(k.split("[")[0], k.split("[")[1].rstrip("]"))
            for k in result.sort_by_columns
            if "[" in k
        }
        fp_cols_norm = {
            _normalize_column_key(k.split("[")[0], k.split("[")[1].rstrip("]"))
            for k in result.field_parameter_columns
            if "[" in k
        }
        fp_refs_norm = {
            _normalize_column_key(k.split("[")[0], k.split("[")[1].rstrip("]"))
            for k in result.field_parameter_ref_columns
            if "[" in k
        }
        rls_norm = {
            _normalize_column_key(k.split("[")[0], k.split("[")[1].rstrip("]"))
            for k in result.rls_columns
            if "[" in k
        }
        all_used_normalized = (
            used_by_measures_normalized
            | relationship_normalized
            | sort_by_norm
            | fp_cols_norm
            | fp_refs_norm
            | rls_norm
        )
        result.columns_with_usage = sum(
            1 for norm_k in normalized_to_display.keys() if norm_k in all_used_normalized
        )
        result.columns_without_usage = result.total_columns_analyzed - result.columns_with_usage

        # Cache the result
        self._cached_result = result
        self._cache_valid = True
        self._cached_include_dax = include_dax
        self._cached_generation = gen

        logger.info(
            f"Column usage mapping complete: {result.total_columns_analyzed} columns, "
            f"{result.total_measures_analyzed} measures, {result.columns_with_usage} columns used"
        )

        return result

    def get_measures_using_column(
        self, table: str, column: str, force_refresh: bool = False, include_dax: bool = False
    ) -> Dict[str, Any]:
        """
        Get all measures that use a specific column.

        Args:
            table: Table name
            column: Column name
            force_refresh: Force rebuild of cache
            include_dax: Include DAX expressions in output (default: False for size)

        Returns:
            Dictionary with list of measures using this column
        """
        mapping = self.build_complete_mapping(force_refresh, include_dax)
        col_key = f"{table}[{column}]"

        measures = mapping.column_to_measures.get(col_key, [])

        return {
            "success": True,
            "column": {"table": table, "column": column},
            "measures": measures,
            "measure_count": len(measures),
        }

    def get_columns_used_by_measure(
        self, table: str, measure: str, force_refresh: bool = False, include_dax: bool = False
    ) -> Dict[str, Any]:
        """
        Get all columns used by a specific measure.

        Args:
            table: Table name containing the measure
            measure: Measure name
            force_refresh: Force rebuild of cache
            include_dax: Include DAX expression in output (default: False for size)

        Returns:
            Dictionary with list of columns used by this measure
        """
        mapping = self.build_complete_mapping(force_refresh, include_dax)
        measure_key = f"{table}[{measure}]"

        columns = mapping.measure_to_columns.get(measure_key, [])

        # Get DAX expression for this measure if available
        dax_expression = None
        if include_dax:
            # Find the DAX from any column that references this measure
            for col_measures in mapping.column_to_measures.values():
                for m in col_measures:
                    if m.get("table") == table and m.get("measure") == measure:
                        dax_expression = m.get("dax")
                        break
                if dax_expression:
                    break

        result = {
            "success": True,
            "measure": {"table": table, "measure": measure},
            "columns": columns,
            "column_count": len(columns),
        }

        if dax_expression:
            result["dax"] = dax_expression

        return result

    def get_measures_using_tables(
        self,
        tables: List[str],
        force_refresh: bool = False,
        group_by: str = "table",
        include_dax: bool = False,
    ) -> Dict[str, Any]:
        """
        Get all measures that use columns from specified tables.

        This is the primary use case: "Which measures use columns from these tables?"

        Args:
            tables: List of table names to check
            force_refresh: Force rebuild of cache
            group_by: How to group results - "table", "column", "measure", or "flat"
            include_dax: Include DAX expressions in output (default: False for size)

        Returns:
            Dictionary with measures grouped as requested
        """
        mapping = self.build_complete_mapping(force_refresh, include_dax)

        # Normalize table names for comparison
        tables_lower = {t.lower() for t in tables}

        if group_by == "table":
            # Group by table: {table: {column: [measures]}}
            result_by_table: Dict[str, Dict[str, List[Dict]]] = defaultdict(
                lambda: defaultdict(list)
            )

            for col_key, measures in mapping.column_to_measures.items():
                if not measures:
                    continue
                # Parse column key
                if "[" in col_key:
                    col_table = col_key.split("[")[0]
                    col_name = col_key.split("[")[1].rstrip("]")

                    if col_table.lower() in tables_lower:
                        result_by_table[col_table][col_name] = measures

            # Convert defaultdict to regular dict
            result = {table: dict(columns) for table, columns in result_by_table.items()}

            # Calculate summary
            total_measures = set()
            total_columns = 0
            for table_data in result.values():
                total_columns += len(table_data)
                for measures in table_data.values():
                    for m in measures:
                        total_measures.add(f"{m['table']}[{m['measure']}]")

            return {
                "success": True,
                "tables_requested": tables,
                "group_by": "table",
                "results": result,
                "summary": {
                    "tables_found": len(result),
                    "columns_with_usage": total_columns,
                    "unique_measures": len(total_measures),
                },
            }

        elif group_by == "column":
            # Group by column: {"Table[Column]": [measures]}
            result: Dict[str, List[Dict]] = {}

            for col_key, measures in mapping.column_to_measures.items():
                if not measures:
                    continue
                if "[" in col_key:
                    col_table = col_key.split("[")[0]
                    if col_table.lower() in tables_lower:
                        result[col_key] = measures

            return {
                "success": True,
                "tables_requested": tables,
                "group_by": "column",
                "results": result,
                "summary": {
                    "columns_with_usage": len(result),
                    "total_measure_references": sum(len(m) for m in result.values()),
                },
            }

        elif group_by == "measure":
            # Group by measure: {"Table[Measure]": [columns from requested tables]}
            result: Dict[str, List[Dict]] = defaultdict(list)

            for measure_key, columns in mapping.measure_to_columns.items():
                for col in columns:
                    if col["table"].lower() in tables_lower:
                        result[measure_key].append(col)

            # Filter to only measures that actually use these tables
            result = {k: v for k, v in result.items() if v}

            return {
                "success": True,
                "tables_requested": tables,
                "group_by": "measure",
                "results": dict(result),
                "summary": {
                    "measures_using_tables": len(result),
                    "total_column_references": sum(len(c) for c in result.values()),
                },
            }

        else:  # flat
            # Flat list of all unique measures
            all_measures: Set[str] = set()
            measure_details: List[Dict] = []

            for col_key, measures in mapping.column_to_measures.items():
                if not measures:
                    continue
                if "[" in col_key:
                    col_table = col_key.split("[")[0]
                    if col_table.lower() in tables_lower:
                        for m in measures:
                            m_key = f"{m['table']}[{m['measure']}]"
                            if m_key not in all_measures:
                                all_measures.add(m_key)
                                measure_details.append(m)

            return {
                "success": True,
                "tables_requested": tables,
                "group_by": "flat",
                "measures": measure_details,
                "summary": {"unique_measures": len(measure_details)},
            }

    def get_unused_columns(
        self,
        tables: Optional[List[str]] = None,
        force_refresh: bool = False,
        include_dax: bool = False,
    ) -> Dict[str, Any]:
        """
        Get columns that are not referenced by any measure AND not used in relationships.
        Returns COMPLETE analysis - no other tools needed.

        Args:
            tables: Optional list of tables to filter (None = all tables)
            force_refresh: Force rebuild of cache
            include_dax: Include DAX (not applicable here but for consistency)

        Returns:
            Dictionary with complete unused columns analysis
        """
        mapping = self.build_complete_mapping(force_refresh, include_dax)

        tables_lower = {t.lower() for t in tables} if tables else None

        # Build normalized sets for case-insensitive matching
        def _normalize_set(display_keys: Set[str]) -> Set[str]:
            return {
                _normalize_column_key(k.split("[")[0], k.split("[")[1].rstrip("]"))
                for k in display_keys
                if "[" in k
            }

        relationship_normalized = _normalize_set(mapping.relationship_columns)
        sort_by_normalized = _normalize_set(mapping.sort_by_columns)
        field_param_cols_normalized = _normalize_set(mapping.field_parameter_columns)
        field_param_refs_normalized = _normalize_set(mapping.field_parameter_ref_columns)
        rls_normalized = _normalize_set(mapping.rls_columns)

        # Combine all "structurally used" columns into one set for quick lookup
        structurally_used = (
            relationship_normalized
            | sort_by_normalized
            | field_param_cols_normalized
            | field_param_refs_normalized
            | rls_normalized
        )

        # Track ALL columns in scope for complete picture
        all_columns_in_scope: List[Dict[str, str]] = []
        used_by_measures: List[Dict[str, str]] = []
        used_by_relationships_only: List[Dict[str, str]] = []
        used_by_sort_by: List[Dict[str, str]] = []
        used_by_field_params: List[Dict[str, str]] = []
        used_by_rls: List[Dict[str, str]] = []
        unused_columns: List[Dict[str, str]] = []

        for col_key, measures in mapping.column_to_measures.items():
            if "[" not in col_key:
                continue

            col_table = col_key.split("[")[0]
            col_name = col_key.split("[")[1].rstrip("]")

            # Filter by tables if specified
            if tables_lower and col_table.lower() not in tables_lower:
                continue

            col_info = {"table": col_table, "column": col_name}
            all_columns_in_scope.append(col_info)

            # Use normalized key for matching
            norm_key = _normalize_column_key(col_table, col_name)

            if measures:
                # Used by measures (highest priority)
                used_by_measures.append(col_info)
            elif norm_key not in structurally_used:
                # Truly unused (by the MODEL — see scope below)
                unused_columns.append(col_info)
            elif norm_key in relationship_normalized:
                used_by_relationships_only.append(col_info)
            elif norm_key in sort_by_normalized:
                used_by_sort_by.append(col_info)
            elif norm_key in field_param_cols_normalized or norm_key in field_param_refs_normalized:
                used_by_field_params.append(col_info)
            elif norm_key in rls_normalized:
                used_by_rls.append(col_info)
            else:
                # Truly unused
                unused_columns.append(col_info)

        # Helper to group columns by table
        def _group_by_table(cols: List[Dict[str, str]]) -> Dict[str, List[str]]:
            grouped: Dict[str, List[str]] = defaultdict(list)
            for col in cols:
                grouped[col["table"]].append(col["column"])
            return dict(grouped)

        unused_by_table = _group_by_table(unused_columns)
        used_by_measures_by_table = _group_by_table(used_by_measures)
        rel_only_by_table = _group_by_table(used_by_relationships_only)
        sort_by_by_table = _group_by_table(used_by_sort_by)
        field_params_by_table = _group_by_table(used_by_field_params)
        rls_by_table = _group_by_table(used_by_rls)

        return {
            "success": True,
            # G-19: the live model knows nothing about report bindings (visuals,
            # slicers, filters, bookmarks): "unused" here means "not referenced by
            # the model". For deletion decisions use get_unused_columns_pbip /
            # 07_PBIP_Operations query_unused, which also walk the reports.
            "scope": "model-only",
            "scope_note": (
                "Columns used only by report visuals/filters/bookmarks are listed as unused "
                "here; run get_unused_columns_pbip (offline, report-aware) before deleting."
            ),
            "tables_filter": tables,
            "unused_columns": unused_columns,
            "unused_by_table": unused_by_table,
            "used_by_measures": used_by_measures,
            "used_by_measures_by_table": used_by_measures_by_table,
            "used_by_relationships_only": used_by_relationships_only,
            "used_by_relationships_only_by_table": rel_only_by_table,
            "used_by_sort_by": used_by_sort_by,
            "used_by_sort_by_by_table": sort_by_by_table,
            "used_by_field_params": used_by_field_params,
            "used_by_field_params_by_table": field_params_by_table,
            "used_by_rls": used_by_rls,
            "used_by_rls_by_table": rls_by_table,
            "summary": {
                "total_columns_analyzed": len(all_columns_in_scope),
                "used_by_measures": len(used_by_measures),
                "used_by_relationships_only": len(used_by_relationships_only),
                "used_by_sort_by": len(used_by_sort_by),
                "used_by_field_params": len(used_by_field_params),
                "used_by_rls": len(used_by_rls),
                "unused": len(unused_columns),
                "tables_with_unused": len(unused_by_table),
            },
        }

    def get_full_mapping(
        self, force_refresh: bool = False, include_dax: bool = False
    ) -> Dict[str, Any]:
        """
        Get the complete column-measure mapping.

        Returns both directions:
        - column_to_measures: What measures use each column
        - measure_to_columns: What columns each measure uses

        Args:
            force_refresh: Force rebuild of cache
            include_dax: Include DAX expressions in output (default: False for size)

        Returns:
            Complete mapping dictionary
        """
        mapping = self.build_complete_mapping(force_refresh, include_dax)

        return {
            "success": True,
            "column_to_measures": mapping.column_to_measures,
            "measure_to_columns": mapping.measure_to_columns,
            "statistics": {
                "total_columns": mapping.total_columns_analyzed,
                "total_measures": mapping.total_measures_analyzed,
                "columns_with_usage": mapping.columns_with_usage,
                "columns_without_usage": mapping.columns_without_usage,
            },
        }

    def export_to_csv(
        self,
        tables: Optional[List[str]] = None,
        output_path: Optional[str] = None,
        include_dax: bool = False,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        """
        Export column-measure mapping to CSV files for Excel.

        Creates two CSV files:
        1. column_to_measures.csv: Table, Column, Measure_Table, Measure_Name, Display_Folder[, DAX]
        2. measure_to_columns.csv: Measure_Table, Measure_Name, Column_Table, Column_Name[, DAX]

        Args:
            tables: Optional list of tables to filter (None = all tables)
            output_path: Directory to save CSV files (default: exports/)
            include_dax: Include DAX column (default: False)
            force_refresh: Force rebuild of cache

        Returns:
            Dictionary with file paths and statistics
        """
        import csv
        import os
        from datetime import datetime

        mapping = self.build_complete_mapping(force_refresh, include_dax)

        # Determine output directory
        if output_path is None:
            output_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "exports"
            )

        os.makedirs(output_path, exist_ok=True)

        # Filter by tables if specified
        tables_lower = {t.lower() for t in tables} if tables else None

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Export column_to_measures
        col_to_msr_path = os.path.join(output_path, f"column_to_measures_{timestamp}.csv")
        col_to_msr_count = 0

        with open(col_to_msr_path, "w", newline="", encoding="utf-8") as f:
            headers = [
                "Column_Table",
                "Column_Name",
                "Measure_Table",
                "Measure_Name",
                "Display_Folder",
            ]
            if include_dax:
                headers.append("DAX")
            writer = csv.writer(f)
            writer.writerow(headers)

            for col_key, measures in mapping.column_to_measures.items():
                if not measures:
                    continue

                if "[" in col_key:
                    col_table = col_key.split("[")[0]
                    col_name = col_key.split("[")[1].rstrip("]")

                    # Filter by tables
                    if tables_lower and col_table.lower() not in tables_lower:
                        continue

                    for m in measures:
                        row = [
                            col_table,
                            col_name,
                            m.get("table", ""),
                            m.get("measure", ""),
                            m.get("display_folder", ""),
                        ]
                        if include_dax:
                            row.append(m.get("dax", ""))
                        writer.writerow(row)
                        col_to_msr_count += 1

        # Build a lookup for measure display folders from column_to_measures data
        measure_folders: Dict[str, str] = {}
        for col_measures in mapping.column_to_measures.values():
            for m in col_measures:
                msr_key = f"{m.get('table', '')}[{m.get('measure', '')}]"
                if msr_key not in measure_folders:
                    measure_folders[msr_key] = m.get("display_folder", "")

        # Export measure_to_columns
        msr_to_col_path = os.path.join(output_path, f"measure_to_columns_{timestamp}.csv")
        msr_to_col_count = 0

        with open(msr_to_col_path, "w", newline="", encoding="utf-8") as f:
            headers = [
                "Measure_Table",
                "Measure_Name",
                "Display_Folder",
                "Column_Table",
                "Column_Name",
            ]
            writer = csv.writer(f)
            writer.writerow(headers)

            for msr_key, columns in mapping.measure_to_columns.items():
                if not columns:
                    continue

                if "[" in msr_key:
                    msr_table = msr_key.split("[")[0]
                    msr_name = msr_key.split("[")[1].rstrip("]")
                    msr_folder = measure_folders.get(msr_key, "")

                    # Filter - include measure if any of its columns are in target tables
                    if tables_lower:
                        has_target_column = any(
                            col.get("table", "").lower() in tables_lower for col in columns
                        )
                        if not has_target_column:
                            continue

                    for col in columns:
                        # Filter columns by table
                        if tables_lower and col.get("table", "").lower() not in tables_lower:
                            continue

                        row = [
                            msr_table,
                            msr_name,
                            msr_folder,
                            col.get("table", ""),
                            col.get("column", ""),
                        ]
                        writer.writerow(row)
                        msr_to_col_count += 1

        logger.info(
            f"Exported column usage to CSV: {col_to_msr_count} column->measure rows, {msr_to_col_count} measure->column rows"
        )

        return {
            "success": True,
            "column_to_measures_file": col_to_msr_path,
            "measure_to_columns_file": msr_to_col_path,
            "statistics": {
                "column_to_measures_rows": col_to_msr_count,
                "measure_to_columns_rows": msr_to_col_count,
                "tables_filter": tables,
            },
            "message": f"Exported to:\n  - {col_to_msr_path}\n  - {msr_to_col_path}",
        }


def _model_generation() -> int:
    """Current live-model generation (0 when the connection layer is unavailable)."""
    try:
        from core.infrastructure.connection_state import connection_state

        return int(getattr(connection_state, "model_generation", 0) or 0)
    except Exception:  # noqa: BLE001
        return 0
