"""client_gateway.adapters core: codecs, SSE framing, the split-safe hold, R4 control, routing and
the MaskContext. Synthetic names only; no network, sockets, threads or processes."""

from __future__ import annotations

import zlib
from typing import Any

import pytest

from client_gateway import images as _images
from client_gateway.adapters import base as adapter_base
from client_gateway.adapters.base import ImageWithheld, MaskContext, Route, norm_host, norm_path
from client_gateway.adapters.control import typed_command
from client_gateway.adapters.registry import adapter_for, adapters
from client_gateway.adapters.streams import (
    PassthroughRehydrator,
    SSEFramer,
    TailHold,
    UnsupportedEncoding,
    decode_bytes,
    decode_stream,
    encode_bytes,
    encode_stream,
    frame_event,
    normalize_encoding,
    parse_event,
    rebuild_event,
)
from client_gateway.masking import MaskingFailed
from client_gateway.memo import RemaskMemo
from tests._wire_support import MARKER, NAME, NAME2, make_state, resplit, token_of

ENCODINGS = ["identity", "gzip", "deflate", "br", "zstd"]
PAYLOAD = [b"event: delta\n", "café ☃ ".encode(), b"x" * 5000, b"data: tail\n\n"]


@pytest.fixture()
def state(tmp_path):
    return make_state(tmp_path)


# ---------------------------------------------------------------------------------- codecs
@pytest.mark.parametrize("encoding", ENCODINGS)
def test_codec_round_trip_at_every_chunking(encoding):
    data = b"".join(PAYLOAD)
    wire = encode_bytes(data, encoding)
    assert decode_bytes(wire, encoding) == data
    for size in (1, 7, 64, len(wire)):
        dec = decode_stream(encoding)
        out = b"".join(dec.feed(wire[i : i + size]) for i in range(0, len(wire), size))
        assert out + dec.finish() == data


@pytest.mark.parametrize("encoding", ["gzip", "deflate", "br", "zstd"])
def test_encoder_flushes_every_feed_so_a_client_sees_each_event(encoding):
    enc = encode_stream(encoding)
    first = enc.feed(b"first event\n\n")
    dec = decode_stream(encoding)  # a client that has received ONLY the first flush
    assert dec.feed(first) == b"first event\n\n"
    assert enc.feed(b"") == b""  # nothing to flush
    rest = enc.feed(b"second\n\n") + enc.finish()
    assert dec.feed(rest) + dec.finish() == b"second\n\n"


def test_deflate_accepts_a_raw_stream_and_a_one_byte_first_chunk():
    comp = zlib.compressobj(6, zlib.DEFLATED, -zlib.MAX_WBITS)
    raw = comp.compress(b"raw deflate body") + comp.flush()
    dec = decode_stream("deflate")
    out = b"".join(dec.feed(raw[i : i + 1]) for i in range(len(raw)))
    assert out + dec.finish() == b"raw deflate body"


def test_encoding_names_and_unsupported_ones():
    assert [normalize_encoding(e) for e in (None, "", "Identity", "GZIP", "x-gzip", "br")] == [
        "identity", "identity", "identity", "gzip", "gzip", "br",
    ]  # fmt: skip
    for bad in ("compress", "gzip, br", "dcb"):
        with pytest.raises(UnsupportedEncoding):
            normalize_encoding(bad)
        with pytest.raises(UnsupportedEncoding):
            decode_stream(bad)


# ---------------------------------------------------------------------------------- SSE framing
def _frames(chunks: list[bytes]) -> tuple[list[bytes], bytes]:
    framer = SSEFramer()
    out = [raw for chunk in chunks for raw, _ in framer.feed(chunk)]
    return out, framer.rest()


def test_framer_gives_the_same_frames_at_every_split():
    data = b"event: a\ndata: {}\n\ndata: [DONE]\n\ndata: unterminated"
    whole = _frames([data])
    assert whole == ([b"event: a\ndata: {}", b"data: [DONE]"], b"data: unterminated")
    for cut in range(1, len(data)):
        assert _frames(resplit(data, [cut])) == whole
    assert _frames([data[i : i + 1] for i in range(len(data))]) == whole


def test_framer_handles_crlf_and_remembers_the_line_ending():
    framer = SSEFramer()
    frames = framer.feed(b"data: 1\r\n\r\ndata: 2\r\n\r\n")
    assert [f for f, _ in frames] == [b"data: 1", b"data: 2"] and framer.eol == "\r\n"


