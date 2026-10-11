"""Build the dependency graph from the live model and the PBIR report folder.

Model side (review J-03 b/c, J-20): every DAX-bearing object of
``core.graph.model_source.ModelSnapshot`` is parsed with the repo's lexer-based
``core.dax.dax_reference_parser.scan_references`` — strings and comments are
never looked into, ``''`` / ``]]`` escapes are handled, and every name lookup is
case-insensitive (DAX names are). Relationships, sort-by columns, hierarchy
levels, RLS table permissions and UDF calls become edges too. References that
resolve to nothing are kept on the node as ``attrs["unresolved_refs"]``.

Report side: ``core.graph.report_source`` (visual bindings, visual / page /
report filters, conditional-format refs, bookmarks, extension measures).
Field references resolve to the model's canonical node ids case-insensitively;
bindings to objects the model does not have are reported in
``graph.meta["dangling_refs"]``.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from core.graph.model_source import ModelSnapshot, as_snapshot
from core.graph.models import Edge, Graph, Node
from core.utilities import progress_ticker
from core.utilities.progress_ticker import Ticker

logger = logging.getLogger(__name__)

_MAX_UNRESOLVED_PER_NODE = 20


class GraphBuildCancelled(Exception):
    """The MCP call was cancelled mid-build (F-26). A half-built graph is never usable evidence
    (the destructive-op gate trusts ``model_side_complete``), so the build is abandoned, not returned.
    """


def _cf(s: str) -> str:
    return " ".join(str(s).split()).casefold()


class ModelIndex:
    """Case-insensitive lookups over a snapshot's object names."""

    def __init__(self, snap: Optional[ModelSnapshot]) -> None:
        self.tables: Dict[str, str] = {}
        self.columns: Dict[Tuple[str, str], Tuple[str, str]] = {}
        self.measures: Dict[str, Tuple[str, str]] = {}
        self.measures_qualified: Dict[Tuple[str, str], Tuple[str, str]] = {}
        self.hierarchies: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.functions: Dict[str, str] = {}
        if snap is None:
            return
        for t in snap.tables:
            self.tables[_cf(t)] = t
        for c in snap.columns:
            self.columns[(_cf(c["table"]), _cf(c["name"]))] = (c["table"], c["name"])
            self.tables.setdefault(_cf(c["table"]), c["table"])
        for m in snap.measures:
            key = (m["table"], m["name"])
            self.measures.setdefault(_cf(m["name"]), key)
            self.measures_qualified[(_cf(m["table"]), _cf(m["name"]))] = key
            self.tables.setdefault(_cf(m["table"]), m["table"])
        for h in snap.hierarchies:
            self.hierarchies[(_cf(h["table"]), _cf(h["name"]))] = h
        for f in snap.functions:
            self.functions[_cf(f["name"])] = f["name"]

    @property
    def known_tables(self) -> Set[str]:
        return set(self.tables.keys())

    def table(self, name: str) -> Optional[str]:
        return self.tables.get(_cf(name))

    def column_id(self, table: str, name: str) -> Optional[str]:
        hit = self.columns.get((_cf(table), _cf(name)))
        return f"column:{hit[0]}[{hit[1]}]" if hit else None

    def measure_id(self, name: str, table: Optional[str] = None) -> Optional[str]:
        if table is not None:
            hit = self.measures_qualified.get((_cf(table), _cf(name)))
            if hit:
                return f"measure:{hit[0]}[{hit[1]}]"
        hit = self.measures.get(_cf(name))
        return f"measure:{hit[0]}[{hit[1]}]" if hit else None


def _udf_calls(code: List[Any], index: ModelIndex) -> List[str]:
    """Names of UDFs called in a comment-free token list (``Name (`` pairs)."""
    from core.dax.tokenizer import TokenType

    out: List[str] = []
    if not index.functions:
        return out
    for i, t in enumerate(code[:-1]):
        if (
            t.type in (TokenType.IDENTIFIER, TokenType.FUNCTION)
            and code[i + 1].type == TokenType.PAREN_OPEN
        ):
            hit = index.functions.get(_cf(t.value))
            if hit and hit not in out:
                out.append(hit)
    return out


