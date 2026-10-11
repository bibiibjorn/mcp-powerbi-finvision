"""The MCP masking layer: what a tool returns is masked before it leaves this process.

Off by default (config ``client_gateway.mcp_masking`` or env ``FINVISION_MCP_MASKING=1``): while it
is off every function here is the identity and no call reaches the gateway. Masking on demand (owner
rule 2026-10-08): even with the flag on, a result is masked only while masking is ON for this MCP's
own chat (``CLAUDE_CODE_SESSION_ID``, asked from the gateway; without a session id: while a gateway
runs). No gateway running = masking off = results as they are. When on, the gateway is the one
masking authority (``/__gateway/mask|unmask|mask_image``, see ``client_gateway``):

* a tool RESULT has every string leaf and dict key masked in ONE call, gets the Power BI chat marker
  (``"_fv": "fvpbi-<id>"``, first member of a top-level object) and its images redacted;
* a tool's ARGUMENTS are unmasked (the model works with pseudonyms, the tool needs the real values);
* resources, forwarded log messages, argument completions and the resource / template listings
  (name, title, description) are masked the same way.

Fail closed: when the gateway cannot do its job the caller gets a ``masking_unavailable`` error and
NO data (never the original result). Exempt tools (``00_Client_Mode``, ``10_Show_User_Guide``, config
``client_gateway.mcp_masking_exempt``) are not masked but still carry the marker: a spoofed marker
can only switch masking on, which is the safe direction.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import threading
import time
from typing import Any, Callable, Iterator, List, Optional

from mcp import types

from client_gateway import control_client
from core.client_guard.home import home_dir
from core.client_guard.marker import MARKER_FIELD, marker
from core.config.config_manager import config
from server.response_pipeline import serialize

logger = logging.getLogger(__name__)

ENV_FLAG = "FINVISION_MCP_MASKING"
EXEMPT_TOOLS = frozenset({"00_Client_Mode", "10_Show_User_Guide"})

UNAVAILABLE_TEXT = (
    "The masking gateway is not reachable or not ready, so this result was withheld. "
    "No data was returned. Check the gateway (00_Client_Mode status) and retry."
)
IMAGE_WITHHELD_TEXT = "image withheld: masking unavailable"
CONTENT_WITHHELD_TEXT = "content withheld: masking unavailable for this content type"


class MaskingUnavailable(Exception):
    """The gateway could not mask / unmask; the message is fixed and carries no data."""

    def __init__(self, message: str = UNAVAILABLE_TEXT) -> None:
        super().__init__(message)


def configured() -> bool:
    """Config ``client_gateway.mcp_masking`` (default false) or env flag ``1``."""
    if os.environ.get(ENV_FLAG, "").strip() == "1":
        return True
    try:
        return config.get("client_gateway.mcp_masking", False) is True
    except Exception:  # noqa: BLE001 - an unreadable config is the default (off)
        return False


ACTIVE_CACHE_S = 2.0
_active_cache: dict[str, Any] = {"at": -1e9, "value": False}
_active_lock = threading.Lock()


def _chat_masked() -> bool:
    """Masking is ON for this MCP's chat: the gateway runs and (with a Claude Code session id)
    reports the session's mode as on. Cached for ``ACTIVE_CACHE_S``; never raises."""
    now = time.monotonic()
    with _active_lock:
        if now - _active_cache["at"] < ACTIVE_CACHE_S:
            return bool(_active_cache["value"])
    try:
        sid = control_client.current_session_id()
        if sid:
            value = control_client.session_mode(sid) == "on"
        else:
            value = control_client.health() is not None
    except Exception:  # noqa: BLE001 - not reachable: masking is off (nothing routes there)
        value = False
    with _active_lock:
        _active_cache.update(at=time.monotonic(), value=value)
    return value


def enabled() -> bool:
    """Read per call: the flag is set AND masking is on for this chat (``_chat_masked``)."""
    return configured() and _chat_masked()


def exempt_tools() -> frozenset[str]:
    extra: Any = None
    try:
        extra = config.get("client_gateway.mcp_masking_exempt", None)
    except Exception:  # noqa: BLE001
        extra = None
    names = {str(n) for n in extra} if isinstance(extra, (list, tuple, set)) else set()
    return EXEMPT_TOOLS | names


