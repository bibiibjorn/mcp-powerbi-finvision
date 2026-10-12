"""INTERNAL HELPER — Not a registered MCP tool.

clear_default_selection / set_default_selection ops.

A slicer's persisted selection lives in TWO places Desktop keeps in sync:
``visual.objects.general[0].properties.filter`` and the slicer's own
``filterConfig.filters[]`` entry on its bound column (whose
``objects.general[].properties.isInvertedSelectionMode`` carries the
polarity). Deep review 2026-09-22 (H-10 / H-27 / H-30):

* both locations are read AND written together (clearing only
  ``general.filter`` used to leave the filterConfig still filtering);
* polarity is read first: on an exclude-mode ("select all except") slicer
  ``set_default_selection`` refuses unless the caller says which polarity to
  write (``polarity="exclude"`` keeps the mode and sets the EXCLUDED list,
  ``polarity="include"`` switches to a positive selection); the result always
  states ``polarity_applied``; an empty value list is never written;
* the value type comes from the bound model's TMDL through
  ``definition.pbir`` (thin reports -- H-27), never from whatever model the
  live connection happens to point at;
* only the named page is scanned (H-30); writes are locked RMWs that keep the
  file's byte traits.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.pbip import pbir_model
from core.pbip.authoring import safe_io
from core.pbip.authoring.targets import resolve_page_dir
from core.utilities.pbip_utils import SKIP_WRITE, load_json_file
from server.handlers._internal.slicer_helpers import (
    _find_slicers,
    slicer_filter_entries,
    slicer_polarity,
)
from core.validation.error_response import error_envelope


def _resolve_target_slicer(
    args: Dict[str, Any],
    definition_path: Path,
) -> Dict[str, Any]:
    """Find the single matching slicer on ONE page.

    Returns either {"slicer": <info>} on unique match, or {"error": str,
    "candidates": [...]} on no / ambiguous match.
    """
    page = args.get("page_id") or args.get("page_name")
    visual_name = args.get("visual_name") or args.get("visual_id")
    display_name = args.get("display_name")
    entity = args.get("entity")
    property_name = args.get("property")

    page_dir, err = resolve_page_dir(Path(definition_path), page)
    if err:
        return {k: v for k, v in err.items() if k != "success"}
    slicers = _find_slicers(
        definition_path, display_name, entity, property_name, page=page_dir.name
    )
    if visual_name:
        slicers = [s for s in slicers if s["visual_name"] == visual_name]

    if not slicers:
        return {"error": "No slicer matches the given identifiers", "candidates": []}
    if len(slicers) > 1:
        return {
            "error": "ambiguous match — use visual_name to disambiguate",
            "candidates": [
                {
                    "visual_name": s["visual_name"],
                    "page_name": s.get("page_name"),
                    "title": s.get("title"),
                    "field_reference": s.get("field_reference"),
                }
                for s in slicers
            ],
        }
    return {"slicer": slicers[0]}


def _general_entries(visual_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    objects = visual_data.setdefault("visual", {}).setdefault("objects", {})
    general = objects.setdefault("general", [])
    if not general:
        general.append({"properties": {}})
    return general


def _op_clear_default_selection(
    args: Dict[str, Any],
    definition_path: Path,
) -> Dict[str, Any]:
    dry_run: bool = bool(args.get("dry_run", False))
    if not (args.get("page_name") or args.get("page_id")):
        return error_envelope("missing_argument", "page_name is required")

    resolved = _resolve_target_slicer(args, definition_path)
    if "slicer" not in resolved:
        return {"success": False, **resolved}
    slicer = resolved["slicer"]
    file_path = Path(slicer["file_path"])
    before = load_json_file(file_path) or {}
    polarity_before = slicer_polarity(before, slicer["entity"], slicer["property"])
    prior = {
        "general_filter": pbir_model.get_slicer_selection_filter(before),
        "filterConfig": copy.deepcopy(
            slicer_filter_entries(before, slicer["entity"], slicer["property"])
        ),
    }
    cleared: List[str] = []

    def _mutate(data: Dict[str, Any]) -> Any:
        for entry in (data.get("visual") or {}).get("objects", {}).get("general") or []:
            props = entry.get("properties") if isinstance(entry, dict) else None
            if isinstance(props, dict) and "filter" in props:
                del props["filter"]
                cleared.append("visual.objects.general.filter")
        for f in slicer_filter_entries(data, slicer["entity"], slicer["property"]):
            if "filter" in f:
                # Keep the entry (and its polarity flag): a field-only entry
                # = no selection; an inverted one with no filter = exclude
                # nothing. Either way nothing is filtered any more.
                del f["filter"]
                cleared.append(f"filterConfig.filters[{f.get('name')}].filter")
        return None if cleared else SKIP_WRITE

    if dry_run:
        _mutate(copy.deepcopy(before))
        return {
            "success": True,
            "dry_run": True,
            "would_clear": {"visual_name": slicer["visual_name"], "locations": cleared},
            "polarity_before": polarity_before,
            "prior_state": prior,
        }
    changed = safe_io.update_json(file_path, _mutate)
    if not changed:
        return {
            "success": True,
            "status": "already_cleared",
            "visual_name": slicer["visual_name"],
            "polarity_before": polarity_before,
        }
    return {
        "success": True,
        "status": "cleared",
        "visual_name": slicer["visual_name"],
        "cleared_locations": cleared,
        "polarity_before": polarity_before,
        "prior_state": prior,
    }


_VALUE_TYPE_TO_KIND = {
    "string": "string",
    "int": "int64",
    "double": "double",
    "bool": "bool",
    "datetime": "datetime",
    "decimal": "decimal",
}


def _format_literal(value: Any, value_type: str) -> str:
    """Format a Python value as a PBIR Literal.Value string, per type."""
    kind = _VALUE_TYPE_TO_KIND.get(value_type)
    if kind is None:
        raise ValueError(f"unsupported value_type: {value_type!r}")
    return pbir_model.encode_literal(value, kind)


def _condition(slicer: Dict, alias: str, values: List[Any], value_type: str, exclude: bool) -> Dict:
    inner = {
        "In": {
            "Expressions": [
                {
                    "Column": {
                        "Expression": {"SourceRef": {"Source": alias}},
                        "Property": slicer["property"],
                    }
                }
            ],
            "Values": [[{"Literal": {"Value": _format_literal(v, value_type)}}] for v in values],
        }
    }
    if exclude:
        return {"Not": {"Expression": inner}}
    return inner


def _build_filter_dict(
    slicer: Dict, values: List[Any], value_type: str, *, exclude: bool = False
) -> Dict:
    """The PBIR query filter ``{Version, From, Where}`` for the slicer's bound column."""
    entity = slicer["entity"]
    source_letter = next((c for c in entity.lower() if c.isalpha()), "t")
    return {
        "filter": {
            "Version": 2,
            "From": [{"Name": source_letter, "Entity": entity, "Type": 0}],
            "Where": [
                {"Condition": _condition(slicer, source_letter, values, value_type, exclude)}
            ],
        }
    }


