"""Runtime-failure rules: constructs the canon lists as "these fail (or silently misbehave) at
runtime" (2026-10-03 review D-25; canon core rule 8 / 14 and AP-30 .. AP-35).

All are lexical: they walk the enclosing-call frames of the token stream and never need a model.
A schema (``AnalysisContext.schema``) only sharpens two of them (measure-vs-column for a lone
``[x]``, field-parameter tables).

  CORR_MEASURE_IN_FILTER          AP-30  measure compared with a column of the iterated table
  CORR_FILTER_ARG_NO_TRANSITION   AP-31  SELECTEDVALUE(iterated column) in a CALCULATE filter arg
  CORR_SELECTEDVALUE_FP           AP-32  SELECTEDVALUE(<table>) - a column is required
  CORR_REMOVEFILTERS_AS_TABLE     AP-33  REMOVEFILTERS used as a table expression
  CORR_CROSS_TABLE_PREDICATE      AP-34  one boolean filter predicate over two tables' columns
  CORR_EXTENDED_COLUMN_IN_FILTER  AP-35  [@Col] in a CALCULATE boolean filter
  CORR_VAR_CONDITIONAL_FILTER     rule 8 a VAR assigned an IF/SWITCH used as a CALCULATE filter
"""

from typing import List, Optional, Set, Tuple

from core.dax.tokenizer import Token, TokenType
from core.dax.analyzer.models import AnalysisIssue
from core.dax.analyzer.rule_engine import _declared_names
from core.dax.analyzer.structure import (
    CallFrame,
    arg_slices,
    call_frames,
    column_refs,
    ref_table,
)
from .base import PythonRule
from .context_rules import top_level_vars

_CALC = ("CALCULATE", "CALCULATETABLE")

# Functions that open a row context for their expression arguments (arg index >= 1).
_ROW_CONTEXT_FNS = frozenset(
    {
        "SUMX", "AVERAGEX", "MINX", "MAXX", "COUNTX", "COUNTAX", "PRODUCTX", "RANKX", "CONCATENATEX",
        "MEDIANX", "GEOMEANX", "STDEVX.S", "STDEVX.P", "VARX.S", "VARX.P", "PERCENTILEX.INC",
        "PERCENTILEX.EXC", "ADDCOLUMNS", "SELECTCOLUMNS", "GENERATE", "GENERATEALL", "FILTER",
        "TOPN",
    }
)  # fmt: skip

_COMPARE_OPS = {"=", "==", "<>", "<", ">", "<=", ">="}


def _is_measure_like(tok: Token, prev: Optional[Token], context) -> bool:
    """A lone ``[x]`` that is a measure (not ``T[x]``, not an extended column ``[@x]``)."""
    if tok.type != TokenType.COLUMN_REF or tok.value.startswith("[@"):
        return False
    if prev is not None and prev.type == TokenType.IDENTIFIER:
        return False
    schema = getattr(context, "schema", None)
    if schema is not None and schema.classify_ref(tok.value) == "column":
        return False
    return True


def _table_arg_tables(tokens: List[Token], declared: Set[str]) -> Set[str]:
    """Tables an iterator / FILTER walks, read off its first-argument tokens."""
    out: Set[str] = set()
    for k, t in enumerate(tokens):
        tb = ref_table(t, tokens[k - 1] if k else None)
        if tb:
            out.add(tb)
        elif t.type == TokenType.IDENTIFIER and t.value.upper() not in declared:
            nxt = tokens[k + 1] if k + 1 < len(tokens) else None
            if nxt is None or nxt.type != TokenType.COLUMN_REF:
                out.add(" ".join(t.value.lower().split()))
    return out


def _iterated_tables_at(
    tokens: List[Token], frames: Tuple[CallFrame, ...], declared: Set[str]
) -> Set[str]:
    """Tables iterated by the row-context frames enclosing a token."""
    out: Set[str] = set()
    for fr in frames:
        if fr.name in _ROW_CONTEXT_FNS and fr.arg >= 1:
            slices = arg_slices(tokens, fr.name_idx)
            if slices:
                a, b = slices[0]
                out |= _table_arg_tables(tokens[a:b], declared)
    return out


def _depth0_indices(tokens: List[Token], a: int, b: int) -> List[int]:
    """Indices in ``tokens[a:b]`` that sit at bracket depth 0 relative to the slice."""
    depth = 0
    out: List[int] = []
    for k in range(a, b):
        tt = tokens[k].type
        if tt in (TokenType.PAREN_CLOSE, TokenType.BRACE_CLOSE):
            depth -= 1
        if depth == 0:
            out.append(k)
        if tt in (TokenType.PAREN_OPEN, TokenType.BRACE_OPEN):
            depth += 1
    return out


