"""
17_Desktop_Bridge — MCP tool wrapping the MS ``powerbi-desktop`` CLI.

Ops:
  status                — list running Desktop instances + their pages; each
                          instance carries ``dirty_sources`` (intent flag,
                          live-write ledger, pending disk edits, verdict)
  reload                — in-place reload of a running Desktop (report/PBIR
                          changes). REFUSES on unsaved in-memory work
                          (``unsaved_changes`` / ``dirty_state_unknown``) unless
                          ``save_first`` or ``discard_unsaved``.
                          ``include_model=true`` also re-applies the on-disk TMDL,
                          but only when a classifier proves every pending change is
                          metadata-only (else ``model_reload_unsafe_change``)
  save                  — flush in-memory model + report to disk (UI automation;
                          the MS CLI has no save subcommand), verified ON DISK.
                          REFUSES while this server has disk edits Desktop has
                          not reloaded (``conflict_pending_disk_and_memory``)
  screenshot            — capture one page (by id or display name) to PNG
  screenshot_all        — capture every page to a directory
  reload_and_screenshot — composite: reload + settle + screenshot in ONE call
                          (the standard verify step after a PBIR disk edit;
                          takes ``include_model`` like reload)
  resolve_page          — display-name -> PBIR page id helper
  manifest              — capability detection: what RPC surface the connected
                          Desktop build actually exposes (probe, don't assume)
  open                  — launch Desktop on a .pbip/.pbix (the reopen half of
                          "close WITHOUT saving -> reopen" after TMDL disk edits)

Rule the refusals enforce: save BEFORE disk edits; after disk edits reload,
never save first.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional, Tuple

from core.autonomous import desktop_bridge
from server.progress import emit_progress as _progress
from server.registry import ToolDefinition
from server.tool_schemas import REQUEST_ID_PROPERTY

logger = logging.getLogger(__name__)

_VALID_OPS = {
    "status",
    "reload",
    "save",
    "screenshot",
    "screenshot_all",
    "reload_and_screenshot",
    "resolve_page",
    "manifest",
    "open",
}

_VISION_TIERS = ("high", "standard")


class _ArgError(Exception):
    def __init__(self, envelope: Dict[str, Any]) -> None:
        super().__init__(envelope.get("error"))
        self.envelope = envelope


def _bad(name: str, msg: str) -> _ArgError:
    return _ArgError(
        {"success": False, "error_type": "invalid_argument", "error": f"{name}: {msg}"}
    )


def _coerce_pid(args: Dict[str, Any]) -> Optional[int]:
    """None when absent; an invalid pid is an ERROR (J-31) — never silently dropped,
    which used to fall back to "the single running instance"."""
    raw = args.get("pid")
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        raise _bad("pid", f"must be an integer process id, got {raw!r}")
    try:
        val = int(raw)
    except (TypeError, ValueError):
        raise _bad("pid", f"must be an integer process id, got {raw!r}")
    if isinstance(raw, float) and not raw.is_integer():
        raise _bad("pid", f"must be an integer process id, got {raw!r}")
    if val <= 0:
        raise _bad("pid", f"must be a positive process id, got {raw!r}")
    return val


def _coerce_float(args: Dict[str, Any], key: str, default: Optional[float]) -> Optional[float]:
    """``null`` / absent -> default (J-31: ``float(None)`` used to crash)."""
    raw = args.get(key)
    if raw is None or raw == "":
        return default
    try:
        val = float(raw)
    except (TypeError, ValueError):
        raise _bad(key, f"must be a number, got {raw!r}")
    if val != val or val < 0:
        raise _bad(key, f"must be a non-negative number, got {raw!r}")
    return val


def _coerce_bool(value: Any, default: bool) -> bool:
    """MCP clients sometimes send booleans as strings — "false" must not
    coerce to True via bool()."""
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "")
    return bool(value)


def _coerce_scale(args: Dict[str, Any]) -> Any:
    """None when absent; the raw value when it isn't numeric.

    A non-numeric value is passed through UNCHANGED so the core's validator
    produces the `invalid_argument` error naming the accepted range — coercing
    it to None here would silently ignore what the caller asked for.
    """
    raw = args.get("scale")
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return raw


def _coerce_settle_ms(args: Dict[str, Any], default: Any = None) -> Any:
    """Same contract as `_coerce_scale`: pass junk through to the validator."""
    raw = args.get("settle_ms")
    if raw is None or raw == "":
        return default
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return raw


def _vision_tier(args: Dict[str, Any]) -> str:
    raw = args.get("vision_tier")
    if raw is None or raw == "":
        return "high"
    val = str(raw).strip().lower()
    if val not in _VISION_TIERS:
        raise _bad("vision_tier", f"must be one of {list(_VISION_TIERS)}, got {raw!r}")
    return val


def _page_arg(args: Dict[str, Any]) -> Optional[str]:
    # L-11: canonical page_name; page / page_id are legacy aliases.
    return args.get("page_name") or args.get("page") or args.get("page_id")


def _deadline() -> Tuple[float, float]:
    """Monotonic deadline a little inside the dispatcher's 17_Desktop_Bridge timeout (J-13)."""
    try:
        from core.config.tool_timeouts import ToolTimeoutManager

        budget = float(ToolTimeoutManager().get_timeout("17_Desktop_Bridge", default=120))
    except Exception:  # noqa: BLE001
        budget = 120.0
    budget = max(10.0, budget - 5.0)
    return time.monotonic() + budget, budget


