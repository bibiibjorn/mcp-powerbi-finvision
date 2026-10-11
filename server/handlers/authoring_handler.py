"""
PBIP Authoring Handler

MCP tool handler for report authoring operations: cloning, creating,
and deleting pages and visuals in PBIP reports.
"""

import logging
from pathlib import Path
from typing import Any, Dict

from server.registry import ToolDefinition
from server.tool_schemas import REQUEST_ID_PROPERTY
from server.progress import emit_progress
from core.utilities.pbip_utils import resolve_definition_path
from core.validation.path_guard import guard_or_error
from server.handlers._authoring_utils import gate_and_wrap
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)

# P1-131: per-tool classification sets. Previously duplicated from
# _authoring_utils' module defaults; now passed explicitly so this tool's
# semantics don't leak across handlers.
_READ_ONLY_AUTHORING_OPS = {"list_templates", "get_template", "pbir_resolve_container"}
_CATASTROPHIC_AUTHORING_OPS = {"delete_page", "clone_report"}


def _severity(args: Dict[str, Any], pbip_path: Any) -> Any:
    """H-25: delete_visual is rated exactly like 07_Visual_Operations:delete."""
    if args.get("operation") == "delete_visual":
        from server.handlers.visual_creation_handler import delete_severity

        return delete_severity(args, pbip_path)
    return None


def _dispatch_authoring(args: Dict[str, Any], pbip_path: str) -> Dict[str, Any]:
    """Gate-free dispatcher for authoring operations."""
    operation = args.get("operation", "")

    ops = {
        "clone_page": _op_clone_page,
        "clone_report": _op_clone_report,
        "clone_visual": _op_clone_visual,
        "create_page": _op_create_page,
        "create_visual": _op_create_visual,
        "create_visual_group": _op_create_visual_group,
        "delete_page": _op_delete_page,
        "delete_visual": _op_delete_visual,
        "list_templates": _op_list_templates,
        "get_template": _op_get_template,
        "pbir_add_visuals": _op_pbir_add_visuals,
        "pbir_resolve_container": _op_pbir_resolve_container,
    }

    handler = ops.get(operation)
    if not handler:
        return error_envelope(
            "invalid_operation",
            f"Unknown operation: '{operation}'. Valid: {', '.join(sorted(ops.keys()))}",
        )

    return handler(args, pbip_path)


