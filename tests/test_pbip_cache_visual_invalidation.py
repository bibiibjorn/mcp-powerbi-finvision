"""Regression: editing a visual.json must invalidate the PBIP cache.

The report analyzer reads individual visual.json files to build report_data /
dependencies (field bindings, unused-column/measure analysis). Previously
_get_max_mtime excluded visual.json entirely (P2-6 perf optimization), so a
pure visual field-binding edit never bumped the cache key — 07_PBIP_Operations
query_unused/query_dependencies could serve stale results indefinitely. The
mtime walk now includes visual.json (stat-only; content still isn't re-read on
the hot path), closing the correctness hole while keeping the walk cheap.
"""

import os
import time
from pathlib import Path

from server.pbip_cache import PbipModelCache


def _make_report(tmp_path: Path) -> Path:
    root = tmp_path / "Proj"
    report = root / "Report.Report"
    vdir = report / "definition" / "pages" / "p1" / "visuals" / "v1"
    vdir.mkdir(parents=True)
    (report / "definition" / "pages" / "p1" / "page.json").write_text("{}", encoding="utf-8")
    (vdir / "visual.json").write_text('{"name":"v1"}', encoding="utf-8")
    sm = root / "Model.SemanticModel"
    (sm / "definition").mkdir(parents=True)
    (sm / "definition" / "model.tmdl").write_text("model M", encoding="utf-8")
    return root


def test_visual_json_edit_bumps_max_mtime(tmp_path):
    root = _make_report(tmp_path)
    before = PbipModelCache._get_max_mtime(str(root))

    # Edit a visual.json with a strictly newer mtime.
    vj = root / "Report.Report" / "definition" / "pages" / "p1" / "visuals" / "v1" / "visual.json"
    newer = time.time() + 100
    vj.write_text('{"name":"v1","edited":true}', encoding="utf-8")
    os.utime(vj, (newer, newer))

    after = PbipModelCache._get_max_mtime(str(root))
    assert after > before, "visual.json edit did not bump the cache-key mtime"


def test_tmdl_edit_still_bumps_max_mtime(tmp_path):
    # The existing TMDL invalidation must keep working.
    root = _make_report(tmp_path)
    before = PbipModelCache._get_max_mtime(str(root))
    tmdl = root / "Model.SemanticModel" / "definition" / "model.tmdl"
    newer = time.time() + 100
    tmdl.write_text("model M2", encoding="utf-8")
    os.utime(tmdl, (newer, newer))
    after = PbipModelCache._get_max_mtime(str(root))
    assert after > before
