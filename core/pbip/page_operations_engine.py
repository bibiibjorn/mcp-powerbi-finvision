"""
Page Operations Engine for PBIP Reports

Page-level operations beyond create/clone/delete (which exist in clone_engine.py
and page_builder.py). Handles reordering, resizing, display options, backgrounds,
wallpapers, drillthrough configuration, tooltip pages, and visibility.

Deep review 2026-09-22 (WP9): writes the REAL PBIR ``page.json`` shapes
(83 pages checked, H-04) — ``displayOption: "FitToPage" | "FitToWidth" |
"ActualSize"``, ``visibility: "HiddenInViewMode"``, ``type: "Tooltip" |
"Drillthrough"``, drillthrough = ``pageBinding{name, type: "Drillthrough",
parameters[{name, boundFilter, fieldExpr}]}`` + a Categorical filter with
``howCreated: "Drillthrough"`` — instead of legacy PBIX integers / invented
keys, reads both forms back, uses ``definition/pages/pages.json`` (H-03) and
does every read-modify-write through ``safe_io.update_json`` (H-01).

No MCP awareness — pure domain logic operating on PBIP file structures.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.pbip import pbir_model
from core.pbip.authoring import safe_io
from core.pbip.authoring.id_generator import generate_visual_id
from core.pbip.authoring.targets import resolve_page_dir
from core.utilities.pbip_utils import SKIP_WRITE, load_json_file

logger = logging.getLogger(__name__)

#: PBIR displayOption enum (case-insensitive input; legacy ints read back).
_DISPLAY_OPTIONS = {
    "fittopage": "FitToPage",
    "fittowidth": "FitToWidth",
    "actualsize": "ActualSize",
}
_LEGACY_DISPLAY_INTS = {1: "FitToPage", 2: "FitToWidth", 3: "ActualSize", 0: "FitToPage"}
HIDDEN_VISIBILITY = "HiddenInViewMode"


def display_option_name(value: Any) -> Optional[str]:
    """PBIR name of a stored displayOption (string or legacy int)."""
    if isinstance(value, str):
        return _DISPLAY_OPTIONS.get(value.lower().replace("_", ""), value)
    if isinstance(value, int) and not isinstance(value, bool):
        return _LEGACY_DISPLAY_INTS.get(value)
    return None


def is_hidden_page(page_data: Dict[str, Any]) -> bool:
    """True for ``visibility: "HiddenInViewMode"`` (and the legacy ``1``)."""
    vis = page_data.get("visibility")
    return vis == HIDDEN_VISIBILITY or vis == 1


def page_type(page_data: Dict[str, Any]) -> Optional[str]:
    """``"Tooltip"`` / ``"Drillthrough"`` / None (legacy ``pageType: 1`` = Tooltip)."""
    t = page_data.get("type")
    if isinstance(t, str):
        return t
    if page_data.get("pageType") == 1:
        return "Tooltip"
    return None


def _resolve_page_id(definition_path: Path, page_name: str) -> Optional[str]:
    """Resolve a page name or ID to the page folder name (ID), exact-first."""
    folder, _err = resolve_page_dir(Path(definition_path), page_name)
    return folder.name if folder else None


def _page(definition_path: Path, page_name: str) -> Any:
    """``(page_folder, page_json_path, error)``."""
    folder, err = resolve_page_dir(Path(definition_path), page_name)
    if err:
        err["error"] = f"Page not found: '{page_name}' ({err.get('error')})"
        return None, None, err
    path = folder / "page.json"
    if not path.exists():
        return (
            None,
            None,
            {"success": False, "error": f"Could not read page.json for '{page_name}'"},
        )
    return folder, path, None


def reorder_pages(definition_path: Path, page_order: list) -> Dict[str, Any]:
    """Reorder pages. page_order is list of page IDs or display names in desired order."""
    definition_path = Path(definition_path)
    pages_json_path = pbir_model.pages_json_path(definition_path)
    meta = load_json_file(pages_json_path) if pages_json_path.exists() else None
    on_disk = (
        sorted(p.name for p in (definition_path / "pages").iterdir() if p.is_dir())
        if (definition_path / "pages").is_dir()
        else []
    )
    existing_order = [p for p in ((meta or {}).get("pageOrder") or []) if p in on_disk]
    # Pages that exist but were never registered are kept (appended) — repairs
    # reports damaged by the old definition/pages.json bug.
    existing_order += [p for p in on_disk if p not in existing_order]
    existing_set = set(existing_order)

    resolved_order: List[str] = []
    for entry in page_order:
        if entry in existing_set:
            resolved_order.append(entry)
            continue
        page_id = _resolve_page_id(definition_path, entry)
        if page_id and page_id in existing_set:
            resolved_order.append(page_id)
        else:
            return {
                "success": False,
                "error": f"Page not found: '{entry}'. Available pages: {existing_order}",
            }
    if len(set(resolved_order)) != len(resolved_order):
        return {"success": False, "error": "Duplicate pages in page_order"}

    remaining = [pid for pid in existing_order if pid not in resolved_order]
    final_order = resolved_order + remaining

    from core.pbip.authoring.clone_engine import _pages_meta_default

    def _mutate(m: Dict[str, Any]) -> None:
        m["pageOrder"] = final_order
        for key in ("activePageName", "landingPageName"):
            if key in m and m[key] not in final_order:
                m[key] = final_order[0] if final_order else m.pop(key)

    safe_io.update_json(
        pages_json_path,
        _mutate,
        create_if_missing=True,
        default_factory=lambda: _pages_meta_default(definition_path),
    )
    return {
        "success": True,
        "page_order": final_order,
        "pages_reordered": len(resolved_order),
        "pages_appended": len(remaining),
        "pages_json": str(pages_json_path),
    }


def rename_page(
    definition_path: Path,
    page_name: str,
    new_display_name: str,
) -> Dict[str, Any]:
    """Set a page's DISPLAY name (``page.json`` ``displayName``). The page id (folder name,
    references in bookmarks / drillthrough / pages.json) is unchanged. Duplicate display
    names are refused: ``page_name`` accepts a display name, so they would be ambiguous."""
    new_name = str(new_display_name or "").strip()
    if not new_name:
        return {
            "success": False,
            "error_type": "missing_argument",
            "error": "new_display_name is required (non-empty)",
        }
    if len(new_name) > 255:
        return {
            "success": False,
            "error_type": "invalid_argument",
            "error": "new_display_name must be at most 255 characters",
        }
    page_folder, path, err = _page(definition_path, page_name)
    if err:
        return err
    for other in (Path(definition_path) / "pages").iterdir():
        if not other.is_dir() or other.name == page_folder.name:
            continue
        other_json = other / "page.json"
        data = load_json_file(other_json) if other_json.exists() else None
        if isinstance(data, dict) and str(data.get("displayName") or "") == new_name:
            return {
                "success": False,
                "error_type": "duplicate_name",
                "error": f"Page '{other.name}' already has the display name '{new_name}'",
                "page_id": other.name,
            }
    out: Dict[str, Any] = {}

    def _mutate(page_data: Dict[str, Any]) -> None:
        out["old"] = page_data.get("displayName")
        if out["old"] == new_name:
            out["unchanged"] = True
            return
        page_data["displayName"] = new_name

    safe_io.update_json(path, _mutate)
    result: Dict[str, Any] = {
        "success": True,
        "page_id": page_folder.name,
        "old_display_name": out.get("old"),
        "new_display_name": new_name,
        "page_id_unchanged": True,
    }
    if out.get("unchanged"):
        result["unchanged"] = True
    return result


def resize_page(
    definition_path: Path,
    page_name: str,
    width: int = None,
    height: int = None,
) -> Dict[str, Any]:
    """Resize a page canvas. Updates page.json width/height."""
    if width is None and height is None:
        return {"success": False, "error": "At least one of width or height must be provided"}
    page_folder, path, err = _page(definition_path, page_name)
    if err:
        return err
    out: Dict[str, Any] = {}

    def _mutate(page_data: Dict[str, Any]) -> None:
        out["old"] = {"width": page_data.get("width", 1280), "height": page_data.get("height", 720)}
        if width is not None:
            page_data["width"] = width
        if height is not None:
            page_data["height"] = height
        out["new"] = {"width": page_data.get("width"), "height": page_data.get("height")}
        out["name"] = page_data.get("displayName", page_folder.name)

    safe_io.update_json(path, _mutate)
    return {
        "success": True,
        "page": out["name"],
        "old_dimensions": out["old"],
        "new_dimensions": out["new"],
    }


def set_display_options(
    definition_path: Path,
    page_name: str,
    display_option: str,
) -> Dict[str, Any]:
    """Set page display option. display_option: "FitToPage", "FitToWidth", or "ActualSize"."""
    option_value = _DISPLAY_OPTIONS.get(str(display_option).lower().replace("_", ""))
    if option_value is None:
        return {
            "success": False,
            "error": (
                f"Invalid display option: '{display_option}'. "
                f"Valid options: FitToPage, FitToWidth, ActualSize"
            ),
        }
    page_folder, path, err = _page(definition_path, page_name)
    if err:
        return err
    out: Dict[str, Any] = {}

    def _mutate(page_data: Dict[str, Any]) -> None:
        out["old"] = display_option_name(page_data.get("displayOption"))
        out["name"] = page_data.get("displayName", page_folder.name)
        page_data["displayOption"] = option_value

    safe_io.update_json(path, _mutate)
    return {
        "success": True,
        "page": out["name"],
        "old_display_option": out["old"],
        "new_display_option": option_value,
        "display_option_name": option_value,
    }


def _build_color_property(color: str) -> Dict[str, Any]:
    """Build a Power BI color property dict from a hex color string."""
    return {
        "solid": {
            "color": {"expr": {"Literal": {"Value": pbir_model.encode_literal(color, "string")}}}
        }
    }


def _build_transparency_property(transparency: float) -> Dict[str, Any]:
    """Build a Power BI transparency property dict (0-100 percent)."""
    return {"expr": {"Literal": {"Value": pbir_model.encode_literal(transparency, "double")}}}


def _set_page_object(
    definition_path: Path, page_name: str, card: str, color: Any, transparency: Any
) -> Dict[str, Any]:
    if color is None and transparency is None:
        return {"success": False, "error": "At least one of color or transparency must be provided"}
    page_folder, path, err = _page(definition_path, page_name)
    if err:
        return err
    out: Dict[str, Any] = {}

    def _mutate(page_data: Dict[str, Any]) -> None:
        objects = page_data.setdefault("objects", {})
        entries = objects.setdefault(card, [])
        if not entries:
            entries.append({})
        properties = entries[0].setdefault("properties", {})
        if color is not None:
            properties["color"] = _build_color_property(color)
        if transparency is not None:
            properties["transparency"] = _build_transparency_property(transparency)
        out["name"] = page_data.get("displayName", page_folder.name)

    safe_io.update_json(path, _mutate)
    return {"success": True, "page": out["name"]}


def set_page_background(
    definition_path: Path,
    page_name: str,
    color: str = None,
    transparency: float = None,
) -> Dict[str, Any]:
    """Set page background color/transparency. Updates page.json objects.background."""
    res = _set_page_object(definition_path, page_name, "background", color, transparency)
    if res.get("success"):
        res["background"] = {"color": color, "transparency": transparency}
    return res


def set_page_wallpaper(
    definition_path: Path,
    page_name: str,
    color: str = None,
    transparency: float = None,
) -> Dict[str, Any]:
    """Set page wallpaper (``objects.outspace`` — the area outside the canvas)."""
    res = _set_page_object(definition_path, page_name, "outspace", color, transparency)
    if res.get("success"):
        res["wallpaper"] = {"color": color, "transparency": transparency}
    return res


def _is_drillthrough_filter(f: Dict[str, Any]) -> bool:
    return f.get("howCreated") == "Drillthrough" or f.get("type") == "Drillthrough"


def set_drillthrough(
    definition_path: Path,
    page_name: str,
    table: str = None,
    field: str = None,
    clear: bool = False,
    hide_page: bool = False,
) -> Dict[str, Any]:
    """Configure or clear drillthrough on a page (real PBIR shape, H-04).

    Set: adds a Categorical filter ``howCreated: "Drillthrough"`` on
    ``table[field]``, a matching ``pageBinding.parameters[]`` entry
    (``type: "Drillthrough"``) and ``type: "Drillthrough"`` on the page.
    Clear: removes all three (other filters and ``filterSortOrder`` kept).
    """
    page_folder, path, err = _page(definition_path, page_name)
    if err:
        return err
    if not clear and (not table or not field):
        return {
            "success": False,
            "error": "Both 'table' and 'field' are required to set drillthrough (or use clear=True)",
        }
    out: Dict[str, Any] = {}

    def _mutate(page_data: Dict[str, Any]) -> None:
        out["name"] = page_data.get("displayName", page_folder.name)
        fc = page_data.get("filterConfig")
        filters = list((fc or {}).get("filters") or []) if isinstance(fc, dict) else []
        if clear:
            removed = [f.get("name") for f in filters if _is_drillthrough_filter(f)]
            kept = [f for f in filters if not _is_drillthrough_filter(f)]
            if isinstance(fc, dict):
                if kept:
                    fc["filters"] = kept
                else:
                    fc.pop("filters", None)
                    if not fc:
                        page_data.pop("filterConfig", None)
            binding = page_data.get("pageBinding")
            if isinstance(binding, dict) and binding.get("type") == "Drillthrough":
                page_data.pop("pageBinding", None)
            if page_data.get("type") == "Drillthrough":
                page_data.pop("type", None)
            page_data.pop("drillthrough", None)  # legacy invented key
            out["removed"] = removed
            return
        field_expr = {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": field}}
        existing = next(
            (f for f in filters if _is_drillthrough_filter(f) and f.get("field") == field_expr),
            None,
        )
        fname = existing.get("name") if existing else generate_visual_id()
        if existing is None:
            filters.append(
                {
                    "name": fname,
                    "field": field_expr,
                    "type": "Categorical",
                    "howCreated": "Drillthrough",
                }
            )
        else:
            existing["type"] = "Categorical"
            existing["howCreated"] = "Drillthrough"
        page_data.setdefault("filterConfig", {})["filters"] = filters
        binding = page_data.get("pageBinding")
        if not isinstance(binding, dict) or binding.get("type") != "Drillthrough":
            binding = {"name": generate_visual_id(), "type": "Drillthrough", "parameters": []}
            page_data["pageBinding"] = binding
        params = binding.setdefault("parameters", [])
        if not any(p.get("boundFilter") == fname for p in params if isinstance(p, dict)):
            params.append({"name": fname, "boundFilter": fname, "fieldExpr": field_expr})
        page_data["type"] = "Drillthrough"
        page_data.pop("drillthrough", None)
        if hide_page:
            page_data["visibility"] = HIDDEN_VISIBILITY
        out["filter_name"] = fname

    safe_io.update_json(path, _mutate)
    if clear:
        return {
            "success": True,
            "page": out["name"],
            "drillthrough": "cleared",
            "removed_filters": out.get("removed", []),
        }
    return {
        "success": True,
        "page": out["name"],
        "drillthrough": {"table": table, "field": field, "filter_name": out.get("filter_name")},
    }


def set_tooltip_page(
    definition_path: Path,
    page_name: str,
    enabled: bool = True,
    width: int = 320,
    height: int = 240,
) -> Dict[str, Any]:
    """Configure a page as a tooltip page (``type: "Tooltip"``, 320x240 default).

    ``enabled=False`` reverts to a normal page and restores the pre-tooltip
    canvas size recorded on enable.
    """
    page_folder, path, err = _page(definition_path, page_name)
    if err:
        return err
    out: Dict[str, Any] = {}

    def _mutate(page_data: Dict[str, Any]) -> None:
        out["name"] = page_data.get("displayName", page_folder.name)
        if enabled:
            if page_type(page_data) != "Tooltip":
                existing_w = page_data.get("width")
                existing_h = page_data.get("height")
                if existing_w is not None and existing_h is not None:
                    annotations = [
                        a
                        for a in (page_data.get("annotations") or [])
                        if a.get("name") != "MCP_prev_dimensions"
                    ]
                    annotations.append(
                        {"name": "MCP_prev_dimensions", "value": f"{existing_w}x{existing_h}"}
                    )
                    page_data["annotations"] = annotations
            page_data["type"] = "Tooltip"
            page_data.pop("pageType", None)  # legacy PBIX integer written by old versions
            page_data["width"] = width
            page_data["height"] = height
        else:
            if page_type(page_data) == "Tooltip":
                page_data.pop("type", None)
            page_data.pop("pageType", None)
            restored = False
            annotations = page_data.get("annotations") or []
            for a in list(annotations):
                if a.get("name") == "MCP_prev_dimensions":
                    try:
                        w_str, h_str = str(a.get("value", "")).split("x", 1)
                        page_data["width"] = int(w_str)
                        page_data["height"] = int(h_str)
                        restored = True
                    except (ValueError, AttributeError):
                        pass
                    annotations.remove(a)
            if annotations:
                page_data["annotations"] = annotations
            else:
                page_data.pop("annotations", None)
            if not restored:
                if page_data.get("width", 0) <= 640 and page_data.get("height", 0) <= 480:
                    page_data["width"] = 1280
                    page_data["height"] = 720
        out["width"] = page_data.get("width")
        out["height"] = page_data.get("height")

    safe_io.update_json(path, _mutate)
    return {
        "success": True,
        "page": out["name"],
        "tooltip": {"enabled": enabled, "width": out["width"], "height": out["height"]},
    }


def set_page_visibility(
    definition_path: Path,
    page_name: str,
    hidden: bool,
) -> Dict[str, Any]:
    """Hide (``visibility: "HiddenInViewMode"``) or show a page."""
    page_folder, path, err = _page(definition_path, page_name)
    if err:
        return err
    out: Dict[str, Any] = {}

    def _mutate(page_data: Dict[str, Any]) -> Any:
        out["name"] = page_data.get("displayName", page_folder.name)
        if hidden:
            if page_data.get("visibility") == HIDDEN_VISIBILITY:
                return SKIP_WRITE
            page_data["visibility"] = HIDDEN_VISIBILITY
        else:
            if "visibility" not in page_data:
                return SKIP_WRITE
            page_data.pop("visibility", None)
        return None

    safe_io.update_json(path, _mutate)
    return {"success": True, "page": out["name"], "hidden": hidden}
