"""
PBIP Conditional Formatting Engine - Manages CF rules on visuals.

Conditional formatting lives in ``visual.json`` under
``visual.objects.{container}[].properties.{prop}``, each entry scoped by a
``selector``. Deep review 2026-09-22 (H-20) — the builders now emit the
shapes Desktop writes (1,540 real CF properties surveyed in R0105/R0201):

* colour scale::

    {"solid": {"color": {"expr": {"FillRule": {
        "Input": <field>,
        "FillRule": {"linearGradient2" | "linearGradient3": {
            "min": {"color": {"Literal": {"Value": "'#hex'"}}[, "value": ...]},
            ["mid": {...},]
            "max": {...},
            "nullColoringStrategy": {"strategy": {"Literal": {"Value": "'asZero'"}}}}}}}}}}

  with selector ``{"data": [{"dataViewWildcard": {"matchingOption": 1}}]}``;
* rules::

    {"solid": {"color": {"expr": {"Conditional": {"Cases": [
        {"Condition": {"Comparison": {"ComparisonKind": k, "Left": <field>,
                                      "Right": {"Literal": ...}}},
         "Value": {"Literal": {"Value": "'#hex'"}}}]}}}}}

  with selector ``{"data": [{"dataViewWildcard": {"matchingOption": 0}}],
  "metadata": "<Table.Measure>"}``.

The rule is placed in the container entry whose ``selector`` matches (never
blindly in ``container_list[0]``). Data bars and icon sets have no verified
sample and are refused rather than written in an invented shape.

All public functions return ``Dict[str, Any]`` with ``success: bool`` plus
data, or ``error: str`` on failure.  No MCP awareness.
"""

import copy
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.pbip import pbir_model
from core.pbip.authoring import safe_io
from core.pbip.authoring.formatting import is_cf_value
from core.utilities.pbip_utils import (
    load_json_file,
    iter_pages as _iter_pages,
    iter_visuals as _iter_visuals,
)

logger = logging.getLogger(__name__)

_OPERATOR_ALIASES = {
    ">": "GreaterThan",
    ">=": "GreaterThanOrEqual",
    "<": "LessThan",
    "<=": "LessThanOrEqual",
    "=": "Equal",
    "==": "Equal",
    "!=": "NotEqual",
    "<>": "NotEqual",
}


def _resolve_page_folder(definition_path: Path, page_name: str) -> Optional[Path]:
    from core.pbip.authoring.targets import resolve_page_dir

    folder, _err = resolve_page_dir(Path(definition_path), page_name)
    return folder


def _resolve_visual(page_folder: Path, visual_name: str) -> Optional[tuple]:
    """Resolve a visual id or exact title to ``(visual_folder, visual_data)``."""
    from core.pbip.authoring.targets import resolve_visual_dir

    vf, _err = resolve_visual_dir(page_folder, visual_name)
    if vf is None:
        return None
    vdata = load_json_file(vf / "visual.json")
    if vdata is None:
        return None
    return vf, vdata


def _is_cf_property(prop_value: Any) -> bool:
    """True when a property value is conditional / data-driven formatting (real shapes)."""
    return is_cf_value(prop_value)


