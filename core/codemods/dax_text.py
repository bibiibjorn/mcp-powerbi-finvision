"""Token-level DAX helpers shared by the 15_DAX_Refactor transforms and the optimizer.

2026-09-22 review E-05/E-07/E-10: every rewrite used to run regexes over raw DAX
text, so string literals, comments, ``]]`` escapes, case-insensitive names and
qualified ``T[M]`` refs were all mishandled. Everything here is driven by the
shared ``DaxLexer`` (``core.dax.dax_reference_parser.get_lexer``):

- ``frame_stacks(code)`` -- the stack of enclosing ``(function, arg_index)``
  frames for every code token;
- ``has_row_context(frames)`` / ``is_hoistable(frames)`` -- the static
  safety predicates the rewrites use (row context / context transition);
- ``measure_ref_sites(expr, name, home_table)`` -- every reference to a measure
  (``[M]`` and ``Home[M]``), escape- and case-aware, with its frame stack;
- ``sanity_check(after, before)`` -- re-lex a rewrite and refuse it when it is
  structurally broken (unbalanced parens, a paren swallowed by a line comment,
  ``, *`` operator after a separator, unterminated literal ...);
- ``iter_scoped_measures(model, args)`` -- the ``measures`` / ``tables`` scope
  filter every transform honours (E-22).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from core.dax.tokenizer import Token, TokenType

Frame = Tuple[str, int]  # (function name upper-case or "" for a grouping paren / "{", arg index)

# Functions that evaluate an argument in a ROW context (or iterate a table and
# evaluate an expression per row). A measure reference inside one of them has
# an implicit context transition, so ``[M]`` != ``(body of M)`` there, a
# ``[M]`` cannot be hoisted into a VAR, and a sub-expression cannot be lifted
# into a measure. Any function whose name ends in ``X`` is treated as one too.
ROW_CONTEXT_FUNCTIONS = frozenset(
    {
        "FILTER",
        "ADDCOLUMNS",
        "SELECTCOLUMNS",
        "GENERATE",
        "GENERATEALL",
        "SUMMARIZE",
        "GROUPBY",
        "TOPN",
        "RANKX",
        "RANK",
        "ROWNUMBER",
        "FIRSTNONBLANK",
        "LASTNONBLANK",
        "FIRSTNONBLANKVALUE",
        "LASTNONBLANKVALUE",
        "CONCATENATEX",
        "COUNTAX",
        "MINX",
        "MAXX",
        "SUMX",
        "AVERAGEX",
        "COUNTX",
        "PRODUCTX",
        "MEDIANX",
        "STDEVX.S",
        "STDEVX.P",
        "VARX.S",
        "VARX.P",
        "PERCENTILEX.INC",
        "PERCENTILEX.EXC",
        "GEOMEANX",
        "ROLLUPADDISSUBTOTAL",
        "SUBSTITUTEWITHINDEX",
        "INDEX",
        "OFFSET",
        "WINDOW",
    }
)

# Functions that change the filter context of their FIRST argument; their other
# arguments are filter arguments (a boolean filter is FILTER(ALL(col), pred) --
# a row context).
FILTER_CONTEXT_FUNCTIONS = frozenset({"CALCULATE", "CALCULATETABLE"})

# Scalar functions that neither iterate nor change the filter context. A
# measure reference inside only these (or plain grouping parens) is evaluated in
# the measure's own filter context, exactly like a reference at depth 0.
SCALAR_SAFE_FUNCTIONS = frozenset(
    {
        "IF",
        "IF.EAGER",
        "SWITCH",
        "DIVIDE",
        "COALESCE",
        "AND",
        "OR",
        "NOT",
        "ABS",
        "ROUND",
        "ROUNDUP",
        "ROUNDDOWN",
        "MROUND",
        "INT",
        "TRUNC",
        "CEILING",
        "FLOOR",
        "SIGN",
        "SQRT",
        "POWER",
        "EXP",
        "LN",
        "LOG",
        "LOG10",
        "MOD",
        "QUOTIENT",
        "MIN",
        "MAX",
        "BLANK",
        "ISBLANK",
        "ISERROR",
        "IFERROR",
        "ISNUMBER",
        "ISTEXT",
        "ISLOGICAL",
        "ISEVEN",
        "ISODD",
        "FORMAT",
        "CONCATENATE",
        "VALUE",
        "CONVERT",
        "CURRENCY",
        "FIXED",
        "TRUE",
        "FALSE",
        "UPPER",
        "LOWER",
        "LEFT",
        "RIGHT",
        "MID",
        "LEN",
        "TRIM",
        "SUBSTITUTE",
        "REPLACE",
        "REPT",
        "SEARCH",
        "FIND",
        "EXACT",
        "CONTAINSSTRING",
        "CONTAINSSTRINGEXACT",
        "UNICHAR",
        "DATE",
        "TIME",
        "YEAR",
        "MONTH",
        "DAY",
    }
)


def get_lexer():
    from core.dax.dax_reference_parser import get_lexer as _gl

    return _gl()


def code_tokens(expr: str) -> List[Token]:
    """Comment/whitespace-free token list of *expr* (shared, function-aware lexer)."""
    return get_lexer().tokenize_code(expr or "")


def norm_name(name: str) -> str:
    """DAX names are case-insensitive; collapse whitespace like the rename helpers."""
    return " ".join((name or "").lower().split())


def is_row_context_function(fn: str) -> bool:
    f = (fn or "").upper()
    if f in ROW_CONTEXT_FUNCTIONS:
        return True
    return len(f) > 1 and f.endswith("X") and f not in SCALAR_SAFE_FUNCTIONS


def frame_stacks(code: Sequence[Token]) -> List[Tuple[Frame, ...]]:
    """For each token in *code*, the tuple of enclosing frames (outermost first).

    A frame is ``(FUNCTION, arg_index)``; ``""`` is a grouping paren and ``"{"``
    a table constructor. The frame of a ``(`` token itself is its OUTER stack.
    (Shared implementation: ``core.dax.analyzer.structure.frame_stacks``.)
    """
    from core.dax.analyzer.structure import frame_stacks as _frame_stacks

    return _frame_stacks(code)


def has_row_context(frames: Sequence[Frame]) -> bool:
    """True when a site under *frames* may be evaluated in a row context or in a
    CALCULATE filter argument -- i.e. where ``[M]`` has a context transition that
    a plain ``(body)`` would not have.

    Conservative: any function that is not known to be scalar-safe counts
    (UDF calls, calc-group helpers, time intelligence, table functions ...).
    """
    for fn, arg in frames:
        if fn == "":
            continue
        if fn in FILTER_CONTEXT_FUNCTIONS:
            if arg == 0:
                continue
            return True
        if fn in SCALAR_SAFE_FUNCTIONS:
            continue
        return True
    return False


def is_hoistable(frames: Sequence[Frame]) -> bool:
    """True when a measure reference under *frames* may be hoisted into a
    top-level VAR without changing its value: only grouping parens and
    scalar-safe functions enclose it (no iterator, no CALCULATE, no time
    intelligence, no UDF / unknown function)."""
    for fn, _arg in frames:
        if fn == "":
            continue
        if fn in SCALAR_SAFE_FUNCTIONS:
            continue
        return False
    return True


def is_unconditional(frames: Sequence[Frame]) -> bool:
    """True when no enclosing function evaluates the site lazily (IF / SWITCH / COALESCE /
    IFERROR branch, DIVIDE alternate result).

    A site in a conditional branch is NOT evaluated whenever the measure is: hoisting its
    reference into a top-level VAR makes the engine evaluate it eagerly - canon core rule 3 /
    AP-10 ("VAR defeats short-circuit", 3,094 vs 484 ms) - and can raise errors the branch's
    guard prevented (2026-10-03 review D-02).
    """
    from core.dax.analyzer.structure import is_conditional_arg

    return not any(is_conditional_arg(fn, arg) for fn, arg in frames)


@dataclass(frozen=True)
class RefSite:
    """One reference to a measure inside an expression."""

    start: int  # char offset of the whole ref (table part included when qualified)
    end: int
    qualified: bool
    frames: Tuple[Frame, ...]


def measure_ref_sites(expr: str, name: str, home_table: Optional[str] = None) -> List[RefSite]:
    """Every reference to measure *name* in *expr*: lone ``[name]`` and, with
    *home_table*, ``'Home'[name]`` / ``Home[name]``. Case-insensitive, ``]]``-
    and ``''``-escape aware, never inside strings or comments."""
    from core.dax.dax_reference_parser import scan_references

    if not isinstance(expr, str) or not expr or not name:
        return []
    code = code_tokens(expr)
    stacks = frame_stacks(code)
    by_start = {t.start: i for i, t in enumerate(code)}
    target = norm_name(name)
    home = norm_name(home_table) if home_table else None
    out: List[RefSite] = []
    for ref in scan_references(expr, code=code):
        if norm_name(ref.name) != target:
            continue
        if ref.kind == "bracket":
            qualified = False
        elif ref.kind == "qualified" and home is not None and norm_name(ref.table) == home:
            qualified = True
        else:
            continue
        idx = by_start.get(ref.start)
        frames = stacks[idx] if idx is not None else ()
        out.append(RefSite(ref.start, ref.end, qualified, frames))
    return out


def splice(expr: str, edits: List[Tuple[int, int, str]]) -> str:
    """Apply non-overlapping ``(start, end, text)`` edits to *expr*."""
    out: List[str] = []
    cursor = 0
    for start, end, text in sorted(edits, key=lambda e: e[0]):
        if start < cursor:
            raise ValueError("overlapping edits")
        out.append(expr[cursor:start])
        out.append(text)
        cursor = end
    out.append(expr[cursor:])
    return "".join(out)


def declared_var_names(expr: str) -> List[str]:
    """Upper-cased VAR names declared anywhere in *expr*."""
    code = code_tokens(expr)
    names: List[str] = []
    for i, t in enumerate(code):
        if t.type == TokenType.KEYWORD and t.value.upper() == "VAR" and i + 1 < len(code):
            names.append(code[i + 1].value.upper())
    return names


# ---------------------------------------------------------------------------
# Structural sanity gate (parse-before-write, statically)
# ---------------------------------------------------------------------------

_BINARY_ONLY_OPS = frozenset(
    {"*", "/", "^", "=", "==", "<>", "<", ">", "<=", ">=", "&&", "||", "&"}
)
_OPENERS = {
    TokenType.PAREN_OPEN: TokenType.PAREN_CLOSE,
    TokenType.BRACE_OPEN: TokenType.BRACE_CLOSE,
}


def _suspicious(code: Sequence[Token]) -> Dict[str, int]:
    counts = {"op_after_sep": 0, "sep_before_close": 0, "double_sep": 0, "unknown": 0}
    prev: Optional[Token] = None
    for t in code:
        if t.type == TokenType.UNKNOWN:
            counts["unknown"] += 1
        if prev is None or prev.type in (
            TokenType.PAREN_OPEN,
            TokenType.COMMA,
            TokenType.BRACE_OPEN,
        ):
            if t.type == TokenType.OPERATOR and t.value in _BINARY_ONLY_OPS:
                counts["op_after_sep"] += 1
        if prev is not None and prev.type == TokenType.COMMA:
            if t.type in (TokenType.PAREN_CLOSE, TokenType.BRACE_CLOSE):
                counts["sep_before_close"] += 1
            if t.type == TokenType.COMMA:
                counts["double_sep"] += 1
        prev = t
    return counts


def sanity_check(after: str, before: Optional[str] = None) -> Optional[str]:
    """Return None when *after* is structurally sound DAX, else the reason.

    Checks (on the re-lexed token stream): non-empty; every delimited token
    terminated; parens/braces balanced and correctly nested (a ``)`` swallowed
    by a ``// comment`` shows up here); no trailing operator/comma; and no NEW
    ``(*`` / ``, *`` / ``,)`` / ``,,`` / unknown-character patterns compared to
    *before* (some functions legitimately take empty arguments, so only
    patterns the rewrite introduced are refused).
    """
    lexer = get_lexer()
    full = lexer.tokenize(after or "")
    for t in full:
        if not t.terminated:
            return f"unterminated {t.type.name.lower()} at offset {t.start}"
    code = [
        t
        for t in full
        if t.type
        not in (
            TokenType.WHITESPACE,
            TokenType.NEWLINE,
            TokenType.COMMENT_LINE,
            TokenType.COMMENT_BLOCK,
        )
    ]
    if not code:
        return "rewrite is empty"
    stack: List[TokenType] = []
    for t in code:
        if t.type in _OPENERS:
            stack.append(_OPENERS[t.type])
        elif t.type in (TokenType.PAREN_CLOSE, TokenType.BRACE_CLOSE):
            if not stack or stack.pop() != t.type:
                return f"unbalanced '{t.value}' at offset {t.start}"
    if stack:
        return "unbalanced parentheses (a ')' may be commented out by a line comment)"
    last = code[-1]
    if last.type == TokenType.COMMA or (last.type == TokenType.OPERATOR):
        return f"rewrite ends with {last.value!r}"
    if last.type == TokenType.KEYWORD and last.value.upper() in ("RETURN", "VAR", "IN", "NOT"):
        return f"rewrite ends with keyword {last.value!r}"
    first = code[0]
    if first.type == TokenType.KEYWORD and first.value.upper() == "RETURN":
        return "rewrite starts with RETURN (no VAR declared)"
    new = _suspicious(code)
    old = _suspicious(code_tokens(before)) if before is not None else {k: 0 for k in new}
    for k, v in new.items():
        if v > old.get(k, 0):
            return f"rewrite introduced a malformed token sequence ({k})"
    return None


# ---------------------------------------------------------------------------
# Scope filter (E-22)
# ---------------------------------------------------------------------------

_REF_RE = re.compile(r"^\s*'?(?P<t>.*?)'?\s*\[(?P<m>.+)\]\s*$")


def _parse_scope_ref(ref: str) -> Tuple[Optional[str], str]:
    m = _REF_RE.match(ref or "")
    if m:
        return m.group("t").replace("''", "'") or None, m.group("m").replace("]]", "]")
    return None, (ref or "").strip()


def iter_scoped_measures(model: Any, args: Dict[str, Any]) -> Iterator[Tuple[Any, Any]]:
    """Yield ``(table, measure)`` for every model measure inside the caller's scope.

    ``args["measures"]``: list of ``"Table[Name]"`` or bare ``"Name"`` (case-
    insensitive); ``args["tables"]``: list of table names. Both absent -> every
    measure (the pre-v7.16 behaviour).
    """
    wanted_measures = args.get("measures") or []
    wanted_tables = {norm_name(t) for t in (args.get("tables") or [])}
    parsed = [_parse_scope_ref(r) for r in wanted_measures]
    for tbl in getattr(model, "tables", []) or []:
        tname = getattr(tbl, "name", "") or ""
        if wanted_tables and norm_name(tname) not in wanted_tables:
            continue
        for m in getattr(tbl, "measures", []) or []:
            mname = getattr(m, "name", "") or ""
            if parsed:
                hit = any(
                    norm_name(pm) == norm_name(mname)
                    and (pt is None or norm_name(pt) == norm_name(tname))
                    for pt, pm in parsed
                )
                if not hit:
                    continue
            yield tbl, m


SCOPE_SCHEMA = {
    "measures": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Only rewrite these measures ('Table[Name]' or 'Name').",
    },
    "tables": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Only rewrite measures hosted by these tables.",
    },
}


def measure_target(tbl: Any, m: Any) -> str:
    return f"measure:{getattr(tbl, 'name', '')}[{getattr(m, 'name', '')}]"


def all_measure_names(model: Any) -> Dict[str, str]:
    """``norm(name) -> host table`` for every measure in *model*."""
    out: Dict[str, str] = {}
    for tbl in getattr(model, "tables", []) or []:
        for m in getattr(tbl, "measures", []) or []:
            out[norm_name(getattr(m, "name", ""))] = getattr(tbl, "name", "")
    return out