def handle_desktop_bridge(args: Dict[str, Any]) -> Dict[str, Any]:
    op = args.get("operation")
    if op not in _VALID_OPS:
        return {
            "success": False,
            "error_type": "invalid_operation",
            "error": f"operation must be one of {sorted(_VALID_OPS)}",
            "got": op,
        }

    try:
        pid = _coerce_pid(args)
        pbip_path = args.get("pbip_path")
        # None = "not given": the status pre-flight uses 30 s and the CLI keeps
        # its own --wait-seconds default unless the caller set one (J-31).
        wait_seconds = _coerce_float(args, "wait_seconds", None)
        timeout = _coerce_float(args, "timeout", desktop_bridge.DEFAULT_TIMEOUT)
        vision_tier = _vision_tier(args)
    except _ArgError as e:
        return e.envelope
    deadline, _budget = _deadline()

    try:
        if op == "status":
            return desktop_bridge.op_status(
                wait_seconds=(
                    wait_seconds if wait_seconds is not None else desktop_bridge.DEFAULT_STATUS_WAIT
                ),
                timeout=timeout,
                pid=pid,
                include_dirty_sources=_coerce_bool(args.get("dirty_sources"), True),
            )
        if op == "reload":
            return desktop_bridge.op_reload(
                pid=pid,
                pbip_path=pbip_path,
                wait_seconds=wait_seconds,
                timeout=timeout,
                save_first=_coerce_bool(args.get("save_first"), False),
                discard_unsaved=_coerce_bool(args.get("discard_unsaved"), False),
                overwrite_disk_edits=_coerce_bool(args.get("overwrite_disk_edits"), False),
                include_model=_coerce_bool(args.get("include_model"), False),
                deadline=deadline,
            )
        if op == "save":
            return desktop_bridge.op_save(
                pid=pid,
                pbip_path=pbip_path,
                wait_seconds=wait_seconds,
                save_timeout=_coerce_float(args, "save_timeout", 30.0),
                overwrite_disk_edits=_coerce_bool(args.get("overwrite_disk_edits"), False),
                deadline=deadline,
            )
        if op == "screenshot":
            page = _page_arg(args)
            output = args.get("output")
            if not page or not output:
                return {
                    "success": False,
                    "error_type": "missing_argument",
                    "error": "screenshot requires `page` (id or display name) and `output`.",
                }
            return desktop_bridge.op_screenshot(
                page=page,
                output=output,
                pid=pid,
                pbip_path=pbip_path,
                wait_seconds=wait_seconds,
                timeout=timeout,
                optimize=_coerce_bool(args.get("optimize"), True),
                per_visual=_coerce_bool(args.get("per_visual"), False),
                report_root=args.get("report_root"),
                crops_dir=args.get("crops_dir"),
                scale=_coerce_scale(args),
                settle_ms=_coerce_settle_ms(args),
                render_stable=_coerce_bool(args.get("render_stable"), False),
                vision_tier=vision_tier,
                deadline=deadline,
            )
        if op == "screenshot_all":
            output_dir = args.get("output_dir")
            if not output_dir:
                return {
                    "success": False,
                    "error_type": "missing_argument",
                    "error": "screenshot_all requires `output_dir`.",
                }
            # F-26: the CLI captures every page in ONE call, so only the bracket is reported
            # (per-page progress needs desktop_bridge.op_screenshot_all to loop per page).
            _progress(0, 1, "Capturing every page (screenshot-all)")
            shot_all = desktop_bridge.op_screenshot_all(
                output_dir=output_dir,
                pid=pid,
                pbip_path=pbip_path,
                wait_seconds=wait_seconds,
                timeout=timeout,
                optimize=_coerce_bool(args.get("optimize"), True),
                scale=_coerce_scale(args),
                settle_ms=_coerce_settle_ms(args),
                vision_tier=vision_tier,
                deadline=deadline,
            )
            _progress(1, 1, "screenshot-all finished")
            return shot_all
        if op == "reload_and_screenshot":
            page = _page_arg(args)
            output = args.get("output")
            if not page or not output:
                return {
                    "success": False,
                    "error_type": "missing_argument",
                    "error": (
                        "reload_and_screenshot requires `page` (id or display name) "
                        "and `output`."
                    ),
                }
            return desktop_bridge.op_reload_and_screenshot(
                page=page,
                output=output,
                pid=pid,
                pbip_path=pbip_path,
                wait_seconds=wait_seconds,
                timeout=timeout,
                optimize=_coerce_bool(args.get("optimize"), True),
                per_visual=_coerce_bool(args.get("per_visual"), False),
                report_root=args.get("report_root"),
                crops_dir=args.get("crops_dir"),
                save_first=_coerce_bool(args.get("save_first"), False),
                scale=_coerce_scale(args),
                settle_ms=_coerce_settle_ms(args, desktop_bridge.DEFAULT_RELOAD_SETTLE_MS),
                render_stable=_coerce_bool(args.get("render_stable"), False),
                discard_unsaved=_coerce_bool(args.get("discard_unsaved"), False),
                overwrite_disk_edits=_coerce_bool(args.get("overwrite_disk_edits"), False),
                include_model=_coerce_bool(args.get("include_model"), False),
                vision_tier=vision_tier,
                deadline=deadline,
            )
        if op == "manifest":
            return desktop_bridge.op_manifest(
                pid=pid,
                pbip_path=pbip_path,
                # The CLI's own manifest default is 0 (fail fast); don't inherit
                # the 30s status default here.
                wait_seconds=wait_seconds if wait_seconds is not None else 0.0,
                timeout=timeout,
            )
        if op == "resolve_page":
            page = args.get("page") or args.get("page_name") or args.get("page_id")
            if not page:
                return {
                    "success": False,
                    "error_type": "missing_argument",
                    "error": "resolve_page requires `page`.",
                }
            return desktop_bridge.op_resolve_page(
                page=page, pid=pid, pbip_path=pbip_path, wait_seconds=wait_seconds
            )
        if op == "open":
            if not pbip_path:
                return {
                    "success": False,
                    "error_type": "missing_argument",
                    "error": "open requires `pbip_path` (a .pbip or .pbix file).",
                }
            try:
                open_timeout = _coerce_float(args, "timeout_seconds", 60.0)
            except _ArgError as e:
                return e.envelope
            return desktop_bridge.op_open(
                pbip_path=pbip_path,
                timeout_seconds=open_timeout if open_timeout is not None else 60.0,
                allow_duplicate=_coerce_bool(args.get("allow_duplicate"), False),
            )
    except Exception as e:  # pragma: no cover - defensive boundary
        logger.error("desktop_bridge op %s crashed: %s", op, e, exc_info=True)
        return {"success": False, "error_type": "handler_error", "error": str(e)}

    return {"success": False, "error_type": "invalid_operation", "error": op}


