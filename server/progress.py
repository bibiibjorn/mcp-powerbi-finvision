"""
Progress notification bridge for sync handlers running in worker threads.

Handlers are sync functions the dispatcher runs on a worker thread through
``contextvars.copy_context().run`` (``run_in_executor`` alone does NOT copy
contextvars), so the MCP ``request_ctx`` — session + progress token — is visible
here. The notification is scheduled on the server loop published by the
dispatcher (``server.request_context.SERVER_LOOP``); ``asyncio.get_event_loop()``
cannot be used because it raises in non-main threads on Python 3.12+.
"""

import logging
from typing import Optional

from server.request_context import current_request_meta, current_session, fire_on_server_loop

logger = logging.getLogger(__name__)


def _get_progress_token() -> Optional[tuple]:
    """
    Get the progress token and session from the MCP request context.

    Returns (progress_token, session) or None if not available.
    """
    meta = current_request_meta()
    token = getattr(meta, "progressToken", None) if meta is not None else None
    if token is None:
        return None
    session = current_session()
    if session is None:
        return None
    return (token, session)


def emit_progress(
    progress: float,
    total: Optional[float] = None,
    message: Optional[str] = None,
) -> None:
    """
    Emit a progress notification from a sync handler.

    Safe to call from any context -- silently no-ops if:
    - No MCP request context is available
    - No progress token was provided by the client
    - The notification fails for any reason

    Never blocks the caller (fire-and-forget on the server loop).

    Args:
        progress: Current progress value (should increase monotonically).
        total: Total expected progress value, if known.
        message: Human-readable progress message.
    """
    ctx_info = _get_progress_token()
    if ctx_info is None:
        return

    progress_token, session = ctx_info

    async def _send():
        try:
            await session.send_progress_notification(
                progress_token=progress_token,
                progress=progress,
                total=total,
                message=message,
            )
        except Exception as e:
            logger.debug("Progress notification failed: %s", e)

    if not fire_on_server_loop(_send):
        logger.debug("Could not schedule progress notification (no server loop)")
