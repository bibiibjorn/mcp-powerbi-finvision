"""2026-09-22 review slice E — E-23 (debugger advice), E-24 (cache compare / p95),
E-25 (visual flow output), E-27 (parse-before-write), handler wiring (E-13 /
E-22) and the wave-1 leftovers (DaxValidator ``]]``)."""

from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from core.codemods import transaction as tx

# --------------------------------------------------------------------------- E-23


def test_e23_step_through_honours_breakpoints():
    from core.dax.context_debugger import DaxContextDebugger

    dax = "CALCULATE([A], T[x] = 1) + SUMX(T, [B])"
    dbg = DaxContextDebugger()
    all_steps = dbg.step_through(dax)
    assert len(all_steps) >= 2
    only_first = dbg.step_through(dax, breakpoints=[0])
    assert len(only_first) == 1 and only_first[0].step_number == 1


def test_e23_advice_never_recommends_calculate_over_a_var():
    import inspect

    from core.dax import context_debugger

    src = inspect.getsource(context_debugger)
    assert "RETURN CALCULATE(Step1, Filter2)" not in src
    assert "CALCULATE(InnerResult, AdditionalFilter)" not in src


# --------------------------------------------------------------------------- E-24


def test_e24_perfect_warm_run_is_fully_cached():
    from core.dax.se_event_analyzer import SeEventAnalyzer

    out = SeEventAnalyzer.compare_cache_impact(
        [{"duration_ms": 120}], [{"duration_ms": 0}, {"duration_ms": 0}]
    )
    assert out["fully_cached"] is True and out["cache_effective"] is True
    assert "fully cached" in out["diagnosis"]
    none = SeEventAnalyzer.compare_cache_impact([{"duration_ms": 120}], [])
    assert none["fully_cached"] is False and none["cache_effective"] is False


def test_e24_p95_is_nearest_rank_not_the_max():
    from core.dax.se_event_analyzer import SeEventAnalyzer

    events = [{"duration_ms": d} for d in range(1, 21)]
    out = SeEventAnalyzer()._analyze_timing_distribution(events, {})
    assert out["p95_ms"] == 19  # nearest-rank: ceil(0.95 * 20) = 19th value (was 20 = max)


# --------------------------------------------------------------------------- E-27


class _P:
    def __init__(self):
        self.upserts = []

    def upsert_measure(self, *, table_name, measure_name, dax_expression, **kw):
        self.upserts.append((table_name, measure_name, dax_expression))
        return {"success": True}

    def delete_measure(self, **kw):
        return {"success": True}


