"""M4: protocol niceties (TS-01 titles), the new prompts and the DAX-query-rules resource
(PRM-01), and the lane-split sentence in the session instructions."""

from __future__ import annotations

import json

import pytest

from server.handlers import register_all_handlers
from server.prompts import PROMPTS, get_prompt_messages, get_prompt_steps
from server.registry import TOOL_TITLES, HandlerRegistry


@pytest.fixture(scope="module")
def registry():
    reg = HandlerRegistry()
    register_all_handlers(reg)
    return reg


# --- TS-01 -------------------------------------------------------------------------------------


def test_all_28_tools_carry_a_top_level_title(registry):
    tools = registry.get_all_tools_as_mcp()
    assert len(tools) == 28
    missing = [t.name for t in tools if not t.title]
    assert missing == []
    for t in tools:
        assert t.title == t.annotations.title == TOOL_TITLES[t.name]


def test_title_survives_serialization_into_tools_list(registry):
    payload = [
        t.model_dump(by_alias=True, exclude_none=True, mode="json")
        for t in registry.get_all_tools_as_mcp()
    ]
    assert all(isinstance(p.get("title"), str) and p["title"] for p in payload)
    assert len({p["title"] for p in payload}) == len(payload)  # titles are distinguishable


def test_title_and_new_resource_and_prompts_reach_a_client_over_the_real_protocol(registry):
    from server.mcp_app import seed_resources
    from server.resources import ResourceManager
    from tests._mcp_harness import run_session

    rm = ResourceManager()
    seed_resources(rm)
    from server.dispatch import ToolDispatcher
    from tests._mcp_harness import make_app

    d = ToolDispatcher()
    d.registry = registry
    app, _ = make_app(d, resource_manager=rm)

    async def body(client):
        tools = await client.list_tools()
        prompts = await client.list_prompts()
        rules = await client.read_resource("powerbi://docs/dax-query-rules")
        got = await client.get_prompt("create-dax-query", {"request": "sales by month"})
        return tools, prompts, rules, got

    tools, prompts, rules, got = run_session(app, body)
    assert len(tools.tools) == 28 and all(t.title for t in tools.tools)
    assert {"create-dax-query", "connect-desktop-by-name"} <= {p.name for p in prompts.prompts}
    assert rules.contents[0].text.startswith("# DAX query rules")
    assert rules.contents[0].mimeType == "text/markdown"
    assert "mode" in got.messages[1].content.text


# --- prompts --------------------------------------------------------------------------------------

NEW = ["create-dax-query", "run-dax-with-metrics", "analyze-dax-query", "connect-desktop-by-name"]


def test_the_four_new_prompts_are_registered():
    names = [p.name for p in PROMPTS]
    for n in NEW:
        assert n in names
    assert len(names) == 11 and len(set(names)) == 11


def _text(name, **args):
    res = get_prompt_messages(name, args)
    return "\n".join(m.content.text for m in res.messages)


def test_create_dax_query_validates_and_never_executes():
    steps = get_prompt_steps("create-dax-query", {"request": "sales by month"})
    calls = [(s.tool, s.args) for s in steps if s.tool]
    run_dax = [a for t, a in calls if t == "04_Run_DAX"]
    assert run_dax and all(a["mode"] == "validate" for a in run_dax)
    text = _text("create-dax-query", request="sales by month")
    assert "sales by month" in text and "do NOT execute" in text
    assert "powerbi://docs/dax-query-rules" in text
    assert "ONLY the validated DAX" in text


def test_run_dax_with_metrics_bypasses_the_cache_and_traces():
    q = 'EVALUATE ROW("v", [Total])'
    steps = get_prompt_steps("run-dax-with-metrics", {"query": q})
    call = next(s for s in steps if s.tool == "04_Run_DAX")
    assert call.args == {"query": q, "mode": "trace", "bypass_cache": True}
    text = _text("run-dax-with-metrics", query=q)
    assert "exactly as returned" in text and "ONE json code block" in text
    assert (
        json.dumps({"query": q, "mode": "trace", "bypass_cache": True}, separators=(", ", ": "))
        in text
    )


