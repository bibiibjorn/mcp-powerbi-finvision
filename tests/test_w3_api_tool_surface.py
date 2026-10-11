"""L-10 / K-18 / A-33: the tools/list token diet must not lose capability.

tools/list was 128.6k chars (~32k tokens) after wave 2. The diet:

* rare-op arguments are DEFERRED (server/tool_surface.py): still declared in the
  registered schema, validated and never reported unknown, but left out of
  tools/list; ``10_Show_User_Guide tool=<name>`` documents them;
* tool descriptions are short search surfaces, the long text lives in
  ``ToolDefinition.guide_notes``;
* property descriptions are one line, the long text lives in the private
  ``x-doc`` keyword (stripped from tools/list, rendered by the guide);
* the injected protocol args carry no per-tool prose (the session
  instructions explain them once).

These tests pin every one of those "moved, not lost" guarantees.
"""

from __future__ import annotations

import json

import pytest

from server.handlers import register_all_handlers
from server.mcp_app import QUICKSTART_CALLS, build_instructions
from server.registry import DOC_KEY, TOOL_EXAMPLES, HandlerRegistry, get_registry
from server.tool_surface import DEFERRED_ARGS


@pytest.fixture(scope="module")
def registry():
    reg = HandlerRegistry()
    register_all_handlers(reg)
    return reg


@pytest.fixture(scope="module")
def payload(registry):
    return [
        t.model_dump(by_alias=True, exclude_none=True, mode="json")
        for t in registry.get_all_tools_as_mcp()
    ]


def _walk_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_keys(v)


# ---------------------------------------------------------------------------
# Size
# ---------------------------------------------------------------------------


def test_payload_is_under_the_wave3_target(payload):
    size = len(json.dumps(payload))
    assert size <= 90_000, size  # the L-10 target; the tight guard is in test_review_a_registry
    biggest = max(payload, key=lambda t: len(json.dumps(t)))
    assert len(json.dumps(biggest)) < 9_000, biggest["name"]


def test_emitted_property_descriptions_are_one_liners(payload):
    long = [
        (t["name"], name, len(spec.get("description", "")))
        for t in payload
        for name, spec in t["inputSchema"].get("properties", {}).items()
        if len(spec.get("description", "")) > 160
    ]
    assert not long, long


def test_injected_protocol_args_carry_no_per_tool_prose(payload):
    for t in payload:
        props = t["inputSchema"]["properties"]
        for key in ("response_format", "page_size", "next_token"):
            assert "description" not in props[key], (t["name"], key)
    text = build_instructions(get_registry(), "x")
    for key in ("response_format", "page_size", "next_token", "deprecated_args"):
        assert key in text


# ---------------------------------------------------------------------------
# Deferred args: moved out of tools/list, not out of the tool
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool", sorted(DEFERRED_ARGS))
def test_deferred_args_are_real_optional_and_unexampled(registry, tool):
    td = registry.get_tool_def(tool)
    registered = td.input_schema["properties"]
    names = DEFERRED_ARGS[tool]
    assert names <= set(registered), sorted(names - set(registered))
    assert not names & set(td.input_schema.get("required") or [])
    assert not names & {k for ex in TOOL_EXAMPLES.get(tool, []) for k in ex}
    for call in QUICKSTART_CALLS:
        if call["tool"] == tool:
            assert not names & set(call["args"])
    emitted = registry.get_emitted_schema(tool)["properties"]
    assert not names & set(emitted)
    assert names <= registry.declared_properties(tool)  # never unknown_args
    assert (
        f"10_Show_User_Guide tool={tool}"
        in registry.get_all_tools_as_mcp()[
            [t.name for t in registry.get_all_tools_as_mcp()].index(tool)
        ].description
    )


def test_deferred_arg_is_still_type_checked_by_the_validator(registry):
    from server.mcp_app import validate_arguments

    ok = validate_arguments(
        registry, "02_Model_Operations", {"object_type": "table", "start_year": 2020}
    )
    bad = validate_arguments(
        registry, "02_Model_Operations", {"object_type": "table", "start_year": "soon"}
    )
    assert ok is None
    assert bad["error_type"] == "invalid_arguments" and bad["argument"] == "start_year"


