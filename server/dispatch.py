"""
Central Tool Dispatcher
Routes tool calls to appropriate handlers with error handling.

Sync entry point: :meth:`ToolDispatcher.dispatch` (security gates, argument
normalisation, write serialisation, pagination, response_format, error
envelope). Async entry points run it on a dedicated, bounded worker pool:

* :meth:`ToolDispatcher.dispatch_async` → result dict;
* :meth:`ToolDispatcher.dispatch_and_render_async` → rendered JSON text (the
  response pipeline also runs on the worker, never on the event loop — A-14).

Every handler runs inside ``contextvars.copy_context().run`` with the server
loop + a per-call cancel event published in :mod:`server.request_context`, so
progress / elicitation / roots / sampling / log forwarding work from the worker
thread (A-02 / L-01).

Timeouts & cancellation (A-03 / A-04 / L-16): a call that times out or is
cancelled (``notifications/cancelled``) has its cancel event set, in-flight
ADOMD commands are cancelled via ``connection_state.cancel_active_queries()``
when available, and:

* a call that never STARTED (still queued for a worker or the write lock) is
  skipped and reported ``timeout`` with ``executed: false`` (safe to retry);
* a started READ reports ``timeout`` (retry is safe);
* a started WRITE reports ``timeout_outcome_unknown`` — the worker may still
  finish it — with ``may_still_be_running``, an ``operation_id`` and a
  ``verify_with`` read call. An identical write is refused
  (``write_in_progress``) while the orphaned one is still running.

Idempotent retries (F-27): a write call may carry ``request_id`` (any string). The async
path records ``(tool, args hash, request_id) -> result`` for ``server.idempotency_ttl_s``
(default 600 s) — also when the caller timed out and the worker finished later — so a retry
after ``timeout_outcome_unknown`` returns the RECORDED outcome (``replayed: true``) instead of
applying the write twice, or ``write_in_progress`` while it still runs. A recorded FAILURE is
replayed only while the model generation is unchanged (otherwise it re-executes).

Write serialisation: ``write`` / ``destructive`` ops (see
:mod:`server.op_classification`) hold a process-wide lock; reads run
concurrently. After each successful write ``connection_state.bump_model_generation``
is called when available.
"""

from typing import Any, Dict, List, Optional, Tuple
import asyncio
import concurrent.futures
import contextvars
import difflib
import hashlib
import itertools
import json
import logging
import threading
import time

from server.registry import get_registry, HIGH_TOKEN_TOOLS
from server.arg_aliases import ArgConflict, normalize_tool_args
from server import request_context as rc
from server.op_classification import (
    classify,
    is_model_preview,
    is_write,
    op_label,
    returns_data,
    DESTRUCTIVE,
)
from server.security_policy import SecurityPolicy, load_policy

#: XM-01 / N-01: while the read-only XMLA target is active EVERY write-classified call is
#: refused, except for tools that only write LOCAL files (PBIR report files, TMDL / linguistic
#: files, suite JSON, Word docs, the Desktop bridge, gateway control). An allow-list, so a new
#: writer tool is blocked by default; ``write_lane.commit`` refuses as the second line.
_XMLA_FILE_ONLY_TOOLS = frozenset(
    {
        "00_Client_Mode",
        "01_Connection",
        "02_QA_Linguistic",
        "02_TMDL_Operations",  # find_replace / bulk_rename / migrate_measures edit PBIP files
        "08_Documentation_Word",
        "09_Debug_Operations",  # except debug_loop fix_strategy=live_tom (refused explicitly)
        "09_Validate",  # suite files
        "10_Show_User_Guide",
        "11_PBIP_Authoring",
        "17_Desktop_Bridge",
    }
)
_XMLA_FILE_ONLY_PREFIXES = ("07_",)


def _xmla_writes_service(tool_name: str, args: Dict[str, Any]) -> bool:
    """True when this write-level call could modify the published model (N-01)."""
    operation = args.get("operation")
    if tool_name == "02_TMDL_Operations" and operation == "format_dax":
        return True  # rewrites measures through the connected (published) model
    if (
        tool_name == "09_Debug_Operations"
        and operation == "debug_loop"
        and str(args.get("fix_strategy") or "ask_user") == "live_tom"
    ):
        return True
    return not (
        tool_name in _XMLA_FILE_ONLY_TOOLS or tool_name.startswith(_XMLA_FILE_ONLY_PREFIXES)
    )


from core.validation.error_handler import ErrorHandler  # noqa: E402
from core.validation.error_enrichment import enrich_error_response  # noqa: E402
from core.validation.error_response import error_envelope, normalize_error_envelope  # noqa: E402
from core.validation.pagination_helpers import paginate  # noqa: E402
from core.validation.response_format import (  # noqa: E402
    INLINE_BUDGET_OFF_KEY,
    apply_response_format,
    concise_analysis_full,
    default_response_format,
    inline_budget_opted_out,
    normalize_response_format,
    soft_inline_budget,
)
from core.config.config_manager import config  # noqa: E402

logger = logging.getLogger(__name__)

# Common list-bearing result keys eligible for uniform opt-in pagination.
# When a caller passes page_size, the dispatcher paginates the FIRST of these
# present in the result; if none is present it paginates the LARGEST top-level
# list instead (A-18), and reports ``paginated_key: null`` when there is none.
_PAGINABLE_KEYS = [
    "measures",
    "columns",
    "tables",
    "relationships",
    "visuals",
    "rows",
    "items",
    "results",
    "objects",
    "violations",
    "findings",
]

# Keys never reported as unknown_args (protocol envelope).
_ALWAYS_ALLOWED_ARGS = frozenset({"_meta", "request_id", "full"})

