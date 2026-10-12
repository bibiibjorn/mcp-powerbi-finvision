"""
Type Conversion Utilities

Safe type conversion functions with fallback defaults.
"""

from typing import Any


def safe_int(value: Any, default: int = 0) -> int:
    """
    Safely convert value to integer with fallback default.

    Args:
        value: Value to convert
        default: Default value if conversion fails (default: 0)

    Returns:
        Integer value or default

    Example:
        >>> safe_int("123")
        123
        >>> safe_int("1,234.56")
        1234
        >>> safe_int(None, -1)
        -1
        >>> safe_int("invalid")
        0
    """
    try:
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return int(value)
        # Handle string with commas and whitespace
        s = str(value).replace(",", "").strip()
        return int(float(s)) if s else default
    except Exception:
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    """
    Safely convert value to float with fallback default.

    Args:
        value: Value to convert
        default: Default value if conversion fails (default: 0.0)

    Returns:
        Float value or default

    Example:
        >>> safe_float("123.45")
        123.45
        >>> safe_float("1,234.56")
        1234.56
        >>> safe_float(None, -1.0)
        -1.0
    """
    try:
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return float(value)
        # Handle string with commas and whitespace
        s = str(value).replace(",", "").strip()
        return float(s) if s else default
    except Exception:
        return default


def safe_bool(value: Any) -> bool:
    """
    Safely convert value to boolean.

    Args:
        value: Value to convert

    Returns:
        Boolean value

    Example:
        >>> safe_bool(True)
        True
        >>> safe_bool("true")
        True
        >>> safe_bool("1")
        True
        >>> safe_bool("yes")
        True
        >>> safe_bool("false")
        False
        >>> safe_bool(0)
        False
    """
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    return s in ("true", "1", "yes")