class MeasureInFilterRule(PythonRule):
    """FILTER(<table>, <column of that table> <op> [Measure]) - AP-30 / core rule 14.

    The measure is evaluated once per iterated row (context transition), so ``[MaxDate]`` is
    the current iterated date and every row filters itself: the running total returns the grand
    total everywhere. Read the value from the outer context with MAX('Date'[Date]) or a VAR.
    A measure compared with a constant (``[Sales] > 100``) is the legitimate "filter by measure
    value" form and is not flagged."""

    rule_id = "CORR_MEASURE_IN_FILTER"
    category = "correctness"
    severity = "high"
    title = "Measure compared with a column inside FILTER - each row filters itself"

    def evaluate(self, tokens, function_db, context=None) -> List[AnalysisIssue]:
        issues: List[AnalysisIssue] = []
        declared: Optional[Set[str]] = None
        for idx in self._find_functions(tokens, {"FILTER"}):
            slices = arg_slices(tokens, idx)
            if len(slices) < 2:
                continue
            if declared is None:
                declared = _declared_names(tokens)
            iterated = _table_arg_tables(tokens[slices[0][0] : slices[0][1]], declared)
            if not iterated:
                continue
            a, b = slices[1]
            top = _depth0_indices(tokens, a, b)
            ops = [
                k
                for k in top
                if tokens[k].type == TokenType.OPERATOR and tokens[k].value in _COMPARE_OPS
            ]
            if not ops:
                continue
            split = ops[0]
            sides = (range(a, split), range(split + 1, b))
            hit = None
            for col_side, meas_side in (sides, sides[::-1]):
                has_col = any(
                    ref_table(tokens[k], tokens[k - 1] if k > a else None) in iterated
                    for k in col_side
                )
                meas = next(
                    (
                        tokens[k]
                        for k in meas_side
                        if _is_measure_like(tokens[k], tokens[k - 1] if k > a else None, context)
                    ),
                    None,
                )
                if has_col and meas is not None:
                    hit = meas
                    break
            if hit is None:
                continue
            issues.append(
                self._make_issue(
                    f"{hit.value} is compared with a column of the table FILTER iterates. The "
                    "measure gets a context transition per row, so it returns the current row's "
                    "own value and every row passes (a running total shows the grand total "
                    "everywhere - canon core rule 14 / AP-30).",
                    "Read the outer value with a raw aggregation (MAX('Date'[Date])) or capture it "
                    "in a VAR before the FILTER.",
                    location=f"FILTER(..., {hit.value})",
                    match_text=hit.value,
                    line=hit.line,
                )
            )
        return issues


class FilterArgNoTransitionRule(PythonRule):
    """SELECTEDVALUE of the iterated table's column inside a CALCULATE filter argument - AP-31.

    CALCULATE evaluates its filter arguments in the OUTER context, before context transition,
    so the iterated row is invisible there and SELECTEDVALUE returns BLANK."""

    rule_id = "CORR_FILTER_ARG_NO_TRANSITION"
    category = "correctness"
    severity = "high"
    title = "SELECTEDVALUE of the iterated row inside a CALCULATE filter argument returns BLANK"

    def evaluate(self, tokens, function_db, context=None) -> List[AnalysisIssue]:
        issues: List[AnalysisIssue] = []
        frames = None
        declared: Optional[Set[str]] = None
        for idx in self._find_functions(tokens, {"SELECTEDVALUE"}):
            args = self._get_function_args(tokens, idx)
            if not args:
                continue
            refs = column_refs(args[0])
            if not refs or not refs[0][1]:
                continue
            table = refs[0][1]
            if frames is None:
                frames = call_frames(tokens)
                declared = _declared_names(tokens)
            stack = frames[idx]
            # the innermost CALCULATE whose filter argument holds the token
            pos = next((p for p in range(len(stack) - 1, -1, -1) if stack[p].name in _CALC), None)
            if pos is None or stack[pos].arg < 1:
                continue
            outer = _iterated_tables_at(tokens, stack[:pos], declared or set())
            if table not in outer:
                continue
            issues.append(
                self._make_issue(
                    f"SELECTEDVALUE({args[0][0].value if args[0] else ''}...) reads a column of the "
                    "table the enclosing iterator walks, but a CALCULATE filter argument is "
                    "evaluated before context transition, so it returns BLANK (canon AP-31).",
                    "Capture the row value in a VAR before CALCULATE: "
                    "VAR _y = Customers[BirthYear] RETURN CALCULATE([Sales], 'Date'[Year] = _y).",
                    location="SELECTEDVALUE in CALCULATE filter",
                    match_text="SELECTEDVALUE",
                    line=tokens[idx].line,
                )
            )
        return issues


