"""Client for daxformatter.com — formats DAX expressions via the public API.

PRIVACY (2026-09-22 review D-20): every expression is POSTed to a third-party
service (SQLBI's DAX Formatter). Measure bodies can contain client names,
account ids and other literals, so sending is OFF by default. Enable it
explicitly with ``"dax_formatter": {"enabled": true}`` in
``config/local_config.json`` or the environment variable
``MCP_DAX_FORMATTER_ENABLED=1``; ``is_enabled()`` is the single gate.

Endpoint: POST https://daxformatter.azurewebsites.net/api/DaxFormat
  (the public www.daxformatter.com URL 301-redirects here; `requests`
  converts POST->GET on redirect and drops the body, so we hit the
  canonical Azure URL directly).
Payload: {"Dax": "<expr>", "ListSeparator": ",", "DecimalSeparator": ".", ...}
Response: plain text — the formatted DAX with CRLF line breaks.

Safety:
- ``token_equivalent(original, formatted)`` compares the two lexer token
  streams (ignoring whitespace, comments, keyword/function/name case and
  table-name quoting). Callers must refuse to write anything that is not
  token-equivalent — an error page or banner returned with HTTP 200 would
  otherwise overwrite the measure.
- Line endings are normalised to the original's style, so an LF-only measure
  is not rewritten just because the service answers with CRLF.
- ``format_many`` works under a time budget and returns the keys it did not
  reach in ``remaining`` (one request per expression; the service's
  multi-expression endpoint is not used because its contract could not be
  verified offline).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_DAXFORMATTER_URL = "https://daxformatter.azurewebsites.net/api/DaxFormat"
_DEFAULT_LINE_LEN = 90
_MIN_DELAY_BETWEEN_REQUESTS_S = 0.25  # be polite
_DEFAULT_TIME_BUDGET_S = 120.0  # below the 180 s 02_TMDL_Operations dispatcher timeout
_CALLER_APP = "MCP-PowerBi-Finvision"

ENABLE_ENV_VAR = "MCP_DAX_FORMATTER_ENABLED"
ENABLE_CONFIG_KEY = "dax_formatter.enabled"

DISABLED_MESSAGE = (
    "format_dax sends measure DAX to the external service daxformatter.com (SQLBI). "
    "This is disabled by default because DAX literals can contain client data. "
    'To allow it, set "dax_formatter": {"enabled": true} in config/local_config.json '
    f"or the environment variable {ENABLE_ENV_VAR}=1, then retry."
)


def is_enabled() -> bool:
    """True only when the user opted in to sending DAX to daxformatter.com."""
    env = os.environ.get(ENABLE_ENV_VAR)
    if env is not None and env.strip() != "":
        return env.strip().lower() in ("1", "true", "yes", "on")
    try:
        from core.config.config_manager import config

        return bool(config.get(ENABLE_CONFIG_KEY, False))
    except Exception:  # noqa: BLE001 - no config => not enabled
        return False


# ---------------------------------------------------------------------------
# Token equivalence
# ---------------------------------------------------------------------------


def _canonical_tokens(dax: str) -> List[Tuple[Any, ...]]:
    from core.dax.dax_names import split_qualified, unbracket, unquote_table
    from core.dax.tokenizer import DaxLexer, TokenType

    code = DaxLexer().tokenize_code(dax or "")
    out: List[Tuple[Any, ...]] = []
    i = 0
    n = len(code)
    while i < n:
        t = code[i]
        tt = t.type
        if tt == TokenType.STRING:
            out.append(("S", t.value))
        elif tt == TokenType.DATETIME:
            out.append(("DT", t.value[2:]))
        elif tt == TokenType.NUMBER:
            try:
                out.append(("N", float(t.value)))
            except ValueError:
                out.append(("N", t.value))
        elif tt == TokenType.QUALIFIED_REF:
            tb, col = split_qualified(t.value)
            out.append(("Q", tb.lower(), col.lower()))
        elif (
            tt == TokenType.IDENTIFIER
            and i + 1 < n
            and code[i + 1].type == TokenType.COLUMN_REF
            and code[i + 1].start == t.end
        ):
            out.append(("Q", t.value.lower(), unbracket(code[i + 1].value).lower()))
            i += 1
        elif tt == TokenType.COLUMN_REF:
            out.append(("C", unbracket(t.value).lower()))
        elif tt == TokenType.TABLE_REF:
            out.append(("W", unquote_table(t.value).upper()))
        elif tt in (TokenType.IDENTIFIER, TokenType.KEYWORD, TokenType.FUNCTION):
            out.append(("W", t.value.upper()))
        else:
            out.append((tt.name, t.value))
        i += 1
    return out


def token_equivalent(original: str, formatted: str) -> Tuple[bool, Optional[str]]:
    """``(True, None)`` when *formatted* is the same DAX as *original* up to
    whitespace, comments, case of keywords/functions/names and table quoting;
    otherwise ``(False, "first difference at token N: … vs …")``."""
    a = _canonical_tokens(original)
    b = _canonical_tokens(formatted)
    if a == b:
        return True, None
    for k, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return False, f"first difference at token {k}: {x!r} vs {y!r}"
    return False, f"token count differs: {len(a)} vs {len(b)}"


def _match_line_endings(original: str, formatted: str) -> str:
    """Use the original's line-ending style (the service answers with CRLF)."""
    if "\r\n" in (original or ""):
        return formatted.replace("\r\n", "\n").replace("\n", "\r\n")
    return formatted.replace("\r\n", "\n")


