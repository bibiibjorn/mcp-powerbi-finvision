"""2026-09-22 deep review, slice D — context-transition detection (D-18).

Unqualified column references, format strings and plain column arithmetic in
an iterator are not context transitions.
"""

from core.dax.analyzer.models import ModelSchema
from core.dax.context_analyzer import DaxContextAnalyzer, TransitionType
from core.dax.dax_reference_parser import DaxReferenceIndex


def transitions(dax, index=None):
    return DaxContextAnalyzer().analyze_context_transitions(dax, reference_index=index).transitions


def test_row_level_column_arithmetic_is_not_a_transition():
    assert transitions("SUMX(Sales, Sales[Qty] * Sales[Price])") == []


def test_format_string_is_not_a_measure():
    got = transitions('FORMAT([Sales], "0;[Red]-0")')
    assert [t.measure_name for t in got if t.type == TransitionType.IMPLICIT_MEASURE] == ["Sales"]


def test_iterator_over_measure_is_medium_with_table():
    got = transitions("SUMX(Customer, [Sales])")
    it = [t for t in got if t.type == TransitionType.ITERATOR]
    assert len(it) == 1
    assert it[0].performance_impact.value == "medium"
    assert it[0].table_name == "Customer"


def test_dotted_iterator_detected():
    got = transitions("STDEVX.S(T, [M])")
    assert any(t.type == TransitionType.ITERATOR and t.function == "STDEVX.S" for t in got)


def test_schema_known_column_is_not_a_measure():
    schema = ModelSchema(column_names={"qty"}, measure_names={"sales"})
    got = transitions("SUMX(Sales, [Qty] * 2) + [Sales]", schema)
    names = [t.measure_name for t in got if t.type == TransitionType.IMPLICIT_MEASURE]
    assert names == ["Sales"]
    assert not any(t.type == TransitionType.ITERATOR for t in got)


def test_reference_index_classification():
    idx = DaxReferenceIndex(column_rows=[{"Table": "Sales", "Name": "Qty"}])
    got = transitions("SUMX(Sales, [Qty])", idx)
    assert got == []


def test_extension_column_is_not_a_measure():
    got = transitions('SUMX(ADDCOLUMNS(VALUES(T[k]), "Tmp", [M]), [Tmp])')
    names = [t.measure_name for t in got if t.type == TransitionType.IMPLICIT_MEASURE]
    assert names == ["M"]


def test_calculate_filter_arguments_and_apostrophes():
    got = transitions('CALCULATE([Men\'s Sales], T[a] = 1, "x,y" = T[b]) + [Other]')
    calc = [t for t in got if t.type == TransitionType.EXPLICIT_CALCULATE][0]
    assert calc.filter_context_after == ["T[a] = 1", '"x,y" = T[b]']
    names = [t.measure_name for t in got if t.type == TransitionType.IMPLICIT_MEASURE]
    assert names == ["Men's Sales", "Other"]


def test_positions_are_line_and_column_of_the_token():
    got = transitions("VAR x = 1\nRETURN\n  CALCULATE([M])")
    calc = [t for t in got if t.type == TransitionType.EXPLICIT_CALCULATE][0]
    assert (calc.line, calc.column) == (3, 3)


def test_comment_text_is_not_analysed():
    assert transitions("[A] // CALCULATE([B])") and all(
        t.measure_name != "B" for t in transitions("[A] // CALCULATE([B])")
    )
