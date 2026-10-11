"""Request masking of the gateway: ``mask_request(body, ctype, state, memo) -> bytes``.

Every string leaf of the JSON request body is masked through the shared ``SessionMap``, and so is
every dict key INSIDE a ``tool_use`` ``input`` (arbitrary user data). Keys everywhere else are the
Anthropic API's own field names and tool-schema property names: masking them would corrupt the
request whenever the dictionary holds a word such as ``content``. Exceptions to the leaf rule:

* ``thinking`` / ``redacted_thinking`` blocks are returned byte-identical (the API checks their
  signature) - but only when the thinking text holds no dictionary value. A block that does (it
  was recorded while masking was off, or before the folder was routed) is DROPPED, and with it
  every other thinking block of the same content list: the signature could not verify any more,
  and Claude Code recovers by resending without thinking. The request is not failed;
* image / document blocks (a dict with a ``source`` object) go to ``images.redact_image_block``
  (policy ``client_gateway.images``); the result is cached by content, so the same screenshot in
  the history is read once and the bytes stay identical from turn to turn;
* structural enum values are not masked, so the request stays valid when the dictionary happens to
  hold a word such as ``text`` or ``High``: the value of ``type`` when it is a known Anthropic /
  JSON-schema type word, a message ``role``, ``cache_control`` anywhere, and the top-level API
  settings in ``TOP_PASS`` (``model``, ``thinking``, ``output_config``, ``service_tier``,
  ``tool_choice``, ``metadata`` ...), which are never client text. Everything else at the top
  level (``messages``, ``tools`` and any unknown key) is masked. NOTHING inside the
  ``input`` of a ``tool_use`` block is exempt: that is user data;
* the top-level ``system`` field: its leading short blocks (the billing line and the app's
  identity sentence, ``SYSTEM_IDENTITY_MAX``) are forwarded byte-identical and never a reason to
  refuse: Anthropic checks them for a subscription (Claude Code) login, and a system prompt whose
  identity sentence was rewritten got HTTP 429 on EVERY request (2026-10-08); a client value there
  is only counted (``system_values_passed``). The instructions block after them holds the app's
  environment (project folder, memory path, MCP server instructions) and is masked with the
  developer-text rule (``system_blocks_masked``); a vendor accepts any instructions after the
  identity block. Conversation text (CLAUDE.md, files, tool results, what the user types) is in
  ``messages`` and is masked.

Opaque ``signature`` / ``data`` payloads need no rule of their own: a base64 blob that holds no
dictionary value comes back unchanged, and one that does is masked (never leaked).

For each string the memo is tried first (``memo.lookup(s)`` = the exact masked text the model saw
for that text), then the per-dictionary-version string cache, then ``smap.mask_text``. A residual
leak check (``smap.count_leaks``) runs PER STRING LEAF on exactly the text that was produced by
masking (or replayed from the memo), never on the serialised JSON (where an escape such as ``\\n``
reads as text and invents hits).

Serialisation is deterministic (``ensure_ascii=False``, compact separators), so the same input
always gives the same bytes (prompt cache, preserved thinking).

Any failure raises ``MaskingFailed(code)``; the message is fixed and never contains input text.
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
from typing import Any

from client_gateway import images as _images
from client_gateway import rows as _rows
from client_gateway.memo import RemaskMemo
from client_gateway.reminders import recognition_texts
from client_gateway.state import GatewayState
from core.client_guard.engine import MaskingError

THINKING_TYPES = frozenset({"thinking", "redacted_thinking"})
# top-level request fields that are API settings (enums, numbers, ids), never client text; a
# dictionary word such as "High" or "Standard" must not turn effort / service_tier into an
# invalid value
TOP_PASS = frozenset(
    {
        "model", "max_tokens", "stream", "temperature", "top_p", "top_k", "service_tier",
        "thinking", "output_config", "tool_choice", "metadata", "container", "context_management",
        "betas", "speed", "inference_geo", "anthropic_version", "anthropic_beta", "mcp_servers",
    }
)  # fmt: skip
PASS_ANYWHERE = frozenset({"cache_control"})
# inside the tool definitions: the JSON-schema keyword values are the API contract, kept
# verbatim (a dictionary word such as "boolean" or "Decimal" in a ``type`` list or an ``enum``
# made Anthropic reject the whole request: "input_schema: JSON schema is invalid");
# descriptions, titles and the tool name are still masked
SCHEMA_KEEP = frozenset(
    {
        "type",
        "enum",
        "const",
        "required",
        "format",
        "pattern",
        "$ref",
        "$schema",
        "$id",
        "default",
        "examples",
    }
)
THINKING_REMOVED = "[Client masking: a reasoning block that held client data was removed.]"
# the leading system blocks up to this length are the app's billing / identity lines (Claude Code:
# "x-anthropic-billing-header: ...", "You are Claude Code, Anthropic's official CLI for Claude."):
# kept byte-identical, see _Masker._system. Its instructions block is several thousand characters.
SYSTEM_IDENTITY_MAX = 400
_DROP: Any = object()
IMAGE_TYPES = frozenset({"image", "document"})
TOOL_USE_TYPES = frozenset({"tool_use", "server_tool_use", "mcp_tool_use"})
# every role the Messages API accepts (Claude Code sends "system" messages too since Oct 2026: a
# masked role was rejected with HTTP 400 "Unexpected role"); a role is never client data
ROLES = frozenset({"user", "assistant", "system", "developer", "tool"})
STRUCT_TYPES = frozenset(
    {
        # content blocks and sources
        "text", "image", "document", "tool_use", "tool_result", "thinking", "redacted_thinking",
        "server_tool_use", "mcp_tool_use", "mcp_tool_result", "web_search_tool_result",
        "web_search_result", "search_result", "base64", "url", "file", "ephemeral", "custom",
        # tool_choice / thinking config
        "auto", "any", "tool", "none", "enabled", "disabled",
        # JSON schema
        "object", "array", "string", "number", "integer", "boolean", "null",
    }
)  # fmt: skip

TEXT_TYPES = ("text/", "application/x-ndjson", "application/xml")
TRANSIENT_WITHHELD = frozenset(
    {_images.W_OCR_FAILED, _images.W_OCR_TIMEOUT, _images.W_MATCH_FAILED}
)

CODES = (
    "bad_json",
    "unsupported_body",
    "mask_error",
    "residual_leak",
    "too_deep",
    "internal",
    "dictionary_not_ready",
    "dictionary_empty",
    "dictionary_incomplete",
    "ner_incomplete",
    # wire adapters (client_gateway/adapters): append only, the index is part of a status code
    "unsupported_format",
    "image_withheld",
    "attachment_unchecked",
)


class MaskingFailed(Exception):
    """The request cannot be masked safely. ``code`` is a fixed label; the message never holds
    input text."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"client data could not be masked safely ({code})")


