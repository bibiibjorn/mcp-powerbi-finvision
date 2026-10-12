"""Command line of the gateway: ``python -m client_gateway <command>``.

* ``serve``   run in the foreground (what ``start`` launches detached); ``--frontend-port N`` also
              runs the TLS front end of the masking gateway on 127.0.0.1:N (lab only; off by default);
* ``start``   make sure ONE gateway of THIS version runs: idempotent, replaces an older version,
              launches ``pythonw`` detached and hidden, waits up to 10 s for ``/__gateway/health``;
* ``stop``    shut the gateway down (control token) and wait for it to be gone;
* ``status``  print the gateway status (counts only); exit 1 when it is not running;
* ``mode on|off [--session ID] [--all]``  switch masking of one session (default: the session in
              ``CLAUDE_CODE_SESSION_ID``). ``off`` sends the control token. Switching EVERY
              session off needs ``--all`` (an empty session id is refused: an unset environment
              variable must not do it by accident);
* ``setup [--yes] [--remove] [--status] [--check] [--register] [--desktop-apps] [--managed-ok]``  the one
              human-run command that installs / shows / removes the masking pieces: by default
              Claude Code masking on demand (config flags + the masking hooks), with
              ``--desktop-apps`` also the routed pieces for Copilot / ChatGPT (CA, proxy checks):
              a PLAN by default, nothing changes without ``--yes`` (``client_gateway.setup_cmd``).

Port: ``--port``, else env ``FINVISION_GATEWAY_PORT``, else config ``client_gateway.port``, else
8890. The singleton is the exclusive bind of that port; ``<home>/gateway.lock`` (pid, port,
version) is informational. No hot reload: a new version means ``start`` replaces the process.
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from client_gateway import __version__
from client_gateway.defaults import DEFAULT_PORT, PORT_ENV
from client_gateway.state import TOKEN_HEADER, GatewayState, cfg, masking_home, read_token

TOKEN_PATHS = ("/__gateway/status", "/__gateway/session", "/__gateway/refresh")
LOCK_FILE = "gateway.lock"
PROVIDER_ENV = "FINVISION_GATEWAY_PROVIDER"
UPSTREAM_ENV = "FINVISION_GATEWAY_UPSTREAM"
START_WAIT_S = 90.0  # a large remembered dictionary takes about a minute to load
STARTING_GRACE_S = 120.0  # a gateway this young that does not answer yet is starting, not hung
STOP_WAIT_S = 8.0
KILL_WAIT_S = 3.0
EXIT_GRACE_S = 5.0  # a stopped ``serve`` process exits by then, even if a thread hangs
_REPO_ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------------------------ helpers
def rotate_key_command(port: int) -> int:
    """``rotate-key``: a new pseudonym key of the next generation (``core.client_guard.tokens``).
    Refused while a gateway runs: its dictionary holds the codes of the current key."""
    from core.client_guard.tokens import rotate_key  # noqa: PLC0415

    if health(port) is not None:
        _out("a gateway is running: type masking off in the chats that have it on (or run stop),")
        _out("then run rotate-key again")
        return 1
    generation = rotate_key(masking_home())
    _out(f"new pseudonym key (generation {generation}): every client gets a new code from the")
    _out(
        "next masking on. Codes in older chats stay as codes (they are never read as another client)."
    )
    return 0


def resolve_port(arg: int | None = None) -> int:
    if arg:
        return int(arg)
    raw = os.environ.get(PORT_ENV, "").strip()
    if raw.isdigit() and 0 < int(raw) < 65536:
        return int(raw)
    try:
        value = int(cfg("client_gateway.port", DEFAULT_PORT))
    except (TypeError, ValueError):
        return DEFAULT_PORT
    return value if 0 < value < 65536 else DEFAULT_PORT


def call(
    port: int,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 3.0,
) -> tuple[int, dict[str, Any]] | None:
    """``(status, json)`` from the gateway on ``port``, or None when it cannot be reached."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    hdrs = {"Content-Type": "application/json"} if data is not None else {}
    if path.startswith(TOKEN_PATHS):  # status / session / refresh need the control token
        token = read_token(masking_home())
        if token:
            hdrs[TOKEN_HEADER] = token
    hdrs.update(headers or {})
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, method=method, headers=hdrs
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - loopback
            raw, code = resp.read(), resp.status
    except urllib.error.HTTPError as exc:
        raw, code = exc.read(), exc.code
    except Exception:  # noqa: BLE001 - refused, timeout, reset: not reachable
        return None
    try:
        parsed = json.loads(raw or b"{}")
    except ValueError:
        parsed = {}
    return code, parsed if isinstance(parsed, dict) else {}


