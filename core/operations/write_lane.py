"""Process-wide lane for live TOM writes (review C-10, C-22, C-27, C-43).

Every ``model.SaveChanges()`` in the CRUD managers goes through
:func:`commit`. One choke point gives four guarantees the managers used to
re-implement (or skip) individually:

* **Read-only XMLA guard (N-01)** — :func:`commit` raises
  :class:`~core.infrastructure.xmla_target.ServiceReadOnly` while the published-model
  target is active, so no writer (present or future) can reach the service.
* **Serialisation** — a re-entrant lock around mutate+save, so a nested or
  non-dispatcher caller cannot interleave two SaveChanges on Desktop's engine
  (the dispatcher already serialises write *tools*; this covers the rest).
* **Post-write classification (C-10)** — an exception raised by SaveChanges is
  not proof the write failed: an older AMO client can fail to parse the
  server's response *after* the server committed (``Unexpected column name
  'ObjectID.BindingInfo' in rowset 'Annotations'``). When the caller supplies a
  ``verify`` callback (a read-back over a fresh ADOMD round trip) and it says
  the change landed, the write is reported as applied with a
  ``post_write_warning`` instead of a failure — a blind retry would
  double-apply.
* **Pacing + health (C-22)** — inside multi-save loops (``in_loop=True``) a
  small configurable pause plus a cheap health probe run after each save, so a
  bulk write cannot wedge Desktop; an unhealthy probe raises
  :class:`DesktopUnresponsive` and the loop stops.
* **Cache invalidation (C-27)** — every successful save bumps the model
  generation (DAX result cache, table/column id maps, dependency index), even
  when the dispatcher later sees a ``success: false`` result.
* **Durable journal (JR-01)** — every save that lands is appended to
  ``output/journal/<project>.jsonl`` with the pre- and post-image of each touched
  object (:mod:`core.operations.write_journal`), so a discarded reload or a crash
  can be replayed and a write undone. A journal failure never fails the write: it
  surfaces as ``journal_warning`` in the returned info.

* **Preview mode (F-01)** — inside :func:`preview_scope` every :func:`commit` is SUPPRESSED: the model
  is serialised (the "after" image), the committed model is read once (the "before" image) and
  ``SaveChanges`` is never called; the caller (``core.operations.write_preview``) diffs the images
  and rolls the in-memory edits back with :func:`safe_undo`. A preview therefore runs the real
  manager code (name checks, cascades, errors) against the real model without persisting anything.

* **Single-write pacing + post-write health (F-25 / S-12)** — consecutive SINGLE writes
  (separate tool calls, ``in_loop=False``) are paced by a token bucket (``pace_max_saves``
  saves inside ``pace_window_s`` insert ``pace_pause_s`` before the next), and every save
  against a live Desktop reads which measures / columns / calc items are NOW in error and
  were not before (``post_write_errors: [{object, message}]``). Both reach the tool result
  through :func:`call_notes` (the dispatcher merges them), so the ~85 ``commit`` call sites
  need no edit; the result also carries ``writes_last_60s`` so an agent can pace itself.

Config (``config/default_config.json`` → ``model_write.*``, all optional):
``loop_pause_s`` (default 0.1), ``health_check_in_loops`` (default true),
``health_timeout_s`` (default 5.0), ``pace_single_writes`` (true), ``pace_window_s`` (10),
``pace_max_saves`` (3), ``pace_pause_s`` (0.5), ``post_write_health`` (true),
``post_write_baseline_ttl_s`` (120).
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "DesktopUnresponsive",
    "ModelWriteLane",
    "PreviewCapture",
    "call_notes",
    "commit",
    "get_write_lane",
    "merge_notes",
    "post_write_warning",
    "preview_active",
    "preview_scope",
    "safe_undo",
]


class DesktopUnresponsive(RuntimeError):
    """Raised inside a write loop when the post-save health probe fails."""

    def __init__(self, message: str, health: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.health = health or {}


def post_write_warning(label: str, exc: BaseException) -> str:
    return (
        f"{label}: SaveChanges raised {type(exc).__name__}: {exc} — but a read-back "
        "shows the change IS in the model (client-side response parse error after "
        "the server committed). Treat as applied; do NOT retry."
    )


def safe_undo(model: Any) -> None:
    """Roll back every unsaved edit on ``model`` (N-02).

    Live lane: ``model.UndoLocalChanges()``. Offline lane: that call throws on a
    disconnected model, so the folder is re-read instead (``offline_model.rollback``);
    the ``model`` the caller holds is stale afterwards. Raises like the underlying call.
    """
    from core.infrastructure import offline_model

    if offline_model.is_active():
        offline_model.rollback()
        return
    model.UndoLocalChanges()


class PreviewCapture:
    """What a suppressed commit captured during one preview run (F-01).

    ``serialize(model) -> TMSL dict`` and ``committed(model) -> TMSL dict`` are injected by
    :mod:`core.operations.write_preview` (they touch the CLR); ``before`` may be handed in
    (offline lane: the folder-loaded model is already mutated by the time a commit happens).
    """

    def __init__(
        self,
        serialize: Callable[[Any], Dict[str, Any]],
        committed: Optional[Callable[[Any], Dict[str, Any]]] = None,
        before: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> None:
        self._serialize = serialize
        self._committed = committed
        self.before = before
        self.after: Optional[Dict[str, Dict[str, Any]]] = None
        self.labels: List[str] = []
        self.models: List[Any] = []
        self.problems: List[str] = []

    @property
    def commits(self) -> int:
        return len(self.labels)

    def record(self, model: Any, label: str) -> Dict[str, Any]:
        """The suppressed ``commit``: capture images, return the info dict a save would."""
        from core.operations import journal_images as images

        self.labels.append(label)
        if not any(m is model for m in self.models):
            self.models.append(model)
        try:
            after = images.split_model(self._serialize(model))
            if self.before is None and self._committed is not None:
                self.before = images.split_model(self._committed(model))
            self.after = after
        except Exception as exc:  # noqa: BLE001 - the preview still reports what it can
            logger.warning("preview: image capture failed for %s: %s", label, exc)
            self.problems.append(f"{label}: {type(exc).__name__}: {exc}")
        return {"preview_commit_suppressed": True}


_PREVIEW: "contextvars.ContextVar[Optional[PreviewCapture]]" = contextvars.ContextVar(
    "write_lane_preview", default=None
)


def preview_active() -> bool:
    """True while a :func:`preview_scope` is open in this context (commits are suppressed)."""
    return _PREVIEW.get() is not None


@contextlib.contextmanager
def preview_scope(capture: PreviewCapture) -> Iterator[PreviewCapture]:
    """Suppress ``commit`` for the block: nothing is saved, the images go to *capture*."""
    token = _PREVIEW.set(capture)
    try:
        yield capture
    finally:
        _PREVIEW.reset(token)


def _cfg(key: str, default: Any) -> Any:
    try:
        from core.config.config_manager import config

        value = config.get(f"model_write.{key}", default)
        return default if value is None else value
    except Exception:  # noqa: BLE001 - config is optional here
        return default


#: Per-tool-call collector (set by the dispatcher around a write handler). ``commit`` appends
#: pacing / post-write-health notes here, so they reach the result even where the manager
#: discards ``commit``'s return value.
_CALL_NOTES: "contextvars.ContextVar[Optional[Dict[str, Any]]]" = contextvars.ContextVar(
    "write_lane_call_notes", default=None
)

_MAX_REPORTED_ERRORS = 10


@contextlib.contextmanager
def call_notes() -> Iterator[Dict[str, Any]]:
    """Collect the lane's notes for the commits made inside the block."""
    notes: Dict[str, Any] = {}
    token = _CALL_NOTES.set(notes)
    try:
        yield notes
    finally:
        _CALL_NOTES.reset(token)


