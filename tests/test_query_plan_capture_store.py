"""T0-4: thread-safety tests for core.query_plan.capturer._CAPTURE_STORE.

Previously remember() did `pop(next(iter(_CAPTURE_STORE)))` for LRU eviction
without a lock. The MCP dispatcher uses a thread pool, so two concurrent
13_Query_Plan:capture calls would race and either raise
``RuntimeError: dictionary changed size during iteration`` or silently lose
a capture. The fix uses ``threading.Lock`` around mutation; these tests
hammer remember/recall from many threads to surface any regression.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from core.query_plan import capturer


def _store_view() -> dict:
    """Direct view of the store (testing only)."""
    with capturer._CAPTURE_LOCK:
        return dict(capturer._CAPTURE_STORE)


def _make_capture(cid: str) -> dict:
    return {"capture_id": cid, "logical_plan_rows": [], "physical_plan_rows": []}


def test_remember_concurrent_inserts_do_not_raise() -> None:
    """64 threads each inserting 50 captures must not raise."""
    # Reset for a clean run.
    with capturer._CAPTURE_LOCK:
        capturer._CAPTURE_STORE.clear()

    ids = [uuid.uuid4().hex for _ in range(64 * 50)]

    def worker(start: int) -> int:
        for i in range(50):
            capturer.remember(_make_capture(ids[start * 50 + i]))
        return start

    errors = []
    with ThreadPoolExecutor(max_workers=64) as pool:
        futures = [pool.submit(worker, i) for i in range(64)]
        for f in as_completed(futures):
            try:
                f.result()
            except Exception as e:  # noqa: BLE001
                errors.append(e)
    assert not errors, f"Concurrent inserts raised: {errors}"
    # Store is capped at _MAX_CAPTURES, eviction must have kicked in.
    assert len(_store_view()) <= capturer._MAX_CAPTURES


def test_remember_recall_round_trip() -> None:
    cid = uuid.uuid4().hex
    capturer.remember(_make_capture(cid))
    got = capturer.recall(cid)
    assert got is not None
    assert got["capture_id"] == cid


def test_remember_no_id_is_noop() -> None:
    """A capture missing capture_id is ignored, not stored."""
    before = len(_store_view())
    capturer.remember({"logical_plan_rows": []})  # no capture_id
    after = len(_store_view())
    assert before == after


def test_remember_evicts_oldest() -> None:
    """LRU eviction caps the store at _MAX_CAPTURES."""
    with capturer._CAPTURE_LOCK:
        capturer._CAPTURE_STORE.clear()

    sentinel_cap = capturer._MAX_CAPTURES
    for i in range(sentinel_cap + 10):
        capturer.remember(_make_capture(f"cap-{i:03d}"))
    snap = _store_view()
    assert len(snap) == sentinel_cap
    # The first 10 should be gone.
    for i in range(10):
        assert f"cap-{i:03d}" not in snap
    # The last 32 should remain.
    for i in range(sentinel_cap + 10 - sentinel_cap, sentinel_cap + 10):
        assert f"cap-{i:03d}" in snap


def test_recall_returns_none_for_missing() -> None:
    assert capturer.recall("definitely-not-there") is None
