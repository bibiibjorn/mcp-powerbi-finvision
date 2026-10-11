"""Tool description / example quality gates, driven off the LIVE registry.

Claude Code defers this server's tools and finds them by searching NAME +
DESCRIPTION, so the description is a search surface with real selection
consequences. These tests pin the properties that make that surface work and
fail loudly when it regresses:

  * every tool is registered and carries a description above the search floor;
  * descriptions are clean UTF-8 with no mojibake / replacement chars;
  * the five `object_type` x `operation` dispatcher tools carry canonical call
    examples, and EVERY key in EVERY example exists in that tool's own
    inputSchema properties (the anti-drift guard: renaming an arg without
    updating the example fails here instead of shipping a wrong example);
  * the central `confirm_destructive` / `response_format` schema injection still
    reaches every tool (regression guard on that chain);
  * a few specific sibling-tool disambiguations that have been mis-selected in
    practice stay documented.

Everything reads the registry rather than hard-coded literals, so the file keeps
working as tools are added or reworded.
"""

import json

import pytest

from server.registry import HandlerRegistry, TOOL_EXAMPLES
from server.handlers import register_all_handlers

EXPECTED_TOOL_COUNT = 28

# Descriptions shorter than this can't carry the nouns an agent searches for
# plus the sibling-tool boundary that prevents mis-selection.
MIN_DESCRIPTION_CHARS = 120

# The dispatcher tools that must ship canonical call examples.
DISPATCHER_TOOLS = [
    "02_Model_Operations",
    "03_Batch_Operations",
    "07_Visual_Operations",
    "09_Debug_Operations",
    "16_Dependency_Graph",
]

# Classic mojibake: UTF-8 bytes decoded as cp1252/latin-1. If any of these
# appear in a description, an encoding round-trip broke somewhere.
MOJIBAKE_SEQUENCES = ["â", "Ã©", "Â ", "ï¿½"]


@pytest.fixture(scope="module")
def registry():
    """A freshly populated registry (isolated from the global singleton)."""
    reg = HandlerRegistry()
    register_all_handlers(reg)
    return reg


@pytest.fixture(scope="module")
def mcp_tools(registry):
    """The EMITTED MCP tool list — description + inputSchema as a client sees them."""
    return {t.name: t for t in registry.get_all_tools_as_mcp(include_deferred=True)}


# ---------------------------------------------------------------------------
# Inventory + description floor
# ---------------------------------------------------------------------------


def test_all_tools_registered(registry):
    assert registry.total_tools() == EXPECTED_TOOL_COUNT


def test_every_description_meets_search_floor(registry):
    too_short = {
        t.name: len(t.description or "")
        for t in registry.get_all_tools()
        if not t.description or len(t.description) < MIN_DESCRIPTION_CHARS
    }
    assert not too_short, (
        "Descriptions below the search-surface floor of "
        f"{MIN_DESCRIPTION_CHARS} chars: {too_short}. Front-load the concrete "
        "nouns an agent would search for, then the op list, then the boundary "
        "with sibling tools."
    )


def test_descriptions_are_clean_utf8(registry):
    """No mojibake, no U+FFFD, and a clean encode/decode round-trip."""
    for tool in registry.get_all_tools():
        desc = tool.description
        assert "�" not in desc, f"{tool.name}: contains U+FFFD replacement char"
        for seq in MOJIBAKE_SEQUENCES:
            assert seq not in desc, f"{tool.name}: contains mojibake sequence {seq!r}"
        # Must survive a UTF-8 round-trip byte-identically.
        assert desc.encode("utf-8").decode("utf-8") == desc, f"{tool.name}: not clean UTF-8"


def test_documentation_word_first_sentence_is_not_truncated(registry):
    """The old text read as 'Generate or update Word (' when split on '.'."""
    desc = registry.get_tool_def("08_Documentation_Word").description
    first_sentence = desc.split(".")[0].strip()
    assert len(first_sentence) > 30, f"first sentence truncates oddly: {first_sentence!r}"
    assert not first_sentence.endswith("("), f"dangling parenthesis: {first_sentence!r}"


# ---------------------------------------------------------------------------
# Canonical examples on the dispatcher tools
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool_name", DISPATCHER_TOOLS)
def test_dispatcher_tool_has_examples(registry, tool_name):
    examples = registry.get_tool_def(tool_name).resolved_examples()
    assert examples, f"{tool_name}: no canonical examples"
    assert 2 <= len(examples) <= 4, f"{tool_name}: expected 2-4 examples, got {len(examples)}"
    for ex in examples:
        assert isinstance(ex, dict), f"{tool_name}: example is {type(ex).__name__}, not dict"
        assert ex, f"{tool_name}: empty example"


@pytest.mark.parametrize("tool_name", DISPATCHER_TOOLS)
def test_example_keys_exist_in_input_schema(registry, mcp_tools, tool_name):
    """ANTI-DRIFT: every example key must be a declared schema property.

    If an arg is renamed in the handler schema without updating the example,
    this fails loudly — a wrong example is worse than no example.
    """
    props = set((mcp_tools[tool_name].inputSchema or {}).get("properties", {}))
    assert props, f"{tool_name}: emitted inputSchema has no properties"
    for i, ex in enumerate(registry.get_tool_def(tool_name).resolved_examples()):
        unknown = sorted(set(ex) - props)
        assert not unknown, (
            f"{tool_name} example #{i}: keys not in inputSchema.properties: "
            f"{unknown}. Either the example is wrong or the arg was renamed."
        )