def test_parse_and_rebuild_keep_the_other_lines():
    lines, text = parse_event(b'event: delta\ndata: {"v":"a"}  ')
    assert text == '{"v":"a"}  ' and lines[0] == "event: delta"
    out = rebuild_event(lines, {"v": "b"}, "\n")
    assert out == b'event: delta\ndata: {"v":"b"}'
    assert parse_event(b": keep-alive") == ([": keep-alive"], None)
    assert parse_event(b"\xff\xfe") == ([], None)
    assert frame_event("delta", {"v": 1}, "\r\n") == b'event: delta\r\ndata: {"v":1}'
    assert frame_event(None, {"v": 1}, "\n") == b'data: {"v":1}'


# ---------------------------------------------------------------------------------- TailHold
def test_tail_hold_gives_the_unmasked_text_for_every_split_of_a_pseudonym(state):
    token = token_of(state, NAME)
    text = f"Customer: {token} and (NV) later {token}."
    expected = state.smap.unmask_text(text)[0]
    assert NAME in expected and token not in expected
    for cut in range(len(text) + 1):
        hold = TailHold(state.smap)
        out = hold.feed(text[:cut]) + hold.feed(text[cut:]) + hold.flush()
        assert out == expected, cut
    hold = TailHold(state.smap)  # one character at a time
    assert "".join(hold.feed(c) for c in text) + hold.flush() == expected


def test_tail_hold_holds_only_a_possible_token_tail_and_records_the_pair(state):
    token = token_of(state, NAME)
    hold = TailHold(state.smap)
    assert hold.feed("Hello CUS") == "Hello "  # "CUS" may become a token
    assert hold.held == "CUS"
    assert hold.feed(token[3:]) == ""  # a complete token can still grow one more digit
    assert hold.feed(" is a client.") == f"{NAME} is a client."
    assert hold.flush() == "" and hold.held == ""
    assert hold.masked == f"Hello {token} is a client."
    assert hold.unmasked == f"Hello {NAME} is a client."
    assert not hold.failed and hold.tokens == 1


def test_tail_hold_json_context_escapes_the_real_value(state):
    state.smap.add_values("CUS", ['Quote "Q" Holding'], "test")
    token = token_of(state, 'Quote "Q" Holding')
    hold = TailHold(state.smap, "json")
    out = hold.feed('{"customer": "' + token[:5]) + hold.feed(token[5:] + '"}') + hold.flush()
    assert out == '{"customer": "Quote \\"Q\\" Holding"}'


def test_tail_hold_survives_an_engine_error(state):
    class Boom:
        def unmask_text(self, *args: Any, **kwargs: Any):
            raise RuntimeError("boom")

    hold = TailHold(Boom())  # type: ignore[arg-type]
    assert hold.feed("masked CUS_12345 text") == "masked CUS_12345 text"
    assert hold.failed and hold.errors == 1


# ---------------------------------------------------------------------------------- control (R4)
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("masking off", "off"), ("  Masking OFF \n", "off"), ("/masking off", "off"),
        ("masking on", "on"), ("MASKING ON", "on"), ("/Masking   On", "on"),
        ("masking off please", None), ("please masking off", None), ("masking", None),
        ("masking off\nmasking on", None), ("//masking off", None), ("", None), (None, None),
        ("we use masking off as a phrase", None), ("`masking off`", None),
        ("<userRequest>masking off</userRequest>", None), ("masking off.", None),
    ],
)  # fmt: skip
def test_typed_command_only_matches_a_whole_message_command(text, expected):
    assert typed_command(text) == expected


