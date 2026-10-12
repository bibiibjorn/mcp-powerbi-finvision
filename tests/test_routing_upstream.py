"""Upstream trust and upstream proxy (phase 3b.6), unit level: the trust bundle built from a (fake) Windows
store, the previous-proxy chooser and the addon hook that applies it. The real-proxy checks are in
``test_routing_upstream_live.py``. The default store reader is never called: tests pass fakes."""

from __future__ import annotations

import ssl
from typing import Any

import pytest

from client_gateway.frontend.addon import FrontendAddon
from client_gateway.routing import upstream as up
from client_gateway.routing.ca import CA_COMMON_NAME, build_ca
from client_gateway.routing.pac import DIRECT, Previous, StaticProxy
from client_gateway.routing.upstream import (
    SERVER_AUTH_OID,
    UpstreamProxy,
    build_upstream_bundle,
    windows_store,
)
from tests.test_frontend_addon import flow_of, make_state

SERVER_ONLY = {SERVER_AUTH_OID}
CODE_SIGNING = {"1.3.6.1.5.5.7.3.3"}


def der_of(common_name: str) -> bytes:
    """A self-signed certificate (a stand-in for a root) with this subject."""
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.DER)


def fake_stores(**stores: list[tuple[bytes, str, Any]]):
    asked: list[str] = []

    def enum(name: str):
        asked.append(name)
        return stores.get(name, [])

    enum.asked = asked  # type: ignore[attr-defined]
    return enum


# ---------------------------------------------------------------------------------- the bundle
def test_the_bundle_holds_the_windows_roots_trusted_for_servers(tmp_path):
    public, company, signer = (
        der_of("Public Root"),
        der_of("Company TLS Inspection"),
        der_of("Signer"),
    )
    enum = fake_stores(
        ROOT=[(public, "x509_asn", True), (signer, "x509_asn", CODE_SIGNING)],
        CA=[(company, "x509_asn", SERVER_ONLY), (public, "x509_asn", True)],  # a duplicate
    )
    info = build_upstream_bundle(tmp_path, enum_store=enum, include_certifi=False)
    assert enum.asked == ["ROOT", "CA"]
    text = info.path.read_text(encoding="ascii")
    assert text.count("BEGIN CERTIFICATE") == 2  # public + company; the code-signing root is out
    assert ssl.DER_cert_to_PEM_cert(public) in text and ssl.DER_cert_to_PEM_cert(company) in text
    assert ssl.DER_cert_to_PEM_cert(signer) not in text
    assert (info.windows, info.skipped, info.certifi) == (2, 1, False)
    assert info.path == tmp_path / "routing" / "upstream-ca.pem"
    ssl.create_default_context(cafile=str(info.path))  # a bundle OpenSSL loads


def test_a_company_tls_inspection_root_is_honoured(tmp_path):
    company = der_of("Corp TLS Inspection Root")
    info = build_upstream_bundle(
        tmp_path, enum_store=fake_stores(ROOT=[(company, "x509_asn", True)]), include_certifi=False
    )
    assert ssl.DER_cert_to_PEM_cert(company) in info.path.read_text(encoding="ascii")


def test_our_own_interception_ca_is_never_trusted_upstream(tmp_path):
    ours = build_ca()
    older = build_ca()  # a rotated CA the thumbprint list no longer names
    other = der_of("Other Root")
    enum = fake_stores(
        ROOT=[
            (ours.der, "x509_asn", True),
            (older.der, "x509_asn", True),
            (other, "x509_asn", True),
        ]
    )
    info = build_upstream_bundle(
        tmp_path, enum_store=enum, exclude_thumbprints=[ours.thumbprint], include_certifi=False
    )
    text = info.path.read_text(encoding="ascii")
    assert ssl.DER_cert_to_PEM_cert(ours.der) not in text  # by thumbprint
    assert ssl.DER_cert_to_PEM_cert(older.der) not in text  # by subject name: it is ours too
    assert ssl.DER_cert_to_PEM_cert(other) in text and info.skipped == 2
    assert (
        older.cert.subject.get_attributes_for_oid(
            __import__("cryptography").x509.NameOID.COMMON_NAME
        )[0].value
        == CA_COMMON_NAME
    )


def test_what_is_not_a_certificate_is_skipped_not_fatal(tmp_path):
    good = der_of("Good")
    enum = fake_stores(
        ROOT=[(b"not der", "x509_asn", True), (good, "pkcs_7_asn", True), (good, "x509_asn", True)]
    )
    info = build_upstream_bundle(tmp_path, enum_store=enum, include_certifi=False)
    assert (info.windows, info.skipped) == (1, 2)


def test_the_public_mozilla_list_is_added_as_a_supplement(tmp_path):
    only = der_of("Only Root")
    enum = fake_stores(ROOT=[(only, "x509_asn", True)])
    pem = ssl.DER_cert_to_PEM_cert(der_of("Public Mozilla Root"))
    info = build_upstream_bundle(tmp_path, enum_store=enum, certifi_pem=lambda: pem)
    text = info.path.read_text(encoding="ascii")
    assert text.count("BEGIN CERTIFICATE") == 2 and info.certifi is True
    # the real certifi list is a readable PEM file here
    assert up.certifi_text().count("BEGIN CERTIFICATE") > 50


