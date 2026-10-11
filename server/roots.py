"""MCP Roots bridge for sync handlers.

The MCP 2025-06-18 protocol lets the client declare workspace roots via
``roots/list``. A well-behaved client (Claude Desktop, IDE plugins) does
this at session start. This module exposes a sync helper that pulls the
roots from the current MCP request context, so a handler running on a
dispatcher worker thread can default its ``pbip_path`` from the declared roots
when the caller omitted it, plus an async variant for code that already runs
ON the event loop (the ``powerbi://state/roots`` resource).

Scope: intentionally small — we don't subscribe to ``notifications/roots/list_changed``;
each lookup re-queries. Handler calls are infrequent enough that the cost
is negligible, and avoiding a subscription keeps this module dependency-free.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, List
from urllib.parse import unquote, urlparse

from server.request_context import current_session, get_server_loop, on_loop_thread
from server.request_context import run_on_server_loop

logger = logging.getLogger(__name__)


def _get_session():
    """Return the current MCP session or None if no request is in flight."""
    return current_session()


def _file_uri_to_path(uri: str) -> str:
    """``file:///C:/My%20Repo`` → ``C:/My Repo``; ``file://server/share`` → ``//server/share``.

    Percent-escapes are decoded (L-01: OneDrive paths with spaces never matched
    ``Path.exists()`` before).
    """
    parsed = urlparse(uri)
    path = unquote(parsed.path or "")
    if parsed.netloc and parsed.netloc.lower() != "localhost":
        return f"//{parsed.netloc}{path}"
    # Windows drive: "/C:/foo" → "C:/foo"
    if path.startswith("/") and len(path) > 2 and path[2] == ":":
        path = path[1:]
    return path


def _paths_from_result(result: Any) -> List[str]:
    paths: List[str] = []
    for root in getattr(result, "roots", []) or []:
        uri = str(getattr(root, "uri", "") or "")
        if uri.startswith("file://"):
            paths.append(_file_uri_to_path(uri))
        elif uri:
            paths.append(uri)
    return paths


async def alist_root_paths(timeout: float = 2.0) -> List[str]:
    """Async variant for callers already on the event loop (never blocks it)."""
    session = _get_session()
    if session is None:
        return []
    try:
        result = await asyncio.wait_for(session.list_roots(), timeout=timeout)
    except Exception as e:  # noqa: BLE001
        logger.debug("list_roots failed: %s", e)
        return []
    return _paths_from_result(result)


def list_root_paths(timeout: float = 2.0) -> List[str]:
    """Return client-declared workspace root paths as a list of local paths.

    Converts ``file://`` URIs to local paths. Returns an empty list on any
    failure (no session, client didn't declare roots, timeout, network error).
    Safe to call from a sync handler — silently degrades if the protocol
    feature isn't available. Called ON the loop thread it returns ``[]``
    immediately instead of deadlocking the loop (use :func:`alist_root_paths`).
    """
    session = _get_session()
    if session is None:
        return []
    if on_loop_thread(get_server_loop()):
        return []

    async def _fetch():
        try:
            return await asyncio.wait_for(session.list_roots(), timeout=timeout)
        except Exception as e:  # noqa: BLE001
            logger.debug("list_roots failed: %s", e)
            return None

    result = run_on_server_loop(_fetch, timeout=timeout + 0.5)
    if result is None:
        return []
    return _paths_from_result(result)
