"""Tests for the in-process AMO trace lane (7.15.0).

The lane that replaced DaxExecutor.exe. Everything is mocked at the CLR
boundary — the fake ``Microsoft.AnalysisServices.*`` modules are installed into
``sys.modules`` so ``TraceSession`` exercises its real logic without pythonnet,
AMO or a running Power BI Desktop.

Four things are pinned:

1. Event plumbing — DMV capability intersection, plan splitting, internal/ping
   filtering, ActivityID correlation.
2. The response contract — every pre-7.15.0 key survives, the new ones appear,
   and the exe fallback still produces the same shape.
3. Fallback — a CLR failure falls through to the exe rather than surfacing as a
   query error.
4. The 13_Query_Plan surface — capture is a summary (never a raw plan), and
   plan_rows pages the real rows.
"""

from __future__ import annotations

import datetime as _dt
import sys
import types

import pytest

from core.infrastructure import trace_session
from core.infrastructure.query_trace import NativeTraceRunner

# --- helpers -------------------------------------------------------------

_T0 = _dt.datetime(2026, 9, 2, 9, 34, 0)


def _ev(event_class, **kw):
    """One raw trace-event row in the shape the AMO callback produces."""
    row = {
        "event_class": event_class,
        "text_data": kw.pop("text", ""),
        "activity_id": kw.pop("activity", "ACT-1"),
    }
    row.update(kw)
    return row


def _query_pair(activity="ACT-1", duration=1428, cpu=1297):
    """QueryBegin + QueryEnd, the frame every aggregation hangs off."""
    return [
        _ev(9, text='EVALUATE ROW("a",1)', activity=activity, start_time=_T0),
        _ev(
            10,
            activity=activity,
            start_time=_T0,
            end_time=_T0 + _dt.timedelta(milliseconds=duration),
            duration_ms=duration,
            cpu_time_ms=cpu,
        ),
    ]


_PHYSICAL = "AddColumns: IterPhyOp #Records=8\n\tCache: IterPhyOp #Records=418\n"
_LOGICAL = "AddColumns: RelLogOp\n\tScan_Vertipaq: RelLogOp\n"


# --- 1. event plumbing ---------------------------------------------------


class TestEventPlumbing:
    def test_plan_split_by_subclass(self):
        events = [
            _ev(112, event_subclass="1", text=_LOGICAL),
            _ev(112, event_subclass="2", text=_PHYSICAL),
        ]
        logical, physical = trace_session._split_plans(events)
        assert "Scan_Vertipaq" in logical
        assert "#Records=418" in physical

    def test_plan_split_accepts_named_subclass(self):
        """The subclass arrives as a name on some builds, an int on others."""
        events = [
            _ev(112, event_subclass="DAXVertiPaqLogicalPlan", text=_LOGICAL),
            _ev(112, event_subclass="DAXVertiPaqPhysicalPlan", text=_PHYSICAL),
        ]
        logical, physical = trace_session._split_plans(events)
        assert logical.strip() and physical.strip()

    def test_directquery_plan_subclasses_ignored(self):
        """Subclasses 3/4 are DirectQuery trees; DAX Studio drops them."""
        events = [_ev(112, event_subclass="3", text="AlgebrizerTree")]
        logical, physical = trace_session._split_plans(events)
        assert logical == "" and physical == ""

    def test_internal_and_ping_text_recognised(self):
        assert trace_session._is_internal_text(
            'EVALUATE /* <<MCP Internal>> */ ROW("MCP Session Refresh", 0)'
        )
        assert trace_session._is_internal_text("/* PING */ SELECT 1")
        assert not trace_session._is_internal_text('EVALUATE ROW("a", 1)')
        assert not trace_session._is_internal_text(None)

    def test_activity_id_comes_from_our_query_events(self):
        events = [
            _ev(82, activity="OTHER"),  # SE event from Desktop's own chatter
            _ev(10, activity="OURS"),
        ]
        assert trace_session._our_activity_id(events) == "OURS"

    def test_activity_id_none_without_query_events(self):
        assert trace_session._our_activity_id([_ev(82, activity="X")]) is None

    def test_eval_log_rows_parse_json_payload(self):
        rows = trace_session._eval_log_rows(
            [_ev(135, event_subclass="1", text='{"input": {"a": 1}}')]
        )
        assert rows[0]["parsed"] == {"input": {"a": 1}}

    def test_eval_log_rows_keep_unparseable_payload(self):
        rows = trace_session._eval_log_rows([_ev(135, text="not json")])
        assert rows[0]["raw"] == "not json" and "parsed" not in rows[0]

    def test_capability_map_parsed_from_dmv_xml(self, monkeypatch):
        """QueryBegin has no Duration and ExecutionMetrics no SessionID.

        Requesting a column an event lacks fails the whole trace, so the
        intersection has to come from the DMV rather than from a constant.
        """
        session = trace_session.TraceSession.__new__(trace_session.TraceSession)
        xml = (
            "<EVENTCATEGORY><EVENTLIST>"
            "<EVENT><ID>9</ID><EVENTCOLUMNLIST>"
            "<EVENTCOLUMN><ID>42</ID></EVENTCOLUMN>"
            "</EVENTCOLUMNLIST></EVENT>"
            "<EVENT><ID>136</ID><EVENTCOLUMNLIST>"
            "<EVENTCOLUMN><ID>0</ID></EVENTCOLUMN>"
            "<EVENTCOLUMN><ID>42</ID></EVENTCOLUMN>"
            "</EVENTCOLUMNLIST></EVENT>"
            "</EVENTLIST></EVENTCATEGORY>"
        )
        monkeypatch.setattr(
            session, "_execute_scalar_rows", lambda q: [{"Data": xml}], raising=False
        )
        supported = session._discover_supported_events()
        assert supported[9] == {42}
        assert supported[136] == {0, 42}
        assert 5 not in supported[9]  # Duration is genuinely absent

    def test_capability_map_empty_when_dmv_fails(self, monkeypatch):
        session = trace_session.TraceSession.__new__(trace_session.TraceSession)

        def boom(_q):
            raise RuntimeError("no such DMV")

        monkeypatch.setattr(session, "_execute_scalar_rows", boom, raising=False)
        assert session._discover_supported_events() == {}


