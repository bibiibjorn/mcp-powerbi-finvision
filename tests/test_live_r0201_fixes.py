"""Fixes found by the 2026-10-03 live test round on R0201 (Desktop 2.157, CL 1702).

Each test pins one behaviour that was wrong against the real model and is not visible with the
earlier fakes.
"""

import shutil
from pathlib import Path

import pytest

from core.analysis.info_normalize import dax_referenced_tables
from core.analysis.star_schema_validator import StarSchemaValidator
from tests.test_pbip_preflight import GOLDEN, _defn, _edit, _git, _run


def _tmsl():
    return {
        "tables": [
            {
                "name": "Sales",
                "columns": [{"name": "Amount"}, {"name": "Key"}],
                "measures": [
                    {
                        "name": "Rate",
                        "expression": "SUM ( Sales[Amount] ) * SELECTEDVALUE ( 's_Rate Type'[Rate] )",
                    }
                ],
            },
            {"name": "Dim", "columns": [{"name": "Key"}]},
            # disconnected slicer table read only through DAX
            {"name": "s_Rate Type", "columns": [{"name": "Rate"}, {"name": "Label"}]},
            # disconnected and read by nothing: a real orphan
            {"name": "Leftover", "columns": [{"name": "A"}, {"name": "B"}]},
            # read only from a role filter
            {"name": "r_Users", "columns": [{"name": "UPN"}, {"name": "Key"}]},
        ],
        "relationships": [
            {"fromTable": "Sales", "fromColumn": "Key", "toTable": "Dim", "toColumn": "Key"}
        ],
        "roles": [
            {
                "name": "RLS",
                "tablePermissions": [
                    {
                        "name": "Dim",
                        "filterExpression": "Dim[Key] IN CALCULATETABLE ( VALUES ( r_Users[Key] ), r_Users[UPN] = USERPRINCIPALNAME () )",
                    }
                ],
            }
        ],
    }


def test_dax_referenced_tables_covers_measures_and_role_filters():
    refs = set(dax_referenced_tables(_tmsl()))
    assert {"s_Rate Type", "r_Users"} <= refs
    # Sales (its own measure) and Dim (its own role filter) read only themselves: not "read"
    assert "Leftover" not in refs and "Sales" not in refs and "Dim" not in refs


def test_tables_read_by_dax_are_not_reported_as_disconnected():
    """R0201: CR4 reported 71 'orphaned' tables, nearly all slicer / helper / RLS tables that
    measures and role filters read."""
    from core.analysis import info_normalize as infn

    model = _tmsl()
    tables = infn.tables_from_tmsl(model)
    rels = [
        {"fromTable": "Sales", "toTable": "Dim", "fromCardinality": "Many", "toCardinality": "One"}
    ]
    findings = StarSchemaValidator().validate(
        {
            "tables": tables,
            "relationships": rels,
            "daxReferencedTables": dax_referenced_tables(model),
        }
    )
    disconnected = {f.object_name for f in findings if f.finding_id == "DISCONNECTED_TABLE"}
    assert disconnected == {"Leftover"}


def test_cr4_counts_the_validated_topology_finding_only():
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    tax = json.loads((root / "config" / "audit_taxonomy.json").read_text(encoding="utf-8"))

    def find(o):
        if isinstance(o, dict):
            if o.get("id") == "CR4":
                return o
            for v in o.values():
                hit = find(v)
                if hit:
                    return hit
        if isinstance(o, list):
            for v in o:
                hit = find(v)
                if hit:
                    return hit
        return None

    cr4 = find(tax)
    assert cr4 is not None
    assert cr4["rules"] == []
    assert cr4["topology"] == ["DISCONNECTED_TABLE"]


def test_audit_interactions_default_output_is_compact():
    """R0201 'P&L Statement' returned 190 KB; the default now keeps verdicts + counts."""
    from server.handlers.report_operations_handler import _compact_interaction_audit

    pair = {"visual": "v1", "title": "t", "type": "card", "rule": "default"}
    result = {
        "pages": [
            {
                "slicers": [
                    {
                        "slicer": "s1",
                        "needed": [pair] * 40,
                        "no_effect": [pair] * 300,
                        "missing_no_filter": [pair] * 280,
                        "wrong_no_filter": [pair] * 2,
                        "reach_tables": [f"t{i}" for i in range(30)],
                    }
                ],
                "suggested": {
                    "add_no_filter": [pair] * 100,
                    "remove_dangling": [pair] * 3,
                    "review_rules": [],
                },
            }
        ],
        "suite_plan": [
            {
                "slicer": "s1",
                "must_change": [pair, pair],
                "must_not_change": [{**pair, "reason": "model_unreachable"}],
            }
        ],
    }
    _compact_interaction_audit(result)
    sl = result["pages"][0]["slicers"][0]
    assert "needed" not in sl and sl["needed_count"] == 40
    assert sl["no_effect_count"] == 300 and sl["missing_no_filter_count"] == 280
    assert sl["wrong_no_filter"] == [pair] * 2
    assert sl["reach_tables_count"] == 30 and len(sl["reach_tables"]) == 10
    sug = result["pages"][0]["suggested"]
    assert len(sug["add_no_filter"]) == 25 and sug["add_no_filter_count"] == 100
    assert len(sug["remove_dangling"]) == 3 and "remove_dangling_count" not in sug
    # the per-pair object shape consumers read stays; only title / type go
    row = result["suite_plan"][0]
    assert row["must_change"] == [{"visual": "v1"}, {"visual": "v1"}]
    assert row["must_not_change"] == [{"visual": "v1", "reason": "model_unreachable"}]
    assert result["compact"] is True


def test_cleanup_plan_evidence_is_compact_by_default():
    from server.handlers.dependency_graph_handler import _compact_cleanup_evidence

    rec = {"safe": True, "breaks": [], "warning": "x" * 200}
    plan = {
        "stages": [
            {
                "evidence": {
                    "impacts": {"a": rec, "b": rec, "c": {"safe": False, "breaks": ["v"]}},
                    "unsafe_nodes": ["c"],
                }
            }
        ]
    }
    _compact_cleanup_evidence(plan)
    ev = plan["stages"][0]["evidence"]
    assert ev["safe_count"] == 2 and list(ev["impacts"]) == ["c"]
    assert ev["impacts_compacted"] is True


