"""F-10: suite generation (core/validation/test_generator.py) with a fake builder and engine."""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from core.validation import test_generator as tg
from core.validation import test_suites as ts


class FakeBuilder:
    def __init__(self):
        self.visuals = {
            "Overview": [
                {
                    "id": "vm",
                    "type": "matrix",
                    "title": "P&L",
                    "measures": ["Sales"],
                    "is_slicer": False,
                    "is_data_visual": True,
                },
                {
                    "id": "vc",
                    "type": "card",
                    "title": "KPI",
                    "measures": ["Revenue"],
                    "is_slicer": False,
                    "is_data_visual": True,
                },
                {
                    "id": "vb",
                    "type": "barChart",
                    "title": "Bars",
                    "measures": ["Sales"],
                    "is_slicer": False,
                    "is_data_visual": True,
                },
                {
                    "id": "vs",
                    "type": "slicer",
                    "title": "Year",
                    "measures": [],
                    "is_slicer": True,
                    "is_data_visual": False,
                },
                {
                    "id": "vx",
                    "type": "card",
                    "title": "Empty",
                    "measures": [],
                    "is_slicer": False,
                    "is_data_visual": True,
                },
            ]
        }

    def list_pages(self):
        return [{"id": "pg1", "name": "Overview", "ordinal": 0}]

    def list_visuals(self, page):
        assert page in ("Overview", "pg1")
        return self.visuals["Overview"]

    def list_slicers(self, page):
        return [
            SimpleNamespace(
                slicer_id="s1",
                table="Date",
                column="Year",
                selected_values=[2025, 2024],
                is_inverted=False,
                composite_values=None,
                extra_columns=[],
            ),
            SimpleNamespace(
                slicer_id="s2",
                table="Date",
                column="Month",
                selected_values=[],
                is_inverted=False,
                composite_values=None,
                extra_columns=[],
            ),
        ]

    def build_visual_query(self, page, visual_id=None, **k):
        if visual_id == "vx":
            return None
        return SimpleNamespace(
            dax_query=f'EVALUATE ROW("{visual_id}", 1)',
            fidelity="partial" if visual_id == "vb" else "full",
            fidelity_notes=["a slicer was skipped"] if visual_id == "vb" else [],
            filter_breakdown={"slicer": [{"table": "Date", "column": "Year", "values": [2025]}]},
        )


def _runner(values=None, slow=()):
    values = values or {}

    def run(q):
        if q.startswith('EVALUATE ROW("v"'):
            for m, v in values.items():
                if f"[{m}]" in q:
                    return {"success": True, "rows": [{"[v]": v}]}
            return {"success": False, "error": "unknown measure"}
        if "COUNTROWS(FILTER(VALUES" in q:
            return {"success": True, "rows": [{"[n]": 2}]}
        vid = q.split('"')[1]
        rows = [{"[Sales]": 100.0 + i} for i in range(3 if vid != "vb" else 80)]
        return {
            "success": True,
            "rows": rows,
            "columns": ["[Sales]"],
            "row_count": len(rows),
            "execution_time_ms": 900 if vid in slow else 20,
        }

    return run


def test_stable_ids_are_safe_and_bounded():
    assert tg.stable_id("snap", "pg 1", "v/1") == "snap.pg_1.v_1"
    long = tg.stable_id("snap", "p" * 60, "v" * 60)
    assert len(long) <= 80 and long == tg.stable_id("snap", "p" * 60, "v" * 60)
    assert long != tg.stable_id("snap", "p" * 60, "w" * 60)


def test_generate_page_covers_every_test_kind():
    res = tg.generate(
        "page", builder=FakeBuilder(), run_query=_runner({"Revenue": 42.0}), page_name="Overview"
    )
    assert res["success"]
    by_id = {t["id"]: t for t in res["tests"]}
    assert set(by_id) == {
        "snap.pg1.vm",
        "tie.pg1.vm",
        "snap.pg1.vc",
        "snap.pg1.vb",
        "slicer.pg1.s1",
        "kpi.Revenue",
    }
    snap = by_id["snap.pg1.vm"]
    assert (
        snap["type"] == "dax_assertion"
        and snap["provenance"] == "snapshot"
        and snap["generated"] is True
    )
    assert snap["fidelity"] == "full" and snap["expect"]["rows"][0] == {"Sales": 100.0}
    assert snap["base"]["slicers"][0]["column"] == "Year"
    # a big result keeps the shape only; partial fidelity is recorded and tagged
    assert by_id["snap.pg1.vb"]["expect"] == {"row_count": 80, "columns": ["Sales"]}
    assert (
        by_id["snap.pg1.vb"]["fidelity"] == "partial"
        and "fidelity-partial" in by_id["snap.pg1.vb"]["tags"]
    )
    assert (
        by_id["tie.pg1.vm"]["type"] == "tieout" and by_id["tie.pg1.vm"]["provenance"] == "invariant"
    )
    assert (
        by_id["kpi.Revenue"]["expected"] == 42.0
        and by_id["kpi.Revenue"]["type"] == "measure_assertion"
    )
    assert by_id["slicer.pg1.s1"]["expect"] == {"scalar": 2}
    assert "IN {2025, 2024}" in by_id["slicer.pg1.s1"]["query"]
    assert [s["visual"] for s in res["skipped"]] == ["vx"]
    # every generated test is a valid suite test
    for t in res["tests"]:
        ts.validate_test(t)


