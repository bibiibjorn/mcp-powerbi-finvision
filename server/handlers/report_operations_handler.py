"""
Report Operations Handler
Consolidated tool for report-level operations.

Operations:
- info: Get report structure (pages, visuals, filters) — from original report_info_handler
- measure_usage: Find measures used per page — from original report_info_handler
- rename: Rename report folder and .platform file
- rebind: Rebind to different semantic model
- backup: Create timestamped backup
- restore: Restore from backup
- discover_schema: Discover visual type properties and data roles
- manage_extension_measures: CRUD for report-level extension measures
- audit_interactions: read-only slicer reach / NoFilter audit + UI-suite plan (F-12)
"""

from typing import Dict, Any
import logging
from pathlib import Path

from server.registry import ToolDefinition
from core.utilities.pbip_utils import find_definition_folder
from core.validation.path_guard import guard_or_error
from core.pbip.report_format import attach_format_warning
from server.handlers._authoring_utils import gate_and_wrap
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)

# P0-53 fix: retrofit onto gate_and_wrap so writes get a reload_required
# hint. The previous inline pattern applied gating but never called
# attach_reload_hint, so callers never knew to reload PBI Desktop after
# rename / rebind / restore / manage_extension_measures.
_READ_ONLY_REPORT_OPS = {"info", "measure_usage", "discover_schema", "audit_interactions"}
_CATASTROPHIC_REPORT_OPS = {"restore"}


def _dispatch_report(args: Dict[str, Any], _unused) -> Dict[str, Any]:
    """Dispatcher for report-level operations (gate is applied upstream)."""
    operation = args.get("operation", "info")
    handler = _OPS.get(operation)
    if not handler:
        return error_envelope(
            "invalid_operation",
            f"Unknown operation: '{operation}'. Valid: {', '.join(sorted(_OPS.keys()))}",
        )
    return handler(args)


