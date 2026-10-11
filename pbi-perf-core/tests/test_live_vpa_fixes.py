"""Found in the live R0201 check (2026-09-23)."""

from pbi_perf_core.vpa import ColumnMetrics, ModelMetrics, TableMetrics, analyse_model


def _codes(model):
    return [(f["code"], f["object"]) for f in analyse_model(model)["findings"]]


def test_unknown_cardinality_is_none_not_zero():
    col = ColumnMetrics("T", "c", data_type="DBTYPE_R8")
    assert col.cardinality is None
    assert col.to_dict()["cardinality"] is None


def test_hash_encoded_numeric_fires_on_dmv_dbtype_codes():
    col = ColumnMetrics(
        "F",
        "Amount",
        data_type="DBTYPE_R8",
        encoding="HASH",
        data_size=3_000_000,
        dictionary_size=21_000_000,
    )
    model = ModelMetrics(tables=[TableMetrics("F", rows_count=1_000_000, columns=[col])])
    codes = _codes(model)
    assert ("HASH_ENCODED_NUMERIC", "F[Amount]") in codes
    # numeric columns are not reported as "high cardinality text"
    assert ("DICTIONARY_HEAVY", "F[Amount]") not in codes


def test_dictionary_heavy_ignores_the_1mb_text_baseline():
    tiny = ColumnMetrics(
        "S",
        "Scenario",
        data_type="DBTYPE_WSTR",
        encoding="HASH",
        data_size=200,
        dictionary_size=1_065_784,
    )
    big = ColumnMetrics(
        "F",
        "Doc",
        data_type="DBTYPE_WSTR",
        encoding="HASH",
        data_size=500_000,
        dictionary_size=8_500_000,
    )
    model = ModelMetrics(
        tables=[
            TableMetrics("S", rows_count=17, columns=[tiny]),
            TableMetrics("F", rows_count=300_000, columns=[big]),
        ]
    )
    codes = _codes(model)
    assert ("DICTIONARY_HEAVY", "S[Scenario]") not in codes
    assert ("DICTIONARY_HEAVY", "F[Doc]") in codes
