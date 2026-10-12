"""Review slice C — the remaining live-TOM fixes (C-10, C-13, C-15, C-16, C-19,
C-20, C-22, C-24, C-25, C-27, C-28, C-29, C-30, C-31, C-33, C-34, C-35, C-37,
C-38, C-42, K-03).

Every test drives the real manager / handler code against PascalCase fakes; the
TOM assembly is replaced by a fake ``Microsoft.AnalysisServices.Tabular`` module
where a manager imports it (``monkeypatch.setitem(sys.modules, ...)`` with the
parent modules chained). Nothing connects to Power BI.
"""

from __future__ import annotations

import sys
import threading
import time
import types
from types import SimpleNamespace

import pytest

from core.autonomous import gating
from tests._c_fake_tom import Coll, FakeMeasure, FakeModel, FakeServer, Obj, column, measure, table


def _install_tom(monkeypatch, **attrs):
    tom = types.ModuleType("Microsoft.AnalysisServices.Tabular")
    for k, v in attrs.items():
        setattr(tom, k, v)
    ms = types.ModuleType("Microsoft")
    asv = types.ModuleType("Microsoft.AnalysisServices")
    ms.AnalysisServices = asv
    asv.Tabular = tom
    monkeypatch.setitem(sys.modules, "Microsoft", ms)
    monkeypatch.setitem(sys.modules, "Microsoft.AnalysisServices", asv)
    monkeypatch.setitem(sys.modules, "Microsoft.AnalysisServices.Tabular", tom)
    return tom


def _enum(name, *members):
    return type(name, (), {m: f"{name}.{m}" for m in members})


@pytest.fixture
def policy(monkeypatch):
    def _set(value):
        monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", value)
        gating.invalidate_policy_cache()

    yield _set
    gating.invalidate_policy_cache()


# ---------------------------------------------------------------------------
# C-10 — "applied but the client could not read the response" is not a failure
# ---------------------------------------------------------------------------
class _RaisingModel:
    def __init__(self, exc):
        self.exc = exc
        self.calls = 0

    def SaveChanges(self):
        self.calls += 1
        raise self.exc


def test_c10_save_error_with_landed_change_is_reported_as_applied():
    from core.operations.write_lane import commit

    exc = RuntimeError("Unexpected column name 'ObjectID.BindingInfo' in rowset 'Annotations'")
    info = commit(_RaisingModel(exc), label="measure/update", verify=lambda: True)
    assert info["applied"] is True and "do NOT retry" in info["post_write_warning"]
    assert "BindingInfo" in info["client_error"]


@pytest.mark.parametrize("landed", [False, None])
def test_c10_save_error_without_proof_still_raises(landed):
    from core.operations.write_lane import commit

    with pytest.raises(RuntimeError):
        commit(_RaisingModel(RuntimeError("rejected")), label="x", verify=lambda: landed)


def test_c10_measure_update_surfaces_applied_unverified(monkeypatch):
    import core.operations.measure_crud_manager as mcm
    from core.operations import model_readback

    model = FakeModel(table("Sales", measures=[measure("M", "1")]))
    model.save_error = RuntimeError("compatibility level ... needed for [function '']")
    monkeypatch.setattr(mcm, "AMO_AVAILABLE", True)
    mgr = mcm.MeasureCRUDManager(None)
    monkeypatch.setattr(mgr, "_get_server_db_model", lambda: (FakeServer(), None, model))
    monkeypatch.setattr(
        model_readback,
        "find_measure",
        lambda t, n: {"expression": "2", "name": n, "table": t},
    )
    out = mgr.update_measure("Sales", "M", expression="2")
    assert out["success"] is True and out.get("applied") is True


# ---------------------------------------------------------------------------
# C-22 / C-27 — one serialised write lane; generation bump; loop health stop
# ---------------------------------------------------------------------------
def test_c22_saves_are_serialised_across_threads():
    from core.operations.write_lane import commit

    state = {"inside": 0, "max": 0}
    lock = threading.Lock()

    class _Slow:
        def SaveChanges(self):
            with lock:
                state["inside"] += 1
                state["max"] = max(state["max"], state["inside"])
            time.sleep(0.02)
            with lock:
                state["inside"] -= 1

    threads = [
        threading.Thread(target=commit, args=(_Slow(),), kwargs={"label": "t"}) for _ in range(6)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert state["max"] == 1


def test_c27_every_save_bumps_the_model_generation():
    import core.infrastructure.connection_state as cs_mod
    from core.operations.write_lane import commit

    before = cs_mod.connection_state.model_generation
    commit(SimpleNamespace(SaveChanges=lambda: None), label="table/create")
    assert cs_mod.connection_state.model_generation == before + 1


def test_c22_loop_stops_when_desktop_stops_answering(monkeypatch):
    from core.operations import write_lane

    lane = write_lane.ModelWriteLane()
    monkeypatch.setattr(lane, "check_health", lambda: {"success": False, "error": "timeout"})
    monkeypatch.setattr(write_lane, "_cfg", lambda k, d: 0.0 if k == "loop_pause_s" else d)
    with pytest.raises(write_lane.DesktopUnresponsive):
        lane.commit(SimpleNamespace(SaveChanges=lambda: None), label="x", in_loop=True)


# ---------------------------------------------------------------------------
# C-13 / K-03 — 02 severity by effect; read-only ops never gated
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "object_type, operation, args, expected",
    [
        ("calculation_group", "delete", {}, "CATASTROPHIC"),
        ("culture", "delete", {}, "CATASTROPHIC"),
        ("named_expression", "delete", {}, "CATASTROPHIC"),
        ("table", "rename", {}, "CATASTROPHIC"),
        ("function", "daxlib_install", {}, "CATASTROPHIC"),
        ("measure", "rename", {"pbip_path": "x.pbip"}, "CATASTROPHIC"),
        ("model", "refresh", {}, "CATASTROPHIC"),
        ("model", "refresh", {"tables": ["A", "B"]}, "DESTRUCTIVE"),
        ("model", "refresh", {"refresh_type": "calculate"}, "DESTRUCTIVE"),
        ("measure", "delete", {}, "DESTRUCTIVE"),
    ],
)
def test_c13_model_operation_severity(object_type, operation, args, expected):
    import server.handlers.model_operations_handler as mod

    assert mod._severity_for(object_type, operation, args).name == expected


