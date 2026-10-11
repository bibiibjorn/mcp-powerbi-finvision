"""Deep review 2026-10-03 slice N: offline TMDL lane hardening (N-02/03/04/20/21/23/27) on the real TOM.

Fixtures are a tiny synthetic TMDL folder; the bundled AMO/TOM does the (de)serialisation, so the tests
skip when the assemblies cannot be loaded. Desktop detection is always stubbed.
"""

from __future__ import annotations

import stat
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.infrastructure import offline_model, xmla_target
from core.operations import write_lane
from core.tmdl import tom_check

pytestmark = pytest.mark.skipif(
    tom_check._load_serializer() is None, reason="bundled AMO/TOM assemblies not available"
)

TABLE = (
    "table {n}\n\n\tcolumn C1\n\t\tdataType: string\n\t\tsourceColumn: C1\n\n"
    "\tpartition {n} = m\n\t\tmode: import\n\t\tsource =\n\t\t\t\tlet\n"
    '\t\t\t\t    S = #table({{"C1"}}, {{}})\n\t\t\t\tin\n\t\t\t\t    S\n'
)


def _write_project(root: Path) -> Path:
    d = root / "M.SemanticModel" / "definition"
    (d / "tables").mkdir(parents=True)
    (d / "database.tmdl").write_text("database\n\tcompatibilityLevel: 1601\n", encoding="utf-8")
    (d / "model.tmdl").write_text(
        "model Model\n\tculture: en-US\n\tdefaultPowerBIDataSourceVersion: powerBI_V3\n\nref table A\nref table B\n",
        encoding="utf-8",
    )
    for n in "AB":
        (d / "tables" / f"{n}.tmdl").write_text(TABLE.format(n=n), encoding="utf-8")
    return d


@pytest.fixture
def lane(tmp_path, monkeypatch):
    """A connected offline lane whose folder already holds serializer-normalised files."""
    monkeypatch.setattr(offline_model, "_desktop_has_open", lambda d: None)
    xmla_target.deactivate()
    d = _write_project(tmp_path)
    assert offline_model.connect_folder(str(d))["success"]
    offline_model.persist("normalise")  # make the files byte-identical to the serializer's output
    offline_model.disconnect()
    assert offline_model.connect_folder(str(d))["success"]
    yield d
    offline_model.disconnect()


def _col(table: str = "A"):
    return offline_model.database().Model.Tables[table].Columns["C1"]


def _snapshot(d: Path):
    return {p.relative_to(d).as_posix(): p.read_bytes() for p in d.rglob("*") if p.is_file()}


# --------------------------------------------------------------------- N-03 staged persist


def test_persist_failure_midway_restores_every_file(lane):
    before = _snapshot(lane)
    _col("A").Description = "edit A"
    _col("B").Description = "edit B"
    locked = lane / "tables" / "B.tmdl"
    locked.chmod(stat.S_IREAD)  # os.replace over a read-only file fails on Windows
    try:
        with pytest.raises(offline_model.OfflineModelError) as ei:
            offline_model.persist("two tables")
    finally:
        locked.chmod(stat.S_IWRITE | stat.S_IREAD)
    assert "previous files were restored" in str(ei.value)
    assert _snapshot(lane) == before  # nothing half-written, no *.mcp_new leftovers
    assert not list(lane.rglob("*.mcp_new"))


def test_persist_failure_leaves_model_dirty_so_next_write_cannot_leak_it(lane):
    _col("A").Description = "leaky"
    locked = lane / "tables" / "A.tmdl"
    locked.chmod(stat.S_IREAD)
    try:
        with pytest.raises(offline_model.OfflineModelError):
            offline_model.persist("fails")
    finally:
        locked.chmod(stat.S_IWRITE | stat.S_IREAD)
    offline_model.server_db_model()[0].Disconnect()  # what every manager does in `finally`
    assert not _col("A").Description
    assert offline_model.persist("next")["files"] == {"changed": [], "added": [], "removed": []}


