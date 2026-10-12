"""Screenshots through an ISOLATED gateway: ``python tools/screenshot_soak.py IMG...``.

In-process gateway (temporary masking home, free port, provider reading the models open in Power
BI Desktop), masking ON, a local fake upstream that keeps what it receives. Each screenshot is sent
as an image block; the image the upstream received is then read again with both OCR engines and
checked for client values. Reports per screenshot: time, values painted, values still readable in
what left the gateway (must be 0), and whether it was withheld. Writes the received images next to
the temp home for a visual check. The live gateway and Anthropic are never involved.
"""

from __future__ import annotations

import base64
import io
import json
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from client_gateway import images as im  # noqa: E402
from client_gateway.provider import DesktopBackend, Provider  # noqa: E402
from client_gateway.server import Gateway  # noqa: E402
from client_gateway.state import SESSION_HEADER, GatewayState  # noqa: E402
from tools.gateway_soak import RECEIVED, _Upstream  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402


def main() -> int:
    up = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    home = Path(tempfile.mkdtemp(prefix="shot_soak_"))
    state = GatewayState(home=home, default_mode="on", images_policy="redact")
    gw = Gateway(
        state,
        port=0,
        upstream=f"http://127.0.0.1:{up.server_address[1]}",
        provider=Provider(state, DesktopBackend()),
    )
    gw.start()
    state.wait_ready(180)
    state.set_mode("shot-soak", "on")  # as a chat does: starts the name recognizer early
    ner = getattr(state.detectors, "ner", None)
    if ner is not None:
        ner._ready.wait(180)
    print(
        f"isolated gateway {gw.url}, dictionary {state.smap.stats['values']} values, "
        f"OCR engines {im._resolve_engines(None)}"
    )
    for path in sys.argv[1:]:
        data = Path(path).read_bytes()
        block = {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.b64encode(data).decode(),
            },
        }
        body = json.dumps(
            {
                "model": "x",
                "max_tokens": 10,
                "messages": [
                    {
                        "role": "user",
                        "content": [block, {"type": "text", "text": "What is on this screenshot?"}],
                    }
                ],
            }
        ).encode()
        req = urllib.request.Request(
            gw.url + "/v1/messages",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", SESSION_HEADER: "shot-soak"},
        )
        t0 = time.perf_counter()
        try:
            urllib.request.urlopen(req, timeout=180).read()
        except urllib.error.HTTPError as exc:
            print(f"{Path(path).stem:16} BLOCKED {exc.code}: {exc.read()[:300]!r}")
            continue
        ms = (time.perf_counter() - t0) * 1000
        sent = json.loads(RECEIVED[-1])["messages"][0]["content"][0]
        name = Path(path).stem
        if sent.get("type") != "image":
            print(f"{name:16} {ms:7.0f} ms  WITHHELD ({str(sent.get('text', ''))[:80]})")
            continue
        out = base64.b64decode(sent["source"]["data"])
        (home / f"{name}.sent.png").write_bytes(out)
        img = Image.open(io.BytesIO(out)).convert("RGB")
        st = {"engines": [], "passes": 0, "words_first_pass": 0}
        found = im._find_hits(img, state.smap, im._resolve_engines(None), st, recognize_text=False)
        left = [h for h in found if h.label != im.UNREAD_LABEL]
        unsure = len(found) - len(left)
        stats = state.stats()
        print(
            f"{name:16} {ms:7.0f} ms  redacted={stats['images_redacted']} "
            f"withheld={stats['images_withheld']}  client values still readable in what left "
            f"the gateway: {len(left)}, unsure lines left: {unsure}  (re-read with "
            f"{st['engines']}, {st['passes']} passes, {st.get('unreadable', 0)} unreadable)"
        )
    print(f"received images in {home}")
    gw.shutdown()
    up.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
