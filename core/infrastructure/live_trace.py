"""Long-lived trace session: capture every query while visuals are driven (TRC-01).

``13_Query_Plan`` ``trace_start`` opens one unfiltered local AMO trace and keeps
it running while the user (or ``pbi-webview2``) clicks through the report;
``trace_fetch`` groups the captured events per query (ActivityID) and returns
DAX Studio-style server timings for each; ``trace_stop`` ends it. A headless
Performance Analyzer for a whole page interaction, complementing the one-shot
``04_Run_DAX mode=trace``.

Constraints worth knowing:

* Only ONE trace may be open per process (``trace_session._TRACE_LOCK``). While a
  live trace runs, ``04_Run_DAX mode=trace`` / ``13_Query_Plan capture`` wait and
  then report ``trace_busy`` — stop the live trace first.
* The trace auto-stops after ``max_duration_s`` (default 300 s, max 3600 s) and
  once ``max_events`` raw events are buffered, so a forgotten session cannot
  keep a server-wide trace running for hours. The cap is enforced in the
  session's callback buffer (oldest rows dropped, counted in ``events_dropped``)
  and by a background pump every ``_PUMP_INTERVAL_S`` seconds, not only at the
  next ``trace_fetch``.
* A local Desktop trace is unfiltered, so it sees every session's queries —
  that is the point (it catches the report's own visual queries).
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_MAX_DURATION_S = 300
MAX_DURATION_CAP_S = 3600
DEFAULT_MAX_EVENTS = 200_000

#: Background pull cadence: enforces ``max_events`` even when nobody fetches (N-12).
_PUMP_INTERVAL_S = 5.0

_QUERY_BEGIN, _QUERY_END = 9, 10
_SE_BEGIN = 82
_EXEC_METRICS = 136


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class _LiveTrace:
    """State of the single live trace (module singleton ``_STATE``)."""

    def __init__(
        self,
        session: Any,
        *,
        max_duration_s: int,
        max_events: int,
        label: Optional[str],
    ) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.session = session
        self.label = label
        self.started_at = _utc()
        self.started_monotonic = time.monotonic()
        self.max_duration_s = max_duration_s
        self.max_events = max_events
        self.events: List[Dict[str, Any]] = []
        self.stopped_reason: Optional[str] = None
        self.lock = threading.Lock()
        self.timer: Optional[threading.Timer] = None
        self.pump_timer: Optional[threading.Timer] = None
        #: events discarded at the cap (our buffer + the session's callback buffer)
        self.events_dropped = 0
        self._session_dropped_seen = 0

    def elapsed_s(self) -> float:
        return round(time.monotonic() - self.started_monotonic, 1)


_STATE: Optional[_LiveTrace] = None
_STATE_LOCK = threading.Lock()
#: True while ``start`` opens its session OUTSIDE ``_STATE_LOCK`` (N-34).
_STARTING = False


def _default_session_factory(connection_string: str):
    from core.infrastructure.trace_session import TraceSession

    return TraceSession(connection_string, local=True)


def _session_dropped(session: Any) -> int:
    value = getattr(session, "events_dropped", 0) if session is not None else 0
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _absorb(state: _LiveTrace, drained: List[Dict[str, Any]], session: Any) -> int:
    """Append drained events within the cap; count what did not fit. Returns the overflow."""
    seen_now = _session_dropped(session)
    with state.lock:
        room = max(state.max_events - len(state.events), 0)
        state.events.extend(drained[:room])
        overflow = len(drained) - room
        if overflow > 0:
            state.events_dropped += overflow
        new_session_drops = seen_now - state._session_dropped_seen
        if new_session_drops > 0:
            state._session_dropped_seen = seen_now
            state.events_dropped += new_session_drops
            overflow += new_session_drops
    return overflow


def _pull(state: _LiveTrace) -> List[Dict[str, Any]]:
    """Move newly delivered events from the session into our buffer (returns them)."""
    session = state.session
    drained = session._drain_events() if session is not None else []
    overflow = _absorb(state, drained, session)
    if overflow > 0 and state.stopped_reason is None:
        state.stopped_reason = f"max_events ({state.max_events}) reached"
        _close_session(state)
    return drained


#: The engine releases trace events in batches about every 2 s (measured on
#: Desktop 2026-09-30), so a flush waits for its own ping to come back, capped here.
_FLUSH_MAX_S = 3.0


def _flush(state: _LiveTrace, max_wait_s: float = _FLUSH_MAX_S) -> None:
    """Nudge the engine's trace buffer, then pull until it has drained past the nudge.

    Live 2026-09-30: a fetch right after the report's queries saw only our own
    start-up pings, because Desktop holds QueryBegin/End + SE events until its
    next batch. A cheap DMV ping (filtered out as internal) is sent; once ITS
    QueryEnd arrives, everything before it has been delivered.
    """
    session = state.session
    if session is None:
        return
    ping = getattr(session, "_execute_scalar_rows", None)
    if ping is None:  # a session that cannot ping (test doubles): one plain pull
        _pull(state)
        return
    from core.infrastructure.trace_session import _PING_HEADER, _event_id

    # Comments are stripped from DMV event text, so the token lives in the statement
    # (a string compare on SESSION_ID is rejected by the engine; a negative SPID is not).
    token = str(-(uuid.uuid4().int % 900_000_000 + 100_000_000))
    try:
        ping(
            f"{_PING_HEADER} SELECT SESSION_ID FROM $SYSTEM.DISCOVER_SESSIONS WHERE SESSION_SPID = {token}"
        )
    except Exception as exc:  # noqa: BLE001 - the pull below still runs
        logger.debug("live trace flush ping failed: %s", exc)
        _pull(state)
        return
    deadline = time.monotonic() + max(max_wait_s, 0.0)
    while state.session is not None:
        if any(
            _event_id(e) == _QUERY_END and token in str(e.get("text_data") or "")
            for e in _pull(state)
        ):
            return
        if time.monotonic() >= deadline:
            return
        time.sleep(0.1)


def _close_session(state: _LiveTrace) -> None:
    session, state.session = state.session, None
    if state.timer is not None:
        state.timer.cancel()
    if state.pump_timer is not None:
        state.pump_timer.cancel()
    if session is not None:
        try:
            # Collect whatever arrived last before the trace is dropped.
            _absorb(state, session._drain_events(), session)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("live trace final drain failed: %s", exc)
        try:
            session.close()
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("live trace close failed: %s", exc)


def _auto_stop(state: Any) -> None:
    """Duration timer: close the session it owns, registered or not (N-13)."""
    with _STATE_LOCK:
        if not isinstance(state, _LiveTrace):  # legacy callers passed the trace id
            state = _STATE if _STATE is not None and _STATE.id == state else None
        if state is None or state.session is None:
            return
        _pull(state)
        if state.session is None:  # the pull hit the cap and already closed it
            return
        state.stopped_reason = (
            state.stopped_reason or f"max_duration_s ({state.max_duration_s}) reached"
        )
        _close_session(state)
        logger.info("Live trace %s auto-stopped: %s", state.id, state.stopped_reason)


def _schedule_pump(state: _LiveTrace) -> None:
    timer = threading.Timer(_PUMP_INTERVAL_S, _pump, args=(state,))
    timer.daemon = True
    state.pump_timer = timer
    timer.start()


def _pump(state: _LiveTrace) -> None:
    """Background pull so ``max_events`` is enforced between fetches (N-12)."""
    with _STATE_LOCK:
        if state.session is None:
            return
        try:
            _pull(state)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("live trace pump failed: %s", exc)
        if state.session is not None:
            _schedule_pump(state)


def start(
    connection_string: str,
    *,
    max_duration_s: Optional[int] = None,
    max_events: Optional[int] = None,
    label: Optional[str] = None,
    session_factory: Optional[Callable[[str], Any]] = None,
) -> Dict[str, Any]:
    """Open the live trace. Refuses while one is already running."""
    global _STATE
    try:
        duration = DEFAULT_MAX_DURATION_S if max_duration_s is None else int(max_duration_s)
        cap = DEFAULT_MAX_EVENTS if max_events is None else int(max_events)
    except (TypeError, ValueError):
        duration, cap = -1, -1
    if duration <= 0 or duration > MAX_DURATION_CAP_S or cap <= 0:
        return {
            "success": False,
            "error_type": "invalid_parameters",
            "error": f"max_duration_s must be 1..{MAX_DURATION_CAP_S} and max_events > 0",
        }
    global _STARTING
    with _STATE_LOCK:
        if _STARTING:
            return {
                "success": False,
                "error_type": "trace_already_running",
                "error": "A live trace is already starting.",
                "suggestion": "Retry trace_start / trace_fetch in a moment.",
            }
        if _STATE is not None and _STATE.session is not None:
            return {
                "success": False,
                "error_type": "trace_already_running",
                "error": f"A live trace is already running (id {_STATE.id}, {_STATE.elapsed_s()} s).",
                "suggestion": "trace_fetch to read it, trace_stop to end it.",
                "trace_id": _STATE.id,
            }
        _STARTING = True
    # N-34: opening can wait up to 45 s for the process-wide trace lock; do it OUTSIDE
    # _STATE_LOCK so status / fetch / stop stay responsive meanwhile.
    session = None
    state = None
    try:
        try:
            session = (session_factory or _default_session_factory)(connection_string)
            session.open()
        except Exception as exc:
            name = type(exc).__name__
            error_type = "trace_busy" if name == "TraceBusy" else "trace_unavailable"
            return {"success": False, "error_type": error_type, "error": f"{name}: {exc}"}
        setter = getattr(session, "set_max_buffered_events", None)
        if callable(setter):
            try:
                setter(cap)  # N-12: bound the callback buffer between pulls
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("live trace buffer cap not applied: %s", exc)
        state = _LiveTrace(session, max_duration_s=duration, max_events=cap, label=label)
        with _STATE_LOCK:
            state.timer = threading.Timer(duration, _auto_stop, args=(state,))
            state.timer.daemon = True
            state.timer.start()
            _schedule_pump(state)
            _STATE = state
    finally:
        with _STATE_LOCK:
            _STARTING = False
        if state is None and session is not None:
            try:  # never leave an opened session holding the process-wide trace lock
                session.close()
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("live trace close after failed start: %s", exc)
    return {
        "success": True,
        "trace_id": state.id,
        "started_at": state.started_at,
        "max_duration_s": duration,
        "max_events": cap,
        "trace_delivering_events": bool(getattr(session, "_trace_started", True)),
        "note": (
            "Unfiltered local trace: drive the report now, then trace_fetch / trace_stop. "
            "Other traces (04_Run_DAX mode=trace, 13_Query_Plan capture) wait until it stops."
        ),
    }


def _group_queries(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Events -> one group per query, in arrival order.

    Keyed on RequestID first: every command on one connection shares the
    connection's ActivityID (live 2026-09-30 — two report queries on one
    session collapsed into one), while RequestID is per command.
    """
    from core.infrastructure.trace_session import _event_id, _is_internal_text

    groups: Dict[str, List[Dict[str, Any]]] = {}
    order: List[str] = []
    for ev in events:
        if _is_internal_text(ev.get("text_data")):
            continue
        act = ev.get("request_id") or ev.get("activity_id") or ""
        if not act:
            continue
        if act not in groups:
            groups[act] = []
            order.append(act)
        groups[act].append(ev)

    out: List[Dict[str, Any]] = []
    for act in order:
        evs = [e for e in groups[act] if _event_id(e) != _SE_BEGIN]
        begin = next((e for e in evs if _event_id(e) == _QUERY_BEGIN), None)
        end = next((e for e in evs if _event_id(e) == _QUERY_END), None)
        if begin is None and end is None:
            continue  # SE-only fragments without their query
        if (begin or end or {}).get("event_subclass") not in (None, "DAXQuery"):
            continue  # DMV / discover traffic
        out.append({"request_id": act, "events": evs, "begin": begin, "end": end})
    return out


