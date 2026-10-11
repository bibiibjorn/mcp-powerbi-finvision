"""DAXInjector with fakes (review E-01 / E-14 — the module had 0 % coverage).

Fakes mirror the real seams: an ADOMD connection with ``State`` /
``ConnectionString``, an AMO ``Server`` with ``Connect`` / ``Databases`` /
``Disconnect``, PascalCase TOM tables/measures and an ``AdomdCommand`` for the
catalog lookup.
"""

from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from core.dax import dax_injector as di


class Coll(list):
    def Add(self, x):  # noqa: N802
        self.append(x)

    def Remove(self, x):  # noqa: N802
        self.remove(x)


class TomMeasure:
    def __init__(self, Name="", Expression=""):  # noqa: N803
        self.Name = Name
        self.Expression = Expression
        self.FormatString = None
        self.DisplayFolder = None
        self.Description = None
        self.DataCategory = None
        self.IsHidden = False
        self.ErrorMessage = ""
        self.KPI = None
        self.FormatStringDefinition = None
        self.DetailRowsDefinition = None


class TomModel:
    def __init__(self, tables):
        self.Tables = Coll(tables)
        self.Roles = Coll()
        self.Functions = Coll()
        self.saves = 0
        self.undos = 0
        self.fail_save = False

    def SaveChanges(self):  # noqa: N802
        if self.fail_save:
            raise RuntimeError("save exploded")
        self.saves += 1

    def UndoLocalChanges(self):  # noqa: N802
        self.undos += 1


def table(name, *measures):
    return NS(
        Name=name,
        Measures=Coll(TomMeasure(n, e) for n, e in measures),
        Columns=Coll(),
        Partitions=Coll(),
        CalculationGroup=None,
    )


class FakeDatabases(list):
    @property
    def Count(self):  # noqa: N802
        return len(self)

    def GetByName(self, name):  # noqa: N802
        return next(d for d in self if d.Name == name)


class FakeServerFactory:
    def __init__(self, model):
        self.model = model
        self.connected_with = []
        self.disconnects = 0

    def __call__(self):
        factory = self

        class _Server:
            Databases = FakeDatabases([NS(Name="db-1", Model=factory.model)])

            def Connect(self, cs):  # noqa: N802
                factory.connected_with.append(cs)

            def Disconnect(self):  # noqa: N802
                factory.disconnects += 1

        return _Server()


class FakeReader:
    def __init__(self, value):
        self.value = value
        self.read = False

    def Read(self):  # noqa: N802
        if self.read:
            return False
        self.read = True
        return True

    def GetValue(self, i):  # noqa: N802
        return self.value

    def Close(self):  # noqa: N802
        pass


class OpenConn:
    ConnectionString = "Data Source=localhost:1234"
    State = NS(ToString=lambda: "Open")


@pytest.fixture
def env(monkeypatch):
    model = TomModel([table("Sales", ("A", "1"), ("B", "[A] + 1"))])
    factory = FakeServerFactory(model)
    monkeypatch.setattr(di, "AMO_AVAILABLE", True)
    monkeypatch.setattr(di, "AMOServer", factory)
    monkeypatch.setattr(di, "Measure", TomMeasure)
    monkeypatch.setattr(
        di, "AdomdCommand", lambda q, c: NS(ExecuteReader=lambda: FakeReader("db-1"))
    )
    bumps = []
    from core.infrastructure import connection_state as cs_mod

    monkeypatch.setattr(
        cs_mod.connection_state, "bump_model_generation", lambda reason: bumps.append(reason) or 1
    )
    return NS(model=model, factory=factory, inj=di.DAXInjector(OpenConn()), bumps=bumps)


def test_upsert_updates_existing_and_bumps_generation(env):
    out = env.inj.upsert_measure("Sales", "A", "2", format_string="0.0")
    assert out["success"] and out["action"] == "updated"
    m = env.model.Tables[0].Measures[0]
    assert m.Expression == "2" and m.FormatString == "0.0"
    assert env.model.saves == 1 and env.factory.disconnects == 1
    assert env.factory.connected_with == [OpenConn.ConnectionString]
    assert env.bumps  # cached DAX results invalidated (E-02)


def test_upsert_creates_and_respects_create_if_missing(env):
    out = env.inj.upsert_measure("Sales", "New", "3", display_folder="F")
    assert out["action"] == "created"
    assert env.model.Tables[0].Measures[-1].DisplayFolder == "F"
    out = env.inj.upsert_measure("Sales", "Typo", "3", create_if_missing=False)
    assert out["error_type"] == "measure_not_found"
    out = env.inj.upsert_measure("Nope", "X", "3")
    assert out["error_type"] == "table_not_found"
    out = env.inj.upsert_measure("Sales", "Empty", "")
    assert out["error_type"] == "invalid_parameters"


def test_upsert_save_failure_reports_and_disconnects(env):
    env.model.fail_save = True
    out = env.inj.upsert_measure("Sales", "A", "2")
    assert out["success"] is False and out["error_type"] == "injection_error"
    assert env.factory.disconnects == 1


def test_delete_and_rename(env):
    assert env.inj.delete_measure("Sales", "Missing")["success"] is False
    out = env.inj.delete_measure("Sales", "B")
    assert out["success"] and [m.Name for m in env.model.Tables[0].Measures] == ["A"]
    out = env.inj.rename_measure("Sales", "A", "A2")
    assert out["success"] and env.model.Tables[0].Measures[0].Name == "A2"


def test_apply_measure_changes_one_session(env):
    out = env.inj.apply_measure_changes(
        upserts=[
            {
                "table_name": "Sales",
                "measure_name": "B",
                "dax_expression": "1 + 1",
                "expected_expression": "[A] + 1",
            },
        ],
        deletes=[{"table_name": "Sales", "measure_name": "A", "guard_refs": True}],
    )
    assert out["committed"] is True and out["database"] == "db-1"
    assert [m.Name for m in env.model.Tables[0].Measures] == ["B"]
    assert env.model.saves == 1 and env.factory.disconnects == 1
    assert out["deleted"][0]["snapshot"]["expression"] == "1"


def test_apply_measure_changes_guard_refuses_referenced_delete(env):
    out = env.inj.apply_measure_changes(
        upserts=[], deletes=[{"table_name": "Sales", "measure_name": "A", "guard_refs": True}]
    )
    assert out["committed"] is False
    assert out["delete_refused"][0]["referenced_by"][0]["owner"] == "Sales[B]"
    assert len(env.model.Tables[0].Measures) == 2


def test_verify_connection_reconnects_through_manager(env):
    closed = NS(State=NS(ToString=lambda: "Closed"), ConnectionString="x")
    inj = di.DAXInjector(closed)
    assert inj._verify_connection()["success"] is False
    mgr = NS(
        ensure_connected=lambda max_retries=3: {"connected": True},
        get_connection=lambda: OpenConn(),
    )
    inj.set_connection_manager(mgr)
    assert inj._verify_connection() == {"success": True, "reconnected": True}
    assert isinstance(inj.connection, OpenConn)


def test_connection_state_is_not_a_connection():
    """E-01 root cause: an injector bound to a ConnectionState-like object can
    never verify its connection — callers must use get_live_injector."""
    state_like = NS(is_connected=lambda: True)
    assert di.DAXInjector(state_like)._verify_connection()["success"] is False
    managed = object()
    assert di.get_live_injector(NS(dax_injector=managed)) is managed
    with pytest.raises(RuntimeError):
        di.get_live_injector(NS(dax_injector=None, is_connected=lambda: False))