def test_an_empty_windows_store_still_gives_a_working_bundle(tmp_path):
    info = build_upstream_bundle(tmp_path, enum_store=fake_stores())
    assert info.windows == 0 and info.certifi is True
    ssl.create_default_context(cafile=str(info.path))


def test_the_default_reader_is_a_read_of_the_system_store_and_fails_soft(monkeypatch):
    monkeypatch.setattr(
        ssl, "enum_certificates", lambda name: [(b"x", "x509_asn", True)], raising=False
    )
    assert windows_store("ROOT") == [(b"x", "x509_asn", True)]

    def broken(name: str):
        raise OSError("no store")

    monkeypatch.setattr(ssl, "enum_certificates", broken, raising=False)
    assert windows_store("ROOT") == []
    monkeypatch.delattr(ssl, "enum_certificates", raising=False)
    assert windows_store("ROOT") == []  # a platform without the call


# ---------------------------------------------------------------------------------- the chooser
def static(entries: dict[str, str], bypass: tuple[str, ...] = ()) -> Previous:
    return Previous("static", static=StaticProxy(entries, bypass))


def test_a_previous_static_proxy_is_chained_for_routed_hosts():
    chooser = UpstreamProxy(static({"*": "proxy.corp:8080"}))
    assert chooser.mode == "static_proxy"
    assert chooser.via("chatgpt.com") == ("http", ("proxy.corp", 8080))
    assert chooser.via("api.openai.com", 443, "https") == ("http", ("proxy.corp", 8080))


def test_the_https_entry_wins_and_a_missing_port_is_80():
    chooser = UpstreamProxy(static({"http": "plain:80", "https": "secure:3128"}))
    assert chooser.via("chatgpt.com", 443, "https") == ("http", ("secure", 3128))
    assert chooser.via("chatgpt.com", 80, "http") == ("http", ("plain", 80))
    assert UpstreamProxy(static({"*": "noport.corp"})).via("claude.ai") == (
        "http",
        ("noport.corp", 80),
    )
    assert UpstreamProxy(static({"*": "[::1]:3128"})).via("claude.ai") == ("http", ("::1", 3128))


def test_the_bypass_list_keeps_those_hosts_direct():
    chooser = UpstreamProxy(static({"*": "p:1"}, ("<local>", "*.corp.local", "10.*")))
    assert chooser.via("intranet") is None and chooser.via("git.corp.local") is None
    assert chooser.via("10.1.2.3") is None and chooser.via("GIT.CORP.LOCAL") is None
    assert chooser.via("chatgpt.com") == ("http", ("p", 1))


@pytest.mark.parametrize(
    "previous",
    [
        DIRECT,
        Previous("pac", pac_url="http://x/p.pac", script="function FindProxyForURL(u,h){}"),
        static({"socks": "s:1080"}),  # mitmproxy chains HTTP proxies only
        static({"http": "only-http:80"}),  # no entry for https
        static({"*": "bad entry"}),
        static({"*": "host:99999"}),
    ],
)
def test_everything_that_cannot_be_chained_goes_direct(previous):
    assert UpstreamProxy(previous).via("chatgpt.com", 443, "https") is None


def test_the_status_label_says_how_the_hosts_are_reached():
    assert UpstreamProxy().mode == "direct"
    assert UpstreamProxy(DIRECT).mode == "direct"
    assert UpstreamProxy(static({"*": "p:1"})).mode == "static_proxy"
    assert UpstreamProxy(Previous("pac", script="x")).mode == "direct_pac_not_chained"
    chooser = UpstreamProxy(static({"*": "p:1"}))
    chooser.set_previous(DIRECT)  # settings that changed to direct: no chaining any more
    assert chooser.via("chatgpt.com") is None and chooser.mode == "direct"


# ---------------------------------------------------------------------------------- the addon hook
def routed_flow(url: str):
    return flow_of("POST", url, b"{}", {"content-type": "application/json"})


def test_the_addon_chains_a_routed_host_through_the_chooser(tmp_path):
    chooser = UpstreamProxy(static({"*": "proxy.corp:8080"}))
    addon = FrontendAddon(make_state(tmp_path), upstream_proxy=chooser.via)
    flow = routed_flow("https://chatgpt.com/backend-api/x")
    addon.safe_request(flow)
    assert flow.server_conn.via == ("http", ("proxy.corp", 8080))
    other = routed_flow("https://example.com/x")
    addon.safe_request(other)
    assert other.server_conn.via is None  # a host that is not routed is never chained here


def test_without_a_chooser_or_with_a_failing_one_the_request_still_goes(tmp_path):
    state = make_state(tmp_path)
    plain = FrontendAddon(state)
    flow = routed_flow("https://chatgpt.com/x")
    plain.safe_request(flow)
    assert flow.server_conn.via is None and flow.response is None

    def broken(host: str, port: int, scheme: str):
        raise RuntimeError("boom")

    addon = FrontendAddon(state, upstream_proxy=broken)
    flow = routed_flow("https://chatgpt.com/x")
    addon.safe_request(flow)
    assert flow.server_conn.via is None and flow.response is None
    assert state.stats()["errors"] == 1
