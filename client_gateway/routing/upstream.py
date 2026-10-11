"""How the front end reaches the real AI hosts (spec §8.2, §8.3 "company proxy / TLS inspection").

* **Trust.** The front end verifies the REAL server's certificate (verification is never switched off:
  ``ssl_insecure`` is not used anywhere). Against which roots? A company that inspects TLS puts its own
  root in the Windows store, and a gateway that trusted only a public list would fail there; so the
  roots are the WINDOWS ones (``ssl.enum_certificates`` for ``ROOT`` and ``CA``, only those trusted for
  server authentication) plus the public Mozilla list (``certifi``: Windows adds public roots on demand,
  so a PC that never needed one would otherwise miss it). Our own interception CA is left OUT of the
  bundle (by thumbprint and by subject name, so a rotated old one too): the gateway must never accept a
  connection that goes back through itself. The bundle is written to ``<home>/routing/upstream-ca.pem``
  (public certificates only) and handed to mitmproxy as ``ssl_verify_upstream_trusted_ca``.
* **Upstream proxy.** When the user's PREVIOUS decision was a static proxy, the routed hosts reach the
  internet through it (R7: the company route is kept): ``UpstreamProxy.via`` gives mitmproxy's per-
  connection ``server_conn.via`` for a host, honouring the ``ProxyOverride`` bypass list. A previous
  PAC script or SOCKS proxy cannot be chained (mitmproxy chains HTTP proxies only): those connect
  directly and ``status`` says so (``upstream: direct``).

Only counts and fixed labels leave this module.
"""

from __future__ import annotations

import os
import re
import ssl
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from client_gateway.routing.ca import CA_COMMON_NAME, ROUTING_DIR, thumbprint_of
from client_gateway.routing.pac import Previous, StaticProxy

BUNDLE_FILE = "upstream-ca.pem"
SERVER_AUTH_OID = "1.3.6.1.5.5.7.3.1"
WINDOWS_STORES = ("ROOT", "CA")
EnumStore = Callable[[str], Iterable[tuple[bytes, str, Any]]]


def windows_store(name: str) -> list[tuple[bytes, str, Any]]:
    """The certificates of a Windows system store, READ ONLY (``ssl.enum_certificates``); empty
    where the platform has none."""
    enum = getattr(ssl, "enum_certificates", None)
    if enum is None:
        return []
    try:
        return list(enum(name))
    except OSError:
        return []


def certifi_text() -> str:
    """The Mozilla public roots (PEM text), or an empty string when ``certifi`` is missing."""
    try:
        import certifi  # noqa: PLC0415

        return Path(certifi.where()).read_text(encoding="ascii")
    except (ImportError, OSError):
        return ""


@dataclass(frozen=True)
class BundleInfo:
    path: Path
    windows: int  # certificates taken from the Windows stores
    skipped: int  # not usable for servers / not X.509 / our own CA
    certifi: bool


def _subject_cn(der: bytes) -> str | None:
    from cryptography import x509  # noqa: PLC0415

    cert = x509.load_der_x509_certificate(der)
    attrs = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
    return str(attrs[0].value) if attrs else None


def build_upstream_bundle(
    home: Path,
    *,
    enum_store: EnumStore = windows_store,
    exclude_thumbprints: Iterable[str] = (),
    include_certifi: bool = True,
    certifi_pem: Callable[[], str] = certifi_text,
) -> BundleInfo:
    """Write the upstream trust bundle and say how many certificates went in. The default
    ``enum_store`` is a read of the Windows store; tests pass a fake."""
    skip = {t.upper() for t in exclude_thumbprints}
    seen: set[str] = set()
    pems: list[str] = []
    skipped = 0
    for store in WINDOWS_STORES:
        for der, encoding, trust in enum_store(store):
            if encoding != "x509_asn":
                skipped += 1
                continue
            if trust is not True and SERVER_AUTH_OID not in (trust or ()):
                skipped += 1  # not trusted for server authentication (a code-signing root, ...)
                continue
            thumb = thumbprint_of(der)
            if thumb in seen:
                continue
            try:
                ours = thumb in skip or _subject_cn(der) == CA_COMMON_NAME
            except ValueError:
                skipped += 1  # not a certificate
                continue
            if ours:
                skipped += 1
                continue
            seen.add(thumb)
            pems.append(ssl.DER_cert_to_PEM_cert(der))
    extra = certifi_pem() if include_certifi else ""
    text = "".join(pems) + extra
    path = Path(home) / ROUTING_DIR / BUNDLE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(text.encode("ascii"))
    os.replace(tmp, path)
    return BundleInfo(path, len(pems), skipped, bool(extra))


# ------------------------------------------------------------------------------------ chaining
_HOST_PORT = re.compile(r"^(?P<host>\[[0-9A-Fa-f:]+\]|[A-Za-z0-9._\-]+)(?::(?P<port>\d{1,5}))?$")


def _host_port(entry: str, default_port: int = 80) -> tuple[str, int] | None:
    match = _HOST_PORT.match(entry)
    if match is None:
        return None
    port = int(match.group("port") or default_port)
    if not 0 < port < 65536:
        return None
    return match.group("host").strip("[]"), port


def _bypassed(host: str, static: StaticProxy) -> bool:
    name = host.lower()
    for item in static.bypass:
        if item == "<local>":
            if "." not in name:
                return True
        elif re.fullmatch(
            re.escape(item).replace(r"\*", ".*").replace(r"\?", "."), name, re.IGNORECASE
        ):
            return True
    return False


class UpstreamProxy:
    """The upstream proxy of the routed hosts: the previous static proxy, or None (direct)."""

    def __init__(self, previous: Previous | None = None) -> None:
        self._static: StaticProxy | None = None
        self.mode = "direct"
        if previous is not None:
            self.set_previous(previous)

    def set_previous(self, previous: Previous) -> None:
        """``direct`` / ``pac`` -> connect directly; ``static`` -> chain through that proxy."""
        self._static = previous.static if previous.kind == "static" else None
        self.mode = {"static": "static_proxy", "pac": "direct_pac_not_chained"}.get(
            previous.kind, "direct"
        )

    def via(
        self, host: str, port: int = 443, scheme: str = "https"
    ) -> tuple[str, tuple[str, int]] | None:
        """mitmproxy's ``via`` for a connection to ``host``: ``("http", (proxy host, port))`` or None.
        Never raises: an entry that cannot be read means no chaining."""
        static = self._static
        if static is None or _bypassed(host, static):
            return None
        entry = static.entries.get(scheme) or static.entries.get("*")
        if entry is None:
            return None  # a SOCKS-only proxy cannot be chained
        target = _host_port(entry)
        return ("http", target) if target is not None else None
