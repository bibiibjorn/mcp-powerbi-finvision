"""2026-09-22 review, slice B — reconnect / instance selection (B-05, B-12, B-27, B-30, B-34).

* B-05: ``connect`` to another instance kept every manager bound to the OLD
  (closed) connection and kept the table-ID map of the old model.
* B-12: the handler chose an index on detection #1 and ``cm.connect(index)``
  re-detected and indexed detection #2; ordering was by random port.
* B-30: a second ``compare`` reused a MultiInstanceManager connection that the
  first call's ``finally`` then disconnected.
* B-34: netstat parsing was English-locale only; the cmdline file was trusted
  blindly after File->Open.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.infrastructure import connection_manager as cm_mod
from core.infrastructure.connection_manager import (
    ConnectionManager,
    PowerBIDesktopDetector,
    file_path_confidence,
)
from tests.fake_adomd import FakeServer, install, instance_for, make_state

CS_A = "Data Source=localhost:50001"
CS_B = "Data Source=localhost:50002"


@pytest.fixture
def two(monkeypatch):
    a, b = FakeServer("A"), FakeServer("B")
    a.respond("INFO.TABLES", [{"[ID]": 1, "[Name]": "Sales_A"}])
    b.respond("INFO.TABLES", [{"[ID]": 1, "[Name]": "Budget_B"}])
    state, cm, opened = make_state(monkeypatch, {CS_A: a, CS_B: b}, connect_to=CS_A)
    return state, cm, a, b


def test_switching_instance_rebuilds_managers_on_the_new_connection(two):
    state, cm, _a, _b = two
    old_conn = cm.get_connection()
    old_crud = state.measure_crud_manager
    assert old_crud is not None and old_crud.connection is old_conn
    assert cm.connect_instance(instance_for(CS_B))["success"]
    new_conn = cm.get_connection()
    assert new_conn is not old_conn
    assert state.measure_crud_manager is not old_crud
    assert state.measure_crud_manager.connection is new_conn
    assert state.model_exporter.connection is new_conn


def test_switching_instance_clears_table_id_maps(two):
    state, cm, _a, _b = two
    state.query_executor._ensure_table_mappings()
    assert state.query_executor._get_table_name_from_id(1) == "Sales_A"
    cm.connect_instance(instance_for(CS_B))
    assert state.query_executor._get_table_name_from_id(1) == "Budget_B"


def test_switching_instance_clears_pbip_info_and_traces(two):
    state, cm, _a, _b = two
    state.set_pbip_info(
        pbip_folder_path="C:\\A.Report", file_full_path="C:\\A.pbip", file_type="pbip"
    )
    state.store_trace_result({"query": "EVALUATE A"})
    cm.connect_instance(instance_for(CS_B))
    assert state.get_pbip_info()["pbip_folder_path"] is None
    assert state.get_cached_trace_result() is None


def test_same_target_reconnect_keeps_pbip_info(two):
    state, cm, _a, _b = two
    state.set_pbip_info(
        pbip_folder_path="C:\\A.Report", file_full_path="C:\\A.pbip", file_type="pbip"
    )
    assert cm.reconnect(max_retries=1)["success"]
    assert state.get_pbip_info()["pbip_folder_path"] == "C:\\A.Report"


def test_connect_handler_connects_the_instance_it_chose(monkeypatch):
    """B-12: exactly the chosen dict is connected — no second detection."""
    import server.handlers.connection_handler as ch

    fake_state = MagicMock()
    fake_state.get_pbip_info.return_value = {}
    cm = MagicMock()
    inst = [
        {"port": 1, "pid": 10, "file_full_path": "C:\\a.pbip"},
        {"port": 2, "pid": 20, "file_full_path": "C:\\b.pbip"},
    ]
    cm.detect_instances.return_value = inst
    cm.connect_instance.return_value = {"success": True, "connection_string": "x"}
    fake_state.connection_manager = cm
    monkeypatch.setattr(ch, "connection_state", fake_state)
    res = ch.handle_connect_to_powerbi({"model_index": 1})
    assert res["success"] is True
    cm.connect_instance.assert_called_once_with(inst[1])
    cm.connect.assert_not_called()
    assert cm.detect_instances.call_count == 1
    fake_state.set_pbip_info.assert_called_once()
    assert fake_state.set_pbip_info.call_args.kwargs["file_full_path"] == "C:\\b.pbip"


def test_status_op_reports_amo_and_identity(monkeypatch):
    import server.handlers.connection_handler as ch

    fake_state = MagicMock()
    fake_state.is_connected.return_value = True
    fake_state.get_connection_identity.return_value = {"port": 1, "model_generation": 3}
    fake_state.connection_manager.get_instance_info.return_value = {"port": 1}
    fake_state.connection_manager.gate.status.return_value = {"busy": False}
    monkeypatch.setattr(ch, "connection_state", fake_state)
    monkeypatch.setattr(
        ch, "_amo_info", lambda include_candidates=False: {"selected_version": "19.x"}
    )
    res = ch.handle_connection({"operation": "status"})
    assert res["success"] is True
    assert res["amo"]["selected_version"] == "19.x"
    assert res["connection"]["model_generation"] == 3


# --- detection -------------------------------------------------------------


class _Proc:
    def __init__(self, pid, name, create_time, parent=None, cmdline=None):
        self.info = {"pid": pid, "name": name, "create_time": create_time}
        self.pid = pid
        self._name = name
        self._parent = parent
        self._cmdline = cmdline or []

    def name(self):
        return self._name

    def parent(self):
        return self._parent

    def cmdline(self):
        return self._cmdline


def test_detection_uses_psutil_listen_sockets_and_orders_newest_first(monkeypatch):
    import psutil

    desktop_old = _Proc(100, "PBIDesktop.exe", 0, cmdline=["PBIDesktop.exe", "C:\\old.pbix"])
    desktop_new = _Proc(200, "PBIDesktop.exe", 0, cmdline=["PBIDesktop.exe", "C:\\new.pbix"])
    procs = {
        11: _Proc(11, "msmdsrv.exe", 1000.0, parent=desktop_old),
        22: _Proc(22, "msmdsrv.exe", 2000.0, parent=desktop_new),
    }

    class _Conn:
        def __init__(self, port, pid):
            self.status = psutil.CONN_LISTEN
            self.pid = pid
            self.laddr = type("A", (), {"port": port})()

    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: list(procs.values()))
    monkeypatch.setattr(psutil, "Process", lambda pid: procs[pid])
    # The OLDER instance listens on the HIGHER port: port order is not age order.
    monkeypatch.setattr(
        psutil, "net_connections", lambda kind="tcp": [_Conn(60000, 11), _Conn(50000, 22)]
    )

    def _no_netstat():
        raise AssertionError("netstat must not be needed when psutil works")

    monkeypatch.setattr(cm_mod, "_listening_ports_netstat", _no_netstat)
    monkeypatch.setattr(cm_mod, "_window_titles_by_pid", lambda pids: {})
    found = PowerBIDesktopDetector.find_powerbi_instances()
    assert [i["port"] for i in found] == [50000, 60000]
    assert found[0]["file_full_path"] == "C:\\new.pbix"
    assert found[0]["msmdsrv_pid"] == 22
    assert found[0]["file_path_confidence"] == "unverified"


def test_file_path_confidence_flags_stale_cmdline():
    assert file_path_confidence("C:\\x\\Sales.pbip", ["Sales - Power BI Desktop"]) == "high"
    assert file_path_confidence("C:\\x\\Sales.pbip", ["Budget - Power BI Desktop"]) == "low"
    assert file_path_confidence("C:\\x\\Sales.pbix", []) == "unverified"


def test_connect_instance_refuses_an_instance_whose_msmdsrv_exited(monkeypatch):
    import psutil

    server = FakeServer()
    install(monkeypatch, {CS_A: server})
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: False)
    cm = ConnectionManager()
    inst = dict(instance_for(CS_A), msmdsrv_pid=4242)
    res = cm.connect_instance(inst)
    assert res["success"] is False
    assert cm.get_connection() is None


def test_dead_helpers_removed():
    """B-27 / B-31: unused connection helpers are gone."""
    assert not hasattr(ConnectionManager, "test_connection")
    assert not hasattr(ConnectionManager, "connect_by_stable_id")


# --- multi-instance (06 compare) -------------------------------------------


def test_multi_instance_reference_counting(monkeypatch):
    from core.infrastructure.multi_instance_manager import MultiInstanceManager

    class _CM:
        instances = []

        def __init__(self):
            self.disconnected = 0
            self.connection_string = None
            _CM.instances.append(self)

        def connect_to_port(self, port):
            self.connection_string = f"Data Source=localhost:{port}"
            return {"success": True, "database_name": f"db{port}", "instance": {"port": port}}

        def is_connected(self):
            return self.disconnected == 0

        def disconnect(self):
            self.disconnected += 1

        def get_instance_info(self):
            return {"port": 1, "database": "db"}

    import core.infrastructure.multi_instance_manager as mim

    monkeypatch.setattr(mim, "ConnectionManager", _CM)
    mgr = MultiInstanceManager()
    first = mgr.connect_to_instance(1234)
    second = mgr.connect_to_instance(1234)  # concurrent compare reuses it
    assert first["status"] == "connected"
    assert second["status"] == "already_connected"
    assert second["database_name"] == "db1234"
    assert mgr.disconnect_instance(1234)["status"] == "released"
    assert _CM.instances[0].disconnected == 0, "first caller's finally closed a shared connection"
    assert mgr.disconnect_instance(1234)["status"] == "disconnected"
    assert _CM.instances[0].disconnected == 1
    # get_all_instances hands out copies
    mgr.connect_to_instance(1234)
    info = mgr.get_all_instances()[0]
    info["port"] = -1
    assert mgr.get_all_instances()[0]["port"] == 1234
