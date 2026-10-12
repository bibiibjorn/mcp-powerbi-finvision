"""
Connection State Manager for PBIXRay MCP Server

Manages connection state and service initialization to avoid repeated initialization.
"""

import logging
import threading
import time
import uuid
from contextlib import nullcontext
from typing import Any, Dict, List, Optional

from core.config.config_manager import config

logger = logging.getLogger(__name__)


class ConnectionState:
    """
    Manages connection state and service instances to avoid repeated initialization.
    """

    def __init__(self):
        """Initialize connection state manager."""
        self.connection_manager = None
        self.query_executor = None
        self.dax_injector = None
        self.bpa_analyzer = None
        # P1-1: human-readable reason when bpa_analyzer is None despite init
        # being attempted. Surfaced by get_status() so callers can distinguish
        # "BPA not initialized yet" from "BPA permanently unavailable".
        self.bpa_unavailable_reason: Optional[str] = None
        self.dependency_analyzer = None
        self.bulk_operations = None
        self.calc_group_manager = None
        self.partition_manager = None
        self.rls_manager = None
        self.table_crud_manager = None
        self.column_crud_manager = None
        self.measure_crud_manager = None
        self.relationship_crud_manager = None
        self.model_exporter = None
        self.performance_optimizer = None
        self.model_validator = None
        self.agent_policy = None

        self._is_connected = False
        self._connection_info = None
        self._managers_initialized = False

        # PBIP/PBIX file information (for visual debugger integration)
        self._pbip_folder_path: Optional[str] = None  # Path to PBIP project folder
        self._file_full_path: Optional[str] = None  # Full path to .pbix or .pbip file
        self._file_type: Optional[str] = None  # 'pbix' or 'pbip'
        self._pbip_path_source: str = "none"  # 'auto-detected', 'manual', 'none'

        # Rolling query history (lightweight). Lock protects append + read
        # because _history_logger runs from the query executor thread pool
        # while get_query_history may be called from the main thread.
        self._query_history = []
        self._query_history_max = 200
        self._history_lock = threading.Lock()
        # Agent context memory and safety limits
        self._context: Dict[str, Any] = {}
        self._safety_limits: Dict[str, Any] = {
            "max_rows_per_call": 10000,
        }
        # Performance baselines and last result summary
        self._perf_baselines: Dict[str, Any] = {}
        self._last_result_meta: Dict[str, Any] = {}
        # Init lock for lazy managers
        self._init_lock = threading.RLock()

        # Table mapping cache (shared across all tool invocations)
        # This cache persists table ID<->name mappings to avoid repeated INFO.TABLES() queries
        self._table_id_by_name: Optional[Dict[str, Any]] = None
        self._table_name_by_id: Optional[Dict[Any, str]] = None
        self._table_mapping_timestamp: Optional[float] = None
        self._table_mapping_ttl: int = config.get(
            "performance.table_mapping_cache_ttl", 600
        )  # 10 minutes default

        # Last trace result cache (auto-passed to optimize). L-20: every stored
        # trace gets a ``trace_id``; a small ring keeps recent ones addressable.
        self._last_trace_result: Optional[Dict[str, Any]] = None
        self._trace_ring: List[Dict[str, Any]] = []
        self._trace_ring_max = 20

        # INFO.VIEW.* capability cache: None=unknown, True=supported,
        # False=fall back to TMSCHEMA/MDSCHEMA DMVs. Reset by cleanup()
        # so reconnecting to a different model re-probes capability.
        self._info_view_supported: Optional[bool] = None
        self._last_trace_timestamp: Optional[float] = None

        # Intelligent analysis components (lazy loaded only when needed)
        self.context_tracker = None
        self.suggestion_engine = None
        self.smart_defaults = None

        # B-02/B-03/B-04/L-03: result-cache correctness. ``model_generation``
        # is part of every executor cache key; it is bumped by write ops
        # (bump_model_generation), connection swaps and the external
        # model-version probe. ``connection_generation`` counts swaps.
        self._gen_lock = threading.Lock()
        self._model_generation = 0
        self._connection_generation = 0
        self._model_version_fingerprint: Optional[tuple] = None
        self._model_version_checked_at = 0.0
        self._model_version_probe_interval = 2.0
        self._probe_lock = threading.Lock()
        # B-01: auto-recovery of a broken connection (throttled, non-reentrant).
        self._auto_reconnect_last = 0.0
        self._auto_reconnect_interval = 2.0
        self._auto_reconnect_local = threading.local()

    def _raw_is_connected(self) -> bool:
        """Client-side check only — never reconnects (safe under ``_init_lock``)."""
        return (
            self._is_connected
            and self.connection_manager is not None
            and self.connection_manager.is_connected()
        )

    def is_connected(self) -> bool:
        """True when connected to Power BI, auto-recovering a broken connection.

        B-01: ADOMD flips ``State`` away from Open after a protocol error (stream
        corruption, engine restart). Every handler gates on this method, so
        without recovery every tool returned ``not_connected`` until the user ran
        ``01_Connection ensure``. When the user HAS connected before
        (``_is_connected``) and the connection is no longer Open, one quick
        reconnect to the same connection string is attempted (throttled to one
        attempt per ~2 s, never re-entrant, short gate wait).
        """
        from core.infrastructure import offline_model

        if offline_model.is_active():
            # OFF-01: the offline TMDL lane counts as connected for the TOM-based
            # tools; engine-backed tools fail on the missing query executor instead.
            return True
        if not self._is_connected or self.connection_manager is None:
            return False
        if self.connection_manager.is_connected():
            return True
        return self._try_auto_reconnect()

    def _try_auto_reconnect(self) -> bool:
        if getattr(self._auto_reconnect_local, "active", False):
            return False
        now = time.monotonic()
        if now - self._auto_reconnect_last < self._auto_reconnect_interval:
            return False
        self._auto_reconnect_last = now
        cm = self.connection_manager
        if cm is None or not isinstance(getattr(cm, "connection_string", None), str):
            return False
        self._auto_reconnect_local.active = True
        try:
            logger.info("Connection is not open; attempting automatic reconnect")
            try:
                result = cm.reconnect(
                    max_retries=1, retry_delay=0.0, gate_timeout=2.0, force=False, reason="auto"
                )
            except TypeError:  # legacy/fake manager without the keyword surface
                result = cm.reconnect(max_retries=1, retry_delay=0.0)
        except Exception as e:  # noqa: BLE001 — CLR interop
            logger.warning("Automatic reconnect failed: %s", e)
            return False
        finally:
            self._auto_reconnect_local.active = False
        ok = bool(isinstance(result, dict) and result.get("success"))
        if ok:
            logger.info("Automatic reconnect succeeded")
        return ok and bool(cm.is_connected())

    def verify_connection(self, auto_reconnect: bool = True) -> Dict[str, Any]:
        """Verify connection is alive. Returns dict with 'connected', 'reconnected', and optional 'error'."""
        if not self.connection_manager:
            return {
                "connected": False,
                "reconnected": False,
                "error": "Connection manager not initialized",
            }

        if self.connection_manager.is_connected():
            return {"connected": True, "reconnected": False}

        if not auto_reconnect:
            return {
                "connected": False,
                "reconnected": False,
                "error": "Connection lost and auto_reconnect is disabled",
            }

        logger.info("Connection lost, attempting reconnection...")
        result = self.connection_manager.reconnect(max_retries=3, retry_delay=0.5)

        if result.get("success"):
            # The swap listener (_on_connection_swapped) already flushed caches
            # and rebuilt every manager bound to the old connection.
            if self.query_executor and hasattr(self.query_executor, "invalidate_connection_cache"):
                self.query_executor.invalidate_connection_cache()
            logger.info("Connection restored successfully")
            return {"connected": True, "reconnected": True}
        self._is_connected = False
        return {
            "connected": False,
            "reconnected": False,
            "error": result.get("error", "Reconnection failed"),
        }

    def ensure_connected(self) -> Dict[str, Any]:
        """Alias for verify_connection(auto_reconnect=True)."""
        return self.verify_connection(auto_reconnect=True)

    def set_connection_manager(self, connection_manager):
        """Set the connection manager instance and subscribe to its swaps."""
        self.connection_manager = connection_manager
        self._is_connected = connection_manager.is_connected() if connection_manager else False
        add = getattr(connection_manager, "add_swap_listener", None)
        if callable(add):
            try:
                add(self._on_connection_swapped)
            except Exception as e:  # noqa: BLE001
                logger.debug("add_swap_listener failed: %s", e)

    # ---- Command gate / cancellation (B-01, B-19) ----
    def _gate(self):
        from core.infrastructure.adomd_utils import CommandGate

        gate = getattr(self.connection_manager, "gate", None)
        return gate if isinstance(gate, CommandGate) else None

    def command_gate(self, timeout: Optional[float] = None, purpose: str = ""):
        """Context manager serialising one ADOMD command on the shared connection.

        Wrap every direct ``AdomdCommand(...).ExecuteReader()/ExecuteNonQuery()``
        on ``connection_manager.get_connection()`` in this. Raises
        :class:`core.infrastructure.adomd_utils.ConnectionBusy` on timeout
        (convert with ``adomd_utils.busy_error``). A no-op context when no
        connection manager / gate is set (tests, disconnected).
        """
        gate = self._gate()
        if gate is None:
            return nullcontext()
        return gate.hold(timeout=timeout, purpose=purpose)

    def cancel_active_queries(self) -> int:
        """``Cancel()`` every in-flight ADOMD command; returns how many were cancelled.

        For the dispatcher's timeout path: the worker thread then stops reading
        and releases the command gate instead of racing the caller's retry on the
        shared connection.
        """
        gate = self._gate()
        if gate is None:
            return 0
        try:
            return int(gate.cancel_all())
        except Exception as e:  # noqa: BLE001
            logger.warning("cancel_active_queries failed: %s", e)
            return 0

    # ---- Model / connection generations (B-02, B-04, L-03) ----
    @property
    def model_generation(self) -> int:
        return self._model_generation

    @property
    def connection_generation(self) -> int:
        return self._connection_generation

    def bump_model_generation(self, reason: str) -> int:
        """Mark the live model as changed; returns the new generation.

        Every cached DAX result keyed on the previous generation becomes
        unreachable and the executor cache is flushed. Call after EVERY write:
        TOM CRUD, batch/changeset apply, codemods, format_dax, refresh.
        """
        with self._gen_lock:
            self._model_generation += 1
            gen = self._model_generation
        self.invalidate_query_cache(reason)
        # Table/column IDs and names change on create/rename/delete.
        self.invalidate_table_mappings()
        qe = self.query_executor
        if qe is not None and hasattr(qe, "invalidate_id_maps"):
            try:
                qe.invalidate_id_maps()
            except Exception:  # noqa: BLE001
                pass
        logger.debug("model_generation -> %d (%s)", gen, reason)
        return gen

    def invalidate_query_cache(self, reason: str) -> None:
        """Flush the executor's DAX result cache (does not bump the generation)."""
        qe = self.query_executor
        if qe is not None and hasattr(qe, "flush_cache"):
            try:
                qe.flush_cache()
            except Exception as e:  # noqa: BLE001
                logger.debug("flush_cache failed: %s", e)
        logger.debug("query cache invalidated (%s)", reason)

    def check_model_version(self, force: bool = False) -> Optional[bool]:
        """Detect model changes made OUTSIDE this server (Desktop UI edit / refresh).

        Runs a cheap model-version DMV (MDSCHEMA_CUBES LAST_SCHEMA_UPDATE /
        LAST_DATA_UPDATE + TMSCHEMA_MODEL ModifiedTime / StructureModifiedTime)
        at most once per ~2 s. Returns True when the fingerprint changed (the
        model generation is bumped), False when unchanged, None when it could
        not be verified (callers must then treat cached data as unverified).
        """
        interval = self._model_version_probe_interval
        if not force and time.monotonic() - self._model_version_checked_at < interval:
            return False if self._model_version_fingerprint is not None else None
        qe = self.query_executor
        if qe is None or not hasattr(qe, "get_model_version_fingerprint"):
            return None
        with self._probe_lock:
            if not force and time.monotonic() - self._model_version_checked_at < interval:
                return False if self._model_version_fingerprint is not None else None
            try:
                fingerprint = qe.get_model_version_fingerprint()
            except Exception as e:  # noqa: BLE001
                logger.debug("model version probe failed: %s", e)
                fingerprint = None
            self._model_version_checked_at = time.monotonic()
            previous = self._model_version_fingerprint
            self._model_version_fingerprint = fingerprint
        if fingerprint is None:
            return None
        if previous is not None and previous != fingerprint:
            self.bump_model_generation("external model change detected")
            return True
        return False

    def get_connection_identity(self) -> Dict[str, Any]:
        """Port / database / generations — lets a caller detect a model switch (B-35)."""
        info: Dict[str, Any] = {}
        try:
            inst = self.connection_manager.get_instance_info() if self.connection_manager else None
        except Exception:  # noqa: BLE001
            inst = None
        if isinstance(inst, dict):
            info["port"] = inst.get("port")
            info["database"] = inst.get("database")
        info["connection_generation"] = self._connection_generation
        info["model_generation"] = self._model_generation
        try:
            from core.infrastructure.dll_paths import select_amo_folder

            info["amo_version"] = select_amo_folder().get("version")
        except Exception:  # noqa: BLE001
            pass
        return info

    def _on_connection_swapped(self, old_conn_str, new_conn_str, reason: str) -> None:
        """B-05: a connection swap invalidates everything bound to the old one.

        Clears the result cache, table/column ID maps, the INFO.VIEW capability
        flag and (on a different target) the trace cache and PBIP info, bumps
        both generations and rebuilds every manager that captured the old
        connection object at construction time. Never does ADOMD I/O.
        """
        same_target = bool(old_conn_str) and old_conn_str == new_conn_str
        with self._gen_lock:
            self._connection_generation += 1
            self._model_generation += 1
        self._model_version_fingerprint = None
        self._model_version_checked_at = 0.0
        old_qe = self.query_executor
        if old_qe is not None:
            for hook in ("flush_cache", "invalidate_id_maps", "invalidate_connection_cache"):
                fn = getattr(old_qe, hook, None)
                if callable(fn):
                    try:
                        fn()
                    except Exception:  # noqa: BLE001
                        pass
        with self._init_lock:
            self._table_id_by_name = None
            self._table_name_by_id = None
            self._table_mapping_timestamp = None
            self._info_view_supported = None
            if not same_target:
                self._last_trace_result = None
                self._last_trace_timestamp = None
                self._trace_ring = []
                self._pbip_folder_path = None
                self._file_full_path = None
                self._file_type = None
                self._pbip_path_source = "none"
            rebuild = self._managers_initialized
        logger.info("Connection swapped (%s, same_target=%s); caches cleared", reason, same_target)
        if rebuild and self.connection_manager is not None:
            try:
                conn = self.connection_manager.get_connection()
                with self._init_lock:
                    self._build_managers(conn, force_reinit=True)
            except Exception as e:  # noqa: BLE001
                logger.error("Rebuilding managers after connection swap failed: %s", e)

    def probe_connection_health(self, timeout_s: float = 5.0) -> Dict[str, Any]:
        """Round-trip health probe that never touches the result cache (B-03)."""
        qe = self.query_executor
        if qe is None:
            return {"success": False, "error": "query executor not initialized"}
        probe = getattr(qe, "probe_health", None)
        if callable(probe):
            return probe(timeout_s=timeout_s)
        return qe.validate_and_execute_dax(
            "SELECT [CATALOG_NAME] FROM $SYSTEM.DBSCHEMA_CATALOGS", 1, bypass_cache=True
        )

    def _is_table_mapping_cache_valid(self) -> bool:
        """Check if table mapping cache is valid (not expired).

        Audit T123: re-read the TTL each call so config.reload() takes
        effect without needing a process restart. Cheap (config.get is
        an in-memory dict read after init).
        """
        if self._table_id_by_name is None or self._table_name_by_id is None:
            return False
        if self._table_mapping_timestamp is None:
            return False
        ttl = config.get("performance.table_mapping_cache_ttl", self._table_mapping_ttl)
        age = time.time() - self._table_mapping_timestamp
        return age <= int(ttl)

    def get_table_mappings(
        self, refresh: bool = False
    ) -> tuple[Optional[Dict[str, Any]], Optional[Dict[Any, str]]]:
        """Return cached (id_by_name, name_by_id) table mappings, loading fresh if needed or refresh=True.

        P1: the INFO.TABLES() query runs OUTSIDE ``_init_lock``. Holding the
        RLock across the synchronous DAX round-trip (hundreds of ms on a large
        model) would block every other ``_init_lock``-guarded method (manager
        init, perf baselines, ...). The lock is held only to read the cache and
        to write the result — never across the query.
        """
        # Fast path: read cache under the lock, release before any I/O.
        with self._init_lock:
            if not refresh and self._is_table_mapping_cache_valid():
                logger.debug(
                    f"Table mappings cache hit ({len(self._table_id_by_name or {})} tables)"
                )
                return (self._table_id_by_name, self._table_name_by_id)
            executor = self.query_executor

        if not executor:
            logger.warning("Cannot load table mappings: query executor not initialized")
            return (None, None)

        # Load fresh mappings OUTSIDE the lock.
        try:
            logger.debug("Loading table mappings from INFO.TABLES()...")
            # NOTE: No circular dependency here - INFO.TABLES() is a system function that
            # doesn't require table name resolution, so validate_and_execute_dax() won't
            # call _ensure_table_mappings() for this query
            result = executor.validate_and_execute_dax("EVALUATE INFO.TABLES()", 0)
        except Exception as e:
            logger.error(f"Error loading table mappings: {e}")
            return (None, None)

        if not result.get("success"):
            logger.error(f"Failed to load table mappings: {result.get('error')}")
            return (None, None)

        id_by_name: Dict[str, Any] = {}
        name_by_id: Dict[Any, str] = {}

        for row in result.get("rows", []):
            # Check bracketed keys FIRST since Power BI Desktop returns [ID] and [Name]
            name = (
                row.get("[Name]")
                or row.get("Name")
                or row.get("[TABLE_NAME]")
                or row.get("TABLE_NAME")
            )
            tid = row.get("[ID]") or row.get("ID") or row.get("[TableID]") or row.get("TableID")

            if name is not None and tid is not None:
                id_by_name[name] = tid
                name_by_id[tid] = name

        # Re-acquire the lock only to write the result.
        with self._init_lock:
            self._table_id_by_name = id_by_name
            self._table_name_by_id = name_by_id
            self._table_mapping_timestamp = time.time()

        logger.info(
            f"Table mappings loaded and cached: {len(id_by_name)} tables (TTL: {self._table_mapping_ttl}s)"
        )
        return (id_by_name, name_by_id)

    def invalidate_table_mappings(self) -> Dict[str, Any]:
        """Invalidate table mapping cache, forcing reload on next access (useful after schema changes)."""
        try:
            with self._init_lock:
                had_cache = self._table_id_by_name is not None
                table_count = len(self._table_id_by_name) if self._table_id_by_name else 0

                self._table_id_by_name = None
                self._table_name_by_id = None
                self._table_mapping_timestamp = None

            if had_cache:
                logger.info(f"Table mapping cache invalidated ({table_count} tables)")
                return {
                    "success": True,
                    "message": f"Cache invalidated ({table_count} tables)",
                    "tables_cleared": table_count,
                }
            else:
                return {"success": True, "message": "No cache to invalidate", "tables_cleared": 0}
        except Exception as e:
            logger.error(f"Error invalidating table mapping cache: {e}")
            return {"success": False, "error": str(e)}

    def initialize_managers(self, force_reinit: bool = False):
        """Initialize all service managers with double-checked locking. force_reinit=True re-creates existing managers."""
        # Quick check without lock
        if self._managers_initialized and not force_reinit:
            logger.debug("Managers already initialized, skipping")
            return

        # Thread-safe initialization with double-check locking pattern
        with self._init_lock:
            # Double-check after acquiring lock
            if self._managers_initialized and not force_reinit:
                logger.debug("Managers already initialized (double-check), skipping")
                return

            # Raw check: is_connected() may auto-reconnect, which swaps the
            # connection and re-enters here via the swap listener.
            if not self._raw_is_connected() or not self.connection_manager:
                logger.warning("Cannot initialize managers: not connected")
                return

            try:
                conn = self.connection_manager.get_connection()
                self._build_managers(conn, force_reinit=force_reinit)

                # Initialize BPA if available
                if config.is_feature_enabled("enable_bpa"):
                    self._initialize_bpa(force_reinit)

                self._managers_initialized = True
                logger.info("[OK] All managers initialized successfully")

            except Exception as e:
                logger.error(f"Error initializing managers: {e}", exc_info=True)
                self._managers_initialized = False

    # ---------------------------------------------------------------
    # Factory registry — one source of truth for manager construction
    # ---------------------------------------------------------------
    # P2-6 / P2-14 (2026-04-23 audit): previously 14 near-identical
    # `if self.X is None: try/except ImportError` blocks. Each addition or
    # rename had to be synchronized across init, get_status, cleanup,
    # docstrings. A declarative table collapses 120 lines of boilerplate into
    # a single spec. The old guarantees are preserved: per-manager ImportError
    # isolation, `is None` identity checks (avoid __bool__ triggers), and the
    # query_executor-first ordering (downstream managers depend on it).
    _MANAGER_SPECS = (
        # (attr_name,                 module_path,                                     class_name,               ctor_arg_names,         post_hooks)
        (
            "query_executor",
            "core.infrastructure.query_executor",
            "OptimizedQueryExecutor",
            ("conn",),
            ("link_state", "link_history_logger"),
        ),
        (
            "dax_injector",
            "core.dax.dax_injector",
            "DAXInjector",
            ("conn",),
            ("link_connection_manager",),
        ),
        (
            "dependency_analyzer",
            "core.model.dependency_analyzer",
            "DependencyAnalyzer",
            ("query_executor",),
            (),
        ),
        (
            "calc_group_manager",
            "core.operations.calculation_group_manager",
            "CalculationGroupManager",
            ("conn",),
            (),
        ),
        (
            "rls_manager",
            "core.operations.rls_manager",
            "RLSManager",
            ("conn", "query_executor"),
            (),
        ),
        (
            "table_crud_manager",
            "core.operations.table_crud_manager",
            "TableCRUDManager",
            ("conn",),
            (),
        ),
        (
            "column_crud_manager",
            "core.operations.column_crud_manager",
            "ColumnCRUDManager",
            ("conn",),
            (),
        ),
        (
            "measure_crud_manager",
            "core.operations.measure_crud_manager",
            "MeasureCRUDManager",
            ("conn",),
            (),
        ),
        (
            "relationship_crud_manager",
            "core.operations.relationship_crud_manager",
            "RelationshipCRUDManager",
            ("conn",),
            (),
        ),
        ("model_exporter", "core.model.model_exporter", "ModelExporter", ("conn",), ()),
        (
            "performance_optimizer",
            "core.performance.performance_optimizer",
            "PerformanceOptimizer",
            ("query_executor",),
            (),
        ),
        (
            "model_validator",
            "core.model.model_validator",
            "ModelValidator",
            ("query_executor",),
            (),
        ),
    )

    def _build_managers(self, conn, *, force_reinit: bool = False) -> None:
        """Instantiate every manager per ``_MANAGER_SPECS``.

        Each manager is built inside its own try/except ImportError block so a
        failure in one submodule doesn't cascade — that was the root-cause of
        the pre-P0-B "partial init" NameError chain. Post-hooks run only on
        the newly-constructed instance and are best-effort (logged, not fatal).
        """
        import importlib

        for attr, module_path, class_name, ctor_args, post_hooks in self._MANAGER_SPECS:
            current = getattr(self, attr, None)
            if current is not None and not force_reinit:
                continue
            try:
                module = importlib.import_module(module_path)
                cls = getattr(module, class_name)
                kwargs_for_ctor = []
                for ctor_arg in ctor_args:
                    if ctor_arg == "conn":
                        kwargs_for_ctor.append(conn)
                    else:
                        kwargs_for_ctor.append(getattr(self, ctor_arg, None))
                instance = cls(*kwargs_for_ctor)
                setattr(self, attr, instance)

                for hook in post_hooks:
                    self._run_manager_post_hook(attr, instance, hook)

                logger.debug("[OK] %s initialized", attr)
            except ImportError as ie:
                logger.error("%s import failed: %s", class_name, ie, exc_info=True)
            except Exception as ctor_err:  # noqa: BLE001
                logger.error(
                    "%s construction failed (%s); leaving %s=None",
                    class_name,
                    ctor_err,
                    attr,
                    exc_info=True,
                )

    def _run_manager_post_hook(self, attr: str, instance, hook: str) -> None:
        """Apply a named post-construction hook to a freshly-built manager.

        Hooks are side-effectful wiring steps (link to connection_state, link
        to connection_manager for reconnection, register history logger).
        Kept out of ``_MANAGER_SPECS`` so the registry stays a plain data
        table. Failures are warnings — they don't void the manager.
        """
        try:
            if hook == "link_state":
                if hasattr(instance, "set_connection_state"):
                    instance.set_connection_state(self)
            elif hook == "link_connection_manager":
                if self.connection_manager and hasattr(instance, "set_connection_manager"):
                    instance.set_connection_manager(self.connection_manager)
            elif hook == "link_history_logger":
                if hasattr(instance, "set_history_logger"):
                    instance.set_history_logger(self._history_logger)
            else:
                logger.warning("Unknown post-hook %r for %s", hook, attr)
        except Exception as hook_err:  # noqa: BLE001
            logger.warning("Post-hook %s for %s failed: %s", hook, attr, hook_err)

    def _initialize_bpa(self, force_reinit: bool = False):
        """Initialize BPA analyzer if available.

        P1-1 fix: previously fell through to `BPAAnalyzer(rules_path)` even when
        every rules file path was missing, which caused a silent construction
        failure that left `bpa_analyzer = None` without a user-visible reason.
        Now finds the first existing rules file and records a diagnostic
        `bpa_status` / `bpa_unavailable_reason` on self so `get_status()` can
        report truthfully.
        """
        if self.bpa_analyzer and not force_reinit:
            return
        try:
            from core.analysis.bpa_analyzer import BPAAnalyzer
            import os

            script_dir = os.path.dirname(os.path.abspath(__file__))
            root_dir = os.path.dirname(os.path.dirname(script_dir))

            # Prefer the curated ruleset; fall back to the Microsoft one. Pick the first that exists.
            candidate_paths = [
                (os.path.join(root_dir, "config", "bpa_rules_comprehensive.json"), "comprehensive"),
                (os.path.join(root_dir, "core", "bpa.json"), "legacy"),
            ]
            rules_path = None
            rules_label = None
            for path, label in candidate_paths:
                if os.path.exists(path):
                    rules_path = path
                    rules_label = label
                    break

            if rules_path is None:
                self.bpa_analyzer = None
                self.bpa_unavailable_reason = (
                    "No BPA rules file found. Expected one of: "
                    + ", ".join(p for p, _ in candidate_paths)
                )
                logger.warning(self.bpa_unavailable_reason)
                return

            logger.info(f"Using BPA rules: {rules_label} ({rules_path})")
            self.bpa_analyzer = BPAAnalyzer(rules_path)
            self.bpa_unavailable_reason = None
            logger.debug(f"[OK] BPA analyzer initialized with {len(self.bpa_analyzer.rules)} rules")
        except ImportError as e:
            self.bpa_analyzer = None
            self.bpa_unavailable_reason = f"BPA import failed: {e}"
            logger.debug(self.bpa_unavailable_reason)
        except FileNotFoundError as e:
            self.bpa_analyzer = None
            self.bpa_unavailable_reason = f"BPA rules file not found: {e}"
            logger.warning(self.bpa_unavailable_reason)
        except Exception as e:  # noqa: BLE001 — BPAAnalyzer ctor may raise anything
            self.bpa_analyzer = None
            self.bpa_unavailable_reason = f"BPA init error: {e}"
            logger.error(self.bpa_unavailable_reason, exc_info=True)

    def get_manager(self, manager_name: str) -> Optional[Any]:
        """
        Get a specific manager instance.

        Args:
            manager_name: Name of the manager to retrieve

        Returns:
            Manager instance or None if not available
        """
        return getattr(self, manager_name, None)

    def cleanup(self):
        """Clean up connection state and managers."""
        with self._init_lock:
            self.query_executor = None
            self.dax_injector = None
            self.bpa_analyzer = None
            self.dependency_analyzer = None
            self.bulk_operations = None
            self.calc_group_manager = None
            self.partition_manager = None
            self.rls_manager = None
            self.table_crud_manager = None
            self.column_crud_manager = None
            self.measure_crud_manager = None
            self.relationship_crud_manager = None
            self.model_exporter = None
            self.performance_optimizer = None
            self.model_validator = None
            # agent_policy is NOT reset: it is created once per process at server start
            # (src/pbixray_server_enhanced.py), not per connection. Resetting it here left
            # every 04_Run_DAX call failing with "agent_policy not available" after any
            # target switch (connect_folder / connect_xmla / ensure onto another instance)
            # until the server restarted (live 2026-09-30).

            self._is_connected = False
            self._connection_info = None
            self._managers_initialized = False

            # Reset capability caches that depend on the connected model
            self._info_view_supported = None

            # Clear table mapping cache
            self._table_id_by_name = None
            self._table_name_by_id = None
            self._table_mapping_timestamp = None

            # Clear trace result cache
            self._last_trace_result = None
            self._last_trace_timestamp = None
            self._trace_ring = []
            self._model_version_fingerprint = None
            self._model_version_checked_at = 0.0

            # Clear PBIP path information
            self._pbip_folder_path = None
            self._file_full_path = None
            self._file_type = None
            self._pbip_path_source = "none"

        logger.info("Connection state cleaned up")

    # ---- PBIP Path Management ----
    def set_pbip_info(
        self,
        pbip_folder_path: Optional[str] = None,
        file_full_path: Optional[str] = None,
        file_type: Optional[str] = None,
        source: str = "manual",
    ) -> Dict[str, Any]:
        """
        Set PBIP/PBIX file information.

        Args:
            pbip_folder_path: Path to PBIP project folder (for PBIP projects)
            file_full_path: Full path to .pbix or .pbip file
            file_type: 'pbix' or 'pbip'
            source: 'auto-detected' or 'manual'

        Returns:
            Status dictionary with current PBIP info
        """
        self._pbip_folder_path = pbip_folder_path
        self._file_full_path = file_full_path
        self._file_type = file_type
        self._pbip_path_source = source

        logger.info(f"PBIP info set: type={file_type}, folder={pbip_folder_path}, source={source}")
        return self.get_pbip_info()

    def get_pbip_info(self) -> Dict[str, Any]:
        """
        Get current PBIP/PBIX file information.

        Returns:
            Dictionary with:
            - pbip_folder_path: Path to PBIP folder (None for PBIX)
            - file_full_path: Full path to file
            - file_type: 'pbix', 'pbip', or None
            - source: 'auto-detected', 'manual', or 'none'
            - pbip_available: True if PBIP folder is available for analysis
        """
        return {
            "pbip_folder_path": self._pbip_folder_path,
            "file_full_path": self._file_full_path,
            "file_type": self._file_type,
            "source": self._pbip_path_source,
            "pbip_available": self._pbip_folder_path is not None and self._file_type == "pbip",
        }

    def get_pbip_folder_path(self) -> Optional[str]:
        """Get the PBIP folder path if available."""
        return self._pbip_folder_path

    @property
    def pbip_path(self) -> Optional[str]:
        """Read-only alias for get_pbip_folder_path. To set, call set_pbip_info(...)."""
        return self._pbip_folder_path

    @pbip_path.setter
    def pbip_path(self, value: Optional[str]) -> None:
        # P2-12: explicit setter to prevent silent writes to _pbip_folder_path.
        # Canonical path to set this is set_pbip_info(...).
        raise AttributeError(
            "connection_state.pbip_path is read-only; use set_pbip_info(pbip_folder_path=...) instead"
        )

    # ---- Query history helpers ----
    def _history_logger(self, entry: dict) -> None:
        """Internal callback used by query executor to log execution metadata."""
        try:
            # Attach timestamp and minimal connection hint
            entry = dict(entry or {})
            entry.setdefault("ts", time.time())
            try:
                if self.connection_manager:
                    info = self.connection_manager.get_instance_info() or {}
                    if "port" in info:
                        entry.setdefault("port", info.get("port"))
            except (AttributeError, TypeError) as e:
                logger.debug(f"history_logger: failed to attach port hint: {e}")
            with self._history_lock:
                self._query_history.append(entry)
                if len(self._query_history) > self._query_history_max:
                    # Trim oldest
                    overflow = len(self._query_history) - self._query_history_max
                    if overflow > 0:
                        del self._query_history[0:overflow]
            # Update last result meta for successes
            try:
                if entry.get("success"):
                    keys = [
                        "query",
                        "final_query",
                        "top_n",
                        "row_count",
                        "execution_time_ms",
                        "cached",
                        "columns",
                        "sample_rows",
                        "ts",
                    ]
                    self._last_result_meta = {k: entry.get(k) for k in keys if k in entry}
            except (AttributeError, TypeError) as e:
                logger.debug(f"history_logger: failed to update last_result_meta: {e}")
        except (AttributeError, TypeError) as e:
            # Never break execution on history issues, but do log
            logger.debug(f"history_logger: swallowed error: {e}")

    # ---- Trace result cache (auto-passed to optimize) ----
    def store_trace_result(self, trace_data: Dict[str, Any]) -> Optional[str]:
        """Cache a trace result for auto-retrieval by optimize; returns its ``trace_id``.

        L-20: the trace is tagged with a ``trace_id`` (kept when the caller
        already set one), the query text when present and the model generation,
        and kept in a small ring so ``get_trace_result(trace_id)`` can address a
        specific trace instead of "whatever ran last in the process".
        """
        if not isinstance(trace_data, dict):
            return None
        trace_id = str(trace_data.get("trace_id") or f"trace_{uuid.uuid4().hex[:12]}")
        trace_data["trace_id"] = trace_id
        entry = {
            "trace_id": trace_id,
            "stored_at": time.time(),
            "query": trace_data.get("query") or trace_data.get("dax_query"),
            "model_generation": self._model_generation,
            "data": trace_data,
        }
        with self._init_lock:
            self._last_trace_result = trace_data
            self._last_trace_timestamp = entry["stored_at"]
            self._trace_ring.append(entry)
            if len(self._trace_ring) > self._trace_ring_max:
                del self._trace_ring[0 : len(self._trace_ring) - self._trace_ring_max]
        return trace_id

    def get_cached_trace_result(
        self, max_age_seconds: int = 300, trace_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Get a cached trace result if still fresh (default 5 min TTL).

        Without ``trace_id`` this is the legacy "last trace in the process"
        (backward compatible); prefer passing the ``trace_id`` a trace-producing
        call returned. See :meth:`get_last_trace_info` to detect ambiguity.
        """
        if trace_id:
            return self.get_trace_result(trace_id, max_age_seconds=max_age_seconds)
        with self._init_lock:
            if self._last_trace_result is None or self._last_trace_timestamp is None:
                return None
            if time.time() - self._last_trace_timestamp > max_age_seconds:
                return None
            return self._last_trace_result

    def get_trace_result(
        self, trace_id: str, max_age_seconds: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """The trace stored under ``trace_id`` (None when unknown or expired)."""
        with self._init_lock:
            for entry in reversed(self._trace_ring):
                if entry["trace_id"] != trace_id:
                    continue
                if (
                    max_age_seconds is not None
                    and time.time() - entry["stored_at"] > max_age_seconds
                ):
                    return None
                return entry["data"]
        return None

    def get_last_trace_info(self, window_seconds: int = 300) -> Dict[str, Any]:
        """Describe the most recent trace + whether picking "the last one" is ambiguous.

        ``ambiguous`` is True when more than one distinct query was traced in the
        window (e.g. a sibling agent traced in parallel) — callers such as
        ``09_Debug optimize`` should then require an explicit ``trace_id``.
        """
        now = time.time()
        with self._init_lock:
            recent = [e for e in self._trace_ring if now - e["stored_at"] <= window_seconds]
        if not recent:
            return {"available": False, "ambiguous": False, "recent": []}
        last = recent[-1]
        distinct = {e.get("query") or e["trace_id"] for e in recent}
        return {
            "available": True,
            "trace_id": last["trace_id"],
            "query": last.get("query"),
            "age_s": round(now - last["stored_at"], 1),
            "stale_model": last.get("model_generation") != self._model_generation,
            "ambiguous": len(distinct) > 1,
            "recent": [
                {
                    "trace_id": e["trace_id"],
                    "query": (e.get("query") or "")[:200],
                    "age_s": round(now - e["stored_at"], 1),
                }
                for e in reversed(recent)
            ],
        }

    def get_safety_limits(self) -> Dict[str, Any]:
        with self._init_lock:
            return dict(self._safety_limits)

    def get_status(self) -> Dict[str, Any]:
        """
        Get current connection and manager status.

        Returns:
            Status dictionary with connection and manager states
        """
        managers_status = {}
        manager_names = [
            "query_executor",
            "dax_injector",
            "bpa_analyzer",
            "dependency_analyzer",
            "calc_group_manager",
            "rls_manager",
            "table_crud_manager",
            "column_crud_manager",
            "measure_crud_manager",
            "relationship_crud_manager",
            "model_exporter",
            "performance_optimizer",
            "model_validator",
        ]

        for name in manager_names:
            managers_status[name] = getattr(self, name) is not None

        gate = self._gate()
        return {
            "connected": self.is_connected(),
            "managers_initialized": self._managers_initialized,
            "managers": managers_status,
            "connection_info": self._connection_info,
            "identity": self.get_connection_identity(),
            "command_gate": gate.status() if gate is not None else None,
        }


# Global connection state instance
connection_state = ConnectionState()
