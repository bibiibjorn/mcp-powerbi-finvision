"""Tests for JSONPath escape hatch."""

from core.graph.models import Graph, Node
from core.graph.queries import query


def _g():
    g = Graph()
    g.add_node(Node(kind="measure", scoped_name="A[m1]"))
    g.add_node(Node(kind="measure", scoped_name="A[m2]"))
    g.add_node(Node(kind="column", scoped_name="A[c1]"))
    return g


def test_jsonpath_filters_by_kind():
    res = query(_g(), "$.nodes[?kind=='measure']")
    assert len(res["matches"]) == 2
    assert res["truncated"] is False


def test_jsonpath_truncation_returns_flag():
    g = Graph()
    for i in range(600):
        g.add_node(Node(kind="measure", scoped_name=f"T[m{i}]"))
    res = query(g, "$.nodes[?kind=='measure']")
    assert res["truncated"] is True
    assert len(res["matches"]) == 500


def test_jsonpath_invalid_returns_error():
    res = query(_g(), "$$$$invalid$$$$")
    assert res.get("success") is False
    assert res.get("error_type") == "invalid_args"
