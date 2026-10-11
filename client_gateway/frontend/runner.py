"""Runs mitmproxy inside the gateway process: the listener of the TLS front end.

* In-process: ``mitmproxy.master.Master`` on its own asyncio loop in a daemon thread, with a curated
  addon list (proxy server, TLS config, next-layer decision, DNS, h2c / alt-svc / HTTPS-record
  hygiene, ``Block``) plus ``FrontendAddon``; no UI, no script loader, no onboarding app, no flow
  storage. ``mode=regular`` on ``127.0.0.1:<port>`` only (a non-loopback host is refused).
* ``allow_hosts`` = the routed AI hosts (``addon.ROUTED_SUFFIXES``): every other CONNECT is tunnelled
  without being decrypted. Upstream TLS is verified (``ssl_insecure`` is never set).
* Lab CA (default): lives in ``<masking home>/frontend`` (mitmproxy's ``mitmproxy-ca.pem`` +
  ``-cert.pem``), made here on first start so ``ca_spki_sha256_b64()`` is known before the first
  connection: test windows trust it only through ``--ignore-certificate-errors-spki-list=<spki>``.
* Routing CA (``cert_store`` given, phase 3b): a factory returning a ready ``mitmproxy.certs.CertStore``
  built from the IN-MEMORY key of the name-constrained local CA (``client_gateway.routing.ca``). The
  ``TlsConfig`` addon is replaced by a subclass that never builds a store from ``confdir``, so no CA
  file (and no plain key) is written anywhere. Installing that CA in the Windows store is setup's job.
* Exclusive port: a port that is taken raises ``FrontEndError("port_in_use")``. One front end per
  process (mitmproxy keeps its master in a module global).
* mitmproxy is imported only when ``start`` (or ``ca_spki_sha256_b64``) runs.

The messages of ``FrontEndError`` are fixed codes; mitmproxy's own log is not forwarded anywhere.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import socket
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from client_gateway.frontend.addon import ROUTED_SUFFIXES, FrontendAddon, allow_hosts_pattern
from client_gateway.log import GatewayLog
from client_gateway.state import GatewayState

LISTEN_HOST = "127.0.0.1"
CONF_BASENAME = "mitmproxy"  # file names mitmproxy's tlsconfig looks for in confdir
CA_KEY_BITS = 2048
START_WAIT_S = 60.0
STOP_WAIT_S = 15.0
# the addons of mitmproxy's default set this front end needs (by class name)
_KEEP = frozenset(
    {
        "Core",
        "Block",
        "DisableH2C",
        "StripDnsHttpsRecords",
        "UpdateAltSvc",
        "Proxyserver",
        "DnsResolver",
        "NextLayer",
        "TlsConfig",
    }
)
_REQUIRED = frozenset({"Proxyserver", "NextLayer", "TlsConfig", "Core"})


class FrontEndError(Exception):
    """The front end cannot run; the message is a fixed code (``port_in_use``, ``bad_port``,
    ``not_loopback``, ``start_failed``, ``start_timeout``, ``mitmproxy_unavailable``)."""


def _probe_port(host: str, port: int) -> None:
    """Raise ``FrontEndError("port_in_use")`` when ``host:port`` cannot be bound exclusively."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            sock.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        sock.bind((host, port))
    except OSError:
        raise FrontEndError("port_in_use") from None
    finally:
        sock.close()


