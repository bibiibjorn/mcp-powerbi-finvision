"""Review slice J — 05 dependencies cost (J-12), AggregateException causes (J-29).
(J-25, the research-regex cost tests, went with ``core/research`` in the 2026-10-03 D-01 fix.)"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# J-12 — 05 dependencies: O(1) DAX round-trips, memory cache, Type filter
# ---------------------------------------------------------------------------
class _QE:
    def __init__(self):
        self.calls = []

    def execute_info_query(self, view, top_n=None, **kw):
        self.calls.append(view)
        if view == "MEASURES":
            return {
                "success": True,
                "rows": [
                    {"Table": "Sales", "Name": "Total", "Expression": "SUM(Sales[Amount])"},
                    {"Table": "Sales", "Name": "Avg", "Expression": "[Total] / COUNTROWS(Sales)"},
                    {"Table": "Kpi", "Name": "Margin", "Expression": "[total] * 0.1 // [Avg]"},
                ],
            }
        if view == "COLUMNS":
            return {
                "success": True,
                "rows": [
                    {"Table": "Sales", "Name": "Amount", "Type": 1},
                    {"Table": "Sales", "Name": "RowNumber-2662979B", "Type": 3},
                    {
                        "Table": "Sales",
                        "Name": "Amount x2",
                        "Type": 2,
                        "Expression": "[Amount] * 2",
                    },
                ],
            }
        return {"success": False}


@pytest.fixture
def helpers(monkeypatch):
    import server.handlers._internal.dependencies_helpers as h

    cs = MagicMock()
    cs.is_connected.return_value = True
    cs.get_connection_identity.return_value = {"port": 1, "database": "db", "model_generation": 1}
    qe = _QE()
    cs.query_executor = qe
    da = MagicMock()
    da.analyze_dependencies.return_value = {
        "success": True,
        "referenced_measures": [("", "Total")],
        "referenced_columns": [],
        "referenced_tables": [],
    }
    da.generate_impact_mermaid.return_value = {"success": True, "mermaid": "graph LR; A-->B"}
    cs.dependency_analyzer = da
    monkeypatch.setattr(h, "connection_state", cs)
    h._sidebar_cache.update({"key": None, "data": None})
    return h, cs, qe, da


def test_j12_one_measures_query_serves_enrichment_and_used_by(helpers):
    h, cs, qe, da = helpers
    res = h.handle_analyze_measure_dependencies({"table": "Sales", "measure": "Total"})
    assert res["success"] is True
    assert qe.calls == ["MEASURES"]
    da.find_measure_usage.assert_not_called()
    assert res["referenced_measures"] == [("Sales", "Total")]
    users = {(u["table"], u["measure"]) for u in res["used_by_measures"]}
    assert users == {("Sales", "Avg"), ("Kpi", "Margin")}  # case-insensitive, comments ignored


def test_j12_sidebar_is_computed_once_per_model_state(helpers, monkeypatch):
    h, cs, qe, da = helpers
    monkeypatch.setattr(h, "generate_dependency_html", MagicMock(return_value="C:/x.html"))
    h.handle_analyze_measure_dependencies({"table": "Sales", "measure": "Total", "html": True})
    h.handle_analyze_measure_dependencies({"table": "Sales", "measure": "Total", "html": True})
    # 2 calls x 1 MEASURES + ONE COLUMNS for the sidebar (the second call hits the cache);
    # no per-measure analyze_dependencies / find_measure_usage round-trips.
    assert qe.calls.count("COLUMNS") == 1
    assert da.analyze_dependencies.call_count == 2
    da.find_measure_usage.assert_not_called()
    cs.get_connection_identity.return_value = {"port": 1, "database": "db", "model_generation": 2}
    h.handle_analyze_measure_dependencies({"table": "Sales", "measure": "Total", "html": True})
    assert qe.calls.count("COLUMNS") == 2  # a write bumped the generation -> recompute


def test_j12_sidebar_keeps_calculated_and_drops_rownumber_columns(helpers):
    h, cs, qe, da = helpers
    data = h._compute_sidebar_data(qe)
    cols = {(c["name"], c["columnType"]) for c in data["all_columns"]}
    assert cols == {("Amount", "Data"), ("Amount x2", "Calculated")}
    dep = data["all_dependencies"]["Sales[Total]"]
    assert dep["upstream"]["columns"] == ["Sales[Amount]"]
    assert sorted(dep["downstream"]["measures"]) == ["Kpi[Margin]", "Sales[Avg]"]


# ---------------------------------------------------------------------------
# J-29 — every AggregateException cause is surfaced
# ---------------------------------------------------------------------------
class _T:
    def __init__(self, name):
        self.FullName = name


class _Clr(Exception):
    def __init__(self, msg, tname="System.Exception", inner=None, inners=None):
        super().__init__(msg)
        self.Message = msg
        self._t = tname
        self.InnerException = inner
        if inners is not None:
            self.InnerExceptions = inners

    def GetType(self):
        return _T(self._t)


def test_j29_aggregate_inner_exceptions_are_all_recorded():
    from core.autonomous.clr_errors import extract_clr_exception_chain, format_clr_error

    a = _Clr("column X invalid")
    b = _Clr("measure Y invalid")
    c = _Clr("relationship Z invalid")
    agg = _Clr(
        "One or more errors occurred.", "System.AggregateException", inner=a, inners=[a, b, c]
    )
    chain = extract_clr_exception_chain(agg)
    assert [i["message"] for i in chain[0]["inner_exceptions"]] == [
        "column X invalid",
        "measure Y invalid",
        "relationship Z invalid",
    ]
    out = format_clr_error(agg, error_type="save_error")
    assert out["clr_root_causes"] == [
        "column X invalid",
        "measure Y invalid",
        "relationship Z invalid",
    ]


def test_j29_single_inner_exception_keeps_old_shape():
    from core.autonomous.clr_errors import extract_clr_exception_chain

    a = _Clr("only cause")
    agg = _Clr("wrapper", "System.AggregateException", inner=a, inners=[a])
    chain = extract_clr_exception_chain(agg)
    assert "inner_exceptions" not in chain[0]
    assert chain[-1]["message"] == "only cause"
