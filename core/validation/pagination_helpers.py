"""
Pagination helpers for consistent pagination handling across operations.

These helpers consolidate duplicate pagination logic patterns.
Reduces ~80 lines of duplicated code.
"""

from typing import Any, Dict, List, Optional, Tuple

# Pagination limits (canonical source — server/middleware.py re-exports these)
MAX_PAGE_SIZE = 10000
MAX_PAGINATION_OFFSET = 1000000


def paginate(
    result: Any, page_size: Optional[int], next_token: Optional[str], list_keys: List[str]
) -> Any:
    """
    Paginate result arrays with continuation tokens.

    Args:
        result: Result dict containing arrays to paginate
        page_size: Items per page (must be between 1 and MAX_PAGE_SIZE)
        next_token: Continuation token
        list_keys: Keys in result dict that contain lists to paginate

    Returns:
        Result with paginated data and next_token if more data available
    """
    if not isinstance(result, dict) or not result.get("success"):
        return result

    if page_size is None or page_size <= 0:
        return result

    # Validate page_size bounds
    if page_size > MAX_PAGE_SIZE:
        return {
            "success": False,
            "error": f"page_size exceeds maximum allowed value of {MAX_PAGE_SIZE}",
            "error_type": "ValidationError",
        }

    # Parse token (format: "key:offset")
    start_offset = 0
    target_key = None
    if next_token:
        try:
            parts = next_token.split(":", 1)
            if len(parts) == 2:
                target_key, offset_str = parts
                start_offset = int(offset_str)
                # Validate offset is non-negative and reasonable
                if start_offset < 0 or start_offset > MAX_PAGINATION_OFFSET:
                    return {
                        "success": False,
                        "error": "Invalid next_token: offset out of valid range",
                        "error_type": "ValidationError",
                    }
        except (ValueError, AttributeError) as e:
            return {
                "success": False,
                "error": f"Invalid next_token format: {str(e)}",
                "error_type": "ValidationError",
            }

    # Apply pagination to each list key
    new_token = None
    found_target = False
    for key in list_keys:
        if key not in result:
            continue

        arr = result.get(key, [])
        if not isinstance(arr, list):
            continue

        # Skip if this isn't the target key (when continuing pagination)
        if target_key and key != target_key:
            continue

        if target_key is not None and key == target_key:
            found_target = True

        # Paginate
        paginated, token = paginate_section(arr, page_size, start_offset)
        result[key] = paginated

        if token is not None:
            new_token = f"{key}:{token}"
            break

    # A continuation token named a key that's no longer present (as a paginable
    # list) in the result — the page set changed under us. Signal explicitly
    # rather than lying with has_more=False.
    if target_key is not None and not found_target:
        # A-18: a failure, not {success: True, error_type: ...}.
        return {
            **result,
            "success": False,
            "error_type": "stale_pagination_token",
            "error": (
                f"pagination key {target_key!r} not present in result — the result set "
                "changed since the token was issued; restart without next_token"
            ),
        }

    # Add pagination metadata
    if new_token:
        result["next_token"] = new_token
        result["has_more"] = True
    else:
        result.pop("next_token", None)
        result["has_more"] = False

    return result


def paginate_section(arr: Any, size: Optional[Any], offset: int = 0) -> Tuple[list, Optional[str]]:
    """
    Paginate a single array section.

    Returns:
        (paginated_array, next_offset_or_none)
    """
    if not isinstance(arr, list):
        return arr, None

    if size is None or size <= 0:
        return arr, None

    try:
        size = int(size)
        offset = int(offset)
    except (ValueError, TypeError):
        return arr, None

    end = offset + size
    paginated = arr[offset:end]

    next_token = str(end) if end < len(arr) else None
    return paginated, next_token


def apply_default_limits(arguments: dict, defaults: dict) -> dict:
    """
    Apply default values to missing arguments.

    Args:
        arguments: Tool arguments
        defaults: Default values dict

    Returns:
        Arguments with defaults applied
    """
    args = dict(arguments)
    for key, default_value in defaults.items():
        if key not in args or args[key] is None:
            args[key] = default_value
    return args


def apply_default_page_size(
    args: Dict[str, Any], default_size: Optional[int] = None
) -> Dict[str, Any]:
    """Set args['page_size'] from default_size or limits_manager if not already present."""
    if "page_size" not in args or args["page_size"] is None:
        if default_size is not None:
            args["page_size"] = default_size
        else:
            from core.infrastructure.limits_manager import get_limits

            limits = get_limits()
            args["page_size"] = limits.query.default_page_size
    return args


def apply_pagination(
    result: Dict[str, Any], args: Dict[str, Any], rows_key: str = "rows"
) -> Dict[str, Any]:
    """Apply pagination from args['page_size']/args['next_token'] to result[rows_key]."""
    page_size = args.get("page_size")
    next_token = args.get("next_token")

    if page_size or next_token:
        result = paginate(result, page_size, next_token, [rows_key])

    return result


def apply_pagination_with_defaults(
    result: Dict[str, Any],
    args: Dict[str, Any],
    rows_key: str = "rows",
    default_page_size: Optional[int] = None,
) -> Dict[str, Any]:
    """Apply default page size then paginate result. Combines apply_default_page_size + apply_pagination."""
    # Apply default page size
    apply_default_page_size(args, default_page_size)

    # Apply pagination
    return apply_pagination(result, args, rows_key)


def apply_describe_table_defaults(args: Dict[str, Any]) -> Dict[str, Any]:
    """Set columns/measures/relationships page sizes from limits_manager for describe_table operation."""
    from core.infrastructure.limits_manager import get_limits

    limits = get_limits()
    defaults = {
        "columns_page_size": limits.token.describe_table_columns_page_size,
        "measures_page_size": limits.token.describe_table_measures_page_size,
        "relationships_page_size": limits.token.describe_table_relationships_page_size,
    }
    return apply_default_limits(args, defaults)


def get_page_size_with_default(args: Dict[str, Any], default: Optional[int] = None) -> int:
    """Get page_size from args, falling back to default or limits_manager default."""
    page_size = args.get("page_size")
    if page_size is not None:
        return page_size

    if default is not None:
        return default

    from core.infrastructure.limits_manager import get_limits

    limits = get_limits()
    return limits.query.default_page_size


def paginate_list(
    items: List[Any], page_size: Optional[int] = None, next_token: Optional[str] = None
) -> Dict[str, Any]:
    """Paginate a raw list; returns dict with 'items', 'total_count', 'has_more', and optionally 'next_token'."""
    total = len(items)

    # Parse offset from token
    offset = 0
    if next_token:
        try:
            offset = int(next_token)
        except ValueError:
            offset = 0

    # Apply pagination
    if page_size is not None and page_size > 0:
        end = offset + page_size
        page_items = items[offset:end]
        has_more = end < total
        new_token = str(end) if has_more else None
    else:
        page_items = items
        has_more = False
        new_token = None

    result = {"items": page_items, "total_count": total, "has_more": has_more}

    if new_token:
        result["next_token"] = new_token

    return result


def wrap_with_pagination_metadata(
    result: Dict[str, Any], args: Dict[str, Any], rows_key: str = "rows"
) -> Dict[str, Any]:
    """Add 'pagination' metadata to result without re-slicing the data."""
    if not result.get("success"):
        return result

    items = result.get(rows_key, [])
    page_size = args.get("page_size")
    next_token = args.get("next_token")

    result["pagination"] = {
        "page_size": page_size,
        "current_count": len(items),
        "has_more": result.get("has_more", False),
    }

    if next_token:
        result["pagination"]["continued_from"] = next_token

    return result
