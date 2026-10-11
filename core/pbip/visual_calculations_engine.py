"""
Visual Calculations Engine for PBIP Reports

Visual calculations are DAX expressions scoped to one visual's query. H-35
(deep review 2026-09-22): PBIR stores them as queryState PROJECTIONS::

    {"field": {"NativeVisualCalculation": {"Language": "dax",
                                           "Expression": "RUNNINGSUM([Sales])",
                                           "Name": "Running sales"}},
     "queryRef": "Running sales", "nativeQueryRef": "Running sales"}

(the old engine wrote an invented ``visual.visualCalculations.calculations``
block that Desktop ignores). This engine reads and writes the projection
shape; calculations found in the legacy block are still listed (flagged
``legacy_location``) and are migrated into a projection on update.

NEEDS LIVE CHECK: no visual calculation exists in the R0105/R0201 corpus, so
the exact key set Desktop writes (e.g. an extra ``displayName``) was taken
from the published PBIR semanticQuery schema — create one visual calc in
Desktop and diff its visual.json against ``add_calculation`` output.

No MCP awareness — pure domain logic operating on PBIP file structures.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.pbip.authoring import safe_io
from core.pbip.authoring.targets import resolve_page_dir, resolve_visual_dir
from core.utilities.pbip_utils import SKIP_WRITE, load_json_file, get_page_display_name

logger = logging.getLogger(__name__)

_NVC = "NativeVisualCalculation"


def _find_visual_folder(page_folder: Path, visual_name: str) -> Optional[Path]:
    vf, _err = resolve_visual_dir(page_folder, visual_name)
    return vf


def find_page_folder(definition_path: Path, page_name: str) -> Optional[Path]:
    folder, _err = resolve_page_dir(Path(definition_path), page_name)
    return folder


def _get_visual_type(visual_data: Dict[str, Any]) -> str:
    return visual_data.get("visual", {}).get("visualType", "unknown")


def _get_visual_title(visual_data: Dict[str, Any]) -> str:
    vco = visual_data.get("visual", {}).get("visualContainerObjects", {})
    for t in vco.get("title", []):
        val = (
            (((t.get("properties") or {}).get("text") or {}).get("expr") or {})
            .get("Literal", {})
            .get("Value", "")
        )
        if val:
            return val.strip("'")
    return ""


def _iter_calcs(visual_data: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any], Dict[str, Any]]]:
    """``[(role, projection, NativeVisualCalculation node)]`` of a visual."""
    out = []
    qs = ((visual_data.get("visual") or {}).get("query") or {}).get("queryState") or {}
    for role, payload in qs.items():
        for proj in (payload or {}).get("projections") or []:
            node = (proj.get("field") or {}).get(_NVC) if isinstance(proj, dict) else None
            if isinstance(node, dict):
                out.append((role, proj, node))
    return out


def _legacy_calcs(visual_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    block = (visual_data.get("visual") or {}).get("visualCalculations") or {}
    return [c for c in (block.get("calculations") or []) if isinstance(c, dict)]


def _default_role(visual_data: Dict[str, Any]) -> str:
    """Role that holds measures (first role with a Measure projection), else Y / Values."""
    qs = ((visual_data.get("visual") or {}).get("query") or {}).get("queryState") or {}
    for role, payload in qs.items():
        for proj in (payload or {}).get("projections") or []:
            if "Measure" in (proj.get("field") or {}):
                return role
    for cand in ("Y", "Values", "Data"):
        if cand in qs:
            return cand
    return "Values"


def _projection(name: str, expression: str) -> Dict[str, Any]:
    return {
        "field": {_NVC: {"Language": "dax", "Expression": expression, "Name": name}},
        "queryRef": name,
        "nativeQueryRef": name,
    }


def list_calculations(
    definition_path: Path,
    page_name: str = None,
    visual_name: str = None,
) -> Dict[str, Any]:
    """List visual calculations across visuals (projection shape + legacy block)."""
    pages_dir = definition_path / "pages"
    if not pages_dir.exists():
        return {"success": True, "calculations": [], "total_count": 0}
    if page_name:
        page_folder = find_page_folder(definition_path, page_name)
        if not page_folder:
            return {"success": False, "error": f"Page not found: '{page_name}'"}
        page_folders = [page_folder]
    else:
        page_folders = sorted(f for f in pages_dir.iterdir() if f.is_dir())

    calculations: List[Dict[str, Any]] = []
    for pf in page_folders:
        p_display_name = get_page_display_name(pf)
        visuals_dir = pf / "visuals"
        if not visuals_dir.exists():
            continue
        if visual_name:
            vf = _find_visual_folder(pf, visual_name)
            visual_folders = [vf] if vf else []
        else:
            visual_folders = sorted(f for f in visuals_dir.iterdir() if f.is_dir())
        for vf in visual_folders:
            visual_data = load_json_file(vf / "visual.json")
            if not visual_data:
                continue
            base = {
                "page": p_display_name,
                "page_id": pf.name,
                "visual_id": vf.name,
                "visual_type": _get_visual_type(visual_data),
                "visual_title": _get_visual_title(visual_data),
            }
            for role, _proj, node in _iter_calcs(visual_data):
                calculations.append(
                    {
                        **base,
                        "name": node.get("Name", ""),
                        "expression": node.get("Expression", ""),
                        "role": role,
                    }
                )
            for calc in _legacy_calcs(visual_data):
                calculations.append(
                    {
                        **base,
                        "name": calc.get("name", ""),
                        "expression": calc.get("expression", ""),
                        "legacy_location": "visual.visualCalculations (not read by Desktop)",
                    }
                )
    return {"success": True, "calculations": calculations, "total_count": len(calculations)}


def _visual_json(definition_path: Path, page_name: str, visual_name: str) -> Any:
    page_folder = find_page_folder(definition_path, page_name)
    if not page_folder:
        return None, None, {"success": False, "error": f"Page not found: '{page_name}'"}
    visual_folder = _find_visual_folder(page_folder, visual_name)
    if not visual_folder:
        return None, None, {"success": False, "error": f"Visual not found: '{visual_name}'"}
    return page_folder, visual_folder, None


def add_calculation(
    definition_path: Path,
    page_name: str,
    visual_name: str,
    name: str,
    expression: str,
    result_type: str = "double",
    *,
    bucket: Optional[str] = None,
) -> Dict[str, Any]:
    """Add a visual calculation as a ``NativeVisualCalculation`` projection."""
    if not name or not name.strip():
        return {"success": False, "error": "Calculation name is required"}
    if not expression or not expression.strip():
        return {"success": False, "error": "Calculation expression is required"}
    page_folder, visual_folder, err = _visual_json(definition_path, page_name, visual_name)
    if err:
        return err
    path = visual_folder / "visual.json"
    out: Dict[str, Any] = {}

    def _mutate(data: Dict[str, Any]) -> Any:
        if any(n.get("Name") == name for _r, _p, n in _iter_calcs(data)) or any(
            c.get("name") == name for c in _legacy_calcs(data)
        ):
            out["duplicate"] = True
            return SKIP_WRITE
        role = bucket or _default_role(data)
        qs = data.setdefault("visual", {}).setdefault("query", {}).setdefault("queryState", {})
        qs.setdefault(role, {}).setdefault("projections", []).append(_projection(name, expression))
        out["role"] = role
        return None

    safe_io.update_json(path, _mutate)
    if out.get("duplicate"):
        return {
            "success": False,
            "error": (
                f"Visual calculation '{name}' already exists on this visual. "
                f"Use update_calculation to modify it."
            ),
        }
    return {
        "success": True,
        "page": get_page_display_name(page_folder),
        "visual_id": visual_folder.name,
        "calculation": name,
        "expression": expression,
        "role": out.get("role"),
        "written_to": f"visual.query.queryState.{out.get('role')}.projections",
        "needs_live_check": (
            "shape from the PBIR semanticQuery schema; verify once against a visual "
            "calculation created in Desktop"
        ),
    }


def update_calculation(
    definition_path: Path,
    page_name: str,
    visual_name: str,
    name: str,
    expression: str = None,
) -> Dict[str, Any]:
    """Update a visual calculation's expression (a legacy-block calc is migrated)."""
    if expression is None:
        return {"success": False, "error": "Expression must be provided for update"}
    page_folder, visual_folder, err = _visual_json(definition_path, page_name, visual_name)
    if err:
        return err
    out: Dict[str, Any] = {}

    def _mutate(data: Dict[str, Any]) -> Any:
        for _role, _proj, node in _iter_calcs(data):
            if node.get("Name") == name:
                out["old"] = node.get("Expression", "")
                node["Expression"] = expression
                return None
        legacy = _legacy_calcs(data)
        for calc in legacy:
            if calc.get("name") == name:
                out["old"] = calc.get("expression", "")
                out["migrated"] = True
                block = data["visual"]["visualCalculations"]
                block["calculations"] = [c for c in legacy if c.get("name") != name]
                if not block["calculations"]:
                    data["visual"].pop("visualCalculations", None)
                role = _default_role(data)
                qs = data["visual"].setdefault("query", {}).setdefault("queryState", {})
                qs.setdefault(role, {}).setdefault("projections", []).append(
                    _projection(name, expression)
                )
                return None
        return SKIP_WRITE

    safe_io.update_json(visual_folder / "visual.json", _mutate)
    if "old" not in out:
        return {"success": False, "error": f"Visual calculation '{name}' not found on this visual"}
    result = {
        "success": True,
        "page": get_page_display_name(page_folder),
        "visual_id": visual_folder.name,
        "calculation": name,
        "old_expression": out["old"],
        "new_expression": expression,
    }
    if out.get("migrated"):
        result["migrated_from_legacy_location"] = True
    return result


