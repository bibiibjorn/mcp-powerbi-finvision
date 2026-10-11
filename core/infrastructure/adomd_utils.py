"""Shared ADOMD plumbing: the per-connection command gate, value conversion,
error classification and the command-timeout budget.

Why this module exists (2026-09-22 deep review, slice B):

* **B-01** — every tool shares ONE ``AdomdConnection`` and handlers run on a
  thread pool. ADOMD allows one open command/reader per connection; two
  concurrent readers corrupt the XMLA stream ("Root element is missing",
  "Cannot write to a BufferedStream while the read buffer is not empty") and
  the connection turns Broken. :class:`CommandGate` serialises every command
  on a connection and tracks the in-flight ones so a timed-out dispatcher can
  ``Cancel()`` them.
* **B-06** — ``str()`` on a CLR ``System.Decimal`` / ``System.DateTime`` calls
  .NET ``ToString()`` under the *current culture* (``'1234,56'``,
  ``'22/09/2026 13:45:00'`` on en-BE). :func:`clr_to_py` returns native JSON
  numbers / bools / ISO dates instead.
* **B-19** — the ADOMD ``CommandTimeout`` must fire BEFORE the dispatcher's
  per-tool timeout, otherwise which one wins is a coin flip and a timed-out
  worker keeps reading on the shared connection. :func:`command_budget` lets the
  dispatcher publish the remaining budget for the current worker thread.

Everything here is CLR-free at import time so it can be unit-tested with fakes.
"""

from __future__ import annotations

import datetime as _dt
import decimal as _decimal
import logging
import math
import re
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

#: Seconds a caller waits for the command gate before it gets a ``busy`` error.
DEFAULT_GATE_TIMEOUT_S = 30.0


class ConnectionBusy(RuntimeError):
    """Raised when the command gate could not be acquired in time."""

    def __init__(self, message: str, retry_after: float = 2.0, holder: Optional[str] = None):
        super().__init__(message)
        self.retry_after = retry_after
        self.holder = holder


def busy_error(exc: ConnectionBusy) -> Dict[str, Any]:
    """Structured ``busy`` envelope for a :class:`ConnectionBusy`."""
    out: Dict[str, Any] = {
        "success": False,
        "error": str(exc),
        "error_type": "busy",
        "retry_after": exc.retry_after,
        "suggestions": [
            "Another query is still running on the shared Power BI connection; retry shortly",
            "Avoid firing several live-model tool calls in parallel",
        ],
    }
    if exc.holder:
        out["busy_with"] = exc.holder
    return out


#: Substrings that mean the XMLA stream / connection itself is broken, as
#: opposed to a DAX error. Recognised case-insensitively.
_STREAM_CORRUPTION_TOKENS = (
    "root element is missing",
    "bufferedstream",
    "unrecognizable response",
    "unrecognized response",
    "forcibly closed",
    "an existing connection was forcibly",
    "connection was broken",
    "connection is broken",
    "the connection is not open",
    "connection is not open",
    "connection was closed",
    "connection is closed",
    "connection state is closed",
    "no active connection",
    "the peer prematurely closed",
    "unable to read data from the transport",
    "unable to write data to the transport",
    "cannot access a disposed object",
)


_STACK_FRAME_RE = re.compile(r"(?:\r?\n)\s+at (?:Microsoft|System)\.", re.MULTILINE)


def engine_message(error: Any) -> str:
    """The engine's message without the .NET stack trace pythonnet appends.

    ``str()`` of an ADOMD exception is the engine message followed by CRLF-separated
    ``   at Microsoft.AnalysisServices...`` frames; the frames are noise for an agent (every
    R0201 live-test error carried ~600 chars of them)."""
    text = str(error) if error is not None else ""
    m = _STACK_FRAME_RE.search(text)
    return (text[: m.start()] if m else text).strip()


def is_connection_failure(message: Any) -> bool:
    """True when ``message`` describes a broken/closed connection or XMLA stream."""
    text = str(message or "").lower()
    return any(tok in text for tok in _STREAM_CORRUPTION_TOKENS)


def is_cancellation(message: Any) -> bool:
    """True when the engine/ADOMD reports that the command was cancelled."""
    text = str(message or "").lower()
    return "cancel" in text and ("operation" in text or "command" in text or "query" in text)


