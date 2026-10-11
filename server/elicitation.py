"""MCP Elicitation bridge for sync handlers.

Elicitation (MCP spec 2025-06-18) lets the *server* ask the *user* a structured
question mid-tool-call. Unlike sampling (which asks the client's LLM and is
deprecated + unimplemented by Claude Code — see server/sampling.py), elicitation
is supported by Claude Code since v2.1.76, so this is the live interactive path.

Used by the destructive-op gate (core/autonomous/gating.py) to turn the
"blocked, retry with confirm_destructive=true" round trip into a single
in-flight confirmation prompt.

This module exposes a sync helper ``elicit_confirmation`` that handlers call
inside a tool-call context. It returns:

    True   — user explicitly confirmed  → proceed
    False  — user explicitly declined/cancelled → block, saying so
    None   — elicitation unavailable/errored/timed out → caller MUST fall back
             to its pre-existing behaviour

None is never the same thing as False. Conflating them would either block ops
that were previously allowed, or allow ops that were previously blocked.

Intentionally small: one request, one boolean answer, no multi-turn.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, Optional

from server.request_context import current_session, run_on_server_loop

logger = logging.getLogger(__name__)

_DISABLED_VALUES = {"0", "false", "off", "no"}


def elicitation_enabled() -> bool:
    """Whether gate elicitation is enabled, read LIVE from the environment.

    Read live on every call (a dict lookup, negligible next to the CLR ops the
    gate guards) for the same reason ``gating.current_policy`` does: an operator
    who sees elicitation prompts misbehaving — a wedged client, an unattended
    session, a batch run that must not stop for a human — has to be able to kill
    the path with ``MCP_GATE_ELICITATION=0`` mid-session, without restarting the
    server process. A cached read would silently ignore that.
    """
    raw = os.environ.get("MCP_GATE_ELICITATION")
    if raw is None:
        return True
    return raw.strip().lower() not in _DISABLED_VALUES


def _get_session():
    return current_session()


def _build_message(
    *,
    message: str,
    op_name: str,
    severity: str,
    details: Optional[Dict[str, Any]],
) -> str:
    lines = [message, "", f"Operation: {op_name}", f"Severity: {severity}"]
    if isinstance(details, dict):
        for key, value in details.items():
            if value is None:
                continue
            text = str(value)
            if len(text) > 300:
                text = text[:297] + "..."
            lines.append(f"{key}: {text}")
    return "\n".join(lines)


# Spec constraint: the requested schema must be a flat object of primitives.
_CONFIRM_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "confirm": {
            "type": "boolean",
            "title": "Proceed with this destructive operation?",
            "description": "Yes authorizes this single operation. No cancels it.",
        }
    },
    "required": ["confirm"],
}


def elicit_confirmation(
    *,
    message: str,
    op_name: str,
    severity: str,
    details: Optional[Dict[str, Any]] = None,
    timeout: float = 120.0,
) -> Optional[bool]:
    """Ask the user to confirm a destructive operation.

    Returns True (confirmed), False (explicitly declined/cancelled), or None
    (elicitation unavailable — no session, no client support, error, timeout).
    Callers MUST treat None as "no answer" and keep their existing behaviour.

    The timeout is generous (a human is reading the prompt) but bounded, so a
    tool call can never hang forever on an unattended client.
    """
    if not elicitation_enabled():
        return None

    session = _get_session()
    if session is None:
        return None

    elicit = getattr(session, "elicit_form", None) or getattr(session, "elicit", None)
    if elicit is None:
        return None

    prompt = _build_message(message=message, op_name=op_name, severity=severity, details=details)

    async def _ask():
        try:
            result = await asyncio.wait_for(
                elicit(message=prompt, requestedSchema=_CONFIRM_SCHEMA),
                timeout=timeout,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("elicit_confirmation request failed: %s", e)
            return None
        return _interpret(result)

    # Scheduled on the server loop published by the dispatcher (a handler runs
    # on a worker thread, where asyncio.get_event_loop() raises). Returns None
    # when there is no loop or when called on the loop thread itself.
    return run_on_server_loop(_ask, timeout=timeout + 1.0)


def _interpret(result: Any) -> Optional[bool]:
    """Map an ElicitResult onto the True/False/None contract.

    accept + confirm truthy  → True
    accept + confirm falsey  → False
    decline / cancel         → False   (an explicit "no" from the user)
    anything else / malformed → None   (no answer; caller falls back)
    """
    try:
        action = getattr(result, "action", None)
        if action == "accept":
            content = getattr(result, "content", None)
            if not isinstance(content, dict) or "confirm" not in content:
                # Accepted but no usable answer — not an explicit decline.
                return None
            return bool(content.get("confirm"))
        if action in ("decline", "cancel"):
            return False
        return None
    except Exception as e:  # noqa: BLE001
        logger.debug("elicit_confirmation could not interpret result: %s", e)
        return None