def health(port: int, timeout: float = 1.0) -> dict[str, Any] | None:
    reply = call(port, "GET", "/__gateway/health", timeout=timeout)
    return reply[1] if reply is not None and reply[0] == 200 and reply[1].get("ok") else None


def _lock_path(home: Path) -> Path:
    return home / LOCK_FILE


def _write_lock(home: Path, port: int) -> None:
    try:
        home.mkdir(parents=True, exist_ok=True)
        _lock_path(home).write_text(
            json.dumps({"pid": os.getpid(), "port": port, "version": __version__}),
            encoding="ascii",
        )
    except OSError:
        pass


def _read_lock(home: Path) -> dict[str, Any] | None:
    try:
        info = json.loads(_lock_path(home).read_text(encoding="ascii"))
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) else None


def _remove_lock(home: Path) -> None:
    path = _lock_path(home)
    try:
        info = json.loads(path.read_text(encoding="ascii"))
        if info.get("pid") == os.getpid():
            path.unlink()
    except (OSError, ValueError):
        pass


def _out(text: str) -> None:
    sys.stdout.write(text + "\n")


# ------------------------------------------------------------------------------------ serve
def _lower_priority() -> None:
    """Below-normal process priority: OCR and dictionary reads never slow down the user's work."""
    try:
        import psutil

        proc = psutil.Process()
        if os.name == "nt":
            proc.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        else:
            proc.nice(10)
    except Exception:  # noqa: BLE001 - best effort
        pass


def routing_wanted() -> bool:
    """Config ``client_gateway.routing`` (default off): the owner approved routing the desktop apps
    through this gateway (setup turns it on)."""
    return str(cfg("client_gateway.routing", False)).strip().lower() in ("1", "true", "on", "yes")


def _start_routing(
    state: GatewayState, home: Path, port: int, log: Any, backends: Any
) -> tuple[Any, Any]:
    """Routing on: ``(RoutingControl or InactiveRouting, FrontEnd or None)``. Cleans a crash
    leftover first. The front end starts only when the CA is trusted; nothing is routed until the
    lifecycle enables the rule (after the gateway answers). Never raises."""
    from client_gateway import lifecycle  # noqa: PLC0415
    from client_gateway.frontend.runner import FrontEnd, FrontEndError  # noqa: PLC0415
    from client_gateway.routing import guard  # noqa: PLC0415
    from client_gateway.routing.upstream import build_upstream_bundle  # noqa: PLC0415

    try:
        if backends is None:
            from client_gateway.routing.backends import real_backends  # noqa: PLC0415

            backends = real_backends()
    except guard.RealSystemForbidden:
        log.event("routing_refused")
        return lifecycle.InactiveRouting("real_system_forbidden"), None
    certs, settings = backends.certs, backends.settings
    fport = int(cfg("client_gateway.frontend_port", 8891))
    if fport == port:
        return lifecycle.InactiveRouting("frontend_port_equals_gateway_port"), None
    stale = lifecycle.cleanup_stale_rule(home, settings, lambda p: health(p) is not None)
    if stale.get("stale"):
        log.event("routing_stale_rule", restored=bool(stale.get("restored")))
    control = lifecycle.RoutingControl(
        state,
        home,
        port,
        None,
        certs=certs,
        settings=settings,
        crypto=backends.crypto,
        wpad=getattr(backends, "wpad", None),
    )
    reason = control.preflight()
    if reason:
        control.reason = reason
        return control, None
    ca = control.ca_store.load()
    assert ca is not None
    try:
        bundle = build_upstream_bundle(home, exclude_thumbprints=[ca.thumbprint])
        frontend = FrontEnd(
            state,
            fport,
            log=log,
            cert_store=lambda: control.ca_store.mitm_store(ca),
            trust_bundle=bundle.path,
            upstream_proxy=control.upstream.via,
        )
        frontend.start()
    except (FrontEndError, OSError) as exc:
        control.reason = f"frontend_{exc}"
        log.event("frontend_refused")
        return control, None
    control.frontend = frontend
    return control, frontend


