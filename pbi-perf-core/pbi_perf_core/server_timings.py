"""DAX Studio ``ServerTimesModel.ProcessResults`` parity.

The numbers DAX Studio shows in its Server Timings pane are not simple sums of
the trace events.  Four rules make the difference, and every one was derived
from a real DAX Studio export rather than from documentation - see
``tests/test_server_timings.py``, which reproduces that export's headline
numbers from its own event rows:

1. ``VertiPaqScanInternal`` rows are bookkeeping.  They restate the CPU of the
   scan they accompany, so they contribute neither duration nor CPU.
2. ``BatchVertiPaqScan`` rows span all their member scans on the clock, but the
   engine already reports only the batch's own overhead in ``Duration`` - so
   that value is used as-is and the batch's window is excluded from the
   formula-engine sweep, or the members' time would be counted twice.
3. Concurrent scans share wall clock, so an overlapping scan contributes only
   the part not already covered by an earlier one.
4. ``StorageEngineDuration`` is a *reconciliation*, not an accumulation: the
   formula engine gets whatever is left of the query once the storage engine
   intervals are removed, and the storage engine total is derived back from it.

Naively summing the event durations on the reference capture yields 81 ms and
64 ms CPU where DAX Studio reports 100 ms and 32 ms.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from ._util import ms_between, to_datetime, to_int

__all__ = [
    "TraceEvent",
    "ServerTimingsResult",
    "SEEventRow",
    "process_results",
    "EVENT_CLASS_NAMES",
    "CALLBACK_KEYWORDS",
]

# ---------------------------------------------------------------------------
# Event vocabulary
# ---------------------------------------------------------------------------

EVENT_CLASS_NAMES = {
    9: "QueryBegin",
    10: "QueryEnd",
    11: "QuerySubcube",
    15: "CommandBegin",
    36: "DiscoverBegin",
    82: "VertiPaqSEQueryBegin",
    83: "VertiPaqSEQueryEnd",
    85: "VertiPaqSEQueryCacheMatch",
    99: "DirectQueryEnd",
    112: "DAXQueryPlan",
    131: "AggregateTableRewriteQuery",
    135: "DAXEvaluationLog",
    136: "ExecutionMetrics",
}

#: Keywords DAX Studio highlights - a callback means the storage engine had to
#: call back into the (single-threaded) formula engine for every row.
CALLBACK_KEYWORDS = (
    "CallbackDataID",
    "LogAbsValueCallback",
    "RoundValueCallback",
    "EncodeCallback",
    "MinMaxColumnPositionCallback",
    "Cond",
)

#: ``[Estimated size (volume, marshalling bytes): 24, 192]`` - the spacing around
#: the colon varies between engine builds, so both forms are accepted.
_ESTIMATED_SIZE_RE = re.compile(
    r"[\'\[]Estimated size[^:\]]*:\s*(?P<rows>\d+),\s*(?P<bytes>\d+)\s*[\'\]]"
)

_INTERNAL_SUBCLASSES = {"VertiPaqScanInternal"}
_BATCH_SUBCLASS = "BatchVertiPaqScan"
_SCAN_SUBCLASS = "VertiPaqScan"


@dataclass
class TraceEvent:
    """One raw trace event, normalised out of AMO or a JSON export."""

    event_class: int | str
    event_subclass: str | None = None
    text_data: str = ""
    start_time: _dt.datetime | None = None
    end_time: _dt.datetime | None = None
    current_time: _dt.datetime | None = None
    duration_ms: int = 0
    cpu_time_ms: int = 0
    session_id: str | None = None
    activity_id: str | None = None
    request_id: str | None = None
    object_name: str | None = None
    application_name: str | None = None
    request_parameters: str | None = None
    integer_data: int = 0

    @property
    def class_name(self) -> str:
        if isinstance(self.event_class, str):
            return self.event_class
        return EVENT_CLASS_NAMES.get(int(self.event_class), str(self.event_class))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TraceEvent":
        def pick(*names, default=None):
            for name in names:
                if name in data and data[name] is not None:
                    return data[name]
            return default

        return cls(
            event_class=pick("event_class", "EventClass", "Class", default=""),
            event_subclass=pick("event_subclass", "EventSubclass", "Subclass"),
            text_data=pick("text_data", "TextData", default="") or "",
            start_time=to_datetime(pick("start_time", "StartTime")),
            end_time=to_datetime(pick("end_time", "EndTime")),
            current_time=to_datetime(pick("current_time", "CurrentTime")),
            duration_ms=to_int(pick("duration_ms", "Duration")),
            cpu_time_ms=to_int(pick("cpu_time_ms", "CpuTime")),
            session_id=pick("session_id", "SessionID"),
            activity_id=pick("activity_id", "ActivityID"),
            request_id=pick("request_id", "RequestID"),
            object_name=pick("object_name", "ObjectName"),
            application_name=pick("application_name", "ApplicationName"),
            request_parameters=pick("request_parameters", "RequestParameters"),
            integer_data=to_int(pick("integer_data", "IntegerData")),
        )


@dataclass
class SEEventRow:
    """A storage-engine row exactly as DAX Studio renders and exports it."""

    class_name: str
    subclass: str | None
    query: str = ""
    text_data: str = ""
    row_number: int = 0
    duration: int = 0
    net_parallel_duration: int = 0
    cpu_time: int = 0
    cpu_factor: float | None = None
    estimated_rows: int | None = None
    estimated_kbytes: int | None = None
    start_time: _dt.datetime | None = None
    end_time: _dt.datetime | None = None
    start_offset_ms: int = 0
    total_query_duration: int = 0
    highlight_query: bool = False
    is_internal_event: bool = False
    internal_batch_event: bool = False
    is_batch_event: bool = False
    object_name: str | None = None
    #: Duration/CPU exactly as the engine reported them (no batch-child
    #: subtraction happens; kept for export fidelity).
    raw_duration: int = 0
    raw_cpu_time: int = 0

    def to_export_dict(self) -> dict[str, Any]:
        """Serialise the way DAX Studio does - default values are omitted."""
        out: dict[str, Any] = {"Class": self.class_name}
        if self.subclass:
            out["Subclass"] = self.subclass
        out["Query"] = self.query
        out["TextData"] = self.text_data
        if self.class_name != "VertiPaqSEQueryCacheMatch":
            out["Duration"] = self.duration
            out["NetParallelDuration"] = self.net_parallel_duration
            out["CpuTime"] = self.cpu_time
        out["RowNumber"] = self.row_number
        if self.cpu_factor is not None:
            out["CpuFactor"] = self.cpu_factor
        if self.estimated_rows is not None:
            out["EstimatedRows"] = self.estimated_rows
        if self.estimated_kbytes is not None:
            out["EstimatedKBytes"] = self.estimated_kbytes
        if self.highlight_query:
            out["HighlightQuery"] = True
        if self.is_internal_event:
            out["IsInternalEvent"] = True
        if self.internal_batch_event:
            out["InternalBatchEvent"] = True
        if self.is_batch_event:
            out["IsBatchEvent"] = True
        if self.start_time is not None:
            out["StartTime"] = self.start_time.isoformat()
        if self.end_time is not None and self.class_name not in (
            "VertiPaqSEQueryCacheMatch",
            "ExecutionMetrics",
        ):
            out["EndTime"] = self.end_time.isoformat()
        out["StartOffsetMs"] = self.start_offset_ms
        out["TotalQueryDuration"] = self.total_query_duration
        return out


@dataclass
class ServerTimingsResult:
    """Everything the Server Timings pane shows, plus our own extras."""

    total_duration: int = 0
    total_cpu_duration: int = 0
    formula_engine_duration: int = 0
    storage_engine_duration: int = 0
    storage_engine_net_parallel_duration: int = 0
    storage_engine_cpu: int = 0
    storage_engine_query_count: int = 0
    vertipaq_cache_matches: int = 0
    total_direct_query_duration: int = 0
    parallel_storage_engine_events_detected: bool = False
    timeline_total_duration: int = 0
    activity_id: str | None = None
    request_id: str | None = None
    command_text: str = ""
    parameters: str | None = None
    query_start: _dt.datetime | None = None
    query_end: _dt.datetime | None = None
    events: list[SEEventRow] = field(default_factory=list)
    execution_metrics: dict[str, Any] = field(default_factory=dict)
    aggregate_rewrites: list[dict[str, Any]] = field(default_factory=list)

    # -- derived ----------------------------------------------------------
    @property
    def storage_engine_cpu_factor(self) -> float:
        if not self.storage_engine_duration:
            return 0.0
        return round(self.storage_engine_cpu / self.storage_engine_duration, 2)

    @property
    def se_pct(self) -> float:
        if not self.total_duration:
            return 0.0
        return round(100.0 * self.storage_engine_duration / self.total_duration, 1)

    @property
    def fe_pct(self) -> float:
        if not self.total_duration:
            return 0.0
        return round(100.0 * self.formula_engine_duration / self.total_duration, 1)

    @property
    def cache_pct(self) -> float:
        if not self.storage_engine_query_count:
            return 0.0
        return round(100.0 * self.vertipaq_cache_matches / self.storage_engine_query_count, 1)

    @property
    def callback_count(self) -> int:
        return sum(1 for e in self.events if e.highlight_query)

    def headline(self) -> dict[str, Any]:
        """The compact summary an MCP tool returns instead of 370 rows."""
        return {
            "total_ms": self.total_duration,
            "fe_ms": self.formula_engine_duration,
            "se_ms": self.storage_engine_duration,
            "se_net_parallel_ms": self.storage_engine_net_parallel_duration,
            "se_cpu_ms": self.storage_engine_cpu,
            "total_cpu_ms": self.total_cpu_duration,
            "se_cpu_factor": self.storage_engine_cpu_factor,
            "se_queries": self.storage_engine_query_count,
            "se_cache_hits": self.vertipaq_cache_matches,
            "cache_pct": self.cache_pct,
            "fe_pct": self.fe_pct,
            "se_pct": self.se_pct,
            "dq_ms": self.total_direct_query_duration,
            "callback_scans": self.callback_count,
            "parallel_se_detected": self.parallel_storage_engine_events_detected,
            "event_rows": len(self.events),
        }


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------


def _normalise_times(ev: TraceEvent) -> None:
    """DAX Studio's pre-pass: make StartTime/EndTime/Duration self-consistent."""
    if ev.start_time is None:
        return
    if ev.end_time is not None and ev.end_time < ev.start_time:
        # A skewed row (EndTime before StartTime) would open an inverted interval
        # in the formula-engine sweep; trust the reported Duration instead (F-30).
        ev.end_time = ev.start_time + _dt.timedelta(milliseconds=max(ev.duration_ms, 0))
    if ev.end_time is not None:
        if ev.end_time == ev.start_time and ev.duration_ms > 0:
            ev.end_time = ev.start_time + _dt.timedelta(milliseconds=ev.duration_ms)
        elif ev.event_subclass != _BATCH_SUBCLASS:
            # A batch row's window spans all of its member scans while its
            # Duration reports only the batch's own overhead - which the engine
            # has already netted off.  Widening it to the span would re-add the
            # children's time that the Duration deliberately excludes.
            span = ms_between(ev.start_time, ev.end_time)
            if span > ev.duration_ms:
                ev.duration_ms = int(round(span))
    elif ev.duration_ms > 0:
        ev.end_time = ev.start_time + _dt.timedelta(milliseconds=ev.duration_ms)


