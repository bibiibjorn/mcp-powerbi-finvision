"""The certificate-store backend: where the name-constrained local CA is trusted (spec §8.2, rule R8).

``CertStoreBackend`` is the interface (``installed`` / ``install`` / ``remove`` / ``list_ours``).
``MemoryCertStore`` is the fake every test (and every dry run) uses. ``WindowsCertStore`` is the real
one: ``CurrentUser\\Root`` through ``crypt32`` (ctypes); Windows itself asks the user to confirm a
certificate added to that store. Building a ``WindowsCertStore`` calls ``guard.require_real_system``
FIRST, so it raises in every test, lab run and build, and works only when ``setup`` (a human in a
terminal) has set ``FINVISION_MASKING_REAL_SYSTEM=1``.

``CAInstaller`` is the only caller of a backend. It records what it installed in
``<home>/routing/state.json`` (thumbprints) and removes ONLY a certificate that is both recorded there
and carries our friendly name in the store: a certificate somebody else put in the store, or one that
merely has our name, is never touched.

Failures raise ``CertStoreError(code)`` with a fixed code.
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from typing import Any, Protocol

from client_gateway.routing.ca import ROUTING_DIR, LocalCA, thumbprint_of
from client_gateway.routing.guard import require_real_system

FRIENDLY_NAME = "Finvision masking gateway (local only)"
STATE_FILE = "state.json"
STORE_NAME = "Root"


class CertStoreError(Exception):
    """A certificate-store step failed. The message is a fixed code (``user_declined``,
    ``install_failed``, ``remove_failed``, ``not_installed``, ``store_unavailable``)."""


class CertStoreBackend(Protocol):
    def installed(self, thumbprint: str) -> bool:
        """Is a certificate with this SHA-1 thumbprint in the store (whoever put it there)?"""

    def install(self, der: bytes) -> None:
        """Add the DER certificate and give it the friendly name ``FRIENDLY_NAME``."""

    def remove(self, thumbprint: str) -> None:
        """Delete the certificate with this thumbprint. ``CertStoreError("not_installed")`` when it
        is not there."""

    def list_ours(self) -> list[str]:
        """Thumbprints of the certificates in the store whose friendly name is ``FRIENDLY_NAME``."""


# ------------------------------------------------------------------------------------ the fake
class MemoryCertStore:
    """An in-memory store: nothing outside this object changes. ``decline`` makes ``install`` behave
    as if the user answered No to Windows' confirmation dialog."""

    def __init__(self, decline: bool = False) -> None:
        self.certs: dict[str, tuple[bytes, str]] = {}  # thumbprint -> (der, friendly name)
        self.decline = decline
        self.calls: list[tuple[str, str]] = []  # (operation, thumbprint) for the tests

    def add_foreign(self, der: bytes, friendly_name: str = "") -> str:
        """A certificate somebody else put there (a test of "never touch what is not ours")."""
        thumb = thumbprint_of(der)
        self.certs[thumb] = (der, friendly_name)
        return thumb

    def installed(self, thumbprint: str) -> bool:
        return thumbprint.upper() in self.certs

    def install(self, der: bytes) -> None:
        thumb = thumbprint_of(der)
        self.calls.append(("install", thumb))
        if self.decline:
            raise CertStoreError("user_declined")
        self.certs[thumb] = (der, FRIENDLY_NAME)

    def remove(self, thumbprint: str) -> None:
        thumb = thumbprint.upper()
        self.calls.append(("remove", thumb))
        if thumb not in self.certs:
            raise CertStoreError("not_installed")
        del self.certs[thumb]

    def list_ours(self) -> list[str]:
        return sorted(t for t, (_der, name) in self.certs.items() if name == FRIENDLY_NAME)


