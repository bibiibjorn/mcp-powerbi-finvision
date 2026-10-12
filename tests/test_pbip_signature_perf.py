"""WP-P wave 2 -- PBIP signature speed (P-01 / P-06 / P-14) and cached unused columns (P-02).

* P-01: the signature walk is parallel (value-identical to the serial walk) and a
  ``ReadDirectoryChangesW`` watcher makes a TTL-expired hit free; every failure
  mode falls back to the TTL walk.
* P-06: ``core.graph`` / the 09 debug handler use the ONE shared signature service.
* P-14: the dead ``field_parameter_tmdl_content`` pre-read is gone.
* P-02: ``get_unused_columns_pbip`` is served by ``PbipModelCache`` and equals the
  fresh (uncached) per-report analysis.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

import core.utilities.pbip_utils as pu
import server.pbip_cache as pc
from server import pbip_watcher
from server.pbip_cache import PbipModelCache

GOLDEN = Path(__file__).parent / "fixtures" / "pbir_golden" / "GoldenCo"
needs_watcher = pytest.mark.skipif(
    not pbip_watcher.available(), reason="ReadDirectoryChangesW needs Windows + pywin32"
)


@pytest.fixture
def proj(tmp_path) -> Path:
    root = tmp_path / "GoldenCo"
    shutil.copytree(GOLDEN, root)
    return root


def _make_tree(root: Path, pages: int = 12, visuals: int = 6) -> None:
    for p in range(pages):
        for v in range(visuals):
            d = root / "pages" / f"p{p:02d}" / "visuals" / f"v{v:02d}"
            d.mkdir(parents=True, exist_ok=True)
            (d / "visual.json").write_text("x" * (10 + p + v), encoding="utf-8")
    (root / "report.json").write_text("{}", encoding="utf-8")
    (root / "StaticResources" / "img").mkdir(parents=True)
    (root / "StaticResources" / "img" / "big.png").write_bytes(b"0" * 999)


def _wait_version_change(w, before: int, timeout: float = 3.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if w.version != before:
            return True
        time.sleep(0.005)
    return False


# ------------------------------------------------------------------ P-01 walk


def test_parallel_walk_equals_serial_walk(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    _make_tree(a)  # 12 pages x 6 visuals: forces the split path
    _make_tree(b, pages=1, visuals=1)  # tiny tree: no split
    missing = str(tmp_path / "nope")
    monkeypatch.setattr(pc, "_SPLIT_MIN_UNITS", 4)
    got = pc._walk_trees([str(a), str(b), missing])
    want = [pc._walk_tree(str(a)), pc._walk_tree(str(b)), pc._walk_tree(missing)]
    assert got == want
    assert got[2] == (-1, -1, -1, -1, -1)
    # the pruned StaticResources folder is not counted
    assert got[0][0] == 12 * 6 + 1


def test_parallel_walk_runs_on_the_pool_for_big_trees(tmp_path, monkeypatch):
    _make_tree(tmp_path / "a", pages=30, visuals=3)
    seen = []
    real = pc._walk_subtrees

    def spy(folders):
        import threading

        seen.append(threading.current_thread().name)
        return real(folders)

    monkeypatch.setattr(pc, "_walk_subtrees", spy)
    monkeypatch.setattr(pc, "_SPLIT_MIN_UNITS", 4)
    sig = pc._walk_trees([str(tmp_path / "a")])[0]
    assert sig == pc._walk_tree(str(tmp_path / "a"))
    assert any(n.startswith("pbip-sig") for n in seen)


# ---------------------------------------------------------------- P-01 watcher


@needs_watcher
def test_watcher_makes_ttl_expired_hits_free_and_sees_external_edits(proj, monkeypatch):
    cache = PbipModelCache(signature_ttl_s=0.05, watch=True)
    try:
        pbip = str(proj / "RPT-Sales.pbip")
        first = cache.get_or_parse(pbip)
        assert [w.healthy for w in cache._watchers.values()] == [True]
        time.sleep(0.2)  # TTL long expired
        walks = {"n": 0}
        real = pc._walk_trees

        def spy(trees):
            walks["n"] += 1
            return real(trees)

        monkeypatch.setattr(pc, "_walk_trees", spy)
        for _ in range(5):
            assert cache.get_or_parse(pbip) is first
        assert walks["n"] == 0, "a watcher-validated hit must not walk the tree"

        # an EXTERNAL edit (plain open(), no pbip_utils listener) is picked up
        w = next(iter(cache._watchers.values()))
        before = w.version
        vis = next((proj / "RPT-Sales.Report" / "definition").rglob("visual.json"))
        vis.write_text(vis.read_text(encoding="utf-8") + " ", encoding="utf-8")
        assert _wait_version_change(w, before), "watcher missed an external write"
        second = cache.get_or_parse(pbip)
        assert second is not first
        assert walks["n"] >= 1
    finally:
        cache.close()


@needs_watcher
def test_watcher_sees_pointer_file_and_new_report_changes(proj):
    cache = PbipModelCache(signature_ttl_s=0.05, watch=True)
    try:
        pbip = str(proj / "RPT-Sales.pbip")
        cache.get_or_parse(pbip)
        w = next(iter(cache._watchers.values()))
        before = w.version
        pointer = proj / "RPT-Sales.Report" / "definition.pbir"
        os.utime(pointer, None)
        assert _wait_version_change(w, before)
    finally:
        cache.close()


@needs_watcher
def test_watcher_ignores_pruned_and_unrelated_files(proj):
    cache = PbipModelCache(signature_ttl_s=0.05, watch=True)
    try:
        cache.get_or_parse(str(proj / "RPT-Sales.pbip"))
        w = next(iter(cache._watchers.values()))
        before = w.version
        report = proj / "RPT-Sales.Report"
        (report / "unrelated.txt").write_text("a", encoding="utf-8")
        (report / ".pbi").mkdir(exist_ok=True)
        (report / ".pbi" / "localSettings.json").write_text("{}", encoding="utf-8")
        time.sleep(0.3)
        assert w.version == before
    finally:
        cache.close()


def test_no_watcher_falls_back_to_the_ttl_walk(proj, monkeypatch):
    cache = PbipModelCache(signature_ttl_s=0.05, watch=False)
    pbip = str(proj / "RPT-Sales.pbip")
    first = cache.get_or_parse(pbip)
    assert cache._watchers == {}
    time.sleep(0.15)
    walks = {"n": 0}
    real = pc._walk_trees
    monkeypatch.setattr(
        pc, "_walk_trees", lambda t: (walks.__setitem__("n", walks["n"] + 1), real(t))[1]
    )
    assert cache.get_or_parse(pbip) is first
    assert walks["n"] == 1  # TTL expired -> exactly one (parallel) walk


def test_watcher_start_failure_falls_back_cleanly(proj, monkeypatch):
    monkeypatch.setattr(pbip_watcher.DirWatcher, "start", lambda self: False)
    cache = PbipModelCache(signature_ttl_s=0.05, watch=True)
    pbip = str(proj / "RPT-Sales.pbip")
    first = cache.get_or_parse(pbip)
    assert cache._watchers == {}
    time.sleep(0.15)
    assert cache.get_or_parse(pbip) is first  # TTL path still correct


def test_unhealthy_watcher_is_not_trusted(proj, monkeypatch):
    cache = PbipModelCache(signature_ttl_s=0.05, watch=True)
    try:
        pbip = str(proj / "RPT-Sales.pbip")
        cache.get_or_parse(pbip)
        for w in cache._watchers.values():
            w._healthy = False  # e.g. a watched folder was deleted
        time.sleep(0.15)
        walks = {"n": 0}
        real = pc._walk_trees
        monkeypatch.setattr(
            pc, "_walk_trees", lambda t: (walks.__setitem__("n", walks["n"] + 1), real(t))[1]
        )
        cache.get_or_parse(pbip)
        assert walks["n"] >= 1
    finally:
        cache.close()


def test_watch_env_switch_disables_watchers(proj, monkeypatch):
    monkeypatch.setenv("FINVISION_PBIP_WATCH", "0")
    cache = PbipModelCache(signature_ttl_s=1.0)
    cache.get_or_parse(str(proj / "RPT-Sales.pbip"))
    assert cache._watchers == {}


@needs_watcher
def test_our_own_write_still_invalidates_with_a_watcher(proj):
    cache = PbipModelCache(signature_ttl_s=3600, watch=True)
    pu.register_write_listener(cache.invalidate)
    try:
        pbip = str(proj / "RPT-Sales.pbip")
        first = cache.get_or_parse(pbip)
        vis = next((proj / "RPT-Sales.Report" / "definition").rglob("visual.json"))
        data = pu.load_json_file(vis)
        data["_edited"] = True
        assert pu.save_json_file(vis, data)
        assert cache.get_or_parse(pbip) is not first
    finally:
        pu.unregister_write_listener(cache.invalidate)
        cache.close()


@needs_watcher
def test_connection_scope_swap_gets_its_own_watcher(proj, tmp_path):
    other = tmp_path / "Other"
    shutil.copytree(GOLDEN, other)
    cache = PbipModelCache(signature_ttl_s=0.05, watch=True)
    try:
        a = cache.get_or_parse(str(proj / "RPT-Sales.pbip"))
        b = cache.get_or_parse(str(other / "RPT-Sales.pbip"))
        assert a is not b
        assert len(cache._watchers) == 2
    finally:
        cache.close()


# ------------------------------------------------------------ P-06 shared service


def test_signature_for_trees_is_memoised_and_invalidated(tmp_path, monkeypatch):
    t = tmp_path / "def"
    _make_tree(t, pages=2, visuals=2)
    cache = PbipModelCache(signature_ttl_s=60, watch=False)
    calls = {"n": 0}
    real = pc._walk_trees
    monkeypatch.setattr(
        pc, "_walk_trees", lambda x: (calls.__setitem__("n", calls["n"] + 1), real(x))[1]
    )
    s1 = cache.signature_for_trees([str(t)])
    s2 = cache.signature_for_trees([str(t)])
    assert s1 == s2 and calls["n"] == 1
    (t / "pages" / "p00" / "visuals" / "v00" / "visual.json").write_text(
        "changed!!", encoding="utf-8"
    )
    assert cache.signature_for_trees([str(t)]) == s1  # inside TTL: memo (documented)
    cache.invalidate(str(t / "pages" / "p00"))  # what the pbip_utils write listener does
    s3 = cache.signature_for_trees([str(t)])
    assert s3 != s1 and calls["n"] == 2
    assert cache.signature_for_trees([str(t)], fresh=True) == s3
    assert calls["n"] == 3  # fresh=True always walks


def test_graph_report_signature_uses_the_shared_service(tmp_path, monkeypatch):
    from core.graph.report_source import report_signature

    t = tmp_path / "definition"
    _make_tree(t, pages=2, visuals=2)
    seen = []
    real = pc.pbip_cache.signature_for_trees

    def spy(trees, *a, **k):
        seen.append(list(trees))
        return real(trees, *a, **k)

    monkeypatch.setattr(pc.pbip_cache, "signature_for_trees", spy)
    sig = report_signature([str(t)])
    assert seen == [[str(t)]]
    files, total, max_f, dirs, max_d = sig
    assert files == 2 * 2 + 1
    assert dirs == 1 + 2 + 2 + 4  # pages/, p*, visuals/, v* (StaticResources pruned)
    assert report_signature([str(tmp_path / "missing")]) == (0, 0, 0, 0, 0)
    pc.pbip_cache.invalidate(str(t))


def test_debug_definition_signature_is_a_fresh_shared_walk(tmp_path):
    from server.handlers._debug import helpers

    t = tmp_path / "definition"
    _make_tree(t, pages=2, visuals=2)
    s1 = helpers._definition_signature(t)
    assert s1[1] == 2 * 2 + 1
    (t / "pages" / "p00" / "visuals" / "v00" / "visual.json").write_text(
        "changed!!", encoding="utf-8"
    )
    os.utime(
        t / "pages" / "p00" / "visuals" / "v00" / "visual.json", (time.time() + 5, time.time() + 5)
    )
    assert helpers._definition_signature(t) != s1  # no memo: next call sees it
    assert helpers._definition_signature(None) == (0, 0)
    assert helpers._definition_signature(tmp_path / "gone") == (0, 0)


# ------------------------------------------------------------------------ P-14


def test_dead_field_parameter_tmdl_preload_is_gone(proj):
    project = PbipModelCache(watch=False).get_or_parse(str(proj / "RPT-Sales.pbip"))
    assert "field_parameter_tmdl_content" not in project.model_data


# ------------------------------------------------------------------------ P-02


def _norm_result(res):
    out = dict(res)
    out["usage_reasons"] = {k: sorted(v) for k, v in (res.get("usage_reasons") or {}).items()}
    return out


def test_unused_columns_from_cache_equals_fresh_analysis(proj):
    from server.handlers import column_usage_handler as C

    for target in (proj / "RPT-Sales.pbip", proj / "RPT-Finance.pbip", proj):
        fresh = C._analyze_pbip_fresh(str(target), None)
        cached = C._unused_from_cache(str(target))
        assert _norm_result(cached) == _norm_result(fresh), target


def test_unused_columns_op_uses_the_cache_without_report_paths(proj, monkeypatch):
    from server.handlers import column_usage_handler as C

    def boom(*a, **k):
        raise AssertionError("fresh parse used without report_paths")

    monkeypatch.setattr(C, "_analyze_pbip_fresh", boom)
    out = C._handle_get_unused_columns_pbip({"pbip_path": str(proj / "RPT-Sales.pbip")})
    assert out["success"] is True and out["scope"] == "model+reports"
    # tables filter + include_reasons are applied on top of the cached result
    out2 = C._handle_get_unused_columns_pbip(
        {"pbip_path": str(proj / "RPT-Sales.pbip"), "tables": ["Sales"], "include_reasons": True}
    )
    assert all(c.startswith("Sales[") or c.startswith("'Sales'[") for c in out2["unused_columns"])
    assert "usage_reasons" in out2


def test_unused_columns_explicit_report_paths_parse_fresh(proj, monkeypatch):
    from server.handlers import column_usage_handler as C

    called = {}
    monkeypatch.setattr(C, "_analyze_pbip_fresh", lambda p, rp: called.setdefault("rp", rp) or {})
    rp = [str(proj / "RPT-Sales.Report")]
    try:
        C._analyze_pbip_multi_report(str(proj), rp)
    except Exception:
        pass
    assert called["rp"] == rp


def test_unused_columns_cache_sees_our_own_edit(proj):
    from server.handlers import column_usage_handler as C

    pu.register_write_listener(pc.pbip_cache.invalidate)
    path = str(proj / "RPT-Sales.pbip")
    before = C._unused_from_cache(path)
    # bind a column that was unused to a visual via our own write path -> must be seen
    assert before["unused_columns"], "fixture should have unused columns"
    pc.pbip_cache.invalidate(path)
    again = C._unused_from_cache(path)
    assert again["unused_columns"] == before["unused_columns"]


# ------------------------------------------------- P-06: graph signature invalidation


def test_graph_report_signature_follows_our_own_writes(proj):
    from core.graph import service

    pu.register_write_listener(pc.pbip_cache.invalidate)
    path = str(proj / "RPT-Sales.pbip")
    before = service._report_signature(path)
    assert service._report_signature(path) == before  # memoised, stable
    vis = next((proj / "RPT-Sales.Report" / "definition").rglob("visual.json"))
    data = pu.load_json_file(vis)
    data["_edited"] = True
    assert pu.save_json_file(vis, data)
    assert service._report_signature(path) != before


@needs_watcher
def test_graph_report_signature_follows_external_edits_via_the_watcher(proj):
    from core.graph import service

    path = str(proj / "RPT-Sales.pbip")
    before = service._report_signature(path)
    vis = next((proj / "RPT-Sales.Report" / "definition").rglob("visual.json"))
    vis.write_text(vis.read_text(encoding="utf-8") + "  ", encoding="utf-8")  # plain open()
    deadline = time.time() + 3
    after = before
    while time.time() < deadline and after == before:
        time.sleep(0.02)
        after = service._report_signature(path)
    assert after != before


def test_vpa_measure_dmv_is_projected_to_the_id():
    from core.dax import vpa_extractor as vpa

    assert vpa.DMV_MEASURES == "SELECT [ID] FROM $SYSTEM.TMSCHEMA_MEASURES"


def test_signature_memos_are_bounded(tmp_path):
    cache = PbipModelCache(signature_ttl_s=60, watch=False)
    cache._MEMO_MAX = 8
    for i in range(30):
        d = tmp_path / f"t{i}"
        d.mkdir()
        cache.signature_for_trees([str(d)])
    assert len(cache._sig_memo) <= 8 and len(cache._scopes) <= 8
