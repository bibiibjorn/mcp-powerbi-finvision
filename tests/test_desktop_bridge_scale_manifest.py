"""Tests for the v0.1.1 CLI surface additions on 17_Desktop_Bridge:
``--scale`` / ``--settle`` passthrough and the new ``manifest`` capability-probe
op (+ ``bridge_supports`` and its per-PID cache).

Mocks ``run_bridge`` / the core ops at the module boundary; never invokes the
real powerbi-desktop CLI or a live Desktop.
"""

import pytest

from core.autonomous import desktop_bridge as db
from server.handlers import desktop_bridge_handler as h


def _instance(pid=7, page_id="pid_abc", display="Cover"):
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


@pytest.fixture
def capture_argv(monkeypatch):
    """Patch run_bridge to record argv + count calls; returns the record."""
    rec = {"argv": [], "calls": 0}

    def fake_run(argv, **k):
        rec["calls"] += 1
        rec["argv"].append(list(argv))
        return {"ok": True, "data": {"status": "ok"}}

    monkeypatch.setattr(db, "run_bridge", fake_run)
    monkeypatch.setattr(db, "_optimize_output", lambda out: None)
    return rec


# ---------------------------------------------------------------------------
# Task 1 — --scale passthrough (screenshot)
# ---------------------------------------------------------------------------
def test_screenshot_appends_scale(monkeypatch, capture_argv):
    monkeypatch.setattr(db, "op_status", lambda **k: _instance())
    out = db.op_screenshot(page="Cover", pid=7, output="o.png", scale=3)
    assert out["success"] is True
    assert capture_argv["argv"][0] == [
        "screenshot",
        "pid_abc",
        "--pid",
        "7",
        "--output",
        "o.png",
        "--scale",
        "3",
    ]
    assert out["scale"] == 3


def test_screenshot_omits_scale_when_none(monkeypatch, capture_argv):
    monkeypatch.setattr(db, "op_status", lambda **k: _instance())
    out = db.op_screenshot(page="Cover", pid=7, output="o.png")
    assert "--scale" not in capture_argv["argv"][0]
    assert "scale" not in out


def test_screenshot_scale_float_kept_verbatim(monkeypatch, capture_argv):
    # A fractional scale is legal (CLI takes a number); 1.5 must not become "1".
    monkeypatch.setattr(db, "op_status", lambda **k: _instance())
    db.op_screenshot(page="Cover", pid=7, output="o.png", scale=1.5)
    argv = capture_argv["argv"][0]
    assert argv[argv.index("--scale") + 1] == "1.5"


def test_screenshot_integral_float_scale_is_not_dotted(monkeypatch, capture_argv):
    monkeypatch.setattr(db, "op_status", lambda **k: _instance())
    db.op_screenshot(page="Cover", pid=7, output="o.png", scale=2.0)
    argv = capture_argv["argv"][0]
    assert argv[argv.index("--scale") + 1] == "2"


@pytest.mark.parametrize("bad", [0, 0.5, 3.5, 4, -1, "abc", "", [2], float("nan")])
def test_screenshot_invalid_scale_rejected_without_cli_call(monkeypatch, capture_argv, bad):
    monkeypatch.setattr(db, "op_status", lambda **k: _instance())
    out = db.op_screenshot(page="Cover", pid=7, output="o.png", scale=bad)
    assert out["success"] is False
    assert out["error_type"] == "invalid_argument"
    # The accepted range must be named so the caller can self-correct.
    assert "1" in out["error"] and "3" in out["error"]
    assert capture_argv["calls"] == 0


def test_invalid_scale_message_does_not_clamp(monkeypatch, capture_argv):
    monkeypatch.setattr(db, "op_status", lambda **k: _instance())
    out = db.op_screenshot(page="Cover", pid=7, output="o.png", scale=9)
    assert out["success"] is False
    assert capture_argv["calls"] == 0


# ---------------------------------------------------------------------------
# Task 1 — --scale passthrough (screenshot_all + reload_and_screenshot)
# ---------------------------------------------------------------------------
def test_screenshot_all_appends_scale(monkeypatch, capture_argv):
    monkeypatch.setattr(db, "op_status", lambda **k: _instance())
    out = db.op_screenshot_all(output_dir="d", pid=7, scale=3, optimize=False)
    assert out["success"] is True
    assert capture_argv["argv"][0] == [
        "screenshot-all",
        "--pid",
        "7",
        "--output-dir",
        "d",
        "--scale",
        "3",
    ]


def test_screenshot_all_omits_scale_when_none(monkeypatch, capture_argv):
    monkeypatch.setattr(db, "op_status", lambda **k: _instance())
    db.op_screenshot_all(output_dir="d", pid=7, optimize=False)
    assert "--scale" not in capture_argv["argv"][0]


