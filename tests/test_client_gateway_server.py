"""End-to-end tests of the gateway server against a fake upstream (synthetic names only)."""

from __future__ import annotations

import http.client
import json
import socketserver
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

import httpx
import pytest

from client_gateway.log import GatewayLog
from client_gateway.server import Gateway, host_is_local
from client_gateway.state import SESSION_HEADER, TOKEN_HEADER, GatewayState
from core.client_guard.engine import MaskingError
from tests._masking_fakes import pin_fail_mode

NAME = "Zorvex Holding"
NAME2 = "Quillan Trust"
SESSION = "11111111-2222-3333-4444-555555555555"


# ---------------------------------------------------------------------------------- fake upstream
class _FastServer(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self) -> None:  # HTTPServer.server_bind does a slow reverse lookup
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]


class FakeUpstream:
    """Records every request; replies with whatever ``respond`` returns.

    ``respond(req) -> (status, headers, body)`` where ``body`` is bytes or a list of byte chunks
    (written one by one with a flush and a short pause, so the gateway sees real chunk splits).
    """

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.respond: Callable[[dict[str, Any]], tuple[int, dict[str, str], Any]] = lambda req: (
            200,
            {"content-type": "application/json"},
            b"{}",
        )
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                req = {
                    "method": self.command,
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": body,
                }
                outer.requests.append(req)
                status, headers, payload = outer.respond(req)
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                if isinstance(payload, (bytes, bytearray)):
                    self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if isinstance(payload, (bytes, bytearray)):
                    self.wfile.write(payload)
                else:
                    for chunk in payload:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                        time.sleep(0.002)

            do_GET = do_POST = do_HEAD = do_PUT = do_DELETE = _handle

        self.server = _FastServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.02), daemon=True
        )
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def sse(events: list[tuple[str, dict[str, Any]]], split: int = 7) -> list[bytes]:
    """SSE bytes for ``events`` cut into ``split``-byte pieces (cuts fall inside events)."""
    raw = b"".join(
        f"event: {name}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()
        for name, data in events
    )
    return [raw[i : i + split] for i in range(0, len(raw), split)]  # noqa: E203


def text_stream(pieces: list[str], index: int = 0) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = [
        ("message_start", {"type": "message_start", "message": {"id": "m1", "content": []}}),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": index,
                "content_block": {"type": "text", "text": ""},
            },
        ),
    ]
    for piece in pieces:
        events.append(
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "text_delta", "text": piece},
                },
            )
        )
    events.append(("content_block_stop", {"type": "content_block_stop", "index": index}))
    done = {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}
    events.append(("message_delta", done))
    events.append(("message_stop", {"type": "message_stop"}))
    return events


def parse_sse(raw: bytes) -> list[dict[str, Any]]:
    out = []
    for frame in raw.replace(b"\r\n", b"\n").split(b"\n\n"):
        for line in frame.split(b"\n"):
            if line.startswith(b"data: "):
                out.append(json.loads(line[6:]))
    return out


def streamed_text(raw: bytes) -> str:
    return "".join(
        e["delta"]["text"]
        for e in parse_sse(raw)
        if e.get("type") == "content_block_delta" and e["delta"].get("type") == "text_delta"
    )


def streamed_tool_json(raw: bytes) -> str:
    return "".join(
        e["delta"]["partial_json"]
        for e in parse_sse(raw)
        if e.get("type") == "content_block_delta" and e["delta"].get("type") == "input_json_delta"
    )


def message(role: str, text: str) -> dict[str, Any]:
    return {"role": role, "content": [{"type": "text", "text": text}]}


def body_of(messages: list[dict[str, Any]], **extra: Any) -> bytes:
    payload = {"model": "claude-test", "max_tokens": 100, "messages": messages, **extra}
    return json.dumps(payload).encode()


# ---------------------------------------------------------------------------------- fixtures
def _plain_client() -> httpx.Client:
    """Upstream client without TLS setup (the fake upstream speaks plain http; saves ~1 s)."""
    return httpx.Client(http2=False, verify=False, timeout=httpx.Timeout(10.0, read=None))


@pytest.fixture()
def upstream():
    up = FakeUpstream()
    yield up
    up.close()


