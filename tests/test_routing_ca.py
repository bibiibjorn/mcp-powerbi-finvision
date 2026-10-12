"""The name-constrained local CA (phase 3b.1): constraints, key at rest, chain checks against real path
validation, and its use as mitmproxy's certificate store. Fakes only: no certificate store, no registry,
no DPAPI (a fake with the same call shape), loopback sockets for the one live handshake."""

from __future__ import annotations

import datetime
import ipaddress
import socket
import ssl
import tempfile
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.x509 import verification

from client_gateway.routing import ca as ca_mod
from client_gateway.routing.ca import (
    CA_COMMON_NAME,
    ROUTED_DOMAINS,
    CAStore,
    RoutingError,
    build_ca,
    problems,
    thumbprint_of,
)
from tests._routing_fakes import fake_crypto

LOOPBACK = "127.0.0.1"


@pytest.fixture(scope="module")
def local_ca():
    return build_ca()


@pytest.fixture()
def store(tmp_path):
    return CAStore(tmp_path, crypto=fake_crypto())


# ---------------------------------------------------------------------------------- the certificate
def ext(cert, cls):
    return cert.extensions.get_extension_for_class(cls)


def test_the_constraints_are_present_critical_and_exactly_the_routed_domains(local_ca):
    cert = local_ca.cert
    bc = ext(cert, x509.BasicConstraints)
    assert bc.critical and bc.value.ca and bc.value.path_length == 0
    ku = ext(cert, x509.KeyUsage)
    assert ku.critical and ku.value.key_cert_sign and ku.value.crl_sign
    assert not ku.value.digital_signature and not ku.value.key_encipherment
    nc = ext(cert, x509.NameConstraints)
    assert nc.critical
    assert [n.value for n in nc.value.permitted_subtrees] == list(ROUTED_DOMAINS)
    assert all(isinstance(n, x509.DNSName) for n in nc.value.permitted_subtrees)
    excluded = {str(n.value) for n in nc.value.excluded_subtrees}
    assert excluded == {"0.0.0.0/0", "::/0"}
    assert local_ca.domains == ROUTED_DOMAINS


def test_the_routed_domains_are_those_of_the_front_end():
    from client_gateway.frontend.addon import ROUTED_SUFFIXES

    from client_gateway.routing.ca import INTERCEPTED_DOMAINS

    # the front end intercepts exactly the domains with an adapter; the CA permits those plus
    # claude.ai (its adapter is pending), so adding it later needs no new certificate
    assert ROUTED_SUFFIXES == INTERCEPTED_DOMAINS
    assert set(INTERCEPTED_DOMAINS) <= set(ROUTED_DOMAINS)
    assert set(ROUTED_DOMAINS) - set(INTERCEPTED_DOMAINS) == {"claude.ai"}


def test_subject_validity_and_key_identifiers(local_ca):
    cert = local_ca.cert
    assert cert.subject == cert.issuer
    assert cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value == CA_COMMON_NAME
    lifetime = cert.not_valid_after_utc - cert.not_valid_before_utc
    assert datetime.timedelta(days=729) < lifetime < datetime.timedelta(days=733)
    assert cert.not_valid_before_utc <= datetime.datetime.now(datetime.timezone.utc)
    ski = ext(cert, x509.SubjectKeyIdentifier).value
    aki = ext(cert, x509.AuthorityKeyIdentifier).value
    assert aki.key_identifier == ski.digest
    assert cert.public_key().key_size == 2048
    assert problems(local_ca) == []


def test_a_new_ca_has_a_new_key_and_serial():
    a, b = build_ca(), build_ca()
    assert a.cert.serial_number != b.cert.serial_number and a.thumbprint != b.thumbprint
    assert a.spki_sha256_b64 != b.spki_sha256_b64
    assert a.thumbprint == thumbprint_of(a.der) and len(a.thumbprint) == 40
    with pytest.raises(RoutingError, match="no_domains"):
        build_ca(domains=())


# ---------------------------------------------------------------------------------- real path validation
def leaf_for(local_ca, *names: str, ips: tuple[str, ...] = ()):
    """A leaf certificate made the way mitmproxy makes it (its own ``CertStore`` over our CA)."""
    from mitmproxy import certs

    crl = certs.dummy_crl(local_ca.key, local_ca.cert)
    with tempfile.TemporaryDirectory() as tmp:
        dh_path = Path(tmp) / "dh.pem"
        dh_path.write_bytes(certs.DEFAULT_DHPARAM)
        dh = certs.CertStore.load_dhparam(dh_path)
    mitm = certs.CertStore(local_ca.key, certs.Cert(local_ca.cert), None, crl, dh)
    sans = [x509.DNSName(n) for n in names] + [
        x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips
    ]
    entry = mitm.get_cert(names[0] if names else None, sans)
    return entry.cert.to_cryptography()


def verify(local_ca, leaf, host: str) -> None:
    store = verification.Store([local_ca.cert])
    verifier = (
        verification.PolicyBuilder().store(store).build_server_verifier(verification.DNSName(host))
    )
    verifier.verify(leaf, [])