# ------------------------------------------------------------------ JSON tree <-> flat string list
def _collect(node: Any, out: List[str], keys: bool = True) -> None:
    """Every string leaf and (``keys``) dict key, in one deterministic order (``_rebuild`` mirrors
    it)."""
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        for key, value in node.items():
            if keys:
                out.append(str(key))
            _collect(value, out, keys)
    elif isinstance(node, list):
        for item in node:
            _collect(item, out, keys)


def _rebuild(node: Any, it: Iterator[str], keys: bool = True) -> Any:
    if isinstance(node, str):
        return next(it)
    if isinstance(node, dict):
        rebuilt = {}
        for key, value in node.items():
            new_key = next(it) if keys else key
            rebuilt[new_key] = _rebuild(value, it, keys)
        return rebuilt
    if isinstance(node, list):
        return [_rebuild(item, it, keys) for item in node]
    return node


def _transform_tree(node: Any, transform: Callable[[List[str]], Optional[List[str]]]) -> Any:
    """``node`` with every string (leaf and key) passed through ``transform`` in ONE call."""
    texts: List[str] = []
    _collect(node, texts)
    if not texts:
        return node
    done = transform(texts)
    if done is None or len(done) != len(texts):
        raise MaskingUnavailable()
    return _rebuild(node, iter(done))


def _mask_strings(texts: List[str]) -> Optional[List[str]]:
    return control_client.mask_texts(texts, whole=True)


# ------------------------------------------------------------------ text with marker
def _marker() -> str:
    return marker(home_dir())


def _mask_text(text: str, mark: Optional[str], mask: bool) -> str:
    """One text with the marker added and (``mask``) its strings masked."""
    parsed: Any = None
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        tree = _transform_tree(parsed, _mask_strings) if mask else parsed
        if mark is None:
            return serialize(tree)
        out = {MARKER_FIELD: mark}
        out.update({k: v for k, v in tree.items() if k != MARKER_FIELD})
        return serialize(out)
    masked = text
    if mask and text:
        done = _mask_strings([text])
        if done is None or len(done) != 1:
            raise MaskingUnavailable()
        masked = done[0]
    return masked if mark is None else f"{masked}\n{MARKER_FIELD}: {mark}"


def _mask_image(item: types.ImageContent) -> types.ContentBlock:
    try:
        raw = base64.b64decode(item.data, validate=True)
    except (binascii.Error, ValueError):
        return types.TextContent(type="text", text=IMAGE_WITHHELD_TEXT)
    res = control_client.mask_image(raw)
    if res is None:
        return types.TextContent(type="text", text=IMAGE_WITHHELD_TEXT)
    data, action = res
    if action == "clean":
        return item
    return item.model_copy(
        update={"data": base64.b64encode(data).decode("ascii"), "mimeType": "image/png"}
    )


def _mask_block(block: Any, mark: Optional[str], mask: bool) -> Any:
    if isinstance(block, types.TextContent):
        return block.model_copy(update={"text": _mask_text(block.text, mark, mask)})
    if not mask:
        return block
    if isinstance(block, types.ImageContent):
        return _mask_image(block)
    return types.TextContent(type="text", text=CONTENT_WITHHELD_TEXT)


def unavailable_result(tool_name: str) -> types.CallToolResult:
    """The ``masking_unavailable`` error envelope as the call result (no data)."""
    from core.validation.error_response import error_envelope
    from server.mcp_app import _envelope_result

    return _envelope_result(tool_name, error_envelope("masking_unavailable", UNAVAILABLE_TEXT))


def mask_call_result(tool_name: str, result: types.CallToolResult) -> types.CallToolResult:
    """``result`` as it may leave the process; never the original data when masking fails."""
    if not enabled():
        return result
    try:
        mask = tool_name not in exempt_tools()
        try:
            mark: Optional[str] = _marker()
        except Exception:  # noqa: BLE001
            if mask:
                raise MaskingUnavailable() from None
            logger.warning("masking marker unavailable for exempt tool %s", tool_name)
            return result
        content = [_mask_block(b, mark, mask) for b in result.content]
        update: dict[str, Any] = {"content": content}
        if mask and result.structuredContent is not None:
            update["structuredContent"] = _transform_tree(result.structuredContent, _mask_strings)
        return result.model_copy(update=update)
    except Exception as exc:  # noqa: BLE001 - fail closed: never return the original on any error
        if not isinstance(exc, MaskingUnavailable):
            logger.error("masking failed for %s: %s", tool_name, type(exc).__name__)
        return unavailable_result(tool_name)