def _placeholder() -> dict[str, str]:
    return {"type": "text", "text": THINKING_REMOVED}


def dumps(obj: Any) -> bytes:
    """Deterministic compact JSON as UTF-8 (falls back to ASCII escapes for lone surrogates)."""
    try:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except UnicodeEncodeError:
        return json.dumps(obj, ensure_ascii=True, separators=(",", ":")).encode("ascii")


class _Masker:
    def __init__(self, state: GatewayState, memo: RemaskMemo, ner_degrade: bool = False) -> None:
        self.state = state
        self.memo = memo
        self.ner_degrade = ner_degrade  # the user's explicit "dictionary only" for this chat
        self._clean: set[str] = set()  # exempt strings already checked in this request
        self.smap = state.smap
        self.cache = state.mask_cache
        self.masked = 0
        self.thinking_dropped = 0
        self.memo_hits = 0
        self.images = {"redacted": 0, "withheld": 0, "clean": 0}
        self._whole = True  # False while walking the developer-written tool definitions
        self._pre_recognized = False
        # True while masking a string that is an MCP result the authority already pseudonymised
        # (wire adapters set it for a string that carried the PBI marker): its pseudonyms stay
        self._premasked = False

    def learn_tables(self, tree: Any) -> None:
        """Text cells of data tables in the new content (a model the gateway never read, a
        pasted table) become dictionary values before anything is masked (``rows``)."""
        try:
            cells = _rows.new_table_cells(tree)
            learned = (
                self.smap.add_values(self.smap.dimension_prefix, cells, "table") if cells else 0
            )
        except Exception:  # noqa: BLE001 - fail closed before anything can be forwarded
            raise MaskingFailed("mask_error") from None
        self.state.bump("table_values_learned", learned)

    def pre_recognize(self, tree: Any) -> None:
        """Run NER on what is NEW in this request, before anything is masked: the last message
        (the user's text or the tool results of the last round), and, the first time this chat
        masks a request (after a gateway restart, a ``--resume`` or ``masking off`` then ``on``:
        the memo is empty), the WHOLE history once, because names an earlier gateway or an earlier
        switch-off never recognized are not in the dictionary. Retries resume from the recognizer's
        segment cache.

        Static instruction text Claude Code injects as ``<system-reminder>`` is left out, but
        attached files, IDE selections and hook output are read (``reminders``)."""
        texts: list[str] = []
        older: list[str] = []
        messages = tree.get("messages") if isinstance(tree, dict) else None
        whole_history = not self.memo.history_scanned
        if isinstance(messages, list) and messages:
            for message in messages[-1:]:
                self._collect_texts(message, 2, False, texts)
            if whole_history:
                for message in messages[:-1]:
                    self._collect_texts(message, 2, False, older)
        # an MCP result (it carries the PBI marker) was masked by the masking authority already
        pieces = [p for s in texts if not self._carries_marker(s) for p in recognition_texts(s)]
        if whole_history:
            # the earlier conversation: best effort, never blocks the new message
            recognize_history(self.state, [p for s in older for p in recognition_texts(s)])
            self.memo.history_scanned = True
        recognize_new_text(self.state, pieces)
        self._pre_recognized = True

    def _collect_texts(self, obj: Any, depth: int, in_input: bool, out: list[str]) -> None:
        if isinstance(obj, str):
            if obj:
                out.append(obj)
        elif isinstance(obj, list):
            for item in obj:
                self._collect_texts(item, depth + 1, in_input, out)
        elif isinstance(obj, dict):
            kind = obj.get("type")
            if isinstance(kind, str) and not in_input:
                if kind in THINKING_TYPES or (
                    kind in IMAGE_TYPES and isinstance(obj.get("source"), dict)
                ):
                    return
            for key, value in obj.items():
                if in_input and isinstance(key, str) and key:
                    out.append(key)
                if not in_input and (
                    key in PASS_ANYWHERE
                    or (depth == 0 and key in TOP_PASS)
                    or (isinstance(value, str) and self._structural(key, value))
                ):
                    continue
                if depth == 0 and key in ("tools", "system"):
                    # Static setup text still gets dictionary/shape masking in walk().
                    # NER is reserved for user and tool content that can introduce new names.
                    continue
                value_in_input = in_input or (key == "input" and kind in TOOL_USE_TYPES)
                self._collect_texts(value, depth + 1, value_in_input, out)

    # ------------------------------------------------------------------ strings
    def _carries_marker(self, s: str) -> bool:
        """The string is an MCP result stamped with this install's PBI marker: the masking
        authority already pseudonymised it (the Claude Code path keeps the marker in the string; the
        caller strips it from the final body). A spoofed marker only keeps pseudonym-shaped
        strings as they are; every real value in the string is still masked."""
        marker = getattr(self.state, "marker", "")
        return bool(marker) and marker in s

    def text(self, s: str) -> str:
        """Mask one string (memo, then cache, then the engine) and leak-check what is produced."""
        if not s:
            return s
        whole = self._whole
        hit = self.memo.lookup(s) if whole else None
        if hit is not None and self.smap.count_leaks(hit) == 0:
            self.memo_hits += 1
            return hit
        version = self.smap.revision
        premasked = self._premasked or (whole and self._carries_marker(s))
        ckey: Any = (s, "premasked") if premasked else (s if whole else (s, "tools"))
        cached = self.cache.get(ckey)
        if cached is not None and (
            cached[0] == version or not self.smap.changed_since(s, cached[0])
        ):
            if cached[0] != version:  # still valid: re-stamp, so the next check is free
                self.cache.put(ckey, (version, cached[1], cached[2]), len(s) + len(cached[1]))
            self.masked += cached[2]
            return cached[1]
        try:
            out, n = (
                self.smap.mask_text(s, recognize=not self._pre_recognized, premasked=premasked)
                if whole
                else self.smap.mask_text(s, whole=False, premasked=premasked)
            )
        except MaskingError:
            raise MaskingFailed("mask_error") from None
        if n:
            if self.smap.count_leaks(out) if whole else self.smap.count_leaks(out, whole=False):
                raise MaskingFailed("residual_leak")
            self.masked += n
        if self.smap.revision == version:  # a shape hit grew the dictionary: do not cache
            self.cache.put(ckey, (version, out, n), len(s) + len(out))
        return out

    # ------------------------------------------------------------------ tree
    def walk(self, obj: Any, depth: int = 0, in_input: bool = False) -> Any:
        if isinstance(obj, str):
            return self.text(obj)
        if isinstance(obj, dict):
            return self._dict(obj, depth, in_input)
        if isinstance(obj, list):
            return self._list(obj, depth, in_input)
        return obj

    def _list(self, obj: list[Any], depth: int, in_input: bool) -> list[Any]:
        out = [self.walk(v, depth + 1, in_input) for v in obj]
        if not any(v is _DROP for v in out):
            return out
        # a thinking block was dropped: its siblings would not verify either
        before = len(out)
        kept = [
            v for v in out
            if v is not _DROP and not (isinstance(v, dict) and v.get("type") in THINKING_TYPES)
        ]  # fmt: skip
        self.thinking_dropped += before - len(kept)
        return kept or [_placeholder()]

    def _thinking_leaks(self, block: dict[Any, Any]) -> bool:
        """True when a thinking block holds a dictionary value (checked per string field, with the
        opaque signature / data payloads left out)."""
        for key, value in block.items():
            if not isinstance(value, str) or key in ("type", "signature", "data") or not value:
                continue
            raw = value.encode("utf-8", "surrogatepass")
            digest = hashlib.blake2b(raw, digest_size=16).digest()
            version = self.smap.revision
            cached = self.cache.get(("thinking", digest))
            if cached is not None and (
                cached[0] == version or not self.smap.changed_since(value, cached[0])
            ):
                leaks = cached[1]
            else:
                leaks = self.smap.count_leaks(value) > 0
                self.cache.put(("thinking", digest), (version, leaks), 64)
            if leaks:
                return True
        return False

    def _dict(self, obj: dict[Any, Any], depth: int, in_input: bool) -> dict[Any, Any]:
        kind = obj.get("type")
        if isinstance(kind, str) and not in_input:
            if kind == "thinking":
                return _DROP if self._thinking_leaks(obj) else obj
            if kind == "redacted_thinking":
                return obj  # opaque: kept with its turn, dropped with a sibling that is dropped
            if kind in IMAGE_TYPES and isinstance(obj.get("source"), dict):
                return self._image(obj)
        out: dict[Any, Any] = {}
        for key, value in obj.items():
            if depth == 0 and key == "system" and not in_input and "messages" in obj:
                out[key] = self._system(value)  # identity lines verbatim (module docstring)
                continue
            new_key = self.text(key) if in_input and isinstance(key, str) else key
            if not in_input and self._whole:
                self._leak_check(key)  # a key is never rewritten: it must not hold a value
            if not in_input and (
                key in PASS_ANYWHERE
                or (depth == 0 and key in TOP_PASS)
                or (not self._whole and key in SCHEMA_KEEP)
                or (isinstance(value, str) and self._structural(key, value))
            ):
                if self._whole and not (isinstance(value, str) and self._structural(key, value)):
                    self._leak_check(value)  # exempt from the rewrite, not from the check
                out[new_key] = value
                continue
            value_in_input = in_input or (key == "input" and kind in TOOL_USE_TYPES)
            if depth == 0 and key == "tools":
                # tool definitions are developer text: phrases are masked, but a lone keyword
                # ("status", "include") is not a client cell
                self._whole = False
                try:
                    walked = self.walk(value, depth + 1, value_in_input)
                finally:
                    self._whole = True
            else:
                walked = self.walk(value, depth + 1, value_in_input)
            out[new_key] = _placeholder() if walked is _DROP else walked
        return out

    def _system(self, value: Any) -> Any:
        """The top-level ``system`` field: its LEADING short text blocks (at most
        ``SYSTEM_IDENTITY_MAX`` characters each: the billing line and the app's identity sentence,
        which the vendor checks for a subscription login) are forwarded byte-identical, a client
        value in them is only counted (``system_values_passed``); the first longer block and
        everything after it (instructions, folder paths, memory paths, MCP server instructions) is
        masked with the developer-text rule, like the tool definitions. A system field that is
        one string is one block."""
        blocks = value if isinstance(value, list) else [value]
        out: list[Any] = []
        leading = True
        for block in blocks:
            text = block.get("text") if isinstance(block, dict) else block
            if leading and isinstance(text, str) and len(text) <= SYSTEM_IDENTITY_MAX:
                if self._system_has_values(block):
                    self.state.bump("system_values_passed")
                out.append(block)
                continue
            leading = False
            self._whole = False
            try:
                walked = self.walk(block, 1, False)
            finally:
                self._whole = True
            out.append(_placeholder() if walked is _DROP else walked)
            self.state.bump("system_blocks_masked")
        return out if isinstance(value, list) else out[0]

    def _system_has_values(self, obj: Any, depth: int = 0) -> bool:
        if isinstance(obj, str):
            return len(obj) >= 3 and bool(self.smap.exempt_leaks(obj))
        if depth < 20 and isinstance(obj, dict):
            return any(self._system_has_values(v, depth + 1) for v in obj.values())
        if depth < 20 and isinstance(obj, list):
            return any(self._system_has_values(v, depth + 1) for v in obj)
        return False

    def _leak_check(self, obj: Any, depth: int = 0) -> None:
        """Check-only pass over text that is copied verbatim (API settings, ``metadata``, dict keys
        outside a tool input): an identity value or a shape in it fails the request closed instead
        of leaving the machine. The tool-schema keywords (``_whole`` False) are the API contract and
        are not checked: masking them is what broke requests before, and a client name as a schema
        keyword is the developer's own text."""
        if isinstance(obj, str):
            if len(obj) < 3 or obj in self._clean:
                return
            if self.smap.exempt_leaks(obj):
                raise MaskingFailed("residual_leak")
            if len(self._clean) < 100_000:
                self._clean.add(obj)
        elif depth < 40 and isinstance(obj, dict):
            for key, value in obj.items():
                self._leak_check(key, depth + 1)
                self._leak_check(value, depth + 1)
        elif depth < 40 and isinstance(obj, list):
            for item in obj:
                self._leak_check(item, depth + 1)

    @staticmethod
    def _structural(key: Any, value: str) -> bool:
        if key == "type":
            return value in STRUCT_TYPES
        return key == "role" and value in ROLES

    # ------------------------------------------------------------------ images
    def _image(self, block: dict[str, Any]) -> dict[str, Any]:
        state = self.state
        policy = state.images_policy
        if policy == "pass":
            self.images["clean"] += 1
            return block
        digest = hashlib.sha256(
            json.dumps(block, sort_keys=True, ensure_ascii=False).encode("utf-8", "surrogatepass")
        ).digest()
        version = self.smap.revision
        key = (digest, policy)
        cached = state.image_cache.get(key)
        if cached is not None and (
            cached[0] == version
            or (
                len(cached) > 3
                and cached[3] is not None
                and not self.smap.changed_since(cached[3], cached[0], normalised=True, loose=True)
            )
        ):
            # an image is only redone when a value added since then occurs in its OCR text
            self._count_image(cached[2])
            return cached[1]
        st: dict[str, Any] = {}
        out = _images.redact_image_block(block, self.smap, policy, stats=st)
        outcome = (
            "withheld" if st.get("withheld") or out.get("type") == "text"
            else "redacted" if st.get("hits")
            else "clean"
        )  # fmt: skip
        self._count_image(outcome)
        if outcome != "withheld" or st.get("withheld") not in TRANSIENT_WITHHELD:
            data = block.get("source", {}).get("data")
            cost = (len(data) if isinstance(data, str) else 0) + 1024
            state.image_cache.put(key, (version, out, outcome, st.get("ocr_norm")), cost)
        return out

    def _count_image(self, outcome: str) -> None:
        self.images[outcome] += 1


