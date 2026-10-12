"""Fail mode ``degrade`` (the default) of the MCP masking layer and the reason-returning control client.

Owner rule 2026-10-12: a masking / gateway problem never makes a Power BI MCP call fail. The layer
retries once, and when the gateway still cannot do its job it masks in-process with what runs
without it (the value shapes), keeps the ``_fv`` marker and adds a ``_masking`` notice. ``closed``
keeps the strict behaviour (``tests/test_mcp_masking_layer.py`` pins it). Synthetic data only; the
gateway is a fake (``tests/_masking_fakes.py``).
"""

from __future__ import annotations

import base64
import json
import socket
import urllib.error

import pytest
from mcp import types

from client_gateway import control_client as cc
from core.client_guard import fallback
from core.client_guard.home import home_dir
from core.client_guard.marker import marker
from server import masking_layer
from tests._masking_fakes import FakeGateway, install, pin_fail_mode

REAL = "Zorblax Holdings NV"
TOKEN = "CUS_48213"
MAIL = "jan.testermans@example.com"
META = {"anthropic/maxResultSizeChars": 123}


def _result(payload, *, extra=()):
    text = payload if isinstance(payload, str) else json.dumps(payload, separators=(",", ":"))
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text, _meta=META), *extra], isError=False
    )


def _png() -> types.ImageContent:
    return types.ImageContent(
        type="image", data=base64.b64encode(b"\x89PNGfake").decode("ascii"), mimeType="image/png"
    )


@pytest.fixture
def gw(monkeypatch) -> FakeGateway:
    fallback.reset()
    return install(monkeypatch, fail_mode="degrade")


def _data(out):
    return json.loads(out.content[0].text)


def _down(gw: FakeGateway, reason: str = "unreachable") -> None:
    """Every call fails with ``reason`` although the gateway still answers its health check."""
    gw.reachable = False
    gw.running = True
    gw.reason = reason


# ------------------------------------------------------------------ the control client reasons
class _Reply:
    def __init__(self, reply):
        self.reply = reply

    def __call__(self, *a, **k):
        return self.reply


@pytest.mark.parametrize(
    "reply, reason",
    [
        (None, "unreachable"),
        ((403, {"ok": False, "error": "token_required"}), "token_required"),
        ((503, {"ok": False, "error": "not_ready"}), "not_ready"),
        ((503, {"ok": False, "error": "mask_failed"}), "mask_failed"),
        ((503, {"ok": False, "error": "something_else"}), "mask_failed"),
        ((500, {}), "bad_reply"),
        ((200, {}), "bad_reply"),
        ((200, {"ok": True, "texts": ["only one"]}), "bad_reply"),
    ],
)
def test_the_control_client_says_why_a_call_failed(monkeypatch, reply, reason):
    monkeypatch.setattr(cc, "request", _Reply(reply))
    texts, why, extra = cc.mask_texts_ex(["a", "b"])
    assert texts is None and why == reason and extra == {}
    assert cc.mask_texts(["a", "b"]) is None  # the plain variant keeps its contract
    assert cc.unmask_texts_ex(["a", "b"])[1] == reason


def test_a_timeout_is_told_apart_from_an_unreachable_gateway(monkeypatch):
    def slow(*_a, **_k):
        raise socket.timeout("timed out")

    monkeypatch.setattr(cc.urllib.request, "urlopen", slow)
    assert cc.mask_texts_ex(["a"]) == (None, "timeout", {})

    def refused(*_a, **_k):
        raise urllib.error.URLError(ConnectionRefusedError("refused"))

    monkeypatch.setattr(cc.urllib.request, "urlopen", refused)
    assert cc.mask_texts_ex(["a"]) == (None, "unreachable", {})

    def wrapped_timeout(*_a, **_k):
        raise urllib.error.URLError(socket.timeout("timed out"))

    monkeypatch.setattr(cc.urllib.request, "urlopen", wrapped_timeout)
    assert cc.unmask_texts_ex(["a"])[1] == "timeout"