@pytest.mark.parametrize("tool_name", DISPATCHER_TOOLS)
def test_examples_are_json_serializable_single_line(registry, tool_name):
    """Examples are emitted as compact one-liners; they must serialize cleanly."""
    for ex in registry.get_tool_def(tool_name).resolved_examples():
        line = json.dumps(ex, separators=(",", ":"), ensure_ascii=True)
        assert "\n" not in line
        assert json.loads(line) == ex


def test_examples_block_emitted_for_exactly_the_dispatcher_tools(mcp_tools):
    with_block = {name for name, t in mcp_tools.items() if "Examples:" in t.description}
    assert with_block == set(DISPATCHER_TOOLS)


def test_examples_emitted_once_not_on_schema_root(mcp_tools):
    """A-30 / L-10: examples live in the description only (the schema-root copy
    duplicated ~2.6K chars per session)."""
    for name, tool in mcp_tools.items():
        assert "examples" not in (tool.inputSchema or {}), f"{name}: schema-root examples"


def test_emitted_description_stays_token_disciplined(mcp_tools, registry):
    """The appended block must not blow up an already-large description."""
    for name in DISPATCHER_TOOLS:
        base = registry.get_tool_def(name).description
        added = len(mcp_tools[name].description) - len(base)
        assert added > 0, f"{name}: no Examples block appended"
        # Trimmed to 2 examples when the full block would breach the ceiling.
        assert added < 1200, f"{name}: Examples block added {added} chars"


def test_tool_examples_map_only_targets_registered_tools(registry):
    unknown = sorted(n for n in TOOL_EXAMPLES if not registry.has_tool(n))
    assert not unknown, f"TOOL_EXAMPLES references unregistered tools: {unknown}"


# ---------------------------------------------------------------------------
# Central schema injection (regression guard on the injection chain)
# ---------------------------------------------------------------------------


def test_confirm_destructive_and_response_format_injected(registry, mcp_tools):
    """response_format/page_size/next_token reach every tool; confirm_destructive
    only the destructive-capable ones (A-30)."""
    missing_format, wrong_confirm = [], []
    for name, tool in mcp_tools.items():
        schema = tool.inputSchema or {}
        if schema.get("type") != "object" or not isinstance(schema.get("properties"), dict):
            continue
        props = schema["properties"]
        for key in ("response_format", "page_size", "next_token"):
            if key not in props:
                missing_format.append((name, key))
        destructive = registry.get_tool_def(name).is_destructive_capable()
        if ("confirm_destructive" in props) is not destructive:
            wrong_confirm.append(name)
    assert not missing_format, f"protocol args not injected: {missing_format}"
    assert not wrong_confirm, f"confirm_destructive injection wrong on: {wrong_confirm}"


def test_injection_does_not_mutate_the_registered_schema(registry, mcp_tools):
    """Injection must be non-mutating (shallow copy), so handlers stay clean."""
    # 02_Model_Operations is destructive-capable but doesn't declare it itself.
    assert (
        "confirm_destructive"
        not in registry.get_tool_def("02_Model_Operations").input_schema["properties"]
    )
    assert "confirm_destructive" in mcp_tools["02_Model_Operations"].inputSchema["properties"]
    assert "response_format" not in registry.get_tool_def("04_Run_DAX").input_schema["properties"]
    assert "response_format" in mcp_tools["04_Run_DAX"].inputSchema["properties"]
    # read-only tools are not offered the break-glass flag
    assert "confirm_destructive" not in mcp_tools["04_Run_DAX"].inputSchema["properties"]


# ---------------------------------------------------------------------------
# Sibling-tool disambiguation (documented, repeatedly-hit traps)
# ---------------------------------------------------------------------------


def test_pbip_operations_points_at_html_and_warns_off_generate_documentation(registry):
    desc = registry.get_tool_def("07_PBIP_Operations").description
    assert "output_format=html" in desc
    assert "generate_documentation" in desc
    # Must explicitly warn the trap, not merely list the op.
    assert "NOT generate_documentation" in desc


def test_debug_operations_is_declared_preferred_over_report_info(registry):
    desc = registry.get_tool_def("09_Debug_Operations").description
    assert "07_Report_Operations" in desc
    assert "PREFER" in desc.upper()


def test_run_dax_disambiguates_from_static_analysis_and_plans(registry):
    desc = registry.get_tool_def("04_Run_DAX").description
    assert "EVALUATE" in desc
    for sibling in ("05_DAX_Intelligence", "13_Query_Plan"):
        assert sibling in desc, f"04_Run_DAX should draw the boundary vs {sibling}"


def test_validate_surfaces_baseline_and_tolerance_vocabulary(registry):
    desc = registry.get_tool_def("09_Validate").description.lower()
    for term in ("baseline", "tolerance", "expected_value", "regression"):
        assert term in desc, f"09_Validate description missing search term {term!r}"


def test_batch_operations_draws_the_line_against_single_object_crud(registry):
    desc = registry.get_tool_def("03_Batch_Operations").description
    assert "02_Model_Operations" in desc
    assert "02_TMDL_Operations" in desc
