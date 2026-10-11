"""Deep review 2026-10-03 (slice N): live-trace buffer bound, stop/start race, start outside lock.

N-12 (buffer is bounded between pulls, background pump enforces the cap, drops counted),
N-13 (stop clears state in one critical section; auto-stop closes what it owns),
N-34 (start opens the session outside the state lock; events_dropped reported).
"""

from __future__ import annotations

import threading
import time

import pytest

from core.infrastructure import live_trace, trace_session


class FakeSession:
    def __init__(self, events=None, name="S"):
        self.name = name
        self.events = list(events or [])
        self.opened = False
        self.closed = False
        self._trace_started = True

    def open(self):
        self.opened = True

    def close(self):
        self.closed = True

    def _drain_events(self):
        out, self.events = self.events, []
        return out


def _ev(i):
    return {
        "event_class": 10,
        "event_subclass": "DAXQuery",
        "activity_id": f"A{i}",
        "text_data": "EVALUATE 1",
    }


@pytest.fixture(autouse=True)
def _reset():
    live_trace.reset_for_tests()
    yield
    live_trace.reset_for_tests()


# --- N-13 -------------------------------------------------------------------


def test_start_during_stop_is_not_wiped(monkeypatch):
    """A trace_start racing stop()'s fetch phase must neither be orphaned nor wiped."""
    s1, s2 = FakeSession(name="A"), FakeSession(name="B")
    assert live_trace.start("cs", session_factory=lambda cs: s1, max_duration_s=30)["success"]

    results = {}
    real_result = live_trace._result_from

    def racing_result(*a, **kw):
        # the slow fetch phase of stop(): another request starts a new trace right now
        results["start"] = live_trace.start("cs", session_factory=lambda cs: s2, max_duration_s=30)
        return real_result(*a, **kw)

    monkeypatch.setattr(live_trace, "_result_from", racing_result)
    out = live_trace.stop()
    monkeypatch.setattr(live_trace, "_result_from", real_result)

    assert out["stopped"] and s1.closed
    # the new trace is registered and alive, not wiped by stop()'s tail
    assert results["start"]["success"] is True
    assert live_trace.status()["running"] is True
    assert s2.closed is False
    live_trace.stop()
    assert s2.closed is True


def test_auto_stop_closes_session_even_when_state_replaced():
    s1 = FakeSession(name="A")
    live_trace.start("cs", session_factory=lambda cs: s1, max_duration_s=30)
    state = live_trace._STATE
    with live_trace._STATE_LOCK:
        live_trace._STATE = None  # registry lost track of it (the orphan scenario)
    live_trace._auto_stop(state)
    assert s1.closed is True


def test_stop_releases_state_so_next_start_works():
    live_trace.start("cs", session_factory=lambda cs: FakeSession())
    live_trace.stop()
    assert live_trace.status() == {"running": False}
    assert live_trace.start("cs", session_factory=lambda cs: FakeSession())["success"]


# --- N-34 -------------------------------------------------------------------


def test_start_does_not_hold_state_lock_while_opening():
    gate = threading.Event()
    seen = {}

    class SlowOpen(FakeSession):
        def open(self):
            gate.wait(5)
            super().open()

    t = threading.Thread(
        target=lambda: seen.setdefault(
            "r", live_trace.start("cs", session_factory=lambda cs: SlowOpen())
        )
    )
    t.start()
    time.sleep(0.2)
    t0 = time.monotonic()
    st = live_trace.status()  # must not block behind session.open()
    assert time.monotonic() - t0 < 1.0
    assert st["running"] is False and st.get("starting") is True
    # a second start while the first is opening is refused cleanly
    r2 = live_trace.start("cs", session_factory=lambda cs: FakeSession())
    assert r2["success"] is False and r2["error_type"] == "trace_already_running"
    gate.set()
    t.join(5)
    assert seen["r"]["success"] is True
    assert live_trace.status()["running"] is True


def test_failed_open_clears_starting_sentinel():
    class Boom(FakeSession):
        def open(self):
            raise RuntimeError("nope")

    r = live_trace.start("cs", session_factory=lambda cs: Boom())
    assert r["success"] is False and r["error_type"] == "trace_unavailable"
    assert live_trace.status() == {"running": False}
    assert live_trace.start("cs", session_factory=lambda cs: FakeSession())["success"]


def test_factory_exception_is_reported_not_raised():
    def factory(cs):
        raise ValueError("bad conn string")

    r = live_trace.start("cs", session_factory=factory)
    assert r["success"] is False and r["error_type"] == "trace_unavailable"
    assert live_trace.start("cs", session_factory=lambda cs: FakeSession())["success"]


# --- N-12 / N-34: bound + dropped count --------------------------------------


def test_cap_enforced_without_fetch_by_background_pump(monkeypatch):
    monkeypatch.setattr(live_trace, "_PUMP_INTERVAL_S", 0.05)
    sess = FakeSession([_ev(i) for i in range(50)])
    live_trace.start("cs", max_events=10, session_factory=lambda cs: sess, max_duration_s=30)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not sess.closed:
        time.sleep(0.05)
    assert sess.closed, "pump should have stopped the trace once max_events was exceeded"
    st = live_trace.status()
    assert st["running"] is False
    assert st["buffered_events"] == 10
    assert st["events_dropped"] == 40
    assert "max_events" in st["stopped_reason"]


