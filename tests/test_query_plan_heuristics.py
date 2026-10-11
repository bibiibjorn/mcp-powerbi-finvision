"""Tests for query-plan heuristics."""

from core.query_plan.heuristics import analyze, list_rules
from core.query_plan.parser import parse_logical_plan


def _tree_with_callbacks(n=10):
    return parse_logical_plan(
        "Root: RelLogOp\n" + "\n".join([f" CallbackDataID: ScaLogOp #{i}" for i in range(n)])
    )


def test_list_rules_returns_metadata():
    out = list_rules()
    assert any(r["id"] == "callback_data_id_count" for r in out)


def test_callback_count_warn_threshold():
    res = analyze(_tree_with_callbacks(6), {}, {})
    ids = {f["rule"] for f in res["findings"]}
    assert "callback_data_id_count" in ids
    assert res["verdict"] in {"warn", "hot"}


def test_no_findings_returns_ok_verdict():
    tree = parse_logical_plan("Scan_Vertipaq: RelLogOp")
    res = analyze(tree, {}, {})
    assert res["verdict"] == "ok"
    assert res["findings"] == []


def test_cartesian_join_is_hot():
    tree = parse_logical_plan("PhysicalRoot: RelPhyOp\n CrossApply: RelPhyOp Cartesian")
    res = analyze(tree, {}, {})
    ids = {f["rule"] for f in res["findings"]}
    assert "cartesian_join_in_physical_plan" in ids
    assert res["verdict"] == "hot"
