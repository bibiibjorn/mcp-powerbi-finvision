"""The Power BI chat marker.

Every result the Power BI MCP returns carries a top-level field ``"_fv": "fvpbi-<install id>"``. The
install id is 16 random hex characters, created once per masking home (``<home>/install.id``). The
gateway searches the bytes of a request for the marker token (format-agnostic: the token holds only
``[a-z0-9-]``, so it reads the same in plain JSON, in a JSON string that holds JSON, and in any
deeper escaping) and removes it before the request leaves the machine: ``strip_marker`` replaces it
by the fixed word ``fvpbi``, so ``"_fv":"fvpbi-..."`` stays valid JSON as ``"_fv":"fvpbi"``.

A spoofed marker can only switch masking ON for a chat (the safe direction); another install's
marker is not ours and does not match.
"""

from __future__ import annotations

import os
import re
import secrets
import time
from pathlib import Path
from typing import overload

INSTALL_ID_FILE = "install.id"
MARKER_FIELD = "_fv"
MARKER_PREFIX = "fvpbi-"
STRIPPED = "fvpbi"
_ID = re.compile(r"[0-9a-f]{16}")
_RETRIES = 12
_RETRY_S = 0.025


def _read(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return text if _ID.fullmatch(text) else None


def install_id(home: Path) -> str:
    """The install id of ``home`` (16 lowercase hex characters); created atomically when absent.

    The file is created EXCLUSIVELY (the same pattern as the control token): two creators end up
    with the one id that is in the file, and a reader that meets a half-written file reads again.
    A file that still holds anything else than an id after the retries is replaced."""
    path = home / INSTALL_ID_FILE
    home.mkdir(parents=True, exist_ok=True)
    for _ in range(_RETRIES):
        current = _read(path)
        if current is not None:
            return current
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            time.sleep(_RETRY_S)  # somebody is writing it right now: read it again
            continue
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            fh.write(secrets.token_hex(8) + "\n")
    tmp = path.with_name(f"{INSTALL_ID_FILE}.{os.getpid()}.tmp")
    tmp.write_text(secrets.token_hex(8) + "\n", encoding="ascii")
    os.replace(tmp, path)
    final = _read(path)
    if final is None:
        raise OSError("install id could not be created")
    return final


def marker(home: Path) -> str:
    """``fvpbi-<install id>``."""
    return MARKER_PREFIX + install_id(home)


def has_marker(data: bytes | str, marker: str) -> bool:
    """True when ``data`` (raw bytes or text, plain or JSON-escaped) holds ``marker``."""
    if not marker:
        return False
    if isinstance(data, bytes):
        return marker.encode("ascii") in data
    return marker in data


@overload
def strip_marker(data: bytes, marker: str) -> bytes: ...


@overload
def strip_marker(data: str, marker: str) -> str: ...


def strip_marker(data: bytes | str, marker: str) -> bytes | str:
    """``data`` with every occurrence of ``marker`` replaced by ``fvpbi`` (same type as ``data``)."""
    if not marker:
        return data
    if isinstance(data, bytes):
        return data.replace(marker.encode("ascii"), STRIPPED.encode("ascii"))
    return data.replace(marker, STRIPPED)
