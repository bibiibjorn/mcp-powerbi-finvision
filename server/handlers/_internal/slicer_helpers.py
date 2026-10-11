"""
INTERNAL HELPER — Not a registered MCP tool.
Provides helper functions consumed by active handlers.

Slicer/visual interaction operations consumed by visual_operations_handler.py
and page_operations_handler.py.

Deep review 2026-09-22 (WP9):
* slicer state is read with ``core.pbip.pbir_model`` (inverted polarity lives
  on the filterConfig entry, H-10) and reported as ``polarity``;
* ``_find_slicers(page=...)`` resolves the page first and only reads that
  page's visuals (H-30);
* interactions are read/written under ``visualInteractions`` with the type
  names Desktop writes (``NoFilter`` / ``DataFilter`` / ``HighlightFilter`` /
  ``Default``); ``Filter`` / ``Highlight`` are accepted as aliases (H-28);
* interaction pages resolve exact-first and an ambiguous visual title is an
  error instead of "last match wins" (H-28).
"""

from typing import Dict, Any, List, Optional
import logging
from pathlib import Path

from core.pbip.authoring import safe_io
from core.pbip.authoring.targets import (
    _title_of,
    absolute_offset,
    load_page_visuals,
    resolve_page,
    resolve_page_dir,
)
from core.pbip import pbir_model
from core.utilities.pbip_utils import (
    load_json_file as _load_json_file,
    get_page_display_name as _get_page_display_name,
)
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)

# Visual types that are slicers (standard, advanced/button, list, text, input).
SLICER_VISUAL_TYPES = {
    "slicer",
    "advancedSlicerVisual",
    "listSlicer",
    "textSlicer",
    "inputSlicer",
    "filterSlicer",
    "buttonSlicer",
}

#: Interaction types Desktop writes in page.json visualInteractions.
INTERACTION_TYPES = {"NoFilter", "DataFilter", "HighlightFilter", "Default"}
#: Friendly aliases accepted from callers (older docs used these names).
INTERACTION_ALIASES = {"Filter": "DataFilter", "Highlight": "HighlightFilter", "None": "NoFilter"}


