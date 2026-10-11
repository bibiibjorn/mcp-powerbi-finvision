"""2026-09-22 review, slice F: debugger / validator / profiler handler-level fixes.

Pins F-01, F-06 (handler), F-07, F-13, F-15 (handler), F-17, F-20, F-21, F-22
(compare), F-26 / L-20, F-28, F-31, F-32 / K-08, F-33, F-34 (format strings),
F-37 (baseline), F-38, F-39(1)(2) and the WP6 hand-off (debug_loop live_tom
passes ``gate_args`` to the gated ``MeasureApplier``).

No live engine. The builder runs offline on the synthetic golden PBIP or on
small synthetic reports (fake names only); the query executor is a recording
fake that answers by query shape; the handlers under test are the real ones.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from core.debug.debug_operations import DebugOperations
from core.debug.visual_query_builder import VisualQueryBuilder

GOLDEN = Path(__file__).parent / "fixtures" / "pbir_golden" / "GoldenCo"
SALES = GOLDEN / "RPT-Sales.Report"


# --- synthetic report helpers (same shapes as test_review_f_filter_fidelity) ---


def _col(entity: str, prop: str, source: str = None) -> Dict[str, Any]:
    ref = {"Source": source} if source else {"Entity": entity}
    return {"Column": {"Expression": {"SourceRef": ref}, "Property": prop}}


def _measure(entity: str, prop: str) -> Dict[str, Any]:
    return {"Measure": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def _lit(value: str) -> Dict[str, Any]:
    return {"Literal": {"Value": value}}


def _write(root: Path, rel: str, obj: Any) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def _report(tmp_path: Path, visuals: Dict[str, Dict]) -> VisualQueryBuilder:
    root = tmp_path / "Synth.Report"
    d = "definition"
    _write(root, f"{d}/report.json", {"filterConfig": {"filters": []}})
    _write(root, f"{d}/pages/pages.json", {"pageOrder": ["p1"], "activePageName": "p1"})
    _write(
        root,
        f"{d}/pages/p1/page.json",
        {"name": "p1", "displayName": "P1", "filterConfig": {"filters": []}},
    )
    for vid, body in visuals.items():
        _write(root, f"{d}/pages/p1/visuals/{vid}/visual.json", {"name": vid, **body})
    return VisualQueryBuilder(str(root))


def _table_visual(columns, measures, vtype="tableEx", role="Values"):
    projections = [{"field": _col(t, c)} for t, c in columns] + [
        {"field": _measure(t, m)} for t, m in measures
    ]
    return {
        "visual": {
            "visualType": vtype,
            "query": {"queryState": {role: {"projections": projections}}},
        }
    }


def _slicer(entity, prop, selection_cond):
    where = [{"Condition": selection_cond}]
    flt = {"Version": 2, "From": [{"Name": "s", "Entity": entity, "Type": 0}], "Where": where}
    return {
        "visual": {
            "visualType": "advancedSlicerVisual",
            "query": {"queryState": {"Values": {"projections": [{"field": _col(entity, prop)}]}}},
            "objects": {"general": [{"properties": {"filter": {"filter": flt}}}]},
        }
    }


def _in(entity, prop, values):
    return {"In": {"Expressions": [_col(entity, prop, "s")], "Values": [[_lit(v)] for v in values]}}


class _QE:
    """Recording executor fake: answers by query shape, records ``bypass_cache``."""

    def __init__(self, responder=None):
        self.calls: List[Dict[str, Any]] = []
        self.responder = responder or (lambda q: {"success": True, "rows": []})

    def validate_and_execute_dax(self, query, top_n=0, bypass_cache=False, **kw):
        self.calls.append({"query": query, "bypass_cache": bypass_cache})
        res = dict(self.responder(query))
        res.setdefault("execution_time_ms", 5)
        res.setdefault("cache", {"hit": False, "age_s": None})
        return res

    def execute_info_query(self, fn, **kw):
        return {"success": False}

    @property
    def queries(self) -> List[str]:
        return [c["query"] for c in self.calls]

    def all_bypassed(self) -> bool:
        return bool(self.calls) and all(c["bypass_cache"] is True for c in self.calls)


def _visual_rows_responder(total="42"):
    """ROW("Value", ...) -> the total; anything else -> a grand-total-first visual result."""

    def responder(query):
        if 'ROW("Value"' in query:
            return {"success": True, "rows": [{"[Value]": total}]}
        return {
            "success": True,
            "rows": [
                {"'d Customer'[Name]": None, "[Sales]": total, "[IsGrandTotalRowTotal]": "True"},
                {"'d Customer'[Name]": "A", "[Sales]": "30", "[IsGrandTotalRowTotal]": "False"},
            ],
        }

    return responder


# ---------------------------------------------------------------------------
# F-01 / F-37: baseline capture/compare is a real round-trip, every measure reported
# ---------------------------------------------------------------------------


class _CachingQE:
    """Mimics the executor's text-keyed result cache (bypass_cache=False replays)."""

    def __init__(self, values):
        self.values = values
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.queries: List[str] = []

    def validate_and_execute_dax(self, query, top_n=0, bypass_cache=False, **kw):
        self.queries.append(query)
        if not bypass_cache and query in self.cache:
            return dict(self.cache[query], cache={"hit": True, "age_s": 1})
        for name, value in self.values.items():
            if f"[{name}]" in query:
                if isinstance(value, Exception):
                    return {"success": False, "error": str(value)}
                res = {"success": True, "rows": [{"[v]": value}]}
                break
        else:
            res = {"success": True, "rows": [{"[v]": None}]}
        self.cache[query] = res
        return dict(res, cache={"hit": False, "age_s": None})


