"""
Debug loop — closed-loop debug → fix → reload → re-verify cycle.

Drives:
    set_pbip_path -> read filter context -> run DAX (with trace) ->
    detect anomaly -> propose fix -> apply fix ->
    bridge reload (in-place, running Desktop) + bridge screenshot ->
    next iteration

Verification uses the Desktop Bridge (`17_Desktop_Bridge` / core.autonomous.
desktop_bridge): an in-place reload of the RUNNING Power BI Desktop plus a
page screenshot — no close/reopen lifecycle. The fix-application step is
pluggable via fix_strategy:

    'dry_run'   — propose only, never apply (always exits after iter 1)
    'live_tom'  — apply via 02_Model_Operations TOM CRUD; no reload needed.
                  For a deterministic anomaly (fe_dominated) it runs the
                  optimizer, PROVES the rewrite is semantically equivalent
                  (core.dax.semantic_equivalence — a rewrite that changes the
                  numbers is a failed fix and is deferred, never applied), then
                  persists the rewrite via the injector. For a
                  SEMANTIC anomaly (blank / wrong value) it defers by default;
                  only when cfg.allow_llm_fix is set does it escalate to the
                  Tier-2 LLM-in-the-loop path (_default_semantic_llm_apply):
                  ask the MCP client's model for a DAX rewrite via sampling,
                  validate it, and apply it. There is NO automatic revert — the
                  applied change is re-verified on the next loop iteration, and
                  the measure's pre-edit expression is recorded as ``original_dax``
                  (plus a ``revert_hint``) so a caller can restore it manually.
                  The LLM path falls back to a deferred marker when no MCP
                  session is available, so behaviour is unchanged without a
                  client.
    'tmdl_edit' — caller-supplied tmdl_edit_fn writes TMDL (Python API only).
                  The bridge reload can NOT apply TMDL (the MS CLI's reload
                  sends reloadModelDefinition:false), so the loop stops with
                  final_status 'awaiting_reopen': close Desktop WITHOUT saving
                  and reopen (17_Desktop_Bridge open) before re-running (J-26).
    'ask_user'  — bail to caller with proposed_fix + resume_token (not
                  yet wired through MCP — placeholder for v1.1)

Gating (review J-09): fix_strategy='live_tom' writes live measures, so the loop
passes the standard destructive-op gate (``require_autonomous_mode``, severity
DESTRUCTIVE) BEFORE the first iteration; ``allow_llm_fix`` additionally needs an
explicit ``confirm_destructive`` under WARN / STRICT (an LLM-written body replacing
a live measure is never auto-cleared there). Gate notices are merged into the
result (``merge_gate_info``).

For v1, the loop only auto-applies in 'live_tom' and 'dry_run' modes.
'tmdl_edit' requires the caller to supply tmdl_edit_fn; 'ask_user'
returns a structured "awaiting fix approval" response.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.autonomous import desktop_bridge
from core.utilities.pbip_utils import resolve_pbip_paths

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class DebugStep:
    """One iteration of the debug loop."""

    iteration: int
    measure_name: Optional[str]
    page: str
    visual: str
    dax: Optional[str] = None
    filter_context: Dict[str, Any] = field(default_factory=dict)
    result: Dict[str, Any] = field(default_factory=dict)
    anomaly: Optional[Dict[str, Any]] = None
    proposed_fix: Optional[Dict[str, Any]] = None
    applied_fix: Optional[Dict[str, Any]] = None
    screenshot_path: Optional[str] = None
    duration_seconds: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DebugLoopConfig:
    pbip_path: str
    page: str
    visual: str
    measure: Optional[str] = None
    expected: Optional[float] = None
    expected_predicate: Optional[Callable[[float], bool]] = None
    max_iterations: int = 5
    take_screenshot: bool = True
    trace: bool = True
    fix_strategy: str = "ask_user"  # 'dry_run' | 'live_tom' | 'tmdl_edit' | 'ask_user'
    tmdl_edit_fn: Optional[Callable[[Path, Dict[str, Any]], Dict[str, Any]]] = None
    # live_tom: optional injected applier ``(proposal, cfg, paths) -> dict``.
    # Defaults to _default_live_tom_apply (optimizer + injector + baseline guard).
    # Exposed for testing and for callers that want a custom apply strategy.
    live_tom_apply_fn: Optional[Callable[[Dict[str, Any], Any, Any], Dict[str, Any]]] = None
    # Opt-in gate for the Tier-2 LLM-in-the-loop semantic rewrite. When False
    # (default), a SEMANTIC anomaly (blank / wrong value) under live_tom defers
    # with a proposed_fix instead of auto-applying an LLM-generated rewrite —
    # the caller must explicitly opt in before the loop lets an LLM edit the
    # model. fe_dominated always takes the deterministic optimizer path
    # regardless of this flag.
    allow_llm_fix: bool = False
    # Retry a transient debug-op failure this many times before aborting the
    # whole iteration budget (a CLR/RPC hiccup right after a SaveChanges is
    # common and self-heals).
    debug_retry_attempts: int = 2
    screenshot_dir: Optional[str] = None
    wait_timeout_seconds: float = 180.0
    # Tolerance for expected-value comparison. Final tolerance is
    #   max(abs_tolerance, rel_tolerance * abs(expected))
    # Defaults: abs=1e-6 (enough for currency values), rel=1e-9 (tight for
    # very-small-scale values like ppm where 1e-12 vs 2e-12 should not compare
    # equal). Expose both so callers can tune per-domain without patching.
    abs_tolerance: float = 1e-6
    rel_tolerance: float = 1e-9
    # J-09: the break-glass for the destructive-op gate (MCP arg
    # ``confirm_destructive``); only an explicit True counts.
    confirm_destructive: bool = False


# J-26: max_iterations is clamped to this range (negative used to run 0
# iterations and still report "max_iterations reached").
MAX_ITERATIONS_CAP = 20

# Anomalies that name no concrete defect — never "fixed" (an LLM rewrite of a
# measure whose value column could not even be located is a phantom fix, J-10).
_NON_ACTIONABLE_ANOMALIES = frozenset({"measure_not_located", "expected_ambiguous"})

_GATE_OP = "09_Debug_Operations:debug_loop"


def _loop_gate(
    cfg: "DebugLoopConfig",
) -> "tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]":
    """``(block, gate_info)`` for the configured fix strategy (J-09)."""
    if cfg.fix_strategy != "live_tom":
        return None, None
    # N-01: live_tom writes the connected model; never the published model of the XMLA target.
    from core.infrastructure import offline_model, xmla_target

    if xmla_target.is_active() and not offline_model.is_active():
        refused = xmla_target.refuse_write("09_Debug_Operations", "debug_loop(live_tom)", {})
        if refused is not None:
            refused["final_status"] = "gate_blocked"
            return refused, None
    from core.autonomous.gating import (
        GATE_POLICY_STRICT,
        GATE_POLICY_WARN,
        Severity,
        current_policy,
        require_autonomous_mode,
    )

    gate_args = {
        "confirm_destructive": cfg.confirm_destructive is True,
        "measure_name": cfg.measure,
    }
    if cfg.allow_llm_fix and cfg.confirm_destructive is not True:
        policy = current_policy()
        if policy in (GATE_POLICY_WARN, GATE_POLICY_STRICT):
            return (
                {
                    "success": False,
                    "error_type": "confirm_destructive_required",
                    "error": (
                        f"allow_llm_fix lets an LLM-written DAX body replace a live measure; "
                        f"policy {policy} requires confirm_destructive=true for it."
                    ),
                    "blocked_op": f"{_GATE_OP}(allow_llm_fix)",
                    "severity": "destructive",
                    "policy": policy,
                },
                None,
            )
    gate = require_autonomous_mode(f"{_GATE_OP}(live_tom)", gate_args, Severity.DESTRUCTIVE)
    if gate is not None and gate.get("success") is False:
        return gate, None
    return None, gate


def _apply_gate_args(cfg: "DebugLoopConfig", measure_name: str) -> Dict[str, Any]:
    """``gate_args`` for ``MeasureApplier.apply`` (WP6 E-* gated the applier).

    Once ``run_debug_loop`` has passed the loop's own DESTRUCTIVE gate for
    ``live_tom`` (explicit ``confirm_destructive`` or an elicitation-confirmed
    STRICT block), that authorisation covers the loop's writes: the applier gets
    ``confirm_destructive=True`` so it neither re-blocks under STRICT nor asks
    the user again per iteration. Called outside an authorised loop, the
    caller's own ``confirm_destructive`` is passed through unchanged.
    """
    authorised = getattr(cfg, "_gate_authorised", False) is True
    return {
        "confirm_destructive": authorised or cfg.confirm_destructive is True,
        "measure_name": measure_name,
        "authorised_by": _GATE_OP if authorised else None,
    }


def _cancelled() -> bool:
    try:
        from server.request_context import is_cancelled

        return bool(is_cancelled())
    except Exception:  # noqa: BLE001 - core must not hard-depend on server
        return False


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_debug_loop(cfg: DebugLoopConfig, *, connection_state) -> Dict[str, Any]:
    """Execute the closed-loop debug cycle.

    Returns a dict:
        {
            "success": bool,                # True iff anomaly == None at end
            "iterations": int,              # actual iterations run
            "history": list[DebugStep dicts],
            "final_status": str,            # 'resolved' | 'unresolved' |
                                            # 'aborted_no_fix' | 'lifecycle_failed'
            "error": str | None,
        }
    """
    history: List[DebugStep] = []

    block, gate_info = _loop_gate(cfg)
    if block is not None:
        out = _envelope(
            success=False,
            iterations=0,
            history=history,
            final_status="gate_blocked",
            error=block.get("error"),
        )
        out.update({k: v for k, v in block.items() if k not in ("success", "error")})
        return out
    if cfg.fix_strategy == "live_tom":
        # The loop gate passed: its authorisation is handed to the applier.
        cfg._gate_authorised = True

    try:
        max_iters = int(cfg.max_iterations)
    except (TypeError, ValueError):
        max_iters = 5
    clamped = max(1, min(MAX_ITERATIONS_CAP, max_iters))

    result = _run_iterations(cfg, history, clamped)
    if clamped != max_iters:
        result["max_iterations_clamped"] = {"requested": cfg.max_iterations, "used": clamped}
    if gate_info:
        from core.autonomous.gating import merge_gate_info

        merge_gate_info(result, gate_info)
    return result


def _run_iterations(
    cfg: DebugLoopConfig, history: List[DebugStep], max_iters: int
) -> Dict[str, Any]:
    first_original: Dict[str, Any] = {}
    try:
        paths = resolve_pbip_paths(cfg.pbip_path)
    except FileNotFoundError as e:
        return _envelope(
            success=False,
            iterations=0,
            history=history,
            final_status="aborted_no_path",
            error=str(e),
        )

    for i in range(1, max_iters + 1):
        if _cancelled():
            # J-13: the dispatcher gave up on this call — never keep applying
            # live-TOM rewrites after the caller was told it failed.
            return _envelope(
                success=False,
                iterations=i - 1,
                history=history,
                final_status="cancelled",
                error="the call was cancelled / timed out; no further iteration was run",
            )
        step_started = time.time()
        step = DebugStep(
            iteration=i,
            measure_name=cfg.measure,
            page=cfg.page,
            visual=cfg.visual,
        )
        history.append(step)

        # 1. Bust VQB cache so filter context reflects post-edit state.
        _bust_vqb_cache_safe(str(paths.pbip_file) if paths.pbip_file else cfg.pbip_path)

        # 2. Run debug visual op (handles filter context + DAX execution + trace).
        # Retry a TRANSIENT failure (CLR/RPC hiccup right after a concurrent
        # SaveChanges, momentary connection blip) a few times before treating
        # it as fatal — otherwise one blip burns the whole iteration budget.
        debug_args = _build_debug_args(cfg, paths)
        debug_result = _invoke_debug_visual(debug_args)
        attempts = 0
        while (
            not debug_result.get("success")
            and _is_transient_debug_error(debug_result)
            and attempts < cfg.debug_retry_attempts
        ):
            attempts += 1
            time.sleep(min(2.0 * attempts, 5.0))
            debug_result = _invoke_debug_visual(debug_args)
        if attempts:
            debug_result.setdefault("retry_attempts", attempts)
        step.result = debug_result
        step.dax = debug_result.get("dax_query") or debug_result.get("query")
        step.filter_context = {
            "report_filters": debug_result.get("report_filters"),
            "page_filters": debug_result.get("page_filters"),
            "visual_filters": debug_result.get("visual_filters"),
            "slicers": debug_result.get("slicers"),
        }

        if not debug_result.get("success"):
            step.error = debug_result.get("error", "debug visual op failed")
            step.duration_seconds = time.time() - step_started
            # Hard error from the debug op — there's nothing to fix yet, bail.
            return _envelope(
                success=False,
                iterations=i,
                history=history,
                final_status="aborted_debug_error",
                error=step.error,
            )

        # 3. Detect anomaly
        step.anomaly = _detect_anomaly(debug_result, cfg)

        # 4. Take screenshot regardless of anomaly status (gives the agent
        #    visual context even on success).
        if cfg.take_screenshot:
            step.screenshot_path = _capture_visual_screenshot(
                pbip_file=paths.pbip_file or Path(cfg.pbip_path),
                screenshot_dir=cfg.screenshot_dir,
                iteration=i,
                page=cfg.page,
            )

        step.duration_seconds = time.time() - step_started

        # 5. Done?
        if step.anomaly is None:
            return _envelope(
                success=True,
                iterations=i,
                history=history,
                final_status="resolved",
                error=None,
            )

        if step.anomaly.get("kind") in _NON_ACTIONABLE_ANOMALIES:
            return _envelope(
                success=False,
                iterations=i,
                history=history,
                final_status="aborted_indeterminate",
                error=step.anomaly.get("detail"),
            )

        # 6. Propose + apply fix
        step.proposed_fix = _propose_fix(step.anomaly, cfg, debug_result)
        if _cancelled():
            return _envelope(
                success=False,
                iterations=i,
                history=history,
                final_status="cancelled",
                error="the call was cancelled / timed out before the fix was applied",
            )
        step.applied_fix = _apply_fix(step.proposed_fix, cfg, paths)
        applied = step.applied_fix
        if isinstance(applied, dict) and applied.get("applied"):
            # J-26: every iteration re-optimizes its own previous rewrite; the
            # revert target is the FIRST pre-edit expression, not the latest.
            if "original_dax" not in first_original and applied.get("original_dax") is not None:
                first_original["original_dax"] = applied["original_dax"]
            if "original_dax" in first_original:
                applied["previous_dax"] = applied.get("original_dax")
                applied["original_dax"] = first_original["original_dax"]
            if cfg.fix_strategy == "live_tom":
                _note_live_write()

        if step.applied_fix is None or step.applied_fix.get("status") == "deferred":
            # Either dry_run / ask_user / no actionable fix — caller decides.
            # Normalize to the canonical vocabulary documented in the docstring:
            # {'resolved', 'unresolved', 'aborted_no_fix', 'lifecycle_failed'}.
            # Fix-strategy status ('deferred', 'ask_user', etc.) is preserved on
            # step.applied_fix for callers that want detail.
            return _envelope(
                success=False,
                iterations=i,
                history=history,
                final_status="aborted_no_fix",
                error=None,
            )

        if not step.applied_fix.get("success"):
            return _envelope(
                success=False,
                iterations=i,
                history=history,
                final_status="aborted_fix_failed",
                error=step.applied_fix.get("error"),
            )

        if step.applied_fix.get("requires_reopen"):
            # J-26: a TMDL disk edit cannot be applied by the bridge reload.
            out = _envelope(
                success=False,
                iterations=i,
                history=history,
                final_status="awaiting_reopen",
                error=None,
            )
            out["hint"] = (
                "The TMDL fix is on disk but NOT live: the bridge reload cannot apply TMDL. "
                "Ask the user to close Power BI Desktop WITHOUT saving, reopen it "
                "(17_Desktop_Bridge operation=open), then re-run the loop."
            )
            return out

        # 7. Reload the RUNNING Desktop only when the fix needed disk
        #    persistence. Live-TOM fixes propagate instantly — no reload.
        if step.applied_fix.get("requires_reload"):
            reload_result = _reload(paths, cfg)
            if not reload_result.get("success"):
                return _envelope(
                    success=False,
                    iterations=i,
                    history=history,
                    final_status="reload_failed",
                    # op_reload's specific reason (unsaved_changes, RELOAD_REJECTED, ...)
                    error=reload_result.get("error"),
                )

    # max_iterations exhausted without resolution
    out = _envelope(
        success=False,
        iterations=max_iters,
        history=history,
        final_status="unresolved",
        error="max_iterations reached without anomaly resolution",
    )
    if "original_dax" in first_original:
        out["original_dax"] = first_original["original_dax"]
    return out


def _note_live_write() -> None:
    """Record the loop's live-TOM write in the bridge's edit ledger (J-01)."""
    try:
        from core.autonomous.edit_ledger import record_live_write

        record_live_write(f"{_GATE_OP}(live_tom)")
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _envelope(
    *,
    success: bool,
    iterations: int,
    history: List[DebugStep],
    final_status: str,
    error: Optional[str],
) -> Dict[str, Any]:
    return {
        "success": success,
        "iterations": iterations,
        "history": [s.to_dict() for s in history],
        "final_status": final_status,
        "error": error,
    }


def _build_debug_args(cfg: DebugLoopConfig, paths) -> Dict[str, Any]:
    """Translate DebugLoopConfig into the args 09_Debug_Operations:visual expects."""
    args: Dict[str, Any] = {
        "page_name": cfg.page,
        "visual_id": cfg.visual,
        "execute_query": True,
        "trace": cfg.trace,
        "compact": False,
        # The handler reads pbip from connection_state; ensure it's set.
        "pbip_path": str(paths.pbip_file) if paths.pbip_file else None,
    }
    if cfg.measure:
        args["measure_name"] = cfg.measure
    return args


def _invoke_debug_visual(args: Dict[str, Any]) -> Dict[str, Any]:
    """Call into 09_Debug_Operations:visual via the handler entry point."""
    try:
        from server.handlers.debug_handler import handle_debug_visual
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"debug handler unavailable: {e}"}
    try:
        return handle_debug_visual(args) or {"success": False, "error": "empty result"}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"handle_debug_visual raised: {e}"}


_TRANSIENT_ERROR_MARKERS = (
    "timeout",
    "connection",
    "rpc",
    "transport",
    "busy",
    "temporarily",
    "target of an invocation",
    "being used by another",
)


def _is_transient_debug_error(debug_result: Dict[str, Any]) -> bool:
    """Heuristic: does a failed debug result look transient (worth a retry)?

    A structured not-found / invalid-args error is permanent and shouldn't be
    retried; a CLR/connection/timeout signature is likely transient.
    """
    et = str(debug_result.get("error_type") or "").lower()
    if et in ("not_connected", "connection_lost", "timeout", "query_execution_error"):
        return True
    msg = str(debug_result.get("error") or "").lower()
    return any(marker in msg for marker in _TRANSIENT_ERROR_MARKERS)


def _bust_vqb_cache_safe(pbip_path: str) -> None:
    try:
        from server.handlers.debug_handler import bust_vqb_cache

        bust_vqb_cache(pbip_path)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Anomaly detection
# ---------------------------------------------------------------------------


def _norm_col(name: Any) -> str:
    """Case/format-insensitive column key: ``Sales[Total Sales]`` / ``[Total_Sales]``
    / ``Total Sales`` all normalise to ``totalsales``."""
    text = str(name or "")
    if "[" in text and text.rstrip().endswith("]"):
        text = text[text.index("[") + 1 : text.rstrip().rindex("]")]
    return re.sub(r"[\s_\]\[']+", "", text).casefold()


def _measure_column(rows: List[Dict[str, Any]], measure: Optional[str]) -> Optional[str]:
    """The result column holding *measure*'s values, or None.

    Matches the raw DAX key (``[Total_Sales]``, ``Sales[Total Sales]``) and the
    display name, case- and underscore-insensitively. Without a measure, a
    single-column result is unambiguous.
    """
    first = rows[0] if rows and isinstance(rows[0], dict) else None
    if not first:
        return None
    keys = list(first.keys())
    if measure:
        _t, bare = _resolve_measure_table_static(measure)
        want = {_norm_col(measure), _norm_col(bare)}
        for k in keys:
            if _norm_col(k) in want:
                return k
    if len(keys) == 1:
        return keys[0]
    return None


def _measure_values(rows: Any, measure: Optional[str]) -> "tuple[Optional[str], List[Any]]":
    if not isinstance(rows, list) or not rows:
        return None, []
    if not isinstance(rows[0], dict):
        return None, list(rows)
    col = _measure_column(rows, measure)
    if col is None:
        return None, []
    return col, [r.get(col) if isinstance(r, dict) else None for r in rows]


def _as_float(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _detect_anomaly(debug_result: Dict[str, Any], cfg: DebugLoopConfig) -> Optional[Dict[str, Any]]:
    """Return an anomaly dict or None when the result is acceptable.

    Reads the column that holds ``cfg.measure`` (J-10: the old code read the first
    value of the first row, which on any chart / matrix is a group-by LABEL) and
    checks every row:
      - BLANK when every value is blank; NaN / Inf in any row
      - expected-value mismatch (``cfg.expected`` needs a single-row result)
      - ``cfg.expected_predicate`` must hold for every row
      - SE/FE imbalance (FE > 5x SE) when trace is on
    """
    execution = debug_result.get("execution")
    rows = (execution if isinstance(execution, dict) else {}).get("rows")
    if rows is None:
        rows = debug_result.get("rows")

    if rows and isinstance(rows, list) and isinstance(rows[0], dict):
        col, values = _measure_values(rows, cfg.measure)
        if col is None:
            return {
                "kind": "measure_not_located",
                "value": None,
                "columns": list(rows[0].keys()),
                "detail": (
                    f"Could not find the column of measure {cfg.measure!r} in the visual "
                    f"result (columns: {list(rows[0].keys())}); no value to judge."
                ),
            }
    else:
        col, values = None, (list(rows) if isinstance(rows, list) else [])

    # 1. BLANK / NaN / Inf
    if not values or all(v is None for v in values):
        return {
            "kind": "blank",
            "value": None,
            "column": col,
            "detail": "Measure value is BLANK/None",
        }
    for idx, v in enumerate(values):
        f = _as_float(v)
        if f is None:
            continue
        if f != f:  # NaN
            return {
                "kind": "nan",
                "value": v,
                "row": idx,
                "column": col,
                "detail": "Measure value is NaN",
            }
        if f in (float("inf"), float("-inf")):
            return {
                "kind": "infinity",
                "value": v,
                "row": idx,
                "column": col,
                "detail": "Measure value is Inf",
            }

    primary = values[0]

    # 2. Expected mismatch — use configurable abs + rel tolerance.
    if cfg.expected is not None:
        if len(values) > 1:
            return {
                "kind": "expected_ambiguous",
                "value": None,
                "rows": len(values),
                "detail": (
                    f"expected={cfg.expected} is a single value but the visual returned "
                    f"{len(values)} rows; use expected_predicate (checked per row) or a "
                    "single-value visual."
                ),
            }
        try:
            actual = float(primary)
            expected_f = float(cfg.expected)
            tolerance = max(cfg.abs_tolerance, cfg.rel_tolerance * abs(expected_f))
            if abs(actual - expected_f) > tolerance:
                return {
                    "kind": "expected_mismatch",
                    "value": primary,
                    "expected": cfg.expected,
                    "tolerance": tolerance,
                    "column": col,
                    "detail": f"Got {primary}, expected {cfg.expected} (tolerance {tolerance:g})",
                }
        except (TypeError, ValueError):
            return {
                "kind": "type_mismatch",
                "value": primary,
                "expected": cfg.expected,
                "detail": f"Cannot compare {primary!r} to expected={cfg.expected}",
            }

    if cfg.expected_predicate is not None:
        for idx, v in enumerate(values):
            try:
                ok = cfg.expected_predicate(float(v))
            except Exception as e:  # noqa: BLE001
                return {
                    "kind": "predicate_error",
                    "value": v,
                    "row": idx,
                    "detail": f"expected_predicate raised: {e}",
                }
            if not ok:
                return {
                    "kind": "predicate_failed",
                    "value": v,
                    "row": idx,
                    "detail": "expected_predicate returned False",
                }

    # 3. SE/FE imbalance — only when trace enabled and trace data present
    perf = debug_result.get("performance") or {}
    se_ms = perf.get("storage_engine_ms")
    fe_ms = perf.get("formula_engine_ms")
    if isinstance(se_ms, (int, float)) and isinstance(fe_ms, (int, float)) and se_ms > 0:
        if fe_ms > 5 * se_ms:
            return {
                "kind": "fe_dominated",
                "se_ms": se_ms,
                "fe_ms": fe_ms,
                "detail": (
                    f"Formula engine {fe_ms}ms dominates storage engine {se_ms}ms "
                    f"(>5x). Likely CALCULATE/iterator inefficiency."
                ),
            }

    return None


def _primary_scalar(rows: Any, measure: Optional[str] = None) -> Any:
    """The first value of the measure's column (single-column results: that column)."""
    if not rows:
        return None
    if isinstance(rows, list) and rows:
        first = rows[0]
        if isinstance(first, dict) and first:
            _col, values = _measure_values(rows, measure)
            return values[0] if values else None
        return first
    return None


