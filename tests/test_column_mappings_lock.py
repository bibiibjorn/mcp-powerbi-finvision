"""Regression: _ensure_column_mappings is guarded by _mapping_lock (P1).

Without the double-checked lock its table sibling has, two concurrent first
callers both pass the `is not None` check and both issue INFO.COLUMNS() — 2x
the expensive DMV roundtrip. The lock must collapse a concurrent burst to one
query.
"""

import threading
import time

from core.infrastructure.query_executor import OptimizedQueryExecutor


def test_concurrent_column_mappings_issue_single_query():
    qe = OptimizedQueryExecutor.__new__(OptimizedQueryExecutor)
    qe._column_name_by_id = None
    qe._connection_state = None

    call_count = {"n": 0}
    lock = threading.Lock()

    def fake_query(query, top_n=0, **kw):
        with lock:
            call_count["n"] += 1
        # Simulate a slow DMV roundtrip so the threads actually overlap.
        time.sleep(0.1)
        return {"success": True, "rows": [{"[ID]": 1, "[ExplicitName]": "Amount"}]}

    qe.validate_and_execute_dax = fake_query

    threads = [threading.Thread(target=qe._ensure_column_mappings) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert qe._column_name_by_id == {1: "Amount"}
    assert (
        call_count["n"] == 1
    ), f"expected one INFO.COLUMNS() query under concurrency, got {call_count['n']}"
