"""
Query Trace Runner for SE/FE timing analysis.

Primary lane is an in-process pythonnet AMO trace
(:mod:`core.infrastructure.trace_session`), which is the only lane that returns
query plans: the DaxExecutor.exe fallback was built before its plan-capture code
existed and cannot be rebuilt without a .NET SDK. The exe is kept for machines
where ``import clr`` fails.

Both lanes return the same key set, so every caller (04_Run_DAX, 09_Debug,
13_Query_Plan) is agnostic to which one ran.
"""

import json
import re
import subprocess
import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


def _trace_session_opts():
    """Local Desktop trace, or remote + access token on the read-only XMLA target (XM-01)."""
    try:
        from core.infrastructure import xmla_target

        return xmla_target.trace_options()
    except Exception as e:  # noqa: BLE001 - fall back to the local lane
        logger.debug("xmla trace options unavailable: %s", e)
        return {"local": True}


# Every callback form the SE can emit into xmSQL. A callback means the SE
# handed that row back to the single-threaded FE, so the scan is effectively
# serialised AND its result is never cached.
_CALLBACK_RE = re.compile(
    r"(CallbackDataID|EncodeCallback|LogAbsValueCallback|RoundValueCallback|"
    r"MinMaxColumnPositionCallback|Cond)\s*\(",
    re.IGNORECASE,
)

# Subprocess timeout: 5 min query + 1 min margin for trace setup/teardown
_SUBPROCESS_TIMEOUT_S = 360

#: Rows that appear in the timeline but are not storage-engine scans.
_NON_SCAN_CLASSES = frozenset({"VertiPaqSEQueryCacheMatch", "ExecutionMetrics"})


def _stopped_result() -> dict:
    """Placeholder for a batch query skipped by should_stop (F-14)."""
    return {
        "_error": "not run: the caller's time budget ran out or the call was cancelled",
        "_error_type": "cancelled",
    }


