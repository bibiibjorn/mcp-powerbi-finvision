"""Counts-only rotating log of the gateway: ``<home>/gateway.log``.

The log holds numbers and fixed labels, never request or response text and never a value. ``event``
therefore accepts only integers and booleans as free fields; the two string fields (``route`` and
``error_type``) are reduced to a safe label.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import re
import threading
import time
from pathlib import Path

LOG_FILE = "gateway.log"
LOG_BYTES = 512 * 1024
LOG_BACKUPS = 2

_SAFE_LABEL = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,39}")
_ID_SEGMENT = re.compile(r"[^/]*\d[^/]*")


def route_label(path: str) -> str:
    """URL path reduced to a label: query dropped, id-like segments (digits) become ``:id``."""
    parts = path.split("?", 1)[0].split("/")
    return "/".join(
        ":id" if _ID_SEGMENT.fullmatch(p) and not p.startswith("v") else p for p in parts
    )[:80]


class GatewayLog:
    """Rotating counts-only log; the file is created on the first write. Never raises."""

    def __init__(self, home: Path | None) -> None:
        self._home = home
        self._lock = threading.Lock()
        self._logger: logging.Logger | None = None
        self._handler: logging.Handler | None = None
        self._failed = home is None

    def _get_logger(self) -> logging.Logger | None:
        if self._logger is None and not self._failed:
            try:
                assert self._home is not None
                self._home.mkdir(parents=True, exist_ok=True)
                logger = logging.getLogger(f"client_gateway.stats.{id(self)}")
                logger.setLevel(logging.INFO)
                logger.propagate = False
                handler = logging.handlers.RotatingFileHandler(
                    self._home / LOG_FILE,
                    maxBytes=LOG_BYTES,
                    backupCount=LOG_BACKUPS,
                    encoding="utf-8",
                    delay=True,
                )
                handler.setFormatter(logging.Formatter("%(message)s"))
                logger.addHandler(handler)
                self._logger, self._handler = logger, handler
            except Exception:  # noqa: BLE001 - logging must never break the gateway
                self._failed = True
        return self._logger

    def event(
        self, name: str, *, route: str | None = None, error_type: str | None = None, **counts: int
    ) -> None:
        record: dict[str, object] = {"ts": round(time.time(), 3), "event": name}
        if route is not None:
            record["route"] = route_label(route)
        if error_type is not None:
            record["error_type"] = error_type if _SAFE_LABEL.fullmatch(error_type) else "?"
        for key, value in counts.items():
            if isinstance(value, (int, bool)):
                record[key] = value
        try:
            with self._lock:
                logger = self._get_logger()
                if logger is not None:
                    logger.info(json.dumps(record, separators=(",", ":")))
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        with self._lock:
            if self._logger is not None and self._handler is not None:
                try:
                    self._logger.removeHandler(self._handler)
                    self._handler.close()
                except Exception:  # noqa: BLE001
                    pass
            self._logger = self._handler = None
            self._failed = True
