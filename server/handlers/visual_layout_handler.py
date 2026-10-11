"""Visual layout operations: list, update_position, replace_measure, sync_visual,
sync_column_widths, align, sync_formatting.

Extracted from visual_operations_handler.py. All helpers here are shared with
visual_config_handler.py via re-export (_find_visuals is the key coupling point).
"""

import copy
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.pbip.authoring import safe_io
from core.pbip.authoring.targets import (
    _title_of,
    find_visuals as _targets_find,
    resolve_page,
    resolve_targets,
)
from core.utilities.pbip_utils import load_json_file as _load_json_file
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared helpers (also imported by visual_config_handler)
# ---------------------------------------------------------------------------


def _get_visual_title(visual_data: Dict) -> Optional[str]:
    return _title_of(visual_data)


def _find_visuals(
    definition_path: Path,
    display_title: Optional[str] = None,
    visual_type: Optional[str] = None,
    visual_name: Optional[str] = None,
    page_name: Optional[str] = None,
    include_hidden: bool = True,
    *,
    page_exact: bool = False,
) -> List[Dict]:
    """Legacy list-returning finder (see ``core.pbip.authoring.targets``).

    ``page_name`` resolves id -> exact name -> unique prefix; only when
    ``page_exact`` is False does it fall back to a substring filter (reads).
    ``visual_name`` = visual id or exact title (L-05). Loads only the
    matched page(s) (H-30). Returns [] on an unresolvable page.
    """
    res = _targets_find(
        definition_path,
        page=page_name,
        visual=visual_name,
        display_title=display_title,
        visual_type=visual_type,
        include_hidden=include_hidden,
        allow_page_substring=not page_exact,
    )
    return res.get("visuals", []) if res.get("success") else []


def _save(file_path: Path, data: Dict) -> None:
    """Whole-file write through the authoring write layer (raises WriteError)."""
    safe_io.write_json(file_path, data)


# ---------------------------------------------------------------------------
# Layout-specific helpers
# ---------------------------------------------------------------------------


def _update_visual_position(
    visual_data: Dict,
    x: Optional[float] = None,
    y: Optional[float] = None,
    width: Optional[float] = None,
    height: Optional[float] = None,
    z: Optional[int] = None,
    parent_offset: Optional[Dict[str, float]] = None,
) -> Dict:
    position = visual_data.setdefault("position", {})
    offset = parent_offset or {"x": 0, "y": 0}
    if x is not None:
        position["x"] = x - offset["x"]
    if y is not None:
        position["y"] = y - offset["y"]
    if width is not None:
        position["width"] = width
    if height is not None:
        position["height"] = height
    if z is not None:
        position["z"] = z
    return visual_data


def _find_child_visuals(parent_name: str, visuals_path: Path) -> List[Dict]:
    children: List[Dict] = []
    if not visuals_path.exists():
        return children
    for visual_folder in visuals_path.iterdir():
        if not visual_folder.is_dir():
            continue
        visual_json_path = visual_folder / "visual.json"
        if not visual_json_path.exists():
            continue
        visual_data = _load_json_file(visual_json_path)
        if not visual_data:
            continue
        if visual_data.get("parentGroupName") == parent_name:
            children.append(
                {
                    "name": visual_data.get("name", visual_folder.name),
                    "path": visual_json_path,
                    "data": visual_data,
                }
            )
    return children


def _sync_visual_content(source_data: Dict, target_data: Dict, sync_position: bool = False) -> Dict:
    synced_data = copy.deepcopy(source_data)
    synced_data["name"] = target_data.get("name")
    if "parentGroupName" in target_data:
        synced_data["parentGroupName"] = target_data["parentGroupName"]
    elif "parentGroupName" in synced_data:
        del synced_data["parentGroupName"]
    if not sync_position and "position" in target_data:
        synced_data["position"] = target_data["position"]
    return synced_data


def _sync_column_widths_helper(source_data: Dict, target_data: Dict) -> Dict[str, Any]:
    updated_target = copy.deepcopy(target_data)
    source_column_widths = source_data.get("visual", {}).get("objects", {}).get("columnWidth", [])
    if not source_column_widths:
        return {
            "modified": False,
            "target_data": updated_target,
            "source_widths": None,
            "previous_widths": None,
            "reason": "Source visual has no columnWidth settings",
        }
    target_objects = updated_target.setdefault("visual", {}).setdefault("objects", {})
    previous_widths = target_objects.get("columnWidth", [])
    target_objects["columnWidth"] = copy.deepcopy(source_column_widths)
    return {
        "modified": True,
        "target_data": updated_target,
        "source_widths": source_column_widths,
        "previous_widths": previous_widths if previous_widths else None,
    }


