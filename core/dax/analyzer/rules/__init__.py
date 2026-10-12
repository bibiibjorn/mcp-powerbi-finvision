"""Python-based structural analysis rules for DAX.

One canonical rule per anti-pattern (2026-09-22 review D-09): where a JSON rule
already detects a pattern, the Python twin was deleted rather than kept as a
second finding with a different id / severity. Removed Python rules and their
canonical replacement:

  PY_IF_IN_ITERATOR          -> CB001      PY_DIVIDE_IN_ITERATOR   -> CB007
  PY_NESTED_ITERATOR         -> CB003      PY_RELATED_IN_ITERATOR  -> PERF_RELATED_IN_SUMX
  PY_NESTED_CALCULATE        -> PERF_NESTED_CALCULATE
  PY_FILTER_TABLE_NOT_COLUMN -> CB004      PY_INTERSECT_VS_TREATAS -> CORR_INTERSECT_VS_TREATAS
  PY_ADDCOLUMNS_SUMMARIZE    -> PERF_ADDCOLUMNS_SUMMARIZE
  PY_MEASURE_REF_WITHOUT_VAR -> MAINT_REPEATED_MEASURE
  (JSON PERF_UNNECESSARY_SUMX -> PY_UNNECESSARY_ITERATOR, the canonical one)

The JSON rules list these ids in ``supersedes`` so an externally merged rule
set cannot re-introduce the double finding.
"""

from .base import PythonRule  # noqa: F401
from .iterator_rules import UnnecessaryIteratorRule, is_trivial_iterator  # noqa: F401
from .calculate_rules import (
    KeepfiltersOpportunityRule,
    AllTableVsColumnRule,
    AllPreferRemovefiltersRule,
)
from .context_rules import VarDefeatingShortCircuitRule
from .model_rules import (
    DirectMeasureReferenceRule,
    SemiAdditivePatternRule,
    AllCrossFilteredRule,
    RlsInteractionRule,
)
from .runtime_rules import (
    MeasureInFilterRule,
    FilterArgNoTransitionRule,
    SelectedValueOnTableRule,
    RemovefiltersAsTableRule,
    CrossTablePredicateRule,
    ExtendedColumnInFilterRule,
    VarConditionalFilterRule,
)
from .design_rules import (
    AllExceptRule,
    NeedKeepfiltersRule,
    IteratorNeedsKeepfiltersRule,
    SplitAndPredicateRule,
    NeverBlankRule,
    ExpandedTableIteratorRule,
    SummarizeColumnsRule,
    SummarizeColumnsKeyRule,
    RepeatedCalculateRule,
    QualificationRule,
    QualifiedMeasureRule,
    CalendarAutoRule,
    CalcItemOverrideRule,
    UdfExprBareColumnRule,
    EvaluateAndLogRule,
    FormatStringDoubleEvalRule,
    RelatedInCalcColumnRule,
)
from .udf_rules import (
    UdfUntypedParameterRule,
    UdfEagerEvalRiskRule,
)


def load_python_rules() -> list:
    """Instantiate and return all Python rules."""
    return [
        # Iterator rules
        UnnecessaryIteratorRule(),
        # Calculate rules
        KeepfiltersOpportunityRule(),
        AllTableVsColumnRule(),
        AllPreferRemovefiltersRule(),
        # Context rules
        VarDefeatingShortCircuitRule(),
        # Model rules
        DirectMeasureReferenceRule(),
        SemiAdditivePatternRule(),
        AllCrossFilteredRule(),
        RlsInteractionRule(),
        # UDF rules (DAX UDF GA, CL 1702). Inert on non-UDF expressions.
        UdfUntypedParameterRule(),
        UdfEagerEvalRiskRule(),
        # Runtime-failure rules (canon core rule 8 / 14, AP-30..AP-35; review D-25)
        MeasureInFilterRule(),
        FilterArgNoTransitionRule(),
        SelectedValueOnTableRule(),
        RemovefiltersAsTableRule(),
        CrossTablePredicateRule(),
        ExtendedColumnInFilterRule(),
        VarConditionalFilterRule(),
        # Design / hygiene rules from the canon coverage matrix (review D-26 / D-27)
        AllExceptRule(),
        NeedKeepfiltersRule(),
        IteratorNeedsKeepfiltersRule(),
        SplitAndPredicateRule(),
        NeverBlankRule(),
        ExpandedTableIteratorRule(),
        SummarizeColumnsRule(),
        SummarizeColumnsKeyRule(),
        RepeatedCalculateRule(),
        QualificationRule(),
        QualifiedMeasureRule(),
        CalendarAutoRule(),
        CalcItemOverrideRule(),
        UdfExprBareColumnRule(),
        EvaluateAndLogRule(),
        FormatStringDoubleEvalRule(),
        RelatedInCalcColumnRule(),
    ]
