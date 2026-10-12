"""WP-P wave 2 -- one DBSCHEMA_CATALOGS round trip per connection, not per model op (P-03)."""

from __future__ import annotations

import contextlib
from types import SimpleNamespace

import pytest

import core.operations.amo_helpers as amo


class _Reader:
    def __init__(self, name):
        self.name = name

    def Read(self):
        return True

    def GetValue(self, _i):
        return self.name

    def Close(self):
        pass


class _Cmd:
    executed = 0
    catalog = "ModelA"

    def __init__(self, _text, _conn):
        pass

    def ExecuteReader(self):
        _Cmd.executed += 1
        return _Reader(_Cmd.catalog)


class _Dbs:
    Count = 1

    def __init__(self, known):
        self.known = known

    def GetByName(self, name):
        if name not in self.known:
            raise KeyError(name)
        return SimpleNamespace(Name=name, Model=f"model-of-{name}")

    def __getitem__(self, i):
        return SimpleNamespace(Name=next(iter(self.known)))


class _Server:
    known = {"ModelA"}
    connects = 0

    def __init__(self):
        self.Databases = _Dbs(_Server.known)

    def Connect(self, _cs):
        _Server.connects += 1

    def Disconnect(self):
        pass


def _state(conn_str="Data Source=localhost:5001", generation=0):
    conn = SimpleNamespace(ConnectionString=conn_str)
    return SimpleNamespace(
        connection_manager=SimpleNamespace(get_connection=lambda: conn),
        connection_generation=generation,
        command_gate=lambda purpose="": contextlib.nullcontext(),
    )


@pytest.fixture(autouse=True)
def _fakes(monkeypatch):
    monkeypatch.setattr(amo, "AMOServer", _Server)
    monkeypatch.setattr(amo, "AdomdCommand", _Cmd)
    from core.infrastructure import offline_model

    monkeypatch.setattr(offline_model, "is_active", lambda: False)
    _Cmd.executed = 0
    _Cmd.catalog = "ModelA"
    _Server.known = {"ModelA"}
    _Server.connects = 0
    amo.invalidate_db_name_cache()
    yield
    amo.invalidate_db_name_cache()


def test_catalog_is_queried_once_per_connection():
    state = _state()
    for _ in range(5):
        server, db, model, err = amo.get_server_db_model(state)
        assert err is None and db.Name == "ModelA" and model == "model-of-ModelA"
    assert _Cmd.executed == 1
    assert _Server.connects == 5  # the AMO session itself is still per call (writes need it)


def test_a_new_connection_generation_or_string_looks_the_name_up_again():
    amo.get_server_db_model(_state(generation=0))
    amo.get_server_db_model(_state(generation=1))  # connection swap bumps the generation
    assert _Cmd.executed == 2
    _Cmd.catalog = "ModelB"
    _Server.known = {"ModelB"}
    _server, db, _m, err = amo.get_server_db_model(_state("Data Source=localhost:5002", 1))
    assert err is None and db.Name == "ModelB" and _Cmd.executed == 3
    amo.get_server_db_model(_state("Data Source=localhost:5002", 1))
    assert _Cmd.executed == 3


def test_a_remembered_name_that_no_longer_resolves_is_dropped_and_looked_up_again():
    state = _state()
    amo.get_server_db_model(state)
    _Cmd.catalog = "Renamed"
    _Server.known = {"Renamed"}  # same endpoint, different database now
    _server, db, _m, err = amo.get_server_db_model(state)
    assert err is None and db.Name == "Renamed"
    assert _Cmd.executed == 2


def test_cache_is_bounded():
    for port in range(40):
        amo.get_server_db_model(_state(f"Data Source=localhost:{6000 + port}"))
    assert len(amo._DB_NAME_CACHE) <= amo._DB_NAME_CACHE_MAX


def test_connections_without_a_connection_string_are_never_cached():
    state = _state()
    state.connection_manager = SimpleNamespace(
        get_connection=lambda: SimpleNamespace(ConnectionString=None)
    )
    for _ in range(3):
        amo.get_server_db_model(state)
    assert _Cmd.executed == 3 and amo._DB_NAME_CACHE == {}
