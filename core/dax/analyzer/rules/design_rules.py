"""Design / hygiene rules from the canon coverage matrix (2026-10-03 review D-26, D-27).

Severity is calibrated to what the canon says is measurable: the rules that need a model fact
(storage mode, calc-group tables, UDF signatures, columns vs measures) stay silent without it.

  PY_ALLEXCEPT_VS_ALL_VALUES         AP-17    ALLEXCEPT returns the grand total when the kept column is unfiltered
  PY_NEED_KEEPFILTERS                AP-25    multi-value IN list in CALCULATE without KEEPFILTERS
  PY_ITERATOR_NEEDS_KEEPFILTERS      AP-24    iterator over a composite (Year-Month) column
  MAINT_SPLIT_AND_PREDICATE          DAX001   && inside one CALCULATE boolean filter
  PY_NEVER_BLANK                     AP-49    measure that can never be BLANK (+ 0, COALESCE root ...)
  PERF_EXPANDED_TABLE_ITERATOR       AP-05    iterator over a whole table whose expression uses a measure
  PERF_PK_SUMMARIZECOLUMNS           AP-22    key column in SUMMARIZECOLUMNS group-by
  PERF_SUMMARIZECOLUMNS_INTERNAL_FILTER AP-23 filter table / boolean inside SUMMARIZECOLUMNS
  MAINT_REPEATED_CALCULATE           AP-37    identical CALCULATE expression repeated
  MAINT_UNQUALIFIED_COLUMN / MAINT_QUALIFIED_MEASURE  AP-40 (needs a schema)
  PERF_CALENDARAUTO                  AP-43    CALENDARAUTO date table
  PY_CALC_ITEM_OVERRIDE              AP-44    CALCULATE hard-wiring a calculation-group item
  CORR_UDF_EXPR_BARE_COLUMN          AP-48    bare column passed to a UDF expr parameter in an iterator
  MAINT_EVALUATEANDLOG               MS guideline: never EVALUATEANDLOG in production
"""

import re
from typing import Dict, List, Optional, Set

from core.dax.tokenizer import Token, TokenType
from core.dax.analyzer.models import AnalysisIssue
from core.dax.analyzer.rule_engine import _declared_names
from core.dax.analyzer.structure import (
    arg_slices,
    call_frames,
    column_refs,
    matching_close,
    ref_table,
)
from .base import PythonRule
from .context_rules import top_level_vars

_CALC = ("CALCULATE", "CALCULATETABLE")

_ITERATORS = frozenset(
    {
        "SUMX", "AVERAGEX", "MINX", "MAXX", "COUNTX", "COUNTAX", "PRODUCTX", "RANKX", "CONCATENATEX",
        "MEDIANX", "GEOMEANX", "STDEVX.S", "STDEVX.P", "VARX.S", "VARX.P", "PERCENTILEX.INC",
        "PERCENTILEX.EXC",
    }
)  # fmt: skip

_ROW_CONTEXT_FNS = _ITERATORS | {"ADDCOLUMNS", "SELECTCOLUMNS", "GENERATE", "GENERATEALL", "FILTER"}

_COMPARE_OPS = {"=", "==", "<>", "<", ">", "<=", ">="}

_COMPOSITE_COLUMN_RE = re.compile(
    r"(year|yr)\W*(month|mon|qtr|quarter|week|wk)|(month|mon|qtr|quarter)\W*(year|yr)", re.I
)
_KEY_COLUMN_RE = re.compile(r"(?:^|[\s_\-])(?:key|id)$|[a-z0-9](?:Key|KEY|ID|Id)$")


def _depth0(tokens: List[Token], a: int, b: int) -> List[int]:
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


def _text(tokens: List[Token]) -> str:
    return " ".join(t.value.lower() for t in tokens)


