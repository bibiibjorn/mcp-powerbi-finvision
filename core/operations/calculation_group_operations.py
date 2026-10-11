"""
Calculation Group Operations — handler dispatch layer (P2-24 two-layer split).

See calculation_group_manager.py for the two-layer convention description.
This file is the *_operations.py layer: validates args, resolves managers,
calls calculation_group_manager, formats results.
"""

from typing import Dict, Any
import logging
from .base_operations import BaseOperationsHandler

# Import validation utilities
from core.validation import (
    get_manager_or_error,
    get_group_name,
    validate_required,
)

logger = logging.getLogger(__name__)


class CalculationGroupOperationsHandler(BaseOperationsHandler):
    """Handles all calculation group-related operations"""

    def __init__(self):
        super().__init__("calculation_group_operations")

        # Register all operations
        self.register_operation("list", self._list_calculation_groups)
        self.register_operation("create", self._create_calculation_group)
        self.register_operation("delete", self._delete_calculation_group)
        self.register_operation("list_items", self._list_items)
        self.register_operation("add_item", self._add_item)
        self.register_operation("update_item", self._update_item)
        self.register_operation("delete_item", self._delete_item)
        self.register_operation("set_precedence", self._set_precedence)
        self.register_operation("update", self._update_calculation_group)
        self.register_operation("rename", self._rename_calculation_group)

    def _list_calculation_groups(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """List calculation groups"""
        # Get manager with connection check
        calc_group_mgr = get_manager_or_error("calc_group_manager")
        if isinstance(calc_group_mgr, dict):  # Error response
            return calc_group_mgr

        return calc_group_mgr.list_calculation_groups()

    def _create_calculation_group(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Create a calculation group"""
        # Get manager with connection check
        calc_group_mgr = get_manager_or_error("calc_group_manager")
        if isinstance(calc_group_mgr, dict):  # Error response
            return calc_group_mgr

        group_name = get_group_name(args)

        # Validate required parameter
        if error := validate_required(group_name, "group_name", "create"):
            return error

        items = args.get("items", [])
        description = args.get("description")
        precedence = args.get("precedence")

        # If no precedence specified, find next available precedence value
        if precedence is None:
            # Get existing calculation groups to find used precedence values
            existing_groups = calc_group_mgr.list_calculation_groups()
            if existing_groups.get("success"):
                used_precedences = set()
                for group in existing_groups.get("calculation_groups", []):
                    used_precedences.add(group.get("precedence", 0))

                # Find first available precedence (starting from 0)
                precedence = 0
                while precedence in used_precedences:
                    precedence += 1

                logger.info(
                    f"Auto-assigned precedence {precedence} for calculation group '{group_name}'"
                )
            else:
                # Fallback to 0 if we can't list existing groups
                precedence = 0
        else:
            # Validate that the specified precedence isn't already taken
            existing_groups = calc_group_mgr.list_calculation_groups()
            if existing_groups.get("success"):
                for group in existing_groups.get("calculation_groups", []):
                    if group.get("precedence") == precedence:
                        used_precedences = [
                            g.get("precedence", 0)
                            for g in existing_groups.get("calculation_groups", [])
                        ]
                        available = [
                            p for p in range(max(used_precedences) + 2) if p not in used_precedences
                        ]
                        return {
                            "success": False,
                            "error": f'Precedence {precedence} is already taken by calculation group "{group.get("name")}"',
                            "suggestion": f"Use one of these available precedence values: {available[:5]}",
                        }

        return calc_group_mgr.create_calculation_group(
            name=group_name, items=items, description=description, precedence=precedence
        )

    def _delete_calculation_group(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Delete a calculation group"""
        # Get manager with connection check
        calc_group_mgr = get_manager_or_error("calc_group_manager")
        if isinstance(calc_group_mgr, dict):  # Error response
            return calc_group_mgr

        group_name = get_group_name(args)

        # Validate required parameter
        if error := validate_required(group_name, "group_name", "delete"):
            return error

        return calc_group_mgr.delete_calculation_group(group_name)

    def _list_items(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """List calculation items for a specific group"""
        # Get manager with connection check
        calc_group_mgr = get_manager_or_error("calc_group_manager")
        if isinstance(calc_group_mgr, dict):  # Error response
            return calc_group_mgr

        group_name = get_group_name(args)

        # Validate required parameter
        if error := validate_required(group_name, "group_name", "list_items"):
            return error

        # Get all calculation groups and filter for the specific one
        result = calc_group_mgr.list_calculation_groups()

        if result.get("success"):
            groups = result.get("calculation_groups", [])
            matching = [g for g in groups if g.get("name") == group_name]

            if matching:
                group = matching[0]
                # C-16: the manager returns the list under 'items' (the old
                # 'calculationItems' key never existed, so item_count was 0).
                items = group.get("items") or group.get("calculationItems") or []
                return {
                    "success": True,
                    "group_name": group_name,
                    "item_count": len(items),
                    "items": items,
                }
            else:
                return {"success": False, "error": f"Calculation group not found: {group_name}"}

        return result

    def _add_item(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Add a calculation item to an existing group."""
        calc_group_mgr = get_manager_or_error("calc_group_manager")
        if isinstance(calc_group_mgr, dict):
            return calc_group_mgr
        group_name = get_group_name(args)
        if error := validate_required(group_name, "group_name", "add_item"):
            return error
        item_name = args.get("item_name")
        expression = args.get("expression")
        if error := validate_required(item_name, "item_name", "add_item"):
            return error
        if error := validate_required(expression, "expression", "add_item"):
            return error
        return calc_group_mgr.add_calculation_item(
            group_name=group_name,
            item_name=item_name,
            expression=expression,
            ordinal=args.get("ordinal"),
            format_string_expression=args.get("format_string_expression"),
            description=args.get("description"),
        )

    def _update_item(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Update a calculation item (rename, expression, ordinal, format_string_expression)."""
        calc_group_mgr = get_manager_or_error("calc_group_manager")
        if isinstance(calc_group_mgr, dict):
            return calc_group_mgr
        group_name = get_group_name(args)
        if error := validate_required(group_name, "group_name", "update_item"):
            return error
        item_name = args.get("item_name")
        if error := validate_required(item_name, "item_name", "update_item"):
            return error
        return calc_group_mgr.update_calculation_item(
            group_name=group_name,
            item_name=item_name,
            new_name=args.get("new_name"),
            expression=args.get("expression"),
            ordinal=args.get("ordinal"),
            format_string_expression=args.get("format_string_expression"),
            description=args.get("description"),
        )

    def _delete_item(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Delete a calculation item from a group."""
        calc_group_mgr = get_manager_or_error("calc_group_manager")
        if isinstance(calc_group_mgr, dict):
            return calc_group_mgr
        group_name = get_group_name(args)
        if error := validate_required(group_name, "group_name", "delete_item"):
            return error
        item_name = args.get("item_name")
        if error := validate_required(item_name, "item_name", "delete_item"):
            return error
        return calc_group_mgr.delete_calculation_item(group_name, item_name)

    def _set_precedence(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Update Precedence on an existing calc group."""
        calc_group_mgr = get_manager_or_error("calc_group_manager")
        if isinstance(calc_group_mgr, dict):
            return calc_group_mgr
        group_name = get_group_name(args)
        if error := validate_required(group_name, "group_name", "set_precedence"):
            return error
        precedence = args.get("precedence")
        if precedence is None:
            return {"success": False, "error": "precedence is required for set_precedence"}
        try:
            precedence_int = int(precedence)
        except (TypeError, ValueError):
            return {
                "success": False,
                "error": f"precedence must be an integer (got {precedence!r})",
            }
        return calc_group_mgr.set_precedence(group_name=group_name, precedence=precedence_int)

    def _update_calculation_group(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Update a group's description / precedence / hidden flag (QG-01)."""
        calc_group_mgr = get_manager_or_error("calc_group_manager")
        if isinstance(calc_group_mgr, dict):
            return calc_group_mgr
        group_name = get_group_name(args)
        if error := validate_required(group_name, "group_name", "update"):
            return error
        return calc_group_mgr.update_calculation_group(
            group_name=group_name,
            description=args.get("description"),
            precedence=args.get("precedence"),
            hidden=args.get("hidden"),
            multiple_or_empty_selection_expression=args.get(
                "multiple_or_empty_selection_expression"
            ),
            no_selection_expression=args.get("no_selection_expression"),
            selection_expression_behavior=args.get("selection_expression_behavior"),
        )

    def _rename_calculation_group(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Rename a calculation group = rename its hosting table (QG-01).

        Delegates to the table rename, so every DAX reference to the group's table and columns is
        cascaded, verified and rolled back on new errors; ``preview`` / ``preview_hash`` /
        ``pbip_path`` behave exactly as for ``table`` rename.
        """
        calc_group_mgr = get_manager_or_error("calc_group_manager")
        if isinstance(calc_group_mgr, dict):
            return calc_group_mgr
        table_crud = get_manager_or_error("table_crud_manager")
        if isinstance(table_crud, dict):
            return table_crud
        group_name = get_group_name(args)
        if error := validate_required(group_name, "group_name", "rename"):
            return error
        new_name = args.get("new_name")
        if error := validate_required(new_name, "new_name", "rename"):
            return error
        listed = calc_group_mgr.list_calculation_groups()
        if listed.get("success"):
            names = [g.get("name") for g in listed.get("calculation_groups", [])]
            if group_name not in names:
                return {
                    "success": False,
                    "error": f"Calculation group '{group_name}' not found",
                    "error_type": "calc_group_not_found",
                    "available_groups": names,
                }
        result = table_crud.rename_table(
            group_name,
            new_name,
            pbip_path=args.get("pbip_path"),
            preview=bool(args.get("preview", False)),
            preview_hash=args.get("preview_hash"),
        )
        if isinstance(result, dict) and result.get("success"):
            result.setdefault("calculation_group", new_name)
            result.setdefault("original_name", group_name)
        return result
