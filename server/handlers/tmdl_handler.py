"""
TMDL Operations Handler
Unified handler for all TMDL operations
"""

from typing import Dict, Any
import logging
from server.registry import ToolDefinition
from server.tool_schemas import TOOL_SCHEMAS
from core.infrastructure.connection_state import connection_state
from core.validation.error_handler import ErrorHandler
from core.validation.path_guard import guard_or_error
from core.autonomous.reload_hints import attach_reload_hint
from core.autonomous.gating import require_autonomous_mode, Severity, merge_gate_info
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)

_GATED_TMDL_OPS = {"find_replace", "bulk_rename", "migrate_measures", "format_dax"}
# Ops that don't write PBIP files — skip the reload-hint to avoid misleading
# callers into thinking a PBI Desktop reload is required. `export` reads TMDL
# from the live model; `generate_script` is a pure transform; `format_dax`
# writes via live TOM (02_Model_Operations:measure/update) so no PBIP reload.
_READ_ONLY_TMDL_OPS = {"export", "generate_script", "format_dax"}


def _dispatch_tmdl(args: Dict[str, Any]) -> Dict[str, Any]:
    """Gate-free dispatcher for TMDL operations."""
    operation = args.get("operation")

    # Route to appropriate handler based on operation
    if operation == "export":
        return _handle_export(args)
    elif operation == "find_replace":
        return _handle_find_replace(args)
    elif operation == "bulk_rename":
        return _handle_bulk_rename(args)
    elif operation == "generate_script":
        return _handle_generate_script(args)
    elif operation == "migrate_measures":
        return _handle_migrate_measures(args)
    elif operation == "format_dax":
        return _handle_format_dax(args)
    else:
        return error_envelope(
            "invalid_operation",
            f"Unknown operation: {operation}",
        )


