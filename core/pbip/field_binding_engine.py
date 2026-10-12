"""
Field Binding Engine for PBIP Reports

Add, remove, and clear fields from visual data role buckets.
Uses data_binding_builder.py for constructing projection entries
and reads/writes visual.json files through ``safe_io`` (locked RMW).

Deep review 2026-09-22 (H-32): ``remove_field`` also drops what pointed at
the removed field — ``sortDefinition`` entries and per-field formatting
(``objects[*][].selector.metadata == queryRef``); visual-level filters on the
field are reported (a filter may legitimately target an unbound field).
``add_field(field_type="Hierarchy")`` builds a real ``HierarchyLevel``
binding (needs ``hierarchy_level``) instead of silently binding a column,
and the role is validated against ``VISUAL_ROLES``.

No MCP awareness — pure domain logic operating on PBIP file structures.
"""

import copy
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.pbip import pbir_model
from core.pbip.authoring import safe_io
from core.utilities.pbip_utils import SKIP_WRITE, load_json_file

logger = logging.getLogger(__name__)


def _find_visual_json(definition_path: Path, page_name: str, visual_name: str) -> Optional[Path]:
    """visual.json by exact page (id / name / unique prefix) + visual id / exact title."""
    from core.pbip.authoring.targets import resolve_page_dir, resolve_visual_dir

    page_folder, err = resolve_page_dir(Path(definition_path), page_name)
    if err:
        return None
    vf, verr = resolve_visual_dir(page_folder, visual_name)
    if verr:
        return None
    vj = vf / "visual.json"
    return vj if vj.exists() else None


def _get_query_state(visual_data: Dict[str, Any]) -> Dict[str, Any]:
    """Get or create the queryState dict from visual data (created in place if absent)."""
    visual_section = visual_data.setdefault("visual", {})
    query = visual_section.setdefault("query", {})
    return query.setdefault("queryState", {})


def _field_entity_prop(field_ref: Dict[str, Any]) -> Optional[tuple]:
    for field_type in ("Column", "Measure"):
        type_ref = field_ref.get(field_type)
        if isinstance(type_ref, dict):
            entity = ((type_ref.get("Expression") or {}).get("SourceRef") or {}).get("Entity", "")
            return entity, type_ref.get("Property", "")
    hl = field_ref.get("HierarchyLevel")
    if isinstance(hl, dict):
        hier = (hl.get("Expression") or {}).get("Hierarchy") or {}
        entity = ((hier.get("Expression") or {}).get("SourceRef") or {}).get("Entity", "")
        return entity, hl.get("Level", "")
    agg = field_ref.get("Aggregation")
    if isinstance(agg, dict):
        return _field_entity_prop(agg.get("Expression") or {})
    return None


def _matches_field(projection: Dict[str, Any], table: str, field: str) -> bool:
    """True if a projection references ``table.field`` (Column / Measure / level / aggregation)."""
    ep = _field_entity_prop(projection.get("field", {}) or {})
    if ep is not None and ep == (table, field):
        return True
    return projection.get("queryRef", "") == f"{table}.{field}"


def add_field(
    definition_path: Path,
    page_name: str,
    visual_name: str,
    table: str,
    field: str,
    bucket: str,
    field_type: str = "Column",
    display_name: str = None,
    hierarchy_level: Optional[str] = None,
) -> Dict[str, Any]:
    """Add a field to a visual's data role bucket.

    ``field_type``: "Column", "Measure" or "Hierarchy" (``field`` = hierarchy
    name, ``hierarchy_level`` = level).
    """
    from core.pbip.authoring.data_binding_builder import (
        build_column_binding,
        build_hierarchy_binding,
        build_measure_binding,
    )
    from core.pbip.authoring.visual_roles import validate_bindings_ex

    if not table or not field:
        return {"success": False, "error": "Both table and field are required"}
    if not bucket:
        return {"success": False, "error": "Bucket name is required"}

    visual_json_path = _find_visual_json(definition_path, page_name, visual_name)
    if not visual_json_path:
        return {
            "success": False,
            "error": f"Visual not found: page='{page_name}', visual='{visual_name}'",
        }
    visual_data = load_json_file(visual_json_path)
    if not visual_data:
        return {"success": False, "error": "Could not read visual.json"}

    vtype = (visual_data.get("visual") or {}).get("visualType", "")
    ok, role_errors, role_warnings = (
        validate_bindings_ex(vtype, [bucket]) if vtype else (True, [], [])
    )
    if not ok:
        return {
            "success": False,
            "error_type": "invalid_role",
            "error": "; ".join(role_errors),
        }

    field_type_lower = (field_type or "Column").lower()
    if field_type_lower == "measure":
        projection = build_measure_binding(table, field, display_name)
    elif field_type_lower == "column":
        projection = build_column_binding(table, field, display_name)
    elif field_type_lower == "hierarchy":
        if not hierarchy_level:
            return {
                "success": False,
                "error_type": "invalid_args",
                "error": "field_type=Hierarchy needs hierarchy_level (field = hierarchy name)",
            }
        projection = build_hierarchy_binding(table, field, hierarchy_level, display_name)
    else:
        return {
            "success": False,
            "error": f"Invalid field_type: '{field_type}'. Use 'Column', 'Measure', or 'Hierarchy'",
        }

    match_field = hierarchy_level if field_type_lower == "hierarchy" else field
    out: Dict[str, Any] = {}

    def _mutate(data: Dict[str, Any]) -> Any:
        query_state = _get_query_state(data)
        projections = query_state.setdefault(bucket, {}).setdefault("projections", [])
        for existing in projections:
            if _matches_field(existing, table, match_field):
                out["duplicate"] = True
                return SKIP_WRITE
        projections.append(copy.deepcopy(projection))
        out["count"] = len(projections)
        return None

    safe_io.update_json(visual_json_path, _mutate)
    if out.get("duplicate"):
        return {
            "success": False,
            "error": f"Field '{table}.{match_field}' already exists in bucket '{bucket}'",
        }
    result = {
        "success": True,
        "visual_id": visual_json_path.parent.name,
        "table": table,
        "field": field,
        "bucket": bucket,
        "field_type": field_type,
        "total_fields_in_bucket": out.get("count"),
    }
    if role_warnings:
        result["role_warnings"] = role_warnings
    return result


