"""Live-swap A/B on the visual's REAL measure chain (F-08): ``09_Debug_Operations compare mode=live_swap``.

``compare`` evaluates ``CALCULATE(<optimized_expression>, filters)`` standalone, so dependents and the
visual's other measures never run, and a ``DEFINE MEASURE`` override does not reach the model measures
that reference it. This writes the candidate body into the LIVE model instead and measures the
visual's own query, cold, interleaved with the baseline:

1. read the baseline body of every patched measure (nothing is written if one is missing);
2. ``checkpoint_create`` the whole model (captures the user's other unsaved edits too);
3. run the visual query once for its baseline rows, then ``runs`` cold timed runs per side in the
   order A B | B A | A B ... - the model is only rewritten when the side CHANGES (about one write per
   round, each through ``write_lane.commit`` so it is journaled and paced);
4. compare the candidate's rows with the baseline's at the visual's grain (``compare_rows``);
5. ALWAYS (``finally``): restore - ``checkpoint_restore preview`` first (nothing to do when the model
   already matches), else ``checkpoint_restore``, else a direct write-back of the pre-images - and
   verify with a second preview + a read of every patched body. ``restore_verified`` is reported;
   anything but True is a loud ``restore_not_verified`` error naming the checkpoint.

Verdict: ``values_differ`` (timings are not comparable), else on the fastest cold run of each side
(the baseline protocol of ``13_Query_Plan benchmark``): ``faster`` / ``slower`` when the delta
exceeds the minimum detectable effect (the larger of both groups' spread and ``MIN_EFFECT_PCT``),
otherwise ``noise``.

Every environment touch goes through :class:`SwapEnv`, so the orchestration is testable with fakes.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "SwapEnv",
    "SwapSpec",
    "run_live_swap",
    "sequence",
    "verdict_from_runs",
    "MIN_EFFECT_PCT",
]

#: Smallest relative difference ever called a win/loss, whatever the spread says.
MIN_EFFECT_PCT = 5.0

Side = str  # "A" (baseline) | "B" (candidate)
Key = Tuple[str, str]  # (table_name, measure_name)


@dataclass
class SwapEnv:
    """The side effects live-swap needs; every member is a plain callable (fakes in tests)."""

    read_expression: Callable[[str, str], Optional[str]]
    write_expressions: Callable[[Dict[Key, str]], None]  # ONE save for all of them
    checkpoint_create: Callable[[str], Dict[str, Any]]
    checkpoint_restore: Callable[[str, bool], Dict[str, Any]]  # (name, preview)
    run_cold: Callable[
        [str], Dict[str, Any]
    ]  # -> {"duration_ms"|"total_ms", ...}; raises on failure
    run_rows: Callable[[str], Dict[str, Any]]  # -> {"success", "rows", "error"}
    compare_rows: Callable[[Any, Any, float], Dict[str, Any]]
    stats: Callable[[List[float]], Dict[str, Any]]
    now: Callable[[], float] = time.monotonic
    is_cancelled: Callable[[], bool] = lambda: False  # noqa: E731


@dataclass
class SwapSpec:
    query: str
    patches: List[Dict[str, str]]  # {table_name, measure_name, expression}
    runs: int = 3
    tolerance: float = 0.001
    time_budget_s: Optional[float] = None
    checkpoint_prefix: str = "fv_liveswap"
    query_source: str = "visual"
    extras: Dict[str, Any] = field(default_factory=dict)


def sequence(runs: int) -> List[Side]:
    """A B | B A | A B ...: alternating the order cancels drift; consecutive equal sides cost no write."""
    out: List[Side] = []
    for i in range(max(1, runs)):
        out.extend(("A", "B") if i % 2 == 0 else ("B", "A"))
    return out


def _primary(rows: List[Dict[str, Any]]) -> Tuple[List[float], str]:
    """The engine's own duration when every run has it, else the trace window (same rule as 13)."""
    durations = [r["duration_ms"] for r in rows if r.get("duration_ms") is not None]
    if rows and len(durations) == len(rows):
        return [float(d) for d in durations], "execution_metrics.duration_ms"
    return [float(r.get("total_ms") or 0) for r in rows], "total_ms"