# Tools that never touch the live Desktop connection (pure PBIP / disk / docs).
# They run on a separate pool so a hung Desktop cannot starve them (A-04).
OFFLINE_TOOLS = frozenset(
    {
        "00_Client_Mode",
        "02_QA_Linguistic",
        "07_PBIP_Operations",
        "07_Report_Operations",
        "07_Page_Operations",
        "07_Visual_Operations",
        "07_Bookmark_Operations",
        "07_Theme_Operations",
        "10_Show_User_Guide",
        "11_PBIP_Authoring",
    }
)

# Suggested read call to verify the outcome of a write whose result is unknown.
_VERIFY_WITH: Dict[str, Dict[str, Any]] = {
    "00_Client_Mode": {"operation": "status"},
    "02_Model_Operations": {"operation": "list"},
    "02_TMDL_Operations": {"operation": "export"},
    "02_QA_Linguistic": {"operation": "load"},
    "03_Batch_Operations": {},  # redirected by _verify_with (03 has no read op of its own)
    "07_Report_Operations": {"operation": "info"},
    "07_Page_Operations": {"operation": "list"},
    "07_Visual_Operations": {"operation": "list"},
    "07_Bookmark_Operations": {"operation": "list"},
    "07_Theme_Operations": {"operation": "get_theme"},
    "09_Debug_Operations": {"operation": "status"},
    "09_Profile": {"operation": "export"},
    "09_Validate": {"operation": "test_list"},
    "11_PBIP_Authoring": {},  # redirected by _verify_with (authoring has no read op)
    "15_DAX_Refactor": {"operation": "list_transforms"},
    "17_Desktop_Bridge": {"operation": "status"},
}

# 03_Batch_Operations `object_type` (plural) -> the 02_Model_Operations `object_type` whose
# `list` op shows the result of that batch.
_BATCH_TO_MODEL_TYPE = {
    "measures": "measure",
    "columns": "column",
    "tables": "table",
    "relationships": "relationship",
    "hierarchies": "hierarchy",
    "partitions": "partition",
    "perspectives": "perspective",
    "cultures": "culture",
    "roles": "role",
    "annotations": "annotation",
    "functions": "function",
}
# Read that shows when a model refresh last touched each partition (RefreshedTime / State).
_REFRESH_VERIFY_QUERY = "EVALUATE INFO.PARTITIONS()"

# --------------------------------------------------------------------------
# Process-wide concurrency primitives
# --------------------------------------------------------------------------

#: Serialises write/destructive handlers process-wide (re-entrant so a handler
#: that dispatches a nested write on the same thread cannot deadlock).
_WRITE_LOCK = threading.RLock()
#: How often a worker waiting for the write lock re-checks its cancel event.
_LOCK_POLL_SECONDS = 0.1

_POOL_LOCK = threading.Lock()
_POOLS: Dict[str, concurrent.futures.ThreadPoolExecutor] = {}
_POOL_SIZES = {"live": 8, "offline": 4}

_op_ids = itertools.count(1)

#: Calls whose caller gave up (timeout/cancel) but whose worker still runs.
_ORPHANS_LOCK = threading.Lock()
_ORPHANS: Dict[int, "_Call"] = {}


def _pool_for(tool_name: str) -> Tuple[str, concurrent.futures.ThreadPoolExecutor]:
    kind = "offline" if tool_name in OFFLINE_TOOLS else "live"
    with _POOL_LOCK:
        pool = _POOLS.get(kind)
        if pool is None:
            size = _POOL_SIZES[kind]
            try:
                size = int(config.get(f"server.{kind}_workers", size) or size)
            except (TypeError, ValueError):
                pass
            pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=max(1, size), thread_name_prefix=f"mcp-{kind}"
            )
            _POOLS[kind] = pool
    return kind, pool


