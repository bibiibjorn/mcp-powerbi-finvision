"""
Bookmark Operations Handler
CRUD operations for bookmarks plus analysis.

Operations:
- list: List all bookmarks
- create: Create a new bookmark
- rename: Rename a bookmark
- delete: Delete a bookmark
- set_capture: Configure what bookmark captures
- set_affected_visuals: Set which visuals are affected
- analyze: Generate HTML bookmark analysis (existing functionality)
"""

from typing import Dict, Any
import logging

from server.registry import ToolDefinition
from core.utilities.pbip_utils import find_definition_folder
from server.handlers._authoring_utils import gate_and_wrap
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)

# P0-52 fix: classify bookmark ops for gating.
_READ_ONLY_BOOKMARK_OPS = {"list", "analyze", "get"}
# T1-G: delete_group with delete_children=True can affect many files.
_CATASTROPHIC_BOOKMARK_OPS: set = set()


def _dispatch(args: Dict[str, Any], _unused) -> Dict[str, Any]:
    operation = args.get("operation", "list")
    handler = _OPS.get(operation)
    if not handler:
        return error_envelope(
            "invalid_operation",
            f"Unknown operation: '{operation}'. Valid: {', '.join(sorted(_OPS.keys()))}",
        )
    return handler(args)


def handle_bookmark_operations(args: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch bookmark operations.

    P0-52 fix: previously had NO gating. Bookmark create/rename/delete/
    set_capture/set_affected_visuals now require autonomous mode.
    """
    operation = args.get("operation", "list")
    pbip_path = args.get("pbip_path")

    if not pbip_path:
        return error_envelope("missing_argument", "pbip_path is required")

    return gate_and_wrap(
        args,
        operation,
        tool_name="07_Bookmark_Operations",
        dispatch_fn=_dispatch,
        pbip_path_or_defn=pbip_path,
        read_only_ops=_READ_ONLY_BOOKMARK_OPS,
        catastrophic_ops=_CATASTROPHIC_BOOKMARK_OPS,
    )


# --- Delegated operation (reuse existing logic) ---


def _op_analyze(args: Dict[str, Any]) -> Dict[str, Any]:
    """Generate HTML bookmark analysis — delegates to existing bookmark_theme_handler."""
    from server.handlers._internal.bookmark_theme_helpers import handle_analyze_bookmarks

    return handle_analyze_bookmarks(args)


# --- New operations (dispatch to domain engine) ---


def _op_list(args: Dict[str, Any]) -> Dict[str, Any]:
    """List all bookmarks."""
    from core.pbip.bookmark_engine import list_bookmarks

    definition_path = find_definition_folder(args["pbip_path"])
    if not definition_path:
        return error_envelope(
            "not_found",
            f"Could not find definition folder in: {args['pbip_path']}",
        )

    return list_bookmarks(definition_path=definition_path)


def _op_create(args: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new bookmark."""
    from core.pbip.bookmark_engine import create_bookmark

    display_name = args.get("display_name")
    if not display_name:
        return error_envelope("missing_argument", "display_name is required for create")

    definition_path = find_definition_folder(args["pbip_path"])
    if not definition_path:
        return error_envelope(
            "not_found",
            f"Could not find definition folder in: {args['pbip_path']}",
        )

    return create_bookmark(
        definition_path=definition_path,
        display_name=display_name,
        page_name=args.get("page_name"),
    )


def _op_rename(args: Dict[str, Any]) -> Dict[str, Any]:
    """Rename a bookmark."""
    from core.pbip.bookmark_engine import rename_bookmark

    bookmark_id = args.get("bookmark_id")
    new_name = args.get("new_name")
    if not bookmark_id:
        return error_envelope("missing_argument", "bookmark_id is required for rename")
    if not new_name:
        return error_envelope("missing_argument", "new_name is required for rename")

    definition_path = find_definition_folder(args["pbip_path"])
    if not definition_path:
        return error_envelope(
            "not_found",
            f"Could not find definition folder in: {args['pbip_path']}",
        )

    return rename_bookmark(
        definition_path=definition_path,
        bookmark_id=bookmark_id,
        new_name=new_name,
    )


def _op_delete(args: Dict[str, Any]) -> Dict[str, Any]:
    """Delete a bookmark."""
    from core.pbip.bookmark_engine import delete_bookmark

    bookmark_id = args.get("bookmark_id")
    if not bookmark_id:
        return error_envelope("missing_argument", "bookmark_id is required for delete")

    definition_path = find_definition_folder(args["pbip_path"])
    if not definition_path:
        return error_envelope(
            "not_found",
            f"Could not find definition folder in: {args['pbip_path']}",
        )

    return delete_bookmark(
        definition_path=definition_path,
        bookmark_id=bookmark_id,
    )


def _op_set_capture(args: Dict[str, Any]) -> Dict[str, Any]:
    """Configure what a bookmark captures."""
    from core.pbip.bookmark_engine import set_bookmark_capture

    bookmark_id = args.get("bookmark_id")
    if not bookmark_id:
        return error_envelope("missing_argument", "bookmark_id is required for set_capture")

    definition_path = find_definition_folder(args["pbip_path"])
    if not definition_path:
        return error_envelope(
            "not_found",
            f"Could not find definition folder in: {args['pbip_path']}",
        )

    return set_bookmark_capture(
        definition_path=definition_path,
        bookmark_id=bookmark_id,
        capture_data=args.get("capture_data"),
        capture_display=args.get("capture_display"),
        capture_current_page=args.get("capture_current_page"),
    )


def _op_set_affected_visuals(args: Dict[str, Any]) -> Dict[str, Any]:
    """Set which visuals are affected by a bookmark."""
    from core.pbip.bookmark_engine import set_affected_visuals

    bookmark_id = args.get("bookmark_id")
    if not bookmark_id:
        return error_envelope(
            "missing_argument",
            "bookmark_id is required for set_affected_visuals",
        )

    visual_ids = args.get("visual_ids")
    all_visuals = args.get("all_visuals", False)

    if not visual_ids and not all_visuals:
        return error_envelope("missing_argument", "visual_ids or all_visuals=true is required")

    definition_path = find_definition_folder(args["pbip_path"])
    if not definition_path:
        return error_envelope(
            "not_found",
            f"Could not find definition folder in: {args['pbip_path']}",
        )

    return set_affected_visuals(
        definition_path=definition_path,
        bookmark_id=bookmark_id,
        visual_ids=visual_ids,
        all_visuals=all_visuals,
    )


# --- T1-G: get / update + bookmark groups ---


def _op_get(args: Dict[str, Any]) -> Dict[str, Any]:
    from core.pbip.bookmark_engine import get_bookmark

    bookmark_id = args.get("bookmark_id")
    if not bookmark_id:
        return error_envelope("missing_argument", "bookmark_id is required for get")
    definition_path = find_definition_folder(args["pbip_path"])
    if not definition_path:
        return error_envelope(
            "not_found",
            f"Could not find definition folder in: {args['pbip_path']}",
        )
    return get_bookmark(definition_path=definition_path, bookmark_id=bookmark_id)


def _op_update(args: Dict[str, Any]) -> Dict[str, Any]:
    from core.pbip.bookmark_engine import update_bookmark

    bookmark_id = args.get("bookmark_id")
    if not bookmark_id:
        return error_envelope("missing_argument", "bookmark_id is required for update")
    definition_path = find_definition_folder(args["pbip_path"])
    if not definition_path:
        return error_envelope(
            "not_found",
            f"Could not find definition folder in: {args['pbip_path']}",
        )
    return update_bookmark(
        definition_path=definition_path,
        bookmark_id=bookmark_id,
        display_name=args.get("display_name"),
        capture_data=args.get("capture_data"),
        capture_display=args.get("capture_display"),
        capture_current_page=args.get("capture_current_page"),
    )


def _op_create_group(args: Dict[str, Any]) -> Dict[str, Any]:
    from core.pbip.bookmark_engine import create_bookmark_group

    display_name = args.get("display_name")
    if not display_name:
        return error_envelope("missing_argument", "display_name is required for create_group")
    definition_path = find_definition_folder(args["pbip_path"])
    if not definition_path:
        return error_envelope(
            "not_found",
            f"Could not find definition folder in: {args['pbip_path']}",
        )
    return create_bookmark_group(
        definition_path=definition_path,
        display_name=display_name,
        bookmark_ids=args.get("bookmark_ids") or [],
    )


def _op_update_group(args: Dict[str, Any]) -> Dict[str, Any]:
    from core.pbip.bookmark_engine import update_bookmark_group

    group_id = args.get("group_id")
    if not group_id:
        return error_envelope("missing_argument", "group_id is required for update_group")
    definition_path = find_definition_folder(args["pbip_path"])
    if not definition_path:
        return error_envelope(
            "not_found",
            f"Could not find definition folder in: {args['pbip_path']}",
        )
    return update_bookmark_group(
        definition_path=definition_path,
        group_id=group_id,
        display_name=args.get("display_name"),
        bookmark_ids=args.get("bookmark_ids"),
    )


def _op_delete_group(args: Dict[str, Any]) -> Dict[str, Any]:
    from core.pbip.bookmark_engine import delete_bookmark_group

    group_id = args.get("group_id")
    if not group_id:
        return error_envelope("missing_argument", "group_id is required for delete_group")
    definition_path = find_definition_folder(args["pbip_path"])
    if not definition_path:
        return error_envelope(
            "not_found",
            f"Could not find definition folder in: {args['pbip_path']}",
        )
    return delete_bookmark_group(
        definition_path=definition_path,
        group_id=group_id,
        delete_children=bool(args.get("delete_children", False)),
    )


# --- Op registry (built after all functions are defined) ---

_OPS = {
    "list": _op_list,
    "create": _op_create,
    "rename": _op_rename,
    "delete": _op_delete,
    "set_capture": _op_set_capture,
    "set_affected_visuals": _op_set_affected_visuals,
    "analyze": _op_analyze,
    # T1-G
    "get": _op_get,
    "update": _op_update,
    "create_group": _op_create_group,
    "update_group": _op_update_group,
    "delete_group": _op_delete_group,
}


# --- Registration ---


def register_bookmark_operations_handler(registry):
    """Register the bookmark operations tool."""
    from server.tool_schemas import TOOL_SCHEMAS

    registry.register(
        ToolDefinition(
            name="07_Bookmark_Operations",
            description=(
                "Bookmark CRUD on PBIP files: list, get, create, update, rename, delete, "
                "set_capture, set_affected_visuals, create_group, update_group, delete_group, "
                "analyze (HTML). Scope with visual_ids=[...] or all_visuals=true - an "
                "unscoped bookmark resets every visual on the page. Disk writes: reload via "
                "17_Desktop_Bridge. Bookmark buttons are visuals: 07_Visual_Operations "
                "set_action."
            ),
            guide_notes=(
                "Bookmark CRUD on a PBIP report — create/list/rename/delete "
                "bookmarks and bookmark groups, control what each bookmark "
                "captures (data/display filter snapshot) and which visuals it "
                "affects. Ops: list, get, create, update, rename, delete, "
                "set_capture, set_affected_visuals, create_group, update_group, "
                "delete_group, analyze (interactive HTML report). Most-missed "
                "arg: scope a bookmark with visual_ids=[...] (or "
                "all_visuals=true) — an unscoped bookmark silently resets every "
                "visual on the page. Writes PBIP files on disk, so reload the "
                "running Desktop afterwards via 17_Desktop_Bridge. Bookmark "
                "BUTTONS/navigators are visuals — create those with "
                "07_Visual_Operations:set_action, not here."
            ),
            handler=handle_bookmark_operations,
            input_schema=TOOL_SCHEMAS.get("bookmark_operations", {}),
            category="pbip",
            sort_order=76,
            annotations={
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
                "openWorldHint": True,
            },
        )
    )

    logger.info("Registered bookmark_operations handler")
