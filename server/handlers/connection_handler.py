"""
Connection Handler — 01_Connection (detect / connect / reconnect / ensure / status).

2026-09-22 review (slice B):
* B-03 — health probes go straight to the server (``probe_connection_health``:
  DBSCHEMA_CATALOGS, short CommandTimeout, never the result cache), so
  ``ensure`` can no longer report a dead or re-ported instance as healthy.
* B-05 — every swap rebuilds the managers bound to the old connection and
  clears the ID maps / result cache (``connection_state._on_connection_swapped``).
* B-12 — the instance is chosen on ONE detection and connected with
  ``connect_instance(instance)``; the reported ``pbip_info`` belongs to the
  model actually connected.
* B-07 / K-09 — ``detect`` / ``status`` report the AMO/ADOMD build that is
  selected and loaded (``amo``).
"""

from typing import Any, Dict, List, Optional
import logging

from server.registry import ToolDefinition
from core.infrastructure.connection_state import connection_state
from core.validation.error_handler import ErrorHandler
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)


def _amo_info(include_candidates: bool = False) -> Optional[Dict[str, Any]]:
    try:
        from core.infrastructure.dll_paths import get_amo_info

        return get_amo_info(include_candidates=include_candidates)
    except Exception as e:  # noqa: BLE001
        logger.debug("get_amo_info failed: %s", e)
        return None


def _port_of(conn_str: Any) -> Optional[str]:
    try:
        cs = str(conn_str or "")
        if "localhost:" in cs:
            return cs.split("localhost:")[1].split(";")[0].strip()
    except Exception:  # noqa: BLE001
        pass
    return None


def _connect_chosen(cm, instances: List[Dict[str, Any]], index: int) -> Dict[str, Any]:
    """Connect to ``instances[index]`` exactly (B-12), falling back to index-connect."""
    instance = instances[index]
    connect_instance = getattr(cm, "connect_instance", None)
    if callable(connect_instance):
        result = connect_instance(instance)
    else:  # legacy manager without connect_instance
        result = cm.connect(index)
    if isinstance(result, dict) and result.get("success"):
        result.setdefault("model_index", index)
        result.setdefault("total_instances", len(instances))
    return result


def _apply_instance_state(instance: Dict[str, Any], source: str) -> None:
    """Managers + PBIP info for the instance just connected."""
    connection_state.initialize_managers()
    try:
        pbip_folder = instance.get("pbip_folder_path")
        file_path = instance.get("file_full_path")
        file_type = instance.get("file_type")
        if pbip_folder or file_path:
            connection_state.set_pbip_info(
                pbip_folder_path=pbip_folder,
                file_full_path=file_path,
                file_type=file_type,
                source=source,
            )
            if pbip_folder:
                logger.info(f"PBIP folder auto-detected: {pbip_folder}")
    except Exception as pbip_error:
        logger.warning(f"Failed to store PBIP info (non-critical): {pbip_error}")


def _prewarm_table_mappings() -> None:
    try:
        qe = connection_state.query_executor
        if qe and hasattr(qe, "_ensure_table_mappings"):
            qe._ensure_table_mappings()
    except Exception as cache_error:
        logger.warning(f"Failed to pre-warm table mappings (non-critical): {cache_error}")


def _probe() -> Dict[str, Any]:
    """Server round-trip health probe (B-03)."""
    try:
        probe = connection_state.probe_connection_health()
    except Exception as e:  # noqa: BLE001
        return error_envelope("operation_failed", str(e))
    return probe if isinstance(probe, dict) else {"success": bool(probe)}


def handle_detect_powerbi_desktop(args: Dict[str, Any]) -> Dict[str, Any]:
    """Detect running Power BI Desktop instances"""
    try:
        connection_manager = connection_state.connection_manager
        if not connection_manager:
            return ErrorHandler.handle_manager_unavailable("connection_manager")

        instances = connection_manager.detect_instances()
        out: Dict[str, Any] = {"success": True, "instances": instances, "count": len(instances)}
        amo = _amo_info()
        if amo:
            out["amo"] = amo
        return out
    except Exception as e:
        logger.error(f"Error detecting instances: {e}", exc_info=True)
        return ErrorHandler.handle_unexpected_error("detect_powerbi_desktop", e)


