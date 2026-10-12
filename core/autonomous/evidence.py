"""Auto-populate blast-radius evidence for the evidence-aware destructive gate.

`gating.require_autonomous_mode(..., evidence=)` can auto-clear a CATASTROPHIC op
to an advisory (under LIGHT) when the caller supplies evidence proving the op is
safe — a zero blast radius from the dependency graph, optionally corroborated by
a no-regression baseline diff. Previously an *agent* had to compute that evidence
by calling 16_Dependency_Graph:batch_impact and threading the result back in.

This module lets the destructive handlers compute that evidence *themselves*,
in-process, so the safe-delete downgrade works without a second round trip. It
reuses the same ``core.graph.service.get_or_build_graph`` → ``get_impact`` path
the 16_Dependency_Graph handler uses (and the same cache, so a warm graph is
reused).

Everything here is **best-effort**: any failure — not connected, graph build
error, unexpected exception — returns ``None``. The gate then falls back to its
normal block/advisory behaviour. Evidence auto-population can only ever *relax* a
gate when it affirmatively proves safety; it can never make the gate stricter or
crash the destructive op's hot path.

Proof contract (review C-02 / C-03): evidence is computed ONLY here, in-process,
from the refs the op will actually touch — the gate never reads a
caller-supplied ``evidence`` argument. A blast radius counts as *proof* only
when every ``get_impact`` result says ``report_side_available: true``, lists no
``unknown_nodes`` (edges the graph could not resolve), was built with
``graph_scope == "both"`` and carries the ``model_etag`` of the model as it is
NOW (a graph built before other edits proves nothing) — AND the graph itself
has ``model_side_complete: true`` (every core INFO.* view answered), no
unscanned ``other_bound_reports`` (the graph is built with
``all_bound_reports=True``) and ``freshness(graph)["stale"]`` false. A missing
field means "not proven". The evidence dict carries ``proven`` +
``unproven_reasons`` + ``provenance`` so a blocked caller sees why.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def attach_blast_radius(
    response: Optional[Dict[str, Any]], evidence: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Surface a computed-but-not-clearing blast radius on a gate block response.

    When the handler computed evidence in-process but it did NOT prove safety
    (so the gate blocked), the expensive analysis is otherwise discarded — the
    agent never sees WHY. Copy the blast radius onto the block dict so the reason
    travels with the block. No-op unless both are dicts and a blast_radius exists.
    """
    if not isinstance(response, dict) or not isinstance(evidence, dict):
        return response
    br = evidence.get("blast_radius")
    if isinstance(br, dict):
        response.setdefault("blast_radius", br)
        if "unsafe_nodes" in br:
            response.setdefault("unsafe_nodes", br["unsafe_nodes"])
    return response


def table_node_ref(table_name: str) -> str:
    """Dependency-graph node id for a table (``table:Sales``)."""
    return f"table:{table_name}"


def measure_node_refs(items: Any) -> List[str]:
    """Build ``measure:Table[Name]`` refs from a batch-delete ``items`` list.

    Accepts the loose dict shapes the batch handler passes through — table under
    ``table_name`` or ``table``, name under ``name``, ``measure_name`` or
    ``measure`` (the batch executor in ``batch_operations`` accepts the bare
    ``measure`` alias, so the ref builder must too, or coverage silently drops
    those items). Items missing either half are skipped (no ref can be built),
    rather than emitting a malformed ref that would silently miss in the graph.
    """
    refs: List[str] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        table = it.get("table_name") or it.get("table")
        # Same precedence as the executor (batch_operations._m) — C-19.
        name = it.get("measure_name") or it.get("measure") or it.get("name")
        if table and name:
            refs.append(f"measure:{table}[{name}]")
    return refs


def column_node_refs(items: Any) -> List[str]:
    """Build ``column:Table[Name]`` refs from a batch-delete ``items`` list.

    Name accepted under ``name``, ``column_name`` or ``column`` (the changeset
    column path accepts the bare ``column`` alias, so the ref builder must match
    or coverage silently drops those items).
    """
    refs: List[str] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        table = it.get("table_name") or it.get("table")
        # Same precedence as the executor (batch_operations._cn) — C-19.
        name = it.get("column_name") or it.get("name") or it.get("column")
        if table and name:
            refs.append(f"column:{table}[{name}]")
    return refs


