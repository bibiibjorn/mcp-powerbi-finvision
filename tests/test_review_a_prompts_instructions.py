"""A-08 / L-04 / A-09 / A-34 / L-09: prompts, session instructions and tool
examples must only contain calls the live schemas accept.

Before: all 5 prompts used non-existent ops (set_pbip_path, bpa, analyze …),
args (visual_title, table, format) or tools (07_PBIP_Dependency_Analysis), and
the session instructions said `04_Run_DAX {dax_query: ...}` (the arg is
`query`) and listed the removed 12_Autonomous_Workflow.
"""

from __future__ import annotations

import json

import jsonschema
import pytest

from server.handlers import register_all_handlers
from server.prompts import PROMPTS, get_prompt_messages, get_prompt_steps
from server.registry import TOOL_EXAMPLES, HandlerRegistry

_SAMPLE_ARGS = {
    "page_name": "Sales Overview",
    "visual_name": "Total Sales",
    "enable_trace": "true",
    "pbip_path": "C:/work/Fake Project/Report.pbip",
    "depth": "deep",
    "table_name": "Sales",
    "measure_name": "Total Sales",
    "expression": 'CALCULATE ( SUM ( Sales[Amt] ), Sales[Region] = "EU" )',
    "format_string": "#,0.00",
    "output_format": "word",
    "goal": "make the card title bigger",
    "new_name": "Sales Amount",
}


@pytest.fixture(scope="module")
def schemas():
    reg = HandlerRegistry()
    register_all_handlers(reg)
    return {t.name: t.inputSchema for t in reg.get_all_tools_as_mcp()}


def _check_call(schemas, tool, args, where):
    assert tool in schemas, f"{where}: unknown tool {tool}"
    schema = schemas[tool]
    props = schema.get("properties", {})
    unknown = sorted(set(args) - set(props))
    assert not unknown, f"{where}: {tool} args not in schema: {unknown}"
    try:
        jsonschema.validate(args, schema)
    except jsonschema.ValidationError as e:  # pragma: no cover - message on failure
        pytest.fail(f"{where}: {tool} {args} rejected: {e.message}")


def _prompt_variants(prompt):
    full = {a.name: _SAMPLE_ARGS.get(a.name, "x") for a in prompt.arguments}
    minimal = {a.name: full[a.name] for a in prompt.arguments if a.required}
    variants = [full, minimal]
    if prompt.name == "analyze-model":
        variants += [dict(full, depth=d) for d in ("fast", "balanced")]
        variants += [{"depth": "deep"}]
    if prompt.name == "document-report":
        variants += [dict(full, output_format="html")]
    return variants


@pytest.mark.parametrize("prompt", PROMPTS, ids=lambda p: p.name)
def test_every_prompt_step_is_a_valid_call(schemas, prompt):
    for args in _prompt_variants(prompt):
        steps = get_prompt_steps(prompt.name, args)
        assert any(s.tool for s in steps)
        for i, step in enumerate(steps, start=1):
            if step.tool is None:
                continue
            _check_call(schemas, step.tool, step.args, f"{prompt.name} step {i}")


@pytest.mark.parametrize("prompt", PROMPTS, ids=lambda p: p.name)
def test_rendered_prompt_embeds_exact_json_calls(prompt):
    args = {a.name: _SAMPLE_ARGS.get(a.name, "x") for a in prompt.arguments}
    res = get_prompt_messages(prompt.name, args)
    text = "\n".join(m.content.text for m in res.messages)
    for step in get_prompt_steps(prompt.name, args):
        if step.tool:
            line = f"{step.tool} " + json.dumps(
                step.args, ensure_ascii=False, separators=(", ", ": ")
            )
            assert line in text
    # A DAX literal with quotes survives as valid JSON (L-04 escaping bug).
    if prompt.name == "create-measure":
        assert '\\"EU\\"' in text


def test_prompt_texts_never_mention_removed_tools_or_ops():
    for prompt in PROMPTS:
        args = {a.name: _SAMPLE_ARGS.get(a.name, "x") for a in prompt.arguments}
        text = "\n".join(m.content.text for m in get_prompt_messages(prompt.name, args).messages)
        for bad in (
            "set_pbip_path",
            "visual_title",
            "07_PBIP_Dependency_Analysis",
            "12_Autonomous_Workflow",
            "operation='bpa'",
        ):
            assert bad not in text, (prompt.name, bad)


def test_session_instructions_are_generated_and_valid(schemas):
    from server.mcp_app import QUICKSTART_CALLS, build_instructions

    reg = HandlerRegistry()
    register_all_handlers(reg)
    text = build_instructions(reg, "7.15.0")
    assert "12_Autonomous_Workflow" not in text
    assert "dax_query" not in text
    assert f"({reg.total_tools()} tools" in text
    for call in QUICKSTART_CALLS:
        _check_call(schemas, call["tool"], call["args"], "instructions")
        assert f"{call['tool']} " + json.dumps(call["args"], separators=(", ", ": ")) in text
    # the three damage-preventing rules
    assert "DISCARDS unsaved" in text
    assert "04_Run_DAX" in text and "confirm_destructive" in text


@pytest.mark.parametrize("tool_name", sorted(TOOL_EXAMPLES))
def test_tool_examples_validate_against_the_emitted_schema(schemas, tool_name):
    """Stronger than the key-name check in test_tool_descriptions: every example
    must pass jsonschema (the 07_Visual `create` example used to pass strings
    where the schema requires {table, measure} objects)."""
    for i, example in enumerate(TOOL_EXAMPLES[tool_name]):
        _check_call(schemas, tool_name, example, f"{tool_name} example {i}")