class NativeTraceRunner:
    """Executes a traced DAX query and returns DAX Studio-parity metrics.

    In-process AMO trace first (plans + ExecutionMetrics + all result sets),
    DaxExecutor.exe second (timings only, no plans).
    """

    def __init__(self, connection_string: str):
        self._connection_string = connection_string
        self._exe_path: Optional[str] = None
        if not self.in_process_available():
            # Only the fallback lane needs the exe, and only then is its absence
            # fatal — raising here when the in-process lane works would break
            # every caller on a machine that never had the exe built.
            self._exe_path = self._find_exe()

    @staticmethod
    def is_available() -> bool:
        """True when either lane can run a trace."""
        from core.infrastructure.dll_paths import get_trace_runner_path

        return NativeTraceRunner.in_process_available() or get_trace_runner_path() is not None

    @staticmethod
    def in_process_available() -> bool:
        """True when the pythonnet AMO lane can be used."""
        try:
            from core.infrastructure import trace_session

            return trace_session.is_available()
        except Exception:
            return False

    def _find_exe(self) -> str:
        from core.infrastructure.dll_paths import get_trace_runner_path

        path = get_trace_runner_path()
        if path is None:
            raise FileNotFoundError(
                "No trace lane available: pythonnet/AMO could not be loaded and "
                "DaxExecutor.exe is not built. Run: cd core/infrastructure/dax_executor "
                "&& dotnet build -c Release"
            )
        return path

    def execute_with_trace(
        self,
        query: str,
        clear_cache: bool = True,
        *,
        capture_plan: bool = True,
        capture_eval_log: bool = False,
        settle_ms: int = 6000,
        timeout_s: int = 300,
        keep_rows: int = 0,
    ) -> dict:
        """Execute DAX under a trace. Returns the timing/plan metrics dict.

        ``keep_rows`` > 0 keeps the first N rows of every result set (in-process
        lane only) so a caller gets data and timings from the same run (L-19).
        """
        if self.in_process_available():
            result = self._execute_in_process(
                query,
                clear_cache,
                capture_plan=capture_plan,
                capture_eval_log=capture_eval_log,
                settle_ms=settle_ms,
                timeout_s=timeout_s,
                keep_rows=keep_rows,
            )
            # A CLR-level failure is not a query failure: fall through to the
            # exe rather than reporting a broken trace as a broken query.
            if result is not None:
                return result
        return self._execute_via_exe(query, clear_cache)

    def execute_batch_with_trace(
        self,
        queries: List[str],
        clear_cache: bool = True,
        *,
        capture_plan: bool = False,
        settle_ms: int = 6000,
        timeout_s: int = 300,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> List[dict]:
        """Trace several queries under ONE trace session, in order.

        ``should_stop()`` is polled before every query (F-14): once it returns
        True -- the caller's time budget ran out or its MCP call was cancelled --
        the remaining queries are NOT run (no more ClearCache + queries in the
        background after a timeout) and come back as
        ``{"_error": ..., "_error_type": "cancelled"}``.

        Page profiling runs dozens of queries. Opening a trace per query pays the
        AMO connect + DMV capability discovery + xmSQL name-map build every time,
        so a page of 20 visuals spends most of its wall clock on setup rather than
        on the queries. ``TraceSession.run`` already drains the event buffer before
        each query and correlates the rest by our own ``ActivityID``, so successive
        runs on one open session stay cleanly separated.

        Falls back to one exe call per query when the in-process lane is unusable;
        the exe has no reusable session, so nothing is gained there.
        """
        if not queries:
            return []
        if self.in_process_available():
            batched = self._execute_batch_in_process(
                queries,
                clear_cache,
                capture_plan=capture_plan,
                settle_ms=settle_ms,
                timeout_s=timeout_s,
                should_stop=should_stop,
            )
            if batched is not None:
                return batched
        out: List[dict] = []
        for q in queries:
            if should_stop is not None and should_stop():
                out.append(_stopped_result())
                continue
            out.append(self._execute_via_exe(q, clear_cache))
        return out

    def _execute_batch_in_process(
        self,
        queries: List[str],
        clear_cache: bool,
        *,
        capture_plan: bool,
        settle_ms: int,
        timeout_s: int,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> Optional[List[dict]]:
        """Run every query on one open TraceSession. None when the lane is unusable."""
        from core.infrastructure.trace_session import TraceBusy, TraceSession, TraceUnavailable

        session = TraceSession(self._connection_string, **_trace_session_opts())
        try:
            with session:
                mapped: List[dict] = []
                for query in queries:
                    if should_stop is not None and should_stop():
                        mapped.append(_stopped_result())
                        continue
                    try:
                        result = session.run(
                            query,
                            clear_cache=clear_cache,
                            capture_plan=capture_plan,
                            settle_ms=settle_ms,
                            timeout_s=timeout_s,
                        )
                    except Exception as e:
                        # One bad visual query must not abort the whole page.
                        logger.warning("traced batch query failed: %s", e)
                        mapped.append({"_error": str(e)})
                        continue
                    if result.error and not result.result_sets:
                        mapped.append({"_error": result.error})
                    elif self._no_events(result):
                        mapped.append(self._no_events_error(result))
                    else:
                        mapped.append(self._map_trace_result(result))
                return mapped
        except TraceBusy as e:
            return [{"_error": str(e), "_error_type": "busy"} for _ in queries]
        except TraceUnavailable as e:
            logger.info("In-process trace unavailable (%s); falling back to exe", e)
            return None
        except Exception as e:
            logger.warning("In-process batch trace failed: %s", e)
            return None

    def _execute_in_process(
        self,
        query: str,
        clear_cache: bool,
        *,
        capture_plan: bool,
        capture_eval_log: bool,
        settle_ms: int,
        timeout_s: int,
        keep_rows: int = 0,
    ) -> Optional[dict]:
        """Run the trace in-process. Returns None when the lane is unusable."""
        from core.infrastructure.trace_session import TraceBusy, TraceSession, TraceUnavailable

        # F-10: the eval-log subscription is fixed when the trace starts (on
        # __enter__), so it must be requested at construction.
        session = TraceSession(
            self._connection_string, capture_eval_log=capture_eval_log, **_trace_session_opts()
        )
        try:
            with session:
                result = session.run(
                    query,
                    clear_cache=clear_cache,
                    capture_plan=capture_plan,
                    capture_eval_log=capture_eval_log,
                    settle_ms=settle_ms,
                    timeout_s=timeout_s,
                    keep_rows=keep_rows,
                )
        except TraceBusy as e:
            # Another trace owns the (database-wide) trace lane; running the exe
            # concurrently would contaminate both captures.
            return {"_error": str(e), "_error_type": "busy"}
        except TraceUnavailable as e:
            logger.info("In-process trace unavailable (%s); falling back to exe", e)
            return None
        except Exception as e:
            logger.warning("In-process trace failed: %s", e)
            return None

        if result.error and not result.result_sets:
            return {"_error": result.error}
        if self._no_events(result):
            return self._no_events_error(result)
        return self._map_trace_result(result)

    @staticmethod
    def _no_events(result) -> bool:
        """B-10: the trace delivered nothing at all (not even a heartbeat)."""
        received = max(
            int(getattr(result, "events_received", 0) or 0),
            len(getattr(result, "events", []) or []),
        )
        return received == 0 and not getattr(result, "trace_started", False)

    @staticmethod
    def _amo_info() -> dict:
        try:
            from core.infrastructure.dll_paths import get_amo_info

            return get_amo_info()
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    @classmethod
    def _no_events_error(cls, result) -> dict:
        """``trace_unavailable`` instead of success-with-zeros (B-10)."""
        amo = cls._amo_info()
        return {
            "_error": (
                "Trace delivered no events (not even the heartbeat) — timings would be "
                f"meaningless zeros. Loaded AMO build: {amo.get('selected_version')} from "
                f"{amo.get('selected_folder')}. If this persists, pin a trace-capable build "
                "with MCP_PBI_AMO_DIR (e.g. %LOCALAPPDATA%\\Programs\\DaxStudio\\bin or "
                "C:\\Program Files\\DAX Studio\\bin) and restart."
            ),
            "_error_type": "trace_unavailable",
            "reason": "trace_no_events",
            "amo": amo,
            "result_sets": getattr(result, "result_sets", []) or [],
            "trace_started": False,
            "events_received": 0,
        }

    @staticmethod
    def _map_trace_result(result) -> dict:
        """Map a :class:`TraceResult` onto the response contract.

        The first fifteen keys are the pre-existing contract and must not move —
        04_Run_DAX, 09_Debug and 13_Query_Plan all read them by name.
        """
        from pbi_perf_core.daxstudio_export import (
            build_query_plan_export,
            build_server_timings_export,
        )
        from pbi_perf_core.execution_metrics import summarise_execution_metrics
        from pbi_perf_core.query_plan import analyse_plan, parse_plan

        timings = result.timings
        total_ms = timings.total_duration if timings else 0
        fe_ms = timings.formula_engine_duration if timings else 0
        se_ms = timings.storage_engine_duration if timings else 0

        se_events: List[Dict[str, Any]] = []
        timeline: List[Dict[str, Any]] = []
        line = 1
        for row in (timings.events if timings else []):
            timeline.append(
                {
                    "line": row.row_number,
                    "class": row.class_name,
                    "subclass": row.subclass or "",
                    "duration_ms": row.duration,
                    "cpu_ms": row.cpu_time,
                    "rows": row.estimated_rows or 0,
                    "kb": row.estimated_kbytes or 0,
                    "start_offset_ms": row.start_offset_ms,
                    "end_offset_ms": row.start_offset_ms + row.duration,
                }
            )
            # se_events is the SE *scan* list. Cache matches have no duration,
            # and ExecutionMetrics is a JSON metrics payload — neither is a scan,
            # and both are surfaced through their own keys.
            if row.is_internal_event or row.class_name in _NON_SCAN_CLASSES:
                continue
            se_events.append(
                {
                    "line": line,
                    "duration_ms": row.duration,
                    "cpu_ms": row.cpu_time,
                    "parallelism": row.cpu_factor or 0,
                    "rows": row.estimated_rows or 0,
                    "kb": row.estimated_kbytes or 0,
                    "has_callback": row.highlight_query,
                    "query": row.query or row.text_data,
                }
            )
            line += 1

        physical = parse_plan(result.physical_plan, "physical")
        logical = parse_plan(result.logical_plan, "logical")

        mapped = {
            "total_ms": int(total_ms),
            "fe_ms": int(fe_ms),
            "se_ms": int(se_ms),
            "se_cpu_ms": int(timings.storage_engine_cpu) if timings else 0,
            "se_parallelism": round(timings.storage_engine_cpu_factor, 1) if timings else 0.0,
            "se_queries": timings.storage_engine_query_count if timings else 0,
            "se_cache_hits": timings.vertipaq_cache_matches if timings else 0,
            "fe_pct": timings.fe_pct if timings else 0.0,
            "se_pct": timings.se_pct if timings else 0.0,
            "se_events": se_events,
            "timeline": timeline,
            "agg_rewrites": list(timings.aggregate_rewrites) if timings else [],
            "logical_plan": result.logical_plan,
            "physical_plan": result.physical_plan,
            "cache_cleared": result.cache_cleared,
            # -- new in 7.15.0 -------------------------------------------
            "execution_metrics": summarise_execution_metrics(result.execution_metrics),
            "execution_metrics_raw": result.execution_metrics,
            "result_sets": result.result_sets,
            "plan_rows": {
                "physical": len(physical),
                "logical": len(logical),
                "max_records": physical.max_records,
                "highlighted": physical.highlight_count,
            },
            "plan_analysis": analyse_plan(
                physical,
                logical,
                result_rows=result.row_count,
                se_query_count=timings.storage_engine_query_count if timings else None,
            ),
            "parallel_se_detected": (
                timings.parallel_storage_engine_events_detected if timings else False
            ),
            "se_net_parallel_ms": (timings.storage_engine_net_parallel_duration if timings else 0),
            "total_cpu_ms": timings.total_cpu_duration if timings else 0,
            "dq_ms": timings.total_direct_query_duration if timings else 0,
            "runner_lane": "in_process",
            # B-08/B-10: how our events were identified and how many arrived.
            "trace_started": bool(getattr(result, "trace_started", False) or result.events),
            "events_received": max(
                int(getattr(result, "events_received", 0) or 0), len(result.events or [])
            ),
            "correlation": dict(getattr(result, "correlation", {}) or {}),
            # The DAX Studio file payloads, ready to write or diff. Built here
            # because only this lane has the ServerTimingsResult they need.
            "daxstudio": {
                "server_timings": build_server_timings_export(timings) if timings else {},
                "query_plan": build_query_plan_export(
                    physical,
                    logical,
                    activity_id=result.activity_id,
                    request_id=timings.request_id if timings else None,
                    command_text=timings.command_text if timings else "",
                    start_datetime=timings.query_start if timings else None,
                ),
            },
            "_timings": timings,
            "_physical_plan_parsed": physical,
            "_logical_plan_parsed": logical,
            "evaluation_log": result.evaluation_log,
            "eval_log_subscribed": bool(getattr(result, "eval_log_subscribed", False)),
        }
        if result.error:
            mapped["query_error"] = result.error
        return mapped

    def _execute_via_exe(self, query: str, clear_cache: bool = True) -> dict:
        """Legacy subprocess lane. No plans — the shipped exe predates them."""
        if self._exe_path is None:
            self._exe_path = self._find_exe()
        # .NET 8 resolves 'localhost' to IPv6 ::1, but PBI Desktop SSAS only
        # listens on IPv4 127.0.0.1. Normalise before passing to the exe.
        conn_str = self._connection_string.replace("localhost:", "127.0.0.1:")
        request = json.dumps(
            {
                "connection_string": conn_str,
                "query": query,
                "clear_cache": clear_cache,
            }
        )

        try:
            result = subprocess.run(
                [self._exe_path, "--local"],
                input=request,
                capture_output=True,
                text=True,
                timeout=_SUBPROCESS_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            logger.error("Native trace runner timed out")
            return {"_error": "Native trace runner timed out"}
        except FileNotFoundError:
            logger.error("DaxExecutor.exe not found at %s", self._exe_path)
            return {"_error": "DaxExecutor.exe not found"}

        if result.stderr:
            for line in result.stderr.strip().split("\n"):
                logger.debug("TraceRunner: %s", line)

        try:
            response = json.loads(result.stdout)
        except (json.JSONDecodeError, ValueError) as e:
            logger.error("Failed to parse trace runner output: %s", e)
            logger.error("stdout (first 500): %s", result.stdout[:500])
            return {"_error": f"Failed to parse native trace output: {e}"}

        # Check for error in C# response
        perf = response.get("Performance", {})
        if perf.get("Error"):
            return {"_error": perf.get("ErrorMessage", "Unknown trace error")}

        return self._map_response(response)

    @staticmethod
    def _map_timeline_event(evt: dict) -> Dict[str, Any]:
        """Map one C# EventDetail to a timeline row (SE scan or synthetic FE gap).

        Timeline offsets are absent on older DaxExecutor.exe builds — they
        default to None rather than 0 so a missing offset is distinguishable
        from a genuine zero offset at query start.
        """
        tl = evt.get("Timeline") or {}
        return {
            "line": evt.get("Line", 0),
            "class": evt.get("Class", "") or "",
            "subclass": evt.get("Subclass", "") or "",
            "duration_ms": evt.get("Duration", 0),
            "cpu_ms": evt.get("CPU", 0),
            "rows": evt.get("Rows", 0),
            "kb": evt.get("KB", 0),
            "start_offset_ms": tl.get("StartOffset"),
            "end_offset_ms": tl.get("EndOffset"),
        }

    @staticmethod
    def _map_agg_rewrites(response: dict) -> List[Dict[str, Any]]:
        """Normalise the C# AggRewrites list.

        Absent on the pre-existing exe build → empty list. Status is
        re-classified in Python so a plain-string subclass from an older
        build still lands in the right bucket.
        """
        from core.query_plan.capturer import _classify_agg_subclass

        rewrites = response.get("AggRewrites") or []
        if not isinstance(rewrites, list):
            return []
        mapped: List[Dict[str, Any]] = []
        for item in rewrites[:50]:
            if not isinstance(item, dict):
                continue
            subclass = item.get("Subclass", "")
            status = item.get("Status") or _classify_agg_subclass(subclass)
            mapped.append(
                {
                    "status": status,
                    "text": (item.get("Text") or "")[:500],
                    "subclass": subclass,
                }
            )
        return mapped

    @staticmethod
    def _map_response(response: dict) -> dict:
        """Map C# output format to Python handler format."""
        perf = response.get("Performance", {})
        events = response.get("EventDetails", [])

        total_ms = perf.get("Total", 0)
        fe_ms = perf.get("FE", 0)
        se_ms = perf.get("SE", 0)

        # Map SE event details (filter to SE events only, skip FE segments)
        # and, in the same pass, the full ordered timeline INCLUDING the
        # synthetic FE segments the C# side computes. se_events stays
        # SE-only for backwards compatibility; timeline is the waterfall.
        se_details = []
        timeline = []
        se_line = 1
        for evt in events:
            timeline.append(NativeTraceRunner._map_timeline_event(evt))
            if evt.get("Class") != "SE":
                continue
            scan_text = evt.get("Query", "") or ""
            se_details.append(
                {
                    "line": se_line,
                    "duration_ms": evt.get("Duration", 0),
                    "cpu_ms": evt.get("CPU", 0),
                    "parallelism": evt.get("Par", 0),
                    "rows": evt.get("Rows", 0),
                    "kb": evt.get("KB", 0),
                    # Per-scan callback flag. Aggregate callback stats live in
                    # se_analysis, but without this you cannot tell WHICH scan is
                    # paying the FE round-trip — the first thing to fix in a trace.
                    "has_callback": bool(_CALLBACK_RE.search(scan_text)),
                    "query": scan_text,
                }
            )
            se_line += 1

        return {
            "total_ms": int(total_ms),
            "fe_ms": int(fe_ms),
            "se_ms": int(se_ms),
            "se_cpu_ms": int(perf.get("SE_CPU", 0)),
            "se_parallelism": round(perf.get("SE_Par", 0), 1),
            "se_queries": perf.get("SE_Queries", 0),
            "se_cache_hits": perf.get("SE_Cache", 0),
            "fe_pct": round(fe_ms / total_ms * 100, 1) if total_ms > 0 else 0,
            "se_pct": round(se_ms / total_ms * 100, 1) if total_ms > 0 else 0,
            "se_events": se_details,
            # Full ordered waterfall incl. synthetic FE gaps. A large FE segment
            # points you at the SE query immediately before it.
            "timeline": timeline,
            "agg_rewrites": NativeTraceRunner._map_agg_rewrites(response),
            "logical_plan": response.get("LogicalPlan", "") or "",
            "physical_plan": response.get("PhysicalPlan", "") or "",
            "cache_cleared": response.get("CacheCleared", False),
            # The 7.15.0 keys, present but empty on this lane: the shipped exe
            # emits neither plans nor ExecutionMetrics, and reads only the first
            # result set. Callers get a stable shape, not a KeyError.
            "execution_metrics": {},
            "execution_metrics_raw": {},
            "result_sets": [],
            "plan_rows": {"physical": 0, "logical": 0, "max_records": 0, "highlighted": 0},
            "plan_analysis": {},
            "parallel_se_detected": False,
            "se_net_parallel_ms": int(se_ms),
            "total_cpu_ms": int(perf.get("SE_CPU", 0)),
            "dq_ms": 0,
            "runner_lane": "exe",
            "evaluation_log": [],
            "daxstudio": {"server_timings": {}, "query_plan": {}},
        }
