"""Per-item progress + cooperative cancel for long ``core/`` loops (F-26).

``core/`` must not hard-depend on the server layer, so the MCP bridges
(:func:`server.progress.emit_progress`, :func:`server.request_context.is_cancelled`)
are imported lazily and every failure degrades to "no progress / not cancelled".
Both are module-level functions so tests can monkeypatch them::

    ticker = Ticker(total=len(items), label="measure")
    for item in items:
        if ticker.cancelled():          # emits nothing, returns True once the call was cancelled
            break
        ...work...
        ticker.tick()                   # "measure i of n"
"""

from __future__ import annotations

from typing import Optional


def emit(done: float, total: Optional[float], message: str) -> None:
    """Send an MCP progress notification (no-op outside a request / without a token)."""
    try:
        from server.progress import emit_progress

        emit_progress(done, total, message)
    except Exception:  # noqa: BLE001 - progress must never break the work
        pass


def is_cancelled() -> bool:
    """True once the in-flight call was cancelled or timed out."""
    try:
        from server.request_context import is_cancelled as _is_cancelled

        return bool(_is_cancelled())
    except Exception:  # noqa: BLE001 - server layer absent in some callers
        return False


class Ticker:
    """``label i of n`` progress with a cancel probe; emits at most ``max_events`` times."""

    def __init__(self, total: int, label: str = "item", max_events: int = 100) -> None:
        self.total = max(int(total), 0)
        self.label = label
        self.done = 0
        self._stride = max(1, -(-self.total // max(int(max_events), 1)))

    def cancelled(self) -> bool:
        return is_cancelled()

    def tick(self, detail: str = "") -> None:
        self.done += 1
        if self.done % self._stride and self.done != self.total:
            return
        suffix = f": {detail}" if detail else ""
        emit(self.done, self.total, f"{self.label} {self.done} of {self.total}{suffix}")
