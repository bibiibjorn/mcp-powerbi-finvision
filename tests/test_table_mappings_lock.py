"""Regression: get_table_mappings runs INFO.TABLES outside _init_lock (P1).

Holding the RLock across the synchronous INFO.TABLES() DAX query blocks every
other _init_lock-guarded method (manager init, perf baselines, etc.) for the
hundreds of ms the query takes on a large model. The query must run OUTSIDE
the lock; the lock is only held to read the cache and to write the result.
"""

import threading
from unittest.mock import MagicMock

from core.infrastructure.connection_state import ConnectionState


def test_info_tables_query_runs_outside_init_lock():
    cs = ConnectionState()
    observed = {}

    def fake_query(query, top_n=0, **kw):
        # From a SEPARATE thread, try to grab _init_lock. If the calling
        # thread (get_table_mappings) still holds it, this fails. RLock is
        # reentrant only on the same thread, so a foreign thread is a true probe.
        def grab():
            got = cs._init_lock.acquire(timeout=0.3)
            observed["other_thread_acquired"] = got
            if got:
                cs._init_lock.release()

        t = threading.Thread(target=grab)
        t.start()
        t.join()
        return {"success": True, "rows": [{"[Name]": "Sales", "[ID]": 1}]}

    cs.query_executor = MagicMock()
    cs.query_executor.validate_and_execute_dax = fake_query

    id_by_name, name_by_id = cs.get_table_mappings(refresh=True)

    assert id_by_name == {"Sales": 1}
    assert name_by_id == {1: "Sales"}
    assert (
        observed.get("other_thread_acquired") is True
    ), "_init_lock was held across the INFO.TABLES() query"
