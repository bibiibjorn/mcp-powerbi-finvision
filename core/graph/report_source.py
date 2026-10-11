"""Report side of the dependency graph, read with ``core.pbip.pbir_model``.

Review J-03 (b): the old builder instantiated ``PbipDependencyEngine()`` with no
arguments and called a ``scan`` method that does not exist; the ``TypeError``
was swallowed, so the report graph was ALWAYS empty and every "zero blast
radius" ignored visuals. This module walks the PBIR definition directly:

* every visual's field expressions — ``visual.query`` projections (binds),
  ``filterConfig`` (filters), ``visual.objects`` / ``visualContainerObjects``
  (conditional-format / object bindings; a slicer's persisted selection counts
  as a filter), field parameters bound like any column;
* page filters and drillthrough bindings, report-level filters;
* bookmarks (captured filters, captured visuals, group membership) and the
  action buttons / navigators pointing at them;
* report-level (extension) measures from ``reportExtensions.json``.

Field expressions handled: ``Measure`` / ``Column`` (``SourceRef.Entity`` or a
``From`` alias via ``SourceRef.Source``), ``Aggregation`` (walks into its
column), ``HierarchyLevel`` / ``Hierarchy`` (incl. ``PropertyVariationSource``
auto-date hierarchies -> the underlying date column).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)

FieldRef = Tuple[str, str, str]  # (kind: measure|column|hierarchy, table, name)


def _load(path: Path) -> Optional[Any]:
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _entity(expr: Any, aliases: Dict[str, str]) -> Optional[str]:
    """Table name of a ``{"SourceRef": {...}}`` expression."""
    if not isinstance(expr, dict):
        return None
    src = expr.get("SourceRef")
    if isinstance(src, dict):
        if isinstance(src.get("Entity"), str):
            return src["Entity"]
        if isinstance(src.get("Source"), str):
            return aliases.get(src["Source"])
    return None


def _hierarchy_target(node: Dict[str, Any], aliases: Dict[str, str]) -> List[FieldRef]:
    """Targets of a ``{"Hierarchy": {...}}`` expression body."""
    out: List[FieldRef] = []
    inner = node.get("Expression") if isinstance(node, dict) else None
    hname = node.get("Hierarchy") if isinstance(node, dict) else None
    if isinstance(inner, dict) and isinstance(inner.get("PropertyVariationSource"), dict):
        pvs = inner["PropertyVariationSource"]
        table = _entity(pvs.get("Expression"), aliases)
        prop = pvs.get("Property")
        if table and isinstance(prop, str):
            out.append(("column", table, prop))  # auto date hierarchy -> the date column
        return out
    table = _entity(inner, aliases)
    if table and isinstance(hname, str):
        out.append(("hierarchy", table, hname))
    return out


def iter_field_refs(
    obj: Any, aliases: Optional[Dict[str, str]] = None
) -> Iterator[Tuple[FieldRef, Optional[str]]]:
    """Yield ``((kind, table, name), level)`` for every field expression in *obj*.

    ``level`` is the hierarchy level name for ``HierarchyLevel`` refs, else None.
    ``From`` alias lists (query / filter definitions) are honoured for their subtree.
    """
    aliases = dict(aliases or {})
    stack: List[Tuple[Any, Dict[str, str]]] = [(obj, aliases)]
    while stack:
        node, al = stack.pop()
        if isinstance(node, list):
            for item in reversed(node):
                stack.append((item, al))
            continue
        if not isinstance(node, dict):
            continue
        frm = node.get("From")
        if isinstance(frm, list):
            al = dict(al)
            for f in frm:
                if (
                    isinstance(f, dict)
                    and isinstance(f.get("Name"), str)
                    and isinstance(f.get("Entity"), str)
                ):
                    al[f["Name"]] = f["Entity"]
        handled = False
        for kind, key in (("measure", "Measure"), ("column", "Column")):
            body = node.get(key)
            if isinstance(body, dict) and isinstance(body.get("Property"), str):
                table = _entity(body.get("Expression"), al)
                if table:
                    yield (kind, table, body["Property"]), None
                    handled = True
        hl = node.get("HierarchyLevel")
        if isinstance(hl, dict):
            hexpr = (hl.get("Expression") or {}).get("Hierarchy")
            if isinstance(hexpr, dict):
                for ref in _hierarchy_target(hexpr, al):
                    yield ref, hl.get("Level") if isinstance(hl.get("Level"), str) else None
                handled = True
        hier = node.get("Hierarchy")
        if (
            isinstance(hier, dict)
            and isinstance(hier.get("Hierarchy"), str)
            and not isinstance(hl, dict)
        ):
            for ref in _hierarchy_target(hier, al):
                yield ref, None
            handled = True
        for key, val in node.items():
            if handled and key in ("Measure", "Column", "HierarchyLevel", "Hierarchy"):
                continue
            if isinstance(val, (dict, list)):
                stack.append((val, al))


def _split_objects(visual_json: Dict[str, Any]) -> List[Tuple[str, Any]]:
    """``(edge_kind, subtree)`` pairs of a visual.json."""
    parts: List[Tuple[str, Any]] = []
    visual = visual_json.get("visual") if isinstance(visual_json.get("visual"), dict) else {}
    for key, val in visual.items():
        if key == "objects" and isinstance(val, dict):
            for oname, entries in val.items():
                if oname == "general" and isinstance(entries, list):
                    for entry in entries:
                        props = entry.get("properties") if isinstance(entry, dict) else None
                        if isinstance(props, dict) and "filter" in props:
                            parts.append(("filters", props.get("filter")))
                            rest = {k: v for k, v in props.items() if k != "filter"}
                            parts.append(("binds", rest))
                        else:
                            parts.append(("binds", entry))
                else:
                    parts.append(("binds", entries))
        elif key in ("query", "visualContainerObjects", "expansionStates"):
            parts.append(("binds", val))
        elif isinstance(val, (dict, list)):
            parts.append(("binds", val))
    for key in ("filterConfig",):
        if key in visual_json:
            parts.append(("filters", visual_json[key]))
    group = visual_json.get("visualGroup")
    if isinstance(group, dict):
        parts.append(("binds", group))
    return parts


class ReportScan:
    """Result of scanning one or more PBIR reports (plain data for the builder)."""

    def __init__(self) -> None:
        self.reports: List[Dict[str, Any]] = []
        self.pages: List[Dict[str, Any]] = []
        self.visuals: List[Dict[str, Any]] = []
        self.bookmarks: List[Dict[str, Any]] = []
        # (src_id, edge_kind, FieldRef, level, via)
        self.field_edges: List[Tuple[str, str, FieldRef, Optional[str], str]] = []
        # (src_id, dst_id, edge_kind)
        self.plain_edges: List[Tuple[str, str, str]] = []
        self.extension_measures: List[Dict[str, Any]] = []
        self.warnings: List[str] = []
        # (bookmark node id, captured visual node id) -> True when the bookmark shows the visual
        # (not display.mode == hidden); drives "hidden visual never revealed by a bookmark" (F-13)
        self.capture_reveals: Dict[Tuple[str, str], bool] = {}


def _visual_ids_in_bookmark(bm: Any) -> List[Tuple[str, str]]:
    """``(page_id, visual_id)`` pairs a bookmark's explorationState captures."""
    out: List[Tuple[str, str]] = []
    es = bm.get("explorationState") if isinstance(bm, dict) else None
    sections = es.get("sections") if isinstance(es, dict) else None
    if isinstance(sections, dict):
        for page_id, sec in sections.items():
            vcs = sec.get("visualContainers") if isinstance(sec, dict) else None
            if isinstance(vcs, dict):
                for vid in vcs:
                    out.append((str(page_id), str(vid)))
    return out


