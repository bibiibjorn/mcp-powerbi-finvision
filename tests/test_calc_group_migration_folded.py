"""Tests that calc-group migration ops are reachable via 02_Model_Operations.

Confirms the fold from former 14_Calc_Group_Migrator into the
calculation_group object_type. Read-only ops, no live model needed for
list_patterns/scaffold; detect path is mocked.
"""

from server.handlers.model_operations_handler import handle_model_operations


def test_list_patterns_via_calculation_group_dispatch():
    res = handle_model_operations(
        {"object_type": "calculation_group", "operation": "list_patterns"}
    )
    assert res["success"] is True
    assert any(p["name"] == "time_intel" for p in res["patterns"])


def test_scaffold_via_calculation_group_requires_candidate():
    res = handle_model_operations({"object_type": "calculation_group", "operation": "scaffold"})
    assert res["success"] is False
    assert res["error_type"] == "invalid_args"


def test_scaffold_via_calculation_group_emits_tmdl():
    res = handle_model_operations(
        {
            "object_type": "calculation_group",
            "operation": "scaffold",
            "candidate": {
                "pattern_name": "time_intel",
                "variant_suffix_set": ["YTD", "MTD"],
                "base_measures": ["Sales"],
            },
            "calc_group_table_name": "TI",
            "precedence": 5,
        }
    )
    assert res["success"] is True
    assert "calculationItem YTD" in res["tmdl"]


def test_detect_via_calculation_group_errors_when_disconnected(monkeypatch):
    from core.infrastructure.connection_state import connection_state

    monkeypatch.setattr(connection_state, "is_connected", lambda: False)
    res = handle_model_operations({"object_type": "calculation_group", "operation": "detect"})
    assert res["success"] is False
    assert res["error_type"] == "not_connected"


def test_unknown_calc_group_op_still_rejected():
    res = handle_model_operations({"object_type": "calculation_group", "operation": "bogus_op"})
    assert res["success"] is False
    assert "Invalid operation" in res["error"]
