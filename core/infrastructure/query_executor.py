"""
Optimized Query Executor for PBIXRay MCP Server

Enhanced DAX query execution with:
- Better error handling and suggestions
- Table reference support
- Query caching
- DAX-level filtering
- Comprehensive error analysis

Based on fabric-toolbox best practices.
"""

import re
import time
import hashlib
import logging
import threading
from typing import Any, Dict, List, Optional, Tuple
from collections import OrderedDict
from xml.sax.saxutils import escape
from core.dax.dax_validator import DaxValidator
from core.config.config_manager import config
from core.validation.constants import QueryLimits
from core.infrastructure.adomd_utils import (
    ConnectionBusy,
    apply_command_timeout,
    busy_error,
    engine_message,
    is_cancellation,
    is_connection_failure,
    iter_reader_rows,
    read_rows,
    reader_columns,
)
from core.infrastructure.executor_catalog import ExecutorCatalogMixin
from core.infrastructure.executor_tom import ExecutorTomMixin

logger = logging.getLogger(__name__)

#: Results larger than this are not memoised (B-28): a 10k-row x 20-column
#: result held 200 times was an unbounded memory sink, and big results are the
#: ones a caller least wants served stale.
CACHE_MAX_ROWS_PER_ENTRY = 1000
#: Rough upper bound of cached cells across all entries (B-28 byte bound proxy).
CACHE_MAX_TOTAL_CELLS = 400_000
#: Default ADOMD CommandTimeout. MUST stay below the dispatcher's 60 s budget for
#: 04_Run_DAX so the engine-side timeout fires first and the worker releases the
#: connection (B-19). A dispatcher-published budget (adomd_utils.command_budget)
#: overrides it per call.
DEFAULT_COMMAND_TIMEOUT_S = 55

# DMV Column Type Constants (INFO.COLUMNS()[Type] field)
# These are numeric values, not text
COLUMN_TYPE_DATA = 1  # Regular data column from source
COLUMN_TYPE_CALCULATED = 2  # Calculated column with DAX expression
COLUMN_TYPE_HIERARCHY = 3  # Hierarchy

# Try to load ADOMD.NET
ADOMD_AVAILABLE = False
AdomdConnection: Any = None
AdomdCommand: Any = None

try:
    from core.infrastructure.dll_paths import load_adomd_assembly, load_amo_assemblies

    if load_adomd_assembly():
        from Microsoft.AnalysisServices.AdomdClient import AdomdConnection, AdomdCommand  # type: ignore  # noqa: F401

        ADOMD_AVAILABLE = True
        logger.info("ADOMD.NET loaded successfully")
except Exception as e:
    logger.warning(f"ADOMD.NET not available: {e}")

# Try to load AMO/TOM assemblies early so helpers can use them
AMO_AVAILABLE = False
try:
    if load_amo_assemblies():
        from Microsoft.AnalysisServices.Tabular import Server as _AMOServer  # type: ignore  # noqa: F401

        AMO_AVAILABLE = True
except Exception as _e:
    logger.warning(f"AMO/TOM not available: {_e}")


