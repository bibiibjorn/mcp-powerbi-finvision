"""Verify-after-rename + automatic rollback (review C-01, C-40, C-41).

A rename is ``object.Name = new`` plus a DAX cascade across every expression
(``rename_cascade``). Both can go wrong in ways TOM accepts silently, so every
live rename runs:

1. :func:`snapshot_errors` BEFORE mutating (which objects are already broken);
2. the mutation, recording every changed site (``record``; the object-name
   change itself is recorded too, with a restore callable);
3. :func:`finish` — ``SaveChanges`` through the write lane, then a fresh
   read-back of object errors. Any NEW error (a measure / calc item / format
   string / RLS filter that evaluated before and fails now) triggers an exact
   rollback: every recorded site gets its ``before`` text back, the name is
   restored and a second ``SaveChanges`` persists the revert.

:func:`preview_hash` gives rename previews (``preview=true``) a staleness token
over the would-be diff, so an apply can require that nothing changed since.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Callable, Dict, List, Optional

from core.operations import model_readback
from core.operations.rename_cascade import public_records, restore_records
from core.operations.write_lane import commit

logger = logging.getLogger(__name__)

__all__ = [
    "finish",
    "name_record",
    "preview_hash",
    "preview_payload",
    "public_records",
    "snapshot_errors",
    "udf_reference_report",
]


def _verify_enabled() -> bool:
    try:
        from core.config.config_manager import config

        return bool(config.get("model_write.verify_renames", True))
    except Exception:  # noqa: BLE001
        return True


def snapshot_errors() -> Optional[Dict[str, str]]:
    """Baseline of object errors, or None when verification is off/unavailable."""
    if not _verify_enabled():
        return None
    return model_readback.expression_errors()


def name_record(obj: Any, old_name: str, new_name: str, path: str) -> Dict[str, Any]:
    """Record for the object-name change itself (restorable like a cascade site)."""
    return {
        "path": path,
        "kind": "object_name",
        "before": old_name,
        "after": new_name,
        "_restore": (lambda o=obj, n=old_name: setattr(o, "Name", n)),
    }


def preview_hash(changes: List[Dict[str, Any]]) -> str:
    body = json.dumps(
        sorted(
            (str(c.get("path")), str(c.get("kind")), str(c.get("before")), str(c.get("after")))
            for c in public_records(changes)
        ),
        ensure_ascii=False,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]


def preview_payload(
    object_type: str, old_name: str, new_name: str, record: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Standard rename-preview response from a dry-run cascade record."""
    changes = public_records(record)
    edits = [c for c in changes if c.get("kind") not in ("untouched",)]
    return {
        "success": True,
        "preview": True,
        "object_type": object_type,
        "old_name": old_name,
        "new_name": new_name,
        "expression_changes": edits,
        "change_count": len(edits),
        "untouched": [c for c in changes if c.get("kind") == "untouched"],
        "preview_hash": preview_hash(record),
        "hint": "Re-call without preview (optionally with this preview_hash) to apply.",
    }


def udf_reference_report(rewrite: Callable[[str], Any]) -> List[Dict[str, Any]]:
    """UDF bodies the cascade cannot rewrite (AMO cannot see UDFs): names that
    reference the renamed object, from the DMV. ``rewrite(expr) -> (new, n)``."""
    rows = model_readback.udf_rows()
    if not rows:
        return []
    out = []
    for r in rows:
        expr = r.get("Expression") or ""
        try:
            _new, n = rewrite(expr)
        except Exception:  # noqa: BLE001
            continue
        if n:
            out.append(
                {
                    "kind": "udf",
                    "path": f"function '{r.get('Name')}'",
                    "references": n,
                    "reason": (
                        "UDF bodies are not reachable through AMO — update with "
                        "02_Model_Operations object_type=function operation=update."
                    ),
                }
            )
    return out


def finish(
    model: Any,
    *,
    label: str,
    baseline: Optional[Dict[str, str]],
    record: List[Dict[str, Any]],
    verify_landed: Optional[Callable[[], Optional[bool]]] = None,
) -> Dict[str, Any]:
    """Commit a recorded rename; roll it back when it introduced new errors.

    Returns an info dict to merge into the success result, or a failure dict
    (``success: False``) when the rename was rolled back. Raises whatever
    ``commit`` raises when the save itself failed and could not be verified.
    """
    info = dict(commit(model, label=label, verify=verify_landed))
    if baseline is None:
        info["verification"] = "unavailable"
        return info
    after = model_readback.expression_errors()
    new_errors = model_readback.diff_errors(baseline, after)
    if new_errors is None:
        info["verification"] = "unavailable"
        return info
    if not new_errors:
        info["verification"] = "passed"
        return info

    logger.warning("%s introduced %d new error(s); rolling back", label, len(new_errors))
    restored = restore_records(record)
    rollback: Dict[str, Any] = {"restored_sites": restored}
    try:
        commit(model, label=f"{label}:rollback")
        rollback["saved"] = True
        post = model_readback.diff_errors(baseline, model_readback.expression_errors())
        rollback["verified_clean"] = post == [] if post is not None else None
    except Exception as e:  # noqa: BLE001
        rollback["saved"] = False
        rollback["error"] = str(e)
    return {
        "success": False,
        "error_type": "rename_introduced_errors",
        "error": (
            f"{label} made {len(new_errors)} expression(s) fail to evaluate; the rename "
            "and its cascade were rolled back."
            if rollback.get("saved")
            else f"{label} made {len(new_errors)} expression(s) fail AND the rollback "
            "save failed — inspect the model now."
        ),
        "new_errors": new_errors[:50],
        "rolled_back": bool(rollback.get("saved")),
        "rollback": rollback,
        "attempted_changes": public_records(record)[:50],
    }
