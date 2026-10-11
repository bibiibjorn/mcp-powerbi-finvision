"""Masking lab: run a LAB gateway for live checks without touching the real one.

    python tools/masking_lab/lab.py start  --dir <lab dir> [--port 18890] [--frontend-port 18899]
    python tools/masking_lab/lab.py stop   --dir <lab dir> [--port N]
    python tools/masking_lab/lab.py status --dir <lab dir> [--port N]
    python tools/masking_lab/lab.py selftest

``start`` creates ``<lab dir>/home``, seeds ``client_names.txt`` there from
``tests/fixtures/masking/synthetic_clients.txt`` (invented names only) and starts
``python -m client_gateway serve --no-provider`` as a child process. ``--no-provider`` is a hard
rule (R8): the lab gateway never queries the Power BI Desktop files the owner has open. The child
runs with the lab home and port in ITS environment only, autostart off and name recognition off;
this process does not change its own environment.

The lab refuses (exit 2, nothing created): port 8890 or the configured production port
(``client_gateway.port``), a home that is, contains or sits inside the REAL masking home
(``%LOCALAPPDATA%\\FinvisionMasking``, computed here, never read from
``FINVISION_MASKING_HOME``), and any home outside ``--dir``. It never writes Claude settings, the
registry or the Windows certificate store, and never reads or writes the real gateway or the real
masking home.

``--frontend-port N`` (phase 3a) also runs the TLS front end of the gateway on 127.0.0.1:N, with a
CA that lives in ``<lab dir>/home/frontend`` and is trusted by NOTHING except a test window started
with ``--ignore-certificate-errors-spki-list=<spki>``; it is refused for the same ports as the lab
port and when it equals the lab port. ``status`` prints the front end block of the gateway status,
including that SPKI hash (a public value).

``selftest`` (phase 3c, rule R9) is offline: it starts no gateway and opens no port. It replays every
recorded wire fixture of every named app (``tests/fixtures/masking/wire``) through the current code
and prints one line per app (masked out / rehydrated back / leak scan); exit 0 = every app passes
(``tools/masking_lab/selftest.py``).

``stop`` posts ``/__gateway/shutdown`` with the LAB home's token; ``status`` prints the lab
gateway's health and its token-authenticated status (counts only). Exit codes: 0 ok, 1 failed or
not running, 2 refused.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "masking" / "synthetic_clients.txt"
PRODUCTION_PORT = 8890
DEFAULT_LAB_PORT = 18890
START_WAIT_S = 30.0
STOP_WAIT_S = 8.0
TOKEN_FILE = "gateway.token"
TOKEN_HEADER = "X-Gateway-Token"
LAB_FILE = "lab.json"
LOG_FILE = "lab.log"
CLIENT_LIST = "client_names.txt"


class LabRefusal(Exception):
    """The lab will not run with these arguments (the message names the rule)."""


# ------------------------------------------------------------------------------------ safety
def real_masking_home() -> Path:
    """The REAL masking home. Computed from ``LOCALAPPDATA`` only: the ``FINVISION_MASKING_HOME``
    override is deliberately ignored, so the lab can never be tricked into the owner's data."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "FinvisionMasking"


def configured_port() -> int:
    """The configured production port (``client_gateway.port``, default 8890)."""
    try:
        from core.config.config_manager import config

        return int(config.get("client_gateway.port", PRODUCTION_PORT))
    except Exception:  # noqa: BLE001 - config not loadable: the default
        return PRODUCTION_PORT


def _norm(path: Path) -> Path:
    return Path(os.path.normcase(str(path.resolve())))


def _inside(child: Path, parent: Path) -> bool:
    """True when ``child`` is ``parent`` or below it (resolved, case-insensitive on Windows)."""
    c, p = _norm(child), _norm(parent)
    return c == p or p in c.parents


def validate(lab_dir: Path, port: int, home: Path | None = None) -> Path:
    """The lab home for ``lab_dir`` and ``port``; ``LabRefusal`` when a rule is broken."""
    if not 0 < port < 65536:
        raise LabRefusal(f"port {port} is not a valid TCP port")
    if port == PRODUCTION_PORT:
        raise LabRefusal(f"port {PRODUCTION_PORT} is the production gateway port")
    configured = configured_port()
    if port == configured:
        raise LabRefusal(f"port {port} is the configured production port (client_gateway.port)")
    real = real_masking_home()
    if _inside(lab_dir, real):
        raise LabRefusal("the lab dir is the real masking home or inside it")
    lab_home = home if home is not None else lab_dir / "home"
    if _inside(lab_home, real) or _inside(real, lab_home):
        raise LabRefusal("the lab home is, contains or sits inside the real masking home")
    if _norm(lab_dir) not in _norm(lab_home).parents:
        raise LabRefusal("the lab home is outside the lab dir")
    return lab_home.resolve()


