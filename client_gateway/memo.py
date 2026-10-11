"""Remask memo: ``unmasked text -> the exact masked text Claude saw``.

When a response is rehydrated, the pair (masked, rehydrated) is recorded here. On the next request
Claude Code resends the rehydrated text in the conversation history; the request masker looks it
up and replays the identical masked bytes instead of masking again. That keeps the prompt prefix
byte-stable (prompt caching) and never lets a re-masking drift from what the model produced.

In memory only, thread-safe, bounded by entry count AND characters (oldest entries first).
"""

from __future__ import annotations

import threading

MEMO_MAX_ENTRIES = 50_000
MEMO_MAX_CHARS = (
    24_000_000  # entries hold whole assistant texts and tool inputs: bound the bytes too
)


class RemaskMemo:
    """Thread-safe bounded ``unmasked -> masked`` dictionary."""

    def __init__(
        self, max_entries: int = MEMO_MAX_ENTRIES, max_chars: int = MEMO_MAX_CHARS
    ) -> None:
        self._lock = threading.Lock()
        self._max = max(1, int(max_entries))
        self._max_chars = max(1, int(max_chars))
        self._chars = 0
        self._map: dict[str, str] = {}
        # the whole history of this chat was read by name recognition once (a gateway restart, a
        # resumed chat or "masking off" then "on" starts with False)
        self.history_scanned = False

    def remember(self, masked: str, unmasked: str) -> None:
        """Record ``unmasked -> masked``. No-op for empty or unchanged strings."""
        if not unmasked or not masked or unmasked == masked:
            return
        cost = len(unmasked) + len(masked)
        if cost > self._max_chars:
            return  # one entry bigger than the whole budget is not kept
        with self._lock:
            old = self._map.pop(unmasked, None)  # re-insert: most recent entry is evicted last
            if old is not None:
                self._chars -= len(unmasked) + len(old)
            while self._map and (
                len(self._map) >= self._max or self._chars + cost > self._max_chars
            ):
                oldest = next(iter(self._map))
                self._chars -= len(oldest) + len(self._map.pop(oldest))
            self._map[unmasked] = masked
            self._chars += cost

    def lookup(self, unmasked: str) -> str | None:
        """The masked text recorded for ``unmasked``, or None."""
        with self._lock:
            return self._map.get(unmasked)

    def __len__(self) -> int:
        with self._lock:
            return len(self._map)

    def clear(self) -> None:
        with self._lock:
            self._chars = 0
            self._map.clear()
