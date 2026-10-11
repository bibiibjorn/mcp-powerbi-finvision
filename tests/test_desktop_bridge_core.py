"""Unit tests for core.autonomous.desktop_bridge — subprocess wrapper, PID
selection, page resolution, and high-level ops. Mocks the subprocess boundary;
never invokes the real powerbi-desktop CLI or a live Desktop."""

import json
import subprocess

import pytest

from core.autonomous import desktop_bridge as db


class _FakeCompleted:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


# ---------------------------------------------------------------------------
# Task 1 — run_bridge
# ---------------------------------------------------------------------------
def test_run_bridge_parses_json_stdout(monkeypatch):
    payload = {"status": "ready", "instances": []}

    def fake_run(argv, **kwargs):
        # Regression: argv[0] must be the RESOLVED path from shutil.which (so a
        # Windows .cmd/.CMD shim is executable), NOT the bare "powerbi-desktop"
        # name — passing the bare name to subprocess.run raises WinError 2.
        assert argv[0] == r"C:\fake\powerbi-desktop.CMD"
        assert argv[1] == "status"
        return _FakeCompleted(stdout=json.dumps(payload))

    monkeypatch.setattr(db.shutil, "which", lambda _: r"C:\fake\powerbi-desktop.CMD")
    monkeypatch.setattr(db.subprocess, "run", fake_run)

    result = db.run_bridge(["status"], timeout=10)
    assert result["ok"] is True
    assert result["data"] == payload


def test_run_bridge_cli_not_installed(monkeypatch):
    monkeypatch.setattr(db.shutil, "which", lambda _: None)
    result = db.run_bridge(["status"], timeout=10)
    assert result["ok"] is False
    assert result["error_type"] == "cli_not_installed"


def test_run_bridge_non_json_stdout(monkeypatch):
    monkeypatch.setattr(db.shutil, "which", lambda _: "C:/fake/powerbi-desktop")
    monkeypatch.setattr(db.subprocess, "run", lambda *a, **k: _FakeCompleted(stdout="not json"))
    result = db.run_bridge(["status"], timeout=10)
    assert result["ok"] is False
    assert result["error_type"] == "bad_output"
    assert "not json" in result["raw_stdout"]


def test_run_bridge_nonzero_exit_is_error_even_with_json(monkeypatch):
    # Regression: the CLI can exit non-zero WITH a JSON error envelope on
    # stdout. That must not masquerade as ok=True.
    monkeypatch.setattr(db.shutil, "which", lambda _: "C:/fake/powerbi-desktop")
    monkeypatch.setattr(
        db.subprocess,
        "run",
        lambda *a, **k: _FakeCompleted(
            stdout='{"status": "error", "message": "no pipe"}', stderr="boom", returncode=1
        ),
    )
    result = db.run_bridge(["reload"], timeout=10)
    assert result["ok"] is False
    assert result["error_type"] == "cli_error"
    assert result["returncode"] == 1
    assert "boom" in result["raw_stderr"]
    assert result["data"] == {"status": "error", "message": "no pipe"}


def test_run_bridge_nonzero_exit_empty_stdout(monkeypatch):
    # Regression: non-zero exit with EMPTY stdout used to become ok=True with
    # data={} (misreported downstream as "not connected").
    monkeypatch.setattr(db.shutil, "which", lambda _: "C:/fake/powerbi-desktop")
    monkeypatch.setattr(
        db.subprocess,
        "run",
        lambda *a, **k: _FakeCompleted(stdout="", stderr="cannot connect", returncode=2),
    )
    result = db.run_bridge(["status"], timeout=10)
    assert result["ok"] is False
    assert result["error_type"] == "cli_error"
    assert "cannot connect" in result["raw_stderr"]


def test_run_bridge_timeout(monkeypatch):
    monkeypatch.setattr(db.shutil, "which", lambda _: "C:/fake/powerbi-desktop")

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="powerbi-desktop", timeout=10)

    monkeypatch.setattr(db.subprocess, "run", boom)
    result = db.run_bridge(["status"], timeout=10)
    assert result["ok"] is False
    assert result["error_type"] == "timeout"


# ---------------------------------------------------------------------------
# Task 2 — select_instance + resolve_page_id
# ---------------------------------------------------------------------------
def test_select_instance_single():
    status = {"status": "ready", "instances": [{"pid": 42, "currentFilePath": "x.pbip"}]}
    inst = db.select_instance(status, pid=None, pbip_path=None)
    assert inst["pid"] == 42


def test_select_instance_explicit_pid():
    status = {"instances": [{"pid": 42}, {"pid": 99}]}
    inst = db.select_instance(status, pid=99, pbip_path=None)
    assert inst["pid"] == 99