def serve(
    port: int,
    upstream: str | None = None,
    provider: bool | None = None,
    frontend_port: int | None = None,
    routing: bool | None = None,
    backends: Any = None,
) -> int:
    """Run the gateway in the foreground. Returns 2 when the port is already taken.

    ``frontend_port`` starts the TLS front end (``client_gateway.frontend``, lab only in phase 3a)
    in this process on 127.0.0.1:<port>; without it no front end exists. It must differ from the
    gateway port. A front end that cannot start refuses the whole start (exit 2)."""
    import logging

    from client_gateway.log import GatewayLog
    from client_gateway.provider import DesktopBackend, Provider
    from client_gateway.server import DEFAULT_UPSTREAM, Gateway, upstream_allowed

    for name in ("stdout", "stderr"):  # pythonw has no console: libraries must not crash on it
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w"))  # noqa: SIM115
    logging.getLogger().addHandler(logging.NullHandler())
    _lower_priority()
    home = masking_home()
    state = GatewayState(home=home)
    if state.detectors is not None:
        state.detectors.start(state.smap)  # remembered values + client list now
        if state.global_mode == "on" or state.has_saved_on():
            # masking on by default, or a chat that was masked before this restart: the worker
            # (its own low-priority process) starts now in the background. When masking is off
            # by default and no chat is remembered as masked nothing is loaded here: the worker
            # starts when a chat switches masking on, or when the safety net first sees prose
            state.detectors.masking_on()
    log = GatewayLog(home)
    if provider is None:
        env = os.environ.get(PROVIDER_ENV, "").strip().lower()
        provider = env not in ("0", "false", "off") and bool(cfg("client_gateway.provider", True))
    prov = Provider(state, DesktopBackend(), log) if provider else None
    target = upstream or os.environ.get(UPSTREAM_ENV) or cfg("client_gateway.upstream", "")
    if not upstream_allowed(str(target or DEFAULT_UPSTREAM)):
        _out("refused: the upstream must be an https URL (plain http only to this machine)")
        log.event("upstream_refused")
        log.close()
        return 2
    frontend: Any = None
    control: Any = None
    life: Any = None
    if routing is None:
        routing = routing_wanted()
    if routing and frontend_port is None:
        control, frontend = _start_routing(state, home, port, log, backends)
    elif frontend_port is not None:
        if frontend_port == port:
            _out("refused: the front-end port must differ from the gateway port")
            log.close()
            return 2
        from client_gateway.frontend.runner import FrontEnd, FrontEndError  # noqa: PLC0415

        try:
            frontend = FrontEnd(state, frontend_port, log=log)
            frontend.start()
        except FrontEndError as exc:
            _out(f"refused: the front end could not start ({exc})")
            log.event("frontend_refused")
            log.close()
            return 2

    def _on_shutdown() -> None:
        if life is not None:
            life.shutdown()  # the proxy rule goes first: the user's settings are restored
        if frontend is not None:
            frontend.stop()
        _remove_lock(home)

    try:
        gateway = Gateway(
            state,
            port=port,
            upstream=str(target or DEFAULT_UPSTREAM),
            log=log,
            provider=prov,
            on_shutdown=_on_shutdown,
        )
    except OSError:
        log.event("bind_failed", port=port)
        if frontend is not None:
            frontend.stop()
        log.close()
        return 2
    _write_lock(home, gateway.port)
    atexit.register(_remove_lock, home)
    from client_gateway import reaper  # noqa: PLC0415

    if control is None and reaper.wanted(cfg):
        # masking on demand: the gateway stops by itself once nothing is routed to it and no
        # Power BI MCP without a Claude Code chat (VS Code Copilot) switched masking on
        from client_gateway import keep  # noqa: PLC0415
        from client_gateway.folders import anything_routed  # noqa: PLC0415

        reaper.Reaper(gateway, lambda: anything_routed(home) or keep.any_alive(home)).start()
    if control is not None:
        from client_gateway.lifecycle import Lifecycle  # noqa: PLC0415

        def _routing_ended() -> None:
            # no MCP attached any more: the lifecycle already restored the proxy settings (the
            # desktop apps go direct). Nothing else stops: the GATEWAY KEEPS RUNNING (Claude Code
            # talks to the AI through it - stopping it made every Claude Code request fail with
            # ECONNREFUSED, owner rule 2026-10-07) and the front end stays up idle, so routing
            # comes back as soon as a Power BI MCP attaches again
            log.event("routing_dormant")

        life = Lifecycle(
            state,
            routing=control,
            on_exit=_routing_ended,
            ready=lambda: health(gateway.port) is not None,
        )
        gateway.lifecycle = life
        state.routing_status = lambda: {
            **control.status(),
            "attached": life.attached(),
            "exit_reason": life.exit_reason,
        }
        atexit.register(life.shutdown)
        life.start()
    try:
        gateway.serve_forever()
    except KeyboardInterrupt:
        gateway.shutdown()
    return 0