def is_unchanged(original: str, formatted: str) -> bool:
    """True when *formatted* only differs from *original* by line endings /
    surrounding whitespace."""
    norm = lambda x: (x or "").replace("\r\n", "\n").strip()  # noqa: E731
    return norm(original) == norm(formatted)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def format_one(
    expression: str,
    *,
    max_line_length: int = _DEFAULT_LINE_LEN,
    skip_space_after_function: bool = False,
    timeout: float = 10.0,
) -> Dict[str, Any]:
    """Format a single DAX expression. Returns {success, formatted, original}.

    Does NOT check ``is_enabled()`` — callers that send model DAX must (the
    ``02_TMDL_Operations:format_dax`` handler does). ``formatted`` uses the
    original's line-ending style; ``token_equivalent`` reports whether the
    service returned the same DAX.
    """
    if not expression or not expression.strip():
        return {"success": True, "formatted": "", "original": expression}
    try:
        import requests  # already a project dep
    except Exception as e:
        return {"success": False, "error": f"requests unavailable: {e}"}
    payload: Dict[str, Any] = {
        "Dax": expression,
        "MaxLineLength": int(max_line_length),
        "SkipSpaceAfterFunctionName": bool(skip_space_after_function),
        "ListSeparator": ",",
        "DecimalSeparator": ".",
        "CallerApp": _CALLER_APP,
    }
    try:
        resp = requests.post(_DAXFORMATTER_URL, json=payload, timeout=timeout)
        resp.raise_for_status()
    except Exception as e:
        return {"success": False, "error": f"daxformatter request failed: {e}"}
    formatted = (resp.text or "").strip()
    if not formatted:
        return {
            "success": False,
            "error": "daxformatter returned empty body",
            "raw": (resp.text or "")[:500],
        }
    formatted = _match_line_endings(expression, formatted)
    equivalent, why = token_equivalent(expression, formatted)
    out: Dict[str, Any] = {
        "success": True,
        "formatted": formatted,
        "original": expression,
        "token_equivalent": equivalent,
    }
    if not equivalent:
        out["equivalence_detail"] = why
    return out


def format_many(
    expressions: List[Dict[str, Any]],
    *,
    max_line_length: int = _DEFAULT_LINE_LEN,
    skip_space_after_function: bool = False,
    delay_s: float = _MIN_DELAY_BETWEEN_REQUESTS_S,
    on_error: str = "skip",  # "skip" | "abort"
    time_budget_s: Optional[float] = _DEFAULT_TIME_BUDGET_S,
) -> Dict[str, Any]:
    """Format N expressions sequentially. expressions = list of {key, expression}.

    Stops starting new requests once *time_budget_s* has elapsed and returns the
    unprocessed keys in ``remaining`` (``timed_out: True``) — so the caller never
    outlives the dispatcher timeout while still writing measures.

    Returns {success, results: [{key, success, formatted, original, error?}],
    remaining, timed_out, stats}.
    """
    results: List[Dict[str, Any]] = []
    succeeded = failed = unchanged = not_equivalent = 0
    started = time.monotonic()
    remaining: List[Any] = []
    timed_out = False
    for i, item in enumerate(expressions):
        if time_budget_s is not None and time.monotonic() - started >= float(time_budget_s):
            timed_out = True
            remaining = [x.get("key") for x in expressions[i:]]
            break
        key = item.get("key")
        expr = item.get("expression", "") or ""
        out = format_one(
            expr,
            max_line_length=max_line_length,
            skip_space_after_function=skip_space_after_function,
        )
        if out.get("success"):
            if is_unchanged(expr, out.get("formatted") or ""):
                unchanged += 1
            elif out.get("token_equivalent") is False:
                not_equivalent += 1
            else:
                succeeded += 1
            results.append({"key": key, **out})
        else:
            failed += 1
            results.append(
                {"key": key, "success": False, "error": out.get("error"), "original": expr}
            )
            if on_error == "abort":
                remaining = [x.get("key") for x in expressions[i + 1 :]]
                break
        if i < len(expressions) - 1 and delay_s > 0:
            time.sleep(delay_s)
    return {
        "success": failed == 0 and not_equivalent == 0 and not timed_out,
        "results": results,
        "remaining": remaining,
        "timed_out": timed_out,
        "stats": {
            "total": len(expressions),
            "succeeded": succeeded,
            "unchanged": unchanged,
            "not_token_equivalent": not_equivalent,
            "failed": failed,
            "not_processed": len(remaining),
        },
    }
