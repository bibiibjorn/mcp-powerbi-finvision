"""DirectMeasureReferenceRule becomes schema-aware (Phase 2.2 consumer).

A measure whose whole body is a bare [X] is a pass-through ONLY when [X] is a
measure. When the schema says [X] is a column, the expression is a column
reference (e.g. a calc column), not a pass-through measure — the rule must stay
silent. With no schema it keeps the prior conservative behaviour (fires).
"""

from core.dax.tokenizer import DaxLexer
from core.dax.knowledge import DaxFunctionDatabase
from core.dax.analyzer.models import AnalysisContext, ModelSchema
from core.dax.analyzer.rules.model_rules import DirectMeasureReferenceRule


def _evaluate(dax, context=None):
    db = DaxFunctionDatabase.get()
    tokens = DaxLexer(function_names=db.get_function_names()).tokenize_code(dax)
    return DirectMeasureReferenceRule().evaluate(tokens, db, context)


def test_fires_when_schema_confirms_measure():
    ctx = AnalysisContext(schema=ModelSchema.from_model({"measures": [{"name": "Total Sales"}]}))
    issues = _evaluate("[Total Sales]", ctx)
    assert any(i.rule_id == "PY_DIRECT_MEASURE_REF" for i in issues)


def test_silent_when_schema_says_column():
    ctx = AnalysisContext(schema=ModelSchema.from_model({"columns": [{"name": "Amount"}]}))
    issues = _evaluate("[Amount]", ctx)
    assert not any(i.rule_id == "PY_DIRECT_MEASURE_REF" for i in issues)


def test_fires_without_schema_conservative_fallback():
    # No schema -> keep prior behaviour (a lone bracket ref is treated as a
    # pass-through candidate).
    issues = _evaluate("[Total Sales]", None)
    assert any(i.rule_id == "PY_DIRECT_MEASURE_REF" for i in issues)
