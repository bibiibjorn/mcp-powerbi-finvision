"""F-25 / S-12: pacing + post-write health for consecutive SINGLE writes.

``write_lane.commit`` used to pace and probe only inside multi-save loops (``in_loop=True``).
Now every live single write (a) is paced by a token bucket (more than ``pace_max_saves`` saves
inside ``pace_window_s`` inserts ``pace_pause_s``), (b) reads which objects are NEWLY in error
(``post_write_errors: [{object, message}]``) and (c) reports ``writes_last_60s`` - through the
per-call notes the dispatcher merges into the tool result, so no manager needs editing.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.operations import write_lane
from server.dispatch import ToolDispatcher
from server.registry import HandlerRegistry, ToolDefinition


class _FakeCS:
    query_executor = object()

    def is_connected(self):
        return True

    def bump_model_generation(self, reason):  # pragma: no cover - trivial
        return 1

    def probe_connection_health(self, timeout_s=5.0):
        return {"success": True}


@pytest.fixture
def lane(monkeypatch):
    """A fresh lane that believes it is connected to a live Desktop."""
    ln = write_lane.ModelWriteLane()
    monkeypatch.setattr(write_lane, "_connection_state", lambda: _FakeCS())
    monkeypatch.setattr(write_lane, "_journal", lambda: None)
    sleeps = []
    monkeypatch.setattr(write_lane.time, "sleep", lambda s: sleeps.append(s))
    ln.sleeps = sleeps
    return ln


def _model():
    return SimpleNamespace(SaveChanges=lambda: None)


def _errors(monkeypatch, snapshots):
    """expression_errors() returns the given snapshots in order (last one repeats)."""
    from core.operations import model_readback

    seq = list(snapshots)

    def _next():
        return seq.pop(0) if len(seq) > 1 else seq[0]

    monkeypatch.setattr(model_readback, "expression_errors", _next)


def test_fourth_save_inside_the_window_is_paced(lane, monkeypatch):
    _errors(monkeypatch, [{}])
    with write_lane.call_notes() as notes:
        for _ in range(3):
            lane.commit(_model(), label="measure/update")
        assert lane.sleeps == []  # three saves: no pause yet
        lane.commit(_model(), label="measure/update")
    assert len(lane.sleeps) == 1 and lane.sleeps[0] >= 0.5
    assert notes["paced_ms"] >= 500
    assert notes["writes_last_60s"] == 4


def test_pacing_is_configurable_and_can_be_switched_off(lane, monkeypatch):
    _errors(monkeypatch, [{}])
    monkeypatch.setattr(
        write_lane, "_cfg", lambda key, default: False if key == "pace_single_writes" else default
    )
    for _ in range(6):
        lane.commit(_model(), label="x")
    assert lane.sleeps == []


def test_loop_saves_are_not_double_paced_or_probed_twice(lane, monkeypatch):
    _errors(monkeypatch, [{}])
    for _ in range(5):
        lane.commit(_model(), label="x", in_loop=True)
    # in_loop keeps its own _loop_pause (0.1 s default, only if recent) - never pace_pause_s
    assert all(s < 0.5 for s in lane.sleeps)


def test_new_errors_after_a_write_are_reported(lane, monkeypatch):
    before = {}
    after = {"measure:7": "measure Sales[Bad]: Column 'X' cannot be found"}
    _errors(monkeypatch, [before, after])
    with write_lane.call_notes() as notes:
        info = lane.commit(_model(), label="measure/create")
    assert info["post_write_errors"] == [
        {"object": "measure Sales[Bad]", "message": "Column 'X' cannot be found"}
    ]
    assert notes["post_write_health"] == "errors_introduced"
    assert notes["post_write_errors"] == info["post_write_errors"]


def test_pre_existing_errors_are_not_blamed_on_this_write(lane, monkeypatch):
    broken = {"measure:1": "measure T[Old]: broken long ago"}
    _errors(monkeypatch, [broken, broken])
    with write_lane.call_notes() as notes:
        info = lane.commit(_model(), label="measure/update")
    assert "post_write_errors" not in info
    assert notes["post_write_health"] == "ok"


def test_baseline_is_carried_between_consecutive_writes(lane, monkeypatch):
    from core.operations import model_readback

    calls = []
    snaps = [{}, {"measure:1": "measure T[A]: err"}, {"measure:1": "measure T[A]: err"}]

    def _snap():
        calls.append(1)
        return snaps[min(len(calls) - 1, len(snaps) - 1)]

    monkeypatch.setattr(model_readback, "expression_errors", _snap)
    first = lane.commit(_model(), label="a")  # baseline read + post read
    second = lane.commit(_model(), label="b")  # baseline reused (fresh) + post read
    assert first["post_write_errors"][0]["object"] == "measure T[A]"
    assert "post_write_errors" not in second  # already present after write 1
    assert len(calls) == 3  # not 4: the second write reused the previous post snapshot


def test_unreadable_model_reports_unverified_not_ok(lane, monkeypatch):
    from core.operations import model_readback

    monkeypatch.setattr(model_readback, "expression_errors", lambda: None)
    with write_lane.call_notes() as notes:
        info = lane.commit(_model(), label="x")
    assert info == {}
    assert "post_write_errors" not in notes
    assert notes.get("post_write_health") in (None, "unverified")


def test_post_write_health_can_be_disabled(lane, monkeypatch):
    from core.operations import model_readback

    def _boom():
        raise AssertionError("must not read errors when disabled")

    monkeypatch.setattr(model_readback, "expression_errors", _boom)
    monkeypatch.setattr(
        write_lane, "_cfg", lambda key, default: False if key == "post_write_health" else default
    )
    with write_lane.call_notes() as notes:
        assert lane.commit(_model(), label="x") == {}
    assert notes["writes_last_60s"] == 1


def test_not_live_keeps_the_old_clean_contract(monkeypatch):
    """No connection (tests, offline): commit returns {} and never paces or reads errors."""
    ln = write_lane.ModelWriteLane()
    monkeypatch.setattr(write_lane, "_journal", lambda: None)
    sleeps = []
    monkeypatch.setattr(write_lane.time, "sleep", lambda s: sleeps.append(s))
    for _ in range(6):
        assert ln.commit(_model(), label="x") == {}
    assert sleeps == []


def test_health_failure_never_fails_the_write(lane, monkeypatch):
    from core.operations import model_readback

    def _boom():
        raise RuntimeError("DMV exploded")

    monkeypatch.setattr(model_readback, "expression_errors", _boom)
    assert lane.commit(_model(), label="x") == {}


def test_dispatcher_merges_lane_notes_into_a_write_result(monkeypatch):
    """The managers drop commit()'s return value; the dispatcher still surfaces the notes."""
    ln = write_lane.ModelWriteLane()
    monkeypatch.setattr(write_lane, "_connection_state", lambda: _FakeCS())
    monkeypatch.setattr(write_lane, "_journal", lambda: None)
    monkeypatch.setattr(write_lane.time, "sleep", lambda s: None)
    monkeypatch.setattr(write_lane, "get_write_lane", lambda: ln)
    _errors(monkeypatch, [{}, {"measure:9": "measure S[M]: boom"}])

    def _handler(args):
        write_lane.commit(_model(), label="measure/create")  # return value dropped on purpose
        return {"success": True, "created": "M"}

    reg = HandlerRegistry()
    reg.register(
        ToolDefinition(
            name="02_Model_Operations",
            description="t",
            handler=_handler,
            input_schema={"type": "object", "properties": {"object_type": {}, "operation": {}}},
            category="model",
        )
    )
    d = ToolDispatcher()
    d.registry = reg
    out = d.dispatch("02_Model_Operations", {"object_type": "measure", "operation": "create"})
    assert out["success"] is True
    assert out["post_write_errors"][0]["object"] == "measure S[M]"
    assert out["writes_last_60s"] == 1
    # a handler-supplied key is never overwritten
    assert "paced_ms" not in out


def test_merge_notes_never_overwrites_handler_keys():
    result = {"success": True, "writes_last_60s": "mine"}
    write_lane.merge_notes(result, {"writes_last_60s": 3, "post_write_health": "ok"})
    assert result["writes_last_60s"] == "mine" and result["post_write_health"] == "ok"
