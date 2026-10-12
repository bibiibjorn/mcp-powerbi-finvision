"""Codemod transaction layer — preview / apply / rollback with a plan_hash staleness check.

2026-09-22 review (E-01, E-06, E-11, E-12, E-13, E-14, E-22, E-27, K-04):

- ``plan_hash`` covers transform + canonical args + database identity + model
  state (E-12); the model the handler passes is read with ``bypass_cache=True``
  (E-02);
- every rewrite is re-lexed by ``dax_text.sanity_check`` — structurally broken
  results are REJECTED (listed in preview/apply, never written);
- apply writes a WRITE-AHEAD journal (status ``pending``) before the first write,
  with database identity, every edit's before/after and full snapshots of
  deleted measures; then persists the whole plan in ONE TOM session / ONE
  ``SaveChanges`` (``DAXInjector.apply_measure_changes``, E-14) with an
  in-session check that each measure still has the previewed ``before``;
- a delete (``inline_measure delete_after``) happens only when every rewrite is
  written, the in-memory TOM model (all expression kinds) has no remaining
  reference, and the report scan (PBIP path) proves no visual binds it (E-06);
- semantics-preserving transforms are verified: the affected measures are
  evaluated before and after; any difference rolls the apply back (E-27);
- rollback accepts only journals from the journal directory, verifies the
  database identity, refuses targets changed since apply (unless ``force``),
  DELETES measures the apply created (K-04), recreates deleted measures with
  their metadata and reports every failure (E-13).

2026-10-03 review F-15 (``extract_udf``): an edit with target ``function:<Name>`` and note
``create`` creates a DAX UDF. apply creates it FIRST (an uncalled UDF is inert) so the measure
rewrites that call it can be bound by the engine, writes the measures in the usual single save,
and deletes the function again when the apply does not commit (a failed write, a refused plan, a
value-verification rollback); ``rollback`` deletes it after restoring the measures.
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.codemods import dax_text
from core.codemods.plan_hash import compute_plan_hash
from core.codemods.transforms import registry as transform_registry
from core.codemods.transforms.base import Edit, PreviewResult

logger = logging.getLogger(__name__)

JOURNAL_RETENTION_DAYS = 30
JOURNAL_ENV = "MCP_CODEMOD_JOURNAL_DIR"


def list_transforms() -> Dict[str, Any]:
    return {
        "transforms": [
            {
                "name": t.name,
                "description": t.description,
                "args_schema": t.args_schema,
                "persistable": t.persistable,
                "semantics_preserving": t.semantics_preserving,
            }
            for t in transform_registry().values()
        ]
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_marker_edit(edit_after: str, edit_note: str) -> bool:
    """True when an edit is a non-persistable MARKER (move_measure_batch's
    ``after='table=<Dst>'`` / ``note='move'``), not real DAX."""
    if (edit_note or "") == "move":
        return True
    return (edit_after or "").startswith("table=")


def _target_to_function(target: str) -> Optional[str]:
    """``'function:Name'`` -> ``Name`` (None for any other target)."""
    t = target or ""
    return (
        t[len("function:") :] if t.startswith("function:") and len(t) > len("function:") else None
    )


def _default_function_persister():
    from core.operations.function_crud_manager import FunctionCrudManager

    return FunctionCrudManager()


def _delete_functions(names: List[str], function_persister: Any) -> List[Dict[str, Any]]:
    """Delete created functions (best effort; an already-missing one is fine)."""
    results: List[Dict[str, Any]] = []
    try:
        fp = function_persister if function_persister is not None else _default_function_persister()
    except Exception as ex:  # noqa: BLE001
        return [
            {"function": n, "deleted": False, "error": f"no function persister: {ex}"}
            for n in names
        ]
    for n in names:
        try:
            r = fp.delete_function(n)
        except Exception as ex:  # noqa: BLE001
            r = {"success": False, "error": str(ex)}
        ok = bool(r.get("success")) or r.get("error_type") == "not_found"
        results.append({"function": n, "deleted": ok, **({} if ok else {"error": r.get("error")})})
    return results


def _target_to_table_measure(target: str) -> Tuple[Optional[str], Optional[str]]:
    """``'measure:Table[Name]'`` -> ``(Table, Name)``; names may contain ``[``/``]``."""
    t = target or ""
    if not t.startswith("measure:"):
        return None, None
    body = t[len("measure:") :]
    i = body.find("[")
    if i <= 0 or not body.endswith("]"):
        return None, None
    return body[:i], body[i + 1 : -1]


def _diff(before: str, after: str, target: str) -> str:
    return "".join(
        difflib.unified_diff(
            (before or "").splitlines(keepends=True),
            (after or "").splitlines(keepends=True),
            fromfile=f"{target} (before)",
            tofile=f"{target} (after)",
        )
    )


def _run_preview(t: Any, model: Any, args: Dict[str, Any]) -> PreviewResult:
    """Transform preview + structural sanity gate (rejected edits are dropped)."""
    pr = t.preview(model, args)
    kept: List[Edit] = []
    for e in pr.edits:
        if _is_marker_edit(e.after, e.note):
            kept.append(e)
            continue
        reason = dax_text.sanity_check(e.after, None if e.note == "create" else e.before)
        if reason is None:
            kept.append(e)
        else:
            pr.rejected.append({"target": e.target, "reason": reason, "after": e.after})
            if e.target in pr.affected_measures:
                pr.affected_measures.remove(e.target)
    pr.edits = kept
    return pr


def _unknown(name: str) -> Dict[str, Any]:
    return {
        "success": False,
        "error": f"unknown transform {name!r}",
        "error_type": "invalid_args",
        "valid_transforms": sorted(transform_registry()),
    }


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def preview(
    model: Any,
    transform_name: str,
    args: Dict[str, Any],
    identity: Optional[str] = None,
) -> Dict[str, Any]:
    reg = transform_registry()
    if transform_name not in reg:
        return _unknown(transform_name)
    t = reg[transform_name]
    pr = _run_preview(t, model, args)
    if pr.refusal:
        return {
            "success": False,
            **pr.refusal,
            "plan_hash": compute_plan_hash(model, transform_name, args, identity),
            "persistable": t.persistable,
            "edits": [],
            "warnings": pr.warnings,
        }
    out: Dict[str, Any] = {
        "success": True,
        "plan_hash": compute_plan_hash(model, transform_name, args, identity),
        "persistable": t.persistable,
        "semantics_preserving": t.semantics_preserving,
        # E-22: full bodies + a unified diff (the old 200-char cut made a
        # rewrite impossible to review).
        "edits": [
            {
                "target": e.target,
                "before": e.before or "",
                "after": e.after or "",
                "note": e.note,
                "diff": (
                    "" if _is_marker_edit(e.after, e.note) else _diff(e.before, e.after, e.target)
                ),
            }
            for e in pr.edits
        ],
        "affected_measures": pr.affected_measures,
        "affected_visuals": pr.affected_visuals,
        "warnings": pr.warnings,
        "rejected_edits": pr.rejected,
        "skipped": pr.skipped,
    }
    if not t.persistable:
        out["warnings"] = list(pr.warnings) + [
            f"{transform_name} is PREVIEW-ONLY: apply refuses it "
            "(error_type='transform_not_persistable')."
        ]
    if pr.edits and t.persistable:
        res = t.apply(model, args, pr)
        if isinstance(res, dict) and res.get("delete"):
            out["delete_candidate"] = {k: res["delete"].get(k) for k in ("table", "measure")}
    return out


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------


def _journal_dir() -> Path:
    base = os.environ.get(JOURNAL_ENV)
    p = Path(base) if base else Path(tempfile.gettempdir()) / "mcp-pbi-codemods"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _prune_journals(d: Path) -> None:
    cutoff = time.time() - JOURNAL_RETENTION_DAYS * 86400
    for f in d.glob("codemod-*.json"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def _write_journal(path: Path, journal: Dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(journal, default=str), encoding="utf-8")
    os.replace(tmp, path)


def list_journals(limit: int = 50) -> Dict[str, Any]:
    d = _journal_dir()
    items = []
    for f in sorted(d.glob("codemod-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[
        : max(1, int(limit))
    ]:
        try:
            j = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        items.append(
            {
                "journal_path": str(f),
                "ts": j.get("ts"),
                "transform": j.get("transform"),
                "status": j.get("status", "committed"),
                "database": (j.get("identity") or {}).get("database"),
                "edits": len(j.get("edits") or []),
                "deleted": len(j.get("deleted") or []),
            }
        )
    return {
        "success": True,
        "journal_dir": str(d),
        "retention_days": JOURNAL_RETENTION_DAYS,
        "journals": items,
    }


def _resolve_journal(journal_path: str) -> Tuple[Optional[Path], Optional[Dict[str, Any]]]:
    """Only journals INSIDE the journal directory are accepted (E-13: an arbitrary
    JSON file would otherwise be an ungated bulk-write primitive)."""
    d = _journal_dir().resolve()
    p = Path(journal_path)
    if not p.is_absolute():
        p = d / p
    try:
        rp = p.resolve()
    except OSError:
        rp = p
    if rp.parent != d or not rp.name.startswith("codemod-") or rp.suffix != ".json":
        return None, {
            "success": False,
            "error": f"journal_path must be a codemod journal inside {d}",
            "error_type": "invalid_args",
        }
    if not rp.is_file():
        return None, {
            "success": False,
            "error": f"journal not found: {journal_path}",
            "error_type": "invalid_args",
        }
    return rp, None


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _default_persister():
    """The production persister: the ADOMD-bound DAXInjector the connection
    state manages (E-01 — ``DAXInjector(connection_state)`` made every write
    fail with ``connection_lost``)."""
    from core.dax.dax_injector import get_live_injector

    return get_live_injector()


def _persist(
    persister: Any,
    upserts: List[Dict[str, Any]],
    deletes: List[Dict[str, Any]],
    model: Any,
) -> Dict[str, Any]:
    """One atomic write when the persister supports it, else a checked
    sequential fallback (test fakes / legacy persisters)."""
    if hasattr(persister, "apply_measure_changes"):
        return persister.apply_measure_changes(upserts=upserts, deletes=deletes)

    results, errors, deleted, refused = [], [], [], []
    for u in upserts:
        if u.get("create_only") and _facade_measure(model, u["table_name"], u["measure_name"]):
            errors.append({"target": f"{u['table_name']}[{u['measure_name']}]", "error": "exists"})
            continue
        try:
            r = persister.upsert_measure(
                table_name=u["table_name"],
                measure_name=u["measure_name"],
                dax_expression=u["dax_expression"],
            )
        except Exception as ex:  # noqa: BLE001
            r = {"success": False, "error": str(ex)}
        if isinstance(r, dict) and r.get("success") is False:
            errors.append(
                {"target": f"{u['table_name']}[{u['measure_name']}]", "error": r.get("error")}
            )
        else:
            results.append(f"{u['table_name']}[{u['measure_name']}]")
    for d in deletes:
        if errors:
            refused.append({**d, "reason": "a rewrite failed — delete skipped"})
            continue
        if d.get("guard_refs"):
            remaining = _facade_remaining_refs(model, d, upserts)
            if remaining:
                refused.append({**d, "reason": "still referenced", "referenced_by": remaining})
                continue
        try:
            r = persister.delete_measure(table_name=d["table_name"], measure_name=d["measure_name"])
        except Exception as ex:  # noqa: BLE001
            r = {"success": False, "error": str(ex)}
        if isinstance(r, dict) and r.get("success") is False:
            errors.append(
                {"target": f"{d['table_name']}[{d['measure_name']}]", "error": r.get("error")}
            )
        else:
            deleted.append(
                {"table": d["table_name"], "measure": d["measure_name"], "snapshot": None}
            )
    return {
        "success": not errors,
        "committed": bool(results or deleted),
        "atomic": False,
        "written": results,
        "errors": errors,
        "deleted": deleted,
        "delete_refused": refused,
        "conflicts": [],
    }


def _facade_measure(model: Any, table: str, name: str) -> Any:
    for tbl in getattr(model, "tables", []) or []:
        if dax_text.norm_name(getattr(tbl, "name", "")) != dax_text.norm_name(table):
            continue
        for m in getattr(tbl, "measures", []) or []:
            if dax_text.norm_name(getattr(m, "name", "")) == dax_text.norm_name(name):
                return m
    return None


def _facade_remaining_refs(
    model: Any, d: Dict[str, Any], upserts: List[Dict[str, Any]]
) -> List[str]:
    """Measures-only rescan for the non-atomic fallback persister."""
    new_bodies = {
        (dax_text.norm_name(u["table_name"]), dax_text.norm_name(u["measure_name"])): u[
            "dax_expression"
        ]
        for u in upserts
    }
    out = []
    for tbl in getattr(model, "tables", []) or []:
        for m in getattr(tbl, "measures", []) or []:
            key = (
                dax_text.norm_name(getattr(tbl, "name", "")),
                dax_text.norm_name(getattr(m, "name", "")),
            )
            if key == (dax_text.norm_name(d["table_name"]), dax_text.norm_name(d["measure_name"])):
                continue
            expr = new_bodies.get(key, getattr(m, "expression", "") or "")
            if dax_text.measure_ref_sites(expr, d["measure_name"], d["table_name"]):
                out.append(f"{getattr(tbl, 'name', '')}[{getattr(m, 'name', '')}]")
    return out


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def apply(
    model: Any,
    transform_name: str,
    args: Dict[str, Any],
    plan_hash: str,
    persister: Any = None,
    *,
    identity: Optional[str] = None,
    identity_info: Optional[Dict[str, Any]] = None,
    visual_checker: Optional[Callable[[str, str], Dict[str, Any]]] = None,
    evaluator: Optional[Callable[[List[Tuple[str, str]]], Optional[Dict[str, Any]]]] = None,
    verify: Optional[bool] = None,
    validator: Optional[Callable[[str, Dict[Tuple[str, str], str]], Optional[str]]] = None,
    function_persister: Any = None,
) -> Dict[str, Any]:
    """Apply a previewed plan (``_apply_plan`` + removal of functions a failed apply created).

    ``function_persister``: ``create_function`` / ``delete_function`` provider for ``function:``
    edits (default ``FunctionCrudManager``).
    """
    created: List[str] = []
    result = _apply_plan(
        model,
        transform_name,
        args,
        plan_hash,
        persister,
        identity=identity,
        identity_info=identity_info,
        visual_checker=visual_checker,
        evaluator=evaluator,
        verify=verify,
        validator=validator,
        function_persister=function_persister,
        created_functions=created,
    )
    if created and not (isinstance(result, dict) and result.get("committed")):
        result["function_cleanup"] = _delete_functions(created, function_persister)
        result["functions_created"] = []
    elif created:
        result["functions_created"] = list(created)
    return result


def _apply_plan(
    model: Any,
    transform_name: str,
    args: Dict[str, Any],
    plan_hash: str,
    persister: Any = None,
    *,
    identity: Optional[str] = None,
    identity_info: Optional[Dict[str, Any]] = None,
    visual_checker: Optional[Callable[[str, str], Dict[str, Any]]] = None,
    evaluator: Optional[Callable[[List[Tuple[str, str]]], Optional[Dict[str, Any]]]] = None,
    verify: Optional[bool] = None,
    validator: Optional[Callable[[str, Dict[Tuple[str, str], str]], Optional[str]]] = None,
    function_persister: Any = None,
    created_functions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Apply a previewed plan.

    ``identity``: database identity string (part of the plan hash);
    ``identity_info``: dict stored in the journal (database / port);
    ``visual_checker(table, measure)``: report usage probe for deletes
    (``report_refs.find_measure_bindings`` bound to the PBIP path); without it a
    delete is SKIPPED (usage unverifiable);
    ``evaluator(refs) -> {ref: value}`` (or None when evaluation failed): E-27
    before/after verification of semantics-preserving transforms; ``verify``
    defaults to ``t.semantics_preserving`` when an evaluator is given;
    ``validator(after, defines) -> error | None``: engine parse/bind check of
    every rewritten body BEFORE the write (E-27 parse-before-write; ``defines``
    are measures the plan creates, so callers may reference them). A rejected
    body is dropped (``rejected_edits``); a rejected CREATE aborts the apply.
    """
    reg = transform_registry()
    if transform_name not in reg:
        return _unknown(transform_name)
    t = reg[transform_name]

    # move_measure_batch & co: edits are markers, not bodies — refuse up front.
    if not t.persistable:
        return {
            "success": False,
            "applied_edits": 0,
            "applied_count": 0,
            "error_type": "transform_not_persistable",
            "error": (
                f"{transform_name} cannot be persisted by 15_DAX_Refactor apply — "
                "use 02_Model_Operations measure/move or 03_Batch_Operations measures/move"
            ),
        }

    actual_hash = compute_plan_hash(model, transform_name, args, identity)
    if actual_hash != plan_hash:
        return {
            "success": False,
            "error": (
                "plan_hash mismatch — the model, the transform, its args or the connected "
                "database changed since preview; run preview again"
            ),
            "error_type": "plan_stale",
            "expected": plan_hash,
            "actual": actual_hash,
        }

    pr = _run_preview(t, model, args)
    if pr.refusal:
        return {"success": False, "applied_edits": 0, "applied_count": 0, **pr.refusal}
    if any(_is_marker_edit(e.after, e.note) for e in pr.edits):
        return {
            "success": False,
            "applied_edits": 0,
            "applied_count": 0,
            "error_type": "transform_not_persistable",
            "error": f"{transform_name} emitted marker edits — nothing written",
        }

    # F-15: functions first. An uncalled UDF is inert, and the measure rewrites that call it can
    # only be bound by the engine once it exists. ``apply`` deletes it again unless the plan commits.
    fn_edits = [e for e in pr.edits if _target_to_function(e.target) and e.note == "create"]
    if fn_edits:
        try:
            fp = (
                function_persister
                if function_persister is not None
                else _default_function_persister()
            )
        except Exception as ex:  # noqa: BLE001 - CLR / connection edge
            return {
                "success": False,
                "error": f"could not acquire a function persister for apply: {ex}",
                "error_type": "not_connected",
            }
        for e in fn_edits:
            fname = _target_to_function(e.target)
            try:
                r = fp.create_function(
                    fname, e.after, description=(args.get("description") or None)
                )
            except Exception as ex:  # noqa: BLE001
                r = {"success": False, "error": str(ex)}
            if not (isinstance(r, dict) and r.get("success")):
                return {
                    "success": False,
                    "applied_edits": 0,
                    "applied_count": 0,
                    "error_type": (r or {}).get("error_type") or "function_create_failed",
                    "error": f"could not create function {fname!r}: {(r or {}).get('error')}",
                    "function_failed": fname,
                }
            if created_functions is not None:
                created_functions.append(fname)

    if validator is not None and pr.edits:
        creates = {
            _target_to_table_measure(e.target): e.after
            for e in pr.edits
            if e.note == "create" and _target_to_table_measure(e.target)[0]
        }
        kept: List[Edit] = []
        for e in pr.edits:
            key = _target_to_table_measure(e.target)
            if key[0] is None:  # a function edit: bound through the measures that call it
                kept.append(e)
                continue
            defines = {k: v for k, v in creates.items() if k != key}
            try:
                err = validator(e.after, defines)
            except Exception as ex:  # noqa: BLE001
                err = f"validation failed: {ex}"
            if err:
                pr.rejected.append(
                    {"target": e.target, "reason": f"engine rejected the rewrite: {err}"}
                )
                if e.note == "create":
                    return {
                        "success": False,
                        "applied_edits": 0,
                        "applied_count": 0,
                        "error_type": "invalid_rewrite",
                        "error": f"the new measure {e.target} does not compile: {err}",
                        "rejected_edits": pr.rejected,
                    }
            else:
                kept.append(e)
        pr.edits = kept
    res = t.apply(model, args, pr)
    delete_spec = res.get("delete") if isinstance(res, dict) else None

    upserts: List[Dict[str, Any]] = []
    for e in pr.edits:
        tname, mname = _target_to_table_measure(e.target)
        if not tname or not mname:
            continue
        create = e.note == "create"
        upserts.append(
            {
                "table_name": tname,
                "measure_name": mname,
                "dax_expression": e.after,
                "create_only": create,
                # In-session staleness check (E-02): the TOM body must still be
                # what preview saw; None for a create (must not exist).
                "expected_expression": None if create else (e.before or ""),
            }
        )

    deletes: List[Dict[str, Any]] = []
    delete_skipped: Optional[Dict[str, Any]] = None
    affected_visuals: List[str] = []
    if delete_spec:
        dt, dm = delete_spec["table"], delete_spec["measure"]
        usage = (
            visual_checker(dt, dm)
            if visual_checker is not None
            else {"checked": False, "reason": "no report scanner available"}
        )
        affected_visuals = list(usage.get("visuals") or [])
        if pr.rejected or pr.skipped:
            delete_skipped = {
                "table": dt,
                "measure": dm,
                "reason": "some callers were not rewritten (rejected/skipped) — delete skipped",
            }
        elif not usage.get("checked"):
            delete_skipped = {
                "table": dt,
                "measure": dm,
                "reason": f"report usage unverifiable ({usage.get('reason')}) — delete skipped; "
                "pass pbip_path (or set it via 09_Debug_Operations set_path) to allow it",
            }
        elif usage.get("files"):
            delete_skipped = {
                "table": dt,
                "measure": dm,
                "reason": "the report still binds this measure — delete skipped",
                "report_files": usage.get("files"),
            }
        else:
            deletes.append(
                {
                    "table_name": dt,
                    "measure_name": dm,
                    "guard_refs": True,
                    "expected_expression": delete_spec.get("expression"),
                }
            )

    if not upserts and not deletes:
        return {
            "success": True,
            "applied_edits": 0,
            "applied_count": 0,
            "deleted": [],
            "delete_skipped": delete_skipped,
            "rejected_edits": pr.rejected or None,
            "warnings": pr.warnings,
            "plan_hash_matched": True,
            "message": "nothing to write",
        }

    if persister is None:
        try:
            persister = _default_persister()
        except Exception as e:  # noqa: BLE001 - CLR/connection edge
            return {
                "success": False,
                "error": f"could not acquire a persister for apply: {e}",
                "error_type": "not_connected",
            }

    do_verify = (
        t.semantics_preserving if verify is None else bool(verify)
    ) and evaluator is not None
    verify_refs = [
        _target_to_table_measure(e.target)
        for e in pr.edits
        if e.note != "create" and _target_to_table_measure(e.target)[0]
    ]
    baseline = None
    if do_verify and verify_refs:
        baseline = evaluator(verify_refs)  # type: ignore[arg-type]
        if baseline is None:
            return {
                "success": False,
                "error": "could not evaluate the affected measures before the write "
                "(verification baseline failed) — nothing written; pass verify=false to skip",
                "error_type": "verification_unavailable",
            }

    # Write-ahead journal (E-13): exists before the first write.
    jdir = _journal_dir()
    _prune_journals(jdir)
    out_path = jdir / f"codemod-{int(time.time() * 1000)}-{os.getpid()}.json"
    journal: Dict[str, Any] = {
        "version": 2,
        "ts": time.time(),
        "status": "pending",
        "transform": transform_name,
        "args": args,
        "plan_hash": plan_hash,
        "identity": identity_info or ({"database": identity} if identity else None),
        "edits": [
            {"target": e.target, "before": e.before, "after": e.after, "note": e.note}
            for e in pr.edits
        ],
        "deleted": [],
        "functions_created": list(created_functions or []),
    }
    try:
        _write_journal(out_path, journal)
    except OSError as e:
        return {
            "success": False,
            "error": f"could not write the undo journal ({e}) — nothing written",
            "error_type": "journal_error",
        }

    try:
        pres = _persist(persister, upserts, deletes, model)
    except Exception as ex:  # noqa: BLE001
        pres = {"success": False, "committed": False, "errors": [{"error": str(ex)}]}

    committed = bool(pres.get("committed"))
    deleted = []
    for d in pres.get("deleted") or []:
        snap = d.get("snapshot") or {}
        expr = snap.get("expression") if snap else None
        if expr is None and delete_spec:
            expr = delete_spec.get("expression")
        deleted.append(
            {"table": d["table"], "measure": d["measure"], "expression": expr, "snapshot": snap}
        )
    journal["deleted"] = deleted
    journal["status"] = "committed" if committed else "failed"
    journal["persist_result"] = {
        k: pres.get(k) for k in ("errors", "conflicts", "delete_refused", "invalid", "atomic")
    }

    verification = None
    rolled_back = None
    if committed and baseline is not None:
        after_vals = evaluator(verify_refs)  # type: ignore[misc]
        verification = _compare_values(baseline, after_vals)
        journal["verification"] = verification
        if _verification_failed(verification):
            _write_journal(out_path, journal)
            rolled_back = rollback(
                str(out_path),
                persister=persister,
                force=True,
                identity=None,
                function_persister=function_persister,
            )
            journal = json.loads(out_path.read_text(encoding="utf-8"))
    _write_journal(out_path, journal)

    errors = list(pres.get("errors") or []) + list(pres.get("conflicts") or [])
    if pres.get("invalid"):
        errors += [{"target": i.get("target"), "error": i.get("error")} for i in pres["invalid"]]
    refused = pres.get("delete_refused") or []
    if refused and delete_skipped is None:
        delete_skipped = refused[0] if len(refused) == 1 else {"refused": refused}

    out: Dict[str, Any] = {
        "success": committed
        and not errors
        and (verification is None or not _verification_failed(verification)),
        "applied_edits": len(upserts) if committed else 0,
        "applied_count": len(upserts) if committed else 0,
        "committed": committed,
        "atomic": pres.get("atomic", True),
        "deleted": deleted,
        "delete_skipped": delete_skipped,
        "affected_visuals": affected_visuals,
        "persist_errors": errors or None,
        "rejected_edits": pr.rejected or None,
        "warnings": pr.warnings,
        "plan_hash_matched": True,
        "journal_path": str(out_path),
        "rollback_hint": "15_DAX_Refactor operation='rollback' journal_path=<above>",
        "details": res,
    }
    if pres.get("conflicts"):
        out["error_type"] = "plan_stale"
        out["error"] = "a measure changed after preview (in-session TOM check) — nothing written"
    elif not committed:
        out["error_type"] = pres.get("error_type") or "persist_failed"
        out["error"] = pres.get("error") or "write failed — nothing committed"
    if verification is not None:
        out["verification"] = verification
        if verification.get("inconclusive"):
            out["warnings"] = list(pr.warnings) + [
                "verification inconclusive: every affected measure is BLANK at the grand total "
                "(before and after), so the rewrite could not be proven equivalent - and nothing "
                "contradicts it either; the apply was NOT rolled back. Spot-check the measure "
                "under a filter where it returns a value."
            ]
        if _verification_failed(verification):
            out["error_type"] = "semantic_change_detected"
            out["error"] = (
                "the rewrite changed the value of an affected measure — the apply was "
                "rolled back from the journal"
            )
            out["rolled_back"] = rolled_back
    return out


