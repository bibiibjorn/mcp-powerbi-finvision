"""
Handler Registry System
Manages registration and lookup of tool handlers.

The MCP ``tools/list`` payload is built ONCE per registry state (A-33) together
with a cached jsonschema validator per tool (A-14), and invalidated on
``register()``. The registry injects the protocol-level arguments every tool
understands (``response_format``, ``page_size`` / ``next_token``) and — on tools
that can perform destructive work only — the single break-glass flag
``confirm_destructive`` (A-11 / A-30).

(The v6 deferred-loading / category-discovery API had no callers and was
removed — MCP clients defer tools client-side. L-17 / A-23.)
"""

from typing import Dict, Callable, Any, List, Optional, FrozenSet
from dataclasses import dataclass, field
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class ToolCategory(Enum):
    """Tool categories (the allowed ToolDefinition.category values)."""

    CORE = "core"  # Always loaded (connection, help)
    MODEL = "model"  # Table, column, measure, relationship, calc group, role ops
    BATCH = "batch"  # Batch operations & transactions
    QUERY = "query"  # DAX, search, data sources
    DAX = "dax"  # DAX intelligence, dependencies
    ANALYSIS = "analysis"  # Simple/full analysis, comparison
    PBIP = "pbip"  # PBIP analysis, report info, slicers, visuals
    DOCS = "docs"  # Documentation generation
    DEBUG = "debug"  # Debug tools
    AUTHORING = "authoring"  # PBIP report authoring & prototyping
    AUTONOMOUS = "autonomous"  # Retired in v7.8 (no tools); kept so the enum stays stable


# Short human-readable titles for MCP ToolAnnotations (2025-06-18 spec).
# Clients that render tool pickers (Claude Desktop, Inspector, IDE plugins)
# display these instead of the raw "NN_Category_Name" identifier. The numeric
# prefix on tool names helps category sort; the title strips it for display.
TOOL_TITLES: Dict[str, str] = {
    "00_Client_Mode": "Client Guard",
    "01_Connection": "Connect to Power BI Desktop",
    "02_Model_Operations": "Model Operations (tables/columns/measures)",
    "02_TMDL_Operations": "TMDL Editor (export/find-replace/bulk-rename)",
    "02_QA_Linguistic": "Q&A Linguistic Schema (synonyms)",
    "03_Batch_Operations": "Batch Operations (transactional)",
    "04_Run_DAX": "Run DAX Query (with trace timings)",
    "04_Query_Operations": "Query Operations (M, roles, RLS, search)",
    "05_DAX_Intelligence": "DAX Intelligence (analyze/optimize)",
    "05_Column_Usage_Mapping": "Column Usage Mapping",
    "06_Analysis_Operations": "Model Analysis & BPA",
    "07_PBIP_Operations": "PBIP Offline Analysis",
    "07_Report_Operations": "Report Operations",
    "07_Page_Operations": "Page CRUD & Display",
    "07_Visual_Operations": "Visual CRUD & Formatting",
    "07_Bookmark_Operations": "Bookmark Operations",
    "07_Theme_Operations": "Theme Operations",
    "08_Documentation_Word": "Word Documentation Export",
    "09_Debug_Operations": "Visual Debug & DAX Trace",
    "09_Validate": "Cross-Visual Validation",
    "09_Profile": "Performance Profiling",
    "09_Document": "Page / Measure Documentation",
    "10_Show_User_Guide": "Show User Guide",
    "11_PBIP_Authoring": "PBIP Authoring (pages/visuals/clone)",
    "13_Query_Plan": "DAX Query Plan (capture/parse/analyze)",
    "15_DAX_Refactor": "DAX Refactor (preview/apply transforms)",
    "16_Dependency_Graph": "Dependency Graph (model + report)",
    "17_Desktop_Bridge": "Desktop Bridge (reload + screenshot)",
}


