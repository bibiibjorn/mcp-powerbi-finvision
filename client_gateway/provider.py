"""Dictionary provider of the gateway: reads every open Power BI Desktop model, adds the values.

A background thread of the gateway process. There is NO timer: the thread sleeps until something
needs it. It runs one cycle at start, on ``POST /__gateway/refresh``, when a masked request or a
"masking on" finds a model it has not read, and when a chat sends a request and the last cycle is
older than ``poll_s`` (60 s; ``notice()``, non-blocking: the request never waits for it). No chat
active means no query against Power BI Desktop at all. A failed cycle (instance listing failed,
a model not read completely) is retried after ``poll_s`` only while a chat is active. A cycle is
one cheap fingerprint query per open model; the full detection (a few hundred small queries,
paced) runs only for a new model, a changed model (new data), or hourly while a chat used the
gateway in the last 15 minutes. The gateway process itself runs at below-normal priority.

* find the open Desktop instances (msmdsrv ports, as ``01_Connection detect`` does);
* per instance, open a PRIVATE read-only connection (``ConnectionManager.connect_instance`` plus
  ``OptimizedQueryExecutor``; it never touches the user's own finvision MCP session);
* run ``detector.detect(run_dax, policy=client_guard.gateway_policy)`` when the instance is new,
  when its model fingerprint changed, when a refresh was requested, when the last detection was
  not ready, or every ``redetect_every_s`` seconds while a chat is active;
* add ALL batches to the shared map (add-only), also those of a detection that is not ready.

Readiness (``GatewayState.ready``): False from the start until the first cycle has finished, and
whenever an open instance has no complete detection of its current model (new instance, failed
connect, a column that could not be read completely), and while NOTHING has been read yet (no
instance open and no value known: an empty dictionary masks nothing, so it must not look ready).
The server holds masked requests for a few seconds while the dictionary is not ready and then
fails them (400, fail closed, with the reason). It is True again as soon as every open instance
has a complete detection. Once a model has been read its values stay (add-only), so closing it
later does not close the gate.

Two things keep the gap between "a model opened" and "its values are known" short:

* every masked request and every "masking on" calls ``verify_instances()`` (throttled to
  ``VERIFY_EVERY_S``): a cheap instance listing; an instance without a complete detection closes
  the gate at once and wakes the thread; with every instance known it only wakes the thread when
  the last cycle is older than ``poll_s`` (``notice()``: a changed model is re-read);
* a FAILED re-detection of an instance that was complete (periodic, requested, retry) keeps its
  last good state: the gate does not close for every session because one refresh failed. A
  changed model fingerprint (new data) whose detection fails does close it.

A failed detection is classed (``connect_failed``, ``query_failed``, ``column_unreadable``,
``timeout``) so the error the user sees says what to fix.

A detection that takes longer than its limit is NOT thrown away: its thread keeps running on its
own connection (the connection is not closed under it, and no second read starts), the next cycle
waits for the same read with a doubled limit (``client_gateway.detect_timeout_s``, then x2 up to
``MAX_DETECT_TIMEOUT_S``) and takes its result when it is done. A big model therefore completes
after a few cycles instead of starting from scratch every time and never being ready.

Every call into Power BI Desktop has a hard time limit (``_bounded``): the instance listing, a
fingerprint, and the connect + detection of one model. A Desktop that is closed or hangs while it
is being read must never stall the provider: a stalled thread kept the gate closed for good
(2026-10-02, cycles 0 after minutes) and blocked every masked chat. A call over its limit is
abandoned (its daemon thread may stay stuck on a dead socket, the result is discarded), the
target is dropped without waiting for its close, and the next cycle carries on.

Only counts are exposed (``status()``), never values. Exception messages are never logged or
stored, only the exception class name.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from client_gateway.log import GatewayLog
from client_gateway.state import GatewayState, cfg
from core.client_guard.detector import NO_DATA, NO_DATA_CODE, DetectionResult, detect
from core.client_guard.engine import GENERIC_PREFIXES

POLL_S = 60.0
REDETECT_EVERY_S = 3600.0
ACTIVE_WINDOW_S = 900.0  # a periodic re-detection only runs while a chat used the gateway recently
QUERY_PACE_S = 0.005  # pause between detection queries: Power BI Desktop stays responsive
VERIFY_EVERY_S = 2.0
LIST_TIMEOUT_S = 20.0  # instance listing
FINGERPRINT_TIMEOUT_S = 20.0
DETECT_TIMEOUT_S = 120.0  # connect + full detection of one model (R0201: about 13 s)
MAX_DETECT_TIMEOUT_S = 1800.0  # the limit doubles after every timeout, up to this
VERIFY_WAIT_S = 1.5  # a masked request waits at most this long for the instance check
CLOSE_TIMEOUT_S = 5.0
ERROR_SKIP_AFTER = 2  # a column whose read failed in this many detections in a row is given up


def _bounded(fn: Callable[[], Any], timeout_s: float, name: str) -> Any:
    """``fn()`` in a daemon thread; ``TimeoutError`` when it does not return within ``timeout_s``
    (the thread is abandoned, never joined). Exceptions of ``fn`` are re-raised."""
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller
            box["error"] = exc

    thread = threading.Thread(target=run, name=name, daemon=True)
    thread.start()
    thread.join(timeout_s)
    if thread.is_alive():
        raise TimeoutError(name)
    if "error" in box:
        raise box["error"]
    return box.get("value")


class _Read:
    """One detection running in a daemon thread that outlives its caller's time limit."""

    def __init__(self, fn: Callable[[], Any], name: str) -> None:
        self.box: dict[str, Any] = {}

        def run() -> None:
            try:
                self.box["value"] = fn()
            except BaseException as exc:  # noqa: BLE001 - re-raised by ``result``
                self.box["error"] = exc

        self.thread = threading.Thread(target=run, name=name, daemon=True)
        self.thread.start()

    def result(self, timeout_s: float) -> Any:
        """The value; ``TimeoutError`` when it is still running after ``timeout_s`` (the read goes
        on); the read's own exception otherwise."""
        self.thread.join(timeout_s)
        if self.thread.is_alive():
            raise TimeoutError("provider-detect")
        if "error" in self.box:
            raise self.box["error"]
        return self.box.get("value")


