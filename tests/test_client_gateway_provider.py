"""Tests of client_gateway.provider with a fake Desktop backend (synthetic names only)."""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import pytest

from client_gateway.provider import Provider, instance_key, policy_settings, source_label
from client_gateway.state import GatewayState
from core.client_guard.detector import DetectionResult, ValueBatch

NAME_A = "Zorvex Holding"
NAME_B = "Quillan Trust"
NAME_C = "Brindle Capital"


class FakeTarget:
    def __init__(self, backend: FakeBackend, instance: dict[str, Any]) -> None:
        self.backend = backend
        self.instance = instance
        self.closed = False

    def run_dax(self, query: str, max_rows: int) -> list[dict[str, Any]]:
        return []

    def fingerprint(self) -> object | None:
        if self.backend.fp_error:
            raise RuntimeError("stale connection")
        return self.backend.fingerprints.get(instance_key(self.instance))

    def close(self) -> None:
        self.closed = True


class FakeBackend:
    def __init__(self, instances: list[dict[str, Any]]) -> None:
        self.current = instances
        self.fingerprints: dict[str, object] = {instance_key(i): 1 for i in instances}
        self.opened: list[FakeTarget] = []
        self.open_error: Exception | None = None
        self.list_error: Exception | None = None
        self.fp_error = False

    def instances(self) -> list[dict[str, Any]]:
        if self.list_error:
            raise self.list_error
        return list(self.current)

    def open(self, instance: dict[str, Any]) -> FakeTarget:
        if self.open_error:
            raise self.open_error
        target = FakeTarget(self, instance)
        self.opened.append(target)
        return target


class FakeDetect:
    """detect_fn double: per call returns the next result; records the arguments."""

    def __init__(self, results: list[DetectionResult] | None = None) -> None:
        self.results = results or []
        self.calls: list[dict[str, Any]] = []
        self.error: Exception | None = None

    def __call__(self, run_dax: Any, **kwargs: Any) -> DetectionResult:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        if self.results:
            return self.results.pop(0) if len(self.results) > 1 else self.results[0]
        return result({"CUS": [NAME_A]})


def result(batches: dict[str, list[str]], ready: bool = True) -> DetectionResult:
    return DetectionResult(
        batches=[ValueBatch(p, tuple(v)) for p, v in batches.items()], ready=ready
    )


def inst(port: int = 51000, pid: int = 4000) -> dict[str, Any]:
    return {"port": port, "msmdsrv_pid": pid, "name": f"{NAME_A}.pbix"}


@pytest.fixture()
def state(tmp_path):
    return GatewayState(home=tmp_path, default_mode="on")


def make(state, backend, detect=None, **kw):
    detect = detect or FakeDetect()
    prov = Provider(
        state, backend, detect_fn=detect, policy_fn=lambda: ("dimensions", ["^x$"]), **kw
    )
    return prov, detect


def test_not_ready_until_the_first_cycle(state):
    make(state, FakeBackend([]))
    assert not state.ready


def test_first_step_adds_values_and_turns_ready(state):
    backend = FakeBackend([inst()])
    found = result({"CUS": [NAME_A, NAME_B], "GLA": ["Cash"]})
    prov, detect = make(state, backend, FakeDetect([found]))
    assert prov.step() == 1
    assert state.ready
    assert state.dictionary_counts() == {"CUS": 2, "GLA": 1}
    assert state.smap.mask_text(f"see {NAME_A}")[0] != f"see {NAME_A}"
    assert state.smap.counts()[source_label(inst())] == {"CUS": 2, "GLA": 1}
    # policy settings are passed to detect()
    assert detect.calls[0] == {"policy": "dimensions", "safe_tables": ["^x$"]}


def test_no_model_open_passes_until_power_bi_work_starts(state):
    # the gateway started and Desktop is not open: nothing to mask, dev work must not be blocked
    prov, detect = make(state, FakeBackend([]))
    assert prov.step() == 0
    assert state.ready and not state.smap.has_values and detect.calls == []