# Tools whose responses can blow past the token budget — the response pipeline
# spills their results to output/ above max_result_tokens x 4 chars (instead of
# the 450K inline cap other tools get). They used to be hard-blocked (A-05).
# Must reference CURRENT tool names; test_high_token_tools_current pins this to
# the registry so it cannot rot (the previous inline set held only dead v6
# names, silently disabling the safeguard — P1).
HIGH_TOKEN_TOOLS = {
    "06_Analysis_Operations",  # BPA + performance + integrity, can be huge
    "02_TMDL_Operations",  # full-model TMDL export
    "07_PBIP_Operations",  # offline PBIP analysis / documentation
    "16_Dependency_Graph",  # whole-model dependency graph
    "05_Column_Usage_Mapping",  # full column→measure mapping
}


# Canonical call examples for the big `object_type` x `operation` dispatcher
# tools. Anthropic measured complex-parameter accuracy 72% -> 90% when a tool
# definition carries concrete example calls; these dispatchers are exactly the
# shape that benefits (valid JSON is easy, CORRECT arg combinations are not).
#
# Kept centrally here rather than on each ToolDefinition so the five dispatcher
# handlers stay untouched, and `test_tool_descriptions.py` can assert every
# example key against the tool's own inputSchema["properties"] (anti-drift: a
# renamed arg fails the suite loudly instead of shipping a wrong example).
#
# Discipline: single-line JSON when emitted, no prose per example, 2-4 per tool.
TOOL_EXAMPLES: Dict[str, List[Dict[str, Any]]] = {
    "02_Model_Operations": [
        {"object_type": "measure", "operation": "list", "table_name": "Sales"},
        {
            "object_type": "measure",
            "operation": "create",
            "table_name": "Sales",
            "measure_name": "Total Sales",
            "expression": "SUM ( Sales[Amount] )",
            "format_string": "#,##0",
            "display_folder": "KPI",
        },
        {
            "object_type": "measure",
            "operation": "safe_rename",
            "table_name": "Sales",
            "measure_name": "Total Sales",
            "new_name": "Sales Amount",
            "pbip_path": "C:/repo/Report.pbip",
        },
        {
            "object_type": "relationship",
            "operation": "create",
            "from_table": "Sales",
            "from_column": "DateKey",
            "to_table": "Date",
            "to_column": "DateKey",
            "from_cardinality": "Many",
            "to_cardinality": "One",
        },
    ],
    "03_Batch_Operations": [
        {
            "object_type": "measures",
            "batch_operation": "create",
            "items": [
                {
                    "table_name": "Sales",
                    "measure_name": "Total Qty",
                    "expression": "SUM ( Sales[Qty] )",
                }
            ],
            "options": {"use_transaction": True},
        },
        {
            "object_type": "measures",
            "batch_operation": "move_display_folder",
            "items": [
                {"table_name": "Sales", "measure_name": "Total Qty", "display_folder": "KPI"}
            ],
        },
        {
            "object_type": "changeset",
            "preview": True,
            "changeset": {"columns": {"delete": [{"table_name": "Sales", "column_name": "Tmp"}]}},
        },
    ],
    "07_Visual_Operations": [
        {
            "operation": "list",
            "pbip_path": "C:/repo/Report.pbip",
            "page_name": "Overview",
            "summary_only": True,
        },
        {
            "operation": "create",
            "pbip_path": "C:/repo/Report.pbip",
            "page_name": "Overview",
            "visual_type": "cardVisual",
            "title": "Total Sales",
            "position": {"x": 20, "y": 20, "width": 440, "height": 130},
            "measures": [{"table": "Sales", "measure": "Total Sales"}],
        },
        {
            "operation": "set_raw_property",
            "pbip_path": "C:/repo/Report.pbip",
            "page_name": "Overview",
            "visual_name": "a1b2c3d4e5f60718",
            "json_path": "visual.objects.value[0].properties.fontSize",
            "value": "14D",
            "wrap_literal": True,
        },
        {
            "operation": "add_field",
            "pbip_path": "C:/repo/Report.pbip",
            "page_name": "Overview",
            "visual_name": "Sales by Month",
            "table": "Date",
            "field": "Month",
            "bucket": "Category",
            "field_type": "Column",
        },
    ],
    "09_Debug_Operations": [
        {"operation": "set_path", "pbip_path": "C:/repo/Report.pbip"},
        {
            "operation": "visual",
            "page_name": "Overview",
            "visual_name": "Sales by Month",
            "trace": True,
            "include_slicers": True,
        },
        {"operation": "audit", "page_name": "Overview", "include_data": True},
        {
            "operation": "compare",
            "page_name": "Overview",
            "visual_id": "a1b2c3",
            "original_measure": "Total Sales",
            "optimized_expression": "SUMX ( Sales, Sales[Qty] * Sales[Price] )",
            "equivalence_check": True,
        },
    ],
    "16_Dependency_Graph": [
        {"operation": "build", "scope": "both", "pbip_path": "C:/repo/Report.pbip"},
        {"operation": "quick_lineage", "node_ref": "Sales[Total Sales]", "direction": "impact"},
        {
            "operation": "get_impact",
            "graph_id": "g1",
            "node_ref": "Sales[Total Sales]",
            "change": "rename",
            "new_name": "Sales Amount",
        },
        {
            "operation": "batch_impact",
            "graph_id": "g1",
            "node_refs": ["Sales[Total Sales]", "Sales[Total Qty]"],
            "change": "delete",
        },
    ],
}