def _parse_estimated_size(text: str) -> tuple[int | None, int | None, str]:
    """Extract the estimated rows/bytes and rewrite them human-readably."""
    match = _ESTIMATED_SIZE_RE.search(text or "")
    if not match:
        return None, None, text
    rows = int(match.group("rows"))
    raw_bytes = int(match.group("bytes"))
    kbytes = 1 + raw_bytes // 1024
    replacement = f"Estimated size: rows = {rows:,}  bytes = {raw_bytes:,}"
    return rows, kbytes, _ESTIMATED_SIZE_RE.sub(replacement, text)


def _is_callback(text: str) -> bool:
    return any(keyword in (text or "") for keyword in CALLBACK_KEYWORDS)


def _compute_formula_engine_ms(
    query_start: _dt.datetime,
    query_end: _dt.datetime,
    intervals: Sequence[tuple[_dt.datetime, _dt.datetime]],
) -> float:
    """Sweep line: the time inside the query where **no** storage engine ran.

    Intervals are clamped to ``[query_start, query_end]`` (F-30): a scan outside
    the query window (another query's event that survived correlation) must not
    move the cursor past the query's end and inflate the formula engine.
    """
    clamped: list[tuple[_dt.datetime, _dt.datetime]] = []
    for start, end in intervals:
        if end < start or start >= query_end or end <= query_start:
            continue
        clamped.append((max(start, query_start), min(end, query_end)))
    if not clamped:
        return ms_between(query_start, query_end)
    markers: list[tuple[_dt.datetime, int]] = []
    for start, end in clamped:
        markers.append((start, 1))
        markers.append((end, -1))
    # Opening events sort before closing ones at the same instant so two
    # back-to-back scans do not register a zero-length formula-engine gap.
    markers.sort(key=lambda m: (m[0], -m[1]))

    fe_ms = 0.0
    level = 0
    cursor = query_start
    for moment, delta in markers:
        if level == 0 and moment > cursor:
            fe_ms += ms_between(cursor, moment)
        level += delta
        if level == 0:
            cursor = max(cursor, moment)
        elif moment > cursor:
            cursor = max(cursor, moment)
    if level == 0 and query_end > cursor:
        fe_ms += ms_between(cursor, query_end)
    return max(fe_ms, 0.0)


