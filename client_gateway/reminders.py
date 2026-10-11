"""Which text of a request holds NEW content that name recognition must read.

Claude Code wraps much of what it adds to a message in ``<system-reminder>`` blocks: its own static
instructions (CLAUDE.md files, the git status, the memory index, the skill list, todo hints), but
ALSO the content the user attached: an ``@file`` ("Called the Read tool ... Result of calling the
Read tool: <the file>"), an IDE selection, the output of a hook. Cutting every reminder out before
name recognition (the first version) therefore let a name in an attached e-mail or document go out
unchecked (2026-10-03 review, G-01).

``recognition_texts(text)`` returns the pieces of one string that are worth recognizing: the text
outside any reminder, plus the body of every reminder that is NOT static instruction. A static
reminder is recognised by the fixed wording Claude Code (and this user's setup) puts in it; those
are skipped because they hold the user's own name, paths and product names, not client names, and
because a 30 KB CLAUDE.md in every first message would cost a minute of NER. Everything else is
read; the recognizer caches by segment content, so a repeated reminder costs nothing the second
time.
"""

from __future__ import annotations

import re

_REMINDER = re.compile(r"(?s)<system-reminder>(.*?)</system-reminder>")
# how far into a reminder the fixed wording is looked for
_HEAD_CHARS = 1500
_STATIC_MARKERS = (
    "as you answer the user's questions, you can use the following context",
    "codebase and user instructions are shown below",
    "user's private global instructions",
    "project instructions, checked into the codebase",
    "user's auto-memory",
    "this is the git status at the start of the conversation",
    "# claudemd",
    "# currentdate",
    "# useremail",
    "the following skills are available",
    "skills are available for use with the skill tool",
    "the following deferred tools",
    "# mcp server instructions",
    "here are the existing contents of your todo list",
    "todowrite tool",
    "task tools haven't been used",
    "plan mode is active",
    "auto mode is active",
    "attribution for git commits",
    "claude code attached this context automatically",
    "ide_opened_file_not_text",  # a notice, not content
)


def is_static(body: str) -> bool:
    """True for a reminder whose text is fixed instruction (see the module text)."""
    head = body[:_HEAD_CHARS].casefold()
    return any(marker in head for marker in _STATIC_MARKERS)


def strip_reminders(text: str) -> str:
    """``text`` with every ``<system-reminder>`` block cut out."""
    return _REMINDER.sub(" ", text) if "<system-reminder>" in text else text


def recognition_texts(text: str) -> list[str]:
    """The pieces of ``text`` name recognition must read (non-empty strings only)."""
    if "<system-reminder>" not in text:
        return [text] if text.strip() else []
    out: list[str] = []
    rest = _REMINDER.sub(" ", text)
    if rest.strip():
        out.append(rest)
    for match in _REMINDER.finditer(text):
        body = match.group(1).strip()
        if body and not is_static(body):
            out.append(body)
    return out
