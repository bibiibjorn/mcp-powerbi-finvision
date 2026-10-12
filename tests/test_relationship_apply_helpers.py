"""_apply_* helpers mutate a model without connecting/saving (audit H1 enabler)."""

from unittest.mock import MagicMock


class _FakeRelationships(list):
    def Add(self, r):
        self.append(r)

    def Remove(self, r):
        list.remove(self, r)


def _fake_model(relationships=None, tables=None):
    m = MagicMock()
    m.Relationships = _FakeRelationships(relationships or [])
    m.Tables = list(tables or [])
    return m


def _fake_rel(name):
    r = MagicMock()
    r.Name = name
    return r


def test_apply_create_source_table_not_found_without_saving():
    from core.operations.relationship_crud_manager import RelationshipCRUDManager

    mgr = RelationshipCRUDManager(connection=MagicMock())
    model = _fake_model()  # no tables
    res = mgr._apply_create_relationship(
        model,
        from_table="Ghost",
        from_column="c",
        to_table="Dim",
        to_column="k",
    )
    assert res["success"] is False
    assert res["error_type"] == "table_not_found"
    model.SaveChanges.assert_not_called()


def test_apply_update_relationship_not_found_without_saving():
    from core.operations.relationship_crud_manager import RelationshipCRUDManager

    mgr = RelationshipCRUDManager(connection=MagicMock())
    model = _fake_model()  # no relationships
    res = mgr._apply_update_relationship(model, relationship_name="Ghost", is_active=False)
    assert res["success"] is False
    assert res["error_type"] == "relationship_not_found"
    model.SaveChanges.assert_not_called()


def test_apply_delete_relationship_not_found_without_saving():
    from core.operations.relationship_crud_manager import RelationshipCRUDManager

    mgr = RelationshipCRUDManager(connection=MagicMock())
    model = _fake_model()  # no relationships
    res = mgr._apply_delete_relationship(model, relationship_name="Ghost")
    assert res["success"] is False
    assert res["error_type"] == "relationship_not_found"
    model.SaveChanges.assert_not_called()


def test_apply_activate_toggles_is_active():
    from core.operations.relationship_crud_manager import RelationshipCRUDManager

    mgr = RelationshipCRUDManager(connection=MagicMock())
    rel = _fake_rel("R")
    model = _fake_model([rel])
    res = mgr._apply_activate_relationship(model, relationship_name="R")
    assert res["success"] is True
    assert rel.IsActive is True
    assert "is_active" in res["updates"]
    model.SaveChanges.assert_not_called()


def test_apply_deactivate_toggles_is_active():
    from core.operations.relationship_crud_manager import RelationshipCRUDManager

    mgr = RelationshipCRUDManager(connection=MagicMock())
    rel = _fake_rel("R")
    model = _fake_model([rel])
    res = mgr._apply_deactivate_relationship(model, relationship_name="R")
    assert res["success"] is True
    assert rel.IsActive is False
    assert "is_active" in res["updates"]
    model.SaveChanges.assert_not_called()


def test_public_method_disconnects_when_helper_raises():
    """The whole refactor relies on the public finally still disconnecting."""
    from unittest.mock import patch
    from core.operations.relationship_crud_manager import RelationshipCRUDManager

    mgr = RelationshipCRUDManager(connection=MagicMock())
    server, db, model = MagicMock(), MagicMock(), MagicMock()
    with patch.object(mgr, "_get_server_db_model", return_value=(server, db, model)), patch.object(
        mgr, "_apply_update_relationship", side_effect=RuntimeError("boom")
    ):
        res = mgr.update_relationship(relationship_name="R")
    assert res["success"] is False
    server.Disconnect.assert_called_once()