POLICIES = ("identity", "dimensions")
DEFAULT_POLICY = "dimensions"


class Target(Protocol):
    """One open, read-only connection to a model."""

    def run_dax(self, query: str, max_rows: int) -> list[dict[str, Any]]: ...

    def fingerprint(self) -> object | None: ...

    def close(self) -> None: ...


class Backend(Protocol):
    def instances(self) -> list[dict[str, Any]]: ...

    def open(self, instance: dict[str, Any]) -> Target: ...


def instance_key(instance: dict[str, Any]) -> str:
    return f"{instance.get('port')}:{instance.get('msmdsrv_pid')}"


def source_label(instance: dict[str, Any]) -> str:
    """Source name stored with the values: a port, never a file name (those can be client names)."""
    return f"pbi_{instance.get('port')}"


def policy_settings() -> tuple[str, list[str] | None]:
    """(policy, safe_tables) from ``client_guard.gateway_policy`` (default 'dimensions') and
    ``client_guard.safe_tables``; anything unreadable or unknown falls back to the defaults."""
    policy = str(cfg("client_guard.gateway_policy", DEFAULT_POLICY)).strip().lower()
    if policy not in POLICIES:
        policy = DEFAULT_POLICY
    safe = cfg("client_guard.safe_tables", None)
    if not isinstance(safe, (list, tuple)) or not safe:
        return policy, None
    return policy, [str(x) for x in safe]


# ------------------------------------------------------------------------------------ backend
class DesktopTarget:
    """A private finvision connection + query executor for one Desktop instance."""

    def __init__(self, instance: dict[str, Any]) -> None:
        from core.infrastructure.connection_manager import ConnectionManager
        from core.infrastructure.query_executor import OptimizedQueryExecutor

        self._cm = ConnectionManager()
        result = self._cm.connect_instance(instance)
        if not result.get("success"):
            raise RuntimeError("connect_failed")
        self._executor = OptimizedQueryExecutor(self._cm.get_connection())
        self._executor._connection_manager = self._cm

    def run_dax(self, query: str, max_rows: int) -> list[dict[str, Any]]:
        time.sleep(QUERY_PACE_S)
        result = self._executor.validate_and_execute_dax(
            query, bypass_cache=True, max_rows=max_rows
        )
        if not result.get("success"):
            if NO_DATA.search(str(result.get("error") or "")):
                raise RuntimeError(NO_DATA_CODE)  # a code only: the message is not kept
            raise RuntimeError(str(result.get("error_type") or "query_failed"))
        rows = result.get("rows")
        return rows if isinstance(rows, list) else []

    def fingerprint(self) -> object | None:
        return self._executor.get_model_version_fingerprint()

    def close(self) -> None:
        try:
            self._cm.disconnect()
        except Exception:  # noqa: BLE001 - CLR interop
            pass


