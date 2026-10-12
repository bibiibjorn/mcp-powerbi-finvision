"""Visual configuration operations: update_visual_config, configure_slicer, add_field,
remove_field, set_sort, set_action, inject_code, update_formatting, manage_visual_calcs,
set_raw_property, get_raw_property, delete_raw_property.

Extracted from visual_operations_handler.py.

Deep review 2026-09-22 (WP9): every op resolves its target through
``core.pbip.authoring.targets`` (exact page, id-or-exact-title visual, refuse
ambiguous / unbounded writes -- H-16 / L-02 / L-05), writes through
``safe_io.update_json`` (locked RMW -- H-01, failures surface -- H-29) and
writes the REAL PBIR locations (H-05 / H-14 / H-15):

* chrome -> ``visual.visualContainerObjects`` (never top-level);
* sort -> ``visual.query.sortDefinition.sort[{field, direction: "Ascending" |
  "Descending"}]``;
* actions -> ``visual.visualContainerObjects.visualLink[0].properties``
  (``navigationSection`` / ``bookmark`` / ``drillthroughSection`` / ``webUrl``
  / ``dataFunction``);
* Deneb -> ``visual.objects.vega[0].properties.{jsonSpec, provider}``.
"""

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.pbip.authoring import safe_io
from core.pbip.authoring.formatting import literal as _lit
from core.pbip.authoring.formatting import property_value as _property_value
from core.pbip.authoring.targets import resolve_targets
from core.pbip.pbir_model import encode_literal
from core.utilities.pbip_utils import SKIP_WRITE as _SKIP
from core.utilities.pbip_utils import load_json_file as _load_json_file
from server.handlers.visual_layout_handler import _find_visuals  # noqa: F401 — re-export
from core.validation.error_response import error_envelope

# ---------------------------------------------------------------------------
# Config-specific helpers
# ---------------------------------------------------------------------------


def _update_visual_config_property(
    visual_data: Dict,
    config_type: str,
    property_name: str,
    property_value: Any,
    selector_metadata: Optional[str] = None,
    value_type: str = "auto",
) -> Dict[str, Any]:
    visual = visual_data.setdefault("visual", {})
    objects = visual.setdefault("objects", {})
    if config_type not in objects:
        objects[config_type] = []
    config_array = objects[config_type]

    # H-15: one encoder — strings are always quoted + '' escaped unless they
    # already are a PBIR literal; floats never use exponent notation.
    formatted_value = _property_value(property_name, property_value, value_type)
    target_entry = None
    target_index = -1
    if selector_metadata:
        for i, entry in enumerate(config_array):
            if entry.get("selector", {}).get("metadata") == selector_metadata:
                target_entry = entry
                target_index = i
                break
        if target_entry is None:
            target_entry = {"properties": {}, "selector": {"metadata": selector_metadata}}
            config_array.append(target_entry)
            target_index = len(config_array) - 1
    else:
        for i, entry in enumerate(config_array):
            if "selector" not in entry or not entry.get("selector"):
                target_entry = entry
                target_index = i
                break
        if target_entry is None:
            # Never write into a per-series / per-measure entry by accident:
            # add an unselected entry instead of reusing config_array[0].
            target_entry = {"properties": {}}
            config_array.insert(0, target_entry)
            target_index = 0
    properties = target_entry.setdefault("properties", {})
    old_value = properties.get(property_name)
    properties[property_name] = formatted_value
    return {
        "modified": old_value != formatted_value,
        "change": {
            "config_type": config_type,
            "property_name": property_name,
            "selector_metadata": selector_metadata,
            "old_value": old_value,
            "new_value": formatted_value,
            "entry_index": target_index,
        },
    }


def _remove_visual_config_property(
    visual_data: Dict,
    config_type: str,
    property_name: str,
    selector_metadata: Optional[str] = None,
) -> Dict[str, Any]:
    objects = visual_data.get("visual", {}).get("objects", {})
    if config_type not in objects:
        return {"modified": False, "change": None}
    for entry in objects[config_type]:
        selector = entry.get("selector", {})
        selector_match = (
            selector.get("metadata") == selector_metadata
            if selector_metadata
            else ("selector" not in entry or not entry.get("selector"))
        )
        if selector_match:
            properties = entry.get("properties", {})
            if property_name in properties:
                old_value = properties.pop(property_name)
                return {
                    "modified": True,
                    "change": {
                        "config_type": config_type,
                        "property_name": property_name,
                        "selector_metadata": selector_metadata,
                        "old_value": old_value,
                        "new_value": None,
                        "action": "removed",
                    },
                }
    return {"modified": False, "change": None}


def _parse_json_path(path: str) -> List:
    """Tokenise ``a.b[0].c`` / ``a['x.y']``. Raises ValueError on an unclosed bracket (H-37)."""
    tokens: List = []
    buf = ""
    i = 0
    n = len(path or "")
    while i < n:
        ch = path[i]
        if ch == ".":
            if buf:
                tokens.append(buf)
                buf = ""
            i += 1
            continue
        if ch == "[":
            if buf:
                tokens.append(buf)
                buf = ""
            end = path.find("]", i)
            if end < 0:
                raise ValueError(f"unclosed '[' at position {i} in json_path {path!r}")
            idx_token = path[i + 1 : end].strip()
            try:
                tokens.append(int(idx_token))
            except ValueError:
                tokens.append(idx_token.strip("'\""))
            i = end + 1
            if i < n and path[i] == ".":
                i += 1
            continue
        buf += ch
        i += 1
    if buf:
        tokens.append(buf)
    return tokens