class AllExceptRule(PythonRule):
    """ALLEXCEPT - AP-17. Without a filter on the kept column it silently returns the grand
    total; SQLBI (2026) prefers REMOVEFILTERS + VALUES. ``FILTER(ALLEXCEPT(...), cond)``
    materializes the table (DAX Optimizer 100800)."""

    rule_id = "PY_ALLEXCEPT_VS_ALL_VALUES"
    category = "correctness"
    severity = "info"
    title = "ALLEXCEPT returns the grand total when the kept column is not filtered"

    def evaluate(self, tokens, function_db, context=None) -> List[AnalysisIssue]:
        issues: List[AnalysisIssue] = []
        frames = None
        for idx in self._find_functions(tokens, {"ALLEXCEPT"}):
            if frames is None:
                frames = call_frames(tokens)
            stack = frames[idx]
            parent = stack[-1] if stack else None
            if parent is not None and parent.name == "FILTER" and parent.arg == 0:
                issue = self._make_issue(
                    "FILTER(ALLEXCEPT(...), cond) materializes the table in the Formula Engine "
                    "(DAX Optimizer 100800).",
                    "Use ALLEXCEPT / REMOVEFILTERS as a CALCULATE modifier and pass the "
                    "predicate as a separate filter argument.",
                    line=tokens[idx].line,
                )
                issue.severity = "medium"
                issues.append(issue)
            elif parent is not None and parent.name in _CALC and parent.arg >= 1:
                issues.append(
                    self._make_issue(
                        "ALLEXCEPT only REMOVES filters: if the kept column is not filtered at "
                        "runtime it silently produces the grand total, not the subtotal "
                        "(canon core rule 10 / AP-17; SQLBI 2026: avoid ALLEXCEPT by default).",
                        "Prefer CALCULATE([M], REMOVEFILTERS(Table), VALUES(Table[Col])): it adds "
                        "the visible values back, correct whether the column is in scope or not.",
                        line=tokens[idx].line,
                    )
                )
        return issues


class NeedKeepfiltersRule(PythonRule):
    """``col IN {a, b, c}`` as a CALCULATE filter without KEEPFILTERS - AP-25 / core rule 9.

    CALCULATE replaces an existing filter on the column, so a "% trendy colours" numerator can
    exceed its denominator. Low by default (an IN list that deliberately overrides the slicer is
    legitimate); medium when the CALCULATE is the numerator of a ratio over the same measure."""

    rule_id = "PY_NEED_KEEPFILTERS"
    category = "correctness"
    severity = "low"
    title = "Multi-value filter in CALCULATE without KEEPFILTERS"

    def evaluate(self, tokens, function_db, context=None) -> List[AnalysisIssue]:
        issues: List[AnalysisIssue] = []
        frames = None
        for idx in self._find_functions(tokens, _CALC):
            slices = arg_slices(tokens, idx)
            for fi, (a, b) in enumerate(slices):
                if fi == 0 or b <= a:
                    continue
                head = tokens[a]
                if head.type == TokenType.FUNCTION and head.value.upper() == "KEEPFILTERS":
                    continue
                top = _depth0(tokens, a, b)
                in_pos = next(
                    (
                        k
                        for k in top
                        if tokens[k].type == TokenType.KEYWORD and tokens[k].value.upper() == "IN"
                    ),
                    None,
                )
                if (
                    in_pos is None
                    or in_pos + 1 >= b
                    or tokens[in_pos + 1].type != TokenType.BRACE_OPEN
                ):
                    continue
                close = matching_close(tokens, in_pos + 1)
                inner = tokens[in_pos + 2 : close]
                if not any(t.type == TokenType.COMMA for t in inner):
                    continue  # one value: filtering a single value needs no KEEPFILTERS
                if not column_refs(tokens[a:in_pos]):
                    continue
                issue = self._make_issue(
                    "A multi-value IN filter in CALCULATE replaces any existing filter on that "
                    "column, so the result can exceed the unfiltered total (canon core rule 9 / "
                    "AP-25).",
                    "Wrap it: CALCULATE([M], KEEPFILTERS(Table[Col] IN {...})) - unless "
                    "overriding the slicer is the intent.",
                    line=tokens[a].line,
                )
                if frames is None:
                    frames = call_frames(tokens)
                if self._is_ratio_numerator(tokens, frames, idx):
                    issue.severity = "medium"
                    issue.description += (
                        " Here the CALCULATE is the numerator of a ratio over the same measure: "
                        "the denominator silently differs from the numerator's filter scope."
                    )
                issues.append(issue)
                break
        return issues

    @staticmethod
    def _is_ratio_numerator(tokens, frames, idx: int) -> bool:
        stack = frames[idx]
        parent = stack[-1] if stack else None
        if parent is None or parent.name != "DIVIDE" or parent.arg != 0:
            return False
        calc_slices = arg_slices(tokens, idx)
        div_slices = arg_slices(tokens, parent.name_idx)
        if not calc_slices or len(div_slices) < 2:
            return False
        measure = _text(tokens[calc_slices[0][0] : calc_slices[0][1]])
        denominator = _text(tokens[div_slices[1][0] : div_slices[1][1]])
        return bool(measure) and measure == denominator