def test_equivalence_measure_name_with_parentheses_is_bracketed():
    """R0201: measure_name 'PL | % of Revenue (Managerial)' was sent as raw DAX (the '(' made
    it look like an expression) and every cell failed with a syntax error."""
    from core.validation.equivalence_grid import _measure_expr

    assert _measure_expr("PL | % of Revenue (Managerial)") == "[PL | % of Revenue (Managerial)]"
    assert _measure_expr("[Sales]") == "[Sales]"
    assert _measure_expr("Odd]Name") == "[Odd]]Name]"


def test_equivalence_without_any_compared_cell_has_no_verdict():
    from core.validation.equivalence_grid import run_equivalence

    def boom(_q):
        return {"success": False, "error": "syntax error"}

    res = run_equivalence(boom, expression_a="[A]", expression_b="[B]", host_table="T")
    assert res["equivalent"] is None and res["cells_compared"] == 0
    assert "Equal results" not in res["note"]


def test_slicer_base_tests_decode_pbir_literals():
    """R0201: generated slicer-base assertions compared against "'Calendar'" (the raw PBIR
    literal), so every one would fail when run."""
    from types import SimpleNamespace

    from core.validation import test_generator as tg

    slicers = [
        SimpleNamespace(
            slicer_id="s1",
            table="s_Switch",
            column="Selection",
            selected_values=["'Calendar'", "'O''Neil'"],
            is_inverted=False,
        ),
        SimpleNamespace(
            slicer_id="s2", table="d_Flag", column="Is Default", selected_values=["true"]
        ),
    ]
    builder = SimpleNamespace(list_slicers=lambda _p: slicers)
    tests, _skipped = tg.slicer_tests(builder, None, {"id": "p1", "name": "P"})
    q1 = tests[0]["query"]
    assert '"Calendar"' in q1 and '"O\'Neil"' in q1
    assert "\"'Calendar'\"" not in q1
    assert tests[0]["base"]["slicer"]["selected"] == ["Calendar", "O'Neil"]
    assert "TRUE()" in tests[1]["query"]


def test_engine_errors_lose_the_dotnet_stack_trace():
    from core.infrastructure.adomd_utils import engine_message

    raw = (
        "Query (1, 37) The syntax for ')' is incorrect. (EVALUATE ROW(\"bad\", 1/0 + )).\r\n"
        "   at Microsoft.AnalysisServices.AdomdClient.XmlaClient.CheckForSoapFault(XmlReader r)\r\n"
        "   at System.Data.Whatever()"
    )
    assert engine_message(raw) == (
        "Query (1, 37) The syntax for ')' is incorrect. (EVALUATE ROW(\"bad\", 1/0 + ))."
    )
    assert engine_message("plain message") == "plain message"
    assert engine_message(None) == ""


# ---------------------------------------------------------------------------
# WP-7 (2026-10-03 live test, small fixes)
# ---------------------------------------------------------------------------

_TRACE = (
    "\r\n   at Microsoft.AnalysisServices.AdomdClient.XmlaClient.CheckForSoapFault(XmlReader r)"
)


def test_wp7_1_error_envelope_strips_stack_frames_centrally():
    from core.validation.error_response import normalize_error_envelope

    res = normalize_error_envelope({"success": False, "error": "Cannot connect." + _TRACE})
    assert res["error"] == "Cannot connect."
    ok = normalize_error_envelope({"success": False, "error": "line 1\nline 2"})
    assert ok["error"] == "line 1\nline 2"


def test_wp7_1_executor_and_rls_helpers_use_engine_message():
    from core.infrastructure.executor_tom import ExecutorTomMixin  # noqa: F401
    from core.validation.rls_audit import _read_counts

    counts, err = _read_counts({"success": False, "error": "bad query" + _TRACE}, ["T"])
    assert counts == {} and err == "bad query"


def test_wp7_1_equivalence_errors_have_no_trace():
    from core.validation.equivalence_grid import run_equivalence

    def boom(_q):
        return {"success": False, "error": "syntax error" + _TRACE}

    res = run_equivalence(boom, expression_a="[A]", expression_b="[B]", host_table="T")
    assert "at Microsoft" not in str(res["errors"])


def test_wp7_2_explain_keeps_udf_and_table_casing():
    from core.dax.analyzer.models import ModelSchema
    from core.dax.explain import explain_expression

    schema = ModelSchema(
        measure_names={"sales"},
        table_names={"d_cf_line", "sales"},
        udf_signatures={"CURRENCYCOREFOR": ["expr", "val"]},
    )
    r = explain_expression(
        "CurrencyCoreFor([Sales], 1) + SUM(d_CF_Line[Amount]) + SUM(Sales[X])", schema=schema
    )
    names = {n["function"] for n in r["call_tree"]}
    assert "CurrencyCoreFor" in names and "CURRENCYCOREFOR" not in names
    assert "SUM" in names  # built-ins stay upper-case
    assert r["dependencies"]["tables"] == ["d_CF_Line", "Sales"]
    assert r["dependencies"]["udf_calls"] == [
        {"function": "CurrencyCoreFor", "parameter_modes": ["expr", "val"]}
    ]


def test_wp7_3_unknown_dmv_probe_names_the_right_op_and_suggests():
    from server.handlers.query_handler import _dispatch_query_operation

    res = _dispatch_query_operation("dmv_probe", {"probe": "vertipaq_column"})
    assert res["error_type"] == "probe_not_found"
    assert "list_dmv_probes" in res["error"] and "list_probes()" not in res["error"]
    assert "vertipaq_columns" in res["did_you_mean"]


def _lint_fake_analyzer(monkeypatch):
    from types import SimpleNamespace

    import core.dax.analyzer.unified_analyzer as ua

    def issue(rule, sev, title):
        return SimpleNamespace(
            rule_id=rule, severity=sev, category="c", title=title, description="d"
        )

    class Fake:
        def analyze(self, _dax, _ctx):
            return SimpleNamespace(
                issues=[
                    issue("CB001", "high", "dup"),
                    issue("CB001", "high", "dup"),
                    issue("CB001", "high", "dup"),
                    issue("LOW1", "low", "minor"),
                ],
                suppressed=[],
                health_score=50,
            )

        def analyze_batch(self, items, _ctx):
            return [self.analyze(t, None) for _n, t in items]

    monkeypatch.setattr(ua, "get_shared_analyzer", lambda: Fake())


