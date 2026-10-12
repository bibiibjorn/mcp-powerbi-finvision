"""
Unified relationship operations handler
Extends: list_relationships + new CRUD operations

Refactored to use validation utilities for reduced code duplication.
"""

from typing import Dict, Any
import logging
from .base_operations import BaseOperationsHandler

# Import validation utilities
from core.validation import (
    get_manager_or_error,
    get_table_name,
    get_relationship_name,
    get_optional_bool,
    apply_pagination,
    validate_required,
    validate_relationship_create_params,
)

logger = logging.getLogger(__name__)


class RelationshipOperationsHandler(BaseOperationsHandler):
    """Handles all relationship-related operations"""

    def __init__(self):
        super().__init__("relationship_operations")

        # Register all operations
        self.register_operation("list", self._list_relationships)
        self.register_operation("get", self._get_relationship)
        self.register_operation("find", self._find_relationships)
        self.register_operation("create", self._create_relationship)
        self.register_operation("update", self._update_relationship)
        self.register_operation("delete", self._delete_relationship)
        self.register_operation("activate", self._activate_relationship)
        self.register_operation("deactivate", self._deactivate_relationship)
        self.register_operation("rename", self._rename_relationship)

    def _list_relationships(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """List relationships with optional active_only filter"""
        # Get manager with connection check
        qe = get_manager_or_error("query_executor")
        if isinstance(qe, dict):  # Error response
            return qe

        active_only = get_optional_bool(args, "active_only", False)

        # Use INFO query to get relationships
        result = qe.execute_info_query("RELATIONSHIPS")

        if result.get("success") and active_only:
            rows = result.get("rows", [])
            # Filter for active relationships only
            active_rows = [r for r in rows if r.get("IsActive") or r.get("[IsActive]")]
            result["rows"] = active_rows

        # Apply pagination
        return apply_pagination(result, args)

    def _get_relationship(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Get single relationship details"""
        # Get manager with connection check
        qe = get_manager_or_error("query_executor")
        if isinstance(qe, dict):  # Error response
            return qe

        relationship_name = get_relationship_name(args)

        # Validate required parameter
        if error := validate_required(relationship_name, "relationship_name", "get"):
            return error

        # Get all relationships and filter
        result = qe.execute_info_query("RELATIONSHIPS")

        if result.get("success"):
            rows = result.get("rows", [])
            matching = [
                r
                for r in rows
                if r.get("Name") == relationship_name or r.get("[Name]") == relationship_name
            ]

            if matching:
                return {"success": True, "relationship": matching[0]}
            else:
                return {"success": False, "error": f"Relationship not found: {relationship_name}"}

        return result

    def _find_relationships(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Find relationships for a specific table"""
        # Get manager with connection check
        qe = get_manager_or_error("query_executor")
        if isinstance(qe, dict):  # Error response
            return qe

        table_name = get_table_name(args)

        # Validate required parameter
        if error := validate_required(table_name, "table_name", "find"):
            return error

        # Get all relationships
        result = qe.execute_info_query("RELATIONSHIPS")

        if result.get("success"):
            rows = result.get("rows", [])
            # Filter for relationships involving this table
            matching = [
                r
                for r in rows
                if r.get("FromTable") == table_name
                or r.get("[FromTable]") == table_name
                or r.get("ToTable") == table_name
                or r.get("[ToTable]") == table_name
            ]

            return {
                "success": True,
                "table_name": table_name,
                "relationship_count": len(matching),
                "relationships": matching,
            }

        return result

    def _create_relationship(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new relationship"""
        # Get manager with connection check
        rel_crud = get_manager_or_error("relationship_crud_manager")
        if isinstance(rel_crud, dict):  # Error response
            return rel_crud

        from_table = args.get("from_table")
        from_column = args.get("from_column")
        to_table = args.get("to_table")
        to_column = args.get("to_column")

        # Validate required parameters
        if error := validate_relationship_create_params(
            from_table, from_column, to_table, to_column
        ):
            return error

        return rel_crud.create_relationship(
            from_table=from_table,
            from_column=from_column,
            to_table=to_table,
            to_column=to_column,
            name=args.get("name"),
            from_cardinality=args.get("from_cardinality", "Many"),
            to_cardinality=args.get("to_cardinality", "One"),
            cross_filtering_behavior=args.get("cross_filtering_behavior", "OneDirection"),
            is_active=args.get("is_active", True),
            allow_ambiguous=bool(args.get("allow_ambiguous", False)),
        )

    def _update_relationship(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Update an existing relationship"""
        # Get manager with connection check
        rel_crud = get_manager_or_error("relationship_crud_manager")
        if isinstance(rel_crud, dict):  # Error response
            return rel_crud

        relationship_name = get_relationship_name(args)

        # Validate required parameter
        if error := validate_required(relationship_name, "relationship_name", "update"):
            return error

        # C-42: cardinality / security filtering / referential integrity / date join
        extra = {
            k: args[k]
            for k in (
                "from_cardinality",
                "to_cardinality",
                "security_filtering_behavior",
                "rely_on_referential_integrity",
                "join_on_date_behavior",
                "allow_ambiguous",
            )
            if args.get(k) is not None
        }
        return rel_crud.update_relationship(
            relationship_name=relationship_name,
            cross_filtering_behavior=args.get("cross_filtering_behavior"),
            is_active=args.get("is_active"),
            new_name=args.get("new_name"),
            **extra,
        )

    def _delete_relationship(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Delete a relationship"""
        # Get manager with connection check
        rel_crud = get_manager_or_error("relationship_crud_manager")
        if isinstance(rel_crud, dict):  # Error response
            return rel_crud

        relationship_name = get_relationship_name(args)

        # Validate required parameter
        if error := validate_required(relationship_name, "relationship_name", "delete"):
            return error

        return rel_crud.delete_relationship(relationship_name)

    def _activate_relationship(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Activate a relationship"""
        # Get manager with connection check
        rel_crud = get_manager_or_error("relationship_crud_manager")
        if isinstance(rel_crud, dict):  # Error response
            return rel_crud

        relationship_name = get_relationship_name(args)

        # Validate required parameter
        if error := validate_required(relationship_name, "relationship_name", "activate"):
            return error

        return rel_crud.activate_relationship(relationship_name)

    def _deactivate_relationship(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Deactivate a relationship"""
        # Get manager with connection check
        rel_crud = get_manager_or_error("relationship_crud_manager")
        if isinstance(rel_crud, dict):  # Error response
            return rel_crud

        relationship_name = get_relationship_name(args)

        # Validate required parameter
        if error := validate_required(relationship_name, "relationship_name", "deactivate"):
            return error

        return rel_crud.deactivate_relationship(relationship_name)

    def _rename_relationship(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Rename a relationship (QG-01). DAX never references a relationship by name
        (USERELATIONSHIP takes columns), so there is no cascade and no report rebind."""
        relationship_name = get_relationship_name(args)
        if error := validate_required(relationship_name, "relationship_name", "rename"):
            return error
        new_name = args.get("new_name")
        if error := validate_required(new_name, "new_name", "rename"):
            return error
        if args.get("preview") is True:
            # A rename has nothing to preview beyond the names; never write on a dry-run.
            return {
                "success": True,
                "preview": True,
                "relationship": relationship_name,
                "new_name": new_name,
                "change_count": 0,
                "note": "No DAX or report references a relationship by name; nothing to cascade.",
            }
        rel_crud = get_manager_or_error("relationship_crud_manager")
        if isinstance(rel_crud, dict):
            return rel_crud
        return rel_crud.rename_relationship(relationship_name, new_name)
