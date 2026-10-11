"""Persisted, CI-runnable model test suites for ``09_Validate`` (TEST-01).

A suite is one JSON file (commit it next to the model, e.g. ``tests/model.suite.json``)::

    {
      "version": 1,
      "name": "S02 regression",
      "tests": [
        {"id": "rev-2025", "type": "measure_assertion", "measure": "[Revenue]",
         "filters": ["'Date'[Year] = 2025"], "expected": 1234567.89, "tolerance": 0.5},
        {"id": "rows", "type": "dax_assertion", "query": "EVALUATE VALUES('Date'[Year])",
         "expect": {"row_count": 6}},
        {"id": "snap", "type": "regression_snapshot", "baseline_path": "tests/baseline.json"},
        {"id": "rls-be", "type": "rls_validation", "role": "Belgium",
         "query": "EVALUATE ROW(\\"n\\", COUNTROWS(Company))", "expect": {"scalar": 3}},
        {"id": "perf-pl", "type": "performance_budget", "query": "EVALUATE ...",
         "max_ms": 1500, "runs": 3},
        {"id": "meta", "type": "metadata_compliance", "rule": "no_measure_errors"}
      ]
    }

Test types and their ``expect`` forms:

* ``measure_assertion`` — ``measure`` (+ optional DAX ``filters`` applied with
  CALCULATE) equals ``expected`` within an absolute ``tolerance`` (default 0.001);
  ``expected: null`` asserts BLANK.
* ``dax_assertion`` / ``rls_validation`` (the latter under ``role`` and optionally
  ``effective_user_name``) — ``expect`` holds any of ``scalar`` (+ ``tolerance``),
  ``row_count``, ``min_rows``, ``max_rows``, ``columns`` (list), ``rows`` (exact
  list of row dicts, order-insensitive unless ``ordered: true``), ``error``
  (substring the query must fail with).
* ``regression_snapshot`` — re-runs a ``capture_baseline`` file (``baseline_path``)
  and fails on any changed measure beyond ``tolerance``.
* ``performance_budget`` — median of ``runs`` (default 3) cache-bypassed runs must
  be <= ``max_ms``. Wall clock includes transfer; use it as a regression budget,
  not an SE/FE measurement (``04_Run_DAX mode=trace`` for that).
* ``metadata_compliance`` — ``rule`` one of ``no_measure_errors``,
  ``measures_exist`` (``measures: [...]``), ``measure_name_regex`` (``pattern``),
  ``descriptions_required`` (``scope``: measures | tables).
* ``rls_audit`` (F-07) — every role (or ``roles``) under probe identities (``as_user`` = the known
  UPN(s)); fails on findings at or above ``fail_on`` (default ``critical``: fail-open) except the
  ``allow`` codes. Filter-rewrite simulation, see ``core/validation/rls_audit.py``.
* ``tieout`` (F-09) — ``mode`` visual (``page_name`` + ``visual_id|visual_name``: parent == sum of
  children at every subtotal level; ``non_additive`` measures skipped) | A_vs_B (``measure_a``,
  ``measure_b``, optional ``by`` / ``filters``) | page_vs_page (``page_a``, ``page_b``, ``measure``).
  Passes only on a definite tie; ``tolerance`` is absolute.

Tests written by ``test_generate`` carry ``generated: true`` and ``provenance`` (``snapshot`` =
asserts today's numbers, not a correctness proof; ``invariant`` / ``audit`` = a property).

Every test may carry ``tags`` (run a subset with ``tags``) and ``skip: true``.
The runner never writes to the model. ``run_suite(record=True)`` stores the last
run in a ``<suite>.last-run.json`` sidecar and never rewrites the suite file (N-19).
A run in which no test executed reports ``no_tests_ran`` and exit code 1 (N-16).
"""

from __future__ import annotations

import json
import math
import os
import re
import statistics
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

