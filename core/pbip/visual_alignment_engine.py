"""
Visual Alignment Engine for PBIP Reports

Multi-visual alignment and distribution operations. Reads visual positions
from visual.json files, computes alignment targets or equal spacing, and
writes the updated positions back.

H-31 (deep review 2026-09-22): positions of grouped visuals are GROUP-LOCAL
(2,906 of 3,004 R0201 visuals are grouped). Alignment now converts every
visual to page-absolute coordinates (group chain offset), aligns there, and
converts back to each visual's own frame before writing. Writes go through
``safe_io.update_json`` (locked RMW) after the whole plan is computed.

No MCP awareness — pure domain logic operating on PBIP file structures.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.pbip.authoring import safe_io
from core.pbip.authoring.targets import absolute_offset, resolve_page_dir, resolve_visual_dir
from core.utilities.pbip_utils import load_json_file, get_page_display_name

logger = logging.getLogger(__name__)

# Supported alignment modes
_VALID_ALIGNMENTS = {"left", "right", "top", "bottom", "center_h", "center_v"}

# Supported distribution directions
_VALID_DIRECTIONS = {"horizontal", "vertical"}


def _find_visual_folder(page_folder: Path, visual_name: str) -> Optional[Path]:
    """Visual folder by id or EXACT title (unique)."""
    vf, _err = resolve_visual_dir(page_folder, visual_name)
    return vf


def find_page_folder(definition_path: Path, page_name: str) -> Optional[Path]:
    folder, _err = resolve_page_dir(Path(definition_path), page_name)
    return folder


def _load_visual_positions(
    page_folder: Path, visual_names: list
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Load visuals with their PAGE-ABSOLUTE position (``position``) + group offset.

    Each dict: folder, data, path, name, offset {x, y}, position (absolute copy).
    """
    visuals: List[Dict[str, Any]] = []
    errors: List[str] = []
    for name in visual_names:
        vf = _find_visual_folder(page_folder, name)
        if not vf:
            errors.append(f"Visual not found: '{name}'")
            continue
        visual_json_path = vf / "visual.json"
        visual_data = load_json_file(visual_json_path)
        if not visual_data:
            errors.append(f"Could not read visual.json for '{name}'")
            continue
        offset = absolute_offset(page_folder, visual_data)
        raw = dict(visual_data.get("position", {}) or {})
        absolute = dict(raw)
        absolute["x"] = (raw.get("x", 0) or 0) + offset["x"]
        absolute["y"] = (raw.get("y", 0) or 0) + offset["y"]
        visuals.append(
            {
                "folder": vf,
                "data": visual_data,
                "path": visual_json_path,
                "position": absolute,
                "offset": offset,
                "name": name,
            }
        )
    return visuals, errors


def _write_positions(visuals: List[Dict[str, Any]], axis: str) -> List[str]:
    """Persist the new ABSOLUTE ``_new`` coordinate of each visual in its own frame."""
    errors: List[str] = []
    for v in visuals:
        if "_new" not in v:
            continue
        local = v["_new"] - v["offset"][axis]

        def _mutate(data: Dict[str, Any], _local: float = local) -> None:
            data.setdefault("position", {})[axis] = _local

        try:
            safe_io.update_json(v["path"], _mutate)
        except safe_io.WriteError as e:
            errors.append(f"Failed to write {v['name']}: {e}")
    return errors


def align_visuals(
    definition_path: Path,
    page_name: str,
    visual_names: list,
    alignment: str,
) -> Dict[str, Any]:
    """Align visuals on a page.

    Reads positions, calculates alignment target, and updates positions.

    Args:
        definition_path: Path to the report's definition/ folder
        page_name: Page display name or page ID
        visual_names: List of visual names/IDs to align
        alignment: Alignment mode: left, right, top, bottom, center_h, center_v

    Returns:
        Dict with success status and alignment details
    """
    alignment_lower = alignment.lower().replace("-", "_")
    if alignment_lower not in _VALID_ALIGNMENTS:
        return {
            "success": False,
            "error": (
                f"Invalid alignment: '{alignment}'. "
                f"Valid options: {', '.join(sorted(_VALID_ALIGNMENTS))}"
            ),
        }

    if len(visual_names) < 2:
        return {"success": False, "error": "At least 2 visuals are required for alignment"}

    page_folder = find_page_folder(definition_path, page_name)
    if not page_folder:
        return {"success": False, "error": f"Page not found: '{page_name}'"}

    visuals, errors = _load_visual_positions(page_folder, visual_names)
    if errors:
        return {"success": False, "error": "; ".join(errors)}

    if len(visuals) < 2:
        return {"success": False, "error": "Need at least 2 valid visuals for alignment"}

    # Calculate alignment target
    changes: List[Dict[str, Any]] = []

    if alignment_lower == "left":
        target_x = min(v["position"].get("x", 0) for v in visuals)
        for v in visuals:
            old_x = v["position"].get("x", 0)
            v["_new"] = target_x
            changes.append({"visual": v["name"], "x": old_x, "new_x": target_x})

    elif alignment_lower == "right":
        target_right = max(
            v["position"].get("x", 0) + v["position"].get("width", 0) for v in visuals
        )
        for v in visuals:
            old_x = v["position"].get("x", 0)
            width = v["position"].get("width", 0)
            new_x = target_right - width
            v["_new"] = new_x
            changes.append({"visual": v["name"], "x": old_x, "new_x": new_x})

    elif alignment_lower == "top":
        target_y = min(v["position"].get("y", 0) for v in visuals)
        for v in visuals:
            old_y = v["position"].get("y", 0)
            v["_new"] = target_y
            changes.append({"visual": v["name"], "y": old_y, "new_y": target_y})

    elif alignment_lower == "bottom":
        target_bottom = max(
            v["position"].get("y", 0) + v["position"].get("height", 0) for v in visuals
        )
        for v in visuals:
            old_y = v["position"].get("y", 0)
            height = v["position"].get("height", 0)
            new_y = target_bottom - height
            v["_new"] = new_y
            changes.append({"visual": v["name"], "y": old_y, "new_y": new_y})

    elif alignment_lower == "center_h":
        # Align all visuals to the average horizontal center
        centers = [v["position"].get("x", 0) + v["position"].get("width", 0) / 2 for v in visuals]
        avg_center = sum(centers) / len(centers)
        for v in visuals:
            old_x = v["position"].get("x", 0)
            width = v["position"].get("width", 0)
            new_x = avg_center - width / 2
            v["_new"] = new_x
            changes.append({"visual": v["name"], "x": old_x, "new_x": new_x})

    elif alignment_lower == "center_v":
        # Align all visuals to the average vertical center
        centers = [v["position"].get("y", 0) + v["position"].get("height", 0) / 2 for v in visuals]
        avg_center = sum(centers) / len(centers)
        for v in visuals:
            old_y = v["position"].get("y", 0)
            height = v["position"].get("height", 0)
            new_y = avg_center - height / 2
            v["_new"] = new_y
            changes.append({"visual": v["name"], "y": old_y, "new_y": new_y})

    axis = "x" if alignment_lower in ("left", "right", "center_h") else "y"
    save_errors = _write_positions(visuals, axis)
    if save_errors:
        return {"success": False, "error": "; ".join(save_errors), "error_type": "partial_write"}

    return {
        "success": True,
        "page": get_page_display_name(page_folder),
        "alignment": alignment,
        "visuals_aligned": len(visuals),
        "coordinates": "page-absolute",
        "changes": changes,
    }