def test_select_instance_ambiguous_raises():
    status = {
        "instances": [
            {"pid": 42, "currentFilePath": "a.pbip"},
            {"pid": 99, "currentFilePath": "b.pbip"},
        ]
    }
    with pytest.raises(db.BridgeAmbiguous):
        db.select_instance(status, pid=None, pbip_path=None)


def test_select_instance_disambiguate_by_path():
    status = {
        "instances": [
            {"pid": 42, "currentFilePath": r"C:\repo\A.pbip"},
            {"pid": 99, "currentFilePath": r"C:\repo\B.pbip"},
        ]
    }
    inst = db.select_instance(status, pid=None, pbip_path=r"C:\repo\B.pbip")
    assert inst["pid"] == 99


def test_select_instance_ambiguous_carries_instances():
    # The ambiguous error must list the candidates so an agent can
    # self-correct without another status round-trip.
    status = {
        "instances": [
            {"pid": 42, "currentFilePath": "a.pbip"},
            {"pid": 99, "currentFilePath": "b.pbip"},
        ]
    }
    with pytest.raises(db.BridgeAmbiguous) as exc:
        db.select_instance(status, pid=None, pbip_path=None)
    assert exc.value.extra["instances"] == [
        {"pid": 42, "currentFilePath": "a.pbip"},
        {"pid": 99, "currentFilePath": "b.pbip"},
    ]


def test_ambiguous_envelope_lists_instances(monkeypatch):
    monkeypatch.setattr(
        db,
        "op_status",
        lambda **k: {
            "success": True,
            "instances": [
                {"pid": 42, "currentFilePath": "a.pbip"},
                {"pid": 99, "currentFilePath": "b.pbip"},
            ],
        },
    )
    out = db.op_reload()  # no pid, no pbip_path -> ambiguous
    assert out["success"] is False
    assert out["error_type"] == "ambiguous_instance"
    assert [i["pid"] for i in out["instances"]] == [42, 99]


def test_select_instance_none_connected_raises():
    with pytest.raises(db.BridgeNotConnected):
        db.select_instance({"status": "not_connected", "instances": []}, pid=None, pbip_path=None)


def test_resolve_page_id_by_display_name():
    status_instance = {
        "pages": [
            {"id": "abc123", "displayName": "Cover Page"},
            {"id": "def456", "displayName": "Content"},
        ]
    }
    assert db.resolve_page_id(status_instance, "Content") == "def456"
    # already an id passes through
    assert db.resolve_page_id(status_instance, "abc123") == "abc123"


# ---------------------------------------------------------------------------
# Task 3 — high-level ops
# ---------------------------------------------------------------------------
def test_op_status_returns_data(monkeypatch):
    payload = {"status": "ready", "instances": [{"pid": 7, "pages": []}]}
    monkeypatch.setattr(db, "run_bridge", lambda argv, **k: {"ok": True, "data": payload})
    out = db.op_status(wait_seconds=5)
    assert out["success"] is True
    assert out["instances"][0]["pid"] == 7


def test_op_reload_builds_argv(monkeypatch):
    seen = {}

    def fake_run(argv, **k):
        seen["argv"] = argv
        return {"ok": True, "data": {"status": "ok", "pid": 7}}

    monkeypatch.setattr(db, "run_bridge", fake_run)
    monkeypatch.setattr(
        db,
        "op_status",
        lambda **k: {
            "success": True,
            "instances": [
                {"pid": 7, "currentFilePath": "x.pbip", "hasUnsavedChanges": False, "pages": []}
            ],
        },
    )
    out = db.op_reload(pid=7)
    assert out["success"] is True
    assert seen["argv"] == ["reload", "--pid", "7"]


def test_op_screenshot_resolves_page_name(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        db,
        "op_status",
        lambda **k: {
            "success": True,
            "instances": [
                {
                    "pid": 7,
                    "currentFilePath": "x.pbip",
                    "pages": [{"id": "pid_abc", "displayName": "Cover"}],
                }
            ],
        },
    )

    def fake_run(argv, **k):
        seen["argv"] = argv
        return {"ok": True, "data": {"status": "ok", "outputPath": "out.png"}}

    monkeypatch.setattr(db, "run_bridge", fake_run)
    out = db.op_screenshot(page="Cover", pid=7, output="out.png")
    assert out["success"] is True
    assert "pid_abc" in seen["argv"]
    assert "--output" in seen["argv"]