def test_persist_verify_failure_restores(lane, monkeypatch):
    before = _snapshot(lane)
    _col("A").Description = "bad"
    monkeypatch.setattr(
        tom_check, "tom_deserialize_check", lambda p: {"ok": False, "error": "boom", "ran": True}
    )
    with pytest.raises(offline_model.OfflineModelError, match="failed to deserialise"):
        offline_model.persist("verify")
    assert _snapshot(lane) == before


def test_apply_plan_staging_failure_touches_no_real_file(tmp_path, monkeypatch):
    src, dst = tmp_path / "s", tmp_path / "d"
    src.mkdir()
    dst.mkdir()
    for n in ("a", "b"):
        (src / f"{n}.tmdl").write_text("new\n", encoding="utf-8", newline="")
        (dst / f"{n}.tmdl").write_text("old\n", encoding="utf-8", newline="")
    plan = offline_model._plan_sync(src, dst)
    real = Path.write_bytes
    calls = {"n": 0}

    def flaky(self, data):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        return real(self, data)

    monkeypatch.setattr(Path, "write_bytes", flaky)
    with pytest.raises(OSError):
        offline_model._apply_plan(plan, dst)
    monkeypatch.undo()
    assert (dst / "a.tmdl").read_bytes() == b"old\n" and (dst / "b.tmdl").read_bytes() == b"old\n"
    assert not list(dst.glob("*.mcp_new"))


# --------------------------------------------------------------------- N-04 external edits


def test_external_edit_is_refused_not_reverted(lane):
    external = lane / "tables" / "A.tmdl"
    external.write_bytes(
        external.read_bytes().replace(b"\tcolumn C1", b"\t/// colleague note\r\n\tcolumn C1", 1)
    )
    edited = external.read_bytes()
    before_b = (lane / "tables" / "B.tmdl").read_bytes()
    _col("B").Description = "my edit"
    with pytest.raises(offline_model.OfflineFolderChanged) as ei:
        offline_model.persist("b")
    assert ei.value.error_type == "offline_folder_changed" and "A.tmdl" in str(ei.value)
    assert external.read_bytes() == edited and (lane / "tables" / "B.tmdl").read_bytes() == before_b
    # the manager's `finally` Disconnect re-reads the folder, so a retry now works and keeps the edit
    offline_model.server_db_model()[0].Disconnect()
    _col("B").Description = "my edit"
    out = offline_model.persist("b again")
    assert out["files"]["changed"] == [str(Path("tables") / "B.tmdl")]
    assert external.read_bytes() == edited


def test_fingerprint_refreshed_after_own_persist(lane):
    _col("A").Description = "one"
    offline_model.persist("one")
    _col("A").Description = "two"
    assert offline_model.persist("two")["offline_write"]  # our own previous write is not "external"


# --------------------------------------------------------------------- N-20 line endings


def test_lf_tree_stays_lf_and_only_the_edited_file_is_rewritten(tmp_path, monkeypatch):
    monkeypatch.setattr(offline_model, "_desktop_has_open", lambda d: None)
    d = _write_project(tmp_path)
    assert offline_model.connect_folder(str(d))["success"]
    offline_model.persist("normalise")
    offline_model.disconnect()
    for p in d.rglob("*.tmdl"):  # a checkout with eol=lf
        p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n"))
    try:
        assert offline_model.connect_folder(str(d))["success"]
        noop = offline_model.persist("noop")
        assert noop["files"] == {"changed": [], "added": [], "removed": []}
        _col("A").Description = "one line"
        out = offline_model.persist("edit")
        assert out["files"]["changed"] == [str(Path("tables") / "A.tmdl")]
        data = (d / "tables" / "A.tmdl").read_bytes()
        assert b"one line" in data and b"\r\n" not in data
        assert b"\r\n" not in (d / "model.tmdl").read_bytes()
    finally:
        offline_model.disconnect()