@pytest.mark.parametrize(
    "object_type, operation",
    [
        ("calculation_group", "detect"),
        ("calculation_group", "scaffold"),
        ("calculation_group", "list_patterns"),
        ("function", "daxlib_search"),
        ("measure", "list"),
    ],
)
def test_k03_read_only_ops_are_never_gated(object_type, operation):
    import server.handlers.model_operations_handler as mod

    assert mod._severity_for(object_type, operation, {}) is None


def test_k03_rename_preview_is_read_only():
    import server.handlers.model_operations_handler as mod

    assert mod._severity_for("table", "rename", {"preview": True}) is None


def test_k03_unknown_operation_is_a_typo_not_a_confirm_demand(policy):
    import server.handlers.model_operations_handler as mod

    policy("STRICT")
    out = mod.handle_model_operations({"object_type": "table", "operation": "delet"})
    assert out["success"] is False
    assert out.get("error_type") != "confirm_destructive_required"


# ---------------------------------------------------------------------------
# C-19 — one target resolution; conflicting aliases are rejected
# ---------------------------------------------------------------------------
def test_c19_conflicting_aliases_are_rejected_before_anything_runs(monkeypatch, policy):
    import server.handlers.model_operations_handler as mod

    policy("OFF")
    ran = []
    monkeypatch.setattr(mod, "_original_handle_model_operations", lambda a: ran.append(a) or {})
    out = mod.handle_model_operations(
        {
            "object_type": "measure",
            "operation": "delete",
            "table_name": "T",
            "name": "Unused Measure",
            "measure_name": "Critical Measure",
        }
    )
    assert out["success"] is False and out["error_type"] == "ambiguous_target"
    assert ran == []


def test_c19_batch_item_aliases_resolve_once(monkeypatch):
    import server.handlers.batch_operations_handler as bmod

    out = bmod.handle_batch_operations(
        {
            "object_type": "measures",
            "batch_operation": "delete",
            "items": [{"table": "T", "name": "Unused", "measure": "Critical"}],
        }
    )
    assert out["success"] is False and out["error_type"] == "ambiguous_target"


def test_c19_evidence_and_executor_read_the_same_canonical_target():
    from core.autonomous.evidence import measure_node_refs
    from core.operations.target_resolution import canonicalize_items

    items = canonicalize_items("measures", [{"table": "T", "measure": "Critical"}])
    assert items == [{"table_name": "T", "measure_name": "Critical"}]
    assert measure_node_refs(items) == ["measure:T[Critical]"]


# ---------------------------------------------------------------------------
# C-20 — safe_* run the policy gate (STRICT blocks) and forward the PBIP path
# ---------------------------------------------------------------------------
def _proven(br, pbip_path=None):
    return {
        "blast_radius": br,
        "proven": True,
        "unproven_reasons": [],
        "provenance": {"source": "in_process", "pbip_path": pbip_path},
    }


def test_c20_safe_delete_is_blocked_under_strict(monkeypatch, policy):
    import server.handlers.model_operations_handler as mod
    from core.autonomous import evidence as ev

    policy("STRICT")
    ran = []
    monkeypatch.setattr(mod, "_original_handle_model_operations", lambda a: ran.append(a) or {})
    monkeypatch.setattr(
        ev,
        "compute_delete_evidence",
        lambda refs, pbip_path=None: _proven({"all_safe": True, "unsafe_nodes": [], "impacts": {}}),
    )
    out = mod.handle_model_operations(
        {"object_type": "table", "operation": "safe_delete", "table_name": "Junk"}
    )
    assert out["success"] is False and out["error_type"] == "confirm_destructive_required"
    assert ran == []


