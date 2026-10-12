"""2026-09-22 deep review, slice I — 06_Analysis_Operations orchestration.

I-06 / I-11 / I-29 (one TMSL snapshot feeds BPA, topology, M), I-13 (coverage
in the default depth), I-14 (flattened issues + correct severities), I-15
(INFO enum normalisation in ``simple``), I-20 (per-call external rules),
I-24 (``max_seconds`` bounds the run), I-25 (word-bounded TI detection).
Fakes answer with INFO-/TMSL-shaped data; nothing touches AMO/ADOMD.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import server.handlers.analysis_handler as handler
from core.analysis.bpa_analyzer import BPAAnalyzer
from core.orchestration.analysis_orchestrator import (
    AnalysisOrchestrator,
    is_time_intelligence_name,
)
from tests.fixtures.bpa_synthetic import build_clean_model, table

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _textbook():
    m = build_clean_model(8)
    table(m, "Measures")["measures"].append(
        {
            "name": "Bad",
            "expression": "SUMX ( FILTER ( 'Sales', 1 ), 1 )",
            "formatString": "#",
            "description": "d",
            "displayFolder": "F",
        }
    )
    table(m, "Sales")["partitions"][0]["source"]["expression"] = "let S = Table.Buffer(X) in S"
    m["relationships"][0]["crossFilteringBehavior"] = "bothDirections"
    return m


class _Exec:
    def __init__(self, model):
        self.model = model
        self.tmsl_calls = 0
        self.info_calls = []

    def get_tmsl_definition(self):
        self.tmsl_calls += 1
        return {"success": True, "tmsl": json.dumps(self.model)}

    def execute_info_query(self, fn, **kw):
        self.info_calls.append(fn)
        return {"success": True, "rows": []}

    def validate_and_execute_dax(self, *a, **k):  # pragma: no cover - must not be used
        raise AssertionError("no DAX expected")


def _state(model, *, bpa=True):
    ex = _Exec(model)
    validator = MagicMock()
    validator.validate_model_integrity.return_value = {
        "success": True,
        "total_issues": 1,
        "issues": [{"type": "null_keys", "severity": "high", "description": "nulls"}],
        "summary": {"critical_issues": 0, "high_issues": 1},
    }
    return SimpleNamespace(
        is_connected=lambda: True,
        query_executor=ex,
        bpa_analyzer=(
            BPAAnalyzer(os.path.join(ROOT, "config", "bpa_rules_comprehensive.json"))
            if bpa
            else None
        ),
        model_validator=validator,
        performance_optimizer=None,
    )


@pytest.fixture
def orch(monkeypatch):
    o = AnalysisOrchestrator(config={})
    monkeypatch.setattr(
        o,
        "_run_model_settings_analysis",
        lambda: {"success": True, "finding_count": 0, "findings": []},
    )
    return o


def test_full_uses_one_tmsl_snapshot_for_every_section(orch):
    cs = _state(_textbook())
    res = orch.comprehensive_analysis(cs)
    a = res["analyses"]
    assert cs.query_executor.tmsl_calls == 1
    assert a["topology"]["source"] == "tmsl" and a["topology"]["success"]
    assert a["m_practices"]["partitions_scanned"] >= 1
    assert {i["rule"] for i in a["m_practices"]["issues"]} == {"M001"}
    assert a["relationships"]["source"] == "tmsl"
    assert "RELATIONSHIPS" not in cs.query_executor.info_calls


def test_full_default_depth_reports_bpa_coverage_and_textbook_hits(orch):
    res = orch.comprehensive_analysis(_state(_textbook()))
    bpa = res["analyses"]["bpa"]
    rules = {v["rule_id"] for v in bpa["violations"]}
    assert {"AVOID_SUMX_FILTER", "AVOID_BIDIRECTIONAL_RELATIONSHIPS"} <= rules
    assert bpa["coverage"]["rules_unsupported"] == 0
    assert all(v["severity"] in ("INFO", "WARNING", "ERROR") for v in bpa["violations"])


def test_full_flattens_issues_and_counts_m_issues(orch):
    res = orch.comprehensive_analysis(_state(_textbook()))
    sources = {i["source"] for i in res["issues"]}
    assert {"bpa", "integrity", "m_practices"} <= sources
    sev = [i["severity"] for i in res["issues"]]
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    assert sev == sorted(sev, key=order.get)
    expected = (
        res["analyses"]["bpa"]["violation_count"]
        + res["analyses"]["m_practices"]["total_issues"]
        + res["analyses"]["integrity"]["total_issues"]
        + res["analyses"]["topology"]["finding_count"]
    )
    assert res["summary"]["total_issues"] == expected
    assert res["issues_total"] == len(res["issues"])


def test_recommendation_escalates_on_bpa_warning(orch):
    res = orch.comprehensive_analysis(_state(_textbook()), include_integrity=False)
    # AVOID_BIDIRECTIONAL / AVOID_SUMX_FILTER are WARNING-severity rules
    assert res["summary"]["recommendation"].startswith("WARNING")


def test_full_without_bpa_analyzer_still_runs_topology_and_m(orch):
    res = orch.comprehensive_analysis(_state(_textbook(), bpa=False))
    assert res["analyses"]["bpa"]["skipped"] is True
    assert res["analyses"]["m_practices"]["success"]


def test_max_seconds_bounds_the_whole_run(orch, monkeypatch):
    import core.orchestration.analysis_orchestrator as mod

    clock = iter([1000.0, 1000.0] + [2000.0] * 100)
    monkeypatch.setattr(mod.time, "time", lambda: next(clock))
    res = orch.comprehensive_analysis(_state(_textbook()), max_seconds=5)
    skipped = [k for k, v in res["analyses"].items() if isinstance(v, dict) and v.get("skipped")]
    assert skipped, res["analyses"].keys()


def test_extra_rules_apply_to_one_call_only(orch, tmp_path):
    from core.analysis.bpa_analyzer import load_rules_file

    p = tmp_path / "r.json"
    p.write_text(
        json.dumps(
            [{"ID": "EXT", "Name": "n", "Severity": 1, "Scope": "Model", "Expression": "true"}]
        ),
        encoding="utf-8",
    )
    cs = _state(_textbook())
    rules, _ = load_rules_file(str(p))
    with_rules = orch.comprehensive_analysis(cs, extra_rules=rules)
    without = orch.comprehensive_analysis(cs)
    assert "EXT" in {v["rule_id"] for v in with_rules["analyses"]["bpa"]["violations"]}
    assert "EXT" not in {v["rule_id"] for v in without["analyses"]["bpa"]["violations"]}


# ── handler: rules_path / ruleset ────────────────────────────────────────────


def test_handler_rules_path_is_guarded_and_reported(monkeypatch, tmp_path):
    orch = MagicMock()
    orch.comprehensive_analysis.return_value = {"success": True, "issues": []}
    cs = SimpleNamespace(
        is_connected=lambda: True,
        agent_policy=SimpleNamespace(analysis_orch=orch),
        bpa_analyzer=BPAAnalyzer(),
    )
    monkeypatch.setattr(handler, "connection_state", cs)
    out = handler.handle_full_analysis({"rules_path": "C:/Windows/System32/drivers/etc/hosts"})
    assert out["success"] is False and out["error_type"] == "path_traversal_blocked"

    good = os.path.join(ROOT, "exports", "wp10_test_rules.json")
    os.makedirs(os.path.dirname(good), exist_ok=True)
    try:
        with open(good, "w", encoding="utf-8") as fh:
            json.dump(
                [
                    {
                        "ID": "EXT",
                        "Name": "n",
                        "Severity": 1,
                        "Scope": "Model",
                        "Expression": "true",
                    },
                    {"ID": "BAD", "Severity": 9},
                ],
                fh,
            )
        before = len(cs.bpa_analyzer.rules)
        out = handler.handle_full_analysis({"rules_path": good})
        assert (
            out["external_rules"]["added"] == 1
            and out["external_rules"]["invalid"][0]["id"] == "BAD"
        )
        assert out["external_rules_merged"] == 1
        assert len(cs.bpa_analyzer.rules) == before  # not merged into the shared analyzer
        kwargs = orch.comprehensive_analysis.call_args.kwargs
        assert [r.id for r in kwargs["extra_rules"]] == ["EXT"]
    finally:
        os.remove(good)


def test_handler_ruleset_microsoft_swaps_the_analyzer(monkeypatch):
    orch = MagicMock()
    orch.comprehensive_analysis.return_value = {"success": True}
    cs = SimpleNamespace(
        is_connected=lambda: True,
        agent_policy=SimpleNamespace(analysis_orch=orch),
        bpa_analyzer=BPAAnalyzer(),
    )
    monkeypatch.setattr(handler, "connection_state", cs)
    out = handler.handle_full_analysis({"ruleset": "microsoft"})
    state = orch.comprehensive_analysis.call_args.args[0]
    assert any(r.id == "AVOID_USING_THE_IFERROR_FUNCTION" for r in state.bpa_analyzer.rules)
    assert out["ruleset"] == "microsoft"
    assert handler.handle_full_analysis({"ruleset": "nope"})["error_type"] == "invalid_input"


def test_handler_enriches_flattened_issues(monkeypatch):
    orch = MagicMock()
    orch.comprehensive_analysis.return_value = {
        "success": True,
        "issues": [{"source": "bpa", "rule_id": "X", "severity": "high", "type": "Performance"}],
    }
    cs = SimpleNamespace(
        is_connected=lambda: True,
        agent_policy=SimpleNamespace(analysis_orch=orch),
        bpa_analyzer=None,
    )
    monkeypatch.setattr(handler, "connection_state", cs)
    out = handler.handle_full_analysis({})
    assert "business_impact" in out["issues"][0] and out["impact_summary"]["high_impact"] == 1


# ── I-15: simple mode over INFO-shaped rows ──────────────────────────────────


class _InfoExec:
    """INFO.* rows as execute_info_query returns them (brackets stripped,
    TableID -> Table for MEASURES/COLUMNS, integer enums)."""

    ROWS = {
        "TABLES": [
            {"ID": 1, "Name": "Sales", "IsHidden": False},
            {"ID": 2, "Name": "Customer", "IsHidden": True},
        ],
        "COLUMNS": [
            {
                "TableID": 1,
                "Table": "Sales",
                "ExplicitName": "Amount",
                "Name": "Amount",
                "ExplicitDataType": 10,
                "DataType": 10,
                "Type": 1,
            },
            {
                "TableID": 1,
                "Table": "Sales",
                "ExplicitName": "Label",
                "Name": "Label",
                "ExplicitDataType": 2,
                "DataType": 2,
                "Type": 2,
            },
            {
                "TableID": 1,
                "Table": "Sales",
                "ExplicitName": "RowNumber-X",
                "Name": "RowNumber-X",
                "ExplicitDataType": 6,
                "Type": 3,
            },
            {
                "TableID": 2,
                "Table": "Customer",
                "ExplicitName": "Key",
                "Name": "Key",
                "ExplicitDataType": 6,
                "Type": 1,
                "IsHidden": True,
            },
        ],
        "MEASURES": [{"TableID": 1, "Table": "Sales", "Name": "Total", "DisplayFolder": "A"}],
        "PARTITIONS": [{"TableID": 1}, {"TableID": 2}],
        "RELATIONSHIPS": [
            {
                "Name": "r",
                "FromTable": "Sales",
                "FromColumn": "Key",
                "ToTable": "Customer",
                "ToColumn": "Key",
                "FromCardinality": 2,
                "ToCardinality": 1,
                "CrossFilteringBehavior": 2,
                "IsActive": True,
            }
        ],
        "ROLES": [{"ID": 7, "Name": "Region", "ModelPermission": 2}],
        "TABLEPERMISSIONS": [
            {"RoleID": 7, "TableID": 2, "FilterExpression": "[Key] = 1", "MetadataPermission": 1}
        ],
        "MODEL": [{"Name": "Model"}],
    }

    def execute_info_query(self, fn, **kw):
        return {"success": True, "rows": [dict(r) for r in self.ROWS.get(fn, [])]}

    def _get_table_name_from_id(self, tid):
        return {1: "Sales", 2: "Customer"}.get(tid)

    def execute_dmv_query(self, q):
        assert "DBSCHEMA_CATALOGS" in q
        return {"success": True, "rows": [{"CATALOG_NAME": "guid", "COMPATIBILITY_LEVEL": 1702}]}


def _info_state():
    return SimpleNamespace(
        is_connected=lambda: True, query_executor=_InfoExec(), connection_manager=None
    )


def test_simple_tables_counts_from_info_rows():
    res = AnalysisOrchestrator(config={}).list_tables_simple(_info_state())
    by = {t["name"]: t for t in res["data"]}
    assert by["Sales"]["column_count"] == 2  # rowNumber excluded
    assert by["Sales"]["measure_count"] == 1 and by["Sales"]["partition_count"] == 1
    assert by["Customer"]["is_hidden"] is True


def test_simple_relationships_enum_names():
    res = AnalysisOrchestrator(config={}).list_relationships_simple(_info_state())
    rel = res["data"][0]
    assert rel["crossFilteringBehavior"] == "BothDirections"
    assert (rel["fromCardinality"], rel["toCardinality"]) == ("Many", "One")
    analysis = handler._generate_operation_analysis("06_relationships", res)
    assert any("BI-DIRECTIONAL" in i for i in analysis["insights"])


def test_simple_columns_names_types_and_kinds():
    res = AnalysisOrchestrator(config={}).list_columns_simple(_info_state())
    cols = {c["name"]: c for t in res["data"] for c in t["columns"]}
    assert cols["Amount"]["dataType"] == "Decimal"
    assert cols["Label"]["type"] == "Calculated"
    assert not any(n.startswith("RowNumber") for n in cols)
    analysis = handler._generate_operation_analysis("05_columns", res)
    assert any("calculated columns" in f for f in analysis["key_findings"])


def test_simple_roles_read_tablepermissions():
    res = AnalysisOrchestrator(config={}).list_roles_simple(_info_state())
    role = res["data"][0]
    assert role["modelPermission"] == "Read"
    assert role["tablePermissionCount"] == 1 and role["filteredTables"] == ["Customer"]
    analysis = handler._generate_operation_analysis("08_roles", res)
    assert not any("no table permissions" in i for i in analysis["insights"])


def test_simple_stats_compatibility_level_from_dmv():
    res = AnalysisOrchestrator(config={}).simple_model_analysis(_info_state())
    assert res["model"]["compatibility_level"] == 1702
    assert res["counts"]["columns"] == 3 and res["counts"]["measures"] == 1


def test_simple_measures_carry_their_table():
    res = AnalysisOrchestrator(config={}).list_measures_simple(_info_state())
    assert res["data"][0]["table"] == "Sales"


@pytest.mark.parametrize(
    "name,ti",
    [
        ("Sales YTD", True),
        ("Margin PY %", True),
        ("Sales LY", True),
        ("Revenue last year", True),
        ("Supply", False),
        ("Monthly Budget", False),
        ("Only Active", False),
        ("Happy Customers", False),
    ],
)
def test_time_intelligence_detection_is_word_bounded(name, ti):
    assert is_time_intelligence_name(name) is ti


# ---------------------------------------------------------------- I-29 settings


class _CatalogQE:
    def __init__(self, level):
        self.level = level

    def execute_dmv_query(self, q):
        return {"success": True, "rows": [{"CATALOG_NAME": "M", "COMPATIBILITY_LEVEL": self.level}]}


def test_model_settings_come_from_tmsl_without_amo_session(monkeypatch):
    import core.operations.model_properties_manager as mpm
    from core.orchestration.analysis_orchestrator import AnalysisOrchestrator

    def boom(*a, **k):
        raise AssertionError("extra AMO session opened")

    monkeypatch.setattr(mpm.ModelPropertiesManager, "get_properties", boom)
    orch = AnalysisOrchestrator(config={})
    # Default (omitted by the TOM serializer) at CL 1606 = Automatic -> finding.
    res = orch._run_model_settings_analysis(
        tmsl_model={"tables": []}, query_executor=_CatalogQE(1606)
    )
    assert res["success"] and res["properties"]["source"] == "tmsl"
    assert res["properties"]["value_filter_behavior"] == "Automatic"
    assert res["finding_count"] == 1
    res = orch._run_model_settings_analysis(
        tmsl_model={"valueFilterBehavior": "independent"}, query_executor=_CatalogQE(1606)
    )
    assert res["finding_count"] == 0
    res = orch._run_model_settings_analysis(tmsl_model={}, query_executor=_CatalogQE(1550))
    assert res["finding_count"] == 0
