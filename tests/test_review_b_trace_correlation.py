"""2026-09-22 review, slice B — trace correlation / concurrency (B-08, B-09, B-10, B-33, L-19, L-20).

* B-08: local traces are unfiltered and ``_our_activity_id`` took the FIRST
  DAXQuery from anyone; ActivityID-less events were always kept; the longest
  ExecutionMetrics from any session won; any event restarted the settle window.
* B-09: every TraceSession dropped ALL ``MCP_Trace*`` traces, including another
  session's live one; two traces ran concurrently.
* L-19: ``mode=trace`` returned no rows and ignored ``top_n`` / ``export_path``.
"""

from __future__ import annotations

import datetime as _dt
import threading
import time

import pytest

from core.infrastructure import trace_session as ts

_T0 = _dt.datetime(2026, 9, 22, 9, 0, 0)
OURS = "OUR-SESSION"


def _ev(event_class, activity=None, session=None, text="", **kw):
    row = {"event_class": event_class, "text_data": text}
    if activity is not None:
        row["activity_id"] = activity
    if session is not None:
        row["session_id"] = session
    row.update(kw)
    return row


def _desktop_first_events():
    """Desktop repaints a visual while our query runs — its QueryBegin arrives FIRST."""
    return [
        _ev(
            9,
            "DESKTOP-ACT",
            "DESKTOP-SESSION",
            text="EVALUATE SUMMARIZECOLUMNS(...)",
            event_subclass="DAXQuery",
            start_time=_T0,
        ),
        _ev(
            83,
            "DESKTOP-ACT",
            text="SELECT desktop scan",
            duration_ms=900,
            cpu_time_ms=900,
            start_time=_T0,
            end_time=_T0,
        ),
        _ev(
            9,
            "OUR-ACT",
            OURS,
            text='EVALUATE ROW("x", [M])',
            event_subclass="DAXQuery",
            start_time=_T0,
        ),
        _ev(
            83,
            "OUR-ACT",
            text="SELECT our scan",
            duration_ms=20,
            cpu_time_ms=20,
            start_time=_T0,
            end_time=_T0,
        ),
        _ev(
            83,
            None,
            text="unattributed scan",
            duration_ms=500,
            cpu_time_ms=500,
            start_time=_T0,
            end_time=_T0,
        ),
        _ev(
            10,
            "OUR-ACT",
            OURS,
            event_subclass="DAXQuery",
            duration_ms=40,
            cpu_time_ms=30,
            start_time=_T0,
            end_time=_T0 + _dt.timedelta(milliseconds=40),
        ),
        _ev(136, None, text='{"activityID": "DESKTOP-ACT", "durationMs": 950}'),
        _ev(136, None, text='{"activityID": "OUR-ACT", "durationMs": 41}'),
    ]


def test_our_query_is_identified_by_session_not_arrival_order():
    act, method = ts._identify_our_activity(_desktop_first_events(), OURS, 'EVALUATE ROW("x", [M])')
    assert act == "OUR-ACT" and method == "session"


def test_text_match_is_the_fallback_when_session_ids_are_absent():
    events = [e for e in _desktop_first_events()]
    for e in events:
        e.pop("session_id", None)
    act, method = ts._identify_our_activity(events, OURS, 'EVALUATE  ROW("x", [M])')
    assert act == "OUR-ACT" and method == "text"


def test_ambiguous_capture_does_not_guess():
    events = [e for e in _desktop_first_events()]
    for e in events:
        e.pop("session_id", None)
    act, method = ts._identify_our_activity(events, None, "EVALUATE somethingelse")
    assert act is None and method == "ambiguous"


def test_foreign_and_unattributed_events_are_dropped_when_contaminated():
    kept, info = ts._filter_to_activity(_desktop_first_events(), "OUR-ACT", OURS)
    texts = [e.get("text_data") for e in kept]
    assert "SELECT desktop scan" not in texts
    assert "unattributed scan" not in texts
    assert "SELECT our scan" in texts
    assert info["foreign_dropped"] >= 2 and info["unattributed_dropped"] == 1
    # ExecutionMetrics correlated through the activityID inside its JSON.
    assert any('"OUR-ACT"' in (e.get("text_data") or "") for e in kept)


def test_unattributed_events_kept_when_capture_is_clean():
    events = [
        _ev(9, "OUR-ACT", OURS, event_subclass="DAXQuery"),
        _ev(83, None, text="scan without activity id"),
        _ev(10, "OUR-ACT", OURS, event_subclass="DAXQuery"),
    ]
    kept, info = ts._filter_to_activity(events, "OUR-ACT", OURS)
    assert len(kept) == 3 and info["unattributed_dropped"] == 0