def test_c20_safe_rename_with_bound_visuals_needs_a_pbip_path(monkeypatch, policy):
    import server.handlers.model_operations_handler as mod
    from core.autonomous import evidence as ev

    policy("LIGHT")
    monkeypatch.setattr(mod, "_original_handle_model_operations", lambda a: {"success": True})
    br = {
        "all_safe": True,
        "unsafe_nodes": [],
        "impacts": {"measure:T[M]": {"rebinds_needed": ["visual:R/p/v"]}},
    }
    monkeypatch.setattr(
        ev, "compute_rename_evidence", lambda ref, new, pbip_path=None: _proven(br, None)
    )
    out = mod.handle_model_operations(
        {
            "object_type": "measure",
            "operation": "safe_rename",
            "table_name": "T",
            "measure_name": "M",
            "new_name": "N",
        }
    )
    assert out["success"] is False and out["error_type"] == "rebind_path_required"


def test_c20_safe_rename_forwards_the_proof_pbip_path(monkeypatch, policy):
    import server.handlers.model_operations_handler as mod
    from core.autonomous import evidence as ev

    policy("LIGHT")
    seen = {}
    monkeypatch.setattr(
        mod, "_original_handle_model_operations", lambda a: seen.update(a) or {"success": True}
    )
    br = {
        "all_safe": True,
        "unsafe_nodes": [],
        "impacts": {"measure:T[M]": {"rebinds_needed": ["visual:R/p/v"]}},
    }
    monkeypatch.setattr(
        ev, "compute_rename_evidence", lambda ref, new, pbip_path=None: _proven(br, "C:/p/R.pbip")
    )
    out = mod.handle_model_operations(
        {
            "object_type": "measure",
            "operation": "safe_rename",
            "table_name": "T",
            "measure_name": "M",
            "new_name": "N",
        }
    )
    assert out["success"] is True and seen["pbip_path"] == "C:/p/R.pbip"


# ---------------------------------------------------------------------------
# C-15 — OLS set works with pythonnet's `None` member; table-level OLS
# ---------------------------------------------------------------------------
def _ols_setup(monkeypatch):
    import core.operations.ols_crud_manager as omod

    perm = type("MetadataPermission", (), {"Read": "Read", "Default": "Default"})
    setattr(perm, "None", "None")  # reachable ONLY via getattr under pythonnet 3
    _install_tom(
        monkeypatch,
        MetadataPermission=perm,
        TablePermission=lambda: Obj(
            Name=None, Table=None, MetadataPermission="Default", ColumnPermissions=Coll()
        ),
        ColumnPermission=lambda: Obj(Name=None, Column=None, MetadataPermission="Default"),
    )
    col = column("Salary")
    tbl = table("HR", columns=[col])
    role = Obj(Name="Readers", TablePermissions=Coll())
    model = FakeModel(tbl, roles=[role])
    mgr = omod.OlsCrudManager()
    monkeypatch.setattr(mgr, "_get_server_db_model", lambda: (FakeServer(), None, model))
    return mgr, model, role, col


def test_c15_ols_set_column_none(monkeypatch):
    mgr, model, role, col = _ols_setup(monkeypatch)
    out = mgr.set_ols_rule("Readers", "HR", "Salary", "none")
    assert out["success"] is True, out
    tp = role.TablePermissions[0]
    tp.ColumnPermissions[0].Name = "Salary"
    assert tp.ColumnPermissions[0].MetadataPermission == "None" and model.saves == 1


def test_c15_ols_set_table_level(monkeypatch):
    mgr, model, role, _col = _ols_setup(monkeypatch)
    out = mgr.set_ols_rule("Readers", "HR", None, "None")
    assert out["success"] is True and out["scope"] == "table"
    assert role.TablePermissions[0].MetadataPermission == "None"


def test_c15_invalid_permission_is_rejected_before_connecting(monkeypatch):
    mgr, model, _role, _col = _ols_setup(monkeypatch)
    out = mgr.set_ols_rule("Readers", "HR", "Salary", "Hidden")
    assert out["success"] is False and out["error_type"] == "invalid_parameters"
    assert model.saves == 0


# ---------------------------------------------------------------------------
# C-16 — calc-group list_items reads the manager's `items`
# ---------------------------------------------------------------------------
def test_c16_list_items_returns_the_items(monkeypatch):
    from core.operations import calculation_group_operations as cgo

    mgr = SimpleNamespace(
        list_calculation_groups=lambda **kw: {
            "success": True,
            "calculation_groups": [{"name": "TI", "items": [{"name": "YTD"}, {"name": "PY"}]}],
        }
    )
    monkeypatch.setattr(cgo, "get_manager_or_error", lambda *a, **k: mgr)
    out = cgo.CalculationGroupOperationsHandler()._list_items({"group_name": "TI"})
    assert out["item_count"] == 2 and [i["name"] for i in out["items"]] == ["YTD", "PY"]


