"""
HTML Template Module: Vue 3 Application Script

Vue 3 app logic including data, computed properties, methods, and mounted hooks.
JS content lives in core/pbip/static/pbip_vue_app.js.
The placeholder __DATA_JSON__ in the static file is replaced with the runtime data.
"""

import pathlib

_HERE = pathlib.Path(__file__).parent
_STATIC = _HERE.parent / "static" / "pbip_vue_app.js"


def get_vue_app_script(data_json_str: str) -> str:
    """Get Vue 3 app script.

    Args:
        data_json_str: JSON-serialized data for the Vue app
    """
    js = _STATIC.read_text(encoding="utf-8")
    js = js.replace("__DATA_JSON__", data_json_str)
    return f"    <script>\n{js}\n    </script>"