GUARD_NER_BUDGET_S = 0.5  # model time per request for the safety net (unmasked chats)


def client_values_in_last_message(body: bytes, ctype: str, state: GatewayState) -> dict[str, int]:
    """Client data in the NEWEST message of a request (the user's text or the last tool results),
    without the ``<system-reminder>`` text Claude Code adds. For chats in which masking is off
    only because nobody switched it on. Three checks, cheapest first, stopping at the first hit:

    1. known identity values (dictionary, remembered values, client list, name parts): ~1 ms;
    2. names nobody knows yet: the NER worker on the prose of the message, at most
       ``GUARD_NER_BUDGET_S`` and only when the worker is already running (never waits for it);
    3. screenshots: Windows OCR (fast engine only) of each image, matched against the dictionary
       and run through NER; once per image (cached by content).

    Nothing is changed or added to the dictionary. Returns ``{kind: n}`` (PER / ORG / CUS ... or
    IMG); never raises (anything unreadable counts as nothing found)."""
    if "json" not in (ctype or "").lower():
        return {}
    try:
        tree = json.loads(body)
        messages = tree.get("messages") if isinstance(tree, dict) else None
        if not isinstance(messages, list) or not messages:
            return {}
        last = messages[-1]
        if not isinstance(last, dict) or last.get("role") != "user":
            return {}
        texts: list[str] = []
        images: list[dict[str, Any]] = []

        def collect(obj: Any) -> None:
            if isinstance(obj, str):
                texts.append(obj)
            elif isinstance(obj, list):
                for item in obj:
                    collect(item)
            elif isinstance(obj, dict):
                if obj.get("type") == "image" and isinstance(obj.get("source"), dict):
                    images.append(obj)
                    return
                if obj.get("type") in ("document", "thinking", "redacted_thinking"):
                    return
                for key, value in obj.items():
                    if key not in ("type", "id", "tool_use_id", "cache_control", "signature"):
                        collect(value)

        collect(last.get("content"))
        texts = [piece for s in texts for piece in recognition_texts(s)]
        found: dict[str, int] = {}
        if state.smap.has_values:
            for text in texts:
                for prefix, n in state.smap.identity_hits(text).items():
                    found[prefix] = found.get(prefix, 0) + n
        if found:
            return found
        ner = _guard_ner(state, wanted=bool(texts))
        if ner is not None and texts:
            for _surface, prefix in ner.find_many(texts, budget_s=GUARD_NER_BUDGET_S, wait=False):
                found[prefix] = found.get(prefix, 0) + 1
            if found:
                return found
        for block in images:
            if _guard_image(block, state, ner):
                found["IMG"] = found.get("IMG", 0) + 1
        return found
    except Exception:  # noqa: BLE001 - a safety net never breaks a passthrough request
        return {}


