"""ChatGPT image uploads in masked chats (phase 3b.0, item 3). Synthetic names only; no sockets.

An upload is three requests: ``POST /backend-api/files`` (registration; its reply names the file),
``PUT <host>.oaiusercontent.com/files/<id>/raw`` (the bytes) and the chat send that references the
file. The registration carries the page the user is on in its same-origin ``Referer`` (``/c/<id>``),
which attributes the file to a chat; the bytes of a masked chat's file are redacted / withheld; a
masked chat's send that references a file nobody redacted or checked is blocked (fail closed). A chat
that is not masked is never touched (R2). The residual: a first-turn upload of a brand-new chat has no
id to attribute it to.
"""

from __future__ import annotations

import json

import pytest

from client_gateway.adapters.base import MaskContext, Route
from client_gateway.adapters.chatgpt_web import ChatGPTWebAdapter
from client_gateway.frontend.addon import BLOCK_ATTACHMENT_TEXT, FrontendAddon
from client_gateway.state import FILE_TTL_S
from tests._wire_support import NAME, body_bytes, load_fixture
from tests.test_frontend_addon import (
    CHATGPT,
    CID,
    JSON_HEADERS,
    OTHER_CID,
    flow_of,
    make_state,
    respond,
    sha,
)

ADAPTER = ChatGPTWebAdapter()
FILE_ID = "file_00000000000000000000000000000001"  # the file of the recorded image turn
UPLOAD_ID = "00000000-0000-4000-8000-000000000026"
FILES_URL = "https://chatgpt.com/backend-api/files"
PUT_URL = f"https://files-test.oaiusercontent.com/files/{UPLOAD_ID}/raw?sig=abc"
PNG = b"\x89PNG-bytes"
REDACTED = b"REDACTED-PNG"


@pytest.fixture()
def state(tmp_path):
    return make_state(tmp_path)


@pytest.fixture()
def addon(state):
    return FrontendAddon(state)


@pytest.fixture(autouse=True)
def redactor(monkeypatch):
    monkeypatch.setattr(MaskContext, "image_bytes", lambda self, data: REDACTED)


def registration(page: str | None, file_name: str = "chart.png"):
    headers = dict(JSON_HEADERS)
    if page is not None:
        headers["referer"] = page
    body = json.dumps({"file_name": file_name, "file_size": 10, "use_case": "multimodal"}).encode()
    return flow_of("POST", FILES_URL, body, headers)


def reply(flow, file_id: str = FILE_ID, upload_id: str = UPLOAD_ID, status: int = 200):
    payload = {
        "status": "success",
        "file_id": file_id,
        "upload_url": f"https://files-test.oaiusercontent.com/files/{upload_id}/raw?sig=abc",
    }
    respond(flow, status, {"content-type": "application/json"}, json.dumps(payload).encode())


def upload(addon, page: str | None, **kw):
    """The registration of a file (attributed through ``page``) and its reply."""
    flow = registration(page, **kw)
    addon.process_request(flow)
    reply(flow)
    addon.response(flow)
    return flow


def put(addon, url: str = PUT_URL):
    flow = flow_of("PUT", url, PNG, {"content-type": "image/png"})
    addon.process_request(flow)
    return flow


def image_turn(chat: str | None = CID):
    fx = load_fixture("chatgpt_conversation_image_turn")
    tree = json.loads(body_bytes(fx))
    if chat is None:
        tree.pop("conversation_id", None)
    else:
        tree["conversation_id"] = chat
    r = fx["request"]
    data = json.dumps(tree, ensure_ascii=False, separators=(",", ":")).encode()
    return flow_of(r["method"], f"https://{r['host']}{r['path']}", data, JSON_HEADERS)


def page(chat: str = CID) -> str:
    return f"https://chatgpt.com/c/{chat}"


def mask_chat(state, chat: str = CID) -> None:
    state.frontend_mode(chat, True, CHATGPT)  # a PBI chat (the marker was seen)


# ---------------------------------------------------------------------------------- the adapter
def route(path: str = "/backend-api/files", referer: str = "", host: str = "chatgpt.com"):
    return Route("POST", host, path, "application/json", "", referer)


def test_a_registration_is_attributed_through_its_same_origin_referer():
    body = b'{"file_name":"a.png"}'
    assert ADAPTER.chat_key(body, route(referer=page())) == CID
    assert ADAPTER.chat_key(body, route(referer=page() + "?model=x#frag")) == CID
    assert ADAPTER.chat_key(body, route(referer=f"https://chatgpt.com/g/g-abc/c/{CID}")) == CID
    assert ADAPTER.chat_key(body, route(referer=f"https://chatgpt.com/c/{CID}/")) == CID


