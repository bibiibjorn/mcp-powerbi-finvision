"""
Unified role operations handler
Extends: list_roles + new CRUD and permission operations

Refactored to use validation utilities for reduced code duplication.
"""

from typing import Dict, Any
import logging
from .base_operations import BaseOperationsHandler

# Import validation utilities
from core.validation import (
    get_manager_or_error,
)

logger = logging.getLogger(__name__)


class RoleOperationsHandler(BaseOperationsHandler):
    """Handles all RLS/OLS role-related operations"""

    def __init__(self):
        super().__init__("role_operations")

        # Register all operations
        self.register_operation("list", self._list_roles)
        # Future: create, update, delete, rename,
        # create_permission, update_permission, delete_permission, test_role

    def _list_roles(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """List RLS roles"""
        # Get manager with connection check
        rls_mgr = get_manager_or_error("rls_manager")
        if isinstance(rls_mgr, dict):  # Error response
            return rls_mgr

        return rls_mgr.list_roles()

    # Future methods: _create_role, _update_role, _delete_role, _rename_role,
    # _create_permission, _update_permission, _delete_permission, _test_role