@pytest.fixture()
def state(tmp_path):
    st = GatewayState(home=tmp_path, default_mode="on", images_policy="withhold")
    st.smap.add_values("CUS", [NAME, NAME2], "test")
    return st


@pytest.fixture()
def gw(state, upstream):
    gateway = Gateway(
        state, port=0, upstream=upstream.url, log=GatewayLog(None), client=_plain_client()
    )
    gateway.start()
    yield gateway
    gateway.shutdown()


@pytest.fixture()
def client(gw):
    with httpx.Client(base_url=gw.url, timeout=30) as c:
        yield c


def post(client: httpx.Client, path: str, body: bytes, **headers: str) -> httpx.Response:
    hdrs = {"content-type": "application/json", SESSION_HEADER: SESSION, **headers}
    return client.post(path, content=body, headers=hdrs)


# ---------------------------------------------------------------------------------- basics
def test_head_api_hello_answered_locally(client, upstream):
    resp = client.head("/api/hello")
    assert resp.status_code == 200
    assert upstream.requests == []


def test_host_is_local():
    assert host_is_local("127.0.0.1:8890")
    assert host_is_local("localhost")
    assert host_is_local("[::1]:8890")
    assert not host_is_local("evil.example:8890")
    assert not host_is_local("127.0.0.1.evil.example")
    assert not host_is_local(None)


def test_prompt_reaches_upstream_masked_and_stream_comes_back_rehydrated(client, upstream, state):
    token = state.smap.mask_text(NAME)[0]
    assert token != NAME
    reply = [f"Summary of {token[:5]}", f"{token[5:]} done; also {token}", "."]
    upstream.respond = lambda req: (
        200,
        {"content-type": "text/event-stream"},
        sse(text_stream(reply), split=5),
    )
    resp = post(client, "/v1/messages", body_of([message("user", f"Please summarise {NAME}")]))
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    # upstream saw the pseudonym only
    sent = upstream.requests[0]["body"].decode()
    assert NAME not in sent and token in sent
    assert upstream.requests[0]["path"] == "/v1/messages"
    # Claude Code sees the real name, also when the token was split across deltas
    assert streamed_text(resp.content) == f"Summary of {NAME} done; also {NAME}."
    assert token.encode() not in resp.content
    assert state.stats()["masked_requests"] == 1


def test_tool_use_input_is_rehydrated(client, upstream, state):
    token = state.smap.mask_text(NAME)[0]
    start = {
        "type": "content_block_start",
        "index": 0,
        "content_block": {"type": "tool_use", "id": "tu1", "name": "lookup", "input": {}},
    }
    parts = ['{"entity": "', token[:6], token[6:], '", "q": "a\\"b"}']
    events: list[tuple[str, dict[str, Any]]] = [("content_block_start", start)]
    for p in parts:
        events.append(
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "input_json_delta", "partial_json": p},
                },
            )
        )
    events.append(("content_block_stop", {"type": "content_block_stop", "index": 0}))
    upstream.respond = lambda req: (200, {"content-type": "text/event-stream"}, sse(events, 9))
    resp = post(client, "/v1/messages", body_of([message("user", "go")]))
    assert json.loads(streamed_tool_json(resp.content)) == {"entity": NAME, "q": 'a"b'}


def test_second_turn_resends_the_masked_history_byte_identical(client, upstream, state):
    token = state.smap.mask_text(NAME)[0]
    answer = f"{token} is fine"
    upstream.respond = lambda req: (
        200,
        {"content-type": "text/event-stream"},
        sse(text_stream([answer]), 11),
    )
    first = [message("user", f"Tell me about {NAME}")]
    resp = post(client, "/v1/messages", body_of(first))
    assistant_text = streamed_text(resp.content)
    assert assistant_text == f"{NAME} is fine"
    # the dictionary grows between the turns: a fresh masking of the assistant text would now
    # pick a longer match and differ; the memo must replay what the model saw
    state.smap.add_values("CUS", [f"{NAME} is"], "test")
    assert state.smap.mask_text(assistant_text)[0] != answer
    second = first + [message("assistant", assistant_text), message("user", "and then?")]
    post(client, "/v1/messages", body_of(second))
    sent1 = json.loads(upstream.requests[0]["body"])
    sent2 = json.loads(upstream.requests[1]["body"])
    assert sent2["messages"][0] == sent1["messages"][0]  # history prefix is identical
    assert sent2["messages"][1]["content"][0]["text"] == answer
    assert state.stats()["memo_hits"] >= 1


