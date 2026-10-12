"""A-05 / A-14 / A-17 / L-06 / A-28 / K-07: the response pipeline.

* Large results are SPILLED to a file with a correct pointer and all scalar
  metadata preserved — never silently replaced by a 3-row sample.
* The 450K inline path is reachable (a 300K result comes back verbatim).
* User payload keys are never compacted (visualType/position/success inside
  rows or raw PBIR stay as-is; the envelope stays success/error/error_type).
* NaN/Infinity become null (strict JSON).
* One serialization on the inline path; the spill dir has bounded retention.
"""

from __future__ import annotations

import json
import os
import time


import server.response_pipeline as rp


def test_inline_result_under_cap_is_verbatim_and_uncompacted():
    raw_visual = {
        "$schema": "https://example/visualContainer.json",
        "name": "abc123",
        "position": {"x": 10, "y": 20},
        "visual": {"visualType": "cardVisual", "objects": {}},
    }
    result = {
        "success": True,
        "value": raw_visual,
        "rows": [{"success": "Yes", "error": 0.02, "description": "Net sales", "position": 3}],
        "execution_time_ms": 12,
    }
    out = rp.render_result("07_Visual_Operations", result)
    assert out.is_error is False and out.spilled_path is None
    assert json.loads(out.text) == result  # byte-for-byte semantic equality
    assert '"vtype"' not in out.text and '"ok"' not in out.text


def test_300k_result_stays_inline():
    """The old pipeline summarized anything over ~200K chars to 3 rows."""
    rows = [{"i": i, "pad": "x" * 100} for i in range(2500)]
    result = {"success": True, "rows": rows, "row_count": 2500}
    out = rp.render_result("04_Run_DAX", result)
    assert out.spilled_path is None
    assert 250_000 < len(out.text) <= rp.INLINE_MAX_CHARS
    assert len(json.loads(out.text)["rows"]) == 2500


def test_oversized_result_spills_with_pointer_and_preserved_metadata(tmp_path):
    result = {
        "success": True,
        "result_sets": [{"rows": [{"v": "y" * 200, "i": i} for i in range(3000)]}],
        "execution_metrics": {"peak_memory_kb": 1234, "fe_cpu_ms": 5},
        "total_ms": 42.5,
        "runner_lane": "amo",
        "warnings": ["w1"],
    }
    out = rp.render_result("04_Run_DAX", result, inline_max_chars=50_000, spill_directory=tmp_path)
    assert out.spilled_path and os.path.exists(out.spilled_path)
    summary = json.loads(out.text)
    # pointer is correct and the file holds the COMPLETE result
    with open(summary["_full_output"], encoding="utf-8") as f:
        assert json.load(f) == result
    assert summary["_spilled"] is True
    assert summary["_full_output_chars"] > 50_000
    # every scalar survives; containers are sized + previewed
    assert summary["success"] is True
    assert summary["total_ms"] == 42.5
    assert summary["runner_lane"] == "amo"
    assert summary["_sizes"]["result_sets"] == {"type": "list", "count": 1}
    assert summary["_preview"]["warnings"] == ["w1"]
    assert summary["_preview"]["execution_metrics"] == {"peak_memory_kb": 1234, "fe_cpu_ms": 5}
    assert len(out.text) <= rp.SPILL_SUMMARY_MAX_CHARS + 2000
    assert "fields" not in summary["_note"]  # no advertised non-existent `fields` filter


def test_spilled_error_stays_an_error(tmp_path):
    result = {"success": False, "error_type": "query_execution_error", "error": "x" * 60_000}
    out = rp.render_result("04_Run_DAX", result, inline_max_chars=10_000, spill_directory=tmp_path)
    summary = json.loads(out.text)
    assert out.is_error is True
    assert summary["success"] is False
    assert summary["error_type"] == "query_execution_error"


def test_non_finite_floats_become_null():
    out = rp.render_result(
        "04_Run_DAX", {"success": True, "rows": [{"a": float("nan")}], "b": float("inf")}
    )
    assert json.loads(out.text) == {"success": True, "rows": [{"a": None}], "b": None}
    assert "NaN" not in out.text and "Infinity" not in out.text


def test_inline_path_serializes_once(monkeypatch):
    """One encode per result: orjson (P-12) or, as the fallback, one json.dumps."""
    calls = {"n": 0}
    real = rp.json.dumps
    real_fast = rp._serialize_orjson

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    def counting_fast(*a, **k):
        calls["n"] += 1
        return real_fast(*a, **k)

    monkeypatch.setattr(rp.json, "dumps", counting)
    monkeypatch.setattr(rp, "_serialize_orjson", counting_fast)
    rp.render_result("04_Run_DAX", {"success": True, "rows": [{"i": i} for i in range(5000)]})
    assert calls["n"] == 1


def test_spill_dir_retention(tmp_path):
    now = time.time()
    for i in range(8):
        p = tmp_path / f"t_{i}.json"
        p.write_text("{}", encoding="utf-8")
        os.utime(p, (now - i * 60, now - i * 60))
    old = tmp_path / "ancient.json"
    old.write_text("{}", encoding="utf-8")
    os.utime(old, (now - 30 * 86400, now - 30 * 86400))
    removed = rp.prune_spill_dir(tmp_path, max_files=5, max_age_days=7, now=now)
    remaining = sorted(p.name for p in tmp_path.iterdir())
    assert "ancient.json" not in remaining
    assert len(remaining) == 5
    assert remaining == sorted(f"t_{i}.json" for i in range(5))
    assert len(removed) == 4


def test_high_token_tools_spill_at_the_token_budget(monkeypatch, tmp_path):
    """HIGH_TOKEN_TOOLS used to be HARD-BLOCKED over max_result_tokens; they now
    spill at that budget instead (the call succeeds, the data is on disk)."""
    import server.dispatch as dispatch_mod
    from server.dispatch import ToolDispatcher

    monkeypatch.setattr(rp, "spill_dir", lambda: tmp_path)
    # F-34: the soft inline budget (first page of the main list) sits BELOW the token budget; switch
    # it off here to exercise the hard spill this test is about.
    monkeypatch.setattr(dispatch_mod, "soft_inline_budget", lambda: 0)
    d = ToolDispatcher()
    cap = d._inline_cap("06_Analysis_Operations")
    assert cap < rp.INLINE_MAX_CHARS
    assert d._inline_cap("04_Run_DAX") == rp.INLINE_MAX_CHARS
    big = {"success": True, "violations": [{"rule": "x" * 50} for _ in range(cap // 40)]}
    rendered = d._render("06_Analysis_Operations", big)
    assert rendered.spilled_path is not None
    assert rendered.is_error is False


def test_compact_response_no_longer_renames_keys():
    """L-06: the debug handler's compact_response used to rewrite
    expression→expr, success→ok etc. inside its payload."""
    from server.middleware import compact_response

    out = compact_response(
        {
            "success": True,
            "expression": "SUM(x)",
            "empty": [],
            "none": None,
            "rows": [{"description": "d"}],
        }
    )
    assert out == {"success": True, "expression": "SUM(x)", "rows": [{"description": "d"}]}
