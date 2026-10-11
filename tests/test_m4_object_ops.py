"""MP-01 / QG-01 object operations, manager level with TOM faked:
``table mark_as_date_table``, ``hierarchy add_level|remove_level|rename_level``,
``role get_effective_permissions``, ``query_group`` CRUD, ``calculation_group update|rename``,
``relationship rename``."""

from __future__ import annotations

import sys
import types

import pytest

from core.operations import hierarchy_crud_manager as hmod
from core.operations import query_group_crud_manager as qgm
from core.operations import rls_role_crud_manager as rmod
from core.operations import table_crud_manager as tcm
from core.operations.calculation_group_manager import CalculationGroupManager
from core.operations.calculation_group_operations import CalculationGroupOperationsHandler
from core.operations.query_group_crud_manager import QueryGroupCrudManager
from core.operations.relationship_operations import RelationshipOperationsHandler
from tests._c_fake_tom import Coll, FakeServer, Obj


class Model:
    def __init__(self, tables=(), **extra):
        self.Tables = Coll(tables)
        self.Roles = Coll()
        self.Expressions = Coll()
        self.QueryGroups = Coll()
        self.saves = 0
        self.undos = 0
        self.save_error = None
        self.Database = Obj(CompatibilityLevel=1702)
        self.__dict__.update(extra)

    def SaveChanges(self):
        if self.save_error:
            raise self.save_error
        self.saves += 1

    def UndoLocalChanges(self):
        self.undos += 1


def patch_amo(monkeypatch, model):
    import core.operations.amo_helpers as amo

    srv = FakeServer()
    monkeypatch.setattr(amo, "get_server_db_model", lambda cs: (srv, model.Database, model, None))
    return srv


@pytest.fixture
def fake_tom(monkeypatch):
    """`import Microsoft.AnalysisServices.Tabular as TOM` resolves to a fake with Level/QueryGroup."""

    class Level(Obj):
        def __init__(self):
            super().__init__(Name=None, Ordinal=0, Column=None)

    class QueryGroup(Obj):
        def __init__(self):
            super().__init__(Folder=None, Description=None)

        @property
        def Name(self):
            return self.Folder

    tabular = types.ModuleType("Microsoft.AnalysisServices.Tabular")
    tabular.Level = Level
    tabular.QueryGroup = QueryGroup
    analysis = types.ModuleType("Microsoft.AnalysisServices")
    analysis.Tabular = tabular
    root = types.ModuleType("Microsoft")
    root.AnalysisServices = analysis
    monkeypatch.setitem(sys.modules, "Microsoft", root)
    monkeypatch.setitem(sys.modules, "Microsoft.AnalysisServices", analysis)
    monkeypatch.setitem(sys.modules, "Microsoft.AnalysisServices.Tabular", tabular)
    return tabular


# ---------------------------------------------------------------------------
# table.mark_as_date_table
# ---------------------------------------------------------------------------


def date_table(dtype="DataType.DateTime", other_key=False):
    d = Obj(Name="Date", DataType=dtype, IsKey=False)
    cols = [d, Obj(Name="Year", DataType="DataType.Int64", IsKey=other_key)]
    return Obj(Name="DimDate", Columns=Coll(cols), DataCategory=None, Partitions=Coll()), d


def stats(**over):
    base = {
        "rows": 1461,
        "distinct_values": 1461,
        "blanks": 0,
        "min_day": 43831,
        "max_day": 45291,
        "with_time": 0,
        "distinct_days": 1461,
    }
    base.update(over)
    return {f"[{k}]": v for k, v in base.items()}


class QE:
    def __init__(self, row=None, result=None):
        self.queries = []
        self._result = result or {"success": True, "rows": [row if row is not None else stats()]}

    def validate_and_execute_dax(self, q, **kw):
        self.queries.append((q, kw))
        return self._result