def _guard_ner(state: GatewayState, wanted: bool = False) -> Any:
    """The name recognizer for the safety net, when it is ready. A gateway that only passes traffic
    through starts no model at gateway start: the first message with prose in it starts the worker
    in the background (never waits for it), and an idle worker is unloaded again."""
    detectors = getattr(state, "detectors", None)
    ner = getattr(detectors, "ner", None) if detectors is not None else None
    if ner is None or not getattr(state, "guard_ner", True):
        return None
    if not ner.ready():
        if wanted:
            ner.start()  # background, idempotent
        return None  # not running (yet): never wait for it from a passthrough request
    return ner


def _guard_image(block: dict[str, Any], state: GatewayState, ner: Any) -> bool:
    """Does this screenshot show client data? Fast OCR only; cached per image and revision."""
    data = block.get("source", {}).get("data")
    if not isinstance(data, str) or not data:
        return False
    digest = hashlib.sha256(data.encode("ascii", "ignore")).digest()
    key = ("guard_image", digest)
    version = state.smap.revision
    cached = state.image_cache.get(key)
    if cached is not None and cached[0] == version:
        return bool(cached[1])
    hit = False
    try:
        img = _images._decode(base64.b64decode(data))
        if min(img.size) >= _images.MIN_SIDE:
            st: dict[str, Any] = {"engines": [], "passes": 0, "words_first_pass": 0}
            passes = _images._collect_passes(img, ["winrt"], st)
            if state.smap.has_values:
                hit = bool(_images._find_hits(img, state.smap, ["winrt"], st, recognize_text=False))
            if not hit and ner is not None and passes:
                lines = _images._group_lines(passes[0])
                text = "\n".join(" ".join(w.text for w in line) for line in lines)
                hit = bool(ner.find_many([text], budget_s=GUARD_NER_BUDGET_S, wait=False))
    except Exception:  # noqa: BLE001 - unreadable: the masked path would withhold it anyway
        hit = False
    state.image_cache.put(key, (version, hit), 256)
    return hit


