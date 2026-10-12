"""Tests for the extended trace event coverage in core/query_plan/capturer.py.

F-24 (2026-09-22 review): the counters used to be filled by a loop over a
``_NullTrace`` that never yielded an event, so DirectQuery / agg-rewrite /
cache-removal counts were always 0 and the rules built on them could never
fire. The dead hook is gone; these tests now feed the NATIVE trace result
(its ``timeline`` rows, ``agg_rewrites``, ``dq_ms``, ``se_cache_hits`` and
``se_events``) and assert the counters come from there.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.query_plan import capturer as cap_mod


@pytest.fixture
def fake_state(monkeypatch):
    """Stub connection_state so capture() reaches the native-result mapping."""
    state = MagicMock()
    state.is_connected.return_value = True
    executor = MagicMock()
    executor.validate_and_execute_dax.return_value = {"success": True, "ms": 5}
    state.query_executor = executor
    state.dax_query_executor = executor

    import core.infrastructure.connection_state as cs_mod

    monkeypatch.setattr(cs_mod, "connection_state", state)
    return state


def _install_native(monkeypatch, **native):
    base = {
        "logical_plan": "",
        "physical_plan": "",
        "se_events": [],
        "cache_cleared": True,
        "total_ms": 5,
        "timeline": [],
        "agg_rewrites": [],
    }
    base.update(native)
    monkeypatch.setattr(cap_mod, "_native_capture", lambda dax_query, clear_cache: base)


def test_null_trace_hook_is_gone():
    assert not hasattr(cap_mod, "_open_trace")
    assert not hasattr(cap_mod, "_NullTrace")


def test_directquery_events_counted(fake_state, monkeypatch):
    _install_native(
        monkeypatch,
        timeline=[{"class": "DirectQueryEnd"}, {"class": "DirectQueryEnd"}, {"class": "x"}],
        dq_ms=35,
    )
    cap = cap_mod.capture("EVALUATE 'X'")
    assert cap["success"] is True
    assert cap["dq_query_count"] == 2
    assert cap["dq_total_ms"] == 35


def test_aggregate_rewrite_status_classification(fake_state, monkeypatch):
    _install_native(
        monkeypatch,
        agg_rewrites=[
            {"text": "rewrote -> agg_sales", "subclass": "MatchFound"},
            {"text": "could not rewrite", "subclass": "NoMatch"},
            {"text": "trying...", "subclass": "Attempting"},
        ],
    )
    cap = cap_mod.capture("EVALUATE 'X'")
    assert [r["status"] for r in cap["agg_rewrites"]] == ["matched", "nomatch", "attempted"]


def test_aggregate_rewrite_integer_subclass(fake_state, monkeypatch):
    """Live CLR traces emit integer EventSubclass values (1=MatchFound, 2=NoMatch)."""
    _install_native(
        monkeypatch,
        agg_rewrites=[
            {"text": "rewrote", "subclass": 1},
            {"text": "missed", "subclass": 2},
            {"text": "?", "subclass": 99},
        ],
    )
    cap = cap_mod.capture("EVALUATE 'X'")
    assert [r["status"] for r in cap["agg_rewrites"]] == ["matched", "nomatch", "attempted"]


def test_aggregate_rewrite_without_subclass_is_attempted(fake_state, monkeypatch):
    """pbi_perf_core currently keeps only {text, start} -> 'attempted', never a guess."""
    _install_native(monkeypatch, agg_rewrites=[{"text": "rewrote", "start": 0}])
    cap = cap_mod.capture("EVALUATE 'X'")
    assert cap["agg_rewrites"][0]["status"] == "attempted"
    assert cap["agg_rewrites"][0]["agg"] == "rewrote"


def test_se_cache_match_and_removal(fake_state, monkeypatch):
    _install_native(
        monkeypatch,
        se_cache_hits=3,
        timeline=[{"class": "VertiPaqSEQueryCacheRemoval"}],
    )
    cap = cap_mod.capture("EVALUATE 'X'")
    assert cap["se_cache_match_count"] == 3
    assert cap["se_cache_removal_count"] == 1


def test_callback_dataid_detection_in_xmsql(fake_state, monkeypatch):
    """CallbackDataID substring in the SE queries increments callback_count."""
    _install_native(
        monkeypatch,
        se_events=[
            {"query": "SELECT [t].[c], CallbackDataID(...) FROM [t]"},
            {"query": "SELECT [t].[c2] FROM [t]"},
            {"query": "with callbackDATAid here too"},  # case-insensitive
        ],
    )
    cap = cap_mod.capture("EVALUATE 'X'")
    assert cap["callback_count"] == 2
    assert len(cap["xmsql_queries"]) == 3


def test_no_new_events_zero_counts(fake_state, monkeypatch):
    """All accumulators default to 0 / [] when no events occur."""
    _install_native(monkeypatch)
    cap = cap_mod.capture("EVALUATE 'X'")
    assert cap["dq_query_count"] == 0
    assert cap["dq_total_ms"] == 0
    assert cap["se_cache_match_count"] == 0
    assert cap["se_cache_removal_count"] == 0
    assert cap["agg_rewrites"] == []
    assert cap["callback_count"] == 0


def test_agg_rewrites_capped(fake_state, monkeypatch):
    """The list is capped at _MAX_AGG_REWRITES to avoid unbounded growth."""
    _install_native(
        monkeypatch,
        agg_rewrites=[
            {"text": f"r{i}", "subclass": "Attempting"}
            for i in range(cap_mod._MAX_AGG_REWRITES + 25)
        ],
    )
    cap = cap_mod.capture("EVALUATE 'X'")
    assert len(cap["agg_rewrites"]) == cap_mod._MAX_AGG_REWRITES
