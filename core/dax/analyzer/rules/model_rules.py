"""Model-level structural analysis rules."""

from typing import List

from core.dax.tokenizer import TokenType
from core.dax.analyzer.models import AnalysisIssue
from core.dax.analyzer.structure import call_frames, ref_table
from .base import PythonRule

_TRIVIA_TYPES = (
    TokenType.WHITESPACE,
    TokenType.NEWLINE,
    TokenType.COMMENT_LINE,
    TokenType.COMMENT_BLOCK,
)


class DirectMeasureReferenceRule(PythonRule):
    """Detect measures that are just pass-through references to another measure."""

    rule_id = "PY_DIRECT_MEASURE_REF"
    category = "maintainability"
    severity = "low"
    title = "Pass-through measure reference"

    def evaluate(self, tokens, function_db, context=None) -> List[AnalysisIssue]:
        issues: List[AnalysisIssue] = []

        # The entire expression (after tokenize_code stripping) is a single COLUMN_REF.
        meaningful = [t for t in tokens if t.type not in (TokenType.WHITESPACE, TokenType.NEWLINE)]
        if len(meaningful) == 1 and meaningful[0].type == TokenType.COLUMN_REF:
            ref = meaningful[0].value
            # Schema-aware: a lone bracket ref is a pass-through MEASURE only when
            # the schema confirms it is a measure. If schema says it is a column,
            # this is a column reference (e.g. a calc column), not a pass-through.
            schema = getattr(context, "schema", None)
            if schema is not None and schema.classify_ref(ref) == "column":
                return issues
            issues.append(
                self._make_issue(
                    f"This measure is a direct reference to {ref} — "
                    f"a pass-through that adds indirection without logic.",
                    "Consider referencing the target measure directly, "
                    "or add documentation explaining why the indirection exists.",
                )
            )
        return issues


class AllCrossFilteredRule(PythonRule):
    """ALL/REMOVEFILTERS(Table) on a table reachable through a cross-filtered path.

    Schema-aware. When filters reach a table through a many-to-many or bidirectional
    relationship, ``ALL(Table)`` / ``REMOVEFILTERS(Table)`` does not remove them, so a "% of
    total" measure can stay at 100%; ``ALLCROSSFILTERED(Table)`` clears them too.

    2026-10-03 review D-09: the check is per call and per table - ``ALL('Date'[Year])`` (a
    column) and a table nowhere near the limited relationship are not flagged. When the
    relationship rows carry no table names (ID-only DMV rows) the rule keeps the old model-level
    behaviour for table-form arguments. Silent without a schema or without a limited path.
    """

    rule_id = "PY_ALL_VS_ALLCROSSFILTERED"
    category = "correctness"
    severity = "medium"
    title = "ALL may not clear filters from cross-filtered (M2M/bidirectional) paths"

    def evaluate(self, tokens, function_db, context=None) -> List[AnalysisIssue]:
        issues: List[AnalysisIssue] = []
        schema = getattr(context, "schema", None)
        if schema is None or not schema.has_cross_filtered_paths():
            return issues

        if any(
            t.type == TokenType.FUNCTION and t.value.upper() == "ALLCROSSFILTERED" for t in tokens
        ):
            return issues

        affected = schema.cross_filtered_tables() if schema.named_edges_available() else None
        frames = call_frames(tokens)
        flagged: List[str] = []
        for idx in self._find_functions(tokens, {"ALL", "REMOVEFILTERS"}):
            stack = frames[idx]
            parent = stack[-1] if stack else None
            if (
                parent is None
                or parent.name not in ("CALCULATE", "CALCULATETABLE")
                or parent.arg < 1
            ):
                continue  # a table expression, not a filter modifier
            args = self._get_function_args(tokens, idx)
            if not args:
                flagged.append(tokens[idx].value + "()")
                continue
            if len(args) != 1:
                continue
            sg = [t for t in args[0] if t.type not in _TRIVIA_TYPES]
            if len(sg) != 1 or sg[0].type not in (TokenType.IDENTIFIER, TokenType.TABLE_REF):
                continue  # a column list: ALL(col) cannot be limited-relationship relevant
            table = ref_table(sg[0]) or " ".join(sg[0].value.lower().split())
            if affected is None or table in affected:
                flagged.append(f"{tokens[idx].value}({sg[0].value})")
        if not flagged:
            return issues

        issues.append(
            self._make_issue(
                "The model has cross-filtered paths (a many-to-many or "
                "bidirectional relationship) that reach "
                + ", ".join(sorted(set(flagged)))
                + ". ALL/REMOVEFILTERS does not clear filters that arrive through them, so a "
                "'% of total' can stay locked at 100%.",
                "Use ALLCROSSFILTERED(Table) instead of ALL(Table)/"
                "REMOVEFILTERS(Table) to also clear cross-filtered paths.",
            )
        )
        return issues


