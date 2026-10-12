"""
PBIP Theme Compliance HTML Generator - Generates interactive HTML report for theme compliance.

Creates a professional, interactive HTML dashboard with:
- Compliance score visualization
- Color palette analysis
- Font usage breakdown
- Violation tracking
- Page-by-page compliance details
"""

import html
import os
import logging
import pathlib
import webbrowser
from datetime import datetime
from typing import Dict, Any, Optional

from core.utilities.pbip_dependency_html_generator import fill_template, script_json

logger = logging.getLogger(__name__)

_STATIC = pathlib.Path(__file__).parent / "static"


def generate_theme_compliance_html(
    analysis_data: Dict[str, Any],
    model_name: str = "Power BI Report",
    auto_open: bool = True,
    output_path: Optional[str] = None,
) -> Optional[str]:
    """
    Generate an interactive HTML report for theme compliance analysis.

    Args:
        analysis_data: Output from PbipThemeComplianceAnalyzer.analyze_theme_compliance()
        model_name: Name of the report for display
        auto_open: Whether to open the HTML in browser
        output_path: Optional custom output path

    Returns:
        Path to generated HTML file or None if failed
    """
    if not analysis_data:
        logger.error("No analysis data provided")
        return None

    summary = analysis_data.get("summary", {})
    theme = analysis_data.get("theme", {})
    pages = analysis_data.get("pages", [])
    color_analysis = analysis_data.get("color_analysis", {})
    font_analysis = analysis_data.get("font_analysis", {})
    visual_analysis = analysis_data.get("visual_analysis", {})
    violations = analysis_data.get("violations", [])

    # Prepare data for JavaScript
    js_data = {
        "modelName": model_name,
        "timestamp": datetime.now().isoformat(),
        "summary": summary,
        "theme": theme,
        "themeSource": analysis_data.get("theme_source", "Unknown"),
        "pages": pages,
        "colorAnalysis": color_analysis,
        "fontAnalysis": font_analysis,
        "visualAnalysis": visual_analysis,
        "violations": violations,
        "complianceScore": analysis_data.get("compliance_score", 0),
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
        exports_dir = os.path.join(server_root, "exports", "theme_compliance")
        os.makedirs(exports_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = "".join(c if c.isalnum() or c in (" ", "-", "_") else "_" for c in model_name)
        safe_name = safe_name.replace(" ", "_").strip("_")
        html_file = os.path.join(exports_dir, f"{safe_name}_Theme_Compliance_{timestamp}.html")

    try:
        with open(html_file, "w", encoding="utf-8") as f:
            f.write(html_content)

        logger.info(f"Theme compliance HTML generated: {html_file}")

        if auto_open:
            webbrowser.open(f"file://{os.path.abspath(html_file)}")

        return html_file

    except Exception as e:
        logger.error(f"Failed to write HTML report: {e}")
        return None


def _build_styles() -> str:
    """Load CSS from the static file and wrap in a <style> tag."""
    css = (_STATIC / "theme_compliance.css").read_text(encoding="utf-8")
    return f"<style>\n{css}\n</style>"


def _build_html_document(data: Dict[str, Any], model_name: str) -> str:
    """Build the complete HTML document from the static template."""
    template = (_STATIC / "theme_compliance.html").read_text(encoding="utf-8")
    # One pass + context escaping (C-06), see pbip_bookmark_html_generator.
    return fill_template(
        template,
        {
            "__MODEL_NAME__": html.escape(str(model_name)),
            "__DATA_JSON__": script_json(data),
            "__STYLES__": _build_styles(),
        },
    )
