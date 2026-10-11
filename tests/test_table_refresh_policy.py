"""02_Model_Operations table refresh_policy CRUD (Phase 5 quick win).

get/set/clear of table.RefreshPolicy (incremental-refresh authoring). Tests
exercise the pure helpers with fake TOM objects (no live AMO) and the read
serializer.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from core.operations.table_crud_manager import TableCRUDManager


def _table(name, policy=None):
    t = SimpleNamespace()
    t.Name = name
    t.RefreshPolicy = policy
    return t


def _model(tables):
    m = MagicMock()
    m.Tables = list(tables)
    return m


def test_get_refresh_policy_dict_none_when_absent():
    mgr = TableCRUDManager(connection=MagicMock())
    out = mgr._refresh_policy_to_dict(None)
    assert out is None


def test_get_refresh_policy_dict_serializes_fields():
    mgr = TableCRUDManager(connection=MagicMock())
    policy = SimpleNamespace(
        RollingWindowGranularity="Year",
        RollingWindowPeriods=5,
        IncrementalGranularity="Day",
        IncrementalPeriods=10,
        SourceExpression="RangeStart..RangeEnd",
    )
    out = mgr._refresh_policy_to_dict(policy)
    assert out["rolling_window_granularity"] == "Year"
    assert out["rolling_window_periods"] == 5
    assert out["incremental_granularity"] == "Day"
    assert out["incremental_periods"] == 10


def test_apply_clear_refresh_policy_sets_none():
    mgr = TableCRUDManager(connection=MagicMock())
    tbl = _table("Sales", policy=SimpleNamespace())
    model = _model([tbl])
    res = mgr._apply_clear_refresh_policy(model, table_name="Sales")
    assert res["success"] is True
    assert tbl.RefreshPolicy is None
    model.SaveChanges.assert_not_called()


def test_apply_clear_refresh_policy_table_not_found():
    mgr = TableCRUDManager(connection=MagicMock())
    model = _model([])
    res = mgr._apply_clear_refresh_policy(model, table_name="Ghost")
    assert res["success"] is False
    assert res["error_type"] == "table_not_found"


def test_get_refresh_policy_public_reads_without_save():
    from unittest.mock import patch

    mgr = TableCRUDManager(connection=MagicMock())
    policy = SimpleNamespace(
        RollingWindowGranularity="Month",
        RollingWindowPeriods=12,
        IncrementalGranularity="Day",
        IncrementalPeriods=3,
        SourceExpression=None,
    )
    tbl = _table("Sales", policy=policy)
    server, db, model = MagicMock(), MagicMock(), _model([tbl])
    with patch.object(mgr, "_get_server_db_model", return_value=(server, db, model)):
        res = mgr.get_refresh_policy("Sales")
    assert res["success"] is True
    assert res["refresh_policy"]["rolling_window_periods"] == 12
    model.SaveChanges.assert_not_called()
    server.Disconnect.assert_called_once()