def _replace_measure_in_visual(
    visual_data: Dict,
    source_entity: str,
    source_property: str,
    target_entity: str,
    target_property: str,
    new_display_name: Optional[str] = None,
) -> Dict[str, Any]:
    changes: List = []
    modified = False
    found: set = set()
    visual = visual_data.get("visual", {})
    query_state = visual.get("query", {}).get("queryState", {})
    for area_name, area_data in query_state.items():
        projections = area_data.get("projections", []) if isinstance(area_data, dict) else []
        for i, projection in enumerate(projections):
            measure = projection.get("field", {}).get("Measure", {})
            if not measure:
                continue
            expression = measure.get("Expression", {})
            source_ref = expression.get("SourceRef", {})
            current_entity = source_ref.get("Entity", "")
            current_property = measure.get("Property", "")
            if (
                current_entity.lower() == source_entity.lower()
                and current_property.lower() == source_property.lower()
            ):
                original_display_name = projection.get(
                    "displayName", projection.get("nativeQueryRef", "")
                )
                source_ref["Entity"] = target_entity
                measure["Property"] = target_property
                projection["queryRef"] = f"{target_entity}.{target_property}"
                projection["nativeQueryRef"] = target_property
                if new_display_name:
                    projection["displayName"] = new_display_name
                elif "displayName" not in projection:
                    projection["displayName"] = original_display_name or source_property
                changes.append(
                    {
                        "area": area_name,
                        "index": i,
                        "from": {
                            "entity": current_entity,
                            "property": current_property,
                            "display_name": original_display_name,
                        },
                        "to": {
                            "entity": target_entity,
                            "property": target_property,
                            "display_name": projection.get("displayName", target_property),
                        },
                    }
                )
                modified = True
                found.add((current_entity, current_property))
    # H-32: everything else that points at the measure — sortDefinition,
    # visual-level filterConfig filters, objects[*].selector.metadata
    # (per-measure formatting / CF), CF inputs, measure-bound titles.
    from core.pbip.cascade_rename import RenameSpec, retarget_field_refs

    found.add((source_entity, source_property))
    other_refs = 0
    for entity, prop in found:
        other_refs += retarget_field_refs(
            visual_data,
            RenameSpec("measure", prop, target_property, table=entity, new_table=target_entity),
        )
    if other_refs:
        modified = True
    return {"modified": modified, "changes": changes, "other_references_updated": other_refs}


# ---------------------------------------------------------------------------
# Op functions
# ---------------------------------------------------------------------------


def _op_get_roles_for_type(args: Dict[str, Any], definition_path: Path) -> Dict[str, Any]:
    """T1-C: return canonical data roles for a visual type plus an example
    same-type visual from the report.

    Removes the duplicated `pathlib.rglob` snippet that pbir-page-builder,
    pbi-visual-author, and autonomous-pbi-build all teach. The skill no
    longer has to read visual.json files manually to confirm the role
    name to bind to.

    Args:
        visual_type (required): the visual type string (e.g. "cardVisual").

    Returns:
        {success, visual_type, canonical_roles: [...], example_visual_id?,
         example_used_roles?, error?}
    """
    visual_type = args.get("visual_type")
    if not visual_type:
        return {
            "success": False,
            "error": "visual_type is required",
            "error_type": "invalid_args",
        }

    # 1) Canonical role list from the registry. None means "unknown type" —
    #    which is advisory only (the registry is a guard rail, not a schema).
    try:
        from core.pbip.authoring.visual_roles import (
            CATALOG_DOC_HINT,
            VISUAL_ROLES,
            unknown_type_warning,
        )
    except ImportError as e:  # pragma: no cover - import failure is exceptional
        return {
            "success": False,
            "error": f"visual_roles registry unavailable: {e}",
            "error_type": "registry_unavailable",
        }

    canonical = VISUAL_ROLES.get(visual_type)
    response: Dict[str, Any] = {
        "success": True,
        "visual_type": visual_type,
        "canonical_roles": list(canonical) if canonical is not None else None,
        "known_in_registry": canonical is not None,
    }
    if canonical is None:
        # Informative unknown path: name the doc that would carry the roles.
        response["warning"] = unknown_type_warning(visual_type)
        response["catalog_doc"] = CATALOG_DOC_HINT.format(visual_type=visual_type)

    # 2) Find an example visual of this type in the report (if any) so the
    #    caller can copy its `visual.objects` / role wiring verbatim.
    example_visual_id: Optional[str] = None
    example_used_roles: List[str] = []
    pages_dir = Path(definition_path) / "pages"
    if pages_dir.exists():
        for page_dir in pages_dir.iterdir():
            if not page_dir.is_dir():
                continue
            visuals_dir = page_dir / "visuals"
            if not visuals_dir.exists():
                continue
            for vdir in visuals_dir.iterdir():
                if not vdir.is_dir():
                    continue
                vj = _load_json_file(vdir / "visual.json")
                if not vj:
                    continue
                vis = vj.get("visual") or {}
                if vis.get("visualType") != visual_type:
                    continue
                # Found one. Extract its used role names.
                example_visual_id = vdir.name
                page_id = page_dir.name
                response["example_page_id"] = page_id
                response["example_visual_id"] = example_visual_id
                # The visual has query.queryState[role] = {...} per role.
                qs = (vis.get("query") or {}).get("queryState") or {}
                example_used_roles = sorted(qs.keys())
                response["example_used_roles"] = example_used_roles
                break
            if example_visual_id:
                break

    if canonical is None and example_visual_id is None:
        # Caller asked for an unknown type AND we couldn't find one — return
        # the list of known types so the agent can self-correct.
        response["known_types"] = sorted(VISUAL_ROLES.keys())
    return response


