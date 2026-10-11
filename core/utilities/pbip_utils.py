"""
PBIP Utilities - Shared helper functions for Power BI PBIP file operations.

Extracted from slicer_operations_handler.py and visual_operations_handler.py
to eliminate code duplication and fix known issues.
"""

import io
import json
import logging
import os
import re
import stat
import tempfile
import threading
import time
import weakref
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple, Union

from core.utilities.json_utils import load_json

logger = logging.getLogger(__name__)

# Pre-compiled regex for WSL path detection (avoids recompiling on every call)
_WSL_PATH_PATTERN = re.compile(r"^/mnt/([a-z])/", re.IGNORECASE)
# Git-Bash / MSYS drive form: `/c/Users/...` (single drive letter segment).
_MSYS_PATH_PATTERN = re.compile(r"^/([a-z])(?:/|$)", re.IGNORECASE)


def normalize_path(path: str) -> str:
    """Normalize path to handle Unix-style paths on Windows.

    Fixes:
    - Eliminated double regex match (was matching twice, now matches once)
    - Fixed hardcoded index 7 for rest_of_path (now uses match.end() for correctness)

    P1-4 fix: previously converted *any* absolute POSIX path (`/home/foo`) to
    backslashes unconditionally. That broke POSIX test hosts / CI sandboxes
    where `/home/foo` is a legitimate path. Now only rewrites separators on
    Windows; on POSIX returns WSL-style paths unchanged and non-WSL absolute
    paths untouched.
    """
    match = _WSL_PATH_PATTERN.match(path)
    if match:
        # `/mnt/c/foo` → `C:\foo` is always correct: WSL paths are Windows paths.
        drive_letter = match.group(1)
        rest_of_path = path[match.end() :].replace("/", "\\")
        return f"{drive_letter.upper()}:\\{rest_of_path}"
    # Only rewrite absolute POSIX paths when running on Windows. Linux/macOS
    # dev hosts keep their paths intact so tests and CI work cross-platform.
    if path.startswith("/") and os.name == "nt":
        # G-17: Git-Bash / MSYS form `/c/Users/...` is drive C:, not `C:\c\Users`.
        msys = _MSYS_PATH_PATTERN.match(path)
        if msys:
            rest_of_path = path[msys.end() :].replace("/", "\\")
            return f"{msys.group(1).upper()}:\\{rest_of_path}"
        return path.replace("/", "\\")

    return path


def find_definition_folder(pbip_path: str) -> Optional[Path]:
    """Find the definition folder for a PBIP project.

    Accepts a path to a .pbip file, a .Report folder, a definition folder,
    or a directory containing a .Report folder.
    """
    path = Path(normalize_path(pbip_path))

    if not path.exists():
        return None

    # If it's a .pbip file, look for .Report folder
    if path.is_file() and path.suffix == ".pbip":
        report_folder = path.parent / f"{path.stem}.Report"
        if report_folder.exists():
            definition = report_folder / "definition"
            if definition.exists():
                return definition

    # If it's a .Report folder
    if path.is_dir() and path.name.endswith(".Report"):
        definition = path / "definition"
        if definition.exists():
            return definition

    # If it's already a definition folder
    if path.is_dir() and path.name == "definition":
        return path

    # If it's a directory, search for .Report folders
    if path.is_dir():
        for item in path.iterdir():
            if item.is_dir() and item.name.endswith(".Report"):
                definition = item / "definition"
                if definition.exists():
                    return definition
        # Also check if definition exists directly
        definition = path / "definition"
        if definition.exists():
            return definition

    return None


