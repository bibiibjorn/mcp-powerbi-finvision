"""Review G-08 / G-25 / G-28 / G-26: offline unused analysis (PBIP dependency engine).

The pre-review engine flagged objects that are demonstrably in use (RLS columns,
relationship keys with an UNQUOTED table, UDF bodies, formatStringDefinition /
detailRows / calc-item carriers, hierarchy levels, spaced ``NAMEOF ( T[C] )``),
silently dropped a report that failed to parse while still answering, and gave
no reason for why anything was used. Every test below fails on base ``07fd722``.

Inputs: a synthetic mini model written to ``tmp_path`` (one control object per
carrier) and the golden PBIP fixture (``tests/fixtures/pbir_golden``).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from core.pbip.pbip_dependency_engine import PbipDependencyEngine
from core.pbip.pbip_model_analyzer import TmdlModelAnalyzer
from server.pbip_cache import PbipModelCache

GOLDEN = Path(__file__).parent / "fixtures" / "pbir_golden" / "GoldenCo"

# ─── synthetic mini model: one object per DAX / structural carrier ─────

_FACT = """table Fact
\tlineageTag: 00000000-0000-4000-8000-000000000001

\tmeasure Base = SUM(Fact[Amount])
\t\tlineageTag: 00000000-0000-4000-8000-000000000101

\tmeasure 'Fmt Only' = "#,0"
\t\tlineageTag: 00000000-0000-4000-8000-000000000102

\tmeasure Shown = [Base] + CallsFn ()
\t\tlineageTag: 00000000-0000-4000-8000-000000000103

\t\tformatStringDefinition = [Fmt Only]

\tmeasure 'With Detail' = [Base]
\t\tlineageTag: 00000000-0000-4000-8000-000000000104

\t\tdetailRowsDefinition =
\t\t\t\tSELECTCOLUMNS(Fact, "d", Fact[Detail Col])

\tmeasure 'Udf Measure' = 42
\t\tlineageTag: 00000000-0000-4000-8000-000000000105

\tmeasure 'CI Measure' = 7
\t\tlineageTag: 00000000-0000-4000-8000-000000000106

\tmeasure 'Orphan Measure' = 1
\t\tlineageTag: 00000000-0000-4000-8000-000000000107

\tcolumn Amount
\t\tdataType: double
\t\tsourceColumn: Amount

\tcolumn DimKey
\t\tdataType: int64
\t\tsourceColumn: DimKey

\tcolumn 'Detail Col'
\t\tdataType: string
\t\tsourceColumn: Detail Col

\tcolumn 'Udf Col'
\t\tdataType: double
\t\tsourceColumn: Udf Col

\tcolumn 'CalcItem Col'
\t\tdataType: int64
\t\tsourceColumn: CalcItem Col

\tcolumn 'Nameof Col'
\t\tdataType: double
\t\tsourceColumn: Nameof Col

\tcolumn 'Orphan Col'
\t\tdataType: string
\t\tsourceColumn: Orphan Col

\tpartition Fact = m
\t\tmode: import
\t\tsource = #table({"Amount"}, {})
"""

_DIM = """table Dim
\tlineageTag: 00000000-0000-4000-8000-000000000002

\tcolumn DimKey
\t\tdataType: int64
\t\tsourceColumn: DimKey

\tcolumn 'Rls Key'
\t\tdataType: string
\t\tsourceColumn: Rls Key

\tcolumn 'Level Col'
\t\tdataType: string
\t\tsourceColumn: Level Col

\tcolumn 'Orphan Dim'
\t\tdataType: string
\t\tsourceColumn: Orphan Dim

\thierarchy H
\t\tlineageTag: 00000000-0000-4000-8000-000000000301

\t\tlevel L
\t\t\tlineageTag: 00000000-0000-4000-8000-000000000302
\t\t\tcolumn: 'Level Col'

\tpartition Dim = m
\t\tmode: import
\t\tsource = #table({"DimKey"}, {})
"""

_CG = """table CG
\tlineageTag: 00000000-0000-4000-8000-000000000003

\tcalculationGroup

\t\tcalculationItem Plain = SELECTEDMEASURE()

\t\tcalculationItem Scoped =
\t\t\t\tCALCULATE(SELECTEDMEASURE() + [CI Measure], Fact[CalcItem Col] = 1)