def _traverse_json_path(root, tokens, create_missing: bool = False):
    """Walk to the parent of the last token -> ``(parent, last, exists, error)``.

    H-05b: a missing INTERMEDIATE key with ``create_missing=False`` is an
    error (the old code returned that intermediate as the write target and
    then wrote the value at the wrong path).
    """
    if not tokens:
        return None, None, False, "empty json_path"
    cursor = root
    for idx, tok in enumerate(tokens[:-1]):
        nxt = tokens[idx + 1]
        if isinstance(tok, int):
            if not isinstance(cursor, list):
                return (
                    None,
                    None,
                    False,
                    f"path[{idx}] expected list for index {tok}, got {type(cursor).__name__}",
                )
            while create_missing and tok >= len(cursor):
                cursor.append([] if isinstance(nxt, int) else {})
            if tok >= len(cursor) or tok < 0:
                return None, None, False, f"path[{idx}] index {tok} out of range"
            cursor = cursor[tok]
        else:
            if not isinstance(cursor, dict):
                return (
                    None,
                    None,
                    False,
                    f"path[{idx}] expected dict for key '{tok}', got {type(cursor).__name__}",
                )
            if tok not in cursor:
                if not create_missing:
                    return (
                        None,
                        None,
                        False,
                        f"path[{idx}] key '{tok}' does not exist (pass create_missing=true "
                        "to create intermediate containers)",
                    )
                cursor[tok] = [] if isinstance(nxt, int) else {}
            cursor = cursor[tok]
    last = tokens[-1]
    if isinstance(last, int):
        if not isinstance(cursor, list):
            return None, None, False, f"final token expects list, got {type(cursor).__name__}"
        exists = 0 <= last < len(cursor)
    else:
        if not isinstance(cursor, dict):
            return None, None, False, f"final token expects dict, got {type(cursor).__name__}"
        exists = last in cursor
    return cursor, last, exists, None


def _single_target(args: Dict[str, Any], definition_path: Path, op: str) -> Dict[str, Any]:
    """Exactly one visual (id or exact title) on an exactly-resolved page."""
    if not (args.get("visual_name") or args.get("visual_id")) or not (
        args.get("page_name") or args.get("page_id")
    ):
        return {
            "success": False,
            "error_type": "invalid_args",
            "error": f"{op} requires: visual_name (id or exact title) and page_name/page_id",
        }
    return resolve_targets(definition_path, args, write=True, op=op, single=True)


# ---------------------------------------------------------------------------
# Op functions
# ---------------------------------------------------------------------------


def _op_update_visual_config(args: Dict[str, Any], definition_path: Path) -> Dict[str, Any]:
    config_type = args.get("config_type")
    property_name = args.get("property_name")
    property_value = args.get("property_value")
    selector_metadata = args.get("selector_metadata")
    value_type = args.get("value_type", "auto")
    remove_property = args.get("remove_property", False)
    dry_run = args.get("dry_run", False)
    config_updates = args.get("config_updates")
    if not config_updates:
        if not config_type or not property_name:
            return error_envelope(
                "missing_argument",
                "update_visual_config requires either: (config_type + property_name + "
                "property_value) OR config_updates array",
            )
        if property_value is None and not remove_property:
            return error_envelope(
                "missing_argument",
                "property_value is required unless remove_property is True",
            )
        config_updates = [
            {
                "config_type": config_type,
                "property_name": property_name,
                "property_value": property_value,
                "selector_metadata": selector_metadata,
                "value_type": value_type,
                "remove_property": remove_property,
            }
        ]
    else:
        # P1-124 fix: validate every entry (a None property_value used to be
        # written as the literal "None").
        for idx, upd in enumerate(config_updates):
            if not isinstance(upd, dict):
                return error_envelope(
                    "invalid_argument",
                    f"config_updates[{idx}] must be an object",
                )
            if not upd.get("config_type") or not upd.get("property_name"):
                return error_envelope(
                    "missing_argument",
                    f"config_updates[{idx}] requires both config_type and property_name",
                )
            if upd.get("property_value") is None and not upd.get("remove_property", False):
                return error_envelope(
                    "missing_argument",
                    f"config_updates[{idx}] property_value is required "
                    f'(config_type={upd.get("config_type")}, '
                    f'property_name={upd.get("property_name")}) '
                    f"unless remove_property is True",
                )
    targets = resolve_targets(definition_path, args, write=True, op="update_visual_config")
    if not targets.get("success"):
        return targets

    def _apply(visual_data: Dict) -> List:
        visual_changes: List = []
        for update in config_updates:
            if update.get("remove_property", False):
                res = _remove_visual_config_property(
                    visual_data,
                    update.get("config_type"),
                    update.get("property_name"),
                    update.get("selector_metadata"),
                )
            else:
                res = _update_visual_config_property(
                    visual_data,
                    update.get("config_type"),
                    update.get("property_name"),
                    update.get("property_value"),
                    update.get("selector_metadata"),
                    update.get("value_type", "auto"),
                )
            if res["modified"]:
                visual_changes.append(res["change"])
        return visual_changes

    changes: List = []
    errors: List = []
    for visual in targets["visuals"]:
        file_path = Path(visual["file_path"])
        visual_data = _load_json_file(file_path)
        if not visual_data:
            errors.append({"file_path": str(file_path), "error": "Failed to load visual.json"})
            continue
        visual_changes = _apply(copy.deepcopy(visual_data))
        if not visual_changes:
            continue
        record: Dict = {
            "display_title": visual["display_title"],
            "page_name": visual.get("page_name", ""),
            "visual_name": visual["visual_name"],
            "visual_type": visual["visual_type"],
            "config_changes": visual_changes,
            "status": "would_change" if dry_run else "changed",
        }
        if not dry_run:
            try:
                # _apply returns the change list: never hand it to update_json
                # as the mutate result (a non-None return REPLACES the file).
                safe_io.update_json(file_path, lambda d: (_apply(d), None)[1])
            except safe_io.WriteError as e:
                record["status"] = "error"
                errors.append({"file_path": str(file_path), "error": str(e)})
        changes.append(record)
    result: Dict = {
        "success": len(errors) == 0,
        "operation": "update_visual_config",
        "dry_run": dry_run,
        "matched": targets["matched"],
        "message": f'{"Would update" if dry_run else "Updated"} config in {len(changes)} visual(s)',
        "changes": changes,
        "changes_count": len(changes),
    }
    if errors:
        result["errors"] = errors
        result["errors_count"] = len(errors)
        result["message"] += f" with {len(errors)} error(s)"
    return result