@pytest.fixture
def baseline_qe(monkeypatch):
    import server.handlers._debug.tools as tools

    qe = _CachingQE({"Sales": 100.0})
    cs = MagicMock()
    cs.is_connected.return_value = True
    cs.query_executor = qe
    cs.model_generation = 3
    monkeypatch.setattr(tools, "connection_state", cs, raising=False)
    return tools, qe


def test_f01_compare_baseline_sees_a_change_made_after_capture(tmp_path, baseline_qe):
    tools, qe = baseline_qe
    path = str(tmp_path / "b.json")
    cap = tools.handle_validate(
        {"operation": "capture_baseline", "measures": ["Sales"], "baseline_path": path}
    )
    assert cap["success"] is True
    qe.values["Sales"] = 250.0  # the model edit the guard exists for
    cmp = tools.handle_validate({"operation": "compare_baseline", "baseline_path": path})
    assert cmp["changed"] == 1, cmp
    assert cmp["differences"][0]["current"] == 250.0
    assert cmp["cache_hits"] == []
    saved = json.loads(Path(path).read_text(encoding="utf-8"))
    assert saved["version"] == 2 and saved["captured_at"] and "errors" in saved


def test_f37_baseline_reports_measures_that_errored_at_capture(tmp_path, baseline_qe):
    tools, qe = baseline_qe
    qe.values["Broken"] = RuntimeError("syntax error")
    path = str(tmp_path / "b.json")
    tools.handle_validate(
        {"operation": "capture_baseline", "measures": ["Sales", "Broken"], "baseline_path": path}
    )
    qe.values["Broken"] = 7.0
    cmp = tools.handle_validate({"operation": "compare_baseline", "baseline_path": path})
    broken = [d for d in cmp["differences"] if d["measure"] == "Broken"]
    assert broken and broken[0]["current"] == 7.0
    assert "syntax error" in broken[0]["baseline_error"]


def test_f37_nan_against_a_number_is_a_change(tmp_path, baseline_qe):
    tools, qe = baseline_qe
    qe.values["Sales"] = float("nan")
    path = str(tmp_path / "b.json")
    tools.handle_validate(
        {"operation": "capture_baseline", "measures": ["Sales"], "baseline_path": path}
    )
    qe.values["Sales"] = 5.0
    cmp = tools.handle_validate({"operation": "compare_baseline", "baseline_path": path})
    assert cmp["changed"] == 1
    assert tools._values_differ(float("nan"), float("nan"), 0.001) is False
    assert tools._values_differ(None, 0, 0.001) is True


def test_f37_measure_refs_are_escaped_and_table_qualified(tmp_path, baseline_qe):
    tools, qe = baseline_qe
    path = str(tmp_path / "b.json")
    tools.handle_validate(
        {
            "operation": "capture_baseline",
            "measures": ["Net]Value", "Sales[Total]"],
            "baseline_path": path,
        }
    )
    assert any("[Net]]Value]" in q for q in qe.queries)
    assert any("'Sales'[Total]" in q for q in qe.queries)


# ---------------------------------------------------------------------------
# F-06 (handler): manual filters are applied verbatim, not as TREATAS({BLANK()}, ''[])
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("skip_auto", [False, True])
def test_f06_manual_filters_are_keepfilters_in_the_executed_query(skip_auto):
    from server.handlers._debug.context import _apply_manual_filters

    b = VisualQueryBuilder(str(SALES))
    r = b.build_visual_query("Overview", visual_id="vm01matrixfilters001")
    response: Dict[str, Any] = {}
    q = _apply_manual_filters(
        {"filters": ["'Sales'[Year] = 2023"], "skip_auto_filters": skip_auto},
        r,
        b,
        response,
        r.filter_context.data_filters_only(),
        r.dax_query,
    )
    assert "TREATAS({BLANK()}, ''[])" not in q
    assert "KEEPFILTERS('Sales'[Year] = 2023)" in q
    assert "ROLLUPADDISSUBTOTAL(" in q
    assert response["manual_filters_applied"] == ["'Sales'[Year] = 2023"]


# ---------------------------------------------------------------------------
# F-15 / F-07: validators read the MEASURE column of a total query, cache bypassed
# ---------------------------------------------------------------------------


def _customer_report(tmp_path, extra=None):
    visuals = {"t1": _table_visual([("d Customer", "Name")], [("m", "Sales")])}
    visuals.update(extra or {})
    return _report(tmp_path, visuals)