class RlsInteractionRule(PythonRule):
    """Bidirectional CROSSFILTER on an RLS model - security-filter caveat.

    Schema-aware. When the model has RLS roles, forcing bidirectional filtering with
    ``CROSSFILTER(..., BOTH)`` can widen row visibility unless "Apply security filter in both
    directions" is enabled.

    2026-10-03 review D-09: when the schema knows which tables the roles secure, the rule fires
    only if one of the CROSSFILTER endpoints is a secured table; capped at medium (per-measure
    CROSSFILTER is the canon's recommended bidirectional pattern).
    """

    rule_id = "PY_RLS_BIDIRECTIONAL_INTERACTION"
    category = "correctness"
    severity = "medium"
    title = "Bidirectional CROSSFILTER interacts with RLS"

    def evaluate(self, tokens, function_db, context=None) -> List[AnalysisIssue]:
        issues: List[AnalysisIssue] = []
        schema = getattr(context, "schema", None)
        if schema is None or not schema.roles:
            return issues

        crossfilter_idxs = self._find_functions(tokens, {"CROSSFILTER"})
        for idx in crossfilter_idxs:
            args = self._get_function_args(tokens, idx)
            # The direction is the 3rd argument; BOTH tokenizes as an IDENTIFIER.
            has_both = any(
                t.type == TokenType.IDENTIFIER and t.value.upper() == "BOTH"
                for arg in args
                for t in arg
            )
            if not has_both:
                continue
            if schema.secured_tables:
                endpoint_tables = set()
                for arg in args[:2]:
                    for k, t in enumerate(arg):
                        prev = arg[k - 1] if k else None
                        tb = ref_table(t, prev)
                        if tb:
                            endpoint_tables.add(tb)
                if not (endpoint_tables & schema.secured_tables):
                    continue
            secured_note = (
                " secured on one of the relationship's tables" if schema.secured_tables else ""
            )
            issues.append(
                self._make_issue(
                    "CROSSFILTER(..., BOTH) forces bidirectional filtering on "
                    "a model that has RLS roles" + secured_note + ". Bidirectional filtering "
                    "can widen the rows a restricted user sees.",
                    "Confirm 'Apply security filter in both directions' on the "
                    "relationship, or keep single-direction filtering and "
                    "enforce RLS on the dimension.",
                )
            )
            break
        return issues


class SemiAdditivePatternRule(PythonRule):
    """Detect LASTNONBLANK usage — suggest faster alternatives."""

    rule_id = "PY_SEMI_ADDITIVE_RANKING"
    category = "performance"
    severity = "low"
    title = "LASTNONBLANK may be slower than LASTDATE/MAX"

    def evaluate(self, tokens, function_db, context=None) -> List[AnalysisIssue]:
        issues: List[AnalysisIssue] = []
        hits = self._find_functions(tokens, {"LASTNONBLANK"})
        if hits:
            issues.append(
                self._make_issue(
                    "LASTNONBLANK evaluates its expression for each date "
                    "to find the last non-blank. When the date column has no gaps "
                    "LASTDATE is faster; LASTNONBLANK stays the right tool for "
                    "'the last date that has data'.",
                    "If the date column has no gaps, use "
                    "CALCULATE([Measure], LASTDATE(DateTable[Date])). "
                    "For the last date with data, keep LASTNONBLANK (or MAX over the fact "
                    "date) and measure it.",
                )
            )
        return issues