def _set_inverted_flag(objects_general: List[Dict[str, Any]], value: bool) -> None:
    if value:
        if not objects_general:
            objects_general.append({"properties": {}})
        objects_general[0].setdefault("properties", {})["isInvertedSelectionMode"] = {
            "expr": {"Literal": {"Value": "true"}}
        }
    else:
        for entry in objects_general:
            props = entry.get("properties") if isinstance(entry, dict) else None
            if isinstance(props, dict):
                props.pop("isInvertedSelectionMode", None)


def _op_set_default_selection(
    args: Dict[str, Any],
    definition_path: Path,
) -> Dict[str, Any]:
    values = args.get("values")
    if not values:
        return {
            "success": False,
            "error_type": "empty_in_list",
            "error": (
                "'values' is required (non-empty list) — an empty In list hard-fails "
                "visuals. Use clear_default_selection to remove the selection."
            ),
        }
    value_type: Optional[str] = args.get("value_type")
    requested_polarity: Optional[str] = args.get("polarity")
    if requested_polarity not in (None, "include", "exclude"):
        return {
            "success": False,
            "error_type": "invalid_args",
            "error": "polarity must be 'include' or 'exclude'",
        }
    dry_run: bool = bool(args.get("dry_run", False))
    if not (args.get("page_name") or args.get("page_id")):
        return error_envelope("missing_argument", "page_name is required")

    resolved = _resolve_target_slicer(args, definition_path)
    if "slicer" not in resolved:
        return {"success": False, **resolved}
    slicer = resolved["slicer"]
    file_path = Path(slicer["file_path"])
    current = load_json_file(file_path) or {}
    polarity_before = slicer_polarity(current, slicer["entity"], slicer["property"])
    if polarity_before == "exclude" and requested_polarity is None:
        return {
            "success": False,
            "error_type": "polarity_required",
            "error": (
                "This slicer is in INVERTED selection mode ('select all except'): its "
                "values list names what is EXCLUDED. Pass polarity='exclude' to set the "
                "excluded values (keeps the mode) or polarity='include' to switch to a "
                "positive selection of exactly these values."
            ),
            "polarity_before": polarity_before,
            "current_values": slicer.get("selected_values"),
        }
    polarity = requested_polarity or "include"
    exclude = polarity == "exclude"

    value_type, warning = _resolve_value_type(slicer, definition_path, value_type)
    try:
        filter_dict = _build_filter_dict(slicer, values, value_type, exclude=exclude)
    except (ValueError, TypeError) as e:
        return {"success": False, "error_type": "invalid_value", "error": str(e)}

    def _mutate(data: Dict[str, Any]) -> None:
        general = _general_entries(data)
        general[0].setdefault("properties", {})["filter"] = copy.deepcopy(filter_dict)
        # Most real slicers (1,227 of 1,946 in R0105+R0201) persist their
        # selection ONLY in general.filter; the filterConfig entry is updated
        # when it exists, never invented.
        entries = slicer_filter_entries(data, slicer["entity"], slicer["property"])
        for f in entries:
            f["filter"] = copy.deepcopy(filter_dict["filter"])
            f.setdefault("type", "Categorical")
            gen = f.setdefault("objects", {}).setdefault("general", [])
            _set_inverted_flag(gen, exclude)
            if not gen or gen == [{"properties": {}}]:
                f["objects"].pop("general", None)
            if not f.get("objects"):
                f.pop("objects", None)
        # Visual-level inverted flag must agree: without a filterConfig entry it
        # is the only place the exclude mode can live.
        objects = data["visual"]["objects"]
        data_cfg = objects.get("data") or []
        has_flag = any(
            isinstance(e, dict) and "isInvertedSelectionMode" in (e.get("properties") or {})
            for e in data_cfg
        )
        if has_flag or (exclude and not entries):
            if not data_cfg:
                data_cfg = objects.setdefault("data", [{"properties": {}}])
            data_cfg[0].setdefault("properties", {})["isInvertedSelectionMode"] = {
                "expr": {"Literal": {"Value": "true" if exclude else "false"}}
            }

    result_base = {
        "visual_name": slicer["visual_name"],
        "values": values,
        "value_type": value_type,
        "polarity_before": polarity_before,
        "polarity_applied": polarity,
        **({"warning": warning} if warning else {}),
    }
    if dry_run:
        return {"success": True, "dry_run": True, "would_set": result_base, **result_base}
    safe_io.update_json(file_path, _mutate)
    return {"success": True, "status": "set", **result_base}


