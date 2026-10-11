"""WP-P wave 2 -- ``get_tmsl_definition`` memo (P-03 / review C-22).

``06 full``, ``08_Documentation_Word``, ``06 compare`` and the M-practices scan
each opened a brand-new AMO session + serialised the whole model on every call.
The result is now memoised per (connection, database, connection generation,
model generation) and served only after the external-change probe says the model
is unchanged. No live AMO is touched here: the fetch itself is stubbed.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import core.infrastructure.query_executor as qe_mod
from core.infrastructure.query_executor import OptimizedQueryExecutor


class _State:
    def __init__(self):
        self.model_generation = 0
        self.connection_generation = 0
        self.verdict = False  # check_model_version(): False = unchanged
        self._model_version_fingerprint = "fp"
        self.probes = 0

    def check_model_version(self, force=False):
        self.probes += 1
        return self.verdict


class _Exec(OptimizedQueryExecutor):
    def __init__(self, state, db="DB"):
        self._connection_state = state
        self._db = db
        self.fetches = 0
        self.fail_next = False

    def _get_database_name(self):
        return self._db

    def _cache_identity(self):
        s = self._connection_state
        return ("conn", self._db, s.connection_generation, s.model_generation)

    def _fetch_tmsl_definition(self):
        self.fetches += 1
        if self.fail_next:
            return {"success": False, "error": "boom"}
        return {"success": True, "tmsl": f'{{"n": {self.fetches}}}', "database_name": self._db}


@pytest.fixture(autouse=True)
def _amo(monkeypatch):
    monkeypatch.setattr(qe_mod, "AMO_AVAILABLE", True)


def test_unchanged_model_is_fetched_once():
    ex = _Exec(_State())
    a = ex.get_tmsl_definition()
    b = ex.get_tmsl_definition()
    c = ex.get_tmsl_definition()
    assert ex.fetches == 1 and a == b == c and a["tmsl"] == '{"n": 1}'
    b["tmsl"] = "mutated"  # callers get copies
    assert ex.get_tmsl_definition()["tmsl"] == '{"n": 1}'


def test_a_write_bumps_the_generation_and_the_next_call_refetches():
    state = _State()
    ex = _Exec(state)
    ex.get_tmsl_definition()
    state.model_generation += 1  # write_lane.commit -> bump_model_generation
    assert ex.get_tmsl_definition()["tmsl"] == '{"n": 2}'
    assert ex.fetches == 2
    ex.get_tmsl_definition()
    assert ex.fetches == 2


def test_connection_swap_refetches():
    state = _State()
    ex = _Exec(state)
    ex.get_tmsl_definition()
    state.connection_generation += 1
    state.model_generation += 1
    ex.get_tmsl_definition()
    assert ex.fetches == 2
    ex._db = "OtherDb"  # another database on the same connection
    ex.get_tmsl_definition()
    assert ex.fetches == 3


def test_external_change_or_unverifiable_model_is_not_served_from_memo():
    state = _State()
    ex = _Exec(state)
    ex.get_tmsl_definition()
    state.verdict = True  # probe saw a Desktop-side edit
    ex.get_tmsl_definition()
    assert ex.fetches == 2
    state.verdict = None  # probe could not verify
    ex.get_tmsl_definition()
    assert ex.fetches == 3


def test_no_baseline_fingerprint_means_no_memo():
    state = _State()
    state._model_version_fingerprint = None
    state.check_model_version = lambda force=False: None  # cannot establish a baseline
    ex = _Exec(state)
    ex.get_tmsl_definition()
    ex.get_tmsl_definition()
    assert ex.fetches == 2


def test_executor_without_connection_state_never_memoises():
    ex = _Exec(None)
    ex._cache_identity = lambda: ("c", "DB", 0, 0)
    ex.get_tmsl_definition()
    ex.get_tmsl_definition()
    assert ex.fetches == 2


def test_failures_are_not_memoised():
    state = _State()
    ex = _Exec(state)
    ex.fail_next = True
    assert ex.get_tmsl_definition()["success"] is False
    ex.fail_next = False
    assert ex.get_tmsl_definition()["success"] is True
    assert ex.fetches == 2


def test_generation_change_during_the_fetch_is_not_stored():
    state = _State()
    ex = _Exec(state)
    real = ex._fetch_tmsl_definition

    def racing():
        out = real()
        state.model_generation += 1  # a write landed while we were serialising
        return out

    ex._fetch_tmsl_definition = racing
    ex.get_tmsl_definition()
    ex._fetch_tmsl_definition = real
    ex.get_tmsl_definition()
    assert ex.fetches == 2  # the racy result was NOT memoised under the new generation


def test_flush_cache_and_connection_cache_reset_drop_the_memo():
    ex = _Exec(_State())
    ex.query_cache = {}
    import threading

    ex._query_cache_lock = threading.Lock()
    ex._health_lock = threading.Lock()
    ex.get_tmsl_definition()
    ex.flush_cache()
    ex.get_tmsl_definition()
    assert ex.fetches == 2
    ex.invalidate_connection_cache()
    ex.get_tmsl_definition()
    assert ex.fetches == 3


def test_amo_unavailable_still_reports_cleanly(monkeypatch):
    monkeypatch.setattr(qe_mod, "AMO_AVAILABLE", False)
    assert _Exec(_State()).get_tmsl_definition() == {
        "success": False,
        "error": "AMO/TOM not available",
    }


def test_real_connection_state_bump_makes_the_memo_unreachable():
    from core.infrastructure.connection_state import ConnectionState

    state = ConnectionState()
    state._model_version_fingerprint = "fp"
    state.check_model_version = lambda force=False: False
    ex = _Exec(SimpleNamespace(connection_generation=0, model_generation=0))
    ex._connection_state = state
    ex._cache_identity = lambda: ("c", "DB", state.connection_generation, state.model_generation)
    ex.get_tmsl_definition()
    state.bump_model_generation("test write")
    ex.get_tmsl_definition()
    assert ex.fetches == 2
