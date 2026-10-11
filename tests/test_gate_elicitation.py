"""Elicitation-backed confirmation path on the destructive-op gate.

The gate used to fail a blocked call with `error_type: confirm_destructive_required`
and make the agent retry with `confirm_destructive=true`. With MCP elicitation
(spec 2025-06-18, supported by Claude Code) the gate can instead ask the user
in-flight. The hard requirement is FAIL-SAFE: when elicitation is unavailable,
declined by the client, errored, timed out, or killed via MCP_GATE_ELICITATION=0,
behaviour must be byte-identical to the pre-change gate.

Two layers are tested:
  1. `core.autonomous.gating` wiring — with `elicit_confirmation` monkeypatched.
  2. `server.elicitation` itself — with a faked `request_ctx` / session; no real
     MCP session is ever touched.
"""

import asyncio
import sys
import threading
import types as pytypes

import pytest

from core.autonomous import gating
from core.autonomous.gating import (
    Severity,
    invalidate_policy_cache,
    merge_gate_info,
    require_autonomous_mode,
)

OP = "02_Model_Operations:table/delete"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class Spy:
    """Records calls and returns a canned answer (True / False / None)."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.answer


@pytest.fixture
def spy(monkeypatch):
    """Install a spy in place of the real elicit_confirmation.

    Patched on `server.elicitation` (the module the gate lazily imports from)
    so the gate's own import path is exercised.
    """
    import server.elicitation as se

    def _install(answer):
        s = Spy(answer)
        monkeypatch.setattr(se, "elicit_confirmation", s)
        return s

    return _install


def _policy(monkeypatch, value):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", value)
    invalidate_policy_cache()


def _expected_classic_block(policy, severity=Severity.CATASTROPHIC, op=OP):
    """The pre-change block response, spelled out explicitly.

    Written out by hand rather than calling the production builder: that is the
    point of the no-regression test — if someone changes _build_block_response,
    this fails.
    """
    return {
        "success": False,
        "error_type": "confirm_destructive_required",
        "error": (
            f"{severity.value.capitalize()} operation {op!r} blocked. "
            f"Policy {policy} requires explicit confirmation for this operation."
        ),
        "hint": (
            "Pass confirm_destructive=true to authorize this operation. "
            "(Autonomous mode was removed in v7.8; confirm_destructive is the "
            "break-glass.)"
        ),
        "blocked_op": op,
        "severity": severity.value,
        "policy": policy,
    }


# ---------------------------------------------------------------------------
# STRICT — the always-blocks tier
# ---------------------------------------------------------------------------


def test_strict_user_confirms_is_not_a_block(monkeypatch, spy):
    _policy(monkeypatch, "STRICT")
    s = spy(True)

    gate = require_autonomous_mode(OP, {}, Severity.CATASTROPHIC)

    assert len(s.calls) == 1
    assert gate is not None
    assert gate.get("success") is not False
    assert "success" not in gate
    assert gate["user_confirmed"] is True
    assert "confirmed interactively" in gate["gate_notice"]


def test_strict_user_confirmed_propagates_through_merge_gate_info(monkeypatch, spy):
    _policy(monkeypatch, "STRICT")
    spy(True)

    gate = require_autonomous_mode(OP, {}, Severity.DESTRUCTIVE)
    result = merge_gate_info({"success": True, "deleted": 1}, gate)

    assert result["success"] is True
    assert result["user_confirmed"] is True
    assert result["gate_notice"]


def test_strict_user_declines_blocks_with_same_error_type(monkeypatch, spy):
    _policy(monkeypatch, "STRICT")
    spy(False)

    gate = require_autonomous_mode(OP, {}, Severity.CATASTROPHIC)

    assert gate["success"] is False
    assert gate["error_type"] == "confirm_destructive_required"
    assert gate["user_declined"] is True
    assert "declined" in gate["error"]
    # The programmatic override must still be advertised.
    assert "confirm_destructive=true" in gate["hint"]


def test_strict_no_answer_is_byte_identical_to_classic_block(monkeypatch, spy):
    """The no-regression guarantee: None → exactly today's response."""
    _policy(monkeypatch, "STRICT")
    spy(None)

    gate = require_autonomous_mode(OP, {}, Severity.CATASTROPHIC)

    assert gate == _expected_classic_block("STRICT")