# ------------------------------------------------------------------------------------ the real one
class WindowsCertStore:
    """``Cert:\\CurrentUser\\Root`` through ``crypt32``. REAL: refused unless ``guard`` allows it."""

    X509_ASN_ENCODING = 0x1
    CERT_STORE_PROV_SYSTEM_W = 10
    CERT_SYSTEM_STORE_CURRENT_USER = 0x10000
    CERT_STORE_OPEN_EXISTING_FLAG = 0x4000
    CERT_STORE_ADD_REPLACE_EXISTING = 3
    CERT_FRIENDLY_NAME_PROP_ID = 11

    def __init__(self) -> None:
        require_real_system("Windows certificate store")  # before ctypes is even imported
        if os.name != "nt":
            raise CertStoreError("store_unavailable")
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        class CertContext(ctypes.Structure):
            _fields_ = [
                ("dwCertEncodingType", wintypes.DWORD),
                ("pbCertEncoded", ctypes.POINTER(ctypes.c_ubyte)),
                ("cbCertEncoded", wintypes.DWORD),
                ("pCertInfo", ctypes.c_void_p),
                ("hCertStore", ctypes.c_void_p),
            ]

        class DataBlob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

        self._ctypes = ctypes
        self._CertContext = CertContext
        self._DataBlob = DataBlob
        crypt = ctypes.WinDLL("crypt32", use_last_error=True)
        vp, dw, bl = ctypes.c_void_p, wintypes.DWORD, wintypes.BOOL
        crypt.CertOpenStore.restype = vp
        crypt.CertOpenStore.argtypes = [vp, dw, vp, dw, ctypes.c_wchar_p]
        crypt.CertCloseStore.restype = bl
        crypt.CertCloseStore.argtypes = [vp, dw]
        crypt.CertAddEncodedCertificateToStore.restype = bl
        crypt.CertAddEncodedCertificateToStore.argtypes = [
            vp, dw, ctypes.c_char_p, dw, dw, ctypes.POINTER(vp),
        ]  # fmt: skip
        crypt.CertSetCertificateContextProperty.restype = bl
        crypt.CertSetCertificateContextProperty.argtypes = [vp, dw, dw, vp]
        crypt.CertGetCertificateContextProperty.restype = bl
        crypt.CertGetCertificateContextProperty.argtypes = [vp, dw, vp, ctypes.POINTER(dw)]
        crypt.CertEnumCertificatesInStore.restype = vp
        crypt.CertEnumCertificatesInStore.argtypes = [vp, vp]
        crypt.CertDeleteCertificateFromStore.restype = bl
        crypt.CertDeleteCertificateFromStore.argtypes = [vp]
        crypt.CertFreeCertificateContext.restype = bl
        crypt.CertFreeCertificateContext.argtypes = [vp]
        self._crypt = crypt

    # -------------------------------------------------------------- helpers
    def _open(self) -> Any:
        flags = self.CERT_SYSTEM_STORE_CURRENT_USER | self.CERT_STORE_OPEN_EXISTING_FLAG
        handle = self._crypt.CertOpenStore(
            self.CERT_STORE_PROV_SYSTEM_W, 0, None, flags, STORE_NAME
        )
        if not handle:
            raise CertStoreError("store_unavailable")
        return handle

    def _der(self, ctx: Any) -> bytes:
        c = self._ctypes
        info = c.cast(ctx, c.POINTER(self._CertContext)).contents
        return bytes(c.string_at(info.pbCertEncoded, info.cbCertEncoded))

    def _friendly(self, ctx: Any) -> str:
        from ctypes import wintypes  # noqa: PLC0415

        c = self._ctypes
        size = wintypes.DWORD(0)
        prop = self.CERT_FRIENDLY_NAME_PROP_ID
        if not self._crypt.CertGetCertificateContextProperty(ctx, prop, None, c.byref(size)):
            return ""
        buf = c.create_string_buffer(size.value)
        if not self._crypt.CertGetCertificateContextProperty(ctx, prop, buf, c.byref(size)):
            return ""
        return bytes(buf.raw[: size.value]).decode("utf-16-le", "replace").rstrip("\x00")

    def _each(self, store: Any) -> Any:
        """Yield every certificate context of the store (the walk frees the previous one)."""
        ctx = self._crypt.CertEnumCertificatesInStore(store, None)
        while ctx:
            yield ctx
            ctx = self._crypt.CertEnumCertificatesInStore(store, ctx)

    # -------------------------------------------------------------- interface
    def installed(self, thumbprint: str) -> bool:
        store = self._open()
        try:
            return any(thumbprint_of(self._der(c)) == thumbprint.upper() for c in self._each(store))
        finally:
            self._crypt.CertCloseStore(store, 0)

    def install(self, der: bytes) -> None:
        c = self._ctypes
        store = self._open()
        try:
            ctx = c.c_void_p()
            added = self._crypt.CertAddEncodedCertificateToStore(
                store,
                self.X509_ASN_ENCODING,
                der,
                len(der),
                self.CERT_STORE_ADD_REPLACE_EXISTING,
                c.byref(ctx),
            )
            if not added:
                # ERROR_CANCELLED (1223): the user answered No in Windows' confirmation dialog
                raise CertStoreError(
                    "user_declined" if c.get_last_error() == 1223 else "install_failed"
                )
            try:
                name = (FRIENDLY_NAME + "\x00").encode("utf-16-le")
                raw = c.create_string_buffer(name, len(name))
                blob = self._DataBlob(len(name), c.cast(raw, c.POINTER(c.c_ubyte)))
                if not self._crypt.CertSetCertificateContextProperty(
                    ctx, self.CERT_FRIENDLY_NAME_PROP_ID, 0, c.byref(blob)
                ):
                    raise CertStoreError("install_failed")
            finally:
                self._crypt.CertFreeCertificateContext(ctx)
        finally:
            self._crypt.CertCloseStore(store, 0)

    def remove(self, thumbprint: str) -> None:
        store = self._open()
        try:
            for ctx in self._each(store):
                if thumbprint_of(self._der(ctx)) == thumbprint.upper():
                    if not self._crypt.CertDeleteCertificateFromStore(ctx):  # frees the context
                        raise CertStoreError("remove_failed")
                    return
            raise CertStoreError("not_installed")
        finally:
            self._crypt.CertCloseStore(store, 0)

    def list_ours(self) -> list[str]:
        store = self._open()
        try:
            return sorted(
                thumbprint_of(self._der(c))
                for c in self._each(store)
                if self._friendly(c) == FRIENDLY_NAME
            )
        finally:
            self._crypt.CertCloseStore(store, 0)


