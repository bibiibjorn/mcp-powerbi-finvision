"""
PBIP HTML Generator (Vue 3 Version) - Generates interactive HTML dashboard for PBIP analysis.

This module creates a comprehensive, interactive HTML dashboard with Vue 3,
D3.js visualizations, searchable tables, and dependency graphs.

Template sections are split into submodules under core.pbip.html_templates/
for maintainability. This file is the orchestrator that assembles them.
"""

import html
import json
import logging
import os
from datetime import datetime
from typing import Dict, Optional, Any

from core.pbip.html_templates import (
    get_body_content,
    get_head_section,
    get_styles,
    get_vue_app_script,
)

logger = logging.getLogger(__name__)


class PbipHtmlGenerator:
    """Generates interactive HTML dashboard for PBIP analysis using Vue 3."""

    def __init__(self):
        """Initialize the HTML generator."""
        self.logger = logger

    def generate_full_report(
        self,
        model_data: Dict[str, Any],
        report_data: Optional[Dict[str, Any]],
        dependencies: Dict[str, Any],
        output_path: str,
        repository_name: str = "PBIP Repository",
        enhanced_results: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Generate comprehensive HTML report.

        Args:
            model_data: Parsed model data
            report_data: Optional parsed report data
            dependencies: Dependency analysis results
            output_path: Output directory path
            repository_name: Name of the repository
            enhanced_results: Optional enhanced analysis results (BPA, perspectives, etc.)

        Returns:
            Path to generated HTML file

        Raises:
            IOError: If unable to write output file
        """
        # Convert to absolute path for MCP compatibility
        abs_output_path = os.path.abspath(output_path)
        self.logger.info(f"Generating Vue 3 HTML report to {abs_output_path}")

        # Create output directory
        os.makedirs(abs_output_path, exist_ok=True)

        # Generate HTML content
        html_content = self._build_html_document(
            model_data, report_data, dependencies, repository_name, enhanced_results
        )

        # Generate filename from repository name and timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Clean repository name for filename
        safe_name = "".join(
            c if c.isalnum() or c in (" ", "-", "_") else "_" for c in repository_name
        )
        safe_name = safe_name.replace(" ", "_").strip("_")

        # Create meaningful filename
        filename = f"{safe_name}_PBIP_Analysis_{timestamp}.html"
        html_file = os.path.join(abs_output_path, filename)

        try:
            with open(html_file, "w", encoding="utf-8") as f:
                f.write(html_content)

            self.logger.info(f"Vue 3 HTML report generated: {html_file}")
            return html_file

        except Exception as e:
            self.logger.error(f"Failed to write HTML report: {e}")
            raise IOError(f"Failed to write HTML report: {e}")

    def _build_html_document(
        self,
        model_data: Dict,
        report_data: Optional[Dict],
        dependencies: Dict,
        repo_name: str,
        enhanced_results: Optional[Dict] = None,
    ) -> str:
        """Build complete HTML document with Vue 3."""
        # Prepare data for JavaScript
        data_json = {
            "model": model_data,
            "report": report_data,
            "dependencies": dependencies,
            "enhanced": enhanced_results,
            "generated": datetime.now().isoformat(),
            "repository_name": repo_name,
        }

        # Serialize to JSON string
        # G-18: the JSON is embedded in a <script> block -- "</" (e.g. a DAX
        # string "</script>") would close it, and U+2028/U+2029 break old JS parsers.
        data_json_str = (
            json.dumps(data_json, ensure_ascii=False, separators=(",", ":"), default=str)
            .replace("</", "<\\/")
            .replace(" ", "\\u2028")
            .replace(" ", "\\u2029")
        )

        # Assemble HTML from template modules
        escaped_repo_name = html.escape(repo_name)

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
{get_head_section(escaped_repo_name)}
{get_styles()}
</head>
{get_body_content()}
{get_vue_app_script(data_json_str)}
</html>"""
