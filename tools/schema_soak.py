"""Check that masking leaves every tool definition valid: ``python tools/schema_soak.py``.

An ISOLATED gateway (temporary masking home, free port, the provider reading the models open in
Power BI Desktop, masking on) in front of a fake upstream that records the request. A real
headless Claude Code turn is sent through it, so the request carries the real tool list. Each
tool's ``input_schema`` before (what Claude Code sent) and after masking (what the upstream got)
is compared: every changed schema string is reported with its JSON path and the KEY it sits
under (never the value), and each masked schema is checked with jsonschema's Draft 2020-12
meta-schema. The live gateway and Anthropic are never involved.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from client_gateway.provider import DesktopBackend, Provider  # noqa: E402
from client_gateway.server import Gateway  # noqa: E402
from client_gateway.state import GatewayState  # noqa: E402
from tools.gateway_soak import RECEIVED, _Upstream  # noqa: E402


def _diff(a, b, path, out):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in a:
            if k in b:
                _diff(a[k], b[k], f"{path}.{k}", out)
            else:
                out.append((path, f"key renamed: {k!r}"))
    elif isinstance(a, list) and isinstance(b, list):
        for i, (x, y) in enumerate(zip(a, b)):
            _diff(x, y, f"{path}[{i}]", out)
    elif a != b:
        out.append((path, f"string changed ({len(str(a))} -> {len(str(b))} chars)"))


def main() -> int:
    up = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    home = Path(tempfile.mkdtemp(prefix="schema_soak_"))
    state = GatewayState(home=home, default_mode="on")
    gw = Gateway(
        state,
        port=0,
        upstream=f"http://127.0.0.1:{up.server_address[1]}",
        provider=Provider(state, DesktopBackend()),
    )
    gw.start()
    state.wait_ready(180)
    print(f"isolated gateway {gw.url}, dictionary {state.smap.stats['values']} values")
    # the unmasked request, for the comparison: a second fake upstream would do, but the masked
    # body plus the gateway's own memo is enough: re-run masking off via a session switched off
    env = dict(os.environ, ANTHROPIC_BASE_URL=gw.url)
    # the user settings point ANTHROPIC_BASE_URL at the live gateway and win over the process
    # environment: leave the user and local sources out (MCP servers come from ~/.claude.json)
    cwd = tempfile.mkdtemp(prefix="schema_soak_cwd_")
    cmd = ["claude", "-p", "say ok", "--output-format", "json", "--setting-sources", "project"]
    if os.environ.get("SOAK_MCP_CONFIG"):  # the MCP servers whose tools to check
        cmd += ["--mcp-config", os.environ["SOAK_MCP_CONFIG"], "--strict-mcp-config"]
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        capture_output=True,
        timeout=300,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    print(f"claude exit {proc.returncode}: {(proc.stdout or proc.stderr)[-300:]}")
    masked = [json.loads(b) for b in RECEIVED if b.strip().startswith(b"{")]
    masked = [m for m in masked if m.get("tools")]
    if not masked:
        print("no request with tools reached the upstream")
        return 1
    body = masked[-1]
    state.set_mode("", "off", token=state.token)
    RECEIVED.clear()
    subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, timeout=180)
    plain = [json.loads(b) for b in RECEIVED if b.strip().startswith(b"{")]
    plain = [p for p in plain if p.get("tools")][-1]
    by_name = {t.get("name"): t for t in plain["tools"]}
    try:
        from jsonschema import Draft202012Validator  # noqa: PLC0415
    except ImportError:
        Draft202012Validator = None
    bad = 0
    for i, tool in enumerate(body["tools"]):
        name = tool.get("name")
        orig = by_name.get(name)
        if orig is None:
            print(f"tools[{i}] {name}: renamed by masking")
            bad += 1
            continue
        diffs: list = []
        _diff(orig.get("input_schema"), tool.get("input_schema"), "input_schema", diffs)
        invalid = ""
        if Draft202012Validator is not None:
            try:
                Draft202012Validator.check_schema(tool.get("input_schema") or {})
            except Exception as exc:  # noqa: BLE001
                invalid = f" INVALID: {type(exc).__name__} at {list(getattr(exc, 'path', []))}"
        if diffs or invalid:
            bad += 1
            print(f"tools[{i}] {name}:{invalid}")
            for path, what in diffs[:10]:
                print(f"    {path}: {what}")
    print(f"{len(body['tools'])} tools, {bad} changed or invalid")
    gw.shutdown()
    up.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
