"""
Clone Engine for PBIP Report Authoring

Deep-cloning of pages and reports, and deletion of pages/visuals, against
the REAL PBIR layout (deep review 2026-09-22, WP9):

* ``definition/pages/pages.json`` is the only page registry (H-03) — clone /
  create / delete keep ``pageOrder`` / ``activePageName`` /
  ``landingPageName`` consistent.
* Page interactions live under ``visualInteractions`` (H-08) and are
  remapped on clone / stripped on delete.
* ``clone_page`` stages the copy in a short-named sibling folder of
  ``pages/`` and moves it into place only when every file is rewritten
  (H-34); visual ids are remapped with a structural walk that replaces only
  EXACT string values / dict keys equal to an old id (or its ``'id'``
  literal) inside the cloned page — never substrings, never base64 blobs.
* ``clone_report`` keeps page and visual ids (a separate report needs no new
  ids, so every navigation button / tooltip / drillthrough / bookmark
  reference stays valid — H-07); only ``.platform`` gets a new logicalId, and
  ``definition.pbir`` ``byPath`` is re-based to the new location.
* ``delete_visual`` refuses to delete a slicer or a visual carrying filters
  without ``confirm_delete_filter_layer=True`` and returns the filter state it
  would destroy (H-25); un-parented children keep their page position.
* ``delete_page`` scrubs bookmarks that pointed at the page and reports the
  navigation / tooltip / drillthrough references that now dangle.

Every write goes through ``safe_io`` (dry-run aware, locked, failures raise).
"""

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from core.pbip import pbir_model
from core.pbip.authoring import safe_io
from core.pbip.authoring.id_generator import generate_guid, generate_visual_id
from core.pbip.authoring.reference_cleanup import (
    strip_dangling_interactions as _strip_interactions_shared,
    strip_dangling_visual_refs as _strip_refs_shared,
)
from core.utilities.pbip_utils import SKIP_WRITE, load_json_file

logger = logging.getLogger(__name__)