def distribute_visuals(
    definition_path: Path,
    page_name: str,
    visual_names: list,
    direction: str,
) -> Dict[str, Any]:
    """Evenly distribute visuals with equal spacing between them.

    Calculates equal gaps between visuals along the specified axis.
    The first and last visuals (in spatial order) stay in place; inner
    visuals are repositioned to create even spacing.

    Args:
        definition_path: Path to the report's definition/ folder
        page_name: Page display name or page ID
        visual_names: List of visual names/IDs to distribute
        direction: Distribution direction: horizontal, vertical

    Returns:
        Dict with success status and distribution details
    """
    direction_lower = direction.lower()
    if direction_lower not in _VALID_DIRECTIONS:
        return {
            "success": False,
            "error": (
                f"Invalid direction: '{direction}'. "
                f"Valid options: {', '.join(sorted(_VALID_DIRECTIONS))}"
            ),
        }

    if len(visual_names) < 3:
        return {
            "success": False,
            "error": "At least 3 visuals are required for distribution",
        }

    page_folder = find_page_folder(definition_path, page_name)
    if not page_folder:
        return {"success": False, "error": f"Page not found: '{page_name}'"}

    visuals, errors = _load_visual_positions(page_folder, visual_names)
    if errors:
        return {"success": False, "error": "; ".join(errors)}

    if len(visuals) < 3:
        return {
            "success": False,
            "error": "Need at least 3 valid visuals for distribution",
        }

    changes: List[Dict[str, Any]] = []

    if direction_lower == "horizontal":
        # Sort by x position
        visuals.sort(key=lambda v: v["position"].get("x", 0))

        # Calculate total width of all visuals
        total_visual_width = sum(v["position"].get("width", 0) for v in visuals)

        # Calculate total available space
        first_x = visuals[0]["position"].get("x", 0)
        last_x = visuals[-1]["position"].get("x", 0)
        last_width = visuals[-1]["position"].get("width", 0)
        total_span = (last_x + last_width) - first_x

        # Calculate equal gap
        total_gap = total_span - total_visual_width
        gap = total_gap / (len(visuals) - 1) if len(visuals) > 1 else 0

        # Position each visual
        current_x = first_x
        for v in visuals:
            old_x = v["position"].get("x", 0)
            v["_new"] = current_x
            changes.append({"visual": v["name"], "x": old_x, "new_x": current_x})
            current_x += v["position"].get("width", 0) + gap

    elif direction_lower == "vertical":
        # Sort by y position
        visuals.sort(key=lambda v: v["position"].get("y", 0))

        # Calculate total height of all visuals
        total_visual_height = sum(v["position"].get("height", 0) for v in visuals)

        # Calculate total available space
        first_y = visuals[0]["position"].get("y", 0)
        last_y = visuals[-1]["position"].get("y", 0)
        last_height = visuals[-1]["position"].get("height", 0)
        total_span = (last_y + last_height) - first_y

        # Calculate equal gap
        total_gap = total_span - total_visual_height
        gap = total_gap / (len(visuals) - 1) if len(visuals) > 1 else 0

        # Position each visual
        current_y = first_y
        for v in visuals:
            old_y = v["position"].get("y", 0)
            v["_new"] = current_y
            changes.append({"visual": v["name"], "y": old_y, "new_y": current_y})
            current_y += v["position"].get("height", 0) + gap

    axis = "x" if direction_lower == "horizontal" else "y"
    save_errors = _write_positions(visuals, axis)
    if save_errors:
        return {"success": False, "error": "; ".join(save_errors), "error_type": "partial_write"}

    return {
        "success": True,
        "page": get_page_display_name(page_folder),
        "direction": direction,
        "visuals_distributed": len(visuals),
        "gap": round(gap, 2),
        "changes": changes,
    }