def test_wp7_4_lint_model_collapses_identical_findings_and_filters_by_rule(monkeypatch):
    from unittest.mock import MagicMock

    import server.handlers.dax_context_handler as dh

    cs = MagicMock()
    cs.is_connected.return_value = True
    cs.query_executor.enumerate_measures_tom.return_value = {
        "success": True,
        "rows": [{"Name": "M", "Table": "F", "Expression": "1"}],
    }
    monkeypatch.setattr(dh, "connection_state", cs)
    _lint_fake_analyzer(monkeypatch)

    res = dh.handle_dax_intelligence({"operation": "lint_model", "min_severity": "high"})
    dups = [f for f in res["findings"] if f["rule_id"] == "CB001" and f["measure"] == "M"]
    assert len(dups) == 1 and dups[0]["occurrences"] == 3
    assert res["total_findings"] == 1
    assert res["by_rule"] == {"CB001": 1}
    assert res["by_rule_all_severities"] == {"CB001": 1, "LOW1": 1}


def test_wp7_5_tom_datetime_values_are_iso():
    import datetime as dt

    from core.operations.tom_properties import _display

    class ClrDateTime:
        """pythonnet System.DateTime stand-in: component attributes, culture-formatted str()."""

        Year, Month, Day, Hour, Minute, Second, Millisecond = 2026, 9, 21, 14, 12, 12, 0

        def GetType(self):
            return type("T", (), {"FullName": "System.DateTime"})()

        def __str__(self):
            return "21/09/2026 14:12:12"

    assert _display(ClrDateTime()) == "2026-09-21T14:12:12"
    assert _display(dt.datetime(2026, 9, 21, 14, 12, 12)) == "2026-09-21T14:12:12"
    assert _display("x") == "x" and _display(None) is None


def test_wp7_6_concise_full_analysis_drops_boilerplate_and_caps_nested_lists():
    from core.validation.response_format import apply_response_format, concise_analysis_full

    def issue(i):
        return {
            "rule_id": f"R{i}",
            "business_impact": {"urgency": "HIGH"},
            "why_it_matters": "x" * 200,
            "consequences": "y" * 200,
        }

    result = {
        "success": True,
        "issues": [issue(i) for i in range(30)],
        "analyses": {
            "integrity": {"issues": [issue(i) for i in range(12)]},
            "topology": {"findings": [issue(i) for i in range(3)]},
            "bpa": {"violations": [issue(i) for i in range(40)]},
            "m_practices": {"issues": [issue(i) for i in range(11)]},
            "relationships": {"relationships": [{"n": i} for i in range(50)]},
        },
        "audit": {"checks": [{"id": "C1", "examples": [{"o": i} for i in range(20)]}]},
    }
    out = apply_response_format(concise_analysis_full(result), "concise")
    an = out["analyses"]
    assert len(an["integrity"]["issues"]) == 10
    assert an["integrity"]["issues_truncated"] == {"shown": 10, "total": 12}
    assert len(an["bpa"]["violations"]) == 10 and an["bpa"]["violations_truncated"]["total"] == 40
    assert len(an["m_practices"]["issues"]) == 10
    assert len(an["relationships"]["relationships"]) == 10
    assert len(an["topology"]["findings"]) == 3 and "findings_truncated" not in an["topology"]
    assert len(out["audit"]["checks"][0]["examples"]) == 10
    assert out["audit"]["checks"][0]["examples_truncated"]["total"] == 20
    flat = str(out)
    assert "business_impact" not in flat and "why_it_matters" not in flat
    assert "consequences" not in flat
    # detailed mode (no call) and the input stay untouched
    assert len(result["analyses"]["bpa"]["violations"]) == 40
    assert "business_impact" in result["issues"][0]


def test_wp7_6_dispatcher_applies_the_concise_analysis_shape():
    from server.dispatch import ToolDispatcher

    d = ToolDispatcher.__new__(ToolDispatcher)
    row = {"rule_id": "R", "why_it_matters": "z"}
    result = {"success": True, "analyses": {"bpa": {"violations": [dict(row)] * 30}}}
    concise = d._paginate_and_format(
        dict(result), {"operation": "full", "response_format": "concise"}, "06_Analysis_Operations"
    )
    assert len(concise["analyses"]["bpa"]["violations"]) == 10
    assert "why_it_matters" not in str(concise)
    detailed = d._paginate_and_format(
        dict(result), {"operation": "full", "response_format": "detailed"}, "06_Analysis_Operations"
    )
    assert len(detailed["analyses"]["bpa"]["violations"]) == 30
    assert "why_it_matters" in str(detailed)


def test_wp7_8_unknown_args_carry_did_you_mean():
    from server.dispatch import ToolDispatcher, _did_you_mean_args

    declared = frozenset({"operation", "candidate_expression", "measure_name", "page_size"})
    assert _did_you_mean_args(["candidate", "zzzzzz"], declared) == {
        "candidate": "candidate_expression"
    }
    assert _did_you_mean_args(["measure_nam"], declared) == {"measure_nam": "measure_name"}

    d = ToolDispatcher.__new__(ToolDispatcher)
    d.registry = type("R", (), {"declared_properties": lambda self, _t: declared})()
    _args, notices = d._normalize_arguments(
        "09_Validate", {"operation": "equivalence", "candidate": "[X]"}
    )
    assert notices["unknown_args"] == ["candidate"]
    assert notices["did_you_mean_args"] == {"candidate": "candidate_expression"}
    assert "did_you_mean" not in notices


def test_wp7_7_tieout_skips_text_measures():
    from core.validation import tieout as to

    rows = [
        {
            "[IsGrandTotalRowTotal]": True,
            "Geo[Country]": None,
            "[Sales]": 30,
            "[Icon]": "data:image/svg+xml;utf8,<svg/>",
        },
        {
            "[IsGrandTotalRowTotal]": False,
            "Geo[Country]": "BE",
            "[Sales]": 20,
            "[Icon]": "data:image/svg+xml;utf8,<svg a/>",
        },
        {"[IsGrandTotalRowTotal]": False, "Geo[Country]": "NL", "[Sales]": 10, "[Icon]": None},
    ]
    res = to.check_hierarchy(
        rows,
        levels=[["'Geo'[Country]"]],
        flags=["IsGrandTotalRowTotal"],
        measures={"Sales": "Sales", "Icon": "Icon"},
    )
    assert res["measures_checked"] == ["Sales"]
    assert res["skipped_non_numeric"] == ["Icon"]
    assert "Icon" not in res["per_measure"] and res["ties"] is True


