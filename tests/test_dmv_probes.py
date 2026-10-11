"""Tests for canned DMV / INFO.* probe definitions and executor."""

from unittest.mock import MagicMock, patch

import pytest

from core.dax.dmv_probes import (
    PROBE_REGISTRY,
    list_probes,
    run_probe,
    ProbeNotFoundError,
)


def test_known_probes_are_registered():
    names = {p["name"] for p in list_probes()}
    expected = {
        "vertipaq_columns",
        "vertipaq_dictionary",
        "calc_dependency",
        "object_memory_usage",
        "object_activity",
        "sessions",
        "user_defined_functions",
        "refresh_policies",
    }
    assert expected <= names


def test_each_probe_entry_has_required_fields():
    for entry in list_probes():
        assert isinstance(entry["name"], str)
        assert isinstance(entry["description"], str)
        assert isinstance(entry["dax"], str)
        assert entry["dax"].strip().upper().startswith(("EVALUATE", "SELECT"))


def test_run_probe_unknown_name_raises():
    with pytest.raises(ProbeNotFoundError):
        run_probe("does_not_exist")


@patch("core.dax.dmv_probes.connection_state")
def test_run_probe_executes_dax_against_query_executor(mock_state):
    """EVALUATE-prefix probes go through validate_and_execute_dax."""
    qx = MagicMock()
    qx.validate_and_execute_dax.return_value = {
        "success": True,
        "rows": [{"col": "x"}],
        "row_count": 1,
    }
    mock_state.query_executor = qx

    result = run_probe("vertipaq_columns")

    assert result["success"] is True
    assert result["probe"] == "vertipaq_columns"
    assert result["row_count"] == 1
    qx.validate_and_execute_dax.assert_called_once()
    invoked_dax = qx.validate_and_execute_dax.call_args[0][0]
    assert "INFO.STORAGETABLECOLUMNS" in invoked_dax.upper()


@patch("core.dax.dmv_probes.connection_state")
def test_run_probe_executes_dmv_against_dmv_executor(mock_state):
    """SELECT-prefix probes go through execute_dmv_query."""
    qx = MagicMock()
    qx.execute_dmv_query.return_value = {"success": True, "rows": [], "row_count": 0}
    mock_state.query_executor = qx

    result = run_probe("sessions")

    assert result["success"] is True
    qx.execute_dmv_query.assert_called_once()
    qx.validate_and_execute_dax.assert_not_called()
    invoked = qx.execute_dmv_query.call_args[0][0]
    assert "DISCOVER_SESSIONS" in invoked.upper()


@patch("core.dax.dmv_probes.connection_state")
def test_run_probe_no_connection(mock_state):
    mock_state.query_executor = None
    result = run_probe("sessions")
    assert result["success"] is False
    assert "not connected" in result["error"].lower()


@patch("core.dax.dmv_probes.connection_state")
def test_run_probe_metadata_wins_over_executor_keys(mock_state):
    """If query result happens to contain 'probe' or 'dax' keys, metadata still wins."""
    qx = MagicMock()
    qx.validate_and_execute_dax.return_value = {
        "success": True,
        "rows": [],
        "probe": "WRONG",
        "dax": "WRONG",
    }
    mock_state.query_executor = qx
    result = run_probe("vertipaq_columns")
    assert result["probe"] == "vertipaq_columns"
    assert result["dax"] == PROBE_REGISTRY["vertipaq_columns"]["dax"]


@patch("core.dax.dmv_probes.connection_state")
def test_run_probe_soft_fails_on_executor_exception(mock_state):
    """Exceptions from query_executor are converted to {success: False, error}."""
    qx = MagicMock()
    qx.execute_dmv_query.side_effect = RuntimeError("DMV not supported on this build")
    mock_state.query_executor = qx
    result = run_probe("sessions")
    assert result["success"] is False
    assert "DMV not supported" in result["error"]
    assert result["probe"] == "sessions"
    assert result["dax"] == PROBE_REGISTRY["sessions"]["dax"]


def test_list_probes_matches_registry():
    """list_probes() must enumerate exactly PROBE_REGISTRY (no drift)."""
    assert {p["name"] for p in list_probes()} == set(PROBE_REGISTRY)