def test_f15_expected_value_reads_the_measure_not_the_group_column(tmp_path):
    b = _customer_report(tmp_path)
    qe = _QE(_visual_rows_responder("42"))
    out = DebugOperations(b, qe).expected_value_test("P1", visual_id="t1", expected_value=42)
    assert out["actual_value"] == "42"
    assert out["test_passed"] is True
    assert qe.all_bypassed()


def test_f15_cross_visual_values_are_not_blank_grand_total_labels(tmp_path):
    card = {
        "visual": {
            "visualType": "card",
            "query": {
                "queryState": {
                    "Values": {
                        "projections": [
                            {
                                "field": {
                                    "Measure": {
                                        "Expression": {"SourceRef": {"Entity": "m"}},
                                        "Property": "Sales",
                                    }
                                }
                            }
                        ]
                    }
                }
            },
        }
    }
    b = _customer_report(tmp_path, {"c1": card})
    qe = _QE(_visual_rows_responder("42"))
    out = DebugOperations(b, qe).cross_visual_validation("Sales")
    assert out["visuals_checked"] == 2
    assert [r["value"] for r in out["results"]] == ["42", "42"]
    assert out["failed"] == []
    assert qe.all_bypassed()


def test_f06_f37_filter_permutation_replaces_the_slicer_with_a_typed_literal(tmp_path):
    slicer = _slicer("d Date", "Year", _in("d Date", "Year", ["2024L"]))
    b = _customer_report(tmp_path, {"s1": slicer})
    b.converter.set_column_type("d Date", "Year", "Int64")

    def responder(query):
        if "DISTINCT(" in query:
            return {"success": True, "rows": [{"'d Date'[Year]": "2023"}]}
        return {"success": True, "rows": [{"[Value]": "9"}]}

    qe = _QE(responder)
    out = DebugOperations(b, qe).filter_permutation_test("P1", visual_id="t1")
    assert out["error_results"] == 0
    res = out["results"][0]
    assert res["filter_dax"] == ["'d Date'[Year] = 2023"]
    assert res["value"] == "9"
    executed = [q for q in qe.queries if "DISTINCT(" not in q][0]
    assert "{BLANK()}" not in executed
    assert "2024" not in executed  # the slicer's own selection was replaced
    assert qe.all_bypassed()


# ---------------------------------------------------------------------------
# F-28: trend = one row per period, in the visual's filter context
# ---------------------------------------------------------------------------


def test_f28_trend_groups_by_period_and_keeps_visual_filters(tmp_path):
    line = _table_visual([("d Date", "Date")], [("m", "Sales")], vtype="lineChart", role="Category")
    slicer = _slicer("d Region", "Region", _in("d Region", "Region", ["'EU'"]))
    b = _report(tmp_path, {"l1": line, "s1": slicer})

    def responder(query):
        return {
            "success": True,
            "rows": [
                {"[__Start]": "2025-01-01", "[Period]": "2025-01", "[Value]": "100"},
                {"[__Start]": "2025-02-01", "[Period]": "2025-02", "[Value]": "150"},
            ],
        }

    qe = _QE(responder)
    out = DebugOperations(b, qe).trend_analysis(
        "P1", visual_id="l1", date_column="'d Date'[Date]", granularity="month"
    )
    q = out["query"]
    assert "GROUPBY(" in q and "EDATE(" in q
    assert "TREATAS({\"EU\"}, 'd Region'[Region])" in q
    assert [d["period"] for d in out["data"]] == ["2025-01", "2025-02"]
    assert out["data"][1]["growth_pct"] == 50.0
    assert qe.all_bypassed()


# ---------------------------------------------------------------------------
# F-22 / F-07: compare warms up, interleaves, bypasses the cache
# ---------------------------------------------------------------------------


def test_f22_compare_interleaves_after_a_warmup(monkeypatch):
    import core.infrastructure.impersonated_executor as ie
    import server.handlers._debug.visual as visual_mod

    cs = MagicMock()
    cs.is_connected.return_value = True
    monkeypatch.setattr(visual_mod, "connection_state", cs, raising=False)

    def responder(query):
        side = "Original" if "Original" in query else "Optimized"
        return {"success": True, "rows": [{side: 1.0}]}

    qe = _QE(responder)
    monkeypatch.setattr(ie, "get_executor_for_args", lambda args: qe, raising=False)
    out = visual_mod.handle_compare_measures(
        {"original_measure": "Sales", "optimized_expression": "SUM('f'[Amt])", "runs": 3}
    )
    order = ["o" if "Original" in q else "p" for q in qe.queries]
    assert order == ["o", "p", "o", "p", "p", "o", "o", "p"]  # warm-up, then A B | B A | A B
    assert qe.all_bypassed()
    timing = out["comparison"]["timing"]
    assert timing["order"] == "interleaved" and timing["runs_per_side"] == 3


# ---------------------------------------------------------------------------
# F-13: VAR parsing is lexer-driven; the chain runs INSIDE the filter context
# ---------------------------------------------------------------------------

_P12_MEASURE = """VAR _sales = SUM('f'[Amount])
VAR _py = CALCULATE(
    VAR _d = MAX('d Date'[Date])
    RETURN [Sales], SAMEPERIODLASTYEAR('d Date'[Date]))
VAR _txt = "RETURN of VAR x"
RETURN DIVIDE(_sales - _py, _py)"""