def test_plan_sync_keeps_crlf_files_crlf_and_new_files_use_tree_majority(tmp_path):
    src, dst = tmp_path / "s", tmp_path / "d"
    src.mkdir()
    dst.mkdir()
    (src / "x.tmdl").write_bytes(b"a\r\nb2\r\n")
    (dst / "x.tmdl").write_bytes(b"a\nb\n")  # LF file with a change
    (src / "y.tmdl").write_bytes(b"same\r\n")
    (dst / "y.tmdl").write_bytes(b"same\n")  # equal modulo EOL: untouched
    (src / "new.tmdl").write_bytes(b"n\r\n")
    plan = offline_model._plan_sync(src, dst)
    assert plan["changed"] == [(Path("x.tmdl"), b"a\nb2\n")]
    assert plan["added"] == [(Path("new.tmdl"), b"n\n")]  # tree majority is LF
    assert plan["removed"] == []


# --------------------------------------------------------------------- N-02 rollback


def test_safe_undo_rereads_the_folder_offline(lane):
    before = _snapshot(lane)
    model = offline_model.database().Model
    _col("A").Description = "never persisted"
    write_lane.safe_undo(model)  # UndoLocalChanges would throw on a disconnected model
    assert not _col("A").Description
    assert _snapshot(lane) == before


def test_safe_undo_calls_undo_on_the_live_lane():
    offline_model.disconnect()
    model = MagicMock()
    write_lane.safe_undo(model)
    model.UndoLocalChanges.assert_called_once_with()


def test_disconnect_discards_unpersisted_edit_of_a_write(lane):
    server, _db, model = offline_model.server_db_model()
    model.Tables["A"].Columns["C1"].Description = "rejected write"
    server.Disconnect()
    assert not _col("A").Description
    assert offline_model.info()["pending_unpersisted_changes"] is False


def test_disconnect_keeps_persisted_edit(lane):
    server, _db, model = offline_model.server_db_model()
    model.Tables["A"].Columns["C1"].Description = "kept"
    offline_model.persist("keep")
    server.Disconnect()
    assert _col("A").Description == "kept"


def test_read_scope_does_not_mark_dirty_nor_reload(lane):
    db_before = offline_model.database()
    with offline_model.op_scope(False):
        server, _db, _model = offline_model.server_db_model()
        server.Disconnect()
    assert offline_model.database() is db_before


# --------------------------------------------------------------------- N-23 scopes


def test_writer_waits_for_readers_and_readers_wait_for_writer(lane):
    order = []
    reader_in, release_reader = threading.Event(), threading.Event()

    def reader():
        with offline_model.op_scope(False):
            order.append("r-in")
            reader_in.set()
            release_reader.wait(5)
            order.append("r-out")

    def writer():
        with offline_model.op_scope(True):
            order.append("w")

    t1 = threading.Thread(target=reader)
    t1.start()
    assert reader_in.wait(5)
    t2 = threading.Thread(target=writer)
    t2.start()
    time.sleep(0.2)
    assert order == ["r-in"]  # writer is blocked while a reader is inside
    release_reader.set()
    t1.join(5)
    t2.join(5)
    assert order == ["r-in", "r-out", "w"]


def test_op_scope_is_a_noop_when_inactive():
    offline_model.disconnect()
    with offline_model.op_scope(True):
        with offline_model.op_scope(False):  # nested + inactive: no deadlock
            pass


# --------------------------------------------------------------------- N-27 desktop detection


def test_is_under_uses_a_separator_boundary(tmp_path):
    root = tmp_path / "R01"
    assert offline_model._is_under(str(root / "R01.pbip"), str(root))
    assert offline_model._is_under(str(root), str(root))
    assert not offline_model._is_under(str(tmp_path / "R01-copy" / "x.pbip"), str(root))


