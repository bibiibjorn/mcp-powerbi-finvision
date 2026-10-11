"""
INTERNAL HELPER — Not a registered MCP tool.
Provides helper functions consumed by active handlers.

Called by analysis_handler.py (06_Analysis_Operations.compare mode).

2026-09-22 review (I-21): with exactly two Desktop instances open and no ports
given, the older instance (earliest start) is OLD and the newer is NEW; a
single port is a validation error (it used to be silently ignored); labels are
the Desktop file names; ``output_path`` is path-guarded.
"""

import logging
from typing import Any, Dict, List, Optional

from core.infrastructure.connection_state import connection_state
from core.validation.error_handler import ErrorHandler
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)


def _port(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    return int(value)


def _instance_label(instances: List[Dict[str, Any]], port: int) -> Optional[str]:
    for inst in instances:
        if inst.get("port") == port:
            return inst.get("display_name") or inst.get("database")
    return None


def handle_compare_targets(args: Dict[str, Any]) -> Dict[str, Any]:
    """F-17: ``old`` / ``new`` targets (port | xmla | pbip_path | git_ref + pbip_path).

    Needs no live connection unless a side is a port. The XMLA side is read-only and not
    live-verified; the PBIP / git sides load TMDL into an in-memory TOM database.
    """
    if args.get("old") is None or args.get("new") is None:
        return error_envelope("invalid_input", "Provide BOTH old and new (a target each)")
    if args.get("old_port") is not None or args.get("new_port") is not None:
        return error_envelope(
            "invalid_input", "Use either old/new targets or old_port/new_port, not both"
        )
    output_path = args.get("output_path")
    if output_path:
        from core.validation.path_guard import guard_or_error

        safe, err = guard_or_error(output_path, "output_path", create_parents=True)
        if err:
            return err
        output_path = str(safe)
    from core.comparison.model_comparison_orchestrator import ModelComparisonOrchestrator

    result = ModelComparisonOrchestrator().compare_targets(
        args["old"],
        args["new"],
        output_path=output_path,
        generate_json=bool(args.get("generate_json", False)),
        report=bool(args.get("report", True)),
    )
    if result.get("success"):
        s = result["summary"]
        result["workflow"] = {
            "name": "Model Comparison",
            "status": "completed",
            "summary": (
                f"{s['affects_results']} change(s) can affect results, {s['cosmetic']} cosmetic "
                f"({s['old']} -> {s['new']})"
            ),
        }
    return result


def handle_compare_pbi_models(args: Dict[str, Any]) -> Dict[str, Any]:
    """Compare two open Power BI models (06_Analysis_Operations operation=compare)."""
    if args.get("old") is not None or args.get("new") is not None:
        return handle_compare_targets(args)
    if not connection_state.is_connected():
        return ErrorHandler.handle_not_connected()

    connection_manager = connection_state.connection_manager
    if not connection_manager:
        return ErrorHandler.handle_manager_unavailable("connection_manager")

    instances = connection_manager.detect_instances() or []
    if len(instances) < 2:
        return {
            "success": False,
            "error": "Need at least 2 Power BI Desktop instances running for comparison",
            "error_type": "insufficient_instances",
            "detected_instances": len(instances),
            "instruction": "Open both Power BI models in separate Desktop instances",
        }

    try:
        old_port = _port(args.get("old_port"))
        new_port = _port(args.get("new_port"))
    except (ValueError, TypeError):
        return {
            "success": False,
            "error": "old_port and new_port must be valid integers",
            "error_type": "invalid_input",
        }

    auto_selected = False
    if old_port is None and new_port is None:
        if len(instances) == 2:
            # Older Desktop instance (earliest start) = OLD. detect_instances()
            # lists newest first, so reversed order is the fallback.
            if all(i.get("started_at") for i in instances):
                ordered = sorted(instances, key=lambda i: str(i.get("started_at")))
            else:
                ordered = list(reversed(instances))
            old_port, new_port = ordered[0].get("port"), ordered[1].get("port")
            auto_selected = True
        else:
            return {
                "success": True,
                "requires_input": True,
                "message": (
                    f"{len(instances)} models detected. Specify old_port and new_port "
                    "(auto-selection only happens with exactly two instances open)."
                ),
                "instances": [
                    {
                        "port": inst.get("port"),
                        "name": inst.get("display_name") or inst.get("database"),
                        "file_path": inst.get("file_full_path"),
                        "started_at": inst.get("started_at"),
                    }
                    for inst in instances
                ],
                "workflow": {"name": "Model Comparison", "status": "awaiting_user_input"},
            }
    elif old_port is None or new_port is None:
        return {
            "success": False,
            "error": "Provide BOTH old_port and new_port (or neither, with exactly two instances open)",
            "error_type": "invalid_input",
        }

    if not (1 <= old_port <= 65535) or not (1 <= new_port <= 65535):
        return {
            "success": False,
            "error": "Port numbers must be between 1 and 65535",
            "error_type": "invalid_input",
        }
    if old_port == new_port:
        return {
            "success": False,
            "error": "old_port and new_port must be different instances",
            "error_type": "invalid_input",
        }

    output_path = args.get("output_path")
    if output_path:
        from core.validation.path_guard import guard_or_error

        safe, err = guard_or_error(output_path, "output_path", create_parents=True)
        if err:
            return err
        output_path = str(safe)

    try:
        from core.comparison.model_comparison_orchestrator import ModelComparisonOrchestrator

        result = ModelComparisonOrchestrator().compare_models(
            old_port,
            new_port,
            output_path=output_path,
            generate_json=bool(args.get("generate_json", False)),
            model1_label=_instance_label(instances, old_port),
            model2_label=_instance_label(instances, new_port),
        )
        if result.get("success"):
            result["ports"] = {"old": old_port, "new": new_port, "auto_selected": auto_selected}
            result["workflow"] = {
                "name": "Model Comparison",
                "status": "completed",
                "summary": f"Compared models (old: {old_port}, new: {new_port})",
                "next_recommendations": [
                    {
                        "action": "Open the HTML report",
                        "path": (result.get("reports") or {}).get("html"),
                    },
                    {
                        "action": "Document the NEW model",
                        "tool": "08_Documentation_Word",
                        "description": "Word data dictionary of the connected model (mode=update keeps a change log)",
                    },
                ],
            }
        return result
    except ImportError as e:
        logger.error(f"Import error in model comparison: {e}", exc_info=True)
        return {
            "success": False,
            "error": f"Model comparison module not available: {str(e)}",
            "error_type": "import_error",
        }
    except Exception as e:
        logger.error(f"Error comparing models: {e}", exc_info=True)
        return error_envelope("operation_failed", f"Error comparing models: {str(e)}")
