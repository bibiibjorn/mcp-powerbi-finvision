"""
PBIP Bookmark HTML Generator - Generates interactive HTML report for bookmark analysis.

Creates a professional, interactive HTML dashboard with:
- Bookmark inventory with categorization
- Visual state breakdown
- Navigation structure visualization
- Issue detection and recommendations
"""

import html
import os
import logging
import pathlib
import webbrowser
from datetime import datetime
from typing import Dict, Any, Optional

from core.utilities.pbip_dependency_html_generator import fill_template, script_json

_STATIC_DIR = pathlib.Path(__file__).parent / "static"
_TEMPLATE_PATH = _STATIC_DIR / "bookmark_report.html"

logger = logging.getLogger(__name__)


def generate_bookmark_analysis_html(
    analysis_data: Dict[str, Any],
    model_name: str = "Power BI Report",
    auto_open: bool = True,
    output_path: Optional[str] = None,
) -> Optional[str]:
    """
    Generate an interactive HTML report for bookmark analysis.

    Args:
        analysis_data: Output from PbipBookmarkAnalyzer.analyze_bookmarks()
        model_name: Name of the report for display
        auto_open: Whether to open the HTML in browser
        output_path: Optional custom output path

    Returns:
        Path to generated HTML file or None if failed
    """
    if not analysis_data:
        logger.error("No analysis data provided")
        return None

    bookmarks = analysis_data.get("bookmarks", [])
    summary = analysis_data.get("summary", {})
    categories = analysis_data.get("categories", {})
    issues = analysis_data.get("issues", [])
    navigation_structure = analysis_data.get("navigation_structure", {})

    # Prepare data for JavaScript
    js_data = {
        "modelName": model_name,
        "timestamp": datetime.now().isoformat(),
        "summary": summary,
        "bookmarks": bookmarks,
        "categories": categories,
        "issues": issues,
        "navigation": navigation_structure,
    }

    html_content = _build_html_document(js_data, model_name)

    # Determine output path
    if output_path:
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        html_file = output_path
    else:
        # Default to exports folder
        server_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        exports_dir = os.path.join(server_root, "exports", "bookmark_analysis")
        os.makedirs(exports_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = "".join(c if c.isalnum() or c in (" ", "-", "_") else "_" for c in model_name)
        safe_name = safe_name.replace(" ", "_").strip("_")
        html_file = os.path.join(exports_dir, f"{safe_name}_Bookmark_Analysis_{timestamp}.html")

    try:
        with open(html_file, "w", encoding="utf-8") as f:
            f.write(html_content)

        logger.info(f"Bookmark analysis HTML generated: {html_file}")

        if auto_open:
            webbrowser.open(f"file://{os.path.abspath(html_file)}")

        return html_file

    except Exception as e:
        logger.error(f"Failed to write HTML report: {e}")
        return None


def _build_html_document(data: Dict[str, Any], model_name: str) -> str:
    """Build the complete HTML document from the static template."""
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    # One pass + context escaping (C-06): the name goes into <title> as HTML text, the data into a
    # <script> as JSON that cannot close the element; neither value is re-scanned for tokens.
    return fill_template(
        template,
        {
            "__MODEL_NAME__": html.escape(str(model_name)),
            "__DATA_JSON__": script_json(data),
        },
    )