def test_a_referer_that_names_no_chat_or_another_origin_attributes_nothing():
    body = b'{"file_name":"a.png"}'
    for referer in (
        "",
        "https://chatgpt.com/",
        "https://chatgpt.com/c/not-a-uuid",
        f"https://evil.example/c/{CID}",
        f"https://chatgpt.com.evil.example/c/{CID}",
        "https://user@chatgpt.com@evil.example/c/" + CID,
        "not a url",
        "http://[bad/c/" + CID,
    ):
        assert ADAPTER.chat_key(body, route(referer=referer)) is None, referer


def test_the_referer_only_attributes_a_registration_never_a_send_or_a_draft():
    body = b'{"messages":[]}'
    for path in ("/backend-api/f/conversation", "/backend-api/f/conversation/prepare"):
        assert ADAPTER.chat_key(body, route(path, referer=page())) is None
    assert (
        ADAPTER.chat_key(
            b'{"conversation_id":"' + OTHER_CID.encode() + b'"}', route(referer=page())
        )
        == OTHER_CID
    )  # the body's own id wins


def test_the_upload_ids_of_a_put_url_and_of_a_registration_reply():
    assert ADAPTER.file_ids_in_url(f"/files/{UPLOAD_ID}/raw") == (UPLOAD_ID,)
    assert ADAPTER.file_ids_in_url(f"/files/{FILE_ID}/raw?x=1") == (FILE_ID,)
    assert ADAPTER.file_ids_in_url("/other") == ()
    reply_body = json.dumps(
        {
            "file_id": FILE_ID,
            "upload_url": f"https://h.oaiusercontent.com/files/{UPLOAD_ID}/raw?s=1",
        }
    ).encode()
    assert ADAPTER.response_file_ids(reply_body) == (FILE_ID, UPLOAD_ID)
    for bad in (b"", b"not json", b"[]", b'{"file_id": 3}', b'{"upload_url": "http://[x"}'):
        assert ADAPTER.response_file_ids(bad) == ()


def test_the_attachment_references_of_a_send():
    body = body_bytes(load_fixture("chatgpt_conversation_image_turn"))
    assert ADAPTER.attachment_refs(body) == (FILE_ID,)
    assert ADAPTER.attachment_refs(body_bytes(load_fixture("chatgpt_conversation_turn1"))) == ()
    assert ADAPTER.attachment_refs(b"not json") == ()
    assert ADAPTER.attachment_refs(b'{"partial_query": {}}') == ()


# ---------------------------------------------------------------------------------- the state
def test_the_registry_remembers_for_fifteen_minutes_and_shares_one_note_per_upload(state):
    clock = [1000.0]
    state.clock = lambda: clock[0]
    state.frontend_note_file(CHATGPT, CID, (FILE_ID, UPLOAD_ID))
    assert state.frontend_file_chat(CHATGPT, FILE_ID) == CID
    assert state.frontend_file_chat(CHATGPT, UPLOAD_ID) == CID
    assert state.frontend_files_unchecked(CHATGPT, (FILE_ID,)) == 1
    state.frontend_file_checked(CHATGPT, (UPLOAD_ID,))  # the alias marks the whole upload
    assert state.frontend_files_unchecked(CHATGPT, (FILE_ID,)) == 0
    clock[0] += FILE_TTL_S + 1
    assert state.frontend_file_chat(CHATGPT, FILE_ID) is None
    assert state.frontend_files_unchecked(CHATGPT, (FILE_ID,)) == 1  # unknown counts as unchecked
    assert state.frontend_file_chat("copilot", FILE_ID) is None  # per chat space


def test_the_registry_is_bounded(state, monkeypatch):
    monkeypatch.setattr("client_gateway.state.FILE_MAX", 5)
    for i in range(20):
        state.frontend_note_file(CHATGPT, CID, (f"file_{i}",))
    assert len(state._frontend_files) <= 5
    assert state.frontend_file_chat(CHATGPT, "file_19") == CID


# ---------------------------------------------------------------------------------- the flow
def test_the_whole_upload_of_a_masked_chat_is_redacted_and_the_send_passes(addon, state):
    mask_chat(state)
    flow = upload(addon, page(), file_name=f"{NAME}.png")
    assert flow.response.status_code == 200 and b"file_id" in flow.response.raw_content
    assert NAME not in flow.request.raw_content.decode()  # the registration was masked too
    assert state.stats()["frontend_files_noted"] == 1
    sent = put(addon)
    assert sent.response is None and sent.request.raw_content == REDACTED
    assert sent.request.headers["content-type"] == "image/png"
    turn = image_turn()
    addon.process_request(turn)
    assert turn.response is None, turn.response and turn.response.get_text()
    assert state.stats()["unchecked_attachment_blocked"] == 0


def test_a_send_that_references_a_file_nobody_checked_is_blocked(addon, state):
    mask_chat(state)
    turn = image_turn()  # no registration, no PUT: the gateway never saw this file
    addon.process_request(turn)
    assert turn.response.status_code == 400
    text = turn.response.get_text()
    assert "attached before masking could check it" in text and "attachment_unchecked" not in text
    assert BLOCK_ATTACHMENT_TEXT.split(".")[0] in text
    stats = state.stats()
    assert stats["unchecked_attachment_blocked"] == 1 and stats["frontend_blocked"] == 1
    assert stats["frontend_masked"] == 0


