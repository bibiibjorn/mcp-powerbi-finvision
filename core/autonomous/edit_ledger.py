"""
Edit ledger — what this server has changed that Power BI Desktop has not seen yet
(or has in memory but not on disk).

Two independent kinds of pending state decide whether a bridge ``reload`` or
``save`` is safe (review J-01 / J-02 / J-07 / J-32):

* **Live-model (TOM) writes** — every successful write op the dispatcher runs
  that is NOT a PBIR/TMDL disk edit and not a bridge op (02_Model_Operations,
  03_Batch_Operations, 15_DAX_Refactor apply, 09 debug_loop live_tom, ...).
  They live only in Desktop's memory until Desktop saves. A reload re-reads
  disk and discards them, so ``op_reload`` refuses while any is pending for the
  target project. Only a disk-verified save (or a reload the caller explicitly
  allowed to discard them) clears them. Desktop's own ``hasUnsavedChanges`` flag
  is not enough on its own: the MS CLI emits ``appState.hasUnsavedChanges ===
  true`` so "unknown" arrives as ``false``.
* **Pending disk edits** — PBIR/TMDL files this server wrote. Fed by
  ``pbip_utils.register_write_listener`` (every ``save_json_file`` /
  ``update_json_file`` / ``notify_path_written``) plus the dispatcher hook for
  disk ops whose writers bypass pbip_utils (TMDL). Until a reload, Desktop's
  memory still holds the OLD report, so ANY save flushes that old copy OVER the
  edits: ``op_save`` refuses with ``conflict_pending_disk_and_memory``. A
  successful reload clears the PBIR entries; TMDL entries survive a plain reload (the
  CLI's reload sends ``reloadModelDefinition:false``) and clear when Desktop reopens the
  project (``17_Desktop_Bridge open``) or when a classified ``reload include_model=true``
  succeeded and the post-reload re-read of the live model shows no mismatch with the disk
  definition (an unreadable live model counts as no mismatch; the result says so).

Projects are keyed by artifact folder (``*.Report`` / ``*.SemanticModel``),
normalised (absolute, long-path, case-folded). An entry whose project could not
be determined is recorded under ``None`` and matches EVERY project (fail
closed). Everything here is in-process state — a server restart forgets it,
which fails OPEN, so the Desktop flag is still consulted too.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Caps so a long session cannot grow the ledger without bound.
_MAX_TOM_ENTRIES = 500
_MAX_DISK_PATHS_PER_PROJECT = 50
_RECENT_OWN_WRITES_S = 120.0

_LOCK = threading.RLock()
# project key (artifact dir, normalised) or None -> list of {"t", "label", "project"}
_TOM: List[Dict[str, Any]] = []
# artifact key or None -> {"kind": "pbir"|"tmdl", "first_at", "last_at", "count", "paths": [...]}
_DISK: Dict[Optional[str], Dict[str, Any]] = {}
# recent own write paths (normalised) -> monotonic-free wall time (for save verification)
_OWN_WRITES: Dict[str, float] = {}

_ARTIFACT_SUFFIXES = (".report", ".semanticmodel", ".dataset")


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------
def _long_path(p: str) -> str:
    """Expand an 8.3 short path on Windows (``C:\\PROGRA~1`` -> long form)."""
    if os.name != "nt" or "~" not in p:
        return p
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetLongPathNameW(p, buf, len(buf))  # type: ignore[attr-defined]
        if 0 < n < len(buf):
            return buf.value
    except Exception:  # noqa: BLE001 - best effort only
        pass
    return p


def norm_path(p: Optional[str]) -> str:
    """Absolute, long-path, separator-normalised, case-folded path ('' for None)."""
    if not p:
        return ""
    try:
        from core.utilities.pbip_utils import normalize_path

        p = normalize_path(str(p))
    except Exception:  # noqa: BLE001
        p = str(p)
    try:
        p = os.path.abspath(p)
    except (OSError, ValueError):
        pass
    p = _long_path(p)
    return os.path.normcase(os.path.normpath(p)).rstrip("\\/").lower()


def artifact_of(path: Optional[str]) -> Optional[str]:
    """Normalised ``*.Report`` / ``*.SemanticModel`` ancestor of *path*, or None."""
    n = norm_path(path)
    if not n:
        return None
    cur = n
    while True:
        if cur.endswith(_ARTIFACT_SUFFIXES):
            return cur
        parent = os.path.dirname(cur)
        if not parent or parent == cur:
            return None
        cur = parent


def _kind_of(artifact: Optional[str], default: str = "pbir") -> str:
    if artifact and artifact.endswith((".semanticmodel", ".dataset")):
        return "tmdl"
    return default


_RESOLVE_CACHE: Dict[str, Tuple[float, Tuple[Set[str], Set[str]]]] = {}
_RESOLVE_TTL_S = 10.0


def project_artifacts(path: Optional[str]) -> Tuple[Set[str], Set[str]]:
    """``(report_dirs, model_dirs)`` (normalised) a project path resolves to.

    Accepts a ``.pbip`` file, a ``.Report`` / ``.SemanticModel`` folder or a
    project folder (via ``core.pbip.pbir_model.resolve_pbip``). Empty sets when
    the path does not resolve. Cached briefly (the bridge asks per op).
    """
    key = norm_path(path)
    if not key:
        return set(), set()
    now = time.time()
    hit = _RESOLVE_CACHE.get(key)
    if hit is not None and now - hit[0] < _RESOLVE_TTL_S:
        return set(hit[1][0]), set(hit[1][1])
    reports: Set[str] = set()
    models: Set[str] = set()
    try:
        from core.pbip.pbir_model import resolve_pbip

        res = resolve_pbip(str(path))
        for r in res.get("reports") or []:
            if r.get("report_dir"):
                reports.add(norm_path(r["report_dir"]))
            if r.get("model_dir"):
                models.add(norm_path(r["model_dir"]))
        for m in res.get("models") or []:
            if m.get("model_dir"):
                models.add(norm_path(m["model_dir"]))
    except Exception as e:  # noqa: BLE001 - resolution is best effort
        logger.debug("edit_ledger: resolve_pbip(%s) failed: %s", path, e)
    art = artifact_of(path)
    if art:
        (models if _kind_of(art) == "tmdl" else reports).add(art)
    if len(_RESOLVE_CACHE) > 256:
        _RESOLVE_CACHE.clear()
    _RESOLVE_CACHE[key] = (now, (set(reports), set(models)))
    return reports, models


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------
def record_disk_write(path: Optional[str], *, kind: Optional[str] = None, source: str = "") -> None:
    """A PBIR/TMDL file (or folder) under *path* was written by this server.

    Paths outside any ``*.Report`` / ``*.SemanticModel`` folder (exports, HTML,
    screenshots) are ignored unless *path* is a project path (``.pbip`` /
    project folder), in which case every artifact it resolves to is recorded.
    ``path=None`` records a project-unknown edit (matches every project).
    """
    now = time.time()
    arts: List[Optional[str]]
    if path is None:
        arts = [None]
    else:
        art = artifact_of(path)
        if art is not None:
            arts = [art]
        else:
            reports, models = project_artifacts(path)
            if kind == "tmdl":
                arts = list(models)
            elif kind == "pbir":
                arts = list(reports)
            else:
                arts = list(reports | models)
            if not arts:
                return
        with _LOCK:
            _OWN_WRITES[norm_path(path)] = now
            if len(_OWN_WRITES) > 2000:
                cutoff = now - _RECENT_OWN_WRITES_S
                for k in [k for k, v in _OWN_WRITES.items() if v < cutoff]:
                    _OWN_WRITES.pop(k, None)
    with _LOCK:
        for art in arts:
            entry = _DISK.get(art)
            if entry is None:
                entry = {
                    "kind": kind or _kind_of(art),
                    "first_at": now,
                    "last_at": now,
                    "count": 0,
                    "paths": [],
                    "sources": [],
                }
                _DISK[art] = entry
            entry["last_at"] = now
            entry["count"] += 1
            if kind == "tmdl" or _kind_of(art) == "tmdl":
                entry["kind"] = "tmdl"
            if path is not None and len(entry["paths"]) < _MAX_DISK_PATHS_PER_PROJECT:
                np_ = str(path)
                if np_ not in entry["paths"]:
                    entry["paths"].append(np_)
            if source and source not in entry["sources"] and len(entry["sources"]) < 20:
                entry["sources"].append(source)


def _on_pbip_write(path: str) -> None:
    """``pbip_utils`` write-listener callback."""
    record_disk_write(path, source="pbip_utils")


def _connected_project() -> Optional[str]:
    """The project path of the model the server is connected to, if known."""
    try:
        from core.infrastructure.connection_state import connection_state

        info = connection_state.get_pbip_info() or {}
        return info.get("file_full_path") or info.get("pbip_folder_path") or None
    except Exception:  # noqa: BLE001
        return None


def _offline_lane_active() -> bool:
    try:
        from core.infrastructure import offline_model

        return offline_model.is_active()
    except Exception:  # noqa: BLE001
        return False


def record_live_write(label: str = "", *, project: Optional[str] = None) -> None:
    """A live-model (TOM) write succeeded; Desktop's memory now differs from disk.

    *project* defaults to the connected model's PBIP path; when that is unknown
    the entry matches every project (fail closed).
    """
    proj = project if project is not None else _connected_project()
    with _LOCK:
        _TOM.append({"t": time.time(), "label": label, "project": proj})
        if len(_TOM) > _MAX_TOM_ENTRIES:
            del _TOM[: len(_TOM) - _MAX_TOM_ENTRIES]


# Path-ish argument names disk-writing tools accept (fallback feed for writers
# that bypass pbip_utils, e.g. the TMDL bulk editor).
_PATH_ARG_KEYS = (
    "pbip_path",
    "tmdl_path",
    "report_path",
    "definition_path",
    "target_path",
    "project_path",
    "path",
)


def note_dispatch_write(label: str, args: Optional[Mapping[str, Any]] = None) -> None:
    """Dispatcher hook: called after every SUCCESSFUL write/destructive op.

    ``label`` is ``"<tool>:<op>"`` (``server.op_classification.op_label``).
    Bridge ops are ignored (the bridge maintains the ledger itself); ops listed
    in ``reload_hints.OPS_REQUIRING_RELOAD`` are disk edits; everything else
    that writes is a live-model write.
    """
    tool, _, op = (label or "").partition(":")
    if not tool or tool == "17_Desktop_Bridge":
        return
    try:
        from core.autonomous.reload_hints import is_reload_required

        disk = is_reload_required(tool, op)
    except Exception:  # noqa: BLE001
        disk = False
    if disk:
        kind = "tmdl" if tool == "02_TMDL_Operations" else "pbir"
        paths = []
        for key in _PATH_ARG_KEYS:
            val = (args or {}).get(key)
            if isinstance(val, str) and val.strip():
                paths.append(val)
        if not paths:
            proj = _connected_project()
            paths = [proj] if proj else []
        if not paths:
            record_disk_write(None, kind=kind, source=label)
        for p in paths:
            record_disk_write(p, kind=kind, source=label)
        return
    if _offline_lane_active():
        # N-10: an offline-lane model write is a disk write already in the project's git, not
        # Desktop memory that differs from disk; recording it would block every bridge reload.
        return
    record_live_write(label)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
def _matches(project: Optional[str], reports: Set[str], models: Set[str]) -> bool:
    if project is None:
        return True
    r, m = project_artifacts(project)
    if not r and not m:
        return True  # unresolvable project -> fail closed
    return bool((r & reports) or (m & models))


def _matches_strict(project: Optional[str], reports: Set[str], models: Set[str]) -> bool:
    if project is None:
        return False
    r, m = project_artifacts(project)
    return bool((r & reports) or (m & models))


def matches_project(
    project: Optional[str],
    reports: Iterable[str],
    models: Iterable[str],
    *,
    strict: bool = False,
) -> bool:
    """Does an entry recorded for *project* belong to these report / model artifact folders?

    Default (``strict=False``) is the fail-CLOSED rule for "is anything unsaved?": an unknown
    project (``None``) or one whose artifacts cannot be resolved matches everything.
    ``strict=True`` is the rule for "mark as saved / flushed" (N-15): only a project that
    resolves to one of these artifacts matches, so a save of project A never flushes work
    recorded for an unknown project.
    """
    rs, ms = {norm_path(r) for r in reports if r}, {norm_path(m) for m in models if m}
    if strict:
        return _matches_strict(project, rs, ms)
    return _matches(project, rs, ms)


def pending_tom_writes(reports: Iterable[str], models: Iterable[str]) -> List[Dict[str, Any]]:
    """Live-model writes not yet flushed that may belong to this project."""
    rs, ms = {norm_path(r) for r in reports if r}, {norm_path(m) for m in models if m}
    with _LOCK:
        entries = list(_TOM)
    return [e for e in entries if _matches(e.get("project"), rs, ms)]


def pending_disk_edits(reports: Iterable[str], models: Iterable[str]) -> List[Dict[str, Any]]:
    """Disk edits Desktop's memory has not picked up for this project."""
    rs, ms = {norm_path(r) for r in reports if r}, {norm_path(m) for m in models if m}
    out: List[Dict[str, Any]] = []
    with _LOCK:
        items = list(_DISK.items())
    for art, entry in items:
        if art is None or art in rs or art in ms:
            out.append(
                {
                    "artifact": art,
                    "kind": entry["kind"],
                    "count": entry["count"],
                    "first_at": entry["first_at"],
                    "last_at": entry["last_at"],
                    "paths": list(entry["paths"][:10]),
                    "sources": list(entry["sources"][:5]),
                }
            )
    return out


