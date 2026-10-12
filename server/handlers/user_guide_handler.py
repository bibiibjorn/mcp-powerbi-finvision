"""
User Guide Handler

Shows a comprehensive user guide. The TOOL INVENTORY section is GENERATED from
the live HandlerRegistry (never hand-maintained) so it cannot drift out of sync
with the actually-registered tools — a prior hand-written version listed a
nonexistent tool, omitted five real ones, and named a tool removed in v7.8.
Only the stable preamble (quick start, workflows, tips) is prose.
"""

from typing import Any, Dict, List, Optional
import logging

from server.registry import ToolDefinition, get_registry

logger = logging.getLogger(__name__)


_TOPICS = ("index", "all", "workflows", "tips")


def handle_show_user_guide(args: Dict[str, Any]) -> Dict[str, Any]:
    """Show the user guide (tool inventory generated from the registry).

    L-18: the full guide is ~70K chars and mostly repeats tools/list, so the
    default is now the compact INDEX (quick start + one line per tool +
    workflows). ``tool=<name>`` returns one tool's full parameter section (+
    its section of the tool reference, Part III of docs/GUIDE.md, when
    available); ``topic='all'`` returns the complete legacy guide.
    """
    args = args or {}
    tool = args.get("tool")
    operation = args.get("operation")
    topic = (args.get("topic") or "index").strip().lower()
    if tool:
        registry = get_registry()
        if not registry.has_tool(tool):
            names = sorted(t.name for t in _exposed_tools(registry))
            return {
                "success": False,
                "error_type": "unknown_tool",
                "error": f"Unknown tool: {tool}",
                "valid_values": names,
            }
        if not _is_exposed(tool):
            return {
                "success": False,
                "error_type": "tool_hidden_by_profile",
                "error": f"{tool} is hidden by the active MCP_PBI_PROFILE and cannot be called.",
                "suggestion": "Switch MCP_PBI_PROFILE (or MCP_PBI_TOOLS_EXCLUDE) and restart the server.",
            }
        if operation:
            ops = _all_operations(registry.get_tool_def(tool))
            if ops and operation not in ops:
                return {
                    "success": False,
                    "error_type": "invalid_operation",
                    "error": f"{tool} has no operation {operation!r}",
                    "valid_values": sorted(ops),
                }
            return {
                "success": True,
                "tool": tool,
                "operation": operation,
                "guide": _operation_section(tool, operation),
            }
        return {"success": True, "tool": tool, "guide": _tool_section(tool)}
    if topic not in _TOPICS:
        return {
            "success": False,
            "error_type": "invalid_argument",
            "error": f"Unknown topic {topic!r}",
            "valid_values": list(_TOPICS),
        }
    return {"success": True, "topic": topic, "guide": _get_user_guide(topic)}


# ---------------------------------------------------------------------------
# Generated tool inventory
# ---------------------------------------------------------------------------
# Human-readable category labels for the numeric ToolCategory enum values.
_CATEGORY_LABELS = {
    "core": "Connection & Guide",
    "model": "Model Operations",
    "batch": "Batch Operations",
    "query": "Query & Search",
    "dax": "DAX Intelligence",
    "analysis": "Analysis & Dependency",
    "pbip": "PBIP / Report Authoring",
    "docs": "Documentation",
    "debug": "Debug / Validate / Profile / Bridge",
    "authoring": "Authoring",
    "autonomous": "Autonomous",
}
# Stable display order for categories.
_CATEGORY_ORDER = [
    "core",
    "model",
    "batch",
    "query",
    "dax",
    "analysis",
    "pbip",
    "docs",
    "debug",
    "authoring",
    "autonomous",
]


def _format_properties(
    input_schema: Dict[str, Any],
    *,
    full_text: bool = False,
    deferred: frozenset = frozenset(),
    only: Optional[set] = None,
) -> List[str]:
    """Render an input schema's properties as bullet lines.

    ``full_text`` (the per-tool section) prints the long-form ``x-doc`` text and
    never trims; ``deferred`` args are marked - tools/list omits them (L-10).
    """
    from server.registry import DOC_KEY

    if not isinstance(input_schema, dict):
        return []
    props = input_schema.get("properties")
    if not isinstance(props, dict):
        return []
    required = set(input_schema.get("required") or [])
    lines: List[str] = []
    for name, spec in props.items():
        if only is not None and name not in only:
            continue
        # The registry injects `confirm_destructive` on destructive-capable
        # tools; it is documented once in the header rather than per tool.
        if name == "confirm_destructive":
            continue
        spec = spec if isinstance(spec, dict) else {}
        ptype = spec.get("type", "any")
        if isinstance(ptype, list):
            ptype = "|".join(str(t) for t in ptype)
        req = " (required)" if name in required else ""
        desc = spec.get("description", "")
        doc = spec.get(DOC_KEY) if full_text else None
        if isinstance(doc, str) and doc:
            desc = doc
        enum = spec.get("enum")
        enum_str = f" — one of {enum}" if enum else ""
        # The compact inventory keeps each line short; the tool section does not.
        if not full_text and desc and len(desc) > 140:
            desc = desc[:137] + "..."
        detail = f": {desc}" if desc else ""
        rare = " [rare-op: not in tools/list]" if name in deferred else ""
        lines.append(f"  - `{name}` ({ptype}{req}){enum_str}{rare}{detail}")
    return lines