def _bookmark_captures(bm: Any) -> List[Tuple[str, str, bool]]:
    """``(page_id, visual_or_group_id, reveals)`` for everything a bookmark's state captures.

    ``visualContainers[id].singleVisual.display.mode == "hidden"`` (or an ``isHidden`` flag on a
    ``visualContainerGroups`` entry) means the bookmark HIDES that element; any other capture
    shows it (so a base-hidden visual captured this way is revealed by the bookmark).
    """
    out: List[Tuple[str, str, bool]] = []
    es = bm.get("explorationState") if isinstance(bm, dict) else None
    sections = es.get("sections") if isinstance(es, dict) else None
    if not isinstance(sections, dict):
        return out

    def hidden(entry: Any) -> bool:
        if not isinstance(entry, dict):
            return False
        single = entry.get("singleVisual")
        holders = [entry, single if isinstance(single, dict) else {}]
        for holder in holders:
            display = holder.get("display")
            if isinstance(display, dict) and str(display.get("mode")).lower() == "hidden":
                return True
        return entry.get("isHidden") is True

    def groups(page_id: str, block: Any) -> None:
        if not isinstance(block, dict):
            return
        for gid, entry in block.items():
            if gid == "children":
                continue
            out.append((page_id, str(gid), not hidden(entry)))
            if isinstance(entry, dict):
                groups(page_id, entry.get("children"))

    for page_id, sec in sections.items():
        if not isinstance(sec, dict):
            continue
        vcs = sec.get("visualContainers")
        if isinstance(vcs, dict):
            for vid, entry in vcs.items():
                out.append((str(page_id), str(vid), not hidden(entry)))
        groups(str(page_id), sec.get("visualContainerGroups"))
    return out


