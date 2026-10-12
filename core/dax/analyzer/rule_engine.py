"""JSON-based DAX rule engine -- loads rules from JSON and evaluates against token streams.

The single live DAX rule engine together with the Python structural rules in
``core.dax.analyzer.rules`` (both driven by ``DaxUnifiedAnalyzer``). The legacy
engines ``DaxRulesEngine`` / ``CallbackDetector`` / ``DaxBestPracticesAnalyzer``
are thin format shims over it (2026-09-22 review D-11).

  - Loads rules from core/dax/knowledge/rules/*.json (no code change to add a rule)
  - Operates on the DaxLexer token stream (core.dax.tokenizer), not raw text
  - A rule may declare ``"supersedes": [rule_ids]`` — the unified analyzer then
    drops those findings when this rule fires (one canonical rule per pattern)

Advice follows current SQLBI / DAX Patterns guidance (review D-05/D-07/D-08):
column filters are recommended, boolean CALCULATE filters are what
``FILTER(ALL(col))`` expands to, constant denominators use ``/``, CROSSFILTER /
USERELATIONSHIP per measure are the recommended patterns, and
``IF(ISBLANK(x), BLANK(), …)`` preserves sparsity.
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from core.dax.tokenizer import DaxLexer, Token, TokenType
from core.dax.knowledge import DaxFunctionDatabase
from core.dax.analyzer.models import AnalysisIssue
from core.dax.analyzer.structure import (
    call_frames,
    column_key,
    column_refs,
    constant_denominator,
)

logger = logging.getLogger(__name__)

# Non-descriptive variable name pattern: single char or char + single digit. A leading
# underscore is the canon's explicit "private variable" marker (core rule 3 GOOD uses
# ``_c``), so ``_x`` is not flagged -- only ``x`` / ``a1`` are (AP-38).
_NONDESCRIPTIVE_VAR_RE = re.compile(r"^[A-Za-z]\d?$")

_TRIVIA = (
    TokenType.WHITESPACE,
    TokenType.NEWLINE,
    TokenType.COMMENT_LINE,
    TokenType.COMMENT_BLOCK,
)

_LITERAL_TYPES = (TokenType.NUMBER, TokenType.STRING, TokenType.DATETIME)

# Functions that cannot appear in a "column-only" FILTER predicate (they need a
# model relationship, a context transition or another table).
_NON_SIMPLE_PREDICATE_FUNCS: Set[str] = {
    "CALCULATE",
    "CALCULATETABLE",
    "RELATED",
    "RELATEDTABLE",
    "LOOKUPVALUE",
    "EARLIER",
    "EARLIEST",
    "SELECTEDVALUE",
    "SELECTEDMEASURE",
}


def _meaningful(arg: List[Token]) -> List[Token]:
    return [t for t in arg if t.type not in _TRIVIA]


def _declared_names(tokens: List[Token]) -> Set[str]:
    """VAR / UDF-parameter names (upper) — never model tables."""
    from core.dax.dax_reference_parser import declared_names

    return declared_names([t for t in tokens if t.type not in _TRIVIA])


class JsonRuleEngine:
    """Load DAX analysis rules from JSON config files and evaluate them against tokens.

    Rules are grouped into JSON files under ``core/dax/knowledge/rules/``.
    Each file is a JSON array of rule objects with a ``pattern_type`` field that
    determines which evaluator method handles the rule.
    """

    def __init__(self, rules_dir: Optional[Path] = None) -> None:
        self._rules: List[Dict[str, Any]] = []
        self._db = DaxFunctionDatabase.get()
        self._lexer = DaxLexer(function_names=self._db.get_function_names())

        if rules_dir is None:
            rules_dir = Path(__file__).resolve().parent.parent / "knowledge" / "rules"

        self._load_rules(rules_dir)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def rule_count(self) -> int:
        """Total number of loaded rules."""
        return len(self._rules)

    def rule_ids(self) -> Set[str]:
        """IDs of every loaded JSON rule."""
        return {r.get("rule_id", "") for r in self._rules}

    def supersedes_map(self) -> Dict[str, Set[str]]:
        """rule_id -> set of rule ids it supersedes (from the JSON ``supersedes`` key)."""
        out: Dict[str, Set[str]] = {}
        for r in self._rules:
            sup = r.get("supersedes") or []
            if sup:
                out[r["rule_id"]] = {str(s) for s in sup}
        return out

    def evaluate(
        self,
        tokens: List[Token],
        dax: str,
        context: Optional[Any] = None,
    ) -> List[AnalysisIssue]:
        """Run all rules against *tokens* and return found issues."""
        issues: List[AnalysisIssue] = []

        for rule in self._rules:
            ptype = rule.get("pattern_type", "")
            evaluator = self._EVALUATORS.get(ptype)
            if evaluator is None:
                continue
            try:
                found = evaluator(self, rule, tokens, dax, context)
                issues.extend(found)
            except Exception:
                logger.debug("Rule %s raised an exception", rule.get("rule_id"), exc_info=True)

        return issues

    # ------------------------------------------------------------------
    # Rule loading
    # ------------------------------------------------------------------

    def _load_rules(self, rules_dir: Path) -> None:
        """Load all ``*.json`` files in *rules_dir*."""
        if not rules_dir.is_dir():
            logger.warning("Rules directory not found: %s", rules_dir)
            return

        for path in sorted(rules_dir.glob("*.json")):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self._rules.extend(data)
                    logger.debug("Loaded %d rules from %s", len(data), path.name)
            except Exception:
                logger.warning("Failed to load rules from %s", path, exc_info=True)

    # ------------------------------------------------------------------
    # Issue factory
    # ------------------------------------------------------------------

    def _make_issue(
        self,
        rule: Dict[str, Any],
        *,
        location: Optional[str] = None,
        match_text: Optional[str] = None,
        line: int = 0,
        flagged_function: Optional[str] = None,
    ) -> AnalysisIssue:
        """Create an ``AnalysisIssue`` from a rule dict."""
        se = None
        risk = None
        if flagged_function:
            info = self._db.lookup(flagged_function)
            if info is not None:
                se = info.se_pushable
                risk = info.callback_risk
        return AnalysisIssue(
            rule_id=rule["rule_id"],
            category=rule.get("category", "performance"),
            severity=rule.get("severity", "medium"),
            title=rule.get("title", rule["rule_id"]),
            description=rule.get("description", ""),
            fix_suggestion=rule.get("fix_suggestion", ""),
            source="static",
            location=location,
            estimated_improvement=rule.get("estimated_improvement"),
            rewrite_strategy=rule.get("rewrite_strategy"),
            references=rule.get("references"),
            match_text=match_text,
            line=line,
            flagged_function=flagged_function,
            se_classification=se,
            callback_risk=risk,
        )

    # ------------------------------------------------------------------
    # Shared structural helpers
    # ------------------------------------------------------------------

    def _args(self, tokens: List[Token], idx: int) -> List[List[Token]]:
        return self._lexer.extract_function_args(tokens, idx)

    @staticmethod
    def _bare_model_table(arg: List[Token], declared: Set[str]) -> Optional[str]:
        """Table name when *arg* is exactly one bare model table (not a VAR)."""
        sig = _meaningful(arg)
        if len(sig) != 1:
            return None
        t = sig[0]
        if t.type == TokenType.TABLE_REF:
            from core.dax.dax_names import unquote_table

            return unquote_table(t.value)
        if t.type == TokenType.IDENTIFIER and t.value.upper() not in declared:
            return t.value
        return None

    def _is_column_only_predicate(self, arg: List[Token], table: str) -> bool:
        """True when a FILTER predicate reads only columns of *table* (no measure
        refs, no context transition, no relationship traversal) — i.e. it could
        be a column filter instead of a table filter."""
        from core.dax.dax_names import split_qualified

        sig = _meaningful(arg)
        if not sig:
            return False
        tnorm = " ".join(table.lower().split())
        saw_column = False
        for k, t in enumerate(sig):
            if t.type == TokenType.QUALIFIED_REF:
                tb, _col = split_qualified(t.value)
                if " ".join(tb.lower().split()) != tnorm:
                    return False
                saw_column = True
            elif t.type == TokenType.COLUMN_REF:
                prev = sig[k - 1] if k > 0 else None
                if prev is None or prev.type != TokenType.IDENTIFIER:
                    return False  # lone [x]: a measure (context transition per row)
                if " ".join(prev.value.lower().split()) != tnorm:
                    return False
                saw_column = True
            elif t.type == TokenType.FUNCTION:
                name = t.value.upper()
                info = self._db.lookup(name)
                if name in _NON_SIMPLE_PREDICATE_FUNCS or (
                    info is not None and (info.creates_row_context or info.creates_filter_context)
                ):
                    return False
            elif t.type == TokenType.TABLE_REF:
                return False
        return saw_column

    # ------------------------------------------------------------------
    # Pattern-type evaluators
    # ------------------------------------------------------------------

    def _eval_function_nesting(
        self, rule: Dict, tokens: List[Token], dax: str, context: Any = None
    ) -> List[AnalysisIssue]:
        """Detect outer(inner(...)) nesting patterns (optional ``match.check``)."""
        match = rule.get("match", {})
        outer_names: Set[str] = {n.upper() for n in match.get("outer", [])}
        inner_name: str = match.get("inner", "").upper()
        position: str = match.get("position", "first_arg")
        check: str = match.get("check", "")

        if not outer_names or not inner_name:
            return []

        issues: List[AnalysisIssue] = []
        declared: Optional[Set[str]] = None

        for i, tok in enumerate(tokens):
            if tok.type != TokenType.FUNCTION:
                continue
            if tok.value.upper() not in outer_names:
                continue

            args = self._args(tokens, i)
            if not args:
                continue

            if position == "any_arg":
                args_to_check = args
            else:
                args_to_check = [args[0]]

            for arg_tokens in args_to_check:
                for at in arg_tokens:
                    if at.type == TokenType.FUNCTION and at.value.upper() == inner_name:
                        if check:
                            if declared is None:
                                declared = _declared_names(tokens)
                            if not self._nesting_check_holds(
                                check, tokens, i, arg_tokens, at, declared
                            ):
                                break
                        issue = self._make_issue(
                            rule,
                            location=f"{tok.value}({inner_name}(...))",
                            match_text=dax[tok.start : at.end] if at.end <= len(dax) else None,
                            line=tok.line,
                        )
                        # ``match.severity_when``: {check_name: severity} lowers the severity
                        # when that predicate holds for this call site (D-08: a column-only
                        # FILTER(ALL(Table), ...) is only a style nit, canon rule 14 GOOD).
                        for cond, sev in (match.get("severity_when") or {}).items():
                            if declared is None:
                                declared = _declared_names(tokens)
                            if self._nesting_check_holds(cond, tokens, i, arg_tokens, at, declared):
                                issue.severity = sev
                                issue.description += (
                                    " [Predicate reads columns of that table only: the column"
                                    " form is a style improvement, not a defect.]"
                                )
                                break
                        issues.append(issue)
                        break  # One match per arg group is enough.

        return issues

    def _nesting_check_holds(
        self,
        check: str,
        tokens: List[Token],
        outer_idx: int,
        arg_tokens: List[Token],
        inner_tok: Token,
        declared: Set[str],
    ) -> bool:
        inner_idx = next(
            (k for k in range(outer_idx + 1, len(tokens)) if tokens[k] is inner_tok), None
        )
        if check == "inner_table_form":
            # FILTER(ALL(Table), …) — the inner call takes exactly one bare table.
            if inner_idx is None:
                return False
            inner_args = self._args(tokens, inner_idx)
            return (
                len(inner_args) == 1 and self._bare_model_table(inner_args[0], declared) is not None
            )
        if check == "column_only_filter_all":
            # FILTER(ALL(<bare table>), <predicate on that table's columns only>): the outer
            # call is the FILTER, its first argument the ALL(...).
            outer_args = self._args(tokens, outer_idx)
            if len(outer_args) < 2:
                return False
            sg = _meaningful(outer_args[0])
            if not (sg and sg[0].type == TokenType.FUNCTION and sg[0].value.upper() == "ALL"):
                return False
            deeper = self._lexer.extract_function_args(sg, 0)
            if len(deeper) != 1:
                return False
            table = self._bare_model_table(deeper[0], declared)
            return table is not None and self._is_column_only_predicate(outer_args[1], table)
        if check == "simple_filter_over_table":
            # CALCULATETABLE(FILTER(<bare table>, <column-only predicate>), ...): the inner call
            # is the FILTER; with a measure predicate or a column-set first argument it cannot
            # be written as a boolean filter, so the wrapping is not redundant (D-07).
            if inner_idx is None:
                return False
            inner_args = self._args(tokens, inner_idx)
            if len(inner_args) < 2:
                return False
            table = self._bare_model_table(inner_args[0], declared)
            return table is not None and self._is_column_only_predicate(inner_args[1], table)
        if check == "distinct_column_count":
            # COUNTROWS(VALUES/DISTINCT(<one column>)) used as a number. Not flagged for a table
            # argument (DISTINCTCOUNT(table) is invalid) nor when compared with a value: the
            # ``COUNTROWS(VALUES(col)) = 1`` HASONEVALUE idiom (D-17).
            if inner_idx is None:
                return False
            first_sig = _meaningful(arg_tokens)
            if not first_sig or first_sig[0] is not inner_tok:
                return False  # COUNTROWS(FILTER(VALUES(col), …)): VALUES is not the whole argument
            inner_args = self._args(tokens, inner_idx)
            if len(inner_args) != 1 or not self._is_single_column_ref(inner_args[0]):
                return False
            cmp_ops = {"=", "==", "<>", "<", ">", "<=", ">="}
            prev = next(
                (tokens[j] for j in range(outer_idx - 1, -1, -1) if tokens[j].type not in _TRIVIA),
                None,
            )
            if prev is not None and prev.type == TokenType.OPERATOR and prev.value in cmp_ops:
                return False
            open_idx = next(
                (j for j in range(outer_idx + 1, len(tokens)) if tokens[j].type not in _TRIVIA),
                None,
            )
            close_idx = (
                self._lexer.paren_map(tokens).get(open_idx) if open_idx is not None else None
            )
            if close_idx is not None:
                nxt = next(
                    (
                        tokens[j]
                        for j in range(close_idx + 1, len(tokens))
                        if tokens[j].type not in _TRIVIA
                    ),
                    None,
                )
                if nxt is not None and nxt.type == TokenType.OPERATOR and nxt.value in cmp_ops:
                    return False
            return True
        if check == "isblank_constant_branch":
            # IF(ISBLANK(x), <constant>, …): BLANK converted to a value (dense).
            outer_args = self._args(tokens, outer_idx)
            if len(outer_args) < 2:
                return False
            sig = _meaningful(outer_args[1])
            if len(sig) == 1 and sig[0].type in _LITERAL_TYPES:
                return True
            if (
                len(sig) == 2
                and sig[0].type == TokenType.OPERATOR
                and sig[1].type == TokenType.NUMBER
            ):
                return True  # -1
            return False
        return True

    # Functions whose result is a table of reduced grain (distinct values / grouped rows).
    _REDUCED_GRAIN_FUNCS = frozenset(
        {
            "SUMMARIZE",
            "SUMMARIZECOLUMNS",
            "GROUPBY",
            "GENERATESERIES",
            "DATATABLE",
            "CALENDAR",
            "CALENDARAUTO",
            "TREATAS",
            "SELECTCOLUMNS_DISTINCT",
        }
    )
    _PASS_THROUGH_TABLE_FUNCS = frozenset(
        {"FILTER", "CALCULATETABLE", "ADDCOLUMNS", "SELECTCOLUMNS", "KEEPFILTERS", "NONVISUAL"}
    )

    def _iterated_grain(self, arg: List[Token], declared: Set[str]) -> str:
        """Classify the table an iterator walks: ``row`` (a model table / FILTER or
        RELATEDTABLE over one: one iteration per fact row), ``reduced`` (VALUES / DISTINCT /
        ALL of a column, SUMMARIZE, a VAR table ...: the canon's "iterate the distinct values"
        remedy) or ``unknown``."""
        sig = _meaningful(arg)
        if not sig:
            return "unknown"
        if self._bare_model_table(arg, declared) is not None:
            return "row"
        if (
            len(sig) == 1
            and sig[0].type == TokenType.IDENTIFIER
            and sig[0].value.upper() in declared
        ):
            return "reduced"
        head = sig[0]
        if head.type != TokenType.FUNCTION and head.type != TokenType.KEYWORD:
            return "unknown"
        name = head.value.upper()
        inner = self._lexer.extract_function_args(sig, 0)
        if name in self._PASS_THROUGH_TABLE_FUNCS and inner:
            return self._iterated_grain(inner[0], declared)
        if name == "TOPN" and len(inner) >= 2:
            return self._iterated_grain(inner[1], declared)
        if name == "RELATEDTABLE":
            return "row"
        if name in self._REDUCED_GRAIN_FUNCS:
            return "reduced"
        if name in (
            "VALUES",
            "DISTINCT",
            "ALL",
            "ALLNOBLANKROW",
            "ALLSELECTED",
            "ALLCROSSFILTERED",
        ):
            if not inner:
                return "unknown"
            if len(inner) == 1 and self._bare_model_table(inner[0], declared) is not None:
                return "row"
            return "reduced"
        return "unknown"

    def _eval_function_in_context(
        self, rule: Dict, tokens: List[Token], dax: str, context: Any = None
    ) -> List[AnalysisIssue]:
        """Detect target functions inside iterator expression args.

        With ``match.grain_gate`` the finding keeps its severity only when the iterator walks
        fact-grain rows (a bare model table, FILTER / RELATEDTABLE over one) or an unknown table
        expression. Iterating distinct values (VALUES / DISTINCT / SUMMARIZE / ALL(column) / a
        VAR) is the canon's own remedy (core rule 6, AP-04, MS DAX015) and is reported at info
        (2026-10-03 review D-03).
        """
        match = rule.get("match", {})
        context_fns: Set[str] = {n.upper() for n in match.get("context_functions", [])}
        targets: Set[str] = {n.upper() for n in match.get("target", [])}
        gate = bool(match.get("grain_gate", False))

        if not context_fns or not targets:
            return []

        issues: List[AnalysisIssue] = []
        declared: Optional[Set[str]] = None

        for i, tok in enumerate(tokens):
            if tok.type != TokenType.FUNCTION:
                continue
            if tok.value.upper() not in context_fns:
                continue

            args = self._args(tokens, i)
            if len(args) < 2:
                continue

            # Expression arg is the 2nd argument for standard iterators.
            expr_arg = args[1]

            for at in expr_arg:
                if at.type == TokenType.FUNCTION and at.value.upper() in targets:
                    issue = self._make_issue(
                        rule,
                        location=f"{at.value} inside {tok.value} expression",
                        match_text=at.value,
                        line=at.line,
                        flagged_function=at.value.upper(),
                    )
                    if gate:
                        if declared is None:
                            declared = _declared_names(tokens)
                        if self._iterated_grain(args[0], declared) == "reduced":
                            issue.severity = "info"
                            issue.description += (
                                " [Iterating a reduced grain (distinct values / grouped rows /"
                                " a VAR table): the row count is small, which is the remedy the"
                                " canon recommends - informational only.]"
                            )
                    issues.append(issue)
                    break  # One match per iterator.

        return issues

    def _eval_function_usage(
        self, rule: Dict, tokens: List[Token], dax: str, context: Any = None
    ) -> List[AnalysisIssue]:
        """Detect usage of specific functions anywhere in the expression.

        When the rule's ``match`` carries a ``check`` subkey, the function is
        only flagged if that check holds for the specific call site. Without a
        ``check`` the rule fires on any occurrence of the function.
        """
        match = rule.get("match", {})
        target_fns: Set[str] = {n.upper() for n in match.get("functions", [])}
        check: str = match.get("check", "")

        if not target_fns:
            return []

        issues: List[AnalysisIssue] = []
        seen: Set[str] = set()

        for i, tok in enumerate(tokens):
            if tok.type != TokenType.FUNCTION:
                continue
            upper = tok.value.upper()
            if upper not in target_fns or upper in seen:
                continue
            if check and not self._check_holds(check, tokens, i):
                continue
            seen.add(upper)
            issues.append(
                self._make_issue(
                    rule,
                    location=tok.value,
                    match_text=tok.value,
                    line=tok.line,
                    flagged_function=upper,
                )
            )

        return issues

    def _check_holds(self, check: str, tokens: List[Token], func_index: int) -> bool:
        """Evaluate a ``match.check`` predicate for the call at *func_index*.

        Unknown checks return ``True`` (fail-open to current behaviour) so a
        new ``check`` value never silently disables an otherwise-valid rule.
        """
        args = self._args(tokens, func_index)

        if check == "simple_column_expression":
            # SUMX-style: equivalent to SUM(col) ONLY when the table argument is
            # the bare model table that owns the column (review D-05):
            # SUMX(FILTER(Sales, …), Sales[Amount]) keeps the filter and is NOT
            # SUM(Sales[Amount]).
            if len(args) < 2:
                return False
            return is_trivial_iterator_args(args, _declared_names(tokens))

        if check == "more_than_two_args":
            # SUMMARIZE inline-expression form: SUMMARIZE(T, groupCols..., "Name", expr).
            # The deprecated/incorrect shape has a STRING name literal at depth 0.
            # Pure group-by SUMMARIZE has no string-literal arg and is correct.
            if len(args) <= 2:
                return False
            return any(any(t.type == TokenType.STRING for t in arg) for arg in args)

        if check == "scalar_context":
            # VALUES used where a scalar is expected (compared/assigned), not as
            # a table argument.
            return self._in_scalar_context(tokens, func_index)

        return True

    @staticmethod
    def _is_single_column_ref(arg: List[Token]) -> bool:
        """True if *arg* is exactly one qualified column reference
        (``'T'[C]`` or ``T[C]``). A bare ``[X]`` is ambiguous (usually a measure)
        and is NOT accepted."""
        meaningful = _meaningful(arg)
        if len(meaningful) == 1:
            return meaningful[0].type == TokenType.QUALIFIED_REF
        if len(meaningful) == 2:
            return (
                meaningful[0].type == TokenType.IDENTIFIER
                and meaningful[1].type == TokenType.COLUMN_REF
            )
        return False

    def _in_scalar_context(self, tokens: List[Token], func_index: int) -> bool:
        """True if the function call at *func_index* is adjacent to a comparison
        operator (scalar context), rather than used as a table expression."""
        scalar_ops = {"=", "==", "<>", "<", ">", "<=", ">="}

        j = func_index - 1
        while j >= 0:
            if tokens[j].type not in _TRIVIA:
                if tokens[j].type == TokenType.OPERATOR and tokens[j].value in scalar_ops:
                    return True
                break
            j -= 1

        pmap = self._lexer.paren_map(tokens)
        paren_idx: Optional[int] = None
        for i in range(func_index + 1, len(tokens)):
            if tokens[i].type in _TRIVIA:
                continue
            if tokens[i].type == TokenType.PAREN_OPEN:
                paren_idx = i
            break
        if paren_idx is not None:
            close_idx = pmap.get(paren_idx)
            if close_idx is not None:
                k = close_idx + 1
                while k < len(tokens):
                    if tokens[k].type not in _TRIVIA:
                        return (
                            tokens[k].type == TokenType.OPERATOR and tokens[k].value in scalar_ops
                        )
                    k += 1
        return False

    _ITERATOR_NAMES = frozenset(
        {
            "SUMX", "AVERAGEX", "MINX", "MAXX", "COUNTX", "COUNTAX", "PRODUCTX", "RANKX",
            "CONCATENATEX", "MEDIANX", "GEOMEANX", "STDEVX.S", "STDEVX.P", "VARX.S", "VARX.P",
            "PERCENTILEX.INC", "PERCENTILEX.EXC",
        }
    )  # fmt: skip

    @staticmethod
    def _predicate_excludes_zero(tokens: List[Token], key: str) -> bool:
        """True when *tokens* contain ``<col> <> 0`` / ``<col> > 0`` / ``<col> <> BLANK()``
        for the column *key* (the canon's pre-filter that makes ``/`` safe, AP-08 GOOD B)."""
        sg = _meaningful(tokens)
        for k, t in enumerate(sg):
            if t.type == TokenType.QUALIFIED_REF:
                ck = column_key([t])
                end = k + 1
            elif (
                t.type == TokenType.COLUMN_REF and k > 0 and sg[k - 1].type == TokenType.IDENTIFIER
            ):
                ck = column_key([sg[k - 1], t])
                end = k + 1
            else:
                continue
            if ck != key or end + 1 >= len(sg):
                continue
            op = sg[end]
            if op.type != TokenType.OPERATOR or op.value not in ("<>", ">"):
                continue
            rhs = sg[end + 1]
            if rhs.type == TokenType.NUMBER:
                try:
                    if float(rhs.value) == 0.0:
                        return True
                except ValueError:
                    pass
            elif (
                rhs.type in (TokenType.FUNCTION, TokenType.KEYWORD) and rhs.value.upper() == "BLANK"
            ):
                return True
        return False

    def _denominator_prefiltered(self, sig: List[Token], frames: list, k: int) -> bool:
        """The ``/`` at *k* divides by a column the enclosing CALCULATE (or the iterator's own
        table argument) already filters to non-zero: MS DAX018 / canon rule 4 exception 2."""
        from core.dax.analyzer.structure import arg_slices

        nxt = sig[k + 1 : k + 3]
        key = column_key(nxt[:1]) or column_key(nxt[:2])
        if not key:
            return False
        for fr in frames[k]:
            if fr.name in ("CALCULATE", "CALCULATETABLE") and fr.arg == 0:
                for a, b in arg_slices(sig, fr.name_idx)[1:]:
                    if self._predicate_excludes_zero(sig[a:b], key):
                        return True
            elif fr.name in self._ITERATOR_NAMES and fr.arg == 1:
                slices = arg_slices(sig, fr.name_idx)
                if slices and self._predicate_excludes_zero(sig[slices[0][0] : slices[0][1]], key):
                    return True
        return False

    def _eval_missing_function(
        self, rule: Dict, tokens: List[Token], dax: str, context: Any = None
    ) -> List[AnalysisIssue]:
        """Detect a ``/`` whose denominator is neither a non-zero literal constant nor a
        column that an enclosing filter already excludes zero from.

        Exempt (``match.exempt_constant_denominator``, default true): ``[Sales] / 1000``, a
        constant sub-expression (``(60 * 60 * 24)``, ``POWER(10, 6)``, ``-1000``) and the
        iterator pre-filter ``CALCULATE(SUMX(T, T[a] / T[b]), KEEPFILTERS(T[b] <> 0))`` (canon
        rule 4 exception 2, MS DAX018). One issue per expression (the first offending operator).
        """
        match = rule.get("match", {})
        operator = match.get("operator", "/")
        exempt_const = bool(match.get("exempt_constant_denominator", True))

        sig = [t for t in tokens if t.type not in _TRIVIA]
        frames = None
        for k, tok in enumerate(sig):
            if tok.type != TokenType.OPERATOR or tok.value != operator:
                continue
            if exempt_const:
                val = constant_denominator(sig, k)
                if val is not None and val == val and val != 0.0 and abs(val) != float("inf"):
                    continue
                if frames is None:
                    frames = call_frames(sig)
                if self._denominator_prefiltered(sig, frames, k):
                    continue
            issue = self._make_issue(
                rule,
                location=f"operator '{operator}'",
                match_text=operator,
                line=tok.line,
            )
            if exempt_const:
                if frames is None:
                    frames = call_frames(sig)
                if any(fr.name in self._ITERATOR_NAMES and fr.arg == 1 for fr in frames[k]):
                    # inside an iterator DIVIDE forces a callback; `/` is right only when the
                    # denominator is provably non-zero, which text alone cannot decide (rule 4
                    # exception 2): advise, do not nag.
                    issue.severity = "info"
                    issue.description += (
                        " [Inside an iterator DIVIDE is evaluated per row by the Formula Engine; "
                        "keep `/` only if the denominator is provably non-zero, e.g. pre-filtered "
                        "with CALCULATE(..., KEEPFILTERS(T[den] <> 0)).]"
                    )
                    issue.rewrite_strategy = None
            return [issue]
        return []

    def _eval_bare_table_arg(
        self, rule: Dict, tokens: List[Token], dax: str, context: Any = None
    ) -> List[AnalysisIssue]:
        """Detect FILTER over a bare MODEL table (never a VAR / UDF parameter).

        With ``match.check == "simple_column_predicate"`` only a predicate that
        reads columns of that table alone (no measure, no relationship) is
        flagged — the case a column filter can replace.
        """
        match = rule.get("match", {})
        fn_name = str(match.get("function", "FILTER")).upper()
        check = match.get("check", "")
        issues: List[AnalysisIssue] = []
        declared: Optional[Set[str]] = None

        for i, tok in enumerate(tokens):
            if tok.type != TokenType.FUNCTION or tok.value.upper() != fn_name:
                continue
            args = self._args(tokens, i)
            if not args:
                continue
            if declared is None:
                declared = _declared_names(tokens)
            table = self._bare_model_table(args[0], declared)
            if table is None:
                continue
            if check == "simple_column_predicate":
                if len(args) < 2 or not self._is_column_only_predicate(args[1], table):
                    continue
            issues.append(
                self._make_issue(
                    rule,
                    location=f"{tok.value}({_meaningful(args[0])[0].value}, ...)",
                    match_text=_meaningful(args[0])[0].value,
                    line=tok.line,
                )
            )

        return issues

    def _eval_repeated_reference(
        self, rule: Dict, tokens: List[Token], dax: str, context: Any = None
    ) -> List[AnalysisIssue]:
        """Detect the same MEASURE reference appearing >= threshold times in the
        same filter context (review D-08).

        Only lone ``[X]`` references count (a column reference cannot be put in a
        VAR); with a schema, names the schema knows as columns are skipped.
        Occurrences are grouped by the innermost enclosing call that changes the
        evaluation context (CALCULATE, iterators, FILTER, time intelligence …) —
        a VAR hoisted out of different CALCULATE filters would change results.

        Review D-02 (2026-10-03): branches of a conditional (IF / SWITCH / COALESCE /
        IFERROR, DIVIDE's alternate result) are evaluated lazily, so occurrences in DIFFERENT
        branches are not "repeated" (only one lane runs). A reference that also occurs
        unconditionally (condition / SWITCH expression / depth 0) is counted with all of its
        branch occurrences; otherwise each branch is counted on its own.
        """
        from core.dax.analyzer.structure import is_conditional_arg

        match = rule.get("match", {})
        threshold: int = match.get("threshold", 3)
        schema = getattr(context, "schema", None)

        sig = [t for t in tokens if t.type not in _TRIVIA]
        # One frame per open paren: [context key, function name, current arg index, open index].
        stack: List[List[Any]] = []
        # (context key, name) -> {branch key or None -> [tokens]}
        groups: Dict[Tuple[int, str], Dict[Any, List[Token]]] = {}

        for k, tok in enumerate(sig):
            if tok.type == TokenType.PAREN_OPEN:
                prev = sig[k - 1] if k > 0 else None
                parent = stack[-1][0] if stack else -1
                key = parent
                fname = ""
                if prev is not None and prev.type in (TokenType.FUNCTION, TokenType.KEYWORD):
                    fname = prev.value.upper()
                    info = self._db.lookup(fname)
                    if fname in ("CALCULATE", "CALCULATETABLE") or (
                        info is not None
                        and (info.creates_row_context or info.creates_filter_context)
                    ):
                        key = k
                stack.append([key, fname, 0, k])
                continue
            if tok.type == TokenType.PAREN_CLOSE:
                if stack:
                    stack.pop()
                continue
            if tok.type == TokenType.COMMA and stack:
                stack[-1][2] += 1
                continue
            if tok.type != TokenType.COLUMN_REF:
                continue
            prev = sig[k - 1] if k > 0 else None
            if prev is not None and prev.type == TokenType.IDENTIFIER:
                continue  # Table[Col]
            if tok.value.startswith("[@"):
                continue
            if schema is not None and schema.classify_ref(tok.value) == "column":
                continue
            ctx_key = stack[-1][0] if stack else -1
            branch = tuple((f[3], f[2]) for f in stack if is_conditional_arg(f[1], f[2]))
            groups.setdefault((ctx_key, tok.value.lower()), {}).setdefault(
                branch or None, []
            ).append(tok)

        issues: List[AnalysisIssue] = []
        reported: Set[str] = set()
        for (_ctx, _name), by_branch in groups.items():
            if None in by_branch:
                # evaluated unconditionally anyway: every occurrence is part of one group
                counted = [sum(len(v) for v in by_branch.values())]
                first = min((t for v in by_branch.values() for t in v), key=lambda t: t.start)
                lanes = [(counted[0], first)]
            else:
                lanes = [(len(v), v[0]) for v in by_branch.values()]
            for count, ftok in lanes:
                if count < threshold or ftok.value.lower() in reported:
                    continue
                reported.add(ftok.value.lower())
                issues.append(
                    self._make_issue(
                        rule,
                        location=f"{ftok.value} referenced {count} times in one filter context",
                        match_text=ftok.value,
                        line=ftok.line,
                    )
                )

        return issues

    def _eval_nesting_depth(
        self, rule: Dict, tokens: List[Token], dax: str, context: Any = None
    ) -> List[AnalysisIssue]:
        """Detect function nesting deeper than max_depth (review D-13).

        ``function == "*"`` counts every function call; otherwise ONLY calls to
        that function are counted (CALCULATE nested in CALCULATE, not a single
        CALCULATE that happens to sit three functions deep). One issue per
        expression, reported at the maximum depth reached.
        """
        match = rule.get("match", {})
        target_fn: str = match.get("function", "*").upper()
        max_depth: int = match.get("max_depth", 5)

        sig = [t for t in tokens if t.type not in _TRIVIA]
        stack: List[bool] = []  # per open paren: counts toward depth?
        depth = 0
        worst = 0
        worst_tok: Optional[Token] = None
        for k, tok in enumerate(sig):
            if tok.type == TokenType.PAREN_OPEN:
                prev = sig[k - 1] if k > 0 else None
                is_call = prev is not None and prev.type in (
                    TokenType.FUNCTION,
                    TokenType.KEYWORD,
                    TokenType.IDENTIFIER,
                )
                counts = is_call and (target_fn == "*" or prev.value.upper() == target_fn)
                stack.append(counts)
                if counts:
                    depth += 1
                    if depth > worst:
                        worst = depth
                        worst_tok = prev
            elif tok.type == TokenType.PAREN_CLOSE:
                if stack and stack.pop():
                    depth -= 1

        if worst > max_depth and worst_tok is not None:
            return [
                self._make_issue(
                    rule,
                    location=f"nesting depth {worst} at {worst_tok.value}",
                    match_text=worst_tok.value,
                    line=worst_tok.line,
                )
            ]
        return []

    def _eval_unused_var(
        self, rule: Dict, tokens: List[Token], dax: str, context: Any = None
    ) -> List[AnalysisIssue]:
        """Detect VAR definitions whose names are never referenced afterward."""
        issues: List[AnalysisIssue] = []

        var_defs: List[tuple] = []
        for i, tok in enumerate(tokens):
            if tok.type == TokenType.KEYWORD and tok.value.upper() == "VAR":
                for j in range(i + 1, len(tokens)):
                    nt = tokens[j]
                    if nt.type in _TRIVIA:
                        continue
                    if nt.type == TokenType.IDENTIFIER:
                        var_defs.append((nt.value, j, nt))
                    break

        if not var_defs:
            return []

        for var_name, def_idx, var_tok in var_defs:
            upper_name = var_name.upper()
            used = False
            for j in range(def_idx + 1, len(tokens)):
                t = tokens[j]
                if t.type == TokenType.IDENTIFIER and t.value.upper() == upper_name:
                    used = True
                    break
            if not used:
                issues.append(
                    self._make_issue(
                        rule,
                        location=f"VAR {var_name}",
                        match_text=var_name,
                        line=var_tok.line,
                    )
                )

        return issues

    def _eval_switch_without_default(
        self, rule: Dict, tokens: List[Token], dax: str, context: Any = None
    ) -> List[AnalysisIssue]:
        """Detect SWITCH calls without a default/else branch.

        SWITCH(expr, val1, res1, val2, res2) has an even number of args after
        the expression; with a default the count is odd.
        """
        issues: List[AnalysisIssue] = []

        for i, tok in enumerate(tokens):
            if tok.type not in (TokenType.FUNCTION, TokenType.KEYWORD):
                continue
            if tok.value.upper() != "SWITCH":
                continue

            args = self._args(tokens, i)
            if len(args) < 3:
                continue

            remaining = len(args) - 1
            if remaining % 2 == 0:
                issues.append(
                    self._make_issue(
                        rule,
                        location="SWITCH without default",
                        match_text="SWITCH",
                        line=tok.line,
                    )
                )

        return issues

    # ------------------------------------------------------------------
    # Pattern-rule evaluator (P0-4)
    #
    # These rules carry only a prose `match.description` in JSON (no
    # machine-readable pattern), so each is dispatched by rule_id to a small
    # typed handler below.
    # ------------------------------------------------------------------

    def _eval_pattern(
        self, rule: Dict, tokens: List[Token], dax: str, context: Any = None
    ) -> List[AnalysisIssue]:
        handler = self._PATTERN_HANDLERS.get(rule.get("rule_id", ""))
        if handler is None:
            # No handler => surfaced by the drift-guard test, not silently lost.
            return []
        return handler(self, rule, tokens, dax)

    # -- individual pattern handlers --------------------------------------

    def _pat_redundant_if_true_false(
        self, rule: Dict, tokens: List[Token], dax: str
    ) -> List[AnalysisIssue]:
        """IF(cond, TRUE, FALSE) / IF(cond, FALSE, TRUE) — redundant boolean."""
        issues: List[AnalysisIssue] = []
        for i, tok in enumerate(tokens):
            if tok.type not in (TokenType.FUNCTION, TokenType.KEYWORD):
                continue
            if tok.value.upper() != "IF":
                continue
            args = self._args(tokens, i)
            if len(args) != 3:
                continue

            def _bool_literal(arg_tokens):
                vals = [t.value.upper() for t in _meaningful(arg_tokens)]
                if vals in (["TRUE"], ["FALSE"]):
                    return vals[0]
                if vals in (["TRUE", "(", ")"], ["FALSE", "(", ")"]):
                    return vals[0]
                return None

            second = _bool_literal(args[1])
            third = _bool_literal(args[2])
            if second and third and second != third:
                issues.append(
                    self._make_issue(
                        rule, location="IF returning TRUE/FALSE", match_text="IF", line=tok.line
                    )
                )
        return issues

    def _pat_no_return(self, rule: Dict, tokens: List[Token], dax: str) -> List[AnalysisIssue]:
        """VAR present but no RETURN keyword anywhere."""
        has_var = any(t.type == TokenType.KEYWORD and t.value.upper() == "VAR" for t in tokens)
        has_return = any(
            t.type == TokenType.KEYWORD and t.value.upper() == "RETURN" for t in tokens
        )
        if has_var and not has_return:
            return [self._make_issue(rule, location="VAR without RETURN", line=1)]
        return []

    def _pat_nondescriptive_var(
        self, rule: Dict, tokens: List[Token], dax: str
    ) -> List[AnalysisIssue]:
        """VAR names that are single char or char + single digit (x, a1, _t)."""
        issues: List[AnalysisIssue] = []
        for i, tok in enumerate(tokens):
            if tok.type == TokenType.KEYWORD and tok.value.upper() == "VAR":
                for j in range(i + 1, len(tokens)):
                    nt = tokens[j]
                    if nt.type in _TRIVIA:
                        continue
                    if nt.type == TokenType.IDENTIFIER and _NONDESCRIPTIVE_VAR_RE.match(nt.value):
                        issues.append(
                            self._make_issue(
                                rule,
                                location=f"VAR {nt.value}",
                                match_text=nt.value,
                                line=nt.line,
                            )
                        )
                    break
        return issues

    def _pat_long_measure(self, rule: Dict, tokens: List[Token], dax: str) -> List[AnalysisIssue]:
        """Token count exceeds threshold (default 150 significant tokens)."""
        threshold = int((rule.get("match") or {}).get("threshold", 150))
        significant = _meaningful(tokens)
        if len(significant) > threshold:
            return [
                self._make_issue(
                    rule, location=f"{len(significant)} tokens (> {threshold})", line=1
                )
            ]
        return []

    def _pat_multiple_calculate_filters(
        self, rule: Dict, tokens: List[Token], dax: str
    ) -> List[AnalysisIssue]:
        """CALCULATE with more than 4 comma-separated arguments."""
        issues: List[AnalysisIssue] = []
        for i, tok in enumerate(tokens):
            if tok.type not in (TokenType.FUNCTION, TokenType.KEYWORD):
                continue
            if tok.value.upper() != "CALCULATE":
                continue
            args = self._args(tokens, i)
            if len(args) > 4:
                issues.append(
                    self._make_issue(
                        rule,
                        location=f"CALCULATE with {len(args)} args",
                        match_text="CALCULATE",
                        line=tok.line,
                    )
                )
        return issues

    _DATEISH_COLUMN_RE = re.compile(r"(year|date|month|quarter|period|fiscal|yyyy)", re.I)

    def _pat_fixed_filter_in_calculate(
        self, rule: Dict, tokens: List[Token], dax: str
    ) -> List[AnalysisIssue]:
        """CALCULATE with a hard-coded DATE literal in a filter argument, e.g.
        ``CALCULATE([X], 'T'[Year] = 2024)`` or ``CALCULATE([X], T[Day] >= DATE(2024, 1, 1))``.

        Only the date shape is flagged (2026-10-03 review D-10): a literal predicate on a
        non-date column (``Geography[Country] = "US"``, ``Sales[Amount] > 1000``, a fixed
        currency) is the canon's own GOOD form (core rule 1, MS DAX001/DAX007). The date
        shape is: a ``DATE(...)`` / ``DATEVALUE(...)`` / ``dt"..."`` right-hand side, or a
        4-digit year number compared to a column whose name says year / date / month ..."""
        issues: List[AnalysisIssue] = []
        ops = {"=", "==", ">", "<", ">=", "<=", "<>"}
        for i, tok in enumerate(tokens):
            if tok.type not in (TokenType.FUNCTION, TokenType.KEYWORD):
                continue
            if tok.value.upper() != "CALCULATE":
                continue
            args = self._args(tokens, i)
            hit = False
            for arg in args[1:]:
                sig = _meaningful(arg)
                for k in range(len(sig) - 2):
                    if sig[k].type == TokenType.QUALIFIED_REF:
                        col = column_refs([sig[k]])[0][2]
                    elif (
                        sig[k].type == TokenType.COLUMN_REF
                        and k > 0
                        and sig[k - 1].type == TokenType.IDENTIFIER
                    ):
                        col = column_refs([sig[k - 1], sig[k]])[0][2]
                    else:
                        continue
                    if not (sig[k + 1].type == TokenType.OPERATOR and sig[k + 1].value in ops):
                        continue
                    rhs = sig[k + 2]
                    if rhs.type == TokenType.DATETIME:
                        hit = True
                    elif rhs.type == TokenType.FUNCTION and rhs.value.upper() in (
                        "DATE",
                        "DATEVALUE",
                        "TIME",
                    ):
                        hit = True
                    elif rhs.type == TokenType.NUMBER and self._DATEISH_COLUMN_RE.search(col):
                        try:
                            hit = 1900 <= float(rhs.value) <= 2100 and float(rhs.value).is_integer()
                        except ValueError:
                            hit = False
                    if hit:
                        break
                if hit:
                    break
            if hit:
                issues.append(
                    self._make_issue(
                        rule,
                        location="CALCULATE hardcoded date literal filter",
                        match_text="CALCULATE",
                        line=tok.line,
                    )
                )
        return issues

    def _pat_blank_propagation(
        self, rule: Dict, tokens: List[Token], dax: str
    ) -> List[AnalysisIssue]:
        """``<number> - DIVIDE(...)`` or ``<number> - a / b`` (review D-07).

        Adjacency check: a NUMBER literal, then ``-``, then either a DIVIDE call
        or a term that contains a top-level ``/`` before the next additive
        operator. ``DATEADD(d, -1, YEAR)`` (the ``-`` precedes the number) and a
        ``-`` anywhere else no longer trigger it.
        """
        sig = _meaningful(tokens)
        n = len(sig)
        for k in range(n - 2):
            if sig[k].type != TokenType.NUMBER:
                continue
            if not (sig[k + 1].type == TokenType.OPERATOR and sig[k + 1].value == "-"):
                continue
            # The number must itself start an operand (not "x * 2 - …"): the
            # previous token must be an opener, a separator, RETURN or start.
            prev = sig[k - 1] if k > 0 else None
            if prev is not None and not (
                prev.type in (TokenType.PAREN_OPEN, TokenType.COMMA, TokenType.BRACE_OPEN)
                or (prev.type == TokenType.OPERATOR and prev.value in ("=", "+", "&", "==", "<>"))
                or (prev.type == TokenType.KEYWORD and prev.value.upper() in ("RETURN", "ELSE"))
            ):
                continue
            first = sig[k + 2]
            if first.type == TokenType.FUNCTION and first.value.upper() == "DIVIDE":
                return [self._make_issue(rule, location="1 - DIVIDE(...)", line=first.line)]
            depth = 0
            for rt in sig[k + 2 :]:
                if rt.type in (TokenType.PAREN_OPEN, TokenType.BRACE_OPEN):
                    depth += 1
                elif rt.type in (TokenType.PAREN_CLOSE, TokenType.BRACE_CLOSE):
                    depth -= 1
                    if depth < 0:
                        break
                elif depth == 0 and rt.type == TokenType.COMMA:
                    break
                elif depth == 0 and rt.type == TokenType.OPERATOR:
                    if rt.value == "/":
                        return [self._make_issue(rule, location="1 - a / b", line=rt.line)]
                    if rt.value in (
                        "+",
                        "-",
                        "&",
                        "=",
                        "==",
                        "<>",
                        "<",
                        ">",
                        "<=",
                        ">=",
                        "&&",
                        "||",
                    ):
                        break
                elif depth == 0 and rt.type == TokenType.KEYWORD:
                    break
        return []

    _PATTERN_HANDLERS = {
        "MAINT_REDUNDANT_IF_TRUE_FALSE": _pat_redundant_if_true_false,
        "MAINT_NO_RETURN": _pat_no_return,
        "MAINT_NONDESCRIPTIVE_VAR": _pat_nondescriptive_var,
        "MAINT_LONG_MEASURE": _pat_long_measure,
        "MAINT_MULTIPLE_CALCULATE_FILTERS": _pat_multiple_calculate_filters,
        "CORR_FIXED_FILTER_IN_CALCULATE": _pat_fixed_filter_in_calculate,
        "CORR_BLANK_PROPAGATION": _pat_blank_propagation,
    }

    # ------------------------------------------------------------------
    # Evaluator dispatch table
    # ------------------------------------------------------------------

    _EVALUATORS = {
        "function_nesting": _eval_function_nesting,
        "function_in_context": _eval_function_in_context,
        "function_usage": _eval_function_usage,
        "missing_function": _eval_missing_function,
        "bare_table_arg": _eval_bare_table_arg,
        "repeated_reference": _eval_repeated_reference,
        "nesting_depth": _eval_nesting_depth,
        "unused_var": _eval_unused_var,
        "switch_without_default": _eval_switch_without_default,
        "pattern": _eval_pattern,
    }


def is_trivial_iterator_args(args: List[List[Token]], declared: Set[str]) -> bool:
    """True when ``X(args)`` (SUMX/AVERAGEX/MINX/MAXX) is exactly ``AGG(col)``:
    the table argument is the bare model table that owns the single qualified
    column in the expression argument (review D-05).

    ``SUMX(Sales, Sales[Amount])`` -> True; ``SUMX(FILTER(Sales, …), Sales[Amount])``,
    ``SUMX(RELATEDTABLE(Sales), Sales[Amount])``, ``SUMX(_t, Sales[Amount])`` and
    ``SUMX(Sales, [Measure])`` -> False.
    """
    from core.dax.dax_names import split_qualified, unquote_table

    if len(args) != 2:
        return False
    table_sig = _meaningful(args[0])
    expr_sig = _meaningful(args[1])
    if len(table_sig) != 1:
        return False
    tt = table_sig[0]
    if tt.type == TokenType.TABLE_REF:
        table = unquote_table(tt.value)
    elif tt.type == TokenType.IDENTIFIER and tt.value.upper() not in declared:
        table = tt.value
    else:
        return False
    if len(expr_sig) == 1 and expr_sig[0].type == TokenType.QUALIFIED_REF:
        col_table, col = split_qualified(expr_sig[0].value)
    elif (
        len(expr_sig) == 2
        and expr_sig[0].type == TokenType.IDENTIFIER
        and expr_sig[1].type == TokenType.COLUMN_REF
    ):
        col_table, col = expr_sig[0].value, expr_sig[1].value
    else:
        return False
    if col.startswith("@") or col.startswith("[@"):
        return False
    return " ".join(col_table.lower().split()) == " ".join(table.lower().split())