def resolve_report_folder_from_pbip(pbip_file_path: str) -> Optional[str]:
    """Resolve a .pbip file path to its matching .Report folder.

    PBIP convention: filename.pbip → filename.Report/ in the same directory.
    Example: C:/Repo/MyDashboard.pbip → C:/Repo/MyDashboard.Report/

    Falls back to parsing the .pbip JSON for artifact path hints.

    Args:
        pbip_file_path: Absolute path to a .pbip file

    Returns:
        Path to the .Report folder if found, or None
    """
    path = Path(normalize_path(pbip_file_path))

    if not path.is_file() or path.suffix.lower() != ".pbip":
        return None

    pbip_dir = path.parent
    project_name = path.stem

    # Primary: matching .Report folder by name convention
    report_folder = pbip_dir / f"{project_name}.Report"
    if report_folder.is_dir():
        return str(report_folder)

    # Fallback: parse .pbip JSON for artifact path
    try:
        pbip_data = load_json(str(path))
        if isinstance(pbip_data, dict):
            for artifact in pbip_data.get("artifacts", []):
                report_info = artifact.get("report", {})
                report_path = report_info.get("path", "")
                if report_path:
                    # Path is relative, e.g. "MyDashboard.Report"
                    candidate = pbip_dir / report_path
                    if candidate.is_dir():
                        return str(candidate)
    except Exception:
        pass

    return None


def resolve_pbip_report_path(input_path: str) -> Optional[str]:
    """Resolve any path to a valid PBIP .Report folder.

    Handles:
    - .pbip file → matching .Report folder
    - .Report folder → returned as-is
    - Directory with definition/ → returned as-is
    - Directory containing .pbip files → .Report from first Report-type .pbip

    Args:
        input_path: Path to .pbip file, directory, or .Report folder

    Returns:
        Path to the best .Report folder or valid PBIP folder, or None
    """
    path = Path(normalize_path(input_path))

    if not path.exists():
        return None

    # Case 1: .pbip file
    if path.is_file() and path.suffix.lower() == ".pbip":
        return resolve_report_folder_from_pbip(str(path))

    # Case 2: Already a .Report folder
    if path.is_dir() and path.name.endswith(".Report"):
        return str(path)

    # Case 3: Has definition/ directly (already a valid PBIP folder)
    if path.is_dir() and (path / "definition").is_dir():
        return str(path)

    # Case 4: Directory containing .pbip files — resolve from those
    if path.is_dir():
        for pbip_file in sorted(path.glob("*.pbip")):
            report_folder = resolve_report_folder_from_pbip(str(pbip_file))
            if report_folder:
                return report_folder

    return None


def load_json_file(file_path: Path) -> Optional[Dict]:
    """Load JSON file safely.

    Catches specific exceptions instead of bare Exception:
    - json.JSONDecodeError for malformed JSON
    - FileNotFoundError for missing files
    - PermissionError for access issues
    """
    try:
        return load_json(file_path)
    except json.JSONDecodeError as e:
        logger.warning(f"Invalid JSON in {file_path}: {e}")
        return None
    except FileNotFoundError:
        logger.warning(f"File not found: {file_path}")
        return None
    except PermissionError as e:
        logger.warning(f"Permission denied reading {file_path}: {e}")
        return None


def iter_pages(definition_path: Path):
    """Yield ``(page_folder, page_data)`` for every page in the definition.

    Shared by the PBIR engines (filter / conditional-formatting / ...) so the
    page-walk lives in one place (Phase 2 DUP consolidation).
    """
    pages_dir = definition_path / "pages"
    if not pages_dir.is_dir():
        return
    for page_folder in sorted(pages_dir.iterdir()):
        if not page_folder.is_dir():
            continue
        page_data = load_json_file(page_folder / "page.json")
        if page_data is not None:
            yield page_folder, page_data


def iter_visuals(page_folder: Path):
    """Yield ``(visual_folder, visual_data)`` for every visual on a page."""
    visuals_dir = page_folder / "visuals"
    if not visuals_dir.is_dir():
        return
    for visual_folder in sorted(visuals_dir.iterdir()):
        if not visual_folder.is_dir():
            continue
        visual_data = load_json_file(visual_folder / "visual.json")
        if visual_data is not None:
            yield visual_folder, visual_data