def emit_dax_refs(
    g: Graph,
    src_id: str,
    expression: Optional[str],
    index: ModelIndex,
    *,
    home_table: Optional[str] = None,
    via: str = "expression",
    self_name: Optional[str] = None,
) -> List[str]:
    """Add ``references`` / ``calls`` edges from *src_id* for every DAX ref.

    Returns the unresolved references (``'T'[x]`` / ``[x]`` / ``'T'`` text).
    ``home_table`` resolves a bare ``[x]`` to a column of the object's own table
    (calculated columns / row context) when no measure has that name.
    """
    if not expression or not str(expression).strip():
        return []
    from core.dax.dax_reference_parser import get_lexer, scan_references

    try:
        code = get_lexer().tokenize_code(str(expression))
        refs = scan_references(str(expression), known_tables=index.known_tables or None, code=code)
    except Exception as e:  # noqa: BLE001 - a lexer failure must not kill the build
        logger.debug("scan_references failed for %s: %s", src_id, e)
        return [f"<unparsed: {e}>"]
    unresolved: List[str] = []
    attrs = {"via": via} if via != "expression" else {}
    for r in refs:
        dst: Optional[str] = None
        if r.kind == "qualified":
            dst = index.column_id(r.table, r.name) or index.measure_id(r.name, r.table)
            label = f"'{r.table}'[{r.name}]"
        elif r.kind == "bracket":
            if self_name is not None and _cf(r.name) == _cf(self_name) and home_table:
                continue
            dst = index.measure_id(r.name)
            if dst is None and home_table:
                dst = index.column_id(home_table, r.name)
            label = f"[{r.name}]"
            if dst is None:
                # Extension columns (ADDCOLUMNS "x"), row-context columns of a
                # table expression: not model objects — record, don't link.
                unresolved.append(label)
                continue
        else:  # table
            t = index.table(r.table)
            dst = f"table:{t}" if t else None
            label = f"'{r.table}'"
        if dst is None:
            unresolved.append(label)
            continue
        if dst != src_id:
            g.add_edge(Edge(src=src_id, dst=dst, kind="references", attrs=dict(attrs)))
    for fn in _udf_calls(code, index):
        g.add_edge(Edge(src=src_id, dst=f"function:{fn}", kind="calls", attrs=dict(attrs)))
    return unresolved


def _note_unresolved(g: Graph, node_id: str, items: List[str]) -> None:
    if not items or node_id not in g.nodes:
        return
    attrs = g.nodes[node_id].attrs
    bucket = attrs.setdefault("unresolved_refs", [])
    for it in items:
        if it not in bucket and len(bucket) < _MAX_UNRESOLVED_PER_NODE:
            bucket.append(it)


