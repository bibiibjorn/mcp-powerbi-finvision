"""
Write layer for every PBIR authoring engine (WP9 / deep review 2026-09-22).

One place that every report-file mutation goes through, so four guarantees
hold for ALL authoring ops instead of per-engine:

* **Locked read-modify-write (H-01).** ``update_json`` delegates to
  ``pbip_utils.update_json_file`` (process-wide per-path lock around the
  read AND the write), so two parallel MCP calls editing the same
  visual.json / page.json can no longer lose each other's update.
* **Real dry run (A-06).** Inside ``write_scope(dry_run=True)`` nothing is
  written: every write / delete / copy / move is RECORDED as a planned
  change instead (``planned_changes()``), and ``update_json`` computes the
  would-be result on a copy so the preview reports whether the file would
  actually change.
* **Failures surface (H-29).** A failed write raises ``WriteError`` instead
  of returning a bool nobody checks; the scope counts every file really
  written (``files_modified()``) so the gate can attach the reload hint to a
  PARTIAL write too.
* **Writes stay inside the report (J-06 part).** When the scope has
  ``allowed_roots``, any write/delete outside them raises ``WriteError``
  (a crafted page/visual id like ``..\\..\\x`` cannot escape the report).

Outside any scope (direct engine calls, tests) the helpers behave as plain
atomic writes -- no preview, no root check.
"""

from __future__ import annotations

import contextvars
import copy
import json
import logging
import os
import shutil
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Union

from core.utilities.pbip_utils import (
    SKIP_WRITE,
    detect_json_traits,
    notify_path_written,
    save_json_file,
    save_json_minimal,
    serialize_json,
    update_json_file,
)

logger = logging.getLogger(__name__)

PathLike = Union[str, "os.PathLike[str]"]

#: Cap on the planned-change list returned in a dry-run envelope.
MAX_PLANNED_CHANGES_REPORTED = 200


class WriteError(OSError):
    """A report-file write/delete/copy failed (or was refused)."""

    def __init__(self, message: str, path: Optional[PathLike] = None) -> None:
        super().__init__(message)
        self.path = str(path) if path is not None else None


@dataclass
class _Scope:
    dry_run: bool = False
    allowed_roots: List[Path] = field(default_factory=list)
    planned: List[Dict[str, Any]] = field(default_factory=list)
    written: List[str] = field(default_factory=list)

    def record(self, action: str, path: PathLike, **detail: Any) -> None:
        entry: Dict[str, Any] = {"action": action, "path": str(path)}
        entry.update({k: v for k, v in detail.items() if v is not None})
        self.planned.append(entry)

    def mark_written(self, path: PathLike) -> None:
        key = str(path)
        if key not in self.written:
            self.written.append(key)


_SCOPE: contextvars.ContextVar[Optional[_Scope]] = contextvars.ContextVar(
    "pbir_write_scope", default=None
)


@contextmanager
def write_scope(
    *, dry_run: bool = False, allowed_roots: Optional[List[PathLike]] = None
) -> Iterator[_Scope]:
    """Open a write scope for one authoring operation (used by ``gate_and_wrap``).

    Nested scopes inherit the outer scope (a delegated op shares its
    caller's ledger); ``dry_run`` is sticky once any enclosing scope set it.
    """
    outer = _SCOPE.get()
    if outer is not None:
        if dry_run and not outer.dry_run:
            outer.dry_run = True
        if allowed_roots:
            outer.allowed_roots.extend(_canon(r) for r in allowed_roots)
        yield outer
        return
    scope = _Scope(
        dry_run=bool(dry_run),
        allowed_roots=[_canon(r) for r in (allowed_roots or [])],
    )
    token = _SCOPE.set(scope)
    try:
        yield scope
    finally:
        _SCOPE.reset(token)


def current_scope() -> Optional[_Scope]:
    return _SCOPE.get()


def dry_run_active() -> bool:
    """True inside a dry-run scope: callers must not touch disk."""
    scope = _SCOPE.get()
    return bool(scope and scope.dry_run)


def allow_root(path: PathLike) -> None:
    """Add a write root to the active scope (e.g. an explicit export target)."""
    scope = _SCOPE.get()
    if scope is not None:
        scope.allowed_roots.append(_canon(path))


def files_modified() -> List[str]:
    scope = _SCOPE.get()
    return list(scope.written) if scope else []


def planned_changes() -> List[Dict[str, Any]]:
    scope = _SCOPE.get()
    return list(scope.planned) if scope else []


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _canon(path: PathLike) -> Path:
    p = Path(os.fspath(path))
    try:
        return p.resolve()
    except OSError:  # pragma: no cover
        return p.absolute()