def test_f13_nested_vars_and_strings_are_not_top_level():
    from server.handlers._debug.variables import _extract_variables_full

    vs = _extract_variables_full(_P12_MEASURE)
    assert [v["name"] for v in vs] == ["_sales", "_py", "_txt"]
    assert vs[2]["expression"] == '"RETURN of VAR x"'
    assert "VAR _d" in vs[1]["expression"]


def test_f13_step_variables_evaluates_the_chain_inside_the_filters(monkeypatch):
    import server.handlers._debug.variables as var_mod
    from server.handlers import debug_handler

    qe = _QE(lambda q: {"success": True, "rows": [{"[value]": 1}]})
    cs = MagicMock()
    cs.is_connected.return_value = True
    cs.query_executor = qe
    monkeypatch.setattr(var_mod, "connection_state", cs, raising=False)
    monkeypatch.setattr(
        debug_handler,
        "_resolve_measure_expression",
        lambda *a, **k: {"success": True, "expression": _P12_MEASURE},
    )
    out = var_mod._handle_step_variables(
        {"measure_name": "Growth", "filters": ["'d Date'[Year] = 2025"]}
    )
    assert out["success"] is True and out["variable_count"] == 3
    first = qe.queries[0]
    assert first.startswith('EVALUATE\nROW("value", CALCULATE(\nVAR _sales')
    assert "RETURN _sales\n,\n'd Date'[Year] = 2025\n)" in first
    assert "DEFINE" not in first
    assert qe.all_bypassed()
    assert out["filter_context"]["filters_applied"] == 1


# ---------------------------------------------------------------------------
# F-17: per-measure trace reads the runner's TOP-LEVEL timings; refs via the lexer
# ---------------------------------------------------------------------------


def test_f17_per_measure_timings_come_from_the_top_level(monkeypatch):
    import core.infrastructure.query_trace as qt
    import server.handlers._debug.variables as var_mod

    class _Runner:
        def __init__(self, conn):
            pass

        @staticmethod
        def is_available():
            return True

        def execute_with_trace(self, query, clear_cache, **kw):
            return {"total_ms": 120, "se_ms": 30, "fe_ms": 90, "se_queries": 4}

    cs = MagicMock()
    cs.connection_manager.connection_string = "Data Source=localhost:1"
    monkeypatch.setattr(var_mod, "connection_state", cs, raising=False)
    monkeypatch.setattr(qt, "NativeTraceRunner", _Runner)
    out = var_mod._trace_measures_individually(["Sales"])
    assert out["Sales"]["total_ms"] == 120 and out["Sales"]["se_queries"] == 4


def test_f17_column_refs_are_not_traced_as_measures():
    from server.handlers._debug.variables import _measure_refs_in

    expr = "SUMX('Sales', 'Sales'[Amount]) + [Margin] + CALCULATE([Sales], 'd'[Y] = 1)"
    assert _measure_refs_in(expr) == ["Margin", "Sales"]
    assert _measure_refs_in(expr, {"Sales", "Margin"}) == ["Margin", "Sales"]


# ---------------------------------------------------------------------------
# F-26 / L-20: optimize never borrows an unrelated trace; traces carry their query
# ---------------------------------------------------------------------------


class _TraceRunner:
    queries: List[str] = []

    def __init__(self, conn):
        pass

    @staticmethod
    def is_available():
        return True

    def execute_with_trace(self, query, clear_cache, **kw):
        _TraceRunner.queries.append(query)
        return {
            "total_ms": 50,
            "fe_ms": 40,
            "se_ms": 10,
            "fe_pct": 80.0,
            "se_pct": 20.0,
            "se_queries": 2,
            "se_events": [],
        }


def test_f26_optimize_traces_the_measure_instead_of_reusing_another_trace(monkeypatch):
    import core.infrastructure.query_trace as qt
    from server.handlers import debug_handler

    other = {
        "trace_id": "t_other",
        "query": 'EVALUATE ROW("x", [Other Measure])',
        "measure_names": ["Other Measure"],
        "performance": {"total_ms": 9999, "fe_ms": 9000, "se_ms": 999, "fe_pct": 90.0},
    }
    cs = MagicMock()
    cs.is_connected.return_value = True
    cs.query_executor = MagicMock()
    cs.connection_manager.connection_string = "Data Source=localhost:1"
    cs.get_cached_trace_result.return_value = other  # what the old code borrowed
    cs.get_last_trace_info.return_value = {
        "available": True,
        "ambiguous": False,
        "trace_id": "t_other",
        "query": other["query"],
        "age_s": 3,
        "recent": [],
    }
    cs.get_trace_result.return_value = other
    stored: List[Dict[str, Any]] = []
    cs.store_trace_result.side_effect = lambda d: stored.append(d) or "t_new"
    monkeypatch.setattr(debug_handler, "connection_state", cs)
    monkeypatch.setattr(
        debug_handler,
        "_resolve_measure_expression",
        lambda *a, **k: {
            "success": True,
            "expression": "SUM('f'[Amount])",
            "measure_details": {"table_name": "m"},
            "expression_source": "live",
        },
    )
    _TraceRunner.queries = []
    monkeypatch.setattr(qt, "NativeTraceRunner", _TraceRunner)
    out = debug_handler._handle_optimize({"operation": "optimize", "measure_name": "Sales"})
    assert out.get("timing_source") == "fresh_trace_row", out
    assert _TraceRunner.queries == ['EVALUATE ROW("Result", [Sales])']
    assert stored and stored[0]["measure_names"] == ["Sales"]