def test_performance_budget_only_for_heavy_visuals():
    res = tg.generate(
        "page",
        builder=FakeBuilder(),
        run_query=_runner({"Revenue": 1.0}, slow=("vm",)),
        page_name="pg1",
        include_perf=True,
        headroom=2.0,
    )
    perf = [t for t in res["tests"] if t["type"] == "performance_budget"]
    assert [t["id"] for t in perf] == ["perf.pg1.vm"]
    assert perf[0]["max_ms"] == 1800 and perf[0]["measured_median_ms"] == 900.0


def test_page_not_found_and_missing_args():
    r = tg.generate("page", builder=FakeBuilder(), run_query=_runner(), page_name="Nope")
    assert r["error_type"] == "not_found"
    assert (
        tg.generate("page", builder=FakeBuilder(), run_query=_runner())["error_type"]
        == "missing_argument"
    )
    assert (
        tg.generate("page", builder=None, run_query=_runner(), page_name="x")["error_type"]
        == "pbip_path_required"
    )
    assert tg.generate("bogus", run_query=_runner())["error_type"] == "invalid_parameters"


def test_roles_and_measures_sources_need_no_pbip():
    r = tg.generate("roles", run_query=_runner(), roles=["Advisor", "Admin"], as_user="a@x.com")
    assert [t["id"] for t in r["tests"]] == ["rls.Advisor", "rls.Admin"]
    assert r["tests"][0]["type"] == "rls_audit" and r["tests"][0]["as_user"] == "a@x.com"
    m = tg.generate(
        "measures", run_query=_runner({"Revenue": 7.0}), measures=["Revenue", "Missing"]
    )
    assert [t["id"] for t in m["tests"]] == ["kpi.Revenue"] and m["skipped"][0][
        "measure"
    ] == "Missing"


def test_max_visuals_caps_the_run():
    res = tg.generate(
        "page",
        builder=FakeBuilder(),
        run_query=_runner({"Revenue": 1.0}),
        page_name="pg1",
        max_visuals=1,
    )
    snaps = [t for t in res["tests"] if t["id"].startswith("snap.")]
    assert len(snaps) == 1 and any("max_visuals" in s["reason"] for s in res["skipped"])


# --------------------------------------------------------------------------- diff / write
def _gen():
    return tg.generate(
        "page", builder=FakeBuilder(), run_query=_runner({"Revenue": 42.0}), page_name="pg1"
    )["tests"]


def test_regenerate_updates_instead_of_duplicating(tmp_path):
    path = str(tmp_path / "s.json")
    tests = _gen()
    d1 = tg.diff_against_suite(tests, [])
    assert len(d1["added"]) == len(tests) and not d1["changed"]
    w1 = ts.put_tests(path, tests)
    assert len(w1["added"]) == len(tests) and w1["test_count"] == len(tests)
    d2 = tg.diff_against_suite(_gen(), ts.load_suite(path)["tests"])
    assert not d2["added"] and not d2["changed"] and len(d2["unchanged"]) == len(tests)
    w2 = ts.put_tests(path, _gen())
    assert (
        w2["added"] == [] and len(w2["replaced"]) == len(tests) and w2["test_count"] == len(tests)
    )


def test_changed_expectation_is_held_back_until_accepted(tmp_path):
    path = str(tmp_path / "s.json")
    ts.put_tests(path, _gen())
    new = _gen()
    for t in new:
        if t["id"] == "kpi.Revenue":
            t["expected"] = 99.0
    diff = tg.diff_against_suite(new, ts.load_suite(path)["tests"])
    assert [c["id"] for c in diff["changed"]] == ["kpi.Revenue"]
    assert (
        diff["changed"][0]["old"]["expected"] == 42.0
        and diff["changed"][0]["new"]["expected"] == 99.0
    )
    write, held = tg.select_for_write(new, diff)
    assert held == ["kpi.Revenue"] and "kpi.Revenue" not in {t["id"] for t in write}
    ts.put_tests(path, write)
    kept = {t["id"]: t for t in ts.load_suite(path)["tests"]}
    assert kept["kpi.Revenue"]["expected"] == 42.0  # the reviewed expectation survived
    write2, held2 = tg.select_for_write(new, diff, accept_changes=True)
    assert held2 == []
    ts.put_tests(path, write2)
    assert {t["id"]: t for t in ts.load_suite(path)["tests"]}["kpi.Revenue"]["expected"] == 99.0