def handle_connect_to_powerbi(args: Dict[str, Any]) -> Dict[str, Any]:
    """Connect to Power BI Desktop instance"""
    try:
        connection_manager = connection_state.connection_manager
        if not connection_manager:
            return ErrorHandler.handle_manager_unavailable("connection_manager")

        raw_index = args.get("model_index", 0)
        try:
            model_index = int(raw_index)
        except (TypeError, ValueError):
            return {
                "success": False,
                "error": f"model_index must be an integer; got {raw_index!r}",
                "error_type": "invalid_model_index",
            }
        if model_index < 0:
            return {
                "success": False,
                "error": f"model_index must be >= 0; got {model_index}",
                "error_type": "invalid_model_index",
            }

        instances = connection_manager.detect_instances()
        if not instances:
            return {
                "success": False,
                "error": "No Power BI Desktop instances detected",
                "error_type": "no_instances",
            }
        if model_index >= len(instances):
            return {
                "success": False,
                "error": (
                    f"model_index {model_index} out of range; "
                    f"only {len(instances)} Power BI Desktop instance(s) detected"
                ),
                "error_type": "invalid_model_index",
                "available_instances": len(instances),
            }

        result = _connect_chosen(connection_manager, instances, model_index)
        if result.get("success"):
            connection_state.set_connection_manager(connection_manager)
            _apply_instance_state(instances[model_index], "auto-detected")
            result["pbip_info"] = connection_state.get_pbip_info()
            _prewarm_table_mappings()
            result["connection"] = connection_state.get_connection_identity()
        return result

    except Exception as e:
        logger.error(f"Error connecting: {e}", exc_info=True)
        return ErrorHandler.handle_unexpected_error("connect_to_powerbi", e)


def handle_reconnect(args: Dict[str, Any]) -> Dict[str, Any]:
    """Re-acquire the ADOMD/TOM connection in-place.

    Use when PBI Desktop's msmdsrv port has changed (process restart, reopen,
    crash+relaunch) or the live connection is stale. Only the connection objects
    and manager singletons are rebuilt — PBI Desktop is not touched.

    Returns old_port vs new_port + a server round-trip health check.
    """
    try:
        cm = connection_state.connection_manager
        if not cm:
            return ErrorHandler.handle_manager_unavailable("connection_manager")

        prev_info = connection_state.get_pbip_info() or {}
        prev_file = prev_info.get("file_full_path")
        prev_port = None
        try:
            prev_conn = cm.get_connection()
            prev_port = _port_of(getattr(prev_conn, "ConnectionString", "") if prev_conn else "")
        except Exception:
            pass

        instances = cm.detect_instances()
        if not instances:
            return {
                "success": False,
                "error": "No Power BI Desktop instances detected",
                "error_type": "no_instances",
            }

        try:
            target_index = int(args.get("model_index", 0))
        except (TypeError, ValueError):
            target_index = 0
        if prev_file:
            for i, inst in enumerate(instances):
                if inst.get("file_full_path") == prev_file:
                    target_index = i
                    break
        if target_index < 0 or target_index >= len(instances):
            return {
                "success": False,
                "error": (
                    f"model_index {target_index} out of range; "
                    f"only {len(instances)} Power BI Desktop instance(s) detected"
                ),
                "error_type": "invalid_model_index",
                "available_instances": len(instances),
            }

        result = _connect_chosen(cm, instances, target_index)
        if not result.get("success"):
            return result

        connection_state.set_connection_manager(cm)
        _apply_instance_state(instances[target_index], "auto-detected")

        probe = _probe()
        health_ok = bool(probe.get("success"))
        health_error = None if health_ok else probe.get("error")
        new_port = _port_of(result.get("connection_string", ""))

        if not health_ok:
            return {
                "success": False,
                "reconnected": True,
                "error_type": "connection_unhealthy",
                "health_error": health_error,
                "previous_port": prev_port,
                "new_port": new_port,
                "model_index": target_index,
                "message": "Reconnected but post-reconnect health probe failed; retry recommended.",
            }

        return {
            "success": True,
            "reconnected": True,
            "port_changed": bool(prev_port and new_port and prev_port != new_port),
            "previous_port": prev_port,
            "new_port": new_port,
            "file": prev_file,
            "model_index": target_index,
            "health_ok": health_ok,
            "health_error": health_error,
            "pbip_info": connection_state.get_pbip_info(),
            "connection": connection_state.get_connection_identity(),
        }
    except Exception as e:
        logger.error(f"Error reconnecting: {e}", exc_info=True)
        return ErrorHandler.handle_unexpected_error("reconnect", e)