def merge_notes(result: Any, notes: Dict[str, Any]) -> Any:
    """Merge collected notes into a handler result (never overwrites a handler key)."""
    if not isinstance(result, dict) or not notes:
        return result
    for key, value in notes.items():
        result.setdefault(key, value)
    return result


def _note(**kwargs: Any) -> None:
    notes = _CALL_NOTES.get()
    if notes is None:
        return
    for key, value in kwargs.items():
        if key == "post_write_errors":
            merged = list(notes.get(key) or [])
            for item in value:
                if item not in merged and len(merged) < _MAX_REPORTED_ERRORS:
                    merged.append(item)
            notes[key] = merged
        elif key == "paced_ms":
            notes[key] = int(notes.get(key, 0)) + int(value)
        else:
            notes[key] = value


def _error_items(
    before: Optional[Dict[str, str]], after: Optional[Dict[str, str]]
) -> Optional[List[Dict[str, str]]]:
    """``[{object, message}]`` for errors in *after* that are not in *before*."""
    if before is None or after is None:
        return None
    items: List[Dict[str, str]] = []
    for key, msg in after.items():
        if key in before:
            continue
        label, sep, rest = str(msg).partition(": ")
        items.append({"object": label, "message": (rest if sep else str(msg))[:300]})
    return sorted(items, key=lambda i: i["object"])


def _journal():
    """The write journal module, or None if it cannot be imported."""
    try:
        from core.operations import write_journal

        return write_journal
    except Exception as e:  # noqa: BLE001 - the journal must never break a write
        logger.debug("write journal unavailable: %s", e)
        return None