def _op_list(args: Dict[str, Any], definition_path: Path) -> Dict[str, Any]:
    res = _targets_find(
        definition_path,
        page=args.get("page_id") or args.get("page_name"),
        visual=args.get("visual_id") or args.get("visual_name"),
        display_title=args.get("display_title"),
        visual_type=args.get("visual_type"),
        include_hidden=args.get("include_hidden", True),
        allow_page_substring=True,
    )
    if not res.get("success"):
        return res
    visuals = res["visuals"]
    if not visuals:
        return {
            "success": True,
            "message": "No visuals found matching the criteria",
            "visuals": [],
            "count": 0,
        }

    # T1-E: include selector — merge extra fields into the condensed summary
    # so callers don't have to follow up with per-visual disk reads. When
    # summary_only=False, the full visual is already returned and include
    # is a no-op.
    include_raw = args.get("include") or []
    if isinstance(include_raw, str):
        include_raw = [include_raw]
    include = {str(x).lower() for x in include_raw}
    want_all = "all" in include

    summary_only = args.get("summary_only", True)
    page_match = res.get("page_match")
    if summary_only:
        condensed = []
        for v in visuals:
            c: Dict = {
                "display_title": v["display_title"],
                "page_name": v.get("page_name", ""),
                "page_id": v.get("page_id", ""),
                "visual_type": v["visual_type"],
                "visual_name": v["visual_name"],
                "position": v["position"],
            }
            if v.get("is_hidden"):
                c["is_hidden"] = True
            if v.get("parent_group"):
                c["parent_group"] = v["parent_group"]
            if include:
                vj = _load_json_file(Path(v["file_path"])) if v.get("file_path") else None
                vis_obj = (vj or {}).get("visual") or {}
                if want_all or "objects" in include:
                    c["objects"] = vis_obj.get("objects")
                if want_all or "vco" in include or "visualcontainerobjects" in include:
                    c["visualContainerObjects"] = vis_obj.get("visualContainerObjects")
                if want_all or "fields" in include:
                    # query.queryState.<role>.projections — high-level field bindings.
                    qs = (vis_obj.get("query") or {}).get("queryState") or {}
                    c["fields"] = {role: payload for role, payload in qs.items()}
                if want_all or "parent_group" in include:
                    c["parentGroupName"] = (vj or {}).get("parentGroupName")
                if want_all or "filters" in include:
                    from core.pbip.pbir_model import get_visual_filters

                    c["filters"] = get_visual_filters(vj or {})
            condensed.append(c)
        out = {
            "success": True,
            "message": f"Found {len(visuals)} visual(s) matching criteria",
            "visuals": condensed,
            "count": len(visuals),
            "summary_only": True,
            "page_match": page_match,
            "note": (
                "Positions are page-absolute (as shown in Power BI UI): group-local "
                "positions plus the parent group chain offset"
            ),
        }
        if not include:
            out["hint"] = (
                "Use summary_only=false for full details, or include=['objects','vCO',"
                "'fields','filters'] to selectively enrich the summary without the full "
                "file path payload."
            )
        return out
    clean = [{k: v for k, v in vis.items() if not k.startswith("_")} for vis in visuals]
    return {
        "success": True,
        "message": f"Found {len(visuals)} visual(s) matching criteria",
        "visuals": clean,
        "count": len(visuals),
        "page_match": page_match,
    }