# ---------------------------------------------------------------------------
# C-24 — DAX never overwrites Power Query; import tables need their M
# ---------------------------------------------------------------------------
def test_c24_update_expression_refuses_an_m_partition():
    from core.operations.table_crud_manager import TableCRUDManager

    m_src = Obj(Expression='let Source = Sql.Database("s", "d") in Source')
    part = Obj(SourceType="PartitionSourceType.M", Source=m_src)
    model = FakeModel(table("Sales", partitions=[part]))
    out = TableCRUDManager(None)._apply_update_table(model, table_name="Sales", expression="ROW(1)")
    assert out["success"] is False and out["error_type"] == "invalid_operation"
    assert m_src.Expression.startswith("let Source")


def test_c24_update_expression_on_a_calculated_partition():
    from core.operations.table_crud_manager import TableCRUDManager

    src = Obj(Expression="ROW(1)")
    model = FakeModel(table("Calc", partitions=[Obj(SourceType="Calculated", Source=src)]))
    out = TableCRUDManager(None)._apply_update_table(model, table_name="Calc", expression="ROW(2)")
    assert out["success"] is True and src.Expression == "ROW(2)"


def test_c24_create_without_any_source_is_refused():
    from core.operations.table_crud_manager import TableCRUDManager

    out = TableCRUDManager(None)._apply_create_table(FakeModel(), table_name="Empty")
    assert out["success"] is False and out["error_type"] == "invalid_parameters"


# ---------------------------------------------------------------------------
# C-25 — strict, case-insensitive enums (no silent defaults)
# ---------------------------------------------------------------------------
def test_c25_column_data_type_is_strict(monkeypatch):
    from core.operations.column_crud_manager import ColumnCRUDManager

    _install_tom(monkeypatch, DataType=_enum("DataType", "String", "Int64", "DateTime", "Double"))
    mgr = ColumnCRUDManager(None)
    assert mgr._parse_data_type("int64") == "DataType.Int64"
    for bad in ("Integer", "Date", "int"):
        with pytest.raises(ValueError):
            mgr._parse_data_type(bad)


def test_c25_relationship_enums_are_strict(monkeypatch):
    from core.operations.relationship_crud_manager import RelationshipCRUDManager

    _install_tom(
        monkeypatch,
        RelationshipEndCardinality=_enum("RelationshipEndCardinality", "One", "Many"),
        CrossFilteringBehavior=_enum(
            "CrossFilteringBehavior", "OneDirection", "BothDirections", "Automatic"
        ),
    )
    mgr = RelationshipCRUDManager(None)
    assert mgr._parse_cardinality("one") == "RelationshipEndCardinality.One"
    assert mgr._parse_cross_filtering("Both") == "CrossFilteringBehavior.BothDirections"
    with pytest.raises(ValueError):
        mgr._parse_cardinality("1")


def test_c25_role_permission_is_strict(monkeypatch):
    import core.operations.rls_role_crud_manager as rmod

    perm = type(
        "ModelPermission",
        (),
        {
            "Read": "Read",
            "Administrator": "Administrator",
            "ReadRefresh": "ReadRefresh",
            "Refresh": "Refresh",
        },
    )
    setattr(perm, "None", "None")
    monkeypatch.setattr(rmod, "ModelPermission", perm)
    assert rmod._resolve_permission("ADMINISTRATOR") == "Administrator"
    with pytest.raises(ValueError):
        rmod._resolve_permission("admin")


def test_c25_real_tom_enum_names_exist():
    """The enum member names the strict parsers accept exist on the real TOM
    assembly (skipped when pythonnet / AMO are not loadable)."""
    try:
        from core.infrastructure.dll_paths import load_amo_assemblies

        load_amo_assemblies()
        import Microsoft.AnalysisServices.Tabular as TOM  # type: ignore
    except Exception:  # noqa: BLE001
        pytest.skip("TOM assembly not loadable")
    from core.operations.column_crud_manager import ColumnCRUDManager

    for name in ColumnCRUDManager._DATA_TYPE_NAMES:
        assert hasattr(TOM.DataType, name), name
    for name in ("None", "Read", "Default"):
        assert getattr(TOM.MetadataPermission, name) is not None
    assert not hasattr(TOM.MetadataPermission, "None_")  # the C-15 root cause


# ---------------------------------------------------------------------------
# C-28 — one INFO.MEASURES per request, invalidated by a model write
# ---------------------------------------------------------------------------
def test_c28_measure_usage_reuses_one_snapshot(monkeypatch):
    import core.infrastructure.connection_state as cs_mod
    from core.model.dependency_analyzer import DependencyAnalyzer

    calls = []

    class _QE:
        def execute_info_query(self, view, **kw):
            calls.append(view)
            if view == "MEASURES":
                return {
                    "success": True,
                    "rows": [
                        {"Table": "S", "Name": "Base", "Expression": "1"},
                        {"Table": "S", "Name": "A", "Expression": "[Base] + 1"},
                        {"Table": "S", "Name": "B", "Expression": "[Base] * [A]"},
                    ],
                }
            return {"success": True, "rows": []}

    da = DependencyAnalyzer(_QE())
    usage = da.find_measure_usage("S", "Base")
    assert {u["measure"] for u in usage["used_by"]} == {"A", "B"}
    first = calls.count("MEASURES")  # the snapshot (+ the reference index, built once)
    da.find_measure_usage("S", "A")
    da.find_measure_usage("S", "B")
    assert calls.count("MEASURES") == first  # was one INFO.MEASURES per call
    cs_mod.connection_state.bump_model_generation("test write")
    da.find_measure_usage("S", "Base")
    assert calls.count("MEASURES") == first + 1  # a write invalidates the snapshot


