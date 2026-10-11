"""Remembered dictionary: every client value the gateway ever read, encrypted on this machine.

Without it the gateway only knows the models that are open right now, so a name from a model that
is closed (in an e-mail, a note, a screenshot of last week's report) would go out readable. The
store keeps the union of all values read, per prefix, and is loaded at gateway start.

* Encrypted with Windows DPAPI for the current user (``CryptProtectData``): only this Windows
  account on this machine can read it. No key is written anywhere.
* Values only: no model, file or table names, no counts per model.
* ``python -m client_gateway forget`` (the user, in a terminal) deletes it; the AI cannot run it.
* Where DPAPI is not available the store is off (nothing is written in clear text).
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Iterable

STORE_FILE = "dictionary.dpapi"
_DESCRIPTION = "Finvision client gateway dictionary"
_VERSION = 1


def _dpapi() -> tuple[object, object] | None:
    if os.name != "nt":
        return None
    try:
        import win32crypt  # type: ignore[import-not-found]  # noqa: PLC0415
    except ImportError:
        return None
    return win32crypt.CryptProtectData, win32crypt.CryptUnprotectData


class ValueStore:
    """``{prefix: {"strong": [...], "plain": [...]}}``, DPAPI-encrypted at ``home/STORE_FILE``."""

    def __init__(self, home: Path) -> None:
        self.path = Path(home) / STORE_FILE
        self._lock = threading.Lock()
        self._crypto = _dpapi()
        self._values: dict[tuple[str, bool], set[str]] = {}
        self.loaded = False
        self.error: str | None = None if self._crypto else "dpapi_unavailable"

    @property
    def available(self) -> bool:
        return self._crypto is not None

    def load(self) -> dict[tuple[str, bool], set[str]]:
        """The remembered values (empty when there is no store or it cannot be read)."""
        with self._lock:
            self.loaded = True
            if self._crypto is None or not self.path.is_file():
                return {}
            try:
                blob = self.path.read_bytes()
                _desc, plain = self._crypto[1](blob, None, None, None, 0)
                data = json.loads(plain.decode("utf-8"))
                values: dict[tuple[str, bool], set[str]] = {}
                for prefix, kinds in (data.get("values") or {}).items():
                    for kind, items in (kinds or {}).items():
                        values[(str(prefix), kind == "strong")] = {str(v) for v in items}
                self._values = values
                self.error = None
            except Exception as exc:  # noqa: BLE001 - a broken store is reported, never fatal
                self.error = type(exc).__name__
                return {}
            return {k: set(v) for k, v in self._values.items()}

    def remember(self, batches: Iterable[tuple[str, bool, Iterable[str]]]) -> int:
        """Add values; writes only when something is new. Returns the number of new values."""
        if self._crypto is None:
            return 0
        with self._lock:
            added = 0
            for prefix, strong, items in batches:
                bucket = self._values.setdefault((prefix, bool(strong)), set())
                for value in items:
                    if value not in bucket:
                        bucket.add(value)
                        added += 1
            if added:
                self._write()
            return added

    def _write(self) -> None:
        data = {"version": _VERSION, "values": {}}
        for (prefix, strong), items in sorted(self._values.items()):
            data["values"].setdefault(prefix, {})["strong" if strong else "plain"] = sorted(items)
        plain = json.dumps(data, ensure_ascii=False).encode("utf-8")
        blob = self._crypto[0](plain, _DESCRIPTION, None, None, None, 0)  # type: ignore[index]
        tmp = self.path.with_suffix(".tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(blob)
        os.replace(tmp, self.path)

    def count(self) -> int:
        with self._lock:
            return sum(len(v) for v in self._values.values())

    def forget(self) -> bool:
        """Delete the store (``python -m client_gateway forget``)."""
        with self._lock:
            self._values = {}
            try:
                self.path.unlink()
                return True
            except FileNotFoundError:
                return False
