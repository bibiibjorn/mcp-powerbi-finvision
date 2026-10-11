"""A-23 / A-25 / A-26 / A-30 / A-33 / L-10 / L-17 / L-18: registry hygiene,
schema duplicates, the injected-argument diet and the user guide.
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

from server.handlers import register_all_handlers
from server.registry import TOOL_TITLES, HandlerRegistry, ToolCategory

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def registry():
    reg = HandlerRegistry()
    register_all_handlers(reg)
    return reg


def test_every_tool_has_a_title_and_a_valid_category(registry):
    """A-25: 02_QA_Linguistic had no title (and the category maps missed it)."""
    names = {t.name for t in registry.get_all_tools()}
    assert names - set(TOOL_TITLES) == set()
    assert set(TOOL_TITLES) - names == set()
    valid = {c.value for c in ToolCategory}
    bad = {t.name: t.category for t in registry.get_all_tools() if t.category not in valid}
    assert not bad, bad


def test_dead_registry_api_removed():
    """L-17 / A-23: deferred-loading + discovery API had no callers."""
    for attr in (
        "enable_deferred_mode",
        "load_category",
        "is_category_loaded",
        "get_discovery_info",
        "get_category_tools_info",
        "get_tools_by_tool_category",
        "get_category_for_tool",
    ):
        assert not hasattr(HandlerRegistry, attr), attr
    import server.registry as r

    assert not hasattr(r, "CATEGORY_TOOLS") and not hasattr(r, "CATEGORY_INFO")


def test_dead_schema_entries_removed_and_no_duplicate_keys():
    """A-23 / L-17: unreferenced TOOL_SCHEMAS keys; A-26: duplicate dict keys
    (theme_operations.output_path was declared twice, the second silently won)."""
    from server.tool_schemas import TOOL_SCHEMAS

    assert "pbip_dependency_analysis" not in TOOL_SCHEMAS
    assert "filter_operations" not in TOOL_SCHEMAS
    tree = ast.parse((ROOT / "server" / "tool_schemas.py").read_text(encoding="utf-8"))
    dups = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
            seen = set()
            for k in keys:
                if k in seen:
                    dups.append((k, node.lineno))
                seen.add(k)
    assert not dups, dups
    desc = TOOL_SCHEMAS["theme_operations"]["properties"]["output_path"]["description"]
    assert "analyze_compliance" in desc and "export_theme" in desc


def test_tools_list_payload_budget(registry):
    """L-10 guard: the A-30 diet (confirm_destructive only on destructive tools,
    one copy of examples, short injected descriptions) must not creep back."""
    payload = [
        t.model_dump(by_alias=True, exclude_none=True, mode="json")
        for t in registry.get_all_tools_as_mcp()
    ]
    size = len(json.dumps(payload))
    # 115k after the WP2 diet; 128.6k after all of wave 2. The wave-3 L-10/L-11 diet
    # (deferred rare-op args, short descriptions + guide_notes / x-doc, canonical
    # names only, no per-tool prose on the injected args) -> 73.3k. Guard = +3%.
    # Never raise it: move new prose to guide_notes / x-doc, new rare args to
    # server/tool_surface.py DEFERRED_ARGS.
    # The one exception: the 28th tool (00_Client_Mode, Client Guard) adds its own 771 chars,
    # already minimised (75,185 -> 75,956); the guard moved 75,500 -> 76,300.
    assert size < 76_300, size
    per_tool_injected = len(
        json.dumps(
            {
                k: v
                for k, v in payload[0]["inputSchema"]["properties"].items()
                if k in ("response_format", "page_size", "next_token")
            }
        )
    )
    assert per_tool_injected < 200


# ---------------------------------------------------------------------------
# 10_Show_User_Guide (L-18)
# ---------------------------------------------------------------------------


def _guide(args):
    from server.handlers.user_guide_handler import handle_show_user_guide
    from server.registry import get_registry

    reg = get_registry()
    if reg.total_tools() == 0:
        register_all_handlers(reg)
    return handle_show_user_guide(args), reg


def test_default_guide_is_a_compact_index_listing_every_tool():
    out, reg = _guide({})
    assert out["success"] is True and out["topic"] == "index"
    for t in reg.get_all_tools():
        assert f"`{t.name}`" in out["guide"]
    full, _ = _guide({"topic": "all"})
    assert len(out["guide"]) < len(full["guide"]) / 2
    assert len(out["guide"]) < 20_000


def test_guide_for_one_tool_includes_parameters_and_reference():
    out, _ = _guide({"tool": "04_Run_DAX"})
    assert out["success"] is True
    assert "`query`" in out["guide"]
    assert "5.6" in out["guide"] or "Tool reference" in out["guide"] or "04_Run_DAX" in out["guide"]


def test_guide_unknown_tool_and_topic_are_typed_errors():
    bad_tool, _ = _guide({"tool": "99_Nope"})
    assert bad_tool["error_type"] == "unknown_tool" and "04_Run_DAX" in bad_tool["valid_values"]
    bad_topic, _ = _guide({"topic": "everything"})
    assert bad_topic["error_type"] == "invalid_argument"


def test_invalid_schema_does_not_crash_prebuild():
    from server.registry import ToolDefinition

    reg = HandlerRegistry()
    reg.register(
        ToolDefinition(
            name="99_Bad",
            description="bad schema tool",
            handler=lambda a: {"success": True},
            input_schema={"type": "object", "properties": {"x": {"type": "no-such-type"}}},
        )
    )
    reg.prebuild()  # must not raise
    assert reg.get_input_validator("99_Bad") is None