# ---------------------------------------------------------------------------------- registry
@pytest.mark.parametrize(
    ("args", "name"),
    [
        (("POST", "api.individual.githubcopilot.com", "/chat/completions", "application/json"), "openai_chat"),
        (("POST", "api.githubcopilot.com", "/chat/completions?x=1", ""), "openai_chat"),
        (("POST", "API.business.githubcopilot.com:443", "/chat/completions/", "application/json"), "openai_chat"),
        (("POST", "chatgpt.com", "/backend-api/f/conversation", "application/json"), "chatgpt_web"),
        (("POST", "chatgpt.com", "/backend-api/f/conversation/prepare", "application/json"), "chatgpt_web"),
        (("POST", "chatgpt.com", "/backend-api/files", "application/json"), "chatgpt_web"),
        (("POST", "chatgpt.com", "/backend-api/files/process_upload_stream", "application/json"), "chatgpt_web"),
        (("PUT", "sdmnt.oaiusercontent.com", "/files/x/raw", "image/png"), "chatgpt_web"),
        (("GET", "chatgpt.com", "/backend-api/conversations/6ac62844-1e8c-83ed-9ce5-d109cb0ef483", ""), "chatgpt_web"),
        (("GET", "chatgpt.com", "/backend-api/conversation/6ac62844-1e8c-83ed-9ce5-d109cb0ef483", ""), "chatgpt_web"),
        (("GET", "chatgpt.com", "/backend-api/conversations", ""), "chatgpt_web"),
    ],
)  # fmt: skip
def test_adapter_for_finds_the_right_adapter(args, name):
    adapter = adapter_for(*args)
    assert adapter is not None and adapter.name == name


@pytest.mark.parametrize(
    "args",
    [
        ("GET", "api.individual.githubcopilot.com", "/chat/completions", ""),
        ("POST", "api.individual.githubcopilot.com", "/models", "application/json"),
        ("POST", "api.individual.githubcopilot.com", "/chat/completions", "text/plain"),
        ("POST", "evilgithubcopilot.com", "/chat/completions", "application/json"),
        ("POST", "chatgpt.com", "/backend-api/conversation/init", "application/json"),
        ("POST", "chatgpt.com", "/backend-api/f/conversation", "text/plain"),
        ("OPTIONS", "sdmnt.oaiusercontent.com", "/files/x/raw", ""),
        ("GET", "chatgpt.com", "/backend-api/me", ""),
        ("POST", "example.org", "/chat/completions", "application/json"),
        ("POST", "api.anthropic.com", "/v1/messages", "application/json"),
    ],
)
def test_adapter_for_returns_none_for_anything_else(args):
    assert adapter_for(*args) is None


def test_the_adapters_are_registered_and_names_are_unique():
    assert [a.name for a in adapters()] == ["openai_chat", "openai_responses", "chatgpt_web"]


def test_host_and_path_normalisation():
    assert norm_host("API.Host.com:443.") == "api.host.com" and norm_host("[::1]:80") == "[::1]:80"
    assert norm_path("/a/b/?q=1#f") == "/a/b" and norm_path("") == "/" and norm_path("/") == "/"


# ---------------------------------------------------------------------------------- MaskContext
def test_text_masks_and_strips_the_marker_before_masking(state):
    ctx = MaskContext(state, marker=MARKER)
    out = ctx.text(f'{{"_fv":"{MARKER}","c":"{NAME}"}}')
    assert NAME not in out and MARKER not in out and '"_fv":"fvpbi"' in out
    assert ctx.marker_seen
    plain = MaskContext(state, marker=MARKER)
    assert plain.text("no marker here") == "no marker here" and not plain.marker_seen
    assert MaskContext(state, marker=MARKER).scan_marker(f"x {MARKER} y".encode())
    assert MaskContext(state).marker == (state.marker or None)


def test_text_whole_false_is_the_developer_text_rule(state):
    state.smap.add_values("DIM", ["status"], "test")
    ctx = MaskContext(state, marker=None)
    assert ctx.text("status") != "status"  # a cell that is just that word
    assert ctx.text("status", whole=False) == "status"  # a keyword in developer text
    assert NAME not in ctx.text(f"for {NAME}", whole=False)  # a name is masked anyway


def test_memo_replays_the_exact_masked_text_the_model_saw(state):
    memo = RemaskMemo()
    ctx = MaskContext(state, memo, marker=None)
    memo.remember("CUS_00001 wrote this", f"{NAME} wrote this")
    assert ctx.text(f"{NAME} wrote this") == "CUS_00001 wrote this"
    assert ctx.replay(f"{NAME} wrote this") == "CUS_00001 wrote this"
    assert ctx.replay("unknown text") is None
    memo.remember(f"leaks {NAME}", "leaky text")  # a replay that still holds a name is refused
    assert ctx.replay("leaky text") is None


