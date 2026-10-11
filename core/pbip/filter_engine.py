"""
PBIP Filter Engine - Manages filters at report, page, and visual level.

Filters live in ``filterConfig.filters`` arrays in:
- ``report.json``  (report-level)
- ``page.json``    (page-level)
- ``visual.json``  TOP-LEVEL ``filterConfig.filters`` (visual-level — never
  ``visual.filters``, which Desktop does not read: H-09)

Deep review 2026-09-22 (WP9):

* **Polarity (H-10, the user's hard rule).** An inverted-selection filter
  (``objects.general[].properties.isInvertedSelectionMode`` or a
  ``Not(In ...)`` condition) is an EXCLUDE list: removing a value WIDENS it.
  Every reader reports ``polarity``; ``set_filter_values`` on an exclude
  filter requires ``polarity="exclude"`` (or ``confirm_widen=true``); an empty
  ``Values`` list is never written (``error_type: empty_in_list``) and every
  write says which polarity it applied.
* **Literals (H-11 / H-15)**: ``pbir_model.encode_literal`` — quotes doubled,
  ``L`` for Int64 columns (type read from the bound model's TMDL), ``D`` for
  doubles, no exponent notation.
* **Advanced comparisons (H-11)**: ComparisonKind ints 0=Equal,
  1=GreaterThan, 2=GreaterThanOrEqual, 3=LessThan, 4=LessThanOrEqual;
  NotEqual = ``Not(Comparison 0)``; IsBlank / IsNotBlank =
  ``(Not) Comparison 0 null``.
* **TopN (A-07 / H-11d)**: the real shape (``In`` over a ``Top`` subquery
  ``From`` entry of ``Type 2``), count from ``top_n`` and direction from
  ``top_direction``. RelativeDate has no verified sample and is refused.
* Hidden / locked are TOP-LEVEL ``isHiddenInViewMode`` /
  ``isLockedInViewMode`` filter keys (R0105), not ``objects.general``.
* Every write is a locked read-modify-write (``safe_io.update_json``).

All public functions return ``Dict[str, Any]`` with ``success: bool`` plus
data, or ``error: str`` on failure.  No MCP awareness.
"""

import copy
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.pbip import pbir_model
from core.pbip.authoring import safe_io
from core.pbip.authoring.id_generator import generate_visual_id
from core.utilities.pbip_utils import (
    SKIP_WRITE,
    load_json_file,
    iter_pages as _iter_pages,
    iter_visuals as _iter_visuals,
)

logger = logging.getLogger(__name__)

#: Accepted Advanced-filter operators -> (ComparisonKind, negate).
_ADVANCED_OPERATORS: Dict[str, Tuple[int, bool]] = {
    **{name: (kind, False) for name, kind in pbir_model.ADVANCED_FILTER_COMPARISON.items()},
    "NotEqual": (0, True),
}
_BLANK_OPERATORS = {"IsBlank": False, "IsNotBlank": True}


def _resolve_page_folder(definition_path: Path, page_name: str) -> Optional[Path]:
    """Resolve a page folder by id / exact name / unique prefix (never a substring)."""
    from core.pbip.authoring.targets import resolve_page_dir

    folder, _err = resolve_page_dir(Path(definition_path), page_name)
    return folder


def _resolve_visual_folder(page_folder: Path, visual_name: str) -> Optional[Path]:
    """Resolve a visual id or EXACT title to its folder inside a page."""
    from core.pbip.authoring.targets import resolve_visual_dir

    folder, _err = resolve_visual_dir(page_folder, visual_name)
    return folder


def _extract_literal_text(expr: Dict) -> Optional[str]:
    """Extract plain text from a Power BI Literal expression wrapper."""
    try:
        val = expr.get("expr", {}).get("Literal", {}).get("Value", "")
        if val.startswith("'") and val.endswith("'"):
            return val[1:-1].replace("''", "'")
        return val or None
    except (AttributeError, TypeError):
        return None