def process_results(
    events: Iterable[TraceEvent | dict[str, Any]],
    *,
    command_text: str | None = None,
    xmsql_cleaner=None,
) -> ServerTimingsResult:
    """Aggregate raw trace events the way DAX Studio's Server Timings pane does.

    ``xmsql_cleaner`` is an optional ``callable(text) -> str`` used to build the
    friendly ``Query`` column; :mod:`pbi_perf_core.xmsql` supplies one.
    """
    normalised: list[TraceEvent] = []
    for raw in events:
        ev = raw if isinstance(raw, TraceEvent) else TraceEvent.from_dict(raw)
        _normalise_times(ev)
        normalised.append(ev)

    result = ServerTimingsResult()
    query_end = _pick_query_end(normalised)
    query_begin = _pick_query_begin(normalised, query_end)

    if query_end is not None:
        result.total_duration = query_end.duration_ms
        result.total_cpu_duration = query_end.cpu_time_ms
        result.activity_id = query_end.activity_id
        result.request_id = query_end.request_id
        result.query_start = query_end.start_time
        result.query_end = query_end.end_time
        result.command_text = query_end.text_data or ""
    if query_begin is not None:
        result.command_text = query_begin.text_data or result.command_text
        result.parameters = query_begin.request_parameters
        if result.query_start is None:
            result.query_start = query_begin.start_time
    if command_text:
        result.command_text = command_text

    # Storage-engine rows, in arrival order - the order batch nesting relies on.
    se_events = [
        ev
        for ev in normalised
        if ev.class_name
        in (
            "VertiPaqSEQueryEnd",
            "VertiPaqSEQueryCacheMatch",
            "DirectQueryEnd",
            "ExecutionMetrics",
            "AggregateTableRewriteQuery",
        )
    ]

    last_end: _dt.datetime | None = None
    rows: list[SEEventRow] = []
    row_number = 0

    # Batch membership is always attributed POSITIONALLY after the loop
    # (``_reattribute_batches``), with or without VertiPaqSEQueryBegin rows (F-29):
    # Begin events are not in ``se_events``, so a Begin-driven counter was never
    # opened and a live capture with Begin rows attributed no member at all.
    for ev in se_events:
        name = ev.class_name
        subclass = ev.event_subclass

        if (
            name != "ExecutionMetrics"
            and result.query_end is not None
            and ev.start_time is not None
            and ev.start_time > result.query_end
        ):
            # Started after our query ended: another query's event (F-30).
            continue

        if name == "AggregateTableRewriteQuery":
            # Keep the subclass: it says whether the rewrite MATCHED (F-24).
            result.aggregate_rewrites.append(
                {"text": ev.text_data, "start": ev.start_time, "subclass": subclass}
            )
            continue

        if name == "ExecutionMetrics":
            row_number += 1
            rows.append(
                SEEventRow(
                    class_name="ExecutionMetrics",
                    subclass=None,
                    query=ev.text_data,
                    text_data=ev.text_data,
                    row_number=row_number,
                    start_time=ev.start_time or ev.current_time,
                    total_query_duration=0,
                )
            )
            continue

        if name == "VertiPaqSEQueryCacheMatch":
            result.vertipaq_cache_matches += 1
            row_number += 1
            clean = xmsql_cleaner(ev.text_data) if xmsql_cleaner else ev.text_data
            rows.append(
                SEEventRow(
                    class_name=name,
                    subclass=subclass or "VertiPaqCacheExactMatch",
                    query=clean,
                    text_data=ev.text_data,
                    row_number=row_number,
                    start_time=ev.start_time or ev.current_time,
                    highlight_query=_is_callback(ev.text_data),
                    object_name=ev.object_name,
                )
            )
            continue

        # --- VertiPaqSEQueryEnd / DirectQueryEnd --------------------------
        duration = ev.duration_ms
        cpu = ev.cpu_time_ms
        net_parallel = duration
        is_internal = subclass in _INTERNAL_SUBCLASSES
        is_batch = subclass == _BATCH_SUBCLASS
        internal_batch = False

        if name == "DirectQueryEnd":
            cpu = duration
            result.total_direct_query_duration += duration

        if not is_batch and not is_internal:
            # Concurrent scans share wall clock, so the part already covered by
            # an earlier scan is not counted twice.  Only a substantial overlap
            # means deliberate parallelism - SSAS timestamps are rounded to
            # 1/300 s, so a couple of milliseconds is quantisation noise.
            if last_end is not None and ev.start_time is not None and ev.start_time < last_end:
                overlap = ms_between(ev.start_time, min(last_end, ev.end_time or last_end))
                if overlap > 10:
                    result.parallel_storage_engine_events_detected = True
                net_parallel = max(int(duration - overlap), 0)

        # Only real leaf scans occupy the storage engine, so only they move the
        # overlap watermark: internal markers and batch envelopes would
        # otherwise mask genuine concurrency between scans.
        if (
            not is_internal
            and not is_batch
            and ev.end_time is not None
            and (last_end is None or ev.end_time > last_end)
        ):
            last_end = ev.end_time

        est_rows, est_kb, friendly_source = _parse_estimated_size(ev.text_data)
        clean = xmsql_cleaner(friendly_source) if xmsql_cleaner else friendly_source
        cpu_factor = None
        if duration > 0 and cpu > 0:
            factor = round(cpu / duration, 2)
            if factor >= 2:
                cpu_factor = factor

        row_number += 1
        rows.append(
            SEEventRow(
                class_name=name,
                subclass=subclass,
                query=clean,
                text_data=ev.text_data,
                row_number=row_number,
                duration=duration,
                net_parallel_duration=net_parallel,
                cpu_time=cpu,
                cpu_factor=cpu_factor,
                estimated_rows=est_rows,
                estimated_kbytes=est_kb,
                start_time=ev.start_time,
                end_time=ev.end_time,
                highlight_query=_is_callback(ev.text_data),
                is_internal_event=is_internal,
                internal_batch_event=internal_batch,
                is_batch_event=is_batch,
                object_name=ev.object_name,
                raw_duration=ev.duration_ms,
                raw_cpu_time=ev.cpu_time_ms,
            )
        )

    # Infer batch membership from position (see _reattribute_batches).
    if _reattribute_batches(rows):
        # These rows were treated as independent scans while the loop ran, so
        # any overlap reduction applied to them is wrong: a batch member runs
        # inside the batch window, not concurrently with what preceded it.
        for row in rows:
            if row.internal_batch_event:
                row.net_parallel_duration = row.duration
    _recompute_totals(result, rows)

    result.events = rows

    # -- formula engine (sweep line) and reconciliation -------------------
    if result.query_start and result.query_end:
        # The batch row's window spans all of its children, so counting both
        # would cover the same wall clock twice.  The leaf scans are what
        # actually occupied the storage engine.
        intervals = [
            (r.start_time, r.end_time)
            for r in rows
            if r.start_time and r.end_time and not r.is_internal_event and not r.is_batch_event
        ]
        fe = _compute_formula_engine_ms(result.query_start, result.query_end, intervals)
        result.formula_engine_duration = int(round(fe))

    total = max(result.total_duration, result.formula_engine_duration)
    result.total_duration = total
    se_net = result.storage_engine_net_parallel_duration
    if se_net + result.formula_engine_duration < total:
        result.storage_engine_duration = se_net
        result.formula_engine_duration = total - se_net
    else:
        result.storage_engine_duration = max(total - result.formula_engine_duration, 0)

    # -- timeline ---------------------------------------------------------
    if result.query_start:
        latest = 0
        for row in rows:
            if row.start_time is None:
                continue
            row.start_offset_ms = int(round(ms_between(result.query_start, row.start_time)))
            row.total_query_duration = (
                0 if row.class_name == "ExecutionMetrics" else result.total_duration
            )
            latest = max(latest, row.start_offset_ms + max(row.duration, 1))
        result.timeline_total_duration = max(latest, result.total_duration)

    # -- execution metrics -------------------------------------------------
    from .execution_metrics import parse_execution_metrics

    # Several ExecutionMetrics can survive the ActivityID filter (a priming
    # query's arrives first when neither carries an ActivityID): take the one
    # whose durationMs is closest to the query's total (F-30).
    candidates: list[dict[str, Any]] = []
    for ev in normalised:
        if ev.class_name != "ExecutionMetrics":
            continue
        if result.activity_id and ev.activity_id and ev.activity_id != result.activity_id:
            continue
        parsed = parse_execution_metrics(ev.text_data)
        if parsed:
            candidates.append(parsed)
    if candidates:

        def _distance(metrics: dict[str, Any]) -> float:
            duration = metrics.get("durationMs")
            if isinstance(duration, bool) or not isinstance(duration, (int, float)):
                return float("inf")
            return abs(float(duration) - float(result.total_duration))

        result.execution_metrics = min(candidates, key=_distance)

    return result