def build_model_graph(model: Any) -> Graph:
    """Graph of every model object + the dependencies between them."""
    g = Graph()
    snap = as_snapshot(model)
    if snap is None:
        return g
    index = ModelIndex(snap)
    # F-26: per-object progress + cancel over the expression pass (the slow part of a build).
    ticker = Ticker(
        len(snap.tables)
        + len(snap.columns)
        + len(snap.measures)
        + len(snap.calc_items)
        + len(snap.functions),
        "graph object",
    )

    def _step(name: str = "") -> None:
        if ticker.cancelled():
            raise GraphBuildCancelled(
                f"cancelled while building the model graph ({ticker.done} done)"
            )
        ticker.tick(name)

    for tname, info in snap.tables.items():
        attrs = {k: True for k in ("calculated", "field_parameter", "calc_group") if info.get(k)}
        g.add_node(Node(kind="table", scoped_name=tname, attrs=attrs))
    for c in snap.columns:
        cid = f"column:{c['table']}[{c['name']}]"
        g.add_node(
            Node(
                kind="column",
                scoped_name=f"{c['table']}[{c['name']}]",
                attrs={"type": c.get("type") or "data"},
            )
        )
        g.add_edge(Edge(src=cid, dst=f"table:{c['table']}", kind="belongs_to"))
    for m in snap.measures:
        mid = f"measure:{m['table']}[{m['name']}]"
        g.add_node(Node(kind="measure", scoped_name=f"{m['table']}[{m['name']}]"))
        g.add_edge(Edge(src=mid, dst=f"table:{m['table']}", kind="belongs_to"))
    for ci in snap.calc_items:
        iid = f"calc_group_item:{ci['table']}[{ci['name']}]"
        g.add_node(Node(kind="calc_group_item", scoped_name=f"{ci['table']}[{ci['name']}]"))
        g.add_edge(Edge(src=iid, dst=f"table:{ci['table']}", kind="belongs_to"))
    for f in snap.functions:
        g.add_node(Node(kind="function", scoped_name=f["name"]))
    for h in snap.hierarchies:
        hid = f"hierarchy:{h['table']}[{h['name']}]"
        g.add_node(Node(kind="hierarchy", scoped_name=f"{h['table']}[{h['name']}]"))
        g.add_edge(Edge(src=hid, dst=f"table:{h['table']}", kind="belongs_to"))
        for lvl in h.get("levels") or []:
            col = index.column_id(h["table"], lvl)
            if col:
                g.add_edge(Edge(src=hid, dst=col, kind="hierarchy_level"))
    for r in snap.roles:
        rid = f"rls_role:{r['name']}"
        g.add_node(Node(kind="rls_role", scoped_name=r["name"]))
        for perm in r.get("permissions") or []:
            t = index.table(perm.get("table") or "")
            if not t:
                continue
            g.add_edge(Edge(src=rid, dst=f"table:{t}", kind="filters"))
            _note_unresolved(
                g,
                rid,
                emit_dax_refs(g, rid, perm.get("filter"), index, home_table=t, via=f"rls:{t}"),
            )
    for rel in snap.relationships:
        fcol = index.column_id(rel["from_table"], rel["from_column"])
        tcol = index.column_id(rel["to_table"], rel["to_column"])
        if not fcol or not tcol:
            continue
        name = f"{fcol[len('column:'):]}->{tcol[len('column:'):]}"
        rid = f"relationship:{name}"
        g.add_node(
            Node(
                kind="relationship",
                scoped_name=name,
                attrs={"active": bool(rel.get("active", True))},
            )
        )
        g.add_edge(Edge(src=rid, dst=fcol, kind="relates", attrs={"end": "from"}))
        g.add_edge(Edge(src=rid, dst=tcol, kind="relates", attrs={"end": "to"}))

    # Expressions (after every node exists so references resolve).
    for tname, info in snap.tables.items():
        _step(tname)
        tid = f"table:{tname}"
        if info.get("expression"):
            _note_unresolved(
                g, tid, emit_dax_refs(g, tid, info["expression"], index, via="table_expression")
            )
        if info.get("detail_rows"):
            _note_unresolved(
                g, tid, emit_dax_refs(g, tid, info["detail_rows"], index, via="detail_rows")
            )
    for c in snap.columns:
        _step()
        cid = f"column:{c['table']}[{c['name']}]"
        if c.get("expression"):
            _note_unresolved(
                g,
                cid,
                emit_dax_refs(
                    g, cid, c["expression"], index, home_table=c["table"], self_name=c["name"]
                ),
            )
        if c.get("sort_by"):
            sort = index.column_id(c["table"], c["sort_by"])
            if sort and sort != cid:
                g.add_edge(Edge(src=cid, dst=sort, kind="sort_by"))
    for m in snap.measures:
        _step(m["name"])
        mid = f"measure:{m['table']}[{m['name']}]"
        un = emit_dax_refs(g, mid, m.get("expression"), index, home_table=None)
        un += emit_dax_refs(g, mid, m.get("format_string_expr"), index, via="format_string")
        un += emit_dax_refs(g, mid, m.get("detail_rows_expr"), index, via="detail_rows")
        _note_unresolved(g, mid, un)
    for ci in snap.calc_items:
        _step()
        iid = f"calc_group_item:{ci['table']}[{ci['name']}]"
        un = emit_dax_refs(g, iid, ci.get("expression"), index)
        un += emit_dax_refs(g, iid, ci.get("format_string_expr"), index, via="format_string")
        _note_unresolved(g, iid, un)
    for f in snap.functions:
        _step(f["name"])
        fid = f"function:{f['name']}"
        _note_unresolved(g, fid, emit_dax_refs(g, fid, f.get("expression"), index))

    g.meta.update(
        {
            "model_side_available": True,
            "model_side_complete": bool(snap.complete),
            "model_warnings": list(snap.warnings),
        }
    )
    return g