def _condition_kind(filt: Dict[str, Any]) -> Optional[str]:
    where = (filt.get("filter") or {}).get("Where") or []
    for w in where:
        cond = (w or {}).get("Condition") or {}
        key = next(iter(cond), None)
        if key == "Not":
            inner = (cond.get("Not") or {}).get("Expression") or {}
            return "Not(" + str(next(iter(inner), None)) + ")"
        return key
    return None


def _summarize_filter(filt: Dict[str, Any], visual_json: Optional[Dict] = None) -> Dict[str, Any]:
    """Compact summary of one PBIR filter, with its polarity (H-10)."""
    field = filt.get("field", {}) or {}
    table = ""
    column = ""
    field_type = "Unknown"
    for kind in ("Column", "Measure"):
        if kind in field:
            field_type = kind
            node = field[kind]
            table = ((node.get("Expression") or {}).get("SourceRef") or {}).get("Entity", "")
            column = node.get("Property", "")
            break

    values: List[Any] = []
    for vgroup in pbir_model.get_filter_in_values(filt) or []:
        for v in vgroup if isinstance(vgroup, list) else []:
            lit = ((v or {}).get("Literal") or {}).get("Value")
            if isinstance(lit, str):
                value, kind = pbir_model.decode_literal(lit)
                values.append(value if kind != "unknown" else lit)
    polarity = pbir_model.filter_polarity(filt, visual_json)
    has_condition = isinstance(filt.get("filter"), dict)
    summary: Dict[str, Any] = {
        "name": filt.get("name", ""),
        "type": filt.get("type", ""),
        "field_type": field_type,
        "table": table,
        "field": column,
        "howCreated": filt.get("howCreated", ""),
        "values": values,
        # H-10: with polarity "exclude" the values are what is EXCLUDED.
        "polarity": polarity,
        "is_inverted": pbir_model.is_inverted_selection(filt, visual_json),
        "condition": _condition_kind(filt),
        "has_condition": has_condition,
    }
    if filt.get("isHiddenInViewMode"):
        summary["hidden"] = True
    if filt.get("isLockedInViewMode"):
        summary["locked"] = True
    if polarity == "exclude" and not has_condition:
        summary["note"] = "inverted with no condition = excludes nothing (all selected)"
    return summary


def _build_source_alias(table: str) -> str:
    """Short, de-conflicted alias for a table name (P2-58)."""
    import hashlib

    first_alpha = next((ch.lower() for ch in table if ch.isalpha()), "t")
    compact = table.strip()
    if " " not in compact and "_" not in compact:
        return first_alpha
    digest = hashlib.md5(compact.encode("utf-8")).hexdigest()[:3]
    return f"{first_alpha}{digest}"


def _literal_kind(definition_path: Optional[Path], table: str, field: str, value: Any) -> str:
    """encode_literal kind for a filter value: bound model column type first (H-11c)."""
    if isinstance(value, bool):
        return "bool"
    if value is None:
        return "null"
    if definition_path is not None:
        from core.pbip.authoring.model_lookup import LITERAL_KIND_FOR_DATATYPE, column_type

        dt = column_type(definition_path, table, field)
        if dt and dt in LITERAL_KIND_FOR_DATATYPE:
            return LITERAL_KIND_FOR_DATATYPE[dt]
    if isinstance(value, int):
        return "int64"
    if isinstance(value, float):
        return "double"
    return "string"


def _lit(definition_path: Optional[Path], table: str, field: str, value: Any, kind: Optional[str]):
    k = kind or _literal_kind(definition_path, table, field, value)
    return {"Literal": {"Value": pbir_model.encode_literal(value, k)}}


