"""Tests for core.autonomous.evidence — auto-population of blast-radius evidence.

The evidence auto-populator builds a dependency graph in-process and computes
the blast radius for a set of node_refs, returning a dict shaped exactly the way
the evidence-aware gate (`gating._evidence_proves_safe`) expects. It must be
best-effort: any failure (not connected, build error, exception) returns None so
the destructive-op gate falls back to its normal block/advisory behaviour and
never crashes the hot path.

Graph construction is injectable so these tests need no live AMO/CLR.
"""

from __future__ import annotations


from core.autonomous import evidence as ev
from core.graph.models import Edge, Graph, Node


def _graph_with(measures=(), columns=(), tables=(), edges=()):
    g = Graph()
    for t in tables:
        g.add_node(Node(kind="table", scoped_name=t))
    for scoped in columns:
        g.add_node(Node(kind="column", scoped_name=scoped))
    for scoped in measures:
        g.add_node(Node(kind="measure", scoped_name=scoped))
    for src, dst, kind in edges:
        g.add_edge(Edge(src=src, dst=dst, kind=kind))
    return g


def test_delete_evidence_all_safe_when_no_consumers(monkeypatch):
    # Orphan measure — nothing references it, nothing binds to it.
    g = _graph_with(measures=["Sales[Orphan]"], tables=["Sales"])
    monkeypatch.setattr(ev, "_build_graph_for_evidence", lambda pbip_path=None: g)

    out = ev.compute_delete_evidence(["measure:Sales[Orphan]"])
    assert out is not None
    br = out["blast_radius"]
    assert br["all_safe"] is True
    assert br["unsafe_nodes"] == []
    assert "measure:Sales[Orphan]" in br["impacts"]


def test_delete_evidence_unsafe_when_consumed(monkeypatch):
    # Base measure is referenced by Derived -> deleting Base breaks Derived.
    g = _graph_with(
        measures=["Sales[Base]", "Sales[Derived]"],
        tables=["Sales"],
        edges=[("measure:Sales[Derived]", "measure:Sales[Base]", "references")],
    )
    monkeypatch.setattr(ev, "_build_graph_for_evidence", lambda pbip_path=None: g)

    out = ev.compute_delete_evidence(["measure:Sales[Base]"])
    assert out is not None
    br = out["blast_radius"]
    assert br["all_safe"] is False
    assert "measure:Sales[Base]" in br["unsafe_nodes"]


def test_delete_evidence_returns_none_when_graph_unavailable(monkeypatch):
    monkeypatch.setattr(ev, "_build_graph_for_evidence", lambda pbip_path=None: None)
    assert ev.compute_delete_evidence(["measure:Sales[X]"]) is None


def test_delete_evidence_returns_none_on_exception(monkeypatch):
    def _boom(pbip_path=None):
        raise RuntimeError("CLR exploded")

    monkeypatch.setattr(ev, "_build_graph_for_evidence", _boom)
    assert ev.compute_delete_evidence(["measure:Sales[X]"]) is None


def test_delete_evidence_returns_none_for_empty_refs(monkeypatch):
    # No refs to check -> nothing to prove -> None (don't fabricate all_safe).
    monkeypatch.setattr(
        ev, "_build_graph_for_evidence", lambda pbip_path=None: _graph_with(tables=["Sales"])
    )
    assert ev.compute_delete_evidence([]) is None


def test_table_delete_ref_helper():
    assert ev.table_node_ref("Sales") == "table:Sales"


def test_measure_delete_refs_from_items():
    items = [
        {"table_name": "Sales", "name": "M1"},
        {"table": "Sales", "measure_name": "M2"},
        {"name": "NoTable"},  # skipped — can't build a ref
    ]
    refs = ev.measure_node_refs(items)
    assert refs == ["measure:Sales[M1]", "measure:Sales[M2]"]


def test_rename_evidence_flags_collision(monkeypatch):
    g = _graph_with(measures=["Sales[A]", "Sales[B]"], tables=["Sales"])
    monkeypatch.setattr(ev, "_build_graph_for_evidence", lambda pbip_path=None: g)

    # Renaming A -> B collides with the existing B.
    out = ev.compute_rename_evidence("measure:Sales[A]", "B")
    assert out is not None
    assert out["blast_radius"]["all_safe"] is False


def test_rename_evidence_safe_when_no_collision(monkeypatch):
    g = _graph_with(measures=["Sales[A]"], tables=["Sales"])
    monkeypatch.setattr(ev, "_build_graph_for_evidence", lambda pbip_path=None: g)

    out = ev.compute_rename_evidence("measure:Sales[A]", "Fresh")
    assert out is not None
    assert out["blast_radius"]["all_safe"] is True


