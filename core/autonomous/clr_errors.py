"""
CLR Error Extraction

Unwraps `System.AggregateException` / nested `InnerException` chains coming
from TOM `RequestRefresh` / `SaveChanges` failures so handlers can surface a
structured, actionable error instead of the opaque top-level message.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# J-29: cap on how many entries of an AggregateException.InnerExceptions are kept.
MAX_INNER_EXCEPTIONS = 10


def _type_and_message(exc: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    try:
        get_type = getattr(exc, "GetType", None)
        if callable(get_type):
            t = get_type()
            out["type"] = getattr(t, "FullName", None) or str(t)
        else:
            out["type"] = type(exc).__name__
    except Exception:  # noqa: BLE001
        out["type"] = type(exc).__name__
    try:
        out["message"] = str(getattr(exc, "Message", None) or exc).strip()
    except Exception:  # noqa: BLE001
        out["message"] = repr(exc)
    return out


def _inner_exceptions(exc: Any) -> List[Any]:
    """Entries of ``AggregateException.InnerExceptions`` (a .NET collection), if any."""
    coll = getattr(exc, "InnerExceptions", None)
    if coll is None or isinstance(coll, (str, bytes)):
        return []
    try:
        items = list(coll)
    except Exception:  # noqa: BLE001
        count = getattr(coll, "Count", None)
        if not isinstance(count, int):
            return []
        items = []
        for i in range(count):
            try:
                items.append(coll[i])
            except Exception:  # noqa: BLE001
                break
    return [i for i in items if i is not None]


def extract_clr_exception_chain(exc: BaseException, max_depth: int = 8) -> List[Dict[str, Any]]:
    """
    Walk pythonnet's exception chain and return one dict per level.

    Each level captures:
        - type:    .NET type name if available else Python class name
        - message: scalar message string
        - source:  .Source if available (library that raised)
        - hresult: .HResult if available (signed int)
        - inner_exceptions: for an ``AggregateException`` with several
          causes, every ``InnerExceptions`` entry (type + message, capped at
          ``MAX_INNER_EXCEPTIONS``) — the walk itself continues along
          ``InnerException`` (the first cause). J-29: multi-error
          ``SaveChanges`` / refresh failures used to surface one cause only.
    """
    out: List[Dict[str, Any]] = []
    seen: set[int] = set()
    current: Optional[BaseException] = exc
    depth = 0

    while current is not None and depth < max_depth:
        ident = id(current)
        if ident in seen:
            break
        seen.add(ident)

        detail: Dict[str, Any] = {}
        clr_type = getattr(current, "GetType", None)
        try:
            if callable(clr_type):
                t = clr_type()
                detail["type"] = getattr(t, "FullName", None) or str(t)
            else:
                detail["type"] = type(current).__name__
        except Exception:  # noqa: BLE001
            detail["type"] = type(current).__name__

        try:
            message = getattr(current, "Message", None) or str(current)
            detail["message"] = str(message).strip()
        except Exception:  # noqa: BLE001
            detail["message"] = str(current)

        for field in ("Source", "HResult", "StackTrace"):
            try:
                v = getattr(current, field, None)
                if v not in (None, ""):
                    if field == "StackTrace":
                        # Keep stack trace short — first line only
                        detail[field.lower()] = str(v).splitlines()[0][:400]
                    else:
                        detail[field.lower()] = v
            except Exception:  # noqa: BLE001
                pass

        inners = _inner_exceptions(current)
        if len(inners) > 1:
            detail["inner_exceptions"] = [
                _type_and_message(i) for i in inners[:MAX_INNER_EXCEPTIONS]
            ]
            if len(inners) > MAX_INNER_EXCEPTIONS:
                detail["inner_exceptions_truncated"] = len(inners) - MAX_INNER_EXCEPTIONS

        out.append(detail)

        inner = getattr(current, "InnerException", None)
        if inner is None:
            inner = getattr(current, "__cause__", None) or getattr(
                current,
                "__context__",
                None,
            )
        current = inner
        depth += 1

    return out


def format_clr_error(
    exc: BaseException,
    error_type: str = "tom_error",
    **context: Any,
) -> Dict[str, Any]:
    """Build a structured error dict from an arbitrary CLR / Python exception.

    Generalization of ``format_refresh_error`` (kept as a thin wrapper) so
    handlers that bubble CLR exceptions out of TOM ``SaveChanges`` / KPI
    authoring / culture writes / batch ops can return the same shape:

        {
            "success": False,
            "error": <top-level message>,
            "error_type": <caller-supplied label>,
            "clr_chain": [...],
            "clr_root_cause": <root message>,
            "clr_root_type": <root type>,
            ...context...
        }

    ``last_query`` in ``context`` is truncated to 800 chars (the only key
    that gets special handling — everything else is passed through verbatim).
    """
    chain = extract_clr_exception_chain(exc)
    top = chain[0] if chain else {"message": str(exc), "type": type(exc).__name__}
    root = chain[-1] if chain else top

    out: Dict[str, Any] = {
        "success": False,
        "error": top.get("message") or str(exc),
        "error_type": error_type,
        "clr_chain": chain,
        "clr_root_cause": root.get("message"),
        "clr_root_type": root.get("type"),
    }
    causes: List[str] = []
    for level in chain:
        for inner in level.get("inner_exceptions") or []:
            msg = inner.get("message")
            if msg and msg not in causes:
                causes.append(msg)
    if len(causes) > 1:
        out["clr_root_causes"] = causes
    # Per-key handling
    last_query = context.pop("last_query", None)
    if last_query:
        snippet = str(last_query).strip()
        if len(snippet) > 800:
            snippet = snippet[:800] + f"...[truncated {len(last_query) - 800} chars]"
        out["last_query"] = snippet
    # Drop None values so callers don't have to gate every kwarg.
    for k, v in context.items():
        if v is not None:
            out[k] = v
    return out


def format_refresh_error(
    exc: BaseException,
    table: Optional[str] = None,
    partition: Optional[str] = None,
    last_query: Optional[str] = None,
) -> Dict[str, Any]:
    """Back-compat shim for refresh failures. Prefer format_clr_error directly."""
    return format_clr_error(
        exc,
        error_type="refresh_error",
        table=table,
        partition=partition,
        last_query=last_query,
    )
