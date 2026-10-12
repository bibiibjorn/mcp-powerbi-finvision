"""Review G-21 / G-22 / A-13: 07_PBIP_Operations handler arguments and queries.

* G-21 -- ``analyze`` ignored ``output_format`` / ``auto_open``; the docs file
  name was fixed (projects overwrote each other).
* G-22 -- ``query_measures display_folder`` crashed on a measure with no folder;
  ``query_dependencies`` never looked at columns (``success`` + empty
  ``referenced_by``); unqualified names took the first case-sensitive match.
* A-13 -- the handler normalised a ``.pbip`` to its FOLDER before the cache, so a
  report ``.pbip`` was analysed with every report of the folder.

Every test below fails on base ``07fd722``. Golden synthetic fixture only.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from server.handlers.pbip_operations_handler import handle_pbip_operations
from server.pbip_cache import PbipModelCache

GOLDEN = Path(__file__).parent / "fixtures" / "pbir_golden" / "GoldenCo"


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    import server.handlers.pbip_operations_handler as h

    monkeypatch.setattr(h, "_cache", PbipModelCache())


def _op(**args):
    return handle_pbip_operations(args)


# ─── G-22 ────────────────────────────────────────────────────────────


def test_query_measures_display_folder_with_folderless_measures():
    res = _op(
        operation="query_measures", pbip_path=str(GOLDEN / "RPT-Sales.pbip"), display_folder="base"
    )
    assert res["success"] is True, res
    assert [(m["table"], m["name"]) for m in res["measures"]] == [("Sales", "Total Amount")]


def test_query_dependencies_on_a_column_returns_its_referrers():
    res = _op(
        operation="query_dependencies",
        pbip_path=str(GOLDEN / "RPT-Sales.pbip"),
        object_name="sales[amount]",
    )
    assert res["success"] is True, res
    assert res["object"] == "Sales[Amount]" and res["object_type"] == "column"
    assert "Sales[Total Amount]" in res["referenced_by"]
    assert res["used_by"], "usage reasons expected for a used column"


def test_query_dependencies_ambiguous_unqualified_name():
    res = _op(
        operation="query_dependencies",
        pbip_path=str(GOLDEN / "RPT-Sales.pbip"),
        object_name="[Product Key]",
    )
    assert res["success"] is False
    assert res["error_type"] == "ambiguous"
    assert sorted(res["candidates"]) == ["Product Line[Product Key]", "Sales[Product Key]"]


def test_query_dependencies_unknown_object_is_an_error():
    res = _op(
        operation="query_dependencies",
        pbip_path=str(GOLDEN / "RPT-Sales.pbip"),
        object_name="Sales[Nope]",
    )
    assert res["success"] is False and res["error_type"] == "not_found"


# ─── A-13 ────────────────────────────────────────────────────────────


def test_report_pbip_is_scoped_to_that_report():
    fin = _op(operation="query_unused", pbip_path=str(GOLDEN / "RPT-Finance.pbip"))
    both = _op(operation="query_unused", pbip_path=str(GOLDEN / "MDL-Core.SemanticModel"))
    assert fin["success"] and both["success"]
    # RPT-Finance never shows Owner's Book[Owner]; RPT-Sales does
    assert "Owner's Book[Owner]" in fin["unused_columns"]
    assert "Owner's Book[Owner]" not in both["unused_columns"]
    assert sorted(both["reports_analyzed"]) == ["RPT-Finance.Report", "RPT-Sales.Report"]


def test_query_unused_include_reasons():
    res = _op(
        operation="query_unused", pbip_path=str(GOLDEN / "RPT-Sales.pbip"), include_reasons=True
    )
    assert "sales[total amount]" in res["usage_reasons"]
    assert res["coverage"]["complete"] is True


# ─── G-21 ────────────────────────────────────────────────────────────


def test_analyze_json_output_format(tmp_path):
    res = _op(
        operation="analyze",
        pbip_path=str(GOLDEN / "RPT-Sales.pbip"),
        output_format="json",
        output_path=str(tmp_path),
    )
    assert res["success"] is True, res
    assert res["output_format"] == "json"
    p = Path(res["json_report"])
    assert p.parent == tmp_path and p.name == "MDL-Core_analysis.json"
    body = json.loads(p.read_text(encoding="utf-8"))
    assert body["summary"]["measures"] == 6
    assert "analysis" not in res  # json = summary inline only


def test_analyze_detailed_returns_analysis_inline(tmp_path):
    res = _op(
        operation="analyze",
        pbip_path=str(GOLDEN / "RPT-Sales.pbip"),
        output_format="detailed",
        output_path=str(tmp_path),
    )
    assert res["success"] is True and "analysis" in res


def test_analyze_html_auto_open(tmp_path):
    with patch("webbrowser.open", return_value=True) as wb:
        res = _op(
            operation="analyze",
            pbip_path=str(GOLDEN / "RPT-Sales.pbip"),
            output_format="html",
            auto_open=True,
            output_path=str(tmp_path),
        )
    assert res["success"] is True, res
    assert res["opened_in_browser"] is True
    wb.assert_called_once()
    assert Path(res["html_report"]).is_file()


def test_documentation_file_name_comes_from_the_model_folder():
    from server.handlers.pbip_operations_handler import _project_name

    a = _project_name("x.pbip", {"model_folder": r"C:\r\S01-SemanticModel.SemanticModel"})
    b = _project_name("x.pbip", {"model_folder": r"C:\r\S02-SemanticModel.SemanticModel"})
    assert (a, b) == ("S01-SemanticModel", "S02-SemanticModel")
    assert _project_name(r"C:\r\Foo.pbip", {"database": {"name": "Db"}}) == "Db"