# Ceiling for the emitted description (base text + Examples: block). The
# description is a SEARCH surface, not documentation — 10_Show_User_Guide owns
# the docs. Past this we drop examples (keeping at least one). 1024 is also the
# OpenAI function-description limit: GitHub Copilot sends every tool to its GPT
# models, and one longer description fails the whole request
# (tests/test_copilot_schema_compat.py).
_MAX_DESCRIPTION_CHARS = 1024

# Protocol-level arguments injected into every tool's emitted schema. The
# dispatcher honours them for every tool, so they must be declared (A-18). They
# carry no per-tool description: the session instructions explain them ONCE
# (L-10 - the old per-tool prose cost ~10k chars of tools/list).
INJECTED_PROPERTIES: Dict[str, Dict[str, Any]] = {
    "response_format": {"type": "string", "enum": ["concise", "detailed"]},
    "page_size": {"type": "integer", "minimum": 1},
    "next_token": {"type": "string"},
}

# Protocol-level arguments handled by server/mcp_app.py for ONE tool (the app,
# not the handler, implements them), keyed by tool name.
TOOL_EXTRA_PROPERTIES: Dict[str, Dict[str, Dict[str, Any]]] = {
    "17_Desktop_Bridge": {
        "inline_image": {
            "type": "boolean",
            "description": "screenshot*: also return the PNG inline as image content.",
        }
    },
}

# The single break-glass flag (A-11). Injected only on tools whose annotations
# declare destructiveHint=true (A-30): read-only tools never gate.
CONFIRM_DESTRUCTIVE_PROPERTY: Dict[str, Any] = {
    "type": "boolean",
    "description": "Break-glass: authorize a gated destructive op.",
}

# Legacy spelling of the break-glass flag (folded into confirm_destructive by
# server/arg_aliases.py, with a deprecated_args notice).
LEGACY_CONFIRM_ARG = "confirmed"

#: Private schema keyword holding an argument's long-form documentation. It is
#: rendered by ``10_Show_User_Guide tool=<name>`` and stripped from tools/list,
#: so the emitted ``description`` can stay one line (L-10).
DOC_KEY = "x-doc"


def _strip_private(spec: Any) -> Any:
    """Copy of a property spec without private ``x-*`` keywords (recursive)."""
    if isinstance(spec, dict):
        return {
            k: _strip_private(v)
            for k, v in spec.items()
            if not (isinstance(k, str) and k.startswith("x-"))
        }
    if isinstance(spec, list):
        return [_strip_private(v) for v in spec]
    return spec


def _format_examples_block(examples: List[Dict[str, Any]]) -> str:
    """Render examples as a compact `Examples:` block of single-line JSON."""
    import json as _json

    lines = ["Examples:"]
    for ex in examples:
        lines.append(_json.dumps(ex, separators=(",", ":"), ensure_ascii=True))
    return "\n".join(lines)