class OptimizedQueryExecutor(ExecutorTomMixin, ExecutorCatalogMixin):
    """
    Optimized query executor with enhanced error handling and DAX query optimization.

    Features:
    - Intelligent table reference handling
    - Comprehensive error analysis with suggestions
    - Query result caching
    - DAX-level filtering for performance
    - Safe execution with multiple fallback strategies
    """

    def __init__(self, connection):
        """
        Initialize the query executor.

        Args:
            connection: Active ADOMD connection (initial reference, use get_connection() for current)
        """
        self._initial_connection = connection  # Keep initial reference for backward compatibility
        self._connection_manager = (
            None  # Will hold reference to ConnectionManager for dynamic connection
        )
        # TTL-based LRU cache for query results (the single live DAX result cache).
        # Key = (connection identity, model generation, prepared query, top_n,
        # row cap) — B-02: without the identity + generation a model switch or a
        # TOM write kept serving the previous model's rows for the whole TTL.
        # Audit T84: callers run from a thread pool, so every mutation is locked.
        self.query_cache: "OrderedDict[Tuple[Any, ...], Dict[str, Any]]" = OrderedDict()
        self._query_cache_lock = threading.Lock()
        self.max_cache_items = getattr(QueryLimits, "TELEMETRY_BUFFER_SIZE", 200)
        self.cache_ttl_seconds = max(0, int(config.get("performance.cache_ttl_seconds", 300) or 0))
        self._table_cache = None
        # Deprecated: Local table mapping cache (use connection_state shared cache instead)
        self._table_id_by_name: Optional[Dict[str, Any]] = None
        self._table_name_by_id: Optional[Dict[Any, str]] = None
        self._column_name_by_id: Optional[Dict[Any, str]] = None
        # Connection state reference for shared table mapping cache
        self._connection_state = None
        # Command timeout (seconds) for ADOMD command execution.
        # `or DEFAULT` is WRONG here — it replaces 0 (caller-intended disable)
        # with the default. Use `if val is not None`.
        try:
            _ct_raw = config.get("performance.command_timeout_seconds", DEFAULT_COMMAND_TIMEOUT_S)
            self.command_timeout_seconds = (
                int(_ct_raw) if _ct_raw is not None else DEFAULT_COMMAND_TIMEOUT_S
            )
        except Exception:
            self.command_timeout_seconds = DEFAULT_COMMAND_TIMEOUT_S
        # Optional history logger callback: callable(dict)
        self._history_logger = None
        # Cache stats — counters are mutated from the dispatcher's thread pool,
        # so plain `+=` races. Guard with a dedicated cheap lock.
        self.cache_hits = 0
        self.cache_misses = 0
        self.cache_bypass = 0
        self._counter_lock = threading.Lock()
        # Database-name cache per connection object (B-01: every AMO acquisition
        # used to run a DBSCHEMA_CATALOGS round-trip on the shared connection).
        self._db_name_cache: Tuple[Optional[int], Optional[str]] = (None, None)
        self._last_connection_check = 0
        self._connection_healthy = False
        self._health_lock = threading.Lock()

    @property
    def connection(self):
        """
        Get current active connection.

        Returns connection from ConnectionManager if available (always fresh),
        otherwise falls back to initial connection reference.
        """
        # Try to get current connection from connection manager (always fresh)
        if self._connection_manager:
            current_conn = self._connection_manager.get_connection()
            if current_conn:
                return current_conn

        # Fallback to initial connection for backward compatibility
        return self._initial_connection

    def get_connection(self):
        """
        Explicitly get the current active connection.

        Returns:
            Active ADOMD connection object
        """
        return self.connection

    def invalidate_connection_cache(self):
        """Reset the (legacy) connection-health cache; kept for callers of the old API."""
        self.invalidate_tmsl_memo()
        with self._health_lock:
            self._last_connection_check = 0
            self._connection_healthy = False

    def invalidate_id_maps(self) -> None:
        """Drop the local table/column ID<->name maps (B-05).

        Plain attribute assignment (no ``_mapping_lock``): this runs from the
        connection-swap listener, which can fire on a thread that is already
        inside ``_ensure_column_mappings`` holding that non-reentrant lock.
        """
        self._table_id_by_name = None
        self._table_name_by_id = None
        self._column_name_by_id = None
        self._db_name_cache = (None, None)

    # --------------------
    # Command gate plumbing (B-01)
    # --------------------
    def _gate(self):
        """The CommandGate of the shared connection (None when unmanaged/tests)."""
        from core.infrastructure.adomd_utils import CommandGate

        cm = getattr(self, "_connection_manager", None)
        gate = getattr(cm, "gate", None) if cm is not None else None
        return gate if isinstance(gate, CommandGate) else None

    def _hold_gate(self, purpose: str, timeout: Optional[float] = None):
        from contextlib import nullcontext

        gate = self._gate()
        if gate is None:
            return nullcontext()
        return gate.hold(timeout=timeout, purpose=purpose)

    def _track(self, cmd: Any):
        from contextlib import nullcontext

        gate = self._gate()
        if gate is None:
            return nullcontext(cmd)
        return gate.track(cmd)

    def _reconnect_for_retry(self) -> bool:
        """Reconnect the shared connection after a transport failure (read retry)."""
        cm = getattr(self, "_connection_manager", None)
        if cm is None or not hasattr(cm, "reconnect"):
            return False
        try:
            result = cm.reconnect(max_retries=2, retry_delay=0.25, reason="stream_recovery")
        except TypeError:
            result = cm.reconnect(max_retries=2, retry_delay=0.25)
        except Exception as e:  # noqa: BLE001 — CLR interop
            logger.warning("Reconnect after connection failure failed: %s", e)
            return False
        ok = bool(isinstance(result, dict) and result.get("success"))
        if ok:
            logger.warning("Recovered the Power BI connection after a transport failure")
        return ok

    def _run_command(
        self,
        command_text: str,
        *,
        max_rows: Optional[int],
        purpose: str,
        retry_read: bool = True,
        timeout_s: Optional[float] = None,
        gate_timeout: Optional[float] = None,
    ) -> Tuple[List[str], List[Dict[str, Any]], float, bool, bool]:
        """Execute one READ command under the gate.

        Returns ``(columns, rows, execution_ms, truncated, retried)``. On a
        transport/stream failure ("Root element is missing", "BufferedStream",
        connection closed) the connection is rebuilt INSIDE the gate and the
        read is retried once — reads are idempotent; writes never come here.
        """
        retried = False
        with self._hold_gate(purpose, timeout=gate_timeout):
            attempts = 2 if retry_read else 1
            for attempt in range(attempts):
                conn = self.connection
                if not conn:
                    raise RuntimeError("No active connection available")
                if attempt == 0 and retry_read:
                    try:
                        state = str(conn.State.ToString())
                    except Exception:  # noqa: BLE001 — fakes / disposed object
                        state = "Open"
                    if state == "Closed" or state == "Broken":
                        if self._reconnect_for_retry():
                            retried = True
                            conn = self.connection
                cmd = AdomdCommand(command_text, conn)  # type: ignore[misc]
                apply_command_timeout(
                    cmd, timeout_s if timeout_s is not None else self._command_timeout()
                )
                try:
                    with self._track(cmd):
                        columns, rows, ms, truncated = self._read_command(cmd, max_rows)
                    return columns, rows, ms, truncated, retried
                except Exception as exc:  # noqa: BLE001
                    if (
                        attempt + 1 < attempts
                        and is_connection_failure(exc)
                        and not is_cancellation(exc)
                        and self._reconnect_for_retry()
                    ):
                        retried = True
                        continue
                    raise
        raise RuntimeError("unreachable")  # pragma: no cover

    def _command_timeout(self) -> int:
        return int(getattr(self, "command_timeout_seconds", DEFAULT_COMMAND_TIMEOUT_S))

    def probe_health(self, timeout_s: float = 5.0) -> Dict[str, Any]:
        """Server round-trip health check that never touches the result cache (B-03).

        Short CommandTimeout and gate wait, and NO auto-reconnect — callers
        (01_Connection ensure/reconnect) decide what to do on failure.
        """
        if not ADOMD_AVAILABLE:
            return {
                "success": False,
                "error": "ADOMD.NET not available",
                "error_type": "adomd_not_available",
            }
        started = time.time()
        try:
            _cols, rows, _ms, _t, _r = self._run_command(
                "SELECT [CATALOG_NAME] FROM $SYSTEM.DBSCHEMA_CATALOGS",
                max_rows=5,
                purpose="health_probe",
                retry_read=False,
                timeout_s=timeout_s,
                gate_timeout=timeout_s,
            )
        except ConnectionBusy as busy:
            return busy_error(busy)
        except Exception as e:  # noqa: BLE001
            return {
                "success": False,
                "error": engine_message(e),
                "error_type": "connection_not_open" if is_connection_failure(e) else "probe_failed",
            }
        return {
            "success": True,
            "latency_ms": round((time.time() - started) * 1000, 2),
            "catalogs": [next(iter(r.values()), None) for r in rows],
        }

    def get_model_version_fingerprint(self) -> Optional[tuple]:
        """Cheap model-version fingerprint for external-change detection (B-02).

        Combines MDSCHEMA_CUBES (LAST_SCHEMA_UPDATE, LAST_DATA_UPDATE — schema
        edits and data refreshes) and TMSCHEMA_MODEL (ModifiedTime,
        StructureModifiedTime). Values go through ``clr_to_py`` so DateTimes
        compare as ISO strings, not culture strings. Returns None when neither
        DMV answers (cached results then cannot be validated).
        """
        parts: List[Any] = []
        for dmv in (
            "SELECT [CUBE_NAME],[LAST_SCHEMA_UPDATE],[LAST_DATA_UPDATE] FROM $SYSTEM.MDSCHEMA_CUBES",
            "SELECT [ModifiedTime],[StructureModifiedTime] FROM $SYSTEM.TMSCHEMA_MODEL",
        ):
            try:
                _cols, rows, _ms, _t, _r = self._run_command(
                    dmv,
                    max_rows=100,
                    purpose="model_version_probe",
                    retry_read=False,
                    timeout_s=5,
                    gate_timeout=5,
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("model version DMV failed (%s): %s", dmv[:40], e)
                continue
            parts.append((dmv[:24], tuple(tuple(sorted(r.items())) for r in rows)))
        return tuple(parts) if parts else None

    def set_connection_state(self, connection_state):
        """
        Set reference to connection state for shared table mapping cache.

        Args:
            connection_state: ConnectionState instance with shared table mappings
        """
        self._connection_state = connection_state
        # Also store connection manager reference for dynamic connection retrieval
        if connection_state and hasattr(connection_state, "connection_manager"):
            self._connection_manager = connection_state.connection_manager
            logger.debug(
                "Query executor linked to connection manager for dynamic connection retrieval"
            )
        logger.debug("Query executor linked to connection state for shared table mapping cache")

    # --------------------
    # AMO/TOM helper methods
    # --------------------
    def _get_database_name(self) -> Optional[str]:
        """Resolve the current database name via ADOMD catalogs DMV."""
        # B-01: gated, and cached per connection object — every AMO acquisition
        # used to run this DMV on the shared connection, racing live queries.
        try:
            if not AdomdCommand:
                return None
            current_conn = self.connection
            if current_conn is None:
                return None
            cached_id, cached_name = getattr(self, "_db_name_cache", (None, None))
            if cached_id == id(current_conn) and cached_name:
                return cached_name
            _cols, rows, _ms, _t, _r = self._run_command(
                "SELECT [CATALOG_NAME] FROM $SYSTEM.DBSCHEMA_CATALOGS",
                max_rows=1,
                purpose="catalog_name",
            )
            db_name = None
            if rows:
                first = next(iter(rows[0].values()), None)
                db_name = str(first) if first is not None else None
            if db_name:
                self._db_name_cache = (id(self.connection), db_name)
            return db_name
        except Exception as e:
            logger.debug(f"_get_database_name failed: {e}")
            return None

    def _amo_session(self):
        """Context manager: ``with self._amo_session() as (server, db):``.

        Guaranteed disconnect on every exit path (success, exception, early
        return inside the ``with``). New code should prefer this over the bare
        ``_connect_amo_server_db()`` + inline try/finally pattern — each
        pattern is one line, but 9 existing call sites = 9 chances to forget
        the finally. Yields ``(None, None)`` when AMO is unavailable so
        callers can early-exit the same way they did before.

        Existing callers keep working unchanged; migration is opportunistic.
        """
        from contextlib import contextmanager

        @contextmanager
        def _mgr():
            server, db = self._connect_amo_server_db()
            try:
                yield (server, db)
            finally:
                if server is not None:
                    try:
                        server.Disconnect()
                    except Exception:  # noqa: BLE001 — disconnect must never raise
                        pass

        return _mgr()

    def _connect_amo_server_db(self):
        """Return (server, database) using AMO/TOM or (None, None) if unavailable.

        Prefer ``self._amo_session()`` (context manager) for new call sites —
        guarantees disconnect on every path. This raw form remains available
        for the 9 existing call sites whose try/finally blocks already handle
        disconnect; migrating them is a separate (safe, mechanical) pass.
        """
        if not AMO_AVAILABLE:
            return None, None
        srv = None
        try:
            from Microsoft.AnalysisServices.Tabular import Server as AMOServer  # type: ignore

            srv = AMOServer()
            from core.infrastructure.xmla_target import prepare_amo_server

            prepare_amo_server(srv)
            # Reuse ADOMD connection string when possible
            conn_str = getattr(self.connection, "ConnectionString", None)
            if not conn_str:
                return None, None
            srv.Connect(conn_str)
            # Use current DB name if available
            db_name = self._get_database_name()
            db = None
            if db_name and hasattr(srv, "Databases"):
                try:
                    db = srv.Databases.GetByName(db_name)
                except Exception:
                    db = srv.Databases[0] if srv.Databases.Count > 0 else None
            else:
                db = srv.Databases[0] if srv.Databases.Count > 0 else None
            if not db:
                try:
                    srv.Disconnect()
                except Exception:
                    pass
                return None, None
            return srv, db
        except Exception as e:
            # Disconnect the AMO server on any failure so we don't leak a
            # connected handle per failed call.
            if srv is not None:
                try:
                    srv.Disconnect()
                except Exception:
                    pass
            logger.debug(f"_connect_amo_server_db failed: {e}")
            return None, None

    def set_history_logger(self, logger_cb) -> None:
        """Register a callback to receive execution history events.

        logger_cb will be invoked with a dict containing keys like
        { 'query': str, 'final_query': str, 'top_n': int, 'success': bool,
          'row_count': int, 'execution_time_ms': float, 'error': str|None, 'cached': bool }
        """
        self._history_logger = logger_cb

    # Lock guards the *local* fallback table-mapping cache. The shared
    # connection_state cache has its own lock; the local cache lives on
    # this QueryExecutor instance and is mutated from the dispatcher's
    # thread pool when connection_state is unavailable.
    @property
    def _mapping_lock(self) -> threading.Lock:
        lock = self.__dict__.get("_mapping_lock_instance")
        if lock is None:
            lock = threading.Lock()
            self.__dict__["_mapping_lock_instance"] = lock
        return lock

    def _ensure_table_mappings(self) -> None:
        """
        Load table ID<->name mappings once for fast lookups.
        Uses shared cache from connection_state if available (recommended),
        or falls back to local cache for backwards compatibility.
        """
        try:
            # Use shared cache from connection_state if available (PERFORMANCE OPTIMIZATION)
            if self._connection_state:
                id_by_name, name_by_id = self._connection_state.get_table_mappings()
                if id_by_name and name_by_id:
                    with self._mapping_lock:
                        self._table_id_by_name = id_by_name
                        self._table_name_by_id = name_by_id
                    return
                # P1-9 fix: previously returned immediately on shared-cache miss,
                # which permanently disabled table mappings until reconnect when
                # INFO.TABLES() had a transient failure during connection_state
                # bootstrap. Fall through to the legacy one-shot query below so
                # a transient failure can still be recovered.
                logger.warning(
                    "Shared table-mapping cache is empty; falling back to direct INFO.TABLES() query"
                )

            # Local cache check + populate guarded by _mapping_lock so two
            # concurrent INFO.TABLES() roundtrips can't race on the
            # populate-then-publish pair.
            with self._mapping_lock:
                if self._table_id_by_name is not None and self._table_name_by_id is not None:
                    return

            logger.debug("Loading table mappings via direct INFO.TABLES() query")
            # Load all columns from INFO.TABLES() (no projection) to include ID.
            # Run OUTSIDE the lock — the DAX query is expensive and we don't
            # want to serialize unrelated callers behind it.
            result = self.validate_and_execute_dax("EVALUATE INFO.TABLES()", 0)
            if not result.get("success"):
                logger.error(f"Failed to load table mappings: {result.get('error')}")
                with self._mapping_lock:
                    # FIX: Set to None instead of {} to allow retry on next call
                    self._table_id_by_name, self._table_name_by_id = None, None
                return
            id_by_name: Dict[str, Any] = {}
            name_by_id: Dict[Any, str] = {}
            for row in result.get("rows", []):
                # FIX: Check bracketed keys FIRST since Power BI Desktop returns [ID] and [Name]
                name = (
                    row.get("[Name]")
                    or row.get("Name")
                    or row.get("[TABLE_NAME]")
                    or row.get("TABLE_NAME")
                )
                # FIX: Check [ID] first - critical bug fix for bracketed column names
                tid = row.get("[ID]") or row.get("ID") or row.get("[TableID]") or row.get("TableID")
                if name is not None and tid is not None:
                    id_by_name[name] = tid
                    name_by_id[tid] = name
            with self._mapping_lock:
                self._table_id_by_name = id_by_name
                self._table_name_by_id = name_by_id
            logger.info(f"Table mappings loaded (legacy local cache): {len(id_by_name)} tables")
        except Exception as e:
            logger.error(f"Error building table mappings: {e}")
            with self._mapping_lock:
                # FIX: Set to None instead of {} to allow retry on next call
                self._table_id_by_name, self._table_name_by_id = None, None

    def _cache_get(self, key: Tuple[Any, ...]) -> Optional[Dict[str, Any]]:
        """Get cached item if not expired; maintains LRU order.

        B-04/B-24/B-29: the returned dict is a fresh copy (rows copied too, so a
        caller normalising keys in place can't corrupt the cached entry or race
        another thread), carries no internal ``__cached_at__`` key, reports
        ``execution_time_ms: 0`` (nothing ran) with the original timing under
        ``cached_execution_time_ms``, and ``cache: {hit: True, age_s}``.
        """
        if getattr(self, "cache_ttl_seconds", 0) <= 0:
            return None
        with self._query_cache_lock:
            item = self.query_cache.get(key)
            if not item:
                return None
            ts = item.get("__cached_at__")
            if ts is None:
                self.query_cache.pop(key, None)
                return None
            age = time.time() - ts
            if age > self.cache_ttl_seconds:
                self.query_cache.pop(key, None)
                return None
            self.query_cache.move_to_end(key)
            res = {k: v for k, v in item.items() if k not in ("__cached_at__", "__cells__")}
        rows = res.get("rows")
        if isinstance(rows, list):
            res["rows"] = [dict(r) if isinstance(r, dict) else r for r in rows]
        if isinstance(res.get("columns"), list):
            res["columns"] = list(res["columns"])
        res["cached_execution_time_ms"] = res.get("execution_time_ms")
        res["execution_time_ms"] = 0
        age_r = round(age, 3)
        res["cache"] = {"hit": True, "age_s": age_r, "age_seconds": age_r}
        return res

    def _get_table_id_from_name(self, table_name: str) -> Optional[int]:
        """
        Get the numeric TableID from a table name by querying INFO.TABLES().

        Args:
            table_name: Name of the table

        Returns:
            Numeric table ID or None if not found
        """
        try:
            self._ensure_table_mappings()
            return (self._table_id_by_name or {}).get(table_name)
        except Exception as e:
            logger.error(f"Error getting table ID for {table_name}: {e}")
            return None

    def _get_table_name_from_id(self, table_id: Any) -> Optional[str]:
        """Map numeric/guid TableID back to human-readable table name."""
        try:
            self._ensure_table_mappings()
            return (self._table_name_by_id or {}).get(table_id)
        except Exception:
            return None

    def _ensure_column_mappings(self) -> None:
        """Load column ID<->name mappings once for fast lookups (for relationships).

        P1: guarded by _mapping_lock so a concurrent burst of first callers
        collapses to a single INFO.COLUMNS() roundtrip. This is a rarely-hit
        one-shot init keyed on the same cache, so the lock is held across the
        populate (subsequent callers find the cache already published and
        return immediately rather than issuing a duplicate query).
        """
        try:
            with self._mapping_lock:
                if self._column_name_by_id is not None:
                    return
                # Load all columns from INFO.COLUMNS() to build ID->Name mapping.
                result = self.validate_and_execute_dax("EVALUATE INFO.COLUMNS()", 0)
                if not result.get("success"):
                    logger.error(f"Failed to load column mappings: {result.get('error')}")
                    self._column_name_by_id = None
                    return
                name_by_id: Dict[Any, str] = {}
                for row in result.get("rows", []):
                    # Check bracketed keys first, then unbracketed
                    # For columns, the name is in 'ExplicitName' or 'InferredName', not 'Name'
                    col_name = (
                        row.get("[ExplicitName]")
                        or row.get("ExplicitName")
                        or row.get("[InferredName]")
                        or row.get("InferredName")
                        or row.get("[Name]")
                        or row.get("Name")
                        or row.get("[COLUMN_NAME]")
                        or row.get("COLUMN_NAME")
                    )
                    col_id = (
                        row.get("[ID]")
                        or row.get("ID")
                        or row.get("[ColumnID]")
                        or row.get("ColumnID")
                    )
                    if col_name is not None and col_id is not None:
                        name_by_id[col_id] = col_name
                self._column_name_by_id = name_by_id
                logger.info(f"Column mappings loaded: {len(name_by_id)} columns")
        except Exception as e:
            logger.error(f"Error building column mappings: {e}")
            with self._mapping_lock:
                self._column_name_by_id = None

    def _get_column_name_from_id(self, column_id: Any) -> Optional[str]:
        """Map numeric/guid ColumnID back to human-readable column name."""
        try:
            self._ensure_column_mappings()
            return (self._column_name_by_id or {}).get(column_id)
        except Exception:
            return None

    def _cache_set(self, key: Tuple[Any, ...], value: Dict[str, Any]) -> bool:
        """Insert item into cache and enforce size limits. Returns True when cached.

        B-28: results over ``CACHE_MAX_ROWS_PER_ENTRY`` rows are not cached, and
        eviction also bounds the total number of cached cells, not only entries.
        """
        if getattr(self, "cache_ttl_seconds", 0) <= 0:
            return False
        try:
            rows = value.get("rows") or []
            if len(rows) > CACHE_MAX_ROWS_PER_ENTRY:
                return False
            cached = {k: v for k, v in value.items() if k != "cache"}
            cached["rows"] = [dict(r) if isinstance(r, dict) else r for r in rows]
            cached["__cached_at__"] = time.time()
            cached["__cells__"] = len(rows) * max(1, len(value.get("columns") or []))
            with self._query_cache_lock:
                self.query_cache[key] = cached
                self.query_cache.move_to_end(key)
                total = sum(int(v.get("__cells__", 0)) for v in self.query_cache.values())
                while self.query_cache and (
                    len(self.query_cache) > self.max_cache_items or total > CACHE_MAX_TOTAL_CELLS
                ):
                    _k, evicted = self.query_cache.popitem(last=False)
                    total -= int(evicted.get("__cells__", 0))
            return key in self.query_cache
        except Exception:
            return False

    def flush_cache(self) -> Dict[str, Any]:
        """Clear the in-memory query cache and return stats."""
        self.invalidate_tmsl_memo()
        try:
            with self._query_cache_lock:
                size_before = len(self.query_cache)
                self.query_cache.clear()
            return {
                "success": True,
                "cleared_items": size_before,
                "cache_enabled": getattr(self, "cache_ttl_seconds", 0) > 0,
            }
        except Exception as e:
            logger.error(f"Error flushing cache: {e}")
            return {"success": False, "error": engine_message(e)}

    def clear_vertipaq_cache(self) -> Dict[str, Any]:
        """Clear the SSAS **VertiPaq storage-engine cache** via a ClearCache XMLA batch.

        This is NOT `flush_cache()` — that one only clears this process's in-memory
        result dict. Only the XMLA ``<ClearCache>`` command drops the engine-side SE
        datacaches, which is what a performance baseline actually requires. Running a
        throwaway query such as ``EVALUATE {1}`` does **not** clear anything.

        Columns stay resident in memory (warm); only the SE query cache is dropped —
        the "warm + no-cache" state that is the correct condition for optimization
        A/B tests.

        Returns a dict with ``cache_cleared`` so callers can honestly report whether
        the baseline was valid rather than assuming it was.
        """
        try:
            from core.infrastructure import xmla_target

            if xmla_target.is_active():
                # N-11: a published model's cache is shared with production users and the
                # XMLA lane is read-only; callers report cache_cleared=False honestly.
                return {
                    "success": False,
                    "cache_cleared": False,
                    "error_type": "service_target",
                    "error": "cache not cleared (service target): ClearCache is never sent to a published model",
                }
            if not AdomdCommand:
                return {
                    "success": False,
                    "cache_cleared": False,
                    "error": "AdomdCommand unavailable (CLR not loaded)",
                }
            db_name = self._get_database_name()
            if not db_name:
                return {
                    "success": False,
                    "cache_cleared": False,
                    "error": "Could not resolve database name for ClearCache",
                }
            # DatabaseID is the catalog name for a PBI Desktop local model.
            xmla = (
                '<Batch xmlns="http://schemas.microsoft.com/analysisservices/2003/engine">'
                "<ClearCache><Object><DatabaseID>"
                f"{escape(db_name)}"
                "</DatabaseID></Object></ClearCache></Batch>"
            )
            with self._hold_gate("clear_cache"):
                cmd = AdomdCommand(xmla, self.connection)
                apply_command_timeout(cmd, self._command_timeout())
                with self._track(cmd):
                    cmd.ExecuteNonQuery()
            # The engine-side cache is gone; drop our own memoised results too,
            # otherwise the next call returns a cached row set and the timing lies.
            self.flush_cache()
            return {"success": True, "cache_cleared": True, "database": db_name}
        except ConnectionBusy as busy:
            out = busy_error(busy)
            out["cache_cleared"] = False
            return out
        except Exception as e:
            logger.warning("clear_vertipaq_cache failed: %s", e)
            return {"success": False, "cache_cleared": False, "error": engine_message(e)}

    def _escape_dax_string(self, text: str) -> str:
        """Escape text for a DAX double-quoted string literal.

        B-18: only ``"`` needs doubling inside ``"..."``. Doubling ``'`` too turned
        ``Client's Value`` into ``"Client''s Value"``, which never matches.
        """
        if not text:
            return text
        return text.replace('"', '""')

    def _analyze_dax_error(self, error_msg: str, dax_query: str) -> List[str]:
        suggestions: List[str] = []
        error_lower = (error_msg or "").lower()
        if "table" in error_lower and (
            "not found" in error_lower or "doesn't exist" in error_lower
        ):
            suggestions.extend(
                [
                    "Verify the table exists: 02_Model_Operations object_type=table operation=list",
                    "Check case-sensitive spelling",
                    "Try single quotes: 'TableName'",
                ]
            )
        if "column" in error_lower and (
            "not found" in error_lower or "doesn't exist" in error_lower
        ):
            suggestions.extend(
                [
                    "Verify the column: 02_Model_Operations object_type=table operation=describe",
                    "Check case-sensitive spelling",
                    "Try [Table][Column] syntax",
                ]
            )
        if "syntax" in error_lower:
            suggestions.extend(
                [
                    "Ensure EVALUATE for table expressions",
                    "Check balanced delimiters",
                    "Verify function parameters",
                ]
            )
        if "function" in error_lower:
            suggestions.extend(["Check function name spelling", "Verify parameter types/count"])
        if "error" in error_lower and "measure" in error_lower:
            suggestions.extend(["Check for circular dependencies", "Test expressions individually"])
        if not suggestions:
            suggestions.extend(
                ["Check DAX syntax", "Verify references exist", "Simplify query to isolate issue"]
            )
        return suggestions

    #: Static-validation memo size (P-07): the pure-Python DaxValidator costs
    #: ~4-5 us per query character (45 ms @ 9 KB, 175 ms @ 37 KB); the memo makes
    #: the 04 pre-check, the executor check and every benchmark / profile repeat of
    #: the same query text cost one validation in total.
    _SYNTAX_MEMO_MAX = 64
    _SYNTAX_MEMO_LOCK = threading.Lock()

    def _syntax_check(self, query: Any) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
        """``(basic errors, complete-query errors)`` for *query*, memoised by text.

        Pure functions of the query text, so the memo can never go stale. The
        complete-query (DEFINE) checks run only when a DEFINE keyword is present
        and any exception inside them is non-fatal (empty result), as before.
        """
        key = None
        if isinstance(query, str):
            key = hashlib.blake2b(query.encode("utf-8", "surrogatepass"), digest_size=16).digest()
            with self._SYNTAX_MEMO_LOCK:
                memo = self.__dict__.setdefault("_syntax_memo", OrderedDict())
                hit = memo.get(key)
                if hit is not None:
                    memo.move_to_end(key)
                    return hit
        basic = tuple(DaxValidator.validate_query_syntax(query))
        complete: Tuple[str, ...] = ()
        try:
            # Word match: a bare substring test fired on INFO.USERDEFINEDFUNCTIONS()
            # and rejected it as a misplaced DEFINE.
            if isinstance(query, str) and re.search(r"\bDEFINE\b", query, re.IGNORECASE):
                complete = tuple(DaxValidator.validate_complete_dax_query(query) or ())
        except Exception:
            # Non-fatal: continue with basic errors
            complete = ()
        result = (basic, complete)
        if key is not None:
            with self._SYNTAX_MEMO_LOCK:
                memo = self.__dict__.setdefault("_syntax_memo", OrderedDict())
                memo[key] = result
                while len(memo) > self._SYNTAX_MEMO_MAX:
                    memo.popitem(last=False)
        return result

    def syntax_errors(self, query: str) -> List[str]:
        """Basic syntax errors of *query* (memoised; ``[]`` = valid)."""
        return list(self._syntax_check(query)[0])

    def _validate_dax_syntax(self, query: str) -> Optional[Dict[str, Any]]:
        """
        Validate DAX query syntax before execution.

        Args:
            query: DAX query to validate

        Returns:
            Error dict if validation fails, None if valid
        """
        basic, complete = self._syntax_check(query)
        syntax_errors = list(basic)
        # Additional complete-query structural checks when DEFINE is present:
        # merge and de-duplicate
        for e in complete:
            if e not in syntax_errors:
                syntax_errors.append(e)

        if syntax_errors:
            return {
                "success": False,
                "error": f"Query validation failed: {'; '.join(syntax_errors)}",
                "error_type": "syntax_validation_error",
                "query": query,
                "suggestions": ["Fix syntax errors before executing", "Check balanced delimiters"],
            }
        return None

    def _prepare_dax_query(self, query: str, top_n: int) -> str:
        """
        Prepare DAX query by auto-adding EVALUATE if needed.

        B-11: leading ``//`` / ``--`` / ``/* */`` comments are ignored when
        looking for the EVALUATE/DEFINE keyword — a comment-prefixed complete
        query used to be wrapped whole (``EVALUATE TOPN(100, // c\\nEVALUATE ...)``).
        DMV ``SELECT ... FROM $SYSTEM.*`` statements pass through unchanged.
        """
        from core.orchestration.query_policy import strip_leading_comments

        head = strip_leading_comments(query).upper()
        if not head.startswith(("EVALUATE", "DEFINE", "SELECT")):
            if self._is_table_expression(query):
                query = f"EVALUATE TOPN({top_n}, {query})" if top_n > 0 else f"EVALUATE {query}"
            else:
                query = f'EVALUATE ROW("Value", {query})'
        return query

    def _check_dax_cache(
        self,
        cache_key: Tuple[Any, ...],
        original_query: str,
        query: str,
        top_n: int,
        bypass_cache: bool,
    ) -> Optional[Dict[str, Any]]:
        """
        Check cache for existing query results.

        A candidate hit is only served after the external model-version probe
        (``connection_state.check_model_version``, rate-limited to one DMV per
        ~2 s) confirms the model did not change underneath — a Desktop UI edit
        or refresh bumps the generation and turns the hit into a miss. When the
        probe cannot verify the model the hit is NOT served (conservative).
        """
        if bypass_cache:
            try:
                with self._counter_lock:
                    self.cache_bypass += 1
            except Exception:
                pass
            return None

        cached = self._cache_get(cache_key)
        if cached is not None:
            state = getattr(self, "_connection_state", None)
            checker = getattr(state, "check_model_version", None) if state is not None else None
            if callable(checker):
                try:
                    verdict = checker()
                except Exception:  # noqa: BLE001
                    verdict = None
                if verdict is not False:
                    # Changed (generation bumped, cache flushed) or unverifiable.
                    cached = None
        if cached is not None:
            try:
                with self._counter_lock:
                    self.cache_hits += 1
            except Exception:
                pass
            try:
                if callable(self._history_logger):
                    self._history_logger(
                        {
                            "query": original_query,
                            "final_query": query,
                            "top_n": int(top_n or 0),
                            "success": True,
                            "row_count": cached.get("row_count", 0),
                            "execution_time_ms": 0,
                            "cached": True,
                            "columns": cached.get("columns"),
                            "sample_rows": cached.get("rows", [])[
                                : min(5, len(cached.get("rows", [])))
                            ],
                        }
                    )
            except Exception:
                pass
            return cached
        try:
            with self._counter_lock:
                self.cache_misses += 1
        except Exception:
            pass
        return None

    def _read_command(
        self, cmd: Any, max_rows: Optional[int]
    ) -> Tuple[List[str], List[Dict[str, Any]], float, bool]:
        """ExecuteReader + materialise (B-06 native values, B-20 honours ``max_rows``).

        Returns ``(columns, rows, execution_ms, truncated)``. The reader is
        closed exactly once, in ``finally`` (double Close can corrupt CLR state).
        """
        start_time = time.time()
        reader = None
        try:
            reader = cmd.ExecuteReader()
            columns, rows, truncated = read_rows(reader, max_rows)
            return columns, rows, (time.time() - start_time) * 1000, truncated
        finally:
            if reader is not None:
                try:
                    reader.Close()
                except Exception:
                    pass

    def _execute_dax_reader(
        self, cmd: Any, max_rows: Optional[int] = None
    ) -> Tuple[List[str], List[Dict[str, Any]], float]:
        """Backward-compatible 3-tuple wrapper around :meth:`_read_command`."""
        cap = max_rows if max_rows is not None else getattr(QueryLimits, "SAFETY_MAX_ROWS", 10000)
        columns, rows, ms, _truncated = self._read_command(cmd, cap)
        return columns, rows, ms

    def _cache_identity(self) -> Tuple[Any, ...]:
        """(connection string, database, connection gen, model gen) for cache keys."""
        state = getattr(self, "_connection_state", None)
        cm = getattr(self, "_connection_manager", None)
        conn_str = getattr(cm, "connection_string", None) if cm is not None else None
        if not isinstance(conn_str, str):
            conn = self.connection
            conn_str = getattr(conn, "ConnectionString", None) if conn is not None else None
            conn_str = conn_str if isinstance(conn_str, str) else None
        _cid, db = getattr(self, "_db_name_cache", (None, None))
        conn_gen = getattr(state, "connection_generation", 0) if state is not None else 0
        model_gen = getattr(state, "model_generation", 0) if state is not None else 0
        return (
            conn_str,
            db,
            conn_gen if isinstance(conn_gen, int) else 0,
            model_gen if isinstance(model_gen, int) else 0,
        )

    def _version_baseline_ok(self) -> bool:
        """Make sure a model-version fingerprint exists before caching a result.

        Without a baseline the first hit-validation would adopt whatever the
        model looks like at THAT moment and serve a result computed before an
        external edit. Returns False when the model version cannot be verified
        (the result is then not cached — its hits could never be validated).
        Standalone executors (no connection_state) cache as before.
        """
        state = getattr(self, "_connection_state", None)
        checker = getattr(state, "check_model_version", None) if state is not None else None
        if not callable(checker):
            return True
        if getattr(state, "_model_version_fingerprint", None) is not None:
            return True
        try:
            return checker() is not None
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _classify_error(error_msg: str) -> str:
        if is_cancellation(error_msg):
            return "cancelled"
        if "timeout" in error_msg.lower() and "expired" in error_msg.lower():
            return "timeout"
        if is_connection_failure(error_msg):
            return "connection_not_open"
        return "query_execution_error"

    def validate_and_execute_dax(
        self,
        query: str,
        top_n: int = 0,
        bypass_cache: bool = False,
        *,
        max_rows: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Validate and execute DAX query with comprehensive error handling.

        Args:
            query: DAX query to execute
            top_n: Optional row limit (also the reader cap when > 0 — B-20)
            bypass_cache: skip the result cache (always hits the server)
            max_rows: explicit reader cap (defaults to ``top_n`` when > 0, else
                ``SAFETY_MAX_ROWS``); ``truncated`` reports whether more rows existed

        Returns:
            ``{success, columns, rows, row_count, execution_time_ms, truncated,
            has_more, query, cache: {hit, age_s}}``. On a cache hit
            ``execution_time_ms`` is 0 and the original run's timing is in
            ``cached_execution_time_ms``. Values are native JSON types (B-06).
        """
        original_query = query
        try:
            if not ADOMD_AVAILABLE:
                return {
                    "success": False,
                    "error": "ADOMD.NET not available; cannot execute DAX",
                    "error_type": "adomd_not_available",
                }

            validation_error = self._validate_dax_syntax(query)
            if validation_error:
                return validation_error

            query = self._prepare_dax_query(query, top_n)

            safety_cap = int(getattr(QueryLimits, "SAFETY_MAX_ROWS", 10000))
            if max_rows is not None and int(max_rows) > 0:
                cap = int(max_rows)
            elif top_n and int(top_n) > 0:
                cap = min(int(top_n), safety_cap)
            else:
                cap = safety_cap

            cache_key = (self._cache_identity(), query, int(top_n or 0), cap)
            cached_result = self._check_dax_cache(
                cache_key, original_query, query, top_n, bypass_cache
            )
            if cached_result is not None:
                return cached_result

            if not self.connection:
                return {
                    "success": False,
                    "error": "No active connection available",
                    "error_type": "connection_not_open",
                    "suggestions": [
                        "Reconnect to Power BI Desktop via 01_Connection (operation='ensure')",
                        "Check if Power BI Desktop is still running",
                    ],
                }

            columns, rows, execution_time, truncated, retried = self._run_command(
                query, max_rows=cap, purpose="dax_query"
            )

            result: Dict[str, Any] = {
                "success": True,
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
                "execution_time_ms": round(execution_time, 2),
                "truncated": truncated,
                "has_more": truncated,
                "query": query,
            }
            if retried:
                result["connection_recovered"] = True

            cached_ok = False
            if not bypass_cache and self._version_baseline_ok():
                # Recompute the identity: a recovery reconnect bumps the generation.
                cached_ok = self._cache_set(
                    (self._cache_identity(), query, int(top_n or 0), cap), result
                )
            result["cache"] = {
                "hit": False,
                "age_s": None,
                "bypassed": bool(bypass_cache),
                "stored": cached_ok,
                "ttl_seconds": getattr(self, "cache_ttl_seconds", 0),
            }

            try:
                if callable(self._history_logger):
                    self._history_logger(
                        {
                            "query": original_query,
                            "final_query": query,
                            "top_n": int(top_n or 0),
                            "success": True,
                            "row_count": len(rows),
                            "execution_time_ms": round(execution_time, 2),
                            "cached": False,
                            "columns": columns,
                            "sample_rows": rows[: min(5, len(rows))],
                        }
                    )
            except Exception:
                pass

            return result

        except ConnectionBusy as busy:
            out = busy_error(busy)
            out["query"] = query
            return out
        except Exception as e:
            error_msg = engine_message(e)
            q_upper = (query or "").upper()
            is_expected_dmv_probe = (
                "$SYSTEM.TMSCHEMA_" in q_upper
                or "$SYSTEM.DISCOVER" in q_upper
                or "DISCOVER_" in q_upper
            )
            if is_expected_dmv_probe:
                logger.debug(f"DMV probe failed (expected on some Desktop builds): {error_msg}")
            else:
                logger.error(f"DAX query error: {error_msg}")

            # B-01: transport / stream corruption tokens ("Root element is
            # missing", "BufferedStream", "forcibly closed") are connection
            # failures too, not DAX errors — they get the reconnect hint.
            error_type = self._classify_error(error_msg)
            is_connection_error = error_type == "connection_not_open"

            suggestions = self._analyze_dax_error(error_msg, query)
            if is_connection_error and "reconnect" not in "".join(suggestions).lower():
                suggestions.insert(0, "Reconnect to Power BI Desktop via 01_Connection (ensure)")

            result = {
                "success": False,
                "error": error_msg,
                "error_type": error_type,
                "query": query,
                "suggestions": suggestions,
            }
            try:
                if callable(self._history_logger):
                    self._history_logger(
                        {
                            "query": original_query,
                            "final_query": query,
                            "top_n": int(top_n or 0),
                            "success": False,
                            "row_count": 0,
                            "execution_time_ms": None,
                            "error": error_msg,
                            "cached": False,
                        }
                    )
            except Exception:
                pass
            return result

    #: Hard backstop for ``mode=validate`` (seconds): the rewrite below should return in
    #: milliseconds, so a longer run means the engine evaluated something.
    VALIDATE_TIMEOUT_S = 10.0

    def validate_dax(self, query: str, *, timeout_s: Optional[float] = None) -> Dict[str, Any]:
        """Parse/bind ``query`` on the engine WITHOUT executing it (PV-01).

        Rewrites every ``EVALUATE <expr>`` into an unused-branch ``VAR`` reference
        (``core.dax.dax_validate``) and runs only that cheap ``ROW`` statement, so the
        engine's own syntax and name-resolution errors come back without the query's
        cost. The result cache is never read or written.

        Returns ``{success, valid, errors[], elapsed_ms, ...}``: ``success`` is False
        only when validation could not run (no connection, timeout, transport failure,
        query without EVALUATE); an invalid query is ``success: True, valid: False``.
        """
        from core.dax.dax_validate import build_bind_only_query, remap_engine_positions

        plan = build_bind_only_query(query)
        if not plan.ok:
            return {"success": False, "error": plan.error, "error_type": "invalid_args"}
        if not ADOMD_AVAILABLE:
            return {
                "success": False,
                "error": "ADOMD.NET not available; cannot validate DAX",
                "error_type": "adomd_not_available",
            }
        if not self.connection:
            return {
                "success": False,
                "error": "No active connection available",
                "error_type": "connection_not_open",
                "suggestions": ["Reconnect via 01_Connection (operation='ensure')"],
            }
        budget = float(timeout_s if timeout_s is not None else self.VALIDATE_TIMEOUT_S)
        base: Dict[str, Any] = {
            "statements": plan.statements,
            "strategy": "define_var_dead_branch",
        }
        if plan.dropped:
            base["not_validated"] = list(plan.dropped)
        started = time.perf_counter()
        try:
            self._run_command(
                plan.query,
                max_rows=1,
                purpose="dax_validate",
                timeout_s=budget,
                gate_timeout=budget,
            )
        except ConnectionBusy as busy:
            return busy_error(busy)
        except Exception as exc:  # noqa: BLE001 - CLR interop
            elapsed = round((time.perf_counter() - started) * 1000.0, 1)
            msg = engine_message(exc)
            error_type = self._classify_error(msg)
            # Only an engine (ADOMD) error means "the DAX is invalid"; anything else
            # (no connection object, programming error) is a failed validation run.
            engine_error = "Adomd" in type(exc).__name__ or "AdomdClient" in type(exc).__module__
            if error_type == "query_execution_error" and engine_error:
                mapped = remap_engine_positions(msg, plan)
                return {
                    "success": True,
                    "valid": False,
                    "errors": [mapped],
                    "raw_errors": [msg] if mapped != msg else [],
                    "elapsed_ms": elapsed,
                    **base,
                }
            if error_type == "query_execution_error":
                error_type = "validate_failed"  # not an engine (ADOMD) error: never "invalid DAX"
            out: Dict[str, Any] = {
                "success": False,
                "error": msg,
                "error_type": error_type,
                "elapsed_ms": elapsed,
                **base,
            }
            if error_type == "timeout":
                out["hint"] = (
                    f"Validation did not finish within {budget:g}s - the engine evaluated part of "
                    "the query. Treat the result as unknown, not as valid."
                )
            return out
        return {
            "success": True,
            "valid": True,
            "errors": [],
            "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 1),
            **base,
        }

    def export_query_to_file(
        self,
        query: str,
        export_path: str,
        *,
        max_rows: int = 1_000_000,
        count_limit: int = 5_000_000,
    ) -> Dict[str, Any]:
        """Stream a query's rows straight to CSV/JSON on disk (B-15).

        No 10k reader cap: up to ``max_rows`` rows are written, further rows are
        only counted (up to ``count_limit``) so ``truncated`` / ``total_rows``
        are honest. The file is written to a temp name and ``os.replace``d
        (atomic), CSV as ``utf-8-sig`` so Excel on comma-decimal locales opens it
        correctly. Returns ``{success, exported, export_path (absolute),
        row_count, total_rows, truncated, columns, sample}``.
        """
        import csv
        import json
        import os
        import tempfile

        if not ADOMD_AVAILABLE:
            return {
                "success": False,
                "error": "ADOMD.NET not available; cannot execute DAX",
                "error_type": "adomd_not_available",
            }
        validation_error = self._validate_dax_syntax(query)
        if validation_error:
            return validation_error
        query = self._prepare_dax_query(query, 0)

        abspath = os.path.abspath(export_path)
        parent = os.path.dirname(abspath)
        ext = os.path.splitext(abspath)[1].lower()
        written = 0
        total = 0
        counted_all = True
        columns: List[str] = []
        sample: List[Dict[str, Any]] = []
        tmp_path = None
        fd = None
        started = time.time()
        try:
            os.makedirs(parent, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(prefix=".export_", suffix=ext or ".csv", dir=parent)
            with self._hold_gate("export"):
                conn = self.connection
                if not conn:
                    raise RuntimeError("No active connection available")
                cmd = AdomdCommand(query, conn)  # type: ignore[misc]
                apply_command_timeout(cmd, self._command_timeout())
                reader = None
                try:
                    with self._track(cmd):
                        reader = cmd.ExecuteReader()
                        columns = reader_columns(reader)
                        if ext == ".json":
                            fh = os.fdopen(fd, "w", encoding="utf-8")
                        else:
                            fh = os.fdopen(fd, "w", newline="", encoding="utf-8-sig")
                        fd = None
                        with fh:
                            writer = None
                            if ext == ".json":
                                fh.write("[")
                            else:
                                writer = csv.DictWriter(fh, fieldnames=columns)
                                writer.writeheader()
                            if max_rows > 0:
                                for row in iter_reader_rows(reader, columns):
                                    if ext == ".json":
                                        if written:
                                            fh.write(",")
                                        fh.write(json.dumps(row, ensure_ascii=False, default=str))
                                    else:
                                        writer.writerow(row)  # type: ignore[union-attr]
                                    if len(sample) < 10:
                                        sample.append(row)
                                    written += 1
                                    total += 1
                                    if written >= max_rows:
                                        break
                            # Count (without converting) whatever lies beyond the cap.
                            while written >= max_rows:
                                if total >= count_limit:
                                    counted_all = False
                                    break
                                if not reader.Read():
                                    break
                                total += 1
                            if ext == ".json":
                                fh.write("]")
                finally:
                    if reader is not None:
                        try:
                            reader.Close()
                        except Exception:
                            pass
            os.replace(tmp_path, abspath)
            tmp_path = None
        except ConnectionBusy as busy:
            return busy_error(busy)
        except OSError as e:
            return {
                "success": False,
                "error": f"Failed to write export to {abspath}: {e}",
                "error_type": "export_write_error",
            }
        except Exception as e:  # noqa: BLE001
            return {
                "success": False,
                "error": engine_message(e),
                "error_type": self._classify_error(engine_message(e)),
                "query": query,
            }
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
        truncated = total > written
        out: Dict[str, Any] = {
            "success": True,
            "exported": True,
            "export_path": abspath,
            "format": "json" if ext == ".json" else "csv",
            "row_count": written,
            "total_rows": total if counted_all else None,
            "total_rows_at_least": total if not counted_all else None,
            "truncated": truncated,
            "columns": columns,
            "sample": sample,
            "execution_time_ms": round((time.time() - started) * 1000, 2),
        }
        if truncated:
            out["warning"] = (
                f"Export capped at {written} rows; the query returned "
                f"{'at least ' if not counted_all else ''}{total}. Pass a larger top_n "
                "or narrow the query."
            )
        return out

    def execute_dmv_query(self, dmv_query: str) -> Dict[str, Any]:
        """
        Execute DMV (Dynamic Management Views) query against $SYSTEM schema.

        Runs under the command gate with the read-retry of :meth:`_run_command`.
        Values are native Python types (B-06): ints/floats/bools as-is, Decimal
        -> float, DateTime -> ISO string, everything else -> str. String columns
        are no longer coerced to int/float when they look numeric.

        Returns ``{success, data, rows (alias of data), row_count, columns,
        execution_time_ms}`` or ``{success: False, error, data: [], row_count: 0}``.
        """
        try:
            query = dmv_query.strip()
            if query.upper().startswith("EVALUATE"):
                query = query[8:].strip()
            logger.debug(f"Executing DMV query: {query[:100]}...")

            if not ADOMD_AVAILABLE:
                return {
                    "success": False,
                    "error": "ADOMD.NET not available; cannot execute DMV queries",
                    "data": [],
                }

            columns, rows, execution_time, truncated, _retried = self._run_command(
                query, max_rows=None, purpose="dmv_query"
            )
            logger.info(f"DMV query executed: {len(rows)} rows in {execution_time:.2f}ms")
            return {
                "success": True,
                "data": rows,
                "rows": rows,
                "row_count": len(rows),
                "columns": columns,
                "execution_time_ms": execution_time,
            }
        except ConnectionBusy as busy:
            out = busy_error(busy)
            out.update({"data": [], "row_count": 0})
            return out
        except Exception as e:
            error_msg = engine_message(e)
            logger.error(f"DMV query failed: {error_msg}")
            return {
                "success": False,
                "error": error_msg,
                "error_type": self._classify_error(error_msg),
                "data": [],
                "row_count": 0,
            }

    def analyze_dax_query(self, query: str) -> Dict[str, Any]:
        """
        Analyze DAX query for complexity, patterns, and optimization opportunities.

        Args:
            query: DAX query or expression to analyze

        Returns:
            Analysis results with complexity metrics and suggestions
        """
        try:
            # Validate syntax first (memoised: P-07)
            syntax_errors = self.syntax_errors(query)

            # Analyze complexity
            complexity_analysis = DaxValidator.analyze_complexity(query)

            # Analyze patterns. ``analyze_patterns`` returns (warnings,
            # recommendations); D-14: the warnings are the anti-patterns (the old
            # unpacking published them as "good_patterns").
            anti_patterns, good_patterns = DaxValidator.analyze_patterns(query)

            # Generate optimization suggestions
            optimization_suggestions = DaxValidator.generate_optimization_suggestions(query)

            return {
                "success": True,
                "query": query,
                "syntax_valid": len(syntax_errors) == 0,
                "syntax_errors": syntax_errors,
                "complexity": complexity_analysis,
                "good_patterns": good_patterns,
                "anti_patterns": anti_patterns,
                "optimization_suggestions": optimization_suggestions,
                "security_validated": (
                    DaxValidator.validate_identifier(query.split()[0]) if query.strip() else True
                ),
            }
        except Exception as e:
            logger.error(f"Error analyzing DAX query: {e}")
            return {"success": False, "error": engine_message(e)}

    # ---- Convenience surface for analyzers expecting a 'model' like interface ----
    # These wrappers allow components such as DependencyAnalyzer to work with the
    # query executor directly without needing a separate model API object.

    def get_tmsl_definition(self) -> Dict:
        """
        Get TMSL definition for BPA analysis.

        P-03: a successful result is memoised per ``_cache_identity()`` (connection
        string, database, connection generation, model generation) and served only
        after the same external-change probe the DAX result cache uses
        (``connection_state.check_model_version``) confirms the model is unchanged.
        Every write bumps the model generation (``write_lane.commit``), a
        connection swap bumps both generations, so a stale TMSL is never served; an
        executor without a connection state (or one whose model version cannot be
        verified) never memoises. ``06 full`` / 08 docs / ``06 compare`` no longer
        open a fresh AMO session + metadata load + serialisation for an unchanged model.

        Returns:
            TMSL definition dictionary with metadata
        """
        if not AMO_AVAILABLE:
            return {"success": False, "error": "AMO/TOM not available"}
        memo_ok = False
        ident: Optional[Tuple[Any, ...]] = None
        try:
            db_for_ident = self._get_database_name()
            ident = self._cache_identity() if db_for_ident else None
        except Exception:  # noqa: BLE001
            ident = None
        if ident is not None:
            memo = self.__dict__.get("_tmsl_memo")
            if memo is not None and memo[0] == ident:
                state = getattr(self, "_connection_state", None)
                checker = getattr(state, "check_model_version", None) if state is not None else None
                try:
                    verdict = checker() if callable(checker) else None
                except Exception:  # noqa: BLE001
                    verdict = None
                if verdict is False:  # unchanged (changed = generation bumped, unverifiable = None)
                    return dict(memo[1])
            # a memo may only be stored when its baseline fingerprint existed BEFORE the read
            memo_ok = self._version_baseline_ok() and ident == self._cache_identity()
        result = self._fetch_tmsl_definition()
        if memo_ok and result.get("success") and ident == self._cache_identity():
            self.__dict__["_tmsl_memo"] = (ident, dict(result))
        return result

    def invalidate_tmsl_memo(self) -> None:
        """Drop the memoised TMSL (the generation bump already makes it unreachable)."""
        self.__dict__.pop("_tmsl_memo", None)

    def _fetch_tmsl_definition(self) -> Dict:
        """One uncached TMSL fetch: new AMO session + ``JsonSerializer`` (see above)."""
        try:
            from Microsoft.AnalysisServices.Tabular import Server as AMOServer  # type: ignore
        except Exception as e:
            logger.error(f"Error getting TMSL (AMO import): {e}")
            return {"success": False, "error": engine_message(e)}

        # Resolve database
        db_name = self._get_database_name()
        if not db_name:
            return {"success": False, "error": "Could not determine database name"}

        server = AMOServer()
        from core.infrastructure.xmla_target import prepare_amo_server

        prepare_amo_server(server)
        try:
            server.Connect(self.connection.ConnectionString)
            database = server.Databases.GetByName(db_name)
            # Try to serialize with options, fall back if unavailable
            tmsl_json = None
            try:
                from Microsoft.AnalysisServices.Tabular import JsonSerializer, JsonSerializeOptions  # type: ignore

                options = JsonSerializeOptions()
                # Some versions may not support these props; guard with setattr
                try:
                    setattr(options, "IgnoreInferredObjects", False)
                    setattr(options, "IgnoreInferredProperties", False)
                    setattr(options, "IgnoreTimestamps", True)
                except Exception:
                    pass
                tmsl_json = JsonSerializer.SerializeObject(database.Model, options)
            except Exception:
                try:
                    from Microsoft.AnalysisServices.Tabular import JsonSerializer  # type: ignore

                    tmsl_json = JsonSerializer.SerializeObject(database.Model)
                except Exception as inner:
                    return {
                        "success": False,
                        "error": f"TMSL serialization not supported by installed AMO: {inner}",
                    }

            return {
                "success": True,
                "tmsl": tmsl_json,
                "database_name": db_name,
                "method": "TMSL extraction via AMO",
            }
        except Exception as e:
            logger.error(f"Error getting TMSL: {e}")
            return {"success": False, "error": engine_message(e)}
        finally:
            # P2-10: always disconnect AMO server to avoid leaking the connection on error
            try:
                server.Disconnect()
            except Exception:
                pass
