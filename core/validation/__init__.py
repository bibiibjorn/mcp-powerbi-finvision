"""
Validation utilities for PBIXRay MCP Server.

This package provides:
- Manager decorators for connection/manager validation
- Parameter helpers for backward compatibility
- Pagination helpers for consistent pagination
- Parameter validators for validation error handling
- Error decorators for consistent error handling
"""

# Manager decorators - eliminate ~200 lines of duplicate code
from .manager_decorators import (
    require_connection,
    require_manager,
    require_managers,
    ManagerContext,
    get_manager_or_error,
    check_connection_and_manager,
)

# Parameter helpers - eliminate ~50 lines of duplicate code
from .param_helpers import (
    get_table_name,
    get_measure_name,
    get_column_name,
    get_relationship_name,
    get_group_name,
    get_role_name,
    get_format_string,
    get_source_table,
    get_target_table,
    get_new_name,
    extract_params,
    extract_table_and_name,
    extract_crud_params,
    get_pagination_params,
    get_optional_int,
    get_optional_bool,
)

# Pagination helpers - eliminate ~80 lines of duplicate code
from .pagination_helpers import (
    apply_default_page_size,
    apply_pagination,
    apply_pagination_with_defaults,
    apply_describe_table_defaults,
    get_page_size_with_default,
    paginate_list,
    wrap_with_pagination_metadata,
)

# Parameter validators - eliminate ~60 lines of duplicate code
from .param_validators import (
    validate_required,
    validate_required_params,
    validate_any_of,
    validate_enum,
    validate_positive_int,
    validate_table_and_item,
    validate_create_params,
    validate_rename_params,
    validate_move_params,
    validate_relationship_create_params,
    ValidationBuilder,
)

# Error decorators - eliminate ~90 lines of duplicate code
from .error_decorators import (
    handle_operation_errors,
    handle_query_errors,
    log_operation,
    with_error_context,
    retry_on_connection_error,
    wrap_result,
    OperationErrorHandler,
    combine_decorators,
)

# Error handler and response (existing)
from .error_handler import ErrorHandler, safe_tool_execution
from .error_response import ErrorResponse

# Constants
from .constants import QueryLimits

__all__ = [
    # Manager decorators
    "require_connection",
    "require_manager",
    "require_managers",
    "ManagerContext",
    "get_manager_or_error",
    "check_connection_and_manager",
    # Parameter helpers
    "get_table_name",
    "get_measure_name",
    "get_column_name",
    "get_relationship_name",
    "get_group_name",
    "get_role_name",
    "get_format_string",
    "get_source_table",
    "get_target_table",
    "get_new_name",
    "extract_params",
    "extract_table_and_name",
    "extract_crud_params",
    "get_pagination_params",
    "get_optional_int",
    "get_optional_bool",
    # Pagination helpers
    "apply_default_page_size",
    "apply_pagination",
    "apply_pagination_with_defaults",
    "apply_describe_table_defaults",
    "get_page_size_with_default",
    "paginate_list",
    "wrap_with_pagination_metadata",
    # Parameter validators
    "validate_required",
    "validate_required_params",
    "validate_any_of",
    "validate_enum",
    "validate_positive_int",
    "validate_table_and_item",
    "validate_create_params",
    "validate_rename_params",
    "validate_move_params",
    "validate_relationship_create_params",
    "ValidationBuilder",
    # Error decorators
    "handle_operation_errors",
    "handle_query_errors",
    "log_operation",
    "with_error_context",
    "retry_on_connection_error",
    "wrap_result",
    "OperationErrorHandler",
    "combine_decorators",
    # Existing exports
    "ErrorHandler",
    "ErrorResponse",
    "safe_tool_execution",
    "QueryLimits",
]
