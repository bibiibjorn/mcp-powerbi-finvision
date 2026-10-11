"""A/B test of client masking with real headless Claude Code sessions (isolated).

``python tools/ab_masking.py --out DIR SCENARIO_FILE...``

For each scenario (a text file holding the prompt) and each arm (``off``, ``on``):

* an in-process gateway on a free port with a TEMPORARY masking home, the provider reading the
  models open in Power BI Desktop (read-only), the real Anthropic upstream, and the arm's
  default mode. The live gateway (port 8890) and its home are never touched;
* every request leaving the gateway is scanned per string leaf against the dictionary: the ON
  arm must send zero client values, the OFF arm shows how many it sends;
* ``claude -p <prompt> --output-format json`` with ``--settings`` pointing ANTHROPIC_BASE_URL at
  that gateway, run in a temp directory (the masking hooks there see an unrouted session).

Arms run one after the other (never in parallel: traces and Desktop queries would contend).
Writes ``DIR/<scenario>.<arm>.json`` with the answer, timings, turns, cost and the leak counts.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from client_gateway.provider import DesktopBackend, Provider  # noqa: E402
from client_gateway.server import DEFAULT_UPSTREAM, Gateway  # noqa: E402
from client_gateway.state import GatewayState  # noqa: E402

SKIP_KEYS = {"signature", "data"}
MCP_CONFIG = os.environ.get("AB_MCP_CONFIG", "")
CLAUDE = shutil.which("claude.cmd") or shutil.which("claude") or "claude"


class LeakScan:
    """Counts dictionary values in what the gateway sends upstream (per string leaf)."""

    def __init__(self, state: GatewayState) -> None:
        self.state = state
        self.requests = 0
        self.leaks = 0
        self.leaky_requests = 0
        self.lock = threading.Lock()

    def _walk(self, obj: Any, whole: bool, depth: int = 0) -> int:
        smap = self.state.smap
        if isinstance(obj, str):
            return smap.count_leaks(obj, whole=whole) if obj else 0
        if isinstance(obj, list):
            return sum(self._walk(v, whole, depth + 1) for v in obj)
        if isinstance(obj, dict):
            if obj.get("type") in ("image", "document") and isinstance(obj.get("source"), dict):
                return 0  # images are checked by the OCR verify loop, not as text
            n = 0
            for k, v in obj.items():
                if k in SKIP_KEYS:
                    continue
                n += self._walk(v, whole and not (depth == 0 and k == "tools"), depth + 1)
            return n
        return 0

    def hook(self, request: httpx.Request) -> None:
        if not request.url.path.startswith("/v1/messages"):
            return
        try:
            body = json.loads(request.content or b"{}")
        except ValueError:
            return
        n = self._walk(body, True)
        with self.lock:
            self.requests += 1
            self.leaks += n
            self.leaky_requests += 1 if n else 0


def run_arm(prompt: str, arm: str, workdir: Path, timeout_s: int) -> dict[str, Any]:
    home = Path(tempfile.mkdtemp(prefix=f"ab_{arm}_"))
    state = GatewayState(home=home, default_mode=arm)
    scan = LeakScan(state)
    client = httpx.Client(
        http2=False,
        timeout=httpx.Timeout(connect=10.0, read=None, write=120.0, pool=60.0),
        limits=httpx.Limits(max_connections=50, max_keepalive_connections=10, keepalive_expiry=4.0),
        follow_redirects=False,
        event_hooks={"request": [scan.hook]},
    )
    provider = Provider(state, DesktopBackend())
    gw = Gateway(state, port=0, upstream=DEFAULT_UPSTREAM, provider=provider, client=client)
    gw.start()
    t_ready = time.perf_counter()
    state.wait_ready(120)
    ready_s = time.perf_counter() - t_ready
    # the user settings set ANTHROPIC_BASE_URL to the live gateway and win over the process
    # environment: leave that source out, so this session talks to THIS gateway only (the
    # MCP servers come from ~/.claude.json and stay)
    cmd = [
        CLAUDE,
        "-p",
        "--output-format",
        "json",
        "--setting-sources",
        "project,local",
        "--permission-mode",
        "bypassPermissions",
    ]
    if MCP_CONFIG:
        cmd += ["--mcp-config", MCP_CONFIG, "--strict-mcp-config"]
    env = dict(os.environ, ANTHROPIC_BASE_URL=gw.url)
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(workdir),
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            env=env,
        )
        out, err, code = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as exc:
        out, err, code = (exc.stdout or ""), "timeout", -1
    wall_s = time.perf_counter() - t0
    status = state.status()
    gw.shutdown()
    try:
        res = json.loads(out.strip().splitlines()[-1]) if out.strip() else {}
    except (ValueError, IndexError):
        res = {"raw": out[-4000:]}
    mask_ms = []
    log = home / "gateway.log"
    if log.is_file():
        for line in log.read_text(encoding="utf-8").splitlines():
            ev = json.loads(line)
            if ev.get("event") == "request" and ev.get("route") == "/v1/messages":
                mask_ms.append(ev.get("mask_ms", 0))
    return {
        "arm": arm,
        "exit_code": code,
        "stderr_tail": (err or "")[-1500:],
        "wall_s": round(wall_s, 1),
        "dictionary_ready_s": round(ready_s, 1),
        "result": res.get("result", res.get("raw")),
        "num_turns": res.get("num_turns"),
        "duration_api_ms": res.get("duration_api_ms"),
        "cost_usd": res.get("total_cost_usd"),
        "is_error": res.get("is_error"),
        "gateway_requests": status["stats"].get("requests"),
        "masked_requests": status["stats"].get("masked_requests"),
        "masked_strings": status["stats"].get("masked_strings"),
        "images": {
            k: status["stats"].get(k)
            for k in ("images_redacted", "images_withheld", "images_clean")
        },
        "dictionary_values": status["dictionary"]["values"],
        "mask_ms_total": sum(mask_ms),
        "mask_ms_max": max(mask_ms) if mask_ms else 0,
        "upstream_requests_scanned": scan.requests,
        "client_values_sent_upstream": scan.leaks,
        "requests_with_client_values": scan.leaky_requests,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    ap.add_argument("--arms", default="off,on")
    ap.add_argument("--timeout", type=int, default=1500)
    ap.add_argument("scenarios", nargs="+")
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="ab_work_"))
    for scenario in args.scenarios:
        path = Path(scenario)
        prompt = path.read_text(encoding="utf-8")
        for arm in args.arms.split(","):
            print(f"[{time.strftime('%H:%M:%S')}] {path.stem} / masking {arm} ...", flush=True)
            result = run_arm(prompt, arm, workdir, args.timeout)
            result["scenario"] = path.stem
            (out / f"{path.stem}.{arm}.json").write_text(
                json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            print(
                f"    wall {result['wall_s']} s, turns {result['num_turns']}, "
                f"gateway requests {result['gateway_requests']}, client values sent "
                f"{result['client_values_sent_upstream']}, mask {result['mask_ms_total']} ms",
                flush=True,
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
