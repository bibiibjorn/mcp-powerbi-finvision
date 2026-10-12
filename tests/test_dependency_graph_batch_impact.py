"""P1c: batch get_impact (node_refs[]) + rename collision check.

Phase 2's evidence-aware gate needs to check the blast radius of many objects
in one call (deleting 15 unused columns should not cost 15 round trips). Also,
get_impact(change='rename') previously always returned safe=True and never
checked for a name collision — the most common rename failure mode.
"""

from core.graph.models import Edge, Graph, Node
from core.graph.queries import get_impact
import server.handlers.dependency_graph_handler as dh


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
        Edge(src="measure:Sales[Avg Sales]", dst="measure:Sales[Total Sales]", kind="references")
    )
    return g


# ---------------------------------------------------------------------------
# get_impact rename collision
# ---------------------------------------------------------------------------
def test_rename_to_existing_name_is_unsafe():
    res = get_impact(_g(), "measure:Sales[Unused]", change="rename", new_name="Total Sales")
    assert res["safe"] is False
    assert res.get("collision") is True


def test_rename_to_free_name_is_safe():
    res = get_impact(_g(), "measure:Sales[Unused]", change="rename", new_name="Brand New")
    assert res["safe"] is True
    assert not res.get("collision")


def test_rename_with_rebinds_is_unsafe_unless_the_op_cascades():
    # J-22: this used to pin "rebinds only -> still safe", which let caller-supplied
    # rename evidence auto-clear CATASTROPHIC cascade renames. Avg Sales must be
    # rebound, so the rename is safe only when the op rewrites its consumers.
    res = get_impact(_g(), "measure:Sales[Total Sales]", change="rename")
    assert res["safe"] is False
    assert res["rebinds_needed"] == ["measure:Sales[Avg Sales]"]
    assert (
        get_impact(_g(), "measure:Sales[Total Sales]", change="rename", cascade=True)["safe"]
        is True
    )


# ---------------------------------------------------------------------------
# batch_impact handler op
# ---------------------------------------------------------------------------
def _patch_graph(monkeypatch, graph):
    monkeypatch.setattr(dh, "_get_graph", lambda gid: (graph, None))


def test_batch_impact_returns_per_ref_and_summary(monkeypatch):
    _patch_graph(monkeypatch, _g())
    out = dh.handle_dependency_graph(
        {
            "operation": "batch_impact",
            "graph_id": "g1",
            "node_refs": ["measure:Sales[Unused]", "measure:Sales[Total Sales]"],
            "change": "delete",
        }
    )
    assert out["success"] is True
    impacts = out["impacts"]
    assert impacts["measure:Sales[Unused]"]["safe"] is True
    assert impacts["measure:Sales[Total Sales]"]["safe"] is False  # Avg Sales depends on it
    assert out["all_safe"] is False
    assert "measure:Sales[Total Sales]" in out["unsafe_nodes"]
    assert "measure:Sales[Unused]" not in out["unsafe_nodes"]


def test_batch_impact_all_safe(monkeypatch):
    _patch_graph(monkeypatch, _g())
    out = dh.handle_dependency_graph(
        {
            "operation": "batch_impact",
            "graph_id": "g1",
            "node_refs": ["measure:Sales[Unused]"],
            "change": "delete",
        }
    )
    assert out["all_safe"] is True
    assert out["unsafe_nodes"] == []


def test_batch_impact_requires_node_refs(monkeypatch):
    _patch_graph(monkeypatch, _g())
    out = dh.handle_dependency_graph({"operation": "batch_impact", "graph_id": "g1"})
    assert out["success"] is False
    assert out["error_type"] == "invalid_args"


def test_batch_impact_is_registered():
    assert "batch_impact" in dh._VALID_OPS


# ---------------------------------------------------------------------------
# FIX 4 — table delete must not count the table's OWN children as breaks
# ---------------------------------------------------------------------------
def _table_graph():
    """A table with its own column + measure, plus an EXTERNAL measure that
    references the table's column (through the belongs_to hop)."""
    g = Graph()
    g.add_node(Node(kind="table", scoped_name="Sales"))
    g.add_node(Node(kind="column", scoped_name="Sales[Amount]"))
    g.add_node(Node(kind="measure", scoped_name="Sales[Total]"))
    g.add_node(Node(kind="table", scoped_name="Other"))
    g.add_node(Node(kind="measure", scoped_name="Other[External]"))
    # belongs_to: owned children point INTO the table.
    g.add_edge(Edge(src="column:Sales[Amount]", dst="table:Sales", kind="belongs_to"))
    g.add_edge(Edge(src="measure:Sales[Total]", dst="table:Sales", kind="belongs_to"))
    # External measure references the owned column -> it breaks if the table dies.
    g.add_edge(Edge(src="measure:Other[External]", dst="column:Sales[Amount]", kind="references"))
    return g


def test_table_delete_excludes_own_children_from_breaks():
    res = get_impact(_table_graph(), "table:Sales", change="delete")
    # The table's own column/measure are deleted WITH it -> not "breaks".
    assert "column:Sales[Amount]" not in res["breaks"]
    assert "measure:Sales[Total]" not in res["breaks"]
    # But the external measure that references a column of the table DOES break.
    assert "measure:Other[External]" in res["breaks"]
    assert res["safe"] is False  # recomputed after subtraction


def test_empty_table_delete_is_safe():
    g = Graph()
    g.add_node(Node(kind="table", scoped_name="Lonely"))
    res = get_impact(g, "table:Lonely", change="delete")
    assert res["breaks"] == []
    assert res["safe"] is True


def test_table_with_only_own_children_is_safe():
    g = Graph()
    g.add_node(Node(kind="table", scoped_name="Sales"))
    g.add_node(Node(kind="column", scoped_name="Sales[Amount]"))
    g.add_node(Node(kind="measure", scoped_name="Sales[Total]"))
    g.add_edge(Edge(src="column:Sales[Amount]", dst="table:Sales", kind="belongs_to"))
    g.add_edge(Edge(src="measure:Sales[Total]", dst="table:Sales", kind="belongs_to"))
    # Total references its own Amount column, no external consumers.
    g.add_edge(Edge(src="measure:Sales[Total]", dst="column:Sales[Amount]", kind="references"))
    res = get_impact(g, "table:Sales", change="delete")
    assert res["breaks"] == []
    assert res["safe"] is True


def test_non_table_delete_unaffected_by_child_exclusion():
    # A measure delete still counts its consumers normally.
    res = get_impact(_g(), "measure:Sales[Total Sales]", change="delete")
    assert "measure:Sales[Avg Sales]" in res["breaks"]
    assert res["safe"] is False