def _summarize(
    group: Dict[str, Any], *, include_text: bool, text_limit: int, keep_text: bool = False
) -> Dict[str, Any]:
    from pbi_perf_core.server_timings import process_results

    begin, end = group["begin"] or {}, group["end"] or {}
    text = begin.get("text_data") or end.get("text_data") or ""
    row: Dict[str, Any] = {
        "request_id": group["request_id"],
        "application": begin.get("application_name") or end.get("application_name"),
        "start": str(begin.get("start_time") or end.get("start_time") or ""),
        "complete": end is not None and bool(end),
    }
    try:
        t = process_results(group["events"], command_text=text or None)
        row.update(
            total_ms=getattr(t, "total_duration", None),
            fe_ms=getattr(t, "formula_engine_duration", None),
            se_ms=getattr(t, "storage_engine_duration", None),
            se_cpu_ms=getattr(t, "storage_engine_cpu", None),
            se_queries=getattr(t, "storage_engine_query_count", None),
            se_cache_hits=getattr(t, "vertipaq_cache_matches", None),
            fe_pct=getattr(t, "fe_pct", None),
            se_pct=getattr(t, "se_pct", None),
            callbacks=getattr(t, "callback_count", None),
        )
    except Exception as exc:  # timings are best-effort per query
        row["total_ms"] = end.get("duration_ms")
        row["timings_error"] = f"{type(exc).__name__}: {exc}"
    if include_text:
        row["query"] = text if len(text) <= text_limit else text[:text_limit] + " …"
    else:
        row["query_preview"] = " ".join(text.split())[:160]
    # F-02: the engine's ApplicationContext (VisualId / ReportId) when the event carries one.
    # Whether Desktop fills it locally is unverified, so it is only ever an optional extra.
    try:
        from core.debug.trace_visual_match import application_context_ref

        ref = application_context_ref(group["events"])
        if ref:
            row["application_context"] = ref
    except Exception as exc:  # noqa: BLE001 - never let the visual hint break a fetch
        logger.debug("application context parse failed: %s", exc)
    if keep_text:
        row["_text"] = text  # full DAX for the visual matcher; stripped before the result leaves
    return row


