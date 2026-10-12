"""Regression: the token-overflow guard's tool set matches real tools (P1).

The high_token_tools set in pbixray_server_enhanced contained only v6-era names
(full_analysis, analyze_model_bpa, 11_TMDL_Operations, ...) that no longer
exist, so the overflow safeguard was dead. This test pins the set to currently
registered tool names so it cannot silently rot again.
"""

from server.handlers import register_all_handlers
from server.registry import HandlerRegistry, HIGH_TOKEN_TOOLS


def _registered_tool_names():
    r = HandlerRegistry()
    register_all_handlers(r)
    return set(r._handlers.keys())


def test_high_token_tools_are_all_registered():
    registered = _registered_tool_names()
    unknown = HIGH_TOKEN_TOOLS - registered
    assert not unknown, f"high_token_tools references non-existent tools: {unknown}"


def test_high_token_tools_nonempty():
    assert HIGH_TOKEN_TOOLS, "the overflow guard set must not be empty"