def _op_update_position(args: Dict[str, Any], definition_path: Path) -> Dict[str, Any]:
    new_x = args.get("x")
    new_y = args.get("y")
    new_width = args.get("width")
    new_height = args.get("height")
    new_z = args.get("z")
    if all(v is None for v in [new_x, new_y, new_width, new_height, new_z]):
        return error_envelope(
            "missing_argument",
            "At least one position/size parameter is required: x, y, width, height, or z",
        )
    targets = resolve_targets(definition_path, args, write=True, op="update_position")
    if not targets.get("success"):
        return targets
    visuals = targets["visuals"]
    dry_run = bool(args.get("dry_run", False))
    changes: List = []
    errors: List = []
    for visual in visuals:
        file_path = Path(visual["file_path"])
        before_position = visual["position"].copy()
        after_position = before_position.copy()
        if new_x is not None:
            after_position["x"] = new_x
        if new_y is not None:
            after_position["y"] = new_y
        if new_width is not None:
            after_position["width"] = new_width
        if new_height is not None:
            after_position["height"] = new_height
        if new_z is not None:
            after_position["z"] = new_z
        position_changed = before_position != after_position
        bp = {k: before_position.get(k) for k in ("x", "y", "width", "height")}
        ap = {k: after_position.get(k) for k in ("x", "y", "width", "height")}
        base = {
            "display_title": visual["display_title"],
            "page_name": visual.get("page_name", ""),
            "visual_name": visual["visual_name"],
        }
        if dry_run:
            changes.append(
                {
                    **base,
                    "before": bp,
                    "after": ap,
                    "status": "would_change" if position_changed else "no_change",
                }
            )
            continue
        if not position_changed:
            changes.append({**base, "status": "no_change"})
            continue
        offset = visual.get("_parent_offset", {"x": 0, "y": 0})

        def _mutate(data: Dict, _offset: Dict = offset) -> None:
            _update_visual_position(
                data,
                x=new_x,
                y=new_y,
                width=new_width,
                height=new_height,
                z=new_z,
                parent_offset=_offset,
            )

        try:
            safe_io.update_json(file_path, _mutate)
            changes.append({**base, "before": bp, "after": ap, "status": "changed"})
        except safe_io.WriteError as e:
            errors.append({"file_path": str(file_path), "error": str(e)})
    n_changed = len([c for c in changes if c.get("status") in ["changed", "would_change"]])
    result: Dict = {
        "success": len(errors) == 0,
        "operation": "update_position",
        "dry_run": dry_run,
        "matched": targets["matched"],
        "message": f'{"Would modify" if dry_run else "Modified"} {n_changed} visual(s)',
        "changes": changes,
        "changes_count": len(changes),
        "coordinates": "page-absolute (converted to group-local for grouped visuals)",
    }
    if errors:
        result["errors"] = errors
        result["errors_count"] = len(errors)
        result["message"] += f" with {len(errors)} error(s)"
    return result


def _op_replace_measure(args: Dict[str, Any], definition_path: Path) -> Dict[str, Any]:
    source_entity = args.get("source_entity")
    source_property = args.get("source_property")
    target_entity = args.get("target_entity")
    target_property = args.get("target_property")
    new_display_name = args.get("new_display_name")
    dry_run = bool(args.get("dry_run", False))
    if not all([source_entity, source_property, target_entity, target_property]):
        return error_envelope(
            "missing_argument",
            "replace_measure requires: source_entity, source_property, "
            "target_entity, target_property",
        )
    res = _targets_find(
        definition_path,
        page=args.get("page_id") or args.get("page_name"),
        visual=args.get("visual_id") or args.get("visual_name"),
        display_title=args.get("display_title"),
        visual_type=args.get("visual_type"),
        include_hidden=args.get("include_hidden", True),
        allow_page_substring=False,
    )
    if not res.get("success"):
        return res
    visuals = res["visuals"]
    if not visuals:
        return {
            "success": True,
            "message": "No visuals found matching the criteria",
            "changes": [],
            "count": 0,
        }
    # Plan in memory first: only visuals that actually reference the source
    # measure count as "matched" (the measure itself is the selector).
    planned: List = []
    for visual in visuals:
        data = _load_json_file(Path(visual["file_path"]))
        if not data:
            continue
        probe = copy.deepcopy(data)
        res_v = _replace_measure_in_visual(
            probe,
            source_entity,
            source_property,
            target_entity,
            target_property,
            new_display_name,
        )
        if res_v["modified"]:
            planned.append((visual, res_v))
    allow_multiple = bool(args.get("allow_multiple", False) or args.get("match_all", False))
    if len(planned) > 1 and not allow_multiple and not dry_run:
        return {
            "success": False,
            "error_type": "multiple_matches",
            "error": (
                f"replace_measure would change {len(planned)} visuals. Pass "
                "allow_multiple=true to replace in all of them (or dry_run=true to preview)."
            ),
            "matched": len(planned),
            "matches": [
                {
                    "visual_name": v["visual_name"],
                    "page_id": v.get("page_id"),
                    "page_name": v.get("page_name"),
                    "display_title": v.get("display_title"),
                }
                for v, _ in planned[:50]
            ],
        }
    all_changes: List = []
    errors: List = []
    visuals_modified = 0
    for visual, res_v in planned:
        file_path = Path(visual["file_path"])
        record: Dict = {
            "display_title": visual["display_title"],
            "page_name": visual.get("page_name", ""),
            "visual_name": visual["visual_name"],
            "visual_type": visual["visual_type"],
            "measure_changes": res_v["changes"],
            "other_references_updated": res_v.get("other_references_updated", 0),
            "status": "would_change" if dry_run else "changed",
        }
        if not dry_run:

            def _mutate(data: Dict) -> None:
                _replace_measure_in_visual(
                    data,
                    source_entity,
                    source_property,
                    target_entity,
                    target_property,
                    new_display_name,
                )

            try:
                safe_io.update_json(file_path, _mutate)
                visuals_modified += 1
            except safe_io.WriteError as e:
                record["status"] = "error"
                errors.append({"file_path": str(file_path), "error": str(e)})
        else:
            visuals_modified += 1
        all_changes.append(record)
    verb = "Would replace" if dry_run else "Replaced"
    result: Dict = {
        "success": len(errors) == 0,
        "operation": "replace_measure",
        "dry_run": dry_run,
        "matched": len(planned),
        "message": f"{verb} measure in {visuals_modified} visual(s)",
        "source": {"entity": source_entity, "property": source_property},
        "target": {"entity": target_entity, "property": target_property},
        "changes": all_changes,
        "changes_count": len(all_changes),
    }
    if new_display_name:
        result["new_display_name"] = new_display_name
    if errors:
        result["errors"] = errors
        result["errors_count"] = len(errors)
        result["message"] += f" with {len(errors)} error(s)"
    return result