def _result_from(
    state: _LiveTrace,
    events: List[Dict[str, Any]],
    *,
    since: int = 0,
    include_text: bool = False,
    text_limit: int = 4000,
    slowest_first: bool = False,
    keep_text: bool = False,
) -> Dict[str, Any]:
    """Group a copy of the buffered events into the fetch/stop result (no locks held)."""
    queries = [
        _summarize(g, include_text=include_text, text_limit=text_limit, keep_text=keep_text)
        for g in _group_queries(events)
    ]
    for i, q in enumerate(queries):
        q["seq"] = i
    queries = [q for q in queries if q["seq"] >= int(since or 0)]
    if slowest_first:
        queries.sort(key=lambda q: -(q.get("total_ms") or 0))
    return {
        "success": True,
        "trace_id": state.id,
        "running": state.session is not None,
        "stopped_reason": state.stopped_reason,
        "elapsed_s": state.elapsed_s(),
        "raw_events": len(events),
        "events_dropped": state.events_dropped,
        "query_count": len(queries),
        "total_query_ms": sum(q.get("total_ms") or 0 for q in queries),
        "queries": queries,
        "next_since": (max((q["seq"] for q in queries), default=int(since or 0) - 1) + 1),
    }


def fetch(
    *,
    since: int = 0,
    include_text: bool = False,
    text_limit: int = 4000,
    slowest_first: bool = False,
    keep_text: bool = False,
) -> Dict[str, Any]:
    """Queries captured so far (the trace keeps running).

    ``keep_text=True`` leaves each row's full DAX under the private ``_text`` key for in-process
    consumers (the visual matcher); callers must strip it before returning the result.
    """
    with _STATE_LOCK:
        state = _STATE
        if state is None:
            return {
                "success": False,
                "error_type": "no_trace",
                "error": "No live trace. Use trace_start.",
            }
        if state.session is not None:
            _flush(state)
        with state.lock:
            events = list(state.events)
        running = state.session is not None
    result = _result_from(
        state,
        events,
        since=since,
        include_text=include_text,
        text_limit=text_limit,
        slowest_first=slowest_first,
        keep_text=keep_text,
    )
    result["running"] = running
    return result


