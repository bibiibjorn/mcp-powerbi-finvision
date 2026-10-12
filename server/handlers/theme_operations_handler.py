"""
Theme Operations Handler
Theme manipulation and conditional formatting operations.

Operations:
- analyze_compliance: Theme compliance analysis with HTML output (existing)
- get_theme: Load and return current theme
- set_colors: Set theme colors
- set_formatting: Set theme formatting defaults
- push_visual: Push visual formatting to theme
- list_text_classes: List text class definitions
- set_font: Set font properties
- list_cf: List conditional formatting rules
- add_cf: Add conditional formatting rule
- remove_cf: Remove conditional formatting rule
- copy_cf: Copy CF rules between visuals
"""

from typing import Dict, Any
import logging
from pathlib import Path

from server.registry import ToolDefinition
from core.utilities.pbip_utils import find_definition_folder
from server.handlers._authoring_utils import gate_and_wrap
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)

# P0-51 fix: classify theme ops for gate_and_wrap.
# Read-only: get_theme, list_text_classes, list_cf, analyze_compliance.
# Catastrophic: set_colors (cross-cutting, affects every visual in report).
_READ_ONLY_THEME_OPS = {
    "get_theme",
    "list_text_classes",
    "list_cf",
    "analyze_compliance",
    "export_theme",
}
# T1-H: apply_theme overwrites the whole theme; import_theme adds a new theme
# file + updates report.json; clear_visual_overrides touches every visual on
# the report. All three deserve CATASTROPHIC alongside set_colors.
_CATASTROPHIC_THEME_OPS = {"set_colors", "apply_theme", "import_theme", "clear_visual_overrides"}


def _resolve_report_path(pbip_path: str) -> Path:
    """Resolve a pbip_path string to the .Report folder Path.

    Theme engine functions expect report_path (the .Report folder), not definition_path.
    Raises FileNotFoundError if not found.
    """
    from server.handlers._internal.bookmark_theme_helpers import _find_report_folder

    return Path(_find_report_folder(pbip_path))


def _dispatch(args: Dict[str, Any], _unused) -> Dict[str, Any]:
    # _OPS is built below once the handler functions are defined.
    operation = args.get("operation", "get_theme")
    handler = _OPS.get(operation)
    if not handler:
        return error_envelope(
            "invalid_operation",
            f"Unknown operation: '{operation}'. Valid: {', '.join(sorted(_OPS.keys()))}",
        )
    return handler(args)


