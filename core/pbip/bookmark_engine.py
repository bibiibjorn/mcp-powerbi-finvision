"""
PBIP Bookmark Engine - Manages bookmarks in PBIP report definitions.

Bookmarks live in ``definition/bookmarks/`` as individual
``{id}.bookmark.json`` files plus the ``bookmarks.json`` index
(``items[]``: top-level ``{name}`` entries and groups
``{name, displayName, children: [<id>, ...]}``).

Real shapes (R0105 + R0201, 515 bookmarks — deep review 2026-09-22, H-06):

* group ``children`` are plain STRING ids (497 of 497; the old engine wrote
  ``[{"name": id}]`` and crashed reading real files);
* ``options`` = ``suppressData`` / ``suppressDisplay`` /
  ``suppressActiveSection`` / ``applyOnlyToTargetVisuals`` +
  ``targetVisualNames`` — there is no ``captureData`` / ``captureDisplay``
  key (old invented keys are still read and migrated on write);
* deleting updates the index FIRST and unlinks after, so a crash can no
  longer leave the index pointing at a missing file.

All public functions return ``Dict[str, Any]`` with ``success: bool`` plus
data, or ``error: str`` on failure.  No MCP awareness.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from core.pbip import pbir_model
from core.pbip.authoring import safe_io
from core.pbip.authoring.id_generator import generate_visual_id
from core.utilities.pbip_utils import SKIP_WRITE, load_json_file

logger = logging.getLogger(__name__)

# Default schema versions for generated files; sibling files win (H-36).
BOOKMARK_SCHEMA = (
    "https://developer.microsoft.com/json-schemas/fabric/"
    "item/report/definition/bookmark/2.1.0/schema.json"
)
BOOKMARKS_INDEX_SCHEMA = (
    "https://developer.microsoft.com/json-schemas/fabric/"
    "item/report/definition/bookmarksMetadata/1.0.0/schema.json"
)

_LEGACY_CAPTURE_KEYS = ("captureData", "captureDisplay", "captureCurrentPage")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _bookmarks_dir(definition_path: Path) -> Path:
    """Return the bookmarks directory, creating it if needed."""
    bdir = definition_path / "bookmarks"
    safe_io.make_dirs(bdir)
    return bdir


def _find_bookmark_file(definition_path: Path, bookmark_id: str) -> Optional[Path]:
    """Locate a bookmark file by id (file stem / ``name``) or UNIQUE display name."""
    bdir = Path(definition_path) / "bookmarks"
    if not bdir.is_dir() or not bookmark_id:
        return None
    if safe_io.is_safe_segment(bookmark_id):
        direct = bdir / f"{bookmark_id}.bookmark.json"
        if direct.is_file():
            return direct
    id_lower = bookmark_id.lower()
    by_display: List[Path] = []
    for bfile in sorted(bdir.glob("*.bookmark.json")):
        data = load_json_file(bfile)
        if not data:
            continue
        if str(data.get("name", "")).lower() == id_lower:
            return bfile
        if str(data.get("displayName", "")).lower() == id_lower:
            by_display.append(bfile)
    if len(by_display) == 1:
        return by_display[0]
    if len(by_display) > 1:
        logger.warning("bookmark display name %r is ambiguous (%d)", bookmark_id, len(by_display))
    return None


def _capture_flags(options: Dict[str, Any]) -> Dict[str, bool]:
    """``{data, display, current_page}`` from real ``suppress*`` keys (legacy keys tolerated)."""

    def flag(real: str, legacy: str) -> bool:
        if real in options:
            return not bool(options[real])
        if legacy in options:
            return bool(options[legacy])
        return True

    return {
        "data": flag("suppressData", "captureData"),
        "display": flag("suppressDisplay", "captureDisplay"),
        "current_page": flag("suppressActiveSection", "captureCurrentPage"),
    }


def _apply_capture(
    options: Dict[str, Any],
    capture_data: Optional[bool],
    capture_display: Optional[bool],
    capture_current_page: Optional[bool],
) -> List[str]:
    """Write capture choices as Desktop's ``suppress*`` flags.

    Returns the (legacy-named) capture settings whose effective value
    changed. When any capture arg is given, the invented legacy keys
    (``captureData`` ...) written by older versions are migrated too.
    """
    requested = (capture_data, capture_display, capture_current_page)
    if all(a is None for a in requested):
        return []
    before_flags = _capture_flags(options)
    changed: List[str] = []
    for arg, real, legacy, flag in (
        (capture_data, "suppressData", "captureData", "data"),
        (capture_display, "suppressDisplay", "captureDisplay", "display"),
        (capture_current_page, "suppressActiveSection", "captureCurrentPage", "current_page"),
    ):
        effective = arg if arg is not None else before_flags[flag]
        options.pop(legacy, None)
        if effective:
            options.pop(real, None)
        else:
            options[real] = True
        if arg is not None and bool(arg) != before_flags[flag]:
            changed.append(legacy)
    return changed


def _summarize_bookmark(bfile: Path, data: Dict[str, Any]) -> Dict[str, Any]:
    """Build a compact summary dict for one bookmark (real PBIR schema)."""
    options = data.get("options", {}) or {}
    exploration = data.get("explorationState") or {}
    active_section = exploration.get("activeSection")
    sections = exploration.get("sections") or {}

    captured_visuals: List[str] = []
    if active_section and active_section in sections:
        vis_containers = sections[active_section].get("visualContainers") or {}
        if isinstance(vis_containers, dict):
            captured_visuals = list(vis_containers.keys())

    legacy_state = data.get("state") or {}
    target_page = active_section or legacy_state.get("currentPage") or legacy_state.get("page")
    flags = _capture_flags(options)
    capture = [k for k, key in (("data", "data"), ("display", "display")) if flags[key]]
    if flags["current_page"]:
        capture.append("currentPage")
    targets = options.get("targetVisualNames") if options.get("applyOnlyToTargetVisuals") else None
    section = sections.get(active_section, {}) if active_section else {}
    return {
        "id": data.get("name", bfile.name[: -len(".bookmark.json")]),
        "display_name": data.get("displayName", ""),
        "file": str(bfile),
        "target_page": target_page,
        "capture": capture,
        "applies_to": "selected_visuals" if targets is not None else "all_visuals",
        "target_visual_names": (targets or [])[:20],
        "affected_visual_count": len(targets) if targets is not None else len(captured_visuals),
        "affected_visuals": (targets if targets is not None else captured_visuals)[:20],
        "has_filters": bool(section.get("filters") or legacy_state.get("filters")),
        "has_slicer_states": bool(section.get("slicerStates") or legacy_state.get("slicerStates")),
        "exploration_state_version": exploration.get("version") if exploration else None,
    }


def _bookmarks_index_file(definition_path: Path) -> Path:
    """Path of the bookmarks.json index (ordering + group nesting)."""
    return Path(definition_path) / "bookmarks" / "bookmarks.json"


def _load_bookmarks_index(definition_path: Path) -> Dict[str, Any]:
    """Load the bookmarks index, returning a default skeleton if absent."""
    idx = _bookmarks_index_file(definition_path)
    if not idx.exists():
        return {"$schema": BOOKMARKS_INDEX_SCHEMA, "items": []}
    data = load_json_file(idx)
    if not data or "items" not in data:
        return {"$schema": BOOKMARKS_INDEX_SCHEMA, "items": []}
    return data


def _save_bookmarks_index(definition_path: Path, index: Dict[str, Any]) -> bool:
    """Persist the bookmarks index (children normalised to string ids)."""
    for item in index.get("items") or []:
        if isinstance(item, dict) and "children" in item:
            pbir_model.set_bookmark_group_children(item, pbir_model.bookmark_group_children(item))
    _bookmarks_dir(definition_path)
    return safe_io.write_json(_bookmarks_index_file(definition_path), index)


def _update_index(definition_path: Path, mutate) -> bool:
    """Locked RMW of bookmarks.json (created when missing)."""
    _bookmarks_dir(definition_path)
    return safe_io.update_json(
        _bookmarks_index_file(definition_path),
        mutate,
        create_if_missing=True,
        default_factory=lambda: {"$schema": BOOKMARKS_INDEX_SCHEMA, "items": []},
    )


def remove_from_bookmarks_index(
    definition_path: Path, ids: Set[str], *, drop_empty_groups: bool = True
) -> Dict[str, Any]:
    """Remove bookmark ids from bookmarks.json (top level + group children).

    Groups left without children are dropped (``drop_empty_groups``).
    Returns ``{removed: [...], groups_dropped: [...]}``.
    """
    removed: List[str] = []
    dropped: List[str] = []

    def _mutate(index: Dict[str, Any]) -> Any:
        out = []
        for item in index.get("items") or []:
            if not isinstance(item, dict):
                continue
            if "children" in item:
                kids = pbir_model.bookmark_group_children(item)
                kept = [k for k in kids if k not in ids]
                removed.extend(k for k in kids if k in ids)
                pbir_model.set_bookmark_group_children(item, kept)
                if not kept and drop_empty_groups and len(kids) != len(kept):
                    dropped.append(item.get("name"))
                    continue
                out.append(item)
            elif item.get("name") in ids:
                removed.append(item["name"])
            else:
                out.append(item)
        if not removed and not dropped:
            return SKIP_WRITE
        index["items"] = out
        return None

    if _bookmarks_index_file(definition_path).exists():
        safe_io.update_json(_bookmarks_index_file(definition_path), _mutate)
    return {"removed": removed, "groups_dropped": dropped}


def _bookmark_referenced_by(definition_path: Path, bookmark_id: str) -> List[Dict[str, str]]:
    """Action buttons / navigators pointing at *bookmark_id* (G-11 accessor)."""
    out: List[Dict[str, str]] = []
    for rec in pbir_model.iter_visuals(definition_path):
        refs = pbir_model.get_bookmark_references(rec.data)
        if bookmark_id in refs["bookmarks"] or bookmark_id in refs["selected_bookmarks"]:
            out.append({"page_id": rec.page_dir.name, "visual_name": rec.visual_dir.name})
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_bookmarks(definition_path: Path) -> Dict[str, Any]:
    """List all bookmarks with display name, page, capture settings and group."""
    definition_path = Path(definition_path)
    bdir = definition_path / "bookmarks"
    if not bdir.is_dir():
        return {"success": True, "bookmarks": [], "count": 0}
    membership = pbir_model.bookmark_membership(_load_bookmarks_index(definition_path))
    bookmarks: List[Dict[str, Any]] = []
    for bfile in sorted(bdir.glob("*.bookmark.json")):
        data = load_json_file(bfile)
        if data is None:
            logger.warning("Skipping unreadable bookmark file: %s", bfile)
            continue
        summary = _summarize_bookmark(bfile, data)
        summary["group"] = membership["parent"].get(summary["id"])
        bookmarks.append(summary)
    return {
        "success": True,
        "bookmarks": bookmarks,
        "count": len(bookmarks),
        "groups": {g: kids for g, kids in membership["groups"].items()},
    }


def create_bookmark(
    definition_path: Path,
    display_name: str,
    page_name: Optional[str] = None,
    capture_data: bool = True,
    capture_display: bool = True,
) -> Dict[str, Any]:
    """Create a new bookmark with the given display name and optional target page."""
    definition_path = Path(definition_path)
    bdir = _bookmarks_dir(definition_path)
    bookmark_id = generate_visual_id()

    resolved_page_id: Optional[str] = None
    if page_name:
        from core.pbip.authoring.targets import resolve_page_dir

        page_dir, err = resolve_page_dir(definition_path, page_name)
        if err:
            return err
        resolved_page_id = page_dir.name

    exploration_state: Dict[str, Any] = {"version": "1.3", "sections": {}}
    if resolved_page_id:
        exploration_state["activeSection"] = resolved_page_id
        exploration_state["sections"][resolved_page_id] = {"visualContainers": {}}

    options: Dict[str, Any] = {}
    _apply_capture(options, capture_data, capture_display, resolved_page_id is not None)
    bookmark_data: Dict[str, Any] = {
        "$schema": _sibling_bookmark_schema(bdir),
        "displayName": display_name,
        "name": bookmark_id,
        "options": options,
        "explorationState": exploration_state,
    }
    bfile = bdir / f"{bookmark_id}.bookmark.json"
    safe_io.write_json(bfile, bookmark_data)

    def _register(index: Dict[str, Any]) -> Any:
        items = index.setdefault("items", [])
        if any(isinstance(it, dict) and it.get("name") == bookmark_id for it in items):
            return SKIP_WRITE
        items.append({"name": bookmark_id})
        return None

    _update_index(definition_path, _register)
    logger.info("Created bookmark '%s' (%s)", display_name, bookmark_id)
    return {
        "success": True,
        "bookmark_id": bookmark_id,
        "display_name": display_name,
        "page_id": resolved_page_id,
        "file": str(bfile),
        "note": (
            "The bookmark captures no visual state yet (Desktop records state when you "
            "update it in the Bookmarks pane)."
        ),
    }


def _sibling_bookmark_schema(bdir: Path) -> str:
    for other in sorted(bdir.glob("*.bookmark.json")):
        data = load_json_file(other)
        if isinstance(data, dict) and isinstance(data.get("$schema"), str):
            return data["$schema"]
    return BOOKMARK_SCHEMA


def rename_bookmark(definition_path: Path, bookmark_id: str, new_name: str) -> Dict[str, Any]:
    """Rename a bookmark's display name."""
    definition_path = Path(definition_path)
    bfile = _find_bookmark_file(definition_path, bookmark_id)
    if not bfile:
        return {"success": False, "error": f"Bookmark not found: {bookmark_id}"}
    before = load_json_file(bfile)
    if before is None:
        return {"success": False, "error": f"Cannot read bookmark file: {bfile}"}
    old_name = before.get("displayName", "")

    def _mutate(data: Dict[str, Any]) -> None:
        data["displayName"] = new_name

    safe_io.update_json(bfile, _mutate)
    logger.info("Renamed bookmark '%s' -> '%s'", old_name, new_name)
    return {
        "success": True,
        "bookmark_id": before.get("name", bookmark_id),
        "old_name": old_name,
        "new_name": new_name,
    }