def test_screenshot_all_invalid_scale_rejected(monkeypatch, capture_argv):
    monkeypatch.setattr(db, "op_status", lambda **k: _instance())
    out = db.op_screenshot_all(output_dir="d", pid=7, scale=0)
    assert out["error_type"] == "invalid_argument"
    assert capture_argv["calls"] == 0


def test_reload_and_screenshot_passes_scale_through(monkeypatch):
    seen = {}
    monkeypatch.setattr(db, "op_reload", lambda **k: {"success": True, "pid": 7, "data": {}})
    monkeypatch.setattr(
        db,
        "op_screenshot",
        lambda **k: seen.update(k) or {"success": True, "output": k["output"]},
    )
    db.op_reload_and_screenshot(page="P", output="o.png", pid=7, scale=3)
    assert seen["scale"] == 3


def test_reload_and_screenshot_rejects_bad_scale_before_reloading(monkeypatch):
    # A bad scale must not reload Desktop first — validate then act.
    reloaded = []
    monkeypatch.setattr(db, "op_reload", lambda **k: reloaded.append(1))
    out = db.op_reload_and_screenshot(page="P", output="o.png", pid=7, scale=7)
    assert out["success"] is False
    assert out["error_type"] == "invalid_argument"
    assert reloaded == []


# ---------------------------------------------------------------------------
# Task 2 — --settle passthrough (screenshot_all only)
# ---------------------------------------------------------------------------
def test_screenshot_all_appends_settle(monkeypatch, capture_argv):
    monkeypatch.setattr(db, "op_status", lambda **k: _instance())
    out = db.op_screenshot_all(output_dir="d", pid=7, settle_ms=1500, optimize=False)
    assert out["success"] is True
    assert capture_argv["argv"][0] == [
        "screenshot-all",
        "--pid",
        "7",
        "--output-dir",
        "d",
        "--settle",
        "1500",
    ]
    assert out["settle_ms"] == 1500


def test_screenshot_all_scale_and_settle_together(monkeypatch, capture_argv):
    monkeypatch.setattr(db, "op_status", lambda **k: _instance())
    db.op_screenshot_all(output_dir="d", pid=7, scale=2, settle_ms=0, optimize=False)
    argv = capture_argv["argv"][0]
    assert argv[argv.index("--scale") + 1] == "2"
    # settle_ms=0 is a VALID explicit value and must not be dropped as falsy.
    assert argv[argv.index("--settle") + 1] == "0"


def test_screenshot_all_omits_settle_when_none(monkeypatch, capture_argv):
    monkeypatch.setattr(db, "op_status", lambda **k: _instance())
    db.op_screenshot_all(output_dir="d", pid=7, optimize=False)
    assert "--settle" not in capture_argv["argv"][0]


@pytest.mark.parametrize("bad", [-1, -500, "abc"])
def test_screenshot_all_invalid_settle_rejected(monkeypatch, capture_argv, bad):
    monkeypatch.setattr(db, "op_status", lambda **k: _instance())
    out = db.op_screenshot_all(output_dir="d", pid=7, settle_ms=bad)
    assert out["success"] is False
    assert out["error_type"] == "invalid_argument"
    assert capture_argv["calls"] == 0


# ---------------------------------------------------------------------------
# Task 3 — op_manifest + method extraction
# ---------------------------------------------------------------------------
_FAKE_MANIFEST = {
    "version": "1.0",
    "capabilities": [
        {"name": "report.reload", "description": "reload"},
        {"name": "report.screenshot"},
        # duplicate must be deduped
        {"method": "report.reload"},
        {"nested": {"methods": [{"name": "model.info"}]}},
    ],
}


def test_op_manifest_success_extracts_methods(monkeypatch, capture_argv):
    monkeypatch.setattr(
        db,
        "run_bridge",
        lambda argv, **k: capture_argv["argv"].append(list(argv))
        or {"ok": True, "data": _FAKE_MANIFEST},
    )
    out = db.op_manifest(pid=7)
    assert out["success"] is True
    assert out["pid"] == 7
    assert out["data"] == _FAKE_MANIFEST
    assert out["methods"] == ["model.info", "report.reload", "report.screenshot"]
    assert "methods_unavailable" not in out
    assert capture_argv["argv"][-1] == ["manifest", "--pid", "7"]


def test_op_manifest_explicit_pid_skips_status(monkeypatch):
    calls = {"status": 0, "manifest": 0}

    def fake_run(argv, **k):
        calls[argv[0]] = calls.get(argv[0], 0) + 1
        return {"ok": True, "data": _FAKE_MANIFEST}

    monkeypatch.setattr(db, "run_bridge", fake_run)
    db.op_manifest(pid=7)
    assert calls["status"] == 0
    assert calls["manifest"] == 1


