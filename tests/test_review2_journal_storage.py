"""Deep review 2026-10-03, slice N: write-journal storage (N-14, N-15, N-24, N-25, N-26, N-36).

Torn tail, strict flush marking, the unflushed index cache, retention, journal-name validation,
multi-process sequence numbers and the capture timing. Pure Python; synthetic records only.
"""

from __future__ import annotations

import json

import pytest

from core.autonomous import edit_ledger
from core.operations import write_journal as wj
from core.operations.write_lane import commit
from server.handlers import model_operations_handler as h
from tests import _journal_fakes as jf

CHG = {
    "ref": "measure:T[M]",
    "kind": "measure",
    "table": "T",
    "name": "M",
    "change": "modified",
    "pre": {"name": "M", "expression": "1"},
    "post": {"name": "M", "expression": "2"},
}


@pytest.fixture
def jdir(tmp_path):
    wj.reset()
    wj.configure(enabled=True, directory=tmp_path / "journal")
    yield tmp_path / "journal"
    wj.reset()


def _rec(key="proj-1", project=None, chg=None):
    return wj._write_record(  # noqa: SLF001
        key, {"project": project, "database_name": "db"}, "lbl", [chg or CHG], {}, {}, None
    )


# ---------------------------------------------------------------------------
# N-14: torn tail
# ---------------------------------------------------------------------------
def test_append_after_a_torn_tail_keeps_the_new_record_readable(jdir):
    _rec()
    path = jdir / "proj-1.jsonl"
    with open(path, "ab") as fh:  # a crash mid-append: partial line, no newline
        fh.write(b'{"v":1,"type":"write","id":"J000002","se')
    wj._SEQ.clear()  # noqa: SLF001 - a new process
    _rec()
    view = wj.read_journal("proj-1")
    assert [e["id"] for e in view["entries"]] == ["J000001", "J000002"]
    assert view["corrupt_lines"] == 1


def test_clean_file_is_not_given_an_extra_blank_line(jdir):
    _rec()
    _rec()
    raw = (jdir / "proj-1.jsonl").read_bytes()
    assert b"\n\n" not in raw and raw.endswith(b"\n")


def test_history_reports_corrupt_lines_with_a_warning(monkeypatch, tmp_path):
    wj.configure(enabled=True, directory=tmp_path / "journal")
    jf.install(monkeypatch, jf.LiveModel())
    key, _ = wj._identity(None)  # noqa: SLF001
    _rec(key, project=jf.PROJECT)
    with open(wj._path(key), "ab") as fh:  # noqa: SLF001
        fh.write(b'{"torn')
    out = h.handle_model_operations({"object_type": "model", "operation": "history"})
    assert out["corrupt_lines"] == 1 and "unreadable" in out["journal_warning"]


# ---------------------------------------------------------------------------
# N-15: strict flush
# ---------------------------------------------------------------------------
def test_flush_of_an_unrelated_save_never_touches_unknown_project_entries(jdir):
    _rec("Port_51234", project=None)
    n = wj.mark_flushed([r"C:\work\A\A.Report"], [r"C:\work\A\A.SemanticModel"])
    assert n == 0
    assert wj.read_journal("Port_51234")["entries"][0]["flushed"] is False


def test_unflushed_count_still_fails_closed_for_unknown_project(jdir):
    _rec("Port_51234", project=None)
    assert wj.unflushed_summary([r"C:\work\A\A.Report"], [])["count"] == 1


def test_flush_covers_the_saved_projects_entries_only(jdir, monkeypatch):
    a, b = r"C:\work\A\A.pbip", r"C:\work\B\B.pbip"
    arts = {
        edit_ledger.norm_path(a): ({"c:\\work\\a\\a.report"}, {"c:\\work\\a\\a.semanticmodel"}),
        edit_ledger.norm_path(b): ({"c:\\work\\b\\b.report"}, {"c:\\work\\b\\b.semanticmodel"}),
    }
    monkeypatch.setattr(
        edit_ledger,
        "project_artifacts",
        lambda p: arts.get(edit_ledger.norm_path(p), (set(), set())),
    )
    _rec("k1", project=a)
    _rec("k2", project=b)
    n = wj.mark_flushed({"c:\\work\\a\\a.report"}, {"c:\\work\\a\\a.semanticmodel"})
    assert n == 1
    assert wj.read_journal("k1")["entries"][0]["flushed"] is True
    assert wj.read_journal("k2")["entries"][0]["flushed"] is False


def test_flush_watermark_stops_below_another_projects_older_entry(jdir, monkeypatch):
    a, b = r"C:\work\A\A.pbip", r"C:\work\B\B.pbip"
    monkeypatch.setattr(
        edit_ledger,
        "project_artifacts",
        lambda p: (
            ({"c:\\work\\a\\a.report"}, set())
            if edit_ledger.norm_path(p) == edit_ledger.norm_path(a)
            else (set(), set())
        ),
    )
    _rec("k", project=b)  # seq 1, someone else's
    _rec("k", project=a)  # seq 2
    assert wj.mark_flushed({"c:\\work\\a\\a.report"}, []) == 0
    assert [e["flushed"] for e in wj.read_journal("k")["entries"]] == [False, False]


# ---------------------------------------------------------------------------
# N-24: index cache, retention, capture timing
# ---------------------------------------------------------------------------
def test_unflushed_summary_does_not_reparse_an_unchanged_journal(jdir, monkeypatch):
    for _ in range(3):
        _rec(project=None)
    wj.unflushed_summary([], [])
    calls = {"n": 0}
    real = wj._iter_records  # noqa: SLF001

    def counting(path):
        calls["n"] += 1
        return real(path)

    monkeypatch.setattr(wj, "_iter_records", counting)
    for _ in range(5):
        assert wj.unflushed_summary([], [])["count"] == 3
    assert calls["n"] == 0
    _rec(project=None)  # the file changed -> re-read once
    assert wj.unflushed_summary([], [])["count"] == 4
    assert calls["n"] == 1


