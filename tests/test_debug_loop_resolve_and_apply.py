"""Regression tests for the debug_loop live_tom auto-apply fixes.

Covers two confirmed review findings:

FIX 1 — live_tom auto-apply could never persist:
  (a) pipeline.optimize_measure was called with the wrong arity (one arg) —
      it requires (measure_name, table_name[, dry_run]).
  (b) both apply paths read a non-existent cfg.table then applied with
      table_name='' — which DAXInjector rejects via _valid_amo_name.
  The shared _resolve_measure_table helper now parses 'Table[Name]' or resolves
  a bare name from the live model, and the paths defer (never empty-string
  apply) when the table can't be resolved.

FIX 2 — LLM auto-apply opt-in + original_dax capture:
  semantic anomalies defer unless allow_llm_fix=True, and every applied result
  records the pre-edit expression as original_dax.
"""

from __future__ import annotations

from core.autonomous.loops import debug_loop as dl
from core.autonomous.loops.debug_loop import DebugLoopConfig


def _cfg(**kw):
    base = dict(pbip_path="C:/x.pbip", page="P", visual="v1", fix_strategy="live_tom")
    base.update(kw)
    return DebugLoopConfig(**base)


# ---------------------------------------------------------------------------
# FIX 1a — 'Table[Name]' parsing
# ---------------------------------------------------------------------------


def test_resolve_qualified_ref_parses_table_and_measure():
    assert dl._resolve_measure_table("Sales[Total]") == ("Sales", "Total")


def test_resolve_quoted_table_ref():
    # "'Sales Fact'[Total Revenue]" — single-quoted table with spaces.
    assert dl._resolve_measure_table("'Sales Fact'[Total Revenue]") == (
        "Sales Fact",
        "Total Revenue",
    )


def test_resolve_empty_ref_returns_none():
    assert dl._resolve_measure_table("") == (None, "")
    assert dl._resolve_measure_table(None) == (None, "")


# ---------------------------------------------------------------------------
# FIX 1b — bare-name resolution via injected model lookup
# ---------------------------------------------------------------------------


def test_resolve_bare_name_via_injected_lookup(monkeypatch):
    monkeypatch.setattr(
        dl,
        "_load_model_measures",
        lambda: [
            {"Name": "Other", "Table": "Dim", "Expression": "1"},
            {"Name": "Total Sales", "Table": "Sales", "Expression": "SUM(Sales[Amt])"},
        ],
    )
    assert dl._resolve_measure_table("Total Sales") == ("Sales", "Total Sales")


def test_resolve_bare_name_unresolvable_returns_none_table(monkeypatch):
    monkeypatch.setattr(dl, "_load_model_measures", lambda: [])
    assert dl._resolve_measure_table("Ghost Measure") == (None, "Ghost Measure")


# ---------------------------------------------------------------------------
# FIX 1b — unresolvable table → deferred (NEVER empty-string apply)
# ---------------------------------------------------------------------------


def test_live_tom_fe_dominated_unresolvable_table_defers(monkeypatch):
    # Bare measure name, no model match → deferred; the applier/pipeline must
    # never be reached with an empty table name.
    monkeypatch.setattr(dl, "_load_model_measures", lambda: [])

    def _boom(*a, **k):
        raise AssertionError("must not apply/optimize with an unresolved table")

    monkeypatch.setattr(dl, "_current_measure_dax", _boom)

    out = dl._default_live_tom_apply(
        {"measure": "Ghost", "anomaly_kind": "fe_dominated"}, _cfg(), None
    )
    assert out.get("status") == "deferred"
    assert out.get("applied") is False
    assert "resolve owning table" in out["reason"]


def test_semantic_unresolvable_table_defers(monkeypatch):
    monkeypatch.setattr(dl, "_load_model_measures", lambda: [])
    applied = {"ran": False}

    def _apply(**kw):
        applied["ran"] = True
        return {"success": True}

    out = dl._default_semantic_llm_apply(
        {"measure": "Ghost", "anomaly_kind": "blank", "current_dax": "BLANK()"},
        _cfg(allow_llm_fix=True),
        None,
        sampler=lambda prompt, **kw: "SUM ( Sales[Amt] )",
        validate_fn=lambda dax: [],
        apply_fn=_apply,
    )
    assert out.get("status") == "deferred"
    assert applied["ran"] is False
    assert "resolve owning table" in out["reason"]


# ---------------------------------------------------------------------------
# FIX 1a — optimize_measure called with BOTH args
# ---------------------------------------------------------------------------


