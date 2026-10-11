"""The masking authority routes of the gateway: ``/__gateway/mask``, ``unmask`` and ``mask_image``.

In-process gateway (same pattern as ``test_client_gateway_server.py``), synthetic names only. The
upstream is never contacted: these routes only talk to the shared ``SessionMap``.
"""

from __future__ import annotations

import base64
import re
import threading
import time
from typing import Any

import httpx
import pytest

from client_gateway import server as server_mod
from client_gateway.images import ImageResult
from client_gateway.log import GatewayLog
from client_gateway.server import Gateway
from client_gateway.state import TOKEN_HEADER, GatewayState
from core.client_guard.engine import MaskingError

NAME = "Zorvex Holding"
NAME2 = "Quillan Trust"
TOKEN_RE = re.compile(r"CUS_\d{5}")
PNG = b"\x89PNG\r\n\x1a\nnot really a png"
PNG_B64 = base64.b64encode(PNG).decode("ascii")


def _plain_client() -> httpx.Client:
    return httpx.Client(http2=False, verify=False, timeout=httpx.Timeout(10.0, read=None))


@pytest.fixture()
def state(tmp_path):
    st = GatewayState(home=tmp_path, default_mode="off", images_policy="redact")
    st.smap.add_values("CUS", [NAME, NAME2], "test")
    return st


@pytest.fixture()
def gw(state):
    # port 9 (discard) is never contacted: the authority routes do not forward anything
    gateway = Gateway(
        state, port=0, upstream="http://127.0.0.1:9", log=GatewayLog(None), client=_plain_client()
    )
    gateway.start()
    yield gateway
    gateway.shutdown()


@pytest.fixture()
def client(gw):
    with httpx.Client(base_url=gw.url, timeout=30) as c:
        yield c


def call(client: httpx.Client, state: GatewayState, route: str, body: Any, token: bool = True):
    headers = {"content-type": "application/json"}
    if token:
        headers[TOKEN_HEADER] = state.token or ""
    return client.post(f"/__gateway/{route}", json=body, headers=headers)


# ---------------------------------------------------------------------------------- token
@pytest.mark.parametrize(
    ("route", "body"),
    [
        ("mask", {"texts": [NAME]}),
        ("unmask", {"texts": ["CUS_10000"]}),
        ("mask_image", {"png_b64": PNG_B64}),
    ],
)
def test_every_authority_route_needs_the_control_token(client, state, route, body):
    missing = call(client, state, route, body, token=False)
    assert missing.status_code == 403
    assert missing.json() == {"ok": False, "error": "token_required"}
    wrong = client.post(
        f"/__gateway/{route}",
        json=body,
        headers={"content-type": "application/json", TOKEN_HEADER: "x" * 43},
    )
    assert wrong.status_code == 403
    assert state.stats()["authority_mask_calls"] == 0
    assert state.stats()["authority_unmask_calls"] == 0


def test_routes_are_registered_as_token_routes():
    for route in ("mask", "unmask", "mask_image"):
        assert ("POST", f"/__gateway/{route}") in server_mod.TOKEN_ROUTES


def test_the_token_is_checked_before_the_body_is_read(client, state):
    resp = client.post(
        "/__gateway/mask", content=b"not json at all", headers={"content-type": "text/plain"}
    )
    assert resp.status_code == 403  # not 415: nothing is parsed for a caller without the token


def test_authority_routes_are_post_only(client, state):
    resp = client.get("/__gateway/mask", headers={TOKEN_HEADER: state.token or ""})
    assert resp.status_code == 404


def test_authority_route_needs_json(client, state):
    resp = client.post("/__gateway/mask", content=b"x", headers={TOKEN_HEADER: state.token or ""})
    assert resp.status_code == 415


# ---------------------------------------------------------------------------------- mask / unmask
def test_mask_then_unmask_round_trip(client, state):
    texts = [f"{NAME} owes {NAME2} 100", "nothing to hide here", ""]
    masked = call(client, state, "mask", {"texts": texts})
    assert masked.status_code == 200
    body = masked.json()
    assert body["ok"] is True
    assert body["masked"] >= 2
    assert len(body["texts"]) == 3
    assert NAME not in body["texts"][0] and NAME2 not in body["texts"][0]
    assert len(TOKEN_RE.findall(body["texts"][0])) == 2
    assert body["texts"][1:] == ["nothing to hide here", ""]

    back = call(client, state, "unmask", {"texts": body["texts"]})
    assert back.status_code == 200
    out = back.json()
    assert out["ok"] is True
    assert out["texts"] == texts
    assert out["unmasked"] == 2