def test_desktop_detection_matches_only_the_real_project(tmp_path, monkeypatch):
    definition = tmp_path / "R01" / "M.SemanticModel" / "definition"
    definition.mkdir(parents=True)

    class FakeDetector:
        def __init__(self, path):
            self.path = path

        def find_powerbi_instances(self):
            return [{"file_full_path": self.path, "display_name": "R01 - Power BI Desktop"}]

    import core.infrastructure.connection_manager as cmod

    monkeypatch.setattr(
        cmod, "PowerBIDesktopDetector", lambda: FakeDetector(str(tmp_path / "R01-copy" / "R.pbip"))
    )
    assert offline_model._desktop_has_open(definition) is None
    monkeypatch.setattr(
        cmod, "PowerBIDesktopDetector", lambda: FakeDetector(str(tmp_path / "R01" / "R.pbip"))
    )
    assert offline_model._desktop_has_open(definition) == "R01 - Power BI Desktop"


def test_desktop_detection_failure_fails_closed(tmp_path, monkeypatch):
    sm = tmp_path / "X.SemanticModel" / "definition"
    sm.mkdir(parents=True)
    (sm / "model.tmdl").write_text("model Model\n", encoding="utf-8")
    import core.infrastructure.connection_manager as cmod

    class Boom:
        def find_powerbi_instances(self):
            raise RuntimeError("wmi down")

    monkeypatch.setattr(cmod, "PowerBIDesktopDetector", Boom)
    owner = offline_model._desktop_has_open(sm)
    assert owner and owner.startswith(offline_model.DETECTION_FAILED_PREFIX)
    r = offline_model.connect_folder(str(sm))
    assert r["success"] is False and r["error_type"] == "project_open_in_desktop"
    assert "Could not check" in r["error"]


def test_allow_open_in_desktop_needs_confirm_destructive(tmp_path):
    from server.handlers.connection_handler import handle_connect_folder

    out = handle_connect_folder({"pbip_path": str(tmp_path), "allow_open_in_desktop": True})
    assert out["success"] is False and out["error_type"] == "confirmation_required"


# --------------------------------------------------------------------- N-21 compatibility level


def test_supported_levels_come_from_the_loaded_tom_build():
    from core.operations.model_properties_manager import supported_compatibility_levels

    levels = supported_compatibility_levels()
    assert levels and 1702 in levels and 99999 not in levels
    assert levels == sorted(levels)


def test_set_properties_refuses_unknown_compatibility_level_offline(lane):
    from core.operations.model_properties_manager import ModelPropertiesManager

    mgr = ModelPropertiesManager()
    out = mgr.set_properties(compatibility_level=99999, allow_compatibility_upgrade=True)
    assert out["success"] is False and out["error_type"] == "compatibility_level_unknown"
    assert 1702 in out["supported_levels"]
    assert "compatibilityLevel: 99999" not in (lane / "database.tmdl").read_text(encoding="utf-8")
    ok = mgr.set_properties(compatibility_level=1702, allow_compatibility_upgrade=True)
    assert ok["success"] is True
    assert "compatibilityLevel: 1702" in (lane / "database.tmdl").read_text(encoding="utf-8")


def test_write_lane_commit_on_the_offline_lane_persists_and_bumps(lane):
    _col("A").Description = "via commit"
    info = write_lane.commit(offline_model.database().Model, label="offline commit")
    assert info["offline_write"] and info["files"]["changed"] == [str(Path("tables") / "A.tmdl")]
    assert "via commit" in (lane / "tables" / "A.tmdl").read_text(encoding="utf-8")


def test_write_scope_that_cannot_take_the_lock_refuses_instead_of_running_unlocked(monkeypatch):
    """A write must never run unlocked next to a reader on the shared offline database."""
    from core.infrastructure import offline_model as om

    monkeypatch.setattr(om, "_state", {"database": object(), "dirty": False})
    monkeypatch.setattr(om._rw, "acquire", lambda write, timeout: False)
    with pytest.raises(om.OfflineBusy) as exc:
        with om.op_scope(True):
            pytest.fail("the write body must not run")
    assert exc.value.error_type == "busy"
    # A read still proceeds (logged), it cannot corrupt the model.
    ran = []
    with om.op_scope(False):
        ran.append(True)
    assert ran == [True]