HISTORY_INLINE_BUDGET_S = 2.0  # the quick pass over the earlier conversation, inside the request
HISTORY_BACKGROUND_CAP_S = 600.0  # the rest of it, in the background, at most this long
HISTORY_BATCH = 8  # pieces per background call (a call recognizes a bounded number of new segments)


def recognize_new_text(state: GatewayState, pieces: list[str]) -> None:
    """Name recognition over the NEW text of a request (the user's text, the last tool results):
    all of it within the recognizer's ``max_s``, else what finished in time, and the request goes
    on. Owner rule 2026-10-07: a masked chat must keep working at normal speed. A strict scan that
    refused or held the request made a chat with masking on hang on every message. Every value
    the dictionary holds (the models read, remembered names, the client list) is still masked and
    the whole-request leak scan still runs; what was not recognized in time is checked on the next
    request (finished segments are cached). MCP results carry the PBI marker and are left out:
    the masking authority already masked them. Raises ``MaskingFailed`` only on a real error."""
    if not pieces:
        return
    try:
        state.smap.recognize_many(pieces)
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ != "NerIncomplete":
            raise MaskingFailed("mask_error") from None
        state.bump("ner_partial")
        # what did finish is in the recognizer's cache: collect it (no new model time)
        state.smap.recognize_best_effort(pieces, 0.0)


