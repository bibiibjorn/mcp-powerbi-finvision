"""Owner rule (2026-10-07): the gateway may refuse ONE message, it never locks a chat.

Incidents this pins:
- a day-long chat switched to ``masking on``: the strict whole-history name scan never finished
  and EVERY message was refused with "send it again";
- name recognition that cannot finish refused every new message the same way;
- the routing watchdog shut the whole gateway down when no MCP was attached, so Claude Code
  (which talks to the AI through the gateway) got ECONNREFUSED on every request.
"""

from __future__ import annotations

import json

import httpx
import pytest

from client_gateway.state import NER_REFUSALS_BEFORE_DEGRADE, SessionState
from core.client_guard.ner import NerIncomplete
from tests.test_client_gateway_pbi_chats import JSON_OK, NAME, _serve, _state, post
from tests.test_client_gateway_server import FakeUpstream, body_of, message


@pytest.fixture()
def upstream():
    up = FakeUpstream()
    up.respond = lambda req: (200, JSON_OK, b"{}")
    yield up
    up.close()


class _NeverFinishes:
    """Name recognition that can never check the new text in time."""

    def find_many(self, texts, complete=False, budget_s=None, wait=True):
        raise NerIncomplete("timeout_or_worker")


def test_a_chat_switches_to_dictionary_masking_after_one_refusal():
    sess = SessionState("s")
    assert NER_REFUSALS_BEFORE_DEGRADE == 1
    assert sess.ner_refused() is True and sess.ner_degrade is True  # the first refusal switches
    assert sess.ner_refused() is False  # already dictionary-only: nothing more to switch
    sess.ner_ok()
    assert sess.ner_blocks == 0


def test_a_message_is_never_refused_when_name_recognition_cannot_finish(tmp_path, upstream):
    # 2026-10-07: refusing (and before that, holding) a message made a chat with masking on
    # unusable; the request goes on, the dictionary still masks every known value
    st = _state(tmp_path, default_mode="on")
    st.smap._recognizers = [_NeverFinishes()]
    gateway = _serve(st, upstream)
    try:
        with httpx.Client(base_url=gateway.url, timeout=30) as c:
            for _ in range(2):
                reply = post(c, body_of([message("user", f"Check {NAME} please")]))
                assert reply.status_code == 200
                assert NAME.encode() not in upstream.requests[-1]["body"]
            assert st.stats()["ner_partial"] == 2
    finally:
        gateway.shutdown()


def test_the_routing_watchdog_never_stops_the_gateway_process():
    import inspect

    from client_gateway import cli

    src = inspect.getsource(cli)
    assert "on_exit=gateway.shutdown" not in src  # the old wiring that caused ECONNREFUSED
    assert "on_exit=_routing_ended" in src


def test_a_dictionary_that_is_not_ready_refuses_one_message_only(tmp_path, upstream):
    st = _state(tmp_path, default_mode="on", ready_wait_s=0.05)
    st.verify = lambda: None  # no Power BI Desktop in a test: keep the gate as set below
    st.set_ready(False, "model_reading")
    gateway = _serve(st, upstream)
    try:
        with httpx.Client(base_url=gateway.url, timeout=30) as c:
            first = post(c, body_of([message("user", f"Check {NAME} please")]))
            assert first.status_code == 400 and upstream.requests == []  # one refusal
            second = post(c, body_of([message("user", f"Check {NAME} please")]))
            assert second.status_code == 200  # never a locked chat
            assert NAME.encode() not in upstream.requests[-1]["body"]  # still masked
            assert st.stats()["not_ready_bypassed"] == 1
    finally:
        gateway.shutdown()


