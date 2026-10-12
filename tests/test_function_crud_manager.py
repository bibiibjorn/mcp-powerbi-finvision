"""FunctionCrudManager — DAX UDF CRUD (review C-18 / FG-11).

Reads come from the DMV (``query_executor.list_udfs``) because AMO's
``Model.Functions`` is empty on Desktop; writes are TMSL commands executed
server-side. The fakes below stand in for exactly those two seams — a fake
"server" holding UDF rows that the TMSL executor mutates — so the tests assert
the command shapes, the read-back verification and the collision checks.
"""

from __future__ import annotations

import json

import pytest

from core.operations import function_crud_manager as fcm
from core.operations.function_crud_manager import FunctionCrudManager


class _FakeServer:
    """UDF rows + a TMSL interpreter for createOrReplace / delete / sequence."""

    def __init__(self, rows=None):
        self.rows = [dict(r) for r in (rows or [])]
        self.commands = []

    def udf_rows(self):
        return [dict(r) for r in self.rows], None

    def execute(self, command):
        self.commands.append(json.loads(json.dumps(command)))
        self._apply(command)
        return {"success": True}

    def _apply(self, command):
        if "sequence" in command:
            for op in command["sequence"]["operations"]:
                self._apply(op)
            return
        if "createOrReplace" in command:
            body = command["createOrReplace"]["function"]
            self.rows = [r for r in self.rows if r["Name"] != body["name"]]
            self.rows.append(
                {
                    "Name": body["name"],
                    "Expression": body["expression"],
                    "Description": body.get("description", ""),
                    "LineageTag": body.get("lineageTag"),
                    "IsHidden": body.get("isHidden", False),
                }
            )
        elif "delete" in command:
            name = command["delete"]["object"]["function"]
            self.rows = [r for r in self.rows if r["Name"] != name]


@pytest.fixture
def server(monkeypatch):
    srv = _FakeServer([{"Name": "Existing", "Expression": "(x) => x", "LineageTag": "lt-1"}])
    mgr = FunctionCrudManager()
    monkeypatch.setattr(mgr, "_udf_rows", srv.udf_rows)
    monkeypatch.setattr(mgr, "_execute_tmsl", srv.execute)
    return srv, mgr


# ---- reads ----------------------------------------------------------------


def test_list_functions_reads_the_dmv(server):
    _srv, mgr = server
    res = mgr.list_functions(include=["expression"])
    assert res["success"] is True
    assert res["count"] == 1
    assert res["functions"][0]["name"] == "Existing"
    assert res["functions"][0]["expression"] == "(x) => x"


def test_list_functions_propagates_read_error(monkeypatch):
    mgr = FunctionCrudManager()
    monkeypatch.setattr(
        mgr, "_udf_rows", lambda: (None, {"success": False, "error_type": "not_connected"})
    )
    assert mgr.list_functions()["error_type"] == "not_connected"


def test_list_never_touches_amo_model_functions(monkeypatch, server):
    """C-18: the AMO collection is empty on Desktop — list must not depend on it."""
    _srv, mgr = server
    monkeypatch.setattr(fcm, "get_server_db_model", lambda cs: pytest.fail("AMO opened"))
    assert mgr.list_functions()["count"] == 1


def test_get_function_not_found(server):
    _srv, mgr = server
    res = mgr.get_function("Nope")
    assert res["success"] is False
    assert res["error_type"] == "not_found"


def test_get_function_invalid_name():
    res = FunctionCrudManager().get_function("")
    assert res["success"] is False
    assert res["error_type"] == "invalid_parameters"


# ---- create ---------------------------------------------------------------


def test_create_function_validates_dax(server, monkeypatch):
    srv, mgr = server
    monkeypatch.setattr(
        FunctionCrudManager, "_validate_dax_expression", staticmethod(lambda e: "Bad expression")
    )
    res = mgr.create_function("F", expression="garbage")
    assert res["error_type"] == "invalid_dax"
    assert srv.commands == []


def test_create_function_refuses_collision_case_insensitively(server):
    srv, mgr = server
    res = mgr.create_function("existing", expression="(x) => 2")
    assert res["error_type"] == "already_exists"
    assert srv.commands == []


def test_create_function_emits_create_or_replace_and_verifies(server):
    srv, mgr = server
    res = mgr.create_function("Fmt.Pct", expression='(v: DOUBLE) => FORMAT(v, "0%")')
    assert res["success"] is True
    assert res["verified"] is True
    cmd = srv.commands[-1]["createOrReplace"]
    assert cmd["object"] == {"database": "<database>", "function": "Fmt.Pct"}
    assert cmd["function"]["expression"] == '(v: DOUBLE) => FORMAT(v, "0%")'
    assert cmd["function"]["lineageTag"]


def test_create_builds_lambda_header_from_parameters(server):
    srv, mgr = server
    res = mgr.create_function(
        "Scale", expression="v * f", parameters=[{"name": "v", "type": "DOUBLE"}, {"name": "f"}]
    )
    assert res["success"] is True
    assert (
        srv.commands[-1]["createOrReplace"]["function"]["expression"] == "(v: DOUBLE, f) =>\nv * f"
    )


def test_create_reports_write_not_visible(server, monkeypatch):
    srv, mgr = server
    monkeypatch.setattr(mgr, "_execute_tmsl", lambda cmd: {"success": True})  # no-op server
    res = mgr.create_function("Ghost", expression="() => 1")
    assert res["success"] is False
    assert res["error_type"] == "write_not_visible"