def _build_filter_dict(
    table: str,
    field: str,
    filter_type: str,
    values: Optional[List] = None,
    operator: Optional[str] = None,
    by_table: Optional[str] = None,
    by_field: Optional[str] = None,
    *,
    top_n: Optional[int] = None,
    top_direction: Optional[str] = None,
    polarity: str = "include",
    field_type: str = "Column",
    value_type: Optional[str] = None,
    definition_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Build a complete PBIR filter dict. Raises ValueError on invalid input."""
    filter_id = generate_visual_id()
    alias = _build_source_alias(table)
    fkind = "Measure" if str(field_type).lower() == "measure" else "Column"
    field_ref: Dict[str, Any] = {
        fkind: {"Expression": {"SourceRef": {"Entity": table}}, "Property": field}
    }
    filt: Dict[str, Any] = {
        "name": filter_id,
        "field": field_ref,
        "type": filter_type,
        "howCreated": "User",
    }
    column_expr = {fkind: {"Expression": {"SourceRef": {"Source": alias}}, "Property": field}}
    base_from = [{"Name": alias, "Entity": table, "Type": 0}]

    if filter_type == "Categorical":
        if not values:
            raise ValueError(
                "empty_in_list: a Categorical filter needs at least one value "
                "(Values: [] hard-fails visuals). Omit the filter instead."
            )
        cond: Dict[str, Any] = {
            "In": {
                "Expressions": [column_expr],
                "Values": [[_lit(definition_path, table, field, v, value_type)] for v in values],
            }
        }
        if polarity == "exclude":
            cond = {"Not": {"Expression": cond}}
            filt["objects"] = {
                "general": [
                    {
                        "properties": {
                            "isInvertedSelectionMode": {"expr": {"Literal": {"Value": "true"}}}
                        }
                    }
                ]
            }
        filt["filter"] = {"Version": 2, "From": base_from, "Where": [{"Condition": cond}]}

    elif filter_type == "Advanced":
        op = operator or "GreaterThan"
        if op in _BLANK_OPERATORS:
            comp: Dict[str, Any] = {
                "Comparison": {
                    "ComparisonKind": 0,
                    "Left": column_expr,
                    "Right": {"Literal": {"Value": "null"}},
                }
            }
            cond = {"Not": {"Expression": comp}} if _BLANK_OPERATORS[op] else comp
        else:
            if op not in _ADVANCED_OPERATORS:
                raise ValueError(
                    f"unknown Advanced operator {op!r}; use one of "
                    f"{', '.join(sorted(list(_ADVANCED_OPERATORS) + list(_BLANK_OPERATORS)))}"
                )
            if not values:
                raise ValueError(f"Advanced operator {op} needs a value")
            kind, negate = _ADVANCED_OPERATORS[op]
            comp = {
                "Comparison": {
                    "ComparisonKind": kind,
                    "Left": column_expr,
                    "Right": _lit(definition_path, table, field, values[0], value_type),
                }
            }
            cond = {"Not": {"Expression": comp}} if negate else comp
        filt["filter"] = {"Version": 2, "From": base_from, "Where": [{"Condition": cond}]}

    elif filter_type in ("TopN", "VisualTopN"):
        filt["type"] = "TopN"
        n = top_n if top_n is not None else (int(values[0]) if values else None)
        if n is None:
            raise ValueError("TopN needs top_n (or values[0])")
        n = int(n)
        if n <= 0:
            raise ValueError("top_n must be a positive integer")
        direction = top_direction or (operator if operator in ("Top", "Bottom") else "Top")
        if direction not in ("Top", "Bottom"):
            raise ValueError("top_direction must be 'Top' or 'Bottom'")
        if not by_field:
            raise ValueError("TopN needs by_table / by_field (the ranking measure)")
        by_tbl = by_table or table
        by_alias = _build_source_alias(by_tbl)
        if by_alias == alias and by_tbl != table:
            by_alias = f"{by_alias}2"
        sub_from = [{"Name": alias, "Entity": table, "Type": 0}]
        if by_tbl != table:
            sub_from.append({"Name": by_alias, "Entity": by_tbl, "Type": 0})
        rank_alias = alias if by_tbl == table else by_alias
        subquery = {
            "Version": 2,
            "From": sub_from,
            "Select": [{**copy.deepcopy(column_expr), "Name": "field"}],
            "OrderBy": [
                {
                    # QuerySortDirection: 2 = Descending (top N), 1 = Ascending (bottom N).
                    "Direction": 2 if direction == "Top" else 1,
                    "Expression": {
                        "Measure": {
                            "Expression": {"SourceRef": {"Source": rank_alias}},
                            "Property": by_field,
                        }
                    },
                }
            ],
            "Top": n,
        }
        filt["filter"] = {
            "Version": 2,
            "From": [
                {"Name": "subquery", "Expression": {"Subquery": {"Query": subquery}}, "Type": 2},
                {"Name": alias, "Entity": table, "Type": 0},
            ],
            "Where": [
                {
                    "Condition": {
                        "In": {
                            "Expressions": [copy.deepcopy(column_expr)],
                            "Table": {"SourceRef": {"Source": "subquery"}},
                        }
                    }
                }
            ],
        }

    elif filter_type == "RelativeDate":
        raise ValueError(
            "unsupported_filter_type: RelativeDate filters have no verified PBIR sample in "
            "this toolchain (the old writer emitted an invented shape). Create it in "
            "Desktop, then manage it with set_filter / hide_filter / lock_filter."
        )
    else:
        raise ValueError(f"unknown filter_type {filter_type!r}; use Categorical, Advanced or TopN")
    return filt


# ---------------------------------------------------------------------------
# Containers
# ---------------------------------------------------------------------------


def _get_filters_from_container(data: Dict[str, Any], level: str) -> List[Dict[str, Any]]:
    """Filters of a report/page/visual container (visual = top-level filterConfig — H-09)."""
    if level == "visual":
        return pbir_model.get_visual_filters(data)
    fc = data.get("filterConfig")
    return list((fc or {}).get("filters") or []) if isinstance(fc, dict) else []


def _set_filters_on_container(
    data: Dict[str, Any], level: str, filters: List[Dict[str, Any]]
) -> None:
    """Write *filters* back (visual: top-level filterConfig, legacy key removed)."""
    if level == "visual":
        pbir_model.set_visual_filters(data, filters)
        return
    fc = data.get("filterConfig")
    if not isinstance(fc, dict):
        fc = {}
    if filters:
        fc["filters"] = filters
    else:
        fc.pop("filters", None)
    if fc:
        data["filterConfig"] = fc
    else:
        data.pop("filterConfig", None)


def _container_targets(
    definition_path: Path, level: str, page_name: Optional[str], visual_name: Optional[str]
) -> Tuple[List[Tuple[Path, str, str]], Optional[Dict[str, Any]]]:
    """``[(json_path, level, context)]`` for the requested scope, or an error."""
    from core.pbip.authoring.targets import resolve_page_dir, resolve_visual_dir

    if level == "report":
        return [(definition_path / "report.json", "report", "report")], None
    if not page_name:
        return [], {"success": False, "error": f"page_name is required for {level}-level filters"}
    page_folder, err = resolve_page_dir(definition_path, page_name)
    if err:
        err["error"] = f"Page not found: {page_name} ({err.get('error')})"
        return [], err
    if level == "page":
        return [(page_folder / "page.json", "page", f"page:{page_folder.name}")], None
    if level == "visual":
        if not visual_name:
            return [], {
                "success": False,
                "error": "visual_name is required for visual-level filters",
            }
        vdir, verr = resolve_visual_dir(page_folder, visual_name)
        if verr:
            verr["error"] = f"Visual not found: {visual_name} ({verr.get('error')})"
            return [], verr
        return [(vdir / "visual.json", "visual", f"visual:{vdir.name}@{page_folder.name}")], None
    return [], {
        "success": False,
        "error": f"Invalid level: {level}. Must be 'report', 'page', or 'visual'.",
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_filters(
    definition_path: Path,
    level: str = "all",
    page_name: Optional[str] = None,
    visual_name: Optional[str] = None,
) -> Dict[str, Any]:
    """List filters at the requested level(s). level: 'report', 'page', 'visual', or 'all'."""
    definition_path = Path(definition_path)
    if not definition_path.is_dir():
        return {"success": False, "error": f"Definition path not found: {definition_path}"}

    result: Dict[str, Any] = {
        "success": True,
        "report_filters": [],
        "page_filters": [],
        "visual_filters": [],
    }
    page_filter_id: Optional[str] = None
    if page_name:
        pf = _resolve_page_folder(definition_path, page_name)
        page_filter_id = pf.name if pf else None
        if pf is None:
            return {"success": False, "error": f"Page not found: {page_name}"}

    if level in ("report", "all"):
        report_data = load_json_file(definition_path / "report.json")
        if report_data:
            for f in _get_filters_from_container(report_data, "report"):
                result["report_filters"].append(_summarize_filter(f))

    for page_folder, page_data in _iter_pages(definition_path):
        if page_filter_id and page_folder.name != page_filter_id:
            continue
        display = page_data.get("displayName", page_folder.name)
        if level in ("page", "all"):
            for f in _get_filters_from_container(page_data, "page"):
                summary = _summarize_filter(f)
                summary["page"] = display
                summary["page_id"] = page_folder.name
                result["page_filters"].append(summary)
        if level in ("visual", "all"):
            target_vid = None
            if visual_name:
                vdir = _resolve_visual_folder(page_folder, visual_name)
                if vdir is None:
                    continue
                target_vid = vdir.name
            for visual_folder, visual_data in _iter_visuals(page_folder):
                if target_vid and visual_folder.name != target_vid:
                    continue
                vis_name = visual_data.get("name", visual_folder.name)
                for f in _get_filters_from_container(visual_data, "visual"):
                    summary = _summarize_filter(f, visual_data)
                    summary["page"] = display
                    summary["page_id"] = page_folder.name
                    summary["visual"] = vis_name
                    result["visual_filters"].append(summary)

    result["total_filters"] = (
        len(result["report_filters"]) + len(result["page_filters"]) + len(result["visual_filters"])
    )
    return result


def add_filter(
    definition_path: Path,
    level: str,
    table: str,
    field: str,
    filter_type: str = "Categorical",
    values: Optional[List] = None,
    page_name: Optional[str] = None,
    visual_name: Optional[str] = None,
    operator: Optional[str] = None,
    by_table: Optional[str] = None,
    by_field: Optional[str] = None,
    *,
    top_n: Optional[int] = None,
    top_direction: Optional[str] = None,
    polarity: str = "include",
    field_type: str = "Column",
    value_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Add a new filter at the specified level ('report', 'page', or 'visual').

    filter_type: 'Categorical' (``polarity`` include/exclude), 'Advanced'
    (``operator`` Equal/NotEqual/GreaterThan/.../IsBlank/IsNotBlank) or
    'TopN' (``top_n`` + ``top_direction`` + ``by_table``/``by_field``).
    """
    definition_path = Path(definition_path)
    if polarity not in ("include", "exclude"):
        return {
            "success": False,
            "error_type": "invalid_args",
            "error": "polarity must be include|exclude",
        }
    kind = None
    if value_type:
        kind = {"int": "int64", "integer": "int64"}.get(value_type, value_type)
    try:
        new_filter = _build_filter_dict(
            table=table,
            field=field,
            filter_type=filter_type,
            values=values,
            operator=operator,
            by_table=by_table,
            by_field=by_field,
            top_n=top_n,
            top_direction=top_direction,
            polarity=polarity,
            field_type=field_type,
            value_type=kind,
            definition_path=definition_path,
        )
    except (ValueError, TypeError) as e:
        msg = str(e)
        etype = (
            msg.split(":", 1)[0]
            if ":" in msg and " " not in msg.split(":", 1)[0]
            else "invalid_filter"
        )
        return {"success": False, "error_type": etype, "error": msg}

    targets, err = _container_targets(definition_path, level, page_name, visual_name)
    if err:
        return err
    json_path, lvl, context = targets[0]
    if not json_path.exists():
        return {"success": False, "error": f"Cannot read {json_path.name} at {json_path}"}

    def _mutate(data: Dict[str, Any]) -> None:
        filters = _get_filters_from_container(data, lvl)
        filters.append(copy.deepcopy(new_filter))
        _set_filters_on_container(data, lvl, filters)

    safe_io.update_json(json_path, _mutate)
    out: Dict[str, Any] = {
        "success": True,
        "filter_id": new_filter["name"],
        "level": lvl,
        "at": context,
        "filter_type": new_filter["type"],
        "polarity_applied": pbir_model.filter_polarity(new_filter),
    }
    if page_name:
        out["page"] = page_name
    if visual_name:
        out["visual"] = visual_name
    return out


def _iter_filter_containers(definition_path: Path, level: Optional[str], page_name: Optional[str]):
    """Yield ``(json_path, level, context, data)`` over the searched containers."""
    if level in (None, "report"):
        report_path = definition_path / "report.json"
        data = load_json_file(report_path)
        if data:
            yield report_path, "report", "report", data
    if level in (None, "page", "visual"):
        page_id = None
        if page_name:
            pf = _resolve_page_folder(definition_path, page_name)
            if pf is None:
                return
            page_id = pf.name
        for page_folder, page_data in _iter_pages(definition_path):
            if page_id and page_folder.name != page_id:
                continue
            if level in (None, "page"):
                yield page_folder / "page.json", "page", f"page:{page_folder.name}", page_data
            if level in (None, "visual"):
                for visual_folder, visual_data in _iter_visuals(page_folder):
                    yield (
                        visual_folder / "visual.json",
                        "visual",
                        f"visual:{visual_folder.name}@{page_folder.name}",
                        visual_data,
                    )


def _find_named_filter(
    definition_path: Path, filter_name: str, level: Optional[str], page_name: Optional[str]
) -> List[Tuple[Path, str, str, Dict[str, Any], Dict[str, Any]]]:
    name_lower = filter_name.lower()
    hits = []
    for json_path, lvl, context, data in _iter_filter_containers(definition_path, level, page_name):
        for f in _get_filters_from_container(data, lvl):
            if str(f.get("name", "")).lower() == name_lower:
                hits.append((json_path, lvl, context, data, f))
    return hits


def set_filter_values(
    definition_path: Path,
    filter_name: str,
    values: List,
    level: Optional[str] = None,
    page_name: Optional[str] = None,
    *,
    polarity: Optional[str] = None,
    confirm_widen: bool = False,
    value_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Replace the ``In`` values of a filter, honouring its polarity (H-10).

    * Empty ``values`` -> ``empty_in_list`` error (never ``Values: []``).
    * Exclude-mode filter (inverted / ``Not(In)``): the values are EXCLUDED;
      refused unless ``polarity="exclude"`` (keep the mode) or
      ``confirm_widen=true``. ``polarity="include"`` switches it to a
      positive list (drops the inverted flag).
    * A field-only filter (no condition) gets a new condition.
    """
    definition_path = Path(definition_path)
    if not values:
        return {
            "success": False,
            "error_type": "empty_in_list",
            "error": (
                "values must be a non-empty list — an empty In list hard-fails visuals "
                "(InvalidQueryInExpression). To remove the filter's condition use "
                "clear_filters (filter_names=[...]) instead."
            ),
        }
    if polarity not in (None, "include", "exclude"):
        return {
            "success": False,
            "error_type": "invalid_args",
            "error": "polarity must be include|exclude",
        }
    hits = _find_named_filter(definition_path, filter_name, level, page_name)
    if not hits:
        return {"success": False, "error": f"Filter not found: {filter_name}"}
    if len(hits) > 1:
        return {
            "success": False,
            "error_type": "ambiguous_filter",
            "error": f"{len(hits)} filters are named {filter_name!r}; pass level / page_name",
            "candidates": [h[2] for h in hits],
        }
    json_path, lvl, context, data, filt = hits[0]
    if lvl == "visual" and pbir_model.get_slicer_selection_filter(data) is not None:
        # A slicer's selection lives in TWO places (visual.objects.general
        # filter + this filterConfig entry); editing only one desynchronises
        # them. The slicer op keeps both consistent.
        return {
            "success": False,
            "error_type": "use_set_default_selection",
            "error": (
                f"Filter {filter_name!r} belongs to a slicer with a persisted selection; use "
                "07_Visual_Operations operation=set_default_selection (it updates the "
                "selection and its filterConfig entry together, polarity-aware)."
            ),
        }
    current_polarity = pbir_model.filter_polarity(filt, data if lvl == "visual" else None)
    if current_polarity == "exclude" and polarity is None and not confirm_widen:
        current = _summarize_filter(filt, data if lvl == "visual" else None)["values"]
        return {
            "success": False,
            "error_type": "polarity_confirmation_required",
            "error": (
                f"Filter {filter_name!r} is an EXCLUDE list (inverted selection / Not In): its "
                "values are what is excluded, so changing them changes what is shown in the "
                "opposite direction (removing a value WIDENS the filter). Pass "
                "polarity='exclude' to set the excluded values, polarity='include' to switch "
                "to a positive list, or confirm_widen=true."
            ),
            "polarity": current_polarity,
            "current_values": current,
        }
    target_polarity = polarity or current_polarity
    ftype = filt.get("type")
    if ftype not in (None, "Categorical"):
        return {
            "success": False,
            "error_type": "unsupported_filter_type",
            "error": f"set_filter only edits Categorical (In) filters; {filter_name!r} is {ftype}",
        }
    field = filt.get("field") or {}
    fkind = "Measure" if "Measure" in field else "Column"
    node = field.get(fkind) or {}
    table = ((node.get("Expression") or {}).get("SourceRef") or {}).get("Entity", "")
    prop = node.get("Property", "")
    kind = {"int": "int64", "integer": "int64"}.get(value_type, value_type) if value_type else None
    try:
        new_rows = [[_lit(definition_path, table, prop, v, kind)] for v in values]
    except (ValueError, TypeError) as e:
        return {"success": False, "error_type": "invalid_value", "error": str(e)}
    fname = filt.get("name")

    def _mutate(d: Dict[str, Any]) -> None:
        filters = _get_filters_from_container(d, lvl)
        for f in filters:
            if f.get("name") != fname:
                continue
            q = f.get("filter")
            if not isinstance(q, dict) or not pbir_model.get_filter_in_values(f):
                alias = _build_source_alias(table)
                q = {
                    "Version": 2,
                    "From": [{"Name": alias, "Entity": table, "Type": 0}],
                    "Where": [
                        {
                            "Condition": {
                                "In": {
                                    "Expressions": [
                                        {
                                            fkind: {
                                                "Expression": {"SourceRef": {"Source": alias}},
                                                "Property": prop,
                                            }
                                        }
                                    ],
                                    "Values": [],
                                }
                            }
                        }
                    ],
                }
                f["filter"] = q
            for w in q.get("Where") or []:
                cond = w.get("Condition") or {}
                inner = cond.get("Not", {}).get("Expression") if "Not" in cond else cond
                if not (isinstance(inner, dict) and "In" in inner):
                    continue
                inner["In"]["Values"] = copy.deepcopy(new_rows)
                if target_polarity == "exclude":
                    w["Condition"] = {"Not": {"Expression": inner}}
                else:
                    w["Condition"] = inner
                break
            gen = f.setdefault("objects", {}).setdefault("general", [])
            if target_polarity == "exclude":
                if not gen:
                    gen.append({"properties": {}})
                gen[0].setdefault("properties", {})["isInvertedSelectionMode"] = {
                    "expr": {"Literal": {"Value": "true"}}
                }
            else:
                for e in gen:
                    (e.get("properties") or {}).pop("isInvertedSelectionMode", None)
                gen[:] = [e for e in gen if e.get("properties")]
                if not gen:
                    f["objects"].pop("general", None)
            if not f.get("objects"):
                f.pop("objects", None)
            f.setdefault("type", "Categorical")
        if lvl == "visual" and target_polarity == "include":
            # A visual-level inverted flag (visual.objects.data[]) would keep
            # the new positive list in exclude mode.
            vobjs = (d.get("visual") or {}).get("objects") or {}
            for section in ("data", "general", "selection"):
                for e in vobjs.get(section) or []:
                    if isinstance(e, dict):
                        (e.get("properties") or {}).pop("isInvertedSelectionMode", None)
        _set_filters_on_container(d, lvl, filters)

    safe_io.update_json(json_path, _mutate)
    return {
        "success": True,
        "updated_at": context,
        "filter_name": filter_name,
        "values": values,
        "polarity_before": current_polarity,
        "polarity_applied": target_polarity,
    }


def clear_filters(
    definition_path: Path,
    level: str,
    page_name: Optional[str] = None,
    visual_name: Optional[str] = None,
    *,
    filter_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Remove filters at the specified level ('report', 'page', or 'visual').

    ``filter_names`` limits the removal to those filters. The removed filter
    definitions are returned (``removed``) so the change can be undone, with
    each one's polarity — an exclude-mode slicer filter removed here stops
    excluding (H-10).
    """
    definition_path = Path(definition_path)
    targets, err = _container_targets(definition_path, level, page_name, visual_name)
    if err:
        return err
    json_path, lvl, context = targets[0]
    if not json_path.exists():
        return {"success": False, "error": f"Cannot read {json_path.name}"}
    wanted = {n.lower() for n in filter_names} if filter_names else None
    removed: List[Dict[str, Any]] = []

    def _mutate(data: Dict[str, Any]) -> Any:
        filters = _get_filters_from_container(data, lvl)
        kept = []
        for f in filters:
            if wanted is None or str(f.get("name", "")).lower() in wanted:
                removed.append(copy.deepcopy(f))
            else:
                kept.append(f)
        legacy = lvl == "visual" and isinstance((data.get("visual") or {}).get("filters"), list)
        if not removed and not legacy:
            return SKIP_WRITE
        _set_filters_on_container(data, lvl, kept)
        return None

    safe_io.update_json(json_path, _mutate)
    out: Dict[str, Any] = {
        "success": True,
        "cleared_count": len(removed),
        "level": lvl,
        "at": context,
        "removed": removed,
        "removed_polarity": {f.get("name"): pbir_model.filter_polarity(f) for f in removed},
    }
    if page_name:
        out["page"] = page_name
    if visual_name:
        out["visual"] = visual_name
    return out


def set_filter_visibility(
    definition_path: Path,
    filter_name: str,
    hidden: bool,
    level: Optional[str] = None,
    page_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Hide or unhide a filter in the filter pane (top-level ``isHiddenInViewMode``)."""
    return _set_filter_property(
        definition_path, filter_name, "isHiddenInViewMode", hidden, level, page_name
    )


def set_filter_lock(
    definition_path: Path,
    filter_name: str,
    locked: bool,
    level: Optional[str] = None,
    page_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Lock or unlock a filter in view mode (top-level ``isLockedInViewMode``)."""
    return _set_filter_property(
        definition_path, filter_name, "isLockedInViewMode", locked, level, page_name
    )


def _set_filter_property(
    definition_path: Path,
    filter_name: str,
    prop_name: str,
    prop_value: bool,
    level: Optional[str],
    page_name: Optional[str],
) -> Dict[str, Any]:
    """Set a boolean TOP-LEVEL filter key (R0105: ``isHiddenInViewMode: true``)."""
    definition_path = Path(definition_path)
    hits = _find_named_filter(definition_path, filter_name, level, page_name)
    if not hits:
        return {"success": False, "error": f"Filter not found: {filter_name}"}
    if len(hits) > 1:
        return {
            "success": False,
            "error_type": "ambiguous_filter",
            "error": f"{len(hits)} filters are named {filter_name!r}; pass level / page_name",
            "candidates": [h[2] for h in hits],
        }
    json_path, lvl, context, _data, filt = hits[0]
    fname = filt.get("name")

    def _mutate(d: Dict[str, Any]) -> None:
        filters = _get_filters_from_container(d, lvl)
        for f in filters:
            if f.get("name") != fname:
                continue
            if prop_value:
                f[prop_name] = True
            else:
                f.pop(prop_name, None)
            # Legacy location written by older versions of this tool.
            for e in (f.get("objects") or {}).get("general") or []:
                (e.get("properties") or {}).pop(prop_name, None)
        _set_filters_on_container(d, lvl, filters)

    safe_io.update_json(json_path, _mutate)
    return {
        "success": True,
        "filter_name": filter_name,
        "property": prop_name,
        "value": prop_value,
        "at": context,
    }