# ---------------------------------------------------------------------------
# Value conversion (B-06)
# ---------------------------------------------------------------------------


def _clr_type_name(value: Any) -> str:
    get_type = getattr(value, "GetType", None)
    if callable(get_type):
        try:
            name = getattr(get_type(), "FullName", None)
            if name:
                return str(name)
        except Exception:  # noqa: BLE001 — CLR interop
            pass
    return ""


_DT_EPOCH = _dt.datetime(1, 1, 1)

#: ``type(value)`` -> short CLR type name, for REAL pythonnet types only (their
#: ``__module__`` is ``System``). Looking the name up per cell cost two CLR calls
#: (``GetType().FullName``); non-CLR stand-ins (test fakes) are never cached, so
#: their per-instance ``GetType()`` keeps being honoured.
_FAST_KIND: Dict[type, str] = {}


def _clr_datetime_ticks_to_iso(value: Any) -> Optional[str]:
    """ISO string from ONE ``Ticks`` read (P-09: 11.4 -> 1.4 us per cell).

    ``DateTime.Ticks`` counts 100 ns from 0001-01-01, the same epoch as
    ``datetime(1, 1, 1)``; sub-microsecond ticks are truncated exactly like the
    component-wise path below. ``None`` when out of Python's range / no ``Ticks``.
    """
    try:
        ticks = int(value.Ticks)
        return (_DT_EPOCH + _dt.timedelta(microseconds=ticks // 10)).isoformat()
    except Exception:  # noqa: BLE001 -- fall back to the component-wise conversion
        return None


def _clr_datetime_to_iso(value: Any) -> Optional[str]:
    try:
        ticks = int(getattr(value, "Ticks"))
        micro = (ticks % 10_000_000) // 10
    except Exception:  # noqa: BLE001
        try:
            micro = int(value.Millisecond) * 1000
        except Exception:  # noqa: BLE001
            micro = 0
    try:
        py = _dt.datetime(
            int(value.Year),
            int(value.Month),
            int(value.Day),
            int(value.Hour),
            int(value.Minute),
            int(value.Second),
            micro,
        )
    except Exception:  # noqa: BLE001
        return None
    return py.isoformat()


_SYS_TODOUBLE: Any = None
_SYS_TODOUBLE_RESOLVED = False


def _sys_decimal_to_double() -> Any:
    """``System.Decimal.ToDouble`` resolved once (``None`` without CLR)."""
    global _SYS_TODOUBLE, _SYS_TODOUBLE_RESOLVED
    if not _SYS_TODOUBLE_RESOLVED:
        try:
            from System import Decimal as _SysDecimal  # type: ignore

            _SYS_TODOUBLE = _SysDecimal.ToDouble
        except Exception:  # noqa: BLE001 — no CLR in tests / odd build
            _SYS_TODOUBLE = None
        _SYS_TODOUBLE_RESOLVED = True
    return _SYS_TODOUBLE


def _clr_decimal_to_float(value: Any) -> Any:
    to_double = _sys_decimal_to_double()
    if to_double is not None:
        try:
            return float(to_double(value))
        except Exception:  # noqa: BLE001
            pass
    # .NET's default "G" format never emits group separators, so the only
    # culture-dependent character is the decimal separator.
    text = str(value).strip()
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return text


def clr_to_py(value: Any) -> Any:
    """Convert one ADOMD cell to a locale-neutral, JSON-friendly Python value.

    * ``None`` / ``DBNull`` -> ``None``
    * ``bool`` / ``int`` / ``float`` / ``str`` -> unchanged (NaN/inf -> ``None``)
    * ``System.Decimal`` / ``decimal.Decimal`` -> ``float``
    * ``System.DateTime`` / ``datetime`` -> ISO-8601 string
    * ``System.TimeSpan`` / ``System.Guid`` / anything else -> ``str``
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return None if (math.isnan(value) or math.isinf(value)) else value
    if isinstance(value, str):
        return value
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    if isinstance(value, _decimal.Decimal):
        return float(value)

    tp = type(value)
    short = _FAST_KIND.get(tp)
    real_clr = short is not None
    if short is None:
        type_name = _clr_type_name(value) or tp.__name__
        short = type_name.rsplit(".", 1)[-1]
        if getattr(tp, "__module__", "") == "System":
            _FAST_KIND[tp] = short
            real_clr = True
    if short == "DBNull":
        return None
    if short == "DateTime" or short == "DateTimeOffset":
        iso = _clr_datetime_ticks_to_iso(value) if real_clr else None
        if iso is None:
            iso = _clr_datetime_to_iso(value)
        return iso if iso is not None else str(value)
    if short == "Decimal":
        return _clr_decimal_to_float(value)
    if short in ("Double", "Single"):
        try:
            f = float(value)
            return None if (math.isnan(f) or math.isinf(f)) else f
        except Exception:  # noqa: BLE001
            return _clr_decimal_to_float(value)
    if short in ("Int16", "Int32", "Int64", "UInt16", "UInt32", "UInt64", "Byte", "SByte"):
        try:
            return int(value)
        except Exception:  # noqa: BLE001
            return str(value)
    if short == "Boolean":
        return str(value).strip().lower() == "true"
    return str(value)


# ---------------------------------------------------------------------------
# Reader materialisation (B-06 + B-20)
# ---------------------------------------------------------------------------


def reader_columns(reader: Any) -> List[str]:
    """Column names of the reader's current result set."""
    return [str(reader.GetName(i)) for i in range(int(reader.FieldCount))]


def iter_reader_rows(reader: Any, columns: List[str]) -> Iterator[Dict[str, Any]]:
    """Yield converted rows until the reader is exhausted (caller may stop early).

    Uses ``reader.GetValues(object[])`` once per row when pythonnet is present
    (~3x cheaper than ``GetValue`` per cell, B-20); falls back to per-cell reads.
    Each yielded row consumed exactly one ``Read()``, so after breaking out a
    caller can keep calling ``reader.Read()`` to count the remainder.
    """
    buffer = None
    if columns:
        try:
            from System import Array, Object  # type: ignore

            buffer = Array.CreateInstance(Object, len(columns))
        except Exception:  # noqa: BLE001 — no CLR (tests) or unusual build
            buffer = None
    while reader.Read():
        row: Dict[str, Any] = {}
        values = None
        if buffer is not None:
            try:
                reader.GetValues(buffer)
                values = list(buffer)
            except Exception:  # noqa: BLE001
                values = None
                buffer = None
        for i, col in enumerate(columns):
            try:
                raw = values[i] if values is not None else reader.GetValue(i)
                row[col] = clr_to_py(raw)
            except Exception as col_error:  # noqa: BLE001
                logger.warning("Error reading column %s: %s", col, col_error)
                row[col] = "<read_error>"
        yield row


def read_rows(
    reader: Any, max_rows: Optional[int]
) -> "tuple[List[str], List[Dict[str, Any]], bool]":
    """Materialise up to ``max_rows`` rows of the reader's current result set.

    Returns ``(columns, rows, truncated)``. ``truncated`` is decided by a single
    peek ``Read()`` past the cap, so a result of exactly ``max_rows`` rows is not
    reported as truncated. Values go through :func:`clr_to_py`.
    """
    columns = reader_columns(reader)
    cap = int(max_rows) if max_rows is not None and int(max_rows) > 0 else None
    rows: List[Dict[str, Any]] = []
    if cap is None:
        rows.extend(iter_reader_rows(reader, columns))
        return columns, rows, False
    for row in iter_reader_rows(reader, columns):
        rows.append(row)
        if len(rows) >= cap:
            break
    truncated = len(rows) >= cap and bool(reader.Read())
    return columns, rows, truncated


# ---------------------------------------------------------------------------
# Command-timeout budget (B-19)
# ---------------------------------------------------------------------------

_budget_local = threading.local()


@contextmanager
def command_budget(seconds: Optional[float]) -> Iterator[None]:
    """Publish the remaining time budget (seconds) for ADOMD commands on this thread.

    The dispatcher should wrap each handler call with
    ``command_budget(tool_timeout - 5)`` so the ADOMD ``CommandTimeout`` always
    fires before the dispatcher gives up (the worker then releases the gate
    instead of reading on after the caller got a timeout envelope).
    Nested budgets keep the tighter one.
    """
    previous = getattr(_budget_local, "deadline", None)
    if seconds is not None and seconds > 0:
        deadline = time.monotonic() + float(seconds)
        if previous is not None:
            deadline = min(deadline, previous)
        _budget_local.deadline = deadline
    try:
        yield
    finally:
        _budget_local.deadline = previous


def effective_command_timeout(default_seconds: float) -> int:
    """CommandTimeout (whole seconds, >= 1) to use for the next ADOMD command.

    The dispatcher-published budget wins when present — it is the time the
    caller will actually wait. Otherwise ``default_seconds`` (the executor's
    configured timeout) applies.
    """
    deadline = getattr(_budget_local, "deadline", None)
    if deadline is not None:
        remaining = deadline - time.monotonic()
        return max(1, int(remaining))
    try:
        return max(1, int(default_seconds))
    except (TypeError, ValueError):
        return 55


def apply_command_timeout(cmd: Any, default_seconds: float) -> Optional[int]:
    """Set ``cmd.CommandTimeout`` from the budget; returns the value set (or None)."""
    value = effective_command_timeout(default_seconds)
    try:
        if hasattr(cmd, "CommandTimeout"):
            cmd.CommandTimeout = int(value)
            return int(value)
    except Exception:  # noqa: BLE001 — CLR property may be read-only on fakes
        pass
    return None


# ---------------------------------------------------------------------------
# Command gate (B-01)
# ---------------------------------------------------------------------------


class CommandGate:
    """Serialise ADOMD commands on one connection and track the in-flight ones.

    * :meth:`hold` — re-entrant (same thread may nest) lock acquired with a
      timeout; raises :class:`ConnectionBusy` instead of blocking forever.
    * :meth:`track` — register a command object while it executes so
      :meth:`cancel_all` (called from another thread, e.g. the dispatcher's
      timeout path) can ``Cancel()`` it and let the holder release the gate.
    """

    def __init__(self, default_timeout: float = DEFAULT_GATE_TIMEOUT_S) -> None:
        self._lock = threading.RLock()
        self._default_timeout = default_timeout
        self._active: Dict[int, Any] = {}
        self._active_lock = threading.Lock()
        self._holder: Optional[str] = None
        self._holder_since: Optional[float] = None
        self._depth = 0

    @contextmanager
    def hold(self, timeout: Optional[float] = None, purpose: str = "") -> Iterator[None]:
        wait = self._default_timeout if timeout is None else max(0.0, float(timeout))
        acquired = self._lock.acquire(timeout=wait) if wait > 0 else self._lock.acquire(False)
        if not acquired:
            holder = self._holder
            since = self._holder_since
            held_for = round(time.monotonic() - since, 1) if since else None
            detail = f" (held by {holder} for {held_for}s)" if holder else ""
            raise ConnectionBusy(
                f"Power BI connection busy: another command is still running{detail}",
                retry_after=2.0,
                holder=holder,
            )
        self._depth += 1
        if self._depth == 1:
            self._holder = f"{threading.current_thread().name}:{purpose or 'query'}"
            self._holder_since = time.monotonic()
        try:
            yield
        finally:
            self._depth -= 1
            if self._depth == 0:
                self._holder = None
                self._holder_since = None
            self._lock.release()

    @contextmanager
    def track(self, cmd: Any) -> Iterator[Any]:
        key = id(cmd)
        with self._active_lock:
            self._active[key] = cmd
        try:
            yield cmd
        finally:
            with self._active_lock:
                self._active.pop(key, None)

    def cancel_all(self) -> int:
        """Call ``Cancel()`` on every tracked in-flight command. Returns how many."""
        with self._active_lock:
            commands = list(self._active.values())
        cancelled = 0
        for cmd in commands:
            try:
                cmd.Cancel()
                cancelled += 1
            except Exception as exc:  # noqa: BLE001 — CLR interop
                logger.debug("Cancel() failed on in-flight command: %s", exc)
        if cancelled:
            logger.warning("Cancelled %d in-flight ADOMD command(s)", cancelled)
        return cancelled

    def active_count(self) -> int:
        with self._active_lock:
            return len(self._active)

    def status(self) -> Dict[str, Any]:
        since = self._holder_since
        return {
            "busy": self._holder is not None,
            "holder": self._holder,
            "held_for_s": round(time.monotonic() - since, 2) if since else None,
            "active_commands": self.active_count(),
        }
