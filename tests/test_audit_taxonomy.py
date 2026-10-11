"""F-05: the executable audit taxonomy - ``config/audit_taxonomy.json``, the per-check verdicts and
``06_Analysis_Operations full ruleset=audit`` end to end (fakes only)."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import server.handlers.analysis_handler as handler
from core.analysis import audit_taxonomy as at
from core.analysis.audit_rules import AUDIT_CHECKS
from core.analysis.bpa_analyzer import RULESET_FILES, BPAAnalyzer
from core.orchestration.analysis_orchestrator import AnalysisOrchestrator
from tests.fixtures.bpa_synthetic import build_clean_model, with_changes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: the families and ids the user's taxonomy defines (audit-categories.md)
EXPECTED = {
    "CR": range(1, 11),
    "MS": range(1, 10),
    "DR": range(1, 4),
    "DAX": range(1, 12),
    "QP": range(1, 8),
    "DOC": range(1, 7),
    "DES": range(1, 11),
    "DL": range(1, 9),
    "AI": range(1, 10),
}


@pytest.fixture(scope="module")
def tax():
    return at.load_taxonomy()


# ── the table itself ─────────────────────────────────────────────────────────


def test_taxonomy_covers_every_documented_check_id(tax):
    ids = {c["id"] for c in tax["checks"]}
    wanted = {f"{fam}{n}" for fam, rng in EXPECTED.items() for n in rng}
    assert wanted <= ids, sorted(wanted - ids)
    assert len(ids) == len(tax["checks"])  # unique
    extensions = {c["id"] for c in tax["checks"] if c.get("extension")}
    assert extensions == {"DES11", "DES12", "DES13"}  # proposed ids are flagged, never silent


def test_taxonomy_entries_are_well_formed(tax):
    for c in tax["checks"]:
        assert c["severity"] in ("blocker", "warning", "nit"), c["id"]
        assert c["status"] in tax["statuses"], c["id"]
        if c["status"] == "static":
            assert c.get("rules") or c.get("topology"), f"{c['id']} claims static with no rule"
        if c["status"] in ("requires_live", "manual"):
            assert c.get("requires") or c.get("hand_off") or c.get("applies_when"), c["id"]


def test_every_mapped_rule_id_exists_in_a_shipped_ruleset(tax):
    universe = {
        r.id for name in ("default", "microsoft") for r in BPAAnalyzer(RULESET_FILES[name]).rules
    }
    universe |= set(AUDIT_CHECKS)
    for c in tax["checks"]:
        for rid in c.get("rules", []):
            assert rid in universe, f"{c['id']} maps unknown rule {rid}"
        for tid in c.get("topology", []):
            assert tid in at.TOPOLOGY_IDS, f"{c['id']} maps unknown topology id {tid}"


def test_every_audit_rule_and_topology_finding_is_cited_by_some_check(tax):
    mapped = set(at.source_index(tax))
    assert set(AUDIT_CHECKS) <= mapped
    assert set(at.TOPOLOGY_IDS) <= mapped
    # the modern-model rules the D slice added are mapped, not duplicated
    for rid in ("UDF_MISSING_DESCRIPTION", "CALCGROUP_DUPLICATE_PRECEDENCE", "AI_NO_SYNONYMS"):
        assert rid in mapped


def test_audit_rules_do_not_duplicate_existing_rule_ids(tax):
    existing = {
        r.id for name in ("default", "microsoft") for r in BPAAnalyzer(RULESET_FILES[name]).rules
    }
    assert not (set(AUDIT_CHECKS) & existing)
    rules = at.audit_bpa_rules()
    assert {r.id for r in rules} == set(AUDIT_CHECKS)
    assert all(r.python_check is not None and not r.unsupported_reason for r in rules)


# ── tag + verdicts ───────────────────────────────────────────────────────────


def test_tag_cites_the_first_check_and_lists_the_rest(tax):
    f = {}
    assert at.tag(f, "AI_COLUMN_NO_DESCRIPTION", tax)
    assert f["audit_id"] == "DOC2" and "AI4" in f["audit_ids"]
    assert f["audit_citation"] == "audit-categories.md:DOC2"
    g = {}
    assert at.tag(g, "UDF_MISSING_DESCRIPTION", tax) and g["audit_extension"] is True
    assert at.tag({}, "SOME_USER_RULE", tax) is False


def _report(tax, **kw):
    base = dict(
        violations=[],
        topology_findings=[],
        evaluated_rule_ids=[],
        topology_ok=True,
    )
    base.update(kw)
    return at.build_report(tax, **base)


def _row(report, cid):
    return next(r for r in report["checks"] if r["id"] == cid)


def test_verdicts_distinguish_pass_fail_not_evaluated_and_not_checkable(tax):
    all_rules = [r for c in tax["checks"] for r in c.get("rules", [])]
    rep = _report(
        tax,
        violations=[
            {"rule_id": "AUDIT_RLS_ON_FACT_TABLE", "object_name": "Sales", "table_name": None},
            {"rule_id": "UDF_MISSING_DESCRIPTION", "object_name": "Local.F", "table_name": None},
        ],
        topology_findings=[{"finding_id": "FACT_TO_FACT", "object_name": "A <-> B"}],
        evaluated_rule_ids=[r for r in all_rules if r != "AUDIT_COLUMN_DATATYPE_UNKNOWN"],
    )
    assert _row(rep, "CR5")["verdict"] == "fail" and _row(rep, "CR5")["examples"] == ["Sales"]
    assert _row(rep, "DES13")["verdict"] == "fail" and _row(rep, "DES13")["extension"] is True
    assert _row(rep, "DES1")["verdict"] == "fail"  # topology finding
    assert _row(rep, "CR6")["verdict"] == "pass"  # rule ran, found nothing
    assert _row(rep, "CR3")["verdict"] == "not_evaluated"  # rule did not run: NOT a pass
    assert _row(rep, "CR3")["not_evaluated_rules"] == ["AUDIT_COLUMN_DATATYPE_UNKNOWN"]
    assert _row(rep, "CR2")["verdict"] == "engine_enforced"
    assert _row(rep, "DAX1")["verdict"] == "manual" and _row(rep, "DAX1")["hand_off"]
    assert (
        _row(rep, "QP4")["verdict"] == "requires_live" and _row(rep, "QP4")["requires"] == "trace"
    )
    assert _row(rep, "CR10")["verdict"] == "requires_live"
    assert _row(rep, "AI9")["verdict"] == "requires_live"
    s = rep["summary"]
    assert (
        s["checks_total"]
        == len(tax["checks"])
        == sum(
            s[k]
            for k in ("fail", "pass", "not_evaluated", "requires_live", "manual", "engine_enforced")
        )
    )
    assert s["findings"] == 3


def test_topology_section_failure_makes_its_checks_not_evaluated(tax):
    rep = _report(tax, topology_ok=False)
    assert _row(rep, "DES1")["verdict"] == "not_evaluated"
    assert _row(rep, "DES11")["verdict"] == "not_evaluated"


def test_vertipaq_dependent_check_is_not_a_pass_without_annotations(tax):
    rules = [r for c in tax["checks"] for r in c.get("rules", [])]
    assert _row(_report(tax, evaluated_rule_ids=rules), "MS1")["verdict"] == "not_evaluated"
    assert (
        _row(_report(tax, evaluated_rule_ids=rules, vertipaq_ran=True), "MS1")["verdict"] == "pass"
    )


# ── runtime RI (CR10) ────────────────────────────────────────────────────────


class _RIExec:
    def __init__(self, orphans):
        self.orphans = orphans
        self.queries = []

    def validate_and_execute_dax(self, query, top_n=0, **kw):
        self.queries.append(query)
        for key, value in self.orphans.items():
            if key in query:
                if isinstance(value, Exception):
                    raise value
                return (
                    value
                    if isinstance(value, dict)
                    else {"success": True, "rows": [{"[orphans]": value}]}
                )
        return {"success": True, "rows": [{"[orphans]": 0}]}


def test_runtime_ri_reports_orphans_and_never_counts_a_failed_probe_as_clean():
    model = build_clean_model(2)
    ex = _RIExec(
        {
            "'Sales'[Product Key]": 12,
            "'Returns'[Customer Key]": RuntimeError("boom"),
            "'Budget'[Store Key]": {"success": False, "error": "bad"},
        }
    )
    findings, notes = at.runtime_ri_findings(model, ex)
    assert [(cid, f["object_name"]) for cid, f in findings] == [
        ("CR10", "Sales[Product Key] -> Product[Product Key]")
    ]
    assert findings[0][1]["orphans"] == 12
    assert any("Returns" in n and "boom" in n for n in notes)
    assert any("Budget" in n for n in notes)
    # one query per active many-to-one relationship, each a read-only EVALUATE
    assert len(ex.queries) == len(model["relationships"])
    assert all(q.startswith("EVALUATE ROW(") and "RELATED(" in q for q in ex.queries)


def test_runtime_ri_skips_inactive_and_non_many_to_one_and_caps():
    model = build_clean_model(2)
    for r in model["relationships"]:
        r["isActive"] = False
    model["relationships"][0]["isActive"] = True
    model["relationships"][1].update(isActive=True, toCardinality="many")
    ex = _RIExec({})
    findings, notes = at.runtime_ri_findings(model, ex)
    assert len(ex.queries) == 1 and findings == []
    ex2 = _RIExec({})
    _f, notes2 = at.runtime_ri_findings(build_clean_model(2), ex2, max_relationships=3)
    assert len(ex2.queries) == 3 and any("limited to the first 3" in n for n in notes2)


# ── 06 full ruleset=audit, end to end ────────────────────────────────────────


class _Exec:
    def __init__(self, model, orphans=None):
        self.model = model
        self.ri = _RIExec(orphans or {})

    def get_tmsl_definition(self):
        return {"success": True, "tmsl": json.dumps(self.model)}

    def execute_info_query(self, fn, **kw):
        return {"success": True, "rows": []}

    def validate_and_execute_dax(self, query, **kw):
        return self.ri.validate_and_execute_dax(query, **kw)


def _state(model, orphans=None):
    validator = MagicMock()
    validator.validate_model_integrity.return_value = {
        "success": True,
        "total_issues": 0,
        "issues": [],
        "summary": {},
    }
    orch = AnalysisOrchestrator(config={})
    orch._run_model_settings_analysis = lambda **kw: {
        "success": True,
        "finding_count": 0,
        "findings": [],
    }
    return SimpleNamespace(
        is_connected=lambda: True,
        query_executor=_Exec(model, orphans),
        bpa_analyzer=BPAAnalyzer(RULESET_FILES["default"]),
        model_validator=validator,
        performance_optimizer=None,
        agent_policy=SimpleNamespace(analysis_orch=orch),
    )


def _audit_model():
    def mut(m):
        m["roles"] = [
            {
                "name": "Regional",
                "modelPermission": "read",
                "tablePermissions": [{"name": "Sales", "filterExpression": "[Product Key] = 1"}],
            }
        ]
        for r in m["relationships"]:
            if r["name"] == "Sales_Product":
                r["crossFilteringBehavior"] = "bothDirections"

    return with_changes(build_clean_model(6), mut)


@pytest.fixture
def audit_run(monkeypatch):
    def go(args=None, orphans=None, model=None):
        monkeypatch.setattr(handler, "connection_state", _state(model or _audit_model(), orphans))
        return handler.handle_full_analysis({"ruleset": "audit", **(args or {})})

    return go


def test_audit_ruleset_cites_taxonomy_ids_on_findings_and_issues(audit_run):
    out = audit_run()
    assert out["success"] and out["ruleset"] == "audit"
    viol = {v["rule_id"]: v for v in out["analyses"]["bpa"]["violations"]}
    assert viol["AUDIT_RLS_ON_FACT_TABLE"]["audit_id"] == "CR5"
    assert viol["AUDIT_RLS_ON_FACT_TABLE"]["audit_citation"] == "audit-categories.md:CR5"
    assert viol["AUDIT_BIDI_ON_RLS_PATH"]["audit_id"] == "CR1"
    assert viol["AVOID_BIDIRECTIONAL_RELATIONSHIPS"]["audit_id"] == "DES9"  # mapped, not duplicated
    issues = {i["rule_id"]: i for i in out["issues"] if i.get("source") == "bpa"}
    assert issues["AUDIT_RLS_ON_FACT_TABLE"]["audit_id"] == "CR5"


def test_audit_section_is_a_verdict_table_over_the_whole_taxonomy(audit_run):
    audit = audit_run()["audit"]
    rows = {r["id"]: r for r in audit["checks"]}
    assert audit["success"] and len(rows) == len(at.load_taxonomy()["checks"])
    assert rows["CR5"]["verdict"] == "fail" and rows["CR1"]["verdict"] == "fail"
    assert rows["CR6"]["verdict"] == "pass" and rows["CR9"]["verdict"] == "pass"
    assert rows["CR2"]["verdict"] == "engine_enforced"
    assert rows["QP1"]["verdict"] == "requires_live"
    assert (
        rows["CR10"]["verdict"] == "requires_live" and rows["CR10"]["requires"] == "include_runtime"
    )
    assert rows["DAX6"]["verdict"] == "manual"
    assert audit["summary"]["fail"] >= 2


def test_include_runtime_runs_ri_and_resolves_cr10(audit_run):
    out = audit_run({"include_runtime": True}, orphans={"'Sales'[Product Key]": 7})
    row = next(r for r in out["audit"]["checks"] if r["id"] == "CR10")
    assert row["verdict"] == "fail" and row["count"] == 1 and row["source"] == "runtime"
    ri = [i for i in out["issues"] if i.get("source") == "audit_runtime"]
    assert ri and ri[0]["audit_id"] == "CR10" and ri[0]["severity"] == "high"
    clean = audit_run({"include_runtime": True}, orphans={})
    assert next(r for r in clean["audit"]["checks"] if r["id"] == "CR10")["verdict"] == "pass"


def test_default_ruleset_has_no_audit_section_and_no_audit_rules(monkeypatch):
    monkeypatch.setattr(handler, "connection_state", _state(_audit_model()))
    out = handler.handle_full_analysis({})
    assert "audit" not in out
    assert not any(v["rule_id"].startswith("AUDIT_") for v in out["analyses"]["bpa"]["violations"])


def test_ambiguity_shows_up_as_a_topology_finding_cited_des11(audit_run):
    def mut(m):
        for r in m["relationships"]:
            if r["name"] == "Sales_Product":
                r["crossFilteringBehavior"] = "bothDirections"

    out = audit_run(model=with_changes(build_clean_model(2), mut))
    topo = {f["finding_id"]: f for f in out["analyses"]["topology"]["findings"]}
    assert topo["AMBIGUOUS_RELATIONSHIP_PATH"]["audit_id"] == "DES11"
    assert next(r for r in out["audit"]["checks"] if r["id"] == "DES11")["verdict"] == "fail"


def test_fix_available_flows_to_violations_and_issues(monkeypatch):
    model = build_clean_model(2)
    model["tables"][0]["columns"][0]["isHidden"] = False  # Date[Date]
    cs = _state(model)
    monkeypatch.setattr(handler, "connection_state", cs)
    out = handler.handle_full_analysis({"ruleset": "microsoft"})
    fixable = [v for v in out["analyses"]["bpa"]["violations"] if v.get("fix_available")]
    assert fixable, "the Microsoft ruleset carries machine-applicable FixExpressions"
    flagged = {(v["rule_id"], v["object_name"]) for v in fixable}
    issue_flags = {
        (i["rule_id"], i["object_name"]) for i in out["issues"] if i.get("fix_available")
    }
    assert issue_flags and issue_flags <= flagged
