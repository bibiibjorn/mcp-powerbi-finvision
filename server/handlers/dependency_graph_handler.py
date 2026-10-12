"""16_Dependency_Graph handler — structured dependency graph queries.

Graphs come from ``core.graph.service.get_or_build_graph`` (model side = every
DAX-bearing object of the live model, report side = PBIR bindings / filters /
bookmarks). Every ``get_impact`` / ``batch_impact`` result carries the fields
the destructive-op gate consumes — ``unknown_nodes``, ``report_side_available``,
``model_etag``, ``built_at``, ``graph_scope`` — plus ``stale`` once the model
changed after the build.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from server.registry import ToolDefinition

logger = logging.getLogger(__name__)

# S-32: the direction words ``upstream`` / ``downstream`` read backwards in this graph (an edge
# points consumer -> dependency, ``get_upstream`` walks the INCOMING edges). The ops say what they
# return: ``get_consumers`` (= what depends on the node) and ``get_dependencies`` (= what the node
# depends on). The old names stay accepted (``server.arg_aliases`` rewrites them with a notice).
LEGACY_OP_ALIASES = {"get_upstream": "get_consumers", "get_downstream": "get_dependencies"}
DIRECTION_TO_INTERNAL = {"consumers": "upstream", "dependencies": "downstream"}

_VALID_OPS = {
    "build",
    "get_consumers",
    "get_dependencies",
    "get_upstream",
    "get_downstream",
    "find_unused",
    "find_cycles",
    "get_impact",
    # Batch blast-radius: impact for many node_refs in one call (feeds the
    # evidence-aware destructive-op gate). Returns per-ref impact + all_safe.
    "batch_impact",
    "query",
    # T1-I: single-call lineage sugar — builds (or reuses cached) graph,
    # then queries upstream / downstream / impact in one shot.
    "quick_lineage",
    # Phase 5: serialize a built graph to DOT / Mermaid / JSON (diff-able
    # artifact for PRs / the pbi-lineage skill).
    "export",
    # F-13: staged deletion plan from the transitive dead set (plans only, never deletes).
    "cleanup_plan",
}
_VALID_SCOPES = {"model", "report", "both"}


def _err(msg: str, et: str = "invalid_args", **extra) -> Dict[str, Any]:
    out = {"success": False, "error": msg, "error_type": et}
    out.update(extra)
    return out


def _need(args: Dict[str, Any], key: str):
    if not args.get(key):
        return _err(f"{key} is required")
    return None


def _flag(args: Dict[str, Any], key: str, default: bool) -> bool:
    value = args.get(key, default)
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "off", "")
    return bool(value)


def _get_graph(graph_id: str):
    from core.graph.cache import get_cache

    # J-27: ONE lookup — a TTL expiry between two get_by_id calls used to
    # crash with len(None.nodes).
    g = get_cache().get_by_id(graph_id)
    if g is None:
        return None, _err(f"graph_id {graph_id!r} not found or expired", et="invalid_args")
    return g, None


def _freshness(g) -> Dict[str, Any]:
    from core.graph.service import freshness

    return freshness(g)


def _build_summary(res: Dict[str, Any]) -> Dict[str, Any]:
    g = res["graph"]
    meta = g.meta or {}
    from core.graph.cache import get_cache

    out = {
        "success": True,
        "graph_id": res["graph_id"],
        "node_count": len(g.nodes),
        "edge_count": len(g.edges),
        "cached": res["cached"],
        "ttl_seconds": get_cache().ttl_seconds,
        "graph_scope": meta.get("graph_scope"),
        "built_at": meta.get("built_at"),
        "model_etag": meta.get("model_etag"),
        "report_side_available": g.has_report_side(),
        "model_side_available": bool(meta.get("model_side_available")),
    }
    for key in (
        "model_side_complete",
        "reports",
        "other_bound_reports",
        "dangling_ref_count",
        "model_warnings",
        "report_warnings",
    ):
        if meta.get(key) not in (None, [], {}):
            out[key] = meta[key]
    kinds: Dict[str, int] = {}
    for n in g.nodes.values():
        kinds[n.kind] = kinds.get(n.kind, 0) + 1
    out["node_kinds"] = kinds
    return out


def _get_or_build(args: Dict[str, Any]) -> Dict[str, Any]:
    scope = args.get("scope", "both")
    if scope not in _VALID_SCOPES:
        return _err(f"invalid scope {scope!r}", valid_scopes=sorted(_VALID_SCOPES))
    from core.graph.service import get_or_build_graph

    return get_or_build_graph(
        scope=scope,
        pbip_path=args.get("pbip_path"),
        use_cache=_flag(args, "cache", True),
        all_bound_reports=_flag(args, "all_bound_reports", False),
    )


def handle_dependency_graph(args: Dict[str, Any]) -> Dict[str, Any]:
    op = args.get("operation")
    op = LEGACY_OP_ALIASES.get(op, op)  # get_upstream -> get_consumers (same walk)
    if op not in _VALID_OPS:
        return _err(f"Unknown operation {op!r}", valid_operations=sorted(_VALID_OPS))

    if op == "build":
        res = _get_or_build(args)
        if not res.get("success"):
            return res
        return _build_summary(res)

    if op == "query":
        if e := _need(args, "graph_id"):
            return e
        if e := _need(args, "jsonpath"):
            return e
        from core.graph.queries import query

        g, err = _get_graph(args["graph_id"])
        if err:
            return err
        out = query(g, args["jsonpath"])
        out.setdefault("success", True)
        return out

    if op in {"get_consumers", "get_dependencies", "get_upstream", "get_downstream", "get_impact"}:
        if e := _need(args, "graph_id"):
            return e
        if e := _need(args, "node_ref"):
            return e
        from core.graph.queries import get_downstream, get_impact, get_upstream

        g, err = _get_graph(args["graph_id"])
        if err:
            return err
        depth = int(args.get("depth", -1))
        if op in ("get_consumers", "get_upstream"):
            out = get_upstream(g, args["node_ref"], depth=depth)
            out["consumers"] = list(out.get("ancestors") or [])  # S-32: the key says what it holds
        elif op in ("get_dependencies", "get_downstream"):
            out = get_downstream(g, args["node_ref"], depth=depth)
            out["dependencies"] = list(out.get("descendants") or [])
        else:
            out = get_impact(
                g,
                args["node_ref"],
                change=args.get("change", "delete"),
                new_name=args.get("new_name"),
                cascade=_flag(args, "cascade", False),
            )
        out.update({k: v for k, v in _freshness(g).items() if k not in out or k == "stale"})
        out["success"] = True
        return out

    if op == "batch_impact":
        if e := _need(args, "graph_id"):
            return e
        node_refs = args.get("node_refs")
        if not node_refs or not isinstance(node_refs, list):
            return _err("node_refs (non-empty array) is required")
        from core.graph.queries import get_impact, impact_meta

        g, err = _get_graph(args["graph_id"])
        if err:
            return err
        change = args.get("change", "delete")
        impacts: Dict[str, Any] = {}
        unsafe_nodes = []
        unknown = []
        for ref in node_refs:
            imp = get_impact(
                g,
                ref,
                change=change,
                new_name=args.get("new_name"),
                cascade=_flag(args, "cascade", False),
            )
            impacts[ref] = imp
            unknown.extend(imp.get("unknown_nodes") or [])
            if not imp.get("safe", False):
                unsafe_nodes.append(ref)
        out = {
            "success": True,
            "change": change,
            "impacts": impacts,
            "unsafe_nodes": unsafe_nodes,
            # J-04: an unknown node is never safe, so it can never make all_safe true.
            "all_safe": len(unsafe_nodes) == 0 and not unknown,
            **impact_meta(g, unknown),
        }
        out.update({k: v for k, v in _freshness(g).items() if k == "stale" or k not in out})
        warnings = sorted({i["warning"] for i in impacts.values() if i.get("warning")})
        if warnings:
            out["warnings"] = warnings
        return out

    if op == "find_unused":
        if e := _need(args, "graph_id"):
            return e
        from core.graph.queries import find_unused

        g, err = _get_graph(args["graph_id"])
        if err:
            return err
        kinds = args.get("kinds") or ["measure", "column"]
        model_kinds = {"measure", "column", "table", "calc_group_item", "hierarchy", "function"}
        if (
            not g.has_report_side()
            and set(kinds) & model_kinds
            and not _flag(args, "allow_model_only", False)
        ):
            # J-18: without the report side every visual-only measure/column
            # would be listed as unused — the exact false positive that gets
            # objects deleted.
            return _err(
                "find_unused needs the report side: this graph has no visuals / filters / "
                "bookmarks, so objects used only in reports would be reported unused. "
                "Build with scope='both' and a pbip_path, or pass allow_model_only=true to "
                "accept model-only results.",
                et="report_side_unavailable",
                **_freshness(g),
            )
        out = find_unused(g, kinds=kinds, transitive=_flag(args, "transitive", False))
        out.update({k: v for k, v in _freshness(g).items() if k == "stale" or k not in out})
        out["success"] = True
        return out

    if op == "cleanup_plan":
        if e := _need(args, "graph_id"):
            return e
        from core.graph.cleanup import cleanup_plan

        g, err = _get_graph(args["graph_id"])
        if err:
            return err
        if not g.has_report_side():
            # Evidence fails closed: without visuals / filters / bookmarks every report-only
            # object would be planned for deletion (the J-18 false positive).
            return _err(
                "cleanup_plan needs the report side: build with scope='both' and a pbip_path "
                "(all_bound_reports=true to count every report bound to the model).",
                et="report_side_unavailable",
                **_freshness(g),
            )
        try:
            max_stages = int(args.get("max_stages") or 10)
        except (TypeError, ValueError):
            return _err("max_stages must be an integer")
        out = cleanup_plan(g, kinds=args.get("kinds") or None, max_stages=max(1, max_stages))
        out.update({k: v for k, v in _freshness(g).items() if k == "stale" or k not in out})
        out["success"] = True
        if args.get("full") is not True:
            _compact_cleanup_evidence(out)
        return out

    if op == "find_cycles":
        if e := _need(args, "graph_id"):
            return e
        from core.graph.queries import find_cycles

        g, err = _get_graph(args["graph_id"])
        if err:
            return err
        out = find_cycles(g)
        out.update(_freshness(g))
        out["success"] = True
        return out

    if op == "quick_lineage":
        # T1-I: one-shot lineage. Equivalent to:
        #   1. build(scope, pbip_path?)  (uses cache when fresh)
        #   2. get_upstream / get_downstream / get_impact  (per direction)
        if e := _need(args, "node_ref"):
            return e
        node_ref = args["node_ref"]
        direction = args.get("direction", "both")  # consumers|dependencies|both|impact
        direction = DIRECTION_TO_INTERNAL.get(direction, direction)  # S-32 spellings
        if direction not in {"upstream", "downstream", "both", "impact"}:
            return _err(
                f"invalid direction {direction!r}",
                valid_directions=["consumers", "dependencies", "both", "impact"],
            )
        res = _get_or_build(args)
        if not res.get("success"):
            return res
        graph = res["graph"]

        from core.graph.queries import get_downstream, get_impact, get_upstream

        depth = int(args.get("depth", -1))
        result: Dict[str, Any] = {
            "success": True,
            "graph_id": res["graph_id"],
            "cached": res["cached"],
            "node_ref": node_ref,
            "direction": direction,
            "node_count": len(graph.nodes),
            "edge_count": len(graph.edges),
        }
        result.update(_freshness(graph))
        result["report_side_available"] = graph.has_report_side()
        if direction in {"upstream", "both"}:
            result["upstream"] = get_upstream(graph, node_ref, depth=depth)
        if direction in {"downstream", "both"}:
            result["downstream"] = get_downstream(graph, node_ref, depth=depth)
        if direction == "impact":
            result["impact"] = get_impact(
                graph,
                node_ref,
                change=args.get("change", "delete"),
                new_name=args.get("new_name"),
                cascade=_flag(args, "cascade", False),
            )
        return result

    if op == "export":
        if e := _need(args, "graph_id"):
            return e
        g, err = _get_graph(args["graph_id"])
        if err:
            return err
        from core.graph.export import export_graph

        fmt = args.get("output_format") or args.get("format") or "json"
        try:
            content = export_graph(g, fmt)
        except ValueError as ve:
            return _err(str(ve))

        result = {
            "success": True,
            "format": fmt.lower(),
            "node_count": len(g.nodes),
            "edge_count": len(g.edges),
        }
        # Optionally spill to disk (large graphs); else return inline.
        output_path = args.get("output_path")
        if output_path:
            # J-28: route through the path guard like every other writer.
            from core.validation.path_guard import guard_or_error

            safe, gerr = guard_or_error(
                output_path, "output_path", extra_roots=[p for p in [args.get("pbip_path")] if p]
            )
            if gerr:
                return gerr
            try:
                with open(safe, "w", encoding="utf-8") as fh:
                    fh.write(content)
                result["output_path"] = str(safe)
            except OSError as oe:
                return _err(f"could not write export to {output_path}: {oe}", et="io_error")
        else:
            result["content"] = content
        return result

    return _err(f"Unhandled op {op!r}")


def register_dependency_graph_handler(registry):
    registry.register(
        ToolDefinition(
            name="16_Dependency_Graph",
            description=(
                "Queryable dependency graph of the model (every DAX object, relationships, "
                "sort-by, hierarchies, RLS, UDFs, field params) + report (PBIR bindings, "
                "filters, bookmarks): build, get_consumers, get_dependencies, find_unused, "
                "find_cycles, get_impact, batch_impact, query (JSONPath), quick_lineage "
                "(build + query), export (json|dot|mermaid), cleanup_plan (plans only). node_ref: 'T[M]', '[M]' or 'T'; "
                "an unknown node is never safe."
            ),
            guide_notes=(
                "Queryable dependency graph: model (every DAX object, relationships, sort-by, "
                "hierarchies, RLS, UDFs, field params) + report (PBIR bindings, filters, "
                "bookmarks). build -> get_consumers/get_dependencies/find_unused/find_cycles/"
                "get_impact/batch_impact/query; get_consumers = what DEPENDS ON the node (the old "
                "get_upstream), get_dependencies = what the node DEPENDS ON (the old get_downstream); "
                "quick_lineage direction = consumers|dependencies|both|impact (old upstream/downstream "
                "still accepted, with a notice); quick_lineage = build+query; export = "
                "json|dot|mermaid; find_unused transitive=true -> fixed point 'used only by unused' (+ "
                "hidden never-revealed visuals); cleanup_plan -> staged deletion plan (apply_changeset "
                "column deletes, measure batches, remove_manually, prerequisite visuals, per-stage "
                "evidence, plan_hash; never deletes). node_ref: id or bare 'T[M]' / '[M]' / 'T', "
                "case-insensitive; unknown = never safe. Impact results carry unknown_nodes, "
                "report_side_available, model_etag, built_at, graph_scope, stale."
            ),
            handler=handle_dependency_graph,
            input_schema={
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": sorted(_VALID_OPS - set(LEGACY_OP_ALIASES)),
                    },
                    "scope": {
                        "type": "string",
                        "enum": sorted(_VALID_SCOPES),
                        "default": "both",
                    },
                    "pbip_path": {"type": "string"},
                    "all_bound_reports": {
                        "type": "boolean",
                        "description": "Also scan other reports bound to the model.",
                    },
                    "cache": {"type": "boolean", "default": True},
                    "graph_id": {"type": "string"},
                    "node_ref": {"type": "string"},
                    "node_refs": {"type": "array", "items": {"type": "string"}},
                    "new_name": {"type": "string"},
                    "cascade": {
                        "type": "boolean",
                        "description": "rename: the op rewrites every consumer.",
                    },
                    "allow_model_only": {
                        "type": "boolean",
                        "description": "find_unused without a report side.",
                    },
                    "depth": {"type": "integer", "default": -1},
                    "direction": {
                        "type": "string",
                        "enum": ["consumers", "dependencies", "both", "impact"],
                    },
                    "kinds": {"type": "array", "items": {"type": "string"}},
                    "transitive": {
                        "type": "boolean",
                        "description": "find_unused: 'used only by unused' fixed point",
                    },
                    "max_stages": {
                        "type": "integer",
                        "description": "cleanup_plan: stages returned (default 10)",
                    },
                    "change": {"type": "string", "enum": ["delete", "rename", "modify"]},
                    "jsonpath": {"type": "string"},
                    "output_format": {
                        "type": "string",
                        "enum": ["json", "dot", "mermaid"],
                        "default": "json",
                    },
                    "output_path": {"type": "string"},
                },
                "required": ["operation"],
            },
            category="analysis",
            sort_order=1600,
            # WP2 (A-10/L-08): read-only graph queries.
            annotations={
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        )
    )


def _compact_cleanup_evidence(plan: Dict[str, Any]) -> None:
    """cleanup_plan default output: per-node impact records repeat the same graph metadata
    (R0201: 69 KB of evidence in stage 1 alone), so only the nodes that are NOT safe keep
    their full record; safe nodes are counted. full=true keeps every record."""
    for stage in plan.get("stages") or []:
        ev = stage.get("evidence")
        if not isinstance(ev, dict) or not isinstance(ev.get("impacts"), dict):
            continue
        impacts = ev["impacts"]
        unsafe = set(ev.get("unsafe_nodes") or [])
        ev["safe_count"] = sum(1 for n, imp in impacts.items() if n not in unsafe)
        ev["impacts"] = {n: imp for n, imp in impacts.items() if n in unsafe}
        ev["impacts_compacted"] = True
    plan["full_hint"] = "pass full=true for the per-node impact record of every staged node"
