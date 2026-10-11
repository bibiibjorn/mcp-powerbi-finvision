"""MCP protocol surface: builds the low-level ``mcp.server.Server`` app.

Extracted from ``src/pbixray_server_enhanced.py`` so the whole protocol layer
(tools/list, tools/call pipeline, resources, templates, prompts, logging) can be
driven end-to-end in tests over the SDK's in-memory transport (A-20).

``create_server(registry, dispatcher, ...)`` wires:

* ``tools/list``  → the registry's pre-built Tool list (A-33);
* ``tools/call``  → cached-validator input check (A-14; the SDK's per-call
  ``jsonschema.validate`` re-checked the metaschema every time) → rate limit →
  ``dispatcher.dispatch_and_render_async`` (handler + response pipeline on the
  worker thread) → ``CallToolResult`` with ``isError`` and the
  ``anthropic/maxResultSizeChars`` _meta;
* ``resources/*`` incl. templates (A-01 / L-14);
* ``prompts/*`` (A-08 / L-04);
* ``logging/setLevel`` → declares the ``logging`` capability (A-27).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp import types
from mcp.server import Server

from core.utilities.json_utils import MCPJsonEncoder
from core.validation.error_enrichment import enrich_error_response
from core.validation.error_handler import ErrorHandler
from core.validation.error_response import error_envelope, normalize_error_envelope
from server import masking_layer
from server.arg_aliases import ArgConflict, normalize_tool_args
from server.dax_query_rules import DAX_QUERY_RULES
from server.prompts import get_prompt_messages, get_prompts
from server.resources import (
    GUIDE_RELATIVE_PATH,
    ResourceManager,
    doc_file_resolver,
    get_resource_manager,
    markdown_section,
    tool_reference_region,
)
from server.response_pipeline import MAX_RESULT_SIZE_META, render_result

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Session instructions (A-09 / A-34 / L-09) — generated from the registry
# ---------------------------------------------------------------------------

#: One valid call per core workflow step. Validated against the emitted
#: schemas by tests/test_review_a_prompts_instructions.py.
QUICKSTART_CALLS: List[Dict[str, Any]] = [
    {
        "tool": "01_Connection",
        "args": {"operation": "ensure"},
        "why": "connect (idempotent; auto-detects the running Desktop)",
    },
    {
        "tool": "02_Model_Operations",
        "args": {"object_type": "measure", "operation": "list"},
        "why": "inspect the model (object_type + operation)",
    },
    {
        "tool": "04_Run_DAX",
        "args": {"query": "EVALUATE TOPN(10, 'Sales')"},
        "why": "run DAX (arg is `query`; must start with EVALUATE)",
    },
    {
        "tool": "09_Debug_Operations",
        "args": {"operation": "set_path", "pbip_path": "C:/repo/Report.pbip"},
        "why": "point visual debugging at the PBIP project",
    },
    {
        "tool": "17_Desktop_Bridge",
        "args": {"operation": "reload_and_screenshot"},
        "why": "after a PBIR edit: reload the running Desktop in place + screenshot",
    },
]

SAFETY_RULES = [
    "17_Desktop_Bridge reload re-reads disk and DISCARDS unsaved in-memory model edits: "
    "check status (hasUnsavedChanges) and save first (or pass save_first=true).",
    "Live model edits (02_Model_Operations / 03 / 15) take effect immediately: verify them "
    "with 04_Run_DAX, never with a reload.",
    "confirm_destructive=true is the single break-glass flag for a gated destructive op; a "
    "result with error_type timeout_outcome_unknown must be VERIFIED before any retry (or pass "
    "request_id=<unique string> on a write: re-sending the same call returns the recorded outcome "
    "instead of executing twice).",
]


def build_instructions(registry, version: str) -> str:
    """Session instructions generated from the live registry (never drifts)."""
    from server.registry import ToolCategory

    tools = registry.get_all_tools()
    names = {t.name for t in tools}
    cats = sorted({t.category for t in tools})
    valid_cats = {c.value for c in ToolCategory}
    lines = [
        f"MCP-PowerBi-Finvision v{version} — Power BI Desktop MCP server "
        f"({len(tools)} tools, categories: {', '.join(c for c in cats if c in valid_cats)}).",
        "",
        "Quick start (arguments are exact JSON):",
    ]
    step = 1
    for call in QUICKSTART_CALLS:
        if call["tool"] not in names:
            continue
        args = json.dumps(call["args"], separators=(", ", ": "))
        lines.append(f"{step}) {call['tool']} {args}  — {call['why']}")
        step += 1
    lines += [
        "",
        "Rules that prevent real damage:",
        *[f"- {rule}" for rule in SAFETY_RULES],
        "",
        "Every result uses one envelope: success, error_type, error (+ suggestion / "
        "retry_with). Results over 450K chars are written to a file (_full_output).",
        "Every tool accepts response_format (concise = lists capped at 25, prose dropped; "
        "detailed = default) and page_size + next_token (paginate the result's main list; "
        "the result names it in paginated_key). A result over ~60K chars comes back as the first "
        "page of its main list (truncated_inline); pass full=true with response_format=detailed "
        "for everything.",
        "Legacy argument names still work and are reported in deprecated_args. Canonical "
        "names: query (DAX text), pbip_path, page_name (page id or display name), "
        "visual_name (id or exact title), confirm_destructive.",
        "Full tool reference, incl. rare-op arguments a schema does not list: "
        "10_Show_User_Guide tool=<name> (add operation=<op> for one op) or the resource "
        "powerbi://docs/tools/{tool}.",
        "Model, DAX and authoring live here; the rendered canvas and interaction tests live in "
        "the pbi-webview2 server.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Resources (A-01 / L-14)
# ---------------------------------------------------------------------------


def _tool_reference_text() -> Optional[str]:
    """Part III (tool reference) of docs/GUIDE.md, or None when the guide is not on this install."""
    target = PROJECT_ROOT / GUIDE_RELATIVE_PATH
    if not target.exists():
        return None
    return tool_reference_region(target.read_text(encoding="utf-8"))


def _tool_reference_section(tool: str) -> str:
    region = _tool_reference_text()
    if region is None:
        raise LookupError(
            f"The tool reference ({GUIDE_RELATIVE_PATH}) is not available on this install"
        )
    section = markdown_section(region, f"`{tool}`")
    if section is None:
        raise LookupError(f"No tool-reference section for {tool!r}")
    return section


def seed_resources(rm: ResourceManager, dispatcher=None) -> None:
    """Register the always-available resources + templates. Providers are lazy."""

    def _tool_reference_provider() -> str:
        region = _tool_reference_text()
        if region is None:
            return f"(resource not available on this install: {GUIDE_RELATIVE_PATH})"
        return region

    def _bpa_rules_provider() -> str:
        target = PROJECT_ROOT / "core" / "bpa.json"
        if not target.exists():
            return '{"error": "bpa.json not found"}'
        return target.read_text(encoding="utf-8")

    def _connection_status_provider() -> str:
        try:
            from core.infrastructure.connection_state import connection_state

            status = connection_state.get_status()
            pbip_info = connection_state.get_pbip_info() or {}
            return json.dumps(
                {"status": status, "pbip": pbip_info},
                separators=(",", ":"),
                cls=MCPJsonEncoder,
            )
        except Exception as e:  # noqa: BLE001
            return json.dumps({"error": f"{type(e).__name__}: {e}"})

    async def _roots_provider() -> str:
        """Awaits roots/list ON the loop (the sync bridge would deadlock it)."""
        from server.roots import alist_root_paths

        return json.dumps({"roots": await alist_root_paths()}, separators=(",", ":"))

    def _user_guide_provider() -> str:
        from server.handlers.user_guide_handler import handle_show_user_guide

        return handle_show_user_guide({"topic": "all"}).get("guide", "")

    def _dispatcher_state_provider() -> str:
        stats = dispatcher.get_stats() if dispatcher is not None else {}
        return json.dumps(stats, separators=(",", ":"), cls=MCPJsonEncoder)

    rm.register_dynamic_resource(
        uri="powerbi://docs/user-guide",
        name="User Guide",
        description="Generated user guide: every registered tool, its operations and parameters.",
        provider=_user_guide_provider,
        mime_type="text/markdown",
    )
    rm.register_dynamic_resource(
        uri="powerbi://docs/tool-reference",
        name="Tool Reference",
        description="Per-tool operation tables and argument details (Part III of docs/GUIDE.md).",
        provider=_tool_reference_provider,
        mime_type="text/markdown",
    )
    rm.register_dynamic_resource(
        uri="powerbi://docs/dax-query-rules",
        name="DAX Query Rules",
        description="Checklist for writing DAX EVALUATE queries the engine accepts (validate first).",
        provider=lambda: DAX_QUERY_RULES,
        mime_type="text/markdown",
    )
    rm.register_dynamic_resource(
        uri="powerbi://metadata/bpa-rules",
        name="BPA Rules",
        description="Best Practice Analyzer rule definitions (JSON).",
        provider=_bpa_rules_provider,
        mime_type="application/json",
    )
    rm.register_dynamic_resource(
        uri="powerbi://state/connection",
        name="Live Connection State",
        description="Current PBI Desktop connection + PBIP workspace (JSON snapshot).",
        provider=_connection_status_provider,
        mime_type="application/json",
    )
    rm.register_dynamic_resource(
        uri="powerbi://state/roots",
        name="Declared Workspace Roots",
        description="Workspace roots the MCP client has declared via roots/list.",
        provider=_roots_provider,
        mime_type="application/json",
    )
    rm.register_dynamic_resource(
        uri="powerbi://state/dispatcher",
        name="Dispatcher State",
        description="Call counts and calls still running after a timeout (stuck_calls).",
        provider=_dispatcher_state_provider,
        mime_type="application/json",
    )
    rm.register_template(
        "powerbi://docs/tools/{tool}",
        name="Tool reference section",
        description="Tool-reference section (Part III of docs/GUIDE.md) for one tool, e.g. powerbi://docs/tools/04_Run_DAX",
        resolver=lambda tool: _tool_reference_section(tool),
        mime_type="text/markdown",
    )
    rm.register_template(
        "powerbi://docs/visual-catalog/{type}",
        name="Visual catalog entry",
        description="Roles + formatting surface of one visual type (docs/visual-catalog/<type>.md)",
        resolver=doc_file_resolver(PROJECT_ROOT / "docs" / "visual-catalog", "type"),
        mime_type="text/markdown",
    )
    _seed_live_resources(rm)
    rm.register_template(
        "powerbi://docs/design-principles/{topic}",
        name="Design principle",
        description="Report design guidance by topic (docs/design-principles/<topic>.md)",
        resolver=doc_file_resolver(PROJECT_ROOT / "docs" / "design-principles", "topic"),
        mime_type="text/markdown",
    )


def _seed_live_resources(rm: ResourceManager) -> None:
    """F-29: read-only live templates (model / report / capture), see server/live_resources.py."""
    from server import live_resources as live

    rm.register_template(
        "powerbi://model/measure/{table}/{name}",
        name="Measure (live)",
        description="DAX, format string, folder, state and dependencies of one measure of the "
        "connected model, e.g. powerbi://model/measure/Sales/Total%20Sales (percent-encode /).",
        resolver=live.measure_resource,
        mime_type="application/json",
        lenient=True,
        offload=True,
    )
    rm.register_dynamic_resource(
        uri="powerbi://model/schema",
        name="Model schema (live)",
        description="Tables with their columns and measure names + relationships of the "
        "connected model (JSON).",
        provider=live.schema_resource,
        mime_type="application/json",
        offload=True,
    )
    rm.register_dynamic_resource(
        uri="powerbi://report/pages",
        name="Report pages",
        description="Pages of the PBIP report in view order: id, display name, size, visual "
        "count (JSON).",
        provider=live.pages_resource,
        mime_type="application/json",
        offload=True,
    )
    rm.register_template(
        "powerbi://report/page/{page}/visuals",
        name="Page visuals",
        description="Visuals of one page (page id or display name): id, type, title, position, "
        "bound fields (JSON).",
        resolver=live.page_visuals_resource,
        mime_type="application/json",
        lenient=True,
        offload=True,
    )
    rm.register_template(
        "powerbi://capture/{capture_id}",
        name="Query-plan capture",
        description="Summary of a 13_Query_Plan capture kept in memory (raw plan text omitted).",
        resolver=live.capture_resource,
        mime_type="application/json",
    )


def _complete_argument(ref: Any, argument: Any, context: Any) -> Optional[types.Completion]:
    """completion/complete: table / measure / page / visual / capture / tool values (F-29)."""
    from server import live_resources as live

    if isinstance(ref, types.ResourceTemplateReference):
        origin = str(ref.uri)
    else:
        origin = f"prompt:{getattr(ref, 'name', '')}"
    ctx_args = dict(getattr(context, "arguments", None) or {})
    values, total, has_more = live.complete(
        origin, str(argument.name), str(argument.value or ""), ctx_args
    )
    return types.Completion(values=values, total=total, hasMore=has_more)


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------


#: Largest PNG returned inline as ImageContent (A-31b / L-15).
INLINE_IMAGE_MAX_BYTES = 5 * 1024 * 1024


def _text_result(
    text: str, is_error: bool, images: Optional[List[Any]] = None
) -> types.CallToolResult:
    content: List[Any] = [
        types.TextContent(
            type="text",
            text=text,
            _meta={"anthropic/maxResultSizeChars": MAX_RESULT_SIZE_META},
        )
    ]
    content.extend(images or [])
    return types.CallToolResult(content=content, isError=is_error)


def _png_paths(result: Any, depth: int = 0) -> List[str]:
    """PNG paths a bridge result points at (``output`` keys, <= 2 levels deep)."""
    found: List[str] = []
    if depth > 2 or not isinstance(result, dict):
        return found
    for key, value in result.items():
        if key == "output" and isinstance(value, str) and value.lower().endswith(".png"):
            found.append(value)
        elif isinstance(value, dict):
            found.extend(_png_paths(value, depth + 1))
    return found


def inline_images(tool_name: str, args: Dict[str, Any], result: Any) -> List[Any]:
    """ImageContent for ``17_Desktop_Bridge ... inline_image=true`` (opt-in)."""
    if tool_name != "17_Desktop_Bridge" or args.get("inline_image") is not True:
        return []
    if not isinstance(result, dict) or result.get("success") is False:
        return []
    import base64

    images: List[Any] = []
    for path in _png_paths(result)[:1]:
        p = Path(path)
        try:
            if p.is_file() and p.stat().st_size <= INLINE_IMAGE_MAX_BYTES:
                images.append(
                    types.ImageContent(
                        type="image",
                        data=base64.b64encode(p.read_bytes()).decode("ascii"),
                        mimeType="image/png",
                    )
                )
        except OSError as e:
            logger.debug("inline_image: could not read %s: %s", path, e)
    return images


def _envelope_result(tool_name: str, envelope: Dict[str, Any]) -> types.CallToolResult:
    rendered = render_result(tool_name, envelope)
    return _text_result(rendered.text, rendered.is_error)


def validate_arguments(registry, name: str, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Validate against the cached validator; None if valid, else an error envelope."""
    if not registry.has_tool(name):
        return None  # the dispatcher reports unknown_tool
    validator = registry.get_input_validator(name)
    if validator is None:
        return None
    errors = sorted(validator.iter_errors(arguments), key=lambda e: list(e.absolute_path))
    if not errors:
        return None
    first = errors[0]
    path = ".".join(str(p) for p in first.absolute_path)
    extra: Dict[str, Any] = {"argument": path or None}
    if first.validator == "enum":
        extra["valid_values"] = list(first.validator_value)
    if len(errors) > 1:
        extra["other_errors"] = [e.message for e in errors[1:5]]
    if first.validator == "required":
        missing = [
            str(e.message).split("'")[1]
            for e in errors
            if e.validator == "required" and "'" in str(e.message) and not list(e.absolute_path)
        ]
        if missing:
            extra["missing_arguments"] = missing[:12]
    envelope = error_envelope(
        "invalid_arguments", f"Input validation error: {first.message}", **extra
    )
    # F-24: required args, an example call and did_you_mean for a near-miss enum value.
    return enrich_error_response(envelope, tool=name, arguments=arguments, registry=registry)