_PAGES_METADATA_SCHEMA = (
    "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/"
    "pagesMetadata/1.1.0/schema.json"
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def remap_exact_ids(obj: Any, mapping: Dict[str, str]) -> Tuple[Any, int]:
    """Replace string values / dict keys EXACTLY equal to an old id (or ``'id'``).

    Structural and scoped: a value merely CONTAINING an id (base64 image
    blobs, Deneb specs, DAX) is never touched. Returns ``(new_obj, count)``.
    """
    lit_map = {f"'{k}'": f"'{v}'" for k, v in mapping.items()}
    count = 0

    def walk(node: Any) -> Any:
        nonlocal count
        if isinstance(node, str):
            if node in mapping:
                count += 1
                return mapping[node]
            if node in lit_map:
                count += 1
                return lit_map[node]
            return node
        if isinstance(node, list):
            return [walk(x) for x in node]
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                nk = mapping.get(k, k)
                if nk != k:
                    count += 1
                out[nk] = walk(v)
            return out
        return node

    return walk(obj), count


def _pages_meta_default(definition_path: Path) -> Dict[str, Any]:
    return {
        "$schema": safe_io.sibling_schema(definition_path, "pages.json", _PAGES_METADATA_SCHEMA),
        "pageOrder": [],
    }


def update_pages_json(
    definition_path: Path,
    *,
    add: Optional[str] = None,
    insert_after: Optional[str] = None,
    remove: Optional[str] = None,
) -> None:
    """Register / unregister a page in ``definition/pages/pages.json`` (H-03).

    Removing a page also repoints ``activePageName`` / ``landingPageName``
    when they named it (first remaining page, or the key is dropped).
    """
    path = pbir_model.pages_json_path(definition_path)

    def _mutate(meta: Dict[str, Any]) -> None:
        order = [p for p in (meta.get("pageOrder") or []) if isinstance(p, str)]
        if remove is not None:
            order = [p for p in order if p != remove]
            for key in ("activePageName", "landingPageName"):
                if meta.get(key) == remove:
                    if order:
                        meta[key] = order[0]
                    else:
                        meta.pop(key, None)
        if add is not None and add not in order:
            if insert_after and insert_after in order:
                order.insert(order.index(insert_after) + 1, add)
            else:
                order.append(add)
        meta["pageOrder"] = order

    safe_io.update_json(
        path,
        _mutate,
        create_if_missing=True,
        default_factory=lambda: _pages_meta_default(definition_path),
    )


def filter_layer_state(visual_json: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The filter layer a visual carries (H-25) — None when it carries none.

    A slicer, or any visual whose top-level ``filterConfig`` holds filters with
    a condition, a persisted slicer selection or an inverted-selection flag.
    """
    visual = visual_json.get("visual") if isinstance(visual_json, dict) else None
    vtype = (visual or {}).get("visualType") or ""
    filters = pbir_model.get_visual_filters(visual_json)
    active = [f for f in filters if isinstance(f.get("filter"), dict)]
    selection = pbir_model.get_slicer_selection_filter(visual_json)
    inverted = [f.get("name") for f in filters if pbir_model.is_inverted_selection(f, visual_json)]
    is_slicer = "slicer" in vtype.lower()
    if not (is_slicer or active or selection is not None or inverted):
        return None
    return {
        "visual_type": vtype,
        "is_slicer": is_slicer,
        "filterConfig": visual_json.get("filterConfig"),
        "general_filter": selection,
        "inverted_filters": inverted,
        "queryState": ((visual or {}).get("query") or {}).get("queryState"),
    }


class CloneEngine:
    """Deep-clone pages and reports with proper ID regeneration."""

    # ------------------------------------------------------------------
    # clone_page
    # ------------------------------------------------------------------
    def clone_page(
        self,
        definition_path: Path,
        source_page_id: str,
        new_display_name: Optional[str] = None,
        insert_after: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Clone a page and all its visuals with new IDs.

        Returns ``{success, page_id, display_name, visual_count, id_mapping, path}``.
        """
        from core.pbip.authoring.targets import resolve_page_dir

        definition_path = Path(definition_path)
        pages_dir = definition_path / "pages"
        source_page_dir, err = resolve_page_dir(definition_path, source_page_id)
        if err:
            err["error"] = f"Source page not found: {source_page_id} ({err.get('error')})"
            return err
        source_page_id = source_page_dir.name

        new_page_id = generate_visual_id()
        new_page_dir = pages_dir / new_page_id
        id_mapping: Dict[str, str] = {source_page_id: new_page_id}
        visuals_dir = source_page_dir / "visuals"
        visual_ids = (
            sorted(v.name for v in visuals_dir.iterdir() if v.is_dir())
            if visuals_dir.is_dir()
            else []
        )
        visual_map = {vid: generate_visual_id() for vid in visual_ids}
        id_mapping.update(visual_map)

        page_data = load_json_file(source_page_dir / "page.json") or {}
        display_name = new_display_name or f"{page_data.get('displayName', source_page_id)} (Copy)"

        if safe_io.dry_run_active():
            return {
                "success": True,
                "dry_run": True,
                "page_id": new_page_id,
                "display_name": display_name,
                "visual_count": len(visual_ids),
                "id_mapping": id_mapping,
                "path": str(new_page_dir),
                "would_register_in": str(pbir_model.pages_json_path(definition_path)),
            }

        # H-34: stage next to the report definition (short name, same volume)
        # and move into place only once every file is rewritten.
        staging = definition_path.parent / f".mcp_stage_{new_page_id[:8]}"
        if staging.exists():
            safe_io.remove_path(staging)
        try:
            safe_io.copy_tree(source_page_dir, staging)
            remapped = 0
            for json_file in sorted(staging.rglob("*.json")):
                rel = json_file.relative_to(staging).parts

                def _mutate(data: Any, _rel: Tuple[str, ...] = rel) -> Any:
                    nonlocal remapped
                    # Visual ids only: references to OTHER pages (nav buttons,
                    # tooltips, drillthrough) must keep pointing at those pages.
                    new_data, n = remap_exact_ids(data, visual_map)
                    remapped += n
                    if _rel == ("page.json",) and isinstance(new_data, dict):
                        new_data["name"] = new_page_id
                        new_data["displayName"] = display_name
                        binding = new_data.get("pageBinding")
                        if isinstance(binding, dict) and binding.get("name"):
                            binding["name"] = generate_visual_id()
                        # H-08: legacy key folded into visualInteractions.
                        pbir_model.set_page_interactions(
                            new_data, pbir_model.get_page_interactions(new_data)
                        )
                    return new_data

                safe_io.update_json(json_file, _mutate)
            staged_visuals = staging / "visuals"
            for old_id, new_id in visual_map.items():
                src = staged_visuals / old_id
                if src.is_dir():
                    safe_io.move_path(src, staged_visuals / new_id)
            safe_io.move_path(staging, new_page_dir)
        except Exception as e:
            try:
                safe_io.remove_path(staging)
            except safe_io.WriteError:  # pragma: no cover - best effort
                logger.warning("clone_page: could not remove staging folder %s", staging)
            return {
                "success": False,
                "error_type": "clone_failed",
                "error": f"clone_page failed, nothing was added to the report: {e}",
            }

        update_pages_json(definition_path, add=new_page_id, insert_after=insert_after)
        return {
            "success": True,
            "page_id": new_page_id,
            "display_name": display_name,
            "visual_count": len(visual_ids),
            "id_mapping": id_mapping,
            "references_remapped": remapped,
            "path": str(new_page_dir),
        }

    # ------------------------------------------------------------------
    # clone_report
    # ------------------------------------------------------------------
    def clone_report(
        self,
        source_report_path: Path,
        target_path: Path,
        new_report_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Copy a whole ``.Report`` folder (ids kept, new ``.platform`` logicalId).

        H-07: page / visual ids are KEPT — a copied report needs no new ids,
        and regenerating them broke every navigationSection / tooltip /
        drillthrough / bookmark reference. ``definition.pbir`` ``byPath`` is
        re-based so the copy still binds to the same semantic model.
        """
        source_path = Path(source_report_path)
        target = Path(target_path)

        if not source_path.exists():
            return {"success": False, "error": f"Source report not found: {source_path}"}
        if target.exists():
            return {"success": False, "error": f"Target path already exists: {target}"}

        safe_io.allow_root(target.parent)
        definition_path = source_path / "definition"
        pages_dir = definition_path / "pages"
        page_count = 0
        total_visuals = 0
        if pages_dir.is_dir():
            for p in pages_dir.iterdir():
                if p.is_dir():
                    page_count += 1
                    vroot = p / "visuals"
                    if vroot.is_dir():
                        total_visuals += sum(1 for v in vroot.iterdir() if v.is_dir())
        if safe_io.dry_run_active():
            return {
                "success": True,
                "dry_run": True,
                "report_path": str(target),
                "page_count": page_count,
                "total_visuals": total_visuals,
                "ids_kept": True,
            }

        # H-34: short staging name (the old ".__clone_tmp__" suffix pushed deep
        # OneDrive paths past MAX_PATH) and the copy runs INSIDE the try block.
        staging = target.parent / f".mcp_clone_{generate_visual_id()[:8]}"
        try:
            safe_io.copy_tree(source_path, staging)
            self._finish_report_copy(source_path, staging, target, new_report_name)
            safe_io.move_path(staging, target)
        except Exception as e:
            try:
                safe_io.remove_path(staging)
            except safe_io.WriteError:  # pragma: no cover - best effort
                logger.warning("clone_report: could not remove staging folder %s", staging)
            return {
                "success": False,
                "error_type": "clone_failed",
                "error": f"clone_report failed, no report was created: {e}",
            }
        return {
            "success": True,
            "report_path": str(target),
            "page_count": page_count,
            "total_visuals": total_visuals,
            "ids_kept": True,
        }

    def _finish_report_copy(
        self, source: Path, staging: Path, target: Path, new_report_name: Optional[str]
    ) -> None:
        platform_path = staging / ".platform"
        if platform_path.exists():

            def _platform(data: Dict[str, Any]) -> None:
                if isinstance(data.get("config"), dict):
                    data["config"]["logicalId"] = generate_guid()
                if new_report_name and isinstance(data.get("metadata"), dict):
                    data["metadata"]["displayName"] = new_report_name

            safe_io.update_json(platform_path, _platform)
        pbir = staging / "definition.pbir"
        ref = pbir_model.read_dataset_reference(source)
        if pbir.exists() and ref.get("kind") == "byPath" and ref.get("model_dir"):
            try:
                rel = os.path.relpath(ref["model_dir"], str(target))
            except ValueError:  # different drive
                rel = str(ref["model_dir"])

            def _rebase(data: Dict[str, Any]) -> None:
                by_path = (data.get("datasetReference") or {}).get("byPath")
                if isinstance(by_path, dict):
                    by_path["path"] = rel.replace("\\", "/")

            safe_io.update_json(pbir, _rebase)

    # ------------------------------------------------------------------
    # delete_page
    # ------------------------------------------------------------------
    def delete_page(self, definition_path: Path, page_id: str) -> Dict[str, Any]:
        """Delete a page; keep pages.json, bookmarks and cross-page refs consistent (H-03)."""
        from core.pbip.authoring.targets import resolve_page_dir

        definition_path = Path(definition_path)
        page_dir, err = resolve_page_dir(definition_path, page_id)
        if err:
            err["error"] = f"Page not found: {page_id} ({err.get('error')})"
            return err
        page_id = page_dir.name
        page_json = load_json_file(page_dir / "page.json") or {}
        display_name = page_json.get("displayName", page_id)
        visuals_dir = page_dir / "visuals"
        visual_count = (
            sum(1 for f in visuals_dir.iterdir() if f.is_dir()) if visuals_dir.exists() else 0
        )
        dangling = self._references_to_page(definition_path, page_id)

        safe_io.remove_path(page_dir)
        update_pages_json(definition_path, remove=page_id)
        bookmarks = self._scrub_page_from_bookmarks(definition_path, page_id)
        nav_cleaned = self._strip_page_navigator_entries(definition_path, page_id)

        result: Dict[str, Any] = {
            "success": True,
            "deleted_page": page_id,
            "display_name": display_name,
            "visuals_removed": visual_count,
            "bookmarks_removed": bookmarks["removed"],
            "bookmarks_section_scrubbed": bookmarks["scrubbed"],
            "page_navigator_entries_removed": nav_cleaned,
            "cross_ref_cleanup": {},
        }
        if dangling:
            result["dangling_references"] = dangling
            result["warning"] = (
                f"{len(dangling)} visual(s) on other pages still point at the deleted page "
                "(navigation / drillthrough / tooltip); retarget or remove them."
            )
        return result

    def _references_to_page(self, definition_path: Path, page_id: str) -> List[Dict[str, Any]]:
        """Visuals on OTHER pages whose visualLink / visualTooltip targets *page_id*."""
        lit = f"'{page_id}'"
        out: List[Dict[str, Any]] = []
        for rec in pbir_model.iter_visuals(definition_path):
            if rec.page_dir.name == page_id:
                continue
            vco = (rec.data.get("visual") or {}).get("visualContainerObjects") or {}
            for card, keys in (
                ("visualLink", ("navigationSection", "drillthroughSection")),
                ("visualTooltip", ("section",)),
            ):
                for entry in vco.get(card) or []:
                    props = entry.get("properties") if isinstance(entry, dict) else None
                    for key in keys:
                        value = (
                            (((props or {}).get(key) or {}).get("expr") or {})
                            .get("Literal", {})
                            .get("Value")
                        )
                        if value == lit:
                            out.append(
                                {
                                    "page_id": rec.page_dir.name,
                                    "visual_name": rec.visual_dir.name,
                                    "property": f"{card}.{key}",
                                }
                            )
        return out

    def _strip_page_navigator_entries(self, definition_path: Path, page_id: str) -> int:
        removed = 0
        for rec in pbir_model.iter_visuals(definition_path):
            pages = ((rec.data.get("visual") or {}).get("objects") or {}).get("pages")
            if not isinstance(pages, list):
                continue
            if not any((e.get("selector") or {}).get("id") == page_id for e in pages):
                continue

            def _mutate(data: Dict[str, Any]) -> None:
                objs = data["visual"]["objects"]
                objs["pages"] = [
                    e for e in objs["pages"] if (e.get("selector") or {}).get("id") != page_id
                ]

            safe_io.update_json(rec.path, _mutate)
            removed += 1
        return removed

    def _scrub_page_from_bookmarks(self, definition_path: Path, page_id: str) -> Dict[str, Any]:
        """Remove bookmarks that navigate to *page_id*; drop its section elsewhere."""
        bdir = definition_path / "bookmarks"
        removed: List[str] = []
        scrubbed: List[str] = []
        if not bdir.is_dir():
            return {"removed": removed, "scrubbed": scrubbed}
        for bfile in sorted(bdir.glob("*.bookmark.json")):
            data = load_json_file(bfile) or {}
            exploration = data.get("explorationState") or {}
            bid = data.get("name") or bfile.name[: -len(".bookmark.json")]
            if exploration.get("activeSection") == page_id:
                safe_io.remove_path(bfile)
                removed.append(bid)
                continue
            if page_id in (exploration.get("sections") or {}):

                def _mutate(d: Dict[str, Any]) -> None:
                    d["explorationState"]["sections"].pop(page_id, None)

                safe_io.update_json(bfile, _mutate)
                scrubbed.append(bid)
        if removed:
            from core.pbip.bookmark_engine import remove_from_bookmarks_index

            remove_from_bookmarks_index(definition_path, set(removed))
        return {"removed": removed, "scrubbed": scrubbed}

    # ------------------------------------------------------------------
    # delete_visual
    # ------------------------------------------------------------------
    def delete_visual(
        self,
        definition_path: Path,
        page_id: str,
        visual_id: Any,
        delete_children: bool = True,
        *,
        confirm_delete_filter_layer: bool = False,
    ) -> Dict[str, Any]:
        """Delete visual(s) by EXACT id (``visual_id`` = id or list of ids).

        * H-25: a slicer / a visual carrying filters is only deleted with
          ``confirm_delete_filter_layer=True``; otherwise the call returns the
          filter state it would destroy and suggests ``hide`` instead.
        * ``delete_children=False`` re-parents the children to the group's
          own parent and translates their coordinates so they stay put.
        * Cleans ``visualInteractions``, ``parentGroupName`` and bookmark
          references to the deleted visuals.
        """
        from core.pbip.authoring.targets import resolve_page_dir, resolve_visual_dir

        definition_path = Path(definition_path)
        page_dir, err = resolve_page_dir(definition_path, page_id)
        if err:
            err["error"] = f"Page not found: {page_id} ({err.get('error')})"
            return err
        page_id = page_dir.name
        visuals_dir = page_dir / "visuals"
        if not visuals_dir.exists():
            return {"success": False, "error": "No visuals folder found"}

        requested = visual_id if isinstance(visual_id, list) else [visual_id]
        roots: List[str] = []
        for ref in requested:
            vdir, verr = resolve_visual_dir(page_dir, ref, allow_title=False)
            if verr:
                verr["error"] = f"Visual not found: {ref} ({verr.get('error')})"
                return verr
            if vdir.name not in roots:
                roots.append(vdir.name)

        all_data = {
            v.name: (load_json_file(v / "visual.json") or {})
            for v in visuals_dir.iterdir()
            if v.is_dir()
        }
        to_delete: List[str] = []
        reparent: List[Tuple[str, str]] = []  # (child, group)
        for root in roots:
            if root not in to_delete:
                to_delete.append(root)
            children = [vid for vid, d in all_data.items() if d.get("parentGroupName") == root]
            if delete_children:
                for child in self._descendants(all_data, root):
                    if child not in to_delete:
                        to_delete.append(child)
            else:
                reparent.extend((c, root) for c in children if c not in roots)

        filter_layers = {
            vid: state
            for vid in to_delete
            if (state := filter_layer_state(all_data.get(vid) or {})) is not None
        }
        if filter_layers and not confirm_delete_filter_layer:
            return {
                "success": False,
                "error_type": "filter_layer_delete_requires_confirmation",
                "error": (
                    f"{len(filter_layers)} of the {len(to_delete)} visual(s) to delete carry a "
                    "filter layer (slicer / filterConfig filters / persisted selection). "
                    "Deleting them changes what every other visual on the page shows, and "
                    "restoring the files does not restore the behaviour. Prefer "
                    "07_Visual_Operations operation=hide (sets isHidden). To delete anyway "
                    "pass confirm_delete_filter_layer=true."
                ),
                "filter_layer": filter_layers,
                "would_delete": to_delete,
                "alternative": {
                    "tool": "07_Visual_Operations",
                    "operation": "hide",
                    "page_name": page_id,
                    "visual_name": roots[0],
                },
            }

        # Re-parent orphaned children BEFORE removing their group (keep page position).
        for child, group in reparent:
            gdata = all_data.get(group) or {}
            gpos = gdata.get("position") or {}
            grand = gdata.get("parentGroupName")

            def _reparent(d: Dict[str, Any], _gpos: Dict = gpos, _grand: Any = grand) -> None:
                pos = d.setdefault("position", {})
                pos["x"] = (pos.get("x") or 0) + (_gpos.get("x") or 0)
                pos["y"] = (pos.get("y") or 0) + (_gpos.get("y") or 0)
                if _grand:
                    d["parentGroupName"] = _grand
                else:
                    d.pop("parentGroupName", None)

            safe_io.update_json(visuals_dir / child / "visual.json", _reparent)

        for vid in to_delete:
            safe_io.remove_path(visuals_dir / vid)

        deleted = set(to_delete)
        cross_ref_cleanup = self._strip_dangling_interactions(definition_path, deleted)
        if not safe_io.dry_run_active():
            live_group_ids = {vd.name for vd in visuals_dir.iterdir() if vd.is_dir()}
            _strip_refs_shared(visuals_dir, live_group_ids)
        bookmarks_scrubbed = self._scrub_deleted_visuals_from_bookmarks(definition_path, deleted)

        result: Dict[str, Any] = {
            "success": True,
            "deleted_visuals": to_delete,
            "count": len(to_delete),
            "page_id": page_id,
            "reparented_children": [c for c, _ in reparent],
            "cross_ref_cleanup": cross_ref_cleanup,
            "bookmarks_scrubbed": bookmarks_scrubbed,
        }
        if filter_layers:
            result["deleted_filter_layer"] = filter_layers
        return result

    @staticmethod
    def _descendants(all_data: Dict[str, Dict[str, Any]], root: str) -> List[str]:
        out: List[str] = []
        stack = [root]
        seen: Set[str] = {root}
        while stack:
            cur = stack.pop()
            for vid, d in all_data.items():
                if d.get("parentGroupName") == cur and vid not in seen:
                    seen.add(vid)
                    out.append(vid)
                    stack.append(vid)
        return out

    def _scrub_deleted_visuals_from_bookmarks(self, definition_path: Path, deleted_ids: set) -> int:
        """Remove deleted visual ids from every bookmark's captured state.

        ``explorationState.sections.*.visualContainers`` /
        ``visualContainerGroups`` (dicts keyed by id) and
        ``options.targetVisualNames``. Returns the number of bookmark files
        modified. A malformed bookmark is skipped.
        """
        bookmarks_dir = definition_path / "bookmarks"
        if not bookmarks_dir.exists() or not deleted_ids:
            return 0
        modified_count = 0
        for bookmark_file in sorted(bookmarks_dir.glob("*.bookmark.json")):

            def _mutate(data: Any) -> Any:
                if not isinstance(data, dict):
                    return SKIP_WRITE
                modified = False
                sections = (data.get("explorationState") or {}).get("sections") or {}
                for section_data in sections.values():
                    if not isinstance(section_data, dict):
                        continue
                    for key in ("visualContainers", "visualContainerGroups"):
                        containers = section_data.get(key)
                        if not isinstance(containers, dict):
                            continue
                        survivors = {k: v for k, v in containers.items() if k not in deleted_ids}
                        if len(survivors) != len(containers):
                            section_data[key] = survivors
                            modified = True
                options = data.get("options")
                if isinstance(options, dict) and isinstance(options.get("targetVisualNames"), list):
                    kept = [t for t in options["targetVisualNames"] if t not in deleted_ids]
                    if len(kept) != len(options["targetVisualNames"]):
                        options["targetVisualNames"] = kept
                        modified = True
                return None if modified else SKIP_WRITE

            try:
                if safe_io.update_json(bookmark_file, _mutate):
                    modified_count += 1
            except safe_io.WriteError as e:
                logger.warning("bookmark scrub skipped %s: %s", bookmark_file, e)
        return modified_count

    # --- Cross-reference cleanup (matches `pbir rm` semantics) ---

    def _strip_dangling_interactions(
        self,
        definition_path: Path,
        deleted_visual_ids: set,
        *,
        exclude_page_id: Optional[str] = None,
    ) -> Dict[str, int]:
        """Remove ``visualInteractions`` entries referencing deleted visuals (H-08)."""
        cleaned: Dict[str, int] = {}
        if not deleted_visual_ids:
            return cleaned
        pages_dir = definition_path / "pages"
        if not pages_dir.exists():
            return cleaned
        for pf in sorted(pages_dir.iterdir()):
            if not pf.is_dir() or (exclude_page_id is not None and pf.name == exclude_page_id):
                continue
            page_json_path = pf / "page.json"
            page_data = load_json_file(page_json_path)
            if not page_data:
                continue
            interactions = pbir_model.get_page_interactions(page_data)
            if not any(
                i.get("source") in deleted_visual_ids or i.get("target") in deleted_visual_ids
                for i in interactions
            ):
                continue
            visuals_dir = pf / "visuals"
            all_ids = (
                {vd.name for vd in visuals_dir.iterdir() if vd.is_dir()}
                if visuals_dir.exists()
                else set()
            )
            live_ids = all_ids - deleted_visual_ids
            kept = [
                i
                for i in interactions
                if i.get("source") in live_ids and i.get("target") in live_ids
            ]

            def _mutate(d: Dict[str, Any], _live: Set[str] = live_ids) -> None:
                _strip_interactions_shared(d, _live)

            safe_io.update_json(page_json_path, _mutate)
            if len(interactions) > len(kept):
                cleaned[pf.name] = len(interactions) - len(kept)
        return cleaned

    # --- Back-compat private helpers (used by callers / tests) ---

    def _remap_visual_ids(self, visual_data: Dict[str, Any], id_mapping: Dict[str, str]) -> None:
        """Remap ids in a visual.json dict in place (exact values only)."""
        new_data, _ = remap_exact_ids(visual_data, id_mapping)
        visual_data.clear()
        visual_data.update(new_data)

    def _remap_interactions(self, page_data: Dict[str, Any], id_mapping: Dict[str, str]) -> None:
        interactions = pbir_model.get_page_interactions(page_data)
        for interaction in interactions:
            for key in ("source", "target"):
                if interaction.get(key) in id_mapping:
                    interaction[key] = id_mapping[interaction[key]]
        pbir_model.set_page_interactions(page_data, interactions)

    def _update_pages_json(
        self, definition_path: Path, new_page_id: str, insert_after: Optional[str] = None
    ) -> None:
        update_pages_json(Path(definition_path), add=new_page_id, insert_after=insert_after)