def test_analyze_dax_query_traces_cold_then_reviews_the_metrics():
    steps = get_prompt_steps("analyze-dax-query", {"query": "EVALUATE 'T'"})
    call = next(s for s in steps if s.tool == "04_Run_DAX")
    assert call.args["mode"] == "trace" and call.args["bypass_cache"] is True
    assert any("peak memory" in s.purpose and "queue wait" in s.purpose for s in steps)


def test_connect_desktop_by_name_lists_then_ensures_with_file_path():
    steps = get_prompt_steps("connect-desktop-by-name", {"file_name": "Sales.pbix"})
    calls = [(s.tool, s.args) for s in steps if s.tool]
    assert calls[0] == ("01_Connection", {"operation": "detect"})
    assert calls[1][0] == "01_Connection"
    assert calls[1][1]["operation"] == "ensure" and "file_path" in calls[1][1]
    assert '"Sales.pbix"' in _text("connect-desktop-by-name", file_name="Sales.pbix")


def test_every_new_prompt_renders_without_arguments_too():
    for n in NEW:
        assert get_prompt_messages(n, {}).messages


# --- PRM-01 resource -----------------------------------------------------------------------------------


def test_dax_query_rules_resource_is_listed_and_readable():
    from server.mcp_app import seed_resources
    from server.resources import ResourceManager

    rm = ResourceManager()
    seed_resources(rm)
    listed = {str(r.uri): r for r in rm.list_resources()}
    assert "powerbi://docs/dax-query-rules" in listed
    assert listed["powerbi://docs/dax-query-rules"].mimeType == "text/markdown"
    body = rm.read_resource("powerbi://docs/dax-query-rules")
    assert body.startswith("# DAX query rules")


def test_dax_query_rules_cover_the_required_rules_in_about_40_lines():
    from server.dax_query_rules import DAX_QUERY_RULES as rules

    body = [ln for ln in rules.splitlines() if ln.strip()]
    assert 30 <= len(body) <= 60
    text = rules.lower()
    for needle in [
        "order by",  # multi-row EVALUATE
        "one define",  # single DEFINE block
        "without a table",  # measure reference qualification
        "boolean filter",  # restrictions in CALCULATE
        "summarizecolumns",  # default grouping function
        "summarize with",  # never SUMMARIZE with expressions
        "groupby",
        "currentgroup",
        "new names",  # rename then ORDER BY
        "datesinperiod",
        "-12",  # window offset
        "treatas",  # explicit anchor date for ROW + time intelligence
        "mode=validate",
    ]:
        assert needle in text, needle


def test_dax_query_rules_are_our_own_words_not_a_copy():
    """The vendor resource text is EULA-covered: no long verbatim run may appear in ours."""
    import pathlib
    import re

    ref_dir = pathlib.Path.home() / ".claude/docs/research/2026-09-30-pbi-agentic-sept/ms_resources"
    ref = ref_dir / "resource__dax_query_instructions_and_examples.md"
    if not ref.exists():
        pytest.skip("reference copy not present on this machine")
    from server.dax_query_rules import DAX_QUERY_RULES

    def words(s):
        return re.findall(r"[a-z0-9_\[\]']+", s.lower())

    ours, theirs = words(DAX_QUERY_RULES), words(ref.read_text(encoding="utf-8", errors="ignore"))
    grams = {tuple(theirs[i : i + 9]) for i in range(len(theirs) - 8)}
    shared = [i for i in range(len(ours) - 8) if tuple(ours[i : i + 9]) in grams]
    assert shared == [], "9-word verbatim overlap with the vendor resource text"


# --- instructions -----------------------------------------------------------------------------------------


def test_instructions_state_the_lane_split_in_one_short_sentence(registry):
    from server.mcp_app import build_instructions

    text = build_instructions(registry, "7.15.0")
    assert "pbi-webview2" in text
    line = next(ln for ln in text.splitlines() if "pbi-webview2" in ln)
    assert (
        len(line) < 200
        and "canvas" in line
        and "authoring" not in line.replace("Model, DAX and authoring", "")
    )
    assert "Model, DAX and authoring live here" in line
