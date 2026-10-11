"""09_Debug_Operations operation='compare' — semantic-equivalence guard wiring.

The compare op used to report a perf delta off ONE scalar under ONE filter
context. `equivalence_check=true` now proves the optimized expression returns
the same data (optionally at a grain) and gates the perf claim behind that
proof. These tests mock the query executor at its boundary — no live model.
"""

from unittest.mock import MagicMock

import server.handlers._debug.visual as visual_mod
from core.autonomous.loops.debug_loop import (
    DebugLoopConfig,
    _default_live_tom_apply,
)


def _patch_qe(monkeypatch, responder):
    """Patch connection_state + the impersonated-executor factory.

    `responder(query) -> dict` decides each query's result, so a test can vary
    the answer by query shape (ROW vs SUMMARIZECOLUMNS, Original vs Optimized).
    """
    cs = MagicMock()
    cs.is_connected.return_value = True
    monkeypatch.setattr(visual_mod, "connection_state", cs, raising=False)

    qe = MagicMock()
    qe.validate_and_execute_dax.side_effect = lambda query, *a, **k: responder(query)

    import core.infrastructure.impersonated_executor as ie

    monkeypatch.setattr(ie, "get_executor_for_args", lambda args: qe, raising=False)
    return qe


def _scalar_responder(original_value, optimized_value, *, ms_a=100.0, ms_b=40.0):
    def responder(query):
        if "Original" in query:
            return {
                "success": True,
                "rows": [{"Original": original_value}],
                "execution_time_ms": ms_a,
            }
        return {
            "success": True,
            "rows": [{"Optimized": optimized_value}],
            "execution_time_ms": ms_b,
        }

    return responder


BASE_ARGS = {
    "original_measure": "Total Sales",
    "optimized_expression": "SUM(Sales[Amount])",
}


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------


def test_equivalence_check_off_preserves_legacy_shape(monkeypatch):
    _patch_qe(monkeypatch, _scalar_responder(100.0, 100.0))
    res = visual_mod.handle_compare_measures(dict(BASE_ARGS))

    assert res["success"] is True
    comp = res["comparison"]
    # Legacy keys unchanged.
    assert comp["values_match"] is True
    assert comp["difference"] == 0.0
    assert comp["performance_improvement_ms"] == 60.0
    assert comp["performance_improvement_pct"] == 60.0
    # No equivalence block when not requested.
    assert "equivalence" not in comp
    # Perf claim tracks the legacy scalar match.
    assert comp["performance_claim_valid"] is True
    assert "performance_warning" not in comp


def test_equivalence_check_off_with_differing_scalar_invalidates_claim(monkeypatch):
    _patch_qe(monkeypatch, _scalar_responder(100.0, 250.0))
    res = visual_mod.handle_compare_measures(dict(BASE_ARGS))

    comp = res["comparison"]
    assert comp["values_match"] is False
    assert comp["performance_claim_valid"] is False
    assert "performance_warning" in comp
    # Timings are never removed.
    assert comp["performance_improvement_ms"] == 60.0


# ---------------------------------------------------------------------------
# Scalar-grain equivalence check
# ---------------------------------------------------------------------------


def test_equivalence_check_scalar_matching(monkeypatch):
    _patch_qe(monkeypatch, _scalar_responder(100.0, 100.0))
    res = visual_mod.handle_compare_measures({**BASE_ARGS, "equivalence_check": True})

    comp = res["comparison"]
    assert comp["equivalence"]["equivalent"] is True
    assert comp["equivalence"]["grain"] == "scalar"
    assert comp["performance_claim_valid"] is True
    assert "performance_warning" not in comp


def test_equivalence_check_scalar_blank_vs_zero_blocks_claim(monkeypatch):
    _patch_qe(monkeypatch, _scalar_responder(None, 0))
    res = visual_mod.handle_compare_measures({**BASE_ARGS, "equivalence_check": True})

    comp = res["comparison"]
    assert comp["equivalence"]["equivalent"] is False
    assert comp["equivalence"]["reason"] == "blank_vs_zero"
    assert comp["performance_claim_valid"] is False
    assert "performance_warning" in comp


# ---------------------------------------------------------------------------
# Grain equivalence check
# ---------------------------------------------------------------------------


def _grain_responder(rows_a, rows_b):
    def responder(query):
        if "SUMMARIZECOLUMNS" in query:
            rows = rows_a if "[Total Sales]" in query else rows_b
            return {"success": True, "rows": rows, "execution_time_ms": 10.0}
        if "Original" in query:
            return {"success": True, "rows": [{"Original": 30.0}], "execution_time_ms": 100.0}
        return {"success": True, "rows": [{"Optimized": 30.0}], "execution_time_ms": 40.0}

    return responder


