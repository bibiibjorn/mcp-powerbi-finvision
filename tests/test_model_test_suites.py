"""TEST-01: persisted model test suites (core/validation/test_suites.py)."""

import json

import pytest

from core.validation import test_suites as ts


def _q(results):
    """Fake query runner: maps a query substring to a result dict."""

    def run(query):
        for key, res in results.items():
            if key in query:
                return res
        return {"success": True, "rows": [], "row_count": 0, "columns": []}

    return run


def _ok(value, n=1):
    return {
        "success": True,
        "rows": [{"[v]": value}] * n,
        "row_count": n,
        "columns": ["[v]"],
        "execution_time_ms": 12,
    }


def test_put_list_remove_roundtrip(tmp_path):
    path = str(tmp_path / "s.suite.json")
    r = ts.put_test(
        path, {"id": "a", "type": "measure_assertion", "measure": "Revenue", "expected": 1}
    )
    assert r["action"] == "added"
    r = ts.put_test(
        path, {"id": "a", "type": "measure_assertion", "measure": "Revenue", "expected": 2}
    )
    assert r["action"] == "replaced"
    lst = ts.list_tests(path)
    assert lst["test_count"] == 1 and lst["tests"][0]["id"] == "a"
    assert ts.remove_test(path, "a")["success"] is True
    assert ts.remove_test(path, "a")["success"] is False
    assert json.loads(open(path, encoding="utf-8").read())["tests"] == []


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "measure_assertion", "measure": "M", "expected": 1},  # no id
        {"id": "x", "type": "nope"},
        {"id": "x", "type": "measure_assertion", "measure": "M"},  # no expected
        {"id": "x", "type": "dax_assertion", "query": 'EVALUATE ROW("a",1)', "expect": 3},
        {"id": "x", "type": "metadata_compliance", "rule": "made_up"},
        {"id": "bad id!", "type": "measure_assertion", "measure": "M", "expected": 1},
    ],
)
def test_validate_rejects(bad):
    with pytest.raises(ts.SuiteError):
        ts.validate_test(bad)


def test_measure_assertion_pass_fail_and_filters():
    seen = {}

    def run(q):
        seen["q"] = q
        return _ok(100.0004)

    t = {
        "id": "a",
        "type": "measure_assertion",
        "measure": "Revenue",
        "expected": 100,
        "filters": ["'D'[Y]=2025"],
    }
    assert ts.run_test(t, query=run)["status"] == "pass"
    assert "CALCULATE([Revenue], 'D'[Y]=2025)" in seen["q"]
    t["expected"] = 90
    r = ts.run_test(t, query=run)
    assert r["status"] == "fail" and "expected 90" in r["failures"][0]


def test_measure_assertion_blank():
    t = {"id": "b", "type": "measure_assertion", "measure": "[M]", "expected": None}
    assert (
        ts.run_test(t, query=lambda q: {"success": True, "rows": [{"[v]": None}]})["status"]
        == "pass"
    )
    assert ts.run_test(t, query=lambda q: _ok(0))["status"] == "fail"


def test_dax_assertion_expect_forms():
    res = {
        "success": True,
        "rows": [{"'T'[a]": 1, "[b]": "x"}, {"'T'[a]": 2, "[b]": "y"}],
        "row_count": 2,
        "columns": ["'T'[a]", "[b]"],
    }
    run = lambda q: res  # noqa: E731
    base = {"id": "d", "type": "dax_assertion", "query": "EVALUATE T"}
    assert (
        ts.run_test({**base, "expect": {"row_count": 2, "columns": ["a", "b"]}}, query=run)[
            "status"
        ]
        == "pass"
    )
    assert (
        ts.run_test(
            {**base, "expect": {"rows": [{"a": 2, "b": "y"}, {"a": 1, "b": "x"}]}}, query=run
        )["status"]
        == "pass"
    )
    assert (
        ts.run_test(
            {**base, "expect": {"rows": [{"a": 2, "b": "y"}, {"a": 1, "b": "x"}], "ordered": True}},
            query=run,
        )["status"]
        == "fail"
    )
    assert ts.run_test({**base, "expect": {"min_rows": 3}}, query=run)["status"] == "fail"
    err = lambda q: {"success": False, "error": "The column 'X' cannot be found"}  # noqa: E731
    assert (
        ts.run_test({**base, "expect": {"error": "cannot be found"}}, query=err)["status"] == "pass"
    )
    assert ts.run_test({**base, "expect": {"row_count": 1}}, query=err)["status"] == "fail"


