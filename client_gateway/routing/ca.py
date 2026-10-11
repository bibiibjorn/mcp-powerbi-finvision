"""The name-constrained local CA of the TLS front end (spec §8.2).

The front end re-signs the certificates of the routed AI hosts (and only those) so the apps talk to the
gateway. The CA that does it is made to be as harmless as a root certificate can be:

* ``BasicConstraints(ca, path_length=0)`` (critical): it signs leaf certificates, never another CA;
* ``KeyUsage(keyCertSign, cRLSign)`` (critical);
* ``NameConstraints`` (critical): permitted DNS subtrees = the routed domains and nothing else, and
  every IP address excluded. A client that enforces name constraints (Windows, Chromium, OpenSSL) refuses
  a certificate this CA signs for ``example.com`` or for an IP, so a stolen CA key cannot be used to
  impersonate a bank;
* a subject that says what it is (``Finvision masking gateway (local only)``), SKI / AKI, 2 years.

Key at rest: the RSA key (2048 bit: mitmproxy's ``CertStore`` and its leaf certificates are RSA) is
encrypted with DPAPI for the current Windows user (``<home>/routing/ca.key.dpapi``, same pattern as
``client_gateway.store``). The plain key never touches the disk: ``mitm_store`` hands mitmproxy a
``CertStore`` built from the in-memory key and certificate. The certificate itself is public:
``ca.cer`` (DER, what the setup installs) and ``ca.pem``.

``rotate`` makes a new CA (a new key); the old certificate has to be removed from the Windows store by
the setup (``rotate`` returns the old thumbprint for that). ``problems`` reports what is wrong with a CA
that was loaded (expired, a routed-domain list that no longer matches, a certificate and key that do
not belong together): the setup or the gateway then rotates.

Failures raise ``RoutingError(code)`` with a fixed code; no message holds key material.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import ipaddress
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

ROUTED_DOMAINS = (
    "claude.ai",
    "chatgpt.com",
    "openai.com",
    "oaiusercontent.com",
    "githubcopilot.com",
)
# the domains actually routed and intercepted today: every one that has an adapter. ``claude.ai`` is
# permitted by the CA (no rotation when its adapter lands) but not routed yet (frontend/addon.py)
INTERCEPTED_DOMAINS = tuple(d for d in ROUTED_DOMAINS if d != "claude.ai")
CA_COMMON_NAME = "Finvision masking gateway (local only)"
CA_ORGANIZATION = "Finvision"
KEY_BITS = 2048
VALID_DAYS = 730  # 2 years
CLOCK_SKEW = datetime.timedelta(days=1)
EXPIRES_SOON = datetime.timedelta(days=30)
ROUTING_DIR = "routing"
KEY_FILE = "ca.key.dpapi"
CER_FILE = "ca.cer"
PEM_FILE = "ca.pem"
DH_FILE = "dhparam.pem"
_DESCRIPTION = "Finvision masking gateway CA key"

Crypto = tuple[Callable[..., Any], Callable[..., Any]]  # (CryptProtectData, CryptUnprotectData)


class RoutingError(Exception):
    """A routing step failed. The message is a fixed code (``dpapi_unavailable``, ``ca_unreadable``,
    ``ca_mismatch``, ``mitmproxy_unavailable``)."""


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def within_domains(name: str, domains: tuple[str, ...]) -> bool:
    """Is a DNS name (``a.b.chatgpt.com``, ``*.chatgpt.com``) one of the permitted domains or below
    one? The same reading a client gives a DNS name constraint (``example.com`` permits the name itself
    and every name under it, never ``notexample.com``)."""
    n = name.strip().lower().rstrip(".")
    if n.startswith("*."):
        n = n[2:]
    return any(n == d or n.endswith("." + d) for d in (x.lower() for x in domains))


def default_crypto() -> Crypto | None:
    """DPAPI for the current user (``win32crypt``), or None where it is not available."""
    from client_gateway.store import _dpapi  # noqa: PLC0415 - the one DPAPI loader of the gateway

    found = _dpapi()
    return (found[0], found[1]) if found is not None else None  # type: ignore[return-value]


def thumbprint_of(der: bytes) -> str:
    """SHA-1 of a DER certificate, upper-case hex: what the Windows store calls a thumbprint."""
    return hashlib.sha1(der, usedforsecurity=False).hexdigest().upper()  # noqa: S324


@dataclass(frozen=True)
class LocalCA:
    """The CA key and certificate, in memory."""

    key: rsa.RSAPrivateKey
    cert: x509.Certificate

    @property
    def der(self) -> bytes:
        return self.cert.public_bytes(serialization.Encoding.DER)

    @property
    def pem(self) -> bytes:
        return self.cert.public_bytes(serialization.Encoding.PEM)

    @property
    def thumbprint(self) -> str:
        """SHA-1 of the DER certificate, upper-case hex: what the Windows store calls a thumbprint."""
        return thumbprint_of(self.der)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.der).hexdigest().upper()

    @property
    def spki_sha256_b64(self) -> str:
        der = self.cert.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        return base64.b64encode(hashlib.sha256(der).digest()).decode("ascii")

    @property
    def not_after(self) -> datetime.datetime:
        return self.cert.not_valid_after_utc

    @property
    def domains(self) -> tuple[str, ...]:
        """The permitted DNS subtrees of the certificate (empty when it has no name constraints)."""
        try:
            nc = self.cert.extensions.get_extension_for_class(x509.NameConstraints).value
        except x509.ExtensionNotFound:
            return ()
        return tuple(n.value for n in (nc.permitted_subtrees or []) if isinstance(n, x509.DNSName))


# ------------------------------------------------------------------------------------ building
def build_ca(
    domains: tuple[str, ...] = ROUTED_DOMAINS,
    now: datetime.datetime | None = None,
    days: int = VALID_DAYS,
) -> LocalCA:
    """A new name-constrained CA (a new RSA key)."""
    if not domains:
        raise RoutingError("no_domains")
    moment = now or _now()
    key = rsa.generate_private_key(public_exponent=65537, key_size=KEY_BITS)
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, CA_COMMON_NAME),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, CA_ORGANIZATION),
        ]
    )
    public = key.public_key()
    ski = x509.SubjectKeyIdentifier.from_public_key(public)
    constraints = x509.NameConstraints(
        permitted_subtrees=[x509.DNSName(d) for d in domains],
        excluded_subtrees=[
            x509.IPAddress(ipaddress.ip_network("0.0.0.0/0")),
            x509.IPAddress(ipaddress.ip_network("::/0")),
        ],
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(public)
        .serial_number(x509.random_serial_number())
        .not_valid_before(moment - CLOCK_SKEW)
        .not_valid_after(moment + datetime.timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(constraints, critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(ski, critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ski), critical=False
        )
        .sign(private_key=key, algorithm=hashes.SHA256())
    )
    return LocalCA(key, cert)


def problems(
    ca: LocalCA,
    domains: tuple[str, ...] = ROUTED_DOMAINS,
    now: datetime.datetime | None = None,
) -> list[str]:
    """Fixed codes for what is wrong with a CA (empty = fine): ``not_a_ca``, ``path_length``,
    ``constraints_missing``, ``constraints_not_critical``, ``domains_changed``, ``ips_not_excluded``,
    ``key_usage``, ``key_mismatch``, ``expired``, ``expires_soon``. A CA with any of the first nine
    must be rotated; ``expires_soon`` is a warning."""
    out: list[str] = []
    cert = ca.cert
    exts = cert.extensions
    try:
        bc = exts.get_extension_for_class(x509.BasicConstraints)
        if not bc.value.ca or not bc.critical:
            out.append("not_a_ca")
        if bc.value.path_length != 0:
            out.append("path_length")
    except x509.ExtensionNotFound:
        out.append("not_a_ca")
    try:
        ku = exts.get_extension_for_class(x509.KeyUsage)
        if not (ku.critical and ku.value.key_cert_sign and ku.value.crl_sign):
            out.append("key_usage")
    except x509.ExtensionNotFound:
        out.append("key_usage")
    try:
        nce = exts.get_extension_for_class(x509.NameConstraints)
    except x509.ExtensionNotFound:
        out.append("constraints_missing")
    else:
        if not nce.critical:
            out.append("constraints_not_critical")
        if set(ca.domains) != set(domains):
            out.append("domains_changed")
        excluded = {str(n.value) for n in (nce.value.excluded_subtrees or [])}
        if not {"0.0.0.0/0", "::/0"} <= excluded:
            out.append("ips_not_excluded")
    if cert.public_key().public_numbers() != ca.key.public_key().public_numbers():  # type: ignore[union-attr]
        out.append("key_mismatch")
    moment = now or _now()
    if cert.not_valid_after_utc <= moment:
        out.append("expired")
    elif cert.not_valid_after_utc - moment < EXPIRES_SOON:
        out.append("expires_soon")
    return out


# ------------------------------------------------------------------------------------ storage
class CAStore:
    """The CA of one masking home: ``<home>/routing/{ca.key.dpapi, ca.cer, ca.pem, dhparam.pem}``.

    ``crypto`` is ``(protect, unprotect)`` with the call shapes of ``win32crypt.CryptProtectData`` /
    ``CryptUnprotectData`` (the default is DPAPI for the current user; tests pass a fake)."""

    def __init__(
        self,
        home: Path,
        crypto: Crypto | None = None,
        domains: tuple[str, ...] = ROUTED_DOMAINS,
    ) -> None:
        self.dir = Path(home) / ROUTING_DIR
        self.domains = tuple(domains)
        self._crypto = crypto if crypto is not None else default_crypto()

    # -------------------------------------------------------------- paths
    @property
    def key_path(self) -> Path:
        return self.dir / KEY_FILE

    @property
    def cer_path(self) -> Path:
        return self.dir / CER_FILE

    @property
    def pem_path(self) -> Path:
        return self.dir / PEM_FILE

    @property
    def dh_path(self) -> Path:
        return self.dir / DH_FILE

    def exists(self) -> bool:
        return self.key_path.is_file() and self.cer_path.is_file()

    # -------------------------------------------------------------- load / create
    def load(self) -> LocalCA | None:
        """The stored CA, or None when there is none. ``RoutingError("ca_unreadable")`` when the
        files cannot be read (another Windows user, a damaged file), ``"ca_mismatch"`` when the
        certificate and the key do not belong together."""
        if not self.exists():
            return None
        crypto = self._need_crypto()
        try:
            _desc, plain = crypto[1](self.key_path.read_bytes(), None, None, None, 0)
            key = serialization.load_pem_private_key(plain, password=None)
            cert = x509.load_der_x509_certificate(self.cer_path.read_bytes())
        except Exception:  # noqa: BLE001 - fixed code, never the cause (it may name key material)
            raise RoutingError("ca_unreadable") from None
        if not isinstance(key, rsa.RSAPrivateKey):
            raise RoutingError("ca_unreadable")
        ca = LocalCA(key, cert)
        if "key_mismatch" in problems(ca, self.domains):
            raise RoutingError("ca_mismatch")
        return ca

    def ensure(self) -> LocalCA:
        """The stored CA; made (and stored) on first use."""
        ca = self.load()
        if ca is None:
            ca = build_ca(self.domains)
            self._write(ca)
        return ca

    def rotate(self) -> tuple[LocalCA, str | None]:
        """Replace the CA by a new one. Returns it and the thumbprint of the old certificate
        (None when there was none, or it could not be read): the setup removes that thumbprint from
        the Windows store and installs the new certificate."""
        old: str | None = None
        try:
            previous = self.load()
            old = previous.thumbprint if previous is not None else None
        except RoutingError:  # the key cannot be read, the public certificate still names it
            try:
                old = thumbprint_of(self.cer_path.read_bytes())
            except OSError:
                old = None
        ca = build_ca(self.domains)
        self._write(ca)
        return ca, old

    def _need_crypto(self) -> Crypto:
        if self._crypto is None:
            raise RoutingError("dpapi_unavailable")  # nothing is ever written in clear text
        return self._crypto

    def _write(self, ca: LocalCA) -> None:
        crypto = self._need_crypto()
        plain = ca.key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        blob = crypto[0](plain, _DESCRIPTION, None, None, None, 0)
        self.dir.mkdir(parents=True, exist_ok=True)
        for path, data in (
            (self.cer_path, ca.der),
            (self.pem_path, ca.pem),
            (self.key_path, blob),
        ):
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)

    # -------------------------------------------------------------- mitmproxy
    def mitm_store(self, ca: LocalCA | None = None) -> Any:
        """A ``mitmproxy.certs.CertStore`` built from the IN-MEMORY key and certificate (mitmproxy's
        own ``from_store`` / ``from_files`` would need a PEM with the plain key on disk). The Diffie-
        Hellman parameters are public and are kept in ``dhparam.pem``."""
        try:
            from mitmproxy import certs  # noqa: PLC0415
        except ImportError:
            raise RoutingError("mitmproxy_unavailable") from None
        ca = ca or self.ensure()
        self.dir.mkdir(parents=True, exist_ok=True)
        if not self.dh_path.is_file():
            self.dh_path.write_bytes(certs.DEFAULT_DHPARAM)
        crl = certs.dummy_crl(ca.key, ca.cert)
        dh = certs.CertStore.load_dhparam(self.dh_path)

        domains = ca.domains or self.domains

        class ConstrainedCertStore(certs.CertStore):  # type: ignore[misc]
            """mitmproxy's store, minus every name this CA's own constraints would reject: a leaf
            with an IP address or a domain outside the routed ones in its names is refused by the
            CLIENT as a whole, so it is never made (an upstream certificate that lists other names,
            or a connection by IP address, must not break the routed host)."""

            def get_cert(
                self,
                commonname: str | None,
                sans: Any,
                organization: str | None = None,
                crl_url: str | None = None,
            ) -> Any:
                kept = [
                    n
                    for n in sans
                    if isinstance(n, x509.DNSName) and within_domains(str(n.value), domains)
                ]
                if not kept:
                    raise ValueError("no_permitted_name")
                if not (commonname and within_domains(commonname, domains)):
                    commonname = str(kept[0].value)
                return super().get_cert(commonname, kept, organization, crl_url)

        return ConstrainedCertStore(ca.key, certs.Cert(ca.cert), None, crl, dh)