def test_strict_destructive_no_answer_also_unchanged(monkeypatch, spy):
    _policy(monkeypatch, "STRICT")
    spy(None)

    gate = require_autonomous_mode(OP, {}, Severity.DESTRUCTIVE)

    assert gate == _expected_classic_block("STRICT", Severity.DESTRUCTIVE)


def test_strict_declined_block_keeps_every_classic_key(monkeypatch, spy):
    _policy(monkeypatch, "STRICT")
    spy(False)

    gate = require_autonomous_mode(OP, {}, Severity.CATASTROPHIC)
    classic = _expected_classic_block("STRICT")

    for key in classic:
        if key == "error":  # intentionally reworded to name the decline
            continue
        assert gate[key] == classic[key], key


# ---------------------------------------------------------------------------
# LIGHT
# ---------------------------------------------------------------------------


def test_light_catastrophic_user_confirms_passes_as_advisory(monkeypatch, spy):
    _policy(monkeypatch, "LIGHT")
    s = spy(True)

    gate = require_autonomous_mode(OP, {}, Severity.CATASTROPHIC)

    assert len(s.calls) == 1
    assert gate.get("success") is not False
    assert gate["user_confirmed"] is True


def test_light_catastrophic_no_answer_is_classic_block(monkeypatch, spy):
    _policy(monkeypatch, "LIGHT")
    spy(None)

    gate = require_autonomous_mode(OP, {}, Severity.CATASTROPHIC)

    assert gate == _expected_classic_block("LIGHT")


def test_light_catastrophic_safe_evidence_never_elicits(monkeypatch, spy):
    """Evidence auto-clear must short-circuit BEFORE bothering the user."""
    _policy(monkeypatch, "LIGHT")
    s = spy(True)

    # C-02: only handler-computed, proven, in-process evidence clears the gate.
    gate = require_autonomous_mode(
        OP,
        {},
        Severity.CATASTROPHIC,
        evidence={
            "blast_radius": {"all_safe": True, "unsafe_nodes": []},
            "proven": True,
            "provenance": {"source": "in_process"},
        },
    )

    assert s.calls == []
    assert gate["evidence_cleared"] is True
    assert "user_confirmed" not in gate


def test_light_destructive_advisory_never_elicits(monkeypatch, spy):
    _policy(monkeypatch, "LIGHT")
    s = spy(True)

    gate = require_autonomous_mode(OP, {}, Severity.DESTRUCTIVE)

    assert s.calls == []
    assert gate.get("success") is not False
    assert "user_confirmed" not in gate
    assert gate["gate_notice"] == (
        f"Destructive op {OP!r} ran under policy LIGHT. "
        "Pass confirm_destructive=true to acknowledge catastrophic ops."
    )


def test_light_unsafe_evidence_elicits_and_forwards_blast_radius(monkeypatch, spy):
    """Unsafe evidence still blocks — but the human sees what it would break."""
    _policy(monkeypatch, "LIGHT")
    s = spy(None)
    evidence = {"blast_radius": {"all_safe": False, "unsafe_nodes": ["measure:Sales[X]"]}}

    gate = require_autonomous_mode(OP, {}, Severity.CATASTROPHIC, evidence=evidence)

    assert len(s.calls) == 1
    details = s.calls[0]["details"]
    assert details["unsafe_nodes"] == ["measure:Sales[X]"]
    assert gate == _expected_classic_block("LIGHT")


def test_elicit_details_include_item_count_and_target(monkeypatch, spy):
    _policy(monkeypatch, "STRICT")
    s = spy(None)

    require_autonomous_mode(
        "03_Batch_Operations:measures/delete",
        {"items": [{"name": "A"}, {"name": "B"}], "table_name": "Fact"},
        Severity.CATASTROPHIC,
    )

    details = s.calls[0]["details"]
    assert details["item_count"] == 2
    assert details["table_name"] == "Fact"
    assert s.calls[0]["op_name"] == "03_Batch_Operations:measures/delete"
    assert s.calls[0]["severity"] == "catastrophic"


