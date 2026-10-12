"""Upstream trust and upstream proxy against the REAL mitmproxy front end (phase 3b.6), loopback sockets only:
a local TLS "AI host" whose certificate is signed by a test CA, a local CONNECT tunnel as the company proxy.
No AI host, no non-loopback address, no system setting. Synthetic data only.

* the REAL server's certificate is verified against ``ssl_verify_upstream_trusted_ca`` (the trust bundle):
  with the origin's root in the bundle the request goes through, without it the upstream handshake fails
  and nothing reaches the origin (verification is never disabled);
* the previous static proxy is chained per connection (``server_conn.via``): the tunnel sees the CONNECT.
"""

from __future__ import annotations

import datetime
import socket
import ssl
import threading
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from client_gateway.frontend.addon import ROUTED_SUFFIXES
from client_gateway.frontend.runner import FrontEnd
from client_gateway.routing.ca import CAStore
from tests._routing_fakes import fake_crypto
from tests.test_frontend_runner import LOOPBACK, TIMEOUT, connect_through, make_state

HOST = "api.individual.githubcopilot.com"
OPTIONS = {"connection_strategy": "lazy", "upstream_cert": False}


def make_origin_pki(tmp_path: Path) -> tuple[bytes, Path]:
    """A test root CA and a leaf for ``HOST`` signed by it: ``(root PEM, PEM with leaf + key)``."""
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Origin Root")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOST)]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(HOST)]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    pem = tmp_path / "origin.pem"
    pem.write_bytes(
        leaf.public_bytes(serialization.Encoding.PEM)
        + key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return ca.public_bytes(serialization.Encoding.PEM), pem


class Origin:
    """A TLS server on loopback that answers every request with ``200 hello`` and records the request
    lines it got."""

    def __init__(self, pem: Path) -> None:
        self.requests: list[str] = []
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(pem)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind((LOOPBACK, 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            try:
                conn.settimeout(TIMEOUT)
                with self.ctx.wrap_socket(conn, server_side=True) as tls:
                    head = b""
                    while b"\r\n\r\n" not in head:
                        chunk = tls.recv(4096)
                        if not chunk:
                            break
                        head += chunk
                    if head:
                        self.requests.append(head.split(b"\r\n", 1)[0].decode("latin-1"))
                        tls.sendall(
                            b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nConnection: close\r\n\r\nhello"
                        )
            except (OSError, ssl.SSLError):
                pass

    def close(self) -> None:
        self.sock.close()


class Tunnel:
    """A company proxy stand-in: accepts ``CONNECT host:port``, answers 200 and pipes bytes."""

    def __init__(self) -> None:
        self.connects: list[str] = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind((LOOPBACK, 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        try:
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                head += chunk
            line = head.split(b"\r\n", 1)[0].decode("latin-1")
            method, target, _ = line.split(" ", 2)
            if method != "CONNECT":
                conn.sendall(b"HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\n\r\n")
                return
            self.connects.append(target)
            host, _, port = target.rpartition(":")
            upstream = socket.create_connection((host, int(port)), timeout=TIMEOUT)
            conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            threading.Thread(target=self._pipe, args=(upstream, conn), daemon=True).start()
            self._pipe(conn, upstream)
        except (OSError, ValueError):
            pass

    @staticmethod
    def _pipe(src: socket.socket, dst: socket.socket) -> None:
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        finally:
            for s in (src, dst):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def close(self) -> None:
        self.sock.close()


def get_through(front: FrontEnd, origin_port: int, client_ctx: ssl.SSLContext) -> bytes:
    """An HTTPS GET to ``HOST`` through the front end (CONNECT to the local origin port)."""
    sock = connect_through(front.port, f"{LOOPBACK}:{origin_port}")
    with client_ctx.wrap_socket(sock, server_hostname=HOST) as tls:
        tls.settimeout(TIMEOUT)
        tls.sendall(f"GET /x HTTP/1.1\r\nHost: {HOST}\r\nConnection: close\r\n\r\n".encode())
        data = b""
        try:
            while True:
                chunk = tls.recv(65536)
                if not chunk:
                    break
                data += chunk
        except (OSError, ssl.SSLError):
            pass
    return data


@pytest.fixture()
def world(tmp_path):
    """Origin PKI, the local CA, the origin and the tunnel."""
    root_pem, origin_pem = make_origin_pki(tmp_path)
    origin_dir = tmp_path / "origin"
    origin_dir.mkdir()
    origin = Origin(origin_pem)
    tunnel = Tunnel()
    store = CAStore(tmp_path / "home", crypto=fake_crypto())
    ca = store.ensure()
    yield {
        "root_pem": root_pem,
        "origin": origin,
        "tunnel": tunnel,
        "store": store,
        "ca": ca,
        "home": tmp_path / "home",
        "tmp": tmp_path,
    }
    origin.close()
    tunnel.close()


def start_front(world, bundle_pem: bytes, via=None) -> FrontEnd:
    bundle = world["tmp"] / "bundle.pem"
    bundle.write_bytes(bundle_pem)
    state = make_state(world["home"])
    front = FrontEnd(
        state,
        0,
        routed_hosts=(*ROUTED_SUFFIXES, LOOPBACK),
        options=OPTIONS,
        cert_store=lambda: world["store"].mitm_store(world["ca"]),
        trust_bundle=bundle,
        upstream_proxy=via,
    )
    front.start()
    return front


def client_context(world) -> ssl.SSLContext:
    return ssl.create_default_context(cadata=world["ca"].pem.decode("ascii"))


def test_the_real_server_certificate_is_verified_against_the_trust_bundle(world):
    front = start_front(world, world["root_pem"])
    try:
        reply = get_through(front, world["origin"].port, client_context(world))
    finally:
        front.stop()
    assert reply.startswith(b"HTTP/1.1 200") and reply.endswith(b"hello")
    assert world["origin"].requests == ["GET /x HTTP/1.1"]


def test_a_server_certificate_the_bundle_does_not_trust_is_refused_and_nothing_reaches_it(world):
    unrelated = build_other_root()
    front = start_front(world, unrelated)
    try:
        reply = get_through(front, world["origin"].port, client_context(world))
    finally:
        front.stop()
    assert b"hello" not in reply and not reply.startswith(b"HTTP/1.1 200")
    assert world["origin"].requests == []  # verification stayed on: no request was ever sent


def build_other_root() -> bytes:
    now = datetime.datetime.now(datetime.timezone.utc)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Some Other Root")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=2))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM)


def test_the_previous_static_proxy_is_chained_per_connection(world):
    tunnel = world["tunnel"]
    seen: list[tuple[str, int, str]] = []

    def via(host: str, port: int, scheme: str):
        seen.append((host, port, scheme))
        return ("http", (LOOPBACK, tunnel.port))

    front = start_front(world, world["root_pem"], via=via)
    try:
        reply = get_through(front, world["origin"].port, client_context(world))
    finally:
        front.stop()
    assert reply.startswith(b"HTTP/1.1 200") and reply.endswith(b"hello")
    assert seen == [(LOOPBACK, world["origin"].port, "https")]  # the chooser saw the real target
    assert tunnel.connects == [
        f"{LOOPBACK}:{world['origin'].port}"
    ]  # ... and the tunnel the CONNECT
    assert world["origin"].requests == ["GET /x HTTP/1.1"]  # the origin was reached THROUGH it


def test_without_an_upstream_proxy_the_connection_does_not_touch_the_tunnel(world):
    front = start_front(world, world["root_pem"], via=lambda h, p, s: None)
    try:
        reply = get_through(front, world["origin"].port, client_context(world))
    finally:
        front.stop()
    assert reply.endswith(b"hello") and world["tunnel"].connects == []


def test_verification_still_applies_through_the_upstream_proxy(world):
    tunnel = world["tunnel"]
    front = start_front(
        world, build_other_root(), via=lambda h, p, s: ("http", (LOOPBACK, tunnel.port))
    )
    try:
        reply = get_through(front, world["origin"].port, client_context(world))
    finally:
        front.stop()
    assert b"hello" not in reply and world["origin"].requests == []
    assert tunnel.connects  # it did go through the proxy; the certificate was refused after that