def date_mgr(monkeypatch, table, qe):
    monkeypatch.setattr(tcm, "AMO_AVAILABLE", True)
    model = Model([table])
    mgr = tcm.TableCRUDManager(None)
    srv = FakeServer()
    mgr._get_server_db_model = lambda: (srv, model.Database, model)
    import core.infrastructure.connection_state as cs

    monkeypatch.setattr(cs, "connection_state", types.SimpleNamespace(query_executor=qe))
    return mgr, model, srv


def test_mark_as_date_table_validates_then_marks_in_one_save(monkeypatch):
    table, col = date_table()
    qe = QE()
    mgr, model, srv = date_mgr(monkeypatch, table, qe)
    out = mgr.mark_as_date_table("DimDate", "Date")
    assert out["success"] is True and model.saves == 1 and srv.disconnected == 1
    assert table.DataCategory == "Time" and col.IsKey is True
    assert out["validated"]["rows"] == 1461
    q, kw = qe.queries[0]
    assert "'DimDate'[Date]" in q and kw["bypass_cache"] is True


@pytest.mark.parametrize(
    "over,fragment",
    [
        ({"blanks": 2}, "2 blank"),
        ({"distinct_values": 1400}, "not unique"),
        ({"with_time": 5}, "time of day"),
        ({"distinct_days": 1400}, "not contiguous"),
        ({"rows": 0, "distinct_values": 0, "distinct_days": 0}, "no rows"),
    ],
)
def test_invalid_date_data_writes_nothing(monkeypatch, over, fragment):
    table, col = date_table()
    mgr, model, _ = date_mgr(monkeypatch, table, QE(stats(**over)))
    out = mgr.mark_as_date_table("DimDate", "Date")
    assert out["success"] is False and out["error_type"] == "invalid_date_column"
    assert any(fragment in p for p in out["problems"])
    assert model.saves == 0 and table.DataCategory is None and col.IsKey is False


def test_non_datetime_column_is_refused_before_any_query(monkeypatch):
    table, _ = date_table(dtype="DataType.String")
    qe = QE()
    mgr, model, _ = date_mgr(monkeypatch, table, qe)
    out = mgr.mark_as_date_table("DimDate", "Date")
    assert out["error_type"] == "invalid_date_column" and qe.queries == [] and model.saves == 0


def test_another_key_column_is_refused(monkeypatch):
    table, _ = date_table(other_key=True)
    mgr, model, _ = date_mgr(monkeypatch, table, QE())
    out = mgr.mark_as_date_table("DimDate", "Date")
    assert out["error_type"] == "another_key_column" and "Year" in out["error"]


def test_unloaded_table_reports_the_validation_failure(monkeypatch):
    table, _ = date_table()
    qe = QE(result={"success": False, "error": "table has no data"})
    mgr, model, _ = date_mgr(monkeypatch, table, qe)
    out = mgr.mark_as_date_table("DimDate", "Date")
    assert out["success"] is False and out["error_type"] == "date_validation_failed"
    assert "refresh" in out["hint"] and model.saves == 0


def test_missing_table_column_or_argument(monkeypatch):
    table, _ = date_table()
    mgr, _, _ = date_mgr(monkeypatch, table, QE())
    assert mgr.mark_as_date_table("Nope", "Date")["error_type"] == "table_not_found"
    assert mgr.mark_as_date_table("DimDate", "Nope")["error_type"] == "column_not_found"
    assert mgr.mark_as_date_table("DimDate", None)["error_type"] == "missing_argument"


def test_table_handler_registers_the_operation(monkeypatch):
    from core.operations import table_operations as top

    seen = {}
    fake = types.SimpleNamespace(
        mark_as_date_table=lambda t, c: seen.update(t=t, c=c) or {"success": True}
    )
    monkeypatch.setattr(top, "get_manager_or_error", lambda name, *a: fake)
    h = top.TableOperationsHandler()
    assert "mark_as_date_table" in h.get_available_operations()
    h.execute({"operation": "mark_as_date_table", "table_name": "DimDate", "column_name": "Date"})
    assert seen == {"t": "DimDate", "c": "Date"}  # column_name is accepted as an alias
    missing = h.execute({"operation": "mark_as_date_table", "table_name": "DimDate"})
    assert missing["success"] is False