def test_thinking_blocks_pass_untouched(client, upstream, state):
    code = state.smap.mask_text(NAME)[0]
    thinking = {"type": "thinking", "thinking": f"hmm {code}", "signature": "A" * 80}
    redacted = {"type": "redacted_thinking", "data": "B" * 80}
    msgs = [
        message("user", "hi"),
        {"role": "assistant", "content": [thinking, redacted, {"type": "text", "text": NAME2}]},
        message("user", "next"),
    ]
    post(client, "/v1/messages", body_of(msgs))
    sent = json.loads(upstream.requests[0]["body"])
    blocks = sent["messages"][1]["content"]
    assert blocks[0] == thinking
    assert blocks[1] == redacted
    assert NAME2 not in blocks[2]["text"]


def test_a_thinking_block_with_a_real_name_never_reaches_upstream(client, upstream):
    thinking = {"type": "thinking", "thinking": f"hmm {NAME}", "signature": "A" * 80}
    msgs = [
        message("user", "hi"),
        {"role": "assistant", "content": [thinking, {"type": "text", "text": NAME2}]},
        message("user", "next"),
    ]
    resp = post(client, "/v1/messages", body_of(msgs))
    assert resp.status_code == 200  # the request is not failed
    raw = upstream.requests[0]["body"]
    assert NAME.encode() not in raw and NAME2.encode() not in raw and b"AAAA" not in raw
    blocks = json.loads(raw)["messages"][1]["content"]
    assert [b["type"] for b in blocks] == ["text"]


def test_masking_failure_is_a_400_per_request_and_nothing_goes_upstream(
    client, upstream, state, monkeypatch
):
    def boom(_s: str, **_kwargs):
        raise MaskingError()

    monkeypatch.setattr(state.smap, "mask_text", boom)
    resp = post(client, "/v1/messages", body_of([message("user", f"about {NAME}")]))
    assert resp.status_code == 400
    assert resp.headers["x-should-retry"] == "false"
    err = resp.json()
    assert err["type"] == "error" and err["error"]["type"] == "invalid_request_error"
    assert err["error"]["message"] == (
        "Client gateway: request not sent because client data could not be masked safely "
        "(mask_error). Your data did not leave this machine."
    )
    assert NAME not in resp.text
    assert upstream.requests == []
    assert state.stats()["blocked"] == 1
    # not a brick: the very next request, once masking works again, goes through
    monkeypatch.undo()
    ok = post(client, "/v1/messages", body_of([message("user", f"about {NAME}")]))
    assert ok.status_code == 200
    assert len(upstream.requests) == 1


def test_bad_json_and_binary_bodies_are_blocked(client, upstream):
    bad = post(client, "/v1/messages", b'{"messages": [')
    assert bad.status_code == 400 and "bad_json" in bad.json()["error"]["message"]
    binary = client.post(
        "/v1/files", content=b"\x00\x01", headers={"content-type": "application/octet-stream"}
    )
    assert binary.status_code == 400 and "unsupported_body" in binary.json()["error"]["message"]
    assert upstream.requests == []


def test_dictionary_not_ready_fails_the_request_not_the_session(
    client, upstream, state, monkeypatch
):
    pin_fail_mode(monkeypatch, "closed")  # the strict behaviour
    state.ready_wait_s = 0.05
    state.set_ready(False)
    resp = post(client, "/v1/messages", body_of([message("user", "hi")]))
    assert resp.status_code == 400
    assert "dictionary_not_ready" in resp.json()["error"]["message"]
    assert upstream.requests == []
    state.set_ready(True)
    assert post(client, "/v1/messages", body_of([message("user", "hi")])).status_code == 200


