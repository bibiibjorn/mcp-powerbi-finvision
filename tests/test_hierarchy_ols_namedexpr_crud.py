"""Regression tests for HierarchyCrudManager / NamedExpressionCrudManager /
OlsCrudManager — the three managers that were 100% broken because they read a
nonexistent `connection_state.model` attribute instead of acquiring the live
model via `amo_helpers.get_server_db_model` like every other CRUD manager.

Mocks at the AMO boundary (get_server_db_model) — never touches live CLR/AMO.
"""

import types


from core.operations import hierarchy_crud_manager as hmod
from core.operations import named_expression_crud_manager as nmod
from core.operations import ols_crud_manager as omod


# ---------------------------------------------------------------------------
# Fake TOM model scaffolding
# ---------------------------------------------------------------------------
class _NamedColl:
    """A TOM-like collection: iterable + Find(name) + Count + Add/Remove."""

    def __init__(self, items=None):
        self._items = list(items or [])

    def __iter__(self):
        return iter(self._items)

    @property
    def Count(self):
        return len(self._items)

    def Find(self, name):
        for it in self._items:
            if getattr(it, "Name", None) == name:
                return it
        return None

    def Add(self, item):
        self._items.append(item)

    def Remove(self, item):
        self._items.remove(item)


class _Level:
    def __init__(self, name, ordinal, column):
        self.Name, self.Ordinal, self.Column = name, ordinal, column


class _Hier:
    def __init__(self, name, levels=None):
        self.Name = name
        self.Description = ""
        self.IsHidden = False
        self.DisplayFolder = ""
        self.Levels = _NamedColl(levels or [])


class _Col:
    def __init__(self, name):
        self.Name = name


class _Table:
    def __init__(self, name, columns=None, hierarchies=None):
        self.Name = name
        self.Columns = _NamedColl(columns or [])
        self.Hierarchies = _NamedColl(hierarchies or [])


class _Expr:
    def __init__(self, name, expression="let x = 1 in x"):
        self.Name = name
        self.Expression = expression
        self.Description = ""
        self.Kind = "M"


class _FakeModel:
    def __init__(self, tables=None, expressions=None, roles=None):
        self.Tables = _NamedColl(tables or [])
        self.Expressions = _NamedColl(expressions or [])
        self.Roles = _NamedColl(roles or [])
        self.save_count = 0

    def SaveChanges(self):
        self.save_count += 1


def _patch_amo(monkeypatch, target_module, model):
    """Patch get_server_db_model imported inside the manager method body.

    The managers do `from core.operations.amo_helpers import get_server_db_model`
    at call time, so patch it on amo_helpers itself.
    """
    import core.operations.amo_helpers as amo

    disconnects = {"n": 0}
    server = types.SimpleNamespace(
        Disconnect=lambda: disconnects.__setitem__("n", disconnects["n"] + 1)
    )
    db = types.SimpleNamespace(Model=model)
    monkeypatch.setattr(amo, "get_server_db_model", lambda cs: (server, db, model, None))
    return disconnects


# ---------------------------------------------------------------------------
# Hierarchy
# ---------------------------------------------------------------------------
def test_list_hierarchies_returns_data_not_attribute_error(monkeypatch):
    col = _Col("Country")
    hier = _Hier("Geography", [_Level("Country", 0, col)])
    model = _FakeModel(tables=[_Table("Geo", columns=[col], hierarchies=[hier])])
    disc = _patch_amo(monkeypatch, hmod, model)

    mgr = hmod.HierarchyCrudManager()
    out = mgr.list_hierarchies()
    assert out["success"] is True
    assert out["count"] == 1
    assert out["hierarchies"][0]["name"] == "Geography"
    assert disc["n"] == 1  # server disconnected in finally


def test_create_hierarchy_saves_once_and_disconnects(monkeypatch):
    # create_hierarchy imports TOM inside the body; patch sys.modules so
    # `import Microsoft.AnalysisServices.Tabular as TOM` resolves to our fake.
    import sys

    monkeypatch.setitem(sys.modules, "Microsoft", types.ModuleType("Microsoft"))
    ms_as = types.ModuleType("Microsoft.AnalysisServices")
    monkeypatch.setitem(sys.modules, "Microsoft.AnalysisServices", ms_as)
    fake_tom = types.ModuleType("Microsoft.AnalysisServices.Tabular")
    fake_tom.Hierarchy = lambda: _Hier("__tmp__")
    fake_tom.Level = lambda: _Level(None, 0, None)
    monkeypatch.setitem(sys.modules, "Microsoft.AnalysisServices.Tabular", fake_tom)

    col = _Col("Country")
    model = _FakeModel(tables=[_Table("Geo", columns=[col])])
    disc = _patch_amo(monkeypatch, hmod, model)

    mgr = hmod.HierarchyCrudManager()
    out = mgr.create_hierarchy("Geo", "Geography", [{"name": "Country", "column": "Country"}])
    assert out["success"] is True
    assert model.save_count == 1
    assert disc["n"] == 1


def test_hierarchy_not_connected_is_structured_error(monkeypatch):
    import core.operations.amo_helpers as amo

    monkeypatch.setattr(amo, "get_server_db_model", lambda cs: (None, None, None, "Not connected"))
    mgr = hmod.HierarchyCrudManager()
    out = mgr.list_hierarchies()
    assert out["success"] is False
    assert "connect" in out["error"].lower()


# ---------------------------------------------------------------------------
# Named expression
# ---------------------------------------------------------------------------
def test_list_expressions_returns_data(monkeypatch):
    model = _FakeModel(expressions=[_Expr("MyParam")])
    disc = _patch_amo(monkeypatch, nmod, model)
    mgr = nmod.NamedExpressionCrudManager()
    out = mgr.list_expressions()
    assert out["success"] is True
    assert out["count"] == 1
    assert out["expressions"][0]["name"] == "MyParam"
    assert disc["n"] == 1


def test_update_expression_saves_and_disconnects(monkeypatch):
    expr = _Expr("MyParam")
    model = _FakeModel(expressions=[expr])
    disc = _patch_amo(monkeypatch, nmod, model)
    mgr = nmod.NamedExpressionCrudManager()
    out = mgr.update_expression("MyParam", expression="let y = 2 in y")
    assert out["success"] is True
    assert expr.Expression == "let y = 2 in y"
    assert model.save_count == 1
    assert disc["n"] == 1


# ---------------------------------------------------------------------------
# OLS
# ---------------------------------------------------------------------------
def test_list_ols_rules_returns_data(monkeypatch):
    cp = types.SimpleNamespace(Name="Salary", MetadataPermission="None_")
    tp = types.SimpleNamespace(Name="Employee", ColumnPermissions=_NamedColl([cp]))
    role = types.SimpleNamespace(Name="Analyst", TablePermissions=_NamedColl([tp]))
    model = _FakeModel(roles=[role])
    disc = _patch_amo(monkeypatch, omod, model)
    mgr = omod.OlsCrudManager()
    out = mgr.list_ols_rules()
    assert out["success"] is True
    assert out["count"] == 1
    assert out["ols_rules"][0]["column"] == "Salary"
    assert disc["n"] == 1