# ---------------------------------------------------------------------------
# Fix proposal + application
# ---------------------------------------------------------------------------


def _propose_fix(
    anomaly: Dict[str, Any], cfg: DebugLoopConfig, debug_result: Dict[str, Any]
) -> Dict[str, Any]:
    """Return a structured fix proposal. Real proposals come from the
    optimizer/analyzer; for v1 this returns a metadata stub the caller
    (or future agent step) can act on.
    """
    return {
        "anomaly_kind": anomaly.get("kind"),
        "measure": cfg.measure,
        "page": cfg.page,
        "visual": cfg.visual,
        "summary": _summarize_anomaly(anomaly),
        # Context the Tier-2 LLM semantic-fix path needs to propose a rewrite:
        # the measure's current DAX and the observed vs. expected value.
        "current_dax": debug_result.get("measure_dax")
        or debug_result.get("dax_expression")
        or debug_result.get("expression"),
        "observed_value": anomaly.get("value"),
        "expected": anomaly.get("expected"),
        # J-10: the equivalence proof runs through the visual's own query.
        "visual_dax": debug_result.get("dax_query") or debug_result.get("query"),
        "filter_context": {
            "report_filters": debug_result.get("report_filters"),
            "page_filters": debug_result.get("page_filters"),
            "visual_filters": debug_result.get("visual_filters"),
            "slicers": debug_result.get("slicers"),
        },
        # When the loop integrates the optimizer this becomes a concrete
        # rewrite suggestion. For v1 it's a hint the caller can use.
        "actionable": cfg.fix_strategy in ("live_tom", "tmdl_edit"),
    }