def verdict_from_runs(
    a_ms: List[float], b_ms: List[float], stats: Callable[[List[float]], Dict[str, Any]]
) -> Dict[str, Any]:
    """faster | slower | noise from the cold runs of baseline (A) and candidate (B)."""
    if not a_ms or not b_ms:
        return {"verdict": "inconclusive", "reason": "each side needs at least one successful run"}
    a_best, b_best = min(a_ms), min(b_ms)

    def spread(values: List[float]) -> float:
        best = min(values)
        return round((max(values) - best) / best * 100, 1) if best else 0.0

    sa, sb = spread(a_ms), spread(b_ms)
    mde = round(max(sa, sb, MIN_EFFECT_PCT), 1)
    delta_ms = round(b_best - a_best, 1)
    delta_pct = round((b_best - a_best) / a_best * 100, 1) if a_best else None
    if delta_pct is None or abs(delta_pct) <= mde:
        label = "noise"
    else:
        label = "faster" if delta_pct < 0 else "slower"
    out = {
        "verdict": label,
        "delta_ms": delta_ms,
        "delta_pct": delta_pct,
        "min_detectable_effect_pct": mde,
        "baseline": {"best_ms": a_best, "spread_pct": sa, "runs_ms": a_ms, **stats(a_ms)},
        "candidate": {"best_ms": b_best, "spread_pct": sb, "runs_ms": b_ms, **stats(b_ms)},
        "compared_on": "fastest cold run per side",
    }
    if min(len(a_ms), len(b_ms)) < 2:
        out["warning"] = "single run per side: spread is unknown, treat the verdict as indicative"
    return out


def _restore(env: SwapEnv, checkpoint: str, pre_image: Dict[Key, str]) -> Dict[str, Any]:
    """Put the model back and PROVE it: a restore preview with zero changes + the pre-image bodies."""
    log: List[str] = []
    method = "none_needed"

    def matches() -> bool:
        try:
            preview = env.checkpoint_restore(checkpoint, True)
        except Exception as exc:  # noqa: BLE001
            log.append(f"restore preview failed: {exc}")
            return False
        if not preview.get("success"):
            log.append(f"restore preview refused: {preview.get('error')}")
            return False
        if int(preview.get("change_count") or 0) != 0:
            log.append(f"{preview.get('change_count')} object(s) still differ from the checkpoint")
            return False
        for (table, name), body in pre_image.items():
            try:
                if env.read_expression(table, name) != body:
                    log.append(f"{table}[{name}] differs from its pre-image")
                    return False
            except Exception as exc:  # noqa: BLE001
                log.append(f"read of {table}[{name}] failed: {exc}")
                return False
        return True

    if not matches():
        method = "checkpoint_restore"
        try:
            res = env.checkpoint_restore(checkpoint, False)
            if not res.get("success"):
                log.append(f"checkpoint_restore failed: {res.get('error')}")
        except Exception as exc:  # noqa: BLE001
            log.append(f"checkpoint_restore raised: {exc}")
        if not matches():
            method = "pre_image_write_back"
            try:
                env.write_expressions(dict(pre_image))
            except Exception as exc:  # noqa: BLE001
                log.append(f"write-back of the pre-images failed: {exc}")
        verified = matches()
    else:
        verified = True
    out: Dict[str, Any] = {"restore_verified": bool(verified), "restore_method": method}
    if not verified:
        out["restore_log"] = log
    return out


