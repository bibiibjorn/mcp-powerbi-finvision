"""Regressions found by the live 7.16 smoke against Desktop / XMLA / offline (2026-09-30).

1. ``connection_state.cleanup()`` reset the process-wide ``agent_policy``, so every
   ``04_Run_DAX`` failed with "agent_policy not available" after any target switch.
2. ``02_Model_Operations object_type=calendar`` fell through to ``handler.execute()``,
   which CalendarCrudManager does not have.
3. On the offline lane a DMV-backed read reported "connection lost"; it now says
   ``offline_no_engine``.
"""

from core.infrastructure.connection_state import connection_state


def test_cleanup_keeps_the_process_wide_agent_policy():
    sentinel = object()
    before = connection_state.agent_policy
    connection_state.agent_policy = sentinel
    try:
        connection_state.cleanup()
        assert connection_state.agent_policy is sentinel
    finally:
        connection_state.agent_policy = before


def test_calendar_dispatches_directly_through_the_handler(monkeypatch):
    from server.handlers import model_operations_handler as moh

    calls = []

    class FakeCalendarManager:  # like CalendarCrudManager: no execute()
        def list_calendars(self, table_name=None):
            calls.append(table_name)
            return {"success": True, "calendars": [], "count": 0}

    monkeypatch.setitem(moh._handlers, "calendar", FakeCalendarManager())
    out = moh._original_handle_model_operations(
        {"object_type": "calendar", "operation": "list", "table_name": "Date"}
    )
    assert out.get("success") is True, out
    assert calls == ["Date"]


def test_offline_lane_reports_no_engine(monkeypatch):
    from core.infrastructure import offline_model
    from core.validation.error_handler import ErrorHandler

    monkeypatch.setattr(offline_model, "is_active", lambda: True)
    out = ErrorHandler.handle_manager_unavailable("query_executor")
    assert out["error_type"] == "offline_no_engine"
    monkeypatch.setattr(offline_model, "is_active", lambda: False)
    assert (
        ErrorHandler.handle_manager_unavailable("query_executor")["error_type"]
        == "manager_unavailable"
    )
