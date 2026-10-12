"""Adapter for ChatGPT in the browser, logged in (``chatgpt.com``).

Endpoints (captured 2026-10-07):

* ``POST /backend-api/f/conversation``: JSON ``messages[].content.parts[]`` (strings; a
  ``multimodal_text`` message mixes strings with ``asset_pointer`` dicts), ``metadata.attachments[]``
  (``name``), ``conversation_id`` absent on the first turn. Reply: SSE, ``data: "v1"`` then JSON
  patch events (see ``ChatGPTStream``), gzip / br / zstd encoded.
* ``POST /backend-api/f/conversation/prepare``: sent while typing; ``partial_query`` is a message
  object (``content.parts[]``) holding the half-typed text.
* ``POST /backend-api/files`` and ``.../files/process_upload_stream``: upload metadata (``file_name``).
* ``PUT <host>.oaiusercontent.com/files/<id>/raw``: the raw image bytes, ``content-type: image/*``.
* ``GET /backend-api/conversations/<id>`` (the app reloads the stored conversation after a stream)
  and ``GET /backend-api/conversations`` (the sidebar list: titles are generated from masked text).

Masking: part strings, extra string fields of ``content``, attachment names, ``file_name``,
``partial_query``; dict parts and every other field are forwarded verbatim after a check-only leak
pass (a client name there fails the request closed). An upload that is not a readable image, or whose
image is withheld, raises ``MaskingFailed`` so the upload fails instead of leaking.

Uploads in a masked chat (phase 3b.0): ``POST /backend-api/files`` is attributed to a chat through its
same-origin ``Referer`` (``/c/<conversation id>``, ``chat_key``); its reply names the file (``file_id``
and the id its ``upload_url`` carries, ``response_file_ids``) and the front end remembers
``file id -> chat`` for 15 minutes (memory only). The raw ``PUT`` names that id in its URL
(``file_ids_in_url``), so the bytes of a masked chat's file are redacted / withheld (``_upload``) and the
file is marked checked. A chat send that references a file nobody redacted or checked
(``attachment_refs``: ``asset_pointer`` parts, ``attachments[].id``) is blocked by the front end in a
masked chat ("attached before masking could check it"; counted ``unchecked_attachment_blocked``).
Residual: a first-turn upload of a brand-new chat has no conversation id to attribute it to, so its
bytes pass unchanged (R2: the chat is not a Power BI chat yet); if that chat is masked on its first send
(typed ``masking on``) the send is blocked, and the user removes the image and attaches it again.

Anything else on ``chatgpt.com`` is NOT matched (``adapter_for`` returns None): in a masked chat the
front end decides what to do with an unknown POST body (the spec blocks it); in any other chat it is
passed through unchanged (R2).

Control (R4): only a real send (``/f/conversation``) yields ``newest_user_text``; the half-typed
``prepare`` text is a draft and never a command. A typed command never reaches the AI:
``replace_newest_user_text`` swaps the text of that turn (and ``draft_text`` lets the front end find a
``prepare`` draft that is exactly the command, which is swapped the same way).
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

from client_gateway.adapters.base import (
    ImageWithheld,
    MaskContext,
    Route,
    base_type,
    load_json,
    norm_host,
    norm_path,
)
from client_gateway.adapters.common import (
    VERBATIM,
    HoldStream,
    guarded_mask,
    mask_str,
    replace_part_texts,
    rewrite_body,
    sse_rehydrator,
    unmask_json_response,
    walk_dict,
)
from client_gateway.adapters.streams import (
    PassthroughRehydrator,
    StreamRehydrator,
    TailHold,
    frame_event,
    rebuild_event,
)
from client_gateway.masking import MaskingFailed

HOST = "chatgpt.com"
FILES_HOST_SUFFIX = ".oaiusercontent.com"
_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_CONVERSATION_URL = re.compile(rf"/backend-api/conversations?/({_UUID})")
# the page the app is on names its chat: ``/c/<id>`` (also ``/g/<gpt>/c/<id>``, a project page)
_PAGE_CHAT = re.compile(rf"(?:^|/)c/({_UUID})(?:/|$)")
_UPLOAD_PATH_ID = re.compile(r"^/files/([^/]+)(?:/|$)")
_FILE_ID = re.compile(r"file[-_][0-9A-Za-z]+")
# values that are ids or opaque tokens, never text: not touched when a response is unmasked
RESPONSE_SKIP = frozenset(
    {
        "id", "token", "conduit_token", "resume_conversation_token", "message_id", "parent_id",
        "conversation_id", "request_id", "turn_exchange_id", "working_turn_id", "turn_trace_id",
        "asset_pointer", "library_file_id", "file_id", "marker", "event", "type", "kind",
    }
)  # fmt: skip

# route kinds
CONVERSATION = "conversation"
PREPARE = "prepare"
FILES = "files"
PROCESS_UPLOAD = "process_upload"
UPLOAD = "upload"
CONVERSATION_GET = "conversation_get"
CONVERSATION_LIST = "conversation_list"

_POST_KINDS = {
    "/backend-api/f/conversation": CONVERSATION,
    "/backend-api/f/conversation/prepare": PREPARE,
    "/backend-api/files": FILES,
    "/backend-api/files/process_upload_stream": PROCESS_UPLOAD,
}


def route_kind(method: str, host: str, path: str, content_type: str = "") -> str | None:
    """Which ChatGPT endpoint this is, or None for anything this adapter does not handle."""
    method = method.upper()
    h = norm_host(host)
    p = norm_path(path)
    if h == HOST or h.endswith("." + HOST):
        if method == "POST":
            kind = _POST_KINDS.get(p)
            if kind and (not content_type or "json" in content_type.lower()):
                return kind
        elif method == "GET":
            if p == "/backend-api/conversations":
                return CONVERSATION_LIST
            if _CONVERSATION_URL.fullmatch(p):
                return CONVERSATION_GET
    elif h.endswith(FILES_HOST_SUFFIX) and method == "PUT":
        return UPLOAD
    return None


def _referer_chat(referer: str, host: str) -> str | None:
    """The conversation id of a same-origin ``Referer`` (``https://chatgpt.com/c/<id>``), else None."""
    try:
        parts = urlsplit(referer)
    except ValueError:
        return None
    if norm_host(parts.netloc.rsplit("@", 1)[-1]) != norm_host(host):
        return None
    m = _PAGE_CHAT.search(parts.path.strip("/"))
    return m.group(1) if m else None


class ChatGPTWebAdapter:
    name = "chatgpt_web"

    # ------------------------------------------------------------------ routing
    def match(self, method: str, host: str, path: str, content_type: str = "") -> bool:
        return route_kind(method, host, path, content_type) is not None

    def _kind(self, route: Route | None, tree: Any = None) -> str | None:
        """The endpoint of a message: from its route, else from the shape of its JSON body."""
        if route is not None:
            return route_kind(route.method, route.host, route.path, route.content_type)
        if isinstance(tree, dict):
            if isinstance(tree.get("messages"), list):
                return CONVERSATION
            if "partial_query" in tree:
                return PREPARE
            if "file_name" in tree:
                return FILES
        return None

    def chat_key(self, body: bytes, route: Route | None = None) -> str | None:
        """``conversation_id`` of the body; an upload-processing request names its chat in
        ``metadata.library_file_info.origination_thread_id``; an upload registration
        (``POST /files``) names none, so it is attributed through the same-origin ``Referer`` of the
        page the user is on (``/c/<id>``) when there is one."""
        key = self._body_chat_key(body)
        if key is None and route is not None and route.referer:
            if route_kind(route.method, route.host, route.path, route.content_type) == FILES:
                key = _referer_chat(route.referer, route.host)
        return key

    @staticmethod
    def _body_chat_key(body: bytes) -> str | None:
        try:
            tree = json.loads(body)
        except (ValueError, RecursionError):
            return None
        if not isinstance(tree, dict):
            return None
        cid = tree.get("conversation_id")
        if isinstance(cid, str) and cid:
            return cid
        info = (tree.get("metadata") or {}).get("library_file_info")
        thread = info.get("origination_thread_id") if isinstance(info, dict) else None
        return thread if isinstance(thread, str) and thread else None

    def url_chat_key(self, path: str) -> str | None:
        m = _CONVERSATION_URL.fullmatch(norm_path(path))
        return m.group(1) if m else None

    # ------------------------------------------------------------------ uploads (phase 3b.0)
    def file_ids_in_url(self, path: str) -> tuple[str, ...]:
        """The upload ids the URL of a raw-bytes ``PUT`` carries (``/files/<id>/raw`` on the files
        host, plus any ``file_...`` id in the path), to be looked up in the uploads the front end
        attributed to a chat."""
        p = norm_path(path)
        ids: list[str] = []
        m = _UPLOAD_PATH_ID.match(p)
        if m:
            ids.append(m.group(1))
        ids.extend(_FILE_ID.findall(p))
        return tuple(dict.fromkeys(ids))

    def response_file_ids(self, body: bytes) -> tuple[str, ...]:
        """The ids of an upload registration reply (``POST /files``): its ``file_id`` and the id its
        ``upload_url`` carries (the later ``PUT`` names that one)."""
        try:
            tree = json.loads(body)
        except (ValueError, RecursionError):
            return ()
        if not isinstance(tree, dict):
            return ()
        ids: list[str] = []
        fid = tree.get("file_id")
        if isinstance(fid, str) and fid:
            ids.append(fid)
        url = tree.get("upload_url")
        if isinstance(url, str) and url:
            try:
                ids.extend(self.file_ids_in_url(urlsplit(url).path))
            except ValueError:
                pass
        return tuple(dict.fromkeys(ids))

    def attachment_refs(self, body: bytes, route: Route | None = None) -> tuple[str, ...]:
        """The file ids a real send references (``asset_pointer`` parts, ``attachments[].id``): the
        front end refuses a masked chat's send that names a file nobody redacted or checked."""
        try:
            tree = json.loads(body)
        except (ValueError, RecursionError):
            return ()
        if self._kind(route, tree) != CONVERSATION or not isinstance(tree, dict):
            return ()
        messages = tree.get("messages")
        if not isinstance(messages, list):
            return ()
        ids: list[str] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            parts = content.get("parts") if isinstance(content, dict) else None
            for part in parts if isinstance(parts, list) else ():
                pointer = part.get("asset_pointer") if isinstance(part, dict) else None
                if isinstance(pointer, str) and pointer:
                    ids.append(pointer.rsplit("://", 1)[-1])
            metadata = message.get("metadata")
            attachments = metadata.get("attachments") if isinstance(metadata, dict) else None
            for item in attachments if isinstance(attachments, list) else ():
                fid = item.get("id") if isinstance(item, dict) else None
                if isinstance(fid, str) and fid:
                    ids.append(fid)
        return tuple(dict.fromkeys(ids))

    # ------------------------------------------------------------------ request
    def mask_request(self, body: bytes, ctx: MaskContext, route: Route | None = None) -> bytes:
        kind = (
            route_kind(route.method, route.host, route.path, route.content_type) if route else None
        )
        if kind in (CONVERSATION_GET, CONVERSATION_LIST):
            if body:
                raise MaskingFailed("unsupported_format")
            return body
        if kind == UPLOAD:
            return self._upload(body, ctx, route)
        if kind is None:  # no route: only a JSON body whose shape tells the endpoint is understood
            try:
                tree = json.loads(body)
            except (ValueError, RecursionError):
                raise MaskingFailed("unsupported_format") from None
            kind = self._kind(None, tree)
        else:
            tree = load_json(body)
        if kind is None or not isinstance(tree, dict):
            raise MaskingFailed("unsupported_format")
        ctx.scan_marker(body)
        kind_walk = {CONVERSATION: self._conversation, PREPARE: self._prepare}
        return guarded_mask(ctx, lambda: kind_walk.get(kind, self._file_metadata)(tree, ctx))

    def _upload(self, body: bytes, ctx: MaskContext, route: Route | None) -> bytes:
        """The raw PUT of an upload: images are redacted, anything else cannot be read here.
        Redacted bytes are a PNG: the front end sets ``content-type: image/png`` and the new
        ``content-length`` when the bytes changed."""
        ctype = base_type(route.content_type) if route else ""
        if not ctype.startswith("image/") or not body:
            raise MaskingFailed("unsupported_format")
        try:
            out = ctx.image_bytes(body)
        except ImageWithheld:
            raise MaskingFailed("image_withheld") from None
        except MaskingFailed:
            raise
        except Exception:  # noqa: BLE001
            raise MaskingFailed("internal") from None
        ctx.finish()
        return out

    def _conversation(self, tree: dict[str, Any], ctx: MaskContext) -> dict[str, Any]:
        messages = tree.get("messages")
        if not isinstance(messages, list) or not all(isinstance(m, dict) for m in messages):
            raise MaskingFailed("unsupported_format")
        ctx.recognize([s for m in messages for s in _message_texts(m)])
        return walk_dict(tree, ctx, {"messages": lambda v: [self._message(m, ctx) for m in v]})

    def _prepare(self, tree: dict[str, Any], ctx: MaskContext) -> dict[str, Any]:
        query = tree.get("partial_query")
        if isinstance(query, dict):
            ctx.recognize(_message_texts(query))
        elif isinstance(query, str):
            ctx.recognize([query])
        return walk_dict(
            tree,
            ctx,
            {
                "partial_query": lambda v: (
                    self._message(v, ctx) if isinstance(v, dict) else ctx.mask_tree(v)
                )
            },
        )

    def _file_metadata(self, tree: dict[str, Any], ctx: MaskContext) -> dict[str, Any]:
        return walk_dict(tree, ctx, {"file_name": mask_str(ctx)})

    def _message(self, message: dict[str, Any], ctx: MaskContext) -> dict[str, Any]:
        def keep_dict(fn: Any) -> Any:
            return lambda v: fn(v, ctx) if isinstance(v, dict) else VERBATIM

        return walk_dict(
            message,
            ctx,
            {"content": keep_dict(self._content), "metadata": keep_dict(self._metadata)},
        )

    def _content(self, content: dict[str, Any], ctx: MaskContext) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in content.items():
            if key == "parts" and isinstance(value, list):
                out[key] = [self._part(p, ctx) for p in value]
            elif key == "content_type":
                out[key] = value
            else:  # code / text fields of other content types
                out[key] = ctx.mask_tree(value)
        return out

    def _part(self, part: Any, ctx: MaskContext) -> Any:
        if isinstance(part, str):
            return ctx.text(part)
        ctx.check_verbatim(part)  # an asset pointer or other dict part: forwarded as it is
        return part

    def _metadata(self, metadata: dict[str, Any], ctx: MaskContext) -> dict[str, Any]:
        def attachments(value: Any) -> Any:
            return (
                [self._attachment(a, ctx) for a in value] if isinstance(value, list) else VERBATIM
            )

        return walk_dict(metadata, ctx, {"attachments": attachments})

    def _attachment(self, attachment: Any, ctx: MaskContext) -> Any:
        if not isinstance(attachment, dict):
            ctx.check_verbatim(attachment)
            return attachment
        return walk_dict(attachment, ctx, {"name": mask_str(ctx)})

    # ------------------------------------------------------------------ newest user text
    def newest_user_text(self, body: bytes, route: Route | None = None) -> str | None:
        """The text of the last user message of a real send (string parts, joined by a newline).
        None for everything else, in particular the half-typed ``prepare`` draft."""
        try:
            tree = json.loads(body)
        except (ValueError, RecursionError):
            return None
        if self._kind(route, tree) != CONVERSATION or not isinstance(tree, dict):
            return None
        messages = tree.get("messages")
        if not isinstance(messages, list):
            return None
        message = _newest_user_message(messages)
        if message is None:
            return None
        return "\n".join(_message_texts(message)).strip() or None

    def draft_text(self, body: bytes, route: Route | None = None) -> str | None:
        """The half-typed text of a ``prepare`` request (never a command by itself: the front end
        only uses it to keep an exact ``masking on`` / ``masking off`` draft off the wire)."""
        try:
            tree = json.loads(body)
        except (ValueError, RecursionError):
            return None
        if self._kind(route, tree) != PREPARE or not isinstance(tree, dict):
            return None
        query = tree.get("partial_query")
        if isinstance(query, str):
            return query.strip() or None
        if isinstance(query, dict):
            return "\n".join(_message_texts(query)).strip() or None
        return None

    def replace_newest_user_text(self, body: bytes, route: Route | None, new_text: str) -> bytes:
        """``body`` with the text of the newest user message replaced by ``new_text`` (its first
        string part is set, its other string parts are dropped, asset pointers stay); for a
        ``prepare`` request the draft is replaced. The same bytes when there is nothing to replace.
        """

        def edit(tree: Any) -> bool:
            if not isinstance(tree, dict):
                return False
            kind = self._kind(route, tree)
            if kind == PREPARE:
                query = tree.get("partial_query")
                if isinstance(query, str) and query.strip():
                    tree["partial_query"] = new_text
                    return True
                return isinstance(query, dict) and _set_message_text(query, new_text)
            messages = tree.get("messages")
            if kind != CONVERSATION or not isinstance(messages, list):
                return False
            message = _newest_user_message(messages)
            return message is not None and _set_message_text(message, new_text)

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
        kind = (
            route_kind(route.method, route.host, route.path, route.content_type) if route else None
        )
        if route is not None and kind != CONVERSATION:
            return PassthroughRehydrator()  # NDJSON / buffered bodies: not an event stream
        return sse_rehydrator(ChatGPTStream, content_type, content_encoding, ctx, reencode)

    def unmask_response(
        self,
        body: bytes,
        content_type: str,
        content_encoding: str | None,
        ctx: MaskContext,
        route: Route | None = None,
        reencode: bool = True,
    ) -> bytes:
        """A stored conversation or the conversation list (JSON, usually br or gzip)."""
        return unmask_json_response(
            body, content_type, content_encoding, ctx, reencode, RESPONSE_SKIP
        )


def _message_texts(message: dict[str, Any]) -> list[str]:
    """The string parts of a message object (``content.parts``)."""
    content = message.get("content")
    parts = content.get("parts") if isinstance(content, dict) else None
    return [p for p in parts if isinstance(p, str) and p] if isinstance(parts, list) else []


def _newest_user_message(messages: list[Any]) -> dict[str, Any] | None:
    """The last message whose author is the user."""
    for message in reversed(messages):
        author = message.get("author") if isinstance(message, dict) else None
        if isinstance(author, dict) and author.get("role") == "user":
            return message  # type: ignore[no-any-return]
    return None


def _set_message_text(message: dict[str, Any], new_text: str) -> bool:
    """Put ``new_text`` in a message's string parts (False when it has none)."""
    content = message.get("content")
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(content, dict) or not isinstance(parts, list) or not _message_texts(message):
        return False
    content["parts"] = replace_part_texts(parts, new_text)
    return True


# ---------------------------------------------------------------------- response stream


class ChatGPTStream(HoldStream):
    """Rehydrates the ``/backend-api/f/conversation`` event stream (protocol ``v1``).

    Events: ``data: "v1"``; JSON patch events ``{"p": path, "o": "add"|"append"|"replace"|"patch",
    "v": value}`` (an ``add`` carries a whole ``message``; ``patch`` batches a list of operations);
    a bare ``{"v": "text"}`` continues the text of the last ``append`` path; typed events
    (``input_message``, ``title_generation``, ``message_marker``, ``resume_conversation_token``,
    ``message_stream_complete`` ...).

    * text appended to one path is rehydrated split-safe (``TailHold``); the tail that is held back
      is flushed as ONE extra ``{"p": path, "o": "append", "v": tail}`` event when another path or
      operation follows (inside a ``patch`` batch: as an extra operation in front of it), at
      ``message_stream_complete``, ``[DONE]`` and the end of the stream;
    * every other string value (a whole ``message``, ``input_message``, ``title``) is unmasked with
      ids and tokens left alone; every other event passes through byte-identical;
    * a ``conversation_id`` seen in the stream goes to ``ctx.report_chat_id`` (first-turn chats).

    Nothing is ever dropped or reordered; an event without pseudonyms is forwarded unchanged.
    """

    def __init__(self, ctx: MaskContext, content_encoding: str | None, reencode: bool) -> None:
        super().__init__(ctx, content_encoding, reencode)
        self._hold: TailHold | None = None
        self._hold_path: str | None = None
        self._path: str | None = None  # path of the last append: a bare "v" continues it

    # -- events
    def _event(self, raw: bytes, delim: bytes) -> bytes:
        got = self._read(raw, delim)
        if isinstance(got, bytes):
            return got
        lines, payload = got
        if not isinstance(payload, dict):
            return raw  # the "v1" protocol marker and other bare values
        self.stats["recognised"] += 1
        self._report(payload)
        kind = payload.get("type")
        pre = b""
        changed = False
        if kind is None and "v" in payload:
            extra, changed = self._operation(payload)
            pre = b"".join(self._frame(op) + delim for op in extra)
        elif kind == "input_message":
            changed = self._swap(payload, "input_message", self._unmask_obj)
        elif kind == "title_generation":
            changed = self._swap(payload, "title", self._ctx.unmask)
        elif kind == "message_stream_complete" or (
            kind == "message_marker" and payload.get("event") == "last"
        ):
            pre = self._flush(delim)
        if not changed:
            return pre + raw
        self.stats["rehydrated_events"] += 1
        return pre + rebuild_event(lines, payload, self.eol)

    def _report(self, payload: dict[str, Any]) -> None:
        value = payload.get("v")
        for source in (payload, value if isinstance(value, dict) else None):
            if source is not None:
                self._ctx.report_chat_id(source.get("conversation_id"))

    @staticmethod
    def _swap(payload: dict[str, Any], key: str, convert: Any) -> bool:
        old = payload.get(key)
        if old is None:
            return False
        payload[key] = convert(old)
        return bool(payload[key] != old)

    def _unmask_obj(self, value: Any) -> Any:
        return self._ctx.unmask_obj(value, RESPONSE_SKIP)

    # -- one operation: an event itself, or one entry of a patch batch
    def _operation(self, op: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
        """Convert ``op`` in place. Returns (extra operations that must come BEFORE it, changed)."""
        value = op.get("v")
        kind = op.get("o")
        path = op.get("p") if isinstance(op.get("p"), str) and op.get("p") else None
        if kind == "patch" and isinstance(value, list):
            out: list[Any] = []
            changed = False
            for sub in value:
                if not isinstance(sub, dict):
                    out.append(sub)
                    continue
                extra, sub_changed = self._operation(sub)
                out.extend(extra)
                out.append(sub)
                changed = changed or sub_changed or bool(extra)
            op["v"] = out
            return [], changed
        if isinstance(value, str) and (kind == "append" or (kind is None and path is None)):
            return self._append(op, value, path or self._path)
        extra = self._flush_op(drop=True)
        if value is None:
            return extra, bool(extra)
        new = self._unmask_obj(value)
        op["v"] = new
        return extra, bool(extra) or new != value

    def _append(
        self, op: dict[str, Any], piece: str, path: str | None
    ) -> tuple[list[dict[str, Any]], bool]:
        extra: list[dict[str, Any]] = []
        if self._hold is not None and self._hold_path != path:
            extra = self._flush_op(drop=True)
        if self._hold is None:
            self._hold = TailHold(self._smap, "plain")
            self._hold_path = path
        self._path = path
        new = self._hold.feed(piece)
        op["v"] = new
        return extra, bool(extra) or new != piece

    # -- flushing
    def _flush_op(self, drop: bool = False) -> list[dict[str, Any]]:
        """The operation that carries the held tail (empty list: nothing held)."""
        hold = self._hold
        if hold is None:
            return []
        out: list[dict[str, Any]] = []
        if hold.held:
            tail = hold.flush()
            out = [{"p": self._hold_path or "", "o": "append", "v": tail}]
            self.stats["flushed_tails"] += 1
        self.stats["tokens"] += hold.tokens
        self.stats["errors"] += hold.errors
        hold.tokens = hold.errors = 0
        if drop:
            self._hold = None
            self._hold_path = None
        return out

    def _frame(self, op: dict[str, Any]) -> bytes:
        return frame_event("delta", op, self.eol)

    def _flush(self, delim: bytes) -> bytes:
        return b"".join(self._frame(op) + delim for op in self._flush_op())

    def _end(self, delim: bytes) -> bytes:
        return self._flush(delim)
