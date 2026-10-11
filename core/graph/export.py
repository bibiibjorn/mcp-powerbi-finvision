"""Serialize a dependency Graph to DOT / Mermaid / JSON (Phase 5).

Pure functions — no I/O, no MCP awareness. The handler decides whether to
return the string inline or spill it to a file.
"""

from __future__ import annotations

import json as _json

from core.graph.models import Graph

# Per-kind visual hints (used by both DOT and Mermaid where supported).
_KIND_COLOR = {
    "measure": "#118DFF",
    "column": "#12239E",
    "table": "#E66C37",
    "visual": "#6B007B",
    "page": "#C83D95",
}


def to_json(graph: Graph) -> str:
    """Compact JSON adjacency (the same shape Graph.to_dict produces)."""
    return _json.dumps(graph.to_dict(), separators=(",", ":"))


def _mermaid_id(node_id: str, counter: dict) -> str:
    """Mermaid node ids must be alnum-ish; map each graph id to nN."""
    if node_id not in counter:
        counter[node_id] = f"n{len(counter)}"
    return counter[node_id]


def to_mermaid(graph: Graph) -> str:
    """Mermaid flowchart (graph TD)."""
    lines = ["graph TD"]
    ids: dict = {}
    for node in graph.nodes.values():
        nid = _mermaid_id(node.id, ids)
        label = node.scoped_name.replace('"', "'")
        lines.append(f'    {nid}["{label}"]')
    for edge in graph.edges.values():
        if edge.src not in graph.nodes or edge.dst not in graph.nodes:
            continue
        src = _mermaid_id(edge.src, ids)
        dst = _mermaid_id(edge.dst, ids)
        kind = edge.kind
        lines.append(f"    {src} -->|{kind}| {dst}")
    return "\n".join(lines) + "\n"


def _dot_str(text: str) -> str:
    """A DOT double-quoted string (J-28: a quote or backslash in an id broke the file)."""
    esc = str(text).replace("\\", "\\\\").replace('"', '\\"')
    esc = esc.replace("\r", " ").replace("\n", " ")
    return f'"{esc}"'


def to_dot(graph: Graph) -> str:
    """Graphviz DOT digraph (ids escaped)."""
    lines = ["digraph dependencies {", "    rankdir=LR;", "    node [shape=box];"]
    for node in graph.nodes.values():
        color = _KIND_COLOR.get(node.kind)
        attrs = f' [fillcolor="{color}", style=filled, fontcolor=white]' if color else ""
        lines.append(f"    {_dot_str(node.id)}{attrs};")
    for edge in graph.edges.values():
        lines.append(
            f"    {_dot_str(edge.src)} -> {_dot_str(edge.dst)} [label={_dot_str(edge.kind)}];"
        )
    lines.append("}")
    return "\n".join(lines) + "\n"


def export_graph(graph: Graph, fmt: str) -> str:
    """Dispatch by format string. Raises ValueError on unknown format."""
    fmt = (fmt or "json").lower()
    if fmt == "json":
        return to_json(graph)
    if fmt == "dot":
        return to_dot(graph)
    if fmt == "mermaid":
        return to_mermaid(graph)
    raise ValueError(f"unknown export format: {fmt!r} (use json|dot|mermaid)")