def test_op_manifest_resolves_pid_from_status(monkeypatch):
    monkeypatch.setattr(db, "op_status", lambda **k: _instance(pid=42))
    monkeypatch.setattr(db, "run_bridge", lambda argv, **k: {"ok": True, "data": _FAKE_MANIFEST})
    out = db.op_manifest()
    assert out["pid"] == 42


def test_op_manifest_appends_wait_seconds(monkeypatch, capture_argv):
    monkeypatch.setattr(
        db,
        "run_bridge",
        lambda argv, **k: capture_argv["argv"].append(list(argv)) or {"ok": True, "data": {}},
    )
    db.op_manifest(pid=7, wait_seconds=10)
    assert capture_argv["argv"][-1] == ["manifest", "--pid", "7", "--wait-seconds", "10"]


def test_op_manifest_unrecognizable_payload_flags_unavailable(monkeypatch):
    # No name/method keys and no plain-string list entries -> UNKNOWN, not "none".
    monkeypatch.setattr(
        db, "run_bridge", lambda argv, **k: {"ok": True, "data": {"schemaVersion": 3, "x": 1}}
    )
    out = db.op_manifest(pid=7)
    assert out["success"] is True
    assert out["methods_unavailable"] is True
    assert "methods" not in out


def test_op_manifest_cli_failure_propagates(monkeypatch):
    monkeypatch.setattr(
        db,
        "run_bridge",
        lambda argv, **k: {"ok": False, "error_type": "cli_error", "error": "exited 1"},
    )
    out = db.op_manifest(pid=7)
    assert out["success"] is False
    assert out["error_type"] == "cli_error"


def test_extract_manifest_methods_handles_plain_string_list():
    assert db.extract_manifest_methods(["b.m", "a.m", "b.m"]) == ["a.m", "b.m"]


def test_extract_manifest_methods_empty_on_scalar():
    assert db.extract_manifest_methods(42) == []
    assert db.extract_manifest_methods({}) == []


# ---------------------------------------------------------------------------
# Task 3 — bridge_supports + per-PID manifest cache
# ---------------------------------------------------------------------------
def test_bridge_supports_true_and_false(monkeypatch):
    monkeypatch.setattr(db, "run_bridge", lambda argv, **k: {"ok": True, "data": _FAKE_MANIFEST})
    assert db.bridge_supports("report.reload", pid=7) is True
    db.invalidate_manifest_cache()
    assert db.bridge_supports("report.doesNotExist", pid=7) is False


def test_bridge_supports_unknown_when_methods_undiscoverable(monkeypatch):
    monkeypatch.setattr(db, "run_bridge", lambda argv, **k: {"ok": True, "data": {"v": 1}})
    assert db.bridge_supports("report.reload", pid=7) is None


def test_bridge_supports_unknown_on_failure(monkeypatch):
    monkeypatch.setattr(
        db,
        "run_bridge",
        lambda argv, **k: {"ok": False, "error_type": "not_connected", "error": "x"},
    )
    assert db.bridge_supports("report.reload", pid=7) is None


def test_manifest_cache_prevents_second_cli_call(monkeypatch):
    calls = {"n": 0}

    def fake_run(argv, **k):
        calls["n"] += 1
        return {"ok": True, "data": _FAKE_MANIFEST}

    monkeypatch.setattr(db, "run_bridge", fake_run)
    assert db.bridge_supports("report.reload", pid=7) is True
    assert db.bridge_supports("report.screenshot", pid=7) is True
    assert db.bridge_supports("nope", pid=7) is False
    assert calls["n"] == 1


def test_manifest_cache_is_per_pid(monkeypatch):
    calls = {"n": 0}

    def fake_run(argv, **k):
        calls["n"] += 1
        return {"ok": True, "data": _FAKE_MANIFEST}

    monkeypatch.setattr(db, "run_bridge", fake_run)
    db.bridge_supports("report.reload", pid=7)
    db.bridge_supports("report.reload", pid=8)
    assert calls["n"] == 2


def test_invalidate_manifest_cache_forces_refetch(monkeypatch):
    calls = {"n": 0}

    def fake_run(argv, **k):
        calls["n"] += 1
        return {"ok": True, "data": _FAKE_MANIFEST}

    monkeypatch.setattr(db, "run_bridge", fake_run)
    db.bridge_supports("report.reload", pid=7)
    db.invalidate_manifest_cache()
    db.bridge_supports("report.reload", pid=7)
    assert calls["n"] == 2


def test_failed_manifest_is_not_cached(monkeypatch):
    calls = {"n": 0}

    def fake_run(argv, **k):
        calls["n"] += 1
        return {"ok": False, "error_type": "timeout", "error": "x"}

    monkeypatch.setattr(db, "run_bridge", fake_run)
    db.bridge_supports("report.reload", pid=7)
    db.bridge_supports("report.reload", pid=7)
    assert calls["n"] == 2