def normalize_interaction_type(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    if value in INTERACTION_TYPES:
        return value
    return INTERACTION_ALIASES.get(value)


def _lit_value(prop: Any) -> Optional[str]:
    if not isinstance(prop, dict):
        return None
    value = ((prop.get("expr") or {}).get("Literal") or {}).get("Value")
    return value if isinstance(value, str) else None


def _bound_column(visual: Dict) -> Optional[Dict[str, Any]]:
    query_state = (visual.get("query") or {}).get("queryState") or {}
    values_projections = (query_state.get("Values") or {}).get("projections") or []
    if not values_projections:
        return None
    field_info = values_projections[0].get("field") or {}
    column_info = field_info.get("Column") or {}
    if not column_info:
        return None
    return {
        "projection": values_projections[0],
        "entity": ((column_info.get("Expression") or {}).get("SourceRef") or {}).get("Entity", ""),
        "property": column_info.get("Property", ""),
    }


def slicer_filter_entries(visual_data: Dict, entity: str, prop: str) -> List[Dict[str, Any]]:
    """filterConfig filters of this visual on the slicer's own bound column."""
    out = []
    for f in pbir_model.get_visual_filters(visual_data):
        col = (f.get("field") or {}).get("Column") or {}
        ent = ((col.get("Expression") or {}).get("SourceRef") or {}).get("Entity")
        if ent == entity and col.get("Property") == prop:
            out.append(f)
    return out


def slicer_polarity(visual_data: Dict, entity: str, prop: str) -> str:
    """'exclude' when the slicer is in inverted selection mode (filter-level or visual-level)."""
    for f in slicer_filter_entries(visual_data, entity, prop):
        if pbir_model.filter_polarity(f, visual_data) == "exclude":
            return "exclude"
    sel = pbir_model.get_slicer_selection_filter(visual_data)
    if sel is not None and pbir_model.filter_polarity(sel, visual_data) == "exclude":
        return "exclude"
    visual = visual_data.get("visual") or {}
    data_cfg = (visual.get("objects") or {}).get("data") or [{}]
    props = (data_cfg[0] or {}).get("properties", {}) if data_cfg else {}
    if _lit_value(props.get("isInvertedSelectionMode")) == "true":
        return "exclude"
    return "include"


def _extract_slicer_info(visual_data: Dict, file_path: Path) -> Optional[Dict]:
    """Extract slicer information from visual.json"""
    visual = visual_data.get("visual", {})

    visual_type = visual.get("visualType", "")
    if visual_type not in SLICER_VISUAL_TYPES:
        return None

    bound = _bound_column(visual)
    if not bound:
        return None
    entity = bound["entity"]
    property_name = bound["property"]
    projection = bound["projection"]

    raw_title = _title_of(visual_data)
    display_name = (
        raw_title or projection.get("displayName") or projection.get("nativeQueryRef") or ""
    )
    if not display_name:
        display_name = property_name

    objects = visual.get("objects", {}) or {}
    selection = objects.get("selection") or [{}]
    selection_props = (selection[0] or {}).get("properties", {}) if selection else {}

    single_select = _lit_value(selection_props.get("singleSelect")) == "true"
    strict_single_select = _lit_value(selection_props.get("strictSingleSelect")) == "true"
    select_all_checkbox = _lit_value(selection_props.get("selectAllCheckboxEnabled")) == "true"
    polarity = slicer_polarity(visual_data, entity, property_name)
    is_inverted_selection = polarity == "exclude"

    current_filter = pbir_model.get_slicer_selection_filter(visual_data)
    fc_entries = slicer_filter_entries(visual_data, entity, property_name)
    source_filter = current_filter
    if source_filter is None:
        for f in fc_entries:
            if isinstance(f.get("filter"), dict):
                source_filter = f["filter"]
                break
    selected_values: List[str] = []
    rows = pbir_model.get_filter_in_values(source_filter) if source_filter else None
    for value_list in rows or []:
        for value_item in value_list if isinstance(value_list, list) else []:
            lit = ((value_item or {}).get("Literal") or {}).get("Value")
            if isinstance(lit, str):
                value, _kind = pbir_model.decode_literal(lit)
                selected_values.append(value if isinstance(value, str) else lit)

    if is_inverted_selection and (single_select or strict_single_select):
        selection_mode = "single_select_all"  # Single select with "All" selected
    elif single_select or strict_single_select:
        selection_mode = "single_select"
    else:
        selection_mode = "multi_select"

    try:
        rel_path = str(file_path.relative_to(file_path.parent.parent.parent.parent.parent))
    except ValueError:
        rel_path = str(file_path)

    MAX_SELECTED_VALUES = 5
    total_selected_count = len(selected_values)

    pos_raw = visual_data.get("position", {}) or {}
    position = {
        "x": pos_raw.get("x"),
        "y": pos_raw.get("y"),
        "width": pos_raw.get("width"),
        "height": pos_raw.get("height"),
        "z": pos_raw.get("z"),
    }

    return {
        "file_path": str(file_path),
        "relative_path": rel_path,
        "visual_name": visual_data.get("name", ""),
        "display_name": display_name,
        "entity": entity,
        "property": property_name,
        "field_reference": f"{entity}[{property_name}]",
        "selection_mode": selection_mode,
        "single_select": single_select,
        "strict_single_select": strict_single_select,
        "select_all_checkbox": select_all_checkbox,
        "is_inverted_selection": is_inverted_selection,
        # H-10: with polarity "exclude" the selected_values are what is EXCLUDED.
        "polarity": polarity,
        "selected_values": selected_values[:MAX_SELECTED_VALUES],
        "selected_values_count": total_selected_count,
        "selected_values_truncated": total_selected_count > MAX_SELECTED_VALUES,
        "has_filter": bool(current_filter) or any(f.get("filter") for f in fc_entries),
        "title": raw_title,
        # H-38: raw file position — GROUP-LOCAL when parent_group is set.
        "position": position,
        "parent_group": visual_data.get("parentGroupName"),
        "position_frame": "group-local" if visual_data.get("parentGroupName") else "page",
    }


def _find_slicers(
    definition_path: Path,
    display_name: Optional[str],
    entity: Optional[str],
    property_name: Optional[str],
    *,
    page: Optional[str] = None,
    page_exact: bool = True,
) -> List[Dict]:
    """Find slicers matching the criteria (on ``page`` only when given — H-30).

    ``page`` resolves id -> exact name -> unique prefix; with
    ``page_exact=False`` an unresolved page falls back to a substring filter
    (read-only audits). An unresolvable page yields [].
    """
    matching_slicers: List[Dict] = []
    pres = resolve_page(Path(definition_path), page, allow_substring=not page_exact)
    if "error" in pres:
        return matching_slicers
    for page_folder in pres["pages"]:
        page_id = page_folder.name
        page_display_name = _get_page_display_name(page_folder)
        for vid, visual_data in load_page_visuals(page_folder).items():
            visual_json_path = page_folder / "visuals" / vid / "visual.json"
            slicer_info = _extract_slicer_info(visual_data, visual_json_path)
            if not slicer_info:
                continue
            slicer_info["page_id"] = page_id
            slicer_info["page_name"] = page_display_name
            if slicer_info.get("parent_group"):
                off = absolute_offset(page_folder, visual_data)
                pos = slicer_info["position"]
                slicer_info["absolute_position"] = {
                    **pos,
                    "x": (pos.get("x") or 0) + off["x"],
                    "y": (pos.get("y") or 0) + off["y"],
                }
            if display_name and display_name.lower() not in slicer_info["display_name"].lower():
                continue
            if entity and entity.lower() != slicer_info["entity"].lower():
                continue
            if property_name and property_name.lower() != slicer_info["property"].lower():
                continue
            matching_slicers.append(slicer_info)
    return matching_slicers


# ============================================
# Visual Interaction Helper Functions
# ============================================


def _get_visual_display_info(visuals_path: Path) -> Dict[str, Dict]:
    """``{visual_name: {'display_title', 'visual_type'}}`` for all visuals on a page."""
    visual_info_cache: Dict[str, Dict] = {}
    for vid, visual_data in load_page_visuals(Path(visuals_path).parent).items():
        visual_name = visual_data.get("name", vid)
        visual_info_cache[visual_name] = {
            "display_title": _title_of(visual_data) or visual_name,
            "has_title": _title_of(visual_data) is not None,
            "visual_type": (visual_data.get("visual") or {}).get("visualType", "unknown"),
        }
    return visual_info_cache


def _get_page_interactions(page_folder: Path) -> List[Dict]:
    """``visualInteractions`` of a page (legacy ``interactions`` tolerated — H-08)."""
    page_data = _load_json_file(page_folder / "page.json")
    if not page_data:
        return []
    return pbir_model.get_page_interactions(page_data)


def _set_page_interactions(page_folder: Path, interactions: List[Dict]) -> bool:
    """Write ``visualInteractions`` (locked RMW). Raises safe_io.WriteError on failure."""
    page_json_path = page_folder / "page.json"
    if not page_json_path.exists():
        return False

    def _mutate(page_data: Dict) -> None:
        pbir_model.set_page_interactions(page_data, interactions)

    safe_io.update_json(page_json_path, _mutate)
    return True


def _find_interactions(
    definition_path: Path,
    page_name: Optional[str] = None,
    source_visual: Optional[str] = None,
    target_visual: Optional[str] = None,
    interaction_type: Optional[str] = None,
    include_visual_info: bool = True,
) -> Dict[str, Any]:
    """Find visual interactions across pages (read-only; page substring allowed)."""
    pres = resolve_page(Path(definition_path), page_name, allow_substring=True)
    if "error" in pres:
        return {"pages": [], "total_interactions": 0, "page_count": 0}
    wanted_type = normalize_interaction_type(interaction_type) if interaction_type else None

    result_pages = []
    total_interactions = 0
    for page_folder in pres["pages"]:
        page_id = page_folder.name
        page_display_name = _get_page_display_name(page_folder)
        interactions = _get_page_interactions(page_folder)
        if not interactions:
            continue
        visual_info_cache: Dict[str, Dict] = {}
        if include_visual_info or source_visual or target_visual:
            visual_info_cache = _get_visual_display_info(page_folder / "visuals")
        filtered_interactions = []
        for interaction in interactions:
            source_id = interaction.get("source", "")
            target_id = interaction.get("target", "")
            int_type = interaction.get("type", "")
            if interaction_type and int_type not in (wanted_type, interaction_type):
                continue
            if source_visual:
                source_title = visual_info_cache.get(source_id, {}).get("display_title", source_id)
                if (
                    source_visual.lower() not in source_id.lower()
                    and source_visual.lower() not in source_title.lower()
                ):
                    continue
            if target_visual:
                target_title = visual_info_cache.get(target_id, {}).get("display_title", target_id)
                if (
                    target_visual.lower() not in target_id.lower()
                    and target_visual.lower() not in target_title.lower()
                ):
                    continue
            int_record: Dict[str, Any] = {
                "source": source_id,
                "target": target_id,
                "type": int_type,
            }
            if include_visual_info:
                source_info = visual_info_cache.get(source_id, {})
                target_info = visual_info_cache.get(target_id, {})
                int_record["source_title"] = source_info.get("display_title", source_id)
                int_record["source_type"] = source_info.get("visual_type", "unknown")
                int_record["target_title"] = target_info.get("display_title", target_id)
                int_record["target_type"] = target_info.get("visual_type", "unknown")
                if source_id not in visual_info_cache or target_id not in visual_info_cache:
                    int_record["dangling"] = True
            filtered_interactions.append(int_record)
        if filtered_interactions:
            result_pages.append(
                {
                    "page_id": page_id,
                    "page_name": page_display_name,
                    "interactions": filtered_interactions,
                    "interaction_count": len(filtered_interactions),
                }
            )
            total_interactions += len(filtered_interactions)
    return {
        "pages": result_pages,
        "total_interactions": total_interactions,
        "page_count": len(result_pages),
    }


def _resolve_visual_id(visual_info_cache: Dict[str, Dict], ref: str, role: str) -> Dict[str, Any]:
    """Visual id or EXACT title (unique) -> ``{"id": ...}`` or ``{"error": ...}`` (H-28)."""
    if ref in visual_info_cache:
        return {"id": ref}
    hits = [
        vid
        for vid, info in visual_info_cache.items()
        if info.get("has_title") and info.get("display_title", "").lower() == ref.lower()
    ]
    if len(hits) == 1:
        return {"id": hits[0]}
    if len(hits) > 1:
        return {
            "error": f"{role} visual title {ref!r} is ambiguous ({len(hits)} visuals); pass the id",
            "candidates": hits,
        }
    return {"error": f"{role} visual not found: {ref}"}


def _set_interaction(
    definition_path: Path,
    page_name: str,
    source_visual: str,
    target_visual: str,
    interaction_type: str,
) -> Dict[str, Any]:
    """Set an interaction between two visuals on ONE page (exact page resolution)."""
    norm_type = normalize_interaction_type(interaction_type)
    if norm_type is None:
        return error_envelope(
            "invalid_argument",
            f"Invalid interaction_type: {interaction_type}. Must be one of: "
            f"{', '.join(sorted(INTERACTION_TYPES | set(INTERACTION_ALIASES)))}",
        )
    matching_page, err = resolve_page_dir(Path(definition_path), page_name)
    if err:
        return err
    visual_info_cache = _get_visual_display_info(matching_page / "visuals")
    src = _resolve_visual_id(visual_info_cache, source_visual, "Source")
    if "error" in src:
        return {"success": False, **src}
    tgt = _resolve_visual_id(visual_info_cache, target_visual, "Target")
    if "error" in tgt:
        return {"success": False, **tgt}
    source_id, target_id = src["id"], tgt["id"]

    interactions = _get_page_interactions(matching_page)
    found = False
    for interaction in interactions:
        if interaction.get("source") == source_id and interaction.get("target") == target_id:
            interaction["type"] = norm_type
            found = True
            break
    if not found:
        interactions.append({"source": source_id, "target": target_id, "type": norm_type})
    _set_page_interactions(matching_page, interactions)
    return {
        "success": True,
        "page_name": _get_page_display_name(matching_page),
        "page_id": matching_page.name,
        "source_visual": source_id,
        "target_visual": target_id,
        "interaction_type": norm_type,
        "action": "updated" if found else "added",
    }


def _bulk_set_interactions(
    definition_path: Path,
    page_name: str,
    interactions: List[Dict[str, str]],
    replace_all: bool = False,
) -> Dict[str, Any]:
    """Set multiple interactions at once on ONE page."""
    matching_page, err = resolve_page_dir(Path(definition_path), page_name)
    if err:
        return err
    visual_info_cache = _get_visual_display_info(matching_page / "visuals")

    resolved_interactions = []
    errors = []
    for i, int_spec in enumerate(interactions):
        source = int_spec.get("source", "")
        target = int_spec.get("target", "")
        int_type = normalize_interaction_type(int_spec.get("type", ""))
        if int_type is None:
            errors.append(f'Interaction {i}: Invalid type "{int_spec.get("type", "")}"')
            continue
        src = _resolve_visual_id(visual_info_cache, source, "Source")
        if "error" in src:
            errors.append(f"Interaction {i}: {src['error']}")
            continue
        tgt = _resolve_visual_id(visual_info_cache, target, "Target")
        if "error" in tgt:
            errors.append(f"Interaction {i}: {tgt['error']}")
            continue
        resolved_interactions.append({"source": src["id"], "target": tgt["id"], "type": int_type})

    if errors and not resolved_interactions:
        return {"success": False, "errors": errors}

    if replace_all:
        final_interactions = resolved_interactions
    else:
        interaction_lookup = {}
        for interaction in _get_page_interactions(matching_page):
            key = f"{interaction.get('source')}|{interaction.get('target')}"
            interaction_lookup[key] = interaction
        for interaction in resolved_interactions:
            key = f"{interaction['source']}|{interaction['target']}"
            interaction_lookup[key] = interaction
        final_interactions = list(interaction_lookup.values())

    _set_page_interactions(matching_page, final_interactions)
    result = {
        "success": True,
        "page_name": _get_page_display_name(matching_page),
        "page_id": matching_page.name,
        "interactions_set": len(resolved_interactions),
        "total_interactions": len(final_interactions),
        "replace_mode": replace_all,
    }
    if errors:
        result["warnings"] = errors
    return result


def _set_true(entries: List[Dict[str, Any]], key: str) -> None:
    if not entries:
        entries.append({"properties": {}})
    entries[0].setdefault("properties", {})[key] = {"expr": {"Literal": {"Value": "true"}}}


def _configure_single_select_all(visual_data: Dict) -> Dict:
    """Configure a slicer for single-select with "All" selected.

    1. selection.singleSelect / strictSingleSelect / selectAllCheckboxEnabled = true
    2. data.isInvertedSelectionMode = true  (inverted + nothing excluded = All)
    3. remove the persisted selection (general.filter) AND the condition of the
       slicer's own filterConfig entry — leaving either behind kept filtering
       (H-10); the entry itself stays, flagged inverted with no ``filter``,
       which is Desktop's "exclude nothing" shape.
    """
    visual = visual_data.setdefault("visual", {})
    objects = visual.setdefault("objects", {})

    selection = objects.setdefault("selection", [])
    for key in ("strictSingleSelect", "selectAllCheckboxEnabled", "singleSelect"):
        _set_true(selection, key)
    data = objects.setdefault("data", [])
    _set_true(data, "isInvertedSelectionMode")

    for entry in objects.get("general") or []:
        props = entry.get("properties") if isinstance(entry, dict) else None
        if isinstance(props, dict):
            props.pop("filter", None)

    bound = _bound_column(visual)
    if bound:
        for f in slicer_filter_entries(visual_data, bound["entity"], bound["property"]):
            f.pop("filter", None)
            gen = f.setdefault("objects", {}).setdefault("general", [])
            _set_true(gen, "isInvertedSelectionMode")
    return visual_data


def _op_list_interactions(args: Dict[str, Any], definition_path: Path) -> Dict[str, Any]:
    """List visual interactions from page.json files."""
    result = _find_interactions(
        definition_path,
        page_name=args.get("page_id") or args.get("page_name"),
        source_visual=args.get("source_visual"),
        target_visual=args.get("target_visual"),
        interaction_type=args.get("interaction_type"),
        include_visual_info=args.get("include_visual_info", True),
    )

    if result["total_interactions"] == 0:
        return {
            "success": True,
            "message": "No visual interactions found matching the criteria",
            "pages": [],
            "total_interactions": 0,
            "hint": (
                "Visual interactions define cross-filtering behavior. Default behavior is "
                "cross-filter; only overrides (NoFilter / DataFilter / HighlightFilter) are "
                "stored in page.json visualInteractions."
            ),
        }

    summary_only = args.get("summary_only", True)
    if summary_only:
        summary_pages = []
        for page in result["pages"]:
            type_counts: Dict[str, int] = {}
            for interaction in page["interactions"]:
                int_type = interaction.get("type", "Unknown")
                type_counts[int_type] = type_counts.get(int_type, 0) + 1
            summary_pages.append(
                {
                    "page_name": page["page_name"],
                    "page_id": page["page_id"],
                    "interaction_count": page["interaction_count"],
                    "by_type": type_counts,
                }
            )
        return {
            "success": True,
            "pages": summary_pages,
            "total_interactions": result["total_interactions"],
            "page_count": result["page_count"],
            "hint": "Use page_name filter and summary_only=false to see individual interactions",
        }

    MAX_INTERACTIONS_PER_PAGE = 25
    capped_pages = []
    for page in result["pages"]:
        page_copy = dict(page)
        if len(page_copy["interactions"]) > MAX_INTERACTIONS_PER_PAGE:
            page_copy["interactions"] = page_copy["interactions"][:MAX_INTERACTIONS_PER_PAGE]
            page_copy["truncated"] = True
        capped_pages.append(page_copy)

    return {
        "success": True,
        "pages": capped_pages,
        "total_interactions": result["total_interactions"],
        "page_count": result["page_count"],
    }


def _op_set_interaction(args: Dict[str, Any], definition_path: Path) -> Dict[str, Any]:
    """Set a single interaction between two visuals."""
    page_name_param = args.get("page_id") or args.get("page_name")
    source_visual = args.get("source_visual")
    target_visual = args.get("target_visual")
    interaction_type = args.get("interaction_type")
    dry_run = bool(args.get("dry_run", False))

    if not all([page_name_param, source_visual, target_visual, interaction_type]):
        return error_envelope(
            "missing_argument",
            "set_interaction requires: page_name, source_visual, target_visual, "
            "interaction_type",
        )
    with safe_io.write_scope(dry_run=dry_run):
        result = _set_interaction(
            definition_path,
            page_name=page_name_param,
            source_visual=source_visual,
            target_visual=target_visual,
            interaction_type=interaction_type,
        )
    if dry_run and isinstance(result, dict):
        result["dry_run"] = True
        if result.get("success"):
            result["message"] = (
                f"Would set interaction: {result.get('source_visual')} -> "
                f"{result.get('target_visual')} = {result.get('interaction_type')} "
                f"on page {result.get('page_name')!r}"
            )
    return result


def _op_bulk_set_interactions(args: Dict[str, Any], definition_path: Path) -> Dict[str, Any]:
    """Set multiple interactions at once."""
    page_name_param = args.get("page_id") or args.get("page_name")
    interactions_list = args.get("interactions", [])
    replace_all = args.get("replace_all", False)
    dry_run = bool(args.get("dry_run", False))

    if not page_name_param:
        return error_envelope("missing_argument", "bulk_set_interactions requires: page_name")

    if not interactions_list:
        return error_envelope(
            "missing_argument",
            "bulk_set_interactions requires: interactions (array of {source, target, type})",
        )
    with safe_io.write_scope(dry_run=dry_run):
        result = _bulk_set_interactions(
            definition_path,
            page_name=page_name_param,
            interactions=interactions_list,
            replace_all=replace_all,
        )
    if dry_run and isinstance(result, dict):
        result["dry_run"] = True
        result["interactions_preview"] = interactions_list[:5]
    return result