SUITE_VERSION = 1
TEST_TYPES = (
    "measure_assertion",
    "dax_assertion",
    "regression_snapshot",
    "rls_validation",
    "performance_budget",
    "metadata_compliance",
    "rls_audit",
    "tieout",
)
METADATA_RULES = (
    "no_measure_errors",
    "measures_exist",
    "measure_name_regex",
    "descriptions_required",
)
_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,80}$")
#: ``expect`` keys understood by :func:`check_expect`; anything else is a typo (N-18).
EXPECT_KEYS = frozenset(
    {
        "scalar",
        "tolerance",
        "row_count",
        "min_rows",
        "max_rows",
        "columns",
        "rows",
        "ordered",
        "error",
    }
)
_ASSERTING_EXPECT_KEYS = EXPECT_KEYS - {"tolerance", "ordered"}
# XML 1.0 forbids most control characters even when escaped.
_XML_BAD_CHARS = re.compile("[^\u0009\u000a\u000d\u0020-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")

QueryFn = Callable[[str], Dict[str, Any]]
RoleQueryFn = Callable[[str, List[str], Optional[str]], Dict[str, Any]]
BaselineFn = Callable[[Dict[str, Any]], Dict[str, Any]]
#: ``rls_audit`` / ``tieout`` tests hand their definition to an engine callable and get its result.
EngineFn = Callable[[Dict[str, Any]], Dict[str, Any]]


class SuiteError(ValueError):
    """Invalid suite file or test definition."""


class SuiteNotFound(SuiteError):
    """The suite file does not exist (typo'd path)."""


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- files
def load_suite(path: str, *, must_exist: bool = False) -> Dict[str, Any]:
    if not path:
        raise SuiteError("suite_path is required")
    if not os.path.exists(path):
        if must_exist:
            raise SuiteNotFound(f"Suite file not found: {path}")
        return {
            "version": SUITE_VERSION,
            "name": os.path.splitext(os.path.basename(path))[0],
            "tests": [],
        }
    try:
        with open(path, "r", encoding="utf-8") as fh:
            suite = json.load(fh)
    except (OSError, ValueError) as e:
        raise SuiteError(f"Cannot read suite {path}: {e}") from e
    if not isinstance(suite, dict) or not isinstance(suite.get("tests"), list):
        raise SuiteError("A suite is an object with a 'tests' list")
    return suite


def last_run_path(path: str) -> str:
    """Sidecar holding the last run summary (keeps the committed suite file untouched)."""
    base = path[: -len(".json")] if path.lower().endswith(".json") else path
    return base + ".last-run.json"


