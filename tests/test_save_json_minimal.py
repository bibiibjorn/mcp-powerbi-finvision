"""Tests for save_json_minimal: byte-fidelity, EOL-preserving JSON writer."""

from __future__ import annotations

from pathlib import Path

import pytest

import core.utilities.pbip_utils as pu
from core.utilities.pbip_utils import save_json_minimal


def test_no_change_returns_false_and_does_not_write(tmp_path: Path):
    f = tmp_path / "v.json"
    original = b'{\r\n  "a": 1\r\n}'
    f.write_bytes(original)
    mtime_before = f.stat().st_mtime_ns

    changed = save_json_minimal(f, original, original)

    assert changed is False
    assert f.read_bytes() == original
    # Idempotent writes must not even bump mtime — caller relies on this for diff hygiene.
    assert f.stat().st_mtime_ns == mtime_before


def test_change_writes_exact_bytes(tmp_path: Path):
    f = tmp_path / "v.json"
    original = b'{\r\n  "title": "old"\r\n}'
    new = b'{\r\n  "title": "new"\r\n}'
    f.write_bytes(original)

    changed = save_json_minimal(f, new, original)

    assert changed is True
    assert f.read_bytes() == new


def test_no_trailing_newline_added(tmp_path: Path):
    f = tmp_path / "v.json"
    original = b'{"a":1}'  # no trailing \n
    new = b'{"a":2}'
    f.write_bytes(original)

    save_json_minimal(f, new, original)

    written = f.read_bytes()
    assert not written.endswith(b"\n"), "writer must not append a trailing newline"


def test_crlf_preserved_when_input_uses_crlf(tmp_path: Path):
    f = tmp_path / "v.json"
    original = b'{\r\n  "a": 1\r\n}'
    new = b'{\r\n  "a": 2\r\n}'
    f.write_bytes(original)

    save_json_minimal(f, new, original)

    written = f.read_bytes()
    assert b"\r\n" in written and b"\n" in written
    # No bare \n that isn't part of a \r\n pair.
    assert written.replace(b"\r\n", b"").count(b"\n") == 0


def test_raise_on_error_reraises_write_failure(tmp_path: Path, monkeypatch):
    """With raise_on_error=True a real write failure must RAISE, not return
    False — cascade rename relies on this to distinguish a locked file from a
    benign no-op (both otherwise return False)."""
    f = tmp_path / "v.json"
    f.write_bytes(b'{"a":1}')

    monkeypatch.setattr(pu.os, "replace", lambda s, d: (_ for _ in ()).throw(OSError("locked")))

    with pytest.raises(OSError):
        save_json_minimal(f, b'{"a":2}', b'{"a":1}', raise_on_error=True)


def test_raise_on_error_still_silent_noop_on_unchanged_bytes(tmp_path: Path):
    """The unchanged-bytes no-op must STILL return False silently even under
    raise_on_error=True — only a real failure raises."""
    f = tmp_path / "v.json"
    same = b'{"a":1}'
    f.write_bytes(same)

    assert save_json_minimal(f, same, same, raise_on_error=True) is False


def test_default_swallows_write_failure(tmp_path: Path, monkeypatch):
    """Existing callers (raise_on_error default False) keep the old behaviour:
    a write failure is swallowed and returns False."""
    f = tmp_path / "v.json"
    f.write_bytes(b'{"a":1}')
    monkeypatch.setattr(pu.os, "replace", lambda s, d: (_ for _ in ()).throw(OSError("locked")))
    assert save_json_minimal(f, b'{"a":2}', b'{"a":1}') is False
