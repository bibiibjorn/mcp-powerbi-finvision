"""Visual Operations Handler — thin dispatch shell (v13 refactor).

All operation implementations have been extracted to focused modules:
  - visual_layout_handler   : list, update_position, replace_measure, sync_*, align
  - visual_config_handler   : update_visual_config, configure_slicer, add/remove_field,
                              set_sort, set_action, inject_code, update_formatting,
                              manage_visual_calcs, *_raw_property
  - visual_creation_handler : create, create_group, delete, list_templates, get_template
"""

import logging
from typing import Any, Dict

from server.registry import ToolDefinition
from core.utilities.pbip_utils import resolve_definition_path
from core.infrastructure.connection_state import connection_state  # noqa: F401 – used in fallback
from server.handlers._authoring_utils import ambiguous_report_error, gate_and_wrap

from server.handlers.visual_layout_handler import (
    _op_list,
    _op_update_position,
    _op_replace_measure,
    _op_sync_visual,
    _op_sync_column_widths,
    _op_align,
    _op_sync_formatting,
    _op_get_roles_for_type,
)
from server.handlers.visual_config_handler import (
    _op_update_visual_config,
    _op_configure_slicer,
    _op_add_field,
    _op_remove_field,
    _op_set_sort,
    _op_set_action,
    _op_inject_code,
    _op_update_formatting,
    _op_manage_visual_calcs,
    _op_set_raw_property,
    _op_get_raw_property,
    _op_delete_raw_property,
    _op_audit_pane_collisions,
    _op_bulk_rename_pane_names,
    _op_clear_default_selection,
    _op_set_default_selection,
)

# P2-180: dual-dispatch invariant — create/delete ops live in one place
# (visual_creation_handler.op_*) and are reachable from two tools:
#   - 07_Visual_Operations: dispatch keys 'create', 'create_group', 'delete'
#   - 11_PBIP_Authoring:    dispatch keys 'create_visual', 'create_visual_group', 'delete_visual'
# Do NOT fork the implementation. Both tools must keep delegating to
# visual_creation_handler so gate annotations, reload hints, and validation
# stay in lockstep. If you rename the underlying op_* function, update both
# sides (see server/handlers/authoring_handler.py:33-36).
from server.handlers.visual_creation_handler import (
    op_create_visual as _op_create_visual,
    op_create_visual_group as _op_create_visual_group,
    op_delete_visual as _op_delete_visual,
    op_hide_visual as _op_hide_visual,
    op_unhide_visual as _op_unhide_visual,
    op_list_templates as _op_list_templates,
    op_get_template as _op_get_template,
    delete_severity as _delete_severity,
)
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)

_OPS = {
    "list": _op_list,
    "create": _op_create_visual,
    "create_group": _op_create_visual_group,
    "delete": _op_delete_visual,
    "hide": _op_hide_visual,
    "unhide": _op_unhide_visual,
    "update_position": _op_update_position,
    "update_visual_config": _op_update_visual_config,
    "update_formatting": _op_update_formatting,
    "align": _op_align,
    "add_field": _op_add_field,
    "remove_field": _op_remove_field,
    "set_sort": _op_set_sort,
    "set_action": _op_set_action,
    "inject_code": _op_inject_code,
    "manage_visual_calcs": _op_manage_visual_calcs,
    "configure_slicer": _op_configure_slicer,
    "list_templates": _op_list_templates,
    "get_template": _op_get_template,
    "replace_measure": _op_replace_measure,
    "sync_visual": _op_sync_visual,
    "sync_column_widths": _op_sync_column_widths,
    "sync_formatting": _op_sync_formatting,
    "set_raw_property": _op_set_raw_property,
    "get_raw_property": _op_get_raw_property,
    "delete_raw_property": _op_delete_raw_property,
    "audit_pane_collisions": _op_audit_pane_collisions,
    "bulk_rename_pane_names": _op_bulk_rename_pane_names,
    "clear_default_selection": _op_clear_default_selection,
    "set_default_selection": _op_set_default_selection,
    "get_roles_for_type": _op_get_roles_for_type,
}