def test_fetch_reports_events_dropped():
    sess = FakeSession([_ev(i) for i in range(8)])
    live_trace.start("cs", max_events=5, session_factory=lambda cs: sess)
    f = live_trace.fetch()
    assert f["raw_events"] == 5 and f["events_dropped"] == 3 and f["running"] is False


def test_session_buffer_cap_is_pushed_to_session():
    sess = FakeSession()
    seen = {}
    sess.set_max_buffered_events = lambda n: seen.setdefault("cap", n)
    live_trace.start("cs", max_events=123, session_factory=lambda cs: sess)
    assert seen["cap"] == 123


def test_session_side_drops_count_and_stop_trace():
    class Dropping(FakeSession):
        events_dropped = 7

    sess = Dropping([_ev(1)])
    live_trace.start("cs", max_events=5, session_factory=lambda cs: sess)
    f = live_trace.fetch()
    assert f["events_dropped"] == 7 and f["running"] is False


# --- N-12: the real TraceSession buffer is bounded ----------------------------


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _real_session():
    s = trace_session.TraceSession.__new__(trace_session.TraceSession)
    s._events = []
    s._lock = threading.Lock()
    s._first_event = threading.Event()
    return s


def test_trace_session_buffer_drops_oldest_and_counts():
    s = _real_session()
    s.set_max_buffered_events(10)
    for i in range(35):
        s._on_event(None, _Args(EventClass=10, TextData=f"q{i}"))
    drained = s._drain_events()
    assert len(drained) <= 10
    assert drained[-1].get("text_data") == "q34"  # newest kept
    assert s.events_dropped == 35 - len(drained)
    assert s.events_dropped >= 25


def test_trace_session_unbounded_by_default():
    s = _real_session()
    for i in range(30):
        s._on_event(None, _Args(EventClass=10, TextData=f"q{i}"))
    assert len(s._drain_events()) == 30
    assert s.events_dropped == 0


# --- N-11: never ClearCache a published (XMLA) model --------------------------


def test_trace_session_clear_cache_skipped_on_service_target():
    commands = []
    s = trace_session.TraceSession.__new__(trace_session.TraceSession)
    s._database_name = "Prod"
    s.local = False
    s.access_token = "tok"
    s._new_command = lambda text: commands.append(text) or (_ for _ in ()).throw(
        AssertionError("sent")
    )
    assert s.is_service_target() is True
    assert s.clear_cache() is False
    assert commands == []


def test_trace_session_clear_cache_still_sent_locally():
    commands = []

    class Cmd:
        def ExecuteNonQuery(self):
            return 0

    s = trace_session.TraceSession.__new__(trace_session.TraceSession)
    s._database_name = "Local"
    s.local = True
    s.access_token = None
    s._new_command = lambda text: commands.append(text) or Cmd()
    assert s.clear_cache() is True
    assert "<ClearCache>" in commands[0]


def test_vertipaq_clear_refused_on_xmla(monkeypatch):
    from core.infrastructure import xmla_target
    from core.infrastructure.query_executor import OptimizedQueryExecutor

    monkeypatch.setattr(xmla_target, "is_active", lambda: True)
    ex = OptimizedQueryExecutor.__new__(OptimizedQueryExecutor)
    ex._hold_gate = lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not run"))
    out = ex.clear_vertipaq_cache()
    assert out["cache_cleared"] is False and out["success"] is False
    assert "service target" in out["error"]


def test_trace_start_refused_on_xmla(monkeypatch):
    from types import SimpleNamespace

    from core.infrastructure import connection_state as cs_mod
    from core.infrastructure import xmla_target
    from server.handlers import query_plan_handler as qph

    monkeypatch.setattr(xmla_target, "is_active", lambda: True)
    monkeypatch.setattr(
        cs_mod,
        "connection_state",
        SimpleNamespace(
            connection_manager=SimpleNamespace(connection_string="Data Source=powerbi://x")
        ),
    )
    r = qph.handle_query_plan({"operation": "trace_start"})
    assert r["success"] is False and r["error_type"] == "trace_unsupported_on_service"


def test_run_once_reports_cache_not_cleared_on_service_target():
    s = trace_session.TraceSession.__new__(trace_session.TraceSession)
    s._database_name = "Prod"
    s.local = False
    s.access_token = "tok"
    s._events = []
    s._lock = threading.Lock()
    s._execute_query = lambda q, keep_rows=0: []
    s._settle = lambda settle_ms, query=None: []

    def aggregate(result, events, query, capture_plan, capture_eval_log):
        result.correlation = {"method": "x"}  # _aggregate overwrites correlation

    s._aggregate = aggregate
    res = s._run_once("EVALUATE 1", True, False, False, 0, 5, 0)
    assert res.cache_cleared is False
    assert res.correlation["cache_not_cleared"] == "cache not cleared (service target)"
    assert res.correlation["method"] == "x"
    # not requested -> no note
    res2 = s._run_once("EVALUATE 1", False, False, False, 0, 5, 0)
    assert "cache_not_cleared" not in res2.correlation