# ---------------------------------------------------------------------------
# Task 4 — min-Desktop-version hint on the not_connected path
# ---------------------------------------------------------------------------
def test_not_connected_carries_min_desktop_version(monkeypatch):
    monkeypatch.setattr(db, "op_status", lambda **k: {"success": True, "instances": []})
    out = db.op_save()
    assert out["error_type"] == "not_connected"  # unchanged — callers match on it
    assert out["min_desktop_version"] == "2.155.756.0"
    assert "external tool access through secure local APIs" in out["min_desktop_hint"]
    assert "2.155.756.0" in out["error"]


def test_not_connected_for_unknown_pid_carries_hint():
    with pytest.raises(db.BridgeNotConnected) as exc:
        db.select_instance({"instances": [{"pid": 1}]}, pid=99, pbip_path=None)
    assert exc.value.extra["min_desktop_version"] == db.MIN_DESKTOP_VERSION


def test_ambiguous_error_does_not_get_version_hint():
    # Only not_connected is version-ambiguous; ambiguity is a caller problem.
    with pytest.raises(db.BridgeAmbiguous) as exc:
        db.select_instance(
            {"instances": [{"pid": 1, "currentFilePath": "a"}, {"pid": 2, "currentFilePath": "b"}]},
            pid=None,
            pbip_path=None,
        )
    assert "min_desktop_version" not in exc.value.extra


# ---------------------------------------------------------------------------
# Handler routing
# ---------------------------------------------------------------------------
def test_handler_routes_manifest(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        h.desktop_bridge,
        "op_manifest",
        lambda **k: seen.update(k) or {"success": True, "pid": k.get("pid")},
    )
    out = h.handle_desktop_bridge({"operation": "manifest", "pid": 7})
    assert out["success"] is True
    assert seen["pid"] == 7
    # manifest defaults to the CLI's own wait_seconds=0, not the 30s status default.
    assert seen["wait_seconds"] == 0.0


def test_manifest_is_a_valid_op():
    assert "manifest" in h._VALID_OPS


def test_handler_rejects_unknown_op():
    out = h.handle_desktop_bridge({"operation": "manifesto"})
    assert out["success"] is False
    assert out["error_type"] == "invalid_operation"
    assert "manifest" in out["error"]


def test_handler_passes_scale_to_screenshot(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        h.desktop_bridge, "op_screenshot", lambda **k: seen.update(k) or {"success": True}
    )
    h.handle_desktop_bridge(
        {"operation": "screenshot", "page": "P", "output": "o.png", "scale": "3"}
    )
    assert seen["scale"] == 3.0


def test_handler_scale_absent_is_none(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        h.desktop_bridge, "op_screenshot", lambda **k: seen.update(k) or {"success": True}
    )
    h.handle_desktop_bridge({"operation": "screenshot", "page": "P", "output": "o.png"})
    assert seen["scale"] is None


def test_handler_passes_junk_scale_through_to_validator(monkeypatch):
    # Non-numeric input must reach the core validator (which names the range),
    # NOT be silently coerced to None / ignored.
    seen = {}
    monkeypatch.setattr(
        h.desktop_bridge, "op_screenshot", lambda **k: seen.update(k) or {"success": True}
    )
    h.handle_desktop_bridge(
        {"operation": "screenshot", "page": "P", "output": "o.png", "scale": "abc"}
    )
    assert seen["scale"] == "abc"


def test_handler_passes_scale_and_settle_to_screenshot_all(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        h.desktop_bridge, "op_screenshot_all", lambda **k: seen.update(k) or {"success": True}
    )
    h.handle_desktop_bridge(
        {"operation": "screenshot_all", "output_dir": "d", "scale": 2, "settle_ms": "750"}
    )
    assert seen["scale"] == 2.0
    assert seen["settle_ms"] == 750


def test_handler_passes_scale_to_reload_and_screenshot(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        h.desktop_bridge,
        "op_reload_and_screenshot",
        lambda **k: seen.update(k) or {"success": True},
    )
    h.handle_desktop_bridge(
        {"operation": "reload_and_screenshot", "page": "P", "output": "o.png", "scale": 3}
    )
    assert seen["scale"] == 3.0


def test_schema_documents_scale_range():
    from server.registry import HandlerRegistry

    reg = HandlerRegistry()
    h.register_desktop_bridge_handler(reg)
    props = reg._handlers["17_Desktop_Bridge"].input_schema["properties"]
    assert props["scale"]["minimum"] == 1
    assert props["scale"]["maximum"] == 3
    assert "manifest" in props["operation"]["enum"]
    assert props["settle_ms"]["minimum"] == 0