class IteratorNeedsKeepfiltersRule(PythonRule):
    """AVERAGEX/SUMX over VALUES(<Year-Month style column>) - AP-24.

    A composite period column usually means arbitrarily-shaped (multi-column) filters on the
    date table; the iterator strips them and totals come out wrong. Info: it is a heuristic on
    the column name."""

    rule_id = "PY_ITERATOR_NEEDS_KEEPFILTERS"
    category = "correctness"
    severity = "info"
    title = "Iterator over a composite period column may need KEEPFILTERS"

    def evaluate(self, tokens, function_db, context=None) -> List[AnalysisIssue]:
        issues: List[AnalysisIssue] = []
        for idx in self._find_functions(tokens, _ITERATORS):
            slices = arg_slices(tokens, idx)
            if len(slices) < 2:
                continue
            a, b = slices[0]
            head = tokens[a] if a < b else None
            if (
                head is None
                or head.type != TokenType.FUNCTION
                or head.value.upper()
                not in (
                    "VALUES",
                    "DISTINCT",
                )
            ):
                continue
            refs = column_refs(tokens[a + 1 : b])
            if len(refs) != 1 or not _COMPOSITE_COLUMN_RE.search(refs[0][2]):
                continue
            issues.append(
                self._make_issue(
                    f"{tokens[idx].value} iterates the values of a composite period column "
                    f"({refs[0][2]}). When the filter context holds multi-column (arbitrarily "
                    "shaped) filters, the iterator strips them and the visual total looks "
                    "wrong (canon AP-24).",
                    f"If the total is wrong, wrap the table argument: {tokens[idx].value}"
                    f"(KEEPFILTERS(VALUES(...)), ...).",
                    line=tokens[idx].line,
                )
            )
        return issues


class SplitAndPredicateRule(PythonRule):
    """``T[a] = 1 && T[b] = 2`` inside one CALCULATE filter - MS DAX001: separate arguments."""

    rule_id = "MAINT_SPLIT_AND_PREDICATE"
    category = "performance"
    severity = "info"
    title = "&& inside one CALCULATE filter - use separate filter arguments"

    def evaluate(self, tokens, function_db, context=None) -> List[AnalysisIssue]:
        issues: List[AnalysisIssue] = []
        for idx in self._find_functions(tokens, _CALC):
            for fi, (a, b) in enumerate(arg_slices(tokens, idx)):
                if fi == 0:
                    continue
                top = _depth0(tokens, a, b)
                if not any(
                    tokens[k].type == TokenType.OPERATOR and tokens[k].value == "&&" for k in top
                ):
                    continue
                tables = {
                    tb
                    for k in top
                    if (tb := ref_table(tokens[k], tokens[k - 1] if k > a else None))
                }
                if len(tables) != 1:
                    continue
                issues.append(
                    self._make_issue(
                        "A && inside one CALCULATE boolean filter builds a single multi-column "
                        "filter; separate filter arguments are fusion-friendly and intersect "
                        "cleanly (canon core rule 1, MS DAX001).",
                        "Split it: CALCULATE([M], T[a] = 1, T[b] = 2).",
                        line=tokens[a].line,
                    )
                )
                break
        return issues