# ---------------------------------------------------------------------------
# hierarchy levels
# ---------------------------------------------------------------------------


def hierarchy_model():
    country, city, code = Obj(Name="Country"), Obj(Name="City"), Obj(Name="Code")
    levels = Coll(
        [
            Obj(Name="Country", Ordinal=0, Column=country),
            Obj(Name="City", Ordinal=1, Column=city),
        ]
    )
    hier = Obj(Name="Geo", Levels=levels)
    tbl = Obj(
        Name="Places",
        Columns=Coll([country, city, code]),
        Hierarchies=Coll([hier]),
    )
    return Model([tbl]), hier


def ordered(hier):
    return [(lv.Name, lv.Ordinal) for lv in sorted(hier.Levels, key=lambda x: x.Ordinal)]


def test_add_level_appends_with_one_save(monkeypatch, fake_tom):
    m, hier = hierarchy_model()
    srv = patch_amo(monkeypatch, m)
    out = hmod.HierarchyCrudManager().add_level("Places", "Geo", column_name="Code")
    assert out["success"] is True and m.saves == 1 and srv.disconnected == 1
    assert ordered(hier) == [("Country", 0), ("City", 1), ("Code", 2)]
    assert out["level_count"] == 3


def test_add_level_at_a_position_shifts_the_rest(monkeypatch, fake_tom):
    m, hier = hierarchy_model()
    patch_amo(monkeypatch, m)
    out = hmod.HierarchyCrudManager().add_level(
        "Places", "Geo", level_name="Postcode", column_name="Code", ordinal="1"
    )
    assert out["success"] is True and out["ordinal"] == 1
    assert ordered(hier) == [("Country", 0), ("Postcode", 1), ("City", 2)]


@pytest.mark.parametrize(
    "kwargs,error_type",
    [
        ({"column_name": "Nope"}, "column_not_found"),
        ({"column_name": "Code", "level_name": "city"}, "name_conflict"),
        ({"column_name": "Code", "ordinal": 9}, "invalid_parameters"),
        ({"column_name": "Code", "ordinal": "x"}, "invalid_parameters"),
        ({}, "missing_argument"),
    ],
)
def test_add_level_refusals_change_nothing(monkeypatch, fake_tom, kwargs, error_type):
    m, hier = hierarchy_model()
    patch_amo(monkeypatch, m)
    out = hmod.HierarchyCrudManager().add_level("Places", "Geo", **kwargs)
    assert out["success"] is False and out["error_type"] == error_type
    assert m.saves == 0 and ordered(hier) == [("Country", 0), ("City", 1)]


def test_remove_level_renumbers_and_keeps_the_last_one(monkeypatch):
    m, hier = hierarchy_model()
    patch_amo(monkeypatch, m)
    mgr = hmod.HierarchyCrudManager()
    out = mgr.remove_level("Places", "Geo", level_name="Country")
    assert out["success"] is True and m.saves == 1
    assert ordered(hier) == [("City", 0)]
    last = mgr.remove_level("Places", "Geo", level_name="City")
    assert last["error_type"] == "last_level" and m.saves == 1
    assert mgr.remove_level("Places", "Geo", level_name="Nope")["error_type"] == "level_not_found"
    assert mgr.remove_level("Places", "Geo")["error_type"] == "missing_argument"


def test_rename_level_updates_name_and_points_at_the_report(monkeypatch):
    m, hier = hierarchy_model()
    patch_amo(monkeypatch, m)
    mgr = hmod.HierarchyCrudManager()
    out = mgr.rename_level("Places", "Geo", level_name="City", new_name="Town")
    assert out["success"] is True and m.saves == 1
    assert [lv.Name for lv in hier.Levels] == ["Country", "Town"]
    assert "pbir_followup" in out and out["original_name"] == "City"
    clash = mgr.rename_level("Places", "Geo", level_name="Town", new_name="country")
    assert clash["error_type"] == "name_conflict" and m.saves == 1
    same = mgr.rename_level("Places", "Geo", level_name="Town", new_name="Town")
    assert same["action"] == "noop" and m.saves == 1


