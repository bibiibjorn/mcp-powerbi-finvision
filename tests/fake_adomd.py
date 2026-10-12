"""Synthetic ADOMD.NET fakes for the 2026-09-22 slice-B regression tests.

Nothing here touches pythonnet or a live engine. ``FakeServer`` stands in for
one msmdsrv instance: it maps command text -> rows (or an exception), counts
executions, and records the maximum number of commands that were in flight on
the SAME connection at once — the property B-01 is about.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional, Union

Response = Union[List[Dict[str, Any]], Exception, Callable[[str], Any]]


class FakeClrDecimal:
    """Stands in for a pythonnet ``System.Decimal``: str() is culture-formatted."""

    def __init__(self, value: str, culture_text: str):
        self._value = value
        self._culture_text = culture_text

    def GetType(self):
        return type("T", (), {"FullName": "System.Decimal"})()

    def __str__(self) -> str:  # what the old reader did: str(val)
        return self._culture_text


class FakeClrDateTime:
    """Stands in for a pythonnet ``System.DateTime`` (dd/MM culture string)."""

    def __init__(self, y, mo, d, h=0, mi=0, s=0, ms=0):
        self.Year, self.Month, self.Day = y, mo, d
        self.Hour, self.Minute, self.Second, self.Millisecond = h, mi, s, ms
        self.Ticks = ms * 10_000

    def GetType(self):
        return type("T", (), {"FullName": "System.DateTime"})()

    def __str__(self) -> str:
        return f"{self.Day:02d}/{self.Month:02d}/{self.Year} {self.Hour:02d}:{self.Minute:02d}:{self.Second:02d}"


class FakeServer:
    def __init__(self, name: str = "server"):
        self.name = name
        self.responses: Dict[str, Response] = {}
        self.default: Response = [{"[v]": 1}]
        self.executions: List[str] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self.delay_s = 0.0
        self._lock = threading.Lock()
        self.cancelled = 0
        self.fail_next: List[Exception] = []

    def respond(self, needle: str, response: Response) -> None:
        self.responses[needle] = response

    def _lookup(self, text: str) -> Response:
        for needle, resp in self.responses.items():
            if needle in text:
                return resp
        return self.default

    def execute(self, text: str, cmd: "FakeCommand"):
        with self._lock:
            self.executions.append(text)
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.delay_s:
                deadline = time.monotonic() + self.delay_s
                while time.monotonic() < deadline:
                    if cmd.cancel_requested:
                        with self._lock:
                            self.cancelled += 1
                        raise RuntimeError("The operation was cancelled by the user.")
                    time.sleep(0.005)
            if self.fail_next:
                raise self.fail_next.pop(0)
            resp = self._lookup(text)
            if callable(resp):
                resp = resp(text)
            if isinstance(resp, Exception):
                raise resp
            return resp
        finally:
            with self._lock:
                self.in_flight -= 1


class FakeReader:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows
        self._cols = list(rows[0].keys()) if rows else []
        self._i = -1
        self.closed = 0

    @property
    def FieldCount(self) -> int:
        return len(self._cols)

    def GetName(self, i: int) -> str:
        return self._cols[i]

    def Read(self) -> bool:
        self._i += 1
        return self._i < len(self._rows)

    def GetValue(self, i: int):
        return self._rows[self._i][self._cols[i]]

    def Close(self) -> None:
        self.closed += 1


class FakeState:
    def __init__(self, text: str):
        self._text = text

    def ToString(self) -> str:
        return self._text


class FakeConnection:
    def __init__(self, conn_str: str, server: FakeServer, database: str = "DB"):
        self.ConnectionString = conn_str
        self.server = server
        self.Database = database
        self._state = "Closed"
        self.closed = 0

    def Open(self) -> None:
        self._state = "Open"

    def Close(self) -> None:
        self._state = "Closed"
        self.closed += 1

    def break_(self) -> None:
        self._state = "Broken"

    @property
    def State(self) -> FakeState:
        return FakeState(self._state)


class FakeCommand:
    def __init__(self, text: str, conn: FakeConnection):
        self.text = text
        self.conn = conn
        self.CommandTimeout = 0
        self.cancel_requested = False

    def ExecuteReader(self) -> FakeReader:
        return FakeReader(self.conn.server.execute(self.text, self))

    def ExecuteNonQuery(self) -> int:
        self.conn.server.execute(self.text, self)
        return 1

    def Cancel(self) -> None:
        self.cancel_requested = True


def install(monkeypatch, servers: Dict[str, FakeServer]):
    """Patch AdomdConnection/AdomdCommand in the executor + connection manager.

    ``servers`` maps connection string -> FakeServer (a new connection to the
    same string talks to the same server, like a reconnect to one port).
    """
    from core.infrastructure import connection_manager as cm_mod
    from core.infrastructure import query_executor as qx

    opened: List[FakeConnection] = []

    def _conn_factory(conn_str: str) -> FakeConnection:
        conn = FakeConnection(conn_str, servers[conn_str])
        opened.append(conn)
        return conn

    monkeypatch.setattr(cm_mod, "AdomdConnection", _conn_factory)
    monkeypatch.setattr(cm_mod, "ADOMD_AVAILABLE", True)
    monkeypatch.setattr(qx, "AdomdCommand", FakeCommand)
    monkeypatch.setattr(qx, "ADOMD_AVAILABLE", True)
    return opened


def make_state(monkeypatch, servers: Dict[str, FakeServer], connect_to: Optional[str] = None):
    """A fresh, fully wired ConnectionState + ConnectionManager over fakes."""
    from core.infrastructure.connection_manager import ConnectionManager
    from core.infrastructure.connection_state import ConnectionState

    opened = install(monkeypatch, servers)
    cm = ConnectionManager()
    state = ConnectionState()
    state.set_connection_manager(cm)
    target = connect_to or next(iter(servers))
    port = int(target.rsplit(":", 1)[-1])
    instance = {
        "port": port,
        "connection_string": target,
        "database": f"Model{port}.pbix",
        "file_full_path": f"C:\\\\fake\\\\Model{port}.pbip",
        "pbip_folder_path": f"C:\\\\fake\\\\Model{port}.Report",
        "file_type": "pbip",
    }
    res = cm.connect_instance(instance)
    assert res["success"], res
    state.set_connection_manager(cm)
    state.initialize_managers()
    assert state.query_executor is not None
    return state, cm, opened


def instance_for(conn_str: str) -> Dict[str, Any]:
    port = int(conn_str.rsplit(":", 1)[-1])
    return {
        "port": port,
        "connection_string": conn_str,
        "database": f"Model{port}.pbix",
        "file_full_path": f"C:\\\\fake\\\\Model{port}.pbip",
        "pbip_folder_path": f"C:\\\\fake\\\\Model{port}.Report",
        "file_type": "pbip",
    }
