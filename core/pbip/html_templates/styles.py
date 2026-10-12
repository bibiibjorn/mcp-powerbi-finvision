"""
HTML Template Module: CSS Styles

Warm Terracotta design system CSS for the PBIP analysis dashboard.
CSS content lives in core/pbip/static/pbip_styles.css.
"""

import pathlib

_HERE = pathlib.Path(__file__).parent
_STATIC = _HERE.parent / "static" / "pbip_styles.css"


def get_styles() -> str:
    """Get all CSS styles for Warm Terracotta design."""
    css = _STATIC.read_text(encoding="utf-8")
    return f"    <style>\n{css}\n    </style>"
