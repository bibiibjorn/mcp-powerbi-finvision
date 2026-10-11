"""Regression: 10_Show_User_Guide must reflect the LIVE tool registry, not a
hand-maintained prose snapshot that drifts. The old guide listed a nonexistent
`11_PBIP_Prototype`, omitted five real tools (02_QA_Linguistic, 13_Query_Plan,
15_DAX_Refactor, 16_Dependency_Graph, 17_Desktop_Bridge), and named a tool
removed in v7.8 (12_Autonomous_Workflow). Generating the inventory from the
registry makes that class of drift structurally impossible.
"""

from server.handlers import register_all_handlers
from server.handlers.user_guide_handler import handle_show_user_guide
from server.registry import get_registry


def _guide_text():
    # Ensure the shared registry is populated (register_all_handlers asserts 27).
    reg = get_registry()
    if reg.total_tools() == 0:
        register_all_handlers(reg)
    out = handle_show_user_guide({})
    assert out["success"] is True
    return out["guide"], reg


def test_guide_lists_every_registered_tool():
    guide, reg = _guide_text()
    missing = [t.name for t in reg.get_all_tools() if t.name not in guide]
    assert missing == [], f"guide omits registered tools: {missing}"


def test_guide_has_no_phantom_or_removed_tools():
    guide, _ = _guide_text()
    # 11_PBIP_Prototype never existed as a registered tool; 12_Autonomous_Workflow
    # was removed in v7.8. Neither should appear in the self-serve guide.
    assert "11_PBIP_Prototype" not in guide
    assert "12_Autonomous_Workflow" not in guide


def test_guide_includes_previously_missing_tools():
    guide, reg = _guide_text()
    names = {t.name for t in reg.get_all_tools()}
    for expected in [
        "02_QA_Linguistic",
        "13_Query_Plan",
        "15_DAX_Refactor",
        "16_Dependency_Graph",
        "17_Desktop_Bridge",
    ]:
        if expected in names:  # only assert for tools actually registered
            assert expected in guide, f"{expected} is registered but missing from the guide"


def test_guide_tool_count_matches_registry():
    guide, reg = _guide_text()
    # The guide should state the real registered count, not a stale number.
    assert str(reg.total_tools()) in guide
