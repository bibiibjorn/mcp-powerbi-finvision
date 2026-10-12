"""The wire fixtures hold no account identifier, and the tool that makes them scrubs correctly.

Two jobs: (1) scan every committed fixture for the classes of data a capture session leaves
behind (e-mail addresses, JWTs, ChatGPT ``user-`` ids, Windows user paths, machine names, UUIDs
that are not the deterministic fake ones, file ids, a real PBI marker id); (2) test the
sanitizer (``tools/masking_lab/make_wire_fixtures.py``) on invented input, including a invented
private term, without ever touching a real capture.
"""

from __future__ import annotations

import base64
import json
import re

import pytest

from tests._wire_support import MARKER, WIRE, load_fixture
from tools.masking_lab import make_wire_fixtures as mwf

EXPECTED = {
    "chatgpt_conversation_get",
    "chatgpt_conversation_image_turn",
    "chatgpt_conversation_turn1",
    "chatgpt_files_metadata",
    "chatgpt_prepare_first",
    "chatgpt_prepare_followup",
    "chatgpt_process_upload_stream",
    "chatgpt_upload_put",
    "copilot_post_tool_reply_stream",
    "copilot_responses_first",
    "copilot_responses_tool_call",
    "copilot_responses_tool_result",
    "copilot_title_stream",
    "copilot_tool_call_stream",
    "copilot_tool_result_image",
}


def fixture_files() -> list:
    return sorted(WIRE.glob("*.json"))


def test_the_expected_fixture_set_is_there():
    assert {p.stem for p in fixture_files()} == EXPECTED


@pytest.mark.parametrize("path", fixture_files(), ids=lambda p: p.stem)
def test_fixture_holds_no_identifier(path):
    text = path.read_text(encoding="utf-8")
    assert mwf.find_leaks(text) == []
    # the generic scan above, spelled out here so a weakened tool cannot weaken the test too
    assert not re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}", text)
    assert "eyJ" not in text
    assert not re.search(r"user-[A-Za-z0-9]{10,}", text)
    assert not re.search(r"[A-Za-z]:[\\/]+Users[\\/]", text, re.I)
    assert not re.search(r"\blt-[0-9a-z]{6,}\b", text, re.I)
    for uuid in re.findall(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}", text):
        assert re.fullmatch(r"00000000-0000-4000-8000-\d{12}", uuid), "a real UUID is left"
    for file_id in re.findall(r"file_[0-9a-f]{32}", text):
        assert re.fullmatch(r"file_0{24}\d{8}", file_id), "a real file id is left"
    for marker in re.findall(r"fvpbi-[0-9a-f]{16}", text):
        assert marker == MARKER


