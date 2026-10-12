"""``client_gateway.frontend.runner`` (phase 3a): the in-process mitmproxy listener, with REAL sockets
on loopback only (ephemeral ports, temp homes). No AI host, no non-loopback address and no real
gateway is contacted: the "upstream" is a local server, and the one place a routed host name is used
is a TLS handshake whose destination is a local port (lazy connection strategy: nothing is sent
upstream). Synthetic names only.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import socket
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from client_gateway.adapters.openai_chat import OpenAIChatAdapter
from client_gateway.frontend import addon as addon_mod
from client_gateway.frontend.runner import FrontEnd, FrontEndError
from client_gateway.state import GatewayState
from tests._wire_support import (
    MARKER,
    NAME,
    NAME2,
    copilot_slots,
    load_fixture,
    mask_stream,
    stream_text,
    synthetic_clients,
    token_of,
)

COPILOT_SNI = "api.individual.githubcopilot.com"
LOOPBACK = "127.0.0.1"
TIMEOUT = 15.0
LAZY = {"connection_strategy": "lazy", "upstream_cert": False}


def make_state(home: Path) -> GatewayState:
    (home / "install.id").write_text("0123456789abcdef\n", encoding="ascii")
    state = GatewayState(home=home, default_mode="off", images_policy="withhold", ready_wait_s=0.05)
    for prefix, values in synthetic_clients().items():
        state.smap.add_values(prefix, values, "test")
    assert state.marker == MARKER
    return state


# ---------------------------------------------------------------------------------- lifecycle
def test_start_listens_on_loopback_only_and_stop_closes_the_port(tmp_path):
    state = make_state(tmp_path)
    front = FrontEnd(state, 0)
    front.start()
    try:
        assert front.running and front.port > 0
        assert state.status()["frontend"]["port"] == front.port
        with socket.create_connection((LOOPBACK, front.port), timeout=TIMEOUT):
            pass
        assert front.confdir == tmp_path / "frontend" and front.ca_cert_path.is_file()
        listening = _listening_hosts(front.port)
        assert listening <= {LOOPBACK}  # never 0.0.0.0 / a LAN address
    finally:
        front.stop()
    assert not front.running and state.status()["frontend"] == {"enabled": False}
    with pytest.raises(OSError):
        socket.create_connection((LOOPBACK, front.port), timeout=2).close()
    front.stop()  # twice is fine


def _listening_hosts(port: int) -> set[str]:
    import psutil

    return {
        c.laddr.ip
        for c in psutil.net_connections(kind="tcp")
        if c.status == psutil.CONN_LISTEN and c.laddr.port == port
    }


def test_the_spki_is_that_of_the_lab_ca_and_the_ca_stays_in_the_home(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    front = FrontEnd(make_state(tmp_path), 0)
    spki = front.ca_spki_sha256_b64()  # no listener needed to know it
    pem = front.ca_cert_path.read_bytes()
    cert = x509.load_pem_x509_certificate(pem)
    der = cert.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    assert spki == base64.b64encode(hashlib.sha256(der).digest()).decode()
    assert front.ca_spki_sha256_b64() == spki  # stable: the CA is made once
    assert {p.name for p in (tmp_path / "frontend").iterdir()} >= {
        "mitmproxy-ca.pem",
        "mitmproxy-ca-cert.pem",
    }
    again = FrontEnd(GatewayState(home=tmp_path, default_mode="off", images_policy="withhold"), 0)
    assert again.ca_spki_sha256_b64() == spki  # a restart keeps the CA (and the trust of windows)


def test_a_port_that_is_taken_is_a_clear_error(tmp_path):
    held = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    held.bind((LOOPBACK, 0))
    held.listen(1)
    try:
        front = FrontEnd(make_state(tmp_path), held.getsockname()[1])
        with pytest.raises(FrontEndError, match="port_in_use"):
            front.start()
        assert not front.running
    finally:
        held.close()


def test_bad_ports_and_non_loopback_hosts_are_refused(tmp_path):
    state = make_state(tmp_path)
    for port in (-1, 65536, True, "80"):
        with pytest.raises(FrontEndError, match="bad_port"):
            FrontEnd(state, port)  # type: ignore[arg-type]
    for host in ("0.0.0.0", "192.168.1.5", "localhost", ""):
        with pytest.raises(FrontEndError, match="not_loopback"):
            FrontEnd(state, 0, host=host)


def test_the_front_end_status_carries_the_spki_and_counts_only(tmp_path):
    front = FrontEnd(make_state(tmp_path), 0)
    status = front.status()
    assert status["enabled"] is True and status["running"] is False
    assert status["spki_sha256_b64"] == front.ca_spki_sha256_b64()
    assert set(status) == {"enabled", "running", "port", "mode", "routed_hosts", "spki_sha256_b64"}


# ---------------------------------------------------------------------------------- live front end
# ONE live front end at a time: mitmproxy keeps its master and options in module globals. The
# lifecycle tests above start and stop their own before this module-scoped one exists.
@pytest.fixture(scope="module")
def live(tmp_path_factory):
    home = tmp_path_factory.mktemp("frontend_home")
    # the autouse isolation fixture of conftest is function-scoped: set what it sets, here
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("FINVISION_MASKING_HOME", str(home))
        mp.setenv("FINVISION_GATEWAY_NER", "0")  # no name-recognition worker in a test
        state = make_state(home)
    front = FrontEnd(state, 0, options=LAZY)  # allow_hosts: the real routed hosts only
    front.addon.routed = (*addon_mod.ROUTED_SUFFIXES, LOOPBACK)  # + the local "AI host"
    front.start()
    yield front, state
    front.stop()


@pytest.fixture()
def front_state(live):
    front, state = live
    state._frontend_turns.clear()
    state._sessions.clear()
    yield front, state


def tls_server(tmp_path: Path) -> tuple[ssl.SSLContext, Any, bytes, int]:
    """A local TLS server with its own self-signed certificate; returns (client context that
    trusts exactly that certificate, server socket, its DER certificate, port)."""
    import datetime
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "lab-origin")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(LOOPBACK))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    pem = tmp_path / "origin.pem"
    pem.write_bytes(
        cert.public_bytes(serialization.Encoding.PEM)
        + key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(pem)
    client_ctx = ssl.create_default_context(cafile=str(pem))
    client_ctx.check_hostname = False  # the chain is still verified: only the origin's certificate
    raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    raw.bind((LOOPBACK, 0))
    raw.listen(4)
    der = cert.public_bytes(serialization.Encoding.DER)

    def serve() -> None:
        while True:
            try:
                conn, _ = raw.accept()
            except OSError:
                return
            try:
                with server_ctx.wrap_socket(conn, server_side=True) as tls:
                    tls.recv(1)
            except (OSError, ssl.SSLError):
                pass

    threading.Thread(target=serve, daemon=True).start()
    return client_ctx, raw, der, raw.getsockname()[1]


def connect_through(proxy_port: int, target: str) -> socket.socket:
    sock = socket.create_connection((LOOPBACK, proxy_port), timeout=TIMEOUT)
    sock.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = sock.recv(4096)
        assert chunk, "the proxy closed the CONNECT"
        head += chunk
    assert b" 200 " in head.split(b"\r\n", 1)[0], head
    return sock


def test_a_connect_to_a_host_that_is_not_routed_is_tunnelled_not_decrypted(front_state, tmp_path):
    front, _ = front_state
    client_ctx, origin, origin_der, origin_port = tls_server(tmp_path)
    try:
        sock = connect_through(front.port, f"{LOOPBACK}:{origin_port}")
        with client_ctx.wrap_socket(sock, server_hostname="example.test") as tls:
            seen = tls.getpeercert(binary_form=True)
        assert seen == origin_der  # the client saw the ORIGIN's certificate: nothing intercepted
    finally:
        origin.close()


def test_a_connect_with_a_routed_sni_is_intercepted_with_the_lab_ca_only(front_state, tmp_path):
    front, _ = front_state
    client_ctx, origin, _, origin_port = tls_server(tmp_path)
    try:
        sock = connect_through(front.port, f"{LOOPBACK}:{origin_port}")
        lab = ssl.create_default_context(cafile=str(front.ca_cert_path))
        with lab.wrap_socket(sock, server_hostname=COPILOT_SNI) as tls:
            cert = tls.getpeercert()
        issuer = dict(x[0] for x in cert["issuer"])
        assert issuer["organizationName"] == "mitmproxy"
        assert ("DNS", COPILOT_SNI) in cert["subjectAltName"]
        # nothing trusts the lab CA by itself: a normal client refuses the certificate
        sock = connect_through(front.port, f"{LOOPBACK}:{origin_port}")
        with pytest.raises(ssl.SSLCertVerificationError):
            ssl.create_default_context().wrap_socket(sock, server_hostname=COPILOT_SNI)
    finally:
        origin.close()


# ---------------------------------------------------------------------------------- end to end
class Upstream:
    """A local plain-HTTP 'AI host': records the request bodies it gets and answers a streamed
    SSE reply (chunked, a short pause between the chunks, like a real model)."""

    def __init__(self) -> None:
        self.received: list[bytes] = []
        self.reply: list[bytes] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:
                pass

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                outer.received.append(self.rfile.read(length))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                for chunk in outer.reply:
                    self.wfile.write(b"%x\r\n%b\r\n" % (len(chunk), chunk))
                    self.wfile.flush()
                    time.sleep(0.01)
                self.wfile.write(b"0\r\n\r\n")  # the end of the body
                self.close_connection = True

        self.server = ThreadingHTTPServer((LOOPBACK, 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def upstream():
    server = Upstream()
    yield server
    server.close()


@pytest.fixture()
def copilot_route(monkeypatch):
    """The loopback upstream plays the Copilot host: route its chat endpoint to the real adapter."""
    adapter = OpenAIChatAdapter()
    monkeypatch.setattr(
        addon_mod,
        "adapter_for",
        lambda method, host, path, ctype="": adapter if path == "/chat/completions" else None,
    )


def post_through(front: FrontEnd, upstream: Upstream, body: bytes) -> tuple[int, bytes]:
    conn = http.client.HTTPConnection(LOOPBACK, front.port, timeout=TIMEOUT)
    try:
        conn.request(
            "POST",
            f"http://{LOOPBACK}:{upstream.port}/chat/completions",
            body,
            {"Host": f"{LOOPBACK}:{upstream.port}", "Content-Type": "application/json"},
        )
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


def chat_body(text: str, marked: bool) -> bytes:
    messages: list[dict[str, Any]] = [{"role": "user", "content": text}]
    if marked:
        messages.append(
            {"role": "tool", "tool_call_id": "c1", "content": json.dumps({"_fv": MARKER})}
        )
    return json.dumps({"model": "m", "messages": messages, "stream": True}).encode()


def test_a_masked_chat_goes_out_masked_and_the_stream_comes_back_rehydrated(
    front_state, upstream, copilot_route
):
    front, state = front_state
    before = state.stats()["frontend_masked"]
    tokens = {NAME: token_of(state, NAME), NAME2: token_of(state, NAME2)}
    chunks = load_fixture("copilot_tool_call_stream")["response"]["chunks"]
    masked, expected = mask_stream(chunks, copilot_slots, tokens)
    upstream.reply = [c.encode() for c in masked]
    status, body = post_through(front, upstream, chat_body(f"Tell me about {NAME}", marked=True))
    assert status == 200
    sent = upstream.received[0].decode()
    assert NAME not in sent and MARKER not in sent and tokens[NAME] in sent  # masked, marker gone
    text = body.decode()
    assert stream_text([text], copilot_slots) == expected  # rehydrated across the real proxy
    assert not any(t in text for t in tokens.values())
    assert state.stats()["frontend_masked"] == before + 1


def test_the_held_tail_reaches_the_app_when_the_real_proxy_ends_the_body(
    front_state, upstream, copilot_route
):
    front, state = front_state
    token = token_of(state, NAME)

    def frame(text: str) -> bytes:
        payload = {"choices": [{"index": 0, "delta": {"content": text}}]}
        return b"data: " + json.dumps(payload).encode() + b"\n\n"

    # the reply stops right after a complete token: it is held back until the body ends, and
    # mitmproxy ends a streamed body by calling the stream callable with b"" (verified here)
    upstream.reply = [frame("Customer: "), frame(token)]
    status, body = post_through(front, upstream, chat_body("hello", marked=True))
    assert status == 200
    assert NAME.encode() in body and token.encode() not in body


def test_a_chat_that_is_not_masked_is_forwarded_byte_identical_both_ways(
    front_state, upstream, copilot_route
):
    front, state = front_state
    before = state.stats()
    request = chat_body(f"Tell me about {NAME}", marked=False)
    reply = [b'data: {"choices":[{"index":0,"delta":{"content":"hi ' + NAME.encode() + b'"}}]}\n\n']
    upstream.reply = reply
    status, body = post_through(front, upstream, request)
    assert status == 200 and upstream.received == [request]
    assert body == b"".join(reply)
    after = state.stats()
    assert after["frontend_masked"] == before["frontend_masked"]
    assert after["frontend_passthrough"] == before["frontend_passthrough"] + 1


def test_a_request_that_cannot_be_masked_never_reaches_the_upstream(
    front_state, upstream, copilot_route
):
    front, state = front_state
    status, body = post_through(front, upstream, b"not json " + MARKER.encode())
    assert status == 400 and upstream.received == []
    assert b"did not leave this machine" in body and b"not json" not in body