# ---------------------------------------------------------------------------
# Safe PBIR writes (H-01 / K-15)
#
# Every PBIR engine does load -> mutate -> save on the dispatcher's thread pool.
# The old writer used a DETERMINISTIC temp name (``visual.json.tmp``) and no
# lock, so two concurrent writers to the same file shared one temp file and
# raced each other's ``os.replace`` / cleanup -- on Windows the destination
# could vanish (review probe p_race.py: 106/120 writes failed, then
# ``visual.json`` was gone). Fixes:
#   * a UNIQUE temp file per write (``tempfile.mkstemp(dir=parent)``);
#   * a process-wide per-path lock (``file_lock``) around every write, and
#     ``update_json_file`` for a locked read-modify-write;
#   * ``os.replace`` retried with backoff on WinError 5/32 (AV scanner,
#     indexer, OneDrive or Desktop's watcher holding the target);
#   * the original file's encoding traits (BOM, CRLF/LF, trailing newline,
#     indent, raw UTF-8 vs \uXXXX escaping) are preserved, so a save does not
#     rewrite every line of a Desktop-authored file.
# ---------------------------------------------------------------------------


class _PathLock:
    """Weak-referenceable holder for a per-path re-entrant lock."""

    __slots__ = ("lock", "__weakref__")

    def __init__(self) -> None:
        self.lock = threading.RLock()


_PATH_LOCKS: "weakref.WeakValueDictionary[str, _PathLock]" = weakref.WeakValueDictionary()
_PATH_LOCKS_GUARD = threading.Lock()


def _lock_key(file_path: Union[str, Path]) -> str:
    """Canonical, case-insensitive (on Windows) key for a filesystem path."""
    raw = normalize_path(str(file_path))
    return os.path.normcase(os.path.abspath(raw))


@contextmanager
def file_lock(file_path: Union[str, Path]) -> Iterator[None]:
    """Process-wide re-entrant lock keyed by *file_path* (any file or folder).

    Serialises every in-process writer of the same path. Re-entrant, so a
    caller holding it may call ``save_json_file`` / ``update_json_file`` on the
    same path. Lock a folder path (e.g. a report ``definition/``) to serialise
    a multi-file edit; per-file saves inside still take their own file lock.
    Does NOT protect against other processes (Desktop, git) -- that is what the
    atomic replace + retry is for.
    """
    key = _lock_key(file_path)
    with _PATH_LOCKS_GUARD:
        holder = _PATH_LOCKS.get(key)
        if holder is None:
            holder = _PathLock()
            _PATH_LOCKS[key] = holder
    # ``holder`` stays strongly referenced for the duration of the block, so
    # the WeakValueDictionary entry cannot be collected while it is held.
    with holder.lock:
        yield


# Write listeners: callbacks invoked with the written path after every
# successful write through this module. ``server.pbip_cache`` registers its
# ``invalidate`` here (A-12 / G-17) -- core/ must not import server/, so the
# dependency points this way.
_WRITE_LISTENERS: List[Callable[[str], None]] = []
_WRITE_LISTENERS_GUARD = threading.Lock()


def register_write_listener(callback: Callable[[str], None]) -> None:
    """Register ``callback(path)`` to run after every successful PBIR write.

    Idempotent (the same callable is registered once). Listener exceptions
    are logged and swallowed -- a cache hook must never fail a write.
    """
    with _WRITE_LISTENERS_GUARD:
        if callback not in _WRITE_LISTENERS:
            _WRITE_LISTENERS.append(callback)


def unregister_write_listener(callback: Callable[[str], None]) -> None:
    """Remove a listener registered with ``register_write_listener``."""
    with _WRITE_LISTENERS_GUARD:
        if callback in _WRITE_LISTENERS:
            _WRITE_LISTENERS.remove(callback)


