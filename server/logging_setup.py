"""Process logging for the MCP server (A-27 / K-07).

* stderr handler level = ``config.server.log_level`` (was ignored; hard-coded
  INFO). The root logger stays at INFO so the file log keeps profiling /
  trace-lifecycle records.
* File log ``logs/pbixray.log`` is a ``RotatingFileHandler`` (10 MB × 3 backups;
  the plain FileHandler had grown to 33.7 MB), still buffered through a
  ``MemoryHandler`` (flush on WARNING+ or every 64 records) — plus a periodic
  background flush so buffered INFO records are not lost when the client kills
  the stdio process (the normal shutdown on Windows).
"""

from __future__ import annotations

import atexit
import logging
import os
import threading
from logging.handlers import MemoryHandler, RotatingFileHandler
from typing import Optional

LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
MAX_LOG_BYTES = 10 * 1024 * 1024
LOG_BACKUPS = 3
FLUSH_INTERVAL_SECONDS = 2.0

_flusher_started = False
_flusher_lock = threading.Lock()
_flush_targets: list = []


def _level(value: Optional[str], default: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        lvl = logging.getLevelName(value.strip().upper())
        if isinstance(lvl, int):
            return lvl
    return default


def _start_flusher(handler: MemoryHandler, interval: float) -> None:
    """One daemon thread flushes every registered buffered handler periodically."""
    global _flusher_started
    with _flusher_lock:
        if handler not in _flush_targets:
            _flush_targets.append(handler)
        if _flusher_started:
            return
        _flusher_started = True

    def _loop():
        ev = threading.Event()
        while not ev.wait(interval):
            with _flusher_lock:
                targets = list(_flush_targets)
            for h in targets:
                try:
                    h.flush()
                except Exception:  # noqa: BLE001
                    pass

    threading.Thread(target=_loop, name="mcp-log-flush", daemon=True).start()


def configure_logging(
    logs_dir: str,
    console_level: Optional[str] = "WARNING",
    *,
    flush_interval: float = FLUSH_INTERVAL_SECONDS,
) -> Optional[str]:
    """Configure root logging; returns the log file path (None if unavailable).

    Idempotent: re-running does not add duplicate handlers.
    """
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter(LOG_FORMAT)

    console = next((h for h in root.handlers if getattr(h, "_mcp_console", False)), None)
    if console is None:
        console = logging.StreamHandler()
        console._mcp_console = True  # type: ignore[attr-defined]
        console.setFormatter(fmt)
        root.addHandler(console)
    console.setLevel(_level(console_level, logging.WARNING))

    try:
        os.makedirs(logs_dir, exist_ok=True)
        log_path = os.path.join(logs_dir, "pbixray.log")
        for h in root.handlers:
            if getattr(h, "_mcp_file_path", None) == log_path:
                return log_path
        fh = RotatingFileHandler(
            log_path, maxBytes=MAX_LOG_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
        )
        fh.setLevel(logging.INFO)
        fh.setFormatter(fmt)
        mh = MemoryHandler(capacity=64, flushLevel=logging.WARNING, target=fh)
        mh._mcp_file_path = log_path  # type: ignore[attr-defined]
        root.addHandler(mh)
        atexit.register(mh.close)
        if flush_interval and flush_interval > 0:
            _start_flusher(mh, flush_interval)
        return log_path
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).warning("Could not set up file logging: %s", e)
        return None
