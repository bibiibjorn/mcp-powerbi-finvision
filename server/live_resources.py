"""Live read-only resources + argument completion (F-29).

Resource templates backed by the connected model / the PBIP report / the capture store::

    powerbi://model/measure/{table}/{name}    DAX + format + dependencies of one measure
    powerbi://model/schema                    tables, columns, measure names, relationships
    powerbi://report/pages                    pages of the PBIP report (ids, display names)
    powerbi://report/page/{page}/visuals      visuals of one page (id, type, title, fields)
    powerbi://capture/{capture_id}            summary of a 13_Query_Plan capture

plus ``completion/complete`` values for the ``table`` / ``name`` / ``page`` / ``capture_id`` /
``tool`` template arguments and the ``table_name`` / ``measure_name`` / ``page_name`` /
``visual_name`` prompt arguments.

Every function here is READ-ONLY. Nothing joins a caller-supplied value into a filesystem
path: pages and visuals are matched against the directory listing, measures against DMV rows.
When Desktop is not connected / no PBIP is known, a read raises ``LookupError`` with the step
that fixes it, and completion simply returns no values.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_MAX_ITEMS = 500
_MAX_DEPENDENCIES = 50
_MAX_COMPLETIONS = 100
_MAX_FIELD_REFS = 12
_MAX_VALUE_JSON_CHARS = 2000


def _dumps(payload: Any) -> str:
    from core.utilities.json_utils import MCPJsonEncoder

    return json.dumps(payload, separators=(",", ":"), cls=MCPJsonEncoder, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Live model
# ---------------------------------------------------------------------------


def _require_model() -> None:
    from core.operations import model_readback

    if model_readback._executor() is None:  # noqa: SLF001 - the single connected-check
        raise LookupError(
            "Not connected to a Power BI model: run 01_Connection operation='ensure' first."
        )


def _relationship_rows() -> Optional[List[Dict[str, Any]]]:
    from core.operations import model_readback

    return model_readback.dmv_rows("TMSCHEMA_RELATIONSHIPS", "INFO.RELATIONSHIPS()")


def _column_rows() -> Optional[List[Dict[str, Any]]]:
    from core.operations import model_readback

    return model_readback.dmv_rows("TMSCHEMA_COLUMNS", "INFO.COLUMNS()")


def _column_name(row: Dict[str, Any]) -> str:
    return str(row.get("ExplicitName") or row.get("InferredName") or row.get("Name") or "")


def list_table_names() -> List[str]:
    from core.operations import model_readback

    tables = model_readback.table_names_by_id()
    return sorted(t for t in (tables or {}).values() if t)


def list_measure_names(table: Optional[str] = None) -> List[str]:
    from core.operations import model_readback

    rows = model_readback.measure_rows() or []
    names = [r["name"] for r in rows if r.get("name") and (not table or r.get("table") == table)]
    return sorted(set(names))


def measure_resource(table: str, name: str) -> str:
    """JSON for ``powerbi://model/measure/{table}/{name}``."""
    from core.operations import model_readback

    _require_model()
    row = model_readback.find_measure(table, name)
    if row is None:
        raise LookupError("The model could not be read (DMV/INFO query failed).")
    if not row:
        near = [m for m in list_measure_names(table) if name.lower() in m.lower()][:10]
        raise LookupError(
            f"No measure {name!r} in table {table!r}."
            + (f" Similar: {near}" if near else " List them with 02_Model_Operations.")
        )
    payload: Dict[str, Any] = {
        "table": row.get("table") or table,
        "name": row.get("name") or name,
        "expression": row.get("expression") or "",
        "format_string": row.get("format_string") or "",
        "display_folder": row.get("display_folder") or "",
        "state": row.get("state"),
        "error": row.get("error") or None,
        "dependencies": _measure_dependencies(row.get("table") or table, row.get("name") or name),
    }
    return _dumps(payload)


def _is_table_type(object_type: Any) -> bool:
    return str(object_type or "").upper() in ("TABLE", "CALC_TABLE")


def _dependency_label(object_type: Any, table: Any, obj: Any) -> str:
    """One dependency as DAX: table ``'T'``, column ``'T'[C]``, measure ``[M]``, function ``F()``.

    ``object_type`` is the DISCOVER_CALC_DEPENDENCY ``(REFERENCED_)OBJECT_TYPE``. A table-level
    reference arrives as table = object = the table's name (it used to print ``T[T]``) and a UDF
    has no table (it used to print ``[F]``). Without a type the shape decides: a name equal to its
    table is the table, no table is a measure, else a qualified ``'T'[O]`` (valid for measures too).
    """
    from core.dax.dax_names import bracket, column_ref, quote_table

    kind = str(object_type or "").upper()
    name = str(obj or "")
    if "FUNCTION" in kind:
        return f"{name}()"
    if _is_table_type(kind):
        return quote_table(name or str(table or ""))
    if "MEASURE" in kind:
        return bracket(name)
    if "COLUMN" in kind or "HIERARCHY" in kind:
        return column_ref(str(table), name) if table else bracket(name)
    if table and name == str(table):
        return quote_table(name)
    return column_ref(str(table), name) if table else bracket(name)