def test_wp7_9_format_dax_dry_run_is_a_read_and_carries_no_gate(monkeypatch):
    import server.handlers.tmdl_handler as th
    from server.op_classification import READ, classify_known

    assert (
        classify_known("02_TMDL_Operations", {"operation": "format_dax", "dry_run": True}) == READ
    )
    assert classify_known("02_TMDL_Operations", {"operation": "format_dax"}) != READ

    gated = []

    def fake_gate(op, _args, _severity):
        gated.append(op)
        return {"gate_notice": "LIGHT advisory"}

    monkeypatch.setattr(th, "require_autonomous_mode", fake_gate)
    monkeypatch.setattr(th, "_dispatch_tmdl", lambda _a: {"success": True})

    preview = th.handle_tmdl_operations({"operation": "format_dax", "dry_run": True})
    assert gated == [] and "gate_notice" not in preview
    th.handle_tmdl_operations({"operation": "format_dax", "dry_run": False})
    assert gated == ["02_TMDL_Operations:format_dax"]


def test_wp7_10_live_swap_refuses_a_self_referencing_candidate():
    from server.handlers._debug.live_swap import circular_patch_error, handle_live_swap

    own = {
        "table_name": "M",
        "measure_name": "PL | TC0 Reported",
        "expression": "[pl | tc0 reported] + 0",
    }
    err = circular_patch_error([own])
    assert err["error_type"] == "circular_candidate" and err["success"] is False

    qualified = {**own, "expression": "CALCULATE('M'[PL | TC0 Reported], ALL('D'))"}
    assert circular_patch_error([qualified])["error_type"] == "circular_candidate"

    # both dry_run and a real run are refused before anything touches the model
    for dry in (True, False):
        res = handle_live_swap(
            {
                "operation": "compare",
                "mode": "live_swap",
                "original_measure": own["measure_name"],
                "table_name": "M",
                "optimized_expression": own["expression"],
                "reference_query": 'EVALUATE ROW("x", 1)',
                "dry_run": dry,
            }
        )
        assert res["error_type"] == "circular_candidate"

    fine = {**own, "expression": '[Other] + 0 + "[PL | TC0 Reported]" // [PL | TC0 Reported]'}
    assert circular_patch_error([fine]) is None
    # a column of another table that merely shares the name is not a self reference
    other_table = {**own, "expression": "SUM('Other'[PL | TC0 Reported])"}
    assert circular_patch_error([other_table]) is None


def test_wp7_11_measure_resource_dependencies_are_dax_references(monkeypatch):
    from core.operations import model_readback
    from server import live_resources as live

    def row(otype, ref_type, ref_table, ref_obj):
        return {
            "OBJECT_TYPE": otype,
            "OBJECT": "PL",
            "TABLE": "M",
            "REFERENCED_OBJECT_TYPE": ref_type,
            "REFERENCED_TABLE": ref_table,
            "REFERENCED_OBJECT": ref_obj,
        }

    rows = [
        row("MEASURE", "TABLE", "d_cf_line", "d_cf_line"),  # used to print d_cf_line[d_cf_line]
        row("MEASURE", "COLUMN", "Sales", "Amount"),
        row("MEASURE", "MEASURE", "M", "Base"),
        row("MEASURE", "FUNCTION", "", "CurrencyCoreFor"),  # used to print [CurrencyCoreFor]
        {
            "OBJECT_TYPE": "MEASURE",
            "OBJECT": "Child",
            "TABLE": "M",
            "REFERENCED_OBJECT_TYPE": "MEASURE",
            "REFERENCED_TABLE": "M",
            "REFERENCED_OBJECT": "PL",
        },
    ]
    monkeypatch.setattr(model_readback, "dmv_rows", lambda dmv, info=None: rows)
    deps = live._measure_dependencies("M", "PL")
    assert deps["references"] == [
        "'d_cf_line'",
        "'Sales'[Amount]",
        "[Base]",
        "CurrencyCoreFor()",
    ]
    assert deps["referenced_by"] == ["[Child]"]


def test_wp7_12_find_unused_tags_columns_that_cannot_be_deleted():
    from core.graph.cleanup import manual_reason
    from core.graph.models import Edge, Graph, Node
    from core.graph.queries import find_unused

    g = Graph()
    for name, attrs in (
        ("Plain", {}),
        ("Switch", {"calc_group": True}),
        ("Params", {"field_parameter": True}),
        ("Calc", {"calculated": True}),
    ):
        g.add_node(Node(kind="table", scoped_name=name, attrs=attrs))
    cols = {
        "Plain[Old]": "data",
        "Switch[Name]": "data",
        "Params[Fields]": "data",
        "Calc[Amount]": "calculated_table_column",
    }
    for scoped, ctype in cols.items():
        g.add_node(Node(kind="column", scoped_name=scoped, attrs={"type": ctype}))
        g.add_edge(
            Edge(src=f"column:{scoped}", dst=f"table:{scoped.split('[')[0]}", kind="belongs_to")
        )

    for transitive in (False, True):
        out = {e["node"]: e for e in find_unused(g, ["column"], transitive=transitive)["unused"]}
        assert set(out) == {f"column:{c}" for c in cols}
        assert "removable" not in out["column:Plain[Old]"]
        for nid in ("column:Switch[Name]", "column:Params[Fields]", "column:Calc[Amount]"):
            assert out[nid]["removable"] is False
            assert out[nid]["not_removable_reason"] == manual_reason(g, nid)
    assert "calculation-group table" in out["column:Switch[Name]"]["not_removable_reason"]


# --- WP-7 edge cases (what must NOT change, beside what must) ---


def test_wp7_3_run_probe_raises_with_suggestions_or_an_empty_list():
    import pytest

    from core.dax.dmv_probes import PROBE_REGISTRY, ProbeNotFoundError, run_probe

    with pytest.raises(ProbeNotFoundError) as exc:
        run_probe("vertipaq_dictionry")
    assert "vertipaq_dictionary" in exc.value.did_you_mean
    assert set(exc.value.did_you_mean) <= set(PROBE_REGISTRY)
    with pytest.raises(ProbeNotFoundError) as exc:
        run_probe("zzzzzzzzzzzz")
    assert exc.value.did_you_mean == []

    from server.handlers.query_handler import _dispatch_query_operation

    res = _dispatch_query_operation("dmv_probe", {"probe": "zzzzzzzzzzzz"})
    assert res["error_type"] == "probe_not_found" and "did_you_mean" not in res


