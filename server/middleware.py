"""
Server Middleware Utilities

NOTE (WP2 2026-09-22): the MCP response pipeline lives in
``server/response_pipeline.py``. It no longer compacts keys, summarizes large
results to a 3-row sample, or truncates lists (A-05 / A-17 / L-06): results up
to 450K chars go inline verbatim, larger ones spill to ``output/``. The dead
key-compaction / truncation / token-estimation helpers that used to live here
were removed (deep review C-04); ``compact_response`` is the only survivor.
"""

from typing import Any, Dict, Tuple


def compact_response(
    data: Dict[str, Any], compact: bool = True, remove_empty: bool = True, remove_nulls: bool = True
) -> Dict[str, Any]:
    """
    Response cleanup for token optimization: empty removal and null removal.

    Args:
        data: Response data dictionary
        compact: Accepted for backward compatibility. Key RENAMING
            (``expression``→``expr``, ``success``→``ok`` …) is retired (L-06):
            it produced a second vocabulary that read/write callers could not
            round-trip. Keys are always kept verbatim.
        remove_empty: If True, remove empty strings, lists, dicts
        remove_nulls: If True, remove None values

    Returns:
        Optimized response dictionary
    """
    if not isinstance(data, dict):
        return data

    # Fields to preserve even if empty (important diagnostic info)
    PRESERVE_FIELDS = {
        "anomalies",
        "pbip_warning",
        "relationship_hints",
        "aggregation_info",
        "retry_info",
        "execution_mode",
        "success",
        "ok",
        "error",
        "err",
    }

    # Fields to skip entirely (verbose/redundant)
    SKIP_FIELDS = {"original", "selected_values_raw", "hint", "recommendations"}

    def clean_value(key: str, value: Any) -> Tuple[bool, Any]:
        """Returns (should_include, cleaned_value)"""
        # Always preserve important fields
        if key in PRESERVE_FIELDS:
            return True, value

        # Skip redundant fields
        if key in SKIP_FIELDS:
            return False, None

        # Handle None
        if value is None:
            return not remove_nulls, value

        # Handle empty values
        if remove_empty:
            if value == "" or value == [] or value == {}:
                return False, None

        # Recursively clean nested dicts
        if isinstance(value, dict):
            cleaned = clean_dict(value)
            return bool(cleaned) or not remove_empty, cleaned

        # Clean lists of dicts
        if isinstance(value, list):
            if value and isinstance(value[0], dict):
                cleaned_list = [clean_dict(item) for item in value]
                cleaned_list = [item for item in cleaned_list if item]
                return bool(cleaned_list) or not remove_empty, cleaned_list
            return bool(value) or not remove_empty, value

        return True, value

    def clean_dict(d: Dict) -> Dict:
        result = {}
        for k, v in d.items():
            should_include, cleaned = clean_value(k, v)
            if should_include:
                result[k] = cleaned
        return result

    return clean_dict(data)