def create_server(
    registry,
    dispatcher,
    *,
    version: str,
    instructions: Optional[str] = None,
    rate_limiter=None,
    resource_manager: Optional[ResourceManager] = None,
    name: str = "MCP-PowerBi-Finvision",
) -> Server:
    """Build the MCP ``Server`` for ``registry`` / ``dispatcher`` (A-24: version set)."""
    rm = resource_manager if resource_manager is not None else get_resource_manager()
    app = Server(
        name,
        version=version,
        instructions=(
            instructions if instructions is not None else build_instructions(registry, version)
        ),
    )
    registry.prebuild()

    @app.list_tools()
    async def list_tools() -> List[types.Tool]:
        # PROF-01: MCP_PBI_PROFILE / MCP_PBI_TOOLS_EXCLUDE hide tools from this session.
        from server import tool_profiles

        return [t for t in registry.get_all_tools_as_mcp() if tool_profiles.is_exposed(t.name)]

    @app.call_tool(validate_input=False)
    async def call_tool(tool_name: str, arguments: Any) -> types.CallToolResult:
        # ONE place for the masking layer (off by default: the result is returned as is): every
        # return path of the inner function passes through it, none can skip it.
        try:
            result = await _call_tool(tool_name, arguments)
        except masking_layer.MaskingUnavailable:
            return masking_layer.unavailable_result(tool_name)
        if not masking_layer.enabled():
            return result
        return await asyncio.to_thread(masking_layer.mask_call_result, tool_name, result)

    async def _call_tool(tool_name: str, arguments: Any) -> types.CallToolResult:
        t0 = time.monotonic()
        from server import tool_profiles

        if registry.has_tool(tool_name) and not tool_profiles.is_exposed(tool_name):
            return _envelope_result(
                tool_name,
                {
                    "success": False,
                    "error_type": "tool_hidden_by_profile",
                    "error": (
                        f"{tool_name} is hidden by MCP_PBI_PROFILE={tool_profiles.active_profile()}"
                        " / MCP_PBI_TOOLS_EXCLUDE; change the profile and restart the server."
                    ),
                },
            )
        try:
            args = dict(arguments) if isinstance(arguments, dict) else {}
            args.pop("_meta", None)
            # Undeclared-in-tools/list args arrive as strings ("true", "5"); coerce
            # them to the FULL schema's types before strict validation.
            try:
                from server.arg_coercion import coerce_arguments

                coerce_arguments(registry.get_full_schema(tool_name), args)
            except Exception as e:  # noqa: BLE001
                logger.debug("arg coercion skipped for %s: %s", tool_name, e)

            # L-11: validate the CANONICAL spelling (a legacy alias must satisfy a
            # required canonical arg). The dispatcher re-normalizes the original
            # args and attaches the deprecated_args notice to the result.
            try:
                canonical_args, _ = normalize_tool_args(tool_name, args)
            except ArgConflict as conflict:
                return _envelope_result(
                    tool_name,
                    error_envelope(
                        "conflicting_arguments",
                        f"{conflict} - pass only the canonical name.",
                        conflicts=conflict.conflicts,
                    ),
                )
            invalid = validate_arguments(registry, tool_name, canonical_args)
            if invalid is not None:
                return _envelope_result(tool_name, invalid)

            if masking_layer.enabled():
                # the model works with pseudonyms; the tool needs the real values
                args = await asyncio.to_thread(masking_layer.unmask_arguments, args)

            if (
                rate_limiter is not None
                and getattr(rate_limiter, "enabled", False)
                and not rate_limiter.allow_request(tool_name)
            ):
                return _envelope_result(
                    tool_name,
                    error_envelope(
                        "rate_limit",
                        "Rate limit exceeded",
                        retry_after=rate_limiter.get_retry_after(tool_name),
                    ),
                )

            result, rendered = await dispatcher.dispatch_and_render_async(tool_name, args)
            if rendered.spilled_path:
                try:
                    rm.register_export(
                        rendered.spilled_path,
                        {"format": "json", "description": f"Full {tool_name} result (spilled)"},
                    )
                except Exception as e:  # noqa: BLE001
                    logger.debug("register_export failed: %s", e)
            logger.debug("Tool %s completed in %.1fms", tool_name, (time.monotonic() - t0) * 1000)
            return _text_result(
                rendered.text, rendered.is_error, inline_images(tool_name, args, result)
            )
        except masking_layer.MaskingUnavailable:
            raise  # the outer call_tool answers it (no data, fixed text)
        except Exception as e:  # noqa: BLE001 - never leak a traceback as a protocol error
            logger.error(f"Error in {tool_name}: {e}", exc_info=True)
            return _envelope_result(
                tool_name,
                normalize_error_envelope(ErrorHandler.handle_unexpected_error(tool_name, e)),
            )

    @app.list_resources()
    async def list_resources() -> List[types.Resource]:
        try:
            listing = rm.list_resources()
            if masking_layer.enabled():
                listing = await asyncio.to_thread(masking_layer.mask_resource_listing, listing)
            return listing
        except Exception as e:  # noqa: BLE001
            logger.error(f"Error listing resources: {e}", exc_info=True)
            return []

    @app.list_resource_templates()
    async def list_resource_templates() -> List[types.ResourceTemplate]:
        templates = rm.list_templates()
        if masking_layer.enabled():
            templates = await asyncio.to_thread(masking_layer.mask_resource_listing, templates)
        return templates

    @app.read_resource()
    async def read_resource(uri):
        try:
            contents = await rm.aread(uri)
            if masking_layer.enabled():
                contents = await asyncio.to_thread(masking_layer.mask_resource_contents, contents)
            return contents
        except Exception as e:
            logger.error(f"Error reading resource {uri}: {e}")
            raise ValueError(f"Failed to read resource {uri}: {e}") from e

    @app.completion()
    async def complete(ref, argument, context) -> Optional[types.Completion]:
        try:
            # model / PBIP reads are blocking: keep them off the event loop
            completion = await asyncio.to_thread(_complete_argument, ref, argument, context)
            if masking_layer.enabled():
                completion = await asyncio.to_thread(masking_layer.mask_completion, completion)
            return completion
        except Exception as e:  # noqa: BLE001 - completion must never error the session
            logger.debug("completion failed: %s", e)
            return None

    @app.list_prompts()
    async def list_prompts() -> List[types.Prompt]:
        return get_prompts()

    @app.get_prompt()
    async def get_prompt(
        name: str, arguments: Optional[Dict[str, str]] = None
    ) -> types.GetPromptResult:
        return get_prompt_messages(name, arguments or {})

    @app.set_logging_level()
    async def set_logging_level(level) -> None:
        from server.log_forward import set_client_level

        set_client_level(str(level))

    return app