# ------------------------------------------------------------------------------------ start/stop
def _python_exe() -> str:
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    return str(pyw) if os.name == "nt" and pyw.is_file() else str(exe)


def spawn(port: int) -> None:
    """Launch ``serve`` detached (no console window, no inherited handles). A gateway that routes
    the desktop apps (``client_gateway.routing`` on, the owner's approval) gets the permission to
    touch the system in ITS environment only (``routing.guard``)."""
    from client_gateway.routing.guard import ENV as REAL_SYSTEM_ENV  # noqa: PLC0415

    env = dict(os.environ)
    if routing_wanted():
        env[REAL_SYSTEM_ENV] = "1"
    flags = 0
    if os.name == "nt":
        flags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    subprocess.Popen(  # noqa: S603 - fixed argv
        [_python_exe(), "-m", "client_gateway", "serve", "--port", str(port)],
        cwd=str(_REPO_ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=True,
        start_new_session=os.name != "nt",
        env=env,
    )


def start(port: int, wait_s: float = START_WAIT_S, replace: bool = False) -> int:
    """Make sure a gateway runs. A RUNNING gateway is never stopped or replaced unless
    ``replace`` (``start --replace``, the user in a terminal): every routed chat goes through it,
    and replacing it with code that was not soaked on an isolated port took every chat down on
    2026-10-02. Test new code with FINVISION_MASKING_HOME=<temp dir> and ``serve --port 8891``."""
    lock = _read_lock(masking_home())
    other = lock.get("port") if lock else None
    if isinstance(other, int) and other != port and health(other) is not None:
        if not replace:
            _out(
                f"a client gateway already runs on port {other}; not touching it "
                "(start --replace to move it)"
            )
            return 0
        _out(f"stopping the client gateway on port {other} (one gateway per machine)")
        _carry_modes(other)
        if stop(other) != 0:
            return 1
    info = health(port)
    if info is not None:
        if info.get("version") == __version__ and not replace:
            _out(f"client gateway already running on port {port} (version {__version__})")
            return 0
        if not replace:
            _out(
                f"client gateway version {info.get('version')} is running on port {port}; "
                f"this code is {__version__}. Not replacing it (start --replace, by the user, "
                "when no chat is busy)"
            )
            return 0
        _out(f"replacing client gateway version {info.get('version')} by {__version__}")
        _carry_modes(port)
        if stop(port) != 0:
            return 1
    elif (pid := _gateway_pid(masking_home(), port, None)) is not None:
        # a gateway process holds the port but does not answer: still starting, or hanging
        age = _process_age(pid)
        if age is not None and age < STARTING_GRACE_S:
            _out(f"the client gateway on port {port} is still starting (pid {pid}); waiting for it")
            info = _wait_until_answers(port, STARTING_GRACE_S - age + 10.0, pid)
            if info is not None:
                _out(f"client gateway running on port {port} (version {info.get('version')})")
                return 0
        if not replace:
            _out(
                f"a client gateway on port {port} does not answer; "
                "start --replace ends it and starts a new one"
            )
            return 1
        _out(f"the client gateway on port {port} does not answer; ending it")
        if stop(port) != 0:
            return 1
    spawn(port)
    info = _wait_until_answers(port, wait_s)
    if info is not None:
        _out(f"client gateway running on port {port} (version {info.get('version')})")
        return 0
    _out(f"client gateway did not answer on port {port} within {wait_s:.0f} s")
    return 1


def _process_age(pid: int) -> float | None:
    try:
        import psutil

        return max(0.0, time.time() - psutil.Process(pid).create_time())
    except Exception:  # noqa: BLE001 - gone, or not ours to inspect
        return None


def _wait_until_answers(port: int, wait_s: float, pid: int | None = None) -> dict[str, Any] | None:
    """Health of the gateway on ``port`` once it answers, else None after ``wait_s`` (or as soon
    as process ``pid`` is gone). Loading a large remembered dictionary takes a while: say so."""
    start = time.monotonic()
    note_at = start + 5.0
    while time.monotonic() - start < wait_s:
        info = health(port)
        if info is not None:
            return info
        if pid is not None and not _is_gateway_process(pid):
            return None
        if time.monotonic() >= note_at:
            _out(f"still starting ({time.monotonic() - start:.0f} s; loading the dictionary)")
            note_at += 10.0
        time.sleep(0.5)
    return None


def _carry_modes(port: int) -> None:
    """Keep the mode every chat of the old gateway had (a masked chat stays masked)."""
    from client_gateway.state import carry_session_modes  # noqa: PLC0415

    reply = call(port, "GET", "/__gateway/status")
    block = reply[1].get("sessions") if reply is not None and reply[0] == 200 else None
    sessions = block.get("items") if isinstance(block, dict) else None
    if isinstance(sessions, list):
        n = carry_session_modes(masking_home(), sessions)
        _out(f"kept the masking mode of {n} chat(s)")


def _kill(pid: int) -> None:
    try:
        import psutil

        psutil.Process(pid).terminate()
    except Exception:  # noqa: BLE001 - gone already, or not allowed
        pass


def _is_gateway_process(pid: Any) -> bool:
    """True only for a live ``client_gateway`` process other than this one (a pid in a stale
    lock file can belong to any program by now: never wait on or end that one)."""
    if not isinstance(pid, int) or pid <= 0 or pid == os.getpid():
        return False
    try:
        import psutil

        proc = psutil.Process(pid)
        if proc.status() == psutil.STATUS_ZOMBIE:
            return False
        return "client_gateway" in " ".join(proc.cmdline())
    except Exception:  # noqa: BLE001 - gone, or not ours to inspect
        return False


def _gateway_pid(home: Path, port: int, info: dict[str, Any] | None) -> int | None:
    """The process of the gateway on ``port``: from its health reply, else from the lock file
    (a gateway that is hanging in its shutdown no longer answers health, but still holds the
    port - treating that as "gone" started a second gateway that could not bind)."""
    candidates = [info.get("pid") if info else None]
    lock = _read_lock(home)
    if lock and lock.get("port") == port:
        candidates.append(lock.get("pid"))
    for pid in candidates:
        if isinstance(pid, int) and _is_gateway_process(pid):
            return pid
    return None


def _stopped(port: int, pid: int | None) -> bool:
    if pid is not None:
        return not _is_gateway_process(pid)
    return health(port) is None


def stop(port: int, wait_s: float = STOP_WAIT_S) -> int:
    """Stop the gateway on ``port`` and wait until its PROCESS is gone (not merely until it stops
    answering): a gateway that hangs while shutting down is ended, so ``start --replace`` never
    hangs or starts a second gateway next to the old one (2026-10-07)."""
    home = masking_home()
    info = health(port)
    pid = _gateway_pid(home, port, info)
    if info is None and pid is None:
        _remove_stale_lock(home)
        _out("client gateway is not running")
        return 0
    token = read_token(home)
    if info is not None and token:
        call(port, "POST", "/__gateway/shutdown", {}, {TOKEN_HEADER: token}, timeout=2.0)
    _out("waiting for the client gateway to exit" + (f" (pid {pid})" if pid else ""))
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline and not _stopped(port, pid):
        time.sleep(0.2)
    if not _stopped(port, pid) and pid is not None:
        _out("it did not exit in time; ending the process")
        _kill(pid)
        deadline = time.monotonic() + KILL_WAIT_S
        while time.monotonic() < deadline and not _stopped(port, pid):
            time.sleep(0.2)
    gone = _stopped(port, pid)
    if gone:
        _remove_stale_lock(home)
    _out("client gateway stopped" if gone else "client gateway did not stop")
    return 0 if gone else 1


def _remove_stale_lock(home: Path) -> None:
    try:
        _lock_path(home).unlink()
    except OSError:
        pass


# ------------------------------------------------------------------------------------ status/mode
def status(port: int) -> int:
    reply = call(port, "GET", "/__gateway/status")
    if reply is None or reply[0] != 200:
        _out(f"client gateway is not running on port {port}")
        return 1
    _out(json.dumps(reply[1], indent=2, sort_keys=True))
    return 0


def mode(port: int, value: str, session: str | None, allow_global: bool = False) -> int:
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "") if session is None else session
    if value == "off" and not sid.strip() and not allow_global:
        _out(
            "refused: no session id, so this would switch masking off for EVERY session; "
            "pass --session ID, or --all if that is what you want"
        )
        return 2
    headers: dict[str, str] = {}
    token = read_token(masking_home())
    if token:
        headers[TOKEN_HEADER] = token
    reply = call(port, "POST", "/__gateway/mode", {"session": sid, "mode": value}, headers)
    if reply is None:
        _out(f"client gateway is not running on port {port}")
        return 1
    _out(json.dumps(reply[1], sort_keys=True))
    return 0 if reply[0] == 200 else 1


