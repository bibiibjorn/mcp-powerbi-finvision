"""The MCP masking layer (``server/masking_layer.py``) against a fake gateway.

Off by default and the identity then; when on: results masked (leaves AND keys, one call), marker as
the first member, images redacted or withheld, any failure = a ``masking_unavailable`` envelope and
never the original data; arguments unmasked; exempt tools carry the marker only.
"""

from __future__ import annotations

import base64
import json

import pytest
from mcp import types

from core.client_guard.home import home_dir
from core.client_guard.marker import MARKER_FIELD, marker
from server import masking_layer
from tests._masking_fakes import FakeGateway, forbid_gateway, install

REAL = "Zorblax Holdings NV"
TOKEN = "CUS_48213"
META = {"anthropic/maxResultSizeChars": 123}


def _result(payload, *, is_error=False, extra=()):
    text = payload if isinstance(payload, str) else json.dumps(payload, separators=(",", ":"))
    block = types.TextContent(type="text", text=text, _meta=META)
    return types.CallToolResult(content=[block, *extra], isError=is_error)


def _png_block(raw: bytes = b"\x89PNGfake") -> types.ImageContent:
    return types.ImageContent(
        type="image", data=base64.b64encode(raw).decode("ascii"), mimeType="image/png"
    )


@pytest.fixture
def gw(monkeypatch) -> FakeGateway:
    return install(monkeypatch)


def _mark() -> str:
    return marker(home_dir())


# ------------------------------------------------------------------ switch
def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("FINVISION_MCP_MASKING", raising=False)
    assert masking_layer.enabled() is False


def test_env_flag_enables(monkeypatch):
    monkeypatch.setenv("FINVISION_MCP_MASKING", "1")
    monkeypatch.setattr(masking_layer, "_chat_masked", lambda: True)
    assert masking_layer.enabled() is True


# masking on demand (2026-10-08): the flag alone is not enough, this chat must have masking on
@pytest.fixture
def chat(monkeypatch):
    from client_gateway import control_client

    state = {"sid": "s-1", "mode": "on", "health": {"ok": True}}
    monkeypatch.setenv("FINVISION_MCP_MASKING", "1")
    monkeypatch.setattr(masking_layer, "_active_cache", {"at": -1e9, "value": False})
    monkeypatch.setattr(control_client, "current_session_id", lambda: state["sid"])
    monkeypatch.setattr(control_client, "session_mode", lambda sid, timeout=1.0: state["mode"])
    monkeypatch.setattr(control_client, "health", lambda timeout=1.0: state["health"])
    return state


def test_masks_only_while_this_chat_has_masking_on(chat):
    assert masking_layer.enabled() is True
    masking_layer._active_cache["at"] = -1e9
    chat["mode"] = "off"
    assert masking_layer.enabled() is False


def test_no_gateway_running_means_masking_off_and_results_as_they_are(chat):
    chat["mode"] = None  # the gateway does not answer
    res = _result({"client": REAL})
    assert masking_layer.mask_call_result("04_Run_DAX", res) is res


def test_without_a_session_id_a_running_gateway_decides(chat):
    chat["sid"] = ""
    assert masking_layer.enabled() is True
    masking_layer._active_cache["at"] = -1e9
    chat["health"] = None
    assert masking_layer.enabled() is False


def test_the_chat_check_is_cached_briefly(chat, monkeypatch):
    from client_gateway import control_client

    calls = []
    monkeypatch.setattr(
        control_client, "session_mode", lambda sid, timeout=1.0: calls.append(1) or "on"
    )
    for _ in range(5):
        assert masking_layer.enabled() is True
    assert len(calls) == 1


def test_config_flag_enables(monkeypatch):
    monkeypatch.delenv("FINVISION_MCP_MASKING", raising=False)
    real_get = masking_layer.config.get
    monkeypatch.setattr(
        masking_layer.config,
        "get",
        lambda key, default=None: (
            True if key == "client_gateway.mcp_masking" else real_get(key, default)
        ),
    )
    monkeypatch.setattr(masking_layer, "_chat_masked", lambda: True)
    assert masking_layer.enabled() is True


