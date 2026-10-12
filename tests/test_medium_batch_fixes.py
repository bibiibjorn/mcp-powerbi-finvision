"""Medium-severity batch:
- gating policy must be re-readable mid-session (no permanent cache)
- dispatch security flags must be re-readable mid-session
- code_rewriter legacy fallback must not emit invalid SUM([Measure]) DAX
"""


# ---------------------------------------------------------------------------
# gating: tightening the policy mid-session must take effect
# ---------------------------------------------------------------------------
def test_gating_policy_reflects_env_change_mid_session(monkeypatch):
    from core.autonomous import gating

    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "OFF")
    gating.invalidate_policy_cache()
    assert gating.current_policy() == "OFF"

    # Operator tightens to STRICT mid-session — must take effect without a
    # process restart or a manual cache invalidation.
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")
    assert gating.current_policy() == "STRICT"


# ---------------------------------------------------------------------------
# dispatch: refresh_security_flags picks up config changes
# ---------------------------------------------------------------------------
def test_dispatch_refresh_security_flags(monkeypatch):
    from server.dispatch import ToolDispatcher
    import server.dispatch as dmod

    d = ToolDispatcher()
    calls = {"read_only": False}
    monkeypatch.setattr(
        dmod.config,
        "get",
        lambda k, default=None: calls.get(
            {"security.read_only_mode": "read_only", "security.confirm_destructive": "cd"}.get(
                k, ""
            ),
            default,
        ),
    )
    calls["read_only"] = True
    d.refresh_security_flags()
    assert d._read_only is True


# ---------------------------------------------------------------------------
# code_rewriter: legacy fallback must not emit invalid measure-as-column DAX
# ---------------------------------------------------------------------------
def test_iterator_rewrite_does_not_wrap_bare_measure_in_sum():
    from core.dax.code_rewriter import DaxCodeRewriter

    rw = DaxCodeRewriter()
    # SUMX(FILTER(Sales, ...), [Total]) — [Total] is a MEASURE ref; SUM([Total])
    # is invalid DAX. The rewrite must NOT produce SUM([...]) / AVERAGE([...]).
    # (E-21: the legacy regex fallback is gone; rewrite_dax is engine-only.)
    dax = 'SUMX(FILTER(Sales, Sales[Region] = "US"), [Total])'
    out = rw.rewrite_dax(dax).get("rewritten_code") or dax
    assert "SUM([Total])" not in out
    assert "AVERAGE([Total])" not in out