def test_a_system_message_role_is_never_masked(tmp_path):
    # 2026-10-07: Claude Code sent a role "system" message; the gateway masked the role (it was
    # a dictionary value too) and Anthropic refused the request with HTTP 400 "Unexpected role"
    from client_gateway.masking import mask_request
    from client_gateway.memo import RemaskMemo

    st = _state(tmp_path, default_mode="on")
    st.smap.add_values("DIM", ["system", "System"], "test")
    body = json.dumps({"model": "m", "messages": [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": f"Check {NAME} in the system"},
    ]}).encode()  # fmt: skip
    out = json.loads(mask_request(body, "application/json", st, RemaskMemo()))
    assert [m["role"] for m in out["messages"]] == ["system", "user"]
    assert NAME not in json.dumps(out)


def _recognizer(waiting: int = 0):
    import threading

    from core.client_guard.ner import NerRecognizer

    rec = NerRecognizer.__new__(NerRecognizer)
    rec._predict_lock = threading.Lock()
    rec._fg_cond = threading.Condition()
    rec._fg_waiting = waiting
    return rec


def test_new_text_goes_before_background_history_scans():
    # 2026-10-07: after a restart the history scans of several long masked chats kept the one
    # name-recognition worker busy, and a new chat with masking on waited behind them
    import threading
    import time

    from core.client_guard.engine import background_priority

    rec = _recognizer()
    order: list[str] = []
    rec._predict_lock.acquire()  # a call in flight

    def history() -> None:
        with background_priority():
            if rec._acquire_worker(time.monotonic() + 5):
                order.append("history")
                rec._predict_lock.release()

    def new_text() -> None:
        if rec._acquire_worker(time.monotonic() + 5):
            order.append("new text")
            time.sleep(0.2)
            rec._predict_lock.release()

    fg, bg = threading.Thread(target=new_text), threading.Thread(target=history)
    fg.start()
    time.sleep(0.1)  # the new text is waiting for the worker
    bg.start()
    time.sleep(0.1)
    rec._predict_lock.release()
    fg.join(5)
    bg.join(5)
    assert order == ["new text", "history"]


def test_a_background_call_gives_up_at_its_deadline_while_new_text_waits():
    import time

    from core.client_guard.engine import background_priority

    rec = _recognizer(waiting=1)  # new text keeps wanting the worker
    with background_priority():
        assert rec._acquire_worker(time.monotonic() + 0.3) is False


def test_product_names_are_never_masked_so_claude_code_is_not_refused(tmp_path):
    # 2026-10-08: "Anthropic" was a vendor in the model; masking rewrote Claude Code's system
    # prompt ("You are Claude Code, Anthropic's official CLI") and every masked request got 429
    from client_gateway.masking import mask_request
    from client_gateway.memo import RemaskMemo

    st = _state(tmp_path, default_mode="on")
    st.smap.add_values("VEN", ["Anthropic", "Anthropic PBC", "Claude"], "test")
    st.smap.add_values("CUS", [NAME], "test")
    system = "You are Claude Code, Anthropic's official CLI for Claude."
    body = json.dumps({"model": "m", "system": [{"type": "text", "text": system}], "messages": [
        {"role": "user", "content": f"Check {NAME} with Claude"},
    ]}).encode()  # fmt: skip
    out = json.loads(mask_request(body, "application/json", st, RemaskMemo()))
    assert out["system"][0]["text"] == system
    assert NAME not in json.dumps(out)


def test_the_system_prompt_is_forwarded_byte_identical_and_never_refused(tmp_path):
    # a subscription login is checked on the system prompt: rewriting it = 429 on every request
    from client_gateway.masking import mask_request
    from client_gateway.memo import RemaskMemo

    st = _state(tmp_path, default_mode="on")
    st.smap.add_values("CUS", [NAME], "test")
    system = [{"type": "text", "text": "You are Claude Code."}, {"type": "text", "text": NAME}]
    body = json.dumps({"model": "m", "system": system, "messages": [
        {"role": "user", "content": f"Check {NAME}"},
    ]}).encode()  # fmt: skip
    out = json.loads(mask_request(body, "application/json", st, RemaskMemo()))
    assert out["system"] == system
    assert NAME not in json.dumps(out["messages"])
    assert st.stats()["system_values_passed"] == 1