def test_optimize_measure_called_with_measure_and_table(monkeypatch):
    captured = {}

    class _Opt:
        final_dax = "SUM ( Sales[Amt] )"

    class _Pipe:
        def __init__(self, cs):
            pass

        def optimize_measure(self, measure_name, table_name, dry_run=True):
            captured["args"] = (measure_name, table_name, dry_run)
            return _Opt()

    class _Applier:
        def __init__(self, cs):
            pass

        def apply(self, *, measure_name, table_name, new_dax, gate_args=None):
            captured["apply"] = (measure_name, table_name, new_dax)
            captured["gate_args"] = gate_args
            return {"success": True}

    import sys
    import types

    fake_pipeline_mod = types.ModuleType("core.dax.optimizer.pipeline")
    fake_pipeline_mod.OptimizationPipeline = _Pipe
    fake_applier_mod = types.ModuleType("core.dax.optimizer.measure_applier")
    fake_applier_mod.MeasureApplier = _Applier
    monkeypatch.setitem(sys.modules, "core.dax.optimizer.pipeline", fake_pipeline_mod)
    monkeypatch.setitem(sys.modules, "core.dax.optimizer.measure_applier", fake_applier_mod)
    monkeypatch.setattr(dl, "_current_measure_dax", lambda t, m: "OLD DAX")
    # The semantic-equivalence guard gates the write; without a live model it
    # fails closed, so stub it equivalent to exercise the apply path here.
    # (Its own behaviour is covered by tests/test_debug_compare_equivalence.py.)
    monkeypatch.setattr(
        dl,
        "_check_rewrite_equivalence",
        lambda measure, dax, **kw: {"equivalent": True, "reason": "equivalent"},
    )

    out = dl._default_live_tom_apply(
        {"measure": "Sales[Total]", "anomaly_kind": "fe_dominated"}, _cfg(), None
    )
    assert out["success"] is True
    # Both args passed, compute-only (dry_run=True): MeasureApplier is the single
    # write path — dry_run=False would make the pipeline write the same DAX twice.
    assert captured["args"] == ("Total", "Sales", True)
    assert captured["apply"] == ("Total", "Sales", "SUM ( Sales[Amt] )")
    # FIX 2b — original_dax captured on the applied result.
    assert out["original_dax"] == "OLD DAX"
    assert "original_dax" in out["revert_hint"]


# ---------------------------------------------------------------------------
# FIX 2a — semantic anomaly opt-in gate
# ---------------------------------------------------------------------------


def test_semantic_defers_without_opt_in(monkeypatch):
    monkeypatch.setattr(
        dl,
        "_default_semantic_llm_apply",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run without opt-in")),
    )
    out = dl._default_live_tom_apply(
        {"measure": "Sales[Total]", "anomaly_kind": "blank"}, _cfg(allow_llm_fix=False), None
    )
    assert out.get("status") == "deferred"
    assert "allow_llm_fix" in out["reason"]
    assert out.get("proposed_fix") is not None


def test_semantic_runs_llm_when_opted_in(monkeypatch):
    ran = {"llm": False}

    def _fake_semantic(proposal, cfg, paths, **kw):
        ran["llm"] = True
        return {"success": True, "applied": True, "fix_source": "llm"}

    monkeypatch.setattr(dl, "_default_semantic_llm_apply", _fake_semantic)
    out = dl._default_live_tom_apply(
        {"measure": "Sales[Total]", "anomaly_kind": "blank"}, _cfg(allow_llm_fix=True), None
    )
    assert ran["llm"] is True
    assert out["success"] is True


# ---------------------------------------------------------------------------
# FIX 2b — original_dax present in applied result (semantic path)
# ---------------------------------------------------------------------------


def test_semantic_records_original_dax(monkeypatch):
    monkeypatch.setattr(dl, "_current_measure_dax", lambda t, m: "BLANK()")

    out = dl._default_semantic_llm_apply(
        {"measure": "Sales[Total]", "anomaly_kind": "blank", "current_dax": "BLANK()"},
        _cfg(allow_llm_fix=True),
        None,
        sampler=lambda prompt, **kw: "SUM ( Sales[Amt] )",
        validate_fn=lambda dax: [],
        apply_fn=lambda **kw: {"success": True},
    )
    assert out["success"] is True
    assert out["original_dax"] == "BLANK()"
    assert "original_dax" in out["revert_hint"]
    assert out["fix_source"] == "llm"


# ---------------------------------------------------------------------------
# config + handler wiring for allow_llm_fix
# ---------------------------------------------------------------------------


def test_config_defaults_allow_llm_fix_false():
    assert DebugLoopConfig(pbip_path="x", page="P", visual="v").allow_llm_fix is False


def test_load_model_measures_degrades_without_connection(monkeypatch):
    # No query_executor / not connected → [] (never raises).
    import core.infrastructure.connection_state as cs_mod

    monkeypatch.setattr(cs_mod.connection_state, "query_executor", None, raising=False)
    assert dl._load_model_measures() == []
