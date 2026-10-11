"""Deep review 2026-10-03 slice N: XMLA read-only is enforced at the choke points (N-01),
the offline lane replaces the XMLA target (N-09/N-10), token cache robustness (N-32),
edit-ledger strict flush matching (N-15) and offline_no_engine answers (N-31), calendar scoping (N-28).
"""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

import pytest

from core.autonomous import edit_ledger
from core.infrastructure import offline_model, xmla_target
from core.operations import write_lane
from server import dispatch
from server.op_classification import classify, op_label

WS_CONN = "Data Source=powerbi://api.powerbi.com/v1.0/myorg/WS;"


@pytest.fixture
def xmla():
    xmla_target.activate("WS", "Prod Model", WS_CONN)
    yield
    xmla_target.deactivate()


@pytest.fixture
def dispatcher():
    d = dispatch.ToolDispatcher()
    d._read_only = False
    d._confirm_destructive = False
    return d


def gate(d, tool, **args):
    level = classify(tool, args)
    return d._security_gate(tool, args, level, op_label(tool, args))


# One case per tool that can write the connected model (N-01): (tool, args)
WRITERS = [
    (
        "02_Model_Operations",
        {"object_type": "measure", "operation": "create", "table_name": "T", "name": "M"},
    ),
    ("02_Model_Operations", {"object_type": "model", "operation": "undo"}),
    ("03_Batch_Operations", {"object_type": "measure", "batch_operation": "create", "items": []}),
    ("15_DAX_Refactor", {"operation": "apply", "transform": "x"}),
    ("02_TMDL_Operations", {"operation": "format_dax", "measure_refs": ["T[M]"]}),
    ("09_Debug_Operations", {"operation": "debug_loop", "fix_strategy": "live_tom"}),
]


@pytest.mark.parametrize(
    "tool,args",
    WRITERS,
    ids=[f"{t}:{a.get('operation') or a.get('batch_operation')}" for t, a in WRITERS],
)
def test_every_connected_model_writer_is_refused_on_xmla(dispatcher, xmla, tool, args):
    refused = gate(dispatcher, tool, **args)
    assert refused is not None and refused["error_type"] == "service_read_only"
    assert "Prod Model" in refused["error"]


def test_unknown_future_writer_tool_is_refused_by_default(dispatcher, xmla):
    # allow-list: a tool nobody classified (=> write) must not reach the service
    refused = dispatcher._security_gate("99_New_Model_Writer", {}, "write", "99_New_Model_Writer:x")
    assert refused is not None and refused["error_type"] == "service_read_only"


FILE_ONLY = [
    ("07_Page_Operations", {"operation": "update"}),
    ("07_Visual_Operations", {"operation": "update"}),
    ("11_PBIP_Authoring", {"operation": "create_page"}),
    ("17_Desktop_Bridge", {"operation": "reload"}),
    ("02_QA_Linguistic", {"operation": "set"}),
    ("00_Client_Mode", {"operation": "on"}),
    ("02_TMDL_Operations", {"operation": "find_replace", "dry_run": False}),
    ("09_Debug_Operations", {"operation": "debug_loop", "fix_strategy": "tmdl_edit"}),
]


@pytest.mark.parametrize(
    "tool,args", FILE_ONLY, ids=[f"{t}:{a['operation']}" for t, a in FILE_ONLY]
)
def test_local_file_tools_stay_allowed_on_xmla(dispatcher, xmla, tool, args):
    assert dispatcher._security_gate(tool, args, "write", op_label(tool, args)) is None


def test_suite_file_writes_of_09_validate_stay_allowed(dispatcher, xmla):
    assert (
        dispatcher._security_gate(
            "09_Validate", {"operation": "test_put"}, "write", "09_Validate:test_put"
        )
        is None
    )


def test_reads_are_never_gated(dispatcher, xmla):
    assert gate(dispatcher, "04_Run_DAX", query='EVALUATE ROW("a",1)') is None
    assert gate(dispatcher, "02_Model_Operations", object_type="measure", operation="list") is None


def test_dry_run_format_dax_is_a_read(dispatcher, xmla):
    assert gate(dispatcher, "02_TMDL_Operations", operation="format_dax", dry_run=True) is None


def test_gate_is_off_without_a_target_and_on_the_offline_lane(dispatcher, monkeypatch):
    xmla_target.deactivate()
    assert (
        gate(dispatcher, "02_Model_Operations", object_type="measure", operation="create") is None
    )
    xmla_target.activate("WS", "M", WS_CONN)
    try:
        monkeypatch.setattr(offline_model, "_state", {"definition": None})
        assert (
            gate(dispatcher, "02_Model_Operations", object_type="measure", operation="create")
            is None
        )
    finally:
        xmla_target.deactivate()


