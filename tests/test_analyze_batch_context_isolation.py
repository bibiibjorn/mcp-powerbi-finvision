"""Regression: analyze_batch must not mutate a caller-supplied context (P0-3).

`ctx = context or AnalysisContext()` reused the caller's object and overwrote
ctx.measure_name on every iteration, so the last measure's name bled into all
results that reference context.measure_name. analyze_batch must build a fresh
per-measure context that carries the caller's enrichment forward without
mutating the caller's object.
"""

from core.dax.analyzer.unified_analyzer import DaxUnifiedAnalyzer
from core.dax.analyzer.models import AnalysisContext, ModelSchema


def test_analyze_batch_does_not_mutate_caller_context():
    schema = ModelSchema(measure_names={"sales"}, column_names={"amount"})
    shared = AnalysisContext(schema=schema)

    analyzer = DaxUnifiedAnalyzer()
    measures = [("Sales", "SUM('T'[Amount])"), ("Cost", "SUM('T'[Amount])")]
    analyzer.analyze_batch(measures, context=shared)

    # The caller's context must be untouched (measure_name never set on it).
    assert (
        shared.measure_name is None
    ), f"caller context mutated: measure_name={shared.measure_name!r}"
    # The schema must still be the same object (per-measure context reuses it).
    assert shared.schema is schema


def test_analyze_batch_returns_one_result_per_measure():
    analyzer = DaxUnifiedAnalyzer()
    shared = AnalysisContext()
    measures = [("Alpha", "VAR x = 1 RETURN 2"), ("Beta", "VAR y = 1 RETURN 2")]
    results = analyzer.analyze_batch(measures, context=shared)
    assert len(results) == 2


def test_analyze_batch_with_no_context_still_works():
    analyzer = DaxUnifiedAnalyzer()
    results = analyzer.analyze_batch([("M", "SUM('T'[A])")])
    assert len(results) == 1
