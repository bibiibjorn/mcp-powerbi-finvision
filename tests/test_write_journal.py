"""JR-01: the durable write journal - recording at the write_lane choke point, storage, restart
persistence, flush marking, secrets, the dispatcher scope and the bridge integration.

Never touches a live model: the CLR seams (serialise / read-committed) are faked by
``tests/_journal_fakes.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.autonomous import desktop_bridge as db
from core.autonomous import edit_ledger
from core.operations import write_journal
from core.operations.write_lane import ModelWriteLane, commit
from tests import _journal_fakes as jf
from tests.test_review_j_bridge_safety import _fake_cli, _inst, _project


@pytest.fixture
def live(monkeypatch, tmp_path):
    write_journal.configure(enabled=True, directory=tmp_path / "journal")
    lv = jf.LiveModel()
    jf.install(monkeypatch, lv)
    return lv


def _journal_path(tmp_path: Path) -> Path:
    files = list((tmp_path / "journal").glob("*.jsonl"))
    assert len(files) == 1, files
    return files[0]


def _lines(tmp_path: Path):
    return [json.loads(x) for x in _journal_path(tmp_path).read_text(encoding="utf-8").splitlines()]


def _edit_total(live, expr="SUM(Sales[Amount]) * 2", label="measure/update"):
    tom = live.connect()
    tom.measure("Sales", "Total")["expression"] = expr
    with write_journal.call_scope("02_Model_Operations", f"02_Model_Operations:{label}", {"x": 1}):
        info = commit(tom, label=label)
    return tom, info


# ---------------------------------------------------------------------------
# recording
# ---------------------------------------------------------------------------
def test_commit_appends_one_line_with_pre_and_post_image(live, tmp_path):
    _tom, info = _edit_total(live)
    assert info == {}
    (rec,) = _lines(tmp_path)
    assert rec["type"] == "write" and rec["id"] == "J000001" and rec["seq"] == 1
    assert rec["ts"].endswith("Z") and rec["session"] == write_journal.SESSION_ID
    assert rec["tool"] == "02_Model_Operations" and rec["operation"] == "measure/update"
    assert rec["flushed"] is False and rec["result"]["success"] is True
    assert rec["refs"] == ["measure:Sales[Total]"]
    (ch,) = rec["changes"]
    assert ch["change"] == "modified"
    assert ch["pre"]["expression"] == "SUM(Sales[Amount])"
    assert ch["post"]["expression"] == "SUM(Sales[Amount]) * 2"
    # the pre-image is the FULL measure, not just the changed property
    assert ch["pre"]["formatString"] == "0.00" and ch["pre"]["displayFolder"] == "Base"
    assert rec["args"] == {"x": 1}
    assert rec["project"] == jf.PROJECT


def test_added_removed_and_renamed_objects_are_journaled(live, tmp_path):
    tom = live.connect()
    tom.table("Sales")["measures"].append({"name": "New", "expression": "1"})
    tom.table("Sales")["measures"] = [
        m for m in tom.table("Sales")["measures"] if m["name"] != "Count"
    ]
    col = next(c for c in tom.table("Sales")["columns"] if c["name"] == "Amount")
    col["name"] = "Amount2"  # same lineageTag -> a rename
    commit(tom, label="mixed")
    (rec,) = _lines(tmp_path)
    by_ref = {c["ref"]: c for c in rec["changes"]}
    assert by_ref["measure:Sales[New]"]["change"] == "added"
    assert by_ref["measure:Sales[Count]"]["change"] == "removed"
    assert by_ref["measure:Sales[Count]"]["pre"]["expression"] == "COUNTROWS(Sales)"
    renamed = by_ref["column:Sales[Amount2]"]
    assert renamed["change"] == "renamed" and renamed["pre_ref"] == "column:Sales[Amount]"


def test_no_local_changes_writes_nothing(live, tmp_path):
    tom = live.connect()
    tom.HasLocalChanges = False
    commit(tom, label="noop")
    assert not (tmp_path / "journal").exists() or not list((tmp_path / "journal").glob("*.jsonl"))


def test_save_without_metadata_change_is_a_noop_entry(live, tmp_path):
    commit(live.connect(), label="model/refresh:full")
    (rec,) = _lines(tmp_path)
    assert rec["noop"] is True and rec["changes"] == []
    assert write_journal.unflushed_summary(jf.REPORTS, jf.MODELS)["count"] == 0


def test_failed_save_writes_no_entry(live, tmp_path):
    tom = live.connect()
    tom.measure("Sales", "Total")["expression"] = "1"
    tom.fail = RuntimeError("rejected")
    with pytest.raises(RuntimeError):
        commit(tom, label="x")
    assert not (tmp_path / "journal").exists() or not list((tmp_path / "journal").glob("*.jsonl"))


def test_save_error_that_landed_is_still_journaled(live, tmp_path):
    tom = live.connect()
    tom.measure("Sales", "Total")["expression"] = "1"
    tom.fail = RuntimeError("client parse error")
    info = commit(tom, label="x", verify=lambda: True)
    assert info["applied"] is True and "post_write_warning" in info
    (rec,) = _lines(tmp_path)
    assert rec["result"]["applied"] is True and rec["result"]["post_write_warning"]


def test_journal_write_failure_never_fails_the_write(live, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(write_journal, "_append", boom)
    tom = live.connect()
    tom.measure("Sales", "Total")["expression"] = "42"
    info = commit(tom, label="measure/update")
    assert live.saves == 1  # the write landed
    assert "disk full" in info["journal_warning"]


def test_preimage_capture_failure_never_fails_the_write(live, tmp_path, monkeypatch):
    monkeypatch.setattr(
        write_journal, "fetch_committed", lambda m: (_ for _ in ()).throw(RuntimeError("no server"))
    )
    tom = live.connect()
    tom.measure("Sales", "Total")["expression"] = "42"
    info = commit(tom, label="measure/update")
    assert live.saves == 1
    assert "pre-image could not be captured" in info["journal_warning"]
    (rec,) = _lines(tmp_path)
    assert rec["degraded"] and rec["changes"] == []
    assert (
        write_journal.unflushed_summary(jf.REPORTS, jf.MODELS)["count"] == 0
    )  # degraded: nothing to replay


def test_disabled_journal_records_nothing(live, tmp_path):
    write_journal.configure(enabled=False)
    _edit_total(live)
    assert not (tmp_path / "journal").exists() or not list((tmp_path / "journal").glob("*.jsonl"))


def test_previous_commit_image_is_the_next_preimage_without_a_server_read(live, tmp_path):
    _edit_total(live, "A")
    assert live.reads == 1  # first commit: no baseline yet
    # a fresh connection sees the same ModifiedTime the last save left behind
    tom = live.connect()
    tom.ModifiedTime = tom.ModifiedTime  # unchanged since the commit
    tom.measure("Sales", "Total")["expression"] = "B"
    commit(tom, label="measure/update")
    assert live.reads == 1, "cache hit must not read the server again"
    recs = _lines(tmp_path)
    assert recs[1]["changes"][0]["pre"]["expression"] == "A"


def test_desktop_side_edit_invalidates_the_cached_preimage(live, tmp_path):
    _edit_total(live, "A")
    live.tmsl["tables"][0]["measures"][0]["expression"] = "edited in the Desktop UI"
    live.stamp += 1  # Desktop's own edit moves ModifiedTime
    tom = live.connect()
    tom.measure("Sales", "Total")["expression"] = "B"
    commit(tom, label="measure/update")
    assert live.reads == 2
    assert _lines(tmp_path)[1]["changes"][0]["pre"]["expression"] == "edited in the Desktop UI"


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------
def test_journal_survives_a_process_restart(live, tmp_path, monkeypatch):
    _edit_total(live, "A")
    _edit_total(live, "B")
    # a new process: every in-memory cache is gone, only the file remains
    write_journal.reset()
    write_journal.configure(enabled=True, directory=tmp_path / "journal")
    key = write_journal.resolve_key(jf.PROJECT, "db1")
    view = write_journal.read_journal(key)
    assert [e["id"] for e in view["entries"]] == ["J000001", "J000002"]
    assert all(e["flushed"] is False for e in view["entries"])
    # ...and numbering continues where the file left off
    _edit_total(live, "C")
    assert write_journal.read_journal(key)["entries"][-1]["id"] == "J000003"


def test_a_fresh_python_process_reads_the_existing_journal(live, tmp_path):
    """Real restart persistence: a separate interpreter sees the entries and continues numbering."""
    import subprocess
    import sys

    _edit_total(live, "A")
    root = Path(__file__).resolve().parent.parent
    code = "; ".join(
        [
            "import json, sys",
            "from core.operations import write_journal as w",
            "w.configure(enabled=True, directory=sys.argv[1])",
            "key = w.resolve_key(sys.argv[2], 'db1')",
            "ids = [e['id'] for e in w.read_journal(key)['entries']]",
            "print(json.dumps({'ids': ids, 'next': w._next_seq(key)}))",
        ]
    )
    proc = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path / "journal"), jf.PROJECT],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out == {"ids": ["J000001"], "next": 2}


def test_torn_last_line_is_skipped_not_fatal(live, tmp_path):
    _edit_total(live)
    path = _journal_path(tmp_path)
    with open(path, "ab") as fh:
        fh.write(b'{"type":"write","id":"J000002","seq":2,"cha')  # crash mid-append
    view = write_journal.read_journal(path.stem)
    assert len(view["entries"]) == 1 and view["corrupt_lines"] == 1


def test_journal_lives_under_the_git_ignored_output_folder():
    write_journal.reset()
    assert write_journal.journal_dir().parts[-2:] == ("output", "journal")
    ignore = (Path(__file__).resolve().parent.parent / ".gitignore").read_text(encoding="utf-8")
    assert any(line.strip() in ("output/", "/output/", "output") for line in ignore.splitlines())


# ---------------------------------------------------------------------------
# flush marking
# ---------------------------------------------------------------------------
def test_flush_marks_entries_with_a_checkpoint_record_not_a_rewrite(live, tmp_path):
    for expr in ("A", "B", "C"):
        _edit_total(live, expr)
    path = _journal_path(tmp_path)
    before = path.read_bytes()
    assert write_journal.unflushed_summary(jf.REPORTS, jf.MODELS)["count"] == 3

    assert write_journal.mark_flushed(jf.REPORTS, jf.MODELS) == 3
    after = path.read_bytes()
    assert after.startswith(before), "append only: existing lines are never rewritten"
    flush = json.loads(after[len(before) :].decode("utf-8"))
    assert (
        flush["type"] == "flush" and flush["up_to_seq"] == 3 and flush["reason"] == "save_verified"
    )
    view = write_journal.read_journal(path.stem)
    assert all(e["flushed"] for e in view["entries"])
    assert write_journal.unflushed_summary(jf.REPORTS, jf.MODELS)["count"] == 0

    _edit_total(live, "D")  # a later write is unflushed again
    assert write_journal.unflushed_summary(jf.REPORTS, jf.MODELS)["ids"] == ["J000004"]


def test_flush_only_covers_entries_written_before_the_save(live, tmp_path):
    _edit_total(live, "A")
    cutoff = write_journal._ts_epoch(_lines(tmp_path)[0]["ts"])  # noqa: SLF001
    import time

    time.sleep(0.01)
    _edit_total(live, "B")
    assert write_journal.mark_flushed(jf.REPORTS, jf.MODELS, before=cutoff) == 1
    assert write_journal.unflushed_summary(jf.REPORTS, jf.MODELS)["ids"] == ["J000002"]


def test_flush_ignores_other_projects(live, tmp_path):
    a = _project(tmp_path, "A")
    b = _project(tmp_path, "B")
    write_journal._connected_project = lambda: str(a["pbip"])  # noqa: SLF001
    _edit_total(live)
    assert write_journal.mark_flushed({str(b["report"])}, {str(b["model"])}) == 0
    assert write_journal.unflushed_summary({str(b["report"])}, {str(b["model"])})["count"] == 0
    assert write_journal.unflushed_summary({str(a["report"])}, {str(a["model"])})["count"] == 1
    assert write_journal.mark_flushed({str(a["report"])}, {str(a["model"])}) == 1


# ---------------------------------------------------------------------------
# secrets + argument sanitising
# ---------------------------------------------------------------------------
def test_sanitize_redacts_secrets_and_caps_text():
    out = write_journal.sanitize(
        {
            "password": "hunter2",
            "nested": {"api_key": "k-123", "Authorization": "Bearer abcdefghijkl"},
            "connection_string": "Server=x;Password=hunter2",
            "m_expression": 'let s = Sql.Database("x", "y", [Password="hunter2"]) in s',
            "expression": "Password=abc; Bearer ABCDEFGH12345678",
            "big": "x" * 5000,
            "items": list(range(200)),
        }
    )
    text = json.dumps(out)
    assert "hunter2" not in text and "k-123" not in text and "abcdefghijkl" not in text
    assert "ABCDEFGH12345678" not in text
    assert out["password"] == write_journal.REDACTED
    assert len(out["big"]) < 2100 and "+3000 chars" in out["big"]
    assert len(out["items"]) == 51
    assert out["items"][-1].startswith("...[+150 items")


def test_secrets_never_reach_the_journal_file(live, tmp_path):
    tom = live.connect()
    tom.measure("Sales", "Total")["expression"] = "1"
    args = {"password": "hunter2", "expression": "1", "m_expression": "x Password=hunter2;y"}
    with write_journal.call_scope("02_Model_Operations", "02_Model_Operations:table/create", args):
        commit(tom, label="table/create")
    text = _journal_path(tmp_path).read_text(encoding="utf-8")
    assert "hunter2" not in text
    assert json.loads(text.splitlines()[0])["args"]["password"] == write_journal.REDACTED


def test_dispatcher_scope_labels_the_entry(live, tmp_path):
    from server import dispatch

    tom = live.connect()
    tom.measure("Sales", "Total")["expression"] = "7"
    with dispatch._journal_scope(  # noqa: SLF001
        "02_Model_Operations:measure/update", {"table_name": "Sales", "measure_name": "Total"}
    ):
        commit(tom, label="measure/update")
    (rec,) = _lines(tmp_path)
    assert rec["tool"] == "02_Model_Operations"
    assert rec["operation"] == "measure/update"
    assert rec["args"]["measure_name"] == "Total"


# ---------------------------------------------------------------------------
# write paths that used to bypass the lane
# ---------------------------------------------------------------------------
def test_dax_injector_saves_go_through_the_write_lane():
    import re

    src = (Path(__file__).resolve().parent.parent / "core" / "dax" / "dax_injector.py").read_text(
        encoding="utf-8"
    )
    assert not re.findall(r"^\s*model\.SaveChanges\(\)", src, flags=re.M)
    assert "from core.operations.write_lane import commit" in src


def test_function_manager_tom_save_goes_through_the_write_lane():
    src = (
        Path(__file__).resolve().parent.parent / "core" / "operations" / "function_crud_manager.py"
    ).read_text(encoding="utf-8")
    assert "model.SaveChanges()" not in src


def test_tmsl_udf_write_is_journaled(live, tmp_path):
    from core.operations.function_crud_manager import FunctionCrudManager

    mgr = FunctionCrudManager()
    pre = {"myfn": {"name": "MyFn", "expression": "() => 1", "lineageTag": "t"}}
    cmd = {
        "sequence": {
            "operations": [
                {
                    "createOrReplace": {
                        "object": {"function": "MyFn"},
                        "function": {"name": "MyFn", "expression": "() => 2", "lineageTag": "t"},
                    }
                },
                {
                    "createOrReplace": {
                        "object": {"function": "New"},
                        "function": {"name": "New", "expression": "() => 3"},
                    }
                },
                {"delete": {"object": {"database": "d", "function": "MyFn"}}},
            ]
        }
    }
    out = {"success": True}
    mgr._journal_tmsl(cmd, pre, out)  # noqa: SLF001
    (rec,) = _lines(tmp_path)
    by_ref = {c["ref"]: c for c in rec["changes"]}
    assert by_ref["function:MyFn"]["change"] in {"modified", "removed"}
    assert by_ref["function:New"]["change"] == "added"
    assert rec["label"] == "function/tmsl" and "journal_warning" not in out


# ---------------------------------------------------------------------------
# bridge integration (status dirty_sources, reload refusal, flush on a verified save)
# ---------------------------------------------------------------------------
def _project_journal(live, proj, monkeypatch):
    monkeypatch.setattr(write_journal, "_connected_project", lambda: str(proj["pbip"]))
    _edit_total(live, "A")
    _edit_total(live, "B")


def test_status_dirty_sources_report_journal_unflushed(live, tmp_path, monkeypatch):
    proj = _project(tmp_path)
    _fake_cli(monkeypatch, [_inst(proj["pbip"])])
    _project_journal(live, proj, monkeypatch)
    out = db.op_status(include_dirty_sources=True)
    src = out["instances"][0]["dirty_sources"]
    assert src["journal_unflushed"] == 2 and src["journal_unflushed_ids"] == ["J000001", "J000002"]
    assert any("journal_unflushed: 2" in r for r in src["reasons"])


def test_reload_refusal_names_journal_unflushed_and_replay(live, tmp_path, monkeypatch):
    proj = _project(tmp_path)
    calls = _fake_cli(monkeypatch, [_inst(proj["pbip"], dirty=True)])
    _project_journal(live, proj, monkeypatch)
    out = db.op_reload(pid=7)
    assert out["success"] is False and out["error_type"] == "unsaved_changes"
    assert out["journal_unflushed"] == 2
    assert "journal_unflushed: 2" in out["error"] and "operation=replay" in out["error"]
    assert "reload" not in [c[0] for c in calls]


def test_discarded_reload_reports_what_replay_can_recover(live, tmp_path, monkeypatch):
    proj = _project(tmp_path)
    _fake_cli(monkeypatch, [_inst(proj["pbip"], dirty=True)])
    _project_journal(live, proj, monkeypatch)
    out = db.op_reload(pid=7, discard_unsaved=True)
    assert out["success"] is True
    assert out["discarded_unsaved"]["journal_unflushed"] == 2
    assert "replay" in out["discarded_unsaved"]["recover_with"]
    # a discarded reload does NOT flush: the entries are exactly what replay must restore
    assert (
        write_journal.unflushed_summary(*db._instance_artifacts(_inst(proj["pbip"])))["count"] == 2
    )  # noqa: SLF001


def test_verified_save_flushes_the_journal(live, tmp_path, monkeypatch):
    proj = _project(tmp_path)
    _fake_cli(monkeypatch, [_inst(proj["pbip"], dirty=True)])
    _project_journal(live, proj, monkeypatch)
    reports, models = db._instance_artifacts(_inst(proj["pbip"]))  # noqa: SLF001
    assert write_journal.unflushed_summary(reports, models)["count"] == 2

    class _UIA:
        def __init__(self, **k):
            pass

        def save(self, pid, pbip_path=None, required_tree=None):
            (proj["model"] / "definition" / "model.tmdl").write_text(
                "model Model\n", encoding="utf-8"
            )
            return {"success": True, "verified": "disk"}

    import core.autonomous.ui_automation as ui

    monkeypatch.setattr(ui, "PbiDesktopUIAutomation", _UIA)
    monkeypatch.setattr(db, "_verify_saved_clean", lambda *a, **k: True)
    out = db.op_save(pid=7)
    assert out["success"] is True and out["saved"] is True
    assert write_journal.unflushed_summary(reports, models)["count"] == 0
    assert (
        write_journal.read_journal(write_journal.resolve_key(str(proj["pbip"]), "db1"))[
            "flush_upto"
        ]
        == 2
    )


def test_open_does_not_flush_the_journal(live, tmp_path, monkeypatch):
    proj = _project(tmp_path)
    _project_journal(live, proj, monkeypatch)
    reports, models = db._instance_artifacts(_inst(proj["pbip"]))  # noqa: SLF001
    edit_ledger.clear_tom(reports, models)  # what `open` does to the ledger
    assert write_journal.unflushed_summary(reports, models)["count"] == 2


# ---------------------------------------------------------------------------
# lane bookkeeping unchanged
# ---------------------------------------------------------------------------
def test_lane_commit_is_the_only_journal_entry_point(live, tmp_path):
    lane = ModelWriteLane()
    tom = live.connect()
    tom.measure("Sales", "Total")["expression"] = "9"
    lane.commit(tom, label="x", journal_meta={"op": "custom"})
    (rec,) = _lines(tmp_path)
    assert rec["meta"] == {"op": "custom"}