def register_desktop_bridge_handler(registry) -> None:
    registry.register(
        ToolDefinition(
            name="17_Desktop_Bridge",
            description=(
                "Live Power BI Desktop bridge: reload a RUNNING Desktop in place, save it, "
                "screenshot pages (no close/reopen). After PBIR disk edits call "
                "reload_and_screenshot ONCE per batch. Rule: save BEFORE disk edits; after "
                "disk edits reload, never save first. reload REFUSES on unsaved in-memory "
                "work (unsaved_changes / dirty_state_unknown: use save_first or "
                "discard_unsaved); save REFUSES while disk edits are pending "
                "(conflict_pending_disk_and_memory: overwrite_disk_edits). Plain reload cannot "
                "apply TMDL: include_model=true does for metadata-only edits, else close "
                "Desktop without saving (ask the user), then open. status shows "
                "dirty_sources; manifest probes the RPC surface (Desktop 2.155.756+)."
            ),
            handler=handle_desktop_bridge,
            input_schema={
                "type": "object",
                "properties": {
                    "operation": {"type": "string", "enum": sorted(_VALID_OPS)},
                    # F-27: idempotency key (deferred in tools/list)
                    "request_id": dict(REQUEST_ID_PROPERTY),
                    "pid": {"type": "integer"},
                    "pbip_path": {
                        "type": "string",
                        "description": "Target project (.pbip, its folder, .Report or "
                        ".SemanticModel); the instance must have it open. open: file to launch.",
                    },
                    "page_name": {"type": "string", "description": "Page id or display name."},
                    "output": {"type": "string"},
                    "output_dir": {"type": "string"},
                    "optimize": {
                        "type": "boolean",
                        "description": "Resize PNGs to the vision tier (default true).",
                    },
                    "vision_tier": {"type": "string", "enum": list(_VISION_TIERS)},
                    "per_visual": {
                        "type": "boolean",
                        "description": "screenshot: also one crop per visual.",
                    },
                    "report_root": {"type": "string"},
                    "crops_dir": {"type": "string"},
                    "scale": {"type": "number", "minimum": 1, "maximum": 3},
                    "settle_ms": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Wait before capture (reload_and_screenshot default 2000).",
                    },
                    "render_stable": {
                        "type": "boolean",
                        "description": "Re-capture until two captures match.",
                    },
                    "save_first": {"type": "boolean"},
                    "discard_unsaved": {
                        "type": "boolean",
                        "description": "reload: accept discarding unsaved in-memory edits.",
                    },
                    "overwrite_disk_edits": {
                        "type": "boolean",
                        "description": "save: accept overwriting pending disk edits.",
                    },
                    "include_model": {
                        "type": "boolean",
                        "description": "reload / reload_and_screenshot: also re-apply the on-disk "
                        "TMDL (metadata-only changes; the same unsaved gate applies).",
                    },
                    "dirty_sources": {"type": "boolean"},
                    "allow_duplicate": {"type": "boolean"},
                    "timeout_seconds": {"type": "number"},
                    "wait_seconds": {"type": "number"},
                    "timeout": {"type": "number"},
                    "save_timeout": {"type": "number"},
                },
                "required": ["operation"],
            },
            category="debug",
            sort_order=1700,
            # WP2 (A-10/L-08): reload re-reads disk and DISCARDS unsaved
            # in-memory edits; save overwrites on-disk PBIR edits.
            annotations={
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
                "openWorldHint": True,
            },
        )
    )


__all__ = ["handle_desktop_bridge", "register_desktop_bridge_handler"]
