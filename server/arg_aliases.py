"""Canonical argument vocabulary + legacy-alias normaliser (L-11 / A-32).

The same concept used to be spelled differently across tools (``query`` vs
``dax_query`` vs ``test_query``; ``page_name`` vs ``page_id`` vs ``page``; ...).
The schemas now declare ONE canonical name per concept; every legacy spelling
keeps working through this table and the response says so::

    "deprecated_args": {"dax_query": "renamed to query"}

Canonical vocabulary (see "Argument vocabulary" in Part III of docs/GUIDE.md):

==================  ==========================================================
concept             canonical (legacy aliases)
==================  ==========================================================
DAX query text      ``query`` (``dax_query``, ``test_query``)
project locator     ``pbip_path`` (``pbip_folder_path``)
one page            ``page_name`` = page id or display name (``page_id``, ``page``)
several pages       ``pages`` (``page_names``)
one visual          ``visual_name`` = id or exact title; ``visual_id`` = exact id
object refs         ``table_name`` / ``measure_name`` (``table``, ``measure``)
output format       ``output_format`` (``format``)
result cache        ``bypass_cache`` (``no_cache``)
break-glass flag    ``confirm_destructive`` (``confirmed``)
==================  ==========================================================

``normalize_tool_args`` runs twice per call: in ``server/mcp_app.py`` before
input validation (so a legacy name satisfies a ``required`` canonical one) and
in the dispatcher, which attaches the notices to the result. It is idempotent.
Handlers read the canonical name (and still fall back to the legacy one for
in-process callers that bypass the dispatcher).
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

__all__ = [
    "COMMON_ALIASES",
    "TOOL_ALIASES",
    "IGNORED_LEGACY_ARGS",
    "ArgConflict",
    "aliases_for",
    "normalize_tool_args",
]

#: Aliases every tool accepts.
COMMON_ALIASES: Dict[str, str] = {"confirmed": "confirm_destructive"}

#: Per-tool ``{legacy_name: canonical_name}``. Only TRUE synonyms live here: the
#: handler feeds both spellings into the same resolver.
TOOL_ALIASES: Dict[str, Dict[str, str]] = {
    "02_Model_Operations": {"table": "table_name"},
    # P2-188: 03's object type used to be spelled `operation`.
    "03_Batch_Operations": {"operation": "object_type"},
    "04_Run_DAX": {"dax_query": "query", "no_cache": "bypass_cache"},
    "04_Query_Operations": {"dax_query": "query", "test_query": "query"},
    "07_Bookmark_Operations": {"name": "display_name"},
    "07_PBIP_Operations": {"pbip_folder_path": "pbip_path"},
    "07_Page_Operations": {"page_id": "page_name"},
    "07_Visual_Operations": {"page_id": "page_name", "property_path": "json_path"},
    "09_Debug_Operations": {
        "dax_query": "query",
        "page": "page_name",
        "visual": "visual_id",
        "measure": "measure_name",
    },
    "09_Validate": {"page_names": "pages"},
    "09_Profile": {"format": "output_format"},
    "11_PBIP_Authoring": {
        "page_id": "page_name",
        "page": "page_name",
        "report_path": "pbip_path",
        "container_name": "container_display_name",
    },
    "13_Query_Plan": {"dax_query": "query"},
    "16_Dependency_Graph": {"format": "output_format"},
    "17_Desktop_Bridge": {"page": "page_name", "page_id": "page_name"},
}

#: Legacy / guessed args with no effect: accepted, dropped, and explained (so a
#: call written against an older doc still runs instead of erroring).
IGNORED_LEGACY_ARGS: Dict[str, Dict[str, str]] = {
    "04_Run_DAX": {
        "return_execution_metrics": "execution_metrics are always returned by mode=trace",
    },
    "07_Bookmark_Operations": {
        "recapture": "bookmarks are edited offline in the PBIR files - there is no live "
        "Desktop state to re-snapshot; set capture_data / capture_display / "
        "capture_current_page, or edit the bookmark JSON (get -> update)",
    },
    "08_Documentation_Word": {"output_format": "this tool only writes .docx"},
    "09_Document": {"output_format": "results are always JSON"},
    "13_Query_Plan": {
        "plan_type": "capture always records both plans; page one with "
        "operation=plan_rows plan=physical|logical",
    },
}


class ArgConflict(ValueError):
    """An alias and its canonical name were both passed with different values."""

    def __init__(self, conflicts: Dict[str, str]):
        self.conflicts = conflicts
        names = ", ".join(f"{a} vs {c}" for a, c in conflicts.items())
        super().__init__(f"Conflicting values for the same argument: {names}")


def aliases_for(tool_name: str) -> Dict[str, str]:
    """Every ``{alias: canonical}`` pair ``tool_name`` accepts."""
    out = dict(COMMON_ALIASES)
    out.update(TOOL_ALIASES.get(tool_name, {}))
    return out


def _legacy_confirm(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


_VALUE_COERCE: Dict[str, Callable[[Any], Any]] = {"confirmed": _legacy_confirm}


# ---------------------------------------------------------------------------
# Shape transforms for call forms the user's skills document
# ---------------------------------------------------------------------------


def _dependency_graph_object_form(args: Dict[str, Any]) -> Optional[str]:
    """16_Dependency_Graph without ``operation`` but with ``object_name`` (the
    pbi-lineage skill's form: object_type / object_name / table_name /
    direction / include_report_layer) -> ``quick_lineage`` on a ``node_ref``."""
    if args.get("operation") or not args.get("object_name"):
        return None
    name = str(args.pop("object_name"))
    table = args.pop("table_name", None)
    kind = str(args.pop("object_type", "") or "").lower()
    include_report = args.pop("include_report_layer", True)
    if table and kind in ("measure", "column", ""):
        args["node_ref"] = f"{table}[{name}]"
    else:
        args["node_ref"] = name
    args["operation"] = "quick_lineage"
    args.setdefault("scope", "both" if include_report is not False else "model")
    return (
        "object_type/object_name/table_name/include_report_layer -> "
        "operation=quick_lineage, node_ref, scope"
    )


def _dependency_graph_report_layer(args: Dict[str, Any]) -> Optional[str]:
    if "include_report_layer" not in args:
        return None
    flag = args.pop("include_report_layer")
    args.setdefault("scope", "both" if flag is not False else "model")
    return "include_report_layer -> scope"


_GRAPH_OP_RENAMES = {"get_upstream": "get_consumers", "get_downstream": "get_dependencies"}
_GRAPH_DIRECTION_RENAMES = {"upstream": "consumers", "downstream": "dependencies"}


def _dependency_graph_direction_words(args: Dict[str, Any]) -> Optional[str]:
    """S-32: ``get_upstream`` / ``get_downstream`` (and quick_lineage ``direction`` upstream /
    downstream) read backwards in this graph. The canonical names say what they return:
    ``get_consumers`` = what depends on the node, ``get_dependencies`` = what the node depends on.
    """
    notes = []
    op = args.get("operation")
    if isinstance(op, str) and op in _GRAPH_OP_RENAMES:
        args["operation"] = _GRAPH_OP_RENAMES[op]
        notes.append(f"operation {op} -> {args['operation']}")
    direction = args.get("direction")
    if isinstance(direction, str) and direction in _GRAPH_DIRECTION_RENAMES:
        args["direction"] = _GRAPH_DIRECTION_RENAMES[direction]
        notes.append(f"direction {direction} -> {args['direction']}")
    if not notes:
        return None
    return (
        "; ".join(notes)
        + " (consumers = what depends on the node, dependencies = what it depends on)"
    )


def _authoring_page_path(args: Dict[str, Any]) -> Optional[str]:
    """11_PBIP_Authoring ``page_path`` (".../definition/pages/<page_id>", the
    pbi-visual-author skill's form) -> ``page_name`` = the page folder id (+
    ``pbip_path`` = the part before ``/definition`` when none was given)."""
    raw = args.get("page_path")
    if not isinstance(raw, str) or not raw.strip():
        return None
    args.pop("page_path")
    norm = raw.replace("\\", "/").rstrip("/")
    args.setdefault("page_name", norm.rsplit("/", 1)[-1])
    marker = norm.lower().find("/definition/pages")
    if marker > 0:
        args.setdefault("pbip_path", norm[:marker])
    return "page_path -> page_name (+ pbip_path)"


_TRANSFORMS: Dict[str, List[Callable[[Dict[str, Any]], Optional[str]]]] = {
    "11_PBIP_Authoring": [_authoring_page_path],
    "16_Dependency_Graph": [
        _dependency_graph_object_form,
        _dependency_graph_report_layer,
        _dependency_graph_direction_words,
    ],
}


def normalize_tool_args(
    tool_name: str, arguments: Optional[Dict[str, Any]]
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Rewrite legacy names to canonical ones.

    Returns ``(args, notices)``; ``notices`` may hold ``deprecated_args``
    (``{legacy: explanation}``) and ``ignored_legacy_args``. The input is not
    mutated. Raises :class:`ArgConflict` when an alias and its canonical name
    carry different values (the caller turns it into an error envelope).
    """
    args: Dict[str, Any] = dict(arguments) if isinstance(arguments, dict) else {}
    deprecated: Dict[str, str] = {}
    conflicts: Dict[str, str] = {}

    for transform in _TRANSFORMS.get(tool_name, []):
        note = transform(args)
        if note:
            prev = deprecated.get("call_form")
            deprecated["call_form"] = f"{prev}; {note}" if prev else note

    for alias, canonical in aliases_for(tool_name).items():
        if alias not in args:
            continue
        value = args.pop(alias)
        coerce = _VALUE_COERCE.get(alias)
        if coerce is not None:
            value = coerce(value)
        if canonical in args and args[canonical] != value:
            if alias == "confirmed":
                # The explicit break-glass flag always wins over the legacy one.
                deprecated[alias] = f"renamed to {canonical} (ignored: {canonical} given)"
                continue
            conflicts[alias] = canonical
            continue
        args[canonical] = value
        deprecated[alias] = f"renamed to {canonical}"

    if conflicts:
        raise ArgConflict(conflicts)

    ignored: Dict[str, str] = {}
    for name, why in IGNORED_LEGACY_ARGS.get(tool_name, {}).items():
        if name in args:
            args.pop(name)
            ignored[name] = why

    notices: Dict[str, Any] = {}
    if deprecated:
        notices["deprecated_args"] = deprecated
    if ignored:
        notices["ignored_legacy_args"] = ignored
    return args, notices
