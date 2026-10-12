"""Staged cleanup plan from the transitive dead set (F-13). PLANS ONLY -- never deletes.

``cleanup_plan`` turns :func:`core.graph.queries.transitive_dead_set` into an ordered series
of deletion stages. A stage deletes objects whose only consumers are removed by earlier
stages (``pbi-lineage``: "a sort key whose only consumer is a dead column is flagged only once
that column is gone, so re-run find_unused" -- the agent no longer loops by hand). Every stage
carries:

* the documents to run: a ``03_Batch_Operations apply_changeset`` delete document for columns
  (the changeset engine supports tables / columns / relationships only), a
  ``03_Batch_Operations`` measures delete batch, and ``02_Model_Operations`` targets for
  calculation items / UDFs / hierarchies;
* ``batch_impact``-style evidence computed AS IF the earlier stages were already applied
  (consumers removed by an earlier stage are not "breaks"); a node that is not provably safe
  is reported with its remaining breaks;
* ``remove_manually``: dead objects no model delete reaches (calculated-table columns,
  field-parameter / calculation-group table columns, report-level extension measures).

``plan_hash`` fingerprints the plan itself (model etag + every stage's refs): re-run the plan
and compare before acting. It is NOT the TOM fingerprint ``apply_changeset`` demands -- run
``03_Batch_Operations apply_changeset preview=true`` with a stage's ``changeset`` to get that.
Hidden visuals that are never revealed by a bookmark are listed as ``prerequisite_visuals``:
a visual binding a deleted field makes Desktop reject the report, so they must be removed
(11_PBIP_Authoring delete_visual, your decision) BEFORE the stage that deletes what they bind.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Set

from core.graph.models import Graph
from core.graph.queries import (
    CONSUMER_EDGE_KINDS,
    TRANSITIVE_KINDS,
    get_impact,
    impact_meta,
    transitive_dead_set,
)

DEFAULT_KINDS = ["measure", "column"]
MAX_STAGES_DEFAULT = 10


def _split(node_id: str) -> Dict[str, str]:
    kind, _, rest = node_id.partition(":")
    if "[" in rest and rest.endswith("]"):
        table, name = rest[:-1].split("[", 1)
        return {"kind": kind, "table": table, "name": name}
    return {"kind": kind, "table": "", "name": rest}


def manual_reason(graph: Graph, node_id: str) -> Optional[str]:
    """Why a dead node cannot be removed by a model delete (None = removable automatically)."""
    node = graph.nodes[node_id]
    parts = _split(node_id)
    if node.kind == "measure" and node.attrs.get("report_extension"):
        return (
            "report-level (extension) measure: 07_Report_Operations operation=manage_extension_"
            f"measures sub_operation=delete measure_name={parts['name']!r}"
        )
    if node.kind == "column":
        if node.attrs.get("type") == "calculated_table_column":
            return "column of a calculated table: change the table expression, not the column"
        tinfo = graph.nodes.get(f"table:{parts['table']}")
        if tinfo is not None and tinfo.attrs.get("field_parameter"):
            return "column of a field-parameter table: edit the field list (02_Model_Operations field_parameter)"
        if tinfo is not None and tinfo.attrs.get("calc_group"):
            return "column of a calculation-group table: it is part of the group, not a deletable column"
    return None


def _impact_after(
    graph: Graph, node_ref: str, removed: Set[str], dead_visuals_: Dict[str, Any]
) -> Dict[str, Any]:
    """``get_impact(delete)`` as if every node in *removed* were already gone.

    The upstream walk does not pass through a removed node or through a hidden-never-revealed
    visual (those are removed first -- reported as ``prerequisite_visuals`` -- and take the
    bookmarks that captured them along).
    """
    imp = dict(get_impact(graph, node_ref, change="delete"))
    nid = imp.get("node")
    if nid not in graph.nodes:
        return imp  # unknown / ambiguous: safe=False, unknown_nodes set by get_impact
    seen = {nid}
    stack = [nid]
    breaks: Set[str] = set()
    pre: Set[str] = set()
    while stack:
        cur = stack.pop()
        for e in graph.incoming(cur):
            src = e.src
            if src in seen:
                continue
            seen.add(src)
            if src in removed:
                continue
            if src in dead_visuals_:
                pre.add(src)
                continue
            breaks.add(src)
            stack.append(src)
    imp["breaks"] = sorted(breaks)
    imp["rebinds_needed"] = []
    imp["safe"] = not breaks and not imp.get("unknown_nodes")
    if pre:
        imp["prerequisite_visuals"] = sorted(pre)
    return imp


def _documents(graph: Graph, nodes: List[str]) -> Dict[str, Any]:
    changeset_cols: List[Dict[str, str]] = []
    measures: List[Dict[str, str]] = []
    other: List[Dict[str, Any]] = []
    for nid in nodes:
        p = _split(nid)
        if p["kind"] == "column":
            changeset_cols.append({"table": p["table"], "name": p["name"]})
        elif p["kind"] == "measure":
            measures.append({"table": p["table"], "name": p["name"]})
        elif p["kind"] == "calc_group_item":
            other.append(
                {
                    "ref": nid,
                    "tool": "02_Model_Operations",
                    "object_type": "calculation_group",
                    "operation": "delete_item",
                    "group": p["table"],
                    "item": p["name"],
                }
            )
        elif p["kind"] == "function":
            other.append(
                {
                    "ref": nid,
                    "tool": "02_Model_Operations",
                    "object_type": "function",
                    "operation": "delete",
                    "function": p["name"],
                }
            )
        elif p["kind"] == "hierarchy":
            other.append(
                {
                    "ref": nid,
                    "tool": "02_Model_Operations",
                    "object_type": "hierarchy",
                    "operation": "delete",
                    "table": p["table"],
                    "hierarchy": p["name"],
                }
            )
    docs: Dict[str, Any] = {}
    if changeset_cols:
        docs["changeset"] = {"columns": {"delete": changeset_cols}}
        docs["apply_changeset_args"] = {
            "tool": "03_Batch_Operations",
            "operation": "apply_changeset",
            "preview": True,
            "changeset": docs["changeset"],
            "note": "preview first: it returns the TOM plan_hash that apply requires",
        }
    if measures:
        docs["measure_batch"] = {
            "tool": "03_Batch_Operations",
            "object_type": "measures",
            "batch_operation": "delete",
            "items": measures,
            "note": "measures are not supported by apply_changeset; >20 items need confirm_destructive",
        }
    if other:
        docs["other_deletes"] = other
    return docs


def cleanup_plan(
    graph: Graph,
    kinds: Optional[List[str]] = None,
    *,
    max_stages: int = MAX_STAGES_DEFAULT,
) -> Dict[str, Any]:
    """Staged deletion plan for the dead set of *graph* (needs the report side; see module doc)."""
    kinds = [k for k in (kinds or DEFAULT_KINDS)]
    td = transitive_dead_set(graph)
    dead: Set[str] = td["dead"]
    stage_of: Dict[str, int] = td["stage"]
    dv = td["dead_visuals"]
    selected = {nid for nid in dead if graph.nodes[nid].kind in kinds}

    # A selected node whose dead consumer is NOT selected cannot be staged: deleting it would
    # leave that (dead but present) consumer with a broken reference.
    deferred: List[Dict[str, Any]] = []
    stageable: Set[str] = set(selected)
    changed = True
    while changed:
        changed = False
        for nid in sorted(stageable):
            blockers = sorted(
                {
                    e.src
                    for e in graph.incoming(nid)
                    if e.kind in CONSUMER_EDGE_KINDS
                    and e.src in dead
                    and e.src not in stageable
                    and e.src != nid
                }
            )
            if blockers:
                stageable.discard(nid)
                deferred.append(
                    {
                        "node": nid,
                        "blocked_by": blockers[:10],
                        "reason": "a dead consumer of another kind is not selected "
                        f"(kinds={kinds}); include its kind or delete it first",
                    }
                )
                changed = True

    manual: Dict[str, str] = {}
    for nid in sorted(stageable):
        why = manual_reason(graph, nid)
        if why:
            manual[nid] = why

    by_stage: Dict[int, List[str]] = {}
    for nid in sorted(stageable):
        by_stage.setdefault(stage_of.get(nid, 1), []).append(nid)

    stages: List[Dict[str, Any]] = []
    removed: Set[str] = set()
    truncated = False
    prereq: Dict[str, Dict[str, Any]] = {}
    for idx, stage_no in enumerate(sorted(by_stage), start=1):
        if idx > max_stages:
            truncated = True
            break
        nodes = by_stage[stage_no]
        auto_nodes = [n for n in nodes if n not in manual]
        impacts: Dict[str, Any] = {}
        unsafe: List[str] = []
        unknown: List[str] = []
        for nid in nodes:
            imp = _impact_after(graph, nid, removed, dv)
            for v in imp.get("prerequisite_visuals", []):
                prereq.setdefault(v, {"visual": v, "hidden_by": dv[v]["hidden_by"], "binds": []})[
                    "binds"
                ].append(nid)
            impacts[nid] = imp
            if not imp["safe"]:
                unsafe.append(nid)
            unknown.extend(imp.get("unknown_nodes") or [])
        stage = {
            "stage": idx,
            "peel_round": stage_no,
            "nodes": nodes,
            "remove_manually": {n: manual[n] for n in nodes if n in manual},
            "documents": _documents(graph, auto_nodes),
            "evidence": {
                "change": "delete",
                "assumes_removed": sorted(removed),
                "impacts": impacts,
                "unsafe_nodes": unsafe,
                "all_safe": not unsafe and not unknown,
                **impact_meta(graph, unknown),
            },
            "requires_prerequisite_visuals": sorted(
                {v for nid in nodes for v in impacts[nid].get("prerequisite_visuals", [])}
            ),
        }
        stages.append(stage)
        removed |= set(nodes)

    plan_refs = [[s["stage"], s["nodes"]] for s in stages]
    plan_hash = hashlib.sha256(
        json.dumps(
            {
                "etag": (graph.meta or {}).get("model_etag"),
                "stages": plan_refs,
                "visuals": sorted(prereq),
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]

    warnings: List[str] = []
    meta = graph.meta or {}
    if meta.get("other_bound_reports"):
        warnings.append(
            f"{len(meta['other_bound_reports'])} other report(s) bound to this model were NOT scanned "
            "(build with all_bound_reports=true): their bindings are not counted, so this plan may "
            "delete something they use"
        )
    if meta.get("model_side_complete") is False:
        warnings.append("the model side of the graph is incomplete: do not act on this plan")
    if any(kind not in TRANSITIVE_KINDS for kind in kinds):
        warnings.append(
            "kinds outside " + ", ".join(TRANSITIVE_KINDS) + " are not computed transitively"
        )
    if truncated:
        warnings.append(f"plan truncated at max_stages={max_stages}; re-run after applying those")

    return {
        "plan_hash": plan_hash,
        "applies": False,
        "preview": True,
        "kinds": kinds,
        "stage_count": len(stages),
        "object_count": sum(len(s["nodes"]) for s in stages),
        "stages": stages,
        "prerequisite_visuals": sorted(prereq.values(), key=lambda v: v["visual"]),
        "remove_manually": manual,
        "deferred": deferred,
        "unused_cycles": td["cycle"][:50],
        "truncated": truncated,
        "warnings": warnings,
        "how_to_apply": [
            "1. Review prerequisite_visuals (hidden, never revealed by a bookmark) and remove them "
            "yourself (11_PBIP_Authoring delete_visual) -- the plan never touches the report.",
            "2. For each stage in order: run its documents (apply_changeset preview=true -> plan_hash "
            "-> apply; measure_batch; other_deletes), then re-build the graph.",
            "3. Compare plan_hash after a re-run; a changed hash means the model or report moved.",
        ],
        **impact_meta(graph),
    }


__all__ = ["cleanup_plan", "manual_reason"]