# ---------------------------------------------------------------------------
# C-29 — RLS test compares VALUES as the role, all filters together
# ---------------------------------------------------------------------------
def test_c29_rls_test_detects_a_filter_that_keeps_the_row_count(monkeypatch):
    import core.infrastructure.role_impersonation as ri
    from core.operations.rls_manager import RLSManager

    class _QE:
        def validate_and_execute_dax(self, q, top_n=0, bypass_cache=False, **kw):
            return {"success": True, "rows": [{"[RowCount]": 5}]}

    mgr = RLSManager(None, _QE())
    monkeypatch.setattr(
        mgr,
        "list_roles",
        lambda: {
            "success": True,
            "roles": [
                {
                    "name": "EU",
                    "table_permissions": [
                        {"table": "Geo", "filter_expression": '[Region] = "EU"'},
                        {"table": "Sales", "filter_expression": "[Year] = 2026"},
                    ],
                }
            ],
        },
    )
    monkeypatch.setattr(
        ri, "run_as_role", lambda **kw: {"success": True, "rows": [{"[RowCount]": 3}]}
    )
    out = mgr.test_role_filter("EU", None, test_table="Sales")
    assert out["success"] is True and out["method"] == "impersonation"
    assert out["summary"]["role_changes_result"] is True
    assert out["filter_tests"][0]["tables"] == ["Geo", "Sales"]  # combined, not one at a time


def test_c29_simulation_keeps_define_queries_intact(monkeypatch):
    import core.infrastructure.role_impersonation as ri
    from core.operations.rls_manager import RLSManager

    seen = []

    class _QE:
        def validate_and_execute_dax(self, q, top_n=0, bypass_cache=False, **kw):
            seen.append(q)
            return {"success": True, "rows": [{"v": 1}]}

    mgr = RLSManager(None, _QE())
    monkeypatch.setattr(
        mgr,
        "list_roles",
        lambda: {
            "success": True,
            "roles": [
                {
                    "name": "R",
                    "table_permissions": [{"table": "T", "filter_expression": "T[x] = 1"}],
                }
            ],
        },
    )
    monkeypatch.setattr(
        ri, "run_as_role", lambda **kw: {"success": False, "error": "no impersonation"}
    )
    out = mgr.test_role_filter("R", 'DEFINE VAR v = 1 EVALUATE ROW("v", v)', None)
    assert out["success"] is False and out["error_type"] == "simulation_unsupported"
    assert all("EVALUATE CALCULATETABLE" not in q for q in seen)


# ---------------------------------------------------------------------------
# C-30 — calc-group DMV list; TOM-valid scaffold with one-call create_args
# ---------------------------------------------------------------------------
def test_c30_list_via_dmv_groups_items(monkeypatch):
    from core.operations import model_readback
    from core.operations.calculation_group_manager import CalculationGroupManager

    rows = {
        "TMSCHEMA_CALCULATION_GROUPS": [{"ID": 7, "TableID": 1, "Precedence": 20}],
        "TMSCHEMA_CALCULATION_ITEMS": [
            {"CalculationGroupID": 7, "Name": "PY", "Expression": "x", "Ordinal": 1},
            {
                "CalculationGroupID": 7,
                "Name": "CY",
                "Expression": "SELECTEDMEASURE()",
                "Ordinal": 0,
            },
        ],
        "TMSCHEMA_FORMAT_STRING_DEFINITIONS": [],
    }
    monkeypatch.setattr(model_readback, "dmv_rows", lambda dmv, info=None: rows.get(dmv))
    monkeypatch.setattr(model_readback, "table_names_by_id", lambda: {1: "Time Calc"})
    out = CalculationGroupManager(None)._list_calculation_groups_via_dax()
    assert out["success"] is True
    grp = out["calculation_groups"][0]
    assert grp["name"] == "Time Calc" and grp["precedence"] == 20
    assert [i["name"] for i in grp["items"]] == ["CY", "PY"]


def test_c30_scaffold_emits_valid_tmdl_and_one_call_create_args():
    from core.calc_group_migration.tmdl_scaffold import scaffold_calc_group

    cand = {
        "pattern_name": "time_intel",
        "variant_suffix_set": ["YTD", "YoY%"],
        "base_measures": ["Revenue"],
    }
    out = scaffold_calc_group(cand, calc_group_table_name="Time Intel", date_column="'Date'[Date]")
    tmdl = out["tmdl"]
    assert "table 'Time Intel'" in tmdl
    assert "calculationItem YTD =" in tmdl and "calculationItem 'YoY%' =" in tmdl
    assert "\tcolumn Name" in tmdl and "calculationGroup" in tmdl
    assert "partition 'Time Intel' = calculationGroup" in tmdl
    args = out["create_args"]
    assert args["group_name"] == "Time Intel" and len(args["items"]) == 2
    assert "table_name" not in args


