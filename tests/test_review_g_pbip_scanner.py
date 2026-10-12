"""G-06: ``resolve_pbip`` + ``project_info_from_resolution`` find thin reports AND the shared model they bind to.

The old scanner only understood "one .pbip + same-name .Report + same-name
.SemanticModel": on the real G01/G02 layout (thin reports bound by
``definition.pbir -> byPath ../S0x-SemanticModel.SemanticModel`` and model-only
``.pbip`` files naming a missing report) it returned ``models []``, so every
offline analysis op failed with "No PBIP semantic models found".
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.pbip.pbir_model import resolve_pbip
from core.pbip.pbip_project_scanner import project_info_from_resolution

GOLDEN = Path(__file__).parent / "fixtures" / "pbir_golden" / "GoldenCo"


def _scan(path, exclude_folders=None) -> dict:
    path = str(path)
    res = resolve_pbip(path, exclude_folders=exclude_folders)
    base_dir = path if os.path.isdir(path) else os.path.dirname(path)
    return project_info_from_resolution(res, os.path.abspath(base_dir))


def test_folder_scan_finds_shared_model_and_links_thin_reports():
    res = _scan(GOLDEN)
    assert [m["name"] for m in res["semantic_models"]] == ["MDL-Core"]
    model = res["semantic_models"][0]
    assert Path(model["model_folder"]).name == "MDL-Core.SemanticModel"
    assert Path(model["definition_path"]).name == "definition"
    assert model["has_tmdl"] is True
    assert Path(model["pbip_file"]).name == "MDL-Core.pbip"
    assert sorted(model["reports"]) == ["RPT-Finance", "RPT-Sales"]

    reports = {r["name"]: r for r in res["reports"]}
    assert sorted(reports) == ["RPT-Finance", "RPT-Remote", "RPT-Sales"]
    for name in ("RPT-Finance", "RPT-Sales"):
        r = reports[name]
        assert r["type"] == "Report"
        assert r["linked_model"] == "../MDL-Core.SemanticModel"
        assert r["linked_model_name"] == "MDL-Core"
        assert Path(r["pbip_file"]).name == f"{name}.pbip"
    remote = reports["RPT-Remote"]
    assert remote["dataset_reference"] == "byConnection"
    assert "linked_model_name" not in remote


def test_pbip_path_is_scoped_to_that_report():
    res = _scan(GOLDEN / "RPT-Finance.pbip")
    assert [r["name"] for r in res["reports"]] == ["RPT-Finance"]
    assert [m["name"] for m in res["semantic_models"]] == ["MDL-Core"]


@pytest.mark.parametrize(
    "rel",
    ["MDL-Core.pbip", "MDL-Core.SemanticModel", "MDL-Core.SemanticModel/definition"],
)
def test_model_paths_return_model_and_every_bound_report(rel):
    res = _scan(GOLDEN / rel)
    assert [m["name"] for m in res["semantic_models"]] == ["MDL-Core"]
    assert sorted(r["name"] for r in res["reports"]) == ["RPT-Finance", "RPT-Sales"]


def test_exclude_folders_prunes(tmp_path):
    import shutil

    shutil.copytree(GOLDEN, tmp_path / "keep" / "GoldenCo")
    shutil.copytree(GOLDEN, tmp_path / "old" / "GoldenCo")
    res = _scan(tmp_path, exclude_folders=["old"])
    assert len(res["semantic_models"]) == 1
    assert all("old" not in Path(r["report_folder"]).parts for r in res["reports"])