def test_not_ready_error_says_why_and_what_to_do(client, upstream, state, monkeypatch):
    pin_fail_mode(monkeypatch, "closed")  # the strict behaviour
    state.ready_wait_s = 0.05
    state.set_ready(False, "dictionary_empty", {"total": 0})
    resp = post(client, "/v1/messages", body_of([message("user", "hi")]))
    text = resp.json()["error"]["message"]
    assert resp.status_code == 400 and resp.headers["x-should-retry"] == "false"
    assert "dictionary_empty" in text and "Power BI Desktop" in text and "masking off" in text
    detail = {"total": 3, "not_ready": 1, "classes": ["column_unreadable"]}
    state.set_ready(False, "dictionary_incomplete", detail)
    state.session(SESSION).ready_blocks = 0  # a chat is refused once, then goes on: a fresh one
    resp = post(client, "/v1/messages", body_of([message("user", "hi")]))
    text = resp.json()["error"]["message"]
    assert "1 of 3 open model(s)" in text and "column_unreadable" in text
    assert upstream.requests == []
    assert state.status()["not_ready"]["reason"] == "dictionary_incomplete"


def test_every_masked_request_asks_the_provider_to_verify_first(
    client, upstream, state, monkeypatch
):
    pin_fail_mode(monkeypatch, "closed")  # the strict behaviour
    calls: list[int] = []

    def verify() -> None:
        calls.append(1)
        state.set_ready(False, "dictionary_incomplete", {"classes": ["new_instance"]})
        state.ready_wait_s = 0.05

    state.verify_hook = verify
    resp = post(client, "/v1/messages", body_of([message("user", f"about {NAME}")]))
    assert calls == [1] and resp.status_code == 400  # a model that opened a second ago: held
    assert upstream.requests == []
    state.set_mode(SESSION, "off", state.token)
    post(client, "/v1/messages", body_of([message("user", "hi")]))
    assert calls == [1]  # passthrough sessions are not verified


def test_mode_off_is_byte_for_byte_passthrough(client, upstream, state):
    state.set_mode(SESSION, "off", state.token)
    raw = b'{ "model":"x",  "messages":[{"role":"user","content":"' + NAME.encode() + b'"}] }'
    token = state.smap.mask_text(NAME)[0]
    stream = sse(text_stream([f"say {token}"]), 13)
    upstream.respond = lambda req: (200, {"content-type": "text/event-stream"}, stream)
    resp = post(client, "/v1/messages", raw)
    assert upstream.requests[0]["body"] == raw  # identical bytes, real name included
    assert resp.content == b"".join(stream)  # tokens stay tokens
    assert state.stats()["passthrough_requests"] == 1
    # another session is still masked
    post(client, "/v1/messages", raw, **{SESSION_HEADER: "other-session"})
    assert NAME.encode() not in upstream.requests[1]["body"]


def test_count_tokens_is_masked(client, upstream):
    upstream.respond = lambda req: (
        200,
        {"content-type": "application/json"},
        b'{"input_tokens": 12}',
    )
    resp = post(client, "/v1/messages/count_tokens", body_of([message("user", NAME)]))
    assert resp.json() == {"input_tokens": 12}
    assert upstream.requests[0]["path"] == "/v1/messages/count_tokens"
    assert NAME.encode() not in upstream.requests[0]["body"]


def test_request_headers_are_forwarded_verbatim(client, upstream):
    post(
        client,
        "/v1/messages",
        body_of([message("user", "hi")]),
        **{
            "anthropic-beta": "claude-code-20250219,oauth-2025-04-20",
            "anthropic-version": "2023-06-01",
            "authorization": "Bearer sk-test-token",
            "x-api-key": "key-123",
            "x-app": "cli",
            "user-agent": "claude-cli/9.9.9",
            "accept-encoding": "gzip, br",
        },
    )
    h = upstream.requests[0]["headers"]
    assert h["anthropic-beta"] == "claude-code-20250219,oauth-2025-04-20"
    assert h["anthropic-version"] == "2023-06-01"
    assert h["authorization"] == "Bearer sk-test-token"
    assert h["x-api-key"] == "key-123"
    assert h["x-app"] == "cli"
    assert h["user-agent"] == "claude-cli/9.9.9"
    assert h[SESSION_HEADER] == SESSION
    assert h["accept-encoding"] == "identity"
    assert h["host"] == f"127.0.0.1:{upstream.port}"  # the upstream's own host, not the gateway's
    assert int(h["content-length"]) == len(upstream.requests[0]["body"])


