"""16_Dependency_Graph:export op dispatch (Phase 5)."""

import json
from pathlib import Path

import server.handlers.dependency_graph_handler as h
from core.graph.models import Graph, Node, Edge


def _graph():
    g = Graph()
    g.add_node(Node(kind="measure", scoped_name="Sales[Total]"))
    g.add_node(Node(kind="column", scoped_name="Sales[Amount]"))
    g.add_edge(Edge(src="measure:Sales[Total]", dst="column:Sales[Amount]", kind="references"))
    return g


def test_export_requires_graph_id():
    out = h.handle_dependency_graph({"operation": "export"})
    assert out["success"] is False
    assert "graph_id" in out["error"]


def test_export_json_inline(monkeypatch):
    monkeypatch.setattr(h, "_get_graph", lambda gid: (_graph(), None))
    out = h.handle_dependency_graph({"operation": "export", "graph_id": "g1", "format": "json"})
    assert out["success"] is True
    assert out["format"] == "json"
    assert out["node_count"] == 2 and out["edge_count"] == 1
    data = json.loads(out["content"])
    assert len(data["nodes"]) == 2


def test_export_dot_inline(monkeypatch):
    monkeypatch.setattr(h, "_get_graph", lambda gid: (_graph(), None))
    out = h.handle_dependency_graph({"operation": "export", "graph_id": "g1", "format": "dot"})
    assert out["content"].startswith("digraph")


def test_export_unknown_format(monkeypatch):
    monkeypatch.setattr(h, "_get_graph", lambda gid: (_graph(), None))
    out = h.handle_dependency_graph({"operation": "export", "graph_id": "g1", "format": "svg"})
    assert out["success"] is False
    assert "format" in out["error"]


def test_export_spills_to_file(monkeypatch, tmp_path):
    monkeypatch.setattr(h, "_get_graph", lambda gid: (_graph(), None))
    target = tmp_path / "graph.mmd"
    # J-28: output_path goes through the path guard; the pbip_path is an allowed root.
    out = h.handle_dependency_graph(
        {
            "operation": "export",
            "graph_id": "g1",
            "format": "mermaid",
            "output_path": str(target),
            "pbip_path": str(tmp_path),
        }
    )
    assert out["success"] is True
    assert Path(out["output_path"]) == target.resolve()
    assert "content" not in out  # spilled, not inline
    assert target.read_text(encoding="utf-8").startswith("graph TD")


def test_export_bad_graph_id_propagates_error(monkeypatch):
    monkeypatch.setattr(h, "_get_graph", lambda gid: (None, h._err("nope", et="invalid_args")))
    out = h.handle_dependency_graph(
        {"operation": "export", "graph_id": "missing", "format": "json"}
    )
    assert out["success"] is False