# ------------------------------------------------------------------------------------ the installer
class CAInstaller:
    """Installs and removes the local CA through a backend; records its thumbprints."""

    def __init__(self, home: Path, backend: CertStoreBackend) -> None:
        self.path = Path(home) / ROUTING_DIR / STATE_FILE
        self.backend = backend

    # -------------------------------------------------------------- state.json
    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def recorded(self) -> list[str]:
        """The thumbprints this installer put in the store (state.json)."""
        found = self._read().get("ca_thumbprints")
        return [str(t).upper() for t in found] if isinstance(found, list) else []

    def _write(self, thumbprints: list[str]) -> None:
        data = self._read()
        data["ca_thumbprints"] = sorted(set(thumbprints))
        data["updated"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    # -------------------------------------------------------------- operations
    def install(self, ca: LocalCA) -> dict[str, Any]:
        """Trust ``ca``. Already installed (and recorded) = nothing happens. Records the thumbprint
        only after the store reports the certificate present."""
        thumb = ca.thumbprint
        if self.backend.installed(thumb) and thumb in self.recorded():
            return {"installed": True, "changed": False, "thumbprint": thumb}
        self.backend.install(ca.der)
        if not self.backend.installed(thumb):
            raise CertStoreError("install_failed")
        self._write([*self.recorded(), thumb])
        return {"installed": True, "changed": True, "thumbprint": thumb}

    def remove(self, thumbprint: str | None = None) -> dict[str, Any]:
        """Remove what this installer installed: one thumbprint, or all. Only a certificate that is
        recorded in state.json AND carries our friendly name in the store is removed; anything else
        is left alone and reported under ``skipped``."""
        ours = set(self.backend.list_ours())
        wanted = [thumbprint.upper()] if thumbprint else self.recorded()
        removed: list[str] = []
        skipped: list[str] = []
        recorded = set(self.recorded())
        for thumb in wanted:
            if thumb not in recorded or thumb not in ours:
                skipped.append(thumb)
                continue
            self.backend.remove(thumb)
            removed.append(thumb)
        self._write([t for t in self.recorded() if t not in removed])
        return {"removed": removed, "skipped": skipped}

    def replace(self, old_thumbprint: str | None, ca: LocalCA) -> dict[str, Any]:
        """A rotated CA: trust the new certificate first, then remove the old one (never a window
        with no CA)."""
        result = self.install(ca)
        if old_thumbprint and old_thumbprint.upper() != ca.thumbprint:
            result["old"] = self.remove(old_thumbprint)
        return result

    def status(self, ca: LocalCA | None = None) -> dict[str, Any]:
        """What is installed: ``recorded`` (state.json), ``in_store`` (ours by friendly name), and
        whether ``ca`` is trusted. Certificates with our name that were not recorded are listed as
        ``unrecorded`` (never removed by this installer)."""
        recorded = set(self.recorded())
        in_store = self.backend.list_ours()
        out: dict[str, Any] = {
            "recorded": sorted(recorded),
            "in_store": in_store,
            "unrecorded": sorted(set(in_store) - recorded),
        }
        if ca is not None:
            out["current_trusted"] = self.backend.installed(ca.thumbprint)
        return out