# ---- update / delete ------------------------------------------------------


def test_update_function_not_found(server):
    _srv, mgr = server
    res = mgr.update_function("Missing", expression="(x) => 1")
    assert res["error_type"] == "not_found"


def test_update_keeps_lineage_tag(server):
    srv, mgr = server
    res = mgr.update_function("Existing", expression="(x) => x * 2")
    assert res["success"] is True
    body = srv.commands[-1]["createOrReplace"]["function"]
    assert body["lineageTag"] == "lt-1"
    assert body["expression"] == "(x) => x * 2"


def test_delete_function_emits_tmsl_delete(server):
    srv, mgr = server
    res = mgr.delete_function("Existing")
    assert res["success"] is True
    assert srv.commands[-1] == {
        "delete": {"object": {"database": "<database>", "function": "Existing"}}
    }
    assert srv.rows == []


# ---- TMSL executor --------------------------------------------------------


def test_execute_tmsl_substitutes_database_and_always_disconnects(monkeypatch):
    class _Db:
        Name = "Model'DB"

    class _Srv:
        disconnected = 0
        text = None

        def Execute(self, text):
            _Srv.text = text
            return []

        def Disconnect(self):
            _Srv.disconnected += 1

    monkeypatch.setattr(fcm, "AMO_AVAILABLE", True)
    monkeypatch.setattr(fcm, "get_server_db_model", lambda cs: (_Srv(), _Db(), None, None))
    mgr = FunctionCrudManager()
    res = mgr._execute_tmsl({"delete": {"object": {"database": "<database>", "function": "F"}}})
    assert res["success"] is True
    assert json.loads(_Srv.text)["delete"]["object"]["database"] == "Model'DB"
    assert _Srv.disconnected == 1


def test_execute_tmsl_reports_xmla_errors(monkeypatch):
    class XmlaError:
        Description = "Function 'F' has a syntax error"

    class _Result:
        Messages = [XmlaError()]

    class _Srv:
        def Execute(self, text):
            return [_Result()]

        def Disconnect(self):
            pass

    class _Db:
        Name = "db"

    monkeypatch.setattr(fcm, "AMO_AVAILABLE", True)
    monkeypatch.setattr(fcm, "get_server_db_model", lambda cs: (_Srv(), _Db(), None, None))
    res = FunctionCrudManager()._execute_tmsl({"delete": {}})
    assert res["success"] is False
    assert "syntax error" in res["error"]


# ---- daxlib_install -------------------------------------------------------


def _package(monkeypatch, functions):
    from core.operations import daxlib_client

    monkeypatch.setattr(
        daxlib_client,
        "fetch_package",
        lambda pkg_id: {"success": True, "package_id": pkg_id, "functions": functions},
    )


def test_daxlib_install_collision_without_force(server, monkeypatch):
    srv, mgr = server
    _package(monkeypatch, [{"name": "Existing", "expression": "() => 1"}])
    res = mgr.daxlib_install(package_id="some_pkg")
    assert res["error_type"] == "name_collision"
    assert "Existing" in res["collisions"]
    assert srv.commands == []


def test_daxlib_install_with_prefix_is_one_sequence(server, monkeypatch):
    srv, mgr = server
    _package(
        monkeypatch,
        [
            {"name": "Existing", "expression": "() => 1"},
            {"name": "Other", "expression": "v", "parameters": [{"name": "v", "type": "INT64"}]},
        ],
    )
    res = mgr.daxlib_install(package_id="some_pkg", prefix="Money")
    assert res["success"] is True
    assert res["installed"] == ["Money.Existing", "Money.Other"]
    assert len(srv.commands) == 1 and "sequence" in srv.commands[0]
    ops = srv.commands[0]["sequence"]["operations"]
    assert ops[1]["createOrReplace"]["function"]["expression"] == "(v: INT64) =>\nv"


def test_daxlib_install_validates_before_writing(server, monkeypatch):
    """C-38: a network-fetched body is validated; nothing is written on failure."""
    srv, mgr = server
    _package(
        monkeypatch,
        [
            {"name": "Good", "expression": "() => 1"},
            {"name": "Bad", "expression": "(x) => SUM(T[a]"},
        ],
    )
    res = mgr.daxlib_install(package_id="pkg")
    assert res["success"] is False
    assert res["error_type"] == "invalid_package"
    assert srv.commands == []


def test_daxlib_install_empty_package(monkeypatch):
    _package(monkeypatch, [])
    res = FunctionCrudManager().daxlib_install(package_id="empty")
    assert res["error_type"] == "empty_package"


def test_list_functions_omits_bodies_by_default():
    """Live 2026-09-23: 36 UDF bodies made `function list` 77 KB; bodies are opt-in."""
    from unittest.mock import patch

    from core.operations.function_crud_manager import FunctionCrudManager

    mgr = FunctionCrudManager.__new__(FunctionCrudManager)
    row = {"Name": "F", "Expression": "() => 1", "Description": "", "IsHidden": False, "State": 1}
    with patch.object(FunctionCrudManager, "_udf_rows", return_value=([row], None)):
        res = mgr.list_functions()
        full = mgr.list_functions(include="all")
    assert "expression" not in res["functions"][0]
    assert res["functions"][0]["expression_chars"] == len("() => 1")
    assert full["functions"][0]["expression"] == "() => 1"
