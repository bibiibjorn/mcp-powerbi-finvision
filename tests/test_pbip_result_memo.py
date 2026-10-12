"""WP-P wave 2 -- audit ops memoised until the files change (P-10).

``scan_broken_refs``, ``07_Report_Operations info`` and ``validate_model`` re-read
every report / TMDL file on each call; ``PbipModelCache.memoize_by_trees`` keeps
the result for as long as the signature of the folders they read is unchanged.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

import core.utilities.pbip_utils as pu
import server.pbip_cache as pc
from server.pbip_cache import PbipModelCache

GOLDEN = Path(__file__).parent / "fixtures" / "pbir_golden" / "GoldenCo"


@pytest.fixture
def proj(tmp_path) -> Path:
    root = tmp_path / "GoldenCo"
    shutil.copytree(GOLDEN, root)
    pc.pbip_cache.invalidate(str(root))
    return root


def _first_visual(root: Path) -> Path:
    return next((root / "RPT-Sales.Report" / "definition").rglob("visual.json"))


# --------------------------------------------------------- memoize_by_trees


def test_factory_runs_once_until_the_tree_changes(tmp_path):
    tree = tmp_path / "t"
    tree.mkdir()
    (tree / "a.json").write_text("{}", encoding="utf-8")
    cache = PbipModelCache(signature_ttl_s=60, watch=False)
    calls = []

    def factory():
        calls.append(1)
        return {"n": len(calls), "items": [1, 2]}

    first = cache.memoize_by_trees("op", "k", [str(tree)], factory)
    again = cache.memoize_by_trees("op", "k", [str(tree)], factory)
    assert first == again == {"n": 1, "items": [1, 2]} and len(calls) == 1

    (tree / "b.json").write_text("{}", encoding="utf-8")
    cache.invalidate(str(tree))  # what the pbip_utils write listener does
    third = cache.memoize_by_trees("op", "k", [str(tree)], factory)
    assert third["n"] == 2 and len(calls) == 2
    # other key / other op name: separate entries
    cache.memoize_by_trees("op", "other", [str(tree)], factory)
    cache.memoize_by_trees("op2", "k", [str(tree)], factory)
    assert len(calls) == 4


def test_results_are_copied_in_and_out_and_errors_are_not_memoised(tmp_path):
    tree = tmp_path / "t"
    tree.mkdir()
    cache = PbipModelCache(signature_ttl_s=60, watch=False)
    n = {"calls": 0}

    def factory():
        n["calls"] += 1
        return {"rows": [1]}

    a = cache.memoize_by_trees("op", "k", [str(tree)], factory)
    a["rows"].append("mutated by the caller")
    b = cache.memoize_by_trees("op", "k", [str(tree)], factory)
    assert b == {"rows": [1]} and n["calls"] == 1

    def boom():
        raise RuntimeError("nope")

    with pytest.raises(RuntimeError):
        cache.memoize_by_trees("bad", "k", [str(tree)], boom)
    with pytest.raises(RuntimeError):
        cache.memoize_by_trees("bad", "k", [str(tree)], boom)  # re-run, not served from memo


def test_memo_is_bounded(tmp_path):
    tree = tmp_path / "t"
    tree.mkdir()
    cache = PbipModelCache(signature_ttl_s=60, watch=False)
    cache._result_memo_max = 3
    for i in range(8):
        cache.memoize_by_trees("op", i, [str(tree)], lambda: {"i": 1})
    assert len(cache._result_memo) == 3


# ------------------------------------------------------------------- handlers


def test_scan_broken_refs_is_memoised_and_sees_an_edit(proj, monkeypatch):
    from core.pbip import pbip_visual_validator as v
    from server.handlers import pbip_operations_handler as P

    calls = {"n": 0}
    real = v.scan_broken_visual_references

    def spy(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(v, "scan_broken_visual_references", spy)
    path = str(proj / "RPT-Sales.pbip")
    first = P._handle_scan_broken_refs({"pbip_path": path})
    again = P._handle_scan_broken_refs({"pbip_path": path})
    assert first["success"] is True and again == first and calls["n"] == 1

    # our own write (pbip_utils -> write listener) invalidates
    vis = _first_visual(proj)
    data = pu.load_json_file(vis)
    data["_touched"] = True
    assert pu.save_json_file(vis, data)
    P._handle_scan_broken_refs({"pbip_path": path})
    assert calls["n"] == 2


def test_scan_broken_refs_sees_an_external_edit_through_the_watcher(proj, monkeypatch):
    from core.pbip import pbip_visual_validator as v
    from server import pbip_watcher
    from server.handlers import pbip_operations_handler as P

    if not pbip_watcher.available():
        pytest.skip("needs ReadDirectoryChangesW")
    calls = {"n": 0}
    real = v.scan_broken_visual_references
    monkeypatch.setattr(
        v,
        "scan_broken_visual_references",
        lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1), real(*a, **k))[1],
    )
    path = str(proj / "RPT-Sales.pbip")
    P._handle_scan_broken_refs({"pbip_path": path})
    P._handle_scan_broken_refs({"pbip_path": path})
    assert calls["n"] == 1
    vis = _first_visual(proj)
    vis.write_text(vis.read_text(encoding="utf-8") + " ", encoding="utf-8")  # plain open()
    deadline = time.time() + 3
    while time.time() < deadline and calls["n"] == 1:
        time.sleep(0.05)
        P._handle_scan_broken_refs({"pbip_path": path})
    assert calls["n"] == 2


def test_report_info_is_memoised_per_args_and_invalidated_by_a_write(proj, monkeypatch):
    from server.handlers import report_operations_handler as R
    from server.handlers._internal import report_info_helpers as H

    calls = {"n": 0}
    real = H.handle_report_info
    monkeypatch.setattr(
        H, "handle_report_info", lambda a: (calls.__setitem__("n", calls["n"] + 1), real(a))[1]
    )
    rep = str(proj / "RPT-Sales.Report")
    a = R._op_info({"operation": "info", "pbip_path": rep})
    b = R._op_info({"operation": "info", "pbip_path": rep})
    assert a == b and a.get("success") is not False and calls["n"] == 1
    R._op_info({"operation": "info", "pbip_path": rep, "summary_only": False})  # other args
    assert calls["n"] == 2

    vis = _first_visual(proj)
    data = pu.load_json_file(vis)
    data["_touched"] = True
    assert pu.save_json_file(vis, data)
    R._op_info({"operation": "info", "pbip_path": rep})
    assert calls["n"] == 3


def test_report_info_with_a_bad_path_is_not_memoised():
    from server.handlers import report_operations_handler as R

    out = R._op_info({"operation": "info", "pbip_path": "C:/definitely/not/here"})
    assert out["success"] is False


def test_validate_model_is_memoised_until_the_tmdl_changes(proj, monkeypatch):
    from core.tmdl import validator as V
    from server.handlers import pbip_operations_handler as P

    calls = {"n": 0}
    real = V.TmdlValidator.validate_syntax

    def spy(self, *a, **k):
        calls["n"] += 1
        return real(self, *a, **k)

    monkeypatch.setattr(V.TmdlValidator, "validate_syntax", spy)
    args = {"pbip_path": str(proj / "RPT-Sales.pbip"), "tom_check": "off"}
    first = P._handle_validate_model(args)
    again = P._handle_validate_model(args)
    assert first["success"] is True and again == first and calls["n"] == 1

    table = next((proj / "MDL-Core.SemanticModel" / "definition" / "tables").glob("*.tmdl"))
    table.write_text(table.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    pc.pbip_cache.invalidate(str(table))  # writers outside pbip_utils notify like this
    P._handle_validate_model(args)
    assert calls["n"] == 2