def test_disabled_is_identity_with_zero_gateway_calls(monkeypatch):
    monkeypatch.delenv("FINVISION_MCP_MASKING", raising=False)
    counter = forbid_gateway(monkeypatch)
    res = _result({"client": REAL})
    assert masking_layer.mask_call_result("04_Run_DAX", res) is res
    args = {"query": f'EVALUATE {{"{TOKEN}"}}'}
    assert masking_layer.unmask_arguments(args) is args
    assert masking_layer.mask_log_message(f"x {REAL}") == f"x {REAL}"
    contents = [object()]
    assert masking_layer.mask_resource_contents(contents) is contents
    assert counter["calls"] == 0


def test_default_config_ships_the_flag_off():
    from core.config.config_manager import config

    assert config.get("client_gateway.mcp_masking", None) is False


# ------------------------------------------------------------------ results
def test_json_leaves_and_keys_masked_in_one_call_marker_first(gw):
    payload = {
        "success": True,
        "rows": [{REAL: 1, "name": REAL, "amount": 12.5, "ok": True, "none": None}],
        "note": f"owner {REAL}",
    }
    out = masking_layer.mask_call_result("04_Run_DAX", _result(payload))
    text = out.content[0].text
    data = json.loads(text)
    assert list(data)[0] == MARKER_FIELD and data[MARKER_FIELD] == _mark()
    assert REAL not in text and TOKEN in text
    assert data["rows"][0][TOKEN] == 1  # a dict KEY is masked too
    assert data["rows"][0]["name"] == TOKEN
    assert data["rows"][0]["amount"] == 12.5 and data["rows"][0]["ok"] is True
    assert data["rows"][0]["none"] is None
    assert data["note"] == f"owner {TOKEN}"
    assert len(gw.mask_calls) == 1  # ONE call for every string of the result
    assert text == json.dumps(data, separators=(",", ":"), ensure_ascii=False)  # compact format


def test_marker_value_is_this_installs_marker(gw):
    out = masking_layer.mask_call_result("04_Run_DAX", _result({"a": "b"}))
    assert json.loads(out.content[0].text)["_fv"].startswith("fvpbi-")
    assert json.loads(out.content[0].text)["_fv"] == _mark()


def test_spoofed_marker_member_is_replaced_not_duplicated(gw):
    out = masking_layer.mask_call_result("04_Run_DAX", _result({"a": 1, "_fv": "fvpbi-spoof"}))
    text = out.content[0].text
    assert text.count('"_fv"') == 1 and json.loads(text)["_fv"] == _mark()


def test_meta_and_is_error_preserved(gw):
    out = masking_layer.mask_call_result("04_Run_DAX", _result({"e": REAL}, is_error=True))
    assert out.isError is True
    assert out.content[0].meta == META


def test_plain_text_masked_and_marker_on_its_own_line(gw):
    out = masking_layer.mask_call_result("04_Run_DAX", _result(f"hello {REAL}"))
    assert out.content[0].text == f"hello {TOKEN}\n_fv: {_mark()}"


def test_non_object_json_is_masked_as_one_string(gw):
    out = masking_layer.mask_call_result("04_Run_DAX", _result(f'["{REAL}"]'))
    assert out.content[0].text == f'["{TOKEN}"]\n_fv: {_mark()}'
    assert gw.mask_calls == [[f'["{REAL}"]']]


def test_structured_content_is_masked_too(gw):
    res = _result({"a": 1})
    res = res.model_copy(update={"structuredContent": {"c": REAL}})
    out = masking_layer.mask_call_result("04_Run_DAX", res)
    assert out.structuredContent == {"c": TOKEN}