# --- 2. aggregation + response contract ----------------------------------


def _fake_trace_result(with_plans=True, result_sets=None):
    """A TraceResult built from real core aggregation over synthetic events."""
    from pbi_perf_core.server_timings import process_results

    events = _query_pair()
    events.append(
        _ev(
            83,
            event_subclass="VertiPaqScan",
            text="SELECT SUM('Sales'[Amt]) -- CallbackDataID",
            start_time=_T0,
            end_time=_T0 + _dt.timedelta(milliseconds=60),
            duration_ms=60,
            cpu_time_ms=20,
        )
    )
    events.append(_ev(85, current_time=_T0))  # cache match
    events.append(
        _ev(
            136,
            text=(
                '{"durationMs": 1430, "totalCpuTimeMs": 1297, '
                '"queryProcessingCpuTimeMs": 1266, "vertipaqJobCpuTimeMs": 31, '
                '"approximatePeakMemConsumptionKB": 223127, "queryResultRows": 8, '
                '"queryDialect": 3}'
            ),
        )
    )

    result = trace_session.TraceResult()
    result.events = events
    result.result_sets = (
        result_sets if result_sets is not None else [{"columns": ["[a]"], "row_count": 8}]
    )
    result.timings = process_results(events, command_text='EVALUATE ROW("a",1)')
    result.logical_plan = _LOGICAL if with_plans else ""
    result.physical_plan = _PHYSICAL if with_plans else ""
    result.execution_metrics = {
        "durationMs": 1430,
        "totalCpuTimeMs": 1297,
        "queryProcessingCpuTimeMs": 1266,
        "vertipaqJobCpuTimeMs": 31,
        "approximatePeakMemConsumptionKB": 223127,
        "queryResultRows": 8,
    }
    result.cache_cleared = True
    result.activity_id = "ACT-1"
    return result


#: Every key the pre-7.15.0 response carried. Other handlers read these by name.
_LEGACY_KEYS = (
    "total_ms",
    "fe_ms",
    "se_ms",
    "se_cpu_ms",
    "se_parallelism",
    "se_queries",
    "se_cache_hits",
    "fe_pct",
    "se_pct",
    "se_events",
    "timeline",
    "agg_rewrites",
    "logical_plan",
    "physical_plan",
    "cache_cleared",
)

_NEW_KEYS = (
    "execution_metrics",
    "result_sets",
    "plan_rows",
    "daxstudio",
    "parallel_se_detected",
    "se_net_parallel_ms",
    "total_cpu_ms",
    "dq_ms",
)