def delete_bookmark(definition_path: Path, bookmark_id: str) -> Dict[str, Any]:
    """Delete a bookmark file by id or display name.

    H-06: the id is removed from ``bookmarks.json`` (top level AND every
    group's string ``children``) BEFORE the file is unlinked. Buttons /
    navigators still pointing at it are reported (``referenced_by``).
    """
    definition_path = Path(definition_path)
    bfile = _find_bookmark_file(definition_path, bookmark_id)
    if not bfile:
        return {"success": False, "error": f"Bookmark not found: {bookmark_id}"}
    data = load_json_file(bfile)
    display = data.get("displayName", bookmark_id) if data else bookmark_id
    resolved_id = data.get("name", bookmark_id) if data else bookmark_id
    referenced_by = _bookmark_referenced_by(definition_path, resolved_id)

    index_result = remove_from_bookmarks_index(definition_path, {resolved_id})
    safe_io.remove_path(bfile)
    logger.info("Deleted bookmark '%s' (%s)", display, resolved_id)
    result: Dict[str, Any] = {
        "success": True,
        "deleted_id": resolved_id,
        "display_name": display,
        "file": str(bfile),
        "index_updated": bool(index_result["removed"]),
        "groups_dropped": index_result["groups_dropped"],
    }
    if referenced_by:
        result["referenced_by"] = referenced_by
        result["warning"] = (
            f"{len(referenced_by)} button / navigator visual(s) still point at the deleted "
            "bookmark; retarget them (07_Visual_Operations:set_action)."
        )
    return result