def handle_pbip_authoring(args: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch PBIP authoring operations via gate_and_wrap.

    P1-120 / P1-131: retrofit from inline gate+hint pattern onto
    shared gate_and_wrap helper so 11_PBIP_Authoring and 07_Visual_Operations
    go through the same governance code path.

    P1-121: previously returned "Unknown operation: ''" on missing
    operation; now returns a specific "operation is required" error.

    P1-136: _READ_ONLY_AUTHORING_OPS no longer requires pbip_path since
    list_templates / get_template / pbir_resolve_container don't touch disk.
    """
    operation = args.get("operation", "")
    pbip_path = args.get("pbip_path", "")

    if not operation:
        return error_envelope("missing_argument", "operation is required for 11_PBIP_Authoring")

    # Read-only ops don't need pbip_path.
    if not pbip_path and operation not in _READ_ONLY_AUTHORING_OPS:
        return error_envelope("missing_argument", "pbip_path is required")

    return gate_and_wrap(
        args,
        operation,
        tool_name="11_PBIP_Authoring",
        dispatch_fn=_dispatch_authoring,
        pbip_path_or_defn=pbip_path,
        read_only_ops=_READ_ONLY_AUTHORING_OPS,
        catastrophic_ops=_CATASTROPHIC_AUTHORING_OPS,
        severity_fn=_severity,
    )


# --- Operation Implementations ---


def _op_clone_page(args: Dict[str, Any], pbip_path: str) -> Dict[str, Any]:
    """Clone a page with all visuals, regenerating IDs."""
    from core.pbip.authoring.clone_engine import CloneEngine

    resolved = resolve_definition_path(pbip_path)
    if "error" in resolved:
        return {"success": False, **resolved}

    source_page = args.get("source_page")
    if not source_page:
        return error_envelope(
            "missing_argument",
            "source_page is required (page ID or display name)",
        )

    emit_progress(1, 3, f"Cloning page {source_page!r}")
    engine = CloneEngine()
    result = engine.clone_page(
        definition_path=resolved["path"],
        source_page_id=source_page,
        new_display_name=args.get("new_display_name"),
        insert_after=args.get("insert_after"),
    )
    emit_progress(3, 3, "Clone complete")
    result["operation"] = "clone_page"
    return result


def _op_clone_report(args: Dict[str, Any], pbip_path: str) -> Dict[str, Any]:
    """Clone an entire report with new IDs."""
    from core.pbip.authoring.clone_engine import CloneEngine

    from core.utilities.pbip_utils import normalize_path, resolve_pbip_report_path

    source = resolve_pbip_report_path(pbip_path)
    if not source:
        return error_envelope("operation_failed", f"Could not resolve report path: {pbip_path}")

    target_path = args.get("target_path")
    if not target_path:
        return {
            "success": False,
            "error": "target_path is required",
            "error_type": "missing_target_path",
        }

    target_path = normalize_path(target_path)
    # Guard target_path — clone_report writes a full copytree. Don't let a
    # hostile input clone into C:\Windows or outside the intended workspace.
    # create_parents so the target's parent dir is allowed to be auto-created.
    safe_target, t_err = guard_or_error(
        target_path,
        "target_path",
        extra_roots=[str(Path(source).parent), args.get("pbip_path", "")],
        create_parents=True,
    )
    if t_err:
        return t_err
    target_path = str(safe_target)
    emit_progress(1, 3, "Staging copy + ID remap")
    engine = CloneEngine()
    result = engine.clone_report(
        source_report_path=Path(source),
        target_path=Path(target_path),
        new_report_name=args.get("new_report_name"),
    )
    emit_progress(3, 3, "Clone complete")
    result["operation"] = "clone_report"
    return result


def _op_clone_visual(args: Dict[str, Any], pbip_path: str) -> Dict[str, Any]:
    """F-16: clone ONE visual / group (same or another page) with fresh ids.

    Carries parentGroupName handling, the visual's filterConfig and -- with
    ``inherit_interactions=true`` -- every visualInteractions entry in both directions.
    """
    from core.pbip.authoring.visual_clone import clone_visual

    resolved = resolve_definition_path(pbip_path)
    if "error" in resolved:
        return {"success": False, **resolved}

    source_page = args.get("source_page") or args.get("page_id") or args.get("page_name")
    visual = args.get("visual_id") or args.get("visual_name")
    if not source_page or not visual:
        return error_envelope(
            "missing_argument",
            "clone_visual needs source_page (page id or name) and visual_id (or visual_name)",
        )
    offset = args.get("offset")
    position = args.get("position")
    for label, val in (("offset", offset), ("position", position)):
        if val is not None and not isinstance(val, dict):
            return error_envelope("invalid_argument", f"{label} must be an object")
    imap = args.get("interaction_map")
    if imap is not None and not isinstance(imap, dict):
        return error_envelope("invalid_argument", "interaction_map must be an object")

    emit_progress(1, 2, f"Cloning visual {visual!r}")
    try:
        result = clone_visual(
            Path(resolved["path"]),
            str(source_page),
            str(visual),
            target_page=args.get("target_page"),
            offset=offset,
            position=position,
            position_frame=str(args.get("position_frame") or "page"),
            group_mode=str(args.get("group_mode") or "auto"),
            parent_group=args.get("parent_group"),
            inherit_interactions=_truthy(args.get("inherit_interactions", False)),
            interaction_map=imap,
            copy_bookmark_refs=_truthy(args.get("copy_bookmark_refs", False)),
        )
    except ValueError as e:  # non-numeric position / offset
        return error_envelope("invalid_argument", str(e))
    emit_progress(2, 2, "Clone complete")
    result["operation"] = "clone_visual"
    return result


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _op_create_page(args: Dict[str, Any], pbip_path: str) -> Dict[str, Any]:
    """Create a new empty page."""
    from core.pbip.authoring.page_builder import PageBuilder

    resolved = resolve_definition_path(pbip_path)
    if "error" in resolved:
        return {"success": False, **resolved}

    page_name = args.get("page_name")
    if not page_name:
        return error_envelope("missing_argument", "page_name is required")

    builder = PageBuilder(resolved["path"], page_name)

    width = args.get("width")
    height = args.get("height")
    if width and height:
        builder.set_dimensions(int(width), int(height))

    insert_after = args.get("insert_after")
    result = builder.build(insert_after=insert_after)
    result["operation"] = "create_page"
    return result


def _op_create_visual(args: Dict[str, Any], pbip_path: str) -> Dict[str, Any]:
    from server.handlers.visual_creation_handler import op_create_visual

    resolved = resolve_definition_path(pbip_path)
    if "error" in resolved:
        return {"success": False, **resolved}
    return op_create_visual(args, resolved["path"])


def _op_create_visual_group(args: Dict[str, Any], pbip_path: str) -> Dict[str, Any]:
    from server.handlers.visual_creation_handler import op_create_visual_group

    resolved = resolve_definition_path(pbip_path)
    if "error" in resolved:
        return {"success": False, **resolved}
    return op_create_visual_group(args, resolved["path"])


def _op_delete_page(args: Dict[str, Any], pbip_path: str) -> Dict[str, Any]:
    """Delete a page and update pages.json."""
    from core.pbip.authoring.clone_engine import CloneEngine

    resolved = resolve_definition_path(pbip_path)
    if "error" in resolved:
        return {"success": False, **resolved}

    page_id = args.get("page_id") or args.get("page_name")
    if not page_id:
        return error_envelope("missing_argument", "page_id or page_name is required")

    engine = CloneEngine()
    result = engine.delete_page(resolved["path"], page_id)
    result["operation"] = "delete_page"
    return result


def _op_delete_visual(args: Dict[str, Any], pbip_path: str) -> Dict[str, Any]:
    from server.handlers.visual_creation_handler import op_delete_visual

    resolved = resolve_definition_path(pbip_path)
    if "error" in resolved:
        return {"success": False, **resolved}
    return op_delete_visual(args, resolved["path"])


def _op_list_templates(args: Dict[str, Any], pbip_path: str) -> Dict[str, Any]:
    from server.handlers.visual_creation_handler import op_list_templates

    return op_list_templates(args, None)


def _op_get_template(args: Dict[str, Any], pbip_path: str) -> Dict[str, Any]:
    from server.handlers.visual_creation_handler import op_get_template

    return op_get_template(args, None)


def _op_pbir_add_visuals(args: Dict[str, Any], pbip_path: str) -> Dict[str, Any]:
    """Bulk-add visuals via pbir CLI with optional container parenting."""
    from core.pbip.authoring.pbir_add_visuals import add_visuals

    resolved = resolve_definition_path(pbip_path)
    if "error" in resolved:
        return {"success": False, **resolved}

    page = args.get("page_name") or args.get("page") or args.get("page_id")
    if not page:
        return {
            "success": False,
            "error": "page_name (page id or display name) is required",
            "error_type": "missing_argument",
        }

    visuals = args.get("visuals") or []
    if not isinstance(visuals, list) or not visuals:
        return error_envelope(
            "invalid_argument",
            "visuals must be a non-empty list of {visual_type, x, y, ...} dicts",
        )

    container_x = args.get("container_x")
    container_y = args.get("container_y")
    container_group_id = args.get("container_group_id")
    container_display_name = args.get("container_display_name")
    timeout = args.get("timeout")
    emit_progress(1, 3, f"Adding {len(visuals)} visual(s)")
    result = add_visuals(
        definition_path=Path(resolved["path"]),
        page=str(page),
        visuals=visuals,
        # Coerce to str — callers passing an int / other type should still land
        # as a string before path join downstream.
        container_group_id=str(container_group_id) if container_group_id else None,
        container_display_name=str(container_display_name) if container_display_name else None,
        # H-19: omitted -> None, so add_visuals computes the container's
        # page-absolute offset itself (0.0 treated a nested group as top-left).
        container_x=float(container_x) if container_x is not None else None,
        container_y=float(container_y) if container_y is not None else None,
        timeout=int(timeout) if timeout is not None else 120,
    )
    # Add fully_applied flag for container-parenting clarity (carry-over P2-8).
    if isinstance(result, dict) and "container_applied" in result and "visual_ids" in result:
        applied = result.get("container_applied") or []
        created = result.get("visual_ids") or []
        result["fully_applied"] = bool(result.get("success")) and len(applied) == len(created)
    emit_progress(3, 3, "Complete")
    result["operation"] = "pbir_add_visuals"
    return result


def _op_pbir_resolve_container(args: Dict[str, Any], pbip_path: str) -> Dict[str, Any]:
    """Look up a container group id + its page-absolute coords by displayName.

    Useful as a preparatory call before pbir_add_visuals, so the caller knows
    what container_x/container_y to subtract.
    """
    from core.pbip.authoring.pbir_add_visuals import resolve_container

    resolved = resolve_definition_path(pbip_path)
    if "error" in resolved:
        return {"success": False, **resolved}

    page = args.get("page_name") or args.get("page") or args.get("page_id")
    display_name = args.get("container_display_name")
    if not page or not display_name:
        return {
            "success": False,
            "error": "page_name and container_display_name are required",
            "error_type": "missing_argument",
        }

    container_id, coords = resolve_container(Path(resolved["path"]), str(page), str(display_name))
    if container_id is None:
        return {
            "success": False,
            "error": f"container group not found: {display_name}",
            "error_type": "container_not_found",
        }
    return {
        "success": True,
        "operation": "pbir_resolve_container",
        "container_id": container_id,
        "coords": coords,
    }


# --- Registration ---


AUTHORING_SCHEMA = {
    "type": "object",
    "properties": {
        # F-27: idempotency key (deferred in tools/list; dispatch replays a recorded write)
        "request_id": dict(REQUEST_ID_PROPERTY),
        "pbip_path": {
            "type": "string",
            "description": "Path to PBIP project, .Report folder, or .pbip file",
        },
        "operation": {
            "type": "string",
            "enum": [
                "clone_page",
                "clone_report",
                "clone_visual",
                "create_page",
                "create_visual",
                "create_visual_group",
                "delete_page",
                "delete_visual",
                "list_templates",
                "get_template",
                "pbir_add_visuals",
                "pbir_resolve_container",
            ],
            "description": "Operation to perform",
        },
        # Clone operations
        "source_page": {
            "type": "string",
            "description": "Source page ID or display name (for clone_page)",
        },
        "new_display_name": {
            "type": "string",
            "description": "New display name for cloned page",
        },
        "target_path": {
            "type": "string",
            "description": "Target path for cloned report (clone_report)",
        },
        # clone_visual
        "target_page": {
            "type": "string",
            "description": "clone_visual: target page (default: source)",
        },
        "offset": {
            "type": "object",
            "description": "clone_visual: page-absolute {x, y} added to the source position",
            "x-doc": "clone_visual: page-absolute {x, y} added to the source position (default "
            "20/20 on the same page, 0/0 on another page).",
            "properties": {"x": {"type": "number"}, "y": {"type": "number"}},
        },
        "group_mode": {
            "type": "string",
            "enum": ["auto", "keep", "clone_group", "top_level"],
            "description": "clone_visual: parent-group handling",
            "x-doc": "clone_visual parent handling: keep the source group (same page), clone_group "
            "(clone the empty ancestor groups onto the target page), top_level (drop the parent; "
            "coordinates converted). auto = keep on the same page, else top_level.",
        },
        "inherit_interactions": {
            "type": "boolean",
            "description": "clone_visual: copy the source's visualInteractions in both directions",
        },
        "interaction_map": {
            "type": "object",
            "description": "clone_visual cross-page: {source visual id: target visual id}",
        },
        "copy_bookmark_refs": {
            "type": "boolean",
            "description": "clone_visual: add the clone to the bookmarks that captured the source",
        },
        "new_report_name": {
            "type": "string",
            "description": "New name for cloned report",
        },
        # Page operations
        "page_name": {
            "type": "string",
            "description": "create_page: the new page's display name; other ops: page id or "
            "display name",
        },
        "width": {
            "type": "integer",
            "description": "Page width in pixels (default: 1280)",
        },
        "height": {
            "type": "integer",
            "description": "Page height in pixels (default: 720)",
        },
        "insert_after": {
            "type": "string",
            "description": "Page ID to insert new page after",
        },
        # Visual operations
        "visual_type": {
            "type": "string",
            "description": "Visual type (e.g., columnChart, card, table, slicer, shape)",
        },
        "visual_id": {
            "type": "string",
            "description": "Visual ID (for delete_visual)",
        },
        "visual_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "delete_visual: several ids",
        },
        "confirm_delete_filter_layer": {
            "type": "boolean",
            "description": "delete_visual of a slicer/filtered visual",
        },
        "position_frame": {"type": "string", "enum": ["page", "group"], "default": "page"},
        "dry_run": {"type": "boolean", "description": "Preview any write"},
        "visual_name": {
            "type": "string",
            "description": "Visual name/title (alternative to visual_id)",
        },
        "title": {
            "type": "string",
            "description": "Visual title text",
        },
        "position": {
            "type": "object",
            "description": "Visual position {x, y, width, height, z}",
            "properties": {
                "x": {"type": "number"},
                "y": {"type": "number"},
                "width": {"type": "number"},
                "height": {"type": "number"},
                "z": {"type": "integer"},
            },
        },
        "measures": {
            "type": "array",
            "description": "Measure bindings [{table, measure, bucket?, display_name?}]",
            "items": {
                "type": "object",
                "properties": {
                    "table": {"type": "string"},
                    "measure": {"type": "string"},
                    "bucket": {"type": "string"},
                    "display_name": {"type": "string"},
                },
                "required": ["table", "measure"],
            },
        },
        "columns": {
            "type": "array",
            "description": "Column bindings [{table, column, bucket?, display_name?}]",
            "items": {
                "type": "object",
                "properties": {
                    "table": {"type": "string"},
                    "column": {"type": "string"},
                    "bucket": {"type": "string"},
                    "display_name": {"type": "string"},
                },
                "required": ["table", "column"],
            },
        },
        "parent_group": {
            "type": "string",
            "description": "Parent visual group ID",
        },
        "group_name": {
            "type": "string",
            "description": "Display name for visual group",
        },
        "formatting": {
            "type": "array",
            "description": "Formatting overrides [{config_type, property_name, value}]",
            "items": {
                "type": "object",
                "properties": {
                    "config_type": {"type": "string"},
                    "property_name": {"type": "string"},
                    "value": {},
                },
            },
        },
        "delete_children": {
            "type": "boolean",
            "description": "Delete child visuals of groups (default: true)",
        },
        # pbir_add_visuals / pbir_resolve_container
        "visuals": {
            "type": "array",
            "description": (
                "pbir_add_visuals specs [{visual_type, x, y (page-absolute), width, height, "
                "title, fields:{role:'Table.Field'}, field_types?, objects?}]."
            ),
            "x-doc": (
                "pbir_add_visuals specs: {visual_type, x, y (page-absolute), width, height, "
                "title, fields:{role:'Table.Field'}, field_types?:{role:'Measure'|'Column'}, "
                "objects?, visualContainerObjects?}. Measure vs Column comes from the "
                "bound model when field_types is omitted."
            ),
            "items": {"type": "object"},
        },
        "container_group_id": {
            "type": "string",
            "description": "Parent new visuals into this group id (pbir_add_visuals)",
        },
        "container_display_name": {
            "type": "string",
            "description": "Container group displayName (must be unique on the page)",
        },
        "container_x": {
            "type": "number",
            "description": "Container page-absolute X; computed when omitted",
        },
        "container_y": {
            "type": "number",
            "description": "Container page-absolute Y; computed when omitted",
        },
        "timeout": {
            "type": "integer",
            "description": "Ignored (kept for compatibility)",
            "minimum": 5,
        },
    },
    # Only `operation` is strictly required — read-only ops (list_templates,
    # get_template, pbir_resolve_container) don't touch disk and don't need
    # pbip_path. The handler validates pbip_path per-op at runtime, so declaring
    # it here would reject valid MCP-schema-conforming read-only calls.
    "required": ["operation"],
}


def register_authoring_handler(registry):
    """Register the PBIP authoring tool."""
    registry.register(
        ToolDefinition(
            name="11_PBIP_Authoring",
            description=(
                "Create, clone, and delete pages/visuals in PBIP reports. "
                "Operations: clone_page, clone_report, clone_visual (one visual, fresh ids, "
                "inherit_interactions), create_page, create_visual, "
                "create_visual_group, delete_page, delete_visual, list_templates, "
                "get_template, pbir_add_visuals (bulk direct-write create with "
                "container parenting, page-absolute coords, role pre-flight and "
                "Measure/Column from the bound model), pbir_resolve_container "
                "(group id + page-absolute coords by displayName). dry_run=true "
                "previews any write."
            ),
            handler=handle_pbip_authoring,
            input_schema=AUTHORING_SCHEMA,
            category="authoring",
            sort_order=1100,
            annotations={
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
                "openWorldHint": False,
            },
        )
    )
