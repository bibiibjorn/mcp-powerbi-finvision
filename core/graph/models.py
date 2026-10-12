"""Graph data model — Node, Edge, Graph dataclasses.

Node id format:  "<kind>:<scoped_name>"
  measure          -> "measure:Sales[Total Sales]"
  column           -> "column:Sales[Amount]"
  table            -> "table:Sales"             (attrs: calculated / field_parameter / calc_group)
  calc_group_item  -> "calc_group_item:TimeIntel[YTD]"
  rls_role         -> "rls_role:US Sales"
  hierarchy        -> "hierarchy:Date[Calendar]"
  function         -> "function:Fin.Rate"       (DAX user-defined function)
  relationship     -> "relationship:Sales[DateKey]->Date[DateKey]"
  report           -> "report:RPT-Sales"
  page             -> "page:<report>/<page-id>"
  visual           -> "visual:<report>/<page-id>/<visual-id>"
  bookmark         -> "bookmark:<report>/<bookmark-id>"
  slicer, theme_ref   (reserved; not emitted)

Edges always point from the CONSUMER to what it depends on:
  references      DAX reference (measure / calc column / calc table / calc item /
                  RLS / format-string / detail-rows / UDF body)
  binds           a visual projection / conditional-format / object binding
  filters         a visual / page / report / bookmark / RLS filter
  belongs_to      child -> container (column->table, visual->page, page->report)
  sort_by         column -> its sort-by column
  relates         relationship -> its from / to column
  hierarchy_level hierarchy -> a level's column
  calls           DAX object -> the UDF it calls
  navigates       action button / bookmark navigator -> bookmark
  captures        bookmark -> a visual whose state it captures
  captured_by     (legacy, not emitted)

Adjacency indexes (J-11) make ``outgoing`` / ``incoming`` O(degree), so BFS,
``find_unused`` and ``find_cycles`` are O(V+E). They are rebuilt lazily when the
edge dict is mutated directly (``g.edges.update(...)``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

_VALID_NODE_KINDS = frozenset(
    {
        "measure",
        "column",
        "table",
        "calc_group_item",
        "rls_role",
        "hierarchy",
        "function",
        "relationship",
        "report",
        "visual",
        "page",
        "bookmark",
        "slicer",
        "theme_ref",
    }
)
_VALID_EDGE_KINDS = frozenset(
    {
        "references",
        "binds",
        "filters",
        "belongs_to",
        "captured_by",
        "sort_by",
        "relates",
        "hierarchy_level",
        "calls",
        "navigates",
        "captures",
    }
)

#: Model-side node kinds (objects that live in the semantic model).
MODEL_NODE_KINDS = frozenset(
    {
        "measure",
        "column",
        "table",
        "calc_group_item",
        "rls_role",
        "hierarchy",
        "function",
        "relationship",
    }
)
#: Report-side node kinds.
REPORT_NODE_KINDS = frozenset({"report", "page", "visual", "bookmark", "slicer", "theme_ref"})


@dataclass(frozen=True)
class Node:
    kind: str
    scoped_name: str
    attrs: Dict[str, Any] = field(default_factory=dict, hash=False, compare=False)

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.scoped_name}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "scoped_name": self.scoped_name,
            "attrs": dict(self.attrs),
        }


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    kind: str
    attrs: Dict[str, Any] = field(default_factory=dict, hash=False, compare=False)

    @property
    def id(self) -> str:
        return f"{self.src}->{self.dst}:{self.kind}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "src": self.src,
            "dst": self.dst,
            "kind": self.kind,
            "attrs": dict(self.attrs),
        }


@dataclass
class Graph:
    nodes: Dict[str, Node] = field(default_factory=dict)
    edges: Dict[str, Edge] = field(default_factory=dict)
    #: Build metadata: scope, model_etag, built_at, report_side_available, ...
    meta: Dict[str, Any] = field(default_factory=dict)
    _out: Dict[str, List[Edge]] = field(default_factory=dict, repr=False, compare=False)
    _in: Dict[str, List[Edge]] = field(default_factory=dict, repr=False, compare=False)
    _indexed: int = field(default=0, repr=False, compare=False)
    _ci: Optional[Dict[str, List[str]]] = field(default=None, repr=False, compare=False)
    _ci_nodes: int = field(default=-1, repr=False, compare=False)

    def add_node(self, node: Node) -> None:
        if node.kind not in _VALID_NODE_KINDS:
            raise ValueError(f"invalid node kind: {node.kind!r}")
        self.nodes.setdefault(node.id, node)

    def add_edge(self, edge: Edge) -> None:
        if edge.kind not in _VALID_EDGE_KINDS:
            raise ValueError(f"invalid edge kind: {edge.kind!r}")
        if edge.id in self.edges:
            return
        in_sync = self._indexed == len(self.edges)
        self.edges[edge.id] = edge
        if in_sync:
            self._out.setdefault(edge.src, []).append(edge)
            self._in.setdefault(edge.dst, []).append(edge)
            self._indexed = len(self.edges)

    def _ensure_index(self) -> None:
        if self._indexed == len(self.edges):
            return
        out: Dict[str, List[Edge]] = {}
        inc: Dict[str, List[Edge]] = {}
        for e in self.edges.values():
            out.setdefault(e.src, []).append(e)
            inc.setdefault(e.dst, []).append(e)
        self._out, self._in = out, inc
        self._indexed = len(self.edges)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "nodes": [n.to_dict() for n in self.nodes.values()],
            "edges": [e.to_dict() for e in self.edges.values()],
        }
        if self.meta:
            out["meta"] = dict(self.meta)
        return out

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Graph":
        g = cls()
        for nd in data.get("nodes", []):
            g.add_node(
                Node(
                    kind=nd["kind"],
                    scoped_name=nd["scoped_name"],
                    attrs=dict(nd.get("attrs", {})),
                )
            )
        for ed in data.get("edges", []):
            g.add_edge(
                Edge(
                    src=ed["src"],
                    dst=ed["dst"],
                    kind=ed["kind"],
                    attrs=dict(ed.get("attrs", {})),
                )
            )
        if isinstance(data.get("meta"), dict):
            g.meta.update(data["meta"])
        return g

    def outgoing(self, node_id: str) -> List[Edge]:
        self._ensure_index()
        return list(self._out.get(node_id, ()))

    def incoming(self, node_id: str) -> List[Edge]:
        self._ensure_index()
        return list(self._in.get(node_id, ()))

    # -- case-insensitive lookup (DAX names are case-insensitive, J-04) -------
    def find_ci(self, node_id: str) -> List[str]:
        """Node ids equal to *node_id* ignoring case (exact id first)."""
        if node_id in self.nodes:
            return [node_id]
        if self._ci is None or self._ci_nodes != len(self.nodes):
            idx: Dict[str, List[str]] = {}
            for nid in self.nodes:
                idx.setdefault(nid.casefold(), []).append(nid)
            self._ci, self._ci_nodes = idx, len(self.nodes)
        return list(self._ci.get(node_id.casefold(), ()))

    def has_report_side(self) -> bool:
        if "report_side_available" in self.meta:
            return bool(self.meta["report_side_available"])
        return any(n.kind in ("page", "visual") for n in self.nodes.values())
