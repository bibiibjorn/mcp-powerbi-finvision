"""Args absent from a client's tools/list arrive as strings; they must still validate.

Found live on 2026-09-23: `04_Run_DAX bypass_cache` was sent as "true" by Claude Code
(its cached schema didn't declare the arg) and strict validation rejected the call.
"""

from server.arg_coercion import coerce_arguments

SCHEMA = {
    "type": "object",
    "properties": {
        "flag": {"type": "boolean"},
        "n": {"type": "integer"},
        "x": {"type": "number"},
        "items": {"type": "array", "items": {"type": "string"}},
        "obj": {"type": "object"},
        "name": {"type": "string"},
        "either": {"type": ["integer", "string"]},
    },
}


def test_string_scalars_are_coerced_to_declared_types():
    args = {"flag": "true", "n": "5", "x": "2.5", "items": '["a","b"]', "obj": '{"k":1}'}
    changed = coerce_arguments(SCHEMA, args)
    assert args == {"flag": True, "n": 5, "x": 2.5, "items": ["a", "b"], "obj": {"k": 1}}
    assert set(changed) == {"flag", "n", "x", "items", "obj"}


def test_false_and_integer_float_forms():
    args = {"flag": "False", "n": "3.0"}
    coerce_arguments(SCHEMA, args)
    assert args == {"flag": False, "n": 3}


def test_strings_that_do_not_parse_or_are_declared_strings_are_untouched():
    args = {"flag": "maybe", "n": "five", "name": "true", "either": "7", "unknown": "true"}
    coerce_arguments(SCHEMA, args)
    assert args == {"flag": "maybe", "n": "five", "name": "true", "either": "7", "unknown": "true"}


def test_real_typed_values_are_untouched():
    args = {"flag": True, "n": 4, "items": ["x"]}
    assert coerce_arguments(SCHEMA, args) == {}
    assert args == {"flag": True, "n": 4, "items": ["x"]}


def test_run_dax_bypass_cache_string_passes_validation_through_the_real_app():
    """End to end through create_app's call_tool: "true" for a boolean must not be rejected."""
    from server import mcp_app
    from server.registry import get_registry
    from server.handlers import register_all_handlers

    registry = get_registry()
    if not registry.has_tool("04_Run_DAX"):
        register_all_handlers(registry)
    args = {"query": 'EVALUATE ROW("a", 1)', "bypass_cache": "true", "top_n": "5"}
    coerce_arguments(registry.get_full_schema("04_Run_DAX"), args)
    assert args["bypass_cache"] is True and args["top_n"] == 5
    assert mcp_app.validate_arguments(registry, "04_Run_DAX", args) is None
