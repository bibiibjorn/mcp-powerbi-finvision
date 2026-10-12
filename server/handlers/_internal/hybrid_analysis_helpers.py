"""
INTERNAL HELPER — Not a registered MCP tool.
Provides helper functions consumed by active handlers.

Provides PBIP dependency analysis; consumed by pbip_operations_handler.py
(07_PBIP_Operations operation=dependency_html).

2026-09-22 (I-19): ``output_path`` is path-guarded like every other writer
and ``auto_open`` defaults to False (agent use — opening a browser is a side
effect the caller must ask for).
"""

import logging
import traceback
from pathlib import Path
from typing import Any, Dict

from core.utilities.pbip_dependency_html_generator import generate_pbip_dependency_html
from core.validation.path_guard import guard_or_error
from server.pbip_cache import normalize_pbip_path, pbip_cache

logger = logging.getLogger(__name__)


def handle_generate_pbip_dependency_diagram(args: Dict[str, Any]) -> Dict[str, Any]:
    """Generate an interactive HTML dependency diagram for a PBIP project."""
    pbip_folder_path = args.get("pbip_folder_path") or args.get("pbip_path") or ""
    try:
        auto_open = bool(args.get("auto_open", False))
        output_path = args.get("output_path")
        main_item = args.get("main_item")

        path = normalize_pbip_path(pbip_folder_path)
        if output_path:
            safe_out, err = guard_or_error(
                output_path, "output_path", extra_roots=[str(path)], create_parents=True
            )
            if err:
                return err
            output_path = str(safe_out)

        data = pbip_cache.get_or_parse(path)
        report_data = data.report_data
        dependency_data = data.dependencies

        p = Path(path)
        model_name = (
            p.stem.replace(".SemanticModel", "") if p.name.endswith(".SemanticModel") else p.name
        )
        logger.info(f"Generating PBIP dependency diagram for: {model_name}")
        summary = dependency_data.get("summary", {})
        if report_data:
            logger.info(
                f"    Analyzed {summary.get('total_visuals', 0)} visuals "
                f"across {summary.get('total_pages', 0)} pages"
            )

        html_path = generate_pbip_dependency_html(
            dependency_data=dependency_data,
            model_name=model_name,
            auto_open=auto_open,
            output_path=output_path,
            main_item=main_item,
        )

        if not html_path:
            return {
                "success": False,
                "error": "Failed to generate HTML diagram",
                "error_type": "generation_error",
            }
        return {
            "success": True,
            "message": "PBIP dependency diagram generated successfully",
            "diagram_path": html_path,
            "model_name": model_name,
            "initial_selection": main_item if main_item else "First measure in model",
            "auto_opened": auto_open,
            "summary": {
                "visuals": summary.get("total_visuals", 0),
                "pages": summary.get("total_pages", 0),
                "measures": summary.get("total_measures", 0),
                "columns": summary.get("total_columns", 0),
                "field_parameters": len(dependency_data.get("column_to_field_params", {})),
                "unused_measures": summary.get("unused_measures", 0),
                "unused_columns": summary.get("unused_columns", 0),
            },
        }

    except Exception as e:
        logger.error(
            f"Error generating PBIP dependency diagram: {str(e)}\n{traceback.format_exc()}"
        )
        return {
            "success": False,
            "error": f"Diagram generation failed: {str(e)}",
            "error_type": "generation_error",
            "context": {"pbip_folder_path": pbip_folder_path},
        }
