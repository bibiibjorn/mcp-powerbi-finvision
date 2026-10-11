"""XM-01 xmla_target, OFF-01 offline_model, CAL-01 validation, PROF-01 tool_profiles."""

import os
import shutil
from pathlib import Path

import pytest

from core.infrastructure import offline_model, xmla_target
from core.operations.calendar_crud_manager import TIME_UNITS, validate_groups
from server import tool_profiles

# ----------------------------------------------------------------- XMLA target


def test_connection_string_and_injection_guard():
    cs = xmla_target.build_connection_string("FTX ACC", "S02 Model")
    assert (
        cs
        == 'Data Source=powerbi://api.powerbi.com/v1.0/myorg/FTX ACC;Initial Catalog="S02 Model";'
    )
    for bad in ("a;b", 'x"y', "n\nl"):
        with pytest.raises(ValueError):
            xmla_target.build_connection_string(bad, "m")
    with pytest.raises(ValueError):
        xmla_target.build_connection_string("", "m")


def test_env_token_wins(monkeypatch):
    monkeypatch.setenv("MCP_PBI_XMLA_TOKEN", " tok ")
    token, exp = xmla_target.get_token()
    assert token == "tok" and exp > 0


def test_missing_cache_is_actionable(monkeypatch, tmp_path):
    monkeypatch.delenv("MCP_PBI_XMLA_TOKEN", raising=False)
    monkeypatch.setenv("FABRIC_XMLA_TOKEN_CACHE", str(tmp_path / "none.json"))
    monkeypatch.setattr(xmla_target, "_token", None)
    with pytest.raises(xmla_target.XmlaAuthError) as ei:
        xmla_target.get_token(force_refresh=True)
    msg = str(ei.value)
    assert "MCP_PBI_XMLA_TOKEN" in msg or "msal" in msg


def test_read_only_gate_and_swap_listener():
    xmla_target.deactivate()
    assert xmla_target.refuse_write("02_Model_Operations", "create", {}) is None
    xmla_target.activate("WS", "M", "Data Source=powerbi://api.powerbi.com/v1.0/myorg/WS;")
    try:
        r = xmla_target.refuse_write("02_Model_Operations", "create", {})
        assert r["error_type"] == "service_read_only" and "M" in r["error"]
        # no override: not even the break-glass pair opens a Service write
        assert xmla_target.refuse_write("x", "y", {"allow_service_writes": True}) is not None
        assert (
            xmla_target.refuse_write(
                "x", "y", {"allow_service_writes": True, "confirm_destructive": True}
            )
            is not None
        )
        assert xmla_target.trace_options.__name__  # callable exists
        xmla_target.on_connection_swap("old", "Data Source=localhost:1234", "connect")
        assert xmla_target.is_active() is False
    finally:
        xmla_target.deactivate()


def test_prepare_amo_noop_when_inactive():
    xmla_target.deactivate()
    sentinel = object()
    assert xmla_target.prepare_amo_server(sentinel) is sentinel


# ----------------------------------------------------------------- offline lane


def test_resolve_definition_folder(tmp_path):
    sm = tmp_path / "X.SemanticModel" / "definition"
    sm.mkdir(parents=True)
    (sm / "model.tmdl").write_text("model Model\n", encoding="utf-8")
    assert offline_model.resolve_definition_folder(str(sm)) == sm
    assert offline_model.resolve_definition_folder(str(sm.parent)) == sm
    with pytest.raises(offline_model.OfflineModelError):
        offline_model.resolve_definition_folder(str(tmp_path))


def test_sync_tree_reports_changes(tmp_path):
    src, dst = tmp_path / "s", tmp_path / "d"
    (src / "tables").mkdir(parents=True)
    (dst / "tables").mkdir(parents=True)
    (src / "model.tmdl").write_text("a", encoding="utf-8")
    (dst / "model.tmdl").write_text("b", encoding="utf-8")
    (src / "tables" / "New.tmdl").write_text("n", encoding="utf-8")
    (dst / "tables" / "Old.tmdl").write_text("o", encoding="utf-8")
    (dst / "notes.txt").write_text("keep", encoding="utf-8")
    diff = offline_model._sync_tree(src, dst)
    assert diff["changed"] == ["model.tmdl"]
    assert diff["added"] == [str(Path("tables") / "New.tmdl")]
    assert diff["removed"] == [str(Path("tables") / "Old.tmdl")]
    assert (dst / "notes.txt").exists()


_SPIKE = Path(os.environ.get("MCP_TEST_TMDL_FOLDER", ""))


@pytest.mark.skipif(
    not (_SPIKE / "model.tmdl").exists(),
    reason="set MCP_TEST_TMDL_FOLDER to a TMDL definition folder",
)
def test_offline_roundtrip_real_tom(tmp_path, monkeypatch):
    work = tmp_path / "M.SemanticModel" / "definition"
    shutil.copytree(_SPIKE, work)
    monkeypatch.setattr(offline_model, "_desktop_has_open", lambda d: None)
    r = offline_model.connect_folder(str(work))
    assert r["success"], r
    db = offline_model.database()
    table = next(iter(db.Model.Tables))
    import Microsoft.AnalysisServices.Tabular as TOM

    m = TOM.Measure()
    m.Name = "Offline Added"
    m.Expression = "1"
    table.Measures.Add(m)
    out = offline_model.persist("test")
    assert out["offline_write"] and out["files"]["changed"]
    assert "Offline Added" in (work / "tables" / f"{table.Name}.tmdl").read_text(encoding="utf-8")
    offline_model.disconnect()


