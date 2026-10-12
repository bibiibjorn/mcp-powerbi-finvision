"""A measure whose DMV Expression is NULL must not crash the debug/trace path.

`row.get("Expression", "")` returns the STORED value when the key exists, so a real
NULL from the MEASURES DMV (calc-group placeholders, some FormatString-only measures,
or any NULL column) flows through as None and blows up at
`measure_def.expression.strip()` with:

    'NoneType' object has no attribute 'strip'

surfacing to the caller as an opaque `unexpected_error` in debug_visual.
"""

from unittest.mock import MagicMock

from core.debug.visual_query_builder import MeasureDefinition, VisualQueryBuilder


def _builder_with_measures(tmp_path, rows):
    report = tmp_path / "R.Report"
    (report / "definition").mkdir(parents=True)
    b = VisualQueryBuilder(str(report))

    qe = MagicMock()

    def _info(view):
        if view == "MEASURES":
            return {"success": True, "rows": rows}
        if view == "TABLES":
            return {"success": True, "rows": [{"ID": "1", "Name": "m_Measures"}]}
        return {"success": False, "rows": []}

    qe.execute_info_query.side_effect = _info
    b._query_executor = qe
    return b


def test_null_expression_is_coerced_to_empty_string(tmp_path):
    """A NULL Expression must become '' so downstream .strip() is safe."""
    rows = [
        {"Name": "Good", "Expression": "SUM(x)", "TableID": "1", "FormatString": "0"},
        # NULL expression AND NULL format string - the real DMV shape that crashed
        {"Name": "NullExpr", "Expression": None, "TableID": "1", "FormatString": None},
    ]
    builder = _builder_with_measures(tmp_path, rows)

    assert builder._load_all_measures_from_dmv() is True

    null_measure = builder._measure_cache["NullExpr"]
    assert null_measure.expression == "", "NULL expression must be coerced to empty string"
    assert null_measure.expression.strip() == ""  # must not raise
    assert builder._measure_cache["Good"].expression == "SUM(x)"


def test_expanded_query_survives_null_expression_measure(tmp_path):
    """_build_expanded_dax_query must not raise on a None expression."""
    builder = _builder_with_measures(tmp_path, [])

    defs = [
        MeasureDefinition(name="Good", expression="SUM(x)", table="m_Measures"),
        MeasureDefinition(name="Broken", expression=None, table="m_Measures"),
    ]

    # Must not raise AttributeError: 'NoneType' object has no attribute 'strip'
    query = builder._build_expanded_dax_query(defs, [], [])
    assert isinstance(query, str)