def test_mask_gives_the_same_pseudonym_as_the_shared_map(client, state):
    expected = state.smap.mask_text(NAME)[0]
    body = call(client, state, "mask", {"texts": [NAME]}).json()
    assert body["texts"] == [expected]
    assert TOKEN_RE.fullmatch(expected)


def test_mask_works_with_the_default_mode_off(client, state):
    assert state.global_mode == "off"
    body = call(client, state, "mask", {"texts": [NAME]}).json()
    assert NAME not in body["texts"][0]


def test_unmask_leaves_unknown_tokens_and_needs_no_readiness(client, state):
    state.set_ready(False, "dictionary_empty")
    body = call(client, state, "unmask", {"texts": ["x CUS_99999 y", "plain"]}).json()
    assert body == {"ok": True, "texts": ["x CUS_99999 y", "plain"], "unmasked": 0}


def test_unmask_returns_values_with_quotes_verbatim(client, state):
    state.smap.add_values("CUS", ['Quote "Co" Ltd'], "test")
    token = state.smap.mask_text('Quote "Co" Ltd')[0]
    body = call(client, state, "unmask", {"texts": [token]}).json()
    assert body["texts"] == ['Quote "Co" Ltd']


def test_whole_flag_is_passed_to_the_masker(client, state, monkeypatch):
    seen: list[bool] = []

    def fake(texts, st, whole=True):
        seen.append(whole)
        return list(texts), 0

    monkeypatch.setattr(server_mod, "mask_strings", fake)
    call(client, state, "mask", {"texts": ["a"]})
    call(client, state, "mask", {"texts": ["a"], "whole": False})
    assert seen == [True, False]


def test_empty_texts_list_is_fine(client, state):
    assert call(client, state, "mask", {"texts": []}).json() == {
        "ok": True,
        "texts": [],
        "masked": 0,
    }


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"texts": "a string"},
        {"texts": ["ok", 5]},
        {"texts": [None]},
        {"texts": {"a": "b"}},
        {"texts": ["ok"], "whole": "yes"},
    ],
)
def test_bad_payloads_are_400(client, state, body):
    resp = call(client, state, "mask", body)
    assert resp.status_code == 400
    assert resp.json() == {"ok": False, "error": "bad_request"}


@pytest.mark.parametrize(
    "body", [{}, {"texts": "a string"}, {"texts": ["ok", 5]}, {"texts": [None]}]
)
def test_unmask_bad_payloads_are_400(client, state, body):
    resp = call(client, state, "unmask", body)
    assert resp.status_code == 400
    assert resp.json() == {"ok": False, "error": "bad_request"}


def test_counters_count_calls_and_failures(client, state, monkeypatch):
    call(client, state, "mask", {"texts": [NAME]})
    call(client, state, "mask", {"texts": [NAME]})
    call(client, state, "unmask", {"texts": ["CUS_10000"]})
    stats = state.stats()
    assert stats["authority_mask_calls"] == 2
    assert stats["authority_unmask_calls"] == 1
    assert stats["authority_failures"] == 0
    assert stats["masked_strings"] >= 2


# ---------------------------------------------------------------------------------- not ready / failure
def test_not_ready_is_503_and_nothing_is_returned(client, state, monkeypatch):
    monkeypatch.setattr(server_mod, "authority_wait_s", lambda: 0.05)
    state.set_ready(False, "dictionary_empty")
    started = time.monotonic()
    resp = call(client, state, "mask", {"texts": [NAME]})
    assert resp.status_code == 503
    assert resp.json() == {"ok": False, "error": "not_ready"}
    assert time.monotonic() - started < 5
    assert state.stats()["authority_failures"] == 1


def test_mask_waits_for_the_dictionary_up_to_the_bound(client, state, monkeypatch):
    monkeypatch.setattr(server_mod, "authority_wait_s", lambda: 10.0)
    state.set_ready(False, "dictionary_empty")
    threading.Timer(0.2, lambda: state.set_ready(True)).start()
    resp = call(client, state, "mask", {"texts": [NAME]})
    assert resp.status_code == 200
    assert NAME not in resp.json()["texts"][0]


