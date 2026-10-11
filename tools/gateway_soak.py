"""Isolated soak test of the client gateway: ``python tools/gateway_soak.py``.

Never touches the live gateway or Anthropic: an in-process gateway on a free port with a temporary
masking home, in front of a local fake upstream that answers like the Messages API (SSE) and
records what it received. Measures, per request size: the time direct to the fake upstream vs
through the gateway with masking OFF and ON (the gateway's own cost), health latency while a
large masked request runs, and that the mode is per chat (one chat on, another off).

Run this before anyone restarts the live gateway with new code (``start --replace``).
"""

from __future__ import annotations

import json
import statistics
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from client_gateway.server import Gateway  # noqa: E402
from client_gateway.state import SESSION_HEADER, GatewayState  # noqa: E402
from core.client_guard.engine import SessionMap  # noqa: E402

RECEIVED: list[bytes] = []
SSE = (
    b'event: message_start\ndata: {"type":"message_start","message":{"id":"m","type":'
    b'"message","role":"assistant","content":[],"model":"x","usage":{}}}\n\n'
    b'event: content_block_start\ndata: {"type":"content_block_start","index":0,'
    b'"content_block":{"type":"text","text":""}}\n\n'
    b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
    b'"delta":{"type":"text_delta","text":"Hello there."}}\n\n'
    b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
)


class _Upstream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # noqa: D401 - quiet
        pass

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        RECEIVED.append(self.rfile.read(n))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(SSE)))
        self.end_headers()
        self.wfile.write(SSE)


def _post(url: str, body: bytes, session: str) -> float:
    req = urllib.request.Request(
        url + "/v1/messages",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            SESSION_HEADER: session,
            "anthropic-version": "2023-06-01",
        },
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=120) as resp:
        resp.read()
    return (time.perf_counter() - t0) * 1000


def _body(n_messages: int, name: str) -> bytes:
    msgs = []
    for i in range(n_messages):
        msgs.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Turn {i}: check the overdue balance of {name} for OCT 25, see "
                            "EVALUATE SUMMARIZECOLUMNS('d_Vendor'[VendorName], \"Total\", [AP | Total (RC)]). "
                            + "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 40
                        ),
                    }
                ],
            }
        )
        msgs.append({"role": "assistant", "content": [{"type": "text", "text": "Done. " * 50}]})
    msgs.append({"role": "user", "content": "hi"})
    return json.dumps({"model": "x", "max_tokens": 10, "stream": True, "messages": msgs}).encode()


def main() -> int:
    up = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    upstream = f"http://127.0.0.1:{up.server_address[1]}"

    home = Path(tempfile.mkdtemp(prefix="gw_soak_"))
    smap = SessionMap(token_fn=lambda prefix, key: 70_000)
    name = "Vanhoutte Bouwwerken NV"
    smap.add_values("CUS", [name] + [f"Klant {i} Holding NV" for i in range(8000)], "soak")
    state = GatewayState(home=home, default_mode="off", smap=smap)
    gw = Gateway(state, port=0, upstream=upstream)
    gw.start()
    url = gw.url
    print(f"isolated gateway {url} -> fake upstream {upstream} (home {home.name})")

    # per-chat mode: chat A on, chat B stays on the default (off)
    on = urllib.request.Request(
        url + "/__gateway/mode",
        method="POST",
        data=json.dumps({"session": "chat-A", "mode": "on"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(on, timeout=5).read()
    probe = _body(1, name)
    _post(url, probe, "chat-A")
    a_sent = RECEIVED[-1]
    _post(url, probe, "chat-B")
    b_sent = RECEIVED[-1]
    print(
        f"per-chat mode: chat A (on) sent the name: {name.encode() in a_sent} | "
        f"chat B (off) sent it unchanged: {b_sent == probe}"
    )

    rows = []
    for label, n in (("new chat 'hi'", 0), ("medium chat", 40), ("long chat", 400)):
        body = _body(n, name)
        res = {}
        for mode, target, session in (
            ("direct", upstream, "x"),
            ("gateway OFF", url, "chat-B"),
            ("gateway ON", url, "chat-A"),
        ):
            times = [_post(target, body, session) for _ in range(6)]
            res[mode] = (times[0], statistics.median(times[1:]))
        rows.append((label, len(body), res))
    print(
        f"\n{'request':16} {'size':>8} | {'direct':>14} | {'gateway OFF':>14} | {'gateway ON':>14}"
        "   (first / median of next 5, ms)"
    )
    for label, size, res in rows:
        cells = " | ".join(
            f"{res[m][0]:6.0f} /{res[m][1]:6.1f}" for m in ("direct", "gateway OFF", "gateway ON")
        )
        print(f"{label:16} {size/1e6:6.2f}MB | {cells}")

    # health while a large masked request runs
    big = _body(1500, name)
    lat: list[float] = []
    stop = threading.Event()

    def poll():
        while not stop.is_set():
            t0 = time.perf_counter()
            urllib.request.urlopen(url + "/__gateway/health", timeout=5).read()
            lat.append((time.perf_counter() - t0) * 1000)
            time.sleep(0.02)

    th = threading.Thread(target=poll, daemon=True)
    th.start()
    ms = _post(url, big + b" ", "chat-A")
    stop.set()
    th.join()
    print(
        f"\nhealth during a {len(big)/1e6:.1f} MB masked request ({ms:.0f} ms): "
        f"max {max(lat):.0f} ms, median {statistics.median(lat):.1f} ms over {len(lat)} probes"
    )
    gw.shutdown()
    up.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