# ------------------------------------------------------------------ arguments
def _token_shaped(texts: List[str]) -> bool:
    from core.client_guard.engine import _LOOSE_TOKEN

    return any(_LOOSE_TOKEN.search(t) for t in texts)


def unmask_arguments(args: dict[str, Any]) -> dict[str, Any]:
    """``args`` with the gateway's pseudonyms replaced by the real values.

    Unreachable gateway: ``MaskingUnavailable`` when any string holds a pseudonym-shaped token
    (it would reach the tool as a wrong name), else ``args`` unchanged."""
    if not enabled():
        return args
    texts: List[str] = []
    _collect(args, texts, keys=False)  # the argument NAMES are the tool's own words
    if not texts:
        return args
    done = control_client.unmask_texts(texts)
    if done is None or len(done) != len(texts):
        if _token_shaped(texts):
            raise MaskingUnavailable()
        return args
    rebuilt = _rebuild(args, iter(done), keys=False)
    return rebuilt if isinstance(rebuilt, dict) else args


# ------------------------------------------------------------------ log messages, resources
def mask_log_message(text: str) -> Optional[str]:
    """The log message masked, or None (drop it) when masking is unavailable. Never logs."""
    if not enabled():
        return text
    try:
        done = _mask_strings([text])
    except Exception:  # noqa: BLE001
        return None
    if done is None or len(done) != 1:
        return None
    return done[0]


def mask_resource_contents(contents: List[Any]) -> List[Any]:
    """Resource contents (``ReadResourceContents``) with their text masked and the marker added.

    Binary content cannot be masked here: ``MaskingUnavailable`` (the read fails, no data)."""
    if not enabled():
        return contents
    try:
        mark = _marker()
        out: List[Any] = []
        for item in contents:
            body = getattr(item, "content", None)
            if not isinstance(body, str):
                raise MaskingUnavailable()
            out.append(_replace_content(item, _mask_text(body, mark, True)))
        return out
    except MaskingUnavailable:
        raise
    except Exception:  # noqa: BLE001 - the message must not carry data
        raise MaskingUnavailable() from None


def _replace_content(item: Any, text: str) -> Any:
    import dataclasses

    if dataclasses.is_dataclass(item) and not isinstance(item, type):
        return dataclasses.replace(item, content=text)
    return item.model_copy(update={"content": text})


# ------------------------------------------------------------------ completions, listings
def mask_completion(completion: Optional[types.Completion]) -> Optional[types.Completion]:
    """Argument completions with their values masked (one gateway call); an empty ``Completion``
    when masking is unavailable (the suggestions are dropped, never sent as they are)."""
    if not enabled() or completion is None or not completion.values:
        return completion
    try:
        done = control_client.mask_texts(list(completion.values), whole=True)
    except Exception:  # noqa: BLE001
        done = None
    if done is None or len(done) != len(completion.values):
        return types.Completion(values=[])
    return completion.model_copy(update={"values": done})


_LISTING_FIELDS = ("name", "title", "description")  # never ``uri`` / ``uriTemplate`` / mimeType


def mask_resource_listing(items: List[Any]) -> List[Any]:
    """``Resource`` / ``ResourceTemplate`` entries with ``name``, ``title`` and ``description``
    masked (one gateway call for the whole list); ``[]`` when masking is unavailable."""
    if not enabled() or not items:
        return items
    try:
        texts: List[str] = []
        for item in items:
            for field in _LISTING_FIELDS:
                value = getattr(item, field, None)
                if isinstance(value, str):
                    texts.append(value)
        if not texts:
            return items
        done = _mask_strings(texts)
        if done is None or len(done) != len(texts):
            return []
        it = iter(done)
        out: List[Any] = []
        for item in items:
            update = {
                field: next(it)
                for field in _LISTING_FIELDS
                if isinstance(getattr(item, field, None), str)
            }
            out.append(item.model_copy(update=update))
        return out
    except Exception:  # noqa: BLE001 - the listing is dropped, never sent as it is
        return []
