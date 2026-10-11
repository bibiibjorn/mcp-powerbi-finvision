"""Review C-11 / K-07: ``exports/perf`` is pruned by capture count and total size on every write."""

from __future__ import annotations

import os

from core.query_plan import exporter


def _make(root, name, size, mtime):
    d = root / name
    d.mkdir()
    (d / "QueryPlan.json").write_bytes(b"x" * size)
    os.utime(d, (mtime, mtime))
    return d


def test_keeps_only_the_newest_n_captures(tmp_path):
    for i in range(6):
        _make(tmp_path, f"cap{i}", 10, 1000 + i)
    removed = exporter.prune_perf_exports(str(tmp_path), max_captures=3, max_mb=0)
    assert sorted(removed) == ["cap0", "cap1", "cap2"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cap3", "cap4", "cap5"]


def test_size_cap_drops_oldest_first_but_never_the_new_capture(tmp_path):
    mb = 1024 * 1024
    _make(tmp_path, "old", mb, 1000)
    _make(tmp_path, "mid", mb, 2000)
    _make(tmp_path, "new", 2 * mb, 3000)  # alone above a 1 MB cap
    removed = exporter.prune_perf_exports(str(tmp_path), max_captures=0, max_mb=1, keep="new")
    assert sorted(removed) == ["mid", "old"]
    assert [p.name for p in tmp_path.iterdir()] == ["new"]


def test_limits_of_zero_disable_pruning_and_missing_root_is_fine(tmp_path):
    for i in range(4):
        _make(tmp_path, f"c{i}", 5, 1000 + i)
    assert exporter.prune_perf_exports(str(tmp_path), max_captures=0, max_mb=0) == []
    assert len(list(tmp_path.iterdir())) == 4
    assert exporter.prune_perf_exports(str(tmp_path / "nope"), max_captures=1, max_mb=1) == []


def test_defaults_come_from_config(tmp_path, monkeypatch):
    from core.config.config_manager import config

    values = {"performance.perf_export_max_captures": 2, "performance.perf_export_max_mb": 0}
    monkeypatch.setattr(config, "get", lambda key, default=None: values.get(key, default))
    for i in range(4):
        _make(tmp_path, f"c{i}", 5, 1000 + i)
    removed = exporter.prune_perf_exports(str(tmp_path))
    assert sorted(removed) == ["c0", "c1"]


def test_write_exports_prunes_but_keeps_the_fresh_capture(tmp_path, monkeypatch):
    monkeypatch.setattr(exporter, "PERF_EXPORT_ROOT", str(tmp_path))
    from core.config.config_manager import config

    values = {"performance.perf_export_max_captures": 1, "performance.perf_export_max_mb": 0}
    monkeypatch.setattr(config, "get", lambda key, default=None: values.get(key, default))
    _make(tmp_path, "stale", 5, 1000)
    cap = {
        "capture_id": "fresh",
        "daxstudio": {"server_timings": {"a": 1}, "query_plan": {"b": 2}},
    }
    out = exporter.write_exports(cap, dax_query="EVALUATE {1}")
    assert out["written"] is True and out["pruned"] == ["stale"]
    assert (tmp_path / "fresh" / "QueryPlan.json").exists()
    assert not (tmp_path / "stale").exists()
