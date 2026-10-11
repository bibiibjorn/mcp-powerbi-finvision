"""F-04: ``02_Model_Operations object_type=any`` - list_properties / get_property / set_property.

Three layers, none touching a live model:

* fakes (plain Python objects; ``__tom_kinds__`` marks enum properties) for resolution, the
  allow-list / deny-list, coercion, dry run, commit + rollback, annotations;
* the REAL TOM object model built in memory (skipped when the AMO assemblies are not loadable): CLR
  reflection, enum parsing, the SortByColumn reference, and TOM's own compatibility enforcement;
* the 02 handler: gate level per property, read-only / dry-run classification.
"""

from __future__ import annotations

import sys
import types

import pytest

from core.operations import tom_properties as tp
from core.operations.tom_properties import TomPropertyManager, parse_ref
from tests._c_fake_tom import Coll, FakeServer, Obj


# ---------------------------------------------------------------------------
# parse_ref
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "ref,expected",
    [
        ("Sales[Amount]", ("column_or_measure", "Sales", "Amount")),
        ("'My Table'[Col]", ("column_or_measure", "My Table", "Col")),
        ("table:Sales", ("table", None, "Sales")),
        ("Sales", ("table", None, "Sales")),
        ("model", ("model", None, None)),
        ("measure:Sales[Total]", ("measure", "Sales", "Total")),
        ("relationship:abc", ("relationship", None, "abc")),
        ("expression:Src", ("named_expression", None, "Src")),
        ("hierarchy:Date[Calendar]", ("hierarchy", "Date", "Calendar")),
        ("calculation_group:Time", ("calculation_group", None, "Time")),
    ],
)
def test_parse_ref(ref, expected):
    kind, table, name, err = parse_ref(ref)
    assert err is None and (kind, table, name) == expected


@pytest.mark.parametrize("ref", ["", None, "column:Sales", "role:Sales[X]"])
def test_parse_ref_rejects(ref):
    assert parse_ref(ref)[3] is not None


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------
class FakeColumn(Obj):
    def __init__(self, name, table=None, **kw):
        base = dict(
            Name=name,
            Description="",
            IsHidden=False,
            SummarizeBy="Default",
            IsAvailableInMDX=True,
            SortByColumn=None,
            Table=table,
            TableDetailPosition=-1,
            IsKey=False,
            Expression="x",
            LineageTag="tag",
            Annotations=Coll(),
            ExtendedProperties=Coll(),
        )
        base.update(kw)
        super().__init__(**base)
        self.__tom_kinds__ = {"SummarizeBy": ("enum", ["Default", "Sum", "Count"])}


class Model:
    def __init__(self):
        self.Tables = Coll()
        self.Relationships = Coll()
        self.Roles = Coll()
        self.Cultures = Coll()
        self.Perspectives = Coll()
        self.Expressions = Coll()
        self.Description = ""
        self.Annotations = Coll()
        self.saves = 0
        self.undos = 0
        self.Database = Obj(CompatibilityLevel=1702)

    def SaveChanges(self):
        self.saves += 1

    def UndoLocalChanges(self):
        self.undos += 1


@pytest.fixture
def world(monkeypatch):
    m = Model()
    t = Obj(
        Name="Sales",
        Description="",
        IsHidden=False,
        DataCategory="",
        Columns=Coll(),
        Measures=Coll(),
        Hierarchies=Coll(),
        Partitions=Coll(),
        Annotations=Coll(),
        ExtendedProperties=Coll(),
    )
    amount = FakeColumn("Amount", t)
    month = FakeColumn("Month", t)
    t.Columns.extend([amount, month])
    total = Obj(
        Name="Total",
        Description="",
        FormatString="",
        DisplayFolder="",
        IsHidden=False,
        Expression="1",
        Annotations=Coll(),
        ExtendedProperties=Coll(),
    )
    t.Measures.append(total)
    m.Tables.append(t)
    commits = []
    monkeypatch.setattr(
        tp, "_lane_commit", lambda model, label, **kw: commits.append(label) or {"journaled": True}
    )
    mgr = TomPropertyManager()
    server = FakeServer()
    mgr._open = lambda: (server, m.Database, m, None)
    return types.SimpleNamespace(
        model=m,
        table=t,
        amount=amount,
        month=month,
        total=total,
        mgr=mgr,
        commits=commits,
        server=server,
    )


def test_list_properties_marks_settable_and_denied(world):
    out = world.mgr.list_properties("Sales[Amount]")
    assert out["success"] and out["object_kind"] == "column"
    by = {p["name"]: p for p in out["properties"]}
    assert by["SummarizeBy"]["settable"] is True and by["SummarizeBy"]["values"] == [
        "Default",
        "Sum",
        "Count",
    ]
    assert by["IsAvailableInMDX"]["settable"] is True and by["IsAvailableInMDX"]["value"] is True
    assert by["Name"]["settable"] is False and "rename" in by["Name"]["reason"]
    assert by["LineageTag"]["settable"] is False and "lineage" in by["LineageTag"]["reason"]
    assert by["IsKey"]["settable"] is False
    assert "Annotations[<name>]" in out["pseudo_properties"]
    assert world.server.disconnected == 1