def test_l20_run_dax_trace_stores_its_query_and_returns_the_id(monkeypatch):
    """L-12: 09 run_dax is a thin alias of 04_Run_DAX - the trace, the trace store
    and the (bypassed) row execution all go through query_handler.execute_dax."""
    import core.infrastructure.query_trace as qt
    import server.handlers._debug.variables as var_mod
    import server.handlers.query_handler as qh

    cs = MagicMock()
    cs.is_connected.return_value = True
    cs.connection_manager.connection_string = "Data Source=localhost:1"
    stored: List[Dict[str, Any]] = []
    cs.store_trace_result.side_effect = lambda d: stored.append(dict(d)) or "t_run"
    runs: List[Dict[str, Any]] = []
    cs.agent_policy.safe_run_dax.side_effect = lambda **kw: runs.append(kw) or {
        "success": True,
        "rows": [{"[x]": 1}],
    }
    monkeypatch.setattr(var_mod, "connection_state", cs, raising=False)
    monkeypatch.setattr(qh, "connection_state", cs, raising=False)
    monkeypatch.setattr(qt, "NativeTraceRunner", _TraceRunner)
    out = var_mod._handle_run_dax({"query": 'EVALUATE ROW("x", [Sales])', "trace": True})
    assert out["se_fe_trace"]["trace_id"] == "t_run"
    assert stored[0]["query"] == 'EVALUATE ROW("x", [Sales])'
    assert stored[0]["source"] == "09_Debug_Operations:run_dax"
    assert out["executed_via"] == "04_Run_DAX"
    # The trace lane returned no rows -> one row execution, result cache bypassed (F-07).
    assert [r["bypass_cache"] for r in runs] == [True]
    assert out["result"]["rows"] == [{"[x]": 1}]


# ---------------------------------------------------------------------------
# F-20: numeric strings and bracketed total flags
# ---------------------------------------------------------------------------


def test_f20_anomaly_detector_reads_executor_stringified_numbers():
    from core.debug.anomaly_detector import analyze_results

    nums = [100, 102, 98, 101, 99, 103, 97, 100, -50000, 100000]
    rows = [{"d[Cat]": f"c{i}", "[Net_Asset_Value]": str(v)} for i, v in enumerate(nums)]
    kinds = {a["type"] for a in analyze_results(rows).get("items", [])}
    assert kinds & {"outlier", "extreme_outlier", "unexpected_negative"}, kinds


def test_f20_audit_share_check_skips_the_bracketed_grand_total_row():
    from server.handlers._debug.audit import _audit_check_math

    rows = [
        {"[IsGrandTotalRowTotal]": "True", "[Share]": "1.0", "[NAV]": "-5"},
        {"[IsGrandTotalRowTotal]": "False", "[Share]": "0.6", "[NAV]": "3"},
        {"[IsGrandTotalRowTotal]": "False", "[Share]": "0.4", "[NAV]": "-8"},
    ]
    checks = _audit_check_math([{"status": "ok", "visual_name": "v", "data": rows}])
    assert not [c for c in checks if c["check"] == "share_sum"], checks
    assert [c for c in checks if c["check"] == "negative_nav"]


# ---------------------------------------------------------------------------
# F-21: VQB cache freshness + bust normalisation; freshness warning is not noise
# ---------------------------------------------------------------------------


@pytest.fixture
def cached_report(tmp_path, monkeypatch):
    import shutil

    import server.handlers._debug.helpers as helpers

    root = tmp_path / "GoldenCo"
    shutil.copytree(SALES.parent, root)
    report = root / "RPT-Sales.Report"
    cs = MagicMock()
    cs.get_pbip_folder_path.return_value = str(report)
    monkeypatch.setattr(helpers, "connection_state", cs, raising=False)
    helpers.bust_vqb_cache()
    yield helpers, root, report
    helpers.bust_vqb_cache()


def test_f21_bust_with_the_pbip_file_drops_the_report_entry(cached_report):
    helpers, root, report = cached_report
    first, err = helpers._get_visual_query_builder()
    assert err is None
    assert helpers.bust_vqb_cache(str(root / "RPT-Sales.pbip")) == 1
    second, _ = helpers._get_visual_query_builder()
    assert second is not first


def test_f21_a_saved_definition_change_rebuilds_the_builder(cached_report, monkeypatch):
    helpers, root, report = cached_report
    monkeypatch.setattr(helpers, "_VQB_SIGNATURE_RECHECK_S", 0, raising=False)
    first, _ = helpers._get_visual_query_builder()
    page = report / "definition" / "pages" / "aa01overview00000001" / "page.json"
    stamp = time.time() + 5
    page.write_text(page.read_text(encoding="utf-8"), encoding="utf-8")
    os.utime(page, (stamp, stamp))
    second, _ = helpers._get_visual_query_builder()
    assert second is not first