def recognize_history(state: GatewayState, pieces: list[str]) -> None:
    """Name recognition over the EARLIER conversation of a chat that is masked for the first time
    (after ``masking on``, a gateway restart or a ``--resume``). Never blocks the new message and
    never fails it: that history already left this machine in earlier turns (or was masked then,
    and the remembered dictionary still holds what was recognized), and every known client value in
    it is masked by the dictionary anyway. A quick pass runs inline; the rest continues in a
    background thread, and whatever it finds is masked from the next request on. Before
    2026-10-07 this scan was strict, so a long chat (hours of history) never finished it and every
    message was refused with "send it again"."""
    if not pieces:
        return
    smap = state.smap
    smap.recognize_best_effort(pieces, HISTORY_INLINE_BUDGET_S)
    state.bump("history_scans")

    def _rest() -> None:
        deadline = time.monotonic() + HISTORY_BACKGROUND_CAP_S
        with _HISTORY_SLOT:  # one history scan at a time, however many chats need one
            _scan(deadline)

    def _scan(deadline: float) -> None:
        for start in range(0, len(pieces), HISTORY_BATCH):
            if time.monotonic() > deadline:
                state.bump("history_scans_capped")
                return
            smap.recognize_best_effort(pieces[start : start + HISTORY_BATCH], 30.0)  # noqa: E203

    _background(_rest)