def _slicer_state_dump(visual_data: Dict) -> Dict[str, Any]:
    """What a slicer reconfiguration would destroy: selection + filterConfig (H-17)."""
    from core.pbip.pbir_model import get_slicer_selection_filter, get_visual_filters

    objs = (visual_data.get("visual") or {}).get("objects") or {}
    return {
        "general_filter": get_slicer_selection_filter(visual_data),
        "filterConfig_filters": get_visual_filters(visual_data),
        "selection": objs.get("selection"),
        "data": objs.get("data"),
    }


def _op_configure_slicer(args: Dict[str, Any], definition_path: Path) -> Dict[str, Any]:
    """Configure matching slicers for single-select with "All" selected.

    H-17: the op REQUIRES a selector (display_name / entity / property /
    visual_name) AND a page; a match of several slicers only previews unless
    ``allow_multiple=true``; the prior selection state of every slicer is
    returned so the change can be undone.
    """
    from server.handlers._internal.slicer_helpers import _configure_single_select_all, _find_slicers

    display_name = args.get("display_name_filter") or args.get("display_name")
    entity = args.get("entity")
    property_name = args.get("property")
    visual_name = args.get("visual_name") or args.get("visual_id")
    page = args.get("page_id") or args.get("page_name")
    dry_run = bool(args.get("dry_run", False))
    if not (display_name or entity or property_name or visual_name) or not page:
        return {
            "success": False,
            "error_type": "target_required",
            "error": (
                "configure_slicer resets the selection of every slicer it matches (forces "
                "single-select 'All' and removes the persisted selection). Pass page_name AND "
                "at least one of visual_name / display_name / entity / property."
            ),
        }
    slicers = _find_slicers(definition_path, display_name, entity, property_name, page=page)
    if isinstance(slicers, dict):
        return slicers
    if visual_name:
        slicers = [s for s in slicers if s["visual_name"] == visual_name]
    if not slicers:
        return error_envelope("not_found", "No slicers found matching criteria")
    allow_multiple = bool(args.get("allow_multiple", False))
    preview_only = dry_run or (len(slicers) > 1 and not allow_multiple)
    changes: List = []
    errors: List = []
    for slicer in slicers:
        file_path = Path(slicer["file_path"])
        before_mode = slicer["selection_mode"]
        prior = _slicer_state_dump(_load_json_file(file_path) or {})
        base = {
            "visual_name": slicer["visual_name"],
            "display_name": slicer["display_name"],
            "page_name": slicer.get("page_name", ""),
            "before_mode": before_mode,
            "after_mode": "single_select_all",
            "prior_state": prior,
        }
        if preview_only:
            base["status"] = (
                "would_change" if before_mode != "single_select_all" else "already_configured"
            )
            changes.append(base)
            continue
        try:
            safe_io.update_json(file_path, _configure_single_select_all)
            changes.append({**base, "status": "changed"})
        except safe_io.WriteError as e:
            errors.append({"file_path": str(file_path), "error": str(e)})
    result: Dict = {
        "success": len(errors) == 0,
        "operation": "configure_slicer",
        "dry_run": preview_only,
        "matched": len(slicers),
        "polarity_applied": "exclude (inverted selection, nothing excluded = All)",
        "changes": changes,
        "changes_count": len(changes),
    }
    if preview_only and not dry_run:
        result["note"] = (
            f"{len(slicers)} slicers matched — previewed only. Pass allow_multiple=true to "
            "reconfigure all of them, or narrow with visual_name."
        )
    if errors:
        result["errors"] = errors
    return result


def _op_audit_pane_collisions(args: Dict[str, Any], definition_path: Path) -> Dict[str, Any]:
    from server.handlers._internal.slicer_pane_audit import _op_audit_pane_collisions as _impl

    return _impl(args, definition_path)


def _op_bulk_rename_pane_names(args: Dict[str, Any], definition_path: Path) -> Dict[str, Any]:
    from server.handlers._internal.slicer_pane_audit import _op_bulk_rename_pane_names as _impl

    return _impl(args, definition_path)