def test_aggregate_uses_our_execution_metrics_not_the_longest():
    session = ts.TraceSession.__new__(ts.TraceSession)
    session._xmsql_cleaner = None
    session._session_id = OURS
    result = ts.TraceResult()
    session._aggregate(result, _desktop_first_events(), 'EVALUATE ROW("x", [M])', True, False)
    assert result.activity_id == "OUR-ACT"
    assert result.correlation["method"] == "session"
    assert result.execution_metrics.get("durationMs") == 41


# --- B-09: one trace at a time; don't drop live traces ----------------------


def test_second_trace_session_waits_then_reports_busy(monkeypatch):
    monkeypatch.setattr(ts, "_TRACE_LOCK_TIMEOUT_S", 0.05)
    monkeypatch.setattr(ts, "_clr_available", lambda: True)
    monkeypatch.setattr(ts, "_load_trace_assemblies", lambda: True)
    assert ts._TRACE_LOCK.acquire(timeout=1)
    try:
        session = ts.TraceSession("Data Source=localhost:1")
        with pytest.raises(ts.TraceBusy):
            session.open()
    finally:
        ts._TRACE_LOCK.release()


def test_close_releases_the_trace_lock(monkeypatch):
    monkeypatch.setattr(ts, "_clr_available", lambda: True)
    monkeypatch.setattr(ts, "_load_trace_assemblies", lambda: True)
    session = ts.TraceSession("Data Source=localhost:1")
    monkeypatch.setattr(session, "_open_connection", lambda: None)
    monkeypatch.setattr(session, "_cached_supported_events", lambda: {})
    monkeypatch.setattr(session, "_start_trace", lambda: None)
    session.open()
    assert ts._TRACE_LOCK.locked()
    session.close()
    assert not ts._TRACE_LOCK.locked()


def test_failed_open_releases_the_trace_lock(monkeypatch):
    monkeypatch.setattr(ts, "_clr_available", lambda: True)
    monkeypatch.setattr(ts, "_load_trace_assemblies", lambda: True)
    session = ts.TraceSession("Data Source=localhost:1")

    def boom():
        raise RuntimeError("connect failed")

    monkeypatch.setattr(session, "_open_connection", boom)
    with pytest.raises(RuntimeError):
        session.open()
    assert not ts._TRACE_LOCK.locked()


class _Trace:
    def __init__(self, name):
        self.Name = name
        self.dropped = False

    def Drop(self):
        self.dropped = True


def test_stale_cleanup_keeps_live_sessions_traces(monkeypatch):
    live_sid = "6F2A1C3E-LIVE-4B7D-9E21-0A1B2C3D4E5F"
    dead_sid = "0D3B5A7C-DEAD-4C8E-8F10-1B2C3D4E5F60"
    # Same naming rule as TraceSession._trace_name (session id truncated to 24).
    live = _Trace(f"MCP_Trace_{live_sid[:24]}_abcd1234")
    dead = _Trace(f"MCP_Trace_{dead_sid[:24]}_ffff0000")
    other = _Trace("DAXStudio_Trace")
    session = ts.TraceSession.__new__(ts.TraceSession)
    session._server = type("S", (), {"Traces": [live, dead, other]})()
    monkeypatch.setattr(
        session,
        "_execute_scalar_rows",
        lambda q: [{"SESSION_ID": live_sid}],
        raising=False,
    )
    assert session.cleanup_stale_traces() == 1
    assert dead.dropped and not live.dropped and not other.dropped


def test_stale_cleanup_drops_nothing_without_a_session_list(monkeypatch):
    dead = _Trace("MCP_Trace_DEADSESSION0000000000000000_ffff0000")
    session = ts.TraceSession.__new__(ts.TraceSession)
    session._server = type("S", (), {"Traces": [dead]})()

    def fail(q):
        raise RuntimeError("DISCOVER_SESSIONS unavailable")

    monkeypatch.setattr(session, "_execute_scalar_rows", fail, raising=False)
    assert session.cleanup_stale_traces() == 0 and not dead.dropped


# --- settle ends on our QueryEnd + ExecutionMetrics (B-08/B-33) -------------


def test_settle_ends_early_once_our_query_is_complete():
    session = ts.TraceSession.__new__(ts.TraceSession)
    session._lock = threading.Lock()
    session._session_id = OURS
    session._supported = {136: {0, 42}}
    session._events = [
        _ev(9, "OUR-ACT", OURS, event_subclass="DAXQuery", text="EVALUATE X"),
        _ev(10, "OUR-ACT", OURS, event_subclass="DAXQuery"),
        _ev(136, None, text='{"activityID": "OUR-ACT", "durationMs": 5}'),
    ]
    started = time.monotonic()
    events = session._settle(3000, query="EVALUATE X")
    assert time.monotonic() - started < 1.5, "settle waited out the full window"
    assert len(events) == 3


