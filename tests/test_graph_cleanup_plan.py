"""F-13: ``16_Dependency_Graph find_unused transitive=true`` + ``cleanup_plan``.

Fixed point "used only by unused" (a sort key whose only consumer is a dead column, a measure
chain, a UDF called only by dead measures, dead reference cycles), hidden visuals never revealed
by a bookmark do not count as consumers, and the staged deletion plan (documents, per-stage
evidence, remove_manually, prerequisite visuals, plan_hash). Plans only: nothing is deleted.
Golden fixture (+ synthetic additions) in a temp dir.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from core.graph.cleanup import cleanup_plan
from core.graph.model_source import snapshot_from_model_data
from core.graph.queries import dead_visuals, find_unused, transitive_dead_set
from core.graph.service import get_or_build_graph
from server.handlers.dependency_graph_handler import handle_dependency_graph
from server.pbip_cache import pbip_cache

GOLDEN = Path(__file__).parent / "fixtures" / "pbir_golden" / "GoldenCo"
OVERVIEW = "aa01overview00000001"


@pytest.fixture()
def proj(tmp_path):
    dst = tmp_path / "GoldenCo"
    shutil.copytree(GOLDEN, dst)
    return dst


def _defn(p: Path) -> Path:
    return p / "RPT-Sales.Report" / "definition"


def _meas(table, name, expr, fs=None):
    return {
        "table": table,
        "name": name,
        "expression": expr,
        "format_string_expr": fs,
        "detail_rows_expr": None,
    }


def _col(table, name, **kw):
    return {"table": table, "name": name, "type": "data", "expression": None, "sort_by": None, **kw}


def _snap(proj: Path):
    data = pbip_cache.get_or_parse(str(proj / "RPT-Sales.pbip"))
    snap = snapshot_from_model_data(data.model_data)
    snap.columns += [
        _col("Sales", "Spare Key", sort_by="Spare Sort"),
        _col("Sales", "Spare Sort"),
        _col("Sales", "Used Spare"),
    ]
    snap.measures += [
        _meas("m Metrics", "Dead A", "[Dead B] * OnlyDead(1)"),
        _meas("m Metrics", "Dead B", "[Dead C] + 1"),
        _meas("m Metrics", "Dead C", "SUM(Sales[Spare Sort])"),
        _meas("m Metrics", "Cyc A", "[Cyc B]"),
        _meas("m Metrics", "Cyc B", "[Cyc A]"),
        _meas("m Metrics", "Hidden Only", "SUM(Sales[Used Spare])"),
        _meas("m Metrics", "Live Chain 1", "[Live Chain 2]"),
        _meas("m Metrics", "Live Chain 2", "1"),
    ]
    snap.functions.append({"name": "OnlyDead", "expression": "(x: int64) => x"})
    return snap


def _card(vid, measure, *, hidden=False, parent=None):
    doc = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.11.0/schema.json",
        "name": vid,
        "position": {"x": 0, "y": 0, "z": 1, "height": 10, "width": 10, "tabOrder": 1},
        "visual": {
            "visualType": "card",
            "query": {
                "queryState": {
                    "Values": {
                        "projections": [
                            {
                                "field": {
                                    "Measure": {
                                        "Expression": {"SourceRef": {"Entity": "m Metrics"}},
                                        "Property": measure,
                                    }
                                },
                                "queryRef": f"m Metrics.{measure}",
                            }
                        ]
                    }
                }
            },
        },
    }
    if hidden:
        doc["isHidden"] = True
    if parent:
        doc["parentGroupName"] = parent
    return doc


def _put(proj: Path, vid: str, doc):
    d = _defn(proj) / "pages" / OVERVIEW / "visuals" / vid
    d.mkdir(parents=True, exist_ok=True)
    (d / "visual.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _bookmark_reveals(proj: Path, vid: str, hidden_mode: bool):
    bm = _defn(proj) / "bookmarks" / "bm0000000000000000a1.bookmark.json"
    data = json.loads(bm.read_text(encoding="utf-8"))
    entry = {"singleVisual": {"visualType": "card"}}
    if hidden_mode:
        entry["singleVisual"]["display"] = {"mode": "hidden"}
    data["explorationState"]["sections"][OVERVIEW]["visualContainers"][vid] = entry
    bm.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _graph(proj: Path):
    res = get_or_build_graph(
        scope="both", pbip_path=str(proj / "RPT-Sales.pbip"), model=_snap(proj), use_cache=False
    )
    assert res["success"], res
    return res["graph"]


def _unused(g, **kw):
    out = find_unused(g, kinds=kw.pop("kinds", ["measure", "column", "function"]), transitive=True)
    return {u["node"]: u for u in out["unused"]}


# -- transitive find_unused -------------------------------------------------


def test_default_find_unused_is_unchanged_and_direct_only(proj):
    g = _graph(proj)
    direct = {u["node"] for u in find_unused(g, kinds=["measure", "column"])["unused"]}
    assert "measure:m Metrics[Dead A]" in direct
    assert "measure:m Metrics[Dead B]" not in direct  # consumed by Dead A
    assert "transitive" not in find_unused(g)


def test_transitive_fixed_point_and_stages(proj):
    g = _graph(proj)
    u = _unused(g)
    assert u["measure:m Metrics[Dead A]"]["stage"] == 1
    assert u["measure:m Metrics[Dead A]"]["reason"] == "no_consumers"
    assert u["measure:m Metrics[Dead B]"]["stage"] == 2
    assert u["measure:m Metrics[Dead B]"]["reason"] == "only_used_by_unused"
    assert u["measure:m Metrics[Dead B]"]["consumers"] == ["measure:m Metrics[Dead A]"]
    assert u["measure:m Metrics[Dead C]"]["stage"] == 3
    # the pbi-lineage example: a sort key whose only consumer is a dead column
    assert u["column:Sales[Spare Key]"]["stage"] == 1
    assert u["column:Sales[Spare Sort]"]["stage"] == 4  # Spare Key (1) AND Dead C (3) hold it
    # UDF called only by a dead measure
    assert (
        u["function:OnlyDead"]["stage"] == 2
        and u["function:OnlyDead"]["reason"] == "only_used_by_unused"
    )
    # live things stay live
    assert "measure:Sales[Total Amount]" not in u


def test_live_chain_without_a_visual_is_still_dead_but_used_objects_are_not(proj):
    g = _graph(proj)
    u = _unused(g)
    assert "measure:m Metrics[Live Chain 1]" in u and "measure:m Metrics[Live Chain 2]" in u
    assert u["measure:m Metrics[Live Chain 2]"]["stage"] == 2
    assert "measure:Sales[Total Amount]" not in u  # bound by a card
    assert "column:Sales[Amount]" not in u  # read by a live measure


def test_dead_reference_cycle_is_reported(proj):
    g = _graph(proj)
    td = transitive_dead_set(g)
    assert {"measure:m Metrics[Cyc A]", "measure:m Metrics[Cyc B]"} <= set(td["cycle"])
    u = _unused(g)
    assert u["measure:m Metrics[Cyc A]"]["reason"] == "unused_cycle"
    assert find_unused(g, kinds=["measure"], transitive=True)["unused_cycles"]


def test_kinds_filter_reports_only_requested_kinds_but_computes_over_all(proj):
    g = _graph(proj)
    only_cols = {u["node"] for u in find_unused(g, kinds=["column"], transitive=True)["unused"]}
    assert {"column:Sales[Spare Key]", "column:Sales[Spare Sort]"} <= only_cols
    assert all(n.startswith("column:") for n in only_cols)
    # Spare Sort is still stage 4: the dead measure chain is computed even though not reported
    stages = {
        u["node"]: u["stage"] for u in find_unused(g, kinds=["column"], transitive=True)["unused"]
    }
    assert stages["column:Sales[Spare Sort]"] == 4


# -- hidden visuals ---------------------------------------------------------


def test_hidden_visual_never_revealed_does_not_keep_a_measure_alive(proj):
    _put(proj, "vh01hidden0000000001", _card("vh01hidden0000000001", "Hidden Only", hidden=True))
    g = _graph(proj)
    assert "visual:RPT-Sales/%s/vh01hidden0000000001" % OVERVIEW in dead_visuals(g)
    u = _unused(g)
    assert u["measure:m Metrics[Hidden Only]"]["reason"] == "only_hidden_visuals"
    assert u["measure:m Metrics[Hidden Only]"]["hidden_visuals"]
    assert u["column:Sales[Used Spare]"]["stage"] == 2  # held only by the dead measure
    out = find_unused(g, kinds=["measure"], transitive=True)
    assert out["dead_visuals"] == ["visual:RPT-Sales/%s/vh01hidden0000000001" % OVERVIEW]


def test_a_bookmark_that_reveals_the_hidden_visual_keeps_it_alive(proj):
    _put(proj, "vh01hidden0000000001", _card("vh01hidden0000000001", "Hidden Only", hidden=True))
    _bookmark_reveals(proj, "vh01hidden0000000001", hidden_mode=False)
    g = _graph(proj)
    assert not dead_visuals(g)
    assert "measure:m Metrics[Hidden Only]" not in _unused(g)


def test_a_bookmark_that_only_captures_it_hidden_does_not_reveal(proj):
    _put(proj, "vh01hidden0000000001", _card("vh01hidden0000000001", "Hidden Only", hidden=True))
    _bookmark_reveals(proj, "vh01hidden0000000001", hidden_mode=True)
    g = _graph(proj)
    assert dead_visuals(g)
    assert _unused(g)["measure:m Metrics[Hidden Only]"]["reason"] == "only_hidden_visuals"


def test_children_of_a_hidden_group_are_dead_unless_the_group_is_revealed(proj):
    group = {
        "$schema": "x",
        "name": "vh02grp0000000000001",
        "position": {"x": 0, "y": 0, "z": 1, "height": 10, "width": 10},
        "visualGroup": {"displayName": "G", "groupMode": "ScaleMode"},
        "isHidden": True,
    }
    _put(proj, "vh02grp0000000000001", group)
    _put(
        proj,
        "vh03child000000000001",
        _card("vh03child000000000001", "Hidden Only", parent="vh02grp0000000000001"),
    )
    g = _graph(proj)
    assert any(k.endswith("vh03child000000000001") for k in dead_visuals(g))
    _bookmark_reveals(proj, "vh02grp0000000000001", hidden_mode=False)
    g2 = _graph(proj)
    assert not any(k.endswith("vh03child000000000001") for k in dead_visuals(g2))


# -- cleanup_plan -----------------------------------------------------------


def test_cleanup_plan_stages_documents_and_evidence(proj):
    g = _graph(proj)
    plan = cleanup_plan(g, kinds=["measure", "column", "function"])
    assert plan["applies"] is False and plan["preview"] is True
    stages = {s["stage"]: s for s in plan["stages"]}
    s1, s2 = stages[1], stages[2]
    assert "measure:m Metrics[Dead A]" in s1["nodes"] and "column:Sales[Spare Key]" in s1["nodes"]
    assert "measure:m Metrics[Dead B]" in s2["nodes"] and "function:OnlyDead" in s2["nodes"]
    # documents: columns -> apply_changeset (preview first), measures -> measure batch
    assert {"table": "Sales", "name": "Spare Key"} in s1["documents"]["changeset"]["columns"][
        "delete"
    ]
    assert s1["documents"]["apply_changeset_args"]["preview"] is True
    items = s1["documents"]["measure_batch"]["items"]
    assert {"table": "m Metrics", "name": "Dead A"} in items
    assert s2["documents"]["other_deletes"] == [
        {
            "ref": "function:OnlyDead",
            "tool": "02_Model_Operations",
            "object_type": "function",
            "operation": "delete",
            "function": "OnlyDead",
        }
    ]
    # evidence is computed as if the earlier stages were applied
    ev1, ev2 = s1["evidence"], s2["evidence"]
    assert ev2["assumes_removed"] == sorted(s1["nodes"])
    assert ev2["impacts"]["measure:m Metrics[Dead B]"]["safe"] is True
    assert ev2["impacts"]["measure:m Metrics[Dead B]"]["breaks"] == []
    assert ev1["all_safe"] and ev2["all_safe"]
    for key in ("report_side_available", "model_etag", "built_at", "graph_scope", "unknown_nodes"):
        assert key in ev1
    # without the earlier stages Dead B would still break (Dead A consumes it)
    from core.graph.queries import get_impact

    assert get_impact(g, "measure:m Metrics[Dead B]")["safe"] is False


def test_plan_hash_is_stable_and_moves_with_the_plan(proj):
    g = _graph(proj)
    a = cleanup_plan(g)["plan_hash"]
    assert a == cleanup_plan(g)["plan_hash"]
    assert cleanup_plan(g, kinds=["measure"])["plan_hash"] != a


def test_deferred_when_the_dead_consumer_kind_is_not_selected(proj):
    g = _graph(proj)
    plan = cleanup_plan(g, kinds=["column"])  # Spare Sort is held by dead MEASURE Dead C
    nodes = {n for s in plan["stages"] for n in s["nodes"]}
    assert "column:Sales[Spare Key]" in nodes and "column:Sales[Spare Sort]" not in nodes
    d = next(x for x in plan["deferred"] if x["node"] == "column:Sales[Spare Sort]")
    assert "measure:m Metrics[Dead C]" in d["blocked_by"]


def test_remove_manually_for_extension_measures_and_special_columns(proj):
    (_defn(proj) / "reportExtensions.json").write_text(
        json.dumps(
            {"entities": [{"name": "Ext", "measures": [{"name": "Ext Dead", "expression": "1"}]}]}
        ),
        encoding="utf-8",
    )
    snap = _snap(proj)
    snap.columns.append(_col("Sales", "Calc Table Col", type="calculated_table_column"))
    snap.columns.append(_col("fp Metric", "Spare FP Col"))
    res = get_or_build_graph(
        scope="both", pbip_path=str(proj / "RPT-Sales.pbip"), model=snap, use_cache=False
    )
    g = res["graph"]
    plan = cleanup_plan(g)
    assert "manage_extension_measures" in plan["remove_manually"]["measure:Ext[Ext Dead]"]
    assert "calculated table" in plan["remove_manually"]["column:Sales[Calc Table Col]"]
    stage = next(s for s in plan["stages"] if "measure:Ext[Ext Dead]" in s["nodes"])
    assert {"table": "Ext", "name": "Ext Dead"} not in stage["documents"].get(
        "measure_batch", {}
    ).get("items", [])
    # field-parameter / calc-group table columns are never auto-removed either
    assert "field-parameter" in plan["remove_manually"]["column:fp Metric[Spare FP Col]"]


def test_prerequisite_visuals_are_listed_not_deleted(proj):
    _put(proj, "vh01hidden0000000001", _card("vh01hidden0000000001", "Hidden Only", hidden=True))
    before = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in proj.rglob("*") if p.is_file()
    }
    g = _graph(proj)
    plan = cleanup_plan(g, kinds=["measure", "column"])
    pre = plan["prerequisite_visuals"]
    assert [v["visual"].split("/")[-1] for v in pre] == ["vh01hidden0000000001"]
    assert pre[0]["binds"] == ["measure:m Metrics[Hidden Only]"]
    stage = next(s for s in plan["stages"] if "measure:m Metrics[Hidden Only]" in s["nodes"])
    assert stage["requires_prerequisite_visuals"] == [pre[0]["visual"]]
    assert stage["evidence"]["impacts"]["measure:m Metrics[Hidden Only]"]["safe"] is True
    after = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in proj.rglob("*") if p.is_file()
    }
    assert before == after  # a plan never writes


def test_max_stages_truncates(proj):
    plan = cleanup_plan(_graph(proj), kinds=["measure", "column", "function"], max_stages=2)
    assert plan["stage_count"] == 2 and plan["truncated"] is True
    assert any("truncated" in w for w in plan["warnings"])


def test_unsafe_node_stays_listed_when_a_live_consumer_exists(proj):
    g = _graph(proj)
    # a live visual binding a node the plan includes would make it unsafe: simulate by editing
    # the dead set's evidence call directly for a LIVE measure
    from core.graph.cleanup import _impact_after

    imp = _impact_after(g, "measure:Sales[Total Amount]", set(), {})
    assert imp["safe"] is False and imp["breaks"]


# -- handler ----------------------------------------------------------------


def _handler_graph(proj):
    res = get_or_build_graph(
        scope="both", pbip_path=str(proj / "RPT-Sales.pbip"), model=_snap(proj), use_cache=True
    )
    return res["graph_id"]


def test_handler_find_unused_transitive_flag(proj):
    gid = _handler_graph(proj)
    direct = handle_dependency_graph(
        {"operation": "find_unused", "graph_id": gid, "kinds": ["measure"]}
    )
    trans = handle_dependency_graph(
        {"operation": "find_unused", "graph_id": gid, "kinds": ["measure"], "transitive": True}
    )
    assert direct["success"] and trans["success"] and trans["transitive"] is True
    assert len(trans["unused"]) > len(direct["unused"])
    # the string "false" must not enable it
    off = handle_dependency_graph(
        {"operation": "find_unused", "graph_id": gid, "kinds": ["measure"], "transitive": "false"}
    )
    assert "transitive" not in off


def test_handler_cleanup_plan(proj):
    gid = _handler_graph(proj)
    res = handle_dependency_graph(
        {"operation": "cleanup_plan", "graph_id": gid, "kinds": ["measure", "column"]}
    )
    assert res["success"] and res["stage_count"] >= 3 and res["applies"] is False
    assert res["plan_hash"] and res["report_side_available"] is True
    assert handle_dependency_graph({"operation": "cleanup_plan"})["success"] is False
    bad = handle_dependency_graph({"operation": "cleanup_plan", "graph_id": gid, "max_stages": "x"})
    assert bad["success"] is False


def test_cleanup_plan_refuses_a_model_only_graph(proj):
    res = get_or_build_graph(scope="model", model=_snap(proj), use_cache=True)
    out = handle_dependency_graph({"operation": "cleanup_plan", "graph_id": res["graph_id"]})
    assert out["success"] is False and out["error_type"] == "report_side_unavailable"