def _op_clear_default_selection(args: Dict[str, Any], definition_path: Path) -> Dict[str, Any]:
    from server.handlers._internal.slicer_default_selection import (
        _op_clear_default_selection as _impl,
    )

    return _impl(args, definition_path)


def _op_set_default_selection(args: Dict[str, Any], definition_path: Path) -> Dict[str, Any]:
    from server.handlers._internal.slicer_default_selection import (
        _op_set_default_selection as _impl,
    )

    return _impl(args, definition_path)


def _op_add_field(args: Dict[str, Any], definition_path: Path) -> Dict[str, Any]:
    from core.pbip.field_binding_engine import add_field

    return add_field(
        definition_path=definition_path,
        page_name=args.get("page_id") or args.get("page_name", ""),
        visual_name=args.get("visual_id") or args.get("visual_name", ""),
        table=args.get("table", ""),
        field=args.get("field", ""),
        bucket=args.get("bucket", "Values"),
        field_type=args.get("field_type", "Column"),
        display_name=args.get("display_name"),
        hierarchy_level=args.get("hierarchy_level"),
    )


def _op_remove_field(args: Dict[str, Any], definition_path: Path) -> Dict[str, Any]:
    from core.pbip.field_binding_engine import remove_field

    return remove_field(
        definition_path=definition_path,
        page_name=args.get("page_id") or args.get("page_name", ""),
        visual_name=args.get("visual_id") or args.get("visual_name", ""),
        table=args.get("table", ""),
        field=args.get("field", ""),
        bucket=args.get("bucket"),
    )


def _split_table_field(ref: str) -> Optional[tuple]:
    """``Table.Field`` split at the FIRST dot (measure names may contain dots)."""
    if not isinstance(ref, str) or "." not in ref:
        return None
    table, _, field = ref.partition(".")
    if not table or not field:
        return None
    return table, field


def _bound_field_type(visual_data: Dict, table: str, field: str) -> Optional[str]:
    """'Measure' / 'Column' when the field is already bound in the visual's queryState."""
    qs = ((visual_data.get("visual") or {}).get("query") or {}).get("queryState") or {}
    for role in qs.values():
        for proj in (role or {}).get("projections") or []:
            f = proj.get("field") or {}
            for kind in ("Measure", "Column"):
                node = f.get(kind)
                if not isinstance(node, dict):
                    continue
                ent = ((node.get("Expression") or {}).get("SourceRef") or {}).get("Entity")
                if ent == table and node.get("Property") == field:
                    return kind
    return None


def _op_set_sort(args: Dict[str, Any], definition_path: Path) -> Dict[str, Any]:
    """H-14: ``visual.query.sortDefinition.sort[{field, direction}]`` (string direction)."""
    sort_field = args.get("sort_field", "")
    sort_direction = args.get("sort_direction", "Ascending")
    if not sort_field:
        return error_envelope(
            "missing_argument",
            "set_sort requires: page_name, visual_name, sort_field",
        )
    if sort_direction not in ("Ascending", "Descending"):
        return {
            "success": False,
            "error_type": "invalid_args",
            "error": "sort_direction must be 'Ascending' or 'Descending'",
        }
    split = _split_table_field(sort_field)
    if not split:
        return error_envelope("invalid_argument", "sort_field must be Table.Field format")
    table, field = split
    targets = _single_target(args, definition_path, "set_sort")
    if not targets.get("success"):
        return targets
    visual = targets["visuals"][0]
    file_path = Path(visual["file_path"])
    visual_data = _load_json_file(file_path)
    if not visual_data:
        return error_envelope("operation_failed", "Failed to load visual.json")
    ftype = args.get("sort_field_type") or _bound_field_type(visual_data, table, field)
    if ftype not in ("Measure", "Column"):
        from core.pbip.authoring.model_lookup import field_kind

        ftype = field_kind(definition_path, table, field) or "Column"
    new_sort = {
        "sort": [
            {
                "field": {
                    ftype: {"Expression": {"SourceRef": {"Entity": table}}, "Property": field}
                },
                "direction": sort_direction,
            }
        ]
    }
    query = (visual_data.get("visual") or {}).get("query") or {}
    before = query.get("sortDefinition")
    would_change = before != new_sort or "sort" in (visual_data.get("visual") or {})
    result: Dict[str, Any] = {
        "success": True,
        "operation": "set_sort",
        "visual_name": visual["visual_name"],
        "sort_field": sort_field,
        "sort_field_type": ftype,
        "sort_direction": sort_direction,
        "before": before,
        "after": new_sort,
    }
    if args.get("dry_run", False):
        result.update(dry_run=True, status="would_change" if would_change else "no_change")
        return result

    def _mutate(data: Dict) -> None:
        vis = data.setdefault("visual", {})
        vis.pop("sort", None)  # legacy invalid key written by older versions
        vis.setdefault("query", {})["sortDefinition"] = copy.deepcopy(new_sort)

    safe_io.update_json(file_path, _mutate)
    result.update(dry_run=False, status="changed" if would_change else "no_change")
    return result


#: Accepted `action_type` values = the ``visualLink.type`` enum Desktop writes
#: (R0105 / R0201 census: Bookmark, WebUrl, PageNavigation, ClearAllSlicers,
#: Drillthrough, Back). `DataFunction` = translytical task flows.
ACTION_TYPES = (
    "PageNavigation",
    "Bookmark",
    "Drillthrough",
    "WebUrl",
    "Back",
    "ClearAllSlicers",
    "DataFunction",
)