def _all_operations(t: ToolDefinition) -> set:
    ops = set(_operation_values(t.input_schema))
    for names in (t.operations_by_type or {}).values():
        ops.update(names)
    return ops


def _mentions(spec: Dict[str, Any], operation: str) -> bool:
    import re

    from server.registry import DOC_KEY

    text = " ".join(str(spec.get(k) or "") for k in ("description", DOC_KEY))
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(operation)}(?![A-Za-z0-9_])", text) is not None


def _operation_section(tool_name: str, operation: str) -> str:
    """Arguments that concern ONE operation: the selectors, required args and
    every argument whose documentation names the operation (describe_op, K-18)."""
    from server.tool_surface import deferred_args

    t = get_registry().get_tool_def(tool_name)
    props = (t.input_schema or {}).get("properties") or {}
    selectors = {"operation", "object_type", "batch_operation", "sub_operation"}
    if "operation" not in props:
        selectors.add("mode")  # 04_Run_DAX / 08: mode IS the operation selector
    keep = {n for n in props if n in selectors}
    keep |= set((t.input_schema or {}).get("required") or [])
    keep |= {
        n for n, spec in props.items() if isinstance(spec, dict) and _mentions(spec, operation)
    }
    out = [f"# {t.name} operation={operation}", ""]
    types = [k for k, v in (t.operations_by_type or {}).items() if operation in v]
    if types:
        out.append(f"object_type: {', '.join(types)}")
        out.append("")
    out.append("- **Arguments that mention this operation** (plus selectors / required):")
    out.extend(
        _format_properties(
            t.input_schema, full_text=True, deferred=deferred_args(t.name), only=keep
        )
    )
    out += [
        "",
        f"Arguments not listed here may still apply: 10_Show_User_Guide tool={t.name}.",
    ]
    return "\n".join(out)


def _operation_values(input_schema: Dict[str, Any]) -> List[str]:
    props = (input_schema or {}).get("properties") or {}
    for key in ("operation", "object_type", "mode"):
        enum = (props.get(key) or {}).get("enum")
        if enum:
            return [str(v) for v in enum]
    return []


def _is_exposed(tool_name: str) -> bool:
    """False for a tool the active ``MCP_PBI_PROFILE`` hides from ``tools/list`` (N-33)."""
    try:
        from server import tool_profiles

        return tool_profiles.is_exposed(tool_name)
    except Exception:  # noqa: BLE001 - never break the guide over the profile lookup
        return True


def _exposed_tools(registry) -> List[ToolDefinition]:
    return [t for t in registry.get_all_tools() if _is_exposed(t.name)]


def _generate_tool_index() -> str:
    """One line per tool: name — title — operations (compact default view)."""
    from server.registry import TOOL_TITLES

    registry = get_registry()
    tools = sorted(_exposed_tools(registry), key=lambda td: (td.sort_order, td.name))
    out = [f"## TOOL INDEX ({len(tools)} tools)", ""]
    out.append(
        "_Generated from the live registry. Details: `10_Show_User_Guide` with "
        '`tool: "<name>"` (or `topic: "all"` for everything)._'
    )
    out.append("")
    for t in tools:
        title = TOOL_TITLES.get(t.name, "")
        ops = _operation_values(t.input_schema)
        ops_txt = f" — ops: {', '.join(ops)}" if ops else ""
        out.append(f"- `{t.name}` — {title}{ops_txt}")
    return "\n".join(out)