def _current_connection_matches(cm: Any, args: Dict[str, Any]) -> bool:
    """True when the live connection already points at the instance ``args`` names."""
    try:
        current_port = (connection_state.get_connection_identity() or {}).get("port")
        instances = cm.detect_instances() or []
    except Exception:  # noqa: BLE001
        return True  # can't tell: keep today's behaviour rather than drop a connection
    target = None
    if args.get("prefer_pid") is not None:
        try:
            pid = int(args["prefer_pid"])
            target = next((i for i in instances if i.get("pid") == pid), None)
        except (TypeError, ValueError):
            target = None
    if target is None and args.get("file_path"):
        target = next((i for i in instances if i.get("file_full_path") == args["file_path"]), None)
    if target is None and args.get("model_index") is not None:
        try:
            idx = int(args["model_index"])
            target = instances[idx] if 0 <= idx < len(instances) else None
        except (TypeError, ValueError):
            target = None
    if target is None:
        return True  # named target not found: the normal path below reports it
    return str(target.get("port")) == str(current_port)


def handle_ensure(args: Dict[str, Any]) -> Dict[str, Any]:
    """Idempotent connect.

    1. If currently connected AND a server round-trip probe succeeds (never the
       result cache — B-03), return ``{success: True, reused: True, ...}``.
    2. Otherwise call ``detect_instances`` ONCE and pick the best match:
       a) ``prefer_pid`` (when supplied and live)
       b) instance whose ``file_full_path`` matches ``file_path`` (when supplied)
       c) instance whose ``file_full_path`` matches the previous session's
       d) ``model_index`` (default 0)
    3. Connect to exactly that instance (B-12), initialize managers, pre-warm the
       table mapping cache. Returns ``{success: True, reused: False, ...}``.

    Never raises — connection errors collapse into ``{success: False, ...}``.
    """
    try:
        cm = connection_state.connection_manager
        if not cm:
            return ErrorHandler.handle_manager_unavailable("connection_manager")

        # Live 2026-09-23: `ensure prefer_pid=<other instance>` returned reused=True
        # and stayed on the CURRENT instance. The fast path may only reuse the
        # connection when no target was named, or when it already IS the target.
        wants_target = any(
            args.get(k) is not None for k in ("prefer_pid", "file_path", "model_index")
        )
        if connection_state.is_connected() and wants_target:
            if not _current_connection_matches(cm, args):
                logger.info("ensure: connected instance is not the requested target; switching")
                try:
                    connection_state.cleanup()
                except Exception as e:  # noqa: BLE001
                    logger.debug("ensure: cleanup before switch failed: %s", e)
        if connection_state.is_connected():
            probe = _probe()
            if probe.get("success"):
                return {
                    "success": True,
                    "reused": True,
                    "pbip_info": connection_state.get_pbip_info(),
                    "health_ok": True,
                    "connection": connection_state.get_connection_identity(),
                }
            logger.info(
                "ensure: existing connection failed the health probe (%s); reconnecting",
                probe.get("error"),
            )

        instances = cm.detect_instances()
        if not instances:
            return {
                "success": False,
                "error": "No Power BI Desktop instances detected",
                "error_type": "no_instances",
            }

        prefer_pid = args.get("prefer_pid")
        file_path = args.get("file_path")
        prev_file = (connection_state.get_pbip_info() or {}).get("file_full_path")

        chosen = None
        if prefer_pid is not None:
            try:
                pid_int = int(prefer_pid)
                chosen = next(
                    (i for i, inst in enumerate(instances) if inst.get("pid") == pid_int),
                    None,
                )
            except (TypeError, ValueError):
                pass
        if chosen is None and file_path:
            chosen = next(
                (i for i, inst in enumerate(instances) if inst.get("file_full_path") == file_path),
                None,
            )
        if chosen is None and prev_file:
            chosen = next(
                (i for i, inst in enumerate(instances) if inst.get("file_full_path") == prev_file),
                None,
            )
        if chosen is None:
            try:
                chosen = int(args.get("model_index", 0))
            except (TypeError, ValueError):
                chosen = 0
            if chosen < 0 or chosen >= len(instances):
                return {
                    "success": False,
                    "error": (
                        f"model_index {chosen} out of range; "
                        f"only {len(instances)} Power BI Desktop instance(s) detected"
                    ),
                    "error_type": "invalid_model_index",
                    "available_instances": len(instances),
                }

        result = _connect_chosen(cm, instances, chosen)
        if not result.get("success"):
            return result

        connection_state.set_connection_manager(cm)
        _apply_instance_state(instances[chosen], "ensure")
        _prewarm_table_mappings()

        result["reused"] = False
        result["pbip_info"] = connection_state.get_pbip_info()
        result["connection"] = connection_state.get_connection_identity()
        return result
    except Exception as e:
        logger.error(f"Error in ensure: {e}", exc_info=True)
        return ErrorHandler.handle_unexpected_error("ensure", e)


