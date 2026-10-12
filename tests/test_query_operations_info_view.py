"""Tests for core.dax.info_view.info_view.

Mocks connection_state.is_connected() + query_executor to cover:
- success path (INFO.VIEW returns rows, source="INFO.VIEW")
- DMV fallback path on `unrecognized` error message
- DMV fallback path on raw exception from validate_and_execute_dax
- invalid view name → invalid_args
- not connected → not_connected
- top_n truncation flag on the INFO.VIEW success path
- capability cache short-circuits to DMV on second call
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.dax import info_view as iv_mod


@pytest.fixture
def fake_state(monkeypatch):
    """Install a fresh mock connection_state on the info_view module's import path."""
    state = MagicMock()
    state.is_connected.return_value = True
    state.query_executor = MagicMock()
    # Reset capability cache between tests
    state._info_view_supported = None

    # Patch the module the wrapper imports inside the function
    import core.infrastructure.connection_state as cs_mod

    monkeypatch.setattr(cs_mod, "connection_state", state)
    return state


def test_info_view_success(fake_state):
    fake_state.query_executor.validate_and_execute_dax.return_value = {
        "success": True,
        "rows": [{"Name": "Total Sales"}, {"Name": "Sales LY"}],
        "columns": ["Name"],
    }

    result = iv_mod.info_view("measures", top_n=10)

    assert result["success"] is True
    assert result["source"] == "INFO.VIEW"
    assert result["view"] == "measures"
    assert result["row_count"] == 2
    assert result["truncated"] is False
    assert fake_state._info_view_supported is True


def test_info_view_dmv_fallback_on_unrecognized(fake_state):
    fake_state.query_executor.validate_and_execute_dax.return_value = {
        "success": False,
        "error": "INFO.VIEW.MEASURES is unrecognized in this model",
    }

    def dmv(sql):
        if "TMSCHEMA_TABLES" in sql:
            return {"success": True, "data": [{"ID": 7, "Name": "Sales"}]}
        return {"success": True, "data": [{"ID": 1, "Name": "X", "TableID": 7, "Expression": "1"}]}

    fake_state.query_executor.execute_dmv_query.side_effect = dmv

    result = iv_mod.info_view("measures")

    assert result["success"] is True
    assert result["source"] == "DMV_FALLBACK"
    assert fake_state._info_view_supported is False
    # E-26: normalised to the INFO.VIEW column names, table id resolved.
    assert result["rows"][0]["Name"] == "X" and result["rows"][0]["Table"] == "Sales"


def test_info_view_dmv_fallback_on_exception(fake_state):
    fake_state.query_executor.validate_and_execute_dax.side_effect = RuntimeError(
        "function not found"
    )
    fake_state.query_executor.execute_dmv_query.return_value = {
        "success": True,
        "data": [{"ID": 3, "Name": "Sales"}],
        "columns": ["ID", "Name"],
    }

    result = iv_mod.info_view("tables")

    assert result["success"] is True
    assert result["source"] == "DMV_FALLBACK"
    # E-26: a transient exception falls back for THIS call only.
    assert fake_state._info_view_supported is None


def test_info_view_generic_error_does_not_disable_info_view(fake_state):
    fake_state.query_executor.validate_and_execute_dax.return_value = {
        "success": False,
        "error": "",
    }
    result = iv_mod.info_view("tables")
    assert result["success"] is False
    assert fake_state._info_view_supported is None


def test_info_view_reads_bypass_the_cache(fake_state):
    fake_state.query_executor.validate_and_execute_dax.return_value = {
        "success": True,
        "rows": [],
        "columns": [],
    }
    iv_mod.info_view("tables")
    kwargs = fake_state.query_executor.validate_and_execute_dax.call_args.kwargs
    assert kwargs.get("bypass_cache") is True


def test_info_view_invalid_view_name(fake_state):
    result = iv_mod.info_view("not_a_real_view")
    assert result["success"] is False
    assert result["error_type"] == "invalid_args"


def test_info_view_not_connected(fake_state):
    fake_state.is_connected.return_value = False
    result = iv_mod.info_view("measures")
    assert result["success"] is False
    assert result["error_type"] == "not_connected"


def test_info_view_truncation_flag(fake_state):
    """When INFO.VIEW returns >= top_n rows, truncated is True."""
    fake_state.query_executor.validate_and_execute_dax.return_value = {
        "success": True,
        "rows": [{"i": 1}, {"i": 2}],
        "columns": ["i"],
    }
    result = iv_mod.info_view("columns", top_n=2)
    assert result["truncated"] is True


def test_info_view_capability_cache_skips_retry(fake_state):
    """Once `_info_view_supported = False`, subsequent calls go straight to DMV."""
    fake_state._info_view_supported = False
    fake_state.query_executor.execute_dmv_query.return_value = {
        "success": True,
        "data": [],
        "columns": [],
    }

    result = iv_mod.info_view("relationships")

    assert result["success"] is True
    assert result["source"] == "DMV_FALLBACK"
    fake_state.query_executor.validate_and_execute_dax.assert_not_called()


def test_info_view_dmv_fallback_failure_returns_error(fake_state):
    """Both INFO.VIEW and DMV fail → info_view_unsupported envelope."""
    fake_state.query_executor.validate_and_execute_dax.side_effect = RuntimeError("nope")
    fake_state.query_executor.execute_dmv_query.side_effect = RuntimeError("dmv broken")

    result = iv_mod.info_view("measures")
    assert result["success"] is False
    assert result["error_type"] == "info_view_unsupported"