def test_the_degraded_note_of_a_200_reply_is_passed_on_as_fixed_labels(monkeypatch):
    body = {
        "ok": True,
        "texts": ["x"],
        "degraded": True,
        "degraded_reason": "dictionary_incomplete",
        "degraded_classes": ["query_failed", "Not A Label!", 5],
    }
    monkeypatch.setattr(cc, "request", _Reply((200, body)))
    texts, why, extra = cc.mask_texts_ex(["a"])
    assert texts == ["x"] and why == ""
    assert extra == {
        "degraded": True,
        "degraded_reason": "dictionary_incomplete",
        "degraded_classes": ["query_failed"],
    }
    body["degraded_reason"] = "a value like Zorblax"  # never echoed
    assert cc.mask_texts_ex(["a"])[2]["degraded_reason"] == "dictionary_not_ready"


def test_the_image_variant_says_why_and_passes_the_note(monkeypatch):
    png = b"\x89PNGfake"
    b64 = base64.b64encode(png).decode("ascii")
    monkeypatch.setattr(cc, "request", _Reply((403, {"ok": False})))
    assert cc.mask_image_ex(png) == (None, "token_required", {})
    ok = {"ok": True, "png_b64": b64, "action": "clean", "degraded": True, "degraded_reason": "x"}
    monkeypatch.setattr(cc, "request", _Reply((200, ok)))
    res, why, extra = cc.mask_image_ex(png)
    assert res == (png, "clean") and why == "" and extra["degraded"] is True
    assert cc.mask_image(png) == (png, "clean")


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, "degrade"),
        ("closed", "closed"),
        ("CLOSED", "closed"),
        (" closed ", "closed"),
        ("degrade", "degrade"),
        ("strict", "degrade"),
        (5, "degrade"),
        ("", "degrade"),
    ],
)
def test_fail_mode_is_degrade_unless_it_says_closed(monkeypatch, value, expected):
    real_get = cc_config().get
    monkeypatch.setattr(
        cc_config(),
        "get",
        lambda key, default=None: (
            value if key == "client_gateway.fail_mode" else real_get(key, default)
        ),
    )
    assert cc.fail_mode() == expected


def cc_config():
    from core.config.config_manager import config

    return config


def test_the_shipped_default_is_degrade():
    assert cc_config().get("client_gateway.fail_mode", None) == "degrade"
    assert cc_config().get("client_gateway.authority_degrade_wait_s", None) == 3.0
    assert cc.fail_mode() == "degrade"


# ------------------------------------------------------------------ results
def test_a_down_gateway_never_fails_the_call_it_masks_in_process(gw):
    _down(gw)
    out = masking_layer.mask_call_result("04_Run_DAX", _result({"mail": MAIL, "client": REAL}))
    assert out.isError is False
    data = _data(out)
    assert list(data)[:2] == ["_fv", "_masking"]
    assert data["_fv"] == marker(home_dir())  # the wire still sees a Power BI chat
    assert data["_masking"]["degraded"] is True and data["_masking"]["reason"] == "unreachable"
    assert "00_Client_Mode" in data["_masking"]["hint"]
    assert MAIL not in out.content[0].text  # the shape floor masks the e-mail address
    assert data["client"] == REAL  # a name is the gateway's dictionary: the floor cannot know it
    assert out.content[0].meta == META


def test_one_retry_hides_a_blip(gw):
    gw.running = True
    gw.fail_first = 1
    gw.reason = "timeout"
    out = masking_layer.mask_call_result("04_Run_DAX", _result({"c": REAL}))
    data = _data(out)
    assert data["c"] == TOKEN and "_masking" not in data
    assert len(gw.mask_calls) == 2


@pytest.mark.parametrize("reason", ["token_required", "not_ready", "mask_failed", "bad_reply"])
def test_the_reason_of_the_last_failure_is_reported(gw, reason):
    _down(gw, reason)
    data = _data(masking_layer.mask_call_result("04_Run_DAX", _result({"a": 1})))
    assert data["_masking"]["reason"] == reason
    assert len(gw.mask_calls) == 2  # one retry, then the in-process floor


def test_no_gateway_at_all_means_masking_is_off_and_the_result_is_untouched(gw):
    gw.reachable = False
    gw.running = False  # health does not answer either
    res = _result({"client": REAL})
    assert masking_layer.mask_call_result("04_Run_DAX", res) is res