def test_wp7_4_lint_model_keeps_distinct_objects_and_titles_apart(monkeypatch):
    from unittest.mock import MagicMock

    import server.handlers.dax_context_handler as dh

    cs = MagicMock()
    cs.is_connected.return_value = True
    cs.query_executor.enumerate_measures_tom.return_value = {
        "success": True,
        "rows": [
            {"Name": "M", "Table": "F", "Expression": "1"},
            {"Name": "N", "Table": "F", "Expression": "1"},
        ],
    }
    monkeypatch.setattr(dh, "connection_state", cs)
    _lint_fake_analyzer(monkeypatch)

    res = dh.handle_dax_intelligence({"operation": "lint_model"})
    dups = [f for f in res["findings"] if f["rule_id"] == "CB001"]
    assert sorted(f["measure"] for f in dups) == ["M", "N"]  # not merged across objects
    assert all(f["occurrences"] == 3 for f in dups)
    assert res["by_rule"] == {"CB001": 2, "LOW1": 2}
    assert "by_rule_all_severities" not in res  # no min_severity filter: it would repeat by_rule


def test_wp7_5_tom_display_date_and_enum_like_object():
    import datetime as dt

    from core.operations.tom_properties import _display

    assert _display(dt.date(2026, 9, 21)) == "2026-09-21"

    class Mode:
        Name = "Import"

        def GetType(self):
            return type("T", (), {"FullName": "Microsoft.AnalysisServices.Tabular.ModeType"})()

    assert _display(Mode()) == "Import"  # not routed through the date conversion


def test_wp7_6_concise_analysis_passes_non_analysis_results_through():
    from core.validation.response_format import concise_analysis_full
    from server.dispatch import ToolDispatcher

    plain = {"success": True, "issues": [{"why_it_matters": "x"}] * 30}
    assert concise_analysis_full(plain) is plain
    assert concise_analysis_full({"analyses": "nope"}) == {"analyses": "nope"}
    assert concise_analysis_full(None) is None

    d = ToolDispatcher.__new__(ToolDispatcher)
    rows = [{"rule_id": f"R{i}", "why_it_matters": "z"} for i in range(12)]  # 10 < 12 < 25
    result = {"success": True, "analyses": {"bpa": {"violations": rows}}}
    args = {"operation": "x", "response_format": "concise"}
    other = d._paginate_and_format(dict(result), args, "05_DAX_Intelligence")
    assert len(other["analyses"]["bpa"]["violations"]) == 12
    failed = d._paginate_and_format(
        {**result, "success": False}, {**args, "operation": "full"}, "06_Analysis_Operations"
    )
    assert len(failed["analyses"]["bpa"]["violations"]) == 12


def test_wp7_7_tieout_measure_with_no_values_stays_checked():
    from core.validation import tieout as to

    rows = [
        {"[IsGrandTotalRowTotal]": True, "Geo[Country]": None, "[Sales]": 30, "[Empty]": None},
        {"[IsGrandTotalRowTotal]": False, "Geo[Country]": "BE", "[Sales]": 30, "[Empty]": None},
    ]
    res = to.check_hierarchy(
        rows,
        levels=[["'Geo'[Country]"]],
        flags=["IsGrandTotalRowTotal"],
        measures={"Sales": "Sales", "Empty": "Empty"},
    )
    assert res["measures_checked"] == ["Sales", "Empty"]
    assert res["skipped_non_numeric"] == []


def test_wp7_9_format_dax_gate_edges(monkeypatch):
    import server.handlers.tmdl_handler as th

    gated = []

    def fake_gate(op, _args, _severity):
        gated.append(op)
        return {"gate_notice": "LIGHT advisory"}

    monkeypatch.setattr(th, "require_autonomous_mode", fake_gate)
    monkeypatch.setattr(th, "_dispatch_tmdl", lambda _a: {"success": True})

    assert "gate_notice" in th.handle_tmdl_operations({"operation": "format_dax"})
    th.handle_tmdl_operations({"operation": "bulk_rename", "dry_run": True})  # only format_dax
    assert gated == ["02_TMDL_Operations:format_dax", "02_TMDL_Operations:bulk_rename"]


def test_wp7_10_circular_candidate_edges():
    from server.handlers._debug.live_swap import circular_patch_error

    def patch(expr, table="T", name="M"):
        return {"table_name": table, "measure_name": name, "expression": expr}

    assert circular_patch_error([patch("[M] + 0")])["patched"] == ["M"]
    assert circular_patch_error([patch("T[M] + 0")])["error_type"] == "circular_candidate"
    assert circular_patch_error([patch("SUM(Other[M])")]) is None  # other table
    assert circular_patch_error([patch("SUM(Other[M])", table=None)]) is not None  # unknown table
    assert circular_patch_error([patch("1 /* [M] */ + 2")]) is None
    assert circular_patch_error([patch('"[M]" & "x"')]) is None
    assert circular_patch_error([patch("[Other] + [MM]")]) is None
    bad = circular_patch_error([patch("[A]+1", name="A"), patch("[X]", name="B")])
    assert bad["patched"] == ["A"]


# --- WP-7 tests end ---


def test_hidden_only_unused_column_rules_do_not_fail_the_visible_column_check():
    """R0201: MS7 (hidden) and MS8 (visible) both reported the same hidden column."""
    from core.analysis import audit_taxonomy as at

    tax = at.load_taxonomy()
    rep = at.build_report(
        tax,
        violations=[
            {"rule_id": "REMOVE_UNUSED_COLUMNS", "object_name": "Key", "table_name": "d_Org"}
        ],
        topology_findings=[],
        evaluated_rule_ids=["REMOVE_UNUSED_COLUMNS", "UNNECESSARY_COLUMNS"],
        topology_ok=True,
    )
    checks = {c["id"]: c for c in rep["checks"]}
    assert checks["MS7"]["verdict"] == "fail"
    assert checks["MS8"]["verdict"] == "manual"
    assert "get_unused_columns_pbip" in checks["MS8"]["hand_off"]


def test_equivalence_names_grid_groups_that_returned_no_cell():
    """R0201: a grid on d_Scenario[Version] returned no row (the measure is BLANK per version), and
    the pass on the grand total alone read like a grid pass."""
    from core.validation.equivalence_grid import run_equivalence

    def run(dax):
        if "SUMMARIZECOLUMNS" in dax:
            return {"success": True, "rows": []}
        return {"success": True, "rows": [{"[a]": 5.0, "[b]": 5.0}]}

    res = run_equivalence(
        run, expression_a="[A]", expression_b="[B]", host_table="T", grid_columns=["D[V]"]
    )
    assert res["equivalent"] is True
    assert res["empty_groups"] == ["col:D[V]"]
    assert "returned no non-blank cell" in res["note"]


