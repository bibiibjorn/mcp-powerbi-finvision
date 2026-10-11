"""LRU cache for built dependency graphs.

Key  = tuple from the caller. ``core.graph.service`` keys on
       ``(scope, normalised path, model_etag, report_signature, include_bound_reports)``
       (J-19: the old ``(model_etag, max_mtime)`` key ignored scope and path, and
       max-mtime missed deletions and mtime-preserving copies).
Id   = sha256(key)[:12]; returned to caller for subsequent lookups.

The singleton is created at import (J-27: the lazy ``global`` init raced).
Expired / evicted entries also drop their key mapping (no ``_key_to_id``
orphans).
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from typing import Any, Optional, Tuple

from core.graph.models import Graph


class GraphCache:
    def __init__(self, max_entries: int = 8, ttl_seconds: Optional[int] = 300):
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._store: "OrderedDict[str, Tuple[Graph, float]]" = OrderedDict()
        self._key_to_id: dict[Tuple[Any, ...], str] = {}
        self._lock = threading.RLock()

    def _id_for(self, key: Tuple[Any, ...]) -> str:
        return hashlib.sha256(repr(key).encode("utf-8")).hexdigest()[:12]

    def _drop(self, gid: str) -> None:
        self._store.pop(gid, None)
        for k in [k for k, v in self._key_to_id.items() if v == gid]:
            del self._key_to_id[k]

    def put(self, key: Tuple[Any, ...], graph: Graph) -> str:
        gid = self._id_for(key)
        with self._lock:
            self._store[gid] = (graph, time.time())
            self._store.move_to_end(gid)
            self._key_to_id[key] = gid
            while len(self._store) > self.max_entries:
                oldest_id = next(iter(self._store))
                self._drop(oldest_id)
        return gid

    def get_by_id(self, gid: str) -> Optional[Graph]:
        with self._lock:
            entry = self._store.get(gid)
            if entry is None:
                return None
            graph, ts = entry
            if self.ttl_seconds is not None and self.ttl_seconds <= (time.time() - ts):
                self._drop(gid)
                return None
            self._store.move_to_end(gid)
            return graph

    def get_id_for(self, key: Tuple[Any, ...]) -> Optional[str]:
        with self._lock:
            gid = self._key_to_id.get(key)
            if gid is not None and gid not in self._store:
                del self._key_to_id[key]
                return None
            return gid

    def get(self, key: Tuple[Any, ...]) -> Tuple[Optional[str], Optional[Graph]]:
        """``(graph_id, graph)`` for *key* in ONE locked step (J-27), or ``(None, None)``."""
        with self._lock:
            gid = self.get_id_for(key)
            if gid is None:
                return None, None
            graph = self.get_by_id(gid)
            return (gid, graph) if graph is not None else (None, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self._key_to_id.clear()


_singleton: GraphCache = GraphCache(max_entries=8, ttl_seconds=300)


def get_cache() -> GraphCache:
    return _singleton
