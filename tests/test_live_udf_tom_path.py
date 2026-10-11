"""Live 2026-09-23: with AMO 19.117 Model.Functions is visible on Desktop 2.157 and the
TMSL createOrReplace.function path is rejected -> UDF writes go through TOM first."""

import sys
import types

import core.operations.function_crud_manager as fcm


class _Coll(list):
    @property
    def Count(self):
        return len(self)

    def Add(self, x):
        self.append(x)

    def Remove(self, x):
        list.remove(self, x)

    def Find(self, name):
        return next((f for f in self if f.Name == name), None)


class _Model:
    def __init__(self):
        self.Functions = _Coll()
        self.saved = 0

    def SaveChanges(self):
        self.saved += 1


class _Server:
    def Disconnect(self):
        self.disconnected = True


def _setup(monkeypatch, rows):
    tom = types.ModuleType("Microsoft.AnalysisServices.Tabular")

    class Function:
        Name = ""
        Expression = ""
        Description = ""

    tom.Function = Function
    ms = types.ModuleType("Microsoft")
    asv = types.ModuleType("Microsoft.AnalysisServices")
    asv.Tabular = tom
    ms.AnalysisServices = asv
    monkeypatch.setitem(sys.modules, "Microsoft", ms)
    monkeypatch.setitem(sys.modules, "Microsoft.AnalysisServices", asv)
    monkeypatch.setitem(sys.modules, "Microsoft.AnalysisServices.Tabular", tom)
    monkeypatch.setattr(fcm, "AMO_AVAILABLE", True)
    model, server = _Model(), _Server()
    monkeypatch.setattr(fcm, "get_server_db_model", lambda cs: (server, object(), model, None))
    mgr = fcm.FunctionCrudManager.__new__(fcm.FunctionCrudManager)
    monkeypatch.setattr(mgr, "_udf_rows", lambda: (rows, None), raising=False)
    return mgr, model, server


def test_create_goes_through_tom_when_amo_sees_functions(monkeypatch):
    mgr, model, server = _setup(monkeypatch, rows=[])
    res = mgr._write_via_tom("create", "LiveDouble", "(x: NUMERIC) => x * 2")
    assert res == {"success": True, "via": "tom"}
    assert [f.Name for f in model.Functions] == ["LiveDouble"] and model.saved == 1
    assert getattr(server, "disconnected", False)


def test_blind_amo_falls_back_to_tmsl(monkeypatch):
    # DMV shows functions but this AMO build sees none -> None (caller uses TMSL)
    mgr, model, _ = _setup(monkeypatch, rows=[{"Name": "Existing"}])
    assert mgr._write_via_tom("create", "X", "() => 1") is None
    assert model.saved == 0
