"""Tests for 16_Dependency_Graph handler dispatch."""

from server.handlers.dependency_graph_handler import handle_dependency_graph


def test_unknown_op_returns_error():
    res = handle_dependency_graph({"operation": "bogus"})
    assert res["success"] is False
    assert "valid_operations" in res


def test_get_upstream_requires_graph_id():
    res = handle_dependency_graph({"operation": "get_upstream"})
    assert res["success"] is False


def test_query_requires_jsonpath():
    res = handle_dependency_graph({"operation": "query", "graph_id": "abc"})
    assert res["success"] is False


def test_build_with_unknown_scope_errors():
    res = handle_dependency_graph({"operation": "build", "scope": "nonsense"})
    assert res["success"] is False
