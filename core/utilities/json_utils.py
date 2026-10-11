"""
JSON Utilities with orjson Optimization

Provides JSON loading/dumping functions with automatic fallback from orjson to standard json.
"""

import json
import logging
import math
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Union
from uuid import UUID

logger = logging.getLogger(__name__)


class MCPJsonEncoder(json.JSONEncoder):
    """JSON encoder for MCP responses with structured handling of common
    non-JSON-native types.

    Why: prior code used ``json.dumps(..., default=str)`` everywhere, which
    silently stringifies datetime/Decimal/UUID into opaque blobs. Tooling
    parsing the autonomous audit log or response stream cannot reliably
    recover the original value.

    Strategy:
      * datetime / date  → ISO 8601 string (sortable, parseable)
      * Decimal          → float (lossy but JSON-native; callers needing
                           exact decimals must serialize as string upstream)
      * UUID             → str(uuid)
      * set / frozenset  → sorted list (deterministic for diffs/tests)
      * bytes            → ``"<bytes:N>"`` placeholder (binary doesn't
                           belong in JSON; raise visibility instead of
                           hiding it as "b'...'")
      * Anything else    → repr fallback so we never crash on serialization
                           but the response carries a recognizable marker.
      * NaN / ±Infinity  → ``null`` (A-28: ``NaN`` is not JSON; strict clients
                           reject the whole response). Only when the caller did
                           not pass ``allow_nan=False`` explicitly.
    """

    def iterencode(self, o: Any, _one_shot: bool = False):  # noqa: D401
        if not self.allow_nan:
            return super().iterencode(o, _one_shot)
        # Strict pass first (common case, one encode); on a non-finite float
        # sanitize the tree to null and encode again (rare path).
        self.allow_nan = False
        try:
            return iter(list(super().iterencode(o, _one_shot)))
        except ValueError:
            return iter(list(super().iterencode(_replace_non_finite(o), _one_shot)))
        finally:
            self.allow_nan = True

    def default(self, obj: Any) -> Any:  # noqa: D401
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, UUID):
            return str(obj)
        if isinstance(obj, (set, frozenset)):
            try:
                return sorted(obj)
            except TypeError:
                return list(obj)
        if isinstance(obj, bytes):
            return f"<bytes:{len(obj)}>"
        try:
            return super().default(obj)
        except TypeError:
            return f"<unserializable:{type(obj).__name__}>"


def _replace_non_finite(value: Any, _depth: int = 0) -> Any:
    """Copy of ``value`` with NaN/±Infinity floats replaced by None."""
    if _depth > 500:
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _replace_non_finite(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_replace_non_finite(v, _depth + 1) for v in value]
    return value


_UTF8_BOM = b"\xef\xbb\xbf"
_BOM_CHAR = chr(0xFEFF)


# Try to import orjson for better performance
try:
    import orjson

    HAS_ORJSON = True
except ImportError:
    HAS_ORJSON = False
    logger.debug("orjson not available, using standard json")


# Maximum file size for JSON loading (500 MB)
MAX_JSON_FILE_SIZE = 500 * 1024 * 1024


def load_json(file_path: Union[str, Path], max_size: int = MAX_JSON_FILE_SIZE) -> Any:
    """
    Load JSON from file with orjson optimization.

    Args:
        file_path: Path to JSON file
        max_size: Maximum file size in bytes (default 500MB)

    Returns:
        Parsed JSON data

    Raises:
        ValueError: If file exceeds max_size

    Example:
        >>> data = load_json("model.json")
    """
    file_path = Path(file_path)
    file_size = file_path.stat().st_size
    if file_size > max_size:
        raise ValueError(
            f"JSON file too large: {file_size / 1024 / 1024:.1f}MB exceeds "
            f"limit of {max_size / 1024 / 1024:.1f}MB"
        )
    with open(file_path, "rb") as f:
        data = f.read()
    # A-28: files saved by Notepad / PowerShell carry a UTF-8 BOM, which
    # orjson (and json.loads on bytes) reject at line 1 column 1.
    if data.startswith(_UTF8_BOM):
        data = data[len(_UTF8_BOM) :]
    if HAS_ORJSON:
        return orjson.loads(data)
    return json.loads(data.decode("utf-8"))


def loads_json(data: Union[bytes, str]) -> Any:
    """
    Parse JSON string/bytes with orjson optimization.

    Args:
        data: JSON string or bytes

    Returns:
        Parsed JSON data

    Example:
        >>> obj = loads_json('{"key": "value"}')
        >>> obj = loads_json(b'{"key": "value"}')
    """
    if isinstance(data, bytes) and data.startswith(_UTF8_BOM):
        data = data[len(_UTF8_BOM) :]
    elif isinstance(data, str) and data.startswith(_BOM_CHAR):
        data = data[1:]
    if HAS_ORJSON and isinstance(data, bytes):
        return orjson.loads(data)
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    return json.loads(data)


def dump_json(
    data: Any,
    file_path: Union[str, Path],
    indent: int = 2,
    *,
    ensure_ascii: bool = False,
) -> None:
    """
    Dump data to JSON file with orjson optimization.

    Args:
        data: Data to serialize
        file_path: Output file path
        indent: Indentation level (default: 2)
        ensure_ascii: When True, escape non-ASCII characters as \\uXXXX. Forces
            the stdlib branch (orjson does not support this option). **MUST be
            True for any PBIR output** — Windows `pbir` CLI re-encodes through
            Latin-1 and corrupts Δ/∆/€/… if they are written as raw UTF-8
            bytes. For PBIR writes prefer `core.utilities.pbip_utils.save_json_file`
            which forces ASCII escaping and uses stdlib unconditionally.

    Example:
        >>> dump_json({"key": "value"}, "output.json")                # non-PBIR
        >>> dump_json(pbir_dict, "visual.json", ensure_ascii=True)    # PBIR-safe
    """
    # P1-17 fix: the conditional expression `encoding=None if HAS_ORJSON else
    # 'utf-8'` combined with `mode='wb' if HAS_ORJSON else 'w'` works today but
    # raises ValueError on some Python versions because passing encoding=None to
    # binary-mode `open()` is rejected. Split into two explicit branches.
    #
    # 2026-04-22 audit (P1): orjson has no ASCII-escape option, so when the
    # caller requires it we fall back to stdlib. Default ensure_ascii=False
    # preserves prior behavior for non-PBIR callers that want raw UTF-8.
    if HAS_ORJSON and not ensure_ascii:
        with open(file_path, "wb") as f:
            f.write(orjson.dumps(data, option=orjson.OPT_INDENT_2))
    else:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=ensure_ascii)


def dumps_json(data: Any, indent: int = 2) -> str:
    """
    Serialize data to JSON string with orjson optimization.

    Args:
        data: Data to serialize. Pass `indent=0` (or `None`) for compact JSON.
        indent: Indentation level (default: 2). Audit T83: previously the
            orjson path ignored this argument and always pretty-printed,
            which inflated tokens.

    Returns:
        JSON string

    Example:
        >>> json_str = dumps_json({"key": "value"})
    """
    if HAS_ORJSON:
        option = orjson.OPT_INDENT_2 if indent and indent > 0 else 0
        return orjson.dumps(data, option=option).decode("utf-8")
    return json.dumps(data, indent=indent if (indent and indent > 0) else None)