def _read_last_run(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(last_run_path(path), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _as_list(value: Any) -> List[str]:
    """``ids`` / ``tags`` as a list: MCP clients often send a bare string (N-16)."""
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value if v is not None and str(v) != ""]
    return [str(value)]


def save_suite(path: str, suite: Dict[str, Any]) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(suite, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def validate_test(test: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise + validate one test definition (raises SuiteError)."""
    if not isinstance(test, dict):
        raise SuiteError("test must be an object")
    tid = str(test.get("id") or "").strip()
    if not _ID_RE.match(tid):
        raise SuiteError("test.id is required: 1-80 chars of letters, digits, _ . -")
    ttype = test.get("type")
    if ttype not in TEST_TYPES:
        raise SuiteError(f"test.type must be one of {list(TEST_TYPES)}")
    need = {
        "measure_assertion": ("measure",),
        "dax_assertion": ("query", "expect"),
        "regression_snapshot": ("baseline_path",),
        "rls_validation": ("role", "query", "expect"),
        "performance_budget": ("query", "max_ms"),
        "metadata_compliance": ("rule",),
        "rls_audit": (),
        "tieout": (),
    }[ttype]
    missing = [k for k in need if test.get(k) in (None, "", [])]
    if ttype == "measure_assertion" and "expected" not in test:
        missing.append("expected")
    if missing:
        raise SuiteError(f"{ttype} test '{tid}' needs: {', '.join(missing)}")
    if ttype == "metadata_compliance" and test["rule"] not in METADATA_RULES:
        raise SuiteError(f"metadata_compliance.rule must be one of {list(METADATA_RULES)}")
    if ttype == "tieout":
        _validate_tieout(tid, test)
    if ttype in ("dax_assertion", "rls_validation"):
        expect = test.get("expect")
        if not isinstance(expect, dict):
            raise SuiteError("expect must be an object")
        unknown = sorted(str(k) for k in expect if k not in EXPECT_KEYS)
        if unknown:
            raise SuiteError(
                f"{ttype} test '{tid}': unknown expect key(s) {unknown}; allowed: {sorted(EXPECT_KEYS)}"
            )
        if not any(k in expect for k in _ASSERTING_EXPECT_KEYS):
            raise SuiteError(
                f"{ttype} test '{tid}': expect asserts nothing; use one of {sorted(_ASSERTING_EXPECT_KEYS)}"
            )
    out = dict(test)
    out["id"] = tid
    return out


def _validate_tieout(tid: str, test: Dict[str, Any]) -> None:
    mode = str(test.get("mode") or ("A_vs_B" if test.get("measure_a") else "visual"))
    need = {
        "visual": ("page_name",),
        "A_vs_B": ("measure_a", "measure_b"),
        "page_vs_page": ("page_a", "page_b", "measure"),
    }.get(mode)
    if need is None:
        raise SuiteError(f"tieout test '{tid}': mode must be visual, page_vs_page or A_vs_B")
    missing = [k for k in need if not test.get(k)]
    if mode == "visual" and not (test.get("visual_id") or test.get("visual_name")):
        missing.append("visual_id|visual_name")
    if missing:
        raise SuiteError(f"tieout test '{tid}' (mode {mode}) needs: {', '.join(missing)}")


def put_test(path: str, test: Dict[str, Any], *, replace: bool = True) -> Dict[str, Any]:
    """Add or replace one test in the suite file (created when missing)."""
    t = validate_test(test)
    suite = load_suite(path)
    tests = suite["tests"]
    idx = next((i for i, x in enumerate(tests) if x.get("id") == t["id"]), None)
    if idx is not None and not replace:
        raise SuiteError(f"test '{t['id']}' already exists (replace=false)")
    action = "replaced" if idx is not None else "added"
    if idx is not None:
        tests[idx] = t
    else:
        tests.append(t)
    suite.setdefault("version", SUITE_VERSION)
    suite["updated_at"] = _utc()
    save_suite(path, suite)
    return {
        "success": True,
        "action": action,
        "test_id": t["id"],
        "suite_path": path,
        "test_count": len(tests),
    }


def put_tests(
    path: str, tests: Sequence[Dict[str, Any]], *, replace: bool = True
) -> Dict[str, Any]:
    """Add or replace several tests in ONE atomic write (``test_generate``); same validation as ``put_test``."""
    validated = [validate_test(t) for t in tests]
    suite = load_suite(path)
    existing = suite["tests"]
    added, replaced, skipped = [], [], []
    for t in validated:
        idx = next((i for i, x in enumerate(existing) if x.get("id") == t["id"]), None)
        if idx is None:
            existing.append(t)
            added.append(t["id"])
        elif replace:
            existing[idx] = t
            replaced.append(t["id"])
        else:
            skipped.append(t["id"])
    suite.setdefault("version", SUITE_VERSION)
    suite["updated_at"] = _utc()
    save_suite(path, suite)
    return {
        "success": True,
        "suite_path": path,
        "added": added,
        "replaced": replaced,
        "skipped": skipped,
        "test_count": len(existing),
    }


def remove_test(path: str, test_id: str) -> Dict[str, Any]:
    suite = load_suite(path)
    before = len(suite["tests"])
    suite["tests"] = [t for t in suite["tests"] if t.get("id") != test_id]
    if len(suite["tests"]) == before:
        return {
            "success": False,
            "error_type": "not_found",
            "error": f"No test '{test_id}' in {path}",
        }
    save_suite(path, suite)
    return {
        "success": True,
        "action": "removed",
        "test_id": test_id,
        "test_count": len(suite["tests"]),
    }


def list_tests(path: str) -> Dict[str, Any]:
    suite = load_suite(path)
    return {
        "success": True,
        "suite_path": path,
        "name": suite.get("name"),
        "test_count": len(suite["tests"]),
        "tests": [
            {
                "id": t.get("id"),
                "type": t.get("type"),
                "tags": t.get("tags") or [],
                "skip": bool(t.get("skip")),
            }
            for t in suite["tests"]
        ],
        "last_run": _read_last_run(path) or suite.get("last_run"),
    }


# --------------------------------------------------------------------------- helpers
def _first_scalar(res: Dict[str, Any]) -> Any:
    rows = res.get("rows") or []
    if not rows:
        return None
    return next(iter(rows[0].values()), None)


def _num_equal(actual: Any, expected: Any, tol: float) -> bool:
    if expected is None or actual is None:
        return (expected is None) == (actual is None)
    if isinstance(expected, bool) or isinstance(actual, bool):
        return actual == expected
    try:
        a, e = float(actual), float(expected)
    except (TypeError, ValueError):
        return str(actual) == str(expected)
    if math.isnan(a) or math.isnan(e):
        return math.isnan(a) and math.isnan(e)  # NaN equals NaN here (N-18)
    if a == e:  # also +inf == +inf, where inf - inf would be NaN
        return True
    return abs(a - e) <= tol


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _cell_equal(got: Any, want: Any, tol: float) -> bool:
    """One result cell vs its expectation: numbers use the test tolerance (1 == 1.0)."""
    if _is_number(got) and _is_number(want):
        return _num_equal(got, want, tol)
    return got == want


def _row_equal(got: Dict[str, Any], want: Dict[str, Any], tol: float) -> bool:
    if set(got) != set(want):
        return False
    return all(_cell_equal(got[k], want[k], tol) for k in want)


def _compare_rows(
    got: List[Dict[str, Any]], want: List[Dict[str, Any]], tol: float, ordered: bool
) -> Optional[str]:
    """None when equal, else a message naming the first differing row."""

    def show(r: Any) -> str:
        return json.dumps(r, default=str)[:300]

    if ordered:
        for i, (g, w) in enumerate(zip(got, want)):
            if not _row_equal(g, w, tol):
                return f"row {i} differs: got {show(g)}, expected {show(w)}"
        if len(got) != len(want):
            return f"rows differ (got {len(got)}, expected {len(want)})"
        return None
    remaining = list(got)
    for w in want:
        hit = next((i for i, g in enumerate(remaining) if _row_equal(g, w, tol)), None)
        if hit is None:
            return (
                f"expected row not found: {show(w)} (got {len(got)} row(s), expected {len(want)})"
            )
        remaining.pop(hit)
    if remaining:
        return f"unexpected row: {show(remaining[0])} (got {len(got)} row(s), expected {len(want)})"
    return None


def _norm_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Strip DAX column decoration: ``'T'[c]`` / ``T[c]`` / ``[c]`` -> ``c``."""
    out = {}
    for k, v in row.items():
        key = str(k)
        m = re.search(r"\[([^\]]+)\]\s*$", key)
        out[m.group(1) if m else key] = v
    return out


def _measure_query(measure: str, filters: Sequence[str]) -> str:
    ref = measure.strip()
    if not (ref.startswith("[") or "[" in ref):
        ref = f"[{ref}]"
    body = ref if not filters else f"CALCULATE({ref}, {', '.join(filters)})"
    return f'EVALUATE ROW("v", {body})'


def check_expect(res: Dict[str, Any], expect: Dict[str, Any]) -> List[str]:
    """Return failure messages (empty = pass) for a query result vs expect."""
    fails: List[str] = []
    if "error" in expect:
        if res.get("success"):
            return [f"expected the query to fail with '{expect['error']}', it succeeded"]
        err = str(res.get("error") or "")
        if str(expect["error"]).lower() not in err.lower():
            fails.append(f"error '{err[:200]}' does not contain '{expect['error']}'")
        return fails
    if not res.get("success"):
        return [f"query failed: {str(res.get('error'))[:300]}"]
    rows = res.get("rows") or []
    n = res.get("row_count", len(rows))
    truncated = bool(res.get("truncated"))
    tol = float(expect.get("tolerance", 0.001))
    if truncated and any(k in expect for k in ("row_count", "max_rows", "rows")):
        fails.append(
            f"result truncated at {n} rows: row_count / max_rows / rows cannot be verified exactly"
        )
    if "scalar" in expect:
        got = _first_scalar(res)
        if not _num_equal(got, expect["scalar"], float(expect.get("tolerance", 0.001))):
            fails.append(f"scalar {got!r} != expected {expect['scalar']!r}")
    if "row_count" in expect and not truncated and n != expect["row_count"]:
        fails.append(f"row_count {n} != expected {expect['row_count']}")
    if "min_rows" in expect and n < expect["min_rows"]:
        fails.append(f"row_count {n} < min_rows {expect['min_rows']}")
    if "max_rows" in expect and not truncated and n > expect["max_rows"]:
        fails.append(f"row_count {n} > max_rows {expect['max_rows']}")
    if "columns" in expect:
        cols = [list(_norm_row({c: None}).keys())[0] for c in (res.get("columns") or [])]
        want = list(expect["columns"])
        if cols != want:
            fails.append(f"columns {cols} != expected {want}")
    if "rows" in expect and not truncated:
        got = [_norm_row(r) for r in rows]
        want = [_norm_row(r) for r in expect["rows"]]
        diff = _compare_rows(got, want, tol, bool(expect.get("ordered")))
        if diff:
            fails.append(diff)
    return fails


# --------------------------------------------------------------------------- runner
def run_test(
    test: Dict[str, Any],
    *,
    query: QueryFn,
    query_as_role: Optional[RoleQueryFn] = None,
    compare_baseline: Optional[BaselineFn] = None,
    rls_audit: Optional[EngineFn] = None,
    tieout: Optional[EngineFn] = None,
) -> Dict[str, Any]:
    """Run one test. Never raises: a crash becomes status ``error``."""
    t0 = time.perf_counter()
    out: Dict[str, Any] = {"id": test.get("id"), "type": test.get("type")}
    try:
        ttype = test["type"]
        fails: List[str] = []
        detail: Dict[str, Any] = {}
        if ttype == "measure_assertion":
            q = _measure_query(test["measure"], test.get("filters") or [])
            res = query(q)
            if not res.get("success"):
                fails.append(f"query failed: {str(res.get('error'))[:300]}")
            else:
                got = _first_scalar(res)
                detail = {"actual": got, "expected": test["expected"]}
                if not _num_equal(got, test["expected"], float(test.get("tolerance", 0.001))):
                    fails.append(f"{test['measure']} = {got!r}, expected {test['expected']!r}")
        elif ttype == "dax_assertion":
            fails = check_expect(query(test["query"]), test["expect"])
        elif ttype == "rls_validation":
            if query_as_role is None:
                raise RuntimeError("role impersonation unavailable")
            res = query_as_role(test["query"], [test["role"]], test.get("effective_user_name"))
            fails = check_expect(res, test["expect"])
        elif ttype == "regression_snapshot":
            if compare_baseline is None:
                raise RuntimeError("baseline comparison unavailable")
            res = compare_baseline(
                {"baseline_path": test["baseline_path"], "tolerance": test.get("tolerance", 0.001)}
            )
            if not res.get("success"):
                fails.append(f"baseline compare failed: {res.get('error')}")
            elif res.get("changed"):
                fails.append(f"{res['changed']} measure(s) changed vs baseline")
                detail = {"differences": (res.get("differences") or [])[:25]}
        elif ttype == "performance_budget":
            runs = max(1, min(int(test.get("runs", 3)), 20))
            times: List[float] = []
            for _ in range(runs):
                s = time.perf_counter()
                res = query(test["query"])
                wall = (time.perf_counter() - s) * 1000
                if not res.get("success"):
                    fails.append(f"query failed: {str(res.get('error'))[:300]}")
                    break
                times.append(float(res.get("execution_time_ms") or wall))
            if times:
                med = statistics.median(times)
                detail = {
                    "median_ms": round(med, 1),
                    "runs_ms": [round(x, 1) for x in times],
                    "max_ms": test["max_ms"],
                }
                if med > float(test["max_ms"]):
                    fails.append(f"median {med:.0f} ms > budget {test['max_ms']} ms")
        elif ttype == "metadata_compliance":
            fails, detail = _metadata_rule(test, query)
        elif ttype == "rls_audit":
            if rls_audit is None:
                raise RuntimeError("rls audit unavailable")
            from core.validation.rls_audit import audit_passes

            res = rls_audit(test)
            ok, fails = audit_passes(
                res, str(test.get("fail_on") or "critical"), test.get("allow") or ()
            )
            if res.get("success"):
                detail = {"roles_audited": res.get("roles_audited"), "summary": res.get("summary")}
        elif ttype == "tieout":
            if tieout is None:
                raise RuntimeError("tieout unavailable")
            from core.validation.tieout import tieout_passes

            res = tieout(test)
            ok, fails = tieout_passes(res)
            if res.get("success"):
                detail = {
                    k: res[k]
                    for k in ("mode", "break_count", "first_break", "difference_count")
                    if k in res
                }
        out["status"] = "fail" if fails else "pass"
        if fails:
            out["failures"] = fails
        if detail:
            out["detail"] = detail
    except Exception as exc:  # noqa: BLE001 - reported per test
        out["status"] = "error"
        out["failures"] = [f"{type(exc).__name__}: {exc}"]
    out["duration_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return out


def _metadata_rule(test: Dict[str, Any], query: QueryFn):
    rule = test["rule"]
    fails: List[str] = []
    detail: Dict[str, Any] = {}
    if rule == "no_measure_errors":
        res = query(
            'EVALUATE SELECTCOLUMNS(FILTER(INFO.MEASURES(), NOT ISBLANK([ErrorMessage]) && [ErrorMessage] <> ""), "Name", [Name], "Error", [ErrorMessage])'
        )
        if not res.get("success"):
            return [f"INFO.MEASURES failed: {res.get('error')}"], detail
        bad = [_norm_row(r) for r in res.get("rows") or []]
        if bad:
            fails.append(f"{len(bad)} measure(s) in error")
            detail = {"measures": bad[:25]}
    elif rule in ("measures_exist", "measure_name_regex", "descriptions_required"):
        if rule == "descriptions_required" and test.get("scope") == "tables":
            res = query(
                'EVALUATE SELECTCOLUMNS(FILTER(INFO.TABLES(), NOT [IsHidden]), "Name", [Name], "Description", [Description])'
            )
        else:
            res = query(
                'EVALUATE SELECTCOLUMNS(INFO.MEASURES(), "Name", [Name], "Description", [Description])'
            )
        if not res.get("success"):
            return [f"INFO query failed: {res.get('error')}"], detail
        rows = [_norm_row(r) for r in res.get("rows") or []]
        names = [str(r.get("Name")) for r in rows]
        if rule == "measures_exist":
            missing = [
                m.strip("[]") for m in test.get("measures") or [] if m.strip("[]") not in names
            ]
            if missing:
                fails.append(f"missing measures: {missing}")
        elif rule == "measure_name_regex":
            pat = re.compile(str(test.get("pattern") or ".*"))
            offenders = [n for n in names if not pat.search(n)]
            if offenders:
                fails.append(f"{len(offenders)} measure name(s) do not match {pat.pattern!r}")
                detail = {"offenders": offenders[:50]}
        else:
            empty = [
                str(r.get("Name")) for r in rows if not str(r.get("Description") or "").strip()
            ]
            if empty:
                fails.append(f"{len(empty)} object(s) without a description")
                detail = {"without_description": empty[:50]}
    return fails, detail


def run_suite(
    path: str,
    *,
    query: QueryFn,
    query_as_role: Optional[RoleQueryFn] = None,
    compare_baseline: Optional[BaselineFn] = None,
    rls_audit: Optional[EngineFn] = None,
    tieout: Optional[EngineFn] = None,
    ids: Optional[Any] = None,
    tags: Optional[Any] = None,
    record: bool = True,
) -> Dict[str, Any]:
    suite = load_suite(path, must_exist=True)
    want_ids, want_tags = _as_list(ids), _as_list(tags)
    selected = []
    for t in suite["tests"]:
        if want_ids and t.get("id") not in want_ids:
            continue
        if want_tags and not (set(want_tags) & set(_as_list(t.get("tags")))):
            continue
        selected.append(t)
    results = []
    # F-26: "test i of n" progress + a cooperative cancel check before every test.
    from core.utilities.progress_ticker import Ticker

    ticker = Ticker(len(selected), "test")
    cancelled = False
    for t in selected:
        if ticker.cancelled():
            cancelled = True
            break
        if t.get("skip"):
            results.append({"id": t.get("id"), "type": t.get("type"), "status": "skipped"})
            ticker.tick(str(t.get("id") or ""))
            continue
        try:
            vt = validate_test(t)
        except SuiteError as e:
            results.append(
                {"id": t.get("id"), "type": t.get("type"), "status": "error", "failures": [str(e)]}
            )
            ticker.tick(str(t.get("id") or ""))
            continue
        results.append(
            run_test(
                vt,
                query=query,
                query_as_role=query_as_role,
                compare_baseline=compare_baseline,
                rls_audit=rls_audit,
                tieout=tieout,
            )
        )
        ticker.tick(str(t.get("id") or ""))
    counts = {
        s: sum(1 for r in results if r["status"] == s) for s in ("pass", "fail", "error", "skipped")
    }
    executed = counts["pass"] + counts["fail"] + counts["error"]
    no_tests_ran = executed == 0
    not_run = len(selected) - len(results)
    # A cancelled run never reports a green suite: the tests it did not reach are unknown.
    all_passed = counts["fail"] == 0 and counts["error"] == 0 and not no_tests_ran and not cancelled
    summary = {
        "success": not no_tests_ran,
        "suite_path": path,
        "name": suite.get("name"),
        "ran_at": _utc(),
        "passed": counts["pass"],
        "failed": counts["fail"],
        "errors": counts["error"],
        "skipped": counts["skipped"],
        "all_passed": all_passed,
        "exit_code": 0 if all_passed else 1,
        "results": results,
    }
    if cancelled:
        summary["cancelled"] = True
        summary["partial"] = True
        summary["tests_not_run"] = not_run
    if no_tests_ran:
        summary["no_tests_ran"] = True
        summary["error_type"] = "no_tests_ran"
        summary["error"] = (
            f"No test ran ({len(suite['tests'])} in the suite, {len(selected)} selected, "
            f"{counts['skipped']} skipped); check suite_path / ids / tags."
        )
    if record and not cancelled:
        # N-19: a sidecar, never the suite file (a test_put during a long run would be lost).
        try:
            save_suite(
                last_run_path(path),
                {
                    k: summary[k]
                    for k in ("ran_at", "passed", "failed", "errors", "skipped", "exit_code")
                },
            )
        except OSError:
            summary["record_error"] = f"could not write {last_run_path(path)}"
    return summary


def _xml_text(value: Any) -> str:
    return _XML_BAD_CHARS.sub("", str(value))


def export_results(summary: Dict[str, Any], output_path: str, fmt: str = "junit") -> Dict[str, Any]:
    """Write a run summary as JUnit XML (CI) or JSON."""
    fmt = (fmt or "junit").lower()
    if fmt not in ("junit", "json"):
        return {
            "success": False,
            "error_type": "invalid_parameters",
            "error": "output_format must be junit or json",
        }
    parent = os.path.dirname(os.path.abspath(output_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    if fmt == "json":
        with open(output_path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(summary, fh, indent=2, default=str)
    else:
        res = summary.get("results") or []
        root = ET.Element(
            "testsuite",
            {
                "name": _xml_text(summary.get("name") or "model-suite"),
                "tests": str(len(res)),
                "failures": str(summary.get("failed", 0)),
                "errors": str(summary.get("errors", 0)),
                "skipped": str(summary.get("skipped", 0)),
                "timestamp": _xml_text(summary.get("ran_at") or ""),
            },
        )
        for r in res:
            secs = (r.get("duration_ms") or 0) / 1000
            case = ET.SubElement(
                root,
                "testcase",
                {
                    "classname": _xml_text(r.get("type")),
                    "name": _xml_text(r.get("id")),
                    "time": f"{secs:.3f}",
                },
            )
            msg = _xml_text("; ".join(str(f) for f in (r.get("failures") or [])))
            if r.get("status") == "fail":
                ET.SubElement(case, "failure", {"message": msg}).text = msg
            elif r.get("status") == "error":
                ET.SubElement(case, "error", {"message": msg}).text = msg
            elif r.get("status") == "skipped":
                ET.SubElement(case, "skipped")
        ET.indent(root)
        ET.ElementTree(root).write(output_path, encoding="utf-8", xml_declaration=True)
    return {"success": True, "output_path": output_path, "format": fmt}