#: visualLink property that carries the target of each action type.
_ACTION_TARGET_KEY = {
    "PageNavigation": "navigationSection",
    "Drillthrough": "drillthroughSection",
    "Bookmark": "bookmark",
    "WebUrl": "webUrl",
}


def _pbir_string_literal(text: str) -> str:
    """Encode a string as a PBIR Literal Value (quotes doubled, never backslash-escaped)."""
    return encode_literal(str(text), "string")


def _build_data_function_payload(args: Dict[str, Any]) -> Dict[str, Any]:
    """``visualLink.dataFunction`` in the shape Desktop writes (R0105/R0201: 592 entries).

    ``{"kind": "ItemLocation", "byReference": {"itemId", "workspaceId"},
    "metadata": {"dataFunction": {"autoRefresh": false, "parameters": []}}}``.
    Only ``data_function_id`` (itemId) / ``data_function_workspace_id`` map to
    known keys; ``data_function_name`` / ``power_fx`` have no verified
    location and are NOT written (reported back as ``unwritten_args``).
    """
    fn_id = args.get("data_function_id")
    ws_id = args.get("data_function_workspace_id")
    if not fn_id and not ws_id:
        return {}
    return {
        "kind": "ItemLocation",
        "byReference": {
            "itemId": _lit(_pbir_string_literal(str(fn_id or ""))),
            "workspaceId": _lit(_pbir_string_literal(str(ws_id or ""))),
        },
        "metadata": {"dataFunction": {"autoRefresh": False, "parameters": []}},
    }


def _resolve_action_target(definition_path: Path, action_type: str, target: str) -> Dict[str, Any]:
    """Page names -> page ids, bookmark display names -> bookmark ids."""
    if action_type in ("PageNavigation", "Drillthrough"):
        from core.pbip.authoring.targets import resolve_page_dir

        page_dir, err = resolve_page_dir(definition_path, target)
        if err:
            return err
        return {"success": True, "value": page_dir.name}
    if action_type == "Bookmark":
        from core.pbip.bookmark_engine import _find_bookmark_file

        bfile = _find_bookmark_file(Path(definition_path), target)
        if bfile is None:
            return {
                "success": False,
                "error_type": "bookmark_not_found",
                "error": f"bookmark not found: {target!r}",
            }
        data = _load_json_file(bfile) or {}
        return {"success": True, "value": data.get("name") or bfile.name.split(".")[0]}
    return {"success": True, "value": target}


def _op_set_action(args: Dict[str, Any], definition_path: Path) -> Dict[str, Any]:
    """H-14: write ``visual.visualContainerObjects.visualLink[0].properties``."""
    action_type = args.get("action_type")
    action_target = args.get("action_target", "") or ""
    if not action_type:
        return error_envelope(
            "missing_argument",
            "set_action requires: page_name, visual_name, action_type",
        )
    if action_type not in ACTION_TYPES:
        return {
            "success": False,
            "error": f"Unknown action_type: {action_type}. Valid: {', '.join(ACTION_TYPES)}",
            "error_type": "invalid_action_type",
        }
    targets = _single_target(args, definition_path, "set_action")
    if not targets.get("success"):
        return targets
    file_path = Path(targets["visuals"][0]["file_path"])
    visual_data = _load_json_file(file_path)
    if not visual_data:
        return error_envelope("operation_failed", "Failed to load visual.json")
    target_key = _ACTION_TARGET_KEY.get(action_type)
    resolved_target: Optional[str] = None
    if target_key:
        if not action_target:
            return {
                "success": False,
                "error_type": "invalid_args",
                "error": f"action_type={action_type} requires action_target",
            }
        rt = _resolve_action_target(definition_path, action_type, action_target)
        if not rt.get("success"):
            return rt
        resolved_target = rt["value"]

    vco = (visual_data.get("visual") or {}).get("visualContainerObjects") or {}
    existing = vco.get("visualLink") or []
    existing_props = (
        existing[0].get("properties", {}) if existing and isinstance(existing[0], dict) else {}
    )
    new_props = copy.deepcopy(existing_props)
    new_props["show"] = _lit("true")
    new_props["type"] = _lit(_pbir_string_literal(action_type))
    if target_key and resolved_target is not None:
        new_props[target_key] = _lit(_pbir_string_literal(resolved_target))
    unwritten: List[str] = []
    df_payload = _build_data_function_payload(args) if action_type == "DataFunction" else {}
    if df_payload:
        new_props["dataFunction"] = df_payload
    if action_type == "DataFunction":
        unwritten = [k for k in ("data_function_name", "power_fx") if args.get(k)]
    new_link = [{"properties": new_props}]
    before = existing or None
    legacy_action = "action" in vco
    would_change = before != new_link or legacy_action
    result: Dict[str, Any] = {
        "success": True,
        "operation": "set_action",
        "visual_name": targets["visuals"][0]["visual_name"],
        "action_type": action_type,
        "action_target": action_target,
        "resolved_target": resolved_target,
        "before": before,
        "after": new_link,
    }
    if action_type == "DataFunction":
        result["warning"] = (
            "DataFunction: only the item/workspace reference is written (the shape "
            "Desktop uses for visualLink.dataFunction). Function parameters / Power Fx "
            "have no verified PBIR location — bind them in Desktop."
        )
        if unwritten:
            result["unwritten_args"] = unwritten
    if args.get("dry_run", False):
        result.update(dry_run=True, status="would_change" if would_change else "no_change")
        return result

    def _mutate(data: Dict) -> None:
        vis = data.setdefault("visual", {})
        v = vis.setdefault("visualContainerObjects", {})
        v.pop("action", None)  # legacy invalid key written by older versions
        v["visualLink"] = copy.deepcopy(new_link)

    safe_io.update_json(file_path, _mutate)
    result.update(dry_run=False, status="changed" if would_change else "no_change")
    return result


