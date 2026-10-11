"""
Query Handler
Handles DAX query execution, validation, and data preview.
Includes SE/FE trace analysis (previously in query_trace_handler).

2026-09-22 review (slice B / L): results carry ``cache: {hit, age_s}``
top-level; ``bypass_cache`` (alias ``no_cache``) forces a server round-trip;
``mode=analyze`` is a real N-run benchmark (``runs``, ``cold``); ``roles`` /
``effective_user_name`` / ``custom_data`` are honoured via an impersonated
connection; ``export_path`` streams the full result to disk (no 10k cap) and
reports truncation; ``mode=trace`` returns rows too and honours ``top_n`` /
``export_path``.
"""

from typing import Any, Dict, List, Optional
import logging
import uuid

from server.registry import ToolDefinition
from server.tool_schemas import TOOL_SCHEMAS
from core.infrastructure.adomd_utils import engine_message
from core.infrastructure.connection_state import connection_state
from core.validation.error_handler import ErrorHandler
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)

#: Rows kept per result set in mode=trace when neither top_n nor export_path is given.
_TRACE_DEFAULT_ROWS = 100
#: Upper bound of rows materialised during a traced run (rows are held in memory).
_TRACE_MAX_ROWS = 100_000
#: Default export cap when export_path is set without top_n.
_EXPORT_DEFAULT_MAX_ROWS = 1_000_000


def _command_budget_for(tool_name: str):
    """ADOMD CommandTimeout budget = dispatcher timeout - 5 s (B-19).

    The dispatcher (WP2) may publish its own budget; ``command_budget`` keeps
    the tighter of the two, so this is safe either way.
    """
    from core.infrastructure.adomd_utils import command_budget

    try:
        from core.config.tool_timeouts import ToolTimeoutManager

        seconds = ToolTimeoutManager().get_timeout(tool_name)
        return command_budget(max(5, int(seconds) - 5))
    except Exception:  # noqa: BLE001
        return command_budget(None)


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _read_only_mode() -> bool:
    try:
        from core.config.config_manager import config

        return bool(config.get("security.read_only_mode", False))
    except Exception:  # noqa: BLE001
        return False


def _read_only_export_refusal(export_path: str) -> Dict[str, Any]:
    return {
        "success": False,
        "error": (
            f"export_path={export_path!r} writes a file, which read_only_mode forbids. "
            "Drop export_path (rows are returned inline, capped by top_n) or disable "
            "security.read_only_mode."
        ),
        "error_type": "read_only_mode",
    }


def _attach_identity(result: Dict[str, Any]) -> Dict[str, Any]:
    """B-35: port + connection/model generation so a caller can see a model switch."""
    try:
        ident = connection_state.get_connection_identity()
    except Exception:  # noqa: BLE001
        return result
    if isinstance(ident, dict):
        result.setdefault("connection", ident)
    return result


def _ensure_cache_block(result: Dict[str, Any], bypassed: bool) -> Dict[str, Any]:
    """Every 04_Run_DAX result carries ``cache: {hit, age_s}`` top-level (B-35)."""
    if not isinstance(result, dict):
        return result
    cache = result.get("cache")
    if not isinstance(cache, dict):
        cache = {}
        result["cache"] = cache
    cache.setdefault("hit", False)
    cache.setdefault("age_s", None)
    if bypassed:
        cache["bypassed"] = True
    return result


