"""Per-visual / per-measure timing history and a noise-aware regression verdict (F-18).

``09_Profile record=true`` appends the page profile's timings to
``output/perf/<project>.jsonl`` (median, spread, run count, git sha, model generation, session and
environment). ``09_Profile operation=regress baseline=<label|sha|date>`` compares the latest
recorded run with that baseline and REFUSES a verdict inside the noise band.

The statistics are ported from ``~/.claude/scripts/pbi-telemetry/benchmark.py``, which exists
because two real reverts were justified by differences inside the noise (3,876 -> 4,662 ms with
sigma 442; a fake +115 ms of drift between sequential blocks):

* at least ``MIN_RUNS`` (6) measured runs per side, at least ``MIN_WARMUP`` (1) discarded warm-up;
* the verdict needs ``noise / |delta| <= 0.15`` where ``noise`` is the largest of the standard
  deviation, the scaled MAD (1.4826 x MAD) and the scaled IQR (IQR / 1.349) of the two sides;
* runs recorded in DIFFERENT server sessions carry machine drift the history cannot control, so
  the bar halves (``0.075``) and the result says so;
* a history comparison is never interleaved, which the result states as a caveat.

Nothing here executes DAX; the only subprocess is the read-only ``git rev-parse`` / ``git status``.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import statistics
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

MIN_RUNS = 6
MIN_WARMUP = 1
#: ``noise / |delta|`` above this = the delta is not resolvable (benchmark.py SIGMA_TO_DELTA_BAR).
NOISE_TO_DELTA_BAR = 0.15
#: Same bar when baseline and candidate come from different server sessions (machine drift).
CROSS_SESSION_BAR = 0.075
MAD_SCALE = 1.4826
IQR_SCALE = 1.349
#: Newest records kept per project file (config ``perf_history.max_records`` overrides).
DEFAULT_MAX_RECORDS = 5000

#: One id per server process: two runs with the same id share the machine's state.
SESSION_ID = uuid.uuid4().hex[:12]

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def history_dir() -> Path:
    return _PROJECT_ROOT / "output" / "perf"


def project_key(path_or_name: Optional[str]) -> str:
    """Filesystem-safe project name for ``<project>.jsonl`` (the report folder's name)."""
    raw = str(path_or_name or "").replace("\\", "/").rstrip("/").split("/")[-1]
    if raw.lower().endswith(".pbip"):
        raw = raw[:-5]
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._")
    return safe[:80] or "default"


# ---------------------------------------------------------------------------
# Statistics (port of benchmark.py)
# ---------------------------------------------------------------------------


def run_stats(runs: Iterable[float]) -> Dict[str, float]:
    xs = sorted(float(x) for x in runs)
    n = len(xs)
    if n == 0:
        return {"n": 0, "median": 0.0, "mad": 0.0, "sigma": 0.0, "iqr": 0.0, "min": 0.0, "max": 0.0}
    med = statistics.median(xs)
    mad = statistics.median(abs(x - med) for x in xs)
    if n >= 4:
        q = statistics.quantiles(xs, n=4)
        iqr = q[2] - q[0]
    else:
        iqr = xs[-1] - xs[0]
    return {
        "n": n,
        "median": med,
        "mad": mad,
        "sigma": statistics.stdev(xs) if n > 1 else 0.0,
        "iqr": iqr,
        "min": xs[0],
        "max": xs[-1],
    }


def noise_of(stats: Dict[str, float]) -> float:
    """Robust noise estimate: the largest of sigma, scaled MAD and scaled IQR."""
    return max(stats["sigma"], MAD_SCALE * stats["mad"], stats["iqr"] / IQR_SCALE)


def compare_runs(
    base: Iterable[float],
    new: Iterable[float],
    *,
    min_runs: int = MIN_RUNS,
    base_warmup: Optional[int] = None,
    new_warmup: Optional[int] = None,
    cross_session: bool = False,
) -> Dict[str, Any]:
    """Compare two arms; ``verdict`` in better | worse | inconclusive | insufficient_data."""
    a, b = list(base), list(new)
    sa, sb = run_stats(a), run_stats(b)
    out: Dict[str, Any] = {"baseline": sa, "candidate": sb, "caveats": []}
    if len(a) < min_runs or len(b) < min_runs:
        out["verdict"] = "insufficient_data"
        out["reason"] = (
            f"Need >={min_runs} measured runs per side; got {len(a)} and {len(b)}. "
            "Measured precedent: 3 cold runs produced a confident wrong answer that caused an "
            "unnecessary revert (profile with iterations >= 6)."
        )
        return out
    for side, warmup in (("baseline", base_warmup), ("candidate", new_warmup)):
        if warmup is not None and warmup < MIN_WARMUP:
            out["caveats"].append(
                f"{side}: {warmup} warmup run(s); >={MIN_WARMUP} expected. Cold first runs "
                "measure the cache, not the change."
            )
    if cross_session:
        out["caveats"].append(
            "baseline and candidate were recorded in different server sessions: machine drift "
            f"is not controlled, so the noise bar is halved to {CROSS_SESSION_BAR:.1%}."
        )
    delta = sb["median"] - sa["median"]
    noise = max(noise_of(sa), noise_of(sb))
    out["delta"] = delta
    out["delta_pct"] = (100.0 * delta / sa["median"]) if sa["median"] else None
    out["noise"] = noise
    if abs(delta) < 1e-9:
        out["verdict"] = "inconclusive"
        out["reason"] = "Medians are identical."
        return out
    bar = CROSS_SESSION_BAR if cross_session else NOISE_TO_DELTA_BAR
    ratio = noise / abs(delta)
    out["noise_to_delta"] = ratio
    if ratio > bar:
        out["verdict"] = "inconclusive"
        out["reason"] = (
            f"noise ({noise:.1f} ms) is {ratio:.0%} of the delta ({abs(delta):.1f} ms), above the "
            f"{bar:.1%} bar: the difference is inside the noise band and cannot be resolved. Do "
            "NOT revert or ship on this number; collect more runs."
        )
        return out
    if not (sa["max"] < sb["min"] or sb["max"] < sa["min"]):
        out["caveats"].append(
            "Run ranges overlap; the medians separate but individual runs do not: treat the "
            "magnitude as approximate."
        )
    out["verdict"] = "better" if delta < 0 else "worse"
    out["reason"] = (
        f"candidate median {sb['median']:.1f} ms vs baseline {sa['median']:.1f} ms "
        f"({delta:+.1f} ms, {out['delta_pct']:+.1f}%); noise {noise:.1f} ms is {ratio:.0%} of the delta."
    )
    return out


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def _git_sha(path: Optional[str]) -> Tuple[Optional[str], Optional[bool]]:
    """``(HEAD sha, dirty)`` of the git repo holding ``path`` via read-only git calls, else
    ``(None, None)`` (no git, no repo, timeout)."""
    if not path:
        return None, None
    cwd = path if os.path.isdir(path) else os.path.dirname(path)
    if not cwd or not os.path.isdir(cwd):
        return None, None
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
            stdin=subprocess.DEVNULL,
        )
        if head.returncode != 0 or not head.stdout.strip():
            return None, None
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
            stdin=subprocess.DEVNULL,
        )
        dirty = bool(status.stdout.strip()) if status.returncode == 0 else None
        return head.stdout.strip(), dirty
    except Exception:  # noqa: BLE001 - provenance is best-effort
        return None, None


