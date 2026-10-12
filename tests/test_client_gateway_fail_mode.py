"""Fail mode ``degrade`` (``client_gateway.fail_mode``, the default) of the gateway itself.

Owner rule 2026-10-12: a masking / gateway problem never makes a Power BI MCP call fail and never
makes a Copilot / ChatGPT / Claude Code chat request fail. A dictionary that is not ready, or name
recognition that cannot finish, no longer refuses: the work is masked with everything the gateway
has and reported as degraded. ``closed`` keeps the strict behaviour. The blocks that mean "a known
client value would leave" (residual leak, unsupported format, ...) are not touched.

Covers the authority routes (``/__gateway/mask|mask_image``), the Claude Code wire (``_proxy``) and
the TLS front end (``FrontendAddon``). Synthetic names only, in-process gateway, no live seam.
"""

from __future__ import annotations

import base64
import time
from typing import Any

import httpx
import pytest

from client_gateway import server as server_mod
from client_gateway.images import ImageResult
from client_gateway.log import GatewayLog
from client_gateway.masking import MaskingFailed
from client_gateway.masking import mask_request as real_mask_request
from client_gateway.server import Gateway
from client_gateway.state import TOKEN_HEADER, GatewayState, fail_mode
from tests._masking_fakes import pin_fail_mode
from tests.test_client_gateway_pbi_chats import JSON_OK, NAME, _serve, _state, post
from tests.test_client_gateway_server import FakeUpstream, body_of, message
from client_gateway.frontend.addon import FrontendAddon
from tests.test_frontend_addon import fixture_flow, make_state

PNG_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\nnot really a png").decode("ascii")


# ------------------------------------------------------------------ the setting
def test_fail_mode_reads_the_config_and_defaults_to_degrade(monkeypatch):
    assert fail_mode() == "degrade"
    for value, expected in (("closed", "closed"), ("CLOSED", "closed"), ("nonsense", "degrade"),
                            (None, "degrade"), (7, "degrade")):  # fmt: skip
        pin_fail_mode(monkeypatch, value)
        assert fail_mode() == expected


def test_the_degrade_wait_defaults_to_a_few_seconds_and_survives_bad_values(monkeypatch):
    from client_gateway import state as state_mod

    assert state_mod.degrade_wait_s() == 3.0
    for value, expected in (("1.5", 1.5), (0, 0.0), ("abc", 3.0), (-1, 3.0), (None, 3.0)):
        monkeypatch.setattr(state_mod, "cfg", lambda key, default, v=value: v)
        assert state_mod.degrade_wait_s() == expected


# ------------------------------------------------------------------ authority routes
@pytest.fixture()
def auth_state(tmp_path):
    st = GatewayState(home=tmp_path, default_mode="off", images_policy="pass")
    st.smap.add_values("CUS", [NAME], "test")
    return st


@pytest.fixture()
def auth_client(auth_state):
    gateway = Gateway(
        auth_state,
        port=0,
        upstream="http://127.0.0.1:9",
        log=GatewayLog(None),
        client=httpx.Client(http2=False, timeout=httpx.Timeout(10.0, read=None)),
    )
    gateway.start()
    with httpx.Client(base_url=gateway.url, timeout=30) as c:
        yield c
    gateway.shutdown()


def _call(client: httpx.Client, state: GatewayState, route: str, body: Any):
    headers = {"content-type": "application/json", TOKEN_HEADER: state.token or ""}
    return client.post(f"/__gateway/{route}", json=body, headers=headers)