def test_f21_old_files_are_not_reported_stale(cached_report):
    helpers, root, report = cached_report
    old = time.time() - 3600
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            os.utime(os.path.join(dirpath, f), (old, old))
    info = helpers._check_pbip_freshness(str(report))
    assert info is not None and info["stale"] is False
    assert "hasUnsavedChanges" in info["note"]


def test_f21_measure_cache_is_dropped_after_a_model_write(monkeypatch):
    import core.infrastructure.connection_state as cs_mod

    fake = MagicMock()
    fake.model_generation = 1
    monkeypatch.setattr(cs_mod, "connection_state", fake)
    b = VisualQueryBuilder(str(SALES))
    b._check_model_generation()
    b._measure_cache["Sales"] = object()
    b._all_measures_loaded = True
    fake.model_generation = 2
    b._check_model_generation()
    assert b._measure_cache == {} and b._all_measures_loaded is False


# ---------------------------------------------------------------------------
# F-38: the shared builder never binds an RLS-impersonated executor
# ---------------------------------------------------------------------------


def test_f38_impersonated_executor_is_used_once_not_stored():
    from core.infrastructure.impersonated_executor import ImpersonatedExecutor

    class _Imp(ImpersonatedExecutor):
        def validate_and_execute_dax(self, *a, **k):
            return {"success": False, "error": "offline"}

        def execute_info_query(self, *a, **k):
            return {"success": False}

    b = VisualQueryBuilder(str(SALES))
    admin = _QE()
    b.load_column_types(admin)
    b.load_column_types(_Imp(["Reader"]))
    assert b._query_executor is admin


# ---------------------------------------------------------------------------
# F-34: format-string companions resolved once per builder (INFO.MEASURES)
# ---------------------------------------------------------------------------


def test_f34_format_string_companions_from_info_measures_once(monkeypatch):
    import core.infrastructure.dll_paths as dll_paths

    def _no_amo(*a, **k):
        raise RuntimeError("AMO must not be loaded")

    monkeypatch.setattr(dll_paths, "load_amo_assemblies", _no_amo, raising=False)

    class _InfoQE(_QE):
        def __init__(self):
            super().__init__()
            self.info_calls: List[str] = []

        def execute_info_query(self, fn, **kw):
            self.info_calls.append(fn)
            if fn == "MEASURES":
                return {
                    "success": True,
                    "rows": [
                        {
                            "Name": "Sales",
                            "TableID": 1,
                            "Expression": "1",
                            "FormatStringDefinitionID": 9,
                        },
                        {
                            "Name": "Cost",
                            "TableID": 1,
                            "Expression": "2",
                            "FormatStringDefinitionID": None,
                        },
                    ],
                }
            if fn == "TABLES":
                return {"success": True, "rows": [{"ID": 1, "Name": "m Metrics"}]}
            return {"success": False}

    qe = _InfoQE()
    b = VisualQueryBuilder(str(SALES))
    b._query_executor = qe
    for _ in range(3):
        refs = b._detect_format_string_measures_via_tom(["Sales", "Cost"], "Data Source=x")
        assert refs == ["'m Metrics'[_Sales FormatString]"]
    assert qe.info_calls.count("MEASURES") == 1


# ---------------------------------------------------------------------------
# F-32 / K-08: partition sources via TableID (+ bracketed keys)
# ---------------------------------------------------------------------------


class _PartitionQE(_QE):
    """INFO.* answers shaped like ADOMD: bracketed keys, [TableID] not [TableName]."""

    def validate_and_execute_dax(self, query, top_n=0, bypass_cache=False, **kw):
        if "TableName" in query:
            return {"success": False, "error": "Column 'TableName' cannot be found"}
        return super().validate_and_execute_dax(query, top_n, bypass_cache, **kw)

    def execute_info_query(self, fn, **kw):
        if fn == "TABLES":
            return {
                "success": True,
                "rows": [
                    {"[ID]": 7, "[Name]": "sf Row Drill", "[SystemFlags]": 0},
                    {"[ID]": 8, "[Name]": "Sales Rollup", "[SystemFlags]": 0},
                ],
            }
        if fn == "PARTITIONS":
            return {
                "success": True,
                "rows": [
                    {
                        "[TableID]": 7,
                        "[QueryDefinition]": "{(\"Unit\", NAMEOF('d Unit'[Name]), 0)}",
                    },
                    {
                        "[TableID]": 8,
                        "[QueryDefinition]": "SUMMARIZECOLUMNS('d Date'[Year], \"A\", [Sales])",
                    },
                ],
            }
        return {"success": False}


def test_f32_nameof_field_parameter_detected_from_partitions():
    from core.debug.semantic_classifier import SemanticFilterClassifier, partition_sources

    qe = _PartitionQE()
    assert {p["table"] for p in partition_sources(qe)} == {"sf Row Drill", "Sales Rollup"}
    c = SemanticFilterClassifier(qe).classify("sf Row Drill", "sf Row Drill Fields")
    assert c.classification == "field_parameter"
    assert c.detection_method == "nameof_pattern"


