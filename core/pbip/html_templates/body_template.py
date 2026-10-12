"""
HTML Template Module: Body Content

Vue 3 template HTML for the PBIP analysis dashboard.
HTML content lives in core/pbip/static/pbip_body.html.
"""

import pathlib

_HERE = pathlib.Path(__file__).parent
_STATIC = _HERE.parent / "static" / "pbip_body.html"


def get_body_content() -> str:
    """Get HTML body content with Vue 3 template."""
    return _STATIC.read_text(encoding="utf-8")
