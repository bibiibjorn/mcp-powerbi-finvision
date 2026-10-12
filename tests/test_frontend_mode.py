"""``GatewayState.frontend_mode`` and its helpers (phase 3a): the PBI-chat rule for the TLS front end.

Chat ids are namespaced ``"<adapter>:<key>"``, a request without a chat id belongs to no chat (it is
masked only by its own marker, never because another chat of the app was a PBI chat a moment ago: R2),
a typed ``off`` needs no control token (it is the human's own message), every other off path still
does. Synthetic names only; no sockets, no processes.
"""

from __future__ import annotations

import pytest

from client_gateway.memo import RemaskMemo
from client_gateway.state import (
    MODE_OFF,
    MODE_ON,
    MODE_PASSTHROUGH,
    MODE_UNMASK,
    TURN_MAX,
    TURN_TTL_S,
    STAT_NAMES,
    GatewayState,
    ModeDenied,
    carry_session_modes,
    frontend_chat_id,
)

CHATGPT = "chatgpt_web"
COPILOT = "openai_chat"
KEY = "00000000-0000-4000-8000-000000000001"
OTHER = "00000000-0000-4000-8000-000000000002"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def clock():
    return Clock()


@pytest.fixture()
def state(tmp_path, clock):
    return GatewayState(home=tmp_path, default_mode="off", images_policy="withhold", clock=clock)


def test_chat_ids_are_namespaced_and_headerless_ids_end_in_a_colon():
    assert frontend_chat_id(CHATGPT, KEY) == f"chatgpt_web:{KEY}"
    assert frontend_chat_id(COPILOT, None) == "openai_chat:"
    assert frontend_chat_id(COPILOT, "") == "openai_chat:"


def test_an_unmarked_chat_with_no_switch_passes_through(state):
    assert state.frontend_mode(KEY, False, CHATGPT) == MODE_PASSTHROUGH
    assert state.status()["pbi_chats"] == 0


def test_a_marked_chat_is_masked_and_stays_a_pbi_chat(state):
    assert state.frontend_mode(KEY, True, CHATGPT) == MODE_ON
    # the marker is gone from the history (compaction): the chat is still a PBI chat
    assert state.frontend_mode(KEY, False, CHATGPT) == MODE_ON
    assert state.status()["pbi_chats"] == 1


def test_the_pbi_flag_survives_a_restart(tmp_path, clock):
    first = GatewayState(home=tmp_path, default_mode="off", images_policy="withhold", clock=clock)
    first.frontend_mode(KEY, True, CHATGPT)
    again = GatewayState(home=tmp_path, default_mode="off", images_policy="withhold", clock=clock)
    assert again.frontend_mode(KEY, False, CHATGPT) == MODE_ON


def test_front_end_chats_never_collide_with_other_adapters_or_claude_sessions(state):
    state.frontend_mode(KEY, True, CHATGPT)
    assert state.frontend_mode(KEY, False, COPILOT) == MODE_PASSTHROUGH
    assert state.request_mode(KEY, b"{}") == MODE_PASSTHROUGH  # a Claude Code session id


def test_a_typed_off_needs_no_token_and_unmasks_a_pbi_chat(state):
    state.frontend_mode(KEY, True, CHATGPT)
    assert state.frontend_set_mode(CHATGPT, KEY, "off")["mode"] == "off"
    assert state.frontend_mode(KEY, False, CHATGPT) == MODE_UNMASK
    assert state.frontend_mode(KEY, True, CHATGPT) == MODE_UNMASK
    # back on, by the same typed route
    state.frontend_set_mode(CHATGPT, KEY, "on")
    assert state.frontend_mode(KEY, False, CHATGPT) == MODE_ON


def test_a_typed_off_in_a_chat_that_is_not_a_pbi_chat_is_plain_off(state):
    state.frontend_set_mode(CHATGPT, KEY, "off")
    assert state.frontend_mode(KEY, False, CHATGPT) == MODE_OFF


def test_every_other_off_path_still_needs_the_control_token(state):
    sid = frontend_chat_id(CHATGPT, KEY)
    with pytest.raises(ModeDenied):
        state.set_mode(sid, "off")
    with pytest.raises(ModeDenied):
        state.set_mode("", "off")
    with pytest.raises(ValueError):
        state.frontend_set_mode(CHATGPT, KEY, "sideways")


