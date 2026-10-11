"""09_Validate capture_baseline + compare_baseline (Phase 5 quick win).

Snapshot measure values to a JSON baseline file, then later re-run and diff
with tolerance — the regression guard around 15_DAX_Refactor / model edits
("prove the refactor didn't change the numbers").
"""

import json
from unittest.mock import MagicMock

import server.handlers._debug.tools as tools


def _patch_executor(monkeypatch, value_by_measure):
    """Patch connection_state so run_dax returns a scalar per measure."""
    cs = MagicMock()
    cs.is_connected.return_value = True
    qe = MagicMock()

    def fake_eval(query, *a, **k):
        # Identify which measure this query targets by substring.
        for m, v in value_by_measure.items():
            if f"[{m}]" in query:
                return {"success": True, "rows": [{"v": v}], "columns": ["v"]}
        return {"success": True, "rows": [{"v": None}], "columns": ["v"]}

    qe.validate_and_execute_dax.side_effect = fake_eval
    cs.query_executor = qe
    monkeypatch.setattr(tools, "connection_state", cs, raising=False)
    return cs


def test_capture_then_compare_no_change(tmp_path, monkeypatch):
    _patch_executor(monkeypatch, {"Sales": 100.0, "Cost": 40.0})
    bpath = tmp_path / "baseline.json"

    cap = tools.handle_validate(
        {
            "operation": "capture_baseline",
            "measures": ["Sales", "Cost"],
            "baseline_path": str(bpath),
        }
    )
    assert cap["success"] is True
    assert cap["captured"] == 2
    saved = json.loads(bpath.read_text(encoding="utf-8"))
    assert "Sales" in saved["results"]

    cmp = tools.handle_validate(
        {
            "operation": "compare_baseline",
            "baseline_path": str(bpath),
        }
    )
    assert cmp["success"] is True
    assert cmp["changed"] == 0
    assert cmp["unchanged"] == 2


def test_compare_detects_change_beyond_tolerance(tmp_path, monkeypatch):
    _patch_executor(monkeypatch, {"Sales": 100.0})
    bpath = tmp_path / "baseline.json"
    tools.handle_validate(
        {
            "operation": "capture_baseline",
            "measures": ["Sales"],
            "baseline_path": str(bpath),
        }
    )

    # Now the measure returns a different value.
    _patch_executor(monkeypatch, {"Sales": 250.0})
    cmp = tools.handle_validate(
        {
            "operation": "compare_baseline",
            "baseline_path": str(bpath),
            "tolerance": 0.001,
        }
    )
    assert cmp["success"] is True
    assert cmp["changed"] == 1
    assert cmp["differences"][0]["measure"] == "Sales"


def test_capture_requires_measures(monkeypatch):
    _patch_executor(monkeypatch, {})
    res = tools.handle_validate({"operation": "capture_baseline", "baseline_path": "x.json"})
    assert res["success"] is False
