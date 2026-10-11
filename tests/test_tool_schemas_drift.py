"""Phase 4: guard against tool-schema drift.

server/tool_schemas.py is the highest-churn untested file in the repo. These
tests pin the invariants that actually break in practice:
  - every registered tool exposes a non-empty input_schema
  - every operation enum is well-formed (no duplicates, no empty strings)
  - exactly 28 tools register (mirrors register_all_handlers' own assertion)
  - each schema is a structurally valid JSON-Schema object
"""

import pytest

from server.handlers import register_all_handlers
from server.registry import HandlerRegistry
from server.tool_schemas import TOOL_SCHEMAS


@pytest.fixture(scope="module")
def registry():
    r = HandlerRegistry()
    register_all_handlers(r)
    return r


def test_exactly_28_tools_registered(registry):
    assert (
        len(registry._handlers) == 28
    ), f"expected 28 registered tools, got {len(registry._handlers)}"


# No-argument tools legitimately carry an object schema with empty properties.
_NO_ARG_TOOLS = {"10_Show_User_Guide"}


def test_every_tool_has_an_object_input_schema(registry):
    """Every tool must declare a JSON-Schema object. No-arg tools may have
    empty properties; everything else must declare at least one property."""
    bad = []
    for name in registry._handlers:
        td = registry.get_tool_def(name)
        schema = getattr(td, "input_schema", None)
        if not isinstance(schema, dict) or schema.get("type") != "object":
            bad.append((name, "no object schema"))
            continue
        if name not in _NO_ARG_TOOLS and not schema.get("properties"):
            bad.append((name, "empty properties"))
    assert not bad, f"tools with bad input_schema: {bad}"


def test_operation_enums_are_well_formed():
    """Every TOOL_SCHEMAS entry with an operation enum: no dup / empty ops."""
    problems = {}
    for key, schema in TOOL_SCHEMAS.items():
        props = schema.get("properties", {}) if isinstance(schema, dict) else {}
        for op_key in ("operation", "batch_operation", "sub_operation", "mode"):
            field = props.get(op_key)
            if not isinstance(field, dict):
                continue
            enum = field.get("enum")
            if not enum:
                continue
            if any((not isinstance(v, str)) or v == "" for v in enum):
                problems[f"{key}.{op_key}"] = f"empty/non-str op in {enum}"
            if len(enum) != len(set(enum)):
                dups = [v for v in set(enum) if enum.count(v) > 1]
                problems[f"{key}.{op_key}"] = f"duplicate ops: {dups}"
    assert not problems, f"malformed operation enums: {problems}"


def test_all_schemas_are_object_type():
    bad = {
        k: s.get("type")
        for k, s in TOOL_SCHEMAS.items()
        if not (isinstance(s, dict) and s.get("type") == "object")
    }
    assert not bad, f"schemas that are not type=object: {bad}"
