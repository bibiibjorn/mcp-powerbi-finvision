"""F-08: ``09_Debug_Operations compare mode=live_swap`` - A/B on the visual's real measure chain.

The orchestration (core.debug.live_swap) runs against a simulated model: expressions in a dict, a
checkpoint that is a copy of it, timings that depend on the CURRENT expression. Every failure path
must leave the model restored and say so (``restore_verified``); a restore that cannot be proven is a
loud error that names the checkpoint. LIVE CHECK (needs Desktop): one real run on a copy of a
report, then compare the visual's values before/after with 04_Run_DAX.
"""

from __future__ import annotations

import pytest

from core.debug.live_swap import SwapEnv, SwapSpec, run_live_swap, sequence, verdict_from_runs

K = ("Sales", "Total")
BASE, CAND = "SUM(Sales[Amount])", "SUMX(Sales, Sales[Amount])"


def stats(values):
    return {"runs": len(values), "min_ms": min(values)} if values else {}


class World:
    """A fake live model + engine."""

    def __init__(self, base_ms=100.0, cand_ms=60.0, cand_rows=None, jitter=0.0):
        self.exprs = {K: BASE}
        self.checkpoints = {}
        self.base_ms, self.cand_ms, self.jitter = base_ms, cand_ms, jitter
        self.writes = []
        self.runs = []
        self.cand_rows = cand_rows
        self.fail_write_at = None  # n-th write raises
        self.fail_restore = False  # checkpoint_restore (apply) does nothing
        self.cand_query_fails = False
        self.stuck = False  # nothing can put the model back
        self.clock = 0.0
        self.previews = 0

    # --- SwapEnv members
    def read_expression(self, table, name):
        return self.exprs.get((table, name))

    def write_expressions(self, bodies):
        if self.stuck and bodies.get(K) == BASE:
            self.writes.append("write-back ignored")
            return
        n = len(self.writes) + 1
        if self.fail_write_at == n:
            self.writes.append("boom")
            raise RuntimeError("SaveChanges failed")
        self.exprs.update(bodies)
        self.writes.append(dict(bodies))

    def checkpoint_create(self, name):
        self.checkpoints[name] = dict(self.exprs)
        return {"success": True}

    def checkpoint_restore(self, name, preview):
        snap = self.checkpoints[name]
        diff = sum(1 for k in snap if self.exprs.get(k) != snap[k])
        if preview:
            self.previews += 1
            return {"success": True, "preview": True, "change_count": diff}
        if self.fail_restore or self.stuck:
            return {"success": False, "error": "journal apply failed"}
        self.exprs = dict(snap)
        self.writes.append("checkpoint_restore")
        return {"success": True, "applied": diff}

    def run_cold(self, query):
        self.clock += 1
        base = self.cand_ms if self.exprs[K] == CAND else self.base_ms
        ms = base + (self.clock % 3) * self.jitter
        self.runs.append("B" if self.exprs[K] == CAND else "A")
        return {"duration_ms": ms, "total_ms": ms + 5}

    def run_rows(self, query):
        if self.cand_query_fails and self.exprs[K] == CAND:
            return {"success": False, "error": "circular dependency"}
        rows = [{"Year": 2024, "v": 10.0}, {"Year": 2025, "v": 20.0}]
        if self.exprs[K] == CAND and self.cand_rows is not None:
            rows = self.cand_rows
        return {"success": True, "rows": rows}

    def compare_rows(self, a, b, tol):
        same = sorted(map(str, a)) == sorted(map(str, b))
        return {"equivalent": same, "reason": None if same else "value_mismatch"}

    def env(self, **kw):
        return SwapEnv(
            read_expression=self.read_expression,
            write_expressions=self.write_expressions,
            checkpoint_create=self.checkpoint_create,
            checkpoint_restore=self.checkpoint_restore,
            run_cold=self.run_cold,
            run_rows=self.run_rows,
            compare_rows=self.compare_rows,
            stats=stats,
            now=lambda: self.clock,
            **kw,
        )


