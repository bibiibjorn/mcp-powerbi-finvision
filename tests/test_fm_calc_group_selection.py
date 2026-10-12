"""F-20c: calculation-group selection expressions + Model.SelectionExpressionBehavior (CL-gated).

Manager level with TOM faked (the real-TOM variants at the bottom skip when the AMO assemblies are
not loadable): both selection expressions and the behavior are written in ONE save, an empty string
clears an expression, a model below the required compatibility level is refused BEFORE anything is
written, and TOM's own CompatibilityViolationException is reported as compatibility_level_too_low.
"""

from __future__ import annotations

import sys
import types

import pytest

from core.operations.calculation_group_manager import CalculationGroupManager
from core.operations.calculation_group_operations import CalculationGroupOperationsHandler
from tests._c_fake_tom import Coll, FakeServer, Obj


class Model:
    def __init__(self, tables=(), level=1702):
        self.Tables = Coll(tables)
        self.SelectionExpressionBehavior = "Automatic"
        self.Database = Obj(CompatibilityLevel=level)
        self.saves = 0
        self.undos = 0

    def SaveChanges(self):
        self.saves += 1

    def UndoLocalChanges(self):
        self.undos += 1


def fake_tabular(monkeypatch):
    class CalculationGroupExpression(Obj):
        def __init__(self):
            super().__init__(Expression=None)

    class Behavior:
        Automatic, Visual, NonVisual = "Automatic", "Visual", "NonVisual"

    tabular = types.ModuleType("Microsoft.AnalysisServices.Tabular")
    tabular.CalculationGroupExpression = CalculationGroupExpression
    tabular.SelectionExpressionBehaviorType = Behavior
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
    return tabular


def setup(monkeypatch, level=1702):
    tabular = fake_tabular(monkeypatch)
    cg = Obj(
        Precedence=10,
        Description=None,
        MultipleOrEmptySelectionExpression=None,
        NoSelectionExpression=None,
    )
    table = Obj(Name="Time", CalculationGroup=cg, IsHidden=False)
    model = Model([table], level)
    mgr = CalculationGroupManager(None)
    mgr._connect_amo_server_db = lambda: (
        FakeServer(),
        Obj(Model=model, CompatibilityLevel=level),
        tabular,
    )
    commits = []
    import core.operations.write_lane as wl

    monkeypatch.setattr(
        wl, "commit", lambda m, label, **kw: commits.append(label) or m.SaveChanges() or {}
    )
    return mgr, model, cg, commits


def test_sets_both_expressions_and_the_behavior_in_one_save(monkeypatch):
    mgr, model, cg, commits = setup(monkeypatch)
    out = mgr.update_calculation_group(
        "Time",
        multiple_or_empty_selection_expression='"Multiple"',
        no_selection_expression="SELECTEDMEASURE()",
        selection_expression_behavior="visual",
    )
    assert out["success"] is True and model.saves == 1 and commits == ["calculation_group/update"]
    assert out["updated"] == [
        "multiple_or_empty_selection_expression",
        "no_selection_expression",
        "selection_expression_behavior",
    ]
    assert cg.MultipleOrEmptySelectionExpression.Expression == '"Multiple"'
    assert cg.NoSelectionExpression.Expression == "SELECTEDMEASURE()"
    assert model.SelectionExpressionBehavior == "Visual"
    assert (
        out["previous_selection_expression_behavior"] == "Automatic" and "MODEL-wide" in out["note"]
    )


def test_empty_string_clears_an_expression(monkeypatch):
    mgr, model, cg, _ = setup(monkeypatch)
    mgr.update_calculation_group("Time", no_selection_expression="1")
    assert cg.NoSelectionExpression.Expression == "1"
    out = mgr.update_calculation_group("Time", no_selection_expression="")
    assert out["success"] and cg.NoSelectionExpression is None