def _summarize_anomaly(anomaly: Dict[str, Any]) -> str:
    return f"{anomaly.get('kind')}: {anomaly.get('detail')}"


# ---------------------------------------------------------------------------
# Measure/table resolution + current-DAX capture (shared by both apply paths)
# ---------------------------------------------------------------------------

# 'Table[Name]' or "'Table'[Name]" — mirrors core/codemods/transaction.py's
# target parser but tolerates the optional single-quoted table form the debug
# layer produces. Implemented locally (do NOT import from codemods).
_QUALIFIED_MEASURE_RE = re.compile(r"^\s*'?([^'\[\]]+?)'?\s*\[([^\[\]]+)\]\s*$")


def _load_model_measures() -> List[Dict[str, Any]]:
    """Return the live model's measures as ``[{Name, Table, Expression}, ...]``.

    Uses the cheapest existing seam — ``query_executor.enumerate_measures_tom``
    — via the connection_state singleton. Best-effort: any failure (not
    connected, CLR hiccup) returns ``[]`` so callers degrade to "could not
    resolve". Split out as a module-level function so tests can monkeypatch it
    without touching CLR.
    """
    try:
        from core.infrastructure.connection_state import connection_state

        qe = getattr(connection_state, "query_executor", None)
        if qe is None:
            return []
        result = qe.enumerate_measures_tom() or {}
    except Exception:  # noqa: BLE001 - CLR/connection edge; degrade gracefully
        return []
    if not result.get("success"):
        return []
    return result.get("rows") or []