def validate_frontend_port(frontend_port: int, port: int) -> None:
    """``LabRefusal`` when the front-end port breaks a lab rule: not a TCP port, the production
    port, the configured production port, or the lab gateway port itself."""
    if not 0 < frontend_port < 65536:
        raise LabRefusal(f"front-end port {frontend_port} is not a valid TCP port")
    if frontend_port == PRODUCTION_PORT:
        raise LabRefusal(f"front-end port {PRODUCTION_PORT} is the production gateway port")
    if frontend_port == configured_port():
        raise LabRefusal(
            f"front-end port {frontend_port} is the configured production port (client_gateway.port)"
        )
    if frontend_port == port:
        raise LabRefusal("the front-end port must differ from the lab gateway port")


# ------------------------------------------------------------------------------------ seams
def http_call(
    port: int, method: str, path: str, token: str | None = None, timeout: float = 3.0
) -> tuple[int, dict[str, Any]] | None:
    """``(status, json)`` from the lab gateway on 127.0.0.1:``port``; None when not reachable."""
    headers = {"Content-Type": "application/json"} if method == "POST" else {}
    if token:
        headers[TOKEN_HEADER] = token
    data = b"{}" if method == "POST" else None
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, method=method, headers=headers
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


def windowless_python() -> str:
    """``pythonw.exe`` next to this interpreter on Windows (no console window, R10), else it."""
    if os.name == "nt":
        candidate = Path(sys.executable).with_name("pythonw.exe")
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def spawn_child(argv: list[str], env: dict[str, str], cwd: Path, log_path: Path) -> int:
    """Start the lab gateway detached and hidden; its output goes to ``log_path``. Returns the pid."""
    flags = 0
    if os.name == "nt":
        flags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    with open(log_path, "ab") as log:
        proc = subprocess.Popen(  # noqa: S603 - fixed argv
            argv,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=flags,
            close_fds=True,
            start_new_session=os.name != "nt",
        )
    return proc.pid


