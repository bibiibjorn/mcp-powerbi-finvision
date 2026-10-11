"""L-11 / A-32: canonical argument vocabulary + the dispatcher-level alias layer.

Before: the same concept had several spellings (``query`` / ``dax_query`` /
``test_query``; ``page_name`` / ``page_id`` / ``page``; ...), every one of them
declared in some schema, and the user's skills used the "wrong" one for a tool
(``04_Run_DAX dax_query`` failed input validation: ``query`` is required).

Now each schema declares the canonical name only; ``server/arg_aliases.py``
folds every legacy spelling into it BEFORE input validation and the result
carries ``deprecated_args``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from server.arg_aliases import (
    COMMON_ALIASES,
    IGNORED_LEGACY_ARGS,
    TOOL_ALIASES,
    ArgConflict,
    normalize_tool_args,
)
from server.handlers import register_all_handlers
from server.registry import HandlerRegistry


@pytest.fixture(scope="module")
def registry():
    reg = HandlerRegistry()
    register_all_handlers(reg)
    return reg


_ALIAS_ROWS = [
    (tool, alias, canon) for tool, m in TOOL_ALIASES.items() for alias, canon in m.items()
]


# ---------------------------------------------------------------------------
# The table itself
# ---------------------------------------------------------------------------


def test_alias_table_targets_registered_tools(registry):
    names = {t.name for t in registry.get_all_tools()}
    assert set(TOOL_ALIASES) <= names
    assert set(IGNORED_LEGACY_ARGS) <= names


@pytest.mark.parametrize("tool,alias,canonical", _ALIAS_ROWS)
def test_canonical_is_declared_and_alias_is_not(registry, tool, alias, canonical):
    """One spelling per concept in the schema: the canonical one."""
    props = registry.declared_properties(tool)
    assert canonical in props, f"{tool}: canonical {canonical!r} not declared"
    assert alias not in props, f"{tool}: legacy {alias!r} still declared next to {canonical!r}"


@pytest.mark.parametrize("tool,alias,canonical", _ALIAS_ROWS)
def test_every_alias_maps_with_a_notice(tool, alias, canonical):
    args, notes = normalize_tool_args(tool, {alias: "v"})
    assert args == {canonical: "v"}
    assert notes["deprecated_args"][alias] == f"renamed to {canonical}"


@pytest.mark.parametrize("tool,alias,canonical", _ALIAS_ROWS)
def test_alias_equal_to_canonical_is_fine_and_conflict_raises(tool, alias, canonical):
    args, _ = normalize_tool_args(tool, {alias: "v", canonical: "v"})
    assert args == {canonical: "v"}
    with pytest.raises(ArgConflict) as exc:
        normalize_tool_args(tool, {alias: "a", canonical: "b"})
    assert exc.value.conflicts == {alias: canonical}


def test_normalization_is_idempotent_and_does_not_mutate_the_input():
    raw = {"operation": "capture", "dax_query": "EVALUATE {1}"}
    once, notes = normalize_tool_args("13_Query_Plan", raw)
    twice, notes2 = normalize_tool_args("13_Query_Plan", once)
    assert raw == {"operation": "capture", "dax_query": "EVALUATE {1}"}
    assert once == twice == {"operation": "capture", "query": "EVALUATE {1}"}
    assert notes and not notes2


def test_legacy_confirmed_is_coerced_and_never_overrides_the_explicit_flag():
    args, notes = normalize_tool_args("02_Model_Operations", {"confirmed": "true"})
    assert args == {"confirm_destructive": True} and "confirmed" in notes["deprecated_args"]
    args, _ = normalize_tool_args(
        "02_Model_Operations", {"confirmed": True, "confirm_destructive": False}
    )
    assert args == {"confirm_destructive": False}
    assert COMMON_ALIASES == {"confirmed": "confirm_destructive"}


@pytest.mark.parametrize("tool,arg", [(t, a) for t, m in IGNORED_LEGACY_ARGS.items() for a in m])
def test_ignored_legacy_args_are_dropped_and_explained(registry, tool, arg):
    args, notes = normalize_tool_args(tool, {arg: True, "operation": "x"})
    assert arg not in args and arg in notes["ignored_legacy_args"]
    assert arg not in registry.declared_properties(tool)


# ---------------------------------------------------------------------------
# Shape transforms for the call forms the skills document
# ---------------------------------------------------------------------------


def test_dependency_graph_object_form_becomes_quick_lineage():
    """pbi-lineage skill: 16_Dependency_Graph with object_type/object_name/
    table_name/direction/include_report_layer and NO operation."""
    args, notes = normalize_tool_args(
        "16_Dependency_Graph",
        {
            "object_type": "measure",
            "object_name": "Total Revenue",
            "table_name": "m Measures",
            "direction": "downstream",
            "include_report_layer": True,
        },
    )
    assert args == {
        "operation": "quick_lineage",
        "node_ref": "m Measures[Total Revenue]",
        # S-32: the direction words say what they return (downstream = dependencies)
        "direction": "dependencies",
        "scope": "both",
    }
    assert "call_form" in notes["deprecated_args"]
    assert "downstream -> dependencies" in notes["deprecated_args"]["call_form"]
    model_only, _ = normalize_tool_args(
        "16_Dependency_Graph",
        {"object_type": "table", "object_name": "Sales", "include_report_layer": False},
    )
    assert model_only["node_ref"] == "Sales" and model_only["scope"] == "model"


def test_authoring_page_path_becomes_page_name_and_pbip_path():
    args, _ = normalize_tool_args(
        "11_PBIP_Authoring",
        {"operation": "pbir_add_visuals", "page_path": "C:/r/X.Report/definition/pages/abc123"},
    )
    assert args["page_name"] == "abc123" and args["pbip_path"] == "C:/r/X.Report"


# ---------------------------------------------------------------------------
# End to end: dispatcher + protocol layer
# ---------------------------------------------------------------------------


def test_dispatcher_hands_the_canonical_name_to_the_handler(monkeypatch):
    from server.dispatch import ToolDispatcher
    from server.registry import ToolDefinition

    reg = HandlerRegistry()
    seen = []
    reg.register(
        ToolDefinition(
            name="13_Query_Plan",
            description="x" * 130,
            handler=lambda a: seen.append(a) or {"success": True},
            input_schema={
                "type": "object",
                "properties": {"operation": {"type": "string"}, "query": {"type": "string"}},
                "required": ["operation"],
            },
            annotations={"readOnlyHint": True, "destructiveHint": False},
        )
    )
    d = ToolDispatcher()
    d.registry = reg
    out = d.dispatch("13_Query_Plan", {"operation": "capture", "dax_query": "EVALUATE {1}"})
    assert seen == [{"operation": "capture", "query": "EVALUATE {1}"}]
    assert out["deprecated_args"] == {"dax_query": "renamed to query"}
    assert "unknown_args" not in out

    bad = d.dispatch("13_Query_Plan", {"operation": "capture", "dax_query": "a", "query": "b"})
    assert bad["success"] is False and bad["error_type"] == "conflicting_arguments"
    assert len(seen) == 1  # the handler never ran


def test_legacy_name_satisfies_a_required_canonical_arg_over_the_protocol():
    """dax/SKILL.md: `04_Run_DAX dax_query` used to fail input validation."""
    from tests._mcp_harness import make_app, make_dispatcher, run_session, tool

    d = make_dispatcher(
        tool(
            "04_Run_DAX",
            lambda a: {"success": True, "echo": a},
            properties={"query": {"type": "string"}},
            required=["query"],
        )
    )
    app, _ = make_app(d)

    async def body(client):
        return await client.call_tool("04_Run_DAX", {"dax_query": "EVALUATE {1}"})

    res = run_session(app, body)
    out = json.loads(res.content[0].text)
    assert res.isError is False, out
    assert out["echo"] == {"query": "EVALUATE {1}"}
    assert out["deprecated_args"] == {"dax_query": "renamed to query"}


# ---------------------------------------------------------------------------
# The user's skills keep working
# ---------------------------------------------------------------------------

#: Argument names the user's skills (~/.claude/skills/**/*.md) pass per tool,
#: snapshot 2026-09-23 (scan: JSON-ish calls, `key=value` prose, YAML-ish blocks).
#: Item-level keys (03 items), JSON-literal keys (`expr`) and prose false
#: positives (`ensure_ascii`, `key`, `ignore`) are excluded.
SKILL_ARGS = {
    "01_Connection": ["file_path", "model_index", "operation"],
    "02_Model_Operations": [
        "confirm_destructive",
        "object_type",
        "operation",
        "refresh_type",
        "table_name",
        "tables",
    ],
    "02_QA_Linguistic": ["entity", "operation", "synonym"],
    "03_Batch_Operations": ["batch_operation", "operation", "items", "object_type"],
    "04_Run_DAX": [
        "query",
        "dax_query",
        "mode",
        "clear_cache",
        "return_execution_metrics",
        "roles",
    ],
    "05_DAX_Intelligence": ["analysis_mode"],
    "06_Analysis_Operations": ["include_bpa", "operation"],
    "07_Bookmark_Operations": ["bookmark_id", "operation", "name", "recapture"],
    "07_PBIP_Operations": ["auto_open", "operation", "output_format"],
    "07_Visual_Operations": ["operation", "page_name", "property_path"],
    "08_Documentation_Word": ["output_format"],
    "09_Debug_Operations": ["clear_cache", "operation", "page_name", "pbip_path", "roles", "trace"],
    "09_Document": ["output_format"],
    "09_Profile": ["trace"],
    "11_PBIP_Authoring": ["page_path", "report_path", "visuals", "container_name"],
    "13_Query_Plan": ["dax_query", "plan_type", "clear_cache", "operation"],
    "16_Dependency_Graph": [
        "object_type",
        "object_name",
        "table_name",
        "direction",
        "include_report_layer",
    ],
    "17_Desktop_Bridge": ["operation", "output", "page", "pbip_path"],
}


def _rejected(registry, tool, names):
    """Names of one call that are neither declared, aliased, ignored nor consumed
    by a shape transform once the whole call is normalized."""
    try:
        args, _ = normalize_tool_args(tool, {n: "x" for n in names})
    except ArgConflict:
        return []
    declared = registry.declared_properties(tool)
    return sorted(n for n in args if n not in declared)


@pytest.mark.parametrize("tool", sorted(SKILL_ARGS))
def test_every_skill_arg_still_works(registry, tool):
    missing = _rejected(registry, tool, SKILL_ARGS[tool])
    assert not missing, f"{tool}: skill args no longer accepted: {missing}"


_SKILLS = Path(os.path.expanduser("~")) / ".claude" / "skills"


@pytest.mark.skipif(not _SKILLS.exists(), reason="no ~/.claude/skills on this machine")
def test_live_skill_yaml_blocks_use_accepted_args(registry):
    """Re-scan the skills' YAML-ish call blocks (``<tool>`` line + indented
    ``key: value`` lines) so a NEW skill arg is caught too."""
    import re

    tools = sorted((t.name for t in registry.get_all_tools()), key=len, reverse=True)
    head = re.compile(r"^(" + "|".join(map(re.escape, tools)) + r")(?:[:\s]+\w+)?\s*$")
    problems = []
    for md in _SKILLS.rglob("*.md"):
        lines = md.read_text(encoding="utf-8", errors="replace").split("\n")
        for i, line in enumerate(lines):
            m = head.match(line.strip().strip("`").strip())
            if not m:
                continue
            tool = m.group(1)
            names = []
            for nxt in lines[i + 1 : i + 25]:
                km = re.match(r"^\s+([a-z_][a-z0-9_]*)\s*:", nxt)
                if not km:
                    break
                names.append(km.group(1))
            problems += [(md.name, tool, n) for n in _rejected(registry, tool, names)]
    assert not problems, problems
