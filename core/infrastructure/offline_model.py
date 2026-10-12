"""Offline TMDL-folder lane: edit a PBIP semantic model through the real TOM serializer (OFF-01).

``01_Connection operation=connect_folder pbip_path=<.pbip | .SemanticModel | definition/>``
deserialises the model's ``definition/`` folder into an in-memory TOM database —
no Desktop, no engine. The TOM-based model tools (``02_Model_Operations``,
``03_Batch_Operations`` object writes) then edit that database, and every commit
serialises it back to disk with Microsoft's ``TmdlSerializer``.

Why: hand/text edits of TMDL are unsafe (indentation-scoped blocks — deleting a
header orphans its children and Desktop refuses to open the project). The
serializer cannot emit an orphan. This lane replaces "close Desktop, hand-edit
TMDL, pray, reopen" for overnight refactors, CI and repo-side model changes.

Safety rules enforced here:

* **Refuses while a Power BI Desktop instance has the project open** — Desktop
  would overwrite the files on its next save (and a reload re-reads them).
* Every write is serialised to a temp folder first, then STAGED next to the real
  files (``*.mcp_new``) and swapped in with ``os.replace`` only after every copy
  succeeded (deletions last); the written folder is deserialised again. Any
  failure (a locked file, a failed re-parse) restores the backup and the write is
  reported as failed (N-03). Existing line endings are preserved and a file whose
  content did not change is not rewritten (N-20).
* The folder is fingerprinted at connect and after every persist; a persist
  refuses with ``offline_folder_changed`` when something else edited it meanwhile
  (git pull, Desktop save, a colleague) instead of silently reverting it (N-04).
* Rollback (N-02): ``Model.UndoLocalChanges`` throws on a disconnected model, so
  managers call :func:`rollback` (via ``write_lane.safe_undo``) which re-reads the
  folder; a handed-out database that was mutated but not persisted is re-read on
  ``Disconnect`` as well.
* No engine: DAX queries, refresh, traces and DMV reads are unavailable
  (``offline_no_engine``). Calculated objects are not evaluated; open the project
  in Desktop afterwards to see values.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_state: Optional[Dict[str, Any]] = None


class OfflineModelError(RuntimeError):
    pass


class OfflineFolderChanged(OfflineModelError):
    """The definition folder was edited by someone else since connect / the last persist."""

    error_type = "offline_folder_changed"


class OfflineBusy(OfflineModelError):
    """A write could not take the offline lane's lock in time; nothing was written."""

    error_type = "busy"


def resolve_definition_folder(path: str) -> Path:
    """.pbip / .SemanticModel / definition folder -> the ``definition`` folder."""
    p = Path(path).expanduser()
    if p.is_file() and p.suffix.lower() == ".pbip":
        import json

        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            raise OfflineModelError(f"Cannot read {p}: {e}") from e
        # .pbip -> report -> definition.pbir byPath -> the SemanticModel folder
        for art in doc.get("artifacts") or []:
            rep = (art.get("report") or {}).get("path")
            if not rep:
                continue
            pbir = p.parent / rep / "definition.pbir"
            if pbir.exists():
                ref = json.loads(pbir.read_text(encoding="utf-8")).get("datasetReference") or {}
                by_path = (ref.get("byPath") or {}).get("path")
                if by_path:
                    return resolve_definition_folder(str((pbir.parent / by_path).resolve()))
        candidates = list(p.parent.glob("*.SemanticModel"))
        if len(candidates) == 1:
            return resolve_definition_folder(str(candidates[0]))
        raise OfflineModelError(f"Could not locate the semantic model of {p}")
    if p.is_dir() and p.name.lower() == "definition" and (p / "model.tmdl").exists():
        return p
    if p.is_dir() and (p / "definition" / "model.tmdl").exists():
        return p / "definition"
    raise OfflineModelError(
        f"{p} is not a .pbip, a .SemanticModel folder or a TMDL definition folder"
    )


DETECTION_FAILED_PREFIX = "<Desktop detection failed: "


def _norm(path: str) -> str:
    try:
        resolved = os.path.realpath(path)
    except OSError:
        resolved = path
    return os.path.normcase(os.path.normpath(resolved))


def _is_under(candidate: str, root: str) -> bool:
    """``candidate`` is ``root`` or lies inside it (separator boundary, not a string prefix)."""
    c, r = _norm(candidate), _norm(root)
    return c == r or c.startswith(r.rstrip("/\\") + os.sep)