# ---------------------------------------------------------------------------
# paths that must never elicit at all
# ---------------------------------------------------------------------------


def test_confirm_destructive_short_circuits_before_elicitation(monkeypatch, spy):
    _policy(monkeypatch, "STRICT")
    s = spy(True)

    gate = require_autonomous_mode(OP, {"confirm_destructive": True}, Severity.CATASTROPHIC)

    assert gate is None
    assert s.calls == []


def test_warn_unchanged_and_never_elicits(monkeypatch, spy):
    _policy(monkeypatch, "WARN")
    s = spy(False)

    gate = require_autonomous_mode(OP, {}, Severity.CATASTROPHIC)

    assert s.calls == []
    assert gate == {
        "gate_warning": (
            f"Catastrophic op {OP!r} ran under policy WARN. "
            "Pass confirm_destructive=true to acknowledge."
        )
    }


def test_off_unchanged_and_never_elicits(monkeypatch, spy):
    _policy(monkeypatch, "OFF")
    s = spy(False)

    gate = require_autonomous_mode(OP, {}, Severity.CATASTROPHIC)

    assert gate is None
    assert s.calls == []


def test_import_error_degrades_to_classic_block(monkeypatch):
    """core/ must not hard-depend on server/ — a missing module still blocks cleanly."""
    _policy(monkeypatch, "STRICT")
    monkeypatch.setitem(sys.modules, "server.elicitation", None)

    gate = require_autonomous_mode(OP, {}, Severity.CATASTROPHIC)

    assert gate == _expected_classic_block("STRICT")


def test_elicitation_raising_degrades_to_classic_block(monkeypatch):
    _policy(monkeypatch, "STRICT")
    import server.elicitation as se

    def _boom(**kwargs):
        raise RuntimeError("transport exploded")

    monkeypatch.setattr(se, "elicit_confirmation", _boom)

    gate = require_autonomous_mode(OP, {}, Severity.CATASTROPHIC)

    assert gate == _expected_classic_block("STRICT")


# ---------------------------------------------------------------------------
# server.elicitation unit tests — faked session, never a real one
# ---------------------------------------------------------------------------


class _TrapSession:
    """Session that fails the test if anything touches it."""

    def __getattr__(self, name):  # pragma: no cover - must never run
        raise AssertionError(f"session was accessed ({name}) when it should not be")


class _FakeSession:
    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.calls = []

    async def elicit_form(self, message, requestedSchema):
        self.calls.append((message, requestedSchema))
        if self.exc is not None:
            raise self.exc
        return self.result


def _fake_result(action, content=None):
    return pytypes.SimpleNamespace(action=action, content=content)


@pytest.fixture
def with_session(monkeypatch):
    """Install a fake request_ctx session and a live loop in another thread.

    elicit_confirmation is a sync helper that schedules onto a running loop via
    run_coroutine_threadsafe, so the test needs a loop running elsewhere.
    """
    import server.elicitation as se

    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()

    def _install(session):
        from server import request_context

        monkeypatch.setattr(se, "_get_session", lambda: session)
        # WP2: the bridge schedules on the server loop published by the
        # dispatcher (asyncio.get_event_loop() raises in worker threads); the
        # real dispatch path is covered by test_review_a_protocol_bridges.py.
        request_context.set_server_loop(loop)
        return session

    yield _install

    from server import request_context

    request_context.set_server_loop(None)
    loop.call_soon_threadsafe(loop.stop)
    t.join(timeout=5)
    loop.close()


@pytest.mark.parametrize(
    "action,content,expected",
    [
        ("accept", {"confirm": True}, True),
        ("accept", {"confirm": False}, False),
        ("decline", None, False),
        ("cancel", None, False),
        ("accept", {}, None),  # accepted but no answer → not an explicit no
        ("accept", None, None),
        ("weird", None, None),
    ],
)
def test_action_mapping(with_session, action, content, expected):
    from server.elicitation import elicit_confirmation

    with_session(_FakeSession(result=_fake_result(action, content)))

    got = elicit_confirmation(message="m", op_name=OP, severity="catastrophic", timeout=5)

    assert got is expected