def _check_root(path: PathLike) -> None:
    scope = _SCOPE.get()
    if scope is None or not scope.allowed_roots:
        return
    target = _canon(path)
    for root in scope.allowed_roots:
        try:
            target.relative_to(root)
            return
        except ValueError:
            continue
    raise WriteError(
        f"refused: {target} is outside the report being edited "
        f"({', '.join(str(r) for r in scope.allowed_roots)})",
        target,
    )


def long_path(path: PathLike) -> str:
    """``\\\\?\\``-prefixed absolute path on Windows when it nears MAX_PATH (H-34).

    OneDrive / deep repo paths plus PBIR's nested page/visual folders exceed
    260 chars easily; ``shutil`` then fails with WinError 206 mid-copy.
    """
    text = os.path.abspath(os.fspath(path))
    if os.name == "nt" and len(text) >= 240 and not text.startswith("\\\\?\\"):
        if text.startswith("\\\\"):
            return "\\\\?\\UNC\\" + text[2:]
        return "\\\\?\\" + text
    return text


def is_safe_segment(name: Any) -> bool:
    """True when *name* is usable as ONE path segment (page / visual / bookmark id).

    Rejects separators, ``..``, drive letters and control characters so an
    id taken from MCP args can never address a file outside its folder.
    """
    if not isinstance(name, str) or not name or name in (".", ".."):
        return False
    if any(ch in name for ch in '/\\:*?"<>|') or ".." in name:
        return False
    return all(ord(ch) >= 32 for ch in name)


# ---------------------------------------------------------------------------
# JSON writes
# ---------------------------------------------------------------------------


def read_json(path: PathLike) -> Optional[Any]:
    """Parse a JSON file (BOM tolerated); None when missing / unreadable."""
    try:
        with open(os.fspath(path), "rb") as f:
            raw = f.read()
    except OSError:
        return None
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as e:
        logger.warning(f"Invalid JSON in {path}: {e}")
        return None


def write_json(path: PathLike, data: Any) -> bool:
    """Write *data* (create or replace), keeping the existing file's byte traits.

    Dry run: records ``create`` / ``write`` and returns True. Raises
    ``WriteError`` when the write fails.
    """
    p = Path(os.fspath(path))
    _check_root(p)
    scope = _SCOPE.get()
    if scope is not None and scope.dry_run:
        scope.record("write" if p.exists() else "create", p)
        return True
    if not save_json_file(p, data):
        raise WriteError(f"failed to write {p}", p)
    if scope is not None:
        scope.mark_written(p)
    return True


def update_json(
    path: PathLike,
    mutate: Callable[[Any], Any],
    *,
    create_if_missing: bool = False,
    default_factory: Callable[[], Any] = dict,
) -> bool:
    """Locked read-modify-write (H-01). Returns True when the file (would) change.

    ``mutate(data)`` mutates in place, returns a replacement, or returns
    ``SKIP_WRITE``. In a dry run the mutation runs on a deep copy and
    nothing is written. Read / parse / write failures raise ``WriteError``;
    exceptions raised by ``mutate`` propagate unchanged.
    """
    p = Path(os.fspath(path))
    _check_root(p)
    user_mutate = mutate

    def mutate(data: Any) -> Any:  # noqa: F811 - guarded wrapper
        # A mutate that accidentally RETURNS a result (a change list, a
        # status dict) would silently replace the whole document: refuse a
        # replacement whose type differs from the loaded document.
        result = user_mutate(data)
        if result is not None and result is not SKIP_WRITE and type(result) is not type(data):
            raise WriteError(
                f"refused: mutate for {p.name} returned a {type(result).__name__} "
                f"replacement for a {type(data).__name__} document",
                p,
            )
        return result

    scope = _SCOPE.get()
    if scope is not None and scope.dry_run:
        raw: Optional[bytes]
        try:
            raw = p.read_bytes()
        except FileNotFoundError:
            raw = None
        except OSError as e:
            raise WriteError(f"cannot read {p}: {e}", p) from e
        if raw is None:
            if not create_if_missing:
                raise WriteError(f"JSON file not found: {p}", p)
            data = default_factory()
        else:
            try:
                data = json.loads(raw.decode("utf-8-sig"))
            except (ValueError, UnicodeDecodeError) as e:
                raise WriteError(f"invalid JSON in {p}: {e}", p) from e
        work = copy.deepcopy(data)
        result = mutate(work)
        if result is SKIP_WRITE:
            return False
        if result is not None:
            work = result
        new_bytes = serialize_json(work, detect_json_traits(raw))
        if raw is not None and new_bytes == raw:
            return False
        scope.record("create" if raw is None else "write", p)
        return True
    try:
        changed = update_json_file(
            p, mutate, create_if_missing=create_if_missing, default_factory=default_factory
        )
    except (FileNotFoundError, ValueError, UnicodeDecodeError) as e:
        raise WriteError(f"cannot update {p}: {e}", p) from e
    except OSError as e:
        raise WriteError(f"cannot update {p}: {e}", p) from e
    if changed and scope is not None:
        scope.mark_written(p)
    return changed


