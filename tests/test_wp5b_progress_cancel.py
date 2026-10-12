"""F-26: per-item progress + cooperative cancel in the long ops (WP-5B).

Covers 09_Profile page / filter_matrix, 05 lint_model, 16 build, 09_Validate test_run and
17_Desktop_Bridge screenshot_all. Everything runs on fakes: no ADOMD, no bridge CLI.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.utilities import progress_ticker as pt


@pytest.fixture
def progress(monkeypatch):
    """Record emit() calls; ``cancel_after(n)`` flips is_cancelled() once n emits happened."""
    seen: list = []
    state = {"cancel_at": None}

    def _emit(done, total, msg):
        seen.append((done, total, msg))

    def _is_cancelled():
        return state["cancel_at"] is not None and len(seen) >= state["cancel_at"]

    monkeypatch.setattr(pt, "emit", _emit)
    monkeypatch.setattr(pt, "is_cancelled", _is_cancelled)
    seen_api = SimpleNamespace(seen=seen, cancel_after=lambda n: state.update(cancel_at=n))
    return seen_api


def test_ticker_emits_per_item_and_caps_events():
    seen = []
    orig = pt.emit
    pt.emit = lambda d, t, m: seen.append((d, t, m))
    try:
        t = pt.Ticker(3, "visual")
        for _ in range(3):
            t.tick("x")
        assert [s[:2] for s in seen] == [(1, 3), (2, 3), (3, 3)]
        assert seen[0][2] == "visual 1 of 3: x"
        seen.clear()
        big = pt.Ticker(1000, "m", max_events=10)
        for _ in range(1000):
            big.tick()
        assert 10 <= len(seen) <= 11  # stride-capped, last item always reported
        assert seen[-1][:2] == (1000, 1000)
    finally:
        pt.emit = orig


# --- 09_Profile page / filter_matrix ---------------------------------------------------------

VISUALS = [
    {"id": f"v{i}", "type": "card", "friendly_name": f"Card {i}", "is_slicer": False}
    for i in range(3)
]


def _ops(builder, qe):
    from core.debug.debug_operations import DebugOperations

    ops = DebugOperations.__new__(DebugOperations)
    ops.builder, ops.qe, ops.logger = builder, qe, MagicMock()
    return ops


def _builder():
    b = MagicMock()
    b.list_visuals.return_value = list(VISUALS)
    b.build_visual_query.return_value = SimpleNamespace(
        dax_query='EVALUATE ROW("v", 1)', filter_context=SimpleNamespace(all_filters=lambda: [])
    )
    return b


def test_profile_page_reports_progress_per_visual(progress):
    qe = MagicMock()
    qe.validate_and_execute_dax.return_value = {
        "success": True,
        "execution_time_ms": 5.0,
        "rows": [],
    }
    out = _ops(_builder(), qe).profile_page("P", iterations=1)
    assert out["visuals_profiled"] == 3
    assert [s[:2] for s in progress.seen] == [(1, 3), (2, 3), (3, 3)]
    assert "cancelled" not in out


def test_profile_page_cancel_returns_partial_results(progress, monkeypatch):
    from core.debug import debug_operations as dbg

    qe = MagicMock()
    qe.validate_and_execute_dax.return_value = {
        "success": True,
        "execution_time_ms": 5.0,
        "rows": [],
    }
    monkeypatch.setattr(dbg, "_cancelled", pt.is_cancelled)
    progress.cancel_after(1)  # cancel once the first visual finished
    out = _ops(_builder(), qe).profile_page("P", iterations=1)
    assert out["cancelled"] is True and out["partial"] is True
    assert out["visuals_profiled"] == 1
    assert {s["reason"] for s in out["skipped"]} == {"cancelled"}
    assert len(out["skipped"]) == 2


def test_profile_traced_ticks_every_run_and_flags_cancel(progress, monkeypatch):
    from core.debug import debug_operations as dbg

    class _Runner:
        def execute_batch_with_trace(self, queries, clear_cache, capture_plan, should_stop):
            out = []
            for _q in queries:
                if should_stop():
                    out.append({"_error": "cancelled", "_error_type": "cancelled"})
                else:
                    out.append({"total_ms": 10.0, "se_ms": 4.0, "fe_ms": 6.0})
            return out

    ops = _ops(_builder(), MagicMock())
    monkeypatch.setattr(ops, "_get_trace_runner", lambda: (_Runner(), None), raising=False)
    monkeypatch.setattr(dbg, "_cancelled", pt.is_cancelled)
    progress.cancel_after(4)  # 3 visuals x (1 warm-up + 1 run) = 6 runs; stop after run 4
    out = ops.profile_page("P", iterations=1, trace=True)
    assert out["cancelled"] is True
    assert [s[:2] for s in progress.seen][:4] == [(1, 6), (2, 6), (3, 6), (4, 6)]


def test_filter_matrix_progress_and_cancel(progress, monkeypatch):
    from core.debug import debug_operations as dbg

    b = _builder()
    b.build_visual_query.return_value.visual_info = SimpleNamespace(
        visual_id="v0", visual_name="Card 0"
    )
    b.list_slicers.return_value = []
    b.converter.equality_filter.side_effect = lambda t, c, v: SimpleNamespace(
        dax=f"{t}[{c}] = {v}", table=t, column=c
    )
    b.rebuild_query.return_value = 'EVALUATE ROW("v", 2)'
    qe = MagicMock()

    def run(query, **kw):
        if "DISTINCT" in query:
            return {"success": True, "rows": [{"x": 1}, {"x": 2}, {"x": 3}]}
        return {"success": True, "execution_time_ms": 7.0, "rows": []}

    qe.validate_and_execute_dax.side_effect = run
    monkeypatch.setattr(dbg, "_cancelled", pt.is_cancelled)
    full = _ops(b, qe).filter_performance_matrix("P", visual_id="v0", filter_columns=["'T'[C]"])
    assert full["combinations_tested"] == 4 and "cancelled" not in full
    assert [s[:2] for s in progress.seen] == [(1, 4), (2, 4), (3, 4), (4, 4)]

    progress.seen.clear()
    progress.cancel_after(2)
    cut = _ops(b, qe).filter_performance_matrix("P", visual_id="v0", filter_columns=["'T'[C]"])
    assert cut["cancelled"] is True and cut["combinations_tested"] == 2


# --- 05 lint_model -----------------------------------------------------------------------


def _lint_conn(monkeypatch, rows):
    import server.handlers.dax_context_handler as dh

    cs = MagicMock()
    cs.is_connected.return_value = True
    qe = MagicMock()
    qe.enumerate_measures_tom.return_value = {"success": True, "rows": rows, "row_count": len(rows)}
    cs.query_executor = qe
    monkeypatch.setattr(dh, "connection_state", cs)
    return dh


ROWS = [{"Name": f"M{i}", "Table": "F", "Expression": "IF([X] > 0, TRUE, FALSE)"} for i in range(4)]


def test_lint_model_progress_per_measure(monkeypatch, progress):
    dh = _lint_conn(monkeypatch, ROWS)
    res = dh.handle_dax_intelligence({"operation": "lint_model"})
    assert res["measures_analyzed"] == 4 and "cancelled" not in res
    assert [s[:2] for s in progress.seen][:4] == [(1, 4), (2, 4), (3, 4), (4, 4)]


def test_lint_model_cancel_returns_partial_never_a_clean_bill(monkeypatch, progress):
    dh = _lint_conn(monkeypatch, ROWS)
    progress.cancel_after(2)
    res = dh.handle_dax_intelligence({"operation": "lint_model"})
    assert res["cancelled"] is True and res["partial"] is True
    assert res["measures_analyzed"] == 2
    assert res["measures_not_analyzed"] == 2


# --- 16 build ---------------------------------------------------------------------------


def _snapshot():
    from core.graph.model_source import ModelSnapshot

    return ModelSnapshot(
        tables={"Sales": {}},
        columns=[{"table": "Sales", "name": "Amount"}],
        measures=[
            {"table": "Sales", "name": f"M{i}", "expression": "SUM(Sales[Amount])"}
            for i in range(3)
        ],
    )


def test_graph_build_reports_progress(progress):
    from core.graph.builder import build_model_graph

    g = build_model_graph(_snapshot())
    assert "measure:Sales[M2]" in g.nodes
    assert progress.seen and progress.seen[-1][:2] == (5, 5)  # 1 table + 1 column + 3 measures


def test_graph_build_cancel_fails_closed_and_caches_nothing(progress):
    from core.graph import service
    from core.graph.builder import GraphBuildCancelled, build_model_graph

    progress.cancel_after(2)
    with pytest.raises(GraphBuildCancelled):
        build_model_graph(_snapshot())

    progress.seen.clear()
    progress.cancel_after(2)
    before = len(service.get_cache()._items) if hasattr(service.get_cache(), "_items") else None
    res = service.get_or_build_graph(scope="model", model=_snapshot(), use_cache=False)
    assert res["success"] is False and res["cancelled"] is True and "graph_id" not in res
    if before is not None:
        assert len(service.get_cache()._items) == before


# --- 09_Validate test_run ------------------------------------------------------------------


def test_run_suite_progress_and_cancel(tmp_path, progress):
    from core.validation import test_suites as ts

    path = str(tmp_path / "s.suite.json")
    for i in range(3):
        ts.put_test(
            path,
            {
                "id": f"t{i}",
                "type": "dax_assertion",
                "query": 'EVALUATE ROW("a", 1)',
                "expect": {"row_count": 1},
            },
        )

    def run(_q):
        return {"success": True, "rows": [{"[a]": 1}], "row_count": 1, "columns": ["[a]"]}

    full = ts.run_suite(path, query=run)
    assert full["passed"] == 3 and "cancelled" not in full
    assert [s[:2] for s in progress.seen] == [(1, 3), (2, 3), (3, 3)]

    progress.seen.clear()
    progress.cancel_after(1)
    cut = ts.run_suite(path, query=run, record=True)
    assert cut["cancelled"] is True and cut["tests_not_run"] == 2
    assert cut["all_passed"] is False and cut["exit_code"] == 1  # never green on a partial run
    assert len(cut["results"]) == 1


# --- 17 screenshot_all ----------------------------------------------------------------------


def _inst(pages=3):
    return {
        "success": True,
        "instances": [
            {
                "pid": 7,
                "currentFilePath": "x.pbip",
                "pages": [{"id": f"p{i}", "displayName": f"P{i}"} for i in range(pages)],
            }
        ],
    }


def test_screenshot_all_optimize_progress_and_cancel(monkeypatch, tmp_path, progress):
    from core.autonomous import desktop_bridge as db

    out_dir = tmp_path / "shots"
    out_dir.mkdir()
    for i in range(3):
        (out_dir / f"p{i}.png").write_bytes(b"x")

    def fake_run(argv, **k):
        shots = [{"outputPath": str(out_dir / f"p{i}.png")} for i in range(3)]
        return {"ok": True, "data": {"status": "ok", "screenshots": shots}}

    monkeypatch.setattr(db, "run_bridge", fake_run)
    monkeypatch.setattr(db, "op_status", lambda **k: _inst())
    monkeypatch.setattr(db, "_optimize", lambda path, tier: {"w": 1})
    monkeypatch.setattr(
        db, "_CaptureProgress", lambda *a, **k: __import__("contextlib").nullcontext()
    )

    full = db.op_screenshot_all(output_dir=str(out_dir), pid=7)
    assert len(full["optimize"]) == 3 and "cancelled" not in full
    assert [s[:2] for s in progress.seen] == [(1, 3), (2, 3), (3, 3)]

    progress.seen.clear()
    progress.cancel_after(1)
    monkeypatch.setattr(db, "_cancelled", pt.is_cancelled)
    cut = db.op_screenshot_all(output_dir=str(out_dir), pid=7)
    assert cut["cancelled"] is True
    assert cut["success"] is True
    assert cut["optimize_skipped"] == ["p1.png", "p2.png"]


def test_capture_progress_counts_pngs_as_they_appear(monkeypatch, tmp_path, progress):
    from core.autonomous import desktop_bridge as db

    monkeypatch.setattr(db._CaptureProgress, "_POLL_S", 0.01)
    cp = db._CaptureProgress(str(tmp_path), 3)
    (tmp_path / "a.png").write_bytes(b"x")
    cp._poll()
    (tmp_path / "b.png").write_bytes(b"x")
    cp._poll()
    cp._poll()  # nothing new -> nothing emitted
    assert [s[:2] for s in progress.seen] == [(1, 3), (2, 3)]
    with db._CaptureProgress(str(tmp_path), 3):  # thread starts and stops cleanly
        pass