def test_level_ops_report_unknown_hierarchy_and_table(monkeypatch):
    m, _ = hierarchy_model()
    patch_amo(monkeypatch, m)
    mgr = hmod.HierarchyCrudManager()
    assert mgr.remove_level("Places", "Nope", level_name="x")["error_type"] == "hierarchy_not_found"
    assert (
        mgr.rename_level("Nope", "Geo", level_name="x", new_name="y")["error_type"]
        == "table_not_found"
    )


def test_failed_save_rolls_back(monkeypatch):
    m, _ = hierarchy_model()
    m.save_error = RuntimeError("no")
    patch_amo(monkeypatch, m)
    out = hmod.HierarchyCrudManager().rename_level("Places", "Geo", level_name="City", new_name="T")
    assert out["success"] is False and m.undos == 1


# ---------------------------------------------------------------------------
# role.get_effective_permissions
# ---------------------------------------------------------------------------


def role_model():
    sales, dim, secret = Obj(Name="Sales"), Obj(Name="Dim"), Obj(Name="Hr")
    eu = Obj(
        Name="EU",
        ModelPermission="ModelPermission.Read",
        Members=Coll([Obj(MemberName="a@x"), Obj(MemberName="b@x")]),
        TablePermissions=Coll(
            [
                Obj(
                    Name="Sales",
                    Table=sales,
                    FilterExpression='[Region] = "EU"',
                    MetadataPermission="MetadataPermission.Default",
                    ColumnPermissions=Coll([]),
                ),
                Obj(
                    Name="Hr",
                    Table=secret,
                    FilterExpression=None,
                    MetadataPermission="MetadataPermission.None",
                    ColumnPermissions=Coll(
                        [
                            Obj(Name="Salary", MetadataPermission="MetadataPermission.None"),
                            Obj(Name="Name", MetadataPermission="MetadataPermission.Default"),
                        ]
                    ),
                ),
            ]
        ),
    )
    all_r = Obj(
        Name="All", ModelPermission="ModelPermission.Read", Members=Coll(), TablePermissions=Coll()
    )
    m = Model([sales, dim, secret])
    m.Roles = Coll([eu, all_r])
    return m


def test_effective_permissions_lists_filters_ols_and_unrestricted_tables(monkeypatch):
    m = role_model()
    monkeypatch.setattr(rmod, "get_server_db_model", lambda cs: (FakeServer(), m.Database, m, None))
    out = rmod.RlsRoleCrudManager().get_effective_permissions()
    assert out["success"] is True and out["count"] == 2
    eu = next(r for r in out["roles"] if r["role"] == "EU")
    assert eu["model_permission"] == "Read" and eu["member_count"] == 2
    sales = next(t for t in eu["tables"] if t["table"] == "Sales")
    assert sales["filter_expression"] == '[Region] = "EU"' and sales["table_ols"] == "Default"
    hr = next(t for t in eu["tables"] if t["table"] == "Hr")
    assert hr["table_ols"] == "None" and hr["filter_expression"] is None
    assert hr["columns"] == [{"column": "Salary", "permission": "None"}]  # Default entries dropped
    assert eu["filtered_tables"] == ["Sales"] and eu["unrestricted_tables"] == ["Dim"]
    everyone = next(r for r in out["roles"] if r["role"] == "All")
    assert sorted(everyone["unrestricted_tables"]) == ["Dim", "Hr", "Sales"]


def test_effective_permissions_for_one_role_and_table(monkeypatch):
    m = role_model()
    monkeypatch.setattr(rmod, "get_server_db_model", lambda cs: (FakeServer(), m.Database, m, None))
    mgr = rmod.RlsRoleCrudManager()
    out = mgr.get_effective_permissions(role_name="EU", table_name="Sales")
    assert out["count"] == 1 and [t["table"] for t in out["roles"][0]["tables"]] == ["Sales"]
    assert mgr.get_effective_permissions(role_name="Nope")["error_type"] == "role_not_found"


