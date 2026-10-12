"""Suite generation (F-10): ``09_Validate test_generate source=page|report|roles|measures``.

Generates regression tests into the suite file ``test_put`` writes (``core.validation.test_suites``):

* per data visual a ``dax_assertion`` SNAPSHOT of the reconstructed query (``fidelity`` recorded;
  ``expect.rows`` for small results, else ``row_count`` + ``columns``),
* per matrix / table visual a ``tieout`` (parent == sum of children),
* per page slicer with a saved selection a ``dax_assertion`` that the selected members still exist,
* per KPI measure (card / KPI visuals, or ``measures``) a grand-total ``measure_assertion``,
* per role an ``rls_audit``,
* optionally (``include_perf``) a ``performance_budget`` for heavy visuals (median of N runs x headroom).

Ids are stable (``snap.<page>.<visual>``, ``kpi.<measure>``, ``rls.<role>`` ...), so re-generating
updates instead of duplicating. A re-generate is a DIFF: a changed expectation is listed under
``changed`` and only written with ``accept_changes=true``; a hand-written test with the same id is
never overwritten silently. Generated tests carry ``generated: true`` and ``provenance``:
``snapshot`` = asserts today's numbers (including today's bugs), ``invariant`` / ``audit`` = a property.

The functions take the builder and the query runner as arguments (fakes in the tests).
"""

from __future__ import annotations

import hashlib
import math
import re
import statistics
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

SOURCES = ("page", "report", "roles", "measures")
KPI_VISUAL_TYPES = frozenset({"card", "cardVisual", "kpi", "multiRowCard", "gauge"})
TIEOUT_VISUAL_TYPES = frozenset({"pivotTable", "matrix", "table", "tableEx"})
GENERATOR = "test_generate"
DEFAULT_SNAPSHOT_ROWS = 50
DEFAULT_MAX_VISUALS = 200
_EXPECTATION_KEYS = (
    "query",
    "expect",
    "expected",
    "measure",
    "filters",
    "tolerance",
    "max_ms",
    "roles",
    "as_user",
    "mode",
    "page_name",
    "visual_id",
    "visual_name",
    "measure_a",
    "measure_b",
    "by",
    "non_additive",
    "fail_on",
    "fidelity",
    "page_a",
    "page_b",
)
_SAFE_ID = re.compile(r"[^A-Za-z0-9_.\-]")

RunQuery = Callable[[str], Dict[str, Any]]


def stable_id(prefix: str, *parts: str) -> str:
    """``prefix.part.part`` limited to the suite id charset and 80 chars (long ids keep a hash suffix)."""
    raw = ".".join([prefix] + [str(p) for p in parts if str(p) != ""])
    cleaned = _SAFE_ID.sub("_", raw)
    if len(cleaned) <= 80:
        return cleaned
    return cleaned[:70] + "-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]


def _test(tid: str, ttype: str, provenance: str, source: str, **fields: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"id": tid, "type": ttype}
    out.update({k: v for k, v in fields.items() if v is not None})
    out["generated"] = True
    out["generator"] = GENERATOR
    out["provenance"] = provenance
    out["source"] = source
    out.setdefault("tags", ["generated", provenance])
    return out


def _decode_pbir_typed(v: Any) -> Tuple[Any, str]:
    """PBIR ``Literal.Value`` text -> ``(python value, kind)``; non-text values pass as ``unknown``."""
    if not isinstance(v, str):
        return v, "unknown"
    from core.pbip.pbir_model import decode_literal

    return decode_literal(v)


def _decode_pbir_value(v: Any) -> Any:
    """PBIR ``Literal.Value`` text -> Python value; anything else unchanged."""
    value, kind = _decode_pbir_typed(v)
    if kind == "unknown":
        return v
    if kind == "datetime":
        return value.isoformat() if hasattr(value, "isoformat") else value
    if kind == "decimal":
        return float(value)
    return value


def _dax_literal(v: Any) -> Optional[str]:
    """DAX literal of a decoded value; None when it has no safe literal (the test is skipped).

    ``datetime`` / ``date`` become ``DATE(y, m, d)`` (+ ``TIME(h, m, s)``): quoting the ISO text
    would compare a date column against a string and never match."""
    import datetime as _dt

    from core.dax.dax_names import dax_string

    if v is None or isinstance(v, (dict, list)):
        return None
    if isinstance(v, bool):
        return "TRUE()" if v else "FALSE()"
    if isinstance(v, _dt.datetime):
        date = f"DATE({v.year}, {v.month}, {v.day})"
        if v.hour or v.minute or v.second:
            return f"({date} + TIME({v.hour}, {v.minute}, {v.second}))"
        return date
    if isinstance(v, _dt.date):
        return f"DATE({v.year}, {v.month}, {v.day})"
    if isinstance(v, (int, float)):
        return repr(v)
    return dax_string(str(v))