def test_a_typed_on_masks_an_unmarked_chat_and_is_remembered(tmp_path, state, clock):
    state.frontend_set_mode(CHATGPT, KEY, "on")
    assert state.frontend_mode(KEY, False, CHATGPT) == MODE_ON
    assert state.frontend_mode(OTHER, False, CHATGPT) == MODE_PASSTHROUGH  # another chat: untouched
    again = GatewayState(home=tmp_path, default_mode="off", images_policy="withhold", clock=clock)
    assert again.frontend_mode(KEY, False, CHATGPT) == MODE_ON


def test_the_global_on_masks_every_front_end_chat(state):
    state.set_mode("", "on", state.token)
    assert state.frontend_mode(KEY, False, CHATGPT) == MODE_ON
    assert state.frontend_mode(None, False, COPILOT) == MODE_ON


def test_no_chat_id_is_masked_when_marked(state):
    assert state.frontend_mode(None, False, COPILOT) == MODE_PASSTHROUGH
    assert state.frontend_mode(None, True, COPILOT) == MODE_ON


def test_r2_a_request_without_a_chat_id_is_never_masked_because_of_another_chat(state, clock):
    state.frontend_mode(None, True, COPILOT)  # a marked request with no chat id
    state.frontend_mode(KEY, True, COPILOT)  # and a PBI chat of the same app
    for _ in range(3):
        assert state.frontend_mode(None, False, COPILOT) == MODE_PASSTHROUGH
        clock.now += 1
    assert state.frontend_mode(KEY, False, COPILOT) == MODE_ON  # the PBI chat itself is unchanged
    assert state.frontend_mode(OTHER, False, COPILOT) == MODE_PASSTHROUGH  # another chat: untouched
    assert state._frontend_turns == {}  # no adapter-wide window state exists any more


def test_a_command_typed_in_a_first_turn_acts_for_that_request_only(state):
    assert state.frontend_mode(None, False, CHATGPT, typed="on") == MODE_ON
    assert state.frontend_mode(None, False, CHATGPT) == MODE_PASSTHROUGH
    assert state.frontend_mode(None, False, CHATGPT, typed="off") == MODE_OFF
    assert state.frontend_mode(None, True, CHATGPT, typed="off") == MODE_UNMASK


def test_a_switch_typed_in_a_request_without_a_chat_id_acts_for_that_request_only(state):
    assert state.frontend_mode(None, False, COPILOT, typed="off") == MODE_OFF
    assert state.frontend_mode(None, False, COPILOT) == MODE_PASSTHROUGH
    assert state.status()["sessions"]["count"] == 0  # no headerless chat is created


def test_without_the_pbi_rule_the_legacy_rule_applies(tmp_path, clock):
    legacy = GatewayState(
        home=tmp_path, default_mode="off", images_policy="withhold", clock=clock,
        pbi_chats_only=False,
    )  # fmt: skip
    assert legacy.frontend_mode(KEY, True, CHATGPT) == MODE_OFF
    legacy.frontend_set_mode(CHATGPT, KEY, "on")
    assert legacy.frontend_mode(KEY, False, CHATGPT) == MODE_ON
    assert legacy.frontend_mode(None, False, CHATGPT, typed="on") == MODE_ON


def test_binding_a_first_turn_chat_carries_marker_switch_and_memo(state):
    memo = RemaskMemo()
    memo.remember("masked", "unmasked")
    state.frontend_bind_chat(CHATGPT, KEY, pbi=True, mode="on", memo=memo)
    assert state.frontend_mode(KEY, False, CHATGPT) == MODE_ON
    assert state.session(frontend_chat_id(CHATGPT, KEY)).memo is memo
    state.frontend_bind_chat(CHATGPT, OTHER, pbi=True)  # marker only
    assert state.frontend_mode(OTHER, False, CHATGPT) == MODE_ON
    state.frontend_bind_chat(CHATGPT, "", pbi=True, mode="on")  # no id: nothing to bind
    assert state.status()["sessions"]["count"] == 2


def test_frontend_chat_masked_is_read_only(state):
    assert state.frontend_chat_masked(CHATGPT, KEY) is False
    assert state.frontend_chat_masked(CHATGPT, None) is False
    assert state.status()["sessions"]["count"] == 0  # nothing was created
    state.frontend_mode(KEY, True, CHATGPT)
    assert state.frontend_chat_masked(CHATGPT, KEY) is True
    state.frontend_set_mode(CHATGPT, KEY, "off")
    assert state.frontend_chat_masked(CHATGPT, KEY) is False