def _resolve_measure_table_static(measure_ref: Optional[str]) -> "tuple[Optional[str], str]":
    """``(table|None, bare_name)`` by parsing only (no model lookup)."""
    ref = (measure_ref or "").strip()
    m = _QUALIFIED_MEASURE_RE.match(ref)
    if m:
        return (m.group(1).strip() or None), m.group(2).strip()
    return None, ref.strip("[]")


def _resolve_measure_table(measure_ref: Optional[str]) -> "tuple[Optional[str], str]":
    """Resolve a measure reference into ``(table, bare_measure_name)``.

    - ``'Table[Name]'`` / ``"'Table'[Name]'"`` → parse directly to
      ``(Table, Name)`` (no model lookup needed).
    - bare ``'Name'`` → look up the owning table from the live model via
      ``_load_model_measures`` (exact name match). Returns ``(None, Name)`` when
      the table can't be resolved so the caller can defer rather than apply with
      an empty table name.

    Returns ``(None, "")`` for an empty/None ref.
    """
    ref = (measure_ref or "").strip()
    if not ref:
        return None, ""

    m = _QUALIFIED_MEASURE_RE.match(ref)
    if m:
        table = m.group(1).strip()
        name = m.group(2).strip()
        return (table or None), name

    # Bare name — resolve the owning table from the live model.
    for row in _load_model_measures():
        if str(row.get("Name") or "") == ref:
            table = str(row.get("Table") or "").strip()
            return (table or None), ref
    return None, ref