def notify_path_written(file_path: Union[str, Path]) -> None:
    """Tell listeners (the PBIP cache) that *file_path* changed on disk.

    Called automatically by ``save_json_file`` / ``save_json_minimal`` /
    ``update_json_file``. Writers that bypass this module (TMDL writers, the
    pbir CLI, ``shutil`` copies/deletes) should call it themselves with the
    changed file or folder.
    """
    with _WRITE_LISTENERS_GUARD:
        listeners = list(_WRITE_LISTENERS)
    for cb in listeners:
        try:
            cb(str(file_path))
        except Exception as e:  # pragma: no cover - defensive
            logger.warning(f"PBIP write listener {cb!r} failed for {file_path}: {e}")


# Backoff schedule (seconds) for os.replace retries on a transiently locked
# target: ~1.5 s worst case. Module-level so tests can shorten it.
_REPLACE_BACKOFF_S: Tuple[float, ...] = (0.05, 0.1, 0.2, 0.4, 0.8)
_TRANSIENT_WINERRORS = frozenset({5, 32})  # ERROR_ACCESS_DENIED, ERROR_SHARING_VIOLATION


def _is_transient_replace_error(exc: OSError) -> bool:
    winerror = getattr(exc, "winerror", None)
    if winerror is not None:
        return winerror in _TRANSIENT_WINERRORS
    # POSIX / tests: a bare PermissionError is the same "someone holds it" case.
    return isinstance(exc, PermissionError)


def _replace_with_retry(src: str, dst: Union[str, Path]) -> None:
    """``os.replace`` with retry/backoff on WinError 5/32 (K-15)."""
    attempt = 0
    while True:
        try:
            os.replace(src, dst)
            return
        except OSError as e:
            if attempt >= len(_REPLACE_BACKOFF_S) or not _is_transient_replace_error(e):
                raise
            delay = _REPLACE_BACKOFF_S[attempt]
            attempt += 1
            logger.debug(f"os.replace({dst}) transient failure ({e}); retry {attempt} in {delay}s")
            time.sleep(delay)