def test_execute_query_keeps_first_rows_for_l19(monkeypatch):
    import sys
    import types

    from tests.fake_adomd import FakeReader

    class _Reader(FakeReader):
        def NextResult(self):
            return False

    class _Cmd:
        def __init__(self, text, conn):
            self.CommandTimeout = 0

        def ExecuteReader(self):
            return _Reader([{"[a]": i} for i in range(5)])

    module = types.ModuleType("Microsoft.AnalysisServices.AdomdClient")
    module.AdomdCommand = _Cmd
    monkeypatch.setitem(sys.modules, "Microsoft.AnalysisServices.AdomdClient", module)
    session = ts.TraceSession.__new__(ts.TraceSession)
    session._connection = object()
    sets = session._execute_query("EVALUATE A", keep_rows=2)
    assert sets[0]["row_count"] == 5
    assert sets[0]["rows"] == [{"[a]": 0}, {"[a]": 1}]
    assert sets[0]["truncated"] is True


def test_capability_map_is_cached_per_connection(monkeypatch):
    ts.clear_trace_caches()
    calls = []
    session = ts.TraceSession.__new__(ts.TraceSession)
    session.connection_string = "Data Source=127.0.0.1:9"
    monkeypatch.setattr(
        session, "_discover_supported_events", lambda: calls.append(1) or {9: {42}}, raising=False
    )
    assert session._cached_supported_events() == {9: {42}}
    other = ts.TraceSession.__new__(ts.TraceSession)
    other.connection_string = "Data Source=127.0.0.1:9"
    monkeypatch.setattr(other, "_discover_supported_events", lambda: calls.append(2), raising=False)
    assert other._cached_supported_events() == {9: {42}}
    assert calls == [1]
    ts.clear_trace_caches()


# --- L-19 / L-20 through the 04_Run_DAX handler ------------------------------


def test_trace_mode_returns_rows_trace_id_and_honours_export(monkeypatch, tmp_path):
    import server.handlers.query_handler as qh

    captured = {}

    class FakeRunner:
        def __init__(self, conn_str):
            pass

        @staticmethod
        def is_available():
            return True

        def execute_with_trace(self, query, clear_cache=True, **kw):
            captured.update(kw)
            n = kw.get("keep_rows", 0)
            return {
                "total_ms": 10,
                "se_events": [],
                "runner_lane": "in_process",
                "result_sets": [
                    {
                        "columns": ["[a]"],
                        "row_count": 50,
                        "rows": [{"[a]": i} for i in range(min(n, 50))],
                        "truncated": n < 50,
                    }
                ],
            }

    stored = {}
    monkeypatch.setattr("core.infrastructure.query_trace.NativeTraceRunner", FakeRunner)
    monkeypatch.setattr(qh.connection_state, "is_connected", lambda: True)
    monkeypatch.setattr(
        qh.connection_state,
        "connection_manager",
        type("CM", (), {"connection_string": "Data Source=127.0.0.1:1"})(),
        raising=False,
    )
    monkeypatch.setattr(
        qh.connection_state,
        "store_trace_result",
        lambda d: stored.update(d) or "trace_x",
        raising=False,
    )
    monkeypatch.setattr(qh, "_read_only_mode", lambda: False)

    res = qh.handle_run_dax_trace({"query": "EVALUATE A", "top_n": 5})
    assert res["success"] is True
    assert captured["keep_rows"] == 5
    assert res["rows"] == [{"[a]": i} for i in range(5)] and res["truncated"] is True
    assert res["trace_id"] == "trace_x"
    assert stored["query"] == "EVALUATE A"
    assert "rows" not in res["result_sets"][0], "rows are not duplicated per result set"

    out = tmp_path / "t.csv"
    res = qh.handle_run_dax_trace({"query": "EVALUATE A", "export_path": str(out)})
    assert res["export"]["exported"] is True and out.exists()


def test_trace_mode_on_exe_lane_reports_ignored_args(monkeypatch):
    import server.handlers.query_handler as qh

    class FakeRunner:
        def __init__(self, conn_str):
            pass

        @staticmethod
        def is_available():
            return True

        def execute_with_trace(self, query, clear_cache=True, **kw):
            return {"total_ms": 1, "se_events": [], "runner_lane": "exe", "result_sets": []}

    monkeypatch.setattr("core.infrastructure.query_trace.NativeTraceRunner", FakeRunner)
    monkeypatch.setattr(qh.connection_state, "is_connected", lambda: True)
    monkeypatch.setattr(
        qh.connection_state,
        "connection_manager",
        type("CM", (), {"connection_string": "Data Source=127.0.0.1:1"})(),
        raising=False,
    )
    monkeypatch.setattr(qh.connection_state, "store_trace_result", lambda d: None, raising=False)
    res = qh.handle_run_dax_trace({"query": "EVALUATE A", "top_n": 5})
    assert res["success"] is True
    assert res["ignored_args"] == ["top_n"]


