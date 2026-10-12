"""H-01 / K-15: PBIR writes are race-free, retried and formatting-preserving.

Regression tests for the 2026-09-22 deep review:

* H-01 -- the writer used a shared deterministic ``visual.json.tmp`` and no
  lock; two concurrent writers raced each other's ``os.replace`` / cleanup and
  the destination file could VANISH (probe p_race.py: 106/120 failed writes,
  then ``visual.json`` gone).
* K-15 -- ``os.replace`` was not retried on WinError 5/32 (AV / indexer /
  Desktop holding the target).
* Encoding traits of the replaced file (BOM, CRLF, trailing newline, raw
  UTF-8) are preserved; ``update_json_file`` is a locked read-modify-write.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

import core.utilities.pbip_utils as pu


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch):
    monkeypatch.setattr(pu, "_REPLACE_BACKOFF_S", (0.001, 0.001, 0.001, 0.001, 0.001))


def _visual(tmp_path: Path) -> Path:
    vdir = tmp_path / "M.Report" / "definition" / "pages" / "p1" / "visuals" / "v1"
    vdir.mkdir(parents=True)
    vj = vdir / "visual.json"
    vj.write_bytes(b'{\r\n  "name": "v1",\r\n  "visual": {\r\n    "objects": {}\r\n  }\r\n}')
    return vj


# --------------------------------------------------------------------- H-01


def test_concurrent_saves_never_fail_and_file_never_disappears(tmp_path):
    """Two writers x 60 saves on the SAME file (the p_race.py scenario)."""
    vj = _visual(tmp_path)
    failures: list = []
    missing: list = []
    stop = threading.Event()

    def watcher():
        while not stop.is_set():
            if not vj.exists():
                missing.append(1)

    def writer(tag):
        for i in range(60):
            data = {"name": "v1", "visual": {"objects": {f"{tag}{i}": {"k": i}}}}
            if not pu.save_json_file(vj, data):
                failures.append((tag, i))

    w = threading.Thread(target=watcher)
    w.start()
    ts = [threading.Thread(target=writer, args=(t,)) for t in ("a", "b")]
    [t.start() for t in ts]
    [t.join() for t in ts]
    stop.set()
    w.join()

    assert failures == []
    assert missing == [], "visual.json vanished during concurrent writes"
    json.loads(vj.read_text(encoding="utf-8"))  # still valid JSON
    assert list(vj.parent.glob("*.tmp")) == [], "temp files leaked"


def test_update_json_file_has_no_lost_updates(tmp_path):
    """Locked read-modify-write: every concurrent mutation survives."""
    vj = _visual(tmp_path)

    def writer(tag):
        for i in range(20):
            pu.update_json_file(
                vj, lambda d, k=f"{tag}{i}": d["visual"]["objects"].__setitem__(k, {"v": 1})
            )

    ts = [threading.Thread(target=writer, args=(t,)) for t in ("a", "b", "c")]
    [t.start() for t in ts]
    [t.join() for t in ts]
    objects = json.loads(vj.read_text(encoding="utf-8"))["visual"]["objects"]
    assert len(objects) == 60


def test_each_write_uses_a_unique_temp_file(tmp_path, monkeypatch):
    vj = _visual(tmp_path)
    seen = []
    real = pu.tempfile.mkstemp

    def spy(*a, **kw):
        fd, name = real(*a, **kw)
        seen.append(name)
        return fd, name

    monkeypatch.setattr(pu.tempfile, "mkstemp", spy)
    pu.save_json_file(vj, {"a": 1})
    pu.save_json_file(vj, {"a": 2})
    assert len(seen) == 2 and seen[0] != seen[1]
    assert all(Path(n).parent == vj.parent for n in seen)
    assert not any(n.endswith("visual.json.tmp") for n in seen)


def test_update_json_file_skip_and_noop(tmp_path):
    vj = _visual(tmp_path)
    before = vj.read_bytes()
    assert pu.update_json_file(vj, lambda d: pu.SKIP_WRITE) is False
    assert pu.update_json_file(vj, lambda d: None) is False  # unchanged bytes
    assert vj.read_bytes() == before
    with pytest.raises(FileNotFoundError):
        pu.update_json_file(tmp_path / "missing.json", lambda d: None)
    assert pu.update_json_file(tmp_path / "new.json", lambda d: {"x": 1}, create_if_missing=True)
    assert json.loads((tmp_path / "new.json").read_text(encoding="utf-8")) == {"x": 1}


def test_file_lock_is_reentrant_and_case_insensitive_on_windows(tmp_path):
    vj = _visual(tmp_path)
    with pu.file_lock(vj):
        with pu.file_lock(str(vj)):
            assert pu.save_json_file(vj, {"nested": True})
    assert pu._lock_key(str(vj).upper()) == pu._lock_key(str(vj)) or pu.os.name != "nt"


# --------------------------------------------------------------------- K-15


def _winerror(code: int) -> OSError:
    e = PermissionError(13, "locked")
    e.winerror = code  # type: ignore[attr-defined]
    return e


@pytest.mark.parametrize("code", [5, 32])
def test_replace_retries_transient_winerror(tmp_path, monkeypatch, code):
    vj = _visual(tmp_path)
    real = pu.os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise _winerror(code)
        return real(src, dst)

    monkeypatch.setattr(pu.os, "replace", flaky)
    assert pu.save_json_file(vj, {"ok": 1}) is True
    assert calls["n"] == 3
    assert json.loads(vj.read_text(encoding="utf-8")) == {"ok": 1}
    assert list(vj.parent.glob("*.tmp")) == []


def test_replace_gives_up_after_backoff_and_keeps_original(tmp_path, monkeypatch):
    vj = _visual(tmp_path)
    before = vj.read_bytes()
    calls = {"n": 0}

    def always(src, dst):
        calls["n"] += 1
        raise _winerror(32)

    monkeypatch.setattr(pu.os, "replace", always)
    assert pu.save_json_file(vj, {"ok": 1}) is False
    assert calls["n"] == len(pu._REPLACE_BACKOFF_S) + 1
    assert vj.read_bytes() == before
    assert list(vj.parent.glob("*.tmp")) == []
    with pytest.raises(OSError):
        pu.save_json_minimal(vj, b"{}", before, raise_on_error=True)


def test_non_transient_error_is_not_retried(tmp_path, monkeypatch):
    vj = _visual(tmp_path)
    calls = {"n": 0}

    def boom(src, dst):
        calls["n"] += 1
        e = OSError(28, "disk full")
        e.winerror = 112  # type: ignore[attr-defined]
        raise e

    monkeypatch.setattr(pu.os, "replace", boom)
    assert pu.save_json_file(vj, {"x": 1}) is False
    assert calls["n"] == 1


# ------------------------------------------------------------ encoding traits


def test_desktop_traits_preserved_crlf_no_trailing_newline_raw_utf8(tmp_path):
    f = tmp_path / "visual.json"
    f.write_bytes('{\r\n  "title": "Net Δ"\r\n}'.encode("utf-8"))
    assert pu.save_json_file(f, {"title": "Net Δ", "x": 1})
    raw = f.read_bytes()
    assert raw == '{\r\n  "title": "Net Δ",\r\n  "x": 1\r\n}'.encode("utf-8")


def test_lf_bom_trailing_newline_and_escapes_preserved(tmp_path):
    f = tmp_path / "page.json"
    f.write_bytes(b'\xef\xbb\xbf{\n    "t": "\\u0394"\n}\n')
    assert pu.save_json_file(f, {"t": "Δ", "y": 2})
    raw = f.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in raw and raw.endswith(b"}\n")
    assert b'    "t": "\\u0394"' in raw  # 4-space indent + escaped non-ASCII kept


def test_new_file_defaults(tmp_path):
    f = tmp_path / "new.json"
    assert pu.save_json_file(f, {"t": "Δ"})
    raw = f.read_bytes()
    nl = b"\r\n" if pu.os.name == "nt" else b"\n"
    assert raw == b"{" + nl + b'  "t": "\\u0394"' + nl + b"}"


def test_write_listener_notified(tmp_path):
    f = tmp_path / "x.json"
    got = []
    pu.register_write_listener(got.append)
    try:
        pu.save_json_file(f, {"a": 1})
        pu.save_json_minimal(f, b'{"a": 2}', f.read_bytes())
        pu.update_json_file(f, lambda d: {"a": 3})
        pu.save_json_minimal(f, f.read_bytes(), f.read_bytes())  # no-op: no event
    finally:
        pu.unregister_write_listener(got.append)
    assert got == [str(f)] * 3


def test_msys_path_normalised_on_windows():
    if pu.os.name != "nt":
        pytest.skip("MSYS paths are Windows-only")
    assert pu.normalize_path("/c/Users/x/R.Report") == "C:\\Users\\x\\R.Report"
    assert pu.normalize_path("/mnt/d/a/b") == "D:\\a\\b"