class SelectedValueOnTableRule(PythonRule):
    """SELECTEDVALUE(<bare table>) - e.g. a field-parameter table - AP-32.

    SELECTEDVALUE takes a column. A field parameter is a table of (label, NAMEOF-ref, ordinal)
    rows; branch on the label column instead."""

    rule_id = "CORR_SELECTEDVALUE_FP"
    category = "correctness"
    severity = "high"
    title = "SELECTEDVALUE needs a column, not a table (field parameter)"

    def evaluate(self, tokens, function_db, context=None) -> List[AnalysisIssue]:
        issues: List[AnalysisIssue] = []
        declared: Optional[Set[str]] = None
        schema = getattr(context, "schema", None)
        for idx in self._find_functions(tokens, {"SELECTEDVALUE"}):
            args = self._get_function_args(tokens, idx)
            if not args:
                continue
            sg = [t for t in args[0]]
            if len(sg) != 1 or sg[0].type not in (TokenType.IDENTIFIER, TokenType.TABLE_REF):
                continue
            if declared is None:
                declared = _declared_names(tokens)
            if sg[0].type == TokenType.IDENTIFIER and sg[0].value.upper() in declared:
                continue  # a UDF ANYREF parameter / VAR
            is_fp = schema is not None and (
                " ".join(sg[0].value.strip("'").lower().split()) in schema.field_parameter_tables
            )
            issues.append(
                self._make_issue(
                    f"SELECTEDVALUE({sg[0].value}) is applied to a table"
                    + (" (a field parameter)" if is_fp else "")
                    + ": SELECTEDVALUE takes a column. A field parameter is a table of "
                    "(label, NAMEOF reference, ordinal) rows (canon AP-32).",
                    "Branch on the label column: VAR _sel = SELECTEDVALUE(FieldParam[Parameter "
                    'Fields], "Sales") RETURN SWITCH(_sel, "Sales", [Sales], ...).',
                    location=f"SELECTEDVALUE({sg[0].value})",
                    match_text=sg[0].value,
                    line=tokens[idx].line,
                )
            )
        return issues


class RemovefiltersAsTableRule(PythonRule):
    """REMOVEFILTERS used as a table expression - AP-33 (a CALCULATE modifier only)."""

    rule_id = "CORR_REMOVEFILTERS_AS_TABLE"
    category = "correctness"
    severity = "high"
    title = "REMOVEFILTERS is a CALCULATE modifier, not a table expression"

    def evaluate(self, tokens, function_db, context=None) -> List[AnalysisIssue]:
        issues: List[AnalysisIssue] = []
        frames = None
        for idx in self._find_functions(tokens, {"REMOVEFILTERS"}):
            if frames is None:
                frames = call_frames(tokens)
            stack = frames[idx]
            if not stack:
                continue
            parent = stack[-1]
            if parent.name in _CALC and parent.arg >= 1:
                continue
            if parent.name in ("", "{") or not function_db.is_function(parent.name):
                continue  # grouping paren / UDF call: cannot tell
            issues.append(
                self._make_issue(
                    f"REMOVEFILTERS is used inside {parent.name}(...), where a table expression "
                    "is expected. REMOVEFILTERS only works as a CALCULATE / CALCULATETABLE "
                    "filter modifier; elsewhere it does not parse (canon AP-33).",
                    "Use ALL(...) for the table expression, e.g. "
                    'FILTER(ALL(Products[Category]), Products[Category] = "Bikes").',
                    location=f"{parent.name}(REMOVEFILTERS(...))",
                    match_text="REMOVEFILTERS",
                    line=tokens[idx].line,
                )
            )
        return issues


