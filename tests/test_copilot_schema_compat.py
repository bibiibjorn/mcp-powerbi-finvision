"""The emitted tools/list must load in GitHub Copilot (VS Code agent mode).

Copilot sends EVERY tool of a server with each request, to whichever model is selected, so one tool
the model API rejects fails every request on that model. The rules below are the ones the OpenAI
(GPT) and Anthropic function-calling APIs and VS Code enforce:

* tool name ``^[a-zA-Z0-9_-]{1,64}$``, and VS Code's ``mcp_<server>_<tool>`` name within 64 chars;
* at most 128 tools;
* description at most 1024 chars (OpenAI's function-description limit);
* inputSchema: top-level ``type: object`` with no top-level oneOf/anyOf/allOf/enum/not, a valid
  JSON Schema, and an ``items`` schema on every array (OpenAI answers 400 without one).
"""

from __future__ import annotations

import re
from typing import Any, Iterator

import jsonschema
import pytest

from server.handlers import register_all_handlers
from server.registry import HandlerRegistry

NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
# The server names the setup script registers (Claude Code / VS Code, Claude Desktop).
SERVER_NAMES = ("powerbi-finvision", "MCP-PowerBi-Finvision-DEV")


@pytest.fixture(scope="module")
def tools() -> list[Any]:
    reg = HandlerRegistry()
    register_all_handlers(reg)
    # What tools/list returns (deferred rare-op args omitted, as a client sees it).
    return list(reg.get_all_tools_as_mcp())


def _nodes(schema: Any, path: str = "$") -> Iterator[tuple[str, dict[str, Any]]]:
    """Every schema node, with its JSON path."""
    if not isinstance(schema, dict):
        return
    yield path, schema
    for key in ("properties", "patternProperties", "$defs", "definitions"):
        for name, sub in (schema.get(key) or {}).items():
            yield from _nodes(sub, f"{path}.{key}.{name}")
    for key in ("items", "additionalProperties", "not", "contains"):
        if isinstance(schema.get(key), dict):
            yield from _nodes(schema[key], f"{path}.{key}")
    for key in ("anyOf", "oneOf", "allOf", "prefixItems"):
        for i, sub in enumerate(schema.get(key) or []):
            yield from _nodes(sub, f"{path}.{key}[{i}]")


def _is_array(node: dict[str, Any]) -> bool:
    kind = node.get("type")
    return kind == "array" or (isinstance(kind, list) and "array" in kind)


def test_tool_count_within_copilot_limit(tools):
    assert 0 < len(tools) <= 128


def test_tool_names_valid_for_copilot(tools):
    for tool in tools:
        assert NAME_RE.match(tool.name), tool.name
        for server in SERVER_NAMES:
            exposed = f"mcp_{server}_{tool.name}".lower()
            assert len(exposed) <= 64, exposed


def test_descriptions_within_openai_limit(tools):
    too_long = {t.name: len(t.description or "") for t in tools if len(t.description or "") > 1024}
    assert not too_long, too_long


def test_input_schema_top_level_is_a_plain_object(tools):
    for tool in tools:
        schema = tool.inputSchema
        assert schema.get("type") == "object", tool.name
        clash = {"oneOf", "anyOf", "allOf", "enum", "not"} & set(schema)
        assert not clash, (tool.name, clash)


def test_input_schema_is_valid_json_schema(tools):
    for tool in tools:
        jsonschema.Draft7Validator.check_schema(tool.inputSchema)
        jsonschema.Draft202012Validator.check_schema(tool.inputSchema)


def test_every_array_declares_items(tools):
    missing = [
        f"{tool.name} {path}"
        for tool in tools
        for path, node in _nodes(tool.inputSchema)
        if _is_array(node) and "items" not in node
    ]
    assert not missing, missing


def test_required_entries_are_declared(tools):
    for tool in tools:
        for path, node in _nodes(tool.inputSchema):
            props = node.get("properties") or {}
            undeclared = [r for r in node.get("required") or [] if r not in props]
            assert not undeclared, (tool.name, path, undeclared)