def _environment(clear_cache: Optional[bool], mode: str) -> Dict[str, Any]:
    env: Dict[str, Any] = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "cache_state": "cleared_per_run" if clear_cache else "warm",
        "timing": "server_trace" if mode == "trace" else "client_wallclock",
    }
    try:  # background load, when psutil is installed (it is optional)
        import psutil  # type: ignore

        env["background_cpu_pct"] = psutil.cpu_percent(interval=None)
    except Exception:  # noqa: BLE001
        env["background_cpu_pct"] = None
    return env


def _max_records() -> int:
    try:
        from core.config.config_manager import config

        value = config.get("perf_history.max_records", DEFAULT_MAX_RECORDS)
        return max(int(value), 1) if value is not None else DEFAULT_MAX_RECORDS
    except Exception:  # noqa: BLE001
        return DEFAULT_MAX_RECORDS


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def _file(project: str, directory: Optional[Path]) -> Path:
    base = Path(directory) if directory is not None else history_dir()
    return base / f"{project_key(project)}.jsonl"


def load_records(project: str, directory: Optional[Path] = None) -> List[Dict[str, Any]]:
    path = _file(project, directory)
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue  # a torn line must not take the history down
    return out


def _append(path: Path, rows: List[Dict[str, Any]], max_records: int) -> int:
    """Append ``rows``; rewrite the file atomically when it exceeds ``max_records``.
    Returns how many old records were trimmed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")
    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.readlines()
    if len(lines) <= max_records:
        return 0
    keep = lines[-max_records:]
    fd, tmp = tempfile.mkstemp(prefix=".perf_", suffix=".jsonl", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.writelines(keep)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return len(lines) - len(keep)


def _row_stats(runs: List[float]) -> Dict[str, Any]:
    s = run_stats(runs)
    return {
        "runs_ms": [round(float(x), 1) for x in runs],
        "n": s["n"],
        "median_ms": round(s["median"], 2),
        "mad_ms": round(s["mad"], 2),
        "sigma_ms": round(s["sigma"], 2),
        "min_ms": round(s["min"], 1),
        "max_ms": round(s["max"], 1),
    }


def record_profile(
    profile: Dict[str, Any],
    *,
    project: str,
    pbip_path: Optional[str],
    model_generation: Optional[int],
    label: Optional[str] = None,
    directory: Optional[Path] = None,
    max_records: Optional[int] = None,
) -> Dict[str, Any]:
    """Append one run (every profiled visual + its measures) of a ``profile_page`` result."""
    rows_in = [r for r in (profile.get("results") or []) if isinstance(r, dict)]
    if not profile.get("success") or not rows_in:
        return {
            "success": False,
            "error_type": "nothing_to_record",
            "error": "the profile has no per-visual results",
        }
    mode = "trace" if profile.get("mode") == "trace" else "wallclock"
    clear_cache = (profile.get("baseline") or {}).get("clear_cache")
    sha, dirty = _git_sha(pbip_path)
    run_id = uuid.uuid4().hex[:12]
    base = {
        "v": 1,
        "run_id": run_id,
        "ts": _utc(),
        "label": label,
        "project": project_key(project),
        "page": profile.get("page"),
        "git_sha": sha,
        "git_dirty": dirty,
        "model_generation": model_generation,
        "session_id": SESSION_ID,
        "mode": mode,
        "warmup_runs": 1 if mode == "trace" else 0,
        "env": _environment(clear_cache, mode),
    }
    rows: List[Dict[str, Any]] = []
    warnings: List[str] = []
    short = 0
    for r in rows_in:
        runs = r.get("runs_ms")
        if not runs:
            runs = [r.get("avg_time_ms") if r.get("avg_time_ms") is not None else r.get("dax_ms")]
            runs = [x for x in runs if x is not None]
        if not runs:
            continue
        stats = _row_stats(list(runs))
        if stats["n"] < MIN_RUNS:
            short += 1
        vid = str(r.get("visual_id") or r.get("visual") or "")
        name = r.get("visual_name") or r.get("visual")
        measures = [str(m) for m in (r.get("measures") or [])]
        rows.append(
            {
                **base,
                "kind": "visual",
                "key": vid,
                "name": name,
                "visual_type": r.get("visual_type") or r.get("type"),
                "measures": measures,
                **stats,
            }
        )
        for m in measures:
            rows.append(
                {
                    **base,
                    "kind": "measure",
                    "key": f"{m}@{vid}",
                    "name": m,
                    "via_visual": vid,
                    "attribution": "visual_total",
                    **stats,
                }
            )
    if short:
        warnings.append(
            f"{short} visual(s) have fewer than {MIN_RUNS} measured runs: operation=regress will "
            f"return insufficient_data for them (profile with iterations >= {MIN_RUNS})."
        )
    if mode != "trace":
        warnings.append(
            "No warm-up run was discarded (trace=false): the first run pays query-plan "
            "compilation; use trace=true for a protocol-grade baseline."
        )
    if not rows:
        return {
            "success": False,
            "error_type": "nothing_to_record",
            "error": "no visual produced a timing",
        }
    path = _file(project, directory)
    trimmed = _append(path, rows, max_records if max_records is not None else _max_records())
    out = {
        "success": True,
        "run_id": run_id,
        "label": label,
        "history_file": str(path),
        "records_written": len(rows),
        "git_sha": sha,
        "model_generation": model_generation,
        "warnings": warnings,
    }
    if trimmed:
        out["records_trimmed"] = trimmed
    return out


# ---------------------------------------------------------------------------
# Regress
# ---------------------------------------------------------------------------


def _runs(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Distinct runs in file order: ``{run_id, ts, label, git_sha, session_id, records}``."""
    order: List[str] = []
    runs: Dict[str, Dict[str, Any]] = {}
    for rec in records:
        rid = rec.get("run_id")
        if not rid:
            continue
        if rid not in runs:
            order.append(rid)
            runs[rid] = {
                "run_id": rid,
                "ts": rec.get("ts"),
                "label": rec.get("label"),
                "git_sha": rec.get("git_sha"),
                "session_id": rec.get("session_id"),
                "model_generation": rec.get("model_generation"),
                "records": [],
            }
        runs[rid]["records"].append(rec)
    return [runs[r] for r in order]


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")