def _tool_section(tool_name: str) -> str:
    """Full parameter section for one tool + its tool-reference section (GUIDE.md Part III).

    L-10: this is where the text tools/list no longer carries lives - the long
    usage notes (``guide_notes``), every argument incl. the rare-op ones, the
    long-form ``x-doc`` of each argument, and (02_Model_Operations) the
    operations valid per object_type.
    """
    from server.tool_surface import deferred_args

    t = get_registry().get_tool_def(tool_name)
    out = [f"# {t.name}", ""]
    if t.description:
        out.append(t.description.strip())
    if t.guide_notes:
        out += ["", "## Usage notes", "", t.guide_notes.strip()]
    if t.operations_by_type:
        out += ["", "## Operations by object_type", ""]
        for kind, ops in t.operations_by_type.items():
            out.append(f"- `{kind}`: {', '.join(ops)}")
    prop_lines = _format_properties(t.input_schema, full_text=True, deferred=deferred_args(t.name))
    out.append("")
    out.append("- **Parameters**:" if prop_lines else "- **Parameters**: none")
    out.extend(prop_lines)
    try:
        from server.mcp_app import _tool_reference_section

        out += ["", "---", "", _tool_reference_section(tool_name)]
    except Exception:  # noqa: BLE001 - docs are optional on an install
        pass
    return "\n".join(out)


def _generate_tool_inventory() -> str:
    """Build the tool-inventory markdown from the live registry."""
    registry = get_registry()
    tools = _exposed_tools(registry)
    total = len(tools)

    # Group by category, preserving each tool's sort_order within the group.
    by_cat: Dict[str, List[ToolDefinition]] = {}
    for t in tools:
        by_cat.setdefault(t.category or "general", []).append(t)
    for cat_tools in by_cat.values():
        cat_tools.sort(key=lambda td: (td.sort_order, td.name))

    out: List[str] = []
    out.append(f"## TOOL INVENTORY ({total} tools)")
    out.append("")
    out.append(
        "_Generated from the live tool registry — always current. Pass "
        "`confirm_destructive: true` to authorize any gated destructive op._"
    )
    out.append("")

    ordered_cats = [c for c in _CATEGORY_ORDER if c in by_cat]
    ordered_cats += [c for c in sorted(by_cat) if c not in _CATEGORY_ORDER]

    for cat in ordered_cats:
        label = _CATEGORY_LABELS.get(cat, cat.title())
        cat_tools = by_cat[cat]
        out.append(f"### {label} ({len(cat_tools)} tool{'s' if len(cat_tools) != 1 else ''})")
        out.append("")
        for t in cat_tools:
            out.append(f"#### {t.name}")
            if t.description:
                out.append(t.description.strip())
            prop_lines = _format_properties(t.input_schema)
            if prop_lines:
                out.append("- **Parameters**:")
                out.extend(prop_lines)
            else:
                out.append("- **Parameters**: none")
            out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Static preamble (stable prose — quick start, workflows, tips)
# ---------------------------------------------------------------------------
_PREAMBLE = """# MCP-PowerBi-Finvision User Guide

## QUICK START

1. Open Power BI Desktop with your model
2. `01_Connection` (operation='detect') to find running instances
3. `01_Connection` (operation='connect', model_index=0) to connect (or operation='ensure')
4. Explore with `06_Analysis_Operations` (operation='simple') or
   `02_Model_Operations` (object_type='table', operation='list')

The tool inventory below is generated live from the server's registry, so the
tool names, categories, and parameters shown are exactly what this build
exposes — follow them verbatim.

---
"""