def test_retention_drops_old_flushed_entries_but_never_unflushed(jdir, monkeypatch):
    monkeypatch.setattr(
        wj, "_cfg", lambda k, d: {"journal_max_bytes": 3000, "journal_retention_days": 0}.get(k, d)
    )
    for _ in range(6):
        _rec(project=None)
    # flush everything via an explicit watermark, then add unflushed ones
    wj._append("proj-1", {"type": "flush", "up_to_seq": 6, "ts": wj._now_iso()})  # noqa: SLF001
    for _ in range(2):
        _rec(project=None)
    wj._enforce_retention("proj-1")  # noqa: SLF001
    view = wj.read_journal("proj-1")
    ids = [e["id"] for e in view["entries"]]
    assert "J000007" in ids and "J000008" in ids  # unflushed survive
    assert len(ids) < 8
    assert all(e["flushed"] for e in view["entries"] if int(e["seq"]) <= 6)
    assert wj._next_seq("proj-1") == 9  # numbering continues  # noqa: SLF001


def test_retention_is_off_below_the_cap(jdir):
    for _ in range(3):
        _rec(project=None)
    wj._append("proj-1", {"type": "flush", "up_to_seq": 3, "ts": wj._now_iso()})  # noqa: SLF001
    before = (jdir / "proj-1.jsonl").read_bytes()
    wj._enforce_retention("proj-1")  # noqa: SLF001
    assert (jdir / "proj-1.jsonl").read_bytes() == before


def test_commit_records_where_the_pre_image_came_from(monkeypatch, tmp_path):
    wj.configure(enabled=True, directory=tmp_path / "journal")
    live = jf.LiveModel()
    jf.install(monkeypatch, live)
    tom = live.connect()
    tom.measure("Sales", "Total")["expression"] = "X"
    with wj.call_scope("02_Model_Operations", "02_Model_Operations:measure/update", {}):
        commit(tom, label="measure/update")
    key, _ = wj._identity(None)  # noqa: SLF001
    (entry,) = wj.read_journal(key)["entries"]
    assert entry["capture"]["pre_source"] in ("cache", "server")
    assert isinstance(entry["capture"]["ms"], int)


# ---------------------------------------------------------------------------
# N-25: journal names
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "bad", ["..", "../x", "a/b", "a\\b", "", ".hidden", "C:\\x", "a..b", "x" * 200, None]
)
def test_invalid_journal_names_are_rejected(bad):
    assert wj.valid_key(bad) is False


def test_path_refuses_a_traversing_key(jdir):
    with pytest.raises(ValueError):
        wj._path("../outside")  # noqa: SLF001
    assert wj.valid_key("Proj-1a2b3c4d") is True


# ---------------------------------------------------------------------------
# N-26: ids stay unique when another process appended
# ---------------------------------------------------------------------------
def test_sequence_is_rescanned_after_an_append_by_another_process(jdir):
    assert _rec() == "J000001"
    other = {
        "v": 1,
        "type": "write",
        "id": "J000002",
        "seq": 2,
        "ts": wj._now_iso(),  # noqa: SLF001
        "changes": [CHG],
        "refs": ["measure:T[M]"],
        "flushed": False,
    }
    with open(jdir / "proj-1.jsonl", "ab") as fh:  # not through _append: no _SEQ_SIZE update
        fh.write((json.dumps(other) + "\n").encode("ascii"))
    assert _rec() == "J000003"
    ids = [e["id"] for e in wj.read_journal("proj-1")["entries"]]
    assert ids == ["J000001", "J000002", "J000003"]


def test_lock_file_does_not_show_up_as_a_journal(jdir):
    _rec()
    assert (jdir / "proj-1.lock").exists()
    assert [j["key"] for j in wj.list_journals()] == ["proj-1"]


# ---------------------------------------------------------------------------
# N-22 end to end: a restore that removes a data column needs confirm_destructive
# ---------------------------------------------------------------------------
def test_checkpoint_restore_removing_a_column_needs_confirmation(monkeypatch, tmp_path):
    wj.configure(enabled=True, directory=tmp_path / "journal")
    live = jf.LiveModel()
    jf.install(monkeypatch, live)
    op = lambda o, **kw: h.handle_model_operations(  # noqa: E731
        {"object_type": "model", "operation": o, **kw}
    )
    assert op("checkpoint_create", checkpoint="ck")["success"] is True
    tom = live.connect()
    tom.table("Sales")["columns"].append(
        {"name": "Extra", "dataType": "string", "sourceColumn": "Extra", "lineageTag": "t-x"}
    )
    with wj.call_scope("02_Model_Operations", "02_Model_Operations:column/create", {}):
        commit(tom, label="column/create")
    prev = op("checkpoint_restore", checkpoint="ck", preview=True)
    assert prev["requires_confirm_destructive"] is True
    assert "column:Sales[Extra]" in prev["removes_gated_objects"]
    refused = op("checkpoint_restore", checkpoint="ck")
    assert refused["success"] is False and refused["error_type"] == "confirmation_required"
    ok = op("checkpoint_restore", checkpoint="ck", confirm_destructive=True)
    assert ok["success"] is True
    assert all(c["name"] != "Extra" for c in live.tmsl["tables"][0]["columns"])