_TMDL_DATATYPE_MAP = {
    "int64": "int",
    "double": "double",
    "decimal": "decimal",
    "boolean": "bool",
    "datetime": "datetime",
    "date": "datetime",
    "string": "string",
}


def _detect_via_tmdl(definition_path: Path, entity: str, prop: str) -> Optional[str]:
    """Column dataType from the model the report is BOUND to (definition.pbir byPath).

    H-27: the old code only looked for a same-stem ``<Report>.SemanticModel``
    sibling, which never exists for thin reports bound to a shared model.
    """
    from core.pbip.authoring.model_lookup import column_type

    dt = column_type(definition_path, entity, prop)
    return _TMDL_DATATYPE_MAP.get(dt) if dt else None


def _resolve_value_type(
    slicer: Dict,
    definition_path: Path,
    explicit: Optional[str],
) -> tuple:
    """Return (value_type, warning_or_None).

    Order: explicit arg -> bound model TMDL -> 'string' (with a warning). The
    old live-DAX probe was removed: it queried whatever model the connection
    pointed at, which can be a different Desktop instance than this report's.
    """
    if explicit:
        return explicit, None
    detected = _detect_via_tmdl(definition_path, slicer["entity"], slicer["property"])
    if detected:
        return detected, None
    return "string", (
        "value_type defaulted to 'string' (column type not found in the bound model's "
        "TMDL); pass value_type explicitly if PBI doesn't apply the filter"
    )
