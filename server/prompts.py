"""
MCP Prompts — Named workflow templates for Power BI analysis.

Each prompt guides an LLM client through a multi-step tool sequence. Steps are
STRUCTURED data (tool + exact argument dict + purpose), rendered as one-line
JSON, so user input (a DAX expression with quotes, a page name with an
apostrophe) is escaped correctly and every call can be validated against the
live tool schemas (A-08 / L-04). ``tests/test_review_a_prompts_instructions.py``
checks every step of every prompt: the tool exists, the operation is in its
enum, every argument is declared and the call passes the emitted schema.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from mcp.types import (
    Prompt,
    PromptArgument,
    PromptMessage,
    GetPromptResult,
    TextContent,
)


@dataclass
class PromptStep:
    """One step of a prompt workflow. ``tool`` None = a reasoning step."""

    purpose: str
    tool: Optional[str] = None
    args: Dict[str, Any] = field(default_factory=dict)
    optional: bool = False

    def render(self, index: int) -> str:
        head = f"{index}. {self.purpose}"
        if self.tool is None:
            return head
        call = json.dumps(self.args, ensure_ascii=False, separators=(", ", ": "))
        opt = " (skip if already done)" if self.optional else ""
        return f"{head}{opt}\n   {self.tool} {call}"


# ---------------------------------------------------------------------------
# Prompt definitions
# ---------------------------------------------------------------------------

PROMPTS: List[Prompt] = [
    Prompt(
        name="debug-visual",
        description=(
            "Debug a Power BI visual — trace SE/FE timing, analyze filters, " "identify bottlenecks"
        ),
        arguments=[
            PromptArgument(
                name="page_name",
                description="Report page containing the visual",
                required=True,
            ),
            PromptArgument(
                name="visual_name",
                description="Title or ID of the visual to debug (omit to debug all visuals on the page)",
                required=False,
            ),
            PromptArgument(
                name="enable_trace",
                description="Enable SE/FE profiling trace (default: true)",
                required=False,
            ),
            PromptArgument(
                name="pbip_path",
                description="PBIP project path (omit if 09_Debug_Operations set_path was already run)",
                required=False,
            ),
        ],
    ),
    Prompt(
        name="analyze-model",
        description=(
            "Comprehensive model quality analysis — BPA rules, relationships, "
            "unused columns, dependencies"
        ),
        arguments=[
            PromptArgument(
                name="depth",
                description="Analysis depth: fast (BPA only), balanced (BPA + relationships + columns), or deep (full analysis with dependencies)",
                required=False,
            ),
            PromptArgument(
                name="pbip_path",
                description="PBIP project path (enables the offline unused-column scan)",
                required=False,
            ),
        ],
    ),
    Prompt(
        name="create-measure",
        description=(
            "Create a DAX measure with validation — analyze expression, "
            "check best practices, then create"
        ),
        arguments=[
            PromptArgument(
                name="table_name",
                description="Target table for the measure",
                required=True,
            ),
            PromptArgument(
                name="measure_name",
                description="Name of the new measure",
                required=True,
            ),
            PromptArgument(
                name="expression",
                description="DAX expression for the measure",
                required=True,
            ),
            PromptArgument(
                name="format_string",
                description="Optional DAX format string (e.g. '#,0.00', '0.0%')",
                required=False,
            ),
        ],
    ),
    Prompt(
        name="document-report",
        description=(
            "Generate comprehensive report documentation — pages, visuals, " "measures, filters"
        ),
        arguments=[
            PromptArgument(
                name="pbip_path",
                description="Path to the PBIP project folder",
                required=True,
            ),
            PromptArgument(
                name="output_format",
                description="Output format: html or word (default: html)",
                required=False,
            ),
        ],
    ),
    Prompt(
        name="optimize-slow-measure",
        description=(
            "Full performance investigation for a slow measure — trace, "
            "analyze DAX patterns, suggest optimization"
        ),
        arguments=[
            PromptArgument(
                name="measure_name",
                description="Name of the slow measure to investigate",
                required=True,
            ),
            PromptArgument(
                name="page_name",
                description="Report page where the measure is used",
                required=True,
            ),
        ],
    ),
    Prompt(
        name="live-iterate",
        description=(
            "Edit a report visual and verify it live — edit PBIR, reload the running "
            "Desktop in place, screenshot, review, iterate"
        ),
        arguments=[
            PromptArgument(name="pbip_path", description="PBIP project path", required=True),
            PromptArgument(name="page_name", description="Page to work on", required=True),
            PromptArgument(
                name="goal", description="What should change on the page", required=True
            ),
        ],
    ),
    Prompt(
        name="safe-rename",
        description=(
            "Rename a measure without breaking the report — impact check, fail-closed "
            "safe_rename with PBIR cascade, verify"
        ),
        arguments=[
            PromptArgument(name="table_name", description="Table of the measure", required=True),
            PromptArgument(name="measure_name", description="Current measure name", required=True),
            PromptArgument(name="new_name", description="New measure name", required=True),
            PromptArgument(name="pbip_path", description="PBIP project path", required=True),
        ],
    ),
    Prompt(
        name="create-dax-query",
        description=(
            "Draft a DAX query for a question, validate it with the engine (mode=validate) and "
            "return only the DAX - never executed"
        ),
        arguments=[
            PromptArgument(
                name="request",
                description="The question or data request the query must answer",
                required=True,
            ),
        ],
    ),
    Prompt(
        name="run-dax-with-metrics",
        description=(
            "Run a DAX query with the cache bypassed and trace timings, and present every "
            "returned field unchanged"
        ),
        arguments=[
            PromptArgument(name="query", description="DAX query (EVALUATE ...)", required=True),
        ],
    ),
    Prompt(
        name="analyze-dax-query",
        description=(
            "Run a DAX query cold with a trace and review the server metrics for problems "
            "(SE/FE split, memory, queue wait, cache use)"
        ),
        arguments=[
            PromptArgument(name="query", description="DAX query (EVALUATE ...)", required=True),
        ],
    ),
    Prompt(
        name="connect-desktop-by-name",
        description=(
            "Connect to the open Power BI Desktop instance that has a given file open "
            "(by file name or full path)"
        ),
        arguments=[
            PromptArgument(
                name="file_name",
                description="File name (Sales.pbix) or full path of the open report/model",
                required=True,
            ),
        ],
    ),
]

_PROMPT_MAP = {p.name: p for p in PROMPTS}


# ---------------------------------------------------------------------------
# Step builders (one per prompt) — pure data, validated by the test suite
# ---------------------------------------------------------------------------

_CONNECT = PromptStep("Ensure a live connection.", "01_Connection", {"operation": "ensure"})


def _dax_measure_ref(name: str) -> str:
    return "[" + name.replace("]", "]]") + "]"


def _steps_debug_visual(args: Dict[str, str]) -> List[PromptStep]:
    page_name = args.get("page_name") or "<page_name>"
    visual_name = args.get("visual_name") or ""
    trace = (args.get("enable_trace") or "true").strip().lower() != "false"
    pbip_path = args.get("pbip_path") or "<pbip_path>"

    visual_args: Dict[str, Any] = {"operation": "visual", "page_name": page_name}
    if visual_name:
        visual_args["visual_name"] = visual_name
    visual_args["trace"] = trace
    visual_args["include_slicers"] = True
    return [
        _CONNECT,
        PromptStep(
            "Point the debugger at the PBIP project.",
            "09_Debug_Operations",
            {"operation": "set_path", "pbip_path": pbip_path},
            optional=not args.get("pbip_path"),
        ),
        PromptStep(
            "Run the visual's query with filter context (+ trace).",
            "09_Debug_Operations",
            visual_args,
        ),
        PromptStep(
            "Analyze the results: SE vs FE split, SE query count, total duration, filter context."
        ),
        PromptStep(
            "If SE/FE time is dominant, get optimization suggestions (uses the last trace).",
            "09_Debug_Operations",
            {"operation": "optimize", "page_name": page_name},
        ),
        PromptStep(
            "Summarize: visual, total ms, SE ms, FE ms, SE query count, top bottleneck, "
            "recommended fix."
        ),
    ]


def _steps_analyze_model(args: Dict[str, str]) -> List[PromptStep]:
    depth = (args.get("depth") or "balanced").strip().lower()
    if depth not in ("fast", "balanced", "deep"):
        depth = "balanced"
    pbip_path = args.get("pbip_path") or ""

    steps = [
        _CONNECT,
        PromptStep(
            "Run the Best Practice Analyzer.",
            "06_Analysis_Operations",
            {"operation": "full", "scope": "best_practices", "depth": depth},
        ),
    ]
    if depth in ("balanced", "deep"):
        steps.append(
            PromptStep(
                "Review relationship topology.",
                "06_Analysis_Operations",
                {"operation": "simple", "mode": "relationships"},
            )
        )
        if pbip_path:
            steps.append(
                PromptStep(
                    "Find unused columns (offline PBIP scan — no live false positives).",
                    "05_Column_Usage_Mapping",
                    {"operation": "get_unused_columns_pbip", "pbip_path": pbip_path},
                )
            )
        else:
            steps.append(
                PromptStep(
                    "Find unused columns (live; verify candidates, the live scan has false "
                    "positives — prefer get_unused_columns_pbip with a pbip_path).",
                    "05_Column_Usage_Mapping",
                    {"operation": "get_unused_columns"},
                )
            )
    if depth == "deep":
        steps.append(
            PromptStep(
                "Full analysis incl. performance + integrity.",
                "06_Analysis_Operations",
                {"operation": "full", "scope": "all", "depth": "deep"},
            )
        )
        dep_args: Dict[str, Any] = {"operation": "build", "scope": "both" if pbip_path else "model"}
        if pbip_path:
            dep_args["pbip_path"] = pbip_path
        steps.append(PromptStep("Build the dependency graph.", "16_Dependency_Graph", dep_args))
        steps.append(
            PromptStep(
                "Find dependency cycles.", "16_Dependency_Graph", {"operation": "find_cycles"}
            )
        )
    steps.append(
        PromptStep(
            "Summarize findings grouped by severity (critical / warning / info) with remediation."
        )
    )
    return steps


def _steps_create_measure(args: Dict[str, str]) -> List[PromptStep]:
    table_name = args.get("table_name") or "<table_name>"
    measure_name = args.get("measure_name") or "<measure_name>"
    expression = args.get("expression") or "<expression>"
    format_string = args.get("format_string") or ""

    create_args: Dict[str, Any] = {
        "object_type": "measure",
        "operation": "create",
        "table_name": table_name,
        "measure_name": measure_name,
        "expression": expression,
    }
    if format_string:
        create_args["format_string"] = format_string
    return [
        _CONNECT,
        PromptStep(
            "Analyze the expression for anti-patterns and errors.",
            "05_DAX_Intelligence",
            {"expression": expression, "analysis_mode": "analyze"},
        ),
        PromptStep(
            "If critical issues are found, propose a corrected expression and confirm with the "
            "user before continuing."
        ),
        PromptStep(
            "Create the measure (live TOM — no reload needed).", "02_Model_Operations", create_args
        ),
        PromptStep(
            "Verify it exists.",
            "02_Model_Operations",
            {
                "object_type": "measure",
                "operation": "get",
                "table_name": table_name,
                "measure_name": measure_name,
            },
        ),
        PromptStep(
            "Evaluate it with real data.",
            "04_Run_DAX",
            {"query": f'EVALUATE ROW("Value", {_dax_measure_ref(measure_name)})'},
        ),
        PromptStep("Report: measure created, validation warnings, final expression, value."),
    ]


def _steps_document_report(args: Dict[str, str]) -> List[PromptStep]:
    pbip_path = args.get("pbip_path") or "<pbip_path>"
    output_format = (args.get("output_format") or "html").strip().lower()
    if output_format not in ("html", "word"):
        output_format = "html"

    steps = [
        PromptStep(
            "Point the debugger/documenter at the project.",
            "09_Debug_Operations",
            {"operation": "set_path", "pbip_path": pbip_path},
        ),
        PromptStep(
            "Generate the HTML model/report analysis (NOT generate_documentation).",
            "07_PBIP_Operations",
            {"operation": "analyze", "pbip_path": pbip_path, "output_format": "html"},
        ),
        PromptStep(
            "Document the report pages, visuals and filters.",
            "09_Document",
            {"operation": "report"},
        ),
        PromptStep(
            "Map measure-to-visual dependencies.",
            "16_Dependency_Graph",
            {"operation": "build", "scope": "both", "pbip_path": pbip_path},
        ),
    ]
    if output_format == "word":
        steps.insert(0, _CONNECT)
        steps.append(
            PromptStep("Generate the Word document.", "08_Documentation_Word", {"mode": "generate"})
        )
    steps.append(
        PromptStep("Report: output file path(s), page count, summary of the report structure.")
    )
    return steps


def _steps_optimize_slow_measure(args: Dict[str, str]) -> List[PromptStep]:
    measure_name = args.get("measure_name") or "<measure_name>"
    page_name = args.get("page_name") or "<page_name>"
    return [
        _CONNECT,
        PromptStep(
            "Inspect the measure's DAX for anti-patterns (nested CALCULATE, row-by-row "
            "iteration, FILTER over tables).",
            "05_DAX_Intelligence",
            {"expression": measure_name, "analysis_mode": "all"},
        ),
        PromptStep(
            "Point the debugger at the PBIP project if not already set.",
            "09_Debug_Operations",
            {"operation": "status"},
        ),
        PromptStep(
            "Trace the visuals on the page that use the measure.",
            "09_Debug_Operations",
            {
                "operation": "visual",
                "page_name": page_name,
                "measures": [measure_name],
                "trace": True,
            },
        ),
        PromptStep(
            "Capture the query plan of the slowest visual query (paste its DAX).",
            "13_Query_Plan",
            {
                "operation": "capture",
                "query": f'EVALUATE ROW("v", {_dax_measure_ref(measure_name)})',
            },
        ),
        PromptStep(
            "Get optimization suggestions (uses the last trace).",
            "09_Debug_Operations",
            {"operation": "optimize", "measure_name": measure_name},
        ),
        PromptStep(
            "Summarize: DAX pattern issues, SE vs FE breakdown, recommended rewrite "
            "(before/after), expected improvement. Prove equivalence with 09_Debug_Operations "
            "compare equivalence_check=true before applying."
        ),
    ]


def _steps_live_iterate(args: Dict[str, str]) -> List[PromptStep]:
    pbip_path = args.get("pbip_path") or "<pbip_path>"
    page_name = args.get("page_name") or "<page_name>"
    goal = args.get("goal") or "<goal>"
    return [
        PromptStep(
            "Check Desktop state. If hasUnsavedChanges is true, SAVE first — a reload discards "
            "unsaved in-memory edits.",
            "17_Desktop_Bridge",
            {"operation": "status"},
        ),
        PromptStep(
            "List the page's visuals (ids, types, positions).",
            "07_Visual_Operations",
            {
                "operation": "list",
                "pbip_path": pbip_path,
                "page_name": page_name,
                "summary_only": True,
            },
        ),
        PromptStep(
            f"Make the PBIR edit(s) for the goal: {goal}. Target visuals by exact visual_name "
            "(the PBIR id) — never leave the selector empty."
        ),
        PromptStep(
            "Reload the running Desktop in place and capture the page.",
            "17_Desktop_Bridge",
            {"operation": "reload_and_screenshot", "pbip_path": pbip_path, "page_name": page_name},
        ),
        PromptStep("Read the screenshot carefully; iterate from step 3 until the goal is met."),
    ]


def _steps_safe_rename(args: Dict[str, str]) -> List[PromptStep]:
    table_name = args.get("table_name") or "<table_name>"
    measure_name = args.get("measure_name") or "<measure_name>"
    new_name = args.get("new_name") or "<new_name>"
    pbip_path = args.get("pbip_path") or "<pbip_path>"
    return [
        _CONNECT,
        PromptStep(
            "Build the model + report dependency graph.",
            "16_Dependency_Graph",
            {"operation": "build", "scope": "both", "pbip_path": pbip_path},
        ),
        PromptStep(
            "Check the rename impact (collisions, report bindings).",
            "16_Dependency_Graph",
            {
                "operation": "get_impact",
                "node_ref": f"{table_name}[{measure_name}]",
                "change": "rename",
                "new_name": new_name,
            },
        ),
        PromptStep(
            "Rename fail-closed with the PBIR cascade.",
            "02_Model_Operations",
            {
                "object_type": "measure",
                "operation": "safe_rename",
                "table_name": table_name,
                "measure_name": measure_name,
                "new_name": new_name,
                "pbip_path": pbip_path,
            },
        ),
        PromptStep(
            "Verify the renamed measure evaluates.",
            "04_Run_DAX",
            {"query": f'EVALUATE ROW("Value", {_dax_measure_ref(new_name)})'},
        ),
    ]


def _steps_create_dax_query(args: Dict[str, str]) -> List[PromptStep]:
    return [
        _CONNECT,
        PromptStep(
            "Read the query rules before writing anything: resource powerbi://docs/dax-query-rules."
        ),
        PromptStep(
            "List the tables so every name you use is real.",
            "02_Model_Operations",
            {"object_type": "table", "operation": "list"},
        ),
        PromptStep(
            "List the measures (use existing measures instead of re-deriving them).",
            "02_Model_Operations",
            {"object_type": "measure", "operation": "list"},
            optional=True,
        ),
        PromptStep(
            "Draft the DAX query (DEFINE ... EVALUATE ...) for the request. Put the full "
            "text in the query argument of the next call."
        ),
        PromptStep(
            "Have the engine parse and bind it WITHOUT executing it. Repeat with a corrected "
            "query until valid is true.",
            "04_Run_DAX",
            {"query": "<the DAX query you drafted>", "mode": "validate"},
        ),
        PromptStep(
            "Reply with ONLY the validated DAX in one dax code block. Do not execute it, do "
            "not add commentary or results."
        ),
    ]


def _steps_run_dax_with_metrics(args: Dict[str, str]) -> List[PromptStep]:
    query = args.get("query") or "<query>"
    return [
        _CONNECT,
        PromptStep(
            "Run the query cold: cache bypassed, engine cache cleared, trace on.",
            "04_Run_DAX",
            {"query": query, "mode": "trace", "bypass_cache": True},
        ),
        PromptStep(
            "Present every field of the result exactly as returned - rows, timings, "
            "execution_metrics, trace_id. Do not summarise, round, rename or drop fields. "
            "Put the raw metrics (execution_metrics and the timing fields) in ONE json code "
            "block."
        ),
    ]


def _steps_analyze_dax_query(args: Dict[str, str]) -> List[PromptStep]:
    query = args.get("query") or "<query>"
    return [
        _CONNECT,
        PromptStep(
            "Run the query cold with a trace so the numbers are not cache artefacts.",
            "04_Run_DAX",
            {"query": query, "mode": "trace", "bypass_cache": True},
        ),
        PromptStep(
            "Review the metrics for problems: FE time much larger than SE time (row-by-row "
            "work), many SE queries or large SE result sizes, high peak memory, queue wait, "
            "duplicated SE queries. Quote the numbers you rely on."
        ),
        PromptStep(
            "Report each finding with the evidence and a concrete rewrite. If you suggest "
            "changed DAX, validate it first with 04_Run_DAX mode=validate and compare results "
            "before recommending it."
        ),
    ]


def _steps_connect_desktop_by_name(args: Dict[str, str]) -> List[PromptStep]:
    file_name = args.get("file_name") or "<file_name>"
    return [
        PromptStep(
            "List the running Desktop instances with the files they have open.",
            "01_Connection",
            {"operation": "detect"},
        ),
        PromptStep(
            f"Pick the instance whose file_full_path ends with {json.dumps(file_name)} "
            "(ask the user if several or none match)."
        ),
        PromptStep(
            "Connect to exactly that instance (idempotent).",
            "01_Connection",
            {"operation": "ensure", "file_path": "<file_full_path of the chosen instance>"},
        ),
        PromptStep(
            "Confirm the `database` field of the result belongs to the requested file before "
            "doing anything else."
        ),
    ]


_STEP_BUILDERS: Dict[str, Callable[[Dict[str, str]], List[PromptStep]]] = {
    "debug-visual": _steps_debug_visual,
    "analyze-model": _steps_analyze_model,
    "create-measure": _steps_create_measure,
    "document-report": _steps_document_report,
    "optimize-slow-measure": _steps_optimize_slow_measure,
    "live-iterate": _steps_live_iterate,
    "safe-rename": _steps_safe_rename,
    "create-dax-query": _steps_create_dax_query,
    "run-dax-with-metrics": _steps_run_dax_with_metrics,
    "analyze-dax-query": _steps_analyze_dax_query,
    "connect-desktop-by-name": _steps_connect_desktop_by_name,
}

_SYSTEM_TEXT = {
    "debug-visual": (
        "You are a Power BI performance engineer. Follow the steps below using the "
        "powerbi-finvision MCP tools; each tool call is given as `<tool> <JSON arguments>` — "
        "pass the JSON verbatim. Report timings, filter context and actionable recommendations."
    ),
    "analyze-model": (
        "You are a Power BI model quality analyst. Follow the steps below; each tool call is "
        "given as `<tool> <JSON arguments>`. Present findings grouped by severity with "
        "clear remediation steps."
    ),
    "create-measure": (
        "You are a DAX expert creating a validated measure. Follow the steps below; each tool "
        "call is given as `<tool> <JSON arguments>`. Report any issues found during validation."
    ),
    "document-report": (
        "You are a Power BI documentation specialist. Follow the steps below; each tool call is "
        "given as `<tool> <JSON arguments>`. Organize the output with a section per page."
    ),
    "optimize-slow-measure": (
        "You are a Power BI performance engineer. Investigate the slow measure with the steps "
        "below; each tool call is given as `<tool> <JSON arguments>`. Give concrete, "
        "equivalence-checked before/after DAX."
    ),
    "live-iterate": (
        "You are editing a Power BI report with Desktop kept open. Model edits go live via "
        "02_Model_Operations (verify with 04_Run_DAX, never reload); report edits need the "
        "bridge reload. Each tool call is given as `<tool> <JSON arguments>`."
    ),
    "safe-rename": (
        "You are renaming a measure without breaking report bindings. Stop if the impact check "
        "reports a collision or unsafe breakage. Each tool call is given as "
        "`<tool> <JSON arguments>`."
    ),
    "create-dax-query": (
        "You write DAX queries against the live model. Follow the steps below; each tool call "
        "is given as `<tool> <JSON arguments>`. Validate, never execute, and return only the DAX."
    ),
    "run-dax-with-metrics": (
        "You run a DAX query and report its server metrics faithfully. Each tool call is given "
        "as `<tool> <JSON arguments>`; pass the JSON verbatim."
    ),
    "analyze-dax-query": (
        "You are a DAX performance analyst. Each tool call is given as `<tool> <JSON "
        "arguments>`; base every finding on the returned metrics."
    ),
    "connect-desktop-by-name": (
        "You connect to a specific open Power BI Desktop instance. Each tool call is given as "
        "`<tool> <JSON arguments>`."
    ),
}

_INTRO = {
    "debug-visual": lambda a: "Debug {} on page {}.".format(
        f"visual {json.dumps(a['visual_name'])}" if a.get("visual_name") else "all visuals",
        json.dumps(a.get("page_name") or "<page_name>"),
    ),
    "analyze-model": lambda a: f"Perform a {(a.get('depth') or 'balanced')}-depth model quality analysis.",
    "create-measure": lambda a: (
        f"Create measure {json.dumps(a.get('measure_name') or '<measure_name>')} in table "
        f"{json.dumps(a.get('table_name') or '<table_name>')} with expression:\n```dax\n"
        f"{a.get('expression') or '<expression>'}\n```"
    ),
    "document-report": lambda a: (
        f"Generate {(a.get('output_format') or 'html').upper()} documentation for the PBIP "
        f"project at {json.dumps(a.get('pbip_path') or '<pbip_path>')}."
    ),
    "optimize-slow-measure": lambda a: (
        f"Investigate slow measure {json.dumps(a.get('measure_name') or '<measure_name>')} on "
        f"page {json.dumps(a.get('page_name') or '<page_name>')}."
    ),
    "live-iterate": lambda a: f"Goal: {a.get('goal') or '<goal>'}",
    "safe-rename": lambda a: (
        f"Rename {json.dumps(a.get('measure_name') or '<measure_name>')} to "
        f"{json.dumps(a.get('new_name') or '<new_name>')}."
    ),
    "create-dax-query": lambda a: (
        f"Write a DAX query for this request: {a.get('request') or '<request>'}\n\n"
        "Validate the syntax but do NOT execute it. Return only the DAX query code."
    ),
    "run-dax-with-metrics": lambda a: (
        f"Run this DAX query and show its metrics:\n```dax\n{a.get('query') or '<query>'}\n```"
    ),
    "analyze-dax-query": lambda a: (
        f"Analyze the performance of this DAX query:\n```dax\n{a.get('query') or '<query>'}\n```"
    ),
    "connect-desktop-by-name": lambda a: (
        "Connect to the Power BI Desktop instance that has "
        f"{json.dumps(a.get('file_name') or '<file_name>')} open."
    ),
}


def get_prompt_steps(name: str, arguments: Optional[Dict[str, str]] = None) -> List[PromptStep]:
    """The structured steps of prompt ``name`` (used by the anti-drift test)."""
    builder = _STEP_BUILDERS.get(name)
    if builder is None:
        raise ValueError(f"Unknown prompt: {name}")
    return builder(dict(arguments or {}))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_prompts() -> list[Prompt]:
    """Return all registered prompts for list_prompts."""
    return PROMPTS


def get_prompt_messages(name: str, arguments: dict[str, str] | None = None) -> GetPromptResult:
    """Build and return prompt messages for get_prompt.

    Raises ValueError if the prompt name is not found.
    """
    args = dict(arguments or {})
    steps = get_prompt_steps(name, args)
    body = "\n".join(step.render(i) for i, step in enumerate(steps, start=1))
    user_text = f"{_INTRO[name](args)}\n\nSteps:\n{body}"
    messages = [
        PromptMessage(role="user", content=TextContent(type="text", text=_SYSTEM_TEXT[name])),
        PromptMessage(role="user", content=TextContent(type="text", text=user_text)),
    ]
    return GetPromptResult(description=_PROMPT_MAP[name].description, messages=messages)