def set_bookmark_capture(
    definition_path: Path,
    bookmark_id: str,
    capture_data: Optional[bool] = None,
    capture_display: Optional[bool] = None,
    capture_current_page: Optional[bool] = None,
) -> Dict[str, Any]:
    """Configure what a bookmark captures (Desktop's ``suppress*`` option flags)."""
    definition_path = Path(definition_path)
    bfile = _find_bookmark_file(definition_path, bookmark_id)
    if not bfile:
        return {"success": False, "error": f"Bookmark not found: {bookmark_id}"}
    before = load_json_file(bfile)
    if before is None:
        return {"success": False, "error": f"Cannot read bookmark file: {bfile}"}
    holder: Dict[str, Any] = {}

    def _mutate(data: Dict[str, Any]) -> None:
        options = data.setdefault("options", {})
        _apply_capture(options, capture_data, capture_display, capture_current_page)
        holder["options"] = dict(options)

    safe_io.update_json(bfile, _mutate)
    return {
        "success": True,
        "bookmark_id": before.get("name", bookmark_id),
        "options": holder.get("options"),
        "capture": _capture_flags(holder.get("options") or {}),
    }


def set_affected_visuals(
    definition_path: Path,
    bookmark_id: str,
    visual_ids: Optional[List[str]] = None,
    all_visuals: bool = False,
) -> Dict[str, Any]:
    """Scope a bookmark to selected visuals (``options.applyOnlyToTargetVisuals`` +
    ``targetVisualNames``, the shape of 515 real bookmarks) or to all visuals.

    The captured per-visual state in ``explorationState`` is left untouched.
    """
    definition_path = Path(definition_path)
    bfile = _find_bookmark_file(definition_path, bookmark_id)
    if not bfile:
        return {"success": False, "error": f"Bookmark not found: {bookmark_id}"}
    before = load_json_file(bfile)
    if before is None:
        return {"success": False, "error": f"Cannot read bookmark file: {bfile}"}
    ids = list(dict.fromkeys(visual_ids or []))
    if not all_visuals:
        if not ids:
            return {"success": False, "error": "visual_ids or all_visuals=true is required"}
        page = (before.get("explorationState") or {}).get("activeSection")
        if page:
            vroot = definition_path / "pages" / page / "visuals"
            missing = [v for v in ids if not (safe_io.is_safe_segment(v) and (vroot / v).is_dir())]
            if missing:
                return {
                    "success": False,
                    "error_type": "visuals_not_found",
                    "error": f"visual id(s) not on the bookmark's page {page}: {missing}",
                    "missing": missing,
                }

    def _mutate(data: Dict[str, Any]) -> None:
        options = data.setdefault("options", {})
        if all_visuals:
            options.pop("applyOnlyToTargetVisuals", None)
            options.pop("targetVisualNames", None)
        else:
            options["applyOnlyToTargetVisuals"] = True
            options["targetVisualNames"] = ids

    safe_io.update_json(bfile, _mutate)
    return {
        "success": True,
        "bookmark_id": before.get("name", bookmark_id),
        "all_visuals": all_visuals,
        "affected_count": -1 if all_visuals else len(ids),
        "target_visual_names": None if all_visuals else ids,
    }