def spki_sha256_b64(cert: Any) -> str:
    """Base64 of the SHA-256 of the certificate's SubjectPublicKeyInfo (Chromium's
    ``--ignore-certificate-errors-spki-list`` format). ``cert`` is a ``mitmproxy.certs.Cert``."""
    from mitmproxy import certs  # lazy

    ser = certs.serialization
    der = cert.public_key().public_bytes(ser.Encoding.DER, ser.PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(hashlib.sha256(der).digest()).decode("ascii")


class _QuietHandler(logging.Handler):
    """mitmproxy's log goes nowhere (it names hosts and peers); only the error COUNT is kept."""

    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.errors = 0

    def emit(self, record: logging.LogRecord) -> None:
        self.errors += 1


class FrontEnd:
    def __init__(
        self,
        state: GatewayState,
        port: int,
        *,
        log: GatewayLog | None = None,
        host: str = LISTEN_HOST,
        confdir: Path | None = None,
        routed_hosts: tuple[str, ...] = ROUTED_SUFFIXES,
        options: dict[str, Any] | None = None,
        cert_store: Callable[[], Any] | None = None,
        trust_bundle: Path | None = None,
        upstream_proxy: Callable[[str, int, str], Any] | None = None,
    ) -> None:
        if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port < 65536:
            raise FrontEndError("bad_port")
        if host != LISTEN_HOST:
            raise FrontEndError("not_loopback")
        self.state = state
        self.log = log
        self.host = host
        self.requested_port = port
        self.confdir = Path(confdir) if confdir is not None else state.home / "frontend"
        self.routed = tuple(routed_hosts)  # tests route a loopback host; the product: the default
        self.extra_options = dict(options or {})  # mitmproxy options on top of ours (tests only)
        self._cert_store = cert_store  # routing: the CA's in-memory ``CertStore`` (no CA file)
        self._store: Any = None
        self.trust_bundle = Path(trust_bundle) if trust_bundle is not None else None
        self.addon = FrontendAddon(state, log, self.routed, upstream_proxy=upstream_proxy)
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._error: str | None = None
        self._master: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._port = 0
        self._spki: str | None = None
        self._quiet = _QuietHandler()

    # ------------------------------------------------------------------ CA
    def _routing_store(self) -> Any:
        """The injected ``CertStore`` (built once; its failure is a fixed code)."""
        if self._store is None:
            try:
                self._store = self._cert_store() if self._cert_store is not None else None
            except Exception:  # noqa: BLE001 - the cause may name key material: fixed code only
                raise FrontEndError("ca_unavailable") from None
        return self._store

    def ensure_ca(self) -> Any:
        """The CA certificate. Lab CA: created in ``confdir`` when it is not there yet. Routing CA:
        the injected store's (nothing is written)."""
        try:
            from mitmproxy import certs  # lazy
        except ImportError:
            raise FrontEndError("mitmproxy_unavailable") from None
        if self._cert_store is not None:
            return self._routing_store().default_ca
        store = certs.CertStore.from_store(self.confdir, CONF_BASENAME, CA_KEY_BITS)
        return store.default_ca

    def ca_spki_sha256_b64(self) -> str:
        """The SPKI hash of the lab CA, for ``--ignore-certificate-errors-spki-list`` of a test
        window. Creates the CA on first use."""
        if self._spki is None:
            self._spki = spki_sha256_b64(self.ensure_ca())
        return self._spki

    @property
    def ca_cert_path(self) -> Path:
        return self.confdir / f"{CONF_BASENAME}-ca-cert.pem"

    # ------------------------------------------------------------------ lifecycle
    @property
    def port(self) -> int:
        """The port actually listened on (the requested one, or the one the OS gave for 0)."""
        return self._port or self.requested_port

    @property
    def running(self) -> bool:
        thread = self._thread
        return bool(thread is not None and thread.is_alive() and self._ready.is_set())

    def start(self, timeout: float = START_WAIT_S) -> None:
        """Start listening; returns when the port accepts connections. Raises ``FrontEndError``."""
        if self._thread is not None:
            raise FrontEndError("start_failed")
        self.ensure_ca()
        _probe_port(self.host, self.requested_port)
        self._thread = threading.Thread(target=self._run, name="frontend", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            self.stop()
            raise FrontEndError("start_timeout")
        if self._error is not None:
            code = self._error
            self.stop()
            raise FrontEndError(code)
        self.state.frontend_status = self.status
        if self.log is not None:
            self.log.event("frontend_start", port=self.port)

    def stop(self) -> None:
        """Shut the listener down and join its thread. Safe to call twice."""
        master, thread = self._master, self._thread
        if master is not None:
            try:
                master.shutdown()  # thread-safe
            except Exception:  # noqa: BLE001
                pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=STOP_WAIT_S)
        self._master = None
        self._thread = None
        self._ready.clear()
        if self.state.frontend_status == self.status:
            self.state.frontend_status = None
        if self.log is not None:
            self.log.event("frontend_stop")

    def status(self) -> dict[str, Any]:
        """Counts and fixed labels only (shown in the gateway status): the port, the CA's SPKI hash
        (a public value) and whether it listens."""
        out: dict[str, Any] = {
            "enabled": True,
            "running": self.running,
            "port": self.port,
            "mode": "regular",
            "routed_hosts": list(self.routed),
        }
        try:
            out["spki_sha256_b64"] = self.ca_spki_sha256_b64()
        except Exception:  # noqa: BLE001
            out["error"] = True
        return out

    # ------------------------------------------------------------------ the mitmproxy thread
    def _run(self) -> None:
        mitm_log = logging.getLogger("mitmproxy")
        mitm_log.addHandler(self._quiet)
        mitm_log.propagate = False
        try:
            asyncio.run(self._main())
        except BaseException:  # noqa: BLE001 - the thread must report, never die silently
            if not self._ready.is_set():
                self._error = self._error or "start_failed"
                self._ready.set()
        finally:
            mitm_log.removeHandler(self._quiet)
            mitm_log.propagate = True

    async def _main(self) -> None:
        try:
            from mitmproxy import addons as mitm_addons
            from mitmproxy import options as mitm_options
            from mitmproxy.master import Master
        except ImportError:
            self._error = "mitmproxy_unavailable"
            self._ready.set()
            return
        loop = asyncio.get_running_loop()
        opts = mitm_options.Options()
        master = Master(opts, event_loop=loop, with_termlog=False)
        chosen = [a for a in mitm_addons.default_addons() if type(a).__name__ in _KEEP]
        if self._cert_store is not None:  # routing: the CA never comes from a file in confdir
            chosen = [
                self._in_memory_tls() if type(a).__name__ == "TlsConfig" else a for a in chosen
            ]
        if not _REQUIRED <= {type(a).__name__ for a in chosen}:
            self._error = "start_failed"
            self._ready.set()
            return
        ready = _ReadyAddon(self, master)
        master.addons.add(*chosen, self.addon, ready)
        routing: dict[str, Any] = {}
        if self._cert_store is not None:
            # routing: the upstream connection is made when the request arrives (so the previous
            # proxy can be chained per connection) and the leaf certificate carries only the names
            # the app asked for (never the upstream certificate's other names)
            routing = {"connection_strategy": "lazy", "upstream_cert": False}
        if self.trust_bundle is not None:
            # the REAL server's certificate is verified against this bundle (the Windows roots, a
            # company TLS-inspection root included); ``ssl_insecure`` is never set
            routing["ssl_verify_upstream_trusted_ca"] = str(self.trust_bundle)
        opts.update(
            listen_host=self.host,
            listen_port=self.requested_port,
            confdir=str(self.confdir),
            mode=["regular"],
            allow_hosts=[allow_hosts_pattern(self.routed)],
            **{**routing, **self.extra_options},
        )
        self._master, self._loop = master, loop
        await master.run()

    def _in_memory_tls(self) -> Any:
        """``TlsConfig`` with the injected ``CertStore``: ``configure`` is mitmproxy's own, except that
        it never builds a store from ``confdir`` (that would write a plain CA key to disk)."""
        from mitmproxy.addons.tlsconfig import TlsConfig  # lazy

        store = self._routing_store()
        stores = {"certs", "confdir", "key_size", "cert_passphrase"}

        class RoutingTlsConfig(TlsConfig):  # type: ignore[misc]
            def configure(self, updated: Any) -> None:
                names = {updated} if isinstance(updated, str) else set(updated)
                super().configure(names - stores)
                if names & stores or getattr(self, "certstore", None) is None:
                    self.certstore = store

        # mitmproxy names an addon after its class (``master.addons.get("tlsconfig")``): keep the name
        RoutingTlsConfig.__name__ = RoutingTlsConfig.__qualname__ = "TlsConfig"
        return RoutingTlsConfig()


class _ReadyAddon:
    """Signals ``FrontEnd.start`` when the servers are up (mitmproxy's ``running`` hook) and checks
    that the port really is bound."""

    def __init__(self, front: FrontEnd, master: Any) -> None:
        self._front = front
        self._master = master

    def running(self) -> None:
        front = self._front
        proxy = self._master.addons.get("proxyserver")
        addrs = proxy.listen_addrs() if proxy is not None else []
        if addrs:
            front._port = int(addrs[0][1])
        else:
            front._error = "port_in_use" if front._quiet.errors else "start_failed"
            self._master.shutdown()
        front._ready.set()