def _op_inject_code(args: Dict[str, Any], definition_path: Path) -> Dict[str, Any]:
    """Deneb -> ``objects.vega[0].properties.{jsonSpec, provider}`` (H-14).

    Python / R -> ``objects.script[0].properties.{source, provider}``.
    Other properties of the entry (render mode, …) are preserved.
    """
    code_type = args.get("code_type")
    code = args.get("code", "")
    if not code_type or not code:
        return error_envelope(
            "missing_argument",
            "inject_code requires: page_name, visual_name, code_type, code",
        )
    if code_type not in ("deneb", "python", "r"):
        return error_envelope(
            "invalid_argument",
            f"Unknown code_type: {code_type}. Use deneb, python, or r",
        )
    targets = _single_target(args, definition_path, "inject_code")
    if not targets.get("success"):
        return targets
    file_path = Path(targets["visuals"][0]["file_path"])
    if code_type == "deneb":
        card = "vega"
        provider = args.get("provider", "vegaLite")
        props = {
            "jsonSpec": _lit(_pbir_string_literal(code)),
            "provider": _lit(_pbir_string_literal(provider)),
        }
    else:
        card = "script"
        props = {
            "source": _lit(_pbir_string_literal(code)),
            "provider": _lit(_pbir_string_literal("Python" if code_type == "python" else "R")),
        }
    dry_run = bool(args.get("dry_run", False))

    def _mutate(data: Dict) -> None:
        objects = data.setdefault("visual", {}).setdefault("objects", {})
        objects.pop("denebConfig", None)  # legacy invalid key written by older versions
        entries = objects.setdefault(card, [])
        if not entries:
            entries.append({"properties": {}})
        entries[0].setdefault("properties", {}).update(copy.deepcopy(props))

    if not dry_run:
        safe_io.update_json(file_path, _mutate)
    return {
        "success": True,
        "operation": "inject_code",
        "dry_run": dry_run,
        "visual_name": targets["visuals"][0]["visual_name"],
        "code_type": code_type,
        "code_length": len(code),
        "written_to": f"visual.objects.{card}[0].properties",
    }


_VCO_TARGETS = {
    "title",
    "subTitle",
    "subtitle",
    "divider",
    "background",
    "border",
    "dropShadow",
    "shadow",
    "padding",
    "spacing",
    "visualHeader",
    "header",
    "visualTooltip",
    "tooltip",
    "visualLink",
    "lockAspect",
    "general",
}
_VCO_ALIASES = {
    "subtitle": "subTitle",
    "shadow": "dropShadow",
    "header": "visualHeader",
    "tooltip": "visualTooltip",
}
_OBJ_TARGETS = {"legend", "categoryAxis", "valueAxis", "labels", "dataPoint", "plotArea"}


def _op_update_formatting(args: Dict[str, Any], definition_path: Path) -> Dict[str, Any]:
    formatting_target = args.get("formatting_target")
    formatting_properties = args.get("formatting_properties", {})
    dry_run = args.get("dry_run", False)
    if not formatting_target or not formatting_properties:
        return error_envelope(
            "missing_argument",
            "update_formatting requires: formatting_target and formatting_properties",
        )
    is_vc = formatting_target in _VCO_TARGETS
    is_obj = formatting_target in _OBJ_TARGETS
    if not is_vc and not is_obj:
        return error_envelope(
            "invalid_argument",
            f"Unknown formatting_target: {formatting_target}. "
            f'Valid: {", ".join(sorted(_VCO_TARGETS | _OBJ_TARGETS))}',
        )
    if is_obj:
        remove_property = args.get("remove_property", False)
        updated_args = dict(args)
        updated_args["config_updates"] = [
            {
                "config_type": formatting_target,
                "property_name": k,
                "property_value": v,
                "value_type": "auto",
                "remove_property": remove_property,
            }
            for k, v in formatting_properties.items()
        ]
        updated_args["dry_run"] = dry_run
        return _op_update_visual_config(updated_args, definition_path)
    card = _VCO_ALIASES.get(formatting_target, formatting_target)
    targets = resolve_targets(definition_path, args, write=True, op="update_formatting")
    if not targets.get("success"):
        return targets
    encoded = {k: _property_value(k, v) for k, v in formatting_properties.items()}

    def _mutate(visual_data: Dict) -> None:
        vis = visual_data.setdefault("visual", {})
        vc_objects = vis.setdefault("visualContainerObjects", {})
        # H-14: fold a legacy top-level visualContainerObjects (written by
        # older versions of this tool) into the real location.
        legacy = visual_data.pop("visualContainerObjects", None)
        if isinstance(legacy, dict):
            for k, v in legacy.items():
                vc_objects.setdefault(k, v)
        target_array = vc_objects.setdefault(card, [])
        target_entry = next((e for e in target_array if not e.get("selector")), None)
        if target_entry is None:
            target_entry = {"properties": {}}
            target_array.insert(0, target_entry)
        target_entry.setdefault("properties", {}).update(copy.deepcopy(encoded))

    changes: List = []
    errors: List = []
    for visual in targets["visuals"]:
        file_path = Path(visual["file_path"])
        record: Dict = {
            "display_title": visual["display_title"],
            "page_name": visual.get("page_name", ""),
            "visual_name": visual["visual_name"],
            "visual_type": visual["visual_type"],
            "config_changes": [
                f"visualContainerObjects.{card}.{k} = {v}" for k, v in formatting_properties.items()
            ],
            "status": "would_change" if dry_run else "changed",
        }
        if not dry_run:
            try:
                safe_io.update_json(file_path, _mutate)
            except safe_io.WriteError as e:
                record["status"] = "error"
                errors.append({"file_path": str(file_path), "error": str(e)})
        changes.append(record)
    result: Dict = {
        "success": len(errors) == 0,
        "operation": "update_formatting",
        "dry_run": dry_run,
        "matched": targets["matched"],
        "message": (
            f'{"Would update" if dry_run else "Updated"} formatting in {len(changes)} visual(s)'
        ),
        "changes": changes,
        "changes_count": len(changes),
    }
    if errors:
        result["errors"] = errors
        result["errors_count"] = len(errors)
        result["message"] += f" with {len(errors)} error(s)"
    return result