def test_upstream_error_status_body_and_headers_are_unchanged(client, upstream):
    err = b'{"type":"error","error":{"type":"rate_limit_error","message":"slow down"}}'
    upstream.respond = lambda req: (
        429,
        {
            "content-type": "application/json",
            "retry-after": "17",
            "x-should-retry": "true",
            "request-id": "req_abc",
            "anthropic-ratelimit-unified-5h-utilization": "0.93",
        },
        err,
    )
    resp = post(client, "/v1/messages", body_of([message("user", "hi")]))
    assert resp.status_code == 429
    assert resp.headers["retry-after"] == "17"
    assert resp.headers["x-should-retry"] == "true"
    assert resp.headers["request-id"] == "req_abc"
    assert resp.headers["anthropic-ratelimit-unified-5h-utilization"] == "0.93"
    assert resp.json() == json.loads(err)


def test_json_response_is_rehydrated(client, upstream, state):
    token = state.smap.mask_text(NAME)[0]
    reply = {"type": "message", "content": [{"type": "text", "text": f"about {token}"}]}
    upstream.respond = lambda req: (
        200,
        {"content-type": "application/json"},
        json.dumps(reply).encode(),
    )
    resp = post(client, "/v1/messages", body_of([message("user", "go")]))
    assert resp.json()["content"][0]["text"] == f"about {NAME}"
    assert len(state.session(SESSION).memo) == 1


def test_upstream_down_is_a_502_anthropic_error(state, tmp_path):
    dead = Gateway(
        state, port=0, upstream="http://127.0.0.1:1", log=GatewayLog(None), client=_plain_client()
    )
    dead.start()
    try:
        with httpx.Client(base_url=dead.url, timeout=30) as c:
            resp = post(c, "/v1/messages", body_of([message("user", "hi")]))
        assert resp.status_code == 502
        assert resp.json()["type"] == "error"
        assert resp.json()["error"]["type"] == "api_error"
    finally:
        dead.shutdown()


def test_chunked_request_body_is_read(gw, upstream):
    conn = http.client.HTTPConnection("127.0.0.1", gw.port, timeout=30)
    body = body_of([message("user", f"about {NAME}")])
    conn.request(
        "POST",
        "/v1/messages",
        body=iter([body[:20], body[20:]]),
        headers={"Content-Type": "application/json", "Transfer-Encoding": "chunked"},
        encode_chunked=True,
    )
    resp = conn.getresponse()
    assert resp.status == 200
    resp.read()
    conn.close()
    assert NAME.encode() not in upstream.requests[0]["body"]