class NeverBlankRule(PythonRule):
    """A measure that can never return BLANK - AP-49 / MS QRY004.

    ``+ 0``, ``COALESCE(x, 0)``, ``IFERROR(x, 0)`` and ``DIVIDE(a, b, 0)`` at the measure root
    destroy blank propagation, so a visual materializes the full cross product (measured 3x time,
    2x memory). ``+ 0`` is medium, the others low (they are also the documented error handlers);
    the sparsity-preserving ``IF(ISBLANK(x), BLANK(), ...)`` form is not flagged."""

    rule_id = "PY_NEVER_BLANK"
    category = "performance"
    severity = "medium"
    title = "Measure never returns BLANK (dense measure)"

    def evaluate(self, tokens, function_db, context=None) -> List[AnalysisIssue]:
        issues: List[AnalysisIssue] = []
        _vars, return_idx = top_level_vars(tokens)
        start = (return_idx + 1) if return_idx is not None else 0
        root = tokens[start:]
        if not root:
            return issues
        top = _depth0(root, 0, len(root))

        def is_zero(t: Token) -> bool:
            try:
                return t.type == TokenType.NUMBER and float(t.value) == 0.0
            except ValueError:
                return False

        why = None
        sev = "medium"
        n = len(root)
        for k in top:
            t = root[k]
            if t.type == TokenType.OPERATOR and t.value == "+":
                nxt = root[k + 1] if k + 1 < n else None
                prv = root[k - 1] if k > 0 else None
                after_zero = (
                    nxt is not None
                    and is_zero(nxt)
                    and (k + 2 >= n or k + 2 in top and root[k + 2].value in ("+", "-"))
                )
                before_zero = (
                    prv is not None
                    and is_zero(prv)
                    and (k - 2 < 0 or root[k - 2].value in ("+", "-"))
                )
                if after_zero or before_zero:
                    why = "+ 0 forces a value where BLANK propagation would drop the row"
                    break
        if why is None:
            head = root[0]
            if (
                head.type == TokenType.FUNCTION
                and root[1:2]
                and root[1].type == TokenType.PAREN_OPEN
                and matching_close(root, 1) == n - 1
            ):
                name = head.value.upper()
                slices = arg_slices(root, 0)
                if name in ("COALESCE", "IFERROR") and len(slices) >= 2:
                    a, b = slices[-1]
                    if b - a == 1 and is_zero(root[a]):
                        why = f"{name}(..., 0) at the measure root converts BLANK to 0"
                        sev = "low"
                elif name == "DIVIDE" and len(slices) == 3:
                    a, b = slices[2]
                    if b - a == 1 and is_zero(root[a]):
                        why = "DIVIDE(..., 0) converts a BLANK / zero-denominator result to 0"
                        sev = "low"
        if why is not None:
            issue = self._make_issue(
                f"{why}. The measure is never BLANK, so SUMMARIZECOLUMNS cannot drop empty rows "
                "and the visual materializes the full cross product (canon AP-49: ~3x time, "
                "~2x memory, 1 -> 3 SE queries).",
                "Leave BLANK alone and use a format string for the display of empty cells; if a "
                "zero is genuinely required, test for blank first and measure the visual.",
                line=root[0].line,
            )
            issue.severity = sev
            issues.append(issue)
        return issues


class ExpandedTableIteratorRule(PythonRule):
    """SUMX(<whole table>, [Measure]) - AP-05 / core rule 2.

    Context transition filters every column of the EXPANDED table: iterating a 13-column
    dimension creates 13 simultaneous per-row filters, iterating the key creates one."""

    rule_id = "PERF_EXPANDED_TABLE_ITERATOR"
    category = "performance"
    severity = "medium"
    title = "Iterator over a whole table with a measure / CALCULATE per row"

    def evaluate(self, tokens, function_db, context=None) -> List[AnalysisIssue]:
        issues: List[AnalysisIssue] = []
        declared: Optional[Set[str]] = None
        schema = getattr(context, "schema", None)
        for idx in self._find_functions(tokens, _ITERATORS):
            slices = arg_slices(tokens, idx)
            if len(slices) < 2:
                continue
            a, b = slices[0]
            if b - a != 1:
                continue
            t = tokens[a]
            if t.type == TokenType.IDENTIFIER:
                if declared is None:
                    declared = _declared_names(tokens)
                if t.value.upper() in declared:
                    continue
                table = " ".join(t.value.lower().split())
            elif t.type == TokenType.TABLE_REF:
                table = ref_table(t)
            else:
                continue
            ea, eb = slices[1]
            transition = False
            for k in range(ea, eb):
                tk = tokens[k]
                if tk.type == TokenType.FUNCTION and tk.value.upper() in _CALC:
                    transition = True
                    break
                if tk.type == TokenType.COLUMN_REF and not tk.value.startswith("[@"):
                    prev = tokens[k - 1] if k > ea else None
                    if prev is not None and prev.type == TokenType.IDENTIFIER:
                        continue
                    if schema is not None and schema.classify_ref(tk.value) == "column":
                        continue
                    transition = True
                    break
            if not transition:
                continue
            issue = self._make_issue(
                f"{tokens[idx].value} walks every row of {t.value} and evaluates a measure / "
                "CALCULATE per row. Context transition filters every column of the expanded "
                "table, not just the key (canon core rule 2 / AP-05).",
                f"Iterate the key only: {tokens[idx].value}(VALUES({t.value}[Key]), ...) - or "
                "SUMMARIZE(table, key, other needed columns).",
                line=tokens[idx].line,
            )
            if schema is not None and schema.named_edges_available():
                fact_only = table not in schema.one_side_tables()
                if fact_only:
                    issue.severity = "low"
            issues.append(issue)
        return issues