def test_grain_equivalence_matching_data(monkeypatch):
    rows = [{"Year": 2025, "value": 10.0}, {"Year": 2026, "value": 20.0}]
    # Same multiset, different order — must still be equivalent.
    _patch_qe(monkeypatch, _grain_responder(rows, list(reversed(rows))))

    res = visual_mod.handle_compare_measures(
        {**BASE_ARGS, "equivalence_check": True, "equivalence_grain": "'Date'[Year]"}
    )
    comp = res["comparison"]
    eq = comp["equivalence"]
    assert eq["equivalent"] is True
    assert eq["grain"] == "'Date'[Year]"
    assert eq["row_count_a"] == 2
    assert "SUMMARIZECOLUMNS" in eq["original_query"]
    assert comp["performance_claim_valid"] is True


def test_grain_equivalence_differing_data_blocks_perf_claim(monkeypatch):
    rows_a = [{"Year": 2025, "value": 10.0}, {"Year": 2026, "value": 20.0}]
    rows_b = [{"Year": 2025, "value": 10.0}, {"Year": 2026, "value": 99.0}]
    _patch_qe(monkeypatch, _grain_responder(rows_a, rows_b))

    res = visual_mod.handle_compare_measures(
        {**BASE_ARGS, "equivalence_check": True, "equivalence_grain": "'Date'[Year]"}
    )
    comp = res["comparison"]
    assert comp["equivalence"]["equivalent"] is False
    assert comp["equivalence"]["reason"] == "value_mismatch"
    assert comp["performance_claim_valid"] is False
    assert "performance_warning" in comp
    # Raw timings survive so the caller can still see them.
    assert comp["performance_improvement_ms"] == 60.0
    assert comp["performance_improvement_pct"] == 60.0
    # The scalar totals matched — proving the grain check catches what the
    # single-scalar comparison misses.
    assert comp["values_match"] is True


def test_grain_row_count_mismatch_blocks_claim(monkeypatch):
    rows_a = [{"Year": 2025, "value": 10.0}, {"Year": 2026, "value": 20.0}]
    rows_b = [{"Year": 2025, "value": 10.0}]
    _patch_qe(monkeypatch, _grain_responder(rows_a, rows_b))

    res = visual_mod.handle_compare_measures(
        {**BASE_ARGS, "equivalence_check": True, "equivalence_grain": "'Date'[Year]"}
    )
    assert res["comparison"]["equivalence"]["reason"] == "row_count_mismatch"
    assert res["comparison"]["performance_claim_valid"] is False


# ---------------------------------------------------------------------------
# Fail closed on query failure
# ---------------------------------------------------------------------------


def test_failed_grain_query_is_query_failed_not_equivalent(monkeypatch):
    def responder(query):
        if "SUMMARIZECOLUMNS" in query:
            return {"success": False, "error": "Column not found", "execution_time_ms": 0}
        if "Original" in query:
            return {"success": True, "rows": [{"Original": 5.0}], "execution_time_ms": 100.0}
        return {"success": True, "rows": [{"Optimized": 5.0}], "execution_time_ms": 40.0}

    _patch_qe(monkeypatch, responder)
    res = visual_mod.handle_compare_measures(
        {**BASE_ARGS, "equivalence_check": True, "equivalence_grain": "'Date'[Year]"}
    )
    eq = res["comparison"]["equivalence"]
    assert eq["equivalent"] is False
    assert eq["reason"] == "query_failed"
    assert res["comparison"]["performance_claim_valid"] is False


def test_failed_scalar_query_is_query_failed(monkeypatch):
    def responder(query):
        if "Optimized" in query:
            return {"success": False, "error": "syntax error", "execution_time_ms": 0}
        return {"success": True, "rows": [{"Original": 5.0}], "execution_time_ms": 100.0}

    _patch_qe(monkeypatch, responder)
    res = visual_mod.handle_compare_measures({**BASE_ARGS, "equivalence_check": True})
    eq = res["comparison"]["equivalence"]
    assert eq["equivalent"] is False
    assert eq["reason"] == "query_failed"
    assert res["comparison"]["performance_claim_valid"] is False


# ---------------------------------------------------------------------------
# Grain query construction reuses the shared filter context
# ---------------------------------------------------------------------------


def test_grain_query_includes_manual_filters(monkeypatch):
    seen = []

    def responder(query):
        seen.append(query)
        if "SUMMARIZECOLUMNS" in query:
            return {"success": True, "rows": [{"Year": 2025, "value": 1.0}]}
        if "Original" in query:
            return {"success": True, "rows": [{"Original": 1.0}], "execution_time_ms": 2.0}
        return {"success": True, "rows": [{"Optimized": 1.0}], "execution_time_ms": 1.0}

    _patch_qe(monkeypatch, responder)
    visual_mod.handle_compare_measures(
        {
            **BASE_ARGS,
            "filters": ["'Date'[Year] = 2025"],
            "equivalence_check": True,
            "equivalence_grain": "'Date'[Year]",
        }
    )
    grain_queries = [q for q in seen if "SUMMARIZECOLUMNS" in q]
    assert len(grain_queries) == 2
    for q in grain_queries:
        assert "'Date'[Year] = 2025" in q
        assert '"value"' in q


