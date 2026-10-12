"""Review G-12 / F-17: offline ``compare_models`` (07_PBIP_Operations).

Pre-review (G-12) the handler called a ``TmdlSemanticDiff.compare()`` that did not exist and the
diff skipped roles / UDFs / calculation items / formatStringDefinition. F-17 then retired that
second engine from the op: ``compare_models`` runs ``core.comparison.compare_targets`` (one
TMSL -> ``ModelDiffer`` engine, shared with ``06 compare``) and adds the ``affects_results`` /
``cosmetic`` split. Synthetic fixture only; the TOM TMDL parser is required (skipped without it).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from server.handlers.pbip_operations_handler import handle_pbip_operations

GOLDEN = Path(__file__).parent / "fixtures" / "pbir_golden" / "GoldenCo"
DEF = "MDL-Core.SemanticModel/definition"


def _tom_available() -> bool:
    try:
        from core.infrastructure.dll_paths import load_amo_assemblies

        return bool(load_amo_assemblies())
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _tom_available(), reason="TOM client library not loadable")


def _replace(path: Path, old: str, new: str) -> None:
    raw = path.read_bytes().decode("utf-8")
    assert old in raw, f"{old!r} not in {path.name}"
    path.write_bytes(raw.replace(old, new, 1).encode("utf-8"))


def _copy(tmp: Path, name: str) -> Path:
    dst = tmp / name / "GoldenCo"
    shutil.copytree(GOLDEN, dst)
    return dst


def _edit_common(d: Path) -> None:
    _replace(d / "roles" / "Region Reader.tmdl", '{ "EU", "O\'Brien Isles" }', '{ "EU" }')
    _replace(
        d / "tables" / "Time Calc.tmdl",
        "DATESYTD(Sales[Order Date])",
        "DATESQTD(Sales[Order Date])",
    )
    _replace(d / "tables" / "Sales.tmdl", 'ScaleFormat ("K")', 'ScaleFormat ("M")')


@pytest.fixture(scope="module")
def pair(tmp_path_factory):
    """Same compatibility level on both sides: UDFs are compared too."""
    tmp = tmp_path_factory.mktemp("g12")
    src, tgt = _copy(tmp, "src"), _copy(tmp, "tgt")
    d = tgt / DEF
    _edit_common(d)
    _replace(d / "functions.tmdl", '"#,0,,.0 M"', '"#,0,,.00 M"')
    return src / "MDL-Core.SemanticModel", tgt / "MDL-Core.SemanticModel"


@pytest.fixture(scope="module")
def result(pair):
    src, tgt = pair
    res = handle_pbip_operations(
        {"operation": "compare_models", "source_path": str(src), "target_path": str(tgt)}
    )
    assert res["success"] is True, res
    return res


def _affects(result):
    return {(r["category"], r["object"]): r for r in result["affects_results"]}


def test_compare_models_runs_and_reports_changes(result):
    assert result["comparison"]["has_changes"] is True
    assert result["summary"]["total_changes"] >= 4
    assert result["summary"]["affects_results"] == len(result["affects_results"])
    assert "cosmetic" in result and "sources" in result


def test_role_filter_change(result):
    mod = result["comparison"]["roles"]["modified"]
    assert [m["name"] for m in mod] == ["Region Reader"]
    details = mod[0]["changes"]["filter_expressions"]["details"]["Product Line"][
        "filter_expression"
    ]
    assert "O'Brien Isles" in details["from"] and "O'Brien Isles" not in details["to"]
    # an unrecognised change shape is never dropped from the affects-results list
    assert ("roles", "Region Reader") in _affects(result)


def test_udf_body_change(result):
    assert [f["name"] for f in result["comparison"]["functions"]["modified"]] == ["ScaleFormat"]
    assert ("functions", "ScaleFormat") in _affects(result)


def test_calculation_item_change(result):
    key = ("calculation_items", "Time Calc.YTD")
    assert key in _affects(result)
    tbl = next(t for t in result["comparison"]["tables"]["modified"] if t["name"] == "Time Calc")
    mod = tbl["changes"]["calculation_items"]["modified"]
    assert [m["name"] for m in mod] == ["YTD"]
    assert "DATESQTD" in mod[0]["changes"]["expression"]["to"]


def test_format_string_definition_change(result):
    key = ("measures", "Sales.Amount Dynamic")
    assert _affects(result)[key]["fields"] == ["format_string_definition"]
    mod = {
        (m["table"], m["name"]): m["changes"] for m in result["comparison"]["measures"]["modified"]
    }
    assert 'ScaleFormat ("M")' in mod[("Sales", "Amount Dynamic")]["format_string_definition"]["to"]


def test_compatibility_level_change_is_reported_and_udf_gate_warns(tmp_path):
    src, tgt = _copy(tmp_path, "src"), _copy(tmp_path, "tgt")
    _replace(tgt / DEF / "database.tmdl", "compatibilityLevel: 1702", "compatibilityLevel: 1605")
    res = handle_pbip_operations(
        {
            "operation": "compare_models",
            "source_path": str(src / "MDL-Core.SemanticModel"),
            "target_path": str(tgt / "MDL-Core.SemanticModel"),
        }
    )
    assert res["success"] is True, res
    assert ("model_properties", "model.compatibility_level") in _affects(res)
    assert any("user-defined functions" in w for w in res.get("warnings", []))


def test_identical_models_have_no_changes():
    res = handle_pbip_operations(
        {
            "operation": "compare_models",
            "source_path": str(GOLDEN / "MDL-Core.SemanticModel"),
            "target_path": str(GOLDEN / "MDL-Core.SemanticModel"),
        }
    )
    assert res["success"] is True and res["comparison"]["has_changes"] is False
    assert res["affects_results"] == [] and res["cosmetic"] == []


def test_missing_paths_are_refused():
    res = handle_pbip_operations({"operation": "compare_models", "source_path": "x"})
    assert res["success"] is False and res["error_type"] == "missing_argument"


def test_compare_alias_equals_analyze():
    """``TmdlSemanticDiff`` no longer backs the op; it still feeds the HTML report's TMDL view."""
    from core.tmdl.tmdl_semantic_diff import TmdlSemanticDiff

    a = {"database": {"compatibility_level": 1600}, "tables": []}
    b = {"database": {"compatibility_level": 1601}, "tables": []}
    d = TmdlSemanticDiff(a, b)
    assert d.compare() == d.analyze()


def test_the_op_no_longer_imports_the_second_engine():
    import inspect

    from server.handlers import pbip_operations_handler as handler

    assert "TmdlSemanticDiff" not in inspect.getsource(handler._handle_compare_models)
