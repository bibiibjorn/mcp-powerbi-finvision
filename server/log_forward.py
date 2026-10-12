"""Forward Python logging records to the MCP client as LoggingMessageNotification.

Python's ``logging`` emits to file + stderr by default. MCP clients can't see
those — they only see tool-call responses. When the server hits a CLR
exception mid-operation, a retryable ADOMD disconnect, or a stray validation
warning, the operator has no visibility unless they tail the log file.

This module installs a ``logging.Handler`` that forwards WARN+ records (or the
level the client picked via ``logging/setLevel``) to the current MCP session
via ``session.send_log_message``. Silent no-op if no session is active (e.g.
during startup). The server registers a ``logging/setLevel`` handler so the
``logging`` capability is declared, as the spec requires before sending
``notifications/message`` (A-27).

Records from handler worker threads work because the dispatcher runs handlers
inside a copied context (``request_ctx`` visible) and publishes the server loop
(``server.request_context``). Never blocks; if the forward fails we drop the
record — the file handler still captured it.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from typing import Optional

from server import masking_layer
from server.request_context import current_session, fire_on_server_loop

# RFC-5424 names the client may send in logging/setLevel → Python levels.
_MCP_TO_PY = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "notice": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
    "alert": logging.CRITICAL,
    "emergency": logging.CRITICAL,
}

# Recursion guard: a ContextVar (not a thread-local) so the flag set inside the
# send task never leaks onto other coroutines sharing the loop thread.
_reentry: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "mcp_log_forward_reentry", default=False
)


def _get_session():
    return current_session()


def _mcp_level(levelno: int) -> str:
    if levelno >= logging.CRITICAL:
        return "critical"
    if levelno >= logging.ERROR:
        return "error"
    if levelno >= logging.WARNING:
        return "warning"
    if levelno >= logging.INFO:
        return "info"
    return "debug"


class McpNotificationHandler(logging.Handler):
    """Logging handler that forwards records to the MCP client.

    Usage: ``logging.getLogger().addHandler(McpNotificationHandler())``.
    The handler level (default WARNING) is the forwarding threshold; a client
    may change it with ``logging/setLevel`` (see :func:`set_client_level`).
    """

    def emit(self, record: logging.LogRecord) -> None:
        # Never block the logging caller; fail silent. Guard against recursion
        # (a send failure that logs would re-enter this handler).
        if _reentry.get():
            return
        if record.levelno < self.level:
            return

        session = _get_session()
        if session is None:
            return

        level = _mcp_level(record.levelno)
        try:
            message = self.format(record)
        except Exception:  # noqa: BLE001
            # Formatting must never break logging; bail out cleanly.
            return

        async def _send():
            _reentry.set(True)  # task-local: the task runs in its own context copy
            try:
                text: Optional[str] = message
                if masking_layer.enabled():
                    # a blocking HTTP call: off the loop; dropped when masking is unavailable
                    text = await asyncio.to_thread(masking_layer.mask_log_message, message)
                    if text is None:
                        return
                await session.send_log_message(
                    level=level,
                    data=text,
                    logger=record.name,
                )
            except Exception:  # noqa: BLE001
                pass

        token = _reentry.set(True)
        try:
            fire_on_server_loop(_send)
        except Exception:  # noqa: BLE001
            pass
        finally:
            _reentry.reset(token)


_installed: Optional[McpNotificationHandler] = None


def install(
    root_logger: Optional[logging.Logger] = None, level: int = logging.WARNING
) -> McpNotificationHandler:
    """Install the forwarder on the root logger (idempotent).

    Returns the installed handler so callers can ``removeHandler`` it if needed.
    """
    global _installed
    logger = root_logger or logging.getLogger()
    # Idempotent: if a forwarder is already installed, return it.
    for h in logger.handlers:
        if isinstance(h, McpNotificationHandler):
            _installed = h
            return h
    handler = McpNotificationHandler()
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
    logger.addHandler(handler)
    _installed = handler
    return handler


def set_client_level(level: str) -> int:
    """Apply a client ``logging/setLevel`` request to the installed forwarder.

    Returns the Python level now in effect. Unknown names fall back to WARNING.
    """
    py_level = _MCP_TO_PY.get(str(level).lower(), logging.WARNING)
    handler = _installed
    if handler is not None:
        handler.setLevel(py_level)
    return py_level