def _measure_dependencies(table: str, name: str) -> Dict[str, Any]:
    """``{references: [...], referenced_by: [...]}`` from the calc-dependency DMV (capped)."""
    from core.operations import model_readback

    rows = model_readback.dmv_rows("DISCOVER_CALC_DEPENDENCY", "INFO.CALCDEPENDENCY()")
    if rows is None:
        return {"available": False}

    references: List[str] = []
    referenced_by: List[str] = []
    for r in rows:
        obj, tbl = r.get("OBJECT"), r.get("TABLE")
        ref_obj, ref_tbl = r.get("REFERENCED_OBJECT"), r.get("REFERENCED_TABLE")
        if obj == name and (not tbl or tbl == table):
            label = _dependency_label(r.get("REFERENCED_OBJECT_TYPE"), ref_tbl, ref_obj)
            if label not in references:
                references.append(label)
        if (
            ref_obj == name
            and (not ref_tbl or ref_tbl == table)
            and not _is_table_type(r.get("REFERENCED_OBJECT_TYPE"))
        ):
            label = _dependency_label(r.get("OBJECT_TYPE"), tbl, obj)
            if label not in referenced_by:
                referenced_by.append(label)
    return {
        "available": True,
        "references": references[:_MAX_DEPENDENCIES],
        "referenced_by": referenced_by[:_MAX_DEPENDENCIES],
        "truncated": len(references) > _MAX_DEPENDENCIES or len(referenced_by) > _MAX_DEPENDENCIES,
    }


def schema_resource() -> str:
    """JSON for ``powerbi://model/schema`` (a fixed URI, so it answers even when offline)."""
    try:
        return _schema_json()
    except LookupError as e:
        return _dumps({"available": False, "reason": str(e)})


def pages_resource() -> str:
    """JSON for ``powerbi://report/pages`` (a fixed URI, so it answers even without a PBIP)."""
    try:
        return _pages_json()
    except LookupError as e:
        return _dumps({"available": False, "reason": str(e)})


def _schema_json() -> str:
    """tables -> columns / measures + relationships of the connected model."""
    from core.operations import model_readback

    _require_model()
    tables = model_readback.table_names_by_id()
    if tables is None:
        raise LookupError("The model could not be read (DMV/INFO query failed).")
    columns = _column_rows() or []
    measures = model_readback.measure_rows() or []
    by_table: Dict[str, Dict[str, Any]] = {
        name: {"name": name, "columns": [], "measures": []} for name in tables.values()
    }
    col_label: Dict[Any, str] = {}
    for c in columns:
        tname = tables.get(c.get("TableID"))
        cname = _column_name(c)
        if not cname or str(c.get("Type")) in ("3", "RowNumber"):
            continue
        col_label[c.get("ID")] = f"{tname}[{cname}]"
        if tname in by_table:
            by_table[tname]["columns"].append(cname)
    for m in measures:
        if m.get("table") in by_table and m.get("name"):
            by_table[m["table"]]["measures"].append(m["name"])
    relationships = []
    for r in _relationship_rows() or []:
        relationships.append(
            {
                "from": col_label.get(r.get("FromColumnID"), str(r.get("FromColumnID"))),
                "to": col_label.get(r.get("ToColumnID"), str(r.get("ToColumnID"))),
                "active": bool(r.get("IsActive", True)),
            }
        )
    out_tables = []
    for t in sorted(by_table.values(), key=lambda x: x["name"]):
        out_tables.append(
            {
                "name": t["name"],
                "columns": t["columns"][:_MAX_ITEMS],
                "measures": sorted(t["measures"])[:_MAX_ITEMS],
                "column_count": len(t["columns"]),
                "measure_count": len(t["measures"]),
            }
        )
    return _dumps(
        {
            "tables": out_tables,
            "relationships": relationships[:_MAX_ITEMS],
            "table_count": len(out_tables),
            "relationship_count": len(relationships),
        }
    )


# ---------------------------------------------------------------------------
# PBIP report
# ---------------------------------------------------------------------------


def _definition_dir() -> Path:
    from core.infrastructure.connection_state import connection_state
    from core.utilities.pbip_utils import find_definition_folder

    folder = connection_state.get_pbip_folder_path()
    if not folder:
        raise LookupError(
            "No PBIP project is known: open the .pbip in Desktop, or call "
            "09_Debug_Operations operation='set_path' pbip_path=<project>."
        )
    definition = find_definition_folder(folder)
    if not definition:
        raise LookupError(f"No report definition folder found under {folder!r}.")
    return Path(definition)