def _resolve_field(
    ref: Tuple[str, str, str], index: Optional[ModelIndex], level: Optional[str]
) -> List[str]:
    kind, table, name = ref
    if index is None or not index.tables:
        return [f"{kind}:{table}[{name}]"]
    if kind == "measure":
        hit = index.measure_id(name, table)
        return [hit] if hit else [f"measure:{table}[{name}]"]
    if kind == "column":
        hit = index.column_id(table, name)
        if hit:
            return [hit]
        m = index.measure_id(name, table)
        return [m] if m else [f"column:{table}[{name}]"]
    # hierarchy (+ level -> column)
    h = index.hierarchies.get((_cf(table), _cf(name)))
    if not h:
        return [f"hierarchy:{table}[{name}]"]
    out = [f"hierarchy:{h['table']}[{h['name']}]"]
    if level:
        col = (h.get("level_names") or {}).get(level)
        if col is None:
            for ln, cn in (h.get("level_names") or {}).items():
                if _cf(ln) == _cf(level):
                    col = cn
        if col:
            cid = index.column_id(h["table"], col)
            if cid:
                out.append(cid)
    return out


def build_report_graph(
    pbip_path: Optional[str],
    *,
    index: Optional[ModelIndex] = None,
    include_bound_reports: bool = False,
) -> Graph:
    """Graph of the report(s) *pbip_path* resolves to (visuals / pages / bookmarks)."""
    g = Graph()
    g.meta.update({"report_side_available": False, "reports": [], "report_warnings": []})
    if not pbip_path or not os.path.exists(pbip_path):
        if pbip_path:
            g.meta["report_warnings"].append(f"path does not exist: {pbip_path}")
        return g
    try:
        from core.pbip.pbir_model import find_reports_bound_to_model, resolve_pbip
        from core.graph.report_source import scan_reports

        res = resolve_pbip(pbip_path)
        reports = list(res.get("reports") or [])
        scanned = {os.path.normcase(str(r["report_dir"])) for r in reports}
        others: List[str] = []
        for m in res.get("models") or []:
            try:
                bound = find_reports_bound_to_model(m["model_dir"])
            except Exception:  # noqa: BLE001
                bound = []
            for b in bound:
                if os.path.normcase(str(b)) not in scanned:
                    others.append(str(b))
        if include_bound_reports and others:
            for b in others:
                reports.extend(resolve_pbip(b).get("reports") or [])
            others = []
        scan = scan_reports(reports)
    except Exception as e:  # noqa: BLE001 - reported, never swallowed silently
        logger.warning("report graph build failed for %s: %s", pbip_path, e)
        g.meta["report_warnings"].append(f"report scan failed: {e}")
        return g

    for rep in scan.reports:
        g.add_node(Node(kind="report", scoped_name=rep["name"]))
    for pg in scan.pages:
        g.add_node(
            Node(
                kind="page",
                scoped_name=pg["id"][len("page:") :],
                attrs={"display_name": pg.get("display_name")},
            )
        )
    for v in scan.visuals:
        v_attrs: Dict[str, Any] = {"visual_type": v.get("visual_type")}
        if v.get("hidden"):
            v_attrs["hidden"] = True  # top-level isHidden (F-13: dead unless a bookmark reveals it)
        if v.get("parent_group"):
            v_attrs["parent_group"] = f"visual:{v['report']}/{v['page_id']}/{v['parent_group']}"
        g.add_node(Node(kind="visual", scoped_name=v["id"][len("visual:") :], attrs=v_attrs))
    for b in scan.bookmarks:
        attrs = {"group": True} if b.get("group") else {"display_name": b.get("display_name")}
        g.add_node(Node(kind="bookmark", scoped_name=b["id"][len("bookmark:") :], attrs=attrs))
    for ext in scan.extension_measures:
        sid = f"measure:{ext['table']}[{ext['name']}]"
        if index is not None and index.measure_id(ext["name"], ext["table"]):
            continue  # a model measure of the same name wins
        g.add_node(
            Node(
                kind="measure",
                scoped_name=f"{ext['table']}[{ext['name']}]",
                attrs={"report_extension": True, "report": ext["report"]},
            )
        )
        if index is not None:
            _note_unresolved(g, sid, emit_dax_refs(g, sid, ext["expression"], index))
    for src, dst, kind in scan.plain_edges:
        if dst in g.nodes or kind in ("belongs_to",):
            edge_attrs: Dict[str, Any] = {}
            if kind == "captures" and (src, dst) in scan.capture_reveals:
                edge_attrs["reveals"] = scan.capture_reveals[(src, dst)]
            g.add_edge(Edge(src=src, dst=dst, kind=kind, attrs=edge_attrs))
        elif kind == "navigates":
            g.add_edge(Edge(src=src, dst=dst, kind=kind, attrs={"dangling": True}))
    dangling: Dict[str, int] = {}
    for src, kind, ref, level, via in scan.field_edges:
        for dst in _resolve_field(ref, index, level):
            ext_hit = dst in g.nodes and g.nodes[dst].attrs.get("report_extension")
            if index is not None and index.tables and not ext_hit and not _is_model_id(index, dst):
                dangling[dst] = dangling.get(dst, 0) + 1
            g.add_edge(Edge(src=src, dst=dst, kind=kind, attrs={"via": via}))
    g.meta.update(
        {
            "report_side_available": bool(scan.reports) and bool(scan.pages),
            "reports": [r["name"] for r in scan.reports],
            "other_bound_reports": others,
            "report_warnings": scan.warnings,
            "definition_dirs": [r["definition_dir"] for r in scan.reports],
            "dangling_refs": dict(sorted(dangling.items())[:200]),
            "dangling_ref_count": len(dangling),
        }
    )
    return g