def recent_own_writes(since: float) -> Set[str]:
    """Normalised paths this process wrote at/after *since* (wall clock)."""
    with _LOCK:
        return {p for p, t in _OWN_WRITES.items() if t >= since}


# ---------------------------------------------------------------------------
# Clearing
# ---------------------------------------------------------------------------
def clear_tom(
    reports: Iterable[str], models: Iterable[str], *, before: Optional[float] = None
) -> int:
    """Drop live-model entries for this project (a verified save flushed them)."""
    rs, ms = {norm_path(r) for r in reports if r}, {norm_path(m) for m in models if m}
    with _LOCK:
        keep = []
        dropped = 0
        for e in _TOM:
            hit = _matches(e.get("project"), rs, ms) and (before is None or e["t"] <= before)
            if hit:
                dropped += 1
            else:
                keep.append(e)
        _TOM[:] = keep
    return dropped


def clear_disk(
    reports: Iterable[str], models: Iterable[str], *, kinds: Iterable[str] = ("pbir",)
) -> int:
    """Drop pending disk edits of *kinds* for this project (+ project-unknown ones)."""
    rs, ms = {norm_path(r) for r in reports if r}, {norm_path(m) for m in models if m}
    kset = set(kinds)
    with _LOCK:
        drop = [
            art
            for art, entry in _DISK.items()
            if (art is None or art in rs or art in ms) and entry["kind"] in kset
        ]
        for art in drop:
            _DISK.pop(art, None)
    return len(drop)


def reset() -> None:
    """Forget everything (tests; also safe to call after a Desktop restart)."""
    with _LOCK:
        _TOM.clear()
        _DISK.clear()
        _OWN_WRITES.clear()
    _RESOLVE_CACHE.clear()


def snapshot() -> Dict[str, Any]:
    """Diagnostic view of the whole ledger."""
    with _LOCK:
        return {
            "tom_writes": [dict(e) for e in _TOM[-20:]],
            "tom_write_count": len(_TOM),
            "disk_edits": {str(k): dict(v) for k, v in _DISK.items()},
        }


def _register() -> None:
    try:
        from core.utilities.pbip_utils import register_write_listener

        register_write_listener(_on_pbip_write)
    except Exception as e:  # noqa: BLE001
        logger.debug("edit_ledger: write listener registration failed: %s", e)


_register()

__all__ = [
    "norm_path",
    "artifact_of",
    "project_artifacts",
    "record_disk_write",
    "record_live_write",
    "note_dispatch_write",
    "matches_project",
    "pending_tom_writes",
    "pending_disk_edits",
    "recent_own_writes",
    "clear_tom",
    "clear_disk",
    "reset",
    "snapshot",
]
