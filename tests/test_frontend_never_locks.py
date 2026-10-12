"""Owner rule (2026-10-07) for the desktop apps too: the front end may refuse ONE message of a
Copilot / ChatGPT chat, it never locks the chat.

The same incident as ``test_client_gateway_never_locks.py``: name recognition that cannot finish
must not refuse every next message; the chat goes on with dictionary masking (every known client
value still masked) and ``masking off`` always works."""

from __future__ import annotations

from typing import Any

from client_gateway.frontend.addon import META_KEY
from core.client_guard.ner import NerIncomplete
from tests._wire_support import NAME
from tests.test_frontend_copilot_identity import chat_body, tool_result
from tests.test_frontend_r2 import CHAT_URL, addon, post, state  # noqa: F401 - fixtures


class _NeverFinishes:
    """Name recognition that can never check new text in time."""

    def find_many(self, texts, complete=False, budget_s=None, wait=True):
        raise NerIncomplete("timeout_or_worker")


def _send(addon: Any, body: bytes) -> Any:  # noqa: F811
    flow = post(CHAT_URL, body)
    addon.process_request(flow)
    return flow


def test_a_copilot_chat_is_never_refused_when_ner_cannot_finish(addon, state):  # noqa: F811
    state.smap._recognizers = [_NeverFinishes()]
    body = chat_body("the customer chat", tail=[tool_result()])
    for _ in range(2):
        flow = _send(addon, body)
        assert flow.response is None  # forwarded, never refused or held
        assert flow.metadata[META_KEY].mode == "on"  # still masked, with the dictionary
        assert NAME.encode() not in (flow.request.raw_content or b"")
    assert state.stats()["ner_partial"] >= 1


def test_typed_masking_off_always_reaches_a_masked_copilot_chat(addon, state):  # noqa: F811
    state.smap._recognizers = [_NeverFinishes()]
    body = chat_body("the customer chat", tail=[tool_result()])
    _send(addon, body)  # refused: name recognition cannot finish
    off = _send(addon, chat_body("the customer chat", "masking off", tail=[tool_result()]))
    assert off.response is None  # the switch-off is never refused
