"""Review K-19 #4 / C-25: every operation a tool's schema accepts is named in its section of the
tool reference (Part III of ``docs/GUIDE.md``; the schema is the source of truth, the doc may not
lag)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from server.resources import GUIDE_RELATIVE_PATH, tool_reference_region

REPO = Path(__file__).resolve().parent.parent
DOC = REPO / GUIDE_RELATIVE_PATH
OP_KEYS = ("operation", "object_type", "batch_operation", "mode")


@pytest.fixture(scope="module")
def sections():
    text = tool_reference_region(DOC.read_text(encoding="utf-8"))
    assert text, "docs/GUIDE.md has no tool-reference:start/end markers"
    parts = re.split(r"^## III\.[0-9a-z]+ `([^`]+)`", text, flags=re.M)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts), 2)}


@pytest.fixture(scope="module")
def tools():
    from server.handlers import register_all_handlers
    from server.registry import HandlerRegistry

    registry = HandlerRegistry()
    register_all_handlers(registry)
    return registry.get_all_tools_as_mcp()


def test_every_tool_has_a_section(tools, sections):
    assert [t.name for t in tools if t.name not in sections] == []


def test_every_schema_operation_is_named_in_its_section(tools, sections):
    missing = {}
    for tool in tools:
        props = tool.inputSchema.get("properties", {})
        for key in OP_KEYS:
            enum = (props.get(key) or {}).get("enum")
            if not enum:
                continue
            body = sections.get(tool.name, "")
            absent = [op for op in enum if not re.search(r"\b" + re.escape(op) + r"\b", body)]
            if absent:
                missing[f"{tool.name}.{key}"] = absent
            break
    assert (
        not missing
    ), f"operations in the schema but not in the tool reference (GUIDE.md Part III): {missing}"