# ---------------------------------------------------------------------------
# T1-G: bookmark get + update + group operations
# ---------------------------------------------------------------------------


def get_bookmark(definition_path: Path, bookmark_id: str) -> Dict[str, Any]:
    """Return the full bookmark JSON (not a summary)."""
    definition_path = Path(definition_path)
    bfile = _find_bookmark_file(definition_path, bookmark_id)
    if not bfile:
        return {"success": False, "error": f"Bookmark not found: {bookmark_id}"}
    data = load_json_file(bfile)
    if data is None:
        return {"success": False, "error": f"Cannot read bookmark file: {bfile}"}
    return {
        "success": True,
        "bookmark_id": data.get("name", bookmark_id),
        "file": str(bfile),
        "data": data,
        "capture": _capture_flags(data.get("options") or {}),
    }


def update_bookmark(
    definition_path: Path,
    bookmark_id: str,
    display_name: Optional[str] = None,
    capture_data: Optional[bool] = None,
    capture_display: Optional[bool] = None,
    capture_current_page: Optional[bool] = None,
) -> Dict[str, Any]:
    """Update display name and / or capture flags in one call. None args are unchanged."""
    definition_path = Path(definition_path)
    bfile = _find_bookmark_file(definition_path, bookmark_id)
    if not bfile:
        return {"success": False, "error": f"Bookmark not found: {bookmark_id}"}
    before = load_json_file(bfile)
    if before is None:
        return {"success": False, "error": f"Cannot read bookmark file: {bfile}"}
    changes: List[str] = []
    holder: Dict[str, Any] = {}

    def _mutate(data: Dict[str, Any]) -> Any:
        if display_name is not None and display_name != data.get("displayName"):
            data["displayName"] = display_name
            changes.append("displayName")
        options = data.setdefault("options", {})
        changes.extend(_apply_capture(options, capture_data, capture_display, capture_current_page))
        holder["options"] = dict(options)
        return None if changes else SKIP_WRITE

    safe_io.update_json(bfile, _mutate)
    return {
        "success": True,
        "bookmark_id": before.get("name", bookmark_id),
        "changed": list(dict.fromkeys(changes)),
        "options": holder.get("options"),
    }


