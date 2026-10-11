"""Deep review 2026-10-03 (slice N): N-33, a typo'd MCP_PBI_PROFILE is loud; the guide hides hidden tools."""

import logging

import pytest

from server import tool_profiles


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("MCP_PBI_TOOLS_EXCLUDE", raising=False)
    tool_profiles._warned_profiles.clear()
    yield
    tool_profiles._warned_profiles.clear()


def test_unknown_profile_falls_back_to_all_but_warns_and_is_described(monkeypatch, caplog):
    monkeypatch.setenv("MCP_PBI_PROFILE", "modle")
    with caplog.at_level(logging.WARNING, logger="server.tool_profiles"):
        assert tool_profiles.active_profile() == "all"
        assert tool_profiles.active_profile() == "all"
    warnings = [r for r in caplog.records if "modle" in r.getMessage()]
    assert len(warnings) == 1  # logged once, not on every tools/list
    d = tool_profiles.describe()
    assert d["profile"] == "all" and d["requested_profile"] == "modle"
    assert "modle" in d["warning"] and "model" in d["warning"]  # names the valid profiles


@pytest.mark.parametrize("value", ["all", "model", "report", " MODEL ", ""])
def test_valid_profile_has_no_warning(monkeypatch, value):
    monkeypatch.setenv("MCP_PBI_PROFILE", value)
    assert tool_profiles.profile_warning() is None
    assert "warning" not in tool_profiles.describe()


def _guide(args):
    from server.handlers.user_guide_handler import handle_show_user_guide
    from server.handlers import register_all_handlers
    from server.registry import get_registry

    reg = get_registry()
    if not reg.has_tool("10_Show_User_Guide"):
        register_all_handlers(reg)
    return handle_show_user_guide(args)


def test_guide_index_and_inventory_omit_tools_hidden_by_profile(monkeypatch):
    monkeypatch.setenv("MCP_PBI_PROFILE", "model")
    index = _guide({})["guide"]
    # the generated per-tool lines (the static workflow prose still names tools by hand)
    assert "- `07_Visual_Operations` " not in index and "- `02_Model_Operations` " in index
    full = _guide({"topic": "all"})["guide"]
    assert "#### 07_Visual_Operations" not in full and "#### 02_Model_Operations" in full


def test_guide_index_lists_everything_by_default(monkeypatch):
    monkeypatch.setenv("MCP_PBI_PROFILE", "all")
    index = _guide({})["guide"]
    assert "- `07_Visual_Operations` " in index


def test_guide_tool_section_of_hidden_tool_is_refused(monkeypatch):
    monkeypatch.setenv("MCP_PBI_PROFILE", "model")
    r = _guide({"tool": "07_Visual_Operations"})
    assert r["success"] is False and r["error_type"] == "tool_hidden_by_profile"
    assert _guide({"tool": "02_Model_Operations"})["success"] is True