def test_a_registered_file_whose_bytes_never_passed_is_still_unchecked(addon, state):
    mask_chat(state)
    upload(addon, page())  # registered, but no PUT yet
    turn = image_turn()
    addon.process_request(turn)
    assert turn.response.status_code == 400
    assert state.stats()["unchecked_attachment_blocked"] == 1


def test_a_file_uploaded_before_the_chat_was_masked_is_unchecked_when_it_is_sent_later(
    addon, state
):
    upload(addon, page())  # the chat is not a PBI chat yet: untouched, bytes pass as they are
    sent = put(addon)
    assert sent.request.raw_content == PNG
    mask_chat(state)  # the marker shows up in a later turn
    turn = image_turn()
    addon.process_request(turn)
    assert turn.response.status_code == 400
    assert state.stats()["unchecked_attachment_blocked"] == 1


def test_a_withheld_image_blocks_its_upload_and_the_send_stays_blocked(addon, state, monkeypatch):
    def withheld(self, data):
        from client_gateway.adapters.base import ImageWithheld

        raise ImageWithheld("policy")

    monkeypatch.setattr(MaskContext, "image_bytes", withheld)
    mask_chat(state)
    upload(addon, page())
    sent = put(addon)
    assert sent.response.status_code == 400 and "image_withheld" in sent.response.get_text()
    turn = image_turn()
    addon.process_request(turn)
    assert turn.response.status_code == 400
    assert state.stats()["unchecked_attachment_blocked"] == 1


def test_a_chat_that_is_not_masked_is_never_touched_by_any_of_it(addon, state):
    flows = [registration(page(OTHER_CID)), image_turn(OTHER_CID)]
    before = [sha(f.request.raw_content) for f in flows]
    addon.process_request(flows[0])
    reply(flows[0])
    addon.response(flows[0])
    sent = put(addon)
    addon.process_request(flows[1])
    assert [sha(flows[0].request.raw_content), sha(flows[1].request.raw_content)] == before
    assert sent.request.raw_content == PNG
    assert all(f.response is None or f.response.status_code == 200 for f in (*flows, sent))
    stats = state.stats()
    assert stats["unchecked_attachment_blocked"] == 0 and stats["frontend_blocked"] == 0
    assert stats["frontend_masked"] == 0


def test_a_put_of_an_unknown_or_unattributed_file_is_passed_untouched(addon, state):
    mask_chat(state)  # some chat is masked, but this upload belongs to no known chat
    upload(addon, "https://chatgpt.com/")  # a brand-new chat: no id to attribute it to
    for url in (PUT_URL, f"https://files-test.oaiusercontent.com/files/{OTHER_CID}/raw"):
        sent = put(addon, url)
        assert sent.response is None and sent.request.raw_content == PNG
    assert state.stats()["frontend_files_noted"] == 0


def test_the_residual_a_first_turn_upload_cannot_be_attributed_but_cannot_leak_into_a_masked_send(
    addon, state
):
    upload(addon, "https://chatgpt.com/")  # first turn of a new chat: bytes pass (R2)
    assert put(addon).request.raw_content == PNG
    turn = image_turn(None)  # the human types "masking on" in that first message
    tree = json.loads(turn.request.raw_content)
    tree["messages"][0]["content"]["parts"][-1] = "masking on"
    turn.request.raw_content = json.dumps(tree).encode()
    addon.process_request(turn)
    assert turn.response.status_code == 400  # fail closed: the image was never checked
    assert state.stats()["unchecked_attachment_blocked"] == 1


def test_an_upload_older_than_fifteen_minutes_is_forgotten(addon, state):
    clock = [5000.0]
    state.clock = lambda: clock[0]
    mask_chat(state)
    upload(addon, page())
    clock[0] += FILE_TTL_S + 1
    sent = put(addon)
    assert sent.request.raw_content == PNG  # no longer attributed: R2 passthrough


def test_a_failed_registration_reply_notes_nothing(addon, state):
    mask_chat(state)
    flow = registration(page())
    addon.process_request(flow)
    reply(flow, status=500)
    addon.response(flow)
    assert state.stats()["frontend_files_noted"] == 0


def test_an_unreadable_registration_reply_never_breaks_the_response(addon, state):
    mask_chat(state)
    flow = registration(page())
    addon.process_request(flow)
    respond(flow, 200, {"content-type": "application/json", "content-encoding": "x-weird"}, b"\x00")
    addon.response(flow)
    assert flow.response.status_code == 200 and state.stats()["frontend_files_noted"] == 0


def test_a_human_who_switched_the_chat_off_sends_the_attachment_unchecked(addon, state):
    mask_chat(state)
    state.frontend_set_mode(CHATGPT, CID, "off")
    turn = image_turn()
    addon.process_request(turn)
    assert turn.response is None and state.stats()["unchecked_attachment_blocked"] == 0