def _connection_state():
    try:
        from core.infrastructure.connection_state import connection_state

        return connection_state
    except Exception:  # noqa: BLE001
        return None


class ModelWriteLane:
    """Serialised SaveChanges + verify-after-write + pacing + invalidation."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._last_save_at = 0.0
        self.saves = 0
        self._save_times: Deque[float] = deque(maxlen=256)
        self._error_baseline: Optional[Dict[str, str]] = None
        self._baseline_at = 0.0

    @property
    def lock(self) -> "threading.RLock":
        return self._lock

    # ------------------------------------------------------------------ health
    def check_health(self) -> Optional[Dict[str, Any]]:
        """Cheap readiness probe; None when it cannot be evaluated (offline/tests)."""
        cs = _connection_state()
        if cs is None:
            return None
        try:
            if not cs.is_connected() or getattr(cs, "query_executor", None) is None:
                return None
            probe = getattr(cs, "probe_connection_health", None)
            if not callable(probe):
                return None
            return probe(timeout_s=float(_cfg("health_timeout_s", 5.0)))
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

    def _after_save(self, label: str) -> None:
        self._last_save_at = time.monotonic()
        self._save_times.append(self._last_save_at)
        self.saves += 1
        cs = _connection_state()
        bump = getattr(cs, "bump_model_generation", None) if cs is not None else None
        if callable(bump):
            try:
                bump(f"tom_write:{label}")
            except Exception as e:  # noqa: BLE001
                logger.debug("bump_model_generation failed: %s", e)

    def _loop_pause(self) -> None:
        pause = float(_cfg("loop_pause_s", 0.1) or 0.0)
        if pause <= 0:
            return
        remaining = pause - (time.monotonic() - self._last_save_at)
        if remaining > 0:
            time.sleep(min(remaining, 5.0))

    # ------------------------------------------------- pacing + health (F-25 / S-12)
    def writes_in_last(self, seconds: float) -> int:
        cutoff = time.monotonic() - seconds
        return sum(1 for t in self._save_times if t >= cutoff)

    @staticmethod
    def _is_live() -> bool:
        """A live Desktop connection exists (pacing / health only matter there)."""
        cs = _connection_state()
        if cs is None:
            return False
        try:
            return bool(cs.is_connected()) and getattr(cs, "query_executor", None) is not None
        except Exception:  # noqa: BLE001
            return False

    def _single_write_pause(self) -> float:
        """Token bucket for consecutive single writes: ``pace_max_saves`` saves inside
        ``pace_window_s`` insert ``pace_pause_s`` before the next one. Returns seconds slept."""
        if not bool(_cfg("pace_single_writes", True)):
            return 0.0
        window = float(_cfg("pace_window_s", 10.0) or 0.0)
        limit = int(_cfg("pace_max_saves", 3))
        pause = max(
            float(_cfg("pace_pause_s", 0.5) or 0.0), float(_cfg("loop_pause_s", 0.1) or 0.0)
        )
        if window <= 0 or pause <= 0 or self.writes_in_last(window) < limit:
            return 0.0
        pause = min(pause, 5.0)
        time.sleep(pause)
        return pause

    def _error_snapshot(self) -> Optional[Dict[str, str]]:
        try:
            from core.operations import model_readback

            return model_readback.expression_errors()
        except Exception as e:  # noqa: BLE001 - health is advisory
            logger.debug("post-write error snapshot failed: %s", e)
            return None

    def _baseline_for_write(self) -> Optional[Dict[str, str]]:
        """Errors already present before this save: the previous post-write snapshot while it
        is fresh, else a new read. None when health is off / unreadable."""
        if not bool(_cfg("post_write_health", True)):
            return None
        ttl = float(_cfg("post_write_baseline_ttl_s", 120.0) or 0.0)
        if self._error_baseline is not None and (time.monotonic() - self._baseline_at) < ttl:
            return self._error_baseline
        snap = self._error_snapshot()
        if snap is not None:
            self._error_baseline, self._baseline_at = snap, time.monotonic()
        return snap

    def _post_write_health(self, baseline: Optional[Dict[str, str]], info: Dict[str, Any]) -> None:
        """After a save: report objects newly in error (``post_write_errors``) and the write
        rate. Never raises, never fails the write."""
        try:
            _note(writes_last_60s=self.writes_in_last(60.0))
            if baseline is None or not bool(_cfg("post_write_health", True)):
                return
            after = self._error_snapshot()
            if after is None:
                _note(post_write_health="unverified")
                return
            self._error_baseline, self._baseline_at = after, time.monotonic()
            new = _error_items(baseline, after)
            if new:
                info["post_write_errors"] = new[:_MAX_REPORTED_ERRORS]
                _note(post_write_errors=new, post_write_health="errors_introduced")
            else:
                _note(post_write_health="ok")
        except Exception as e:  # noqa: BLE001
            logger.debug("post-write health skipped: %s", e)

    # ------------------------------------------------------------------ commit
    def commit(
        self,
        model: Any,
        *,
        label: str,
        verify: Optional[Callable[[], Optional[bool]]] = None,
        in_loop: bool = False,
        journal_meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """``model.SaveChanges()`` inside the lane.

        Returns an info dict to merge into the caller's result (empty on a clean
        save; ``post_write_warning`` + ``client_error`` when the save raised but
        ``verify()`` proved the change landed; ``journal_warning`` when the write
        landed but could not be journaled). Re-raises the original exception
        when the write did not land or cannot be verified. Inside loops, raises
        :class:`DesktopUnresponsive` after a save whose health probe failed.
        ``journal_meta`` is stored on the journal entry (undo / replay mark the
        entries they revert or re-apply).
        """
        capture = _PREVIEW.get()
        if capture is not None:
            # F-01: a preview NEVER saves - not to Desktop, not to the offline TMDL folder.
            return capture.record(model, label)
        with self._lock:
            from core.infrastructure import offline_model, xmla_target

            if xmla_target.is_active() and not offline_model.is_active():
                # N-01: the single SaveChanges choke point; the XMLA target is read-only.
                raise xmla_target.ServiceReadOnly(xmla_target.read_only_message(label))
            if in_loop:
                self._loop_pause()
            live_single = (not in_loop) and not offline_model.is_active() and self._is_live()
            baseline: Optional[Dict[str, str]] = None
            if live_single:
                paced = self._single_write_pause()
                if paced:
                    _note(paced_ms=int(paced * 1000))
                baseline = self._baseline_for_write()
            if offline_model.is_active():
                # OFF-01: no engine to SaveChanges to; serialise the in-memory database
                # back to the TMDL folder (validated, with rollback). Git is the history,
                # so the live-write journal is not used on this lane.
                info = offline_model.persist(label)
                self._after_save(label)
                return info
            journal = _journal()
            pending = None
            if journal is not None:
                try:
                    pending = journal.begin(model, label, journal_meta)
                except Exception as je:  # noqa: BLE001 - begin() never raises; belt and braces
                    logger.debug("journal begin failed for %s: %s", label, je)
            try:
                model.SaveChanges()
            except Exception as exc:
                landed: Optional[bool] = None
                if verify is not None:
                    try:
                        landed = verify()
                    except Exception as ve:  # noqa: BLE001
                        logger.debug("post-write verify for %s failed: %s", label, ve)
                        landed = None
                if landed is True:
                    self._after_save(label)
                    logger.warning("%s", post_write_warning(label, exc))
                    info = {
                        "post_write_warning": post_write_warning(label, exc),
                        "client_error": str(exc),
                        "applied": True,
                    }
                    self._journal_finish(journal, pending, info)
                    if live_single:
                        self._post_write_health(baseline, info)
                    return info
                raise
            self._after_save(label)
            info: Dict[str, Any] = {}
            self._journal_finish(journal, pending, info)
            if live_single:
                self._post_write_health(baseline, info)
            if in_loop and bool(_cfg("health_check_in_loops", True)):
                health = self.check_health()
                if isinstance(health, dict) and health.get("success") is False:
                    raise DesktopUnresponsive(
                        f"{label}: Power BI Desktop did not answer the post-write health "
                        f"probe ({health.get('error')}); stopping further writes.",
                        health,
                    )
            return info

    @staticmethod
    def _journal_finish(journal: Any, pending: Any, info: Dict[str, Any]) -> None:
        if journal is None or pending is None:
            return
        try:
            journal.finish(pending, info)
        except Exception as je:  # noqa: BLE001 - never fail a write that landed
            logger.warning("journal finish failed: %s", je)
            info["journal_warning"] = f"The write landed but the journal failed: {je}"


_LANE: Optional[ModelWriteLane] = None
_LANE_LOCK = threading.Lock()


def get_write_lane() -> ModelWriteLane:
    global _LANE
    if _LANE is None:
        with _LANE_LOCK:
            if _LANE is None:
                _LANE = ModelWriteLane()
    return _LANE


def commit(
    model: Any,
    *,
    label: str,
    verify: Optional[Callable[[], Optional[bool]]] = None,
    in_loop: bool = False,
    journal_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Module-level shortcut for ``get_write_lane().commit(...)``."""
    return get_write_lane().commit(
        model, label=label, verify=verify, in_loop=in_loop, journal_meta=journal_meta
    )