def _has_report_side(graph) -> bool:
    """True when the graph covers the report side (pages/visuals were scanned).

    ``Graph.has_report_side()`` reads the builder's ``report_side_available``
    meta flag (falls back to "has page/visual nodes" for graphs without meta).
    A graph with model nodes but no report side misses every visual 'binds'
    edge, so a report-bound measure would show a false zero blast radius.
    """
    fn = getattr(graph, "has_report_side", None)
    if callable(fn):
        try:
            return bool(fn())
        except Exception:  # noqa: BLE001
            return False
    return any(getattr(n, "kind", None) in {"page", "visual"} for n in graph.nodes.values())


def _build_graph_for_evidence(pbip_path: Optional[str] = None):
    """Build (or reuse the cached) model+report dependency graph for evidence.

    Uses ``core.graph.service.get_or_build_graph`` — the same build-or-reuse
    path as 16_Dependency_Graph (value-keyed cache, full INFO.* model side,
    PBIR report side). Built with ``all_bound_reports=True`` so every report
    bound to the model counts, not just the connected one.

    Fails CLOSED (returns None) whenever the graph can't be trusted to reflect
    the FULL blast radius — the safe-delete downgrade must never fire on a
    partial view:

      - not connected / snapshot unavailable / no pbip path → the service
        returns an error envelope → None,
      - the built graph has zero nodes → None,
      - the report side is missing → None.

    A cached graph that ``freshness()`` reports as stale (the model generation
    moved since it was built) is rebuilt once without the cache. Isolated as a
    module-level function so tests can monkeypatch it without touching CLR.
    """
    from core.graph import service

    res = service.get_or_build_graph(
        scope="both", pbip_path=pbip_path, use_cache=True, all_bound_reports=True
    )
    if not isinstance(res, dict) or not res.get("success"):
        return None
    graph = res.get("graph")
    if graph is not None and res.get("cached") and service.freshness(graph).get("stale"):
        res = service.get_or_build_graph(
            scope="both", pbip_path=pbip_path, use_cache=False, all_bound_reports=True
        )
        if not isinstance(res, dict) or not res.get("success"):
            return None
        graph = res.get("graph")
    # Empty graph = we know nothing; report side missing = partial view. Fail closed.
    if graph is None or not graph.nodes or not _has_report_side(graph):
        return None
    return graph


def _current_model_etag() -> Optional[str]:
    """Etag of the live model AS IT IS NOW (``ModelSnapshot.etag()``).

    ``load_model_snapshot`` re-reads the model unless connection identity,
    model generation and the model-version fingerprint are all unchanged, so
    this is the etag the graph must carry to count as fresh. None = unknown
    (not connected / snapshot unavailable) → freshness unverifiable.
    """
    try:
        from core.graph.service import load_model_snapshot

        snap, err = load_model_snapshot()
        if err or snap is None:
            return None
        return snap.etag()
    except Exception as e:  # noqa: BLE001
        logger.debug("current model etag failed: %s", e)
        return None


def _graph_unproven_reasons(graph, current_etag: Optional[str]) -> List[str]:
    """Why the graph as a whole is not proof (empty list = proof)."""
    reasons: List[str] = []
    meta = getattr(graph, "meta", None) or {}
    if meta.get("model_side_complete") is not True:
        reasons.append("model side incomplete (an INFO.* view failed or was not read)")
    others = meta.get("other_bound_reports")
    if others:
        reasons.append(f"{len(others)} other bound report(s) not scanned")
    try:
        from core.graph.service import freshness

        fr = freshness(graph)
        if fr.get("stale"):
            reasons.append(
                "graph is stale (" + str(fr.get("stale_reason") or "model changed") + ")"
            )
    except Exception as e:  # noqa: BLE001
        logger.debug("graph freshness failed: %s", e)
        reasons.append("graph freshness unknown")
    if not current_etag:
        reasons.append("current model etag unknown (freshness unverifiable)")
    return reasons


def _impact_unproven_reasons(imp: Dict[str, Any], current_etag: Optional[str]) -> List[str]:
    """Why one get_impact result is not proof (empty list = proof)."""
    reasons: List[str] = []
    if imp.get("report_side_available") is not True:
        reasons.append("report side not available (visual bindings unknown)")
    if "unknown_nodes" not in imp:
        reasons.append("graph did not report unresolved references (unknown_nodes missing)")
    elif imp.get("unknown_nodes"):
        reasons.append(f"{len(imp['unknown_nodes'])} unresolved reference(s) in the graph")
    if imp.get("graph_scope") != "both":
        reasons.append(f"graph scope is {imp.get('graph_scope')!r}, not 'both'")
    if imp.get("stale"):
        reasons.append("impact result flagged stale")
    etag = imp.get("model_etag")
    if not etag:
        reasons.append("graph carries no model_etag (freshness unknown)")
    elif current_etag and etag != current_etag:
        reasons.append("graph is stale (built for a different model state)")
    return reasons


