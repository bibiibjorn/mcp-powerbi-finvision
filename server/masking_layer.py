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

Fail mode (config ``client_gateway.fail_mode``):

* ``degrade`` (the default; owner rule 2026-10-12: a masking problem never makes a Power BI call
  fail). A failed gateway call is retried once; when the gateway is gone altogether masking is off
  by the rule above. Otherwise the result is masked IN-PROCESS with what runs without the gateway
  (``core.client_guard.fallback``: the value shapes), keeps the marker (so the wire still treats
  the chat as a Power BI chat and masks the conversation there) and carries a top-level
  ``"_masking": {"degraded": true, "reason": ..., "hint": ...}`` member (one fixed line for a
  text result that is not a JSON object), also when the gateway itself answered that its
  dictionary was not ready. Images that cannot be made safe are still withheld (the rest of the
  result is returned); arguments the gateway cannot unmask go to the tool as they are.
* ``closed``: the gateway cannot do its job, the caller gets a ``masking_unavailable`` error and NO
  data (never the original result).

Exempt tools (``00_Client_Mode``, ``10_Show_User_Guide``, config
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
from core.client_guard import fallback
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

NOTICE_FIELD = "_masking"
NOTICE_HINT = (
    "Masking is partial for this result: run 00_Client_Mode gateway_status for the reason."
)
RETRY_TIMEOUT_S = 15.0  # the one retry of a failed gateway call (fail mode degrade)
_RETRY_AFTER = frozenset(
    {control_client.REASON_UNREACHABLE, control_client.REASON_TIMEOUT, control_client.REASON_TOKEN}
)


class MaskingUnavailable(Exception):
    """The gateway could not mask / unmask; the message is fixed and carries no data."""

    def __init__(self, message: str = UNAVAILABLE_TEXT) -> None:
        super().__init__(message)


class _GatewayGone(Exception):
    """No gateway runs any more: masking is off by rule (no gateway, no masking)."""


def configured() -> bool:
    """Config ``client_gateway.mcp_masking`` (default false) or env flag ``1``."""
    if os.environ.get(ENV_FLAG, "").strip() == "1":
        return True
    try:
        return config.get("client_gateway.mcp_masking", False) is True
    except Exception:  # noqa: BLE001 - an unreadable config is the default (off)
        return False


def degrade_mode() -> bool:
    """Fail mode ``degrade`` (the default) is on: never fail a call because of the gateway."""
    return control_client.fail_mode() != "closed"


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


def describe() -> dict[str, Any]:
    """What ``enabled()`` decides for THIS process and why (fixed labels; for ``00_Client_Mode``):
    ``basis`` is ``flag_off`` (the MCP masking flag is not set), ``session`` (a Claude Code
    session id: the gateway's mode for that session) or ``gateway_running`` (no session id, e.g.
    VS Code Copilot: masking is on while a gateway runs)."""
    if not configured():
        return {"effective": False, "basis": "flag_off"}
    basis = "session" if control_client.current_session_id() else "gateway_running"
    return {"effective": _chat_masked(), "basis": basis}


def exempt_tools() -> frozenset[str]:
    extra: Any = None
    try:
        extra = config.get("client_gateway.mcp_masking_exempt", None)
    except Exception:  # noqa: BLE001
        extra = None
    names = {str(n) for n in extra} if isinstance(extra, (list, tuple, set)) else set()
    return EXEMPT_TOOLS | names


# ------------------------------------------------------------------ the gateway, with its fallback
def forget_chat_check() -> None:
    """The next ``enabled()`` asks the gateway again (after a switch or a failed call)."""
    with _active_lock:
        _active_cache.update(at=-1e9)


_forget_chat_check = forget_chat_check


def _gateway_down(reason: str) -> bool:
    """After a failed call: re-check that a gateway answers at all (and make the next ``enabled()``
    ask again). Only a transport / token failure says anything about that."""
    if reason not in _RETRY_AFTER:
        return False
    _forget_chat_check()
    try:
        return control_client.health() is None
    except Exception:  # noqa: BLE001
        return True


class _Run:
    """The gateway work of ONE result / argument set / listing: the calls, the one retry and the
    in-process fallback of fail mode ``degrade``, and what must be reported (``note``)."""

    def __init__(self) -> None:
        self.degrade = degrade_mode()
        self.note: Optional[str] = None  # the first fixed reason the result is only partly masked

    def degraded(self, reason: str) -> None:
        if self.note is None:
            self.note = reason or control_client.REASON_BAD_REPLY

    def _call(self, texts: List[str], retry: bool) -> tuple[Optional[List[str]], str]:
        timeout = RETRY_TIMEOUT_S if retry else control_client.MASK_TIMEOUT_S
        try:
            done, reason, extra = control_client.mask_texts_ex(texts, whole=True, timeout=timeout)
        except Exception:  # noqa: BLE001 - a broken client is a failed call
            return None, control_client.REASON_BAD_REPLY
        if done is None or len(done) != len(texts):
            return None, reason or control_client.REASON_BAD_REPLY
        if extra.get("degraded"):
            self.degraded(str(extra.get("degraded_reason") or ""))
        return done, ""

    def mask(self, texts: List[str]) -> List[str]:
        """``texts`` masked by the gateway; closed: ``MaskingUnavailable`` when it cannot. Degrade:
        one retry, then the in-process shapes (``_GatewayGone`` when no gateway runs any more)."""
        done, reason = self._call(texts, retry=False)
        if done is not None:
            return done
        if not self.degrade:
            raise MaskingUnavailable()
        if _gateway_down(reason):
            raise _GatewayGone()
        done, reason2 = self._call(texts, retry=True)
        if done is not None:
            return done
        if _gateway_down(reason2):
            raise _GatewayGone()
        masked, ok = fallback.mask_texts(texts)
        self.degraded(reason2 if ok else control_client.REASON_MASK_FAILED)
        logger.warning("masking degraded (%s): result masked in-process", reason2 or reason)
        return masked


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


# ------------------------------------------------------------------ text with marker
def _marker() -> str:
    return marker(home_dir())


def _mask_text(text: str, mark: Optional[str], mask: bool, run: Optional[_Run] = None) -> str:
    """One text with the marker added and (``mask``) its strings masked."""
    run = run or _Run()
    parsed: Any = None
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        tree = _transform_tree(parsed, run.mask) if mask else parsed
        if mark is None:
            return serialize(tree)
        out = {MARKER_FIELD: mark}
        out.update({k: v for k, v in tree.items() if k != MARKER_FIELD})
        return serialize(out)
    masked = text
    if mask and text:
        masked = run.mask([text])[0]
    return masked if mark is None else f"{masked}\n{MARKER_FIELD}: {mark}"


def _notice(reason: str) -> dict[str, Any]:
    return {"degraded": True, "reason": reason, "hint": NOTICE_HINT}


def _with_notice(text: str, reason: str) -> Optional[str]:
    """``text`` with the ``_masking`` member (a JSON object: right after the marker) or, for any
    other text, one fixed line appended; None when ``text`` is a JSON object that already has one
    (never twice)."""
    parsed: Any = None
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        if NOTICE_FIELD in parsed:
            return None
        out: dict[str, Any] = {}
        for key, value in parsed.items():
            out[key] = value
            if key == MARKER_FIELD:
                out[NOTICE_FIELD] = _notice(reason)
        if NOTICE_FIELD not in out:
            out = {NOTICE_FIELD: _notice(reason), **parsed}
        return serialize(out)
    return f"{text}\n{NOTICE_FIELD}: degraded ({reason}). {NOTICE_HINT}"


def _mask_image(item: types.ImageContent, run: Optional[_Run] = None) -> types.ContentBlock:
    try:
        raw = base64.b64decode(item.data, validate=True)
    except (binascii.Error, ValueError):
        return types.TextContent(type="text", text=IMAGE_WITHHELD_TEXT)
    try:
        res, _reason, extra = control_client.mask_image_ex(raw)
    except Exception:  # noqa: BLE001 - a broken client is a failed call: the image is withheld
        res, extra = None, {}
    if res is None:
        return types.TextContent(type="text", text=IMAGE_WITHHELD_TEXT)
    if run is not None and extra.get("degraded"):
        run.degraded(str(extra.get("degraded_reason") or ""))
    data, action = res
    if action == "clean":
        return item
    return item.model_copy(
        update={"data": base64.b64encode(data).decode("ascii"), "mimeType": "image/png"}
    )


def _mask_block(block: Any, mark: Optional[str], mask: bool, run: Optional[_Run] = None) -> Any:
    if isinstance(block, types.TextContent):
        return block.model_copy(update={"text": _mask_text(block.text, mark, mask, run)})
    if not mask:
        return block
    if isinstance(block, types.ImageContent):
        return _mask_image(block, run)
    return types.TextContent(type="text", text=CONTENT_WITHHELD_TEXT)


def unavailable_result(tool_name: str) -> types.CallToolResult:
    """The ``masking_unavailable`` error envelope as the call result (no data)."""
    from core.validation.error_response import error_envelope
    from server.mcp_app import _envelope_result

    return _envelope_result(tool_name, error_envelope("masking_unavailable", UNAVAILABLE_TEXT))


def _add_result_notice(update: dict[str, Any], reason: str) -> None:
    """Put the ``_masking`` notice into the first text block of ``update['content']`` that can take
    it (a JSON object first, else the first text block) and into a dict ``structuredContent``."""
    content = list(update["content"])
    texts = [i for i, b in enumerate(content) if isinstance(b, types.TextContent)]
    target = texts[0] if texts else None
    for i in texts:
        try:
            parsed = json.loads(content[i].text)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            target = i
            break
    if target is not None:
        text = _with_notice(content[target].text, reason)
        if text is not None:
            content[target] = content[target].model_copy(update={"text": text})
    update["content"] = content
    structured = update.get("structuredContent")
    if isinstance(structured, dict) and NOTICE_FIELD not in structured:
        update["structuredContent"] = {NOTICE_FIELD: _notice(reason), **structured}


def mask_call_result(tool_name: str, result: types.CallToolResult) -> types.CallToolResult:
    """``result`` as it may leave the process. Fail mode closed: never the original data when
    masking fails. Fail mode degrade: never a failure because of the gateway (see the module
    text)."""
    if not enabled():
        return result
    run = _Run()
    try:
        mask = tool_name not in exempt_tools()
        mark: Optional[str]
        try:
            mark = _marker()
        except Exception:  # noqa: BLE001
            if mask and not run.degrade:
                raise MaskingUnavailable() from None
            logger.warning("masking marker unavailable for tool %s", tool_name)
            if not mask:
                return result
            mark = None
        content = [_mask_block(b, mark, mask, run) for b in result.content]
        update: dict[str, Any] = {"content": content}
        if mask and result.structuredContent is not None:
            update["structuredContent"] = _transform_tree(result.structuredContent, run.mask)
        if run.note is not None:
            _add_result_notice(update, run.note)
        return result.model_copy(update=update)
    except _GatewayGone:
        return result  # no gateway, no masking (the rule of ``enabled``)
    except Exception as exc:  # noqa: BLE001 - never return the original on any error
        if not isinstance(exc, MaskingUnavailable):
            logger.error("masking failed for %s: %s", tool_name, type(exc).__name__)
        if run.degrade:
            fallback_result = _fallback_result(result)
            if fallback_result is not None:
                return fallback_result
        return unavailable_result(tool_name)


def _fallback_result(result: types.CallToolResult) -> Optional[types.CallToolResult]:
    """Fail mode degrade, after an unexpected error in the masking above: the text blocks masked
    in-process (``core.client_guard.fallback``), images withheld, the ``_masking`` notice added.
    None when even that fails (the caller then returns ``masking_unavailable``)."""
    try:
        content: List[Any] = []
        for block in result.content:
            if isinstance(block, types.TextContent):
                masked, ok = fallback.mask_texts([block.text])
                if not ok:
                    return None
                content.append(block.model_copy(update={"text": masked[0]}))
            else:
                content.append(types.TextContent(type="text", text=CONTENT_WITHHELD_TEXT))
        update: dict[str, Any] = {"content": content, "structuredContent": None}
        _add_result_notice(update, control_client.REASON_MASK_FAILED)
        return result.model_copy(update=update)
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ arguments
def _token_shaped(texts: List[str]) -> bool:
    from core.client_guard.engine import _LOOSE_TOKEN

    return any(_LOOSE_TOKEN.search(t) for t in texts)


def _unmask_call(texts: List[str], retry: bool) -> tuple[Optional[List[str]], str]:
    timeout = RETRY_TIMEOUT_S if retry else control_client.UNMASK_TIMEOUT_S
    try:
        done, reason, _extra = control_client.unmask_texts_ex(texts, timeout=timeout)
    except Exception:  # noqa: BLE001
        return None, control_client.REASON_BAD_REPLY
    if done is None or len(done) != len(texts):
        return None, reason or control_client.REASON_BAD_REPLY
    return done, ""


def unmask_arguments(args: dict[str, Any]) -> dict[str, Any]:
    """``args`` with the gateway's pseudonyms replaced by the real values.

    Gateway failure, fail mode closed: ``MaskingUnavailable`` when any string holds a
    pseudonym-shaped token (it would reach the tool as a wrong name), else ``args`` unchanged.
    Fail mode degrade: one retry, then ``args`` go to the tool as they are (it may report an unknown
    name for a pseudonym: a normal tool result, not a masking error)."""
    if not enabled():
        return args
    texts: List[str] = []
    _collect(args, texts, keys=False)  # the argument NAMES are the tool's own words
    if not texts:
        return args
    degrade = degrade_mode()
    done, reason = _unmask_call(texts, retry=False)
    if done is None and degrade and _token_shaped(texts):
        if _gateway_down(reason):
            return args
        done, reason = _unmask_call(texts, retry=True)
    if done is None:
        if _token_shaped(texts):
            if not degrade:
                raise MaskingUnavailable()
            logger.warning("unmasking failed (%s): arguments passed on as they are", reason)
        return args
    rebuilt = _rebuild(args, iter(done), keys=False)
    return rebuilt if isinstance(rebuilt, dict) else args


# ------------------------------------------------------------------ log messages, resources
def mask_log_message(text: str) -> Optional[str]:
    """The log message masked. Fail mode closed: None (drop it) when masking is unavailable.
    Fail mode degrade: masked in-process when the gateway cannot do it. Never logs."""
    if not enabled():
        return text
    run = _Run()
    try:
        return run.mask([text])[0]
    except _GatewayGone:
        return text
    except Exception:  # noqa: BLE001
        return None


def mask_resource_contents(contents: List[Any]) -> List[Any]:
    """Resource contents (``ReadResourceContents``) with their text masked and the marker added
    (and, in fail mode degrade, the ``_masking`` notice when the masking was only partial).

    Binary content cannot be masked here: ``MaskingUnavailable`` (the read fails, no data)."""
    if not enabled():
        return contents
    run = _Run()
    try:
        mark = _marker()
        out: List[Any] = []
        for item in contents:
            body = getattr(item, "content", None)
            if not isinstance(body, str):
                raise MaskingUnavailable()
            out.append(_replace_content(item, _mask_text(body, mark, True, run)))
        if run.note is not None and out:
            body = _with_notice(getattr(out[0], "content", ""), run.note)
            if body is not None:
                out[0] = _replace_content(out[0], body)
        return out
    except _GatewayGone:
        return contents
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
    """Argument completions with their values masked (one gateway call). Fail mode closed: an
    empty ``Completion`` when masking is unavailable (the suggestions are dropped, never sent as
    they are). Fail mode degrade: masked in-process instead."""
    if not enabled() or completion is None or not completion.values:
        return completion
    run = _Run()
    try:
        done: Optional[List[str]] = run.mask(list(completion.values))
    except _GatewayGone:
        return completion
    except Exception:  # noqa: BLE001
        done = None
    if done is None or len(done) != len(completion.values):
        return types.Completion(values=[])
    return completion.model_copy(update={"values": done})


_LISTING_FIELDS = ("name", "title", "description")  # never ``uri`` / ``uriTemplate`` / mimeType


def mask_resource_listing(items: List[Any]) -> List[Any]:
    """``Resource`` / ``ResourceTemplate`` entries with ``name``, ``title`` and ``description``
    masked (one gateway call for the whole list). Fail mode closed: ``[]`` when masking is
    unavailable. Fail mode degrade: masked in-process instead."""
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
        done = _Run().mask(texts)
        if len(done) != len(texts):
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
    except _GatewayGone:
        return items
    except Exception:  # noqa: BLE001 - the listing is dropped, never sent as it is
        return []