def test_no_instance_is_ready_once_values_are_known(state):
    # closing the model later (or a restart that knows the values) does not close the gate
    backend = FakeBackend([inst()])
    prov, _ = make(state, backend)
    prov.step()
    assert state.ready
    backend.current = []
    prov.step()
    assert state.ready and state.smap.has_values and state.not_ready_info() == {}


def test_a_model_without_client_values_still_opens_the_gate(state):
    backend = FakeBackend([inst()])
    prov, _ = make(state, backend, FakeDetect([result({})]))
    prov.step()
    backend.current = []
    prov.step()
    assert state.ready and not state.smap.has_values  # a complete read found nothing to hide


def test_verify_closes_the_gate_when_a_model_opened_since_the_last_poll(state):
    now = [100.0]
    backend = FakeBackend([inst(51000, 1)])
    prov, detect = make(state, backend, clock=lambda: now[0])
    prov.step()
    assert state.ready
    prov.verify_instances()
    assert state.ready  # nothing new
    backend.current = [inst(51000, 1), inst(52000, 2)]
    backend.fingerprints[instance_key(inst(52000, 2))] = 1
    now[0] += 1
    prov.verify_instances()
    assert state.ready  # throttled: listed less than VERIFY_EVERY_S ago
    now[0] += 5
    prov.verify_instances()
    info = state.not_ready_info()
    assert not state.ready and info["reason"] == "dictionary_incomplete"
    assert info["not_ready"] == 1 and info["total"] == 2 and info["classes"] == ["new_instance"]
    prov.step()  # the woken poll reads the new model and opens the gate
    assert state.ready and len(detect.calls) == 2


def test_a_poll_that_started_before_verify_does_not_reopen_the_gate(state):
    now = [100.0]
    backend = FakeBackend([inst(51000, 1)])
    prov, _ = make(state, backend, clock=lambda: now[0])
    prov.step()
    # verify saw a model that the listing of the running poll (taken earlier) does not have
    prov._unscanned.add(instance_key(inst(52000, 2)))
    ready, why, detail = prov._readiness({instance_key(inst(51000, 1))})
    assert not ready and why == "dictionary_incomplete" and detail["classes"] == ["new_instance"]
    prov.step()  # a fresh listing is newer than the verify: the marker is cleared
    assert state.ready


def test_verify_never_raises_and_ignores_listing_errors(state):
    backend = FakeBackend([inst()])
    prov, _ = make(state, backend, clock=lambda: 1000.0)
    prov.step()
    backend.list_error = OSError("psutil")
    prov.verify_instances()
    assert state.ready
    state.verify_hook = lambda: 1 / 0
    state.verify()  # the state hook swallows anything


def test_a_failed_redetection_of_a_complete_instance_keeps_the_last_good_state(state):
    now = [0.0]
    backend = FakeBackend([inst()])
    detect = FakeDetect()
    prov, _ = make(state, backend, detect, redetect_every_s=600, clock=lambda: now[0])
    prov.step()
    assert state.ready
    detect.error = RuntimeError("query_failed")
    assert prov.request_refresh() and prov.step() == 1  # the forced re-read fails ...
    assert state.ready  # ... and the gate stays open for every session
    assert prov.status()["stale_instances"] == 1
    detect.error = None
    prov.step()  # retried on the next cycle, succeeds
    assert state.ready and prov.status()["stale_instances"] == 0


def test_new_data_whose_detection_fails_does_close_the_gate(state):
    backend = FakeBackend([inst()])
    detect = FakeDetect()
    prov, _ = make(state, backend, detect)
    prov.step()
    backend.fingerprints[instance_key(inst())] = 2
    detect.error = RuntimeError("query_failed")
    prov.step()
    assert not state.ready
    assert state.not_ready_info()["classes"] == ["query_failed"]


