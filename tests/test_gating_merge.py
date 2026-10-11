"""Tests for core.autonomous.gating.merge_gate_info — the safe merge that
must never overwrite handler-owned keys (success, error, error_type)."""

from __future__ import annotations

from core.autonomous.gating import merge_gate_info


def test_merge_preserves_handler_success_false():
    """The Phase-0 bug: gate notice must NOT flip success from False to True."""
    result = {"success": False, "error": "real handler failure"}
    gate = {"gate_notice": "destructive op ran without mode"}

    merged = merge_gate_info(result, gate)

    assert merged["success"] is False
    assert merged["error"] == "real handler failure"
    assert merged["gate_notice"] == "destructive op ran without mode"


def test_merge_does_not_overwrite_existing_gate_notice():
    """Nested calls preserve the OUTER gate notice."""
    result = {"success": True, "gate_notice": "outer notice"}
    gate = {"gate_notice": "inner notice"}

    merged = merge_gate_info(result, gate)

    assert merged["gate_notice"] == "outer notice"


def test_merge_returns_result_unchanged_when_gate_is_none():
    result = {"success": True, "data": [1, 2, 3]}
    assert merge_gate_info(result, None) is result


def test_merge_skips_block_response():
    """A block dict (success=False from gate) must NOT be merged — caller
    short-circuits before merge_gate_info is even called for blocks."""
    result = {"success": True, "rows": []}
    block = {
        "success": False,
        "error_type": "confirm_destructive_required",
        "error": "blocked",
    }

    merged = merge_gate_info(result, block)

    # Block dict not merged — handler success preserved.
    assert merged["success"] is True
    assert "error_type" not in merged


def test_merge_copies_warning_metadata_keys():
    result = {"success": True}
    gate = {
        "gate_warning": "be careful",
        "blocked_op": "02_Model_Operations:delete",
        "severity": "destructive",
        "policy": "WARN",
    }

    merged = merge_gate_info(result, gate)

    assert merged["gate_warning"] == "be careful"
    assert merged["blocked_op"] == "02_Model_Operations:delete"
    assert merged["severity"] == "destructive"
    assert merged["policy"] == "WARN"


def test_merge_handles_non_dict_result():
    """Robustness: non-dict result returns unchanged."""
    assert merge_gate_info("scalar", {"gate_notice": "x"}) == "scalar"
    assert merge_gate_info(None, {"gate_notice": "x"}) is None
