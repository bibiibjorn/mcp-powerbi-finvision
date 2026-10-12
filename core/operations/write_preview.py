"""One uniform ``preview=true`` for live-TOM writes (F-01).

``02_Model_Operations`` (every create / update / delete / set_* / refresh) and ``03_Batch_Operations``
accept ``preview=true`` (alias ``dry_run=true``) and answer WITHOUT saving::

    {"success": true, "preview": true, "applied": false, "would_succeed": true,
     "would_change": [{ref, kind, change, before, after, properties...}],
     "errors": [...], "breaks": [{object, breaks:[...]}], "plan_hash": "...", ...}

How: the REAL manager code runs against the real model inside :func:`run_preview`. While the
:func:`core.operations.write_lane.preview_scope` is open ``write_lane.commit`` is suppressed
(``SaveChanges`` is never called, on the live and on the offline lane); the commit captures the
"after" TMSL image and the committed model supplies the "before" image
(:mod:`core.operations.journal_images` diffs them: added / removed / renamed / modified per object).
The in-memory edits are then rolled back with ``write_lane.safe_undo`` (live: ``UndoLocalChanges``;
offline: the folder is re-read). Nothing is persisted, the model generation does not move.

``plan_hash`` binds the preview to the model state and the arguments: an apply that passes it back
(``plan_hash=<hash>``) is refused with ``stale_preview`` when the model generation moved (any write,
a swap, a Desktop-side edit) or the arguments differ - re-run the preview.

Limits (reported, never hidden): the preview cannot evaluate DAX, so an expression's validity is
``not_validated`` (the engine binds expressions at save); ``breaks`` needs the live dependency graph
(``core.autonomous.evidence``) and is ``unavailable`` on the offline lane / without a PBIP path.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Callable, Dict, List, Optional

from core.operations import journal_images as images
from core.operations import write_lane
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)

__all__ = [
    "PLAN_HASH_IGNORED_ARGS",
    "check_plan_hash",
    "plan_hash",
    "refresh_plan",
    "run_preview",
]

#: Arguments that do not change WHAT a call does (a preview and its apply differ in them).
PLAN_HASH_IGNORED_ARGS = frozenset(
    {
        "preview",
        "dry_run",
        "plan_hash",
        "request_id",
        "response_format",
        "page_size",
        "next_token",
        "confirm_destructive",
        "evidence",
    }
)
#: Result keys a suppressed commit / post-commit read-back leaves that mean nothing in a preview.
_JUNK_RESULT_KEYS = frozenset(
    {
        "preview_commit_suppressed",
        "expression_changed_on_store",
        "post_write_errors",
        "post_write_health",
        "post_write_warning",
        "writes_last_60s",
        "paced_ms",
        "journal_warning",
        "committed",
        "applied",
        "rolled_back",
        "rollback_failed",
    }
)
_MAX_CHANGES = 100
_MAX_IMAGE_CHARS = 12_000


# ---------------------------------------------------------------------------
# plan hash
# ---------------------------------------------------------------------------
def _generation() -> Optional[int]:
    try:
        from core.infrastructure.connection_state import connection_state

        return int(connection_state.model_generation)
    except Exception:  # noqa: BLE001
        return None


def plan_hash(tool: str, args: Dict[str, Any], generation: Optional[int] = None) -> str:
    """Hash of (tool, effective arguments, model generation)."""
    body = {k: v for k, v in args.items() if k not in PLAN_HASH_IGNORED_ARGS}
    options = body.get("options")
    if isinstance(options, dict):  # 03: options.dry_run is the validate-only switch, not the plan
        body["options"] = {k: v for k, v in options.items() if k not in PLAN_HASH_IGNORED_ARGS}
    gen = _generation() if generation is None else generation
    blob = json.dumps({"tool": tool, "args": body, "generation": gen}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()[:20]


def check_plan_hash(tool: str, args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """``stale_preview`` refusal when ``args['plan_hash']`` no longer matches, else None."""
    supplied = args.get("plan_hash")
    if not supplied:
        return None
    current = plan_hash(tool, args)
    if str(supplied) == current:
        return None
    return error_envelope(
        "stale_preview",
        "plan_hash does not match: the model changed (a write, a swap or a Desktop-side edit "
        "moved its generation) or the arguments differ from the previewed call. Nothing was "
        "written; re-run the call with preview=true and apply the new plan_hash.",
        executed=False,
        supplied_plan_hash=str(supplied),
        current_plan_hash=current,
    )


# ---------------------------------------------------------------------------
# images
# ---------------------------------------------------------------------------
def tmsl_of(model: Any) -> Dict[str, Any]:
    """TMSL of *model* (``Model``): through its ``Database`` when it has one (a TMDL-loaded model
    that uses a newer property cannot be serialised as a detached ``Model``), else the journal's."""
    db = getattr(model, "Database", None)
    if db is not None:
        try:
            from core.comparison.compare_targets import _serialize_model

            return _serialize_model(db)
        except Exception as exc:  # noqa: BLE001 - fall through to the journal serialiser
            logger.debug("preview: database serialisation failed (%s)", exc)
    from core.operations import write_journal

    return write_journal.serialize_model(model)


