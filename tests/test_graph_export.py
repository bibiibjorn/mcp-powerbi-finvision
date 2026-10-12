"""16_Dependency_Graph:export — serialize a graph to DOT / Mermaid / JSON."""

import json

from core.graph.models import Graph, Node, Edge
from core.graph.export import to_dot, to_mermaid, to_json


def _graph():
    g = Graph()
    g.add_node(Node(kind="measure", scoped_name="Sales[Total]"))
    g.add_node(Node(kind="column", scoped_name="Sales[Amount]"))
    g.add_edge(Edge(src="measure:Sales[Total]", dst="column:Sales[Amount]", kind="references"))
    return g


def test_to_json_round_trips():
    g = _graph()
    data = json.loads(to_json(g))
    assert {n["id"] for n in data["nodes"]} == {"measure:Sales[Total]", "column:Sales[Amount]"}
    assert data["edges"][0]["kind"] == "references"


def test_to_dot_is_valid_digraph():
    out = to_dot(_graph())
    assert out.startswith("digraph")
    assert out.rstrip().endswith("}")
    # node + edge present, identifiers quoted (they contain brackets/colons)
    assert '"measure:Sales[Total]"' in out
    assert "->" in out


def test_to_mermaid_has_graph_header_and_edge():
    out = to_mermaid(_graph())
    assert out.splitlines()[0].strip() in ("graph TD", "flowchart TD")
    assert "-->" in out
    # labels reference the scoped names
    assert "Sales[Total]" in out


def test_empty_graph_serializes_cleanly():
    g = Graph()
    assert json.loads(to_json(g)) == {"nodes": [], "edges": []}
    assert to_dot(g).startswith("digraph")
    assert to_mermaid(g).splitlines()[0].strip() in ("graph TD", "flowchart TD")