def test_hand_written_test_with_same_id_is_never_overwritten_silently(tmp_path):
    path = str(tmp_path / "s.json")
    ts.put_test(
        path,
        {"id": "kpi.Revenue", "type": "measure_assertion", "measure": "Revenue", "expected": 1},
    )
    diff = tg.diff_against_suite(_gen(), ts.load_suite(path)["tests"])
    assert [c["id"] for c in diff["hand_written_conflicts"]] == ["kpi.Revenue"]
    _write, held = tg.select_for_write(_gen(), diff)
    assert "kpi.Revenue" in held


def test_stale_generated_tests_are_reported_never_deleted(tmp_path):
    path = str(tmp_path / "s.json")
    tests = _gen()
    ts.put_tests(path, tests)
    fewer = [t for t in tests if t["id"] != "snap.pg1.vc"]
    diff = tg.diff_against_suite(fewer, ts.load_suite(path)["tests"])
    assert diff["stale"] == ["snap.pg1.vc"]
    ts.put_tests(path, fewer)
    assert "snap.pg1.vc" in {t["id"] for t in ts.load_suite(path)["tests"]}


def test_performance_budget_jitter_is_not_a_change():
    a = {
        "id": "perf.x",
        "type": "performance_budget",
        "query": "q",
        "max_ms": 1000,
        "generated": True,
    }
    b = dict(a, max_ms=1300)
    assert tg.diff_against_suite([b], [a])["unchanged"] == ["perf.x"]
    assert tg.diff_against_suite([dict(a, max_ms=3000)], [a])["changed"]


def test_put_tests_validates_before_writing(tmp_path):
    path = str(tmp_path / "s.json")
    with pytest.raises(ts.SuiteError):
        ts.put_tests(
            path,
            [
                {"id": "ok", "type": "measure_assertion", "measure": "M", "expected": 1},
                {"id": "bad", "type": "dax_assertion", "query": "EVALUATE X", "expect": {}},
            ],
        )
    assert not (tmp_path / "s.json").exists()


# --------------------------------------------------------------------------- suite runner: new types
def test_suite_runs_rls_audit_and_tieout_through_the_engines(tmp_path):
    path = str(tmp_path / "s.json")
    ts.put_test(path, {"id": "rls", "type": "rls_audit", "roles": ["A"]})
    ts.put_test(
        path, {"id": "tie", "type": "tieout", "mode": "A_vs_B", "measure_a": "A", "measure_b": "B"}
    )
    r = ts.run_suite(
        path,
        query=lambda q: {"success": True, "rows": []},
        rls_audit=lambda t: {"success": True, "findings": [], "roles_audited": 1, "summary": {}},
        tieout=lambda t: {
            "success": True,
            "ties": False,
            "mode": "A_vs_B",
            "difference_count": 1,
            "differences": [{"label": "grand total", "a": 1, "b": 2}],
        },
        record=False,
    )
    by = {x["id"]: x for x in r["results"]}
    assert by["rls"]["status"] == "pass"
    assert by["tie"]["status"] == "fail" and "grand total" in by["tie"]["failures"][0]
    assert r["exit_code"] == 1


def test_rls_audit_test_fails_on_fail_open_and_honours_allow(tmp_path):
    path = str(tmp_path / "s.json")
    ts.put_test(path, {"id": "rls", "type": "rls_audit"})
    ts.put_test(path, {"id": "rls2", "type": "rls_audit", "allow": ["fail_open"]})
    audit = lambda t: {  # noqa: E731
        "success": True,
        "roles_audited": 1,
        "summary": {},  # noqa: E731
        "findings": [{"severity": "critical", "code": "fail_open", "role": "R", "message": "x"}],
    }
    r = ts.run_suite(path, query=lambda q: {}, rls_audit=audit, record=False)
    by = {x["id"]: x["status"] for x in r["results"]}
    assert by == {"rls": "fail", "rls2": "pass"}


def test_new_types_without_an_engine_error_cleanly(tmp_path):
    path = str(tmp_path / "s.json")
    ts.put_test(path, {"id": "rls", "type": "rls_audit"})
    r = ts.run_suite(path, query=lambda q: {}, record=False)
    assert r["results"][0]["status"] == "error"


@pytest.mark.parametrize(
    "test",
    [
        {"id": "t1", "type": "tieout", "mode": "visual"},
        {"id": "t2", "type": "tieout", "mode": "visual", "page_name": "P"},
        {"id": "t3", "type": "tieout", "mode": "A_vs_B", "measure_a": "A"},
        {"id": "t4", "type": "tieout", "mode": "page_vs_page", "page_a": "A"},
        {"id": "t5", "type": "tieout", "mode": "nope"},
    ],
)
def test_tieout_test_definition_is_validated(test):
    with pytest.raises(ts.SuiteError):
        ts.validate_test(copy.deepcopy(test))


def test_tieout_test_definitions_that_are_valid():
    ts.validate_test({"id": "a", "type": "tieout", "page_name": "P", "visual_id": "v"})
    ts.validate_test({"id": "b", "type": "tieout", "measure_a": "A", "measure_b": "B"})
    ts.validate_test(
        {
            "id": "c",
            "type": "tieout",
            "mode": "page_vs_page",
            "page_a": "A",
            "page_b": "B",
            "measure": "M",
        }
    )