def handle_status(args: Dict[str, Any]) -> Dict[str, Any]:
    """Connection identity, command-gate state and the loaded AMO/ADOMD build."""
    try:
        cm = connection_state.connection_manager
        instance = cm.get_instance_info() if cm else None
        out: Dict[str, Any] = {
            "success": True,
            "connected": connection_state.is_connected(),
            "instance": instance,
            "connection": connection_state.get_connection_identity(),
            "pbip_info": connection_state.get_pbip_info(),
            "amo": _amo_info(include_candidates=True),
        }
        gate = getattr(cm, "gate", None) if cm else None
        if gate is not None and hasattr(gate, "status"):
            out["command_gate"] = gate.status()
        from core.infrastructure import offline_model, xmla_target
        from server import tool_profiles

        out["target"] = (
            "offline_folder"
            if offline_model.is_active()
            else ("xmla" if xmla_target.is_active() else "desktop")
        )
        if xmla_target.is_active():
            out["xmla_target"] = xmla_target.active()
        if offline_model.is_active():
            out["offline"] = offline_model.info()
        out["tool_profile"] = tool_profiles.describe()
        if args.get("probe"):
            out["health"] = _probe()
        return out
    except Exception as e:
        logger.error(f"Error in status: {e}", exc_info=True)
        return ErrorHandler.handle_unexpected_error("status", e)


def handle_connect_xmla(args: Dict[str, Any]) -> Dict[str, Any]:
    """XM-01: connect READ-ONLY to a published model over the XMLA endpoint."""
    try:
        cm = connection_state.connection_manager
        if not cm:
            return ErrorHandler.handle_manager_unavailable("connection_manager")
        workspace = args.get("workspace")
        model = args.get("model")
        if not workspace or not model:
            return error_envelope("missing_argument", "connect_xmla needs workspace and model")
        _leave_offline()
        result = cm.connect_xmla(str(workspace), str(model))
        if result.get("success"):
            connection_state.set_connection_manager(cm)
            connection_state.initialize_managers(force_reinit=True)
            result["connection"] = connection_state.get_connection_identity()
        return result
    except Exception as e:
        logger.error(f"Error in connect_xmla: {e}", exc_info=True)
        return ErrorHandler.handle_unexpected_error("connect_xmla", e)


def handle_connect_folder(args: Dict[str, Any]) -> Dict[str, Any]:
    """OFF-01: edit a PBIP model offline through the TOM serializer (no Desktop)."""
    try:
        from core.infrastructure import offline_model

        path = args.get("pbip_path")
        if not path:
            return error_envelope(
                "missing_argument",
                "connect_folder needs pbip_path (.pbip, .SemanticModel or definition/)",
            )
        # N-27: the Desktop-open override is a break-glass; it needs the explicit confirmation.
        allow_open = bool(args.get("allow_open_in_desktop"))
        if allow_open and args.get("confirm_destructive") is not True:
            return error_envelope(
                "confirmation_required",
                "allow_open_in_desktop lets offline edits be overwritten by an open Desktop session; "
                "re-call with confirm_destructive=true if the user really wants that.",
            )
        result = offline_model.connect_folder(str(path), allow_open_in_desktop=allow_open)
        if not result.get("success"):
            return result
        # N-09: the offline lane replaces any XMLA target; a stale read-only flag would refuse
        # offline writes with a misleading "published model" error.
        try:
            from core.infrastructure import xmla_target

            xmla_target.deactivate()
        except Exception as e:  # noqa: BLE001
            logger.debug("xmla deactivate before offline lane failed: %s", e)
        # Drop the live link so no DAX / TOM read can silently hit a Desktop model.
        cm = connection_state.connection_manager
        if cm is not None:
            try:
                cm.disconnect()
            except Exception as e:  # noqa: BLE001
                logger.debug("disconnect before offline lane failed: %s", e)
        connection_state.cleanup()
        try:
            connection_state._build_managers(None, force_reinit=True)
        except Exception as e:  # noqa: BLE001 - TOM managers are best effort here
            logger.debug("offline manager build: %s", e)
        connection_state.query_executor = None  # no engine offline
        return result
    except Exception as e:
        logger.error(f"Error in connect_folder: {e}", exc_info=True)
        return ErrorHandler.handle_unexpected_error("connect_folder", e)


def _leave_offline() -> None:
    try:
        from core.infrastructure import offline_model

        if offline_model.is_active():
            offline_model.disconnect()
            connection_state.cleanup()
    except Exception as e:  # noqa: BLE001
        logger.debug("leaving offline lane: %s", e)