def test_explain_types_a_udf_variable_by_its_use_and_keeps_udf_casing():
    """R0201 'LBL | PCD Span MTD': VAR vW = TIWindow ( vSel ) feeds MINX ( vW, ... ) - it was typed
    'scalar' and its use was labelled 'TIWINDOW arg 1'."""
    from core.dax.explain import explain_expression

    dax = (
        'VAR vSel = "YTD"\nVAR vW = TIWindow ( vSel )\n'
        "VAR vLo = MINX ( vW, d_Period[Seq] )\nVAR vN = 3\nRETURN vLo + vN"
    )
    var = {v["name"]: v for v in explain_expression(dax)["variables"]}
    assert var["vW"]["type"] == "table"
    assert var["vSel"]["type"] == "scalar" and var["vN"]["type"] == "scalar"
    assert var["vSel"]["used_in"][0]["in"] == ["TIWindow arg 1"]


def test_tieout_parent_without_child_rows_is_not_a_break():
    """R0201 P&L Statement: formula lines (EBIT, margins) have no Level.2 children; 60 of the 72
    reported breaks were such rows (children_sum 0, children 0)."""
    from core.validation import tieout as to

    gt, sub = "[IsGrandTotalRowTotal]", "[IsDM1Total]"
    rows = [
        {gt: True, sub: True, "L[Line]": None, "L[Sub]": None, "[V]": 15},
        {gt: False, sub: True, "L[Line]": "Sales", "L[Sub]": None, "[V]": 10},
        {gt: False, sub: False, "L[Line]": "Sales", "L[Sub]": "A", "[V]": 6},
        {gt: False, sub: False, "L[Line]": "Sales", "L[Sub]": "B", "[V]": 4},
        {gt: False, sub: True, "L[Line]": "EBIT", "L[Sub]": None, "[V]": 5},
    ]
    res = to.check_hierarchy(
        rows,
        levels=[["'L'[Line]"], ["'L'[Sub]"]],
        flags=["IsGrandTotalRowTotal", "IsDM1Total"],
        measures={"V": "V"},
    )
    assert res["ties"] is True, res["breaks"]
    pm = res["per_measure"]["V"]
    assert pm["parents_without_children"] == 1 and pm["breaks"] == 0


def test_tieout_says_when_every_break_is_a_ratio_measure():
    from core.validation import tieout as to

    gt = "[IsGrandTotalRowTotal]"
    rows = [
        {gt: True, "G[C]": None, "[Amt]": 30, "[Pct]": 1.0},
        {gt: False, "G[C]": "BE", "[Amt]": 20, "[Pct]": 1.0},
        {gt: False, "G[C]": "NL", "[Amt]": 10, "[Pct]": 1.0},
    ]
    res = to.check_hierarchy(
        rows,
        levels=[["'G'[C]"]],
        flags=["IsGrandTotalRowTotal"],
        measures={"Amt": "Amt", "Pct": "Pct"},
        expressions={"Pct": "DIVIDE ( [Amt], [Base] )"},
    )
    assert res["ties"] is False
    assert res["breaks_only_in_non_additive_by_expression"] is True
    assert res["non_additive_by_expression"] == ["Pct"]


# ---------------------------------------------------------------------------
# Fresh-context review fixes (2026-10-03)
# ---------------------------------------------------------------------------


@pytest.fixture()
def gproj(tmp_path):
    dst = tmp_path / "GoldenCo"
    shutil.copytree(GOLDEN, dst)
    return dst


def _baseline_commit(proj: Path) -> None:
    _git(proj, "init", "-q")
    _git(proj, "add", "-A")
    _git(proj, "commit", "-q", "-m", "baseline")


_NEEDS_GIT = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


@_NEEDS_GIT
def test_review1_deleting_a_bound_table_fails_the_commit_gate(gproj):
    """Deleting a table slicers still bind must not pass as 'pre-existing' (the findings sit in
    visual files the change did not touch)."""
    _baseline_commit(gproj)
    _git(gproj, "rm", "-q", "MDL-Core.SemanticModel/definition/tables/Product Line.tmdl")
    res = _run(gproj, level="commit")
    hits = [f for f in res["findings"] if f["code"] == "visual_binds_missing_field"]
    assert hits and any(f["in_scope_reason"] == "caused_by_change" for f in hits)
    assert res["commit_ok"] is False and res["counts"]["error_in_scope"] >= 1


@_NEEDS_GIT
def test_review1_unrelated_change_keeps_old_binding_debt_out_of_scope(gproj):
    card = _defn(gproj) / "pages" / "aa01overview00000001" / "visuals" / "vc01cardvisual000001"
    text = (card / "visual.json").read_text(encoding="utf-8")
    (card / "visual.json").write_text(
        text.replace('"Entity": "Sales"', '"Entity": "No Such Table"'), encoding="utf-8"
    )
    _baseline_commit(gproj)
    _edit(
        _defn(gproj) / "pages" / "ab02details000000002" / "page.json",
        lambda d: d.update(displayName="Details v2"),
    )
    _git(gproj, "add", "-A")
    res = _run(gproj, level="commit")
    hits = [f for f in res["findings"] if f["code"] == "visual_binds_missing_field"]
    assert hits and all(f["in_scope_reason"] is None for f in hits)
    assert res["commit_ok"] is True and res["counts"]["preexisting_error"] >= 1
    # item 4: pre-existing debt must not read as "clean"
    assert res["verdict"].startswith("clean for this change")
    assert "pre-existing error" in res["verdict"]


@_NEEDS_GIT
def test_review1_deleting_a_visual_bookmarks_still_target_fails_the_gate(gproj):
    # item 2 as well: 250 earlier-sorting files push the deleted visual past the 200-name echo cap
    bm = _defn(gproj) / "bookmarks" / "bm0000000000000000a1.bookmark.json"
    _edit(bm, lambda d: d["options"]["targetVisualNames"].append("vt01tableinverted001"))
    _baseline_commit(gproj)
    junk = gproj / "A_junk"
    junk.mkdir()
    for i in range(250):
        (junk / f"f{i:03d}.txt").write_text("x", encoding="utf-8")
    victim = _defn(gproj) / "pages" / "ab02details000000002" / "visuals" / "vt01tableinverted001"
    shutil.rmtree(victim)
    _git(gproj, "add", "-A")
    res = _run(gproj, level="commit")
    assert len(res["git"]["files"]) == 200 and res["git"]["files_total"] >= 251
    assert "_changed" not in res["git"]
    hit = [f for f in res["findings"] if f["code"] == "bookmark_target_visual_missing"]
    assert hit and hit[0]["in_scope_reason"] == "caused_by_change"
    assert res["commit_ok"] is False


