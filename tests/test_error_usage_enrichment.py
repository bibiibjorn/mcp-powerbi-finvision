"""F-24: argument / not-found errors carry a generated ``usage`` block + ``did_you_mean``.

``enrich_error_response(result, tool=..., arguments=...)`` derives ``usage``
(``{tool, operation, required[], optional_top[], example_call}``) from the registry schema
and ``TOOL_EXAMPLES`` - the same data ``10_Show_User_Guide tool= operation=`` renders - so
the ~85 ``missing_argument`` / 51 ``operation_failed`` sites benefit without being edited.
"""

from __future__ import annotations

from server.mcp_app import validate_arguments
from server.registry import HandlerRegistry, ToolDefinition, TOOL_EXAMPLES, get_registry
from core.validation.error_enrichment import enrich_error_response


def _tool(name="T_Tool", ops=("list", "create", "delete"), required=("operation",)):
    return ToolDefinition(
        name=name,
        description="test",
        handler=lambda a: {"success": True},
        input_schema={
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": list(ops)},
                "table_name": {"type": "string", "description": "Table (create, delete)"},
                "measure_name": {"type": "string", "description": "Measure (create)"},
                "expression": {"type": "string", "description": "DAX body for create"},
                "pbip_path": {"type": "string", "description": "Project (list)"},
            },
            "required": list(required),
        },
        category="model",
        examples=[
            {"operation": "list", "pbip_path": "C:/r/Report.pbip"},
            {
                "operation": "create",
                "table_name": "Sales",
                "measure_name": "Total Sales",
                "expression": "SUM ( Sales[Amount] )",
            },
        ],
    )


def _registry_with(td):
    reg = HandlerRegistry()
    reg.register(td)
    return reg


def _patch_registry(monkeypatch, td):
    reg = _registry_with(td)
    import server.registry as registry_mod

    monkeypatch.setattr(registry_mod, "get_registry", lambda: reg)
    return reg


def test_missing_argument_gets_usage_with_the_matching_example(monkeypatch):
    _patch_registry(monkeypatch, _tool())
    res = enrich_error_response(
        {"success": False, "error_type": "missing_argument", "error": "measure_name is required"},
        tool="T_Tool",
        arguments={"operation": "create", "table_name": "Sales"},
    )
    usage = res["usage"]
    assert usage["tool"] == "T_Tool"
    assert usage["operation"] == "create"
    assert "measure_name" in usage["required"]  # named in the error message
    assert "operation" in usage["required"]  # schema-required
    assert usage["example_call"]["tool"] == "T_Tool"
    assert usage["example_call"]["args"]["measure_name"] == "Total Sales"
    assert "10_Show_User_Guide tool=T_Tool operation=create" in usage["more"]
    assert "suggestion" in res  # _RECOVERY_MAP now covers missing_argument


def test_operation_specific_optional_args_are_listed_and_capped(monkeypatch):
    _patch_registry(monkeypatch, _tool())
    res = enrich_error_response(
        {"success": False, "error_type": "missing_argument", "error": "x is required"},
        tool="T_Tool",
        arguments={"operation": "list"},
    )
    assert res["usage"]["optional_top"] == ["pbip_path"]
    assert len(res["usage"]["optional_top"]) <= 10


def test_unknown_operation_gets_did_you_mean_and_usage(monkeypatch):
    _patch_registry(monkeypatch, _tool())
    res = enrich_error_response(
        {
            "success": False,
            "error_type": "invalid_operation",
            "error": "Unknown operation: 'creat'",
        },
        tool="T_Tool",
        arguments={"operation": "creat"},
    )
    assert res["did_you_mean"][0] == "create"
    assert res["usage"]["tool"] == "T_Tool"


def test_enum_violation_from_the_validator_gets_did_you_mean_and_missing_list(monkeypatch):
    td = _tool()
    reg = _patch_registry(monkeypatch, td)
    invalid = validate_arguments(reg, "T_Tool", {"operation": "delet"})
    assert invalid["error_type"] == "invalid_arguments"
    assert invalid["did_you_mean"] == ["delete"]
    assert invalid["usage"]["example_call"]["tool"] == "T_Tool"

    missing = validate_arguments(reg, "T_Tool", {})
    assert missing["missing_arguments"] == ["operation"]
    assert "operation" in missing["usage"]["required"]