def test_refuses_when_desktop_has_project_open(tmp_path, monkeypatch):
    sm = tmp_path / "X.SemanticModel" / "definition"
    sm.mkdir(parents=True)
    (sm / "model.tmdl").write_text("model Model\n", encoding="utf-8")
    monkeypatch.setattr(offline_model, "_desktop_has_open", lambda d: "X.pbip")
    r = offline_model.connect_folder(str(sm))
    assert r["error_type"] == "project_open_in_desktop"


# ----------------------------------------------------------------- calendars


COLS = ["Date", "Year", "Year Month", "Month Name", "Quarter", "IsWeekend", "RelMonth"]


def test_calendar_valid_definition():
    groups = [
        {"time_unit": "Year", "primary_column": "Year"},
        {
            "time_unit": "Month",
            "primary_column": "Year Month",
            "associated_columns": ["Month Name"],
        },
        {"time_unit": "Date", "primary_column": "Date"},
    ]
    assert validate_groups(groups, ["IsWeekend", "RelMonth"], COLS) == []


@pytest.mark.parametrize(
    "groups,related,fragment",
    [
        ([{"time_unit": "Years", "primary_column": "Year"}], None, "did you mean 'Year'"),
        ([{"time_unit": "Day", "primary_column": "Date"}], None, "did you mean 'Date'"),
        (
            [
                {"time_unit": "Year", "primary_column": "Year"},
                {"time_unit": "Year", "primary_column": "Quarter"},
            ],
            None,
            "more than once",
        ),
        ([{"time_unit": "Year", "primary_column": "Nope"}], None, "not in the host table"),
        ([{"time_unit": "Year", "primary_column": "Year"}], ["Year"], "used more than once"),
        ([{"time_unit": "Month"}], None, "needs primary_column"),
    ],
)
def test_calendar_invalid(groups, related, fragment):
    errors = validate_groups(groups, related, COLS)
    assert any(fragment in e for e in errors), errors


def test_calendar_cross_calendar_unit_consistency():
    errors = validate_groups(
        [{"time_unit": "Quarter", "primary_column": "Year Month"}],
        None,
        COLS,
        {"Year Month": "Month"},
    )
    assert any("another calendar" in e for e in errors)


def test_time_units_match_tom_enum_case():
    assert "MonthOfYear" in TIME_UNITS and "Unknown" not in TIME_UNITS and "Day" not in TIME_UNITS


# ----------------------------------------------------------------- profiles


def test_profiles(monkeypatch):
    names = [
        "01_Connection",
        "07_Visual_Operations",
        "13_Query_Plan",
        "04_Run_DAX",
        "10_Show_User_Guide",
    ]
    monkeypatch.delenv("MCP_PBI_TOOLS_EXCLUDE", raising=False)
    monkeypatch.setenv("MCP_PBI_PROFILE", "all")
    assert tool_profiles.filter_tool_names(names) == names
    monkeypatch.setenv("MCP_PBI_PROFILE", "model")
    assert "07_Visual_Operations" not in tool_profiles.filter_tool_names(names)
    monkeypatch.setenv("MCP_PBI_PROFILE", "report")
    assert "13_Query_Plan" not in tool_profiles.filter_tool_names(names)
    monkeypatch.setenv("MCP_PBI_TOOLS_EXCLUDE", "04_Run_DAX, 01_Connection")
    out = tool_profiles.filter_tool_names(names)
    assert "04_Run_DAX" not in out and "01_Connection" in out  # always kept
    monkeypatch.setenv("MCP_PBI_PROFILE", "bogus")
    assert tool_profiles.active_profile() == "all"


@pytest.mark.skipif(
    not (_SPIKE / "model.tmdl").exists(),
    reason="set MCP_TEST_TMDL_FOLDER to a TMDL definition folder",
)
def test_calendar_build_on_real_tom():
    """TimeUnitColumnAssociation has no parameterless ctor: _build must pass the TimeUnit."""
    from core.tmdl.tom_check import load_database_from_folder
    from core.operations.calendar_crud_manager import CalendarCrudManager
    import Microsoft.AnalysisServices.Tabular as TOM

    db = load_database_from_folder(str(_SPIKE))["database"]
    table = next(t for t in db.Model.Tables if any(c.Name for c in t.Columns))
    col = next(c.Name for c in table.Columns if c.Type.ToString() != "RowNumber")
    cal = TOM.Calendar()
    cal.Name = "t"
    CalendarCrudManager._build(
        TOM, cal, table, [{"time_unit": "Year", "primary_column": col}], None
    )
    assert cal.CalendarColumnGroups.Count == 1
