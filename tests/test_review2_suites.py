"""Deep review 2026-10-03 (slice N): 09_Validate suites (N-16, N-17, N-18, N-19)."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import pytest

from core.validation import test_suites as ts


def _ok(value, n=1, **extra):
    return {
        "success": True,
        "rows": [{"[v]": value}] * n,
        "row_count": n,
        "columns": ["[v]"],
        **extra,
    }


def _suite(tmp_path, *tests, name="s.json"):
    path = str(tmp_path / name)
    for t in tests:
        ts.put_test(path, t)
    return path


M = {"type": "measure_assertion", "measure": "M", "expected": 1}


# --- N-16 -------------------------------------------------------------------


def test_string_tags_select_tests_not_characters(tmp_path):
    path = _suite(tmp_path, {**M, "id": "a", "tags": ["smoke"]}, {**M, "id": "b"})
    r = ts.run_suite(path, query=lambda q: _ok(1), tags="smoke", record=False)
    assert [x["id"] for x in r["results"]] == ["a"] and r["exit_code"] == 0


def test_string_ids_are_not_substring_matched(tmp_path):
    path = _suite(tmp_path, {**M, "id": "rev"}, {**M, "id": "rev-2025"})
    r = ts.run_suite(path, query=lambda q: _ok(1), ids="rev-2025", record=False)
    assert [x["id"] for x in r["results"]] == ["rev-2025"]


def test_comma_separated_tags(tmp_path):
    path = _suite(
        tmp_path, {**M, "id": "a", "tags": ["x"]}, {**M, "id": "b", "tags": ["y"]}, {**M, "id": "c"}
    )
    r = ts.run_suite(path, query=lambda q: _ok(1), tags="x, y", record=False)
    assert sorted(x["id"] for x in r["results"]) == ["a", "b"]


def test_zero_tests_selected_is_not_green(tmp_path):
    path = _suite(tmp_path, {**M, "id": "a", "tags": ["smoke"]})
    r = ts.run_suite(path, query=lambda q: _ok(1), tags=["nope"], record=False)
    assert r["no_tests_ran"] is True and r["exit_code"] == 1 and r["all_passed"] is False
    assert r["success"] is False and r["error_type"] == "no_tests_ran"


def test_all_skipped_is_not_green(tmp_path):
    path = _suite(tmp_path, {**M, "id": "a", "skip": True})
    r = ts.run_suite(path, query=lambda q: _ok(1), record=False)
    assert r["no_tests_ran"] is True and r["exit_code"] == 1


def test_missing_suite_file_raises_not_found(tmp_path):
    with pytest.raises(ts.SuiteNotFound):
        ts.run_suite(str(tmp_path / "typo.json"), query=lambda q: _ok(1))


# --- N-17 -------------------------------------------------------------------


def test_junit_is_well_formed_with_quotes_angle_amp_newline_and_control(tmp_path):
    nasty = "Failed to resolve name \"Foo\" <x> & 'y'\nline2 \x00\x0b end"
    summary = {
        "name": 'S "q" & <z>',
        "ran_at": "t",
        "failed": 1,
        "errors": 1,
        "skipped": 1,
        "results": [
            {"id": 'a"b', "type": "t<", "status": "fail", "failures": [nasty], "duration_ms": 5},
            {"id": "c", "type": "t", "status": "error", "failures": ['boom & "'], "duration_ms": 1},
            {"id": "d", "type": "t", "status": "skipped"},
        ],
    }
    out = tmp_path / "r.xml"
    assert ts.export_results(summary, str(out), "junit")["success"] is True
    root = ET.parse(out).getroot()  # raises when not well-formed
    assert root.get("name") == 'S "q" & <z>'
    cases = root.findall("testcase")
    assert cases[0].get("name") == 'a"b'
    msg = cases[0].find("failure").get("message")
    assert 'Failed to resolve name "Foo" <x> & ' in msg and "\x00" not in msg and "line2" in msg
    assert cases[1].find("error") is not None and cases[2].find("skipped") is not None


def test_invalid_format_rejected_before_writing(tmp_path):
    out = tmp_path / "r.txt"
    assert ts.export_results({"results": []}, str(out), "csv")["success"] is False
    assert not out.exists()


# --- N-18 -------------------------------------------------------------------


@pytest.mark.parametrize(
    "expect",
    [{}, {"row_cnt": 999}, {"tolerance": 0.1}, {"ordered": True}, {"rows": [], "bogus": 1}],
)
def test_vacuous_or_unknown_expect_rejected(expect):
    with pytest.raises(ts.SuiteError):
        ts.validate_test(
            {"id": "x", "type": "dax_assertion", "query": "EVALUATE X", "expect": expect}
        )


def test_rls_expect_also_validated():
    with pytest.raises(ts.SuiteError):
        ts.validate_test(
            {"id": "x", "type": "rls_validation", "role": "R", "query": "EVALUATE X", "expect": {}}
        )


def test_bad_expect_in_persisted_suite_errors_instead_of_passing(tmp_path):
    path = str(tmp_path / "s.json")
    bad = {"id": "v", "type": "dax_assertion", "query": "EVALUATE X", "expect": {"row_cnt": 9}}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"tests": [bad]}, fh)
    r = ts.run_suite(path, query=lambda q: _ok(1), record=False)
    assert r["results"][0]["status"] == "error" and r["exit_code"] == 1


def test_int_vs_float_rows_equal():
    res = {"success": True, "rows": [{"T[a]": 1}], "row_count": 1, "columns": ["T[a]"]}
    assert ts.check_expect(res, {"rows": [{"a": 1.0}]}) == []


def test_rows_use_tolerance_and_message_names_first_differing_row():
    res = {"success": True, "rows": [{"a": 1.0004}, {"a": 5}], "row_count": 2}
    assert ts.check_expect(res, {"rows": [{"a": 1.0}, {"a": 5}], "ordered": True}) == []
    fails = ts.check_expect(res, {"rows": [{"a": 1.0}, {"a": 6}], "ordered": True})
    assert len(fails) == 1 and "row 1" in fails[0] and '"a": 5' in fails[0] and '"a": 6' in fails[0]
    fails = ts.check_expect(res, {"rows": [{"a": 1.0}, {"a": 6}]})
    assert "expected row not found" in fails[0] and '"a": 6' in fails[0]


def test_rows_unordered_match_and_extra_row():
    res = {"success": True, "rows": [{"a": 2}, {"a": 1}], "row_count": 2}
    assert ts.check_expect(res, {"rows": [{"a": 1}, {"a": 2}]}) == []
    assert "unexpected row" in ts.check_expect(res, {"rows": [{"a": 1}]})[0]


def test_nan_equals_nan_and_inf():
    assert ts._num_equal("NaN", float("nan"), 0.001) is True
    assert ts._num_equal(float("inf"), float("inf"), 0.001) is True
    assert ts._num_equal(float("nan"), 1, 0.001) is False


def test_truncated_result_cannot_assert_exact_row_count():
    res = {"success": True, "rows": [{"a": 1}] * 10000, "row_count": 10000, "truncated": True}
    assert "truncated" in ts.check_expect(res, {"row_count": 10000})[0]
    assert ts.check_expect(res, {"min_rows": 5000}) == []


# --- N-19 -------------------------------------------------------------------


def test_record_leaves_suite_file_byte_identical_and_writes_sidecar(tmp_path):
    path = _suite(tmp_path, {**M, "id": "a"})
    before = open(path, "rb").read()
    r = ts.run_suite(path, query=lambda q: _ok(1))
    assert open(path, "rb").read() == before
    side = json.loads(open(ts.last_run_path(path), encoding="utf-8").read())
    assert side["passed"] == 1 and r["passed"] == 1
    assert ts.list_tests(path)["last_run"]["passed"] == 1


def test_test_put_during_a_run_is_not_lost(tmp_path):
    path = _suite(tmp_path, {**M, "id": "slow"})

    def query(q):
        ts.put_test(path, {**M, "id": "added-mid-run"})  # a concurrent test_put
        return _ok(1)

    ts.run_suite(path, query=query)
    assert {t["id"] for t in ts.load_suite(path)["tests"]} == {"slow", "added-mid-run"}


def test_last_run_path_naming():
    assert ts.last_run_path("a/b/model.suite.json") == "a/b/model.suite.last-run.json"
    assert ts.last_run_path("a/b/model.suite") == "a/b/model.suite.last-run.json"


# --- N-19: handler path guard + classification ------------------------------------


@pytest.fixture
def handler(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from core.validation import path_guard
    from server.handlers._debug import tools

    monkeypatch.setattr(path_guard, "_resolved_root_list", lambda **_k: [tmp_path.resolve()])
    qe = SimpleNamespace(validate_and_execute_dax=lambda q, **_k: _ok(1))
    monkeypatch.setattr(
        tools, "connection_state", SimpleNamespace(is_connected=lambda: True, query_executor=qe)
    )
    return tools._handle_suite


def test_handler_blocks_suite_path_outside_allowed_roots(handler, tmp_path):
    outside = tmp_path.parent / "outside-suite.json"
    r = handler("test_put", {"suite_path": str(outside), "test": {**M, "id": "a"}})
    assert r["success"] is False and r["error_type"] == "path_traversal_blocked"
    assert not outside.exists()
    r = handler("test_remove", {"suite_path": str(outside), "test_id": "a"})
    assert r["error_type"] == "path_traversal_blocked"
    assert (
        handler("test_list", {"suite_path": "../../evil.json"})["error_type"]
        == "path_traversal_blocked"
    )


def test_handler_blocks_output_path_outside_allowed_roots(handler, tmp_path):
    path = str(tmp_path / "s.json")
    assert handler("test_put", {"suite_path": path, "test": {**M, "id": "a"}})["success"] is True
    outside = tmp_path.parent / "outside-report.xml"
    r = handler("test_run", {"suite_path": path, "output_path": str(outside)})
    assert r["passed"] == 1 and r["export"]["error_type"] == "path_traversal_blocked"
    assert not outside.exists()
    ok = handler("test_export", {"suite_path": path, "output_path": str(tmp_path / "r.xml")})
    assert ok["export"]["success"] is True and (tmp_path / "r.xml").exists()


def test_handler_missing_suite_is_not_found(handler, tmp_path):
    r = handler("test_run", {"suite_path": str(tmp_path / "typo.json")})
    assert r["success"] is False and r["error_type"] == "not_found"


def test_handler_string_tags_reach_the_runner(handler, tmp_path):
    path = str(tmp_path / "s.json")
    handler("test_put", {"suite_path": path, "test": {**M, "id": "a", "tags": ["smoke"]}})
    r = handler("test_run", {"suite_path": path, "tags": "smoke"})
    assert r["passed"] == 1 and r["exit_code"] == 0
    r = handler("test_run", {"suite_path": path, "tags": "nope"})
    assert r["exit_code"] == 1 and r["no_tests_ran"] is True


def test_suite_writers_are_classified_write_and_blocked_in_read_only_mode():
    from server.op_classification import READ, WRITE, classify, is_write

    assert classify("09_Validate", {"operation": "test_put"}) == WRITE
    assert classify("09_Validate", {"operation": "test_remove"}) == WRITE
    for op in ("test_list", "test_run", "test_export", "cross_visual", "capture_baseline"):
        assert classify("09_Validate", {"operation": op}) == READ, op
    assert classify("09_Validate", {}) == READ  # handler default = cross_visual
    assert is_write(classify("09_Validate", {"operation": "test_put"}))
