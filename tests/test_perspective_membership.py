"""Tests for perspective membership add/remove ops."""

import sys
from unittest.mock import MagicMock, patch


from core.operations.perspective_crud_manager import PerspectiveCrudManager


def _patch_tom():
    """Inject a fake Microsoft.AnalysisServices.Tabular into sys.modules."""
    fake = MagicMock()

    def _make_pt():
        m = MagicMock(_kind="PerspectiveTable")
        m.PerspectiveMeasures = _TomCollection()
        m.PerspectiveColumns = _TomCollection()
        m.PerspectiveHierarchies = _TomCollection()
        return m

    fake.PerspectiveTable = MagicMock(side_effect=_make_pt)
    fake.PerspectiveMeasure = MagicMock(side_effect=lambda: MagicMock(_kind="PerspectiveMeasure"))
    fake.PerspectiveColumn = MagicMock(side_effect=lambda: MagicMock(_kind="PerspectiveColumn"))
    fake.PerspectiveHierarchy = MagicMock(
        side_effect=lambda: MagicMock(_kind="PerspectiveHierarchy")
    )

    # Ensure `import Microsoft.AnalysisServices.Tabular as TOM` resolves to `fake`
    # by chaining the parent module attributes.
    ms = MagicMock()
    ms.AnalysisServices = MagicMock()
    ms.AnalysisServices.Tabular = fake
    return patch.dict(
        sys.modules,
        {
            "Microsoft": ms,
            "Microsoft.AnalysisServices": ms.AnalysisServices,
            "Microsoft.AnalysisServices.Tabular": fake,
        },
    )


def _amo_patch(model):
    """Patch PerspectiveCrudManager._amo_session to yield (server, model, None)."""
    from contextlib import contextmanager

    @contextmanager
    def _fake(self):
        yield (MagicMock(), model, None)

    return patch.object(PerspectiveCrudManager, "_amo_session", _fake)


class _TomCollection(list):
    """List subclass that mimics a TOM collection (Add/Remove methods)."""

    def Add(self, item):
        self.append(item)

    def Remove(self, item):
        self.remove(item)


def _build_model(
    persp_name="Exec", tables=("Sales",), measures=None, columns=None, hierarchies=None
):
    """Build a fake model with one perspective and the named tables."""
    measures = measures or {}
    columns = columns or {}
    hierarchies = hierarchies or {}

    persp_tables = _TomCollection()

    persp = MagicMock()
    persp.Name = persp_name
    persp.PerspectiveTables = persp_tables

    model_tables = []
    for tname in tables:
        t = MagicMock()
        t.Name = tname
        t_measures = []
        for m in measures.get(tname, []):
            mm = MagicMock()
            mm.Name = m
            t_measures.append(mm)
        t.Measures = t_measures
        t_columns = []
        for c in columns.get(tname, []):
            col = MagicMock()
            col.Name = c
            t_columns.append(col)
        t.Columns = t_columns
        t_hier = []
        for h in hierarchies.get(tname, []):
            hh = MagicMock()
            hh.Name = h
            t_hier.append(hh)
        t.Hierarchies = t_hier
        model_tables.append(t)
    model = MagicMock()
    model.Tables = model_tables
    model.Perspectives.Find = lambda n: persp if n == persp_name else None
    return model, persp


def test_add_table_creates_perspective_table():
    model, persp = _build_model(tables=("Sales",))
    with _amo_patch(model), _patch_tom():
        result = PerspectiveCrudManager().add_table("Exec", "Sales")

    assert result["success"] is True
    assert result["action"] == "added"
    assert len(persp.PerspectiveTables) == 1
    assert persp.PerspectiveTables[0].Name == "Sales"
    model.SaveChanges.assert_called_once()


def test_add_table_noop_when_already_present():
    model, persp = _build_model(tables=("Sales",))
    existing = MagicMock()
    existing.Name = "Sales"
    persp.PerspectiveTables.append(existing)
    with _amo_patch(model), _patch_tom():
        result = PerspectiveCrudManager().add_table("Exec", "Sales")
    assert result["success"] is True
    assert result["action"] == "noop"
    model.SaveChanges.assert_not_called()


def test_add_table_unknown_table():
    model, _ = _build_model(tables=("Sales",))
    with _amo_patch(model), _patch_tom():
        result = PerspectiveCrudManager().add_table("Exec", "DoesNotExist")
    assert result["success"] is False
    assert "doesnotexist" in result["error"].lower()


def test_add_table_unknown_perspective():
    model, _ = _build_model(tables=("Sales",))
    with _amo_patch(model), _patch_tom():
        result = PerspectiveCrudManager().add_table("DoesNotExist", "Sales")
    assert result["success"] is False
    assert "doesnotexist" in result["error"].lower()


def test_add_measure_implicitly_adds_table():
    model, persp = _build_model(tables=("Sales",), measures={"Sales": ["Revenue"]})
    with _amo_patch(model), _patch_tom():
        result = PerspectiveCrudManager().add_measure("Exec", "Sales", "Revenue")
    assert result["success"] is True
    assert len(persp.PerspectiveTables) == 1
    pt = persp.PerspectiveTables[0]
    assert pt.Name == "Sales"
    assert len(pt.PerspectiveMeasures) == 1
    model.SaveChanges.assert_called_once()


def test_add_measure_unknown_measure():
    model, _ = _build_model(tables=("Sales",), measures={"Sales": ["Revenue"]})
    with _amo_patch(model), _patch_tom():
        result = PerspectiveCrudManager().add_measure("Exec", "Sales", "DoesNotExist")
    assert result["success"] is False
    assert "doesnotexist" in result["error"].lower()