def _current_measure_dax(table: Optional[str], measure_name: str) -> Optional[str]:
    """Fetch a measure's CURRENT expression from the live model, or None.

    Uses the same ``_load_model_measures`` seam as table resolution. Best-effort:
    returns None when it can't be read (not connected, no match) — the caller
    still applies but records ``original_dax=None``.
    """
    if not measure_name:
        return None
    for row in _load_model_measures():
        if str(row.get("Name") or "") != measure_name:
            continue
        if table and str(row.get("Table") or "").strip() != table:
            continue
        expr = row.get("Expression")
        return str(expr) if expr is not None else None
    return None


def _define_measure_override(visual_dax: str, table: str, measure: str, body: str) -> str:
    """The visual query with ``MEASURE 'T'[m] = <body>`` defined in query scope.

    A query-scoped ``DEFINE MEASURE`` of the SAME name overrides the model
    measure for this query, so the visual's grouping + filters evaluate the
    candidate body.
    """
    from core.dax.dax_names import bracket, quote_table

    definition = f"MEASURE {quote_table(table)}{bracket(measure)} = ( {body} )"
    stripped = visual_dax.lstrip()
    if stripped[:6].upper() == "DEFINE" and (len(stripped) == 6 or not stripped[6].isalnum()):
        return "DEFINE\n" + definition + "\n" + stripped[6:]
    return "DEFINE\n" + definition + "\n" + visual_dax