def _normalise_roles(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [r.strip() for r in raw.split(",") if r.strip()]
    if isinstance(raw, (list, tuple)):
        return [str(r).strip() for r in raw if str(r).strip()]
    return [str(raw)]


def handle_run_dax_trace(args: Dict[str, Any], *, source: str = "04_Run_DAX") -> Dict[str, Any]:
    """Execute DAX query with SE/FE trace analysis (in-process AMO trace or exe lane).

    L-19: rows of the SAME traced run are returned (first ``top_n`` rows of the
    first result set, default 100) and ``export_path`` writes them to disk; the
    exe lane cannot return rows and says so in ``ignored_args``.
    """
    if not connection_state.is_connected():
        return ErrorHandler.handle_not_connected()

    query = _query_text(args)
    if not query:
        return {"success": False, "error": "query parameter required", "error_type": "invalid_args"}

    clear_cache = args.get("clear_cache", True)
    export_path = args.get("export_path")
    top_n_raw = args.get("top_n")
    if export_path:
        keep_rows = int(top_n_raw) if top_n_raw else _TRACE_MAX_ROWS
    else:
        keep_rows = int(top_n_raw) if top_n_raw is not None else _TRACE_DEFAULT_ROWS
    keep_rows = max(0, min(_TRACE_MAX_ROWS, keep_rows))
    if export_path and _read_only_mode():
        return _read_only_export_refusal(export_path)

    try:
        conn_str = connection_state.connection_manager.connection_string
        if not conn_str:
            return {
                "success": False,
                "error": "No connection string available",
                "error_type": "not_connected",
            }

        from core.infrastructure.query_trace import NativeTraceRunner

        if not NativeTraceRunner.is_available():
            return {
                "success": False,
                "error": "Native trace runner (DaxExecutor.exe) not found. "
                "Build it with: cd core/infrastructure/dax_executor && dotnet build -c Release",
                "error_type": "trace_runner_unavailable",
            }

        native = NativeTraceRunner(conn_str)
        try:
            result = native.execute_with_trace(query, clear_cache, keep_rows=keep_rows)
        except TypeError:  # older runner signature
            result = native.execute_with_trace(query, clear_cache)

        if not isinstance(result, dict):
            result = {}
        if "_error" in result:
            error_type = result.get("_error_type") or "trace_execution_error"
            out: Dict[str, Any] = {
                "success": False,
                "error": f'Trace execution failed: {result["_error"]}',
                "error_type": error_type,
            }
            for key in ("reason", "amo", "result_sets"):
                if key in result:
                    out[key] = result[key]
            if error_type == "busy":
                out["retry_after"] = 5
            return out

        perf = {
            "total_ms": result.get("total_ms", 0),
            "fe_ms": result.get("fe_ms", 0),
            "se_ms": result.get("se_ms", 0),
            "se_cpu_ms": result.get("se_cpu_ms", 0),
            "se_parallelism": result.get("se_parallelism", 0.0),
            "se_queries": result.get("se_queries", 0),
            "se_cache_hits": result.get("se_cache_hits", 0),
            "fe_pct": result.get("fe_pct", 0.0),
            "se_pct": result.get("se_pct", 0.0),
            "se_net_parallel_ms": result.get("se_net_parallel_ms", 0),
            "total_cpu_ms": result.get("total_cpu_ms", 0),
            "dq_ms": result.get("dq_ms", 0),
            "parallel_se_detected": result.get("parallel_se_detected", False),
        }

        summary = (
            f"Total: {perf['total_ms']}ms | "
            f"FE: {perf['fe_ms']}ms ({perf['fe_pct']}%) | "
            f"SE: {perf['se_ms']}ms ({perf['se_pct']}%) | "
            f"SE queries: {perf['se_queries']} | "
            f"SE cache: {perf['se_cache_hits']}"
        )

        # Deduplicate xmSQL query text in SE events
        raw_se_events = result.get("se_events", []) or []
        query_lookup = []
        query_to_idx = {}
        compact_se = []
        for evt in raw_se_events:
            qt = evt.get("query", "")
            if qt not in query_to_idx:
                query_to_idx[qt] = len(query_lookup)
                query_lookup.append(qt)
            ce = {k: v for k, v in evt.items() if k != "query"}
            ce["query_idx"] = query_to_idx[qt]
            compact_se.append(ce)

        result_sets = result.get("result_sets", []) or []
        trace_id = f"trace_{uuid.uuid4().hex[:12]}"
        trace_data: Dict[str, Any] = {
            "trace_id": trace_id,
            "query": query,
            "source": source,
            "runner": "native",
            "runner_lane": result.get("runner_lane", "unknown"),
            "performance": perf,
            "se_queries_lookup": query_lookup,
            "se_events": compact_se,
            "_se_events_raw": raw_se_events,
            "cache_cleared": result.get("cache_cleared", False),
            # Every EVALUATE, not just the first — a multi-statement query used
            # to report only result set 1, which silently hid the rest.
            "result_sets": [
                {k: v for k, v in rs.items() if k != "rows"} if isinstance(rs, dict) else rs
                for rs in result_sets
            ],
            "execution_metrics": result.get("execution_metrics", {}),
            "summary": summary,
        }
        for key in ("trace_started", "events_received", "correlation"):
            if key in result:
                trace_data[key] = result[key]

        # L-19: rows from the same traced run.
        first = result_sets[0] if result_sets and isinstance(result_sets[0], dict) else None
        ignored: List[str] = []
        if first is not None and "rows" in first:
            rows = first.get("rows") or []
            trace_data["rows"] = rows
            trace_data["columns"] = first.get("columns", [])
            trace_data["row_count"] = len(rows)
            trace_data["truncated"] = bool(first.get("truncated"))
            if export_path:
                trace_data["export"] = _write_rows_file(
                    rows,
                    first.get("columns") or [],
                    export_path,
                    truncated=bool(first.get("truncated")),
                    total_rows=first.get("row_count"),
                )
                trace_data.pop("rows", None)
                trace_data["sample"] = rows[:10]
        else:
            if export_path:
                ignored.append("export_path")
            if top_n_raw is not None:
                ignored.append("top_n")
            trace_data["rows_note"] = (
                "This trace lane returns timings only (no rows); run 04_Run_DAX "
                "without mode=trace (bypass_cache=true) for the data."
            )
        if ignored:
            trace_data["ignored_args"] = ignored

        # Deep SE event analysis — same surface as 09_Debug_Operations, which
        # attaches it via _debug/helpers.py::_build_trace_response.
        if raw_se_events:
            try:
                from core.dax.se_event_analyzer import SeEventAnalyzer

                trace_data["se_analysis"] = SeEventAnalyzer().analyze(raw_se_events, perf)
            except Exception:
                pass
        # L-20: cached for 09 optimize, addressable by trace_id.
        stored_id = connection_state.store_trace_result(trace_data)
        if isinstance(stored_id, str):
            trace_data["trace_id"] = stored_id

        out = {"success": True, **{k: v for k, v in trace_data.items() if not k.startswith("_")}}
        out["cache"] = {"hit": False, "age_s": None, "bypassed": True}
        return out

    except ImportError:
        return {
            "success": False,
            "error": "NativeTraceRunner not available. Ensure core/infrastructure/query_trace.py exists.",
            "error_type": "trace_runner_unavailable",
        }
    except Exception as e:
        logger.error(f"Query trace execution failed: {e}")
        return {
            "success": False,
            "error": f"Trace execution failed: {engine_message(e)}",
            "error_type": "trace_execution_error",
        }


def _run_impersonated(args: Dict[str, Any], roles: List[str]) -> Dict[str, Any]:
    """B-13: 04_Run_DAX honours roles / effective_user_name / custom_data."""
    from core.infrastructure.role_impersonation import run_as_role

    mode = (args.get("mode") or "auto").lower()
    if mode in ("trace", "analyze"):
        return {
            "success": False,
            "error": f"roles/effective_user_name cannot be combined with mode={mode}",
            "error_type": "unsupported_arg",
        }
    if args.get("export_path"):
        return {
            "success": False,
            "error": "export_path is not supported together with roles",
            "error_type": "unsupported_arg",
        }
    top_n = args.get("top_n", 100)
    res = run_as_role(
        dax_query=args["query"],
        roles=roles,
        effective_user_name=args.get("effective_user_name"),
        top_n=int(top_n) if top_n else 100,
        custom_data=args.get("custom_data"),
    )
    if isinstance(res, dict):
        res.setdefault("cache", {"hit": False, "age_s": None, "bypassed": True})
    return res


#: 04_Run_DAX execution modes (L-12: the single DAX executor).
RUN_DAX_MODES = ("auto", "simple", "analyze", "trace", "evaluate_and_log", "validate", "build")


def _query_text(args: Dict[str, Any]) -> str:
    """The DAX text: canonical ``query`` (L-11), legacy ``dax_query``."""
    value = args.get("query") or args.get("dax_query") or ""
    return value if isinstance(value, str) else str(value)


def _run_evaluate_and_log(args: Dict[str, Any], query: str) -> Dict[str, Any]:
    """mode=evaluate_and_log: DAXEvaluationLog events of one traced run.

    This is the implementation behind ``13_Query_Plan operation=evaluate_and_log``
    too (L-12: one code path).
    """
    from core.query_plan import capturer

    cap = capturer.capture(
        dax_query=query,
        clear_cache=bool(args.get("clear_cache", True)),
        include_xmsql=False,
        capture_eval_log=True,
    )
    if not cap.get("success"):
        return cap
    capturer.remember(cap)
    out: Dict[str, Any] = {
        "success": True,
        "capture_id": cap["capture_id"],
        "evaluation_log": cap.get("evaluation_log", []),
        "eval_log_subscribed": cap.get("eval_log_subscribed"),
        "se_fe_timings": cap.get("se_fe_timings", {}),
        "note": (
            "Returns DAXEvaluationLog trace events. Wrap a VAR in EVALUATEANDLOG(...) "
            "in your query, then run this op to inspect intermediate values without "
            "rewriting the measure to return them."
        ),
    }
    if cap.get("eval_log_subscribed") is False:
        out["warning"] = (
            "The DAXEvaluationLog event (135) was not subscribed on this server, so an "
            "empty evaluation_log does NOT mean EVALUATEANDLOG logged nothing."
        )
    elif not out["evaluation_log"]:
        out["hint"] = (
            "No DAXEvaluationLog events: the query contains no EVALUATEANDLOG(...) call "
            "that was evaluated."
        )
    return out


def _run_validate(query: str) -> Dict[str, Any]:
    """mode=validate: the engine parses/binds ``query`` without executing it (PV-01)."""
    qe = connection_state.query_executor
    if not qe or not hasattr(qe, "validate_dax"):
        return ErrorHandler.handle_manager_unavailable("query_executor")
    result = qe.validate_dax(query)
    if isinstance(result, dict):
        result.setdefault("mode", "validate")
        _attach_identity(result)
    return result


def _run_build(args: Dict[str, Any], source: str) -> Dict[str, Any]:
    """mode=build (F-20a): measures + group_by + filters (+ order_by) -> a SUMMARIZECOLUMNS query.

    Returned as ``query``; with ``execute=true`` it is also run through the normal executor
    (``top_n``, ``bypass_cache``, ``roles`` ... apply) and the result carries ``built_query``.
    """
    from core.debug.dax_query_builder import BuildError, build_query

    try:
        built = build_query(
            measures=args.get("measures"),
            group_by=args.get("group_by"),
            filters=args.get("filters"),
            order_by=args.get("order_by"),
        )
    except BuildError as exc:
        return {"success": False, "error": str(exc), "error_type": "invalid_args"}
    if not _truthy(args.get("execute", False)):
        return {"success": True, "mode": "build", "executed": False, **built}
    run_args = {
        k: v
        for k, v in args.items()
        if k not in ("measures", "group_by", "filters", "order_by", "execute")
    }
    run_args.update({"query": built["query"], "mode": "auto"})
    result = execute_dax(run_args, source=source)
    if isinstance(result, dict):
        result.setdefault("built_query", built["query"])
        result.setdefault("built_from", {k: v for k, v in built.items() if k != "query"})
    return result


#: ``queries`` batch cap (S-30): one call = one bounded sequential run on the one connection.
BATCH_MAX_QUERIES = 20
#: Modes a batch may run; trace/analyze/evaluate_and_log/build are one-query-at-a-time ops.
_BATCH_MODES = ("auto", "simple", "validate")
#: Per-call args that make no sense per batch entry (they would overwrite each other).
_BATCH_REFUSED_ARGS = ("export_path", "runs", "cold")


def _batch_invalid(message: str, **extra: Any) -> Dict[str, Any]:
    return error_envelope("invalid_args", message, **extra)


def _batch_entries(raw: Any) -> tuple:
    """``([(id, query)], error)`` for the ``queries`` argument (strings or ``{id, query}``)."""
    if not isinstance(raw, (list, tuple)) or not raw:
        return None, _batch_invalid(
            "queries must be a non-empty array of DAX strings or {id, query} objects"
        )
    if len(raw) > BATCH_MAX_QUERIES:
        return None, _batch_invalid(
            f"queries holds {len(raw)} entries; the cap is {BATCH_MAX_QUERIES} per call. Split the batch.",
            max_queries=BATCH_MAX_QUERIES,
        )
    entries: List[tuple] = []
    seen_ids = set()
    for i, item in enumerate(raw):
        if isinstance(item, dict):
            text = _query_text(item)
            qid = str(item.get("id") or f"q{i + 1}")
        else:
            text, qid = (item if isinstance(item, str) else ""), f"q{i + 1}"
        if not text.strip():
            return None, _batch_invalid(f"queries[{i}] has no DAX text")
        if qid in seen_ids:
            return None, _batch_invalid(f"duplicate query id {qid!r} in queries")
        seen_ids.add(qid)
        entries.append((qid, text))
    return entries, None


def _batch_ok(res: Dict[str, Any]) -> bool:
    """A result's verdict: its success; mode=validate results carry valid instead."""
    if "success" in res:
        return bool(res["success"])
    return not res.get("error") and res.get("valid", True) is not False


def _run_batch(args: Dict[str, Any], source: str) -> Dict[str, Any]:
    """``queries: [...]`` (S-30): run up to 20 queries one after another on the one connection.

    Shared options apply to every query (``top_n``, ``bypass_cache``, ``mode``, ``roles``...). Each
    result is ``{id, index, success, ...the single-query result}``; a failing query is recorded and
    the run continues unless ``stop_on_error``. A cancelled call returns what ran, with the ids that
    did not (``not_run``) and ``cancelled: true``.
    """
    from core.utilities.progress_ticker import Ticker

    if _query_text(args).strip():
        return _batch_invalid("pass either query or queries, not both")
    mode = str(args.get("mode") or "auto").lower()
    if mode not in _BATCH_MODES:
        return _batch_invalid(
            f"mode {mode!r} is not available in batch mode", valid_values=list(_BATCH_MODES)
        )
    refused = [k for k in _BATCH_REFUSED_ARGS if args.get(k)]
    if refused:
        return _batch_invalid(
            f"{', '.join(refused)} cannot be combined with queries (run those one at a time)"
        )
    entries, err = _batch_entries(args.get("queries"))
    if err:
        return err
    if not connection_state.is_connected():
        return ErrorHandler.handle_not_connected()

    stop_on_error = _truthy(args.get("stop_on_error", False))
    shared = {
        k: v for k, v in args.items() if k not in ("queries", "stop_on_error", "query", "dax_query")
    }
    ticker = Ticker(len(entries), "query")
    results: List[Dict[str, Any]] = []
    not_run: List[str] = []
    cancelled = stopped_early = False
    for index, (qid, text) in enumerate(entries):
        if cancelled or stopped_early:
            not_run.append(qid)
            continue
        if ticker.cancelled():
            cancelled = True
            not_run.append(qid)
            continue
        res = execute_dax({**shared, "query": text}, source=source)
        row = {"id": qid, "index": index}
        row.update(res if isinstance(res, dict) else error_envelope("operation_failed", str(res)))
        row["success"] = _batch_ok(row)
        results.append(row)
        ticker.tick(qid)
        if stop_on_error and not row["success"]:
            stopped_early = True
    failed = sum(1 for r in results if not r["success"])
    out: Dict[str, Any] = {
        "success": failed == 0 and not cancelled,
        "mode": "batch",
        "query_count": len(entries),
        "executed": len(results),
        "succeeded": len(results) - failed,
        "failed": failed,
        "results": results,
    }
    if failed:
        out["error"] = f"{failed} of {len(results)} executed queries failed; see results[].error"
        out["error_type"] = "batch_partial_failure"
    if cancelled:
        out["cancelled"] = True
        out["partial"] = True
    if stopped_early:
        out["stopped_early"] = True
    if not_run:
        out["not_run"] = not_run
    return _attach_identity(out)


def execute_dax(args: Dict[str, Any], *, source: str = "04_Run_DAX") -> Dict[str, Any]:
    """THE DAX executor (L-12). ``04_Run_DAX`` is this function; ``09_Debug_Operations
    run_dax``, ``04_Query_Operations impersonate_run`` and ``13_Query_Plan
    evaluate_and_log`` forward to it.

    Args (canonical names): ``query``, ``mode`` (auto|simple|analyze|trace|
    evaluate_and_log|validate), ``top_n``, ``bypass_cache``, ``runs``, ``cold``,
    ``clear_cache``, ``export_path``, ``roles``, ``effective_user_name``,
    ``custom_data``. ``source`` labels trace results (``trace_id`` provenance).
    """
    if str(args.get("mode") or "").lower() == "build":
        return _run_build(args, source)  # F-20a: no query text yet - the builder writes it
    if args.get("queries") is not None:
        return _run_batch(args, source)  # S-30: up to 20 queries, one connection

    query = _query_text(args)
    if not query.strip():
        return {"success": False, "error": "query parameter required", "error_type": "invalid_args"}

    mode = (args.get("mode") or "auto").lower()
    if mode not in RUN_DAX_MODES:
        return {
            "success": False,
            "error": f"Unknown mode {mode!r}",
            "error_type": "invalid_argument",
            "valid_values": list(RUN_DAX_MODES),
        }
    if mode == "evaluate_and_log":
        return _run_evaluate_and_log(args, query)

    if not connection_state.is_connected():
        return ErrorHandler.handle_not_connected()

    args = {**args, "query": query, "mode": mode}
    bypass_cache = _truthy(args.get("bypass_cache", False)) or _truthy(args.get("no_cache", False))

    with _command_budget_for("04_Run_DAX"):
        # PV-01: parse/bind only. Never cached, never impersonated, never executed.
        if mode == "validate":
            return _run_validate(query)

        roles = _normalise_roles(args.get("roles"))
        if roles or args.get("effective_user_name") or args.get("custom_data"):
            if not roles:
                return {
                    "success": False,
                    "error": "effective_user_name/custom_data require roles (Roles= impersonation)",
                    "error_type": "invalid_args",
                }
            return _run_impersonated(args, roles)

        # Trace mode: SE/FE timing analysis
        if mode == "trace":
            return handle_run_dax_trace(args, source=source)

        export_path = args.get("export_path")
        if export_path:
            if _read_only_mode():
                return _read_only_export_refusal(export_path)
            qe = connection_state.query_executor
            if not qe or not hasattr(qe, "export_query_to_file"):
                return ErrorHandler.handle_manager_unavailable("query_executor")
            top_n = args.get("top_n")
            max_rows = int(top_n) if top_n else _EXPORT_DEFAULT_MAX_ROWS
            res = qe.export_query_to_file(query, export_path, max_rows=max_rows)
            if isinstance(res, dict):
                _ensure_cache_block(res, True)
                _attach_identity(res)
            return res

        agent_policy = connection_state.agent_policy
        if not agent_policy:
            return ErrorHandler.handle_manager_unavailable("agent_policy")

        top_n = args.get("top_n", 100)
        kwargs: Dict[str, Any] = {
            "connection_state": connection_state,
            "query": query,
            "mode": mode,
            "max_rows": top_n,
        }
        if bypass_cache:
            kwargs["bypass_cache"] = True
        if args.get("runs") is not None:
            kwargs["runs"] = int(args["runs"])
        if _truthy(args.get("cold", False)):
            kwargs["cold"] = True
        result = agent_policy.safe_run_dax(**kwargs)

    if isinstance(result, dict):
        _ensure_cache_block(result, bypass_cache or mode == "analyze")
        _attach_identity(result)
    return result


def handle_run_dax(args: Dict[str, Any]) -> Dict[str, Any]:
    """04_Run_DAX: execute DAX (see :func:`execute_dax`)."""
    return execute_dax(args, source="04_Run_DAX")


_JSON_EXPORT_CHUNK = 5000


def _write_json_rows(fh: Any, rows: List[Dict[str, Any]]) -> None:
    """Write *rows* as ``json.dump(rows, fh, ensure_ascii=False, default=str)`` would.

    P-12: ``json.dump(obj, fh)`` runs the pure-Python encoder (472 ms per 100k x 8
    cells); ``json.dumps`` runs the C one (148 ms). Chunking keeps memory flat for
    exports of up to a million rows and produces the identical text
    (``[`` + items joined with ``, `` + ``]``).
    """
    import json

    if not rows:
        fh.write("[]")
        return
    fh.write("[")
    first = True
    for start in range(0, len(rows), _JSON_EXPORT_CHUNK):
        chunk = json.dumps(
            rows[start : start + _JSON_EXPORT_CHUNK], ensure_ascii=False, default=str
        )
        if not first:
            fh.write(", ")
        fh.write(chunk[1:-1])
        first = False
    fh.write("]")


def _write_rows_file(
    rows: List[Dict[str, Any]],
    columns: List[str],
    export_path: str,
    *,
    truncated: bool = False,
    total_rows: Optional[int] = None,
) -> Dict[str, Any]:
    """Atomically write already-materialised rows to CSV (utf-8-sig) or JSON."""
    import csv
    import os
    import tempfile

    abspath = os.path.abspath(export_path)
    ext = os.path.splitext(abspath)[1].lower()
    tmp = None
    try:
        os.makedirs(os.path.dirname(abspath), exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            prefix=".export_", suffix=ext or ".csv", dir=os.path.dirname(abspath)
        )
        if ext == ".json":
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                _write_json_rows(fh, rows)
        else:
            with os.fdopen(fd, "w", newline="", encoding="utf-8-sig") as fh:
                writer = csv.DictWriter(fh, fieldnames=columns or (list(rows[0]) if rows else []))
                writer.writeheader()
                for row in rows:
                    writer.writerow(row)
        os.replace(tmp, abspath)
        tmp = None
    except OSError as e:
        return {"exported": False, "error": f"Failed to write export to {abspath}: {e}"}
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return {
        "exported": True,
        "export_path": abspath,
        "row_count": len(rows),
        "total_rows": total_rows,
        "truncated": truncated,
    }


def _dax_bracket_name(name: str) -> str:
    """``Sales [EUR]`` -> ``[Sales [EUR]]]`` (B-22: escape ``]`` as ``]]``)."""
    return "[" + str(name).replace("]", "]]") + "]"


def _measure_ref(target: str) -> str:
    """Accept ``Measure``, ``[Measure]``, ``Table[Measure]`` or ``'Table'[Measure]``."""
    import re

    t = str(target).strip()
    if t.endswith("]") and "[" in t:
        idx = t.index("[")
        prefix, inner = t[:idx], t[idx + 1 : -1]
        if not prefix:
            return _dax_bracket_name(inner.replace("]]", "]"))
        if re.fullmatch(r"'(?:[^']|'')+'|[A-Za-z_][A-Za-z0-9_]*", prefix):
            return t  # already a qualified Table[Measure] reference
    return _dax_bracket_name(t)


def _handle_warm_cache(args: Dict[str, Any]) -> Dict[str, Any]:
    """Warm the VertiPaq cache by evaluating each target without clearing it.

    B-04/B-22: every query bypasses the server's result cache (otherwise a
    second warm within the TTL never reached the engine), measure names are
    bracket-escaped, and columns are scanned with ``COUNTROWS(DISTINCT(col))``
    instead of marshalling up to 10k distinct values.
    """
    qe = connection_state.query_executor
    if not qe:
        return ErrorHandler.handle_manager_unavailable("query_executor")

    measures = args.get("measures") or []
    columns = args.get("columns") or []  # entries like "Table[Column]" or "'Table'[Column]"
    if not measures and not columns:
        return {
            "success": False,
            "error": "measures or columns is required",
            "error_type": "invalid_args",
        }

    warmed = 0
    errors = []
    for m in measures:
        res = qe.validate_and_execute_dax(
            f'EVALUATE ROW("v", {_measure_ref(m)})', 0, bypass_cache=True
        )
        if res.get("success"):
            warmed += 1
        else:
            errors.append({"target": m, "error": res.get("error")})
    for col in columns:
        res = qe.validate_and_execute_dax(
            f'EVALUATE ROW("n", COUNTROWS(DISTINCT({col})))', 0, bypass_cache=True
        )
        if res.get("success"):
            warmed += 1
        else:
            errors.append({"target": col, "error": res.get("error")})

    return {
        "success": True,
        "warmed": warmed,
        "targets": len(measures) + len(columns),
        "errors": errors or None,
    }


def handle_get_data_sources(args: Dict[str, Any]) -> Dict[str, Any]:
    """List data sources: INFO.DATASOURCES, falling back to TOM (B-25).

    Power Query models have no legacy DataSource objects, so INFO.DATASOURCES
    is empty; the TOM crawl adds partition sources and M shared expressions.
    """
    if not connection_state.is_connected():
        return ErrorHandler.handle_not_connected()

    qe = connection_state.query_executor
    if not qe:
        return ErrorHandler.handle_manager_unavailable("query_executor")

    result = qe.execute_info_query("DATASOURCES")
    rows = result.get("rows") if isinstance(result, dict) else None
    if isinstance(result, dict) and result.get("success") and isinstance(rows, list) and rows:
        result.setdefault("method", "INFO.DATASOURCES")
        return result
    tom = getattr(qe, "list_data_sources_tom", None)
    if callable(tom):
        try:
            fallback = tom(limit=args.get("limit"))
        except Exception as e:  # noqa: BLE001
            fallback = error_envelope("operation_failed", str(e))
        if isinstance(fallback, dict) and fallback.get("success"):
            fallback["fallback_from"] = "INFO.DATASOURCES"
            if isinstance(result, dict) and not result.get("success"):
                fallback["info_error"] = result.get("error")
            return fallback
    return result


def handle_get_m_expressions(args: Dict[str, Any]) -> Dict[str, Any]:
    """List M/Power Query expressions: partition sources AND shared expressions (B-25).

    ``limit`` is pushed into the TOPN of each INFO query instead of fetching
    10k rows and slicing.
    """
    if not connection_state.is_connected():
        return ErrorHandler.handle_not_connected()

    qe = connection_state.query_executor
    if not qe:
        return ErrorHandler.handle_manager_unavailable("query_executor")

    limit = args.get("limit")
    try:
        limit_int = int(limit) if limit else None
    except (TypeError, ValueError):
        limit_int = None

    if limit_int:
        result = qe.execute_info_query("PARTITIONS", top_n=limit_int)
    else:
        result = qe.execute_info_query("PARTITIONS")
    if not isinstance(result, dict) or not result.get("success"):
        return result

    rows = list(result.get("rows") or [])
    if limit_int is not None:
        rows = rows[:limit_int]
    for row in rows:
        if isinstance(row, dict):
            row.setdefault("kind", "partition")
    remaining = None if limit_int is None else max(0, limit_int - len(rows))
    shared: List[Dict[str, Any]] = []
    if remaining is None or remaining > 0:
        try:
            if remaining:
                expr = qe.execute_info_query("EXPRESSIONS", top_n=remaining)
            else:
                expr = qe.execute_info_query("EXPRESSIONS")
            if isinstance(expr, dict) and expr.get("success"):
                for row in expr.get("rows") or []:
                    if isinstance(row, dict):
                        row = dict(row)
                        row.setdefault("kind", "shared_expression")
                        shared.append(row)
            elif isinstance(expr, dict):
                result["expressions_error"] = expr.get("error")
        except Exception as e:  # noqa: BLE001
            result["expressions_error"] = str(e)
    result["rows"] = rows + shared
    result["row_count"] = len(result["rows"])
    result["partition_count"] = len(rows)
    result["shared_expression_count"] = len(shared)
    return result


def handle_get_roles(args: Dict[str, Any]) -> Dict[str, Any]:
    """List RLS/OLS security roles with permissions.

    P2-182: inlined previous `_role_handler_singleton` pattern — the
    RoleOperationsHandler class is cheap to instantiate and only reached
    from the deprecated `04_Query_Operations:roles` alias.
    Canonical path is `02_Model_Operations {object_type: "role",
    operation: "list"}`.
    """
    from core.operations.role_operations import RoleOperationsHandler

    return RoleOperationsHandler().execute({"operation": "list"})


def handle_query_operations(args: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch query operations: data_sources, m_expressions, search_objects, roles, test_rls, search_string, dmv_probe, list_dmv_probes"""
    operation = args.get("operation", "data_sources")

    # list_dmv_probes is a static catalogue — no connection required.
    if operation == "list_dmv_probes":
        from core.dax.dmv_probes import list_probes

        return {"success": True, "probes": list_probes()}

    # P1-133 fix: hoist is_connected() guard to the top so disconnected
    # callers get a proper not_connected error shape.
    if not connection_state.is_connected():
        return {
            "success": False,
            "error": "Not connected to Power BI Desktop. Use 01_Connection first.",
            "error_type": "not_connected",
        }

    with _command_budget_for("04_Query_Operations"):
        return _dispatch_query_operation(operation, args)


def _dispatch_query_operation(operation: str, args: Dict[str, Any]) -> Dict[str, Any]:
    if operation == "warm_cache":
        return _handle_warm_cache(args)

    if operation == "dmv_probe":
        from core.dax.dmv_probes import run_probe, ProbeNotFoundError

        name = args.get("probe")
        if not name:
            return {
                "success": False,
                "error": "probe is required (use operation='list_dmv_probes' to see available)",
                "error_type": "invalid_args",
            }
        try:
            return run_probe(name)
        except ProbeNotFoundError as e:
            out = {"success": False, "error": str(e), "error_type": "probe_not_found"}
            if getattr(e, "did_you_mean", None):
                out["did_you_mean"] = e.did_you_mean
            from core.dax.dmv_probes import PROBE_REGISTRY

            out["valid_values"] = sorted(PROBE_REGISTRY)
            return out

    if operation == "data_sources":
        return handle_get_data_sources(args)
    elif operation == "m_expressions":
        return handle_get_m_expressions(args)
    elif operation == "search_objects":
        from server.handlers._internal.metadata_helpers import handle_search_objects

        return handle_search_objects(args)
    elif operation == "roles":
        # T115: deprecated alias. 02_Model_Operations object_type=role
        # operation=list is the canonical path. Kept for back-compat.
        result = handle_get_roles(args)
        if isinstance(result, dict):
            result.setdefault(
                "deprecated",
                "04_Query_Operations:roles is deprecated. "
                "Use 02_Model_Operations object_type=role operation=list instead.",
            )
        return result
    elif operation == "test_rls":
        return _handle_test_rls(args)
    elif operation == "impersonate_run":
        # L-12: a thin alias of 04_Run_DAX roles=[...] (same code path).
        if not _query_text(args):
            return {
                "success": False,
                "error": "query (legacy: dax_query) is required for impersonate_run",
                "error_type": "invalid_args",
            }
        roles = _normalise_roles(args.get("roles"))
        if not roles:
            return {
                "success": False,
                "error": "roles is required (at least one role name)",
                "error_type": "invalid_args",
            }
        res = execute_dax(
            {
                "query": _query_text(args),
                "roles": roles,
                "effective_user_name": args.get("effective_user_name"),
                "custom_data": args.get("custom_data"),
                "top_n": int(args.get("top_n", 1000)),
            },
            source="04_Query_Operations:impersonate_run",
        )
        if isinstance(res, dict):
            res.setdefault("executed_via", "04_Run_DAX")
        return res
    elif operation == "vertipaq_report":
        from core.dax.vertipaq_reporter import vertipaq_report

        return vertipaq_report(
            top_n=int(args.get("top_n", 50)),
            include_table_rollup=bool(args.get("include_table_rollup", True)),
            include_verdict=bool(args.get("include_verdict", True)),
        )
    elif operation == "vertipaq_analyzer":
        from core.dax.vpa_extractor import vertipaq_analyzer

        if args.get("export_path") and _read_only_mode():
            return _read_only_export_refusal(args["export_path"])
        return vertipaq_analyzer(
            include_stats=bool(args.get("include_stats", True)),
            top_n=int(args.get("top_n", 50)),
            view=args.get("view") or "summary",
            export_path=args.get("export_path"),
        )
    elif operation == "simulate_as_user":
        from core.dax.rls_rewriter import simulate_as_user

        if not _query_text(args):
            return {
                "success": False,
                "error": "query (legacy: dax_query) is required for simulate_as_user",
                "error_type": "invalid_args",
            }
        if not args.get("as_user"):
            return {
                "success": False,
                "error": "as_user is required (UPN to simulate, e.g. user@x.com)",
                "error_type": "invalid_args",
            }
        if not args.get("role_name"):
            return {
                "success": False,
                "error": "role_name is required (the RLS role whose filter to apply)",
                "error_type": "invalid_args",
            }
        return simulate_as_user(
            user_dax=_query_text(args),
            role_name=args["role_name"],
            as_user=args["as_user"],
            custom_data=args.get("custom_data"),  # WP6 / E-16: CUSTOMDATA() substitution
        )
    elif operation == "rls_audit":
        # F-07: per role x probe identity (known UPN, zero-grant, empty, casing, B2B #EXT#) visible
        # vs unfiltered rows through the simulate_as_user filter rewrite; read-only.
        from core.validation.rls_audit import audit_from_connection

        return audit_from_connection(args)
    elif operation == "search_string":
        from server.handlers._internal.metadata_helpers import handle_search_string

        return handle_search_string(args)
    elif operation == "info_view":
        from core.dax.info_view import info_view

        # info_view() allow-lists `view` itself (B-24) — an unknown value is a
        # structured error, never interpolated into INFO.VIEW.{view}().
        return info_view(
            view=args.get("view", "measures"),
            top_n=int(args.get("top_n", 1000)),
        )
    else:
        return {
            "success": False,
            "error": f"Unknown operation: {operation}. Valid: data_sources, m_expressions, search_objects, roles, test_rls, impersonate_run, simulate_as_user, vertipaq_report, vertipaq_analyzer, search_string, dmv_probe, list_dmv_probes, info_view, warm_cache, rls_audit",
            "error_type": "unknown_operation",
        }


def _handle_test_rls(args: Dict[str, Any]) -> Dict[str, Any]:
    """Test RLS role filter by comparing filtered vs unfiltered results."""
    from core.infrastructure.connection_state import connection_state

    if not connection_state.is_connected():
        return {
            "success": False,
            "error": "Not connected to Power BI Desktop",
            "error_type": "not_connected",
        }

    rls_mgr = connection_state.rls_manager
    if not rls_mgr:
        return {
            "success": False,
            "error": "RLS manager not available",
            "error_type": "manager_unavailable",
        }

    role_name = args.get("role_name")
    if not role_name:
        return {
            "success": False,
            "error": "role_name parameter required",
            "error_type": "invalid_args",
        }

    test_query = args.get("query") or args.get("test_query") or ""
    test_table = args.get("test_table", "")

    return rls_mgr.test_role_filter(role_name, test_query, test_table)


def register_query_handlers(registry):
    """Register query handlers"""
    tools = [
        ToolDefinition(
            name="04_Run_DAX",
            description=(
                "Run a DAX query (EVALUATE / DEFINE ... EVALUATE) on the live model and "
                "return rows - the ground-truth numbers. The one DAX executor: mode "
                "auto|simple (preview, top_n rows + truncated flag), analyze (N-run "
                "benchmark), trace (SE/FE timings + rows of the same run, trace_id), "
                "evaluate_and_log, validate (parse/bind, no execution), build (measures + "
                "group_by + filters -> SUMMARIZECOLUMNS, execute=true runs it). export_path "
                "streams the full result to .csv/.json; roles / effective_user_name / "
                "custom_data = RLS impersonation; bypass_cache=true skips the cache. "
                "Static analysis: 05_DAX_Intelligence; plans: 13_Query_Plan; a visual's own "
                "query: 09_Debug_Operations."
            ),
            guide_notes=(
                "Run / execute a DAX query against the live model and return "
                "rows — EVALUATE statements, measure values, table previews, "
                "SUMMARIZECOLUMNS, ground-truth numbers. mode=build (F-20a) writes the query for "
                "you: measures=[names | {name, expression}], group_by=['Table[Col]'], filters=["
                "'Table[Col] = 2025' | {column, operator (= in <> not in > >= < <= between), "
                "values} | a DAX table expression], order_by=['Table[Col] desc', '[Measure] desc'] "
                "-> `query` (no `query` argument needed; execute=true also runs it, top_n etc. "
                "apply). mode: auto|simple "
                "(preview), analyze (real N-run benchmark: runs, cold), trace "
                "(SE/FE timings + rows of the same run). `query` MUST start "
                "with EVALUATE (or DEFINE ... EVALUATE); rows are capped by "
                "top_n (default 100) and `truncated` says when more exist — "
                "pass export_path (.csv/.json) to stream the FULL result to "
                "disk. Results carry cache:{hit, age_s}; the cache is keyed on "
                "the connected model + a model generation (writes and "
                "Desktop-side edits/refreshes invalidate it); pass "
                "bypass_cache=true to force a server round-trip. roles / "
                "effective_user_name / custom_data run the query under RLS "
                "impersonation. mode=validate asks the engine to parse and bind the "
                "query WITHOUT executing it (unused-branch rewrite of every EVALUATE, "
                "10 s backstop) and returns {valid, errors[], elapsed_ms}: engine syntax "
                "and name-resolution errors, not run-time errors; ORDER BY / START AT "
                "clauses are not checked. Otherwise this EXECUTES DAX: for static analysis use "
                "05_DAX_Intelligence; for query plan / xmSQL use 13_Query_Plan; "
                "for a visual's own filter context + query use "
                "09_Debug_Operations:visual."
            ),
            handler=handle_run_dax,
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "DAX query (EVALUATE statement)"},
                    "top_n": {
                        "type": "integer",
                        "description": (
                            "Max rows inline (default 100); with export_path: rows written "
                            "(default 1,000,000)."
                        ),
                        "x-doc": (
                            "Max rows returned inline (default 100). With export_path: max "
                            "rows written (default 1,000,000). mode=trace: rows kept from "
                            "the traced run (default 100)."
                        ),
                        "default": 100,
                    },
                    "mode": {
                        "type": "string",
                        "enum": list(RUN_DAX_MODES),
                        "default": "auto",
                        "description": (
                            "auto/simple=preview; analyze=N-run benchmark; trace=SE/FE "
                            "timings + rows; evaluate_and_log; validate=parse/bind only; "
                            "build=query builder"
                        ),
                        "x-doc": (
                            "auto/simple=preview; analyze=N-run benchmark (runs, cold); "
                            "trace=SE/FE timings + rows; evaluate_and_log=EVALUATEANDLOG "
                            "events; validate=parse/bind only; build=measures + group_by + "
                            "filters (+ order_by) -> a SUMMARIZECOLUMNS query, returned as "
                            "`query` (execute=true also runs it; no `query` argument needed)"
                        ),
                    },
                    "bypass_cache": {
                        "type": "boolean",
                        "default": False,
                        "description": "Skip the server-side result cache and always execute",
                    },
                    "runs": {
                        "type": "integer",
                        "description": "analyze: number of timed runs (default 3, max 20)",
                    },
                    "cold": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "analyze: clear the VertiPaq cache before every timed run "
                            "(default: 1 untimed warm-up, then warm runs)"
                        ),
                    },
                    "clear_cache": {
                        "type": "boolean",
                        "default": True,
                        "description": "Clear VertiPaq cache before execution (trace mode only)",
                    },
                    "export_path": {
                        "type": "string",
                        "description": (
                            "Stream the FULL result to .csv (UTF-8 BOM) or .json; returns "
                            "path, row_count, truncated, sample instead of rows."
                        ),
                        "x-doc": (
                            "Stream the FULL result set to this file (.csv as UTF-8 with "
                            "BOM, or .json) and return {export_path (absolute), row_count, "
                            "total_rows, truncated, columns, sample} instead of inline "
                            "rows. Refused in read_only_mode."
                        ),
                    },
                    "roles": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Run under these RLS roles (not with mode=trace/analyze or "
                            "export_path)."
                        ),
                        "x-doc": (
                            "Run under these RLS roles (Roles= impersonation; comma-string "
                            "accepted). Not combinable with mode=trace/analyze or export_path."
                        ),
                    },
                    "effective_user_name": {
                        "type": "string",
                        "description": (
                            "UPN for EffectiveUserName (Service/SSAS only — Power BI "
                            "Desktop rejects it; use 04_Query_Operations simulate_as_user)"
                        ),
                    },
                    "custom_data": {
                        "type": "string",
                        "description": "CustomData= value for CUSTOMDATA()-based RLS (with roles)",
                    },
                    "measures": {
                        "type": "array",
                        "items": {"type": ["string", "object"]},
                        "description": "build: measure names or {name, expression}",
                    },
                    "group_by": {
                        "type": "array",
                        "items": {"type": ["string", "object"]},
                        "description": "build: columns, 'Table[Col]'",
                    },
                    "filters": {
                        "type": "array",
                        "items": {"type": ["string", "object"]},
                        "description": (
                            "build: 'Table[Col] = 2025', {column, operator, values} or a DAX "
                            "table expression"
                        ),
                    },
                    "order_by": {
                        "type": "array",
                        "items": {"type": ["string", "object"]},
                        "description": "build: 'Table[Col] desc' / '[Measure] desc'",
                    },
                    "execute": {
                        "type": "boolean",
                        "description": "build: also run the built query (default: return it only)",
                    },
                    "queries": {
                        "type": "array",
                        "maxItems": 20,
                        "items": {
                            "oneOf": [
                                {"type": "string"},
                                {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "string"},
                                        "query": {"type": "string"},
                                    },
                                    "required": ["query"],
                                },
                            ]
                        },
                        "description": "Batch: up to 20 DAX queries run sequentially, per-query result/error",
                    },
                    "stop_on_error": {"type": "boolean"},
                },
                # `query` is required for every mode except build (checked by the handler).
                "required": [],
            },
            category="query",
            sort_order=40,
            annotations={
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
            },
        ),
        ToolDefinition(
            name="04_Query_Operations",
            description=(
                "Model metadata + introspection on the live model: data_sources, "
                "m_expressions, search_objects, search_string, info_view (INFO.VIEW.*), "
                "dmv_probe, list_dmv_probes, vertipaq_report, vertipaq_analyzer (VertiPaq "
                "Analyzer parity; view=summary|tables|columns|...), warm_cache. RLS: "
                "test_rls, impersonate_run (= 04_Run_DAX roles), simulate_as_user "
                "(USERPRINCIPALNAME rewrite for Desktop), rls_audit (fail-open check per "
                "role). roles op deprecated: 02_Model_Operations object_type=role."
            ),
            guide_notes=(
                "Query model metadata + introspection: data_sources (INFO + TOM "
                "fallback), m_expressions (partitions + shared expressions), "
                "search_objects (substring match), test_rls, impersonate_run (Roles= ADOMD), "
                "simulate_as_user (filter-rewrite for PBI Desktop), "
                "rls_audit (every role x probe identity: known UPN as_user, zero-grant, empty, "
                "casing variant, B2B #EXT#; visible vs unfiltered rows per RLS-filtered table; "
                "flags fail-open / sees_everything, fail_closed_for_everyone, roles without "
                "members, tables reached only via bidirectional relationships; roles=[..] "
                "subset, probe_identities=[..] extra; simulation=filter_rewrite, approximate), "
                "vertipaq_report (top columns by RAM with hot/warn/info verdict), "
                "vertipaq_analyzer (full VertiPaq Analyzer parity — model size, "
                "cardinality, encoding, segments, partitions, relationships, %DB "
                "+ actionable findings; view=summary|tables|columns|relationships|"
                "partitions|segments|all, include_stats=false to skip the DAX "
                "cardinality scan), "
                "search_string, dmv_probe, list_dmv_probes, "
                "info_view (INFO.VIEW.MEASURES/TABLES/COLUMNS/RELATIONSHIPS — "
                "friendly-named metadata, falls back to TMSCHEMA/MDSCHEMA DMVs "
                "on CL<1604 models), warm_cache. "
                "(`roles` op is deprecated — use 02_Model_Operations "
                "object_type=role operation=list.)"
            ),
            handler=handle_query_operations,
            input_schema=TOOL_SCHEMAS["query_operations"],
            category="query",
            sort_order=41,
            annotations={
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": False,
                "openWorldHint": True,
            },
        ),
    ]

    for tool in tools:
        registry.register(tool)

    logger.info(f"Registered {len(tools)} query handlers")
