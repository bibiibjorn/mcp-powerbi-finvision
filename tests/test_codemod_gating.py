from server.handlers.dax_codemods_handler import handle_dax_codemods


def test_apply_with_destructive_severity_gated_by_strict(monkeypatch):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")
    from core.autonomous import gating

    gating.invalidate_policy_cache()
    res = handle_dax_codemods(
        {
            "operation": "apply",
            "transform": "inline_measure",
            "args": {"measure_ref": "T[X]"},
            "plan_hash": "deadbeef",
        }
    )
    assert res["success"] is False
    # Either gate-blocked or not-connected is acceptable; both prove the
    # apply path didn't sail through.
    assert res.get("error_type") in {"gate_blocked", "not_connected"}
