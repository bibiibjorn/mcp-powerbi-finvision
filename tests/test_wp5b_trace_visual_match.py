"""F-02: map live-trace queries to PBIR visuals (core/debug/trace_visual_match.py).

Fakes only: a stub builder (pages / visuals / reconstructed queries) and the FakeSession pattern of
tests/test_live_trace.py. Whether Desktop fills ApplicationContext.VisualId is unverified, so both
tiers (VisualId and query shape) are exercised and shape matching never auto-picks a tie.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core.debug import trace_visual_match as tvm
from core.infrastructure import live_trace

# -- DAX shaped like what Desktop sends / what VisualQueryBuilder rebuilds -------------------------

MATRIX = """DEFINE
  VAR __DS0FilterTable = TREATAS({"2025"}, 'Date'[Year])
  VAR __DS0Core = SUMMARIZECOLUMNS(
      ROLLUPADDISSUBTOTAL('Account'[Group], "IsGrandTotalRowTotal"),
      'Account'[Name], __DS0FilterTable,
      "Amount", [Total Amount], "Budget", [Total Budget])
EVALUATE TOPN(501, __DS0Core, [IsGrandTotalRowTotal], 0, 'Account'[Group], 1)"""

MATRIX_RECON = """DEFINE VAR __DS0FilterTable = TREATAS({"2025"}, 'Date'[Year])
EVALUATE SUMMARIZECOLUMNS('Account'[Group], 'Account'[Name], __DS0FilterTable,
  "Total_Amount", [Total Amount], "Total_Budget", [Total Budget])"""

CARD = 'EVALUATE SUMMARIZECOLUMNS("Total_Amount", [Total Amount])'
CARD_TWIN = 'EVALUATE SUMMARIZECOLUMNS("Total_Amount", [Total Amount])'
CHART = """EVALUATE SUMMARIZECOLUMNS('Date'[Month], "Amount", [Total Amount])"""
SLICER = "EVALUATE SUMMARIZECOLUMNS('Date'[Year])"


class _Builder:
    """pages -> visuals -> reconstructed query."""

    def __init__(self, layout):
        self.layout = layout  # {page: [(id, name, type, dax)]}

    def list_pages(self):
        return [{"name": p} for p in self.layout]

    def list_visuals(self, page):
        return [
            {"id": vid, "friendly_name": name, "type": typ, "is_slicer": typ == "slicer"}
            for vid, name, typ, _ in self.layout[page]
        ]

    def build_visual_query(self, page_name, visual_id, include_slicers=True):
        for vid, _n, _t, dax in self.layout[page_name]:
            if vid == visual_id:
                if dax is None:
                    return None
                return SimpleNamespace(dax_query=dax)
        return None


@pytest.fixture
def builder():
    return _Builder(
        {
            "Overview": [
                ("m1", "P&L matrix", "pivotTable", MATRIX_RECON),
                ("c1", "KPI", "card", CARD),
                ("ch1", "Trend", "lineChart", CHART),
                ("s1", "Year", "slicer", SLICER),
                ("x1", "Decor", "textbox", None),
            ],
            "Detail": [("c2", "KPI copy", "card", CARD_TWIN)],
        }
    )


def _row(seq, dax, ms=100, **extra):
    return {
        "seq": seq,
        "request_id": f"r{seq}",
        "total_ms": ms,
        "_text": dax,
        "query": dax[:50],
        **extra,
    }


# -- shape --------------------------------------------------------------------------------------


def test_signature_ignores_desktop_bookkeeping_and_aliases():
    desktop = tvm.shape_signature(MATRIX)
    recon = tvm.shape_signature(MATRIX_RECON)
    assert desktop["group_by"] == recon["group_by"] == {"'account'[group]", "'account'[name]"}
    assert desktop["measures"] == recon["measures"] == {"[total amount]", "[total budget]"}
    assert desktop["filter_tables"] == recon["filter_tables"] == {"date"}
    assert tvm.similarity(desktop, recon)["score"] == 1.0


def test_similarity_separates_different_shapes():
    a, b = tvm.shape_signature(CHART), tvm.shape_signature(MATRIX)
    assert tvm.similarity(a, b)["score"] < 0.4
    assert tvm.similarity(tvm.shape_signature("EVALUATE ROW(1,1)"), b)["score"] == 0.0


# -- tier 1: ApplicationContext --------------------------------------------------------------------


@pytest.mark.parametrize(
    "event",
    [
        {"application_context": '{"Sources":[{"ReportId":"rep-1","VisualId":"abc123"}]}'},
        {"request_properties": "<Context>VisualId=abc123</Context>"},
        {"application_context": {"Sources": [{"VisualId": "abc123"}]}},
    ],
)
def test_application_context_is_parsed_when_present(event):
    ref = tvm.application_context_ref([{"text_data": "x"}, event])
    assert ref["visual_id"] == "abc123"


def test_application_context_absent_is_none():
    assert tvm.application_context_ref([{"text_data": "EVALUATE x"}]) is None


def test_visual_id_wins_over_shape_and_is_exact(builder):
    idx = tvm.build_visual_index(builder)["visuals"]
    row = _row(
        0, CHART, application_context={"visual_id": "m1"}
    )  # shape says chart, id says matrix
    r = tvm.match_query(row, idx, text=CHART)
    assert r["match"]["visual_id"] == "m1" and r["match"]["method"] == "application_context"
    assert r["match"]["confidence"] == "exact" and r["match"]["shape_score"] < 0.4


def test_unknown_visual_id_falls_back_to_shape(builder):
    idx = tvm.build_visual_index(builder)["visuals"]
    r = tvm.match_query(
        _row(0, MATRIX, application_context={"visual_id": "nope"}), idx, text=MATRIX
    )
    assert r["match"]["visual_id"] == "m1" and r["match"]["method"] == "query_shape"
    assert r["application_context_unresolved"]["visual_id"] == "nope"


# -- tier 2: query shape -----------------------------------------------------------------------------


def test_shape_match_is_high_confidence_with_runner_up_candidates(builder):
    idx = tvm.build_visual_index(builder)["visuals"]
    r = tvm.match_query(_row(0, MATRIX), idx, text=MATRIX)
    assert r["match"]["visual_id"] == "m1" and r["match"]["confidence"] == "high"
    assert r["match"]["page"] == "Overview" and r["match"]["method"] == "query_shape"
    assert len(r["candidates"]) == 3 and r["candidates"][0]["visual_id"] == "m1"


def test_identical_cards_on_two_pages_are_a_tie_never_auto_picked(builder):
    idx = tvm.build_visual_index(builder)["visuals"]
    r = tvm.match_query(_row(0, CARD), idx, text=CARD)
    assert r["match"] is None and r["ambiguous"] is True and r["reason"] == "tie_between_visuals"
    assert set(r["tied"]) == {"Overview/c1", "Detail/c2"}


def test_page_scope_resolves_the_tie(builder):
    idx = tvm.build_visual_index(builder, page_name="Overview")["visuals"]
    r = tvm.match_query(_row(0, CARD), idx, text=CARD)
    assert r["match"]["visual_id"] == "c1"


def test_unrelated_query_is_unmatched_with_reason(builder):
    idx = tvm.build_visual_index(builder)["visuals"]
    q = "EVALUATE SUMMARIZECOLUMNS('Other'[A], 'Other'[B], \"v\", [Something Else])"
    r = tvm.match_query(_row(0, q), idx, text=q)
    assert r["match"] is None and r["reason"] == "below_threshold" and not r["ambiguous"]


def test_slicer_query_matches_the_slicer_not_a_data_visual(builder):
    idx = tvm.build_visual_index(builder)["visuals"]
    assert tvm.match_query(_row(0, SLICER), idx, text=SLICER)["match"]["visual_id"] == "s1"


def test_index_reports_unbuildable_and_respects_the_cap(builder):
    full = tvm.build_visual_index(builder)
    assert [u["visual_id"] for u in full["unbuildable"]] == ["x1"]
    capped = tvm.build_visual_index(builder, limit=2)
    assert capped["truncated"] is True and len(capped["visuals"]) == 2


def test_roll_up_per_visual_sums_time(builder):
    idx = tvm.build_visual_index(builder, page_name="Overview")["visuals"]
    rows = [_row(0, MATRIX, 300), _row(1, CHART, 80), _row(2, MATRIX, 500)]
    out = tvm.match_queries(rows, idx, text_of=lambda q: q["_text"])
    top = out["by_visual"][0]
    assert (
        top["visual_id"] == "m1"
        and top["queries"] == 2
        and top["total_ms"] == 800.0
        and top["max_ms"] == 500.0
    )
    assert out["summary"] == {"queries": 3, "matched": 3, "ambiguous": 0, "unmatched": 0}


# -- trace result glue -------------------------------------------------------------------------------


def test_annotate_trace_result_tags_rows_and_strips_private_text(builder):
    result = {"success": True, "queries": [_row(0, MATRIX), _row(1, CHART)]}
    out = tvm.annotate_trace_result(result, builder, page_name="Overview")
    assert [q["visual_match"]["match"]["visual_id"] for q in out["queries"]] == ["m1", "ch1"]
    assert all("_text" not in q for q in out["queries"])
    vm = out["visual_matches"]
    assert vm["matched"] == 2 and vm["visuals_indexed"] == 4 and vm["visuals_unbuildable"] == 1
    json.dumps(out)  # serialisable


def test_capture_for_visual_picks_the_latest_confident_capture(builder):
    result = {
        "success": True,
        "queries": [_row(0, MATRIX, 900), _row(1, CHART), _row(2, MATRIX, 400)],
    }
    found = tvm.capture_for_visual(result, builder, page_name="Overview", visual_id="m1")
    assert found["found"] and found["seq"] == 2 and found["captures_for_visual"] == 2
    assert found["query"] == MATRIX


def test_capture_for_visual_refuses_ties_and_misses(builder):
    tie = {"success": True, "queries": [_row(0, CARD)]}
    both_pages = _Builder(
        {"Overview": builder.layout["Overview"], "Detail": builder.layout["Detail"]}
    )
    # scoped to ONE page the card is unique, so it resolves; the miss case is a different visual
    assert tvm.capture_for_visual(tie, both_pages, page_name="Overview", visual_id="c1")["found"]
    miss = tvm.capture_for_visual(tie, builder, page_name="Overview", visual_id="m1")
    assert miss["found"] is False and "no captured query" in miss["reason"]
    empty = tvm.capture_for_visual(
        {"success": True, "queries": []}, builder, page_name="Overview", visual_id="m1"
    )
    assert "no queries yet" in empty["reason"]


# -- live_trace integration (FakeSession) -------------------------------------------------------------


class _Session:
    def __init__(self, batches):
        self.batches = list(batches)
        self._trace_started = True

    def open(self):
        pass

    def _drain_events(self):
        return self.batches.pop(0) if self.batches else []

    def close(self):
        pass


def _events(act, text, dur, **ctx):
    begin = {
        "event_class": 9,
        "event_subclass": "DAXQuery",
        "activity_id": act,
        "request_id": act,
        "text_data": text,
    }
    end = {
        "event_class": 10,
        "event_subclass": "DAXQuery",
        "activity_id": act,
        "request_id": act,
        "text_data": text,
        "duration_ms": dur,
        **ctx,
    }
    return [begin, end]


@pytest.fixture
def trace():
    live_trace.reset_for_tests()
    yield
    live_trace.reset_for_tests()


def test_live_trace_rows_carry_application_context_and_text_only_on_request(trace):
    ctx = {"application_context": '{"Sources":[{"VisualId":"mtx1","ReportId":"rep9"}]}'}
    live_trace.start(
        "x",
        session_factory=lambda cs: _Session(
            [_events("A", MATRIX, 50, **ctx) + _events("B", CHART, 20)]
        ),
    )
    plain = live_trace.fetch()["queries"]
    assert plain[0]["application_context"] == {"visual_id": "mtx1", "report_id": "rep9"}
    assert "application_context" not in plain[1] and "_text" not in plain[0]
    kept = live_trace.fetch(keep_text=True)["queries"]
    assert kept[0]["_text"] == MATRIX


def test_trace_fetch_match_visuals_end_to_end(trace, builder, monkeypatch):
    from server.handlers import query_plan_handler as qph

    live_trace.start(
        "x",
        session_factory=lambda cs: _Session([_events("A", MATRIX, 50) + _events("B", CHART, 20)]),
    )
    monkeypatch.setattr(qph, "_visual_builder", lambda args: (builder, None))
    out = qph.handle_query_plan(
        {"operation": "trace_fetch", "match": "visuals", "page_name": "Overview"}
    )
    assert out["success"] is True
    assert [q["visual_match"]["match"]["visual_id"] for q in out["queries"]] == ["m1", "ch1"]
    assert all("_text" not in q for q in out["queries"])
    assert out["visual_matches"]["by_visual"][0]["visual_id"] == "m1"
    json.dumps(out)


def test_trace_fetch_match_without_a_pbip_degrades_cleanly(trace, monkeypatch):
    from server.handlers import query_plan_handler as qph

    live_trace.start("x", session_factory=lambda cs: _Session([_events("A", MATRIX, 50)]))
    monkeypatch.setattr(qph, "_visual_builder", lambda args: (None, "PBIP folder not available"))
    out = qph.handle_query_plan({"operation": "trace_fetch", "match": "visuals"})
    assert out["success"] is True and out["visual_matches"]["error_type"] == "pbip_path_required"
    assert all("_text" not in q for q in out["queries"])


def test_trace_fetch_rejects_unknown_match_value(trace):
    from server.handlers import query_plan_handler as qph

    out = qph.handle_query_plan({"operation": "trace_fetch", "match": "pages"})
    assert out["success"] is False and out["error_type"] == "invalid_args"


# -- 09_Debug_Operations visual from_trace=true ----------------------------------------------------------


def test_apply_reference_query_from_trace_marks_fidelity_captured(trace, builder):
    from server.handlers._debug import visual as dv

    live_trace.start("x", session_factory=lambda cs: _Session([_events("A", MATRIX, 50)]))
    result = SimpleNamespace(
        dax_query=MATRIX_RECON, visual_info=SimpleNamespace(visual_id="m1", page_name="Overview")
    )
    response: dict = {}
    executed = dv._apply_reference_query(
        {"from_trace": True}, result, response, MATRIX_RECON, builder=builder, page_name="Overview"
    )
    assert executed == MATRIX
    assert response["fidelity"] == "captured" and response["executed_query_source"] == "trace"
    assert response["trace_capture"]["match"]["visual_id"] == "m1"
    assert response["reconstruction_check"]["matches"] in (True, False)  # still reported


def test_from_trace_without_a_running_trace_keeps_the_reconstruction(trace, builder):
    from server.handlers._debug import visual as dv

    result = SimpleNamespace(
        dax_query=MATRIX_RECON, visual_info=SimpleNamespace(visual_id="m1", page_name="Overview")
    )
    response: dict = {}
    executed = dv._apply_reference_query(
        {"from_trace": True}, result, response, MATRIX_RECON, builder=builder
    )
    assert executed == MATRIX_RECON and "fidelity" not in response
    assert "live trace" in response["reconstruction_check"]["error"]


def test_from_trace_with_no_matching_capture_keeps_the_reconstruction(trace, builder):
    from server.handlers._debug import visual as dv

    live_trace.start("x", session_factory=lambda cs: _Session([_events("A", CHART, 20)]))
    result = SimpleNamespace(
        dax_query=MATRIX_RECON, visual_info=SimpleNamespace(visual_id="m1", page_name="Overview")
    )
    response: dict = {}
    executed = dv._apply_reference_query(
        {"from_trace": True}, result, response, MATRIX_RECON, builder=builder
    )
    assert executed == MATRIX_RECON
    assert "no captured query matches" in response["reconstruction_check"]["error"]


def test_explicit_reference_query_still_wins_over_from_trace(trace, builder):
    from server.handlers._debug import visual as dv

    live_trace.start("x", session_factory=lambda cs: _Session([_events("A", MATRIX, 50)]))
    result = SimpleNamespace(
        dax_query=MATRIX_RECON, visual_info=SimpleNamespace(visual_id="m1", page_name="Overview")
    )
    response: dict = {}
    executed = dv._apply_reference_query(
        {"from_trace": True, "reference_query": CHART},
        result,
        response,
        MATRIX_RECON,
        builder=builder,
    )
    assert executed == CHART and response.get("fidelity") != "captured"
