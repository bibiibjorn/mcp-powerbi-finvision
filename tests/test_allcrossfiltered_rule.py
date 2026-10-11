"""Phase 2.3b — ALL vs ALLCROSSFILTERED on limited-relationship models.

On a model with a limited relationship (many-to-many or bidirectional active
edge), ALL(Table) / REMOVEFILTERS(Table) does NOT clear the limited-relationship
filters, so a "% of total" measure stays stuck at 100%. ALLCROSSFILTERED is
required. This rule fires ONLY when the schema confirms a limited relationship —
on a clean star schema, ALL is correct and must not be flagged.
"""

from core.dax.tokenizer import DaxLexer
from core.dax.knowledge import DaxFunctionDatabase
from core.dax.analyzer.models import AnalysisContext, ModelSchema
from core.dax.analyzer.rules.model_rules import AllCrossFilteredRule

RULE_ID = "PY_ALL_VS_ALLCROSSFILTERED"

_LIMITED = ModelSchema.from_model(
    {
        "relationships": [
            {
                "fromTable": "Sales",
                "toTable": "Budget",
                "fromCardinality": "Many",
                "toCardinality": "Many",
                "crossFilteringBehavior": "BothDirections",
                "isActive": True,
            },
        ]
    }
)

_CLEAN = ModelSchema.from_model(
    {
        "relationships": [
            {
                "fromTable": "Sales",
                "toTable": "Product",
                "fromCardinality": "Many",
                "toCardinality": "One",
                "crossFilteringBehavior": "OneDirection",
                "isActive": True,
            },
        ]
    }
)


def _evaluate(dax, schema):
    db = DaxFunctionDatabase.get()
    tokens = DaxLexer(function_names=db.get_function_names()).tokenize_code(dax)
    ctx = AnalysisContext(schema=schema)
    return AllCrossFilteredRule().evaluate(tokens, db, ctx)


def _fires(dax, schema):
    return any(i.rule_id == RULE_ID for i in _evaluate(dax, schema))


def test_fires_on_all_with_limited_relationship():
    assert _fires("DIVIDE([Sales], CALCULATE([Sales], ALL(Sales)))", _LIMITED)


def test_fires_on_removefilters_with_limited_relationship():
    assert _fires("DIVIDE([Sales], CALCULATE([Sales], REMOVEFILTERS(Sales)))", _LIMITED)


def test_silent_on_clean_star_schema():
    assert not _fires("DIVIDE([Sales], CALCULATE([Sales], ALL(Sales)))", _CLEAN)


def test_silent_when_already_allcrossfiltered():
    assert not _fires("DIVIDE([Sales], CALCULATE([Sales], ALLCROSSFILTERED(Sales)))", _LIMITED)


def test_silent_without_schema():
    db = DaxFunctionDatabase.get()
    tokens = DaxLexer(function_names=db.get_function_names()).tokenize_code(
        "DIVIDE([Sales], CALCULATE([Sales], ALL(Sales)))"
    )
    issues = AllCrossFilteredRule().evaluate(tokens, db, None)
    assert not any(i.rule_id == RULE_ID for i in issues)


def test_silent_when_no_all_function():
    assert not _fires("DIVIDE([Sales], [Budget])", _LIMITED)