def _args_fingerprint(tool_name: str, arguments: Dict[str, Any]) -> str:
    # ``request_id`` labels a retry of the SAME call: it must not change the fingerprint.
    arguments = {k: v for k, v in arguments.items() if k != "request_id"}
    try:
        blob = json.dumps(arguments, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001
        blob = repr(sorted(arguments.items(), key=lambda kv: str(kv[0])))
    return hashlib.sha1(f"{tool_name}|{blob}".encode("utf-8", "replace")).hexdigest()


# --------------------------------------------------------------------------
# F-27: request_id idempotency store
# --------------------------------------------------------------------------

_IDEM_MAX_ENTRIES = 256
_IDEM_MAX_ID_CHARS = 128
_IDEM_MAX_RESULT_CHARS = 200_000


class _IdempotencyStore:
    """``(tool, args fingerprint, request_id) -> outcome`` with a TTL (thread-safe)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: Dict[Tuple[str, str], Dict[str, Any]] = {}

    @staticmethod
    def ttl_s() -> float:
        try:
            return float(config.get("server.idempotency_ttl_s", 600) or 0)
        except (TypeError, ValueError):
            return 600.0

    @staticmethod
    def _generation() -> Optional[int]:
        cs = _connection_state()
        try:
            return int(cs.model_generation) if cs is not None else None
        except Exception:  # noqa: BLE001
            return None

    def _expire(self, now: float, ttl: float) -> None:
        for key in [
            k for k, e in self._entries.items() if e["state"] == "done" and now - e["at"] > ttl
        ]:
            del self._entries[key]
        while len(self._entries) > _IDEM_MAX_ENTRIES:
            oldest = min(self._entries, key=lambda k: self._entries[k]["at"])
            del self._entries[oldest]

    def begin(self, key: Tuple[str, str], op_id: int, label: str):
        """``("run", None)`` | ``("replay", entry)`` | ``("running", entry)``."""
        ttl = self.ttl_s()
        if ttl <= 0:
            return "run", None
        now = time.monotonic()
        with self._lock:
            self._expire(now, ttl)
            entry = self._entries.get(key)
            if entry is not None:
                if entry["state"] == "running":
                    return "running", dict(entry)
                failed = entry["result"].get("success") is False
                if not failed or entry["generation"] == self._generation():
                    return "replay", dict(entry)
            self._entries[key] = {
                "state": "running",
                "op_id": op_id,
                "label": label,
                "at": now,
                "result": None,
                "generation": None,
            }
        return "run", None

    def finish(self, key: Tuple[str, str], op_id: int, result: Any) -> None:
        """Record the outcome (or drop the entry when nothing was executed)."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or entry.get("op_id") != op_id:
                return
            executed = isinstance(result, dict) and not (
                result.get("executed") is False and result.get("success") is False
            )
            if not executed or not isinstance(result, dict):
                del self._entries[key]
                return
            import copy

            try:
                size = len(json.dumps(result, default=str))
            except (TypeError, ValueError):
                size = _IDEM_MAX_RESULT_CHARS + 1
            if size > _IDEM_MAX_RESULT_CHARS:
                # keep the verdict, not the payload (bounded memory for a 10-minute cache)
                result = {
                    "success": result.get("success", True),
                    **({"error_type": result["error_type"]} if result.get("error_type") else {}),
                    **({"error": result["error"]} if result.get("error") else {}),
                    "result_omitted": f"{size} chars; verify the state with verify_with",
                }
            entry.update(
                state="done",
                result=copy.deepcopy(result),
                at=time.monotonic(),
                generation=self._generation(),
            )

    def discard(self, key: Tuple[str, str], op_id: int) -> None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry.get("op_id") == op_id and entry["state"] == "running":
                del self._entries[key]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_IDEMPOTENCY = _IdempotencyStore()


def _request_id_of(arguments: Dict[str, Any]) -> Optional[str]:
    value = arguments.get("request_id") if isinstance(arguments, dict) else None
    if isinstance(value, str) and value.strip():
        return value.strip()[:_IDEM_MAX_ID_CHARS]
    return None


def _connection_state():
    try:
        from core.infrastructure.connection_state import connection_state

        return connection_state
    except Exception:  # noqa: BLE001 - CLR / import edge in tests
        return None


def _cancel_active_queries(label: str) -> None:
    """Best-effort ``connection_state.cancel_active_queries()`` off the loop."""
    cs = _connection_state()
    fn = getattr(cs, "cancel_active_queries", None) if cs is not None else None
    if not callable(fn):
        return

    def _run():
        try:
            n = fn()
            logger.warning("Cancelled %s in-flight ADOMD command(s) for %s", n, label)
        except Exception as e:  # noqa: BLE001
            logger.debug("cancel_active_queries failed for %s: %s", label, e)

    threading.Thread(target=_run, name="mcp-cancel", daemon=True).start()


def _bump_model_generation(label: str) -> None:
    cs = _connection_state()
    fn = getattr(cs, "bump_model_generation", None) if cs is not None else None
    if not callable(fn):
        return
    try:
        fn(label)
    except Exception as e:  # noqa: BLE001
        logger.debug("bump_model_generation(%s) failed: %s", label, e)


def _offline_scope(*, write: bool):
    """N-23: serialise access to the offline lane's shared TOM tree (no-op when inactive)."""
    try:
        from core.infrastructure import offline_model

        return offline_model.op_scope(write)
    except Exception as e:  # noqa: BLE001 - never fail a call over the lock helper
        logger.debug("offline scope unavailable: %s", e)
        import contextlib

        return contextlib.nullcontext()


def _journal_scope(label: str, args: Dict[str, Any]):
    """JR-01: tell the write journal which tool call the commits inside it belong to."""
    try:
        from core.operations.write_journal import call_scope

        return call_scope(label.split(":", 1)[0], label, args)
    except Exception as e:  # noqa: BLE001 - the journal must never fail a call
        logger.debug("write journal scope unavailable for %s: %s", label, e)
        import contextlib

        return contextlib.nullcontext()


def _lane_notes():
    """F-25: collect the write lane's pacing / post-write-health notes for this call."""
    try:
        from core.operations.write_lane import call_notes

        return call_notes()
    except Exception as e:  # noqa: BLE001 - never fail a call over the notes helper
        logger.debug("write lane notes unavailable: %s", e)
        import contextlib

        return contextlib.nullcontext({})


def _merge_lane_notes(result: Any, notes: Any) -> None:
    if not isinstance(notes, dict) or not notes:
        return
    try:
        from core.operations.write_lane import merge_notes

        merge_notes(result, notes)
    except Exception as e:  # noqa: BLE001
        logger.debug("write lane notes not merged: %s", e)


def _note_edit_ledger(label: str, args: Dict[str, Any]) -> None:
    """J-01/J-02 (WP11): tell the bridge's edit ledger a write succeeded.

    Live-model writes make a later bridge reload refuse until a verified save;
    PBIR/TMDL disk writes make a save refuse until a reload.
    """
    try:
        from core.autonomous.edit_ledger import note_dispatch_write

        note_dispatch_write(label, args)
    except Exception as e:  # noqa: BLE001 - the ledger must never fail a write
        logger.debug("edit ledger note failed for %s: %s", label, e)


class _Call:
    """Book-keeping for one in-flight call (shared by loop and worker)."""

    __slots__ = (
        "op_id",
        "tool",
        "label",
        "level",
        "fingerprint",
        "cancel_event",
        "state_lock",
        "claimed",
        "started",
        "finished",
        "abandoned",
        "submitted_at",
        "started_at",
        "pool_kind",
    )

    def __init__(self, tool: str, arguments: Dict[str, Any]):
        self.op_id = next(_op_ids)
        self.tool = tool
        self.label = op_label(tool, arguments)
        self.level = classify(tool, arguments)
        self.fingerprint = _args_fingerprint(tool, arguments) if is_write(self.level) else None
        self.cancel_event = threading.Event()
        self.state_lock = threading.Lock()
        self.claimed = False
        self.started = False
        self.finished = False
        self.abandoned = False
        self.submitted_at = time.monotonic()
        self.started_at: Optional[float] = None
        self.pool_kind = "live"

    def claim(self) -> bool:
        """Worker side: a worker picked the call up (False if already abandoned)."""
        with self.state_lock:
            if self.abandoned or self.cancel_event.is_set():
                return False
            self.claimed = True
            return True

    def begin(self) -> bool:
        """Worker side: the HANDLER is about to run (after the write lock for
        writes). Atomic with :meth:`abandon`, so a call is either reported
        not-executed or its handler runs — never both."""
        with self.state_lock:
            if self.abandoned or self.cancel_event.is_set():
                return False
            self.started = True
            self.started_at = time.monotonic()
            return True

    def abandon(self) -> bool:
        """Loop side: mark abandoned; returns whether the handler had started."""
        with self.state_lock:
            self.abandoned = True
            self.cancel_event.set()
            return self.started and not self.finished


def _not_executed(call: "_Call") -> Dict[str, Any]:
    return {
        "success": False,
        "error_type": "cancelled",
        "error": f"{call.label} was cancelled before it started; nothing was executed.",
        "executed": False,
    }


def _did_you_mean_args(unknown: List[str], declared: Any) -> Dict[str, str]:
    """``{unknown arg: closest declared arg}`` (difflib, plus a prefix match so a truncated name
    such as ``candidate`` finds ``candidate_expression``)."""
    names = sorted(declared)
    out: Dict[str, str] = {}
    for key in unknown:
        close = difflib.get_close_matches(key, names, n=1, cutoff=0.6)
        if not close:
            prefixed = [n for n in names if len(key) >= 4 and n.startswith(key)]
            close = prefixed[:1]
        if close:
            out[key] = close[0]
    return out


class ToolDispatcher:
    """Dispatches tool calls to registered handlers. Thread-safe call count."""

    def __init__(self):
        self.registry = get_registry()
        self._call_count = 0
        self._count_lock = threading.Lock()
        # Audit T93: cache the security flags at init; config.reload() calls
        # refresh_security_flags() through a reload listener (A-29).
        self.refresh_security_flags()
        try:
            import weakref

            ref = weakref.WeakMethod(self.refresh_security_flags)

            def _on_reload(ref=ref):
                fn = ref()
                if fn is not None:
                    fn()

            config.add_reload_listener(_on_reload)
        except Exception:  # noqa: BLE001 - config stub in tests
            pass

    def refresh_security_flags(self) -> None:
        self._read_only = bool(config.get("security.read_only_mode", False))
        self._confirm_destructive = bool(config.get("security.confirm_destructive", False))
        # F-22 / POL-01: data-vs-metadata split + per-model / per-op rules (permissive by default)
        try:
            self._policy = load_policy(config)
        except Exception as e:  # noqa: BLE001 - a broken rule block must not take the server down
            logger.error(
                "security policy could not be loaded (%s); using the permissive default", e
            )
            self._policy = SecurityPolicy()
        # Rebuild the per-tool timeout table from config on refresh too.
        self._timeout_manager = None
        try:
            self._stuck_limit = int(config.get("server.max_stuck_calls", 4) or 4)
        except (TypeError, ValueError):
            self._stuck_limit = 4

    def _get_timeout(self, tool_name: str) -> float:
        """Per-tool wall-clock timeout (seconds) for the async executor path.

        Resolved by :class:`core.config.tool_timeouts.ToolTimeoutManager` from
        the ``tool_timeouts`` config section (keyed by REGISTERED tool name).
        """
        mgr = getattr(self, "_timeout_manager", None)
        if mgr is None:
            try:
                from core.config.tool_timeouts import ToolTimeoutManager

                mgr = ToolTimeoutManager(config.get("tool_timeouts", {}) or {})
            except Exception:  # pragma: no cover - config/CLR edge
                mgr = None
            self._timeout_manager = mgr
        if mgr is None:
            return 120.0
        try:
            return float(mgr.get_timeout(tool_name, default=120))
        except Exception:  # pragma: no cover - defensive
            return 120.0

    # ------------------------------------------------------------------
    # Argument normalisation
    # ------------------------------------------------------------------

    def _normalize_arguments(
        self, tool_name: str, arguments: Optional[Dict[str, Any]]
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Copy args, fold legacy aliases (L-11); return (args, notices).

        Raises :class:`server.arg_aliases.ArgConflict` when an alias and its
        canonical name disagree.
        """
        raw: Dict[str, Any] = dict(arguments) if isinstance(arguments, dict) else {}
        raw.pop("_meta", None)
        args, notices = normalize_tool_args(tool_name, raw)
        try:
            declared = self.registry.declared_properties(tool_name)
        except Exception:  # noqa: BLE001
            declared = frozenset()
        if declared:
            unknown = sorted(k for k in args if k not in declared and k not in _ALWAYS_ALLOWED_ARGS)
            if unknown:
                notices["unknown_args"] = unknown
                hints = _did_you_mean_args(unknown, declared)
                if hints:
                    notices["did_you_mean_args"] = hints
                notices["unknown_args_note"] = (
                    "Not declared in this tool's schema; passed through to the handler, "
                    "which may ignore them. Check the spelling against the tool schema "
                    "(10_Show_User_Guide tool=<name> lists every argument)."
                )
        return args, notices

    # ------------------------------------------------------------------
    # Sync dispatch
    # ------------------------------------------------------------------

    def dispatch(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        _call: Optional[_Call] = None,
    ) -> Dict[str, Any]:
        """
        Dispatch a tool call to its handler (synchronous).

        Args:
            tool_name: Name of the tool to invoke
            arguments: Tool arguments

        Returns:
            Result dictionary (canonical envelope) from the handler
        """
        with self._count_lock:
            self._call_count += 1

        try:
            if not self.registry.has_tool(tool_name):
                logger.warning(f"Unknown tool requested: {tool_name}")
                return enrich_error_response(
                    error_envelope(
                        "unknown_tool",
                        f"Unknown tool: {tool_name}",
                        available_tools=[t.name for t in self.registry.get_all_tools()][:30],
                    ),
                    tool=tool_name,
                )

            try:
                args, notices = self._normalize_arguments(tool_name, arguments)
            except ArgConflict as conflict:
                return enrich_error_response(
                    error_envelope(
                        "conflicting_arguments",
                        f"{conflict} - pass only the canonical name.",
                        conflicts=conflict.conflicts,
                    )
                )
            level = _call.level if _call is not None else classify(tool_name, args)
            label = _call.label if _call is not None else op_label(tool_name, args)

            # F-22 / POL-01: evaluated before every other gate (a denied call never reaches them)
            blocked = self._policy_gate(tool_name, args, label) or self._security_gate(
                tool_name, args, level, label
            )
            if blocked is not None:
                blocked.update(notices)
                return enrich_error_response(
                    blocked, tool=tool_name, arguments=args, registry=self.registry
                )

            handler = self.registry.get_handler(tool_name)
            logger.debug("Dispatching tool: %s (%s)", label, level)

            if is_write(level):
                result = self._run_write(handler, args, label, _call)
            elif is_model_preview(tool_name, args):
                # F-01: a write preview mutates the model (commit suppressed, then rolled back):
                # exclusive like a write, but it is a READ (no generation bump, no ledger note).
                result = self._run_preview(handler, args, label, _call)
            elif _call is not None and not _call.begin():
                result = _not_executed(_call)
            else:
                with _offline_scope(write=False):
                    result = handler(args)

            # Ensure result is a dict
            if not isinstance(result, dict):
                logger.warning(f"Handler for {tool_name} returned non-dict: {type(result)}")
                result = {"success": True, "result": result}

            # L-13: one canonical envelope for every result.
            result = normalize_error_envelope(result)

            result = self._paginate_and_format(result, args, tool_name)

            for key, value in notices.items():
                _merge_notice(result, key, value)

            # T1-F: enrich failed results with retry_with / suggestion hints.
            return enrich_error_response(
                result, tool=tool_name, arguments=args, registry=self.registry
            )

        except rc.CallCancelled as e:
            return error_envelope("cancelled", str(e), executed="partial")
        except Exception as e:
            logger.error(f"Error dispatching tool {tool_name}: {e}", exc_info=True)
            return enrich_error_response(
                normalize_error_envelope(ErrorHandler.handle_unexpected_error(tool_name, e)),
                tool=tool_name,
                arguments=arguments if isinstance(arguments, dict) else None,
                registry=self.registry,
            )

    def _policy_gate(
        self, tool_name: str, args: Dict[str, Any], label: str
    ) -> Optional[Dict[str, Any]]:
        """F-22 / POL-01: ``security.allow_data_read`` + ``security.rules`` (no-op when unset)."""
        policy = getattr(self, "_policy", None)
        if policy is None or not policy.active:
            return None
        refused = policy.evaluate(
            tool_name,
            args,
            label,
            returns_data=(not policy.allow_data_read) and returns_data(tool_name, args),
            confirmed=args.get("confirm_destructive") is True,
        )
        if refused is not None:
            logger.warning("Security policy: blocked %s (%s)", label, refused.get("error_type"))
        return refused

    def _security_gate(
        self, tool_name: str, args: Dict[str, Any], level: str, label: str
    ) -> Optional[Dict[str, Any]]:
        """Per-OPERATION read-only / confirm gates (A-11 / L-07)."""
        if self._read_only and is_write(level):
            logger.warning("Read-only mode: blocked %s (%s)", label, level)
            return error_envelope(
                "read_only_mode",
                f"{label} is a {level} operation; the server is in read-only mode "
                "(security.read_only_mode). Read operations remain available.",
                operation=label,
                classification=level,
            )
        if is_write(level):
            # XM-01 / N-01: a read-only XMLA target refuses writes to the published model.
            from core.infrastructure import offline_model, xmla_target

            if (
                xmla_target.is_active()
                and not offline_model.is_active()
                and _xmla_writes_service(tool_name, args)
            ):
                refused = xmla_target.refuse_write(tool_name, args.get("operation"), args)
                if refused is not None:
                    logger.warning("XMLA read-only target: blocked %s", label)
                    return refused
        if (
            self._confirm_destructive
            and level == DESTRUCTIVE
            and args.get("confirm_destructive") is not True
        ):
            logger.info("Confirmation gate: blocked %s", label)
            return error_envelope(
                "confirmation_required",
                f"{label} is destructive (security.confirm_destructive is on). "
                "Re-call with confirm_destructive=true to proceed.",
                tool=tool_name,
                operation=label,
                classification=level,
                args_received={k: _redact(v) for k, v in args.items()},
            )
        return None

    def _run_write(self, handler, args: Dict[str, Any], label: str, call: Optional[_Call]):
        """Run a write under the process-wide write lock; bump the model generation."""
        acquired = False
        try:
            while not acquired:
                acquired = _WRITE_LOCK.acquire(timeout=_LOCK_POLL_SECONDS)
                if not acquired and call is not None and call.cancel_event.is_set():
                    return _not_executed(call)
            if call is not None and not call.begin():
                # The caller gave up while we queued for the lock: never execute.
                return _not_executed(call)
            try:
                with _journal_scope(label, args), _offline_scope(
                    write=True
                ), _lane_notes() as notes:
                    result = handler(args)
                _merge_lane_notes(result, notes)
            except Exception as e:  # noqa: BLE001 - typed refusal for an unacquired offline scope
                if getattr(e, "error_type", None) != "busy":
                    raise
                return error_envelope("busy", str(e), executed=False, retry_after=5)
        finally:
            if acquired:
                _WRITE_LOCK.release()
        if not (isinstance(result, dict) and result.get("success") is False):
            _bump_model_generation(label)
            _note_edit_ledger(label, args)
        return result

    def _run_preview(self, handler, args: Dict[str, Any], label: str, call: Optional[_Call]):
        """F-01: run a model write PREVIEW exclusively (process-wide write lock + the offline
        lane's write scope) without the post-write bookkeeping: nothing is saved, so the model
        generation is not bumped and the bridge's edit ledger is not told about a write."""
        acquired = False
        try:
            while not acquired:
                acquired = _WRITE_LOCK.acquire(timeout=_LOCK_POLL_SECONDS)
                if not acquired and call is not None and call.cancel_event.is_set():
                    return _not_executed(call)
            if call is not None and not call.begin():
                return _not_executed(call)
            try:
                with _offline_scope(write=True):
                    return handler(args)
            except Exception as e:  # noqa: BLE001 - typed refusal for an unacquired offline scope
                if getattr(e, "error_type", None) != "busy":
                    raise
                return error_envelope("busy", str(e), executed=False, retry_after=5)
        finally:
            if acquired:
                _WRITE_LOCK.release()

    def _paginate_and_format(
        self, result: Dict[str, Any], args: Dict[str, Any], tool_name: str = ""
    ) -> Dict[str, Any]:
        """Opt-in pagination, then response_format (A-18 ordering).

        F-34: a list-heavy op the caller gave no ``response_format`` defaults to ``concise``; a
        caller who asked for the whole result (``page_size`` / ``response_format=detailed`` +
        ``full=true``) is marked so the renderer skips the soft inline budget.
        """
        paginated_key: Optional[str] = None
        page_size = args.get("page_size")
        if page_size is not None and result.get("success") is not False:
            try:
                page_size_int: Optional[int] = int(page_size)
            except (TypeError, ValueError):
                page_size_int = None
            if page_size_int is not None:
                keys = self._pagination_keys(result, args.get("next_token"))
                if keys:
                    result = paginate(
                        result,
                        page_size=page_size_int,
                        next_token=args.get("next_token"),
                        list_keys=keys,
                    )
                    if result.get("success") is not False:
                        token = result.get("next_token")
                        paginated_key = (
                            token.split(":", 1)[0]
                            if isinstance(token, str) and ":" in token
                            else next((k for k in keys if k in result), None)
                        )
                        result["paginated_key"] = paginated_key
                else:
                    result["paginated_key"] = None
                    result["_pagination_note"] = (
                        "page_size ignored: the result has no top-level list to paginate."
                    )

        # Uniform response_format control. 'concise' never caps the list the
        # caller explicitly sized with page_size.
        keep = {paginated_key} if paginated_key else None
        requested = args.get("response_format") or default_response_format(tool_name, args)
        if (
            tool_name == "06_Analysis_Operations"
            and normalize_response_format(requested) == "concise"
            and isinstance(result, dict)
            and result.get("success") is not False
        ):
            result = concise_analysis_full(result)
        result = apply_response_format(result, requested, keep_full_keys=keep)
        if isinstance(result, dict) and inline_budget_opted_out(args):
            result[INLINE_BUDGET_OFF_KEY] = True
        return result

    @staticmethod
    def _pagination_keys(result: Dict[str, Any], next_token: Any) -> List[str]:
        if isinstance(next_token, str) and ":" in next_token:
            return _PAGINABLE_KEYS + [
                k for k in [next_token.split(":", 1)[0]] if k not in _PAGINABLE_KEYS
            ]
        present = [k for k in _PAGINABLE_KEYS if isinstance(result.get(k), list)]
        if present:
            return present
        lists = [
            (len(v), k)
            for k, v in result.items()
            if isinstance(v, list) and not str(k).startswith("_")
        ]
        if not lists:
            return []
        lists.sort(reverse=True)
        return [lists[0][1]]

    # ------------------------------------------------------------------
    # Async dispatch
    # ------------------------------------------------------------------

    def _refuse_if_saturated(
        self, call: _Call, arguments: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Short-circuit new calls while orphaned calls pile up (A-03 / A-04)."""
        with _ORPHANS_LOCK:
            orphans = [c for c in _ORPHANS.values() if not c.finished]
            for c in list(_ORPHANS.values()):
                if c.finished:
                    _ORPHANS.pop(c.op_id, None)
        if call.fingerprint is not None:
            for c in orphans:
                if c.fingerprint == call.fingerprint:
                    return error_envelope(
                        "write_in_progress",
                        f"An identical {c.label} (operation_id {c.op_id}) timed out earlier and "
                        "is STILL RUNNING. Re-issuing it would apply the write twice. Wait, "
                        "then verify the current state before deciding to retry.",
                        operation_id=c.op_id,
                        may_still_be_running=True,
                        verify_with=self._verify_with(call.tool, arguments),
                    )
        same_pool = [c for c in orphans if c.pool_kind == call.pool_kind]
        if len(same_pool) >= self._stuck_limit:
            return error_envelope(
                "desktop_unresponsive" if call.pool_kind == "live" else "server_busy",
                f"{len(same_pool)} earlier call(s) timed out and are still blocked "
                f"({', '.join(sorted({c.label for c in same_pool}))}). New "
                f"{'live-model' if call.pool_kind == 'live' else 'offline'} calls are refused "
                "until they return, instead of queueing into another timeout. Check that "
                "Power BI Desktop is responsive (not mid-refresh / modal dialog).",
                stuck_calls=[{"operation_id": c.op_id, "operation": c.label} for c in same_pool],
            )
        return None

    @staticmethod
    def _verify_with(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """A VALID read call that shows the state a timed-out write may have changed.

        The op is chosen per tool (and per ``object_type`` for the model tools): a
        ``list`` op does not exist for every object type (``model`` has none;
        ``03_Batch_Operations`` / ``11_PBIP_Authoring`` have no read op at all), so
        those point at a read that does. Pinned by ``tests/test_verify_with.py``:
        every suggestion names an operation the target tool accepts.
        """
        model_type = arguments.get("object_type") if isinstance(arguments, dict) else None
        if tool_name == "03_Batch_Operations":
            batch_type = model_type if isinstance(model_type, str) else arguments.get("operation")
            return ToolDispatcher._verify_with(
                "02_Model_Operations",
                {
                    **{k: v for k, v in arguments.items() if k in ("table_name", "pbip_path")},
                    "object_type": _BATCH_TO_MODEL_TYPE.get(batch_type, "table"),
                },
            )
        if tool_name == "11_PBIP_Authoring":
            return ToolDispatcher._verify_with(
                "07_Page_Operations",
                {k: v for k, v in arguments.items() if k == "pbip_path"},
            )
        if tool_name == "02_Model_Operations":
            from server.handlers.model_operations_handler import _valid_operations

            if model_type == "model":
                return {
                    "tool": "04_Run_DAX",
                    "args": {"query": _REFRESH_VERIFY_QUERY},
                    "why": "partition State / RefreshedTime show whether the refresh ran "
                    "(or 02_Model_Operations object_type=model operation=get_properties)",
                }
            if not (
                isinstance(model_type, str) and "list" in _valid_operations.get(model_type, ())
            ):
                # no `list` op for this object type (e.g. field_parameter): show the tables
                arguments = {**arguments, "object_type": "table"}
        args = dict(_VERIFY_WITH.get(tool_name, {}))
        for key in ("object_type", "pbip_path", "table_name", "page_name", "page"):
            if key in arguments and isinstance(arguments[key], str):
                args.setdefault(key, arguments[key])
        return {"tool": tool_name, "args": args}

    def _timeout_envelope(
        self, call: _Call, timeout: float, started: bool, arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        if not started:
            return error_envelope(
                "timeout",
                f"{call.label} waited {timeout:.0f}s for a free worker / the write lock and "
                "was NOT executed (nothing changed). It is safe to retry; if this repeats, "
                "earlier calls are still blocked.",
                executed=False,
                operation_id=call.op_id,
            )
        if is_write(call.level):
            return error_envelope(
                "timeout_outcome_unknown",
                f"{call.label} exceeded its {timeout:.0f}s timeout while running. It is a "
                f"{call.level} operation and MAY STILL BE RUNNING (or have completed) — the "
                "outcome is unknown. Do NOT retry blindly: verify the current state first "
                "(see verify_with); an identical re-issue is refused while it still runs.",
                may_still_be_running=True,
                operation_id=call.op_id,
                verify_with=self._verify_with(call.tool, arguments),
                request_id=_request_id_of(arguments),
                safe_retry=(
                    "Re-send the SAME call with the SAME request_id: it returns the recorded "
                    "outcome (or write_in_progress) instead of executing twice."
                    if _request_id_of(arguments)
                    else "Pass request_id=<unique string> on write calls to make a retry safe."
                ),
            )
        return error_envelope(
            "timeout",
            f"{call.label} exceeded its {timeout:.0f}s timeout. Power BI Desktop may be busy "
            "or unresponsive. Verify the connection (01_Connection operation='ensure') and "
            "retry; if it persists, check that Desktop isn't mid-refresh or frozen.",
            executed="partial",
            operation_id=call.op_id,
            retry_with={"tool": "01_Connection", "args": {"operation": "ensure"}},
        )

    def _abandon(self, call: _Call) -> bool:
        """Caller side gave up: signal, cancel queries, track the orphan."""
        started = call.abandon()
        if started:
            with _ORPHANS_LOCK:
                _ORPHANS[call.op_id] = call
            if call.pool_kind == "live":
                _cancel_active_queries(call.label)
        return started

    async def _submit(self, tool_name: str, arguments: Dict[str, Any], render: bool):
        """Run dispatch (+ optional render) on the worker pool; returns (result, rendered)."""
        loop = asyncio.get_running_loop()
        rc.set_server_loop(loop)
        norm_args = arguments if isinstance(arguments, dict) else {}
        # Classify on the canonical spelling (an aliased op/arg must gate the same).
        try:
            cls_args, _ = normalize_tool_args(tool_name, norm_args)
        except ArgConflict:
            cls_args = norm_args
        call = _Call(tool_name, cls_args)
        call.pool_kind, pool = _pool_for(tool_name)

        # F-27: a write carrying request_id is replayed from its recorded outcome.
        idem_key: Optional[Tuple[str, str]] = None
        request_id = _request_id_of(norm_args)
        if request_id is not None and call.fingerprint is not None:
            idem_key = (call.fingerprint, request_id)
            verdict, entry = _IDEMPOTENCY.begin(idem_key, call.op_id, call.label)
            if verdict == "replay":
                return self._finish(
                    tool_name, self._replayed(entry, request_id, call, norm_args), render
                )
            if verdict == "running":
                return self._finish(
                    tool_name,
                    error_envelope(
                        "write_in_progress",
                        f"{entry['label']} with request_id {request_id!r} is still running "
                        f"(operation_id {entry['op_id']}). Re-send the same call (same request_id) "
                        "later to get its recorded outcome; do not change the request_id.",
                        operation_id=entry["op_id"],
                        request_id=request_id,
                        may_still_be_running=True,
                        verify_with=self._verify_with(call.tool, norm_args),
                    ),
                    render,
                )

        refused = self._refuse_if_saturated(call, norm_args)
        if refused is not None:
            if idem_key is not None:
                _IDEMPOTENCY.discard(idem_key, call.op_id)
            return self._finish(tool_name, refused, render)

        ctx = contextvars.copy_context()
        ctx.run(rc.SERVER_LOOP.set, loop)
        ctx.run(rc.CANCEL_EVENT.set, call.cancel_event)
        ctx.run(rc.CALL_LABEL.set, call.label)

        def _work():
            if not call.claim():
                if idem_key is not None:
                    _IDEMPOTENCY.discard(idem_key, call.op_id)
                return _not_executed(call), None
            result = None
            try:
                # B-19: publish the ADOMD budget so CommandTimeout fires before the
                # dispatcher timeout and the worker releases the connection gate.
                try:
                    from core.infrastructure.adomd_utils import command_budget
                except Exception:  # pragma: no cover - import edge
                    command_budget = None
                if command_budget is not None:
                    with command_budget(max(1.0, timeout - 5.0)):
                        result = self.dispatch(tool_name, norm_args, _call=call)
                else:
                    result = self.dispatch(tool_name, norm_args, _call=call)
                if idem_key is not None:
                    # recorded even when the caller already timed out (the retry reads it)
                    _IDEMPOTENCY.finish(idem_key, call.op_id, result)
                rendered = self._render(tool_name, result) if render else None
                return result, rendered
            finally:
                if idem_key is not None and result is None:
                    _IDEMPOTENCY.discard(idem_key, call.op_id)
                call.finished = True

        timeout = self._get_timeout(tool_name)
        fut = loop.run_in_executor(pool, ctx.run, _work)
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            started = self._abandon(call)
            logger.error(
                "Tool %s exceeded its %.0fs timeout (started=%s, level=%s)",
                call.label,
                timeout,
                started,
                call.level,
            )
            envelope = enrich_error_response(
                self._timeout_envelope(call, timeout, started, norm_args)
            )
            if idem_key is not None and not started:
                _IDEMPOTENCY.discard(idem_key, call.op_id)
            return self._finish(tool_name, envelope, render)
        except asyncio.CancelledError:
            started = self._abandon(call)
            logger.warning(
                "Tool %s cancelled by the client (started=%s, level=%s)",
                call.label,
                started,
                call.level,
            )
            raise

    @staticmethod
    def _replayed(
        entry: Dict[str, Any], request_id: str, call: _Call, arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        """The recorded outcome of an earlier identical write, flagged as a replay."""
        import copy

        result = copy.deepcopy(entry["result"])
        result["replayed"] = True
        result["request_id"] = request_id
        result["recorded_age_s"] = round(time.monotonic() - entry["at"], 1)
        result["replay_note"] = (
            f"{call.label} was NOT executed again: this is the recorded outcome of the earlier "
            f"call with the same request_id (operation_id {entry['op_id']})."
        )
        return result

    def _finish(self, tool_name: str, result: Dict[str, Any], render: bool):
        return result, (self._render(tool_name, result) if render else None)

    def _inline_cap(self, tool_name: str) -> int:
        from server.response_pipeline import INLINE_MAX_CHARS

        if tool_name not in HIGH_TOKEN_TOOLS:
            return INLINE_MAX_CHARS
        try:
            from core.infrastructure.limits_manager import get_limits

            lim = get_limits().token
            return min(INLINE_MAX_CHARS, int(lim.max_result_tokens) * int(lim.chars_per_token))
        except Exception:  # noqa: BLE001 - limits manager not initialised (tests)
            return 100_000

    def _render(self, tool_name: str, result: Dict[str, Any]):
        from server.response_pipeline import render_result

        return render_result(
            tool_name,
            result,
            inline_max_chars=self._inline_cap(tool_name),
            soft_budget_chars=soft_inline_budget(),
        )

    async def dispatch_async(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Dispatch a tool call without blocking the async event loop.

        Runs the synchronous handler on the dispatcher's bounded worker pool
        inside a copied context (see module docstring).
        """
        result, _ = await self._submit(tool_name, arguments, render=False)
        return result

    async def dispatch_and_render_async(self, tool_name: str, arguments: Dict[str, Any]):
        """Like :meth:`dispatch_async` but also renders the JSON response on
        the worker thread. Returns ``(result_dict, RenderedResult)``."""
        return await self._submit(tool_name, arguments, render=True)

    def get_stats(self) -> Dict[str, Any]:
        """Get dispatcher statistics"""
        with _ORPHANS_LOCK:
            stuck = [
                {"operation_id": c.op_id, "operation": c.label}
                for c in _ORPHANS.values()
                if not c.finished
            ]
        return {
            "total_calls": self._call_count,
            "registered_tools": len(self.registry.get_all_tools()),
            "categories": self.registry.list_categories(),
            "stuck_calls": stuck,
        }


def _merge_notice(result: Dict[str, Any], key: str, value: Any) -> None:
    """Attach a dispatcher notice without clobbering the handler's own one."""
    existing = result.get(key)
    if existing is None:
        result[key] = value
    elif isinstance(existing, dict) and isinstance(value, dict):
        for k, v in value.items():
            existing.setdefault(k, v)
    elif isinstance(existing, list) and isinstance(value, dict):
        existing.extend(f"{k}: {v}" for k, v in value.items())


def _redact(v: Any) -> Any:
    """P2-18: redact large/complex values before echoing args back."""
    if v is None or isinstance(v, (bool, int, float)):
        return v
    if isinstance(v, str):
        return v if len(v) <= 80 else f"<redacted:str[{len(v)}]>"
    if isinstance(v, (list, tuple, set)):
        return f"<redacted:{type(v).__name__}[{len(v)}]>"
    if isinstance(v, dict):
        return f"<redacted:dict[{len(v)}]>"
    return f"<redacted:{type(v).__name__}>"
