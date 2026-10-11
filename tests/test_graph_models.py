"""Tests for core.graph.models dataclasses."""

from core.graph.models import Node, Edge, Graph


def test_node_id_is_kind_plus_scoped_name():
    n = Node(kind="measure", scoped_name="Sales[Total]")
    assert n.id == "measure:Sales[Total]"


def test_edge_id_is_deterministic_for_same_endpoints():
    e1 = Edge(src="measure:Sales[Total]", dst="column:Sales[Amount]", kind="references")
    e2 = Edge(src="measure:Sales[Total]", dst="column:Sales[Amount]", kind="references")
    assert e1.id == e2.id


def test_graph_add_node_is_idempotent():
    g = Graph()
    g.add_node(Node(kind="measure", scoped_name="Sales[Total]"))
    g.add_node(Node(kind="measure", scoped_name="Sales[Total]"))
    assert len(g.nodes) == 1


def test_graph_add_edge_is_idempotent():
    g = Graph()
    e = Edge(src="a", dst="b", kind="references")
    g.add_edge(e)
    g.add_edge(e)
    assert len(g.edges) == 1


def test_graph_to_dict_roundtrip():
    g = Graph()
    g.add_node(Node(kind="measure", scoped_name="Sales[Total]"))
    g.add_node(Node(kind="column", scoped_name="Sales[Amount]"))
    g.add_edge(Edge(src="measure:Sales[Total]", dst="column:Sales[Amount]", kind="references"))
    d = g.to_dict()
    g2 = Graph.from_dict(d)
    assert g.to_dict() == g2.to_dict()
