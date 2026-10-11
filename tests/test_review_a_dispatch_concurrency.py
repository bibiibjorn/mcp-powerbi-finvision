"""A-03 / A-04 / L-16 + write serialisation: timeouts, cancellation, orphaned
workers, the process-wide write lock and model-generation bumps.

Real ToolDispatcher, real worker pools, real asyncio loop; handlers are fakes
that block on threading.Events the test releases (no real sleeps left behind).
"""

from __future__ import annotations

import asyncio
import threading
import time
import types

import pytest

import server.dispatch as dispatch_mod
from tests._mcp_harness import make_dispatcher, tool


@pytest.fixture
def fake_connection_state(monkeypatch):
    """Stand-in for WP1's connection_state hooks (getattr-guarded in dispatch)."""
    cs = types.SimpleNamespace(cancel_calls=0, bumps=[])

    def cancel_active_queries():
        cs.cancel_calls += 1
        return 1

    def bump_model_generation(reason):
        cs.bumps.append(reason)
        return len(cs.bumps)

    cs.cancel_active_queries = cancel_active_queries
    cs.bump_model_generation = bump_model_generation
    monkeypatch.setattr(dispatch_mod, "_connection_state", lambda: cs)
    return cs


def _run(coro):
    return asyncio.run(coro)


def _write_tool(name, handler):
    return tool(name, handler, read_only=False, destructive=True, category="model")


# ---------------------------------------------------------------------------
# A-03: a timed-out WRITE is never advertised as "retry"
# ---------------------------------------------------------------------------


def test_timed_out_write_reports_outcome_unknown_and_refuses_identical_retry(
    monkeypatch, fake_connection_state
):
    release = threading.Event()
    applied = []

    def create_measure(args):
        release.wait(5)
        applied.append(args.get("measure_name"))
        return {"success": True}

    d = make_dispatcher(_write_tool("02_Model_Operations", create_measure))
    monkeypatch.setattr(d, "_get_timeout", lambda t: 0.3)
    args = {"object_type": "measure", "operation": "create", "measure_name": "Total Sales"}

    async def scenario():
        first = await d.dispatch_async("02_Model_Operations", dict(args))
        retry = await d.dispatch_async("02_Model_Operations", dict(args))
        return first, retry

    try:
        first, retry = _run(scenario())
    finally:
        release.set()
    time.sleep(0.2)

    assert first["success"] is False
    assert first["error_type"] == "timeout_outcome_unknown"
    assert first["may_still_be_running"] is True
    assert "retry_with" not in first  # never the old "ensure and retry" advice
    assert first["verify_with"]["tool"] == "02_Model_Operations"
    assert first["verify_with"]["args"]["operation"] == "list"
    assert retry["error_type"] == "write_in_progress"
    assert retry["operation_id"] == first["operation_id"]
    # The orphaned worker finished ONCE; the refused retry never applied.
    assert applied == ["Total Sales"]
    # In-flight ADOMD commands were cancelled on the timeout.
    time.sleep(0.05)
    assert fake_connection_state.cancel_calls >= 1


def test_timed_out_read_is_a_plain_retryable_timeout(monkeypatch, fake_connection_state):
    release = threading.Event()
    d = make_dispatcher(tool("04_Run_DAX", lambda a: (release.wait(5), {"success": True})[1]))
    monkeypatch.setattr(d, "_get_timeout", lambda t: 0.2)
    try:
        out = _run(d.dispatch_async("04_Run_DAX", {"query": "EVALUATE {1}"}))
    finally:
        release.set()
    assert out["error_type"] == "timeout"
    assert out["retry_with"]["tool"] == "01_Connection"


def test_write_that_times_out_while_queued_for_the_lock_never_executes(
    monkeypatch, fake_connection_state
):
    """Writes are serialised; a queued write whose caller gave up is skipped
    and reported executed=false (safe to retry) — not outcome-unknown."""
    release = threading.Event()
    ran = []

    def slow_write(args):
        ran.append(args["tag"])
        if args["tag"] == "first":
            release.wait(5)
        return {"success": True}

    d = make_dispatcher(_write_tool("03_Batch_Operations", slow_write))
    timeouts = {"v": 5.0}
    monkeypatch.setattr(d, "_get_timeout", lambda t: timeouts["v"])

    async def scenario():
        first = asyncio.ensure_future(
            d.dispatch_async(
                "03_Batch_Operations",
                {"object_type": "measures", "batch_operation": "create", "tag": "first"},
            )
        )
        await asyncio.sleep(0.15)  # first now holds the write lock
        timeouts["v"] = 0.3
        second = await d.dispatch_async(
            "03_Batch_Operations",
            {"object_type": "measures", "batch_operation": "create", "tag": "second"},
        )
        release.set()
        return await first, second

    first, second = _run(scenario())
    time.sleep(0.3)
    assert first["success"] is True
    assert second["error_type"] == "timeout"
    assert second["executed"] is False
    assert ran == ["first"]  # the abandoned queued write never ran


