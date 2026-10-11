"""Structured explanation of a DAX measure (2026-10-03 deep review, F-33).

``05_DAX_Intelligence explain`` returns STRUCTURE, never prose: the agent narrates from it.
Everything here is lexical (the repo lexer) plus the optional model facts the caller passes
(``ModelSchema``, calculation groups, roles) - no connection, no CLR.

Sections
- ``call_tree``: every call with its argument roles (CALCULATE: expression / filter modifier;
  iterators: table / per-row expression; IF / SWITCH / DIVIDE: condition / branches ...),
  the references each argument touches and the calls nested in it;
- ``filter_modifiers``: the filter arguments of every CALCULATE / CALCULATETABLE in source
  order, classified (ALL / REMOVEFILTERS / KEEPFILTERS / USERELATIONSHIP / CROSSFILTER / TREATAS /
  boolean predicate / table filter / time intelligence);
- ``context_transitions``: explicit CALCULATE and implicit measure-reference transitions that
  sit inside a row context, and which iterator provides the row context;
- ``iterators``: each iterator with its row source and the references evaluated per row;
- ``variables``: VAR definitions, what each depends on, where each is used, ``unused`` and the
  eager-evaluation note when every use sits in a conditional branch;
- ``calculation_groups`` (SELECTEDMEASURE family + the model's groups), ``rls``,
  ``dependencies`` (measures, columns, tables, UDF calls) and a ``summary``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from core.codemods import dax_text
from core.dax.analyzer.structure import CONDITIONAL_FROM_ARG, is_conditional_arg
from core.dax.tokenizer import DaxLexer, Token, TokenType

logger = logging.getLogger(__name__)

_FILTER_MODIFIER_FUNCS = {
    "ALL": "removes the filters on the listed table / columns",
    "ALLEXCEPT": "removes every filter on the table except the listed columns",
    "ALLSELECTED": "replaces the filter with the outer (visual / slicer) selection",
    "ALLNOBLANKROW": "removes the filters, ignoring the blank row",
    "REMOVEFILTERS": "removes the filters on the listed table / columns",
    "KEEPFILTERS": "adds the filter without overriding the existing one (intersection)",
    "USERELATIONSHIP": "activates the named relationship for this CALCULATE only",
    "CROSSFILTER": "changes the cross-filter direction of a relationship for this CALCULATE only",
    "TREATAS": "applies the values as a filter on other columns (virtual relationship)",
}
_TIME_INTELLIGENCE = frozenset(
    {
        "DATESYTD",
        "DATESMTD",
        "DATESQTD",
        "DATESBETWEEN",
        "DATESINPERIOD",
        "SAMEPERIODLASTYEAR",
        "PARALLELPERIOD",
        "PREVIOUSDAY",
        "PREVIOUSMONTH",
        "PREVIOUSQUARTER",
        "PREVIOUSYEAR",
        "NEXTDAY",
        "NEXTMONTH",
        "NEXTQUARTER",
        "NEXTYEAR",
        "TOTALYTD",
        "TOTALMTD",
        "TOTALQTD",
        "STARTOFMONTH",
        "ENDOFMONTH",
        "STARTOFYEAR",
        "ENDOFYEAR",
        "FIRSTDATE",
        "LASTDATE",
        "DATEADD",
        "DATESWTD",
        "PREVIOUSWEEK",
        "NEXTWEEK",
    }
)
_TABLE_FUNCS = frozenset(
    {
        "FILTER",
        "ALL",
        "ALLSELECTED",
        "ALLEXCEPT",
        "VALUES",
        "DISTINCT",
        "SUMMARIZE",
        "SUMMARIZECOLUMNS",
        "ADDCOLUMNS",
        "SELECTCOLUMNS",
        "TOPN",
        "CALCULATETABLE",
        "GENERATE",
        "UNION",
        "INTERSECT",
        "EXCEPT",
        "CROSSJOIN",
        "NATURALINNERJOIN",
        "TREATAS",
        "DATESYTD",
        "DATESBETWEEN",
        "ROW",
        "GROUPBY",
        "RELATEDTABLE",
        "KEEPFILTERS",
        "REMOVEFILTERS",
        "CALENDAR",
        "CALENDARAUTO",
        "GENERATESERIES",
        "DATATABLE",
        "SAMPLE",
        "DATESMTD",
        "DATESQTD",
        "DATESINPERIOD",
        "FILTERS",
        "ALLNOBLANKROW",
        "ADDMISSINGITEMS",
        "SUBSTITUTEWITHINDEX",
        "NATURALLEFTOUTERJOIN",
        "TOPNSKIP",
        # TOCSV is NOT here: it returns text, so a VAR holding it is a scalar
    }
)
_CALC_GROUP_FUNCS = frozenset(
    {
        "SELECTEDMEASURE",
        "SELECTEDMEASURENAME",
        "SELECTEDMEASUREFORMATSTRING",
        "ISSELECTEDMEASURE",
    }
)
_COMPARISON = frozenset({"=", "==", "<>", "<", ">", "<=", ">=", "&&", "||"})

_ARG_ROLES = {
    "CALCULATE": ["expression"],
    "CALCULATETABLE": ["table"],
    "FILTER": ["table", "predicate (per row)"],
    "IF": ["condition", "then", "else"],
    "IFERROR": ["value", "fallback"],
    "COALESCE": [],
    "DIVIDE": ["numerator", "denominator", "alternate result"],
    "SWITCH": ["switch expression"],
    "ADDCOLUMNS": ["table"],
    "SELECTCOLUMNS": ["table"],
    "TOPN": ["n", "table", "order by"],
    "RANKX": ["table", "expression (per row)"],
    "CONCATENATEX": ["table", "expression (per row)", "delimiter"],
    "SUMMARIZECOLUMNS": [],
    "SUMMARIZE": ["table"],
    "TREATAS": ["values", "target columns"],
    "USERELATIONSHIP": ["column 1", "column 2"],
    "CROSSFILTER": ["column 1", "column 2", "direction"],
    "KEEPFILTERS": ["filter"],
}
_REPEAT_ROLE = {
    "CALCULATE": "filter modifier",
    "CALCULATETABLE": "filter modifier",
    "SWITCH": "value / result pairs, last = default",
    "ADDCOLUMNS": "name / expression (per row) pairs",
    "SELECTCOLUMNS": "name / expression (per row) pairs",
    "SUMMARIZECOLUMNS": "group columns / name-expression pairs / filters",
    "TOPN": "order by / direction",
}


def _arg_role(fn: str, idx: int, nargs: int) -> str:
    roles = _ARG_ROLES.get(fn)
    if roles is not None and idx < len(roles):
        return roles[idx]
    if fn in _REPEAT_ROLE:
        return _REPEAT_ROLE[fn]
    if dax_text.is_row_context_function(fn):
        return ["table (row source)", "expression (per row)"][idx] if idx < 2 else "argument"
    return f"argument {idx + 1}"


def _kind_of(fn: str) -> str:
    if fn in ("CALCULATE", "CALCULATETABLE"):
        return "context_transition"
    if fn in _FILTER_MODIFIER_FUNCS:
        return "filter_modifier"
    if fn in _CALC_GROUP_FUNCS:
        return "calculation_group"
    if fn in CONDITIONAL_FROM_ARG:
        return "conditional"
    if fn in _TIME_INTELLIGENCE:
        return "time_intelligence"
    if dax_text.is_row_context_function(fn):
        return "iterator"
    return "function"


def _snippet(text: str, n: int = 140) -> str:
    t = " ".join(text.split())
    return t if len(t) <= n else t[: n - 1] + "…"


class _Ctx:
    def __init__(self, expression: str, code: List[Token], schema: Any) -> None:
        self.expression = expression
        self.code = code
        self.pmap = DaxLexer().build_paren_map(code)
        self.schema = schema
        self.measure_names: Set[str] = set(getattr(schema, "measure_names", None) or ())
        self.table_names: Set[str] = set(getattr(schema, "table_names", None) or ())
        self.next_id = 0
        self.nodes: List[Dict[str, Any]] = []  # every call, in source order
        self.calls_by_open: Dict[int, Dict[str, Any]] = {}

    def tid(self) -> int:
        self.next_id += 1
        return self.next_id

    def text(self, lo: int, hi: int) -> str:
        if hi <= lo:
            return ""
        return self.expression[self.code[lo].start : self.code[hi - 1].end]


def _split_args(ctx: _Ctx, open_idx: int, close_idx: int) -> List[Tuple[int, int]]:
    spans: List[Tuple[int, int]] = []
    depth = 0
    start = open_idx + 1
    for i in range(open_idx + 1, close_idx):
        tt = ctx.code[i].type
        if tt in (TokenType.PAREN_OPEN, TokenType.BRACE_OPEN):
            depth += 1
        elif tt in (TokenType.PAREN_CLOSE, TokenType.BRACE_CLOSE):
            depth -= 1
        elif tt == TokenType.COMMA and depth == 0:
            spans.append((start, i))
            start = i + 1
    if close_idx > open_idx + 1 or spans:
        spans.append((start, close_idx))
    return spans


def _refs(
    ctx: _Ctx, lo: int, hi: int, skip: Sequence[Tuple[int, int]] = ()
) -> List[Dict[str, str]]:
    """References in tokens ``lo..hi``; ``skip`` = token ranges of nested calls (those are
    reported by their own node)."""
    from core.dax.dax_names import unbracket

    out: List[Dict[str, str]] = []
    i = lo
    skips = sorted(skip)
    while i < hi:
        hit = next((e for s0, e in skips if s0 <= i < e), None)
        if hit is not None:
            i = hit
            continue
        t = ctx.code[i]
        nxt = ctx.code[i + 1] if i + 1 < hi else None
        if (
            t.type == TokenType.IDENTIFIER
            and nxt is not None
            and nxt.type == TokenType.COLUMN_REF
            and nxt.start == t.end
        ):
            out.append({"ref": ctx.expression[t.start : nxt.end], "kind": "column"})
            i += 2
            continue
        if t.type == TokenType.QUALIFIED_REF:
            out.append({"ref": t.value, "kind": "column"})
        elif t.type == TokenType.COLUMN_REF:
            name = dax_text.norm_name(unbracket(t.value))
            if ctx.measure_names and name in ctx.measure_names:
                kind = "measure"
            elif ctx.measure_names:
                kind = "column_or_unknown"
            else:
                kind = "measure_or_column"
            out.append({"ref": t.value, "kind": kind})
        i += 1
    return out


def _is_call(ctx: _Ctx, i: int) -> Optional[int]:
    """Index of the ``(`` when token ``i`` is a call name."""
    t = ctx.code[i]
    if t.type not in (TokenType.FUNCTION, TokenType.IDENTIFIER, TokenType.KEYWORD):
        return None
    if t.type == TokenType.KEYWORD and t.value.upper() in ("VAR", "RETURN", "IN"):
        return None
    j = i + 1
    if j < len(ctx.code) and ctx.code[j].type == TokenType.PAREN_OPEN and j in ctx.pmap:
        return j
    return None


def _is_builtin(fn: str) -> bool:
    from core.dax.knowledge.function_db import DaxFunctionDatabase

    return fn in _CALC_GROUP_FUNCS or DaxFunctionDatabase.get().is_function(fn)


def _build(
    ctx: _Ctx, lo: int, hi: int, parent: Optional[Dict[str, Any]], arg_idx: int
) -> List[Dict[str, Any]]:
    """Call nodes directly under tokens ``lo..hi`` (each node's own args are built recursively)."""
    out: List[Dict[str, Any]] = []
    i = lo
    while i < hi:
        open_idx = _is_call(ctx, i)
        if open_idx is None:
            i += 1
            continue
        close_idx = ctx.pmap[open_idx]
        t = ctx.code[i]
        fn = t.value.upper()
        node: Dict[str, Any] = {
            "id": ctx.tid(),
            # built-ins are shown upper-case; a UDF / other non-builtin keeps the casing as written
            "function": fn if _is_builtin(fn) else t.value,
            "kind": _kind_of(fn),
            "line": t.line,
            "args": [],
            "_open": open_idx,
            "_lo": i,
            "_hi": close_idx + 1,
            "_parent": parent,
            "_arg_idx": arg_idx,
        }
        ctx.nodes.append(node)
        ctx.calls_by_open[open_idx] = node
        spans = _split_args(ctx, open_idx, close_idx)
        for k, (a, b) in enumerate(spans):
            arg: Dict[str, Any] = {
                "index": k,
                "role": _arg_role(fn, k, len(spans)),
                "text": _snippet(ctx.text(a, b)),
                "_lo": a,
                "_hi": b,
            }
            arg["calls"] = _build(ctx, a, b, node, k)
            arg["refs"] = _refs(ctx, a, b, [(c["_lo"], c["_hi"]) for c in arg["calls"]])
            node["args"].append(arg)
        out.append(node)
        i = close_idx + 1
    return out


def _walk(nodes: Sequence[Dict[str, Any]]):
    for n in nodes:
        yield n
        for a in n["args"]:
            yield from _walk(a["calls"])


def _public(nodes: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for n in nodes:
        out.append(
            {
                "id": n["id"],
                "function": n["function"],
                "kind": n["kind"],
                "line": n["line"],
                "args": [
                    {
                        "index": a["index"],
                        "role": a["role"],
                        "text": a["text"],
                        **({"refs": a["refs"]} if a["refs"] else {}),
                        **({"calls": _public(a["calls"])} if a["calls"] else {}),
                    }
                    for a in n["args"]
                ],
            }
        )
    return out


def _depth(nodes: Sequence[Dict[str, Any]]) -> int:
    best = 0
    for n in nodes:
        sub = max((_depth(a["calls"]) for a in n["args"]), default=0)
        best = max(best, 1 + sub)
    return best


# ---------------------------------------------------------------------------
# Row context / frames
# ---------------------------------------------------------------------------


def _row_context_of(node: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The nearest enclosing iterator whose PER-ROW argument contains *node* (None at the top)."""
    cur = node
    while cur is not None:
        par = cur["_parent"]
        if par is None:
            return None
        idx = cur["_arg_idx"]
        fn = par["function"]
        if par["kind"] == "iterator" or fn in ("FILTER", "ADDCOLUMNS", "SELECTCOLUMNS", "TOPN"):
            if idx >= 1:
                return par
        cur = par
    return None


def _row_context_of_ref(ctx: _Ctx, tok_idx: int) -> Optional[Dict[str, Any]]:
    best: Optional[Dict[str, Any]] = None
    for n in ctx.nodes:
        if not (n["_open"] < tok_idx < n["_hi"] - 1):
            continue
        for a in n["args"]:
            if a["_lo"] <= tok_idx < a["_hi"]:
                if (
                    n["kind"] == "iterator"
                    or n["function"] in ("FILTER", "ADDCOLUMNS", "SELECTCOLUMNS", "TOPN")
                ) and a["index"] >= 1:
                    if best is None or n["_open"] > best["_open"]:
                        best = n
    return best


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def _classify_filter_arg(ctx: _Ctx, arg: Dict[str, Any]) -> Dict[str, Any]:
    first: Dict[str, Any] = arg["calls"][0] if arg["calls"] else {}
    lo, hi = arg["_lo"], arg["_hi"]
    toks = ctx.code[lo:hi]
    starts_with_call = bool(first) and first["_lo"] == lo
    contains = sorted({n["function"] for n in _walk(arg["calls"])})
    cols = [r["ref"] for r in arg["refs"] if r["kind"] == "column"]
    for n in _walk(arg["calls"]):
        for a in n["args"]:
            cols += [r["ref"] for r in a["refs"] if r["kind"] == "column"]
    cols = list(dict.fromkeys(cols))
    if starts_with_call and first["function"] in _FILTER_MODIFIER_FUNCS:
        fn = first["function"]
        return {
            "function": fn,
            "effect": _FILTER_MODIFIER_FUNCS[fn],
            "columns": cols,
            "contains": contains,
        }
    if starts_with_call and first["function"] == "FILTER":
        removes = [f for f in contains if f in ("ALL", "ALLSELECTED", "ALLEXCEPT", "REMOVEFILTERS")]
        eff = "table filter: keeps the rows of the first argument for which the predicate is true"
        if removes:
            eff += f" (its table argument first clears filters with {', '.join(removes)})"
        return {"function": "FILTER", "effect": eff, "columns": cols, "contains": contains}
    if starts_with_call and first["function"] in _TIME_INTELLIGENCE:
        return {
            "function": first["function"],
            "effect": "time-intelligence table filter on the date column",
            "columns": cols,
            "contains": contains,
        }
    if any(t.type == TokenType.OPERATOR and t.value in _COMPARISON for t in toks):
        return {
            "function": None,
            "effect": "boolean filter on the referenced column(s) "
            "(shorthand for FILTER(ALL(column), predicate): overrides the existing filter on them)",
            "columns": cols,
            "contains": contains,
        }
    if starts_with_call:
        return {
            "function": first["function"],
            "effect": "table expression used as a filter",
            "columns": cols,
            "contains": contains,
        }
    return {
        "function": None,
        "effect": "other filter argument",
        "columns": cols,
        "contains": contains,
    }


def _filter_modifiers(ctx: _Ctx) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for n in ctx.nodes:
        if n["function"] not in ("CALCULATE", "CALCULATETABLE"):
            continue
        for a in n["args"][1:]:
            info = _classify_filter_arg(ctx, a)
            out.append(
                {
                    "order": len(out) + 1,
                    "call_id": n["id"],
                    "in_function": n["function"],
                    "line": n["line"],
                    "arg_index": a["index"],
                    "text": a["text"],
                    **info,
                }
            )
    return out


def _transitions(ctx: _Ctx) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for n in ctx.nodes:
        if n["function"] in ("CALCULATE", "CALCULATETABLE"):
            rc = _row_context_of(n)
            out.append(
                {
                    "kind": "explicit_calculate",
                    "function": n["function"],
                    "line": n["line"],
                    "call_id": n["id"],
                    "in_row_context": rc is not None,
                    **(
                        {"row_context_from": rc["function"], "row_context_call_id": rc["id"]}
                        if rc
                        else {}
                    ),
                    "effect": (
                        "converts the current row into filters, then applies the filter arguments"
                        if rc
                        else "no row context here: only the filter arguments change the context"
                    ),
                }
            )
    # a bare [Measure] reference inside a row context has an implicit CALCULATE
    from core.dax.dax_names import unbracket

    for i, t in enumerate(ctx.code):
        if t.type != TokenType.COLUMN_REF:
            continue
        prev = ctx.code[i - 1] if i else None
        if prev is not None and prev.type == TokenType.IDENTIFIER and prev.end == t.start:
            continue  # Table[Column]
        name = dax_text.norm_name(unbracket(t.value))
        if ctx.measure_names and name not in ctx.measure_names:
            continue
        rc = _row_context_of_ref(ctx, i)
        if rc is None:
            continue
        out.append(
            {
                "kind": "implicit_measure_reference",
                "ref": t.value,
                "line": t.line,
                "in_row_context": True,
                "row_context_from": rc["function"],
                "row_context_call_id": rc["id"],
                "effect": "a measure reference inside a row context is wrapped in an implicit "
                "CALCULATE (context transition: the current row filters the measure)",
            }
        )
    return out


def _iterators(ctx: _Ctx, transitions: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for n in ctx.nodes:
        if n["kind"] != "iterator" and n["function"] not in (
            "FILTER",
            "ADDCOLUMNS",
            "SELECTCOLUMNS",
        ):
            continue
        per_row = n["args"][1:]
        refs = [r for a in per_row for r in a["refs"]]
        out.append(
            {
                "call_id": n["id"],
                "function": n["function"],
                "line": n["line"],
                "row_source": n["args"][0]["text"] if n["args"] else "",
                "per_row_expression": [a["text"] for a in per_row],
                "row_context_columns": [r["ref"] for r in refs if r["kind"] == "column"],
                "measures_evaluated_per_row": [
                    t["ref"]
                    for t in transitions
                    if t["kind"] == "implicit_measure_reference"
                    and t["row_context_call_id"] == n["id"]
                ],
                "nested_in_row_context": _row_context_of(n) is not None,
            }
        )
    return out


def _depends_on_names(ctx: _Ctx, lo: int, hi: int, var_names: Set[str]) -> List[str]:
    found = []
    for i in range(lo, hi):
        t = ctx.code[i]
        if t.type == TokenType.IDENTIFIER and t.value.upper() in var_names:
            found.append(t.value)
    return list(dict.fromkeys(found))


_TABLE_ARG_ROLES = frozenset({"table", "table (row source)"})


def _is_table_arg(fn: Optional[str], idx: int) -> bool:
    """True when argument ``idx`` of ``fn`` must be a table (an iterator's row source, FILTER /
    ADDCOLUMNS / COUNTROWS ... first argument, TOPN's second)."""
    if not fn:
        return False
    up = str(fn).upper()
    if up in ("COUNTROWS", "ISEMPTY") and idx == 0:
        return True
    return _arg_role(up, idx, 0) in _TABLE_ARG_ROLES


def _variables(ctx: _Ctx) -> List[Dict[str, Any]]:
    code = ctx.code
    n = len(code)
    var_names = {
        code[i + 1].value.upper()
        for i, t in enumerate(code[:-1])
        if t.type == TokenType.KEYWORD and t.value.upper() == "VAR"
    }
    if not var_names:
        return []
    from core.dax.analyzer.structure import frame_stacks

    stacks = frame_stacks(code)
    # frame names come upper-cased; show a UDF the way the call tree does (as written)
    case_of = {str(nd["function"]).upper(): str(nd["function"]) for nd in ctx.nodes}
    out: List[Dict[str, Any]] = []
    for i, t in enumerate(code):
        if not (t.type == TokenType.KEYWORD and t.value.upper() == "VAR") or i + 2 >= n:
            continue
        name_tok = code[i + 1]
        if not (code[i + 2].type == TokenType.OPERATOR and code[i + 2].value == "="):
            continue
        start = i + 3
        depth = 0
        j = start
        while j < n:
            tj = code[j]
            if tj.type in (TokenType.PAREN_OPEN, TokenType.BRACE_OPEN):
                depth += 1
            elif tj.type in (TokenType.PAREN_CLOSE, TokenType.BRACE_CLOSE):
                if depth == 0:
                    break
                depth -= 1
            elif (
                depth == 0
                and tj.type == TokenType.KEYWORD
                and tj.value.upper() in ("VAR", "RETURN")
            ):
                break
            j += 1
        end = j
        up = name_tok.value.upper()
        first = code[start] if start < end else None
        vtype = (
            "table"
            if first is not None
            and (
                (
                    first.type in (TokenType.FUNCTION, TokenType.IDENTIFIER)
                    and first.value.upper() in _TABLE_FUNCS
                )
                or first.type == TokenType.BRACE_OPEN
            )
            else "scalar"
        )
        uses: List[Dict[str, Any]] = []
        for k in range(end, n):
            tk = code[k]
            if tk.type == TokenType.IDENTIFIER and tk.value.upper() == up:
                if (
                    k
                    and code[k - 1].type == TokenType.KEYWORD
                    and code[k - 1].value.upper() == "VAR"
                ):
                    continue  # a redeclaration, not a use
                frames = stacks[k]
                conditional = any(is_conditional_arg(fn, arg) for fn, arg in frames)
                if vtype == "scalar" and frames and _is_table_arg(*frames[-1]):
                    # e.g. VAR w = MyUdf ( x ) used as MINX ( w, ... ): the definition alone
                    # cannot tell (a UDF / unknown function), the use as a row source can
                    vtype = "table"
                uses.append(
                    {
                        "line": tk.line,
                        "in": [
                            f"{case_of.get(str(fn), fn) if fn else '('} arg {arg + 1}"
                            for fn, arg in frames
                        ],
                        "conditional": conditional,
                        "_k": k,
                    }
                )
        # which later VAR definition (if any) contains each use
        spans = []
        for m, tm in enumerate(code):
            if tm.type == TokenType.KEYWORD and tm.value.upper() == "VAR" and m + 3 < n and m > i:
                d2 = 0
                q = m + 3
                while q < n:
                    tq = code[q]
                    if tq.type in (TokenType.PAREN_OPEN, TokenType.BRACE_OPEN):
                        d2 += 1
                    elif tq.type in (TokenType.PAREN_CLOSE, TokenType.BRACE_CLOSE):
                        if d2 == 0:
                            break
                        d2 -= 1
                    elif (
                        d2 == 0
                        and tq.type == TokenType.KEYWORD
                        and tq.value.upper() in ("VAR", "RETURN")
                    ):
                        break
                    q += 1
                spans.append((code[m + 1].value, m + 3, q))
        direct_conditional = []
        for u in uses:
            owner = next((nm for nm, a, b in spans if a <= u["_k"] < b), None)
            u["used_by"] = f"VAR {owner}" if owner else "RETURN / expression"
            del u["_k"]
            if owner is None:
                direct_conditional.append(u["conditional"])
        entry: Dict[str, Any] = {
            "name": name_tok.value,
            "line": name_tok.line,
            "type": vtype,
            "definition": _snippet(ctx.text(start, end), 300),
            "depends_on_variables": _depends_on_names(ctx, start, end, var_names - {up}),
            "depends_on_refs": [r["ref"] for r in _refs(ctx, start, end)],
            "used_in": uses,
            "unused": not uses,
        }
        if direct_conditional and all(direct_conditional):
            entry["eager_evaluation_note"] = (
                "every direct use sits in a conditional branch (IF / SWITCH / IFERROR / COALESCE / "
                "DIVIDE alternate), but a VAR is evaluated eagerly where it is declared: the "
                "branch's short-circuit and any error guard do not protect it"
            )
        out.append(entry)
    return out


def _calc_groups(ctx: _Ctx, calc_groups: Optional[Sequence[Dict[str, Any]]]) -> Dict[str, Any]:
    uses = [
        {"function": n["function"], "line": n["line"]}
        for n in ctx.nodes
        if n["function"] in _CALC_GROUP_FUNCS
    ]
    out: Dict[str, Any] = {"selectedmeasure_family_used": uses}
    if uses:
        out["note"] = (
            "this text reads SELECTEDMEASURE*: it is a calculation item / format-string "
            "expression (the measure it transforms is chosen by the caller), not a plain measure"
        )
    groups = list(calc_groups or [])
    if groups:
        out["model_calculation_groups"] = groups
        out["model_note"] = (
            "calculation items transform MEASURE REFERENCES: when a calculation-group column "
            "is filtered (slicer / visual / filter argument), the measure and every measure it "
            "references go through the selected item(s), in precedence order"
        )
    return out


def _rls(ctx: _Ctx, roles: Optional[Sequence[Dict[str, Any]]], tables: Set[str]) -> Dict[str, Any]:
    rl = list(roles or [])
    if not rl:
        return {"roles_defined": False}
    secured: Dict[str, List[str]] = {}
    for r in rl:
        for tname in r.get("tables") or []:
            secured.setdefault(str(tname).lower(), []).append(str(r.get("name", "")))
    touched = sorted(t for t in tables if t in secured)
    return {
        "roles_defined": True,
        "roles": [r.get("name") for r in rl],
        "secured_tables_read_directly": touched,
        "roles_filtering_those_tables": {t: secured[t] for t in touched},
        "note": "RLS filters apply to every query by a role member and propagate along "
        "relationships to other tables; a measure that reads no secured table directly can still "
        "be filtered through a relationship. Test with the role (04_Query_Operations test_rls).",
    }


def explain_expression(
    expression: str,
    *,
    schema: Any = None,
    calc_groups: Optional[Sequence[Dict[str, Any]]] = None,
    roles: Optional[Sequence[Dict[str, Any]]] = None,
    measure: Optional[str] = None,
    table: Optional[str] = None,
) -> Dict[str, Any]:
    """Structured explanation of *expression* (see the module docstring)."""
    code = dax_text.code_tokens(expression or "")
    ctx = _Ctx(expression or "", code, schema)
    tree = _build(ctx, 0, len(code), None, 0)

    modifiers = _filter_modifiers(ctx)
    transitions = _transitions(ctx)
    iterators = _iterators(ctx, transitions)
    variables = _variables(ctx)

    from core.dax.dax_names import unbracket

    measures: List[str] = []
    columns: List[str] = []
    unresolved: List[str] = []
    table_case: Dict[str, str] = {}  # lower-cased name -> first spelling as written
    for r in _refs(ctx, 0, len(code)):
        if r["kind"] == "measure":
            measures.append(r["ref"])
        elif r["kind"] == "column":
            columns.append(r["ref"])
        else:
            unresolved.append(r["ref"])
    for c in columns:
        from core.dax.dax_names import split_qualified

        try:
            tb, _col = split_qualified(c) if c.startswith("'") else (c.split("[", 1)[0], "")
        except Exception:  # noqa: BLE001
            tb = c.split("[", 1)[0]
        _name = tb.strip("'")
        table_case.setdefault(_name.lower(), _name)
    for t in code:
        if t.type == TokenType.TABLE_REF:
            _name = unbracket(t.value).strip("'")
            table_case.setdefault(_name.lower(), _name)
    tables: Set[str] = set(table_case)
    udf_case: Dict[str, str] = {}  # upper-cased name -> first spelling as written
    for n in ctx.nodes:
        if not _is_builtin(n["function"].upper()):
            udf_case.setdefault(n["function"].upper(), n["function"])
    sigs = getattr(schema, "udf_signatures", None) or {}
    udfs = [
        {"function": udf_case[u], **({"parameter_modes": sigs[u]} if u in sigs else {})}
        for u in sorted(udf_case)
    ]

    out: Dict[str, Any] = {
        "measure": ({"name": measure, "table": table} if measure else None),
        "call_tree": _public(tree),
        "filter_modifiers": modifiers,
        "context_transitions": transitions,
        "iterators": iterators,
        "variables": variables,
        "calculation_groups": _calc_groups(ctx, calc_groups),
        "rls": _rls(ctx, roles, tables),
        "dependencies": {
            "measures": list(dict.fromkeys(measures)),
            "columns": list(dict.fromkeys(columns)),
            "tables": [table_case[t] for t in sorted(tables) if t],
            "udf_calls": udfs,
            **({"bare_refs_not_resolved": list(dict.fromkeys(unresolved))} if unresolved else {}),
        },
        "summary": {
            "calls": len(ctx.nodes),
            "max_call_depth": _depth(tree),
            "variables": len(variables),
            "unused_variables": sum(1 for v in variables if v["unused"]),
            "iterators": len(iterators),
            "filter_modifiers": len(modifiers),
            "context_transitions": sum(1 for t in transitions if t["in_row_context"]),
            "explicit_calculates": sum(1 for t in transitions if t["kind"] == "explicit_calculate"),
            "measure_dependencies": len(set(measures)),
        },
    }
    return out