def _cf_expr(prop_value: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    expr = prop_value.get("expr")
    if isinstance(expr, dict):
        return expr
    color = (prop_value.get("solid") or {}).get("color")
    expr = color.get("expr") if isinstance(color, dict) else None
    return expr if isinstance(expr, dict) else None


def _lit_value(node: Any) -> Any:
    lit = (node or {}).get("Literal") if isinstance(node, dict) else None
    return lit.get("Value") if isinstance(lit, dict) else None


def _field_label(field: Any) -> Dict[str, Any]:
    if not isinstance(field, dict):
        return {}
    for kind in ("Measure", "Column"):
        node = field.get(kind)
        if isinstance(node, dict):
            return {
                "kind": kind,
                "table": ((node.get("Expression") or {}).get("SourceRef") or {}).get("Entity", ""),
                "field": node.get("Property", ""),
            }
    agg = field.get("Aggregation")
    if isinstance(agg, dict):
        inner = _field_label(agg.get("Expression"))
        if inner:
            inner["aggregation"] = agg.get("Function")
        return inner
    return {}


def _extract_cf_info(
    container_name: str,
    prop_name: str,
    prop_value: Any,
    selector: Any = None,
) -> Dict[str, Any]:
    """Build a summary dict describing one CF entry (real shapes)."""
    info: Dict[str, Any] = {
        "container": container_name,
        "property": prop_name,
        "type": "unknown",
        "details": {},
        "selector": selector,
    }
    if "rules" in prop_value:
        info["type"] = "legacy_invented_rules"
        info["details"]["rule_count"] = len(prop_value["rules"])
        info["details"]["note"] = "not a PBIR shape (written by an old tool version) — remove it"
        return info
    expr = _cf_expr(prop_value)
    if not isinstance(expr, dict):
        return info
    if "FillRule" in expr:
        fill_rule = expr["FillRule"]
        info["type"] = "color_scale"
        info["details"]["input"] = _field_label(fill_rule.get("Input"))
        inner = fill_rule.get("FillRule") or {}
        for gkind in ("linearGradient2", "linearGradient3"):
            g = inner.get(gkind)
            if isinstance(g, dict):
                info["details"]["gradient"] = gkind
                for bound in ("min", "mid", "max"):
                    b = g.get(bound)
                    if isinstance(b, dict):
                        color = _lit_value(b.get("color"))
                        info["details"][f"{bound}_color"] = (
                            pbir_model.decode_literal(color)[0] if color else None
                        )
                        if "value" in b:
                            info["details"][f"{bound}_value"] = _lit_value(b.get("value"))
        if "Strategy" in fill_rule:  # old invented shape
            info["type"] = "legacy_invented_color_scale"
        return info
    if "Conditional" in expr:
        info["type"] = "rules"
        cases = expr["Conditional"].get("Cases") or []
        info["details"]["cases"] = len(cases)
        info["details"]["rules"] = [
            {
                "condition": next(iter(c.get("Condition") or {}), None),
                "comparison_kind": ((c.get("Condition") or {}).get("Comparison") or {}).get(
                    "ComparisonKind"
                ),
                "value": _lit_value(c.get("Value")),
            }
            for c in cases[:20]
            if isinstance(c, dict)
        ]
        return info
    if "Measure" in expr:
        info["type"] = "measure_based"
        info["details"].update(_field_label(expr))
        return info
    return info


def _input_expr(definition_path: Path, table: str, field: str, field_type: Optional[str]) -> Dict:
    kind = field_type
    if kind not in ("Measure", "Column"):
        from core.pbip.authoring.model_lookup import field_kind

        kind = field_kind(definition_path, table, field) or "Measure"
    return {kind: {"Expression": {"SourceRef": {"Entity": table}}, "Property": field}}


def _color_lit(color: str) -> Dict[str, Any]:
    return {"Literal": {"Value": pbir_model.encode_literal(str(color), "string")}}


def _num_lit(value: Any) -> Dict[str, Any]:
    return {"Literal": {"Value": pbir_model.encode_literal(value, "double")}}


def _build_color_scale(definition_path: Path, config: Dict[str, Any]) -> Dict[str, Any]:
    table = config.get("table", "")
    field = config.get("field", "")
    if not table or not field:
        raise ValueError("color_scale requires 'table' and 'field' in config")
    bounds: Dict[str, Any] = {}
    for bound, default in (("min", "#FFFFFF"), ("mid", None), ("max", "#118DFF")):
        color = config.get(f"{bound}_color", default)
        if color is None:
            continue
        entry: Dict[str, Any] = {"color": _color_lit(color)}
        if config.get(f"{bound}_value") is not None:
            entry["value"] = _num_lit(config[f"{bound}_value"])
        bounds[bound] = entry
    gradient = "linearGradient3" if "mid" in bounds else "linearGradient2"
    ordered = {k: bounds[k] for k in ("min", "mid", "max") if k in bounds}
    ordered["nullColoringStrategy"] = {
        "strategy": {
            "Literal": {
                "Value": pbir_model.encode_literal(config.get("null_strategy", "asZero"), "string")
            }
        }
    }
    return {
        "solid": {
            "color": {
                "expr": {
                    "FillRule": {
                        "Input": _input_expr(
                            definition_path, table, field, config.get("field_type")
                        ),
                        "FillRule": {gradient: ordered},
                    }
                }
            }
        }
    }


def _build_rules(definition_path: Path, config: Dict[str, Any]) -> Dict[str, Any]:
    rules = config.get("rules") or []
    table = config.get("table", "")
    field = config.get("field", "")
    if not rules:
        raise ValueError("'rules' config requires a 'rules' list of {operator, value, color}")
    if not table or not field:
        raise ValueError("'rules' config requires 'table' and 'field' (the value to test)")
    left = _input_expr(definition_path, table, field, config.get("field_type"))
    cases = []
    for i, rc in enumerate(rules):
        op = _OPERATOR_ALIASES.get(str(rc.get("operator", "")), rc.get("operator"))
        if "color" not in rc or "value" not in rc or not op:
            raise ValueError(f"rules[{i}] needs operator, value and color")
        if op == "NotEqual":
            cond: Dict[str, Any] = {
                "Not": {
                    "Expression": {
                        "Comparison": {
                            "ComparisonKind": 0,
                            "Left": copy.deepcopy(left),
                            "Right": (
                                _num_lit(rc["value"])
                                if isinstance(rc["value"], (int, float))
                                else {"Literal": {"Value": pbir_model.encode_literal(rc["value"])}}
                            ),
                        }
                    }
                }
            }
        else:
            if op not in pbir_model.ADVANCED_FILTER_COMPARISON:
                raise ValueError(f"rules[{i}]: unknown operator {rc.get('operator')!r}")
            right = (
                _num_lit(rc["value"])
                if isinstance(rc["value"], (int, float)) and not isinstance(rc["value"], bool)
                else {"Literal": {"Value": pbir_model.encode_literal(rc["value"])}}
            )
            cond = {
                "Comparison": {
                    "ComparisonKind": pbir_model.ADVANCED_FILTER_COMPARISON[op],
                    "Left": copy.deepcopy(left),
                    "Right": right,
                }
            }
        cases.append({"Condition": cond, "Value": _color_lit(rc["color"])})
    return {"solid": {"color": {"expr": {"Conditional": {"Cases": cases}}}}}


def _default_selector(rule_type: str, selector_metadata: Optional[str]) -> Dict[str, Any]:
    if selector_metadata:
        return {
            "data": [{"dataViewWildcard": {"matchingOption": 0}}],
            "metadata": selector_metadata,
        }
    return {
        "data": [{"dataViewWildcard": {"matchingOption": 1 if rule_type == "color_scale" else 0}}]
    }


def _place(container_list: List[Dict[str, Any]], selector: Any, prop: str, value: Any) -> int:
    """Put *prop* into the entry whose selector equals *selector* (append one if none)."""
    for i, entry in enumerate(container_list):
        if isinstance(entry, dict) and entry.get("selector") == selector:
            entry.setdefault("properties", {})[prop] = value
            return i
    new_entry: Dict[str, Any] = {"properties": {prop: value}}
    if selector is not None:
        new_entry["selector"] = selector
    container_list.append(new_entry)
    return len(container_list) - 1


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_rules(
    definition_path: Path,
    page_name: Optional[str] = None,
    visual_name: Optional[str] = None,
) -> Dict[str, Any]:
    """List all conditional formatting rules across visuals (optionally filtered by page/visual)."""
    definition_path = Path(definition_path)
    if not definition_path.is_dir():
        return {"success": False, "error": f"Definition path not found: {definition_path}"}
    page_id = None
    if page_name:
        pf = _resolve_page_folder(definition_path, page_name)
        if pf is None:
            return {"success": False, "error": f"Page not found: {page_name}"}
        page_id = pf.name

    results: List[Dict[str, Any]] = []
    for page_folder, page_data in _iter_pages(definition_path):
        if page_id and page_folder.name != page_id:
            continue
        display = page_data.get("displayName", page_folder.name)
        target_vid = None
        if visual_name:
            resolved = _resolve_visual(page_folder, visual_name)
            if resolved is None:
                continue
            target_vid = resolved[0].name
        for visual_folder, visual_data in _iter_visuals(page_folder):
            if target_vid and visual_folder.name != target_vid:
                continue
            vis_name = visual_data.get("name", visual_folder.name)
            vis_type = visual_data.get("visual", {}).get("visualType", "")
            objects = visual_data.get("visual", {}).get("objects", {}) or {}
            for container_name, container_entries in objects.items():
                if not isinstance(container_entries, list):
                    continue
                for entry in container_entries:
                    props = entry.get("properties", {}) or {}
                    for prop_name, prop_value in props.items():
                        if _is_cf_property(prop_value):
                            rule_info = _extract_cf_info(
                                container_name, prop_name, prop_value, entry.get("selector")
                            )
                            rule_info["page"] = display
                            rule_info["page_id"] = page_folder.name
                            rule_info["visual"] = vis_name
                            rule_info["visual_type"] = vis_type
                            results.append(rule_info)
    return {"success": True, "rules": results, "count": len(results)}


def add_rule(
    definition_path: Path,
    page_name: str,
    visual_name: str,
    container: str,
    property_name: str,
    rule_type: str,
    config: Dict[str, Any],
    *,
    selector: Optional[Dict[str, Any]] = None,
    selector_metadata: Optional[str] = None,
) -> Dict[str, Any]:
    """Add a CF rule to a visual. rule_type: 'color_scale' or 'rules'."""
    definition_path = Path(definition_path)
    page_folder = _resolve_page_folder(definition_path, page_name)
    if not page_folder:
        return {"success": False, "error": f"Page not found: {page_name}"}
    resolved = _resolve_visual(page_folder, visual_name)
    if not resolved:
        return {"success": False, "error": f"Visual not found: {visual_name}"}
    visual_folder, _visual_data = resolved
    config = config or {}
    selector_metadata = selector_metadata or config.get("selector_metadata")
    try:
        if rule_type == "color_scale":
            cf_value = _build_color_scale(definition_path, config)
        elif rule_type == "rules":
            cf_value = _build_rules(definition_path, config)
        elif rule_type in ("data_bars", "icons"):
            return {
                "success": False,
                "error_type": "unsupported_rule_type",
                "error": (
                    f"{rule_type} has no verified PBIR sample in this toolchain (the old "
                    "writer emitted a shape Desktop ignores). Author it once in Desktop, then "
                    "use copy_cf to replicate it."
                ),
            }
        else:
            return {
                "success": False,
                "error": f"Unknown rule_type: {rule_type}. Use: color_scale, rules",
            }
    except ValueError as e:
        return {"success": False, "error_type": "invalid_cf_config", "error": str(e)}
    sel = selector if selector is not None else _default_selector(rule_type, selector_metadata)
    placed: Dict[str, Any] = {}

    def _mutate(data: Dict[str, Any]) -> None:
        objects = data.setdefault("visual", {}).setdefault("objects", {})
        container_list = objects.setdefault(container, [])
        placed["index"] = _place(container_list, sel, property_name, copy.deepcopy(cf_value))

    safe_io.update_json(visual_folder / "visual.json", _mutate)
    return {
        "success": True,
        "container": container,
        "property": property_name,
        "rule_type": rule_type,
        "selector": sel,
        "entry_index": placed.get("index"),
        "page": page_name,
        "visual": visual_name,
    }


def _selector_matches(entry: Dict[str, Any], selector_metadata: Optional[str]) -> bool:
    if selector_metadata is None:
        return True
    return (entry.get("selector") or {}).get("metadata") == selector_metadata


def remove_rule(
    definition_path: Path,
    page_name: str,
    visual_name: str,
    container: str,
    property_name: str,
    *,
    selector_metadata: Optional[str] = None,
) -> Dict[str, Any]:
    """Remove conditional formatting from a visual property (optionally one selector only)."""
    definition_path = Path(definition_path)
    page_folder = _resolve_page_folder(definition_path, page_name)
    if not page_folder:
        return {"success": False, "error": f"Page not found: {page_name}"}
    resolved = _resolve_visual(page_folder, visual_name)
    if not resolved:
        return {"success": False, "error": f"Visual not found: {visual_name}"}
    visual_folder, visual_data = resolved
    entries = ((visual_data.get("visual") or {}).get("objects") or {}).get(container) or []
    hits = [
        e
        for e in entries
        if isinstance(e, dict)
        and _selector_matches(e, selector_metadata)
        and _is_cf_property((e.get("properties") or {}).get(property_name))
    ]
    if not hits:
        return {
            "success": False,
            "error": f"No CF rule found for {container}.{property_name} on visual {visual_name}",
        }
    removed_selectors: List[Any] = []

    def _mutate(data: Dict[str, Any]) -> None:
        objects = data["visual"]["objects"]
        container_list = objects[container]
        for entry in container_list:
            props = entry.get("properties") or {}
            if _selector_matches(entry, selector_metadata) and _is_cf_property(
                props.get(property_name)
            ):
                del props[property_name]
                removed_selectors.append(entry.get("selector"))
                if selector_metadata is None:
                    break
        objects[container] = [e for e in container_list if e.get("properties")]
        if not objects[container]:
            objects.pop(container, None)

    safe_io.update_json(visual_folder / "visual.json", _mutate)
    return {
        "success": True,
        "removed": True,
        "container": container,
        "property": property_name,
        "removed_selectors": removed_selectors,
        "page": page_name,
        "visual": visual_name,
    }


def copy_rule(
    definition_path: Path,
    source_page: str,
    source_visual: str,
    target_page: str,
    target_visual: str,
    container: str,
    property_name: str,
    *,
    selector_metadata: Optional[str] = None,
) -> Dict[str, Any]:
    """Copy a CF rule (with its selector) from one visual to another."""
    definition_path = Path(definition_path)
    src_page_folder = _resolve_page_folder(definition_path, source_page)
    if not src_page_folder:
        return {"success": False, "error": f"Source page not found: {source_page}"}
    src_resolved = _resolve_visual(src_page_folder, source_visual)
    if not src_resolved:
        return {"success": False, "error": f"Source visual not found: {source_visual}"}
    _src_folder, src_data = src_resolved
    src_container_list = (src_data.get("visual", {}).get("objects", {}) or {}).get(container, [])
    cf_value = None
    cf_selector = None
    for entry in src_container_list:
        props = entry.get("properties", {}) or {}
        if (
            property_name in props
            and _is_cf_property(props[property_name])
            and _selector_matches(entry, selector_metadata)
        ):
            cf_value = copy.deepcopy(props[property_name])
            cf_selector = copy.deepcopy(entry.get("selector"))
            break
    if cf_value is None:
        return {
            "success": False,
            "error": (
                f"No CF rule found for {container}.{property_name} on source visual {source_visual}"
            ),
        }
    tgt_page_folder = _resolve_page_folder(definition_path, target_page)
    if not tgt_page_folder:
        return {"success": False, "error": f"Target page not found: {target_page}"}
    tgt_resolved = _resolve_visual(tgt_page_folder, target_visual)
    if not tgt_resolved:
        return {"success": False, "error": f"Target visual not found: {target_visual}"}
    tgt_folder, tgt_data = tgt_resolved
    warnings: List[str] = []
    meta = (cf_selector or {}).get("metadata")
    if meta:
        import json as _json

        if _json.dumps(meta) not in _json.dumps(
            ((tgt_data.get("visual") or {}).get("query") or {})
        ):
            warnings.append(
                f"selector.metadata {meta!r} is not bound in the target visual; the rule "
                "will not apply until that field is added"
            )

    def _mutate(data: Dict[str, Any]) -> None:
        objects = data.setdefault("visual", {}).setdefault("objects", {})
        _place(objects.setdefault(container, []), cf_selector, property_name, cf_value)

    safe_io.update_json(tgt_folder / "visual.json", _mutate)
    out: Dict[str, Any] = {
        "success": True,
        "container": container,
        "property": property_name,
        "selector": cf_selector,
        "source": f"{source_visual}@{source_page}",
        "target": f"{target_visual}@{target_page}",
    }
    if warnings:
        out["warnings"] = warnings
    return out
