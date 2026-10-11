"""Tests for core.autonomous.loops.debug_loop.

Anomaly-detection unit tests are pure (no PBI). Loop-orchestration tests
mock everything PBI-related (handle_debug_visual, the Desktop Bridge ops)
so the same test runs on Linux/Mac CI without pywin32 or a live Desktop.

The loop verifies fixes via the Desktop Bridge (in-place reload + screenshot
of the RUNNING Desktop) — no close/reopen lifecycle, no autonomous mode.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from core.autonomous.loops import debug_loop as dl
from core.autonomous.loops.debug_loop import (
    DebugLoopConfig,
    _detect_anomaly,
    _primary_scalar,
)

# ---------------------------------------------------------------------------
# Anomaly detection (pure functions)
# ---------------------------------------------------------------------------


def _cfg(**overrides) -> DebugLoopConfig:
    base = dict(
        pbip_path="/tmp/x.pbip",
        page="P",
        visual="V",
    )
    base.update(overrides)
    return DebugLoopConfig(**base)


def test_primary_scalar_handles_dict_rows():
    rows = [{"col": 42}]
    assert _primary_scalar(rows) == 42


def test_primary_scalar_handles_empty():
    assert _primary_scalar([]) is None
    assert _primary_scalar(None) is None


def test_anomaly_blank():
    res = {"rows": [{"v": None}]}
    a = _detect_anomaly(res, _cfg())
    assert a is not None and a["kind"] == "blank"


def test_anomaly_nan():
    res = {"rows": [{"v": float("nan")}]}
    a = _detect_anomaly(res, _cfg())
    assert a is not None and a["kind"] == "nan"


def test_anomaly_infinity():
    res = {"rows": [{"v": float("inf")}]}
    a = _detect_anomaly(res, _cfg())
    assert a is not None and a["kind"] == "infinity"


def test_anomaly_expected_match_returns_none():
    res = {"rows": [{"v": 100.0}]}
    assert _detect_anomaly(res, _cfg(expected=100.0)) is None


def test_anomaly_expected_mismatch():
    res = {"rows": [{"v": 99.0}]}
    a = _detect_anomaly(res, _cfg(expected=100.0))
    assert a is not None and a["kind"] == "expected_mismatch"


def test_anomaly_predicate_pass():
    res = {"rows": [{"v": 50}]}
    cfg = _cfg(expected_predicate=lambda x: x > 10)
    assert _detect_anomaly(res, cfg) is None


def test_anomaly_predicate_fail():
    res = {"rows": [{"v": 5}]}
    cfg = _cfg(expected_predicate=lambda x: x > 10)
    a = _detect_anomaly(res, cfg)
    assert a is not None and a["kind"] == "predicate_failed"


def test_anomaly_fe_dominated():
    """FE > 5x SE flags as fe_dominated (when trace data present)."""
    res = {
        "rows": [{"v": 100}],
        "performance": {"storage_engine_ms": 10, "formula_engine_ms": 100},
    }
    a = _detect_anomaly(res, _cfg())
    assert a is not None and a["kind"] == "fe_dominated"


def test_anomaly_no_fe_imbalance():
    res = {
        "rows": [{"v": 100}],
        "performance": {"storage_engine_ms": 50, "formula_engine_ms": 60},
    }
    assert _detect_anomaly(res, _cfg()) is None


# ---------------------------------------------------------------------------
# run_debug_loop orchestration
# ---------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Minimal PBIP layout: enough for resolve_pbip_paths to succeed."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "MyReport.pbip").write_text("{}", encoding="utf-8")
    report = proj / "MyReport.Report"
    definition = report / "definition"
    pages = definition / "pages"
    pages.mkdir(parents=True)
    (definition / "report.json").write_text("{}", encoding="utf-8")
    page = pages / "abc123"
    page.mkdir()
    (page / "page.json").write_text(
        json.dumps({"displayName": "Dashboard", "name": "abc123"}), encoding="utf-8"
    )
    return proj


def test_loop_aborts_on_unresolvable_pbip(tmp_path: Path):
    result = dl.run_debug_loop(
        DebugLoopConfig(
            pbip_path=str(tmp_path / "nonexistent"),
            page="x",
            visual="y",
        ),
        connection_state=None,
    )

    assert result["success"] is False
    assert result["final_status"] == "aborted_no_path"


def test_loop_resolves_on_first_iteration(project: Path):
    """When the first DAX run hits the expected value, loop returns
    success after one iteration without invoking any bridge reload."""
    with patch.object(dl, "_invoke_debug_visual") as inv:
        inv.return_value = {
            "success": True,
            "rows": [{"v": 100.0}],
            "dax_query": "EVALUATE { 100 }",
        }
        with patch.object(dl, "_capture_visual_screenshot", return_value=None):
            result = dl.run_debug_loop(
                DebugLoopConfig(
                    pbip_path=str(project / "MyReport.pbip"),
                    page="Dashboard",
                    visual="vis-1",
                    expected=100.0,
                    max_iterations=3,
                    take_screenshot=False,
                ),
                connection_state=None,
            )

    assert result["success"] is True
    assert result["iterations"] == 1
    assert result["final_status"] == "resolved"
    assert len(result["history"]) == 1


def test_loop_returns_proposed_fix_in_ask_user_mode(project: Path):
    """Anomaly detected, fix_strategy='ask_user' -> exits with proposed_fix."""
    with patch.object(dl, "_invoke_debug_visual") as inv:
        inv.return_value = {
            "success": True,
            "rows": [{"v": None}],  # BLANK -> anomaly
        }
        with patch.object(dl, "_capture_visual_screenshot", return_value=None):
            result = dl.run_debug_loop(
                DebugLoopConfig(
                    pbip_path=str(project / "MyReport.pbip"),
                    page="Dashboard",
                    visual="vis-1",
                    max_iterations=3,
                    take_screenshot=False,
                    fix_strategy="ask_user",
                ),
                connection_state=None,
            )

    assert result["success"] is False
    assert result["final_status"] == "aborted_no_fix"
    step = result["history"][0]
    assert step["anomaly"] is not None
    assert step["proposed_fix"] is not None
    assert step["applied_fix"] is not None
    assert step["applied_fix"]["status"] == "deferred"


def test_tmdl_edit_stops_awaiting_reopen_instead_of_an_impossible_reload(project: Path):
    """J-26: the bridge reload pins reloadModelDefinition:false, so it can never
    apply a TMDL fix. This test used to pin "tmdl_edit -> bridge reload per
    iteration"; the loop now stops with final_status 'awaiting_reopen'."""
    calls = []

    def fake_edit(definition_path, proposal):
        return {"success": True}

    with patch.object(
        dl.desktop_bridge,
        "op_reload",
        side_effect=lambda **k: calls.append(("reload", k)) or {"success": True, "pid": 7},
    ):
        with patch.object(dl, "_invoke_debug_visual") as inv:
            inv.return_value = {"success": True, "rows": [{"v": None}]}
            with patch.object(dl, "_capture_visual_screenshot", return_value=None):
                result = dl.run_debug_loop(
                    DebugLoopConfig(
                        pbip_path=str(project / "MyReport.pbip"),
                        page="Dashboard",
                        visual="vis-1",
                        max_iterations=2,
                        take_screenshot=False,
                        fix_strategy="tmdl_edit",
                        tmdl_edit_fn=fake_edit,
                    ),
                    connection_state=None,
                )

    assert result["success"] is False
    assert result["iterations"] == 1
    assert result["final_status"] == "awaiting_reopen"
    assert "WITHOUT saving" in result["hint"]
    assert calls == []