def _resolve_target_page_ids(definition_path: Path, target_pages: Any) -> Any:
    """``target_pages`` -> set of page ids (exact-first), or an error envelope."""
    if not target_pages:
        return None
    ids = set()
    for tp in target_pages:
        pres = resolve_page(definition_path, tp, allow_substring=False)
        if "error" in pres:
            return {"success": False, **pres}
        ids.update(p.name for p in pres["pages"])
    return ids


def _page_id_of(visual: Dict) -> Optional[str]:
    if visual.get("page_id"):
        return visual["page_id"]
    fp = visual.get("file_path")
    return Path(fp).parent.parent.parent.name if fp else None


def _op_sync_visual(args: Dict[str, Any], definition_path: Path) -> Dict[str, Any]:
    display_title = args.get("display_title")
    source_visual_name = args.get("source_visual_name")
    source_page = args.get("source_page")
    sync_position = args.get("sync_position", True)
    sync_children = args.get("sync_children", True)
    dry_run = args.get("dry_run", False)
    target_pages = args.get("target_pages")
    target_display_title = args.get("target_display_title")
    target_visual_type = args.get("target_visual_type")
    if not source_visual_name and not display_title:
        return {
            "success": False,
            "error_type": "invalid_args",
            "error": "sync_visual requires either source_visual_name or display_title parameter",
        }
    source_visuals = _find_visuals(
        definition_path,
        visual_name=source_visual_name,
        display_title=display_title if not source_visual_name else None,
        page_name=source_page,
        include_hidden=True,
        page_exact=True,
    )
    if not source_visuals:
        return {
            "success": False,
            "error_type": "visual_not_found",
            "error": (
                f"No source visual found matching: {source_visual_name or display_title}. "
                'Use operation "list" to see available visuals.'
            ),
        }
    if len(source_visuals) > 1:
        # Ambiguous: multiple matches (source_page resolves to ONE page, so
        # this is a duplicate title/id). Return a structured error with
        # candidates rather than silently syncing from the first match.
        return {
            "success": False,
            "error_type": "ambiguous_match",
            "error": (
                f"{len(source_visuals)} source visuals match "
                f"{source_visual_name or display_title!r}. Pass source_page (or a "
                "unique source_visual_name) to disambiguate."
            ),
            "candidates": [
                {
                    "visual_name": v.get("visual_name"),
                    "page_name": v.get("page_name"),
                    "display_title": v.get("display_title"),
                }
                for v in source_visuals
            ],
        }
    source_visual = source_visuals[0]
    source_page_name = source_visual.get("page_name", "")
    source_page_id = _page_id_of(source_visual)
    source_file_path = Path(source_visual["file_path"])
    source_visuals_path = source_file_path.parent.parent
    source_visual_id = source_visual.get("visual_name", "")
    source_data = _load_json_file(source_file_path)
    if not source_data:
        return error_envelope(
            "operation_failed",
            f"Failed to load source visual from: {source_file_path}",
        )
    target_page_ids = _resolve_target_page_ids(definition_path, target_pages)
    if isinstance(target_page_ids, dict):
        return target_page_ids
    source_children: List = []
    is_group = (
        "visualGroup" in source_data
        or source_data.get("visual", {}).get("visualType") == "SummarizeVisualContainer"
    )
    if is_group and sync_children:
        source_children = _find_child_visuals(source_visual_id, source_visuals_path)

    def _eligible(v: Dict) -> bool:
        pid = _page_id_of(v)
        if pid == source_page_id:
            return False
        return target_page_ids is None or pid in target_page_ids

    if target_display_title or target_visual_type:
        all_potential = _find_visuals(
            definition_path,
            display_title=target_display_title,
            visual_type=target_visual_type,
            include_hidden=True,
        )
    else:
        all_potential = _find_visuals(
            definition_path, visual_name=source_visual_id, include_hidden=True
        )
    target_visuals = [v for v in all_potential if _eligible(v)]
    if not target_visuals:
        hint = (
            ""
            if (target_display_title or target_visual_type)
            else " Tip: Use target_display_title or target_visual_type to match visuals by "
            "title/type instead of visual ID."
        )
        return {
            "success": True,
            "message": (
                f'Source visual found on page "{source_page_name}", but no matching visuals '
                f"found on other pages to sync to.{hint}"
            ),
            "source": {
                "visual_name": source_visual_id,
                "display_title": source_visual.get("display_title"),
                "visual_type": source_visual.get("visual_type"),
                "page": source_page_name,
                "is_group": is_group,
                "children_count": len(source_children) if is_group else 0,
            },
            "targets_found": 0,
        }
    changes: List = []
    errors: List = []
    for target_visual in target_visuals:
        target_file_path = Path(target_visual["file_path"])
        target_visuals_path = target_file_path.parent.parent
        target_page_name = target_visual.get("page_name", "")
        target_visual_id = target_visual.get("visual_name", "")
        target_data = _load_json_file(target_file_path)
        if not target_data:
            errors.append(
                {
                    "page": target_page_name,
                    "visual_name": target_visual_id,
                    "error": "Failed to load target visual",
                }
            )
            continue
        synced_data = _sync_visual_content(source_data, target_data, sync_position)
        record: Dict = {
            "page": target_page_name,
            "target_visual_name": target_visual_id,
            "target_display_title": target_visual.get("display_title"),
            "visual_type": target_visual.get("visual_type", "unknown"),
            "position_synced": sync_position,
            "children_synced": [],
            "status": "would_sync" if dry_run else "synced",
        }
        if not dry_run:
            try:
                _save(target_file_path, synced_data)
            except safe_io.WriteError as e:
                errors.append(
                    {"page": target_page_name, "visual_name": target_visual_id, "error": str(e)}
                )
                continue
        if is_group and sync_children and source_children:
            for source_child in source_children:
                child_name = source_child["name"]
                target_child_path = target_visuals_path / child_name / "visual.json"
                if not target_child_path.exists():
                    record["children_synced"].append(
                        {"name": child_name, "status": "skipped_not_found"}
                    )
                    continue
                target_child_data = _load_json_file(target_child_path)
                if not target_child_data:
                    record["children_synced"].append(
                        {"name": child_name, "status": "skipped_load_failed"}
                    )
                    continue
                synced_child = _sync_visual_content(
                    source_child["data"], target_child_data, sync_position
                )
                if dry_run:
                    record["children_synced"].append({"name": child_name, "status": "would_sync"})
                    continue
                try:
                    _save(target_child_path, synced_child)
                    record["children_synced"].append({"name": child_name, "status": "synced"})
                except safe_io.WriteError:
                    record["children_synced"].append({"name": child_name, "status": "save_failed"})
        changes.append(record)
    source_desc = source_visual.get("display_title") or source_visual_id
    verb = "Would sync" if dry_run else "Synced"
    result: Dict = {
        "success": len(errors) == 0,
        "operation": "sync_visual",
        "dry_run": dry_run,
        "message": (
            f'{verb} visual "{source_desc}" from "{source_page_name}" to {len(changes)} page(s)'
        ),
        "source": {
            "visual_name": source_visual_id,
            "display_title": source_visual.get("display_title"),
            "visual_type": source_visual.get("visual_type"),
            "page": source_page_name,
            "is_group": is_group,
            "children_count": len(source_children) if is_group else 0,
        },
        "target_matching": (
            {"by_display_title": target_display_title, "by_visual_type": target_visual_type}
            if (target_display_title or target_visual_type)
            else "by_visual_name"
        ),
        "sync_position": sync_position,
        "sync_children": sync_children,
        "changes": changes,
        "changes_count": len(changes),
    }
    if errors:
        result["errors"] = errors
        result["errors_count"] = len(errors)
        result["message"] += f" with {len(errors)} error(s)"
    return result


