"""T1-F: tests for the uniform error envelope enrichment.

enrich_error_response post-processes failed handler results to attach a
retry_with / suggestion hint when the error_type maps to a known recovery
path. Handler-supplied values always win.
"""

from __future__ import annotations

from core.validation.error_enrichment import enrich_error_response


def test_not_connected_gets_ensure_retry() -> None:
    res = enrich_error_response({"success": False, "error_type": "not_connected"})
    assert res["retry_with"]["tool"] == "01_Connection"
    assert res["retry_with"]["args"]["operation"] == "ensure"
    assert "ensure" in res["suggestion"]


def test_duplicate_name_suggests_upsert() -> None:
    res = enrich_error_response({"success": False, "error_type": "duplicate_name"})
    assert "upsert" in res["suggestion"]
    # No retry_with — caller must supply the args.
    assert "retry_with" not in res


def test_gate_blocked_points_at_confirm_destructive() -> None:
    res = enrich_error_response({"success": False, "error_type": "gate_blocked"})
    assert "confirm_destructive" in res["suggestion"]


def test_confirm_destructive_required_recovery() -> None:
    res = enrich_error_response({"success": False, "error_type": "confirm_destructive_required"})
    assert "confirm_destructive" in res["suggestion"]


def test_unknown_error_type_passes_through_untouched() -> None:
    res = enrich_error_response({"success": False, "error_type": "weird_thing", "error": "x"})
    assert "retry_with" not in res
    assert "suggestion" not in res


def test_success_passes_through_untouched() -> None:
    res = enrich_error_response({"success": True, "rows": []})
    assert "suggestion" not in res
    assert "retry_with" not in res


def test_handler_supplied_suggestion_wins() -> None:
    res = enrich_error_response(
        {
            "success": False,
            "error_type": "not_connected",
            "suggestion": "do something specific",
        }
    )
    assert res["suggestion"] == "do something specific"


def test_handler_supplied_retry_wins() -> None:
    res = enrich_error_response(
        {
            "success": False,
            "error_type": "not_connected",
            "retry_with": {"tool": "X", "args": {"y": 1}},
        }
    )
    assert res["retry_with"] == {"tool": "X", "args": {"y": 1}}


def test_non_dict_passes_through() -> None:
    assert enrich_error_response("oops") == "oops"
    assert enrich_error_response(None) is None
    assert enrich_error_response(42) == 42


def test_reload_pending_recovery() -> None:
    res = enrich_error_response({"success": False, "error_type": "reload_pending"})
    assert res["retry_with"]["tool"] == "17_Desktop_Bridge"
    assert res["retry_with"]["args"]["operation"] == "reload"