def test_k08_aggregation_table_detected_by_expression():
    from core.debug.aggregation_matcher import AggregationMatcher

    found = AggregationMatcher(_PartitionQE()).detect_aggregation_tables()
    assert "Sales Rollup" in found
    assert found["Sales Rollup"].detection_method == "expression"


# ---------------------------------------------------------------------------
# F-31: Desktop's flat Performance Analyzer export
# ---------------------------------------------------------------------------

_FLAT_EXPORT = {
    "version": "1.1.0",
    "events": [
        {
            "id": "1",
            "name": "Visual Container Lifecycle",
            "start": "2026-09-01T10:00:00.000Z",
            "metrics": {
                "visualId": "v1",
                "visualTitle": "Sales matrix",
                "visualType": "pivotTable",
            },
        },
        {
            "id": "2",
            "parentId": "1",
            "name": "Execute DAX Query",
            "start": "2026-09-01T10:00:00.100Z",
            "end": "2026-09-01T10:00:00.600Z",
            "metrics": {"QueryText": 'EVALUATE ROW("x", [Sales])', "RowCount": 3},
        },
        {"id": "1", "name": "Visual Container Lifecycle", "end": "2026-09-01T10:00:01.000Z"},
    ],
}


def test_f31_flat_export_is_normalised_before_parsing(tmp_path):
    path = tmp_path / "PowerBIPerformanceData.json"
    path.write_text(json.dumps(_FLAT_EXPORT), encoding="utf-8")
    out = DebugOperations(VisualQueryBuilder(str(SALES)), None).perf_analyzer_import(str(path))
    assert out["success"] is True
    row = out["results"][0]
    assert row["dax_ms"] == pytest.approx(500.0)
    assert row["total_ms"] == pytest.approx(1000.0)


# ---------------------------------------------------------------------------
# F-39(1)(2): Desktop's own query as ground truth + reconstruction check
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, dax, visual_id="v1"):
        self.dax_query = dax
        self.visual_info = MagicMock(visual_id=visual_id)


_RECON = (
    "DEFINE VAR __DS0FilterTable = TREATAS({\"EU\"}, 'd Region'[Region])\n"
    "EVALUATE SUMMARIZECOLUMNS('d Unit'[Name], __DS0FilterTable, \"S\", [Sales])"
)
_DESKTOP = (
    "DEFINE VAR __DS0FilterTable = FILTER(KEEPFILTERS(VALUES('d Region'[Region])), "
    "NOT('d Region'[Region] IN {\"EU\"}))\n"
    "EVALUATE SUMMARIZECOLUMNS('d Unit'[Name], __DS0FilterTable, \"S\", [Sales])"
)


def test_f39_reference_query_is_diffed_and_executed():
    from server.handlers._debug.visual import _apply_reference_query

    response: Dict[str, Any] = {}
    q = _apply_reference_query({"reference_query": _DESKTOP}, _Result(_RECON), response, _RECON)
    assert q == _DESKTOP
    assert response["executed_query_source"] == "reference"
    check = response["reconstruction_check"]
    assert check["matches"] is False
    assert "'d Region'[Region]" in check["negated_columns"]["only_in_reference"]
    assert response["fidelity"] == "partial"


def test_f39_reference_query_read_from_a_flat_pa_export(tmp_path):
    from server.handlers._debug.visual import _apply_reference_query

    export = json.loads(json.dumps(_FLAT_EXPORT))
    export["events"][1]["metrics"]["QueryText"] = _RECON
    path = tmp_path / "pa.json"
    path.write_text(json.dumps(export), encoding="utf-8")
    response: Dict[str, Any] = {}
    q = _apply_reference_query(
        {"pa_export_path": str(path), "use_reference_query": False},
        _Result(_RECON),
        response,
        _RECON,
    )
    assert q == _RECON
    assert response["reconstruction_check"]["matches"] is True
    assert response["executed_query_source"] == "reconstruction"


# ---------------------------------------------------------------------------
# F-33 / F-35 / F-14: traced page profile - one batch, layout schedule, warm pass, budget
# ---------------------------------------------------------------------------


class _BatchRunner:
    def __init__(self, ms_by_query):
        self.ms = ms_by_query
        self.batches: List[Dict[str, Any]] = []

    def execute_batch_with_trace(
        self, queries, clear_cache=True, *, capture_plan=False, should_stop=None, **kw
    ):
        self.batches.append({"n": len(queries), "clear_cache": clear_cache})
        out = []
        for q in queries:
            if should_stop is not None and should_stop():
                out.append({"_error": "not run", "_error_type": "cancelled"})
                continue
            out.append(
                {
                    "total_ms": self.ms[q],
                    "se_ms": 1,
                    "fe_ms": self.ms[q] - 1,
                    "cache_cleared": clear_cache,
                }
            )
        return out


class _Builder:
    def build_visual_query(self, page_name, visual_id, include_slicers=True):
        return MagicMock(dax_query=f"Q-{visual_id}")