def committed_tmsl(model: Any) -> Dict[str, Any]:
    """TMSL of the COMMITTED model over a fresh connection (the preview never saved anything)."""
    from core.infrastructure.connection_state import connection_state
    from core.operations.amo_helpers import get_server_db_model, safe_disconnect

    server, _db, fresh, err = get_server_db_model(connection_state)
    if server is None or fresh is None:
        raise RuntimeError(f"cannot read the committed model: {err or 'no connection'}")
    try:
        return tmsl_of(fresh)
    finally:
        safe_disconnect(server)


def _offline_before() -> Optional[Dict[str, Dict[str, Any]]]:
    """Offline lane: the folder-loaded model BEFORE the manager touches it."""
    try:
        from core.infrastructure import offline_model

        if not offline_model.is_active():
            return None
        db = offline_model.database()
        return images.split_model(tmsl_of(db.Model)) if db is not None else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("preview: offline before-image failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# dependents that would break
# ---------------------------------------------------------------------------
def _removed_refs(changes: List[Dict[str, Any]]) -> List[str]:
    removed_tables = {
        c["name"] for c in changes if c["kind"] == "table" and c["change"] == "removed"
    }
    refs: List[str] = []
    for c in changes:
        if c["change"] != "removed":
            continue
        if c["kind"] == "table":
            refs.append(f"table:{c['name']}")
        elif c["kind"] in ("column", "measure") and c.get("table") not in removed_tables:
            refs.append(f"{c['kind']}:{c['table']}[{c['name']}]")
    return refs


def _breaks(changes: List[Dict[str, Any]], args: Dict[str, Any]) -> Dict[str, Any]:
    """``{breaks, breaks_status}``: report / model objects that depend on what would be removed."""
    refs = _removed_refs(changes)
    if not refs:
        return {"breaks": [], "breaks_status": "nothing_removed"}
    try:
        from core.infrastructure import offline_model

        if offline_model.is_active():
            return {
                "breaks": [],
                "breaks_status": "unavailable_offline",
                "breaks_note": "the dependency graph needs a live model; run the PBIP-side "
                "16_Dependency_Graph / 07_PBIP_Operations query_dependencies before applying.",
            }
        from core.autonomous import evidence as _ev

        ev = _ev.compute_delete_evidence(refs, pbip_path=args.get("pbip_path"))
    except Exception as exc:  # noqa: BLE001 - advisory
        logger.debug("preview: breaks lookup failed: %s", exc)
        ev = None
    if ev is None:
        return {
            "breaks": [],
            "breaks_status": "unavailable",
            "breaks_note": "the dependency graph could not be built (not connected, no PBIP path "
            "or the report side is missing); treat the removal as unchecked.",
            "removed": refs,
        }
    impacts = (ev.get("blast_radius") or {}).get("impacts") or {}
    out = [
        {
            "object": ref,
            "breaks": imp.get("breaks") or [],
            "unknown_nodes": imp.get("unknown_nodes") or [],
        }
        for ref, imp in impacts.items()
        if imp.get("breaks") or imp.get("unknown_nodes") or not imp.get("safe", True)
    ]
    return {
        "breaks": out,
        "breaks_status": "checked" if ev.get("proven") else "checked_not_proven",
        **({"breaks_unproven_reasons": ev.get("unproven_reasons")} if not ev.get("proven") else {}),
    }


# ---------------------------------------------------------------------------
# result shaping
# ---------------------------------------------------------------------------
def _cap_image(value: Any) -> Any:
    if value is None:
        return None
    text = json.dumps(value, default=str)
    if len(text) <= _MAX_IMAGE_CHARS:
        return value
    return {"truncated": True, "chars": len(text)}


def _would_change(changes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for ch in changes[:_MAX_CHANGES]:
        row = images.describe_change(ch)
        row["before"] = _cap_image(ch.get("pre"))
        row["after"] = _cap_image(ch.get("post"))
        out.append(row)
    return out


def _clean_result(result: Any) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    return {k: v for k, v in result.items() if k not in _JUNK_RESULT_KEYS}


def _failure_row(result: Dict[str, Any]) -> Dict[str, Any]:
    row = {
        "error_type": result.get("error_type") or "operation_failed",
        "error": result.get("error"),
    }
    for key in ("suggestion", "valid_operations", "hint"):
        if key in result:
            row[key] = result[key]
    return row


def run_preview(
    tool: str,
    label: str,
    args: Dict[str, Any],
    fn: Callable[[], Any],
) -> Dict[str, Any]:
    """Run ``fn()`` (the real write path) with the commit suppressed; report instead of saving.

    ``args`` are the call's arguments AS RECEIVED (they key the ``plan_hash``).
    """
    hash_before = plan_hash(tool, args)
    capture = write_lane.PreviewCapture(
        serialize=tmsl_of, committed=committed_tmsl, before=_offline_before()
    )
    result: Any = None
    raised: Optional[BaseException] = None
    rollback_notes: List[str] = []
    try:
        with write_lane.preview_scope(capture):
            try:
                result = fn()
            except Exception as exc:  # noqa: BLE001 - reported as the would-be failure
                raised = exc
                logger.info("preview of %s raised %s: %s", label, type(exc).__name__, exc)
    finally:
        rollback_notes = _roll_back(capture)

    out: Dict[str, Any] = {
        "preview": True,
        "dry_run": True,
        "applied": False,
        "committed": False,
        "rolled_back": not rollback_notes,
        "operation": label,
    }
    if rollback_notes:
        out["rollback_warning"] = "; ".join(rollback_notes)
    if raised is not None:
        out.update(
            success=False,
            would_succeed=False,
            error_type="preview_failed",
            error=f"{type(raised).__name__}: {raised}",
            errors=[{"error_type": type(raised).__name__, "error": str(raised)}],
            would_change=[],
            breaks=[],
        )
        return out
    manager = _clean_result(result)
    failed = isinstance(result, dict) and result.get("success") is False
    changes: List[Dict[str, Any]] = []
    warnings: List[str] = list(capture.problems)
    if capture.before is not None and capture.after is not None:
        changes = images.diff_maps(capture.before, capture.after)
    elif capture.commits and not failed:
        warnings.append(
            "the before / after images could not be captured; `would_change` is empty but the "
            "operation WOULD save (see `result`)."
        )
    out["would_change"] = _would_change(changes)
    out["would_change_counts"] = images.count_by_kind(changes)
    if len(changes) > _MAX_CHANGES:
        out["would_change_truncated"] = len(changes)
    if failed:
        out.update(
            success=False,
            would_succeed=False,
            error_type=(result or {}).get("error_type") or "operation_failed",
            error=(result or {}).get("error"),
            errors=[_failure_row(result)],
            breaks=[],
            result=manager,
        )
        return out
    out.update(success=True, would_succeed=True, errors=[], result=manager)
    out.update(_breaks(changes, args))
    out["validation"] = {
        "structure": "checked (the manager ran against the real model)",
        "dax_expressions": "not_validated (a preview cannot evaluate DAX; the engine binds "
        "expressions when the change is saved)",
    }
    if not capture.commits:
        out["note"] = "the operation made no save (nothing to change, or it is read-only)."
    if warnings:
        out["warnings"] = warnings
    gen = _generation()
    out["model_generation"] = gen
    out["plan_hash"] = hash_before
    out["apply_with"] = "repeat the call WITHOUT preview and pass plan_hash=<plan_hash>"
    return out


def _roll_back(capture: "write_lane.PreviewCapture") -> List[str]:
    """Undo every in-memory edit the preview made. Returns the problems (empty = clean)."""
    notes: List[str] = []
    try:
        from core.infrastructure import offline_model
    except Exception:  # noqa: BLE001
        offline_model = None  # type: ignore[assignment]
    offline = bool(offline_model is not None and offline_model.is_active())
    if offline:
        try:
            dirty = bool((offline_model.info() or {}).get("pending_unpersisted_changes"))
        except Exception:  # noqa: BLE001
            dirty = True
        if dirty or capture.commits:
            try:
                write_lane.safe_undo(None)  # the offline lane re-reads the folder
            except Exception as exc:  # noqa: BLE001
                notes.append(f"offline rollback failed: {exc}")
        return notes
    for model in capture.models:
        try:
            write_lane.safe_undo(model)
        except Exception as exc:  # noqa: BLE001
            # the manager's connection is normally already closed by now: its unsaved edits die
            # with it. Only a still-connected model matters, and that rollback did not raise.
            logger.debug("preview: UndoLocalChanges on a closed model: %s", exc)
    return notes


# ---------------------------------------------------------------------------
# refresh: a plan, not a run
# ---------------------------------------------------------------------------
def _model_tables() -> Optional[List[Dict[str, Any]]]:
    try:
        from core.infrastructure.connection_state import connection_state
        from core.operations.amo_helpers import get_server_db_model, safe_disconnect

        server, _db, model, _err = get_server_db_model(connection_state)
        if server is None or model is None:
            return None
        try:
            return [
                {"name": str(t.Name), "partitions": [str(p.Name) for p in t.Partitions]}
                for t in model.Tables
            ]
        finally:
            safe_disconnect(server)
    except Exception as exc:  # noqa: BLE001
        logger.debug("preview: table listing failed: %s", exc)
        return None


def refresh_plan(tool: str, label: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """``preview=true`` of a refresh: what WOULD be requested. No refresh runs, nothing is saved."""
    object_type = args.get("object_type")
    refresh_type = str(args.get("refresh_type") or "automatic")
    tables = args.get("tables")
    if isinstance(tables, str):
        tables = [tables]
    if args.get("table_name") and not tables:
        tables = [args["table_name"]]
    listing = _model_tables()
    known = {t["name"]: t for t in listing or []}
    targets: List[Dict[str, Any]] = []
    unknown: List[str] = []
    if tables:
        for name in tables:
            if listing is not None and name not in known:
                unknown.append(str(name))
                continue
            entry: Dict[str, Any] = {"table": str(name)}
            if object_type == "partition" and args.get("partition_name"):
                entry["partitions"] = [args["partition_name"]]
            elif name in known:
                entry["partitions"] = known[name]["partitions"]
            targets.append(entry)
    elif listing is not None:
        targets = [{"table": t["name"], "partitions": t["partitions"]} for t in listing]
    out: Dict[str, Any] = {
        "success": not unknown,
        "preview": True,
        "dry_run": True,
        "applied": False,
        "committed": False,
        "operation": label,
        "would_succeed": not unknown,
        "would_change": [],
        "would_refresh": {
            "refresh_type": refresh_type,
            "scope": "tables" if tables else "model",
            "tables": targets,
            "table_count": len(targets) if (tables or listing is not None) else None,
        },
        "errors": [
            {"error_type": "table_not_found", "error": f"table not found: {n}"} for n in unknown
        ],
        "breaks": [],
        "note": "A refresh changes data, not the model definition: no TMSL diff. Nothing was requested.",
    }
    if unknown:
        out.update(error_type="table_not_found", error=f"table(s) not found: {', '.join(unknown)}")
        return out
    out["model_generation"] = _generation()
    out["plan_hash"] = plan_hash(tool, args)
    out["apply_with"] = "repeat the call WITHOUT preview and pass plan_hash=<plan_hash>"
    return out
