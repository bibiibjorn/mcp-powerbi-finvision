"""WP-P wave 2 -- enhanced PBIP analyzer (P-08 + review G-20).

* P-08: column lineage uses inverted indexes (identical visual/measure output).
* G-20: relationship ends match (bare names, real cardinality), ``model_name`` is
  not "Unknown", the TMSL transform keeps the real cardinality, BPA is wired to
  ``core.analysis.bpa_analyzer``.
"""

from __future__ import annotations

import random
import sys
import types

from core.pbip import pbip_enhanced_analyzer as pea
from core.pbip.pbip_enhanced_analyzer import ColumnLineageAnalyzer, EnhancedPbipAnalyzer


def _model(n_tables=3, n_cols=4):
    tables = []
    for t in range(n_tables):
        tables.append(
            {
                "name": f"T{t}",
                "columns": [{"name": f"C{c}", "data_type": "String"} for c in range(n_cols)],
                "measures": [],
            }
        )
    return {
        "tables": tables,
        "relationships": [
            {  # TmdlModel.to_dict() shape: raw refs + bare names, no cardinality key
                "id": "r1",
                "from_column": "T0.C0",
                "to_column": "'T1'.'C1'",
                "from_table": "T0",
                "from_column_name": "C0",
                "to_table": "T1",
                "to_column_name": "C1",
                "from_cardinality": None,
                "to_cardinality": None,
                "is_active": True,
                "cross_filtering_behavior": "bothDirections",
            }
        ],
    }


def _naive(analyzer):
    """The pre-P-08 scan, kept as the reference for visuals."""
    out = {}
    deps = analyzer.dependencies
    for table in analyzer.model["tables"]:
        for col in table["columns"]:
            key = f"{table['name']}[{col['name']}]"
            out[key] = [
                {
                    "visual_key": vk,
                    "visual_type": d.get("visual_type", ""),
                    "page": d.get("page", ""),
                }
                for vk, d in deps.get("visual_dependencies", {}).items()
                if key in d.get("columns", [])
            ]
    return out


def test_visual_index_equals_naive_scan_including_duplicate_columns():
    rng = random.Random(7)
    model = _model(5, 8)
    keys = [f"{t['name']}[{c['name']}]" for t in model["tables"] for c in t["columns"]]
    vd = {}
    for i in range(300):
        cols = [rng.choice(keys) for _ in range(rng.randint(0, 6))]  # may repeat
        vd[f"v{i}"] = {"visual_type": "card", "page": f"p{i % 7}", "columns": cols}
    an = ColumnLineageAnalyzer(model, {"visual_dependencies": vd}, {"pages": []})
    lineage = an.analyze_column_lineage()
    assert {k: v["used_in_visuals"] for k, v in lineage.items()} == _naive(an)


def test_no_report_means_no_visual_usage():
    model = _model()
    vd = {"v": {"columns": ["T0[C0]"]}}
    an = ColumnLineageAnalyzer(model, {"visual_dependencies": vd}, None)
    assert an.analyze_column_lineage()["T0[C0]"]["used_in_visuals"] == []


def test_relationship_usage_matches_tmdl_shaped_relationships():
    an = ColumnLineageAnalyzer(_model(), {}, None)
    lin = an.analyze_column_lineage()
    assert lin["T0[C0]"]["used_in_relationships"] == [
        {"role": "from", "to_table": "T1", "to_column": "C1", "cardinality": "ManyToOne"}
    ]
    assert lin["T1[C1]"]["used_in_relationships"] == [
        {"role": "to", "from_table": "T0", "from_column": "C0", "cardinality": "ManyToOne"}
    ]
    assert lin["T0[C0]"]["usage_score"] == 1 and lin["T2[C0]"]["used_in_relationships"] == []


def test_calculate_column_impact_does_not_rebuild_the_whole_lineage(monkeypatch):
    an = ColumnLineageAnalyzer(_model(), {"column_to_measure": {"T0[C0]": ["[M]"]}}, None)
    monkeypatch.setattr(
        an, "analyze_column_lineage", lambda: (_ for _ in ()).throw(AssertionError("full lineage"))
    )
    impact = an.calculate_column_impact("T0[C0]")
    assert impact["direct_impact"] == {"measures": 1, "relationships": 1, "visuals": 0}
    assert an.calculate_column_impact("Nope[X]") == {"error": "Column Nope[X] not found"}