def run_live_swap(spec: SwapSpec, env: SwapEnv) -> Dict[str, Any]:
    """Run the swap. Never leaves the model changed without saying so (see module docstring)."""
    if not spec.patches:
        return _err(
            "invalid_args",
            "live_swap needs a candidate: optimized_expression + original_measure, or patch=[...]",
        )
    pre_image: Dict[Key, str] = {}
    for p in spec.patches:
        key = (p["table_name"], p["measure_name"])
        body = env.read_expression(*key)
        if body is None:
            return _err(
                "measure_not_found",
                f"measure {p['table_name']}[{p['measure_name']}] not found; nothing was changed",
            )
        pre_image[key] = body

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    checkpoint = f"{spec.checkpoint_prefix}_{stamp}"
    created = env.checkpoint_create(checkpoint)
    if not created.get("success"):
        return _err(
            "checkpoint_failed",
            f"could not checkpoint the model ({created.get('error')}); nothing was changed",
        )

    candidate: Dict[Key, str] = {
        (p["table_name"], p["measure_name"]): p["expression"] for p in spec.patches
    }
    state: Side = "A"
    swaps = 0
    a_rows: List[Dict[str, Any]] = []
    b_rows: List[Dict[str, Any]] = []
    order: List[Side] = []
    errors: List[str] = []
    base_values: Optional[Dict[str, Any]] = None
    cand_values: Optional[Dict[str, Any]] = None
    aborted: Optional[str] = None
    skipped = 0
    started = env.now()

    def swap(to: Side) -> None:
        nonlocal state, swaps
        env.write_expressions(dict(candidate if to == "B" else pre_image))
        state = to
        swaps += 1

    try:
        base_values = env.run_rows(spec.query)
        if not base_values.get("success"):
            aborted = f"the visual query failed on the baseline: {base_values.get('error')}"
        else:
            for side in sequence(spec.runs):
                if env.is_cancelled():
                    aborted = "cancelled"
                    break
                if spec.time_budget_s and env.now() - started > spec.time_budget_s:
                    skipped += 1
                    continue
                if side != state:
                    swap(side)
                if side == "B" and cand_values is None:
                    cand_values = env.run_rows(spec.query)
                    if not cand_values.get("success"):
                        aborted = f"the visual query failed with the candidate body: {cand_values.get('error')}"
                        break
                try:
                    run = env.run_cold(spec.query)
                except Exception as exc:  # noqa: BLE001 - a failed run is reported, not fatal
                    errors.append(f"{side}: {exc}")
                    continue
                order.append(side)
                (a_rows if side == "A" else b_rows).append(run)
    except Exception as exc:  # noqa: BLE001 - the restore below still runs
        aborted = f"{type(exc).__name__}: {exc}"
        logger.error("live_swap aborted: %s", exc, exc_info=True)
    finally:
        restore = _restore(env, checkpoint, pre_image)

    result = {
        "mode": "live_swap",
        "checkpoint": checkpoint,
        "patched": [{"table_name": t, "measure_name": m} for (t, m) in pre_image],
        "query_source": spec.query_source,
        "order": order,
        "model_writes": swaps,
        "errors": errors,
        **restore,
    }
    if skipped:
        result["runs_not_executed"] = skipped
        result["warning"] = f"the time budget ran out: {skipped} run(s) were skipped"
    if aborted:
        result.update(
            {
                "success": False,
                "error_type": "live_swap_aborted",
                "error": aborted,
                "verdict": "inconclusive",
            }
        )
    else:
        a_ms, a_metric = _primary(a_rows)
        b_ms, b_metric = _primary(b_rows)
        values: Dict[str, Any] = {"equivalent": None}
        if base_values and cand_values:
            values = env.compare_rows(
                base_values.get("rows") or [], cand_values.get("rows") or [], spec.tolerance
            )
            values["row_count"] = {
                "baseline": len(base_values.get("rows") or []),
                "candidate": len(cand_values.get("rows") or []),
            }
        result["values"] = values
        if not (base_values and cand_values):
            result.update(
                {
                    "success": True,
                    "verdict": "inconclusive",
                    "reason": "the candidate body never ran (time budget exhausted before the first B run)",
                }
            )
        elif values.get("equivalent") is not True:
            result.update(
                {
                    "success": True,
                    "verdict": "values_differ",
                    "note": "the candidate does not return the same rows at the visual's grain: timings are not comparable",
                }
            )
        else:
            result.update({"success": True, **verdict_from_runs(a_ms, b_ms, env.stats)})
            result["primary_metric"] = (
                a_metric if a_metric == b_metric else f"{a_metric} / {b_metric}"
            )
    if not restore["restore_verified"]:
        result.update(
            {
                "success": False,
                "error_type": "restore_not_verified",
                "error": (
                    f"the model could NOT be proven restored (checkpoint {checkpoint!r}). Run "
                    f"02_Model_Operations object_type=model operation=checkpoint_restore "
                    f"checkpoint={checkpoint} (preview=true first) and check the patched measures."
                ),
            }
        )
    return result


def _err(error_type: str, message: str) -> Dict[str, Any]:
    return {
        "success": False,
        "error": message,
        "error_type": error_type,
        "restore_verified": True,
        "mode": "live_swap",
    }