def test_effective_permissions_is_read_only(monkeypatch):
    m = role_model()
    monkeypatch.setattr(rmod, "get_server_db_model", lambda cs: (FakeServer(), m.Database, m, None))
    rmod.RlsRoleCrudManager().get_effective_permissions()
    assert m.saves == 0


# ---------------------------------------------------------------------------
# query groups
# ---------------------------------------------------------------------------


def qg_model():
    p1 = Obj(Name="Sales", QueryGroup=None)
    p2 = Obj(Name="P2", QueryGroup=None)
    sales = Obj(Name="Sales", Partitions=Coll([p1]))
    multi = Obj(Name="Multi", Partitions=Coll([Obj(Name="A", QueryGroup=None), p2]))
    expr = Obj(Name="SourceFolder", QueryGroup=None)
    m = Model([sales, multi])
    m.Expressions = Coll([expr])
    return m, expr, p1


def test_create_and_list_query_groups(monkeypatch, fake_tom):
    m, expr, p1 = qg_model()
    patch_amo(monkeypatch, m)
    mgr = QueryGroupCrudManager()
    out = mgr.create_query_group("Sources\\Staging", description="Raw loads")
    assert out["success"] is True and m.saves == 1
    assert m.QueryGroups.Find("Sources\\Staging").Description == "Raw loads"
    dup = mgr.create_query_group("Sources\\Staging")
    assert dup["error_type"] == "already_exists" and m.saves == 1
    listed = mgr.list_query_groups()
    assert listed["count"] == 1 and listed["query_groups"][0]["member_count"] == 0


def test_assign_expression_and_partition_then_list_members(monkeypatch, fake_tom):
    m, expr, p1 = qg_model()
    patch_amo(monkeypatch, m)
    mgr = QueryGroupCrudManager()
    mgr.create_query_group("Src")
    a = mgr.assign("Src", expression_name="SourceFolder")
    b = mgr.assign("Src", table_name="Sales")  # single partition: no partition_name needed
    assert a["success"] and b["success"] and expr.QueryGroup is m.QueryGroups.Find("Src")
    assert p1.QueryGroup is m.QueryGroups.Find("Src")
    assert b["target"] == "partition:Sales/Sales"
    members = mgr.list_query_groups()["query_groups"][0]
    assert members["named_expressions"] == ["SourceFolder"]
    assert members["partitions"] == [{"table": "Sales", "partition": "Sales"}]


def test_assign_refusals(monkeypatch, fake_tom):
    m, expr, _ = qg_model()
    patch_amo(monkeypatch, m)
    mgr = QueryGroupCrudManager()
    mgr.create_query_group("Src")
    saves = m.saves
    assert (
        mgr.assign("Nope", expression_name="SourceFolder")["error_type"] == "query_group_not_found"
    )
    assert mgr.assign("Src", expression_name="X")["error_type"] == "expression_not_found"
    assert mgr.assign("Src", table_name="Multi")["error_type"] == "ambiguous_partition"
    assert (
        mgr.assign("Src", table_name="Multi", partition_name="Z")["error_type"]
        == "partition_not_found"
    )
    assert mgr.assign("Src")["error_type"] == "invalid_parameters"
    assert (
        mgr.assign("Src", expression_name="a", table_name="b")["error_type"] == "invalid_parameters"
    )
    assert mgr.assign(None, expression_name="SourceFolder")["error_type"] == "missing_argument"
    assert m.saves == saves and expr.QueryGroup is None


def test_unassign_clears_the_group_and_needs_no_group_name(monkeypatch, fake_tom):
    m, expr, _ = qg_model()
    patch_amo(monkeypatch, m)
    mgr = QueryGroupCrudManager()
    mgr.create_query_group("Src")
    mgr.assign("Src", expression_name="SourceFolder")
    out = mgr.assign(None, expression_name="SourceFolder", unassign=True)
    assert (
        out["success"] is True and out["previous_query_group"] == "Src" and expr.QueryGroup is None
    )


