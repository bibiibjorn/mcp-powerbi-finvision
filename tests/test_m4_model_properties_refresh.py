"""MP-01 ``model set_properties`` and RF-01 ``refresh_status`` / ``refresh_history`` /
``refresh_type=indexes`` - manager level, TOM faked (no CLR)."""

from __future__ import annotations

import datetime as dt
import types

import pytest

from core.operations import model_refresh_manager as mrm
from core.operations import write_journal
from core.operations.model_properties_manager import ModelPropertiesManager
from core.operations.model_refresh_manager import ModelRefreshManager
from tests._c_fake_tom import Coll, FakeServer, Obj


class Model:
    def __init__(self, tables=(), level=1600):
        self.Description = "old"
        self.Culture = "en-US"
        self.DiscourageImplicitMeasures = False
        self.Tables = Coll(tables)
        self.saves = 0
        self.undos = 0
        self.save_error = None
        self.Database = Obj(CompatibilityLevel=level)

    def SaveChanges(self):
        if self.save_error:
            raise self.save_error
        self.saves += 1

    def UndoLocalChanges(self):
        self.undos += 1


def props(model, level=1600):
    mgr = ModelPropertiesManager()
    srv = FakeServer()
    db = model.Database
    db.CompatibilityLevel = level
    mgr._get_server_db_model = lambda: (srv, db, model, None)
    return mgr, srv


# ---------------------------------------------------------------------------
# set_properties
# ---------------------------------------------------------------------------


def test_set_properties_writes_all_requested_in_one_save():
    m = Model()
    mgr, srv = props(m)
    out = mgr.set_properties(
        description="Group model", culture="fr-BE", discourage_implicit_measures="true"
    )
    assert out["success"] is True and m.saves == 1 and srv.disconnected == 1
    assert (m.Description, m.Culture, m.DiscourageImplicitMeasures) == (
        "Group model",
        "fr-BE",
        True,
    )
    assert out["updated"] == ["description", "culture", "discourage_implicit_measures"]
    assert out["previous"]["description"] == "old"
    assert "warning" not in out


def test_set_properties_with_nothing_to_set_is_refused():
    m = Model()
    mgr, _ = props(m)
    out = mgr.set_properties()
    assert out["success"] is False and out["error_type"] == "invalid_parameters"
    assert m.saves == 0


@pytest.mark.parametrize("bad", ["not a culture", "e", "en_US", "12-34"])
def test_set_properties_rejects_a_malformed_culture_before_touching_the_model(bad):
    m = Model()
    mgr, _ = props(m)
    out = mgr.set_properties(culture=bad)
    assert out["success"] is False and out["error_type"] == "invalid_parameters"
    assert m.Culture == "en-US" and m.saves == 0


def test_compatibility_upgrade_needs_the_explicit_opt_in():
    m = Model()
    mgr, _ = props(m, level=1600)
    out = mgr.set_properties(compatibility_level=1707)
    assert out["success"] is False and out["error_type"] == "compatibility_upgrade_not_allowed"
    assert m.Database.CompatibilityLevel == 1600 and m.saves == 0


def test_compatibility_upgrade_with_opt_in_saves_once_and_warns_irreversible():
    m = Model()
    mgr, _ = props(m, level=1600)
    out = mgr.set_properties(compatibility_level="1707", allow_compatibility_upgrade=True)
    assert out["success"] is True and m.saves == 1
    assert m.Database.CompatibilityLevel == 1707
    assert out["compatibility_level"] == 1707 and out["previous"]["compatibility_level"] == 1600
    assert "IRREVERSIBLE" in out["warning"]


def test_compatibility_downgrade_is_always_refused():
    m = Model()
    mgr, _ = props(m, level=1702)
    out = mgr.set_properties(compatibility_level=1600, allow_compatibility_upgrade=True)
    assert out["success"] is False and out["error_type"] == "compatibility_downgrade_refused"
    assert m.Database.CompatibilityLevel == 1702 and m.saves == 0


def test_same_level_is_a_noop_even_without_the_opt_in():
    m = Model()
    mgr, _ = props(m, level=1702)
    out = mgr.set_properties(compatibility_level=1702)
    assert out["success"] is True and out["action"] == "noop" and m.saves == 0


def test_a_failed_save_is_rolled_back():
    m = Model()
    m.save_error = RuntimeError("engine said no")
    mgr, _ = props(m)
    out = mgr.set_properties(description="x")
    assert out["success"] is False and out["error_type"] == "tom_error" and m.undos == 1


def test_missing_discourage_property_is_feature_unsupported():
    m = Model()
    del m.DiscourageImplicitMeasures
    mgr, _ = props(m)
    out = mgr.set_properties(discourage_implicit_measures=True)
    assert out["error_type"] == "feature_unsupported_by_client" and m.saves == 0