# P0-50 / P0-54 fix: classify ops so gate_and_wrap can apply correct severity
# and skip the reload hint for read-only calls.
_READ_ONLY_VISUAL_OPS = {
    "list",
    "get_raw_property",
    "list_templates",
    "get_template",
    "audit_pane_collisions",
    "get_roles_for_type",
}
# H-25: 'delete' is rated by delete_severity (DESTRUCTIVE for one visual,
# CATASTROPHIC for several / a group with children) — the SAME rating as
# 11_PBIP_Authoring:delete_visual, so neither tool is a gate bypass.
_CATASTROPHIC_VISUAL_OPS = {
    "delete_raw_property",
}


def _severity(args: Dict[str, Any], definition_path) -> Any:
    if args.get("operation") == "delete":
        return _delete_severity(args, definition_path)
    return None


def _dispatch(args: Dict[str, Any], definition_path) -> Dict[str, Any]:
    operation = args.get("operation", "list")
    op_func = _OPS.get(operation)
    if not op_func:
        valid = ", ".join(sorted(_OPS))
        return error_envelope(
            "invalid_operation",
            f"Unknown operation: {operation}. Valid: {valid}",
        )
    return op_func(args, definition_path)


def handle_visual_operations(args: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch all visual operations through gate_and_wrap.

    P0-50 fix: previously had NO gating on destructive ops; same ops in
    11_PBIP_Authoring were gated — a safety-regression asymmetry. Now routes
    through the shared gate_and_wrap helper so gate + reload-hint behavior
    matches the 11_* tool.

    P0-54 fix: read-only ops no longer get a reload_required hint (was
    misleading callers after list/get_raw_property calls).
    """
    operation = args.get("operation", "list")
    pbip_path = args.get("pbip_path")

    if not pbip_path:
        try:
            from core.infrastructure.connection_state import connection_state as _cs

            pbip_path = _cs.get_pbip_folder_path()
        except Exception:
            pbip_path = None

    if not pbip_path:
        # Error before any disk touch — no reload hint. P1-135.
        return error_envelope(
            "missing_argument",
            "pbip_path parameter is required (or connect via 01_Connection to auto-resolve from a PBIP session)",
        )

    # H-33: resolve_definition_path picks the FIRST .Report of a multi-report
    # folder; refuse before resolving (the gate only sees the resolved path).
    amb = ambiguous_report_error(pbip_path)
    if amb is not None:
        return amb

    resolved = resolve_definition_path(pbip_path)
    if "error" in resolved:
        return error_envelope("operation_failed", resolved["error"])
    definition_path = resolved["path"]

    return gate_and_wrap(
        args,
        operation,
        tool_name="07_Visual_Operations",
        dispatch_fn=_dispatch,
        pbip_path_or_defn=definition_path,
        read_only_ops=_READ_ONLY_VISUAL_OPS,
        catastrophic_ops=_CATASTROPHIC_VISUAL_OPS,
        severity_fn=_severity,
    )


def register_visual_operations_handler(registry):
    """Register visual operations handler."""
    from server.tool_schemas import TOOL_SCHEMAS

    schema = TOOL_SCHEMAS.get("visual_operations", {})

    # P2-192: guard against schema/dispatch drift. A new op added to the schema
    # without a matching entry in _OPS would otherwise surface only when a
    # client called it — returning "Unknown operation" instead of failing loud.
    schema_ops = set(
        (schema.get("properties", {}).get("operation", {}) or {}).get("enum", []) or []
    )
    missing_from_dispatch = schema_ops - set(_OPS.keys())
    if missing_from_dispatch:
        logger.error(
            "07_Visual_Operations schema lists ops with no dispatch: %s",
            sorted(missing_from_dispatch),
        )

    tool = ToolDefinition(
        name="07_Visual_Operations",
        description=(
            "Visual CRUD, layout, formatting, fields, sort, actions (visualLink), "
            "Deneb/code, slicers, visual calcs, sync, raw JSON. visual_name = id or "
            "exact title; page_name = id, exact name or unique prefix. A write needs "
            "a target (or match_all=true); several matches need allow_multiple=true. "
            "dry_run=true previews any write. Deleting a slicer/filtered visual needs "
            "confirm_delete_filter_layer=true: prefer operation=hide."
        ),
        handler=handle_visual_operations,
        input_schema=schema,
        category="pbip",
        sort_order=73,
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    registry.register(tool)
    logger.info("Registered visual_operations handler (v13 refactored)")