def spec(**kw):
    base = dict(
        query="EVALUATE SUMMARIZECOLUMNS('Date'[Year], \"v\", [Total])",
        patches=[{"table_name": "Sales", "measure_name": "Total", "expression": CAND}],
        runs=3,
    )
    base.update(kw)
    return SwapSpec(**base)


# ---------------------------------------------------------------------------
def test_sequence_alternates_the_order_and_minimises_writes():
    assert sequence(3) == ["A", "B", "B", "A", "A", "B"]
    assert sequence(1) == ["A", "B"] and sequence(0) == ["A", "B"]
    changes = sum(1 for a, b in zip(sequence(3), sequence(3)[1:]) if a != b)
    assert changes == 3  # not 2 writes per round


def test_candidate_faster_is_reported_and_the_model_is_restored():
    w = World(base_ms=100, cand_ms=60)
    out = run_live_swap(spec(), w.env())
    assert out["success"] and out["verdict"] == "faster" and out["delta_pct"] == -40.0
    assert out["restore_verified"] is True and w.exprs[K] == BASE
    assert out["order"] == ["A", "B", "B", "A", "A", "B"] and w.runs == out["order"]
    assert out["baseline"]["best_ms"] == 100 and out["candidate"]["best_ms"] == 60
    assert (
        out["values"]["equivalent"] is True
        and out["primary_metric"] == "execution_metrics.duration_ms"
    )
    assert out["checkpoint"].startswith("fv_liveswap_") and out["patched"] == [
        {"table_name": "Sales", "measure_name": "Total"}
    ]
    # 3 side changes + 1 final swap back is NOT needed beyond the checkpoint restore (ended on B)
    assert out["model_writes"] == 3 and out["restore_method"] == "checkpoint_restore"


def test_ending_on_the_baseline_needs_no_restore_write():
    w = World()
    out = run_live_swap(spec(runs=2), w.env())  # A B B A -> ends on A
    assert (
        out["restore_method"] == "none_needed"
        and out["restore_verified"]
        and "checkpoint_restore" not in w.writes
    )


def test_slower_and_noise():
    slower = run_live_swap(spec(), World(base_ms=100, cand_ms=140).env())
    assert slower["verdict"] == "slower" and slower["delta_pct"] == 40.0
    noisy = run_live_swap(spec(), World(base_ms=100, cand_ms=97, jitter=20).env())
    assert noisy["verdict"] == "noise" and noisy["min_detectable_effect_pct"] >= 5.0
    assert abs(noisy["delta_pct"]) <= noisy["min_detectable_effect_pct"]


def test_values_differ_skips_the_timing_verdict():
    w = World(cand_rows=[{"Year": 2024, "v": 99.0}, {"Year": 2025, "v": 20.0}])
    out = run_live_swap(spec(), w.env())
    assert (
        out["verdict"] == "values_differ"
        and out["success"]
        and out["values"]["equivalent"] is False
    )
    assert "delta_ms" not in out and out["restore_verified"] and w.exprs[K] == BASE


def test_a_candidate_that_breaks_the_visual_aborts_and_restores():
    w = World()
    w.cand_query_fails = True
    out = run_live_swap(spec(), w.env())
    assert out["success"] is False and out["error_type"] == "live_swap_aborted"
    assert "circular dependency" in out["error"] and out["verdict"] == "inconclusive"
    assert out["restore_verified"] is True and w.exprs[K] == BASE


def test_a_failing_save_mid_run_still_restores():
    w = World()
    w.fail_write_at = 2  # the first swap works, the second one raises
    out = run_live_swap(spec(), w.env())
    assert out["success"] is False and "SaveChanges failed" in out["error"]
    assert out["restore_verified"] is True and w.exprs[K] == BASE


def test_restore_falls_back_to_the_pre_image_write_back():
    w = World()
    w.fail_restore = True
    out = run_live_swap(spec(), w.env())
    assert out["restore_verified"] is True and out["restore_method"] == "pre_image_write_back"
    assert w.exprs[K] == BASE and out["success"] and out["verdict"] == "faster"


