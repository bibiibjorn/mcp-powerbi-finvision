"""MCP Sampling bridge for sync handlers.

Sampling lets the *server* ask the *client* to invoke its LLM on the server's
behalf. Useful when the server has structured model data (a measure name,
DAX expression, dependency graph) and wants a natural-language summary
written in the caller's voice.

This module exposes a sync helper ``sample_text`` that handlers call inside a
tool-call context; it silently returns None if no session is available or the
client declined the request.

Intentionally small: no streaming, no multi-turn. One request, one response.

STATUS — do not invest further here (recorded 2026-08):
    Classic ``sampling/createMessage`` is DEPRECATED in MCP spec 2026-07-28, and
    is NOT implemented by Claude Code or Claude Desktop. In practice every call
    from those clients finds no sampling capability and this module silently
    degrades to None — which callers already handle by falling back to
    deterministic output. It is kept for spec-complete clients only.

    New interactive work should use ELICITATION instead — ``server/elicitation.py``
    (spec 2025-06-18, supported by Claude Code since v2.1.76) — which asks the
    *user* a structured question rather than the client's LLM.

    The eventual target for LLM-assist round trips is the 2026-07-28 MRTR
    ``input_required`` shape; when that lands, port callers to it rather than
    deepening this path.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from server.request_context import current_session, run_on_server_loop

logger = logging.getLogger(__name__)


def _get_session():
    return current_session()


def sample_text(
    prompt: str,
    *,
    system_prompt: Optional[str] = None,
    max_tokens: int = 800,
    temperature: float = 0.2,
    timeout: float = 30.0,
) -> Optional[str]:
    """Ask the MCP client to sample an LLM response for ``prompt``.

    Returns the response text on success, ``None`` on any failure (no session,
    client declined, LLM error, timeout). Callers should treat None as "no
    narrative available" and fall back to deterministic output.
    """
    session = _get_session()
    if session is None:
        return None

    try:
        from mcp.types import SamplingMessage, TextContent
    except ImportError:
        return None

    messages = [
        SamplingMessage(
            role="user",
            content=TextContent(type="text", text=prompt),
        )
    ]

    async def _create():
        try:
            result = await asyncio.wait_for(
                session.create_message(
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system_prompt=system_prompt,
                ),
                timeout=timeout,
            )
            # Response content is a SamplingMessage-like object with .content
            content = getattr(result, "content", None)
            if content is None:
                return None
            text = getattr(content, "text", None)
            return text
        except Exception as e:  # noqa: BLE001
            logger.debug("sample_text failed: %s", e)
            return None

    return run_on_server_loop(_create, timeout=timeout + 1.0)