def _first_scalar(res: Dict[str, Any]) -> Any:
    rows = res.get("rows") or []
    return next(iter(rows[0].values()), None) if rows else None


def _norm_cols(res: Dict[str, Any]) -> List[str]:
    cols = []
    for c in res.get("columns") or []:
        m = re.search(r"\[([^\]]+)\]\s*$", str(c))
        cols.append(m.group(1) if m else str(c))
    return cols


def _norm_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k, v in row.items():
        m = re.search(r"\[([^\]]+)\]\s*$", str(k))
        out[m.group(1) if m else str(k)] = v
    return out


# --------------------------------------------------------------------------- generators
def _resolve_pages(builder: Any, page_name: Optional[str]) -> List[Dict[str, Any]]:
    pages = builder.list_pages()
    if not page_name:
        return list(pages)
    wanted = [p for p in pages if page_name in (p.get("id"), p.get("name"))]
    return wanted


def snapshot_expectation(res: Dict[str, Any], snapshot_rows: int) -> Dict[str, Any]:
    rows = res.get("rows") or []
    n = res.get("row_count", len(rows))
    if n <= snapshot_rows and not res.get("truncated"):
        return {"rows": [_norm_row(r) for r in rows], "columns": _norm_cols(res)}
    return {"row_count": n, "columns": _norm_cols(res)}


