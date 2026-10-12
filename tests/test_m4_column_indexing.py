"""Sep-2026 ``column update string_indexing_behavior / full_text_indexing_behavior``.

The TOM enum is injected through ``sys.modules`` (Microsoft + Microsoft.AnalysisServices
parents chained); the model is a plain fake. Guard rails under test: value validation, String
columns only, client support (reflection), compatibility level gates (1707 / 1708) and that the
level is NEVER raised implicitly.
"""

from __future__ import annotations

import sys
import types

import pytest

from core.operations.column_crud_manager import ColumnCRUDManager
from core.operations.column_indexing import apply_indexing_behavior
from tests._c_fake_tom import Coll, Obj, table


class IndexingBehavior:
    Auto = "IB.Auto"
    Full = "IB.Full"
    Explicit = "IB.Explicit"
    Off = "IB.Off"


@pytest.fixture
def tom(monkeypatch):
    tabular = types.ModuleType("Microsoft.AnalysisServices.Tabular")
    tabular.IndexingBehavior = IndexingBehavior
    analysis = types.ModuleType("Microsoft.AnalysisServices")
    analysis.Tabular = tabular
    root = types.ModuleType("Microsoft")
    root.AnalysisServices = analysis
    monkeypatch.setitem(sys.modules, "Microsoft", root)
    monkeypatch.setitem(sys.modules, "Microsoft.AnalysisServices", analysis)
    monkeypatch.setitem(sys.modules, "Microsoft.AnalysisServices.Tabular", tabular)
    return tabular


def model(level=1708, dtype="DataType.String", with_props=True):
    col = Obj(Name="NAME_HOTEL", DataType=dtype, Expression=None, SortByColumn=None)
    if with_props:
        col.StringIndexingBehavior = IndexingBehavior.Auto
        col.FullTextIndexingBehavior = IndexingBehavior.Off
    m = Obj(Tables=Coll([table("Hotels", columns=[col])]), Database=Obj(CompatibilityLevel=level))
    return m, col


def test_string_indexing_is_set_on_a_string_column(tom):
    m, col = model(level=1707)
    updates, err = apply_indexing_behavior(m, col, string_indexing_behavior="Full")
    assert err is None and updates == ["string_indexing_behavior=full"]
    assert col.StringIndexingBehavior == IndexingBehavior.Full


def test_both_behaviours_in_one_call(tom):
    m, col = model(level=1708)
    updates, err = apply_indexing_behavior(
        m, col, string_indexing_behavior="explicit", full_text_indexing_behavior="full"
    )
    assert err is None and len(updates) == 2
    assert col.StringIndexingBehavior == IndexingBehavior.Explicit
    assert col.FullTextIndexingBehavior == IndexingBehavior.Full


@pytest.mark.parametrize(
    "kwargs",
    [
        {"string_indexing_behavior": "sometimes"},
        {"full_text_indexing_behavior": "auto"},  # there is no auto for full-text
        {"full_text_indexing_behavior": ""},
    ],
)
def test_invalid_values_are_rejected_with_the_valid_list(tom, kwargs):
    m, col = model()
    updates, err = apply_indexing_behavior(m, col, **kwargs)
    assert updates == [] and err["error_type"] == "invalid_parameters" and err["valid_values"]
    assert col.StringIndexingBehavior == IndexingBehavior.Auto


def test_only_string_columns(tom):
    m, col = model(dtype="DataType.Int64")
    updates, err = apply_indexing_behavior(m, col, string_indexing_behavior="full")
    assert updates == [] and err["error_type"] == "invalid_column_type"
    assert col.StringIndexingBehavior == IndexingBehavior.Auto