@pytest.mark.parametrize(
    "kwargs,level,needed",
    [
        ({"no_selection_expression": "1"}, 1600, 1605),
        ({"multiple_or_empty_selection_expression": "1"}, 1500, 1605),
        ({"selection_expression_behavior": "visual"}, 1608, 1609),
    ],
)
def test_below_the_required_level_nothing_is_written(monkeypatch, kwargs, level, needed):
    mgr, model, cg, commits = setup(monkeypatch, level=level)
    out = mgr.update_calculation_group("Time", **kwargs)
    assert out["error_type"] == "compatibility_level_too_low"
    assert out["required_compatibility_level"] == needed and out["compatibility_level"] == level
    assert commits == [] and model.saves == 0 and cg.NoSelectionExpression is None


def test_invalid_behavior_and_nothing_to_update(monkeypatch):
    mgr, model, cg, commits = setup(monkeypatch)
    assert (
        mgr.update_calculation_group("Time", selection_expression_behavior="sideways")["error_type"]
        == "invalid_parameters"
    )
    assert mgr.update_calculation_group("Time")["error_type"] == "invalid_parameters"
    assert commits == []


def test_tom_compatibility_exception_is_reported_and_rolled_back(monkeypatch):
    mgr, model, cg, _ = setup(monkeypatch)

    class CompatibilityViolationException(Exception):
        pass

    import core.operations.write_lane as wl

    def boom(m, label, **kw):
        raise CompatibilityViolationException(
            "The database compatibility level of 1604 is below the minimal compatability level of 1605 needed"
        )

    monkeypatch.setattr(wl, "commit", boom)
    monkeypatch.setattr(wl, "safe_undo", lambda m: m.UndoLocalChanges())
    out = mgr.update_calculation_group("Time", no_selection_expression="1")
    assert (
        out["error_type"] == "compatibility_level_too_low"
        and out["required_compatibility_level"] == 1605
    )
    assert model.undos == 1


def test_handler_forwards_the_new_arguments():
    seen = {}
    calc = types.SimpleNamespace(
        update_calculation_group=lambda **kw: seen.update(kw) or {"success": True}
    )
    from core.operations import calculation_group_operations as cgo

    orig = cgo.get_manager_or_error
    cgo.get_manager_or_error = lambda name, *a: calc
    try:
        CalculationGroupOperationsHandler().execute(
            {
                "operation": "update",
                "group_name": "Time",
                "no_selection_expression": "1",
                "multiple_or_empty_selection_expression": "2",
                "selection_expression_behavior": "nonvisual",
            }
        )
    finally:
        cgo.get_manager_or_error = orig
    assert seen["no_selection_expression"] == "1"
    assert seen["multiple_or_empty_selection_expression"] == "2"
    assert seen["selection_expression_behavior"] == "nonvisual"


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


def _real_manager(level):
    T = _real_tom()
    if T is None:
        pytest.skip("AMO/TOM assemblies not loadable here")
    db = T.Database("x")
    db.CompatibilityLevel = level
    model = T.Model()
    db.Model = model
    table = T.Table()
    table.Name = "Time"
    table.CalculationGroup = T.CalculationGroup()
    model.Tables.Add(table)
    mgr = CalculationGroupManager(None)
    mgr._connect_amo_server_db = lambda: (FakeServer(), db, T)
    return mgr, db, model, table


def test_real_tom_round_trip(monkeypatch):
    mgr, db, model, table = _real_manager(1702)
    import core.operations.write_lane as wl

    monkeypatch.setattr(wl, "commit", lambda m, label, **kw: {})
    out = mgr.update_calculation_group(
        "Time",
        no_selection_expression="SELECTEDMEASURE()",
        selection_expression_behavior="NonVisual",
    )
    assert out["success"], out
    assert table.CalculationGroup.NoSelectionExpression.Expression == "SELECTEDMEASURE()"
    assert str(model.SelectionExpressionBehavior) == "NonVisual"
    cleared = mgr.update_calculation_group("Time", no_selection_expression="")
    assert cleared["success"] and table.CalculationGroup.NoSelectionExpression is None


def test_real_tom_refuses_below_the_level_without_touching_the_model():
    mgr, db, model, table = _real_manager(1605)
    out = mgr.update_calculation_group("Time", selection_expression_behavior="Visual")
    assert (
        out["error_type"] == "compatibility_level_too_low"
        and out["required_compatibility_level"] == 1609
    )
    assert str(model.SelectionExpressionBehavior) == "Automatic"