def _handle_format_dax(args: Dict[str, Any]) -> Dict[str, Any]:
    """Bulk-format DAX expressions and write back via live TOM.

    Args:
        engine: "daxformatter" (default; the external service, opt-in) or "local" (F-23: the
                deterministic in-process formatter ``core.dax.local_formatter`` - no network,
                works without ``dax_formatter.enabled``).
        measure_refs: list of "Table[Name]" refs. If omitted/empty AND
                      `format_all=True`, all model measures are formatted.
        format_all: bool — if True, ignore measure_refs and format every measure
                    in the live model.
        skip_space_after_function: local engine only - ``SUM( x )`` instead of ``SUM ( x )``.
        max_line_length: passed to formatter (default 90).
        dry_run: bool — when True, return planned diffs but DO NOT write.
        on_error: "skip" | "abort" — what to do if formatter rejects an expression.
        time_budget_s: stop sending after this many seconds (default 120) and
                    report the unprocessed refs in ``remaining``.

    Returns: {success, dry_run, formatted: [{ref, before, after, written}], stats,
    remaining, external_service}.

    Review D-20 (2026-09-22): DAX is only sent to daxformatter.com when the user
    opted in (``dax_formatter.enabled`` / ``MCP_DAX_FORMATTER_ENABLED``) — the
    default is OFF, which changes the previous behaviour; the formatted text is
    written back only when it is token-equivalent to the original.
    """
    from core.dax.dax_formatter_client import DISABLED_MESSAGE, is_enabled

    engine = str(args.get("engine") or "daxformatter").strip().lower()
    if engine not in ("daxformatter", "local"):
        return error_envelope(
            "invalid_argument",
            f"engine must be 'daxformatter' or 'local', got {args.get('engine')!r}",
        )
    local = engine == "local"

    if not local and not is_enabled():
        return {
            "success": False,
            "error": DISABLED_MESSAGE,
            "error_type": "external_formatter_disabled",
            "external_service": "daxformatter.com",
            "suggestion": "pass engine='local' to format on this machine without sending DAX anywhere",
        }

    if not connection_state.is_connected():
        return ErrorHandler.handle_not_connected()

    dry_run = bool(args.get("dry_run", False))
    if not dry_run:
        # N-01: format_dax rewrites measures through the connected model; refuse on the XMLA target.
        from core.infrastructure import offline_model, xmla_target

        if xmla_target.is_active() and not offline_model.is_active():
            refused = xmla_target.refuse_write("02_TMDL_Operations", "format_dax", args)
            if refused is not None:
                return refused

    # Resolve targets
    refs = list(args.get("measure_refs") or [])
    format_all = bool(args.get("format_all", False))
    max_line_length = int(args.get("max_line_length", 90))
    on_error = str(args.get("on_error", "skip"))
    _budget = args.get("time_budget_s")
    time_budget_s = float(_budget) if _budget is not None else 120.0

    if not refs and not format_all:
        return error_envelope(
            "missing_argument",
            "Either measure_refs (list of T[Name]) or format_all=true is required",
        )

    # Build target list with current expressions
    from core.infrastructure.live_model_facade import build_live_model

    model = build_live_model()
    if model is None:
        return ErrorHandler.handle_not_connected()

    targets = []  # list of {ref, table, name, expression}
    if format_all:
        for tbl in getattr(model, "tables", []) or []:
            for m in getattr(tbl, "measures", []) or []:
                targets.append(
                    {
                        "ref": f"{tbl.name}[{m.name}]",
                        "table": tbl.name,
                        "name": m.name,
                        "expression": m.expression or "",
                    }
                )
    else:
        import re as _re

        for ref in refs:
            m = _re.match(r"^([^\[]+)\[([^\]]+)\]$", ref or "")
            if not m:
                return error_envelope(
                    "invalid_argument",
                    f"Invalid measure_ref {ref!r} (expected 'Table[Name]')",
                )
            tname, mname = m.group(1), m.group(2)
            found = None
            for tbl in model.tables:
                if tbl.name == tname:
                    for meas in tbl.measures:
                        if meas.name == mname:
                            found = meas
                            break
            if found is None:
                return error_envelope(
                    "not_found",
                    f"measure {ref} not found in model",
                )
            targets.append(
                {
                    "ref": ref,
                    "table": tname,
                    "name": mname,
                    "expression": found.expression or "",
                }
            )

    # Call formatter
    items = [{"key": t["ref"], "expression": t["expression"]} for t in targets]
    if local:
        from core.dax.local_formatter import format_many_local

        fmt = format_many_local(
            items,
            max_line_length=max_line_length,
            skip_space_after_function=bool(args.get("skip_space_after_function", False)),
            on_error=on_error,
        )
    else:
        from core.dax.dax_formatter_client import format_many

        fmt = format_many(
            items,
            max_line_length=max_line_length,
            on_error=on_error,
            time_budget_s=time_budget_s,
        )
    from core.dax.dax_formatter_client import is_unchanged, token_equivalent

    processed = {r.get("key") for r in fmt["results"]}
    targets = [t for t in targets if t["ref"] in processed]

    # Apply via live TOM (unless dry_run)
    out_rows = []
    written = 0
    skipped_unchanged = 0
    rejected_not_equivalent = 0
    apply_errors = []
    if not dry_run:
        from core.operations.measure_operations import MeasureOperationsHandler

        meas_h = MeasureOperationsHandler()
        for tgt, res in zip(targets, fmt["results"]):
            if not res.get("success"):
                out_rows.append(
                    {
                        "ref": tgt["ref"],
                        "before": tgt["expression"],
                        "after": None,
                        "written": False,
                        "error": res.get("error"),
                    }
                )
                continue
            new_expr = res["formatted"]
            if is_unchanged(tgt["expression"], new_expr):
                skipped_unchanged += 1
                out_rows.append(
                    {
                        "ref": tgt["ref"],
                        "before": tgt["expression"],
                        "after": new_expr,
                        "written": False,
                        "note": "unchanged",
                    }
                )
                continue
            equivalent, why = token_equivalent(tgt["expression"], new_expr)
            if not equivalent:
                # Never write text that is not the same DAX (an error page / banner
                # returned with HTTP 200, a changed literal, a dropped token).
                rejected_not_equivalent += 1
                out_rows.append(
                    {
                        "ref": tgt["ref"],
                        "before": tgt["expression"],
                        "after": new_expr,
                        "written": False,
                        "error": f"formatted text is not token-equivalent to the original ({why}); not written",
                    }
                )
                continue
            update_res = meas_h.execute(
                {
                    "object_type": "measure",
                    "operation": "update",
                    "table_name": tgt["table"],
                    "measure_name": tgt["name"],
                    "expression": new_expr,
                }
            )
            ok = bool(update_res.get("success", False))
            if ok:
                written += 1
                out_rows.append(
                    {
                        "ref": tgt["ref"],
                        "before": tgt["expression"],
                        "after": new_expr,
                        "written": True,
                    }
                )
            else:
                apply_errors.append({"ref": tgt["ref"], "error": update_res.get("error")})
                out_rows.append(
                    {
                        "ref": tgt["ref"],
                        "before": tgt["expression"],
                        "after": new_expr,
                        "written": False,
                        "error": update_res.get("error"),
                    }
                )
    else:
        for tgt, res in zip(targets, fmt["results"]):
            out_rows.append(
                {
                    "ref": tgt["ref"],
                    "before": tgt["expression"],
                    "after": res.get("formatted") if res.get("success") else None,
                    "written": False,
                    "note": "dry_run" if res.get("success") else None,
                    "error": res.get("error"),
                }
            )

    out = {
        "success": fmt["success"] and not apply_errors and not rejected_not_equivalent,
        "dry_run": dry_run,
        "formatted": out_rows,
        "engine": engine,
        "external_service": None if local else "daxformatter.com",
        "stats": {
            **fmt["stats"],
            "written": written,
            "skipped_unchanged": skipped_unchanged,
            "rejected_not_equivalent": rejected_not_equivalent,
            "apply_errors": len(apply_errors),
        },
    }
    if fmt.get("remaining"):
        out["remaining"] = fmt["remaining"]
        out["partial"] = True
        out["note"] = (
            f"Stopped after the time budget / an abort; {len(fmt['remaining'])} measure(s) "
            "were not sent. Re-run with measure_refs=remaining."
        )
    return out