# ---------------------------------------------------------------------------
# FIX 1 — node_not_found / empty-graph must not be counted as "safe"
# ---------------------------------------------------------------------------
def test_delete_evidence_none_when_ref_missing_from_graph(monkeypatch):
    # The graph knows Sales[A] but the caller asks about Sales[Ghost]. get_impact
    # would return note='node_not_found', safe=True — but we cannot vouch for a
    # node we can't see, so evidence must be None (unavailable), not "all_safe".
    g = _graph_with(measures=["Sales[A]"], tables=["Sales"])
    monkeypatch.setattr(ev, "_build_graph_for_evidence", lambda pbip_path=None: g)
    assert ev.compute_delete_evidence(["measure:Sales[Ghost]"]) is None


def test_delete_evidence_none_when_any_ref_missing(monkeypatch):
    # One known, one unknown -> None (never partial-vouch).
    g = _graph_with(measures=["Sales[A]"], tables=["Sales"])
    monkeypatch.setattr(ev, "_build_graph_for_evidence", lambda pbip_path=None: g)
    assert ev.compute_delete_evidence(["measure:Sales[A]", "measure:Sales[Ghost]"]) is None


def test_delete_evidence_none_when_graph_empty(monkeypatch):
    # build_live_model can swallow per-DMV failures into an empty model -> empty
    # graph. Zero nodes = we know nothing = evidence unavailable.
    monkeypatch.setattr(ev, "_build_graph_for_evidence", lambda pbip_path=None: Graph())
    assert ev.compute_delete_evidence(["measure:Sales[A]"]) is None


def test_rename_evidence_none_when_ref_missing(monkeypatch):
    g = _graph_with(measures=["Sales[A]"], tables=["Sales"])
    monkeypatch.setattr(ev, "_build_graph_for_evidence", lambda pbip_path=None: g)
    assert ev.compute_rename_evidence("measure:Sales[Ghost]", "Fresh") is None


# ---------------------------------------------------------------------------
# FIX 2 — _build_graph_for_evidence fails closed on missing/failed report side
# (seam: core.graph.service.get_or_build_graph — the WP11 build-or-reuse path)
# ---------------------------------------------------------------------------
def _service_returning(monkeypatch, *results):
    import core.graph.service as service

    calls = []
    queue = list(results)

    def _fake(**kw):
        calls.append(kw)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(service, "get_or_build_graph", _fake)
    return calls


def test_build_graph_none_when_not_connected(monkeypatch):
    import core.infrastructure.connection_state as cs

    monkeypatch.setattr(cs.connection_state, "is_connected", lambda: False)
    assert ev._build_graph_for_evidence() is None


def test_build_graph_none_when_service_errors(monkeypatch):
    # Not connected / no pbip path / snapshot unavailable -> error envelope -> None.
    _service_returning(
        monkeypatch, {"success": False, "error_type": "invalid_args", "error": "no pbip"}
    )
    assert ev._build_graph_for_evidence(pbip_path=None) is None


def test_build_graph_none_when_report_side_empty(monkeypatch):
    # The report engine failed into an empty report graph. Fail closed.
    model_only = _graph_with(measures=["Sales[A]"], tables=["Sales"])
    model_only.meta["report_side_available"] = False
    _service_returning(monkeypatch, {"success": True, "graph": model_only, "cached": False})
    assert ev._build_graph_for_evidence(pbip_path="C:/x/proj.pbip") is None


def test_build_graph_ok_when_report_side_present(monkeypatch):
    g = _graph_with(measures=["Sales[A]"], tables=["Sales"])
    g.add_node(Node(kind="page", scoped_name="p1"))
    g.add_node(Node(kind="visual", scoped_name="p1/v1"))
    calls = _service_returning(monkeypatch, {"success": True, "graph": g, "cached": False})
    assert ev._build_graph_for_evidence(pbip_path="C:/x/proj.pbip") is g
    # Full graph over EVERY report bound to the model, for the requested project.
    assert calls == [
        {
            "scope": "both",
            "pbip_path": "C:/x/proj.pbip",
            "use_cache": True,
            "all_bound_reports": True,
        }
    ]


# ---------------------------------------------------------------------------
# FIX 3 — accept 'measure'/'column' name aliases in the ref builders
# ---------------------------------------------------------------------------
def test_measure_refs_accept_measure_alias():
    items = [
        {"table": "Sales", "measure": "M1"},  # 'measure' key (batch executor alias)
        {"table_name": "Sales", "name": "M2"},
    ]
    assert ev.measure_node_refs(items) == ["measure:Sales[M1]", "measure:Sales[M2]"]


def test_column_refs_accept_column_alias():
    items = [
        {"table": "Sales", "column": "C1"},  # 'column' key (changeset alias)
        {"table_name": "Sales", "name": "C2"},
    ]
    assert ev.column_node_refs(items) == ["column:Sales[C1]", "column:Sales[C2]"]