def _blast_radius(refs: List[str], *, change: str, new_name: Optional[str], pbip_path):
    """Compute the batch_impact-shaped blast radius + proof status, or None."""
    if not refs:
        return None
    try:
        graph = _build_graph_for_evidence(pbip_path)
    except Exception as e:  # noqa: BLE001 — best-effort; never let evidence crash the op
        logger.debug("evidence graph build failed: %s", e)
        return None
    if graph is None:
        return None

    from core.graph.queries import get_impact

    current_etag = _current_model_etag()
    impacts: Dict[str, Any] = {}
    unsafe_nodes: List[str] = []
    reasons: List[str] = _graph_unproven_reasons(graph, current_etag)
    for ref in refs:
        imp = get_impact(graph, ref, change=change, new_name=new_name)
        # An unknown / ambiguous ref: we can't vouch for the blast radius of a
        # node we can't see (or can't pin down), so a single one makes the whole
        # evidence unavailable — never partial-vouch.
        if imp.get("note") in ("node_not_found", "node_ambiguous"):
            return None
        impacts[ref] = imp
        if not imp.get("safe", False):
            unsafe_nodes.append(ref)
        for r in _impact_unproven_reasons(imp, current_etag):
            if r not in reasons:
                reasons.append(r)
    first = next(iter(impacts.values()), {})
    meta = getattr(graph, "meta", None) or {}
    return {
        "all_safe": len(unsafe_nodes) == 0,
        "unsafe_nodes": unsafe_nodes,
        "impacts": impacts,
        "_proof": {
            "proven": not reasons,
            "unproven_reasons": reasons,
            "provenance": {
                "source": "in_process",
                "refs": list(refs),
                "change": change,
                "model_etag": first.get("model_etag"),
                "current_model_etag": current_etag,
                "graph_scope": first.get("graph_scope"),
                "built_at": first.get("built_at"),
                "report_side_available": first.get("report_side_available"),
                "model_side_complete": meta.get("model_side_complete"),
                "reports": list(meta.get("reports") or []),
                "other_bound_reports": list(meta.get("other_bound_reports") or []),
                "pbip_path": pbip_path,
            },
        },
    }


def _package(br: Dict[str, Any]) -> Dict[str, Any]:
    proof = br.pop("_proof", {}) or {}
    return {
        "blast_radius": br,
        "proven": bool(proof.get("proven")),
        "unproven_reasons": proof.get("unproven_reasons") or [],
        "provenance": proof.get("provenance") or {"source": "in_process"},
    }


def compute_delete_evidence(
    node_refs: List[str], *, pbip_path: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Evidence dict for deleting ``node_refs``, or None if it can't be computed.

    Returns ``{"blast_radius": {all_safe, unsafe_nodes, impacts}, "proven",
    "unproven_reasons", "provenance"}`` — the shape
    ``gating._evidence_proves_safe`` consumes. None is returned when the graph
    is unavailable or ``node_refs`` is empty (nothing to prove — never
    fabricate an ``all_safe`` for a no-op).
    """
    br = _blast_radius(node_refs, change="delete", new_name=None, pbip_path=pbip_path)
    if br is None:
        return None
    return _package(br)


def compute_rename_evidence(
    node_ref: str, new_name: str, *, pbip_path: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Evidence dict for renaming ``node_ref`` to ``new_name`` (collision check)."""
    if not node_ref or not new_name:
        return None
    br = _blast_radius([node_ref], change="rename", new_name=new_name, pbip_path=pbip_path)
    if br is None:
        return None
    return _package(br)


def evidence_is_proof(evidence: Optional[Dict[str, Any]]) -> bool:
    """True only for in-process evidence whose proof conditions all held."""
    if not isinstance(evidence, dict):
        return False
    prov = evidence.get("provenance")
    return (
        evidence.get("proven") is True
        and isinstance(prov, dict)
        and prov.get("source") == "in_process"
    )