class TestResponseContract:
    def test_legacy_keys_all_present(self):
        mapped = NativeTraceRunner._map_trace_result(_fake_trace_result())
        missing = [k for k in _LEGACY_KEYS if k not in mapped]
        assert missing == []

    def test_new_keys_all_present(self):
        mapped = NativeTraceRunner._map_trace_result(_fake_trace_result())
        missing = [k for k in _NEW_KEYS if k not in mapped]
        assert missing == []

    def test_exe_lane_returns_the_same_shape(self):
        """The fallback must not make callers branch on which lane ran."""
        response = {
            "Performance": {"Total": 100, "FE": 90, "SE": 10, "SE_Queries": 3},
            "EventDetails": [],
        }
        mapped = NativeTraceRunner._map_response(response)
        for key in _LEGACY_KEYS + _NEW_KEYS:
            assert key in mapped, key
        assert mapped["runner_lane"] == "exe"

    def test_plans_reach_the_response(self):
        mapped = NativeTraceRunner._map_trace_result(_fake_trace_result())
        assert "#Records=418" in mapped["physical_plan"]
        assert "Scan_Vertipaq" in mapped["logical_plan"]

    def test_plan_rows_summarises_shape_not_content(self):
        """plan_rows is a shape summary; the rows themselves stay file-backed."""
        mapped = NativeTraceRunner._map_trace_result(_fake_trace_result())
        assert mapped["plan_rows"]["physical"] == 2
        assert mapped["plan_rows"]["max_records"] == 418
        assert mapped["plan_rows"]["highlighted"] == 1

    def test_execution_metrics_summarised(self):
        mapped = NativeTraceRunner._map_trace_result(_fake_trace_result())
        metrics = mapped["execution_metrics"]
        assert metrics["peak_memory_kb"] == 223127
        assert metrics["result_rows"] == 8
        assert metrics["se_cpu_ms"] == 31

    def test_all_result_sets_are_reported(self):
        """A multi-EVALUATE query used to report only the first result set."""
        result = _fake_trace_result(
            result_sets=[
                {"columns": ["[a]"], "row_count": 8},
                {"columns": ["[b]"], "row_count": 3},
            ]
        )
        mapped = NativeTraceRunner._map_trace_result(result)
        assert [rs["row_count"] for rs in mapped["result_sets"]] == [8, 3]

    def test_daxstudio_payloads_have_the_documented_versions(self):
        mapped = NativeTraceRunner._map_trace_result(_fake_trace_result())
        assert mapped["daxstudio"]["server_timings"]["FileFormatVersion"] == 6
        assert mapped["daxstudio"]["query_plan"]["FileFormatVersion"] == 3

    def test_non_scan_rows_excluded_from_se_events(self):
        """se_events is the scan list.

        Cache matches are counted, not billed as scans, and ExecutionMetrics is
        a JSON metrics payload — letting either in puts a non-scan row into the
        list every SE analysis iterates.
        """
        mapped = NativeTraceRunner._map_trace_result(_fake_trace_result())
        assert mapped["se_cache_hits"] == 1
        assert len(mapped["se_events"]) == 1
        assert all(e["duration_ms"] > 0 for e in mapped["se_events"])
        assert not any(e["query"].lstrip().startswith("{") for e in mapped["se_events"])
        # The rows are still visible in the waterfall, just not billed as scans.
        assert {r["class"] for r in mapped["timeline"]} >= {
            "VertiPaqSEQueryCacheMatch",
            "ExecutionMetrics",
        }

    def test_callback_scan_flagged(self):
        mapped = NativeTraceRunner._map_trace_result(_fake_trace_result())
        assert any(e["has_callback"] for e in mapped["se_events"])

    def test_empty_plans_do_not_crash_the_mapper(self):
        mapped = NativeTraceRunner._map_trace_result(_fake_trace_result(with_plans=False))
        assert mapped["plan_rows"]["physical"] == 0
        assert mapped["logical_plan"] == ""


# --- 3. lane selection / fallback ----------------------------------------