def _pick_query_end(events: Sequence[TraceEvent]) -> TraceEvent | None:
    """The QueryEnd of THE query: DAXQuery subclass preferred, then the longest (F-30).

    A heartbeat / DMX / priming QueryEnd that arrives first used to become the
    query's total.
    """
    ends = [ev for ev in events if ev.class_name == "QueryEnd"]
    if not ends:
        return None
    dax = [ev for ev in ends if (ev.event_subclass or "") == "DAXQuery"]
    pool = dax or ends
    return max(pool, key=lambda ev: ev.duration_ms)


def _pick_query_begin(
    events: Sequence[TraceEvent], query_end: TraceEvent | None
) -> TraceEvent | None:
    """The QueryBegin matching *query_end* (same ActivityID / RequestID), else the first."""
    begins = [ev for ev in events if ev.class_name == "QueryBegin"]
    if not begins:
        return None
    if query_end is not None:
        for key in ("activity_id", "request_id"):
            wanted = getattr(query_end, key)
            if wanted:
                for ev in begins:
                    if getattr(ev, key) == wanted:
                        return ev
        dax = [ev for ev in begins if (ev.event_subclass or "") == "DAXQuery"]
        if dax:
            return dax[0]
    return begins[0]


def _reattribute_batches(rows: list[SEEventRow]) -> bool:
    """Attribute scans to the batch they ran inside. True when any changed.

    Used when the capture has no ``VertiPaqSEQueryBegin/BatchVertiPaqScan``
    events to open the window - notably when replaying an exported capture.

    The engine emits a batch's child scans *before* the batch row that reports
    them, and they share the batch's start instant (SSAS timestamps are rounded
    to 1/300 s, so containment tests are unreliable).  Position is therefore the
    dependable signal: the run of scans immediately preceding a batch row, back
    to the previous batch, are its children.
    """
    if not any(r.is_batch_event for r in rows):
        return False

    # The engine emits a batch's member scans immediately before the batch row
    # that reports them, each one followed by a VertiPaqScanInternal marker:
    #
    #     VertiPaqScan  <- child      VertiPaqScan          <- not a child
    #     VertiPaqScanInternal        BatchVertiPaqScan
    #     VertiPaqScan  <- child
    #     VertiPaqScanInternal
    #     VertiPaqScan  <- child
    #     BatchVertiPaqScan
    #
    # So a child is a scan whose *next* storage-engine row is an internal
    # marker, walking back from the batch row until that pattern breaks.
    # Cache matches carry no timing and are skipped entirely.
    material = [
        row
        for row in rows
        if row.class_name not in ("VertiPaqSEQueryCacheMatch", "ExecutionMetrics")
    ]

    changed = False
    for index, row in enumerate(material):
        if not row.is_batch_event:
            continue
        cursor = index - 1
        while cursor >= 0:
            candidate = material[cursor]
            if candidate.is_internal_event:
                cursor -= 1
                continue
            if candidate.subclass != _SCAN_SUBCLASS or candidate.internal_batch_event:
                break
            follower = material[cursor + 1] if cursor + 1 < len(material) else None
            member = follower is not None and (follower.is_internal_event or follower is row)
            if not member:
                break
            candidate.internal_batch_event = True
            changed = True
            cursor -= 1
    return changed


