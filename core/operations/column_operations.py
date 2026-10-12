"""
Unified column operations handler
Consolidates: list_columns, list_calculated_columns, get_column_value_distribution, get_column_summary

Refactored to use validation utilities for reduced code duplication.
"""

from typing import Dict, Any
import logging
from .base_operations import BaseOperationsHandler
from core.infrastructure.query_executor import COLUMN_TYPE_CALCULATED

# Import validation utilities
from core.validation import (
    get_manager_or_error,
    get_table_name,
    get_column_name,
    get_new_name,
    get_optional_int,
    apply_pagination_with_defaults,
    validate_table_and_item,
    validate_rename_params,
)

logger = logging.getLogger(__name__)


class ColumnOperationsHandler(BaseOperationsHandler):
    """Handles all column-related operations"""

    def __init__(self):
        super().__init__("column_operations")

        # Register all operations
        self.register_operation("list", self._list_columns)
        self.register_operation("statistics", self._get_column_statistics)
        self.register_operation("distribution", self._get_column_distribution)
        self.register_operation("get", self._get_column)
        self.register_operation("create", self._create_column)
        self.register_operation("update", self._update_column)
        self.register_operation("delete", self._delete_column)
        self.register_operation("rename", self._rename_column)

    def _list_columns(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """List columns, optionally filtered by table and type"""
        # Get manager with connection check
        qe = get_manager_or_error("query_executor")
        if isinstance(qe, dict):  # Error response
            return qe

        # Extract parameters with backward compatibility
        table_name = get_table_name(args)
        column_type = args.get("column_type", "all")  # 'all', 'data', 'calculated'

        # Build filter expression based on column_type
        filter_expr = None
        if column_type == "calculated":
            filter_expr = f"[Type] = {COLUMN_TYPE_CALCULATED}"
        elif column_type == "data":
            filter_expr = f"[Type] <> {COLUMN_TYPE_CALCULATED}"
        # else: 'all' - no filter

        result = qe.execute_info_query("COLUMNS", table_name=table_name, filter_expr=filter_expr)

        # Apply pagination with defaults
        return apply_pagination_with_defaults(result, args)

    def _get_column_statistics(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Get column summary statistics"""
        # Get manager with connection check
        agent_policy = get_manager_or_error("agent_policy")
        if isinstance(agent_policy, dict):  # Error response
            return agent_policy

        # Extract parameters with backward compatibility
        table_name = get_table_name(args)
        column_name = get_column_name(args)

        # Validate required parameters
        if error := validate_table_and_item(table_name, column_name, "column_name", "statistics"):
            return error

        from core.dax.dax_names import column_ref, quote_table

        # C-35: identifiers escaped ('' in table names, ]] in column names).
        col = column_ref(table_name, column_name)
        tbl = quote_table(table_name)
        query = f"""
        EVALUATE
        ROW(
            "DistinctCount", COUNTROWS(DISTINCT({col})),
            "TotalCount", COUNTROWS({tbl}),
            "BlankCount", COUNTBLANK({col})
        )
        """

        from core.infrastructure.connection_state import connection_state

        return agent_policy.safe_run_dax(
            connection_state=connection_state, query=query, mode="auto"
        )

    def _get_column_distribution(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Get column value distribution"""
        # Get manager with connection check
        agent_policy = get_manager_or_error("agent_policy")
        if isinstance(agent_policy, dict):  # Error response
            return agent_policy

        # Extract parameters with backward compatibility
        table_name = get_table_name(args)
        column_name = get_column_name(args)

        # Validate required parameters
        if error := validate_table_and_item(table_name, column_name, "column_name", "distribution"):
            return error

        top_n = get_optional_int(args, "top_n", 10)

        from core.dax.dax_names import column_ref, quote_table

        # C-35: identifiers escaped ('' in table names, ]] in column names).
        query = f"""
        EVALUATE
        TOPN(
            {top_n},
            SUMMARIZECOLUMNS(
                {column_ref(table_name, column_name)},
                "Count", COUNTROWS({quote_table(table_name)})
            ),
            [Count],
            DESC
        )
        """

        from core.infrastructure.connection_state import connection_state

        return agent_policy.safe_run_dax(
            connection_state=connection_state, query=query, mode="auto", max_rows=top_n
        )

    def _get_column(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Get detailed column information"""
        # Get manager with connection check
        column_crud = get_manager_or_error("column_crud_manager")
        if isinstance(column_crud, dict):  # Error response
            return column_crud

        table_name = args.get("table_name")
        column_name = args.get("column_name")

        # Validate required parameters
        if error := validate_table_and_item(table_name, column_name, "column_name", "get"):
            return error

        return column_crud.get_column(table_name, column_name)

    def _create_column(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new column"""
        # Get manager with connection check
        column_crud = get_manager_or_error("column_crud_manager")
        if isinstance(column_crud, dict):  # Error response
            return column_crud

        # C-25: no silent "String" default — a calculated column infers its
        # type from the expression; a data column defaults to String inside
        # the manager.
        return column_crud.create_column(
            table_name=args.get("table_name"),
            column_name=args.get("column_name"),
            data_type=args.get("data_type"),
            expression=args.get("expression"),
            description=args.get("description"),
            hidden=args.get("hidden", False),
            display_folder=args.get("display_folder"),
            format_string=args.get("format_string"),
            source_column=args.get("source_column"),
        )

    def _update_column(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Update an existing column"""
        # Get manager with connection check
        column_crud = get_manager_or_error("column_crud_manager")
        if isinstance(column_crud, dict):  # Error response
            return column_crud

        return column_crud.update_column(
            table_name=args.get("table_name"),
            column_name=args.get("column_name"),
            expression=args.get("expression"),
            description=args.get("description"),
            hidden=args.get("hidden"),
            display_folder=args.get("display_folder"),
            format_string=args.get("format_string"),
            new_name=args.get("new_name"),
            data_type=args.get("data_type"),
            sort_by_column=args.get("sort_by_column"),
            summarize_by=args.get("summarize_by"),
            data_category=args.get("data_category"),
            is_key=args.get("is_key"),
            is_nullable=args.get("is_nullable"),
            clear_sort_by_column=bool(args.get("clear_sort_by_column", False)),
            string_indexing_behavior=args.get("string_indexing_behavior"),
            full_text_indexing_behavior=args.get("full_text_indexing_behavior"),
        )

    def _delete_column(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Delete a column"""
        # Get manager with connection check
        column_crud = get_manager_or_error("column_crud_manager")
        if isinstance(column_crud, dict):  # Error response
            return column_crud

        table_name = args.get("table_name")
        column_name = args.get("column_name")

        # Validate required parameters
        if error := validate_table_and_item(table_name, column_name, "column_name", "delete"):
            return error

        return column_crud.delete_column(table_name, column_name)

    def _rename_column(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Rename a column"""
        # Get manager with connection check
        column_crud = get_manager_or_error("column_crud_manager")
        if isinstance(column_crud, dict):  # Error response
            return column_crud

        table_name = args.get("table_name")
        column_name = args.get("column_name")
        new_name = get_new_name(args)

        # Validate required parameters
        if error := validate_rename_params(table_name, column_name, new_name, "column_name"):
            return error

        pbip_path = args.get("pbip_path")
        return column_crud.rename_column(
            table_name,
            column_name,
            new_name,
            pbip_path=pbip_path,
            preview=bool(args.get("preview", False)),
            preview_hash=args.get("preview_hash"),
        )