class DesktopBackend:
    def instances(self) -> list[dict[str, Any]]:
        from core.infrastructure.connection_manager import PowerBIDesktopDetector

        return list(PowerBIDesktopDetector.find_powerbi_instances())

    def open(self, instance: dict[str, Any]) -> Target:
        return DesktopTarget(instance)


# ------------------------------------------------------------------------------------ provider
class Provider:
    def __init__(
        self,
        state: GatewayState,
        backend: Backend,
        log: GatewayLog | None = None,
        detect_fn: Callable[..., DetectionResult] = detect,
        policy_fn: Callable[[], tuple[str, list[str] | None]] = policy_settings,
        poll_s: float = POLL_S,
        redetect_every_s: float = REDETECT_EVERY_S,
        clock: Callable[[], float] = time.monotonic,
        detect_timeout_s: float | None = None,
    ) -> None:
        self.state = state
        base = (
            detect_timeout_s
            if detect_timeout_s is not None
            else cfg("client_gateway.detect_timeout_s", DETECT_TIMEOUT_S)
        )
        try:
            self._detect_timeout_s = max(1.0, float(base))
        except (TypeError, ValueError):
            self._detect_timeout_s = DETECT_TIMEOUT_S
        self._reads: dict[str, _Read] = {}  # detections still running after their time limit
        self._timeouts: dict[str, int] = {}  # consecutive timeouts per instance (doubles the limit)
        self.backend = backend
        self.log = log or GatewayLog(None)
        self._detect = detect_fn
        self._policy_fn = policy_fn
        self.poll_s = poll_s
        self._redetect_every = redetect_every_s
        self._clock = clock
        self._targets: dict[str, Target] = {}
        self._fingerprints: dict[str, object | None] = {}
        self._detected_at: dict[str, float] = {}
        self._complete: dict[str, bool] = {}  # last detection of the instance was ready
        self._soft: set[str] = set()  # complete instances whose last re-detection failed
        self._fail_class: dict[str, str] = {}
        # per instance: the model fingerprint the failures below were seen under, and how many
        # detections in a row each (table, column) read failed (names of metadata, memory only)
        self._err_fp: dict[str, object | None] = {}
        self._err_cols: dict[str, dict[tuple[str, str], int]] = {}
        self._unscanned: set[str] = set()  # seen by verify_instances() after the last listing
        self._scan_lock = threading.Lock()
        self._verify_lock = threading.Lock()
        self._last_verify = -1e9
        self._ever_complete = False
        self._last_step = -1e9
        self._retry = ""  # the last cycle left work: "error" (listing failed), "incomplete"
        self._force = True
        self._requested = 0
        self._completed = 0
        self._cond = threading.Condition()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.cycles = 0
        self.errors = 0
        self._info: dict[str, Any] = {}
        self._publish(running=False, instances=0, failed=0, totals={})
        state.refresh_hook = self.request_refresh
        state.provider_status = self.status
        state.verify_hook = self.verify_instances
        state.notice_hook = self.notice
        state.set_ready(False, "dictionary_empty", {"total": 0})  # nothing is read yet

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="gateway-provider", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=10)
        self._thread = None
        self._close_all()

    def request_refresh(self, wait_s: float = 0.0) -> bool:
        """Re-detect every instance now (``POST /__gateway/refresh``). With ``wait_s`` > 0, wait
        for a cycle that started after this call to finish; returns False when it did not."""
        with self._cond:
            self._force = True
            self._requested += 1
            seq = self._requested
        self._wake.set()
        if wait_s <= 0:
            return True
        with self._cond:
            return self._cond.wait_for(lambda: self._completed >= seq, timeout=wait_s)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._info)

    def notice(self) -> None:
        """A chat sent a request: wake the thread when the last cycle is older than ``poll_s``.
        Never blocks and never queries anything itself."""
        if self._clock() - self._last_step >= self.poll_s:
            self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            failed = False
            try:
                self.step()
            except Exception as exc:  # noqa: BLE001 - the loop must survive; class name only
                failed = True
                self.errors += 1
                self.log.event("provider_error", error_type=type(exc).__name__)
            # sleep until woken; a timed retry only for a failure, or for an incomplete model
            # while a chat is active
            retry = (
                failed
                or self._retry == "error"
                or (self._retry == "incomplete" and self.state.active_within(ACTIVE_WINDOW_S))
            )
            self._wake.wait(self.poll_s if retry else None)
            self._wake.clear()

    def _close_all(self) -> None:
        for key in list(self._targets):
            self._drop(key)

    # ---------------------------------------------------------------- one cycle
    def step(self) -> int:
        """One poll: returns the number of instances that were (re)detected."""
        with self._cond:
            force, self._force = self._force, False
            seq = self._requested
        try:
            return self._step(force)
        finally:
            with self._cond:
                self._completed = max(self._completed, seq)
                self._cond.notify_all()

    def _step(self, force: bool) -> int:
        now = self._clock()
        self._last_step = now
        self._retry = "error"
        try:
            instances = _bounded(self.backend.instances, LIST_TIMEOUT_S, "provider-list")
        except Exception as exc:  # noqa: BLE001 - class name only
            self.errors += 1
            self.log.event("provider_error", error_type=type(exc).__name__)
            self._complete_all(False)
            self._publish(running=True, instances=0, failed=1, totals={})
            self.state.set_ready(
                False,
                "dictionary_incomplete",
                {"total": 0, "not_ready": 0, "classes": ["instance_listing_failed"]},
            )
            return 0
        keys = {instance_key(i) for i in instances}
        with self._scan_lock:
            self._unscanned.clear()  # this listing is newer than anything verify_instances saw
        for gone in set(self._targets) | set(self._complete):
            if gone not in keys:
                self._reads.pop(gone, None)
                self._timeouts.pop(gone, None)
                self._drop(gone)
                self._complete.pop(gone, None)
                self._detected_at.pop(gone, None)
                self._soft.discard(gone)
                self._fail_class.pop(gone, None)
                self._err_fp.pop(gone, None)
                self._err_cols.pop(gone, None)
        policy, safe = self._policy_fn()
        detected = 0
        failed = 0
        totals: dict[str, int] = {}
        for instance in instances:
            key = instance_key(instance)
            reason = self._detection_reason(key, force, now)
            if reason is None:
                continue
            ok = self._detect_one(instance, key, policy, safe, totals, reason)
            detected += 1
            failed += 0 if ok else 1
        ready, why, detail = self._readiness(keys)
        self._retry = "incomplete" if failed or not ready else ""
        self.cycles += 1
        self._publish(running=True, instances=len(instances), failed=failed, totals=totals)
        self.state.set_ready(ready, why, detail)
        if detected:
            self.log.event(
                "provider_cycle",
                instances=len(instances),
                detected=detected,
                failed=failed,
                ready=ready,
                values=self.state.smap.stats.get("values", 0),
            )
        return detected

    # ---------------------------------------------------------------- readiness
    def _readiness(self, keys: set[str]) -> tuple[bool, str, dict[str, Any]]:
        """(ready, reason code, detail) after a cycle over the instances ``keys``."""
        incomplete = [k for k in keys if not self._complete.get(k, False)]
        with self._scan_lock:
            appeared = [k for k in self._unscanned if k not in keys]
        if incomplete or appeared:
            classes = {self._fail_class.get(k, "new_instance") for k in incomplete}
            if appeared:
                classes.add("new_instance")
            return (
                False,
                "dictionary_incomplete",
                {
                    "total": len(keys) + len(appeared),
                    "not_ready": len(incomplete) + len(appeared),
                    "classes": sorted(classes),
                },
            )
        # No Power BI model open and nothing read yet: there are no client values to mask, so the
        # session works normally (dev work). Masking takes effect as soon as a model is open:
        # verify_instances() closes the gate for a model that appears and requests wait for it.
        return True, "", {}

    def verify_instances(self) -> None:
        """Called by the server for every masked request: if a model opened since the last poll,
        close the gate NOW and wake the poll thread, so the request waits for that model's values
        instead of going out with a dictionary that lacks them. Throttled; never raises.

        The instance listing runs in its own thread: a Desktop that is slow to answer costs a
        request at most ``VERIFY_WAIT_S`` (the listing itself is bounded by ``LIST_TIMEOUT_S`` and
        its result still closes the gate when it arrives)."""
        now = self._clock()
        if now - self._last_verify < VERIFY_EVERY_S:
            return
        if not self._verify_lock.acquire(blocking=False):
            return
        self._last_verify = now
        done = threading.Event()

        def work() -> None:
            try:
                self._verify_once()
            except Exception:  # noqa: BLE001 - the poll thread reports listing failures
                pass
            finally:
                self._verify_lock.release()
                done.set()

        try:
            threading.Thread(target=work, name="provider-verify", daemon=True).start()
        except Exception:  # noqa: BLE001
            self._verify_lock.release()
            return
        done.wait(VERIFY_WAIT_S)

    def _verify_once(self) -> None:
        instances = _bounded(self.backend.instances, LIST_TIMEOUT_S, "provider-verify")
        unknown = {
            k for k in (instance_key(i) for i in instances) if not self._complete.get(k, False)
        }
        if not unknown:
            self.notice()
            return
        with self._scan_lock:
            self._unscanned |= unknown
        self.state.set_ready(
            False,
            "dictionary_incomplete",
            {"total": len(instances), "not_ready": len(unknown), "classes": ["new_instance"]},
        )
        self._wake.set()

    def _detection_reason(self, key: str, force: bool, now: float) -> str | None:
        """Why ``key`` must be (re)detected now, or None."""
        if not self._complete.get(key, False):
            return "incomplete"  # new, or the last detection failed
        if key in self._soft:
            return "retry"
        if force:
            return "forced"
        if now - self._detected_at.get(key, -1e9) >= self._redetect_every and (
            self.state.active_within(ACTIVE_WINDOW_S)
        ):
            return "periodic"
        target = self._targets.get(key)
        if target is None:
            return "stale"
        try:
            fp = _bounded(target.fingerprint, FINGERPRINT_TIMEOUT_S, "provider-fingerprint")
            changed = fp != self._fingerprints.get(key)
        except Exception:  # noqa: BLE001 - stale connection: reconnect on this cycle
            self._drop(key)
            return "stale"
        return "fingerprint" if changed else None

    def _detect_one(
        self,
        instance: dict[str, Any],
        key: str,
        policy: str,
        safe: list[str] | None,
        totals: dict[str, int],
        reason: str = "incomplete",
    ) -> bool:
        progress = {"stage": "connect_failed"}

        def read() -> tuple[Any, Any, Any]:
            target = self._targets.get(key)
            if target is None:
                target = self.backend.open(instance)
                self._targets[key] = target  # dropped (and closed) when the read fails
            progress["stage"] = "query_failed"
            skip = self._skip_error_columns(key, target)
            extra = {"skip_error_columns": skip} if skip else {}
            result = self._detect(target.run_dax, policy=policy, safe_tables=safe, **extra)
            return target, result, target.fingerprint()

        running = self._reads.get(key)
        if running is None:
            running = self._reads[key] = _Read(read, "provider-detect")
        limit = min(
            self._detect_timeout_s * (2 ** self._timeouts.get(key, 0)), MAX_DETECT_TIMEOUT_S
        )
        try:
            _target, result, fingerprint = running.result(limit)
        except Exception as exc:  # noqa: BLE001 - class name only, never the message
            self.errors += 1
            timed_out = isinstance(exc, TimeoutError)
            stage = "timeout" if timed_out else progress["stage"]
            stage_id = {"connect_failed": 0, "query_failed": 1}.get(stage, 2)  # numbers only
            self.log.event(
                "provider_instance_error", error_type=type(exc).__name__, stage_id=stage_id
            )
            if timed_out:
                # the read is still running on its connection: keep both (closing the connection
                # under it, or starting a second read, is what made big models never finish)
                self._timeouts[key] = self._timeouts.get(key, 0) + 1
            else:
                self._reads.pop(key, None)
                self._drop(key)
            self._fail(key, stage, reason)
            return False
        self._reads.pop(key, None)
        self._timeouts.pop(key, None)
        self._note_error_columns(key, getattr(result, "error_columns", None), fingerprint)
        source = source_label(instance)
        by_kind: dict[tuple[str, bool], set[str]] = {}
        for batch in result.batches:
            strong = bool(getattr(batch, "strong", False))
            by_kind.setdefault((batch.prefix, strong), set()).update(batch.values)
        # identity values first, then the strong and last the plain generic values: a value that
        # is both a name and a dimension value gets the name's prefix and rules (the engine also
        # strengthens it when the order is the other way round)
        for prefix, strong in sorted(
            by_kind, key=lambda k: (0 if k[0] not in GENERIC_PREFIXES else 1 if k[1] else 2, k[0])
        ):
            self.state.smap.add_values(prefix, by_kind[(prefix, strong)], source, strong=strong)
        detectors = getattr(self.state, "detectors", None)
        if detectors is not None and by_kind:
            # remembered (DPAPI-encrypted) so the values stay masked once this model is closed
            detectors.remember([(p, s, v) for (p, s), v in by_kind.items()])
        for name, value in result.counts().items():
            totals[name] = totals.get(name, 0) + int(value)
        skipped = int(getattr(result, "skipped_error", 0) or 0)
        if skipped:  # columns given up after repeated read errors (the detection still completes)
            totals["skipped_error"] = totals.get("skipped_error", 0) + skipped
        if not result.ready:
            self._fail(key, "column_unreadable", reason)  # its partial values were added above
            return False
        self._fingerprints[key] = fingerprint
        self._detected_at[key] = self._clock()
        self._complete[key] = True
        self._ever_complete = True
        self._soft.discard(key)
        self._fail_class.pop(key, None)
        return True

    def _skip_error_columns(self, key: str, target: Target) -> frozenset[tuple[str, str]]:
        """The columns of ``key`` whose read failed in ``ERROR_SKIP_AFTER`` detections in a row of
        the CURRENT model version: a column that cannot be read must not keep the instance not
        ready for good (the detector then counts it as ``skipped_error``)."""
        counts = self._err_cols.get(key)
        if not counts:
            return frozenset()
        try:
            current = target.fingerprint()
        except Exception:  # noqa: BLE001 - unknown version: nothing is given up
            return frozenset()
        if current != self._err_fp.get(key):
            self._err_cols.pop(key, None)  # new data: the columns get their chance again
            return frozenset()
        return frozenset(c for c, n in counts.items() if n >= ERROR_SKIP_AFTER)

    def _note_error_columns(self, key: str, failed: object, fingerprint: object | None) -> None:
        """Count, per column, the detections in a row of this model version whose read failed."""
        columns = [c for c in failed if isinstance(c, tuple)] if isinstance(failed, list) else []
        previous = self._err_cols.get(key, {}) if self._err_fp.get(key) == fingerprint else {}
        counts = {c: previous.get(c, 0) + 1 for c in columns}
        if counts:
            self._err_cols[key] = counts
            self._err_fp[key] = fingerprint
        else:
            self._err_cols.pop(key, None)
            self._err_fp.pop(key, None)

    def _fail(self, key: str, failure: str, reason: str) -> None:
        """A detection of ``key`` failed. An instance that was complete keeps its last good state
        when only a re-read failed (periodic / requested / retry): one failing refresh must not
        close the gate for every session. New data (a changed fingerprint) or an instance that
        never completed does close it."""
        self._fail_class[key] = failure
        if reason in ("periodic", "forced", "retry") and self._complete.get(key, False):
            self._soft.add(key)
            return
        self._soft.discard(key)
        self._complete[key] = False

    def _complete_all(self, value: bool) -> None:
        for key in self._complete:
            self._complete[key] = value

    def _drop(self, key: str) -> None:
        target = self._targets.pop(key, None)
        self._fingerprints.pop(key, None)
        if target is not None:
            try:  # a close on a dead connection can hang too: wait a few seconds at most
                _bounded(target.close, CLOSE_TIMEOUT_S, "provider-close")
            except Exception:  # noqa: BLE001
                pass

    def _publish(self, running: bool, instances: int, failed: int, totals: dict[str, int]) -> None:
        with self._lock:
            previous = self._info.get("last_detection_counts", {})
            self._info = {
                "attached": True,
                "running": running,
                "cycles": self.cycles,
                "errors": self.errors,
                "instances": instances,
                "failed_instances": failed,
                "stale_instances": len(self._soft),
                "policy": self._policy_fn()[0],
                "last_cycle": int(time.time()) if running else None,
                "last_detection_counts": totals or previous,
            }
