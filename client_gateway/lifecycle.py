"""Lifecycle of the routed gateway: who keeps it alive, who turns the proxy rule on and off (spec §8.1, R1, R7).

``RoutingControl`` is the one place that turns routing on and off: the name-constrained CA must be
trusted, the previous proxy decision is read (and a previous PAC fetched once), the PAC is published
(``GET /proxy.pac`` through ``GatewayState.pac_provider``), the front end must already listen, and only
then ``AutoConfigURL`` is pointed at the PAC (after a one-time backup). ``disable`` restores the backup
exactly. Every system change goes through the backends of ``client_gateway.routing`` (certificate store,
Internet Settings): the fakes in tests, the guarded real ones in production.

``Lifecycle`` owns the gateway process's life when routing is on:

* the Power BI MCP servers ``attach`` their process id (``POST /__gateway/attach``) and ``detach``;
* a watchdog (every 2 s) prunes attached processes that died (a recycled pid is told apart by its
  creation time); when none is alive the rule is removed, the front end stops and the gateway exits
  (rule R1: no Power BI MCP running = no gateway active, no proxy rule, the apps go direct);
* a gateway that nothing attaches to within 60 s of its start exits too (the Claude Code exception:
  its first request can come before the MCP starts, so the SessionStart hook may start the gateway);
* ``cleanup_stale_rule`` (every gateway start and every MCP start): an ``AutoConfigURL`` that points at
  our PAC URL while no gateway answers there is a crash leftover and the backup is restored.

Nothing here installs an autostart entry, a service, a scheduled task or a Run key.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from client_gateway.routing.ca import INTERCEPTED_DOMAINS, CAStore, RoutingError, problems
from client_gateway.routing.certstore import CAInstaller, CertStoreBackend
from client_gateway.routing.pac import (
    PacProvider,
    RoutingRefused,
    fetch_pac_script,
    prepare_previous,
)
from client_gateway.routing.upstream import UpstreamProxy
from client_gateway.routing.wpad import UNKNOWN as WPAD_UNKNOWN
from client_gateway.routing.wpad import WpadProbe, wpad_result
from client_gateway.routing.winproxy import (
    InternetSettings,
    InternetSettingsBackend,
    ProxyError,
)

logger = logging.getLogger(__name__)

WATCHDOG_S = 2.0
ATTACH_GRACE_S = 60.0
START_WAIT_S = 20.0
PAC_PATH = "/proxy.pac"
# problems of a CA that make it unfit to route with (``expires_soon`` is only a warning)
UNFIT = frozenset(
    {
        "not_a_ca", "path_length", "constraints_missing", "constraints_not_critical",
        "domains_changed", "ips_not_excluded", "key_usage", "key_mismatch", "expired",
    }
)  # fmt: skip
_OUR_PAC = re.compile(r"^http://127\.0\.0\.1:(\d{1,5})/proxy\.pac$")


# ------------------------------------------------------------------------------------ processes
class ProcessProbe(Protocol):
    def identify(self, pid: int) -> float | None:
        """A value that identifies this process for as long as it lives (its creation time), or
        None when no such process exists."""

    def alive(self, pid: int, identity: float) -> bool:
        """Is the process with this id still the one that was identified?"""


class PsutilProbe:
    """``psutil``: a pid whose creation time changed is another process (the id was reused)."""

    def identify(self, pid: int) -> float | None:
        import psutil  # noqa: PLC0415

        try:
            return float(psutil.Process(pid).create_time())
        except (psutil.Error, OSError, ValueError):
            return None

    def alive(self, pid: int, identity: float) -> bool:
        current = self.identify(pid)
        return current is not None and abs(current - identity) < 1.0


# ------------------------------------------------------------------------------------ routing
class RoutingControl:
    """Turns the proxy rule on and off. ``enable`` returns ``{"enabled": bool, "reason": code}`` and
    never raises; ``disable`` restores the backup (idempotent)."""

    def __init__(
        self,
        state: Any,
        home: Path,
        gateway_port: int,
        frontend: Any,
        *,
        certs: CertStoreBackend,
        settings: InternetSettingsBackend,
        crypto: Any = None,
        fetch: Callable[[str], str] = fetch_pac_script,
        autodetect_is_direct: bool = False,
        wpad: WpadProbe | None = None,
    ) -> None:
        self.state = state
        self.home = Path(home)
        self.gateway_port = gateway_port
        self.frontend = frontend
        self.fetch = fetch
        self.autodetect_is_direct = autodetect_is_direct
        self.wpad = wpad  # read-only: does WPAD find a PAC? (``none`` = auto-detect is direct)
        self.wpad_result = ""
        self.ca_store = CAStore(self.home, crypto=crypto)
        self.installer = CAInstaller(self.home, certs)
        self.manager = InternetSettings(self.home, settings)
        self.upstream = UpstreamProxy()  # the front end asks it per connection (``.via``)
        self.enabled = False
        self.reason = "not_enabled"

    @property
    def pac_url(self) -> str:
        return f"http://127.0.0.1:{self.gateway_port}{PAC_PATH}"

    def _refuse(self, reason: str) -> dict[str, Any]:
        self.enabled = False
        self.reason = reason
        return {"enabled": False, "reason": reason}

    def preflight(self) -> str:
        """Why the CA cannot be used to route (a fixed code), or an empty string when it can: it
        exists, is fit (constraints, key, validity) and is trusted in the certificate store."""
        try:
            ca = self.ca_store.load()
        except RoutingError as exc:
            return str(exc)
        if ca is None:
            return "ca_missing"
        if UNFIT & set(problems(ca, self.ca_store.domains)):
            return "ca_needs_rotation"
        if not self.installer.status(ca).get("current_trusted"):
            return "ca_not_trusted"
        return ""

    def enable(self) -> dict[str, Any]:
        """Routing on, in the order the spec demands: CA trusted, previous decision known, PAC
        published, front end listening, THEN the rule."""
        if self.enabled:
            return {"enabled": True, "reason": ""}
        problem = self.preflight()
        if problem:
            return self._refuse(problem)
        if not getattr(self.frontend, "running", False):
            return self._refuse("frontend_not_running")
        try:
            saved = self.manager.backup()
            current = saved or self.manager.current()
            self.wpad_result = wpad_result(current, self.wpad)
            direct = self.autodetect_is_direct or self.wpad_result == "none"
            previous = prepare_previous(
                current, self.home, fetch=self.fetch, autodetect_is_direct=direct
            )
        except RoutingRefused as exc:
            suffix = (
                "_unknown" if str(exc) == "autodetect" and self.wpad_result == WPAD_UNKNOWN else ""
            )
            return self._refuse(f"refused_{exc}{suffix}")
        except ProxyError as exc:
            return self._refuse(f"proxy_{exc}")
        self.upstream.set_previous(previous)  # the routed hosts keep the company route
        provider = PacProvider(
            previous, int(self.frontend.port), self.state.any_masked, INTERCEPTED_DOMAINS
        )
        self.state.pac_provider = provider.text
        try:
            self.manager.enable(self.pac_url, disable_autodetect=direct)
        except ProxyError as exc:
            self.state.pac_provider = None
            return self._refuse(f"proxy_{exc}")
        self.enabled = True
        self.reason = ""
        return {"enabled": True, "reason": ""}

    def disable(self) -> dict[str, Any]:
        """Take the rule out and restore the user's settings. Safe to call twice (and when routing
        was never on): a missing backup means nothing of ours is set."""
        self.state.pac_provider = None
        was = self.enabled
        self.enabled = False
        self.reason = "disabled" if was else self.reason
        try:
            return {"restored": bool(self.manager.disable().get("restored")), "was_enabled": was}
        except ProxyError as exc:
            self.reason = f"restore_{exc}"
            return {"restored": False, "was_enabled": was, "error": str(exc)}

    def status(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "wanted": True,
            "enabled": self.enabled,
            "reason": self.reason,
            "pac_url": self.pac_url,
            "backup": self.manager.backup_exists(),
            "upstream": self.upstream.mode,
        }
        if self.wpad_result:
            out["wpad"] = self.wpad_result  # found | none | unknown (only when auto-detect was on)
        return out


def cleanup_stale_rule(
    home: Path,
    settings: InternetSettingsBackend,
    probe: Callable[[int], bool],
) -> dict[str, Any]:
    """A crash leftover: ``AutoConfigURL`` points at a PAC of ours (``http://127.0.0.1:<port>/proxy.pac``)
    but nothing answers on that port. The backup is restored (the user's own PAC, proxy or nothing).
    A rule that is not ours, or whose gateway answers, is never touched."""
    manager = InternetSettings(home, settings)
    try:
        url = settings.read().auto_config_url or ""
    except ProxyError:
        return {"stale": False}
    match = _OUR_PAC.match(url)
    if match is None:
        return {"stale": False}
    if probe(int(match.group(1))):
        return {"stale": False, "answering": True}
    if not manager.backup_exists():
        return {"stale": True, "restored": False, "reason": "no_backup"}
    try:
        result = manager.disable()
    except ProxyError as exc:
        return {"stale": True, "restored": False, "reason": str(exc)}
    return {"stale": True, **result}


class InactiveRouting:
    """Routing that was asked for but cannot exist here (a fixed ``reason``): same surface as
    ``RoutingControl``, never touches anything."""

    enabled = False

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def enable(self) -> dict[str, Any]:
        return {"enabled": False, "reason": self.reason}

    def disable(self) -> dict[str, Any]:
        return {"restored": False, "was_enabled": False}

    def status(self) -> dict[str, Any]:
        return {"wanted": True, "enabled": False, "reason": self.reason}


# ------------------------------------------------------------------------------------ lifecycle
class Lifecycle:
    """The attached MCP processes, the watchdog and the shutdown of a routed gateway."""

    def __init__(
        self,
        state: Any,
        *,
        routing: RoutingControl | None = None,
        on_exit: Callable[[], None] | None = None,
        probe: ProcessProbe | None = None,
        clock: Callable[[], float] = time.monotonic,
        grace_s: float = ATTACH_GRACE_S,
        interval_s: float = WATCHDOG_S,
        ready: Callable[[], bool] | None = None,
    ) -> None:
        self.state = state
        self.routing = routing
        self.on_exit = on_exit
        self.probe: ProcessProbe = probe or PsutilProbe()
        self.clock = clock
        self.grace_s = grace_s
        self.interval_s = interval_s
        self._ready = ready  # the PAC URL answers (the gateway serves): routing may be enabled
        self._lock = threading.RLock()
        self._pids: dict[int, float] = {}
        self._ever_attached = False
        self._started = clock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._shut = False
        self._dormant = False  # routing ended (no MCP left); the next attach routes again
        self.exit_reason = ""

    # -------------------------------------------------------------- attach / detach
    def attach(self, pid: Any) -> dict[str, Any]:
        """Register a Power BI MCP process. Only a positive integer of a process that exists."""
        if not isinstance(pid, int) or isinstance(pid, bool) or not 0 < pid < 2**31:
            return {"ok": False, "error": "bad_pid"}
        identity = self.probe.identify(pid)
        if identity is None:
            return {"ok": False, "error": "not_running"}
        with self._lock:
            self._pids[pid] = identity
            self._ever_attached = True
            wake = self._dormant and not self._shut
            self._dormant = False
            count = len(self._pids)
        if wake and self.routing is not None:
            # routing ended earlier because no MCP was left; an MCP is back: route again
            self.exit_reason = ""
            try:
                self.routing.enable()
            except Exception:  # noqa: BLE001 - the gateway keeps running without routing
                self.routing.reason = "enable_failed"
        return {"ok": True, "attached": count}

    def detach(self, pid: Any) -> dict[str, Any]:
        if not isinstance(pid, int) or isinstance(pid, bool) or not 0 < pid < 2**31:
            return {"ok": False, "error": "bad_pid"}
        with self._lock:
            self._pids.pop(pid, None)
            return {"ok": True, "attached": len(self._pids)}

    def attached(self) -> int:
        with self._lock:
            return len(self._pids)

    # -------------------------------------------------------------- watchdog
    def tick(self) -> str:
        """One watchdog step: ``ok`` (an attached process lives), ``grace`` (nothing attached yet,
        within the start-up grace) or ``exit`` (nothing alive: shut everything down)."""
        with self._lock:
            for pid, identity in list(self._pids.items()):
                if not self.probe.alive(pid, identity):
                    del self._pids[pid]
            if self._pids:
                return "ok"
            if not self._ever_attached and self.clock() - self._started < self.grace_s:
                return "grace"
            if self._dormant:
                return "idle"  # routing already ended; waiting for an MCP to attach again
            self._dormant = True
            self.exit_reason = "no_mcp" if self._ever_attached else "never_attached"
        # the desktop-app ROUTING ends (the proxy settings are restored); the gateway process and
        # this watchdog keep running: Claude Code talks to the AI through the gateway, so stopping
        # it made every Claude Code request fail with ECONNREFUSED (owner rule 2026-10-07)
        self._end_routing()
        if self.on_exit is not None:
            self.on_exit()
        return "exit"

    def start(self) -> None:
        """Enable routing (once the gateway answers) and run the watchdog in a daemon thread."""
        self._thread = threading.Thread(target=self._run, name="gateway-lifecycle", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        if self.routing is not None:
            deadline = self.clock() + START_WAIT_S
            while not self._stop.is_set() and self.clock() < deadline:
                if self._ready is None or self._ready():
                    break
                self._stop.wait(0.2)
            if not self._stop.is_set():
                try:
                    self.routing.enable()
                except Exception:  # noqa: BLE001 - the gateway keeps running without routing
                    self.routing.reason = "enable_failed"
        while not self._stop.wait(self.interval_s):
            try:
                self.tick()  # never returns: routing goes dormant and comes back on an attach
            except Exception:  # noqa: BLE001 - the watchdog must not die silently
                logger.warning("gateway watchdog step failed")

    def _end_routing(self) -> None:
        """Remove the proxy rule (restore the backup) without stopping anything else."""
        if self.routing is not None:
            try:
                self.routing.disable()
            except Exception:  # noqa: BLE001 - the next start cleans a stale rule
                logger.warning("restoring the proxy settings failed")

    def shutdown(self) -> None:
        """Remove the proxy rule (restore the backup) and stop the watchdog. Idempotent; called by
        the watchdog, by the gateway's own shutdown and at process exit."""
        with self._lock:
            first = not self._shut
            self._shut = True
        self._stop.set()
        if first and self.routing is not None:
            try:
                self.routing.disable()
            except Exception:  # noqa: BLE001 - the next start cleans a stale rule
                logger.warning("restoring the proxy settings failed")

    def status(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "attached": self.attached(),
            "grace_s": self.grace_s,
            "exit_reason": self.exit_reason,
        }
        if self.routing is not None:
            out["routing"] = self.routing.status()
        return out