def test_an_unprovable_restore_is_a_loud_error_naming_the_checkpoint():
    w = World()
    w.stuck = True
    out = run_live_swap(spec(), w.env())
    assert out["success"] is False and out["error_type"] == "restore_not_verified"
    assert out["restore_verified"] is False and out["checkpoint"] in out["error"]
    assert "checkpoint_restore" in out["error"] and out["restore_log"]


def test_nothing_is_written_when_the_measure_or_checkpoint_is_missing():
    w = World()
    out = run_live_swap(
        spec(patches=[{"table_name": "Sales", "measure_name": "Nope", "expression": "1"}]), w.env()
    )
    assert out["error_type"] == "measure_not_found" and w.writes == [] and w.checkpoints == {}
    w2 = World()
    w2.checkpoint_create = lambda name: {"success": False, "error": "disk full"}
    out = run_live_swap(spec(), w2.env())
    assert out["error_type"] == "checkpoint_failed" and w2.writes == [] and w2.exprs[K] == BASE
    assert run_live_swap(spec(patches=[]), World().env())["error_type"] == "invalid_args"


def test_time_budget_and_cancellation():
    w = World()
    out = run_live_swap(spec(runs=10, time_budget_s=3), w.env())
    assert (
        out["runs_not_executed"] > 0 and "time budget" in out["warning"] and out["restore_verified"]
    )
    w2 = World()
    cancelled = run_live_swap(spec(), w2.env(is_cancelled=lambda: True))
    assert cancelled["error"] == "cancelled" and cancelled["restore_verified"] and w2.writes == []


def test_candidate_that_never_ran_is_inconclusive():
    w = World()
    out = run_live_swap(
        spec(runs=1, time_budget_s=0.5),
        SwapEnv(**{**w.env().__dict__, "now": iter([0, 0, 5, 5, 5, 5, 5]).__next__}),
    )
    assert out["verdict"] == "inconclusive" and out["restore_verified"]


def test_multi_measure_patch_is_one_save_per_swap():
    w = World()
    w.exprs[("Sales", "Margin")] = "1"
    s = spec(
        patches=[
            {"table_name": "Sales", "measure_name": "Total", "expression": CAND},
            {"table_name": "Sales", "measure_name": "Margin", "expression": "2"},
        ],
        runs=1,
    )
    out = run_live_swap(s, w.env())
    assert out["restore_verified"] and w.exprs[("Sales", "Margin")] == "1"
    assert all(len(b) == 2 for b in w.writes if isinstance(b, dict))


def test_verdict_from_runs_uses_the_fastest_run_and_the_spread():
    v = verdict_from_runs([100, 130, 110], [60, 61, 65], stats)
    assert (
        v["verdict"] == "faster"
        and v["delta_pct"] == -40.0
        and v["min_detectable_effect_pct"] == 30.0
    )
    # a 25% win inside a 30% spread is noise
    assert verdict_from_runs([100, 130], [75, 76], stats)["verdict"] == "noise"
    assert verdict_from_runs([], [1], stats)["verdict"] == "inconclusive"
    assert "single run" in verdict_from_runs([100], [50], stats)["warning"]
    assert verdict_from_runs([100, 100], [100, 100], stats)["verdict"] == "noise"


# ---------------------------------------------------------------------------
# server layer
# ---------------------------------------------------------------------------
@pytest.fixture
def handler(monkeypatch):
    from server.handlers._debug import live_swap as h

    monkeypatch.setattr(h.connection_state, "is_connected", lambda: True)
    monkeypatch.setattr(h, "_find_tables", lambda patches: None)
    monkeypatch.setattr(
        h,
        "resolve_query",
        lambda args: ('EVALUATE ROW("v", [Total])', "visual_reconstruction", None),
    )
    return h


