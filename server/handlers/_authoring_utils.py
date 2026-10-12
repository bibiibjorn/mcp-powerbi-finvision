"""Shared helpers for authoring-related MCP handlers.

Provides gate_and_wrap() — applies autonomous gating, dispatches the op,
and attaches a reload hint — used consistently across all authoring handlers.

P1-120 / P0-50..54 retrofit: every write-capable handler should route its
dispatch through this helper. Per-tool read-only and catastrophic op sets
can be passed in so a tool's semantics don't leak across handlers.

Deep review 2026-09-22 (WP9) — every WRITE op now also runs inside:

* ``file_lock(<report definition>)`` so two parallel writes to one report
  are serialised end-to-end (H-01; each file RMW is additionally locked by
  ``safe_io.update_json``);
* a ``safe_io.write_scope``: ``dry_run=true`` is honoured for EVERY write op
  of the tool (A-06) — nothing is written, the planned changes come back
  as ``would_change``; writes outside the report folder are refused;
  every file really written is counted (``files_modified``) so a PARTIAL
  write still carries the reload hint (H-29);
* an ambiguity check: a folder holding several ``.Report`` folders is an
  ``ambiguous_report`` error listing the candidates instead of silently
  editing the first one (H-33);
* a cache-only Desktop-state peek (H-40): when a recent
  ``17_Desktop_Bridge`` status shows the report open with unsaved changes,
  the result carries ``desktop_state``; under STRICT the write is blocked
  unless ``confirm_destructive`` is passed (save first with
  ``17_Desktop_Bridge operation=save`` -- a ``save_first`` flag here would
  only have bypassed the check without saving anything).
"""

from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from core.autonomous.reload_hints import attach_reload_hint
from core.autonomous.gating import require_autonomous_mode, Severity, merge_gate_info


def coerce_num(value: Any, key: str, default: float) -> float:
    """Coerce an MCP-arg value to float with a clear error when it fails.

    Used at handler boundaries so a caller passing a string like "100" for a
    numeric position field doesn't end up writing the string literal into the
    visual.json. Returns `default` for None.
    """
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{key} must be numeric, got {value!r}") from e


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def report_candidates(pbip_path: Any) -> List[str]:
    """``.Report`` folders (with a ``definition/``) directly inside a plain folder."""
    try:
        from core.utilities.pbip_utils import normalize_path

        p = Path(normalize_path(str(pbip_path)))
    except Exception:
        return []
    if not p.is_dir() or p.name.endswith(".Report") or p.name == "definition":
        return []
    if (p / "definition").is_dir():
        return []
    out: List[str] = []
    try:
        for child in sorted(p.iterdir()):
            if (
                child.is_dir()
                and child.name.endswith(".Report")
                and (child / "definition").is_dir()
            ):
                out.append(str(child))
    except OSError:
        return []
    return out


def ambiguous_report_error(pbip_path: Any) -> Optional[Dict[str, Any]]:
    """H-33: error envelope when *pbip_path* is a folder with several reports."""
    cands = report_candidates(pbip_path)
    if len(cands) <= 1:
        return None
    return {
        "success": False,
        "error_type": "ambiguous_report",
        "error": (
            f"{pbip_path} contains {len(cands)} .Report folders; pass the .pbip file or "
            "the .Report folder of the report to edit."
        ),
        "candidates": cands,
    }


def _definition_dir(pbip_path_or_defn: Any) -> Optional[Path]:
    try:
        from core.utilities.pbip_utils import find_definition_folder

        p = Path(str(pbip_path_or_defn))
        if p.is_dir() and p.name == "definition":
            return p
        return find_definition_folder(str(pbip_path_or_defn))
    except Exception:
        return None


def _desktop_state(report_dir: Optional[Path]) -> Optional[Dict[str, Any]]:
    """Cache-only peek at the last 17_Desktop_Bridge status (never spawns the CLI).

    Returns ``{open, pid, has_unsaved_changes, source}`` when a fresh cached
    status lists an instance whose ``currentFilePath`` is this report's
    ``.pbip`` (or lives in the report folder), else None.
    """
    if report_dir is None:
        return None
    try:
        import time

        from core.autonomous import desktop_bridge as db

        with db._STATUS_CACHE_LOCK:
            cached = db._STATUS_CACHE.get("result")
            fresh = cached is not None and (
                time.monotonic() - db._STATUS_CACHE["t"] < db.STATUS_CACHE_TTL
            )
        if not fresh:
            return None
        stem = report_dir.name[: -len(".Report")] if report_dir.name.endswith(".Report") else None
        target_pbip = (report_dir.parent / f"{stem}.pbip") if stem else None
        for inst in cached.get("instances") or []:
            cfp = inst.get("currentFilePath")
            if not cfp:
                continue
            cp = Path(str(cfp))
            same = False
            try:
                if target_pbip is not None and cp.resolve() == target_pbip.resolve():
                    same = True
                else:
                    cp.resolve().relative_to(report_dir.resolve())
                    same = True
            except (ValueError, OSError):
                same = False
            if same:
                return {
                    "open": True,
                    "pid": inst.get("pid"),
                    "has_unsaved_changes": inst.get("hasUnsavedChanges"),
                    "source": "cached 17_Desktop_Bridge status",
                }
    except Exception:
        return None
    return None