# ---------------------------------------------------------------------------
# debug_loop integration — a non-equivalent rewrite is a FAILED fix
# ---------------------------------------------------------------------------


def _loop_cfg(**kw):
    base = dict(
        pbip_path="x", page="P", visual="v", measure="Sales[Total]", fix_strategy="live_tom"
    )
    base.update(kw)
    return DebugLoopConfig(**base)


def _patch_loop_optimizer(monkeypatch, final_dax="SUM(Sales[Amt])"):
    """Stub the optimizer + applier seams _default_live_tom_apply imports."""
    import core.autonomous.loops.debug_loop as dl
    import core.dax.optimizer.pipeline as pipeline_mod
    import core.dax.optimizer.measure_applier as applier_mod

    monkeypatch.setattr(dl, "_current_measure_dax", lambda t, m: "OLD DAX", raising=False)

    class FakeOpt:
        def __init__(self):
            self.final_dax = final_dax

    class FakePipeline:
        def __init__(self, *a, **k):
            pass

        def optimize_measure(self, *a, **k):
            return FakeOpt()

    applied = {}

    class FakeApplier:
        def __init__(self, *a, **k):
            pass

        def apply(self, measure_name, table_name, new_dax, gate_args=None):
            # WP6 gated the applier: the loop must hand it gate_args.
            applied["gate_args"] = gate_args
            applied["new_dax"] = new_dax
            return {"success": True}

    monkeypatch.setattr(pipeline_mod, "OptimizationPipeline", FakePipeline)
    monkeypatch.setattr(applier_mod, "MeasureApplier", FakeApplier)
    return applied


def test_debug_loop_defers_non_equivalent_rewrite(monkeypatch):
    import core.autonomous.loops.debug_loop as dl

    applied = _patch_loop_optimizer(monkeypatch)
    monkeypatch.setattr(
        dl,
        "_check_rewrite_equivalence",
        lambda measure, dax, **k: {"equivalent": False, "reason": "value_mismatch"},
    )

    out = _default_live_tom_apply(
        {"measure": "Sales[Total]", "anomaly_kind": "fe_dominated"}, _loop_cfg(), paths=None
    )
    assert out["applied"] is False
    assert out["status"] == "deferred"
    assert "not semantically equivalent" in out["reason"]
    assert out["equivalence"]["reason"] == "value_mismatch"
    # Critically: nothing was written.
    assert applied == {}


def test_debug_loop_applies_equivalent_rewrite(monkeypatch):
    import core.autonomous.loops.debug_loop as dl

    applied = _patch_loop_optimizer(monkeypatch)
    monkeypatch.setattr(
        dl,
        "_check_rewrite_equivalence",
        lambda measure, dax, **k: {"equivalent": True, "reason": "equivalent"},
    )

    out = _default_live_tom_apply(
        {"measure": "Sales[Total]", "anomaly_kind": "fe_dominated"}, _loop_cfg(), paths=None
    )
    assert out["success"] is True
    assert out["applied"] is True
    assert out["equivalence"]["equivalent"] is True
    assert applied["new_dax"] == "SUM(Sales[Amt])"


def test_loop_equivalence_check_fails_closed_without_connection(monkeypatch):
    import core.autonomous.loops.debug_loop as dl
    import core.infrastructure.connection_state as cs_mod

    cs = MagicMock()
    cs.query_executor = None
    monkeypatch.setattr(cs_mod, "connection_state", cs, raising=False)

    v = dl._check_rewrite_equivalence("Total Sales", "SUM(Sales[Amt])")
    assert v["equivalent"] is False
    assert v["reason"] == "query_failed"


def test_loop_equivalence_check_compares_live_values(monkeypatch):
    import core.autonomous.loops.debug_loop as dl
    import core.infrastructure.connection_state as cs_mod

    qe = MagicMock()

    def responder(query, *a, **k):
        # The original is referenced as [Total Sales]; the candidate is raw DAX.
        if "[Total Sales]" in query:
            return {"success": True, "rows": [{"value": 100.0}]}
        return {"success": True, "rows": [{"value": 250.0}]}

    qe.validate_and_execute_dax.side_effect = responder
    cs = MagicMock()
    cs.query_executor = qe
    monkeypatch.setattr(cs_mod, "connection_state", cs, raising=False)

    v = dl._check_rewrite_equivalence("Total Sales", "SUM(Sales[Amt])")
    assert v["equivalent"] is False
    assert v["reason"] == "value_mismatch"


# ---------------------------------------------------------------------------
# Schema registration
# ---------------------------------------------------------------------------


def test_new_args_registered_in_tool_schema():
    from server.tool_schemas import TOOL_SCHEMAS

    props = TOOL_SCHEMAS["debug_operations"]["properties"]
    assert props["equivalence_check"]["type"] == "boolean"
    assert props["equivalence_check"]["default"] is False
    assert props["equivalence_grain"]["type"] == "string"