@_NEEDS_GIT
def test_review1_git_scope_lists_deleted_and_non_ascii_paths(gproj):
    from core.pbip.preflight import _Findings, _git_scope

    odd = gproj / "Résumé.txt"
    odd.write_text("x", encoding="utf-8")
    _baseline_commit(gproj)
    odd.unlink()
    _git(gproj, "add", "-A")
    info = _git_scope(_Findings(gproj), gproj, None, False)
    assert "Résumé.txt" in info["_changed"]


def test_review5_by_code_reports_the_most_severe_and_the_split(gproj):
    card = _defn(gproj) / "pages" / "aa01overview00000001" / "visuals" / "vc01cardvisual000001"
    text = (card / "visual.json").read_text(encoding="utf-8")
    (card / "visual.json").write_text(
        text.replace('"Entity": "Sales"', '"Entity": "No Such Table"'), encoding="utf-8"
    )
    page = _defn(gproj) / "pages" / "aa01overview00000001" / "page.json"
    _edit(
        page,
        lambda d: d.setdefault("filterConfig", {"filters": []})["filters"].append(
            {
                "name": "f9",
                "field": {
                    "Column": {
                        "Expression": {"SourceRef": {"Entity": "Ghost"}},
                        "Property": "X",
                    }
                },
                "type": "Categorical",
            }
        ),
    )
    grp = _run(gproj)["by_code"]["visual_binds_missing_field"]
    assert grp["severity"] == "error"
    assert sum(grp["by_severity"].values()) == grp["count"]
    assert grp["by_severity"].get("error", 0) >= 1 and grp["by_severity"].get("warning", 0) >= 1


def test_review3_nothing_compared_is_inconclusive_not_a_tie():
    from core.validation import tieout as to

    gt = "[IsGrandTotalRowTotal]"
    rows = [
        {gt: True, "G[C]": None, "[Sales]": 30, "[Icon]": "data:image/svg+xml;utf8,<svg/>"},
        {gt: False, "G[C]": "BE", "[Sales]": 20, "[Icon]": "data:image/svg+xml;utf8,<svg a/>"},
    ]
    base = {"levels": [["'G'[C]"]], "flags": ["IsGrandTotalRowTotal"]}
    # every measure is text
    text_only = to.check_hierarchy(rows, measures={"Icon": "Icon"}, **base)
    assert text_only["ties"] is None and "no additive measure" in text_only["inconclusive"]
    # every measure declared non-additive
    declared = to.check_hierarchy(rows, measures={"Sales": "Sales"}, non_additive=["Sales"], **base)
    assert declared["ties"] is None and "1 declared non-additive" in declared["inconclusive"]
    # every parent childless (a grand total with no child row)
    childless = to.check_hierarchy([rows[0]], measures={"Sales": "Sales"}, **base)
    assert childless["ties"] is None
    ok, msgs = to.tieout_passes({"success": True, **text_only})
    assert ok is False and "no additive measure" in msgs[0]
    # a real comparison still ties
    assert to.check_hierarchy(rows, measures={"Sales": "Sales"}, **base)["ties"] is False


def test_review3_row_cap_keeps_a_specific_inconclusive_reason():
    from core.validation import tieout as to
    from tests.test_validate_tieout import _FakeBuilder, _matrix

    rows = _matrix()
    res = to.run_visual_tieout(
        _FakeBuilder(),
        lambda q: {"success": True, "rows": rows},
        page_name="P",
        max_rows=len(rows),
        non_additive=["Sales"],
    )
    assert res["truncated"] is True and res["ties"] is None
    assert "no additive measure" in res["inconclusive"] and "row cap" in res["inconclusive"]


def test_review3_page_vs_page_errors_do_not_turn_inconclusive_into_a_failure():
    from core.validation import tieout as to
    from tests.test_validate_tieout import _PageBuilder, _page_runner

    b = _PageBuilder(
        {"A": [("a1", "T", 1.0), ("a2", "O", 2.0)], "B": [("b1", "X", 1.0), ("b2", "Y", 1.0)]}
    )
    inner = _page_runner(b)

    def run(q):
        return {"success": False, "error": "boom"} if "A/a2" in q else inner(q)

    res = to.run_page_vs_page(b, run, page_a="A", page_b="B", measure="Sales")
    assert res["ties"] is None and "none share a title" in res["inconclusive"]
    assert res["errors"]


def test_review6_a_tables_own_expressions_do_not_silence_its_disconnected_finding():
    model = {
        "tables": [
            {
                "name": "Orphan",
                "columns": [{"name": "A"}, {"name": "Calc", "expression": "Orphan[A] * 2"}],
                "measures": [{"name": "M", "expression": "SUM ( 'Orphan'[A] )"}],
                "partitions": [{"source": {"type": "calculated", "expression": 'ROW("x", 1)'}}],
            },
            {"name": "Read", "columns": [{"name": "B"}]},
            {"name": "Host", "measures": [{"name": "H", "expression": "MAX ( Read[B] )"}]},
        ],
        "roles": [],
    }
    assert dax_referenced_tables(model) == ["Read"]
    # a UDF body has no host table, so it still counts
    model["functions"] = [{"name": "F", "expression": "SUM ( Orphan[A] )"}]
    assert dax_referenced_tables(model) == ["Orphan", "Read"]