def test_update_renames_the_folder_and_describes(monkeypatch, fake_tom):
    m, _, _ = qg_model()
    patch_amo(monkeypatch, m)
    mgr = QueryGroupCrudManager()
    mgr.create_query_group("Src")
    mgr.create_query_group("Other")
    out = mgr.update_query_group("Src", description="d", new_name="Raw")
    assert (
        out["success"] is True
        and out["query_group"] == "Raw"
        and out["updated"] == ["name", "description"]
    )
    assert m.QueryGroups.Find("Raw").Description == "d"
    assert mgr.update_query_group("Raw", new_name="Other")["error_type"] == "name_conflict"
    assert mgr.update_query_group("Nope", description="x")["error_type"] == "query_group_not_found"
    assert mgr.update_query_group("Raw")["error_type"] == "invalid_parameters"


def test_delete_refuses_a_used_group_unless_forced(monkeypatch, fake_tom):
    m, expr, p1 = qg_model()
    patch_amo(monkeypatch, m)
    mgr = QueryGroupCrudManager()
    mgr.create_query_group("Src")
    mgr.assign("Src", expression_name="SourceFolder")
    mgr.assign("Src", table_name="Sales")
    refused = mgr.delete_query_group("Src")
    assert refused["error_type"] == "query_group_in_use" and refused["named_expressions"] == [
        "SourceFolder"
    ]
    assert m.QueryGroups.Find("Src") is not None
    done = mgr.delete_query_group("Src", force=True)
    assert done["success"] is True and m.QueryGroups.Find("Src") is None
    assert expr.QueryGroup is None and p1.QueryGroup is None
    empty = mgr.delete_query_group("Nope")
    assert empty["error_type"] == "query_group_not_found"


def test_unsupported_client_is_reported(monkeypatch):
    m = Model([])
    del m.QueryGroups
    patch_amo(monkeypatch, m)
    assert (
        QueryGroupCrudManager().list_query_groups()["error_type"] == "feature_unsupported_by_client"
    )
    assert (
        QueryGroupCrudManager().create_query_group("x")["error_type"]
        == "feature_unsupported_by_client"
    )


def test_failed_save_rolls_back_a_create(monkeypatch, fake_tom):
    m, _, _ = qg_model()
    m.save_error = RuntimeError("no")
    patch_amo(monkeypatch, m)
    out = QueryGroupCrudManager().create_query_group("Src")
    assert out["success"] is False and out["error_type"] == "tom_error" and m.undos == 1


# ---------------------------------------------------------------------------
# calculation_group update / rename, relationship rename
# ---------------------------------------------------------------------------


def calc_model():
    ti = Obj(Name="Time", CalculationGroup=Obj(Precedence=10, Description=None), IsHidden=False)
    cur = Obj(
        Name="Currency", CalculationGroup=Obj(Precedence=20, Description=None), IsHidden=False
    )
    plain = Obj(Name="Sales", CalculationGroup=None)
    return Model([ti, cur, plain]), ti, cur


def calc_mgr(m):
    mgr = CalculationGroupManager(None)
    mgr._connect_amo_server_db = lambda: (FakeServer(), Obj(Model=m), Obj())
    return mgr


def test_calc_group_update_sets_description_precedence_hidden_in_one_save():
    m, ti, _ = calc_model()
    out = calc_mgr(m).update_calculation_group(
        "Time", description="Period logic", precedence="30", hidden=True
    )
    assert out["success"] is True and m.saves == 1
    assert out["updated"] == ["description", "precedence", "hidden"]
    assert (
        ti.CalculationGroup.Description == "Period logic" and ti.CalculationGroup.Precedence == 30
    )
    assert ti.IsHidden is True


def test_calc_group_update_refusals():
    m, ti, _ = calc_model()
    mgr = calc_mgr(m)
    assert mgr.update_calculation_group("Time", precedence=20)["error_type"] == "precedence_taken"
    assert mgr.update_calculation_group("Time")["error_type"] == "invalid_parameters"
    assert (
        mgr.update_calculation_group("Time", precedence="x")["error_type"] == "invalid_parameters"
    )
    assert (
        mgr.update_calculation_group("Sales", description="x")["error_type"]
        == "calc_group_not_found"
    )
    assert m.saves == 0 and ti.CalculationGroup.Precedence == 10
    same = mgr.update_calculation_group("Time", precedence=10)  # its own value is fine
    assert same["success"] is True


