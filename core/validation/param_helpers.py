"""
Parameter extraction helpers for backward compatibility.

These helpers consolidate duplicate parameter aliasing patterns across operations.
Reduces ~50 lines of duplicated code.
"""

from typing import Any, Dict, List, Optional, Tuple


def get_table_name(args: Dict[str, Any]) -> Optional[str]:
    """Extract table name from args. Supports: 'table_name', 'table'."""
    return args.get("table_name") or args.get("table")


def get_measure_name(args: Dict[str, Any]) -> Optional[str]:
    """Extract measure name from args. Supports: 'measure_name', 'measure'."""
    return args.get("measure_name") or args.get("measure")


def get_column_name(args: Dict[str, Any]) -> Optional[str]:
    """Extract column name from args. Supports: 'column_name', 'column'."""
    return args.get("column_name") or args.get("column")


def get_relationship_name(args: Dict[str, Any]) -> Optional[str]:
    """Extract relationship name from args. Supports: 'relationship_name', 'name'."""
    return args.get("relationship_name") or args.get("name")


def get_group_name(args: Dict[str, Any]) -> Optional[str]:
    """Extract calculation group name from args. Supports: 'group_name', 'name'."""
    return args.get("group_name") or args.get("name")


def get_role_name(args: Dict[str, Any]) -> Optional[str]:
    """Extract role name from args. Supports: 'role_name', 'name'."""
    return args.get("role_name") or args.get("name")


def get_format_string(args: Dict[str, Any]) -> Optional[str]:
    """Extract format string from args. Supports: 'format_string', 'format'."""
    return args.get("format_string") or args.get("format")


def get_source_table(args: Dict[str, Any]) -> Optional[str]:
    """Extract source table for move operations. Supports: 'source_table', 'table_name', 'table'."""
    return args.get("source_table") or args.get("table_name") or args.get("table")


def get_target_table(args: Dict[str, Any]) -> Optional[str]:
    """Extract target table for move operations. Supports: 'target_table', 'new_table'."""
    return args.get("target_table") or args.get("new_table")


def get_new_name(args: Dict[str, Any]) -> Optional[str]:
    """Extract new name for rename operations from args['new_name']."""
    return args.get("new_name")


def extract_params(args: Dict[str, Any], *param_specs: Tuple[str, ...]) -> Tuple:
    """Extract multiple parameters with alias fallback. Each spec is a tuple of keys tried in order."""
    results = []
    for aliases in param_specs:
        value = None
        for key in aliases:
            value = args.get(key)
            if value is not None:
                break
        results.append(value)
    return tuple(results)


def extract_table_and_name(
    args: Dict[str, Any], name_key: str, name_aliases: List[str] = None
) -> Tuple[Optional[str], Optional[str]]:
    """Extract (table_name, item_name) from args. name_aliases are fallback keys for item_name."""
    table_name = get_table_name(args)

    aliases = [name_key] + (name_aliases or [])
    item_name = None
    for key in aliases:
        item_name = args.get(key)
        if item_name is not None:
            break

    return (table_name, item_name)


def extract_crud_params(args: Dict[str, Any], entity_type: str) -> Dict[str, Any]:
    """Extract and normalize CRUD params for entity_type ('measure', 'column', 'table', 'relationship', 'calculation_group')."""
    result = {
        "new_name": get_new_name(args),
        "expression": args.get("expression"),
        "description": args.get("description"),
        "hidden": args.get("hidden"),
        "display_folder": args.get("display_folder"),
        "format_string": get_format_string(args),
    }

    if entity_type == "measure":
        result["table_name"] = get_table_name(args)
        result["name"] = get_measure_name(args)
    elif entity_type == "column":
        result["table_name"] = get_table_name(args)
        result["name"] = get_column_name(args)
        result["data_type"] = args.get("data_type", "String")
        result["source_column"] = args.get("source_column")
    elif entity_type == "table":
        result["name"] = get_table_name(args)
    elif entity_type == "relationship":
        result["name"] = get_relationship_name(args)
        result["from_table"] = args.get("from_table")
        result["from_column"] = args.get("from_column")
        result["to_table"] = args.get("to_table")
        result["to_column"] = args.get("to_column")
        result["is_active"] = args.get("is_active")
        result["cross_filtering_behavior"] = args.get("cross_filtering_behavior")
        result["from_cardinality"] = args.get("from_cardinality", "Many")
        result["to_cardinality"] = args.get("to_cardinality", "One")
    elif entity_type == "calculation_group":
        result["name"] = get_group_name(args)
        result["items"] = args.get("items", [])
        result["precedence"] = args.get("precedence")

    return result


def get_pagination_params(args: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    """Extract (page_size, next_token) from args."""
    return (args.get("page_size"), args.get("next_token"))


def get_optional_int(args: Dict[str, Any], key: str, default: int) -> int:
    """Get an integer from args[key], returning default if missing or non-coercible."""
    value = args.get(key)
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def get_optional_bool(args: Dict[str, Any], key: str, default: bool) -> bool:
    """Get a boolean from args[key], returning default if missing. Coerces strings 'true'/'1'/'yes'."""
    value = args.get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    return bool(value)