def masking(value: str, folder: str | None, session: str | None) -> int:
    """``masking on|off|status`` from a terminal: the same as typing it in the chat
    (``client_gateway.switch``). ``off`` needs no session: it switches the folder's chats off."""
    from client_gateway import switch  # noqa: PLC0415

    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "") if session is None else session
    where = folder or os.getcwd()
    if value == "on":
        result = switch.masking_on(sid, where)
    elif value == "off":
        result = switch.masking_off(sid, where)
    else:
        result = switch.chat_status(sid, where)
    _out(json.dumps(result, sort_keys=True))
    return 0 if result.get("ok", True) else 1


def routing(command: str) -> int:
    """``route-all`` / ``unroute-all`` on the user settings (new chats pick it up)."""
    from client_gateway import folders

    try:
        result = folders.route_all() if command == "route-all" else folders.unroute_all()
    except (folders.FolderError, OSError) as exc:
        _out(f"{command} failed: {exc}")
        return 1
    _out(json.dumps(result, sort_keys=True))
    _out("Restart Claude Code (or start a new chat) for it to take effect.")
    return 0


# ------------------------------------------------------------------------------------ main
def _exit_after_grace(rc: int) -> None:
    """The gateway has stopped serving and cleaned up; make sure the PROCESS ends too. A thread
    that hangs on the way out (front end, name-recognition worker) kept the old process alive and
    holding the port, which hung ``start --replace``."""
    import threading  # noqa: PLC0415

    timer = threading.Timer(EXIT_GRACE_S, os._exit, (rc,))
    timer.daemon = True
    timer.start()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="client_gateway", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("serve", "start", "stop", "status"):
        p = sub.add_parser(name)
        p.add_argument("--port", type=int, default=None)
        if name == "start":
            p.add_argument(
                "--replace",
                action="store_true",
                help="stop and replace a running gateway (user only, never the AI)",
            )
        if name == "serve":
            p.add_argument("--upstream", default=None)
            p.add_argument("--no-provider", action="store_true")
            p.add_argument(
                "--frontend-port",
                type=int,
                default=None,
                help="also run the TLS front end on this loopback port (lab only; default: none)",
            )
    p = sub.add_parser("mode")
    p.add_argument("value", choices=["on", "off"])
    p.add_argument("--session", default=None, help="session id (default: CLAUDE_CODE_SESSION_ID)")
    p.add_argument("--all", action="store_true", help="every session (needed for a global off)")
    p.add_argument("--port", type=int, default=None)
    p = sub.add_parser(
        "masking",
        help="what the typed 'masking on|off|status' does, from a terminal (masking on demand)",
    )
    p.add_argument("value", choices=["on", "off", "status"])
    p.add_argument("--folder", default=None, help="the chat's project folder (default: here)")
    p.add_argument("--session", default=None, help="session id (default: CLAUDE_CODE_SESSION_ID)")
    sub.add_parser("route-all", help="route every Claude Code chat through the gateway")
    sub.add_parser(
        "unroute-all",
        help="every chat back to Anthropic directly (recovery when the gateway cannot start)",
    )
    sub.add_parser("forget", help="delete the remembered (encrypted) dictionary on this machine")
    sub.add_parser(
        "rotate-key",
        help="new pseudonym key now: every client gets a new code (also automatic, "
        "client_guard.key_rotation_days)",
    )
    p = sub.add_parser(
        "setup",
        help="install / show / remove the routed masking gateway's system pieces (plan by default)",
    )
    p.add_argument("--yes", action="store_true", help="apply (without it: a plan, nothing changes)")
    p.add_argument(
        "--remove", action="store_true", help="undo what setup added (exactly its record)"
    )
    p.add_argument("--status", action="store_true", help="what is installed (read-only)")
    p.add_argument(
        "--check",
        action="store_true",
        help="does masking work now? one mask and one unmask round trip (read-only, starts "
        "nothing): PASS / DEGRADED / FAIL with the reason and the fix",
    )
    p.add_argument(
        "--register",
        action="store_true",
        help="also add the Power BI MCP to Claude Desktop, VS Code and Claude Code (with backups)",
    )
    p.add_argument(
        "--desktop-apps",
        action="store_true",
        help="also route VS Code Copilot and ChatGPT in the browser (managed/proxy checks, a "
        "name-constrained CA; the gateway then runs while the MCP runs)",
    )
    p.add_argument(
        "--managed-ok",
        action="store_true",
        help="with --desktop-apps: continue on a managed PC after IT cleared the certificate",
    )
    args = parser.parse_args(argv)
    if args.command == "setup":
        from client_gateway.setup_cmd import main as setup_main  # noqa: PLC0415

        return setup_main(args)
    if args.command in ("route-all", "unroute-all"):
        return routing(args.command)
    if args.command == "masking":
        return masking(args.value, args.folder, args.session)
    if args.command == "forget":
        from client_gateway.store import ValueStore  # noqa: PLC0415

        removed = ValueStore(masking_home()).forget()
        _out("remembered dictionary deleted" if removed else "no remembered dictionary")
        _out("Restart the gateway (python -m client_gateway start) to clear it from memory too.")
        return 0
    if args.command == "rotate-key":
        return rotate_key_command(resolve_port(None))
    port = resolve_port(args.port)
    if args.command == "serve":
        return serve(port, args.upstream, False if args.no_provider else None, args.frontend_port)
    try:
        if args.command == "start":
            return start(port, replace=args.replace)
        if args.command == "stop":
            return stop(port)
    except KeyboardInterrupt:
        _out("interrupted; run the same command again to finish")
        return 130
    if args.command == "status":
        return status(port)
    return mode(port, args.value, args.session, allow_global=args.all)