def _atomic_write_bytes(file_path: Path, payload: bytes) -> None:
    """Write *payload* via a unique sibling temp file + fsync + retried replace.

    Raises OSError on failure. The temp file is always cleaned up and the
    destination holds either the old or the new content, never partial/missing.
    """
    fd, tmp_name = tempfile.mkstemp(
        dir=str(file_path.parent), prefix=f".{file_path.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        if os.name != "nt":
            # mkstemp creates 0600; keep the destination's mode on POSIX.
            try:
                os.chmod(tmp_name, stat.S_IMODE(os.stat(file_path).st_mode))
            except OSError:
                pass
        _replace_with_retry(tmp_name, file_path)
    except BaseException:
        _cleanup_tmp(tmp_path)
        raise


@dataclass(frozen=True)
class JsonTextTraits:
    """Byte-level formatting traits of an existing JSON file."""

    bom: bool = False
    newline: str = "\r\n" if os.name == "nt" else "\n"
    trailing_newline: bool = False
    indent: Union[int, str] = 2
    ensure_ascii: bool = True


# A \uXXXX escape of a NON-ASCII code point (>= U+0080).
_UNICODE_ESCAPE_NON_ASCII = re.compile(
    rb"\\u(?:00[89a-fA-F][0-9a-fA-F]|0[1-9a-fA-F][0-9a-fA-F]{2}|[1-9a-fA-F][0-9a-fA-F]{3})"
)
_INDENT_RE = re.compile(rb"\n([ \t]+)\S")


def detect_json_traits(raw: Optional[bytes]) -> JsonTextTraits:
    """Infer the formatting traits of an existing JSON file's bytes.

    New files (``raw`` is None/empty) get the defaults: no BOM, CRLF on
    Windows (what Desktop writes, and what the old text-mode writer produced),
    no trailing newline, indent 2, ``ensure_ascii=True`` (P0-15: escaped
    non-ASCII survives shell pbir round-trips).

    Real Desktop files (R0105/R0201 census, 2026-09-22): no BOM, CRLF, no
    trailing newline, indent 2, and ~9 % contain RAW UTF-8 non-ASCII text
    (never \\uXXXX). A file that already holds raw UTF-8 keeps raw UTF-8
    (``ensure_ascii=False``) so a save doesn't rewrite every accented string.
    """
    default = JsonTextTraits()
    if not raw:
        return default
    bom = raw.startswith(b"\xef\xbb\xbf")
    body = raw[3:] if bom else raw
    if b"\r\n" in body:
        newline = "\r\n"
    elif b"\n" in body:
        newline = "\n"
    else:
        newline = default.newline
    trailing = body.endswith(b"\n")
    indent: Union[int, str] = default.indent
    m = _INDENT_RE.search(body)
    if m:
        ws = m.group(1)
        indent = ws.decode("ascii") if b"\t" in ws else len(ws)
    if any(b > 0x7F for b in body):
        ensure_ascii = False
    elif _UNICODE_ESCAPE_NON_ASCII.search(body):
        ensure_ascii = True
    else:
        ensure_ascii = default.ensure_ascii
    return JsonTextTraits(
        bom=bom,
        newline=newline,
        trailing_newline=trailing,
        indent=indent,
        ensure_ascii=ensure_ascii,
    )


def serialize_json(data: Any, traits: Optional[JsonTextTraits] = None) -> bytes:
    """Serialise *data* to bytes honouring *traits* (default: new-file traits).

    Raises TypeError/ValueError for non-serialisable data. Uses ``json.dump``
    into a buffer (not ``json.dumps``) so tests can inject a failing encoder.
    """
    t = traits if traits is not None else JsonTextTraits()
    buf = io.StringIO()
    json.dump(data, buf, indent=t.indent, ensure_ascii=t.ensure_ascii)
    text = buf.getvalue()
    # json never emits a raw newline inside a string (it escapes it), so every
    # newline here is structural and safe to translate.
    if t.newline != "\n":
        text = text.replace("\n", t.newline)
    if t.trailing_newline:
        text += t.newline
    payload = text.encode("utf-8")
    if t.bom:
        payload = b"\xef\xbb\xbf" + payload
    return payload


def _read_bytes_or_none(file_path: Path) -> Optional[bytes]:
    try:
        return file_path.read_bytes()
    except FileNotFoundError:
        return None


def save_json_file(file_path: Path, data: Dict) -> bool:
    """Save JSON file atomically, preserving the existing file's formatting.

    T0-1 atomic write: serialize to a UNIQUE sibling temp file, fsync, then
    ``os.replace`` (retried on WinError 5/32) so a crash mid-write cannot leave
    the destination truncated and two concurrent writers cannot delete it
    (H-01). The write runs under ``file_lock(file_path)``. For a
    read-modify-write use ``update_json_file`` so the READ is locked too.

    Formatting (BOM, CRLF/LF, trailing newline, indent, raw UTF-8 vs \\uXXXX
    escaping) follows the file being replaced (``detect_json_traits``); new
    files get indent 2, CRLF on Windows and ``ensure_ascii=True`` (P0-15).

    Returns False (and logs) on failure instead of raising:
    - FileNotFoundError for missing parent directories
    - PermissionError for access issues (after retries)
    - TypeError / ValueError for non-serializable data
    """
    file_path = Path(file_path)
    try:
        with file_lock(file_path):
            traits = detect_json_traits(_read_bytes_or_none(file_path))
            payload = serialize_json(data, traits)
            _atomic_write_bytes(file_path, payload)
    except FileNotFoundError as e:
        logger.error(f"Parent directory not found for {file_path}: {e}")
        return False
    except PermissionError as e:
        logger.error(f"Permission denied writing {file_path}: {e}")
        return False
    except (TypeError, ValueError) as e:
        logger.error(f"Non-serializable data when saving {file_path}: {e}")
        return False
    except OSError as e:
        logger.error(f"OS error writing {file_path}: {e}")
        return False
    notify_path_written(file_path)
    return True


# Sentinel a mutate_fn may return to abort update_json_file without writing.
SKIP_WRITE = object()


def update_json_file(
    file_path: Union[str, Path],
    mutate_fn: Callable[[Any], Any],
    *,
    create_if_missing: bool = False,
    default_factory: Callable[[], Any] = dict,
    raise_on_error: bool = True,
) -> bool:
    """Locked read-modify-write of a JSON file (the H-01 lost-update fix).

    Under ``file_lock(file_path)``: read + parse the file, call
    ``mutate_fn(data)``, then atomically write the result with the original
    formatting traits. ``mutate_fn`` may mutate ``data`` in place (return
    None), return a replacement object to write, or return ``SKIP_WRITE`` to
    abort without writing. Nothing is written when the serialised bytes equal
    the original bytes.

    Returns True if the file was written, False for a skip / no-op. Load,
    parse and write failures raise (``FileNotFoundError``, ``ValueError``,
    ``OSError``) unless ``raise_on_error=False``, in which case they are
    logged and False is returned. A missing file raises unless
    ``create_if_missing=True`` (then ``default_factory()`` is the input).
    Exceptions raised by ``mutate_fn`` itself always propagate.
    """
    file_path = Path(file_path)
    with file_lock(file_path):
        try:
            raw = _read_bytes_or_none(file_path)
            if raw is None:
                if not create_if_missing:
                    raise FileNotFoundError(f"JSON file not found: {file_path}")
                data = default_factory()
            else:
                data = json.loads(raw.decode("utf-8-sig"))
        except (OSError, ValueError) as e:
            if raise_on_error:
                raise
            logger.error(f"update_json_file could not read {file_path}: {e}")
            return False
        result = mutate_fn(data)
        if result is SKIP_WRITE:
            return False
        if result is not None:
            data = result
        try:
            payload = serialize_json(data, detect_json_traits(raw))
            if raw is not None and payload == raw:
                return False
            _atomic_write_bytes(file_path, payload)
        except (OSError, ValueError, TypeError) as e:
            if raise_on_error:
                raise
            logger.error(f"update_json_file could not write {file_path}: {e}")
            return False
    notify_path_written(file_path)
    return True


def _cleanup_tmp(tmp_path: Path) -> None:
    """Best-effort removal of a .tmp file left behind by a failed atomic write."""
    try:
        if tmp_path.exists():
            tmp_path.unlink()
    except OSError:
        pass


def save_json_minimal(
    file_path: Path,
    new_bytes: bytes,
    original_bytes: bytes,
    raise_on_error: bool = False,
) -> bool:
    """Write ``new_bytes`` to ``file_path`` only if different from ``original_bytes``.

    Sibling to ``save_json_file``. The caller is responsible for producing
    valid JSON; this writer never re-serializes. It exists so high-volume
    in-place edits (slicer renames across hundreds of files) don't flip
    CRLF→LF, add trailing newlines, or pull schema-version bumps into
    unintended files.

    Returns True if the file was written, False if no-op (bytes unchanged).

    Note the ``False`` return conflates two very different outcomes: a benign
    unchanged-bytes no-op vs. a real write FAILURE (locked file, full disk).
    Callers that must distinguish them — e.g. cascade rename, which reports
    files as "modified" — pass ``raise_on_error=True`` so an actual write
    failure raises ``OSError`` while the unchanged-bytes no-op still returns
    ``False`` silently. Default behaviour is unchanged for existing callers.

    Same unique temp + fsync + retried replace + per-path lock as
    ``save_json_file`` (H-01 / K-15).
    """
    if new_bytes == original_bytes:
        return False
    file_path = Path(file_path)
    try:
        with file_lock(file_path):
            _atomic_write_bytes(file_path, new_bytes)
    except FileNotFoundError as e:
        logger.error(f"Parent directory not found for {file_path}: {e}")
        if raise_on_error:
            raise
        return False
    except PermissionError as e:
        logger.error(f"Permission denied writing {file_path}: {e}")
        if raise_on_error:
            raise
        return False
    except OSError as e:
        logger.error(f"OS error writing {file_path}: {e}")
        if raise_on_error:
            raise
        return False
    notify_path_written(file_path)
    return True


def resolve_definition_path(pbip_path: str) -> Dict[str, Any]:
    """Resolve and validate the definition path from a PBIP path.

    Returns a dict with 'path' on success or 'error' on failure.
    """
    definition_path = find_definition_folder(pbip_path)
    if not definition_path:
        return {
            "error": (
                f"Could not find definition folder in: {pbip_path}."
                " Ensure path points to a valid PBIP project."
            ),
            "error_type": "not_found",
        }
    return {"path": definition_path}


@dataclass(frozen=True)
class PbipPaths:
    """Canonical bundle of PBIP project paths used by the autonomous loops.

    All four fields are filled when the input is a fully-formed project root
    or .pbip file. When given a bare ``definition`` folder, ``pbip_file`` and
    ``semantic_model_folder`` may be None.
    """

    pbip_file: Optional[Path]
    report_folder: Optional[Path]
    definition_path: Path
    semantic_model_folder: Optional[Path]
    pages_folder: Path


def resolve_pbip_paths(input_path: str) -> PbipPaths:
    """Build a PbipPaths bundle from any valid PBIP-shaped input.

    Accepts: .pbip file, .Report folder, definition/ folder, or PBIP project
    root. Raises FileNotFoundError when the structure is invalid (callers
    that prefer a soft failure should wrap with try/except).
    """
    definition = find_definition_folder(input_path)
    if definition is None:
        raise FileNotFoundError(f"Could not resolve PBIP structure under {input_path}")

    report_folder: Optional[Path] = None
    pbip_file: Optional[Path] = None
    semantic_model_folder: Optional[Path] = None

    # definition/ lives inside *.Report/. Walk up.
    if definition.parent.name.endswith(".Report"):
        report_folder = definition.parent
        project_root = report_folder.parent
        # *.pbip file shares the project_root + project name (without .Report)
        stem = report_folder.name[: -len(".Report")]
        candidate = project_root / f"{stem}.pbip"
        if candidate.is_file():
            pbip_file = candidate
        # Semantic model sibling
        sm_candidate = project_root / f"{stem}.SemanticModel"
        if sm_candidate.is_dir():
            semantic_model_folder = sm_candidate

    return PbipPaths(
        pbip_file=pbip_file,
        report_folder=report_folder,
        definition_path=definition,
        semantic_model_folder=semantic_model_folder,
        pages_folder=definition / "pages",
    )


def get_page_display_name(page_folder: Path) -> str:
    """Get the display name for a page from its page.json file."""
    page_json_path = page_folder / "page.json"
    if page_json_path.exists():
        page_data = load_json_file(page_json_path)
        if page_data:
            return page_data.get("displayName", page_folder.name)
    return page_folder.name


def find_page_folder(
    definition_path: Path,
    page_id_or_display_name: str,
    *,
    allow_unique_startswith: bool = True,
) -> Optional[Path]:
    """Resolve a page folder by exact ID, then exact display name, then
    unique startswith (case-insensitive) when allow_unique_startswith=True.

    Substring fallback is NOT supported on purpose — searching for "R01"
    must not match "R0101". If multiple pages start with the same prefix,
    None is returned (caller should disambiguate).

    Tolerates a trailing ``.Page`` suffix on the input (the pbir CLI page
    arg uses that form; folders on disk are bare hex IDs).
    """
    pages_dir = definition_path / "pages"
    if not pages_dir.exists():
        return None

    raw = (page_id_or_display_name or "").strip()
    needle = raw[: -len(".Page")] if raw.endswith(".Page") else raw

    # 1. Direct folder-name (page ID) match. Try the original input first
    # (folders are normally bare hex IDs, but some pbir CLI inputs and
    # legacy fixtures use "<id>.Page" as the folder name itself), then the
    # de-suffixed form.
    for candidate in (raw, needle):
        if candidate and (pages_dir / candidate).is_dir():
            return pages_dir / candidate

    needle_lower = needle.lower()
    folders: List[Path] = [p for p in pages_dir.iterdir() if p.is_dir()]

    # 2. Exact display-name match (case-insensitive)
    exact: List[Path] = []
    for folder in folders:
        if get_page_display_name(folder).lower() == needle_lower:
            exact.append(folder)
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        logger.warning(
            "Multiple pages share display name %r in %s; cannot disambiguate",
            page_id_or_display_name,
            definition_path,
        )
        return None

    # 3. Unique startswith fallback (case-insensitive)
    if allow_unique_startswith:
        starts: List[Path] = []
        for folder in folders:
            if get_page_display_name(folder).lower().startswith(needle_lower):
                starts.append(folder)
        if len(starts) == 1:
            return starts[0]
        if len(starts) > 1:
            logger.debug(
                "Page name %r matches multiple display-name prefixes; " "no unique resolution",
                page_id_or_display_name,
            )

    return None


def find_visual_folder(
    page_folder: Path,
    visual_id_or_name: str,
    *,
    allow_unique_startswith: bool = True,
) -> Optional[Path]:
    """Resolve a visual folder by exact ID, then exact title, then unique
    startswith (case-insensitive).

    P1-41 fix: centralizes the 7 near-duplicated `_find_visual_folder`
    helpers that lived in field_binding_engine / theme_engine / filter_engine /
    conditional_formatting_engine / visual_calculations_engine /
    visual_alignment_engine / clone_engine. Previous copies used bare
    substring matching (`name_lower in title_lower`) which made "Card 1"
    match "Card 1", "Card 10", "Card 11" — silently binding / mutating
    the wrong visual. This helper uses exact match first, prefix
    fallback only when unique.
    """
    visuals_dir = page_folder / "visuals"
    if not visuals_dir.exists():
        return None

    raw = (visual_id_or_name or "").strip()
    if not raw:
        return None

    # 1. Direct folder-name (visual ID) match.
    candidate = visuals_dir / raw
    if candidate.is_dir():
        return candidate

    needle_lower = raw.lower()
    folders = [p for p in visuals_dir.iterdir() if p.is_dir()]

    # 2. Exact display-title match (case-insensitive).
    # Pull the title from visual.json's visualContainerObjects.title.text
    # literal or fall back to the folder id.
    def _get_title(folder: Path) -> str:
        vj = folder / "visual.json"
        if not vj.exists():
            return folder.name
        try:
            data = load_json(vj)
        except Exception:
            return folder.name
        try:
            title_entries = (
                data.get("visual", {}).get("visualContainerObjects", {}).get("title", [])
            )
            if title_entries:
                title_text = (
                    title_entries[0]
                    .get("properties", {})
                    .get("text", {})
                    .get("expr", {})
                    .get("Literal", {})
                    .get("Value", "")
                )
                # PBIR literal strings are wrapped in single quotes: `'Card 1'`
                if isinstance(title_text, str):
                    return title_text.strip().strip("'")
        except Exception:
            pass
        return folder.name

    titles = {folder: _get_title(folder) for folder in folders}

    exact = [folder for folder, title in titles.items() if title.lower() == needle_lower]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        logger.warning(
            "Multiple visuals share title %r in %s; cannot disambiguate",
            visual_id_or_name,
            page_folder,
        )
        return None

    # 3. Unique startswith fallback.
    if allow_unique_startswith:
        starts = [
            folder for folder, title in titles.items() if title.lower().startswith(needle_lower)
        ]
        if len(starts) == 1:
            return starts[0]
        if len(starts) > 1:
            logger.debug(
                "Visual name %r matches multiple prefixes in %s; no unique resolution",
                visual_id_or_name,
                page_folder,
            )

    return None