def _op_sync_column_widths(args: Dict[str, Any], definition_path: Path) -> Dict[str, Any]:
    display_title = args.get("display_title")
    source_visual_name = args.get("source_visual_name")
    source_page = args.get("source_page")
    dry_run = args.get("dry_run", False)
    target_pages = args.get("target_pages")
    target_display_title = args.get("target_display_title")
    target_visual_type = args.get("target_visual_type")
    if not source_visual_name and not display_title:
        return error_envelope(
            "missing_argument",
            "sync_column_widths requires either source_visual_name or display_title",
        )
    source_visuals = _find_visuals(
        definition_path,
        visual_name=source_visual_name,
        display_title=display_title if not source_visual_name else None,
        page_name=source_page,
        include_hidden=True,
        page_exact=True,
    )
    if not source_visuals:
        return error_envelope(
            "not_found",
            f"No source visual found matching: {source_visual_name or display_title}.",
        )
    if len(source_visuals) > 1:
        return {
            "success": False,
            "error_type": "ambiguous_match",
            "error": (
                f"{len(source_visuals)} source visuals match "
                f"{source_visual_name or display_title!r}; pass source_page / a unique id."
            ),
            "candidates": [
                {"visual_name": v.get("visual_name"), "page_name": v.get("page_name")}
                for v in source_visuals
            ],
        }
    source_visual = source_visuals[0]
    source_page_name = source_visual.get("page_name", "")
    source_page_id = _page_id_of(source_visual)
    source_file_path = Path(source_visual["file_path"])
    source_visual_id = source_visual.get("visual_name", "")
    source_data = _load_json_file(source_file_path)
    if not source_data:
        return error_envelope(
            "operation_failed",
            f"Failed to load source visual from: {source_file_path}",
        )
    source_column_widths = source_data.get("visual", {}).get("objects", {}).get("columnWidth", [])
    if not source_column_widths:
        return error_envelope("not_found", "Source visual has no columnWidth settings to sync.")
    target_page_ids = _resolve_target_page_ids(definition_path, target_pages)
    if isinstance(target_page_ids, dict):
        return target_page_ids
    if target_display_title or target_visual_type:
        all_potential = _find_visuals(
            definition_path,
            display_title=target_display_title,
            visual_type=target_visual_type,
            include_hidden=True,
        )
    else:
        source_visual_type = source_visual.get("visual_type", "pivotTable")
        all_potential = _find_visuals(
            definition_path, visual_type=source_visual_type, include_hidden=True
        )
    target_visuals = [
        v
        for v in all_potential
        if not (v.get("visual_name") == source_visual_id and _page_id_of(v) == source_page_id)
        and (target_page_ids is None or _page_id_of(v) in target_page_ids)
    ]
    if not target_visuals:
        return {
            "success": True,
            "message": (
                f'Source found on "{source_page_name}" with {len(source_column_widths)} width '
                "setting(s), but no matching target visuals found."
            ),
            "source": {
                "visual_name": source_visual_id,
                "display_title": source_visual.get("display_title"),
                "visual_type": source_visual.get("visual_type"),
                "page": source_page_name,
                "column_widths_count": len(source_column_widths),
            },
            "targets_found": 0,
            "hint": "Use target_display_title or target_visual_type to match target visuals.",
        }
    changes: List = []
    errors: List = []
    for target_visual in target_visuals:
        target_file_path = Path(target_visual["file_path"])
        target_page_name = target_visual.get("page_name", "")
        target_visual_id = target_visual.get("visual_name", "")
        target_data = _load_json_file(target_file_path)
        if not target_data:
            errors.append(
                {
                    "page": target_page_name,
                    "visual_name": target_visual_id,
                    "error": "Failed to load target visual",
                }
            )
            continue
        sync_result = _sync_column_widths_helper(source_data, target_data)
        record: Dict = {
            "page": target_page_name,
            "target_visual_name": target_visual_id,
            "target_display_title": target_visual.get("display_title"),
            "visual_type": target_visual.get("visual_type", "unknown"),
            "column_widths_synced": len(source_column_widths),
            "had_previous_widths": sync_result.get("previous_widths") is not None,
            "status": "would_sync" if dry_run else "synced",
        }
        if not sync_result["modified"]:
            record["status"] = "skipped"
            record["reason"] = sync_result.get("reason", "Unknown")
            changes.append(record)
            continue
        if not dry_run:
            widths = copy.deepcopy(source_column_widths)

            def _mutate(data: Dict, _w: Any = widths) -> None:
                data.setdefault("visual", {}).setdefault("objects", {})["columnWidth"] = _w

            try:
                safe_io.update_json(target_file_path, _mutate)
            except safe_io.WriteError as e:
                errors.append(
                    {"page": target_page_name, "visual_name": target_visual_id, "error": str(e)}
                )
                continue
        changes.append(record)
    source_desc = source_visual.get("display_title") or source_visual_id
    n_synced = len([c for c in changes if c.get("status") in ["synced", "would_sync"]])
    verb = "Would sync" if dry_run else "Synced"
    result: Dict = {
        "success": len(errors) == 0,
        "operation": "sync_column_widths",
        "dry_run": dry_run,
        "message": (
            f'{verb} column widths from "{source_desc}" on "{source_page_name}" to '
            f"{n_synced} visual(s)"
        ),
        "source": {
            "visual_name": source_visual_id,
            "display_title": source_visual.get("display_title"),
            "visual_type": source_visual.get("visual_type"),
            "page": source_page_name,
            "column_widths_count": len(source_column_widths),
        },
        "target_matching": (
            {"by_display_title": target_display_title, "by_visual_type": target_visual_type}
            if (target_display_title or target_visual_type)
            else "by_visual_type"
        ),
        "changes": changes,
        "changes_count": len(changes),
    }
    if errors:
        result["errors"] = errors
        result["errors_count"] = len(errors)
        result["message"] += f" with {len(errors)} error(s)"
    return result


