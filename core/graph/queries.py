"""Canned dependency-graph queries + JSONPath escape hatch.

All traversals use the graph's adjacency indexes (J-11): O(V+E).

Every ``get_impact`` result carries the freshness / completeness fields the
destructive-op gate consumes (``impact_meta``): ``unknown_nodes``,
``report_side_available``, ``model_etag``, ``built_at``, ``graph_scope``.
An unknown node is NEVER safe (J-04): ``safe: False`` + listed in
``unknown_nodes``. Bare ``Table[Name]`` / ``'Table'[Name]`` / ``[Name]`` /
``Table`` refs and case differences resolve to the canonical node id.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from core.graph.models import MODEL_NODE_KINDS, Graph

_QUERY_RESULT_CAP = 500
_CYCLE_CAP = 500

#: Edge kinds that make the destination "used" (everything but containment).
CONSUMER_EDGE_KINDS = frozenset(
    {
        "references",
        "binds",
        "filters",
        "sort_by",
        "relates",
        "hierarchy_level",
        "calls",
        "navigates",
        "captures",
        "captured_by",
    }
)
#: Edge kinds a dependency cycle can run through.
_CYCLE_EDGE_KINDS = frozenset({"references", "calls"})

_ID_KINDS = (
    "measure",
    "column",
    "table",
    "calc_group_item",
    "rls_role",
    "hierarchy",
    "function",
    "relationship",
    "report",
    "page",
    "visual",
    "bookmark",
    "slicer",
    "theme_ref",
)


# ---------------------------------------------------------------------------
# Node-ref resolution (J-04)
# ---------------------------------------------------------------------------
def resolve_node_ref(graph: Graph, node_ref: str) -> Tuple[Optional[str], List[str]]:
    """``(node_id, candidates)``: the canonical id of *node_ref*, or None + the
    ambiguous candidates (empty when nothing matched).

    Accepts a node id (``measure:Sales[Total]``, case-insensitive), a bare
    ``Sales[Total]`` / ``'Sales'[Total]`` (measure, column, calc item or
    hierarchy of that table), ``[Total]`` (the model-wide unique measure) or a
    bare table name.
    """
    ref = (node_ref or "").strip()
    if not ref:
        return None, []
    hits = graph.find_ci(ref)
    if len(hits) == 1:
        return hits[0], []
    if len(hits) > 1:
        return None, hits
    kind, sep, rest = ref.partition(":")
    if sep and kind in _ID_KINDS:
        # A typed id whose table part may carry DAX quoting: measure:'T'[x].
        if "[" in rest and rest.endswith("]"):
            table, name = _split(rest)
            hits = graph.find_ci(f"{kind}:{table}[{name}]")
            if len(hits) == 1:
                return hits[0], []
        return None, hits
    if "[" in ref and ref.endswith("]"):
        table, name = _split(ref)
        if table:
            cands: List[str] = []
            for k in ("measure", "column", "calc_group_item", "hierarchy"):
                cands.extend(graph.find_ci(f"{k}:{table}[{name}]"))
            if len(cands) == 1:
                return cands[0], []
            return None, cands
        # [Name] -> the measure with that name (measure names are model-wide unique)
        cf = name.casefold()
        cands = [
            nid
            for nid, n in graph.nodes.items()
            if n.kind == "measure" and n.scoped_name.rsplit("[", 1)[-1][:-1].casefold() == cf
        ]
        if len(cands) == 1:
            return cands[0], []
        return None, cands
    hits = graph.find_ci(f"table:{_unquote(ref)}")
    if len(hits) == 1:
        return hits[0], []
    return None, hits


def _unquote(t: str) -> str:
    t = t.strip()
    if len(t) >= 2 and t[0] == "'" and t[-1] == "'":
        return t[1:-1].replace("''", "'")
    return t


def _split(ref: str) -> Tuple[str, str]:
    try:
        from core.dax.dax_names import split_qualified

        table, name = split_qualified(ref)
        return table or "", name or ""
    except Exception:  # noqa: BLE001
        i = ref.index("[")
        return _unquote(ref[:i]), ref[i + 1 : -1].replace("]]", "]")


# ---------------------------------------------------------------------------
# Result metadata
# ---------------------------------------------------------------------------
def impact_meta(graph: Graph, unknown: Optional[List[str]] = None) -> Dict[str, Any]:
    """The fields every impact result carries (consumed by the WP5 gate)."""
    meta = graph.meta or {}
    return {
        "unknown_nodes": list(unknown or []),
        "report_side_available": graph.has_report_side(),
        "model_etag": meta.get("model_etag"),
        "built_at": meta.get("built_at"),
        "graph_scope": meta.get("graph_scope") or "unknown",
    }


def _completeness_warning(graph: Graph, node_id: str) -> Optional[str]:
    node = graph.nodes.get(node_id)
    if node is None or node.kind not in MODEL_NODE_KINDS:
        return None
    if not graph.has_report_side():
        return (
            "report side unavailable: visual / filter / bookmark consumers are NOT in this "
            "graph, so an empty break list does not prove the object is unused (build with "
            "scope='both' and a pbip_path)."
        )
    others = (graph.meta or {}).get("other_bound_reports")
    if others:
        return (
            f"{len(others)} other report(s) bound to this model were not scanned "
            "(build with all_bound_reports=true to include them)."
        )
    return None


# ---------------------------------------------------------------------------
# Traversals
# ---------------------------------------------------------------------------
def _bfs(graph: Graph, start: str, direction: str, depth: int) -> Dict[int, List[str]]:
    seen = {start}
    by_depth: Dict[int, List[str]] = {}
    frontier = [start]
    d = 0
    while frontier and (depth == -1 or d < depth):
        d += 1
        next_frontier = []
        for nid in frontier:
            edges = graph.outgoing(nid) if direction == "out" else graph.incoming(nid)
            for e in edges:
                other = e.dst if direction == "out" else e.src
                if other in seen:
                    continue
                seen.add(other)
                next_frontier.append(other)
        if next_frontier:
            by_depth[d] = sorted(next_frontier)
        frontier = next_frontier
    return by_depth


def _resolved(graph: Graph, node_ref: str) -> Tuple[str, Dict[str, Any]]:
    nid, cands = resolve_node_ref(graph, node_ref)
    extra: Dict[str, Any] = {}
    if nid is None:
        extra["note"] = "node_not_found"
        extra["unknown_nodes"] = [node_ref]
        if cands:
            extra["candidates"] = sorted(cands)[:20]
        return node_ref, extra
    if nid != node_ref:
        extra["resolved_from"] = node_ref
    return nid, extra


def get_upstream(graph: Graph, node_ref: str, depth: int = -1) -> Dict[str, Any]:
    nid, extra = _resolved(graph, node_ref)
    by_depth = _bfs(graph, nid, direction="in", depth=depth)
    return {
        "node": nid,
        "ancestors": sorted({n for layer in by_depth.values() for n in layer}),
        "by_depth": by_depth,
        **extra,
    }


def get_downstream(graph: Graph, node_ref: str, depth: int = -1) -> Dict[str, Any]:
    nid, extra = _resolved(graph, node_ref)
    by_depth = _bfs(graph, nid, direction="out", depth=depth)
    return {
        "node": nid,
        "descendants": sorted({n for layer in by_depth.values() for n in layer}),
        "by_depth": by_depth,
        **extra,
    }


#: Node kinds whose liveness is computed by the transitive mark-and-sweep (F-13). Tables are
#: excluded on purpose (a table is consumed through its columns / relationships), as are
#: relationships, RLS roles and every report node: those are live roots.
TRANSITIVE_KINDS = ("measure", "column", "calc_group_item", "function", "hierarchy")


def dead_visuals(graph: Graph) -> Dict[str, Dict[str, Any]]:
    """Hidden visuals that NO bookmark ever reveals -> ``{visual_id: {"hidden_by": id}}``.

    A visual is hidden when its own top-level ``isHidden`` is set or an ancestor group is. A
    hidden element is "revealed" when some bookmark captures it without
    ``display.mode == hidden`` (``captures`` edge ``attrs.reveals``). A visual stays dead when
    ANY hidden element of its chain (itself or an ancestor group) is not revealed. Their
    bindings and filters do not count as consumers in a transitive scan.
    """
    revealed = {
        e.dst
        for e in graph.edges.values()
        if e.kind == "captures" and (e.attrs or {}).get("reveals")
    }
    visuals = {nid: n for nid, n in graph.nodes.items() if n.kind == "visual"}
    out: Dict[str, Dict[str, Any]] = {}
    for nid in visuals:
        chain: List[str] = []
        cur: Optional[str] = nid
        seen = set()
        while cur and cur in visuals and cur not in seen:
            seen.add(cur)
            if visuals[cur].attrs.get("hidden"):
                chain.append(cur)
            cur = visuals[cur].attrs.get("parent_group")
        blockers = [h for h in chain if h not in revealed]
        if blockers:
            out[nid] = {"hidden_by": blockers[0]}
    return out


def _consumer_edges(graph: Graph, node_id: str) -> List[Any]:
    return [e for e in graph.incoming(node_id) if e.kind in CONSUMER_EDGE_KINDS]


def transitive_dead_set(graph: Graph) -> Dict[str, Any]:
    """Fixed point of "used only by unused" over :data:`TRANSITIVE_KINDS` (mark and sweep).

    Live roots = every node that is not a candidate (tables, relationships, RLS roles, pages,
    bookmarks, reports...) plus every visual that is not hidden-and-never-revealed. Liveness
    flows along consumer edges (consumer -> dependency): a dependency of a live node is live.
    A candidate that is not reached is dead, including members of dead reference cycles.

    Returns ``{dead, stage, dead_visuals, rounds, cycle}``: ``stage[id]`` is the peeling round
    (1 = no live consumer at all / only dead visuals; k+1 = every consumer is dead and removed
    by stage k); nodes only in a dead CYCLE get ``stage = rounds + 1`` and are listed in
    ``cycle``.
    """
    dv = dead_visuals(graph)
    cand = {
        nid
        for nid, n in graph.nodes.items()
        if n.kind in TRANSITIVE_KINDS
        and not (n.kind == "column" and n.attrs.get("type") == "row_number")
    }
    live: set = set()
    stack: List[str] = []
    for nid in graph.nodes:
        if nid in cand or nid in dv:
            continue
        live.add(nid)
        stack.append(nid)
    while stack:
        cur = stack.pop()
        for e in graph.outgoing(cur):
            # a bookmark capturing a hidden-never-revealed visual (hidden in the bookmark too)
            # does not make that visual live
            if e.kind not in CONSUMER_EDGE_KINDS or e.dst in live or e.dst in dv:
                continue
            live.add(e.dst)
            stack.append(e.dst)
    dead = {nid for nid in cand if nid not in live}

    stage: Dict[str, int] = {}
    remaining = set(dead)
    rnd = 0
    while remaining:
        peel = [
            nid
            for nid in remaining
            if not any(e.src in remaining for e in _consumer_edges(graph, nid) if e.src != nid)
        ]
        if not peel:
            break
        rnd += 1
        for nid in peel:
            stage[nid] = rnd
        remaining -= set(peel)
    cycle = sorted(remaining)
    for nid in cycle:
        stage[nid] = rnd + 1
    return {"dead": dead, "stage": stage, "dead_visuals": dv, "rounds": rnd, "cycle": cycle}


def _tag_not_removable(graph: Graph, node_id: str, entry: Dict[str, Any]) -> None:
    """``removable: false`` + the reason ``cleanup_plan`` gives for ``remove_manually`` when a model
    delete cannot remove the node on its own (columns of calculation-group / calculated /
    field-parameter tables, report extension measures)."""
    from core.graph.cleanup import manual_reason  # lazy: cleanup imports this module

    why = manual_reason(graph, node_id)
    if why:
        entry["removable"] = False
        entry["not_removable_reason"] = why


def find_unused(
    graph: Graph, kinds: Optional[List[str]] = None, *, transitive: bool = False
) -> Dict[str, Any]:
    """Objects with NO consumer (J-18); ``transitive=True`` = "used only by unused" (F-13).

    A node is unused when no edge other than containment (``belongs_to``)
    points at it: no measure / calc column / calc item / RLS / UDF references
    it, no visual binds or filters on it, no relationship, sort-by or
    hierarchy uses it. (The old rule also required "no outgoing refs", so any
    measure that referenced something was never unused, while a leaf bound only
    in visuals looked unused because the report side was always empty.)
    ``report_side_available`` tells whether visual consumers were counted.

    ``transitive=True`` computes the dead set to a fixed point
    (:func:`transitive_dead_set`): a measure used only by an unused measure, a sort key whose
    only consumer is a dead column, a UDF called only by dead measures... and visuals that are
    hidden and never revealed by a bookmark do not count as consumers. Each entry then carries
    ``stage`` (peeling round), ``reason`` (``no_consumers`` | ``only_hidden_visuals`` |
    ``only_used_by_unused`` | ``unused_cycle``) and ``consumers`` (the dead nodes holding it
    alive, if any); ``dead_visuals`` lists the hidden-never-revealed visuals that bind dead
    objects. Default ``transitive=False`` keeps the direct-consumer behaviour unchanged.
    """
    kinds = kinds or ["measure", "column"]
    out: List[Dict[str, Any]] = []
    extra: Dict[str, Any] = {}
    if transitive:
        td = transitive_dead_set(graph)
        dv = td["dead_visuals"]
        for nid in sorted(td["dead"], key=lambda x: (td["stage"].get(x, 0), x)):
            n = graph.nodes[nid]
            if n.kind not in kinds:
                continue
            cons = [e.src for e in _consumer_edges(graph, nid)]
            dead_cons = sorted({c for c in cons if c in td["dead"]})
            hidden_cons = sorted({c for c in cons if c in dv})
            if nid in td["cycle"]:
                reason = "unused_cycle"
            elif not cons:
                reason = "no_consumers"
            elif not dead_cons:
                reason = "only_hidden_visuals"
            else:
                reason = "only_used_by_unused"
            entry: Dict[str, Any] = {
                "node": nid,
                "reason": reason,
                "stage": td["stage"].get(nid, 1),
            }
            if dead_cons:
                entry["consumers"] = dead_cons[:20]
            if hidden_cons:
                entry["hidden_visuals"] = hidden_cons[:20]
            _tag_not_removable(graph, nid, entry)
            out.append(entry)
        bound = {
            v
            for v in dv
            if any(e.dst in td["dead"] and e.kind in CONSUMER_EDGE_KINDS for e in graph.outgoing(v))
        }
        extra = {
            "transitive": True,
            "rounds": td["rounds"],
            "dead_visuals": sorted(bound),
            "unused_cycles": td["cycle"][:50],
        }
    else:
        for n in graph.nodes.values():
            if n.kind not in kinds:
                continue
            if n.kind == "column" and n.attrs.get("type") == "row_number":
                continue
            if not _consumer_edges(graph, n.id):
                entry = {"node": n.id, "reason": "no_consumers"}
                _tag_not_removable(graph, n.id, entry)
                out.append(entry)
    res: Dict[str, Any] = {"unused": out, **impact_meta(graph), **extra}
    res.pop("unknown_nodes", None)
    if not graph.has_report_side():
        res["warning"] = (
            "report side unavailable: objects used only by visuals / filters / bookmarks "
            "are listed as unused. Build with scope='both' and a pbip_path."
        )
    elif (graph.meta or {}).get("other_bound_reports"):
        res["warning"] = (
            "other reports bound to this model were not scanned; their bindings are not "
            "counted (build with all_bound_reports=true)."
        )
        res["other_bound_reports"] = list(graph.meta["other_bound_reports"])
    return res


def find_cycles(graph: Graph) -> Dict[str, Any]:
    """Reference / UDF-call cycles, iterative DFS (no recursion limit), O(V+E)."""
    cycles: List[List[str]] = []
    state: Dict[str, int] = {}  # 1 = on stack, 2 = done
    for root in graph.nodes:
        if state.get(root):
            continue
        stack: List[Tuple[str, int]] = [(root, 0)]
        path: List[str] = [root]
        on_path = {root: 0}
        state[root] = 1
        while stack:
            nid, i = stack[-1]
            edges = [e for e in graph.outgoing(nid) if e.kind in _CYCLE_EDGE_KINDS]
            if i < len(edges):
                stack[-1] = (nid, i + 1)
                nxt = edges[i].dst
                if state.get(nxt) == 1 and nxt in on_path:
                    if len(cycles) < _CYCLE_CAP:
                        cycles.append(path[on_path[nxt] :] + [nxt])
                    continue
                if state.get(nxt):
                    continue
                state[nxt] = 1
                on_path[nxt] = len(path)
                path.append(nxt)
                stack.append((nxt, 0))
            else:
                stack.pop()
                state[nid] = 2
                on_path.pop(nid, None)
                path.pop()
    return {"cycles": cycles, "truncated": len(cycles) >= _CYCLE_CAP}


# ---------------------------------------------------------------------------
# Impact
# ---------------------------------------------------------------------------
def _collision(graph: Graph, node_id: str, new_name: str) -> Optional[str]:
    """Id of an existing object the rename would collide with (J-22), or None.

    Case-insensitive. Measures: any measure in the model, or a column of the
    measure's own table. Columns: any column or measure of the same table.
    Tables: any other table. Others: same kind, same scope.
    """
    node = graph.nodes.get(node_id)
    if node is None:
        return None
    new_cf = new_name.strip().casefold()
    if node.kind in ("measure", "column", "calc_group_item", "hierarchy"):
        table = node.scoped_name.rsplit("[", 1)[0]
        for nid, n in graph.nodes.items():
            if nid == node_id or n.kind not in (
                "measure",
                "column",
                "calc_group_item",
                "hierarchy",
            ):
                continue
            ntable, nname = n.scoped_name.rsplit("[", 1)
            if nname[:-1].casefold() != new_cf:
                continue
            same_table = ntable.casefold() == table.casefold()
            if node.kind == "measure" and (n.kind == "measure" or same_table):
                return nid
            if node.kind == "column" and same_table and n.kind in ("column", "measure"):
                return nid
            if node.kind in ("calc_group_item", "hierarchy") and same_table and n.kind == node.kind:
                return nid
        return None
    for nid, n in graph.nodes.items():
        if nid != node_id and n.kind == node.kind and n.scoped_name.casefold() == new_cf:
            return nid
    return None


def get_impact(
    graph: Graph,
    node_ref: str,
    change: str = "delete",
    new_name: Optional[str] = None,
    *,
    cascade: bool = False,
) -> Dict[str, Any]:
    """Blast radius of deleting / renaming / modifying *node_ref*.

    * unknown node -> ``safe: False`` + ``unknown_nodes`` (J-04);
    * rename: case-insensitive collision check (J-22); ``safe`` is False while
      ``rebinds_needed`` is non-empty unless the caller's op cascades the rename
      into every consumer (``cascade=True``);
    * every result carries ``impact_meta`` (+ ``warning`` when the report side
      is missing / incomplete).
    """
    nid, cands = resolve_node_ref(graph, node_ref)
    if nid is None:
        out = {
            "node": node_ref,
            "breaks": [],
            "rebinds_needed": [],
            "safe": False,
            "note": "node_not_found",
            **impact_meta(graph, [node_ref]),
        }
        if cands:
            out["candidates"] = sorted(cands)[:20]
            out["note"] = "node_ambiguous"
        return out
    upstream = get_upstream(graph, nid, depth=-1)["ancestors"]
    rebinds = [e.src for e in graph.incoming(nid) if e.kind == "binds"]
    base: Dict[str, Any] = {"node": nid, **impact_meta(graph)}
    if nid != node_ref:
        base["resolved_from"] = node_ref
    warning = _completeness_warning(graph, nid)
    if warning:
        base["warning"] = warning
    if change == "delete":
        breaks = set(upstream + rebinds)
        # A table's OWN columns/measures are deleted WITH the table by definition,
        # so they are not "breaks". They reach the table via a belongs_to edge
        # (child -> table); subtract exactly those owned children. The BFS still
        # traverses THROUGH belongs_to edges — external consumers of a table's
        # columns are only discovered via the column -> table hop.
        node = graph.nodes.get(nid)
        if node is not None and node.kind == "table":
            owned = {e.src for e in graph.incoming(nid) if e.kind == "belongs_to"}
            breaks -= owned
        return {**base, "breaks": sorted(breaks), "rebinds_needed": [], "safe": not breaks}
    rebinds_needed = sorted(set(upstream + rebinds))
    result: Dict[str, Any] = {
        **base,
        "breaks": [],
        "rebinds_needed": rebinds_needed,
        "safe": True,
    }
    if change == "rename":
        if rebinds_needed and not cascade:
            result["safe"] = False
            result["safe_reason"] = (
                f"{len(rebinds_needed)} consumer(s) must be rebound; pass cascade=true only "
                "when the rename op rewrites them all (DAX + PBIR)."
            )
        if new_name:
            hit = _collision(graph, nid, new_name)
            if hit:
                result["collision"] = True
                result["collides_with"] = hit
                result["safe"] = False
    return result


def query(graph: Graph, jsonpath: str) -> Dict[str, Any]:
    try:
        from jsonpath_ng.ext import parse  # type: ignore
    except Exception as e:
        return {
            "success": False,
            "error": f"jsonpath-ng unavailable: {e}",
            "error_type": "invalid_args",
        }
    try:
        expr = parse(jsonpath)
    except Exception as e:
        return {"success": False, "error": str(e), "error_type": "invalid_args"}
    matches = [m.value for m in expr.find(graph.to_dict())]
    truncated = len(matches) > _QUERY_RESULT_CAP
    return {"matches": matches[:_QUERY_RESULT_CAP], "truncated": truncated}
