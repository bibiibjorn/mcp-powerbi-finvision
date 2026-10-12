"""Tier-2 LLM-in-the-loop semantic fix path for the debug loop.

For a SEMANTIC anomaly (blank / wrong value that a mechanical optimizer can't
invent a fix for), the live_tom apply asks the MCP client's LLM (via sampling)
for a DAX rewrite, validates it, and applies it under a revert guard. If no
sampler is available (no client session / declined), it falls back to the
previous deferred marker — so behaviour is unchanged without a session.

The sampler and the applier are injectable so these tests need no MCP session
and no CLR.
"""

from __future__ import annotations


from core.autonomous.loops import debug_loop as dl
from core.autonomous.loops.debug_loop import DebugLoopConfig


def _cfg(**kw):
    base = dict(
        pbip_path="C:/x.pbip",
        page="P",
        visual="v1",
        measure="Sales[Total]",
        fix_strategy="live_tom",
    )
    base.update(kw)
    return DebugLoopConfig(**base)


def _proposal(kind="blank"):
    return {"anomaly_kind": kind, "measure": "Sales[Total]", "summary": "blank value"}


def test_semantic_fix_calls_sampler_and_applies(monkeypatch):
    calls = {}

    def _sampler(prompt, **kw):
        calls["prompt"] = prompt
        return "```dax\nSUM ( Sales[Amount] )\n```"

    def _validate(dax):
        calls["validated"] = dax
        return []  # no errors

    def _apply(*, measure_name, table_name, new_dax, gate_args=None):
        calls["gate_args"] = gate_args
        calls["applied"] = new_dax
        return {"success": True}

    out = dl._default_semantic_llm_apply(
        _proposal(),
        _cfg(),
        None,
        sampler=_sampler,
        validate_fn=_validate,
        apply_fn=_apply,
    )
    assert out["success"] is True
    assert out["applied"] is True
    assert out["fix_source"] == "llm"
    # Markdown fences stripped before validation/apply.
    assert calls["applied"].strip() == "SUM ( Sales[Amount] )"
    assert "blank" in calls["prompt"].lower() or "Sales[Total]" in calls["prompt"]


def test_semantic_fix_defers_when_no_sampler(monkeypatch):
    # sampler returns None (no MCP session / client declined).
    out = dl._default_semantic_llm_apply(
        _proposal(),
        _cfg(),
        None,
        sampler=lambda prompt, **kw: None,
        validate_fn=lambda dax: [],
        apply_fn=lambda **kw: {"success": True},
    )
    assert out.get("status") == "deferred"
    assert out.get("applied") is False
    assert "sampl" in out["reason"].lower() or "llm" in out["reason"].lower()


def test_semantic_fix_rejects_invalid_dax(monkeypatch):
    applied = {"ran": False}

    def _apply(**kw):
        applied["ran"] = True
        return {"success": True}

    out = dl._default_semantic_llm_apply(
        _proposal(),
        _cfg(),
        None,
        sampler=lambda prompt, **kw: "THIS IS NOT ( VALID DAX",
        validate_fn=lambda dax: ["unbalanced parens"],
        apply_fn=_apply,
    )
    assert out.get("status") == "deferred" or out.get("success") is False
    assert applied["ran"] is False  # never applied invalid DAX
    assert "valid" in (out.get("reason", "") + out.get("error", "")).lower()


def test_semantic_fix_apply_failure_surfaces(monkeypatch):
    out = dl._default_semantic_llm_apply(
        _proposal(),
        _cfg(),
        None,
        sampler=lambda prompt, **kw: "SUM ( Sales[Amount] )",
        validate_fn=lambda dax: [],
        apply_fn=lambda **kw: {"success": False, "error": "TOM rejected it"},
    )
    assert out["success"] is False
    assert "TOM rejected" in out["error"]


def test_default_live_tom_routes_semantic_to_llm_when_opted_in(monkeypatch):
    # For a non-fe_dominated kind, _default_live_tom_apply should delegate to
    # the semantic LLM path ONLY when allow_llm_fix is set.
    routed = {}

    def _fake_semantic(proposal, cfg, paths, **kw):
        routed["kind"] = proposal.get("anomaly_kind")
        return {"success": True, "applied": True, "fix_source": "llm"}

    monkeypatch.setattr(dl, "_default_semantic_llm_apply", _fake_semantic)
    out = dl._default_live_tom_apply(_proposal("blank"), _cfg(allow_llm_fix=True), None)
    assert out["success"] is True
    assert routed["kind"] == "blank"


def test_default_live_tom_defers_semantic_without_opt_in(monkeypatch):
    # Without allow_llm_fix, a semantic anomaly must defer (never touch the LLM
    # path) with a clear reason mentioning the opt-in flag.
    monkeypatch.setattr(
        dl,
        "_default_semantic_llm_apply",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")),
    )
    out = dl._default_live_tom_apply(_proposal("blank"), _cfg(allow_llm_fix=False), None)
    assert out.get("status") == "deferred"
    assert out.get("applied") is False
    assert "allow_llm_fix" in out["reason"]


def test_default_live_tom_still_optimizes_fe_dominated(monkeypatch):
    # fe_dominated must NOT go through the LLM path — it stays deterministic.
    monkeypatch.setattr(
        dl,
        "_default_semantic_llm_apply",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")),
    )

    # Stub out the optimizer imports so we don't need CLR — the function tries
    # to import the optimizer; make it return a deferred marker cleanly.
    import sys
    import types

    fake_pipeline_mod = types.ModuleType("core.dax.optimizer.pipeline")

    class _Pipe:
        def __init__(self, cs):
            pass

        def optimize_measure(self, measure_name, table_name, dry_run=True):
            return None  # no rewrite -> deferred, but NOT via the LLM path

    fake_pipeline_mod.OptimizationPipeline = _Pipe
    monkeypatch.setitem(sys.modules, "core.dax.optimizer.pipeline", fake_pipeline_mod)

    # measure="Sales[Total]" resolves table without a model lookup.
    out = dl._default_live_tom_apply(_proposal("fe_dominated"), _cfg(), None)
    # Either deferred (no rewrite) or success — but crucially not an
    # AssertionError from the semantic path.
    assert out.get("status") == "deferred" or out.get("success") is not None