def _desktop_has_open(definition: Path) -> Optional[str]:
    """Title of a Desktop instance that has this project open, if any.

    Fails CLOSED (N-27): when the detection itself raises, the returned text starts with
    :data:`DETECTION_FAILED_PREFIX` so the caller refuses instead of assuming "not open".
    Paths are compared on a separator boundary after ``realpath`` (``.../R01`` no longer
    matches ``.../R01-copy``; junction / subst spellings resolve to the same folder).
    """
    try:
        from core.infrastructure.connection_manager import PowerBIDesktopDetector

        model_dir = str(definition.parent)
        project_dir = str(definition.parent.parent)
        for inst in PowerBIDesktopDetector().find_powerbi_instances():
            for key in ("file_full_path", "pbip_folder_path"):
                v = str(inst.get(key) or "")
                if v and (_is_under(v, project_dir) or _is_under(v, model_dir)):
                    return str(inst.get("display_name") or inst.get("window_title") or v)
    except Exception as e:  # noqa: BLE001 - detection broke: refuse rather than guess
        logger.warning("desktop detection failed: %s", e)
        return f"{DETECTION_FAILED_PREFIX}{type(e).__name__}: {e}>"
    return None


def _fingerprint(folder: Path) -> Dict[str, str]:
    """``{relative posix path: sha1}`` of every ``*.tmdl`` file under ``folder`` (N-04)."""
    out: Dict[str, str] = {}
    for p in sorted(folder.rglob("*.tmdl")):
        try:
            out[p.relative_to(folder).as_posix()] = hashlib.sha1(p.read_bytes()).hexdigest()
        except OSError as e:
            out[p.relative_to(folder).as_posix()] = f"unreadable:{type(e).__name__}"
    return out


def _fingerprint_diff(old: Dict[str, str], new: Dict[str, str]) -> List[str]:
    names = sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))
    return names


def connect_folder(path: str, *, allow_open_in_desktop: bool = False) -> Dict[str, Any]:
    """Deserialise the folder into an in-memory TOM database and activate the lane."""
    global _state
    definition = resolve_definition_folder(path)
    owner = _desktop_has_open(definition)
    if owner and not allow_open_in_desktop:
        detection = owner.startswith(DETECTION_FAILED_PREFIX)
        return {
            "success": False,
            "error_type": "project_open_in_desktop",
            "error": (
                f"Could not check whether Power BI Desktop has this project open {owner}; refusing."
                if detection
                else f"Power BI Desktop has this project open ({owner}); it would overwrite offline edits."
            ),
            "suggestion": "Close Desktop WITHOUT saving (ask the user) or use the live lane (01_Connection ensure).",
        }
    loaded = _load(definition)
    if not loaded.get("database"):
        err = loaded.get("error") or "TMDL deserialisation failed"
        return {
            "success": False,
            "error_type": "tmdl_invalid",
            "error": err,
            "definition_folder": str(definition),
        }
    db = loaded["database"]
    # The client library's UDF gate re-reads the folder WITHOUT function definitions;
    # writing that database back would delete functions.tmdl. Such a load is read-only.
    read_only = bool(loaded.get("udf_gate"))
    with _lock:
        _state = {
            "definition": definition,
            "database": db,
            "connected_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "writes": 0,
            "read_only": read_only,
            "fingerprint": loaded["fingerprint"],
            "dirty": False,
        }
    return {
        "success": True,
        "kind": "offline_folder",
        "definition_folder": str(definition),
        "database": _display_name(db, definition),
        "compatibility_level": int(getattr(db, "CompatibilityLevel", 0) or 0),
        "tables": int(db.Model.Tables.Count),
        "read_only": read_only,
        "read_only_reason": (
            "the TOM client dropped the DAX UDFs on load (UDF compatibility gate); a write would "
            "delete functions.tmdl"
            if read_only
            else None
        ),
        "note": (
            "Offline TMDL lane: TOM model edits are written back through TmdlSerializer. No engine: "
            "DAX, refresh and traces are unavailable. Reconnect to Desktop with 01_Connection ensure."
        ),
    }


def _load(definition: Path) -> Dict[str, Any]:
    """Fingerprint then deserialise ``definition`` (fingerprint FIRST: a change in between is
    then caught by the next persist's check instead of being baked into the baseline)."""
    from core.tmdl.tom_check import load_database_from_folder

    fingerprint = _fingerprint(definition)
    loaded = load_database_from_folder(str(definition))
    if not loaded or not loaded.get("database"):
        return {"database": None, "error": (loaded or {}).get("error"), "fingerprint": fingerprint}
    return {
        "database": loaded["database"],
        "udf_gate": bool(loaded.get("udf_gate")),
        "fingerprint": fingerprint,
    }


