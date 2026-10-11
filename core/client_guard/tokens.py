"""Stable pseudonym numbers for the Client Guard engine.

``stable_token_fn(home)`` returns the ``token_fn`` of ``SessionMap``: a keyed hash (HMAC-SHA256)
of ``prefix`` and the normalised value, folded to a five-digit number (10000..99999). The key is
32 random bytes in ``<home>/order.key``, created once and kept on this machine, so

* the same value gets the same pseudonym in every session and process, whatever else is in the
  model or in which order it was read;
* the number says nothing about the value (no sort order, no length) and cannot be recomputed
  without the key.

Key rotation (ENISA: one key forever lets the vendor link everything said about one pseudonym, for
months): ``rotate_key`` replaces the key with a new one of the NEXT GENERATION, and
``stable_token_fn(home, rotate_after_days)`` does that by itself when the key is older (config
``client_guard.key_rotation_days``, gateway start only). Every generation numbers in its own range,
so a pseudonym of an earlier generation (still in an old chat) can never be read as a client of the
new one: generation 0 (the original 32-byte key file) uses 10000..99999, generation g (1..9, a
33-byte file whose first byte is g) uses g00000..g99999; after 9 the next one is 1 again. An old
pseudonym is then simply unknown (left as it is, never restored to a wrong name).

Nothing here ever sees or stores a value beyond hashing it in memory.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from collections.abc import Callable
from pathlib import Path

KEY_FILE = "order.key"
KEY_BYTES = 32
TOKEN_MIN = 10_000
TOKEN_SPAN = 90_000
GENERATIONS = 9  # generations 1..9 after the original 0, then 1 again
GENERATION_SPAN = 100_000


def token_range(generation: int) -> tuple[int, int]:
    """``(lowest, highest)`` pseudonym number of a key generation (see the module docstring)."""
    if generation <= 0:
        return TOKEN_MIN, TOKEN_MIN + TOKEN_SPAN - 1
    low = generation * GENERATION_SPAN
    return low, low + GENERATION_SPAN - 1


def _split(raw: bytes) -> tuple[bytes, int] | None:
    if len(raw) == KEY_BYTES:
        return raw, 0
    if len(raw) == KEY_BYTES + 1 and 1 <= raw[0] <= GENERATIONS:
        return raw[1:], raw[0]
    return None


def load_key(home: str | os.PathLike[str]) -> tuple[bytes, int]:
    """``(key, generation)`` of ``<home>/order.key``, created (generation 0) when missing.
    Raises ``OSError`` / ``ValueError`` when it cannot be read or created or has a wrong size."""
    load_or_create_key(home)
    parsed = _split((Path(home) / KEY_FILE).read_bytes())
    if parsed is None:
        raise ValueError("order.key has the wrong size")
    return parsed


def key_age_days(home: str | os.PathLike[str], now: float | None = None) -> float | None:
    """Days since the key was made (its file time); None when there is no key yet."""
    try:
        made = (Path(home) / KEY_FILE).stat().st_mtime
    except OSError:
        return None
    return max(0.0, ((time.time() if now is None else now) - made) / 86_400)


def rotate_key(home: str | os.PathLike[str]) -> int:
    """Replace the key with a new random one of the next generation; returns that generation.
    The new file is written next to the old one and moved over it in one step, so a reader sees
    either the old key or the new one. Only the gateway uses the key: run it while none runs (the
    CLI ``rotate-key`` refuses otherwise; at gateway start it runs before the first pseudonym)."""
    path = Path(home) / KEY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    old = _split(path.read_bytes()) if path.is_file() else None
    generation = (old[1] % GENERATIONS + 1) if old is not None else 1
    tmp = path.with_name(f"{KEY_FILE}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    tmp.write_bytes(bytes([generation]) + secrets.token_bytes(KEY_BYTES))
    try:
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return generation


def rotate_if_due(
    home: str | os.PathLike[str], after_days: float, now: float | None = None
) -> int | None:
    """Rotate when the key is older than ``after_days`` (0 or less: never); returns the new
    generation, or None when nothing was rotated."""
    if not after_days or after_days <= 0:
        return None
    age = key_age_days(home, now)
    if age is None or age < after_days:
        return None
    return rotate_key(home)


def load_or_create_key(home: str | os.PathLike[str]) -> bytes:
    """The 32-byte key of ``<home>/order.key``; created (exclusively, so two processes starting
    together agree on one key) when missing. Raises ``OSError`` / ``ValueError`` when the key
    cannot be read or created, or is not 32 bytes."""
    path = Path(home) / KEY_FILE
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        _publish_key(path)
        try:
            os.chmod(path, 0o600)  # best effort: limited on Windows, where it only drops write
        except OSError:
            pass
    for attempt in range(20):
        parsed = _split(path.read_bytes())
        if parsed is not None:
            return parsed[0]
        time.sleep(0.05)  # another process may still be writing it (a partial file)
    raise ValueError("order.key has the wrong size")


def _publish_key(path: Path) -> None:
    """Create ``path`` with a full random key or leave the one that is there: the key is written
    to a private temporary file and hard-linked into place, which is atomic (a reader never sees a
    half-written key, two processes starting together agree on one)."""
    tmp = path.with_name(f"{KEY_FILE}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    tmp.write_bytes(secrets.token_bytes(KEY_BYTES))
    try:
        try:
            os.link(tmp, path)
        except FileExistsError:
            pass
        except OSError:  # a file system without hard links: exclusive create, then wait for it
            try:
                with open(path, "xb") as fh:
                    fh.write(tmp.read_bytes())
            except FileExistsError:
                pass
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def token_fn_from_key(key: bytes, generation: int = 0) -> Callable[[str, str], int]:
    """``token_fn(prefix, normalised_key) -> number`` in the generation's range for the given key;
    the range is on the function as ``token_range`` (``SessionMap`` keeps its numbers in it)."""
    low, high = token_range(generation)
    span = high - low + 1

    def token_fn(prefix: str, value_key: str) -> int:
        digest = hmac.new(key, f"{prefix}\x00{value_key}".encode(), hashlib.sha256).hexdigest()
        return low + int(digest, 16) % span

    token_fn.token_range = (low, high)  # type: ignore[attr-defined]
    return token_fn


def stable_token_fn(
    home: str | os.PathLike[str], rotate_after_days: float = 0
) -> Callable[[str, str], int]:
    """Stable ``token_fn`` backed by the key file in ``home`` (created on first use; rotated first
    when it is older than ``rotate_after_days``)."""
    load_or_create_key(home)
    rotate_if_due(home, rotate_after_days)
    return token_fn_from_key(*load_key(home))


def ephemeral_token_fn() -> Callable[[str, str], int]:
    """Same numbering scheme with a throw-away in-memory key (stable for this process only);
    the fallback when the key file cannot be used."""
    return token_fn_from_key(secrets.token_bytes(KEY_BYTES))
