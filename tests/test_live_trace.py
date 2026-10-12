"""TRC-01: long-lived trace session (core/infrastructure/live_trace.py)."""

import time

import pytest

from core.infrastructure import live_trace


class FakeSession:
    def __init__(self, batches=None, fail=None):
        self.batches = list(batches or [])
        self.fail = fail
        self.opened = False
        self.closed = False
        self._trace_started = True

    def open(self):
        if self.fail:
            raise self.fail
        self.opened = True

    def _drain_events(self):
        return self.batches.pop(0) if self.batches else []

    def close(self):
        self.closed = True


def _q(act, text, dur, se=()):
    evs = [
        {"event_class": 9, "event_subclass": "DAXQuery", "activity_id": act, "text_data": text},
        {
            "event_class": 10,
            "event_subclass": "DAXQuery",
            "activity_id": act,
            "text_data": text,
            "duration_ms": dur,
        },
    ]
    for d in se:
        evs.append(
            {
                "event_class": 83,
                "activity_id": act,
                "duration_ms": d,
                "cpu_time_ms": d,
                "text_data": "SELECT",
            }
        )
    return evs


@pytest.fixture(autouse=True)
def _reset():
    live_trace.reset_for_tests()
    yield
    live_trace.reset_for_tests()


def test_start_fetch_stop_groups_queries():
    batch = _q("A", 'EVALUATE ROW("x",1)', 40, se=(5, 7)) + _q("B", "EVALUATE T", 900)
    batch.append(
        {
            "event_class": 10,
            "event_subclass": "DAXQuery",
            "activity_id": "P",
            "text_data": "/* <<MCP Internal>> */ ping",
        }
    )
    sess = FakeSession([batch])
    r = live_trace.start("Data Source=x", session_factory=lambda cs: sess)
    assert r["success"] and sess.opened
    f = live_trace.fetch()
    assert f["running"] and f["query_count"] == 2
    ids = [q["request_id"] for q in f["queries"]]
    assert ids == ["A", "B"]
    slow = live_trace.fetch(slowest_first=True)["queries"][0]
    assert slow["request_id"] == "B"
    s = live_trace.stop()
    assert s["stopped"] and sess.closed and s["stopped_reason"] == "trace_stop"
    assert live_trace.fetch()["error_type"] == "no_trace"


def test_second_start_refused_and_busy_reported():
    live_trace.start("x", session_factory=lambda cs: FakeSession())
    r = live_trace.start("x", session_factory=lambda cs: FakeSession())
    assert r["error_type"] == "trace_already_running"
    live_trace.reset_for_tests()

    class TraceBusy(RuntimeError):
        pass

    r = live_trace.start("x", session_factory=lambda cs: FakeSession(fail=TraceBusy("held")))
    assert r["error_type"] == "trace_busy"


def test_duration_bounds():
    assert (
        live_trace.start("x", max_duration_s=0, session_factory=lambda cs: FakeSession())[
            "error_type"
        ]
        == "invalid_parameters"
    )
    assert (
        live_trace.start("x", max_duration_s=99999, session_factory=lambda cs: FakeSession())[
            "error_type"
        ]
        == "invalid_parameters"
    )


def test_auto_stop_after_duration():
    sess = FakeSession()
    live_trace.start("x", max_duration_s=1, session_factory=lambda cs: sess)
    time.sleep(1.6)
    assert sess.closed
    f = live_trace.fetch()
    assert f["running"] is False and "max_duration_s" in f["stopped_reason"]


def test_max_events_cap_stops_trace():
    sess = FakeSession([_q("A", "EVALUATE 1", 1) * 5])
    live_trace.start("x", max_events=3, session_factory=lambda cs: sess)
    f = live_trace.fetch()
    assert f["raw_events"] == 3 and f["running"] is False and "max_events" in f["stopped_reason"]


def test_since_paging():
    sess = FakeSession([_q("A", "q1", 1) + _q("B", "q2", 2) + _q("C", "q3", 3)])
    live_trace.start("x", session_factory=lambda cs: sess)
    f = live_trace.fetch(since=1)
    assert [q["seq"] for q in f["queries"]] == [1, 2] and f["next_since"] == 3


def test_queries_on_one_connection_are_split_by_request_id():
    """Live 2026-09-30: every command on one connection shares its ActivityID."""
    a = _q("CONN", "EVALUATE A", 10)
    b = _q("CONN", "EVALUATE B", 20)
    for ev in a:
        ev["request_id"] = "R1"
    for ev in b:
        ev["request_id"] = "R2"
    sess = FakeSession([a + b])
    live_trace.start("Data Source=x", session_factory=lambda cs: sess)
    f = live_trace.fetch(include_text=True)
    assert [q["query"] for q in f["queries"]] == ["EVALUATE A", "EVALUATE B"]


class PingingSession(FakeSession):
    """Releases its held batch only once the flush ping has run (the engine's buffer)."""

    def __init__(self, held):
        super().__init__([])
        self.held = held
        self.pings = []

    def _execute_scalar_rows(self, q):
        self.pings.append(q)
        token = q.rsplit("=", 1)[1].strip()
        # the engine strips comments: the event text is the bare statement
        text = f"SELECT SESSION_ID FROM $SYSTEM.DISCOVER_SESSIONS WHERE SESSION_SPID = {token}"
        self.batches.append(
            self.held
            + [
                {
                    "event_class": 10,
                    "event_subclass": "DmxQuery",
                    "request_id": "PING",
                    "text_data": text,
                }
            ]
        )
        return []


def test_fetch_flushes_the_engine_buffer_with_a_unique_ping():
    sess = PingingSession(_q("A", "EVALUATE A", 5))
    live_trace.start("Data Source=x", session_factory=lambda cs: sess)
    t = time.monotonic()
    f = live_trace.fetch()
    assert f["query_count"] == 1 and len(sess.pings) == 1
    assert "SESSION_SPID = -" in sess.pings[0]
    assert time.monotonic() - t < 1.0  # returned on its own ping, not the 3 s cap
