"""_apply_* helpers mutate a model without connecting/saving (audit H1 enabler)."""

from unittest.mock import MagicMock


class _FakeColumns(list):
    def Add(self, c):
        self.append(c)

    def Remove(self, c):
        list.remove(self, c)


def _fake_table(name, columns=None):
    t = MagicMock()
    t.Name = name
    t.Columns = _FakeColumns(columns or [])
    return t


def _fake_model(tables=None):
    m = MagicMock()
    m.Tables = list(tables or [])
    return m


def test_apply_create_table_not_found_without_saving():
    from core.operations.column_crud_manager import ColumnCRUDManager

    mgr = ColumnCRUDManager(connection=MagicMock())
    model = _fake_model()  # no tables
    res = mgr._apply_create_column(model, table_name="Ghost", column_name="C")
    assert res["success"] is False
    assert res["error_type"] == "table_not_found"
    model.SaveChanges.assert_not_called()


def test_apply_update_column_not_found_without_saving():
    from core.operations.column_crud_manager import ColumnCRUDManager

    mgr = ColumnCRUDManager(connection=MagicMock())
    table = _fake_table("T")  # no columns
    model = _fake_model([table])
    res = mgr._apply_update_column(model, table_name="T", column_name="Ghost", description="x")
    assert res["success"] is False
    assert res["error_type"] == "column_not_found"
    model.SaveChanges.assert_not_called()


def test_apply_delete_column_not_found_without_saving():
    from core.operations.column_crud_manager import ColumnCRUDManager

    mgr = ColumnCRUDManager(connection=MagicMock())
    table = _fake_table("T")  # no columns
    model = _fake_model([table])
    res = mgr._apply_delete_column(model, table_name="T", column_name="Ghost")
    assert res["success"] is False
    assert res["error_type"] == "column_not_found"
    model.SaveChanges.assert_not_called()


def test_public_method_disconnects_when_helper_raises():
    """The whole refactor relies on the public finally still disconnecting."""
    from unittest.mock import patch
    from core.operations.column_crud_manager import ColumnCRUDManager

    mgr = ColumnCRUDManager(connection=MagicMock())
    server, db, model = MagicMock(), MagicMock(), MagicMock()
    with patch.object(mgr, "_get_server_db_model", return_value=(server, db, model)), patch.object(
        mgr, "_apply_update_column", side_effect=RuntimeError("boom")
    ):
        res = mgr.update_column(table_name="T", column_name="C")
    assert res["success"] is False
    server.Disconnect.assert_called_once()