class SummarizeColumnsRule(PythonRule):
    """SUMMARIZECOLUMNS group-by hygiene - AP-22 (key columns) and AP-23 (internal filters)."""

    rule_id = "PERF_SUMMARIZECOLUMNS_INTERNAL_FILTER"
    category = "performance"
    severity = "medium"
    title = "Filter table / boolean predicate inside SUMMARIZECOLUMNS"

    _ALLOWED_GROUPBY_FNS = frozenset(
        {"ROLLUPADDISSUBTOTAL", "ROLLUPGROUP", "ROLLUP", "IGNORE", "NONVISUAL", "ADDMISSINGITEMS"}
    )

    def evaluate(self, tokens, function_db, context=None) -> List[AnalysisIssue]:
        issues: List[AnalysisIssue] = []
        for idx in self._find_functions(tokens, {"SUMMARIZECOLUMNS"}):
            slices = arg_slices(tokens, idx)
            # group-by + filter region = everything before the first "Name" string literal
            region = []
            for a, b in slices:
                if b - a >= 1 and tokens[a].type == TokenType.STRING:
                    break
                region.append((a, b))
            for a, b in region:
                if b <= a:
                    continue
                head = tokens[a]
                is_filter_fn = (
                    head.type == TokenType.FUNCTION
                    and head.value.upper() not in self._ALLOWED_GROUPBY_FNS
                )
                is_boolean = any(
                    tokens[k].type == TokenType.OPERATOR and tokens[k].value in _COMPARE_OPS
                    for k in _depth0(tokens, a, b)
                )
                if is_filter_fn or is_boolean:
                    issues.append(
                        self._make_issue(
                            "A filter table / boolean predicate inside SUMMARIZECOLUMNS creates "
                            "coalesced-filter (auto-exist) interactions with the group-by columns "
                            "(canon AP-23, MS DAX009).",
                            "Wrap it: CALCULATETABLE(SUMMARIZECOLUMNS(cols, measures), <filter>), "
                            "or NONVISUAL(<filter>) when the filter must only shape the group-by.",
                            line=head.line,
                        )
                    )
                    break
        return issues


class SummarizeColumnsKeyRule(PythonRule):
    """Key column in a SUMMARIZECOLUMNS group-by - AP-22 (SPARSE -> DENSE query)."""

    rule_id = "PERF_PK_SUMMARIZECOLUMNS"
    category = "performance"
    severity = "low"
    title = "Key column in SUMMARIZECOLUMNS group-by"

    def evaluate(self, tokens, function_db, context=None) -> List[AnalysisIssue]:
        from core.dax.dax_names import split_qualified

        issues: List[AnalysisIssue] = []
        for idx in self._find_functions(tokens, {"SUMMARIZECOLUMNS"}):
            for a, b in arg_slices(tokens, idx):
                if b - a >= 1 and tokens[a].type == TokenType.STRING:
                    break
                if b - a == 1 and tokens[a].type == TokenType.QUALIFIED_REF:
                    table, col = split_qualified(tokens[a].value)
                    if _KEY_COLUMN_RE.search(col):
                        issues.append(
                            self._make_issue(
                                f"{tokens[a].value} looks like a key column. Grouping by a "
                                "dimension primary key turns an efficient SPARSE query into a "
                                "costly DENSE one (canon AP-22: 831k intermediate rows, 38 s).",
                                "Group by the attribute columns the visual needs; hide "
                                "relationship columns so the field list cannot offer them.",
                                line=tokens[a].line,
                            )
                        )
                        break
        return issues