def is_active() -> bool:
    return _state is not None


def info() -> Optional[Dict[str, Any]]:
    with _lock:
        if _state is None:
            return None
        return {
            "definition_folder": str(_state["definition"]),
            "writes": _state["writes"],
            "connected_at": _state["connected_at"],
            "pending_unpersisted_changes": bool(_state.get("dirty")),
        }


def _display_name(db: Any, definition: Path) -> str:
    """TMDL often leaves Database.Name unset: fall back to the .SemanticModel folder name."""
    name = getattr(db, "Name", None)
    if name and str(name) != "None":
        return str(name)
    folder = definition.parent if definition.name.lower() == "definition" else definition
    return folder.name.rsplit(".SemanticModel", 1)[0] or folder.name


def database() -> Any:
    with _lock:
        return _state["database"] if _state else None


def disconnect() -> None:
    global _state
    with _lock:
        _state = None


# ---------------------------------------------------------------------------
# Concurrency (N-23): the shared TOM tree is not thread-safe, so the dispatcher runs
# READ ops under a shared scope and WRITE ops under an exclusive one. A read scope also
# tells ``server_db_model`` NOT to mark the database dirty (a read cannot need a rollback).
# ---------------------------------------------------------------------------
_SCOPE_WAIT_S = 300.0


class _RWLock:
    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._readers = 0
        self._writer = False
        self._writers_waiting = 0

    def acquire(self, write: bool, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self._cond:
            if write:
                self._writers_waiting += 1
            try:
                while self._writer or (self._readers if write else self._writers_waiting):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    self._cond.wait(remaining)
                if write:
                    self._writer = True
                else:
                    self._readers += 1
                return True
            finally:
                if write:
                    self._writers_waiting -= 1

    def release(self, write: bool) -> None:
        with self._cond:
            if write:
                self._writer = False
            else:
                self._readers -= 1
            self._cond.notify_all()


_rw = _RWLock()
_tls = threading.local()


@contextlib.contextmanager
def op_scope(write: bool) -> Iterator[None]:
    """Dispatcher scope for one op on the offline lane (a no-op when the lane is inactive).

    Nested scopes on one thread just run inside the outer one. A READ scope that cannot be
    acquired within ``_SCOPE_WAIT_S`` proceeds unlocked (logged) instead of wedging the server;
    a WRITE scope raises :class:`OfflineModelError` instead (nothing is written).
    """
    if _state is None or getattr(_tls, "mode", None) is not None:
        yield
        return
    got = _rw.acquire(write, _SCOPE_WAIT_S)
    if not got and write:
        # A write must never run unlocked next to another write on the shared database.
        raise OfflineBusy(
            f"offline lane busy: another operation held the model for {_SCOPE_WAIT_S}s; "
            "nothing was written - retry when it finishes"
        )
    if not got:
        logger.warning(
            "offline lane: %s scope not acquired in %ss; proceeding unlocked",
            "write" if write else "read",
            _SCOPE_WAIT_S,
        )
    _tls.mode = "write" if write else "read"
    try:
        yield
    finally:
        _tls.mode = None
        if got:
            _rw.release(write)


class _OfflineServer:
    """Stands in for an AMO Server for managers that call ``server.Disconnect()``."""

    def Disconnect(self) -> None:  # noqa: N802 - AMO API name
        """Every manager calls this in ``finally``: re-read the folder when the handed-out
        database was mutated and not persisted (the offline counterpart of dropping an
        AMO connection with uncommitted changes). Never raises."""
        try:
            _discard_unpersisted()
        except Exception as e:  # noqa: BLE001 - must not mask the manager's own result
            logger.warning("offline lane: could not discard unpersisted changes: %s", e)


def server_db_model():
    """(server, db, model) triple for ``amo_helpers.get_server_db_model``."""
    with _lock:
        if _state is None:
            return None, None, None
        if getattr(_tls, "mode", None) != "read":
            _state["dirty"] = True
        db = _state["database"]
    return _OfflineServer(), db, db.Model


def _discard_unpersisted() -> bool:
    with _lock:
        if _state is None or not _state.get("dirty"):
            return False
    rollback()
    return True


def rollback() -> None:
    """Throw away unpersisted in-memory edits by re-reading the folder (N-02).

    ``Model.UndoLocalChanges`` throws on a disconnected model, so this is the offline
    lane's undo. The previous ``Database`` object stays valid but stale; callers must not
    keep using a ``model`` they obtained before. Raises :class:`OfflineModelError` when the
    folder cannot be re-read (the old database is then kept, still marked dirty).
    """
    with _lock:
        if _state is None:
            raise OfflineModelError("offline lane is not active")
        definition: Path = _state["definition"]
        loaded = _load(definition)
        if not loaded.get("database"):
            raise OfflineModelError(
                f"could not re-read {definition} to roll back: {loaded.get('error') or 'unknown error'}"
            )
        _state["database"] = loaded["database"]
        _state["fingerprint"] = loaded["fingerprint"]
        _state["read_only"] = bool(loaded.get("udf_gate"))
        _state["dirty"] = False


def _serialize_to(db: Any, folder: Path) -> None:
    from Microsoft.AnalysisServices.Tabular import TmdlSerializer  # type: ignore

    TmdlSerializer.SerializeDatabaseToFolder(db, str(folder))


_CRLF, _LF = b"\r\n", b"\n"
_STAGE_SUFFIX = ".mcp_new"


def _eol_counts(data: bytes) -> Tuple[int, int]:
    crlf = data.count(_CRLF)
    return crlf, data.count(_LF) - crlf


def _dominant_eol(data: bytes) -> Optional[bytes]:
    crlf, lf = _eol_counts(data)
    if not crlf and not lf:
        return None
    return _CRLF if crlf >= lf else _LF


def _with_eol(data: bytes, eol: bytes) -> bytes:
    lf = data.replace(_CRLF, _LF)
    return lf if eol == _LF else lf.replace(_LF, _CRLF)


def _tree_eol(dst: Path, rels: List[Path]) -> bytes:
    """The majority line ending of the existing tree (CRLF = the serializer's own, when empty)."""
    crlf = lf = 0
    for rel in rels:
        try:
            c, n = _eol_counts((dst / rel).read_bytes())
        except OSError:
            continue
        crlf += c
        lf += n
    return _LF if lf > crlf else _CRLF


Plan = Dict[str, Any]


def _plan_sync(src: Path, dst: Path) -> Plan:
    """What it takes to make ``dst`` equal ``src`` for .tmdl files (N-20).

    Files compare modulo line endings; a file that has to be written keeps ITS existing
    ending (new files: the tree's majority) so a one-line edit in an LF checkout is a
    one-line diff, not 100 rewritten files.
    """
    src_files = {p.relative_to(src) for p in src.rglob("*.tmdl")}
    dst_files = {p.relative_to(dst) for p in dst.rglob("*.tmdl")}
    tree_eol = _tree_eol(dst, sorted(dst_files))
    changed: List[Tuple[Path, bytes]] = []
    added: List[Tuple[Path, bytes]] = []
    for rel in sorted(src_files):
        new = (src / rel).read_bytes()
        d = dst / rel
        if not d.exists():
            added.append((rel, _with_eol(new, tree_eol)))
            continue
        old = d.read_bytes()
        if _with_eol(old, _LF) == _with_eol(new, _LF):
            continue
        changed.append((rel, _with_eol(new, _dominant_eol(old) or tree_eol)))
    return {"changed": changed, "added": added, "removed": sorted(dst_files - src_files)}


def _apply_plan(plan: Plan, dst: Path) -> Dict[str, List[str]]:
    """Stage every write as ``<file>.mcp_new``, swap them in once ALL are staged, delete last (N-03).

    A failure while staging leaves every real file untouched (staged leftovers are removed);
    a failure during the swap raises with some files already replaced - the caller restores
    from its backup.
    """
    staged: List[Tuple[Path, Path]] = []
    try:
        for rel, data in plan["changed"] + plan["added"]:
            final = dst / rel
            final.parent.mkdir(parents=True, exist_ok=True)
            tmp = final.with_name(final.name + _STAGE_SUFFIX)
            tmp.write_bytes(data)
            staged.append((tmp, final))
        for tmp, final in list(staged):
            os.replace(tmp, final)
            staged.remove((tmp, final))
        for rel in plan["removed"]:
            (dst / rel).unlink()
    finally:
        for tmp, _final in staged:
            with contextlib.suppress(OSError):
                tmp.unlink()
    return {
        "changed": [str(r) for r, _ in plan["changed"]],
        "added": [str(r) for r, _ in plan["added"]],
        "removed": [str(r) for r in plan["removed"]],
    }


def _sync_tree(src: Path, dst: Path) -> Dict[str, List[str]]:
    """Make ``dst`` equal ``src`` for .tmdl files; report what changed."""
    return _apply_plan(_plan_sync(src, dst), dst)


def _restore_tree(backup: Path, dst: Path) -> List[str]:
    """Best-effort: put ``dst`` back to ``backup`` (all .tmdl files). Returns what could not be restored."""
    leftovers: List[str] = []
    for stray in list(dst.rglob("*" + _STAGE_SUFFIX)):
        try:
            stray.unlink()
        except OSError as e:
            leftovers.append(f"{stray.name} (staged copy not removed: {e})")
    backup_files = {p.relative_to(backup) for p in backup.rglob("*.tmdl")}
    for rel in sorted(backup_files):
        want, target = (backup / rel).read_bytes(), dst / rel
        try:
            if not target.exists() or target.read_bytes() != want:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(want)
        except OSError as e:
            leftovers.append(f"{rel} ({e})")
    for rel in sorted({p.relative_to(dst) for p in dst.rglob("*.tmdl")} - backup_files):
        try:
            (dst / rel).unlink()
        except OSError as e:
            leftovers.append(f"{rel} (added file not removed: {e})")
    return leftovers


def persist(label: str) -> Dict[str, Any]:
    """Write the in-memory database back to ``definition/`` (staged, validated, with rollback)."""
    with _lock:
        if _state is None:
            raise OfflineModelError("offline lane is not active")
        definition: Path = _state["definition"]
        db = _state["database"]
        if _state.get("read_only"):
            raise OfflineModelError(
                f"{label}: this offline load is read-only (the TOM client dropped the UDFs); refusing to write."
            )
        owner = _desktop_has_open(definition)
        if owner:
            raise OfflineModelError(
                f"Power BI Desktop opened this project ({owner}) since connect_folder; refusing to write."
            )
        moved = _fingerprint_diff(_state["fingerprint"], _fingerprint(definition))
        if moved:
            shown = ", ".join(moved[:10]) + (
                f" (+{len(moved) - 10} more)" if len(moved) > 10 else ""
            )
            raise OfflineFolderChanged(
                f"offline_folder_changed: {label}: the definition folder was edited outside this lane "
                f"since connect_folder / the last write ({shown}); refusing to overwrite those edits. "
                "The in-memory edit is discarded and the next call re-reads the current files "
                "(or run 01_Connection connect_folder again); then repeat the change."
            )
        work = Path(tempfile.mkdtemp(prefix="mcp_offline_"))
        backup = Path(tempfile.mkdtemp(prefix="mcp_offline_bak_"))
        try:
            out = work / "definition"
            _serialize_to(db, out)
            shutil.copytree(definition, backup / "definition", dirs_exist_ok=True)
            try:
                diff = _sync_tree(out, definition)
                from core.tmdl.tom_check import tom_deserialize_check

                check = tom_deserialize_check(str(definition))
                verified = check.get("ok") if isinstance(check, dict) else None
                if verified is False:
                    raise OfflineModelError(
                        f"{label}: the written TMDL failed to deserialise; restored the previous files. "
                        f"{(check or {}).get('error') if isinstance(check, dict) else ''}"
                    )
            except (KeyboardInterrupt, SystemExit):
                _restore_tree(backup / "definition", definition)
                raise
            except Exception as exc:
                leftovers = _restore_tree(backup / "definition", definition)
                if isinstance(exc, OfflineModelError) and not leftovers:
                    raise
                if leftovers:
                    detail = (
                        f"{label}: writing the TMDL folder failed ({type(exc).__name__}: {exc}) and the "
                        "restore is INCOMPLETE; check by hand: " + "; ".join(leftovers[:10])
                    )
                else:
                    detail = (
                        f"{label}: writing the TMDL folder failed ({type(exc).__name__}: {exc}); "
                        "the previous files were restored."
                    )
                raise OfflineModelError(detail) from exc
            _state["writes"] += 1
            _state["fingerprint"] = _fingerprint(definition)
            _state["dirty"] = False
            result = {"offline_write": True, "definition_folder": str(definition), "files": diff}
            if verified is None:
                result["offline_warning"] = (
                    "The written TMDL could not be re-parsed for verification "
                    f"({(check or {}).get('reason') if isinstance(check, dict) else 'unavailable'})."
                )
            return result
        finally:
            shutil.rmtree(work, ignore_errors=True)
            shutil.rmtree(backup, ignore_errors=True)
