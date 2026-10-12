"""2026-09-22 deep review, slice D — the semantic-equivalence guard (D-06, D-24).

It certifies optimizer rewrites before they are written to a live model, so
vacuous or loosely-matched results must fail closed.
"""

import math

from core.dax.semantic_equivalence import (
    DEFAULT_ABS_TOLERANCE,
    DEFAULT_REL_TOLERANCE,
    compare_rows,
)


def test_all_blank_scalar_row_is_vacuous():
    res = compare_rows([{"value": None}], [{"value": None}], column_alias_map={"value": "value"})
    assert res["equivalent"] is False and res["reason"] == "vacuous_all_blank"


def test_all_blank_grain_values_are_vacuous_even_with_keys():
    a = [{"Country": "BE", "value": None}, {"Country": "NL", "value": None}]
    b = [{"Country": "NL", "value": None}, {"Country": "BE", "value": None}]
    res = compare_rows(a, b, column_alias_map={"value": "value"})
    assert res["reason"] == "vacuous_all_blank"
    res = compare_rows(a, b, key_columns=["Country"])
    assert res["reason"] == "vacuous_all_blank"


def test_empty_string_is_not_blank():
    res = compare_rows([{"v": ""}], [{"v": None}])
    assert res["equivalent"] is False
    assert res["differences"][0]["reason"] == "blank_vs_empty_string"


def test_numeric_text_is_not_a_number():
    res = compare_rows([{"v": 100}], [{"v": "100"}])
    assert res["equivalent"] is False
    assert res["differences"][0]["reason"] == "type_mismatch"


def test_default_tolerance_is_tight():
    assert DEFAULT_REL_TOLERANCE == 1e-9 and DEFAULT_ABS_TOLERANCE == 1e-6
    assert compare_rows([{"v": 1_000_000.0}], [{"v": 1_000_900.0}])["equivalent"] is False
    assert compare_rows([{"v": 1_000_000.0}], [{"v": 1_000_000.0000001}])["equivalent"] is True


def test_explicit_tolerances():
    assert compare_rows([{"v": 1_000_000.0}], [{"v": 1_000_900.0}], tolerance=0.001)["equivalent"]
    res = compare_rows([{"v": 100.0}], [{"v": 100.5}], rel_tolerance=0.01, abs_tolerance=0)
    assert res["equivalent"] is True and res["rel_tolerance_used"] == 0.01


def test_equal_infinities_and_nans():
    assert compare_rows([{"v": math.inf}], [{"v": math.inf}])["equivalent"] is True
    assert compare_rows([{"v": math.inf}], [{"v": -math.inf}])["equivalent"] is False
    assert compare_rows([{"v": math.nan}], [{"v": math.nan}])["equivalent"] is True
    assert compare_rows([{"v": math.nan}], [{"v": 1.0}])["equivalent"] is False


def test_row_count_mismatch_is_not_equivalent():
    assert compare_rows([{"v": 1.0}], [{"v": 1.0}, {"v": 1.0}])["reason"] == "row_count_mismatch"


def test_shared_columns_align_by_name_not_sorted_position():
    """D-24: 'Ordering' used to interleave between 'Optimized' and 'Original'."""
    a = [{"Ordering": 1, "Original": 10.0}, {"Ordering": 2, "Original": 20.0}]
    b = [{"Ordering": 2, "Optimized": 20.0}, {"Ordering": 1, "Optimized": 10.0}]
    res = compare_rows(a, b)
    assert res["equivalent"] is True, res


def test_real_difference_still_detected_after_alignment():
    a = [{"Ordering": 1, "Original": 10.0}]
    b = [{"Ordering": 1, "Optimized": 11.0}]
    res = compare_rows(a, b)
    assert res["equivalent"] is False and res["reason"] == "value_mismatch"