def test_failures_are_classed_for_the_user(state, tmp_path):
    backend = FakeBackend([inst()])
    backend.open_error = RuntimeError("connect_failed")
    prov, _ = make(state, backend)
    prov.step()
    assert state.not_ready_info()["classes"] == ["connect_failed"]
    other = GatewayState(home=tmp_path / "b", default_mode="on")
    prov2, _ = make(
        other,
        FakeBackend([inst()]),
        FakeDetect([result({"CUS": [NAME_A]}, ready=False)]),
    )
    prov2.step()
    assert other.not_ready_info()["classes"] == ["column_unreadable"]
    assert other.smap.mask_text(NAME_A)[0] != NAME_A  # partial values still protect


def test_unchanged_model_is_not_detected_again_but_a_new_fingerprint_is(state):
    backend = FakeBackend([inst()])
    prov, detect = make(state, backend)
    prov.step()
    prov.step()
    prov.step()
    assert len(detect.calls) == 1
    backend.fingerprints[instance_key(inst())] = 2
    assert prov.step() == 1
    assert len(detect.calls) == 2
    assert len(backend.opened) == 1  # the connection was reused


def test_values_are_added_never_removed(state):
    backend = FakeBackend([inst()])
    seq = [result({"CUS": [NAME_A]}), result({"CUS": [NAME_B]})]
    prov, detect = make(state, backend, FakeDetect(seq))
    prov.step()
    backend.fingerprints[instance_key(inst())] = 2
    prov.step()
    assert state.dictionary_counts() == {"CUS": 2}
    assert state.smap.mask_text(NAME_A)[0] != NAME_A


def test_not_ready_detection_adds_its_values_but_keeps_the_gate_closed_and_retries(state):
    backend = FakeBackend([inst()])
    detect = FakeDetect(
        [result({"CUS": [NAME_A]}, ready=False), result({"CUS": [NAME_B]}, ready=True)]
    )
    prov, _ = make(state, backend, detect)
    prov.step()
    assert not state.ready
    assert state.smap.mask_text(NAME_A)[0] != NAME_A  # partial values still protect
    prov.step()  # not complete: retried although the fingerprint did not change
    assert len(detect.calls) == 2 and state.ready


def test_detection_error_never_raises_and_keeps_the_gate_closed(state):
    backend = FakeBackend([inst()])
    detect = FakeDetect()
    detect.error = RuntimeError(f"boom {NAME_A}")
    prov, _ = make(state, backend, detect)
    prov.step()
    assert not state.ready
    assert prov.errors == 1
    assert backend.opened[0].closed  # the broken connection is dropped
    detect.error = None
    prov.step()
    assert state.ready and len(backend.opened) == 2


def test_connect_failure_is_contained(state):
    backend = FakeBackend([inst()])
    backend.open_error = RuntimeError("connect_failed")
    prov, _ = make(state, backend)
    assert prov.step() == 1
    assert not state.ready
    backend.open_error = None
    prov.step()
    assert state.ready


def test_instance_listing_failure_closes_the_gate_then_recovers(state):
    backend = FakeBackend([inst()])
    prov, _ = make(state, backend)
    prov.step()
    assert state.ready
    backend.list_error = OSError("psutil")
    prov.step()
    assert not state.ready
    backend.list_error = None
    prov.step()
    assert state.ready


def test_new_instance_closes_the_gate_until_it_is_read(state):
    backend = FakeBackend([inst(51000, 1)])
    detect = FakeDetect([result({"CUS": [NAME_A]}), result({"CUS": [NAME_C]})])
    prov, _ = make(state, backend, detect)
    prov.step()
    backend.current = [inst(51000, 1), inst(52000, 2)]
    backend.fingerprints[instance_key(inst(52000, 2))] = 1
    prov.step()
    assert state.ready and len(detect.calls) == 2
    assert set(state.smap.counts()) == {"pbi_51000", "pbi_52000"}


def test_closed_instance_is_dropped_and_target_closed(state):
    backend = FakeBackend([inst()])
    prov, _ = make(state, backend)
    prov.step()
    backend.current = []
    prov.step()
    assert backend.opened[0].closed and state.ready
    assert state.smap.mask_text(NAME_A)[0] != NAME_A  # the dictionary stays


