"""WP-P wave 2 -- static DAX validation runs once per query text (P-07) + D-14.

* P-07: ``04_Run_DAX`` used to run the pure-Python DaxValidator four times for the
  analysis and again inside ``validate_and_execute_dax`` (and per benchmark run).
  The executor now memoises the result by query text; the full analysis is built
  only when it is shown (``verbose``) or is the error detail.
* D-14: ``analyze_patterns`` returns ``(warnings, recommendations)``; the old
  unpacking published the warnings as ``good_patterns``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from core.dax.dax_validator import DaxValidator
from core.infrastructure.query_executor import OptimizedQueryExecutor
from core.orchestration.query_policy import QueryPolicy

Q = "EVALUATE TOPN(5, 'Date', 'Date'[Year])"
Q_DEFINE = 'DEFINE VAR x = 1 EVALUATE ROW("a", x)'


def _executor() -> OptimizedQueryExecutor:
    return OptimizedQueryExecutor.__new__(OptimizedQueryExecutor)


def _count_calls(monkeypatch, name):
    calls = {"n": 0}
    real = getattr(DaxValidator, name)

    def spy(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(DaxValidator, name, staticmethod(spy))
    return calls


def test_syntax_validation_runs_once_per_query_text(monkeypatch):
    calls = _count_calls(monkeypatch, "validate_query_syntax")
    ex = _executor()
    assert ex.syntax_errors(Q) == []
    assert ex._validate_dax_syntax(Q) is None
    assert ex._validate_dax_syntax(Q) is None
    assert ex.analyze_dax_query(Q)["syntax_valid"] is True
    assert calls["n"] == 1
    ex._validate_dax_syntax('EVALUATE ROW("a", 1)')  # another text: validated again
    assert calls["n"] == 2


def test_complete_query_check_is_memoised_too(monkeypatch):
    calls = _count_calls(monkeypatch, "validate_complete_dax_query")
    ex = _executor()
    ex._validate_dax_syntax(Q_DEFINE)
    ex._validate_dax_syntax(Q_DEFINE)
    ex._validate_dax_syntax(Q)  # no DEFINE -> never called
    assert calls["n"] == 1


def test_memo_does_not_hide_a_syntax_error():
    ex = _executor()
    bad = "EVALUATE TOPN(5, 'Date'"  # unbalanced
    first = ex._validate_dax_syntax(bad)
    second = ex._validate_dax_syntax(bad)
    assert first is not None and first["error_type"] == "syntax_validation_error"
    assert second == first
    assert ex.syntax_errors(bad) and ex.syntax_errors(bad) is not ex.syntax_errors(bad)  # copies


def test_memo_is_bounded_and_non_str_queries_bypass_it():
    ex = _executor()
    ex._SYNTAX_MEMO_MAX = 4
    for i in range(10):
        ex.syntax_errors(f'EVALUATE ROW("a", {i})')
    assert len(ex.__dict__["_syntax_memo"]) == 4
    assert ex._syntax_check(None) is not None  # no crash, no memo entry
    assert len(ex.__dict__["_syntax_memo"]) == 4


def _policy_and_state(executor):
    state = MagicMock()
    state.is_connected.return_value = True
    state.query_executor = executor
    state.get_safety_limits.return_value = {"max_rows_per_call": 10000}
    return QueryPolicy(MagicMock(get=lambda k, d=None: d)), state


class _Exec(OptimizedQueryExecutor):
    """Real validation + memo, fake engine."""

    def __init__(self):  # noqa: D401 -- no connection
        self.executed = []
        self.analyzed = 0

    def analyze_dax_query(self, query):
        self.analyzed += 1
        return super().analyze_dax_query(query)

    def validate_and_execute_dax(self, query, top_n=0, bypass_cache=False, **kw):
        err = self._validate_dax_syntax(query)
        if err:
            return err
        self.executed.append(query)
        return {"success": True, "rows": [], "execution_time_ms": 1.0}


def test_preview_skips_the_full_analysis_unless_verbose(monkeypatch):
    calls = _count_calls(monkeypatch, "validate_query_syntax")
    ex = _Exec()
    policy, state = _policy_and_state(ex)
    out = policy.safe_run_dax(state, Q)
    assert out["success"] is True and ex.analyzed == 0 and "analysis" not in out
    assert calls["n"] == 1  # pre-check + executor check = one validation

    out = policy.safe_run_dax(state, Q + " ", verbose=True)
    assert ex.analyzed == 1 and out["analysis"]["syntax_valid"] is True


def test_analyze_mode_validates_once_for_warmup_and_all_timed_runs(monkeypatch):
    calls = _count_calls(monkeypatch, "validate_query_syntax")
    ex = _Exec()
    policy, state = _policy_and_state(ex)
    out = policy.safe_run_dax(state, Q, mode="analyze", runs=3)
    assert out["success"] is True and len(ex.executed) == 4  # warm-up + 3
    assert calls["n"] == 1


def test_syntax_error_keeps_the_old_error_shape():
    ex = _Exec()
    policy, state = _policy_and_state(ex)
    out = policy.safe_run_dax(state, "EVALUATE TOPN(5, 'Date'")
    assert out["success"] is False
    assert out["error"] == "Query validation failed"
    assert out["error_type"] == "syntax_validation_error"
    assert out["details"]["syntax_valid"] is False and out["details"]["syntax_errors"]
    assert ex.executed == []


def test_executor_precheck_failure_still_reports_through_analysis():
    ex = _Exec()
    policy, state = _policy_and_state(ex)
    ex.syntax_errors = MagicMock(side_effect=[RuntimeError("boom"), []])
    out = policy.safe_run_dax(state, Q)  # falls back to analyze_dax_query like before
    assert out["success"] is True and ex.analyzed == 1


def test_d14_warnings_are_anti_patterns_not_good_patterns():
    ex = _executor()
    res = ex.analyze_dax_query('EVALUATE ROW("x", SUMX(FILTER(Sales, Sales[a] > 1), Sales[b]))')
    assert any("SUMX(FILTER" in w for w in res["anti_patterns"])
    assert not any("SUMX(FILTER" in g for g in res["good_patterns"])
    warnings, recommendations = DaxValidator.analyze_patterns(
        'EVALUATE ROW("x", SUMX(FILTER(Sales, Sales[a] > 1), Sales[b]))'
    )
    assert res["anti_patterns"] == warnings and res["good_patterns"] == recommendations
