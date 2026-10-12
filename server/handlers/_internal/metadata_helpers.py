"""
INTERNAL HELPER — Not a registered MCP tool.
Provides helper functions consumed by active handlers.

Provides handle_search_string and handle_search_objects for query_handler.py.
"""

from typing import Dict, Any
import logging

from core.validation import (
    get_manager_or_error,
    apply_pagination,
    apply_pagination_with_defaults,
    get_optional_bool,
)

logger = logging.getLogger(__name__)


def handle_search_string(args: Dict[str, Any]) -> Dict[str, Any]:
    """Search in measure names and/or expressions"""
    # Get manager with connection check
    qe = get_manager_or_error("query_executor")
    if isinstance(qe, dict):  # Error response
        return qe

    search_text = args.get("search_text", "")
    search_in_expression = get_optional_bool(args, "search_in_expression", True)
    search_in_name = get_optional_bool(args, "search_in_name", True)

    result = qe.search_measures_dax(search_text, search_in_expression, search_in_name)
    return apply_pagination_with_defaults(result, args)


def handle_search_objects(args: Dict[str, Any]) -> Dict[str, Any]:
    """Search across tables, columns, and measures"""
    # Get manager with connection check
    qe = get_manager_or_error("query_executor")
    if isinstance(qe, dict):  # Error response
        return qe

    pattern = args.get("pattern", "*")
    types = args.get("types", ["tables", "columns", "measures"])

    result = qe.search_objects_dax(pattern, types)
    return apply_pagination(result, args, rows_key="rows")