def test_exception_maps_to_none(with_session):
    from server.elicitation import elicit_confirmation

    with_session(_FakeSession(exc=RuntimeError("client hung up")))

    assert elicit_confirmation(message="m", op_name=OP, severity="catastrophic", timeout=5) is None


def test_no_session_maps_to_none(monkeypatch):
    import server.elicitation as se

    monkeypatch.setattr(se, "_get_session", lambda: None)

    assert se.elicit_confirmation(message="m", op_name=OP, severity="catastrophic") is None


def test_kill_switch_returns_none_without_touching_session(monkeypatch):
    import server.elicitation as se

    monkeypatch.setenv("MCP_GATE_ELICITATION", "0")
    monkeypatch.setattr(se, "_get_session", lambda: _TrapSession())

    assert se.elicit_confirmation(message="m", op_name=OP, severity="catastrophic") is None


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "off", "Off", "no"])
def test_kill_switch_accepted_spellings(monkeypatch, value):
    import server.elicitation as se

    monkeypatch.setenv("MCP_GATE_ELICITATION", value)
    assert se.elicitation_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "on", "", "yes"])
def test_kill_switch_other_values_leave_it_enabled(monkeypatch, value):
    import server.elicitation as se

    monkeypatch.setenv("MCP_GATE_ELICITATION", value)
    assert se.elicitation_enabled() is True


def test_enabled_by_default(monkeypatch):
    import server.elicitation as se

    monkeypatch.delenv("MCP_GATE_ELICITATION", raising=False)
    assert se.elicitation_enabled() is True


def test_kill_switch_read_live(monkeypatch):
    """Operator must be able to flip it mid-session (mirrors current_policy)."""
    import server.elicitation as se

    monkeypatch.delenv("MCP_GATE_ELICITATION", raising=False)
    assert se.elicitation_enabled() is True
    monkeypatch.setenv("MCP_GATE_ELICITATION", "0")
    assert se.elicitation_enabled() is False
    monkeypatch.setenv("MCP_GATE_ELICITATION", "1")
    assert se.elicitation_enabled() is True


def test_kill_switch_makes_gate_produce_classic_block(monkeypatch):
    """End to end: kill switch on → the gate blocks exactly as it used to."""
    _policy(monkeypatch, "STRICT")
    monkeypatch.setenv("MCP_GATE_ELICITATION", "0")
    import server.elicitation as se

    monkeypatch.setattr(se, "_get_session", lambda: _TrapSession())

    gate = require_autonomous_mode(OP, {}, Severity.CATASTROPHIC)

    assert gate == _expected_classic_block("STRICT")


def test_requested_schema_is_flat_object_of_primitives():
    """Spec constraint on elicitation: flat object, primitive properties only."""
    from server.elicitation import _CONFIRM_SCHEMA

    assert _CONFIRM_SCHEMA["type"] == "object"
    assert _CONFIRM_SCHEMA["required"] == ["confirm"]
    props = _CONFIRM_SCHEMA["properties"]
    assert set(props) == {"confirm"}
    assert props["confirm"]["type"] == "boolean"


def test_prompt_message_names_op_severity_and_details(with_session):
    from server.elicitation import elicit_confirmation

    session = with_session(_FakeSession(result=_fake_result("accept", {"confirm": True})))

    elicit_confirmation(
        message="about to nuke",
        op_name=OP,
        severity="catastrophic",
        details={"unsafe_nodes": ["measure:Sales[X]"], "skipped": None},
        timeout=5,
    )

    prompt, schema = session.calls[0]
    assert "about to nuke" in prompt
    assert OP in prompt
    assert "catastrophic" in prompt
    assert "measure:Sales[X]" in prompt
    assert "skipped" not in prompt  # None-valued details are dropped
    assert schema["properties"]["confirm"]["type"] == "boolean"


def test_matches_installed_sdk_signature():
    """Guard against SDK drift: elicit_form(message, requestedSchema) must exist."""
    import inspect

    from mcp.server.session import ServerSession

    params = inspect.signature(ServerSession.elicit_form).parameters
    assert "message" in params
    assert "requestedSchema" in params


def test_gate_info_keys_carry_the_new_flags():
    assert "user_confirmed" in gating._GATE_INFO_KEYS
    assert "user_declined" in gating._GATE_INFO_KEYS
