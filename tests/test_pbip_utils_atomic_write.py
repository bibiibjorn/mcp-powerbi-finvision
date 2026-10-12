"""T0-1: atomic JSON write tests for core.utilities.pbip_utils.

Verifies that a crash during serialization cannot corrupt the destination file.
PBI Desktop refuses to open a project with a malformed visual.json / page.json
so the temp+replace pattern is a project-corruption safeguard.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from core.utilities import pbip_utils


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_save_json_file_writes_atomically(tmp_path: Path) -> None:
    target = tmp_path / "visual.json"
    target.write_text('{"existing": true}', encoding="utf-8")
    ok = pbip_utils.save_json_file(target, {"name": "test", "value": 1})
    assert ok is True
    data = json.loads(_read_text(target))
    assert data == {"name": "test", "value": 1}
    # Atomic write must not leave a sibling .tmp behind on success.
    assert not target.with_suffix(target.suffix + ".tmp").exists()


def test_save_json_file_failure_preserves_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "page.json"
    original = '{"original": "data"}'
    target.write_text(original, encoding="utf-8")

    real_dump = json.dump

    def crashing_dump(obj: Any, fp: Any, **kwargs: Any) -> None:  # pragma: no cover
        # Write some bytes then explode mid-stream.
        fp.write('{"partial":')
        fp.flush()
        raise OSError("simulated disk full")

    monkeypatch.setattr(json, "dump", crashing_dump)
    ok = pbip_utils.save_json_file(target, {"name": "test"})
    assert ok is False
    # Original content survives because we wrote to .tmp, not the live file.
    assert _read_text(target) == original
    # Failed temp file is cleaned up.
    assert not target.with_suffix(target.suffix + ".tmp").exists()
    monkeypatch.setattr(json, "dump", real_dump)


def test_save_json_file_handles_non_serializable(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    target.write_text('{"ok": true}', encoding="utf-8")
    ok = pbip_utils.save_json_file(target, {"bad": object()})
    assert ok is False
    # Original preserved.
    assert json.loads(_read_text(target)) == {"ok": True}
    assert not target.with_suffix(target.suffix + ".tmp").exists()


def test_save_json_minimal_atomic_on_change(tmp_path: Path) -> None:
    target = tmp_path / "x.json"
    original = b'{"a": 1}'
    new = b'{"a": 2}'
    target.write_bytes(original)
    ok = pbip_utils.save_json_minimal(target, new, original)
    assert ok is True
    assert target.read_bytes() == new
    assert not target.with_suffix(target.suffix + ".tmp").exists()


def test_save_json_minimal_no_op_when_unchanged(tmp_path: Path) -> None:
    target = tmp_path / "x.json"
    same = b'{"a": 1}'
    target.write_bytes(same)
    ok = pbip_utils.save_json_minimal(target, same, same)
    assert ok is False  # No write performed.
    assert target.read_bytes() == same
