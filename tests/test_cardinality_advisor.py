"""Tests for the prescriptive cardinality-reduction advisor (Phase 3.2).

prescriptive_advice() turns a high-cardinality ColumnStorage into a SPECIFIC,
data-type-aware action instead of the generic "consider removing/aggregating".
Pure helper -> unit-testable without a live model.
"""

from core.dax.vertipaq_storage_report import ColumnStorage, prescriptive_advice


def _col(column_name, data_type, cardinality=2_000_000):
    return ColumnStorage(
        table_name="Sales",
        column_name=column_name,
        data_type=data_type,
        cardinality=cardinality,
        dictionary_size=0,
        data_size=0,
        total_size=0,
        encoding="HASH",
        segments=1,
        partitions=1,
    )


def test_datetime_column_advises_split():
    advice = prescriptive_advice(_col("OrderDateTime", "DateTime"))
    assert "split" in advice.lower()
    assert "date" in advice.lower() and "time" in advice.lower()


def test_string_surrogate_key_advises_integer_key():
    for key_name in ["CustomerKey", "ProductID", "OrderCode", "AccountGUID"]:
        advice = prescriptive_advice(_col(key_name, "String"))
        assert "integer" in advice.lower() or "int64" in advice.lower(), advice


def test_high_precision_numeric_advises_reduce_precision():
    advice = prescriptive_advice(_col("UnitPrice", "Double"))
    assert "precision" in advice.lower() or "fixed decimal" in advice.lower()


def test_plain_string_advises_dimension_or_reduce():
    advice = prescriptive_advice(_col("Comment", "String"))
    assert "dimension" in advice.lower() or "distinct" in advice.lower()


def test_advice_is_nonempty_for_unknown_type():
    advice = prescriptive_advice(_col("Mystery", "Binary"))
    assert advice and isinstance(advice, str)