class RepeatedCalculateRule(PythonRule):
    """The same CALCULATE(...) expression written twice in one filter context - AP-37."""

    rule_id = "MAINT_REPEATED_CALCULATE"
    category = "maintainability"
    severity = "low"
    title = "Identical CALCULATE expression repeated - store it in a VAR"

    def evaluate(self, tokens, function_db, context=None) -> List[AnalysisIssue]:
        issues: List[AnalysisIssue] = []
        frames = call_frames(tokens)
        seen: Dict[tuple, List[int]] = {}
        for idx in self._find_functions(tokens, _CALC):
            close = matching_close(tokens, idx + 1)
            body = _text(tokens[idx : close + 1])
            # identical text is only interchangeable inside the same filter context
            ctx_key = tuple(
                f.open_idx for f in frames[idx] if f.name in _CALC or f.name in _ROW_CONTEXT_FNS
            )
            seen.setdefault((body, ctx_key), []).append(idx)
        reported = set()
        for (body, _ctx), idxs in seen.items():
            if len(idxs) < 2 or body in reported:
                continue
            reported.add(body)
            first = tokens[idxs[0]]
            issues.append(
                self._make_issue(
                    f"The same CALCULATE expression appears {len(idxs)} times in one filter "
                    "context; each copy is evaluated separately (canon AP-37).",
                    "Capture it once: VAR _Total = CALCULATE(...) RETURN ... _Total ...",
                    location=f"CALCULATE repeated {len(idxs)} times",
                    match_text=first.value,
                    line=first.line,
                )
            )
        return issues


class QualificationRule(PythonRule):
    """Unqualified column reference - AP-40 (SQLBI style: always 'Table'[Column]). Needs a schema
    that knows both the measure and the column names (a lone [x] is ambiguous otherwise)."""

    rule_id = "MAINT_UNQUALIFIED_COLUMN"
    category = "maintainability"
    severity = "low"
    title = "Column reference without its table"

    def evaluate(self, tokens, function_db, context=None) -> List[AnalysisIssue]:
        schema = getattr(context, "schema", None)
        if schema is None or not (schema.measure_names and schema.column_names):
            return []
        for k, t in enumerate(tokens):
            if t.type != TokenType.COLUMN_REF or t.value.startswith("[@"):
                continue
            prev = tokens[k - 1] if k else None
            if prev is not None and prev.type == TokenType.IDENTIFIER:
                continue
            if schema.classify_ref(t.value) == "column":
                return [
                    self._make_issue(
                        f"{t.value} is a column but is written without its table "
                        "(SQLBI style: always 'Table'[Column]).",
                        "Qualify it: 'Table'[Column].",
                        location=t.value,
                        match_text=t.value,
                        line=t.line,
                    )
                ]
        return []


class QualifiedMeasureRule(PythonRule):
    """Measure reference written with a table - AP-40 (never qualify measures; needs a schema)."""

    rule_id = "MAINT_QUALIFIED_MEASURE"
    category = "maintainability"
    severity = "low"
    title = "Measure reference qualified with a table"

    def evaluate(self, tokens, function_db, context=None) -> List[AnalysisIssue]:
        schema = getattr(context, "schema", None)
        if schema is None or not (schema.measure_names and schema.column_names):
            return []
        for k, t in enumerate(tokens):
            if t.type == TokenType.QUALIFIED_REF:
                name = column_refs([t])[0][2]
                shown = t.value
            elif (
                t.type == TokenType.COLUMN_REF
                and k
                and tokens[k - 1].type == TokenType.IDENTIFIER
                and not t.value.startswith("[@")
            ):
                name = column_refs([tokens[k - 1], t])[0][2]
                shown = tokens[k - 1].value + t.value
            else:
                continue
            tb = column_refs([t] if t.type == TokenType.QUALIFIED_REF else [tokens[k - 1], t])[0][1]
            owned = schema.table_columns.get(tb)
            if not owned or name in owned:
                continue  # unknown table columns, or it really is a column of that table
            if schema.classify_ref("[" + name + "]") == "measure":
                return [
                    self._make_issue(
                        f"{shown} qualifies a measure with a table (SQLBI style: never qualify "
                        "measures; newer compatibility levels reject it).",
                        "Write the bare measure reference: [Measure].",
                        location=shown,
                        match_text=t.value,
                        line=t.line,
                    )
                ]
        return []


