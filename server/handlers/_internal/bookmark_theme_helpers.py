"""
INTERNAL HELPER — Not a registered MCP tool.
Provides helper functions consumed by active handlers.

Provides handle_analyze_bookmarks for bookmark_operations_handler.py (.analyze op),
handle_theme_compliance for theme_operations_handler.py (.analyze_compliance op),
and _find_report_folder used by report_operations_handler.py (.backup and .restore ops).
"""

from typing import Dict, Any
import logging
from pathlib import Path
from core.utilities.pbip_utils import normalize_path as _normalize_path
from core.validation.path_guard import guard_or_error
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)


class AmbiguousReportError(FileNotFoundError):
    """A folder holding several .Report folders was given (H-33)."""

    def __init__(self, pbip_path: str, candidates: list) -> None:
        self.candidates = list(candidates)
        super().__init__(
            f"{pbip_path} contains {len(candidates)} .Report folders "
            f"({', '.join(Path(c).name for c in candidates)}); pass the .pbip file or the "
            ".Report folder of the report to use."
        )


def _find_report_folder(pbip_path: str) -> str:
    """Find the .Report folder from a PBIP path.

    Note: This returns the .Report folder itself, not the definition/ subfolder.
    Differs from pbip_utils.find_definition_folder which returns .Report/definition/.
    Bookmark and theme analyzers need the .Report root, not the definition subfolder.

    H-33: a folder with several ``.Report`` folders is ambiguous
    (``AmbiguousReportError``, a FileNotFoundError subclass) instead of
    silently resolving to the first one in ``iterdir()`` order; a ``.pbip``
    follows its ``artifacts[].report.path`` pointer.
    """
    path_obj = Path(_normalize_path(str(pbip_path)))

    if path_obj.name.endswith(".Report") and path_obj.is_dir():
        return str(path_obj)
    if path_obj.name == "definition" and path_obj.parent.name.endswith(".Report"):
        return str(path_obj.parent)

    if path_obj.suffix == ".pbip":
        report_folder = path_obj.parent / f"{path_obj.stem}.Report"
        if report_folder.is_dir():
            return str(report_folder)
        try:
            from core.pbip.pbir_model import resolve_pbip

            res = resolve_pbip(path_obj)
            if res.get("target") == "report" and len(res.get("reports") or []) == 1:
                return res["reports"][0]["report_dir"]
        except Exception:  # pragma: no cover - defensive
            pass

    if path_obj.is_dir():
        reports = sorted(
            str(item)
            for item in path_obj.iterdir()
            if item.is_dir() and item.name.endswith(".Report")
        )
        if len(reports) == 1:
            return reports[0]
        if len(reports) > 1:
            raise AmbiguousReportError(str(pbip_path), reports)

    raise FileNotFoundError(f"No .Report folder found in: {pbip_path}")