def test_bad_boolean_and_bad_level_are_invalid_parameters():
    m = Model()
    mgr, _ = props(m)
    assert (
        mgr.set_properties(discourage_implicit_measures="maybe")["error_type"]
        == "invalid_parameters"
    )
    assert mgr.set_properties(compatibility_level="high")["error_type"] == "invalid_parameters"


def test_connection_error_is_structured():
    mgr = ModelPropertiesManager()
    mgr._get_server_db_model = lambda: (None, None, None, "Not connected")
    out = mgr.set_properties(description="x")
    assert out["success"] is False and out["error_type"] == "connection_error"


# ---------------------------------------------------------------------------
# refresh_status
# ---------------------------------------------------------------------------


class Stamp:
    """Stands in for a CLR DateTime."""

    def __init__(self, year, text=None):
        self.Year = year
        self._text = text

    def ToString(self, fmt):
        return self._text


def partition(name, state="Ready", refreshed=None, modified=None, mode="Import"):
    return Obj(
        Name=name,
        State=f"ObjectState.{state}",
        Mode=mode,
        RefreshedTime=refreshed if refreshed is not None else Stamp(1),
        ModifiedTime=modified if modified is not None else Stamp(1),
    )


def refresh_mgr(model):
    mgr = ModelRefreshManager()
    srv = FakeServer()
    mgr._get_server_db_model = lambda: (srv, model.Database, model, None)
    return mgr, srv


def tbl(name, *parts):
    return Obj(Name=name, Partitions=Coll(parts))


def test_refresh_status_reports_per_partition_state_and_times():
    t1 = tbl(
        "Sales",
        partition("P1", refreshed=Stamp(2026, "2026-09-30T08:00:00")),
        partition("P2", refreshed=Stamp(2026, "2026-09-30T09:30:00")),
    )
    t2 = tbl("Calc", partition("C", state="CalculationNeeded"))
    m = Model([t1, t2])
    mgr, srv = refresh_mgr(m)
    out = mgr.refresh_status()
    assert out["success"] is True and srv.disconnected == 1
    assert out["partition_count"] == 3 and out["table_count"] == 2
    assert out["model_last_refreshed"] == "2026-09-30T09:30:00"
    sales = next(t for t in out["tables"] if t["table"] == "Sales")
    assert sales["last_refreshed"] == "2026-09-30T09:30:00" and not sales["never_refreshed"]
    calc = next(t for t in out["tables"] if t["table"] == "Calc")
    assert calc["never_refreshed"] is True and calc["last_refreshed"] is None
    assert out["not_ready_partitions"] == ["Calc/C"] and out["not_ready_count"] == 1
    assert out["partitions"][0]["state"] == "Ready"
    assert "no refresh log" in out["limits"]


def test_refresh_status_can_be_narrowed_to_one_table():
    m = Model([tbl("A", partition("p")), tbl("B", partition("q"))])
    mgr, _ = refresh_mgr(m)
    out = mgr.refresh_status(table_name="B")
    assert [t["table"] for t in out["tables"]] == ["B"]
    missing = mgr.refresh_status(table_name="Nope")
    assert missing["success"] is False and missing["error_type"] == "table_not_found"


def test_refresh_status_connection_error():
    mgr = ModelRefreshManager()
    mgr._get_server_db_model = lambda: (None, None, None, "Not connected")
    assert mgr.refresh_status()["error_type"] == "connection_error"


# ---------------------------------------------------------------------------
# refresh_history (journal)
# ---------------------------------------------------------------------------


def _entry(i, op, label, args, ts):
    return {
        "id": f"J{i:06d}",
        "type": "write",
        "seq": i,
        "ts": ts,
        "operation": op,
        "label": label,
        "args": args,
        "session": "s1",
    }


@pytest.fixture
def journal(monkeypatch):
    entries = [
        _entry(
            1, "measure/create", "measure", {"measure_name": "M"}, "2026-09-30T08:00:00.000000Z"
        ),
        _entry(
            2,
            "model/refresh",
            "model/refresh:tables",
            {"refresh_type": "full", "tables": ["Sales"]},
            "2026-09-30T09:00:00.000000Z",
        ),
        _entry(
            3,
            "table/set_refresh_policy",
            "table/set_refresh_policy",
            {},
            "2026-09-30T09:30:00.000000Z",
        ),
        _entry(
            4,
            "table/refresh",
            "table/refresh",
            {"table_name": "Dim", "refresh_type": "calculate"},
            "2026-09-30T10:00:00.000000Z",
        ),
        _entry(
            5,
            "partition/refresh",
            "partition",
            {"table_name": "Sales", "partition_name": "P1"},
            "2026-09-30T11:00:00.000000Z",
        ),
    ]
    monkeypatch.setattr(write_journal, "_identity", lambda model=None: ("proj-abc", {}))
    monkeypatch.setattr(
        write_journal, "read_journal", lambda key: {"key": key, "entries": list(entries)}
    )
    return entries