def _check_rewrite_equivalence(
    original_measure: str,
    candidate_dax: str,
    *,
    tolerance: float = 1e-6,
    visual_dax: Optional[str] = None,
    table: Optional[str] = None,
) -> Dict[str, Any]:
    """Prove a candidate rewrite returns the SAME data as the live measure.

    J-10: the old check evaluated ``ROW("value", [m])`` at the grand total only
    (a rewrite that matched there but differed per row passed) and built
    ``['Sales'[Total Sales]]`` from a qualified ref (invalid DAX, so qualified
    refs always failed). Now:

    * the reference is the bare, bracket-escaped measure name;
    * when the visual's DAX query and the owning table are known, BOTH the
      grand total and the visual's own query (its grouping + filters, candidate
      injected with a query-scoped ``DEFINE MEASURE``) must match.

    Fails CLOSED: not connected, no executor, or a failed query all yield
    ``{"equivalent": False, "reason": "query_failed"}``. Module-level so tests
    can monkeypatch it without touching CLR.
    """
    from core.dax.dax_names import bracket
    from core.dax.semantic_equivalence import compare_rows, verdict

    _t, bare = _resolve_measure_table_static(original_measure)
    table = table or _t
    try:
        from core.infrastructure.connection_state import connection_state

        qe = getattr(connection_state, "query_executor", None)
        if qe is None:
            return verdict(equivalent=False, reason="query_failed", tolerance_used=tolerance)
        ref = bracket(bare)
        res_a = (
            qe.validate_and_execute_dax(
                f'EVALUATE ROW("value", {ref})', top_n=10, bypass_cache=True
            )
            or {}
        )
        res_b = (
            qe.validate_and_execute_dax(
                f'EVALUATE ROW("value", {candidate_dax})', top_n=10, bypass_cache=True
            )
            or {}
        )
        vis_a = vis_b = None
        if visual_dax and table:
            vis_a = qe.validate_and_execute_dax(visual_dax, 0, bypass_cache=True) or {}
            vis_b = (
                qe.validate_and_execute_dax(
                    _define_measure_override(visual_dax, table, bare, candidate_dax),
                    0,
                    bypass_cache=True,
                )
                or {}
            )
    except Exception as e:  # noqa: BLE001 - the guard must never crash the loop
        logger.debug("debug-loop equivalence check raised: %s", e)
        return verdict(equivalent=False, reason="query_failed", tolerance_used=tolerance)

    if not res_a.get("success") or not res_b.get("success"):
        v = verdict(equivalent=False, reason="query_failed", tolerance_used=tolerance)
        v["original_error"] = res_a.get("error")
        v["candidate_error"] = res_b.get("error")
        return v

    total = compare_rows(
        res_a.get("rows") or [],
        res_b.get("rows") or [],
        tolerance=tolerance,
        column_alias_map={"value": "value"},
    )
    if not total.get("equivalent") or vis_a is None:
        if vis_a is None:
            total["scope"] = "grand_total_only"
        return total
    if not vis_a.get("success") or not vis_b.get("success"):
        v = verdict(equivalent=False, reason="query_failed", tolerance_used=tolerance)
        v["scope"] = "visual_query"
        v["original_error"] = vis_a.get("error")
        v["candidate_error"] = vis_b.get("error")
        return v
    per_row = compare_rows(vis_a.get("rows") or [], vis_b.get("rows") or [], tolerance=tolerance)
    per_row["scope"] = "grand_total_and_visual_query"
    return per_row


def _apply_fix(
    proposal: Optional[Dict[str, Any]],
    cfg: DebugLoopConfig,
    paths,
) -> Optional[Dict[str, Any]]:
    """Apply the proposed fix per cfg.fix_strategy."""
    if proposal is None:
        return None

    if cfg.fix_strategy == "dry_run":
        return {"status": "deferred", "reason": "dry_run", "applied": False}

    if cfg.fix_strategy == "ask_user":
        return {
            "status": "deferred",
            "reason": "ask_user",
            "applied": False,
            "proposed_fix": proposal,
        }

    if cfg.fix_strategy == "tmdl_edit":
        if cfg.tmdl_edit_fn is None:
            return {
                "status": "deferred",
                "reason": "tmdl_edit_fn not provided",
                "applied": False,
            }
        try:
            edit_result = cfg.tmdl_edit_fn(paths.definition_path, proposal) or {}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"tmdl_edit_fn raised: {e}"}
        ok = bool(edit_result.get("success", False))
        if ok:
            try:
                from core.autonomous.edit_ledger import record_disk_write

                record_disk_write(
                    str(getattr(paths, "pbip_file", None) or cfg.pbip_path),
                    kind="tmdl",
                    source=f"{_GATE_OP}(tmdl_edit)",
                )
            except Exception:  # noqa: BLE001
                pass
        return {
            "success": ok,
            # J-26: the bridge CLI's reload sends reloadModelDefinition:false — it can
            # never apply a TMDL edit; the project must be reopened.
            "requires_reload": False,
            "requires_reopen": ok,
            "edit_result": edit_result,
        }

    if cfg.fix_strategy == "live_tom":
        apply_fn = cfg.live_tom_apply_fn or _default_live_tom_apply
        try:
            return apply_fn(proposal, cfg, paths)
        except Exception as e:  # noqa: BLE001 - apply must never crash the loop
            return {"success": False, "error": f"live_tom apply raised: {e}", "applied": False}

    return {
        "status": "deferred",
        "reason": f"unknown fix_strategy: {cfg.fix_strategy}",
        "applied": False,
    }