@pytest.mark.parametrize(
    "host",
    [
        "chatgpt.com",
        "api.openai.com",
        "chat.openai.com",
        "claude.ai",
        "api.individual.githubcopilot.com",
        "files.oaiusercontent.com",
    ],
)
def test_a_leaf_for_a_routed_host_validates_against_the_ca(local_ca, host):
    verify(local_ca, leaf_for(local_ca, host), host)


@pytest.mark.parametrize(
    "host",
    ["example.com", "bank.example", "chatgpt.com.evil.example", "notchatgpt.com", "evilopenai.com"],
)
def test_a_leaf_for_any_other_host_is_refused_by_the_name_constraints(local_ca, host):
    leaf = leaf_for(local_ca, host)
    with pytest.raises(verification.VerificationError):
        verify(local_ca, leaf, host)


def test_an_ip_address_leaf_and_a_mixed_leaf_are_refused(local_ca):
    leaf = leaf_for(local_ca, "chatgpt.com", ips=("203.0.113.9",))
    with pytest.raises(verification.VerificationError):
        verify(local_ca, leaf, "chatgpt.com")  # an IP in the SANs breaks the whole leaf
    mixed = leaf_for(local_ca, "chatgpt.com", "example.com")
    with pytest.raises(verification.VerificationError):
        verify(local_ca, mixed, "chatgpt.com")


def test_a_ca_without_the_constraints_would_validate_everything_so_the_check_is_real():
    """Control: the same leaf for example.com validates under a CA that has no name constraints,
    so the refusals above are the constraints' doing and not an accident of the test."""
    from mitmproxy import certs

    plain_key, plain_cert = certs.create_ca("t", "t", 2048)
    leaf = certs.dummy_cert(plain_key, plain_cert, "example.com", [x509.DNSName("example.com")])
    verifier = (
        verification.PolicyBuilder()
        .store(verification.Store([plain_cert]))
        .build_server_verifier(verification.DNSName("example.com"))
    )
    verifier.verify(leaf.to_cryptography(), [])


# ---------------------------------------------------------------------------------- problems
def test_problems_names_what_makes_a_ca_unfit(local_ca):
    assert problems(local_ca, domains=(*ROUTED_DOMAINS, "extra.example")) == ["domains_changed"]
    future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=800)
    assert "expired" in problems(local_ca, now=future)
    soon = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=715)
    assert problems(local_ca, now=soon) == ["expires_soon"]
    wide = build_ca(domains=("example.com",))
    assert problems(wide) == ["domains_changed"]


def test_problems_catches_a_ca_with_no_constraints_a_wrong_key_and_a_wrong_path_length():
    from mitmproxy import certs

    key, cert = certs.create_ca("t", "t", 2048)  # mitmproxy's own CA: no constraints, no path limit
    bare = ca_mod.LocalCA(key, cert)
    found = problems(bare)
    assert {"constraints_missing", "path_length"} <= set(found)
    other = ca_mod.LocalCA(build_ca().key, build_ca().cert)
    assert "key_mismatch" in problems(other)


# ---------------------------------------------------------------------------------- key at rest
def test_the_key_is_encrypted_at_rest_and_the_public_files_are_public(store, tmp_path):
    created = store.ensure()
    blob = store.key_path.read_bytes()
    assert blob.startswith(b"FAKE-DPAPI:") and b"PRIVATE KEY" not in blob
    assert x509.load_der_x509_certificate(store.cer_path.read_bytes()) == created.cert
    assert x509.load_pem_x509_certificate(store.pem_path.read_bytes()) == created.cert
    assert store.key_path == tmp_path / "routing" / "ca.key.dpapi"
    store.mitm_store(created)  # also writes the public dhparam file
    for path in tmp_path.rglob("*"):  # no file anywhere holds the plain key
        if path.is_file():
            assert b"PRIVATE KEY" not in path.read_bytes(), path
    assert {p.name for p in (tmp_path / "routing").iterdir()} == {
        "ca.key.dpapi",
        "ca.cer",
        "ca.pem",
        "dhparam.pem",
    }


def test_the_ca_is_made_once_and_loaded_again(store):
    first = store.ensure()
    again = CAStore(store.dir.parent, crypto=fake_crypto()).ensure()
    assert again.thumbprint == first.thumbprint
    assert again.key.private_numbers() == first.key.private_numbers()
    assert store.load().thumbprint == first.thumbprint


def test_nothing_is_stored_where_dpapi_is_not_available(tmp_path):
    bare = CAStore(tmp_path, crypto=fake_crypto())
    bare._crypto = None  # a platform without DPAPI
    with pytest.raises(RoutingError, match="dpapi_unavailable"):
        bare.ensure()
    assert not (tmp_path / "routing").exists() or not any((tmp_path / "routing").iterdir())


def test_a_load_without_files_is_none_and_a_broken_key_is_a_fixed_code(store):
    assert store.load() is None
    store.ensure()
    store.key_path.write_bytes(b"garbage")
    with pytest.raises(RoutingError) as err:
        store.load()
    assert str(err.value) == "ca_unreadable"  # a fixed code: no key material, no path