def handle_connection(args: Dict[str, Any]) -> Dict[str, Any]:
    """Unified connection handler"""
    operation = args.get("operation", "detect")
    if operation == "connect_xmla":
        return handle_connect_xmla(args)
    if operation == "connect_folder":
        return handle_connect_folder(args)
    if operation in ("connect", "ensure", "reconnect"):
        _leave_offline()
    if operation == "detect":
        return handle_detect_powerbi_desktop(args)
    elif operation == "connect":
        return handle_connect_to_powerbi(args)
    elif operation == "reconnect":
        return handle_reconnect(args)
    elif operation == "ensure":
        return handle_ensure(args)
    elif operation == "status":
        return handle_status(args)
    else:
        return {
            "success": False,
            "error": f"Unknown operation: {operation}. Valid: detect, connect, reconnect, ensure, status, connect_xmla, connect_folder",
            "error_type": "unknown_operation",
        }


def register_connection_handlers(registry):
    """Register connection handlers"""
    tool = ToolDefinition(
        name="01_Connection",
        description=(
            "Connect to Power BI Desktop. Ops: detect (running instances, newest first, with "
            "file_path_confidence + AMO build), connect (model_index), reconnect (after a "
            "port change / restart; never restarts Desktop), ensure (idempotent: skips work "
            "when a round-trip probe succeeds), status (identity + AMO/ADOMD versions; "
            "probe=true adds a health round-trip), connect_xmla (published model, read-only), "
            "connect_folder (offline TMDL)."
        ),
        guide_notes=(
            "Detect running Power BI Desktop instances (detect — newest first, with "
            "file_path_confidence and the AMO build in `amo`), connect to one "
            "(connect), reacquire an in-place connection after a port change / "
            "PBI restart (reconnect — rebuilds only the connection objects, "
            "never restarts Desktop), idempotently ensure a healthy "
            "connection (ensure — skips work when a server round-trip probe "
            "succeeds), or report connection identity + loaded AMO/ADOMD "
            "versions (status; probe=true adds a health round-trip; also reports "
            "the target desktop / xmla / offline_folder and the tool profile). "
            "connect_xmla (workspace, model): READ-ONLY connection to a PUBLISHED "
            "semantic model over the XMLA endpoint - DAX, query plans, VertiPaq, BPA "
            "and dependency tools run against the Service model; every write is refused; needs a "
            "token (MCP_PBI_XMLA_TOKEN or the Fabric MCP's MSAL cache). "
            "connect_folder (pbip_path): OFFLINE lane - the PBIP model's TMDL folder "
            "is loaded with the TOM serializer and every 02/03 model write is "
            "serialised back (validated, backup + rollback); refuses while Desktop "
            "has the project open; no engine (no DAX / refresh / trace). "
            "connect / ensure return to Desktop."
        ),
        handler=handle_connection,
        input_schema={
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": [
                        "detect",
                        "connect",
                        "reconnect",
                        "ensure",
                        "status",
                        "connect_xmla",
                        "connect_folder",
                    ],
                    "default": "detect",
                },
                "model_index": {
                    "type": "integer",
                    "description": "Index of the model to connect to (default 0 = most recently started; connect/ensure fallback)",
                },
                "prefer_pid": {
                    "type": "integer",
                    "description": "Prefer the Power BI Desktop instance with this PID (ensure)",
                },
                "file_path": {
                    "type": "string",
                    "description": "Prefer the instance whose open file matches this path (ensure)",
                },
                "probe": {
                    "type": "boolean",
                    "description": "status: also run a server round-trip health probe",
                },
                "workspace": {"type": "string", "description": "connect_xmla: workspace name"},
                "model": {"type": "string", "description": "connect_xmla: semantic model name"},
                "pbip_path": {
                    "type": "string",
                    "description": "connect_folder: .pbip, .SemanticModel folder or its definition/ folder",
                },
                "allow_open_in_desktop": {
                    "type": "boolean",
                    "description": "connect_folder: proceed although Desktop has the project open (unsafe; also needs confirm_destructive=true)",
                },
                "confirm_destructive": {
                    "type": "boolean",
                    "description": "connect_folder: required together with allow_open_in_desktop",
                },
            },
            "required": [],
        },
        category="core",
        sort_order=10,
        annotations={
            # WP2 (L-08): connecting is not a write (read_only_mode must allow it).
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,  # ensure is idempotent; rest are too in practice
            "openWorldHint": True,
        },
    )
    registry.register(tool)
    logger.info("Registered connection handler")
