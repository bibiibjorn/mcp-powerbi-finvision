"""Canonical create_visual, create_visual_group, delete_visual, hide/unhide, templates.

Both 07_Visual_Operations and 11_PBIP_Authoring delegate here — single source of truth.

Deep review 2026-09-22 (WP9):
* create (H-26 / L-05): roles default per visual type (the handler no longer
  forces ``Values`` / ``Category``), bindings are validated against
  ``VISUAL_ROLES``, ``measures`` / ``columns`` accept ``"Table.Field"`` /
  ``"'Table'[Field]"`` strings as well as objects, ``parent_group`` must be
  an existing group on the page and ``position`` is PAGE-ABSOLUTE (converted
  to group-local under a parent group, like pbir_add_visuals).
* delete (H-25): exact visual ids only; a slicer / filtered visual needs
  ``confirm_delete_filter_layer=true`` and the refusal dumps its filter
  state; severity is the same in both tools (DESTRUCTIVE for one visual,
  CATASTROPHIC for several / a group with children).
* hide / unhide: the non-destructive alternative — top-level ``isHidden``.
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.autonomous.gating import Severity
from core.pbip.authoring import safe_io
from core.pbip.authoring.pbir_executor import write_visual
from core.pbip.authoring.targets import (
    absolute_offset,
    resolve_page_dir,
    resolve_visual_dir,
)
from core.utilities.pbip_utils import SKIP_WRITE, load_json_file
from server.handlers._authoring_utils import coerce_num
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)

_BRACKET_REF = re.compile(r"^\s*'?(?P<table>(?:[^'\[]|'')+?)'?\s*\[(?P<field>[^\]]+)\]\s*$")


def _resolve_page_dir(definition_path: Path, page_id: str):
    page_dir, _err = resolve_page_dir(Path(definition_path), page_id)
    return page_dir


def parse_field_ref(item: Any, key: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """``{table, <key>, bucket?, display_name?}`` from an object OR a string (L-05).

    Strings: ``"Table.Field"`` (split at the FIRST dot — measure names may
    contain dots) or ``"'Table'[Field]"`` / ``"Table[Field]"``.
    """
    if isinstance(item, dict):
        if not item.get("table") or not item.get(key):
            return None, f"{key} binding needs 'table' and '{key}': {item!r}"
        return item, None
    if isinstance(item, str):
        m = _BRACKET_REF.match(item)
        if m:
            return {"table": m.group("table").replace("''", "'"), key: m.group("field")}, None
        if "." in item:
            table, _, field = item.partition(".")
            if table and field:
                return {"table": table, key: field}, None
    return None, f"cannot parse {key} reference {item!r} (use 'Table.Field' or {{table, {key}}})"


def op_create_visual(args: Dict[str, Any], definition_path: Path) -> Dict[str, Any]:
    from core.pbip.authoring.visual_builder import VisualBuilder
    from core.pbip.authoring.visual_roles import validate_bindings_ex

    page_id = args.get("page_id") or args.get("page_name")
    if not page_id:
        return error_envelope("missing_argument", "page_id or page_name is required")
    visual_type = args.get("visual_type")
    if not visual_type:
        return error_envelope("missing_argument", "visual_type is required")

    page_dir, err = resolve_page_dir(Path(definition_path), page_id)
    if err:
        err["error"] = f"Page not found: {page_id} ({err.get('error')})"
        return err

    parent_group = args.get("parent_group")
    group_offset = {"x": 0.0, "y": 0.0}
    if parent_group:
        gdir, gerr = resolve_visual_dir(page_dir, parent_group)
        if gerr:
            return {
                "success": False,
                "error_type": "parent_group_not_found",
                "error": f"parent_group {parent_group!r} not found on page {page_dir.name}",
            }
        gdata = load_json_file(gdir / "visual.json") or {}
        if "visualGroup" not in gdata:
            return {
                "success": False,
                "error_type": "not_a_group",
                "error": f"parent_group {parent_group!r} is not a visual group",
            }
        parent_group = gdir.name
        gpos = gdata.get("position") or {}
        up = absolute_offset(page_dir, gdata)
        group_offset = {
            "x": up["x"] + float(gpos.get("x", 0) or 0),
            "y": up["y"] + float(gpos.get("y", 0) or 0),
        }

    builder = VisualBuilder(visual_type)
    pos = args.get("position", {}) or {}
    if pos:
        # Coerce numeric position fields at the handler boundary — passing
        # {"x": "100"} should not write the string literal into visual.json.
        try:
            x = coerce_num(pos.get("x"), "position.x", 0)
            y = coerce_num(pos.get("y"), "position.y", 0)
            if args.get("position_frame", "page") != "group":
                x -= group_offset["x"]
                y -= group_offset["y"]
            builder.position(
                x=x,
                y=y,
                width=coerce_num(pos.get("width"), "position.width", 300),
                height=coerce_num(pos.get("height"), "position.height", 200),
                z=coerce_num(pos.get("z"), "position.z", 0),
            )
        except ValueError as e:
            return error_envelope("operation_failed", str(e))
    if args.get("title"):
        builder.set_title(args["title"])
    roles: List[str] = []
    for raw in args.get("measures", []) or []:
        m, perr = parse_field_ref(raw, "measure")
        if perr:
            return {"success": False, "error_type": "invalid_binding", "error": perr}
        builder.add_measure(
            table=m["table"],
            measure=m["measure"],
            bucket=m.get("bucket"),
            display_name=m.get("display_name"),
        )
    for raw in args.get("columns", []) or []:
        c, perr = parse_field_ref(raw, "column")
        if perr:
            return {"success": False, "error_type": "invalid_binding", "error": perr}
        builder.add_column(
            table=c["table"],
            column=c["column"],
            bucket=c.get("bucket"),
            display_name=c.get("display_name"),
        )
    roles = list(builder._projections.keys())
    role_type = "cardVisual" if visual_type == "card" else visual_type
    ok, role_errors, role_warnings = validate_bindings_ex(role_type, roles)
    if not ok:
        return {
            "success": False,
            "error_type": "invalid_role",
            "error": "role validation failed",
            "role_errors": role_errors,
        }
    if parent_group:
        builder.in_group(parent_group)
    for fmt in args.get("formatting", []) or []:
        builder.set_formatting(
            config_type=fmt.get("config_type", ""),
            property_name=fmt.get("property_name", ""),
            value=fmt.get("value"),
        )

    try:
        visual_dict = builder.build()
    except KeyError as e:
        return {"success": False, "error_type": "unknown_visual_type", "error": str(e)}
    visual_folder = write_visual(page_dir / "visuals", visual_dict)
    result: Dict[str, Any] = {
        "success": True,
        "operation": "create_visual",
        "visual_id": visual_dict["name"],
        "visual_type": visual_type,
        "written_visual_type": (visual_dict.get("visual") or {}).get("visualType"),
        "page_id": page_dir.name,
        "path": str(visual_folder),
        "roles": {r: len(p) for r, p in builder._projections.items()},
    }
    if parent_group:
        result["parent_group"] = parent_group
        result["position_frame"] = "converted page-absolute -> group-local"
    if role_warnings:
        result["role_warnings"] = role_warnings
    if builder.notices:
        result["notices"] = builder.notices
    return result


def op_create_visual_group(args: Dict[str, Any], definition_path: Path) -> Dict[str, Any]:
    from core.pbip.authoring.visual_builder import VisualBuilder

    page_id = args.get("page_id") or args.get("page_name")
    if not page_id:
        return error_envelope("missing_argument", "page_id or page_name is required")
    page_dir, err = resolve_page_dir(Path(definition_path), page_id)
    if err:
        err["error"] = f"Page not found: {page_id} ({err.get('error')})"
        return err

    group_name = args.get("group_name", "Visual Group")
    builder = VisualBuilder("visualGroup")
    pos = args.get("position", {})
    if pos:
        try:
            builder.position(
                x=coerce_num(pos.get("x"), "position.x", 0),
                y=coerce_num(pos.get("y"), "position.y", 0),
                width=coerce_num(pos.get("width"), "position.width", 400),
                height=coerce_num(pos.get("height"), "position.height", 300),
                z=coerce_num(pos.get("z"), "position.z", 0),
            )
        except ValueError as e:
            return error_envelope("operation_failed", str(e))
    builder.set_group_name(group_name)
    visual_dict = builder.build()
    visual_folder = write_visual(page_dir / "visuals", visual_dict)
    return {
        "success": True,
        "operation": "create_visual_group",
        "visual_id": visual_dict["name"],
        "group_name": group_name,
        "page_id": page_dir.name,
        "path": str(visual_folder),
    }


def _delete_ids(args: Dict[str, Any]) -> List[str]:
    ids = args.get("visual_ids")
    if isinstance(ids, list) and ids:
        return [str(i) for i in ids]
    one = args.get("visual_id") or args.get("visual_name")
    return [str(one)] if one else []


def delete_severity(args: Dict[str, Any], definition_path: Any) -> Optional[Severity]:
    """H-25: one rating for BOTH tools — CATASTROPHIC when bulk, else DESTRUCTIVE.

    Bulk = several visual ids, or a group whose children would be deleted
    with it (``delete_children`` defaults to true).
    """
    ids = _delete_ids(args)
    if len(ids) > 1:
        return Severity.CATASTROPHIC
    if not ids:
        return Severity.DESTRUCTIVE
    try:
        from core.utilities.pbip_utils import find_definition_folder

        defn = Path(str(definition_path))
        if defn.name != "definition":
            defn = find_definition_folder(str(definition_path)) or defn
        page_dir, err = resolve_page_dir(defn, args.get("page_id") or args.get("page_name"))
        if err or not safe_io.is_safe_segment(ids[0]):
            return Severity.DESTRUCTIVE
        vroot = page_dir / "visuals"
        if args.get("delete_children", True):
            for v in vroot.iterdir():
                data = load_json_file(v / "visual.json") if v.is_dir() else None
                if data and data.get("parentGroupName") == ids[0]:
                    return Severity.CATASTROPHIC
    except Exception:  # pragma: no cover - defensive: never block on the probe
        return Severity.DESTRUCTIVE
    return Severity.DESTRUCTIVE


def op_delete_visual(args: Dict[str, Any], definition_path: Path) -> Dict[str, Any]:
    from core.pbip.authoring.clone_engine import CloneEngine

    page_id = args.get("page_id") or args.get("page_name")
    ids = _delete_ids(args)
    if not page_id:
        return error_envelope("missing_argument", "page_id or page_name is required")
    if not ids:
        return error_envelope(
            "missing_argument",
            "visual_id (or visual_ids / visual_name) is required",
        )

    result = CloneEngine().delete_visual(
        definition_path=definition_path,
        page_id=page_id,
        visual_id=ids if len(ids) > 1 else ids[0],
        delete_children=args.get("delete_children", True),
        confirm_delete_filter_layer=bool(args.get("confirm_delete_filter_layer", False)),
    )
    result["operation"] = "delete_visual"
    return result


def op_set_hidden(args: Dict[str, Any], definition_path: Path, hidden: bool) -> Dict[str, Any]:
    """hide / unhide: TOP-LEVEL ``isHidden`` on visual.json (the safe alternative to delete)."""
    page_id = args.get("page_id") or args.get("page_name")
    ids = _delete_ids(args)
    if not page_id or not ids:
        return error_envelope(
            "missing_argument",
            f"{'hide' if hidden else 'unhide'} requires page_name and visual_name/visual_id",
        )
    page_dir, err = resolve_page_dir(Path(definition_path), page_id)
    if err:
        return err
    changed: List[str] = []
    unchanged: List[str] = []
    for ref in ids:
        vdir, verr = resolve_visual_dir(page_dir, ref)
        if verr:
            return verr

        def _mutate(data: Dict[str, Any]) -> Any:
            vg = data.get("visualGroup")
            if isinstance(vg, dict):
                vg.pop("isHidden", None)  # legacy invalid location (H-25)
            if hidden:
                if data.get("isHidden") is True:
                    return SKIP_WRITE if not isinstance(vg, dict) else None
                data["isHidden"] = True
            else:
                if "isHidden" not in data:
                    return SKIP_WRITE
                data.pop("isHidden", None)
            return None

        if safe_io.update_json(vdir / "visual.json", _mutate):
            changed.append(vdir.name)
        else:
            unchanged.append(vdir.name)
    return {
        "success": True,
        "operation": "hide" if hidden else "unhide",
        "page_id": page_dir.name,
        "changed": changed,
        "unchanged": unchanged,
        "note": "isHidden is written at the TOP LEVEL of visual.json (never inside visualGroup)",
    }


def op_hide_visual(args: Dict[str, Any], definition_path: Path) -> Dict[str, Any]:
    return op_set_hidden(args, definition_path, True)


def op_unhide_visual(args: Dict[str, Any], definition_path: Path) -> Dict[str, Any]:
    return op_set_hidden(args, definition_path, False)


def op_list_templates(args: Dict[str, Any], definition_path: Any) -> Dict[str, Any]:
    from core.pbip.authoring.visual_templates import get_template_catalog

    catalog = get_template_catalog()
    return {
        "success": True,
        "operation": "list_templates",
        "templates": catalog,
        "count": len(catalog),
    }


def op_get_template(args: Dict[str, Any], definition_path: Any) -> Dict[str, Any]:
    from core.pbip.authoring.visual_templates import get_template, TEMPLATE_REGISTRY

    visual_type = args.get("visual_type")
    if not visual_type:
        return error_envelope("missing_argument", "visual_type is required")
    if visual_type not in TEMPLATE_REGISTRY:
        return error_envelope(
            "invalid_argument",
            f"Unknown visual type: '{visual_type}'. "
            f"Available: {', '.join(sorted(TEMPLATE_REGISTRY.keys()))}",
        )
    return {
        "success": True,
        "operation": "get_template",
        "visual_type": visual_type,
        "template": get_template(visual_type),
    }