def create_bookmark_group(
    definition_path: Path,
    display_name: str,
    bookmark_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Create a bookmark group containing the given bookmark IDs (in order)."""
    definition_path = Path(definition_path)
    if not display_name:
        return {"success": False, "error": "display_name is required"}
    bookmark_ids = bookmark_ids or []
    resolved: List[str] = []
    missing: List[str] = []
    for bid in bookmark_ids:
        bfile = _find_bookmark_file(definition_path, bid)
        if bfile is None:
            missing.append(bid)
        else:
            data = load_json_file(bfile) or {}
            resolved.append(data.get("name") or bfile.name[: -len(".bookmark.json")])
    if missing:
        return {
            "success": False,
            "error": f"Bookmark(s) not found: {', '.join(missing)}",
            "error_type": "bookmarks_not_found",
            "missing": missing,
        }
    group_id = generate_visual_id()
    member_set = set(resolved)

    def _mutate(index: Dict[str, Any]) -> None:
        new_items = []
        for item in index.get("items", []):
            if not isinstance(item, dict):
                continue
            if "children" in item:
                # A bookmark belongs to at most one group: move it.
                kids = [k for k in pbir_model.bookmark_group_children(item) if k not in member_set]
                pbir_model.set_bookmark_group_children(item, kids)
                new_items.append(item)
            elif item.get("name") not in member_set:
                new_items.append(item)
        group_item: Dict[str, Any] = {"name": group_id, "displayName": display_name}
        pbir_model.set_bookmark_group_children(group_item, resolved)
        new_items.append(group_item)
        index["items"] = new_items

    _update_index(definition_path, _mutate)
    return {
        "success": True,
        "group_id": group_id,
        "display_name": display_name,
        "children": resolved,
    }


def update_bookmark_group(
    definition_path: Path,
    group_id: str,
    display_name: Optional[str] = None,
    bookmark_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Update a bookmark group's display name and / or child ordering.

    Passing ``bookmark_ids`` REPLACES the children list.
    """
    definition_path = Path(definition_path)
    index = _load_bookmarks_index(definition_path)
    target = next(
        (
            i
            for i in index.get("items", [])
            if isinstance(i, dict) and i.get("name") == group_id and "children" in i
        ),
        None,
    )
    if target is None:
        return {"success": False, "error": f"Bookmark group not found: {group_id}"}
    resolved: Optional[List[str]] = None
    if bookmark_ids is not None:
        missing = [b for b in bookmark_ids if _find_bookmark_file(definition_path, b) is None]
        if missing:
            return {
                "success": False,
                "error": f"Bookmark(s) not found: {', '.join(missing)}",
                "error_type": "bookmarks_not_found",
                "missing": missing,
            }
        resolved = []
        for b in bookmark_ids:
            data = load_json_file(_find_bookmark_file(definition_path, b)) or {}
            resolved.append(data.get("name") or b)
    changed: List[str] = []

    def _mutate(idx: Dict[str, Any]) -> Any:
        for item in idx.get("items", []):
            if not (isinstance(item, dict) and item.get("name") == group_id and "children" in item):
                continue
            if display_name is not None and item.get("displayName") != display_name:
                item["displayName"] = display_name
                changed.append("displayName")
            if resolved is not None and (
                pbir_model.bookmark_group_children(item) != resolved
                or any(not isinstance(c, str) for c in item.get("children") or [])
            ):
                pbir_model.set_bookmark_group_children(item, resolved)
                changed.append("children")
        return None if changed else SKIP_WRITE

    safe_io.update_json(_bookmarks_index_file(definition_path), _mutate)
    return {"success": True, "group_id": group_id, "changed": changed}


def delete_bookmark_group(
    definition_path: Path,
    group_id: str,
    delete_children: bool = False,
) -> Dict[str, Any]:
    """Delete a bookmark group. By default keeps the child bookmarks at the
    top level; set ``delete_children=True`` to also delete each child file.

    The index is written ONCE (group removed / children promoted) before any
    child file is unlinked (H-06).
    """
    definition_path = Path(definition_path)
    index = _load_bookmarks_index(definition_path)
    target = next(
        (
            i
            for i in index.get("items", [])
            if isinstance(i, dict) and i.get("name") == group_id and "children" in i
        ),
        None,
    )
    if target is None:
        return {"success": False, "error": f"Bookmark group not found: {group_id}"}
    child_ids = pbir_model.bookmark_group_children(target)

    def _mutate(idx: Dict[str, Any]) -> None:
        items = []
        for item in idx.get("items", []):
            if isinstance(item, dict) and item.get("name") == group_id and "children" in item:
                if not delete_children:
                    items.extend({"name": bid} for bid in child_ids)
                continue
            items.append(item)
        idx["items"] = items

    safe_io.update_json(_bookmarks_index_file(definition_path), _mutate)
    deleted_children: List[str] = []
    if delete_children:
        for bid in child_ids:
            bfile = _find_bookmark_file(definition_path, bid)
            if bfile is not None:
                safe_io.remove_path(bfile)
                deleted_children.append(bid)
    return {
        "success": True,
        "group_id": group_id,
        "child_count": len(child_ids),
        "deleted_children": deleted_children,
    }