def handle_tmdl_operations(args: Dict[str, Any]) -> Dict[str, Any]:
    """Handle unified TMDL operations with severity-gated wrapper."""
    operation = args.get("operation")

    if not operation:
        # No reload-hint on missing-op errors — the hint is meaningless when
        # we don't know what op was intended.
        return error_envelope(
            "missing_argument",
            "operation parameter is required",
        )

    # Gate only mutating ops. generate_script is pure transform; export is a read.
    gate = None
    # format_dax with dry_run=true only returns the planned diffs (a READ, as op_classification
    # already says): it carries no destructive-op gate notice.
    format_dax_preview = operation == "format_dax" and bool(args.get("dry_run", False))
    if operation in _GATED_TMDL_OPS and not format_dax_preview:
        # Review G-04 / A-16: a cascade into report JSON is CATASTROPHIC whether
        # it is requested as `true` or as a report path (it used to escalate
        # only on the literal `True`, which the engine never received).
        if operation == "bulk_rename" and _cascade_requested(args.get("cascade_to_reports")):
            severity = Severity.CATASTROPHIC
        else:
            severity = Severity.DESTRUCTIVE
        op_label = f"02_TMDL_Operations:{operation}"
        gate = require_autonomous_mode(op_label, args, severity)
        if gate is not None and gate.get("success") is False:
            # Gate-rejection is an error, not a PBIP write — skip the hint.
            return gate

    result = _dispatch_tmdl(args)
    merge_gate_info(result, gate)

    # Skip the reload hint for read-only ops (export, generate_script) and
    # for error responses that didn't mutate disk.
    if operation in _READ_ONLY_TMDL_OPS:
        return result
    if isinstance(result, dict) and result.get("success") is False:
        return result
    return attach_reload_hint(result, "02_TMDL_Operations", operation)