def test_get_property_is_case_and_underscore_insensitive(world):
    out = world.mgr.get_property("Sales[Amount]", "is_available_in_mdx")
    assert (
        out["success"]
        and out["property"] == "IsAvailableInMDX"
        and out["value"] is True
        and out["settable"]
    )
    assert world.mgr.get_property("Sales[Amount]", "Nope")["error_type"] == "not_found"


def test_set_property_coerces_and_commits_once(world):
    out = world.mgr.set_property("Sales[Amount]", "IsAvailableInMDX", "false")
    assert (
        out["success"]
        and out["action"] == "updated"
        and out["previous"] is True
        and out["value"] is False
    )
    assert world.amount.IsAvailableInMDX is False
    assert world.commits == ["column/set_property"] and out["journaled"] is True
    out = world.mgr.set_property("Sales[Amount]", "TableDetailPosition", "3")
    assert world.amount.TableDetailPosition == 3 and len(world.commits) == 2


def test_set_property_enum_and_noop_and_dry_run(world):
    dry = world.mgr.set_property("Sales[Amount]", "SummarizeBy", "sum", dry_run=True)
    assert dry["dry_run"] and dry["value"] == "Sum" and world.amount.SummarizeBy == "Default"
    assert world.commits == []
    assert world.mgr.set_property("Sales[Amount]", "SummarizeBy", "sum")["action"] == "updated"
    assert world.amount.SummarizeBy == "Sum"
    again = world.mgr.set_property("Sales[Amount]", "SummarizeBy", "SUM")
    assert again["action"] == "noop" and len(world.commits) == 1
    bad = world.mgr.set_property("Sales[Amount]", "SummarizeBy", "avg")
    assert bad["error_type"] == "invalid_parameters" and bad["valid_values"] == [
        "Default",
        "Sum",
        "Count",
    ]


def test_set_property_refuses_denied_properties(world):
    for prop, needle in [
        ("Name", "rename"),
        ("Expression", "update"),
        ("LineageTag", "lineage"),
        ("IsKey", "key"),
    ]:
        out = world.mgr.set_property("Sales[Amount]", prop, "x")
        assert out["error_type"] == "denied_property", prop
        assert needle in out["error"].lower()
        assert "settable_properties" in out
    assert world.commits == [] and world.amount.IsKey is False


def test_set_property_bad_values(world):
    assert (
        world.mgr.set_property("Sales[Amount]", "IsHidden", "maybe")["error_type"]
        == "invalid_parameters"
    )
    assert (
        world.mgr.set_property("Sales[Amount]", "TableDetailPosition", "x")["error_type"]
        == "invalid_parameters"
    )
    assert world.mgr.set_property("Sales[Missing]", "IsHidden", True)["error_type"] == "not_found"
    assert world.mgr.set_property("Nope[Amount]", "IsHidden", True)["error_type"] == "not_found"
    assert world.mgr.set_property("Sales[Amount]", "", True)["error_type"] == "invalid_parameters"


def test_sort_by_column_reference(world):
    out = world.mgr.set_property("Sales[Amount]", "SortByColumn", "Month")
    assert out["success"] and world.amount.SortByColumn is world.month and out["value"] == "Month"
    assert (
        world.mgr.set_property("Sales[Amount]", "SortByColumn", "Nope")["error_type"] == "not_found"
    )
    cleared = world.mgr.set_property("Sales[Amount]", "SortByColumn", "")
    assert cleared["success"] and world.amount.SortByColumn is None


def test_measure_and_table_properties(world):
    assert world.mgr.set_property("Sales[Total]", "FormatString", "0.0%")["success"]
    assert world.total.FormatString == "0.0%"
    assert world.mgr.set_property("table:Sales", "DataCategory", "Time")["success"]
    assert world.table.DataCategory == "Time"
    assert world.mgr.set_property("model", "Description", "Core model")["success"]
    assert world.model.Description == "Core model"
    # a measure's expression is changed by measure update, never here
    assert (
        world.mgr.set_property("Sales[Total]", "Expression", "2")["error_type"] == "denied_property"
    )


def test_failed_save_rolls_back(world, monkeypatch):
    def boom(model, label, **kw):
        raise RuntimeError("save failed")

    monkeypatch.setattr(tp, "_lane_commit", boom)
    undone = []
    monkeypatch.setattr(tp, "safe_undo", lambda model: undone.append(model))
    out = world.mgr.set_property("Sales[Amount]", "IsHidden", True)
    assert out["success"] is False and undone == [world.model]


