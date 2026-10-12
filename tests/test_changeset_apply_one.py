"""Per-op dispatch tests for BatchOperationsHandler._changeset_apply_one.

This is the single-op executor apply_changeset loops over. It forwards an opaque
`model` to the right CRUD manager's `_apply_*` helper based on (object_type, op).
We fake the three managers on the module-level connection_state singleton so no
CLR / live model is needed; the `model` is a sentinel that must be passed through
verbatim as the first positional arg.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import core.operations.batch_operations as bmod
from core.operations.batch_operations import BatchOperationsHandler


class _FakeConn:
    def __init__(self):
        self.table_crud_manager = MagicMock()
        self.column_crud_manager = MagicMock()
        self.relationship_crud_manager = MagicMock()


@pytest.fixture
def handler(monkeypatch):
    fake = _FakeConn()
    # Make every _apply_* return a success dict by default.
    fake.table_crud_manager._apply_create_table.return_value = {"success": True}
    fake.table_crud_manager._apply_update_table.return_value = {"success": True}
    fake.table_crud_manager._apply_delete_table.return_value = {"success": True}
    fake.column_crud_manager._apply_create_column.return_value = {"success": True}
    fake.column_crud_manager._apply_update_column.return_value = {"success": True}
    fake.column_crud_manager._apply_delete_column.return_value = {"success": True}
    fake.relationship_crud_manager._apply_create_relationship.return_value = {"success": True}
    fake.relationship_crud_manager._apply_update_relationship.return_value = {"success": True}
    fake.relationship_crud_manager._apply_delete_relationship.return_value = {"success": True}
    monkeypatch.setattr(bmod, "connection_state", fake)
    return BatchOperationsHandler(), fake


SENTINEL = object()


def test_table_create_dispatch(handler):
    h, fake = handler
    out = h._changeset_apply_one(SENTINEL, "tables", "create", {"table": "T", "expression": "e"})
    assert out["success"] is True
    fake.table_crud_manager._apply_create_table.assert_called_once()
    # model passed through as first positional arg.
    assert fake.table_crud_manager._apply_create_table.call_args.args[0] is SENTINEL


def test_table_delete_dispatch(handler):
    h, fake = handler
    h._changeset_apply_one(SENTINEL, "tables", "delete", {"table_name": "T"})
    fake.table_crud_manager._apply_delete_table.assert_called_once()


def test_column_update_dispatch(handler):
    h, fake = handler
    h._changeset_apply_one(
        SENTINEL, "columns", "update", {"table": "T", "column": "C", "new_name": "C2"}
    )
    fake.column_crud_manager._apply_update_column.assert_called_once()


def test_column_create_dispatch(handler):
    h, fake = handler
    h._changeset_apply_one(SENTINEL, "columns", "create", {"table": "T", "column": "C"})
    fake.column_crud_manager._apply_create_column.assert_called_once()


def test_relationship_create_dispatch(handler):
    h, fake = handler
    h._changeset_apply_one(
        SENTINEL,
        "relationships",
        "create",
        {"from_table": "F", "from_column": "c", "to_table": "T", "to_column": "k"},
    )
    fake.relationship_crud_manager._apply_create_relationship.assert_called_once()


def test_relationship_delete_dispatch(handler):
    h, fake = handler
    h._changeset_apply_one(SENTINEL, "relationships", "delete", {"relationship_name": "R"})
    fake.relationship_crud_manager._apply_delete_relationship.assert_called_once()


def test_unsupported_object_type_fallthrough(handler):
    h, _ = handler
    out = h._changeset_apply_one(SENTINEL, "bogus", "create", {})
    assert out["success"] is False
    assert "unsupported" in out["error"]


def test_unsupported_op_fallthrough(handler):
    h, _ = handler
    out = h._changeset_apply_one(SENTINEL, "tables", "frobnicate", {"table": "T"})
    assert out["success"] is False
    assert "unsupported" in out["error"]