def _strip_field_everywhere(
    data: Dict[str, Any], table: str, field: str, query_refs: List[str]
) -> Dict[str, Any]:
    """Remove sort entries + per-field formatting of a removed field (H-32)."""
    removed = {"sort_entries": 0, "formatting_entries": 0}
    visual = data.get("visual") or {}
    sort_def = (visual.get("query") or {}).get("sortDefinition")
    if isinstance(sort_def, dict) and isinstance(sort_def.get("sort"), list):
        kept = [
            s
            for s in sort_def["sort"]
            if _field_entity_prop(s.get("field") or {}) != (table, field)
        ]
        removed["sort_entries"] = len(sort_def["sort"]) - len(kept)
        if kept:
            sort_def["sort"] = kept
        else:
            visual["query"].pop("sortDefinition", None)
    for card, entries in list((visual.get("objects") or {}).items()):
        if not isinstance(entries, list):
            continue
        kept_entries = [
            e
            for e in entries
            if not (isinstance(e, dict) and (e.get("selector") or {}).get("metadata") in query_refs)
        ]
        removed["formatting_entries"] += len(entries) - len(kept_entries)
        if kept_entries:
            visual["objects"][card] = kept_entries
        else:
            del visual["objects"][card]
    return removed


def remove_field(
    definition_path: Path,
    page_name: str,
    visual_name: str,
    table: str,
    field: str,
    bucket: str = None,
) -> Dict[str, Any]:
    """Remove a field from a visual's data roles (all buckets unless ``bucket``).

    Also removes the field's sort entry and per-field formatting entries; the
    visual-level filters on the field are listed in ``filters_still_on_field``.
    """
    if not table or not field:
        return {"success": False, "error": "Both table and field are required"}
    visual_json_path = _find_visual_json(definition_path, page_name, visual_name)
    if not visual_json_path:
        return {
            "success": False,
            "error": f"Visual not found: page='{page_name}', visual='{visual_name}'",
        }
    visual_data = load_json_file(visual_json_path)
    if not visual_data:
        return {"success": False, "error": "Could not read visual.json"}
    query_state = visual_data.get("visual", {}).get("query", {}).get("queryState", {})
    if not query_state:
        return {"success": False, "error": "Visual has no query state (no fields bound)"}

    out: Dict[str, Any] = {"removed_from": [], "cleanup": {}}

    def _mutate(data: Dict[str, Any]) -> Any:
        qs = data.get("visual", {}).get("query", {}).get("queryState", {})
        buckets_to_check = [bucket] if bucket else list(qs.keys())
        still_bound = False
        query_refs: List[str] = []
        for b in buckets_to_check:
            if b not in qs:
                continue
            projections = qs[b].get("projections", [])
            kept = []
            for p in projections:
                if _matches_field(p, table, field):
                    if p.get("queryRef"):
                        query_refs.append(p["queryRef"])
                else:
                    kept.append(p)
            if len(kept) < len(projections):
                qs[b]["projections"] = kept
                out["removed_from"].append(b)
        if not out["removed_from"]:
            return SKIP_WRITE
        for b, payload in qs.items():
            if any(_matches_field(p, table, field) for p in (payload or {}).get("projections", [])):
                still_bound = True
        if not still_bound:
            out["cleanup"] = _strip_field_everywhere(
                data, table, field, query_refs or [f"{table}.{field}"]
            )
        return None

    safe_io.update_json(visual_json_path, _mutate)
    if not out["removed_from"]:
        scope = f"bucket '{bucket}'" if bucket else "any bucket"
        return {"success": False, "error": f"Field '{table}.{field}' not found in {scope}"}
    filters_on_field = [
        f.get("name")
        for f in pbir_model.get_visual_filters(visual_data)
        if _field_entity_prop(f.get("field") or {}) == (table, field)
    ]
    result = {
        "success": True,
        "visual_id": visual_json_path.parent.name,
        "table": table,
        "field": field,
        "removed_from_buckets": out["removed_from"],
        "cleanup": out["cleanup"],
    }
    if filters_on_field:
        result["filters_still_on_field"] = filters_on_field
    return result