def test_review7_lint_model_keeps_distinct_descriptions_and_counts_occurrences(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    import core.dax.analyzer.unified_analyzer as ua
    import server.handlers.dax_context_handler as dh

    def issue(desc):
        return SimpleNamespace(
            rule_id="CB001", severity="high", category="c", title="same title", description=desc
        )

    class Fake:
        def analyze(self, _dax, _ctx):
            return SimpleNamespace(
                issues=[issue("col A"), issue("col B"), issue("col A")],
                suppressed=[],
                health_score=50,
            )

        def analyze_batch(self, items, _ctx):
            return [self.analyze(t, None) for _n, t in items]

    monkeypatch.setattr(ua, "get_shared_analyzer", lambda: Fake())
    cs = MagicMock()
    cs.is_connected.return_value = True
    cs.query_executor.enumerate_measures_tom.return_value = {
        "success": True,
        "rows": [{"Name": "M", "Table": "F", "Expression": "1"}],
    }
    monkeypatch.setattr(dh, "connection_state", cs)

    res = dh.handle_dax_intelligence({"operation": "lint_model"})
    by_desc = {f["description"]: f["occurrences"] for f in res["findings"]}
    assert by_desc == {"col A": 2, "col B": 1}
    assert res["total_findings"] == 2 and res["total_occurrences"] == 3
    assert res["by_rule"] == {"CB001": 2}


def test_review10_orchestrator_compatibility_level_needs_an_unambiguous_catalog():
    from core.orchestration.analysis_orchestrator import AnalysisOrchestrator

    class Exec:
        def __init__(self, rows):
            self.rows = rows

        def execute_dmv_query(self, _q):
            return {"success": True, "rows": self.rows}

    one = Exec([{"CATALOG_NAME": "M", "COMPATIBILITY_LEVEL": 1702}])
    assert AnalysisOrchestrator._compatibility_level(one) == 1702
    two = Exec([{"COMPATIBILITY_LEVEL": 1600}, {"COMPATIBILITY_LEVEL": 1702}])
    assert AnalysisOrchestrator._compatibility_level(two) == 0  # first row is not "ours"


def test_review11_explain_types_table_vars_and_leaves_tocsv_scalar():
    from core.dax.explain import explain_expression

    dax = (
        "VAR vCal = CALENDAR ( DATE ( 2020, 1, 1 ), DATE ( 2020, 12, 31 ) )\n"
        "VAR vSeries = GENERATESERIES ( 1, 10 )\n"
        "VAR vMtd = DATESMTD ( 'Date'[Date] )\n"
        "VAR vCsv = TOCSV ( 'Sales' )\n"
        "RETURN COUNTROWS ( vCal ) + COUNTROWS ( vSeries ) + COUNTROWS ( vMtd ) + LEN ( vCsv )"
    )
    var = {v["name"]: v["type"] for v in explain_expression(dax)["variables"]}
    assert var == {"vCal": "table", "vSeries": "table", "vMtd": "table", "vCsv": "scalar"}


def test_review12_live_swap_detects_a_cycle_across_patches():
    from server.handlers._debug.live_swap import circular_patch_error

    a = {"table_name": "T", "measure_name": "A", "expression": "[B] + 1"}
    b = {"table_name": "T", "measure_name": "B", "expression": "T[A] * 2"}
    err = circular_patch_error([a, b])
    assert err["error_type"] == "circular_candidate" and err["patched"] == ["A", "B"]
    assert "each other" in err["error"]
    # a chain without a way back is fine; a longer cycle is caught too
    assert circular_patch_error([a, {**b, "expression": "1"}]) is None
    c = {"table_name": "T", "measure_name": "C", "expression": "[A]"}
    assert circular_patch_error([{**a, "expression": "[B]"}, {**b, "expression": "[C]"}, c])
    # the other table's A is a different object
    assert circular_patch_error([a, {**b, "expression": "Other[A]"}]) is None


def test_review12_live_swap_rechecks_after_the_table_is_resolved(monkeypatch):
    import server.handlers._debug.live_swap as ls

    patch = {"measure_name": "M", "expression": "SUM ( Other[M] )"}  # table_name unknown
    # unknown table: the early pass defers a qualified reference (could be a column)
    assert ls.circular_patch_error([dict(patch)], defer_unknown_table=True) is None
    assert ls.circular_patch_error([dict(patch)]) is not None  # default stays conservative

    monkeypatch.setattr(ls.connection_state, "is_connected", lambda: True)
    stop = {"success": False, "error_type": "stop_here"}
    monkeypatch.setattr(ls, "resolve_query", lambda _a: (None, "", stop))

    def resolve_to(table):
        def fake(patches):
            for p in patches:
                p["table_name"] = table

        return fake

    args = {"original_measure": "M", "optimized_expression": patch["expression"]}
    # the measure lives in 'Facts': Other[M] is another table's column -> goes on to the query
    monkeypatch.setattr(ls, "_find_tables", resolve_to("Facts"))
    assert ls.handle_live_swap(dict(args))["error_type"] == "stop_here"
    # the measure lives in 'Other': the very same text now IS a self reference
    monkeypatch.setattr(ls, "_find_tables", resolve_to("Other"))
    assert ls.handle_live_swap(dict(args))["error_type"] == "circular_candidate"


def test_review13_slicer_base_tests_render_dates_and_null_as_dax_not_text():
    from types import SimpleNamespace

    from core.validation import test_generator as tg

    def one(values):
        s = SimpleNamespace(slicer_id="s", table="d_Date", column="Date", selected_values=values)
        builder = SimpleNamespace(list_slicers=lambda _p: [s])
        tests, _skipped = tg.slicer_tests(builder, None, {"id": "p1", "name": "P"})
        return tests

    day = one(["datetime'2024-01-31T00:00:00'", "datetime'2024-02-29T14:30:05'"])
    q = day[0]["query"]
    assert "DATE(2024, 1, 31)" in q and "(DATE(2024, 2, 29) + TIME(14, 30, 5))" in q
    assert '"2024' not in q  # never quoted ISO text
    assert day[0]["base"]["slicer"]["selected"] == ["2024-01-31T00:00:00", "2024-02-29T14:30:05"]

    mixed = one(["'A'", "null"])
    assert "BLANK()" in mixed[0]["query"] and '"A"' in mixed[0]["query"]
    assert mixed[0]["expect"] == {"scalar": 2}
    assert mixed[0]["base"]["slicer"]["selected"] == ["A", None]
    assert one(["1.5M"])[0]["query"].count("1.5") == 1  # a decimal stays a number, not text
    assert '"1.5"' not in one(["1.5M"])[0]["query"]


def test_review14_validate_report_max_findings_doc_matches_the_concise_default():
    from server.handlers.pbip_operations_handler import _preflight_cap
    from server.tool_schemas import TOOL_SCHEMAS

    desc = TOOL_SCHEMAS["pbip_operations"]["properties"]["max_findings"]["description"]
    assert f"({_preflight_cap({'response_format': 'concise'})} concise" in desc


# --- review fixes end ---


def test_a_finished_validation_with_a_failing_verdict_is_not_an_mcp_error():
    """R0201: validate_report level=commit returned success:true, ok:false (errors in the change)
    and the MCP layer flagged the whole call as isError."""
    from server.response_pipeline import is_error_result

    assert is_error_result({"success": True, "ok": False, "verdict": "x"}) is False
    assert is_error_result({"success": False, "error": "boom"}) is True
    assert is_error_result({"ok": False, "error_type": "cli_error"}) is True  # legacy alias
    assert is_error_result({"ok": True}) is False