class TestLaneSelection:
    def test_clr_failure_falls_back_to_exe(self, monkeypatch):
        """A broken CLR is a trace problem, not a query problem."""
        runner = NativeTraceRunner.__new__(NativeTraceRunner)
        runner._connection_string = "Data Source=127.0.0.1:1234"
        runner._exe_path = None

        monkeypatch.setattr(NativeTraceRunner, "in_process_available", staticmethod(lambda: True))
        monkeypatch.setattr(
            NativeTraceRunner,
            "_execute_in_process",
            lambda self, *a, **k: None,
        )
        monkeypatch.setattr(
            NativeTraceRunner,
            "_execute_via_exe",
            lambda self, q, c=True: {"total_ms": 1, "runner_lane": "exe"},
        )
        assert runner.execute_with_trace("EVALUATE {1}")["runner_lane"] == "exe"

    def test_in_process_result_is_used_when_available(self, monkeypatch):
        runner = NativeTraceRunner.__new__(NativeTraceRunner)
        runner._connection_string = "Data Source=127.0.0.1:1234"
        runner._exe_path = None

        monkeypatch.setattr(NativeTraceRunner, "in_process_available", staticmethod(lambda: True))
        monkeypatch.setattr(
            NativeTraceRunner,
            "_execute_in_process",
            lambda self, *a, **k: {"runner_lane": "in_process"},
        )

        def fail(*a, **k):
            raise AssertionError("exe lane must not run when in-process succeeded")

        monkeypatch.setattr(NativeTraceRunner, "_execute_via_exe", fail)
        assert runner.execute_with_trace("EVALUATE {1}")["runner_lane"] == "in_process"

    def test_is_available_true_when_only_in_process_lane_exists(self, monkeypatch):
        monkeypatch.setattr(NativeTraceRunner, "in_process_available", staticmethod(lambda: True))
        monkeypatch.setattr("core.infrastructure.dll_paths.get_trace_runner_path", lambda: None)
        assert NativeTraceRunner.is_available() is True

    def test_constructor_does_not_require_exe_when_in_process_works(self, monkeypatch):
        """The exe was never rebuilt on this machine; needing it would break everything."""
        monkeypatch.setattr(NativeTraceRunner, "in_process_available", staticmethod(lambda: True))
        monkeypatch.setattr("core.infrastructure.dll_paths.get_trace_runner_path", lambda: None)
        runner = NativeTraceRunner("Data Source=127.0.0.1:1234")
        assert runner._exe_path is None


# --- 4. CLR boundary: TraceSession against fake AMO ----------------------


class _FakeReader:
    """Minimal AdomdDataReader over a list of result sets."""

    def __init__(self, sets):
        self._sets = sets
        self._idx = 0
        self._row = -1

    @property
    def _cur(self):
        return self._sets[self._idx]

    @property
    def FieldCount(self):
        return len(self._cur["columns"])

    def GetName(self, i):
        return self._cur["columns"][i]

    def Read(self):
        self._row += 1
        return self._row < len(self._cur["rows"])

    def GetValue(self, i):
        return self._cur["rows"][self._row][i]

    def NextResult(self):
        if self._idx + 1 >= len(self._sets):
            return False
        self._idx += 1
        self._row = -1
        return True

    def Close(self):
        return None


@pytest.fixture
def fake_adomd(monkeypatch):
    """Install a fake ADOMD module so _execute_query runs its real logic."""
    captured = {}

    class FakeCommand:
        def __init__(self, text, connection):
            captured.setdefault("commands", []).append(text)
            self._text = text

        def ExecuteReader(self):
            return _FakeReader(captured["sets"])

        def ExecuteNonQuery(self):
            return 1

    module = types.ModuleType("Microsoft.AnalysisServices.AdomdClient")
    module.AdomdCommand = FakeCommand
    module.AdomdConnection = object
    monkeypatch.setitem(sys.modules, "Microsoft.AnalysisServices.AdomdClient", module)
    return captured


class TestQueryDraining:
    def test_every_result_set_is_drained_and_counted(self, fake_adomd):
        fake_adomd["sets"] = [
            {"columns": ["[a]"], "rows": [[1], [2], [3]]},
            {"columns": ["[b]", "[c]"], "rows": [[1, 2]]},
        ]
        session = trace_session.TraceSession.__new__(trace_session.TraceSession)
        session._connection = object()
        sets = session._execute_query("EVALUATE A EVALUATE B")
        assert sets == [
            {"columns": ["[a]"], "row_count": 3},
            {"columns": ["[b]", "[c]"], "row_count": 1},
        ]

    def test_rows_are_counted_not_materialised(self, fake_adomd):
        """A plan capture on a huge query must not become an OOM."""
        fake_adomd["sets"] = [{"columns": ["[a]"], "rows": [[i] for i in range(500)]}]
        session = trace_session.TraceSession.__new__(trace_session.TraceSession)
        session._connection = object()
        sets = session._execute_query("EVALUATE Big")
        assert sets[0]["row_count"] == 500
        assert "rows" not in sets[0]


