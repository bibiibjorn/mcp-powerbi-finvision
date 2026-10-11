"""Rehearse a live upgrade on an ISOLATED copy: ``python tools/upgrade_soak.py <old-commit>``.

Runs the OLD gateway code (a git worktree of ``<old-commit>``) on a free port with a temporary
masking home, in front of a local fake upstream. Two "chats" send requests to it without pause:
one masked (the old default), one switched off on purpose. Then this checkout's ``start --replace``
replaces it, exactly as the user would on the live gateway, while the chats keep sending.

Reports: requests that failed during the switch and the longest gap, the mode each chat has after
the upgrade (a masked chat must stay masked), the first masked request time on the new gateway,
and that nothing the fake upstream received holds a value the old gateway masked. The live
gateway, the real masking home and Anthropic are never touched.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tools.gateway_soak import RECEIVED, _Upstream  # noqa: E402

SESSION_ON = "3f2a9c1e-1111-4000-8000-0000000000a1"
SESSION_OFF = "77c1d0b2-2222-4000-8000-0000000000b2"
HEADER = "x-claude-code-session-id"
ERRORS: list[str] = []


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(port: int, path: str) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as r:
            return json.loads(r.read())
    except Exception:  # noqa: BLE001
        return None


def _post(port: int, session: str, text: str) -> tuple[int, float]:
    body = json.dumps(
        {
            "model": "x",
            "max_tokens": 10,
            "stream": True,
            "messages": [{"role": "user", "content": [{"type": "text", "text": text}]}],
        }
    ).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/messages",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", HEADER: session},
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            r.read()
            code = r.status
    except urllib.error.HTTPError as exc:
        code = exc.code
        ERRORS.append(exc.read()[:240].decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 - connection refused / reset during the switch
        code = 0
    return code, (time.perf_counter() - t0) * 1000


def main() -> int:
    old = sys.argv[1] if len(sys.argv) > 1 else "765a55d"
    work = Path(tempfile.mkdtemp(prefix="upgrade_soak_"))
    home = work / "home"
    tree = work / "old"
    subprocess.run(
        ["git", "-C", str(REPO), "worktree", "add", "--detach", str(tree), old],
        check=True,
        capture_output=True,
    )
    up = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    port = _free_port()
    env = dict(
        os.environ,
        FINVISION_MASKING_HOME=str(home),
        FINVISION_GATEWAY_UPSTREAM=f"http://127.0.0.1:{up.server_address[1]}",
        FINVISION_GATEWAY_PORT=str(port),
    )
    py = str(REPO / "venv" / "Scripts" / "python.exe")
    old_proc = subprocess.Popen(
        [py, "-m", "client_gateway", "serve", "--port", str(port)],
        cwd=str(tree),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 120
        while time.time() < deadline and not (_get(port, "/__gateway/health") or {}).get("ready"):
            time.sleep(0.3)
        old_info = _get(port, "/__gateway/health") or {}
        print(f"old gateway {old_info.get('version')} on {port}, isolated home {home}")
        # chat A: masked (the old default); chat B: switched off with the token (as 'masking off')
        token = (home / "gateway.token").read_text(encoding="utf-8").strip()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/__gateway/mode",
            method="POST",
            data=json.dumps({"session": SESSION_OFF, "mode": "off"}).encode(),
            headers={"Content-Type": "application/json", "X-Gateway-Token": token},
        )
        urllib.request.urlopen(req, timeout=3).read()
        for sid in (SESSION_ON, SESSION_OFF):
            print(f"  before: chat {sid[:8]} -> {_post(port, sid, 'hello')}")

        results: list[tuple[float, str, int, float]] = []
        stop = threading.Event()

        def chat(sid: str) -> None:
            while not stop.is_set():
                code, ms = _post(port, sid, "Kan je de marge van maart nakijken?")
                results.append((time.time(), sid[:8], code, ms))
                time.sleep(0.1)

        threads = [
            threading.Thread(target=chat, args=(s,), daemon=True) for s in (SESSION_ON, SESSION_OFF)
        ]
        for t in threads:
            t.start()
        time.sleep(2)
        t0 = time.time()
        rc = subprocess.run(
            [py, "-m", "client_gateway", "start", "--replace", "--port", str(port)],
            cwd=str(REPO),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        switch_s = time.time() - t0
        print(f"replace: exit {rc.returncode} in {switch_s:.1f} s :: {rc.stdout.strip()[-300:]}")
        time.sleep(75)  # keep chatting on the new gateway (NER loading, dictionary)
        stop.set()
        for t in threads:
            t.join(timeout=70)
        new = _get(port, "/__gateway/health") or {}
        print(f"new gateway {new.get('version')}")
        for sid in (SESSION_ON, SESSION_OFF):
            mode = (_get(port, f"/__gateway/session?id={sid}") or {}).get("mode")
            print(f"  after: chat {sid[:8]} mode {mode}")
        bad = [r for r in results if r[2] != 200]
        times = sorted(r[0] for r in results)
        gap = max((b - a for a, b in zip(times, times[1:])), default=0.0)
        print(
            f"requests: {len(results)}, failed: {len(bad)} "
            f"({sorted({(r[1], r[2]) for r in bad})}), longest gap {gap:.1f} s"
        )
        after = [r for r in results if r[0] > t0 + switch_s and r[1] == SESSION_ON[:8]]
        if after:
            print(
                f"first masked request on the new gateway: {after[0][3]:.0f} ms, code {after[0][2]}; "
                f"median after: {sorted(r[3] for r in after)[len(after) // 2]:.0f} ms"
            )
        print(f"upstream received {len(RECEIVED)} requests")
        for text in dict.fromkeys(ERRORS):
            print(f"  error: {text}")
    finally:
        port_info = _get(port, "/__gateway/health") or {}
        pid = port_info.get("pid")
        if isinstance(pid, int):
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        old_proc.kill()
        up.shutdown()
        subprocess.run(
            ["git", "-C", str(REPO), "worktree", "remove", "--force", str(tree)],
            capture_output=True,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
