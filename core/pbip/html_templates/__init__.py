"""
HTML Template Modules for PBIP Analysis Dashboard.

Extracted from pbip_html_generator.py for maintainability.
Each module handles a specific section of the generated HTML report.
"""

from .head import get_head_section
from .styles import get_styles
from .body_template import get_body_content
from .vue_app import get_vue_app_script

__all__ = [
    "get_head_section",
    "get_styles",
    "get_body_content",
    "get_vue_app_script",
]