def test_authority_wait_default_and_config(monkeypatch):
    assert server_mod.authority_wait_s() == 30.0
    monkeypatch.setattr(server_mod, "cfg", lambda key, default: "7.5")
    assert server_mod.authority_wait_s() == 7.5
    monkeypatch.setattr(server_mod, "cfg", lambda key, default: "garbage")
    assert server_mod.authority_wait_s() == 30.0
    monkeypatch.setattr(server_mod, "cfg", lambda key, default: -3)
    assert server_mod.authority_wait_s() == 30.0


def test_engine_failure_is_503_mask_failed_and_never_partial(client, state, monkeypatch):
    real = state.smap.mask_text

    def flaky(s, *args, **kwargs):
        if "boom" in s:
            raise MaskingError("masking failed")
        return real(s, *args, **kwargs)

    monkeypatch.setattr(state.smap, "mask_text", flaky)
    resp = call(client, state, "mask", {"texts": [f"{NAME} first", "boom second", "third"]})
    assert resp.status_code == 503
    body = resp.json()
    assert body == {"ok": False, "error": "mask_failed"}
    assert NAME not in resp.text
    assert state.stats()["authority_failures"] == 1


def test_unexpected_exception_is_also_mask_failed(client, state, monkeypatch):
    def broken(s, *args, **kwargs):
        raise RuntimeError("secret detail " + s)

    monkeypatch.setattr(state.smap, "mask_text", broken)
    resp = call(client, state, "mask", {"texts": [NAME]})
    assert resp.status_code == 503
    assert resp.json() == {"ok": False, "error": "mask_failed"}
    assert "secret detail" not in resp.text


def test_recognizer_failure_fails_closed(client, state, monkeypatch):
    def fail(texts):
        raise RuntimeError("recognizer down")

    monkeypatch.setattr(state.smap, "recognize_many", fail)
    resp = call(client, state, "mask", {"texts": [NAME]})
    assert resp.status_code == 503
    assert resp.json() == {"ok": False, "error": "mask_failed"}


def test_unmask_failure_is_503(client, state, monkeypatch):
    def fail(s, *args, **kwargs):
        raise MaskingError("unmasking failed")

    monkeypatch.setattr(state.smap, "unmask_text", fail)
    resp = call(client, state, "unmask", {"texts": ["CUS_10000"]})
    assert resp.status_code == 503
    assert resp.json() == {"ok": False, "error": "unmask_failed"}
    assert state.stats()["authority_failures"] == 1


# ---------------------------------------------------------------------------------- size limits
def test_authority_limit_is_64_mib_and_control_limit_stays_1_mib():
    assert server_mod.MAX_AUTHORITY_BYTES == 64 * 1024 * 1024
    assert server_mod.MAX_CONTROL_BYTES == 1024 * 1024


def test_authority_body_over_the_authority_limit_is_refused(client, state, monkeypatch):
    monkeypatch.setattr(server_mod, "MAX_AUTHORITY_BYTES", 2000)
    ok = call(client, state, "mask", {"texts": ["a" * 500]})
    assert ok.status_code == 200
    big = call(client, state, "mask", {"texts": ["a" * 5000]})
    assert big.status_code == 400
    assert big.json() == {"ok": False, "error": "bad_request"}


def test_authority_routes_accept_bodies_above_the_control_limit(client, state, monkeypatch):
    monkeypatch.setattr(server_mod, "MAX_CONTROL_BYTES", 1000)
    resp = call(client, state, "mask", {"texts": ["a" * 5000]})
    assert resp.status_code == 200
    assert resp.json()["texts"] == ["a" * 5000]