def scan_reports(report_entries: List[Dict[str, Any]]) -> ReportScan:
    """Scan every report of ``pbir_model.resolve_pbip(...)["reports"]``."""
    from core.pbip.pbir_model import (
        bookmark_membership,
        get_bookmark_references,
        iter_visuals,
    )

    scan = ReportScan()
    for rep in report_entries:
        ddir = Path(rep.get("definition_dir") or Path(rep["report_dir"]) / "definition")
        rname = str(rep.get("name") or Path(rep["report_dir"]).stem)
        if rname.lower().endswith(".report"):
            rname = rname[: -len(".report")]
        if not ddir.is_dir():
            scan.warnings.append(f"{rname}: no definition/ folder (PBIR required)")
            continue
        report_id = f"report:{rname}"
        scan.reports.append({"id": report_id, "name": rname, "definition_dir": str(ddir)})

        report_json = _load(ddir / "report.json")
        if isinstance(report_json, dict) and "filterConfig" in report_json:
            for ref, level in iter_field_refs(report_json["filterConfig"]):
                scan.field_edges.append((report_id, "filters", ref, level, "report_filter"))

        pages_dir = ddir / "pages"
        try:
            page_dirs = sorted(p for p in pages_dir.iterdir() if p.is_dir())
        except OSError:
            page_dirs = []
        page_id_by_folder: Dict[str, str] = {}
        for pdir in page_dirs:
            pj = _load(pdir / "page.json")
            pid = str((pj or {}).get("name") or pdir.name)
            page_id_by_folder[pdir.name] = pid
            page_node = f"page:{rname}/{pid}"
            scan.pages.append(
                {
                    "id": page_node,
                    "report": rname,
                    "page_id": pid,
                    "display_name": (pj or {}).get("displayName"),
                }
            )
            scan.plain_edges.append((page_node, report_id, "belongs_to"))
            if isinstance(pj, dict):
                if "filterConfig" in pj:
                    for ref, level in iter_field_refs(pj["filterConfig"]):
                        scan.field_edges.append((page_node, "filters", ref, level, "page_filter"))
                if "pageBinding" in pj:
                    for ref, level in iter_field_refs(pj["pageBinding"]):
                        scan.field_edges.append((page_node, "filters", ref, level, "page_binding"))

        for rec in iter_visuals(ddir):
            pid = page_id_by_folder.get(rec.page_dir.name, rec.page_dir.name)
            vid = str(rec.data.get("name") or rec.visual_dir.name)
            vnode = f"visual:{rname}/{pid}/{vid}"
            vtype = (
                (rec.data.get("visual") or {}) if isinstance(rec.data.get("visual"), dict) else {}
            ).get("visualType") or ("visualGroup" if "visualGroup" in rec.data else None)
            scan.visuals.append(
                {
                    "id": vnode,
                    "report": rname,
                    "page_id": pid,
                    "visual_id": vid,
                    "visual_type": vtype,
                    "hidden": bool(rec.data.get("isHidden")),
                    "parent_group": rec.data.get("parentGroupName"),
                }
            )
            scan.plain_edges.append((vnode, f"page:{rname}/{pid}", "belongs_to"))
            for edge_kind, subtree in _split_objects(rec.data):
                for ref, level in iter_field_refs(subtree):
                    scan.field_edges.append((vnode, edge_kind, ref, level, "visual"))
            try:
                bref = get_bookmark_references(rec.data)
            except Exception:  # noqa: BLE001
                bref = {}
            for key in ("bookmarks", "selected_bookmarks", "bookmark_groups"):
                for bid in bref.get(key) or []:
                    scan.plain_edges.append((vnode, f"bookmark:{rname}/{bid}", "navigates"))

        bdir = ddir / "bookmarks"
        if bdir.is_dir():
            meta = _load(bdir / "bookmarks.json")
            membership = bookmark_membership(meta) if isinstance(meta, dict) else {"groups": {}}
            for group, children in (membership.get("groups") or {}).items():
                gnode = f"bookmark:{rname}/{group}"
                scan.bookmarks.append({"id": gnode, "report": rname, "name": group, "group": True})
                for child in children:
                    scan.plain_edges.append((f"bookmark:{rname}/{child}", gnode, "belongs_to"))
            for bfile in sorted(bdir.glob("*.bookmark.json")):
                bm = _load(bfile)
                if not isinstance(bm, dict):
                    continue
                bid = str(bm.get("name") or bfile.name[: -len(".bookmark.json")])
                bnode = f"bookmark:{rname}/{bid}"
                scan.bookmarks.append(
                    {
                        "id": bnode,
                        "report": rname,
                        "name": bid,
                        "display_name": bm.get("displayName"),
                    }
                )
                for ref, level in iter_field_refs(bm.get("explorationState") or {}):
                    scan.field_edges.append((bnode, "filters", ref, level, "bookmark"))
                for page_id, vid in _visual_ids_in_bookmark(bm):
                    scan.plain_edges.append((bnode, f"visual:{rname}/{page_id}/{vid}", "captures"))
                for page_id, vid, reveals in _bookmark_captures(bm):
                    vnode = f"visual:{rname}/{page_id}/{vid}"
                    scan.capture_reveals[(bnode, vnode)] = reveals
                    if (bnode, vnode, "captures") not in scan.plain_edges:
                        scan.plain_edges.append((bnode, vnode, "captures"))

        ext = _load(ddir / "reportExtensions.json")
        if isinstance(ext, dict):
            for ent in ext.get("entities") or []:
                if not isinstance(ent, dict):
                    continue
                for m in ent.get("measures") or []:
                    if isinstance(m, dict) and m.get("name"):
                        scan.extension_measures.append(
                            {
                                "report": rname,
                                "table": str(ent.get("name") or ""),
                                "name": str(m["name"]),
                                "expression": str(m.get("expression") or ""),
                            }
                        )
    return scan


def report_signature(definition_dirs: List[str]) -> Tuple[Any, ...]:
    """Value signature of the report trees (J-19): (files, bytes, max mtime_ns,
    dirs, max dir mtime_ns) — deletions and mtime-preserving copies both move it.

    P-06: served by the shared signature service in ``server.pbip_cache``
    (parallel walk, watcher/TTL memo, invalidated by every PBIR write) instead
    of a private serial walk on every call.
    """
    from server.pbip_cache import pbip_cache

    files = total = dirs = 0
    max_f = max_d = 0
    for sig in pbip_cache.signature_for_trees([str(d) for d in definition_dirs]):
        f, d, t, mf, md = sig
        if f < 0:  # tree missing: contributes nothing (same as an unreadable folder)
            continue
        files += f
        dirs += d
        total += t
        max_f = max(max_f, mf)
        max_d = max(max_d, md)
    return (files, total, max_f, dirs, max_d)


__all__ = ["iter_field_refs", "scan_reports", "report_signature", "ReportScan"]
