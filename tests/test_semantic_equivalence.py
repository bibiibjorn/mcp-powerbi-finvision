"""Pure-logic coverage for core/dax/semantic_equivalence.py.

The semantic-equivalence guard is what gates every "this optimization is
faster" claim, so its comparison logic is the part that must be airtight:
order-insensitivity, relative tolerance, BLANK-vs-0, structural mismatches,
the scale guard, and never raising on garbage input.
"""

from core.dax.semantic_equivalence import (
    MAX_DIFFERENCES,
    ROWWISE_ROW_LIMIT,
    compare_rows,
    normalize_value,
    verdict,
)

# ---------------------------------------------------------------------------
# normalize_value
# ---------------------------------------------------------------------------


def test_normalize_blank_stays_none_and_empty_string_stays_text():
    # Review D-06: "" is distinct from BLANK in DAX (ISBLANK("") = FALSE).
    assert normalize_value(None) is None
    assert normalize_value("") == ""
    assert normalize_value("   ") == "   "


def test_normalize_numeric_string_stays_text():
    # Review D-06: a FORMAT-style rewrite returns text; "100" is not 100.
    assert normalize_value("100") == "100"
    assert normalize_value(100) == 100.0


def test_normalize_bool_stays_bool_distinct_from_number():
    assert normalize_value(True) is True
    assert normalize_value(False) is False


def test_normalize_datetime_becomes_iso_string():
    from datetime import datetime

    assert normalize_value(datetime(2026, 1, 2, 3, 4, 5)) == "2026-01-02T03:04:05"


# ---------------------------------------------------------------------------
# Order insensitivity
# ---------------------------------------------------------------------------


def test_identical_rows_in_different_order_are_equivalent():
    a = [
        {"Country": "BE", "value": 10.0},
        {"Country": "NL", "value": 20.0},
        {"Country": "FR", "value": 30.0},
    ]
    b = [
        {"Country": "FR", "value": 30.0},
        {"Country": "BE", "value": 10.0},
        {"Country": "NL", "value": 20.0},
    ]
    res = compare_rows(a, b)
    assert res["equivalent"] is True
    assert res["reason"] == "equivalent"
    assert res["compared_rows"] == 3
    assert res["method"] == "rowwise"
    assert res["differences"] == []


def test_column_alias_map_aligns_differently_named_value_columns():
    a = [{"Original": 42.0}]
    b = [{"Optimized": 42.0}]
    res = compare_rows(a, b, column_alias_map={"Original": "value", "Optimized": "value"})
    assert res["equivalent"] is True


def test_bracketed_column_names_are_stripped_for_alignment():
    a = [{"[value]": 5.0}]
    b = [{"value": 5.0}]
    assert compare_rows(a, b)["equivalent"] is True


# ---------------------------------------------------------------------------
# Structural mismatches
# ---------------------------------------------------------------------------


def test_row_count_mismatch_is_not_equivalent_with_reason():
    res = compare_rows([{"value": 1.0}], [{"value": 1.0}, {"value": 2.0}])
    assert res["equivalent"] is False
    assert res["reason"] == "row_count_mismatch"
    assert res["row_count_a"] == 1
    assert res["row_count_b"] == 2


def test_column_count_mismatch_reason():
    a = [{"Country": "BE", "value": 1.0}]
    b = [{"value": 1.0}]
    res = compare_rows(a, b)
    assert res["equivalent"] is False
    assert res["reason"] == "column_count_mismatch"


def test_both_empty_is_not_proof_of_equivalence():
    """Two empty result sets are trivially equal but prove nothing.

    Regression (caught live on R0201, 2026-08-22): a model with no fact data
    makes every SUMMARIZECOLUMNS return zero rows, so a `* 1.5` rewrite was
    being certified as equivalent. Vacuous truth must fail closed.
    """
    res = compare_rows([], [])
    assert res["equivalent"] is False
    assert res["reason"] == "no_rows_compared"
    assert res["compared_rows"] == 0
    assert "zero rows" in res["note"]


# ---------------------------------------------------------------------------
# Numeric tolerance
# ---------------------------------------------------------------------------


def test_value_beyond_tolerance_reports_row_and_column():
    a = [{"Country": "BE", "value": 10.0}]
    b = [{"Country": "BE", "value": 11.0}]
    res = compare_rows(a, b, tolerance=0.001)
    assert res["equivalent"] is False
    assert res["reason"] == "value_mismatch"
    diff = res["differences"][0]
    assert diff["row_index"] == 0
    assert diff["column"] == "value"
    assert diff["a"] == 10.0
    assert diff["b"] == 11.0
    assert diff["reason"] == "numeric_difference"


def test_relative_tolerance_accepts_tiny_delta_on_large_number():
    a = [{"value": 1_000_000.0}]
    b = [{"value": 1_000_000.0005}]
    res = compare_rows(a, b, tolerance=0.001)
    assert res["equivalent"] is True


def test_absolute_tolerance_still_applies_to_small_numbers():
    # 0.0 vs 0.0005 is inside the absolute floor of 0.001.
    assert compare_rows([{"v": 0.0}], [{"v": 0.0005}], tolerance=0.001)["equivalent"]
    # 0.0 vs 0.5 is not.
    assert not compare_rows([{"v": 0.0}], [{"v": 0.5}], tolerance=0.001)["equivalent"]


def test_tolerance_used_is_reported_and_bad_tolerance_falls_back():
    assert compare_rows([{"v": 1.0}], [{"v": 1.0}], tolerance=0.5)["tolerance_used"] == 0.5
    # Invalid tolerance falls back to the tight default absolute floor (1e-6).
    assert compare_rows([{"v": 1.0}], [{"v": 1.0}], tolerance="bad")["tolerance_used"] == 1e-6


