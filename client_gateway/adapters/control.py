"""Typed masking commands (rule R4): the only text that can switch masking on or off.

``typed_command(text)`` takes the NEWEST user-authored text of a request, as an adapter's
``newest_user_text`` extracted it (wrappers cut away), and answers ``"on"``, ``"off"`` or ``None``.
The WHOLE text must be the command: trimmed, case-insensitive, with an optional leading ``/``.
A phrase inside a sentence, a tool result, a reminder, an attached file or the model's own words
never reaches this function (the adapters only hand over text the human typed), and would not
match it either.

A typed command is handled here, on this machine: the front end switches the chat and rewrites that
user turn to ``NEUTRAL_TEXT`` before anything is forwarded, so the AI never sees the command (it
read ``masking on`` as an instruction and answered ``[REDACTED]``).
"""

from __future__ import annotations

import re

NEUTRAL_TEXT = "Reply with only: OK"  # what the AI gets in place of a typed command
_COMMAND = re.compile(r"/?masking[ \t]+(on|off)", re.IGNORECASE)


def typed_command(text: str | None) -> str | None:
    """``"on"`` / ``"off"`` when ``text`` is exactly ``masking on`` / ``masking off``, else None."""
    if not isinstance(text, str):
        return None
    match = _COMMAND.fullmatch(text.strip())
    return match.group(1).lower() if match else None
