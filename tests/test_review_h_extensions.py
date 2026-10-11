"""Deep review 2026-09-22, slice H-42 — report extension measures.

No reportExtensions.json exists in the corpus, so the shape follows the
published PBIR ``reportExtension`` schema (NEEDS LIVE CHECK, see
STATUS-WP9): top-level ``"name": "extension"``, TOM ``dataType`` casing,
delete refuses while a visual references the measure unless ``force``,
update accepts ``data_type`` (the old signature raised TypeError).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / "fixtures" / "pbir_golden" / "GoldenCo"


@pytest.fixture
def report(tmp_path) -> Path:
    root = tmp_path / "GoldenCo"
    shutil.copytree(GOLDEN, root)
    return root / "RPT-Sales.Report"


def _op(report: Path, **args):
    from server.handlers.report_operations_handler import handle_report_operations

    return handle_report_operations(
        {"operation": "manage_extension_measures", "pbip_path": str(report), **args}
    )


def _ext(report: Path) -> dict:
    return json.loads(
        (report / "definition" / "reportExtensions.json").read_text(encoding="utf-8-sig")
    )


def test_h42_add_writes_schema_shape(report):
    res = _op(
        report,
        sub_operation="add",
        measure_name="Ext Margin",
        expression="[Total Amount] * 0.1",
        table_ref="Sales",
        data_type="double",
    )
    assert res["success"], res
    ext = _ext(report)
    assert ext["name"] == "extension"
    measure = ext["entities"][0]["measures"][0]
    assert ext["entities"][0]["name"] == "Sales"
    assert measure["name"] == "Ext Margin"
    assert measure["dataType"] == "Double"


def test_h42_update_accepts_data_type(report):
    _op(report, sub_operation="add", measure_name="Ext", expression="1", table_ref="Sales")
    res = _op(report, sub_operation="update", measure_name="Ext", data_type="int64", expression="2")
    assert res["success"], res
    measure = _ext(report)["entities"][0]["measures"][0]
    assert measure["dataType"] == "Int64" and measure["expression"] == "2"


def test_h42_delete_refuses_while_referenced(report):
    _op(report, sub_operation="add", measure_name="Ext", expression="1", table_ref="Sales")
    vj = (
        report
        / "definition"
        / "pages"
        / "aa01overview00000001"
        / "visuals"
        / "vc01cardvisual000001"
        / "visual.json"
    )
    data = json.loads(vj.read_text(encoding="utf-8"))
    data["visual"]["query"]["queryState"]["Data"]["projections"][0]["field"]["Measure"][
        "Property"
    ] = "Ext"
    vj.write_text(json.dumps(data), encoding="utf-8")
    res = _op(report, sub_operation="delete", measure_name="Ext", confirm_destructive=True)
    assert res["success"] is False
    assert res["referenced_by"]
    assert _ext(report)["entities"][0]["measures"]
    res = _op(
        report, sub_operation="delete", measure_name="Ext", force=True, confirm_destructive=True
    )
    assert res["success"], res
    assert not any(e.get("measures") for e in _ext(report)["entities"])


def test_backup_and_restore_honour_dry_run(report):
    from server.handlers.report_operations_handler import handle_report_operations

    before = sorted(str(p) for p in report.parent.rglob("*"))
    res = handle_report_operations(
        {"operation": "backup", "pbip_path": str(report), "message": "x", "dry_run": True}
    )
    assert res["dry_run"] is True and res["files_modified"] == 0
    assert sorted(str(p) for p in report.parent.rglob("*")) == before