# ------------------------------------------------------------------------------------ helpers
def child_env(home: Path, port: int, base: dict[str, str] | None = None) -> dict[str, str]:
    """The environment of the lab gateway (a copy: this process's own environment is untouched)."""
    env = dict(os.environ if base is None else base)
    env.update(
        {
            "FINVISION_MASKING_HOME": str(home),
            "FINVISION_GATEWAY_PORT": str(port),
            "FINVISION_GATEWAY_AUTOSTART": "0",
            "FINVISION_GATEWAY_NER": "0",
            "FINVISION_GATEWAY_PROVIDER": "0",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    return env


def seed_home(home: Path) -> None:
    """Create the lab home and seed the synthetic client list (an edited list is kept)."""
    home.mkdir(parents=True, exist_ok=True)
    target = home / CLIENT_LIST
    if not target.exists():
        shutil.copyfile(FIXTURE, target)


def _read_lab_info(lab_dir: Path) -> dict[str, Any]:
    try:
        info = json.loads((lab_dir / LAB_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return info if isinstance(info, dict) else {}


def _port_for(lab_dir: Path, arg: int | None) -> int:
    if arg is not None:
        return arg
    recorded = _read_lab_info(lab_dir).get("port")
    if isinstance(recorded, int) and not isinstance(recorded, bool):
        return recorded
    return DEFAULT_LAB_PORT


def _health(port: int) -> dict[str, Any] | None:
    reply = http_call(port, "GET", "/__gateway/health", timeout=1.0)
    return reply[1] if reply is not None and reply[0] == 200 and reply[1].get("ok") else None


def _lab_token(home: Path) -> str | None:
    try:
        token = (home / TOKEN_FILE).read_text(encoding="ascii").strip()
    except OSError:
        return None
    return token or None


def _refuse(exc: LabRefusal) -> int:
    print(f"refused: {exc}", file=sys.stderr)
    return 2


# ------------------------------------------------------------------------------------ commands
def start(lab_dir: Path, port: int, frontend_port: int | None = None) -> int:
    try:
        home = validate(lab_dir, port)
        if frontend_port is not None:
            validate_frontend_port(frontend_port, port)
    except LabRefusal as exc:
        return _refuse(exc)
    if _health(port) is not None:
        print(f"lab gateway already running on port {port}")
        return 0
    lab_dir = lab_dir.resolve()
    seed_home(home)
    # pythonw: a console python.exe started DETACHED makes the venv launcher's child open a new
    # console window (R10); the windowless interpreter never does
    argv = [
        windowless_python(),
        "-m",
        "client_gateway",
        "serve",
        "--no-provider",
        "--port",
        str(port),
    ]
    if frontend_port is not None:
        argv += ["--frontend-port", str(frontend_port)]
    pid = spawn_child(argv, child_env(home, port), REPO_ROOT, lab_dir / LOG_FILE)
    record: dict[str, Any] = {"port": port, "home": str(home), "pid": pid}
    if frontend_port is not None:
        record["frontend_port"] = frontend_port
    (lab_dir / LAB_FILE).write_text(json.dumps(record), encoding="utf-8")
    deadline = time.monotonic() + max(0.0, START_WAIT_S)
    while True:
        info = _health(port)
        if info is not None:
            print(
                f"lab gateway running on port {port}, home {home} (version {info.get('version')})"
            )
            return 0
        if time.monotonic() >= deadline:
            print(f"lab gateway did not answer on port {port} within {START_WAIT_S:.0f} s")
            return 1
        time.sleep(0.25)


def stop(lab_dir: Path, port: int) -> int:
    try:
        home = validate(lab_dir, port)
    except LabRefusal as exc:
        return _refuse(exc)
    if _health(port) is None:
        print(f"lab gateway is not running on port {port}")
        return 0
    token = _lab_token(home)
    if token is None:
        print("no control token in the lab home: not stopping (nothing was sent)")
        return 1
    http_call(port, "POST", "/__gateway/shutdown", token=token)
    deadline = time.monotonic() + STOP_WAIT_S
    while _health(port) is not None and time.monotonic() < deadline:
        time.sleep(0.2)
    gone = _health(port) is None
    print("lab gateway stopped" if gone else "lab gateway did not stop")
    return 0 if gone else 1


def status(lab_dir: Path, port: int) -> int:
    try:
        home = validate(lab_dir, port)
    except LabRefusal as exc:
        return _refuse(exc)
    info = _health(port)
    if info is None:
        print(f"lab gateway is not running on port {port}")
        return 1
    reply = http_call(port, "GET", "/__gateway/status", token=_lab_token(home))
    full = reply[1] if reply is not None and reply[0] == 200 else None
    frontend = full.get("frontend") if isinstance(full, dict) else None
    out: dict[str, Any] = {"port": port, "home": str(home), "health": info, "status": full}
    if isinstance(frontend, dict) and frontend.get("enabled"):
        out["frontend"] = {
            "port": frontend.get("port"),
            "spki_sha256_b64": frontend.get("spki_sha256_b64"),
            "running": frontend.get("running"),
        }
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


# ------------------------------------------------------------------------------------ main
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lab", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("selftest", help="offline replay of every recorded fixture of every named app")
    for name in ("start", "stop", "status"):
        p = sub.add_parser(name)
        p.add_argument(
            "--dir", required=True, help="lab directory (holds home/, lab.log, lab.json)"
        )
        if name == "start":
            p.add_argument(
                "--frontend-port",
                type=int,
                default=None,
                help="also run the TLS front end on this loopback port (default: none)",
            )
        p.add_argument(
            "--port",
            type=int,
            default=None,
            help=f"lab port (start: default {DEFAULT_LAB_PORT}; stop/status: the recorded one)",
        )
    args = parser.parse_args(argv)
    if args.command == "selftest":
        from tools.masking_lab.selftest import run_selftest

        return run_selftest()
    lab_dir = Path(args.dir)
    if args.command == "start":
        return start(
            lab_dir,
            args.port if args.port is not None else DEFAULT_LAB_PORT,
            args.frontend_port,
        )
    port = _port_for(lab_dir, args.port)
    return stop(lab_dir, port) if args.command == "stop" else status(lab_dir, port)


if __name__ == "__main__":
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))  # core.config for the configured production port
    raise SystemExit(main())
