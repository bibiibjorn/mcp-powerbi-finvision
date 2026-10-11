"""
Extension Measures Engine for PBIP Reports

Manages report-level extension measures stored in definition/reportExtensions.json.
Extension measures are DAX measures defined at the report level rather than in
the semantic model (thin reports).

H-42 (deep review 2026-09-22), defensive fixes — no reportExtensions.json
exists in the R0105/R0201 corpus, so the shape follows the published PBIR
``reportExtension`` schema:

* top-level ``"name": "extension"`` is written when missing;
* ``dataType`` is written in the TOM casing (``Double`` / ``Int64`` /
  ``Decimal`` / ``String`` / ``Boolean`` / ``DateTime``) and read in any case;
* ``delete_measure`` refuses while visuals still reference the measure
  (``referenced_by``) unless ``force=True``;
* ``update_measure`` accepts ``data_type`` (the handler always passed it —
  the old signature raised ``TypeError``);
* writes are locked RMWs through ``safe_io``.

NEEDS LIVE CHECK: create one extension measure in Desktop and diff its
reportExtensions.json against ``add_measure`` output (``$schema`` version,
``dataType`` casing, ``references`` block).

No MCP awareness — pure domain logic operating on PBIP file structures.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.pbip import pbir_model
from core.pbip.authoring import safe_io
from core.utilities.pbip_utils import SKIP_WRITE, load_json_file

logger = logging.getLogger(__name__)

# Schema version for a NEW reportExtensions.json (an existing file keeps its own).
_EXTENSIONS_SCHEMA = (
    "https://developer.microsoft.com/json-schemas/fabric/item/report/"
    "definition/reportExtension/1.0.0/schema.json"
)

_DATA_TYPES = {
    "double": "Double",
    "number": "Double",
    "int64": "Int64",
    "int": "Int64",
    "integer": "Int64",
    "decimal": "Decimal",
    "currency": "Decimal",
    "string": "String",
    "text": "String",
    "boolean": "Boolean",
    "bool": "Boolean",
    "datetime": "DateTime",
    "date": "DateTime",
}


def _norm_data_type(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return _DATA_TYPES.get(str(value).lower(), str(value))


def _get_extensions_path(definition_path: Path) -> Path:
    return Path(definition_path) / "reportExtensions.json"


def _load_extensions(definition_path: Path) -> Optional[Dict[str, Any]]:
    ext_path = _get_extensions_path(definition_path)
    if not ext_path.exists():
        return None
    return load_json_file(ext_path)


def _skeleton() -> Dict[str, Any]:
    return {"$schema": _EXTENSIONS_SCHEMA, "name": "extension", "entities": []}


def _find_measure(entities: List[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
    for ei, entity in enumerate(entities):
        for mi, measure in enumerate(entity.get("measures", []) or []):
            if measure.get("name") == name:
                return {
                    "entity_index": ei,
                    "measure_index": mi,
                    "measure": measure,
                    "table": entity.get("name", ""),
                }
    return None


def _find_or_create_entity(entities: List[Dict[str, Any]], table_ref: str) -> Dict[str, Any]:
    for entity in entities:
        if entity.get("name") == table_ref:
            return entity
    new_entity: Dict[str, Any] = {"name": table_ref, "measures": []}
    entities.append(new_entity)
    return new_entity


def measure_references(definition_path: Path, table: str, name: str) -> List[Dict[str, str]]:
    """Visuals / page / report filters that reference ``table[name]`` as a Measure."""
    import json as _json

    needle_prop = _json.dumps(name)
    refs: List[Dict[str, str]] = []

    def _refers(obj: Any) -> bool:
        if isinstance(obj, dict):
            m = obj.get("Measure")
            if isinstance(m, dict) and m.get("Property") == name:
                ent = ((m.get("Expression") or {}).get("SourceRef") or {}).get("Entity")
                if ent in (table, None):
                    return True
            return any(_refers(v) for v in obj.values())
        if isinstance(obj, list):
            return any(_refers(v) for v in obj)
        return False

    for rec in pbir_model.iter_visuals(definition_path):
        if needle_prop in _json.dumps(rec.data) and _refers(rec.data):
            refs.append({"page_id": rec.page_dir.name, "visual_name": rec.visual_dir.name})
    return refs


def list_measures(definition_path: Path) -> Dict[str, Any]:
    """List all extension measures from reportExtensions.json, grouped by table."""
    data = _load_extensions(definition_path)
    if data is None:
        return {
            "success": True,
            "measures": [],
            "total_count": 0,
            "note": "No reportExtensions.json found — no extension measures defined",
        }
    entities = data.get("entities", []) or []
    measures_list: List[Dict[str, Any]] = []
    for entity in entities:
        table_name = entity.get("name", "")
        for measure in entity.get("measures", []) or []:
            measures_list.append(
                {
                    "name": measure.get("name", ""),
                    "table": table_name,
                    "expression": measure.get("expression", ""),
                    "data_type": measure.get("dataType", ""),
                    "format_string": measure.get("formatString"),
                    "description": measure.get("description"),
                }
            )
    return {
        "success": True,
        "measures": measures_list,
        "total_count": len(measures_list),
        "entity_count": len(entities),
    }


def add_measure(
    definition_path: Path,
    name: str,
    expression: str,
    table_ref: str,
    data_type: str = "double",
    format_string: str = None,
    description: str = None,
) -> Dict[str, Any]:
    """Add a new extension measure to reportExtensions.json."""
    if not name or not name.strip():
        return {"success": False, "error": "Measure name is required"}
    if not expression or not expression.strip():
        return {"success": False, "error": "Measure expression is required"}
    if not table_ref or not table_ref.strip():
        return {"success": False, "error": "Table reference (table_ref) is required"}
    out: Dict[str, Any] = {}

    def _mutate(data: Dict[str, Any]) -> Any:
        data.setdefault("name", "extension")
        entities = data.setdefault("entities", [])
        existing = _find_measure(entities, name)
        if existing is not None:
            out["duplicate"] = existing["table"]
            return SKIP_WRITE
        entity = _find_or_create_entity(entities, table_ref)
        entry: Dict[str, Any] = {
            "name": name,
            "dataType": _norm_data_type(data_type) or "Double",
            "expression": expression,
        }
        if format_string is not None:
            entry["formatString"] = format_string
        if description is not None:
            entry["description"] = description
        entity.setdefault("measures", []).append(entry)
        out["data_type"] = entry["dataType"]
        return None

    safe_io.update_json(
        _get_extensions_path(definition_path),
        _mutate,
        create_if_missing=True,
        default_factory=_skeleton,
    )
    if "duplicate" in out:
        return {
            "success": False,
            "error": (
                f"Measure '{name}' already exists in entity '{out['duplicate']}'. "
                f"Use update_measure to modify it."
            ),
        }
    return {
        "success": True,
        "measure": name,
        "table": table_ref,
        "expression": expression,
        "data_type": out.get("data_type"),
    }


def update_measure(
    definition_path: Path,
    name: str,
    expression: str = None,
    format_string: str = None,
    description: str = None,
    data_type: str = None,
) -> Dict[str, Any]:
    """Update an existing extension measure. None parameters are left unchanged."""
    if expression is None and format_string is None and description is None and data_type is None:
        return {"success": False, "error": "At least one field to update must be provided"}
    if _load_extensions(definition_path) is None:
        return {"success": False, "error": "No reportExtensions.json found"}
    out: Dict[str, Any] = {"updated": []}

    def _mutate(data: Dict[str, Any]) -> Any:
        found = _find_measure(data.get("entities", []) or [], name)
        if found is None:
            out["missing"] = True
            return SKIP_WRITE
        measure = found["measure"]
        out["table"] = found["table"]
        for key, value in (
            ("expression", expression),
            ("formatString", format_string),
            ("description", description),
            ("dataType", _norm_data_type(data_type)),
        ):
            if value is not None:
                measure[key] = value
                out["updated"].append(key)
        data.setdefault("name", "extension")
        return None

    safe_io.update_json(_get_extensions_path(definition_path), _mutate)
    if out.get("missing"):
        return {"success": False, "error": f"Measure '{name}' not found in reportExtensions"}
    return {
        "success": True,
        "measure": name,
        "table": out.get("table"),
        "updated_fields": out["updated"],
    }


def delete_measure(definition_path: Path, name: str, force: bool = False) -> Dict[str, Any]:
    """Delete an extension measure (refused while visuals reference it unless ``force``)."""
    data = _load_extensions(definition_path)
    if data is None:
        return {"success": False, "error": "No reportExtensions.json found"}
    found = _find_measure(data.get("entities", []) or [], name)
    if found is None:
        return {"success": False, "error": f"Measure '{name}' not found in reportExtensions"}
    refs = measure_references(Path(definition_path), found["table"], name)
    if refs and not force:
        return {
            "success": False,
            "error_type": "measure_in_use",
            "error": (
                f"Extension measure '{name}' is still used by {len(refs)} visual(s); deleting "
                "it leaves dangling bindings. Rebind / remove them first, or pass force=true."
            ),
            "referenced_by": refs[:50],
        }
    out: Dict[str, Any] = {}

    def _mutate(d: Dict[str, Any]) -> None:
        entities = d.get("entities", []) or []
        hit = _find_measure(entities, name)
        if hit is None:
            return
        entities[hit["entity_index"]]["measures"].pop(hit["measure_index"])
        if not entities[hit["entity_index"]].get("measures"):
            entities.pop(hit["entity_index"])
        out["remaining"] = sum(len(e.get("measures", [])) for e in entities)

    safe_io.update_json(_get_extensions_path(definition_path), _mutate)
    result = {
        "success": True,
        "deleted_measure": name,
        "table": found["table"],
        "remaining_measures": out.get("remaining", 0),
    }
    if refs:
        result["dangling_references"] = refs[:50]
    return result