def test_add_column_adds_to_perspective_table():
    model, persp = _build_model(tables=("Sales",), columns={"Sales": ["Year"]})
    with _amo_patch(model), _patch_tom():
        result = PerspectiveCrudManager().add_column("Exec", "Sales", "Year")
    assert result["success"] is True
    pt = persp.PerspectiveTables[0]
    assert len(pt.PerspectiveColumns) == 1


def test_add_hierarchy_adds_to_perspective_table():
    model, persp = _build_model(tables=("Sales",), hierarchies={"Sales": ["Calendar"]})
    with _amo_patch(model), _patch_tom():
        result = PerspectiveCrudManager().add_hierarchy("Exec", "Sales", "Calendar")
    assert result["success"] is True
    pt = persp.PerspectiveTables[0]
    assert len(pt.PerspectiveHierarchies) == 1


def test_remove_table_removes_perspective_table():
    model, persp = _build_model(tables=("Sales",))
    pt = MagicMock()
    pt.Name = "Sales"
    persp.PerspectiveTables.append(pt)
    with _amo_patch(model), _patch_tom():
        result = PerspectiveCrudManager().remove_table("Exec", "Sales")
    assert result["success"] is True
    assert result["action"] == "removed"
    assert pt not in persp.PerspectiveTables
    model.SaveChanges.assert_called_once()


def test_remove_table_noop_when_absent():
    model, _ = _build_model(tables=("Sales",))
    with _amo_patch(model), _patch_tom():
        result = PerspectiveCrudManager().remove_table("Exec", "Sales")
    assert result["success"] is True
    assert result["action"] == "noop"
    model.SaveChanges.assert_not_called()


def test_remove_measure_drops_pm():
    model, persp = _build_model(tables=("Sales",))
    pt = MagicMock()
    pt.Name = "Sales"
    pm = MagicMock()
    pm.Name = "Revenue"
    pt.PerspectiveMeasures = _TomCollection([pm])
    persp.PerspectiveTables.append(pt)
    with _amo_patch(model), _patch_tom():
        result = PerspectiveCrudManager().remove_measure("Exec", "Sales", "Revenue")
    assert result["success"] is True
    assert pm not in pt.PerspectiveMeasures


def test_add_measure_when_pt_already_exists():
    """When the PerspectiveTable already exists, add_measure must NOT create a duplicate pt."""
    model, persp = _build_model(tables=("Sales",), measures={"Sales": ["Revenue"]})
    existing_pt = MagicMock()
    existing_pt.Name = "Sales"
    existing_pt.PerspectiveMeasures = _TomCollection()
    persp.PerspectiveTables.append(existing_pt)
    with _amo_patch(model), _patch_tom():
        result = PerspectiveCrudManager().add_measure("Exec", "Sales", "Revenue")
    assert result["success"] is True
    assert result["action"] == "added"
    assert len(persp.PerspectiveTables) == 1  # no duplicate pt
    assert len(existing_pt.PerspectiveMeasures) == 1


def test_add_measure_rolls_back_pt_on_save_failure():
    """If SaveChanges throws and pt was implicitly created, the pt must be rolled back."""
    model, persp = _build_model(tables=("Sales",), measures={"Sales": ["Revenue"]})
    model.SaveChanges.side_effect = RuntimeError("boom")
    with _amo_patch(model), _patch_tom():
        result = PerspectiveCrudManager().add_measure("Exec", "Sales", "Revenue")
    assert result["success"] is False
    assert "boom" in result["error"]
    # The implicitly-created PerspectiveTable was rolled back.
    assert len(persp.PerspectiveTables) == 0


def test_remove_measure_noop_when_pt_absent():
    """When the PerspectiveTable for the named table doesn't exist, remove_measure is a noop."""
    model, _ = _build_model(tables=("Sales",))
    with _amo_patch(model), _patch_tom():
        result = PerspectiveCrudManager().remove_measure("Exec", "Sales", "Revenue")
    assert result["success"] is True
    assert result["action"] == "noop"
    model.SaveChanges.assert_not_called()


def test_remove_measure_noop_when_pm_absent():
    """When pt exists but the named measure isn't in it, remove_measure is a noop."""
    model, persp = _build_model(tables=("Sales",))
    pt = MagicMock()
    pt.Name = "Sales"
    pt.PerspectiveMeasures = _TomCollection()
    persp.PerspectiveTables.append(pt)
    with _amo_patch(model), _patch_tom():
        result = PerspectiveCrudManager().remove_measure("Exec", "Sales", "Revenue")
    assert result["success"] is True
    assert result["action"] == "noop"
    model.SaveChanges.assert_not_called()


def test_no_model_returns_not_connected():
    with _amo_patch(None), _patch_tom():
        for fn, args in [
            (PerspectiveCrudManager().add_table, ("E", "T")),
            (PerspectiveCrudManager().add_measure, ("E", "T", "M")),
            (PerspectiveCrudManager().add_column, ("E", "T", "C")),
            (PerspectiveCrudManager().add_hierarchy, ("E", "T", "H")),
            (PerspectiveCrudManager().remove_table, ("E", "T")),
            (PerspectiveCrudManager().remove_measure, ("E", "T", "M")),
            (PerspectiveCrudManager().remove_column, ("E", "T", "C")),
            (PerspectiveCrudManager().remove_hierarchy, ("E", "T", "H")),
        ]:
            res = fn(*args)
            assert res["success"] is False
            assert "not connected" in res["error"].lower()
