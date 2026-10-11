"""In-process AMO trace session (pythonnet) for DAX Studio-parity timings.

Replaces the ``DaxExecutor.exe`` subprocess: the exe was built 2026-03-03, the
plan-capture code was added to its source 2026-08-22, and there is no .NET SDK
on this machine to rebuild it — which is why every query plan came back empty.
Running the trace in-process removes the build step entirely and lets the
aggregation maths live in one place (``pbi_perf_core``), shared with the Fabric
server.

The flow mirrors DAX Studio's ``QueryTraceEngine``: subscribe a server-level
trace, ping until it delivers its first event, run the query, then wait out a
settle window because the engine delivers events in batches roughly 2.3 s after
the query itself returns.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from xml.sax.saxutils import escape

logger = logging.getLogger(__name__)

#: Marks our own housekeeping queries so their events never reach the aggregator.
INTERNAL_QUERY_HEADER = "/* <<MCP Internal>> */"
_PING_HEADER = "/* PING */"

_TRACE_NAME_PREFIX = "MCP_Trace"

#: Trace event ids we subscribe to. The heartbeat trio (36/15/10) is always
#: present so we can tell the trace has actually started, exactly as DAX Studio
#: does; the rest is what Server Timings + Query Plan consume.
_HEARTBEAT_EVENTS = (36, 15, 10)  # DiscoverBegin, CommandBegin, QueryEnd
_TIMING_EVENTS = (9, 11, 82, 83, 85, 99, 131, 136, 112)
_EVAL_LOG_EVENT = 135  # DAXEvaluationLog, only for evaluate_and_log

#: TraceColumn ids DAX Studio requests, intersected at runtime with what
#: DISCOVER_TRACE_EVENT_CATEGORIES advertises for each event class.
_DESIRED_COLUMNS = (
    0,  # EventClass
    1,  # EventSubclass
    2,  # CurrentTime
    3,  # StartTime
    4,  # EndTime
    5,  # Duration
    6,  # CpuTime
    10,  # ObjectPath
    13,  # Error
    22,  # IntegerData
    24,  # ObjectName
    25,  # ObjectReference
    28,  # DatabaseName
    32,  # NTUserName
    37,  # ApplicationName
    39,  # SessionID
    41,  # Spid
    42,  # TextData
    45,  # RequestProperties
    46,  # ActivityID
    47,  # RequestID
    56,  # RequestParameters
)

#: Column id -> the key the pbi_perf_core TraceEvent contract expects.
_COLUMN_FIELDS = {
    0: "event_class",
    1: "event_subclass",
    2: "current_time",
    3: "start_time",
    4: "end_time",
    5: "duration_ms",
    6: "cpu_time_ms",
    22: "integer_data",
    24: "object_name",
    37: "application_name",
    39: "session_id",
    42: "text_data",
    46: "activity_id",
    47: "request_id",
    56: "request_parameters",
}

#: AMO exposes each trace column as a named property on the event args.
_EVENT_PROPERTIES = {
    "EventClass": "event_class",
    "EventSubclass": "event_subclass",
    "CurrentTime": "current_time",
    "StartTime": "start_time",
    "EndTime": "end_time",
    "Duration": "duration_ms",
    "CpuTime": "cpu_time_ms",
    "IntegerData": "integer_data",
    "ObjectName": "object_name",
    "ApplicationName": "application_name",
    "SessionID": "session_id",
    "TextData": "text_data",
    "ActivityID": "activity_id",
    "RequestID": "request_id",
    "RequestParameters": "request_parameters",
}

#: `<EVENT><ID>112</ID>...<EVENTCOLUMN><ID>42</ID>` — the DMV returns one XML
#: blob per category, so the event id and its column ids come out of one scan.
_EVENT_RE = re.compile(r"<EVENT>(.*?)</EVENT>", re.DOTALL | re.IGNORECASE)
_ID_RE = re.compile(r"<ID>\s*(\d+)\s*</ID>", re.IGNORECASE)
_COLUMNLIST_RE = re.compile(r"<EVENTCOLUMNLIST>(.*?)</EVENTCOLUMNLIST>", re.DOTALL | re.IGNORECASE)

_PING_INTERVAL_S = 0.25
#: B-10: a local Desktop trace that delivers nothing in 5 s will not start
#: delivering later (the old 30 s wait just produced plausible zeros ~40 s later).
_LOCAL_START_TIMEOUT_S = 5
_REMOTE_START_TIMEOUT_S = 90
#: Quiet time on OUR activity after QueryEnd (+ ExecutionMetrics) before the
#: settle window may end early (B-08/B-33).
_OUR_QUIET_S = 0.3
#: B-09: one in-process trace at a time. Every session does a database-wide
#: ClearCache and (locally) an unfiltered trace, so two concurrent traces
#: pollute each other; DAX Studio effectively serialises the same way.
_TRACE_LOCK = threading.Lock()
_TRACE_LOCK_TIMEOUT_S = 45.0
#: How long the settle window may be extended while plan/metrics events keep
#: arriving, so a 10k-row plan is never truncated by an impatient timer.
_MAX_SETTLE_EXTENSION_S = 30


class TraceUnavailable(RuntimeError):
    """Raised when pythonnet or the AMO assemblies cannot be loaded."""


class TraceBusy(RuntimeError):
    """Raised when another in-process trace holds the trace lock (B-09)."""


@dataclass
class TraceResult:
    """Everything one traced query produced."""

    events: List[Dict[str, Any]] = field(default_factory=list)
    result_sets: List[Dict[str, Any]] = field(default_factory=list)
    timings: Any = None  # pbi_perf_core.ServerTimingsResult
    logical_plan: str = ""
    physical_plan: str = ""
    execution_metrics: Dict[str, Any] = field(default_factory=dict)
    evaluation_log: List[Dict[str, Any]] = field(default_factory=list)
    cache_cleared: bool = False
    activity_id: Optional[str] = None
    error: Optional[str] = None
    #: B-10: did the trace deliver ANY event (heartbeat included)?
    trace_started: bool = False
    #: Raw events received during the run, before correlation filtering.
    events_received: int = 0
    #: B-08: how our query's events were identified + what was dropped.
    correlation: Dict[str, Any] = field(default_factory=dict)
    #: F-10: was DAXEvaluationLog (135) subscribed for this run?
    eval_log_subscribed: bool = False

    @property
    def row_count(self) -> int:
        """Rows in the first result set — what a single-EVALUATE caller means."""
        return self.result_sets[0]["row_count"] if self.result_sets else 0


_trace_assemblies_loaded: Optional[bool] = None


def _load_trace_assemblies() -> bool:
    """Load the AMO/ADOMD build the WHOLE process uses (dll_paths selection).

    B-07: this used to AddReference DAX Studio's copy while query_executor had
    already bound the bundled one — mixed builds in one process, the most
    plausible cause of the 2026-09-02 "0 events" observation. Both now share
    dll_paths.get_dll_paths(); a trace that still delivers nothing is
    reported as trace_unavailable naming the loaded build (B-10).
    """
    global _trace_assemblies_loaded
    if _trace_assemblies_loaded is not None:
        return _trace_assemblies_loaded

    from core.infrastructure.dll_paths import load_adomd_assembly, load_amo_assemblies

    _trace_assemblies_loaded = bool(load_adomd_assembly() and load_amo_assemblies())
    return _trace_assemblies_loaded


def _clr_available() -> bool:
    try:
        import clr  # noqa: F401
    except Exception:
        return False
    return True


def _to_datetime(value: Any):
    """Convert a .NET DateTime (or anything datetime-ish) to a Python datetime."""
    if value is None:
        return None
    import datetime as _dt

    if isinstance(value, _dt.datetime):
        return value
    for attr in ("Year", "Month", "Day"):
        if not hasattr(value, attr):
            return None
    try:
        return _dt.datetime(
            value.Year,
            value.Month,
            value.Day,
            value.Hour,
            value.Minute,
            value.Second,
            value.Millisecond * 1000,
        )
    except Exception:
        return None


#: B-33: per-connection caches. The trace-capability map depends only on the
#: server build; the xmSQL name maps on (server, database, model generation).
_CAPABILITY_CACHE: Dict[str, Dict[int, set]] = {}
_NAME_MAP_CACHE: Dict[tuple, Any] = {}
_CACHE_LOCK = threading.Lock()


def _current_model_generation() -> int:
    try:
        from core.infrastructure.connection_state import connection_state

        gen = getattr(connection_state, "model_generation", 0)
        return gen if isinstance(gen, int) else 0
    except Exception:  # noqa: BLE001 — trace_session must not depend on server state
        return 0


def clear_trace_caches() -> None:
    """Drop the cached capability / name maps (tests, model switches)."""
    with _CACHE_LOCK:
        _CAPABILITY_CACHE.clear()
        _NAME_MAP_CACHE.clear()


class TraceSession:
    """One AMO trace over one ADOMD connection, for one or more queries.

    ``local`` selects the server-side filter. A local Desktop trace is
    unfiltered; our own events are then correlated client-side (B-08): our
    QueryBegin/QueryEnd is identified by OUR ``SessionID`` (fallback: identical
    query text), and only events carrying that ActivityID are kept. A shared
    XMLA endpoint gets the DAX Studio session filter so other users' traffic is
    not hauled across the wire.

    Only one TraceSession may be open per process at a time (B-09,
    ``_TRACE_LOCK``); a second one waits up to ``_TRACE_LOCK_TIMEOUT_S`` and
    then raises :class:`TraceBusy`.
    """

    def __init__(
        self,
        connection_string: str,
        *,
        local: bool = True,
        access_token: Optional[str] = None,
        access_token_expires: Optional[float] = None,
        capture_eval_log: bool = False,
    ) -> None:
        # .NET 8 resolves 'localhost' to IPv6 ::1 but Desktop's SSAS listens on
        # IPv4 only; normalise before either the trace or the query connects.
        self.connection_string = connection_string.replace("localhost:", "127.0.0.1:")
        self.local = local
        self.access_token = access_token
        # Unix seconds; the ADOMD / AMO AccessToken types need an expiry (DateTimeOffset).
        self.access_token_expires = access_token_expires

        self._events: List[Dict[str, Any]] = []
        # N-12: optional bound on the callback buffer (live traces set it); the OLDEST
        # rows are dropped and counted so an unpulled trace cannot grow without limit.
        self._max_buffered: Optional[int] = None
        self._events_dropped = 0
        self._lock = threading.Lock()
        self._first_event = threading.Event()
        self._server = None
        self._trace = None
        self._connection = None
        self._session_id: Optional[str] = None
        self._database_name: Optional[str] = None
        self._supported: Dict[int, set] = {}
        self._xmsql_cleaner = None
        self._handler = None
        self._trace_started = False
        self._holds_trace_lock = False
        self._command_timeout_s: Optional[int] = None
        # F-10: the DAXEvaluationLog (135) subscription is decided when the trace
        # STARTS (open() / __enter__), i.e. before run() -- so the flag has to be
        # known at construction time.
        self._capture_eval_log = bool(capture_eval_log)
        self._eval_log_subscribed = False

    # -- lifecycle --------------------------------------------------------
    def __enter__(self) -> "TraceSession":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def open(self) -> None:
        """Connect ADOMD + AMO, discover trace capabilities, start the trace."""
        if not _clr_available():
            raise TraceUnavailable("pythonnet (clr) is not importable")

        if not _load_trace_assemblies():
            raise TraceUnavailable("AMO/ADOMD assemblies could not be loaded")

        if not self._holds_trace_lock:
            if not _TRACE_LOCK.acquire(timeout=_TRACE_LOCK_TIMEOUT_S):
                raise TraceBusy(
                    "Another trace is running in this server; traces are serialised "
                    "(each one clears the engine cache and sees every session's events)"
                )
            self._holds_trace_lock = True
        try:
            self._open_connection()
            self._supported = self._cached_supported_events()
            self._start_trace()
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        """Stop and drop the trace, close the query connection, release the lock.

        ``Drop()`` removes the server-side trace object. DAX Studio deliberately
        skips ``Dispose()`` afterwards because calling both sometimes hangs.
        """
        try:
            trace, self._trace = self._trace, None
            if trace is not None:
                try:
                    trace.Stop()
                except Exception as exc:
                    logger.debug("trace stop failed: %s", exc)
                try:
                    trace.Drop()
                except Exception as exc:
                    logger.debug("trace drop failed: %s", exc)

            server, self._server = self._server, None
            if server is not None:
                try:
                    server.Disconnect()
                except Exception as exc:
                    logger.debug("AMO disconnect failed: %s", exc)

            conn, self._connection = self._connection, None
            if conn is not None:
                try:
                    conn.Close()
                except Exception as exc:
                    logger.debug("ADOMD close failed: %s", exc)
        finally:
            if getattr(self, "_holds_trace_lock", False):
                self._holds_trace_lock = False
                try:
                    _TRACE_LOCK.release()
                except RuntimeError:  # pragma: no cover - defensive
                    pass

    # -- connection -------------------------------------------------------
    def _open_connection(self) -> None:
        from Microsoft.AnalysisServices.AdomdClient import AdomdConnection

        conn = AdomdConnection(self.connection_string)
        if self.access_token:
            # Live 2026-09-30: assigning the raw string raised, the error was swallowed at
            # debug level and the connection opened WITHOUT a token ("interactive
            # authentication is not supported"). Build the typed token; let a failure raise.
            from Microsoft.AnalysisServices.AdomdClient import AccessToken as AdomdAccessToken

            conn.AccessToken = AdomdAccessToken(self.access_token, self._token_expiry(), "")
        conn.Open()
        self._connection = conn
        self._session_id = str(getattr(conn, "SessionID", "") or "")
        try:
            self._database_name = str(conn.Database)
        except Exception:
            self._database_name = None

    def _new_command(self, command_text: str):
        from Microsoft.AnalysisServices.AdomdClient import AdomdCommand

        cmd = AdomdCommand(command_text, self._connection)
        timeout = getattr(self, "_command_timeout_s", None)
        if timeout:
            # B-19: a dispatcher-published budget (adomd_utils.command_budget)
            # wins over timeout_s so the engine gives up before the caller does.
            from core.infrastructure.adomd_utils import apply_command_timeout

            apply_command_timeout(cmd, int(timeout))
        return cmd

    def _execute_scalar_rows(self, command_text: str) -> List[Dict[str, Any]]:
        """Run a DMV/DAX statement and return its first result set as dicts.

        Values are converted with ``clr_to_py`` (B-06) — the xmSQL name maps and
        capability parsing only need strings/ints, never culture-formatted text.
        """
        from core.infrastructure.adomd_utils import clr_to_py

        cmd = self._new_command(command_text)
        reader = cmd.ExecuteReader()
        rows: List[Dict[str, Any]] = []
        try:
            names = [str(reader.GetName(i)) for i in range(reader.FieldCount)]
            while reader.Read():
                row: Dict[str, Any] = {}
                for i, name in enumerate(names):
                    try:
                        value = reader.GetValue(i)
                    except Exception:
                        value = None
                    row[name] = clr_to_py(value)
                rows.append(row)
        finally:
            reader.Close()
        return rows

    # -- capability discovery ---------------------------------------------
    def _cached_supported_events(self) -> Dict[int, set]:
        key = self.connection_string
        with _CACHE_LOCK:
            cached = _CAPABILITY_CACHE.get(key)
        if cached:
            return cached
        supported = self._discover_supported_events()
        if supported:
            with _CACHE_LOCK:
                _CAPABILITY_CACHE[key] = supported
        return supported

    def _discover_supported_events(self) -> Dict[int, set]:
        """Map ``{event_id: {column_id, ...}}`` from the trace-categories DMV.

        Requesting a column an event does not carry makes the whole trace fail
        with "does not contain the column", so every subscription is intersected
        with this map — QueryBegin has no Duration, ExecutionMetrics has neither
        SessionID nor StartTime.
        """
        supported: Dict[int, set] = {}
        try:
            rows = self._execute_scalar_rows(
                "SELECT * FROM $SYSTEM.DISCOVER_TRACE_EVENT_CATEGORIES"
            )
        except Exception as exc:
            logger.warning("DISCOVER_TRACE_EVENT_CATEGORIES failed: %s", exc)
            return supported

        for row in rows:
            for value in row.values():
                if not isinstance(value, str) or "<EVENT>" not in value.upper():
                    continue
                for block in _EVENT_RE.findall(value):
                    ids = _ID_RE.findall(block)
                    if not ids:
                        continue
                    event_id = int(ids[0])
                    columns = set()
                    for col_block in _COLUMNLIST_RE.findall(block):
                        columns.update(int(c) for c in _ID_RE.findall(col_block))
                    supported[event_id] = columns
        return supported

    # -- trace ------------------------------------------------------------
    def _trace_name(self) -> str:
        session = (self._session_id or "nosession").replace(" ", "")[:24]
        return f"{_TRACE_NAME_PREFIX}_{session}_{uuid.uuid4().hex[:8]}"

    def _connect_server(self):
        from Microsoft.AnalysisServices.Tabular import Server

        server = Server()
        if self.access_token:
            from Microsoft.AnalysisServices import AccessToken as AmoAccessToken

            server.AccessToken = AmoAccessToken(self.access_token, self._token_expiry(), "")
        server.Connect(self.connection_string)
        return server

    def _token_expiry(self):
        """The token's expiry as a .NET DateTimeOffset (default: one hour from now)."""
        from System import DateTimeOffset  # type: ignore

        expires = self.access_token_expires or (time.time() + 3600)
        return DateTimeOffset.FromUnixTimeSeconds(int(expires))

    @staticmethod
    def _events_collection(trace):
        """``Trace.Events`` collides with ``Component.Events`` under pythonnet.

        Attribute access resolves to the wrong member, so the property has to be
        fetched off the CLR type explicitly.
        """
        import clr

        return clr.GetClrType(trace.GetType()).GetProperty("Events").GetValue(trace)

    #: "The event Id=9 does not contain the column Id=4."
    _REJECTED_COLUMN_RE = re.compile(
        r"event Id=(\d+) does not contain the column Id=(\d+)", re.IGNORECASE
    )

    def _update_trace_dropping_rejected_columns(
        self, trace, UpdateOptions, UpdateMode, max_attempts: int = 24
    ) -> None:
        """Submit the trace, removing any column the server refuses.

        DISCOVER_TRACE_EVENT_CATEGORIES advertises the columns of a whole
        category, not of each individual event, so it over-reports: it lists
        EndTime for QueryBegin, which the server then rejects. One bad column
        fails the entire Update, so the rejection is parsed and that single
        column dropped before retrying, rather than losing the trace.
        """
        for _ in range(max_attempts):
            try:
                trace.Update(UpdateOptions.Default, UpdateMode.CreateOrReplace)
                return
            except Exception as exc:
                match = self._REJECTED_COLUMN_RE.search(str(exc))
                if not match:
                    raise
                event_id, column_id = int(match.group(1)), int(match.group(2))
                if not self._drop_column(trace, event_id, column_id):
                    raise
                logger.debug(
                    "Trace column %s unsupported for event %s; dropped",
                    column_id,
                    event_id,
                )
        raise TraceUnavailable("Could not agree a trace column set with the server")

    @classmethod
    def _drop_column(cls, trace, event_id: int, column_id: int) -> bool:
        """Remove one column from one event. False when it was not found.

        Goes through the CLR property because ``trace.Events`` resolves to
        ``Component.Events`` (an EventHandlerList), not the trace's own
        collection.
        """
        for trace_event in cls._events_collection(trace):
            if int(trace_event.EventID) != event_id:
                continue
            for column in list(trace_event.Columns):
                if int(column) == column_id:
                    trace_event.Columns.Remove(column)
                    return True
        return False

    def _start_trace(self) -> None:
        # TraceColumn and TraceEventClass exist ONLY in the core
        # Microsoft.AnalysisServices namespace - the .Tabular namespace
        # re-exports Trace/TraceEvent/TraceEventHandler but not the enums, so
        # importing the whole set from .Tabular fails at runtime.
        from Microsoft.AnalysisServices import (
            TraceColumn,
            TraceEventClass,
            UpdateMode,
            UpdateOptions,
        )
        from Microsoft.AnalysisServices.Tabular import TraceEventHandler

        self._server = self._connect_server()
        self.cleanup_stale_traces()

        trace = self._server.Traces.Add(self._trace_name())
        events = self._events_collection(trace)

        wanted = list(_HEARTBEAT_EVENTS) + list(_TIMING_EVENTS)
        if self._capture_eval_log:
            wanted.append(_EVAL_LOG_EVENT)
        self._eval_log_subscribed = bool(
            self._capture_eval_log and (self._supported or {}).get(_EVAL_LOG_EVENT)
        )

        added = 0
        for event_id in dict.fromkeys(wanted):
            columns = self._supported.get(event_id)
            if not columns:
                continue  # the server does not advertise it; subscribing would fail
            # The collection constructs the event itself: Add() takes the event
            # class enum and hands back the TraceEvent to add columns to.
            trace_event = events.Add(TraceEventClass(event_id))
            event_columns = trace_event.Columns
            for column_id in _DESIRED_COLUMNS:
                if column_id in columns:
                    event_columns.Add(TraceColumn(column_id))
            added += 1

        if not added:
            raise TraceUnavailable("Server advertises none of the required trace events")

        if not self.local:
            # AMO's Trace.Filter is an XmlNode, not a string (live 2026-09-30: the remote
            # lane failed with "'str' value cannot be converted to System.Xml.XmlNode").
            from System.Xml import XmlDocument  # type: ignore

            doc = XmlDocument()
            doc.LoadXml(self._session_filter())
            trace.Filter = doc.DocumentElement

        self._update_trace_dropping_rejected_columns(trace, UpdateOptions, UpdateMode)
        self._handler = TraceEventHandler(self._on_event)
        trace.OnEvent += self._handler
        trace.Start()
        self._trace = trace
        self._wait_for_trace_start()

    def _session_filter(self):
        """DAX Studio's server-side filter, for multi-user endpoints only.

        ExecutionMetrics (136) has no SessionID column, so it gets its own
        OR-clause and is correlated client-side by ActivityID.
        """
        ns = "http://schemas.microsoft.com/analysisservices/2003/engine"
        session = escape(self._session_id or "")
        return (
            f'<Or xmlns="{ns}">'
            f"<Equal><ColumnID>39</ColumnID><Value>{session}</Value></Equal>"
            f"<Equal><ColumnID>0</ColumnID><Value>136</Value></Equal>"
            f"</Or>"
        )

    def _wait_for_trace_start(self) -> None:
        """Ping a cheap DMV until the trace delivers its first event.

        A trace that has been created but not yet primed silently drops the
        beginning of the query, so we pay this cost up front. B-10: the local
        wait is 5 s (not 30), and whether an event ever arrived is recorded in
        ``_trace_started`` so the caller can report ``trace_unavailable``
        instead of success-with-zeros.
        """
        timeout = _LOCAL_START_TIMEOUT_S if self.local else _REMOTE_START_TIMEOUT_S
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                self._execute_scalar_rows(f"{_PING_HEADER} SELECT * FROM $SYSTEM.DISCOVER_SESSIONS")
            except Exception as exc:
                logger.debug("trace ping failed: %s", exc)
            if self._first_event.wait(_PING_INTERVAL_S):
                self._trace_started = True
                return
        self._trace_started = self._first_event.is_set()
        logger.warning("Trace produced no event within %ss; capturing anyway", timeout)

    # -- event callback ---------------------------------------------------
    def _on_event(self, sender, args) -> None:
        """AMO callback. Property reads only — this runs on a .NET thread.

        Anything expensive here (parsing, aggregation) would block the engine's
        trace dispatcher and can deadlock pythonnet, so the row is copied out
        under the lock and every interpretation happens later on our thread.
        """
        try:
            row: Dict[str, Any] = {}
            for prop, key in _EVENT_PROPERTIES.items():
                # Named properties, not args[column_id]: indexing by column id
                # raises for any column this event class does not carry, and
                # pythonnet surfaces that as an empty row rather than an error.
                try:
                    value = getattr(args, prop)
                except Exception:
                    continue
                if value is None:
                    continue
                if key in ("start_time", "end_time", "current_time"):
                    row[key] = _to_datetime(value)
                elif key in ("duration_ms", "cpu_time_ms", "integer_data"):
                    try:
                        row[key] = int(value)
                    except (TypeError, ValueError):
                        row[key] = 0
                else:
                    row[key] = value if isinstance(value, (int, float)) else str(value)
        except Exception as exc:  # pragma: no cover - defensive, .NET thread
            logger.debug("trace event read failed: %s", exc)
            return

        with self._lock:
            self._events.append(row)
            cap = getattr(self, "_max_buffered", None)
            if cap is not None and len(self._events) > cap:
                # Drop in chunks (~10 % of the cap) so a saturated buffer is not
                # shifted on every single event.
                drop = min(len(self._events), len(self._events) - cap + max(cap // 10, 1))
                del self._events[:drop]
                self._events_dropped = getattr(self, "_events_dropped", 0) + drop
        self._first_event.set()

    def set_max_buffered_events(self, cap: Optional[int]) -> None:
        """Bound the un-drained event buffer (None = unbounded, the default)."""
        with self._lock:
            self._max_buffered = int(cap) if cap else None

    @property
    def events_dropped(self) -> int:
        """Events discarded because the buffer was at its cap (cumulative)."""
        return int(getattr(self, "_events_dropped", 0))

    def _drain_events(self) -> List[Dict[str, Any]]:
        with self._lock:
            events = list(self._events)
            self._events.clear()
        return events

    # -- xmSQL name maps --------------------------------------------------
    _capture_eval_log = False

    def _get_xmsql_cleaner(self):
        """Build the id->name maps once per (server, database, model generation)."""
        if self._xmsql_cleaner is not None:
            return self._xmsql_cleaner

        key = (
            getattr(self, "connection_string", None),
            getattr(self, "_database_name", None),
            _current_model_generation(),
        )
        with _CACHE_LOCK:
            cached = _NAME_MAP_CACHE.get(key)
        if cached is not None:
            self._xmsql_cleaner = cached
            return cached

        from pbi_perf_core.xmsql import XmSqlCleaner, build_name_maps

        columns: List[Dict[str, Any]] = []
        tables: List[Dict[str, Any]] = []
        try:
            columns = self._execute_scalar_rows(
                "SELECT * FROM $SYSTEM.DISCOVER_STORAGE_TABLE_COLUMNS"
            )
        except Exception as exc:
            logger.debug("DISCOVER_STORAGE_TABLE_COLUMNS failed: %s", exc)
        try:
            tables = self._execute_scalar_rows("SELECT * FROM $SYSTEM.TMSCHEMA_TABLES")
        except Exception as exc:
            logger.debug("TMSCHEMA_TABLES failed: %s", exc)

        column_map, table_map = build_name_maps(columns, tables)
        self._xmsql_cleaner = XmSqlCleaner(column_map, table_map)
        if key[0] and (columns or tables):
            with _CACHE_LOCK:
                if len(_NAME_MAP_CACHE) > 16:
                    _NAME_MAP_CACHE.clear()
                _NAME_MAP_CACHE[key] = self._xmsql_cleaner
        return self._xmsql_cleaner

    # -- cache ------------------------------------------------------------
    def is_service_target(self) -> bool:
        """True for a published (XMLA) model: a remote trace with a bearer token."""
        return (not getattr(self, "local", True)) or bool(getattr(self, "access_token", None))

    def clear_cache(self) -> bool:
        """Drop the SE datacaches, then re-prime the session.

        The ClearCache batch alone leaves the session in a state where the first
        real query pays an unrelated warm-up cost, so DAX Studio follows it with
        a throwaway EVALUATE. Ours is tagged internal so its events are dropped.
        """
        if not self._database_name:
            return False
        if self.is_service_target():
            # N-11: never evict the VertiPaq cache of a published model (it is shared with
            # production users); the XMLA target is read-only.
            logger.info("ClearCache skipped: service (XMLA) target")
            return False

        xmla = (
            '<Batch xmlns="http://schemas.microsoft.com/analysisservices/2003/engine">'
            "<ClearCache><Object><DatabaseID>"
            f"{escape(self._database_name)}"
            "</DatabaseID></Object></ClearCache></Batch>"
        )
        try:
            self._new_command(xmla).ExecuteNonQuery()
        except Exception as exc:
            logger.warning("ClearCache failed: %s", exc)
            return False

        try:
            refresh = f'EVALUATE {INTERNAL_QUERY_HEADER} ROW("MCP Session Refresh", 0)'
            self._new_command(refresh).ExecuteNonQuery()
        except Exception as exc:
            logger.debug("session refresh failed: %s", exc)
        return True

    # -- query ------------------------------------------------------------
    def _execute_query(self, query: str, keep_rows: int = 0) -> List[Dict[str, Any]]:
        """Run the query and drain EVERY result set.

        A DAX query with several EVALUATE statements returns several result
        sets; reading only the first is why the second EVALUATE has always been
        invisible. Rows are counted, not materialised — a plan capture on a
        million-row query must not be an OOM — except the first ``keep_rows``
        rows of each set, which are kept (converted with ``clr_to_py``) so a
        traced call can return data AND timings from the SAME run (L-19).
        """
        from core.infrastructure.adomd_utils import clr_to_py

        cmd = self._new_command(query)
        reader = cmd.ExecuteReader()
        result_sets: List[Dict[str, Any]] = []
        try:
            while True:
                columns = [str(reader.GetName(i)) for i in range(reader.FieldCount)]
                rows = 0
                kept: List[Dict[str, Any]] = []
                while reader.Read():
                    if rows < keep_rows:
                        row: Dict[str, Any] = {}
                        for i, name in enumerate(columns):
                            try:
                                row[name] = clr_to_py(reader.GetValue(i))
                            except Exception:  # noqa: BLE001
                                row[name] = "<read_error>"
                        kept.append(row)
                    rows += 1
                entry: Dict[str, Any] = {"columns": columns, "row_count": rows}
                if keep_rows > 0:
                    entry["rows"] = kept
                    entry["truncated"] = rows > len(kept)
                result_sets.append(entry)
                if not reader.NextResult():
                    break
        finally:
            reader.Close()
        return result_sets

    def run(
        self,
        query: str,
        *,
        clear_cache: bool = True,
        capture_plan: bool = True,
        capture_eval_log: bool = False,
        settle_ms: int = 6000,
        timeout_s: int = 300,
        keep_rows: int = 0,
    ) -> TraceResult:
        """Execute ``query`` under the trace and aggregate everything it emitted."""
        if self._trace is None:
            self._capture_eval_log = self._capture_eval_log or capture_eval_log
        elif capture_eval_log and not self._capture_eval_log:
            logger.warning(
                "capture_eval_log requested after the trace started without it; "
                "construct TraceSession(capture_eval_log=True) (F-10)"
            )
        self._command_timeout_s = max(1, int(timeout_s)) if timeout_s else None
        if self._trace is None:
            self.open()

        # Live 2026-09-23: Desktop occasionally never delivers OUR QueryEnd + SE
        # events for a run (the trace then aggregates to zeros). Detect that and
        # re-run once; the result records how many attempts it took.
        result = self._run_once(
            query, clear_cache, capture_plan, capture_eval_log, settle_ms, timeout_s, keep_rows
        )
        if result.error is None and not any(_event_id(e) == 10 for e in result.events):
            logger.warning("trace incomplete (no QueryEnd for our query); re-running once")
            retry = self._run_once(
                query, clear_cache, capture_plan, capture_eval_log, settle_ms, timeout_s, keep_rows
            )
            retry.correlation = dict(retry.correlation or {}, attempts=2)
            if not any(_event_id(e) == 10 for e in retry.events):
                retry.correlation["incomplete"] = True
            return retry
        return result

    def _run_once(
        self, query, clear_cache, capture_plan, capture_eval_log, settle_ms, timeout_s, keep_rows
    ) -> "TraceResult":
        result = TraceResult()
        result.cache_cleared = self.clear_cache() if clear_cache else False
        received = len(self._drain_events())  # discard cache-clear + priming noise

        started = time.monotonic()
        try:
            result.result_sets = self._execute_query(query, keep_rows=keep_rows)
        except Exception as exc:
            result.error = str(exc)
            logger.warning("traced query failed: %s", exc)

        elapsed = time.monotonic() - started
        if elapsed > timeout_s:
            result.error = result.error or f"Query exceeded timeout_s={timeout_s}"

        events = self._settle(settle_ms, query=query)
        result.events_received = received + len(events)
        result.trace_started = bool(
            getattr(self, "_trace_started", False) or result.events_received > 0
        )
        self._aggregate(result, events, query, capture_plan, capture_eval_log)
        result.eval_log_subscribed = bool(getattr(self, "_eval_log_subscribed", False))
        if clear_cache and not result.cache_cleared and self.is_service_target():
            result.correlation = dict(
                result.correlation or {}, cache_not_cleared="cache not cleared (service target)"
            )
        return result

    def _settle(self, settle_ms: int, query: Optional[str] = None) -> List[Dict[str, Any]]:
        """Collect events until the stream goes quiet for ``settle_ms``.

        The engine hands trace events over in batches ~2.3 s after the query
        returns, so returning as soon as the query does would capture nothing.
        The window restarts on every new event, which keeps a 10k-row plan
        (delivered in several chunks) intact.

        B-08/B-33: the window ends EARLY once OUR QueryEnd (and ExecutionMetrics,
        when subscribed) arrived and OUR activity has been quiet for
        ``_OUR_QUIET_S`` — Desktop's own chatter no longer stretches every trace
        towards the +30 s extension.
        """
        window = max(settle_ms, 0) / 1000.0
        collected: List[Dict[str, Any]] = []
        now = time.monotonic()
        hard_deadline = now + window + _MAX_SETTLE_EXTENSION_S
        quiet_until = now + window
        session_id = getattr(self, "_session_id", None)
        supported = getattr(self, "_supported", {}) or {}
        want_metrics = 136 in supported
        our_activity: Optional[str] = None
        query_end_seen = False
        metrics_seen = False
        last_ours = now

        last_ping = now
        while time.monotonic() < hard_deadline and (
            time.monotonic() < quiet_until
            # Live 2026-09-23: Desktop sometimes holds OUR QueryEnd + SE events in its
            # trace buffer past the quiet window; stopping then reports zeros. While our
            # query has begun but not ended, keep waiting (to the hard deadline) and
            # nudge the buffer with a cheap ping (pings are filtered out as internal).
            or (our_activity is not None and not query_end_seen)
        ):
            if (
                our_activity is not None
                and not query_end_seen
                and time.monotonic() >= quiet_until
                and time.monotonic() - last_ping >= 1.0
            ):
                last_ping = time.monotonic()
                try:
                    self._execute_scalar_rows(
                        f"{_PING_HEADER} SELECT * FROM $SYSTEM.DISCOVER_SESSIONS"
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("settle ping failed: %s", exc)
            batch = self._drain_events()
            if batch:
                collected.extend(batch)
                quiet_until = time.monotonic() + window
                if our_activity is None:
                    our_activity, _method = _identify_our_activity(collected, session_id, query)
                if our_activity:
                    for ev in collected if not query_end_seen else batch:
                        if (
                            ev.get("activity_id") == our_activity
                            or _metrics_activity(ev) == our_activity
                        ):
                            last_ours = time.monotonic()
                            eid = _event_id(ev)
                            if eid == 10:
                                query_end_seen = True
                            elif eid == 136:
                                metrics_seen = True
            elif (
                our_activity
                and query_end_seen
                and (metrics_seen or not want_metrics)
                and time.monotonic() - last_ours >= _OUR_QUIET_S
            ):
                break
            else:
                time.sleep(0.05)

        collected.extend(self._drain_events())
        return collected

    # -- aggregation ------------------------------------------------------
    def _aggregate(
        self,
        result: TraceResult,
        events: List[Dict[str, Any]],
        query: str,
        capture_plan: bool,
        capture_eval_log: bool,
    ) -> None:
        from pbi_perf_core.execution_metrics import parse_execution_metrics
        from pbi_perf_core.server_timings import process_results

        events = [e for e in events if not _is_internal_text(e.get("text_data"))]
        # DMV pings and cache priming also raise QueryBegin/QueryEnd, labelled
        # DmxQuery. Left in, whichever one arrived first would be taken as "the
        # query" and its ~30 ms would replace the real total.
        events = [
            e
            for e in events
            if _event_id(e) not in (9, 10) or e.get("event_subclass") in (None, "DAXQuery")
        ]
        # VertiPaqSEQueryBegin mirrors every End with the same CpuTime. DAX
        # Studio aggregates the End rows only; keeping both doubles SE CPU.
        events = [e for e in events if _event_id(e) != 82]

        session_id = getattr(self, "_session_id", None)
        activity_id, method = _identify_our_activity(events, session_id, query)
        events, correlation = _filter_to_activity(events, activity_id, session_id)
        correlation["method"] = method
        result.correlation = correlation
        result.activity_id = activity_id
        result.events = events

        if capture_plan:
            logical, physical = _split_plans(events)
            result.logical_plan = logical
            result.physical_plan = physical

        if capture_eval_log:
            result.evaluation_log = _eval_log_rows(events)

        # ExecutionMetrics: prefer the payload whose activityID is ours;
        # otherwise (no id anywhere) the longest-running one, as before — a ping
        # reports single-digit ms against the real query's hundreds.
        best: Dict[str, Any] = {}
        best_ours: Dict[str, Any] = {}
        for event in events:
            if _event_id(event) != 136:
                continue
            metrics = parse_execution_metrics(event.get("text_data"))
            if not metrics:
                continue
            if activity_id and _metrics_activity(event) == activity_id:
                best_ours = metrics
            if metrics.get("durationMs", 0) >= best.get("durationMs", -1):
                best = metrics
        result.execution_metrics = best_ours or best

        result.timings = process_results(
            events,
            command_text=query,
            xmsql_cleaner=self._safe_cleaner(),
        )
        if result.execution_metrics and not result.timings.execution_metrics:
            result.timings.execution_metrics = result.execution_metrics

    def _safe_cleaner(self) -> Optional[Callable[[str], str]]:
        """Never let a DMV hiccup on the name maps cost us the whole capture."""
        try:
            return self._get_xmsql_cleaner()
        except Exception as exc:
            logger.debug("xmSQL cleaner unavailable: %s", exc)
            return None

    # -- housekeeping -----------------------------------------------------
    def _live_session_prefixes(self) -> Optional[set]:
        """24-char session prefixes (as embedded in trace names) of live sessions."""
        try:
            rows = self._execute_scalar_rows(
                f"{_PING_HEADER} SELECT [SESSION_ID] FROM $SYSTEM.DISCOVER_SESSIONS"
            )
        except Exception as exc:
            logger.debug("DISCOVER_SESSIONS failed: %s", exc)
            return None
        live = set()
        for row in rows:
            for value in row.values():
                if value:
                    live.add(str(value).replace(" ", "")[:24])
        return live

    def cleanup_stale_traces(self, prefix: str = _TRACE_NAME_PREFIX) -> int:
        """Drop leftover MCP traces whose owning session is gone.

        A trace object survives the process that made it, and each one keeps
        costing the engine, so they are swept on connect. B-09: only traces whose
        embedded session id no longer appears in DISCOVER_SESSIONS are dropped —
        another server's (or another MCP process's) live trace is left alone.
        When the session list is unavailable nothing is dropped.
        """
        if self._server is None:
            return 0
        try:
            ours = [t for t in self._server.Traces if str(t.Name).startswith(prefix)]
        except Exception as exc:
            logger.debug("could not enumerate traces: %s", exc)
            return 0
        if not ours:
            return 0
        live = self._live_session_prefixes()
        if live is None:
            return 0
        dropped = 0
        for trace in ours:
            name = str(trace.Name)
            embedded = name[len(prefix) + 1 :].rsplit("_", 1)[0]
            if embedded and embedded in live:
                continue
            try:
                trace.Drop()
                dropped += 1
            except Exception as exc:
                logger.debug("could not drop stale trace %s: %s", trace.Name, exc)
        return dropped


# ---------------------------------------------------------------------------
# Event helpers (module level so tests can exercise them without a CLR)
# ---------------------------------------------------------------------------


#: Name -> id, because AMO hands back the enum name ("QueryEnd"), not the id.
_EVENT_IDS_BY_NAME = {
    "QueryBegin": 9,
    "QueryEnd": 10,
    "QuerySubcube": 11,
    "CommandBegin": 15,
    "DiscoverBegin": 36,
    "VertiPaqSEQueryBegin": 82,
    "VertiPaqSEQueryEnd": 83,
    "VertiPaqSEQueryCacheMatch": 85,
    "DirectQueryEnd": 99,
    "DAXQueryPlan": 112,
    "AggregateTableRewriteQuery": 131,
    "DAXEvaluationLog": 135,
    "ExecutionMetrics": 136,
}


def _event_id(event: Dict[str, Any]) -> Optional[int]:
    raw = event.get("event_class")
    if isinstance(raw, str):
        name = raw.strip()
        if name in _EVENT_IDS_BY_NAME:
            return _EVENT_IDS_BY_NAME[name]
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _is_internal_text(text: Any) -> bool:
    if not isinstance(text, str):
        return False
    return INTERNAL_QUERY_HEADER in text or text.lstrip().startswith(_PING_HEADER)


_WS_RE = re.compile(r"\s+")
_METRICS_ACTIVITY_RE = re.compile(r'"activityI[Dd]"\s*:\s*"([^"]+)"')


def _normalise_query_text(text: Any) -> str:
    return _WS_RE.sub(" ", str(text or "")).strip().lower()


def _metrics_activity(event: Dict[str, Any]) -> Optional[str]:
    """ActivityID of an ExecutionMetrics (136) event: column first, then its JSON."""
    if _event_id(event) != 136:
        return None
    if event.get("activity_id"):
        return str(event["activity_id"])
    match = _METRICS_ACTIVITY_RE.search(str(event.get("text_data") or ""))
    return match.group(1) if match else None


def _query_candidates(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Non-internal DAXQuery (or unlabelled) QueryBegin/QueryEnd events with an ActivityID."""
    out = []
    for event in events:
        if _event_id(event) not in (9, 10):
            continue
        if event.get("event_subclass") not in (None, "DAXQuery"):
            continue
        if _is_internal_text(event.get("text_data")):
            continue
        if event.get("activity_id"):
            out.append(event)
    return out


def _identify_our_activity(
    events: List[Dict[str, Any]],
    session_id: Optional[str] = None,
    query: Optional[str] = None,
) -> "tuple[Optional[str], str]":
    """``(activity_id, method)`` of OUR query's QueryBegin/QueryEnd (B-08).

    1. ``session``  — the QueryBegin/End whose SessionID is our connection's.
    2. ``text``     — the one whose (whitespace-normalised) text equals our query.
    3. ``single_candidate`` — exactly one distinct DAXQuery activity in the trace
       (nothing foreign to confuse it with).
    Otherwise ``(None, "ambiguous")`` — never "the first DAXQuery from anyone",
    which picked a Desktop visual query over ours.
    """
    candidates = _query_candidates(events)
    if not candidates:
        return None, "none"
    if session_id:
        for event in candidates:
            if str(event.get("session_id") or "") == str(session_id):
                return str(event["activity_id"]), "session"
    if query:
        wanted = _normalise_query_text(query)
        for event in candidates:
            if _normalise_query_text(event.get("text_data")) == wanted:
                return str(event["activity_id"]), "text"
    distinct = {str(e["activity_id"]) for e in candidates}
    if len(distinct) == 1:
        return distinct.pop(), "single_candidate"
    return None, "ambiguous"


def _filter_to_activity(
    events: List[Dict[str, Any]],
    activity_id: Optional[str],
    session_id: Optional[str] = None,
) -> "tuple[List[Dict[str, Any]], Dict[str, Any]]":
    """Keep only our activity's events (B-08). Returns ``(events, correlation)``.

    * Events carrying another ActivityID are dropped (``foreign_dropped``).
    * Events with NO ActivityID are kept when their SessionID is ours, or when
      the capture shows no foreign activity at all (uncontaminated trace — some
      builds may not stamp every SE event); otherwise they are dropped
      (``unattributed_dropped``) because they could belong to anyone.
    * ExecutionMetrics are matched by the activityID inside their JSON too.
    """
    info: Dict[str, Any] = {
        "activity_id": activity_id,
        "foreign_dropped": 0,
        "unattributed_dropped": 0,
    }
    if not activity_id:
        info["filtered"] = False
        return events, info

    def _ours_by_session(e: Dict[str, Any]) -> bool:
        # Live-verified 2026-09-23 (Desktop, CL 1702): VertiPaqSEQueryEnd (83)
        # events carry OUR SessionID but a DIFFERENT ActivityID than our
        # QueryBegin/End. Our ADOMD session is dedicated to this capture, so a
        # matching SessionID is the stronger signal — never drop those events.
        return bool(session_id) and str(e.get("session_id") or "") == str(session_id)

    foreign_present = any(
        not _ours_by_session(e)
        and (e.get("activity_id") or _metrics_activity(e) or activity_id) != activity_id
        for e in events
    )
    kept: List[Dict[str, Any]] = []
    for event in events:
        if _ours_by_session(event):
            kept.append(event)
            continue
        act = event.get("activity_id") or _metrics_activity(event)
        if act:
            if str(act) == activity_id:
                kept.append(event)
            else:
                info["foreign_dropped"] += 1
            continue
        if session_id and str(event.get("session_id") or "") == str(session_id):
            kept.append(event)
        elif not foreign_present:
            kept.append(event)
        else:
            info["unattributed_dropped"] += 1
    info["filtered"] = True
    info["contaminated"] = foreign_present
    return kept, info


def _our_activity_id(
    events: List[Dict[str, Any]],
    session_id: Optional[str] = None,
    query: Optional[str] = None,
) -> Optional[str]:
    """The ActivityID of our own QueryBegin/QueryEnd pair (see _identify_our_activity)."""
    return _identify_our_activity(events, session_id, query)[0]


def _split_plans(events: List[Dict[str, Any]]) -> tuple[str, str]:
    """Pull the logical and physical plan text out of DAXQueryPlan events.

    Subclass 1 is the VertiPaq logical plan and 2 the physical one; the
    DirectQuery subclasses (3, 4) are ignored, as in DAX Studio.
    """
    logical: List[str] = []
    physical: List[str] = []
    for event in events:
        if _event_id(event) != 112:
            continue
        text = event.get("text_data") or ""
        if not text:
            continue
        subclass = str(event.get("event_subclass", "")).strip().lower()
        if subclass in ("1", "daxvertipaqlogicalplan") or "logical" in subclass:
            logical.append(text)
        elif subclass in ("2", "daxvertipaqphysicalplan") or "physical" in subclass:
            physical.append(text)
    return "\n\n".join(logical), "\n\n".join(physical)


def _eval_log_rows(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """DAXEvaluationLog payloads, parsed where they are valid JSON."""
    import json

    rows: List[Dict[str, Any]] = []
    for event in events:
        if _event_id(event) != _EVAL_LOG_EVENT:
            continue
        text = event.get("text_data") or ""
        row: Dict[str, Any] = {"raw": text, "subclass": event.get("event_subclass")}
        try:
            parsed = json.loads(text) if text else None
            if isinstance(parsed, dict):
                row["parsed"] = parsed
        except (ValueError, TypeError):
            pass
        rows.append(row)
    return rows


def is_available() -> bool:
    """True when an in-process trace can be attempted at all."""
    if not _clr_available():
        return False
    try:
        from core.infrastructure.dll_paths import get_dll_paths
        import os

        paths = get_dll_paths()
        return os.path.exists(paths.tabular_dll) and os.path.exists(paths.adomd_dll)
    except Exception:
        return False
