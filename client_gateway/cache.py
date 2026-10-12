"""Small bounded caches for the gateway (in memory only, thread-safe).

``BoundedCache`` keeps insertion order and evicts the oldest entries once the summed cost of its
values passes ``max_cost``. A value that alone costs more than the whole budget is not stored.
"""

from __future__ import annotations

import threading
from typing import Any


class BoundedCache:
    def __init__(self, max_cost: int) -> None:
        self._max = max(1, int(max_cost))
        self._lock = threading.Lock()
        self._map: dict[Any, tuple[Any, int]] = {}
        self._cost = 0

    def get(self, key: Any) -> Any:
        """The stored value, or None."""
        with self._lock:
            hit = self._map.get(key)
        return None if hit is None else hit[0]

    def put(self, key: Any, value: Any, cost: int) -> None:
        cost = max(1, int(cost))
        if cost > self._max:
            return
        with self._lock:
            old = self._map.pop(key, None)
            if old is not None:
                self._cost -= old[1]
            while self._map and self._cost + cost > self._max:
                oldest = next(iter(self._map))
                self._cost -= self._map.pop(oldest)[1]
            self._map[key] = (value, cost)
            self._cost += cost

    def clear(self) -> None:
        with self._lock:
            self._map.clear()
            self._cost = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._map)

    @property
    def cost(self) -> int:
        with self._lock:
            return self._cost
