"""Directory-change watcher behind the PBIP signature memo (P-01).

``PbipModelCache`` validates a memoised scope signature with a filesystem walk
once its 5 s TTL expires (0.2-0.5 s on a 4-9k file project). ``DirWatcher``
replaces that walk with a change counter fed by ``ReadDirectoryChangesW``
(pywin32): every relevant event, and every notification-buffer overflow, bumps
``version``; the cache keeps the signature as long as ``version`` is unchanged
and the watcher is ``healthy``.

Failure policy is "fall back, never guess": ``start()`` returns ``False`` (no
watcher, the cache keeps its TTL walk) on non-Windows, without pywin32, for
UNC / network paths (events are unreliable there), when a root cannot be opened,
or with more than 63 roots. A runtime error (watched folder deleted, handle
invalidated) bumps ``version`` once and flips ``healthy`` to ``False``.

Windows + pywin32 only; everything is imported lazily so importing this module
never costs a CLR/pywin32 load.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from dataclasses import dataclass
from typing import Any, FrozenSet, List, Optional, Sequence

logger = logging.getLogger(__name__)

#: ``WaitForMultipleObjects`` takes at most 64 handles (one is the stop event).
MAX_ROOTS = 63
_BUFFER_BYTES = 64 * 1024
_START_TIMEOUT_S = 3.0


@dataclass(frozen=True)
class WatchSpec:
    """One directory to watch.

    ``recursive`` roots flag every event below them except those under a
    ``prune`` folder name; flat roots (``recursive=False``) flag only events
    whose file name is in ``names`` (lower-cased), e.g. the ``definition.pbir``
    pointer file next to a report's ``definition/`` folder.
    """

    path: str
    recursive: bool = True
    names: FrozenSet[str] = frozenset()
    prune: FrozenSet[str] = frozenset()


def available() -> bool:
    """True when this platform can run a watcher (Windows + pywin32)."""
    if sys.platform != "win32":
        return False
    try:
        import win32event  # noqa: F401
        import win32file  # noqa: F401
        import pywintypes  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def _is_remote(path: str) -> bool:
    drive, _ = os.path.splitdrive(os.path.abspath(path))
    if drive.startswith("\\\\"):
        return True
    try:
        import win32file

        return win32file.GetDriveType(drive + "\\") == 4  # DRIVE_REMOTE
    except Exception:  # noqa: BLE001
        return False


class _Root:
    __slots__ = ("spec", "handle", "overlapped", "buffer", "event")

    def __init__(self, spec: WatchSpec):
        self.spec = spec
        self.handle: Any = None
        self.overlapped: Any = None
        self.buffer: Any = None
        self.event: Any = None


class DirWatcher:
    """Change counter for a set of directories (see the module docstring)."""

    def __init__(self, specs: Sequence[WatchSpec]):
        self._specs = list(specs)
        self._lock = threading.Lock()
        self._version = 0
        self._healthy = False
        self._stopping = threading.Event()
        self._ready = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._stop_event: Any = None
        self._start_ok = False

    # ----------------------------------------------------------------- state

    @property
    def version(self) -> int:
        return self._version

    @property
    def healthy(self) -> bool:
        return self._healthy and not self._stopping.is_set()

    def _bump(self) -> None:
        with self._lock:
            self._version += 1

    def _fail(self, reason: str) -> None:
        logger.debug("pbip watcher stopped: %s", reason)
        self._healthy = False
        self._bump()

    # ----------------------------------------------------------------- start

    def start(self) -> bool:
        """Open every root and start listening. ``False`` = fall back to the TTL walk."""
        if not self._specs or len(self._specs) > MAX_ROOTS or not available():
            return False
        try:
            if any(_is_remote(s.path) for s in self._specs):
                return False
        except Exception:  # noqa: BLE001
            return False
        import win32event

        self._stop_event = win32event.CreateEvent(None, True, False, None)
        self._thread = threading.Thread(target=self._run, name="pbip-dir-watcher", daemon=True)
        self._thread.start()
        if not self._ready.wait(_START_TIMEOUT_S) or not self._start_ok:
            self.stop()
            return False
        self._healthy = True
        return True

    def stop(self) -> None:
        self._stopping.set()
        self._healthy = False
        ev = self._stop_event
        if ev is not None:
            try:
                import win32event

                win32event.SetEvent(ev)
            except Exception:  # noqa: BLE001
                pass
        th = self._thread
        if th is not None and th is not threading.current_thread():
            th.join(timeout=2.0)

    # ---------------------------------------------------------------- worker

    @staticmethod
    def _relevant(spec: WatchSpec, rel_name: str) -> bool:
        parts = [p for p in rel_name.replace("/", "\\").split("\\") if p]
        if not spec.recursive:
            return bool(parts) and parts[-1].lower() in spec.names
        if spec.prune:
            for part in parts[:-1] if parts else ():
                if part in spec.prune:
                    return False
            # the pruned folder itself being created/removed/touched
            if parts and parts[-1] in spec.prune:
                return False
        return True

    def _run(self) -> None:
        import pywintypes
        import win32con
        import win32event
        import win32file

        roots: List[_Root] = []
        flt = (
            win32con.FILE_NOTIFY_CHANGE_FILE_NAME
            | win32con.FILE_NOTIFY_CHANGE_DIR_NAME
            | win32con.FILE_NOTIFY_CHANGE_SIZE
            | win32con.FILE_NOTIFY_CHANGE_LAST_WRITE
        )
        share = win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE | win32con.FILE_SHARE_DELETE
        file_list_directory = 0x0001
        overlapped_flag = 0x40000000

        def issue(root: _Root) -> None:
            win32event.ResetEvent(root.event)
            win32file.ReadDirectoryChangesW(
                root.handle,
                root.buffer,
                bool(root.spec.recursive),
                flt,
                root.overlapped,
                None,
            )

        try:
            try:
                for spec in self._specs:
                    r = _Root(spec)
                    r.handle = win32file.CreateFile(
                        spec.path,
                        file_list_directory,
                        share,
                        None,
                        win32con.OPEN_EXISTING,
                        win32con.FILE_FLAG_BACKUP_SEMANTICS | overlapped_flag,
                        None,
                    )
                    r.event = win32event.CreateEvent(None, True, False, None)
                    r.overlapped = pywintypes.OVERLAPPED()
                    r.overlapped.hEvent = r.event
                    r.buffer = win32file.AllocateReadBuffer(_BUFFER_BYTES)
                    roots.append(r)
                    issue(r)
            except Exception as e:  # noqa: BLE001 -- any open/issue failure = no watcher
                logger.debug("pbip watcher could not start: %s", e)
                self._start_ok = False
                return
            self._start_ok = True
            self._ready.set()

            handles = [r.event for r in roots] + [self._stop_event]
            while not self._stopping.is_set():
                idx = win32event.WaitForMultipleObjects(handles, False, win32event.INFINITE)
                i = idx - win32event.WAIT_OBJECT_0
                if i < 0 or i >= len(roots):
                    break  # stop event (or wait failure)
                r = roots[i]
                try:
                    nbytes = win32file.GetOverlappedResult(r.handle, r.overlapped, False)
                except pywintypes.error as e:
                    self._fail(f"GetOverlappedResult: {e}")
                    break
                if nbytes == 0:
                    self._bump()  # notification buffer overflowed: changes were lost
                else:
                    try:
                        events = win32file.FILE_NOTIFY_INFORMATION(r.buffer, nbytes)
                    except Exception:  # noqa: BLE001
                        events = [(0, "")]
                    if any(self._relevant(r.spec, name) for _a, name in events):
                        self._bump()
                try:
                    issue(r)
                except pywintypes.error as e:
                    self._fail(f"ReadDirectoryChangesW: {e}")
                    break
        except Exception as e:  # noqa: BLE001
            self._fail(f"watcher thread: {e}")
        finally:
            self._healthy = False
            for r in roots:
                try:
                    win32file.CancelIo(r.handle)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    r.handle.Close()
                except Exception:  # noqa: BLE001
                    pass
            self._ready.set()


__all__ = ["DirWatcher", "WatchSpec", "available", "MAX_ROOTS"]