def _verification_failed(verification: Dict[str, Any]) -> bool:
    """A verdict that justifies a rollback: not equivalent AND not merely inconclusive."""
    return not verification.get("equivalent") and not verification.get("inconclusive")


def _compare_values(
    before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Grand-total comparison of the affected measures.

    2026-10-03 review D-13: a measure that is BLANK at the grand total on BOTH sides (a
    HASONEVALUE-guarded KPI / SVG / status measure) proves nothing, but it does not contradict the
    rewrite either. The old verdict (``equivalent: False, vacuous_all_blank``) rolled back every
    correct rewrite of such a measure and reported it as ``semantic_change_detected``. Those
    measures are now set aside as ``inconclusive_measures``; the remaining ones are compared as
    before, and when nothing is left to compare the verdict is ``inconclusive`` (no rollback).
    """
    if after is None:
        return {"equivalent": False, "reason": "post-write evaluation failed"}
    try:
        from core.dax.semantic_equivalence import compare_rows

        b = dict(before or {})
        a = dict(after)
        keys = sorted(set(b) | set(a))
        blank_both = [k for k in keys if b.get(k) is None and a.get(k) is None]
        live = [k for k in keys if k not in blank_both]
        if not live:
            return {
                "equivalent": None,
                "inconclusive": True,
                "reason": "inconclusive_all_blank",
                "method": "grand_total",
                "inconclusive_measures": blank_both,
                "note": (
                    "every affected measure is BLANK at the grand total before and after: the "
                    "rewrite is neither proven nor contradicted"
                ),
            }
        v = compare_rows([{k: b.get(k) for k in live}], [{k: a.get(k) for k in live}])
        v["method"] = "grand_total"
        v["note"] = (
            "verified at the grand total (no filters) — per-slice differences are not covered"
        )
        if blank_both:
            v["inconclusive_measures"] = blank_both
            v["note"] += (
                "; measures BLANK at the grand total on both sides could not be verified: "
                + ", ".join(blank_both)
            )
        return v
    except Exception as e:  # noqa: BLE001
        return {"equivalent": False, "reason": f"comparison failed: {e}"}


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------


def rollback(
    journal_path: str,
    persister: Any = None,
    *,
    identity: Optional[str] = None,
    force: bool = False,
    function_persister: Any = None,
) -> Dict[str, Any]:
    """Replay a codemod journal to restore the pre-apply state (E-13 / K-04).

    - only journals inside the journal directory are accepted;
    - the journal's database must equal ``identity`` (the current connection);
      legacy journals without identity need ``force=True``;
    - each rewritten measure is restored to ``before`` only if it still equals
      the journal's ``after`` (in-session TOM check) — unless ``force``;
    - measures the apply CREATED (``note='create'``) are deleted;
    - measures the apply DELETED are recreated with their snapshot metadata;
    - functions the apply CREATED (``function:`` edits, F-15) are deleted AFTER the measures are
      restored;
    - every result is checked; ``success`` only when everything was restored.
    """
    p, err = _resolve_journal(journal_path)
    if err:
        return err
    try:
        journal = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"bad journal: {e}", "error_type": "bad_output"}

    status = journal.get("status", "committed")
    if status == "rolled_back" and not force:
        return {
            "success": False,
            "error": "journal was already rolled back (pass force=true to replay again)",
            "error_type": "invalid_args",
            "journal_path": str(p),
        }
    if status == "failed":
        return {
            "success": True,
            "restored": [],
            "message": "the apply committed nothing — nothing to roll back",
            "journal_path": str(p),
        }
    jid = (journal.get("identity") or {}).get("database")
    if not force:
        if not jid:
            return {
                "success": False,
                "error": "journal has no database identity (pre-v7.16) — pass force=true to "
                "replay it onto the connected model",
                "error_type": "identity_unverified",
            }
        if identity is not None and jid != identity:
            return {
                "success": False,
                "error": f"journal belongs to database {jid!r}, connected to {identity!r}",
                "error_type": "identity_mismatch",
            }

    upserts: List[Dict[str, Any]] = []
    deletes: List[Dict[str, Any]] = []
    for d in journal.get("deleted") or []:
        snap = d.get("snapshot") or {}
        expr = d.get("expression") or snap.get("expression")
        if not expr:
            continue
        u = {
            "table_name": d["table"],
            "measure_name": d["measure"],
            "dax_expression": expr,
            "create_only": True,
            "expected_expression": None,
        }
        for k in (
            "format_string",
            "format_string_expression",
            "display_folder",
            "description",
            "data_category",
            "is_hidden",
            "detail_rows_expression",
        ):
            if snap.get(k) is not None:
                u[k] = snap[k]
        upserts.append(u)
    missing = [
        d.get("measure")
        for d in journal.get("deleted") or []
        if not (d.get("expression") or (d.get("snapshot") or {}).get("expression"))
    ]
    fn_creates: List[str] = []
    for e in journal.get("edits") or []:
        if _is_marker_edit(e.get("after", ""), e.get("note", "")):
            continue
        fname = _target_to_function(e.get("target", ""))
        if fname:
            if e.get("note") == "create":
                fn_creates.append(fname)
            continue
        tname, mname = _target_to_table_measure(e.get("target", ""))
        if not tname or not mname:
            continue
        if e.get("note") == "create":
            deletes.append(
                {
                    "table_name": tname,
                    "measure_name": mname,
                    "guard_refs": False,
                    "expected_expression": None if force else e.get("after"),
                }
            )
            continue
        upserts.append(
            {
                "table_name": tname,
                "measure_name": mname,
                "dax_expression": e.get("before", ""),
                "create_only": False,
                "expected_expression": None if force else e.get("after"),
            }
        )

    if not upserts and not deletes:
        fn_results = _delete_functions(fn_creates, function_persister) if fn_creates else []
        fn_errors = [r for r in fn_results if not r.get("deleted")]
        journal["status"] = "rolled_back" if not fn_errors else journal.get("status", "committed")
        _write_journal(p, journal)
        return {
            "success": not missing and not fn_errors,
            "restored": [f"function:{r['function']} (deleted)" for r in fn_results if r["deleted"]],
            "errors": (
                [{"measure": m, "error": "no captured expression"} for m in missing]
                + [{"function": r["function"], "error": r.get("error")} for r in fn_errors]
            )
            or None,
            "journal_path": str(p),
        }

    if persister is None:
        try:
            persister = _default_persister()
        except Exception as e:  # noqa: BLE001
            return {
                "success": False,
                "error": f"could not acquire a persister for rollback: {e}",
                "error_type": "not_connected",
            }

    try:
        pres = _persist(persister, upserts, deletes, None)
    except Exception as ex:  # noqa: BLE001
        pres = {"success": False, "committed": False, "errors": [{"error": str(ex)}]}
    errors = list(pres.get("errors") or []) + list(pres.get("conflicts") or [])
    errors += [{"measure": m, "error": "no captured expression"} for m in missing]
    ok = bool(pres.get("committed")) and not errors
    fn_deleted: List[str] = []
    if ok and fn_creates:
        # the measures are back to their old bodies: nothing calls the function any more
        for r in _delete_functions(fn_creates, function_persister):
            if r["deleted"]:
                fn_deleted.append(r["function"])
            else:
                errors.append({"function": r["function"], "error": r.get("error")})
        ok = not errors
    if ok:
        journal["status"] = "rolled_back"
        journal["rolled_back_at"] = time.time()
        _write_journal(p, journal)
    restored = [f"measure:{u['table_name']}[{u['measure_name']}]" for u in upserts]
    restored += [f"measure:{d['table_name']}[{d['measure_name']}] (deleted)" for d in deletes]
    restored += [f"function:{n} (deleted)" for n in fn_deleted]
    out: Dict[str, Any] = {
        "success": ok,
        "restored": restored if pres.get("committed") else [],
        "errors": errors or None,
        "journal_path": str(p),
    }
    if pres.get("conflicts"):
        out["error_type"] = "rollback_conflict"
        out["error"] = (
            "a measure changed after the apply — nothing restored (pass force=true to overwrite)"
        )
    elif not ok:
        out["error_type"] = pres.get("error_type") or "persist_failed"
        out["error"] = pres.get("error") or "rollback write failed"
    return out