_HISTORY_SLOT = threading.Lock()


def _background(fn: Any) -> None:
    """Run ``fn`` in a daemon thread (tests replace this to run it inline)."""
    threading.Thread(target=fn, name="gateway-history-scan", daemon=True).start()


def mask_request(
    body: bytes, ctype: str, state: GatewayState, memo: RemaskMemo, ner_degrade: bool = False
) -> bytes:
    """The request body as it may leave this machine. Raises ``MaskingFailed``.
    ``ner_degrade``: the user switched this chat to dictionary-only masking (name recognition
    that cannot finish is skipped instead of refusing the request)."""
    if not body:
        return body
    base = (ctype or "").split(";", 1)[0].strip().lower()
    masker = _Masker(state, memo, ner_degrade)
    try:
        if base == "application/json" or base.endswith("+json"):
            try:
                tree = json.loads(body)
            except (ValueError, RecursionError):
                raise MaskingFailed("bad_json") from None
            masker.learn_tables(tree)
            masker.pre_recognize(tree)
            walked = masker.walk(tree)
            out = dumps(_placeholder() if walked is _DROP else walked)
        elif base.startswith(TEXT_TYPES):
            out = _mask_plain(masker, body)
        else:
            raise MaskingFailed("unsupported_body")
    except MaskingFailed:
        raise
    except RecursionError:
        raise MaskingFailed("too_deep") from None
    except Exception:  # noqa: BLE001 - fail closed; nothing from the failure is surfaced
        raise MaskingFailed("internal") from None
    state.bump("masked_strings", masker.masked)
    state.bump("thinking_dropped", masker.thinking_dropped)
    state.bump("memo_hits", masker.memo_hits)
    state.bump("images_redacted", masker.images["redacted"])
    state.bump("images_withheld", masker.images["withheld"])
    state.bump("images_clean", masker.images["clean"])
    return out