def test_compatibility_violation_is_reported_with_the_required_level(world, monkeypatch):
    class CompatibilityViolationException(Exception):
        pass

    def boom(model, label, **kw):
        raise CompatibilityViolationException(
            "The database compatibility level of 1500 is below the minimal compatability level of 1609 needed"
        )

    monkeypatch.setattr(tp, "_lane_commit", boom)
    monkeypatch.setattr(tp, "safe_undo", lambda model: None)
    out = world.mgr.set_property("Sales[Amount]", "IsHidden", True)
    assert (
        out["error_type"] == "compatibility_level_too_low"
        and out["required_compatibility_level"] == 1609
    )


def test_min_compatibility_level_precheck_never_touches_the_model(world):
    world.model.Database.CompatibilityLevel = 1500
    world.model.SelectionExpressionBehavior = "Automatic"
    world.model.__tom_kinds__ = {
        "SelectionExpressionBehavior": ("enum", ["Automatic", "Visual", "NonVisual"])
    }
    out = world.mgr.set_property("model", "SelectionExpressionBehavior", "visual")
    assert out["error_type"] == "compatibility_level_too_low"
    assert out["required_compatibility_level"] == 1609 and world.commits == []


def test_annotations_are_pseudo_properties(world, monkeypatch):
    class Annotation(Obj):
        def __init__(self):
            super().__init__(Name=None, Value=None)

    tabular = types.ModuleType("Microsoft.AnalysisServices.Tabular")
    tabular.Annotation = Annotation
    analysis = types.ModuleType("Microsoft.AnalysisServices")
    analysis.Tabular = tabular
    root = types.ModuleType("Microsoft")
    root.AnalysisServices = analysis
    for name, mod in (
        ("Microsoft", root),
        ("Microsoft.AnalysisServices", analysis),
        ("Microsoft.AnalysisServices.Tabular", tabular),
    ):
        monkeypatch.setitem(sys.modules, name, mod)
    out = world.mgr.set_property("Sales[Amount]", "Annotations[Owner]", "Finance")
    assert out["success"] and out["property"] == "Annotations[Owner]" and out["previous"] is None
    assert (
        world.amount.Annotations[0].Name == "Owner"
        and world.amount.Annotations[0].Value == "Finance"
    )
    got = world.mgr.get_property("Sales[Amount]", "Annotations[Owner]")
    assert got["value"] == "Finance"
    assert (
        world.mgr.set_property("Sales[Amount]", "Annotations[Owner]", "Finance")["action"] == "noop"
    )
    upd = world.mgr.set_property("Sales[Amount]", "Annotations[Owner]", "Ops", dry_run=True)
    assert upd["dry_run"] and world.amount.Annotations[0].Value == "Finance"


def test_not_connected(monkeypatch):
    mgr = TomPropertyManager()
    mgr._open = lambda: (None, None, None, "Not connected")
    assert mgr.list_properties("model")["error_type"] == "connection_error"
    assert mgr.set_property("model", "Description", "x")["error_type"] == "connection_error"


# ---------------------------------------------------------------------------
# real TOM (in memory)
# ---------------------------------------------------------------------------
def _real_tom():
    try:
        from core.infrastructure.dll_paths import load_amo_assemblies

        if not load_amo_assemblies():
            return None
        import Microsoft.AnalysisServices.Tabular as T  # type: ignore

        return T
    except Exception:  # noqa: BLE001
        return None


@pytest.fixture
def real(monkeypatch):
    T = _real_tom()
    if T is None:
        pytest.skip("AMO/TOM assemblies not loadable here")
    db = T.Database("x")
    db.CompatibilityLevel = 1702
    model = T.Model()
    db.Model = model
    table = T.Table()
    table.Name = "Sales"
    model.Tables.Add(table)
    for name, dtype in (("Amount", T.DataType.Double), ("Month", T.DataType.String)):
        col = T.DataColumn()
        col.Name, col.DataType, col.SourceColumn = name, dtype, name
        table.Columns.Add(col)
    measure = T.Measure()
    measure.Name, measure.Expression = "Total", "SUM(Sales[Amount])"
    table.Measures.Add(measure)
    commits = []
    monkeypatch.setattr(tp, "_lane_commit", lambda m, label, **kw: commits.append(label) or {})
    mgr = TomPropertyManager()
    mgr._open = lambda: (FakeServer(), db, model, None)
    return types.SimpleNamespace(T=T, db=db, model=model, table=table, mgr=mgr, commits=commits)


def test_real_tom_reflection_lists_enum_values_and_settable_flags(real):
    out = real.mgr.list_properties("Sales[Amount]")
    by = {p["name"]: p for p in out["properties"]}
    assert by["SummarizeBy"]["type"] == "enum" and "DistinctCount" in by["SummarizeBy"]["values"]
    assert by["SummarizeBy"]["settable"] and by["IsAvailableInMDX"]["settable"]
    assert (
        not by["Name"]["settable"] and not by["IsKey"]["settable"] and not by["State"]["settable"]
    )