def _visuals():
    return [
        {"id": "A", "friendly_name": "A", "type": "card", "position": {"tabOrder": 0}},
        {"id": "B", "friendly_name": "B", "type": "tableEx", "position": {"tabOrder": 2}},
        {"id": "C", "friendly_name": "C", "type": "card", "position": {"tabOrder": 1}},
    ]


def test_f33_layout_schedule_one_batch_and_warm_pass(monkeypatch):
    runner = _BatchRunner({"Q-A": 10.0, "Q-B": 100.0, "Q-C": 10.0})
    ops = DebugOperations(_Builder(), _QE())
    monkeypatch.setattr(ops, "_get_trace_runner", lambda: (runner, None))
    kwargs = dict(
        page_name="P",
        data_visuals=_visuals(),
        iterations=2,
        include_slicers=False,
        clear_cache=True,
        simulate_concurrency=2,
        warm_pass=True,
    )
    out = ops._profile_page_traced(schedule="layout", **kwargs)
    assert out["success"] is True
    assert runner.batches[0] == {
        "n": 9,
        "clear_cache": True,
    }  # 3 visuals x (2 runs + warm-up), ONE batch
    assert runner.batches[1] == {"n": 3, "clear_cache": False}  # warm page pass
    assert out["page_estimate"]["schedule"] == "layout"
    assert out["page_estimate"]["est_page_ms"] == 110.0  # A, C then B behind them
    assert all("warm_page_pass_ms" in r for r in out["results"])
    lpt = ops._profile_page_traced(schedule="lpt", **kwargs)
    assert lpt["page_estimate"]["est_page_ms"] == 100.0
    assert "LOWER BOUND" in lpt["estimate_note"]


def test_f14_traced_profile_stops_at_the_deadline(monkeypatch):
    runner = _BatchRunner({"Q-A": 10.0, "Q-B": 100.0, "Q-C": 10.0})
    ops = DebugOperations(_Builder(), _QE())
    monkeypatch.setattr(ops, "_get_trace_runner", lambda: (runner, None))
    out = ops._profile_page_traced(
        page_name="P",
        data_visuals=_visuals(),
        iterations=2,
        include_slicers=False,
        clear_cache=True,
        simulate_concurrency=2,
        deadline=time.monotonic() - 1,
        budget_s=10,
    )
    assert out["visuals_profiled"] == 0
    assert {s["reason"] for s in out["skipped"]} == {"time_budget_exhausted"}


# ---------------------------------------------------------------------------
# WP6 hand-off: debug_loop live_tom hands its gate authorisation to MeasureApplier
# ---------------------------------------------------------------------------


def test_debug_loop_live_tom_passes_gate_args_to_the_gated_applier(monkeypatch, tmp_path):
    from core.autonomous.loops import debug_loop as dl
    from core.dax.optimizer.measure_applier import MeasureApplier

    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")
    proj = tmp_path / "proj"
    pages = proj / "R.Report" / "definition" / "pages" / "p1"
    pages.mkdir(parents=True)
    (proj / "R.pbip").write_text("{}", encoding="utf-8")
    (proj / "R.Report" / "definition" / "report.json").write_text("{}", encoding="utf-8")
    (pages / "page.json").write_text(
        json.dumps({"name": "p1", "displayName": "P"}), encoding="utf-8"
    )

    written: List[Dict[str, Any]] = []
    injector = MagicMock()
    injector.upsert_measure.side_effect = lambda **kw: written.append(kw) or {"success": True}
    cs = MagicMock()
    cs.dax_injector = injector
    monkeypatch.setattr(dl, "_current_measure_dax", lambda t, m: "OLD")

    def apply(proposal, cfg, paths):
        return dl._default_semantic_llm_apply(
            proposal,
            cfg,
            paths,
            sampler=lambda *a, **k: "SUM('f'[Amt])",
            validate_fn=lambda dax: [],
            apply_fn=MeasureApplier(cs).apply,
        )

    cfg = dl.DebugLoopConfig(
        pbip_path=str(proj / "R.pbip"),
        page="P",
        visual="v1",
        measure="Sales[Total]",
        max_iterations=1,
        take_screenshot=False,
        fix_strategy="live_tom",
        allow_llm_fix=True,
        confirm_destructive=True,
        live_tom_apply_fn=apply,
    )
    from unittest.mock import patch

    with patch.object(
        dl, "_invoke_debug_visual", return_value={"success": True, "rows": [{"v": None}]}
    ):
        out = dl.run_debug_loop(cfg, connection_state=None)
    assert written, out
    assert written[0]["measure_name"] == "Total" and written[0]["table_name"] == "Sales"
    assert out["history"][0]["applied_fix"]["applied"] is True


def test_apply_gate_args_outside_an_authorised_loop_passes_the_callers_choice():
    from core.autonomous.loops import debug_loop as dl

    cfg = dl.DebugLoopConfig(pbip_path="x.pbip", page="P", visual="v", confirm_destructive=False)
    assert dl._apply_gate_args(cfg, "M")["confirm_destructive"] is False
    cfg._gate_authorised = True
    args = dl._apply_gate_args(cfg, "M")
    assert args["confirm_destructive"] is True and args["authorised_by"]