def test_not_found_name_gets_did_you_mean_from_the_candidate_list(monkeypatch):
    _patch_registry(monkeypatch, _tool())
    res = enrich_error_response(
        {
            "success": False,
            "error_type": "not_found",
            "error": "Page 'Overvew' not found",
            "available_pages": ["Overview", "Detail"],
        },
        tool="T_Tool",
        arguments={"operation": "list", "page_name": "Overvew"},
    )
    assert res["did_you_mean"] == ["Overview"]


def test_case_only_difference_is_suggested(monkeypatch):
    _patch_registry(monkeypatch, _tool())
    res = enrich_error_response(
        {
            "success": False,
            "error_type": "table_not_found",
            "error": "no such table",
            "available_tables": ["Sales", "Date"],
        },
        tool="T_Tool",
        arguments={"operation": "list", "table_name": "sales"},
    )
    assert res["did_you_mean"] == ["Sales"]


def test_unknown_tool_gets_did_you_mean():
    res = enrich_error_response(
        {
            "success": False,
            "error_type": "unknown_tool",
            "error": "Unknown tool: 02_Model_Operation",
            "available_tools": ["02_Model_Operations", "04_Run_DAX"],
        },
        tool="02_Model_Operation",
    )
    assert res["did_you_mean"][0] == "02_Model_Operations"


def test_handler_supplied_usage_and_did_you_mean_win(monkeypatch):
    _patch_registry(monkeypatch, _tool())
    res = enrich_error_response(
        {
            "success": False,
            "error_type": "missing_argument",
            "error": "x is required",
            "usage": {"custom": True},
            "did_you_mean": ["mine"],
        },
        tool="T_Tool",
        arguments={"operation": "create"},
    )
    assert res["usage"] == {"custom": True}
    assert res["did_you_mean"] == ["mine"]


def test_unknown_tool_name_or_no_tool_adds_no_usage(monkeypatch):
    _patch_registry(monkeypatch, _tool())
    plain = enrich_error_response(
        {"success": False, "error_type": "missing_argument", "error": "e"}
    )
    assert "usage" not in plain
    ghost = enrich_error_response(
        {"success": False, "error_type": "missing_argument", "error": "e"},
        tool="No_Such_Tool",
        arguments={},
    )
    assert "usage" not in ghost


def test_unrelated_error_types_are_untouched(monkeypatch):
    _patch_registry(monkeypatch, _tool())
    res = enrich_error_response(
        {"success": False, "error_type": "timeout", "error": "slow"},
        tool="T_Tool",
        arguments={"operation": "list"},
    )
    assert "usage" not in res


def test_example_payload_is_capped(monkeypatch):
    td = _tool()
    td.examples = [{"operation": "create", "expression": "X" * 5000, "table_name": "T"}]
    _patch_registry(monkeypatch, td)
    res = enrich_error_response(
        {"success": False, "error_type": "missing_argument", "error": "table_name is required"},
        tool="T_Tool",
        arguments={"operation": "create"},
    )
    example = res["usage"]["example_call"]["args"]
    assert len(str(example)) < 800


def test_real_registry_example_for_a_real_tool():
    """The real registry feeds 02_Model_Operations create (TOOL_EXAMPLES) into usage."""
    from server.handlers import register_all_handlers

    reg = get_registry()
    if not reg.has_tool("02_Model_Operations"):
        register_all_handlers(reg)
    assert TOOL_EXAMPLES["02_Model_Operations"]
    res = enrich_error_response(
        {"success": False, "error_type": "missing_argument", "error": "measure_name is required"},
        tool="02_Model_Operations",
        arguments={"object_type": "measure", "operation": "create"},
    )
    args = res["usage"]["example_call"]["args"]
    assert args["operation"] == "create" and args["object_type"] == "measure"
    assert "measure_name" in res["usage"]["required"]


def test_dispatcher_passes_tool_and_arguments(monkeypatch):
    from server.dispatch import ToolDispatcher

    td = _tool()
    reg = _registry_with(td)
    td.handler = lambda a: {
        "success": False,
        "error_type": "missing_argument",
        "error": "table_name is required",
    }
    reg = _registry_with(td)
    d = ToolDispatcher()
    d.registry = reg
    out = d.dispatch("T_Tool", {"operation": "create"})
    assert out["usage"]["operation"] == "create"
    assert "table_name" in out["usage"]["required"]