def test_a_certificate_that_does_not_belong_to_the_key_is_refused(store):
    store.ensure()
    store.cer_path.write_bytes(build_ca().der)  # somebody replaced the public file
    with pytest.raises(RoutingError, match="ca_mismatch"):
        store.load()


def test_rotation_makes_a_new_key_and_reports_the_old_thumbprint(store):
    first = store.ensure()
    new, old = store.rotate()
    assert old == first.thumbprint and new.thumbprint != first.thumbprint
    assert store.load().thumbprint == new.thumbprint
    assert x509.load_der_x509_certificate(store.cer_path.read_bytes()) == new.cert
    fresh = CAStore(store.dir.parent / "other", crypto=fake_crypto())
    made, previous = fresh.rotate()
    assert previous is None and fresh.load().thumbprint == made.thumbprint


def test_rotation_names_the_old_certificate_even_when_its_key_cannot_be_read(store):
    first = store.ensure()
    store.key_path.write_bytes(b"garbage")
    new, old = store.rotate()
    assert old == first.thumbprint and store.load().thumbprint == new.thumbprint


# ---------------------------------------------------------------------------------- mitmproxy
def test_mitmproxy_gets_a_store_built_from_the_in_memory_key(store, tmp_path):
    created = store.ensure()
    mitm = store.mitm_store(created)
    assert mitm.default_ca.to_cryptography() == created.cert
    assert mitm.default_privatekey is created.key
    entry = mitm.get_cert("chatgpt.com", [x509.DNSName("chatgpt.com")])
    verify(created, entry.cert.to_cryptography(), "chatgpt.com")
    assert not list(tmp_path.rglob("*mitmproxy*"))  # mitmproxy's own CA files were never made


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("chatgpt.com", True),
        ("CHATGPT.COM.", True),
        ("api.individual.githubcopilot.com", True),
        ("*.openai.com", True),
        ("notchatgpt.com", False),
        ("chatgpt.com.evil.example", False),
        ("example.com", False),
        ("127.0.0.1", False),
        ("", False),
    ],
)
def test_within_domains_reads_a_name_like_a_client_reads_a_constraint(name, expected):
    assert ca_mod.within_domains(name, ROUTED_DOMAINS) is expected


def test_the_store_never_makes_a_leaf_this_ca_s_own_constraints_would_reject(store):
    created = store.ensure()
    mitm = store.mitm_store(created)
    names = [
        x509.DNSName("chatgpt.com"),
        x509.DNSName("example.com"),
        x509.IPAddress(ipaddress.ip_address("203.0.113.9")),
        x509.DNSName("*.openai.com"),
    ]
    leaf = mitm.get_cert("example.com", names).cert.to_cryptography()
    san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert [n.value for n in san] == ["chatgpt.com", "*.openai.com"]
    assert leaf.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value == "chatgpt.com"
    verify(created, leaf, "chatgpt.com")  # and it validates
    for bad in ([x509.DNSName("example.com")], [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]):
        with pytest.raises(ValueError, match="no_permitted_name"):
            mitm.get_cert(None, bad)


def test_the_front_end_serves_certificates_of_this_ca_and_writes_no_plain_key(tmp_path):
    """A real handshake over loopback: the client trusts ONLY our name-constrained CA and verifies the
    host name; the lab CA files (a plain key) are not created."""
    from client_gateway.frontend.runner import FrontEnd
    from tests.test_frontend_runner import connect_through, make_state, tls_server

    state = make_state(tmp_path)
    local = CAStore(tmp_path, crypto=fake_crypto())
    ca = local.ensure()
    front = FrontEnd(
        state,
        0,
        options={"connection_strategy": "lazy", "upstream_cert": False},
        cert_store=lambda: local.mitm_store(ca),
    )
    assert front.ca_spki_sha256_b64() == ca.spki_sha256_b64
    front.start()
    try:
        origin_dir = tmp_path.parent / (tmp_path.name + "_origin")  # the origin's own test key
        origin_dir.mkdir()
        _, origin, _, origin_port = tls_server(origin_dir)
        try:
            context = ssl.create_default_context(cadata=ca.pem.decode("ascii"))
            sock = connect_through(front.port, f"{LOOPBACK}:{origin_port}")
            host = "api.individual.githubcopilot.com"
            with context.wrap_socket(sock, server_hostname=host) as tls:
                cert = tls.getpeercert()
            assert dict(x[0] for x in cert["issuer"])["commonName"] == CA_COMMON_NAME
            assert ("DNS", host) in cert["subjectAltName"]
            assert front.status()["spki_sha256_b64"] == ca.spki_sha256_b64
        finally:
            origin.close()
    finally:
        front.stop()
    assert not (tmp_path / "frontend").exists() or not any((tmp_path / "frontend").iterdir())
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert b"PRIVATE KEY" not in path.read_bytes(), path
    with pytest.raises(OSError):
        socket.create_connection((LOOPBACK, front.port), timeout=2).close()