def _cascade_requested(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in ("", "false", "0", "no")
    return bool(value)


def _handle_export(args: Dict[str, Any]) -> Dict[str, Any]:
    """Export TMDL definition"""
    if not connection_state.is_connected():
        return ErrorHandler.handle_not_connected()

    model_exporter = connection_state.model_exporter
    if not model_exporter:
        return ErrorHandler.handle_manager_unavailable("model_exporter")

    output_dir = args.get("output_dir")
    if output_dir:
        safe_dir, err = guard_or_error(output_dir, "output_dir", create_parents=True)
        if err:
            return err
        output_dir = str(safe_dir)
    return model_exporter.export_tmdl(output_dir)


def _handle_find_replace(args: Dict[str, Any]) -> Dict[str, Any]:
    """Find and replace in TMDL with regex support"""
    tmdl_path = args.get("tmdl_path")
    pattern = args.get("pattern")
    replacement = args.get("replacement")
    dry_run = args.get("dry_run", True)
    regex = args.get("regex", False)
    case_sensitive = args.get("case_sensitive", True)
    target = args.get("target", "all")
    scope = args.get("scope", "expression")
    include_strings = bool(args.get("include_strings_and_comments", False))

    if not tmdl_path:
        return error_envelope(
            "missing_argument",
            "tmdl_path parameter is required for find_replace operation",
        )

    # Guard the TMDL root against traversal — find_replace will mutate files
    # under this path. must_exist is True: you can't find-replace on nothing.
    safe_tmdl, err = guard_or_error(tmdl_path, "tmdl_path", must_exist=True)
    if err:
        return err
    tmdl_path = str(safe_tmdl)

    if not pattern or replacement is None:
        return error_envelope(
            "missing_argument",
            "pattern and replacement parameters are required for find_replace operation",
        )

    # Regex sanity cap: catastrophic-backtracking patterns tend to grow nested
    # quantifiers fast. A 4KB pattern is already absurd for TMDL find/replace.
    # Also compile once upfront so a bad regex fails fast at the handler edge
    # with a clear error, rather than deep inside TmdlBulkEditor.
    if regex:
        if len(pattern) > 4096:
            return {
                "success": False,
                "error": f"regex pattern too long ({len(pattern)} chars; max 4096)",
                "error_type": "pattern_too_long",
            }
        try:
            import re as _re

            _re.compile(pattern)
        except _re.error as re_err:
            return {
                "success": False,
                "error": f"invalid regex pattern: {re_err}",
                "error_type": "invalid_regex",
            }

    try:
        from core.tmdl import TmdlBulkEditor

        editor = TmdlBulkEditor()

        result = editor.replace_in_measures(
            tmdl_path=tmdl_path,
            find=pattern,
            replace=replacement,
            regex=regex,
            case_sensitive=case_sensitive,
            dry_run=dry_run,
            target=target,
            scope=scope,
            include_strings_and_comments=include_strings,
        )

        return result.to_dict()

    except ImportError as ie:
        logger.error(f"Import error: {ie}", exc_info=True)
        return {
            "success": False,
            "error": "TmdlBulkEditor not available. This is an internal error.",
            "error_type": "import_error",
        }
    except FileNotFoundError as fnf:
        return error_envelope(
            "not_found",
            f"TMDL path not found: {str(fnf)}",
        )
    except Exception as e:
        logger.error(f"Error in TMDL find/replace: {e}", exc_info=True)
        return error_envelope(
            "operation_failed",
            f"Error in TMDL find/replace: {str(e)}",
        )


def _handle_bulk_rename(args: Dict[str, Any]) -> Dict[str, Any]:
    """Bulk rename with reference updates"""
    tmdl_path = args.get("tmdl_path")
    renames = args.get("renames", [])
    dry_run = args.get("dry_run", True)
    update_references = args.get("update_references", True)
    cascade = args.get("cascade_to_reports")
    if isinstance(cascade, str) and not _cascade_requested(cascade):
        cascade = None
    elif isinstance(cascade, str):
        safe_cascade, err = guard_or_error(cascade, "cascade_to_reports", must_exist=True)
        if err:
            return err
        cascade = str(safe_cascade)

    if not tmdl_path:
        return error_envelope(
            "missing_argument",
            "tmdl_path parameter is required for bulk_rename operation",
        )

    safe_tmdl, err = guard_or_error(tmdl_path, "tmdl_path", must_exist=True)
    if err:
        return err
    tmdl_path = str(safe_tmdl)

    if not renames:
        return error_envelope(
            "missing_argument",
            'renames parameter required for bulk_rename operation (array of rename operations with "object_type", "old_name" and "new_name")',
        )

    # Validate and normalize rename operations
    normalized_renames = []
    for i, rename in enumerate(renames):
        if not isinstance(rename, dict):
            return error_envelope(
                "invalid_argument",
                f"Rename operation {i+1} must be a dictionary",
            )

        # Ensure required fields exist
        if "old_name" not in rename or "new_name" not in rename:
            return error_envelope(
                "operation_failed",
                f'Rename operation {i+1} must have "old_name" and "new_name" fields',
            )

        # Add object_type if missing (default to measure)
        normalized_rename = {
            "object_type": rename.get("object_type", "measure"),
            "old_name": rename["old_name"],
            "new_name": rename["new_name"],
        }

        # Add optional table_name for measures and columns
        if "table_name" in rename:
            normalized_rename["table_name"] = rename["table_name"]

        normalized_renames.append(normalized_rename)

    try:
        from core.tmdl import TmdlBulkEditor

        editor = TmdlBulkEditor()

        result = editor.bulk_rename(
            tmdl_path=tmdl_path,
            renames=normalized_renames,
            update_references=update_references,
            dry_run=dry_run,
            cascade_to_reports=cascade if cascade else None,
        )

        return result.to_dict()

    except ImportError as ie:
        logger.error(f"Import error: {ie}", exc_info=True)
        return {
            "success": False,
            "error": "TmdlBulkEditor not available. This is an internal error.",
            "error_type": "import_error",
        }
    except FileNotFoundError as fnf:
        return error_envelope(
            "not_found",
            f"TMDL path not found: {str(fnf)}",
        )
    except Exception as e:
        logger.error(f"Error in TMDL bulk rename: {e}", exc_info=True)
        return error_envelope(
            "operation_failed",
            f"Error in TMDL bulk rename: {str(e)}",
        )


def _handle_generate_script(args: Dict[str, Any]) -> Dict[str, Any]:
    """Generate TMDL script from definition"""
    definition = args.get("definition")
    object_type = args.get("object_type", "table")  # table, measure, relationship, calc_group

    if not definition:
        return error_envelope(
            "missing_argument",
            "definition parameter is required for generate_script operation (object definition as dict)",
        )

    try:
        from core.tmdl import TmdlScriptGenerator

        generator = TmdlScriptGenerator()

        # Use the unified method that handles all object types
        script = generator.generate_from_definition(object_type, definition)

        return {"success": True, "script": script}

    except ImportError as ie:
        logger.error(f"Import error: {ie}", exc_info=True)
        return {
            "success": False,
            "error": "TmdlScriptGenerator not available. This is an internal error.",
            "error_type": "import_error",
        }
    except Exception as e:
        logger.error(f"Error generating TMDL script: {e}", exc_info=True)
        return error_envelope(
            "operation_failed",
            f"Error generating TMDL script: {str(e)}",
        )


def _handle_migrate_measures(args: Dict[str, Any]) -> Dict[str, Any]:
    """Migrate measures between TMDL files with display folder filtering"""
    source_path = args.get("source_path")
    target_path = args.get("target_path")
    display_folder_filter = args.get("display_folder_filter")
    replace_target = args.get("replace_target", False)
    skip_duplicates = args.get("skip_duplicates", True)
    dry_run = args.get("dry_run", True)

    if not source_path:
        return error_envelope(
            "missing_argument",
            "source_path parameter is required for migrate_measures operation",
        )

    if not target_path:
        return error_envelope(
            "missing_argument",
            "target_path parameter is required for migrate_measures operation",
        )

    # Review G-14: both paths are guarded like find_replace / bulk_rename.
    safe_src, err = guard_or_error(source_path, "source_path", must_exist=True)
    if err:
        return err
    safe_tgt, err = guard_or_error(target_path, "target_path", create_parents=False)
    if err:
        return err
    source_path, target_path = str(safe_src), str(safe_tgt)

    try:
        from core.tmdl import TmdlMeasureMigrator

        migrator = TmdlMeasureMigrator()

        result = migrator.migrate_measures(
            source_path=source_path,
            target_path=target_path,
            display_folder_filter=display_folder_filter,
            replace_target=replace_target,
            skip_duplicates=skip_duplicates,
            dry_run=dry_run,
        )

        return result.to_dict()

    except ImportError as ie:
        logger.error(f"Import error: {ie}", exc_info=True)
        return {
            "success": False,
            "error": "TmdlMeasureMigrator not available. This is an internal error.",
            "error_type": "import_error",
        }
    except FileNotFoundError as fnf:
        return error_envelope(
            "not_found",
            f"Source file not found: {str(fnf)}",
        )
    except Exception as e:
        logger.error(f"Error in measure migration: {e}", exc_info=True)
        return error_envelope(
            "operation_failed",
            f"Error in measure migration: {str(e)}",
        )


def register_tmdl_operations_handler(registry):
    """Register unified TMDL operations handler"""

    tool = ToolDefinition(
        name="02_TMDL_Operations",
        description=(
            "Offline TMDL file edits on a PBIP semantic model: export, find_replace, "
            "bulk_rename (cascade_to_reports renames PBIR bindings too), generate_script, "
            "migrate_measures - writes need a Desktop reopen. Plus format_dax (LIVE: formats "
            "measure DAX via daxformatter.com, opt-in, written through TOM). dry_run=true "
            "previews. One live object: 02_Model_Operations."
        ),
        guide_notes=(
            "TMDL operations: export, find/replace, bulk rename, script generation, "
            "measure migration (offline PBIP file edits — writes trigger reload_required), "
            "PLUS format_dax (LIVE — formats one/many/all measure DAX expressions via "
            "daxformatter.com and writes back through live TOM update; no PBIP reload). "
            "Use measure_refs=['T[Name]', ...] for a batch, or format_all=true for the "
            "whole model. dry_run=true previews diffs without writing. For live model "
            "CRUD on individual measures use 02_Model_Operations instead."
        ),
        handler=handle_tmdl_operations,
        input_schema=TOOL_SCHEMAS["tmdl_operations"],
        category="model",
        sort_order=26,  # 02 = Model Operations
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )

    registry.register(tool)
    logger.info("Registered tmdl_operations handler")