def test_op_screenshot_unknown_page(monkeypatch):
    monkeypatch.setattr(
        db,
        "op_status",
        lambda **k: {
            "success": True,
            "instances": [
                {
                    "pid": 7,
                    "currentFilePath": "x.pbip",
                    "pages": [{"id": "a", "displayName": "Cover"}],
                }
            ],
        },
    )
    out = db.op_screenshot(page="Nope", pid=7, output="out.png")
    assert out["success"] is False
    assert out["error_type"] == "page_not_found"


# ---------------------------------------------------------------------------
# Flow/latency — status TTL cache, reload fast path, settle retry, PID lock,
# dirty warning + save_first
# ---------------------------------------------------------------------------
def _fake_run_counting(calls, pages=None, has_unsaved=False):
    pages = pages if pages is not None else [{"id": "a", "displayName": "P"}]

    def fake_run(argv, **k):
        calls.setdefault(argv[0], 0)
        calls[argv[0]] += 1
        if argv[0] == "status":
            return {
                "ok": True,
                "data": {
                    "status": "ready",
                    "instances": [
                        {
                            "pid": 7,
                            "currentFilePath": "x.pbip",
                            "hasUnsavedChanges": has_unsaved,
                            "pages": pages,
                        }
                    ],
                },
            }
        return {"ok": True, "data": {"status": "ok"}}

    return fake_run


def test_instance_resolution_uses_status_cache(monkeypatch):
    calls = {}
    monkeypatch.setattr(db, "run_bridge", _fake_run_counting(calls))
    monkeypatch.setattr(db, "_optimize_output", lambda out: None)
    db.op_screenshot(page="P", output="o.png")
    db.op_screenshot(page="P", output="o.png")
    # Two screenshots within the TTL -> ONE status subprocess spawn.
    assert calls["status"] == 1
    assert calls["screenshot"] == 2


def test_explicit_status_op_bypasses_cache(monkeypatch):
    calls = {}
    monkeypatch.setattr(db, "run_bridge", _fake_run_counting(calls))
    db.op_status()
    db.op_status()
    assert calls["status"] == 2


def test_op_reload_explicit_pid_still_rereads_status(monkeypatch):
    # J-01: the explicit-pid "fast path" used to skip the status pre-flight and
    # therefore the unsaved-changes check. A reload now ALWAYS re-reads the
    # target instance (scoped with --pid) right before reloading.
    calls = {}
    argvs = []
    inner = _fake_run_counting(calls)

    def fake_run(argv, **k):
        argvs.append(list(argv))
        return inner(argv, **k)

    monkeypatch.setattr(db, "run_bridge", fake_run)
    out = db.op_reload(pid=7)
    assert out["success"] is True
    assert calls["status"] == 1
    assert argvs[0][:3] == ["status", "--pid", "7"]
    assert calls["reload"] == 1


def test_reload_invalidates_status_cache(monkeypatch):
    calls = {}
    monkeypatch.setattr(db, "run_bridge", _fake_run_counting(calls))
    monkeypatch.setattr(db, "_optimize_output", lambda out: None)
    db.op_screenshot(page="P", output="o.png")  # warms the cache
    db.op_reload(pid=7)  # fresh pre-flight status (never cached) + reload
    db.op_screenshot(page="P", output="o.png")  # cache was invalidated -> refetch
    assert calls["status"] == 3


def test_screenshot_refreshes_once_on_page_not_found_without_sleeping(monkeypatch):
    # J-21: the CLI reads the page list from pages.json ON DISK, so sleeping 3 s
    # never helped. A missing page is re-checked ONCE with a fresh status (the
    # page may have just been created on disk), without sleeping.
    statuses = [
        {"success": True, "instances": [{"pid": 7, "currentFilePath": "x.pbip", "pages": []}]},
        {
            "success": True,
            "instances": [
                {
                    "pid": 7,
                    "currentFilePath": "x.pbip",
                    "pages": [{"id": "a", "displayName": "P"}],
                }
            ],
        },
    ]
    monkeypatch.setattr(db, "op_status", lambda **k: statuses.pop(0))
    monkeypatch.setattr(db, "run_bridge", lambda argv, **k: {"ok": True, "data": {}})
    monkeypatch.setattr(db, "_optimize_output", lambda out: None)
    sleeps = []
    monkeypatch.setattr(db.time, "sleep", lambda s: sleeps.append(s))
    out = db.op_screenshot(page="P", pid=7, output="o.png")
    assert out["success"] is True
    assert out["page_id"] == "a"
    assert sleeps == []
    assert statuses == []  # both statuses consumed: initial + one refresh


