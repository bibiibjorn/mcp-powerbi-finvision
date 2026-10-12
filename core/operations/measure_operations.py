"""
Unified measure operations handler
Consolidates: list_measures, get_measure_details, upsert_measure, delete_measure

Refactored to use validation utilities for reduced code duplication.
"""

from typing import Any, Dict, List
import logging
from .base_operations import BaseOperationsHandler

# Import validation utilities
from core.validation import (
    get_manager_or_error,
    get_table_name,
    get_measure_name,
    get_format_string,
    get_source_table,
    get_target_table,
    get_new_name,
    apply_pagination_with_defaults,
    validate_table_and_item,
    validate_create_params,
    validate_rename_params,
    validate_move_params,
)

logger = logging.getLogger(__name__)


class MeasureOperationsHandler(BaseOperationsHandler):
    """Handles all measure-related operations"""

    def __init__(self):
        super().__init__("measure_operations")

        # Register all operations
        self.register_operation("list", self._list_measures)
        self.register_operation("get", self._get_measure)
        self.register_operation("create", self._create_measure)
        self.register_operation("update", self._update_measure)
        self.register_operation("delete", self._delete_measure)
        self.register_operation("rename", self._rename_measure)
        self.register_operation("move", self._move_measure)
        self.register_operation("set_format_string_expression", self._set_format_string_expression)
        self.register_operation("set_detail_rows_expression", self._set_detail_rows_expression)
        self.register_operation("set_kpi", self._set_kpi)
        self.register_operation("clear_kpi", self._clear_kpi)
        self.register_operation("get_kpi", self._get_kpi)

    def _list_measures(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """List measures, optionally filtered by table.

        T1-E: accepts an ``include`` selector list. Without it, ``Expression``
        is excluded (the default, to keep payloads small). With it, the
        named fields are merged back into the response so the agent doesn't
        have to follow up with per-measure ``get`` calls. Recognized values
        (case-insensitive):

            expression        -> include the DAX expression
            format_string     -> include the FormatString
            display_folder    -> include the DisplayFolder
            description       -> include the Description
            kpi               -> mark presence of a KPI (KPIID nonzero)
            all               -> include everything (no exclusions)
        """
        qe = get_manager_or_error("query_executor")
        if isinstance(qe, dict):
            return qe

        table_name = get_table_name(args)
        include_raw = args.get("include") or []
        if isinstance(include_raw, str):
            include_raw = [include_raw]
        include = {str(x).lower() for x in include_raw}

        # Build exclude_columns dynamically.
        if "all" in include or "expression" in include:
            exclude_columns: List[str] = []
        else:
            exclude_columns = ["Expression"]

        result = qe.execute_info_query(
            "MEASURES", table_name=table_name, exclude_columns=exclude_columns
        )
        return apply_pagination_with_defaults(result, args)

    def _get_measure(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Get detailed measure information including DAX formula"""
        # Get manager with connection check
        qe = get_manager_or_error("query_executor")
        if isinstance(qe, dict):  # Error response
            return qe

        # Extract parameters with backward compatibility
        table_name = get_table_name(args)
        measure_name = get_measure_name(args)

        # Validate required parameters
        if error := validate_table_and_item(table_name, measure_name, "measure_name", "get"):
            return error

        result = qe.get_measure_details_with_fallback(table_name, measure_name)
        return result

    def _create_measure(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new measure.

        C-12: a real create — fails with ``already_exists`` when the name is
        taken anywhere in the model (``if_exists='update'`` / ``upsert`` retry
        as update in the dispatcher). It used to be an upsert that silently
        overwrote an existing measure.
        """
        measure_crud = get_manager_or_error("measure_crud_manager")
        if isinstance(measure_crud, dict):  # Error response
            return measure_crud

        # Extract parameters with backward compatibility
        table_name = get_table_name(args)
        measure_name = get_measure_name(args)
        expression = args.get("expression")
        format_string = get_format_string(args)

        # Validate required parameters
        if error := validate_create_params(table_name, measure_name, expression, "measure_name"):
            return error

        return measure_crud.create_measure(
            table_name,
            measure_name,
            expression=expression,
            description=args.get("description"),
            format_string=format_string,
            format_string_expression=args.get("format_string_expression"),
            display_folder=args.get("display_folder"),
            data_category=args.get("data_category"),
            hidden=args.get("hidden"),
        )

    def _update_measure(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Update an existing measure.

        P2-29: update now requires the measure to exist; previously this was
        a thin alias for _create_measure so typos silently created new measures.
        """
        measure_crud = get_manager_or_error("measure_crud_manager")
        if isinstance(measure_crud, dict):
            return measure_crud

        table_name = get_table_name(args)
        measure_name = get_measure_name(args)
        expression = args.get("expression")
        format_string = get_format_string(args)

        if error := validate_table_and_item(table_name, measure_name, "measure_name", "update"):
            return error

        return measure_crud.update_measure(
            table_name,
            measure_name,
            expression=expression,
            description=args.get("description"),
            format_string=format_string,
            format_string_expression=args.get("format_string_expression"),
            display_folder=args.get("display_folder"),
            data_category=args.get("data_category"),
            hidden=args.get("hidden"),
        )

    def _delete_measure(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Delete a measure"""
        # Get manager with connection check
        measure_crud = get_manager_or_error("measure_crud_manager")
        if isinstance(measure_crud, dict):  # Error response
            return measure_crud

        # Extract parameters with backward compatibility
        table_name = get_table_name(args)
        measure_name = get_measure_name(args)

        # Validate required parameters
        if error := validate_table_and_item(table_name, measure_name, "measure_name", "delete"):
            return error

        return measure_crud.delete_measure(table_name, measure_name)

    def _rename_measure(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Rename a measure"""
        # Get manager with connection check
        measure_crud = get_manager_or_error("measure_crud_manager")
        if isinstance(measure_crud, dict):  # Error response
            return measure_crud

        # Extract parameters with backward compatibility
        table_name = get_table_name(args)
        measure_name = get_measure_name(args)
        new_name = get_new_name(args)

        # Validate required parameters
        if error := validate_rename_params(table_name, measure_name, new_name, "measure_name"):
            return error

        pbip_path = args.get("pbip_path")
        return measure_crud.rename_measure(
            table_name,
            measure_name,
            new_name,
            pbip_path=pbip_path,
            preview=bool(args.get("preview", False)),
            preview_hash=args.get("preview_hash"),
        )

    def _move_measure(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Move measure to a different table"""
        # Get manager with connection check
        measure_crud = get_manager_or_error("measure_crud_manager")
        if isinstance(measure_crud, dict):  # Error response
            return measure_crud

        # Extract parameters with backward compatibility
        source_table = get_source_table(args)
        target_table = get_target_table(args)
        measure_name = get_measure_name(args)

        # Validate required parameters
        if error := validate_move_params(source_table, measure_name, target_table, "measure_name"):
            return error

        return measure_crud.move_measure(
            source_table, measure_name, target_table, pbip_path=args.get("pbip_path")
        )

    def _set_format_string_expression(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Set or clear dynamic format string expression on a measure (CL 1606+)."""
        measure_crud = get_manager_or_error("measure_crud_manager")
        if isinstance(measure_crud, dict):  # Error response
            return measure_crud

        table_name = get_table_name(args)
        measure_name = get_measure_name(args)

        if error := validate_table_and_item(
            table_name, measure_name, "measure_name", "set_format_string_expression"
        ):
            return error

        expression = args.get("expression")  # may be None to clear
        return measure_crud.set_format_string_expression(
            table_name=table_name,
            measure_name=measure_name,
            expression=expression,
        )

    def _set_detail_rows_expression(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Set or clear DetailRowsDefinition expression on a measure (CL 1400+)."""
        measure_crud = get_manager_or_error("measure_crud_manager")
        if isinstance(measure_crud, dict):
            return measure_crud

        table_name = get_table_name(args)
        measure_name = get_measure_name(args)

        if error := validate_table_and_item(
            table_name, measure_name, "measure_name", "set_detail_rows_expression"
        ):
            return error

        expression = args.get("expression")  # None or "" clears
        return measure_crud.set_detail_rows_expression(
            table_name=table_name,
            measure_name=measure_name,
            expression=expression,
        )

    def _set_kpi(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Author or update the KPI on a measure."""
        measure_crud = get_manager_or_error("measure_crud_manager")
        if isinstance(measure_crud, dict):
            return measure_crud

        table_name = get_table_name(args)
        measure_name = get_measure_name(args)

        if error := validate_table_and_item(table_name, measure_name, "measure_name", "set_kpi"):
            return error

        return measure_crud.set_kpi(
            table_name=table_name,
            measure_name=measure_name,
            target_expression=args.get("target_expression"),
            status_expression=args.get("status_expression"),
            trend_expression=args.get("trend_expression"),
            status_graphic=args.get("status_graphic"),
            trend_graphic=args.get("trend_graphic"),
            target_description=args.get("target_description"),
            status_description=args.get("status_description"),
            trend_description=args.get("trend_description"),
            target_format_string=args.get("target_format_string"),
        )

    def _clear_kpi(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Remove KPI from a measure (idempotent)."""
        measure_crud = get_manager_or_error("measure_crud_manager")
        if isinstance(measure_crud, dict):
            return measure_crud

        table_name = get_table_name(args)
        measure_name = get_measure_name(args)

        if error := validate_table_and_item(table_name, measure_name, "measure_name", "clear_kpi"):
            return error

        return measure_crud.clear_kpi(
            table_name=table_name,
            measure_name=measure_name,
        )

    def _get_kpi(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Read KPI properties on a measure (read-only)."""
        measure_crud = get_manager_or_error("measure_crud_manager")
        if isinstance(measure_crud, dict):
            return measure_crud

        table_name = get_table_name(args)
        measure_name = get_measure_name(args)

        if error := validate_table_and_item(table_name, measure_name, "measure_name", "get_kpi"):
            return error

        return measure_crud.get_kpi(
            table_name=table_name,
            measure_name=measure_name,
        )
