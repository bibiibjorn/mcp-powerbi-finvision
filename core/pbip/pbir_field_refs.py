"""
Field references in PBIR JSON (visual.json, page.json, report.json, bookmarks).

Review G-09 / G-10 (2026-09-22): the report analyzer read visual filters from
``visual.filters`` (no PBIR visual has that key — they live in the TOP-LEVEL
``filterConfig.filters``), used a hard-coded list of projection roles (Deneb's
``dataset`` and future roles were missed), and never looked at
``sortDefinition``, ``HierarchyLevel`` fields or bookmark states. The broken-ref
scanner ignored ``SourceRef.Source`` aliases, so filter references resolved to
table "".

:func:`extract_field_refs` walks ANY PBIR JSON subtree once and returns every
``Column`` / ``Measure`` / ``Hierarchy`` / ``HierarchyLevel`` /
``PropertyVariationSource`` reference with its table resolved (``Entity`` or a
``Source`` alias bound by the nearest enclosing ``From`` list) and a location
tag (``query:<role>``, ``sort``, ``filter``, ``objects``, ``vco``,
``selector``, ``bookmark``, ``other``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

__all__ = ["FieldRef", "extract_field_refs", "location_of"]

_REF_KEYS = frozenset({"Column", "Measure", "Hierarchy", "HierarchyLevel", "selector", "From"})

# ``Table.Field`` or an aggregation wrapper ``Sum(Table.Field)``. Field names may
# themselves contain parentheses (``m Measure.Total Return (%)``), so the wrapper
# is only stripped when the whole string is ``Word(...)``.
_SELECTOR_WRAPPED_RE = re.compile(r"^\s*[A-Za-z]+\((?P<inner>[^()]*\..*)\)\s*$")


def _split_selector(meta: str) -> Optional[Tuple[str, str]]:
    text = meta.strip()
    m = _SELECTOR_WRAPPED_RE.match(text)
    if m and "." in m.group("inner"):
        text = m.group("inner").strip()
    if "." not in text:
        return None
    table, name = text.split(".", 1)
    table, name = table.strip(), name.strip()
    return (table, name) if table and name else None


@dataclass(frozen=True)
class FieldRef:
    """One field reference.

    ``kind``: ``column`` | ``measure`` | ``hierarchy`` | ``hierarchy_level`` |
    ``selector`` (a ``selector.metadata`` string — column OR measure).
    For ``hierarchy_level``: ``name`` is the hierarchy, ``level`` the level.
    ``variation_column`` is set when the hierarchy is an auto date/time
    variation (``PropertyVariationSource``): the model column behind it.
    """

    kind: str
    table: str
    name: str
    level: str = ""
    variation_column: str = ""
    location: str = "other"

    @property
    def key(self) -> str:
        return f"{self.table}[{self.name}]"


def location_of(path: Tuple[str, ...]) -> str:
    """Location tag for a JSON key path."""
    if "explorationState" in path:  # a bookmark's captured filters/state are bookmark refs
        return "bookmark"
    if "queryState" in path:
        i = path.index("queryState")
        role = path[i + 1] if i + 1 < len(path) else ""
        return f"query:{role}" if role else "query"
    if "sortDefinition" in path:
        return "sort"
    if "filterConfig" in path or "filters" in path:
        return "filter"
    if "visualContainerObjects" in path or "vcObjects" in path:
        return "vco"
    if "objects" in path:
        return "objects"
    return "other"


def _entity(expr: Any, aliases: Dict[str, str]) -> str:
    if not isinstance(expr, dict):
        return ""
    src = expr.get("SourceRef")
    if isinstance(src, dict):
        ent = src.get("Entity")
        if isinstance(ent, str) and ent:
            return ent
        alias = src.get("Source")
        if isinstance(alias, str):
            return aliases.get(alias, "")
    return ""


def extract_field_refs(
    obj: Any, *, base_path: Iterable[str] = (), max_depth: int = 60
) -> List[FieldRef]:
    """Every field reference under *obj* (duplicates removed, order kept)."""
    out: List[FieldRef] = []
    seen: set = set()

    def add(ref: FieldRef) -> None:
        k = (ref.kind, ref.table, ref.name, ref.level, ref.variation_column, ref.location)
        if k not in seen:
            seen.add(k)
            out.append(ref)

    stack: List[str] = list(base_path)

    def loc() -> str:
        return location_of(tuple(stack))

    def handle(node: Dict[str, Any], aliases: Dict[str, str]) -> None:
        for kind_key, kind in (("Column", "column"), ("Measure", "measure")):
            ref = node.get(kind_key)
            if type(ref) is dict and isinstance(ref.get("Property"), str):
                add(
                    FieldRef(
                        kind,
                        _entity(ref.get("Expression"), aliases),
                        ref["Property"],
                        location=loc(),
                    )
                )
        hier = node.get("Hierarchy")
        # the Hierarchy inside a HierarchyLevel is that level's owner, not a
        # whole-hierarchy binding
        in_level = len(stack) >= 2 and stack[-2] == "HierarchyLevel" and stack[-1] == "Expression"
        if type(hier) is dict and isinstance(hier.get("Hierarchy"), str) and not in_level:
            expr = hier.get("Expression") or {}
            pvs = expr.get("PropertyVariationSource") if type(expr) is dict else None
            if type(pvs) is dict:
                add(
                    FieldRef(
                        "hierarchy",
                        _entity(pvs.get("Expression"), aliases),
                        hier["Hierarchy"],
                        variation_column=str(pvs.get("Property") or ""),
                        location=loc(),
                    )
                )
            else:
                add(
                    FieldRef("hierarchy", _entity(expr, aliases), hier["Hierarchy"], location=loc())
                )
        hl = node.get("HierarchyLevel")
        if type(hl) is dict and isinstance(hl.get("Level"), str):
            e = hl.get("Expression")
            h = e.get("Hierarchy") if type(e) is dict else None
            if type(h) is dict:
                expr = h.get("Expression") or {}
                pvs = expr.get("PropertyVariationSource") if type(expr) is dict else None
                if type(pvs) is dict:
                    add(
                        FieldRef(
                            "hierarchy_level",
                            _entity(pvs.get("Expression"), aliases),
                            str(h.get("Hierarchy") or ""),
                            level=hl["Level"],
                            variation_column=str(pvs.get("Property") or ""),
                            location=loc(),
                        )
                    )
                else:
                    add(
                        FieldRef(
                            "hierarchy_level",
                            _entity(expr, aliases),
                            str(h.get("Hierarchy") or ""),
                            level=hl["Level"],
                            location=loc(),
                        )
                    )
        sel = node.get("selector")
        if type(sel) is dict:
            meta = sel.get("metadata")
            if isinstance(meta, str):
                split = _split_selector(meta)
                if split:
                    add(FieldRef("selector", split[0], split[1], location="selector"))

    def walk(node: Any, aliases: Dict[str, str], depth: int) -> None:
        if depth > max_depth:
            return
        t = type(node)
        if t is list:
            for item in node:
                ti = type(item)
                if ti is dict or ti is list:
                    walk(item, aliases, depth + 1)
            return
        if t is not dict:
            return
        if not _REF_KEYS.isdisjoint(node):
            frm = node.get("From")
            if type(frm) is list:
                scoped = dict(aliases)
                for f in frm:
                    if type(f) is dict and isinstance(f.get("Name"), str):
                        ent = f.get("Entity")
                        if isinstance(ent, str):
                            scoped[f["Name"]] = ent
                aliases = scoped
            handle(node, aliases)
        for k, v in node.items():
            tv = type(v)
            if tv is dict or tv is list:
                stack.append(k)
                walk(v, aliases, depth + 1)
                stack.pop()

    walk(obj, {}, 0)
    return out


def split_refs(refs: List[FieldRef]) -> Dict[str, List[Dict[str, str]]]:
    """Legacy ``fields`` shape: ``{columns:[{table,column}], measures:[{table,measure}],
    hierarchies:[{table,hierarchy}]}`` (selector refs are listed as measures, as before)."""
    cols: List[Dict[str, str]] = []
    meas: List[Dict[str, str]] = []
    hier: List[Dict[str, str]] = []
    seen: set = set()
    for r in refs:
        if not r.table:
            continue
        if r.kind == "column":
            key = ("c", r.table, r.name)
            if key not in seen:
                seen.add(key)
                cols.append({"table": r.table, "column": r.name})
        elif r.kind in ("measure", "selector"):
            key = ("m", r.table, r.name)
            if key not in seen:
                seen.add(key)
                meas.append({"table": r.table, "measure": r.name})
        elif r.kind in ("hierarchy", "hierarchy_level"):
            key = ("h", r.table, r.name)
            if key not in seen:
                seen.add(key)
                hier.append({"table": r.table, "hierarchy": r.name})
            if r.variation_column:
                key = ("c", r.table, r.variation_column)
                if key not in seen:
                    seen.add(key)
                    cols.append({"table": r.table, "column": r.variation_column})
    return {"columns": cols, "measures": meas, "hierarchies": hier}


def refs_to_dicts(refs: List[FieldRef]) -> List[Dict[str, str]]:
    out = []
    for r in refs:
        d = {"kind": r.kind, "table": r.table, "name": r.name, "location": r.location}
        if r.level:
            d["level"] = r.level
        if r.variation_column:
            d["variation_column"] = r.variation_column
        out.append(d)
    return out


def dicts_to_refs(items: Optional[List[Dict[str, Any]]]) -> List[FieldRef]:
    out = []
    for d in items or []:
        if isinstance(d, dict):
            out.append(
                FieldRef(
                    str(d.get("kind", "")),
                    str(d.get("table", "")),
                    str(d.get("name", "")),
                    str(d.get("level", "")),
                    str(d.get("variation_column", "")),
                    str(d.get("location", "other")),
                )
            )
    return out
