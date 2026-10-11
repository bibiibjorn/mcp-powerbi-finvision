"""Tests for graph LRU cache."""

import time

from core.graph.cache import GraphCache
from core.graph.models import Graph, Node


def _make(n: int) -> Graph:
    g = Graph()
    for i in range(n):
        g.add_node(Node(kind="measure", scoped_name=f"T[m{i}]"))
    return g


def test_put_then_get_returns_same_graph():
    c = GraphCache(max_entries=4, ttl_seconds=300)
    g = _make(3)
    gid = c.put(("etag-A", 0), g)
    assert c.get_by_id(gid) is g


def test_put_with_same_key_returns_same_id():
    c = GraphCache(max_entries=4, ttl_seconds=300)
    id1 = c.put(("etag-A", 1), _make(1))
    id2 = c.put(("etag-A", 1), _make(1))
    assert id1 == id2


def test_lru_evicts_oldest_when_over_capacity():
    c = GraphCache(max_entries=2, ttl_seconds=300)
    a = c.put(("etag-A", 0), _make(1))
    b = c.put(("etag-B", 0), _make(1))
    c.put(("etag-C", 0), _make(1))
    assert c.get_by_id(a) is None
    assert c.get_by_id(b) is not None


def test_ttl_expired_returns_none():
    c = GraphCache(max_entries=4, ttl_seconds=0)
    gid = c.put(("etag-A", 0), _make(1))
    time.sleep(0.01)
    assert c.get_by_id(gid) is None