class CalendarAutoRule(PythonRule):
    """CALENDARAUTO in a calculated date table - AP-43."""

    rule_id = "PERF_CALENDARAUTO"
    category = "performance"
    severity = "medium"
    title = "Calculated date table via CALENDARAUTO"

    def evaluate(self, tokens, function_db, context=None) -> List[AnalysisIssue]:
        hits = self._find_functions(tokens, {"CALENDARAUTO"})
        if not hits:
            return []
        return [
            self._make_issue(
                "CALENDARAUTO scans every datetime column in the model, hidden ones included: a "
                "stray 1900 date of birth explodes the table to 100+ years, and calculated tables "
                "recompute on every full refresh (canon AP-43).",
                "Build the date table in Power Query (or use a Calendar object) and mark it as a "
                "date table.",
                line=tokens[hits[0]].line,
            )
        ]


class CalcItemOverrideRule(PythonRule):
    """``CALCULATE([M], 'TI'[Item])`` against a calculation-group column - AP-44 (schema)."""

    rule_id = "PY_CALC_ITEM_OVERRIDE"
    category = "correctness"
    severity = "medium"
    title = "Measure hard-wires a calculation-group item"

    def evaluate(self, tokens, function_db, context=None) -> List[AnalysisIssue]:
        issues: List[AnalysisIssue] = []
        schema = getattr(context, "schema", None)
        if schema is None or not schema.calculation_group_tables:
            return issues
        for idx in self._find_functions(tokens, _CALC):
            for fi, (a, b) in enumerate(arg_slices(tokens, idx)):
                if fi == 0:
                    continue
                for k in range(a, b):
                    tb = ref_table(tokens[k], tokens[k - 1] if k > a else None)
                    if tb in schema.calculation_group_tables:
                        issues.append(
                            self._make_issue(
                                f"CALCULATE filters the calculation group '{tb}' directly. This "
                                "hard-wires the application order and overrides the precedence "
                                "design of the calculation-group set (canon AP-44).",
                                "Select the item through the report (slicer / visual filter) or "
                                "build the logic into a dedicated measure that does not depend "
                                "on the group's precedence.",
                                line=tokens[k].line,
                            )
                        )
                        return issues
        return issues


class UdfExprBareColumnRule(PythonRule):
    """A bare column passed to a UDF ``expr`` parameter from inside an iterator - AP-48.

    An ``expr`` parameter is re-evaluated at each use INSIDE the function body, outside the
    caller's row context, so a column reference fails at runtime ("A single value for column
    ... cannot be determined"). Needs the UDF signatures (``ModelSchema.udf_signatures``)."""

    rule_id = "CORR_UDF_EXPR_BARE_COLUMN"
    category = "correctness"
    severity = "high"
    title = "Bare column passed to a UDF expr parameter inside an iterator"

    def evaluate(self, tokens, function_db, context=None) -> List[AnalysisIssue]:
        issues: List[AnalysisIssue] = []
        schema = getattr(context, "schema", None)
        sigs = getattr(schema, "udf_signatures", None) if schema is not None else None
        if not sigs:
            return issues
        frames = None
        for k, t in enumerate(tokens):
            if t.type != TokenType.IDENTIFIER or k + 1 >= len(tokens):
                continue
            if tokens[k + 1].type != TokenType.PAREN_OPEN:
                continue
            modes = sigs.get(t.value.upper())
            if not modes:
                continue
            if frames is None:
                frames = call_frames(tokens)
            if not any(fr.name in _ROW_CONTEXT_FNS and fr.arg >= 1 for fr in frames[k]):
                continue
            for ai, (a, b) in enumerate(arg_slices(tokens, k)):
                if ai >= len(modes) or modes[ai] != "expr":
                    continue
                if column_refs(tokens[a:b]) and any(col[1] for col in column_refs(tokens[a:b])):
                    issues.append(
                        self._make_issue(
                            f"{t.value}(...) receives a column reference in an `expr` parameter "
                            "from inside an iterator. The parameter is re-evaluated inside the "
                            "function body, outside this row context, so it fails at runtime "
                            "(canon AP-48).",
                            "Capture the column in a VAR first: SUMX(_Set, VAR _e = T[Col] "
                            f"RETURN {t.value}({{ _e }})).",
                            location=f"{t.value}(...)",
                            match_text=t.value,
                            line=t.line,
                        )
                    )
                    break
        return issues


