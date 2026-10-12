"""Tests for canned graph queries."""

from core.graph.models import Edge, Graph, Node
from core.graph.queries import (
    find_cycles,
    find_unused,
    get_downstream,
    get_impact,
    get_upstream,
)


def _g():
    g = Graph()
    g.add_node(Node(kind="table", scoped_name="Sales"))
    g.add_node(Node(kind="column", scoped_name="Sales[Amount]"))
    g.add_node(Node(kind="measure", scoped_name="Sales[Total Sales]"))
    g.add_node(Node(kind="measure", scoped_name="Sales[Avg Sales]"))
    g.add_node(Node(kind="measure", scoped_name="Sales[Unused]"))
    g.add_edge(
        Edge(src="measure:Sales[Total Sales]", dst="column:Sales[Amount]", kind="references")
    )
    g.add_edge(
        Edge(
            src="measure:Sales[Avg Sales]",
            dst="measure:Sales[Total Sales]",
            kind="references",
        )
    )
    return g


def test_get_upstream_returns_ancestors_by_depth():
    res = get_upstream(_g(), "measure:Sales[Total Sales]", depth=-1)
    assert "measure:Sales[Avg Sales]" in res["ancestors"]
    assert res["by_depth"][1] == ["measure:Sales[Avg Sales]"]


def test_get_downstream_returns_descendants_by_depth():
    res = get_downstream(_g(), "measure:Sales[Avg Sales]", depth=-1)
    assert "measure:Sales[Total Sales]" in res["descendants"]
    assert "column:Sales[Amount]" in res["descendants"]


def test_get_upstream_respects_depth_limit():
    res = get_upstream(_g(), "column:Sales[Amount]", depth=1)
    assert "measure:Sales[Total Sales]" in res["ancestors"]
    assert "measure:Sales[Avg Sales]" not in res["ancestors"]


def test_find_unused_measures():
    # J-18: "unused" = NO CONSUMER. Avg Sales references Total Sales but nothing
    # consumes Avg Sales, so it IS unused (the old rule also demanded "no
    # outgoing refs", which is why this test used to assert the opposite).
    res = find_unused(_g(), kinds=["measure"])
    ids = {u["node"] for u in res["unused"]}
    assert "measure:Sales[Unused]" in ids
    assert "measure:Sales[Avg Sales]" in ids
    assert "measure:Sales[Total Sales]" not in ids  # consumed by Avg Sales
    assert res["report_side_available"] is False and "warning" in res


def test_find_cycles_detects_self_reference_chain():
    g = Graph()
    g.add_node(Node(kind="measure", scoped_name="T[A]"))
    g.add_node(Node(kind="measure", scoped_name="T[B]"))
    g.add_edge(Edge(src="measure:T[A]", dst="measure:T[B]", kind="references"))
    g.add_edge(Edge(src="measure:T[B]", dst="measure:T[A]", kind="references"))
    res = find_cycles(g)
    assert len(res["cycles"]) >= 1


def test_get_impact_delete_lists_break_set():
    res = get_impact(_g(), "measure:Sales[Total Sales]", change="delete")
    assert "measure:Sales[Avg Sales]" in res["breaks"]
    assert res["safe"] is False


def test_get_impact_safe_when_unused():
    res = get_impact(_g(), "measure:Sales[Unused]", change="delete")
    assert res["safe"] is True
    assert res["breaks"] == []