def _attach_hint_for_written(result: Dict[str, Any], tool_name: str, operation: str) -> None:
    """Reload hint for a result that wrote files (also for partial writes / new ops)."""
    from core.autonomous import reload_hints as rh

    result["reload_required"] = True
    result["reload_hint"] = rh._HINT
    result["reload_op_chain"] = list(rh._CHAIN)


def gate_and_wrap(
    args: Dict[str, Any],
    operation: str,
    tool_name: str,
    dispatch_fn: Callable,
    pbip_path_or_defn: Any,
    *,
    read_only_ops: Iterable[str],
    catastrophic_ops: Iterable[str],
    skip_reload_hint_on_error: bool = True,
    severity_fn: Optional[Callable[[Dict[str, Any], Any], Optional[Severity]]] = None,
) -> Dict[str, Any]:
    """Apply gating, dispatch, attach reload hint.

    Args:
        args: raw MCP args dict.
        operation: operation name string.
        tool_name: MCP tool name for reload hint (e.g. "07_Visual_Operations").
        dispatch_fn: callable(args, pbip_path_or_defn) -> dict.
        pbip_path_or_defn: resolved path passed to dispatch_fn.
        read_only_ops: iterable of op names that are read-only for THIS tool
            (required — P2-191: cross-tool leakage risk prevents a default).
        catastrophic_ops: iterable of op names that escalate severity for
            THIS tool (required).
        skip_reload_hint_on_error: when True (default), operations that
            returned success=False AND wrote nothing do not get a
            reload_required hint (P1-135). A failed op that DID write files
            (partial bulk write) keeps the hint (H-29).
        severity_fn: optional ``(args, path) -> Severity | None`` override
            (e.g. a delete is CATASTROPHIC only when it removes several
            visuals — H-25).
    """
    from core.pbip.authoring import safe_io
    from core.utilities.pbip_utils import file_lock

    ro_set = set(read_only_ops)
    cat_set = set(catastrophic_ops)

    amb = ambiguous_report_error(pbip_path_or_defn)
    if amb is not None:
        return amb

    if operation in ro_set:
        return dispatch_fn(args, pbip_path_or_defn)

    dry_run = _truthy(args.get("dry_run", False))
    gate = None
    if not dry_run:
        severity = Severity.CATASTROPHIC if operation in cat_set else Severity.DESTRUCTIVE
        if severity_fn is not None:
            try:
                override = severity_fn(args, pbip_path_or_defn)
            except Exception:
                override = None
            if override is not None:
                severity = override
        gate = require_autonomous_mode(f"{tool_name}:{operation}", args, severity)
        if gate is not None and gate.get("success") is False:
            # Gate failure: never attach reload hint — the op was blocked before
            # touching disk.
            return gate

    definition_dir = _definition_dir(pbip_path_or_defn)
    report_dir = definition_dir.parent if definition_dir is not None else None
    desktop_state = None if dry_run else _desktop_state(report_dir)
    if desktop_state and desktop_state.get("has_unsaved_changes"):
        from core.autonomous.gating import current_policy

        if current_policy() == "STRICT" and not _truthy(args.get("confirm_destructive")):
            return {
                "success": False,
                "error_type": "desktop_unsaved_changes",
                "error": (
                    "Power BI Desktop has this report open with UNSAVED changes. A disk "
                    "edit followed by a reload discards them, and a later Desktop save "
                    "overwrites this edit. Save first (17_Desktop_Bridge operation=save), "
                    "or pass confirm_destructive=true to write anyway."
                ),
                "desktop_state": desktop_state,
            }

    lock_target = str(definition_dir) if definition_dir is not None else str(pbip_path_or_defn)
    roots = [report_dir] if report_dir is not None else None
    with file_lock(lock_target), safe_io.write_scope(dry_run=dry_run, allowed_roots=roots) as scope:
        try:
            result = dispatch_fn(args, pbip_path_or_defn)
        except safe_io.WriteError as e:
            result = {
                "success": False,
                "error_type": "write_failed",
                "error": str(e),
                "path": e.path,
            }
        written = list(scope.written)
        planned = list(scope.planned)

    if not isinstance(result, dict):
        return result
    merge_gate_info(result, gate)
    if desktop_state:
        result.setdefault("desktop_state", desktop_state)

    if dry_run:
        result["dry_run"] = True
        result.pop("reload_required", None)
        if planned or "would_change" not in result:
            result["would_change"] = planned[: safe_io.MAX_PLANNED_CHANGES_REPORTED]
            result["would_change_count"] = len(planned)
        result["files_modified"] = 0
        return result

    result.setdefault("files_modified", len(written))
    is_error = result.get("success") is False or result.get("ok") is False
    if is_error:
        if written:
            # H-29: N-1 files changed before the failure — the caller must
            # still reload (and should inspect what landed).
            result["partial_write"] = True
            result["files_written"] = written[:50]
            _attach_hint_for_written(result, tool_name, operation)
            return result
        if skip_reload_hint_on_error:
            return result

    hinted = attach_reload_hint(result, tool_name, operation)
    if isinstance(hinted, dict):
        result = hinted
    if written and not result.get("reload_required"):
        _attach_hint_for_written(result, tool_name, operation)
    return result