def _is_model_id(index: ModelIndex, dst: str) -> bool:
    """Is *dst* the id of an object the model actually has?"""
    kind, _, rest = dst.partition(":")
    if "[" not in rest:
        return False
    t, n = rest[:-1].split("[", 1)
    if kind == "measure":
        return index.measure_id(n, t) == dst
    if kind == "column":
        return index.column_id(t, n) == dst
    if kind == "hierarchy":
        return (_cf(t), _cf(n)) in index.hierarchies
    return False


def build_graph(
    model: Any,
    pbip_path: Optional[str],
    scope: str = "both",
    *,
    include_bound_reports: bool = False,
) -> Graph:
    """Compose a graph for the requested scope ('model'|'report'|'both').

    ``graph.meta`` carries ``graph_scope``, ``built_at`` (ISO, UTC),
    ``model_etag`` (None without a model), ``report_side_available``,
    ``model_side_available`` and the warnings of both sides.
    """
    g = Graph()
    snap = as_snapshot(model) if scope in {"model", "both"} else None
    index = ModelIndex(snap) if snap is not None else None
    if snap is not None:
        mg = build_model_graph(snap)
        g.nodes.update(mg.nodes)
        g.edges.update(mg.edges)
        g.meta.update(mg.meta)
    if scope in {"report", "both"} and pbip_path:
        if progress_ticker.is_cancelled():
            raise GraphBuildCancelled("cancelled before the report scan")
        progress_ticker.emit(0, 1, "scanning the PBIR report")
        rg = build_report_graph(pbip_path, index=index, include_bound_reports=include_bound_reports)
        g.nodes.update(rg.nodes)
        g.edges.update(rg.edges)
        g.meta.update(rg.meta)
    g.meta.setdefault("model_side_available", snap is not None)
    g.meta.setdefault("report_side_available", False)
    g.meta.update(
        {
            "graph_scope": scope,
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "model_etag": snap.etag() if snap is not None else None,
            "pbip_path": pbip_path,
        }
    )
    return g