def handle_analyze_bookmarks(args: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze bookmarks in a PBIP report and generate HTML analysis."""
    pbip_path = args.get("pbip_path")
    auto_open = args.get("auto_open", True)
    output_path = args.get("output_path")

    if not pbip_path:
        return error_envelope(
            "missing_argument",
            "pbip_path parameter is required - path to PBIP project or .Report folder",
        )

    # Guard output_path if provided (HTML writes here).
    if output_path:
        safe_out, err = guard_or_error(
            output_path,
            "output_path",
            extra_roots=[pbip_path],
            create_parents=True,
        )
        if err:
            return err
        output_path = str(safe_out)

    try:
        # Normalize path
        normalized_path = _normalize_path(pbip_path)

        # Find report folder
        report_folder = _find_report_folder(normalized_path)

        # Import analyzer and generator
        from core.pbip.pbip_bookmark_analyzer import PbipBookmarkAnalyzer
        from core.pbip.pbip_bookmark_html_generator import generate_bookmark_analysis_html

        # Run analysis
        analyzer = PbipBookmarkAnalyzer()
        analysis_data = analyzer.analyze_bookmarks(report_folder)

        # Get model name from path
        model_name = Path(report_folder).stem.replace(".Report", "")

        # Generate HTML report
        html_path = generate_bookmark_analysis_html(
            analysis_data=analysis_data,
            model_name=model_name,
            auto_open=auto_open,
            output_path=output_path,
        )

        if html_path:
            summary = analysis_data.get("summary", {})
            return {
                "success": True,
                "html_report": html_path,
                "message": f"Bookmark analysis HTML generated: {html_path}",
                "summary": {
                    "total_bookmarks": summary.get("total_bookmarks", 0),
                    "categories": summary.get("by_category", {}),
                    "orphaned_count": summary.get("orphaned_count", 0),
                    "avg_complexity": summary.get("avg_complexity", 0),
                    "issue_count": summary.get("issue_count", 0),
                },
            }
        else:
            return error_envelope(
                "operation_failed",
                "Failed to generate HTML report",
            )

    except FileNotFoundError as e:
        return error_envelope(
            "operation_failed",
            str(e),
        )
    except Exception as e:
        logger.error(f"Error analyzing bookmarks: {e}", exc_info=True)
        return error_envelope(
            "operation_failed",
            f"Error analyzing bookmarks: {str(e)}",
        )


def handle_theme_compliance(args: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze theme compliance in a PBIP report and generate HTML analysis."""
    pbip_path = args.get("pbip_path")
    theme_path = args.get("theme_path")
    auto_open = args.get("auto_open", True)
    output_path = args.get("output_path")

    if not pbip_path:
        return error_envelope(
            "missing_argument",
            "pbip_path parameter is required - path to PBIP project or .Report folder",
        )

    # Guard output_path and theme_path if provided.
    if output_path:
        safe_out, err = guard_or_error(
            output_path,
            "output_path",
            extra_roots=[pbip_path],
            create_parents=True,
        )
        if err:
            return err
        output_path = str(safe_out)
    if theme_path:
        safe_theme, err = guard_or_error(
            theme_path,
            "theme_path",
            must_exist=True,
            extra_roots=[pbip_path],
        )
        if err:
            return err
        theme_path = str(safe_theme)

    try:
        # Normalize path
        normalized_path = _normalize_path(pbip_path)

        # Find report folder
        report_folder = _find_report_folder(normalized_path)

        # Import analyzer and generator
        from core.pbip.pbip_theme_compliance_analyzer import PbipThemeComplianceAnalyzer
        from core.pbip.pbip_theme_compliance_html_generator import generate_theme_compliance_html

        # Normalize theme path if provided
        if theme_path:
            theme_path = _normalize_path(theme_path)

        # Run analysis
        analyzer = PbipThemeComplianceAnalyzer()
        analysis_data = analyzer.analyze_theme_compliance(
            report_folder=report_folder, theme_path=theme_path
        )

        # Get model name from path
        model_name = Path(report_folder).stem.replace(".Report", "")

        # Generate HTML report
        html_path = generate_theme_compliance_html(
            analysis_data=analysis_data,
            model_name=model_name,
            auto_open=auto_open,
            output_path=output_path,
        )

        if html_path:
            summary = analysis_data.get("summary", {})
            return {
                "success": True,
                "html_report": html_path,
                "message": f"Theme compliance HTML generated: {html_path}",
                "summary": {
                    "compliance_score": summary.get("compliance_score", 0),
                    "theme_name": summary.get("theme_name", "Unknown"),
                    "theme_source": summary.get("theme_source", "Unknown"),
                    "total_pages": summary.get("total_pages", 0),
                    "total_visuals": summary.get("total_visuals", 0),
                    "compliant_visuals": summary.get("compliant_visuals", 0),
                    "unique_colors": summary.get("unique_colors", 0),
                    "unique_fonts": summary.get("unique_fonts", 0),
                    "total_violations": summary.get("total_violations", 0),
                    "warning_count": summary.get("warning_count", 0),
                },
            }
        else:
            return error_envelope(
                "operation_failed",
                "Failed to generate HTML report",
            )

    except FileNotFoundError as e:
        return error_envelope(
            "operation_failed",
            str(e),
        )
    except Exception as e:
        logger.error(f"Error analyzing theme compliance: {e}", exc_info=True)
        return error_envelope(
            "operation_failed",
            f"Error analyzing theme compliance: {str(e)}",
        )
