"""F-27: optional ``request_id`` on writes -> idempotent retry.

The async dispatcher records ``(tool, args fingerprint, request_id) -> result`` so a retry
after ``timeout_outcome_unknown`` returns the RECORDED outcome (``replayed: true``) - or
``write_in_progress`` while the first call still runs - instead of applying the write twice.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from server import dispatch as dispatch_mod
from server.dispatch import ToolDispatcher
from server.registry import HandlerRegistry, ToolDefinition

WRITE_ARGS = {"object_type": "measure", "operation": "create", "measure_name": "M"}


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def _clean_store():
    dispatch_mod._IDEMPOTENCY.clear()
    dispatch_mod._ORPHANS.clear()
    yield
    dispatch_mod._IDEMPOTENCY.clear()
    dispatch_mod._ORPHANS.clear()


def _dispatcher(handler, *, name="02_Model_Operations"):
    reg = HandlerRegistry()
    reg.register(
        ToolDefinition(
            name=name,
            description="t",
            handler=handler,
            input_schema={
                "type": "object",
                "properties": {"object_type": {}, "operation": {}, "measure_name": {}},
            },
            category="model",
        )
    )
    d = ToolDispatcher()
    d.registry = reg
    return d


def test_retry_with_the_same_request_id_replays_the_recorded_outcome():
    calls = []

    def _h(args):
        calls.append(args)
        return {"success": True, "created": "M", "n": len(calls)}

    d = _dispatcher(_h)
    first = _run(d.dispatch_async("02_Model_Operations", {**WRITE_ARGS, "request_id": "r-1"}))
    second = _run(d.dispatch_async("02_Model_Operations", {**WRITE_ARGS, "request_id": "r-1"}))
    assert len(calls) == 1
    assert first["n"] == 1 and "replayed" not in first
    assert second["replayed"] is True and second["request_id"] == "r-1"
    assert second["n"] == 1 and second["success"] is True
    assert "NOT executed again" in second["replay_note"]


def test_a_different_request_id_or_different_args_executes_again():
    calls = []
    d = _dispatcher(lambda a: calls.append(a) or {"success": True})
    _run(d.dispatch_async("02_Model_Operations", {**WRITE_ARGS, "request_id": "a"}))
    _run(d.dispatch_async("02_Model_Operations", {**WRITE_ARGS, "request_id": "b"}))
    _run(
        d.dispatch_async(
            "02_Model_Operations", {**WRITE_ARGS, "measure_name": "N", "request_id": "a"}
        )
    )
    assert len(calls) == 3


def test_without_request_id_nothing_is_recorded():
    calls = []
    d = _dispatcher(lambda a: calls.append(a) or {"success": True})
    _run(d.dispatch_async("02_Model_Operations", dict(WRITE_ARGS)))
    _run(d.dispatch_async("02_Model_Operations", dict(WRITE_ARGS)))
    assert len(calls) == 2


def test_request_id_is_not_flagged_as_an_unknown_argument():
    d = _dispatcher(lambda a: {"success": True})
    out = _run(d.dispatch_async("02_Model_Operations", {**WRITE_ARGS, "request_id": "z"}))
    assert "unknown_args" not in out


def test_retry_after_a_timeout_gets_the_recorded_outcome_once_the_worker_finishes(monkeypatch):
    release = threading.Event()
    calls = []

    def _slow(args):
        calls.append(1)
        release.wait(5)
        return {"success": True, "created": "M"}

    d = _dispatcher(_slow)
    monkeypatch.setattr(d, "_get_timeout", lambda tool: 0.2)
    timed_out = _run(d.dispatch_async("02_Model_Operations", {**WRITE_ARGS, "request_id": "t-1"}))
    assert timed_out["error_type"] == "timeout_outcome_unknown"
    assert timed_out["request_id"] == "t-1"
    assert "SAME request_id" in timed_out["safe_retry"]

    # still running -> write_in_progress (keyed on the request_id), NOT a second execution
    busy = _run(d.dispatch_async("02_Model_Operations", {**WRITE_ARGS, "request_id": "t-1"}))
    assert busy["error_type"] == "write_in_progress"
    assert busy["request_id"] == "t-1"

    release.set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        entry = dispatch_mod._IDEMPOTENCY._entries
        if entry and all(e["state"] == "done" for e in entry.values()):
            break
        time.sleep(0.02)
    monkeypatch.setattr(d, "_get_timeout", lambda tool: 5.0)
    replay = _run(d.dispatch_async("02_Model_Operations", {**WRITE_ARGS, "request_id": "t-1"}))
    assert replay["replayed"] is True and replay["created"] == "M"
    assert len(calls) == 1


def test_timeout_without_request_id_advises_passing_one(monkeypatch):
    release = threading.Event()
    d = _dispatcher(lambda a: release.wait(5) or {"success": True})
    monkeypatch.setattr(d, "_get_timeout", lambda tool: 0.2)
    out = _run(d.dispatch_async("02_Model_Operations", dict(WRITE_ARGS)))
    release.set()
    assert out["error_type"] == "timeout_outcome_unknown"
    assert "request_id" in out["safe_retry"]


def test_a_recorded_failure_is_replayed_only_while_the_model_generation_is_unchanged(monkeypatch):
    calls = []

    def _h(args):
        calls.append(1)
        return {"success": False, "error_type": "operation_failed", "error": "boom"}

    d = _dispatcher(_h)
    gen = {"v": 1}
    monkeypatch.setattr(
        dispatch_mod._IdempotencyStore, "_generation", staticmethod(lambda: gen["v"])
    )
    args = {**WRITE_ARGS, "request_id": "f-1"}
    _run(d.dispatch_async("02_Model_Operations", args))
    again = _run(d.dispatch_async("02_Model_Operations", args))
    assert again["replayed"] is True and len(calls) == 1
    gen["v"] = 2  # another write changed the model since
    _run(d.dispatch_async("02_Model_Operations", args))
    assert len(calls) == 2


def test_a_recorded_success_survives_a_generation_change():
    calls = []
    d = _dispatcher(lambda a: calls.append(1) or {"success": True})
    args = {**WRITE_ARGS, "request_id": "s-1"}
    _run(d.dispatch_async("02_Model_Operations", args))
    dispatch_mod._bump_model_generation("other write")
    again = _run(d.dispatch_async("02_Model_Operations", args))
    assert again["replayed"] is True and len(calls) == 1


def test_entries_expire_after_the_ttl(monkeypatch):
    calls = []
    d = _dispatcher(lambda a: calls.append(1) or {"success": True})
    monkeypatch.setattr(dispatch_mod._IdempotencyStore, "ttl_s", staticmethod(lambda: 0.05))
    args = {**WRITE_ARGS, "request_id": "e-1"}
    _run(d.dispatch_async("02_Model_Operations", args))
    time.sleep(0.15)
    _run(d.dispatch_async("02_Model_Operations", args))
    assert len(calls) == 2


def test_ttl_zero_disables_the_feature(monkeypatch):
    calls = []
    d = _dispatcher(lambda a: calls.append(1) or {"success": True})
    monkeypatch.setattr(dispatch_mod._IdempotencyStore, "ttl_s", staticmethod(lambda: 0))
    args = {**WRITE_ARGS, "request_id": "d-1"}
    _run(d.dispatch_async("02_Model_Operations", args))
    _run(d.dispatch_async("02_Model_Operations", args))
    assert len(calls) == 2


def test_reads_ignore_request_id():
    calls = []
    d = _dispatcher(lambda a: calls.append(1) or {"success": True}, name="04_Run_DAX")
    for _ in range(2):
        _run(d.dispatch_async("04_Run_DAX", {"request_id": "x"}))
    assert len(calls) == 2


def test_fingerprint_ignores_request_id():
    a = dispatch_mod._args_fingerprint("T", {"a": 1, "request_id": "x"})
    b = dispatch_mod._args_fingerprint("T", {"a": 1, "request_id": "y"})
    c = dispatch_mod._args_fingerprint("T", {"a": 1})
    assert a == b == c


def test_cancelled_before_start_leaves_no_entry():
    d = _dispatcher(lambda a: {"success": True})
    store = dispatch_mod._IDEMPOTENCY
    key = ("fp", "rid")
    assert store.begin(key, 1, "lbl")[0] == "run"
    store.finish(key, 1, {"success": False, "executed": False, "error_type": "cancelled"})
    assert store._entries == {}
    assert store.begin(key, 2, "lbl")[0] == "run"
    store.discard(key, 2)
    assert store._entries == {}
    del d


def test_an_oversized_result_is_recorded_as_a_verdict_only():
    d = _dispatcher(lambda a: {"success": True, "blob": "x" * 300_000})
    args = {**WRITE_ARGS, "request_id": "big"}
    first = _run(d.dispatch_async("02_Model_Operations", args))
    assert len(first["blob"]) == 300_000
    again = _run(d.dispatch_async("02_Model_Operations", args))
    assert again["replayed"] is True and again["success"] is True
    assert "blob" not in again and "result_omitted" in again