@pytest.mark.parametrize(
    "level,kwargs,required",
    [
        (1606, {"string_indexing_behavior": "full"}, 1707),
        (1706, {"string_indexing_behavior": "explicit"}, 1707),
        (1707, {"full_text_indexing_behavior": "full"}, 1708),
        (1600, {"full_text_indexing_behavior": "explicit"}, 1708),
    ],
)
def test_low_compatibility_level_is_refused_and_never_upgraded(tom, level, kwargs, required):
    m, col = model(level=level)
    updates, err = apply_indexing_behavior(m, col, **kwargs)
    assert updates == [] and err["error_type"] == "compatibility_level_too_low"
    assert err["required_compatibility_level"] == required and err["compatibility_level"] == level
    assert "set_properties" in err["suggestion"] and "confirm_destructive" in err["suggestion"]
    assert m.Database.CompatibilityLevel == level  # NEVER auto-upgraded
    assert col.StringIndexingBehavior == IndexingBehavior.Auto
    assert col.FullTextIndexingBehavior == IndexingBehavior.Off


def test_client_without_the_property_is_feature_unsupported(tom):
    m, col = model(with_props=False)
    updates, err = apply_indexing_behavior(m, col, string_indexing_behavior="full")
    assert updates == [] and err["error_type"] == "feature_unsupported_by_client"
    assert "19.117" in err["error"] and err["required_client"]


def test_client_without_the_enum_is_feature_unsupported(monkeypatch, tom):
    monkeypatch.delattr(tom, "IndexingBehavior")
    m, col = model()
    updates, err = apply_indexing_behavior(m, col, string_indexing_behavior="full")
    assert err["error_type"] == "feature_unsupported_by_client"


def test_nothing_requested_is_a_noop(tom):
    m, col = model()
    assert apply_indexing_behavior(m, col) == ([], None)


def test_unknown_compatibility_level_does_not_block(tom):
    m, col = model()
    m.Database = Obj()  # no CompatibilityLevel readable
    updates, err = apply_indexing_behavior(m, col, string_indexing_behavior="off")
    assert err is None and col.StringIndexingBehavior == IndexingBehavior.Off


# --- through the column manager --------------------------------------------------------


def test_apply_update_column_threads_the_new_arguments(tom):
    m, col = model(level=1707)
    mgr = ColumnCRUDManager(None)
    res = mgr._apply_update_column(
        m,
        table_name="Hotels",
        column_name="NAME_HOTEL",
        description="searchable",
        string_indexing_behavior="full",
    )
    assert res["success"] is True
    assert "string_indexing_behavior=full" in res["updates"]
    assert col.StringIndexingBehavior == IndexingBehavior.Full


def test_apply_update_column_reports_the_guard_error(tom):
    m, col = model(level=1600)
    mgr = ColumnCRUDManager(None)
    res = mgr._apply_update_column(
        m, table_name="Hotels", column_name="NAME_HOTEL", string_indexing_behavior="full"
    )
    assert res["success"] is False and res["error_type"] == "compatibility_level_too_low"


def test_column_operations_handler_forwards_the_arguments(monkeypatch):
    from core.operations.column_operations import ColumnOperationsHandler

    seen = {}
    fake = types.SimpleNamespace(update_column=lambda **kw: seen.update(kw) or {"success": True})
    monkeypatch.setattr(
        "core.operations.column_operations.get_manager_or_error", lambda name, *a: fake
    )
    ColumnOperationsHandler().execute(
        {
            "operation": "update",
            "table_name": "Hotels",
            "column_name": "NAME_HOTEL",
            "string_indexing_behavior": "full",
            "full_text_indexing_behavior": "explicit",
        }
    )
    assert seen["string_indexing_behavior"] == "full"
    assert seen["full_text_indexing_behavior"] == "explicit"


def test_get_column_reads_the_indexing_behaviour_back(monkeypatch, tom):
    import core.operations.column_crud_manager as ccm

    monkeypatch.setattr(ccm, "AMO_AVAILABLE", True)
    m, col = model(level=1708)
    col.StringIndexingBehavior = IndexingBehavior.Explicit
    col.Description = ""
    col.IsHidden = False
    col.DisplayFolder = ""
    col.FormatString = ""
    col.IsKey = False
    mgr = ColumnCRUDManager(None)
    mgr._get_server_db_model = lambda: (
        types.SimpleNamespace(Disconnect=lambda: None),
        m.Database,
        m,
    )
    out = mgr.get_column("Hotels", "NAME_HOTEL")
    assert out["success"] is True
    info = out.get("column") or out
    assert info["string_indexing_behavior"] == "explicit"
    assert info["full_text_indexing_behavior"] == "off"
