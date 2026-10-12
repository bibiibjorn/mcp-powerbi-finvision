"""The gateway exits by itself when nothing is routed to it any more (owner rule 2026-10-08).

The user's typed ``masking on`` starts the gateway and routes the chat's folder; ``masking off``
unroutes it (``client_gateway.switch``). The gateway itself then notices that no folder and no
"every chat" route points at it, waits until no request is running and none came in for a grace
period (a session that has not yet followed its settings change still reaches it), and stops.

Not used while the desktop apps are routed (``client_gateway.routing``: their lifecycle decides),
nor with config ``client_gateway.stop_when_unused`` false or env ``FINVISION_GATEWAY_KEEP=1`` (the
test suite and a lab gateway on a temp home).
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable

KEEP_ENV = "FINVISION_GATEWAY_KEEP"
GRACE_S = 20.0  # unused (nothing routed, no request) this long before it stops
EVERY_S = 5.0


def wanted(cfg: Callable[[str, Any], Any]) -> bool:
    """The reaper runs unless switched off (env ``FINVISION_GATEWAY_KEEP=1`` or config)."""
    if os.environ.get(KEEP_ENV, "").strip() == "1":
        return False
    return str(cfg("client_gateway.stop_when_unused", True)).strip().lower() not in (
        "0",
        "false",
        "off",
        "no",
    )


class Reaper:
    def __init__(
        self,
        gateway: Any,
        still_used: Callable[[], bool],
        grace_s: float = GRACE_S,
        every_s: float = EVERY_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.gateway = gateway
        self.still_used = still_used
        self.grace_s = grace_s
        self.every_s = every_s
        self.clock = clock
        self._since: float | None = None
        self._stop = threading.Event()

    def tick(self) -> bool:
        """One check; True when it stopped the gateway."""
        now = self.clock()
        busy = getattr(self.gateway, "in_flight", 0) > 0
        recent = now - getattr(self.gateway, "last_request", now) < self.grace_s
        try:
            used = bool(self.still_used())
        except Exception:  # noqa: BLE001 - unsure: keep running
            used = True
        if used or busy or recent:
            self._since = None
            return False
        if self._since is None:
            self._since = now
            return False
        if now - self._since < self.grace_s:
            return False
        try:
            self.gateway.log.event("stopped_unused")
        except Exception:  # noqa: BLE001
            pass
        self._stop.set()
        self.gateway.shutdown()
        return True

    def _loop(self) -> None:
        while not self._stop.wait(self.every_s):
            try:
                if self.tick():
                    return
            except Exception:  # noqa: BLE001 - the reaper never takes the gateway down by error
                pass

    def start(self) -> None:
        threading.Thread(target=self._loop, name="gateway-reaper", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