def _op_align(args: Dict[str, Any], definition_path: Path) -> Dict[str, Any]:
    from core.pbip.visual_alignment_engine import align_visuals, distribute_visuals

    page = args.get("page_id") or args.get("page_name", "")
    if args.get("direction") and not args.get("alignment"):
        return distribute_visuals(
            definition_path=definition_path,
            page_name=page,
            visual_names=args.get("visual_names", []),
            direction=args["direction"],
        )
    return align_visuals(
        definition_path=definition_path,
        page_name=page,
        visual_names=args.get("visual_names", []),
        alignment=args.get("alignment", "left"),
    )


def _formatting_cards(section: Any, only: Optional[set]) -> Dict[str, Any]:
    """Formatting cards of an objects / vCO section, minus content cards (H-02)."""
    from core.pbip.authoring.formatting import PROTECTED_OBJECT_CARDS

    out: Dict[str, Any] = {}
    for card, entries in (section or {}).items():
        if card in PROTECTED_OBJECT_CARDS:
            continue
        if only is not None and card not in only:
            continue
        out[card] = copy.deepcopy(entries)
    return out


def _op_sync_formatting(args: Dict[str, Any], definition_path: Path) -> Dict[str, Any]:
    source_visual_name = args.get("source_visual_name")
    source_page = args.get("source_page")
    target_visual_name = args.get("target_visual_name")
    target_page = args.get("target_page")
    dry_run = args.get("dry_run", False)
    # P1-129 fix: previously accepted bulk sync (target_page alone) without
    # confirmation and also syncs `include_hidden=True` onto hidden
    # visuals — silent data loss. Require explicit
    # `confirm_bulk_sync=True` when target_visual_name is not set.
    confirm_bulk_sync = args.get("confirm_bulk_sync", False)
    include_hidden = args.get("include_hidden", False)
    formatting_types = args.get("formatting_types")
    only = set(formatting_types) if formatting_types else None
    if not source_visual_name or not source_page:
        return error_envelope(
            "missing_argument",
            "sync_formatting requires: source_visual_name, source_page",
        )
    if not target_visual_name and not target_page:
        return error_envelope(
            "missing_argument",
            "sync_formatting requires: target_visual_name and/or target_page",
        )
    if not target_visual_name and not confirm_bulk_sync:
        return {
            "success": False,
            "error": (
                "Bulk sync (target_page without target_visual_name) overwrites the "
                "formatting of every visual on the target page. Pass "
                "confirm_bulk_sync=true to proceed."
            ),
            "error_type": "confirmation_required",
        }
    source_visuals = _find_visuals(
        definition_path,
        visual_name=source_visual_name,
        page_name=source_page,
        include_hidden=True,
        page_exact=True,
    )
    if len(source_visuals) != 1:
        return {
            "success": False,
            "error_type": "visual_not_found" if not source_visuals else "ambiguous_match",
            "error": (
                f"Source visual not found (or not unique): {source_visual_name} on {source_page}"
            ),
        }
    source_visual = source_visuals[0]
    source_data = _load_json_file(Path(source_visual["file_path"]))
    if not source_data:
        return error_envelope("operation_failed", "Failed to load source visual.json")
    src_visual = source_data.get("visual", {}) or {}
    # H-14: chrome lives at visual.visualContainerObjects (legacy top-level read too).
    source_objects = _formatting_cards(src_visual.get("objects"), only)
    source_vc_objects = _formatting_cards(
        src_visual.get("visualContainerObjects") or source_data.get("visualContainerObjects"),
        only,
    )
    target_visuals = _find_visuals(
        definition_path,
        visual_name=target_visual_name,
        page_name=target_page,
        include_hidden=include_hidden,
        page_exact=True,
    )
    if not target_visuals:
        return error_envelope("not_found", "No target visuals found matching criteria")
    changes: List = []
    errors: List = []
    for target in target_visuals:
        if target["file_path"] == source_visual["file_path"]:
            continue
        file_path = Path(target["file_path"])
        record: Dict = {
            "display_title": target.get("display_title", ""),
            "page_name": target.get("page_name", ""),
            "visual_name": target["visual_name"],
            "visual_type": target["visual_type"],
            "synced_objects": sorted(source_objects),
            "synced_vc_objects": sorted(source_vc_objects),
            "status": "would_change" if dry_run else "changed",
        }
        if not dry_run:

            def _mutate(data: Dict) -> None:
                vis = data.setdefault("visual", {})
                if source_objects:
                    objs = vis.setdefault("objects", {})
                    objs.update(copy.deepcopy(source_objects))
                if source_vc_objects:
                    vco = vis.setdefault("visualContainerObjects", {})
                    legacy = data.pop("visualContainerObjects", None)
                    if isinstance(legacy, dict):
                        for k, v in legacy.items():
                            vco.setdefault(k, v)
                    vco.update(copy.deepcopy(source_vc_objects))

            try:
                safe_io.update_json(file_path, _mutate)
            except safe_io.WriteError as e:
                record["status"] = "error"
                errors.append({"file_path": str(file_path), "error": str(e)})
        changes.append(record)
    result: Dict = {
        "success": len(errors) == 0,
        "operation": "sync_formatting",
        "dry_run": dry_run,
        "source": {
            "visual_name": source_visual["visual_name"],
            "page_name": source_visual.get("page_name", ""),
        },
        "message": f'{"Would sync" if dry_run else "Synced"} formatting to {len(changes)} visual(s)',
        "note": (
            "Content cards (general / vega / data / selection / script / pages / "
            "bookmarks / image) are never synced — slicer selections, text and "
            "Deneb specs stay on each target."
        ),
        "changes": changes,
        "changes_count": len(changes),
    }
    if errors:
        result["errors"] = errors
        result["errors_count"] = len(errors)
    return result