def _op_manage_visual_calcs(args: Dict[str, Any], definition_path: Path) -> Dict[str, Any]:
    from core.pbip.visual_calculations_engine import (
        add_calculation,
        delete_calculation,
        list_calculations,
        update_calculation,
    )

    page = args.get("page_id") or args.get("page_name")
    visual = args.get("visual_id") or args.get("visual_name")
    sub_op = args.get("sub_operation", "list")
    if sub_op == "list":
        return list_calculations(definition_path, page, visual)
    elif sub_op == "add":
        return add_calculation(
            definition_path,
            page or "",
            visual or "",
            args.get("calc_name", ""),
            args.get("expression", ""),
            bucket=args.get("bucket"),
        )
    elif sub_op == "update":
        return update_calculation(
            definition_path,
            page or "",
            visual or "",
            args.get("calc_name", ""),
            args.get("expression"),
        )
    elif sub_op == "delete":
        return delete_calculation(
            definition_path,
            page or "",
            visual or "",
            args.get("calc_name", ""),
        )
    return error_envelope("invalid_operation", f"Unknown sub_operation: {sub_op}")


def _coerce_raw_value(value: Any, prior: Any, exists: bool, force_type: bool) -> Any:
    """H-05a: a JSON object/array passed as a STRING is parsed, not written verbatim.

    Returns the value to write, or raises ValueError on a type mismatch
    against the existing node (unless ``force_type``).
    """
    if isinstance(value, str) and not force_type:
        stripped = value.strip()
        if stripped[:1] in ("{", "[") and stripped[-1:] in ("}", "]"):
            try:
                parsed = json.loads(stripped)
            except ValueError:
                parsed = None
            if isinstance(parsed, (dict, list)):
                value = parsed
    if exists and not force_type and prior is not None and value is not None:
        prior_container = isinstance(prior, (dict, list))
        new_container = isinstance(value, (dict, list))
        if prior_container != new_container or (prior_container and type(prior) is not type(value)):
            raise ValueError(
                f"type mismatch: the existing value is a {type(prior).__name__}, the new "
                f"value a {type(value).__name__}. Pass force_type=true to replace it anyway."
            )
    return value