class TestClearCache:
    def test_clear_cache_issues_batch_then_internal_refresh(self, fake_adomd):
        fake_adomd["sets"] = [{"columns": ["[a]"], "rows": []}]
        session = trace_session.TraceSession.__new__(trace_session.TraceSession)
        session._connection = object()
        session._database_name = "MyModel"
        assert session.clear_cache() is True

        commands = fake_adomd["commands"]
        assert "<ClearCache>" in commands[0]
        assert "<DatabaseID>MyModel</DatabaseID>" in commands[0]
        assert trace_session.INTERNAL_QUERY_HEADER in commands[1]

    def test_clear_cache_without_database_name_is_reported_false(self):
        session = trace_session.TraceSession.__new__(trace_session.TraceSession)
        session._database_name = None
        assert session.clear_cache() is False


class _FakeTraceEvent:
    """Stands in for AMO's TraceEventArgs: named properties, not indexing.

    Accessing a column the event class does not carry raises, exactly as the
    real object does - which is why the reader must tolerate it per property.
    """

    def __init__(self, **columns):
        self._columns = columns

    def __getattr__(self, name):
        try:
            return self._columns[name]
        except KeyError:
            raise AttributeError(name) from None


class TestEventCallbackThreadSafety:
    def test_callback_appends_under_lock_and_signals_start(self):
        """The AMO thread must only copy properties out; parsing happens later."""
        import threading

        session = trace_session.TraceSession.__new__(trace_session.TraceSession)
        session._events = []
        session._lock = threading.Lock()
        session._first_event = threading.Event()

        session._on_event(
            None,
            _FakeTraceEvent(
                EventClass="QueryEnd", TextData="EVALUATE 1", Duration=42, ActivityID="ACT-1"
            ),
        )

        assert session._first_event.is_set()
        assert session._events[0]["event_class"] == "QueryEnd"
        assert session._events[0]["duration_ms"] == 42
        assert session._events[0]["text_data"] == "EVALUATE 1"

    def test_callback_survives_unreadable_columns(self):
        """A column the event does not carry must not kill the capture."""
        import threading

        class Hostile(_FakeTraceEvent):
            @property
            def Duration(self):
                # QueryBegin genuinely has no Duration column; AMO raises here.
                raise RuntimeError("column not present")

        session = trace_session.TraceSession.__new__(trace_session.TraceSession)
        session._events = []
        session._lock = threading.Lock()
        session._first_event = threading.Event()

        session._on_event(None, Hostile(EventClass="QueryBegin", TextData="text"))
        assert session._events[0]["event_class"] == "QueryBegin"
        assert "duration_ms" not in session._events[0]

    def test_drain_clears_the_buffer(self):
        import threading

        session = trace_session.TraceSession.__new__(trace_session.TraceSession)
        session._events = [{"event_class": 10}]
        session._lock = threading.Lock()
        assert len(session._drain_events()) == 1
        assert session._drain_events() == []


class TestAggregationFiltering:
    def test_internal_and_foreign_events_are_dropped(self):
        """Local Desktop runs unfiltered, so correlation happens here."""
        session = trace_session.TraceSession.__new__(trace_session.TraceSession)
        session._xmsql_cleaner = None
        result = trace_session.TraceResult()

        events = _query_pair(activity="OURS")
        events.append(
            _ev(82, activity="OURS", text=f"EVALUATE {trace_session.INTERNAL_QUERY_HEADER} ROW()")
        )
        events.append(_ev(82, activity="SOMEONE-ELSE", text="foreign scan"))

        session._aggregate(result, events, "EVALUATE 1", True, False)

        texts = [e.get("text_data", "") for e in result.events]
        assert not any(trace_session.INTERNAL_QUERY_HEADER in t for t in texts)
        assert "foreign scan" not in texts
        assert result.activity_id == "OURS"

    def test_execution_metrics_are_extracted(self):
        session = trace_session.TraceSession.__new__(trace_session.TraceSession)
        session._xmsql_cleaner = None
        result = trace_session.TraceResult()
        events = _query_pair()
        events.append(_ev(136, text='{"queryResultRows": 8, "durationMs": 1430}'))

        session._aggregate(result, events, "EVALUATE 1", True, False)
        assert result.execution_metrics["queryResultRows"] == 8