def test_c30_create_sets_discourage_implicit_measures_in_source():
    import inspect

    from core.operations.calculation_group_manager import CalculationGroupManager

    src = inspect.getsource(CalculationGroupManager.create_calculation_group)
    assert "DiscourageImplicitMeasures = True" in src and '"Ordinal"' in src


# ---------------------------------------------------------------------------
# C-31 — move keeps lineage, extended properties, translations, perspectives
# ---------------------------------------------------------------------------
class _Translations(Coll):
    def SetTranslation(self, obj, prop, value):
        self.Add(Obj(Object=obj, Property=prop, Value=value))


class _PerspTable(Obj):
    """TOM PerspectiveTable: assigning .Table sets the Name."""

    def __init__(self):
        super().__init__(Name=None, PerspectiveMeasures=Coll())

    @property
    def Table(self):
        return getattr(self, "_table", None)

    @Table.setter
    def Table(self, t):
        self._table = t
        self.Name = t.Name


class _PerspMeasure(Obj):
    def __init__(self):
        super().__init__(Name=None)

    @property
    def Measure(self):
        return getattr(self, "_m", None)

    @Measure.setter
    def Measure(self, m):
        self._m = m
        self.Name = m.Name


def test_c31_move_keeps_identity_and_memberships(monkeypatch):
    import core.operations.measure_crud_manager as mcm

    monkeypatch.setattr(mcm, "Measure", FakeMeasure)
    _install_tom(
        monkeypatch,
        PerspectiveTable=_PerspTable,
        PerspectiveMeasure=_PerspMeasure,
        ObjectTranslation=lambda: Obj(Object=None, Property=None, Value=None),
        StringExtendedProperty=lambda: Obj(Name=None, Value=None),
        JsonExtendedProperty=lambda: Obj(Name=None, Value=None),
        Annotation=lambda: Obj(Name=None, Value=None),
        TranslatedProperty=SimpleNamespace(Caption="Caption"),
    )
    m = measure("Revenue", "SUM(S[Amt])", LineageTag="lt-1", SourceLineageTag="slt-1")
    m.Annotations.Add(Obj(Name="PBI_FormatHint", Value="{}"))
    m.ExtendedProperties.Add(type("JsonExtendedProperty", (Obj,), {})(Name="ep", Value="{}"))
    src, dst = table("Sales", measures=[m]), table("KPI")
    ot = Obj(Object=m, Property="Caption", Value="Omzet")
    culture = Obj(Name="nl-BE", ObjectTranslations=_Translations([ot]))
    pt = Obj(Name="Sales", PerspectiveMeasures=Coll([Obj(Name="Revenue")]))
    persp = Obj(Name="Exec", PerspectiveTables=Coll([pt]))
    model = FakeModel(src, dst, cultures=[culture], perspectives=[persp])
    out = mcm.MeasureCRUDManager(None)._apply_move_measure(
        model, source_table="Sales", measure_name="Revenue", target_table="KPI"
    )
    assert out["success"] is True, out
    moved = dst.Measures[0]
    assert (moved.LineageTag, moved.SourceLineageTag) == ("lt-1", "slt-1")
    assert [a.Name for a in moved.Annotations] == ["PBI_FormatHint"]
    assert [e.Name for e in moved.ExtendedProperties] == ["ep"]
    assert [(t.Object, t.Value) for t in culture.ObjectTranslations] == [(moved, "Omzet")]
    assert any(
        p.Name == "KPI" and [x.Name for x in p.PerspectiveMeasures] == ["Revenue"]
        for p in persp.PerspectiveTables
    )
    assert src.Measures.Count == 0


def test_c34_orphan_check_uses_key_sets_not_related():
    import inspect

    from core.model.model_validator import ModelValidator

    src = inspect.getsource(ModelValidator._check_relationship_integrity)
    assert "EXCEPT(" in src and "ISBLANK(RELATED(" not in src


# ---------------------------------------------------------------------------
# C-35 — refresh of only-missing tables fails; string tables; escaping
# ---------------------------------------------------------------------------
def test_c35_refresh_of_only_missing_tables_is_a_failure(monkeypatch):
    import core.operations.model_refresh_manager as mrm

    model = FakeModel(table("Sales"))
    mgr = mrm.ModelRefreshManager()
    monkeypatch.setattr(mgr, "_get_server_db_model", lambda: (FakeServer(), None, model, None))
    monkeypatch.setattr(mrm, "_resolve_refresh_type", lambda rt: ("Full", None))
    out = mgr.refresh(refresh_type="full", tables=["Nope", "Ghost"])
    assert out["success"] is False and out["error_type"] == "table_not_found"
    assert model.saves == 0