@pytest.mark.parametrize("path", fixture_files(), ids=lambda p: p.stem)
def test_fixture_is_well_formed(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    assert {"name", "note", "request"} <= set(data) and data["name"] == path.stem
    request = data["request"]
    assert {"method", "host", "path", "content_type", "body"} <= set(request)
    assert "\r" not in path.read_text(encoding="utf-8")  # LF only
    for chunk in (data.get("response") or {}).get("chunks", []):
        assert chunk and isinstance(chunk, str)


def test_stream_fixtures_keep_event_aligned_chunks():
    for name in (
        "copilot_tool_call_stream",
        "chatgpt_conversation_turn1",
        "copilot_post_tool_reply_stream",
        "copilot_responses_first",
        "copilot_responses_tool_result",
        "copilot_responses_tool_call",
    ):
        chunks = load_fixture(name)["response"]["chunks"]
        assert len(chunks) > 5 and all(c.endswith("\n\n") for c in chunks)
    assert load_fixture("chatgpt_conversation_turn1")["response"]["content_encoding"] == "gzip"


# ---------------------------------------------------------------------- the sanitizer itself

FAKE_TERM = "Fnord Industries"


def make_san() -> mwf.Sanitizer:
    return mwf.Sanitizer([FAKE_TERM, ""])


def test_sanitizer_scrubs_each_class():
    san = make_san()
    jwt = base64.urlsafe_b64encode(b'{"alg":"ES256"}').decode().rstrip("=") + ".payload.sig"
    assert jwt.startswith("ey")  # built at run time: no literal token in this file
    dirty = (
        f"mail anna.test@example.org token {jwt} id user-AbCdEfGhIjKlMn path "
        "C:\\Users\\jdoe\\Documents\\x.txt host lt-abc123xy and fnord INDUSTRIES here "
        "msg_01ABCDEFGHIJKLMNOPQRST toolu_01ABCDEFGHIJKLMNOPQRST "
        "uuid 6AC62844-1E8C-83ED-9CE5-D109CB0EF483 file file_0000000022c881f4997e474e795b63d5 "
        "fvpbi-444ad8da244e201c"
    )
    clean = san.text(dirty)
    assert mwf.find_leaks(clean) == []
    assert "fnord" not in clean.lower()
    assert "<redacted>" in clean and "<token>" in clean and "<user-home>" in clean
    assert MARKER in clean


def test_sanitizer_ids_are_deterministic_and_consistent():
    a, b = make_san(), make_san()
    text = "a 6ac62844-1e8c-83ed-9ce5-d109cb0ef483 b 11111111-2222-3333-4444-555555555555 a again"
    assert a.text(text) == b.text(text)
    out = a.text(text)
    first = re.findall(r"00000000-0000-4000-8000-\d{12}", out)
    assert first[0] != first[1]
    assert a.text("6AC62844-1E8C-83ED-9CE5-D109CB0EF483") == first[0]  # same id, same fake


def test_sanitizer_walks_json_and_keeps_keys_and_numbers():
    san = make_san()
    obj = {"reasoning_opaque": "SECRETBLOB==", "token": "x", "n": 3, "t": [1.5, "Fnord Industries"]}
    out = san.walk(obj)
    assert out["reasoning_opaque"] == mwf.FAKE_OPAQUE and out["token"] == "<token>"
    assert out["n"] == 3 and out["t"] == [1.5, "<redacted>"] and list(out) == list(obj)


def test_event_trimming_cuts_account_metadata_only():
    san = make_san()
    meta = {"type": "server_ste_metadata", "metadata": {"plan_type": "x"}, "conversation_id": "c"}
    out = san.event(meta)
    assert "plan_type" not in json.dumps(out) and out["conversation_id"] == "c"
    keep = {"type": "message_marker", "marker": "first"}
    assert san.event(keep) == keep


def test_stream_chunk_keeps_boundaries_and_scrubs_each_event():
    san = make_san()
    chunk = 'event: delta\ndata: {"v":"Fnord Industries"}      \n\ndata: [DONE]\n\n'
    out = san.stream_chunk(chunk)
    assert out == 'event: delta\ndata: {"v":"<redacted>"}      \n\ndata: [DONE]\n\n'


def test_copilot_trimming_keeps_the_request_valid_and_short():
    big_tool = {
        "type": "function",
        "function": {"name": "t", "description": "d" * 400, "parameters": {"p": "x" * 3000}},
    }
    mcp = {"type": "function", "function": {"name": "mcp_x_customer_revenue", "description": "ok"}}
    body = {
        "messages": [
            {"role": "system", "content": "s" * 1000},
            {"role": "user", "content": "<reminderInstructions>\nlong\n</reminderInstructions>"},
        ],
        "tools": [big_tool, mcp, dict(big_tool)],
    }
    out = mwf.trim_copilot_request(body)
    assert len(out["messages"][0]["content"]) < 300
    assert "long" not in out["messages"][1]["content"]
    assert "<reminderInstructions>" in out["messages"][1]["content"]
    names = [t["function"]["name"] for t in out["tools"]]
    assert names == ["mcp_x_customer_revenue", "t"]
    assert out["tools"][1]["function"]["parameters"] == {"type": "object", "properties": {}}
    assert body["tools"][0]["function"]["description"] == "d" * 400  # the input is not changed


def test_build_all_fails_on_a_missing_sample(tmp_path):
    with pytest.raises(mwf.FixtureError):
        mwf.build_all(tmp_path, [])


def test_find_leaks_reports_classes_not_content():
    hits = mwf.find_leaks("contact someone@example.org")
    assert hits == ["email"]


def test_the_responses_fixtures_hold_no_account_level_value():
    """A Responses stream echoes the request, including the hashed account (``safety_identifier``),
    opaque provider ids and the whole tool list: none of that is in a fixture."""
    for name in (
        "copilot_responses_first",
        "copilot_responses_tool_result",
        "copilot_responses_tool_call",
    ):
        text = (WIRE / f"{name}.json").read_text(encoding="utf-8")
        assert not re.search(r"[0-9a-f]{40,}", text), name  # a hashed account / long hex id
        for value in re.findall(r'safety_identifier\\?":\\?"([^"\\]*)', text):
            assert value == "<id>"
        assert not re.search(r'"id":\\?"[A-Za-z0-9_-]{40,}', text), name  # opaque item ids
        data = json.loads(text)
        assert len(data["request"]["body"]["tools"]) <= 2
        for chunk in data["response"]["chunks"]:
            assert chunk.count("\n\n") == 1 and chunk.startswith("event: ")


def test_a_responses_event_is_cut_down_and_scrubbed():
    san = make_san()
    long_id = "rs_" + "A1b2C3d4" * 20
    event = {
        "type": "response.created",
        "response": {
            "id": long_id,
            "safety_identifier": "ab" * 32,
            "tools": [{"name": "t"}] * 50,
            "instructions": "i" * 1000,
            "metadata": {"user_id": "someone"},
            "output": [{"id": long_id, "encrypted_content": "BLOB==", "summary": []}],
        },
    }
    out = san.event(event)
    resp = out["response"]
    assert resp["tools"] == [] and len(resp["instructions"]) < 300 and resp["metadata"] == {}
    assert resp["safety_identifier"] == "<id>"
    assert resp["id"] == resp["output"][0]["id"] and long_id not in json.dumps(out)  # consistent
    assert resp["output"][0]["encrypted_content"] == mwf.FAKE_OPAQUE
    assert event["response"]["tools"]  # the input is not changed


def test_responses_request_trimming_keeps_the_request_valid_and_short():
    body = {
        "instructions": "i" * 1000,
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": "s" * 1000}]},
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "<importantReminders>\nlong\n</importantReminders>\nask",
                    }
                ],
            },
            {"type": "function_call_output", "call_id": "c", "output": "ok"},
        ],
        "tools": [
            {
                "type": "function",
                "name": "t",
                "description": "d" * 400,
                "parameters": {"p": "x" * 3000},
            },
            {"type": "function", "name": "mcp_customer_revenue", "description": "ok"},
        ],
    }
    out = mwf.trim_responses_request(body)
    assert len(out["instructions"]) < 300
    assert len(out["input"][0]["content"][0]["text"]) < 300
    user = out["input"][1]["content"][0]["text"]
    assert "long" not in user and "<importantReminders>" in user and user.endswith("ask")
    assert [t["name"] for t in out["tools"]] == ["mcp_customer_revenue", "t"]
    assert out["tools"][1]["parameters"] == {"type": "object", "properties": {}}
    assert out["input"][2] == body["input"][2]
    assert body["tools"][0]["description"] == "d" * 400  # the input is not changed


def test_reframe_gives_one_complete_event_per_chunk():
    chunks = ['event: a\ndata: {"x"', ":1}\n\nevent: b\nda", "ta: {}\n\n"]
    assert mwf.reframe(chunks) == ['event: a\ndata: {"x":1}\n\n', "event: b\ndata: {}\n\n"]
    assert mwf.reframe(["a\r\n\r\nb\r\n\r\n"]) == ["a\n\n", "b\n\n"]
    assert mwf.reframe([]) == []