def test_mask_tree_skips_keys_and_can_mask_keys(state):
    ctx = MaskContext(state, marker=None)
    tree = {"keep": NAME, "x": [NAME, {NAME2: NAME}], "n": 1}
    out = ctx.mask_tree(tree, skip=frozenset({"keep"}))
    assert out["keep"] == NAME and NAME not in out["x"][0] and list(out["x"][1]) == [NAME2]
    keyed = ctx.mask_tree(tree, skip=frozenset({"keep"}), keys=True)
    assert list(keyed["x"][1]) != [NAME2] and keyed["n"] == 1
    deep: Any = "x"
    for _ in range(adapter_base.MAX_DEPTH + 5):
        deep = [deep]
    with pytest.raises(MaskingFailed) as err:
        ctx.mask_tree(deep)
    assert err.value.code == "too_deep"


def test_check_verbatim_fails_closed_on_an_identity_value(state):
    ctx = MaskContext(state, marker=None)
    ctx.check_verbatim({"model": "claude-test", "n": 3})
    with pytest.raises(MaskingFailed) as err:
        ctx.check_verbatim({"user": NAME})
    assert err.value.code == "residual_leak" and NAME not in str(err.value)


class FakeRecognizer:
    def __init__(self, raises: Exception | None = None) -> None:
        self.calls: list[list[str]] = []
        self.raises = raises

    def find_many(self, texts: list[str], complete: bool = False):
        assert complete
        self.calls.append(list(texts))
        if self.raises:
            raise self.raises
        return []

    def find(self, text: str):
        raise AssertionError("batched recognition expected")


def _recognizer_state(tmp_path, recognizer):
    from client_gateway.state import GatewayState
    from core.client_guard.engine import SessionMap

    smap = SessionMap(token_fn=lambda prefix, key: 60_000, recognizers=[recognizer])
    return GatewayState(home=tmp_path, default_mode="on", images_policy="withhold", smap=smap)


def test_recognize_batches_texts_and_marks_the_history_scanned(tmp_path):
    rec = FakeRecognizer()
    ctx = MaskContext(_recognizer_state(tmp_path, rec), marker=None)
    ctx.recognize(
        ["one", "", "<system-reminder>The following skills are available: x</system-reminder>"]
    )
    assert rec.calls == [["one"]] and not ctx.memo.history_scanned
    ctx.recognize(["two"], history=True)
    assert ctx.memo.history_scanned


def test_recognize_goes_on_when_ner_cannot_finish_but_fails_closed_on_a_crash(tmp_path):
    from core.client_guard.ner import NerIncomplete

    state = _recognizer_state(tmp_path, FakeRecognizer(NerIncomplete("x")))
    MaskContext(state, marker=None).recognize(["text"])  # never refused (owner rule 2026-10-07)
    MaskContext(state, marker=None, ner_degrade=True).recognize(["text"])
    assert state.stats()["ner_partial"] == 2
    other = _recognizer_state(tmp_path, FakeRecognizer(RuntimeError("boom")))
    with pytest.raises(MaskingFailed) as err:
        MaskContext(other, marker=None, ner_degrade=True).recognize(["text"])
    assert err.value.code == "mask_error"


# ------------------------------------------------------------------ images (fake redactor)
class FakeRedactor:
    def __init__(self) -> None:
        self.calls = 0
        self.mode = "redact"  # redact | clean | withhold | crash | transient

    def __call__(self, data: bytes, smap: Any, engines: Any = None) -> _images.ImageResult:
        self.calls += 1
        if self.mode == "withhold":
            raise _images._Withhold(_images.W_RESIDUAL)
        if self.mode == "crash":
            raise RuntimeError("boom " + NAME)
        if self.mode == "transient":
            raise _images._Withhold(_images.W_OCR_TIMEOUT)
        if self.mode == "clean":
            return _images.ImageResult(None, None, 0, {"ocr_norm": "nothing"})
        return _images.ImageResult(b"REDACTED:" + data, None, 1, {"ocr_norm": "wibblesnort"})


@pytest.fixture()
def redactor(monkeypatch):
    fake = FakeRedactor()
    monkeypatch.setattr(_images, "redact_image_bytes", fake)
    return fake


def test_image_policies(tmp_path, redactor):
    for policy, outcome in (("pass", b"IMG"), ("redact", b"REDACTED:IMG")):
        ctx = MaskContext(make_state(tmp_path, policy), marker=None)
        assert ctx.image_bytes(b"IMG") == outcome
    with pytest.raises(ImageWithheld) as err:
        MaskContext(make_state(tmp_path, "withhold"), marker=None).image_bytes(b"IMG")
    assert err.value.reason == _images.W_POLICY and redactor.calls == 1