def test_c35_string_tables_is_one_table_not_characters(monkeypatch):
    import core.operations.model_refresh_manager as mrm

    requested = []
    sales = table("Sales")
    sales.RequestRefresh = lambda rt: requested.append("Sales")
    model = FakeModel(sales)
    mgr = mrm.ModelRefreshManager()
    monkeypatch.setattr(mgr, "_get_server_db_model", lambda: (FakeServer(), None, model, None))
    monkeypatch.setattr(mrm, "_resolve_refresh_type", lambda rt: ("Full", None))
    out = mgr.refresh(refresh_type="full", tables="Sales")
    assert out["success"] is True and requested == ["Sales"]


def test_c35_sample_data_escapes_identifiers_and_tolerates_null_direction(monkeypatch):
    import core.operations.table_operations as tops

    seen = []
    policy_obj = SimpleNamespace(
        safe_run_dax=lambda **kw: seen.append(kw["query"]) or {"success": True, "rows": []}
    )
    monkeypatch.setattr(tops, "get_manager_or_error", lambda *a, **k: policy_obj)
    out = tops.TableOperationsHandler()._sample_data(
        {
            "table_name": "Owner's Book",
            "columns": ["Net [EUR]"],
            "order_by": "Net [EUR]",
            "order_direction": None,
        }
    )
    assert out["success"] is True
    q = seen[0]
    assert "'Owner''s Book'[Net [EUR]]]" in q and q.rstrip().endswith("ASC)")


# ---------------------------------------------------------------------------
# C-37 — dead modules are gone
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "module",
    [
        "core.operations.operation_group_tracker",
        "core.operations.partition_manager",
        "core.operations.bulk_operations",
        "core.model.pbip_reader",
    ],
)
def test_c37_dead_modules_are_deleted(module):
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)


# ---------------------------------------------------------------------------
# C-38 — daxlib: URL-quoted ids, cached catalog
# ---------------------------------------------------------------------------
def test_c38_package_id_is_url_quoted(monkeypatch):
    import core.operations.daxlib_client as dl

    urls = []
    monkeypatch.setattr(dl, "_http_get", lambda url: urls.append(url) or {"_http_error": "x"})
    dl.fetch_package("../admin?x=1")
    assert urls == ["https://daxlib.org/api/packages/..%2Fadmin%3Fx%3D1"]


def test_c38_catalog_is_cached(monkeypatch):
    import core.operations.daxlib_client as dl

    dl.clear_catalog_cache()
    calls = []
    monkeypatch.setattr(dl, "_http_get", lambda url: calls.append(url) or {"_body": []})
    dl.search_packages("x")
    dl.search_packages("y")
    assert len(calls) == 1
    dl.clear_catalog_cache()


# ---------------------------------------------------------------------------
# C-42 — relationship guards, hierarchy update, bulk perspective members,
# PBI_* annotation protection, more annotation targets
# ---------------------------------------------------------------------------
def _rel_model():
    a_key, b_key, b_key2 = column("BKey"), column("Key"), column("Key2")
    a = table("A", columns=[a_key, column("BKey2")])
    b = table("B", columns=[b_key, b_key2])
    rel = Obj(Name="r1", FromColumn=a_key, ToColumn=b_key, FromTable=a, ToTable=b, IsActive=True)
    return FakeModel(a, b, relationships=[rel])


def _rel_mgr(monkeypatch, model):
    import core.operations.relationship_crud_manager as rmod

    monkeypatch.setattr(rmod, "AMO_AVAILABLE", True)
    mgr = rmod.RelationshipCRUDManager(None)
    monkeypatch.setattr(mgr, "_get_server_db_model", lambda: (FakeServer(), None, model))
    return mgr


def test_c42_duplicate_relationship_is_refused(monkeypatch):
    model = _rel_model()
    out = _rel_mgr(monkeypatch, model).create_relationship("A", "BKey", "B", "Key")
    assert out["success"] is False and out["error_type"] == "duplicate_relationship"


def test_c42_second_active_path_is_refused(monkeypatch):
    model = _rel_model()
    out = _rel_mgr(monkeypatch, model).create_relationship("A", "BKey2", "B", "Key2")
    assert out["success"] is False and out["error_type"] == "ambiguous_path"
    assert model.saves == 0


def _hier_model():
    y, m = Obj(Name="Year", Ordinal=0), Obj(Name="Month", Ordinal=1)
    h = Obj(Name="Calendar", Levels=Coll([y, m]), Description="", DisplayFolder="", IsHidden=False)
    t = table("Date", Hierarchies=Coll([h]))
    return FakeModel(t), h, y, m


def _hier_mgr(monkeypatch, model):
    from core.operations.hierarchy_crud_manager import HierarchyCrudManager

    mgr = HierarchyCrudManager()
    monkeypatch.setattr(mgr, "_get_server_db_model", lambda: (FakeServer(), None, model))
    return mgr


def test_c42_hierarchy_update_renames_and_reorders_in_one_save(monkeypatch):
    model, h, y, m = _hier_model()
    out = _hier_mgr(monkeypatch, model).update_hierarchy(
        "Date", "Calendar", new_name="Fiscal", levels=["Month", "Year"]
    )
    assert out["success"] is True and set(out["updated"]) == {"name", "levels"}
    assert h.Name == "Fiscal" and (m.Ordinal, y.Ordinal) == (0, 1)
    assert model.saves == 1 and "pbir_followup" in out