# ---------------------------------------------------------------------------
# DAX semantics: BLANK vs 0, bool vs number
# ---------------------------------------------------------------------------


def test_blank_vs_zero_is_a_difference_with_its_own_reason():
    res = compare_rows([{"value": None}], [{"value": 0}])
    assert res["equivalent"] is False
    assert res["reason"] == "blank_vs_zero"
    assert res["differences"][0]["reason"] == "blank_vs_zero"


def test_blank_vs_nonzero_is_blank_mismatch():
    res = compare_rows([{"value": None}], [{"value": 7.0}])
    assert res["equivalent"] is False
    assert res["differences"][0]["reason"] == "blank_mismatch"


def test_blank_on_both_sides_is_not_proof_of_equivalence():
    # Review D-06: ROW("value", [M]) on a model where [M] is BLANK returns one
    # all-BLANK row on both sides — vacuous, must fail closed.
    res = compare_rows([{"value": None}], [{"value": None}])
    assert res["equivalent"] is False
    assert res["reason"] == "vacuous_all_blank"


def test_blank_cells_are_fine_when_other_values_are_compared():
    a = [{"k": "BE", "value": None}, {"k": "NL", "value": 2.0}]
    b = [{"k": "NL", "value": 2.0}, {"k": "BE", "value": None}]
    assert compare_rows(a, b)["equivalent"] is True


def test_bool_is_distinct_from_zero_one():
    res = compare_rows([{"v": True}], [{"v": 1}])
    assert res["equivalent"] is False
    assert res["differences"][0]["reason"] == "type_mismatch"


def test_string_vs_number_is_type_mismatch():
    res = compare_rows([{"v": "BE"}], [{"v": 3.0}])
    assert res["equivalent"] is False
    assert res["differences"][0]["reason"] == "type_mismatch"


def test_equal_strings_are_equivalent():
    assert compare_rows([{"v": "BE"}], [{"v": "BE"}])["equivalent"] is True


# ---------------------------------------------------------------------------
# Difference cap
# ---------------------------------------------------------------------------


def test_more_than_20_differences_are_capped_and_flagged():
    a = [{"k": i, "value": float(i)} for i in range(40)]
    b = [{"k": i, "value": float(i) + 100.0} for i in range(40)]
    res = compare_rows(a, b)
    assert res["equivalent"] is False
    assert len(res["differences"]) == MAX_DIFFERENCES
    assert res["differences_truncated"] is True


# ---------------------------------------------------------------------------
# Scale guard
# ---------------------------------------------------------------------------


def test_over_50k_rows_uses_hash_method_and_detects_equality():
    n = ROWWISE_ROW_LIMIT + 1
    a = [{"k": i} for i in range(n)]
    b = list(reversed([{"k": i} for i in range(n)]))
    res = compare_rows(a, b)
    assert res["method"] == "hash"
    assert res["equivalent"] is True
    assert res["hash_a"] == res["hash_b"]


def test_over_50k_rows_hash_detects_difference():
    n = ROWWISE_ROW_LIMIT + 1
    a = [{"k": i} for i in range(n)]
    b = [{"k": i} for i in range(n)]
    b[0] = {"k": -999}
    res = compare_rows(a, b)
    assert res["method"] == "hash"
    assert res["equivalent"] is False
    assert res["reason"] == "hash_mismatch"


# ---------------------------------------------------------------------------
# Malformed input — must never raise
# ---------------------------------------------------------------------------


def test_none_input_returns_not_equivalent_verdict():
    res = compare_rows(None, None)
    assert res["equivalent"] is False
    assert res["reason"] == "invalid_input"


def test_non_list_input_returns_not_equivalent_verdict():
    res = compare_rows({"value": 1}, [{"value": 1}])
    assert res["equivalent"] is False
    assert res["reason"] == "invalid_input"


def test_list_of_non_dicts_returns_not_equivalent_verdict():
    res = compare_rows([1, 2, 3], [1, 2, 3])
    assert res["equivalent"] is False
    assert res["reason"] == "invalid_input"


def test_mixed_type_values_do_not_raise_during_sorting():
    a = [{"v": None}, {"v": 1.0}, {"v": "x"}, {"v": True}]
    b = [{"v": True}, {"v": "x"}, {"v": 1.0}, {"v": None}]
    res = compare_rows(a, b)
    assert res["equivalent"] is True


# ---------------------------------------------------------------------------
# verdict() helper
# ---------------------------------------------------------------------------


def test_verdict_helper_shape_and_truncation():
    v = verdict(equivalent=False, reason="query_failed")
    assert v["equivalent"] is False
    assert v["reason"] == "query_failed"
    assert v["differences"] == []
    assert v["differences_truncated"] is False
    assert v["method"] == "rowwise"

    many = [{"row_index": i, "column": "v", "a": 1, "b": 2, "reason": "x"} for i in range(30)]
    v2 = verdict(equivalent=False, reason="value_mismatch", differences=many)
    assert len(v2["differences"]) == MAX_DIFFERENCES
    assert v2["differences_truncated"] is True


def test_key_columns_hint_does_not_break_equivalence():
    a = [{"Country": "BE", "value": 1.0}, {"Country": "NL", "value": 2.0}]
    b = [{"Country": "NL", "value": 2.0}, {"Country": "BE", "value": 1.0}]
    res = compare_rows(a, b, key_columns=["Country"])
    assert res["equivalent"] is True