def test_image_clean_withheld_and_failing_redactors(tmp_path, redactor):
    state = make_state(tmp_path, "redact")
    redactor.mode = "clean"
    assert MaskContext(state, marker=None).image_bytes(b"A") == b"A"  # nothing to redact
    redactor.mode = "withhold"
    with pytest.raises(ImageWithheld) as err:
        MaskContext(state, marker=None).image_bytes(b"B")
    assert err.value.reason == _images.W_RESIDUAL
    redactor.mode = "crash"
    with pytest.raises(ImageWithheld) as err:
        MaskContext(state, marker=None).image_bytes(b"C")
    assert err.value.reason == _images.W_OCR_FAILED and NAME not in err.value.reason


def test_image_result_is_cached_by_content_but_a_transient_failure_is_not(tmp_path, redactor):
    state = make_state(tmp_path, "redact")
    ctx = MaskContext(state, marker=None)
    assert ctx.image_bytes(b"IMG") == b"REDACTED:IMG"
    assert ctx.image_bytes(b"IMG") == b"REDACTED:IMG" and redactor.calls == 1
    redactor.mode = "transient"
    for _ in range(2):
        with pytest.raises(ImageWithheld):
            ctx.image_bytes(b"SLOW")
    assert redactor.calls == 3  # timed out twice: it was tried again
    redactor.mode = "withhold"
    for _ in range(2):
        with pytest.raises(ImageWithheld):
            ctx.image_bytes(b"BAD")
    assert redactor.calls == 4  # a real withhold is remembered for this dictionary


def test_a_new_dictionary_value_redoes_an_image_that_could_show_it(tmp_path, redactor):
    state = make_state(tmp_path, "redact")
    ctx = MaskContext(state, marker=None)
    ctx.image_bytes(b"IMG")
    state.smap.add_values("CUS", ["Unrelated Newcomer"], "test")  # the revision changes
    ctx.image_bytes(b"IMG")
    assert redactor.calls == 1  # the new value does not occur in the OCR text of the image
    state.smap.add_values("CUS", ["Wibblesnort Partners"], "test")
    ctx.image_bytes(b"IMG")
    assert redactor.calls == 2  # the new value occurs in the OCR text: read again


def test_finish_adds_the_counters_once(tmp_path, redactor):
    state = make_state(tmp_path, "redact")
    ctx = MaskContext(state, marker=None)
    ctx.text(f"for {NAME}")
    ctx.image_bytes(b"IMG")
    ctx.unmask(token_of(state, NAME))
    ctx.finish()
    ctx.finish()
    stats = state.stats()
    assert stats["masked_strings"] >= 1 and stats["images_redacted"] == 1
    assert stats["tokens_rehydrated"] == 1


# ------------------------------------------------------------------ rehydration helpers
def test_unmask_never_raises_and_unmask_obj_leaves_ids_alone(state):
    token = token_of(state, NAME)
    ctx = MaskContext(state, marker=None)
    tree = {"id": token, "t": [f"x {token}", {"deep": token}], "args": '{"c": "' + token + '"}'}
    out = ctx.unmask_obj(tree, frozenset({"id"}), frozenset({"args"}))
    assert out["id"] == token and out["t"] == [f"x {NAME}", {"deep": NAME}]
    assert out["args"] == '{"c": "' + NAME + '"}'

    class Boom:
        def unmask_text(self, *args: Any, **kwargs: Any):
            raise RuntimeError("boom")

    ctx.state = type("S", (), {"smap": Boom()})()  # type: ignore[assignment]
    assert ctx.unmask(f"x {token}") == f"x {token}"


def test_report_chat_id_calls_back_once_per_id_and_never_raises(state):
    seen: list[str] = []
    ctx = MaskContext(state, marker=None, on_chat_id=seen.append)
    for cid in ("a", "a", None, "", 5, "b", "a"):
        ctx.report_chat_id(cid)
    assert seen == ["a", "b", "a"]

    def boom(_: str) -> None:
        raise RuntimeError("boom")

    MaskContext(state, marker=None, on_chat_id=boom).report_chat_id("x")  # swallowed


def test_route_is_a_plain_value():
    route = Route("POST", "chatgpt.com", "/backend-api/f/conversation", "application/json")
    assert route.method == "POST" and Route("GET", "h", "/").content_type == ""


def test_passthrough_rehydrator_forwards_bytes_unchanged():
    p = PassthroughRehydrator(errors=1)
    assert p.feed(b"abc") == b"abc" and p.finish() == b"" and p.stats["errors"] == 1
