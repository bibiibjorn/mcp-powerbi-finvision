"""F-18: 09_Profile record=true / operation=regress (core/debug/perf_history.py).

Statistics are ported from ~/.claude/scripts/pbi-telemetry/benchmark.py (median, noise band,
minimum runs); the store is output/perf/<project>.jsonl with a retention bound. No git, no
Desktop: ``_git_sha`` is faked.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core.debug import perf_history as ph

BASE = [1426, 1441, 1430, 1438, 1433, 1429]
FAST = [1285, 1298, 1290, 1301, 1288, 1293]


# --- statistics ----------------------------------------------------------------------------


def test_run_stats_median_mad_sigma():
    s = ph.run_stats([10, 12, 11, 13, 100, 12])
    assert s["n"] == 6 and s["median"] == 12 and s["min"] == 10 and s["max"] == 100
    assert s["mad"] == 1.0  # robust to the 100 outlier
    assert s["sigma"] > 30  # the stdev is not


def test_clear_win_is_a_verdict():
    r = ph.compare_runs(BASE, FAST)
    assert r["verdict"] == "better" and r["delta"] < 0
    assert r["delta_pct"] == pytest.approx(-9.7, abs=0.2)


def test_clear_regression_is_a_verdict():
    assert ph.compare_runs(FAST, BASE)["verdict"] == "worse"


def test_inside_the_noise_band_is_refused():
    # the 2026-08-03 case: 3,876 -> 4,662 ms with sigma 442 is NOT a result
    a = [3827, 3876, 4100, 3900, 5050, 3880]
    b = [4000, 4662, 4300, 3950, 5000, 4900]
    r = ph.compare_runs(a, b)
    assert r["verdict"] == "inconclusive"
    assert "noise" in r["reason"]


def test_too_few_runs_gives_no_verdict():
    r = ph.compare_runs([100, 101, 102], [50, 51, 52])
    assert r["verdict"] == "insufficient_data" and "6" in r["reason"]


def test_identical_medians_are_inconclusive():
    assert ph.compare_runs(BASE, list(BASE))["verdict"] == "inconclusive"


def test_cross_session_uses_a_stricter_bar():
    a = [100, 101, 99, 100, 102, 98]
    shifted = [x + 15 for x in a]  # noise ~1.9 ms: ratio ~0.12 -> inside 0.15, outside 0.075
    assert ph.compare_runs(a, shifted)["verdict"] == "worse"
    stricter = ph.compare_runs(a, shifted, cross_session=True)
    assert stricter["verdict"] == "inconclusive"
    assert any("session" in c for c in stricter["caveats"])


def test_cold_runs_are_a_caveat():
    r = ph.compare_runs(BASE, FAST, base_warmup=0, new_warmup=1)
    assert r["verdict"] == "better"
    assert any("warmup" in c.lower() and "baseline" in c for c in r["caveats"])


# --- store ----------------------------------------------------------------------------------


def _profile(runs_by_visual, mode="trace"):
    results = []
    for vid, runs in runs_by_visual.items():
        results.append(
            {
                "visual_id": vid,
                "visual_name": f"Visual {vid}",
                "visual_type": "card",
                "measures": [f"[M_{vid}]"],
                "runs_ms": runs,
                "avg_time_ms": sum(runs) / len(runs),
                "min_time_ms": min(runs),
                "max_time_ms": max(runs),
            }
        )
    return {"success": True, "page": "Overview", "mode": mode, "results": results}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(ph, "_git_sha", lambda path: ("abc1234def", False))
    monkeypatch.setattr(ph, "SESSION_ID", "sess-1")
    return tmp_path


def _record(env, runs_by_visual, **kw):
    kw.setdefault("project", "Finance")
    kw.setdefault("directory", env)
    kw.setdefault("pbip_path", "C:/repo/Finance.pbip")
    kw.setdefault("model_generation", 7)
    return ph.record_profile(_profile(runs_by_visual), **kw)


def test_record_appends_visual_and_measure_rows_with_provenance(env):
    out = _record(env, {"v1": BASE, "v2": FAST}, label="before")
    assert out["records_written"] == 4  # 2 visuals + 2 measure attributions
    rows = [json.loads(x) for x in (env / "Finance.jsonl").read_text(encoding="utf-8").splitlines()]
    visual = next(r for r in rows if r["kind"] == "visual" and r["key"] == "v1")
    assert visual["median_ms"] == 1431.5 and visual["n"] == 6 and visual["mad_ms"] > 0
    assert visual["git_sha"] == "abc1234def" and visual["model_generation"] == 7
    assert visual["label"] == "before" and visual["run_id"] == out["run_id"]
    assert visual["warmup_runs"] == 1 and visual["session_id"] == "sess-1"
    measure = next(r for r in rows if r["kind"] == "measure")
    assert measure["attribution"] == "visual_total" and measure["via_visual"] in ("v1", "v2")


def test_record_warns_when_runs_are_below_the_protocol_minimum(env):
    out = _record(env, {"v1": [10, 11, 12]})
    assert any("6" in w for w in out["warnings"])


def test_non_trace_profile_records_zero_warmup(env):
    out = ph.record_profile(
        _profile({"v1": BASE}, mode="wallclock"),
        project="Finance",
        directory=env,
        pbip_path=None,
        model_generation=None,
    )
    row = json.loads((env / "Finance.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert row["warmup_runs"] == 0 and row["mode"] == "wallclock"
    assert any("warm-up" in w for w in out["warnings"])
    assert out["success"] is True


def test_retention_keeps_only_the_newest_records(env):
    for i in range(5):
        _record(env, {"v1": BASE}, label=f"r{i}", max_records=6)
    lines = (env / "Finance.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 6
    labels = {json.loads(x)["label"] for x in lines}
    assert "r4" in labels and "r0" not in labels


def test_project_name_is_filesystem_safe():
    assert ph.project_key("C:/repo/My Report.Report") == "My_Report.Report"
    assert ph.project_key(None) == "default"
    assert "/" not in ph.project_key("a/b\\c:d")


# --- regress ---------------------------------------------------------------------------------


def test_regress_compares_latest_against_a_labelled_baseline(env):
    _record(env, {"v1": BASE, "v2": BASE}, label="before")
    _record(env, {"v1": FAST, "v2": [x + 400 for x in BASE]}, label="after")
    out = ph.regress("Finance", "before", directory=env)
    assert out["success"] is True
    assert out["baseline"]["label"] == "before" and out["candidate"]["label"] == "after"
    by_key = {r["key"]: r for r in out["results"] if r["kind"] == "visual"}
    assert by_key["v1"]["verdict"] == "better" and by_key["v2"]["verdict"] == "worse"
    assert out["overall"] == "regressed"
    assert out["summary"]["better"] >= 1 and out["summary"]["worse"] >= 1
    assert out["session_match"] is True


def test_regress_by_sha_and_by_date(env, monkeypatch):
    stamps = iter(["2026-09-01T10:00:00+00:00", "2026-10-03T10:00:00+00:00"])
    monkeypatch.setattr(ph, "_utc", lambda: next(stamps))
    monkeypatch.setattr(ph, "_git_sha", lambda path: ("deadbee1234", False))
    _record(env, {"v1": BASE}, label="old")
    monkeypatch.setattr(ph, "_git_sha", lambda path: ("feedbac9999", False))
    _record(env, {"v1": FAST}, label="new")
    by_sha = ph.regress("Finance", "deadbee", directory=env)
    assert by_sha["baseline"]["label"] == "old" and by_sha["baseline"]["resolved_by"] == "git_sha"
    by_date = ph.regress("Finance", "2026-09-01", directory=env)
    assert by_date["baseline"]["label"] == "old" and by_date["baseline"]["resolved_by"] == "date"
    assert by_date["candidate"]["label"] == "new"


def test_regress_refuses_a_verdict_inside_the_noise(env):
    noisy_a = [3827, 3876, 4100, 3900, 5050, 3880]
    noisy_b = [4000, 4662, 4300, 3950, 5000, 4900]
    _record(env, {"v1": noisy_a}, label="a")
    _record(env, {"v1": noisy_b}, label="b")
    out = ph.regress("Finance", "a", directory=env)
    v1 = next(r for r in out["results"] if r["kind"] == "visual")
    assert v1["verdict"] == "inconclusive"
    assert out["overall"] == "inconclusive"


def test_regress_flags_a_cross_session_baseline(env, monkeypatch):
    _record(env, {"v1": BASE}, label="a")
    monkeypatch.setattr(ph, "SESSION_ID", "sess-2")
    _record(env, {"v1": FAST}, label="b")
    out = ph.regress("Finance", "a", directory=env)
    assert out["session_match"] is False
    assert any("session" in c for c in out["caveats"])


def test_regress_errors_are_typed(env):
    assert ph.regress("Finance", "x", directory=env)["error_type"] == "no_history"
    _record(env, {"v1": BASE}, label="a")
    assert ph.regress("Finance", "", directory=env)["error_type"] == "missing_argument"
    assert ph.regress("Finance", "nope", directory=env)["error_type"] == "baseline_not_found"
    assert (
        ph.regress("Finance", "a", directory=env)["error_type"] == "invalid_args"
    )  # baseline is the latest run


def test_regress_page_filter_and_unmatched_keys(env):
    _record(env, {"v1": BASE, "v2": BASE}, label="a")
    _record(env, {"v1": FAST, "v3": FAST}, label="b")
    out = ph.regress("Finance", "a", directory=env)
    assert "v2" in out["only_in_baseline"] and "v3" in out["only_in_candidate"]


# --- git ---------------------------------------------------------------------------------------


def test_git_sha_is_read_only_and_degrades(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[1:3] == ["rev-parse", "HEAD"]:
            return SimpleNamespace(returncode=0, stdout="0123456789abcdef\n")
        return SimpleNamespace(returncode=0, stdout=" M file\n")

    monkeypatch.undo()  # use the real _git_sha with a fake subprocess
    monkeypatch.setattr(ph.subprocess, "run", fake_run)
    assert ph._git_sha(str(tmp_path)) == ("0123456789abcdef", True)
    assert all(c[0] == "git" and c[1] in ("rev-parse", "status") for c in calls)

    def boom(*a, **k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(ph.subprocess, "run", boom)
    assert ph._git_sha(str(tmp_path)) == (None, None)


# --- 09_Profile wiring ----------------------------------------------------------------------


def test_handle_profile_record_and_regress(env, monkeypatch):
    from server.handlers._debug import tools

    monkeypatch.setattr(ph, "history_dir", lambda: env)
    monkeypatch.setattr(
        tools.connection_state,
        "get_pbip_folder_path",
        lambda: "C:/repo/Finance.Report",
        raising=False,
    )
    monkeypatch.setattr(tools.connection_state, "_model_generation", 3, raising=False)

    class _Ops:
        def __init__(self, runs):
            self.runs = runs

        def profile_page(self, **kw):
            return _profile({"v1": self.runs})

    for runs, label in ((BASE, "before"), (FAST, "after")):
        monkeypatch.setattr(tools, "_get_debug_operations", lambda r=runs: (_Ops(r), None))
        out = tools.handle_profile(
            {"operation": "page", "page_name": "Overview", "record": True, "label": label}
        )
        assert out["recorded"]["success"] is True and out["recorded"]["label"] == label

    res = tools.handle_profile({"operation": "regress", "baseline": "before"})
    assert res["success"] is True and res["project"] == "Finance.Report"
    assert res["results"][0]["verdict"] == "better" and res["overall"] == "improved"


def test_handle_profile_without_record_writes_nothing(env, monkeypatch):
    from server.handlers._debug import tools

    monkeypatch.setattr(ph, "history_dir", lambda: env)

    class _Ops:
        def profile_page(self, **kw):
            return _profile({"v1": BASE})

    monkeypatch.setattr(tools, "_get_debug_operations", lambda: (_Ops(), None))
    out = tools.handle_profile({"operation": "page", "page_name": "Overview"})
    assert "recorded" not in out and not list(env.glob("*.jsonl"))


def test_record_is_refused_in_read_only_mode(env, monkeypatch):
    from core.config import config_manager
    from server.handlers._debug import tools

    monkeypatch.setattr(ph, "history_dir", lambda: env)
    real_get = config_manager.config.get
    monkeypatch.setattr(
        config_manager.config,
        "get",
        lambda k, d=None: True if k == "security.read_only_mode" else real_get(k, d),
    )

    class _Ops:
        def profile_page(self, **kw):
            return _profile({"v1": BASE})

    monkeypatch.setattr(tools, "_get_debug_operations", lambda: (_Ops(), None))
    out = tools.handle_profile({"operation": "page", "page_name": "Overview", "record": True})
    assert out["recorded"]["error_type"] == "read_only_mode" and not list(env.glob("*.jsonl"))


def test_profile_page_results_carry_every_measured_run():
    from unittest.mock import MagicMock

    from core.debug.debug_operations import DebugOperations

    b = MagicMock()
    b.list_visuals.return_value = [
        {"id": "v1", "type": "card", "friendly_name": "C", "is_slicer": False, "measures": ["M"]}
    ]
    b.build_visual_query.return_value = SimpleNamespace(
        dax_query="EVALUATE ROW(1,1)", filter_context=SimpleNamespace(all_filters=lambda: [])
    )
    qe = MagicMock()
    times = iter([10.0, 12.0, 11.0])
    qe.validate_and_execute_dax.side_effect = lambda *a, **k: {
        "success": True,
        "execution_time_ms": next(times),
        "rows": [],
    }
    ops = DebugOperations.__new__(DebugOperations)
    ops.builder, ops.qe, ops.logger = b, qe, MagicMock()
    out = ops.profile_page("P", iterations=3)
    assert out["results"][0]["runs_ms"] == [10.0, 12.0, 11.0]
