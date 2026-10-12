"""P1b: recovery-map coverage, error-type alias normalization, and
did-you-mean on not-found errors.

The enrichment layer already existed but covered only 10 error_types and did
not reconcile the 3-way spelling drift (stale_plan / plan_stale /
stale_plan_hash; confirmation_required / confirm_destructive_required). It also
never surfaced the candidate lists that not-found errors already carry in the
envelope. These tests pin the expanded contract.
"""

from core.validation.error_enrichment import enrich_error_response


# ---------------------------------------------------------------------------
# New high-frequency recovery buckets
# ---------------------------------------------------------------------------
def test_query_execution_error_has_recovery():
    out = enrich_error_response(
        {"success": False, "error_type": "query_execution_error", "error": "x"}
    )
    assert out.get("suggestion")


def test_page_not_found_has_recovery():
    out = enrich_error_response({"success": False, "error_type": "page_not_found", "error": "x"})
    assert out.get("suggestion")
    assert out.get("retry_with", {}).get("tool") == "17_Desktop_Bridge"


def test_ambiguous_instance_has_recovery():
    out = enrich_error_response(
        {"success": False, "error_type": "ambiguous_instance", "error": "x"}
    )
    assert out.get("retry_with", {}).get("tool") == "01_Connection"


def test_cli_not_installed_promotes_install_command():
    out = enrich_error_response({"success": False, "error_type": "cli_not_installed", "error": "x"})
    assert out.get("suggestion")
    assert out.get("requires_human") is True


def test_unknown_tool_points_at_user_guide():
    out = enrich_error_response({"success": False, "error_type": "unknown_tool", "error": "x"})
    assert out.get("retry_with", {}).get("tool") == "10_Show_User_Guide"


# ---------------------------------------------------------------------------
# Alias normalization — the drift spellings must resolve to the same recovery
# ---------------------------------------------------------------------------
def test_plan_stale_alias_normalizes():
    for spelling in ("plan_stale", "stale_plan", "stale_plan_hash"):
        out = enrich_error_response({"success": False, "error_type": spelling, "error": "x"})
        assert out.get("suggestion"), f"no recovery for {spelling}"


def test_confirmation_required_alias_normalizes():
    for spelling in ("confirmation_required", "confirm_destructive_required", "gate_blocked"):
        out = enrich_error_response({"success": False, "error_type": spelling, "error": "x"})
        assert out.get("suggestion"), f"no recovery for {spelling}"


# ---------------------------------------------------------------------------
# did-you-mean on not-found errors — use the candidate list already in scope
# ---------------------------------------------------------------------------
def test_measure_not_found_adds_did_you_mean():
    out = enrich_error_response(
        {
            "success": False,
            "error_type": "measure_not_found",
            "error": "Measure 'Total Reveue' not found",
            "measure": "Total Reveue",
            "available_measures": ["Total Revenue", "Total Cost", "Margin %"],
        }
    )
    assert "Total Revenue" in (out.get("did_you_mean") or [])


def test_table_not_found_adds_did_you_mean_from_available_tables():
    out = enrich_error_response(
        {
            "success": False,
            "error_type": "table_not_found",
            "error": "Table 'Sale' not found",
            "table": "Sale",
            "available_tables": ["Sales", "Products", "Date"],
        }
    )
    assert "Sales" in (out.get("did_you_mean") or [])


def test_did_you_mean_absent_when_no_candidate_list():
    out = enrich_error_response(
        {
            "success": False,
            "error_type": "measure_not_found",
            "error": "Measure 'X' not found",
            "measure": "X",
        }
    )
    assert "did_you_mean" not in out


def test_handler_supplied_suggestion_wins():
    out = enrich_error_response(
        {
            "success": False,
            "error_type": "not_connected",
            "error": "x",
            "suggestion": "custom handler suggestion",
        }
    )
    assert out["suggestion"] == "custom handler suggestion"