def test_commit_refuses_on_xmla_and_never_saves(xmla):
    model = MagicMock()
    with pytest.raises(xmla_target.ServiceReadOnly) as ei:
        write_lane.commit(model, label="anything")
    assert ei.value.error_type == "service_read_only" and "Prod Model" in str(ei.value)
    model.SaveChanges.assert_not_called()


def test_commit_is_unaffected_without_a_target():
    xmla_target.deactivate()
    model = MagicMock()
    write_lane.commit(model, label="plain")
    model.SaveChanges.assert_called_once_with()


def test_format_dax_handler_refuses_on_xmla(xmla, monkeypatch):
    from server.handlers import tmdl_handler

    monkeypatch.setattr("core.dax.dax_formatter_client.is_enabled", lambda: True)
    monkeypatch.setattr(tmdl_handler.connection_state, "is_connected", lambda: True)
    out = tmdl_handler._handle_format_dax({"measure_refs": ["T[M]"]})
    assert out["error_type"] == "service_read_only"


def test_debug_loop_live_tom_gate_refuses_on_xmla(xmla):
    from core.autonomous.loops.debug_loop import DebugLoopConfig, run_debug_loop

    cfg = DebugLoopConfig(pbip_path="x.pbip", page="p", visual="v", fix_strategy="live_tom")
    out = run_debug_loop(cfg, connection_state=MagicMock())
    assert out["success"] is False and out["error_type"] == "service_read_only"
    assert out["final_status"] == "gate_blocked"


# --------------------------------------------------------------------- N-09 / N-10


def test_connection_manager_disconnect_leaves_the_xmla_target(xmla):
    from core.infrastructure.connection_manager import ConnectionManager

    cm = ConnectionManager()
    cm.active_connection = MagicMock()
    cm.connection_string = WS_CONN
    cm.disconnect()
    assert xmla_target.is_active() is False


def test_connect_folder_deactivates_the_xmla_target(xmla, monkeypatch):
    from server.handlers import connection_handler as ch

    monkeypatch.setattr(
        offline_model, "connect_folder", lambda path, allow_open_in_desktop=False: {"success": True}
    )
    monkeypatch.setattr(ch.connection_state, "cleanup", lambda: None)
    monkeypatch.setattr(ch.connection_state, "_build_managers", lambda *a, **k: None)
    out = ch.handle_connect_folder({"pbip_path": "x"})
    assert out["success"] is True and xmla_target.is_active() is False


@pytest.fixture
def clean_ledger():
    edit_ledger.reset()
    yield
    edit_ledger.reset()


def test_offline_model_write_is_not_recorded_as_a_live_write(clean_ledger, monkeypatch):
    monkeypatch.setattr(offline_model, "_state", {"definition": None})
    edit_ledger.note_dispatch_write("02_Model_Operations:measure/create", {})
    assert edit_ledger.snapshot()["tom_write_count"] == 0


def test_live_write_is_still_recorded(clean_ledger, monkeypatch):
    monkeypatch.setattr(offline_model, "_state", None)
    edit_ledger.note_dispatch_write("02_Model_Operations:measure/create", {})
    assert edit_ledger.snapshot()["tom_write_count"] == 1


def test_offline_pbir_disk_write_is_still_a_disk_edit(clean_ledger, monkeypatch, tmp_path):
    monkeypatch.setattr(offline_model, "_state", {"definition": None})
    edit_ledger.note_dispatch_write(
        "07_Page_Operations:create", {"pbip_path": str(tmp_path / "R.Report")}
    )
    assert edit_ledger.snapshot()["disk_edits"]


# --------------------------------------------------------------------- N-15 strict matching


def test_strict_match_never_matches_unknown_or_unresolvable_projects(tmp_path):
    proj = tmp_path / "P"
    (proj / "M.SemanticModel" / "definition").mkdir(parents=True)
    (proj / "M.SemanticModel" / "definition" / "model.tmdl").write_text(
        "model Model\n", encoding="utf-8"
    )
    model_dir = str(proj / "M.SemanticModel")
    # default (fail-closed, for "is anything unsaved?"): unknown matches everything
    assert edit_ledger.matches_project(None, [], [model_dir]) is True
    assert edit_ledger.matches_project(str(tmp_path / "gone" / "x.pbip"), [], [model_dir]) is True
    # strict (for "mark as flushed"): only a project that resolves to the saved artifacts
    assert edit_ledger.matches_project(None, [], [model_dir], strict=True) is False
    assert (
        edit_ledger.matches_project(str(tmp_path / "gone" / "x.pbip"), [], [model_dir], strict=True)
        is False
    )
    assert edit_ledger.matches_project(model_dir, [], [model_dir], strict=True) is True


# --------------------------------------------------------------------- N-32 token handling


