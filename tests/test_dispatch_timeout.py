"""Regression: dispatch_async must enforce a per-tool timeout.

A frozen Power BI Desktop / unresponsive msmdsrv blocks a synchronous handler
inside a CLR/ADOMD call forever. The dispatcher wraps the pool call in
asyncio.wait_for(timeout=...) and returns a structured envelope so an
autonomous caller can recover instead of hanging. ToolTimeoutManager supplies
the bound, keyed by REGISTERED tool name (K-06).

WP2 (A-03): a timed-out READ reports ``timeout``; a timed-out WRITE reports
``timeout_outcome_unknown`` (verify before retrying) — covered in depth by
tests/test_review_a_dispatch_concurrency.py.
"""

import asyncio
import threading


from server.dispatch import ToolDispatcher
from server.registry import HandlerRegistry, ToolDefinition


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _dispatcher(handler, name="04_Run_DAX"):
    reg = HandlerRegistry()
    reg.register(
        ToolDefinition(
            name=name,
            description="test tool",
            handler=handler,
            input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
            category="query",
            annotations={"readOnlyHint": True, "destructiveHint": False},
        )
    )
    d = ToolDispatcher()
    d.registry = reg
    return d


def test_slow_handler_times_out_with_structured_error(monkeypatch):
    release = threading.Event()

    def _slow(args):
        release.wait(5)
        return {"success": True}

    d = _dispatcher(_slow)
    monkeypatch.setattr(d, "_get_timeout", lambda tool_name: 0.2)
    try:
        out = _run(d.dispatch_async("04_Run_DAX", {}))
    finally:
        release.set()  # K-14: never leave a worker sleeping past the test
    assert out["success"] is False
    assert out["error_type"] == "timeout"
    # The error should be actionable, not a bare traceback string.
    assert "04_Run_DAX" in out["error"]


def test_fast_handler_returns_normally(monkeypatch):
    d = _dispatcher(lambda args: {"success": True, "value": 42})
    monkeypatch.setattr(d, "_get_timeout", lambda tool_name: 5.0)
    out = _run(d.dispatch_async("04_Run_DAX", {}))
    assert out["success"] is True
    assert out["value"] == 42


def test_get_timeout_uses_tool_timeout_manager(monkeypatch):
    # _get_timeout should consult a ToolTimeoutManager keyed by tool name.
    d = ToolDispatcher()
    t = d._get_timeout("04_Run_DAX")
    assert isinstance(t, (int, float))
    assert t > 0


def _mgr(custom=None):
    from core.config.tool_timeouts import ToolTimeoutManager

    return ToolTimeoutManager(custom)


def test_registered_tool_names_resolve_to_real_budgets():
    mgr = _mgr()
    # Long ops must NOT fall back to the 120s dispatch default.
    assert mgr.get_timeout("06_Analysis_Operations") == 300
    assert mgr.get_timeout("08_Documentation_Word") == 300
    assert mgr.get_timeout("07_PBIP_Operations") == 300
    assert mgr.get_timeout("02_Model_Operations") == 300
    assert mgr.get_timeout("02_TMDL_Operations") == 180
    assert mgr.get_timeout("03_Batch_Operations") == 180
    assert mgr.get_timeout("04_Run_DAX") == 60


def test_unknown_tool_returns_dispatch_default():
    assert _mgr().get_timeout("99_Does_Not_Exist", default=120) == 120


def test_operator_can_override_registered_name_via_config():
    mgr = _mgr({"06_Analysis_Operations": 42})
    assert mgr.get_timeout("06_Analysis_Operations") == 42


def test_legacy_keys_are_reported_not_silently_ignored():
    """K-06: legacy v6 keys ('run_dax', 'full_analysis') never matched a
    registered name, so they never applied. They are now reported (and logged
    at startup) instead of looking like a working setting."""
    mgr = _mgr({"run_dax": 600, "04_Run_DAX": 90})
    assert mgr.get_timeout("04_Run_DAX") == 90
    assert mgr.unknown_keys() == ["run_dax"]


def test_shipped_config_timeouts_are_keyed_by_registered_names():
    """K-06: every key in config/default_config.json tool_timeouts is a real tool."""
    import json
    import os

    from server.handlers import register_all_handlers

    here = os.path.dirname(__file__)
    with open(os.path.join(here, "..", "config", "default_config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    reg = HandlerRegistry()
    register_all_handlers(reg)
    names = {t.name for t in reg.get_all_tools()}
    unknown = sorted(set(cfg["tool_timeouts"]) - names)
    assert not unknown, f"tool_timeouts keys that are not registered tools: {unknown}"
    assert _mgr(cfg["tool_timeouts"]).unknown_keys() == []
