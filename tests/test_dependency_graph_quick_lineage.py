"""T1-I: tests for 16_Dependency_Graph:quick_lineage sugar op.

quick_lineage builds (or reuses cached) the graph then returns upstream /
downstream / impact in one call. Mocks the live-model + graph builder so we
don't need PBI Desktop.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from server.handlers import dependency_graph_handler as mod


def _fake_graph():
    from core.graph.models import Graph, Node

    g = Graph()
    g.add_node(Node(kind="measure", scoped_name="Sales[Revenue]"))
    g.add_node(Node(kind="measure", scoped_name="Sales[Profit]"))
    return g


def _fake_query_returns(name: str):
    def f(graph, node_ref, **kw):
        return {"results": [f"{name}-result"], "node_ref": node_ref}

    return f


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch):
    """Stub out the graph build pipeline at its seam (``_get_or_build`` ->
    ``core.graph.service.get_or_build_graph``) and the query functions, so the
    dispatch shell is tested without PBI Desktop."""
    import sys

    monkeypatch.setattr(
        mod,
        "_get_or_build",
        lambda args: {
            "success": True,
            "graph_id": "graph-xyz",
            "graph": _fake_graph(),
            "cached": False,
        },
    )
    fake_queries = MagicMock()
    fake_queries.get_upstream.side_effect = _fake_query_returns("upstream")
    fake_queries.get_downstream.side_effect = _fake_query_returns("downstream")
    fake_queries.get_impact.side_effect = _fake_query_returns("impact")
    monkeypatch.setitem(sys.modules, "core.graph.queries", fake_queries)
    return None, fake_queries


def test_quick_lineage_returns_both_directions_by_default(patched) -> None:
    res = mod.handle_dependency_graph(
        {
            "operation": "quick_lineage",
            "node_ref": "Sales[Revenue]",
        }
    )
    assert res["success"] is True
    assert res["graph_id"] == "graph-xyz"
    assert res["cached"] is False
    assert "upstream" in res
    assert "downstream" in res
    # impact NOT requested with default direction.
    assert "impact" not in res


def test_quick_lineage_direction_upstream_only(patched) -> None:
    res = mod.handle_dependency_graph(
        {
            "operation": "quick_lineage",
            "node_ref": "Sales[Revenue]",
            "direction": "upstream",
        }
    )
    assert "upstream" in res
    assert "downstream" not in res


def test_quick_lineage_direction_impact(patched) -> None:
    res = mod.handle_dependency_graph(
        {
            "operation": "quick_lineage",
            "node_ref": "Sales[Revenue]",
            "direction": "impact",
            "change": "rename",
        }
    )
    assert "impact" in res
    assert "upstream" not in res


def test_quick_lineage_rejects_invalid_direction(patched) -> None:
    res = mod.handle_dependency_graph(
        {
            "operation": "quick_lineage",
            "node_ref": "Sales[Revenue]",
            "direction": "sideways",
        }
    )
    assert res["success"] is False
    assert "valid_directions" in res


def test_quick_lineage_requires_node_ref(patched) -> None:
    res = mod.handle_dependency_graph({"operation": "quick_lineage"})
    assert res["success"] is False