def test_stale_fingerprint_connection_is_reconnected(state):
    backend = FakeBackend([inst()])
    prov, detect = make(state, backend)
    prov.step()
    backend.fp_error = True
    prov.step()  # fingerprint raises: dropped and re-detected (the new target raises as well)
    backend.fp_error = False
    prov.step()
    assert len(backend.opened) >= 2 and state.ready


def test_periodic_redetect_only_while_a_chat_is_active(state):
    now = [0.0]
    backend = FakeBackend([inst()])
    prov, detect = make(state, backend, redetect_every_s=600, clock=lambda: now[0])
    prov.step()
    now[0] = 700
    prov.step()
    assert len(detect.calls) == 1  # nobody is chatting: no periodic load on Desktop
    state.session("chat-1")  # a request arrives


def test_periodic_redetect(state):
    now = [0.0]
    backend = FakeBackend([inst()])
    state.session("chat-1")
    prov, detect = make(state, backend, redetect_every_s=600, clock=lambda: now[0])
    prov.step()
    now[0] = 599
    prov.step()
    assert len(detect.calls) == 1
    now[0] = 601
    prov.step()
    assert len(detect.calls) == 2


def test_refresh_forces_a_detection_and_waits_for_it(state):
    backend = FakeBackend([inst()])
    prov, detect = make(state, backend, poll_s=30)
    prov.start()
    try:
        assert state.wait_ready(5)
        assert len(detect.calls) == 1
        assert state.request_refresh(10) is True  # blocks until a NEW cycle completed
        assert len(detect.calls) == 2
    finally:
        prov.stop()


def test_refresh_without_running_thread_times_out(state):
    prov, _ = make(state, FakeBackend([]))
    assert prov.request_refresh(0.05) is False


def test_status_exposes_counts_only(state):
    backend = FakeBackend([inst()])
    prov, _ = make(state, backend, FakeDetect([result({"CUS": [NAME_A, NAME_B]})]))
    prov.step()
    text = json.dumps(state.status()["provider"])
    assert NAME_A not in text and NAME_B not in text
    info = state.status()["provider"]
    assert info["instances"] == 1 and info["failed_instances"] == 0 and info["cycles"] == 1
    assert info["last_detection_counts"]["values"] == 2
    assert info["policy"] == "dimensions" and info["attached"] is True


def test_thread_survives_unexpected_errors_and_stops_cleanly(state):
    backend = FakeBackend([inst()])
    calls = {"n": 0}
    original = backend.instances

    def flaky() -> list[dict[str, Any]]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("first poll fails")
        return original()

    backend.instances = flaky  # type: ignore[method-assign]
    prov, _ = make(state, backend, poll_s=0.02)
    prov.start()
    try:
        assert state.wait_ready(5)
    finally:
        prov.stop()
    assert backend.opened and backend.opened[0].closed
    assert not any(t.name == "gateway-provider" and t.is_alive() for t in threading.enumerate())


def test_policy_settings_defaults_and_validation(monkeypatch):
    import client_gateway.provider as mod

    values: dict[str, Any] = {}
    monkeypatch.setattr(mod, "cfg", lambda key, default: values.get(key, default))
    assert policy_settings() == ("dimensions", None)
    values["client_guard.gateway_policy"] = "Identity"
    values["client_guard.safe_tables"] = ["^a$", "b"]
    assert policy_settings() == ("identity", ["^a$", "b"])
    values["client_guard.gateway_policy"] = "nonsense"
    assert policy_settings()[0] == "dimensions"
    values["client_guard.safe_tables"] = "not-a-list"
    assert policy_settings()[1] is None


def test_source_label_never_uses_the_file_name():
    assert source_label(inst(51234)) == "pbi_51234"
    assert NAME_A not in source_label(inst())


def test_poll_timing_helper_does_not_busy_loop(state):
    backend = FakeBackend([inst()])
    prov, detect = make(state, backend, poll_s=0.05)
    prov.start()
    time.sleep(0.4)
    prov.stop()
    assert 1 <= prov.cycles <= 12