def _mask_plain(masker: _Masker, body: bytes) -> bytes:
    """A plain-text body: masked as one string."""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise MaskingFailed("unsupported_body") from None
    return masker.text(text).encode("utf-8")


def mask_strings(
    texts: list[str], state: GatewayState, whole: bool = True
) -> tuple[list[str], int]:
    """Strings as they may leave this machine, for the MCP's authority route: ``(masked texts,
    number of values masked)``. The same engine path as a request leaf: recognizers first (all of
    it, or ``ner_incomplete``), then the string cache and ``smap.mask_text``, then the residual
    leak check per string. There is no per-chat memo (a result is not part of a conversation).

    ``whole=False`` is the developer-text rule of ``mask_text``: whole-string-only values (a lone
    word, a short cell) are not matched and the recognizers do not run.

    ALL or nothing: any failure raises ``MaskingFailed(code)`` and no partial list exists."""
    masker = _Masker(state, RemaskMemo())
    masker._whole = whole
    try:
        if whole:
            recognize_new_text(state, [t for t in texts if t])
            masker._pre_recognized = True
        out = [masker.text(t) for t in texts]
    except MaskingFailed:
        raise
    except RecursionError:
        raise MaskingFailed("too_deep") from None
    except Exception:  # noqa: BLE001 - fail closed; nothing from the failure is surfaced
        raise MaskingFailed("internal") from None
    state.bump("masked_strings", masker.masked)
    return out, masker.masked