def _recompute_totals(result: ServerTimingsResult, rows: list[SEEventRow]) -> None:
    """Recompute the SE aggregates after batch re-attribution.

    The engine already reports a batch row's OWN overhead in its Duration, so
    the value is used as-is (nothing is subtracted).  Every scan (batch
    members included) and every batch row counts once towards duration,
    net-parallel time, CPU and the query count; ``VertiPaqScanInternal`` rows
    contribute nothing - they restate the CPU of the scan they accompany.
    """
    result.storage_engine_duration = 0
    result.storage_engine_net_parallel_duration = 0
    result.storage_engine_cpu = 0
    result.storage_engine_query_count = 0

    for row in rows:
        if row.class_name in ("VertiPaqSEQueryCacheMatch", "ExecutionMetrics"):
            continue

        if row.is_internal_event:
            # An internal marker restates the CPU of the scan it accompanies;
            # counting it again would double the storage-engine CPU.
            continue

        if row.is_batch_event:
            # The engine already reports the batch's own overhead only, so the
            # value is used as-is; subtracting the children again would
            # double-count them.
            row.net_parallel_duration = row.duration

        result.storage_engine_duration += row.duration
        result.storage_engine_net_parallel_duration += row.net_parallel_duration
        result.storage_engine_cpu += row.cpu_time
        result.storage_engine_query_count += 1