@dataclass
class ToolDefinition:
    """Definition of a tool with its handler"""

    name: str
    description: str
    handler: Callable
    input_schema: Dict[str, Any]
    category: str = "general"
    sort_order: int = 999  # Default to end if not specified
    annotations: Optional[Dict[str, Any]] = field(
        default=None
    )  # MCP ToolAnnotations hints (readOnlyHint, destructiveHint, etc.)
    output_schema: Optional[Dict[str, Any]] = field(
        default=None
    )  # JSON Schema for structured output validation
    # Canonical example calls surfaced to the client (see TOOL_EXAMPLES). When
    # left None, get_all_tools_as_mcp() falls back to the central map keyed by
    # tool name, so dispatcher handlers need no edit to gain examples.
    examples: Optional[List[Dict[str, Any]]] = field(default=None)
    # L-10: long-form usage notes. Rendered by 10_Show_User_Guide tool=<name>,
    # never emitted in tools/list (the description stays a short search surface).
    guide_notes: Optional[str] = field(default=None)
    # Per-object_type operation table for dispatcher tools whose `operation`
    # depends on another selector (02_Model_Operations; K-18). Guide-only.
    operations_by_type: Optional[Dict[str, List[str]]] = field(default=None)

    def resolved_examples(self) -> Optional[List[Dict[str, Any]]]:
        """Explicit examples if set, else the central TOOL_EXAMPLES entry."""
        if self.examples:
            return self.examples
        return TOOL_EXAMPLES.get(self.name)

    def is_destructive_capable(self) -> bool:
        """True when the tool's annotations declare destructiveHint=true."""
        return bool((self.annotations or {}).get("destructiveHint") is True)


def build_emitted_schema(tool_def: ToolDefinition, *, full: bool = False) -> Dict[str, Any]:
    """The inputSchema a client sees: the handler schema + injected protocol args.

    ``full=False`` (tools/list) drops the tool's DEFERRED arguments - rare-op
    args documented by ``10_Show_User_Guide tool=<name>`` - and every private
    ``x-*`` keyword (L-10). ``full=True`` keeps every argument: it is the schema
    the input validator and the ``unknown_args`` check use, so a deferred arg
    is still type-checked and never reported unknown.

    Non-mutating. Examples are NOT duplicated at the schema root - they live
    once, in the description (A-30 / L-10).
    """
    from server.tool_surface import deferred_args

    input_schema = tool_def.input_schema
    if not (
        isinstance(input_schema, dict)
        and input_schema.get("type") == "object"
        and isinstance(input_schema.get("properties"), dict)
    ):
        return input_schema
    schema = dict(input_schema)
    hidden = frozenset() if full else deferred_args(tool_def.name)
    props = {k: _strip_private(v) for k, v in input_schema["properties"].items() if k not in hidden}
    for key, spec in INJECTED_PROPERTIES.items():
        props.setdefault(key, dict(spec))
    for key, spec in TOOL_EXTRA_PROPERTIES.get(tool_def.name, {}).items():
        props.setdefault(key, dict(spec))
    if tool_def.is_destructive_capable():
        props.setdefault("confirm_destructive", dict(CONFIRM_DESTRUCTIVE_PROPERTY))
    schema["properties"] = props
    return schema


def build_emitted_description(tool_def: ToolDefinition) -> str:
    """The tools/list description: the short search surface + a pointer to the
    rare-op arguments the emitted schema omits (L-10) + the examples block."""
    from server.tool_surface import deferred_args

    description = tool_def.description
    hidden = deferred_args(tool_def.name)
    if hidden:
        description = (
            f"{description}\n+{len(hidden)} rare-op args: 10_Show_User_Guide tool={tool_def.name}"
        )
    examples = tool_def.resolved_examples()
    if examples:
        count = len(examples)
        block = _format_examples_block(examples)
        while count > 1 and len(description) + 1 + len(block) > _MAX_DESCRIPTION_CHARS:
            count -= 1
            block = _format_examples_block(examples[:count])
        description = f"{description}\n{block}"
    return description