def test_writes_are_serialised_but_reads_run_concurrently(monkeypatch, fake_connection_state):
    active = {"w": 0, "w_max": 0, "r": 0, "r_max": 0}
    lock = threading.Lock()

    def track(kind):
        def handler(args):
            with lock:
                active[kind] += 1
                active[f"{kind}_max"] = max(active[f"{kind}_max"], active[kind])
            time.sleep(0.15)
            with lock:
                active[kind] -= 1
            return {"success": True}

        return handler

    d = make_dispatcher(
        _write_tool("02_Model_Operations", track("w")),
        tool("04_Run_DAX", track("r")),
    )
    monkeypatch.setattr(d, "_get_timeout", lambda t: 10)
    write_args = {"object_type": "measure", "operation": "update"}

    async def scenario():
        calls = [d.dispatch_async("02_Model_Operations", dict(write_args, n=i)) for i in range(3)]
        calls += [d.dispatch_async("04_Run_DAX", {"query": f"EVALUATE {{{i}}}"}) for i in range(3)]
        return await asyncio.gather(*calls)

    results = _run(scenario())
    assert all(r["success"] for r in results)
    assert active["w_max"] == 1
    assert active["r_max"] >= 2


def test_model_generation_bumped_after_successful_write_only(monkeypatch, fake_connection_state):
    outcomes = iter([{"success": True}, {"success": False, "error": "boom"}])
    d = make_dispatcher(
        _write_tool("02_Model_Operations", lambda a: next(outcomes)),
        tool("04_Run_DAX", lambda a: {"success": True}),
    )
    d.dispatch("02_Model_Operations", {"object_type": "measure", "operation": "update"})
    d.dispatch("02_Model_Operations", {"object_type": "measure", "operation": "delete"})
    d.dispatch("04_Run_DAX", {"query": "EVALUATE {1}"})
    assert fake_connection_state.bumps == ["02_Model_Operations:measure/update"]


def test_hung_live_workers_do_not_starve_offline_tools(monkeypatch, fake_connection_state):
    """A-04: once N live calls are stuck, new LIVE calls fail fast with
    desktop_unresponsive, while offline (PBIP) tools keep working."""
    release = threading.Event()
    d = make_dispatcher(
        tool("04_Run_DAX", lambda a: (release.wait(5), {"success": True})[1]),
        tool("07_PBIP_Operations", lambda a: {"success": True, "offline": True}, category="pbip"),
    )
    d._stuck_limit = 2
    monkeypatch.setattr(d, "_get_timeout", lambda t: 0.2)

    async def scenario():
        stuck = await asyncio.gather(
            d.dispatch_async("04_Run_DAX", {"query": "EVALUATE {1}"}),
            d.dispatch_async("04_Run_DAX", {"query": "EVALUATE {2}"}),
        )
        t0 = time.monotonic()
        refused = await d.dispatch_async("04_Run_DAX", {"query": "EVALUATE {3}"})
        refused_after = time.monotonic() - t0
        offline = await d.dispatch_async("07_PBIP_Operations", {"operation": "query_unused"})
        return stuck, refused, refused_after, offline

    try:
        stuck, refused, refused_after, offline = _run(scenario())
    finally:
        release.set()
    assert [s["error_type"] for s in stuck] == ["timeout", "timeout"]
    assert refused["error_type"] == "desktop_unresponsive"
    assert len(refused["stuck_calls"]) == 2
    assert refused_after < 0.15  # refused immediately, not after another timeout
    assert offline["success"] is True


def test_client_cancellation_sets_the_cancel_event_seen_by_the_handler(
    monkeypatch, fake_connection_state
):
    """notifications/cancelled → the awaiting task is cancelled; the worker's
    cancel event is set so long loops can stop (L-16)."""
    started = threading.Event()
    observed = []

    def handler(args):
        from server.request_context import is_cancelled

        started.set()
        for _ in range(100):
            if is_cancelled():
                observed.append("cancelled")
                return {"success": False, "error": "stopped"}
            time.sleep(0.02)
        observed.append("ran to completion")
        return {"success": True}

    d = make_dispatcher(tool("09_Profile", handler))
    monkeypatch.setattr(d, "_get_timeout", lambda t: 10)

    async def scenario():
        task = asyncio.ensure_future(d.dispatch_async("09_Profile", {"operation": "page"}))
        while not started.is_set():
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    _run(scenario())
    deadline = time.monotonic() + 2
    while not observed and time.monotonic() < deadline:
        time.sleep(0.02)
    assert observed == ["cancelled"]
    time.sleep(0.05)
    assert fake_connection_state.cancel_calls >= 1


def test_raise_if_cancelled_inside_handler_maps_to_cancelled_envelope(monkeypatch):
    from server import request_context

    ev = threading.Event()
    ev.set()

    def handler(args):
        request_context.raise_if_cancelled()
        return {"success": True}

    d = make_dispatcher(tool("09_Profile", handler))
    token = request_context.CANCEL_EVENT.set(ev)
    try:
        out = d.dispatch("09_Profile", {"operation": "page"})
    finally:
        request_context.CANCEL_EVENT.reset(token)
    assert out["success"] is False
    assert out["error_type"] == "cancelled"
