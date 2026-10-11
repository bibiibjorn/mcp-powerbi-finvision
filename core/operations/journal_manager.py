"""``02_Model_Operations object_type=model`` journal operations (JR-01).

``history`` / ``diff`` read the durable write journal (:mod:`core.operations.write_journal`);
``undo`` / ``replay`` / ``checkpoint_restore`` re-apply journal or checkpoint images to the LIVE
model in ONE SaveChanges (through ``write_lane.commit``, so the operation is itself journaled and
undoable); ``checkpoint_create`` snapshots the whole model to
``output/journal/checkpoints/<name>.json``.

Planning is pure (:mod:`core.operations.journal_plan`) and runs against a fresh read of the live
model inside the write lane's lock, so the plan an apply executes is never older than the save.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.operations import journal_images as images
from core.operations import journal_plan as plan_mod
from core.operations import write_journal
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)

__all__ = ["WriteJournalManager", "JOURNAL_OPERATIONS"]

JOURNAL_OPERATIONS = [
    "history",
    "diff",
    "undo",
    "replay",
    "checkpoint_create",
    "checkpoint_restore",
]

#: Entries written by these journal ops are never picked by "undo the last N".
_MANAGED_OPS = frozenset({"undo", "replay", "checkpoint_restore"})
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")
_DEFAULT_LIMIT = 50


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _summary(entry: Dict[str, Any]) -> Dict[str, Any]:
    refs = entry.get("refs") or []
    out: Dict[str, Any] = {
        "id": entry["id"],
        "ts": entry.get("ts"),
        "tool": entry.get("tool"),
        "operation": entry.get("operation"),
        "refs": refs[:8],
        "ref_count": len(refs),
        "kinds": entry.get("kinds") or {},
        "flushed": bool(entry.get("flushed")),
        "session": entry.get("session"),
        "model_generation": entry.get("model_generation"),
    }
    meta_op = (entry.get("meta") or {}).get("op")
    if meta_op:
        out["journal_op"] = meta_op
    if entry.get("reverted_by"):
        out["reverted_by"] = entry["reverted_by"]
    if entry.get("noop"):
        out["noop"] = True
    if entry.get("degraded"):
        out["degraded"] = entry["degraded"]
    return out


def _countable(entry: Dict[str, Any]) -> bool:
    return bool(entry.get("changes")) and not entry.get("degraded")


class WriteJournalManager:
    """Journal history / diff / undo / replay / checkpoints."""

    # ------------------------------------------------------------------ plumbing
    def _view(
        self, args: Dict[str, Any]
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """(journal view, error). The connected project's journal, else the newest file (marked
        ``fallback`` - undo / replay refuse it, see :meth:`_project_guard`)."""
        explicit = args.get("journal")
        note = None
        fallback = False
        if explicit:
            key = str(explicit)
            if key.endswith(".jsonl"):
                key = key[: -len(".jsonl")]
            if not write_journal.valid_key(key):
                return None, error_envelope(
                    "invalid_parameters",
                    f"journal={explicit!r} is not a journal name: use the name from "
                    "operation=history (letters, digits, space, . _ - only; no path separators).",
                )
        else:
            key, _ident = write_journal._identity(None)  # noqa: SLF001
            if not write_journal._path(key).exists():  # noqa: SLF001
                journals = write_journal.list_journals()
                if journals:
                    key = journals[0]["key"]
                    fallback = True
                    note = (
                        f"No journal for the connected model; showing the most recently written "
                        f"one ({key}). Pass journal=<name> to pick another."
                    )
        view = write_journal.read_journal(key)
        if note:
            view["note"] = note
        if fallback:
            view["fallback"] = True
        return view, None

    @staticmethod
    def _project_guard(
        view: Dict[str, Any], entries: List[Dict[str, Any]], op: str
    ) -> Optional[Dict[str, Any]]:
        """N-07: never apply the journal of ANOTHER project. The connected project has no journal
        of its own, so ``_view`` fell back to the newest file; applying its entries would write
        a different project's edits into this model. An explicit ``journal=<name>`` skips this
        (the view is then not a fallback); so does every entry whose project is the connected one.
        """
        if not view.get("fallback"):
            return None
        connected = write_journal._connected_project()  # noqa: SLF001
        foreign = [
            e["id"] for e in entries if not write_journal.same_project(e.get("project"), connected)
        ]
        if not foreign:
            return None
        return error_envelope(
            "journal_project_mismatch",
            f"model {op} refused: the connected model has no journal of its own and "
            f"{view['key']!r} belongs to another project ({len(foreign)} entr"
            f"{'y' if len(foreign) == 1 else 'ies'} not recorded for the connected project). "
            "Nothing was changed.",
            journal=view["key"],
            connected_project=connected,
            entries=foreign[:10],
            suggestion="If you are sure, pass journal=<name> explicitly (see operation=history).",
        )

    def _connect(self) -> Tuple[Any, Any, Optional[Dict[str, Any]]]:
        from core.infrastructure.connection_state import connection_state
        from core.operations.amo_helpers import get_server_db_model

        server, _db, model, err = get_server_db_model(connection_state)
        if server is None or model is None:
            return (
                None,
                None,
                error_envelope(
                    "connection_error",
                    f"Could not connect to the model: {err or 'not connected'}",
                    suggestion="Run 01_Connection operation=ensure, then retry.",
                ),
            )
        return server, model, None

    @staticmethod
    def _disconnect(server: Any) -> None:
        try:
            server.Disconnect()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _function_images() -> Optional[Dict[str, Dict[str, Any]]]:
        """UDF images from the DMV (authoritative even when the loaded AMO cannot see UDFs)."""
        try:
            from core.operations.function_crud_manager import FunctionCrudManager

            mgr = FunctionCrudManager()
            rows, err = mgr._udf_rows()  # noqa: SLF001
            if err or rows is None:
                return None
            out = {}
            for row in rows:
                body = mgr._row_image(row)  # noqa: SLF001
                out[f"function:{body['name']}"] = {
                    "kind": "function",
                    "table": None,
                    "name": body["name"],
                    "json": body,
                }
            return out
        except Exception as exc:  # noqa: BLE001
            logger.debug("UDF image read failed: %s", exc)
            return None

    def _live_map(self, model: Any) -> Tuple[Dict[str, Dict[str, Any]], bool]:
        """(image map of the live model, udfs_captured). UDF images come from the DMV."""
        mp = images.split_model(write_journal.serialize_model(model))
        funcs = self._function_images()
        mp = {k: v for k, v in mp.items() if v["kind"] != "function"}
        if funcs is None:
            return mp, False
        mp.update(funcs)
        return mp, True

    @staticmethod
    def _confirmed(args: Dict[str, Any]) -> bool:
        try:
            from core.autonomous.gating import GATE_POLICY_OFF, current_policy, is_confirmed

            return current_policy() == GATE_POLICY_OFF or is_confirmed(args)
        except Exception:  # noqa: BLE001
            return args.get("confirm_destructive") is True

    def _confirmation_error(self, op: str, reasons: List[str], **extra: Any) -> Dict[str, Any]:
        return error_envelope(
            "confirmation_required",
            f"model {op} needs confirm_destructive=true: " + "; ".join(reasons),
            operation=f"02_Model_Operations:model/{op}",
            suggestion="Re-run with preview=true to review the changes, then confirm_destructive=true.",
            **extra,
        )

    # ------------------------------------------------------------------ apply
    def _apply(
        self,
        *,
        op: str,
        args: Dict[str, Any],
        planner: Callable[[Dict[str, Dict[str, Any]]], Tuple[Dict[str, Any], Dict[str, Any]]],
        meta: Dict[str, Any],
        preview: bool,
        block_on_conflict: bool,
        udfs_required: bool = False,
    ) -> Dict[str, Any]:
        """Connect, read the live model, plan, and (unless *preview*) apply + commit in ONE save."""
        from core.operations.journal_apply import JournalApplyError, apply_ops
        from core.operations.write_lane import commit, get_write_lane

        lock = contextlib.nullcontext() if preview else get_write_lane().lock
        with lock:
            server, model, err = self._connect()
            if err:
                return err
            try:
                live_map, udfs_ok = self._live_map(model)
                plan, ctx = planner(live_map if udfs_ok else _without_functions(live_map))
                info = plan_mod.summarize_plan(plan)
                info.update(ctx)
                if not udfs_ok:
                    info["udf_note"] = (
                        "UDFs could not be read (DMV) - function changes were left out of this plan."
                    )
                confirm_reasons: List[str] = []
                removals = plan_mod.needs_removal_confirmation(plan)
                if removals:
                    confirm_reasons.append("it would remove " + ", ".join(removals[:6]))
                if _truthy(args.get("force")):
                    confirm_reasons.append("force=true applies changes over conflicts")
                if preview:
                    return {
                        "success": True,
                        "operation": op,
                        "preview": True,
                        "can_apply": not (plan["conflicts"] and block_on_conflict),
                        "requires_confirm_destructive": bool(confirm_reasons),
                        **info,
                    }
                if plan["conflicts"] and block_on_conflict and not _truthy(args.get("force")):
                    return error_envelope(
                        "journal_conflict",
                        f"{len(plan['conflicts'])} object(s) no longer match the journal entry; "
                        "nothing was changed.",
                        conflicts=plan["conflicts"][:50],
                        suggestion=(
                            "Run preview=true to inspect, undo the newer entries first, or "
                            "re-run with force=true and confirm_destructive=true."
                        ),
                    )
                if confirm_reasons and not self._confirmed(args):
                    return self._confirmation_error(op, confirm_reasons, **info)
                if not plan["ops"]:
                    return {
                        "success": True,
                        "operation": op,
                        "applied": 0,
                        "message": f"Nothing to {op}: the live model already matches.",
                        **info,
                    }
                tom_ops = [o for o in plan["ops"] if o["kind"] != "function"]
                fn_ops = [o for o in plan["ops"] if o["kind"] == "function"]
                warnings: List[str] = []
                commit_info: Dict[str, Any] = {}
                if tom_ops:
                    try:
                        applied = apply_ops(model, tom_ops)
                        warnings.extend(applied["warnings"])
                    except JournalApplyError as exc:
                        self._undo_local(model)
                        return error_envelope(
                            "journal_apply_failed",
                            f"{exc}. Nothing was saved.",
                            operation=f"02_Model_Operations:model/{op}",
                        )
                    try:
                        commit_info = commit(
                            model, label=f"journal/{op}", journal_meta={"op": op, **meta}
                        )
                    except Exception as exc:  # noqa: BLE001 - CLR interop
                        self._undo_local(model)
                        from core.autonomous.clr_errors import format_clr_error

                        return format_clr_error(exc, error_type="journal_save_failed", operation=op)
            finally:
                self._disconnect(server)
            fn_result = self._apply_function_ops(fn_ops) if fn_ops else None

        out: Dict[str, Any] = {
            "success": True,
            "operation": op,
            "applied": len(tom_ops) + (fn_result or {}).get("applied", 0),
            **info,
        }
        out.update(
            {k: v for k, v in commit_info.items() if k in ("journal_warning", "post_write_warning")}
        )
        if warnings:
            out["warnings"] = warnings
        if fn_result is not None:
            out["function_lane"] = fn_result
            if fn_result.get("errors"):
                out["warnings"] = out.get("warnings", []) + fn_result["errors"]
        entry = self._last_entry_id(op)
        if entry:
            out["journal_entry"] = entry
        out["message"] = (
            f"{op}: {out['applied']} change(s) applied in one save. Verify with 04_Run_DAX; "
            "the journal recorded this as a new entry."
        )
        return out

    @staticmethod
    def _undo_local(model: Any) -> None:
        try:
            from core.operations.write_lane import safe_undo

            safe_undo(model)  # offline lane: re-reads the folder (UndoLocalChanges throws there)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Rolling back the unsaved journal apply failed: %s", exc)

    @staticmethod
    def _last_entry_id(op: str) -> Optional[str]:
        try:
            key, _ident = write_journal._identity(None)  # noqa: SLF001
            entries = write_journal.read_journal(key)["entries"]
            for e in reversed(entries):
                if (e.get("meta") or {}).get("op") == op:
                    return e["id"]
        except Exception:  # noqa: BLE001
            pass
        return None

    @staticmethod
    def _apply_function_ops(ops: List[Dict[str, Any]]) -> Dict[str, Any]:
        """UDFs are written through the UDF lane (TOM when the AMO sees them, else TMSL) - a
        separate write, journaled by that lane."""
        from core.operations.function_crud_manager import FunctionCrudManager

        mgr = FunctionCrudManager()
        applied = 0
        errors: List[str] = []
        for op in ops:
            body = op.get("json") or {}
            name = op["name"]
            if op["action"] == "remove":
                res = mgr.delete_function(name)
            elif op["action"] == "add":
                res = mgr.create_function(
                    name, body.get("expression") or "", None, body.get("description")
                )
            else:
                res = mgr.update_function(name, body.get("expression"), body.get("description"))
            if res.get("success"):
                applied += 1
            else:
                errors.append(f"function {name}: {res.get('error')}")
        return {"applied": applied, "errors": errors}

    # ------------------------------------------------------------------ history / diff
    def history(self, args: Dict[str, Any]) -> Dict[str, Any]:
        try:
            since = write_journal.parse_since(args.get("since"))
        except ValueError as exc:
            return error_envelope("invalid_parameters", str(exc))
        view, err = self._view(args)
        if err:
            return err
        needle = str(args.get("object") or "").strip().lower()
        unflushed_only = _truthy(args.get("unflushed_only"))
        limit = max(1, _int(args.get("limit"), _DEFAULT_LIMIT))
        rows = []
        for e in reversed(view["entries"]):
            if e.get("noop") and not needle:
                continue
            if unflushed_only and (e.get("flushed") or not _countable(e)):
                continue
            if since is not None and write_journal._ts_epoch(e.get("ts")) < since:  # noqa: SLF001
                continue
            if needle and not (
                any(needle in str(r).lower() for r in e.get("refs") or [])
                or needle in str(e.get("label") or "").lower()
            ):
                continue
            rows.append(e)
        countable = [e for e in view["entries"] if _countable(e)]
        out: Dict[str, Any] = {
            "success": True,
            "journal": view["key"],
            "journal_file": view["path"],
            "session": write_journal.SESSION_ID,
            "entries": [_summary(e) for e in rows[:limit]],
            "matching": len(rows),
            "total_entries": len(view["entries"]),
            "journal_unflushed": sum(1 for e in countable if not e["flushed"]),
            "journal_flushed": sum(1 for e in countable if e["flushed"]),
            "other_journals": [
                j["key"] for j in write_journal.list_journals() if j["key"] != view["key"]
            ][:10],
            "checkpoints": self._checkpoint_names(),
        }
        if len(rows) > limit:
            out["truncated"] = True
            out["hint"] = "Newest first; narrow with since/object/unflushed_only or raise limit."
        if view.get("note"):
            out["note"] = view["note"]
        if view["corrupt_lines"]:
            out["corrupt_lines"] = view["corrupt_lines"]
            out["journal_warning"] = (
                f"{view['corrupt_lines']} unreadable line(s) in the journal file (a torn write "
                "after a crash?); those writes cannot be undone or replayed. The readable "
                "entries are listed."
            )
        if not write_journal.is_enabled():
            out["warning"] = "The write journal is disabled (model_write.journal_enabled=false)."
        if out["journal_unflushed"]:
            out["hint_replay"] = (
                "Unflushed entries exist only in Desktop's memory; after a discarded reload or a "
                "crash run 02_Model_Operations object_type=model operation=replay preview=true."
            )
        return out

    def diff(self, args: Dict[str, Any]) -> Dict[str, Any]:
        entry_id = str(args.get("entry_id") or "").strip()
        if not entry_id:
            return error_envelope(
                "missing_argument", "entry_id is required (see operation=history)"
            )
        view, err = self._view(args)
        if err:
            return err
        entry = next((e for e in view["entries"] if e["id"] == entry_id), None)
        if entry is None:
            return error_envelope(
                "not_found", f"journal entry {entry_id!r} not found in {view['key']}"
            )
        changes = entry.get("changes") or []
        out: Dict[str, Any] = {
            "success": True,
            "entry": _summary(entry),
            "args": entry.get("args"),
            "result": entry.get("result"),
            "changes": [images.describe_change(c) for c in changes[:200]],
            "change_count": len(changes),
        }
        if len(changes) > 200:
            out["changes_truncated"] = True
        if entry.get("degraded"):
            out["warning"] = f"No pre-image was captured for this write: {entry['degraded']}"
        return out

    # ------------------------------------------------------------------ undo
    def undo(self, args: Dict[str, Any]) -> Dict[str, Any]:
        view, err = self._view(args)
        if err:
            return err
        entry_id = str(args.get("entry_id") or "").strip()
        entries = view["entries"]
        if entry_id:
            picked = [e for e in entries if e["id"] == entry_id]
            if not picked:
                return error_envelope(
                    "not_found", f"journal entry {entry_id!r} not found in {view['key']}"
                )
            if not _countable(picked[0]):
                why = picked[0].get("degraded") or "it changed no model object"
                return error_envelope("not_undoable", f"entry {entry_id} cannot be undone: {why}")
            selected = picked
        else:
            count = max(1, _int(args.get("count"), 1))
            selected = []
            for e in reversed(entries):
                if not _countable(e) or e.get("reverted_by"):
                    continue
                if (e.get("meta") or {}).get("op") in _MANAGED_OPS:
                    continue
                selected.append(e)
                if len(selected) >= count:
                    break
            if not selected:
                return error_envelope(
                    "nothing_to_undo", "The journal has no undoable entries.", journal=view["key"]
                )
        refused = self._project_guard(view, selected, "undo")
        if refused:
            return refused
        force = _truthy(args.get("force"))
        ids = [e["id"] for e in selected]

        def planner(live_map):
            plan = plan_mod.plan_undo(selected, live_map, force=force)
            return plan, {"reverting": [_summary(e) for e in selected]}

        return self._apply(
            op="undo",
            args=args,
            planner=planner,
            meta={"reverts": ids},
            preview=_truthy(args.get("preview")),
            block_on_conflict=True,
        )

    # ------------------------------------------------------------------ replay
    def replay(self, args: Dict[str, Any]) -> Dict[str, Any]:
        try:
            since = write_journal.parse_since(args.get("since"))
        except ValueError as exc:
            return error_envelope("invalid_parameters", str(exc))
        view, err = self._view(args)
        if err:
            return err
        wanted = args.get("entry_ids")
        if isinstance(wanted, str):
            wanted = [w.strip() for w in wanted.split(",") if w.strip()]
        selected = [
            e
            for e in view["entries"]
            if _countable(e)
            and not e["flushed"]
            and (not wanted or e["id"] in wanted)
            and (since is None or write_journal._ts_epoch(e.get("ts")) >= since)  # noqa: SLF001
        ]
        if not selected:
            return {
                "success": True,
                "operation": "replay",
                "applied": 0,
                "journal": view["key"],
                "message": "Nothing to replay: the journal has no unflushed entries.",
            }
        refused = self._project_guard(view, selected, "replay")
        if refused:
            return refused
        force = _truthy(args.get("force"))
        ids = [e["id"] for e in selected]

        def planner(live_map):
            plan = plan_mod.plan_replay(selected, live_map, force=force)
            return plan, {"replaying": [_summary(e) for e in selected]}

        out = self._apply(
            op="replay",
            args=args,
            planner=planner,
            meta={"replays": ids},
            preview=_truthy(args.get("preview")),
            block_on_conflict=False,
        )
        if out.get("success") and out.get("conflicts"):
            out["conflict_note"] = (
                "Conflicting changes were NOT applied (the live object is neither the entry's "
                "pre- nor post-image). Review with preview=true; force=true overrides."
            )
        return out

    # ------------------------------------------------------------------ checkpoints
    @staticmethod
    def _checkpoint_dir():
        return write_journal.journal_dir() / "checkpoints"

    def _checkpoint_names(self) -> List[str]:
        try:
            return sorted(p.stem for p in self._checkpoint_dir().glob("*.json"))[:50]
        except OSError:
            return []

    def _checkpoint_path(self, args: Dict[str, Any]):
        name = str(args.get("checkpoint") or "").strip()
        if not _NAME_RE.match(name):
            return None, error_envelope(
                "invalid_parameters",
                "checkpoint is required: 1-64 chars of letters, digits, space, . _ - "
                "(starting with a letter or digit).",
            )
        return name, self._checkpoint_dir() / f"{name}.json"

    def checkpoint_create(self, args: Dict[str, Any]) -> Dict[str, Any]:
        name, path = self._checkpoint_path(args)
        if name is None:
            return path
        if path.exists() and not _truthy(args.get("force")):
            return error_envelope(
                "already_exists",
                f"checkpoint {name!r} already exists; pass force=true to overwrite.",
                path=str(path),
            )
        server, model, err = self._connect()
        if err:
            return err
        try:
            model_json = write_journal.serialize_model(model)
            funcs = self._function_images()
        finally:
            self._disconnect(server)
        if funcs is not None:
            model_json["functions"] = [v["json"] for v in funcs.values()]
        key, ident = write_journal._identity(None)  # noqa: SLF001
        doc = {
            "v": write_journal.JOURNAL_VERSION,
            "name": name,
            "ts": write_journal._now_iso(),  # noqa: SLF001
            "journal": key,
            "database": ident.get("database_name"),
            "project": ident.get("project"),
            "session": write_journal.SESSION_ID,
            "functions_captured": funcs is not None,
            "model": model_json,
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, ensure_ascii=True, separators=(",", ":"))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except OSError as exc:
            return error_envelope(
                "write_failed", f"could not write checkpoint: {exc}", path=str(path)
            )
        counts = images.count_by_kind(
            {"kind": v["kind"]} for v in images.split_model(model_json).values()
        )
        return {
            "success": True,
            "operation": "checkpoint_create",
            "checkpoint": name,
            "path": str(path),
            "bytes": path.stat().st_size,
            "objects": counts,
            "functions_captured": funcs is not None,
            "message": "Checkpoint written. Restore it with operation=checkpoint_restore checkpoint="
            + name
            + " (preview=true first).",
        }

    @staticmethod
    def _checkpoint_project_guard(
        doc: Dict[str, Any], args: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """N-07: a checkpoint taken for another project is not restored into this one unless
        ``journal=<the checkpoint's journal name>`` is passed explicitly."""
        recorded = doc.get("project")
        connected = write_journal._connected_project()  # noqa: SLF001
        if not recorded and not connected:
            return None  # unsaved model on both sides: nothing to compare
        if write_journal.same_project(recorded, connected):
            return None
        if args.get("journal") and str(args["journal"]) == str(doc.get("journal")):
            return None
        return error_envelope(
            "journal_project_mismatch",
            f"model checkpoint_restore refused: checkpoint {doc.get('name')!r} was taken for "
            f"project {recorded!r}, the connected project is {connected!r}. Nothing was changed.",
            checkpoint_project=recorded,
            connected_project=connected,
            suggestion="Pass journal=<the checkpoint's journal name> to restore it anyway.",
        )

    def checkpoint_restore(self, args: Dict[str, Any]) -> Dict[str, Any]:
        name, path = self._checkpoint_path(args)
        if name is None:
            return path
        try:
            with open(path, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except FileNotFoundError:
            return error_envelope(
                "not_found",
                f"checkpoint {name!r} not found",
                checkpoints=self._checkpoint_names(),
            )
        except (OSError, ValueError) as exc:
            return error_envelope("invalid_checkpoint", f"checkpoint {name!r} is unreadable: {exc}")
        refused = self._checkpoint_project_guard(doc, args)
        if refused:
            return refused
        target_all = images.split_model(doc.get("model") or {})
        funcs_in_ckpt = bool(doc.get("functions_captured"))

        def planner(live_map):
            live, target = live_map, dict(target_all)
            if not funcs_in_ckpt:  # never delete UDFs a checkpoint never knew about
                live = _without_functions(live)
                target = _without_functions(target)
            plan = plan_mod.plan_restore(live, target)
            return plan, {
                "checkpoint": name,
                "checkpoint_ts": doc.get("ts"),
                "checkpoint_database": doc.get("database"),
            }

        return self._apply(
            op="checkpoint_restore",
            args=args,
            planner=planner,
            meta={"checkpoint": name},
            preview=_truthy(args.get("preview")),
            block_on_conflict=False,
        )


def _without_functions(mp: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {k: v for k, v in mp.items() if v["kind"] != "function"}
