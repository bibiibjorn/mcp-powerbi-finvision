"""
PBIR formatting-property helpers shared by the authoring engines (WP9).

* ``property_value`` -- the ONE encoder for a formatting property written from
  an MCP arg (H-15): strings are always quoted + ``'`` doubled unless they are
  already a PBIR literal (``'x'``, ``12D``, ``3L``, ``true``); floats never use
  exponent notation; hex colours become ``{"solid": {"color": ...}}``.
* ``is_cf_value`` -- conditional-formatting detector matching the real shapes
  (``solid.color.expr.FillRule`` / ``Conditional``, measure-driven colours),
  used by ``clear_visual_overrides`` (H-02) and the CF engine (H-20).
* ``PROTECTED_*`` -- visual.objects content that is NOT an override and must
  never be wiped by a formatting reset or a formatting sync: slicer
  selections (``general.filter``), textbox text (``general.paragraphs``),
  Deneb specs (``vega``), slicer mode / selection, images, button text,
  navigator targets (H-02).
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from core.pbip.pbir_model import decode_literal, encode_literal

#: visual.objects cards that hold CONTENT / behaviour, never theme-able formatting.
PROTECTED_OBJECT_CARDS = frozenset(
    {
        "general",  # slicer selection (filter), textbox paragraphs, selfFilterEnabled
        "vega",  # Deneb spec + provider
        "data",  # slicer mode, isInvertedSelectionMode
        "selection",  # single / multi select
        "script",  # python / R source
        "pages",  # page navigator per-page state
        "bookmarks",  # bookmark navigator group / selection
        "image",  # image url / source
        "imageScaling",
        "paragraphs",
        "filter",
        "dataFunction",
    }
)

#: Property names that carry content wherever they appear.
PROTECTED_PROPERTIES = frozenset(
    {
        "filter",
        "paragraphs",
        "jsonSpec",
        "provider",
        "source",
        "text",  # actionButton text, card reference label text
        "url",
        "imageUrl",
        "sourceType",
        "bookmark",
        "bookmarkGroup",
        "selectedBookmark",
        "navigationSection",
        "drillthroughSection",
        "shapeType",
        "tileShape",
        "mode",
        "selfFilterEnabled",
        "isInvertedSelectionMode",
        "singleSelect",
        "strictSingleSelect",
        "selectAllCheckboxEnabled",
        "webUrl",
    }
)

_NUMERIC_LITERAL = re.compile(r"^-?(?:\d+(?:\.\d*)?|\.\d+)[DLM]$")
_HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def literal(value_text: str) -> Dict[str, Any]:
    return {"expr": {"Literal": {"Value": value_text}}}


def is_pbir_literal_text(text: str) -> bool:
    """True when *text* already IS a PBIR literal value (quoted / typed number / bool)."""
    s = text.strip()
    if len(s) >= 2 and s.startswith("'") and s.endswith("'"):
        return True
    if s in ("true", "false", "null"):
        return True
    return bool(_NUMERIC_LITERAL.match(s))


def property_value(prop_name: str, value: Any, value_type: str = "auto") -> Any:
    """Encode an MCP arg as a PBIR formatting-property value (H-15).

    value_type: ``auto`` | ``literal`` (write the text as-is) | ``boolean`` |
    ``number`` | ``string`` | ``color``. Dict / list values pass through
    unchanged (already PBIR-shaped).
    """
    if isinstance(value, (dict, list)):
        return value
    vt = (value_type or "auto").lower()
    if vt == "literal":
        return literal(str(value))
    if vt == "boolean":
        if isinstance(value, str):
            return literal(encode_literal(value, "bool"))
        return literal("true" if value else "false")
    if vt == "number":
        return literal(encode_literal(value, "double"))
    if vt == "string":
        return literal(encode_literal(str(value), "string"))
    if vt == "color":
        text = str(value)
        return {
            "solid": {
                "color": literal(
                    text if is_pbir_literal_text(text) else encode_literal(text, "string")
                )
            }
        }
    # auto
    if isinstance(value, bool):
        return literal("true" if value else "false")
    if isinstance(value, (int, float)):
        return literal(encode_literal(value, "double"))
    if value is None:
        return literal("null")
    text = str(value)
    if _HEX_COLOR.match(text.strip()):
        return {"solid": {"color": literal(encode_literal(text.strip(), "string"))}}
    if text.strip().lower() in ("true", "false"):
        return literal(text.strip().lower())
    if is_pbir_literal_text(text):
        return literal(text.strip())
    return literal(encode_literal(text, "string"))


def is_cf_value(prop_value: Any) -> bool:
    """True when a property value is conditional / data-driven formatting.

    Real shapes (R0105/R0201, 1,540 CF props): ``{"solid": {"color": {"expr":
    {"FillRule" | "Conditional" | "Measure" | "Aggregation" ...}}}}`` or the
    same ``expr`` directly on the property. The old invented ``rules`` key is
    still recognised so previously-written garbage can be found and removed.
    """
    if not isinstance(prop_value, dict):
        return False
    if "rules" in prop_value:
        return True
    expr = prop_value.get("expr")
    if not isinstance(expr, dict):
        solid = prop_value.get("solid")
        color = solid.get("color") if isinstance(solid, dict) else None
        expr = color.get("expr") if isinstance(color, dict) else None
    if not isinstance(expr, dict):
        return False
    return any(k in expr for k in ("FillRule", "Conditional", "Measure", "Aggregation", "Column"))


def is_literal_value(prop_value: Any) -> bool:
    """Plain override: ``{expr: {Literal}}`` / ``{solid: {color: {expr: Literal|ThemeDataColor}}}``."""
    if not isinstance(prop_value, dict):
        return False
    expr = prop_value.get("expr")
    if isinstance(expr, dict):
        return "Literal" in expr or "ThemeDataColor" in expr
    solid = prop_value.get("solid")
    color = solid.get("color") if isinstance(solid, dict) else None
    cexpr = color.get("expr") if isinstance(color, dict) else None
    return isinstance(cexpr, dict) and ("Literal" in cexpr or "ThemeDataColor" in cexpr)


def unwrap_for_theme(prop_value: Any) -> Optional[Any]:
    """PBIR property value -> theme.json plain value, or None when not expressible (H-21).

    ``{'expr': {'Literal': {'Value': '14D'}}}`` -> ``14``; ``"'Segoe UI'"`` ->
    ``"Segoe UI"``; ``{'solid': {'color': Literal '#112233'}}`` ->
    ``{'solid': {'color': '#112233'}}``. Measure-driven / CF / ThemeDataColor
    expressions return None (skipped, reported by the caller).
    """
    if not isinstance(prop_value, dict):
        return None
    expr = prop_value.get("expr")
    if isinstance(expr, dict):
        lit = expr.get("Literal")
        if isinstance(lit, dict) and "Value" in lit:
            value, kind = decode_literal(lit["Value"])
            if kind in ("double", "int64", "decimal"):
                num = float(value)
                return int(num) if num.is_integer() else num
            if kind in ("string", "bool"):
                return value
            return None
        return None
    solid = prop_value.get("solid")
    color = solid.get("color") if isinstance(solid, dict) else None
    cexpr = color.get("expr") if isinstance(color, dict) else None
    lit = cexpr.get("Literal") if isinstance(cexpr, dict) else None
    if isinstance(lit, dict) and "Value" in lit:
        value, kind = decode_literal(lit["Value"])
        if kind == "string":
            return {"solid": {"color": value}}
    return None