def _page_entries(definition: Path) -> List[Tuple[Path, Dict[str, Any]]]:
    from core.utilities.pbip_utils import load_json_file

    pages_dir = definition / "pages"
    entries: List[Tuple[Path, Dict[str, Any]]] = []
    if not pages_dir.is_dir():
        return entries
    for folder in sorted(pages_dir.iterdir()):
        page_json = folder / "page.json"
        if folder.is_dir() and page_json.is_file():
            data = load_json_file(page_json)
            if isinstance(data, dict):
                entries.append((folder, data))
    return entries


def list_page_names() -> List[str]:
    """Display names AND ids of the pages (completion candidates)."""
    names: List[str] = []
    for folder, data in _page_entries(_definition_dir()):
        for value in (data.get("displayName"), folder.name):
            if value and value not in names:
                names.append(str(value))
    return names


def _pages_json() -> str:
    from core.pbip import pbir_model
    from core.utilities.pbip_utils import load_json_file

    definition = _definition_dir()
    meta = load_json_file(pbir_model.pages_json_path(definition)) or {}
    order = [p for p in (meta.get("pageOrder") or []) if isinstance(p, str)]
    pages = []
    for folder, data in _page_entries(definition):
        vroot = folder / "visuals"
        count = (
            sum(1 for v in vroot.iterdir() if v.is_dir() and (v / "visual.json").is_file())
            if vroot.is_dir()
            else 0
        )
        pages.append(
            {
                "page_id": folder.name,
                "display_name": data.get("displayName", folder.name),
                "order": order.index(folder.name) if folder.name in order else None,
                "width": data.get("width", 1280),
                "height": data.get("height", 720),
                "visual_count": count,
                "type": data.get("type"),
                "hidden": data.get("visibility") == "HiddenInViewMode",
            }
        )
    pages.sort(key=lambda p: (p["order"] is None, p["order"] or 0, p["display_name"]))
    return _dumps(
        {
            "pages": pages[:_MAX_ITEMS],
            "count": len(pages),
            "active_page": meta.get("activePageName"),
        }
    )


def _visual_title(visual: Dict[str, Any]) -> Optional[str]:
    try:
        entries = (visual.get("visualContainerObjects") or {}).get("title") or []
        literal = entries[0]["properties"]["text"]["expr"]["Literal"]["Value"]
        return str(literal).strip("'")
    except (KeyError, IndexError, TypeError):
        return None


def _visual_fields(visual: Dict[str, Any]) -> List[str]:
    refs: List[str] = []
    state = ((visual.get("query") or {}).get("queryState")) or {}
    for role, spec in state.items():
        for projection in (spec or {}).get("projections") or []:
            ref = projection.get("queryRef") or projection.get("nativeQueryRef")
            if ref:
                refs.append(f"{role}: {ref}")
    return refs[:_MAX_FIELD_REFS]


def page_visuals_resource(page: str) -> str:
    """JSON for ``powerbi://report/page/{page}/visuals`` (``page`` = id or display name)."""
    from core.utilities.pbip_utils import load_json_file

    definition = _definition_dir()
    wanted = page.strip().lower()
    match: Optional[Tuple[Path, Dict[str, Any]]] = None
    for folder, data in _page_entries(definition):
        if folder.name.lower() == wanted or str(data.get("displayName", "")).lower() == wanted:
            match = (folder, data)
            break
    if match is None:
        raise LookupError(
            f"No page {page!r}. Pages: {list_page_names()[:20]} (see powerbi://report/pages)."
        )
    folder, data = match
    visuals = []
    vroot = folder / "visuals"
    for vdir in sorted(vroot.iterdir()) if vroot.is_dir() else []:
        vjson = vdir / "visual.json"
        if not (vdir.is_dir() and vjson.is_file()):
            continue
        visual = load_json_file(vjson)
        if not isinstance(visual, dict):
            continue
        inner = visual.get("visual") or {}
        pos = visual.get("position") or {}
        visuals.append(
            {
                "visual_id": vdir.name,
                "type": inner.get("visualType") or ("group" if visual.get("visualGroup") else None),
                "title": _visual_title(inner),
                "x": pos.get("x"),
                "y": pos.get("y"),
                "width": pos.get("width"),
                "height": pos.get("height"),
                "hidden": bool(visual.get("isHidden")),
                "parent_group": visual.get("parentGroupName"),
                "fields": _visual_fields(inner),
            }
        )
    return _dumps(
        {
            "page_id": folder.name,
            "display_name": data.get("displayName", folder.name),
            "visuals": visuals[:_MAX_ITEMS],
            "count": len(visuals),
        }
    )


