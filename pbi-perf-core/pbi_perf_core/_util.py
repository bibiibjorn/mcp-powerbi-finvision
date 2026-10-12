"""Shared helpers: tolerant datetime handling and numeric coercion.

Trace events reach us from three places with three different time
representations: pythonnet ``System.DateTime`` proxies, ISO strings coming
back from a JSON export, and native ``datetime`` objects in tests.  Everything
in this package funnels through :func:`to_datetime` so the algorithms only ever
see aware/naive ``datetime`` objects.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

__all__ = ["to_datetime", "to_int", "to_float", "iso", "ms_between"]


def to_datetime(value: Any) -> _dt.datetime | None:
    """Coerce *value* to ``datetime``; return ``None`` when not convertible."""
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        return value
    # pythonnet System.DateTime exposes year/month/day/... attributes.
    if hasattr(value, "Year") and hasattr(value, "Month"):
        try:
            micro = 0
            if hasattr(value, "Millisecond"):
                micro = int(value.Millisecond) * 1000
            return _dt.datetime(
                int(value.Year),
                int(value.Month),
                int(value.Day),
                int(value.Hour),
                int(value.Minute),
                int(value.Second),
                micro,
            )
        except Exception:  # pragma: no cover - defensive
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        # ``fromisoformat`` on 3.10 rejects a trailing Z and >6-digit fractions.
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return _dt.datetime.fromisoformat(text)
        except ValueError:
            pass
        # Trim over-long fractional seconds (SSAS emits 7 digits).
        if "." in text:
            head, _, tail = text.partition(".")
            digits = ""
            for ch in tail:
                if ch.isdigit():
                    digits += ch
                else:
                    tail = tail[len(digits) :]
                    break
            else:
                tail = ""
            try:
                return _dt.datetime.fromisoformat(f"{head}.{digits[:6]}{tail}")
            except ValueError:
                return None
    return None


def to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def iso(value: _dt.datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def ms_between(start: _dt.datetime | None, end: _dt.datetime | None) -> float:
    """Milliseconds from *start* to *end*; 0.0 when either side is missing."""
    if start is None or end is None:
        return 0.0
    return (end - start).total_seconds() * 1000.0
