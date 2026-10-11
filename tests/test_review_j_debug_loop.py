"""Review slice J — debug_loop (J-09 gating, J-10 measure column + equivalence
through the visual query, J-13 cancellation, J-26 clamps / first original / TMDL /
screenshot names). Everything PBI-related is faked."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from core.autonomous.loops import debug_loop as dl
from core.autonomous.loops.debug_loop import DebugLoopConfig


@pytest.fixture
def project(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "MyReport.pbip").write_text("{}", encoding="utf-8")
    pages = proj / "MyReport.Report" / "definition" / "pages"
    (pages / "abc123").mkdir(parents=True)
    (proj / "MyReport.Report" / "definition" / "report.json").write_text("{}", encoding="utf-8")
    (pages / "abc123" / "page.json").write_text(
        json.dumps({"displayName": "Dashboard", "name": "abc123"}), encoding="utf-8"
    )
    return proj


def _cfg(project=None, **kw):
    base = dict(
        pbip_path=str(project / "MyReport.pbip") if project else "/tmp/x.pbip",
        page="Dashboard",
        visual="vis-1",
        take_screenshot=False,
    )
    base.update(kw)
    return DebugLoopConfig(**base)


# ---------------------------------------------------------------------------
# J-09 — live_tom goes through the destructive gate
# ---------------------------------------------------------------------------
def test_j09_live_tom_blocked_under_strict_without_confirm(monkeypatch, project):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "STRICT")
    applied = []
    cfg = _cfg(project, fix_strategy="live_tom", live_tom_apply_fn=lambda *a: applied.append(1))
    with patch.object(dl, "_invoke_debug_visual", side_effect=AssertionError("must not run")):
        out = dl.run_debug_loop(cfg, connection_state=None)
    assert out["success"] is False
    assert out["final_status"] == "gate_blocked"
    assert out["error_type"] == "confirm_destructive_required"
    assert applied == []


def test_j09_live_tom_light_passes_with_advisory(monkeypatch, project):
    with patch.object(
        dl, "_invoke_debug_visual", return_value={"success": True, "rows": [{"v": 1}]}
    ):
        out = dl.run_debug_loop(_cfg(project, fix_strategy="live_tom"), connection_state=None)
    assert out["final_status"] == "resolved"
    assert "gate_notice" in out


def test_j09_allow_llm_fix_needs_confirm_under_warn(monkeypatch, project):
    monkeypatch.setenv("MCP_AUTONOMOUS_GATE_POLICY", "WARN")
    out = dl.run_debug_loop(
        _cfg(project, fix_strategy="live_tom", allow_llm_fix=True), connection_state=None
    )
    assert out["final_status"] == "gate_blocked"
    ok = dl._loop_gate(
        _cfg(project, fix_strategy="live_tom", allow_llm_fix=True, confirm_destructive=True)
    )
    assert ok[0] is None


def test_j09_handler_coerces_string_false(monkeypatch):
    from server.handlers._debug import audit

    seen = {}
    monkeypatch.setattr(
        "core.autonomous.loops.debug_loop.run_debug_loop",
        lambda cfg, connection_state: seen.setdefault("cfg", cfg) and {"success": True},
    )
    audit.handle_debug_loop(
        {
            "pbip_path": "x.pbip",
            "page": "P",
            "visual": "V",
            "allow_llm_fix": "false",
            "take_screenshot": "false",
            "trace": "0",
            "confirm_destructive": "true",
        }
    )
    cfg = seen["cfg"]
    assert cfg.allow_llm_fix is False and cfg.take_screenshot is False and cfg.trace is False
    assert cfg.confirm_destructive is False  # only a real boolean True is the break-glass


# ---------------------------------------------------------------------------
# J-10 — anomaly detection reads the MEASURE column, every row
# ---------------------------------------------------------------------------
def test_j10_year_label_is_not_mistaken_for_the_value():
    res = {"rows": [{"'d Date'[Year]": 2024, "[Total_Sales]": 10.0}]}
    cfg = _cfg(measure="Sales[Total Sales]", expected=10.0)
    assert dl._detect_anomaly(res, cfg) is None
    assert dl._primary_scalar(res["rows"], "Total Sales") == 10.0


def test_j10_blank_category_label_is_not_a_blank_measure():
    res = {
        "rows": [{"[Region]": None, "[Total Sales]": 5.0}, {"[Region]": "EU", "[Total Sales]": 3.0}]
    }
    assert dl._detect_anomaly(res, _cfg(measure="Total Sales")) is None


def test_j10_nan_in_any_row_is_found():
    res = {"rows": [{"[Year]": 2023, "[m]": 1.0}, {"[Year]": 2024, "[m]": float("nan")}]}
    a = dl._detect_anomaly(res, _cfg(measure="m"))
    assert a["kind"] == "nan" and a["row"] == 1


def test_j10_unlocatable_measure_is_indeterminate_and_never_fixed(project):
    applied = []
    cfg = _cfg(
        project,
        measure="Nope",
        fix_strategy="live_tom",
        live_tom_apply_fn=lambda *a: applied.append(1),
    )
    rows = {"success": True, "rows": [{"[Year]": 2024, "[Other]": 1}]}
    with patch.object(dl, "_invoke_debug_visual", return_value=rows):
        out = dl.run_debug_loop(cfg, connection_state=None)
    assert out["final_status"] == "aborted_indeterminate"
    assert applied == []


def test_j10_scalar_expected_on_multirow_visual_is_indeterminate():
    res = {"rows": [{"[m]": 1.0}, {"[m]": 2.0}]}
    assert dl._detect_anomaly(res, _cfg(measure="m", expected=1.0))["kind"] == "expected_ambiguous"


class _QE:
    def __init__(self, answers):
        self.answers = answers
        self.queries = []

    def validate_and_execute_dax(self, q, top_n=0, bypass_cache=False, **k):
        self.queries.append((q, bypass_cache))
        for key, rows in self.answers:
            if key in q:
                return {"success": True, "rows": rows}
        return {"success": False, "error": "unexpected"}


def test_j10_equivalence_uses_bare_name_and_the_visual_query(monkeypatch):
    from core.infrastructure import connection_state as cs_mod

    visual_q = "EVALUATE SUMMARIZECOLUMNS('d Date'[Year], \"v\", [Total Sales])"
    qe = _QE(
        [
            ("MEASURE 'Sales'[Total Sales]", [{"Year": 2023, "v": 1.0}, {"Year": 2024, "v": 99.0}]),
            ("SUMMARIZECOLUMNS", [{"Year": 2023, "v": 1.0}, {"Year": 2024, "v": 2.0}]),
            ('ROW("value"', [{"value": 3.0}]),
        ]
    )
    monkeypatch.setattr(cs_mod.connection_state, "query_executor", qe, raising=False)
    v = dl._check_rewrite_equivalence(
        "Sales[Total Sales]", "SUM(Sales[Amt])", visual_dax=visual_q, table="Sales"
    )
    assert v["equivalent"] is False  # differs per row although the totals match
    assert v["scope"] == "grand_total_and_visual_query"
    first = qe.queries[0][0]
    assert "[Total Sales]" in first and "['Sales'" not in first
    assert all(bypass for _q, bypass in qe.queries)


def test_j10_define_override_merges_into_existing_define():
    q = 'DEFINE VAR x = 1\nEVALUATE ROW("a", [M])'
    out = dl._define_measure_override(q, "Sales", "M", "2")
    assert out.startswith("DEFINE\nMEASURE 'Sales'[M] = ( 2 )\n")
    assert out.count("DEFINE") == 1


# ---------------------------------------------------------------------------
# J-13 — a cancelled call stops iterating
# ---------------------------------------------------------------------------
def test_j13_cancelled_loop_stops(monkeypatch, project):
    monkeypatch.setattr(dl, "_cancelled", lambda: True)
    with patch.object(dl, "_invoke_debug_visual", side_effect=AssertionError("must not run")):
        out = dl.run_debug_loop(_cfg(project), connection_state=None)
    assert out["final_status"] == "cancelled" and out["iterations"] == 0


# ---------------------------------------------------------------------------
# J-26 — clamps, first original_dax, unique screenshot names
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("requested,used", [(-3, 1), (0, 1), (500, 20)])
def test_j26_max_iterations_is_clamped(project, requested, used):
    with patch.object(
        dl, "_invoke_debug_visual", return_value={"success": True, "rows": [{"v": None}]}
    ):
        out = dl.run_debug_loop(
            _cfg(
                project,
                max_iterations=requested,
                fix_strategy="live_tom",
                live_tom_apply_fn=lambda *a: {"success": True, "applied": True},
            ),
            connection_state=None,
        )
    assert out["iterations"] == used
    assert out["max_iterations_clamped"] == {"requested": requested, "used": used}


def test_j26_original_dax_is_the_first_pre_edit_body(project):
    bodies = iter(["ORIGINAL", "REWRITE-1", "REWRITE-2"])

    def apply(proposal, cfg, paths):
        return {"success": True, "applied": True, "original_dax": next(bodies)}

    with patch.object(
        dl, "_invoke_debug_visual", return_value={"success": True, "rows": [{"v": None}]}
    ):
        out = dl.run_debug_loop(
            _cfg(project, max_iterations=3, fix_strategy="live_tom", live_tom_apply_fn=apply),
            connection_state=None,
        )
    assert [h["applied_fix"]["original_dax"] for h in out["history"]] == ["ORIGINAL"] * 3
    assert out["history"][2]["applied_fix"]["previous_dax"] == "REWRITE-2"
    assert out["original_dax"] == "ORIGINAL"


def test_j26_screenshot_names_do_not_collide(tmp_path, monkeypatch):
    monkeypatch.setattr(dl.time, "time", lambda: 1700000000.0)
    a = dl._next_screenshot_path(str(tmp_path), 1)
    b = dl._next_screenshot_path(str(tmp_path), 1)
    assert a != b