def handle_theme_operations(args: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch theme and conditional formatting operations.

    P0-51 fix: previously had NO gating on write ops. All writes now go
    through gate_and_wrap so theme / CF mutations require autonomous mode.
    """
    operation = args.get("operation", "get_theme")
    pbip_path = args.get("pbip_path")

    if not pbip_path:
        # Error before disk touch — no reload hint (P1-135).
        return error_envelope("missing_argument", "pbip_path is required")

    return gate_and_wrap(
        args,
        operation,
        tool_name="07_Theme_Operations",
        dispatch_fn=_dispatch,
        pbip_path_or_defn=pbip_path,
        read_only_ops=_READ_ONLY_THEME_OPS,
        catastrophic_ops=_CATASTROPHIC_THEME_OPS,
    )


# --- Delegated operation (reuse existing logic) ---


def _op_analyze_compliance(args: Dict[str, Any]) -> Dict[str, Any]:
    """Theme compliance analysis — delegates to existing bookmark_theme_handler."""
    from server.handlers._internal.bookmark_theme_helpers import handle_theme_compliance

    return handle_theme_compliance(args)


# --- Theme operations (dispatch to theme_engine) ---


def _op_get_theme(args: Dict[str, Any]) -> Dict[str, Any]:
    """Load and return current theme."""
    from core.pbip.theme_engine import get_theme

    try:
        report_path = _resolve_report_path(args["pbip_path"])
    except FileNotFoundError as e:
        return error_envelope("operation_failed", str(e))

    return get_theme(report_path=report_path)


def _op_set_colors(args: Dict[str, Any]) -> Dict[str, Any]:
    """Set theme colors."""
    from core.pbip.theme_engine import set_colors

    colors = args.get("colors")
    if not colors:
        return error_envelope(
            "missing_argument",
            "colors is required (object with dataColors, background, foreground, etc.)",
        )

    try:
        report_path = _resolve_report_path(args["pbip_path"])
    except FileNotFoundError as e:
        return error_envelope("operation_failed", str(e))

    return set_colors(
        report_path=report_path,
        colors=colors,
    )


def _op_set_formatting(args: Dict[str, Any]) -> Dict[str, Any]:
    """Set theme formatting defaults for a visual type."""
    from core.pbip.theme_engine import set_formatting

    formatting = args.get("formatting")
    if not formatting:
        return error_envelope("missing_argument", "formatting is required")

    visual_type = args.get("visual_type_target")
    if not visual_type:
        return error_envelope(
            "missing_argument",
            "visual_type_target is required for set_formatting",
        )

    try:
        report_path = _resolve_report_path(args["pbip_path"])
    except FileNotFoundError as e:
        return error_envelope("operation_failed", str(e))

    return set_formatting(
        report_path=report_path,
        visual_type=visual_type,
        formatting=formatting,
    )


def _op_push_visual(args: Dict[str, Any]) -> Dict[str, Any]:
    """Push visual formatting to the theme."""
    from core.pbip.theme_engine import push_visual_to_theme

    page_name = args.get("page_name")
    visual_name = args.get("visual_name")
    if not page_name or not visual_name:
        return error_envelope(
            "missing_argument",
            "page_name and visual_name are required for push_visual",
        )

    try:
        report_path = _resolve_report_path(args["pbip_path"])
    except FileNotFoundError as e:
        return error_envelope("operation_failed", str(e))

    definition_path = find_definition_folder(args["pbip_path"])
    if not definition_path:
        return error_envelope(
            "not_found",
            f"Could not find definition folder in: {args['pbip_path']}",
        )

    return push_visual_to_theme(
        report_path=report_path,
        definition_path=definition_path,
        page_name=page_name,
        visual_name=visual_name,
    )


def _op_list_text_classes(args: Dict[str, Any]) -> Dict[str, Any]:
    """List text class definitions in the theme."""
    from core.pbip.theme_engine import list_text_classes

    try:
        report_path = _resolve_report_path(args["pbip_path"])
    except FileNotFoundError as e:
        return error_envelope("operation_failed", str(e))

    return list_text_classes(report_path=report_path)


def _op_set_font(args: Dict[str, Any]) -> Dict[str, Any]:
    """Set font properties in the theme or text class."""
    from core.pbip.theme_engine import set_font

    try:
        report_path = _resolve_report_path(args["pbip_path"])
    except FileNotFoundError as e:
        return error_envelope("operation_failed", str(e))

    return set_font(
        report_path=report_path,
        text_class=args.get("text_class"),
        font_family=args.get("font_family"),
        font_size=args.get("font_size"),
        color=args.get("color"),
        bold=args.get("bold"),
    )


# --- Conditional formatting operations (dispatch to conditional_formatting_engine) ---


def _op_list_cf(args: Dict[str, Any]) -> Dict[str, Any]:
    """List conditional formatting rules."""
    from core.pbip.conditional_formatting_engine import list_rules

    definition_path = find_definition_folder(args["pbip_path"])
    if not definition_path:
        return error_envelope(
            "not_found",
            f"Could not find definition folder in: {args['pbip_path']}",
        )

    return list_rules(
        definition_path=definition_path,
        page_name=args.get("page_name"),
        visual_name=args.get("visual_name"),
    )


def _op_add_cf(args: Dict[str, Any]) -> Dict[str, Any]:
    """Add a conditional formatting rule."""
    from core.pbip.conditional_formatting_engine import add_rule

    page_name = args.get("page_name")
    visual_name = args.get("visual_name")
    if not page_name or not visual_name:
        return error_envelope(
            "missing_argument",
            "page_name and visual_name are required for add_cf",
        )

    container = args.get("container")
    property_name = args.get("property_name")
    rule_type = args.get("rule_type")
    if not container or not property_name or not rule_type:
        return error_envelope(
            "missing_argument",
            "container, property_name, and rule_type are required for add_cf",
        )

    definition_path = find_definition_folder(args["pbip_path"])
    if not definition_path:
        return error_envelope(
            "not_found",
            f"Could not find definition folder in: {args['pbip_path']}",
        )

    return add_rule(
        definition_path=definition_path,
        page_name=page_name,
        visual_name=visual_name,
        container=container,
        property_name=property_name,
        rule_type=rule_type,
        config=args.get("cf_config", {}),
        selector=args.get("selector"),
        selector_metadata=args.get("selector_metadata"),
    )


def _op_remove_cf(args: Dict[str, Any]) -> Dict[str, Any]:
    """Remove a conditional formatting rule."""
    from core.pbip.conditional_formatting_engine import remove_rule

    page_name = args.get("page_name")
    visual_name = args.get("visual_name")
    if not page_name or not visual_name:
        return error_envelope(
            "missing_argument",
            "page_name and visual_name are required for remove_cf",
        )

    container = args.get("container")
    property_name = args.get("property_name")
    if not container or not property_name:
        return error_envelope(
            "missing_argument",
            "container and property_name are required for remove_cf",
        )

    definition_path = find_definition_folder(args["pbip_path"])
    if not definition_path:
        return error_envelope(
            "not_found",
            f"Could not find definition folder in: {args['pbip_path']}",
        )

    return remove_rule(
        definition_path=definition_path,
        page_name=page_name,
        visual_name=visual_name,
        container=container,
        property_name=property_name,
        selector_metadata=args.get("selector_metadata"),
    )


def _op_copy_cf(args: Dict[str, Any]) -> Dict[str, Any]:
    """Copy conditional formatting rules between visuals."""
    from core.pbip.conditional_formatting_engine import copy_rule

    source_page = args.get("source_page")
    source_visual = args.get("source_visual")
    target_page = args.get("target_page")
    target_visual = args.get("target_visual")

    if not source_page or not source_visual:
        return error_envelope(
            "missing_argument",
            "source_page and source_visual are required for copy_cf",
        )
    if not target_page or not target_visual:
        return error_envelope(
            "missing_argument",
            "target_page and target_visual are required for copy_cf",
        )

    container = args.get("container")
    property_name = args.get("property_name")
    if not container or not property_name:
        return error_envelope(
            "missing_argument",
            "container and property_name are required for copy_cf",
        )

    definition_path = find_definition_folder(args["pbip_path"])
    if not definition_path:
        return error_envelope(
            "not_found",
            f"Could not find definition folder in: {args['pbip_path']}",
        )

    return copy_rule(
        definition_path=definition_path,
        source_page=source_page,
        source_visual=source_visual,
        target_page=target_page,
        target_visual=target_visual,
        container=container,
        property_name=property_name,
        selector_metadata=args.get("selector_metadata"),
    )


# --- Op registry (built after all functions are defined) ---


def _op_export_theme(args: Dict[str, Any]) -> Dict[str, Any]:
    """T1-H: export current theme JSON to disk."""
    from core.pbip.theme_engine import export_theme

    output_path = args.get("output_path")
    if not output_path:
        return error_envelope("missing_argument", "output_path is required for export_theme")
    from core.validation.path_guard import guard_or_error

    safe_out, err = guard_or_error(
        output_path, "output_path", extra_roots=[args["pbip_path"]], create_parents=True
    )
    if err:
        return err
    try:
        report_path = _resolve_report_path(args["pbip_path"])
    except FileNotFoundError as e:
        return error_envelope("operation_failed", str(e))
    return export_theme(report_path=report_path, output_path=Path(str(safe_out)))


def _op_apply_theme(args: Dict[str, Any]) -> Dict[str, Any]:
    """T1-H: overwrite the current theme JSON in place."""
    from core.pbip.theme_engine import apply_theme

    theme = args.get("theme")
    if not isinstance(theme, dict):
        return error_envelope("missing_argument", "theme (dict) is required for apply_theme")
    try:
        report_path = _resolve_report_path(args["pbip_path"])
    except FileNotFoundError as e:
        return error_envelope("operation_failed", str(e))
    return apply_theme(
        report_path=report_path,
        theme=theme,
        theme_name=args.get("theme_name"),
    )


def _op_import_theme(args: Dict[str, Any]) -> Dict[str, Any]:
    """T1-H: register a theme JSON file under RegisteredResources and point
    report.json at it.
    """
    from core.pbip.theme_engine import import_theme

    source = args.get("source") or args.get("theme_path")
    if not source:
        return error_envelope(
            "missing_argument",
            "source (theme file path) is required for import_theme",
        )
    try:
        report_path = _resolve_report_path(args["pbip_path"])
    except FileNotFoundError as e:
        return error_envelope("operation_failed", str(e))
    return import_theme(
        report_path=report_path,
        source=Path(source),
        theme_name=args.get("theme_name"),
    )


def _op_clear_visual_overrides(args: Dict[str, Any]) -> Dict[str, Any]:
    """T1-H: remove per-visual visual.objects overrides so the theme applies."""
    from core.pbip.theme_engine import clear_visual_overrides

    try:
        report_path = _resolve_report_path(args["pbip_path"])
    except FileNotFoundError as e:
        return error_envelope("operation_failed", str(e))
    return clear_visual_overrides(
        report_path=report_path,
        scope=args.get("scope", "all"),
        keep_cf=bool(args.get("keep_cf", True)),
    )


_OPS = {
    "analyze_compliance": _op_analyze_compliance,
    "get_theme": _op_get_theme,
    "set_colors": _op_set_colors,
    "set_formatting": _op_set_formatting,
    "push_visual": _op_push_visual,
    "list_text_classes": _op_list_text_classes,
    "set_font": _op_set_font,
    "list_cf": _op_list_cf,
    "add_cf": _op_add_cf,
    "remove_cf": _op_remove_cf,
    "copy_cf": _op_copy_cf,
    # T1-H
    "export_theme": _op_export_theme,
    "apply_theme": _op_apply_theme,
    "import_theme": _op_import_theme,
    "clear_visual_overrides": _op_clear_visual_overrides,
}


# --- Registration ---


def register_theme_operations_handler(registry):
    """Register the theme operations tool."""
    from server.tool_schemas import TOOL_SCHEMAS

    registry.register(
        ToolDefinition(
            name="07_Theme_Operations",
            description=(
                "Report THEME + CONDITIONAL FORMATTING on PBIP files: get_theme, set_colors, "
                "set_font, set_formatting, push_visual, list_text_classes, export_theme, "
                "apply_theme, import_theme, analyze_compliance (HTML), "
                "clear_visual_overrides; CF: list_cf, add_cf, remove_cf, copy_cf (need "
                "container + the exact property_name). dry_run=true previews. One visual's "
                "style: 07_Visual_Operations update_formatting. Reload via 17_Desktop_Bridge."
            ),
            guide_notes=(
                "Report THEME (theme.json) and CONDITIONAL FORMATTING — colors, "
                "fonts, text classes, per-visual-type formatting defaults, data "
                "color scales / rule-based CF. analyze_compliance = HTML audit "
                "of visuals overriding the theme; clear_visual_overrides keeps "
                "content (slicer selections, text, Deneb) and CF. dry_run=true "
                "previews any write. Most-missed arg: CF ops need `container` "
                "plus the exact `property_name` the visual type exposes. Theme "
                "changes are report-wide defaults; to restyle ONE visual use "
                "07_Visual_Operations:update_formatting instead. Writes PBIP "
                "files on disk — reload via 17_Desktop_Bridge."
            ),
            handler=handle_theme_operations,
            input_schema=TOOL_SCHEMAS.get("theme_operations", {}),
            category="pbip",
            sort_order=77,
            annotations={
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
                "openWorldHint": True,
            },
        )
    )

    logger.info("Registered theme_operations handler")
