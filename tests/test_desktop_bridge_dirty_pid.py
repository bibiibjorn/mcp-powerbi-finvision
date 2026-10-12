"""Regression (review J-01): op_reload's explicit-pid path must never reload a
Desktop that may hold unsaved in-memory edits.

The old explicit-pid "fast path" skipped the status pre-flight: it consulted a
cached status (<=5 s stale) when one happened to exist, otherwise reloaded with
``dirty_check_skipped: True`` — and in both cases only WARNED after the reload
had already discarded the edits. These tests pinned that warn-and-proceed
behaviour; they now pin the fail-closed one: the instance is re-read with
``status --pid`` right before the reload, and a dirty / unknown state refuses.
"""

import core.autonomous.desktop_bridge as db


def _seed_cache(monkeypatch, instances):
    monkeypatch.setattr(
        db, "_STATUS_CACHE", {"t": 1e9, "result": {"success": True, "instances": instances}}
    )
    # Make the cache look fresh regardless of monotonic clock.
    monkeypatch.setattr(db.time, "monotonic", lambda: 1e9)


def _cli(monkeypatch, instance, calls):
    def fake_run(argv, **k):
        calls.append(list(argv))
        if argv[0] == "status":
            return {"ok": True, "data": {"status": "ready", "instances": [instance]}}
        return {"ok": True, "data": {"status": "ok", "result": {"success": True}}}

    monkeypatch.setattr(db, "run_bridge", fake_run)


def test_explicit_pid_dirty_refuses_before_reloading(monkeypatch):
    calls = []
    _cli(monkeypatch, {"pid": 7, "currentFilePath": "x.pbip", "hasUnsavedChanges": True}, calls)
    out = db.op_reload(pid=7)
    assert out["success"] is False
    assert out["error_type"] == "unsaved_changes"
    assert [c[0] for c in calls] == ["status"]  # the reload was never issued


def test_stale_clean_cache_does_not_hide_a_fresh_dirty_state(monkeypatch):
    # B4: a clean status cached < 5 s ago must not be trusted — a TOM write may
    # have happened since. The reload re-reads the instance.
    _seed_cache(monkeypatch, [{"pid": 7, "currentFilePath": "x.pbip", "hasUnsavedChanges": False}])
    calls = []
    _cli(monkeypatch, {"pid": 7, "currentFilePath": "x.pbip", "hasUnsavedChanges": True}, calls)
    out = db.op_reload(pid=7)
    assert out["success"] is False
    assert out["error_type"] == "unsaved_changes"
    assert "reload" not in [c[0] for c in calls]


def test_explicit_pid_clean_reloads(monkeypatch):
    calls = []
    _cli(monkeypatch, {"pid": 7, "currentFilePath": "x.pbip", "hasUnsavedChanges": False}, calls)
    out = db.op_reload(pid=7)
    assert out["success"] is True
    assert out["dirty_state"] == "clean"
    assert [c[0] for c in calls] == ["status", "reload"]


def test_unknown_dirty_state_refuses(monkeypatch):
    # B3: bridgeStatus=error (no hasUnsavedChanges) is UNKNOWN, never clean.
    calls = []
    _cli(monkeypatch, {"pid": 7, "bridgeStatus": "error", "error": {"message": "timeout"}}, calls)
    out = db.op_reload(pid=7)
    assert out["success"] is False
    assert out["error_type"] == "dirty_state_unknown"
    assert "reload" not in [c[0] for c in calls]


def test_discard_unsaved_override_reloads_and_reports(monkeypatch):
    calls = []
    _cli(monkeypatch, {"pid": 7, "currentFilePath": "x.pbip", "hasUnsavedChanges": True}, calls)
    out = db.op_reload(pid=7, discard_unsaved=True)
    assert out["success"] is True
    assert out["discarded_unsaved"]["verdict"] == "dirty"
    assert "reload" in [c[0] for c in calls]


def test_explicit_pid_save_first_saves_then_reloads(monkeypatch):
    calls = []
    _cli(monkeypatch, {"pid": 7, "currentFilePath": "x.pbip", "hasUnsavedChanges": True}, calls)
    monkeypatch.setattr(
        db, "op_save", lambda **k: {"success": True, "pid": 7, "saved": True, "verified": "disk"}
    )
    out = db.op_reload(pid=7, save_first=True)
    assert out["success"] is True
    assert out.get("saved") is True
    assert "reload" in [c[0] for c in calls]
