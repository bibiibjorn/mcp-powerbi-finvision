"""
Unified table operations handler
Consolidates: list_tables, describe_table + new CRUD operations

Refactored to use validation utilities for reduced code duplication.
"""

from datetime import datetime
from typing import Dict, Any
import logging
from .base_operations import BaseOperationsHandler
from core.infrastructure.connection_state import connection_state

# Import validation utilities
from core.validation import (
    get_manager_or_error,
    get_table_name,
    get_new_name,
    get_optional_int,
    apply_pagination,
    apply_describe_table_defaults,
    validate_required,
    validate_required_params,
    ErrorHandler,
)

logger = logging.getLogger(__name__)


class TableOperationsHandler(BaseOperationsHandler):
    """Handles all table-related operations"""

    def __init__(self):
        super().__init__("table_operations")

        # Register all operations
        self.register_operation("list", self._list_tables)
        self.register_operation("describe", self._describe_table)
        self.register_operation("preview", self._preview_table)
        self.register_operation("sample_data", self._sample_data)
        self.register_operation("create", self._create_table)
        self.register_operation("update", self._update_table)
        self.register_operation("delete", self._delete_table)
        self.register_operation("rename", self._rename_table)
        self.register_operation("refresh", self._refresh_table)
        self.register_operation("generate_calendar", self._generate_calendar)
        self.register_operation("get_refresh_policy", self._get_refresh_policy)
        self.register_operation("set_refresh_policy", self._set_refresh_policy)
        self.register_operation("clear_refresh_policy", self._clear_refresh_policy)
        self.register_operation("mark_as_date_table", self._mark_as_date_table_op)

    def _list_tables(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """List all tables in the model"""
        # Get manager with connection check
        qe = get_manager_or_error("query_executor")
        if isinstance(qe, dict):  # Error response
            return qe

        result = qe.execute_info_query("TABLES")

        # Apply pagination if requested
        return apply_pagination(result, args)

    def _describe_table(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Get comprehensive table description"""
        # Get manager with connection check
        qe = get_manager_or_error("query_executor")
        if isinstance(qe, dict):  # Error response
            return qe

        # Extract parameters with backward compatibility
        table_name = get_table_name(args)

        # Validate required parameter
        if error := validate_required(table_name, "table_name", "describe"):
            return error

        # Apply default pagination limits for describe_table
        args = apply_describe_table_defaults(args)

        # Check if method exists
        if not hasattr(qe, "describe_table"):
            return {
                "success": False,
                "error": "describe_table method not implemented in query executor",
            }

        try:
            result = qe.describe_table(table_name, args)
            return result
        except Exception as e:
            logger.error(f"Error describing table '{table_name}': {e}", exc_info=True)
            return ErrorHandler.handle_unexpected_error("describe_table", e)

    def _preview_table(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Preview table data (simple version).
        Delegates to sample_data for consistent implementation.
        """
        # Delegate to sample_data - preview is just sample_data without extra options
        return self._sample_data(args)

    def _sample_data(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get sample data from a table with optional column selection and ordering.

        This is an enhanced version of preview that supports:
        - Column selection (return only specific columns)
        - Ordering by a specific column
        - Ascending/descending order
        """
        # Get manager with connection check
        agent_policy = get_manager_or_error("agent_policy")
        if isinstance(agent_policy, dict):  # Error response
            return agent_policy

        # Extract parameters with backward compatibility
        table_name = get_table_name(args)

        # Validate required parameter
        if error := validate_required(table_name, "table_name", "sample_data"):
            return error

        max_rows = min(get_optional_int(args, "max_rows", 10), 1000)  # Cap at 1000 rows
        columns = args.get("columns")  # Optional list of column names
        order_by = args.get("order_by")  # Optional column to order by
        # C-35: order_direction=None used to crash on .upper().
        order_direction = str(args.get("order_direction") or "asc").upper()  # ASC or DESC

        from core.dax.dax_names import column_ref, dax_string, quote_table

        # Build DAX query (C-35: names escaped — '' in tables, ]] in columns).
        if columns:
            # Select specific columns using SELECTCOLUMNS
            col_parts = []
            for col in columns:
                col_parts.append(f"{dax_string(col)}, {column_ref(table_name, col)}")
            inner_query = f"SELECTCOLUMNS({quote_table(table_name)}, {', '.join(col_parts)})"
        else:
            inner_query = quote_table(table_name)

        if order_by:
            # Add ordering
            order_col = column_ref(table_name, order_by)
            if order_direction == "DESC":
                query = f"EVALUATE TOPN({max_rows}, {inner_query}, {order_col}, DESC)"
            else:
                query = f"EVALUATE TOPN({max_rows}, {inner_query}, {order_col}, ASC)"
        else:
            # Simple TOPN without ordering
            query = f"EVALUATE TOPN({max_rows}, {inner_query})"

        result = agent_policy.safe_run_dax(
            connection_state=connection_state, query=query, mode="auto", max_rows=max_rows
        )

        # Add metadata about the query
        if result.get("success"):
            result["metadata"] = {
                "table_name": table_name,
                "max_rows_requested": max_rows,
                "columns_selected": columns if columns else "all",
                "order_by": order_by,
                "order_direction": order_direction if order_by else None,
            }

        return result

    def _create_table(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new table"""
        # Get manager with connection check
        table_crud = get_manager_or_error("table_crud_manager")
        if isinstance(table_crud, dict):  # Error response
            return table_crud

        return table_crud.create_table(
            table_name=args.get("table_name"),
            description=args.get("description"),
            expression=args.get("expression"),
            hidden=args.get("hidden", False),
            m_expression=args.get("m_expression"),
        )

    def _update_table(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Update an existing table"""
        # Get manager with connection check
        table_crud = get_manager_or_error("table_crud_manager")
        if isinstance(table_crud, dict):  # Error response
            return table_crud

        return table_crud.update_table(
            table_name=args.get("table_name"),
            description=args.get("description"),
            expression=args.get("expression"),
            hidden=args.get("hidden"),
            new_name=args.get("new_name"),
        )

    def _delete_table(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Delete a table"""
        # Get manager with connection check
        table_crud = get_manager_or_error("table_crud_manager")
        if isinstance(table_crud, dict):  # Error response
            return table_crud

        table_name = args.get("table_name")

        # Validate required parameter
        if error := validate_required(table_name, "table_name", "delete"):
            return error

        return table_crud.delete_table(table_name)

    def _rename_table(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Rename a table"""
        # Get manager with connection check
        table_crud = get_manager_or_error("table_crud_manager")
        if isinstance(table_crud, dict):  # Error response
            return table_crud

        table_name = args.get("table_name")
        new_name = get_new_name(args)

        # Validate required parameters
        if error := validate_required_params(
            (table_name, "table_name"), (new_name, "new_name"), operation="rename"
        ):
            return error

        pbip_path = args.get("pbip_path")
        return table_crud.rename_table(
            table_name,
            new_name,
            pbip_path=pbip_path,
            preview=bool(args.get("preview", False)),
            preview_hash=args.get("preview_hash"),
        )

    def _get_refresh_policy(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Read a table's incremental-refresh policy."""
        table_crud = get_manager_or_error("table_crud_manager")
        if isinstance(table_crud, dict):
            return table_crud
        table_name = args.get("table_name")
        if error := validate_required(table_name, "table_name", "get_refresh_policy"):
            return error
        return table_crud.get_refresh_policy(table_name)

    def _set_refresh_policy(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Set a table's incremental-refresh policy."""
        table_crud = get_manager_or_error("table_crud_manager")
        if isinstance(table_crud, dict):
            return table_crud
        table_name = args.get("table_name")
        if error := validate_required(table_name, "table_name", "set_refresh_policy"):
            return error
        return table_crud.set_refresh_policy(
            table_name=table_name,
            rolling_window_granularity=args.get("rolling_window_granularity", "Year"),
            rolling_window_periods=args.get("rolling_window_periods", 1),
            incremental_granularity=args.get("incremental_granularity", "Day"),
            incremental_periods=args.get("incremental_periods", 10),
            source_expression=args.get("source_expression"),
        )

    def _clear_refresh_policy(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Remove a table's incremental-refresh policy."""
        table_crud = get_manager_or_error("table_crud_manager")
        if isinstance(table_crud, dict):
            return table_crud
        table_name = args.get("table_name")
        if error := validate_required(table_name, "table_name", "clear_refresh_policy"):
            return error
        return table_crud.clear_refresh_policy(table_name)

    def _mark_as_date_table_op(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Mark a table as the date table on a validated DateTime column (MP-01)."""
        table_crud = get_manager_or_error("table_crud_manager")
        if isinstance(table_crud, dict):
            return table_crud
        table_name = args.get("table_name")
        if error := validate_required(table_name, "table_name", "mark_as_date_table"):
            return error
        date_column = args.get("date_column") or args.get("column_name")
        if error := validate_required(date_column, "date_column", "mark_as_date_table"):
            return error
        return table_crud.mark_as_date_table(table_name, date_column)

    def _refresh_table(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Refresh a table"""
        # Get manager with connection check
        table_crud = get_manager_or_error("table_crud_manager")
        if isinstance(table_crud, dict):  # Error response
            return table_crud

        table_name = args.get("table_name")

        # Validate required parameter
        if error := validate_required(table_name, "table_name", "refresh"):
            return error

        refresh_type = args.get("refresh_type", "full")
        return table_crud.refresh_table(table_name, refresh_type=refresh_type)

    #: F-20e calendar templates. ``standard`` = the original column set; ``iso_week`` adds ISO 8601
    #: week columns; ``445`` / ``454`` / ``544`` add a 52/53-week retail (fiscal) calendar whose
    #: quarters are 13 weeks split 4-4-5 / 4-5-4 / 5-4-4 (week 53 joins the last period).
    CALENDAR_TEMPLATES = ("standard", "iso_week", "445", "454", "544")

    @staticmethod
    def _calendar_base_dax(
        start_year: int,
        end_year: int,
        include_fiscal: bool = False,
        fiscal_start_month: int = 7,
    ) -> str:
        """Build DAX expression for a standard calendar calculated table."""
        base_dax = (
            f"ADDCOLUMNS(\n"
            f"    CALENDAR(DATE({start_year}, 1, 1), DATE({end_year}, 12, 31)),\n"
            f'    "Year", YEAR([Date]),\n'
            f'    "Quarter", "Q" & CEILING(MONTH([Date]) / 3, 1),\n'
            f'    "YearQuarter", YEAR([Date]) & "-Q" & CEILING(MONTH([Date]) / 3, 1),\n'
            f'    "Month", MONTH([Date]),\n'
            f'    "MonthName", FORMAT([Date], "MMMM"),\n'
            f'    "MonthShort", FORMAT([Date], "MMM"),\n'
            f'    "YearMonth", FORMAT([Date], "YYYY-MM"),\n'
            f'    "Day", DAY([Date]),\n'
            f'    "DayOfWeek", WEEKDAY([Date], 2),\n'
            f'    "DayName", FORMAT([Date], "dddd"),\n'
            f'    "WeekNumber", WEEKNUM([Date], 2),\n'
            f'    "IsWeekend", IF(WEEKDAY([Date], 2) > 5, TRUE(), FALSE()),\n'
            f'    "IsCurrentYear", IF(YEAR([Date]) = YEAR(TODAY()), TRUE(), FALSE()),\n'
            f'    "IsCurrentMonth", IF(YEAR([Date]) = YEAR(TODAY()) && MONTH([Date]) = MONTH(TODAY()), TRUE(), FALSE())'
        )

        if include_fiscal:
            base_dax += (
                f",\n"
                f'    "FiscalYear", IF(MONTH([Date]) >= {fiscal_start_month}, YEAR([Date]) + 1, YEAR([Date])),\n'
                f'    "FiscalQuarter", "FQ" & CEILING((MOD(MONTH([Date]) - {fiscal_start_month} + 12, 12) + 1) / 3, 1),\n'
                f'    "FiscalMonth", MOD(MONTH([Date]) - {fiscal_start_month} + 12, 12) + 1'
            )

        base_dax += "\n)"
        return base_dax

    @staticmethod
    def _iso_week_dax(base: str) -> str:
        """ISO 8601 week columns (the ISO year differs from the calendar year around 1 Jan)."""
        iso_year = "YEAR([Date] + 4 - WEEKDAY([Date], 2))"
        return (
            f"ADDCOLUMNS(\n{base},\n"
            f'    "ISOYear", {iso_year},\n'
            f'    "ISOWeek", WEEKNUM([Date], 21),\n'
            f'    "ISOYearWeek", {iso_year} & "-W" & FORMAT(WEEKNUM([Date], 21), "00")\n'
            f")"
        )

    @staticmethod
    def _retail_week_dax(base: str, pattern: str, fiscal_start_month: int) -> str:
        """52/53-week retail calendar (4-4-5 family) on top of ``base``.

        A fiscal year starts on the first Monday on/after the 1st of ``fiscal_start_month``
        (years are 52 or 53 weeks); ``FiscalYear`` is the calendar year the fiscal year starts in.
        Quarters are 13 weeks (week 53 stays in Q4); ``pattern`` gives the weeks of the three
        periods in every quarter.
        """
        a, b = int(pattern[0]), int(pattern[1])
        m = int(fiscal_start_month)

        def first_monday(year_expr: str) -> str:
            first = f"DATE({year_expr}, {m}, 1)"
            return f"{first} + MOD(8 - WEEKDAY({first}, 2), 7)"

        s = (
            f"ADDCOLUMNS(\n{base},\n"
            f'    "FiscalYear", IF([Date] >= {first_monday("YEAR([Date])")}, '
            f"YEAR([Date]), YEAR([Date]) - 1)\n)"
        )
        s = f'ADDCOLUMNS(\n{s},\n    "FiscalYearStart", {first_monday("[FiscalYear]")}\n)'
        s = f'ADDCOLUMNS(\n{s},\n    "FiscalWeek", INT(([Date] - [FiscalYearStart]) / 7) + 1\n)'
        s = f'ADDCOLUMNS(\n{s},\n    "FiscalQuarterNo", MIN(INT(([FiscalWeek] - 1) / 13) + 1, 4)\n)'
        wq = "([FiscalWeek] - ([FiscalQuarterNo] - 1) * 13)"
        s = (
            f"ADDCOLUMNS(\n{s},\n"
            f'    "FiscalQuarter", "FQ" & [FiscalQuarterNo],\n'
            f'    "FiscalPeriod", ([FiscalQuarterNo] - 1) * 3 + '
            f"IF({wq} <= {a}, 1, IF({wq} <= {a + b}, 2, 3))\n)"
        )
        return s

    @staticmethod
    def _generate_calendar_dax(
        start_year: int,
        end_year: int,
        include_fiscal: bool = False,
        fiscal_start_month: int = 7,
        template: str = "standard",
    ) -> str:
        """Build the DAX of a calendar calculated table for ``template``."""
        tpl = (template or "standard").lower()
        if tpl in ("445", "454", "544"):
            base = TableOperationsHandler._calendar_base_dax(start_year, end_year, False, 1)
            return TableOperationsHandler._retail_week_dax(base, tpl, fiscal_start_month)
        base = TableOperationsHandler._calendar_base_dax(
            start_year, end_year, include_fiscal, fiscal_start_month
        )
        if tpl == "iso_week":
            return TableOperationsHandler._iso_week_dax(base)
        return base

    def _generate_calendar(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a standard DAX calendar (date) table."""
        # Get manager with connection check
        table_crud = get_manager_or_error("table_crud_manager")
        if isinstance(table_crud, dict):  # Error response
            return table_crud

        current_year = datetime.now().year
        table_name = args.get("table_name", "Date")
        start_year = args.get("start_year", current_year - 5)
        end_year = args.get("end_year", current_year + 2)
        include_fiscal = args.get("include_fiscal", False)
        template = str(args.get("calendar_template") or "standard").strip().lower()
        if template not in self.CALENDAR_TEMPLATES:
            return {
                "success": False,
                "error": (
                    f"calendar_template must be one of {list(self.CALENDAR_TEMPLATES)}, "
                    f"got {args.get('calendar_template')!r}"
                ),
                "error_type": "invalid_parameters",
            }
        retail = template in ("445", "454", "544")
        # A retail calendar starts on the first Monday of January unless told otherwise.
        fiscal_start_month = args.get("fiscal_start_month", 1 if retail else 7)

        # Validate year range
        if start_year > end_year:
            return {
                "success": False,
                "error": f"start_year ({start_year}) must be <= end_year ({end_year})",
                "error_type": "invalid_parameters",
            }

        if fiscal_start_month < 1 or fiscal_start_month > 12:
            return {
                "success": False,
                "error": f"fiscal_start_month must be 1-12, got {fiscal_start_month}",
                "error_type": "invalid_parameters",
            }

        # Build the DAX expression
        dax_expression = self._generate_calendar_dax(
            start_year, end_year, include_fiscal, fiscal_start_month, template
        )

        # Create the calculated table
        result = table_crud.create_table(
            table_name=table_name,
            description=f"Calendar table ({start_year}-{end_year})"
            + (f", fiscal year starting month {fiscal_start_month}" if include_fiscal else "")
            + (f", template {template}" if template != "standard" else ""),
            expression=dax_expression,
            hidden=False,
        )

        if not result.get("success"):
            return result

        # Try to mark as date table (set IsDateTable property via TOM)
        mark_result = self._mark_as_date_table(table_crud, table_name)

        result["calendar_info"] = {
            "start_year": start_year,
            "end_year": end_year,
            "include_fiscal": include_fiscal,
            "calendar_template": template,
            "fiscal_start_month": fiscal_start_month if (include_fiscal or retail) else None,
            "marked_as_date_table": mark_result.get("success", False),
        }
        if not mark_result.get("success"):
            result["calendar_info"]["mark_date_table_note"] = mark_result.get("note", "")

        return result

    @staticmethod
    def _mark_as_date_table(table_crud, table_name: str) -> Dict[str, Any]:
        """Attempt to mark the table as a date table via TOM."""
        try:
            server, db, model = table_crud._get_server_db_model()
            if not model:
                return {"success": False, "note": "Could not connect to mark as date table"}

            try:
                table = next((t for t in model.Tables if t.Name == table_name), None)
                if not table:
                    return {
                        "success": False,
                        "note": f"Table '{table_name}' not found for date table marking",
                    }

                # Find the Date column
                date_col = next((c for c in table.Columns if c.Name == "Date"), None)
                if not date_col:
                    return {"success": False, "note": "Date column not found in generated table"}

                # Set the Date column as the key column for the date table
                from Microsoft.AnalysisServices.Tabular import DataType, ObjectType  # noqa: F401

                table.DataCategory = "Time"
                date_col.IsKey = True

                from core.operations.write_lane import commit

                commit(model, label="table/mark_date_table")
                return {"success": True}

            finally:
                try:
                    server.Disconnect()
                except Exception:
                    pass

        except Exception as e:
            logger.warning(f"Could not mark '{table_name}' as date table: {e}")
            return {"success": False, "note": f"Could not mark as date table: {e}"}
