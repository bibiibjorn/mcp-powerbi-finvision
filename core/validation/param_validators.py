"""
Parameter validators for consistent validation error handling.

These validators consolidate duplicate validation error response patterns.
Reduces ~60 lines of duplicated code.
"""

from typing import Any, Dict, List, Optional, Tuple


def validate_required(value: Any, param_name: str, operation: str) -> Optional[Dict[str, Any]]:
    """Return error dict if value is falsy, else None."""
    if not value:
        return {"success": False, "error": f"{param_name} is required for operation: {operation}"}
    return None


def validate_required_params(*params: Tuple[Any, str], operation: str) -> Optional[Dict[str, Any]]:
    """Validate multiple (value, param_name) tuples are provided; returns error dict listing missing params or None."""
    missing = []
    for value, param_name in params:
        if not value:
            missing.append(param_name)

    if missing:
        if len(missing) == 1:
            return {
                "success": False,
                "error": f"{missing[0]} is required for operation: {operation}",
            }
        else:
            params_str = " and ".join(missing)
            return {
                "success": False,
                "error": f"{params_str} are required for operation: {operation}",
            }

    return None


def validate_any_of(*params: Tuple[Any, str], operation: str) -> Optional[Dict[str, Any]]:
    """Return error dict if none of the (value, param_name) tuples are non-None, else None."""
    for value, _ in params:
        if value is not None:
            return None

    param_names = [name for _, name in params]
    params_str = ", ".join(param_names)
    return {
        "success": False,
        "error": f"At least one of {params_str} is required for operation: {operation}",
    }


def validate_enum(
    value: Any,
    param_name: str,
    allowed_values: List[str],
    operation: str,
    case_sensitive: bool = False,
) -> Optional[Dict[str, Any]]:
    """Return error dict if value is not in allowed_values (case-insensitive by default), or None if valid/None."""
    if value is None:
        return None

    check_value = value if case_sensitive else str(value).lower()
    check_allowed = allowed_values if case_sensitive else [v.lower() for v in allowed_values]

    if check_value not in check_allowed:
        return {
            "success": False,
            "error": f"Invalid {param_name} '{value}' for operation: {operation}. Allowed values: {', '.join(allowed_values)}",
        }

    return None


def validate_positive_int(
    value: Any,
    param_name: str,
    operation: str,
    max_value: Optional[int] = None,
    allow_none: bool = True,
) -> Optional[Dict[str, Any]]:
    """Return error dict if value is not a positive integer or exceeds max_value; None if valid."""
    if value is None:
        if allow_none:
            return None
        else:
            return {
                "success": False,
                "error": f"{param_name} is required for operation: {operation}",
            }

    try:
        int_value = int(value)
    except (ValueError, TypeError):
        return {
            "success": False,
            "error": f"{param_name} must be an integer for operation: {operation}",
        }

    if int_value <= 0:
        return {
            "success": False,
            "error": f"{param_name} must be a positive integer for operation: {operation}",
        }

    if max_value is not None and int_value > max_value:
        return {
            "success": False,
            "error": f"{param_name} exceeds maximum value of {max_value} for operation: {operation}",
        }

    return None


def validate_table_and_item(
    table_name: Any, item_name: Any, item_param_name: str, operation: str
) -> Optional[Dict[str, Any]]:
    """Validate that table_name and item_name are both provided."""
    return validate_required_params(
        (table_name, "table_name"), (item_name, item_param_name), operation=operation
    )


def validate_create_params(
    table_name: Any,
    item_name: Any,
    expression: Any,
    item_param_name: str,
    require_expression: bool = True,
) -> Optional[Dict[str, Any]]:
    """Validate create params: table_name, item_name, and optionally expression."""
    if require_expression:
        return validate_required_params(
            (table_name, "table_name"),
            (item_name, item_param_name),
            (expression, "expression"),
            operation="create",
        )
    else:
        return validate_required_params(
            (table_name, "table_name"), (item_name, item_param_name), operation="create"
        )


def validate_rename_params(
    table_name: Any, item_name: Any, new_name: Any, item_param_name: str
) -> Optional[Dict[str, Any]]:
    """Validate rename params: table_name, item_name, and new_name are all required."""
    return validate_required_params(
        (table_name, "table_name"),
        (item_name, item_param_name),
        (new_name, "new_name"),
        operation="rename",
    )


def validate_move_params(
    source_table: Any, item_name: Any, target_table: Any, item_param_name: str
) -> Optional[Dict[str, Any]]:
    """Validate move params: source_table, item_name, and target_table are all required."""
    return validate_required_params(
        (source_table, "source_table"),
        (item_name, item_param_name),
        (target_table, "target_table"),
        operation="move",
    )


def validate_relationship_create_params(
    from_table: Any, from_column: Any, to_table: Any, to_column: Any
) -> Optional[Dict[str, Any]]:
    """Validate that from_table, from_column, to_table, and to_column are all provided."""
    return validate_required_params(
        (from_table, "from_table"),
        (from_column, "from_column"),
        (to_table, "to_table"),
        (to_column, "to_column"),
        operation="create",
    )


class ValidationBuilder:
    """Fluent builder for complex validation logic. Chain .require(), .enum(), .positive_int(), then call .validate()."""

    def __init__(self, operation: str):
        self.operation = operation
        self._errors: List[str] = []

    def require(self, value: Any, param_name: str) -> "ValidationBuilder":
        """Add a required parameter validation."""
        if not value:
            self._errors.append(f"{param_name} is required")
        return self

    def enum(
        self, value: Any, param_name: str, allowed: List[str], case_sensitive: bool = False
    ) -> "ValidationBuilder":
        """Add enum validation."""
        if value is not None:
            check_value = value if case_sensitive else str(value).lower()
            check_allowed = allowed if case_sensitive else [v.lower() for v in allowed]
            if check_value not in check_allowed:
                self._errors.append(
                    f"Invalid {param_name} '{value}'. Allowed: {', '.join(allowed)}"
                )
        return self

    def positive_int(
        self, value: Any, param_name: str, max_value: Optional[int] = None
    ) -> "ValidationBuilder":
        """Add positive integer validation."""
        if value is not None:
            try:
                int_value = int(value)
                if int_value <= 0:
                    self._errors.append(f"{param_name} must be positive")
                elif max_value and int_value > max_value:
                    self._errors.append(f"{param_name} exceeds maximum {max_value}")
            except (ValueError, TypeError):
                self._errors.append(f"{param_name} must be an integer")
        return self

    def validate(self) -> Optional[Dict[str, Any]]:
        """Execute validation and return error dict if any failures."""
        if not self._errors:
            return None

        if len(self._errors) == 1:
            return {"success": False, "error": f"{self._errors[0]} for operation: {self.operation}"}
        else:
            return {
                "success": False,
                "error": f'Validation errors for operation {self.operation}: {"; ".join(self._errors)}',
            }