def test_no_timer_when_no_chat_is_active(state):
    backend = FakeBackend([inst()])
    prov, _ = make(state, backend, poll_s=0.02)
    prov.start()
    try:
        assert state.wait_ready(5)
        time.sleep(0.3)  # 15 poll intervals: an idle gateway queries nothing
        assert prov.cycles == 1
    finally:
        prov.stop()


def test_notice_runs_a_cycle_only_when_the_last_one_is_old(state):
    now = [0.0]
    backend = FakeBackend([inst()])
    prov, _ = make(state, backend, poll_s=60, clock=lambda: now[0])
    prov.step()
    prov.notice()
    assert not prov._wake.is_set()  # 0 s after the last cycle: nothing to do
    now[0] = 61
    prov.notice()
    assert prov._wake.is_set()


def test_masking_on_checks_the_open_models_at_once(state):
    calls = []
    state.verify_hook = lambda: calls.append(1)
    state.set_mode("chat-1", "on")
    assert calls == [1]


def test_a_hanging_desktop_never_stalls_the_provider(state, monkeypatch):
    """A model that hangs while it is read (Desktop closed mid-detection) once kept the gate
    closed for good: every call into Desktop is bounded and the next cycle carries on."""
    import client_gateway.provider as mod

    monkeypatch.setattr(mod, "DETECT_TIMEOUT_S", 0.2)
    monkeypatch.setattr(mod, "CLOSE_TIMEOUT_S", 0.1)
    gate = threading.Event()

    def hang(*args, **kwargs):
        gate.wait(10)
        raise RuntimeError("gone")

    backend = FakeBackend([inst()])
    prov, _ = make(state, backend, hang)
    try:
        t0 = time.monotonic()
        prov.step()
        assert time.monotonic() - t0 < 2
        assert state.ready is False
        assert state.not_ready_info()["classes"] == ["timeout"]
        backend.current = []  # the model is closed now
        prov.step()
        assert state.ready is True
    finally:
        gate.set()


def test_a_slow_detection_is_resumed_not_restarted_and_its_connection_stays_open(state):
    """G-15: a model whose detection outlasts the limit completes over a few cycles: the read keeps
    running on its own connection, the next cycle waits for the SAME read with a longer limit."""
    release = threading.Event()
    calls: list[int] = []

    def slow(run_dax, **kwargs):
        calls.append(1)
        release.wait(10)
        return result({"CUS": [NAME_A]})

    backend = FakeBackend([inst()])
    prov, _ = make(state, backend, slow, detect_timeout_s=0.15)
    try:
        prov.step()
        assert state.ready is False and state.not_ready_info()["classes"] == ["timeout"]
        assert len(backend.opened) == 1 and not backend.opened[0].closed  # not closed under it
        release.set()  # the read finishes by itself
        prov.step()
        assert state.ready is True and calls == [1]  # one read in total, no restart
        assert len(backend.opened) == 1
        assert state.smap.mask_text(NAME_A)[1] == 1  # its values arrived
    finally:
        release.set()


def test_the_detection_limit_doubles_after_a_timeout(state):
    release = threading.Event()
    backend = FakeBackend([inst()])
    prov, _ = make(
        state,
        backend,
        lambda run_dax, **kw: (release.wait(10), result({}))[1],
        detect_timeout_s=0.1,
    )
    try:
        prov.step()
        prov.step()
        assert prov._timeouts[instance_key(inst())] == 2
        assert len(backend.opened) == 1  # still the one connection and the one read
    finally:
        release.set()


def test_verify_waits_only_briefly_for_a_slow_listing(state, monkeypatch):
    """G-15: the instance check of a masked request no longer holds the request for the whole
    listing limit."""
    import client_gateway.provider as mod

    monkeypatch.setattr(mod, "VERIFY_WAIT_S", 0.2)
    gate = threading.Event()
    backend = FakeBackend([inst()])
    backend.instances = lambda: (gate.wait(5), [])[1]  # type: ignore[method-assign]
    prov, _ = make(state, backend)
    try:
        t0 = time.monotonic()
        prov.verify_instances()
        assert time.monotonic() - t0 < 1.0
    finally:
        gate.set()