def handle_report_operations(args: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch report-level operations through gate_and_wrap."""
    operation = args.get("operation", "info")
    pbip_path = args.get("pbip_path")

    if not pbip_path:
        return error_envelope("missing_argument", "pbip_path is required")

    result = gate_and_wrap(
        args,
        operation,
        tool_name="07_Report_Operations",
        dispatch_fn=_dispatch_report,
        pbip_path_or_defn=pbip_path,
        read_only_ops=_READ_ONLY_REPORT_OPS,
        catastrophic_ops=_CATASTROPHIC_REPORT_OPS,
    )

    # PBIR GA (~Q3 2026): a legacy report auto-converts on the next Desktop
    # save, rewriting the whole folder. Warn the caller here — before they act
    # on this result — rather than leaving them to discover it in the diff.
    return attach_format_warning(result, pbip_path)


# --- Delegated operations (reuse existing logic) ---


def _op_info(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get report structure — delegates to existing report_info_handler.

    P-10: the read of every page/visual JSON (0.5 s per 1,000 visuals) is memoised
    per argument set until the report's definition folder changes.
    """
    from server.handlers._internal.report_info_helpers import handle_report_info

    definition = None
    try:
        definition = find_definition_folder(args.get("pbip_path"))
    except Exception:  # noqa: BLE001 -- handle_report_info reports the bad path itself
        definition = None
    if not definition:
        return handle_report_info(args)

    import json

    from server.pbip_cache import pbip_cache

    key = json.dumps(args, sort_keys=True, default=str)
    return pbip_cache.memoize_by_trees(
        "report_info", key, [str(definition)], lambda: handle_report_info(args)
    )


def _op_measure_usage(args: Dict[str, Any]) -> Dict[str, Any]:
    """Find measures used per page — delegates to existing report_info_handler."""
    from server.handlers._internal.report_info_helpers import handle_report_measure_usage

    return handle_report_measure_usage(args)


# --- New operations (dispatch to domain engines) ---


def _op_rename(args: Dict[str, Any]) -> Dict[str, Any]:
    """Rename report folder and .platform file."""
    from core.pbip.report_operations_engine import rename_report

    new_name = args.get("new_name")
    if not new_name:
        return error_envelope("missing_argument", "new_name is required for rename")

    return rename_report(
        pbip_path=args["pbip_path"],
        new_name=new_name,
    )


def _op_rebind(args: Dict[str, Any]) -> Dict[str, Any]:
    """Rebind report to a different semantic model."""
    from core.pbip.report_operations_engine import rebind_report

    model_path = args.get("model_path")
    model_id = args.get("model_id")
    if not model_path and not model_id:
        return error_envelope("missing_argument", "model_path or model_id is required for rebind")

    definition_path = find_definition_folder(args["pbip_path"])
    if not definition_path:
        return error_envelope(
            "not_found",
            f"Could not find definition folder in: {args['pbip_path']}",
        )

    return rebind_report(
        definition_path=definition_path,
        model_path=model_path,
        model_id=model_id,
    )


def _op_backup(args: Dict[str, Any]) -> Dict[str, Any]:
    """Create timestamped backup of the report."""
    from core.pbip.report_operations_engine import backup_report
    from server.handlers._internal.bookmark_theme_helpers import _find_report_folder

    try:
        report_path = Path(_find_report_folder(args["pbip_path"]))
    except FileNotFoundError as e:
        return error_envelope("operation_failed", str(e))

    return backup_report(
        report_path=report_path,
        message=args.get("message"),
    )


def _op_restore(args: Dict[str, Any]) -> Dict[str, Any]:
    """Restore report from a backup."""
    from core.pbip.report_operations_engine import restore_report
    from server.handlers._internal.bookmark_theme_helpers import _find_report_folder

    backup_path = args.get("backup_path")
    if not backup_path:
        return {
            "success": False,
            "error": "backup_path is required for restore",
            "error_type": "missing_backup_path",
        }

    try:
        report_path = Path(_find_report_folder(args["pbip_path"]))
    except FileNotFoundError as e:
        return {"success": False, "error": str(e), "error_type": "pbip_path_not_found"}

    # Restore reads from backup_path. Require it exists and stays inside an
    # allowed root — otherwise a hostile input could read arbitrary .zip from
    # elsewhere on disk. ``backup`` writes a SIBLING of the .Report folder
    # (``<name>.Report.backup_<ts>``), so the report's parent folder is an
    # allowed root too (the old guard refused every backup it had written).
    safe_backup, bp_err = guard_or_error(
        backup_path,
        "backup_path",
        must_exist=True,
        extra_roots=[args.get("pbip_path"), str(report_path.parent)],
    )
    if bp_err:
        return bp_err

    return restore_report(
        report_path=report_path,
        backup_path=str(safe_backup),
    )


def _op_discover_schema(args: Dict[str, Any]) -> Dict[str, Any]:
    """Discover visual type properties and data roles."""
    from core.pbip.report_operations_engine import discover_schema

    visual_type = args.get("visual_type")
    if not visual_type:
        return error_envelope("missing_argument", "visual_type is required for discover_schema")

    definition_path = find_definition_folder(args["pbip_path"])
    if not definition_path:
        return error_envelope(
            "not_found",
            f"Could not find definition folder in: {args['pbip_path']}",
        )

    return discover_schema(
        definition_path=definition_path,
        visual_type=visual_type,
    )


def _op_audit_interactions(args: Dict[str, Any]) -> Dict[str, Any]:
    """F-12: which visuals each slicer can reach, which NoFilter rules are wrong / missing.

    Read-only and offline: the report's PBIR + the bound model parsed from TMDL (relationships,
    measure / UDF dependencies through the dependency graph). ``policy`` (optional) names the
    isolations the report SHOULD have::

        {"isolate_visuals": ["<id|title>"], "isolate_visual_types": ["card"],
         "isolate": [{"slicer": "<id|title|*>", "visual": "<id|title|type:card|*>"}]}

    The result carries a flattened ``suite_plan`` (what to click, what must / must not change)
    for the UI suite; the engine is ``core.pbip.interaction_audit``.
    """
    from core.pbip.interaction_audit import audit_interactions, build_invariance_plan

    definition_path = find_definition_folder(args["pbip_path"])
    if not definition_path:
        return error_envelope(
            "not_found", f"Could not find definition folder in: {args['pbip_path']}"
        )
    policy = args.get("policy")
    if policy is not None and not isinstance(policy, dict):
        return error_envelope("invalid_argument", "policy must be an object")

    snapshot = None
    model_note = None
    try:
        from core.graph.model_source import snapshot_from_model_data
        from server.pbip_cache import pbip_cache

        data = pbip_cache.get_or_parse(args["pbip_path"])
        if data.model_data and data.model_data.get("tables"):
            snapshot = snapshot_from_model_data(data.model_data)
        else:
            model_note = "no local semantic model is bound (byConnection?); reach analysis skipped"
    except Exception as e:  # noqa: BLE001 - the audit still reports the declared rules
        logger.warning("audit_interactions: model unavailable: %s", e)
        model_note = f"model could not be parsed ({e}); reach analysis skipped"

    result = audit_interactions(
        Path(str(definition_path)),
        snapshot,
        page=args.get("page_name"),
        policy=policy,
    )
    if not result.get("success"):
        return result
    result["suite_plan"] = build_invariance_plan(result)
    for page in result.get("pages") or []:
        page.pop("suite_plan", None)
    if model_note:
        result["model_note"] = model_note
    result["operation"] = "audit_interactions"
    if args.get("full") is not True:
        _compact_interaction_audit(result)
    return result


def _compact_interaction_audit(result: Dict[str, Any]) -> None:
    """Default output for audit_interactions: the per-slicer pair lists of a real page run to
    hundreds of KB (R0201 'P&L Statement': 23 slicers x 148 visuals = 190 KB), so they become
    counts; the verdicts that need action (wrong / redundant NoFilter, missing isolation,
    dangling rules) stay listed and the suite plan keeps ``{visual, reason}`` per pair (title / type
    dropped). full=true returns the complete lists."""
    keep_lists = ("wrong_no_filter", "redundant_no_filter", "missing_isolation", "review")
    for page in result.get("pages") or []:
        for sl in page.get("slicers") or []:
            for key in ("needed", "no_effect", "missing_no_filter"):
                val = sl.get(key)
                if isinstance(val, list):
                    sl[key + "_count"] = len(val)
                    del sl[key]
            for key in keep_lists:
                val = sl.get(key)
                if isinstance(val, list) and len(val) > 25:
                    sl[key] = val[:25]
                    sl[key + "_truncated"] = True
            reach = sl.get("reach_tables")
            if isinstance(reach, list) and len(reach) > 10:
                sl["reach_tables"] = reach[:10]
                sl["reach_tables_count"] = len(reach)
        # ``suggested`` is {add_no_filter, remove_dangling, review_rules}: cap each sub-list.
        sug = page.get("suggested")
        if isinstance(sug, dict):
            for key, val in list(sug.items()):
                if isinstance(val, list) and len(val) > 25:
                    sug[key] = val[:25]
                    sug[key + "_count"] = len(val)
        elif isinstance(sug, list) and len(sug) > 25:
            page["suggested_count"] = len(sug)
            page["suggested"] = sug[:25]
    plan = result.get("suite_plan")
    if isinstance(plan, list):
        compact_plan = []
        for row in plan:
            if not isinstance(row, dict):
                continue
            r = dict(row)
            for key in ("must_change", "must_not_change"):
                vals = r.get(key)
                if isinstance(vals, list):
                    # Keep the per-pair OBJECT shape (``visual`` + ``reason``): the documented
                    # contract and the pbi-ui-test skill read must_not_change[].reason. Only the
                    # purely descriptive title / type are dropped.
                    r[key] = [
                        (
                            {k: v[k] for k in ("visual", "reason") if k in v}
                            if isinstance(v, dict)
                            else v
                        )
                        for v in vals
                    ]
            compact_plan.append(r)
        result["suite_plan"] = compact_plan
    result["compact"] = True
    result["full_hint"] = "pass full=true for every needed / no_effect / missing pair"


def _op_manage_extension_measures(args: Dict[str, Any]) -> Dict[str, Any]:
    """CRUD for report-level extension measures."""
    from core.pbip.extension_measures_engine import (
        list_measures,
        add_measure,
        update_measure,
        delete_measure,
    )

    sub_operation = args.get("sub_operation")
    if not sub_operation:
        return error_envelope(
            "missing_argument",
            "sub_operation is required (list, add, update, delete)",
        )

    definition_path = find_definition_folder(args["pbip_path"])
    if not definition_path:
        return error_envelope(
            "not_found",
            f"Could not find definition folder in: {args['pbip_path']}",
        )

    if sub_operation == "list":
        return list_measures(definition_path=definition_path)
    elif sub_operation == "add":
        measure_name = args.get("measure_name")
        expression = args.get("expression")
        if not measure_name or not expression:
            return error_envelope(
                "missing_argument",
                "measure_name and expression are required for add",
            )
        return add_measure(
            definition_path=definition_path,
            name=measure_name,
            expression=expression,
            table_ref=args.get("table_ref"),
            data_type=args.get("data_type", "double"),
            format_string=args.get("format_string"),
            description=args.get("description"),
        )
    elif sub_operation == "update":
        measure_name = args.get("measure_name")
        if not measure_name:
            return error_envelope("missing_argument", "measure_name is required for update")
        return update_measure(
            definition_path=definition_path,
            name=measure_name,
            expression=args.get("expression"),
            data_type=args.get("data_type"),
            format_string=args.get("format_string"),
            description=args.get("description"),
        )
    elif sub_operation == "delete":
        measure_name = args.get("measure_name")
        if not measure_name:
            return error_envelope("missing_argument", "measure_name is required for delete")
        return delete_measure(
            definition_path=definition_path,
            name=measure_name,
            force=bool(args.get("force", False)),
        )
    else:
        return error_envelope(
            "invalid_operation",
            f"Unknown sub_operation: '{sub_operation}'. Valid: list, add, update, delete",
        )


# --- Op registry (built after all functions are defined) ---

_OPS = {
    "info": _op_info,
    "measure_usage": _op_measure_usage,
    "rename": _op_rename,
    "rebind": _op_rebind,
    "backup": _op_backup,
    "restore": _op_restore,
    "discover_schema": _op_discover_schema,
    "manage_extension_measures": _op_manage_extension_measures,
    "audit_interactions": _op_audit_interactions,
}


# --- Registration ---


def register_report_operations_handler(registry):
    """Register the report operations tool."""
    from server.tool_schemas import TOOL_SCHEMAS

    registry.register(
        ToolDefinition(
            name="07_Report_Operations",
            description=(
                "REPORT-level PBIP ops: info (STATIC structure - to debug a visual use "
                "09_Debug_Operations), measure_usage, rename, rebind (to another semantic "
                "model), backup, restore (catastrophic), discover_schema, "
                "manage_extension_measures (thin-report measures), audit_interactions "
                "(read-only slicer reach / NoFilter audit). Offline model analysis: "
                "07_PBIP_Operations."
            ),
            guide_notes=(
                "REPORT-level operations on a PBIP project: static report "
                "structure (pages, visuals, filters), measure usage across the "
                "report, rename, rebind to another semantic model, backup and "
                "restore, visual field-schema discovery, and thin-report "
                "extension measures. Ops: info, measure_usage, rename, rebind, "
                "backup, restore, discover_schema, "
                "manage_extension_measures (sub_operation=list|add|update|"
                "delete), audit_interactions (read-only slicer reach / NoFilter audit; optional "
                "policy {isolate_visuals, isolate_visual_types, isolate:[{slicer,visual}]}; "
                "returns suite_plan). `info` is a STATIC read of the PBIR files — it cannot "
                "execute a visual's query, so to debug why a visual is blank, "
                "wrong or slow use 09_Debug_Operations instead. For offline "
                "model-side analysis use 07_PBIP_Operations; restore overwrites "
                "the report folder and is gated catastrophic."
            ),
            handler=handle_report_operations,
            input_schema=TOOL_SCHEMAS.get("report_operations", {}),
            category="pbip",
            sort_order=71,
            annotations={
                "readOnlyHint": False,
                # Includes restore (overwrites report folder), rename, rebind,
                # and manage_extension_measures (writes thin-report measures).
                "destructiveHint": True,
                "idempotentHint": False,
                "openWorldHint": True,
            },
        )
    )

    logger.info("Registered report_operations handler")