def _default_live_tom_apply(
    proposal: Dict[str, Any], cfg: "DebugLoopConfig", paths
) -> Dict[str, Any]:
    """Deterministic live_tom apply: optimize the measure's DAX, prove the
    rewrite is semantically equivalent, then persist it via the DAXInjector.

    The equivalence guard (``_check_rewrite_equivalence`` →
    ``core.dax.semantic_equivalence``) runs BEFORE the write: a rewrite that
    changes the numbers is a FAILED fix and is deferred with the verdict
    attached, never applied — so no revert path is needed for that case.

    There is NO automatic revert of an applied rewrite either — it is
    re-verified on the loop's next iteration (which re-runs the visual and
    re-detects any anomaly), and the measure's pre-edit expression is recorded
    as ``original_dax`` (with a ``revert_hint``) so a caller can restore it
    manually if it regresses.

    Only handles anomalies a deterministic codemod can fix (currently
    fe_dominated / anti-pattern rewrites). For a SEMANTIC anomaly (blank / wrong
    value needing new logic) it defers by default; only when ``cfg.allow_llm_fix``
    is set does it escalate to the Tier-2 LLM-in-the-loop path — a mechanical
    optimizer can't invent that fix, and an LLM editing the model must be opted
    into explicitly.
    """
    measure = proposal.get("measure")
    kind = proposal.get("anomaly_kind")
    if not measure:
        return {"status": "deferred", "reason": "no measure in proposal", "applied": False}
    if kind not in ("fe_dominated",):
        # Semantic anomaly (blank / wrong value). Gate the LLM-in-the-loop
        # rewrite behind an explicit opt-in — without it, defer with the
        # proposal so the agent/caller decides (pre-v7.14 behaviour).
        if not cfg.allow_llm_fix:
            return {
                "status": "deferred",
                "reason": (
                    f"anomaly '{kind}' needs new DAX logic; pass allow_llm_fix=true "
                    "to enable the LLM-in-the-loop rewrite"
                ),
                "applied": False,
                "proposed_fix": proposal,
            }
        return _default_semantic_llm_apply(proposal, cfg, paths)
    try:
        from core.dax.optimizer.pipeline import OptimizationPipeline
        from core.dax.optimizer.measure_applier import MeasureApplier
        from core.infrastructure.connection_state import connection_state
    except Exception as e:  # pragma: no cover
        return {"status": "deferred", "reason": f"optimizer unavailable: {e}", "applied": False}

    # Resolve the owning table (parse 'Table[Name]' or look up a bare name in
    # the live model). Never apply with an empty table name — the injector
    # rejects it via _valid_amo_name.
    table, measure_name = _resolve_measure_table(measure)
    if not table:
        return {
            "status": "deferred",
            "reason": f"could not resolve owning table for {measure!r}",
            "applied": False,
        }

    # Capture the pre-edit expression BEFORE optimizing/applying so a caller can
    # revert manually. Best-effort: None when it can't be read.
    original_dax = _current_measure_dax(table, measure_name)

    pipeline = OptimizationPipeline(connection_state)
    try:
        # dry_run=True: the pipeline only COMPUTES the rewrite. The single write
        # path is the MeasureApplier below — dry_run=False would make the
        # pipeline apply internally too, writing the same DAX twice.
        opt = pipeline.optimize_measure(measure_name, table, dry_run=True)
    except Exception as e:  # noqa: BLE001
        return {"status": "deferred", "reason": f"optimize failed: {e}", "applied": False}
    if not opt or not getattr(opt, "final_dax", None):
        return {"status": "deferred", "reason": "optimizer produced no rewrite", "applied": False}

    # Semantic-equivalence guard: an fe_dominated rewrite is only a fix if it
    # returns the SAME data. Checked BEFORE the write so a non-equivalent
    # rewrite is simply never applied — no revert needed. Fails closed.
    equivalence = _check_rewrite_equivalence(
        measure_name, opt.final_dax, visual_dax=proposal.get("visual_dax"), table=table
    )
    if not equivalence.get("equivalent"):
        return {
            "status": "deferred",
            "reason": (
                "optimizer rewrite is not semantically equivalent "
                f"({equivalence.get('reason')}) — not applied"
            ),
            "applied": False,
            "candidate_dax": opt.final_dax,
            "equivalence": equivalence,
        }

    applier = MeasureApplier(connection_state)
    r = applier.apply(
        measure_name=measure_name,
        table_name=table,
        new_dax=opt.final_dax,
        gate_args=_apply_gate_args(cfg, measure_name),
    )
    if not r.get("success"):
        return {"success": False, "error": r.get("error", "apply failed"), "applied": False}
    return {
        "success": True,
        "applied": True,
        "requires_reload": False,
        "new_dax": opt.final_dax,
        "original_dax": original_dax,
        "revert_hint": "restore via 02_Model_Operations measure/update with original_dax",
        "equivalence": equivalence,
    }


# ---------------------------------------------------------------------------
# Tier-2: LLM-in-the-loop semantic fix
# ---------------------------------------------------------------------------


_SEMANTIC_SYSTEM_PROMPT = (
    "You are a Power BI DAX expert fixing a single measure. You will be told the "
    "measure name, its current DAX, the observed anomaly (a blank or wrong value), "
    "and the filter context. Reply with ONLY the corrected DAX expression for the "
    "measure body — no measure name, no assignment, no EVALUATE, no prose, no "
    "explanation. If you cannot determine a fix, reply with the single word NONE."
)


def _strip_dax_fences(text: str) -> str:
    """Strip markdown code fences / language tags an LLM tends to wrap DAX in."""
    if not text:
        return ""
    s = text.strip()
    if s.startswith("```"):
        # Drop the opening fence line (``` or ```dax) and the closing fence.
        lines = s.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    return s