_WORKFLOWS = """
---

## COMMON WORKFLOWS

### Model Health Check
1. `01_Connection` (operation='connect')
2. `06_Analysis_Operations` (operation='full', scope='all')
3. Review and address best-practice violations worst-first

### Measure Development
1. `02_Model_Operations` (object_type='measure', operation='list') — study existing measures
2. `05_DAX_Intelligence` (analysis_mode='all') — analyze DAX
3. `02_Model_Operations` (object_type='measure', operation='create')
4. `04_Run_DAX` — test with real data
5. `16_Dependency_Graph` — verify dependencies / impact before changing others

### DAX Debugging
1. `09_Debug_Operations` (operation='visual') — capture filter context + run
2. `05_DAX_Intelligence` (analysis_mode='all') — optimization recommendations
3. `13_Query_Plan` — plan-level diagnosis for a stubborn slow measure
4. `09_Validate` (operation='expected_value') — assert the fix is correct

### Live Report Iteration (Desktop stays open)
1. Live model edits (`02_*`/`03_*`/`15_*`) still unsaved in Desktop? `17_Desktop_Bridge`
   (operation='save') FIRST - a reload re-reads disk and silently discards them
2. Edit PBIR via `07_Visual_Operations` / `11_PBIP_Authoring` / `07_Theme_Operations` (batch the edits)
3. `17_Desktop_Bridge` (operation='reload_and_screenshot') ONCE - reload + capture in one call.
   Never save after disk edits (a save overwrites them); the bridge refuses either unsafe order
4. Review the screenshot; iterate; verify numbers with `04_Run_DAX`
   Notes: Bridge CLI 1.0.0 is supported (`status` shows bridge_cli_version). The reload applies the
   REPORT only: metadata-only TMDL edits can use Microsoft's `powerbi-report-author preview
   --reload-with-model` after an explicit hasUnsavedChanges=false; partition/M changes need
   close-without-save + `17_Desktop_Bridge` (operation='open')

### Offline PBIP Analysis (no live connection)
1. `07_PBIP_Operations` (operation='analyze', output_format='html')
2. `07_PBIP_Operations` (operation='query_unused') — dead code
3. `16_Dependency_Graph` — impact of a planned rename/delete

---

## TIPS

- Start with `01_Connection` before any model/debug tool.
- `06_Analysis_Operations` operation='simple' for quick checks, 'full' for BPA.
- Always dry_run TMDL find_replace / bulk_rename first.
- Check `16_Dependency_Graph` (get_impact) before modifying/deleting a measure.
- PBIP tools (`07_*`) work offline; report edits reload live via `17_Desktop_Bridge`.
- Model edits via `02_Model_Operations` (live TOM) propagate instantly - no reload; verify with
  `04_Run_DAX`. They live in Desktop's memory until saved, and a reload discards them.
- `04_Run_DAX` caches per model generation; `bypass_cache=true` forces a round trip.
- `04_Run_DAX` mode='validate' asks the engine to parse/bind a query WITHOUT executing it
  ({valid, errors[], elapsed_ms}) - use it to prove new DAX cheaply; the resource
  `powerbi://docs/dax-query-rules` lists the rules queries must follow.
- `05_DAX_Intelligence` operation='lint_model' checks every measure at once.
- Every live write is journaled: `02_Model_Operations` object_type='model' `history` / `undo` /
  `replay` recover edits a reload or crash discarded; `checkpoint_create` before big refactors.
- Metadata-only TMDL edits on disk go live with `17_Desktop_Bridge` reload `include_model=true`
  (classifier refuses partition / M / data-source changes -> close without saving + reopen).
- `01_Connection` connect_xmla = a published model READ-ONLY (writes refused: service_read_only);
  connect_folder = a PBIP model folder offline (writes serialise straight to the TMDL files).
- `09_Validate` test_put / test_run / test_export keep a JSON model test suite next to the model
  (measure / DAX / RLS / snapshot / performance / metadata tests; JUnit export for CI).
- `13_Query_Plan` trace_start -> use the report -> trace_fetch / trace_stop: every visual query
  with its SE/FE split (auto-stops after max_duration_s).
- `MCP_PBI_PROFILE=model|report` (+ `MCP_PBI_TOOLS_EXCLUDE`) hides tools a session does not need.
- Destructive/catastrophic ops are gated: pass `confirm_destructive: true` to proceed.
"""


def _get_user_guide(topic: str = "all") -> str:
    if topic == "workflows":
        return _WORKFLOWS.split("## TIPS")[0]
    if topic == "tips":
        return "## TIPS" + _WORKFLOWS.split("## TIPS", 1)[1]
    if topic == "index":
        return _PREAMBLE + "\n" + _generate_tool_index() + _WORKFLOWS
    return _PREAMBLE + "\n" + _generate_tool_inventory() + _WORKFLOWS


def register_user_guide_handlers(registry):
    """Register user guide handler"""
    tool = ToolDefinition(
        name="10_Show_User_Guide",
        description=(
            "USER GUIDE generated from the live registry. Default: compact index of every "
            "tool and its operations. tool=<name>: that tool's FULL argument list (incl. "
            "rare-op args tools/list omits) + reference section; add operation=<op> for one "
            "op. topic=all|workflows|tips. No connection; text only."
        ),
        guide_notes=(
            "Show the full USER GUIDE / HELP for this server: every registered "
            "tool with its operations and parameters, plus quick-start and "
            "common workflows (model health check, measure development, DAX "
            "debugging, live report iteration). The tool inventory is GENERATED "
            "from the live registry, so it is always exactly what this build "
            "exposes — read it here rather than guessing an op name. Default = "
            "compact index; pass tool=<name> for one tool's parameters, or "
            "topic='all' for everything. Needs no connection. This is the DOCS "
            "surface; it returns text only and performs no model or report work."
        ),
        handler=handle_show_user_guide,
        input_schema={
            "type": "object",
            "properties": {
                "tool": {
                    "type": "string",
                    "description": "Registered tool name: return that tool's parameters + reference.",
                },
                "operation": {
                    "type": "string",
                    "description": "With tool: only the arguments of this operation.",
                },
                "topic": {
                    "type": "string",
                    "enum": list(_TOPICS),
                    "description": "index (default) | all (full guide) | workflows | tips",
                },
            },
            "required": [],
        },
        category="core",
        sort_order=110,
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    registry.register(tool)
    logger.info("Registered user guide handler")