def stop(**fetch_kwargs: Any) -> Dict[str, Any]:
    """End the live trace and return the final capture.

    Flush, close and de-register happen in ONE critical section (N-13): a trace_start
    arriving while the (slow) grouping below runs sees a free slot and is not wiped.
    """
    global _STATE
    with _STATE_LOCK:
        state = _STATE
        if state is None:
            return {
                "success": False,
                "error_type": "no_trace",
                "error": "No live trace is running.",
            }
        if state.session is not None:
            _flush(state)
            state.stopped_reason = state.stopped_reason or "trace_stop"
            _close_session(state)
        with state.lock:
            events = list(state.events)
        _STATE = None
    result = _result_from(state, events, **fetch_kwargs)
    result["stopped"] = True
    return result


def status() -> Dict[str, Any]:
    with _STATE_LOCK:
        state = _STATE
        if state is None:
            return {"running": False, "starting": True} if _STARTING else {"running": False}
        return {
            "running": state.session is not None,
            "trace_id": state.id,
            "elapsed_s": state.elapsed_s(),
            "stopped_reason": state.stopped_reason,
            "buffered_events": len(state.events),
            "events_dropped": state.events_dropped,
        }


def reset_for_tests() -> None:
    global _STATE, _STARTING
    with _STATE_LOCK:
        if _STATE is not None:
            _close_session(_STATE)
        _STATE = None
        _STARTING = False