def test_image_redacted(gw):
    out = masking_layer.mask_call_result(
        "17_Desktop_Bridge", _result({"a": 1}, extra=[_png_block()])
    )
    img = out.content[1]
    assert isinstance(img, types.ImageContent)
    assert base64.b64decode(img.data) == b"REDACTED:\x89PNGfake"
    assert img.mimeType == "image/png"


def test_image_clean_is_returned_unchanged(gw):
    gw.image_action = "clean"
    block = _png_block()
    out = masking_layer.mask_call_result("17_Desktop_Bridge", _result({"a": 1}, extra=[block]))
    assert out.content[1] == block


def test_image_withheld_when_the_gateway_refuses_but_text_is_still_masked(gw):
    gw.image_action = None
    out = masking_layer.mask_call_result(
        "17_Desktop_Bridge", _result({"a": REAL}, extra=[_png_block()])
    )
    assert out.isError is False
    assert out.content[1].type == "text"
    assert out.content[1].text == "image withheld: masking unavailable"
    assert TOKEN in out.content[0].text and REAL not in out.content[0].text


def test_image_with_invalid_base64_is_withheld(gw):
    bad = types.ImageContent(type="image", data="@@@not base64", mimeType="image/png")
    out = masking_layer.mask_call_result("17_Desktop_Bridge", _result({"a": 1}, extra=[bad]))
    assert out.content[1].text == "image withheld: masking unavailable"
    assert gw.image_calls == []


def test_unsupported_content_type_is_withheld(gw):
    audio = types.AudioContent(type="audio", data="AAAA", mimeType="audio/wav")
    out = masking_layer.mask_call_result("04_Run_DAX", _result({"a": 1}, extra=[audio]))
    assert out.content[1].type == "text" and "withheld" in out.content[1].text


def _assert_unavailable(out):
    assert out.isError is True
    assert len(out.content) == 1
    data = json.loads(out.content[0].text)
    assert data["success"] is False and data["error_type"] == "masking_unavailable"
    assert data["error"] == masking_layer.UNAVAILABLE_TEXT
    assert "No data was returned" in data["error"]
    assert REAL not in out.content[0].text and TOKEN not in out.content[0].text


def test_unreachable_gateway_replaces_the_whole_result(gw):
    gw.reachable = False
    out = masking_layer.mask_call_result(
        "04_Run_DAX", _result({"secret": REAL}, extra=[_png_block()])
    )
    _assert_unavailable(out)


def test_wrong_length_reply_is_a_failure(gw, monkeypatch):
    from client_gateway import control_client

    monkeypatch.setattr(control_client, "mask_texts", lambda texts, **k: texts[:-1])
    _assert_unavailable(masking_layer.mask_call_result("04_Run_DAX", _result({"a": REAL, "b": 1})))


def test_exception_in_the_client_is_a_failure_with_no_data(gw):
    gw.raise_on_mask = True
    _assert_unavailable(masking_layer.mask_call_result("04_Run_DAX", _result({"a": REAL})))


def test_marker_failure_on_a_masked_tool_withholds(gw, monkeypatch):
    def _boom():
        raise OSError("no home")

    monkeypatch.setattr(masking_layer, "_marker", _boom)
    _assert_unavailable(masking_layer.mask_call_result("04_Run_DAX", _result({"a": REAL})))


# ------------------------------------------------------------------ exempt tools
@pytest.mark.parametrize("tool_name", ["00_Client_Mode", "10_Show_User_Guide"])
def test_exempt_tools_are_not_masked_but_carry_the_marker(gw, tool_name):
    out = masking_layer.mask_call_result(tool_name, _result({"status": REAL}, extra=[_png_block()]))
    data = json.loads(out.content[0].text)
    assert list(data)[0] == "_fv" and data["_fv"] == _mark()
    assert data["status"] == REAL  # not masked
    assert out.content[1] == _png_block()  # image untouched
    assert gw.calls == 0


def test_exempt_tool_plain_text_gets_the_marker_line(gw):
    out = masking_layer.mask_call_result("00_Client_Mode", _result("status ok"))
    assert out.content[0].text == f"status ok\n_fv: {_mark()}"