def handler_with(monkeypatch, calc, table):
    from core.operations import calculation_group_operations as cgo

    monkeypatch.setattr(
        cgo,
        "get_manager_or_error",
        lambda name, *a: {"calc_group_manager": calc, "table_crud_manager": table}[name],
    )
    return CalculationGroupOperationsHandler()


def test_calc_group_rename_is_a_table_rename(monkeypatch):
    seen = {}
    calc = types.SimpleNamespace(
        list_calculation_groups=lambda: {"success": True, "calculation_groups": [{"name": "Time"}]}
    )
    table = types.SimpleNamespace(
        rename_table=lambda *a, **kw: seen.update(a=a, kw=kw) or {"success": True, "changes": 3}
    )
    h = handler_with(monkeypatch, calc, table)
    out = h.execute(
        {
            "operation": "rename",
            "group_name": "Time",
            "new_name": "Time Calc",
            "preview": True,
            "pbip_path": "C:/r.pbip",
        }
    )
    assert (
        out["success"] is True
        and out["calculation_group"] == "Time Calc"
        and out["original_name"] == "Time"
    )
    assert seen["a"] == ("Time", "Time Calc")
    assert seen["kw"] == {"pbip_path": "C:/r.pbip", "preview": True, "preview_hash": None}


def test_calc_group_rename_refuses_unknown_group_and_missing_name(monkeypatch):
    calc = types.SimpleNamespace(
        list_calculation_groups=lambda: {"success": True, "calculation_groups": [{"name": "Time"}]}
    )
    table = types.SimpleNamespace(
        rename_table=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not rename"))
    )
    h = handler_with(monkeypatch, calc, table)
    out = h.execute({"operation": "rename", "group_name": "Sales", "new_name": "X"})
    assert out["error_type"] == "calc_group_not_found" and out["available_groups"] == ["Time"]
    assert h.execute({"operation": "rename", "group_name": "Time"})["success"] is False


def test_calc_group_update_handler_forwards(monkeypatch):
    seen = {}
    calc = types.SimpleNamespace(
        update_calculation_group=lambda **kw: seen.update(kw) or {"success": True}
    )
    h = handler_with(monkeypatch, calc, None)
    h.execute(
        {
            "operation": "update",
            "group_name": "Time",
            "description": "d",
            "precedence": 5,
            "hidden": False,
        }
    )
    assert seen == {
        "group_name": "Time",
        "description": "d",
        "precedence": 5,
        "hidden": False,
        "multiple_or_empty_selection_expression": None,  # F-20c
        "no_selection_expression": None,
        "selection_expression_behavior": None,
    }


def test_relationship_rename_delegates_and_preview_never_writes(monkeypatch):
    from core.operations import relationship_operations as ro

    calls = []
    rel = types.SimpleNamespace(
        rename_relationship=lambda n, new: calls.append((n, new)) or {"success": True}
    )
    monkeypatch.setattr(ro, "get_manager_or_error", lambda name, *a: rel)
    h = RelationshipOperationsHandler()
    assert "rename" in h.get_available_operations()
    out = h.execute(
        {"operation": "rename", "relationship_name": "abc", "new_name": "Sales to Date"}
    )
    assert out["success"] is True and calls == [("abc", "Sales to Date")]
    preview = h.execute(
        {"operation": "rename", "relationship_name": "abc", "new_name": "x", "preview": True}
    )
    assert preview["preview"] is True and calls == [("abc", "Sales to Date")]  # no second write
    assert h.execute({"operation": "rename", "relationship_name": "abc"})["success"] is False
    assert h.execute({"operation": "rename", "new_name": "x"})["success"] is False


_ = qgm  # module imported for coverage of its import-time contract
