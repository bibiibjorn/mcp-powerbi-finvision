"""T1-D: tests for upsert / if_exists handling at the 02_Model_Operations
dispatcher layer.

The retry path lives in handle_model_operations and is exercised by mocking
_original_handle_model_operations to simulate the manager's responses.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from server.handlers import model_operations_handler as mod
from server.handlers.model_operations_handler import (
    _is_already_exists_error,
    handle_model_operations,
)


def test_is_already_exists_error_message_substring() -> None:
    assert _is_already_exists_error(
        {"success": False, "error": "Measure 'X' already exists in table 'T'"}
    )
    assert _is_already_exists_error({"success": False, "error_type": "duplicate_name"})
    assert not _is_already_exists_error({"success": True})
    assert not _is_already_exists_error({"success": False, "error": "Invalid expression"})


def test_create_with_if_exists_update_retries_on_duplicate(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: List[Dict[str, Any]] = []

    def fake_original(args: Dict[str, Any]) -> Dict[str, Any]:
        calls.append({k: v for k, v in args.items()})
        if args["operation"] == "create":
            return {"success": False, "error": "Measure 'X' already exists in table 'T'"}
        if args["operation"] == "update":
            return {"success": True, "action": "updated"}
        return {"success": False, "error": "unexpected op"}

    monkeypatch.setattr(mod, "_original_handle_model_operations", fake_original)
    monkeypatch.setattr(mod, "_severity_for", lambda *a, **k: None)  # skip gating

    res = handle_model_operations(
        {
            "object_type": "measure",
            "operation": "create",
            "table_name": "T",
            "measure_name": "X",
            "expression": "1",
            "if_exists": "update",
        }
    )
    assert res["success"] is True
    assert res["upserted"] is True
    assert res["if_exists_action"] == "update"
    # Two calls: create then update.
    assert [c["operation"] for c in calls] == ["create", "update"]


def test_create_without_if_exists_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default behaviour preserved: no retry, original error surfaces."""

    def fake_original(args: Dict[str, Any]) -> Dict[str, Any]:
        return {"success": False, "error": "Measure 'X' already exists"}

    monkeypatch.setattr(mod, "_original_handle_model_operations", fake_original)
    monkeypatch.setattr(mod, "_severity_for", lambda *a, **k: None)

    res = handle_model_operations(
        {
            "object_type": "measure",
            "operation": "create",
            "table_name": "T",
            "measure_name": "X",
            "expression": "1",
        }
    )
    assert res["success"] is False
    assert "upserted" not in res


def test_upsert_alias_routes_to_create_then_update(monkeypatch: pytest.MonkeyPatch) -> None:
    """operation='upsert' is a convenience alias for create+if_exists=update."""
    calls: List[Dict[str, Any]] = []

    def fake_original(args: Dict[str, Any]) -> Dict[str, Any]:
        calls.append(dict(args))
        if args["operation"] == "create":
            return {"success": False, "error": "Column 'X' already exists in table 'T'"}
        return {"success": True, "action": "updated"}

    monkeypatch.setattr(mod, "_original_handle_model_operations", fake_original)
    monkeypatch.setattr(mod, "_severity_for", lambda *a, **k: None)

    res = handle_model_operations(
        {
            "object_type": "column",
            "operation": "upsert",
            "table_name": "T",
            "column_name": "X",
        }
    )
    assert res["success"] is True
    assert res["upserted"] is True
    assert [c["operation"] for c in calls] == ["create", "update"]


def test_create_succeeds_first_try_no_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """When create succeeds, we don't retry."""
    calls: List[Dict[str, Any]] = []

    def fake_original(args: Dict[str, Any]) -> Dict[str, Any]:
        calls.append(dict(args))
        return {"success": True, "action": "created"}

    monkeypatch.setattr(mod, "_original_handle_model_operations", fake_original)
    monkeypatch.setattr(mod, "_severity_for", lambda *a, **k: None)

    res = handle_model_operations(
        {
            "object_type": "measure",
            "operation": "create",
            "table_name": "T",
            "measure_name": "X",
            "expression": "1",
            "if_exists": "update",
        }
    )
    assert res["success"] is True
    assert "upserted" not in res  # not a retry
    assert len(calls) == 1


def test_upsert_skipped_for_unsupported_object_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """if_exists='update' on hierarchy (no update op) does NOT retry."""
    calls: List[Dict[str, Any]] = []

    def fake_original(args: Dict[str, Any]) -> Dict[str, Any]:
        calls.append(dict(args))
        return {"success": False, "error": "Hierarchy 'H' already exists"}

    monkeypatch.setattr(mod, "_original_handle_model_operations", fake_original)
    monkeypatch.setattr(mod, "_severity_for", lambda *a, **k: None)

    res = handle_model_operations(
        {
            "object_type": "hierarchy",
            "operation": "create",
            "table_name": "T",
            "hierarchy_name": "H",
            "if_exists": "update",
        }
    )
    assert res["success"] is False
    assert len(calls) == 1  # no retry