def test_keep_alive_connection_serves_several_requests(gw, upstream):
    conn = http.client.HTTPConnection("127.0.0.1", gw.port, timeout=30)
    for _ in range(3):
        conn.request(
            "POST",
            "/v1/messages",
            body=body_of([message("user", "hi")]),
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        assert resp.status == 200
        resp.read()
    conn.close()
    assert len(upstream.requests) == 3


def test_ten_parallel_streams(client, upstream, state):
    tokens = {i: state.smap.mask_text(NAME if i % 2 else NAME2)[0] for i in range(10)}

    def respond(req: dict[str, Any]) -> tuple[int, dict[str, str], Any]:
        prompt = json.loads(req["body"])["messages"][0]["content"][0]["text"]
        n = int(prompt.split("#")[1].split()[0])
        pieces = [f"answer #{n} for ", tokens[n][:4], tokens[n][4:], " end"]
        return 200, {"content-type": "text/event-stream"}, sse(text_stream(pieces), 6 + n)

    upstream.respond = respond

    def one(n: int) -> str:
        who = NAME if n % 2 else NAME2
        with httpx.Client(base_url=str(client.base_url), timeout=60) as c:
            resp = post(
                c,
                "/v1/messages",
                body_of([message("user", f"ask #{n} about {who}")]),
                **{SESSION_HEADER: f"sess-{n}"},
            )
        return streamed_text(resp.content)

    with ThreadPoolExecutor(10) as pool:
        results = list(pool.map(one, range(10)))
    for n, text in enumerate(results):
        assert text == f"answer #{n} for {NAME if n % 2 else NAME2} end"
    assert all(
        NAME.encode() not in r["body"] and NAME2.encode() not in r["body"]
        for r in upstream.requests
    )
    assert state.stats()["requests"] == 10


def test_other_paths_are_forwarded_and_get_requests_have_no_body(client, upstream):
    upstream.respond = lambda req: (200, {"content-type": "application/json"}, b'{"data":[]}')
    resp = client.get("/v1/models", headers={SESSION_HEADER: SESSION})
    assert resp.status_code == 200 and resp.json() == {"data": []}
    assert upstream.requests[0]["method"] == "GET"
    assert upstream.requests[0]["body"] == b""


# ---------------------------------------------------------------------------------- control API
def test_health_and_status_hold_counts_only(client, state):
    health = client.get("/__gateway/health").json()
    assert health["ok"] is True and health["version"]
    assert client.get("/__gateway/status").status_code == 403  # counts need the control token
    status = client.get("/__gateway/status", headers={TOKEN_HEADER: state.token}).json()
    assert status["dictionary"]["by_prefix"]["CUS"] >= 2
    assert status["mode"] == "on"
    text = json.dumps(status)
    assert NAME not in text and NAME2 not in text
    assert status["provider"] == {"attached": False}


def test_control_mode_switching_and_token(client, state):
    hdr = {"content-type": "application/json"}
    tok = {TOKEN_HEADER: state.token}
    url = "/__gateway/mode"
    off = client.post(url, json={"session": SESSION, "mode": "off"})
    assert off.status_code == 403
    assert client.get(f"/__gateway/session?id={SESSION}").status_code == 403
    assert client.get(f"/__gateway/session?id={SESSION}", headers=tok).json()["mode"] == "on"
    bad = client.post(url, json={"session": SESSION, "mode": "off"}, headers={TOKEN_HEADER: "x"})
    assert bad.status_code == 403
    ok = client.post(
        url, json={"session": SESSION, "mode": "off"}, headers={TOKEN_HEADER: state.token}
    )
    assert ok.status_code == 200 and ok.json()["mode"] == "off"
    assert client.get(f"/__gateway/session?id={SESSION}", headers=tok).json()["mode"] == "off"
    on = client.post(url, json={"session": SESSION, "mode": "on"})  # no token needed
    assert on.status_code == 200
    assert client.get(f"/__gateway/session?id={SESSION}", headers=tok).json()["mode"] == "on"
    assert client.post(url, json={"session": SESSION, "mode": "maybe"}).status_code == 400
    glob = client.post(url, json={"session": "", "mode": "off"})
    assert glob.status_code == 403
    glob = client.post(
        url, json={"session": "", "mode": "off"}, headers={TOKEN_HEADER: state.token}
    )
    assert glob.status_code == 200 and state.global_mode == "off"
    assert hdr  # silence unused


def test_control_rejects_foreign_host_and_non_json_posts(gw):
    conn = http.client.HTTPConnection("127.0.0.1", gw.port, timeout=10)
    conn.request("GET", "/__gateway/status", headers={"Host": "evil.example"})
    assert conn.getresponse().status == 403
    conn.close()
    conn = http.client.HTTPConnection("127.0.0.1", gw.port, timeout=10)
    conn.request(
        "POST",
        "/__gateway/mode",
        body=b"session=x&mode=on",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert conn.getresponse().status == 415
    conn.close()


def test_control_refresh_without_provider_and_unknown_route(client, state):
    assert client.post("/__gateway/refresh", json={}).status_code == 403  # a heavy re-detection
    resp = client.post("/__gateway/refresh", json={}, headers={TOKEN_HEADER: state.token})
    assert resp.status_code == 200 and resp.json()["provider"] is False
    assert client.get("/__gateway/nothing").status_code == 404


def test_shutdown_needs_the_token(gw, client, state):
    assert client.post("/__gateway/shutdown", json={}).status_code == 403
    assert client.get("/__gateway/health").status_code == 200
    resp = client.post("/__gateway/shutdown", json={}, headers={TOKEN_HEADER: state.token})
    assert resp.status_code == 200
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            httpx.get(f"{gw.url}/__gateway/health", timeout=0.5)
        except httpx.HTTPError:
            break
        time.sleep(0.1)
    else:
        pytest.fail("gateway still answering after shutdown")


# ---------------------------------------------------------------------------------- latency
def test_added_latency_for_a_200kb_request_with_a_5k_dictionary(tmp_path, capsys):
    import random

    random.seed(7)
    st = GatewayState(home=tmp_path, default_mode="on", images_policy="withhold")
    syl = ["ka", "lo", "mi", "ra", "tu", "ve", "so", "ni", "bax", "zer", "qui", "dro"]
    names: set[str] = set()
    while len(names) < 5000:
        word = "".join(random.choice(syl) for _ in range(random.randint(3, 5))).capitalize()
        names.add(f"{word} {random.choice(['Holding', 'Invest', 'Capital', 'Partners'])}")
    st.smap.add_values("CUS", sorted(names), "bench")
    pool = ["report", "measure", "table", "revenue", "balance", "query", "visual", "page"]

    def para(n: int) -> str:
        return " ".join(
            random.choice(pool) if i % 40 else random.choice(sorted(names)[:50]) for i in range(n)
        )

    from client_gateway.masking import mask_request
    from client_gateway.memo import RemaskMemo

    memo = RemaskMemo()
    tools = [
        {
            "name": f"t{i}",
            "description": para(600),
            "input_schema": {"type": "object", "properties": {}},
        }
        for i in range(24)
    ]
    system = [{"type": "text", "text": para(6000)}]
    history = [message("user", para(2500)), message("assistant", para(800))]

    def build(tail: str) -> bytes:
        msgs = history + [
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "x", "content": tail}],
            }
        ]
        return body_of(msgs, system=system, tools=tools)

    first = build(para(5000))
    assert len(first) > 195_000
    t0 = time.perf_counter()
    mask_request(first, "application/json", st, memo)
    cold = (time.perf_counter() - t0) * 1000
    timings = []
    for _ in range(5):  # a typical later turn: same prefix (cached), a new tool result
        raw = build(para(5000))
        t0 = time.perf_counter()
        mask_request(raw, "application/json", st, memo)
        timings.append((time.perf_counter() - t0) * 1000)
    typical = sorted(timings)[len(timings) // 2]
    with capsys.disabled():
        print(
            f"\n[gateway latency] {len(first)} bytes, 5000 values: "
            f"first request {cold:.1f} ms, later turns median {typical:.1f} ms"
        )
    assert typical < 100  # generous CI bound; the target on this machine is < 20 ms
    assert cold < 500


# ---------------------------------------------------------------------------------- safety net
def _default_off(state):
    state.global_mode = "off"  # masking off only because nobody switched it on
    state.pbi_chats_only = False  # these tests cover the legacy guard (the PBI-chat rule has none)


def test_client_names_in_an_unswitched_chat_are_stopped_with_a_hint(client, upstream, state):
    _default_off(state)
    resp = post(client, "/v1/messages", body_of([message("user", f"Check {NAME} please")]))
    assert resp.status_code == 400 and upstream.requests == []
    assert "masking on" in resp.text and "masking off" in resp.text and "CUS" in resp.text
    assert NAME not in resp.text  # the hint never repeats a value
    assert state.stats()["unmasked_guard"] == 1


def test_plain_text_in_an_unswitched_chat_passes_untouched(client, upstream, state):
    _default_off(state)
    raw = body_of([message("user", "hi, how does SUMMARIZECOLUMNS work?")])
    upstream.respond = lambda req: (200, {"content-type": "application/json"}, b"{}")
    assert post(client, "/v1/messages", raw).status_code == 200
    assert upstream.requests[0]["body"] == raw


def test_an_explicit_masking_off_lets_client_names_through(client, upstream, state):
    _default_off(state)
    state.set_mode(SESSION, "off", state.token)  # the user typed "masking off"
    raw = body_of([message("user", f"Check {NAME} please")])
    upstream.respond = lambda req: (200, {"content-type": "application/json"}, b"{}")
    assert post(client, "/v1/messages", raw).status_code == 200
    assert upstream.requests[0]["body"] == raw


def test_only_the_newest_message_is_checked(client, upstream, state):
    _default_off(state)
    static = (
        "<system-reminder>As you answer the user's questions, you can use the following "
        "context: " + NAME + "</system-reminder> thanks"
    )
    raw = body_of(
        [message("user", f"earlier: {NAME}"), message("assistant", "ok"), message("user", static)]
    )
    upstream.respond = lambda req: (200, {"content-type": "application/json"}, b"{}")
    assert post(client, "/v1/messages", raw).status_code == 200


def test_an_attached_file_in_a_reminder_is_checked_like_the_users_text(client, upstream, state):
    _default_off(state)  # G-01: an @file / IDE selection arrives inside a system-reminder block
    attached = (
        "<system-reminder>Result of calling the Read tool:\n"
        + NAME
        + " owes us money</system-reminder> see above"
    )
    upstream.respond = lambda req: (200, {"content-type": "application/json"}, b"{}")
    assert post(client, "/v1/messages", body_of([message("user", attached)])).status_code == 400
    assert not upstream.requests


def test_the_safety_net_can_be_switched_off(client, upstream, state):
    _default_off(state)
    state.guard_unmasked = False
    upstream.respond = lambda req: (200, {"content-type": "application/json"}, b"{}")
    assert post(client, "/v1/messages", body_of([message("user", NAME)])).status_code == 200


class _FakeNer:
    def __init__(self, names):
        self.names = names
        self.calls = 0

    def ready(self):
        return True

    def find_many(self, texts, budget_s=None, wait=True):
        self.calls += 1
        return [(n, "PER") for n in self.names if any(n in t for t in texts)]


class _FakeDetectors:
    def __init__(self, ner):
        self.ner = ner

    def masking_on(self):
        pass


def test_the_safety_net_stops_an_unknown_name_found_by_ner(client, upstream, state):
    _default_off(state)
    state.detectors = _FakeDetectors(_FakeNer(["Lotte Vanhoutte"]))
    resp = post(
        client,
        "/v1/messages",
        body_of(
            [
                message(
                    "user", "Mail van Lotte Vanhoutte: de factuur klopt niet, kan je dit bekijken?"
                )
            ]
        ),
    )
    assert resp.status_code == 400 and upstream.requests == [] and "PER" in resp.text


def test_the_safety_net_never_waits_for_a_model_that_is_not_ready(client, upstream, state):
    _default_off(state)
    ner = _FakeNer(["Lotte Vanhoutte"])
    ner.ready = lambda: False
    state.detectors = _FakeDetectors(ner)
    upstream.respond = lambda req: (200, {"content-type": "application/json"}, b"{}")
    resp = post(client, "/v1/messages", body_of([message("user", "Mail van Lotte Vanhoutte")]))
    assert resp.status_code == 200 and ner.calls == 0


def test_the_safety_net_stops_a_screenshot_with_client_data(client, upstream, state, monkeypatch):
    import base64
    import io

    from PIL import Image

    from client_gateway import images as G

    _default_off(state)
    monkeypatch.setattr(
        G,
        "_run_ocr",
        lambda n, i: [G.Word("Zorvex", 20, 30, 80, 44), G.Word("Holding", 85, 30, 150, 44)],
    )
    monkeypatch.setattr(G, "_run_tile", lambda n, i, b, k: [])
    buf = io.BytesIO()
    Image.new("RGB", (300, 100), "white").save(buf, "PNG")
    block = {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(buf.getvalue()).decode(),
        },
    }
    body = json.dumps(
        {
            "model": "x",
            "messages": [
                {"role": "user", "content": [block, {"type": "text", "text": "what is this?"}]}
            ],
        }
    ).encode()
    resp = post(client, "/v1/messages", body)
    assert resp.status_code == 400 and "IMG" in resp.text and upstream.requests == []
    # a screenshot without client data passes
    monkeypatch.setattr(G, "_run_ocr", lambda n, i: [G.Word("Revenue", 20, 30, 80, 44)])
    buf2 = io.BytesIO()
    Image.new("RGB", (301, 100), "white").save(buf2, "PNG")
    block["source"]["data"] = base64.b64encode(buf2.getvalue()).decode()
    body = json.dumps({"model": "x", "messages": [{"role": "user", "content": [block]}]}).encode()
    upstream.respond = lambda req: (200, {"content-type": "application/json"}, b"{}")
    assert post(client, "/v1/messages", body).status_code == 200
