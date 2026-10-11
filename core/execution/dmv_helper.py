"""DMV / INFO.* result-row helpers.

B-31: the unused DmvHelper query-builder class was removed; query building
lives in core.infrastructure.query_executor.
"""

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def get_field_value(row: Dict[str, Any], keys: List[str]) -> Any:
    """
    Extract field value from DMV query result row.

    Tries keys with and without bracket notation ([]).
    DMV results may return fields as "Name" or "[Name]" depending on the query,
    so this helper tries both variations.

    Args:
        row: Dictionary containing DMV query result row
        keys: List of field names to try (without brackets)

    Returns:
        First non-empty value found, or None if all keys return empty/None

    Example:
        >>> row = {"[TableName]": "Sales", "Name": "Customer"}
        >>> get_field_value(row, ["TableName", "Table"])
        "Sales"
    """
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return row[k]
        bk = f"[{k}]"
        if bk in row and row[bk] not in (None, ""):
            return row[bk]
    return None