def test_rls_uses_role_runner():
    calls = []

    def as_role(q, roles, upn):
        calls.append((roles, upn))
        return _ok(3)

    t = {
        "id": "r",
        "type": "rls_validation",
        "role": "BE",
        "query": 'EVALUATE ROW("n",1)',
        "expect": {"scalar": 3},
    }
    assert ts.run_test(t, query=lambda q: _ok(9), query_as_role=as_role)["status"] == "pass"
    assert calls == [(["BE"], None)]
    assert ts.run_test(t, query=lambda q: _ok(9))["status"] == "error"


def test_performance_budget_median():
    times = iter([100, 3000, 120])
    run = lambda q: {"success": True, "rows": [], "execution_time_ms": next(times)}  # noqa: E731
    r = ts.run_test(
        {"id": "p", "type": "performance_budget", "query": "EVALUATE X", "max_ms": 500, "runs": 3},
        query=run,
    )
    assert r["status"] == "pass" and r["detail"]["median_ms"] == 120


def test_regression_snapshot_delegates():
    t = {"id": "s", "type": "regression_snapshot", "baseline_path": "b.json"}
    assert (
        ts.run_test(t, query=_q({}), compare_baseline=lambda a: {"success": True, "changed": 0})[
            "status"
        ]
        == "pass"
    )
    r = ts.run_test(
        t,
        query=_q({}),
        compare_baseline=lambda a: {"success": True, "changed": 2, "differences": [{}, {}]},
    )
    assert r["status"] == "fail"


def test_metadata_rules():
    info = {
        "success": True,
        "rows": [
            {"[Name]": "Revenue", "[Description]": ""},
            {"[Name]": "kpi_x", "[Description]": "d"},
        ],
    }
    run = _q({"ErrorMessage": {"success": True, "rows": []}, "INFO.MEASURES()": info})
    assert (
        ts.run_test(
            {"id": "m1", "type": "metadata_compliance", "rule": "no_measure_errors"}, query=run
        )["status"]
        == "pass"
    )
    assert (
        ts.run_test(
            {
                "id": "m2",
                "type": "metadata_compliance",
                "rule": "measures_exist",
                "measures": ["[Revenue]", "Gone"],
            },
            query=run,
        )["status"]
        == "fail"
    )
    assert (
        ts.run_test(
            {
                "id": "m3",
                "type": "metadata_compliance",
                "rule": "measure_name_regex",
                "pattern": "^[A-Z]",
            },
            query=run,
        )["status"]
        == "fail"
    )
    assert (
        ts.run_test(
            {"id": "m4", "type": "metadata_compliance", "rule": "descriptions_required"}, query=run
        )["status"]
        == "fail"
    )


def test_run_suite_filters_skips_records_and_exit_code(tmp_path):
    path = str(tmp_path / "s.json")
    ts.put_test(
        path,
        {"id": "a", "type": "measure_assertion", "measure": "M", "expected": 1, "tags": ["smoke"]},
    )
    ts.put_test(path, {"id": "b", "type": "measure_assertion", "measure": "M", "expected": 2})
    ts.put_test(
        path, {"id": "c", "type": "measure_assertion", "measure": "M", "expected": 1, "skip": True}
    )
    summary = ts.run_suite(path, query=lambda q: _ok(1))
    assert (summary["passed"], summary["failed"], summary["skipped"], summary["exit_code"]) == (
        1,
        1,
        1,
        1,
    )
    assert ts.run_suite(path, query=lambda q: _ok(1), tags=["smoke"])["exit_code"] == 0
    # N-19: the last run lives in a sidecar; the committed suite file is not rewritten
    assert "last_run" not in json.loads(open(path, encoding="utf-8").read())
    assert ts.list_tests(path)["last_run"]["failed"] == 0


def test_crash_in_query_is_error_not_exception():
    def boom(q):
        raise RuntimeError("engine gone")

    r = ts.run_test(
        {"id": "a", "type": "measure_assertion", "measure": "M", "expected": 1}, query=boom
    )
    assert r["status"] == "error" and "engine gone" in r["failures"][0]


def test_export_junit_and_json(tmp_path):
    summary = {
        "name": "S",
        "ran_at": "t",
        "failed": 1,
        "errors": 0,
        "skipped": 0,
        "results": [
            {"id": "a", "type": "x", "status": "pass", "duration_ms": 5},
            {"id": "b", "type": "x", "status": "fail", "failures": ["x < y"], "duration_ms": 7},
        ],
    }
    j = ts.export_results(summary, str(tmp_path / "r.xml"), "junit")
    xml = open(j["output_path"], encoding="utf-8").read()
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml.encode("utf-8"))
    assert root.get("tests") == "2" and root.find("testcase/failure").get("message") == "x < y"
    ts.export_results(summary, str(tmp_path / "r.json"), "json")
    assert json.loads(open(tmp_path / "r.json", encoding="utf-8").read())["failed"] == 1
    assert ts.export_results(summary, str(tmp_path / "r.txt"), "csv")["success"] is False