def test_a_dictionary_that_is_not_ready_is_reported_once_per_result(gw):
    gw.degraded = {"degraded": True, "degraded_reason": "dictionary_incomplete"}
    second = types.TextContent(type="text", text=json.dumps({"b": REAL}))
    out = masking_layer.mask_call_result("04_Run_DAX", _result({"a": REAL}, extra=[second]))
    first = _data(out)
    assert first["a"] == TOKEN
    assert first["_masking"] == {
        "degraded": True,
        "reason": "dictionary_incomplete",
        "hint": masking_layer.NOTICE_HINT,
    }
    assert "_masking" not in json.loads(out.content[1].text)  # once, not per block
    assert out.content[0].text.count("_masking") == 1


def test_a_text_that_is_not_a_json_object_gets_one_fixed_line(gw):
    _down(gw)
    out = masking_layer.mask_call_result("04_Run_DAX", _result(f"hello {MAIL}"))
    lines = out.content[0].text.split("\n")
    assert lines[0].startswith("hello ") and MAIL not in lines[0]
    assert lines[1] == f"_fv: {marker(home_dir())}"
    assert lines[2] == f"_masking: degraded (unreachable). {masking_layer.NOTICE_HINT}"


def test_structured_content_carries_the_notice_too(gw):
    gw.degraded = {"degraded": True, "degraded_reason": "dictionary_incomplete"}
    res = _result({"a": 1}).model_copy(update={"structuredContent": {"c": REAL}})
    out = masking_layer.mask_call_result("04_Run_DAX", res)
    assert out.structuredContent["c"] == TOKEN
    assert out.structuredContent["_masking"]["reason"] == "dictionary_incomplete"


def test_an_image_the_gateway_cannot_make_safe_is_withheld_but_the_call_returns(gw):
    _down(gw)
    out = masking_layer.mask_call_result("17_Desktop_Bridge", _result({"a": 1}, extra=[_png()]))
    assert out.isError is False
    assert out.content[1].text == masking_layer.IMAGE_WITHHELD_TEXT
    assert _data(out)["_masking"]["degraded"] is True


def test_exempt_tools_are_untouched_by_a_down_gateway(gw):
    _down(gw)
    out = masking_layer.mask_call_result("00_Client_Mode", _result({"status": REAL}))
    data = _data(out)
    assert data["status"] == REAL and "_masking" not in data and data["_fv"] == marker(home_dir())
    assert gw.calls == 0


def test_closed_mode_is_still_strict(gw, monkeypatch):
    pin_fail_mode(monkeypatch, "closed")
    monkeypatch.setattr(cc, "fail_mode", lambda: "closed")
    _down(gw)
    out = masking_layer.mask_call_result("04_Run_DAX", _result({"secret": REAL}))
    assert out.isError is True
    assert json.loads(out.content[0].text)["error_type"] == "masking_unavailable"
    assert len(gw.mask_calls) == 1  # no retry


def test_the_in_process_floor_gives_the_same_pseudonym_for_the_same_value(gw):
    _down(gw)
    one = _data(masking_layer.mask_call_result("04_Run_DAX", _result({"m": MAIL})))
    two = _data(masking_layer.mask_call_result("04_Run_DAX", _result({"x": f"to {MAIL}"})))
    assert one["m"] in two["x"] and MAIL not in one["m"]


def test_the_check_of_the_chat_is_asked_again_after_a_failure(gw):
    _down(gw)
    masking_layer._active_cache.update(at=1e12, value=True)
    masking_layer.mask_call_result("04_Run_DAX", _result({"a": 1}))
    assert masking_layer._active_cache["at"] < 0  # forgotten: the next enabled() asks again


# ------------------------------------------------------------------ arguments
def test_arguments_the_gateway_cannot_unmask_go_to_the_tool_as_they_are(gw):
    _down(gw, "token_required")
    args = {"query": f'EVALUATE ROW("x", "{TOKEN}")', "n": 3}
    assert masking_layer.unmask_arguments(args) == args  # never an error
    assert len(gw.unmask_calls) == 2  # one retry


def test_unmasking_is_retried_once(gw):
    gw.running = True
    gw.fail_first = 1
    out = masking_layer.unmask_arguments({"q": f"x {TOKEN}"})
    assert out == {"q": f"x {REAL}"} and len(gw.unmask_calls) == 2


