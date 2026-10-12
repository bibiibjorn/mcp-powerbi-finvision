"""M1: PbipModelCache is true LRU (promote-on-hit, evict-least-recently-used).

2026-09-22: the cache now keys on a resolved PbipScope + a value signature
(A-12 / G-16 / G-17) instead of ``path|max_mtime``; these tests patch the new
seams (``_resolve_scope`` / ``_compute_signature``) instead of the removed
``_get_max_mtime`` key.
"""

import os
from contextlib import contextmanager
from unittest.mock import patch

from server.pbip_cache import PbipModelCache, PbipScope


def _fake_scope(path: str) -> PbipScope:
    name = os.path.basename(path)
    return PbipScope(
        key=f"model={name}|reports=",
        input_path=path,
        model_dir=path,
        report_dirs=(),
        trees=(),
        pointer_files=(),
    )


@contextmanager
def _patched(cache: PbipModelCache, parse):
    with patch.object(cache, "_resolve_scope", side_effect=_fake_scope), patch.object(
        cache, "_compute_signature", return_value=(1,)
    ), patch.object(cache, "_parse_pbip", side_effect=parse):
        yield


def test_hit_promotes_entry_so_hot_entry_not_evicted():
    cache = PbipModelCache(max_entries=2)
    with _patched(cache, lambda s: f"parsed:{s.key}"):
        a_value = cache.get_or_parse("A")  # cache: [A]
        cache.get_or_parse("B")  # cache: [A, B]
        cache.get_or_parse("A")  # HIT -> A promoted -> [B, A]
        cache.get_or_parse("C")  # evict LRU (B) -> [A, C]

    # A must still be cached (most-recently used); re-parsing A would raise.
    with _patched(cache, AssertionError("re-parsed A!")):
        assert cache.get_or_parse("A") == a_value


def test_lru_evicts_least_recently_used():
    cache = PbipModelCache(max_entries=2)
    with _patched(cache, lambda s: f"parsed:{s.key}"):
        cache.get_or_parse("A")
        b_value = cache.get_or_parse("B")
        cache.get_or_parse("C")  # evict A (LRU) -> [B, C]
    # B and C cached; A evicted -> A re-parses (no raise), B does NOT
    with _patched(cache, AssertionError("re-parsed cached entry!")):
        # B is still cached -> returns the cached value without re-parse
        assert cache.get_or_parse("B") == b_value


def test_capacity_respected():
    cache = PbipModelCache(max_entries=2)
    with _patched(cache, lambda s: f"parsed:{s.key}"):
        for name in ["A", "B", "C", "D"]:
            cache.get_or_parse(name)
        assert len(cache._cache) <= 2