def test_c42_hierarchy_update_rejects_a_partial_level_list(monkeypatch):
    model, _h, _y, _m = _hier_model()
    out = _hier_mgr(monkeypatch, model).update_hierarchy("Date", "Calendar", levels=["Year"])
    assert out["success"] is False and out["error_type"] == "invalid_parameters"
    assert model.saves == 0


def _persp_setup(monkeypatch):
    from core.operations.perspective_crud_manager import PerspectiveCrudManager
    import core.operations.amo_helpers as amo

    _install_tom(
        monkeypatch,
        PerspectiveTable=lambda: Obj(
            Name=None,
            PerspectiveMeasures=Coll(),
            PerspectiveColumns=Coll(),
            PerspectiveHierarchies=Coll(),
        ),
        PerspectiveMeasure=lambda: Obj(Name=None),
        PerspectiveColumn=lambda: Obj(Name=None),
        PerspectiveHierarchy=lambda: Obj(Name=None),
    )
    sales = table(
        "Sales",
        measures=[measure("Rev"), measure("Cost")],
        columns=[column("Amt")],
        Hierarchies=Coll(),
    )
    persp = Obj(Name="Exec", PerspectiveTables=Coll())
    model = FakeModel(sales, perspectives=[persp])
    monkeypatch.setattr(amo, "get_server_db_model", lambda cs: (FakeServer(), None, model, None))
    return PerspectiveCrudManager(), model, persp


def test_c42_perspective_set_members_is_one_save(monkeypatch):
    mgr, model, persp = _persp_setup(monkeypatch)
    out = mgr.set_members(
        "Exec",
        [
            {"table_name": "Sales", "measure_name": "Rev"},
            {"table_name": "Sales", "measure_name": "Cost"},
            {"table_name": "Sales", "column_name": "Amt"},
        ],
    )
    assert out["success"] is True and out["changed"] == 3 and model.saves == 1
    pt = persp.PerspectiveTables[0]
    assert [x.Name for x in pt.PerspectiveMeasures] == ["Rev", "Cost"]
    out = mgr.set_members("Exec", [{"table_name": "Sales", "measure_name": "Rev"}], mode="remove")
    assert out["success"] is True and [x.Name for x in pt.PerspectiveMeasures] == ["Cost"]
    assert model.saves == 2


def test_c42_perspective_set_members_is_all_or_nothing(monkeypatch):
    mgr, model, persp = _persp_setup(monkeypatch)
    out = mgr.set_members(
        "Exec",
        [
            {"table_name": "Sales", "measure_name": "Rev"},
            {"table_name": "Sales", "measure_name": "Nope"},
        ],
    )
    assert out["success"] is False and out["error_type"] == "invalid_members"
    assert persp.PerspectiveTables.Count == 0 and model.saves == 0


def test_c42_set_members_is_classified_by_action():
    from server.op_classification import classify

    base = {"object_type": "perspective", "operation": "set_members"}
    assert classify("02_Model_Operations", base) == "write"
    assert classify("02_Model_Operations", {**base, "member_action": "remove"}) == "destructive"


def _annotation_mgr(monkeypatch, model):
    import core.operations.amo_helpers as amo
    import core.operations.annotation_manager as amod

    monkeypatch.setattr(amo, "get_server_db_model", lambda cs: (FakeServer(), None, model, None))
    monkeypatch.setattr(amod, "get_server_db_model", lambda cs: (FakeServer(), None, model, None))
    return amod.AnnotationManager()


def test_c42_pbi_annotations_need_force_to_delete(monkeypatch):
    t = table("fp Metric")
    t.Annotations.Add(Obj(Name="PBI_Id", Value="abc"))
    model = FakeModel(t)
    mgr = _annotation_mgr(monkeypatch, model)
    out = mgr.delete_annotation("table", "PBI_Id", {"table_name": "fp Metric"})
    assert out["success"] is False and out["error_type"] == "protected_annotation"
    assert t.Annotations.Count == 1
    out = mgr.delete_annotation("table", "PBI_Id", {"table_name": "fp Metric", "force": True})
    assert out["success"] is True and t.Annotations.Count == 0


@pytest.mark.parametrize(
    "target_type, args",
    [
        ("calculation_item", {"group_name": "Time", "item_name": "PY"}),
        ("perspective", {"perspective_name": "Exec"}),
        ("culture", {"culture_name": "nl-BE"}),
    ],
)
def test_c42_new_annotation_targets_resolve(monkeypatch, target_type, args):
    item = Obj(Name="PY", Annotations=Coll())
    cg = Obj(CalculationItems=Coll([item]))
    model = FakeModel(
        table("Time", calc_group=cg),
        cultures=[Obj(Name="nl-BE", Annotations=Coll())],
        perspectives=[Obj(Name="Exec", Annotations=Coll())],
    )
    mgr = _annotation_mgr(monkeypatch, model)
    obj, err = mgr._resolve_target(model, target_type, args)
    assert err is None and obj is not None