class EvaluateAndLogRule(PythonRule):
    """EVALUATEANDLOG in a model measure - Microsoft's guideline: debugging only."""

    rule_id = "MAINT_EVALUATEANDLOG"
    category = "maintainability"
    severity = "medium"
    title = "EVALUATEANDLOG left in an expression"

    def evaluate(self, tokens, function_db, context=None) -> List[AnalysisIssue]:
        hits = self._find_functions(tokens, {"EVALUATEANDLOG"})
        if not hits:
            return []
        return [
            self._make_issue(
                "EVALUATEANDLOG is a debugging function (it raises a profiler event per "
                "evaluation); Microsoft's guidance is never to ship it in a production measure.",
                "Remove it once the intermediate value has been inspected.",
                line=tokens[hits[0]].line,
            )
        ]


class FormatStringDoubleEvalRule(PythonRule):
    """A measure / SELECTEDMEASURE / FORMAT inside a FORMAT-STRING expression - AP-07.

    A dynamic format string is evaluated in addition to the value, so every measure it reads is
    computed twice (once for the value, once for the format). Only meaningful for the
    ``format_string`` / ``calc_item_format_string`` objects of ``lint_model``."""

    rule_id = "PERF_FORMAT_STRING_DOUBLE_EVAL"
    category = "performance"
    severity = "medium"
    title = "Format-string expression evaluates a measure a second time"

    _KINDS = ("format_string", "calc_item_format_string")

    def evaluate(self, tokens, function_db, context=None) -> List[AnalysisIssue]:
        if getattr(context, "object_kind", None) not in self._KINDS:
            return []
        for k, t in enumerate(tokens):
            hit = None
            if t.type == TokenType.FUNCTION and t.value.upper() in ("SELECTEDMEASURE", "FORMAT"):
                hit = t.value.upper() + "()"
            elif (
                t.type == TokenType.COLUMN_REF
                and not t.value.startswith("[@")
                and not (k and tokens[k - 1].type == TokenType.IDENTIFIER)
            ):
                schema = getattr(context, "schema", None)
                if schema is None or schema.classify_ref(t.value) != "column":
                    hit = t.value
            if hit is None:
                continue
            return [
                self._make_issue(
                    f"{hit} inside a format-string expression forces the measure to be evaluated "
                    "twice (once for the value, once for the format) - canon AP-07.",
                    "Use a constant format string, or a dynamic format string that reads a cheap "
                    "selector (SELECTEDMEASUREFORMATSTRING(), a slicer value) instead of the "
                    "measure itself.",
                    location=hit,
                    match_text=hit,
                    line=t.line,
                )
            ]
        return []


class RelatedInCalcColumnRule(PythonRule):
    """RELATED in a calculated column - AP-41 (poor compression, full recompute on refresh)."""

    rule_id = "PERF_RELATED_IN_CALC_COLUMN"
    category = "performance"
    severity = "low"
    title = "Calculated column built with RELATED"

    def evaluate(self, tokens, function_db, context=None) -> List[AnalysisIssue]:
        if getattr(context, "object_kind", None) != "calc_column":
            return []
        hits = self._find_functions(tokens, {"RELATED"})
        if not hits:
            return []
        return [
            self._make_issue(
                "A calculated column using RELATED is processed after native compression and "
                "cannot influence segment order; it compresses worse than a source column and "
                "is recomputed over the entire table on every refresh (canon AP-41).",
                "Do the join in Power Query / the source so the column is value-encoded.",
                line=tokens[hits[0]].line,
            )
        ]