def delete_calculation(
    definition_path: Path,
    page_name: str,
    visual_name: str,
    name: str,
) -> Dict[str, Any]:
    """Delete a visual calculation (projection or legacy block)."""
    page_folder, visual_folder, err = _visual_json(definition_path, page_name, visual_name)
    if err:
        return err
    out: Dict[str, Any] = {"removed": False, "remaining": 0}

    def _mutate(data: Dict[str, Any]) -> Any:
        qs = ((data.get("visual") or {}).get("query") or {}).get("queryState") or {}
        for payload in qs.values():
            projs = (payload or {}).get("projections")
            if not isinstance(projs, list):
                continue
            kept = [
                p for p in projs if ((p.get("field") or {}).get(_NVC) or {}).get("Name") != name
            ]
            if len(kept) != len(projs):
                payload["projections"] = kept
                out["removed"] = True
        legacy = _legacy_calcs(data)
        if any(c.get("name") == name for c in legacy):
            block = data["visual"]["visualCalculations"]
            block["calculations"] = [c for c in legacy if c.get("name") != name]
            if not block["calculations"]:
                data["visual"].pop("visualCalculations", None)
            out["removed"] = True
        out["remaining"] = len(_iter_calcs(data)) + len(_legacy_calcs(data))
        return None if out["removed"] else SKIP_WRITE

    safe_io.update_json(visual_folder / "visual.json", _mutate)
    if not out["removed"]:
        return {"success": False, "error": f"Visual calculation '{name}' not found on this visual"}
    return {
        "success": True,
        "page": get_page_display_name(page_folder),
        "visual_id": visual_folder.name,
        "deleted_calculation": name,
        "remaining_calculations": out["remaining"],
    }
