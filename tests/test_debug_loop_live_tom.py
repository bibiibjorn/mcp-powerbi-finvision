"""P3b: debug_loop live_tom must actually apply a fix (not a permanent stub),
guarded by a baseline, and must retry a transient debug failure before aborting.

Previously fix_strategy='live_tom' returned {status:'deferred'} identical to
dry_run, so the loop exited after one iteration and max_iterations was dead
code. It now runs an (injectable) apply function that optimizes + persists the
measure wrapped in a capture/compare baseline guard.
"""

from core.autonomous.loops.debug_loop import DebugLoopConfig, _apply_fix


def _cfg(**kw):
    base = dict(pbip_path="x", page="P", visual="v", measure="Total Sales", fix_strategy="live_tom")
    base.update(kw)
    return DebugLoopConfig(**base)


def test_live_tom_applies_via_injected_fn():
    calls = {}

    def fake_apply(proposal, cfg, paths):
        calls["proposal"] = proposal
        return {
            "success": True,
            "applied": True,
            "requires_reload": False,
            "new_dax": "SUM(Sales[Amt])",
        }

    cfg = _cfg(live_tom_apply_fn=fake_apply)
    out = _apply_fix({"measure": "Total Sales", "anomaly_kind": "fe_dominated"}, cfg, paths=None)
    assert out["success"] is True
    assert out["applied"] is True
    assert calls["proposal"]["measure"] == "Total Sales"


def test_live_tom_still_deferred_without_apply_fn_or_optimizer(monkeypatch):
    # With no injected fn and the optimizer unavailable, it must degrade to a
    # clear deferred marker (not crash), so callers still get a usable status.
    import core.autonomous.loops.debug_loop as dl

    monkeypatch.setattr(
        dl,
        "_default_live_tom_apply",
        lambda proposal, cfg, paths: {
            "status": "deferred",
            "reason": "optimizer produced no rewrite",
            "applied": False,
        },
    )
    cfg = _cfg()
    out = _apply_fix({"measure": "Total Sales", "anomaly_kind": "blank"}, cfg, paths=None)
    assert out.get("applied") is False
    assert out.get("status") == "deferred"


def test_live_tom_apply_failure_is_reported():
    def failing_apply(proposal, cfg, paths):
        return {"success": False, "error": "optimizer rewrite broke a baseline"}

    cfg = _cfg(live_tom_apply_fn=failing_apply)
    out = _apply_fix({"measure": "X", "anomaly_kind": "fe_dominated"}, cfg, paths=None)
    assert out["success"] is False
    assert "baseline" in out["error"]


# ---------------------------------------------------------------------------
# Transient debug-error retry classification
# ---------------------------------------------------------------------------
def test_transient_error_classification():
    from core.autonomous.loops.debug_loop import _is_transient_debug_error

    assert _is_transient_debug_error({"error_type": "connection_lost"}) is True
    assert _is_transient_debug_error({"error": "RPC transport closed"}) is True
    assert (
        _is_transient_debug_error(
            {"error": "Exception has been thrown by the target of an invocation"}
        )
        is True
    )
    # Permanent errors must NOT be retried.
    assert _is_transient_debug_error({"error_type": "invalid_args"}) is False
    assert _is_transient_debug_error({"error": "measure 'X' not found"}) is False