def write_bytes_minimal(path: PathLike, new_bytes: bytes, original_bytes: bytes) -> bool:
    """Byte-exact write (no re-serialisation) when *new_bytes* differ."""
    p = Path(os.fspath(path))
    _check_root(p)
    if new_bytes == original_bytes:
        return False
    scope = _SCOPE.get()
    if scope is not None and scope.dry_run:
        scope.record("write", p)
        return True
    try:
        written = save_json_minimal(p, new_bytes, original_bytes, raise_on_error=True)
    except OSError as e:
        raise WriteError(f"failed to write {p}: {e}", p) from e
    if written and scope is not None:
        scope.mark_written(p)
    return written


# ---------------------------------------------------------------------------
# Tree operations
# ---------------------------------------------------------------------------


def _retry_transient(fn: Callable[[], Any]) -> Any:
    """Run a filesystem call, retrying WinError 5/32 with pbip_utils' backoff.

    Windows AV / indexer / OneDrive briefly hold freshly copied trees; a
    folder move or delete right after a copy then fails spuriously.
    """
    import time

    from core.utilities import pbip_utils

    attempt = 0
    while True:
        try:
            return fn()
        except OSError as e:
            backoff = pbip_utils._REPLACE_BACKOFF_S
            if attempt >= len(backoff) or not pbip_utils._is_transient_replace_error(e):
                raise
            time.sleep(backoff[attempt])
            attempt += 1


def make_dirs(path: PathLike) -> None:
    p = Path(os.fspath(path))
    _check_root(p)
    scope = _SCOPE.get()
    if scope is not None and scope.dry_run:
        if not p.exists():
            scope.record("mkdir", p)
        return
    try:
        os.makedirs(long_path(p), exist_ok=True)
    except OSError as e:
        raise WriteError(f"cannot create {p}: {e}", p) from e


def remove_path(path: PathLike) -> bool:
    """Delete a file or folder tree. Returns False when it did not exist."""
    p = Path(os.fspath(path))
    _check_root(p)
    if not p.exists():
        return False
    scope = _SCOPE.get()
    if scope is not None and scope.dry_run:
        scope.record("delete", p, kind="dir" if p.is_dir() else "file")
        return True
    try:
        if p.is_dir():
            _retry_transient(lambda: shutil.rmtree(long_path(p)))
        else:
            _retry_transient(lambda: os.remove(long_path(p)))
    except OSError as e:
        raise WriteError(f"cannot delete {p}: {e}", p) from e
    notify_path_written(p)
    if scope is not None:
        scope.mark_written(p)
    return True


def copy_tree(src: PathLike, dst: PathLike) -> None:
    s = Path(os.fspath(src))
    d = Path(os.fspath(dst))
    _check_root(d)
    scope = _SCOPE.get()
    if scope is not None and scope.dry_run:
        scope.record("copy", d, source=str(s))
        return
    try:
        shutil.copytree(long_path(s), long_path(d))
    except (OSError, shutil.Error) as e:
        raise WriteError(f"cannot copy {s} -> {d}: {e}", d) from e
    notify_path_written(d)
    if scope is not None:
        scope.mark_written(d)


def move_path(src: PathLike, dst: PathLike) -> None:
    s = Path(os.fspath(src))
    d = Path(os.fspath(dst))
    _check_root(s)
    _check_root(d)
    scope = _SCOPE.get()
    if scope is not None and scope.dry_run:
        scope.record("move", d, source=str(s))
        return
    try:
        _retry_transient(lambda: os.replace(long_path(s), long_path(d)))
    except OSError as e:
        raise WriteError(f"cannot move {s} -> {d}: {e}", d) from e
    notify_path_written(s)
    notify_path_written(d)
    if scope is not None:
        scope.mark_written(d)


# ---------------------------------------------------------------------------
# $schema of new files (H-36)
# ---------------------------------------------------------------------------


def sibling_schema(folder: PathLike, filename: str, default: str) -> str:
    """The ``$schema`` a NEW ``filename`` should carry: copy it from a sibling.

    Looks for the same file name one and two levels around *folder* (e.g.
    another page's page.json, another visual's visual.json) so a generated
    file matches the schema versions Desktop already wrote into this report
    instead of pinning an old constant. Falls back to *default*.
    """
    base = Path(os.fspath(folder))
    candidates: List[Path] = []
    for pattern in (f"*/{filename}", f"*/*/{filename}", f"*/*/*/{filename}"):
        try:
            for hit in base.glob(pattern):
                candidates.append(hit)
                if len(candidates) >= 3:
                    break
        except OSError:
            continue
        if candidates:
            break
    for cand in candidates:
        data = read_json(cand)
        if isinstance(data, dict) and isinstance(data.get("$schema"), str):
            return data["$schema"]
    return default
