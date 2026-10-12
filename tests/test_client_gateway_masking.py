"""Tests of client_gateway.masking.mask_request (synthetic names only)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from client_gateway import masking
from client_gateway.masking import MaskingFailed, mask_request
from client_gateway.memo import RemaskMemo
from client_gateway.state import GatewayState

NAME = "Zorvex Holding"
NAME2 = "Quillan Trust"
JSON = "application/json"


@pytest.fixture()
def state(tmp_path):
    st = GatewayState(home=tmp_path, default_mode="on", images_policy="withhold")
    st.smap.add_values("CUS", [NAME, NAME2], "test")
    return st


@pytest.fixture()
def memo():
    return RemaskMemo()


def run(state: GatewayState, memo: RemaskMemo, payload: Any) -> Any:
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return json.loads(mask_request(raw, JSON, state, memo))


def tok(state: GatewayState, value: str = NAME) -> str:
    return state.smap.mask_text(value)[0]


def test_every_string_leaf_is_masked_and_keys_of_a_tool_input_too(state, memo):
    t1, t2 = tok(state), tok(state, NAME2)
    out = run(state, memo, {"notes": {"k": [f"a {NAME}", {"deep": NAME2}]}, "n": 3, "ok": True})
    assert out == {"notes": {"k": [f"a {t1}", {"deep": t2}]}, "n": 3, "ok": True}
    block = {"type": "tool_use", "id": "t", "name": "n", "input": {NAME: {NAME2: 1}, "k": NAME}}
    out = run(state, memo, {"messages": [{"role": "assistant", "content": [block]}]})
    assert out["messages"][0]["content"][0]["input"] == {t1: {t2: 1}, "k": t1}


def test_recognizer_batches_text_blocks_before_masking(tmp_path, memo):
    from core.client_guard.engine import SessionMap

    class Recognizer:
        calls: list[list[str]] = []

        def find_many(self, texts: list[str], complete: bool = False):
            assert complete  # masking never accepts a partly checked message
            self.calls.append(texts)
            return [("Lotte Vanhoutte", "PER"), ("Dewulf Metaal", "ORG")]

        def find(self, text: str):
            raise AssertionError("request text blocks should be recognized together")

    recognizer = Recognizer()
    smap = SessionMap(token_fn=lambda prefix, key: 60_000, recognizers=[recognizer])
    smap.add_values("CUS", [NAME], "test")
    st = GatewayState(home=tmp_path, default_mode="on", images_policy="withhold", smap=smap)
    body = {
        "system": "The Zorvex Holding client is covered by the exact dictionary.",
        "tools": [
            {"name": "Dewulf Metaal", "description": "Tool for Dewulf Metaal client records."}
        ],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Mail from Lotte Vanhoutte about the invoice."},
                    {"type": "text", "text": "Dewulf Metaal needs the revised invoice."},
                ],
            }
        ],
    }
    out = run(st, memo, body)
    assert len(recognizer.calls) == 1 and len(recognizer.calls[0]) == 2
    assert "Lotte Vanhoutte" not in json.dumps(out["messages"])
    assert "Dewulf Metaal" not in json.dumps(out["messages"])
    assert "Dewulf Metaal" not in out["tools"][0]["name"]
    assert out["system"] == body["system"]  # the app's own instructions: never rewritten


def test_api_field_names_are_not_masked_even_if_the_dictionary_holds_the_word(state, memo):
    state.smap.add_values("DIM", ["content", "text"], "test")
    block = {"type": "text", "text": "content"}
    out = run(state, memo, {"messages": [{"role": "user", "content": [block]}]})
    block = out["messages"][0]["content"][0]
    assert set(block) == {"type", "text"} and set(out["messages"][0]) == {"role", "content"}
    assert block["text"] != "content"


def test_same_input_gives_identical_bytes_and_keeps_unicode(state, memo):
    body = json.dumps({"messages": [{"role": "user", "content": f"café ☃ {NAME}"}]}).encode()
    a = mask_request(body, JSON, state, memo)
    b = mask_request(body, JSON, state, RemaskMemo())
    assert a == b
    assert "café ☃".encode() in a  # ensure_ascii=False
    assert a == json.dumps(json.loads(a), ensure_ascii=False, separators=(",", ":")).encode()


def test_structural_values_survive_a_dictionary_that_holds_the_same_words(state, memo):
    state.smap.add_values("DIM", ["text", "user", "object", "claude-test"], "test")
    payload = {
        "model": "claude-test",
        "messages": [{"role": "user", "content": [{"type": "text", "text": "text"}]}],
        "tools": [{"name": "t", "input_schema": {"type": "object", "properties": {}}}],
    }
    out = run(state, memo, payload)
    assert out["model"] == "claude-test"
    assert out["messages"][0]["role"] == "user"
    assert out["messages"][0]["content"][0]["type"] == "text"
    assert out["messages"][0]["content"][0]["text"] != "text"  # the VALUE is masked
    assert "text" in out["messages"][0]["content"][0]  # the KEY is not
    assert out["tools"][0]["input_schema"]["type"] == "object"


def test_nothing_inside_a_tool_use_input_is_exempt(state, memo):
    state.smap.add_values("DIM", ["text", "assistant"], "test")
    block = {
        "type": "tool_use",
        "id": "t1",
        "name": "x",
        "input": {"type": "text", "role": "assistant", "path": NAME},
    }
    out = run(state, memo, {"messages": [{"role": "assistant", "content": [block]}]})
    inp = out["messages"][0]["content"][0]["input"]
    assert inp["type"] != "text" and inp["role"] != "assistant"
    assert NAME not in json.dumps(out)
    assert out["messages"][0]["content"][0]["type"] == "tool_use"


def test_thinking_blocks_without_a_value_are_untouched_including_signature(state, memo):
    code = tok(state)
    thinking = {"type": "thinking", "thinking": f"{code} is a code\n ", "signature": "Zm9v" * 30}
    redacted = {"type": "redacted_thinking", "data": "QUJD" * 30}
    out = run(
        state,
        memo,
        {"messages": [{"role": "assistant", "content": [thinking, redacted]}]},
    )
    assert out["messages"][0]["content"] == [thinking, redacted]
    assert state.stats()["thinking_dropped"] == 0


def test_a_thinking_block_that_holds_a_real_value_is_dropped_not_sent(state, memo):
    # recorded while masking was off / before the folder was routed, resent on `claude --resume`
    leaky = {"type": "thinking", "thinking": f"The client {NAME} wants", "signature": "Zm9v" * 30}
    clean = {"type": "thinking", "thinking": "plain thoughts", "signature": "YmFy" * 30}
    redacted = {"type": "redacted_thinking", "data": "QUJD" * 30}
    text = {"type": "text", "text": f"Hello {NAME}"}
    body = {"messages": [{"role": "assistant", "content": [clean, leaky, redacted, text]}]}
    raw = mask_request(json.dumps(body).encode(), JSON, state, memo)
    assert NAME.encode() not in raw and b"Zm9v" not in raw  # block and signature are gone
    content = json.loads(raw)["messages"][0]["content"]
    # its siblings' signatures could not verify either: the whole thinking run goes, the text stays
    assert content == [{"type": "text", "text": f"Hello {tok(state)}"}]
    assert state.stats()["thinking_dropped"] == 3
    assert state.stats()["blocked"] == 0  # the request is NOT failed


def test_a_message_that_only_held_a_leaky_thinking_block_keeps_a_valid_content(state, memo):
    leaky = {"type": "thinking", "thinking": NAME2, "signature": "Zm9v" * 30}
    out = run(state, memo, {"messages": [{"role": "assistant", "content": [leaky]}]})
    content = out["messages"][0]["content"]
    assert len(content) == 1 and content[0]["type"] == "text"
    assert NAME2 not in json.dumps(out)


def test_thinking_leak_check_follows_the_dictionary(state, memo):
    block = {"type": "thinking", "thinking": "about Bravotel Group", "signature": "Zm9v" * 30}
    body = {"messages": [{"role": "assistant", "content": [block]}]}
    assert run(state, memo, body)["messages"][0]["content"] == [block]
    state.smap.add_values("CUS", ["Bravotel Group"], "test")
    out = run(state, memo, body)
    assert out["messages"][0]["content"][0]["type"] == "text"


def test_api_enum_settings_survive_a_dictionary_that_holds_the_same_words(state, memo):
    state.smap.add_values(
        "DIM", ["High", "Medium", "Standard", "Adaptive", "Summarized", "1h", "auto_only"], "t"
    )
    cache = {"type": "ephemeral", "ttl": "1h"}
    payload = {
        "model": "claude-test",
        "output_config": {"effort": "high"},
        "service_tier": "Standard",
        "thinking": {"type": "adaptive", "display": "Summarized"},
        "tool_choice": {"type": "auto", "name": "Medium"},
        "metadata": {"user_id": "Adaptive"},
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "High", "cache_control": cache}]}
        ],
    }
    out = run(state, memo, payload)
    for key in ("model", "output_config", "service_tier", "thinking", "tool_choice", "metadata"):
        assert out[key] == payload[key], key
    block = out["messages"][0]["content"][0]
    assert block["cache_control"] == cache
    assert block["text"] != "High"  # real text is still masked


def test_unknown_top_level_fields_are_still_masked(state, memo):
    out = run(state, memo, {"surprise": f"about {NAME}", "system": f"for {NAME2}"})
    assert NAME not in json.dumps(out) and NAME2 not in json.dumps(out)


def test_a_dictionary_value_equal_to_a_tokens_digits_does_not_block_the_request(tmp_path, memo):
    from core.client_guard.engine import SessionMap

    smap = SessionMap(token_fn=lambda prefix, key: 48_213)
    st = GatewayState(home=tmp_path, default_mode="on", images_policy="withhold", smap=smap)
    smap.add_values("CUS", [NAME], "test")
    smap.add_values("GLA", ["48213"], "test")  # a 5-digit ledger account
    body = {"messages": [{"role": "user", "content": f"Account 48213 of {NAME}"}]}
    for _ in range(2):  # second round: string cache
        out = run(st, memo, body)
        text = out["messages"][0]["content"]
        assert NAME not in text and "CUS_48213" in text
    # the memo-replay path runs the same check
    memo.remember("seen CUS_48213", "seen X")
    assert run(st, memo, {"m": "seen X"}) == {"m": "seen CUS_48213"}
    assert st.stats()["memo_hits"] >= 1 and st.stats()["blocked"] == 0


def test_opaque_payload_that_holds_no_value_is_unchanged_and_one_that_does_is_masked(state, memo):
    blob = "QUJD" * 40
    leaky = "QUJD" * 10 + NAME.replace(" ", "") + "QUJD" * 10
    state.smap.add_values("CUS", [NAME.replace(" ", "")], "test")
    out = run(state, memo, {"a": {"signature": blob}, "b": {"data": leaky}})
    assert out["a"]["signature"] == blob
    assert NAME.replace(" ", "") not in out["b"]["data"]


def test_escapes_in_strings_do_not_invent_leaks_and_a_retry_works(state, memo):
    # v1 lesson: a leak check over the SERIALISED json read "\n@..." as text and blocked forever
    text = 'Total:\n\tjane.doe@client.example\n"quoted" back\\slash \\n literal'
    body = {"messages": [{"role": "user", "content": text}]}
    for _ in range(3):
        out = run(state, memo, body)
        masked = out["messages"][0]["content"]
        assert "jane.doe@client.example" not in masked
        assert masked.startswith("Total:\n\t") and '"quoted" back\\slash \\n literal' in masked


def test_residual_leak_is_detected_per_leaf_and_only_for_that_request(state, memo, monkeypatch):
    real = state.smap.count_leaks
    monkeypatch.setattr(state.smap, "count_leaks", lambda s: 1)
    with pytest.raises(MaskingFailed) as exc:
        run(state, memo, {"m": f"x {NAME}"})
    assert exc.value.code == "residual_leak"
    assert NAME not in str(exc.value)
    monkeypatch.setattr(state.smap, "count_leaks", real)
    assert NAME not in json.dumps(run(state, RemaskMemo(), {"m": f"x {NAME}"}))


def test_strings_without_a_hit_are_not_leak_checked(state, memo, monkeypatch):
    monkeypatch.setattr(state.smap, "count_leaks", lambda s: 1)
    assert run(state, memo, {"m": "nothing to see"}) == {"m": "nothing to see"}


def test_engine_failure_becomes_mask_error(state, memo, monkeypatch):
    from core.client_guard.engine import MaskingError

    def boom(_s, **_kwargs):
        raise MaskingError()

    monkeypatch.setattr(state.smap, "mask_text", boom)
    with pytest.raises(MaskingFailed) as exc:
        run(state, memo, {"m": "hello"})
    assert exc.value.code == "mask_error"


def test_unexpected_exception_becomes_internal(state, memo, monkeypatch):
    def boom(_s):
        raise RuntimeError(NAME)

    monkeypatch.setattr(state.smap, "mask_text", boom)
    with pytest.raises(MaskingFailed) as exc:
        run(state, memo, {"m": "hello"})
    assert exc.value.code == "internal"
    assert NAME not in str(exc.value) and exc.value.__cause__ is None


def test_bad_json_unsupported_body_and_empty_body(state, memo):
    with pytest.raises(MaskingFailed) as e1:
        mask_request(b"{not json", JSON, state, memo)
    assert e1.value.code == "bad_json"
    with pytest.raises(MaskingFailed) as e2:
        mask_request(b"\xff\xfe", JSON, state, memo)
    assert e2.value.code == "bad_json"
    with pytest.raises(MaskingFailed) as e3:
        mask_request(b"abc", "application/octet-stream", state, memo)
    assert e3.value.code == "unsupported_body"
    with pytest.raises(MaskingFailed) as e4:
        mask_request(b"abc", "", state, memo)
    assert e4.value.code == "unsupported_body"
    assert mask_request(b"", "application/octet-stream", state, memo) == b""


def test_deeply_nested_body_fails_closed(state, memo):
    deep = "[" * 5000 + "]" * 5000
    with pytest.raises(MaskingFailed):
        mask_request(deep.encode(), JSON, state, memo)


def test_plain_text_body_is_masked(state, memo):
    out = mask_request(f"hello {NAME}".encode(), "text/plain; charset=utf-8", state, memo)
    assert NAME.encode() not in out and tok(state).encode() in out


def test_lone_surrogate_does_not_crash_serialisation(state, memo):
    out = mask_request(b'{"m": "x\\ud800y"}', JSON, state, memo)
    assert json.loads(out) == {"m": "x\ud800y"}


def test_memo_replays_the_exact_masked_text(state, memo):
    seen = f"{tok(state)} is fine"
    real = f"{NAME} is fine"
    memo.remember(seen, real)
    state.smap.add_values("CUS", [f"{NAME} is"], "test")  # a fresh mask would now differ
    assert state.smap.mask_text(real)[0] != seen
    out = run(state, memo, {"m": real})
    assert out == {"m": seen}
    assert state.stats()["memo_hits"] == 1


def test_a_memo_entry_that_would_leak_is_not_replayed(state, memo):
    # as if the dictionary had grown since the model saw this text: the replay still holds a value
    memo.remember(f"seen {NAME}", "seen X")
    out = run(state, memo, {"m": "seen X"})
    assert NAME not in json.dumps(out)
    assert out == {"m": "seen X"}
    assert state.stats()["memo_hits"] == 0


def test_string_cache_follows_the_dictionary(state, memo):
    text = "alpha Bravotel Group beta"
    first = run(state, memo, {"m": text})["m"]
    assert first == text
    state.smap.add_values("CUS", ["Bravotel Group"], "test")
    second = run(state, memo, {"m": text})["m"]
    assert "Bravotel" not in second and second != first
    again = run(state, RemaskMemo(), {"m": text})["m"]
    assert again == second  # cached or recomputed: same bytes


def test_cached_result_equals_a_fresh_one(state, memo):
    body = {"m": f"{NAME} and {NAME2}"}
    a = mask_request(json.dumps(body).encode(), JSON, state, memo)
    state.mask_cache.clear()
    b = mask_request(json.dumps(body).encode(), JSON, state, memo)
    assert a == b


def test_shape_hits_are_masked_and_not_cached_stale(state, memo):
    out = run(state, memo, {"m": "mail a.b@corp-example.org now"})
    assert "a.b@corp-example.org" not in out["m"]
    again = run(state, memo, {"m": "mail a.b@corp-example.org now"})
    assert again == out


# ---------------------------------------------------------------------------------- images
def image_block(data: str = "AAAA") -> dict[str, Any]:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}}


def test_image_blocks_are_withheld_under_the_withhold_policy(state, memo):
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    image_block(),
                    {"type": "tool_result", "tool_use_id": "x", "content": [image_block("BBBB")]},
                ],
            }
        ]
    }
    out = run(state, memo, payload)
    first = out["messages"][0]["content"][0]
    nested = out["messages"][0]["content"][1]["content"][0]
    assert first["type"] == "text" and first["text"].startswith("[Client masking: image withheld")
    assert nested["type"] == "text"
    assert state.stats()["images_withheld"] == 2


def test_image_policy_pass_leaves_the_block(tmp_path, memo):
    st = GatewayState(home=tmp_path, images_policy="pass")
    block = image_block()
    out = run(st, memo, {"messages": [{"role": "user", "content": [block]}]})
    assert out["messages"][0]["content"][0] == block


def test_image_redaction_goes_through_redact_image_block_and_is_cached(tmp_path, memo, monkeypatch):
    st = GatewayState(home=tmp_path, images_policy="redact")
    calls: list[int] = []

    def fake(block, smap, policy="redact", engines=None, stats=None):
        calls.append(1)
        if stats is not None:
            stats.update({"hits": 2, "withheld": None})
        out = dict(block)
        out["source"] = {**block["source"], "data": "REDACTED"}
        return out

    monkeypatch.setattr(masking._images, "redact_image_block", fake)
    payload = {"messages": [{"role": "user", "content": [image_block("ORIG")]}]}
    one = mask_request(json.dumps(payload).encode(), JSON, st, memo)
    two = mask_request(json.dumps(payload).encode(), JSON, st, memo)
    assert one == two and b"REDACTED" in one and b"ORIG" not in one
    assert len(calls) == 1  # the same screenshot in the next turn is not read again
    assert st.stats()["images_redacted"] == 2
    st.smap.add_values("CUS", ["Whatever Corp"], "test")  # new dictionary: read again
    mask_request(json.dumps(payload).encode(), JSON, st, memo)
    assert len(calls) == 2


def test_transient_ocr_failures_are_not_cached(tmp_path, memo, monkeypatch):
    st = GatewayState(home=tmp_path, images_policy="redact")
    calls: list[int] = []

    def flaky(block, smap, policy="redact", engines=None, stats=None):
        calls.append(1)
        reason = "OCR timed out" if len(calls) == 1 else "document block"
        if stats is not None:
            stats.update({"hits": 0, "withheld": reason})
        return masking._images.withheld_block(reason)

    monkeypatch.setattr(masking._images, "redact_image_block", flaky)
    payload = json.dumps({"messages": [{"role": "user", "content": [image_block()]}]}).encode()
    for _ in range(3):
        mask_request(payload, JSON, st, memo)
    assert len(calls) == 2  # timeout retried once, the permanent reason then cached


def test_an_image_look_alike_inside_tool_input_is_plain_data(state, memo):
    block = {
        "type": "tool_use",
        "id": "t",
        "name": "n",
        "input": {"type": "image", "source": {"data": NAME}},
    }
    out = run(state, memo, {"messages": [{"role": "assistant", "content": [block]}]})
    assert out["messages"][0]["content"][0]["input"]["source"]["data"] == tok(state)


def test_tool_definitions_keep_their_keywords_but_lose_client_phrases():
    import json as _json

    from client_gateway.masking import mask_request
    from client_gateway.memo import RemaskMemo
    from client_gateway.state import GatewayState
    from core.client_guard.engine import SessionMap

    st = GatewayState(smap=SessionMap(token_fn=lambda p, k: 20_000 + len(k)))
    st.smap.add_values("DIM", ["Include", "Status"], "t")
    st.smap.add_values("CUS", ["Zorvex Holding"], "t")
    st.set_ready(True)
    body = {
        "tools": [
            {
                "name": "x",
                "description": "Report for Zorvex Holding",
                "input_schema": {"properties": {"polarity": {"enum": ["Include", "Status"]}}},
            }
        ],
        "messages": [{"role": "user", "content": "Include"}],
    }
    out = _json.loads(
        mask_request(_json.dumps(body).encode(), "application/json", st, RemaskMemo())
    )
    tool = out["tools"][0]
    assert tool["input_schema"]["properties"]["polarity"]["enum"] == ["Include", "Status"]
    assert "Zorvex" not in tool["description"]
    assert out["messages"][0]["content"] != "Include"  # an exact cell in the conversation is masked


def test_ner_that_could_not_finish_lets_the_request_go_on(state):
    # owner rule 2026-10-07: a masked chat keeps working; the dictionary still masks
    from core.client_guard.ner import NerIncomplete

    class Unfinished:
        def find_many(self, texts, complete=False):
            raise NerIncomplete("timeout_or_worker")

    state.smap._recognizers = [Unfinished()]
    body = json.dumps(
        {
            "messages": [
                {"role": "user", "content": "Graag een update van de klant over de factuur."}
            ]
        }
    )
    state.smap.add_values("CUS", ["Vanhoutte Bouwwerken NV"], "t")
    body = body.replace("de klant", "Vanhoutte Bouwwerken NV")
    out = mask_request(body.encode(), "application/json", state, RemaskMemo())
    assert b"Vanhoutte" not in out
    assert state.stats()["ner_partial"] == 1


def test_tool_schema_keywords_are_never_masked(state):
    """A dictionary word in a ``type`` list or an ``enum`` made Anthropic reject the request
    ("input_schema: JSON schema is invalid"); descriptions are still masked."""
    state.smap.add_values("DIM", ["boolean", "Decimal", "viewer"], "t")
    state.smap.add_values("CUS", ["Vanhoutte Bouwwerken NV"], "t")
    tool = {
        "name": "pbi_tool",
        "description": "Report for Vanhoutte Bouwwerken NV",
        "input_schema": {
            "type": "object",
            "required": ["value"],
            "properties": {
                "value": {"type": ["boolean", "string"], "description": "x"},
                "kind": {"type": "string", "enum": ["Decimal", "viewer"], "default": "viewer"},
            },
        },
    }
    body = {"model": "m", "tools": [tool], "messages": [{"role": "user", "content": "hi"}]}
    out = json.loads(
        mask_request(json.dumps(body).encode(), "application/json", state, RemaskMemo())
    )
    sent = out["tools"][0]
    assert sent["input_schema"] == tool["input_schema"] and sent["name"] == "pbi_tool"
    assert "Vanhoutte" not in sent["description"]