def _op_set_raw_property(args: Dict[str, Any], definition_path: Path) -> Dict[str, Any]:
    json_path = args.get("json_path")
    value = args.get("value")
    wrap_literal = bool(args.get("wrap_literal", False))
    dry_run = bool(args.get("dry_run", False))
    create_missing = bool(args.get("create_missing", True))
    force_type = bool(args.get("force_type", False))
    if not (args.get("visual_name") or args.get("visual_id")) or not (
        args.get("page_name") or args.get("page_id")
    ):
        return error_envelope(
            "missing_argument",
            "set_raw_property requires: visual_name, page_name",
        )
    if not json_path:
        return error_envelope(
            "missing_argument",
            'json_path is required, e.g. "visual.visualContainerObjects.title[0].'
            'properties.text"',
        )
    if value is None and not args.get("allow_null", False):
        return error_envelope(
            "missing_argument",
            "value is required (use allow_null=true to write null, or use "
            "delete_raw_property to remove)",
        )
    try:
        tokens = _parse_json_path(json_path)
    except ValueError as e:
        return {"success": False, "error_type": "invalid_json_path", "error": str(e)}
    if not tokens:
        return error_envelope("operation_failed", f"Could not parse json_path: {json_path}")
    targets = _single_target(args, definition_path, "set_raw_property")
    if not targets.get("success"):
        return targets
    final_value = value
    if wrap_literal and not isinstance(value, (dict, list)) and value is not None:
        final_value = _property_value("", value)
    changed: List = []
    errors: List = []
    for v in targets["visuals"]:
        fp = Path(v["file_path"])
        outcome: Dict[str, Any] = {}

        def _mutate(data: Dict, _outcome: Dict = outcome) -> Any:
            parent, last, exists, err = _traverse_json_path(
                data, tokens, create_missing=create_missing
            )
            if err:
                _outcome["error"] = err
                return _SKIP
            prior = parent[last] if exists else None
            try:
                new_value = _coerce_raw_value(copy.deepcopy(final_value), prior, exists, force_type)
            except ValueError as e:
                _outcome["error"] = str(e)
                return _SKIP
            if isinstance(last, int):
                if exists:
                    parent[last] = new_value
                elif last == len(parent):
                    parent.append(new_value)
                else:
                    _outcome["error"] = f"cannot create list index {last} (len={len(parent)})"
                    return _SKIP
            else:
                parent[last] = new_value
            _outcome["prior"] = prior
            _outcome["new"] = new_value
            return None

        try:
            if dry_run:
                data = _load_json_file(fp)
                if not data:
                    errors.append({"file_path": str(fp), "error": "load failed"})
                    continue
                _mutate(copy.deepcopy(data))
            else:
                safe_io.update_json(fp, _mutate)
        except safe_io.WriteError as e:
            errors.append({"file_path": str(fp), "error": str(e)})
            continue
        if "error" in outcome:
            errors.append({"file_path": str(fp), "error": outcome["error"]})
            continue
        changed.append(
            {
                "file_path": str(fp),
                "json_path": json_path,
                "prior_value": outcome.get("prior"),
                "new_value": outcome.get("new"),
                "value_type": type(outcome.get("new")).__name__,
                "status": "would_change" if dry_run else "changed",
            }
        )
    return {
        "success": not errors,
        "operation": "set_raw_property",
        "dry_run": dry_run,
        "wrap_literal": wrap_literal,
        "changes": changed,
        "changes_count": len(changed),
        "errors": errors or None,
    }


def _op_get_raw_property(args: Dict[str, Any], definition_path: Path) -> Dict[str, Any]:
    json_path = args.get("json_path")
    if not (args.get("visual_name") or args.get("visual_id")) or not (
        args.get("page_name") or args.get("page_id")
    ):
        return error_envelope(
            "missing_argument",
            "get_raw_property requires: visual_name, page_name",
        )
    if not json_path:
        return error_envelope("missing_argument", "json_path is required")
    try:
        tokens = _parse_json_path(json_path)
    except ValueError as e:
        return {"success": False, "error_type": "invalid_json_path", "error": str(e)}
    targets = resolve_targets(definition_path, args, write=True, op="get_raw_property", single=True)
    if not targets.get("success"):
        return targets
    data = _load_json_file(Path(targets["visuals"][0]["file_path"]))
    if not data:
        return error_envelope("operation_failed", "Failed to load visual.json")
    parent, last, exists, err = _traverse_json_path(data, tokens, create_missing=False)
    if err:
        if "does not exist" in err:
            return {"success": True, "exists": False, "json_path": json_path, "value": None}
        return error_envelope("operation_failed", err)
    if not exists:
        return {"success": True, "exists": False, "json_path": json_path, "value": None}
    value = parent[last] if isinstance(last, int) else parent.get(last)
    return {"success": True, "exists": True, "json_path": json_path, "value": value}


def _op_delete_raw_property(args: Dict[str, Any], definition_path: Path) -> Dict[str, Any]:
    json_path = args.get("json_path")
    dry_run = bool(args.get("dry_run", False))
    if not (args.get("visual_name") or args.get("visual_id")) or not (
        args.get("page_name") or args.get("page_id")
    ):
        return error_envelope(
            "missing_argument",
            "delete_raw_property requires: visual_name, page_name",
        )
    if not json_path:
        return error_envelope("missing_argument", "json_path is required")
    try:
        tokens = _parse_json_path(json_path)
    except ValueError as e:
        return {"success": False, "error_type": "invalid_json_path", "error": str(e)}
    targets = _single_target(args, definition_path, "delete_raw_property")
    if not targets.get("success"):
        return targets
    changed: List = []
    errors: List = []
    for v in targets["visuals"]:
        fp = Path(v["file_path"])
        outcome: Dict[str, Any] = {}

        def _mutate(data: Dict, _outcome: Dict = outcome) -> Any:
            parent, last, exists, err = _traverse_json_path(data, tokens, create_missing=False)
            if err and "does not exist" not in err:
                _outcome["error"] = err
                return _SKIP
            if err or not exists:
                _outcome["missing"] = True
                return _SKIP
            _outcome["prior"] = parent.pop(last)
            return None

        try:
            if dry_run:
                data = _load_json_file(fp)
                if not data:
                    errors.append({"file_path": str(fp), "error": "load failed"})
                    continue
                _mutate(copy.deepcopy(data))
            else:
                safe_io.update_json(fp, _mutate)
        except safe_io.WriteError as e:
            errors.append({"file_path": str(fp), "error": str(e)})
            continue
        if "error" in outcome:
            errors.append({"file_path": str(fp), "error": outcome["error"]})
            continue
        if outcome.get("missing"):
            continue
        changed.append(
            {
                "file_path": str(fp),
                "json_path": json_path,
                "prior_value": outcome.get("prior"),
                "status": "would_change" if dry_run else "changed",
            }
        )
    return {
        "success": not errors,
        "operation": "delete_raw_property",
        "dry_run": dry_run,
        "changes": changed,
        "changes_count": len(changed),
        "errors": errors or None,
    }