def visual_tests(
    builder: Any,
    run_query: RunQuery,
    page: Dict[str, Any],
    *,
    snapshot_rows: int = DEFAULT_SNAPSHOT_ROWS,
    include_tieout: bool = True,
    include_perf: bool = False,
    runs: int = 3,
    headroom: float = 1.5,
    heavy_ms: float = 500.0,
    budget: Optional[List[int]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Snapshot / tieout / perf tests of every data visual of one page. Returns ``(tests, skipped)``."""
    tests: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    pid, pname = page.get("id"), page.get("name")
    for v in builder.list_visuals(pname or pid):
        if v.get("is_slicer") or v.get("is_visual_group") or not v.get("is_data_visual", True):
            continue
        if budget is not None:
            if budget[0] <= 0:
                skipped.append(
                    {"page": pid, "visual": v.get("id"), "reason": "max_visuals reached"}
                )
                continue
            budget[0] -= 1
        vid = v["id"]
        label = {"page": pid, "visual": vid, "title": v.get("title") or v.get("friendly_name")}
        try:
            vq = builder.build_visual_query(pname or pid, vid)
        except Exception as exc:  # noqa: BLE001
            skipped.append({**label, "reason": f"query reconstruction failed: {exc}"})
            continue
        if vq is None:
            skipped.append({**label, "reason": "no measure bound (nothing to assert)"})
            continue
        res = run_query(vq.dax_query)
        if not res.get("success"):
            skipped.append({**label, "reason": f"query failed: {str(res.get('error'))[:200]}"})
            continue
        fid = getattr(vq, "fidelity", "full")
        tags = ["generated", "snapshot"] + ([] if fid == "full" else ["fidelity-partial"])
        slicer_base = (getattr(vq, "filter_breakdown", None) or {}).get("slicer") or []
        tests.append(
            _test(
                stable_id("snap", str(pid), vid),
                "dax_assertion",
                "snapshot",
                "page",
                query=vq.dax_query,
                expect=snapshot_expectation(res, snapshot_rows),
                fidelity=fid,
                fidelity_notes=list(getattr(vq, "fidelity_notes", []) or [])[:5] or None,
                base=(
                    {
                        "slicers": [
                            {
                                "table": s.get("table"),
                                "column": s.get("column"),
                                "values": s.get("values"),
                            }
                            for s in slicer_base
                        ]
                    }
                    if slicer_base
                    else None
                ),
                visual={
                    "page": pname,
                    "visual_id": vid,
                    "type": v.get("type"),
                    "title": label["title"],
                },
                tags=tags,
            )
        )
        if include_tieout and v.get("type") in TIEOUT_VISUAL_TYPES and v.get("measures"):
            tests.append(
                _test(
                    stable_id("tie", str(pid), vid),
                    "tieout",
                    "invariant",
                    "page",
                    mode="visual",
                    page_name=pname or pid,
                    visual_id=vid,
                    tolerance=0.01,
                    non_additive=[],
                    tags=["generated", "invariant", "tieout"],
                )
            )
        if include_perf:
            times: List[float] = []
            for _ in range(max(1, min(int(runs), 10))):
                s = time.perf_counter()
                r = run_query(vq.dax_query)
                wall = (time.perf_counter() - s) * 1000
                if not r.get("success"):
                    break
                times.append(float(r.get("execution_time_ms") or wall))
            if times and statistics.median(times) >= heavy_ms:
                med = statistics.median(times)
                tests.append(
                    _test(
                        stable_id("perf", str(pid), vid),
                        "performance_budget",
                        "snapshot",
                        "page",
                        query=vq.dax_query,
                        max_ms=int(math.ceil(med * float(headroom))),
                        runs=3,
                        measured_median_ms=round(med, 1),
                        tags=["generated", "performance"],
                    )
                )
    return tests, skipped


def slicer_tests(
    builder: Any, run_query: RunQuery, page: Dict[str, Any]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """One ``dax_assertion`` per page slicer with a saved selection: the selected members must still exist."""
    tests: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    pid, pname = page.get("id"), page.get("name")
    for s in builder.list_slicers(pname or pid) or []:
        raw = list(getattr(s, "selected_values", None) or [])
        # Saved selections are PBIR Literal.Value strings ('Calendar', true, 12L): decode them,
        # or the assertion compares against "'Calendar'" and fails on every run (R0201 live test).
        # A saved null selection (``null``) is the BLANK member, not "no value": keep it.
        values: List[Any] = []
        lits: List[Optional[str]] = []
        for item in raw:
            value, kind = _decode_pbir_typed(item)
            if item is None or kind == "null":
                values.append(None)
                lits.append("BLANK()")
                continue
            decoded = _decode_pbir_value(item)
            values.append(decoded)
            if kind == "datetime":
                # the real datetime -> DATE(...) ; unparseable text has no safe literal
                lits.append(_dax_literal(value) if hasattr(value, "year") else None)
            else:
                lits.append(_dax_literal(decoded))
        if not values or any(x is None for x in lits):
            continue
        if getattr(s, "composite_values", None) or getattr(s, "extra_columns", None):
            skipped.append(
                {
                    "page": pid,
                    "slicer": s.slicer_id,
                    "reason": "composite slicer is not snapshotted",
                }
            )
            continue
        from core.dax.dax_names import column_ref

        col = column_ref(s.table, s.column)
        members = ", ".join(x for x in lits if x is not None)
        query = f'EVALUATE ROW("n", COUNTROWS(FILTER(VALUES({col}), {col} IN {{{members}}})))'
        distinct = len(set(map(str, values)))
        tests.append(
            _test(
                stable_id("slicer", str(pid), s.slicer_id),
                "dax_assertion",
                "snapshot",
                "page",
                query=query,
                expect={"scalar": distinct},
                base={
                    "slicer": {
                        "table": s.table,
                        "column": s.column,
                        "selected": values,
                        "inverted": bool(getattr(s, "is_inverted", False)),
                    }
                },
                tags=["generated", "snapshot", "slicer-base"],
            )
        )
    return tests, skipped


def kpi_measure_test(
    measure: str, run_query: RunQuery, tolerance: float = 0.001
) -> Optional[Dict[str, Any]]:
    from core.dax.dax_names import bracket

    ref = measure if measure.strip().startswith(("[", "'")) else bracket(measure.strip())
    res = run_query(f'EVALUATE ROW("v", {ref})')
    if not res.get("success"):
        return None
    value = _first_scalar(res)
    return _test(
        stable_id("kpi", measure.strip("[] ")),
        "measure_assertion",
        "snapshot",
        "measures",
        measure=measure,
        expected=value,
        tolerance=tolerance,
        tags=["generated", "snapshot", "kpi"],
    )


def role_test(role: str, as_user: Optional[str] = None) -> Dict[str, Any]:
    return _test(
        stable_id("rls", role),
        "rls_audit",
        "audit",
        "roles",
        roles=[role],
        as_user=as_user,
        fail_on="critical",
        tags=["generated", "audit", "rls"],
    )


def generate(
    source: str,
    *,
    builder: Any = None,
    run_query: RunQuery,
    page_name: Optional[str] = None,
    measures: Sequence[str] = (),
    roles: Sequence[str] = (),
    as_user: Optional[str] = None,
    tolerance: float = 0.001,
    snapshot_rows: int = DEFAULT_SNAPSHOT_ROWS,
    include_tieout: bool = True,
    include_perf: bool = False,
    runs: int = 3,
    headroom: float = 1.5,
    heavy_ms: float = 500.0,
    max_visuals: int = DEFAULT_MAX_VISUALS,
) -> Dict[str, Any]:
    """Build the test list for *source*. Executes read-only queries; writes nothing."""
    if source not in SOURCES:
        return {
            "success": False,
            "error_type": "invalid_parameters",
            "error": f"source must be one of {list(SOURCES)}",
        }
    tests: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    notes: List[str] = []
    if source in ("page", "report"):
        if builder is None:
            return {
                "success": False,
                "error_type": "pbip_path_required",
                "error": "source=page|report needs the PBIP path (09_Debug_Operations set_path)",
            }
        if source == "page" and not page_name:
            return {
                "success": False,
                "error_type": "missing_argument",
                "error": "source=page needs page_name",
            }
        pages = _resolve_pages(builder, page_name if source == "page" else None)
        if not pages:
            return {
                "success": False,
                "error_type": "not_found",
                "error": f"page '{page_name}' not found",
            }
        budget = [int(max_visuals)]
        kpis: List[str] = []
        for page in pages:
            vt, vs = visual_tests(
                builder,
                run_query,
                page,
                snapshot_rows=snapshot_rows,
                include_tieout=include_tieout,
                include_perf=include_perf,
                runs=runs,
                headroom=headroom,
                heavy_ms=heavy_ms,
                budget=budget,
            )
            st, ss = slicer_tests(builder, run_query, page)
            tests += vt + st
            skipped += vs + ss
            pname = page.get("name") or page.get("id")
            for v in builder.list_visuals(pname):
                if v.get("type") in KPI_VISUAL_TYPES:
                    for m in v.get("measures", []):
                        if m not in kpis:
                            kpis.append(m)
        measures = list(measures) or kpis
    if source in ("page", "report", "measures"):
        for m in measures:
            t = kpi_measure_test(m, run_query, tolerance)
            if t is None:
                skipped.append({"measure": m, "reason": "grand-total evaluation failed"})
            else:
                tests.append(t)
    if source in ("roles", "report"):
        for r in roles:
            tests.append(role_test(r, as_user))
        if source == "roles" and not roles:
            notes.append("no roles found in the model")
    # one id, one test: keep the first occurrence (a KPI measure on several cards)
    seen, unique = set(), []
    for t in tests:
        if t["id"] not in seen:
            seen.add(t["id"])
            unique.append(t)
    return {"success": True, "source": source, "tests": unique, "skipped": skipped, "notes": notes}


# --------------------------------------------------------------------------- diff
def _expectation(test: Dict[str, Any]) -> Dict[str, Any]:
    return {k: test.get(k) for k in _EXPECTATION_KEYS if k in test}


def _same_expectation(old: Dict[str, Any], new: Dict[str, Any]) -> bool:
    a, b = _expectation(old), _expectation(new)
    if "max_ms" in a and "max_ms" in b:
        # a budget is a headroom, not a snapshot: +-50% is the same budget
        lo, hi = float(a["max_ms"]) * 0.5, float(a["max_ms"]) * 1.5
        if lo <= float(b["max_ms"]) <= hi:
            a, b = dict(a), dict(b)
            a.pop("max_ms"), b.pop("max_ms")
    return a == b


def diff_against_suite(
    tests: Sequence[Dict[str, Any]], suite_tests: Sequence[Dict[str, Any]]
) -> Dict[str, Any]:
    """Classify generated tests against the suite file: added / unchanged / changed / hand-written clashes / stale."""
    existing = {t.get("id"): t for t in suite_tests}
    added, unchanged, changed, conflicts = [], [], [], []
    for t in tests:
        old = existing.get(t["id"])
        if old is None:
            added.append(t["id"])
        elif not old.get("generated"):
            conflicts.append({"id": t["id"], "reason": "a hand-written test uses this id"})
        elif _same_expectation(old, t):
            unchanged.append(t["id"])
        else:
            changed.append({"id": t["id"], "old": _expectation(old), "new": _expectation(t)})
    new_ids = {t["id"] for t in tests}
    sources = {t.get("source") for t in tests}
    stale = [
        t["id"]
        for t in suite_tests
        if t.get("generated")
        and t.get("generator") == GENERATOR
        and t.get("source") in sources
        and t["id"] not in new_ids
    ]
    return {
        "added": added,
        "unchanged": unchanged,
        "changed": changed,
        "hand_written_conflicts": conflicts,
        "stale": stale,
    }


def select_for_write(
    tests: Sequence[Dict[str, Any]], diff: Dict[str, Any], *, accept_changes: bool = False
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """``(tests to write, ids held back for review)``."""
    held = {c["id"] for c in diff["changed"]} | {c["id"] for c in diff["hand_written_conflicts"]}
    if accept_changes:
        return list(tests), []
    return [t for t in tests if t["id"] not in held], sorted(held)