def _resolve(runs: List[Dict[str, Any]], selector: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """Latest run matching ``selector``: exact label, then git sha prefix, then ``YYYY-MM-DD``."""
    sel = selector.strip()
    for run in reversed(runs):
        if run["label"] == sel or run["run_id"] == sel:
            return run, "label" if run["label"] == sel else "run_id"
    if _SHA_RE.match(sel):
        for run in reversed(runs):
            if (run.get("git_sha") or "").lower().startswith(sel.lower()):
                return run, "git_sha"
    if _DATE_RE.match(sel):
        for run in reversed(runs):
            if str(run.get("ts") or "").startswith(sel):
                return run, "date"
    return None, ""


def _brief(run: Dict[str, Any], how: str = "") -> Dict[str, Any]:
    out = {k: run.get(k) for k in ("run_id", "ts", "label", "git_sha", "model_generation")}
    if how:
        out["resolved_by"] = how
    return out


def regress(
    project: str,
    baseline: str,
    *,
    candidate: Optional[str] = None,
    page_name: Optional[str] = None,
    directory: Optional[Path] = None,
) -> Dict[str, Any]:
    """Compare the latest recorded run (or ``candidate``) against ``baseline``."""
    if not str(baseline or "").strip():
        return {
            "success": False,
            "error_type": "missing_argument",
            "error": "baseline is required: a run label, a git sha (prefix) or a YYYY-MM-DD date",
        }
    runs = _runs(load_records(project, directory))
    if not runs:
        return {
            "success": False,
            "error_type": "no_history",
            "error": f"no recorded runs for project {project_key(project)!r}: profile with record=true first",
        }
    base_run, how = _resolve(runs, baseline)
    if base_run is None:
        return {
            "success": False,
            "error_type": "baseline_not_found",
            "error": f"no recorded run matches baseline {baseline!r}",
            "known_labels": sorted({r["label"] for r in runs if r["label"]})[-20:],
            "run_count": len(runs),
        }
    if candidate:
        cand_run, cand_how = _resolve(runs, candidate)
        if cand_run is None:
            return {
                "success": False,
                "error_type": "baseline_not_found",
                "error": f"no recorded run matches candidate {candidate!r}",
            }
    else:
        cand_run, cand_how = runs[-1], "latest"
    if cand_run["run_id"] == base_run["run_id"]:
        return {
            "success": False,
            "error_type": "invalid_args",
            "error": "the baseline resolves to the latest run itself: record a new run (or name a different baseline)",
        }
    cross = base_run.get("session_id") != cand_run.get("session_id")

    def _index(run: Dict[str, Any]) -> Dict[Tuple[str, str], Dict[str, Any]]:
        idx: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for rec in run["records"]:
            if page_name and rec.get("page") != page_name:
                continue
            idx[(str(rec.get("kind")), str(rec.get("key")))] = rec
        return idx

    bi, ci = _index(base_run), _index(cand_run)
    results: List[Dict[str, Any]] = []
    summary = {"better": 0, "worse": 0, "inconclusive": 0, "insufficient_data": 0}
    for key in sorted(set(bi) & set(ci)):
        b, c = bi[key], ci[key]
        cmp_ = compare_runs(
            b.get("runs_ms") or [],
            c.get("runs_ms") or [],
            base_warmup=b.get("warmup_runs"),
            new_warmup=c.get("warmup_runs"),
            cross_session=cross,
        )
        summary[cmp_["verdict"]] += 1
        row = {
            "kind": key[0],
            "key": key[1],
            "name": c.get("name") or b.get("name"),
            "page": c.get("page"),
            "baseline": {k: b.get(k) for k in ("median_ms", "mad_ms", "n", "min_ms", "max_ms")},
            "candidate": {k: c.get(k) for k in ("median_ms", "mad_ms", "n", "min_ms", "max_ms")},
            "delta_ms": round(cmp_["delta"], 1) if "delta" in cmp_ else None,
            "delta_pct": round(cmp_["delta_pct"], 1) if cmp_.get("delta_pct") is not None else None,
            "verdict": cmp_["verdict"],
            "reason": cmp_.get("reason"),
        }
        if cmp_["caveats"]:
            row["caveats"] = cmp_["caveats"]
        if key[0] == "measure":
            row["attribution"] = "visual_total"
        results.append(row)
    if summary["worse"]:
        overall = "regressed"
    elif summary["better"]:
        overall = "improved"
    elif summary["insufficient_data"] and not summary["inconclusive"]:
        overall = "insufficient_data"
    else:
        overall = "inconclusive"
    caveats = [
        "A history comparison is sequential, not interleaved: confirm anything that matters with an "
        "interleaved A/B (13_Query_Plan benchmark / 09_Debug_Operations compare)."
    ]
    if cross:
        caveats.append(
            "baseline and candidate come from different server sessions (machine drift is not "
            "controlled): the noise bar is halved."
        )
    if base_run.get("model_generation") != cand_run.get("model_generation"):
        caveats.append(
            "the model generation differs: model edits between the two runs are part of the delta."
        )
    results.sort(key=lambda r: (-abs(r["delta_ms"] or 0.0)))
    return {
        "success": True,
        "project": project_key(project),
        "baseline": _brief(base_run, how),
        "candidate": _brief(cand_run, cand_how),
        "session_match": not cross,
        "overall": overall,
        "summary": {**summary, "compared": len(results)},
        "only_in_baseline": sorted({k[1] for k in set(bi) - set(ci) if k[0] == "visual"}),
        "only_in_candidate": sorted({k[1] for k in set(ci) - set(bi) if k[0] == "visual"}),
        "caveats": caveats,
        "results": results,
    }