def test_plain_arguments_need_no_retry_when_the_gateway_is_down(gw):
    _down(gw)
    args = {"query": "EVALUATE {1}"}
    assert masking_layer.unmask_arguments(args) == args
    assert len(gw.unmask_calls) == 1


def test_closed_mode_still_refuses_a_pseudonym_argument(gw, monkeypatch):
    monkeypatch.setattr(cc, "fail_mode", lambda: "closed")
    _down(gw)
    with pytest.raises(masking_layer.MaskingUnavailable):
        masking_layer.unmask_arguments({"q": f"x {TOKEN}"})


# ------------------------------------------------------------------ logs, resources, listings
def test_a_log_message_is_masked_in_process_never_dropped(gw):
    _down(gw)
    out = masking_layer.mask_log_message(f"failed for {MAIL}")
    assert out is not None and MAIL not in out and out.startswith("failed for ")


def test_a_log_message_is_untouched_when_no_gateway_runs(gw):
    gw.reachable = False
    assert masking_layer.mask_log_message(f"x {MAIL}") == f"x {MAIL}"


def test_resource_contents_get_the_floor_the_marker_and_the_notice(gw):
    from mcp.server.lowlevel.helper_types import ReadResourceContents

    _down(gw)
    items = [ReadResourceContents(content=json.dumps({"c": MAIL}), mime_type="application/json")]
    out = masking_layer.mask_resource_contents(items)
    data = json.loads(out[0].content)
    assert data["_fv"] == marker(home_dir()) and data["_masking"]["reason"] == "unreachable"
    assert MAIL not in out[0].content


def test_a_binary_resource_still_cannot_be_masked(gw):
    from mcp.server.lowlevel.helper_types import ReadResourceContents

    with pytest.raises(masking_layer.MaskingUnavailable):
        masking_layer.mask_resource_contents(
            [ReadResourceContents(content=b"\x00\x01", mime_type="application/octet-stream")]
        )


def test_completions_are_masked_in_process_not_dropped(gw):
    _down(gw)
    out = masking_layer.mask_completion(types.Completion(values=[MAIL, "plain"]))
    assert out is not None and len(out.values) == 2
    assert MAIL not in out.values[0] and out.values[1] == "plain"


def test_a_listing_is_masked_in_process_not_emptied(gw):
    _down(gw)
    items = [types.Resource(uri="powerbi://x", name=MAIL, description="d")]
    out = masking_layer.mask_resource_listing(items)
    assert len(out) == 1 and out[0].name != MAIL and str(out[0].uri) == "powerbi://x"


# ------------------------------------------------------------------ what 00_Client_Mode reports
def test_describe_says_why_masking_is_effective_here(monkeypatch):
    from client_gateway import control_client

    monkeypatch.delenv("FINVISION_MCP_MASKING", raising=False)
    monkeypatch.setattr(
        masking_layer.config, "get", lambda key, default=None: default
    )  # the flag is off
    assert masking_layer.describe() == {"effective": False, "basis": "flag_off"}
    monkeypatch.setenv("FINVISION_MCP_MASKING", "1")
    monkeypatch.setattr(masking_layer, "_chat_masked", lambda: True)
    monkeypatch.setattr(control_client, "current_session_id", lambda: "")
    assert masking_layer.describe() == {"effective": True, "basis": "gateway_running"}
    monkeypatch.setattr(control_client, "current_session_id", lambda: "s-1")
    monkeypatch.setattr(masking_layer, "_chat_masked", lambda: False)
    assert masking_layer.describe() == {"effective": False, "basis": "session"}


# ------------------------------------------------------------------ the in-process floor
def test_the_floor_masks_value_shapes_only():
    fallback.reset()
    out, ok = fallback.mask_texts([f"mail {MAIL}", "IBAN BE68539007547034 ok", "just words", ""])
    assert ok is True
    assert MAIL not in out[0] and "BE68539007547034" not in out[1]
    assert out[2] == "just words" and out[3] == ""


def test_the_floor_never_raises(monkeypatch):
    fallback.reset()

    def boom():
        raise RuntimeError("no engine")

    monkeypatch.setattr(fallback, "_get", boom)
    assert fallback.mask_texts(["a", "b"]) == (["a", "b"], False)
