"""Review G-10: ``scan_broken_refs`` (PBIP visual reference validator).

Pre-review, the slow path stored BARE measure names while the check required
``Table[Measure]`` (every qualified measure "broken": 3,210 false hits on R0105),
a table-qualified column fell back to bare-name matching (mis-qualified refs
never caught), and a folder with several reports scanned whichever ``iterdir``
returned first. Every test below fails on base ``07fd722``. Synthetic fixture only.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from core.pbip.pbip_visual_validator import scan_broken_visual_references

GOLDEN = Path(__file__).parent / "fixtures" / "pbir_golden" / "GoldenCo"
MATRIX = "RPT-Sales.Report/definition/pages/aa01overview00000001/visuals/vm01matrixfilters001/visual.json"


@pytest.fixture
def proj(tmp_path) -> Path:
    root = tmp_path / "GoldenCo"
    shutil.copytree(GOLDEN, root)
    return root


def _edit_matrix(root: Path, mutate) -> None:
    p = root / MATRIX
    data = json.loads(p.read_text(encoding="utf-8"))
    mutate(data)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _rows(data):
    return data["visual"]["query"]["queryState"]["Rows"]["projections"]


def _values(data):
    return data["visual"]["query"]["queryState"]["Values"]["projections"]


def test_golden_report_has_no_false_positives():
    res = scan_broken_visual_references(str(GOLDEN / "RPT-Sales.pbip"))
    assert res["success"] is True
    assert res["broken_references"] == []
    assert res["summary"]["total_references_checked"] > 20
    assert [r["report"] for r in res["reports_scanned"]] == ["RPT-Sales"]


def test_folder_scans_every_bound_report_and_names_the_skipped_one():
    res = scan_broken_visual_references(str(GOLDEN))
    assert sorted(r["report"] for r in res["reports_scanned"]) == ["RPT-Finance", "RPT-Sales"]
    skipped = {s["report"]: s["reason"] for s in res["reports_skipped"]}
    assert "byConnection" in skipped["RPT-Remote"]
    assert res["summary"]["reports_scanned"] == 2


def test_misqualified_column_is_broken(proj):
    # 'Category' exists only in 'Product Line'; bound as Sales[Category]
    def mutate(d):
        _rows(d)[0]["field"]["Column"]["Expression"]["SourceRef"]["Entity"] = "Sales"

    _edit_matrix(proj, mutate)
    res = scan_broken_visual_references(str(proj / "RPT-Sales.pbip"))
    hits = [(b["table"], b["field"], b["location"]) for b in res["broken_references"]]
    assert ("Sales", "Category", "query:Rows") in hits
    b = next(b for b in res["broken_references"] if b["field"] == "Category")
    assert "not found" in b["reason"] and b["visual_id"] == "vm01matrixfilters001"


def test_measure_in_wrong_table_names_its_home(proj):
    def mutate(d):
        _values(d)[0]["field"]["Measure"]["Expression"]["SourceRef"]["Entity"] = "m Metrics"

    _edit_matrix(proj, mutate)
    res = scan_broken_visual_references(str(proj / "RPT-Sales.Report"))
    b = next(b for b in res["broken_references"] if b["field"] == "Total Amount")
    assert b["table"] == "m Metrics"
    assert "lives in table 'Sales'" in b["reason"]
    assert res["summary"]["by_location"] == {"query": 1}


def test_unknown_source_alias_is_reported(proj):
    def mutate(d):
        filt = d["filterConfig"]["filters"][0]
        filt["field"] = {
            "Measure": {"Expression": {"SourceRef": {"Source": "zz"}}, "Property": "X"}
        }

    _edit_matrix(proj, mutate)
    res = scan_broken_visual_references(str(proj / "RPT-Sales.Report"))
    b = [b for b in res["broken_references"] if b["field"] == "X"]
    assert b and "unresolved SourceRef" in b[0]["reason"]
    assert b[0]["location"] == "filter"