@pytest.fixture(autouse=True)
def _jdir(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_CODEMOD_JOURNAL_DIR", str(tmp_path))


def _model(*ms):
    return NS(
        tables=[NS(name="Sales", measures=[NS(name=n, expression=e) for n, e in ms], columns=[])],
        relationships=[],
    )


def test_e27_engine_rejected_body_is_not_written():
    m = _model(("M1", "[a] / [b]"), ("M2", "[c] / [d]"))
    pv = tx.preview(m, "use_divide", {})
    p = _P()
    seen = []

    def validator(after, defines):
        seen.append(after)
        return "bad column" if "[c]" in after else None

    out = tx.apply(m, "use_divide", {}, pv["plan_hash"], persister=p, validator=validator)
    assert [u[1] for u in p.upserts] == ["M1"]
    assert out["rejected_edits"][0]["target"] == "measure:Sales[M2]"
    assert len(seen) == 2


def test_e27_created_measure_is_defined_for_callers_and_abort_on_bad_create():
    m = _model(("A", "SUM(Sales[X]) * 2"))
    args = {
        "expression": "SUM(Sales[X])",
        "new_name": "XT",
        "target_table": "Sales",
        "auto_detect": True,
    }
    pv = tx.preview(m, "extract_measure", args)
    calls = []

    def ok(after, defines):
        calls.append((after, dict(defines)))
        return None

    tx.apply(m, "extract_measure", args, pv["plan_hash"], persister=_P(), validator=ok)
    caller = next(c for c in calls if c[0].startswith("[XT]"))
    assert caller[1] == {("Sales", "XT"): "SUM(Sales[X])"}

    p = _P()
    out = tx.apply(
        m,
        "extract_measure",
        args,
        pv["plan_hash"],
        persister=p,
        validator=lambda after, d: "nope" if after == "SUM(Sales[X])" else None,
    )
    assert out["error_type"] == "invalid_rewrite" and p.upserts == []


def test_e27_handler_validator_builds_a_cheap_define_query(monkeypatch):
    from server.handlers import dax_codemods_handler as h

    captured = {}

    class QE:
        def validate_and_execute_dax(self, q, top_n=0, bypass_cache=False):
            captured["q"], captured["bypass"] = q, bypass_cache
            return {"success": False, "error": "Column 'x' cannot be found"}

    monkeypatch.setattr(h, "_connection_state", lambda: NS(query_executor=QE()))
    err = h._validator("[XT] * 2 // c", {("Sales", "XT"): "SUM(Sales[X])"})
    assert err == "Column 'x' cannot be found" and captured["bypass"] is True
    assert captured["q"].startswith("DEFINE\nMEASURE 'Sales'[XT] =\nSUM(Sales[X])\n")
    assert captured["q"].endswith('EVALUATE ROW("ok", IF(FALSE(), [XT] * 2 // c\n))')


# ------------------------------------------------------------- handler wiring


def test_handler_rollback_is_gated_under_strict(monkeypatch):
    from core.autonomous import gating
    from server.handlers import dax_codemods_handler as h

    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")
    monkeypatch.setenv("MCP_GATE_ELICITATION", "0")
    gating.invalidate_policy_cache()
    monkeypatch.setattr(h, "_connection_state", lambda: NS(is_connected=lambda: True))
    called = []
    monkeypatch.setattr(tx, "rollback", lambda *a, **k: called.append(1) or {"success": True})
    out = h.handle_dax_codemods({"operation": "rollback", "journal_path": "codemod-1.json"})
    assert out["success"] is False and called == []


def test_handler_move_batch_apply_refuses_before_the_gate(monkeypatch):
    from core.autonomous import gating
    from server.handlers import dax_codemods_handler as h

    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")
    gating.invalidate_policy_cache()
    moves = [{"measure_ref": f"T[M{i}]", "target_table": "X"} for i in range(12)]
    out = h.handle_dax_codemods(
        {
            "operation": "apply",
            "transform": "move_measure_batch",
            "args": {"moves": moves},
            "plan_hash": "x",
        }
    )
    assert out["error_type"] == "transform_not_persistable"


def test_handler_list_journals_and_classification():
    from server.handlers.dax_codemods_handler import handle_dax_codemods
    from server.op_classification import classify

    out = handle_dax_codemods({"operation": "list_journals"})
    assert out["success"] is True and "journals" in out
    assert classify("15_DAX_Refactor", {"operation": "list_journals"}) == "read"
    assert classify("15_DAX_Refactor", {"operation": "rollback"}) == "destructive"


def test_handler_no_reload_hint_and_uses_real_pbip_accessor(monkeypatch):
    """E-22/J-19: the reload hint read a non-existent attribute; live-TOM writes
    need no reload, and pbip_path falls back to get_pbip_folder_path()."""
    from server.handlers import dax_codemods_handler as h

    monkeypatch.setattr(h, "_connection_state", lambda: NS(get_pbip_folder_path=lambda: "C:/x"))
    assert h._pbip_path({}) == "C:/x"
    assert h._pbip_path({"pbip_path": "D:/y"}) == "D:/y"
    import inspect

    assert "reload_required" not in inspect.getsource(h)


# ----------------------------------------------------------- wave-1 leftovers


@pytest.mark.parametrize(
    "q",
    [
        'EVALUATE ROW("x", [A]]B])',
        "EVALUATE ROW(\"x\", COUNTROWS(DISTINCT('Sales'[A]]b])))",
    ],
)
def test_dax_validator_accepts_escaped_brackets(q):
    from core.dax.dax_validator import DaxValidator

    assert DaxValidator.validate_query_syntax(q) == []
    assert DaxValidator.validate_complete_dax_query(q) == []


def test_e22_lint_points_fixes_at_the_one_measure(monkeypatch):
    from unittest.mock import MagicMock

    import server.handlers.dax_context_handler as dh

    cs = MagicMock()
    cs.is_connected.return_value = True
    qe = MagicMock()
    rows = [{"Name": "Unused", "Table": "F", "Expression": "VAR x = 1 VAR y = 2 RETURN y"}]
    qe.enumerate_measures_tom.return_value = {"success": True, "rows": rows, "row_count": 1}
    cs.query_executor = qe
    monkeypatch.setattr(dh, "connection_state", cs)
    monkeypatch.setattr(dh, "_collect_udf_definitions", lambda executor: [])
    res = dh.handle_dax_intelligence({"operation": "lint_model"})
    fixable = [f for f in res["findings"] if f["auto_fixable"]]
    assert fixable, res["findings"]
    assert fixable[0]["fix_args"] == {"measures": ["F[Unused]"]}
