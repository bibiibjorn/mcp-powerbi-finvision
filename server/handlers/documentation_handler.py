"""
Documentation Handler
Handles Word documentation generation and updates via unified tool.
"""

from typing import Dict, Any
import logging
from server.progress import emit_progress
from server.registry import ToolDefinition
from core.infrastructure.connection_state import connection_state
from core.validation.error_handler import ErrorHandler
from core.validation.path_guard import guard_or_error

logger = logging.getLogger(__name__)


def handle_documentation_word(args: Dict[str, Any]) -> Dict[str, Any]:
    """Unified Word documentation: generate or update.

    2026-09-22 (I-09): ``output_path`` ending in ``.docx`` is written exactly
    there (a directory otherwise); ``update`` reads the snapshot saved next to
    ``input_path`` (``<stem>.snapshot.json``) or an explicit ``snapshot_path``
    and, without ``output_path``, rewrites ``input_path`` in place.
    """
    if not connection_state.is_connected():
        return ErrorHandler.handle_not_connected()

    agent_policy = connection_state.agent_policy
    if not agent_policy:
        return ErrorHandler.handle_manager_unavailable("agent_policy")

    mode = args.get("mode", "generate")
    if mode not in ("generate", "update"):
        return {
            "success": False,
            "error": f"Unknown mode '{mode}'. Valid: generate, update",
            "error_type": "invalid_input",
        }
    include_hidden = bool(args.get("include_hidden", True))

    output_path = args.get("output_path")
    # Guard output_path so a hostile caller can't write the Word doc to
    # C:\Windows\System32 or exfil into a user-profile path outside the
    # workspace. Must-not-exist check is deliberately OFF — we write it.
    safe_output, err = guard_or_error(output_path, "output_path", create_parents=True)
    if err:
        return err
    if safe_output is not None:
        output_path = str(safe_output)

    if mode == "update":
        input_path = args.get("input_path")
        if not input_path:
            return {
                "success": False,
                "error": "input_path is required for mode='update' (the existing .docx or its .snapshot.json)",
                "error_type": "missing_input_path",
            }
        safe_input, in_err = guard_or_error(input_path, "input_path", must_exist=True)
        if in_err:
            return in_err
        snapshot_path = args.get("snapshot_path")
        if snapshot_path:
            safe_snap, snap_err = guard_or_error(snapshot_path, "snapshot_path", must_exist=True)
            if snap_err:
                return snap_err
            snapshot_path = str(safe_snap)
        return agent_policy.documentation_orch.update_word_documentation(
            connection_state,
            snapshot_path=snapshot_path,
            output_dir=output_path,
            include_hidden=include_hidden,
            input_path=str(safe_input),
        )

    # F-26: the generation is one orchestrator call; report the bracket.
    emit_progress(0, 1, "Generating Word documentation")
    generated = agent_policy.documentation_orch.generate_word_documentation(
        connection_state, output_path, include_hidden=include_hidden
    )
    emit_progress(1, 1, "Word documentation generated")
    return generated


def register_documentation_handlers(registry):
    """Register documentation handler"""
    tool = ToolDefinition(
        name="08_Documentation_Word",
        description=(
            "Microsoft Word documentation of the LIVE model, as a docx data dictionary of "
            "tables, columns, measures, relationships, calc groups, RLS, sources, UDFs + BPA "
            "findings. mode=generate writes output_path (+ <name>.snapshot.json); mode=update "
            "diffs the model against the snapshot next to input_path and adds a change log. "
            "HTML report from PBIP files: 07_PBIP_Operations analyze output_format=html; "
            "lineage docs: 09_Document."
        ),
        guide_notes=(
            # First sentence is the search surface: keep the ".docx" out of it so
            # a client that splits on the first period doesn't truncate this to
            # "Generate or update Word (".
            "Generate or update a Microsoft Word documentation report for the "
            "LIVE model - a docx data dictionary covering tables, columns, "
            "measures, relationships, calculation groups, RLS roles, Power Query "
            "sources, UDFs and the BPA findings. mode=generate writes output_path "
            "(a .docx file path, or a folder) plus a <name>.snapshot.json next to "
            "it; mode=update compares the live model with the snapshot saved next "
            "to input_path, adds a change log and rewrites input_path in place "
            "(or writes output_path). Requires an active connection "
            "(01_Connection). Word output only: for an HTML model report from "
            "the OFFLINE PBIP files use 07_PBIP_Operations operation=analyze "
            "with output_format=html (NOT generate_documentation, which emits "
            "JSON), and for per-page or per-measure lineage docs use "
            "09_Document."
        ),
        handler=handle_documentation_word,
        input_schema={
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["generate", "update"],
                },
                "output_path": {
                    "type": "string",
                    "description": "Output .docx file path, or a folder (timestamped file name). update: defaults to input_path (in place)",
                },
                "input_path": {
                    "type": "string",
                    "description": "mode=update: the existing .docx (its <stem>.snapshot.json holds the previous state) or the snapshot .json itself",
                },
                "snapshot_path": {
                    "type": "string",
                    "description": "mode=update: explicit previous snapshot (.json) when it is not next to input_path",
                },
                "include_hidden": {
                    "type": "boolean",
                    "default": True,
                    "description": "Include hidden tables/columns/measures",
                },
            },
            "required": [],
        },
        category="docs",
        sort_order=80,
        annotations={
            # Writes a .docx to disk on both generate/update — NOT read-only.
            # A read-only annotation would let it bypass read_only_mode lockdown.
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )

    registry.register(tool)
    logger.info("Registered 1 documentation handler")
