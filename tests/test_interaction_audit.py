"""F-12: ``07_Report_Operations audit_interactions`` (core/pbip/interaction_audit.py).

Slicer reach through the model, wrong / missing NoFilter rules, policy isolations and the UI
suite plan. Ported from pbi-perf-workflow/scripts/slicer_interactions.py; the model comes from
the offline TMDL parse of the golden fixture (Product Line -> Sales <- Owner's Book).
Synthetic copies only.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from core.graph.model_source import snapshot_from_model_data
from core.pbip.interaction_audit import ModelView, audit_interactions, build_invariance_plan
from server.handlers.report_operations_handler import handle_report_operations
from server.pbip_cache import pbip_cache

GOLDEN = Path(__file__).parent / "fixtures" / "pbir_golden" / "GoldenCo"
OVERVIEW = "aa01overview00000001"
SLICER = "vs01slicerinverted01"  # Product Line[Category]
SLICER2 = "vs02slicervisinv0002"  # Product Line[Region]
MATRIX = "vm01matrixfilters001"
CARD = "vc01cardvisual000001"


@pytest.fixture()
def proj(tmp_path):
    dst = tmp_path / "GoldenCo"
    shutil.copytree(GOLDEN, dst)
    return dst


def _defn(p: Path) -> Path:
    return p / "RPT-Sales.Report" / "definition"


def _snap(p: Path):
    pbip_cache.clear() if hasattr(pbip_cache, "clear") else None
    data = pbip_cache.get_or_parse(str(p / "RPT-Sales.pbip"))
    return snapshot_from_model_data(data.model_data)


def _write_visual(
    defn: Path, page: str, vid: str, vtype: str, binding, *, role="Values", extra=None
):
    kind, table, name = binding
    field = {kind: {"Expression": {"SourceRef": {"Entity": table}}, "Property": name}}
    doc = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.11.0/schema.json",
        "name": vid,
        "position": {"x": 0, "y": 0, "z": 1, "height": 100, "width": 100, "tabOrder": 1},
        "visual": {
            "visualType": vtype,
            "query": {
                "queryState": {
                    role: {"projections": [{"field": field, "queryRef": f"{table}.{name}"}]}
                }
            },
        },
    }
    doc.update(extra or {})
    d = defn / "pages" / page / "visuals" / vid
    d.mkdir(parents=True)
    (d / "visual.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _set_interactions(defn: Path, page: str, rules):
    pj = defn / "pages" / page / "page.json"
    data = json.loads(pj.read_text(encoding="utf-8"))
    data["visualInteractions"] = rules
    pj.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _slicer(res, sid, page=OVERVIEW):
    pg = next(p for p in res["pages"] if p["page_id"] == page)
    return next(s for s in pg["slicers"] if s["slicer"] == sid)


def _ids(rows, key="visual"):
    return {r[key] if isinstance(r, dict) else r for r in rows}


# -- reach ------------------------------------------------------------------


def test_reach_follows_relationships_one_to_many(proj):
    res = audit_interactions(_defn(proj), _snap(proj))
    s = _slicer(res, SLICER)
    assert s["field"] == "Product Line[Category]" and s["kind"] == "data (dimension)"
    assert s["reach_tables"] == ["Product Line", "Sales"]
    needed = _ids(s["needed"])
    assert {
        CARD,
        MATRIX,
        SLICER2,
    } <= needed  # card reads Sales; matrix Product Line; sibling slicer
    # via-tables are reported
    assert next(n for n in s["needed"] if n["visual"] == CARD)["via"] == ["Sales"]


def test_dimension_that_is_not_downstream_is_no_effect_and_bidirectional_flips_it(proj):
    d = _defn(proj)
    _write_visual(
        d, OVERVIEW, "vx01ownertable00001", "tableEx", ("Column", "Owner's Book", "Owner")
    )
    snap = _snap(proj)
    res = audit_interactions(d, snap)
    s = _slicer(res, SLICER)
    assert "vx01ownertable00001" in _ids(s["no_effect"])
    assert "vx01ownertable00001" not in _ids(s["needed"])
    assert any(m["visual"] == "vx01ownertable00001" for m in s["missing_no_filter"])
    # a bi-directional Sales <-> Owner's Book relationship makes Product Line reach Owner's Book
    for rel in snap.relationships:
        if rel["to_table"] == "Owner's Book":
            rel["cross_filter"] = "bothDirections"
    s2 = _slicer(audit_interactions(d, snap), SLICER)
    assert "vx01ownertable00001" in _ids(s2["needed"])


def test_measure_reach_through_dax_and_udf_bodies(proj):
    d = _defn(proj)
    snap = _snap(proj)
    snap.measures.append(
        {
            "table": "m Metrics",
            "name": "Owner Count",
            "expression": "COUNTROWS('Owner''s Book')",
            "format_string_expr": None,
            "detail_rows_expr": None,
        }
    )
    snap.measures.append(
        {
            "table": "m Metrics",
            "name": "Via Measure",
            "expression": "[Owner Count] + 1",
            "format_string_expr": None,
            "detail_rows_expr": None,
        }
    )
    _write_visual(
        d, OVERVIEW, "vx02ownermeasure001", "card", ("Measure", "m Metrics", "Via Measure")
    )
    s = _slicer(audit_interactions(d, snap), SLICER)
    assert "vx02ownermeasure001" in _ids(s["no_effect"])  # transitive read = Owner's Book only
    # a table passed BY NAME to a UDF is read too (the CALCDEPENDENCY gap)
    snap.functions.append({"name": "CountOf", "expression": "(t: table) => COUNTROWS(t)"})
    snap.measures.append(
        {
            "table": "m Metrics",
            "name": "Sales Rows",
            "expression": "CountOf(Sales)",
            "format_string_expr": None,
            "detail_rows_expr": None,
        }
    )
    _write_visual(d, OVERVIEW, "vx03udfmeasure0001", "card", ("Measure", "m Metrics", "Sales Rows"))
    s = _slicer(audit_interactions(d, snap), SLICER)
    assert "vx03udfmeasure0001" in _ids(s["needed"])


def test_report_extension_measures_are_scanned(proj):
    d = _defn(proj)
    (d / "reportExtensions.json").write_text(
        json.dumps(
            {
                "entities": [
                    {
                        "name": "Ext",
                        "measures": [
                            {"name": "Owners", "expression": "COUNTROWS('Owner''s Book')"},
                            {"name": "Sold", "expression": "[Total Amount]"},
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    _write_visual(d, OVERVIEW, "vx04ext1000000000001", "card", ("Measure", "Ext", "Owners"))
    _write_visual(d, OVERVIEW, "vx05ext2000000000001", "card", ("Measure", "Ext", "Sold"))
    s = _slicer(audit_interactions(d, _snap(proj)), SLICER)
    assert "vx04ext1000000000001" in _ids(s["no_effect"])
    assert "vx05ext2000000000001" in _ids(s["needed"])


def test_format_string_reads_count_for_a_displayed_measure(proj):
    d = _defn(proj)
    snap = _snap(proj)
    snap.measures.append(
        {
            "table": "m Metrics",
            "name": "Plain",
            "expression": "1",
            "format_string_expr": 'IF(COUNTROWS(\'Product Line\') > 0, "0", "0.0")',
            "detail_rows_expr": None,
        }
    )
    _write_visual(d, OVERVIEW, "vx06fmtstring00001", "card", ("Measure", "m Metrics", "Plain"))
    s = _slicer(audit_interactions(d, snap), SLICER)
    assert "vx06fmtstring00001" in _ids(s["needed"])


def test_field_parameter_slicer_and_calc_group_slicer(proj):
    d = _defn(proj)
    # field-parameter table expands to its candidate measures' reads (Total Amount -> Sales)
    _write_visual(d, OVERVIEW, "vx07fpvisual00001", "tableEx", ("Column", "fp Metric", "fp Metric"))
    snap = _snap(proj)
    mv = ModelView(snap)
    assert {"fp metric", "sales", "m metrics"} <= mv.expand("fp metric")
    s = _slicer(audit_interactions(d, snap), SLICER)
    assert "vx07fpvisual00001" in _ids(s["needed"])
    # calculation-group slicer reaches every visual that shows a measure
    _write_visual(d, OVERVIEW, "vx08cgslicer0001", "slicer", ("Column", "Time Calc", "Name"))
    res = audit_interactions(d, snap)
    cg = _slicer(res, "vx08cgslicer0001")
    assert cg["kind"] == "calculation group"
    assert CARD in _ids(cg["needed"])  # shows a measure


# -- rules ------------------------------------------------------------------


def test_declared_nofilter_rules_are_classified(proj):
    d = _defn(proj)
    _write_visual(
        d, OVERVIEW, "vx01ownertable00001", "tableEx", ("Column", "Owner's Book", "Owner")
    )
    _set_interactions(
        d,
        OVERVIEW,
        [
            {
                "source": SLICER,
                "target": "vx01ownertable00001",
                "type": "NoFilter",
            },  # cannot reach: perf-correct
            {"source": SLICER, "target": CARD, "type": "NoFilter"},  # reaches: blocks a dependency
            {"source": SLICER, "target": "gone000000000000001", "type": "NoFilter"},  # dangling
        ],
    )
    res = audit_interactions(d, _snap(proj))
    s = _slicer(res, SLICER)
    verdicts = {r["visual"]: r["verdict"] for r in s["no_filter_rules"]}
    assert verdicts == {"vx01ownertable00001": "no_effect", CARD: "blocks_dependency"}
    assert s["redundant_no_filter"] == ["vx01ownertable00001"]
    assert (
        _ids(s["wrong_no_filter"]) == {CARD}
        and "NoFilter blocks it" in s["wrong_no_filter"][0]["reason"]
    )
    assert "vx01ownertable00001" not in _ids(s["missing_no_filter"])  # already has its rule
    pg = next(p for p in res["pages"] if p["page_id"] == OVERVIEW)
    assert pg["dangling_interactions"][0]["target"] == "gone000000000000001"
    assert pg["suggested"]["remove_dangling"] == pg["dangling_interactions"]
    assert {"source": SLICER, "target": CARD} in pg["suggested"]["review_rules"]
    # rules are never auto-removed: the blocking rule is for review only
    assert all(a["target"] != CARD for a in pg["suggested"]["add_no_filter"])


def test_policy_marks_isolation_intentional_and_flags_missing_ones(proj):
    d = _defn(proj)
    _set_interactions(d, OVERVIEW, [{"source": SLICER, "target": CARD, "type": "NoFilter"}])
    policy = {
        "isolate": [{"slicer": SLICER, "visual": CARD}],
        "isolate_visuals": ["vc02cardlegacy000001"],
    }
    res = audit_interactions(d, _snap(proj), policy=policy)
    s = _slicer(res, SLICER)
    assert {r["visual"]: r["verdict"] for r in s["no_filter_rules"]}[
        CARD
    ] == "intentional_isolation"
    assert not s["wrong_no_filter"]
    # isolate_visuals applies to EVERY slicer; vc02 has no rule yet
    assert "vc02cardlegacy000001" in _ids(s["missing_isolation"])
    assert "vc02cardlegacy000001" in _ids(_slicer(res, SLICER2)["missing_isolation"])
    pg = next(p for p in res["pages"] if p["page_id"] == OVERVIEW)
    assert {
        "source": SLICER,
        "target": "vc02cardlegacy000001",
        "type": "NoFilter",
        "why": "policy_isolation",
    } in pg["suggested"]["add_no_filter"]


def test_policy_type_and_title_selectors(proj):
    d = _defn(proj)
    _set_interactions(d, OVERVIEW, [])
    res = audit_interactions(
        d,
        _snap(proj),
        policy={
            "isolate_visual_types": ["cardVisual"],
            "isolate": [{"slicer": "*", "visual": "Total Amount"}],
        },
    )
    s = _slicer(res, SLICER)
    assert CARD in _ids(s["missing_isolation"])  # matched by type AND by title


def test_isolation_of_an_unreachable_visual_is_not_missing(proj):
    d = _defn(proj)
    _write_visual(
        d, OVERVIEW, "vx01ownertable00001", "tableEx", ("Column", "Owner's Book", "Owner")
    )
    res = audit_interactions(d, _snap(proj), policy={"isolate_visuals": ["vx01ownertable00001"]})
    s = _slicer(res, SLICER)
    assert "vx01ownertable00001" not in _ids(
        s["missing_isolation"]
    )  # the model already isolates it


# -- suite plan -------------------------------------------------------------


def test_suite_plan_lists_what_must_and_must_not_change(proj):
    d = _defn(proj)
    _write_visual(
        d, OVERVIEW, "vx01ownertable00001", "tableEx", ("Column", "Owner's Book", "Owner")
    )
    _set_interactions(d, OVERVIEW, [{"source": SLICER, "target": CARD, "type": "NoFilter"}])
    res = audit_interactions(d, _snap(proj))
    plan = build_invariance_plan(res)
    row = next(r for r in plan if r["slicer"] == SLICER)
    assert row["field"] == "Product Line[Category]" and row["click"]["visual"] == SLICER
    assert MATRIX in _ids(row["must_change"]) and CARD not in _ids(row["must_change"])
    reasons = {r["visual"]: r["reason"] for r in row["must_not_change"]}
    assert (
        reasons["vx01ownertable00001"] == "model_unreachable"
        and reasons[CARD] == "declared_isolation"
    )


# -- degraded / edge --------------------------------------------------------


def test_without_a_model_only_declared_rules_are_reported(proj):
    res = audit_interactions(_defn(proj), None)
    assert res["model_available"] is False
    s = _slicer(res, SLICER)
    assert s["needed"] == [] and s["no_effect"] == []
    assert s["no_filter_rules"][0]["verdict"] == "unknown"  # no claim without a model


def test_page_filter_and_unknown_page(proj):
    res = audit_interactions(_defn(proj), _snap(proj), page="Overview")
    assert [p["page_id"] for p in res["pages"]] == [OVERVIEW]
    assert audit_interactions(_defn(proj), _snap(proj), page="nope")["success"] is False


def test_audit_is_read_only(proj):
    before = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in proj.rglob("*") if p.is_file()
    }
    audit_interactions(_defn(proj), _snap(proj), policy={"isolate_visuals": [CARD]})
    handle_report_operations(
        {"operation": "audit_interactions", "pbip_path": str(proj / "RPT-Sales.pbip")}
    )
    after = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in proj.rglob("*") if p.is_file()
    }
    assert before == after


# -- handler ----------------------------------------------------------------


def test_handler_op_returns_summary_and_flat_suite_plan(proj):
    res = handle_report_operations(
        {
            "operation": "audit_interactions",
            "pbip_path": str(proj / "RPT-Sales.pbip"),
            "policy": {"isolate_visuals": [CARD]},
        }
    )
    assert res["success"] and res["operation"] == "audit_interactions" and res["model_available"]
    assert res["summary"]["slicers"] == 2 and res["policy_applied"] is True
    assert isinstance(res["suite_plan"], list) and res["suite_plan"]
    assert all("suite_plan" not in p for p in res["pages"])
    assert "reload_required" not in res  # a read


def test_handler_validates_policy_and_path(proj):
    bad = handle_report_operations(
        {
            "operation": "audit_interactions",
            "pbip_path": str(proj / "RPT-Sales.pbip"),
            "policy": "x",
        }
    )
    assert bad["success"] is False
    assert handle_report_operations({"operation": "audit_interactions"})["success"] is False


def test_handler_without_bound_model_degrades(proj):
    (proj / "RPT-Sales.Report" / "definition.pbir").write_text(
        json.dumps(
            {
                "$schema": "x",
                "version": "4.0",
                "datasetReference": {
                    "byConnection": {"connectionString": "Data Source=powerbi://x"}
                },
            }
        ),
        encoding="utf-8",
    )
    res = handle_report_operations(
        {"operation": "audit_interactions", "pbip_path": str(proj / "RPT-Sales.Report")}
    )
    assert res["success"] and res["model_available"] is False and res["model_note"]