def _reload_fix(proposal, cfg, paths):
    # A (report-level) fix that needs the running Desktop to re-read disk.
    return {"success": True, "applied": True, "requires_reload": True}


def test_loop_uses_bridge_reload_when_fix_requires_reload(project: Path):
    """A fix with requires_reload -> loop calls bridge op_reload (NOT close/reopen).
    DAX stays BLANK so it runs to max_iterations."""
    calls = []

    with patch.object(
        dl.desktop_bridge,
        "op_reload",
        side_effect=lambda **k: calls.append(("reload", k)) or {"success": True, "pid": 7},
    ):
        with patch.object(dl, "_invoke_debug_visual") as inv:
            inv.return_value = {"success": True, "rows": [{"v": None}]}
            with patch.object(dl, "_capture_visual_screenshot", return_value=None):
                result = dl.run_debug_loop(
                    DebugLoopConfig(
                        pbip_path=str(project / "MyReport.pbip"),
                        page="Dashboard",
                        visual="vis-1",
                        max_iterations=2,
                        take_screenshot=False,
                        fix_strategy="live_tom",
                        live_tom_apply_fn=_reload_fix,
                    ),
                    connection_state=None,
                )

    assert result["success"] is False
    assert result["iterations"] == 2
    assert result["final_status"] == "unresolved"
    assert sum(1 for c in calls if c[0] == "reload") == 2


def test_loop_aborts_when_bridge_reload_fails(project: Path):
    with patch.object(
        dl.desktop_bridge,
        "op_reload",
        return_value={"success": False, "error": "bridge not connected"},
    ):
        with patch.object(dl, "_invoke_debug_visual") as inv:
            inv.return_value = {"success": True, "rows": [{"v": None}]}
            with patch.object(dl, "_capture_visual_screenshot", return_value=None):
                result = dl.run_debug_loop(
                    DebugLoopConfig(
                        pbip_path=str(project / "MyReport.pbip"),
                        page="Dashboard",
                        visual="vis-1",
                        max_iterations=3,
                        take_screenshot=False,
                        fix_strategy="live_tom",
                        live_tom_apply_fn=_reload_fix,
                    ),
                    connection_state=None,
                )

    assert result["success"] is False
    assert result["final_status"] == "reload_failed"


# ---------------------------------------------------------------------------
# Screenshot wiring (via bridge)
# ---------------------------------------------------------------------------


def test_capture_visual_screenshot_uses_bridge_success(tmp_path: Path):
    """_capture_visual_screenshot returns the output path when the bridge
    op_screenshot succeeds."""
    from core.autonomous.loops.debug_loop import _capture_visual_screenshot

    def fake_shot(**k):
        return {"success": True, "output": k["output"]}

    with patch.object(dl.desktop_bridge, "op_screenshot", side_effect=fake_shot):
        out = _capture_visual_screenshot(
            pbip_file=tmp_path / "fake.pbip",
            screenshot_dir=str(tmp_path),
            iteration=1,
            page="Dashboard",
        )

    assert out is not None
    assert str(tmp_path) in out


def test_capture_visual_screenshot_returns_none_on_failure(tmp_path: Path):
    """When the bridge reports failure, the helper returns None."""
    from core.autonomous.loops.debug_loop import _capture_visual_screenshot

    with patch.object(
        dl.desktop_bridge,
        "op_screenshot",
        return_value={"success": False, "error": "not_connected"},
    ):
        out = _capture_visual_screenshot(
            pbip_file=tmp_path / "fake.pbip",
            screenshot_dir=str(tmp_path),
            iteration=1,
            page="Dashboard",
        )

    assert out is None