def test_a_carried_prefix_never_applies_to_a_front_end_chat(tmp_path, clock):
    # ``status`` shows 8 characters of a session id: for a front end chat that is the adapter name,
    # so a prefix entry would otherwise reach EVERY chat of the adapter
    carry_session_modes(
        tmp_path, [{"id": "chatgpt_", "mode": "off", "pbi": True, "own_switch": True}]
    )
    fresh = GatewayState(home=tmp_path, default_mode="off", images_policy="withhold", clock=clock)
    assert fresh.frontend_mode(KEY, False, CHATGPT) == MODE_PASSTHROUGH
    assert fresh.frontend_mode(OTHER, False, CHATGPT) == MODE_PASSTHROUGH


def test_the_status_has_the_front_end_block_and_counters(state):
    assert state.status()["frontend"] == {"enabled": False}
    state.frontend_status = lambda: {"enabled": True, "port": 1}
    assert state.status()["frontend"] == {"enabled": True, "port": 1}

    def boom() -> dict:
        raise RuntimeError

    state.frontend_status = boom
    assert state.status()["frontend"] == {"enabled": True, "error": True}
    for name in (
        "frontend_requests",
        "frontend_masked",
        "frontend_passthrough",
        "frontend_blocked",
        "frontend_ws_dropped",
    ):
        assert name in STAT_NAMES and state.stats()[name] == 0


# ---------------------------------------------------------------------------------- turn ids
def test_a_turn_id_maps_a_side_call_to_the_chat_of_the_main_call(state, clock):
    assert state.frontend_turn_chat("copilot", "turn-1") is None
    state.frontend_note_turn("copilot", "turn-1", "cabc")
    assert state.frontend_turn_chat("copilot", "turn-1") == "cabc"
    assert state.frontend_turn_chat("chatgpt_web", "turn-1") is None  # per chat space
    assert state.frontend_turn_chat("copilot", "") is None
    state.frontend_note_turn("copilot", "", "cabc")  # nothing to remember
    state.frontend_note_turn("copilot", "turn-2", "")
    assert len(state._frontend_turns) == 1


def test_a_turn_id_expires_and_is_never_persisted(tmp_path, state, clock):
    state.frontend_note_turn("copilot", "turn-1", "cabc")
    clock.now += TURN_TTL_S - 1
    assert state.frontend_turn_chat("copilot", "turn-1") == "cabc"
    clock.now += 2
    assert state.frontend_turn_chat("copilot", "turn-1") is None
    assert not (tmp_path / "session_modes.json").exists() or "turn-1" not in (
        tmp_path / "session_modes.json"
    ).read_text(encoding="utf-8")


def test_the_turn_map_is_bounded(state, clock):
    for i in range(TURN_MAX + 50):
        state.frontend_note_turn("copilot", f"t{i}", "cabc")
        clock.now += 0.001
    assert len(state._frontend_turns) <= TURN_MAX
    assert state.frontend_turn_chat("copilot", f"t{TURN_MAX + 49}") == "cabc"  # the newest stays
    assert state.frontend_turn_chat("copilot", "t0") is None  # the oldest went


def test_the_app_labels_are_counted_and_untested_ones_are_reported(state):
    state.frontend_note_app("copilot", ("copilot-chat/0.32", "vscode/1"))
    state.frontend_note_app("copilot", ("vscode/1",))
    state.frontend_note_app("copilot", ())
    apps = state.status()["frontend_apps"]["copilot"]
    assert apps["seen"] == {"copilot-chat/0.32": 1, "vscode/1": 2}
    assert apps["untested"] == ["copilot-chat/0.32", "vscode/1"]  # no fixture set recorded them yet
    assert "untested_app_version:copilot" in state.status()["warnings"]


def test_the_status_carries_the_new_r9_counters(state):
    for name in (
        "residual_leak_blocked", "format_changed", "rehydration_unrecognised", "frontend_turn_mapped"
    ):  # fmt: skip
        assert name in STAT_NAMES and state.stats()[name] == 0
    assert "rehydration_unrecognised" not in state.status()["warnings"]
    state.bump("rehydration_unrecognised")
    assert "rehydration_unrecognised" in state.status()["warnings"]
