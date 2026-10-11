"""Per-call context shared between the event loop and handler worker threads.

Handlers are synchronous functions that the dispatcher runs on worker threads.
The MCP bridges (progress, elicitation, roots, sampling, log forwarding) need
two things from inside such a worker:

1. The MCP ``request_ctx`` ContextVar (session + progress token). A plain
   ``loop.run_in_executor`` does NOT copy contextvars, so the dispatcher runs
   every handler through ``contextvars.copy_context().run`` (A-02 / L-01).
2. The *server* event loop, to schedule coroutines on it. On Python 3.12+
   ``asyncio.get_event_loop()`` raises in a non-main thread, so the loop is
   captured by the dispatcher and published here (``SERVER_LOOP``), both as a
   ContextVar (per call) and as a process-wide fallback.

It also carries a per-call cancel event (``CANCEL_EVENT``): the dispatcher sets
it when the caller times out or sends ``notifications/cancelled``, and long
handler loops can poll :func:`is_cancelled` / :func:`raise_if_cancelled`.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import threading
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

#: The asyncio loop the MCP server runs on (set per call by the dispatcher).
SERVER_LOOP: contextvars.ContextVar[Optional[asyncio.AbstractEventLoop]] = contextvars.ContextVar(
    "mcp_server_loop", default=None
)

#: Set when the in-flight call was cancelled or timed out.
CANCEL_EVENT: contextvars.ContextVar[Optional[threading.Event]] = contextvars.ContextVar(
    "mcp_call_cancel_event", default=None
)

#: ``"<tool>:<op>"`` label of the in-flight call (diagnostics only).
CALL_LABEL: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "mcp_call_label", default=None
)

_process_loop: Optional[asyncio.AbstractEventLoop] = None
_process_loop_lock = threading.Lock()


class CallCancelled(Exception):
    """Raised by :func:`raise_if_cancelled` inside a handler whose call was cancelled."""


def set_server_loop(loop: Optional[asyncio.AbstractEventLoop]) -> None:
    """Publish ``loop`` as the process-wide server loop (fallback for threads
    that were not started through the dispatcher, e.g. a logging call from a
    background thread)."""
    global _process_loop
    with _process_loop_lock:
        _process_loop = loop


def get_server_loop() -> Optional[asyncio.AbstractEventLoop]:
    """The server loop for the current call, else the process-wide one.

    Returns None when no loop is known or the known loop is closed.
    """
    loop = SERVER_LOOP.get()
    if loop is None:
        loop = _process_loop
    if loop is None or loop.is_closed():
        return None
    return loop


def on_loop_thread(loop: Optional[asyncio.AbstractEventLoop]) -> bool:
    """True when the calling thread is the one running ``loop``.

    Blocking on ``run_coroutine_threadsafe(...).result()`` from that thread
    deadlocks the loop until the timeout (the old 2.5 s roots freeze), so the
    bridges must detect it and degrade instead.
    """
    if loop is None:
        return False
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        return False
    return running is loop


def current_session() -> Any:
    """The MCP ServerSession of the in-flight request, or None."""
    try:
        from mcp.server.lowlevel.server import request_ctx

        ctx = request_ctx.get(None)
        if ctx is None:
            return None
        return ctx.session
    except (LookupError, AttributeError, ImportError):
        return None


def current_request_meta() -> Any:
    """The ``_meta`` of the in-flight request (carries ``progressToken``), or None."""
    try:
        from mcp.server.lowlevel.server import request_ctx

        ctx = request_ctx.get(None)
        if ctx is None:
            return None
        return ctx.meta
    except (LookupError, AttributeError, ImportError):
        return None


def run_on_server_loop(coro_factory: Callable[[], Awaitable[Any]], timeout: float) -> Optional[Any]:
    """Run a coroutine on the server loop from a worker thread and wait for it.

    Returns the coroutine's result, or None when there is no loop, when called
    from the loop thread itself (would deadlock), on timeout, or on any error.
    ``coro_factory`` is only invoked once scheduling is possible, so no
    un-awaited coroutine is ever created.
    """
    loop = get_server_loop()
    if loop is None:
        return None
    if on_loop_thread(loop):
        logger.debug("run_on_server_loop called on the loop thread; refusing to block")
        return None
    try:
        fut = asyncio.run_coroutine_threadsafe(coro_factory(), loop)
    except Exception as e:  # noqa: BLE001
        logger.debug("run_on_server_loop scheduling failed: %s", e)
        return None
    try:
        return fut.result(timeout=timeout)
    except Exception as e:  # noqa: BLE001
        fut.cancel()
        logger.debug("run_on_server_loop wait failed: %s", e)
        return None


def fire_on_server_loop(coro_factory: Callable[[], Awaitable[Any]]) -> bool:
    """Schedule a coroutine on the server loop without waiting (never blocks).

    Works from worker threads (``run_coroutine_threadsafe``) and from the loop
    thread itself (``create_task``). Returns True when scheduled.
    """
    loop = get_server_loop()
    if loop is None:
        return False
    try:
        if on_loop_thread(loop):
            loop.create_task(coro_factory())
        else:
            asyncio.run_coroutine_threadsafe(coro_factory(), loop)
        return True
    except Exception as e:  # noqa: BLE001
        logger.debug("fire_on_server_loop failed: %s", e)
        return False


def is_cancelled() -> bool:
    """True when the current call was cancelled by the client or timed out."""
    ev = CANCEL_EVENT.get()
    return bool(ev is not None and ev.is_set())


def raise_if_cancelled() -> None:
    """Raise :class:`CallCancelled` when the current call was cancelled.

    Long handler loops (per-visual / per-run iterations) should call this
    between items so an abandoned call stops doing work.
    """
    if is_cancelled():
        raise CallCancelled(f"call {CALL_LABEL.get() or '?'} was cancelled")