def test_real_tom_set_enum_bool_and_sort_by_reference(real):
    col = real.table.Columns.Find("Amount")
    assert real.mgr.set_property("Sales[Amount]", "SummarizeBy", "sum")["value"] == "Sum"
    assert str(col.SummarizeBy) == "Sum"
    assert real.mgr.set_property("Sales[Amount]", "IsAvailableInMDX", False)["success"]
    assert col.IsAvailableInMDX is False
    assert real.mgr.set_property("Sales[Amount]", "SortByColumn", "Month")["success"]
    assert col.SortByColumn.Name == "Month"
    assert (
        real.mgr.set_property("Sales[Amount]", "SummarizeBy", "bogus")["error_type"]
        == "invalid_parameters"
    )
    assert real.mgr.set_property("model", "DefaultMeasure", "Sales[Total]")["success"]
    assert real.model.DefaultMeasure.Name == "Total"
    assert real.mgr.set_property("Sales[Amount]", "ExtendedProperties[k]", "v")["success"]
    assert real.table.Columns.Find("Amount").ExtendedProperties.Count == 1


def test_real_tom_enforces_compatibility_level_and_the_failure_is_reported(real, monkeypatch):
    real.db.CompatibilityLevel = (
        1500  # TOM then rejects ValueFilterBehavior (needs 1606) on assignment
    )
    monkeypatch.setitem(
        tp.MIN_CL, ("model", "ValueFilterBehavior"), 0
    )  # bypass our pre-check: TOM decides
    monkeypatch.setattr(tp, "safe_undo", lambda m: None)
    out = real.mgr.set_property("model", "ValueFilterBehavior", "Independent")
    assert out["success"] is False and out["error_type"] == "compatibility_level_too_low"
    assert out["required_compatibility_level"] == 1606


# ---------------------------------------------------------------------------
# handler: gating level + classification
# ---------------------------------------------------------------------------
def test_gate_level_is_per_property():
    from core.autonomous.gating import Severity
    from server.handlers.model_operations_handler import _severity_for

    assert _severity_for("any", "list_properties", {"ref": "model"}) is None
    assert _severity_for("any", "get_property", {"ref": "model"}) is None
    assert (
        _severity_for("any", "set_property", {"ref": "Sales[Amount]", "property_name": "IsHidden"})
        is Severity.DESTRUCTIVE
    )
    assert (
        _severity_for(
            "any", "set_property", {"ref": "model", "property_name": "ValueFilterBehavior"}
        )
        is Severity.CATASTROPHIC
    )
    assert (
        _severity_for(
            "any",
            "set_property",
            {"ref": "relationship:r", "property_name": "security_filtering_behavior"},
        )
        is Severity.CATASTROPHIC
    )
    assert (
        _severity_for(
            "any",
            "set_property",
            {"ref": "model", "property_name": "ValueFilterBehavior", "dry_run": True},
        )
        is None
    )
    # table_name + column_name stand in for ref
    assert (
        _severity_for(
            "any",
            "set_property",
            {"table_name": "Sales", "column_name": "Amount", "property_name": "IsHidden"},
        )
        is Severity.DESTRUCTIVE
    )


def test_classification():
    from server.op_classification import classify

    assert (
        classify("02_Model_Operations", {"object_type": "any", "operation": "list_properties"})
        == "read"
    )
    assert (
        classify("02_Model_Operations", {"object_type": "any", "operation": "get_property"})
        == "read"
    )
    assert (
        classify("02_Model_Operations", {"object_type": "any", "operation": "set_property"})
        == "write"
    )
    assert (
        classify(
            "02_Model_Operations",
            {"object_type": "any", "operation": "set_property", "dry_run": True},
        )
        == "read"
    )


def test_handler_routes_any_to_the_manager(monkeypatch, world):
    from server.handlers import model_operations_handler as h

    monkeypatch.setitem(h._handlers, "any", world.mgr)
    out = h.handle_model_operations(
        {
            "object_type": "any",
            "operation": "set_property",
            "ref": "Sales[Amount]",
            "property_name": "IsHidden",
            "value": "true",
        }
    )
    assert out["success"] and world.amount.IsHidden is True
    listed = h.handle_model_operations(
        {
            "object_type": "any",
            "operation": "list_properties",
            "table_name": "Sales",
            "column_name": "Month",
        }
    )
    assert listed["success"] and listed["ref"] == "Sales[Month]"
    bad = h.handle_model_operations({"object_type": "any", "operation": "frobnicate"})
    assert bad["error_type"] == "invalid_operation" and "set_property" in bad["valid_operations"]