def test_relationship_quality_reads_the_real_keys():
    from core.pbip.pbip_enhanced_analyzer import RelationshipQualityAnalyzer

    m = _model()["relationships"][0]
    m["from_cardinality"], m["to_cardinality"] = "many", "many"
    res = RelationshipQualityAnalyzer({"relationships": [m]}).analyze_relationships()
    assert res["metrics"]["many_to_many"] == 1 and res["metrics"]["bi_directional"] == 1


def test_model_name_prefers_database_then_folder():
    assert EnhancedPbipAnalyzer({"database": {"name": "Sales"}})._model_name() == "Sales"
    assert EnhancedPbipAnalyzer({"model_folder": "C:/x/Fin.SemanticModel"})._model_name() == "Fin"
    assert EnhancedPbipAnalyzer({})._model_name() == "Unknown"


def test_tmsl_transform_keeps_real_relationship_ends_and_cardinality():
    an = EnhancedPbipAnalyzer(_model())
    rel = an._transform_pbip_to_tmsl(_model())["relationships"][0]
    assert (rel["fromTable"], rel["fromColumn"]) == ("T0", "C0")
    assert (rel["toTable"], rel["toColumn"]) == ("T1", "C1")
    assert (rel["fromCardinality"], rel["toCardinality"]) == ("many", "one")
    assert rel["multiplicity"] == "ManyToOne" and rel["name"] == "r1"
    assert rel["crossFilteringBehavior"] == "bothDirections"


def _fake_bpa(monkeypatch, n):
    class V:
        def __init__(self, i):
            self.rule_id, self.rule_name, self.category = f"R{i}", "n", "perf"
            self.severity = types.SimpleNamespace(name="WARNING")
            self.description, self.object_type = "d", "Column"
            self.object_name, self.table_name, self.details = f"c{i}", "T0", None

    class Fake:
        def __init__(self, rules_path=None):
            Fake.rules_path = rules_path

        def analyze_model(self, tmsl):
            Fake.tmsl = tmsl
            return [V(i) for i in range(n)]

    mod = types.ModuleType("core.analysis.bpa_analyzer")
    mod.BPAAnalyzer = Fake
    monkeypatch.setitem(sys.modules, "core.analysis.bpa_analyzer", mod)
    return Fake


def test_bpa_is_wired_to_core_analysis_and_capped(monkeypatch):
    fake = _fake_bpa(monkeypatch, 1500)
    monkeypatch.setattr(EnhancedPbipAnalyzer, "MAX_BPA_VIOLATIONS", 1000)
    res = EnhancedPbipAnalyzer(_model(), None, {}).run_full_analysis()
    bpa = res["analyses"]["bpa"]
    assert bpa["ran"] is True and bpa["violations_total"] == 1500
    assert len(bpa["violations"]) == 1000 and bpa["truncated"] is True
    assert bpa["summary"]["total"] == 1500  # the summary always counts everything
    assert fake.tmsl["relationships"][0]["fromTable"] == "T0"


def test_bpa_can_be_skipped_and_failures_are_reported(monkeypatch):
    res = EnhancedPbipAnalyzer(_model(), None, {}).run_full_analysis(include_bpa=False)
    assert "bpa" not in res["analyses"]

    class Boom:
        def __init__(self, *_a, **_k):
            raise RuntimeError("nope")

    mod = types.ModuleType("core.analysis.bpa_analyzer")
    mod.BPAAnalyzer = Boom
    monkeypatch.setitem(sys.modules, "core.analysis.bpa_analyzer", mod)
    res = EnhancedPbipAnalyzer(_model(), None, {}).run_full_analysis()
    assert res["analyses"]["bpa"] == {"ran": False, "error": "nope"}


def test_module_no_longer_imports_a_nonexistent_sibling():
    src = open(pea.__file__, encoding="utf-8").read()
    assert "from .bpa_analyzer" not in src
