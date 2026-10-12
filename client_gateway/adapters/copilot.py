"""What the two Copilot adapters (Chat Completions, Responses) share: VS Code's request wrappers and
the identity of a chat.

**Chat identity.** Copilot sends no per-chat id: ``x-interaction-id`` is per turn (shared by the main
call and the side calls of that turn), ``vscode-sessionid`` per window, ``x-request-id`` per call.
What every request of a chat does repeat is the chat's FIRST user turn (the whole history is resent),
so the chat key is a short hash of the first human text: the ``<userRequest>`` block of the first
user message that has one (VS Code wraps the typed text in it; ``<environment_info>`` and
``<context>`` are cut away), whitespace normalised. The same key comes out of a Chat Completions body
and of a Responses body, so a chat that Copilot moves to the other API ("Auto") keeps its identity.

Only a WRAPPED turn yields a key. The side calls (title, intent, summary; their prompt is the app's
own text, not the chat's first turn) carry no ``<userRequest>`` and therefore get no key of their own:
the front end maps them through ``x-interaction-id`` to the chat of the main call of that turn.

Limits (documented, not hidden): two chats that open with exactly the same text share a key (and so
a PBI flag and a switch); when Copilot compacts a long chat and drops the first user turn the key
changes (the chat stays masked while a marked tool result is still in the history).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from typing import Any

from client_gateway.adapters.base import MaskContext, Route, load_json, norm_host, norm_path
from client_gateway.adapters.common import (
    guarded_mask,
    replace_part_texts,
    rewrite_body,
    sse_rehydrator,
    unmask_json_response,
    walk_dict,
)
from client_gateway.adapters.control import typed_command
from client_gateway.adapters.streams import StreamRehydrator
from client_gateway.masking import dumps

HOST_SUFFIX = "githubcopilot.com"
DEV_ROLES = frozenset({"system", "developer"})
REMINDERS = re.compile(r"(?s)<reminderInstructions>.*?</reminderInstructions>")
USER_REQUEST = "<userRequest>"
USER_REQUEST_END = "</userRequest>"


def text_parts(content: Any) -> list[str]:
    """The text of a message content: the string itself, or the text of its text parts."""
    if isinstance(content, str):
        return [content]
    out: list[str] = []
    if isinstance(content, list):
        for part in content:
            if isinstance(part, str):
                out.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                out.append(part["text"])
    return out


def human_text(text: str) -> str | None:
    """The text the human typed in a user message: the content of the LAST ``<userRequest>`` block
    when VS Code wrapped it, the plain text when nothing wraps it, None when the message holds only
    wrappers (``<context>``, ``<attachments>``...)."""
    start = text.rfind(USER_REQUEST)
    if start >= 0:
        inner = text[start + len(USER_REQUEST) :]  # noqa: E203
        end = inner.find(USER_REQUEST_END)
        return (inner if end < 0 else inner[:end]).strip() or None
    stripped = text.strip()
    if not stripped or stripped.startswith("<"):
        return None  # only VS Code wrappers (or nothing): no human text
    return stripped


def replace_human_text(text: str, new_text: str) -> str | None:
    """``text`` with the human part set to ``new_text``: only the content of the LAST
    ``<userRequest>`` block when VS Code wrapped it (the ``<context>`` / ``<attachments>`` blocks and
    the whitespace around the typed text stay), the whole text when nothing wraps it. None when the
    text holds no human text (``human_text`` is None)."""
    start = text.rfind(USER_REQUEST)
    if start < 0:
        return new_text if human_text(text) is not None else None
    head = text[: start + len(USER_REQUEST)]
    inner = text[start + len(USER_REQUEST) :]  # noqa: E203
    end = inner.find(USER_REQUEST_END)
    body, tail = (inner, "") if end < 0 else (inner[:end], inner[end:])
    if not body.strip():
        return None
    lead = body[: len(body) - len(body.lstrip())]
    trail = body[len(body.rstrip()) :]  # noqa: E203
    return head + lead + new_text + trail + tail


def replace_human_content(content: Any, new_text: str) -> Any | None:
    """A user message ``content`` (a string, or a list of parts) with its human text replaced, or
    None when there is nothing to replace."""
    if isinstance(content, str):
        return replace_human_text(content, new_text)
    if not isinstance(content, list):
        return None
    texts = text_parts(content)
    if human_text("".join(texts)) is None:
        return None
    if any(
        USER_REQUEST in t for t in texts
    ):  # the wrapper sits in one part: only that part changes
        for index in range(len(content) - 1, -1, -1):
            part = content[index]
            text = part.get("text") if isinstance(part, dict) else part
            if isinstance(text, str) and USER_REQUEST in text:
                new = replace_human_text(text, new_text)
                if new is None:
                    return None
                out = list(content)
                out[index] = new if isinstance(part, str) else dict(part, text=new)
                return out
        return None
    return replace_part_texts(content, new_text)


def chat_key_of(human: str) -> str:
    """The chat key of a first human text: ``c`` + 16 hex characters of its SHA-256 (whitespace
    normalised). Short, stable, and no way back to the text."""
    digest = hashlib.sha256(" ".join(human.split()).encode("utf-8", "surrogatepass")).hexdigest()
    return "c" + digest[:16]


def chat_key(body: bytes) -> str | None:
    """The chat key of a Copilot request body (``messages[]`` or ``input[]``), or None when no user
    message carries a ``<userRequest>`` block. Never raises."""
    try:
        tree = json.loads(body)
    except (ValueError, RecursionError):
        return None
    if not isinstance(tree, dict):
        return None
    items = tree.get("messages")
    if not isinstance(items, list):
        items = tree.get("input")
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict) or item.get("role") != "user":
            continue
        text = "".join(text_parts(item.get("content")))
        if USER_REQUEST not in text:
            continue
        human = human_text(text)
        if human:
            return chat_key_of(human)
    return None


def mask_arguments(text: str, ctx: Any) -> str:
    """Tool-call arguments (a JSON string): the exact text the model wrote when the memo has it,
    else the parsed leaves and keys masked; text that needs no change stays as it is."""
    if not text:
        return text
    hit = ctx.replay(text)
    if hit is not None:
        return str(hit)
    try:
        tree = json.loads(text)
    except ValueError:
        return str(ctx.text(text))
    if not isinstance(tree, (dict, list)):
        return str(ctx.text(text))
    masked = ctx.mask_tree(tree, keys=True)
    return text if masked == tree else dumps(masked).decode("utf-8")


class CopilotAdapter:
    """What the Chat Completions and the Responses adapter share: the route (POST on a
    ``githubcopilot.com`` host), the chat identity, the shape of the request walk, the newest typed
    text and the response side. A subclass names its ``path`` and ``items_key`` (the list that holds
    the history), the stream class, the keys of a response that are never text, and implements
    ``_items`` (shape check), ``_handlers`` (the fields it masks itself) and ``_from_model``."""

    chat_space = "copilot"  # shared by both APIs: one chat, either API
    uses_turn_ids = True  # side calls follow their chat through ``x-interaction-id``
    name: str
    path: str
    items_key: str
    response_skip: frozenset[str]
    verbatim_skip: frozenset[str] = frozenset({"tools"})  # the developer's list, not read
    stream: Callable[[MaskContext, str | None, bool], StreamRehydrator]

    # ------------------------------------------------------------------ routing
    def match(self, method: str, host: str, path: str, content_type: str = "") -> bool:
        h = norm_host(host)
        return (
            method.upper() == "POST"
            and (h == HOST_SUFFIX or h.endswith("." + HOST_SUFFIX))
            and norm_path(path) == self.path
            and (not content_type or "json" in content_type.lower())
        )

    def chat_key(self, body: bytes, route: Route | None = None) -> str | None:
        """Copilot sends no chat id: the chat is its first user turn. None for a body without a
        ``<userRequest>`` block (a side call): the front end maps it by turn id."""
        return chat_key(body)

    def url_chat_key(self, path: str) -> str | None:
        return None

    # ------------------------------------------------------------------ request
    def _items(self, tree: Any) -> Any:  # pragma: no cover - abstract
        raise NotImplementedError

    def _handlers(self, ctx: MaskContext) -> dict[str, Callable[[Any], Any]]:  # pragma: no cover
        raise NotImplementedError

    def _from_model(self, item: dict[str, Any]) -> bool:  # pragma: no cover - abstract
        raise NotImplementedError

    def _strings(self, item: dict[str, Any]) -> list[str]:  # pragma: no cover - abstract
        raise NotImplementedError

    def mask_request(self, body: bytes, ctx: MaskContext, route: Route | None = None) -> bytes:
        tree = load_json(body)
        items = self._items(tree)  # MaskingFailed("unsupported_format") for another shape
        ctx.scan_marker(body)

        def build() -> dict[str, Any]:
            if isinstance(items, list):
                self._recognize(items, ctx)
            return walk_dict(tree, ctx, self._handlers(ctx), self.verbatim_skip)

        return guarded_mask(ctx, build)

    def _recognize(self, items: list[dict[str, Any]], ctx: MaskContext) -> None:
        """Name recognition over what is NEW: everything the first time this chat is masked, then
        the items after the last thing the model wrote (the new user turn or tool results)."""
        whole = not ctx.memo.history_scanned
        last = max((i for i, it in enumerate(items) if self._from_model(it)), default=-1)

        def strings(scope: list[dict[str, Any]]) -> list[str]:
            return [
                REMINDERS.sub(" ", s)
                for item in scope
                if item.get("role") not in DEV_ROLES
                for s in self._strings(item)
            ]

        # strict: what is NEW (after the last thing the model wrote); the earlier conversation of a
        # chat masked for the first time is recognized best effort (it never blocks)
        new = strings(items[last + 1 :])  # noqa: E203
        older = strings(items[: last + 1]) if whole else None
        ctx.recognize(new, history=whole, older=older)

    # ------------------------------------------------------------------ newest user text
    def newest_user_text(self, body: bytes, route: Route | None = None) -> str | None:
        """The text the human typed in the last ``user`` message: the content of the LAST
        ``<userRequest>`` block when VS Code wrapped it, the plain text when nothing wraps it,
        None when the message holds only wrappers (``<context>``, ``<attachments>``...)."""
        try:
            tree = json.loads(body)
        except (ValueError, RecursionError):
            return None
        items = tree.get(self.items_key) if isinstance(tree, dict) else None
        if not isinstance(items, list):
            return None
        for item in reversed(items):
            if isinstance(item, dict) and item.get("role") == "user":
                return human_text("".join(text_parts(item.get("content"))))
        return None

    def replace_newest_user_text(self, body: bytes, route: Route | None, new_text: str) -> bytes:
        """``body`` with the human text of the newest ``user`` message (what ``newest_user_text``
        reads) replaced by ``new_text``; the wrapper blocks around it and everything else are kept.
        The same bytes when there is nothing to replace."""

        def edit(tree: Any) -> bool:
            items = tree.get(self.items_key) if isinstance(tree, dict) else None
            if not isinstance(items, list):
                return False
            for item in reversed(items):
                if isinstance(item, dict) and item.get("role") == "user":
                    new = replace_human_content(item.get("content"), new_text)
                    if new is None:
                        return False
                    item["content"] = new
                    return True
            return False

        return rewrite_body(body, edit)

    def replace_command_turns(self, body: bytes, route: Route | None, new_text: str) -> bytes:
        """``body`` with EVERY user turn whose human text is exactly a typed command replaced by
        ``new_text``. Copilot resends the whole history, so a ``masking on`` typed earlier is in
        every later request; it must not reach the AI either. Rewriting never switches anything
        (only the newest turn does, in the front end). The same bytes when no turn is a command."""

        def edit(tree: Any) -> bool:
            items = tree.get(self.items_key) if isinstance(tree, dict) else None
            if not isinstance(items, list):
                return False
            changed = False
            for item in items:
                if not isinstance(item, dict) or item.get("role") != "user":
                    continue
                content = item.get("content")
                if typed_command(human_text("".join(text_parts(content)))) is None:
                    continue
                new = replace_human_content(content, new_text)
                if new is not None:
                    item["content"] = new
                    changed = True
            return changed

        return rewrite_body(body, edit)

    # ------------------------------------------------------------------ response
    def rehydrator(
        self,
        content_type: str,
        content_encoding: str | None,
        ctx: MaskContext,
        route: Route | None = None,
        reencode: bool = True,
    ) -> StreamRehydrator:
        return sse_rehydrator(self.stream, content_type, content_encoding, ctx, reencode)

    def unmask_response(
        self,
        body: bytes,
        content_type: str,
        content_encoding: str | None,
        ctx: MaskContext,
        route: Route | None = None,
        reencode: bool = True,
    ) -> bytes:
        return unmask_json_response(
            body, content_type, content_encoding, ctx, reencode,
            self.response_skip, frozenset({"arguments"}),
        )  # fmt: skip
