"""Tests for 02_TMDL_Operations:format_dax dispatch."""

import pytest

from server.handlers import tmdl_handler


@pytest.fixture
def formatter_enabled(monkeypatch):
    # Review D-20: sending DAX to daxformatter.com is opt-in.
    monkeypatch.setenv("MCP_DAX_FORMATTER_ENABLED", "1")


def test_format_dax_requires_targets(monkeypatch, formatter_enabled):
    from core.infrastructure.connection_state import connection_state

    monkeypatch.setattr(connection_state, "is_connected", lambda: True)
    res = tmdl_handler.handle_tmdl_operations({"operation": "format_dax"})
    assert res["success"] is False
    assert "measure_refs" in res["error"]


def test_format_dax_disconnected_errors(monkeypatch, formatter_enabled):
    from core.infrastructure.connection_state import connection_state

    monkeypatch.setattr(connection_state, "is_connected", lambda: False)
    res = tmdl_handler.handle_tmdl_operations({"operation": "format_dax", "format_all": True})
    assert res["success"] is False