class CrossTablePredicateRule(PythonRule):
    """One CALCULATE boolean filter comparing columns of two tables - AP-34 (runtime error)."""

    rule_id = "CORR_CROSS_TABLE_PREDICATE"
    category = "correctness"
    severity = "high"
    title = "CALCULATE filter predicate references columns of two tables"

    def evaluate(self, tokens, function_db, context=None) -> List[AnalysisIssue]:
        issues: List[AnalysisIssue] = []
        for idx in self._find_functions(tokens, _CALC):
            for fi, (a, b) in enumerate(arg_slices(tokens, idx)):
                if fi == 0:
                    continue
                top = _depth0_indices(tokens, a, b)
                if not any(
                    tokens[k].type == TokenType.OPERATOR and tokens[k].value in _COMPARE_OPS
                    for k in top
                ):
                    continue
                tables = {
                    tb
                    for k in top
                    if (tb := ref_table(tokens[k], tokens[k - 1] if k > a else None))
                }
                if len(tables) < 2:
                    continue
                issues.append(
                    self._make_issue(
                        "A single CALCULATE boolean filter compares columns of "
                        f"{len(tables)} tables ({', '.join(sorted(tables))}). A boolean filter "
                        "predicate may reference one table's columns (canon AP-34); this fails "
                        "at runtime.",
                        "Capture one side in a VAR first: VAR _bVal = TableB[Col] RETURN "
                        "CALCULATE(..., TableA[Col] <= _bVal).",
                        location="CALCULATE boolean filter over two tables",
                        match_text=tokens[idx].value,
                        line=tokens[idx].line,
                    )
                )
        return issues


class ExtendedColumnInFilterRule(PythonRule):
    """``[@Col]`` (ADDCOLUMNS extended column) inside a CALCULATE boolean filter - AP-35."""

    rule_id = "CORR_EXTENDED_COLUMN_IN_FILTER"
    category = "correctness"
    severity = "high"
    title = "Extended column [@Col] used in a CALCULATE filter predicate"

    def evaluate(self, tokens, function_db, context=None) -> List[AnalysisIssue]:
        issues: List[AnalysisIssue] = []
        for idx in self._find_functions(tokens, _CALC):
            for fi, (a, b) in enumerate(arg_slices(tokens, idx)):
                if fi == 0:
                    continue
                top = _depth0_indices(tokens, a, b)
                hit = next(
                    (
                        tokens[k]
                        for k in top
                        if tokens[k].type == TokenType.COLUMN_REF
                        and tokens[k].value.startswith("[@")
                    ),
                    None,
                )
                if hit is None:
                    continue
                issues.append(
                    self._make_issue(
                        f"{hit.value} is an extended column; it cannot be used directly in a "
                        "CALCULATE boolean filter argument (canon AP-35).",
                        "Capture it in a VAR first: VAR _val = [@ExtendedCol] RETURN "
                        "CALCULATE(SUM(...), OtherTable[Col] = _val).",
                        location=f"CALCULATE filter uses {hit.value}",
                        match_text=hit.value,
                        line=hit.line,
                    )
                )
                break
        return issues


class VarConditionalFilterRule(PythonRule):
    """``VAR _f = IF(...)`` passed as a CALCULATE filter - core rule 8.

    A conditional assigned to a VAR is evaluated eagerly, before CALCULATE, so it cannot act as
    a filter that CALCULATE evaluates lazily; inline the conditional at the call site."""

    rule_id = "CORR_VAR_CONDITIONAL_FILTER"
    category = "correctness"
    severity = "high"
    title = "Conditional VAR used as a CALCULATE filter argument"

    def evaluate(self, tokens, function_db, context=None) -> List[AnalysisIssue]:
        issues: List[AnalysisIssue] = []
        vars_, return_idx = top_level_vars(tokens)
        if not vars_:
            return issues
        cond_vars = {}
        for name, _name_idx, b_start, b_end in vars_:
            if b_start < b_end and tokens[b_start].value.upper() in ("IF", "SWITCH", "IF.EAGER"):
                cond_vars[name.upper()] = name
        if not cond_vars:
            return issues
        for idx in self._find_functions(tokens, _CALC):
            for fi, (a, b) in enumerate(arg_slices(tokens, idx)):
                if fi == 0 or b - a != 1:
                    continue
                t = tokens[a]
                if t.type == TokenType.IDENTIFIER and t.value.upper() in cond_vars:
                    issues.append(
                        self._make_issue(
                            f"VAR {cond_vars[t.value.upper()]} holds an IF/SWITCH and is passed as "
                            "a CALCULATE filter argument. A conditional VAR is evaluated "
                            "eagerly, so it cannot stand in for a filter (canon core rule 8).",
                            "Inline the conditional at the call site: "
                            "CALCULATE([M], IF(cond, filterA, filterB)) or two CALCULATE branches.",
                            location=f"CALCULATE(..., {t.value})",
                            match_text=t.value,
                            line=t.line,
                        )
                    )
        return issues