class HandlerRegistry:
    """Central registry for all tool handlers."""

    def __init__(self):
        self._handlers: Dict[str, ToolDefinition] = {}
        self._categories: Dict[str, List[str]] = {}
        # P1-10: protect registration / iteration against concurrent access.
        import threading as _threading

        self._registry_lock = _threading.RLock()
        # A-33: tools/list payload + per-tool validators, built once per
        # registry state and dropped on register().
        self._mcp_tools_cache: Optional[list] = None
        self._emitted_schemas: Dict[str, Dict[str, Any]] = {}
        self._full_schemas: Dict[str, Dict[str, Any]] = {}
        self._validators: Dict[str, Any] = {}
        # Tools whose schema failed check_schema: remembered so a broken schema is
        # checked + logged once, not on every call (P-05).
        self._invalid_validators: set = set()

    def register(self, tool_def: ToolDefinition) -> None:
        """Register a tool handler"""
        # Validate required fields
        if not getattr(tool_def, "name", None):
            logger.warning("Tool registration skipped: missing 'name' field")
            return
        if not getattr(tool_def, "handler", None):
            logger.warning(
                f"Tool registration skipped for '{tool_def.name}': missing 'handler' field"
            )
            return
        if not getattr(tool_def, "input_schema", None):
            logger.warning(
                f"Tool registration skipped for '{tool_def.name}': missing 'input_schema' field"
            )
            return

        with self._registry_lock:
            self._handlers[tool_def.name] = tool_def

            # Track by category
            if tool_def.category not in self._categories:
                self._categories[tool_def.category] = []
            if tool_def.name not in self._categories[tool_def.category]:
                self._categories[tool_def.category].append(tool_def.name)

            self._invalidate_caches()

        logger.debug(f"Registered tool: {tool_def.name} (category: {tool_def.category})")

    def _invalidate_caches(self) -> None:
        self._mcp_tools_cache = None
        self._emitted_schemas = {}
        self._full_schemas = {}
        self._validators = {}
        self._invalid_validators = set()

    def get_handler(self, tool_name: str) -> Callable:
        """Get handler function for a tool"""
        if tool_name not in self._handlers:
            raise KeyError(f"Unknown tool: {tool_name}")
        return self._handlers[tool_name].handler

    def get_tool_def(self, tool_name: str) -> ToolDefinition:
        """Get full tool definition"""
        if tool_name not in self._handlers:
            raise KeyError(f"Unknown tool: {tool_name}")
        return self._handlers[tool_name]

    def get_all_tools(self) -> List[ToolDefinition]:
        """Get all registered tools"""
        with self._registry_lock:
            return list(self._handlers.values())

    def total_tools(self) -> int:
        """Number of currently registered tools."""
        with self._registry_lock:
            return len(self._handlers)

    def _build_mcp_tools(self) -> list:
        from mcp.types import Tool, ToolAnnotations

        with self._registry_lock:
            handler_snapshot = list(self._handlers.values())
        sorted_defs = sorted(handler_snapshot, key=lambda x: (x.sort_order, x.name))

        tools = []
        schemas: Dict[str, Dict[str, Any]] = {}
        for tool_def in sorted_defs:
            input_schema = build_emitted_schema(tool_def)
            schemas[tool_def.name] = input_schema
            tool_kwargs: Dict[str, Any] = {
                "name": tool_def.name,
                "description": build_emitted_description(tool_def),
                "inputSchema": input_schema,
            }

            # Pass through annotations if defined. Inject `title` from the
            # central TOOL_TITLES map so each annotations dict doesn't have to
            # carry a duplicate literal. Annotations-provided title still wins.
            title = None
            if tool_def.annotations:
                ann = dict(tool_def.annotations)
                if "title" not in ann and tool_def.name in TOOL_TITLES:
                    ann["title"] = TOOL_TITLES[tool_def.name]
                title = ann.get("title")
                tool_kwargs["annotations"] = ToolAnnotations(**ann)
            elif tool_def.name in TOOL_TITLES:
                title = TOOL_TITLES[tool_def.name]
                tool_kwargs["annotations"] = ToolAnnotations(title=title)
            # TS-01: the top-level `title` (MCP 2025-06-18 BaseMetadata) is what current
            # clients render; annotations.title stays for older ones.
            if title:
                tool_kwargs["title"] = title

            if tool_def.output_schema:
                tool_kwargs["outputSchema"] = tool_def.output_schema

            tools.append(Tool(**tool_kwargs))
        with self._registry_lock:
            self._emitted_schemas = schemas
        return tools

    def get_all_tools_as_mcp(self, include_deferred: Optional[bool] = None) -> list:
        """MCP Tool objects for ``tools/list`` (built once, cached — A-33).

        ``include_deferred`` is accepted for backward compatibility and ignored
        (every registered tool is always listed).
        """
        with self._registry_lock:
            cached = self._mcp_tools_cache
        if cached is None:
            cached = self._build_mcp_tools()
            with self._registry_lock:
                self._mcp_tools_cache = cached
        return list(cached)

    def get_emitted_schema(self, tool_name: str) -> Optional[Dict[str, Any]]:
        """The inputSchema the client sees for ``tool_name`` (None if unknown)."""
        if tool_name not in self._handlers:
            return None
        with self._registry_lock:
            schema = self._emitted_schemas.get(tool_name)
        if schema is None:
            self.get_all_tools_as_mcp()
            with self._registry_lock:
                schema = self._emitted_schemas.get(tool_name)
        return schema

    def get_full_schema(self, tool_name: str) -> Optional[Dict[str, Any]]:
        """Every argument ``tool_name`` accepts (emitted + deferred; None if unknown)."""
        if tool_name not in self._handlers:
            return None
        with self._registry_lock:
            schema = self._full_schemas.get(tool_name)
        if schema is None:
            schema = build_emitted_schema(self._handlers[tool_name], full=True)
            with self._registry_lock:
                self._full_schemas[tool_name] = schema
        return schema

    def declared_properties(self, tool_name: str) -> FrozenSet[str]:
        """Argument names ``tool_name`` accepts (emitted + deferred)."""
        schema = self.get_full_schema(tool_name) or {}
        props = schema.get("properties") if isinstance(schema, dict) else None
        return frozenset(props) if isinstance(props, dict) else frozenset()

    def get_input_validator(self, tool_name: str):
        """Cached jsonschema validator for the FULL schema (A-14 / L-10).

        ``check_schema`` runs once here instead of on every call (the SDK's
        ``jsonschema.validate`` re-checks the schema against the metaschema per
        call: 30-55 ms on the big dispatcher tools). None if unknown.
        """
        with self._registry_lock:
            v = self._validators.get(tool_name)
            if v is None and tool_name in self._invalid_validators:
                return None
        if v is not None:
            return v
        schema = self.get_full_schema(tool_name)
        if not isinstance(schema, dict):
            return None
        import jsonschema

        cls = jsonschema.validators.validator_for(schema)
        try:
            cls.check_schema(schema)
        except jsonschema.SchemaError as e:
            # A broken schema must not take the server down at boot; the tool
            # then runs without input validation (logged loudly).
            logger.error("Invalid inputSchema for %s (validation disabled): %s", tool_name, e)
            with self._registry_lock:
                self._invalid_validators.add(tool_name)
            return None
        v = cls(schema)
        with self._registry_lock:
            self._validators[tool_name] = v
        return v

    def prebuild(self, validators: bool = False) -> None:
        """Build the tools/list payload up front.

        P-05: the 28 ``jsonschema.check_schema`` calls (Draft 2020-12 metaschema,
        183-241 ms together = 15-20% of boot) are NOT run at startup any more:
        each tool's validator is built on its first call (6-9 ms, 30-55 ms for the
        big dispatcher tools) and cached. ``validators=True`` restores the eager
        build (tests that want to fail fast on a broken schema).
        """
        self.get_all_tools_as_mcp()
        if validators:
            for name in list(self._handlers):
                self.get_input_validator(name)

    def list_categories(self) -> List[str]:
        """List all categories"""
        return list(self._categories.keys())

    def has_tool(self, tool_name: str) -> bool:
        """Check if tool is registered"""
        return tool_name in self._handlers


# Global registry instance
_registry = HandlerRegistry()


def get_registry() -> HandlerRegistry:
    """Get the global handler registry"""
    return _registry