def test_screenshot_page_not_found_after_retry(monkeypatch):
    inst = {"pid": 7, "currentFilePath": "x.pbip", "pages": [{"id": "a", "displayName": "Other"}]}
    monkeypatch.setattr(db, "op_status", lambda **k: {"success": True, "instances": [inst]})
    monkeypatch.setattr(db, "run_bridge", lambda argv, **k: {"ok": True, "data": {}})
    monkeypatch.setattr(db.time, "sleep", lambda s: None)
    out = db.op_screenshot(page="Nope", pid=7, output="o.png")
    assert out["success"] is False
    assert out["error_type"] == "page_not_found"
    assert out["available"] == ["Other"]


def test_ops_serialized_per_pid(monkeypatch):
    import threading
    import time as _t

    state = {"active": 0, "max": 0}
    guard = threading.Lock()

    def slow_run(argv, **k):
        with guard:
            state["active"] += 1
            state["max"] = max(state["max"], state["active"])
        _t.sleep(0.05)
        with guard:
            state["active"] -= 1
        return {"ok": True, "data": {}}

    def slow_with_status(argv, **k):
        res = slow_run(argv, **k)
        if argv[0] == "status":
            # A clean instance, so every thread really reaches the reload.
            return {
                "ok": True,
                "data": {
                    "instances": [
                        {"pid": 7, "currentFilePath": "x.pbip", "hasUnsavedChanges": False}
                    ]
                },
            }
        return res

    monkeypatch.setattr(db, "run_bridge", slow_with_status)
    threads = [threading.Thread(target=lambda: db.op_reload(pid=7)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # The CLI cancels concurrent calls to one PID; the wrapper must serialize.
    assert state["max"] == 1


def test_op_reload_refuses_when_dirty(monkeypatch):
    # J-01: a dirty Desktop used to be reloaded and a warning attached AFTER the
    # in-memory edits were gone. The reload now refuses up front.
    calls = {}
    monkeypatch.setattr(db, "run_bridge", _fake_run_counting(calls, has_unsaved=True))
    out = db.op_reload()  # resolved via status (single instance)
    assert out["success"] is False
    assert out["error_type"] == "unsaved_changes"
    assert out["dirty_sources"]["intent_flag"] is True
    assert calls.get("reload", 0) == 0


def test_op_reload_save_first(monkeypatch):
    calls = {}
    order = []
    monkeypatch.setattr(db, "run_bridge", _fake_run_counting(calls, has_unsaved=True))

    def fake_save(**k):
        order.append("save")
        return {"success": True, "pid": 7}

    monkeypatch.setattr(db, "op_save", fake_save)
    out = db.op_reload(pid=7, save_first=True)
    assert out["success"] is True
    assert order == ["save"]
    assert out["saved"] is True


def test_op_reload_save_first_failure_propagates(monkeypatch):
    monkeypatch.setattr(db, "run_bridge", _fake_run_counting({}))
    monkeypatch.setattr(
        db, "op_save", lambda **k: {"success": False, "error_type": "save_timeout", "error": "x"}
    )
    out = db.op_reload(pid=7, save_first=True)
    assert out["success"] is False
    assert out["error_type"] == "save_timeout"


# ---------------------------------------------------------------------------
# Task 4 — op_save (UI-automation Ctrl+S; the MS CLI has no save subcommand)
# ---------------------------------------------------------------------------
def _dirty_instance(pid=7):
    return {
        "success": True,
        "instances": [
            {"pid": pid, "currentFilePath": "x.pbip", "hasUnsavedChanges": True, "pages": []}
        ],
    }


def test_op_save_short_circuits_when_clean(monkeypatch):
    monkeypatch.setattr(
        db,
        "op_status",
        lambda **k: {
            "success": True,
            "instances": [
                {"pid": 7, "currentFilePath": "x.pbip", "hasUnsavedChanges": False, "pages": []}
            ],
        },
    )
    out = db.op_save(pid=7)
    assert out["success"] is True
    assert out["already_saved"] is True


def test_op_save_invokes_ui_automation(monkeypatch):
    # Dirty before the save; clean afterwards (post-save verification polls).
    seq = [_dirty_instance(7)]

    def _status(**k):
        if seq:
            return seq.pop(0)
        return {
            "success": True,
            "instances": [
                {"pid": 7, "currentFilePath": "x.pbip", "hasUnsavedChanges": False, "pages": []}
            ],
        }

    monkeypatch.setattr(db, "op_status", _status)

    seen = {}

    class _FakeUIA:
        def __init__(self, save_timeout=30.0):
            seen["save_timeout"] = save_timeout

        def save(self, pid, pbip_path=None):
            seen["pid"] = pid
            return {"success": True, "title_before": "*r", "title_after": "r"}

    import core.autonomous.ui_automation as uia

    monkeypatch.setattr(uia, "PbiDesktopUIAutomation", _FakeUIA)
    out = db.op_save(pid=7, save_timeout=15)
    assert out["success"] is True
    assert seen["pid"] == 7
    assert seen["save_timeout"] == 15
    assert out["title_after"] == "r"


def test_op_save_propagates_failure(monkeypatch):
    monkeypatch.setattr(db, "op_status", lambda **k: _dirty_instance(7))

    class _FakeUIA:
        def __init__(self, save_timeout=30.0):
            pass

        def save(self, pid, pbip_path=None):
            return {
                "success": False,
                "error_type": "save_timeout",
                "error": "still dirty",
                "title_after": "*r",
            }

    import core.autonomous.ui_automation as uia

    monkeypatch.setattr(uia, "PbiDesktopUIAutomation", _FakeUIA)
    out = db.op_save(pid=7)
    assert out["success"] is False
    assert out["error_type"] == "save_timeout"
    assert out["pid"] == 7


def test_op_save_not_connected(monkeypatch):
    monkeypatch.setattr(db, "op_status", lambda **k: {"success": True, "instances": []})
    out = db.op_save()
    assert out["success"] is False
    assert out["error_type"] == "not_connected"


# ---------------------------------------------------------------------------
# Composite op — reload + settle + screenshot in ONE call (one status
# pre-flight, serialization by construction, one agent turn instead of two)
# ---------------------------------------------------------------------------
def test_reload_and_screenshot_happy_path(monkeypatch):
    order = []

    def fake_reload(**k):
        order.append("reload")
        return {"success": True, "pid": 7, "data": {"status": "ok"}}

    def fake_screenshot(**k):
        order.append("screenshot")
        return {"success": True, "pid": 7, "page_id": "a", "output": k["output"], "data": {}}

    monkeypatch.setattr(db, "op_reload", fake_reload)
    monkeypatch.setattr(db, "op_screenshot", fake_screenshot)
    out = db.op_reload_and_screenshot(page="P", output="o.png", pid=7)
    assert order == ["reload", "screenshot"]
    assert out["success"] is True
    assert out["reload"]["success"] is True
    assert out["output"] == "o.png"


def test_reload_and_screenshot_reload_failure_short_circuits(monkeypatch):
    called = []
    monkeypatch.setattr(
        db, "op_reload", lambda **k: {"success": False, "error_type": "timeout", "error": "x"}
    )
    monkeypatch.setattr(db, "op_screenshot", lambda **k: called.append(1))
    out = db.op_reload_and_screenshot(page="P", output="o.png", pid=7)
    assert out["success"] is False
    assert out["error_type"] == "timeout"
    assert called == []


def test_reload_and_screenshot_passes_options_through(monkeypatch):
    seen = {}

    def fake_reload(**k):
        seen["reload"] = k
        return {"success": True, "pid": 7, "data": {}}

    def fake_screenshot(**k):
        seen["shot"] = k
        return {"success": True, "pid": 7, "page_id": "a", "output": k["output"], "data": {}}

    monkeypatch.setattr(db, "op_reload", fake_reload)
    monkeypatch.setattr(db, "op_screenshot", fake_screenshot)
    db.op_reload_and_screenshot(
        page="P",
        output="o.png",
        pid=7,
        save_first=True,
        per_visual=True,
        report_root="R.Report",
        optimize=False,
    )
    assert seen["reload"]["save_first"] is True
    assert seen["shot"]["per_visual"] is True
    assert seen["shot"]["report_root"] == "R.Report"
    assert seen["shot"]["optimize"] is False


def test_all_does_not_export_path():
    assert "Path" not in db.__all__
    assert "op_reload_and_screenshot" in db.__all__


# ---------------------------------------------------------------------------
# op_save post-verification via bridge status (stronger than the title
# heuristic: Desktop itself reports hasUnsavedChanges)
# ---------------------------------------------------------------------------
def _saving_uia(monkeypatch):
    import core.autonomous.ui_automation as uia

    class _FakeUIA:
        def __init__(self, save_timeout=30.0):
            pass

        def save(self, pid, pbip_path=None):
            return {"success": True, "title_before": "*r", "title_after": "r"}

    monkeypatch.setattr(uia, "PbiDesktopUIAutomation", _FakeUIA)


def _save_status(has_unsaved):
    inst = {"pid": 7, "currentFilePath": "x.pbip", "pages": []}
    if has_unsaved is not None:
        inst["hasUnsavedChanges"] = has_unsaved
    return {"success": True, "instances": [inst]}


def test_op_save_verified_clean_via_bridge(monkeypatch):
    seq = [_save_status(True), _save_status(False)]
    monkeypatch.setattr(db, "op_status", lambda **k: seq.pop(0))
    monkeypatch.setattr(db.time, "sleep", lambda s: None)
    _saving_uia(monkeypatch)
    out = db.op_save(pid=7)
    assert out["success"] is True
    assert out["verified_clean"] is True


def test_op_save_unverified_when_bridge_still_dirty(monkeypatch):
    monkeypatch.setattr(db, "op_status", lambda **k: _save_status(True))
    monkeypatch.setattr(db.time, "sleep", lambda s: None)
    _saving_uia(monkeypatch)
    out = db.op_save(pid=7)
    assert out["success"] is False
    assert out["error_type"] == "save_unverified"


def test_op_save_disk_verification_beats_stale_dirty_flag(monkeypatch):
    """A disk-verified save must WIN over hasUnsavedChanges=true.

    hasUnsavedChanges is Desktop's intent marker, not a save receipt. Trusting
    it over the filesystem is what turned real saves into 99 false
    `save_unverified` failures out of 158 attempts. If the bytes changed and
    settled, the save landed -- whatever the flag says afterwards.
    """
    import core.autonomous.ui_automation as uia

    class _DiskVerifiedUIA:
        def __init__(self, save_timeout=30.0):
            pass

        def save(self, pid, pbip_path=None):
            return {
                "success": True,
                "verified": "disk",
                "mechanism": "invoked Button {'auto_id': 'SaveButton'}",
            }

    monkeypatch.setattr(uia, "PbiDesktopUIAutomation", _DiskVerifiedUIA)
    # Bridge keeps insisting the model is dirty; disk says otherwise.
    monkeypatch.setattr(db, "op_status", lambda **k: _save_status(True))
    monkeypatch.setattr(db.time, "sleep", lambda s: None)

    out = db.op_save(pid=7)
    assert out["success"] is True
    assert out["verified_clean"] is True
    assert out["verified"] == "disk"


def test_op_save_verification_skipped_without_flag(monkeypatch):
    # CLI build that doesn't report hasUnsavedChanges: title heuristic is all
    # we have — succeed, but mark verification as unavailable.
    monkeypatch.setattr(db, "op_status", lambda **k: _save_status(None))
    monkeypatch.setattr(db.time, "sleep", lambda s: None)
    _saving_uia(monkeypatch)
    out = db.op_save(pid=7)
    assert out["success"] is True
    assert out["verified_clean"] is None


# ---------------------------------------------------------------------------
# Task 5 — screenshot auto-optimize (default ON) + per-visual crop
# ---------------------------------------------------------------------------
def _instance_with_page(pid=7, page_id="pid_abc", display="Cover"):
    return {
        "success": True,
        "instances": [
            {
                "pid": pid,
                "currentFilePath": "x.pbip",
                "pages": [{"id": page_id, "displayName": display}],
            }
        ],
    }


def test_op_screenshot_optimizes_by_default(monkeypatch):
    monkeypatch.setattr(db, "op_status", lambda **k: _instance_with_page())
    monkeypatch.setattr(db, "run_bridge", lambda argv, **k: {"ok": True, "data": {"status": "ok"}})
    seen = {}

    def _fake_opt(out):
        seen["out"] = out
        return {"visual_tokens": 1296}

    monkeypatch.setattr(db, "_optimize_output", _fake_opt)
    res = db.op_screenshot(page="Cover", pid=7, output="page.png")
    assert res["success"] is True
    assert seen["out"] == "page.png"  # optimizer was called on the captured PNG
    assert res["optimize"] == {"visual_tokens": 1296}


def test_op_screenshot_optimize_false_skips(monkeypatch):
    monkeypatch.setattr(db, "op_status", lambda **k: _instance_with_page())
    monkeypatch.setattr(db, "run_bridge", lambda argv, **k: {"ok": True, "data": {"status": "ok"}})
    called = {"n": 0}

    def _boom(out):
        called["n"] += 1
        return None

    monkeypatch.setattr(db, "_optimize_output", _boom)
    res = db.op_screenshot(page="Cover", pid=7, output="page.png", optimize=False)
    assert res["success"] is True
    assert "optimize" not in res
    assert called["n"] == 0


def test_op_screenshot_per_visual_requires_report_root(monkeypatch):
    monkeypatch.setattr(db, "op_status", lambda **k: _instance_with_page())
    monkeypatch.setattr(db, "run_bridge", lambda argv, **k: {"ok": True, "data": {"status": "ok"}})
    monkeypatch.setattr(db, "_optimize_output", lambda out: None)
    res = db.op_screenshot(page="Cover", pid=7, output="page.png", per_visual=True)
    assert res["success"] is True
    assert "report_root" in res["per_visual_error"]


def _write_pbir_page(root, page_id="p1", display="P", w=1280, h=720):
    page = root / "definition" / "pages" / page_id
    (page / "visuals").mkdir(parents=True)
    (page / "page.json").write_text(
        json.dumps({"name": page_id, "displayName": display, "width": w, "height": h}),
        encoding="utf-8",
    )


def _write_pbir_visual(
    root, vid, *, x, y, w=100, h=50, parent=None, hidden=False, group=False, page_id="p1"
):
    d = root / "definition" / "pages" / page_id / "visuals" / vid
    d.mkdir(parents=True)
    data = {"name": vid, "position": {"x": x, "y": y, "z": 0, "width": w, "height": h}}
    if group:
        data["visualGroup"] = {"displayName": vid, "groupMode": "ScaleMode"}
    else:
        data["visual"] = {"visualType": "cardVisual"}
    if parent:
        data["parentGroupName"] = parent
    if hidden:
        data["isHidden"] = True
    (d / "visual.json").write_text(json.dumps(data), encoding="utf-8")


def _patch_instance(monkeypatch, page_id="p1", display="P", **extra_inst):
    inst = {
        "pid": 7,
        "currentFilePath": "x.pbip",
        "pages": [{"id": page_id, "displayName": display}],
        **extra_inst,
    }
    monkeypatch.setattr(db, "op_status", lambda **k: {"success": True, "instances": [inst]})


def test_crop_resolves_group_absolute_coords(monkeypatch, tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image

    root = tmp_path / "R.Report"
    _write_pbir_page(root)
    # g1 at (100,300); c1 group-local (10,10) -> page-absolute (110,310).
    # g2 nested in g1 at local (50,50); c2 in g2 at local (5,5) -> abs (155,355).
    _write_pbir_visual(root, "g1", x=100, y=300, w=400, h=200, group=True)
    _write_pbir_visual(root, "c1", x=10, y=10, w=100, h=50, parent="g1")
    _write_pbir_visual(root, "g2", x=50, y=50, w=200, h=100, group=True, parent="g1")
    _write_pbir_visual(root, "c2", x=5, y=5, w=60, h=30, parent="g2")

    page_png = tmp_path / "page.png"
    img = Image.new("RGB", (1280, 720), color=(220, 220, 220))
    # Paint each visual's TRUE page-absolute rect a unique color.
    for (ax, ay, aw, ah), color in [
        ((110, 310, 100, 50), (255, 0, 0)),
        ((155, 355, 60, 30), (0, 0, 255)),
    ]:
        for px in range(ax, ax + aw):
            for py in range(ay, ay + ah):
                img.putpixel((px, py), color)
    img.save(page_png)

    _patch_instance(monkeypatch)
    monkeypatch.setattr(db, "run_bridge", lambda argv, **k: {"ok": True, "data": {}})
    res = db.op_screenshot(
        page="P",
        pid=7,
        output=str(page_png),
        per_visual=True,
        report_root=str(root),
        crops_dir=str(tmp_path / "crops"),
        optimize=False,
    )
    assert res["success"] is True
    crops = {c["visual_id"]: c["crop_path"] for c in res["visual_crops"]["crops"]}
    # Center pixel of each crop must be the color painted at the ABSOLUTE rect
    # (group-local coords would land on gray background).
    with Image.open(crops["c1"]) as c1:
        assert c1.getpixel((c1.size[0] // 2, c1.size[1] // 2)) == (255, 0, 0)
    with Image.open(crops["c2"]) as c2:
        assert c2.getpixel((c2.size[0] // 2, c2.size[1] // 2)) == (0, 0, 255)


def test_crop_skips_hidden_visuals_and_hidden_group_children(monkeypatch, tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image

    root = tmp_path / "R.Report"
    _write_pbir_page(root)
    _write_pbir_visual(root, "v1", x=900, y=30, w=100, h=100)
    _write_pbir_visual(root, "vh", x=600, y=20, w=80, h=40, hidden=True)
    _write_pbir_visual(root, "gh", x=0, y=0, w=200, h=200, group=True, hidden=True)
    _write_pbir_visual(root, "ch", x=10, y=10, w=50, h=50, parent="gh")

    page_png = tmp_path / "page.png"
    Image.new("RGB", (1280, 720), color=(220, 220, 220)).save(page_png)
    _patch_instance(monkeypatch)
    monkeypatch.setattr(db, "run_bridge", lambda argv, **k: {"ok": True, "data": {}})
    res = db.op_screenshot(
        page="P",
        pid=7,
        output=str(page_png),
        per_visual=True,
        report_root=str(root),
        crops_dir=str(tmp_path / "crops"),
        optimize=False,
    )
    crops = sorted(c["visual_id"] for c in res["visual_crops"]["crops"])
    assert crops == ["v1"]
    skipped = {s["visual_id"]: s["reason"] for s in res["visual_crops"]["skipped"]}
    assert "hidden" in skipped["vh"]
    assert "hidden" in skipped["ch"]


def test_crops_are_cut_before_page_optimize(monkeypatch, tmp_path):
    # The whole point of per_visual is legibility: crops must come from the
    # NATIVE-resolution capture, not from the already-downscaled page PNG.
    pytest.importorskip("PIL")
    from PIL import Image
    from pathlib import Path as _P

    root = tmp_path / "R.Report"
    _write_pbir_page(root)
    _write_pbir_visual(root, "v1", x=10, y=20, w=300, h=120)

    page_png = tmp_path / "page.png"
    Image.new("RGB", (1280, 720), color=(220, 220, 220)).save(page_png)

    def fake_opt(path):
        # Simulate the model-tier downscale on the PAGE png only.
        if _P(path).name == "page.png":
            with Image.open(path) as im:
                im.resize((640, 360)).save(path)
            return {"visual_tokens": 999}
        return {"visual_tokens": 1}

    _patch_instance(monkeypatch)
    monkeypatch.setattr(db, "run_bridge", lambda argv, **k: {"ok": True, "data": {}})
    monkeypatch.setattr(db, "_optimize_output", fake_opt)
    res = db.op_screenshot(
        page="P",
        pid=7,
        output=str(page_png),
        per_visual=True,
        report_root=str(root),
        crops_dir=str(tmp_path / "crops"),
        optimize=True,
    )
    assert res["success"] is True
    # Native-res crop: (300 + 2*8) x (120 + 2*8). If cropped after the page
    # was shrunk to 640x360, it would be ~166x76 instead.
    with Image.open(tmp_path / "crops" / "v1.png") as c:
        assert c.size == (316, 136)


def test_per_visual_derives_report_root_from_instance(monkeypatch, tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image

    root = tmp_path / "R.Report"
    _write_pbir_page(root)
    _write_pbir_visual(root, "v1", x=10, y=20, w=300, h=120)
    page_png = tmp_path / "page.png"
    Image.new("RGB", (1280, 720), color=(220, 220, 220)).save(page_png)

    # No report_root passed — must fall back to the instance's reportDir.
    _patch_instance(monkeypatch, reportDir=str(root))
    monkeypatch.setattr(db, "run_bridge", lambda argv, **k: {"ok": True, "data": {}})
    res = db.op_screenshot(
        page="P",
        pid=7,
        output=str(page_png),
        per_visual=True,
        crops_dir=str(tmp_path / "crops"),
        optimize=False,
    )
    assert res["success"] is True
    assert "per_visual_error" not in res
    assert [c["visual_id"] for c in res["visual_crops"]["crops"]] == ["v1"]


def test_op_screenshot_per_visual_crops_each_visual(monkeypatch, tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image
    from pathlib import Path

    # Real PBIR fixture: page aaa111 (1280x720) with two visuals (v001, v002).
    fixture = Path(__file__).parent / "fixtures" / "visual_catalog" / "report_mini"
    # Full-page PNG matching the canvas size so scale factor is 1:1.
    page_png = tmp_path / "page.png"
    Image.new("RGB", (1280, 720), color=(220, 220, 220)).save(page_png)

    monkeypatch.setattr(
        db, "op_status", lambda **k: _instance_with_page(page_id="aaa111", display="KPI Overview")
    )
    monkeypatch.setattr(db, "run_bridge", lambda argv, **k: {"ok": True, "data": {"status": "ok"}})
    crops_dir = tmp_path / "crops"
    res = db.op_screenshot(
        page="KPI Overview",
        pid=7,
        output=str(page_png),
        per_visual=True,
        report_root=str(fixture),
        crops_dir=str(crops_dir),
        optimize=False,  # keep crop sizes deterministic for assertions
    )
    assert res["success"] is True
    crops = res["visual_crops"]["crops"]
    ids = sorted(c["visual_id"] for c in crops)
    assert ids == ["v001", "v002"]
    # v001 bounds (x=10,y=20,w=300,h=120) -> crop exists on disk with padding.
    assert (crops_dir / "v001.png").exists()
    assert (crops_dir / "v002.png").exists()
    # v001 crop size = (300 + 2*8) x (120 + 2*8) at 1:1 scale.
    assert Image.open(crops_dir / "v001.png").size == (316, 136)
