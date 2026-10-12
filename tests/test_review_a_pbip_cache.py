"""PBIP cache regression tests (2026-09-22 review: A-12, A-13, A-35, G-16, G-17, G-06, G-10).

* A-12 / G-17 -- the key was ``path|max(mtime)``: deleting a visual/table or an
  mtime-preserving copy never invalidated, and deleting the newest file made
  the key go BACKWARDS onto an older entry. ``invalidate`` prefix-matched
  sibling paths; Git-Bash ``/c/...`` became ``C:\\c\\...``.
* A-13 / G-16 -- a full rglob stat walk ran on EVERY call (7-14 s on G01), even
  on hits, and a ``.pbip`` path parsed every report in the folder.
* A-35 -- no in-flight de-dup: concurrent callers each ran the full parse.
* G-06 / G-10 -- thin reports bound by ``definition.pbir byPath`` had no model,
  so the cache raised and ``scan_broken_refs`` fell back to its slow path.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path

import pytest

import core.utilities.pbip_utils as pu
import server.pbip_cache as pc
from server.pbip_cache import PbipModelCache

GOLDEN = Path(__file__).parent / "fixtures" / "pbir_golden" / "GoldenCo"
P1 = "aa01overview00000001"


@pytest.fixture
def proj(tmp_path) -> Path:
    root = tmp_path / "GoldenCo"
    shutil.copytree(GOLDEN, root)
    return root


def _visuals_dir(root: Path) -> Path:
    return root / "RPT-Sales.Report" / "definition" / "pages" / P1 / "visuals"


class _Counter:
    """Wraps the real parse (or returns a sentinel) and counts calls."""

    def __init__(self, real=True, delay=0.0, side_effect=None):
        self.calls = 0
        self.real = real
        self.delay = delay
        self.side_effect = side_effect
        self._lock = threading.Lock()

    def __call__(self, scope):
        with self._lock:
            self.calls += 1
            n = self.calls
        if self.delay:
            time.sleep(self.delay)
        if self.side_effect:
            self.side_effect()
        if self.real:
            return PbipModelCache._parse_pbip(scope)
        return f"parsed#{n}:{scope.key}"


def _total_visuals(project) -> int:
    return sum(len(p.get("visuals", [])) for p in project.report_data.get("pages", []))


# ---------------------------------------------------------------- G-06 / G-10


def test_thin_report_layout_parses_with_the_bypath_model(proj):
    cache = PbipModelCache()
    folder = cache.get_or_parse(str(proj))
    names = sorted(t["name"] for t in folder.model_data["tables"])
    assert "Sales" in names and "Product Line" in names
    assert sorted(r["name"] for r in folder.project_info["reports"]) == [
        "RPT-Finance",
        "RPT-Sales",
    ]
    # .pbip path -> THAT report only (A-13 / H-13), same model
    one = cache.get_or_parse(str(proj / "RPT-Sales.pbip"))
    assert [r["name"] for r in one.project_info["reports"]] == ["RPT-Sales"]
    assert one.report_data is not None and _total_visuals(one) > 0


def test_model_only_pbip_parses(proj):
    res = PbipModelCache().get_or_parse(str(proj / "MDL-Core.pbip"))
    assert sorted(r["name"] for r in res.project_info["reports"]) == ["RPT-Finance", "RPT-Sales"]


def test_several_models_in_a_folder_is_an_explicit_ambiguity(tmp_path):
    shutil.copytree(GOLDEN, tmp_path / "A" / "GoldenCo")
    shutil.copytree(GOLDEN, tmp_path / "B" / "GoldenCo")
    with pytest.raises(ValueError, match="Ambiguous"):
        PbipModelCache().get_or_parse(str(tmp_path))


def test_no_model_raises_the_historic_message(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError, match="No PBIP semantic models found"):
        PbipModelCache().get_or_parse(str(tmp_path / "empty"))


# ---------------------------------------------------------------- A-12 / G-17


def test_deleting_a_visual_invalidates(proj):
    cache = PbipModelCache(signature_ttl_s=0)
    counter = _Counter()
    cache._parse_pbip = counter
    pbip = str(proj / "RPT-Sales.pbip")
    before = _total_visuals(cache.get_or_parse(pbip))
    assert cache.get_or_parse(pbip) is not None and counter.calls == 1  # hit
    shutil.rmtree(_visuals_dir(proj) / "vc02cardlegacy000001")
    after = _total_visuals(cache.get_or_parse(pbip))
    assert counter.calls == 2 and after == before - 1


def test_mtime_preserving_copy_invalidates(proj):
    cache = PbipModelCache(signature_ttl_s=0)
    counter = _Counter()
    cache._parse_pbip = counter
    pbip = str(proj / "RPT-Sales.pbip")
    vdir = _visuals_dir(proj)
    dir_times = {p: (p.stat().st_atime, p.stat().st_mtime) for p in [vdir]}
    before = _total_visuals(cache.get_or_parse(pbip))

    src = vdir / "vc01cardvisual000001"
    dst = vdir / "vc01cardvisual0000c2"
    shutil.copytree(src, dst)  # copy2: file mtimes preserved
    vj = json.loads((dst / "visual.json").read_text(encoding="utf-8"))
    vj["name"] = dst.name
    old = (src / "visual.json").stat()
    raw = json.dumps(vj, indent=2)
    (dst / "visual.json").write_text(raw, encoding="utf-8")
    # an extract/checkout that preserves EVERY timestamp, incl. the folders
    os.utime(dst / "visual.json", (old.st_atime, old.st_mtime))
    os.utime(dst, (src.stat().st_atime, src.stat().st_mtime))
    for p, (a, m) in dir_times.items():
        os.utime(p, (a, m))

    after = _total_visuals(cache.get_or_parse(pbip))
    assert counter.calls == 2 and after == before + 1


def test_key_cannot_go_backwards_onto_an_older_entry(proj):
    cache = PbipModelCache(signature_ttl_s=0)
    counter = _Counter(real=False)
    cache._parse_pbip = counter
    pbip = str(proj / "RPT-Sales.pbip")
    extra = proj / "MDL-Core.SemanticModel" / "definition" / "tables" / "Extra.tmdl"
    extra.write_text("table Extra\n", encoding="utf-8")
    future = time.time() + 1000
    os.utime(extra, (future, future))
    first = cache.get_or_parse(pbip)
    extra.unlink()  # newest file deleted -> old max-mtime key went BACKWARDS
    second = cache.get_or_parse(pbip)
    extra.write_text("table Extra\n", encoding="utf-8")
    os.utime(extra, (future, future))
    third = cache.get_or_parse(pbip)
    assert counter.calls == 3
    assert len({first, second, third}) == 3


def test_invalidate_matches_path_boundaries_not_prefixes(tmp_path):
    shutil.copytree(GOLDEN, tmp_path / "R01" / "GoldenCo")
    shutil.copytree(GOLDEN, tmp_path / "R010" / "GoldenCo")
    cache = PbipModelCache(max_entries=5, signature_ttl_s=60)
    counter = _Counter(real=False)
    cache._parse_pbip = counter
    a = str(tmp_path / "R01" / "GoldenCo" / "RPT-Sales.pbip")
    b = str(tmp_path / "R010" / "GoldenCo" / "RPT-Sales.pbip")
    cache.get_or_parse(a)
    cache.get_or_parse(b)
    cache.invalidate(str(tmp_path / "R01"))
    cache.get_or_parse(b)
    assert counter.calls == 2, "R010 was evicted by invalidating R01"
    cache.get_or_parse(a)
    assert counter.calls == 3


def test_msys_and_windows_paths_share_one_entry(proj):
    if os.name != "nt":
        pytest.skip("Git-Bash paths are Windows-only")
    cache = PbipModelCache(signature_ttl_s=60)
    counter = _Counter(real=False)
    cache._parse_pbip = counter
    win = str(proj / "RPT-Sales.pbip")
    drive, rest = os.path.splitdrive(win)
    msys = "/" + drive[0].lower() + rest.replace("\\", "/")
    assert cache.get_or_parse(win) is cache.get_or_parse(msys)
    assert counter.calls == 1
    assert pc.normalize_pbip_path(msys) == str(proj.resolve())


# ---------------------------------------------------------------- A-13 / G-16


def test_hit_inside_ttl_does_no_filesystem_walk(proj, monkeypatch):
    cache = PbipModelCache(signature_ttl_s=60)
    counter = _Counter(real=False)
    cache._parse_pbip = counter
    pbip = str(proj / "RPT-Sales.pbip")
    first = cache.get_or_parse(pbip)
    walks = {"n": 0}
    real_walk = pc._walk_tree

    def spy(root):
        walks["n"] += 1
        return real_walk(root)

    monkeypatch.setattr(pc, "_walk_tree", spy)
    t = time.perf_counter()
    for _ in range(20):
        assert cache.get_or_parse(pbip) is first
    per_hit_ms = (time.perf_counter() - t) * 1000 / 20
    assert walks["n"] == 0
    assert per_hit_ms < 200
    assert counter.calls == 1


def test_our_own_write_invalidates_immediately_inside_ttl(proj):
    cache = PbipModelCache(signature_ttl_s=3600)
    counter = _Counter(real=False)
    cache._parse_pbip = counter
    pu.register_write_listener(cache.invalidate)
    try:
        pbip = str(proj / "RPT-Sales.pbip")
        cache.get_or_parse(pbip)
        vj = _visuals_dir(proj) / "vc01cardvisual000001" / "visual.json"
        data = json.loads(vj.read_text(encoding="utf-8"))
        data["position"]["x"] = 1
        assert pu.save_json_file(vj, data)
        cache.get_or_parse(pbip)
        assert counter.calls == 2
        # a write to an UNRELATED report does not evict this entry
        other = proj / "RPT-Finance.Report" / "definition" / "pages" / "pages.json"
        pu.update_json_file(other, lambda d: d.__setitem__("activePageName", "x"))
        cache.get_or_parse(pbip)
        assert counter.calls == 2
    finally:
        pu.unregister_write_listener(cache.invalidate)


def test_global_cache_is_registered_as_write_listener():
    assert pc.pbip_cache.invalidate in pu._WRITE_LISTENERS


def test_external_edit_is_seen_once_the_memo_expires(proj):
    cache = PbipModelCache(signature_ttl_s=0.05)
    counter = _Counter(real=False)
    cache._parse_pbip = counter
    pbip = str(proj / "RPT-Sales.pbip")
    cache.get_or_parse(pbip)
    (_visuals_dir(proj) / "vc01cardvisual000001" / "visual.json").write_text(
        '{"name": "vc01cardvisual000001"}', encoding="utf-8"
    )
    time.sleep(0.1)
    cache.get_or_parse(pbip)
    assert counter.calls == 2


# ---------------------------------------------------------------- A-35 / G-17


def test_concurrent_callers_share_one_parse(proj):
    cache = PbipModelCache(signature_ttl_s=60)
    counter = _Counter(real=False, delay=0.3)
    cache._parse_pbip = counter
    pbip = str(proj / "RPT-Sales.pbip")
    results = []
    ts = [
        threading.Thread(target=lambda: results.append(cache.get_or_parse(pbip))) for _ in range(5)
    ]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert counter.calls == 1
    assert len(set(results)) == 1 and len(results) == 5


def test_parse_failure_propagates_to_waiters_and_is_not_cached(proj):
    cache = PbipModelCache(signature_ttl_s=60)

    def boom(scope):
        time.sleep(0.2)
        raise RuntimeError("parse failed")

    cache._parse_pbip = boom
    pbip = str(proj / "RPT-Sales.pbip")
    errors = []

    def call():
        try:
            cache.get_or_parse(pbip)
        except RuntimeError as e:
            errors.append(e)

    ts = [threading.Thread(target=call) for _ in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(errors) == 3
    cache._parse_pbip = _Counter(real=False)
    assert cache.get_or_parse(pbip).startswith("parsed#1")


def test_edit_during_parse_forces_a_reparse(proj):
    vj = _visuals_dir(proj) / "vc01cardvisual000001" / "visual.json"

    def edit():
        vj.write_text('{"name": "vc01cardvisual000001", "edited": true}', encoding="utf-8")

    cache = PbipModelCache(signature_ttl_s=3600)
    counter = _Counter(real=False, side_effect=edit)
    cache._parse_pbip = counter
    pbip = str(proj / "RPT-Sales.pbip")
    cache.get_or_parse(pbip)
    counter.side_effect = None
    cache.get_or_parse(pbip)  # memo holds the POST-parse signature -> mismatch
    assert counter.calls == 2
    cache.get_or_parse(pbip)
    assert counter.calls == 2


def test_legacy_get_max_mtime_still_tracks_visual_edits(proj):
    before = PbipModelCache._get_max_mtime(str(proj))
    vj = _visuals_dir(proj) / "vc01cardvisual000001" / "visual.json"
    future = time.time() + 100
    os.utime(vj, (future, future))
    assert PbipModelCache._get_max_mtime(str(proj)) > before