def test_deferred_arg_reaches_the_handler_without_an_unknown_args_notice():
    from tests._mcp_harness import make_dispatcher, tool

    import server.tool_surface as ts

    d = make_dispatcher(
        tool(
            "02_Model_Operations",
            lambda a: {"success": True, "echo": a},
            properties={"object_type": {"type": "string"}, "start_year": {"type": "integer"}},
        )
    )
    old = ts.DEFERRED_ARGS.get("02_Model_Operations")
    assert "start_year" in old
    out = d.dispatch("02_Model_Operations", {"object_type": "table", "start_year": 2020})
    assert out["echo"]["start_year"] == 2020
    assert "unknown_args" not in out


# ---------------------------------------------------------------------------
# Private keywords never reach the client
# ---------------------------------------------------------------------------


def test_no_private_keywords_in_tools_list(payload):
    leaked = {k for t in payload for k in _walk_keys(t["inputSchema"]) if str(k).startswith("x-")}
    assert not leaked


def test_x_doc_is_only_used_to_keep_a_longer_text(registry):
    for td in registry.get_all_tools():
        for name, spec in (td.input_schema.get("properties") or {}).items():
            doc = spec.get(DOC_KEY) if isinstance(spec, dict) else None
            if doc is None:
                continue
            short = spec.get("description", "")
            assert isinstance(doc, str) and len(doc) > len(short), (td.name, name)


# ---------------------------------------------------------------------------
# The guide is where the moved text lives
# ---------------------------------------------------------------------------


def _guide(args):
    from server.handlers.user_guide_handler import handle_show_user_guide

    reg = get_registry()
    if reg.total_tools() == 0:
        register_all_handlers(reg)
    return handle_show_user_guide(args), reg


@pytest.mark.parametrize("tool", ["02_Model_Operations", "07_Visual_Operations", "13_Query_Plan"])
def test_tool_guide_carries_every_arg_the_long_docs_and_the_notes(tool):
    out, reg = _guide({"tool": tool})
    td = reg.get_tool_def(tool)
    text = out["guide"]
    for name, spec in td.input_schema["properties"].items():
        assert f"`{name}`" in text, name
        if isinstance(spec, dict) and spec.get(DOC_KEY):
            assert spec[DOC_KEY] in text, name
    if td.guide_notes:
        assert td.guide_notes.strip() in text
    for name in DEFERRED_ARGS.get(tool, ()):
        assert f"`{name}`" in text and "rare-op" in text


def test_every_tool_with_a_shortened_description_keeps_its_notes():
    _, reg = _guide({})
    for td in reg.get_all_tools():
        if td.guide_notes:
            assert len(td.guide_notes) > len(td.description) * 0.6, td.name


def test_model_operations_guide_lists_ops_per_object_type():
    out, reg = _guide({"tool": "02_Model_Operations"})
    assert "Operations by object_type" in out["guide"]
    assert "set_refresh_policy" in out["guide"] and "daxlib_install" in out["guide"]


def test_guide_operation_filter_returns_that_ops_args():
    out, _ = _guide({"tool": "02_Model_Operations", "operation": "set_refresh_policy"})
    assert out["success"] is True
    text = out["guide"]
    assert "rolling_window_granularity" in text and "object_type: table" in text
    assert "`translations`" not in text
    bad, _ = _guide({"tool": "02_Model_Operations", "operation": "nope"})
    assert bad["error_type"] == "invalid_operation" and "set_refresh_policy" in bad["valid_values"]


def test_no_schema_dict_repeats_a_key():
    """K-18: tool_schemas.py once repeated "output_path" in one dict literal (the
    later value silently won). Pin it for every dict literal under server/."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "server"
    dupes = []
    for path in root.rglob("*.py"):
        for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Dict):
                keys = [k.value for k in n.keys if isinstance(k, ast.Constant)]
                rep = sorted({k for k in keys if keys.count(k) > 1})
                if rep:
                    dupes.append(f"{path.name}:{n.lineno} {rep}")
    assert not dupes, dupes