def test_refresh_history_lists_only_refreshes_newest_first(journal):
    mgr = ModelRefreshManager()
    out = mgr.refresh_history()
    assert out["success"] is True and out["journal"] == "proj-abc"
    ids = [r["entry_id"] for r in out["refreshes"]]
    assert ids == ["J000005", "J000004", "J000002"]  # policy edit + measure create excluded
    assert out["refreshes"][1]["refresh_type"] == "calculate"
    assert out["refreshes"][2]["tables"] == ["Sales"]
    assert out["refreshes"][0]["partition"] == "P1"
    assert "no refresh log" in out["limits"]


def test_refresh_history_limit_and_since(journal, monkeypatch):
    mgr = ModelRefreshManager()
    out = mgr.refresh_history(limit=1)
    assert len(out["refreshes"]) == 1 and out["truncated"] is True and out["matching"] == 3
    monkeypatch.setattr(
        write_journal,
        "parse_since",
        lambda v: dt.datetime(2026, 9, 30, 9, 45, tzinfo=dt.timezone.utc).timestamp(),
    )
    out = mgr.refresh_history(since="1h")
    assert [r["entry_id"] for r in out["refreshes"]] == ["J000005", "J000004"]


def test_refresh_history_bad_since_is_invalid_parameters(journal):
    out = ModelRefreshManager().refresh_history(since="yesterday-ish")
    assert out["success"] is False and out["error_type"] == "invalid_parameters"


# ---------------------------------------------------------------------------
# refresh_type=indexes
# ---------------------------------------------------------------------------


def test_indexes_is_a_known_refresh_type():
    assert "indexes" in mrm._REFRESH_TYPES


@pytest.mark.parametrize("level", [1600, 1702, 1706, None])
def test_indexes_refresh_is_refused_below_1707_and_nothing_is_requested(level):
    m = Model([tbl("Sales", partition("P"))], level=level if level else 1600)
    m.Database.CompatibilityLevel = level
    mgr, srv = refresh_mgr(m)
    out = mgr.refresh(refresh_type="indexes", tables=["Sales"])
    assert out["success"] is False and out["error_type"] == "compatibility_level_too_low"
    assert out["required_compatibility_level"] == 1707
    assert (
        "set_properties" in out["suggestion"] and "allow_compatibility_upgrade" in out["suggestion"]
    )
    assert m.saves == 0 and srv.disconnected == 1


def test_indexes_refusal_helper_allows_1707_and_above():
    assert mrm.indexes_refresh_refusal(Obj(CompatibilityLevel=1707)) is None
    assert mrm.indexes_refresh_refusal(Obj(CompatibilityLevel=1708)) is None
    assert mrm.indexes_refresh_refusal(Obj(CompatibilityLevel=1606))["error_type"] == (
        "compatibility_level_too_low"
    )


def test_indexes_refresh_runs_on_1707(monkeypatch):
    requested = []
    sales = tbl("Sales", partition("P"))
    sales.RequestRefresh = lambda rtype: requested.append(rtype)
    m = Model([sales], level=1707)
    mgr, _ = refresh_mgr(m)
    m.Tables.Find = lambda name: sales if name == "Sales" else None
    monkeypatch.setattr(mrm, "_resolve_refresh_type", lambda t: ("INDEXES", None))
    out = mgr.refresh(refresh_type="indexes", tables=["Sales"])
    assert out["success"] is True and requested == ["INDEXES"] and m.saves == 1


def test_resolve_refresh_type_finds_the_real_enum_member():
    rtype, err = mrm._resolve_refresh_type("Indexes")
    if err and "Failed to resolve RefreshType" in err:
        pytest.skip("AMO assemblies not loadable in this environment")
    assert err is None and str(rtype) == "Indexes"


def test_table_refresh_shares_the_guard(monkeypatch):
    from core.operations import table_crud_manager as tcm

    monkeypatch.setattr(tcm, "AMO_AVAILABLE", True)
    sales = tbl("Sales", partition("P"))
    sales.RequestRefresh = lambda rtype: (_ for _ in ()).throw(AssertionError("must not request"))
    m = Model([sales], level=1702)
    mgr = tcm.TableCRUDManager(None)
    mgr._get_server_db_model = lambda: (FakeServer(), m.Database, m)
    monkeypatch.setattr(mrm, "_resolve_refresh_type", lambda t: ("INDEXES", None))
    out = mgr.refresh_table("Sales", refresh_type="indexes")
    assert out["error_type"] == "compatibility_level_too_low" and m.saves == 0


_ = types  # (kept for parity with sibling fakes)