def test_config_adds_exempt_tools(gw, monkeypatch):
    real_get = masking_layer.config.get
    monkeypatch.setattr(
        masking_layer.config,
        "get",
        lambda key, default=None: (
            ["04_Run_DAX"] if key == "client_gateway.mcp_masking_exempt" else real_get(key, default)
        ),
    )
    assert "04_Run_DAX" in masking_layer.exempt_tools()
    out = masking_layer.mask_call_result("04_Run_DAX", _result({"a": REAL}))
    assert json.loads(out.content[0].text)["a"] == REAL and gw.calls == 0


def test_exempt_tool_with_failing_marker_is_returned_unchanged(gw, monkeypatch):
    def _boom():
        raise OSError("no home")

    monkeypatch.setattr(masking_layer, "_marker", _boom)
    res = _result({"a": 1})
    assert masking_layer.mask_call_result("00_Client_Mode", res) is res


# ------------------------------------------------------------------ arguments
def test_arguments_unmasked_in_one_call_including_nested_values(gw):
    args = {"query": f'EVALUATE ROW("x", "{TOKEN}")', "filters": [{"v": "PER_90417"}], "n": 3}
    out = masking_layer.unmask_arguments(args)
    assert out == {
        "query": f'EVALUATE ROW("x", "{REAL}")',
        "filters": [{"v": "Quentin Testermans"}],
        "n": 3,
    }
    assert len(gw.unmask_calls) == 1
    assert args["n"] == 3 and TOKEN in args["query"]  # the input is not mutated


def test_arguments_without_strings_make_no_call(gw):
    assert masking_layer.unmask_arguments({"n": 1, "b": True}) == {"n": 1, "b": True}
    assert gw.calls == 0


@pytest.mark.parametrize("token", ["CUS_48213", "cus_48213", "CUS\\_48213"])
def test_token_shaped_argument_fails_closed_when_the_gateway_is_unreachable(gw, token):
    gw.reachable = False
    with pytest.raises(masking_layer.MaskingUnavailable):
        masking_layer.unmask_arguments({"query": f"x {token}"})


def test_plain_arguments_pass_unchanged_when_the_gateway_is_unreachable(gw):
    gw.reachable = False
    args = {"query": "EVALUATE {1}", "page": "Overview"}
    assert masking_layer.unmask_arguments(args) == args


# ------------------------------------------------------------------ logs and resources
def test_log_message_masked(gw):
    assert masking_layer.mask_log_message(f"failed for {REAL}") == f"failed for {TOKEN}"


def test_log_message_dropped_when_masking_fails(gw):
    gw.reachable = False
    assert masking_layer.mask_log_message(f"failed for {REAL}") is None
    gw.raise_on_mask = True
    assert masking_layer.mask_log_message(f"failed for {REAL}") is None


def test_resource_contents_masked(gw):
    from mcp.server.lowlevel.helper_types import ReadResourceContents

    items = [ReadResourceContents(content=json.dumps({"c": REAL}), mime_type="application/json")]
    out = masking_layer.mask_resource_contents(items)
    assert out[0].mime_type == "application/json"
    data = json.loads(out[0].content)
    assert data == {"_fv": _mark(), "c": TOKEN}


def test_resource_contents_fail_with_a_fixed_message_and_no_data(gw):
    from mcp.server.lowlevel.helper_types import ReadResourceContents

    gw.reachable = False
    items = [ReadResourceContents(content=f"owner {REAL}", mime_type="text/plain")]
    with pytest.raises(masking_layer.MaskingUnavailable) as exc:
        masking_layer.mask_resource_contents(items)
    assert REAL not in str(exc.value)


def test_binary_resource_cannot_be_masked(gw):
    from mcp.server.lowlevel.helper_types import ReadResourceContents

    with pytest.raises(masking_layer.MaskingUnavailable):
        masking_layer.mask_resource_contents(
            [ReadResourceContents(content=b"\x00\x01", mime_type="application/octet-stream")]
        )