def test_other_control_routes_keep_the_small_limit(client, state, monkeypatch):
    monkeypatch.setattr(server_mod, "MAX_CONTROL_BYTES", 1000)
    resp = client.post(
        "/__gateway/mode",
        json={"session": "s" * 5000, "mode": "on"},
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400


def test_a_refused_big_body_does_not_poison_the_connection(client, state, monkeypatch):
    monkeypatch.setattr(server_mod, "MAX_AUTHORITY_BYTES", 2000)
    assert call(client, state, "mask", {"texts": ["a" * 5000]}).status_code == 400
    assert call(client, state, "mask", {"texts": [NAME]}).status_code == 200


# ---------------------------------------------------------------------------------- images
def _fake_redactor(monkeypatch, result=None, error=None, calls=None):
    def redact(data, smap, engines=None):
        if calls is not None:
            calls.append(data)
        if error is not None:
            raise error
        return result

    monkeypatch.setattr(server_mod, "redact_image_bytes", redact)


def test_mask_image_clean_returns_the_original_bytes(client, state, monkeypatch):
    calls: list[bytes] = []
    _fake_redactor(monkeypatch, ImageResult(None, None, 0, {}), calls=calls)
    resp = call(client, state, "mask_image", {"png_b64": PNG_B64})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "png_b64": PNG_B64, "action": "clean"}
    assert calls == [PNG]


def test_mask_image_redacted_returns_the_redacted_bytes(client, state, monkeypatch):
    painted = b"\x89PNG redacted"
    _fake_redactor(monkeypatch, ImageResult(painted, None, 2, {}))
    resp = call(client, state, "mask_image", {"png_b64": PNG_B64})
    body = resp.json()
    assert resp.status_code == 200
    assert body["action"] == "redacted"
    assert base64.b64decode(body["png_b64"]) == painted


def test_mask_image_failure_is_503_and_returns_no_image(client, state, monkeypatch):
    _fake_redactor(monkeypatch, error=RuntimeError("OCR exploded"))
    resp = call(client, state, "mask_image", {"png_b64": PNG_B64})
    assert resp.status_code == 503
    assert resp.json()["ok"] is False
    assert resp.json()["error"] == "mask_failed"
    assert "png_b64" not in resp.json()
    assert "OCR exploded" not in resp.text
    assert state.stats()["authority_failures"] == 1


def test_mask_image_withheld_reason_is_a_fixed_label(client, state, monkeypatch):
    from client_gateway import images

    _fake_redactor(monkeypatch, error=images._Withhold(images.W_NO_ENGINE))
    resp = call(client, state, "mask_image", {"png_b64": PNG_B64})
    assert resp.status_code == 503
    assert resp.json() == {"ok": False, "error": "mask_failed", "reason": images.W_NO_ENGINE}


def test_mask_image_policy_withhold_never_returns_an_image(client, state, monkeypatch):
    calls: list[bytes] = []
    _fake_redactor(monkeypatch, ImageResult(None, None, 0, {}), calls=calls)
    state.images_policy = "withhold"
    resp = call(client, state, "mask_image", {"png_b64": PNG_B64})
    assert resp.status_code == 503
    assert calls == []


def test_mask_image_policy_pass_is_the_users_explicit_choice(client, state, monkeypatch):
    calls: list[bytes] = []
    _fake_redactor(monkeypatch, ImageResult(b"x", None, 1, {}), calls=calls)
    state.images_policy = "pass"
    resp = call(client, state, "mask_image", {"png_b64": PNG_B64})
    assert resp.json() == {"ok": True, "png_b64": PNG_B64, "action": "clean"}
    assert calls == []


@pytest.mark.parametrize("body", [{}, {"png_b64": 5}, {"png_b64": ""}, {"png_b64": "%%%not b64"}])
def test_mask_image_bad_payloads_are_400(client, state, monkeypatch, body):
    _fake_redactor(monkeypatch, ImageResult(None, None, 0, {}))
    resp = call(client, state, "mask_image", body)
    assert resp.status_code == 400
    assert resp.json() == {"ok": False, "error": "bad_request"}


def test_mask_image_waits_for_readiness_and_fails_closed(client, state, monkeypatch):
    monkeypatch.setattr(server_mod, "authority_wait_s", lambda: 0.05)
    calls: list[bytes] = []
    _fake_redactor(monkeypatch, ImageResult(None, None, 0, {}), calls=calls)
    state.set_ready(False, "dictionary_empty")
    resp = call(client, state, "mask_image", {"png_b64": PNG_B64})
    assert resp.status_code == 503
    assert resp.json() == {"ok": False, "error": "not_ready"}
    assert calls == []