def list_visual_names(page: Optional[str] = None) -> List[str]:
    """Visual ids and titles (of ``page`` when given, else every page) for completion."""
    from core.utilities.pbip_utils import load_json_file

    definition = _definition_dir()
    wanted = page.strip().lower() if page else None
    out: List[str] = []
    for folder, data in _page_entries(definition):
        if wanted and wanted not in (folder.name.lower(), str(data.get("displayName", "")).lower()):
            continue
        vroot = folder / "visuals"
        for vdir in sorted(vroot.iterdir()) if vroot.is_dir() else []:
            vjson = vdir / "visual.json"
            if not (vdir.is_dir() and vjson.is_file()):
                continue
            visual = load_json_file(vjson)
            title = (
                _visual_title((visual or {}).get("visual") or {})
                if isinstance(visual, dict)
                else None
            )
            for value in (title, vdir.name):
                if value and value not in out:
                    out.append(value)
    return out


# ---------------------------------------------------------------------------
# Captures
# ---------------------------------------------------------------------------


def capture_ids() -> List[str]:
    from core.query_plan import capturer

    with capturer._CAPTURE_LOCK:  # noqa: SLF001
        return list(capturer._CAPTURE_STORE)[::-1]  # newest first


def capture_resource(capture_id: str) -> str:
    """JSON for ``powerbi://capture/{capture_id}``: the capture minus raw plan text."""
    from core.query_plan import capturer

    capture = capturer.recall(capture_id)
    if capture is None:
        raise LookupError(
            f"No capture {capture_id!r} in memory (the store keeps the last 32). "
            f"Known: {capture_ids()[:10]}"
        )
    summary: Dict[str, Any] = {}
    omitted: Dict[str, str] = {}
    for key, value in capture.items():
        if key in capturer._SPILLABLE_KEYS or key in capturer._DROP_ON_SPILL:  # noqa: SLF001
            omitted[key] = "raw plan text omitted (see _spilled paths / 13_Query_Plan plan_rows)"
            continue
        try:
            size = len(_dumps(value))
        except (TypeError, ValueError):
            omitted[key] = "not serialisable"
            continue
        if size > _MAX_VALUE_JSON_CHARS:
            omitted[key] = f"{size} chars omitted (read via 13_Query_Plan)"
        else:
            summary[key] = value
    if omitted:
        summary["_omitted"] = omitted
    return _dumps(summary)


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------

#: Template argument / prompt argument name -> candidate-list kind.
_ARG_KINDS = {
    "table": "table",
    "table_name": "table",
    "name": "measure",
    "measure": "measure",
    "measure_name": "measure",
    "page": "page",
    "page_name": "page",
    "visual": "visual",
    "visual_name": "visual",
    "capture_id": "capture",
    "tool": "tool",
}


def _tool_names() -> List[str]:
    from server.registry import get_registry

    return sorted(t.name for t in get_registry().get_all_tools())


def completion_candidates(
    uri_or_prompt: str, argument: str, context_args: Optional[Dict[str, str]] = None
) -> List[str]:
    """All candidate values for one argument (unfiltered); ``[]`` when unavailable."""
    kind = _ARG_KINDS.get(argument)
    context_args = context_args or {}
    if kind is None:
        return []
    # `name` is only a measure name inside the measure template.
    if argument == "name" and "model/measure" not in uri_or_prompt:
        return []
    try:
        if kind == "table":
            return list_table_names()
        if kind == "measure":
            return list_measure_names(context_args.get("table") or context_args.get("table_name"))
        if kind == "page":
            return list_page_names()
        if kind == "visual":
            return list_visual_names(context_args.get("page") or context_args.get("page_name"))
        if kind == "capture":
            return capture_ids()
        if kind == "tool":
            return _tool_names()
    except Exception as e:  # noqa: BLE001 - completion is best-effort
        logger.debug("completion for %s/%s unavailable: %s", uri_or_prompt, argument, e)
    return []


def complete(
    uri_or_prompt: str,
    argument: str,
    value: str,
    context_args: Optional[Dict[str, str]] = None,
) -> Tuple[List[str], int, bool]:
    """``(values, total, has_more)`` for the typed ``value`` (prefix first, then substring)."""
    candidates = completion_candidates(uri_or_prompt, argument, context_args)
    needle = (value or "").lower()
    if needle:
        starts = [c for c in candidates if c.lower().startswith(needle)]
        contains = [c for c in candidates if needle in c.lower() and c not in starts]
        candidates = starts + contains
    total = len(candidates)
    return candidates[:_MAX_COMPLETIONS], total, total > _MAX_COMPLETIONS