def _jwt(exp: float) -> str:
    import base64

    def b64(obj) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{b64({'alg': 'none'})}.{b64({'exp': exp})}.sig"


def test_expired_env_token_falls_back_to_the_cache(monkeypatch):
    monkeypatch.setenv("MCP_PBI_XMLA_TOKEN", _jwt(time.time() - 100))
    monkeypatch.setattr(xmla_target, "_token", None)
    monkeypatch.setattr(xmla_target, "_token_from_cache", lambda: ("cached", time.time() + 3000))
    assert xmla_target.get_token()[0] == "cached"


def test_expired_env_token_without_cache_says_so(monkeypatch):
    monkeypatch.setenv("MCP_PBI_XMLA_TOKEN", _jwt(time.time() - 100))
    monkeypatch.setattr(xmla_target, "_token", None)

    def no_cache():
        raise xmla_target.XmlaAuthError("no cache")

    monkeypatch.setattr(xmla_target, "_token_from_cache", no_cache)
    with pytest.raises(xmla_target.XmlaAuthError, match="expired"):
        xmla_target.get_token()


def test_valid_env_token_wins_and_reports_its_expiry(monkeypatch):
    exp = time.time() + 3600
    tok = _jwt(exp)
    monkeypatch.setenv("MCP_PBI_XMLA_TOKEN", tok)
    token, expires = xmla_target.get_token()
    assert token == tok and abs(expires - exp) < 1


def test_account_is_chosen_by_tenant():
    accounts = [
        {"home_account_id": "oid1.tenantA", "realm": "tenantA"},
        {"home_account_id": "oid1.tenantB", "realm": "tenantB"},
    ]
    assert xmla_target._pick_account(accounts, "TenantB") is accounts[1]
    assert xmla_target._pick_account(accounts, "") is accounts[0]
    assert xmla_target._pick_account(accounts, "other") is accounts[0]


def test_cache_write_is_atomic_and_readable_with_retry(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text("old", encoding="utf-8")
    xmla_target._write_cache_atomic(path, "new content")
    assert path.read_text(encoding="utf-8") == "new content"
    assert not list(tmp_path.glob("*.tmp"))

    attempts = {"n": 0}

    class Cache:
        def deserialize(self, text):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise ValueError("truncated")

    xmla_target._load_cache_text(path, Cache())
    assert attempts["n"] == 3

    class Bad:
        def deserialize(self, text):
            raise ValueError("always")

    with pytest.raises(xmla_target.XmlaAuthError, match="could not be read"):
        xmla_target._load_cache_text(path, Bad())


# --------------------------------------------------------------------- N-31 offline_no_engine


def test_refresh_status_offline_says_no_engine(monkeypatch):
    from core.operations.model_refresh_manager import ModelRefreshManager

    monkeypatch.setattr(offline_model, "_state", {"definition": None})
    out = ModelRefreshManager().refresh_status()
    assert out["success"] is False and out["error_type"] == "offline_no_engine"


def test_mark_as_date_table_check_offline_says_no_engine(monkeypatch):
    from core.infrastructure.connection_state import connection_state
    from core.operations.table_crud_manager import TableCRUDManager

    monkeypatch.setattr(offline_model, "_state", {"definition": None})
    monkeypatch.setattr(connection_state, "query_executor", None)
    out = TableCRUDManager(MagicMock()).check_date_column("Date", "Date")
    assert out["success"] is False and out["error_type"] == "offline_no_engine"


# --------------------------------------------------------------------- N-28 calendars per table


class TimeUnitColumnAssociation:
    def __init__(self, unit, primary, assoc=()):
        self.TimeUnit = unit
        self.PrimaryColumn = type("C", (), {"Name": primary})()
        self.AssociatedColumns = [type("C", (), {"Name": n})() for n in assoc]


def _model_with_two_date_tables():
    def cal(name, unit, col):
        c = type("Cal", (), {})()
        c.Name = name
        c.CalendarColumnGroups = [TimeUnitColumnAssociation(unit, col)]
        return c

    def table(name, *cals):
        t = type("T", (), {})()
        t.Name = name
        t.Calendars = list(cals)
        return t

    m = type("M", (), {})()
    m.Tables = [table("DateA", cal("CalA", "Month", "Period")), table("DateB")]
    return m


def test_units_elsewhere_ignores_calendars_of_other_tables():
    from core.operations.calendar_crud_manager import CalendarCrudManager

    mgr = CalendarCrudManager()
    model = _model_with_two_date_tables()
    assert mgr._units_elsewhere(model, None, "DateB") == {}  # DateB.Period may be a Quarter
    assert mgr._units_elsewhere(model, None, "DateA") == {"Period": "Month"}
    assert mgr._units_elsewhere(model, "CalA", "DateA") == {}
    assert mgr._units_elsewhere(model, None) == {"Period": "Month"}  # legacy call shape