\tcolumn Name
\t\tdataType: string
\t\tsourceColumn: Name
"""

_PICK = """table Pick
\tlineageTag: 00000000-0000-4000-8000-000000000004

\tcolumn Pick
\t\tdataType: string
\t\tsourceColumn: [Value1]

\tpartition Pick = calculated
\t\tmode: import
\t\tsource =
\t\t\t\t{
\t\t\t\t    ("a", NAMEOF ( Fact[Nameof Col] ), 0)
\t\t\t\t}
"""

_RELS = """relationship 00000000-0000-4000-8000-00000000r001
\tfromColumn: Fact.DimKey
\ttoColumn: Dim.DimKey
"""

_FUNCS = """function CallsFn =
\t\t() => [Udf Measure] + SUM(Fact[Udf Col])
\tlineageTag: 00000000-0000-4000-8000-00000000f001
"""

_ROLE = """role Reader
\tmodelPermission: read

\ttablePermission Dim =
\t\t\tVAR u = USERPRINCIPALNAME ()
\t\t\tRETURN
\t\t\t    Dim[Rls Key] = u
"""

_MODEL = """model Model
\tculture: en-US

ref table Fact
ref table Dim
ref table CG
ref table Pick
"""


def _write_model(root: Path) -> Path:
    mdl = root / "Mini.SemanticModel"
    d = mdl / "definition"
    (d / "tables").mkdir(parents=True)
    (d / "roles").mkdir()
    (mdl / "definition.pbism").write_text('{"version": "4.2", "settings": {}}', encoding="utf-8")
    (d / "database.tmdl").write_text("database\n\tcompatibilityLevel: 1702\n", encoding="utf-8")
    (d / "model.tmdl").write_text(_MODEL, encoding="utf-8")
    (d / "relationships.tmdl").write_text(_RELS, encoding="utf-8")
    (d / "functions.tmdl").write_text(_FUNCS, encoding="utf-8")
    (d / "roles" / "Reader.tmdl").write_text(_ROLE, encoding="utf-8")
    for name, text in (("Fact", _FACT), ("Dim", _DIM), ("CG", _CG), ("Pick", _PICK)):
        (d / "tables" / f"{name}.tmdl").write_text(text, encoding="utf-8")
    return mdl


def _report(measures):
    """A report that binds *measures* on one visual (both old and new shapes)."""
    return {
        "report": {"filters": []},
        "pages": [
            {
                "id": "p1",
                "display_name": "Page 1",
                "filters": [],
                "visuals": [
                    {
                        "id": "v1",
                        "visual_type": "tableEx",
                        "fields": {
                            "columns": [],
                            "measures": [{"table": "Fact", "measure": m} for m in measures],
                        },
                        "field_refs": [
                            {"kind": "measure", "table": "Fact", "name": m, "location": "Values"}
                            for m in measures
                        ],
                    }
                ],
            }
        ],
        "bookmarks": [],
    }


@pytest.fixture
def mini(tmp_path):
    mdl = _write_model(tmp_path)
    model_data = TmdlModelAnalyzer().analyze_model_typed(str(mdl)).to_dict()
    deps = PbipDependencyEngine(
        model_data, _report(["Shown", "With Detail"])
    ).analyze_all_dependencies()
    return deps


# ─── G-08: every carrier counts ──────────────────────────────────────


def test_controls_stay_unused(mini):
    assert "Fact[Orphan Measure]" in mini["unused_measures"]
    assert "Fact[Orphan Col]" in mini["unused_columns"]
    assert "Dim[Orphan Dim]" in mini["unused_columns"]


def test_rls_filter_column_is_used(mini):
    # multi-line tablePermission filter (the S02 shape) was lost by the old parser
    assert "Dim[Rls Key]" not in mini["unused_columns"]


def test_unquoted_relationship_ends_are_used(mini):
    assert "Fact[DimKey]" not in mini["unused_columns"]
    assert "Dim[DimKey]" not in mini["unused_columns"]


def test_udf_body_references_are_used(mini):
    assert "Fact[Udf Col]" not in mini["unused_columns"]
    # [Udf Measure] is reached through Shown -> CallsFn () -> [Udf Measure]
    assert "Fact[Udf Measure]" not in mini["unused_measures"]
    assert mini["unused_functions"] == []


def test_format_string_definition_reference_is_used(mini):
    assert "Fact[Fmt Only]" not in mini["unused_measures"]


def test_detail_rows_reference_is_used(mini):
    assert "Fact[Detail Col]" not in mini["unused_columns"]


def test_calculation_item_references_are_used(mini):
    assert "Fact[CalcItem Col]" not in mini["unused_columns"]
    assert "Fact[CI Measure]" not in mini["unused_measures"]


def test_hierarchy_level_column_is_used(mini):
    assert "Dim[Level Col]" not in mini["unused_columns"]


# ─── G-25: spaced NAMEOF with an unquoted table ─────────────────────


def test_spaced_nameof_unquoted_table_is_used(mini):
    assert "Fact[Nameof Col]" not in mini["unused_columns"]


# ─── G-28: reasons + coverage ────────────────────────────────────────


def test_usage_reasons_name_the_consumer(mini):
    reasons = mini["usage_reasons"]
    assert any(r.startswith("RLS: Reader") for r in reasons["dim[rls key]"])
    assert any(r.startswith("relationship:") for r in reasons["fact[dimkey]"])
    assert any("CallsFn" in r for r in reasons["fact[udf col]"])
    assert any("Fact[Shown]" in r for r in reasons["fact[fmt only]"])
    assert any(r.startswith("hierarchy level") for r in reasons["dim[level col]"])
    assert any(r.startswith("visual Page 1/v1") for r in reasons["fact[shown]"])


def test_coverage_flags(mini):
    cov = mini["coverage"]
    assert cov["report_attached"] is True
    assert cov["udfs_parsed"] == 1
    assert cov["rls_filters_parsed"] == 1
    assert cov["calc_items_parsed"] == 2
    assert cov["complete"] is True


def test_model_only_run_is_not_complete(tmp_path):
    mdl = _write_model(tmp_path)
    model_data = TmdlModelAnalyzer().analyze_model_typed(str(mdl)).to_dict()
    cov = PbipDependencyEngine(model_data, None).analyze_all_dependencies()["coverage"]
    assert cov["report_attached"] is False
    assert cov["complete"] is False


# ─── golden fixture through the real cache (G-08 + G-09 + G-26) ─────


def test_golden_single_report_unused():
    deps = PbipModelCache().get_or_parse(str(GOLDEN / "RPT-Sales.Report")).dependencies
    assert sorted(deps["unused_measures"]) == ["Sales[Amount Guarded]", "m Metrics[Orphan Metric]"]
    assert sorted(deps["unused_columns"]) == ["Product Line[Unused Code]", "m Metrics[Value]"]
    # visual-level measure filter (Advanced gate) + formatStringDefinition UDF are roots
    assert "m Metrics[Show In Slot]" not in deps["unused_measures"]
    assert deps["unused_functions"] == []
    assert deps["coverage"]["reports_failed"] == []


def test_golden_multi_report_intersection():
    deps = PbipModelCache().get_or_parse(str(GOLDEN / "MDL-Core.SemanticModel")).dependencies
    # Finance alone leaves Owner's Book[Owner] unused; Sales uses it -> intersection drops it
    assert "Owner's Book[Owner]" not in deps["unused_columns"]
    assert sorted(deps["unused_columns"]) == ["Product Line[Unused Code]", "m Metrics[Value]"]
    assert sorted(deps["coverage"]["reports_analyzed"]) == [
        "RPT-Finance.Report",
        "RPT-Sales.Report",
    ]


def test_failed_report_marks_result_incomplete(tmp_path, monkeypatch):
    root = tmp_path / "GoldenCo"
    shutil.copytree(GOLDEN, root)
    from core.pbip import pbip_report_analyzer as ra

    real = ra.PbirReportAnalyzer.analyze_report

    def flaky(self, report_folder):
        if "RPT-Sales" in str(report_folder):
            raise RuntimeError("synthetic parse failure")
        return real(self, report_folder)

    monkeypatch.setattr(ra.PbirReportAnalyzer, "analyze_report", flaky)
    proj = PbipModelCache().get_or_parse(str(root / "MDL-Core.SemanticModel"))
    cov = proj.dependencies["coverage"]
    assert cov["complete"] is False
    assert [f["report"] for f in cov["reports_failed"]] == ["RPT-Sales.Report"]
    assert any("RPT-Sales.Report failed to parse" in w for w in proj.project_info["warnings"])
