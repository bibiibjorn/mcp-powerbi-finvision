"""Durable write journal for live TOM edits (JR-01).

Live model edits exist only in Power BI Desktop's memory until Desktop saves, so a bridge reload
(``include_model`` included) or a crash silently loses them, and ``edit_ledger`` - which only
*refuses* the reload - forgets on restart. This journal turns that loss into a replay:

* :func:`begin` / :func:`finish` are called by ``write_lane.commit`` around ``model.SaveChanges()``
  (the single choke point of every live-TOM write). ``begin`` serialises the mutated model (the
  POST image), reads the committed model (the PRE image - from the previous commit's cache when
  Desktop's ``ModifiedTime`` proves nothing changed since, else from a fresh read of the server,
  which has not seen this save yet) and diffs the two into per-object changes
  (:mod:`core.operations.journal_images`). ``finish`` appends ONE JSON line to
  ``output/journal/<project>.jsonl`` (append + fsync) with the pre- and post-image of every touched
  object. A journal failure NEVER fails the user's write: it becomes a ``journal_warning``.
* A verified save appends a ``flush`` record (:func:`mark_flushed`); entries at or below its
  ``up_to_seq`` are "flushed" (on disk), the rest are "unflushed" (live only) and are what
  ``02_Model_Operations model replay`` re-applies after a discarded reload or a crash.
* Images never contain credentials (``SerializeOptions`` leaves restricted information out); call
  arguments are sanitised (secret-looking keys and ``Password=`` style fragments redacted, long text
  capped).

The journal is per project (the connected PBIP path; the database name when unknown) so it survives
a Desktop restart, where the database id changes.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from core.operations import journal_images as images

logger = logging.getLogger(__name__)

__all__ = [
    "SESSION_ID",
    "begin",
    "call_scope",
    "configure",
    "discard",
    "finish",
    "is_enabled",
    "journal_dir",
    "mark_flushed",
    "read_journal",
    "record_external",
    "reset",
    "resolve_key",
    "sanitize",
    "unflushed_summary",
]

SESSION_ID = uuid.uuid4().hex[:12]
JOURNAL_VERSION = 1
_MAX_TEXT = 2000
_MAX_ITEMS = 50
_MAX_DEPTH = 6

_STATE_LOCK = threading.RLock()
_OVERRIDE: Dict[str, Any] = {"enabled": None, "directory": None}
_SEQ: Dict[str, int] = {}
#: file size right after OUR last append, per key: another process appending changes it, which
#: forces a re-scan of the sequence numbers (N-26)
_SEQ_SIZE: Dict[str, int] = {}
_BASELINES: Dict[str, Dict[str, Any]] = {}
#: per-journal unflushed index, validated by (size, mtime_ns): the bridge asks on every status /
#: save / reload and must not re-parse every journal each time (N-24)
_INDEX: Dict[str, Dict[str, Any]] = {}
_LOCK_STATE = threading.local()
_MAX_BYTES_DEFAULT = 64 * 1024 * 1024
_RETENTION_DAYS_DEFAULT = 30
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,127}$")
_CALL: "contextvars.ContextVar[Optional[Dict[str, Any]]]" = contextvars.ContextVar(
    "write_journal_call", default=None
)


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------
def configure(*, enabled: Optional[bool] = None, directory: Any = None) -> None:
    """Override the journal switch / folder (tests; a config file works too)."""
    with _STATE_LOCK:
        if enabled is not None:
            _OVERRIDE["enabled"] = bool(enabled)
        if directory is not None:
            _OVERRIDE["directory"] = Path(directory)


def reset() -> None:
    """Forget overrides and in-process caches (tests)."""
    with _STATE_LOCK:
        _OVERRIDE["enabled"] = None
        _OVERRIDE["directory"] = None
        _SEQ.clear()
        _SEQ_SIZE.clear()
        _BASELINES.clear()
        _INDEX.clear()


def _cfg(key: str, default: Any) -> Any:
    try:
        from core.config.config_manager import config

        value = config.get(f"model_write.{key}", default)
        return default if value is None else value
    except Exception:  # noqa: BLE001 - config is optional
        return default


def is_enabled() -> bool:
    if _OVERRIDE["enabled"] is not None:
        return bool(_OVERRIDE["enabled"])
    return bool(_cfg("journal_enabled", True))


def journal_dir() -> Path:
    if _OVERRIDE["directory"] is not None:
        return Path(_OVERRIDE["directory"])
    env = os.environ.get("MCP_PBI_JOURNAL_DIR")
    if env:
        return Path(env)
    configured = _cfg("journal_dir", None)
    if configured:
        return Path(str(configured))
    return Path(__file__).resolve().parents[2] / "output" / "journal"


# ---------------------------------------------------------------------------
# dispatcher context
# ---------------------------------------------------------------------------
@contextlib.contextmanager
def call_scope(tool: str, label: str, args: Optional[Dict[str, Any]]) -> Iterator[None]:
    """Tell commits made inside which tool call they belong to (set by the dispatcher)."""
    token = _CALL.set({"tool": tool, "label": label, "args": args})
    try:
        yield
    finally:
        _CALL.reset(token)


# ---------------------------------------------------------------------------
# sanitising
# ---------------------------------------------------------------------------
_SECRET_KEY_RE = re.compile(
    r"(pass(word|wd|phrase)?|pwd|secret|token|api[_-]?key|access[_-]?key|account[_-]?key|"
    r"credential|connection[_-]?string|bearer|authorization|private[_-]?key|sharedaccess)",
    re.IGNORECASE,
)
_SECRET_FRAGMENT_RE = re.compile(
    r"(?i)\b(password|pwd|secret|token|accountkey|sharedaccesssignature|api[_-]?key|"
    r"client[_-]?secret|access[_-]?key)\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^;\"'\s]+)"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
REDACTED = "***redacted***"


def _clean_text(text: str) -> str:
    text = _SECRET_FRAGMENT_RE.sub(lambda m: f"{m.group(1)}={REDACTED}", text)
    text = _BEARER_RE.sub(f"Bearer {REDACTED}", text)
    if len(text) > _MAX_TEXT:
        text = text[:_MAX_TEXT] + f"...[+{len(text) - _MAX_TEXT} chars]"
    return text


def sanitize(value: Any, *, _depth: int = 0) -> Any:
    """JSON-safe copy of call arguments: secrets redacted, long text / lists capped."""
    if _depth > _MAX_DEPTH:
        return "<nested>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _clean_text(value)
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for k, v in value.items():
            key = str(k)
            out[key] = REDACTED if _SECRET_KEY_RE.search(key) else sanitize(v, _depth=_depth + 1)
        return out
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        out_list = [sanitize(v, _depth=_depth + 1) for v in items[:_MAX_ITEMS]]
        if len(items) > _MAX_ITEMS:
            out_list.append(f"...[+{len(items) - _MAX_ITEMS} items]")
        return out_list
    return _clean_text(str(value))


# ---------------------------------------------------------------------------
# identity: which journal file
# ---------------------------------------------------------------------------
def _safe_name(text: str, limit: int = 60) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", text or "").strip("._-")
    return (cleaned or "model")[:limit]


def _connected_project() -> Optional[str]:
    try:
        from core.autonomous.edit_ledger import _connected_project as project

        return project()
    except Exception:  # noqa: BLE001
        return None


def resolve_key(project: Optional[str], database_name: Optional[str]) -> str:
    """Journal file stem: the project when known (stable across Desktop restarts), else the db."""
    if project:
        try:
            from core.autonomous.edit_ledger import norm_path

            digest = hashlib.sha1(norm_path(project).encode("utf-8")).hexdigest()[:8]
        except Exception:  # noqa: BLE001
            digest = hashlib.sha1(str(project).encode("utf-8")).hexdigest()[:8]
        path = Path(str(project))
        stem = path.stem if path.suffix.lower() == ".pbip" else path.name
        return f"{_safe_name(stem)}-{digest}"
    return _safe_name(database_name or "model")


def _identity(model: Any = None) -> Tuple[str, Dict[str, Any]]:
    db = getattr(model, "Database", None) if model is not None else None
    name = str(getattr(db, "Name", "") or "") or None
    db_id = str(getattr(db, "ID", "") or "") or None
    project = _connected_project()
    if name is None and model is None:
        try:
            from core.infrastructure.connection_state import connection_state

            name = connection_state.query_executor._get_database_name()  # noqa: SLF001
        except Exception:  # noqa: BLE001
            name = None
    ident = {"project": project, "database_name": name, "database_id": db_id}
    return resolve_key(project, name), ident


# ---------------------------------------------------------------------------
# reading the model (CLR side; tests replace these)
# ---------------------------------------------------------------------------
def serialize_model(model: Any) -> Dict[str, Any]:
    """TMSL of a connected model (no timestamps, no restricted information)."""
    import Microsoft.AnalysisServices.Tabular as tabular  # type: ignore

    opts = tabular.SerializeOptions()
    opts.IgnoreTimestamps = True
    return json.loads(tabular.JsonSerializer.SerializeObject(model, opts))


def fetch_committed(model: Any) -> Dict[str, Any]:
    """TMSL of the COMMITTED model, read over a fresh connection (the pending save is not in it)."""
    from core.infrastructure.connection_state import connection_state
    from core.operations.amo_helpers import get_server_db_model, safe_disconnect

    server, _db, fresh, err = get_server_db_model(connection_state)
    if server is None or fresh is None:
        raise RuntimeError(f"cannot read the committed model: {err or 'no connection'}")
    try:
        return serialize_model(fresh)
    finally:
        safe_disconnect(server)


def _token(model: Any) -> Optional[str]:
    parts = [getattr(model, "ModifiedTime", None), getattr(model, "StructureModifiedTime", None)]
    if all(p is None for p in parts):
        return None
    return "|".join(str(p) for p in parts)


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------
def valid_key(key: Any) -> bool:
    """A journal name that cannot leave the journal directory (N-25)."""
    return isinstance(key, str) and bool(_KEY_RE.match(key)) and ".." not in key


def _path(key: str) -> Path:
    if not valid_key(key):
        raise ValueError(f"invalid journal name {key!r}")
    return journal_dir() / f"{key}.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _iter_records(path: Path) -> Iterator[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    yield {"type": "corrupt"}
                    continue
                if isinstance(rec, dict):
                    yield rec
    except FileNotFoundError:
        return


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _next_seq(key: str) -> int:
    with _STATE_LOCK:
        path = _path(key)
        if key not in _SEQ or _SEQ_SIZE.get(key) != _size(path):
            top = 0  # first use, or another process appended since our last write: re-scan
            for rec in _iter_records(path):
                if isinstance(rec.get("seq"), int):
                    top = max(top, rec["seq"])
                if isinstance(rec.get("up_to_seq"), int):
                    top = max(top, rec["up_to_seq"])
            _SEQ[key] = top
        _SEQ[key] += 1
        return _SEQ[key]


@contextlib.contextmanager
def _file_lock(key: str, timeout_s: float = 5.0) -> Iterator[None]:
    """Cross-process lock on ``<key>.lock`` around sequence allocation + append (N-26).

    Best effort: a lock that cannot be taken within *timeout_s* (or on a platform without
    msvcrt/fcntl) never fails the user's write - the size check in :func:`_next_seq` still
    repairs the sequence on the next write.
    """
    held = getattr(_LOCK_STATE, "held", None)
    if held is None:
        held = _LOCK_STATE.held = set()
    if key in held:  # re-entrant in this thread
        yield
        return
    fh = None
    mode = None  # "msvcrt" | "fcntl" once the lock is held
    try:
        lock_path = journal_dir() / f"{key}.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(lock_path, "a+b")
        deadline = time.monotonic() + timeout_s
        try:
            import msvcrt  # type: ignore

            fh.seek(0)
            while True:
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    mode = "msvcrt"
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        break
                    time.sleep(0.02)
        except ImportError:
            try:
                import fcntl  # type: ignore

                while True:
                    try:
                        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        mode = "fcntl"
                        break
                    except OSError:
                        if time.monotonic() > deadline:
                            break
                        time.sleep(0.02)
            except ImportError:
                pass
    except Exception as exc:  # noqa: BLE001 - locking is an extra safety net only
        logger.debug("write journal: lock unavailable for %s: %s", key, exc)
    held.add(key)
    try:
        yield
    finally:
        held.discard(key)
        if fh is not None:
            try:
                if mode == "msvcrt":
                    import msvcrt  # type: ignore

                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                elif mode == "fcntl":
                    import fcntl  # type: ignore

                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except Exception:  # noqa: BLE001
                pass
            try:
                fh.close()
            except Exception:  # noqa: BLE001
                pass


def _ends_with_newline(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            if fh.tell() == 0:
                return True
            fh.seek(-1, os.SEEK_END)
            return fh.read(1) == b"\n"
    except FileNotFoundError:
        return True


def _append(key: str, record: Dict[str, Any]) -> None:
    data = (json.dumps(record, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")
    path = _path(key)
    with _STATE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _file_lock(key):
            if not _ends_with_newline(path):
                # a crash mid-append left a torn tail: end that line so this record is its own
                # (the fragment stays a counted corrupt line; N-14)
                data = b"\n" + data
            with open(path, "ab") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            _SEQ_SIZE[key] = _size(path)


def read_journal(key: str) -> Dict[str, Any]:
    """Entries of one journal (oldest first) annotated with ``flushed`` / ``reverted_by``."""
    path = _path(key)
    entries: List[Dict[str, Any]] = []
    flush_upto = 0
    corrupt = 0
    for rec in _iter_records(path):
        kind = rec.get("type")
        if kind == "write":
            entries.append(rec)
        elif kind == "flush":
            flush_upto = max(flush_upto, int(rec.get("up_to_seq") or 0))
        elif kind == "corrupt":
            corrupt += 1
    reverted: Dict[str, str] = {}
    for e in entries:
        for rid in (e.get("meta") or {}).get("reverts") or []:
            reverted[rid] = e["id"]
    for e in entries:
        e["flushed"] = int(e.get("seq") or 0) <= flush_upto
        if e["id"] in reverted:
            e["reverted_by"] = reverted[e["id"]]
    return {
        "key": key,
        "path": str(path),
        "entries": entries,
        "flush_upto": flush_upto,
        "corrupt_lines": corrupt,
    }


def list_journals() -> List[Dict[str, Any]]:
    """``[{key, path, modified}]`` of every journal file, newest first."""
    try:
        files = sorted(journal_dir().glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return []
    out = []
    for p in files:
        if not valid_key(p.stem):
            continue
        try:
            out.append({"key": p.stem, "path": str(p), "modified": p.stat().st_mtime})
        except OSError:
            continue
    return out


def _is_countable(entry: Dict[str, Any]) -> bool:
    return bool(entry.get("changes")) and not entry.get("degraded")


# ---------------------------------------------------------------------------
# recording (called by write_lane.commit)
# ---------------------------------------------------------------------------
class Pending:
    """State captured before ``SaveChanges``; :func:`finish` turns it into a journal line."""

    def __init__(self, key, ident, label, meta, model):
        self.key = key
        self.ident = ident
        self.label = label
        self.meta = meta or {}
        self.model = model
        self.post_map: Optional[Dict[str, Dict[str, Any]]] = None
        self.changes: List[Dict[str, Any]] = []
        self.degraded: Optional[str] = None
        self.pre_source = ""
        self.capture_ms: Optional[int] = None


def begin(model: Any, label: str, meta: Optional[Dict[str, Any]] = None) -> Optional[Pending]:
    """Capture the pre/post images for the save about to happen. Never raises."""
    if not is_enabled():
        return None
    key = "unknown"
    ident: Dict[str, Any] = {}
    try:
        if getattr(model, "HasLocalChanges", True) is False:
            return None
        key, ident = _identity(model)
        pending = Pending(key, ident, label, meta, model)
        started = time.monotonic()
        try:
            post_map = images.split_model(serialize_model(model))
            token = _token(model)
            base = _BASELINES.get(key)
            if base is not None and token is not None and base.get("token") == token:
                pre_map = base["map"]
                pending.pre_source = "cache"
            else:
                pre_map = images.split_model(fetch_committed(model))
                pending.pre_source = "server"
            pending.post_map = post_map
            pending.changes = images.diff_maps(pre_map, post_map)
            pending.capture_ms = int((time.monotonic() - started) * 1000)
        except Exception as exc:  # noqa: BLE001 - journal must never fail the write
            _BASELINES.pop(key, None)
            pending.degraded = f"{type(exc).__name__}: {exc}"
            logger.warning("write journal: pre-image capture failed for %s: %s", label, exc)
        return pending
    except Exception as exc:  # noqa: BLE001
        logger.warning("write journal: begin failed for %s: %s", label, exc)
        pending = Pending(key, ident, label, meta, model)
        pending.degraded = f"{type(exc).__name__}: {exc}"
        return pending


def discard(pending: Optional[Pending]) -> None:
    """The save failed: nothing to record."""
    return None


def _model_generation() -> Optional[int]:
    try:
        from core.infrastructure.connection_state import connection_state

        return int(connection_state.model_generation)
    except Exception:  # noqa: BLE001
        return None


def _write_record(
    key: str,
    ident: Dict[str, Any],
    label: str,
    changes: List[Dict[str, Any]],
    info: Dict[str, Any],
    meta: Dict[str, Any],
    degraded: Optional[str],
    *,
    capture: Optional[Dict[str, Any]] = None,
) -> str:
    call = _CALL.get() or {}
    with _STATE_LOCK, _file_lock(key):  # sequence allocation + append are one critical section
        return _write_locked(key, ident, label, changes, info, meta, degraded, capture, call)


def _write_locked(key, ident, label, changes, info, meta, degraded, capture, call) -> str:
    tool = call.get("tool") or "?"
    op_label = call.get("label") or label
    operation = op_label.split(":", 1)[1] if ":" in str(op_label) else str(op_label)
    seq = _next_seq(key)
    entry_id = f"J{seq:06d}"
    rec: Dict[str, Any] = {
        "v": JOURNAL_VERSION,
        "type": "write",
        "id": entry_id,
        "seq": seq,
        "ts": _now_iso(),
        "session": SESSION_ID,
        "project": ident.get("project"),
        "database": ident.get("database_name"),
        "database_id": ident.get("database_id"),
        "tool": tool,
        "operation": operation,
        "label": label,
        "args": sanitize(call.get("args")) if call.get("args") is not None else None,
        "refs": [c["ref"] for c in changes],
        "kinds": images.count_by_kind(changes),
        "changes": changes,
        "result": {
            "success": True,
            **{k: info[k] for k in ("applied", "post_write_warning", "client_error") if k in info},
        },
        "model_generation": _model_generation(),
        "flushed": False,
    }
    if meta:
        rec["meta"] = sanitize(meta)
    if degraded:
        rec["degraded"] = degraded
    if capture:
        rec["capture"] = capture
    if not changes and not degraded:
        rec["noop"] = True
    _append(key, rec)
    return entry_id


def finish(pending: Optional[Pending], info: Dict[str, Any]) -> Optional[str]:
    """Append the journal line for a save that landed; adds ``journal_warning`` to *info* on failure."""
    if pending is None:
        return None
    try:
        entry_id = _write_record(
            pending.key,
            pending.ident,
            pending.label,
            pending.changes,
            info,
            pending.meta,
            pending.degraded,
            capture=(
                {"pre_source": pending.pre_source, "ms": pending.capture_ms}
                if pending.pre_source
                else None
            ),
        )
        _enforce_retention(pending.key)
        if pending.degraded:
            info["journal_warning"] = (
                "The write was applied but its pre-image could not be captured "
                f"({pending.degraded}); it is listed in the journal without undo data."
            )
        elif pending.post_map is not None:
            _BASELINES[pending.key] = {"token": _token(pending.model), "map": pending.post_map}
        return entry_id
    except Exception as exc:  # noqa: BLE001 - never fail the user's write
        _BASELINES.pop(pending.key, None)
        logger.warning("write journal: append failed for %s: %s", pending.label, exc)
        info["journal_warning"] = (
            f"The write was applied but the journal could not be written ({type(exc).__name__}: "
            f"{exc}); it cannot be undone or replayed from the journal."
        )
        return None


def record_external(
    label: str,
    changes: List[Dict[str, Any]],
    info: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Journal a write that does not go through ``SaveChanges`` (UDF TMSL fallback). Never raises."""
    if not is_enabled() or not changes:
        return None
    info = info if info is not None else {}
    try:
        key, ident = _identity(None)
        _BASELINES.pop(key, None)
        return _write_record(key, ident, label, changes, info, {}, None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("write journal: external record failed for %s: %s", label, exc)
        info["journal_warning"] = f"The journal could not be written ({type(exc).__name__}: {exc})."
        return None


# ---------------------------------------------------------------------------
# flush marking + unflushed counts (bridge integration)
# ---------------------------------------------------------------------------
def _norm_sets(reports: Iterable[str], models: Iterable[str]):
    from core.autonomous import edit_ledger

    rs = {edit_ledger.norm_path(r) for r in reports if r}
    ms = {edit_ledger.norm_path(m) for m in models if m}
    return edit_ledger, rs, ms


def _strict_match(edit_ledger: Any, project: Optional[str], rs: set, ms: set) -> bool:
    """Does an entry recorded for *project* positively belong to these artifact folders?

    Unlike ``edit_ledger.matches_project`` (fail closed: unknown / unresolvable matches
    everything - right for "is anything unsaved?") an unknown or unresolvable project matches
    NOTHING here - right for "mark as saved" and "may I apply this journal" (N-15 / N-07).
    """
    if not project:
        return False
    reports, models = edit_ledger.project_artifacts(project)
    if (reports & rs) or (models & ms):
        return True
    own = edit_ledger.norm_path(project)
    return bool(own) and (own in rs or own in ms)


def same_project(entry_project: Optional[str], connected_project: Optional[str]) -> bool:
    """Strict: both projects known and the same project (path or resolved artifacts)."""
    if not entry_project or not connected_project:
        return False
    try:
        edit_ledger, _rs, _ms = _norm_sets([], [])
        if edit_ledger.norm_path(entry_project) == edit_ledger.norm_path(connected_project):
            return True
        reports, models = edit_ledger.project_artifacts(connected_project)
        return _strict_match(edit_ledger, entry_project, reports, models)
    except Exception:  # noqa: BLE001
        return False


def _journal_index(key: str) -> Dict[str, Any]:
    """Unflushed countable entries of one journal, cached until the file changes (N-24)."""
    path = _path(key)
    try:
        st = path.stat()
        stamp = (st.st_size, st.st_mtime_ns)
    except OSError:
        return {"unflushed": []}
    hit = _INDEX.get(key)
    if hit is not None and hit["stamp"] == stamp:
        return hit
    view = read_journal(key)
    idx = {
        "stamp": stamp,
        "unflushed": [
            {
                "id": e["id"],
                "seq": int(e.get("seq") or 0),
                "ts": e.get("ts"),
                "project": e.get("project"),
            }
            for e in view["entries"]
            if _is_countable(e) and not e["flushed"]
        ],
    }
    _INDEX[key] = idx
    return idx


def unflushed_summary(reports: Iterable[str], models: Iterable[str]) -> Dict[str, Any]:
    """``{"count", "ids", "journals"}`` of live-only entries for this project. Never raises.

    Fail closed: an entry of an unknown / unresolvable project counts for every project."""
    out: Dict[str, Any] = {"count": 0, "ids": [], "journals": []}
    if not is_enabled():
        return out
    try:
        edit_ledger, rs, ms = _norm_sets(reports, models)
        for j in list_journals():
            mine = [
                e
                for e in _journal_index(j["key"])["unflushed"]
                if edit_ledger._matches(e.get("project"), rs, ms)  # noqa: SLF001
            ]
            if mine:
                out["count"] += len(mine)
                out["ids"].extend(e["id"] for e in mine[-5:])
                out["journals"].append(j["key"])
    except Exception as exc:  # noqa: BLE001
        logger.debug("write journal: unflushed summary failed: %s", exc)
    return out


def mark_flushed(
    reports: Iterable[str],
    models: Iterable[str],
    *,
    before: Optional[float] = None,
    reason: str = "save_verified",
) -> int:
    """A save was verified on disk: flush this project's entries written at or before *before*
    (epoch seconds; None = all). Appends a checkpoint record instead of rewriting lines.

    Only entries that STRICTLY belong to the saved project are flushed: an entry of an unknown /
    unresolvable project (unsaved PBIX, moved folder) stays unflushed (N-15). The flush record is
    a watermark, so it stops below the oldest unflushed entry of another project."""
    if not is_enabled():
        return 0
    flushed = 0
    try:
        edit_ledger, rs, ms = _norm_sets(reports, models)
        for j in list_journals():
            key = j["key"]
            pending = _journal_index(key)["unflushed"]
            if not pending:
                continue
            mine = [e for e in pending if _strict_match(edit_ledger, e["project"], rs, ms)]
            others = [e["seq"] for e in pending if e not in mine]
            ceiling = min(others) - 1 if others else None
            todo = [
                e
                for e in mine
                if (ceiling is None or e["seq"] <= ceiling)
                and (before is None or _ts_epoch(e.get("ts")) <= before)
            ]
            if not todo:
                continue
            upto = max(e["seq"] for e in todo)
            _append(
                key,
                {
                    "v": JOURNAL_VERSION,
                    "type": "flush",
                    "ts": _now_iso(),
                    "session": SESSION_ID,
                    "up_to_seq": upto,
                    "count": len(todo),
                    "reason": reason,
                },
            )
            flushed += len(todo)
    except Exception as exc:  # noqa: BLE001 - flush marking must never break a save
        logger.warning("write journal: flush marking failed: %s", exc)
    return flushed


def _enforce_retention(key: str) -> None:
    """Keep a journal bounded (N-24): when the file outgrows ``model_write.journal_max_bytes``
    (default 64 MB) drop FLUSHED entries - those older than ``journal_retention_days`` (default
    30) first, then the oldest - until it is back under 75 % of the cap. Unflushed entries are
    never dropped; one ``flush`` watermark keeps their numbering. Never raises."""
    try:
        cap = int(_cfg("journal_max_bytes", _MAX_BYTES_DEFAULT))
        path = _path(key)
        if cap <= 0 or _size(path) <= cap:
            return
        days = float(_cfg("journal_retention_days", _RETENTION_DAYS_DEFAULT))
        with _STATE_LOCK, _file_lock(key):
            view = read_journal(key)
            entries = view["entries"]
            sizes = {
                e["id"]: len(json.dumps(e, separators=(",", ":"), ensure_ascii=True)) + 1
                for e in entries
            }
            keep = list(entries)
            total = sum(sizes.values())
            horizon = time.time() - days * 86400 if days > 0 else None
            dropped = []
            for e in entries:  # oldest first
                if e["flushed"] is not True:
                    continue
                aged = horizon is not None and _ts_epoch(e.get("ts")) < horizon
                if aged or total > cap * 0.75:
                    keep.remove(e)
                    dropped.append(e)
                    total -= sizes[e["id"]]
            if not dropped:
                return
            lines = []
            for e in keep:
                rec = {k: v for k, v in e.items() if k not in ("flushed", "reverted_by")}
                lines.append(json.dumps(rec, separators=(",", ":"), ensure_ascii=True))
            lines.append(
                json.dumps(
                    {
                        "v": JOURNAL_VERSION,
                        "type": "flush",
                        "ts": _now_iso(),
                        "session": SESSION_ID,
                        "up_to_seq": view["flush_upto"],
                        "count": len(dropped),
                        "reason": "retention",
                    },
                    separators=(",", ":"),
                    ensure_ascii=True,
                )
            )
            tmp = path.with_suffix(".jsonl.tmp")
            with open(tmp, "wb") as fh:
                fh.write(("\n".join(lines) + "\n").encode("ascii"))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
            _SEQ.pop(key, None)
            _INDEX.pop(key, None)
    except Exception as exc:  # noqa: BLE001 - retention must never break a write
        logger.warning("write journal: retention failed for %s: %s", key, exc)


def _ts_epoch(ts: Optional[str]) -> float:
    try:
        return (
            datetime.strptime(str(ts), "%Y-%m-%dT%H:%M:%S.%fZ")
            .replace(tzinfo=timezone.utc)
            .timestamp()
        )
    except (TypeError, ValueError):
        return 0.0


def parse_since(value: Any) -> Optional[float]:
    """``since`` argument -> epoch seconds. ISO-8601 or a relative ``15m`` / ``2h`` / ``1d``."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([smhd])", text.lower())
    if m:
        unit = {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]
        return time.time() - float(m.group(1)) * unit
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(
            f"since={value!r}: use an ISO timestamp or a relative value like 30m / 2h / 1d"
        )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()