def test_trace_busy_and_no_events_are_structured_errors(monkeypatch):
    import server.handlers.query_handler as qh

    responses = [
        {"_error": "Another trace is running", "_error_type": "busy"},
        {
            "_error": "no events",
            "_error_type": "trace_unavailable",
            "reason": "trace_no_events",
            "amo": {"selected_version": "19.101.1.0"},
        },
    ]

    class FakeRunner:
        def __init__(self, conn_str):
            pass

        @staticmethod
        def is_available():
            return True

        def execute_with_trace(self, query, clear_cache=True, **kw):
            return responses.pop(0)

    monkeypatch.setattr("core.infrastructure.query_trace.NativeTraceRunner", FakeRunner)
    monkeypatch.setattr(qh.connection_state, "is_connected", lambda: True)
    monkeypatch.setattr(
        qh.connection_state,
        "connection_manager",
        type("CM", (), {"connection_string": "Data Source=127.0.0.1:1"})(),
        raising=False,
    )
    busy = qh.handle_run_dax_trace({"query": "EVALUATE A"})
    assert busy["error_type"] == "busy" and busy["retry_after"] > 0
    none = qh.handle_run_dax_trace({"query": "EVALUATE A"})
    assert none["error_type"] == "trace_unavailable"
    assert none["reason"] == "trace_no_events" and none["amo"]["selected_version"]


def test_se_events_with_our_session_but_other_activity_are_kept():
    """Live 2026-09-23: Desktop stamps VertiPaqSEQueryEnd (83) with OUR SessionID and a
    DIFFERENT ActivityID than our QueryBegin/End. They must survive correlation."""
    from core.infrastructure.trace_session import _filter_to_activity

    ours, sess = "ACT-OURS", "SESS-OURS"
    events = [
        {"event_id": 9, "activity_id": ours, "session_id": sess},
        {"event_id": 83, "activity_id": "ACT-SE-1", "session_id": sess},
        {"event_id": 83, "activity_id": "ACT-SE-2", "session_id": sess},
        {"event_id": 10, "activity_id": ours, "session_id": sess},
        {"event_id": 83, "activity_id": "ACT-DESKTOP", "session_id": "SESS-DESKTOP"},
    ]
    kept, info = _filter_to_activity(events, ours, sess)
    kept_ids = [(e["event_id"], e["activity_id"]) for e in kept]
    assert (83, "ACT-SE-1") in kept_ids and (83, "ACT-SE-2") in kept_ids
    assert (83, "ACT-DESKTOP") not in kept_ids
    assert info["foreign_dropped"] == 1 and info["contaminated"] is True


def test_own_se_events_alone_do_not_mark_the_trace_contaminated():
    from core.infrastructure.trace_session import _filter_to_activity

    events = [
        {"event_id": 9, "activity_id": "A", "session_id": "S"},
        {"event_id": 83, "activity_id": "B", "session_id": "S"},
        {"event_id": 10, "activity_id": "A", "session_id": "S"},
    ]
    kept, info = _filter_to_activity(events, "A", "S")
    assert len(kept) == 3 and info["contaminated"] is False and info["foreign_dropped"] == 0


def test_run_retries_once_when_our_query_end_never_arrives():
    """Live 2026-09-23: some Desktop runs never deliver our QueryEnd -> zeros. Re-run once."""
    from core.infrastructure import trace_session as tsm
    from core.infrastructure.trace_session import TraceResult, TraceSession

    s = TraceSession.__new__(TraceSession)
    s._trace = object()
    s._capture_eval_log = False
    calls = []

    def fake_once(*a, **k):
        r = TraceResult()
        calls.append(1)
        r.events = [{"event_id": 9}] if len(calls) == 1 else [{"event_id": 9}, {"event_id": 10}]
        return r

    s._run_once = fake_once
    orig_event_id = tsm._event_id
    tsm._event_id = lambda e: e.get("event_id")
    try:
        out = TraceSession.run(s, 'EVALUATE ROW("a",1)')
    finally:
        tsm._event_id = orig_event_id
    assert len(calls) == 2
    assert out.correlation.get("attempts") == 2 and "incomplete" not in out.correlation
