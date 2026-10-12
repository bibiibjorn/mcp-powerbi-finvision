"""Schema-aware bracket-ref classification (Phase 2.2).

A bare [X] token is ambiguous between a measure and a column. With a ModelSchema
present, classify_ref resolves it deterministically; without schema knowledge it
returns 'ambiguous' so callers keep the existing conservative behaviour.

The bracket name may arrive with or without the surrounding [] — classify_ref
normalizes both.
"""

from core.dax.analyzer.models import ModelSchema


def _schema():
    return ModelSchema.from_model(
        {
            "measures": [{"name": "Total Sales"}],
            "columns": [{"name": "Amount"}],
        }
    )


def test_classifies_known_measure():
    assert _schema().classify_ref("[Total Sales]") == "measure"
    assert _schema().classify_ref("Total Sales") == "measure"


def test_classifies_known_column():
    assert _schema().classify_ref("[Amount]") == "column"


def test_unknown_name_is_ambiguous():
    assert _schema().classify_ref("[Mystery]") == "ambiguous"


def test_name_in_both_sets_is_ambiguous():
    s = ModelSchema.from_model({"measures": [{"name": "X"}], "columns": [{"name": "X"}]})
    assert s.classify_ref("[X]") == "ambiguous"


def test_empty_schema_is_ambiguous():
    assert ModelSchema().classify_ref("[Anything]") == "ambiguous"


def test_classification_is_case_insensitive():
    assert _schema().classify_ref("[total sales]") == "measure"