def test_resolve_patches_both_forms_and_errors():
    from server.handlers._debug.live_swap import resolve_patches

    patches, err = resolve_patches(
        {"original_measure": "[Total]", "optimized_expression": "1", "table_name": "Sales"}
    )
    assert err is None and patches == [
        {"table_name": "Sales", "measure_name": "Total", "expression": "1"}
    ]
    patches, err = resolve_patches(
        {
            "patch": [
                {"measure_name": "A", "expression": "1"},
                {"name": "B", "expression": "2", "table_name": "T"},
            ]
        }
    )
    assert err is None and [p["measure_name"] for p in patches] == ["A", "B"]
    for bad in (
        {},
        {"patch": []},
        {"patch": [{"measure_name": "A"}]},
        {"patch": ["x"]},
        {"original_measure": "A"},
    ):
        assert resolve_patches(bad)[1]["success"] is False


def test_dry_run_plans_without_writing_and_is_a_read(handler, monkeypatch):
    from server.handlers._debug.visual import handle_compare_measures
    from server.op_classification import classify

    args = {
        "operation": "compare",
        "mode": "live_swap",
        "original_measure": "Total",
        "optimized_expression": "1",
        "table_name": "Sales",
        "runs": 2,
        "dry_run": True,
    }
    monkeypatch.setattr(
        handler, "build_env", lambda: pytest.fail("a dry run must not build the live environment")
    )
    out = handle_compare_measures(args)
    assert (
        out["success"]
        and out["dry_run"]
        and out["order"] == ["A", "B", "B", "A"]
        and out["model_writes_estimated"] == 2
    )
    assert out["patched"] == [{"table_name": "Sales", "measure_name": "Total"}] and out[
        "query"
    ].startswith("EVALUATE")
    assert classify("09_Debug_Operations", args) == "read"
    assert classify("09_Debug_Operations", {**args, "dry_run": False}) == "destructive"
    assert (
        classify("09_Debug_Operations", {"operation": "compare"}) == "read"
    )  # classic compare is unchanged


def test_live_swap_is_gated_like_a_destructive_write(handler, monkeypatch):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")
    from core.autonomous import gating

    gating.invalidate_policy_cache()
    args = {
        "mode": "live_swap",
        "original_measure": "Total",
        "optimized_expression": "1",
        "table_name": "Sales",
    }
    monkeypatch.setattr(
        handler, "build_env", lambda: pytest.fail("a blocked call must not touch the model")
    )
    out = handler.handle_live_swap(args)
    assert out["success"] is False and "confirm_destructive" in str(out)

    gating.invalidate_policy_cache()
    ran = {}
    monkeypatch.setattr(handler, "build_env", lambda: "ENV")
    import core.debug.live_swap as core_ls

    monkeypatch.setattr(
        core_ls,
        "run_live_swap",
        lambda s, e: ran.update(spec=s, env=e)
        or {"success": True, "verdict": "faster", "restore_verified": True},
    )
    done = handler.handle_live_swap({**args, "confirm_destructive": True, "runs": 99})
    assert done["verdict"] == "faster" and ran["env"] == "ENV"
    assert ran["spec"].runs == 10  # clamped
    assert ran["spec"].time_budget_s and ran["spec"].time_budget_s < 180


def test_live_swap_refuses_service_and_offline_targets(handler, monkeypatch):
    from core.infrastructure import xmla_target

    monkeypatch.setattr(xmla_target, "is_active", lambda: True)
    out = handler.handle_live_swap(
        {
            "mode": "live_swap",
            "original_measure": "T",
            "optimized_expression": "1",
            "table_name": "S",
        }
    )
    assert out["error_type"] == "live_swap_unavailable"


def test_mode_and_patch_are_declared_and_deferred():
    from server.handlers import register_all_handlers
    from server.registry import get_registry

    reg = get_registry()
    if reg.total_tools() == 0:
        register_all_handlers(reg)
    props = reg.get_full_schema("09_Debug_Operations")["properties"]
    assert props["mode"]["enum"] == ["live_swap"] and "patch" in props and "dry_run" in props
    emitted = reg.get_emitted_schema("09_Debug_Operations")["properties"]
    assert not {"mode", "patch", "dry_run"} & set(emitted)
