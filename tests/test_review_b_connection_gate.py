"""2026-09-22 review, slice B — command gate, cancellation, recovery (B-01, B-19, K-08f).

Real ConnectionManager / ConnectionState / OptimizedQueryExecutor over the
synthetic ADOMD fakes in ``tests/fake_adomd.py`` (modelled on
``probes/B/probe_executor.py``). On the old code 4 parallel queries drove 4
concurrent readers down one connection, a stream error was reported as a DAX
error and the connection stayed "Not connected" until a manual ensure.
"""

from __future__ import annotations

import threading
import time

import pytest

from core.infrastructure.adomd_utils import (
    CommandGate,
    ConnectionBusy,
    command_budget,
    effective_command_timeout,
    is_connection_failure,
)
from tests.fake_adomd import FakeServer, make_state

CS = "Data Source=localhost:50001"


@pytest.fixture
def wired(monkeypatch):
    server = FakeServer("A")
    state, cm, opened = make_state(monkeypatch, {CS: server})
    return state, cm, server, opened


def test_parallel_queries_are_serialised_on_one_connection(wired):
    state, _cm, server, _ = wired
    server.delay_s = 0.05
    qe = state.query_executor
    results = []

    def run(i):
        results.append(qe.validate_and_execute_dax(f'EVALUATE ROW("q", {i})', bypass_cache=True))

    threads = [threading.Thread(target=run, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(r["success"] for r in results), results
    assert server.max_in_flight == 1, "two commands shared the ADOMD connection at once"


def test_gate_timeout_returns_structured_busy(wired):
    state, cm, server, _ = wired
    cm.gate._default_timeout = 0.1
    holding = threading.Event()
    release = threading.Event()

    def hog():
        with cm.gate.hold(purpose="long_query"):
            holding.set()
            release.wait(2)

    t = threading.Thread(target=hog)
    t.start()
    holding.wait(1)
    try:
        res = state.query_executor.validate_and_execute_dax("EVALUATE {1}", bypass_cache=True)
    finally:
        release.set()
        t.join()
    assert res["success"] is False
    assert res["error_type"] == "busy"
    assert res["retry_after"] > 0
    assert "long_query" in res.get("busy_with", "")


def test_cancel_active_queries_cancels_the_in_flight_command(wired):
    state, _cm, server, _ = wired
    server.delay_s = 5.0
    out = {}

    def slow():
        out["res"] = state.query_executor.validate_and_execute_dax(
            "EVALUATE SlowTable", bypass_cache=True
        )

    t = threading.Thread(target=slow)
    t.start()
    deadline = time.monotonic() + 2
    while server.in_flight == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    started = time.monotonic()
    assert state.cancel_active_queries() == 1
    t.join(2)
    assert not t.is_alive()
    assert time.monotonic() - started < 2, "cancel did not release the worker"
    assert out["res"]["success"] is False
    assert out["res"]["error_type"] == "cancelled"
    assert server.cancelled == 1


def test_stream_corruption_reconnects_and_retries_read_once(wired):
    state, _cm, server, opened = wired
    gen_before = state.connection_generation
    server.fail_next = [
        RuntimeError("The server sent an unrecognizable response. Root element is missing.")
    ]
    res = state.query_executor.validate_and_execute_dax("EVALUATE Sales", bypass_cache=True)
    assert res["success"] is True
    assert res.get("connection_recovered") is True
    assert len(opened) == 2, "a fresh connection must have been opened"
    assert state.connection_generation > gen_before


def test_persistent_stream_failure_is_a_connection_error_not_a_dax_error(wired):
    state, _cm, server, _ = wired
    boom = RuntimeError("Cannot write to a BufferedStream while the read buffer is not empty")
    server.fail_next = [boom, boom]
    res = state.query_executor.validate_and_execute_dax("EVALUATE Sales", bypass_cache=True)
    assert res["success"] is False
    assert res["error_type"] == "connection_not_open"
    assert any("reconnect" in s.lower() for s in res["suggestions"])


def test_broken_connection_auto_recovers_instead_of_not_connected(wired):
    state, cm, _server, opened = wired
    cm.get_connection().break_()
    assert cm.is_connected() is False
    assert state.is_connected() is True, "is_connected() must auto-reconnect once"
    assert len(opened) == 2


def test_auto_reconnect_is_throttled(wired, monkeypatch):
    state, cm, _server, _ = wired
    calls = []
    monkeypatch.setattr(cm, "reconnect", lambda **kw: calls.append(kw) or {"success": False})
    cm.get_connection().break_()
    assert state.is_connected() is False
    assert state.is_connected() is False
    assert len(calls) == 1, "second check within the throttle window must not reconnect"


def test_command_timeout_stays_below_dispatcher_budget(wired):
    """B-19: ADOMD CommandTimeout < 04_Run_DAX's 60 s dispatcher timeout."""
    state, _cm, server, _ = wired
    seen = []

    def capture(text):
        return [{"[v]": 1}]

    server.default = capture
    from tests import fake_adomd

    original = fake_adomd.FakeCommand.ExecuteReader

    def spy(self):
        seen.append(self.CommandTimeout)
        return original(self)

    fake_adomd.FakeCommand.ExecuteReader = spy
    try:
        state.query_executor.validate_and_execute_dax("EVALUATE {1}", bypass_cache=True)
        with command_budget(12):
            state.query_executor.validate_and_execute_dax("EVALUATE {2}", bypass_cache=True)
    finally:
        fake_adomd.FakeCommand.ExecuteReader = original
    assert 0 < seen[0] < 60
    assert seen[1] <= 12


def test_command_budget_nesting_keeps_the_tighter_budget():
    with command_budget(100):
        with command_budget(10):
            assert effective_command_timeout(55) <= 10
        assert effective_command_timeout(55) > 10
    assert effective_command_timeout(55) == 55


def test_gate_is_reentrant_for_the_holding_thread():
    gate = CommandGate(default_timeout=0.1)
    with gate.hold(purpose="outer"):
        with gate.hold(purpose="inner"):
            assert gate.status()["busy"] is True
    assert gate.status()["busy"] is False


def test_gate_busy_exception_names_the_holder():
    gate = CommandGate(default_timeout=0.05)
    entered = threading.Event()
    leave = threading.Event()

    def hold():
        with gate.hold(purpose="export"):
            entered.set()
            leave.wait(1)

    t = threading.Thread(target=hold)
    t.start()
    entered.wait(1)
    with pytest.raises(ConnectionBusy) as exc:
        with gate.hold():
            pass
    leave.set()
    t.join()
    assert "export" in (exc.value.holder or "")


@pytest.mark.parametrize(
    "msg",
    [
        "Root element is missing.",
        "Cannot write to a BufferedStream while the read buffer is not empty",
        "An existing connection was forcibly closed by the remote host",
        "The connection is not open.",
    ],
)
def test_transport_tokens_classified_as_connection_failures(msg):
    assert is_connection_failure(msg)


def test_dax_errors_are_not_connection_failures():
    assert not is_connection_failure("Query (1, 10) The column 'X' cannot be found.")
