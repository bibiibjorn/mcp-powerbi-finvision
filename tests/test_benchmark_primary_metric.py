"""S-31 + F-26: ``benchmark_traced`` reports the engine's ``execution_metrics.duration_ms`` as
the PRIMARY number (the trace-window ``total_ms`` is secondary), and emits per-run progress.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.query_plan import capturer as cap_mod


@pytest.fixture
def fake_state(monkeypatch):
    state = MagicMock()
    state.is_connected.return_value = True
    import core.infrastructure.connection_state as cs_mod

    monkeypatch.setattr(cs_mod, "connection_state", state)
    return state


def _native(total_ms, duration_ms=None, **over):
    base = {
        "total_ms": total_ms,
        "fe_ms": 1,
        "se_ms": 2,
        "cache_cleared": True,
        "execution_metrics": ({"duration_ms": duration_ms} if duration_ms is not None else {}),
    }
    base.update(over)
    return base


def test_engine_duration_is_the_primary_number(fake_state, monkeypatch):
    durations = iter([100.0, 120.0, 40.0, 42.0])
    monkeypatch.setattr(
        cap_mod,
        "_native_capture",
        lambda q, clear: _native(total_ms=300, duration_ms=next(durations)),
    )
    res = cap_mod.benchmark_traced("EVALUATE {1}", cold_runs=2, warm_runs=2, warmup=0)
    assert res["cold"]["primary_metric"] == "execution_metrics.duration_ms"
    assert res["cold"]["primary_ms"]["min_ms"] == 100.0
    assert res["cold"]["duration_ms"]["avg_ms"] == 110.0
    assert res["cold"]["total_ms"]["avg_ms"] == 300.0  # secondary, unchanged key
    assert res["cold"]["summary"]["baseline_ms"] == 100.0  # baseline protocol on the primary
    assert res["warm"]["primary_ms"]["max_ms"] == 42.0
    assert res["cold"]["runs"][0]["duration_ms"] == 100.0


def test_falls_back_to_total_ms_when_the_engine_gave_no_duration(fake_state, monkeypatch):
    monkeypatch.setattr(cap_mod, "_native_capture", lambda q, clear: _native(total_ms=55))
    res = cap_mod.benchmark_traced("EVALUATE {1}", cold_runs=1, warm_runs=1, warmup=0)
    assert res["cold"]["primary_metric"] == "total_ms"
    assert res["cold"]["primary_ms"]["min_ms"] == 55
    assert "duration_ms" not in res["cold"]


def test_a_partial_set_of_durations_does_not_mix_metrics(fake_state, monkeypatch):
    seq = iter([_native(10, 7.0), _native(20, None)])
    monkeypatch.setattr(cap_mod, "_native_capture", lambda q, clear: next(seq))
    res = cap_mod.benchmark_traced("EVALUATE {1}", cold_runs=2, warm_runs=0, warmup=0)
    assert res["cold"]["primary_metric"] == "total_ms"
    assert res["cold"]["primary_ms"]["avg_ms"] == 15.0


def test_per_run_progress_is_emitted(fake_state, monkeypatch):
    seen = []
    import server.progress as progress

    monkeypatch.setattr(
        progress, "emit_progress", lambda done, total, msg: seen.append((done, total, msg))
    )
    monkeypatch.setattr(cap_mod, "_native_capture", lambda q, clear: _native(5, 5.0))
    cap_mod.benchmark_traced("EVALUATE {1}", cold_runs=2, warm_runs=1, warmup=0)
    assert [(d, t) for d, t, _ in seen] == [(0, 3), (1, 3), (2, 3)]
    assert seen[0][2] == "cold run 1 of 2" and seen[2][2] == "warm run 1 of 1"


def test_ab_and_plain_benchmark_emit_progress(fake_state, monkeypatch):
    seen = []
    import server.progress as progress

    monkeypatch.setattr(progress, "emit_progress", lambda d, t, m: seen.append((d, t)))
    monkeypatch.setattr(cap_mod, "_native_capture", lambda q, clear: _native(5, 5.0))
    cap_mod.benchmark_ab("A", "B", runs=2, warmup=0)
    assert seen == [(0, 2), (1, 2)]
    seen.clear()
    fake_state.query_executor.validate_and_execute_dax.return_value = {"success": True, "ms": 3}
    fake_state.query_executor.clear_vertipaq_cache.return_value = {"cache_cleared": True}
    cap_mod.benchmark("EVALUATE {1}", runs=3, warmup=0)
    assert seen == [(0, 3), (1, 3), (2, 3)]


def test_progress_failure_never_breaks_the_benchmark(fake_state, monkeypatch):
    import server.progress as progress

    def _boom(*a, **k):
        raise RuntimeError("no session")

    monkeypatch.setattr(progress, "emit_progress", _boom)
    monkeypatch.setattr(cap_mod, "_native_capture", lambda q, clear: _native(5, 5.0))
    assert cap_mod.benchmark_traced("EVALUATE {1}", cold_runs=1, warm_runs=0, warmup=0)["success"]
