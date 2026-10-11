"""Lenient type coercion of tool arguments before schema validation.

MCP clients only type the arguments they see in ``tools/list``. Arguments that
the tool accepts but the client's (possibly cached or trimmed, see
``server/tool_surface.py``) schema doesn't declare arrive as strings — Claude
Code sends ``"true"`` for a boolean, ``"5"`` for an integer, a JSON string for
an array. Strict validation against the FULL schema then rejects a call that
the handler would have understood. This module converts such strings to the
type the full schema declares, and leaves everything else untouched.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Optional

_TRUE = {"true", "1", "yes", "on"}
_FALSE = {"false", "0", "no", "off"}


def _types(prop_schema: Dict[str, Any]) -> Iterable[str]:
    t = prop_schema.get("type")
    if isinstance(t, str):
        return (t,)
    if isinstance(t, list):
        return tuple(x for x in t if isinstance(x, str))
    return ()


def _coerce_value(value: Any, prop_schema: Dict[str, Any]) -> Any:
    if not isinstance(value, str) or not isinstance(prop_schema, dict):
        return value
    types = _types(prop_schema)
    if not types or "string" in types:
        return value
    text = value.strip()
    for t in types:
        if t == "boolean":
            low = text.lower()
            if low in _TRUE:
                return True
            if low in _FALSE:
                return False
        elif t == "integer":
            try:
                return int(text)
            except ValueError:
                try:
                    f = float(text)
                except ValueError:
                    continue
                if f.is_integer():
                    return int(f)
        elif t == "number":
            try:
                f = float(text)
            except ValueError:
                continue
            return int(f) if f.is_integer() and "." not in text and "e" not in text.lower() else f
        elif t in ("array", "object"):
            if text[:1] in ("[", "{"):
                try:
                    parsed = json.loads(text)
                except ValueError:
                    continue
                if (t == "array" and isinstance(parsed, list)) or (
                    t == "object" and isinstance(parsed, dict)
                ):
                    return parsed
    return value


def coerce_arguments(schema: Optional[Dict[str, Any]], args: Dict[str, Any]) -> Dict[str, str]:
    """Coerce top-level string args in place to the schema's declared type.

    Returns ``{arg: "<from> -> <to>"}`` for every argument that was converted.
    """
    changed: Dict[str, str] = {}
    if not isinstance(schema, dict) or not isinstance(args, dict):
        return changed
    props = schema.get("properties")
    if not isinstance(props, dict):
        return changed
    for key, value in list(args.items()):
        prop = props.get(key)
        if not isinstance(prop, dict):
            continue
        new = _coerce_value(value, prop)
        if new is not value:
            args[key] = new
            changed[key] = f"str -> {type(new).__name__}"
    return changed