def test_a_dictionary_that_is_not_ready_masks_with_what_it_has_and_says_so(
    auth_client, auth_state, monkeypatch
):
    monkeypatch.setattr(server_mod, "degrade_wait_s", lambda: 0.05)
    auth_state.set_ready(
        False,
        "dictionary_incomplete",
        {"total": 2, "not_ready": 1, "classes": ["query_failed", "Not A Label!"]},
    )
    started = time.monotonic()
    resp = _call(auth_client, auth_state, "mask", {"texts": [f"about {NAME}"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and NAME not in body["texts"][0]  # masked with the known values
    assert body["degraded"] is True and body["degraded_reason"] == "dictionary_incomplete"
    assert body["degraded_classes"] == ["query_failed", "unknown"]  # fixed labels only
    assert time.monotonic() - started < 5  # never the 30 s of fail mode closed
    stats = auth_state.stats()
    assert stats["authority_degraded"] == 1 and stats["authority_failures"] == 0


def test_a_ready_dictionary_adds_no_degraded_note(auth_client, auth_state):
    body = _call(auth_client, auth_state, "mask", {"texts": [NAME]}).json()
    assert "degraded" not in body and "degraded_reason" not in body
    assert auth_state.stats()["authority_degraded"] == 0


def test_the_wait_for_the_dictionary_is_short_and_ends_in_a_ready_reply(
    auth_client, auth_state, monkeypatch
):
    import threading

    monkeypatch.setattr(server_mod, "degrade_wait_s", lambda: 5.0)
    auth_state.set_ready(False, "dictionary_empty")
    threading.Timer(0.2, lambda: auth_state.set_ready(True)).start()
    body = _call(auth_client, auth_state, "mask", {"texts": [NAME]}).json()
    assert "degraded" not in body  # the dictionary came within the wait


def test_closed_mode_still_refuses_a_dictionary_that_is_not_ready(
    auth_client, auth_state, monkeypatch
):
    pin_fail_mode(monkeypatch, "closed")
    monkeypatch.setattr(server_mod, "authority_wait_s", lambda: 0.05)
    auth_state.set_ready(False, "dictionary_empty")
    resp = _call(auth_client, auth_state, "mask", {"texts": [NAME]})
    assert resp.status_code == 503 and resp.json() == {"ok": False, "error": "not_ready"}


def test_a_failed_mask_is_retried_once(auth_client, auth_state, monkeypatch):
    real = server_mod.mask_strings
    calls: list[int] = []

    def flaky(texts, state, whole=True):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return real(texts, state, whole)

    monkeypatch.setattr(server_mod, "mask_strings", flaky)
    resp = _call(auth_client, auth_state, "mask", {"texts": [NAME]})
    assert resp.status_code == 200 and NAME not in resp.json()["texts"][0]
    assert len(calls) == 2 and auth_state.stats()["authority_retried"] == 1


def test_a_mask_that_keeps_failing_is_still_a_503_for_the_mcp_to_handle(
    auth_client, auth_state, monkeypatch
):
    def always(texts, state, whole=True):
        raise RuntimeError("boom " + NAME)

    monkeypatch.setattr(server_mod, "mask_strings", always)
    resp = _call(auth_client, auth_state, "mask", {"texts": [NAME]})
    assert resp.status_code == 503 and resp.json() == {"ok": False, "error": "mask_failed"}
    assert NAME not in resp.text


def test_closed_mode_does_not_retry_a_failed_mask(auth_client, auth_state, monkeypatch):
    pin_fail_mode(monkeypatch, "closed")
    calls: list[int] = []

    def always(texts, state, whole=True):
        calls.append(1)
        raise RuntimeError("boom")

    monkeypatch.setattr(server_mod, "mask_strings", always)
    assert _call(auth_client, auth_state, "mask", {"texts": [NAME]}).status_code == 503
    assert len(calls) == 1


def test_an_image_is_processed_with_the_dictionary_as_it_is_and_flagged(
    auth_client, auth_state, monkeypatch
):
    from client_gateway import server as srv

    monkeypatch.setattr(srv, "degrade_wait_s", lambda: 0.05)
    monkeypatch.setattr(
        srv, "redact_image_bytes", lambda data, smap: ImageResult(None, None, 0, {})
    )
    auth_state.images_policy = "redact"
    auth_state.set_ready(False, "dictionary_empty")
    resp = _call(auth_client, auth_state, "mask_image", {"png_b64": PNG_B64})
    body = resp.json()
    assert resp.status_code == 200 and body["action"] == "clean"
    assert body["degraded"] is True and body["degraded_reason"] == "dictionary_empty"


def test_the_status_names_the_fail_mode(auth_state):
    assert auth_state.status()["fail_mode"] == "degrade"


# ------------------------------------------------------------------ the Claude Code wire
@pytest.fixture()
def upstream():
    up = FakeUpstream()
    up.respond = lambda req: (200, JSON_OK, b"{}")
    yield up
    up.close()


def test_a_dictionary_that_is_not_ready_never_refuses_a_chat_request(tmp_path, upstream):
    st = _state(tmp_path, default_mode="on", ready_wait_s=0.05)
    st.verify = lambda: None  # no Power BI Desktop in a test: keep the gate as set below
    st.set_ready(False, "dictionary_incomplete", {"total": 1, "not_ready": 1})
    gateway = _serve(st, upstream)
    try:
        with httpx.Client(base_url=gateway.url, timeout=30) as c:
            for _ in range(3):
                reply = post(c, body_of([message("user", f"Check {NAME} please")]))
                assert reply.status_code == 200  # never a refusal, not even the first
                assert NAME.encode() not in upstream.requests[-1]["body"]  # still masked
            assert st.stats()["not_ready_bypassed"] == 3 and st.stats()["not_ready"] == 0
            assert st.stats()["blocked"] == 0
    finally:
        gateway.shutdown()


def test_only_the_first_request_of_a_run_waits_for_the_dictionary(tmp_path, upstream, monkeypatch):
    waits: list[float] = []
    st = _state(tmp_path, default_mode="on", ready_wait_s=0.05)
    st.verify = lambda: None
    st.set_ready(False, "dictionary_empty")
    real_wait = st.wait_ready

    def counting(timeout=None):
        waits.append(timeout)
        return real_wait(timeout)

    st.wait_ready = counting  # type: ignore[method-assign]
    gateway = _serve(st, upstream)
    try:
        with httpx.Client(base_url=gateway.url, timeout=30) as c:
            for _ in range(3):
                assert post(c, body_of([message("user", "hi")])).status_code == 200
        assert len(waits) == 1 and waits[0] <= 0.05  # the short wait, once
    finally:
        gateway.shutdown()


def test_name_recognition_that_cannot_finish_masks_with_the_dictionary_instead(
    tmp_path, upstream, monkeypatch
):
    seen: list[bool] = []

    def refusing(body, ctype, state, memo, ner_degrade=False):
        seen.append(ner_degrade)
        if not ner_degrade:
            raise MaskingFailed("ner_incomplete")
        return real_mask_request(body, ctype, state, memo, ner_degrade=ner_degrade)

    monkeypatch.setattr(server_mod, "mask_request", refusing)
    st = _state(tmp_path, default_mode="on")
    gateway = _serve(st, upstream)
    try:
        with httpx.Client(base_url=gateway.url, timeout=30) as c:
            reply = post(c, body_of([message("user", f"Check {NAME} please")]))
            assert reply.status_code == 200 and seen == [False, True]
            assert NAME.encode() not in upstream.requests[-1]["body"]
            assert st.stats()["ner_auto_degraded"] == 1 and st.stats()["blocked"] == 0
            post(c, body_of([message("user", "again")]))
            assert seen[2] is False  # the next request tries the recognizer again
    finally:
        gateway.shutdown()


def test_closed_mode_still_refuses_one_message_for_a_dictionary_that_is_not_ready(
    tmp_path, upstream, monkeypatch
):
    pin_fail_mode(monkeypatch, "closed")
    st = _state(tmp_path, default_mode="on", ready_wait_s=0.05)
    st.verify = lambda: None
    st.set_ready(False, "model_reading")
    gateway = _serve(st, upstream)
    try:
        with httpx.Client(base_url=gateway.url, timeout=30) as c:
            assert post(c, body_of([message("user", "hi")])).status_code == 400
            assert post(c, body_of([message("user", "hi")])).status_code == 200
    finally:
        gateway.shutdown()


def test_the_blocks_that_mean_a_known_value_would_leave_stay(tmp_path, upstream, monkeypatch):
    def leaking(body, ctype, state, memo, ner_degrade=False):
        raise MaskingFailed("residual_leak")

    monkeypatch.setattr(server_mod, "mask_request", leaking)
    st = _state(tmp_path, default_mode="on")
    gateway = _serve(st, upstream)
    try:
        with httpx.Client(base_url=gateway.url, timeout=30) as c:
            reply = post(c, body_of([message("user", f"Check {NAME}")]))
            assert reply.status_code == 400 and upstream.requests == []
    finally:
        gateway.shutdown()


# ------------------------------------------------------------------ the TLS front end
@pytest.fixture()
def fe(tmp_path):
    return make_state(tmp_path)


@pytest.fixture()
def addon(fe):
    return FrontendAddon(fe)


def _masked_flow():
    return fixture_flow("copilot_tool_result_image")


def test_the_front_end_never_refuses_a_chat_for_a_dictionary_that_is_not_ready(addon, fe):
    fe.set_ready(False, "dictionary_empty")
    for _ in range(2):
        flow = _masked_flow()
        addon.process_request(flow)
        assert flow.response is None  # forwarded (masked), not answered locally
    assert fe.stats()["not_ready_bypassed"] == 2 and fe.stats()["not_ready"] == 0
    assert fe.stats()["frontend_masked"] == 2


def test_the_front_end_masks_with_the_dictionary_when_name_recognition_cannot_finish(
    addon, fe, monkeypatch
):
    from client_gateway.adapters import base

    calls: list[bool] = []
    real_text = base.MaskContext.text

    def refusing(self, s, whole=True):
        if not self.ner_degrade:
            calls.append(False)
            raise MaskingFailed("ner_incomplete")
        calls.append(True)
        return real_text(self, s, whole)

    monkeypatch.setattr(base.MaskContext, "text", refusing)
    flow = _masked_flow()
    addon.process_request(flow)
    assert flow.response is None and calls[0] is False and True in calls
    assert fe.stats()["ner_auto_degraded"] == 1 and fe.stats()["frontend_blocked"] == 0


def test_the_front_end_still_blocks_a_residual_leak(addon, fe, monkeypatch):
    from client_gateway.adapters import base

    def leaking(self, s, whole=True):
        raise MaskingFailed("residual_leak")

    monkeypatch.setattr(base.MaskContext, "text", leaking)
    flow = _masked_flow()
    addon.process_request(flow)
    assert flow.response is not None and flow.response.status_code == 400
