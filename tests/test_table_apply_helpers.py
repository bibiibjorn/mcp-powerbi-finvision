"""_apply_* helpers mutate a model without connecting/saving (audit H1 enabler)."""

from unittest.mock import MagicMock


class _FakeTables(list):
    def Add(self, t):
        self.append(t)


def _fake_model():
    m = MagicMock()
    m.Tables = _FakeTables()
    return m


def test_apply_create_duplicate_rejected_without_saving():
    from core.operations.table_crud_manager import TableCRUDManager

    mgr = TableCRUDManager(connection=MagicMock())
    model = _fake_model()
    existing = MagicMock()
    existing.Name = "Dup"
    model.Tables.append(existing)

    res = mgr._apply_create_table(
        model, table_name="Dup", description=None, expression=None, hidden=False
    )
    assert res["success"] is False
    assert res["error_type"] == "table_exists"
    model.SaveChanges.assert_not_called()


def test_apply_delete_not_found_without_saving():
    from core.operations.table_crud_manager import TableCRUDManager

    mgr = TableCRUDManager(connection=MagicMock())
    model = _fake_model()  # no tables
    res = mgr._apply_delete_table(model, table_name="Ghost")
    assert res["success"] is False
    model.SaveChanges.assert_not_called()


def test_apply_update_not_found_without_saving():
    from core.operations.table_crud_manager import TableCRUDManager

    mgr = TableCRUDManager(connection=MagicMock())
    model = _fake_model()  # no tables
    res = mgr._apply_update_table(model, table_name="Ghost", description="x")
    assert res["success"] is False
    assert res["error_type"] == "table_not_found"
    model.SaveChanges.assert_not_called()


def test_apply_rename_not_found_without_saving():
    from core.operations.table_crud_manager import TableCRUDManager

    mgr = TableCRUDManager(connection=MagicMock())
    model = _fake_model()  # no tables
    res = mgr._apply_rename_table(model, table_name="Ghost", new_name="New")
    assert res["success"] is False
    model.SaveChanges.assert_not_called()


def test_public_method_disconnects_when_helper_raises():
    """The whole refactor relies on the public finally still disconnecting."""
    from unittest.mock import patch
    from core.operations.table_crud_manager import TableCRUDManager

    mgr = TableCRUDManager(connection=MagicMock())
    server, db, model = MagicMock(), MagicMock(), MagicMock()
    with patch.object(mgr, "_get_server_db_model", return_value=(server, db, model)), patch.object(
        mgr, "_apply_update_table", side_effect=RuntimeError("boom")
    ):
        res = mgr.update_table(table_name="T")
    assert res["success"] is False
    server.Disconnect.assert_called_once()