def _default_semantic_llm_apply(
    proposal: Dict[str, Any],
    cfg: "DebugLoopConfig",
    paths,
    *,
    sampler: Optional[Callable[..., Optional[str]]] = None,
    validate_fn: Optional[Callable[[str], List[str]]] = None,
    apply_fn: Optional[Callable[..., Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Ask the MCP client's LLM for a DAX rewrite of a semantically-broken
    measure, validate the syntax, and apply it via the injector.

    Injectable seams (default to the real infra so production wiring is
    automatic, tests pass fakes):
      - ``sampler``      → server.sampling.sample_text  (returns text | None)
      - ``validate_fn``  → DaxValidator.validate_query_syntax  (→ error list)
      - ``apply_fn``     → MeasureApplier(...).apply  (→ {success, error?}); called
                           with ``gate_args`` (``_apply_gate_args``)

    Fails safe: no sampler / declined / NONE / invalid DAX → a deferred marker
    (never applies). Apply errors surface as {success: False}. When it does
    apply, the change is live-TOM so no reload is needed.
    """
    measure = proposal.get("measure")
    kind = proposal.get("anomaly_kind")
    if not measure:
        return {"status": "deferred", "reason": "no measure in proposal", "applied": False}

    if sampler is None:
        try:
            from server.sampling import sample_text as sampler  # type: ignore
        except Exception:  # noqa: BLE001
            sampler = None
    if validate_fn is None:
        try:
            from core.dax.dax_validator import DaxValidator

            validate_fn = DaxValidator.validate_query_syntax
        except Exception:  # noqa: BLE001
            validate_fn = lambda dax: []  # noqa: E731 - degrade to no syntax gate
    if apply_fn is None:
        try:
            from core.dax.optimizer.measure_applier import MeasureApplier
            from core.infrastructure.connection_state import connection_state

            apply_fn = MeasureApplier(connection_state).apply
        except Exception as e:  # noqa: BLE001
            return {"status": "deferred", "reason": f"applier unavailable: {e}", "applied": False}

    if sampler is None:
        return {
            "status": "deferred",
            "reason": "no MCP sampler available (LLM-in-the-loop needs a client session)",
            "applied": False,
            "proposed_fix": proposal,
        }

    current_dax = proposal.get("current_dax") or ""
    prompt = (
        f"Measure: {measure}\n"
        f"Anomaly: {kind} — {proposal.get('summary', '')}\n"
        f"Current DAX:\n{current_dax or '(unavailable)'}\n"
        f"Filter context: {proposal.get('filter_context', {})}\n\n"
        "Return only the corrected DAX expression for the measure body."
    )
    try:
        raw = sampler(prompt, system_prompt=_SEMANTIC_SYSTEM_PROMPT, max_tokens=800)
    except Exception as e:  # noqa: BLE001 - sampling must never crash the loop
        return {"status": "deferred", "reason": f"sampler raised: {e}", "applied": False}

    if not raw:
        return {
            "status": "deferred",
            "reason": "sampler returned no rewrite (no session / client declined)",
            "applied": False,
            "proposed_fix": proposal,
        }
    new_dax = _strip_dax_fences(raw)
    if not new_dax or new_dax.strip().upper() == "NONE":
        return {
            "status": "deferred",
            "reason": "LLM could not determine a fix (returned NONE)",
            "applied": False,
            "proposed_fix": proposal,
        }

    errors = validate_fn(new_dax) or []
    if errors:
        return {
            "status": "deferred",
            "reason": f"LLM produced invalid DAX (not applied): {errors}",
            "applied": False,
            "candidate_dax": new_dax,
        }

    # Resolve the owning table (parse 'Table[Name]' or look up a bare name in
    # the live model). Never apply with an empty table name — the injector
    # rejects it via _valid_amo_name.
    table, measure_name = _resolve_measure_table(measure)
    if not table:
        return {
            "status": "deferred",
            "reason": f"could not resolve owning table for {measure!r}",
            "applied": False,
            "candidate_dax": new_dax,
        }

    # Capture the pre-edit expression so a caller can revert manually (no
    # automatic revert — the next loop iteration re-verifies the change).
    original_dax = _current_measure_dax(table, measure_name)
    try:
        r = apply_fn(
            measure_name=measure_name,
            table_name=table,
            new_dax=new_dax,
            gate_args=_apply_gate_args(cfg, measure_name),
        )
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"apply raised: {e}", "applied": False}
    if not r.get("success"):
        return {
            "success": False,
            "error": r.get("error", "apply failed"),
            "applied": False,
            "candidate_dax": new_dax,
        }
    return {
        "success": True,
        "applied": True,
        "requires_reload": False,
        "new_dax": new_dax,
        "original_dax": original_dax,
        "revert_hint": "restore via 02_Model_Operations measure/update with original_dax",
        "fix_source": "llm",
    }


# ---------------------------------------------------------------------------
# Bridge reload (in-place, running Desktop)
# ---------------------------------------------------------------------------


def _reload(paths, cfg: DebugLoopConfig) -> Dict[str, Any]:
    """Reload the RUNNING Desktop in place via the Desktop Bridge — no
    close/reopen. Report/PBIR changes are picked up immediately."""
    pbip = str(paths.pbip_file) if paths.pbip_file else cfg.pbip_path
    result = desktop_bridge.op_reload(pbip_path=pbip, timeout=cfg.wait_timeout_seconds)
    if not result.get("success"):
        return {"success": False, "error": result.get("error"), "step": result}
    # Bust VQB cache so the next debug iteration reads post-reload filter state.
    _bust_vqb_cache_safe(pbip)
    return {"success": True, "reload": result}


# ---------------------------------------------------------------------------
# Screenshot (via bridge — captures the page on the running Desktop)
# ---------------------------------------------------------------------------


def _capture_visual_screenshot(
    *,
    pbip_file: Path,
    screenshot_dir: Optional[str],
    iteration: int,
    page: str,
) -> Optional[str]:
    """Capture the report page via the Desktop Bridge.

    ``op_screenshot`` returns ``success: True`` with an ``output`` path on
    success. Page is resolved by display name or id inside the bridge.
    """
    try:
        out_path = _next_screenshot_path(screenshot_dir, iteration)
        result = desktop_bridge.op_screenshot(
            page=page,
            output=str(out_path),
            pbip_path=str(pbip_file),
        )
        if result.get("success"):
            return str(out_path)
        logger.debug(
            "debug-loop screenshot: bridge op_screenshot failed: %s",
            result.get("error"),
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("debug-loop screenshot failed: %s", e)
    return None


def _next_screenshot_path(screenshot_dir: Optional[str], iteration: int) -> Path:
    import os
    import tempfile

    # Precedence (most specific wins):
    #   1. explicit cfg.screenshot_dir — used as-is (no subdir appending)
    #   2. MCP_AUTONOMOUS_SCREENSHOT_DIR env var — used as-is
    #   3. %TEMP%/mcp-pbi-autonomous/screenshots/ fallback
    if screenshot_dir:
        base = Path(screenshot_dir)
    elif os.environ.get("MCP_AUTONOMOUS_SCREENSHOT_DIR"):
        base = Path(os.environ["MCP_AUTONOMOUS_SCREENSHOT_DIR"])
    else:
        base = Path(tempfile.gettempdir()) / "mcp-pbi-autonomous" / "screenshots"
    base.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    # J-26: two iterations (or loops) in the same second used to collide.
    return base / f"debug-iter{iteration}-{ts}-{uuid.uuid4().hex[:8]}.png"
