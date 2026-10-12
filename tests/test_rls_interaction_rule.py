"""Phase 2.3a — RLS + bidirectional cross-filter interaction.

When the model has RLS roles, a measure that forces bidirectional filtering with
CROSSFILTER(..., BOTH) can leak or widen row visibility unless "Apply security
filter in both directions" is set. The rule warns about that interaction.

Fires ONLY when the schema confirms RLS roles exist AND the expression uses
CROSSFILTER(..., BOTH). Silent when there are no roles, when CROSSFILTER is
single-direction, and when there is no schema.
"""

from core.dax.tokenizer import DaxLexer
from core.dax.knowledge import DaxFunctionDatabase
from core.dax.analyzer.models import AnalysisContext, ModelSchema
from core.dax.analyzer.rules.model_rules import RlsInteractionRule

RULE_ID = "PY_RLS_BIDIRECTIONAL_INTERACTION"

_WITH_ROLES = ModelSchema.from_model({"roles": [{"name": "Region"}]})
_NO_ROLES = ModelSchema.from_model({"roles": []})


def _evaluate(dax, schema):
    db = DaxFunctionDatabase.get()
    tokens = DaxLexer(function_names=db.get_function_names()).tokenize_code(dax)
    ctx = AnalysisContext(schema=schema)
    return RlsInteractionRule().evaluate(tokens, db, ctx)


def _fires(dax, schema):
    return any(i.rule_id == RULE_ID for i in _evaluate(dax, schema))


def test_fires_on_crossfilter_both_with_roles():
    dax = "CALCULATE([Sales], CROSSFILTER(Sales[Key], Dim[Key], BOTH))"
    assert _fires(dax, _WITH_ROLES)


def test_silent_when_no_roles():
    dax = "CALCULATE([Sales], CROSSFILTER(Sales[Key], Dim[Key], BOTH))"
    assert not _fires(dax, _NO_ROLES)


def test_silent_on_single_direction_crossfilter():
    dax = "CALCULATE([Sales], CROSSFILTER(Sales[Key], Dim[Key], ONEWAY))"
    assert not _fires(dax, _WITH_ROLES)


def test_silent_without_schema():
    db = DaxFunctionDatabase.get()
    tokens = DaxLexer(function_names=db.get_function_names()).tokenize_code(
        "CALCULATE([Sales], CROSSFILTER(Sales[Key], Dim[Key], BOTH))"
    )
    assert not any(i.rule_id == RULE_ID for i in RlsInteractionRule().evaluate(tokens, db, None))


def test_silent_when_no_crossfilter():
    assert not _fires("CALCULATE([Sales], ALL(Sales))", _WITH_ROLES)
